"""Tests for power / MDE / sample-size solves (baseline §6, legacy transcription).

Legacy conventions preserved: ``size <= 1 or std == 0`` → inf MDE; the continuous
MDE is rounded to 4 decimals; a zero mean under ``relative`` follows numpy division
semantics (inf, never an exception); CUPED deflates the std by ``sqrt(1 − corr²)``.
"""

from __future__ import annotations

import math

import pytest
from statsmodels.stats.power import TTestIndPower

from abkit.stats.exceptions import MethodParamError
from abkit.stats.power import (
    _as_scalar,
    cuped_adjusted_std,
    get_cuped_ttest_mde,
    get_fraction_mde,
    get_fraction_power,
    get_ttest_mde,
    get_ttest_power,
    get_ttest_sample_size,
)


def test_mde_inf_guard_size() -> None:
    assert get_ttest_mde(10.0, 5.0, 1) == float("inf")
    assert get_ttest_mde(10.0, 5.0, 0) == float("inf")


@pytest.mark.parametrize("size", [139, 146, 151, 165, 174])
def test_mde_solve_survives_ndarray_return(size: int) -> None:
    """statsmodels' fsolve fallback returns a shape-(1,) ndarray for a
    data-dependent few-percent of ordinary sizes; under numpy >= 2.0
    ``float(ndarray)`` raises. The MDE solve must extract the scalar, not
    crash the readout/report path (review finding)."""
    mde = get_ttest_mde(10.0, 5.0, size, test_type="relative", alpha=0.05, power=0.8)
    assert math.isfinite(mde) and mde > 0


def test_as_scalar_extracts_and_rejects() -> None:
    import numpy as np

    assert _as_scalar(np.array([1.5])) == 1.5
    assert _as_scalar(1.5) == 1.5
    assert _as_scalar(np.float64(2.0)) == 2.0
    with pytest.raises(ValueError):
        _as_scalar(np.array([1.0, 2.0]))


def test_mde_inf_guard_zero_std() -> None:
    assert get_ttest_mde(10.0, 0.0, 1000) == float("inf")


def test_mde_round_to_4_matches_unrounded_statsmodels_solve() -> None:
    effect_size = TTestIndPower().solve_power(
        effect_size=None, nobs1=1000, alpha=0.05, power=0.8, ratio=1.0, alternative="two-sided"
    )
    unrounded = (10.0 + float(effect_size) * 5.0 - 10.0) / 10.0
    mde = get_ttest_mde(10.0, 5.0, 1000, test_type="relative", alpha=0.05, power=0.8)
    assert mde == round(unrounded, 4)
    assert mde == round(mde, 4)


def test_mde_absolute_matches_unrounded_statsmodels_solve() -> None:
    effect_size = TTestIndPower().solve_power(
        effect_size=None, nobs1=400, alpha=0.05, power=0.8, ratio=2.0, alternative="two-sided"
    )
    unrounded = float(effect_size) * 5.0
    mde = get_ttest_mde(10.0, 5.0, 400, test_type="absolute", alpha=0.05, power=0.8, ratio=2.0)
    assert mde == round(unrounded, 4)


def test_mde_monotone_in_sample_size() -> None:
    mde_small = get_ttest_mde(10.0, 5.0, 100)
    mde_mid = get_ttest_mde(10.0, 5.0, 1000)
    mde_big = get_ttest_mde(10.0, 5.0, 10000)
    assert mde_big < mde_mid < mde_small


@pytest.mark.parametrize("test_type", ["relative", "absolute"])
def test_mde_power_round_trip(test_type: str) -> None:
    """Power at the returned MDE ≈ the target power (loose: MDE is rounded to 4dp)."""
    mde = get_ttest_mde(10.0, 5.0, 1000, test_type=test_type, alpha=0.05, power=0.8)
    achieved = get_ttest_power(10.0, 5.0, 1000, mde, test_type=test_type, alpha=0.05)
    assert abs(achieved - 0.8) < 0.05


def test_mde_sample_size_round_trip() -> None:
    mde = get_ttest_mde(10.0, 5.0, 1000, test_type="relative")
    size = get_ttest_sample_size(10.0, 5.0, mde, test_type="relative")
    assert abs(size - 1000) <= 50  # 4-decimal rounding of the MDE shifts the solve slightly


def test_cuped_deflation_shrinks_mde() -> None:
    plain = get_ttest_mde(10.0, 5.0, 1000)
    cuped = get_cuped_ttest_mde(10.0, 5.0, 0.6, 1000)
    assert cuped < plain


def test_cuped_zero_correlation_equals_plain() -> None:
    assert get_cuped_ttest_mde(10.0, 5.0, 0.0, 1000) == get_ttest_mde(10.0, 5.0, 1000)


def test_cuped_adjusted_std_formula() -> None:
    assert cuped_adjusted_std(5.0, 0.6) == pytest.approx(5.0 * math.sqrt(1 - 0.36), rel=1e-15)
    assert cuped_adjusted_std(5.0, 0.0) == 5.0


@pytest.mark.parametrize("test_type", ["relative", "absolute"])
def test_fraction_mde_power_round_trip(test_type: str) -> None:
    mde = get_fraction_mde(0.1, 5000, test_type=test_type, alpha=0.05, power=0.8)
    achieved = get_fraction_power(0.1, 5000, mde, test_type=test_type, alpha=0.05)
    assert abs(achieved - 0.8) < 0.05


def test_fraction_relative_is_absolute_over_prop() -> None:
    relative = get_fraction_mde(0.1, 5000, test_type="relative")
    absolute = get_fraction_mde(0.1, 5000, test_type="absolute")
    assert relative == pytest.approx(absolute / 0.1, rel=1e-12)


def test_relative_mde_zero_mean_is_inf_not_exception() -> None:
    """Numpy division semantics preserved: 0 mean under relative → inf, no raise."""
    mde = get_ttest_mde(0.0, 1.0, 100, test_type="relative")
    assert math.isinf(mde)


def test_invalid_test_type_raises() -> None:
    with pytest.raises(MethodParamError, match="test_type"):
        get_ttest_mde(10.0, 5.0, 100, test_type="both")
    with pytest.raises(MethodParamError, match="test_type"):
        get_fraction_mde(0.1, 100, test_type="nope")
