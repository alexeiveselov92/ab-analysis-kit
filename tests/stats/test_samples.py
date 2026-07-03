"""Tests for ``abkit.stats.samples`` — containers and the MIXED-ddof convention.

Baseline fact #1 (docs/specs/statistics-baseline.md §1, statistics-changes.md §1.2):
``np.var``/``np.std`` terms are ddof=0, ``np.cov`` terms are ddof=1. The accessors
must expose exactly that split — never a single normalised ddof.
"""

from __future__ import annotations

import numpy as np
import pytest

from abkit.stats.exceptions import AbkitStatsWarning, SampleValidationError
from abkit.stats.samples import (
    PAIRED_CUPED_LABELS,
    PAIRED_LABELS,
    Fraction,
    JointMoments,
    PairedSufficientStats,
    RatioSample,
    RatioSufficientStats,
    Sample,
    SufficientStats,
    align_paired,
)


def _make_arrays(rng: np.random.Generator, n: int = 200) -> tuple[np.ndarray, np.ndarray]:
    y = rng.lognormal(mean=0.5, sigma=1.0, size=n)
    x = 0.7 * y + rng.normal(0.0, 1.0, size=n)
    return y, x


# --- Sample validation ------------------------------------------------------------


def test_sample_empty_array_raises() -> None:
    with pytest.raises(SampleValidationError, match="must not be empty"):
        Sample([])


def test_sample_two_dimensional_array_raises() -> None:
    with pytest.raises(SampleValidationError, match="one-dimensional"):
        Sample(np.ones((3, 2)))


def test_sample_misaligned_cov_array_raises() -> None:
    with pytest.raises(SampleValidationError, match="cov_array length"):
        Sample([1.0, 2.0, 3.0], cov_array=[1.0, 2.0])


def test_sample_two_dimensional_cov_array_raises() -> None:
    with pytest.raises(SampleValidationError, match="one-dimensional"):
        Sample([1.0, 2.0], cov_array=np.ones((1, 2)))


def test_sample_misaligned_categories_raises() -> None:
    with pytest.raises(SampleValidationError, match="categories_array"):
        Sample([1.0, 2.0, 3.0], categories_array=["a", "b"])


def test_sample_misaligned_pair_ids_raises() -> None:
    with pytest.raises(SampleValidationError, match="pair_ids"):
        Sample([1.0, 2.0, 3.0], pair_ids=[1, 2])


def test_sample_duplicate_pair_ids_raises() -> None:
    with pytest.raises(SampleValidationError, match="unique"):
        Sample([1.0, 2.0, 3.0], pair_ids=[1, 2, 2])


# --- Sample derived stats (exact numpy ddof=0 parity) ------------------------------


def test_sample_derived_stats_match_numpy_ddof0_exactly(rng: np.random.Generator) -> None:
    y, x = _make_arrays(rng)
    sample = Sample(y, cov_array=x, name="control")
    assert sample.sample_size == y.size
    assert sample.mean == float(np.mean(y))
    assert sample.std == float(np.std(y))  # ddof=0, exact
    assert sample.var == float(np.std(y)) ** 2
    assert sample.cov_mean == float(np.mean(x))
    assert sample.cov_std == float(np.std(x))
    assert sample.cov_var == float(np.std(x)) ** 2
    assert sample.name == "control"


def test_sample_corr_coef_matches_numpy_corrcoef(rng: np.random.Generator) -> None:
    y, x = _make_arrays(rng)
    sample = Sample(y, cov_array=x)
    assert sample.corr_coef == float(np.corrcoef(y, x)[0, 1])


def test_sample_without_covariate_has_no_cov_stats() -> None:
    sample = Sample([1.0, 2.0, 3.0])
    assert sample.cov_array is None
    assert sample.cov_mean is None
    assert sample.corr_coef is None


def test_sample_category_counts() -> None:
    sample = Sample([1.0, 2.0, 3.0, 4.0], categories_array=["a", "b", "a", "a"])
    assert sample.category_counts() == {"a": 3, "b": 1}
    assert Sample([1.0]).category_counts() == {}


# --- Fraction -----------------------------------------------------------------------


def test_fraction_derived_stats() -> None:
    fraction = Fraction(count=30, nobs=200, name="control")
    assert fraction.prop == 0.15
    assert fraction.std == pytest.approx(float(np.sqrt(0.15 * 0.85 / 200)), rel=1e-15)
    assert fraction.sample_size == 200


@pytest.mark.parametrize("count,nobs", [(1, 0), (1, -5), (-1, 10), (11, 10)])
def test_fraction_validation(count: float, nobs: float) -> None:
    with pytest.raises(SampleValidationError):
        Fraction(count=count, nobs=nobs)


# --- RatioSample --------------------------------------------------------------------


def test_ratio_sample_misaligned_raises() -> None:
    with pytest.raises(SampleValidationError, match="numerator length"):
        RatioSample([1.0, 2.0], [1.0, 2.0, 3.0])


def test_ratio_sample_empty_raises() -> None:
    with pytest.raises(SampleValidationError, match="must not be empty"):
        RatioSample([], [])


# --- SufficientStats: construction & mixed-ddof accessors ---------------------------


def test_suffstats_constructor_validation() -> None:
    with pytest.raises(SampleValidationError, match="n must be positive"):
        SufficientStats(n=0, mean=0.0, m2=0.0)
    with pytest.raises(SampleValidationError, match="m2 must be non-negative"):
        SufficientStats(n=2, mean=0.0, m2=-1.0)
    with pytest.raises(SampleValidationError, match="provided together"):
        SufficientStats(n=2, mean=0.0, m2=1.0, cov_mean=1.0)  # partial covariate moments


def test_suffstats_from_sample_matches_numpy(rng: np.random.Generator) -> None:
    y, x = _make_arrays(rng)
    stats = SufficientStats.from_sample(Sample(y, cov_array=x, name="c"))
    assert stats.n == y.size
    assert stats.sample_size == y.size
    assert stats.name == "c"
    assert stats.mean == pytest.approx(float(np.mean(y)), rel=1e-12)
    assert stats.var == pytest.approx(float(np.var(y)), rel=1e-12)  # ddof=0 parity
    assert stats.std == pytest.approx(float(np.std(y)), rel=1e-12)
    assert stats.cov_mean == pytest.approx(float(np.mean(x)), rel=1e-12)
    assert stats.cov_var == pytest.approx(float(np.var(x)), rel=1e-12)
    # np.cov parity is ddof=1 — the CUPED θ numerator term (baseline fact #1).
    assert stats.cov1_value_covariate == pytest.approx(float(np.cov(y, x, ddof=1)[0, 1]), rel=1e-12)
    assert stats.cov1_value_covariate == pytest.approx(float(np.cov(y, x)[0, 1]), rel=1e-12)
    assert stats.corr_coef == pytest.approx(float(np.corrcoef(y, x)[0, 1]), rel=1e-12)


def test_suffstats_mixed_ddof_accessors_differ(rng: np.random.Generator) -> None:
    """THE point of the design: var divides by n, cov1 by n−1 — they must differ."""
    y, _ = _make_arrays(rng, n=50)
    stats = SufficientStats.from_sample(Sample(y, cov_array=y))  # X == Y
    n = stats.n
    # var(Y) = m2/n but cov1(Y, Y) = m2/(n−1): same raw moment, different divisor.
    assert stats.var == pytest.approx(stats.m2 / n, rel=1e-15)
    assert stats.cov1_value_covariate == pytest.approx(stats.m2 / (n - 1), rel=1e-15)
    assert stats.cov1_value_covariate != stats.var
    assert stats.cov1_value_covariate == pytest.approx(stats.var * n / (n - 1), rel=1e-12)


def test_suffstats_from_sample_without_covariate(rng: np.random.Generator) -> None:
    y, _ = _make_arrays(rng)
    stats = SufficientStats.from_sample(Sample(y))
    assert not stats.has_covariate
    assert stats.var == pytest.approx(float(np.var(y)), rel=1e-12)
    with pytest.raises(SampleValidationError, match="covariate"):
        _ = stats.cov_var
    with pytest.raises(SampleValidationError, match="covariate"):
        _ = stats.cov1_value_covariate


def test_suffstats_cov1_requires_two_units() -> None:
    stats = SufficientStats(n=1, mean=1.0, m2=0.0, cov_mean=1.0, cov_m2=0.0, cross_c=0.0)
    with pytest.raises(SampleValidationError, match="at least two units"):
        _ = stats.cov1_value_covariate


def test_suffstats_corr_coef_nan_on_degenerate_moments() -> None:
    stats = SufficientStats(n=3, mean=1.0, m2=0.0, cov_mean=1.0, cov_m2=1.0, cross_c=0.0)
    assert np.isnan(stats.corr_coef)


def test_suffstats_heavy_tailed_var_matches_numpy_at_rel_1e9(
    heavy_tailed_sample: Sample,
) -> None:
    """Quorum must-fix fixture: heavy-tailed revenue variance at relative 1e-9."""
    stats = SufficientStats.from_sample(heavy_tailed_sample)
    values = heavy_tailed_sample.array
    covariate = heavy_tailed_sample.cov_array
    assert covariate is not None
    assert stats.var == pytest.approx(float(np.var(values)), rel=1e-9)
    assert stats.cov_var == pytest.approx(float(np.var(covariate)), rel=1e-9)
    assert stats.cov1_value_covariate == pytest.approx(
        float(np.cov(values, covariate, ddof=1)[0, 1]), rel=1e-9
    )


# --- RatioSufficientStats ------------------------------------------------------------


def test_ratio_suffstats_from_ratio_sample(rng: np.random.Generator) -> None:
    num = rng.lognormal(0.0, 1.0, size=300)
    den = rng.lognormal(0.0, 0.5, size=300) + 1.0
    stats = RatioSufficientStats.from_ratio_sample(RatioSample(num, den, name="c"))
    assert stats.n == 300
    assert stats.sample_size == 300
    assert stats.name == "c"
    assert stats.mean_num == pytest.approx(float(np.mean(num)), rel=1e-12)
    assert stats.mean_den == pytest.approx(float(np.mean(den)), rel=1e-12)
    assert stats.m2_num == pytest.approx(float(np.var(num) * num.size), rel=1e-12)
    assert stats.m2_den == pytest.approx(float(np.var(den) * den.size), rel=1e-12)
    assert stats.c_nd == pytest.approx(float(np.cov(num, den, ddof=0)[0, 1] * num.size), rel=1e-12)
    assert stats.ratio == pytest.approx(float(np.mean(num) / np.mean(den)), rel=1e-12)


def test_ratio_suffstats_validation() -> None:
    with pytest.raises(SampleValidationError, match="n must be positive"):
        RatioSufficientStats(n=0, mean_num=1.0, m2_num=0.0, mean_den=1.0, m2_den=0.0, c_nd=0.0)


# --- JointMoments ---------------------------------------------------------------------


def test_joint_moments_var0_cov1_match_numpy(rng: np.random.Generator) -> None:
    y1 = rng.normal(10.0, 3.0, size=500)
    y2 = 1.5 * y1 + rng.normal(0.0, 1.0, size=500)
    moments = JointMoments.from_arrays(y1, y2, labels=("y1", "y2"))
    assert moments.n == 500
    assert moments.mean[0] == pytest.approx(float(np.mean(y1)), rel=1e-12)
    assert moments.var0(0) == pytest.approx(float(np.var(y1)), rel=1e-12)
    assert moments.var0(1) == pytest.approx(float(np.var(y2)), rel=1e-12)
    assert moments.cov1(0, 1) == pytest.approx(float(np.cov(y1, y2)[0, 1]), rel=1e-12)
    # mixed-ddof: var0 and cov1 of the same series use different divisors
    assert moments.cov1(0, 0) == pytest.approx(moments.var0(0) * 500 / 499, rel=1e-12)


def test_joint_moments_linear_combinations_match_numpy(rng: np.random.Generator) -> None:
    """Hand-built combo d = y2 − y1: linear reads must equal direct numpy on d."""
    y1 = rng.normal(10.0, 3.0, size=400)
    y2 = 1.5 * y1 + rng.normal(0.0, 1.0, size=400)
    moments = JointMoments.from_arrays(y1, y2, labels=("y1", "y2"))
    d = y2 - y1
    w_d = np.array([-1.0, 1.0])
    w_1 = np.array([1.0, 0.0])
    assert moments.linear_mean(w_d) == pytest.approx(float(np.mean(d)), rel=1e-12)
    assert moments.linear_var0(w_d) == pytest.approx(float(np.var(d)), rel=1e-12)
    assert moments.linear_cov1(w_d, w_1) == pytest.approx(float(np.cov(d, y1)[0, 1]), rel=1e-12)
    assert moments.linear_comoment(w_d, w_1) == pytest.approx(
        float(np.cov(d, y1)[0, 1] * (400 - 1)), rel=1e-12
    )


def test_joint_moments_default_labels_and_index() -> None:
    moments = JointMoments.from_arrays(np.array([1.0, 2.0]), np.array([3.0, 4.0]))
    assert moments.labels == ("z0", "z1")
    assert moments.index("z1") == 1
    with pytest.raises(SampleValidationError, match="unknown series label"):
        moments.index("nope")


def test_joint_moments_requires_at_least_one_array() -> None:
    with pytest.raises(SampleValidationError, match="at least one array"):
        JointMoments.from_arrays()


def test_joint_moments_cov1_requires_two_units() -> None:
    moments = JointMoments.from_arrays(np.array([1.0]), np.array([2.0]))
    with pytest.raises(SampleValidationError, match="at least two units"):
        moments.cov1(0, 1)
    with pytest.raises(SampleValidationError, match="at least two units"):
        moments.linear_cov1(np.array([1.0, 0.0]), np.array([0.0, 1.0]))


# --- PairedSufficientStats ------------------------------------------------------------


def test_paired_suffstats_size_mismatch_raises() -> None:
    with pytest.raises(SampleValidationError, match="equal-size"):
        PairedSufficientStats.from_samples(Sample([1.0, 2.0]), Sample([1.0, 2.0, 3.0]))


def test_paired_suffstats_covariate_xor_raises() -> None:
    with pytest.raises(SampleValidationError, match="both or neither"):
        PairedSufficientStats.from_samples(
            Sample([1.0, 2.0], cov_array=[1.0, 2.0]), Sample([1.0, 2.0])
        )


def test_paired_suffstats_labels(rng: np.random.Generator) -> None:
    y1, x1 = _make_arrays(rng, n=30)
    y2, x2 = _make_arrays(rng, n=30)
    plain = PairedSufficientStats.from_samples(Sample(y1, name="c"), Sample(y2, name="t"))
    assert plain.moments.labels == PAIRED_LABELS
    assert not plain.has_covariate
    assert plain.n == 30
    assert plain.name_1 == "c" and plain.name_2 == "t"
    cuped = PairedSufficientStats.from_samples(Sample(y1, cov_array=x1), Sample(y2, cov_array=x2))
    assert cuped.moments.labels == PAIRED_CUPED_LABELS
    assert cuped.has_covariate


def test_paired_suffstats_rejects_foreign_labels() -> None:
    moments = JointMoments.from_arrays(np.array([1.0, 2.0]), np.array([3.0, 4.0]))
    with pytest.raises(SampleValidationError, match="requires labels"):
        PairedSufficientStats(moments)


def test_paired_suffstats_weights_vector(rng: np.random.Generator) -> None:
    y1, _ = _make_arrays(rng, n=10)
    y2, _ = _make_arrays(rng, n=10)
    paired = PairedSufficientStats.from_samples(Sample(y1), Sample(y2))
    np.testing.assert_array_equal(paired.weights(y2=1.0, y1=-1.0), np.array([-1.0, 1.0]))


# --- align_paired ----------------------------------------------------------------------


def test_align_paired_intersection_sort_and_dropped_count() -> None:
    sample_1 = Sample(
        [30.0, 10.0, 20.0],
        cov_array=[3.0, 1.0, 2.0],
        categories_array=["c", "a", "b"],
        pair_ids=[3, 1, 2],
    )
    sample_2 = Sample([200.0, 300.0, 400.0], pair_ids=[2, 3, 4])
    with pytest.warns(AbkitStatsWarning, match="dropped 2"):
        aligned_1, aligned_2, dropped = align_paired(sample_1, sample_2)
    assert dropped == 2
    # sorted intersection of pair ids: [2, 3]; values re-ordered positionally
    np.testing.assert_array_equal(aligned_1.pair_ids, np.array([2, 3]))
    np.testing.assert_array_equal(aligned_2.pair_ids, np.array([2, 3]))
    np.testing.assert_array_equal(aligned_1.array, np.array([20.0, 30.0]))
    np.testing.assert_array_equal(aligned_2.array, np.array([200.0, 300.0]))
    # covariate and categories carried through the same permutation
    assert aligned_1.cov_array is not None
    np.testing.assert_array_equal(aligned_1.cov_array, np.array([2.0, 3.0]))
    assert aligned_1.categories_array is not None
    np.testing.assert_array_equal(aligned_1.categories_array, np.array(["b", "c"]))


def test_align_paired_no_drop_no_warning(recwarn: pytest.WarningsRecorder) -> None:
    sample_1 = Sample([1.0, 2.0], pair_ids=[1, 2])
    sample_2 = Sample([3.0, 4.0], pair_ids=[2, 1])
    aligned_1, aligned_2, dropped = align_paired(sample_1, sample_2)
    assert dropped == 0
    assert len(recwarn) == 0
    np.testing.assert_array_equal(aligned_1.array, np.array([1.0, 2.0]))
    np.testing.assert_array_equal(aligned_2.array, np.array([4.0, 3.0]))


def test_align_paired_no_common_ids_raises() -> None:
    with pytest.raises(SampleValidationError, match="no common pair_ids"):
        align_paired(Sample([1.0], pair_ids=[1]), Sample([2.0], pair_ids=[2]))


def test_align_paired_missing_pair_ids_raises() -> None:
    with pytest.raises(SampleValidationError, match="requires pair_ids"):
        align_paired(Sample([1.0]), Sample([2.0], pair_ids=[1]))
