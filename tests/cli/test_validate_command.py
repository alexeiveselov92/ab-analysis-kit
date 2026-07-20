"""``abk validate`` — the WP4 CLI surface (m4-implementation-plan.md WP4).

Runs over the ``abk init`` example against the in-memory seed mirror (the M2/M3 e2e
harness): a run persists the cohort, then validate scores the matrix, writes
``_ab_aa_runs`` rows, honors the out-of-band lock, and emits the ``--report`` matrix.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from abkit.database.internal_tables import InternalTablesManager
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()
EXP = "example_signup_test"


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    # a run first persists the cohort/exposures validate loads from
    assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
    return warehouse


def _aa_rows(warehouse):
    return InternalTablesManager(warehouse).get_aa_runs(EXP)


def test_validate_writes_rows_and_exits_zero(scaffolded):
    result = runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "200"])
    assert result.exit_code == 0, result.output
    assert "effective alphas" in result.output
    rows = _aa_rows(scaffolded)
    assert rows, "validate wrote no _ab_aa_runs rows"
    # every row carries the matrix columns; at least one metric produced a real FPR
    assert all("run_id" in r and "verdict" in r for r in rows)
    assert any(r.get("fpr") is not None for r in rows)


def test_validate_report_writes_the_matrix_html(scaffolded):
    result = runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "150", "--report"])
    assert result.exit_code == 0, result.output
    out = Path("reports") / f"{EXP}__validate.html"  # the __validate suffix, never clobbers run
    assert out.is_file()
    html = out.read_text(encoding="utf-8")
    # WP5: the matrix reuses the committed report bundle (D10) — the section title
    # ships in report.js and the calibration block is baked into the payload.
    assert "__ABK_REPORT__" in html  # the report bundle is embedded
    assert "A/A false-positive matrix" in html  # the calibration section title (from report.js)
    assert '"matrix_rows"' in html  # the calibration block is populated
    # self-containment: the only http(s) is the inline SVG XML namespace (e2e precedent)
    stripped = html.replace("http://www.w3.org", "")
    assert "http://" not in stripped and "https://" not in stripped
    assert f"Report → reports/{EXP}__validate.html" in result.output


def test_validate_respects_the_out_of_band_lock(scaffolded):
    tables = InternalTablesManager(scaffolded)
    tables.ensure_tables()
    assert tables.acquire_lock(EXP, "pipeline", "validate")  # someone else holds it

    held = runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "100"])
    assert held.exit_code == 0  # a held lock is a noop, not a failure
    assert "lock held" in held.output
    assert not _aa_rows(scaffolded)  # nothing written while locked

    forced = runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "100", "--force"])
    assert forced.exit_code == 0, forced.output
    assert _aa_rows(scaffolded)  # --force took over and wrote rows


def test_unlock_clears_a_stale_validate_lock(scaffolded):
    tables = InternalTablesManager(scaffolded)
    tables.ensure_tables()
    tables.acquire_lock(EXP, "pipeline", "validate")

    result = runner.invoke(cli, ["unlock", "--select", EXP])
    assert result.exit_code == 0, result.output
    assert "validate lock cleared" in result.output
    # the lock is gone → validate runs without --force
    assert runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "100"]).exit_code == 0


def test_metric_narrows_scope(scaffolded):
    result = runner.invoke(
        cli, ["validate", "--select", EXP, "--metric", "example_arpu", "--iterations", "120"]
    )
    assert result.exit_code == 0, result.output
    metrics_written = {r["metric"] for r in _aa_rows(scaffolded)}
    assert metrics_written == {"example_arpu"}  # only the requested metric scored


def test_bad_selection_exits_nonzero(scaffolded):
    result = runner.invoke(cli, ["validate", "--select", "no_such_experiment"])
    assert result.exit_code != 0 or "Nothing selected" in result.output


def test_baseexception_releases_the_validate_lock(scaffolded, monkeypatch):
    """m4 exit-gate F5: a BaseException (Ctrl+C / SystemExit) mid-validation RELEASES the
    lock instead of stranding it 'running' for the 2h compute timeout, and re-propagates —
    so the next validate isn't silently no-op'd. (`except Exception` would have missed it.)"""
    import abkit.validate as validate_mod

    real = validate_mod.run_validation
    calls = {"n": 0}

    def interrupt_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SystemExit(2)  # a BaseException — the exact branch a Ctrl+C takes
        return real(*args, **kwargs)

    monkeypatch.setattr(validate_mod, "run_validation", interrupt_first)
    first = runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "50"])
    assert first.exit_code != 0  # the interrupt propagated, not swallowed as success
    assert not _aa_rows(scaffolded)  # nothing persisted on the interrupted run

    # the lock was RELEASED, not stranded: the next validate runs without --force
    second = runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "50"])
    assert second.exit_code == 0, second.output
    assert _aa_rows(scaffolded)  # it acquired the free lock and wrote rows


def test_manager_closed_even_when_acquire_lock_raises(scaffolded, monkeypatch):
    """m4 exit-gate review: a raise in acquire_lock (before the inner try) must still
    close the warehouse manager — the OUTER try/finally, never a leaked connection."""
    closed = {"n": 0}
    real_close = scaffolded.close

    def counting_close():
        closed["n"] += 1
        return real_close()

    monkeypatch.setattr(scaffolded, "close", counting_close)

    def boom(*args, **kwargs):
        raise RuntimeError("lock backend unreachable")

    monkeypatch.setattr(InternalTablesManager, "acquire_lock", boom)
    result = runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "20"])
    assert result.exit_code != 0  # the raise propagated (a real harness failure)
    assert closed["n"] >= 1  # …but the manager was closed in the finally — no leak


def test_validate_help_documents_the_wp6_policy():
    """m7 WP6: the help text names the auto-N-per-alpha default and the --family-sweep
    opt-in (with its behavior-change callout) — no silent policy flip."""
    result = runner.invoke(cli, ["validate", "--help"])
    assert result.exit_code == 0
    flat = " ".join(result.output.split())  # click wraps help text mid-phrase
    assert "ceil(200/alpha)" in flat
    assert "--family-sweep" in flat
    assert "before 0.2.0 it always ran" in flat


def test_validate_migration_notice_prints_and_family_flag_silences_it(scaffolded):
    """m7 WP6 review round 1: the one-release yellow migration notice is its own CLI
    code path (distinct from the runner's DecisionEntry) — pin its text on a bare
    multi-metric run, and its absence once --family-sweep is passed."""
    bare = runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "50"])
    assert bare.exit_code == 0, bare.output
    assert "no longer runs by default" in bare.output
    assert "--family-sweep" in bare.output

    opted = runner.invoke(
        cli, ["validate", "--select", EXP, "--iterations", "50", "--family-sweep"]
    )
    assert opted.exit_code == 0, opted.output
    assert "no longer runs by default" not in opted.output


def test_auto_n_warning_reaches_the_terminal(scaffolded, monkeypatch):
    """m7 WP6 review round 2: the §4.1 warn-uncapped entry must be ECHOED by the CLI —
    decision_log's only other consumer is the Auto-mode JSON reply, so without the
    echo the safeguard is invisible right when a tight alpha makes the run long.
    The formula itself is unit-tested; this pins the wiring, so the resolver is
    stubbed small to keep the run fast."""
    import abkit.validate.runner as runner_mod

    monkeypatch.setattr(runner_mod, "_default_iterations", lambda alpha, **kw: 60)
    monkeypatch.setattr(runner_mod, "AUTO_ITERATIONS_WARN_ABOVE", 1)
    result = runner.invoke(cli, ["validate", "--select", EXP])  # no -n → the auto path
    assert result.exit_code == 0, result.output
    assert "warning:" in result.output and "uncapped" in result.output
    assert "-n/--iterations to override" in result.output
