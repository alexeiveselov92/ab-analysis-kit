"""Golden tests: power/MDE/sample-size solves and alpha adjustment vs the legacy.

Covers docs/specs/statistics-baseline.md §6 against the transcription in
``legacy_reference_parametric``: the statsmodels solves, the legacy inf guards
(``size <= 1 or std == 0``), the round-to-4 on the continuous MDE, the CUPED
std deflation ``std·sqrt(1 − corr²)``, the arcsine (Cohen's h) proportion
back-transform, and the config-time Bonferroni including the two-tier
main/secondary scheme (quorum must-fix "two-tier Bonferroni golden test",
pinned to literal values).
"""

from __future__ import annotations

import math
from collections.abc import Callable

import legacy_reference_parametric as legacy
import pytest

from abkit.stats.correction import adjust_alpha, n_comparisons, two_tier_alphas
from abkit.stats.power import (
    get_cuped_ttest_mde,
    get_cuped_ttest_power,
    get_cuped_ttest_sample_size,
    get_fraction_mde,
    get_fraction_power,
    get_fraction_sample_size,
    get_ttest_mde,
    get_ttest_power,
    get_ttest_sample_size,
)

pytestmark = pytest.mark.golden

AssertRel = Callable[..., None]

ALPHAS = (0.05, 0.01)
TEST_TYPES = ("relative", "absolute")

parametrize_test_type = pytest.mark.parametrize("test_type", TEST_TYPES)
parametrize_alpha = pytest.mark.parametrize("alpha", ALPHAS)


# --- continuous MDE / power / sample size ----------------------------------------------


@parametrize_alpha
@parametrize_test_type
@pytest.mark.parametrize(
    ("mean", "std", "size", "ratio"),
    [
        (10.0, 2.0, 5000, 1.0),
        (0.5, 1.5, 250, 2.0),
        (-3.0, 4.0, 1200, 0.5),
        (2.2, 0.7, 30, 1.0),
    ],
)
def test_get_ttest_mde_matches_legacy(
    mean: float,
    std: float,
    size: int,
    ratio: float,
    test_type: str,
    alpha: float,
    assert_rel: AssertRel,
) -> None:
    engine = get_ttest_mde(mean, std, size, test_type=test_type, alpha=alpha, ratio=ratio)
    expected = legacy.legacy_get_ttest_mde(
        mean, std, size, test_type=test_type, alpha=alpha, ratio=ratio
    )
    assert_rel(engine, expected, what="ttest_mde")


@parametrize_test_type
@pytest.mark.parametrize(("size", "std"), [(1, 2.0), (0, 2.0), (5000, 0.0)])
def test_get_ttest_mde_inf_guards(size: int, std: float, test_type: str) -> None:
    """Legacy guard: ``size <= 1 or std == 0`` → inf MDE, engine and reference alike."""
    engine = get_ttest_mde(10.0, std, size, test_type=test_type)
    expected = legacy.legacy_get_ttest_mde(10.0, std, size, test_type=test_type)
    assert math.isinf(engine) and math.isinf(expected)
    assert engine == expected


def test_get_ttest_mde_zero_mean_relative_is_inf() -> None:
    """Numpy division semantics preserved: zero mean under relative → ±inf, no raise."""
    engine = get_ttest_mde(0.0, 2.0, 5000, test_type="relative")
    expected = legacy.legacy_get_ttest_mde(0.0, 2.0, 5000, test_type="relative")
    assert math.isinf(engine) and engine == expected


@parametrize_alpha
@parametrize_test_type
@pytest.mark.parametrize(
    ("mean", "std", "size", "mde", "ratio"),
    [
        (10.0, 2.0, 4000, 0.02, 1.0),
        (0.8, 1.1, 600, 0.3, 2.0),
        (25.0, 9.0, 12000, 0.05, 0.5),
    ],
)
def test_get_ttest_power_matches_legacy(
    mean: float,
    std: float,
    size: int,
    mde: float,
    ratio: float,
    test_type: str,
    alpha: float,
    assert_rel: AssertRel,
) -> None:
    engine = get_ttest_power(mean, std, size, mde, test_type=test_type, alpha=alpha, ratio=ratio)
    expected = legacy.legacy_get_ttest_power(
        mean, std, size, mde, test_type=test_type, alpha=alpha, ratio=ratio
    )
    assert_rel(engine, expected, what="ttest_power")


@parametrize_alpha
@parametrize_test_type
@pytest.mark.parametrize(
    ("mean", "std", "mde", "ratio"),
    [
        (10.0, 2.0, 0.02, 1.0),
        (0.8, 1.1, 0.3, 2.0),
        (25.0, 9.0, 0.05, 0.5),
    ],
)
def test_get_ttest_sample_size_matches_legacy(
    mean: float, std: float, mde: float, ratio: float, test_type: str, alpha: float
) -> None:
    engine = get_ttest_sample_size(mean, std, mde, test_type=test_type, alpha=alpha, ratio=ratio)
    expected = legacy.legacy_get_ttest_sample_size(
        mean, std, mde, test_type=test_type, alpha=alpha, ratio=ratio
    )
    assert engine == expected  # int(round(...)) on both sides


# --- CUPED variants: std deflated by sqrt(1 − corr²) ------------------------------------


@parametrize_test_type
@pytest.mark.parametrize("corr_coef", (0.0, 0.6, 0.9))
def test_get_cuped_ttest_mde_matches_legacy(
    corr_coef: float, test_type: str, assert_rel: AssertRel
) -> None:
    engine = get_cuped_ttest_mde(10.0, 2.0, corr_coef, 4000, test_type=test_type)
    expected = legacy.legacy_get_cuped_ttest_mde(10.0, 2.0, corr_coef, 4000, test_type=test_type)
    assert_rel(engine, expected, what="cuped_ttest_mde")


def test_get_cuped_ttest_mde_zero_corr_equals_plain_ttest() -> None:
    """corr = 0 must be a no-op deflation — CUPED MDE collapses to the t-test MDE."""
    assert get_cuped_ttest_mde(10.0, 2.0, 0.0, 4000) == get_ttest_mde(10.0, 2.0, 4000)


@parametrize_test_type
@pytest.mark.parametrize("corr_coef", (0.0, 0.6, 0.9))
def test_get_cuped_ttest_power_matches_legacy(
    corr_coef: float, test_type: str, assert_rel: AssertRel
) -> None:
    engine = get_cuped_ttest_power(10.0, 2.0, corr_coef, 4000, 0.03, test_type=test_type)
    expected = legacy.legacy_get_cuped_ttest_power(
        10.0, 2.0, corr_coef, 4000, 0.03, test_type=test_type
    )
    assert_rel(engine, expected, what="cuped_ttest_power")


@parametrize_test_type
@pytest.mark.parametrize("corr_coef", (0.0, 0.6, 0.9))
def test_get_cuped_ttest_sample_size_matches_legacy(corr_coef: float, test_type: str) -> None:
    engine = get_cuped_ttest_sample_size(10.0, 2.0, corr_coef, 0.03, test_type=test_type)
    expected = legacy.legacy_get_cuped_ttest_sample_size(
        10.0, 2.0, corr_coef, 0.03, test_type=test_type
    )
    assert engine == expected


# --- proportions: arcsine (Cohen's h) transform ------------------------------------------


@parametrize_alpha
@parametrize_test_type
@pytest.mark.parametrize("prop", (0.02, 0.2, 0.5, 0.9))
@pytest.mark.parametrize("size", (500, 20000))
def test_get_fraction_mde_matches_legacy(
    prop: float, size: int, test_type: str, alpha: float, assert_rel: AssertRel
) -> None:
    engine = get_fraction_mde(prop, size, test_type=test_type, alpha=alpha)
    expected = legacy.legacy_get_fraction_mde(prop, size, test_type=test_type, alpha=alpha)
    assert_rel(engine, expected, what="fraction_mde")


@parametrize_test_type
@pytest.mark.parametrize(("prop", "size", "mde"), [(0.2, 5000, 0.05), (0.5, 800, 0.1)])
def test_get_fraction_power_matches_legacy(
    prop: float, size: int, mde: float, test_type: str, assert_rel: AssertRel
) -> None:
    engine = get_fraction_power(prop, size, mde, test_type=test_type)
    expected = legacy.legacy_get_fraction_power(prop, size, mde, test_type=test_type)
    assert_rel(engine, expected, what="fraction_power")


@parametrize_test_type
@pytest.mark.parametrize(("prop", "mde"), [(0.2, 0.05), (0.5, 0.1)])
def test_get_fraction_sample_size_matches_legacy(prop: float, mde: float, test_type: str) -> None:
    engine = get_fraction_sample_size(prop, mde, test_type=test_type)
    expected = legacy.legacy_get_fraction_sample_size(prop, mde, test_type=test_type)
    assert engine == expected


# --- Bonferroni alpha adjustment (config-time; two-tier golden values) --------------------


@pytest.mark.parametrize("alpha", ALPHAS)
@pytest.mark.parametrize("groups_count", (2, 3, 5))
@pytest.mark.parametrize("metrics_count", (1, 4))
def test_adjust_alpha_matches_legacy(alpha: float, groups_count: int, metrics_count: int) -> None:
    assert adjust_alpha(alpha, groups_count, metrics_count) == legacy.legacy_adjust_alpha(
        alpha, groups_count, metrics_count
    )


def test_adjust_alpha_pinned_values() -> None:
    """Literal golden values — the mapping must never drift."""
    assert adjust_alpha(0.05, 2, 1) == 0.05
    assert adjust_alpha(0.05, 3, 1) == 0.016666666666666666
    assert adjust_alpha(0.05, 3, 4) == 0.004166666666666667
    assert adjust_alpha(0.05, 4, 10) == 0.0008333333333333334
    assert adjust_alpha(0.01, 3, 1) == 0.0033333333333333335


def test_n_comparisons_matches_legacy_count() -> None:
    assert n_comparisons(2, 1) == 1.0
    assert n_comparisons(3, 1) == 3.0
    assert n_comparisons(3, 4) == 12.0
    assert n_comparisons(5, 2) == 20.0


def test_two_tier_alphas_golden() -> None:
    """The two-tier scheme (quorum must-fix): main metric budgets C(groups,2)
    comparisons; the secondary tier shares the budget across non-main metrics."""
    tiers = two_tier_alphas(0.05, groups_count=3, metrics_count=4)
    assert tiers.main == 0.016666666666666666  # 0.05 / C(3,2)
    assert tiers.secondary == 0.004166666666666667  # 0.05 / (C(3,2) * 4)
    assert tiers.main == legacy.legacy_adjust_alpha(0.05, 3, 1)
    assert tiers.secondary == legacy.legacy_adjust_alpha(0.05, 3, 4)
    assert (tiers.alpha, tiers.groups_count, tiers.metrics_count) == (0.05, 3, 4)


def test_two_tier_alphas_two_groups() -> None:
    tiers = two_tier_alphas(0.05, groups_count=2, metrics_count=5)
    assert tiers.main == 0.05  # single comparison — no correction for the main metric
    assert tiers.secondary == 0.01  # 0.05 / 5 secondary metrics
