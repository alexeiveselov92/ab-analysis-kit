"""WP2 load stage: pooled panels from the real loaders + the denser-early subsampler.

Exercises ``load_placebo_panel`` end-to-end against ``SyntheticWarehouse`` (a null
twin gives an analytic FPR ≈ α), plus the grid subsampler in isolation.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
from synthetic_ab import (
    CONVERSION,
    CTR,
    REVENUE,
    START,
    SyntheticWarehouse,
    experiment_payload,
    make_experiment,
    seed_cohort,
    seed_null_events,
)

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ExperimentConfig
from abkit.core.period_planner import Cutoff
from abkit.stats.factory import create_method
from abkit.validate.load import load_placebo_panel, subsample_grid
from abkit.validate.scoring import score_cell


def _grid(experiment):
    return experiment.grid()


def _band(p, n, sigmas=3.0):
    return sigmas * math.sqrt(p * (1.0 - p) / n)


# ── the subsampler ────────────────────────────────────────────────────────────


def _fake_cutoffs(n):
    base = datetime(2024, 1, 1)
    return tuple(Cutoff(end_ts=base + timedelta(days=k), is_horizon=(k == n - 1)) for k in range(n))


def test_subsample_keeps_all_when_under_cap():
    cutoffs = _fake_cutoffs(30)
    kept, k, total = subsample_grid(cutoffs, cap=100)
    assert (k, total) == (30, 30)
    assert kept == list(cutoffs)


def test_subsample_caps_denser_early_and_keeps_horizon():
    cutoffs = _fake_cutoffs(500)
    kept, k, total = subsample_grid(cutoffs, cap=100)
    assert total == 500
    assert k <= 100
    assert kept[0] == cutoffs[0]
    assert kept[-1] == cutoffs[-1] and kept[-1].is_horizon  # horizon always retained
    # denser early: more kept points in the first quarter than the last quarter
    idx = [cutoffs.index(c) for c in kept]
    first_quarter = sum(1 for i in idx if i < 125)
    last_quarter = sum(1 for i in idx if i >= 375)
    assert first_quarter > last_quarter


# ── the panel loader (integration with the real loaders) ─────────────────────


def test_sample_panel_pools_units_and_scores_null():
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=160)
    seed_null_events(warehouse)
    experiment = make_experiment("aa_arpu", "arpu", {"name": "t-test"})
    backend = RecomputeBackend(warehouse, experiment)

    panel = load_placebo_panel(
        backend,
        experiment.comparisons[0],
        REVENUE,
        REVENUE.get_query_text(None),
        _grid(experiment),
        input_kind="sample",
    )
    assert panel.n_units == 320  # 160 per arm pooled into one universe
    assert panel.covariate is None  # t-test declares no covariate_lookback
    assert panel.cutoffs[-1].is_horizon
    assert panel.cutoffs[-1].unit_idx.size == 320  # horizon holds every unit

    score = score_cell(
        panel, create_method("t-test", alpha=0.05), iterations=1500, seed_parts=("aa", "arpu", "c")
    )
    assert score.fpr is not None
    assert abs(score.fpr - 0.05) < _band(0.05, 1500)


def test_fraction_panel_uses_count_and_nobs_roles():
    # Structural: the loader pools the count/nobs roles (not a per-unit 'value').
    # (Calibration lives in WP1 on clean panels; this clustered conversion metric,
    # nobs>1 per unit, legitimately INFLATES a naive z-test — the WP7 worked example.)
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=160)
    seed_null_events(warehouse)
    experiment = make_experiment("aa_conv", "conversion", {"name": "z-test"})
    backend = RecomputeBackend(warehouse, experiment)

    panel = load_placebo_panel(
        backend,
        experiment.comparisons[0],
        CONVERSION,
        CONVERSION.get_query_text(None),
        _grid(experiment),
        input_kind="fraction",
    )
    assert panel.n_units == 320
    assert panel.cutoffs[-1].secondary is not None  # per-unit trials (nobs)
    assert panel.cutoffs[-1].secondary.sum() > panel.cutoffs[-1].values.sum()  # trials > successes
    score = score_cell(
        panel, create_method("z-test", alpha=0.05), iterations=800, seed_parts=("aa", "conv", "c")
    )
    assert score.fpr is not None  # the scorer runs the fraction path end-to-end


def test_ratio_panel_carries_denominator():
    # Structural: the loader pools numerator/denominator into the panel.
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=160)
    seed_null_events(warehouse)
    experiment = make_experiment("aa_ctr", "ctr", {"name": "ratio-delta"})
    backend = RecomputeBackend(warehouse, experiment)

    panel = load_placebo_panel(
        backend,
        experiment.comparisons[0],
        CTR,
        CTR.get_query_text(None),
        _grid(experiment),
        input_kind="ratio",
    )
    assert panel.n_units == 320
    assert panel.cutoffs[-1].secondary is not None  # per-unit denominator (views)
    # the scorer completes without raising and honestly reports the degenerate fixture
    # (this CTR fixture sums to a constant ratio over a 4-day window) as gaps
    score = score_cell(
        panel,
        create_method("ratio-delta", alpha=0.05),
        iterations=200,
        seed_parts=("aa", "ctr", "c"),
    )
    assert score.degenerate_horizon > 0
    assert score.achieved_mde is None  # ratio-delta has no analytic MDE


def test_cuped_panel_loads_covariate():
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=160)
    seed_null_events(warehouse)
    experiment = make_experiment(
        "aa_cuped", "arpu", {"name": "cuped-t-test", "params": {"covariate_lookback": "7d"}}
    )
    backend = RecomputeBackend(warehouse, experiment)

    panel = load_placebo_panel(
        backend,
        experiment.comparisons[0],
        REVENUE,
        REVENUE.get_query_text(None),
        _grid(experiment),
        input_kind="sample",
    )
    assert panel.covariate is not None  # the CUPED pre-period render is joined on
    assert panel.covariate.shape == (320,)
    score = score_cell(
        panel,
        create_method("cuped-t-test", alpha=0.05),
        iterations=1000,
        seed_parts=("aa", "cuped", "c"),
    )
    assert score.fpr is not None
    assert abs(score.fpr - 0.05) < _band(0.05, 1000) + 0.015


class TestThePlaceboSizesTheCalibratedContrast:
    """m14 DEC-5(a): the panel pools the CONTRAST, not every arm.

    Pooling all arms calibrated a design nobody runs: at three even arms the
    placebo splits 1/3 vs 2/3 over three arms' units while the live
    control-vs-treatment comparison is 1/2 vs 1/2 over two arms'. The FPR column
    is robust to that; achieved-MDE is read off per-arm n and feeds the
    Recommended row, so it came out optimistic by ≈√1.5.
    """

    @staticmethod
    def _three_arm_warehouse(n_per_arm: int = 120):
        warehouse = SyntheticWarehouse()
        for i in range(n_per_arm):
            warehouse.cohort.append((f"c{i:03d}", "control", START + timedelta(hours=1)))
            warehouse.cohort.append((f"t{i:03d}", "treatment", START + timedelta(hours=1)))
            warehouse.cohort.append((f"u{i:03d}", "treatment_b", START + timedelta(hours=1)))
        seed_null_events(warehouse)
        return warehouse

    @staticmethod
    def _three_arm_experiment() -> ExperimentConfig:
        payload = experiment_payload("aa_three", "arpu", {"name": "t-test"})
        payload["assignment"]["variants"] = ["control", "treatment", "treatment_b"]
        payload["assignment"]["expected_split"] = {
            "control": 1 / 3,
            "treatment": 1 / 3,
            "treatment_b": 1 / 3,
        }
        return ExperimentConfig.model_validate(payload)

    def _panel(self, arms):
        warehouse = self._three_arm_warehouse()
        experiment = self._three_arm_experiment()
        return load_placebo_panel(
            RecomputeBackend(warehouse, experiment),
            experiment.comparisons[0],
            REVENUE,
            REVENUE.get_query_text(None),
            _grid(experiment),
            input_kind="sample",
            arms=arms,
        )

    def test_a_third_arms_units_stay_out_of_the_pool(self):
        assert self._panel(("control", "treatment")).n_units == 240
        # what `0.8.0` did, and the reason the achieved MDE was optimistic
        assert self._panel(None).n_units == 360

    def test_the_pool_is_the_pair_whichever_pair_it_is(self):
        assert self._panel(("control", "treatment_b")).n_units == 240
        assert self._panel(("treatment", "treatment_b")).n_units == 240

    def test_a_two_arm_panel_is_unchanged_by_the_filter(self):
        """The WP's №1 assertion at the loader: naming both arms of a two-arm
        experiment is the same pool, in the same concatenation order, as naming
        none — the filter preserves `variants()`' order rather than following
        the argument."""
        warehouse = SyntheticWarehouse()
        seed_cohort(warehouse, n_per_arm=160)
        seed_null_events(warehouse)
        experiment = make_experiment("aa_two", "arpu", {"name": "t-test"})
        backend = RecomputeBackend(warehouse, experiment)
        common = {
            "comparison": experiment.comparisons[0],
            "metric": REVENUE,
            "metric_sql": REVENUE.get_query_text(None),
            "grid": _grid(experiment),
            "input_kind": "sample",
        }

        unfiltered = load_placebo_panel(backend, **common)
        # deliberately reversed, to prove the ORDER comes from the load and not
        # from the argument — a reordered pool would move every seeded draw
        filtered = load_placebo_panel(backend, **common, arms=("treatment", "control"))

        assert filtered.n_units == unfiltered.n_units
        for left, right in zip(filtered.cutoffs, unfiltered.cutoffs, strict=True):
            np.testing.assert_array_equal(left.unit_idx, right.unit_idx)
            np.testing.assert_array_equal(left.values, right.values)
