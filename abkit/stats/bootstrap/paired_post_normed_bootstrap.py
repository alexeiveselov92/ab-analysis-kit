"""``paired-post-normed-bootstrap`` — z-score standardised paired bootstrap.

Despite the name, the legacy method (catalogue "PairedPostNormedBootstrapTest")
does NOT do covariate post-norming: it z-score STANDARDISES each arm's
bootstrap distribution, ``boot_i_normed = (boot_i − mean(boot_i)) /
std(boot_i)``, then differences them. That absolute branch is reproduced
verbatim here over paired index draws (ONE stream applied to both arms).

QUARANTINE (quorum must-fix; docs/specs/statistics-changes.md §3): the legacy
RELATIVE branch takes a ratio of z-scores — the denominator is centred at ~0
and the ratio explodes — so ``test_type="relative"`` (the family default)
raises :class:`~abkit.stats.exceptions.QuarantinedMethodError` at construction.

H9 exception (documented): the standardised-difference estimand has NO
real-data point estimate, so ``effect = mean(boot_data)`` is kept — the only
method where ``effect`` is not a real-data point estimate — and every result
carries a warning recommending ``post-normed-bootstrap`` or ``ratio-delta``.

Legacy quirk (validation parity): ``cov_array`` is REQUIRED on both arms even
though the math never uses it.
"""

from __future__ import annotations

import numpy as np

from abkit.stats.base import require_pair_type
from abkit.stats.bootstrap.bootstrap import BaseBootstrapMethod
from abkit.stats.bootstrap.engine import bootstrap_statistics
from abkit.stats.exceptions import QuarantinedMethodError
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Sample


@register
class PairedPostNormedBootstrapTest(BaseBootstrapMethod):
    """Paired z-score-standardised bootstrap (absolute branch only)."""

    name = "paired-post-normed-bootstrap"

    def _validate_params(self) -> None:
        super()._validate_params()
        if str(self.params["test_type"]) == "relative":
            raise QuarantinedMethodError(
                f"{self.name}: test_type='relative' is quarantined — the legacy relative "
                "branch is a ratio of z-score-standardised bootstrap distributions "
                "(denominator ~0, the ratio explodes; see statistics-changes.md §3); use "
                "test_type='absolute', or 'post-normed-bootstrap' / 'ratio-delta' for "
                "ratio metrics"
            )

    def from_samples(self, sample_1: Sample, sample_2: Sample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, Sample)
        self._require_covariates(sample_1, sample_2)  # legacy quirk: required, unused
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
        with np.errstate(divide="ignore", invalid="ignore"):
            boot_1_normed = (boot_1 - boot_1.mean()) / boot_1.std()  # ddof=0 (np.std law)
            boot_2_normed = (boot_2 - boot_2.mean()) / boot_2.std()
        boot_data = boot_2_normed - boot_1_normed

        effect = float(np.mean(boot_data))
        result_warnings = [
            f"{self.name} standardises each arm's bootstrap distribution (z-scores); its "
            "effect is the mean standardised difference with NO real-data point estimate "
            "(documented H9 exception) — prefer 'post-normed-bootstrap' or 'ratio-delta' "
            "for ratio metrics"
        ]
        return self._finalize(sample_1, sample_2, boot_data, effect, result_warnings)
