"""The A/A scorer: FPR ≈ α, peeking inflation, power/coverage, determinism (m4 D2/D3/D16).

FPR/power asserts use a Binomial(N, p) 3σ band around the analytic truth, never a
point value (aa-false-positive-matrix.md; m4 WP7 gate discipline).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from abkit.stats.base import BaseMethod
from abkit.stats.factory import create_method
from abkit.stats.parametric.ttest import TTest
from abkit.validate._types import ValidateError
from abkit.validate.panel import PanelCutoff, PlaceboPanel
from abkit.validate.scoring import _cell_tau2, _score_cell_scalar, score_cell
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
    tau2 = _cell_tau2(panel, method, share_a=0.5, anchor_seed=1)
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


# ── M7 WP4: engine dispatch + vectorized/scalar smoke parity ──────────────────
# The exhaustive many-seed parity gate is WP5 (test_vector_parity.py); these pin
# the dispatch contract, the argmax footgun, and one smoke-parity pass per kind.


class _ScalarOnlyTTest(TTest):
    """The registered t-test math with the batch-kernel opt-in turned off —
    the design's stand-in for any future/custom plugin that only implements
    ``from_suffstats`` (m7-implementation-plan.md §WP4 step 8)."""

    supports_vectorized = False


def _ratio_panel(*, n_units: int, n_cutoffs: int, seed: int) -> PlaceboPanel:
    """A cumulative multi-cutoff ratio panel (per-unit ratio ≈ 0.3) for parity."""
    rng = np.random.default_rng(seed)
    den_inc = rng.uniform(1.0, 3.0, size=(n_units, n_cutoffs))
    num_inc = den_inc * 0.3 + rng.normal(0.0, 0.5, size=(n_units, n_cutoffs))
    den = np.cumsum(den_inc, axis=1)
    num = np.cumsum(num_inc, axis=1)
    unit_idx = np.arange(n_units)
    cutoffs = tuple(
        PanelCutoff(
            elapsed_days=float(k + 1),
            is_horizon=(k == n_cutoffs - 1),
            unit_idx=unit_idx,
            values=num[:, k].copy(),
            secondary=den[:, k].copy(),
        )
        for k in range(n_cutoffs)
    )
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=cutoffs,
        covariate=None,
        input_kind="ratio",
        kept_grid_points=n_cutoffs,
        total_grid_points=n_cutoffs,
    )


#: Integer tallies — must be EXACTLY equal between the engines.
_COUNT_FIELDS = (
    "iterations",
    "valid_iterations",
    "degenerate_horizon",
    "kept_grid_points",
    "total_grid_points",
)
#: Ratios of integer tallies (+ passthroughs + the shared scalar τ² helper) —
#: identical floats when the underlying counts match.
_EXACT_FIELDS = (
    "fpr",
    "peeking_fpr",
    "power",
    "coverage",
    "fpr_sequential",
    "peeking_fpr_sequential",
    "power_sequential",
    "coverage_sequential",
    "injected_effect",
    "tau2",
)
#: Continuous means — the batch path's GEMM/pairwise reduction order differs from
#: the scalar accumulation, so rel-1e-9 (the WP3 conditioning-band contract).
_CONTINUOUS_FIELDS = (
    "achieved_mde",
    "effect_exaggeration",
    "effect_exaggeration_sequential",
    "ci_width",
    "ci_width_sequential",
)


def _assert_smoke_parity(vec, sca):
    for field in _COUNT_FIELDS:
        assert getattr(vec, field) == getattr(sca, field), field
    for field in _EXACT_FIELDS:
        assert getattr(vec, field) == getattr(sca, field), field
    for field in _CONTINUOUS_FIELDS:
        v, s = getattr(vec, field), getattr(sca, field)
        assert (v is None) == (s is None), field
        if v is not None:
            assert v == pytest.approx(s, rel=1e-9), field
    # curve y-values are ratios of the (exactly-matching) crossing histograms
    assert vec.peeking_curve == sca.peeking_curve
    assert vec.peeking_curve_sequential == sca.peeking_curve_sequential
    assert vec.warnings == sca.warnings


_PARITY_CASES = ("sample", "cuped", "fraction", "ratio", "absolute")


@pytest.mark.parametrize("inject", [None, 0.1])
@pytest.mark.parametrize("case", _PARITY_CASES)
def test_vectorized_engine_smoke_parity_vs_scalar(case, inject):
    """One smoke-parity pass per input kind (± injection) at a loose alpha so the
    peeking / exaggeration / sequential branches all carry real crossings."""
    if case == "sample":
        panel = normal_panel(n_units=400, n_cutoffs=5, seed=91)
        method = create_method("t-test", alpha=0.2)
    elif case == "cuped":
        panel = normal_panel(n_units=400, n_cutoffs=5, seed=92, with_covariate=True)
        method = create_method("cuped-t-test", alpha=0.2)
    elif case == "fraction":
        panel = fraction_panel(n_units=800, seed=93, base_rate=0.3)
        method = create_method("z-test", alpha=0.2)
    elif case == "ratio":
        panel = _ratio_panel(n_units=400, n_cutoffs=5, seed=94)
        method = create_method("ratio-delta", alpha=0.2)
    else:  # the absolute-effect truth anchor (δ·μ̂ pooled) path
        panel = normal_panel(n_units=400, n_cutoffs=5, seed=95)
        method = create_method("t-test", alpha=0.2, params={"test_type": "absolute"})

    kwargs = {"iterations": 150, "seed_parts": SEED_PARTS, "inject_effect": inject}
    vec = score_cell(panel, method, **kwargs)
    sca = _score_cell_scalar(panel, method, **kwargs)
    _assert_smoke_parity(vec, sca)


def test_never_crossing_iterations_are_not_counted_at_look_zero():
    """The argmax-on-all-False footgun (§WP4 step 4): an iteration that never
    crosses significance across the whole grid must not be recorded as a look-0
    crossing — a naive ``argmax(sig, axis=1)`` without the any() guard would
    report peeking_fpr == 1.0 here."""
    panel = normal_panel(n_units=2000, n_cutoffs=6, seed=96)
    method = create_method("t-test", alpha=1e-9)  # a ~6σ CI — no null split crosses
    score = score_cell(panel, method, iterations=200, seed_parts=SEED_PARTS)
    assert score.valid_iterations == 200
    assert score.peeking_fpr == 0.0
    assert score.effect_exaggeration is None  # nobody crossed — no winner's curse
    assert all(y == 0.0 for _x, y in score.peeking_curve)


def test_dispatch_vectorized_method_never_enters_scalar_engine(monkeypatch):
    panel = normal_panel(n_units=300, n_cutoffs=2, seed=97)
    method = create_method("t-test", alpha=ALPHA)

    def _boom(*args, **kwargs):
        raise AssertionError("the scalar engine must not run for a vectorized method")

    monkeypatch.setattr("abkit.validate.scoring._score_cell_scalar", _boom)
    score = score_cell(panel, method, iterations=20, seed_parts=SEED_PARTS)
    assert score.valid_iterations == 20


def test_scalar_fallback_stub_is_the_verbatim_scalar_engine():
    """supports_vectorized=False routes through ``_score_cell_scalar`` unchanged:
    the dispatch result is identical to calling the scalar engine directly, and to
    the registered t-test's scalar run (the opt-out flips no math)."""
    stub = _ScalarOnlyTTest(alpha=ALPHA)
    assert stub.supports_vectorized is False
    panel = normal_panel(n_units=400, n_cutoffs=3, seed=98)

    via_dispatch = score_cell(panel, stub, iterations=60, seed_parts=SEED_PARTS)
    direct = _score_cell_scalar(panel, stub, iterations=60, seed_parts=SEED_PARTS)
    assert via_dispatch == direct

    registered_scalar = _score_cell_scalar(
        panel, create_method("t-test", alpha=ALPHA), iterations=60, seed_parts=SEED_PARTS
    )
    assert via_dispatch == registered_scalar


def test_dispatch_fallback_method_never_enters_vectorized_engine(monkeypatch):
    """The reverse routing pin: an opted-out method must never reach the batch
    engine (the dispatch-equality assert alone is tautological through the
    dispatcher — adversarial review round 1)."""
    panel = normal_panel(n_units=300, n_cutoffs=2, seed=97)
    stub = _ScalarOnlyTTest(alpha=ALPHA)

    def _boom(*args, **kwargs):
        raise AssertionError("the vectorized engine must not run for a fallback method")

    monkeypatch.setattr("abkit.validate.scoring._score_cell_vectorized", _boom)
    score = score_cell(panel, stub, iterations=20, seed_parts=SEED_PARTS)
    assert score.valid_iterations == 20


def test_non_hoisted_prepare_branch_is_bit_identical(monkeypatch):
    """Past the hoist budget every block re-prepares its cutoffs inline — a pure
    memory policy that must not move a single bit (the prepare_cutoff contract;
    coverage gap flagged by adversarial review round 1)."""
    panel = normal_panel(n_units=400, n_cutoffs=4, seed=90)
    method = create_method("t-test", alpha=0.2)
    kwargs = {"iterations": 60, "seed_parts": SEED_PARTS, "inject_effect": 0.1}
    default = score_cell(panel, method, **kwargs)
    # scoring's budget only gates the hoist; block_rows keeps its own constant,
    # so the blocking (and therefore every float) must stay identical
    monkeypatch.setattr("abkit.validate.scoring.DEFAULT_MAX_BLOCK_BYTES", 0)
    forced = score_cell(panel, method, **kwargs)
    assert forced == default

    # Multi-block leg (adversarial review round 2): a tiny quantum ⇒ 9 blocks ×
    # 4 cutoffs, exercising fresh per-block re-prepares against the reused
    # weights scratch. A changed partition may move floats at ULP scale (the
    # vector_resample block contract), so this pair is compared to ITSELF —
    # hoisted vs non-hoisted under the SAME partition must be bit-identical —
    # and its counts to the partition-independent scalar engine.
    monkeypatch.setattr("abkit.validate.scoring.block_rows", lambda n_units, *a, **kw: 7)
    forced_multi = score_cell(panel, method, **kwargs)  # budget 0 → re-prepare per block
    monkeypatch.setattr("abkit.validate.scoring.DEFAULT_MAX_BLOCK_BYTES", 1 << 40)
    hoisted_multi = score_cell(panel, method, **kwargs)  # same partition, hoisted
    assert forced_multi == hoisted_multi
    sca = _score_cell_scalar(panel, method, **kwargs)
    assert forced_multi.valid_iterations == sca.valid_iterations
    assert forced_multi.fpr == sca.fpr
    assert forced_multi.peeking_fpr == sca.peeking_fpr
    assert forced_multi.peeking_curve == sca.peeking_curve


def test_lying_vectorized_flag_fails_the_cell_loudly():
    """supports_vectorized=True without a working batch kernel must raise
    ValidateError (the runner's per-cell isolation catches it and fails only
    that row) — never an uncaught NotImplementedError that would abort the
    whole matrix (adversarial review round 1)."""

    class _KernellessTTest(TTest):
        # revert the override → the raising BaseMethod default, flag still True
        from_suffstats_array = BaseMethod.from_suffstats_array

    method = _KernellessTTest(alpha=ALPHA)
    assert method.supports_vectorized is True
    panel = normal_panel(n_units=300, n_cutoffs=2, seed=89)
    with pytest.raises(ValidateError, match="supports_vectorized=True"):
        score_cell(panel, method, iterations=10, seed_parts=SEED_PARTS)


def test_empty_early_cutoff_is_a_silent_gap_in_both_engines():
    """A non-horizon cutoff with ZERO present units (load.py only guards the
    horizon) must flow through the hoist as a skipped entry: no stray
    'Mean of empty slice' RuntimeWarning out of prepare_cutoff, identical gap
    tallies in both engines (adversarial review round 2)."""
    n = 300
    rng = np.random.default_rng(17)
    unit_idx = np.arange(n)
    empty = PanelCutoff(
        elapsed_days=1.0,
        is_horizon=False,
        unit_idx=np.array([], dtype=np.int64),
        values=np.array([], dtype=np.float64),
    )
    later = tuple(
        PanelCutoff(
            elapsed_days=float(k + 2),
            is_horizon=(k == 1),
            unit_idx=unit_idx,
            values=rng.normal(10.0, 3.0, size=n),
        )
        for k in range(2)
    )
    panel = PlaceboPanel(
        n_units=n,
        cutoffs=(empty, *later),
        covariate=None,
        input_kind="sample",
        kept_grid_points=3,
        total_grid_points=3,
    )
    method = create_method("t-test", alpha=0.2)
    kwargs = {"iterations": 40, "seed_parts": SEED_PARTS, "inject_effect": 0.1}
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("error", RuntimeWarning)  # any stray warning fails
        vec = score_cell(panel, method, **kwargs)
    sca = _score_cell_scalar(panel, method, **kwargs)
    _assert_smoke_parity(vec, sca)
    assert vec.valid_iterations > 0  # the empty look is a gap, not a poisoned cell


def test_saturating_injection_warns_once_and_stays_in_parity():
    """A δ that saturates the proportion arm (count > nobs): the batch clamp +
    the one-shot warning mirror the scalar path exactly (inject.py columns seam)."""
    panel = fraction_panel(n_units=600, seed=99, base_rate=0.9)
    method = create_method("z-test", alpha=0.2)
    kwargs = {"iterations": 80, "seed_parts": SEED_PARTS, "inject_effect": 0.5}
    vec = score_cell(panel, method, **kwargs)
    sca = _score_cell_scalar(panel, method, **kwargs)
    assert sum("saturated" in w for w in vec.warnings) == 1  # warned once, not per row
    _assert_smoke_parity(vec, sca)


def test_zero_pooled_denominator_falls_back_not_crashes():
    """An exactly-zero pooled ratio denominator at the horizon used to raise an
    uncaught ZeroDivisionError out of _point_estimate (pre-existing, shared by
    both engines; the runner's per-cell isolation does NOT catch it → the whole
    matrix aborted). Now it falls back to the per-row value_1 anchor as the
    docstring always promised — and the two engines stay in parity through the
    fallback path (adversarial review round 1)."""
    n = 200
    unit_idx = np.arange(n)
    den = np.tile([1.0, -1.0], n // 2)  # pooled sum EXACTLY 0.0 (integer-valued floats)
    rng = np.random.default_rng(13)
    num = rng.normal(0.5, 0.2, size=n)
    cutoff = PanelCutoff(
        elapsed_days=7.0, is_horizon=True, unit_idx=unit_idx, values=num, secondary=den
    )
    panel = PlaceboPanel(
        n_units=n,
        cutoffs=(cutoff,),
        covariate=None,
        input_kind="ratio",
        kept_grid_points=1,
        total_grid_points=1,
    )
    method = create_method("ratio-delta", alpha=0.2, params={"test_type": "absolute"})
    kwargs = {"iterations": 60, "seed_parts": SEED_PARTS, "inject_effect": 0.15}
    vec = score_cell(panel, method, **kwargs)  # must NOT raise
    sca = _score_cell_scalar(panel, method, **kwargs)  # must NOT raise
    _assert_smoke_parity(vec, sca)
    # splits are generally denominator-imbalanced ⇒ some rows scored through the
    # per-row value_1 anchor (the fallback is genuinely exercised, not skipped)
    assert vec.valid_iterations > 0


def test_value_1_rows_mirror_each_methods_value_1():
    """The per-row truth-anchor fallback replicates each method's own value_1:
    raw control mean (t/CUPED), proportion (z-test), H5-guarded ratio (ratio-delta
    → NaN on a zero/non-finite denominator mean, never a ZeroDivisionError)."""
    from abkit.validate.scoring import _value_1_rows

    sample_cols = {"mean": np.array([1.5, -2.0])}
    np.testing.assert_array_equal(_value_1_rows("sample", sample_cols), [1.5, -2.0])

    fraction_cols = {"count": np.array([3.0, 0.0]), "nobs": np.array([10.0, 5.0])}
    np.testing.assert_array_equal(_value_1_rows("fraction", fraction_cols), [0.3, 0.0])

    ratio_cols = {
        "mean_num": np.array([2.0, 1.0, 1.0]),
        "mean_den": np.array([4.0, 0.0, np.inf]),
    }
    out = _value_1_rows("ratio", ratio_cols)
    assert out[0] == 0.5
    assert np.isnan(out[1]) and np.isnan(out[2])  # the _arm_linearisation H5 rule
