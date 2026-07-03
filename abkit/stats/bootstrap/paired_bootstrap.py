"""``paired-bootstrap`` — index-resampling bootstrap for paired designs.

Legacy semantics (catalogue "PairedBootstrapTest"): both arms are resampled with
the SAME index draws — here made explicit by drawing ONE set of index quanta
from the single Generator and applying it to both arms (the legacy achieved
this implicitly by seeding two generators with the same ``random_seed``).

Deliberate deviation (H9, docs/specs/statistics-changes.md): the legacy set
``effect = mean(boot_data)``; here ``effect`` is the real-data point estimate —
consistent with every other method — and the bootstrap mean stays in
``diagnostics["boot_mean"]`` as a bias diagnostic.
"""

from __future__ import annotations

from abkit.stats.base import require_pair_type
from abkit.stats.bootstrap.applier import stat_point
from abkit.stats.bootstrap.bootstrap import BaseBootstrapMethod
from abkit.stats.bootstrap.engine import bootstrap_statistics
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Sample


@register
class PairedBootstrapTest(BaseBootstrapMethod):
    """Paired bootstrap: equal-size aligned arms sharing one index stream.

    Requires equal sample sizes; when both arms carry ``categories_array`` they
    must be elementwise identical (paired strata travel with the pair), so a
    single stratified plan serves both arms.
    """

    name = "paired-bootstrap"
    is_paired = True

    def from_samples(self, sample_1: Sample, sample_2: Sample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, Sample)
        self._validate_paired(sample_1, sample_2)
        plan = self._paired_plan(sample_1, sample_2)
        rng = self._make_rng()
        boot_1, boot_2 = bootstrap_statistics(
            rng,
            (sample_1.array, sample_2.array),
            plan,
            self._n_samples,
            self._stat,
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
