"""Golden tests: the bootstrap family vs the transcribed legacy math (rel 1e-9).

The RNG stream changed deliberately (H1/H2, docs/specs/statistics-changes.md), so
parity is proven by SHARING the stream — see ``legacy_reference_bootstrap`` —
with an equal seed on both sides. The abkit side runs with ``pvalue_kind="sign"``
for legacy p-value parity (docs/specs/statistics-baseline.md §4); CI bounds, the
sign p-value and ``ci_length`` must match at relative 1e-9 (quorum must-fix
"Golden tolerance = relative 1e-9"). Effects are asserted against their own
definition — the H9 real-data point estimate, computed independently in each test
— and the legacy paired ``mean(boot_data)`` effect is checked against the
``boot_mean`` diagnostic that preserves it.
"""

from __future__ import annotations

import numpy as np
import pytest
from legacy_reference_bootstrap import (
    legacy_bootstrap,
    legacy_paired_bootstrap,
    legacy_paired_poisson_bootstrap,
    legacy_paired_post_normed_bootstrap,
    legacy_poisson_bootstrap,
    legacy_post_normed_bootstrap,
)

from abkit.stats.factory import create_method
from abkit.stats.result import TestResult
from abkit.stats.samples import Sample

pytestmark = pytest.mark.golden

ALPHA = 0.05
REL = 1e-9


def _assert_boot_outputs_match(result: TestResult, reference: dict[str, float]) -> None:
    """CI bounds, sign p-value and ci_length at the golden tolerance (rel 1e-9)."""
    assert result.left_bound == pytest.approx(reference["left_bound"], rel=REL)
    assert result.right_bound == pytest.approx(reference["right_bound"], rel=REL)
    assert result.ci_length == pytest.approx(reference["ci_length"], rel=REL)
    assert result.pvalue == pytest.approx(reference["pvalue"], rel=REL)
    assert result.diagnostics["boot_mean"] == pytest.approx(
        reference["boot_mean"], rel=REL, abs=1e-12
    )


# --- fixtures (deterministic, revenue-like scales, n ~ 1000-3000) --------------------


def _independent_pair(
    seed: int = 11, n_1: int = 1500, n_2: int = 2500
) -> tuple[np.ndarray, np.ndarray]:
    generator = np.random.default_rng(seed)
    values_1 = generator.lognormal(0.0, 1.0, n_1)
    values_2 = generator.lognormal(0.0, 1.0, n_2) * 1.08
    return values_1, values_2


def _paired_pair(seed: int = 21, n: int = 2000) -> tuple[np.ndarray, np.ndarray]:
    generator = np.random.default_rng(seed)
    values_1 = generator.lognormal(0.0, 0.8, n) + 5.0
    values_2 = values_1 * 1.05 + generator.normal(0.0, 0.3, n)
    return values_1, values_2


def _covariate_pair(
    seed: int = 31, n_1: int = 1200, n_2: int = 1600
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    generator = np.random.default_rng(seed)
    cov_1 = generator.lognormal(0.0, 0.7, n_1) + 0.5
    cov_2 = generator.lognormal(0.0, 0.7, n_2) + 0.5
    values_1 = cov_1 * generator.lognormal(0.10, 0.4, n_1)
    values_2 = cov_2 * generator.lognormal(0.15, 0.4, n_2)
    return values_1, cov_1, values_2, cov_2


def _stratified_pair(
    seed: int = 41,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Equal 60/40 stratum mixes, n divisible by 10 — Hamilton apportionment equals
    the legacy int-truncated proportional counts, so both engines resample with
    identical per-stratum widths (and thus share the stream)."""
    generator = np.random.default_rng(seed)
    n_1, n_2 = 1000, 2000
    categories_1 = np.array(["a"] * 600 + ["b"] * 400)
    categories_2 = np.array(["a"] * 1200 + ["b"] * 800)
    generator.shuffle(categories_1)
    generator.shuffle(categories_2)
    values_1 = generator.normal(10.0, 2.0, n_1) + np.where(categories_1 == "b", 3.0, 0.0)
    values_2 = generator.normal(10.4, 2.0, n_2) + np.where(categories_2 == "b", 3.0, 0.0)
    return values_1, categories_1, values_2, categories_2


# --- bootstrap (unstratified) ----------------------------------------------------------


def test_bootstrap_mean_relative_golden() -> None:
    values_1, values_2 = _independent_pair()
    method = create_method(
        "bootstrap", alpha=ALPHA, params={"n_samples": 300, "seed": 424242, "pvalue_kind": "sign"}
    )
    result = method.compare_pair(
        Sample(values_1, name="control"), Sample(values_2, name="treatment")
    )
    reference = legacy_bootstrap(values_1, values_2, seed=424242, n_samples=300, alpha=ALPHA)
    _assert_boot_outputs_match(result, reference)
    # H9: effect is the real-data point estimate, matching its definition.
    expected_effect = float(np.mean(values_2)) / float(np.mean(values_1)) - 1.0
    assert result.effect == pytest.approx(expected_effect, rel=1e-12)
    assert result.effect == pytest.approx(reference["effect"], rel=REL)


def test_bootstrap_mean_absolute_golden() -> None:
    values_1, values_2 = _independent_pair(seed=12)
    method = create_method(
        "bootstrap",
        alpha=ALPHA,
        params={"n_samples": 500, "seed": 7, "test_type": "absolute", "pvalue_kind": "sign"},
    )
    result = method.compare_pair(
        Sample(values_1, name="control"), Sample(values_2, name="treatment")
    )
    reference = legacy_bootstrap(
        values_1, values_2, seed=7, n_samples=500, alpha=ALPHA, test_type="absolute"
    )
    _assert_boot_outputs_match(result, reference)
    expected_effect = float(np.mean(values_2)) - float(np.mean(values_1))
    assert result.effect == pytest.approx(expected_effect, rel=1e-12)
    assert result.effect == pytest.approx(reference["effect"], rel=REL)


def test_bootstrap_median_relative_golden() -> None:
    """stat='median' — the non-mean fast path; n_samples=257 exercises a 1-row tail quantum."""
    values_1, values_2 = _independent_pair(seed=13)
    method = create_method(
        "bootstrap",
        alpha=ALPHA,
        params={"n_samples": 257, "seed": 99, "stat": "median", "pvalue_kind": "sign"},
    )
    result = method.compare_pair(
        Sample(values_1, name="control"), Sample(values_2, name="treatment")
    )
    reference = legacy_bootstrap(
        values_1, values_2, seed=99, n_samples=257, alpha=ALPHA, stat_func=np.median
    )
    _assert_boot_outputs_match(result, reference)
    expected_effect = float(np.median(values_2)) / float(np.median(values_1)) - 1.0
    assert result.effect == pytest.approx(expected_effect, rel=1e-12)
    assert result.value_1 == float(np.median(values_1))
    assert result.value_2 == float(np.median(values_2))


# --- bootstrap (stratified: legacy weights == Hamilton by fixture design) --------------


@pytest.mark.parametrize("weight_method", ["min", "mean"])
def test_stratified_bootstrap_golden(weight_method: str) -> None:
    values_1, categories_1, values_2, categories_2 = _stratified_pair()
    method = create_method(
        "bootstrap",
        alpha=ALPHA,
        params={
            "n_samples": 300,
            "seed": 512,
            "stratify": True,
            "weight_method": weight_method,
            "pvalue_kind": "sign",
        },
    )
    result = method.compare_pair(
        Sample(values_1, categories_array=categories_1, name="control"),
        Sample(values_2, categories_array=categories_2, name="treatment"),
    )
    reference = legacy_bootstrap(
        values_1,
        values_2,
        seed=512,
        n_samples=300,
        alpha=ALPHA,
        categories_1=categories_1,
        categories_2=categories_2,
        weight_method=weight_method,
    )
    _assert_boot_outputs_match(result, reference)
    expected_effect = float(np.mean(values_2)) / float(np.mean(values_1)) - 1.0
    assert result.effect == pytest.approx(expected_effect, rel=1e-12)


# --- paired-bootstrap -------------------------------------------------------------------


def test_paired_bootstrap_relative_golden() -> None:
    values_1, values_2 = _paired_pair()
    method = create_method(
        "paired-bootstrap",
        alpha=ALPHA,
        params={"n_samples": 300, "seed": 2024, "pvalue_kind": "sign"},
    )
    result = method.compare_pair(
        Sample(values_1, name="control"), Sample(values_2, name="treatment")
    )
    reference = legacy_paired_bootstrap(values_1, values_2, seed=2024, n_samples=300, alpha=ALPHA)
    _assert_boot_outputs_match(result, reference)
    # H9 deviation: effect is the point estimate, NOT the legacy mean(boot_data)...
    expected_effect = float(np.mean(values_2)) / float(np.mean(values_1)) - 1.0
    assert result.effect == pytest.approx(expected_effect, rel=1e-12)
    # ...while the legacy effect survives as the boot_mean bias diagnostic.
    assert result.diagnostics["boot_mean"] == pytest.approx(reference["effect"], rel=REL)


# --- poisson-bootstrap / paired-poisson-bootstrap ---------------------------------------


def test_poisson_bootstrap_relative_golden() -> None:
    values_1, values_2 = _independent_pair(seed=14, n_1=1200, n_2=1800)
    method = create_method(
        "poisson-bootstrap",
        alpha=ALPHA,
        params={"n_samples": 300, "seed": 31337, "pvalue_kind": "sign"},
    )
    result = method.compare_pair(
        Sample(values_1, name="control"), Sample(values_2, name="treatment")
    )
    reference = legacy_poisson_bootstrap(values_1, values_2, seed=31337, n_samples=300, alpha=ALPHA)
    _assert_boot_outputs_match(result, reference)
    expected_effect = float(np.mean(values_2)) / float(np.mean(values_1)) - 1.0
    assert result.effect == pytest.approx(expected_effect, rel=1e-12)
    assert result.effect == pytest.approx(reference["effect"], rel=REL)


def test_paired_poisson_bootstrap_absolute_golden() -> None:
    """n_samples=384 (exactly three quanta) exercises the exact-boundary stream."""
    values_1, values_2 = _paired_pair(seed=22, n=1500)
    method = create_method(
        "paired-poisson-bootstrap",
        alpha=ALPHA,
        params={"n_samples": 384, "seed": 55, "test_type": "absolute", "pvalue_kind": "sign"},
    )
    result = method.compare_pair(
        Sample(values_1, name="control"), Sample(values_2, name="treatment")
    )
    reference = legacy_paired_poisson_bootstrap(
        values_1, values_2, seed=55, n_samples=384, alpha=ALPHA, test_type="absolute"
    )
    _assert_boot_outputs_match(result, reference)
    expected_effect = float(np.mean(values_2)) - float(np.mean(values_1))
    assert result.effect == pytest.approx(expected_effect, rel=1e-12)
    assert result.effect == pytest.approx(reference["effect"], rel=REL)


# --- post-normed-bootstrap (relative) ----------------------------------------------------


def test_post_normed_bootstrap_relative_golden() -> None:
    values_1, cov_1, values_2, cov_2 = _covariate_pair()
    method = create_method(
        "post-normed-bootstrap",
        alpha=ALPHA,
        params={"n_samples": 300, "seed": 616, "pvalue_kind": "sign"},
    )
    result = method.compare_pair(
        Sample(values_1, cov_array=cov_1, name="control"),
        Sample(values_2, cov_array=cov_2, name="treatment"),
    )
    reference = legacy_post_normed_bootstrap(
        values_1, cov_1, values_2, cov_2, seed=616, n_samples=300, alpha=ALPHA
    )
    _assert_boot_outputs_match(result, reference)
    # H9: the post-normed point estimate on the real data.
    expected_effect = (float(np.mean(values_2)) / float(np.mean(values_1))) / (
        float(np.mean(cov_2)) / float(np.mean(cov_1))
    ) - 1.0
    assert result.effect == pytest.approx(expected_effect, rel=1e-12)
    assert result.effect == pytest.approx(reference["effect"], rel=REL)


# --- paired-post-normed-bootstrap (absolute) ---------------------------------------------


def test_paired_post_normed_absolute_golden() -> None:
    values_1, values_2 = _paired_pair(seed=23, n=1800)
    generator = np.random.default_rng(24)
    cov_1 = generator.lognormal(0.0, 0.5, 1800) + 0.5  # required by validation, unused in math
    cov_2 = generator.lognormal(0.0, 0.5, 1800) + 0.5
    method = create_method(
        "paired-post-normed-bootstrap",
        alpha=ALPHA,
        params={"n_samples": 300, "seed": 909, "test_type": "absolute", "pvalue_kind": "sign"},
    )
    result = method.compare_pair(
        Sample(values_1, cov_array=cov_1, name="control"),
        Sample(values_2, cov_array=cov_2, name="treatment"),
    )
    reference = legacy_paired_post_normed_bootstrap(
        values_1, values_2, seed=909, n_samples=300, alpha=ALPHA
    )
    _assert_boot_outputs_match(result, reference)
    # Documented H9 exception: effect == mean(boot_data) (the z-difference mean, ~0);
    # abs tolerance because the estimand is centred at zero by construction.
    assert result.effect == pytest.approx(reference["effect"], rel=REL, abs=1e-12)
    assert result.effect == result.diagnostics["boot_mean"]
