"""WP1 tests for the mixture-variance policy tau^2 (mixture.py)."""

from __future__ import annotations

import math

import pytest

from abkit.stats.sequential.mixture import _optimal_ratio, mixture_tau2


@pytest.mark.parametrize("alpha", [0.1, 0.05, 0.01, 0.001])
def test_optimal_ratio_satisfies_stationarity(alpha: float) -> None:
    """u* solves u = 2 ln(1/alpha) + ln(1 + u)."""
    u = _optimal_ratio(alpha)
    assert u == pytest.approx(2.0 * (-math.log(alpha)) + math.log1p(u), rel=1e-10)
    assert u > 0.0


def test_optimal_ratio_known_value_at_5pct() -> None:
    """At alpha=0.05 the optimum is ~8.2 (documented in statistics-changes §4)."""
    assert _optimal_ratio(0.05) == pytest.approx(8.20, abs=0.05)


@pytest.mark.parametrize("alpha", [0.05, 0.01])
def test_tau2_linear_in_horizon_variance(alpha: float) -> None:
    """tau^2 = u*(alpha) * horizon_variance — linear, ratio independent of scale."""
    t1 = mixture_tau2(1.0, alpha)
    t2 = mixture_tau2(7.5, alpha)
    assert t2 / t1 == pytest.approx(7.5, rel=1e-12)
    assert t1 == pytest.approx(_optimal_ratio(alpha), rel=1e-12)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_bad_horizon_variance_raises(bad: float) -> None:
    with pytest.raises(ValueError):
        mixture_tau2(bad, 0.05)


@pytest.mark.parametrize("bad_alpha", [0.0, 1.0, -0.2])
def test_bad_alpha_raises(bad_alpha: float) -> None:
    with pytest.raises(ValueError):
        mixture_tau2(1.0, bad_alpha)
