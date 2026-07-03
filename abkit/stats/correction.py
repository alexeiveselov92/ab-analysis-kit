"""Multiple-testing corrections.

Config-time two-tier Bonferroni (baseline §6, declarative-config.md §6) plus
read-time Benjamini-Hochberg (opt-in, statistics-changes.md §4). The number of
cumulative time points is deliberately NOT part of the correction — peeking is
handled honestly by ``abk validate`` and the sequential toggle, never hidden here.
"""

from __future__ import annotations

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
