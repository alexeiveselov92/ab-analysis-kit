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
start_ts: 2024-07-01
horizon_ts: 2024-07-06
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

    def test_resync_cohort_flag_reaches_the_pipeline(self, project, warehouse):
        """m8 WP5 (§4 Q2): --resync-cohort full-reloads the persisted copy;
        never overloads --full-refresh."""
        yml = project / "experiments" / "signup_test.yml"
        yml.write_text(
            yml.read_text().replace(
                'query: "SELECT user_id, variant, exposure_ts FROM assignments"',
                'query: "SELECT user_id, variant, exposure_ts FROM assignments '
                'WHERE 1 = 1 {{ ab_added_filters }}"\n'
                "  cohort_copy: {enabled: true}",
            )
        )
        assert runner.invoke(cli, ["run", "--select", "signup_test"]).exit_code == 0
        assert len(warehouse._rows["_ab_exposures"]) == 300

        deletes: list[tuple] = []
        original = warehouse.delete_rows

        def spy(*args, **kwargs):
            deletes.append(args)
            return original(*args, **kwargs)

        warehouse.delete_rows = spy
        result = runner.invoke(cli, ["run", "--select", "signup_test", "--resync-cohort"])
        assert result.exit_code == 0, result.output
        assert any("_ab_exposures" in str(args[0]) for args in deletes)
        assert len(warehouse._rows["_ab_exposures"]) == 300

    def test_resync_cohort_in_direct_mode_is_accepted_and_noop(self, project, warehouse):
        result = runner.invoke(cli, ["run", "--select", "signup_test", "--resync-cohort"])
        assert result.exit_code == 0, result.output
        assert warehouse._rows.get("_ab_exposures", []) == []
        # the no-effect notice must reach the terminal, not just the log sink
        assert "no effect in direct mode" in result.output


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


SECOND_METRIC_YML = METRIC_YML.replace("name: arpu", "name: visits")
SECOND_EXPERIMENT_YML = EXPERIMENT_YML.replace("name: signup_test", "name: second_test").replace(
    "metric: arpu", "metric: visits"
)
#: signup_test with BOTH metrics — the only fixture shape that can prove the
#: flag reaches the pipeline. With one comparison per experiment, "only arpu was
#: written" is already true of the experiment-level selection narrowing, so the
#: assertion cannot fail (a review finding: deleting the CLI's `metric_filter=`
#: passthrough left the whole suite green).
TWO_COMPARISON_EXPERIMENT_YML = (
    EXPERIMENT_YML
    + """  - metric: visits
    method: {name: t-test, params: {test_type: relative}}
"""
)
#: a second experiment, DIRECT mode, declaring the same two comparisons — the
#: shape that proves the day-state disclosure is decided per experiment and not
#: once per run (a round-2 review finding: a run-level `any()` printed the
#: copy-mode sentence on behalf of direct-mode experiments).
DIRECT_TWIN_EXPERIMENT_YML = None  # set below
COPY_MODE_EXPERIMENT_YML = TWO_COMPARISON_EXPERIMENT_YML.replace(
    'query: "SELECT user_id, variant, exposure_ts FROM assignments"',
    'query: "SELECT user_id, variant, exposure_ts FROM assignments '
    'WHERE 1 = 1 {{ ab_added_filters }}"\n  cohort_copy: {enabled: true}',
)


DIRECT_TWIN_EXPERIMENT_YML = TWO_COMPARISON_EXPERIMENT_YML.replace(
    "name: signup_test", "name: direct_test"
)


class TestMetricOption:
    """m11 DASH-4a: `abk run --metric` — the selector semantics (step 5)."""

    @staticmethod
    def _add_second_experiment() -> None:
        (Path("metrics") / "visits.yml").write_text(SECOND_METRIC_YML)
        (Path("experiments") / "second_test.yml").write_text(SECOND_EXPERIMENT_YML)

    @staticmethod
    def _make_signup_test_two_comparison() -> None:
        (Path("metrics") / "visits.yml").write_text(SECOND_METRIC_YML)
        (Path("experiments") / "signup_test.yml").write_text(TWO_COMPARISON_EXPERIMENT_YML)

    def test_the_flag_reaches_the_pipeline_inside_one_experiment(self, project, warehouse):
        """The CLI→driver passthrough, on the only fixture that can fail: ONE
        experiment declaring TWO comparisons."""
        self._make_signup_test_two_comparison()
        result = runner.invoke(cli, ["run", "--metric", "arpu"])
        assert result.exit_code == 0, result.output
        assert {row["metric"] for row in warehouse._rows["_ab_results"]} == {"arpu"}

        # the sibling series arrives only when it is the one asked for
        assert runner.invoke(cli, ["run", "--metric", "visits"]).exit_code == 0
        by_metric: dict[str, int] = {}
        for row in warehouse._rows["_ab_results"]:
            by_metric[row["metric"]] = by_metric.get(row["metric"], 0) + 1
        assert by_metric == {"arpu": 5, "visits": 5}

    def test_matching_some_experiments_skips_the_others(self, project, warehouse):
        self._add_second_experiment()
        result = runner.invoke(cli, ["run", "--metric", "arpu"])
        assert result.exit_code == 0, result.output
        assert "second_test: no 'arpu' comparison — skipped by --metric" in result.output
        metrics = {row["metric"] for row in warehouse._rows["_ab_results"]}
        assert metrics == {"arpu"}
        assert {row["experiment"] for row in warehouse._rows["_ab_results"]} == {"signup_test"}

    def test_matching_nowhere_is_a_loud_error(self, project, warehouse):
        self._add_second_experiment()
        result = runner.invoke(cli, ["run", "--metric", "nope"])
        assert result.exit_code != 0
        assert "--metric 'nope' is not a comparison of any selected experiment" in result.output
        assert "arpu" in result.output and "visits" in result.output  # what IS declared
        assert warehouse._rows.get("_ab_results", []) == []

    def test_validate_only_rejects_the_filter(self, project):
        result = runner.invoke(cli, ["run", "--steps", "validate", "--metric", "arpu"])
        assert result.exit_code != 0
        assert "--metric" in result.output

    def test_the_notice_names_what_was_withheld(self, project, warehouse):
        self._make_signup_test_two_comparison()
        result = runner.invoke(cli, ["run", "--metric", "arpu"])
        assert result.exit_code == 0, result.output
        assert "--metric arpu: not recomputed this run: visits" in result.output
        assert "their results and day state stay exactly as they are" in result.output

    def test_a_single_comparison_experiment_withholds_nothing_and_says_nothing(
        self, project, warehouse
    ):
        """The generic wording described comparisons that do not exist."""
        result = runner.invoke(cli, ["run", "--metric", "arpu"])
        assert result.exit_code == 0, result.output
        assert "not recomputed this run" not in result.output

    def test_copy_mode_resync_says_the_cohort_is_not_per_metric(self, project, warehouse):
        (Path("metrics") / "visits.yml").write_text(SECOND_METRIC_YML)
        (Path("experiments") / "signup_test.yml").write_text(COPY_MODE_EXPERIMENT_YML)
        result = runner.invoke(cli, ["run", "--metric", "arpu", "--resync-cohort"])
        assert result.exit_code == 0, result.output
        assert "--resync-cohort rebuilds the whole cohort" in result.output
        assert "day state IS re-materialized for every eligible metric" in result.output
        # ...and the clause this run would contradict is not printed
        assert "day state stay exactly as they are" not in result.output
        # one homogeneous selection ⇒ no experiment names needed on the line
        assert "signup_test: --resync-cohort rebuilds" not in result.output

    def test_direct_mode_resync_claims_no_state_rebuild(self, project, warehouse):
        """The default mode: `--resync-cohort` is a no-op, so day state narrows
        with everything else and the copy-mode disclosure must NOT be printed."""
        self._make_signup_test_two_comparison()
        result = runner.invoke(cli, ["run", "--metric", "arpu", "--resync-cohort"])
        assert result.exit_code == 0, result.output
        assert "--resync-cohort rebuilds the whole cohort" not in result.output
        assert "their results and day state stay exactly as they are" in result.output
        assert "no effect in direct mode" in result.output  # the driver's own line

    def test_scoped_full_refresh_discloses_the_withheld_truncation(self, project, warehouse):
        self._make_signup_test_two_comparison()
        assert runner.invoke(cli, ["run"]).exit_code == 0
        result = runner.invoke(
            cli,
            [
                "run",
                "--metric",
                "arpu",
                "--full-refresh",
                "--from",
                "2024-07-02",
                "--to",
                "2024-07-04",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "truncated from the first day the refresh window touches" in result.output
        assert "not re-rendered" in result.output

    def test_a_mixed_mode_selection_discloses_each_experiment_separately(self, project, warehouse):
        """Round-2 finding: one copy-mode experiment in the selection must not
        make the run claim a whole-cohort day-state rebuild for the direct-mode
        ones — whose withheld series are TRUNCATED by the same command."""
        (Path("metrics") / "visits.yml").write_text(SECOND_METRIC_YML)
        (Path("experiments") / "signup_test.yml").write_text(COPY_MODE_EXPERIMENT_YML)
        (Path("experiments") / "direct_test.yml").write_text(DIRECT_TWIN_EXPERIMENT_YML)
        assert runner.invoke(cli, ["run"]).exit_code == 0

        result = runner.invoke(
            cli,
            [
                "run",
                "--metric",
                "arpu",
                "--resync-cohort",
                "--full-refresh",
                "--from",
                "2024-07-02",
                "--to",
                "2024-07-04",
            ],
        )
        assert result.exit_code == 0, result.output
        # both outcomes are stated, each naming the experiments it applies to
        assert "signup_test: --resync-cohort rebuilds the whole cohort" in result.output
        assert "direct_test: results stay as they are" in result.output
        assert "truncated from the first day the refresh window touches" in result.output

    def test_without_the_state_step_no_day_state_claim_is_made(self, project, warehouse):
        """The day-state sentences describe `materialize_state`, which a run that
        omits the step never calls."""
        self._make_signup_test_two_comparison()
        assert runner.invoke(cli, ["run"]).exit_code == 0
        result = runner.invoke(
            cli,
            [
                "run",
                "--metric",
                "arpu",
                "--steps",
                "plan,load,compute",
                "--full-refresh",
                "--from",
                "2024-07-02",
                "--to",
                "2024-07-04",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "the 'state' step is not selected" in result.output
        assert "truncated" not in result.output

    def test_the_option_is_documented_in_help(self):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--metric" in result.output
