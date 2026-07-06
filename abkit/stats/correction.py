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


def n_comparisons(groups_count: int, metrics_count: int = 1) -> float:
    """``C(groups, 2) × metrics`` — the legacy pairwise comparison count."""
    if groups_count < 2:
        raise MethodParamError("Number of groups must be more than 1")
    if metrics_count < 1:
        raise MethodParamError(f"metrics_count must be >= 1, got {metrics_count}")
    return groups_count * (groups_count - 1) / 2 * metrics_count


def adjust_alpha(alpha: float, groups_count: int, metrics_count: int = 1) -> float:
    """Bonferroni: ``alpha / (C(groups, 2) × metrics)`` — legacy transcription."""
    if not 0.0 < alpha < 1.0:
        raise MethodParamError(f"alpha must be in (0, 1), got {alpha}")
    return alpha / n_comparisons(groups_count, metrics_count)


@dataclass(frozen=True)
class TwoTierAlphas:
    """Effective per-comparison alphas, echoed by run/validate/report (inspectable).

    ``secondary`` is ``None`` for a main-metric-only experiment (``metrics_count=0``)
    — there is no secondary tier to divide the budget over.
    """

    alpha: float
    groups_count: int
    metrics_count: int
    main: float
    secondary: float | None


def two_tier_alphas(alpha: float, groups_count: int, metrics_count: int) -> TwoTierAlphas:
    """The exact legacy two-tier scheme keyed off ``is_main_metric``.

    Main metric: ``adjust_alpha(alpha, groups, 1)``; every other metric:
    ``adjust_alpha(alpha, groups, metrics_count)`` where ``metrics_count`` counts
    the non-main metrics sharing the secondary budget (``0`` is valid — an
    experiment may have only its main metric).
    """
    if metrics_count < 0:
        raise MethodParamError(f"metrics_count must be >= 0, got {metrics_count}")
    return TwoTierAlphas(
        alpha=alpha,
        groups_count=groups_count,
        metrics_count=metrics_count,
        main=adjust_alpha(alpha, groups_count, 1),
        secondary=(
            None if metrics_count == 0 else adjust_alpha(alpha, groups_count, metrics_count)
        ),
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
