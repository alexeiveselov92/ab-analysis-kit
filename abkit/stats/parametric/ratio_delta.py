"""``ratio-delta`` — principled delta-method ratio-metric test (statistics-changes.md §4).

A NEW method with no legacy baseline, hence no mixed-ddof debt: every variance
term uses ``ddof=0`` uniformly. Per arm the estimand is ``R = mean(numerator) /
mean(denominator)``; the per-unit linearisation ``L_u = (N_u − R·D_u) /
mean_den`` gives

    var0_L = (m2_num − 2·R·c_nd + R²·m2_den) / (n · mean_den²)
    var(R̂) = var0_L / n

Absolute effect ``R₂ − R₁`` with variance ``var(R̂₁) + var(R̂₂)``; relative via
the shared delta-method with denominator ``R₁`` and covariance ``−var(R̂₁)``
(the arms are independent, so numerator and denominator share only ``R̂₁``).

KNOWN-ANSWER contract (quorum must-fix): with the denominator identically 1,
``mean_den = 1``, ``m2_den = c_nd = 0``, so ``R_i = mean_i`` and ``var0_L_i =
var0_i`` — the method reproduces the t-test EXACTLY, absolute AND relative.

Hygiene H5: a zero/non-finite denominator mean in either arm yields NaN outputs
plus a recorded warning, never an exception.
"""

from __future__ import annotations

import math

from abkit.stats.base import TEST_TYPE_PARAM, BaseMethod, require_pair_type
from abkit.stats.effects import absolute_effect, normal_test, relative_delta_effect
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import RatioSample, RatioSufficientStats


def _arm_linearisation(
    stats: RatioSufficientStats, fallback_name: str
) -> tuple[float, float, list[str]]:
    """Per-arm ``(R, var0_L, warnings)`` — the ratio and its per-unit linearised variance."""
    if stats.mean_den == 0.0 or not math.isfinite(stats.mean_den):
        name = stats.name if stats.name is not None else fallback_name
        nan = float("nan")
        return (
            nan,
            nan,
            [
                f"ratio undefined for {name!r}: denominator mean is zero or non-finite; "
                "returning NaN (see statistics-changes.md H5)"
            ],
        )
    ratio = stats.mean_num / stats.mean_den
    # Non-negative in exact arithmetic (it is Σ((n_u−n̄) − R(d_u−d̄))²); clamp
    # tiny negatives from float cancellation of the three separately-rounded terms.
    quadratic = max(stats.m2_num - 2.0 * ratio * stats.c_nd + ratio**2 * stats.m2_den, 0.0)
    var_unit = quadratic / (stats.n * stats.mean_den**2)
    return ratio, var_unit, []


@register
class RatioDelta(BaseMethod):
    name = "ratio-delta"
    input_kind = "ratio"
    param_specs = (TEST_TYPE_PARAM,)

    def from_samples(self, sample_1: RatioSample, sample_2: RatioSample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, RatioSample)
        return self.from_suffstats(
            RatioSufficientStats.from_ratio_sample(sample_1),
            RatioSufficientStats.from_ratio_sample(sample_2),
        )

    def from_suffstats(
        self, stats_1: RatioSufficientStats, stats_2: RatioSufficientStats
    ) -> TestResult:
        require_pair_type(self.name, stats_1, stats_2, RatioSufficientStats)

        ratio_1, var_unit_1, warnings_1 = _arm_linearisation(stats_1, "group 1")
        ratio_2, var_unit_2, warnings_2 = _arm_linearisation(stats_2, "group 2")
        method_warnings = [*warnings_1, *warnings_2]
        var_ratio_1 = var_unit_1 / stats_1.n
        var_ratio_2 = var_unit_2 / stats_2.n

        if self.test_type == "absolute":
            estimate = absolute_effect(ratio_1, ratio_2, var_ratio_1, var_ratio_2)
        else:
            estimate = relative_delta_effect(
                mean_num=ratio_2 - ratio_1,
                var_num=var_ratio_1 + var_ratio_2,
                mean_den=ratio_1,
                var_den=var_ratio_1,
                covariance=-var_ratio_1,  # independent arms: num & denom share only R̂1
            )
        test = normal_test(estimate, self.alpha)

        return TestResult(
            name_1=stats_1.name,
            name_2=stats_2.name,
            value_1=ratio_1,
            value_2=ratio_2,
            std_1=math.sqrt(var_unit_1),  # per-unit linearised std
            std_2=math.sqrt(var_unit_2),
            size_1=stats_1.n,
            size_2=stats_2.n,
            method_name=self.name,
            method_params=self.identity_params,
            alpha=self.alpha,
            pvalue=test.pvalue,
            effect=test.effect,
            ci_length=test.ci_length,
            left_bound=test.left_bound,
            right_bound=test.right_bound,
            reject=test.reject,
            effect_distribution=test.distribution,
            warnings=[*method_warnings, *test.warnings],
        )
