"""End-to-end pipeline tests on a synthetic in-memory warehouse.

The warehouse serves the metric SQL by actually aggregating a synthetic event
log within the window parsed from the RENDERED query — so cumulative windows,
the covariate pre-period render, and per-cutoff growth behave like a real
backend. Everything else (exposures, results, tasks) runs through the real
internal-tables mixins on the in-memory manager.

Pins the M2 DoD surface: real results end-to-end, the idempotent byte-stable
re-run (incl. bootstrap via derived seeds), the two-tier Bonferroni golden,
SRM broadcast (blocking-but-non-dropping), insufficient_data demotion, lock
behaviour, watermark planning, and full-refresh re-opening.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import pytest
from fake_db import FakeDatabaseManager

from abkit.config import ExperimentConfig, MetricConfig, ProjectConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.pipeline import PipelineStep, run_experiment, run_experiments
from abkit.pipeline.analyze import AnalyzeError, analyze_cutoff  # noqa: F401  (import check)

START = datetime(2024, 7, 1)

ARPU_SQL = (
    "{% import 'abkit_assignment.jinja' as ab %}\n"
    "SELECT {{ ab.variant_col() }} AS variant, user_id, sum(gross_usd) AS gross_usd "
    "FROM {{ data_database }}.user_revenue {{ ab.exposed_units() }} "
    "GROUP BY variant, user_id"
)

_WINDOW_RE = re.compile(r"event_time >= '([^']+)' AND event_time < '([^']+)'")


class SyntheticWarehouse(FakeDatabaseManager):
    """Aggregates a synthetic event log for the metric SQL; serves the
    assignment SQL from a cohort list; delegates ``_ab_*`` to the store."""

    def __init__(self):
        super().__init__()
        # (unit, variant, event_ts, value)
        self.events: list[tuple[str, str, datetime, float]] = []
        # (unit, variant, exposure_ts)
        self.cohort: list[tuple[str, str, datetime]] = []
        # flip to simulate a warehouse outage on fact-table queries
        self.fail_user_queries = False

    def execute_query(self, query: str, params: dict[str, Any] | None = None):
        flat = " ".join(query.split())
        if "user_revenue" in flat:
            if self.fail_user_queries:
                raise RuntimeError("synthetic warehouse outage")
            match = _WINDOW_RE.search(flat)
            assert match, f"metric SQL lost its window filter: {flat}"
            w_start = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            w_end = datetime.strptime(match.group(2), "%Y-%m-%d %H:%M:%S")
            exposure_filter = "exposure_ts" in flat.split("WHERE experiment")[-1]
            exposure_by_unit = {u: ts for u, _, ts in self.cohort}
            sums: dict[tuple[str, str], float] = {}
            for unit, variant, ts, value in self.events:
                if not (w_start <= ts < w_end):
                    continue
                if exposure_filter and (
                    unit not in exposure_by_unit or ts < exposure_by_unit[unit]
                ):
                    continue
                if unit not in exposure_by_unit:
                    continue  # the cohort join
                sums[(unit, variant)] = sums.get((unit, variant), 0.0) + value
            return [
                {"variant": variant, "user_id": unit, "gross_usd": total}
                for (unit, variant), total in sorted(sums.items())
            ]
        if "FROM assignments" in flat:
            return [{"user_id": u, "variant": v, "exposure_ts": ts} for u, v, ts in self.cohort]
        return super().execute_query(query, params)


def seed_cohort(warehouse: SyntheticWarehouse, n_per_arm: int = 150) -> None:
    for i in range(n_per_arm):
        warehouse.cohort.append((f"c{i}", "control", START + timedelta(hours=1)))
        warehouse.cohort.append((f"t{i}", "treatment", START + timedelta(hours=1)))


def seed_events(warehouse: SyntheticWarehouse, days: int = 5, lift: float = 1.2) -> None:
    """Deterministic per-unit daily revenue with a treatment lift."""
    for unit, variant, _ in warehouse.cohort:
        idx = int(unit[1:])
        for day in range(days):
            base = 1.0 + (idx % 7) * 0.5
            value = base * (lift if variant == "treatment" else 1.0)
            warehouse.events.append((unit, variant, START + timedelta(days=day, hours=12), value))


def make_experiment(**overrides) -> ExperimentConfig:
    payload = {
        "name": "signup_test",
        "start_date": "2024-07-01",
        "end_date": "2024-07-05",
        "unit_key": "user_id",
        "assignment": {
            "query": "SELECT user_id, variant, exposure_ts FROM assignments",
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
        },
        "comparisons": [
            {
                "metric": "arpu",
                "is_main_metric": True,
                "method": {"name": "t-test", "params": {"test_type": "relative"}},
            }
        ],
    }
    payload.update(overrides)
    return ExperimentConfig.model_validate(payload)


ARPU = MetricConfig.model_validate(
    {
        "name": "arpu",
        "type": "sample",
        "columns": {"variant": "variant", "value": "gross_usd"},
        "query": ARPU_SQL,
    }
)

PROJECT = ProjectConfig.model_validate({"name": "p", "default_profile": "dev"})
NOW = datetime(2024, 7, 20)  # past the horizon: every cutoff is complete


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_events(wh)
    return wh


@pytest.fixture
def tables(warehouse):
    return InternalTablesManager(warehouse)


def run(warehouse, tables, experiment=None, metrics=None, **kwargs):
    return run_experiment(
        experiment or make_experiment(),
        metrics or {"arpu": ARPU},
        PROJECT,
        warehouse,
        tables,
        now_utc=kwargs.pop("now_utc", NOW),
        **kwargs,
    )


class TestHappyPath:
    def test_real_results_end_to_end(self, warehouse, tables):
        outcome = run(warehouse, tables)
        assert outcome.status == "completed"
        assert outcome.exposures_loaded == 300
        assert outcome.srm_flagged is False
        assert outcome.cutoffs_planned == 5  # daily grid over 5 days
        assert outcome.results_written == 5

        rows = tables.load_results("signup_test")
        assert len(rows) == 5
        last = rows[-1]
        assert last["is_horizon"] is True
        assert last["ci_kind"] == "fixed"
        assert last["size_1"] == last["size_2"] == 150
        assert last["effect"] == pytest.approx(0.2, abs=0.02)  # the injected lift
        assert last["pvalue"] is not None and last["pvalue"] < 0.05
        assert last["method_config_id"]
        assert last["elapsed_days"] == pytest.approx(5.0)
        assert [r["elapsed_days"] for r in rows] == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_cumulative_values_grow(self, warehouse, tables):
        run(warehouse, tables)
        rows = tables.load_results("signup_test")
        values = [r["value_1"] for r in rows]
        assert values == sorted(values)
        assert values[-1] == pytest.approx(values[0] * 5)  # constant daily revenue


class TestIdempotency:
    def test_rerun_plans_zero_and_is_byte_stable(self, warehouse, tables):
        run(warehouse, tables)
        first = tables.load_results("signup_test")
        second_outcome = run(warehouse, tables)
        assert second_outcome.cutoffs_planned == 0
        assert second_outcome.results_written == 0
        second = tables.load_results("signup_test")

        def strip_version(rows):
            return [{k: v for k, v in r.items() if k != "created_at"} for r in rows]

        assert strip_version(first) == strip_version(second)

    def test_bootstrap_rerun_is_byte_stable_via_derived_seeds(self, warehouse, tables):
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {
                        "name": "bootstrap",
                        "params": {"test_type": "relative", "n_samples": 80},
                    },
                }
            ]
        )
        run(warehouse, tables, experiment=experiment)
        first = [
            (r["end_ts"], r["pvalue"], r["left_bound"], r["right_bound"])
            for r in tables.load_results("signup_test")
        ]
        tables.delete_results("signup_test")
        run(warehouse, tables, experiment=experiment)
        second = [
            (r["end_ts"], r["pvalue"], r["left_bound"], r["right_bound"])
            for r in tables.load_results("signup_test")
        ]
        assert first == second

    def test_middle_hole_is_healed(self, warehouse, tables):
        run(warehouse, tables)
        tables.delete_results(
            "signup_test",
            from_ts=datetime(2024, 7, 3),
            to_ts=datetime(2024, 7, 4),
        )
        outcome = run(warehouse, tables)
        assert outcome.cutoffs_planned == 1
        assert len(tables.load_results("signup_test")) == 5


class TestTwoTierBonferroni:
    def test_golden_two_tier_alphas(self, warehouse, tables):
        """3 variants, 1 main + 2 secondary: main α/C(3,2), secondary α/(C(3,2)·2)."""
        for i in range(150):
            warehouse.cohort.append((f"x{i}", "variant_b", START + timedelta(hours=1)))
        for unit, variant, _ in warehouse.cohort:
            if variant == "variant_b":
                idx = int(unit[1:])
                for day in range(5):
                    warehouse.events.append(
                        (unit, variant, START + timedelta(days=day, hours=12), 1.0 + idx % 3)
                    )
        experiment = make_experiment(
            assignment={
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "treatment", "variant_b"],
                "expected_split": {
                    "control": 1 / 3,
                    "treatment": 1 / 3,
                    "variant_b": 1 / 3,
                },
            },
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {"name": "t-test", "params": {"test_type": "relative"}},
                },
                {
                    "metric": "arpu2",
                    "method": {"name": "t-test", "params": {"test_type": "relative"}},
                },
                {
                    "metric": "arpu3",
                    "is_guardrail": True,
                    "method": {"name": "t-test", "params": {"test_type": "relative"}},
                },
            ],
        )
        metrics = {
            "arpu": ARPU,
            "arpu2": ARPU.model_copy(update={"name": "arpu2"}),
            "arpu3": ARPU.model_copy(update={"name": "arpu3"}),
        }
        run(warehouse, tables, experiment=experiment, metrics=metrics)
        rows = tables.load_results("signup_test")
        # C(3,2) = 3 pairs × 5 days × 3 metrics
        assert len(rows) == 45
        main_alpha = sorted({r["alpha"] for r in rows if r["metric"] == "arpu"})
        secondary_alpha = sorted({r["alpha"] for r in rows if r["metric"] != "arpu"})
        assert main_alpha == [pytest.approx(0.05 / 3)]
        # guardrails count as tests: 2 non-main metrics share the budget
        assert secondary_alpha == [pytest.approx(0.05 / (3 * 2))]


class TestSrmGate:
    def test_broadcast_blocking_but_non_dropping(self, warehouse, tables):
        warehouse.cohort = [c for c in warehouse.cohort if not c[0].startswith("t")][:150]
        for i in range(15):  # 150 vs 15 — a blatant SRM
            warehouse.cohort.append((f"t{i}", "treatment", START + timedelta(hours=1)))
        warehouse.events = []
        seed_events(warehouse)
        outcome = run(warehouse, tables)
        assert outcome.srm_flagged is True
        assert any("SRM FAILED" in w for w in outcome.warnings)
        rows = tables.load_results("signup_test")
        assert rows, "SRM must never drop rows"
        assert all(r["srm_flag"] for r in rows)
        assert all(r["decision_blocked"] for r in rows)
        assert all(r["srm_pvalue"] is not None for r in rows)


class TestTimezoneDates:
    def test_stored_dates_are_experiment_tz_dates(self, warehouse, tables):
        """Review finding: a Moscow daily experiment's end_date must be the
        Moscow calendar date, not the UTC date of the naive end_ts."""
        # re-anchor the synthetic events to Moscow midnights (21:00 UTC prev day)
        warehouse.events = [
            (unit, variant, ts - timedelta(hours=3), value)
            for unit, variant, ts, value in warehouse.events
        ]
        warehouse.cohort = [
            (unit, variant, ts - timedelta(hours=3)) for unit, variant, ts in warehouse.cohort
        ]
        experiment = make_experiment(timezone="Europe/Moscow")
        outcome = run(warehouse, tables, experiment=experiment)
        assert outcome.status == "completed", outcome.error
        rows = tables.load_results("signup_test")
        first = min(rows, key=lambda r: r["end_ts"])
        # first cutoff: end_ts = 2024-06-30 21:00 UTC (Moscow midnight July 2...
        # start_ts = 2024-06-30 21:00 UTC; first end_ts = 2024-07-01 21:00 UTC
        assert first["end_ts"] == datetime(2024, 7, 1, 21, 0)
        # ...whose MOSCOW date is July 1 (the UTC date would wrongly be July 1 21:00 -> July 1;
        # the window covers Moscow July 1 in full)
        assert str(first["start_date"]) == "2024-07-01"
        assert str(first["end_date"]) == "2024-07-01"
        last = max(rows, key=lambda r: r["end_ts"])
        assert str(last["end_date"]) == "2024-07-05"


class TestSrmZeroArm:
    def test_missing_arm_flags_srm_instead_of_crashing(self, warehouse, tables):
        """Review finding: a declared variant with ZERO exposures is the worst
        SRM there is — it must flag loudly, never crash the chi-square."""
        warehouse.cohort = [c for c in warehouse.cohort if c[1] == "control"]
        warehouse.events = []
        seed_events(warehouse)
        outcome = run(warehouse, tables)
        assert outcome.status == "completed", outcome.error
        assert outcome.srm_flagged is True
        rows = tables.load_results("signup_test")
        assert rows and all(r["srm_flag"] for r in rows)
        assert all(r["insufficient_data"] for r in rows)  # 0-unit arm demoted


class TestDemotion:
    def test_insufficient_data_rows_written_with_nulled_inference(self, warehouse, tables):
        warehouse.cohort = warehouse.cohort[:40]  # 20 per arm < min_units_per_arm=100
        warehouse.events = []
        seed_events(warehouse)
        outcome = run(warehouse, tables)
        assert outcome.status == "completed"
        rows = tables.load_results("signup_test")
        assert len(rows) == 5
        for row in rows:
            assert row["insufficient_data"] is True
            assert row["pvalue"] is None and row["effect"] is None
            assert row["reject"] is None
            assert row["size_1"] == 20  # counts stay visible
            assert row["warnings"] and "insufficient data" in row["warnings"]


class TestLockAndFailure:
    def test_locked_experiment_is_not_run(self, warehouse, tables):
        assert tables.acquire_lock("signup_test") is True
        outcome = run(warehouse, tables)
        assert outcome.status == "locked"
        assert outcome.results_written == 0

    def test_failure_records_error_and_releases_lock(self, warehouse, tables):
        bad_metric = ARPU.model_copy(update={"query": "SELECT {{ undefined_var }}"})
        outcome = run(warehouse, tables, metrics={"arpu": bad_metric})
        assert outcome.status == "failed"
        assert outcome.error and "undefined_var" in outcome.error
        # the lock row records the failure and is re-acquirable
        assert tables.check_lock("signup_test") is None
        assert tables.acquire_lock("signup_test") is True


class TestWatermark:
    def test_mid_experiment_plans_only_complete_days(self, warehouse, tables):
        outcome = run(warehouse, tables, now_utc=datetime(2024, 7, 3, 15, 30))
        assert outcome.cutoffs_planned == 2  # July 2 and July 3 midnights
        rows = tables.load_results("signup_test")
        assert max(r["end_ts"] for r in rows) == datetime(2024, 7, 3)

    def test_data_lag_shifts_the_watermark(self, warehouse, tables):
        experiment = make_experiment(data_lag="6h")
        outcome = run(warehouse, tables, experiment=experiment, now_utc=datetime(2024, 7, 3, 2, 0))
        # watermark = 02:00 − 6h = 20:00 July 2 → only July 2 complete
        assert outcome.cutoffs_planned == 1


class TestFullRefresh:
    def test_window_is_reopened(self, warehouse, tables):
        run(warehouse, tables)
        outcome = run(
            warehouse,
            tables,
            full_refresh_window=(datetime(2024, 7, 2), datetime(2024, 7, 4)),
        )
        assert outcome.cutoffs_planned == 2
        assert len(tables.load_results("signup_test")) == 5  # LWW, no duplicates


class TestStepsAndPool:
    def test_load_only_skips_compute(self, warehouse, tables):
        outcome = run(warehouse, tables, steps=[PipelineStep.LOAD])
        assert outcome.exposures_loaded == 300
        assert outcome.results_written == 0
        assert tables.load_results("signup_test") == []

    def test_worker_pool_runs_independent_experiments(self, warehouse, tables):
        exp2 = make_experiment(name="second_test")
        outcomes = run_experiments(
            [(None, make_experiment()), (None, exp2)],
            {"arpu": ARPU},
            PROJECT,
            manager_factory=lambda: warehouse,
            max_workers=2,
            now_utc=NOW,
        )
        assert {o.experiment for o in outcomes} == {"signup_test", "second_test"}
        assert all(o.status == "completed" for o in outcomes)


class TestCuped:
    def test_covariate_from_preperiod_render(self, warehouse, tables):
        # pre-period events: two weeks before start
        for unit, variant, _ in warehouse.cohort:
            idx = int(unit[1:])
            warehouse.events.append(
                (unit, variant, START - timedelta(days=3), 0.8 + (idx % 7) * 0.5)
            )
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {
                        "name": "cuped-t-test",
                        "params": {"test_type": "relative", "covariate_lookback": "14d"},
                    },
                }
            ]
        )
        outcome = run(warehouse, tables, experiment=experiment)
        assert outcome.status == "completed", outcome.error
        rows = tables.load_results("signup_test")
        assert rows[-1]["cov_value_1"] is not None  # covariate means recorded
        assert rows[-1]["effect"] is not None
        assert rows[-1]["method_name"] == "cuped-t-test"
        assert '"covariate_lookback":"14d"' in rows[-1]["method_params"]
