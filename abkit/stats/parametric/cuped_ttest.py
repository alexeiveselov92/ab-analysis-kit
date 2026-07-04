"""``cuped-t-test`` — CUPED variance reduction, independent arms (baseline §3.3).

THE mixed-ddof method (baseline fact #1, quorum must-fix): the pooled θ
numerator is ``np.cov`` parity (ddof=1) while its denominator and every variance
term are ``np.var`` parity (ddof=0). The convention is encoded per term — never
normalised to one ddof — and θ itself is exposed in ``diagnostics["theta"]`` for
the golden test.

Baseline subtleties preserved verbatim:

- θ is pooled across both arms and estimated on the same data used for the test
  (baseline fact #2 — no cross-fitting; a cross-fitted variant is v2);
- the relative denominator is the ORIGINAL control mean (not CUPED-adjusted),
  while the numerator uses the CUPED-adjusted means;
- per-arm ``value``/``std`` report the ORIGINAL mean/std (the dashboard shows
  raw values; CUPED lives in the effect).

CUPED moments are derived exactly from the raw co-moments, so ``from_samples``
reduces to :class:`SufficientStats` and delegates — ONE math path (dual entry by
construction, the ``ttest.py`` exemplar pattern).
"""

from __future__ import annotations

import math
import warnings

import numpy as np

from abkit.stats.base import (
    CALCULATE_MDE_PARAM,
    COVARIATE_LOOKBACK_PARAM,
    POWER_PARAM,
    TEST_TYPE_PARAM,
    BaseMethod,
    require_pair_type,
)
from abkit.stats.effects import absolute_effect, normal_test, relative_delta_effect
from abkit.stats.exceptions import AbkitStatsWarning, SampleValidationError
from abkit.stats.power import get_cuped_ttest_mde
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Sample, SufficientStats

#: Legacy CUPED guard: warn when the value↔covariate correlation is below this.
MIN_CORR_COEF = 0.5


def correlation_warning(method_name: str, arm_name: str, corr_coef: float) -> str | None:
    """Emit and return the legacy low-correlation warning, or ``None`` when adequate.

    A NaN correlation (degenerate covariate) intentionally does not trigger —
    the non-finite-θ warning covers that case.
    """
    if not corr_coef < MIN_CORR_COEF:  # NaN-safe: NaN comparisons are False
        return None
    message = (
        f"{method_name}: covariate correlation for {arm_name!r} is {corr_coef:.4f} < "
        f"{MIN_CORR_COEF} — CUPED variance reduction will be small (legacy guard)"
    )
    warnings.warn(message, AbkitStatsWarning, stacklevel=3)
    return message


@register(aliases=("cuped-ttest",))
class CupedTTest(BaseMethod):
    name = "cuped-t-test"
    requires_covariate = True
    param_specs = (
        TEST_TYPE_PARAM,
        CALCULATE_MDE_PARAM,
        POWER_PARAM,
        COVARIATE_LOOKBACK_PARAM,
    )

    def from_samples(self, sample_1: Sample, sample_2: Sample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, Sample)
        return self.from_suffstats(
            SufficientStats.from_sample(sample_1), SufficientStats.from_sample(sample_2)
        )

    def from_suffstats(self, stats_1: SufficientStats, stats_2: SufficientStats) -> TestResult:
        require_pair_type(self.name, stats_1, stats_2, SufficientStats)
        for stats in (stats_1, stats_2):
            if not stats.has_covariate:
                raise SampleValidationError(
                    f"{self.name}: CUPED requires covariate moments (cov_array) on both groups"
                )
        assert stats_1.cov_mean is not None and stats_1.cov_m2 is not None
        assert stats_1.cross_c is not None
        assert stats_2.cov_mean is not None and stats_2.cov_m2 is not None
        assert stats_2.cross_c is not None

        method_warnings: list[str] = []
        for fallback, stats in (("group 1", stats_1), ("group 2", stats_2)):
            arm_name = stats.name if stats.name is not None else fallback
            message = correlation_warning(self.name, arm_name, stats.corr_coef)
            if message is not None:
                method_warnings.append(message)

        # Pooled θ with the EXACT mixed ddof (baseline fact #1): np.cov-parity
        # numerator (ddof=1) over np.var-parity denominator (ddof=0).
        theta_num = stats_1.cov1_value_covariate + stats_2.cov1_value_covariate
        theta_den = stats_1.cov_var + stats_2.cov_var
        with np.errstate(divide="ignore", invalid="ignore"):
            theta = float(np.float64(theta_num) / np.float64(theta_den))
        if not math.isfinite(theta):
            method_warnings.append(
                f"{self.name}: theta is non-finite (zero pooled covariate variance); "
                "returning NaN test outputs"
            )

        n_1, n_2 = stats_1.n, stats_2.n
        mean_cup_1 = stats_1.mean - theta * stats_1.cov_mean
        mean_cup_2 = stats_2.mean - theta * stats_2.cov_mean
        # var0(cup_i) exactly from raw co-moments: Σ(y−θx − mean)² = m2 − 2θ·cross + θ²·cov_m2.
        var_cup_1 = (stats_1.m2 - 2.0 * theta * stats_1.cross_c + theta**2 * stats_1.cov_m2) / n_1
        var_cup_2 = (stats_2.m2 - 2.0 * theta * stats_2.cross_c + theta**2 * stats_2.cov_m2) / n_2

        if self.test_type == "absolute":
            estimate = absolute_effect(mean_cup_1, mean_cup_2, var_cup_1 / n_1, var_cup_2 / n_2)
        else:
            # np.cov parity (ddof=1): cov(cup_1, y1) = (m2 − θ·cross_c)/(n1 − 1);
            # n1 ≥ 2 is guaranteed — cov1_value_covariate above raised otherwise.
            cov1_cup_value = (stats_1.m2 - theta * stats_1.cross_c) / (n_1 - 1)
            estimate = relative_delta_effect(
                mean_num=mean_cup_2 - mean_cup_1,
                var_num=var_cup_2 / n_2 + var_cup_1 / n_1,
                mean_den=stats_1.mean,  # ORIGINAL control mean (baseline §3.3 subtlety)
                var_den=stats_1.var / n_1,
                covariance=-cov1_cup_value / n_1,
            )
        test = normal_test(estimate, self.alpha)

        mde_1 = mde_2 = None
        if self.params["calculate_mde"]:
            mde_1 = get_cuped_ttest_mde(
                stats_1.mean,
                stats_1.std,
                stats_1.corr_coef,
                n_1,
                test_type=self.test_type,
                alpha=self.alpha,
                power=self.params["power"],
                ratio=n_2 / n_1,
            )
            mde_2 = get_cuped_ttest_mde(
                stats_2.mean,
                stats_2.std,
                stats_2.corr_coef,
                n_2,
                test_type=self.test_type,
                alpha=self.alpha,
                power=self.params["power"],
                ratio=n_1 / n_2,
            )

        return TestResult(
            name_1=stats_1.name,
            name_2=stats_2.name,
            value_1=stats_1.mean,
            value_2=stats_2.mean,
            std_1=stats_1.std,
            std_2=stats_2.std,
            size_1=n_1,
            size_2=n_2,
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
            warnings=[*method_warnings, *test.warnings],
            diagnostics={"theta": theta},
        )
