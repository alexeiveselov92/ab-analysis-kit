"""Tests for the ``t-test`` exemplar method (baseline §3.1).

Known answers are computed with independent numpy/scipy code inside each test —
never by calling back into the implementation under test.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import scipy.stats as sps

from abkit.stats.exceptions import SampleValidationError
from abkit.stats.factory import create_method
from abkit.stats.parametric.ttest import TTest
from abkit.stats.power import get_ttest_mde
from abkit.stats.samples import Fraction, Sample, SufficientStats

Y1 = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
Y2 = np.array([2.0, 4.0, 4.0, 5.0, 7.0])
ALPHA = 0.05


def _expected_relative() -> tuple[float, float, float, float]:
    """Independent baseline §3.1 relative computation on (Y1, Y2)."""
    var_mean_1 = float(np.var(Y1)) / Y1.size
    var_mean_2 = float(np.var(Y2)) / Y2.size
    difference_mean = float(np.mean(Y2) - np.mean(Y1))
    difference_mean_var = var_mean_1 + var_mean_2
    mean_1 = float(np.mean(Y1))
    covariance = -var_mean_1
    relative_mu = difference_mean / mean_1
    relative_var = (
        difference_mean_var / mean_1**2
        + var_mean_1 * (difference_mean**2 / mean_1**4)
        - 2.0 * (difference_mean / mean_1**3) * covariance
    )
    distribution = sps.norm(loc=relative_mu, scale=math.sqrt(relative_var))
    pvalue = 2.0 * min(float(distribution.cdf(0.0)), float(distribution.sf(0.0)))
    left, right = (float(b) for b in distribution.ppf([ALPHA / 2.0, 1.0 - ALPHA / 2.0]))
    return relative_mu, pvalue, left, right


def test_relative_hand_computed_known_answer() -> None:
    method = create_method("t-test", alpha=ALPHA, params={"test_type": "relative"})
    result = method.compare_pair(Sample(Y1, name="control"), Sample(Y2, name="treatment"))
    expected_effect, expected_pvalue, expected_left, expected_right = _expected_relative()
    assert result.effect == pytest.approx(expected_effect, rel=1e-12)
    assert result.pvalue == pytest.approx(expected_pvalue, rel=1e-12)
    assert result.left_bound == pytest.approx(expected_left, rel=1e-12)
    assert result.right_bound == pytest.approx(expected_right, rel=1e-12)
    assert result.ci_length == pytest.approx(expected_right - expected_left, rel=1e-12)
    assert result.reject is (expected_pvalue < ALPHA)
    # effect is the point estimate on the REAL data (hygiene H9)
    assert result.effect == pytest.approx(
        float((np.mean(Y2) - np.mean(Y1)) / np.mean(Y1)), rel=1e-12
    )


def test_absolute_hand_computed_known_answer() -> None:
    method = create_method("t-test", alpha=ALPHA, params={"test_type": "absolute"})
    result = method.compare_pair(Sample(Y1, name="control"), Sample(Y2, name="treatment"))
    difference_mean = float(np.mean(Y2) - np.mean(Y1))
    scale = math.sqrt(float(np.var(Y1)) / Y1.size + float(np.var(Y2)) / Y2.size)
    distribution = sps.norm(loc=difference_mean, scale=scale)
    assert result.effect == pytest.approx(difference_mean, rel=1e-12)
    assert result.pvalue == pytest.approx(
        2.0 * min(float(distribution.cdf(0.0)), float(distribution.sf(0.0))), rel=1e-12
    )
    assert result.left_bound == pytest.approx(float(distribution.ppf(ALPHA / 2.0)), rel=1e-12)
    assert result.right_bound == pytest.approx(
        float(distribution.ppf(1.0 - ALPHA / 2.0)), rel=1e-12
    )


def test_absolute_vs_relative_differ() -> None:
    absolute = create_method("t-test", params={"test_type": "absolute"})
    relative = create_method("t-test", params={"test_type": "relative"})
    control, treatment = Sample(Y1, name="c"), Sample(Y2, name="t")
    result_abs = absolute.compare_pair(control, treatment)
    result_rel = relative.compare_pair(control, treatment)
    assert result_abs.effect != result_rel.effect
    assert result_abs.effect == pytest.approx(result_rel.effect * float(np.mean(Y1)), rel=1e-12)


def test_result_metadata_fields() -> None:
    method = create_method("t-test", alpha=0.01, params={"test_type": "absolute"})
    result = method.compare_pair(Sample(Y1, name="control"), Sample(Y2, name="treatment"))
    assert result.name_1 == "control" and result.name_2 == "treatment"
    assert result.value_1 == float(np.mean(Y1))
    assert result.value_2 == float(np.mean(Y2))
    assert result.std_1 == pytest.approx(float(np.std(Y1)), rel=1e-12)
    assert result.std_2 == pytest.approx(float(np.std(Y2)), rel=1e-12)
    assert result.size_1 == Y1.size and result.size_2 == Y2.size
    assert result.method_name == "t-test"
    assert result.method_params == {"test_type": "absolute"}  # == identity_params
    assert result.alpha == 0.01
    assert result.mde_1 is None and result.mde_2 is None  # calculate_mde defaults off
    assert result.effect_distribution is not None


def test_compare_three_groups_combinations_order() -> None:
    method = create_method("t-test")
    rng = np.random.default_rng(7)
    groups = [
        Sample(rng.normal(10.0, 2.0, size=100), name=name) for name in ("control", "t1", "t2")
    ]
    results = method.compare(groups)
    assert len(results) == 3
    assert [(r.name_1, r.name_2) for r in results] == [
        ("control", "t1"),
        ("control", "t2"),
        ("t1", "t2"),
    ]


def test_compare_requires_two_groups() -> None:
    with pytest.raises(SampleValidationError, match="at least two groups"):
        create_method("t-test").compare([Sample(Y1)])


def test_dual_entry_equality() -> None:
    """from_samples must equal from_suffstats — ONE math path by construction."""
    method = TTest(alpha=ALPHA, test_type="relative", calculate_mde=True)
    sample_1 = Sample(Y1, name="control")
    sample_2 = Sample(Y2, name="treatment")
    from_samples = method.from_samples(sample_1, sample_2)
    from_suffstats = method.from_suffstats(
        SufficientStats.from_sample(sample_1), SufficientStats.from_sample(sample_2)
    )
    assert from_samples.to_dict() == from_suffstats.to_dict()


def test_compare_pair_dispatches_on_input_kind() -> None:
    method = create_method("t-test")
    sample_1, sample_2 = Sample(Y1, name="c"), Sample(Y2, name="t")
    via_samples = method.compare_pair(sample_1, sample_2)
    via_suffstats = method.compare_pair(
        SufficientStats.from_sample(sample_1), SufficientStats.from_sample(sample_2)
    )
    assert via_samples.to_dict() == via_suffstats.to_dict()


def test_compare_pair_rejects_mixed_inputs() -> None:
    method = create_method("t-test")
    with pytest.raises(SampleValidationError, match="cannot mix"):
        method.compare_pair(Sample(Y1), SufficientStats.from_sample(Sample(Y2)))


def test_from_suffstats_rejects_wrong_type() -> None:
    method = create_method("t-test")
    with pytest.raises(SampleValidationError, match="must be SufficientStats"):
        method.from_suffstats(SufficientStats.from_sample(Sample(Y1)), Fraction(1, 10))


def test_calculate_mde_populates_both_arms_with_swapped_ratio() -> None:
    """mde_i uses ratio = n_other / n_this — verify against power.get_ttest_mde."""
    rng = np.random.default_rng(11)
    y1 = rng.normal(10.0, 4.0, size=400)
    y2 = rng.normal(10.5, 4.0, size=900)
    method = create_method(
        "t-test", alpha=ALPHA, params={"test_type": "relative", "calculate_mde": True}
    )
    result = method.compare_pair(Sample(y1, name="c"), Sample(y2, name="t"))
    expected_1 = get_ttest_mde(
        float(np.mean(y1)),
        float(np.std(y1)),
        400,
        test_type="relative",
        alpha=ALPHA,
        power=0.8,
        ratio=900 / 400,
    )
    expected_2 = get_ttest_mde(
        float(np.mean(y2)),
        float(np.std(y2)),
        900,
        test_type="relative",
        alpha=ALPHA,
        power=0.8,
        ratio=400 / 900,
    )
    assert result.mde_1 == pytest.approx(expected_1, rel=1e-12)
    assert result.mde_2 == pytest.approx(expected_2, rel=1e-12)
    assert result.mde_1 != result.mde_2  # asymmetric sizes → asymmetric MDEs


def test_zero_control_mean_relative_is_nan_with_h5_warning() -> None:
    method = create_method("t-test", params={"test_type": "relative"})
    result = method.compare_pair(
        Sample([0.0, 0.0, 0.0], name="c"), Sample([1.0, 2.0, 3.0], name="t")
    )
    assert math.isnan(result.effect)
    assert math.isnan(result.pvalue)
    assert math.isnan(result.left_bound)
    assert result.reject is False
    assert any("H5" in warning for warning in result.warnings)


def test_to_dict_drops_non_finite_and_excludes_distribution() -> None:
    method = create_method("t-test", params={"test_type": "relative"})
    result = method.compare_pair(
        Sample([0.0, 0.0, 0.0], name="c"), Sample([1.0, 2.0, 3.0], name="t")
    )
    payload = result.to_dict()
    assert payload["effect"] is None
    assert payload["pvalue"] is None
    assert payload["left_bound"] is None
    assert payload["right_bound"] is None
    assert payload["ci_length"] is None
    assert "effect_distribution" not in payload
    assert payload["reject"] is False
    assert payload["value_1"] == 0.0  # finite values survive untouched
    assert payload["method_name"] == "t-test"


def test_to_dict_finite_case_roundtrips_values() -> None:
    method = create_method("t-test", alpha=ALPHA, params={"test_type": "absolute"})
    result = method.compare_pair(Sample(Y1, name="c"), Sample(Y2, name="t"))
    payload = result.to_dict()
    assert payload["effect"] == result.effect
    assert payload["pvalue"] == result.pvalue
    assert payload["method_params"] == {"test_type": "absolute"}
    assert isinstance(payload["reject"], bool)
