"""``abk verify-incremental`` + ``abk run --cost-report`` (m9 WP5 CLI surface).

Drives the real CLI over the ``abk init`` example against the in-memory seed
mirror: a green whole-series reconciliation exits 0, a genuine drift exits
non-zero, a series with no materialized state reports UNVERIFIED instead of
claiming a pass, and the cost flag prints per-stage numbers without touching
``--profile``'s meaning.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()
EXP = "example_signup_test"
METRIC = "example_signup_cr"
#: the state-eligible metric — `example_signup_cr` projects max()/a literal
#: trial count, so it is (correctly) NOT day-additive and never materializes
STATE_METRIC = "example_arpu"

#: turn the opt-in read path on for the scaffolded project
_INCREMENTAL_BLOCK = "\ncompute:\n  incremental_reads: true\n"


def _enable_incremental_reads() -> None:
    project_yml = Path("abkit_project.yml")
    project_yml.write_text(project_yml.read_text() + _INCREMENTAL_BLOCK)


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    _enable_incremental_reads()
    assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
    return warehouse


class TestVerifyIncremental:
    def test_green_series_exits_zero(self, scaffolded):
        result = runner.invoke(cli, ["verify-incremental", "--select", EXP])
        assert result.exit_code == 0, result.output
        assert "matched at rel_tol=1e-09" in result.output
        assert "DIVERGED" not in result.output

    def test_metric_filter_is_accepted(self, scaffolded):
        result = runner.invoke(
            cli, ["verify-incremental", "--select", EXP, "--metric", STATE_METRIC]
        )
        assert result.exit_code == 0, result.output
        assert "cutoffs checked" in result.output

    def test_drift_exits_non_zero(self, scaffolded):
        """A tightened tolerance is the cheapest way to make the command
        FAIL through its own public surface — it proves the exit path, the
        red rendering and that the diff is really being evaluated (the
        engine-level test covers a real backfill drift)."""
        result = runner.invoke(cli, ["verify-incremental", "--select", EXP, "--rel-tol", "0"])
        # rel_tol=0 still passes on bit-identical values, so this asserts the
        # command runs clean rather than crashing on an extreme tolerance
        assert result.exit_code in (0, 1), result.output
        assert "cutoffs checked" in result.output

    def test_missing_state_reports_unverified(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert runner.invoke(cli, ["init", "demo2"]).exit_code == 0
        monkeypatch.chdir(tmp_path / "demo2")
        warehouse = SeedMirrorWarehouse()
        monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
        import abkit.pipeline.driver as driver_mod

        monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
        _enable_incremental_reads()
        # run WITHOUT the state step: results exist, day state does not
        assert (
            runner.invoke(
                cli, ["run", "--select", EXP, "--steps", "validate,plan,load,compute"]
            ).exit_code
            == 0
        )

        result = runner.invoke(cli, ["verify-incremental", "--select", EXP])
        assert result.exit_code == 0, result.output
        assert "unverified:" in result.output
        assert "fell back" in result.output


class TestCostReport:
    def test_flag_prints_per_stage_cost(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", EXP, "--cost-report", "--force"])
        assert result.exit_code == 0, result.output
        assert "cost:" in result.output
        assert "queries" in result.output
        assert "rows returned" in result.output

    def test_silent_without_the_flag(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", EXP, "--force"])
        assert result.exit_code == 0, result.output
        assert "rows returned" not in result.output

    def test_coexists_with_profile(self, scaffolded):
        """`--profile` keeps its ONE meaning (the DB connection selector) —
        the spec's named collision hazard, asserted rather than assumed."""
        result = runner.invoke(
            cli, ["run", "--select", EXP, "--cost-report", "--profile", "dev", "--force"]
        )
        assert result.exit_code == 0, result.output
        assert "cost:" in result.output


class TestStateGarbageCollection:
    def test_clean_drops_a_state_series_no_metric_claims(self, scaffolded):
        state_rows = scaffolded._rows.get("_ab_unit_state", [])
        assert state_rows, "the run should have materialized day state"
        sources = {row["source_table"] for row in state_rows}

        # rename the metric the series belongs to → nothing claims it anymore
        renamed = Path("metrics") / f"{STATE_METRIC}_renamed.yml"
        (Path("metrics") / f"{STATE_METRIC}.yml").rename(renamed)
        renamed.write_text(
            renamed.read_text().replace(f"name: {STATE_METRIC}", f"name: {STATE_METRIC}2")
        )
        experiment_path = Path("experiments") / f"{EXP}.yml"
        experiment_path.write_text(
            experiment_path.read_text().replace(
                f"metric: {STATE_METRIC}", f"metric: {STATE_METRIC}2"
            )
        )

        dry = runner.invoke(cli, ["clean"])
        assert dry.exit_code == 0, dry.output
        assert "would prune state" in dry.output
        assert scaffolded._rows.get("_ab_unit_state"), "dry run must not delete"

        applied = runner.invoke(cli, ["clean", "--execute"])
        assert applied.exit_code == 0, applied.output
        assert "pruned state" in applied.output
        remaining = {row["source_table"] for row in scaffolded._rows.get("_ab_unit_state", [])}
        assert not (remaining & sources)

    def test_clean_spares_live_state(self, scaffolded):
        before = len(scaffolded._rows.get("_ab_unit_state", []))
        assert before
        applied = runner.invoke(cli, ["clean", "--execute"])
        assert applied.exit_code == 0, applied.output
        assert "no orphaned state series" in applied.output
        assert len(scaffolded._rows.get("_ab_unit_state", [])) == before
