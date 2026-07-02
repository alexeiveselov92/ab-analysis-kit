"""``poisson-bootstrap`` / ``paired-poisson-bootstrap`` — weight-based bootstrap.

Baseline §4.4 (catalogue "PoissonBootstrapTest"): the streaming-friendly engine
— Poisson(1) weights instead of index resampling, so the whole bootstrap is a
single matmul per quantum and no ``n_samples × n`` value matrix is ever built.

H7 (docs/specs/statistics-changes.md): the Poisson weighted-mean replicate is
only a valid bootstrap of the MEAN — ``stat`` must be ``"mean"``
(:class:`~abkit.stats.exceptions.MethodParamError` at construction otherwise).

Post-stratification scales each unit's weight by 1 / count-of-its-category
(catalogue), per variant. The paired variant applies ONE weights stream to both
arms (catalogue "PairedPoissonBootstrapTest").
"""

from __future__ import annotations

from abkit.stats.base import require_pair_type
from abkit.stats.bootstrap.applier import stat_point
from abkit.stats.bootstrap.bootstrap import BOOTSTRAP_PARAM_SPECS, BaseBootstrapMethod
from abkit.stats.bootstrap.engine import (
    poisson_bootstrap_means,
    poisson_unit_scale,
    require_common_categories,
)
from abkit.stats.exceptions import MethodParamError
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Sample

#: Poisson stratification uses the 1/count unit scale, never ``weight_method`` —
#: accepting it would let a no-op value fork ``method_config_id`` (review finding).
POISSON_PARAM_SPECS = tuple(spec for spec in BOOTSTRAP_PARAM_SPECS if spec.name != "weight_method")


@register
class PoissonBootstrapTest(BaseBootstrapMethod):
    """Independent Poisson-weighted bootstrap of the mean (baseline §4.4)."""

    name = "poisson-bootstrap"
    param_specs = POISSON_PARAM_SPECS

    def _validate_params(self) -> None:
        super()._validate_params()
        if str(self.params["stat"]) != "mean":
            raise MethodParamError(
                f"{self.name}: the Poisson engine is only correct for stat='mean' "
                "(weighted-mean replicates; see statistics-changes.md H7), got "
                f"{self.params['stat']!r}"
            )

    def from_samples(self, sample_1: Sample, sample_2: Sample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, Sample)
        scale_1 = scale_2 = None
        if bool(self.params["stratify"]):
            require_common_categories(self.name, sample_1, sample_2)
            assert sample_1.categories_array is not None and sample_2.categories_array is not None
            scale_1 = poisson_unit_scale(sample_1.categories_array)
            scale_2 = poisson_unit_scale(sample_2.categories_array)
        rng = self._make_rng()
        # Variant 1 is drawn fully (all quanta) before variant 2 (draw-order contract).
        (boot_1,) = poisson_bootstrap_means(
            rng, (sample_1.array,), scale_1, self._n_samples, self._max_block_bytes
        )
        (boot_2,) = poisson_bootstrap_means(
            rng, (sample_2.array,), scale_2, self._n_samples, self._max_block_bytes
        )
        boot_data = self._boot_effect(boot_1, boot_2)
        result_warnings: list[str] = []
        effect = self._point_effect(
            stat_point(sample_1.array, self._stat),
            stat_point(sample_2.array, self._stat),
            result_warnings,
        )
        return self._finalize(sample_1, sample_2, boot_data, effect, result_warnings)


@register
class PairedPoissonBootstrapTest(PoissonBootstrapTest):
    """Paired Poisson bootstrap: ONE weights stream applied to BOTH arms (catalogue).

    Requires equal sample sizes; with ``stratify=True`` both arms must carry
    elementwise-identical ``categories_array``, so one post-stratification
    scale vector serves both arms.
    """

    name = "paired-poisson-bootstrap"

    def from_samples(self, sample_1: Sample, sample_2: Sample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, Sample)
        self._validate_paired(sample_1, sample_2)
        scale = None
        if bool(self.params["stratify"]):
            assert sample_1.categories_array is not None
            scale = poisson_unit_scale(sample_1.categories_array)
        rng = self._make_rng()
        boot_1, boot_2 = poisson_bootstrap_means(
            rng,
            (sample_1.array, sample_2.array),
            scale,
            self._n_samples,
            self._max_block_bytes,
        )
        boot_data = self._boot_effect(boot_1, boot_2)
        result_warnings: list[str] = []
        effect = self._point_effect(
            stat_point(sample_1.array, self._stat),
            stat_point(sample_2.array, self._stat),
            result_warnings,
        )
        return self._finalize(sample_1, sample_2, boot_data, effect, result_warnings)
