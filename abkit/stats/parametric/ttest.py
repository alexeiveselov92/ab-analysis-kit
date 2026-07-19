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

from collections.abc import Mapping

import numpy as np

from abkit.stats.base import (
    CALCULATE_MDE_PARAM,
    POWER_PARAM,
    TEST_TYPE_PARAM,
    BaseMethod,
    require_pair_type,
    suffstats_pair_columns,
)
from abkit.stats.effects import (
    BatchEffectResult,
    FloatArray,
    absolute_effect,
    absolute_effect_array,
    normal_test,
    normal_test_array,
    relative_delta_effect,
    relative_delta_effect_array,
)
from abkit.stats.power import get_ttest_mde
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Sample, SufficientStats

#: Column keys of the batch entry — the ``SufficientStats`` value moments.
TTEST_ARRAY_KEYS = ("n", "mean", "m2")


@register(aliases=("ttest",))
class TTest(BaseMethod):
    name = "t-test"
    param_specs = (TEST_TYPE_PARAM, CALCULATE_MDE_PARAM, POWER_PARAM)
    supports_vectorized = True

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

        return self._result_from_normal_test(
            test,
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
        )

    def from_suffstats_array(
        self,
        arrays_1: Mapping[str, FloatArray],
        arrays_2: Mapping[str, FloatArray] | None = None,
    ) -> BatchEffectResult:
        """Array-wise ``from_suffstats`` (M7 WP2). Column keys: ``n``, ``mean``, ``m2``.

        The same per-row formulas via numpy broadcasting (parity pinned by
        ``tests/stats/test_vectorized_parity.py``); degenerate rows → NaN.
        """
        (n_1, mean_1, m2_1), (n_2, mean_2, m2_2) = suffstats_pair_columns(
            arrays_1, arrays_2, TTEST_ARRAY_KEYS, self.name
        )

        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            # SufficientStats.__init__ truncates n via int(n) — mirror it, or a
            # fractional-n row silently diverges (adversarial review round 2).
            n_1 = np.trunc(n_1)
            n_2 = np.trunc(n_2)
            # Scalar op order preserved: var = m2/n (SufficientStats.var), then /n.
            var_mean_1 = (m2_1 / n_1) / n_1
            var_mean_2 = (m2_2 / n_2) / n_2

            if self.test_type == "absolute":
                effect, var = absolute_effect_array(mean_1, mean_2, var_mean_1, var_mean_2)
            else:
                effect, var = relative_delta_effect_array(
                    mean_num=mean_2 - mean_1,
                    var_num=var_mean_1 + var_mean_2,
                    mean_den=mean_1,
                    var_den=var_mean_1,
                    covariance=-var_mean_1,  # num & denom share mean_1 (baseline §3.1)
                )
        return normal_test_array(effect, var, self.alpha)
