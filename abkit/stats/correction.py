"""Multiple-testing corrections.

Config-time two-tier Bonferroni (baseline §6, declarative-config.md §6) plus the
two **read-time** schemes — Benjamini-Hochberg (FDR) and Holm (FWER), both opt-in
(statistics-changes.md §4, §4.3). The number of cumulative time points is
deliberately NOT part of the correction — peeking is handled honestly by
``abk validate`` and the sequential toggle, never hidden here.

A read-time scheme is a **step** procedure: whether a member is rejected depends
on the other members' p-values, so no fixed per-comparison level reproduces it
(m13 §1 STAT-1: with α=0.05, m=2, p₂=0.03, Holm rejects H₂ when p₁=0.001 and
refuses when p₁=0.9 — same p₂, opposite decisions). That is why these schemes
leave the compute-time alpha RAW (``analyze.effective_alphas``) and why the
stored interval and the decision may legitimately diverge (m13 D7, "Fork B").
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from abkit.stats.exceptions import MethodParamError

#: The declared comparison family (m13 STAT-1b). Stats-core keeps its own
#: literal because it may not import config (the purity invariant); the two are
#: pinned equal by ``tests/pipeline/test_contrast_set.py`` so a third family
#: cannot be added on one side only.
ContrastFamily = Literal["all_pairs", "vs_control"]

#: The correction schemes whose rejection rule needs the WHOLE family at read
#: time, and which therefore leave the compute-time alpha raw. Stats-core owns
#: this classification because ``composed_significance`` is the rule; every
#: caller that used to test ``!= "benjamini_hochberg"`` asks here instead, and
#: ``tests/pipeline/test_correction_rule.py`` asserts the union of this set and
#: the compute-time one EQUALS the config literal — a new scheme cannot be added
#: on one side only (the m12 NTF-1 roster-gate pattern).
READ_TIME_CORRECTIONS = frozenset({"benjamini_hochberg", "holm"})
#: The schemes fully applied when the row is written: the persisted CI already
#: carries the effective level, so read-time significance is "the CI excludes zero".
COMPUTE_TIME_CORRECTIONS = frozenset({"none", "bonferroni"})


def n_comparisons(
    groups_count: int, metrics_count: int = 1, contrasts: ContrastFamily = "all_pairs"
) -> float:
    """The declared comparison count: ``pairs(groups) × metrics``.

    ``contrasts`` names the family the experiment CLAIMS (m13 STAT-1b):
    ``all_pairs`` is the legacy ``C(groups, 2)``; ``vs_control`` is the
    ``groups − 1`` many-to-one contrasts against the control arm. Correcting
    for treatment-vs-treatment contrasts nobody makes costs a factor of
    ``g/2`` in level, so the count — not the correction rule — is where that
    power is recovered. The default reproduces the legacy transcription
    exactly, at every call site that never passes the argument.
    """
    if groups_count < 2:
        raise MethodParamError("Number of groups must be more than 1")
    if metrics_count < 1:
        raise MethodParamError(f"metrics_count must be >= 1, got {metrics_count}")
    if contrasts == "vs_control":
        pairs = float(groups_count - 1)
    elif contrasts == "all_pairs":
        pairs = groups_count * (groups_count - 1) / 2
    else:
        raise MethodParamError(f"contrasts must be 'all_pairs' or 'vs_control', got {contrasts!r}")
    return pairs * metrics_count


def adjust_alpha(
    alpha: float,
    groups_count: int,
    metrics_count: int = 1,
    contrasts: ContrastFamily = "all_pairs",
) -> float:
    """Bonferroni: ``alpha / (pairs(groups) × metrics)`` — legacy transcription."""
    if not 0.0 < alpha < 1.0:
        raise MethodParamError(f"alpha must be in (0, 1), got {alpha}")
    return alpha / n_comparisons(groups_count, metrics_count, contrasts)


@dataclass(frozen=True)
class TwoTierAlphas:
    """Effective per-comparison alphas, echoed by run/validate/report (inspectable).

    ``secondary`` is ``None`` for a main-metric-only experiment (``metrics_count=0``)
    — there is no secondary tier to divide the budget over.

    ``guardrail`` is ``None`` when guardrails share the secondary tier (the
    pre-0.8.0 behaviour, and every non-Bonferroni scheme, where all tiers already
    collapse to the raw alpha). It carries a level of its own only under
    ``guardrail_correction: none`` (m13 D8), in which case ``metrics_count``
    ALREADY excludes guardrails — the two go together, and the caller resolving
    one without the other gets a scheme that helps guardrails and loosens nothing.

    ``contrasts`` (m13 STAT-1b) rides along because ``groups_count`` alone no
    longer determines the pair count: every surface that prints or re-derives
    the divisor from this object must know WHICH family was paid for, and
    reading ``C(groups, 2)`` off ``groups_count`` would silently report the
    wrong one under ``vs_control``.
    """

    alpha: float
    groups_count: int
    metrics_count: int
    main: float
    secondary: float | None
    guardrail: float | None = None
    contrasts: ContrastFamily = "all_pairs"

    @property
    def pairs_count(self) -> float:
        """The declared variant-pair count these levels were divided by."""
        return n_comparisons(self.groups_count, 1, self.contrasts)


def two_tier_alphas(
    alpha: float,
    groups_count: int,
    metrics_count: int,
    guardrail_alpha: float | None = None,
    contrasts: ContrastFamily = "all_pairs",
) -> TwoTierAlphas:
    """The exact legacy two-tier scheme keyed off ``is_main_metric``.

    Main metric: ``adjust_alpha(alpha, groups, 1)``; every other metric:
    ``adjust_alpha(alpha, groups, metrics_count)`` where ``metrics_count`` counts
    the non-main metrics sharing the secondary budget (``0`` is valid — an
    experiment may have only its main metric).

    ``guardrail_alpha`` (m13 D8) is passed through verbatim onto the result: the
    caller decides whether guardrails have a tier of their own, because it is the
    same caller that must then EXCLUDE them from ``metrics_count``. This function
    deliberately does not derive one from the other — it has no idea which
    comparisons are guardrails.

    ``contrasts`` (m13 STAT-1b) selects the pair count both tiers divide by.
    The guardrail tier is deliberately NOT divided by it — it is the raw alpha
    by construction (D8), and a family it does not pay for cannot tighten it.
    """
    if metrics_count < 0:
        raise MethodParamError(f"metrics_count must be >= 0, got {metrics_count}")
    if guardrail_alpha is not None and not 0.0 < guardrail_alpha < 1.0:
        raise MethodParamError(f"guardrail_alpha must be in (0, 1), got {guardrail_alpha}")
    return TwoTierAlphas(
        alpha=alpha,
        groups_count=groups_count,
        metrics_count=metrics_count,
        main=adjust_alpha(alpha, groups_count, 1, contrasts),
        secondary=(
            None
            if metrics_count == 0
            else adjust_alpha(alpha, groups_count, metrics_count, contrasts)
        ),
        guardrail=guardrail_alpha,
        contrasts=contrasts,
    )


@dataclass(frozen=True)
class SignificanceInput:
    """One comparison's read-time significance inputs for the composed rule.

    Callers adapt their own objects (a persisted ``_ab_results`` row, a placebo
    ``TestResult``) to this primitive view. Bounds/pvalue/effect/alpha are ``None``
    when unavailable (a degenerate cutoff); the composed rule treats such members as
    non-significant and excludes them from the BH family.
    """

    left_bound: float | None
    right_bound: float | None
    pvalue: float | None
    effect: float | None
    alpha: float | None


@dataclass(frozen=True)
class Significance:
    """A member's composed-rule outcome: rejected? and the effect sign (+1/−1/0)."""

    significant: bool
    sign: int


def composed_significance(
    inputs: Sequence[SignificanceInput], correction: str
) -> list[Significance]:
    """The composed multiple-testing rule over ONE comparison family, shared by the
    readout and the A/A family sweep (m5-implementation-plan.md WP7/D12).

    Two-tier Bonferroni (and ``none``) is applied at COMPUTE time — the persisted CI
    already carries the effective per-comparison alpha — so here the rule is simply
    "the CI excludes zero", with the sign read off the bound. A read-time scheme
    (``READ_TIME_CORRECTIONS``) instead adjusts the family's p-values (only members
    with a finite p-value form the family; the rest are non-significant and excluded
    from ``m``) and rejects an adjusted p below the member's stored RAW alpha, with the
    sign read off the effect. Benjamini-Hochberg and Holm differ ONLY in the adjuster:
    the step-up/step-down arithmetic is the whole difference between controlling the
    FDR and controlling the FWER, and sharing this body is what keeps the readout, the
    A/A sweep and any future scheme applying one rule.

    An unknown scheme name takes the compute-time branch (the pre-m13 behaviour): the
    config literal is the gate that rejects a typo, and a rule that raised here would
    turn a stale persisted string into a crashing report.

    The caller decides the family membership — for the readout that is one cadence
    cutoff's rows; for the A/A sweep it is one iteration's per-metric marginals.

    Heterogeneous member alphas (a series whose rows were written under a different
    scheme) are handled per member, as BH has always done: the adjustment is over the
    family's p-values, the threshold is each member's own stored alpha. Under a
    read-time scheme every fresh row carries the same raw alpha by construction.
    """
    adjuster = _FAMILY_ADJUSTERS.get(correction)
    if adjuster is None:
        out: list[Significance] = []
        for item in inputs:
            if item.left_bound is not None and item.left_bound > 0:
                out.append(Significance(True, 1))
            elif item.right_bound is not None and item.right_bound < 0:
                out.append(Significance(True, -1))
            else:
                out.append(Significance(False, 0))
        return out

    # Read-time family: only finite-p members form the family (m excludes the rest).
    family_positions = [i for i, item in enumerate(inputs) if item.pvalue is not None]
    results = [Significance(False, 0)] * len(inputs)
    if not family_positions:
        return results
    adjusted = adjuster([inputs[i].pvalue for i in family_positions])
    for pos, adj in zip(family_positions, adjusted, strict=True):
        item = inputs[pos]
        significant = item.alpha is not None and float(adj) < item.alpha
        sign = 0
        if significant and item.effect is not None and item.effect != 0:
            sign = 1 if item.effect > 0 else -1
        if significant and sign == 0:  # a significant-but-zero-effect row cannot orient
            significant = False
        results[pos] = Significance(significant, sign)
    return results


def benjamini_hochberg(pvalues: npt.ArrayLike) -> npt.NDArray[np.float64]:
    """BH step-up adjusted p-values (monotone, capped at 1). Read-time, opt-in.

    Composition with peeking must be validated empirically via the A/A matrix
    before being applied to the cumulative daily series (statistics-changes.md §4).
    """
    p = np.asarray(pvalues, dtype=np.float64)
    if p.ndim != 1 or p.size == 0:
        raise MethodParamError("benjamini_hochberg expects a non-empty 1-d array of p-values")
    if np.any((p < 0) | (p > 1) | ~np.isfinite(p)):
        raise MethodParamError("p-values must be finite and within [0, 1]")
    m = p.size
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    adjusted_sorted = np.minimum.accumulate(ranked[::-1])[::-1]
    adjusted = np.empty(m, dtype=np.float64)
    adjusted[order] = np.minimum(adjusted_sorted, 1.0)
    return adjusted


def holm_adjusted(pvalues: npt.ArrayLike) -> npt.NDArray[np.float64]:
    """Holm step-down adjusted p-values (monotone, capped at 1). Read-time, opt-in.

    ``adj_(i) = max_{j<=i} (m − j + 1)·p_(j)`` over the ascending order; rejecting
    every member with ``adj < alpha`` is exactly Holm's sequential rule (reject
    ``H_(i)`` iff every earlier step also cleared its ``alpha/(m − j + 1)``), and it
    controls the FWER at ``alpha`` under **arbitrary** dependence — the same
    assumption-free guarantee Bonferroni gives, uniformly more powerful (m13 STAT-1).

    What it is NOT: a level abkit can hand a method at compute time. The running
    maximum is what makes it uniformly more powerful, and it is also what makes the
    rejection of one member depend on the others' p-values — see the module
    docstring's two-line proof. Consequently Holm's family level for the MAIN metric
    (``alpha/m`` at worst) is *tighter* than the two-tier scheme's main tier
    (``alpha/pairs``): the two-tier scheme buys that looseness by spending up to 2α
    across its tiers, which is exactly the claim m13 STAT-1 corrects.
    """
    p = np.asarray(pvalues, dtype=np.float64)
    if p.ndim != 1 or p.size == 0:
        raise MethodParamError("holm_adjusted expects a non-empty 1-d array of p-values")
    if np.any((p < 0) | (p > 1) | ~np.isfinite(p)):
        raise MethodParamError("p-values must be finite and within [0, 1]")
    m = p.size
    order = np.argsort(p)
    # multipliers m, m−1, …, 1 down the ascending order; the running MAXIMUM is the
    # step-down enforcement (a member cannot be rejected once an earlier step failed)
    ranked = p[order] * (m - np.arange(m))
    adjusted_sorted = np.maximum.accumulate(ranked)
    adjusted = np.empty(m, dtype=np.float64)
    adjusted[order] = np.minimum(adjusted_sorted, 1.0)
    return adjusted


#: Read-time schemes by name → their family p-value adjuster. Membership here is
#: what makes a scheme read-time; ``READ_TIME_CORRECTIONS`` is its key set, and the
#: two are asserted equal by the test roster so neither can drift.
_FAMILY_ADJUSTERS: dict[str, Callable[[npt.ArrayLike], npt.NDArray[np.float64]]] = {
    "benjamini_hochberg": benjamini_hochberg,
    "holm": holm_adjusted,
}
