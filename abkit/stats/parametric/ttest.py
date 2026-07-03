"""``t-test`` — independent two-sample comparison (baseline §3.1).

Despite the name, the statistic uses the NORMAL distribution on the mean
difference (a large-sample Welch-style z-test) — legacy parity, preserved
verbatim. Relative effects use the delta-method linearisation with the negative
covariance term ``−var_mean_1`` (numerator and denominator share ``mean_1``).

This class is the exemplar for every closed-form method: ``from_samples`` reduces
raw arrays to :class:`SufficientStats` and delegates to ``from_suffstats`` — ONE
math path, so the dual-entry equivalence holds by construction.
"""

from __future__ import annotations

from abkit.stats.base import (
    CALCULATE_MDE_PARAM,
    POWER_PARAM,
    TEST_TYPE_PARAM,
    BaseMethod,
    require_pair_type,
)
from abkit.stats.effects import absolute_effect, normal_test, relative_delta_effect
from abkit.stats.power import get_ttest_mde
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Sample, SufficientStats


@register(aliases=("ttest",))
class TTest(BaseMethod):
    name = "t-test"
    param_specs = (TEST_TYPE_PARAM, CALCULATE_MDE_PARAM, POWER_PARAM)

    def from_samples(self, sample_1: Sample, sample_2: Sample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, Sample)
        return self.from_suffstats(
            SufficientStats.from_sample(sample_1), SufficientStats.from_sample(sample_2)
        )

    def from_suffstats(self, stats_1: SufficientStats, stats_2: SufficientStats) -> TestResult:
        require_pair_type(self.name, stats_1, stats_2, SufficientStats)

        var_mean_1 = stats_1.var / stats_1.n
        var_mean_2 = stats_2.var / stats_2.n
        difference_mean = stats_2.mean - stats_1.mean
        difference_mean_var = var_mean_1 + var_mean_2

        if self.test_type == "absolute":
            estimate = absolute_effect(stats_1.mean, stats_2.mean, var_mean_1, var_mean_2)
        else:
            estimate = relative_delta_effect(
                mean_num=difference_mean,
                var_num=difference_mean_var,
                mean_den=stats_1.mean,
                var_den=var_mean_1,
                covariance=-var_mean_1,  # num & denom share mean_1 (baseline §3.1)
            )
        test = normal_test(estimate, self.alpha)

        mde_1 = mde_2 = None
        if self.params["calculate_mde"]:
            mde_1 = get_ttest_mde(
                stats_1.mean,
                stats_1.std,
                stats_1.n,
                test_type=self.test_type,
                alpha=self.alpha,
                power=self.params["power"],
                ratio=stats_2.n / stats_1.n,
            )
            mde_2 = get_ttest_mde(
                stats_2.mean,
                stats_2.std,
                stats_2.n,
                test_type=self.test_type,
                alpha=self.alpha,
                power=self.params["power"],
                ratio=stats_1.n / stats_2.n,
            )

        return TestResult(
            name_1=stats_1.name,
            name_2=stats_2.name,
            value_1=stats_1.mean,
            value_2=stats_2.mean,
            std_1=stats_1.std,
            std_2=stats_2.std,
            size_1=stats_1.n,
            size_2=stats_2.n,
            cov_value_1=stats_1.cov_mean,
            cov_value_2=stats_2.cov_mean,
            mde_1=mde_1,
            mde_2=mde_2,
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
            warnings=test.warnings,
        )
