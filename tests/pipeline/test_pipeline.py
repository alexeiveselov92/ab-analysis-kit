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
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fake_db import FakeDatabaseManager, serve_assignment_pushdown

from abkit.config import ExperimentConfig, MetricConfig, ProjectConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.pipeline import PipelineStep, run_experiment, run_experiments
from abkit.pipeline.analyze import AnalyzeError, analyze_cutoff  # noqa: F401  (import check)
from abkit.pipeline.driver import _sequential_mode_changed
from abkit.stats.registry import get_method_class

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
            raw = [{"user_id": u, "variant": v, "exposure_ts": ts} for u, v, ts in self.cohort]
            return serve_assignment_pushdown(self._project, flat, raw)
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
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-06",
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


class TestSequentialActivation:
    """M5 WP3: sequential.enabled emits always-valid rows on a plain run."""

    def test_enabled_emits_always_valid_rows(self, warehouse, tables):
        experiment = make_experiment(sequential={"enabled": True})
        outcome = run(warehouse, tables, experiment=experiment)
        assert outcome.status == "completed"
        rows = tables.load_results("signup_test")
        assert len(rows) == 5
        assert all(r["ci_kind"] == "always_valid" for r in rows)

    def test_always_valid_ci_is_wider_than_fixed_same_point(self, warehouse, tables):
        # fixed (default) baseline
        run(warehouse, tables)
        fixed_h = [r for r in tables.load_results("signup_test") if r["is_horizon"]][0]
        # a fresh warehouse+tables for the sequential run over identical data
        wh2 = SyntheticWarehouse()
        seed_cohort(wh2)
        seed_events(wh2)
        tables2 = InternalTablesManager(wh2)
        run(wh2, tables2, experiment=make_experiment(sequential={"enabled": True}))
        seq_h = [r for r in tables2.load_results("signup_test") if r["is_horizon"]][0]

        assert fixed_h["ci_kind"] == "fixed" and seq_h["ci_kind"] == "always_valid"
        # same point estimate, strictly wider CI (the honest anytime price)
        assert seq_h["effect"] == pytest.approx(fixed_h["effect"])
        assert seq_h["ci_length"] > fixed_h["ci_length"]

    def test_disabled_is_byte_identical_to_fixed(self, warehouse, tables):
        # explicit sequential:false must reproduce the default fixed series exactly
        run(warehouse, tables, experiment=make_experiment(sequential={"enabled": False}))
        rows = tables.load_results("signup_test")
        assert all(r["ci_kind"] == "fixed" for r in rows)

    def test_toggle_on_reruns_fixed_series_to_always_valid(self, warehouse, tables):
        """M5 WP3 (B4): enabling sequential on an EXISTING experiment self-invalidates.

        ``sequential.enabled`` is (correctly) not in ``method_config_id``, so a
        bare re-run would otherwise treat the series as fully computed and leave
        the stale ``fixed`` rows. It must re-plan the whole grid in place.
        """
        run(warehouse, tables)  # sequential off (default) → a fixed series
        fixed = {r["end_ts"]: r for r in tables.load_results("signup_test")}
        assert all(r["ci_kind"] == "fixed" for r in fixed.values())

        # re-run the SAME tables with sequential ON, NO --full-refresh
        outcome = run(warehouse, tables, experiment=make_experiment(sequential={"enabled": True}))
        assert outcome.status == "completed"
        assert outcome.cutoffs_planned == 5  # the whole series re-planned, not skipped

        rows = tables.load_results("signup_test")
        assert len(rows) == 5
        assert all(r["ci_kind"] == "always_valid" for r in rows)
        for r in rows:  # same point estimate, strictly wider CI
            f = fixed[r["end_ts"]]
            assert r["effect"] == pytest.approx(f["effect"])
            assert r["ci_length"] > f["ci_length"]

    def test_toggle_off_reverts_always_valid_to_fixed(self, warehouse, tables):
        run(warehouse, tables, experiment=make_experiment(sequential={"enabled": True}))
        assert all(r["ci_kind"] == "always_valid" for r in tables.load_results("signup_test"))

        outcome = run(warehouse, tables, experiment=make_experiment(sequential={"enabled": False}))
        assert outcome.cutoffs_planned == 5  # re-planned back to fixed
        assert all(r["ci_kind"] == "fixed" for r in tables.load_results("signup_test"))

    def test_enabled_rerun_plans_zero_and_is_byte_stable(self, warehouse, tables):
        """No infinite re-plan: a steady sequential experiment is idempotent."""
        run(warehouse, tables, experiment=make_experiment(sequential={"enabled": True}))
        first = tables.load_results("signup_test")
        outcome = run(warehouse, tables, experiment=make_experiment(sequential={"enabled": True}))
        assert outcome.cutoffs_planned == 0
        assert outcome.results_written == 0

        def strip_version(rows):
            return [{k: v for k, v in r.items() if k != "created_at"} for r in rows]

        assert strip_version(first) == strip_version(tables.load_results("signup_test"))


class TestAsymmetricCiIsRefused:
    """m13 STAT-3a: the pipeline refuses to widen an asymmetric interval, loudly.

    The driver holds only the method CLASS at the τ² anchor, so it binds the method
    the way ``analyze_cutoff`` does and hands the guard that INSTANCE. Without the
    plumbing this run would succeed and persist always-valid rows built from a
    "standard error" that is the mean half-width over z — the silent failure §6a
    describes. Flipping the flag on the shipped class is the only way to reach it:
    no registered method is asymmetric (pinned by the roster gate in
    tests/stats/sequential/test_asymmetric_ci_guard.py).
    """

    def test_sequential_run_fails_with_a_message_naming_the_method(
        self, warehouse, tables, monkeypatch
    ):
        # patch the class the REGISTRY resolves, never an imported symbol:
        # tests/stats/test_registry_factory.py reloads the ttest module, after which
        # `abkit.stats.parametric.ttest.TTest` is a stale object nothing looks up.
        monkeypatch.setattr(get_method_class("t-test"), "asymmetric_ci", True)
        outcome = run(warehouse, tables, experiment=make_experiment(sequential={"enabled": True}))

        assert outcome.status == "failed"
        assert "asymmetric" in str(outcome.error).lower()
        assert "t-test" in str(outcome.error)
        assert tables.load_results("signup_test") == []

    def test_the_fixed_mode_is_untouched_by_the_flag(self, warehouse, tables, monkeypatch):
        """The guard sits on the inversion, not on the method: no sequence, no refusal."""
        monkeypatch.setattr(get_method_class("t-test"), "asymmetric_ci", True)
        outcome = run(warehouse, tables)

        assert outcome.status == "completed"
        assert all(r["ci_kind"] == "fixed" for r in tables.load_results("signup_test"))


class TestSequentialModeChanged:
    """The pure toggle-self-invalidation predicate (M5 WP3 B4)."""

    A = ("control", "treatment")
    B = ("control", "variant_c")

    def test_fresh_or_all_demoted_never_changes(self):
        # no non-demoted persisted rows → nothing to flip, whatever the mode
        assert _sequential_mode_changed({}, True, {self.A: 1.0}) is False
        assert _sequential_mode_changed({}, False, None) is False

    def test_toggle_on_from_fixed(self):
        assert _sequential_mode_changed({self.A: {"fixed"}}, True, {self.A: 1.0}) is True

    def test_toggle_off_from_always_valid(self):
        assert _sequential_mode_changed({self.A: {"always_valid"}}, False, None) is True

    def test_steady_states_do_not_change(self):
        assert _sequential_mode_changed({self.A: {"always_valid"}}, True, {self.A: 1.0}) is False
        assert _sequential_mode_changed({self.A: {"fixed"}}, False, None) is False

    def test_later_usable_pair_left_fixed_is_not_a_toggle(self):
        # pair B first becomes usable AFTER the anchor → no τ² → legitimately
        # fixed while pair A is always_valid. This is the compute's own output.
        kinds = {self.A: {"always_valid"}, self.B: {"fixed"}}
        assert _sequential_mode_changed(kinds, True, {self.A: 1.0}) is False

    def test_half_applied_toggle_self_heals(self):
        # a pair carrying BOTH kinds (a crash mid-flip) forces a full re-plan
        kinds = {self.A: {"fixed", "always_valid"}}
        assert _sequential_mode_changed(kinds, True, {self.A: 1.0}) is True


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


def seed_third_arm(warehouse) -> None:
    """Add a `variant_b` arm to the fixture cohort (the golden-alphas shape)."""
    for i in range(150):
        warehouse.cohort.append((f"x{i}", "variant_b", START + timedelta(hours=1)))
        for day in range(5):
            warehouse.events.append(
                (f"x{i}", "variant_b", START + timedelta(days=day, hours=12), 1.0 + i % 3)
            )


def three_arm_experiment(**overrides) -> ExperimentConfig:
    return make_experiment(
        assignment={
            "query": "SELECT user_id, variant, exposure_ts FROM assignments",
            "variants": ["control", "treatment", "variant_b"],
            "expected_split": {"control": 1 / 3, "treatment": 1 / 3, "variant_b": 1 / 3},
        },
        **overrides,
    )


class TestContrastSetThroughTheDriver:
    """m13 STAT-1b at the driver, not at the pure functions.

    Two things only the real planning loop can prove: that the narrowed family
    does not force an endless re-plan (the `declared_pairs` guard is passed at
    the call site, not merely accepted by the predicate), and that WIDENING the
    family backfills the pairs the historical looks are missing.
    """

    def test_narrowing_the_family_does_not_re_plan_the_series(self, warehouse, tables):
        seed_third_arm(warehouse)
        wide = three_arm_experiment(sequential={"enabled": True})
        run(warehouse, tables, experiment=wide, metrics={"arpu": ARPU})

        narrow = three_arm_experiment(sequential={"enabled": True}, contrasts="vs_control")
        outcome = run(warehouse, tables, experiment=narrow, metrics={"arpu": ARPU})

        # every declared pair is already complete at every cutoff ⇒ nothing to do.
        # Without the declared-pairs guard on _sequential_mode_changed the stale
        # (t1, t2) rows would read as a sequential-mode mismatch and re-plan the
        # WHOLE series here — on this run and on every run after it.
        assert outcome.cutoffs_planned == 0

    def test_widening_the_family_backfills_the_missing_pairs(self, warehouse, tables):
        seed_third_arm(warehouse)
        narrow = three_arm_experiment(contrasts="vs_control")
        run(warehouse, tables, experiment=narrow, metrics={"arpu": ARPU})
        rows = tables.load_results("signup_test")
        assert {(r["name_1"], r["name_2"]) for r in rows} == {
            ("control", "treatment"),
            ("control", "variant_b"),
        }
        looks = len({r["end_ts"] for r in rows})

        wide = three_arm_experiment()
        outcome = run(warehouse, tables, experiment=wide, metrics={"arpu": ARPU})

        # a pair-blind anti-join would call every cutoff "computed" and leave
        # (treatment, variant_b) missing for the whole history, while its
        # siblings kept an alpha bought for the two-contrast family
        assert outcome.cutoffs_planned == looks
        after = tables.load_results("signup_test")
        assert {(r["name_1"], r["name_2"]) for r in after} == {
            ("control", "treatment"),
            ("control", "variant_b"),
            ("treatment", "variant_b"),
        }
        alphas = {round(float(r["alpha"]), 10) for r in after}
        assert alphas == {round(0.05 / 3, 10)}, "the re-plan re-homogenised the series"

    def test_the_widened_run_then_settles(self, warehouse, tables):
        """Convergence: the completeness rule must not re-plan forever."""
        seed_third_arm(warehouse)
        run(warehouse, tables, experiment=three_arm_experiment(contrasts="vs_control"))
        run(warehouse, tables, experiment=three_arm_experiment())
        assert run(warehouse, tables, experiment=three_arm_experiment()).cutoffs_planned == 0


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
    """m10 WP3: the derived Date columns are gone; the RECIPE that replaces
    them is what now needs a gate.

    `_ab_results` stores instants only. docs/reference/internal-tables.md and
    the `abk init` scaffold both tell an operator to recover the calendar day a
    look covers as **`end_ts − 1µs`, read in the experiment timezone** — the
    two traps being that `end_ts` is EXCLUSIVE and stored in UTC. These tests
    run the recipe (transcribed to Python; the SQL forms live in the docs)
    against a real Moscow run and pin that it reproduces, to the day, exactly
    what the dropped columns used to hold — then gate each of its two
    corrections separately, since a single fixture cannot observe both.
    """

    @staticmethod
    def _covered_day(end_ts: datetime, tz: str) -> date:
        """The documented recipe, in Python (SQL equivalents in the docs)."""
        return (
            (end_ts - timedelta(microseconds=1))
            .replace(tzinfo=ZoneInfo("UTC"))
            .astimezone(ZoneInfo(tz))
            .date()
        )

    def _moscow_run(self, warehouse, tables):
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
        return tables.load_results("signup_test")

    def test_the_window_is_stored_as_instants(self, warehouse, tables):
        """The grid still anchors on MOSCOW midnights, and nothing else is
        persisted about the window — no Date column survives the drop."""
        rows = self._moscow_run(warehouse, tables)
        first = min(rows, key=lambda r: r["end_ts"])
        # start_ts = 2024-06-30 21:00 UTC (Moscow midnight July 1);
        # first end_ts = 2024-07-01 21:00 UTC (Moscow midnight July 2, exclusive)
        assert first["start_ts"] == datetime(2024, 6, 30, 21, 0)
        assert first["end_ts"] == datetime(2024, 7, 1, 21, 0)
        assert "start_date" not in first
        assert "end_date" not in first

    def test_the_documented_recipe_reproduces_the_dropped_dates(self, warehouse, tables):
        """`end_ts − 1µs` in the experiment tz == the old `end_date`, exactly."""
        rows = self._moscow_run(warehouse, tables)
        first = min(rows, key=lambda r: r["end_ts"])
        last = max(rows, key=lambda r: r["end_ts"])
        # the window covers Moscow July 1 in full, so the day it MEASURES is
        # July 1 — even though the exclusive edge is stamped July 1 21:00 UTC
        assert self._covered_day(first["end_ts"], "Europe/Moscow") == date(2024, 7, 1)
        assert self._covered_day(last["end_ts"], "Europe/Moscow") == date(2024, 7, 5)

    def test_dropping_the_exclusivity_correction_moves_the_day(self, warehouse, tables):
        """`end_ts` is the EXCLUSIVE edge, so it is stamped with the NEXT day's
        boundary. Read it without the −1µs and every look is dated a day late."""
        rows = self._moscow_run(warehouse, tables)
        end_ts = min(rows, key=lambda r: r["end_ts"])["end_ts"]

        assert self._covered_day(end_ts, "Europe/Moscow") == date(2024, 7, 1)
        no_microsecond = (
            end_ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Moscow")).date()
        )
        assert no_microsecond == date(2024, 7, 2)  # off by a day

    def test_dropping_the_timezone_correction_moves_the_day(self):
        """The leg the Moscow fixture CANNOT gate — and why this test is
        arithmetic rather than another pipeline run.

        At UTC+3 a local midnight lands at 21:00 UTC of the previous day, so
        `end_ts − 1µs` already carries the right calendar date and the
        `astimezone` is a no-op: delete it and the Moscow assertions above stay
        green. That coincidence is a property of the sign of the offset, so the
        leg only becomes observable west of Greenwich. New York in July is
        UTC−4: local midnight July 2 is 04:00 UTC *July 2*, and the naive read
        dates the look July 2 — a day after the one it measures.
        """
        ny_midnight_utc = datetime(2024, 7, 2, 4, 0)  # 2024-07-02 00:00 EDT

        assert self._covered_day(ny_midnight_utc, "America/New_York") == date(2024, 7, 1)
        no_tz = (ny_midnight_utc - timedelta(microseconds=1)).date()
        assert no_tz == date(2024, 7, 2)  # off by a day

        # ...and eastward the two agree, which is exactly the trap: a fixture
        # in a positive-offset zone proves nothing about this correction.
        msk_midnight_utc = datetime(2024, 7, 1, 21, 0)  # 2024-07-02 00:00 MSK
        assert (msk_midnight_utc - timedelta(microseconds=1)).date() == self._covered_day(
            msk_midnight_utc, "Europe/Moscow"
        )


def seed_subday_cohort(warehouse: SyntheticWarehouse) -> None:
    """A 60/40 imbalance accruing 150 control + 100 treatment per 6h look.

    Cumulative counts at the 06:00/12:00/18:00/24:00 boundaries are
    150/100, 300/200, 450/300, 600/400 — the anytime-valid gate trips at look
    2 under the strict 0.001 gate, not look 1 (the truthful as-of series)."""
    warehouse.cohort = []
    warehouse.events = []
    for batch in range(4):  # exposed at 03:00, 09:00, 15:00, 21:00
        expose = START + timedelta(hours=3 + 6 * batch)
        for i in range(150):
            unit = f"c{batch}_{i}"
            warehouse.cohort.append((unit, "control", expose))
            warehouse.events.append((unit, "control", expose + timedelta(minutes=30), 1.0))
        for i in range(100):
            unit = f"t{batch}_{i}"
            warehouse.cohort.append((unit, "treatment", expose))
            warehouse.events.append((unit, "treatment", expose + timedelta(minutes=30), 1.1))


class TestSubDaySrmGate:
    """M5 WP5: below 1d the SRM gate is the anytime-valid multinomial e-process,
    stamped PER LOOK; daily & coarser keep the whole-cohort χ² broadcast."""

    def _subday_experiment(self) -> ExperimentConfig:
        return make_experiment(
            start_ts="2024-07-01", horizon_ts="2024-07-02", cadence="6h", data_lag="1h"
        )

    def test_subday_uses_anytime_gate_stamped_per_look(self, tables):
        warehouse = SyntheticWarehouse()
        seed_subday_cohort(warehouse)
        outcome = run(warehouse, tables, experiment=self._subday_experiment())
        assert outcome.status == "completed", outcome.error
        assert outcome.srm_flagged is True  # the latest look's running verdict
        # the loud line names the sequential gate, not χ²
        assert any("anytime e=" in w for w in outcome.warnings)
        assert not any("chi2" in w for w in outcome.warnings)

        rows = sorted(tables.load_results("signup_test"), key=lambda r: r["end_ts"])
        assert len(rows) == 4  # one pair × 4 sub-day looks
        # the truthful as-of series: look 1 quiet (150/100, e≈12 < 1000), then trips
        assert [r["srm_flag"] for r in rows] == [False, True, True, True]
        assert [r["decision_blocked"] for r in rows] == [False, True, True, True]
        assert all(r["srm_pvalue"] is not None for r in rows)  # never dropped

    def test_subday_balanced_never_flags(self, tables):
        warehouse = SyntheticWarehouse()
        # equal 150/150 per look ⇒ a perfectly balanced cumulative stream
        for batch in range(4):
            expose = START + timedelta(hours=3 + 6 * batch)
            for arm in ("control", "treatment"):
                for i in range(150):
                    unit = f"{arm[0]}{batch}_{i}"
                    warehouse.cohort.append((unit, arm, expose))
                    warehouse.events.append((unit, arm, expose + timedelta(minutes=30), 1.0))
        outcome = run(warehouse, tables, experiment=self._subday_experiment())
        assert outcome.status == "completed", outcome.error
        assert outcome.srm_flagged is False
        rows = tables.load_results("signup_test")
        assert rows and not any(r["srm_flag"] for r in rows)

    def test_daily_keeps_chi_square_broadcast(self, warehouse, tables):
        """A daily experiment (default cadence) with a blatant imbalance stays
        on the χ² gate: the loud line reads 'chi2' and every row shares it."""
        warehouse.cohort = [c for c in warehouse.cohort if not c[0].startswith("t")][:150]
        for i in range(15):  # 150 vs 15 — a blatant SRM
            warehouse.cohort.append((f"t{i}", "treatment", START + timedelta(hours=1)))
        warehouse.events = []
        seed_events(warehouse)
        outcome = run(warehouse, tables)  # default daily cadence
        assert outcome.srm_flagged is True
        assert any("chi2" in w for w in outcome.warnings)
        assert not any("anytime e=" in w for w in outcome.warnings)
        rows = tables.load_results("signup_test")
        assert rows and all(r["srm_flag"] for r in rows)  # one whole-run gate, broadcast


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


class TestBacklogSignal:
    """What `abk run --notify` routes as `stale` (m12 NTF-5).

    The warning itself is m2's; NTF-5 gave it a structured twin and, in doing
    so, found it firing on experiments that are simply over.
    """

    def test_a_finished_fully_computed_experiment_reports_nothing(self, warehouse, tables):
        """The regression that made the signal unroutable.

        Both runs are past the July 6 horizon with every look computed. Against
        the raw watermark the second reported a 336h backlog — and would have
        kept reporting a larger one every day, on an experiment with nothing
        left to compute.
        """
        run(warehouse, tables)
        second = run(warehouse, tables)

        assert second.cutoffs_planned == 0
        assert second.backlog == []
        assert [w for w in second.warnings if "backlog" in w] == []

    def test_a_series_left_behind_is_reported_with_its_metric_and_lag(self, warehouse, tables):
        """A long experiment whose looks stopped being computed for two weeks."""
        experiment = make_experiment(horizon_ts="2024-07-25")
        seed_events(warehouse, days=24)
        run(warehouse, tables, experiment=experiment, now_utc=datetime(2024, 7, 4))
        outcome = run(warehouse, tables, experiment=experiment, now_utc=datetime(2024, 7, 18))

        assert [entry.metric for entry in outcome.backlog] == ["arpu"]
        # last DUE cutoff (July 18) − last computed (July 4) = 14 days
        assert outcome.backlog[0].lag_seconds == 14 * 86400
        # the prose half still fires, and agrees with the data half
        warning = next(w for w in outcome.warnings if "backlog" in w)
        assert "336.0h behind the looks already due" in warning and "arpu" in warning


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
        # M9 WP1: the two moments completing the per-arm covariate suffstats
        # persist alongside (cov_m2 = cov_std²·n, cross_c = corr·√(m2·cov_m2))
        assert rows[-1]["cov_std_1"] is not None and rows[-1]["cov_std_2"] is not None
        assert rows[-1]["corr_coef_1"] is not None and rows[-1]["corr_coef_2"] is not None
        assert -1.0 <= rows[-1]["corr_coef_1"] <= 1.0
        assert rows[-1]["effect"] is not None
        assert rows[-1]["method_name"] == "cuped-t-test"
        assert '"covariate_lookback":"14d"' in rows[-1]["method_params"]


class TestCohortModeParity:
    """m8 WP4: ONE source switch — identical numbers, opposite write paths."""

    @staticmethod
    def _copy_experiment(**overrides) -> ExperimentConfig:
        assignment = {
            # copy mode requires the {{ ab_added_filters }} injection point —
            # the WP5 incremental engine lands its batch bounds there
            "query": (
                "SELECT user_id, variant, exposure_ts FROM assignments "
                "WHERE 1 = 1 {{ ab_added_filters }}"
            ),
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
            "cohort_copy": {"enabled": True},
        }
        return make_experiment(assignment=assignment, **overrides)

    @staticmethod
    def _comparable(rows: list[dict]) -> list[dict]:
        """Results rows minus the two legitimately mode-variant columns:
        ``created_at`` (a wall-clock version stamp) and
        ``metric_rendered_query`` (direct mode embeds the assignment subquery
        BY DESIGN — provenance, not a number)."""
        volatile = {"created_at", "metric_rendered_query"}
        stripped = [{k: v for k, v in r.items() if k not in volatile} for r in rows]
        return sorted(
            stripped,
            key=lambda r: (str(r["end_ts"]), str(r["name_1"]), str(r["name_2"])),
        )

    def test_direct_default_never_writes_exposures(self, warehouse, tables):
        outcome = run(warehouse, tables)
        assert outcome.status == "completed", outcome.error
        assert outcome.exposures_loaded == 300  # the snapshot still feeds SRM
        # the no-copy default: zero _ab_exposures rows on the hot path
        assert warehouse._rows.get("_ab_exposures", []) == []

    def test_copy_mode_persists_and_numbers_match_direct(self, warehouse, tables):
        direct_outcome = run(warehouse, tables)
        assert direct_outcome.status == "completed", direct_outcome.error
        direct_rows = tables.load_results("signup_test")

        wh2 = SyntheticWarehouse()
        seed_cohort(wh2)
        seed_events(wh2)
        tables2 = InternalTablesManager(wh2)
        copy_outcome = run(wh2, tables2, experiment=self._copy_experiment())
        assert copy_outcome.status == "completed", copy_outcome.error
        # the opt-in copy persists the full deduped cohort
        assert len(wh2._rows.get("_ab_exposures", [])) == 300
        assert copy_outcome.exposures_loaded == direct_outcome.exposures_loaded

        # the milestone's core numeric-parity gate: every persisted number is
        # identical across modes on a well-formed cohort
        copy_rows = tables2.load_results("signup_test")
        assert self._comparable(direct_rows) == self._comparable(copy_rows)

    def test_subday_srm_stream_is_mode_invariant(self):
        """The sub-day anytime-valid SRM sees the SAME cumulative count stream
        whether it is bucketed from the persisted copy (mixin) or the
        in-memory snapshot (driver direct mode) — one bisect implementation."""
        verdicts: dict[str, list[tuple]] = {}
        for mode in ("direct", "copy"):
            wh = SyntheticWarehouse()
            seed_subday_cohort(wh)
            tbl = InternalTablesManager(wh)
            experiment = (
                make_experiment(
                    start_ts="2024-07-01", horizon_ts="2024-07-02", cadence="6h", data_lag="1h"
                )
                if mode == "direct"
                else self._copy_experiment(
                    start_ts="2024-07-01", horizon_ts="2024-07-02", cadence="6h", data_lag="1h"
                )
            )
            outcome = run(wh, tbl, experiment=experiment)
            assert outcome.status == "completed", outcome.error
            rows = sorted(tbl.load_results("signup_test"), key=lambda r: r["end_ts"])
            verdicts[mode] = [
                (str(r["end_ts"]), r["srm_flag"], r["srm_pvalue"], r["decision_blocked"])
                for r in rows
            ]
        assert verdicts["direct"] == verdicts["copy"]
        # the fixture is built to trip at look 2 — prove the gate actually bit
        assert [flag for _, flag, _, _ in verdicts["direct"]] == [False, True, True, True]


class TestIncrementalCopySeam:
    """m8 WP5: the driver's copy-mode write is the incremental engine."""

    @staticmethod
    def _spy_deletes(warehouse) -> list[tuple]:
        deletes: list[tuple] = []
        original = warehouse.delete_rows

        def spy(*args, **kwargs):
            deletes.append(args)
            return original(*args, **kwargs)

        warehouse.delete_rows = spy
        return deletes

    def test_second_run_only_appends_the_delta(self, warehouse, tables):
        experiment = TestCohortModeParity._copy_experiment()
        outcome = run(warehouse, tables, experiment=experiment)
        assert outcome.status == "completed", outcome.error
        assert len(warehouse._rows["_ab_exposures"]) == 300

        deletes = self._spy_deletes(warehouse)
        # the source grows: 4 new units exposed AFTER the watermark
        for i, ts in enumerate((START + timedelta(days=2), START + timedelta(days=3)), start=900):
            warehouse.cohort.append((f"c{i}", "control", ts))
            warehouse.cohort.append((f"t{i}", "treatment", ts))
        second = run(warehouse, tables, experiment=experiment)
        assert second.status == "completed", second.error

        exposures = warehouse._rows["_ab_exposures"]
        assert len(exposures) == 304  # grew by exactly the delta
        # append-only: no delete touched the persisted cohort on the re-run
        assert not any("_ab_exposures" in str(args[0]) for args in deletes)
        # the SRM snapshot still counts the LIVE source in copy mode
        assert second.exposures_loaded == 304

    def test_resync_cohort_rebuilds_the_copy_through_the_engine(self, warehouse, tables):
        """--resync-cohort = delete + a from-scratch reload through the SAME
        incremental engine (round 2: one write path, one discipline)."""
        experiment = TestCohortModeParity._copy_experiment()
        run(warehouse, tables, experiment=experiment)
        deletes = self._spy_deletes(warehouse)

        outcome = run(warehouse, tables, experiment=experiment, resync_cohort=True)
        assert outcome.status == "completed", outcome.error
        assert any("_ab_exposures" in str(args[0]) for args in deletes)
        assert len(warehouse._rows["_ab_exposures"]) == 300

    def test_resync_heals_a_late_arrival_gap(self, warehouse, tables):
        """The flag's purpose: a backfilled row below the watermark is missed
        by routine runs (the disclosed §4 Q3 limitation) — the resync's
        from-scratch re-scan recovers it."""
        experiment = TestCohortModeParity._copy_experiment(horizon_ts="2024-08-31")
        run(warehouse, tables, experiment=experiment)
        # advance the watermark into a later bucket first — a backfill into
        # the watermark's OWN bucket is rescued by the floor re-scan
        warehouse.cohort.append(("later", "treatment", START + timedelta(days=5)))
        run(warehouse, tables, experiment=experiment)

        # a backfilled assignment row, below the watermark's bucket floor
        warehouse.cohort.append(("backfilled", "control", START + timedelta(days=2)))
        routine = run(warehouse, tables, experiment=experiment)
        assert routine.status == "completed", routine.error
        assert all(r["unit_id"] != "backfilled" for r in warehouse._rows["_ab_exposures"])

        recovered = run(warehouse, tables, experiment=experiment, resync_cohort=True)
        assert recovered.status == "completed", recovered.error
        assert any(r["unit_id"] == "backfilled" for r in warehouse._rows["_ab_exposures"])

    def test_resync_run_also_reports_trailing_coverage(self, warehouse, tables):
        """Round 2: the coverage warning fires on the resync path too — the
        rebuilt copy obeys the same closed/matured boundary."""
        assignment = {
            "query": (
                "SELECT user_id, variant, exposure_ts FROM assignments "
                "WHERE 1 = 1 {{ ab_added_filters }}"
            ),
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
            "cohort_copy": {"enabled": True, "maturity_delay": "1d"},
        }
        experiment = make_experiment(assignment=assignment, horizon_ts="2024-08-31")
        outcome = run(
            warehouse,
            tables,
            experiment=experiment,
            resync_cohort=True,
            now_utc=datetime(2024, 7, 20, 12),
        )
        assert outcome.status == "completed", outcome.error
        assert any("cohort copy trails" in w for w in outcome.warnings)

    def test_resync_cohort_is_a_noop_in_direct_mode(self, warehouse, tables):
        lines: list[str] = []
        outcome = run(warehouse, tables, resync_cohort=True, log=lines.append)
        assert outcome.status == "completed", outcome.error
        assert warehouse._rows.get("_ab_exposures", []) == []
        assert any("no effect in direct mode" in line for line in lines)

    def test_copy_trailing_the_compute_watermark_warns(self, warehouse, tables):
        # a live experiment (horizon in the future) with data_lag 0 (default)
        # but maturity_delay 1d: the copy stops a full day before the newest
        # computable cutoff → that cutoff reads a partial cohort
        late_ts = datetime(2024, 7, 19, 22, 0)  # inside the withheld window
        warehouse.cohort.append(("late1", "control", late_ts))
        assignment = {
            "query": (
                "SELECT user_id, variant, exposure_ts FROM assignments "
                "WHERE 1 = 1 {{ ab_added_filters }}"
            ),
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
            "cohort_copy": {"enabled": True, "maturity_delay": "1d"},
        }
        experiment = make_experiment(assignment=assignment, horizon_ts="2024-08-31")
        outcome = run(warehouse, tables, experiment=experiment, now_utc=datetime(2024, 7, 20, 12))
        assert outcome.status == "completed", outcome.error
        trailing = [w for w in outcome.warnings if "cohort copy trails" in w]
        assert trailing, outcome.warnings
        assert "data_lag" in trailing[0]
        # the withheld-window unit is absent from the copy (closed intervals only)
        assert all(r["unit_id"] != "late1" for r in warehouse._rows["_ab_exposures"])

    def test_matured_experiment_never_warns(self, warehouse, tables):
        # horizon long past, everything matured: coverage reaches the horizon
        outcome = run(warehouse, tables, experiment=TestCohortModeParity._copy_experiment())
        assert outcome.status == "completed", outcome.error
        assert not any("cohort copy trails" in w for w in outcome.warnings)

    def test_closed_enrollment_rerun_never_false_warns(self, warehouse, tables):
        """Review-confirmed: coverage is the deterministic grid bound, not the
        data maximum — a cohort whose source stopped producing rows must not
        read as 'trailing' on every subsequent run forever."""
        experiment = TestCohortModeParity._copy_experiment(horizon_ts="2024-08-31")
        first = run(warehouse, tables, experiment=experiment)
        assert first.status == "completed", first.error
        assert not any("cohort copy trails" in w for w in first.warnings)

        # the 30min offset lands INSIDE the pre-fix failure branch (the old
        # floating-watermark snap left a ~1h residual — verified to false-warn
        # on the pre-fix engine), so this pin actually bites
        second = run(warehouse, tables, experiment=experiment, now_utc=NOW + timedelta(minutes=30))
        assert second.status == "completed", second.error
        assert not any("cohort copy trails" in w for w in second.warnings)

    def test_resync_cannot_poison_the_watermark(self, warehouse, tables):
        """Review-confirmed regression guard: --resync-cohort persists only
        rows below the closed/matured boundary. An ungated rewrite would
        advance MAX(exposure_ts) to ~now and permanently fence out units that
        were still inside the open/maturity window at resync time."""
        assignment = {
            "query": (
                "SELECT user_id, variant, exposure_ts FROM assignments "
                "WHERE 1 = 1 {{ ab_added_filters }}"
            ),
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
            "cohort_copy": {"enabled": True, "maturity_delay": "1d"},
        }
        experiment = make_experiment(assignment=assignment, horizon_ts="2024-08-31")
        t0 = datetime(2024, 7, 20, 12)
        run(warehouse, tables, experiment=experiment, now_utc=t0)

        # a unit exposed 2h before the resync — still inside the maturity window
        warehouse.cohort.append(("freshA", "control", t0 - timedelta(hours=2)))
        resync = run(warehouse, tables, experiment=experiment, resync_cohort=True, now_utc=t0)
        assert resync.status == "completed", resync.error
        exposures = warehouse._rows["_ab_exposures"]
        assert all(r["unit_id"] != "freshA" for r in exposures)  # gated out

        # a routine later run picks the SAME unit up once its bucket matures —
        # nothing was fenced out by the resync
        later = run(warehouse, tables, experiment=experiment, now_utc=t0 + timedelta(days=3))
        assert later.status == "completed", later.error
        assert any(r["unit_id"] == "freshA" for r in warehouse._rows["_ab_exposures"])


class TestMetricFilter:
    """m11 DASH-4a: ``abk run --metric`` recomputes ONE metric's series.

    The WP's #1 assertion is **alpha invariance**: ``effective_alphas`` derives
    the two-tier budget from the CONFIG's comparison list, so narrowing what a
    run computes must never re-alpha the series it writes. The fixture is built
    to catch exactly that leak — 1 main + 2 secondary metrics over 2 variants,
    so the secondary tier is α/2 = 0.025 and a filter that reached
    ``effective_alphas`` would write 0.05 instead.
    """

    METHOD = {"name": "t-test", "params": {"test_type": "relative"}}

    @classmethod
    def _experiment(cls) -> ExperimentConfig:
        return make_experiment(
            comparisons=[
                {"metric": "arpu", "is_main_metric": True, "method": cls.METHOD},
                {"metric": "arpu2", "method": cls.METHOD},
                {"metric": "arpu3", "method": cls.METHOD},
            ]
        )

    @staticmethod
    def _metrics() -> dict[str, MetricConfig]:
        return {
            "arpu": ARPU,
            "arpu2": ARPU.model_copy(update={"name": "arpu2"}),
            "arpu3": ARPU.model_copy(update={"name": "arpu3"}),
        }

    @staticmethod
    def _stable(rows: list[dict]) -> list[dict]:
        """Rows minus ``created_at`` — the LWW version stamp, volatile by design."""
        return [{k: v for k, v in row.items() if k != "created_at"} for row in rows]

    @staticmethod
    def _fresh_store() -> tuple[SyntheticWarehouse, InternalTablesManager]:
        warehouse = SyntheticWarehouse()
        seed_cohort(warehouse)
        seed_events(warehouse)
        return warehouse, InternalTablesManager(warehouse)

    def test_filtered_rows_are_byte_identical_to_an_unfiltered_run(self, warehouse, tables):
        """The #1 assertion, on the metric where a leak would show: a
        SECONDARY one, whose tier is divided by the number of non-main
        comparisons the CONFIG declares."""
        experiment, metrics = self._experiment(), self._metrics()
        run(warehouse, tables, experiment=experiment, metrics=metrics)
        unfiltered = self._stable(tables.load_results("signup_test", metric="arpu2"))
        assert unfiltered, "the unfiltered run must have written the secondary series"
        assert sorted({row["alpha"] for row in unfiltered}) == [pytest.approx(0.05 / 2)]

        second, tables2 = self._fresh_store()
        outcome = run(
            second, tables2, experiment=experiment, metrics=metrics, metric_filter="arpu2"
        )
        assert outcome.status == "completed", outcome.error

        filtered = self._stable(tables2.load_results("signup_test", metric="arpu2"))
        assert filtered == unfiltered  # alpha included: the two-tier scheme is config-derived
        # and nothing else was computed
        assert tables2.load_results("signup_test", metric="arpu") == []
        assert tables2.load_results("signup_test", metric="arpu3") == []
        assert outcome.cutoffs_planned == 5  # one metric's looks, not three metrics'

    def test_a_second_filtered_run_leaves_the_first_series_untouched(self, warehouse, tables):
        experiment, metrics = self._experiment(), self._metrics()
        run(warehouse, tables, experiment=experiment, metrics=metrics, metric_filter="arpu")
        first = tables.load_results("signup_test", metric="arpu")
        assert len(first) == 5

        outcome = run(
            warehouse, tables, experiment=experiment, metrics=metrics, metric_filter="arpu2"
        )
        assert outcome.status == "completed", outcome.error
        # untouched INCLUDING created_at: the row was never even re-written
        assert tables.load_results("signup_test", metric="arpu") == first
        assert len(tables.load_results("signup_test", metric="arpu2")) == 5
        assert tables.load_results("signup_test", metric="arpu3") == []

    def test_full_refresh_metric_reopens_only_that_series(self, warehouse, tables):
        experiment, metrics = self._experiment(), self._metrics()
        run(warehouse, tables, experiment=experiment, metrics=metrics)
        untouched = tables.load_results("signup_test", metric="arpu2")

        outcome = run(
            warehouse,
            tables,
            experiment=experiment,
            metrics=metrics,
            metric_filter="arpu",
            full_refresh_window=(datetime(2024, 7, 2), datetime(2024, 7, 4)),
        )
        assert outcome.status == "completed", outcome.error
        assert outcome.cutoffs_planned == 2  # the window's two looks of ONE metric
        # the sibling series was neither deleted nor re-written (created_at included)
        assert tables.load_results("signup_test", metric="arpu2") == untouched
        assert len(tables.load_results("signup_test")) == 15  # LWW, no duplicates

    def test_an_unmatched_filter_skips_before_the_lock(self, warehouse, tables):
        outcome = run(warehouse, tables, metric_filter="nope")
        assert outcome.status == "skipped"
        assert "nope" in (outcome.error or "")
        assert tables.load_results("signup_test") == []
        # never locked, never rendered a cohort: the guard sits above ensure_tables
        assert warehouse._rows.get("_ab_tasks", []) == []
        assert outcome.exposures_loaded == 0

    def test_the_worker_pool_passes_the_filter_through(self, warehouse, tables):
        experiment, metrics = self._experiment(), self._metrics()
        second = experiment.model_copy(update={"name": "second_test"})
        outcomes = run_experiments(
            [(None, experiment), (None, second)],
            metrics,
            PROJECT,
            manager_factory=lambda: warehouse,
            # >1 so the ThreadPoolExecutor branch is the one under test: this
            # function's OTHER keyword (`ensure_tables`) needed a second call
            # site on exactly that branch (see driver.py), so "the pool path
            # forgot the parameter" is a lived bug class here
            max_workers=2,
            now_utc=NOW,
            metric_filter="arpu3",
        )
        assert all(o.status == "completed" for o in outcomes), [o.error for o in outcomes]
        for name in ("signup_test", "second_test"):
            assert len(tables.load_results(name, metric="arpu3")) == 5
            assert tables.load_results(name, metric="arpu") == []
