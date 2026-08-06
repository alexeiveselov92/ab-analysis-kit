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


class TestTheCulpritDecomposition:
    """m14 DEC-5(c): which arm the mismatch is concentrated in.

    A decomposition of the chi-square the gate already computed — no new
    threshold, nothing that can change a decision.
    """

    def test_it_names_the_arm_with_the_largest_standardised_residual(self):
        from abkit.stats.srm import srm_culprit

        # b is starved: 1000/1000/600 against an even declared split
        arm, residual = srm_culprit(
            {"a": 1000, "b": 1000, "c": 600},
            {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3},
        )
        assert arm == "c"
        assert residual < 0, "the sign says TOO FEW, which is what the operator acts on"

    def test_an_over_allocated_arm_reads_positive(self):
        from abkit.stats.srm import srm_culprit

        arm, residual = srm_culprit(
            {"a": 1000, "b": 1000, "c": 1600},
            {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3},
        )
        assert arm == "c" and residual > 0

    def test_it_is_read_against_the_DECLARED_split_not_the_average(self):
        """A deliberately uneven design is not a mismatch: 20/40/40 observed
        against a 20/40/40 declaration has no culprit worth naming, and an
        implementation comparing arms to each other would blame `a`."""
        from abkit.stats.srm import srm_culprit

        arm, residual = srm_culprit(
            {"a": 200, "b": 400, "c": 400},
            {"a": 0.2, "b": 0.4, "c": 0.4},
        )
        assert abs(residual) < 1e-9, arm

    def test_it_agrees_with_the_gate_it_decomposes(self):
        """The residuals are the chi-square's own terms: their squares sum to
        the statistic scipy computes, so this cannot drift from the gate."""
        import numpy as np
        import scipy.stats as sps

        from abkit.stats.srm import srm_culprit

        observed = {"a": 1000, "b": 1000, "c": 600}
        shares = {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}
        counts = np.array([observed[v] for v in sorted(observed)], dtype=float)
        expected = counts.sum() / 3
        statistic, _ = sps.chisquare(f_obs=counts, f_exp=[expected] * 3)

        _arm, residual = srm_culprit(observed, shares)
        residuals = (counts - expected) / np.sqrt(expected)
        assert np.isclose(float((residuals**2).sum()), float(statistic))
        assert np.isclose(abs(residual), float(np.abs(residuals).max()))

    def test_degenerate_inputs_answer_None_rather_than_guessing(self):
        from abkit.stats.srm import srm_culprit

        assert srm_culprit({"a": 1}, {"a": 1.0}) is None
        assert srm_culprit({"a": 0, "b": 0}, {"a": 0.5, "b": 0.5}) is None
        assert srm_culprit({"a": 1, "b": 1}, {"a": 0.0, "b": 1.0}) is None
        assert srm_culprit({"a": 1, "b": 1}, {"a": 0.5, "c": 0.5}) is None


class TestTheGateLineNamesTheCulprit:
    """m14 DEC-5(c) on the surface an operator actually reads."""

    def test_three_arms_get_the_arm_and_the_direction(self):
        from abkit.stats.srm import srm_check

        result = srm_check(
            {"a": 1000, "b": 1000, "c": 600},
            {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3},
        )

        assert result.srm_flag
        assert "c has too few units" in result.describe()
        assert "σ)" in result.describe()

    def test_a_two_arm_line_is_unchanged(self):
        """With one treatment the residuals mirror each other, so naming one is
        a tautology — and it would move a `0.8.0` string."""
        from abkit.stats.srm import srm_check

        line = srm_check({"control": 6200, "treatment": 3800}, {"control": 0.5, "treatment": 0.5})

        assert line.srm_flag
        assert "has too few units" not in line.describe()
        assert line.describe().endswith("— effects untrustworthy")

    def test_a_healthy_gate_says_nothing_about_a_culprit(self):
        from abkit.stats.srm import srm_check

        ok = srm_check({"a": 1000, "b": 1000, "c": 1000}, {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3})

        assert not ok.srm_flag
        assert "units" not in ok.describe()
