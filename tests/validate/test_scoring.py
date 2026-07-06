"""The A/A scorer: FPR ≈ α, peeking inflation, power/coverage, determinism (m4 D2/D3/D16).

FPR/power asserts use a Binomial(N, p) 3σ band around the analytic truth, never a
point value (aa-false-positive-matrix.md; m4 WP7 gate discipline).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from abkit.stats.factory import create_method
from abkit.validate.panel import PanelCutoff, PlaceboPanel
from abkit.validate.scoring import _cell_tau2, score_cell
from tests.validate._panels import fraction_panel, normal_panel

ALPHA = 0.05
ITERS = 2000
SEED_PARTS = ("aa", "exp", "revenue", "cfg-abc")


def _band(p: float, n: int, sigmas: float = 3.0) -> float:
    return sigmas * math.sqrt(p * (1.0 - p) / n)


def test_single_look_fpr_is_near_nominal_alpha():
    # one cutoff -> only the single-look FPR is exercised (peeking needs >=3 cutoffs)
    panel = normal_panel(n_units=4000, n_cutoffs=1, seed=11)
    method = create_method("t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=ITERS, seed_parts=SEED_PARTS)

    assert score.valid_iterations == ITERS
    assert score.fpr is not None
    assert abs(score.fpr - ALPHA) < _band(ALPHA, ITERS)
    # one cutoff -> the only look IS the horizon, so peeking == single-look
    assert score.peeking_fpr == pytest.approx(score.fpr)
    assert score.power is None and score.coverage is None  # no injection


def test_ztest_on_proportion_is_calibrated():
    panel = fraction_panel(n_units=6000, seed=21, base_rate=0.2)
    method = create_method("z-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=ITERS, seed_parts=SEED_PARTS)
    assert score.fpr is not None
    assert abs(score.fpr - ALPHA) < _band(ALPHA, ITERS)


def test_peeking_fpr_inflates_above_single_look():
    # many cumulative cutoffs -> optional stopping inflates the false-winner rate
    panel = normal_panel(n_units=2500, n_cutoffs=20, seed=31)
    method = create_method("t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=ITERS, seed_parts=SEED_PARTS)

    assert score.fpr is not None and score.peeking_fpr is not None
    # the honest jump: peeking across the grid is strictly worse than a single look
    # (the horizon look is included, so peeking >= single-look by construction)
    assert score.peeking_fpr > score.fpr
    assert score.peeking_fpr > 2 * ALPHA  # the peeking hazard the column warns about
    # winner's curse: iterations that falsely stop early carry a non-zero |effect|
    assert score.effect_exaggeration is not None and score.effect_exaggeration > 0.0


def test_peeking_curve_is_monotone_and_ends_at_peeking_fpr():
    panel = normal_panel(n_units=2500, n_cutoffs=20, seed=31)
    method = create_method("t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=ITERS, seed_parts=SEED_PARTS)

    curve = score.peeking_curve
    assert len(curve) == len(panel.cutoffs)  # one (elapsed_days, cumulative_fpr) per look
    # cumulative FPR is monotone non-decreasing (optional-stopping accrues, never undoes)
    ys = [y for _x, y in curve]
    assert all(b >= a - 1e-12 for a, b in zip(ys, ys[1:], strict=False))
    xs = [x for x, _y in curve]
    assert all(b >= a for a, b in zip(xs, xs[1:], strict=False))  # ordered by elapsed time
    # the final look equals the reported cumulative peeking FPR
    assert curve[-1][1] == pytest.approx(score.peeking_fpr)
    # and the curve's terminus exceeds its first look — the honest peeking climb
    assert curve[-1][1] >= curve[0][1]


def test_peeking_curve_empty_when_unscorable():
    panel = normal_panel(n_units=3, n_cutoffs=4, seed=71)  # too small — no usable horizon
    method = create_method("t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=30, seed_parts=SEED_PARTS)
    if score.valid_iterations == 0:
        assert score.peeking_curve == ()


def test_injected_effect_gives_power_and_calibrated_coverage():
    panel = normal_panel(n_units=4000, n_cutoffs=1, seed=41)
    method = create_method("t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=ITERS, seed_parts=SEED_PARTS, inject_effect=0.15)

    assert score.power is not None and score.power > 0.5  # a real effect is detected
    assert score.coverage is not None
    # a well-calibrated CI covers the truth at ~1-alpha
    assert abs(score.coverage - (1.0 - ALPHA)) < _band(1.0 - ALPHA, ITERS) + 0.02
    assert score.achieved_mde is not None and score.achieved_mde > 0.0


def test_scoring_is_byte_reproducible():
    panel = normal_panel(n_units=1500, n_cutoffs=6, seed=51)
    method = create_method("t-test", alpha=ALPHA)
    a = score_cell(panel, method, iterations=500, seed_parts=SEED_PARTS)
    b = score_cell(panel, method, iterations=500, seed_parts=SEED_PARTS)
    assert a == b  # identical seeds -> identical CellScore


def test_cuped_path_scores_and_reports_mde():
    panel = normal_panel(n_units=3000, n_cutoffs=1, seed=61, with_covariate=True)
    method = create_method("cuped-t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=ITERS, seed_parts=SEED_PARTS)
    assert score.fpr is not None
    assert abs(score.fpr - ALPHA) < _band(ALPHA, ITERS)
    assert score.achieved_mde is not None  # CUPED MDE uses the covariate-deflated std


def test_degenerate_horizon_is_counted_not_a_rejection():
    panel = normal_panel(n_units=3, n_cutoffs=1, seed=71)  # only 3 units -> an arm has < 2
    method = create_method("t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=50, seed_parts=SEED_PARTS)
    assert score.valid_iterations < 50  # most/all iterations degenerate at the horizon
    assert score.degenerate_horizon > 0
    assert score.fpr is None or score.fpr == 0.0  # never inflated by counting gaps


def test_absolute_test_type_coverage_is_calibrated():
    """m4 exit-gate F2: the absolute-effect truth (δ·μ̂) anchors on the FIXED pooled
    horizon mean, not the realized control mean — which co-varies with the effect
    estimate and biases coverage ~2pp low. A well-calibrated CI must cover at ~1−α."""
    panel = normal_panel(n_units=4000, n_cutoffs=1, seed=43)
    method = create_method("t-test", alpha=ALPHA, params={"test_type": "absolute"})
    score = score_cell(panel, method, iterations=4000, seed_parts=SEED_PARTS, inject_effect=0.2)
    assert score.coverage is not None
    # the noisy value_1 anchor gives ~0.936 here; the fixed pooled anchor restores ~0.958
    assert 0.945 < score.coverage < 0.97


def test_zero_trial_fraction_arm_is_a_gap_not_a_crash():
    """m4 exit-gate F3: a fraction cutoff whose present units all have 0 trials is a
    degenerate gap (build_arm -> None), never a Fraction(nobs=0) SampleValidationError
    that would escape per-cell isolation and abort the whole experiment."""
    n = 120
    unit_idx = np.arange(n)
    cutoff = PanelCutoff(
        elapsed_days=1.0,
        is_horizon=True,
        unit_idx=unit_idx,
        values=np.zeros(n),
        secondary=np.zeros(n),  # every present unit has 0 trials
    )
    panel = PlaceboPanel(
        n_units=n,
        cutoffs=(cutoff,),
        covariate=None,
        input_kind="fraction",
        kept_grid_points=1,
        total_grid_points=1,
    )
    method = create_method("z-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=50, seed_parts=SEED_PARTS)  # must NOT raise
    assert score.valid_iterations == 0  # every cutoff is a gap
    assert score.degenerate_horizon > 0
    assert score.fpr is None


def test_sequential_peeking_fpr_returns_to_near_alpha():
    """The D8 headline: where the fixed peeking FPR breaks budget across a 20-look grid,
    the always-valid peeking FPR is brought back to ~alpha (the honest completion)."""
    panel = normal_panel(n_units=2500, n_cutoffs=20, seed=31)
    method = create_method("t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=ITERS, seed_parts=SEED_PARTS)

    assert score.tau2 is not None and score.tau2 > 0.0
    assert score.peeking_fpr is not None and score.peeking_fpr_sequential is not None
    # fixed peeking is inflated; the always-valid twin is strictly better and controlled
    assert score.peeking_fpr > 2 * ALPHA
    assert score.peeking_fpr_sequential < score.peeking_fpr
    assert score.peeking_fpr_sequential <= ALPHA + _band(ALPHA, ITERS)  # ~controlled at alpha
    # the sequential curve mirrors the fixed one's shape (one point per look, monotone)
    curve = score.peeking_curve_sequential
    assert len(curve) == len(panel.cutoffs)
    ys = [y for _x, y in curve]
    assert all(b >= a - 1e-12 for a, b in zip(ys, ys[1:], strict=False))
    assert curve[-1][1] == pytest.approx(score.peeking_fpr_sequential)


def test_sequential_single_look_fpr_and_width_side_by_side():
    """The always-valid CI is wider than the fixed CI (the anytime price), so its
    single-look FPR is <= the fixed single-look FPR at the same horizon."""
    panel = normal_panel(n_units=4000, n_cutoffs=1, seed=11)
    method = create_method("t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=ITERS, seed_parts=SEED_PARTS)

    assert score.fpr is not None and score.fpr_sequential is not None
    assert score.fpr_sequential <= score.fpr + 1e-12  # wider CI rejects no more often
    assert score.ci_width is not None and score.ci_width_sequential is not None
    assert score.ci_width_sequential > score.ci_width  # strictly wider (the anytime price)
    assert 1.3 < score.ci_width_sequential / score.ci_width < 1.7  # ~1.55x at the horizon
    # one look ⇒ peeking == single-look on both columns
    assert score.peeking_fpr_sequential == pytest.approx(score.fpr_sequential)


def test_sequential_power_stays_materially_above_alpha():
    """Guard against a τ² that 'fixes' FPR by never rejecting: on a real injected
    effect the always-valid CI still detects it (power well above α, but <= fixed)."""
    panel = normal_panel(n_units=4000, n_cutoffs=1, seed=41)
    method = create_method("t-test", alpha=ALPHA)
    score = score_cell(panel, method, iterations=ITERS, seed_parts=SEED_PARTS, inject_effect=0.15)

    assert score.power is not None and score.power_sequential is not None
    assert score.power_sequential > 3 * ALPHA  # materially above alpha — not a dead test
    assert score.power_sequential <= score.power + 1e-12  # wider CI ⇒ no more power
    assert score.coverage_sequential is not None
    assert score.coverage_sequential >= score.coverage  # wider CI covers at least as often


def test_bootstrap_method_is_sequential_ineligible():
    """supports_sequential=False (asymmetric percentile CI) ⇒ τ² is not anchored, so no
    always-valid column. Tested at the anchor helper: score_cell's closed-form path does
    not run bootstrap (no from_suffstats), so the guard short-circuits before any call."""
    method = create_method("bootstrap", alpha=ALPHA, params={"n_samples": 200, "seed": 7})
    assert method.supports_sequential is False
    panel = normal_panel(n_units=1500, n_cutoffs=4, seed=51)
    tau2 = _cell_tau2(panel, method, horizon_pos=len(panel.cutoffs) - 1, share_a=0.5, anchor_seed=1)
    assert tau2 is None  # ineligible → no column, and from_suffstats was never invoked


def test_ratio_delta_absolute_coverage_is_calibrated():
    """m4 exit-gate round-2: ratio-delta DOES expose test_type=absolute, so its truth
    must anchor on the FIXED pooled ratio (mean_num/mean_den) — not the noisy realized
    control ratio value_1, which the round-1 F2 fix missed for the ratio kind."""
    rng = np.random.default_rng(7)
    n = 4000
    den = rng.uniform(5.0, 15.0, size=n)
    num = den * 0.3 + rng.normal(0.0, 1.0, size=n)  # per-unit ratio ≈ 0.3
    unit_idx = np.arange(n)
    cutoff = PanelCutoff(
        elapsed_days=14.0, is_horizon=True, unit_idx=unit_idx, values=num, secondary=den
    )
    panel = PlaceboPanel(
        n_units=n,
        cutoffs=(cutoff,),
        covariate=None,
        input_kind="ratio",
        kept_grid_points=1,
        total_grid_points=1,
    )
    method = create_method("ratio-delta", alpha=ALPHA, params={"test_type": "absolute"})
    score = score_cell(panel, method, iterations=4000, seed_parts=SEED_PARTS, inject_effect=0.15)
    assert score.coverage is not None
    # the value_1 anchor biases this to ~0.93; the fixed pooled-ratio anchor restores ~0.95
    assert 0.94 < score.coverage < 0.965
