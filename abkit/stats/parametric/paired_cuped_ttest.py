"""``paired-cuped-t-test`` — CUPED for paired arms (catalogue "PairedCupedTTest").

Legacy parity, preserved verbatim with the mixed-ddof convention (baseline fact
#1): θ is estimated on the per-pair DIFFERENCES — ``np.cov`` parity numerator
(ddof=1) over ``np.var`` parity denominator (ddof=0) — and exposed in
``diagnostics["theta"]`` for the golden test. As in the independent CUPED test,
the relative denominator is the ORIGINAL control mean and per-arm ``value``/
``std`` report the ORIGINAL moments (CUPED lives in the effect). The legacy
paired variants compute no MDE.

All CUPED quantities are exact linear-combination reads of the joint moments
(``w(cup_d) = w(y2−y1) − θ·w(x2−x1)``), so the raw entry reduces to ONE
:class:`PairedSufficientStats` and delegates — dual entry by construction.
"""

from __future__ import annotations

import math

import numpy as np

from abkit.stats.base import COVARIATE_LOOKBACK_PARAM, TEST_TYPE_PARAM
from abkit.stats.effects import EffectEstimate, normal_test, relative_delta_effect
from abkit.stats.exceptions import SampleValidationError
from abkit.stats.parametric.cuped_ttest import correlation_warning
from abkit.stats.parametric.paired_ttest import BasePairedMethod
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import PAIRED_CUPED_LABELS, JointMoments, PairedSufficientStats


def _corr(moments: JointMoments, value_label: str, cov_label: str) -> float:
    """``np.corrcoef`` parity (scale-free — ddof cancels) between two series."""
    i, j = moments.index(value_label), moments.index(cov_label)
    denominator = math.sqrt(moments.comoment[i, i] * moments.comoment[j, j])
    if denominator == 0.0:
        return float("nan")
    return float(moments.comoment[i, j] / denominator)


@register
class PairedCupedTTest(BasePairedMethod):
    name = "paired-cuped-t-test"
    param_specs = (TEST_TYPE_PARAM, COVARIATE_LOOKBACK_PARAM)

    def from_suffstats(self, stats_1: PairedSufficientStats, stats_2: None = None) -> TestResult:
        joint = self._as_joint(stats_1, stats_2)
        if not joint.has_covariate:
            raise SampleValidationError(
                f"{self.name}: paired CUPED requires covariates on both arms "
                f"(joint moments over {PAIRED_CUPED_LABELS})"
            )
        moments = joint.moments
        n = joint.n

        method_warnings: list[str] = []
        for fallback, arm_name, labels in (
            ("group 1", joint.name_1, ("y1", "x1")),
            ("group 2", joint.name_2, ("y2", "x2")),
        ):
            name = arm_name if arm_name is not None else fallback
            message = correlation_warning(self.name, name, _corr(moments, *labels))
            if message is not None:
                method_warnings.append(message)

        weights_diff_y = joint.weights(y2=1.0, y1=-1.0)
        weights_diff_x = joint.weights(x2=1.0, x1=-1.0)
        # θ on the per-pair DIFFERENCES with the EXACT mixed ddof (baseline fact #1):
        # np.cov-parity numerator (ddof=1) over np.var-parity denominator (ddof=0).
        theta_num = moments.linear_cov1(weights_diff_y, weights_diff_x)
        theta_den = moments.linear_var0(weights_diff_x)
        with np.errstate(divide="ignore", invalid="ignore"):
            theta = float(np.float64(theta_num) / np.float64(theta_den))
        if not math.isfinite(theta):
            method_warnings.append(
                f"{self.name}: theta is non-finite (zero covariate-difference variance); "
                "returning NaN test outputs"
            )

        weights_cup_diff = weights_diff_y - theta * weights_diff_x
        difference_mean = moments.linear_mean(weights_cup_diff)
        difference_mean_var = moments.linear_var0(weights_cup_diff) / n

        if self.test_type == "absolute":
            estimate = EffectEstimate(effect=difference_mean, var=difference_mean_var)
        else:
            weights_y1 = joint.weights(y1=1.0)
            estimate = relative_delta_effect(
                mean_num=difference_mean,
                var_num=difference_mean_var,
                mean_den=moments.linear_mean(weights_y1),  # ORIGINAL control mean
                var_den=moments.linear_var0(weights_y1) / n,
                # np.cov parity (ddof=1): −cov(cup_2 − cup_1, y1)/n — baseline fact #1.
                covariance=-moments.linear_cov1(weights_cup_diff, weights_y1) / n,
            )
        test = normal_test(estimate, self.alpha)

        index_y1 = moments.index("y1")
        index_y2 = moments.index("y2")
        return TestResult(
            name_1=joint.name_1,
            name_2=joint.name_2,
            value_1=float(moments.mean[index_y1]),
            value_2=float(moments.mean[index_y2]),
            std_1=math.sqrt(moments.var0(index_y1)),
            std_2=math.sqrt(moments.var0(index_y2)),
            size_1=n,
            size_2=n,
            cov_value_1=float(moments.mean[moments.index("x1")]),
            cov_value_2=float(moments.mean[moments.index("x2")]),
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
