"""Method-level bootstrap tests: identity/seed policy, quarantine, H5/H7/H9 conventions.

Engine mechanics (byte-stability, block invariance, Hamilton, applier, ci) are
covered in test_bootstrap_engine.py; golden parity vs the legacy transcription in
tests/golden/test_golden_bootstrap.py. This module pins the METHOD contracts.

``ALL_BOOTSTRAP_METHODS`` is registry-derived (docs/specs/m7-implementation-plan.md
WP1 A5; the plugin-registry invariant, CLAUDE.md "Methods are plugins") so a new
bootstrap plugin is auto-swept into the contract sweeps below; pair with
test_registry_completeness.py (A6), which catches a plugin module that is never
imported at all.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from abkit.stats import (
    Fraction,
    QuarantinedMethodError,
    Sample,
    SampleValidationError,
    create_method,
    get_method_class,
)
from abkit.stats.bootstrap import BaseBootstrapMethod
from abkit.stats.exceptions import MethodParamError
from abkit.stats.registry import available_methods

#: Registry-derived, not hand-maintained (M7 WP1 A5 — stats-core-review): every
#: registered method whose class is a BaseBootstrapMethod. A future bootstrap
#: plugin is auto-swept in here without touching this file; see
#: test_registry_completeness.py for the "forgotten import" half of the gate.
ALL_BOOTSTRAP_METHODS: tuple[str, ...] = tuple(
    name for name in available_methods() if issubclass(get_method_class(name), BaseBootstrapMethod)
)

#: Params that make every method constructible with its non-quarantined branch.
#: Kept as an explicit per-method mapping by design (a generic default could
#: silently paper over a quarantined branch); the assertion below is the
#: registry trip-wire — a new bootstrap method with no entry here fails loudly
#: at collection instead of being silently skipped by parametrize.
SAFE_PARAMS: dict[str, dict[str, object]] = {
    "bootstrap": {},
    "paired-bootstrap": {},
    "poisson-bootstrap": {},
    "paired-poisson-bootstrap": {},
    "post-normed-bootstrap": {"test_type": "relative"},
    "paired-post-normed-bootstrap": {"test_type": "absolute"},
}

assert set(SAFE_PARAMS) == set(ALL_BOOTSTRAP_METHODS), (
    "SAFE_PARAMS is out of sync with the registry (M7 WP1 A5): "
    f"missing entries for {set(ALL_BOOTSTRAP_METHODS) - set(SAFE_PARAMS)}, "
    f"stale entries for {set(SAFE_PARAMS) - set(ALL_BOOTSTRAP_METHODS)}. "
    "Add (or remove) a SAFE_PARAMS row for the new/removed bootstrap method above "
    "so the contract tests in this module cover it."
)


def _samples(seed: int = 3, n: int = 400, shift: float = 0.0) -> tuple[Sample, Sample]:
    rng = np.random.default_rng(seed)
    control = Sample(rng.normal(10.0, 2.0, n), cov_array=rng.normal(10.0, 2.0, n), name="control")
    treatment = Sample(
        rng.normal(10.0 + shift, 2.0, n), cov_array=rng.normal(10.0, 2.0, n), name="treatment"
    )
    return control, treatment


# --- suffstats path & identity ------------------------------------------------------


@pytest.mark.parametrize("name", ALL_BOOTSTRAP_METHODS)
def test_from_suffstats_raises_for_every_bootstrap_method(name: str) -> None:
    method = create_method(name, params=dict(SAFE_PARAMS[name], seed=1))
    with pytest.raises(SampleValidationError, match="per-unit samples"):
        method.from_suffstats(object(), object())


@pytest.mark.parametrize("name", ALL_BOOTSTRAP_METHODS)
def test_seed_and_max_block_bytes_excluded_from_identity(name: str) -> None:
    base = create_method(name, params=dict(SAFE_PARAMS[name]))
    seeded = create_method(name, params=dict(SAFE_PARAMS[name], seed=1))
    reseeded = create_method(name, params=dict(SAFE_PARAMS[name], seed=2, max_block_bytes=4096))
    assert base.method_config_id == seeded.method_config_id == reseeded.method_config_id
    assert "seed" not in seeded.identity_params
    assert "max_block_bytes" not in reseeded.identity_params


def test_pvalue_kind_is_identity_bearing_and_defaults_to_baseline_sign() -> None:
    default = create_method("bootstrap")
    plugin = create_method("bootstrap", params={"pvalue_kind": "plugin"})
    # Baseline-faithful default (statistics-changes.md §2/§6): sign p-value.
    assert default.params["pvalue_kind"] == "sign"
    assert default.method_config_id != plugin.method_config_id
    assert plugin.identity_params == {"pvalue_kind": "plugin"}


def test_numpy_integer_seed_accepted() -> None:
    method = create_method("bootstrap", params={"seed": np.int64(7)})
    assert method.params["seed"] == 7
    assert isinstance(method.params["seed"], int)


# --- quarantine (quorum must-fix) ---------------------------------------------------


def test_poisson_post_normed_is_quarantined_at_registry_level() -> None:
    with pytest.raises(QuarantinedMethodError, match="poisson-bootstrap.*ratio-delta"):
        get_method_class("poisson-post-normed-bootstrap")
    with pytest.raises(QuarantinedMethodError):
        get_method_class("poisson_post_normed_bootstrap")  # normalisation cannot bypass it


def test_post_normed_absolute_branch_is_quarantined() -> None:
    with pytest.raises(QuarantinedMethodError, match="ratio-delta"):
        create_method("post-normed-bootstrap", params={"test_type": "absolute"})


def test_paired_post_normed_relative_branch_is_quarantined() -> None:
    with pytest.raises(QuarantinedMethodError):
        create_method("paired-post-normed-bootstrap", params={"test_type": "relative"})


# --- H7: Poisson engine is mean-only -------------------------------------------------


@pytest.mark.parametrize("name", ["poisson-bootstrap", "paired-poisson-bootstrap"])
def test_poisson_rejects_non_mean_stat(name: str) -> None:
    with pytest.raises(MethodParamError, match="mean"):
        create_method(name, params={"stat": "median"})


# --- H9: effect is the real-data point estimate --------------------------------------


@pytest.mark.parametrize("name", ["bootstrap", "poisson-bootstrap"])
def test_effect_is_real_data_point_estimate(name: str) -> None:
    control, treatment = _samples()
    for test_type, expected in (
        ("absolute", treatment.mean - control.mean),
        ("relative", treatment.mean / control.mean - 1.0),
    ):
        method = create_method(name, params={"test_type": test_type, "n_samples": 50, "seed": 1})
        result = method.compare_pair(control, treatment)
        assert result.effect == pytest.approx(expected, rel=1e-12)
        assert "boot_mean" in result.diagnostics


def test_paired_bootstrap_effect_is_point_estimate_not_boot_mean() -> None:
    control, treatment = _samples(shift=0.5)
    method = create_method("paired-bootstrap", params={"n_samples": 200, "seed": 5})
    result = method.compare_pair(control, treatment)
    assert result.effect == pytest.approx(treatment.mean / control.mean - 1.0, rel=1e-12)
    # The bootstrap mean stays available as the bias diagnostic (H9).
    assert result.diagnostics["boot_mean"] == pytest.approx(result.effect, abs=0.05)


def test_paired_post_normed_absolute_keeps_boot_mean_effect_with_warning() -> None:
    control, treatment = _samples()
    method = create_method(
        "paired-post-normed-bootstrap",
        params={"test_type": "absolute", "n_samples": 100, "seed": 2},
    )
    result = method.compare_pair(control, treatment)
    assert result.effect == pytest.approx(result.diagnostics["boot_mean"], rel=1e-12)
    assert any("ratio-delta" in warning for warning in result.warnings)


def test_post_normed_effect_is_real_data_ratio_of_ratios() -> None:
    control, treatment = _samples()
    method = create_method("post-normed-bootstrap", params={"n_samples": 100, "seed": 4})
    result = method.compare_pair(control, treatment)
    assert control.cov_array is not None and treatment.cov_array is not None
    expected = (treatment.mean / control.mean) / (
        float(np.mean(treatment.cov_array)) / float(np.mean(control.cov_array))
    ) - 1.0
    assert result.effect == pytest.approx(expected, rel=1e-12)


# --- H5: zero denominators & non-finite replicates ------------------------------------


def test_relative_zero_control_yields_nan_and_warning() -> None:
    rng = np.random.default_rng(0)
    control = Sample(np.zeros(200), name="control")
    treatment = Sample(rng.normal(1.0, 0.1, 200), name="treatment")
    method = create_method("bootstrap", params={"n_samples": 50, "seed": 1})
    result = method.compare_pair(control, treatment)
    assert math.isnan(result.effect)
    assert math.isnan(result.pvalue) and math.isnan(result.left_bound)
    assert result.reject is False
    assert result.diagnostics["n_non_finite"] > 0
    assert any("H5" in warning for warning in result.warnings)


def test_sparse_control_non_finite_replicates_void_pvalue_and_ci() -> None:
    # ~99% zeros: some resamples of the control arm are all-zero => non-finite ratios.
    rng = np.random.default_rng(1)
    values = np.where(rng.uniform(size=60) < 0.99, 0.0, 1.0)
    control = Sample(values, name="control")
    treatment = Sample(rng.normal(1.0, 0.1, 60), name="treatment")
    method = create_method("bootstrap", params={"n_samples": 400, "seed": 3})
    result = method.compare_pair(control, treatment)
    if result.diagnostics.get("n_non_finite", 0.0) > 0:
        assert math.isnan(result.pvalue) and math.isnan(result.ci_length)
        assert result.reject is False
        assert any("non-finite" in warning for warning in result.warnings)


# --- validation ----------------------------------------------------------------------


def test_stratify_requires_categories_and_common_sets() -> None:
    rng = np.random.default_rng(2)
    plain_1 = Sample(rng.normal(size=100), name="a")
    plain_2 = Sample(rng.normal(size=100), name="b")
    method = create_method("bootstrap", params={"stratify": True, "n_samples": 20, "seed": 1})
    with pytest.raises(SampleValidationError, match="categories_array"):
        method.compare_pair(plain_1, plain_2)

    with_cats_1 = Sample(rng.normal(size=100), categories_array=["x"] * 50 + ["y"] * 50, name="a")
    with_cats_2 = Sample(rng.normal(size=100), categories_array=["x"] * 50 + ["z"] * 50, name="b")
    with pytest.raises(SampleValidationError, match="identical stratum category sets"):
        method.compare_pair(with_cats_1, with_cats_2)


def test_paired_methods_require_equal_sizes_and_identical_categories() -> None:
    rng = np.random.default_rng(3)
    short = Sample(rng.normal(size=50), name="a")
    long = Sample(rng.normal(size=60), name="b")
    for name in ("paired-bootstrap", "paired-poisson-bootstrap"):
        method = create_method(name, params={"n_samples": 20, "seed": 1})
        with pytest.raises(SampleValidationError, match="equal-size"):
            method.compare_pair(short, long)

    cats_1 = Sample(rng.normal(size=50), categories_array=["x"] * 25 + ["y"] * 25, name="a")
    cats_2 = Sample(rng.normal(size=50), categories_array=["y"] * 25 + ["x"] * 25, name="b")
    method = create_method("paired-bootstrap", params={"n_samples": 20, "seed": 1})
    with pytest.raises(SampleValidationError, match="elementwise-identical"):
        method.compare_pair(cats_1, cats_2)


def test_post_normed_requires_covariates() -> None:
    rng = np.random.default_rng(4)
    bare_1 = Sample(rng.normal(size=50), name="a")
    bare_2 = Sample(rng.normal(size=50), name="b")
    method = create_method("post-normed-bootstrap", params={"n_samples": 20, "seed": 1})
    with pytest.raises(SampleValidationError, match="cov_array"):
        method.compare_pair(bare_1, bare_2)


def test_bootstrap_rejects_fraction_inputs() -> None:
    method = create_method("bootstrap", params={"seed": 1})
    with pytest.raises(SampleValidationError):
        method.compare_pair(Fraction(10, 100, name="a"), Fraction(20, 100, name="b"))


def test_invalid_bootstrap_params() -> None:
    with pytest.raises(MethodParamError, match="n_samples"):
        create_method("bootstrap", params={"n_samples": 0})
    with pytest.raises(MethodParamError, match="max_block_bytes"):
        create_method("bootstrap", params={"max_block_bytes": 0})
    with pytest.raises(MethodParamError, match="pvalue_kind"):
        create_method("bootstrap", params={"pvalue_kind": "exact"})


# --- statistical sanity ---------------------------------------------------------------


def test_aa_pair_is_not_rejected_and_clear_effect_is() -> None:
    rng = np.random.default_rng(10)
    n = 800
    control = Sample(rng.normal(10.0, 2.0, n), name="control")
    aa_treatment = Sample(rng.normal(10.0, 2.0, n), name="treatment")
    method = create_method("bootstrap", params={"n_samples": 400, "seed": 7})
    assert method.compare_pair(control, aa_treatment).pvalue > 0.01

    shift = 5.0 * 2.0 / math.sqrt(n)  # 5 sigma of the mean difference
    ab_treatment = Sample(rng.normal(10.0 + shift, 2.0, n), name="treatment")
    result = method.compare_pair(control, ab_treatment)
    assert result.reject is True
    true_relative_effect = shift / 10.0
    assert result.left_bound < true_relative_effect < result.right_bound


@pytest.mark.aa
def test_bootstrap_aa_calibration_smoke() -> None:
    """~alpha rejection rate on A/A splits (coarse; the real matrix is abk validate, M4)."""
    rng = np.random.default_rng(42)
    iterations, n, rejections = 400, 500, 0
    population = rng.normal(50.0, 10.0, 20_000)
    for iteration in range(iterations):
        draw = rng.choice(population, size=2 * n, replace=False)
        method = create_method(
            "bootstrap", alpha=0.05, params={"n_samples": 200, "seed": iteration}
        )
        result = method.compare_pair(Sample(draw[:n], name="a"), Sample(draw[n:], name="b"))
        rejections += int(result.reject)
    fpr = rejections / iterations
    assert 0.01 <= fpr <= 0.10, f"A/A false-positive rate {fpr:.3f} out of [0.01, 0.10]"


def test_weight_method_without_stratify_rejected() -> None:
    with pytest.raises(MethodParamError, match="weight_method"):
        create_method("bootstrap", params={"weight_method": "mean"})


def test_poisson_methods_do_not_accept_weight_method() -> None:
    for name in ("poisson-bootstrap", "paired-poisson-bootstrap"):
        with pytest.raises(MethodParamError, match="weight_method"):
            create_method(name, params={"weight_method": "mean", "stratify": True})


def test_custom_registered_stat_end_to_end() -> None:
    from abkit.stats.bootstrap.applier import STAT_FUNCS, register_stat

    def p90(array: np.ndarray) -> float:
        return float(np.quantile(array, 0.9))

    register_stat("p90-test", p90)
    try:
        method = create_method("bootstrap", params={"stat": "p90-test", "n_samples": 50, "seed": 1})
        assert method.identity_params["stat"] == "p90-test"
        control, treatment = _samples()
        result = method.compare_pair(control, treatment)
        expected = p90(treatment.array) / p90(control.array) - 1.0
        assert result.effect == pytest.approx(expected, rel=1e-12)
        # Rebinding an existing name to a DIFFERENT function is refused (identity safety).
        with pytest.raises(MethodParamError, match="already registered"):
            register_stat("p90-test", lambda a: 0.0)
        # Unknown stat is rejected at construction.
        with pytest.raises(MethodParamError, match="unknown stat"):
            create_method("bootstrap", params={"stat": "p99"})
    finally:
        STAT_FUNCS.pop("p90-test", None)
