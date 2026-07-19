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
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from abkit.stats.base import TEST_TYPE_PARAM, BaseMethod, require_pair_type, suffstats_columns
from abkit.stats.effects import (
    BatchEffectResult,
    EffectEstimate,
    FloatArray,
    normal_test,
    normal_test_array,
    relative_delta_effect,
    relative_delta_effect_array,
)
from abkit.stats.exceptions import SampleValidationError
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import PairedSufficientStats, Sample

#: Column keys of the batch entry — the joint (y1, y2) moments per comparison
#: row: pair count ``n``, per-arm means, raw centered second moments and the
#: raw cross co-moment ``c_y1y2 = Σ(y1−ȳ1)(y2−ȳ2)`` (``JointMoments.comoment``).
PAIRED_TTEST_ARRAY_KEYS = ("n", "mean_y1", "mean_y2", "m2_y1", "m2_y2", "c_y1y2")


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
    is_paired = True
    supports_vectorized = True
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

        return self._result_from_normal_test(
            test,
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
        )

    def from_suffstats_array(
        self,
        arrays_1: Mapping[str, FloatArray],
        arrays_2: Mapping[str, FloatArray] | None = None,
    ) -> BatchEffectResult:
        """Array-wise ``from_suffstats`` (M7 WP2) — ONE joint mapping, like the
        scalar paired signature (``arrays_2`` must stay None). Column keys:
        ``n``, ``mean_y1``, ``mean_y2``, ``m2_y1``, ``m2_y2``, ``c_y1y2``.

        The ``JointMoments`` linear-combination reads written out for the
        (y1, y2) weight vectors — the same float operation sequence the
        ``weights @ comoment @ weights`` chain performs, pinned by
        ``tests/stats/test_vectorized_parity.py``. An ``n < 2`` row under
        ``relative`` NaN-poisons (ddof=1 division by zero) instead of the
        scalar's ``SampleValidationError`` — per-row degeneracy is a gap,
        never a batch-wide exception.
        """
        if arrays_2 is not None:
            raise SampleValidationError(
                f"{self.name}: paired suffstats columns are joint by construction — pass one "
                "mapping as arrays_1 (with arrays_2=None), not one per arm"
            )
        n, mean_y1, mean_y2, m2_y1, m2_y2, c_y1y2 = suffstats_columns(
            arrays_1, PAIRED_TTEST_ARRAY_KEYS, self.name, "arrays_1"
        )

        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            difference_mean = mean_y2 - mean_y1
            # w·C·wᵀ for w = (−1, +1): (m2_y1 − c) + (m2_y2 − c) — the matmul's
            # own rounding sequence; then np.var parity (/n), then /n.
            difference_mean_var = (((m2_y1 - c_y1y2) + (m2_y2 - c_y1y2)) / n) / n

            if self.test_type == "absolute":
                effect: FloatArray = difference_mean
                var: FloatArray = difference_mean_var
            else:
                effect, var = relative_delta_effect_array(
                    mean_num=difference_mean,
                    var_num=difference_mean_var,
                    mean_den=mean_y1,
                    var_den=(m2_y1 / n) / n,
                    # np.cov parity (ddof=1): −cov(y2−y1, y1)/n — baseline fact #1;
                    # w_diff·C·w_y1ᵀ = c_y1y2 − m2_y1.
                    covariance=-((c_y1y2 - m2_y1) / (n - 1.0)) / n,
                )
        return normal_test_array(effect, var, self.alpha)
