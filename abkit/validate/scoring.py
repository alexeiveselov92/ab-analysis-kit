"""A/A scoring: FPR / cumulative-peeking FPR / power / achieved-MDE / coverage /
effect-exaggeration-at-stop from a placebo panel (docs/specs/m4-implementation-plan.md
D2, D3, D16).

The scorer runs ``iterations`` placebo splits through a method's ``from_suffstats``
path over the panel's cadence grid. The significance primitive is **CI-excludes-zero**
— the readout's own rule (``pipeline/readout.py`` ``_build_sig_map``), not the raw
``reject`` flag — so z-test / bootstrap edge disagreements follow the readout.

Two passes per cell:

- **Null** (no injection) → single-look FPR (horizon cutoff only, the official
  fixed-horizon rate) and the **cumulative-peeking FPR**: the naive optional-stopping
  hazard — the share of placebos that cross significance at *any* look across the grid,
  the analyst who stops the first time the chart's CI excludes zero (aa-fpr §3; D3).
  Requiring the readout's full stabilization-persistence here would measure the tool's
  *defense*, not the *hazard*, and empirically drops below the single-look rate — the
  opposite of the column's purpose; the stabilized rule stays the official verdict
  (with pre-horizon refusal), and the single-look FPR is reported beside the peeking
  FPR so the jump is visible. Effect-exaggeration-at-stop is the |effect| at that first
  crossing (the winner's curse against a true effect of zero).
- **Injected** (``inject_effect`` δ into the treatment arm) → power (horizon
  CI-excludes-zero), CI coverage of the true effect, and the analytic achieved MDE at
  ``target_power`` from the horizon control arm.

Degenerate cutoffs (an arm too small, or NaN CI bounds from zero variance) are gaps,
never zeros — tallied separately so they can never silently deflate the FPR.

Two engines, one contract (M7 WP4 — docs/specs/m7-implementation-plan.md §WP4):

- **Vectorized** (the default for ``supports_vectorized`` methods): a
  block-streamed loop over ``vector_resample.iter_blocks`` — per cutoff, one
  ``build_arm_batch`` GEMM builds the whole block's arm suffstats and one
  ``from_suffstats_array`` call scores them; the first-crossing/peeking state is
  streamed per row in O(block) memory, never O(block × cutoffs). Blocking is a
  pure function of ``(iterations, n_units)`` plus module constants, so persisted
  A/A numbers stay byte-reproducible run-to-run (D13) **under a fixed BLAS
  configuration** — the same scope the Poisson bootstrap engine ships with: a
  different BLAS build or thread count re-rounds the GEMM's continuous columns
  at the ULP level (measured ~1e-15 rel between 1 and ≥2 OpenBLAS threads;
  counts/curves unaffected — adversarial review round 1), far inside the
  rel-1e-9 budget but not bit-identical across machines. Versus the scalar
  engine, integer counts are expected exact and continuous means agree at
  rel-1e-9 inside the conditioning band (the vector_resample module docstring;
  smoke-pinned in tests/validate/test_scoring.py, exhaustively gated in WP5).
- **Scalar fallback** (``_score_cell_scalar``): a method that has not opted into
  the batch kernels (``supports_vectorized=False`` — the bootstrap family, any
  custom plugin) runs the original per-iteration loop, moved verbatim — validate
  never breaks for a plugin that only implements ``from_suffstats``.
"""

from __future__ import annotations

import functools
import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

import numpy as np

from abkit.stats.base import BaseMethod
from abkit.stats.exceptions import AbkitStatsWarning
from abkit.stats.power import cuped_adjusted_std, get_fraction_mde, get_ttest_mde
from abkit.stats.rng import derive_seed
from abkit.stats.samples import Fraction, RatioSufficientStats, SufficientStats
from abkit.stats.sequential import mixture_tau2, se_from_ci_length, sequentialize
from abkit.stats.sequential.confidence_sequence import (
    se_from_ci_length_array,
    sequentialize_array,
)
from abkit.validate._types import ValidateError
from abkit.validate.inject import (
    inject_multiplicative,
    inject_multiplicative_columns,
    injection_clamped,
    injection_clamped_columns,
)
from abkit.validate.panel import PlaceboPanel
from abkit.validate.resample import ArmStats, build_arm, placebo_mask, present_positions
from abkit.validate.vector_resample import (
    _ROW_TEMP_BYTES,
    DEFAULT_MAX_BLOCK_BYTES,
    FloatArray,
    PreparedCutoff,
    block_rows,
    build_arm_batch,
    iter_blocks,
    placebo_mask_block,
    prepare_cutoff,
)

#: Default target power for the achieved-MDE column.
DEFAULT_TARGET_POWER = 0.8

_F = TypeVar("_F", bound=Callable[..., Any])


def suppress_resample_warnings(fn: _F) -> _F:
    """Silence per-split ``AbkitStatsWarning`` (the CUPED low-correlation and ratio-zero
    legacy guards) for the duration of A/A scoring.

    A validate run re-invokes the SAME method across hundreds of placebo splits × looks,
    so these guards — meaningful once for a real ``abk run`` — become thousands of lines of
    stderr spam (the guard message embeds the varying correlation, so Python's own
    once-per-message dedup never fires). This is **non-numeric**: only the warning
    *emission* is filtered here; every statistic is unchanged, and the single real
    analysis still surfaces the guard (also carried in ``TestResult.warnings``).
    """

    @functools.wraps(fn)
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", AbkitStatsWarning)
            return fn(*args, **kwargs)

    return cast(_F, _wrapper)


@dataclass(frozen=True)
class CellScore:
    """One scored (metric × method × alpha) matrix cell (docs/specs/aa-false-positive-matrix.md §2)."""

    iterations: int
    #: Iterations with a usable (non-degenerate) horizon cutoff — the FPR/power denominator.
    valid_iterations: int
    #: Single-look FPR at the horizon cutoff (null pass), or None if never usable.
    fpr: float | None
    #: Cumulative-peeking FPR: share of null iterations whose CI excludes zero at ANY
    #: look across the grid (optional stopping — the peeking hazard, aa-fpr §3 / D3).
    peeking_fpr: float | None
    #: The peeking curve — one ``(elapsed_days, cumulative_fpr)`` point per grid look,
    #: cumulative_fpr = share of iterations that have first-crossed significance by that
    #: look (monotone non-decreasing; the final look equals ``peeking_fpr``). Empty when
    #: no iteration was scorable. THIS is the "nominal α vs real peeking FPR" story (R10).
    peeking_curve: tuple[tuple[float, float], ...]
    #: Power at the horizon under the injected effect, or None when no injection.
    power: float | None
    #: CI coverage of the injected truth at the horizon, or None when no injection.
    coverage: float | None
    #: Analytic achieved MDE at ``target_power`` from the horizon control arm, or None.
    achieved_mde: float | None
    #: Winner's curse: mean |effect| at the first false crossing (null pass), or None
    #: when no null iteration ever crosses significance across the grid.
    effect_exaggeration: float | None
    #: Injected multiplicative δ (None on a pure FPR run).
    injected_effect: float | None
    #: Cutoffs where an arm was too small or the CI degenerate (a gap, not a zero).
    degenerate_horizon: int
    kept_grid_points: int
    total_grid_points: int
    # ── The M5 D8 always-valid sequential column (measured side-by-side, never
    # asserted — cumulative-intervals §6.5). Same denominators as the fixed columns,
    # computed off the always-valid CI (sequential.sequentialize over the SAME
    # per-look (effect, SE)). None when the method is ineligible
    # (supports_sequential=False) or τ² could not be anchored. ──
    #: The frozen per-cell mixture variance τ² (provenance; anchored to the first usable
    #: look — D-Seq-anchor, matching driver._sequential_tau2 for the D4 parity requirement).
    tau2: float | None = None
    #: Single-look FPR at the horizon under the always-valid CI (should sit near α).
    fpr_sequential: float | None = None
    #: Cumulative-peeking FPR under the always-valid CI — the honest completion of the
    #: peeking story: this should return to ≈α where ``peeking_fpr`` broke budget.
    peeking_fpr_sequential: float | None = None
    #: The always-valid peeking curve (one point per look), for the side-by-side chart.
    peeking_curve_sequential: tuple[tuple[float, float], ...] = ()
    #: Power at the horizon under the always-valid CI (guards a τ² that "fixes" FPR by
    #: never rejecting — must stay materially above α on the injected fixture).
    power_sequential: float | None = None
    #: Always-valid CI coverage of the injected truth at the horizon.
    coverage_sequential: float | None = None
    #: Winner's curse under the always-valid CI (mean |effect| at first crossing).
    effect_exaggeration_sequential: float | None = None
    #: Mean fixed-horizon CI width (the side-by-side baseline for the widening).
    ci_width: float | None = None
    #: Mean always-valid horizon CI width (the anytime price — always ≥ ``ci_width``).
    ci_width_sequential: float | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _significance(left: float, right: float) -> tuple[bool, int] | None:
    """CI-excludes-zero significance (readout ``_build_sig_map`` rule, readout.py:195–205).

    Returns ``(significant, sign)`` — ``sign`` is +1 if the whole CI is above zero,
    −1 if below, 0 if it straddles zero. ``None`` when either bound is non-finite
    (a degenerate cutoff — a gap, never a clean non-rejection).
    """
    if not (math.isfinite(left) and math.isfinite(right)):
        return None
    if left > 0:
        return (True, 1)
    if right < 0:
        return (True, -1)
    return (False, 0)


def _cell_tau2(
    panel: PlaceboPanel,
    method: BaseMethod,
    *,
    share_a: float,
    anchor_seed: int,
) -> float | None:
    """The frozen per-cell mixture variance τ², anchored to the first usable look.

    τ² MUST be a single constant for the cell (Ville's inequality needs a prior fixed
    in advance), so it is computed ONCE — never per iteration or per look. It is
    anchored to the **first usable grid cutoff** (D-Seq-anchor): scan looks from the
    earliest, build the arms under a canonical anchor split, and take the first one
    with a finite positive ``SE`` (recovered by CI-inversion); pass ``SE²`` to the
    shared :func:`abkit.stats.sequential.mixture_tau2` (the SAME helper the pipeline
    uses — the parity requirement). ``None`` when the method is sequential-ineligible
    (``supports_sequential=False``) or no look is usable → the cell has no sequential
    column. Validity is robust to the anchor; τ² only sets where the sequence is
    tightest (here: early, aligned with the impatient-experimenter use-case).
    """
    if not method.supports_sequential:
        return None
    mask = placebo_mask(panel.n_units, share_a, anchor_seed)
    for cut in panel.cutoffs:
        pos_a, pos_b = present_positions(mask, cut.unit_idx)
        arm_a = build_arm(
            panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_a
        )
        arm_b = build_arm(
            panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_b
        )
        if arm_a is None or arm_b is None:
            continue
        se = se_from_ci_length(method.from_suffstats(arm_a, arm_b).ci_length, method.alpha)
        if math.isfinite(se) and se > 0.0:
            return mixture_tau2(se * se, method.alpha)
    return None


def _always_valid_sig(
    result: object, tau2: float, alpha: float
) -> tuple[tuple[bool, int] | None, float]:
    """Always-valid significance + CI width for a fixed ``TestResult`` at a look.

    Recovers ``SE`` from the fixed CI (CI-inversion), widens it into the always-valid
    interval (:func:`sequentialize`), and applies the same CI-excludes-zero primitive.
    Returns ``(significance_or_None, ci_width)`` — width is NaN on a degenerate look.
    """
    se = se_from_ci_length(result.ci_length, alpha)  # type: ignore[attr-defined]
    lo, hi, _ = sequentialize(result.effect, se, tau2, alpha)  # type: ignore[attr-defined]
    return _significance(lo, hi), hi - lo


def _analytic_mde(
    control: ArmStats,
    method: BaseMethod,
    *,
    ratio: float,
    target_power: float,
) -> float | None:
    """Achieved MDE at ``target_power`` from the horizon control arm (best-effort).

    Closed-form t-test / z-test / CUPED families have an analytic MDE; ratio-delta
    and the bootstrap family do not (no power capability) → ``None``. Reporting-only:
    never feeds the FPR/power counts.
    """
    test_type = method.test_type if "test_type" in method.params else "relative"
    if isinstance(control, Fraction):
        mde = get_fraction_mde(
            control.prop, control.sample_size, test_type, method.alpha, target_power, ratio
        )
        return None if not math.isfinite(mde) else float(mde)
    if isinstance(control, SufficientStats):
        std = control.std
        if control.has_covariate:
            corr = control.corr_coef
            if math.isfinite(corr):
                std = cuped_adjusted_std(std, corr)
        mde = get_ttest_mde(
            control.mean, std, control.sample_size, test_type, method.alpha, target_power, ratio
        )
        return None if not math.isfinite(mde) else float(mde)
    return None  # ratio-delta and other families: no analytic MDE


def score_cell(
    panel: PlaceboPanel,
    method: BaseMethod,
    *,
    iterations: int,
    seed_parts: tuple[object, ...],
    share_a: float = 0.5,
    inject_effect: float | None = None,
    target_power: float = DEFAULT_TARGET_POWER,
) -> CellScore:
    """Score one matrix cell over ``iterations`` deterministic placebo splits.

    ``seed_parts`` are the identity parts hashed into each iteration's placebo seed
    (``derive_seed(*seed_parts, i)``) — pass ``(experiment, metric, method_config_id)``
    so re-runs are byte-reproducible (D13). ``share_a`` is arm A's split share.

    Dispatches on ``method.supports_vectorized`` (module docstring): the batch
    engine for opted-in methods, the verbatim scalar loop for everything else —
    a plugin that only implements ``from_suffstats`` must never break validate.
    """
    if not method.supports_vectorized:
        return _score_cell_scalar(
            panel,
            method,
            iterations=iterations,
            seed_parts=seed_parts,
            share_a=share_a,
            inject_effect=inject_effect,
            target_power=target_power,
        )
    try:
        return _score_cell_vectorized(
            panel,
            method,
            iterations=iterations,
            seed_parts=seed_parts,
            share_a=share_a,
            inject_effect=inject_effect,
            target_power=target_power,
        )
    except NotImplementedError as exc:
        # A plugin that declares supports_vectorized=True whose batch kernel
        # raises NotImplementedError must fail ITS cell loudly (ValidateError is
        # what the runner's per-cell isolation catches) — never abort the whole
        # matrix with an uncaught NotImplementedError (adversarial review round
        # 1). The wrapper spans the whole engine call, so the message names the
        # raise honestly — the kernel may be missing entirely OR real but
        # refusing this cell's input partway through (adversarial review round 2).
        raise ValidateError(
            f"{method.name}: supports_vectorized=True but its from_suffstats_array "
            "batch kernel raised NotImplementedError (missing, or unsupported for "
            "this cell's input) — extend the kernel or set "
            "supports_vectorized=False to use the scalar engine"
        ) from exc


@suppress_resample_warnings
def _score_cell_scalar(
    panel: PlaceboPanel,
    method: BaseMethod,
    *,
    iterations: int,
    seed_parts: tuple[object, ...],
    share_a: float = 0.5,
    inject_effect: float | None = None,
    target_power: float = DEFAULT_TARGET_POWER,
) -> CellScore:
    """The original per-iteration scalar engine — a pure code move (M7 WP4).

    Every method reaches identical numbers here as before the vectorized engine
    existed; the WP5 parity gate runs THIS function against the batch path.
    """
    if iterations <= 0:
        raise ValidateError(f"iterations must be positive, got {iterations}")
    if not panel.cutoffs:
        raise ValidateError("panel has no cutoffs")

    horizon_pos = _horizon_index(panel)
    ratio = (1.0 - share_a) / share_a  # n_treatment / n_control at the split
    warnings: list[str] = []

    # τ² is frozen once for the cell (D4) — the always-valid column is measured on the
    # SAME per-look (effect, SE) the fixed column uses, only widened. None ⇒ no column.
    tau2 = _cell_tau2(
        panel,
        method,
        share_a=share_a,
        anchor_seed=derive_seed(*seed_parts, "tau2-anchor"),
    )

    # Absolute-effect coverage anchors the injected truth (δ·μ̂) on a FIXED,
    # split-invariant estimate of the shared population mean — the pooled point
    # estimate over ALL present horizon units, computed once. Anchoring on the
    # realized control mean (value_1) instead biases coverage low, because value_1
    # co-varies with the effect estimate (m4 exit-gate review, F2). None only for a
    # degenerate horizon or a non-finite pooled ratio (then the caller falls back).
    horizon_pooled: float | None = None
    if inject_effect is not None:
        hc = panel.cutoffs[horizon_pos]
        pooled_arm = build_arm(
            panel.input_kind,
            hc.values,
            hc.secondary,
            panel.covariate,
            hc.unit_idx,
            np.arange(hc.unit_idx.size),
        )
        if pooled_arm is not None:
            horizon_pooled = _point_estimate(pooled_arm)

    single_look_hits = 0
    peek_hits = 0
    valid_iterations = 0
    degenerate_horizon = 0
    power_hits = 0
    coverage_hits = 0
    coverage_n = 0
    exagg_values: list[float] = []
    mde_values: list[float] = []
    clamp_warned = False
    # per-grid-look tally of iterations whose FIRST significant crossing lands at that
    # look — cumulative-summed into the peeking curve after the loop (D3).
    first_cross_at_look = [0] * len(panel.cutoffs)

    # ── Parallel always-valid (sequential) tallies — same denominators as the fixed
    # columns, populated only when τ² anchored (D8/WP2). ──
    single_look_hits_seq = 0
    peek_hits_seq = 0
    power_hits_seq = 0
    coverage_hits_seq = 0
    exagg_values_seq: list[float] = []
    first_cross_at_look_seq = [0] * len(panel.cutoffs)
    width_fixed_sum = 0.0  # mean horizon CI width (fixed) — the side-by-side baseline
    width_seq_sum = 0.0  # mean horizon CI width (always-valid) — the anytime price
    width_n = 0

    for i in range(iterations):
        seed = derive_seed(*seed_parts, i)
        mask = placebo_mask(panel.n_units, share_a, seed)

        # ── Null pass over the grid ──────────────────────────────────────────
        sig_stream: list[tuple[int, tuple[bool, int], float]] = []  # (look_idx, sig, effect)
        sig_stream_seq: list[tuple[int, tuple[bool, int], float]] = []  # always-valid twin
        horizon_control: ArmStats | None = None
        horizon_sig: tuple[bool, int] | None = None
        horizon_sig_seq: tuple[bool, int] | None = None
        horizon_width_fixed = float("nan")
        horizon_width_seq = float("nan")

        for k, cut in enumerate(panel.cutoffs):
            pos_a, pos_b = present_positions(mask, cut.unit_idx)
            arm_a = build_arm(
                panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_a
            )
            arm_b = build_arm(
                panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_b
            )
            if arm_a is None or arm_b is None:
                if k == horizon_pos:
                    degenerate_horizon += 1
                continue
            result = method.from_suffstats(arm_a, arm_b)
            sig = _significance(result.left_bound, result.right_bound)
            if sig is None:
                if k == horizon_pos:
                    degenerate_horizon += 1
                continue
            sig_stream.append((k, sig, result.effect))
            if tau2 is not None:
                sig_seq, width_seq = _always_valid_sig(result, tau2, method.alpha)
                if sig_seq is not None:
                    sig_stream_seq.append((k, sig_seq, result.effect))
                if k == horizon_pos:
                    horizon_sig_seq = sig_seq
                    horizon_width_fixed = result.ci_length
                    horizon_width_seq = width_seq
            if k == horizon_pos:
                horizon_control = arm_a
                horizon_sig = sig

        # Single-look FPR + achieved MDE need a usable horizon.
        if horizon_sig is not None and horizon_control is not None:
            valid_iterations += 1
            if horizon_sig[0]:
                single_look_hits += 1
            mde = _analytic_mde(horizon_control, method, ratio=ratio, target_power=target_power)
            if mde is not None:
                mde_values.append(mde)
            # Always-valid single-look FPR + the width side-by-side (same valid horizon).
            # A None seq horizon is the measure-zero SE=0 edge → counted as non-significant.
            if tau2 is not None:
                if horizon_sig_seq is not None and horizon_sig_seq[0]:
                    single_look_hits_seq += 1
                if math.isfinite(horizon_width_fixed) and math.isfinite(horizon_width_seq):
                    width_fixed_sum += horizon_width_fixed
                    width_seq_sum += horizon_width_seq
                    width_n += 1

        # Cumulative-peeking FPR: optional stopping — the first look whose CI excludes
        # zero (a false winner under the A/A null). The horizon look is included, so
        # peeking is monotonically ≥ the single-look FPR.
        first_call = _first_significant_look(sig_stream)
        if first_call is not None:
            first_idx, first_effect = first_call
            peek_hits += 1
            first_cross_at_look[first_idx] += 1
            exagg_values.append(abs(first_effect))

        # The always-valid peeking twin — the honest completion: this should return to
        # ≈α where the fixed peeking FPR broke budget.
        if tau2 is not None:
            first_call_seq = _first_significant_look(sig_stream_seq)
            if first_call_seq is not None:
                first_idx_seq, first_effect_seq = first_call_seq
                peek_hits_seq += 1
                first_cross_at_look_seq[first_idx_seq] += 1
                exagg_values_seq.append(abs(first_effect_seq))

        # ── Injected pass (horizon only — fixed-horizon power/coverage) ──────
        if inject_effect is not None and horizon_control is not None:
            cut = panel.cutoffs[horizon_pos]
            pos_a, pos_b = present_positions(mask, cut.unit_idx)
            arm_a = build_arm(
                panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_a
            )
            arm_b = build_arm(
                panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_b
            )
            if arm_a is not None and arm_b is not None:
                if injection_clamped(arm_b, inject_effect) and not clamp_warned:
                    warnings.append(
                        "injected effect saturated a proportion arm (count > nobs) — MDE unreachable"
                    )
                    clamp_warned = True
                arm_b_inj = inject_multiplicative(arm_b, inject_effect)
                result = method.from_suffstats(arm_a, arm_b_inj)
                sig = _significance(result.left_bound, result.right_bound)
                if sig is not None:
                    coverage_n += 1
                    if sig[0]:
                        power_hits += 1
                    # absolute-effect truth = δ·μ̂ on the FIXED pooled horizon estimate
                    # (split-invariant); relative truth ignores the anchor. Fall back to
                    # value_1 only on a degenerate horizon where no pooled arm was built.
                    anchor = horizon_pooled if horizon_pooled is not None else result.value_1
                    truth = _injected_truth(method, inject_effect, anchor)
                    if result.left_bound <= truth <= result.right_bound:
                        coverage_hits += 1
                    # Always-valid power + coverage on the SAME injected result (same
                    # coverage_n denominator; the always-valid CI must still detect a
                    # real effect — the guard against a τ² that never rejects).
                    if tau2 is not None:
                        se_inj = se_from_ci_length(result.ci_length, method.alpha)
                        lo_seq, hi_seq, _ = sequentialize(result.effect, se_inj, tau2, method.alpha)
                        sig_seq = _significance(lo_seq, hi_seq)
                        if sig_seq is not None and sig_seq[0]:
                            power_hits_seq += 1
                        if lo_seq <= truth <= hi_seq:
                            coverage_hits_seq += 1

    fpr = single_look_hits / valid_iterations if valid_iterations else None
    peeking_fpr = peek_hits / valid_iterations if valid_iterations else None
    # cumulative first-crossings per look ÷ valid_iterations — monotone, ending at
    # peeking_fpr (the horizon look). Empty when nothing was scorable.
    peeking_curve: tuple[tuple[float, float], ...] = ()
    if valid_iterations:
        cumulative = 0
        curve: list[tuple[float, float]] = []
        for k, cut in enumerate(panel.cutoffs):
            cumulative += first_cross_at_look[k]
            curve.append((float(cut.elapsed_days), cumulative / valid_iterations))
        peeking_curve = tuple(curve)
    power = power_hits / coverage_n if coverage_n else None
    coverage = coverage_hits / coverage_n if coverage_n else None
    achieved_mde = float(np.mean(mde_values)) if mde_values else None
    effect_exaggeration = float(np.mean(exagg_values)) if exagg_values else None

    # ── Always-valid column (all None when τ² was not anchored) ──
    has_seq = tau2 is not None
    fpr_sequential = (
        single_look_hits_seq / valid_iterations if (has_seq and valid_iterations) else None
    )
    peeking_fpr_sequential = (
        peek_hits_seq / valid_iterations if (has_seq and valid_iterations) else None
    )
    power_sequential = power_hits_seq / coverage_n if (has_seq and coverage_n) else None
    coverage_sequential = coverage_hits_seq / coverage_n if (has_seq and coverage_n) else None
    effect_exaggeration_sequential = (
        float(np.mean(exagg_values_seq)) if (has_seq and exagg_values_seq) else None
    )
    ci_width = width_fixed_sum / width_n if (has_seq and width_n) else None
    ci_width_sequential = width_seq_sum / width_n if (has_seq and width_n) else None
    peeking_curve_sequential: tuple[tuple[float, float], ...] = ()
    if has_seq and valid_iterations:
        cumulative_seq = 0
        curve_seq: list[tuple[float, float]] = []
        for k, cut in enumerate(panel.cutoffs):
            cumulative_seq += first_cross_at_look_seq[k]
            curve_seq.append((float(cut.elapsed_days), cumulative_seq / valid_iterations))
        peeking_curve_sequential = tuple(curve_seq)

    if valid_iterations == 0:
        warnings.append(
            "no iteration produced a usable horizon cutoff — the population is too small to score"
        )
    if method.supports_sequential and not has_seq and valid_iterations:
        warnings.append(
            "always-valid column skipped — τ² could not be anchored (degenerate horizon)"
        )

    return CellScore(
        iterations=iterations,
        valid_iterations=valid_iterations,
        fpr=fpr,
        peeking_fpr=peeking_fpr,
        power=power,
        coverage=coverage,
        achieved_mde=achieved_mde,
        effect_exaggeration=effect_exaggeration,
        injected_effect=inject_effect,
        degenerate_horizon=degenerate_horizon,
        kept_grid_points=panel.kept_grid_points,
        total_grid_points=panel.total_grid_points,
        peeking_curve=peeking_curve,
        tau2=tau2,
        fpr_sequential=fpr_sequential,
        peeking_fpr_sequential=peeking_fpr_sequential,
        peeking_curve_sequential=peeking_curve_sequential,
        power_sequential=power_sequential,
        coverage_sequential=coverage_sequential,
        effect_exaggeration_sequential=effect_exaggeration_sequential,
        ci_width=ci_width,
        ci_width_sequential=ci_width_sequential,
        warnings=tuple(warnings),
    )


@suppress_resample_warnings
def _score_cell_vectorized(  # noqa: PLR0912, PLR0915 — mirrors the scalar engine stage-for-stage
    panel: PlaceboPanel,
    method: BaseMethod,
    *,
    iterations: int,
    seed_parts: tuple[object, ...],
    share_a: float = 0.5,
    inject_effect: float | None = None,
    target_power: float = DEFAULT_TARGET_POWER,
) -> CellScore:
    """The block-streamed batch engine (M7 WP4) — same contract as the scalar loop.

    Per block of iterations (``iter_blocks``), per cutoff: one ``build_arm_batch``
    GEMM builds every row's arm suffstats and one ``from_suffstats_array`` call
    yields the whole block's CI bounds; first-crossing (peeking) state streams
    per row so nothing ``(block × cutoffs)``-shaped is ever held. The
    reporting-only MDE stays a python loop, but ``iterations``-shaped (horizon
    rows only) — never ``iterations × cutoffs`` (§WP4 risk list). Blocking
    derives from ``(iterations, n_units)`` + module constants ONLY, keeping the
    scored numbers byte-reproducible run-to-run under a fixed BLAS
    configuration (D13 — see the module docstring's thread-count scope).
    """
    if iterations <= 0:
        raise ValidateError(f"iterations must be positive, got {iterations}")
    if not panel.cutoffs:
        raise ValidateError("panel has no cutoffs")

    horizon_pos = _horizon_index(panel)
    ratio = (1.0 - share_a) / share_a  # n_treatment / n_control at the split
    warnings: list[str] = []
    alpha = method.alpha
    n_cutoffs = len(panel.cutoffs)

    # τ² is frozen once for the cell (D4) — identical scalar helper, same anchor seed.
    tau2 = _cell_tau2(
        panel,
        method,
        share_a=share_a,
        anchor_seed=derive_seed(*seed_parts, "tau2-anchor"),
    )

    # The split-invariant pooled anchor for absolute-effect coverage — the same
    # once-per-cell scalar computation as the scalar engine (m4 exit-gate, F2).
    horizon_pooled: float | None = None
    if inject_effect is not None:
        hc = panel.cutoffs[horizon_pos]
        pooled_arm = build_arm(
            panel.input_kind,
            hc.values,
            hc.secondary,
            panel.covariate,
            hc.unit_idx,
            np.arange(hc.unit_idx.size),
        )
        if pooled_arm is not None:
            horizon_pooled = _point_estimate(pooled_arm)

    # ── Deterministic blocking + hoisted per-cutoff GEMM operands ────────────
    # ``quantum`` is a pure function of n_units + module constants (block_rows);
    # the scratch shape of (min(quantum, iterations), n_units) is therefore a pure
    # function of (iterations, n_units) — nothing machine- or env-dependent can
    # change the partition, so the float aggregates are byte-stable run-to-run.
    quantum = block_rows(panel.n_units)
    block_height = min(quantum, iterations)
    scratch = np.empty((block_height, panel.n_units), dtype=np.float64)
    # Hoisting prepared cutoffs is a pure perf/memory policy — consuming one is
    # bit-identical to the inline build (prepare_cutoff docstring), so this
    # branch can never move a number. Their k ≤ 5 value columns are
    # cap-INDEPENDENT overhead (8·k·n_present bytes per cutoff, vector_resample
    # memory contract), and holding ALL cutoffs multiplies that by n_cutoffs —
    # so the hoist gets only what the block working set (scratch + mask
    # temporaries, the same _ROW_TEMP_BYTES accounting block_rows spends) leaves
    # of ONE shared byte budget: the engine's live allocations stay under a
    # single DEFAULT_MAX_BLOCK_BYTES ceiling, never two additive ones
    # (adversarial review round 1). Past the leftover, every block re-prepares
    # per cutoff: bounded memory, re-touched bandwidth, identical bits.
    if panel.input_kind == "ratio" or (
        panel.input_kind == "sample" and panel.covariate is not None
    ):
        value_columns = 5
    else:
        value_columns = 2
    hoist_bytes = 8 * value_columns * sum(int(cut.unit_idx.size) for cut in panel.cutoffs)
    hoist_budget = DEFAULT_MAX_BLOCK_BYTES - _ROW_TEMP_BYTES * block_height * panel.n_units
    prepared: list[PreparedCutoff | None] | None = None
    if hoist_bytes <= hoist_budget:
        # An empty cutoff (no present units yet — load.py only guards the
        # HORIZON against emptiness) must not be prepared: build_arm_batch's own
        # n_present == 0 early-return never consults `prepared`, and preparing
        # it anyway np.mean()'s an empty array — a stray RuntimeWarning the
        # scorer's AbkitStatsWarning filter does not (and must not) swallow
        # (adversarial review round 2).
        prepared = [
            prepare_cutoff(panel.input_kind, cut, panel.covariate) if cut.unit_idx.size else None
            for cut in panel.cutoffs
        ]

    # ── Cross-block accumulators (python scalars + per-look histograms) ──────
    single_look_hits = 0
    peek_hits = 0
    valid_iterations = 0
    degenerate_horizon = 0
    power_hits = 0
    coverage_hits = 0
    coverage_n = 0
    exagg_values: list[float] = []
    mde_values: list[float] = []
    clamp_warned = False
    first_cross_at_look = [0] * n_cutoffs

    single_look_hits_seq = 0
    peek_hits_seq = 0
    power_hits_seq = 0
    coverage_hits_seq = 0
    exagg_values_seq: list[float] = []
    first_cross_at_look_seq = [0] * n_cutoffs
    width_fixed_sum = 0.0
    width_seq_sum = 0.0
    width_n = 0

    for block_start, block_size in iter_blocks(iterations, quantum):
        mask_block = placebo_mask_block(panel.n_units, share_a, seed_parts, block_start, block_size)

        # Streaming first-crossing state — the exact `_first_significant_look`
        # semantics: cutoffs arrive in grid order, so the first update per row IS
        # the first significant look. Rows that never cross stay `uncrossed` and
        # are excluded explicitly — argmax-on-all-False can never fabricate a
        # crossing at look 0 here (§WP4 footgun).
        uncrossed = np.ones(block_size, dtype=bool)
        first_idx = np.zeros(block_size, dtype=np.int64)
        first_eff = np.zeros(block_size, dtype=np.float64)
        uncrossed_seq = np.ones(block_size, dtype=bool)
        first_idx_seq = np.zeros(block_size, dtype=np.int64)
        first_eff_seq = np.zeros(block_size, dtype=np.float64)

        horizon_a = horizon_b = None
        horizon_res = None
        valid_h: np.ndarray | None = None
        sig_h: np.ndarray | None = None
        seq_sig_h: np.ndarray | None = None
        width_seq_h: np.ndarray | None = None

        for k, cut in enumerate(panel.cutoffs):
            arm_a, arm_b = build_arm_batch(
                panel.input_kind,
                cut,
                panel.covariate,
                mask_block,
                weights_scratch=scratch,
                prepared=None if prepared is None else prepared[k],
            )
            res = method.from_suffstats_array(arm_a.columns, arm_b.columns)
            # The scalar gap rule elementwise: an arm too small (degenerate) or a
            # non-finite CI bound is a gap, never a non-rejection (scoring rule +
            # ArmStatsBatch docstring). NaN comparisons are silently False.
            look_valid = (
                ~arm_a.degenerate
                & ~arm_b.degenerate
                & np.isfinite(res.left_bound)
                & np.isfinite(res.right_bound)
            )
            sig_col = look_valid & ((res.left_bound > 0.0) | (res.right_bound < 0.0))

            newly = uncrossed & sig_col
            if newly.any():
                first_idx[newly] = k
                first_eff[newly] = res.effect[newly]
                uncrossed[newly] = False

            if tau2 is not None:
                # The always-valid twin over the SAME per-look (effect, SE) —
                # rows whose widened bounds are non-finite mirror the scalar
                # `_always_valid_sig` → `_significance` None (a gap).
                se_col = se_from_ci_length_array(res.ci_length, alpha)
                lo_col, hi_col, _ = sequentialize_array(res.effect, se_col, tau2, alpha)
                seq_sig_col = (
                    look_valid
                    & np.isfinite(lo_col)
                    & np.isfinite(hi_col)
                    & ((lo_col > 0.0) | (hi_col < 0.0))
                )
                newly_seq = uncrossed_seq & seq_sig_col
                if newly_seq.any():
                    first_idx_seq[newly_seq] = k
                    first_eff_seq[newly_seq] = res.effect[newly_seq]
                    uncrossed_seq[newly_seq] = False
                if k == horizon_pos:
                    seq_sig_h = seq_sig_col
                    with np.errstate(invalid="ignore"):
                        width_seq_h = hi_col - lo_col

            if k == horizon_pos:
                horizon_a, horizon_b, horizon_res = arm_a, arm_b, res
                valid_h = look_valid
                sig_h = sig_col

        assert valid_h is not None and sig_h is not None  # the loop always visits horizon_pos
        assert horizon_a is not None and horizon_b is not None and horizon_res is not None

        # ── Horizon tallies (single-look FPR denominator = usable horizons) ──
        n_valid = int(valid_h.sum())
        valid_iterations += n_valid
        degenerate_horizon += block_size - n_valid
        single_look_hits += int(sig_h.sum())

        # Achieved MDE — reporting-only, `iterations`-shaped (valid horizon rows
        # only; ratio-delta has no analytic MDE, same as the scalar None branch).
        if panel.input_kind != "ratio" and n_valid:
            for i in np.flatnonzero(valid_h):
                control = _control_stats_from_row(
                    panel.input_kind,
                    panel.covariate is not None,
                    horizon_a.columns,
                    int(horizon_a.arm_sizes[i]),
                    int(i),
                )
                mde = _analytic_mde(control, method, ratio=ratio, target_power=target_power)
                if mde is not None:
                    mde_values.append(mde)

        # ── Peeking: first-crossing tallies (ALL iterations, not just valid-
        # horizon ones — the scalar loop counts a crossing even when the horizon
        # itself is degenerate) ──
        crossed = ~uncrossed
        n_crossed = int(crossed.sum())
        if n_crossed:
            peek_hits += n_crossed
            hist = np.bincount(first_idx[crossed], minlength=n_cutoffs)
            for k in range(n_cutoffs):
                first_cross_at_look[k] += int(hist[k])
            exagg_values.extend(np.abs(first_eff[crossed]).tolist())

        if tau2 is not None:
            assert seq_sig_h is not None and width_seq_h is not None
            # A gap seq horizon (seq_sig None scalar-side) counts as non-significant.
            single_look_hits_seq += int((valid_h & seq_sig_h).sum())
            width_mask = valid_h & np.isfinite(horizon_res.ci_length) & np.isfinite(width_seq_h)
            n_width = int(width_mask.sum())
            if n_width:
                width_fixed_sum += float(horizon_res.ci_length[width_mask].sum())
                width_seq_sum += float(width_seq_h[width_mask].sum())
                width_n += n_width
            crossed_seq = ~uncrossed_seq
            n_crossed_seq = int(crossed_seq.sum())
            if n_crossed_seq:
                peek_hits_seq += n_crossed_seq
                hist_seq = np.bincount(first_idx_seq[crossed_seq], minlength=n_cutoffs)
                for k in range(n_cutoffs):
                    first_cross_at_look_seq[k] += int(hist_seq[k])
                exagg_values_seq.extend(np.abs(first_eff_seq[crossed_seq]).tolist())

        # ── Injected pass (horizon only — `iterations`-shaped, valid rows only,
        # mirroring the scalar `horizon_control is not None` gate) ──
        if inject_effect is not None and n_valid:
            clamped = injection_clamped_columns(panel.input_kind, horizon_b.columns, inject_effect)
            if not clamp_warned and bool((clamped & valid_h).any()):
                warnings.append(
                    "injected effect saturated a proportion arm (count > nobs) — MDE unreachable"
                )
                clamp_warned = True
            b_inj = inject_multiplicative_columns(
                panel.input_kind, horizon_b.columns, inject_effect
            )
            res_inj = method.from_suffstats_array(horizon_a.columns, b_inj)
            inj_valid = valid_h & np.isfinite(res_inj.left_bound) & np.isfinite(res_inj.right_bound)
            coverage_n += int(inj_valid.sum())
            power_hits += int(
                (inj_valid & ((res_inj.left_bound > 0.0) | (res_inj.right_bound < 0.0))).sum()
            )

            # Truth anchor: the fixed pooled estimate when available (same scalar
            # helper); the per-row control value_1 fallback mirrors each method's
            # own value_1 (mean / prop / H5-guarded ratio) — reachable only for a
            # non-finite pooled ratio, since a degenerate pooled arm implies
            # degenerate per-iteration arms (no valid rows at all).
            truth: float | FloatArray
            if horizon_pooled is not None:
                truth = _injected_truth(method, inject_effect, horizon_pooled)
            else:
                test_type = method.test_type if "test_type" in method.params else "relative"
                if test_type == "relative":
                    truth = float(inject_effect)
                else:
                    truth = float(inject_effect) * _value_1_rows(
                        panel.input_kind, horizon_a.columns
                    )
            coverage_hits += int(
                (inj_valid & (res_inj.left_bound <= truth) & (truth <= res_inj.right_bound)).sum()
            )

            if tau2 is not None:
                # Same injected result, widened — power_seq gated on finite bounds
                # (the scalar sig_seq-is-not-None check); coverage_seq is NOT
                # (the scalar compares lo ≤ truth ≤ hi directly, NaN → False).
                se_inj = se_from_ci_length_array(res_inj.ci_length, alpha)
                lo_s, hi_s, _ = sequentialize_array(res_inj.effect, se_inj, tau2, alpha)
                power_hits_seq += int(
                    (
                        inj_valid
                        & np.isfinite(lo_s)
                        & np.isfinite(hi_s)
                        & ((lo_s > 0.0) | (hi_s < 0.0))
                    ).sum()
                )
                coverage_hits_seq += int((inj_valid & (lo_s <= truth) & (truth <= hi_s)).sum())

    # ── Final assembly — the scalar engine's tail, verbatim ──────────────────
    fpr = single_look_hits / valid_iterations if valid_iterations else None
    peeking_fpr = peek_hits / valid_iterations if valid_iterations else None
    peeking_curve: tuple[tuple[float, float], ...] = ()
    if valid_iterations:
        cumulative = 0
        curve: list[tuple[float, float]] = []
        for k, cut in enumerate(panel.cutoffs):
            cumulative += first_cross_at_look[k]
            curve.append((float(cut.elapsed_days), cumulative / valid_iterations))
        peeking_curve = tuple(curve)
    power = power_hits / coverage_n if coverage_n else None
    coverage = coverage_hits / coverage_n if coverage_n else None
    achieved_mde = float(np.mean(mde_values)) if mde_values else None
    effect_exaggeration = float(np.mean(exagg_values)) if exagg_values else None

    has_seq = tau2 is not None
    fpr_sequential = (
        single_look_hits_seq / valid_iterations if (has_seq and valid_iterations) else None
    )
    peeking_fpr_sequential = (
        peek_hits_seq / valid_iterations if (has_seq and valid_iterations) else None
    )
    power_sequential = power_hits_seq / coverage_n if (has_seq and coverage_n) else None
    coverage_sequential = coverage_hits_seq / coverage_n if (has_seq and coverage_n) else None
    effect_exaggeration_sequential = (
        float(np.mean(exagg_values_seq)) if (has_seq and exagg_values_seq) else None
    )
    ci_width = width_fixed_sum / width_n if (has_seq and width_n) else None
    ci_width_sequential = width_seq_sum / width_n if (has_seq and width_n) else None
    peeking_curve_sequential: tuple[tuple[float, float], ...] = ()
    if has_seq and valid_iterations:
        cumulative_seq = 0
        curve_seq: list[tuple[float, float]] = []
        for k, cut in enumerate(panel.cutoffs):
            cumulative_seq += first_cross_at_look_seq[k]
            curve_seq.append((float(cut.elapsed_days), cumulative_seq / valid_iterations))
        peeking_curve_sequential = tuple(curve_seq)

    if valid_iterations == 0:
        warnings.append(
            "no iteration produced a usable horizon cutoff — the population is too small to score"
        )
    if method.supports_sequential and not has_seq and valid_iterations:
        warnings.append(
            "always-valid column skipped — τ² could not be anchored (degenerate horizon)"
        )

    return CellScore(
        iterations=iterations,
        valid_iterations=valid_iterations,
        fpr=fpr,
        peeking_fpr=peeking_fpr,
        power=power,
        coverage=coverage,
        achieved_mde=achieved_mde,
        effect_exaggeration=effect_exaggeration,
        injected_effect=inject_effect,
        degenerate_horizon=degenerate_horizon,
        kept_grid_points=panel.kept_grid_points,
        total_grid_points=panel.total_grid_points,
        peeking_curve=peeking_curve,
        tau2=tau2,
        fpr_sequential=fpr_sequential,
        peeking_fpr_sequential=peeking_fpr_sequential,
        peeking_curve_sequential=peeking_curve_sequential,
        power_sequential=power_sequential,
        coverage_sequential=coverage_sequential,
        effect_exaggeration_sequential=effect_exaggeration_sequential,
        ci_width=ci_width,
        ci_width_sequential=ci_width_sequential,
        warnings=tuple(warnings),
    )


def _horizon_index(panel: PlaceboPanel) -> int:
    """The horizon cutoff's index (the flagged one, else the last)."""
    for k, cut in enumerate(panel.cutoffs):
        if cut.is_horizon:
            return k
    return len(panel.cutoffs) - 1


def _first_significant_look(
    sig_stream: list[tuple[int, tuple[bool, int], float]],
) -> tuple[int, float] | None:
    """The first informative cutoff whose CI excludes zero (D3).

    Optional stopping: the naive peeker stops the first time the chart's CI clears
    zero, in whichever direction. Returns ``(grid_look_index, effect)`` at that
    crossing (the index feeds the cumulative peeking curve; the effect the winner's
    curse), or ``None`` if the placebo never crosses significance across the grid.
    """
    for look_idx, sig, effect in sig_stream:
        if sig[0]:
            return (look_idx, effect)
    return None


def _point_estimate(arm: object) -> float | None:
    """The arm's scalar point estimate: mean (sample), proportion (fraction), ratio.

    ``ratio-delta`` DOES expose ``test_type`` and a live ``absolute`` branch, so the
    ratio kind is anchored too (the pooled ``mean_num/mean_den``). Returns ``None`` on a
    non-finite pooled ratio (zero denominator) so the caller falls back safely.
    """
    if isinstance(arm, Fraction):
        return float(arm.prop)
    if isinstance(arm, SufficientStats):
        return float(arm.mean)
    if isinstance(arm, RatioSufficientStats):
        # Guard BEFORE touching .ratio: it divides python floats, so an exactly-
        # zero pooled denominator mean raised ZeroDivisionError — which the
        # runner's per-cell isolation does NOT catch, aborting the whole matrix
        # instead of falling back to the per-iteration value_1 anchor as this
        # docstring always promised (adversarial review round 1). The zero/
        # non-finite check mirrors what _arm_linearisation applies to ARM means,
        # minus its H5 warning — the pooled anchor is silent by design. ±0.0
        # both take the guard; a denormal mean flows to an inf ratio and the
        # isfinite check below. No previously-scorable input moves.
        if arm.mean_den == 0.0 or not math.isfinite(arm.mean_den):
            return None
        return float(arm.ratio) if math.isfinite(arm.ratio) else None
    return None


def _injected_truth(method: BaseMethod, delta: float, pooled_estimate: float) -> float:
    """The true effect a multiplicative δ induces, in the method's estimand units (D2).

    Relative test_type → δ exactly (δ *is* the estimand). Absolute → δ·μ̂ where μ̂ is
    a FIXED, split-invariant estimate of the shared population mean (the pooled point
    estimate over all present horizon units) — NOT the realized control mean, which
    co-varies with the effect estimate and biases coverage low (m4 exit-gate review).
    """
    test_type = method.test_type if "test_type" in method.params else "relative"
    if test_type == "relative":
        return float(delta)
    return float(delta) * pooled_estimate


def _control_stats_from_row(
    input_kind: str,
    has_covariate: bool,
    columns: dict[str, FloatArray],
    arm_size: int,
    i: int,
) -> ArmStats:
    """One valid horizon row's control arm as a scalar container (the MDE seam).

    Feeds the unchanged ``_analytic_mde`` from the batch columns so the MDE
    formula path is shared, not duplicated. Only called on non-degenerate rows
    (finite columns, ``n ≥ MIN_ARM_UNITS``, ``nobs > 0``); never for the ratio
    kind (no analytic MDE). The Fraction ``count`` is clamped into ``[0, nobs]``:
    both are exact GEMM sums for integer-valued panels, but a fractional-valued
    panel could round ``count`` past ``nobs`` by one ULP where the scalar sums
    land exactly equal — there the scalar engine would CRASH the cell at
    ``Fraction`` construction while this clamp scores it with an ULP-shifted
    reporting-only value (a documented, strictly-friendlier divergence on a
    pathological input — adversarial review round 1).
    """
    if input_kind == "fraction":
        nobs = float(columns["nobs"][i])
        return Fraction(count=min(float(columns["count"][i]), nobs), nobs=nobs)
    if has_covariate:
        return SufficientStats(
            n=arm_size,
            mean=float(columns["mean"][i]),
            m2=float(columns["m2"][i]),
            cov_mean=float(columns["cov_mean"][i]),
            cov_m2=float(columns["cov_m2"][i]),
            cross_c=float(columns["cross_c"][i]),
        )
    return SufficientStats(n=arm_size, mean=float(columns["mean"][i]), m2=float(columns["m2"][i]))


def _value_1_rows(input_kind: str, columns: dict[str, FloatArray]) -> FloatArray:
    """Per-row control point estimates — the batch mirror of ``TestResult.value_1``.

    Matches each vectorized method's own ``value_1``: the raw control mean
    (t-test/CUPED), the control proportion (z-test), or the H5-guarded control
    ratio (ratio-delta: NaN when the denominator mean is zero/non-finite —
    ``_arm_linearisation``'s rule). Maps the WHOLE block, including degenerate
    NaN-poisoned rows (NaN in → NaN out, masked by the caller's ``inj_valid``).
    Used only as the absolute-effect truth anchor when the pooled horizon
    estimate is unavailable.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        if input_kind == "fraction":
            return cast(FloatArray, columns["count"] / columns["nobs"])
        if input_kind == "ratio":
            den = columns["mean_den"]
            return cast(
                FloatArray,
                np.where(np.isfinite(den) & (den != 0.0), columns["mean_num"] / den, np.nan),
            )
        return columns["mean"]
