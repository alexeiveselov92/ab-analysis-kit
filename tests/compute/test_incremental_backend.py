"""IncrementalBackend tests (m9-implementation-plan.md WP4).

Covers the WP4 gates: load parity vs ``RecomputeBackend`` across the three
metric kinds + CUPED, daily AND sub-day cadence (the §6.4 closed-days + tail
split), single- and multi-arm; the gap-detection fallback (never a silent
undercount — the §0.2 safety net); bootstrap/stratified comparisons never
routing incremental even with the flag on; the flag-off/flag-on persisted-row
identity (the milestone's central №1 assertion); twice-run idempotence; and
the tail render under both m8 cohort modes (the blocker regression).

Float posture (m9 §0.1): unit arrays and integer counts are exact; continuous
values assert at rel-1e-9 (the incremental path sums per-day partials in a
different order than the full-window scan — never byte-asserted).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pytest
from synthetic_ab import (
    CONVERSION,
    CTR,
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

from abkit.compute.incremental_backend import IncrementalBackend
from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config import ExperimentConfig, ProjectConfig
from abkit.core.period_planner import generate_grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.pipeline import run_experiment

T_TEST = {"name": "t-test", "params": {"test_type": "relative"}}
CUPED = {"name": "cuped-t-test", "params": {"covariate_lookback": "7d"}}
DAYS = [date(2024, 7, 1) + timedelta(days=offset) for offset in range(4)]

#: ProjectConfig with the m9 WP4 opt-in on
PROJECT_INCREMENTAL = ProjectConfig.model_validate(
    {"name": "p", "default_profile": "dev", "compute": {"incremental_reads": True}}
)


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_all_events(wh)
    return wh


@pytest.fixture
def tables(warehouse):
    return InternalTablesManager(warehouse)


def _grid(experiment: ExperimentConfig):
    return generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        tz=experiment.timezone,
    )


def _snapshot_variant_map(warehouse) -> dict[str, str]:
    return {unit: variant for unit, variant, _ in warehouse.cohort}


def _incremental(warehouse, tables, experiment, warnings=None) -> IncrementalBackend:
    return IncrementalBackend(
        tables,
        RecomputeBackend(warehouse, experiment),
        experiment,
        variant_map_loader=lambda: _snapshot_variant_map(warehouse),
        on_warning=(warnings.append if warnings is not None else None),
    )


def _assert_load_parity(incremental_load, recompute_load, what=""):
    """Units/shape exact; continuous role values at the §0.1 tolerance."""
    assert incremental_load.units_by_variant.keys() == recompute_load.units_by_variant.keys(), what
    for variant in recompute_load.units_by_variant:
        np.testing.assert_array_equal(
            incremental_load.units_by_variant[variant],
            recompute_load.units_by_variant[variant],
            err_msg=f"{what}: unit arrays diverged for '{variant}'",
        )
        assert (
            incremental_load.roles_by_variant[variant].keys()
            == recompute_load.roles_by_variant[variant].keys()
        ), what
        for role, expected in recompute_load.roles_by_variant[variant].items():
            np.testing.assert_allclose(
                incremental_load.roles_by_variant[variant][role],
                expected,
                rtol=1e-9,
                atol=1e-12,
                err_msg=f"{what}: role '{role}' diverged for '{variant}'",
            )
        assert incremental_load.strata_by_variant.get(variant) is None


class TestLoadParity:
    """WP4 gate: incremental output within rel-1e-9 of recompute, same shape."""

    CELLS = [
        (REVENUE, T_TEST),
        (REVENUE, CUPED),
        (CONVERSION, {"name": "z-test", "params": {"test_type": "relative"}}),
        (CTR, {"name": "ratio-delta", "params": {"test_type": "relative"}}),
    ]

    @pytest.mark.parametrize("metric,method", CELLS, ids=lambda c: getattr(c, "name", None) or c["name"])
    def test_daily_cadence_parity(self, warehouse, tables, metric, method):
        experiment = make_experiment("exp_par", metric.name, method)
        run_pipeline(warehouse, tables, experiment)  # materializes the state days

        grid = _grid(experiment)
        comparison = experiment.comparisons[0]
        sql = metric.get_query_text(None)
        recompute = RecomputeBackend(warehouse, experiment)
        warnings: list[str] = []
        incremental = _incremental(warehouse, tables, experiment, warnings)
        for cutoff in grid.cutoffs:
            _assert_load_parity(
                incremental.load_cutoff(comparison, metric, sql, grid, cutoff),
                recompute.load_cutoff(comparison, metric, sql, grid, cutoff),
                what=f"{metric.name}@{cutoff.end_ts}",
            )
        assert warnings == []  # every cutoff served from state, no fallback

    def test_cuped_covariate_attaches_identically(self, warehouse, tables):
        experiment = make_experiment("exp_cov", "arpu", CUPED)
        run_pipeline(warehouse, tables, experiment)
        grid = _grid(experiment)
        comparison = experiment.comparisons[0]
        sql = REVENUE.get_query_text(None)
        loaded = _incremental(warehouse, tables, experiment).load_cutoff(
            comparison, REVENUE, sql, grid, grid.cutoffs[-1]
        )
        expected = RecomputeBackend(warehouse, experiment).load_cutoff(
            comparison, REVENUE, sql, grid, grid.cutoffs[-1]
        )
        for variant in expected.variants():
            np.testing.assert_allclose(
                loaded.roles_by_variant[variant]["covariate"],
                expected.roles_by_variant[variant]["covariate"],
                rtol=1e-9,
                atol=1e-12,
            )

    def test_subday_cadence_reads_closed_days_plus_tail(self, warehouse, tables):
        """§6.4: an 18h grid mixes empty-tail, active-tail and midnight looks."""
        payload = experiment_payload("exp_subday", "arpu", T_TEST)
        payload["cadence"] = "18h"
        payload["data_lag"] = "1h"
        experiment = ExperimentConfig.model_validate(payload)
        run_pipeline(warehouse, tables, experiment)

        grid = _grid(experiment)
        comparison = experiment.comparisons[0]
        sql = REVENUE.get_query_text(None)
        recompute = RecomputeBackend(warehouse, experiment)
        warnings: list[str] = []
        incremental = _incremental(warehouse, tables, experiment, warnings)
        assert any(c.end_ts.hour not in (0,) for c in grid.cutoffs)  # real sub-day looks
        for cutoff in grid.cutoffs:
            _assert_load_parity(
                incremental.load_cutoff(comparison, REVENUE, sql, grid, cutoff),
                recompute.load_cutoff(comparison, REVENUE, sql, grid, cutoff),
                what=f"subday@{cutoff.end_ts}",
            )
        assert warnings == []

    def test_multi_arm_split(self):
        """State rows are arm-agnostic; the read-time split covers 3 arms."""
        warehouse = SyntheticWarehouse()
        for i in range(60):
            warehouse.cohort.append((f"a{i:03d}", "control", START + timedelta(hours=1)))
            warehouse.cohort.append((f"b{i:03d}", "treat_a", START + timedelta(hours=1)))
            warehouse.cohort.append((f"c{i:03d}", "treat_b", START + timedelta(hours=1)))
        seed_all_events(warehouse)
        tables = InternalTablesManager(warehouse)
        payload = experiment_payload("exp_3arm", "arpu", T_TEST)
        payload["assignment"]["variants"] = ["control", "treat_a", "treat_b"]
        payload["assignment"]["expected_split"] = {
            "control": 1 / 3,
            "treat_a": 1 / 3,
            "treat_b": 1 / 3,
        }
        experiment = ExperimentConfig.model_validate(payload)
        run_pipeline(warehouse, tables, experiment)

        grid = _grid(experiment)
        comparison = experiment.comparisons[0]
        sql = REVENUE.get_query_text(None)
        loaded = _incremental(warehouse, tables, experiment).load_cutoff(
            comparison, REVENUE, sql, grid, grid.cutoffs[-1]
        )
        expected = RecomputeBackend(warehouse, experiment).load_cutoff(
            comparison, REVENUE, sql, grid, grid.cutoffs[-1]
        )
        assert set(loaded.variants()) == {"control", "treat_a", "treat_b"}
        _assert_load_parity(loaded, expected, what="3-arm")


class TestGapFallback:
    """The §0.2 safety net: a state gap can never become a silent undercount."""

    def test_no_state_at_all_falls_back(self, warehouse, tables):
        experiment = make_experiment("exp_nostate", "arpu", T_TEST)
        # LOAD only — STATE never runs, the series does not exist
        tables.ensure_tables()
        grid = _grid(experiment)
        comparison = experiment.comparisons[0]
        sql = REVENUE.get_query_text(None)
        warnings: list[str] = []
        incremental = _incremental(warehouse, tables, experiment, warnings)
        loaded = incremental.load_cutoff(comparison, REVENUE, sql, grid, grid.cutoffs[-1])
        expected = RecomputeBackend(warehouse, experiment).load_cutoff(
            comparison, REVENUE, sql, grid, grid.cutoffs[-1]
        )
        _assert_load_parity(loaded, expected, what="no-state fallback")
        assert len(warnings) == 1
        assert "fell back to full recompute" in warnings[0]

    def test_truncated_series_falls_back_not_undercounts(self, warehouse, tables):
        """Delete a mid-series day THROUGH the shipped truncation primitive
        (the only way the system itself creates a hole — every failure path
        truncates the tail, WP3): the reader must detect the gap and serve
        recompute numbers, never the partial prefix sum."""
        experiment = make_experiment("exp_hole", "arpu", T_TEST)
        run_pipeline(warehouse, tables, experiment)
        from abkit.pipeline.state import state_series_key

        source_id, series_id = state_series_key(experiment, REVENUE, REVENUE.get_query_text(None))
        tables.delete_state_days_from(source_id, series_id, DAYS[2])  # keep days 0-1

        grid = _grid(experiment)
        comparison = experiment.comparisons[0]
        sql = REVENUE.get_query_text(None)
        warnings: list[str] = []
        incremental = _incremental(warehouse, tables, experiment, warnings)
        recompute = RecomputeBackend(warehouse, experiment)

        # the last cutoff needs closed days through day 3 — must fall back
        last = grid.cutoffs[-1]
        _assert_load_parity(
            incremental.load_cutoff(comparison, REVENUE, sql, grid, last),
            recompute.load_cutoff(comparison, REVENUE, sql, grid, last),
            what="gap fallback",
        )
        assert warnings and "fell back to full recompute" in warnings[0]
        # sanity: the fallback is NOT a partial sum — day-3 revenue is in there
        control = incremental.load_cutoff(comparison, REVENUE, sql, grid, last)
        prefix_only = tables.per_unit_cumulative(source_id, series_id, DAYS[0], DAYS[1])
        one_unit = str(next(iter(prefix_only)))
        variant = _snapshot_variant_map(warehouse)[one_unit]
        units = list(control.units_by_variant[variant])
        full_value = control.roles_by_variant[variant]["value"][units.index(one_unit)]
        assert full_value > prefix_only[one_unit]["sum_value"]  # 4 days > 2 days

    def test_cutoff_within_covered_days_still_serves_from_state(self, warehouse, tables):
        """A truncated tail only affects cutoffs NEEDING the missing days."""
        experiment = make_experiment("exp_partial", "arpu", T_TEST)
        run_pipeline(warehouse, tables, experiment)
        from abkit.pipeline.state import state_series_key

        source_id, series_id = state_series_key(experiment, REVENUE, REVENUE.get_query_text(None))
        tables.delete_state_days_from(source_id, series_id, DAYS[2])

        grid = _grid(experiment)
        comparison = experiment.comparisons[0]
        sql = REVENUE.get_query_text(None)
        warnings: list[str] = []
        incremental = _incremental(warehouse, tables, experiment, warnings)
        # cutoff at day-2 midnight needs closed days 0-1 only — still covered
        early = grid.cutoffs[1]
        assert early.end_ts == datetime(2024, 7, 3)
        expected = RecomputeBackend(warehouse, experiment).load_cutoff(
            comparison, REVENUE, sql, grid, early
        )
        _assert_load_parity(
            incremental.load_cutoff(comparison, REVENUE, sql, grid, early),
            expected,
            what="covered prefix",
        )
        assert warnings == []


def _results_comparable(rows: list[dict]) -> list[dict]:
    """The cross-mode parity pattern: strip volatile columns, sort."""
    volatile = {"created_at", "metric_rendered_query"}
    stripped = [{k: v for k, v in r.items() if k not in volatile} for r in rows]
    return sorted(stripped, key=lambda r: (str(r["end_ts"]), str(r["name_1"]), str(r["name_2"])))


class RecordingWarehouse(SyntheticWarehouse):
    def __init__(self) -> None:
        super().__init__()
        self.executed: list[str] = []

    def execute_query(self, query, params=None):
        self.executed.append(" ".join(query.split()))
        return super().execute_query(query, params)


def _seeded_recording() -> tuple[RecordingWarehouse, InternalTablesManager]:
    warehouse = RecordingWarehouse()
    seed_cohort(warehouse)
    seed_all_events(warehouse)
    return warehouse, InternalTablesManager(warehouse)


def _cumulative_fact_scans(warehouse: RecordingWarehouse, table: str) -> list[str]:
    """Fact-table scans over a MULTI-day cumulative window (not day renders)."""
    scans = []
    for q in warehouse.executed:
        if table not in q or "_ab_unit_state" in q:
            continue
        window = [s for s in q.split("'") if s.startswith("2024-")]
        if len(window) >= 4 and window[2] == "2024-07-01 00:00:00":
            start = datetime.strptime(window[2], "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(window[3], "%Y-%m-%d %H:%M:%S")
            if end - start > timedelta(days=1):
                scans.append(q)
    return scans


class TestDriverRouting:
    """The opt-in resolver: who reads state, who never does."""

    def test_flag_on_serves_computes_from_state(self):
        warehouse, tables = _seeded_recording()
        experiment = make_experiment("exp_flagon", "arpu", T_TEST)
        outcome = run_experiment(
            experiment, METRICS, PROJECT_INCREMENTAL, warehouse, tables, now_utc=NOW
        )
        assert outcome.status == "completed", outcome.error
        assert not outcome.warnings  # no fallback: STATE ran in the same run
        # state was read per cutoff...
        assert any("GROUP BY unit_id" in q and "_ab_unit_state" in q for q in warehouse.executed)
        # ...and no multi-day cumulative fact scan remains (day renders + the
        # sequential-anchor probe stay single-day)
        assert _cumulative_fact_scans(warehouse, "user_revenue") == []

    def test_flag_off_never_touches_state_reads(self):
        warehouse, tables = _seeded_recording()
        experiment = make_experiment("exp_flagoff", "arpu", T_TEST)
        outcome = run_experiment(experiment, METRICS, PROJECT, warehouse, tables, now_utc=NOW)
        assert outcome.status == "completed", outcome.error
        assert not any(
            "GROUP BY unit_id" in q and "_ab_unit_state" in q for q in warehouse.executed
        )

    def test_flag_on_off_persist_matching_rows(self):
        """The milestone's №1 assertion (§0.1 posture): the flag changes HOW,
        never WHAT — identity/integer columns exact, continuous values at
        rel-1e-9 (the state read sums per-day partials in a different float
        order than the full-window scan; byte-equality across the two read
        paths is unachievable in principle, the M7 GEMM lesson — `==` on a
        float is exactly what §0.1 forbids)."""
        warehouse_on, tables_on = _seeded_recording()
        experiment = make_experiment("exp_identity", "arpu", T_TEST)
        run_experiment(experiment, METRICS, PROJECT_INCREMENTAL, warehouse_on, tables_on, now_utc=NOW)
        warehouse_off, tables_off = _seeded_recording()
        run_experiment(experiment, METRICS, PROJECT, warehouse_off, tables_off, now_utc=NOW)
        rows_on = _results_comparable(tables_on.load_results("exp_identity"))
        rows_off = _results_comparable(tables_off.load_results("exp_identity"))
        assert rows_on  # non-vacuous
        assert len(rows_on) == len(rows_off)
        for row_on, row_off in zip(rows_on, rows_off, strict=True):
            assert row_on.keys() == row_off.keys()
            for key, expected in row_off.items():
                actual = row_on[key]
                if isinstance(expected, float) and isinstance(actual, float):
                    assert actual == pytest.approx(expected, rel=1e-9, abs=1e-12), (
                        f"{row_off['end_ts']}/{key}: {actual!r} != {expected!r}"
                    )
                else:
                    assert actual == expected, f"{row_off['end_ts']}/{key}"

    def test_twice_run_is_idempotent_with_flag_on(self):
        warehouse, tables = _seeded_recording()
        experiment = make_experiment("exp_twice", "arpu", T_TEST)
        run_experiment(experiment, METRICS, PROJECT_INCREMENTAL, warehouse, tables, now_utc=NOW)
        first = _results_comparable(tables.load_results("exp_twice"))
        second_outcome = run_experiment(
            experiment, METRICS, PROJECT_INCREMENTAL, warehouse, tables, now_utc=NOW
        )
        assert second_outcome.results_written == 0
        assert _results_comparable(tables.load_results("exp_twice")) == first

    def test_bootstrap_comparison_never_routes_incremental(self):
        """Explicit assertion, not an omission (spec): seeded methods stay on
        the full recompute path even with the flag on."""
        warehouse, tables = _seeded_recording()
        experiment = make_experiment(
            "exp_boot", "arpu", {"name": "bootstrap", "params": {"n_samples": 50}}
        )
        outcome = run_experiment(
            experiment, METRICS, PROJECT_INCREMENTAL, warehouse, tables, now_utc=NOW
        )
        assert outcome.status == "completed", outcome.error
        # bootstrap-only metric: no state is written OR read...
        assert not any("_ab_unit_state" in q and "GROUP BY unit_id" in q for q in warehouse.executed)
        # ...and the cumulative fact scans are all there (the recompute path)
        assert len(_cumulative_fact_scans(warehouse, "user_revenue")) >= 3

    def test_stratified_metric_never_routes_incremental(self):
        warehouse, tables = _seeded_recording()
        stratified = REVENUE.model_copy(deep=True)
        stratified.columns.stratum = "variant"  # any present column works
        metrics = dict(METRICS, arpu=stratified)
        experiment = make_experiment("exp_strat", "arpu", T_TEST)
        outcome = run_experiment(
            experiment, metrics, PROJECT_INCREMENTAL, warehouse, tables, now_utc=NOW
        )
        assert outcome.status == "completed", outcome.error
        assert not any(
            "_ab_unit_state" in q and "GROUP BY unit_id" in q for q in warehouse.executed
        )

    def test_full_refresh_without_state_step_disables_incremental(self):
        """--full-refresh re-plans results but only the STATE step re-renders
        day state: skipping it leaves state IN-PLACE STALE (not absent), which
        the gap check cannot see — the driver must force recompute + warn."""
        from abkit.pipeline import PipelineStep

        warehouse, tables = _seeded_recording()
        experiment = make_experiment("exp_fr", "arpu", T_TEST)
        run_experiment(experiment, METRICS, PROJECT_INCREMENTAL, warehouse, tables, now_utc=NOW)
        warehouse.executed.clear()
        outcome = run_experiment(
            experiment,
            METRICS,
            PROJECT_INCREMENTAL,
            warehouse,
            tables,
            now_utc=NOW,
            steps=[PipelineStep.LOAD, PipelineStep.COMPUTE],
            full_refresh_window=(datetime(2024, 7, 1), datetime(2024, 7, 5)),
        )
        assert outcome.status == "completed", outcome.error
        assert any("incremental reads disabled" in w for w in outcome.warnings)
        assert not any(
            "GROUP BY unit_id" in q and "_ab_unit_state" in q for q in warehouse.executed
        )
        # the re-planned window recomputes over multi-day fact scans again
        assert len(_cumulative_fact_scans(warehouse, "user_revenue")) >= 2

    def test_experiment_override_beats_project_default(self):
        warehouse, tables = _seeded_recording()
        payload = experiment_payload("exp_override", "arpu", T_TEST)
        payload["incremental_reads"] = True
        experiment = ExperimentConfig.model_validate(payload)
        outcome = run_experiment(experiment, METRICS, PROJECT, warehouse, tables, now_utc=NOW)
        assert outcome.status == "completed", outcome.error
        assert any("GROUP BY unit_id" in q and "_ab_unit_state" in q for q in warehouse.executed)

        warehouse2, tables2 = _seeded_recording()
        payload2 = experiment_payload("exp_override_off", "arpu", T_TEST)
        payload2["incremental_reads"] = False
        experiment2 = ExperimentConfig.model_validate(payload2)
        run_experiment(experiment2, METRICS, PROJECT_INCREMENTAL, warehouse2, tables2, now_utc=NOW)
        assert not any(
            "GROUP BY unit_id" in q and "_ab_unit_state" in q for q in warehouse2.executed
        )


class TestCohortModes:
    """The §0.2 blocker regression: the tail scan under both m8 modes."""

    @staticmethod
    def _run_mode(copy_enabled: bool) -> list[dict]:
        warehouse = SyntheticWarehouse()
        seed_cohort(warehouse)
        seed_all_events(warehouse)
        tables = InternalTablesManager(warehouse)
        payload = experiment_payload("exp_modes", "arpu", T_TEST)
        payload["cadence"] = "18h"  # sub-day: the tail scan is exercised
        payload["data_lag"] = "1h"
        payload["incremental_reads"] = True
        if copy_enabled:
            payload["assignment"]["query"] = (
                "SELECT user_id, variant, exposure_ts FROM assignments "
                "WHERE 1 = 1 {{ ab_added_filters }}"
            )
            payload["assignment"]["cohort_copy"] = {"enabled": True}
        experiment = ExperimentConfig.model_validate(payload)
        tables_manager = tables
        outcome = run_experiment(
            experiment, METRICS, PROJECT, warehouse, tables_manager, now_utc=NOW
        )
        assert outcome.status == "completed", outcome.error
        return _results_comparable(tables_manager.load_results("exp_modes"))

    def test_results_identical_across_modes(self):
        direct = self._run_mode(copy_enabled=False)
        copy = self._run_mode(copy_enabled=True)
        assert direct  # non-vacuous
        assert direct == copy


class TestPerUnitCumulative:
    """The new `_unit_state` reader against both dedup regimes."""

    @pytest.mark.parametrize("clickhouse_like", [False, True], ids=["sql-like", "clickhouse-like"])
    def test_sums_dedup_and_bound(self, clickhouse_like):
        from fake_db import FakeDatabaseManager

        manager = FakeDatabaseManager(clickhouse_like=clickhouse_like)
        tables = InternalTablesManager(manager)
        tables.ensure_tables()
        for day_offset in range(3):
            tables.replace_day_state(
                "exp/m",
                "abc123",
                date(2024, 7, 1) + timedelta(days=day_offset),
                {
                    "unit_id": np.array(["u1", "u2"], dtype=object),
                    "n": np.array([1, 1], dtype=np.int64),
                    "sum_value": np.array([1.5, 2.0]),
                },
            )
        # a second replace of day 1 must not double-count (replace-not-sum)
        tables.replace_day_state(
            "exp/m",
            "abc123",
            date(2024, 7, 2),
            {
                "unit_id": np.array(["u1", "u2"], dtype=object),
                "n": np.array([1, 1], dtype=np.int64),
                "sum_value": np.array([10.0, 20.0]),
            },
        )
        per_unit = tables.per_unit_cumulative("exp/m", "abc123", date(2024, 7, 1), date(2024, 7, 2))
        assert set(per_unit) == {"u1", "u2"}
        assert per_unit["u1"]["sum_value"] == pytest.approx(1.5 + 10.0)
        assert per_unit["u2"]["sum_value"] == pytest.approx(2.0 + 20.0)
        assert per_unit["u1"]["n"] == pytest.approx(2)
        # day 3 is outside the bound
        assert per_unit["u1"]["sum_denominator"] == 0.0
