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
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from abkit.stats.base import BaseMethod
from abkit.stats.power import cuped_adjusted_std, get_fraction_mde, get_ttest_mde
from abkit.stats.rng import derive_seed
from abkit.stats.samples import Fraction, RatioSufficientStats, SufficientStats
from abkit.validate._types import ValidateError
from abkit.validate.inject import inject_multiplicative, injection_clamped
from abkit.validate.panel import PlaceboPanel
from abkit.validate.resample import ArmStats, build_arm, placebo_mask, present_positions

#: Default target power for the achieved-MDE column.
DEFAULT_TARGET_POWER = 0.8


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
    """
    if iterations <= 0:
        raise ValidateError(f"iterations must be positive, got {iterations}")
    if not panel.cutoffs:
        raise ValidateError("panel has no cutoffs")

    horizon_pos = _horizon_index(panel)
    ratio = (1.0 - share_a) / share_a  # n_treatment / n_control at the split
    warnings: list[str] = []

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

    for i in range(iterations):
        seed = derive_seed(*seed_parts, i)
        mask = placebo_mask(panel.n_units, share_a, seed)

        # ── Null pass over the grid ──────────────────────────────────────────
        sig_stream: list[tuple[int, tuple[bool, int], float]] = []  # (look_idx, sig, effect)
        horizon_control: ArmStats | None = None
        horizon_sig: tuple[bool, int] | None = None

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

        # Cumulative-peeking FPR: optional stopping — the first look whose CI excludes
        # zero (a false winner under the A/A null). The horizon look is included, so
        # peeking is monotonically ≥ the single-look FPR.
        first_call = _first_significant_look(sig_stream)
        if first_call is not None:
            first_idx, first_effect = first_call
            peek_hits += 1
            first_cross_at_look[first_idx] += 1
            exagg_values.append(abs(first_effect))

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

    if valid_iterations == 0:
        warnings.append(
            "no iteration produced a usable horizon cutoff — the population is too small to score"
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
