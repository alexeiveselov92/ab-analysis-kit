"""Unit tests for the closed-form parametric methods (behaviour, not golden parity).

Covers the quorum known-answer test for ``ratio-delta`` (reduces to the t-test
when the denominator is identically 1 — docs/specs/statistics-changes.md §3),
the CUPED/paired guards and variance-reduction promises, the z-test known answer
against statsmodels' pooled two-proportion test plus its documented legacy
quirks, the H5 NaN-plus-warning divide-by-zero policy, and the identity contract
(``identity_params`` drops defaults; ``method_config_id`` is stable across
instances and independent of alpha).

``METHOD_CLASSES`` (the identity-contract sweep below) is registry-derived
(docs/specs/m7-implementation-plan.md WP1 A5; the plugin-registry invariant,
CLAUDE.md "Methods are plugins") so a new closed-form plugin is auto-swept in;
pair with test_registry_completeness.py (A6) for the "forgotten import" half.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from statsmodels.stats.proportion import proportions_ztest

from abkit.stats.base import BaseMethod
from abkit.stats.bootstrap import BaseBootstrapMethod
from abkit.stats.exceptions import AbkitStatsWarning, SampleValidationError
from abkit.stats.parametric import (
    CupedTTest,
    PairedCupedTTest,
    PairedTTest,
    RatioDelta,
    TTest,
    ZTest,
)
from abkit.stats.registry import available_methods, get_method_class
from abkit.stats.samples import Fraction, PairedSufficientStats, RatioSample, Sample

pytestmark = pytest.mark.unit

TEST_TYPES = ("relative", "absolute")

#: Every registered closed-form (non-bootstrap) method class, sorted by name for
#: stable test IDs. Bootstrap methods have their own contract sweep in
#: test_bootstrap_methods.py (different construction/identity rules — seed
#: exclusion, from_suffstats always raising, etc.).
METHOD_CLASSES: tuple[type[BaseMethod], ...] = tuple(
    sorted(
        (
            get_method_class(name)
            for name in available_methods()
            if not issubclass(get_method_class(name), BaseBootstrapMethod)
        ),
        key=lambda cls: cls.name,
    )
)


# --- ratio-delta ------------------------------------------------------------------------


@pytest.mark.parametrize("test_type", TEST_TYPES)
def test_ratio_delta_reduces_to_ttest_with_unit_denominator(test_type: str) -> None:
    """Quorum known-answer test: denominator ≡ 1 ⇒ ratio-delta IS the t-test."""
    rng = np.random.default_rng(31337)
    y1 = rng.normal(loc=5.0, scale=1.5, size=2500)
    y2 = rng.normal(loc=5.2, scale=1.4, size=2600)

    ratio_result = RatioDelta(alpha=0.05, test_type=test_type).from_samples(
        RatioSample(y1, np.ones_like(y1)), RatioSample(y2, np.ones_like(y2))
    )
    ttest_result = TTest(alpha=0.05, test_type=test_type).from_samples(Sample(y1), Sample(y2))

    # The point estimate reduces EXACTLY (identical means, unit denominator).
    assert ratio_result.effect == ttest_result.effect
    # Variance terms traverse two float paths (raw co-moments vs np.var); the
    # reduction is exact in real arithmetic — allow only last-ulp accumulation.
    for field in ("pvalue", "left_bound", "right_bound", "ci_length"):
        assert math.isclose(
            getattr(ratio_result, field), getattr(ttest_result, field), rel_tol=1e-12
        ), field
    assert ratio_result.reject == ttest_result.reject


@pytest.mark.parametrize(("test_type", "truth"), [("absolute", 0.1), ("relative", 0.05)])
def test_ratio_delta_ci_covers_truth_on_synthetic_ratio_metric(
    test_type: str, truth: float
) -> None:
    """R₁ = 2.0, R₂ = 2.1 by construction — the CI must cover the known effect."""
    rng = np.random.default_rng(4242)
    n = 6000
    den_1 = rng.uniform(1.0, 3.0, size=n)
    num_1 = 2.0 * den_1 + rng.normal(0.0, 0.5, size=n)
    den_2 = rng.uniform(1.0, 3.0, size=n)
    num_2 = 2.1 * den_2 + rng.normal(0.0, 0.5, size=n)

    result = RatioDelta(alpha=0.05, test_type=test_type).from_samples(
        RatioSample(num_1, den_1), RatioSample(num_2, den_2)
    )
    assert result.left_bound <= truth <= result.right_bound
    assert math.isclose(result.effect, truth, abs_tol=5.0 * (result.ci_length / 2.0))


@pytest.mark.parametrize("test_type", TEST_TYPES)
def test_ratio_delta_zero_denominator_arm_yields_nan_and_warning(test_type: str) -> None:
    rng = np.random.default_rng(77)
    good = RatioSample(rng.normal(2.0, 0.5, 500), rng.uniform(1.0, 2.0, 500), name="control")
    degenerate = RatioSample(rng.normal(2.0, 0.5, 500), np.zeros(500), name="variant")

    result = RatioDelta(alpha=0.05, test_type=test_type).from_samples(good, degenerate)

    assert math.isnan(result.effect)
    assert math.isnan(result.pvalue)
    assert result.reject is False
    assert any("denominator mean is zero" in message for message in result.warnings)


# --- cuped-t-test -----------------------------------------------------------------------


def _correlated_arms(
    rng: np.random.Generator, n: int, mean_shift: float
) -> tuple[np.ndarray, np.ndarray]:
    covariate = rng.normal(10.0, 2.0, size=n)
    values = covariate + rng.normal(mean_shift, 2.0, size=n)  # corr(y, x) ≈ 0.71
    return values, covariate


def test_cuped_missing_covariate_raises() -> None:
    rng = np.random.default_rng(5)
    with pytest.raises(SampleValidationError, match="covariate"):
        CupedTTest(alpha=0.05).from_samples(
            Sample(rng.normal(size=100)), Sample(rng.normal(size=100))
        )


def test_cuped_low_correlation_warning_recorded() -> None:
    rng = np.random.default_rng(11)
    y1, x1 = rng.normal(10, 2, 2000), rng.normal(10, 2, 2000)  # independent → corr ≈ 0
    y2, x2 = rng.normal(10, 2, 2000), rng.normal(10, 2, 2000)

    with pytest.warns(AbkitStatsWarning, match="variance reduction will be small"):
        result = CupedTTest(alpha=0.05).from_samples(
            Sample(y1, cov_array=x1, name="control"), Sample(y2, cov_array=x2, name="variant")
        )

    low_corr_warnings = [message for message in result.warnings if "< 0.5" in message]
    assert len(low_corr_warnings) == 2  # one per arm
    assert any("'control'" in message for message in low_corr_warnings)
    assert any("'variant'" in message for message in low_corr_warnings)


@pytest.mark.parametrize("test_type", TEST_TYPES)
def test_cuped_ci_tighter_than_plain_ttest_on_correlated_covariate(test_type: str) -> None:
    rng = np.random.default_rng(97)
    y1, x1 = _correlated_arms(rng, 4000, 0.0)
    y2, x2 = _correlated_arms(rng, 4000, 0.1)

    cuped = CupedTTest(alpha=0.05, test_type=test_type).from_samples(
        Sample(y1, cov_array=x1), Sample(y2, cov_array=x2)
    )
    plain = TTest(alpha=0.05, test_type=test_type).from_samples(Sample(y1), Sample(y2))

    assert cuped.ci_length < plain.ci_length
    assert not cuped.warnings  # corr ≈ 0.71 — the legacy guard must stay silent


# --- paired methods ---------------------------------------------------------------------


def test_paired_size_mismatch_raises() -> None:
    rng = np.random.default_rng(21)
    with pytest.raises(SampleValidationError, match="equal-size"):
        PairedTTest(alpha=0.05).from_samples(
            Sample(rng.normal(size=100)), Sample(rng.normal(size=101))
        )


def test_paired_from_suffstats_rejects_second_stats_object() -> None:
    """Paired sufficient statistics are joint by construction — one object only."""
    rng = np.random.default_rng(22)
    joint = PairedSufficientStats.from_samples(
        Sample(rng.normal(size=100)), Sample(rng.normal(size=100))
    )
    with pytest.raises(SampleValidationError, match="joint by construction"):
        PairedTTest(alpha=0.05).from_suffstats(joint, joint)


def test_paired_cuped_requires_covariates_on_joint_moments() -> None:
    rng = np.random.default_rng(23)
    joint = PairedSufficientStats.from_samples(
        Sample(rng.normal(size=100)), Sample(rng.normal(size=100))
    )
    with pytest.raises(SampleValidationError, match="covariate"):
        PairedCupedTTest(alpha=0.05).from_suffstats(joint)


@pytest.mark.parametrize("test_type", TEST_TYPES)
def test_paired_ci_tighter_than_independent_on_positively_correlated_pairs(
    test_type: str,
) -> None:
    rng = np.random.default_rng(303)
    n = 3000
    base = rng.normal(50.0, 10.0, size=n)  # shared pair effect → strong + correlation
    y1 = base + rng.normal(0.0, 3.0, size=n)
    y2 = base + rng.normal(0.2, 3.0, size=n)

    paired = PairedTTest(alpha=0.05, test_type=test_type).from_samples(Sample(y1), Sample(y2))
    independent = TTest(alpha=0.05, test_type=test_type).from_samples(Sample(y1), Sample(y2))

    assert paired.ci_length < independent.ci_length


# --- z-test ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count_1", "nobs_1", "count_2", "nobs_2"),
    [
        (450, 1000, 505, 1100),
        (30, 2400, 52, 2600),
        (1978, 2000, 1969, 2000),
        (6, 5000, 11, 5200),
    ],
)
def test_ztest_absolute_pvalue_matches_statsmodels_pooled(
    count_1: int, nobs_1: int, count_2: int, nobs_2: int
) -> None:
    """Known answer: the legacy pooled z-test IS statsmodels' proportions_ztest."""
    result = ZTest(alpha=0.05, test_type="absolute").from_samples(
        Fraction(count_1, nobs_1), Fraction(count_2, nobs_2)
    )
    _, expected_pvalue = proportions_ztest(
        np.array([count_1, count_2]), np.array([nobs_1, nobs_2]), alternative="two-sided"
    )
    assert math.isclose(result.pvalue, float(expected_pvalue), rel_tol=1e-9, abs_tol=1e-12)


def test_ztest_documented_quirks_hold() -> None:
    """Orientation: effect = prop_2 − prop_1 (variant minus control), even though the
    legacy z statistic is computed on prop_1 − prop_2; relative = naive /prop_1 scaling."""
    fraction_1, fraction_2 = Fraction(400, 1000), Fraction(460, 1000)
    absolute = ZTest(alpha=0.05, test_type="absolute").from_samples(fraction_1, fraction_2)
    relative = ZTest(alpha=0.05, test_type="relative").from_samples(fraction_1, fraction_2)

    assert absolute.effect == fraction_2.prop - fraction_1.prop
    assert absolute.effect > 0  # variant is higher → positive effect (not the z sign)
    # p-value is orientation-symmetric — identical across both test types.
    assert absolute.pvalue == relative.pvalue
    # The relative branch just divides effect AND std by prop_1 (no covariance term):
    assert math.isclose(relative.effect, absolute.effect / fraction_1.prop, rel_tol=1e-12)
    assert math.isclose(relative.ci_length, absolute.ci_length / fraction_1.prop, rel_tol=1e-12)
    # CI is centered on the effect (Normal, symmetric).
    center = (absolute.left_bound + absolute.right_bound) / 2.0
    assert math.isclose(center, absolute.effect, rel_tol=1e-12)


def test_ztest_zero_control_proportion_relative_yields_nan_and_warning() -> None:
    result = ZTest(alpha=0.05, test_type="relative").from_samples(
        Fraction(0, 1000), Fraction(25, 1000)
    )
    assert math.isnan(result.effect)
    assert math.isnan(result.pvalue)
    assert math.isnan(result.left_bound) and math.isnan(result.right_bound)
    assert result.reject is False
    assert any("control proportion is zero" in message for message in result.warnings)


def test_ztest_degenerate_pooled_proportion_yields_nan_and_warning() -> None:
    result = ZTest(alpha=0.05, test_type="absolute").from_samples(
        Fraction(0, 500), Fraction(0, 600)
    )
    assert math.isnan(result.pvalue)
    assert result.reject is False
    assert any("pooled proportion variance is zero" in message for message in result.warnings)


# --- identity contract (every parametric method) ------------------------------------------


@pytest.mark.parametrize("method_cls", METHOD_CLASSES, ids=lambda cls: cls.name)
def test_identity_params_drop_defaults(method_cls: type[BaseMethod]) -> None:
    assert method_cls(alpha=0.05).identity_params == {}
    assert method_cls(alpha=0.05, test_type="relative").identity_params == {}  # explicit default
    assert method_cls(alpha=0.05, test_type="absolute").identity_params == {"test_type": "absolute"}


@pytest.mark.parametrize("method_cls", METHOD_CLASSES, ids=lambda cls: cls.name)
def test_method_config_id_stable_across_instances(method_cls: type[BaseMethod]) -> None:
    default_id = method_cls(alpha=0.05).method_config_id
    assert default_id == method_cls(alpha=0.05).method_config_id
    # alpha is experiment-level and identity-excluded — it never re-keys the series.
    assert default_id == method_cls(alpha=0.01).method_config_id
    # Non-default identity params DO re-key, deterministically.
    absolute_id = method_cls(alpha=0.05, test_type="absolute").method_config_id
    assert absolute_id == method_cls(alpha=0.01, test_type="absolute").method_config_id
    assert absolute_id != default_id


def _run_on_valid_inputs(method: BaseMethod) -> object:
    """Drive any parametric method with small valid inputs (corr ≈ 0.89 — no warnings)."""
    rng = np.random.default_rng(999)
    n = 400
    y1, y2 = rng.normal(10, 2, n), rng.normal(10, 2, n)
    x1, x2 = y1 + rng.normal(0, 1, n), y2 + rng.normal(0, 1, n)
    if isinstance(method, ZTest):
        return method.from_samples(Fraction(50, 200), Fraction(60, 210))
    if isinstance(method, RatioDelta):
        return method.from_samples(RatioSample(y1, x1 + 20.0), RatioSample(y2, x2 + 20.0))
    if isinstance(method, (CupedTTest, PairedCupedTTest)):
        return method.from_samples(Sample(y1, cov_array=x1), Sample(y2, cov_array=x2))
    return method.from_samples(Sample(y1), Sample(y2))


@pytest.mark.parametrize("method_cls", METHOD_CLASSES, ids=lambda cls: cls.name)
def test_method_params_on_result_equal_identity_params(method_cls: type[BaseMethod]) -> None:
    method = method_cls(alpha=0.05, test_type="absolute")
    assert method.method_params == method.identity_params == {"test_type": "absolute"}
    result = _run_on_valid_inputs(method)
    assert result.method_params == method.identity_params
    assert result.method_name == method_cls.name
