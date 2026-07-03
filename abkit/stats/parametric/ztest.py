"""``z-test`` — two-proportion pooled z-test (baseline §3.2, catalogue "ZTest").

Reproduced verbatim, INCLUDING two documented legacy quirks (flagged in
docs/specs/statistics-changes.md §3 — never fixed silently):

- sign quirk: the z statistic uses ``prop_1 − prop_2`` while the reported effect
  uses ``prop_2 − prop_1``. The p-value is symmetric (``2·min(cdf, sf)``) so it
  is unaffected, but the orientations deliberately differ (legacy parity).
- relative branch: ``std_effect`` is naively divided by ``prop_1`` — there is NO
  delta-method covariance term (unlike the t-test family). A delta-consistent
  relative z-test is a possible v2 version bump if the A/A matrix shows
  under-coverage.

:class:`Fraction` inputs ARE the sufficient statistics (count/nobs), so
``from_samples`` simply delegates to ``from_suffstats`` — one math path.
Hygiene H5: a zero control proportion under ``relative`` (and a degenerate
pooled proportion of 0 or 1) yields NaN outputs plus a recorded warning, never
an exception.
"""

from __future__ import annotations

import math

import scipy.stats as sps

from abkit.stats.base import (
    CALCULATE_MDE_PARAM,
    POWER_PARAM,
    TEST_TYPE_PARAM,
    BaseMethod,
    require_pair_type,
)
from abkit.stats.power import get_fraction_mde
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Fraction


@register(aliases=("ztest",))
class ZTest(BaseMethod):
    name = "z-test"
    input_kind = "fraction"
    param_specs = (TEST_TYPE_PARAM, CALCULATE_MDE_PARAM, POWER_PARAM)

    def from_samples(self, sample_1: Fraction, sample_2: Fraction) -> TestResult:
        return self.from_suffstats(sample_1, sample_2)

    def from_suffstats(self, stats_1: Fraction, stats_2: Fraction) -> TestResult:
        require_pair_type(self.name, stats_1, stats_2, Fraction)
        result_warnings: list[str] = []
        nan = float("nan")

        prop_1, prop_2 = stats_1.prop, stats_2.prop
        nobs_1, nobs_2 = stats_1.nobs, stats_2.nobs
        prop_combined = (stats_1.count + stats_2.count) / (nobs_1 + nobs_2)
        pooled_var = prop_combined * (1.0 - prop_combined) * (1.0 / nobs_1 + 1.0 / nobs_2)
        std_effect = math.sqrt(pooled_var)

        if std_effect > 0.0 and math.isfinite(std_effect):
            # Legacy sign quirk kept verbatim: z uses prop_1 − prop_2, effect prop_2 − prop_1.
            z_stat = (prop_1 - prop_2) / std_effect
            pvalue = float(2.0 * min(sps.norm.cdf(z_stat), sps.norm.sf(z_stat)))
        else:
            result_warnings.append(
                "pooled proportion variance is zero (pooled proportion is 0 or 1); "
                "returning NaN test outputs"
            )
            pvalue = nan

        effect = prop_2 - prop_1
        if self.test_type == "relative":
            if prop_1 == 0.0 or not math.isfinite(prop_1):
                result_warnings.append(
                    "relative effect undefined: control proportion is zero or non-finite; "
                    "returning NaN (see statistics-changes.md H5)"
                )
                effect = std_effect = pvalue = nan
            else:
                effect /= prop_1
                std_effect /= prop_1

        if math.isfinite(effect) and math.isfinite(std_effect) and std_effect > 0.0:
            distribution = sps.norm(effect, std_effect)
            quantiles = sps.norm.ppf([self.alpha / 2.0, 1.0 - self.alpha / 2.0])
            left_bound = float(quantiles[0] * std_effect + effect)
            right_bound = float(quantiles[1] * std_effect + effect)
            ci_length = right_bound - left_bound
        else:
            distribution = None
            left_bound = right_bound = ci_length = nan

        mde_1 = mde_2 = None
        if self.params["calculate_mde"]:
            mde_1 = get_fraction_mde(
                prop_1,
                stats_1.sample_size,
                test_type=self.test_type,
                alpha=self.alpha,
                power=self.params["power"],
                ratio=nobs_2 / nobs_1,
            )
            mde_2 = get_fraction_mde(
                prop_2,
                stats_2.sample_size,
                test_type=self.test_type,
                alpha=self.alpha,
                power=self.params["power"],
                ratio=nobs_1 / nobs_2,
            )

        return TestResult(
            name_1=stats_1.name,
            name_2=stats_2.name,
            value_1=prop_1,
            value_2=prop_2,
            std_1=stats_1.std,
            std_2=stats_2.std,
            size_1=stats_1.sample_size,
            size_2=stats_2.sample_size,
            mde_1=mde_1,
            mde_2=mde_2,
            method_name=self.name,
            method_params=self.identity_params,
            alpha=self.alpha,
            pvalue=pvalue,
            effect=effect,
            ci_length=ci_length,
            left_bound=left_bound,
            right_bound=right_bound,
            reject=bool(pvalue < self.alpha),
            effect_distribution=distribution,
            warnings=result_warnings,
        )
