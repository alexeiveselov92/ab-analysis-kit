"""WP2 load stage: pooled panels from the real loaders + the denser-early subsampler.

Exercises ``load_placebo_panel`` end-to-end against ``SyntheticWarehouse`` (a null
twin gives an analytic FPR ≈ α), plus the grid subsampler in isolation.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from synthetic_ab import (
    CONVERSION,
    CTR,
    REVENUE,
    SyntheticWarehouse,
    make_experiment,
    seed_cohort,
    seed_null_events,
)

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.core.period_planner import Cutoff, generate_grid
from abkit.stats.factory import create_method
from abkit.validate.load import load_placebo_panel, subsample_grid
from abkit.validate.scoring import score_cell


def _grid(experiment):
    return generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        tz=experiment.timezone,
    )


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
