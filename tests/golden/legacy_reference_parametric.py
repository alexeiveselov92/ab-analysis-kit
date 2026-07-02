"""VERBATIM transcription of the legacy A/B engine — the golden-test reference.

Transcribed ONLY from the frozen documents (never from ``abkit`` source):

- docs/specs/statistics-baseline.md — §3 parametric tests, §6 power/alpha;
- docs/reference/legacy-method-catalogue.md — the per-method "Key formula" entries
  (TTest, PairedTTest, ZTest, CupedTTest, PairedCupedTTest, sample_utils,
  fraction_utils, alpha_adjustment_utils).

This module is the independent judge the new closed-form engine is compared
against at relative 1e-9 (docs/specs/quorum-review.md "Golden tolerance").
It deliberately uses RAW-ARRAY numpy operations exactly as the legacy did:
``np.mean``/``np.std``/``np.var`` (population, ddof=0), ``np.cov`` (numpy
default ddof=1 — baseline fact #1, the load-bearing MIXED ddof), and
``scipy.stats.norm`` (Normal, never Student-t). Nothing here may import from
``abkit``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
import scipy.stats as sps
from statsmodels.stats.power import NormalIndPower, TTestIndPower
from statsmodels.stats.proportion import proportion_effectsize

ArrayLike = npt.ArrayLike


def _normal_test_results(distribution: Any, alpha: float) -> dict[str, Any]:
    """Shared parametric result computation (baseline §3.1 "Results")."""
    left_bound, right_bound = distribution.ppf([alpha / 2, 1 - alpha / 2])
    pvalue = 2 * min(distribution.cdf(0), distribution.sf(0))
    return {
        "left_bound": float(left_bound),
        "right_bound": float(right_bound),
        "ci_length": float(right_bound - left_bound),
        "pvalue": float(pvalue),
        "reject": bool(pvalue < alpha),
    }


# --- TTest (baseline §3.1; catalogue "TTest") -------------------------------------


def legacy_ttest(
    y1: ArrayLike, y2: ArrayLike, alpha: float = 0.05, test_type: str = "relative"
) -> dict[str, Any]:
    """Independent two-sample normal-approximation test with delta-method relative."""
    y1 = np.asarray(y1, dtype=np.float64)
    y2 = np.asarray(y2, dtype=np.float64)
    mean_1, mean_2 = np.mean(y1), np.mean(y2)
    var_mean_1 = np.var(y1) / len(y1)  # ddof=0 (Sample.var baseline)
    var_mean_2 = np.var(y2) / len(y2)
    difference_mean = mean_2 - mean_1
    difference_mean_var = var_mean_1 + var_mean_2

    if test_type == "absolute":
        effect = difference_mean
        distribution = sps.norm(loc=difference_mean, scale=np.sqrt(difference_mean_var))
    else:
        covariance = -var_mean_1  # num & denom share mean_1
        relative_mu = difference_mean / mean_1
        relative_var = (
            difference_mean_var / mean_1**2
            + var_mean_1 * (difference_mean**2 / mean_1**4)
            - 2 * (difference_mean / mean_1**3) * covariance
        )
        effect = relative_mu
        distribution = sps.norm(loc=relative_mu, scale=np.sqrt(relative_var))

    return {
        "value_1": float(mean_1),
        "value_2": float(mean_2),
        "std_1": float(np.std(y1)),
        "std_2": float(np.std(y2)),
        "effect": float(effect),
        **_normal_test_results(distribution, alpha),
    }


# --- PairedTTest (baseline §4.5; catalogue "PairedTTest") --------------------------


def legacy_paired_ttest(
    y1: ArrayLike, y2: ArrayLike, alpha: float = 0.05, test_type: str = "relative"
) -> dict[str, Any]:
    """Paired normal-approximation test: ``np.var(y2−y1)`` (ddof=0) over n; the
    relative covariance term is ``np.cov`` (ddof=1) — the mixed-ddof convention."""
    y1 = np.asarray(y1, dtype=np.float64)
    y2 = np.asarray(y2, dtype=np.float64)
    n = len(y1)
    mean_1, mean_2 = np.mean(y1), np.mean(y2)
    difference = y2 - y1
    difference_mean = mean_2 - mean_1
    difference_mean_var = np.var(difference) / n  # population var of paired diffs / n

    if test_type == "absolute":
        effect = difference_mean
        distribution = sps.norm(loc=difference_mean, scale=np.sqrt(difference_mean_var))
    else:
        var_mean_1 = np.var(y1) / n
        covariance = -np.cov(difference, y1)[0, 1] / n  # np.cov default ddof=1
        relative_mu = difference_mean / mean_1
        relative_var = (
            difference_mean_var / mean_1**2
            + var_mean_1 * (difference_mean**2 / mean_1**4)
            - 2 * (difference_mean / mean_1**3) * covariance
        )
        effect = relative_mu
        distribution = sps.norm(loc=relative_mu, scale=np.sqrt(relative_var))

    return {
        "value_1": float(mean_1),
        "value_2": float(mean_2),
        "std_1": float(np.std(y1)),
        "std_2": float(np.std(y2)),
        "effect": float(effect),
        **_normal_test_results(distribution, alpha),
    }


# --- CupedTTest (baseline §3.3; catalogue "CupedTTest") ----------------------------


def legacy_cuped_ttest(
    y1: ArrayLike,
    x1: ArrayLike,
    y2: ArrayLike,
    x2: ArrayLike,
    alpha: float = 0.05,
    test_type: str = "relative",
) -> dict[str, Any]:
    """CUPED with pooled θ: ``np.cov`` (ddof=1) numerator over ``np.var`` (ddof=0)
    denominator — baseline fact #1. Relative denominator is the ORIGINAL control mean."""
    y1 = np.asarray(y1, dtype=np.float64)
    x1 = np.asarray(x1, dtype=np.float64)
    y2 = np.asarray(y2, dtype=np.float64)
    x2 = np.asarray(x2, dtype=np.float64)
    n1, n2 = len(y1), len(y2)

    theta = (np.cov(y1, x1)[0, 1] + np.cov(y2, x2)[0, 1]) / (np.var(x1) + np.var(x2))
    cup_1 = y1 - theta * x1
    cup_2 = y2 - theta * x2

    if test_type == "absolute":
        var_mean_cup_1 = np.var(cup_1) / len(cup_1)
        var_mean_cup_2 = np.var(cup_2) / len(cup_2)
        effect = np.mean(cup_2) - np.mean(cup_1)
        distribution = sps.norm(loc=effect, scale=np.sqrt(var_mean_cup_1 + var_mean_cup_2))
    else:
        mean_den = np.mean(y1)  # original control mean (baseline §3.3 subtlety)
        mean_num = np.mean(cup_2) - np.mean(cup_1)
        var_mean_den = np.var(y1) / n1
        var_mean_num = np.var(cup_2) / n2 + np.var(cup_1) / n1
        covariance = -np.cov(cup_1, y1)[0, 1] / n1  # np.cov default ddof=1
        relative_mu = mean_num / mean_den
        relative_var = (
            var_mean_num / mean_den**2
            + var_mean_den * (mean_num**2 / mean_den**4)
            - 2 * (mean_num / mean_den**3) * covariance
        )
        effect = relative_mu
        distribution = sps.norm(loc=relative_mu, scale=np.sqrt(relative_var))

    return {
        "theta": float(theta),
        "value_1": float(np.mean(y1)),
        "value_2": float(np.mean(y2)),
        "std_1": float(np.std(y1)),
        "std_2": float(np.std(y2)),
        "cov_value_1": float(np.mean(x1)),
        "cov_value_2": float(np.mean(x2)),
        "effect": float(effect),
        **_normal_test_results(distribution, alpha),
    }


# --- PairedCupedTTest (catalogue "PairedCupedTTest") -------------------------------


def legacy_paired_cuped_ttest(
    y1: ArrayLike,
    x1: ArrayLike,
    y2: ArrayLike,
    x2: ArrayLike,
    alpha: float = 0.05,
    test_type: str = "relative",
) -> dict[str, Any]:
    """Paired CUPED: θ on the per-pair DIFFERENCES — ``np.cov`` (ddof=1) over
    ``np.var`` (ddof=0) — then the paired normal machinery on the adjusted values."""
    y1 = np.asarray(y1, dtype=np.float64)
    x1 = np.asarray(x1, dtype=np.float64)
    y2 = np.asarray(y2, dtype=np.float64)
    x2 = np.asarray(x2, dtype=np.float64)
    n = len(y1)

    theta = np.cov(y2 - y1, x2 - x1)[0, 1] / np.var(x2 - x1)
    cup_1 = y1 - theta * x1
    cup_2 = y2 - theta * x2
    mean_num = np.mean(cup_2) - np.mean(cup_1)
    difference_mean_var_cup = np.var(cup_2 - cup_1) / n

    if test_type == "absolute":
        effect = mean_num
        distribution = sps.norm(loc=mean_num, scale=np.sqrt(difference_mean_var_cup))
    else:
        mean_den = np.mean(y1)  # original control mean
        var_mean_den = np.var(y1) / n
        covariance = -np.cov(cup_2 - cup_1, y1)[0, 1] / n  # np.cov default ddof=1
        relative_mu = mean_num / mean_den
        relative_var = (
            difference_mean_var_cup / mean_den**2
            + var_mean_den * (mean_num**2 / mean_den**4)
            - 2 * (mean_num / mean_den**3) * covariance
        )
        effect = relative_mu
        distribution = sps.norm(loc=relative_mu, scale=np.sqrt(relative_var))

    return {
        "theta": float(theta),
        "value_1": float(np.mean(y1)),
        "value_2": float(np.mean(y2)),
        "std_1": float(np.std(y1)),
        "std_2": float(np.std(y2)),
        "cov_value_1": float(np.mean(x1)),
        "cov_value_2": float(np.mean(x2)),
        "effect": float(effect),
        **_normal_test_results(distribution, alpha),
    }


# --- ZTest (baseline §3.2; catalogue "ZTest") --------------------------------------


def legacy_ztest(
    c1: float,
    n1: float,
    c2: float,
    n2: float,
    alpha: float = 0.05,
    test_type: str = "relative",
) -> dict[str, Any]:
    """Two-proportion pooled z-test, including the legacy quirks kept verbatim:
    z uses ``prop_1 − prop_2`` while the effect uses ``prop_2 − prop_1``, and the
    relative branch naively divides ``std_effect`` by ``prop_1`` (no covariance term)."""
    prop_1 = c1 / n1
    prop_2 = c2 / n2
    prop_combined = (c1 + c2) / (n1 + n2)
    std_effect = np.sqrt(prop_combined * (1 - prop_combined) * (1 / n1 + 1 / n2))

    z_stat = (prop_1 - prop_2) / std_effect
    pvalue = 2 * min(sps.norm.cdf(z_stat), sps.norm.sf(z_stat))

    effect = prop_2 - prop_1
    if test_type == "relative":
        effect = effect / prop_1
        std_effect = std_effect / prop_1

    quantiles = sps.norm.ppf([alpha / 2, 1 - alpha / 2])
    left_bound = quantiles[0] * std_effect + effect
    right_bound = quantiles[1] * std_effect + effect

    return {
        "value_1": float(prop_1),
        "value_2": float(prop_2),
        "std_1": float(np.sqrt(prop_1 * (1 - prop_1) / n1)),  # UNpooled per-group std
        "std_2": float(np.sqrt(prop_2 * (1 - prop_2) / n2)),
        "effect": float(effect),
        "left_bound": float(left_bound),
        "right_bound": float(right_bound),
        "ci_length": float(right_bound - left_bound),
        "pvalue": float(pvalue),
        "reject": bool(pvalue < alpha),
    }


# --- Power / MDE / sample-size (baseline §6; catalogue "Power / MDE") ---------------


def legacy_get_ttest_mde(
    mean: float,
    std: float,
    size: int,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> float:
    """sample_utils.get_ttest_mde: inf guard, statsmodels solve, round to 4."""
    if size <= 1 or std == 0:
        return float("inf")
    effect_size = TTestIndPower().solve_power(
        effect_size=None, nobs1=size, alpha=alpha, power=power, ratio=ratio, alternative="two-sided"
    )
    mean_adjusted = mean + float(effect_size) * std
    with np.errstate(divide="ignore", invalid="ignore"):
        if test_type == "relative":
            mde = float(np.float64(mean_adjusted - mean) / np.float64(mean))
        else:
            mde = mean_adjusted - mean
    return round(mde, 4)


def legacy_get_ttest_power(
    mean: float,
    std: float,
    size: int,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    ratio: float = 1.0,
) -> float:
    mean_adjusted = mean * (1 + mde) if test_type == "relative" else mean + mde
    effect_size = abs(mean - mean_adjusted) / std
    return float(
        TTestIndPower().solve_power(
            effect_size=effect_size,
            nobs1=size,
            alpha=alpha,
            power=None,
            ratio=ratio,
            alternative="two-sided",
        )
    )


def legacy_get_ttest_sample_size(
    mean: float,
    std: float,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> int:
    mean_adjusted = mean * (1 + mde) if test_type == "relative" else mean + mde
    effect_size = abs(mean - mean_adjusted) / std
    size = TTestIndPower().solve_power(
        effect_size=effect_size,
        nobs1=None,
        alpha=alpha,
        power=power,
        ratio=ratio,
        alternative="two-sided",
    )
    return int(round(float(size)))


def _legacy_cuped_adjusted_std(std: float, correlation: float) -> float:
    """adjusted_var = std² · (1 − corr²); adjusted_std = sqrt(adjusted_var)."""
    adjusted_var = std**2 * (1 - correlation**2)
    return float(np.sqrt(adjusted_var))


def legacy_get_cuped_ttest_mde(
    mean: float,
    std: float,
    correlation: float,
    size: int,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> float:
    return legacy_get_ttest_mde(
        mean, _legacy_cuped_adjusted_std(std, correlation), size, test_type, alpha, power, ratio
    )


def legacy_get_cuped_ttest_power(
    mean: float,
    std: float,
    correlation: float,
    size: int,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    ratio: float = 1.0,
) -> float:
    return legacy_get_ttest_power(
        mean, _legacy_cuped_adjusted_std(std, correlation), size, mde, test_type, alpha, ratio
    )


def legacy_get_cuped_ttest_sample_size(
    mean: float,
    std: float,
    correlation: float,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> int:
    return legacy_get_ttest_sample_size(
        mean, _legacy_cuped_adjusted_std(std, correlation), mde, test_type, alpha, power, ratio
    )


def legacy_get_fraction_mde(
    prop: float,
    size: int,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> float:
    """fraction_utils.get_fraction_mde: Cohen's h inverse arcsine back-transform."""
    effect_size = NormalIndPower().solve_power(
        effect_size=None, nobs1=size, alpha=alpha, power=power, ratio=ratio, alternative="two-sided"
    )
    mde_absolute = float(np.sin(np.arcsin(np.sqrt(prop)) + float(effect_size) / 2) ** 2 - prop)
    with np.errstate(divide="ignore", invalid="ignore"):
        if test_type == "relative":
            return float(np.float64(mde_absolute) / np.float64(prop))
        return mde_absolute


def legacy_get_fraction_power(
    prop: float,
    size: int,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    ratio: float = 1.0,
) -> float:
    mde_absolute = prop * mde if test_type == "relative" else mde
    effect_size = proportion_effectsize(prop, prop + mde_absolute)
    return float(
        NormalIndPower().solve_power(
            effect_size=effect_size,
            nobs1=size,
            alpha=alpha,
            power=None,
            ratio=ratio,
            alternative="two-sided",
        )
    )


def legacy_get_fraction_sample_size(
    prop: float,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> int:
    mde_absolute = prop * mde if test_type == "relative" else mde
    effect_size = proportion_effectsize(prop, prop + mde_absolute)
    size = NormalIndPower().solve_power(
        effect_size=effect_size,
        nobs1=None,
        alpha=alpha,
        power=power,
        ratio=ratio,
        alternative="two-sided",
    )
    return int(round(float(size)))


# --- Bonferroni alpha adjustment (baseline §6; catalogue "Alpha adjustment") --------


def legacy_adjust_alpha(alpha: float, groups_count: int, metrics_count: int = 1) -> float:
    """alpha / (C(groups, 2) × metrics) — the legacy pairwise Bonferroni."""
    comparisons = groups_count * (groups_count - 1) / 2 * metrics_count
    return alpha / comparisons
