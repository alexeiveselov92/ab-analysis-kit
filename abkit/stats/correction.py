"""Multiple-testing corrections.

Config-time two-tier Bonferroni (baseline §6, declarative-config.md §6) plus
read-time Benjamini-Hochberg (opt-in, statistics-changes.md §4). The number of
cumulative time points is deliberately NOT part of the correction — peeking is
handled honestly by ``abk validate`` and the sequential toggle, never hidden here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from abkit.stats.exceptions import MethodParamError


def n_comparisons(
    groups_count: int, metrics_count: int = 1, contrasts: str = "all_pairs"
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
        raise MethodParamError(
            f"contrasts must be 'all_pairs' or 'vs_control', got {contrasts!r}"
        )
    return pairs * metrics_count


def adjust_alpha(
    alpha: float, groups_count: int, metrics_count: int = 1, contrasts: str = "all_pairs"
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
    contrasts: str = "all_pairs"

    @property
    def pairs_count(self) -> float:
        """The declared variant-pair count these levels were divided by."""
        return n_comparisons(self.groups_count, 1, self.contrasts)


def two_tier_alphas(
    alpha: float,
    groups_count: int,
    metrics_count: int,
    guardrail_alpha: float | None = None,
    contrasts: str = "all_pairs",
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
    "the CI excludes zero", with the sign read off the bound. Read-time
    Benjamini-Hochberg adjusts the family's p-values (only members with a finite
    p-value form the family; the rest are non-significant and excluded from ``m``) and
    rejects an adjusted p below the member's stored RAW alpha, with the sign read off
    the effect. This is the exact rule the readout's ``_build_sig_map`` applied inline;
    extracting it lets WP8's composed FWER/FDR sweep apply the identical rule.

    The caller decides the family membership — for the readout that is one cadence
    cutoff's rows; for the A/A sweep it is one iteration's per-metric marginals.
    """
    if correction != "benjamini_hochberg":
        out: list[Significance] = []
        for item in inputs:
            if item.left_bound is not None and item.left_bound > 0:
                out.append(Significance(True, 1))
            elif item.right_bound is not None and item.right_bound < 0:
                out.append(Significance(True, -1))
            else:
                out.append(Significance(False, 0))
        return out

    # Benjamini-Hochberg: only finite-p members form the family (m excludes the rest).
    family_positions = [i for i, item in enumerate(inputs) if item.pvalue is not None]
    results = [Significance(False, 0)] * len(inputs)
    if not family_positions:
        return results
    adjusted = benjamini_hochberg([inputs[i].pvalue for i in family_positions])
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
