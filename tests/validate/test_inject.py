"""Effect injection: suffstats-level algebra ≡ sample-level scaling (m4 D2)."""

from __future__ import annotations

import numpy as np
import pytest

from abkit.stats.samples import Fraction, RatioSample, RatioSufficientStats, Sample, SufficientStats
from abkit.validate.inject import inject_multiplicative, injection_clamped

REL = 1e-9


def test_sample_multiplicative_matches_scaling():
    rng = np.random.default_rng(1)
    values = rng.normal(5.0, 2.0, size=400)
    delta = 0.3

    baseline = SufficientStats.from_sample(Sample(values))
    injected = inject_multiplicative(baseline, delta)
    reference = SufficientStats.from_sample(Sample(values * (1.0 + delta)))

    assert injected.mean == pytest.approx(reference.mean, rel=REL)
    assert injected.m2 == pytest.approx(reference.m2, rel=REL)
    assert injected.n == reference.n


def test_cuped_injection_keeps_covariate_and_correlation():
    rng = np.random.default_rng(2)
    values = rng.normal(5.0, 2.0, size=400)
    covariate = values * 0.7 + rng.normal(0.0, 1.0, size=400)
    delta = 0.25

    baseline = SufficientStats.from_sample(Sample(values, cov_array=covariate))
    injected = inject_multiplicative(baseline, delta)
    reference = SufficientStats.from_sample(Sample(values * (1.0 + delta), cov_array=covariate))

    assert injected.mean == pytest.approx(reference.mean, rel=REL)
    assert injected.m2 == pytest.approx(reference.m2, rel=REL)
    assert injected.cross_c == pytest.approx(reference.cross_c, rel=REL)
    # covariate moments (X untouched) are invariant; corr is scale-free
    assert injected.cov_m2 == pytest.approx(baseline.cov_m2, rel=REL)
    assert injected.corr_coef == pytest.approx(baseline.corr_coef, rel=REL)


def test_ratio_injection_scales_numerator_only():
    rng = np.random.default_rng(3)
    num = rng.normal(4.0, 1.0, size=300)
    den = rng.normal(2.0, 0.5, size=300)
    delta = 0.4

    baseline = RatioSufficientStats.from_ratio_sample(RatioSample(num, den))
    injected = inject_multiplicative(baseline, delta)
    reference = RatioSufficientStats.from_ratio_sample(RatioSample(num * (1.0 + delta), den))

    assert injected.mean_num == pytest.approx(reference.mean_num, rel=REL)
    assert injected.m2_num == pytest.approx(reference.m2_num, rel=REL)
    assert injected.c_nd == pytest.approx(reference.c_nd, rel=REL)
    assert injected.mean_den == pytest.approx(baseline.mean_den, rel=REL)


def test_fraction_injection_clamps_at_nobs():
    high = Fraction(count=95.0, nobs=100.0)
    assert injection_clamped(high, 0.2) is True
    injected = inject_multiplicative(high, 0.2)  # 95*1.2 = 114 -> clamped to 100
    assert injected.count == pytest.approx(100.0)
    assert injected.nobs == pytest.approx(100.0)

    low = Fraction(count=20.0, nobs=100.0)
    assert injection_clamped(low, 0.2) is False
    assert inject_multiplicative(low, 0.2).count == pytest.approx(24.0)
