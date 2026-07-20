"""``abk explore`` — the WP8 CLI shell (m3-implementation-plan.md WP8).

The ported ``test_tune_command.py`` orchestration shape over the real
``abk init`` example + the seed-mirror warehouse: single-experiment guard,
the friendly never-run noop, the startup orphan warning, the ``--no-serve``
static snapshot, and the serve path with ``serve_explore`` monkeypatched
(cancel → unchanged; apply → the epilogue with the archive/orphan/re-run
lines; ``--no-open`` forwarded).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import abkit.config.profile as profile_mod
import abkit.tuning as tuning_mod
from abkit.cli.main import cli
from abkit.tuning.config_writer import AppliedConfig, OrphanedSeries
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()

EXP = "example_signup_test"


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    """`abk init demo` + the seed-mirror warehouse (the M2 e2e harness)."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init", "demo"])
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


@pytest.fixture
def computed(scaffolded):
    """The example experiment with persisted results (one real run)."""
    result = runner.invoke(cli, ["run", "--select", EXP])
    assert result.exit_code == 0, result.output
    return scaffolded


class FakeServe:
    """Captures serve_explore kwargs; returns a scripted result."""

    def __init__(self, result=None):
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class TestGuards:
    def test_never_run_project_is_a_friendly_noop(self, scaffolded):
        result = runner.invoke(cli, ["explore", "--select", EXP])
        assert result.exit_code == 0, result.output
        assert "no computed results yet" in result.output
        assert f"abk run --select {EXP}" in result.output

    def test_no_match_exits_nonzero_naming_the_namespace(self, computed):
        result = runner.invoke(cli, ["explore", "--select", "nope"])
        assert result.exit_code != 0
        assert "experiment namespace" in result.output

    def test_multi_experiment_selection_refused(self, computed):
        source = Path("experiments") / "example_signup_test.yml"
        clone = Path("experiments") / "second.yml"
        clone.write_text(
            source.read_text(encoding="utf-8").replace(
                "name: example_signup_test", "name: second_test"
            ),
            encoding="utf-8",
        )
        result = runner.invoke(cli, ["explore", "--select", "*"])
        assert result.exit_code != 0
        assert "ONE experiment" in result.output

    def test_unknown_metric_refused(self, computed):
        result = runner.invoke(cli, ["explore", "--select", EXP, "--metric", "nope"])
        assert result.exit_code != 0
        assert "not a configured comparison" in result.output

    def test_orphan_warning_printed_at_startup(self, computed, monkeypatch):
        serve = FakeServe(result=None)
        monkeypatch.setattr(tuning_mod, "serve_explore", serve)
        store = computed._rows["_ab_results"]
        stray = dict(store[0])
        stray["method_config_id"] = "an-orphaned-series-id"
        store.append(stray)
        result = runner.invoke(cli, ["explore", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        assert "orphaned method_config_id series" in result.output
        assert "abk clean" in result.output


class TestNoServe:
    def test_static_snapshot_written_and_selfcontained(self, computed):
        result = runner.invoke(cli, ["explore", "--select", EXP, "--no-serve"])
        assert result.exit_code == 0, result.output
        out = Path("reports") / f"{EXP}__explore.html"
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        assert "window.__ABK_EXPLORE__" in html
        assert 'id="abk-explore"' in html
        for placeholder in ("__PAYLOAD__", "__EXPLORE_JS__", "__EXPERIMENT__"):
            assert placeholder not in html
        # the static page carries NULL endpoints — the preview-badge substrate
        assert '"save_url":null' in html
        assert "Static explore page written" in result.output


class TestServePath:
    def test_cancel_leaves_the_experiment_unchanged(self, computed, monkeypatch):
        serve = FakeServe(result=None)
        monkeypatch.setattr(tuning_mod, "serve_explore", serve)
        before = (Path("experiments") / "example_signup_test.yml").read_bytes()
        result = runner.invoke(cli, ["explore", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        assert "cancelled" in result.output
        assert (Path("experiments") / "example_signup_test.yml").read_bytes() == before
        assert len(serve.calls) == 1

    def test_serve_receives_the_session_payload_and_paths(self, computed, monkeypatch):
        serve = FakeServe(result=None)
        monkeypatch.setattr(tuning_mod, "serve_explore", serve)
        result = runner.invoke(cli, ["explore", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        call = serve.calls[0]
        assert call["open_browser"] is False  # --no-open forwarded
        assert call["original_path"].name == "example_signup_test.yml"
        assert call["project_root"] == Path.cwd()
        payload = call["payload"]
        assert payload["experiment"] == EXP
        # the two configured comparisons ride as knob surfaces
        assert set(payload["explore"]["metrics"]) == {"example_signup_cr", "example_arpu"}
        assert payload["explore"]["default_metric"] == "example_signup_cr"  # the main metric
        assert call["session"] is not None and call["engine"] is not None
        assert call["metric_sql_by_name"].keys() == payload["explore"]["metrics"].keys()

    def test_metric_flag_narrows_the_opened_comparison(self, computed, monkeypatch):
        serve = FakeServe(result=None)
        monkeypatch.setattr(tuning_mod, "serve_explore", serve)
        result = runner.invoke(
            cli, ["explore", "--select", EXP, "--metric", "example_arpu", "--no-open"]
        )
        assert result.exit_code == 0, result.output
        assert serve.calls[0]["payload"]["explore"]["default_metric"] == "example_arpu"

    def test_apply_epilogue_with_the_orphan_hint(self, computed, monkeypatch, tmp_path):
        archived = Path("experiments") / ".history" / EXP / f"{EXP}-20260704T120000Z.yml"
        applied = AppliedConfig(
            experiment=EXP,
            saved=Path("experiments") / "example_signup_test.yml",
            archived=archived,
            updated=("example_signup_cr",),
            preserved=("example_arpu",),
            orphaned=(
                OrphanedSeries(
                    metric="example_signup_cr", old_id="old-id", new_id="new-id", rows=14
                ),
            ),
        )
        serve = FakeServe(result=applied)
        monkeypatch.setattr(tuning_mod, "serve_explore", serve)
        result = runner.invoke(cli, ["explore", "--select", EXP, "--no-open"])
        assert result.exit_code == 0, result.output
        assert "Archived previous config" in result.output
        assert "Updated comparison(s): example_signup_cr" in result.output
        assert "orphaned method_config_id series" in result.output
        assert "abk clean" in result.output
        assert f"abk run --select {EXP}" in result.output
        assert "Applied to" in result.output


def test_direct_mode_source_failure_exits_clean(computed, monkeypatch):
    """m8 WP4: a live source that emptied since the last run must fail the
    house way (ClickException → clean non-zero exit + actionable message),
    never a raw traceback — the factory validates at explore startup."""
    original = SeedMirrorWarehouse.execute_query

    def emptied(self, query, params=None):
        flat = " ".join(query.split())
        if "example_ab_assignments" in flat and "example_signup_events" not in flat:
            from fake_db import serve_assignment_pushdown

            return serve_assignment_pushdown(self._project, flat, [])
        return original(self, query, params)

    monkeypatch.setattr(SeedMirrorWarehouse, "execute_query", emptied)
    result = runner.invoke(cli, ["explore", "--select", EXP, "--no-serve"])
    assert result.exit_code != 0
    assert "cohort source failed validation" in result.output
    assert "returned no rows" in result.output
    assert "Traceback" not in result.output
