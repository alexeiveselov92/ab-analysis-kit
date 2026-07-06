"""WP3c tests: the explore recompute engine under the sequential MODE.

The cockpit is the priority interface, so a sequential-enabled experiment must
never mix CI vocabularies: the baked series is always-valid, so every live
recompute point has to be too. These prove the threading of
``to_always_valid`` through the recompute tiers:

- the default knob state reproduces the baked always-valid bounds (Tier E
  recovers the pre-widening fixed CI from the persisted per-arm stats, then
  re-widens with the same first-usable-look τ²);
- the sequential live CI is strictly wider than the fixed twin's (the anytime
  price), at the same point estimate;
- α-inversion cannot honestly widen an already-widened persisted CI, so under
  the sequential mode those cutoffs are dropped with a Reload hint rather than
  shown as a silent fixed CI;
- switching to a sequential-ineligible method (bootstrap) turns the mode off
  (declarative ``supports_sequential`` gate), never widening a percentile CI.

The warehouse harness lives in ``tests/_helpers/synthetic_ab.py``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from synthetic_ab import (
    START,
    SyntheticWarehouse,
    assert_close,
    build_engine,
    make_experiment,
    persisted,
    run_pipeline,
    seed_all_events,
    seed_cohort,
)

from abkit.config import ExperimentConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.tuning import KnobState

TTEST = {"name": "t-test", "params": {"test_type": "relative"}}
CUPED = {"name": "cuped-t-test", "params": {"test_type": "relative", "covariate_lookback": "7d"}}
SEQ = {"enabled": True}


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_all_events(wh)
    return wh


@pytest.fixture
def tables(warehouse):
    return InternalTablesManager(warehouse)


class TestSequentialRecompute:
    def test_default_recompute_reproduces_baked_always_valid(self, warehouse, tables):
        """The configured knob state's live CI == the baked always-valid CI."""
        exp = make_experiment("seq_arpu", "arpu", TTEST, sequential=SEQ)
        run_pipeline(warehouse, tables, exp)
        baked = persisted(tables, exp, "arpu")
        assert baked and all(r["ci_kind"] == "always_valid" for r in baked.values())

        engine = build_engine(warehouse, tables, exp)
        result = engine.recompute("arpu", engine.default_knobs("arpu"))
        points = result.pairs[0].points
        assert len(points) == 4
        for point in points:
            row = baked[(result.pairs[0].name_1, result.pairs[0].name_2, point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")
            assert point.reject == row["reject"]

    def test_sequential_live_ci_is_wider_than_fixed(self, warehouse, tables):
        """Same point estimate, strictly wider live CI — the honest anytime price."""
        seq = make_experiment("seq_arpu", "arpu", TTEST, sequential=SEQ)
        run_pipeline(warehouse, tables, seq)
        seq_pt = (
            build_engine(warehouse, tables, seq)
            .recompute("arpu", KnobState("t-test", {"test_type": "relative"}))
            .pairs[0]
            .points[-1]
        )

        wh2 = SyntheticWarehouse()
        seed_cohort(wh2)
        seed_all_events(wh2)
        tbl2 = InternalTablesManager(wh2)
        fixed = make_experiment("fx_arpu", "arpu", TTEST)
        run_pipeline(wh2, tbl2, fixed)
        fx_pt = (
            build_engine(wh2, tbl2, fixed)
            .recompute("arpu", KnobState("t-test", {"test_type": "relative"}))
            .pairs[0]
            .points[-1]
        )

        assert seq_pt.effect == pytest.approx(fx_pt.effect, rel=1e-9)
        assert (seq_pt.right_bound - seq_pt.left_bound) > (fx_pt.right_bound - fx_pt.left_bound)

    def test_alpha_change_drops_alpha_inverted_cutoffs_with_reload_hint(self, warehouse, tables):
        """α-inversion can't widen a persisted always-valid CI → drop + Reload hint."""
        exp = make_experiment("seq_cuped", "arpu", CUPED, alpha=0.05, sequential=SEQ)
        run_pipeline(warehouse, tables, exp)

        # cache only the LATEST cutoff (budget clamp) so older cutoffs α-invert
        engine = build_engine(warehouse, tables, exp, budget=500)
        cached = engine._session.cached_cutoffs("arpu")
        result = engine.recompute("arpu", KnobState("cuped-t-test", CUPED["params"], alpha=0.01))
        points = result.pairs[0].points

        # the α-inverted cutoffs are gone (only Tier-S cached cutoffs survive)
        assert 0 < len(points) < 4
        assert all(point.end_ts in cached for point in points)
        # every surviving point is always-valid (widened Tier-S result), not fixed
        assert all(
            point.result is not None and point.result.ci_kind == "always_valid" for point in points
        )
        assert any("Reload" in warning for warning in result.warnings)

    def test_bootstrap_switch_disables_sequential_mode(self, warehouse, tables):
        """A sequential-ineligible method's percentile CI is never widened."""
        exp = make_experiment("seq_arpu", "arpu", TTEST, sequential=SEQ)
        run_pipeline(warehouse, tables, exp)
        engine = build_engine(warehouse, tables, exp)
        result = engine.recompute(
            "arpu", KnobState("bootstrap", {"test_type": "relative", "n_samples": 100})
        )
        # bootstrap has supports_sequential=False → mode off → no widening, no drops
        assert result.pairs[0].points
        assert not any("Reload" in warning for warning in result.warnings)
        for point in result.pairs[0].points:
            if point.result is not None:
                assert point.result.ci_kind == "fixed"


def _three_arm_late_rollout(warehouse) -> None:
    """control + treatment usable from day 0; variant_c rolls out on day 2.

    So at the series' first-usable look (cutoff 1) only (control,treatment) reaches
    ``min_units_per_arm=100`` — the driver's D-Seq-anchor freezes τ² for that pair
    only and leaves (·,variant_c) fixed at every later cutoff (the multi-pair quirk).
    """
    for i in range(150):
        warehouse.cohort.append((f"c{i:03d}", "control", START + timedelta(hours=1)))
        warehouse.cohort.append((f"t{i:03d}", "treatment", START + timedelta(hours=1)))
    for i in range(120):
        warehouse.cohort.append((f"v{i:03d}", "variant_c", START + timedelta(days=2, hours=1)))
    for unit, variant, _ in warehouse.cohort:
        idx = int(unit[1:])
        start_day = 2 if variant == "variant_c" else 0
        base = 1.0 + (idx % 7) * 0.5
        lift = 1.25 if variant != "control" else 1.0
        for day in range(start_day, 4):
            warehouse.events["user_revenue"].append(
                (unit, variant, START + timedelta(days=day, hours=12), {"gross_usd": base * lift})
            )


def _three_arm_experiment(sequential: dict) -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "name": "seq3",
            "start_date": "2024-07-01",
            "end_date": "2024-07-04",
            "unit_key": "user_id",
            "assignment": {
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "treatment", "variant_c"],
                "expected_split": {"control": 0.34, "treatment": 0.33, "variant_c": 0.33},
            },
            "comparisons": [{"metric": "arpu", "is_main_metric": True, "method": TTEST}],
            "sequential": sequential,
        }
    )


class TestMultiPairLateRollout:
    """The D-Seq-anchor multi-pair quirk: only the anchor pair is sequentialized,
    and the live cockpit reproduces that per-pair vocabulary (no over-widening)."""

    def _kinds(self, rows, pair):
        return {
            r["ci_kind"]
            for (n1, n2, _), r in rows.items()
            if (n1, n2) == pair and not r["insufficient_data"]
        }

    def test_pipeline_leaves_late_pair_fixed(self):
        wh = SyntheticWarehouse()
        _three_arm_late_rollout(wh)
        tbl = InternalTablesManager(wh)
        run_pipeline(wh, tbl, _three_arm_experiment(SEQ))

        rows = persisted(tbl, _three_arm_experiment(SEQ), "arpu")
        # the anchor pair is always-valid; the late-usable pairs stay fixed
        assert self._kinds(rows, ("control", "treatment")) == {"always_valid"}
        assert self._kinds(rows, ("control", "variant_c")) == {"fixed"}
        assert self._kinds(rows, ("treatment", "variant_c")) == {"fixed"}

    def test_explore_mirrors_the_per_pair_vocabulary(self):
        wh = SyntheticWarehouse()
        _three_arm_late_rollout(wh)
        tbl = InternalTablesManager(wh)
        exp = _three_arm_experiment(SEQ)
        run_pipeline(wh, tbl, exp)

        engine = build_engine(wh, tbl, exp)
        result = engine.recompute("arpu", engine.default_knobs("arpu"))
        by_pair = {(p.name_1, p.name_2): p for p in result.pairs}

        # anchor pair: every reconstructed point widened to always_valid
        for point in by_pair[("control", "treatment")].points:
            if point.result is not None:
                assert point.result.ci_kind == "always_valid"
        # late pairs: NOT widened live (matching the baked fixed rows), no Reload hint
        for pair in (("control", "variant_c"), ("treatment", "variant_c")):
            for point in by_pair[pair].points:
                if point.result is not None:
                    assert point.result.ci_kind == "fixed"
        assert not any("Reload" in warning for warning in result.warnings)
