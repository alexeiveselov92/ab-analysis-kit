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

With ``sequential=True`` (WP-B) the sweep mirrors the per-cell D8 trio at the family
level, composing three matched families over the SAME shared assignments:

* ``fwer``/``fdr`` — the shipped **single-look** family error (each member judged at its
  horizon with the fixed CI; the readout's honest fixed-decision point). Unchanged.
* ``fwer_peeking``/``fdr_peeking`` — the **fixed-CI peeking** family error (each member's
  fixed CI peeked across every look; the composed optional-stopping hazard — inflated).
* ``fwer_sequential``/``fdr_sequential`` — the **always-valid peeking** twin (each
  member's always-valid CI peeked across every look), composed by the SAME rule, so where
  the fixed peeking column breaks budget this one returns to ≈ the composed nominal.

The fixed-peeking baseline and its always-valid twin are a matched pair (the report's
"peeking Y% → always-valid Z%" recovery story), computed in ONE walk per member and gated
together on a sequential-eligible family (≥1 member has a frozen τ²). It reuses the D8
estimator (the frozen first-usable-look τ² + ``sequentialize``) — one estimator, not a
second. Pure: numpy + ``abkit.stats`` + the validate resample/inject/scoring helpers; no
DB, no clock. Seeds are always derived (``derive_seed``) — byte-reproducible, never
wall-clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from abkit.stats.base import BaseMethod
from abkit.stats.correction import SignificanceInput, composed_significance
from abkit.stats.rng import derive_seed
from abkit.stats.sequential import se_from_ci_length, sequentialize
from abkit.validate._types import ValidateError
from abkit.validate.inject import inject_multiplicative, injection_clamped
from abkit.validate.panel import PlaceboPanel
from abkit.validate.resample import build_arm, placebo_mask, present_positions
from abkit.validate.scoring import _cell_tau2, suppress_resample_warnings


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
    #: The FIXED-CI PEEKING composed family error (D8×D9, WP-B) — each member's fixed CI
    #: peeked across ALL looks (reject at the first look excluding zero), composed by the
    #: SAME rule. The family-level optional-stopping HAZARD: inflated above ``fwer`` because
    #: peeking includes every earlier look. The matched "before" of the recovery story.
    #: ``None`` when the family is not sequential-eligible (no member has a τ²) — the
    #: peeking pair is then absent, never zero-filled.
    fwer_peeking: float | None = None
    fdr_peeking: float | None = None
    any_rejection_rate_peeking: float | None = None
    #: The ALWAYS-VALID (peeking) composed family error — the sequential twin of
    #: ``fwer_peeking`` (D8×D9, WP-B). Each member's marginal is its always-valid CI peeked
    #: across ALL looks, composed by the SAME rule; so where the fixed peeking column breaks
    #: budget, this one returns to ≈ the composed nominal. ``None`` when no member has a
    #: sequential column — the column is then absent, never zero-filled. (The only
    #: sequential-ineligible methods are the bootstrap family, which cannot be scored from
    #: suffstats, so they are a full gap in every family — not a fixed-peeking-only rider.)
    fwer_sequential: float | None = None
    fdr_sequential: float | None = None
    any_rejection_rate_sequential: float | None = None

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


class _Peek:
    """A running peeking accumulator — first-crossing bounds, latest fallback, min p."""

    __slots__ = ("cross", "latest", "min_p")

    def __init__(self) -> None:
        self.cross: tuple[float, float, float | None] | None = None  # (lo, hi, eff) @ 1st cross
        self.latest: tuple[float, float, float | None] | None = None  # latest usable look
        self.min_p: float | None = None

    def observe(self, lo: float, hi: float, effect: float | None, pvalue: float) -> None:
        """Fold one look's interval into the peeking decision (bounds already finite)."""
        self.latest = (lo, hi, effect)
        if math.isfinite(pvalue):
            self.min_p = pvalue if self.min_p is None else min(self.min_p, pvalue)
        if self.cross is None and (lo > 0.0 or hi < 0.0):  # first look excluding zero
            self.cross = (lo, hi, effect)

    def to_input(self, alpha: float | None) -> SignificanceInput:
        """The peeking marginal → a :class:`SignificanceInput` for the composed rule.

        The FIRST-crossing look's bounds (a peeking rejection, with sign) else the latest
        usable look's bounds (not significant); the p-value is the MIN across looks (the
        peeking p read-time BH consumes). All-``None`` (a gap) when no look was usable.
        """
        chosen = self.cross if self.cross is not None else self.latest
        if chosen is None:
            return SignificanceInput(None, None, None, None, alpha)
        lo, hi, eff = chosen
        return SignificanceInput(
            left_bound=lo, right_bound=hi, pvalue=self.min_p, effect=eff, alpha=alpha
        )


def _member_peeked_marginals(
    member: FamilyMember,
    panel_mask: np.ndarray,
    inject_effect: float | None,
    tau2: float | None,
) -> tuple[SignificanceInput, SignificanceInput | None]:
    """The member's (fixed-CI, always-valid) PEEKING marginals under the shared assignment.

    ONE walk over the member's looks yields both matched marginals (WP-B):

    * fixed peeking — the raw fixed CI (already at ``member.alpha``) peeked across looks:
      the family-level optional-stopping hazard.
    * always-valid peeking — the same per-look ``(effect, SE)`` widened by ``sequentialize``
      at the frozen cell ``tau2`` (the SAME D8 estimator via ``se_from_ci_length``), peeked
      across looks: the controlled twin. ``None`` when ``tau2`` is ``None`` (a
      sequential-ineligible member). The fixed marginal is computed independent of ``tau2``;
      a member unscorable via suffstats (the bootstrap family, which raises in
      ``from_suffstats``) simply returns an all-``None`` fixed marginal too — a full gap.

    ``tau2`` is the caller's frozen per-cell mixture variance (never per-look — Ville needs
    a prior fixed in advance). The composed rule (:func:`composed_significance`) then applies
    to each marginal unchanged.
    """
    panel = member.panel
    alpha = member.method.alpha  # the CI's own alpha (== member.alpha) — correct CI-inversion
    fixed = _Peek()
    av = _Peek() if tau2 is not None else None
    for cut in panel.cutoffs:
        pos_a, pos_b = present_positions(panel_mask, cut.unit_idx)
        arm_a = build_arm(
            panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_a
        )
        arm_b = build_arm(
            panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_b
        )
        if arm_a is None or arm_b is None:
            continue
        if member.planted and inject_effect is not None:
            arm_b = inject_multiplicative(arm_b, inject_effect)
        try:
            result = member.method.from_suffstats(arm_a, arm_b)
        except Exception:  # a degenerate arm at this look — a gap, never a crash
            continue
        eff = _finite(result.effect)
        lo_f, hi_f = result.left_bound, result.right_bound
        if math.isfinite(lo_f) and math.isfinite(hi_f):
            fixed.observe(lo_f, hi_f, eff, result.pvalue)
        if av is not None:
            se = se_from_ci_length(result.ci_length, alpha)
            lo_a, hi_a, av_p = sequentialize(result.effect, se, tau2, alpha)
            if math.isfinite(lo_a) and math.isfinite(hi_a):
                av.observe(lo_a, hi_a, eff, av_p)
    return fixed.to_input(member.alpha), (av.to_input(member.alpha) if av is not None else None)


@suppress_resample_warnings
def sweep_family(
    members: list[FamilyMember],
    *,
    correction: str,
    iterations: int,
    share_a: float,
    seed_parts: tuple[object, ...],
    inject_effect: float | None = None,
    budget: float | None = None,
    sequential: bool = False,
) -> FamilyScore:
    """Empirical composed FWER/FDR over the metric family across ``iterations`` shared splits.

    Each iteration: draw ONE arm assignment over the union of the members' cohorts, score
    every member at its horizon under that shared assignment, apply
    :func:`composed_significance`, and tally the family error. ``inject_effect`` (with a
    member's ``planted=True``) plants a true effect so the sweep can show the null members
    stay controlled (D12); with no planted member the sweep is the complete null and FWER
    == FDR by construction.

    ``sequential=True`` (WP-B) additionally composes the peeking pair over the SAME
    assignments — the fixed-CI peeking family error (the optional-stopping hazard) and its
    always-valid twin — populating ``fwer_peeking``/``fwer_sequential`` (and their FDR/
    any-rejection siblings). Gated on a sequential-eligible family (≥1 member has a τ²);
    otherwise those columns stay ``None`` and only the shipped single-look family is scored.
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

    # WP-B: the frozen per-member mixture variance τ² for the always-valid (peeking)
    # composed twin — ONE constant per cell (Ville needs a prior fixed in advance), reusing
    # the SAME first-usable-look anchor + mixture helper as the D8 sequential column. None
    # for a sequential-ineligible member (e.g. bootstrap) → it is a gap in the always-valid
    # family (still rides in the fixed-peeking hazard), never zero-filled. peek_active gates
    # BOTH peeking families (the fixed hazard + its twin are a matched pair) — false unless
    # ≥1 member has a τ² (an all-ineligible family shows only the shipped single-look column).
    member_tau2: list[float | None] = (
        [
            _cell_tau2(
                m.panel,
                m.method,
                share_a=share_a,
                anchor_seed=derive_seed(*seed_parts, "seq-anchor", m.metric),
            )
            for m in members
        ]
        if sequential
        else [None] * len(members)
    )
    peek_active = sequential and any(t is not None for t in member_tau2)

    planted = tuple(m.metric for m in members if m.planted)
    n_null = sum(1 for m in members if not m.planted)
    warnings: list[str] = []
    if n_null == 0:
        warnings.append("every metric is planted — no null member, so FWER/FDR are trivially 0")
    # NB: a sequential-ineligible member (τ² is None) is honestly disclosed by the
    # "scored in 0 iterations — excluded from the family error" warning below — the only
    # ineligible methods are the bootstrap family, which cannot be scored from suffstats at
    # all (they need per-unit samples), so they are a full gap in every family, not a
    # fixed-peeking-only rider. No separate "no always-valid option" note is emitted.

    fwer_hits = 0
    any_rej_hits = 0
    fdp_sum = 0.0
    valid_iterations = 0
    peek_fwer_hits = 0
    peek_any_hits = 0
    peek_fdp_sum = 0.0
    peek_valid_iterations = 0
    seq_fwer_hits = 0
    seq_any_hits = 0
    seq_fdp_sum = 0.0
    seq_valid_iterations = 0
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
        if scorable:
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

        # WP-B: the peeking pair — the fixed-CI hazard and its always-valid twin — tallied
        # over the SAME shared assignment. One walk per member yields both marginals; the
        # composed rule is identical (only the marginals differ), and each family carries its
        # own scorability gate (a sequential-ineligible member is a gap in the twin only).
        if peek_active:
            fixed_inputs: list[SignificanceInput] = []
            seq_inputs: list[SignificanceInput] = []
            for member, pos, tau2 in zip(members, member_union_pos, member_tau2, strict=True):
                fx, av = _member_peeked_marginals(member, union_mask[pos], inject_effect, tau2)
                fixed_inputs.append(fx)
                seq_inputs.append(
                    av
                    if av is not None
                    else SignificanceInput(None, None, None, None, member.alpha)
                )
            if any(s.left_bound is not None or s.pvalue is not None for s in fixed_inputs):
                peek_valid_iterations += 1
                fx_out = composed_significance(fixed_inputs, correction)
                fx_rej = [j for j, o in enumerate(fx_out) if o.significant]
                fx_false = [j for j in fx_rej if not members[j].planted]
                if fx_false:
                    peek_fwer_hits += 1
                if fx_rej:
                    peek_any_hits += 1
                peek_fdp_sum += (len(fx_false) / len(fx_rej)) if fx_rej else 0.0
            if any(s.left_bound is not None or s.pvalue is not None for s in seq_inputs):
                seq_valid_iterations += 1
                seq_out = composed_significance(seq_inputs, correction)
                seq_rej = [j for j, o in enumerate(seq_out) if o.significant]
                seq_false = [j for j in seq_rej if not members[j].planted]
                if seq_false:
                    seq_fwer_hits += 1
                if seq_rej:
                    seq_any_hits += 1
                seq_fdp_sum += (len(seq_false) / len(seq_rej)) if seq_rej else 0.0

    # surface any member that never scored — it contributes 0 to the family error yet
    # rides in n_metrics/the verdict, which would otherwise overstate coverage silently.
    for member, scored in zip(members, member_scored, strict=True):
        if scored == 0:
            warnings.append(
                f"metric '{member.metric}': scored in 0/{iterations} iterations "
                "(cohort too small to split ≥2 units/arm) — excluded from the family error"
            )

    fwer = fwer_hits / valid_iterations if valid_iterations else None
    fdr = fdp_sum / valid_iterations if valid_iterations else None
    any_rate = any_rej_hits / valid_iterations if valid_iterations else None
    # the peeking pair (None unless a sequential-eligible member scored — the columns are
    # then absent, never zero-filled): the fixed-CI hazard and its always-valid twin.
    peek_fwer = peek_fwer_hits / peek_valid_iterations if peek_valid_iterations else None
    peek_fdr = peek_fdp_sum / peek_valid_iterations if peek_valid_iterations else None
    peek_any = peek_any_hits / peek_valid_iterations if peek_valid_iterations else None
    seq_fwer = seq_fwer_hits / seq_valid_iterations if seq_valid_iterations else None
    seq_fdr = seq_fdp_sum / seq_valid_iterations if seq_valid_iterations else None
    seq_any = seq_any_hits / seq_valid_iterations if seq_valid_iterations else None
    if valid_iterations == 0 and seq_valid_iterations == 0 and peek_valid_iterations == 0:
        warnings.append("no iteration produced a scorable family")
    return FamilyScore(
        correction=correction,
        n_metrics=len(members),
        n_null_metrics=n_null,
        planted_metrics=planted,
        iterations=iterations,
        valid_iterations=valid_iterations,
        fwer=fwer,
        fdr=fdr,
        any_rejection_rate=any_rate,
        budget=budget,
        warnings=tuple(warnings),
        fwer_peeking=peek_fwer,
        fdr_peeking=peek_fdr,
        any_rejection_rate_peeking=peek_any,
        fwer_sequential=seq_fwer,
        fdr_sequential=seq_fdr,
        any_rejection_rate_sequential=seq_any,
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
