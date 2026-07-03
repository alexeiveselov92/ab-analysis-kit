"""Tests for the SRM chi-square gate (architecture §5 step 4; statistics-changes.md §4).

SRM is checked against the declared ``expected_split`` and the p-value must equal a
direct ``scipy.stats.chisquare`` on the same counts.
"""

from __future__ import annotations

import numpy as np
import pytest
import scipy.stats as sps

from abkit.stats.exceptions import SampleValidationError
from abkit.stats.srm import DEFAULT_SRM_ALPHA, srm_check


def test_default_srm_alpha() -> None:
    assert DEFAULT_SRM_ALPHA == 0.001


def test_balanced_huge_counts_do_not_flag() -> None:
    result = srm_check({"a": 500_000, "b": 500_000}, {"a": 0.5, "b": 0.5})
    assert result.srm_flag is False
    assert result.pvalue == pytest.approx(1.0)


def test_62_38_on_10k_flags_with_tiny_pvalue() -> None:
    result = srm_check({"a": 6200, "b": 3800}, {"a": 0.5, "b": 0.5})
    assert result.srm_flag is True
    assert result.pvalue < 1e-100  # chi2 = 576 on 1 df


def test_pvalue_matches_scipy_chisquare_directly() -> None:
    counts = {"a": 720, "b": 280}
    result = srm_check(counts, {"a": 0.7, "b": 0.3})
    expected = sps.chisquare(f_obs=np.array([720.0, 280.0]), f_exp=1000.0 * np.array([0.7, 0.3]))
    assert result.pvalue == pytest.approx(float(expected.pvalue), rel=1e-12)


def test_three_variants_supported() -> None:
    counts = {"a": 3400, "b": 3300, "c": 3300}
    split = {"a": 1.0, "b": 1.0, "c": 1.0}
    result = srm_check(counts, split)
    expected = sps.chisquare(
        f_obs=np.array([3400.0, 3300.0, 3300.0]), f_exp=10000.0 * np.full(3, 1 / 3)
    )
    assert result.pvalue == pytest.approx(float(expected.pvalue), rel=1e-12)
    assert result.srm_flag is False


def test_expected_split_is_normalised() -> None:
    counts = {"a": 5100, "b": 4900}
    from_shares = srm_check(counts, {"a": 0.5, "b": 0.5})
    from_weights = srm_check(counts, {"a": 1, "b": 1})
    assert from_weights.pvalue == from_shares.pvalue
    assert from_weights.expected_share == {"a": 0.5, "b": 0.5}


def test_custom_alpha_changes_flag() -> None:
    counts = {"a": 5200, "b": 4800}  # chi2 = 16, p ≈ 6.3e-5
    assert srm_check(counts, {"a": 0.5, "b": 0.5}, alpha=0.001).srm_flag is True
    assert srm_check(counts, {"a": 0.5, "b": 0.5}, alpha=1e-6).srm_flag is False


def test_mismatched_variant_sets_raise() -> None:
    with pytest.raises(SampleValidationError, match="expected_split variants"):
        srm_check({"a": 100, "b": 100}, {"a": 0.5, "c": 0.5})


def test_single_variant_raises() -> None:
    with pytest.raises(SampleValidationError, match="at least two"):
        srm_check({"a": 100}, {"a": 1.0})


def test_zero_total_raises() -> None:
    with pytest.raises(SampleValidationError, match="all be zero"):
        srm_check({"a": 0, "b": 0}, {"a": 0.5, "b": 0.5})


def test_negative_count_raises() -> None:
    with pytest.raises(SampleValidationError, match="non-negative"):
        srm_check({"a": -1, "b": 10}, {"a": 0.5, "b": 0.5})


def test_non_positive_expected_share_raises() -> None:
    with pytest.raises(SampleValidationError, match="positive"):
        srm_check({"a": 100, "b": 100}, {"a": 0.0, "b": 1.0})


def test_describe_flagged_is_loud_with_shares() -> None:
    result = srm_check({"a": 6200, "b": 3800}, {"a": 0.5, "b": 0.5})
    message = result.describe()
    assert "SRM FAILED" in message
    assert "0.62" in message and "0.38" in message  # observed shares
    assert "0.50" in message  # expected shares


def test_describe_ok_when_not_flagged() -> None:
    message = srm_check({"a": 5000, "b": 5000}, {"a": 0.5, "b": 0.5}).describe()
    assert message.startswith("SRM ok")
    assert "0.50" in message
