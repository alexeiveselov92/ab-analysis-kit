"""The composed multi-metric FWER/FDR family sweep (m5-implementation-plan.md WP8, D12).

M4 validated only the per-cell peeking FPR at the correct two-tier alphas; D9 closes the
family-level loop — the empirical **family-wise error rate** (any false rejection across
the metric family, controlled by compute-time two-tier Bonferroni) and **false-discovery
rate** (expected false fraction among rejections, controlled by read-time Benjamini-
Hochberg) under the SAME composed rule the readout applies (``stats.correction.
composed_significance``, WP7).

Shared-mask semantics (D11): each iteration draws **one** unit→arm assignment over the
**union** of the metrics' cohorts (the real single-assignment-per-unit semantics), and
every metric is scored on the units for which it is defined under that shared assignment.
A unit present in metric A but absent in B simply does not contribute to B — no imputation
(that would bias FDR). Aligning the assignment across metrics with different cohorts is
what ``PlaceboPanel.unit_ids`` is for.

A rejection on a **null** metric (no injected effect) is a **false** discovery; on a
**planted** metric it is a true one. Under the complete null (no planted metric) every
rejection is false, so FWER and FDR coincide by construction — the honest identity. A
planted effect in one metric must leave the OTHER metrics' family error controlled (D12).

Fixed-horizon only in M5 (sequential × composed → M6). Pure: numpy + ``abkit.stats`` +
the validate resample/inject helpers; no DB, no clock. Seeds are always derived
(``derive_seed``) — byte-reproducible, never wall-clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from abkit.stats.base import BaseMethod
from abkit.stats.correction import SignificanceInput, composed_significance
from abkit.stats.rng import derive_seed
from abkit.validate._types import ValidateError
from abkit.validate.inject import inject_multiplicative, injection_clamped
from abkit.validate.panel import PlaceboPanel
from abkit.validate.resample import build_arm, placebo_mask, present_positions


@dataclass(frozen=True)
class FamilyMember:
    """One metric in the composed family: its panel, bound method, and effective alpha."""

    metric: str
    panel: PlaceboPanel
    method: BaseMethod
    alpha: float
    planted: bool = False  # inject_effect goes into this metric's treatment arm (a true positive)


@dataclass(frozen=True)
class FamilyScore:
    """The composed family sweep result (persisted as a sentinel row, D9)."""

    correction: str
    n_metrics: int
    n_null_metrics: int
    planted_metrics: tuple[str, ...]
    iterations: int
    #: Iterations with at least one scorable member (the FWER/FDR denominator).
    valid_iterations: int
    #: P(≥1 FALSE rejection across the family) — the empirical family-wise error rate.
    fwer: float | None
    #: Mean false-discovery proportion — false rejections / max(1, total rejections).
    fdr: float | None
    #: P(≥1 rejection of any kind) — a diagnostic (equals FWER under the complete null).
    any_rejection_rate: float | None
    #: The family FWER budget the verdict judges against (generalized ``aa_fpr_budget``).
    budget: float | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def over_budget(self) -> bool:
        return self.fwer is not None and self.budget is not None and self.fwer > self.budget


def _horizon_cutoff(panel: PlaceboPanel):
    """The horizon cutoff (the superset of every earlier cumulative look)."""
    for cut in reversed(panel.cutoffs):
        if cut.is_horizon:
            return cut
    return panel.cutoffs[-1]


def _finite(value: float | None) -> float | None:
    """A bound/effect → float, or None when non-finite (a degenerate look — a gap)."""
    if value is None:
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _member_marginal(
    member: FamilyMember, panel_mask: np.ndarray, inject_effect: float | None
) -> SignificanceInput:
    """Score one member at its horizon under the shared assignment → a SignificanceInput.

    Returns an all-``None`` input (a gap, non-significant and excluded from the BH family)
    when either arm is too small or the CI is degenerate — never a zero, never a crash.
    """
    panel = member.panel
    cut = _horizon_cutoff(panel)
    pos_a, pos_b = present_positions(panel_mask, cut.unit_idx)
    arm_a = build_arm(
        panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_a
    )
    arm_b = build_arm(
        panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_b
    )
    if arm_a is None or arm_b is None:
        return SignificanceInput(None, None, None, None, member.alpha)
    if member.planted and inject_effect is not None:
        arm_b = inject_multiplicative(arm_b, inject_effect)  # a true effect in the treatment arm
    try:
        result = member.method.from_suffstats(arm_a, arm_b)
    except Exception:  # a degenerate arm — a gap for this iteration, never a crash
        return SignificanceInput(None, None, None, None, member.alpha)
    return SignificanceInput(
        left_bound=_finite(result.left_bound),
        right_bound=_finite(result.right_bound),
        pvalue=_finite(result.pvalue),
        effect=_finite(result.effect),
        alpha=member.alpha,
    )


def sweep_family(
    members: list[FamilyMember],
    *,
    correction: str,
    iterations: int,
    share_a: float,
    seed_parts: tuple[object, ...],
    inject_effect: float | None = None,
    budget: float | None = None,
) -> FamilyScore:
    """Empirical composed FWER/FDR over the metric family across ``iterations`` shared splits.

    Each iteration: draw ONE arm assignment over the union of the members' cohorts, score
    every member at its horizon under that shared assignment, apply
    :func:`composed_significance`, and tally the family error. ``inject_effect`` (with a
    member's ``planted=True``) plants a true effect so the sweep can show the null members
    stay controlled (D12); with no planted member the sweep is the complete null and FWER
    == FDR by construction.
    """
    if iterations <= 0:
        raise ValidateError(f"iterations must be positive, got {iterations}")
    if not members:
        raise ValidateError("the family sweep needs at least one member")
    for member in members:
        if member.panel.unit_ids is None:
            raise ValidateError(
                f"metric '{member.metric}': panel has no unit_ids (rebuild the panel — WP8)"
            )
        if not member.panel.cutoffs:
            raise ValidateError(f"metric '{member.metric}': panel has no cutoffs")

    # The union of unit ids over the whole family — the shared unit universe (D11). Each
    # member's position in the union mask is precomputed once (a unit's arm is a function
    # of its id, constant across metrics).
    union_units = np.array(
        sorted({u for m in members for u in m.panel.unit_ids.tolist()}), dtype=object
    )
    union_index = {u: i for i, u in enumerate(union_units.tolist())}
    member_union_pos = [
        np.array([union_index[u] for u in m.panel.unit_ids.tolist()], dtype=np.int64)
        for m in members
    ]
    n_union = int(union_units.size)

    planted = tuple(m.metric for m in members if m.planted)
    n_null = sum(1 for m in members if not m.planted)
    warnings: list[str] = []
    if n_null == 0:
        warnings.append("every metric is planted — no null member, so FWER/FDR are trivially 0")

    fwer_hits = 0
    any_rej_hits = 0
    fdp_sum = 0.0
    valid_iterations = 0
    clamp_warned = False
    # per-member scorable-iteration counts: a member whose cohort is too small to ever
    # split ≥2 units/arm is a persistent gap — it must not silently ride in the family
    # verdict as if it were validated (M5 exit-gate round-2 finding).
    member_scored = [0] * len(members)

    for i in range(iterations):
        union_mask = placebo_mask(n_union, share_a, derive_seed(*seed_parts, i))
        inputs: list[SignificanceInput] = []
        scorable = False
        for j, (member, pos) in enumerate(zip(members, member_union_pos, strict=True)):
            if (
                member.planted
                and inject_effect is not None
                and not clamp_warned
                and _injection_saturates(member, union_mask[pos], inject_effect)
            ):
                warnings.append(
                    f"metric '{member.metric}': injected effect saturates the proportion "
                    "(clamped) — planted power is understated"
                )
                clamp_warned = True
            marginal = _member_marginal(member, union_mask[pos], inject_effect)
            if marginal.left_bound is not None or marginal.pvalue is not None:
                scorable = True
                member_scored[j] += 1
            inputs.append(marginal)
        if not scorable:
            continue
        valid_iterations += 1

        outcomes = composed_significance(inputs, correction)
        rejections = [j for j, o in enumerate(outcomes) if o.significant]
        false_rejections = [j for j in rejections if not members[j].planted]
        total = len(rejections)
        if false_rejections:
            fwer_hits += 1
        if total:
            any_rej_hits += 1
        fdp_sum += (len(false_rejections) / total) if total else 0.0

    # surface any member that never scored — it contributes 0 to the family error yet
    # rides in n_metrics/the verdict, which would otherwise overstate coverage silently.
    for member, scored in zip(members, member_scored, strict=True):
        if scored == 0:
            warnings.append(
                f"metric '{member.metric}': scored in 0/{iterations} iterations "
                "(cohort too small to split ≥2 units/arm) — excluded from the family error"
            )

    if valid_iterations == 0:
        return FamilyScore(
            correction=correction,
            n_metrics=len(members),
            n_null_metrics=n_null,
            planted_metrics=planted,
            iterations=iterations,
            valid_iterations=0,
            fwer=None,
            fdr=None,
            any_rejection_rate=None,
            budget=budget,
            warnings=(*warnings, "no iteration produced a scorable family"),
        )
    return FamilyScore(
        correction=correction,
        n_metrics=len(members),
        n_null_metrics=n_null,
        planted_metrics=planted,
        iterations=iterations,
        valid_iterations=valid_iterations,
        fwer=fwer_hits / valid_iterations,
        fdr=fdp_sum / valid_iterations,
        any_rejection_rate=any_rej_hits / valid_iterations,
        budget=budget,
        warnings=tuple(warnings),
    )


def _injection_saturates(
    member: FamilyMember, panel_mask: np.ndarray, inject_effect: float
) -> bool:
    """True when this iteration's treatment arm would clamp under injection (Fraction)."""
    panel = member.panel
    cut = _horizon_cutoff(panel)
    _pos_a, pos_b = present_positions(panel_mask, cut.unit_idx)
    arm_b = build_arm(
        panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_b
    )
    return arm_b is not None and injection_clamped(arm_b, inject_effect)
