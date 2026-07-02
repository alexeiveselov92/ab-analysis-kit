"""Tests for Chan/Welford-style mergeable moments (docs/specs/statistics-changes.md §1.1).

The must-hold property: folding ``merge_*`` over ANY chunking of the units equals
the single-pass sufficient statistics of the full array at relative 1e-9 —
including on the heavy-tailed revenue fixture (the quorum golden-tolerance gate).
"""

from __future__ import annotations

from functools import reduce

import numpy as np
import pytest

from abkit.stats.accumulate import merge_joint_moments, merge_ratio_suffstats, merge_suffstats
from abkit.stats.exceptions import SampleValidationError
from abkit.stats.samples import (
    JointMoments,
    RatioSample,
    RatioSufficientStats,
    Sample,
    SufficientStats,
)

REL = 1e-9


def _random_chunks(rng: np.random.Generator, size: int, n_chunks: int) -> list[np.ndarray]:
    bounds = np.sort(rng.choice(np.arange(1, size), size=n_chunks - 1, replace=False))
    return np.split(np.arange(size), bounds)


def test_chunked_merge_suffstats_equals_full(rng: np.random.Generator) -> None:
    values = rng.lognormal(mean=0.5, sigma=1.5, size=4000)
    covariate = 0.7 * values + rng.normal(0.0, 1.0, size=4000)
    full = SufficientStats.from_sample(Sample(values, cov_array=covariate, name="c"))

    chunks = _random_chunks(rng, values.size, n_chunks=7)
    parts = [
        SufficientStats.from_sample(Sample(values[idx], cov_array=covariate[idx], name="c"))
        for idx in chunks
    ]
    merged = reduce(merge_suffstats, parts)

    assert merged.n == full.n
    assert merged.name == "c"
    assert merged.mean == pytest.approx(full.mean, rel=REL)
    assert merged.m2 == pytest.approx(full.m2, rel=REL)
    assert merged.cov_mean == pytest.approx(full.cov_mean, rel=REL)
    assert merged.cov_m2 == pytest.approx(full.cov_m2, rel=REL)
    assert merged.cross_c == pytest.approx(full.cross_c, rel=REL)
    # and against direct numpy (values AND covariate moments AND cross term)
    assert merged.var == pytest.approx(float(np.var(values)), rel=REL)
    assert merged.cov_var == pytest.approx(float(np.var(covariate)), rel=REL)
    assert merged.cov1_value_covariate == pytest.approx(
        float(np.cov(values, covariate, ddof=1)[0, 1]), rel=REL
    )


def test_chunked_merge_suffstats_without_covariate(rng: np.random.Generator) -> None:
    values = rng.normal(100.0, 20.0, size=999)
    chunks = _random_chunks(rng, values.size, n_chunks=4)
    merged = reduce(
        merge_suffstats, (SufficientStats.from_sample(Sample(values[idx])) for idx in chunks)
    )
    assert not merged.has_covariate
    assert merged.var == pytest.approx(float(np.var(values)), rel=REL)


def test_heavy_tailed_merge_stability(
    rng: np.random.Generator,
    heavy_tailed_values: np.ndarray,
    heavy_tailed_covariate: np.ndarray,
) -> None:
    """The Welford/Chan requirement: heavy-tailed merged var matches np.var at 1e-9."""
    chunks = _random_chunks(rng, heavy_tailed_values.size, n_chunks=10)
    parts = [
        SufficientStats.from_sample(
            Sample(heavy_tailed_values[idx], cov_array=heavy_tailed_covariate[idx])
        )
        for idx in chunks
    ]
    merged = reduce(merge_suffstats, parts)
    assert merged.var == pytest.approx(float(np.var(heavy_tailed_values)), rel=REL)
    assert merged.cov_var == pytest.approx(float(np.var(heavy_tailed_covariate)), rel=REL)
    assert merged.cov1_value_covariate == pytest.approx(
        float(np.cov(heavy_tailed_values, heavy_tailed_covariate, ddof=1)[0, 1]), rel=REL
    )


def test_merge_suffstats_covariate_xor_raises(rng: np.random.Generator) -> None:
    values = rng.normal(size=20)
    with_cov = SufficientStats.from_sample(Sample(values, cov_array=values * 2))
    without_cov = SufficientStats.from_sample(Sample(values))
    with pytest.raises(SampleValidationError, match="covariate"):
        merge_suffstats(with_cov, without_cov)
    with pytest.raises(SampleValidationError, match="covariate"):
        merge_suffstats(without_cov, with_cov)


def test_merge_suffstats_name_semantics() -> None:
    a = SufficientStats(n=2, mean=1.0, m2=1.0, name="control")
    b = SufficientStats(n=3, mean=2.0, m2=1.0, name="control")
    c = SufficientStats(n=3, mean=2.0, m2=1.0, name="treatment")
    assert merge_suffstats(a, b).name == "control"  # kept when equal
    assert merge_suffstats(a, c).name is None  # dropped when different


# --- ratio suffstats -----------------------------------------------------------------


def test_chunked_merge_ratio_suffstats_equals_full(rng: np.random.Generator) -> None:
    numerator = rng.lognormal(0.0, 2.0, size=3000)
    denominator = rng.lognormal(0.0, 1.0, size=3000) + 1.0
    full = RatioSufficientStats.from_ratio_sample(RatioSample(numerator, denominator, name="c"))

    chunks = _random_chunks(rng, numerator.size, n_chunks=6)
    parts = [
        RatioSufficientStats.from_ratio_sample(
            RatioSample(numerator[idx], denominator[idx], name="c")
        )
        for idx in chunks
    ]
    merged = reduce(merge_ratio_suffstats, parts)

    assert merged.n == full.n
    assert merged.name == "c"
    assert merged.mean_num == pytest.approx(full.mean_num, rel=REL)
    assert merged.m2_num == pytest.approx(full.m2_num, rel=REL)
    assert merged.mean_den == pytest.approx(full.mean_den, rel=REL)
    assert merged.m2_den == pytest.approx(full.m2_den, rel=REL)
    assert merged.c_nd == pytest.approx(full.c_nd, rel=REL)
    assert merged.ratio == pytest.approx(float(np.mean(numerator) / np.mean(denominator)), rel=REL)


def test_merge_ratio_suffstats_name_semantics() -> None:
    a = RatioSufficientStats(n=2, mean_num=1.0, m2_num=0.0, mean_den=1.0, m2_den=0.0, c_nd=0.0)
    b = RatioSufficientStats(
        n=2, mean_num=1.0, m2_num=0.0, mean_den=1.0, m2_den=0.0, c_nd=0.0, name="x"
    )
    assert merge_ratio_suffstats(a, b).name is None


# --- joint moments -------------------------------------------------------------------


def test_chunked_merge_joint_moments_equals_full(rng: np.random.Generator) -> None:
    y1 = rng.lognormal(0.0, 1.5, size=2500)
    y2 = 1.4 * y1 + rng.normal(0.0, 1.0, size=2500)
    x1 = 0.8 * y1 + rng.normal(0.0, 1.0, size=2500)
    labels = ("y1", "y2", "x1")
    full = JointMoments.from_arrays(y1, y2, x1, labels=labels)

    chunks = _random_chunks(rng, y1.size, n_chunks=5)
    parts = [JointMoments.from_arrays(y1[idx], y2[idx], x1[idx], labels=labels) for idx in chunks]
    merged = reduce(merge_joint_moments, parts)

    assert merged.n == full.n
    assert merged.labels == labels
    np.testing.assert_allclose(merged.mean, full.mean, rtol=REL)
    np.testing.assert_allclose(merged.comoment, full.comoment, rtol=REL)
    # spot-check the accessors against direct numpy on the full arrays
    assert merged.var0(0) == pytest.approx(float(np.var(y1)), rel=REL)
    assert merged.cov1(0, 1) == pytest.approx(float(np.cov(y1, y2)[0, 1]), rel=REL)


def test_merge_joint_moments_label_mismatch_raises(rng: np.random.Generator) -> None:
    y = rng.normal(size=10)
    a = JointMoments.from_arrays(y, y * 2, labels=("y1", "y2"))
    b = JointMoments.from_arrays(y, y * 2, labels=("y1", "x1"))
    with pytest.raises(SampleValidationError, match="labels"):
        merge_joint_moments(a, b)
