"""Power / MDE / sample-size solves (baseline §6 — legacy transcription).

Built on statsmodels ``TTestIndPower`` / ``NormalIndPower`` exactly as the legacy
``sample_utils.py`` / ``fraction_utils.py``:

- continuous metrics: solve the standardized effect size at the target power,
  convert to a relative/absolute MDE; CUPED deflates the std by
  ``sqrt(1 − corr²)`` before the same solve;
- proportions: Cohen's h via ``proportion_effectsize`` (arcsine transform) and
  the inverse arcsine back-transform for the MDE.

Legacy conventions preserved: ``size <= 1 or std == 0`` → ``inf`` MDE; the
continuous MDE is rounded to 4 decimals; numpy division semantics (a zero mean
under ``relative`` yields ±inf, never an exception).
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from statsmodels.stats.power import NormalIndPower, TTestIndPower
from statsmodels.stats.proportion import proportion_effectsize

from abkit.stats.exceptions import MethodParamError


@lru_cache(maxsize=4096)
def _ttest_effect_size_at_power(size: int, alpha: float, power: float, ratio: float) -> float:
    """Cached MDE-side solve: the effect size depends only on (n, α, power, ratio),
    so cumulative runs (same sizes every day, many metrics) hit the cache instead
    of re-running the brentq root-solve per call (review finding)."""
    return float(
        TTestIndPower().solve_power(
            effect_size=None,
            nobs1=size,
            alpha=alpha,
            power=power,
            ratio=ratio,
            alternative="two-sided",
        )
    )


@lru_cache(maxsize=4096)
def _normal_effect_size_at_power(size: int, alpha: float, power: float, ratio: float) -> float:
    return float(
        NormalIndPower().solve_power(
            effect_size=None,
            nobs1=size,
            alpha=alpha,
            power=power,
            ratio=ratio,
            alternative="two-sided",
        )
    )


def _check_test_type(test_type: str) -> None:
    if test_type not in ("relative", "absolute"):
        raise MethodParamError(f"test_type must be 'relative' or 'absolute', got {test_type!r}")


def _adjusted_mean(mean: float, mde: float, test_type: str) -> float:
    return mean * (1.0 + mde) if test_type == "relative" else mean + mde


# --- continuous metrics (legacy sample_utils.py) ---------------------------------


def get_ttest_mde(
    mean: float,
    std: float,
    size: int,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> float:
    """MDE at the given power for the (normal-approx) t-test; ``ratio = n_other / n_this``."""
    _check_test_type(test_type)
    if size <= 1 or std == 0:
        return float("inf")
    effect_size = _ttest_effect_size_at_power(int(size), float(alpha), float(power), float(ratio))
    mean_adjusted = mean + effect_size * std
    with np.errstate(divide="ignore", invalid="ignore"):
        if test_type == "relative":
            mde = float(np.float64(mean_adjusted - mean) / np.float64(mean))
        else:
            mde = mean_adjusted - mean
    if not np.isfinite(mde):
        return mde
    return round(mde, 4)


def get_ttest_power(
    mean: float,
    std: float,
    size: int,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    ratio: float = 1.0,
) -> float:
    """Achieved power for detecting ``mde`` at the given sample size."""
    _check_test_type(test_type)
    mean_adjusted = _adjusted_mean(mean, mde, test_type)
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


def get_ttest_sample_size(
    mean: float,
    std: float,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> int:
    """Required per-group-1 sample size to detect ``mde`` at the given power."""
    _check_test_type(test_type)
    mean_adjusted = _adjusted_mean(mean, mde, test_type)
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


# --- CUPED variants: variance shrunk by the covariate correlation ----------------


def cuped_adjusted_std(std: float, corr_coef: float) -> float:
    """``std · sqrt(1 − corr²)`` — CUPED's variance-reduction effect on the solve."""
    return float(std * np.sqrt(1.0 - corr_coef**2))


def get_cuped_ttest_mde(
    mean: float,
    std: float,
    corr_coef: float,
    size: int,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> float:
    return get_ttest_mde(
        mean, cuped_adjusted_std(std, corr_coef), size, test_type, alpha, power, ratio
    )


def get_cuped_ttest_power(
    mean: float,
    std: float,
    corr_coef: float,
    size: int,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    ratio: float = 1.0,
) -> float:
    return get_ttest_power(
        mean, cuped_adjusted_std(std, corr_coef), size, mde, test_type, alpha, ratio
    )


def get_cuped_ttest_sample_size(
    mean: float,
    std: float,
    corr_coef: float,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> int:
    return get_ttest_sample_size(
        mean, cuped_adjusted_std(std, corr_coef), mde, test_type, alpha, power, ratio
    )


# --- proportions (legacy fraction_utils.py) ---------------------------------------


def get_fraction_mde(
    prop: float,
    size: int,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> float:
    """MDE for a proportion via the inverse arcsine (Cohen's h) back-transform."""
    _check_test_type(test_type)
    effect_size = _normal_effect_size_at_power(int(size), float(alpha), float(power), float(ratio))
    mde_absolute = float(np.sin(np.arcsin(np.sqrt(prop)) + effect_size / 2.0) ** 2 - prop)
    with np.errstate(divide="ignore", invalid="ignore"):
        if test_type == "relative":
            return float(np.float64(mde_absolute) / np.float64(prop))
        return mde_absolute


def get_fraction_power(
    prop: float,
    size: int,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    ratio: float = 1.0,
) -> float:
    _check_test_type(test_type)
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


def get_fraction_sample_size(
    prop: float,
    mde: float,
    test_type: str = "relative",
    alpha: float = 0.05,
    power: float = 0.8,
    ratio: float = 1.0,
) -> int:
    _check_test_type(test_type)
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
