"""The A/A scorer: FPR ≈ α, peeking inflation, power/coverage, determinism (m4 D2/D3/D16).

FPR/power asserts use a Binomial(N, p) 3σ band around the analytic truth, never a
point value (aa-false-positive-matrix.md; m4 WP7 gate discipline).
"""

from __future__ import annotations

import math

import pytest

from abkit.stats.factory import create_method
from abkit.validate.scoring import score_cell
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
    assert all(b >= a - 1e-12 for a, b in zip(ys, ys[1:]))
    xs = [x for x, _y in curve]
    assert all(b >= a for a, b in zip(xs, xs[1:]))  # looks are ordered by elapsed time
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
