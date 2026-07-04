"""CLI tests: exit codes, the validate-only no-DB path, run/unlock/clean flows.

The DB is a SyntheticWarehouse injected through ProfileConfig.create_manager,
so the full `abk run` path — validation, alphas echo, pipeline, SRM line,
summary — runs against real files in a tmp project with zero drivers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli

START = datetime(2024, 7, 1)

PROJECT_YML = """
name: demo
default_profile: dev
"""

PROFILES_YML = """
default_profile: dev
profiles:
  dev:
    type: clickhouse
    port: 9000
    internal_database: abkit_internal
    data_database: analytics
"""

EXPERIMENT_YML = """
name: signup_test
start_date: 2024-07-01
end_date: 2024-07-05
unit_key: user_id
assignment:
  query: "SELECT user_id, variant, exposure_ts FROM assignments"
  variants: [control, treatment]
  expected_split: {control: 0.5, treatment: 0.5}
comparisons:
  - metric: arpu
    is_main_metric: true
    method: {name: t-test, params: {test_type: relative}}
"""

METRIC_YML = """
name: arpu
type: sample
columns:
  variant: variant
  value: gross_usd
query: |
  {% import 'abkit_assignment.jinja' as ab %}
  SELECT {{ ab.variant_col() }} AS variant, user_id, sum(gross_usd) AS gross_usd
  FROM {{ data_database }}.user_revenue {{ ab.exposed_units() }}
  GROUP BY variant, user_id
"""


def scaffold_project(root: Path) -> None:
    (root / "abkit_project.yml").write_text(PROJECT_YML)
    (root / "profiles.yml").write_text(PROFILES_YML)
    (root / "experiments").mkdir()
    (root / "metrics").mkdir()
    (root / "experiments" / "signup_test.yml").write_text(EXPERIMENT_YML)
    (root / "metrics" / "arpu.yml").write_text(METRIC_YML)


# reuse the synthetic warehouse from the pipeline tests
import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "pipeline"))
from test_pipeline import SyntheticWarehouse, seed_cohort, seed_events  # noqa: E402


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_events(wh)
    return wh


@pytest.fixture
def project(tmp_path, monkeypatch, warehouse):
    scaffold_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    # pipeline watermark: freeze "now" past the horizon so all cutoffs plan
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 7, 20))
    return tmp_path


runner = CliRunner()


class TestVersionAndHelp:
    def test_version(self):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "abk" in result.output

    def test_help_lists_m2_commands(self):
        result = runner.invoke(cli, ["--help"])
        for command in ("init", "run", "unlock", "clean"):
            assert command in result.output


class TestValidateOnly:
    def test_valid_project_no_db(self, tmp_path, monkeypatch):
        scaffold_project(tmp_path)
        (tmp_path / "profiles.yml").unlink()  # validate must not need profiles/DB
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["run", "--steps", "validate"])
        assert result.exit_code == 0, result.output
        assert "config valid" in result.output
        assert "Validation passed" in result.output

    def test_config_error_exits_nonzero(self, tmp_path, monkeypatch):
        scaffold_project(tmp_path)
        (tmp_path / "experiments" / "signup_test.yml").write_text(
            EXPERIMENT_YML.replace("metric: arpu", "metric: ghost")
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["run", "--steps", "validate"])
        assert result.exit_code != 0
        assert "ghost" in result.output

    def test_outside_project_exits_nonzero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["run", "--steps", "validate"])
        assert result.exit_code != 0
        assert "abkit_project.yml" in result.output

    def test_unknown_step_is_a_parameter_error(self, project):
        result = runner.invoke(cli, ["run", "--steps", "detect"])
        assert result.exit_code != 0
        assert "unknown step" in result.output


class TestRun:
    def test_full_run_writes_results_and_echoes_alphas(self, project, warehouse):
        result = runner.invoke(cli, ["run", "--select", "signup_test"])
        assert result.exit_code == 0, result.output
        assert "effective alphas" in result.output
        assert "main-metric alpha: 0.05" in result.output
        assert "results written: 5" in result.output
        assert "Done." in result.output
        assert len(warehouse._rows.get("_ab_results", [])) == 5

    def test_rerun_is_idempotent(self, project):
        assert runner.invoke(cli, ["run"]).exit_code == 0
        result = runner.invoke(cli, ["run"])
        assert result.exit_code == 0
        assert "cutoffs planned: 0" in result.output

    def test_srm_prints_the_red_gate_line(self, project, warehouse):
        warehouse.cohort = [c for c in warehouse.cohort if not c[0].startswith("t")][:150]
        for i in range(15):
            warehouse.cohort.append((f"t{i}", "treatment", START + timedelta(hours=1)))
        warehouse.events = []
        seed_events(warehouse)
        result = runner.invoke(cli, ["run"])
        assert result.exit_code == 0  # SRM blocks decisions, not the run
        assert "SRM FAILED" in result.output
        assert "effects untrustworthy" in result.output

    def test_failed_experiment_exits_nonzero(self, project, warehouse):
        warehouse.fail_user_queries = True  # a runtime outage, not a config error
        result = runner.invoke(cli, ["run"])
        assert result.exit_code == 1
        assert "✗" in result.output
        assert "synthetic warehouse outage" in result.output

    def test_full_refresh_requires_window(self, project):
        result = runner.invoke(cli, ["run", "--full-refresh"])
        assert result.exit_code != 0
        assert "--from" in result.output


class TestUnlock:
    def test_noop_and_clear(self, project, warehouse):
        result = runner.invoke(cli, ["unlock"])
        assert result.exit_code == 0
        assert "no active lock" in result.output

        from abkit.database.internal_tables import InternalTablesManager

        tables = InternalTablesManager(warehouse)
        tables.ensure_tables()
        tables.acquire_lock("signup_test")
        result = runner.invoke(cli, ["unlock", "--select", "signup_test"])
        assert result.exit_code == 0
        assert "lock cleared" in result.output


class TestClean:
    def test_dry_run_then_execute(self, project, warehouse):
        assert runner.invoke(cli, ["run"]).exit_code == 0
        # orphan the stored series by changing an identity param
        (Path("experiments") / "signup_test.yml").write_text(
            EXPERIMENT_YML.replace("test_type: relative", "test_type: absolute")
        )
        dry = runner.invoke(cli, ["clean", "--select", "signup_test"])
        assert dry.exit_code == 0
        assert "DRY RUN" in dry.output
        assert "would prune" in dry.output
        assert len(warehouse._rows["_ab_results"]) == 5  # untouched

        applied = runner.invoke(cli, ["clean", "--select", "signup_test", "--execute"])
        assert applied.exit_code == 0
        assert "pruned" in applied.output
        assert warehouse._rows["_ab_results"] == []

    def test_orphaned_experiments(self, project, warehouse):
        assert runner.invoke(cli, ["run"]).exit_code == 0
        (Path("experiments") / "signup_test.yml").unlink()
        # a project must keep >=1 experiment for validation — add another
        (Path("experiments") / "other.yml").write_text(
            EXPERIMENT_YML.replace("signup_test", "other_test")
        )
        result = runner.invoke(cli, ["clean", "--orphaned-experiments", "--execute", "--yes"])
        assert result.exit_code == 0, result.output
        assert "purged" in result.output
        assert warehouse._rows["_ab_results"] == []
