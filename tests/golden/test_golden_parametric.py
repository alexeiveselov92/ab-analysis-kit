"""Golden tests: every closed-form method vs the transcribed legacy engine.

Quorum must-fixes covered here (docs/specs/quorum-review.md "Statistical
correctness"):

- **relative 1e-9 tolerance** on effect/pvalue/bounds/ci_length (and per-arm
  value/std where the legacy defines them), on a normal AND the heavy-tailed
  sparse-revenue fixture (docs/specs/statistics-changes.md §1.1);
- **θ golden test** (docs/specs/statistics-changes.md §1.2): the engine θ equals
  the legacy mixed-ddof θ at 1e-9, and a blanket-ddof θ (all-ddof0 or all-ddof1)
  does NOT match — a uniform-ddof rewrite fails loudly;
- **dual entry**: ``from_samples`` and ``from_suffstats`` are bitwise identical
  (one math path, ``ttest.py`` exemplar pattern).

The reference (``legacy_reference_parametric``) is transcribed from the frozen
docs only; when a test fails, the ENGINE is fixed — never the reference or the
tolerance.
"""

from __future__ import annotations

from collections.abc import Callable

import legacy_reference_parametric as legacy
import numpy as np
import pytest

from abkit.stats.parametric import (
    CupedTTest,
    PairedCupedTTest,
    PairedTTest,
    RatioDelta,
    TTest,
    ZTest,
)
from abkit.stats.samples import (
    Fraction,
    PairedSufficientStats,
    RatioSample,
    RatioSufficientStats,
    Sample,
    SufficientStats,
)

pytestmark = [
    pytest.mark.golden,
    # Heavy-tailed covariates may sit below the legacy corr<0.5 CUPED guard; the
    # warning is legacy-faithful and irrelevant to the numeric comparison.
    pytest.mark.filterwarnings("ignore::abkit.stats.exceptions.AbkitStatsWarning"),
]

# Local aliases for the conftest fixture payloads (conftest itself is never imported).
Arms = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
AssertRel = Callable[..., None]
AssertResultMatches = Callable[..., None]

ALPHAS = (0.05, 0.01)
TEST_TYPES = ("relative", "absolute")

parametrize_test_type = pytest.mark.parametrize("test_type", TEST_TYPES)
parametrize_alpha = pytest.mark.parametrize("alpha", ALPHAS)


# --- method vs legacy reference (rel 1e-9) --------------------------------------------


@parametrize_alpha
@parametrize_test_type
def test_ttest_matches_legacy(
    continuous_arms: Arms,
    test_type: str,
    alpha: float,
    assert_result_matches: AssertResultMatches,
) -> None:
    y1, _, y2, _ = continuous_arms
    result = TTest(alpha=alpha, test_type=test_type).from_samples(
        Sample(y1, name="control"), Sample(y2, name="variant")
    )
    expected = legacy.legacy_ttest(y1, y2, alpha=alpha, test_type=test_type)
    assert_result_matches(result, expected)


@parametrize_alpha
@parametrize_test_type
def test_paired_ttest_matches_legacy(
    paired_arms: Arms,
    test_type: str,
    alpha: float,
    assert_result_matches: AssertResultMatches,
) -> None:
    y1, _, y2, _ = paired_arms
    result = PairedTTest(alpha=alpha, test_type=test_type).from_samples(
        Sample(y1, name="control"), Sample(y2, name="variant")
    )
    expected = legacy.legacy_paired_ttest(y1, y2, alpha=alpha, test_type=test_type)
    assert_result_matches(result, expected)


@parametrize_alpha
@parametrize_test_type
def test_cuped_ttest_matches_legacy(
    continuous_arms: Arms,
    test_type: str,
    alpha: float,
    assert_result_matches: AssertResultMatches,
) -> None:
    y1, x1, y2, x2 = continuous_arms
    result = CupedTTest(alpha=alpha, test_type=test_type).from_samples(
        Sample(y1, cov_array=x1, name="control"), Sample(y2, cov_array=x2, name="variant")
    )
    expected = legacy.legacy_cuped_ttest(y1, x1, y2, x2, alpha=alpha, test_type=test_type)
    assert_result_matches(result, expected)


@parametrize_alpha
@parametrize_test_type
def test_paired_cuped_ttest_matches_legacy(
    paired_arms: Arms,
    test_type: str,
    alpha: float,
    assert_result_matches: AssertResultMatches,
) -> None:
    y1, x1, y2, x2 = paired_arms
    result = PairedCupedTTest(alpha=alpha, test_type=test_type).from_samples(
        Sample(y1, cov_array=x1, name="control"), Sample(y2, cov_array=x2, name="variant")
    )
    expected = legacy.legacy_paired_cuped_ttest(y1, x1, y2, x2, alpha=alpha, test_type=test_type)
    assert_result_matches(result, expected)


@parametrize_alpha
@parametrize_test_type
def test_ztest_matches_legacy(
    proportion_case: tuple[float, float, float, float],
    test_type: str,
    alpha: float,
    assert_result_matches: AssertResultMatches,
) -> None:
    c1, n1, c2, n2 = proportion_case
    result = ZTest(alpha=alpha, test_type=test_type).from_samples(
        Fraction(c1, n1, name="control"), Fraction(c2, n2, name="variant")
    )
    expected = legacy.legacy_ztest(c1, n1, c2, n2, alpha=alpha, test_type=test_type)
    assert_result_matches(result, expected)


# --- θ golden test (quorum must-fix: the mixed-ddof convention, pinned) ----------------


def test_cuped_theta_matches_legacy_and_rejects_blanket_ddof(
    continuous_arms: Arms, assert_rel: AssertRel
) -> None:
    """Engine θ == legacy mixed-ddof θ at 1e-9; a uniform-ddof θ must NOT match."""
    y1, x1, y2, x2 = continuous_arms
    result = CupedTTest(alpha=0.05).from_samples(Sample(y1, cov_array=x1), Sample(y2, cov_array=x2))
    theta = result.diagnostics["theta"]
    expected = legacy.legacy_cuped_ttest(y1, x1, y2, x2)["theta"]
    assert_rel(theta, expected, what="theta")

    theta_all_ddof0 = (np.cov(y1, x1, ddof=0)[0, 1] + np.cov(y2, x2, ddof=0)[0, 1]) / (
        np.var(x1) + np.var(x2)
    )
    theta_all_ddof1 = (np.cov(y1, x1)[0, 1] + np.cov(y2, x2)[0, 1]) / (
        np.var(x1, ddof=1) + np.var(x2, ddof=1)
    )
    for blanket in (theta_all_ddof0, theta_all_ddof1):
        assert abs(blanket - theta) / abs(theta) > 1e-6, (
            "a blanket-ddof theta must NOT reproduce the mixed-ddof baseline "
            f"(blanket {blanket!r} vs mixed {theta!r})"
        )


def test_paired_cuped_theta_matches_legacy_and_rejects_blanket_ddof(
    paired_arms: Arms, assert_rel: AssertRel
) -> None:
    y1, x1, y2, x2 = paired_arms
    result = PairedCupedTTest(alpha=0.05).from_samples(
        Sample(y1, cov_array=x1), Sample(y2, cov_array=x2)
    )
    theta = result.diagnostics["theta"]
    expected = legacy.legacy_paired_cuped_ttest(y1, x1, y2, x2)["theta"]
    assert_rel(theta, expected, what="theta")

    diff_y, diff_x = y2 - y1, x2 - x1
    theta_all_ddof0 = np.cov(diff_y, diff_x, ddof=0)[0, 1] / np.var(diff_x)
    theta_all_ddof1 = np.cov(diff_y, diff_x)[0, 1] / np.var(diff_x, ddof=1)
    for blanket in (theta_all_ddof0, theta_all_ddof1):
        assert abs(blanket - theta) / abs(theta) > 1e-6, (
            "a blanket-ddof theta must NOT reproduce the mixed-ddof baseline "
            f"(blanket {blanket!r} vs mixed {theta!r})"
        )


# --- dual entry: from_samples ≡ from_suffstats (bitwise — one shared math path) --------


def _assert_bitwise_equal(result_a: object, result_b: object) -> None:
    for field in ("effect", "pvalue", "left_bound", "right_bound", "ci_length"):
        value_a, value_b = getattr(result_a, field), getattr(result_b, field)
        assert value_a == value_b, f"{field}: {value_a!r} != {value_b!r} (dual entry diverged)"


@parametrize_test_type
def test_dual_entry_ttest(continuous_arms: Arms, test_type: str) -> None:
    y1, _, y2, _ = continuous_arms
    method = TTest(alpha=0.05, test_type=test_type)
    sample_1, sample_2 = Sample(y1), Sample(y2)
    _assert_bitwise_equal(
        method.from_samples(sample_1, sample_2),
        method.from_suffstats(
            SufficientStats.from_sample(sample_1), SufficientStats.from_sample(sample_2)
        ),
    )


@parametrize_test_type
def test_dual_entry_cuped_ttest(continuous_arms: Arms, test_type: str) -> None:
    y1, x1, y2, x2 = continuous_arms
    method = CupedTTest(alpha=0.05, test_type=test_type)
    sample_1, sample_2 = Sample(y1, cov_array=x1), Sample(y2, cov_array=x2)
    _assert_bitwise_equal(
        method.from_samples(sample_1, sample_2),
        method.from_suffstats(
            SufficientStats.from_sample(sample_1), SufficientStats.from_sample(sample_2)
        ),
    )


@parametrize_test_type
def test_dual_entry_paired_ttest(paired_arms: Arms, test_type: str) -> None:
    y1, _, y2, _ = paired_arms
    method = PairedTTest(alpha=0.05, test_type=test_type)
    sample_1, sample_2 = Sample(y1), Sample(y2)
    _assert_bitwise_equal(
        method.from_samples(sample_1, sample_2),
        method.from_suffstats(PairedSufficientStats.from_samples(sample_1, sample_2)),
    )


@parametrize_test_type
def test_dual_entry_paired_cuped_ttest(paired_arms: Arms, test_type: str) -> None:
    y1, x1, y2, x2 = paired_arms
    method = PairedCupedTTest(alpha=0.05, test_type=test_type)
    sample_1, sample_2 = Sample(y1, cov_array=x1), Sample(y2, cov_array=x2)
    _assert_bitwise_equal(
        method.from_samples(sample_1, sample_2),
        method.from_suffstats(PairedSufficientStats.from_samples(sample_1, sample_2)),
    )


@parametrize_test_type
def test_dual_entry_ztest(
    proportion_case: tuple[float, float, float, float], test_type: str
) -> None:
    c1, n1, c2, n2 = proportion_case
    method = ZTest(alpha=0.05, test_type=test_type)
    fraction_1, fraction_2 = Fraction(c1, n1), Fraction(c2, n2)
    _assert_bitwise_equal(
        method.from_samples(fraction_1, fraction_2),
        method.from_suffstats(fraction_1, fraction_2),
    )


@parametrize_test_type
def test_dual_entry_ratio_delta(continuous_arms: Arms, test_type: str) -> None:
    """ratio-delta has no legacy baseline but must still honour the one-path contract."""
    y1, x1, y2, x2 = continuous_arms
    # Reuse the covariates as strictly positive-mean denominators.
    method = RatioDelta(alpha=0.05, test_type=test_type)
    ratio_1, ratio_2 = RatioSample(y1, x1 + 1.0), RatioSample(y2, x2 + 1.0)
    _assert_bitwise_equal(
        method.from_samples(ratio_1, ratio_2),
        method.from_suffstats(
            RatioSufficientStats.from_ratio_sample(ratio_1),
            RatioSufficientStats.from_ratio_sample(ratio_2),
        ),
    )
