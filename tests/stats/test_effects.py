"""Tests for ``abkit.stats.effects`` — the preserved delta-method linearisation.

Known values follow docs/specs/statistics-baseline.md §2–§3; the zero-denominator
NaN + warning behaviour is hygiene fix H5 (docs/specs/statistics-changes.md §2).
"""

from __future__ import annotations

import math

import pytest
import scipy.stats as sps

from abkit.stats.effects import (
    EffectEstimate,
    absolute_effect,
    normal_test,
    relative_delta_effect,
)


def test_absolute_effect_known_value() -> None:
    estimate = absolute_effect(mean_1=4.0, mean_2=6.0, var_mean_1=0.25, var_mean_2=0.5)
    assert estimate.effect == 2.0
    assert estimate.var == 0.75
    assert estimate.warnings == []


def test_relative_delta_effect_hand_computed() -> None:
    """Dyadic inputs so the delta-method expansion is exact in float arithmetic.

    mu = 2/4 = 0.5
    var = 0.5/4² + 0.25·(2²/4⁴) − 2·(2/4³)·(−0.25)
        = 0.03125 + 0.00390625 + 0.015625 = 0.05078125
    """
    estimate = relative_delta_effect(
        mean_num=2.0, var_num=0.5, mean_den=4.0, var_den=0.25, covariance=-0.25
    )
    assert estimate.effect == 0.5
    assert estimate.var == 0.05078125
    assert estimate.warnings == []


@pytest.mark.parametrize("mean_den", [0.0, float("nan"), float("inf"), float("-inf")])
def test_relative_delta_effect_bad_denominator_is_nan_with_h5_warning(mean_den: float) -> None:
    estimate = relative_delta_effect(
        mean_num=1.0, var_num=1.0, mean_den=mean_den, var_den=1.0, covariance=-1.0
    )
    assert math.isnan(estimate.effect)
    assert math.isnan(estimate.var)
    assert len(estimate.warnings) == 1
    assert "H5" in estimate.warnings[0]


def test_normal_test_known_value() -> None:
    """mu=1, var=1, alpha=0.05 → bounds mu ± 1.959963985; pvalue = 2·Φ(−1)."""
    test = normal_test(EffectEstimate(effect=1.0, var=1.0), alpha=0.05)
    z = 1.959963984540054  # norm.ppf(0.975)
    assert test.effect == 1.0
    assert test.left_bound == pytest.approx(1.0 - z, rel=1e-12)
    assert test.right_bound == pytest.approx(1.0 + z, rel=1e-12)
    assert test.ci_length == pytest.approx(2.0 * z, rel=1e-12)
    assert test.pvalue == pytest.approx(2.0 * float(sps.norm.cdf(-1.0)), rel=1e-12)
    assert test.pvalue == pytest.approx(0.31731050786291415, rel=1e-12)
    assert test.reject is False
    assert test.distribution is not None


def test_normal_test_pvalue_is_two_sided_min_of_cdf_sf() -> None:
    """Negative effect exercises the sf(0) side of ``2·min(cdf(0), sf(0))``."""
    test = normal_test(EffectEstimate(effect=-2.5, var=0.49), alpha=0.05)
    distribution = sps.norm(loc=-2.5, scale=0.7)
    expected = 2.0 * min(float(distribution.cdf(0.0)), float(distribution.sf(0.0)))
    assert test.pvalue == pytest.approx(expected, rel=1e-12)
    assert test.pvalue == pytest.approx(2.0 * float(distribution.sf(0.0)), rel=1e-12)
    assert test.reject is True


def test_normal_test_zero_variance_yields_nan_outputs_and_warning() -> None:
    test = normal_test(EffectEstimate(effect=1.0, var=0.0), alpha=0.05)
    assert test.effect == 1.0  # the point estimate survives (hygiene H9)
    assert math.isnan(test.left_bound)
    assert math.isnan(test.right_bound)
    assert math.isnan(test.ci_length)
    assert math.isnan(test.pvalue)
    assert test.reject is False
    assert test.distribution is None
    assert any("variance is zero" in warning for warning in test.warnings)


def test_normal_test_nan_estimate_yields_nan_outputs_reject_false() -> None:
    estimate = EffectEstimate(effect=float("nan"), var=float("nan"), warnings=["upstream H5"])
    test = normal_test(estimate, alpha=0.05)
    assert math.isnan(test.effect)
    assert math.isnan(test.pvalue)
    assert math.isnan(test.left_bound)
    assert test.reject is False
    assert test.distribution is None
    assert "upstream H5" in test.warnings  # estimate warnings carried through
