"""``paired-t-test`` — paired/dependent comparison (baseline §4.5, catalogue "PairedTTest").

Normal approximation on the paired mean difference — legacy parity, preserved
verbatim including the mixed-ddof convention (baseline fact #1): variance terms
are ``np.var`` parity (ddof=0) while the relative-branch covariance term is
``np.cov`` parity (ddof=1). The legacy paired t-test computes no MDE.

Paired sufficient statistics are joint by construction: ONE
:class:`PairedSufficientStats` holds the joint moments of both aligned arms, so
the sufficient-statistics entry takes a single object. The raw entry takes two
position-aligned equal-size samples and reduces to it — ONE math path, so dual
entry holds by construction (the ``ttest.py`` exemplar pattern).
"""

from __future__ import annotations

import math
from abc import abstractmethod
from collections.abc import Sequence
from typing import Any

from abkit.stats.base import TEST_TYPE_PARAM, BaseMethod, require_pair_type
from abkit.stats.effects import EffectEstimate, normal_test, relative_delta_effect
from abkit.stats.exceptions import SampleValidationError
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import PairedSufficientStats, Sample


class BasePairedMethod(BaseMethod):
    """Shared input routing for paired designs (paired t-test, paired CUPED).

    ``compare_pair`` accepts either ``(Sample, Sample)`` — reduced through
    ``from_samples`` as usual — or a single :class:`PairedSufficientStats` as
    ``group_1`` with ``group_2=None`` (the joint moments already describe both
    arms, so there is no second group to pass).
    """

    def compare(self, groups: Sequence[Any]) -> list[TestResult]:
        """Paired suffstats entry: a sequence of PairedSufficientStats is a list of
        ready comparisons (each joint object IS one variant pair), so the generic
        pairwise combination step is skipped — the pipeline can drive paired
        methods through the same ``compare()`` call as every other method."""
        if groups and all(isinstance(group, PairedSufficientStats) for group in groups):
            return [self.from_suffstats(joint) for joint in groups]
        return super().compare(groups)

    def compare_pair(self, group_1: Any, group_2: Any | None = None) -> TestResult:
        if group_2 is None or isinstance(group_1, PairedSufficientStats):
            return self.from_suffstats(group_1, group_2)
        return super().compare_pair(group_1, group_2)

    def from_samples(self, sample_1: Sample, sample_2: Sample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, Sample)
        return self.from_suffstats(PairedSufficientStats.from_samples(sample_1, sample_2))

    @abstractmethod
    def from_suffstats(self, stats_1: Any, stats_2: Any | None = None) -> TestResult:
        """Paired signature: ONE joint PairedSufficientStats (``stats_2`` stays None)."""

    def _as_joint(self, stats_1: Any, stats_2: Any) -> PairedSufficientStats:
        """Validate the sufficient-statistics entry: one joint object, nothing else."""
        if stats_2 is not None:
            raise SampleValidationError(
                f"{self.name}: paired sufficient statistics are joint by construction — pass a "
                "single PairedSufficientStats as group_1 (with group_2=None), not one per arm"
            )
        if not isinstance(stats_1, PairedSufficientStats):
            raise SampleValidationError(
                f"{self.name}: sufficient-statistics entry requires a PairedSufficientStats, "
                f"got {type(stats_1).__name__}"
            )
        return stats_1


@register(aliases=("paired-ttest",))
class PairedTTest(BasePairedMethod):
    name = "paired-t-test"
    param_specs = (TEST_TYPE_PARAM,)

    def from_suffstats(self, stats_1: PairedSufficientStats, stats_2: None = None) -> TestResult:
        joint = self._as_joint(stats_1, stats_2)
        moments = joint.moments
        n = joint.n

        weights_y1 = joint.weights(y1=1.0)
        weights_diff = joint.weights(y2=1.0, y1=-1.0)
        mean_1 = moments.linear_mean(weights_y1)
        difference_mean = moments.linear_mean(weights_diff)
        # np.var parity (ddof=0): var of the per-pair differences over n (baseline §4.5).
        difference_mean_var = moments.linear_var0(weights_diff) / n

        if self.test_type == "absolute":
            estimate = EffectEstimate(effect=difference_mean, var=difference_mean_var)
        else:
            estimate = relative_delta_effect(
                mean_num=difference_mean,
                var_num=difference_mean_var,
                mean_den=mean_1,
                var_den=moments.linear_var0(weights_y1) / n,
                # np.cov parity (ddof=1): −cov(y2−y1, y1)/n — baseline fact #1.
                covariance=-moments.linear_cov1(weights_diff, weights_y1) / n,
            )
        test = normal_test(estimate, self.alpha)

        index_y1 = moments.index("y1")
        index_y2 = moments.index("y2")
        cov_value_1 = cov_value_2 = None
        if joint.has_covariate:
            cov_value_1 = float(moments.mean[moments.index("x1")])
            cov_value_2 = float(moments.mean[moments.index("x2")])

        return TestResult(
            name_1=joint.name_1,
            name_2=joint.name_2,
            value_1=float(moments.mean[index_y1]),
            value_2=float(moments.mean[index_y2]),
            std_1=math.sqrt(moments.var0(index_y1)),
            std_2=math.sqrt(moments.var0(index_y2)),
            size_1=n,
            size_2=n,
            cov_value_1=cov_value_1,
            cov_value_2=cov_value_2,
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
