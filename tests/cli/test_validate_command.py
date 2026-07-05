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
