"""Pure sizing-engine tests (m5-implementation-plan.md WP6; m6 WP-A runtime/ASN).

KAT vs the legacy-transcribed ``abkit.stats.power`` solves + the round-trip property
(required-N re-solved back to ~target power), plus the ``--baseline`` grammar; and the
WP-A runtime (days-to-N) + ASN (always-valid average sample number) additions, the ASN
Monte-Carlo cross-checked against an independent scalar simulation.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from abkit.planning.sizing import (
    FRACTION,
    SAMPLE,
    BaselineMoments,
    asn_for,
    moments_from_override,
    parse_baseline_overrides,
    runtime_for,
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
from abkit.stats.sequential import mixture_tau2

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


# ── runtime: days-to-N (WP-A) ──────────────────────────────────────────────────


def test_runtime_for_divides_required_n_by_rate():
    assert runtime_for(1000, 250.0) == 4.0  # 1000 units / 250 per day = 4 days


def test_runtime_for_none_target_and_no_rate():
    assert runtime_for(None, 250.0) is None  # no target ⇒ no days-to-N
    assert runtime_for(1000, 0.0) is None  # no usable rate ⇒ skipped


def test_runtime_for_infinite_required_n_is_infinite():
    assert runtime_for(float("inf"), 250.0) == float("inf")


# ── ASN: the always-valid average sample number (WP-A) ───────────────────────────

# a well-powered sample metric + a 60-day daily grid at 500 units/day/arm (horizon
# 30000/arm — far past required-N, so P(win) → 1 and the early-stop saving is large)
_ASN_M = BaselineMoments(SAMPLE, baseline=100.0, n=1, n_other=1, std=25.0, source="x")
_ASN_LOOK_DAYS = [float(d) for d in range(1, 61)]
_ASN_RATE = 500.0


def _asn(mde, look_days=None, rate=_ASN_RATE, **kw):
    return asn_for(
        _ASN_M,
        test_type="relative",
        target_mde=mde,
        alpha=0.05,
        plan_ratio=1.0,
        look_days=look_days if look_days is not None else _ASN_LOOK_DAYS,
        rate_control_per_day=rate,
        **kw,
    )


def test_asn_is_deterministic_across_calls():
    # a read-only planner must not wobble between two invocations on identical inputs
    assert _asn(0.05).asn_n_h1 == _asn(0.05).asn_n_h1


def test_asn_h1_stops_well_before_the_horizon():
    # the real sequential saving: under a true effect you conclude far before the planned
    # end (NOT below the fixed required-N — the always-valid CS is the price of peeking).
    a = _asn(0.05)
    assert a.asn_n_h1 < a.horizon_n
    assert a.asn_n_h1 < 0.2 * a.horizon_n  # a big early-stop saving in this overpowered grid


def test_asn_h0_runs_essentially_to_the_horizon():
    # under the null the CS rarely crosses ⇒ the expected stop is ~ the horizon,
    # and strictly later than under the true effect.
    a = _asn(0.05)
    assert a.asn_n_h0 > 0.95 * a.horizon_n
    assert a.asn_n_h0 > a.asn_n_h1


def test_asn_decreases_as_the_true_effect_grows():
    # a larger true effect crosses the boundary sooner (floored by the first look's N)
    asns = [_asn(mde).asn_n_h1 for mde in (0.02, 0.05, 0.10)]
    assert asns[0] > asns[1] > asns[2] - 1e-9  # monotone non-increasing (ties at the floor)


def test_asn_prob_win_is_a_probability():
    a = _asn(0.05)
    assert 0.0 <= a.prob_win_by_horizon <= 1.0


def test_asn_none_without_target_or_enough_looks():
    assert _asn(None) is None  # no target MDE
    assert _asn(0.05, look_days=[1.0]) is None  # a single look cannot peek
    # a zero-baseline relative effect is undetectable (δ_abs = 0)
    zero = BaselineMoments(SAMPLE, baseline=0.0, n=1, n_other=1, std=5.0, source="x")
    assert (
        asn_for(
            zero,
            test_type="relative",
            target_mde=0.05,
            alpha=0.05,
            plan_ratio=1.0,
            look_days=_ASN_LOOK_DAYS,
            rate_control_per_day=_ASN_RATE,
        )
        is None
    )


def test_asn_matches_an_independent_scalar_simulation():
    """Cross-check the vectorised MC against a from-scratch scalar first-passage loop.

    The plan's ASN validation (m6-implementation-plan.md WP-A): an independent
    implementation — a plain per-trajectory Python loop with its OWN RNG and its OWN
    closed-form boundary (the documented mixture radius, re-derived here, not
    ``sizing._cs_radius``) — must agree with the shipped engine within Monte-Carlo noise.
    """
    mde, alpha = 0.05, 0.05
    a = _asn(mde)

    mean, std = 100.0, 25.0
    delta = mean * mde  # absolute-scale target effect
    var_factor = std * std * 2.0  # base_var * (1 + 1/ratio), ratio = 1
    # usable looks: ≥1 control unit; here every day ≥1 (rate 500), strictly increasing
    n_arr = np.array([_ASN_RATE * d for d in _ASN_LOOK_DAYS])
    v_arr = var_factor / n_arr
    tau2 = mixture_tau2(v_arr[0], alpha)

    def radius(v):  # the documented normal-mixture CS half-width (independent derivation)
        return math.sqrt(
            (2.0 * v * (v + tau2) / tau2) * (math.log(1.0 / alpha) + 0.5 * math.log((v + tau2) / v))
        )

    rad = [radius(v) for v in v_arr]
    info = 1.0 / v_arr

    def simulate(true_delta, seed):
        rng = np.random.default_rng(seed)
        stops = []
        for _ in range(4000):
            s = 0.0
            prev_i = 0.0
            stop = float(n_arr[-1])
            for k in range(len(n_arr)):
                di = info[k] - prev_i
                s += true_delta * di + math.sqrt(di) * rng.standard_normal()
                prev_i = info[k]
                if abs(s / info[k]) > rad[k]:
                    stop = float(n_arr[k])
                    break
            stops.append(stop)
        return sum(stops) / len(stops)

    ref_h1 = simulate(delta, seed=777)
    ref_h0 = simulate(0.0, seed=778)
    # agree within ~6% — both are MC estimates of the same first-passage expectation
    assert abs(a.asn_n_h1 - ref_h1) / ref_h1 < 0.06
    assert abs(a.asn_n_h0 - ref_h0) / ref_h0 < 0.06


def test_asn_fraction_metric_is_supported():
    m = BaselineMoments(FRACTION, baseline=0.2, n=1, n_other=1, std=None, source="x")
    a = asn_for(
        m,
        test_type="relative",
        target_mde=0.1,
        alpha=0.05,
        plan_ratio=1.0,
        look_days=[float(d) for d in range(1, 31)],
        rate_control_per_day=2000.0,
    )
    assert a is not None
    assert a.asn_n_h1 < a.horizon_n
    assert a.asn_n_h0 > a.asn_n_h1
