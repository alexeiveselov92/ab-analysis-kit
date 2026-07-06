"""Pure sizing-engine tests (m5-implementation-plan.md WP6).

KAT vs the legacy-transcribed ``abkit.stats.power`` solves + the round-trip property
(required-N re-solved back to ~target power), plus the ``--baseline`` grammar.
"""

from __future__ import annotations

import math

import pytest

from abkit.planning.sizing import (
    FRACTION,
    SAMPLE,
    BaselineMoments,
    moments_from_override,
    parse_baseline_overrides,
    size_comparison,
)
from abkit.stats.power import (
    get_fraction_mde,
    get_fraction_power,
    get_fraction_sample_size,
    get_ttest_mde,
    get_ttest_power,
    get_ttest_sample_size,
)

# ── sample (t-test / CUPED-on-raw) ───────────────────────────────────────────────


def test_sample_matches_power_module_kat():
    m = BaselineMoments(SAMPLE, baseline=12.5, n=5000, n_other=5000, std=8.0, source="x")
    r = size_comparison(
        m, test_type="relative", alpha=0.05, power=0.8, target_mde=0.05, plan_ratio=1.0
    )
    assert r.required_n == get_ttest_sample_size(
        12.5, 8.0, 0.05, test_type="relative", alpha=0.05, power=0.8, ratio=1.0
    )
    assert r.achievable_mde == get_ttest_mde(
        12.5, 8.0, 5000, test_type="relative", alpha=0.05, power=0.8, ratio=1.0
    )
    assert r.achieved_power == get_ttest_power(
        12.5, 8.0, 5000, 0.05, test_type="relative", alpha=0.05, ratio=1.0
    )


def test_sample_required_n_roundtrips_to_target_power():
    m = BaselineMoments(SAMPLE, baseline=100.0, n=1, n_other=1, std=25.0, source="x")
    r = size_comparison(
        m, test_type="relative", alpha=0.05, power=0.8, target_mde=0.02, plan_ratio=1.0
    )
    # re-solving power at the required N recovers ~0.8 (statsmodels rounds N up)
    achieved = get_ttest_power(
        100.0, 25.0, r.required_n, 0.02, test_type="relative", alpha=0.05, ratio=1.0
    )
    assert achieved >= 0.8
    assert achieved < 0.83  # rounding overshoot is small, not a different design


def test_sample_uses_observed_ratio_for_retrospective_but_plan_ratio_for_required():
    # unbalanced observed allocation; a balanced forward plan
    m = BaselineMoments(SAMPLE, baseline=10.0, n=2000, n_other=8000, std=5.0, source="x")
    r = size_comparison(
        m, test_type="relative", alpha=0.05, power=0.8, target_mde=0.05, plan_ratio=1.0
    )
    assert r.required_n == get_ttest_sample_size(
        10.0, 5.0, 0.05, test_type="relative", alpha=0.05, power=0.8, ratio=1.0
    )
    assert r.achievable_mde == get_ttest_mde(
        10.0, 5.0, 2000, test_type="relative", alpha=0.05, power=0.8, ratio=8000 / 2000
    )


# ── fraction (z-test) ────────────────────────────────────────────────────────────


def test_fraction_matches_power_module_kat():
    m = BaselineMoments(FRACTION, baseline=0.1, n=10000, n_other=10000, std=None, source="x")
    r = size_comparison(
        m, test_type="relative", alpha=0.05, power=0.8, target_mde=0.05, plan_ratio=1.0
    )
    assert r.required_n == get_fraction_sample_size(
        0.1, 0.05, test_type="relative", alpha=0.05, power=0.8, ratio=1.0
    )
    assert r.achievable_mde == get_fraction_mde(
        0.1, 10000, test_type="relative", alpha=0.05, power=0.8, ratio=1.0
    )
    assert r.achieved_power == get_fraction_power(
        0.1, 10000, 0.05, test_type="relative", alpha=0.05, ratio=1.0
    )


# ── no target MDE ──────────────────────────────────────────────────────────────


def test_no_target_mde_reports_only_achievable():
    m = BaselineMoments(SAMPLE, baseline=12.5, n=5000, n_other=5000, std=8.0, source="x")
    r = size_comparison(
        m, test_type="relative", alpha=0.05, power=0.8, target_mde=None, plan_ratio=1.0
    )
    assert r.required_n is None
    assert r.achieved_power is None
    assert r.achievable_mde is not None and math.isfinite(r.achievable_mde)


def test_degenerate_size_gives_inf_mde_not_crash():
    m = BaselineMoments(SAMPLE, baseline=12.5, n=1, n_other=1, std=8.0, source="x")
    r = size_comparison(
        m, test_type="relative", alpha=0.05, power=0.8, target_mde=None, plan_ratio=1.0
    )
    assert r.achievable_mde == float("inf")


# ── infeasible / zero-effect targets return ∞, never crash (review finding) ──────


def test_fraction_infeasible_relative_target_is_inf_not_crash():
    # prop*(1+mde) = 0.92*1.10 = 1.012 > 1 ⇒ proportion_effectsize is NaN; the solve
    # would raise. size_comparison must return ∞ (underpowered), not abort the plan.
    m = BaselineMoments(FRACTION, baseline=0.92, n=50000, n_other=50000, std=None, source="x")
    r = size_comparison(
        m, test_type="relative", alpha=0.05, power=0.8, target_mde=0.10, plan_ratio=1.0
    )
    assert r.required_n == float("inf")
    assert r.achieved_power is None  # an infeasible target has no achievable power
    assert math.isfinite(r.achievable_mde)  # the retrospective bound is still fine


def test_fraction_infeasible_absolute_target_is_inf():
    m = BaselineMoments(FRACTION, baseline=0.95, n=50000, n_other=50000, std=None, source="x")
    r = size_comparison(
        m, test_type="absolute", alpha=0.05, power=0.8, target_mde=0.1, plan_ratio=1.0
    )
    assert r.required_n == float("inf")


def test_sample_zero_mean_relative_target_is_inf_not_crash():
    # a relative MDE on a zero baseline mean ⇒ zero standardized effect; statsmodels
    # raises "Cannot detect an effect-size of 0" — we return ∞ instead.
    m = BaselineMoments(SAMPLE, baseline=0.0, n=5000, n_other=5000, std=5.0, source="x")
    r = size_comparison(
        m, test_type="relative", alpha=0.05, power=0.8, target_mde=0.05, plan_ratio=1.0
    )
    assert r.required_n == float("inf")


def test_absolute_test_type_passes_through():
    m = BaselineMoments(SAMPLE, baseline=100.0, n=4000, n_other=4000, std=20.0, source="x")
    r = size_comparison(
        m, test_type="absolute", alpha=0.05, power=0.8, target_mde=2.0, plan_ratio=1.0
    )
    assert r.required_n == get_ttest_sample_size(
        100.0, 20.0, 2.0, test_type="absolute", alpha=0.05, power=0.8, ratio=1.0
    )


def test_observed_ratio_property():
    assert BaselineMoments(SAMPLE, 1.0, 2000, 6000, 1.0).observed_ratio == 3.0
    assert BaselineMoments(SAMPLE, 1.0, 0, 6000, 1.0).observed_ratio == 1.0  # guard /0


# ── --baseline grammar ───────────────────────────────────────────────────────────


def test_parse_baseline_overrides_valid():
    out = parse_baseline_overrides(("arpu:mean=12.5,std=8,n=5000", "cr:prop=0.1,n=10000"))
    assert out == {
        "arpu": {"mean": 12.5, "std": 8.0, "n": 5000.0},
        "cr": {"prop": 0.1, "n": 10000.0},
    }


@pytest.mark.parametrize(
    "spec",
    [
        "no_colon_here",
        ":mean=1,n=2",  # empty metric
        "arpu:mean",  # not field=value
        "arpu:bogus=1",  # unknown field
        "arpu:mean=notnum,n=2",  # not a number
        "arpu:",  # no fields
    ],
)
def test_parse_baseline_overrides_rejects_malformed(spec):
    with pytest.raises(ValueError):
        parse_baseline_overrides((spec,))


def test_moments_from_override_sample_and_fraction():
    s = moments_from_override(SAMPLE, {"mean": 12.5, "std": 8.0, "n": 5000})
    assert s.kind == SAMPLE and s.baseline == 12.5 and s.std == 8.0 and s.n == 5000
    assert s.n_other == 5000  # defaults to n
    f = moments_from_override(FRACTION, {"prop": 0.1, "n": 10000, "n_other": 9800})
    assert f.kind == FRACTION and f.baseline == 0.1 and f.std is None and f.n_other == 9800


@pytest.mark.parametrize(
    "kind,fields",
    [
        (SAMPLE, {"mean": 1.0, "std": 8.0}),  # missing n
        (SAMPLE, {"mean": 1.0, "n": 100}),  # missing std
        (SAMPLE, {"mean": 1.0, "std": 0.0, "n": 100}),  # non-positive std
        (FRACTION, {"n": 100}),  # missing prop
        (FRACTION, {"prop": 1.5, "n": 100}),  # prop out of range
        (FRACTION, {"prop": 0.1, "n": 0}),  # non-positive n
    ],
)
def test_moments_from_override_rejects_incomplete(kind, fields):
    with pytest.raises(ValueError):
        moments_from_override(kind, fields)
