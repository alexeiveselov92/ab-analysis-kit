"""``bootstrap`` — plain IID / stratified index-resampling bootstrap (baseline §4.2).

This module also hosts :class:`BaseBootstrapMethod`, the shared machinery of the
whole bootstrap family: the common parameter schema, the per-call RNG (H1/H2),
stratified resample plans (H6), boot/point effect computation (H5/H9) and the
common result assembly (percentile CI, H4 p-value, ``boot_mean`` diagnostic).
"""

from __future__ import annotations

import math

import numpy as np
import scipy.stats as sps

from abkit.stats.base import (
    MAX_BLOCK_BYTES_PARAM,
    N_SAMPLES_PARAM,
    SEED_PARAM,
    STAT_PARAM,
    STRATIFY_PARAM,
    TEST_TYPE_PARAM,
    WEIGHT_METHOD_PARAM,
    BaseMethod,
    require_pair_type,
)
from abkit.stats.bootstrap.applier import stat_point
from abkit.stats.bootstrap.ci import PVALUE_KIND_PARAM, bootstrap_pvalue, percentile_ci
from abkit.stats.bootstrap.engine import (
    ResamplePlan,
    bootstrap_statistics,
    pooled_stratum_shares,
    stratified_plan,
    unstratified_plan,
)
from abkit.stats.exceptions import MethodParamError, SampleValidationError
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.rng import make_rng
from abkit.stats.samples import FloatArray, Sample

#: The shared bootstrap-family schema — identical for every bootstrap method.
BOOTSTRAP_PARAM_SPECS = (
    TEST_TYPE_PARAM,
    N_SAMPLES_PARAM,
    STRATIFY_PARAM,
    WEIGHT_METHOD_PARAM,
    STAT_PARAM,
    SEED_PARAM,
    MAX_BLOCK_BYTES_PARAM,
    PVALUE_KIND_PARAM,
)


class BaseBootstrapMethod(BaseMethod):
    """Shared bootstrap machinery — abstract, never registered directly.

    Randomness policy (H1/H2): all randomness flows from ONE
    ``np.random.Generator`` built via :func:`abkit.stats.rng.make_rng` from
    ``params["seed"]`` per compare call. Same seed ⇒ byte-identical
    :class:`TestResult`; ``seed=None`` ⇒ nondeterministic. ``seed`` and
    ``max_block_bytes`` are identity-EXCLUDED (docs/specs/declarative-config.md
    §7; baseline fact #3 generalised to all bootstrap methods).

    Result conventions (docs/specs/statistics-changes.md): ``effect`` is the
    point estimate on the REAL data (H9); the bootstrap distribution only sets
    the CI and p-value; ``diagnostics["boot_mean"]`` carries the bootstrap mean
    as a bias diagnostic; non-finite replicates are kept but void the p-value
    and CI (H5). No MDE in M1 (``mde_i=None``).
    """

    param_specs = BOOTSTRAP_PARAM_SPECS

    def _validate_params(self) -> None:
        if int(self.params["n_samples"]) < 1:
            raise MethodParamError(
                f"{self.name}: n_samples must be >= 1, got {self.params['n_samples']}"
            )
        max_block_bytes = self.params["max_block_bytes"]
        if max_block_bytes is not None and int(max_block_bytes) < 1:
            raise MethodParamError(
                f"{self.name}: max_block_bytes must be >= 1, got {max_block_bytes}"
            )

    def from_suffstats(self, stats_1: object, stats_2: object) -> TestResult:
        raise SampleValidationError(
            f"{self.name}: bootstrap methods require per-unit samples; "
            "use the closed-form methods for the suffstats path"
        )

    # --- shared accessors -----------------------------------------------------
    @property
    def _stat(self) -> str:
        return str(self.params["stat"])

    @property
    def _n_samples(self) -> int:
        return int(self.params["n_samples"])

    @property
    def _max_block_bytes(self) -> int | None:
        value = self.params["max_block_bytes"]
        return None if value is None else int(value)

    def _make_rng(self) -> np.random.Generator:
        """The ONE Generator per compare call (H1) — no global numpy state."""
        return make_rng(self.params["seed"])

    # --- shared validation & planning ----------------------------------------
    def _validate_paired(self, sample_1: Sample, sample_2: Sample) -> None:
        """Paired-design input contract: equal sizes; strata travel with the pair."""
        if sample_1.sample_size != sample_2.sample_size:
            raise SampleValidationError(
                f"{self.name}: paired samples must be equal-size and aligned by pair: "
                f"{sample_1.sample_size} != {sample_2.sample_size}"
            )
        if sample_1.categories_array is not None and sample_2.categories_array is not None:
            if not np.array_equal(sample_1.categories_array, sample_2.categories_array):
                raise SampleValidationError(
                    f"{self.name}: paired samples must carry elementwise-identical "
                    "categories_array (paired strata travel with the pair)"
                )
        if bool(self.params["stratify"]) and (
            sample_1.categories_array is None or sample_2.categories_array is None
        ):
            raise SampleValidationError(
                f"{self.name}: stratify=True requires categories_array on both samples"
            )

    def _require_covariates(self, sample_1: Sample, sample_2: Sample) -> None:
        for label, sample in (("first", sample_1), ("second", sample_2)):
            if sample.cov_array is None:
                raise SampleValidationError(
                    f"{self.name}: requires cov_array on the {label} sample "
                    "(post-normalisation covariate)"
                )

    def _independent_plans(
        self, sample_1: Sample, sample_2: Sample
    ) -> tuple[ResamplePlan, ResamplePlan]:
        """Per-variant plans; stratified plans share pooled category shares (H6)."""
        if not bool(self.params["stratify"]):
            return (
                unstratified_plan(sample_1.sample_size),
                unstratified_plan(sample_2.sample_size),
            )
        categories, shares = pooled_stratum_shares(
            sample_1, sample_2, str(self.params["weight_method"]), self.name
        )
        assert sample_1.categories_array is not None and sample_2.categories_array is not None
        return (
            stratified_plan(sample_1.categories_array, categories, shares),
            stratified_plan(sample_2.categories_array, categories, shares),
        )

    def _paired_plan(self, sample_1: Sample, sample_2: Sample) -> ResamplePlan:
        """ONE plan for both arms (paired draws; categories elementwise-identical)."""
        if not bool(self.params["stratify"]):
            return unstratified_plan(sample_1.sample_size)
        categories, shares = pooled_stratum_shares(
            sample_1, sample_2, str(self.params["weight_method"]), self.name
        )
        assert sample_1.categories_array is not None
        return stratified_plan(sample_1.categories_array, categories, shares)

    # --- shared effect computation ---------------------------------------------
    def _boot_effect(self, boot_1: FloatArray, boot_2: FloatArray) -> FloatArray:
        """``boot_2 − boot_1`` (absolute) or ``boot_2/boot_1 − 1`` (relative), §4.2.

        The relative division is evaluated under ``np.errstate`` suppression;
        non-finite replicates are KEPT (baseline numbers preserved) and voided
        in :meth:`_finalize` (H5).
        """
        if self.test_type == "absolute":
            return boot_2 - boot_1
        with np.errstate(divide="ignore", invalid="ignore"):
            return boot_2 / boot_1 - 1.0

    def _point_effect(self, value_1: float, value_2: float, result_warnings: list[str]) -> float:
        """Real-data point effect (H9): ``v2 − v1`` or ``v2/v1 − 1`` with an H5 guard."""
        if self.test_type == "absolute":
            return value_2 - value_1
        with np.errstate(divide="ignore", invalid="ignore"):
            effect = float(np.float64(value_2) / np.float64(value_1) - 1.0)
        if not math.isfinite(effect):
            result_warnings.append(
                "relative effect undefined: control (denominator) statistic is zero or "
                "non-finite; returning NaN (see statistics-changes.md H5)"
            )
            return float("nan")
        return effect

    # --- shared result assembly -------------------------------------------------
    def _finalize(
        self,
        sample_1: Sample,
        sample_2: Sample,
        boot_data: FloatArray,
        effect: float,
        result_warnings: list[str],
    ) -> TestResult:
        """Common bootstrap result computation (baseline §4; H4/H5/H9).

        Percentile CI and the configured p-value from ``boot_data``; if it
        contains non-finite replicates they are kept but p-value/CI become NaN,
        ``reject`` False, with an explanatory warning and an ``n_non_finite``
        diagnostic (H5). ``effect_distribution = norm(mean(boot), std(boot))``
        (catalogue parity; omitted when degenerate/non-finite).
        """
        stat = self._stat
        diagnostics: dict[str, float] = {}
        with np.errstate(invalid="ignore"):  # boot_data may carry kept non-finite entries (H5)
            boot_mean = float(np.mean(boot_data))
            boot_std = float(np.std(boot_data))  # ddof=0 (mixed-ddof law: np.std terms)
        diagnostics["boot_mean"] = boot_mean

        nan = float("nan")
        n_non_finite = int(np.count_nonzero(~np.isfinite(boot_data)))
        if n_non_finite:
            diagnostics["n_non_finite"] = float(n_non_finite)
            result_warnings.append(
                f"bootstrap effect distribution contains {n_non_finite} non-finite "
                "replicate(s) (division by a ~zero control statistic); p-value and CI "
                "are undefined (NaN) — see statistics-changes.md H5"
            )
            pvalue = left_bound = right_bound = ci_length = nan
            reject = False
        else:
            left_bound, right_bound = percentile_ci(boot_data, self.alpha)
            ci_length = right_bound - left_bound
            pvalue = bootstrap_pvalue(boot_data, str(self.params["pvalue_kind"]))
            reject = bool(pvalue < self.alpha)

        distribution = None
        if math.isfinite(boot_mean) and math.isfinite(boot_std) and boot_std > 0.0:
            distribution = sps.norm(boot_mean, boot_std)

        return TestResult(
            name_1=sample_1.name,
            name_2=sample_2.name,
            value_1=stat_point(sample_1.array, stat),
            value_2=stat_point(sample_2.array, stat),
            std_1=sample_1.std,
            std_2=sample_2.std,
            size_1=sample_1.sample_size,
            size_2=sample_2.sample_size,
            cov_value_1=(
                None if sample_1.cov_array is None else stat_point(sample_1.cov_array, stat)
            ),
            cov_value_2=(
                None if sample_2.cov_array is None else stat_point(sample_2.cov_array, stat)
            ),
            mde_1=None,
            mde_2=None,
            method_name=self.name,
            method_params=self.identity_params,
            alpha=self.alpha,
            pvalue=pvalue,
            effect=effect,
            ci_length=ci_length,
            left_bound=left_bound,
            right_bound=right_bound,
            reject=reject,
            effect_distribution=distribution,
            warnings=result_warnings,
            diagnostics=diagnostics,
        )


@register(aliases=("bootstrap-test",))
class BootstrapTest(BaseBootstrapMethod):
    """Plain IID (or stratified) bootstrap of ``stat`` (baseline §4.2).

    Independent index resampling per variant; variant 1 is drawn fully (all
    quanta) before variant 2 (draw-order contract in ``engine.py``). Stratified
    mode aligns stratum weights across variants via ``weight_method`` so both
    variants resample to a common stratum mix (poststratification by design).
    """

    name = "bootstrap"

    def from_samples(self, sample_1: Sample, sample_2: Sample) -> TestResult:
        require_pair_type(self.name, sample_1, sample_2, Sample)
        plan_1, plan_2 = self._independent_plans(sample_1, sample_2)
        rng = self._make_rng()
        (boot_1,) = bootstrap_statistics(
            rng, (sample_1.array,), plan_1, self._n_samples, self._stat, self._max_block_bytes
        )
        (boot_2,) = bootstrap_statistics(
            rng, (sample_2.array,), plan_2, self._n_samples, self._stat, self._max_block_bytes
        )
        boot_data = self._boot_effect(boot_1, boot_2)
        result_warnings: list[str] = []
        effect = self._point_effect(
            stat_point(sample_1.array, self._stat),
            stat_point(sample_2.array, self._stat),
            result_warnings,
        )
        return self._finalize(sample_1, sample_2, boot_data, effect, result_warnings)
