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
from collections.abc import Mapping

import numpy as np

from abkit.stats.base import (
    CALCULATE_MDE_PARAM,
    COVARIATE_LOOKBACK_PARAM,
    POWER_PARAM,
    TEST_TYPE_PARAM,
    BaseMethod,
    require_pair_type,
    suffstats_pair_columns,
)
from abkit.stats.effects import (
    BatchEffectResult,
    FloatArray,
    _libm_pow,
    absolute_effect,
    absolute_effect_array,
    normal_test,
    normal_test_array,
    relative_delta_effect,
    relative_delta_effect_array,
)
from abkit.stats.exceptions import AbkitStatsWarning, SampleValidationError
from abkit.stats.power import get_cuped_ttest_mde
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Sample, SufficientStats

#: Column keys of the batch entry — the ``SufficientStats`` value + covariate moments.
CUPED_ARRAY_KEYS = ("n", "mean", "m2", "cov_mean", "cov_m2", "cross_c")

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
    supports_vectorized = True
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

        return self._result_from_normal_test(
            test,
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
            method_warnings=method_warnings,
            diagnostics={"theta": theta},
        )

    def from_suffstats_array(
        self,
        arrays_1: Mapping[str, FloatArray],
        arrays_2: Mapping[str, FloatArray] | None = None,
    ) -> BatchEffectResult:
        """Array-wise ``from_suffstats`` (M7 WP2). Column keys: ``n``, ``mean``,
        ``m2``, ``cov_mean``, ``cov_m2``, ``cross_c``.

        The pooled-θ mixed-ddof formula verbatim (np.cov-parity numerator over
        np.var-parity denominator, baseline fact #1) via numpy broadcasting;
        parity pinned by ``tests/stats/test_vectorized_parity.py``. A
        non-finite θ (zero pooled covariate variance) or ``n < 2`` row
        NaN-poisons through to NaN outputs — the batch mirror of the scalar
        warning + NaN path (the low-correlation advisory warning is scalar-only:
        validate never reads warnings).
        """
        (
            (n_1, mean_1, m2_1, cov_mean_1, cov_m2_1, cross_c_1),
            (n_2, mean_2, m2_2, cov_mean_2, cov_m2_2, cross_c_2),
        ) = suffstats_pair_columns(arrays_1, arrays_2, CUPED_ARRAY_KEYS, self.name)

        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            # SufficientStats.__init__ truncates n via int(n) — mirror it, or a
            # fractional-n row silently diverges (adversarial review round 2).
            n_1 = np.trunc(n_1)
            n_2 = np.trunc(n_2)
            # Pooled θ, EXACT mixed ddof: cov1_value_covariate = cross_c/(n−1)
            # (np.cov parity) summed over arms, over cov_var = cov_m2/n (np.var
            # parity) summed over arms — the scalar property op order preserved.
            theta_num = cross_c_1 / (n_1 - 1.0) + cross_c_2 / (n_2 - 1.0)
            theta_den = cov_m2_1 / n_1 + cov_m2_2 / n_2
            theta = theta_num / theta_den

            mean_cup_1 = mean_1 - theta * cov_mean_1
            mean_cup_2 = mean_2 - theta * cov_mean_2
            # var0(cup_i) exactly from raw co-moments: m2 − 2θ·cross + θ²·cov_m2,
            # over n; θ² via libm pow (bit parity with the scalar `**`).
            theta_sq = _libm_pow(theta, 2.0)
            var_cup_1 = (m2_1 - 2.0 * theta * cross_c_1 + theta_sq * cov_m2_1) / n_1
            var_cup_2 = (m2_2 - 2.0 * theta * cross_c_2 + theta_sq * cov_m2_2) / n_2

            if self.test_type == "absolute":
                effect, var = absolute_effect_array(
                    mean_cup_1, mean_cup_2, var_cup_1 / n_1, var_cup_2 / n_2
                )
            else:
                # np.cov parity (ddof=1): cov(cup_1, y1) = (m2 − θ·cross_c)/(n1 − 1).
                cov1_cup_value = (m2_1 - theta * cross_c_1) / (n_1 - 1.0)
                effect, var = relative_delta_effect_array(
                    mean_num=mean_cup_2 - mean_cup_1,
                    var_num=var_cup_2 / n_2 + var_cup_1 / n_1,
                    mean_den=mean_1,  # ORIGINAL control mean (baseline §3.3 subtlety)
                    var_den=(m2_1 / n_1) / n_1,
                    covariance=-cov1_cup_value / n_1,
                )
        return normal_test_array(effect, var, self.alpha)
