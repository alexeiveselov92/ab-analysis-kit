"""M7 WP5 — the exhaustive scalar ↔ vectorized ``score_cell`` parity gate.

The milestone's numeric-safety claim rests on THIS suite
(docs/specs/m7-implementation-plan.md §WP5): the e2e golden tests round to .1 %
and would never catch a single boundary-decision flip, so empirical breadth here
substitutes for the formal proof that does not exist (§0.3(3) — count exactness
is an observation, not a theorem).

The contract (the ``vector_resample`` module docstring; smoke-pinned per kind in
``test_scoring.py``, exhaustively gated here):

- **integer count fields agree EXACTLY** — ``valid_iterations``,
  ``degenerate_horizon`` and every ratio-of-counts column (fpr / peeking /
  power / coverage, fixed AND sequential), both peeking curves, and
  ``achieved_mde`` (bit-identical by construction since the R1 MDE-seam fix);
- **continuous mean fields agree at rel-1e-9** — the batch path aggregates via
  GEMM / pairwise reductions whose rounding order differs from the scalar
  ``.sum()`` accumulation (the WP3 conditioning-band finding);
- both engines run **in one process** ⇒ one BLAS thread configuration, so the
  exact-count assertion is CI-safe (the D13 byte-repro scope is "under a fixed
  BLAS configuration"; a runner with a different OpenBLAS build/thread count
  re-rounds continuous columns ~1e-15 rel — far inside rel-1e-9 — while the
  counts asserted exact here compare scalar-vs-vector *within* the process).

Battery breadth — 8 shapes: every validate-reachable ``supports_vectorized``
kernel — ``sample`` (t-test), CUPED covariate, absolute ``test_type`` (the
pooled-anchor truth path), ``fraction`` (z-test), ``ratio`` (ratio-delta) —
plus three adversarially-chosen stress shapes: gap-heavy ``sparse`` (SOME
splits degenerate and some don't — "gaps, never zeros" under partial
degeneracy), ``cuped_sparse`` (CUPED at the ``MIN_ARM_UNITS`` floor — the
corr≡±1 knife-edge that caught the R1 MDE divergence), and ``clamp``
(saturating fraction injection — the clamp-warning path across the battery).
Rare-but-reachable states the battery alone would undersample are pinned by
scanned deterministic seeds: the τ²-unanchorable cell and the
no-valid-horizon cell (R1 round-1 findings). ``paired-t-test`` carries a WP2
batch kernel but no validate ``input_kind`` reaches it (``score_cell`` builds
sample/fraction/ratio arms only), so its parity is pinned at the kernel level
in ``tests/stats/test_vectorized_parity.py``, not here.

Seed breadth is env-tunable: ``ABKIT_PARITY_SEEDS`` (default 50 per shape — the
spec's ≥50 floor; the milestone exit run is recorded at 200 in the WP5 PR).
"""

from __future__ import annotations

import dataclasses
import os

import numpy as np
import pytest
from scipy.optimize import brentq

from abkit.stats.factory import create_method
from abkit.stats.rng import derive_seed
from abkit.validate.inject import inject_multiplicative
from abkit.validate.panel import PanelCutoff, PlaceboPanel
from abkit.validate.resample import build_arm, placebo_mask, present_positions
from abkit.validate.scoring import CellScore, _score_cell_scalar, score_cell
from tests.validate._panels import normal_panel

#: Placebo splits per (shape, seed) run — small on purpose: breadth across seeds
#: catches boundary flips better than depth within one stream (§WP5 step 2).
ITERATIONS = 40
#: Distinct seed_parts tuples per shape (the ≥50 spec floor; exit run at 200).
N_SEEDS = int(os.environ.get("ABKIT_PARITY_SEEDS", "50"))
#: Loose alpha so the peeking / exaggeration / sequential branches all carry
#: real crossings at ITERATIONS=40 (the smoke-parity precedent).
ALPHA = 0.2


# ── The field-classification contract (trip-wired against CellScore below) ────

#: Integer tallies — EXACT equality between the engines.
_COUNT_FIELDS = (
    "iterations",
    "valid_iterations",
    "degenerate_horizon",
    "kept_grid_points",
    "total_grid_points",
)
#: Ratios of integer tallies + passthroughs + the shared scalar τ² helper —
#: identical floats whenever the underlying counts match. ``achieved_mde`` is
#: exact BY CONSTRUCTION since the R1 fix: the vectorized MDE seam rebuilds the
#: control arm through the scalar ``build_arm`` on the row's own mask, so
#: ``_analytic_mde`` sees bit-identical inputs in both engines (reading the
#: GEMM columns instead flipped None↔0.0 at the 2-unit-CUPED corr≡±1 edge).
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
    "achieved_mde",
)
#: Continuous means — rel-1e-9: the batch GEMM/pairwise reduction order differs
#: from the scalar sequential accumulation (WP3's documented finding).
_CONTINUOUS_FIELDS = (
    "effect_exaggeration",
    "effect_exaggeration_sequential",
    "ci_width",
    "ci_width_sequential",
)
#: Curves — exact: y-values are ratios of the (exactly-matching) per-look
#: first-crossing histograms over the (exactly-matching) valid_iterations.
_CURVE_FIELDS = ("peeking_curve", "peeking_curve_sequential")


def test_field_classification_covers_every_cellscore_field():
    """Trip-wire: a future CellScore field CANNOT silently escape this gate —
    adding one forces a conscious choice of its parity class here."""
    classified = (
        set(_COUNT_FIELDS)
        | set(_EXACT_FIELDS)
        | set(_CONTINUOUS_FIELDS)
        | set(_CURVE_FIELDS)
        | {"warnings"}
    )
    assert {f.name for f in dataclasses.fields(CellScore)} == classified


def _assert_full_parity(vec: CellScore, sca: CellScore, ctx: str) -> None:
    for field in _COUNT_FIELDS:
        assert getattr(vec, field) == getattr(sca, field), f"{ctx}: count field {field}"
    for field in _EXACT_FIELDS:
        assert getattr(vec, field) == getattr(sca, field), f"{ctx}: exact field {field}"
    for field in _CONTINUOUS_FIELDS:
        v, s = getattr(vec, field), getattr(sca, field)
        assert (v is None) == (s is None), f"{ctx}: None-ness of {field}"
        if v is not None:
            assert v == pytest.approx(s, rel=1e-9), f"{ctx}: continuous field {field}"
    for field in _CURVE_FIELDS:
        assert getattr(vec, field) == getattr(sca, field), f"{ctx}: curve {field}"
    assert vec.warnings == sca.warnings, f"{ctx}: warnings"


# ── Shape builders (multi-cutoff siblings of the test_scoring smoke shapes) ───


def _binomial_panel(*, n_units: int, n_cutoffs: int, seed: int, rate: float = 0.3) -> PlaceboPanel:
    """A cumulative Bernoulli panel (one new trial per unit per look) — the
    multi-cutoff z-test shape, so the fraction kind exercises peeking too."""
    rng = np.random.default_rng(seed)
    successes = (rng.random((n_units, n_cutoffs)) < rate).astype(np.float64)
    cum_s = np.cumsum(successes, axis=1)
    cum_t = np.cumsum(np.ones((n_units, n_cutoffs)), axis=1)
    unit_idx = np.arange(n_units)
    cutoffs = tuple(
        PanelCutoff(
            elapsed_days=float(k + 1),
            is_horizon=(k == n_cutoffs - 1),
            unit_idx=unit_idx,
            values=cum_s[:, k].copy(),
            secondary=cum_t[:, k].copy(),
        )
        for k in range(n_cutoffs)
    )
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=cutoffs,
        covariate=None,
        input_kind="fraction",
        kept_grid_points=n_cutoffs,
        total_grid_points=n_cutoffs,
    )


def _ratio_panel(*, n_units: int, n_cutoffs: int, seed: int) -> PlaceboPanel:
    """A cumulative ratio panel (per-unit ratio ≈ 0.3) — the ratio-delta shape."""
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


def _sparse_panel(*, seed: int, with_covariate: bool = False) -> PlaceboPanel:
    """A deliberately gap-heavy sample panel: tiny present-cohorts per look
    (horizon = 6 units ⇒ ~22 % of fair splits leave an arm < MIN_ARM_UNITS), so
    SOME iterations degenerate and some don't — the cell-level stress for the
    "gaps, never zeros" bookkeeping (§WP3 risk list, promoted to the cell gate).
    With ``with_covariate`` this doubles as the CUPED × MIN_ARM_UNITS stress:
    a 2-unit arm's metric↔covariate correlation is mathematically ±1, the
    knife-edge where the MDE seam's engines diverged before the scalar
    ``build_arm`` rebuild (adversarial review round 1)."""
    rng = np.random.default_rng(seed)
    n_units = 48
    sizes = (3, 12, 6)  # early look of 3, a healthy middle, a fragile horizon
    cutoffs = []
    for k, size in enumerate(sizes):
        unit_idx = np.sort(rng.choice(n_units, size=size, replace=False))
        cutoffs.append(
            PanelCutoff(
                elapsed_days=float(k + 1),
                is_horizon=(k == len(sizes) - 1),
                unit_idx=unit_idx,
                values=rng.normal(10.0, 3.0, size=size),
            )
        )
    covariate = rng.normal(0.0, 1.0, size=n_units) if with_covariate else None
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=tuple(cutoffs),
        covariate=covariate,
        input_kind="sample",
        kept_grid_points=len(sizes),
        total_grid_points=len(sizes),
    )


def _shape_sample():
    return (
        normal_panel(n_units=200, n_cutoffs=5, seed=501),
        create_method("t-test", alpha=ALPHA),
        0.12,
    )


def _shape_cuped():
    return (
        normal_panel(n_units=200, n_cutoffs=5, seed=502, with_covariate=True),
        create_method("cuped-t-test", alpha=ALPHA),
        0.12,
    )


def _shape_absolute():
    return (
        normal_panel(n_units=200, n_cutoffs=5, seed=503),
        create_method("t-test", alpha=ALPHA, params={"test_type": "absolute"}),
        0.12,
    )


def _shape_fraction():
    return (
        _binomial_panel(n_units=400, n_cutoffs=4, seed=504),
        create_method("z-test", alpha=ALPHA),
        0.15,
    )


def _shape_ratio():
    return (
        _ratio_panel(n_units=200, n_cutoffs=4, seed=505),
        create_method("ratio-delta", alpha=ALPHA),
        0.12,
    )


def _shape_sparse():
    return _sparse_panel(seed=506), create_method("t-test", alpha=ALPHA), 0.3


def _shape_cuped_sparse():
    """CUPED at the MIN_ARM_UNITS floor — the shape class that caught the MDE
    None↔0.0 engine divergence (R1 finding #1); kept forever as its regression."""
    return (
        _sparse_panel(seed=507, with_covariate=True),
        create_method("cuped-t-test", alpha=ALPHA),
        0.3,
    )


def _shape_clamp():
    """A high base rate (0.9) with δ=0.5 saturates the proportion arm
    (count > nobs) on essentially every injected iteration — the clamp warning
    path exercised across the seed battery, not just one pinned seed."""
    return (
        _binomial_panel(n_units=400, n_cutoffs=4, seed=508, rate=0.9),
        create_method("z-test", alpha=ALPHA),
        0.5,
    )


_SHAPES = {
    "sample": _shape_sample,
    "cuped": _shape_cuped,
    "absolute": _shape_absolute,
    "fraction": _shape_fraction,
    "ratio": _shape_ratio,
    "sparse": _shape_sparse,
    "cuped_sparse": _shape_cuped_sparse,
    "clamp": _shape_clamp,
}


# ── The many-seed battery ─────────────────────────────────────────────────────


# The cuped_sparse shape's 2-unit arms sit on the corr≡±1 knife-edge, where
# |corr| can land one ULP above 1 and `cuped_adjusted_std`'s sqrt goes NaN —
# a pre-existing, scalar-reachable M4-era guard path (NaN → isfinite → MDE
# None), emitted IDENTICALLY by both engines (shared `_analytic_mde` code);
# only the emission is noisy, so it is filtered here, never fixed silently.
@pytest.mark.filterwarnings("ignore:invalid value encountered in sqrt:RuntimeWarning")
@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_many_seed_parity(shape):
    """N_SEEDS distinct seed_parts per shape, injection alternating by seed
    parity (both engine branches at ≥ N_SEEDS/2 seeds each). Every CellScore
    field compares per the classification above; the fixture-honesty tallies at
    the bottom guard against a dead shape that would pass parity vacuously."""
    panel, method, delta = _SHAPES[shape]()
    any_crossed = any_powered = any_seq = any_partial_gap = any_clamped = False
    for s in range(N_SEEDS):
        seed_parts = ("wp5-parity", shape, s)
        inject = delta if s % 2 else None
        kwargs = {"iterations": ITERATIONS, "seed_parts": seed_parts, "inject_effect": inject}
        vec = score_cell(panel, method, **kwargs)
        sca = _score_cell_scalar(panel, method, **kwargs)
        _assert_full_parity(vec, sca, ctx=f"shape={shape} seed={s} inject={inject}")
        any_crossed = any_crossed or vec.effect_exaggeration is not None
        any_powered = any_powered or vec.power is not None
        any_seq = any_seq or vec.tau2 is not None
        any_partial_gap = any_partial_gap or 0 < vec.degenerate_horizon < ITERATIONS
        any_clamped = any_clamped or any("saturated" in w for w in vec.warnings)

    # Fixture honesty: the battery must actually exercise the branches it
    # claims to gate — a shape where nothing ever crosses (or injection never
    # scores) would pass parity without testing the peeking/power paths.
    assert any_crossed, f"{shape}: no seed ever crossed significance — dead peeking branch"
    assert any_powered, f"{shape}: no injected seed scored power — dead injected branch"
    assert any_seq, f"{shape}: τ² never anchored — dead always-valid branch"
    if shape in ("sparse", "cuped_sparse"):
        assert any_partial_gap, f"{shape}: no partially-degenerate run — dead gap bookkeeping"
    if shape == "clamp":
        assert any_clamped, "clamp: no injected run ever saturated — dead clamp-warning branch"


# ── Cross-block accumulation parity ───────────────────────────────────────────


@pytest.mark.parametrize("quantum", [1, 7, 128])
def test_multi_block_streaming_parity(quantum, monkeypatch):
    """The battery shapes are small enough that ``block_rows(n_units)`` dwarfs
    ITERATIONS — the whole run streams as ONE block, leaving the cross-block
    accumulator path (partial final block included) unexercised. Shrinking the
    quantum simulates the huge-``n_units`` regime honestly: masks are seeded
    per absolute row (partition-independent by construction), so full parity
    must hold under ANY deterministic partition — quantum=1 (one row per GEMM,
    maximal cross-block traffic), 7 (40 = 5·7 + 5, a ragged final block), and
    128 (the bootstrap-engine quantum)."""
    import abkit.validate.scoring as scoring_mod

    monkeypatch.setattr(scoring_mod, "block_rows", lambda n_units: quantum)
    # covariate GEMM (widest sample column set) + the ratio kind (widest guards)
    for shape in ("cuped", "ratio"):
        panel, method, delta = _SHAPES[shape]()
        for s in range(6):
            seed_parts = ("wp5-blocks", shape, s)
            inject = delta if s % 2 else None
            kwargs = {"iterations": ITERATIONS, "seed_parts": seed_parts, "inject_effect": inject}
            vec = score_cell(panel, method, **kwargs)
            sca = _score_cell_scalar(panel, method, **kwargs)
            _assert_full_parity(vec, sca, ctx=f"quantum={quantum} shape={shape} seed={s}")


# ── Pinned rare-branch parity (adversarial review round 1) ────────────────────


#: Deterministic seeds (found by scan, stable via derive_seed) where the sparse
#: shape's τ² anchor fails at EVERY cutoff while regular iterations still score
#: — the "always-valid column skipped" state (~0.09 % of seeds; the battery
#: alone would sample it far too rarely to pin).
_TAU2_GAP_SEEDS = (2029, 14168, 18071, 18617)


@pytest.mark.parametrize("s", _TAU2_GAP_SEEDS)
def test_tau2_unanchorable_parity(s):
    """The sequential-eligible-but-unanchorable branch (``tau2 is None`` with
    ``valid_iterations > 0``): every ``if tau2 is not None`` guard in the
    vectorized engine must fall through exactly like the scalar early-outs —
    all sequential columns None, the skip warning identical (R1 finding: this
    reachable state had no parity coverage anywhere in the repo)."""
    panel, method, delta = _SHAPES["sparse"]()
    for inject in (None, delta):
        kwargs = {
            "iterations": ITERATIONS,
            "seed_parts": ("wp5-tau2gap", s),
            "inject_effect": inject,
        }
        vec = score_cell(panel, method, **kwargs)
        sca = _score_cell_scalar(panel, method, **kwargs)
        _assert_full_parity(vec, sca, ctx=f"tau2gap seed={s} inject={inject}")
        # Fixture honesty: the branch is genuinely the unanchorable one.
        assert vec.tau2 is None and vec.valid_iterations > 0, f"seed {s} drifted off the branch"
        assert any("could not be anchored" in w for w in vec.warnings)
        assert vec.fpr_sequential is None and vec.peeking_fpr_sequential is None


def test_no_valid_horizon_parity():
    """``valid_iterations == 0`` (every split degenerate at a 2-present-unit
    horizon): the "population is too small to score" warning and the all-None
    columns must agree between engines — with and without injection (the
    injected pass must stay silent when no row is ever valid). R1 finding:
    this warning path had scalar↔vector coverage nowhere."""
    rng = np.random.default_rng(509)
    n_units = 10
    unit_idx = np.array([2, 7])  # 2 present units — no split reaches 2 per arm
    cutoff = PanelCutoff(
        elapsed_days=1.0, is_horizon=True, unit_idx=unit_idx, values=rng.normal(10.0, 3.0, 2)
    )
    panel = PlaceboPanel(
        n_units=n_units,
        cutoffs=(cutoff,),
        covariate=None,
        input_kind="sample",
        kept_grid_points=1,
        total_grid_points=1,
    )
    method = create_method("t-test", alpha=ALPHA)
    for inject in (None, 0.2):
        kwargs = {"iterations": 25, "seed_parts": ("wp5-novalid",), "inject_effect": inject}
        vec = score_cell(panel, method, **kwargs)
        sca = _score_cell_scalar(panel, method, **kwargs)
        _assert_full_parity(vec, sca, ctx=f"no-valid-horizon inject={inject}")
        assert vec.valid_iterations == 0 and vec.degenerate_horizon == 25
        assert any("too small to score" in w for w in vec.warnings)
        assert vec.fpr is None and vec.power is None and vec.peeking_curve == ()


# ── Corrupt-input regression: the MDE seam must stay reporting-only ───────────


def _overcounted_panel() -> PlaceboPanel:
    """A fraction panel with 12 corrupt units (per-unit successes > trials by
    0.6) against 18 healthy units (slack 0.8): whether an ARM's aggregate
    count exceeds its nobs is decided by the split (≥9 corrupt units in a
    15-unit arm), with per-unit margins far above ULP — platform-stable."""
    n_units = 30
    trials = np.full(n_units, 4.0)
    successes = np.full(n_units, 3.2)
    successes[:12] = 4.6
    cutoff = PanelCutoff(
        elapsed_days=7.0,
        is_horizon=True,
        unit_idx=np.arange(n_units),
        values=successes,
        secondary=trials,
    )
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=(cutoff,),
        covariate=None,
        input_kind="fraction",
        kept_grid_points=1,
        total_grid_points=1,
    )


#: Scanned-and-pinned seeds: the τ² anchor split does NOT over-sum (the shared
#: scalar helper survives), ≥1 iteration's arm A DOES (the MDE-seam rebuild
#: meets an unconstructable Fraction), and the scalar engine crashes.
_OVERCOUNT_SEEDS = (0, 1, 3)


@pytest.mark.parametrize("s", _OVERCOUNT_SEEDS)
def test_overcounted_fraction_mde_row_is_skipped_not_a_crash(s):
    """R2 regression: an over-counted fraction arm (per-unit successes >
    trials — corrupt input) yields a finite POOLED z-test CI in the batch main
    pass, but the MDE seam's scalar ``build_arm`` rebuild cannot construct the
    ``Fraction`` (count > nobs raises). Reporting-only means reporting-only:
    the row's MDE is skipped and the cell's fpr/power survive. The scalar
    ENGINE fails such a cell loudly at its own per-iteration ``build_arm`` —
    a pre-existing corrupt-input divergence of the WP4 batch main pass
    (documented in aa-false-positive-matrix.md §9), pinned here so it stays a
    conscious decision, not an accident."""
    from abkit.stats.exceptions import SampleValidationError
    from abkit.stats.rng import derive_seed

    panel = _overcounted_panel()
    method = create_method("z-test", alpha=ALPHA)
    seed_parts = ("wp5-overcount", s)

    # Fixture honesty: at least one iteration's control arm genuinely
    # over-sums under these exact seeds (the skip branch really runs).
    cut = panel.cutoffs[0]
    over = [
        placebo_mask(panel.n_units, 0.5, derive_seed(*seed_parts, i)) for i in range(ITERATIONS)
    ]
    assert any(cut.values[m].sum() > cut.secondary[m].sum() for m in over)

    vec = score_cell(panel, method, iterations=ITERATIONS, seed_parts=seed_parts)
    assert vec.valid_iterations > 0
    assert vec.achieved_mde is not None  # healthy rows still contribute
    with pytest.raises(SampleValidationError):
        _score_cell_scalar(panel, method, iterations=ITERATIONS, seed_parts=seed_parts)


# ── The mandatory near-boundary stress (§0.3(3)) ──────────────────────────────
#
# Count exactness is an empirical claim, and its one dangerous region is a CI
# bound within machine epsilon of zero — where the engines' different reduction
# orders can flip a reject decision random seeds would almost never land on. So
# the boundary is MANUFACTURED per seed: brentq solves the injected δ that puts
# iteration 0's horizon ``left_bound`` exactly at zero on the scalar path.
#
# Two regimes, two tests:
# - at δ·(1±1e-9) — the spec's mandated fixture — the bound sits ~1e-11 from
#   zero, five orders of magnitude above the engines' measured ULP divergence
#   (~1e-16 rel on the GEMM-aggregated bound), so FULL exact parity must hold;
# - at δ = the root itself the bound sits within ~1e-15 of zero, INSIDE the
#   ULP-ambiguity band, and the engines may legitimately disagree on that one
#   manufactured decision (both answers are correct — the input is ill-
#   conditioned at the decision boundary). That honest limit is pinned as
#   "at most the one stressed hit, in the injected significance column only" —
#   measured live by this suite's first run: power 0.5 vs 0.6 at 10 iterations.
#
# Scope: the manufactured boundary uses the sample kind / relative t-test (a
# monotone, well-bracketed left bound for brentq). The power-only exclusion
# in the exact-root test was verified empirically for this shape across a
# 3 000-seed scan (R2); fraction/ratio/CUPED boundaries are not manufactured
# here — their kernel-level guards are stressed in tests/stats and the
# battery above, and a generalization would need its own bracketing analysis.

#: 12 generic seeds + two scanned-and-pinned seeds (108, 124) whose null
#: split is ALREADY significant at δ=0 — the negative-root rebracket branch
#: (lo = -0.5) runs live instead of staying dead defensive code (R1 finding).
_BOUNDARY_SEEDS = tuple(range(12)) + (108, 124)
#: Relative nudges putting the stressed split's bound within ~1e-9 of the
#: CI-excludes-zero decision boundary — yet safely outside ULP ambiguity.
_NUDGES = (1.0 - 1e-9, 1.0 + 1e-9)
_BOUNDARY_ITERATIONS = 10


def _boundary_case(s: int):
    """Panel/method/seed_parts + the brentq-solved boundary δ for seed ``s``."""
    panel = normal_panel(n_units=250, n_cutoffs=4, seed=700 + s)
    method = create_method("t-test", alpha=0.05)
    seed_parts = ("wp5-boundary", s)

    # Iteration 0's horizon arms under the exact seeds the engines will use.
    mask = placebo_mask(panel.n_units, 0.5, derive_seed(*seed_parts, 0))
    cut = panel.cutoffs[-1]  # normal_panel marks the last cutoff as horizon
    pos_a, pos_b = present_positions(mask, cut.unit_idx)
    arm_a = build_arm("sample", cut.values, cut.secondary, None, cut.unit_idx, pos_a)
    arm_b = build_arm("sample", cut.values, cut.secondary, None, cut.unit_idx, pos_b)
    assert arm_a is not None and arm_b is not None

    def left_bound(delta: float) -> float:
        return method.from_suffstats(arm_a, inject_multiplicative(arm_b, delta)).left_bound

    lo, hi = 0.0, 1.0
    if left_bound(lo) > 0.0:
        lo = -0.5  # this null split is already significant — the root sits below δ=0
    assert left_bound(lo) < 0.0 < left_bound(hi), "no boundary bracket — fixture broke"
    d0 = brentq(left_bound, lo, hi, xtol=1e-15, rtol=8.9e-16, maxiter=200)

    # Fixture honesty: the stressed split's bound really sits at the boundary
    # (within 1e-9 of zero relative to the CI scale), not merely near it.
    res = method.from_suffstats(arm_a, inject_multiplicative(arm_b, d0))
    assert abs(res.left_bound) <= 1e-9 * res.ci_length
    return panel, method, seed_parts, d0


@pytest.mark.parametrize("s", _BOUNDARY_SEEDS)
def test_near_boundary_parity(s):
    """The §0.3(3) mandate: full exact parity with the stressed split's bound
    placed within ~1e-9 of the decision boundary, on both sides of it."""
    panel, method, seed_parts, d0 = _boundary_case(s)
    powers = []
    for nudge in _NUDGES:
        kwargs = {
            "iterations": _BOUNDARY_ITERATIONS,
            "seed_parts": seed_parts,
            "inject_effect": d0 * nudge,
        }
        vec = score_cell(panel, method, **kwargs)
        sca = _score_cell_scalar(panel, method, **kwargs)
        _assert_full_parity(vec, sca, ctx=f"boundary seed={s} nudge={nudge!r}")
        powers.append(sca.power)
    # Fixture honesty: the two nudges genuinely straddle the stressed split's
    # decision — exactly one injected hit separates them (d0 > 0 ⇒ +1e-9 flips
    # the boundary iteration significant; a negative root flips the other way).
    assert powers[0] is not None and powers[1] is not None
    assert abs(powers[1] - powers[0]) == pytest.approx(1.0 / _BOUNDARY_ITERATIONS)


@pytest.mark.parametrize("s", _BOUNDARY_SEEDS)
def test_at_exact_boundary_divergence_is_at_most_the_stressed_decision(s):
    """The honest LIMIT of the exactness claim, pinned: with the bound solved
    to within ~1e-15 of zero (inside ULP ambiguity), the engines may disagree
    on that single manufactured decision — and on nothing else. Everything but
    ``power`` stays exact; ``power`` moves by at most one hit. Asserting zero
    divergence here would be asserting which way two correct roundings fall."""
    panel, method, seed_parts, d0 = _boundary_case(s)
    kwargs = {"iterations": _BOUNDARY_ITERATIONS, "seed_parts": seed_parts, "inject_effect": d0}
    vec = score_cell(panel, method, **kwargs)
    sca = _score_cell_scalar(panel, method, **kwargs)

    for field in _COUNT_FIELDS:
        assert getattr(vec, field) == getattr(sca, field), f"seed={s}: count field {field}"
    for field in _EXACT_FIELDS:
        if field == "power":
            continue  # the one column the manufactured boundary decision feeds
        assert getattr(vec, field) == getattr(sca, field), f"seed={s}: exact field {field}"
    for field in _CURVE_FIELDS:
        assert getattr(vec, field) == getattr(sca, field), f"seed={s}: curve {field}"
    for field in _CONTINUOUS_FIELDS:
        v, c = getattr(vec, field), getattr(sca, field)
        assert (v is None) == (c is None), f"seed={s}: None-ness of {field}"
        if v is not None:
            assert v == pytest.approx(c, rel=1e-9), f"seed={s}: continuous field {field}"
    assert vec.warnings == sca.warnings, f"seed={s}: warnings"
    assert vec.power is not None and sca.power is not None
    assert abs(vec.power - sca.power) <= 1.0 / _BOUNDARY_ITERATIONS + 1e-12, (
        f"seed={s}: engines diverged by MORE than the one stressed decision "
        f"(vec={vec.power}, sca={sca.power})"
    )
