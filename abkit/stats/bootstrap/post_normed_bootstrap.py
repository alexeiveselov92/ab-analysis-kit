"""``post-normed-bootstrap`` — covariate-ratio post-normalised bootstrap.

Baseline §4.3 (catalogue "PostNormedBootstrapTest"): ratio / post-normalised
metric — divides out the covariate ratio for variance reduction on ratio
metrics. Requires ``cov_array`` on both arms and resamples value and covariate
with the SAME indices (``cov_bootstrap``, baseline §4.1), so the per-unit
(Y, X) pairing is preserved.

Relative branch (the sane one, reproduced verbatim):
``boot_data = (S2/S1) / (S2_cov/S1_cov) − 1`` per replicate.

QUARANTINE (quorum must-fix; docs/specs/statistics-changes.md §3): the legacy
ABSOLUTE branch ``S2 − (S2_cov/S1_cov)·S1`` is an unusual estimand and is NOT
reproduced — ``test_type="absolute"`` raises
:class:`~abkit.stats.exceptions.QuarantinedMethodError` at construction; use
the principled ``ratio-delta`` instead.

Baseline fact #3: the bootstrap seed is excluded from ``method_params`` — here
via the identity-excluded ``SEED_PARAM`` shared by all bootstrap methods.
"""

from __future__ import annotations

import math

import numpy as np

from abkit.stats.base import require_pair_type
from abkit.stats.bootstrap.applier import stat_point
from abkit.stats.bootstrap.bootstrap import BaseBootstrapMethod
from abkit.stats.bootstrap.engine import bootstrap_statistics
from abkit.stats.exceptions import QuarantinedMethodError
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Sample


@register
class PostNormedBootstrapTest(BaseBootstrapMethod):
    """Covariate-ratio post-normalised bootstrap (relative branch only)."""

    name = "post-normed-bootstrap"
    requires_covariate = True

    def _validate_params(self) -> None:
        super()._validate_params()
        if str(self.params["test_type"]) == "absolute":
            raise QuarantinedMethodError(
                f"{self.name}: test_type='absolute' is quarantined — the legacy absolute "
                "branch (S2 - (S2_cov/S1_cov)*S1) is an unusual estimand (see "
                "statistics-changes.md §3); use test_type='relative' or the principled "
                "'ratio-delta' method"
            )

    def from_samples(self, sample_1: Sample, sample_2: Sample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, Sample)
        self._require_covariates(sample_1, sample_2)
        assert sample_1.cov_array is not None and sample_2.cov_array is not None
        plan_1, plan_2 = self._independent_plans(sample_1, sample_2)
        rng = self._make_rng()
        # Variant 1 fully before variant 2; value and covariate share indices.
        boot_1, cov_boot_1 = bootstrap_statistics(
            rng,
            (sample_1.array, sample_1.cov_array),
            plan_1,
            self._n_samples,
            self._stat,
            self._max_block_bytes,
        )
        boot_2, cov_boot_2 = bootstrap_statistics(
            rng,
            (sample_2.array, sample_2.cov_array),
            plan_2,
            self._n_samples,
            self._stat,
            self._max_block_bytes,
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            boot_data = (boot_2 / boot_1) / (cov_boot_2 / cov_boot_1) - 1.0

        result_warnings: list[str] = []
        effect = self._post_normed_effect(sample_1, sample_2, result_warnings)
        return self._finalize(sample_1, sample_2, boot_data, effect, result_warnings)

    def _post_normed_effect(
        self, sample_1: Sample, sample_2: Sample, result_warnings: list[str]
    ) -> float:
        """Real-data point estimate ``(stat2/stat1)/(stat2c/stat1c) − 1`` (H9), H5-guarded."""
        assert sample_1.cov_array is not None and sample_2.cov_array is not None
        value_1 = np.float64(stat_point(sample_1.array, self._stat))
        value_2 = np.float64(stat_point(sample_2.array, self._stat))
        cov_value_1 = np.float64(stat_point(sample_1.cov_array, self._stat))
        cov_value_2 = np.float64(stat_point(sample_2.cov_array, self._stat))
        with np.errstate(divide="ignore", invalid="ignore"):
            effect = float((value_2 / value_1) / (cov_value_2 / cov_value_1) - 1.0)
        if not math.isfinite(effect):
            result_warnings.append(
                "post-normed relative effect undefined: a denominator statistic is zero "
                "or non-finite; returning NaN (see statistics-changes.md H5)"
            )
            return float("nan")
        return effect
