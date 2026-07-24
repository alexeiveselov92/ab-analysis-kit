"""STATE-stage tests (m9 WP3): per-(unit, day) materialization into ``_ab_unit_state``.

Covers the WP3 gates: the three metric shapes end-to-end, the twice-run
idempotency through the pipeline step, the metric-SQL-hash orphaning, the
``--full-refresh`` day re-materialization, both m8 cohort modes producing the
same per-unit moments (the §0.2 blocker regression), eligibility exclusions
(bootstrap-only / stratified / ``ab_cov_*``), the standalone ``--steps state``
surface, the non-finite truncation bailout, and the crash-safety of a
mid-refresh failure (truncate-then-advance heals on the next plain run).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pytest
from synthetic_ab import (
    METRICS,
    NOW,
    PROJECT,
    REVENUE,
    START,
    SyntheticWarehouse,
    experiment_payload,
    make_experiment,
    run_pipeline,
    seed_all_events,
    seed_cohort,
)

from abkit.config import ExperimentConfig, MetricConfig
from abkit.core.period_planner import generate_grid
from abkit.database.internal_tables import (
    InternalTablesManager,
    compute_metric_state_id,
)
from abkit.loaders.metric_loader import MetricLoadResult
from abkit.loaders.state_loader import StateMomentError, day_moments
from abkit.pipeline import PipelineStep, run_experiment
from abkit.pipeline.state import (
    closed_state_days,
    state_eligible_metrics,
    state_series_key,
)

T_TEST = {"name": "t-test", "params": {"test_type": "relative"}}

DAYS = [date(2024, 7, 1) + timedelta(days=offset) for offset in range(4)]


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_all_events(wh)
    return wh


@pytest.fixture
def tables(warehouse):
    return InternalTablesManager(warehouse)


def state_rows(warehouse) -> list[dict]:
    return warehouse._rows.get("_ab_unit_state", [])


def series_key(experiment: ExperimentConfig, metric: MetricConfig) -> tuple[str, str]:
    return state_series_key(experiment, metric, metric.get_query_text(None))


def event_total(warehouse, table: str, column: str) -> float:
    """Ground truth: every in-window event of the exposed cohort."""
    horizon = START + timedelta(days=4)
    exposure = {unit: ts for unit, _, ts in warehouse.cohort}
    return sum(
        values[column]
        for unit, _, ts, values in warehouse.events[table]
        if unit in exposure and exposure[unit] <= ts and START <= ts < horizon
    )


class TestStateMaterialization:
    def test_sample_metric_materializes_every_closed_day(self, warehouse, tables):
        experiment = make_experiment("exp_state", "arpu", T_TEST)
        outcome = run_pipeline(warehouse, tables, experiment)

        assert outcome.state_days_materialized == 4
        rows = state_rows(warehouse)
        source_id, series_id = series_key(experiment, REVENUE)
        assert {r["source_table"] for r in rows} == {source_id}
        assert {r["column_set_id"] for r in rows} == {series_id}
        assert {r["day"] for r in rows} == set(DAYS)
        # 240 units × 4 days, every unit active every day in the fixture
        assert len(rows) == 960

        moments = tables.sum_moments(source_id, series_id, DAYS[0], DAYS[-1])
        assert moments["n"] == pytest.approx(960)
        assert moments["sum_value"] == pytest.approx(
            event_total(warehouse, "user_revenue", "gross_usd"), rel=1e-9
        )
        # sample metrics leave the covariate/denominator moments at zero
        assert moments["sum_cov"] == 0.0
        assert moments["sum_denominator"] == 0.0

    def test_second_run_materializes_nothing_and_is_stable(self, warehouse, tables):
        experiment = make_experiment("exp_idem", "arpu", T_TEST)
        run_pipeline(warehouse, tables, experiment)
        source_id, series_id = series_key(experiment, REVENUE)
        first = tables.sum_moments(source_id, series_id, DAYS[0], DAYS[-1])
        first_count = len(state_rows(warehouse))

        outcome = run_pipeline(warehouse, tables, experiment)
        assert outcome.state_days_materialized == 0
        assert tables.sum_moments(source_id, series_id, DAYS[0], DAYS[-1]) == first
        assert len(state_rows(warehouse)) == first_count

    def test_fraction_metric_moment_shape(self, warehouse, tables):
        experiment = make_experiment(
            "exp_frac", "conversion", {"name": "z-test", "params": {"test_type": "relative"}}
        )
        run_pipeline(warehouse, tables, experiment)
        rows = state_rows(warehouse)
        # unit c000, day 0: trials = 2 + (0+0)%3 = 2, conversions = 0
        row = next(r for r in rows if r["unit_id"] == "c000" and r["day"] == DAYS[0])
        assert row["n"] == 2
        assert row["sum_value"] == 0.0
        assert row["sum_value_sq"] == 0.0
        assert row["sum_denominator"] == 0.0

        source_id, series_id = series_key(experiment, METRICS["conversion"])
        moments = tables.sum_moments(source_id, series_id, DAYS[0], DAYS[-1])
        assert moments["n"] == pytest.approx(
            event_total(warehouse, "user_conversions", "trials"), rel=1e-9
        )
        assert moments["sum_value"] == pytest.approx(
            event_total(warehouse, "user_conversions", "conversions"), rel=1e-9
        )

    def test_ratio_metric_moment_shape(self, warehouse, tables):
        experiment = make_experiment(
            "exp_ratio", "ctr", {"name": "ratio-delta", "params": {"test_type": "relative"}}
        )
        run_pipeline(warehouse, tables, experiment)
        rows = state_rows(warehouse)
        # unit c000, day 0: views = 5 + 0%4 = 5, clicks = (1 + 0%4) * 1 = 1
        row = next(r for r in rows if r["unit_id"] == "c000" and r["day"] == DAYS[0])
        assert row["n"] == 1
        assert row["sum_value"] == 1.0
        assert row["sum_value_sq"] == 1.0
        assert row["sum_denominator"] == 5.0
        assert row["sum_denominator_sq"] == 25.0
        assert row["sum_value_denominator"] == 5.0

        source_id, series_id = series_key(experiment, METRICS["ctr"])
        moments = tables.sum_moments(source_id, series_id, DAYS[0], DAYS[-1])
        assert moments["sum_denominator"] == pytest.approx(
            event_total(warehouse, "user_engagement", "views"), rel=1e-9
        )

    def test_timezone_days_snap_to_local_midnight(self, warehouse, tables):
        payload = experiment_payload("exp_msk", "arpu", T_TEST)
        payload["timezone"] = "Europe/Moscow"
        experiment = ExperimentConfig.model_validate(payload)
        run_pipeline(warehouse, tables, experiment)

        rows = state_rows(warehouse)
        assert {r["day"] for r in rows} == set(DAYS)
        grid = generate_grid(
            experiment.start_date,
            experiment.end_date,
            experiment.cadence_segments(),
            tz=experiment.timezone,
        )
        days = closed_state_days(experiment, grid, NOW)
        # Moscow midnight = 21:00 UTC of the previous calendar day
        assert days[0].window.start_ts == datetime(2024, 6, 30, 21, 0)
        assert days[0].window.end_ts == datetime(2024, 7, 1, 21, 0)
        assert days[-1].window.end_ts == grid.horizon_ts


class TestStateIdentity:
    def test_metric_sql_edit_orphans_the_old_series(self, warehouse, tables):
        experiment = make_experiment("exp_edit", "arpu", T_TEST)
        run_pipeline(warehouse, tables, experiment)
        source_id, old_series = series_key(experiment, REVENUE)

        edited = REVENUE.model_copy(update={"query": REVENUE.query + " -- edited body"})
        _, new_series = state_series_key(experiment, edited, edited.query)
        assert new_series != old_series

        run_pipeline(warehouse, tables, experiment, metrics={"arpu": edited})
        rows = [r for r in state_rows(warehouse) if r["source_table"] == source_id]
        assert {r["column_set_id"] for r in rows} == {new_series}
        assert len(rows) == 960  # fully re-materialized under the new identity

    def test_whitespace_only_edit_keeps_the_identity(self):
        reformatted = REVENUE.query.replace(" ", "  \n ")
        assert compute_metric_state_id(
            REVENUE.columns.role_map(), REVENUE.query
        ) == compute_metric_state_id(REVENUE.columns.role_map(), reformatted)

    def test_end_date_extension_keeps_an_end_invariant_series(self):
        """R2 fix: extending an experiment (the most routine edit) must not
        orphan state — unless the assignment SQL actually windows on the end."""
        base = make_experiment("exp_extend", "arpu", T_TEST)
        payload = experiment_payload("exp_extend", "arpu", T_TEST)
        payload["end_date"] = "2024-07-14"
        extended = ExperimentConfig.model_validate(payload)
        assert series_key(base, REVENUE) == series_key(extended, REVENUE)

        windowed = experiment_payload("exp_extend", "arpu", T_TEST)
        windowed["assignment"]["query"] = (
            "SELECT user_id, variant, exposure_ts FROM assignments "
            "WHERE event_date <= '{{ ab_end_date }}'"
        )
        w_base = ExperimentConfig.model_validate(windowed)
        windowed["end_date"] = "2024-07-14"
        w_extended = ExperimentConfig.model_validate(windowed)
        # an end-windowed cohort SQL renders differently after the extension
        assert series_key(w_base, REVENUE) != series_key(w_extended, REVENUE)

    def test_cohort_config_edit_orphans_the_old_series(self, warehouse, tables):
        """R1 fix: a filter edit reshapes the cohort — the series must not mix."""
        experiment = make_experiment("exp_filters", "arpu", T_TEST)
        run_pipeline(warehouse, tables, experiment)
        _, old_series = series_key(experiment, REVENUE)

        payload = experiment_payload("exp_filters", "arpu", T_TEST)
        payload["assignment"]["added_filters"] = "AND user_id != 'bot'"
        edited = ExperimentConfig.model_validate(payload)
        _, new_series = series_key(edited, REVENUE)
        assert new_series != old_series

        run_pipeline(warehouse, tables, edited)
        assert {r["column_set_id"] for r in state_rows(warehouse)} == {new_series}


class TestFullRefresh:
    def test_full_refresh_rematerializes_the_windowed_days(self, warehouse, tables):
        experiment = make_experiment("exp_refresh", "arpu", T_TEST)
        run_pipeline(warehouse, tables, experiment)
        source_id, series_id = series_key(experiment, REVENUE)

        def c000_day(day: date) -> float:
            return sum(
                r["sum_value"]
                for r in state_rows(warehouse)
                if r["unit_id"] == "c000" and r["day"] == day
            )

        before = c000_day(DAYS[1])
        warehouse.events["user_revenue"].append(
            ("c000", "control", datetime(2024, 7, 2, 13, 0), {"gross_usd": 10.0})
        )

        # a plain rerun trusts the materialized day — the backfill is invisible
        run_pipeline(warehouse, tables, experiment)
        assert c000_day(DAYS[1]) == pytest.approx(before)

        outcome = run_experiment(
            experiment,
            METRICS,
            PROJECT,
            warehouse,
            tables,
            now_utc=NOW,
            full_refresh_window=(datetime(2024, 7, 2), datetime(2024, 7, 3)),
        )
        assert outcome.status == "completed"
        assert c000_day(DAYS[1]) == pytest.approx(before + 10.0)
        # truncate-then-advance: the touched day AND the tail re-render
        assert outcome.state_days_materialized == 3
        moments = tables.sum_moments(source_id, series_id, DAYS[3], DAYS[3])
        assert moments["n"] == pytest.approx(240)
        # the day before the refresh window is retained, not re-rendered
        assert c000_day(DAYS[0]) > 0.0

    def test_crash_mid_refresh_leaves_no_stale_day_and_self_heals(
        self, warehouse, tables, monkeypatch
    ):
        """R1 fix: a transient failure mid-refresh must never leave a
        freshly-covered ``get_last_state_day`` over silently stale rows."""
        from abkit.compute.recompute_backend import RecomputeBackend

        experiment = make_experiment("exp_crash", "arpu", T_TEST)
        run_pipeline(warehouse, tables, experiment)
        source_id, series_id = series_key(experiment, REVENUE)
        warehouse.events["user_revenue"].append(
            ("c000", "control", datetime(2024, 7, 3, 13, 0), {"gross_usd": 20.0})
        )

        real_load_window = RecomputeBackend.load_window

        def flaky(self, metric, metric_sql, window):
            if window.start_ts == datetime(2024, 7, 3):
                raise RuntimeError("transient warehouse timeout")
            return real_load_window(self, metric, metric_sql, window)

        monkeypatch.setattr(RecomputeBackend, "load_window", flaky)
        failed = run_experiment(
            experiment,
            METRICS,
            PROJECT,
            warehouse,
            tables,
            now_utc=NOW,
            full_refresh_window=(datetime(2024, 7, 2), datetime(2024, 7, 4)),
        )
        assert failed.status == "failed"
        # day 3 was truncated up front — absent, not stale; day 2 re-rendered
        days_present = {r["day"] for r in state_rows(warehouse)}
        assert days_present == {DAYS[0], DAYS[1]}
        assert tables.get_last_state_day(source_id, series_id) == DAYS[1]

        # the next plain run resumes from the contiguous prefix and heals
        monkeypatch.setattr(RecomputeBackend, "load_window", real_load_window)
        healed = run_experiment(experiment, METRICS, PROJECT, warehouse, tables, now_utc=NOW)
        assert healed.status == "completed"
        assert healed.state_days_materialized == 2
        assert {r["day"] for r in state_rows(warehouse)} == set(DAYS)
        day3 = sum(
            r["sum_value"]
            for r in state_rows(warehouse)
            if r["unit_id"] == "c000" and r["day"] == DAYS[2]
        )
        assert day3 == pytest.approx(
            sum(
                values["gross_usd"]
                for unit, _, ts, values in warehouse.events["user_revenue"]
                if unit == "c000" and DAYS[2] == ts.date() and ts >= START
            )
        )


class TestCohortModeParity:
    """The §0.2 blocker regression: both m8 modes yield identical moments."""

    @staticmethod
    def _run_mode(copy_enabled: bool) -> list[dict]:
        warehouse = SyntheticWarehouse()
        seed_cohort(warehouse)
        seed_all_events(warehouse)
        tables = InternalTablesManager(warehouse)
        payload = experiment_payload("exp_modes", "arpu", T_TEST)
        if copy_enabled:
            payload["assignment"]["query"] = (
                "SELECT user_id, variant, exposure_ts FROM assignments "
                "WHERE 1 = 1 {{ ab_added_filters }}"
            )
            payload["assignment"]["cohort_copy"] = {"enabled": True}
        experiment = ExperimentConfig.model_validate(payload)
        run_pipeline(warehouse, tables, experiment)
        # column_set_id legitimately differs across modes (the copy-mode
        # fixture's assignment SQL carries the required ab_added_filters
        # hook, and the assignment SQL text is part of the series identity);
        # the parity claim is about the MOMENTS.
        stripped = [
            {k: v for k, v in row.items() if k not in ("version", "column_set_id")}
            for row in state_rows(warehouse)
        ]
        return sorted(stripped, key=lambda r: (r["unit_id"], str(r["day"])))

    def test_direct_and_copy_mode_state_is_identical(self):
        direct = self._run_mode(copy_enabled=False)
        copy = self._run_mode(copy_enabled=True)
        assert len(direct) == 960
        assert direct == copy

    @staticmethod
    def _copy_experiment(name: str, **cohort_copy) -> ExperimentConfig:
        payload = experiment_payload(name, "arpu", T_TEST)
        payload["assignment"]["query"] = (
            "SELECT user_id, variant, exposure_ts FROM assignments "
            "WHERE 1 = 1 {{ ab_added_filters }}"
        )
        payload["assignment"]["cohort_copy"] = {"enabled": True, **cohort_copy}
        return ExperimentConfig.model_validate(payload)

    def test_copy_coverage_clamps_state_days(self, warehouse, tables):
        """An immature copy must not freeze partial-cohort day state."""
        experiment = self._copy_experiment("exp_immature", maturity_delay="30d")
        outcome = run_experiment(experiment, METRICS, PROJECT, warehouse, tables, now_utc=NOW)
        assert outcome.status == "completed"
        # nothing matured into the copy yet — no day may materialize from it
        assert outcome.state_days_materialized == 0
        assert state_rows(warehouse) == []

    def test_resync_cohort_rebuilds_day_state(self, warehouse, tables):
        experiment = self._copy_experiment("exp_resync")
        run_pipeline(warehouse, tables, experiment)
        source_id, series_id = series_key(experiment, REVENUE)
        first_versions = {
            (r["unit_id"], str(r["day"])): r["version"] for r in state_rows(warehouse)
        }
        assert len(first_versions) == 960

        outcome = run_experiment(
            experiment, METRICS, PROJECT, warehouse, tables, now_utc=NOW, resync_cohort=True
        )
        assert outcome.status == "completed"
        assert outcome.state_days_materialized == 4  # dropped and fully re-rendered
        second_versions = {
            (r["unit_id"], str(r["day"])): r["version"] for r in state_rows(warehouse)
        }
        assert set(second_versions) == set(first_versions)
        assert all(second_versions[k] > first_versions[k] for k in first_versions)
        moments = tables.sum_moments(source_id, series_id, DAYS[0], DAYS[-1])
        assert moments["n"] == pytest.approx(960)


class TestEligibility:
    def test_bootstrap_only_metric_writes_no_state(self, warehouse, tables):
        experiment = make_experiment(
            "exp_boot", "arpu", {"name": "bootstrap", "params": {"n_samples": 50}}
        )
        outcome = run_pipeline(warehouse, tables, experiment)
        assert outcome.state_days_materialized == 0
        assert state_rows(warehouse) == []

    def test_stratified_metric_is_excluded(self):
        stratified = MetricConfig.model_validate(
            {
                "name": "arpu",
                "type": "sample",
                "columns": {"variant": "variant", "value": "gross_usd", "stratum": "country"},
                "query": REVENUE.query,
            }
        )
        experiment = make_experiment("exp_strat", "arpu", T_TEST)
        assert state_eligible_metrics(experiment, {"arpu": stratified}, None) == []

    def test_non_additive_role_projections_are_excluded(self):
        """A metric whose role columns are not additive across days must never
        materialize state: the reader SUMS days, so ``max(...)`` and a literal
        trial count inflate. This is the project's OWN scaffolded fraction
        metric (`example_signup_cr`), caught by `abk verify-incremental`."""
        scaffolded_shape = MetricConfig.model_validate(
            {
                "name": "signup_cr",
                "type": "fraction",
                "columns": {"variant": "variant", "count": "signed_up", "nobs": "visits"},
                "query": (
                    "{% import 'abkit_assignment.jinja' as ab %}\n"
                    "SELECT {{ ab.variant_col() }} AS variant, user_id, "
                    "max(signed_up) AS signed_up, 1 AS visits "
                    "FROM {{ data_database }}.events {{ ab.exposed_units() }} "
                    "GROUP BY variant, user_id"
                ),
            }
        )
        experiment = make_experiment("exp_nonadd", "signup_cr", {"name": "z-test"})
        assert state_eligible_metrics(experiment, {"signup_cr": scaffolded_shape}, None) == []

    @pytest.mark.parametrize(
        "projection,eligible",
        [
            ("sum(gross_usd) AS gross_usd", True),
            ("sumIf(gross_usd, ok) AS gross_usd", True),
            ("sum(if(x > 0, gross_usd, 0)) AS gross_usd", True),
            ("max(gross_usd) AS gross_usd", False),
            ("avg(gross_usd) AS gross_usd", False),
            ("uniq(gross_usd) AS gross_usd", False),
            ("any(gross_usd) AS gross_usd", False),
            ("1 AS gross_usd", False),
        ],
    )
    def test_additive_aggregate_allowlist(self, projection, eligible):
        metric = MetricConfig.model_validate(
            {
                "name": "arpu",
                "type": "sample",
                "columns": {"variant": "variant", "value": "gross_usd"},
                "query": (
                    "{% import 'abkit_assignment.jinja' as ab %}\n"
                    f"SELECT {{{{ ab.variant_col() }}}} AS variant, user_id, {projection} "
                    "FROM {{ data_database }}.t {{ ab.exposed_units() }} "
                    "GROUP BY variant, user_id"
                ),
            }
        )
        experiment = make_experiment("exp_allow", "arpu", T_TEST)
        chosen = state_eligible_metrics(experiment, {"arpu": metric}, None)
        assert bool(chosen) is eligible

    def test_explicit_covariate_metric_is_excluded(self):
        """R2 fix: a snapshot covariate is not day-additive — no state."""
        with_covariate = MetricConfig.model_validate(
            {
                "name": "arpu",
                "type": "sample",
                "columns": {"variant": "variant", "value": "gross_usd", "covariate": "prev"},
                "query": REVENUE.query,
            }
        )
        experiment = make_experiment("exp_snap", "arpu", T_TEST)
        assert state_eligible_metrics(experiment, {"arpu": with_covariate}, None) == []

    def test_cov_window_dependent_sql_is_excluded(self):
        cov_windowed = MetricConfig.model_validate(
            {
                "name": "arpu",
                "type": "sample",
                "columns": {"variant": "variant", "value": "gross_usd"},
                "query": REVENUE.query + " -- uses {{ ab_cov_start }}",
            }
        )
        experiment = make_experiment("exp_cov", "arpu", T_TEST)
        assert state_eligible_metrics(experiment, {"arpu": cov_windowed}, None) == []

    def test_mixed_methods_materialize_only_the_closed_form_metric(self, warehouse, tables):
        arpu2 = REVENUE.model_copy(update={"name": "arpu2"})
        payload = experiment_payload("exp_multi", "arpu", T_TEST)
        payload["comparisons"].append(
            {"metric": "arpu2", "method": {"name": "bootstrap", "params": {"n_samples": 50}}}
        )
        experiment = ExperimentConfig.model_validate(payload)
        metrics = dict(METRICS) | {"arpu2": arpu2}
        chosen = state_eligible_metrics(experiment, metrics, None)
        assert [metric.name for metric, _ in chosen] == ["arpu"]

        run_pipeline(warehouse, tables, experiment, metrics=metrics)
        source_arpu, _ = series_key(experiment, REVENUE)
        sources = {r["source_table"] for r in state_rows(warehouse)}
        assert sources == {source_arpu}  # the bootstrap metric never pays the write


class TestStepsSurface:
    def test_steps_state_standalone_writes_state_only(self, warehouse, tables):
        experiment = make_experiment("exp_steps", "arpu", T_TEST)
        outcome = run_experiment(
            experiment,
            METRICS,
            PROJECT,
            warehouse,
            tables,
            steps=[PipelineStep.STATE],
            now_utc=NOW,
        )
        assert outcome.status == "completed"
        assert outcome.state_days_materialized == 4
        assert outcome.results_written == 0
        assert tables.load_results("exp_steps") == []

    def test_steps_without_state_skips_the_stage(self, warehouse, tables):
        experiment = make_experiment("exp_nostate", "arpu", T_TEST)
        outcome = run_experiment(
            experiment,
            METRICS,
            PROJECT,
            warehouse,
            tables,
            steps=[PipelineStep.LOAD, PipelineStep.COMPUTE],
            now_utc=NOW,
        )
        assert outcome.status == "completed"
        assert outcome.state_days_materialized == 0
        assert state_rows(warehouse) == []
        assert outcome.results_written > 0

    def test_parse_accepts_state(self):
        assert PipelineStep.parse("load,state") == [PipelineStep.LOAD, PipelineStep.STATE]
        with pytest.raises(ValueError, match="state"):
            PipelineStep.parse("bogus")


class TestNonFiniteBailout:
    def test_nan_moment_truncates_from_the_failing_day(self, warehouse, tables):
        warehouse.events["user_revenue"].append(
            ("c000", "control", datetime(2024, 7, 3, 13, 0), {"gross_usd": float("nan")})
        )
        experiment = make_experiment("exp_nan", "arpu", T_TEST)
        outcome = run_experiment(
            experiment,
            METRICS,
            PROJECT,
            warehouse,
            tables,
            steps=[PipelineStep.STATE],
            now_utc=NOW,
        )
        assert outcome.status == "completed"
        assert any("day state truncated at 2024-07-03" in w for w in outcome.warnings)
        # R1 fix: the days BEFORE the failure are retained (no full-history
        # re-render every run); the failing day and everything after are absent
        assert outcome.state_days_materialized == 2
        source_id, series_id = series_key(experiment, REVENUE)
        days_present = {r["day"] for r in state_rows(warehouse)}
        assert days_present == {DAYS[0], DAYS[1]}
        assert tables.get_last_state_day(source_id, series_id) == DAYS[1]

        # the retry next run costs one render, keeps the prefix, and stays put
        second = run_experiment(
            experiment,
            METRICS,
            PROJECT,
            warehouse,
            tables,
            steps=[PipelineStep.STATE],
            now_utc=NOW,
        )
        assert second.state_days_materialized == 0
        assert any("day state truncated" in w for w in second.warnings)
        assert {r["day"] for r in state_rows(warehouse)} == {DAYS[0], DAYS[1]}

    def test_non_integer_nobs_raises(self):
        loaded = MetricLoadResult(
            metric="conversion",
            units_by_variant={"control": np.array(["u1"], dtype=object)},
            roles_by_variant={
                "control": {
                    "count": np.array([1.0]),
                    "nobs": np.array([2.5]),
                }
            },
            strata_by_variant={"control": None},
        )
        with pytest.raises(StateMomentError, match="non-integer 'nobs'"):
            day_moments(METRICS["conversion"], loaded)


class TestClosedDays:
    def test_watermark_clamps_the_closed_days(self):
        experiment = make_experiment("exp_clamp", "arpu", T_TEST)
        grid = generate_grid(
            experiment.start_date,
            experiment.end_date,
            experiment.cadence_segments(),
            tz=experiment.timezone,
        )
        # mid-day watermark: day 3 is chronologically open for state purposes
        days = closed_state_days(experiment, grid, datetime(2024, 7, 3, 12, 0))
        assert [sd.day for sd in days] == DAYS[:2]
        assert days[0].window.start_ts == grid.start_ts
        # past-horizon watermark covers every day, clamped to the horizon
        days = closed_state_days(experiment, grid, NOW)
        assert [sd.day for sd in days] == DAYS
        assert days[-1].window.end_ts == grid.horizon_ts

    def test_before_first_close_yields_nothing(self):
        experiment = make_experiment("exp_none", "arpu", T_TEST)
        grid = generate_grid(
            experiment.start_date,
            experiment.end_date,
            experiment.cadence_segments(),
            tz=experiment.timezone,
        )
        assert closed_state_days(experiment, grid, datetime(2024, 7, 1, 23, 0)) == []
