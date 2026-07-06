"""WP1 known-answer tests for the always-valid confidence sequence.

Pins: SE recovery by CI-inversion round-trips the fixed CI at rel-1e-9; the
always-valid interval strictly contains the fixed interval; the radius matches a
hand-computed normal-mixture constant; p-value <-> CI-excludes-zero consistency;
degenerate/contract handling.
"""

from __future__ import annotations

import math

import pytest
import scipy.stats as sps

from abkit.stats.effects import EffectEstimate, normal_test
from abkit.stats.sequential import mixture_tau2, se_from_ci_length, sequentialize


@pytest.mark.parametrize("alpha", [0.1, 0.05, 0.01, 0.001])
@pytest.mark.parametrize("var", [1e-4, 0.25, 4.0, 100.0])
def test_se_from_ci_length_round_trips_fixed_ci(alpha: float, var: float) -> None:
    """SE = ci_length / (2 * norm.ppf(1 - alpha/2)) recovers sqrt(var) exactly."""
    fixed = normal_test(EffectEstimate(effect=0.37, var=var), alpha)
    recovered = se_from_ci_length(fixed.ci_length, alpha)
    assert recovered == pytest.approx(math.sqrt(var), rel=1e-9)


@pytest.mark.parametrize("alpha", [0.05, 0.01])
@pytest.mark.parametrize("effect", [-2.0, 0.0, 0.8])
@pytest.mark.parametrize("se", [0.05, 0.5, 3.0])
def test_cs_strictly_contains_fixed_ci(alpha: float, effect: float, se: float) -> None:
    """The always-valid interval is wider than the fixed interval at the same look."""
    tau2 = mixture_tau2(reference_variance=se * se, alpha=alpha)
    lo, hi, _ = sequentialize(effect, se, tau2, alpha)
    z = float(sps.norm.ppf(1.0 - alpha / 2.0))
    fixed_lo, fixed_hi = effect - z * se, effect + z * se
    assert lo < fixed_lo
    assert hi > fixed_hi
    # Symmetric about the effect.
    assert (lo + hi) / 2.0 == pytest.approx(effect, abs=1e-12)


def test_radius_known_answer() -> None:
    """Hand-computed normal-mixture radius for fixed (se, tau2, alpha)."""
    effect, se, tau2, alpha = 0.0, 1.0, 5.0, 0.05
    var = se * se
    expected_r = math.sqrt(
        (2.0 * var * (var + tau2) / tau2) * (-math.log(alpha) + 0.5 * math.log((var + tau2) / var))
    )
    lo, hi, _ = sequentialize(effect, se, tau2, alpha)
    assert (hi - lo) / 2.0 == pytest.approx(expected_r, rel=1e-12)


@pytest.mark.parametrize("alpha", [0.05, 0.01])
@pytest.mark.parametrize("effect", [-3.0, -1.0, -0.3, 0.0, 0.3, 1.0, 3.0])
def test_pvalue_matches_ci_excludes_zero(alpha: float, effect: float) -> None:
    """p <= alpha iff the always-valid interval excludes zero (the D3 primitive)."""
    se, tau2 = 1.0, 5.0
    lo, hi, p = sequentialize(effect, se, tau2, alpha)
    excludes_zero = lo > 0.0 or hi < 0.0
    assert (p <= alpha) == excludes_zero
    assert 0.0 < p <= 1.0


def test_pvalue_is_one_at_zero_effect_when_wide() -> None:
    """A zero effect never rejects; its always-valid p-value caps at 1.0."""
    _, _, p = sequentialize(0.0, 1.0, 5.0, 0.05)
    assert p == 1.0


@pytest.mark.parametrize("bad_se", [0.0, -1.0, float("nan"), float("inf")])
def test_degenerate_se_returns_nan_triple(bad_se: float) -> None:
    lo, hi, p = sequentialize(0.5, bad_se, 5.0, 0.05)
    assert math.isnan(lo) and math.isnan(hi) and math.isnan(p)


def test_nan_effect_returns_nan_triple() -> None:
    lo, hi, p = sequentialize(float("nan"), 1.0, 5.0, 0.05)
    assert math.isnan(lo) and math.isnan(hi) and math.isnan(p)


def test_nan_ci_length_recovers_nan_se() -> None:
    assert math.isnan(se_from_ci_length(float("nan"), 0.05))


@pytest.mark.parametrize("bad_alpha", [0.0, 1.0, -0.1, 1.5])
def test_bad_alpha_raises(bad_alpha: float) -> None:
    with pytest.raises(ValueError):
        sequentialize(0.5, 1.0, 5.0, bad_alpha)


@pytest.mark.parametrize("bad_tau2", [0.0, -1.0, float("nan"), float("inf")])
def test_bad_tau2_raises(bad_tau2: float) -> None:
    with pytest.raises(ValueError):
        sequentialize(0.5, 1.0, bad_tau2, 0.05)
