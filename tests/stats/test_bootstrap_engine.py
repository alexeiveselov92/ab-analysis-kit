"""Engine-level bootstrap tests: determinism, block invariance, apportionment, CI.

Covers the quorum must-fixes on the engine surface (docs/specs/quorum-review.md):
H2 byte-stability (same seed → byte-identical results for EVERY bootstrap method),
H10 block-stream invariance (``max_block_bytes`` never changes the random stream),
H6 Hamilton apportionment, H3 applier fast paths, and H4 p-value plug-in behaviour
(docs/specs/statistics-changes.md §2).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from abkit.stats.bootstrap.applier import apply_stat, stat_point
from abkit.stats.bootstrap.ci import (
    bootstrap_pvalue,
    percentile_ci,
    pvalue_plugin,
    pvalue_sign,
)
from abkit.stats.bootstrap.engine import (
    BLOCK_QUANTUM,
    draw_stratum_indices,
    hamilton_apportion,
    iter_resample_blocks,
    poisson_unit_scale,
    pooled_stratum_shares,
    unstratified_plan,
)
from abkit.stats.exceptions import MethodParamError, SampleValidationError
from abkit.stats.factory import create_method
from abkit.stats.samples import Sample

#: Every bootstrap method with the params needed to construct it (the
#: paired-post-normed relative default branch is quarantined — see methods tests).
BOOTSTRAP_METHODS: list[tuple[str, dict[str, Any]]] = [
    ("bootstrap", {}),
    ("paired-bootstrap", {}),
    ("poisson-bootstrap", {}),
    ("paired-poisson-bootstrap", {}),
    ("post-normed-bootstrap", {}),
    ("paired-post-normed-bootstrap", {"test_type": "absolute"}),
]


def _sample_pair() -> tuple[Sample, Sample]:
    """Equal-size samples with covariates — valid input for EVERY bootstrap method."""
    generator = np.random.default_rng(987)
    cov_1 = generator.lognormal(0.0, 0.5, 500) + 0.5
    cov_2 = generator.lognormal(0.0, 0.5, 500) + 0.5
    values_1 = cov_1 * generator.lognormal(0.0, 0.4, 500)
    values_2 = cov_2 * generator.lognormal(0.1, 0.4, 500)
    return (
        Sample(values_1, cov_array=cov_1, name="control"),
        Sample(values_2, cov_array=cov_2, name="treatment"),
    )


# --- byte-stability (quorum must-fix H2) -------------------------------------------------


@pytest.mark.parametrize(("method_name", "extra"), BOOTSTRAP_METHODS)
def test_same_seed_two_runs_byte_identical(method_name: str, extra: dict[str, Any]) -> None:
    sample_1, sample_2 = _sample_pair()
    params = {"n_samples": 300, "seed": 777, **extra}
    result_a = create_method(method_name, params=dict(params)).compare_pair(sample_1, sample_2)
    result_b = create_method(method_name, params=dict(params)).compare_pair(sample_1, sample_2)
    assert result_a.to_dict() == result_b.to_dict()


@pytest.mark.parametrize(("method_name", "extra"), BOOTSTRAP_METHODS)
def test_different_seeds_differ(method_name: str, extra: dict[str, Any]) -> None:
    sample_1, sample_2 = _sample_pair()
    result_a = create_method(
        method_name, params={"n_samples": 300, "seed": 777, **extra}
    ).compare_pair(sample_1, sample_2)
    result_b = create_method(
        method_name, params={"n_samples": 300, "seed": 778, **extra}
    ).compare_pair(sample_1, sample_2)
    assert result_a.left_bound != result_b.left_bound
    assert result_a.to_dict() != result_b.to_dict()
    # ...but the method identity is the same (seed is identity-excluded, H2).
    assert (
        create_method(method_name, params={"seed": 777, **extra}).method_config_id
        == create_method(method_name, params={"seed": 778, **extra}).method_config_id
    )


# --- block invariance (H10: the cap must never affect the stream) ------------------------


@pytest.mark.parametrize(("method_name", "extra"), BOOTSTRAP_METHODS)
def test_max_block_bytes_never_changes_results(method_name: str, extra: dict[str, Any]) -> None:
    """A 1-byte cap forces one-quantum blocks; results must be byte-identical to no cap."""
    sample_1, sample_2 = _sample_pair()
    capped = create_method(
        method_name, params={"n_samples": 300, "seed": 42, "max_block_bytes": 1, **extra}
    ).compare_pair(sample_1, sample_2)
    uncapped = create_method(
        method_name, params={"n_samples": 300, "seed": 42, **extra}
    ).compare_pair(sample_1, sample_2)
    assert capped.to_dict() == uncapped.to_dict()


def test_iter_resample_blocks_quantum_and_stream_invariance() -> None:
    """Blocks under a tiny cap are single quanta; concatenated they equal the uncapped run."""
    values = np.random.default_rng(3).normal(size=50)
    plan = unstratified_plan(50)
    capped = [
        blocks[0]
        for blocks in iter_resample_blocks(np.random.default_rng(9), (values,), plan, 300, 1)
    ]
    uncapped = [
        blocks[0]
        for blocks in iter_resample_blocks(np.random.default_rng(9), (values,), plan, 300, None)
    ]
    assert [block.shape[0] for block in capped] == [BLOCK_QUANTUM, BLOCK_QUANTUM, 44]
    assert [block.shape[0] for block in uncapped] == [300]
    assert np.array_equal(np.vstack(capped), np.vstack(uncapped))


# --- draw-order contract: the shared draw helper -----------------------------------------


def test_draw_stratum_indices_deterministic_and_transparent() -> None:
    """The helper is exactly one ``rng.integers`` call — the golden reference relies on it."""
    first = draw_stratum_indices(np.random.default_rng(5), 100, 7, 20)
    second = draw_stratum_indices(np.random.default_rng(5), 100, 7, 20)
    direct = np.random.default_rng(5).integers(0, 100, size=(7, 20))
    assert np.array_equal(first, second)
    assert np.array_equal(first, direct)
    assert first.shape == (7, 20)
    assert first.min() >= 0 and first.max() < 100


# --- hamilton_apportion (H6) --------------------------------------------------------------


def test_hamilton_thirds_sum_exactly() -> None:
    counts = hamilton_apportion(np.array([1.0, 1.0, 1.0]), 100)
    assert counts.sum() == 100
    assert counts.tolist() == [34, 33, 33]  # equal remainders break toward earlier strata


def test_hamilton_known_answer_exact_quotas() -> None:
    assert hamilton_apportion(np.array([0.5, 0.3, 0.2]), 10).tolist() == [5, 3, 2]


def test_hamilton_largest_remainder_wins() -> None:
    # quotas 4.95 / 4.05 → floors 4/4, the missing unit goes to the larger remainder
    assert hamilton_apportion(np.array([0.55, 0.45]), 9).tolist() == [5, 4]


def test_hamilton_zero_floor_bump_keeps_total() -> None:
    counts = hamilton_apportion(np.array([0.999, 0.001]), 10)
    assert counts.tolist() == [9, 1]  # tiny stratum bumped to 1, donor pays
    assert counts.sum() == 10


@pytest.mark.parametrize("total", [17, 100, 1003])
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_hamilton_sums_exactly_on_adversarial_shares(total: int, seed: int) -> None:
    generator = np.random.default_rng(seed)
    shares = generator.dirichlet(np.full(7, 0.3))  # spiky shares → many fractional quotas
    counts = hamilton_apportion(shares, total)
    assert counts.sum() == total
    assert counts.min() >= 1


def test_hamilton_invalid_inputs_raise() -> None:
    with pytest.raises(SampleValidationError, match="at least one unit"):
        hamilton_apportion(np.array([0.5, 0.3, 0.2]), 2)  # total < strata
    with pytest.raises(SampleValidationError, match="finite and non-negative"):
        hamilton_apportion(np.array([0.5, -0.1]), 10)
    with pytest.raises(SampleValidationError, match="finite and non-negative"):
        hamilton_apportion(np.array([0.5, float("nan")]), 10)
    with pytest.raises(SampleValidationError, match="positive"):
        hamilton_apportion(np.array([0.0, 0.0]), 10)


# --- applier (H3 fast paths) ----------------------------------------------------------------


def test_apply_stat_mean_fast_path_matches_apply_along_axis() -> None:
    matrix = np.random.default_rng(1).normal(size=(50, 33))
    np.testing.assert_allclose(
        apply_stat(matrix, "mean"), np.apply_along_axis(np.mean, 1, matrix), rtol=1e-15
    )


def test_apply_stat_median_fast_path_matches_apply_along_axis() -> None:
    matrix = np.random.default_rng(2).normal(size=(50, 33))
    assert np.array_equal(apply_stat(matrix, "median"), np.apply_along_axis(np.median, 1, matrix))


def test_apply_stat_callable_fallback() -> None:
    matrix = np.random.default_rng(3).normal(size=(20, 15))

    def upper_quartile(row: np.ndarray) -> float:
        return float(np.percentile(row, 75))

    assert np.array_equal(
        apply_stat(matrix, upper_quartile), np.apply_along_axis(upper_quartile, 1, matrix)
    )


def test_apply_stat_unknown_name_raises() -> None:
    with pytest.raises(MethodParamError, match="unknown stat"):
        apply_stat(np.ones((2, 2)), "mode")


def test_stat_point_known_answers_and_unknown_raises() -> None:
    values = np.array([1.0, 2.0, 3.0, 10.0])
    assert stat_point(values, "mean") == 4.0
    assert stat_point(values, "median") == 2.5
    assert stat_point(values, lambda a: float(np.max(a))) == 10.0
    with pytest.raises(MethodParamError, match="unknown stat"):
        stat_point(values, "mode")


# --- ci helpers (baseline §4 parity + H4) ----------------------------------------------------


def test_pvalue_plugin_never_zero_on_one_sided_boot() -> None:
    boot = np.linspace(0.5, 2.0, 99)  # all positive
    assert pvalue_plugin(boot) == 2.0 / 100.0  # 2 * (0 + 1) / (n + 1)
    assert pvalue_plugin(-boot) == 2.0 / 100.0


def test_pvalue_plugin_counts_ties_on_both_sides() -> None:
    # zeros enter both the >=0 and <=0 counts (documented tie convention)
    assert pvalue_plugin(np.array([0.0, 1.0, 2.0, 3.0])) == 2.0 * 2.0 / 5.0
    assert pvalue_plugin(np.array([-1.0, 0.0, 1.0])) == 1.0  # 2*3/4 capped


def test_pvalue_plugin_capped_at_one() -> None:
    assert pvalue_plugin(np.array([-1.0, 1.0])) == 1.0  # 2*2/3 capped


def test_pvalue_sign_can_return_zero_baseline_parity() -> None:
    assert pvalue_sign(np.linspace(0.5, 2.0, 99)) == 0.0


def test_pvalue_sign_known_answers_ties_uncounted() -> None:
    assert pvalue_sign(np.array([-1.0, 1.0, 1.0, 1.0])) == 0.5
    assert pvalue_sign(np.array([-1.0, -2.0, 1.0, 1.0])) == 1.0
    # legacy convention: a tie at exactly 0 counts on neither side
    assert pvalue_sign(np.array([-1.0, 0.0, 1.0, 1.0])) == 0.5


def test_percentile_ci_equals_np_quantile() -> None:
    boot = np.random.default_rng(8).normal(size=501)
    left, right = percentile_ci(boot, 0.05)
    expected_left, expected_right = np.quantile(boot, [0.025, 0.975])
    assert left == float(expected_left)
    assert right == float(expected_right)


def test_bootstrap_pvalue_dispatch_and_unknown_kind() -> None:
    boot = np.array([-1.0, 1.0, 2.0])
    assert bootstrap_pvalue(boot, "sign") == pvalue_sign(boot)
    assert bootstrap_pvalue(boot, "plugin") == pvalue_plugin(boot)
    with pytest.raises(MethodParamError, match="pvalue_kind"):
        bootstrap_pvalue(boot, "smoothed")


# --- stratification helpers -------------------------------------------------------------------


def test_poisson_unit_scale_known_answer() -> None:
    scale = poisson_unit_scale(np.array(["a", "a", "b"]))
    assert scale.tolist() == [0.5, 0.5, 1.0]


def test_pooled_stratum_shares_min_and_mean() -> None:
    sample_1 = Sample(np.arange(10.0), categories_array=np.array(["a"] * 6 + ["b"] * 4))
    sample_2 = Sample(np.arange(10.0), categories_array=np.array(["a"] * 5 + ["b"] * 5))
    categories, min_shares = pooled_stratum_shares(sample_1, sample_2, "min", "bootstrap")
    assert categories.tolist() == ["a", "b"]
    np.testing.assert_allclose(min_shares, np.array([0.5, 0.4]) / 0.9, rtol=1e-15)
    _, mean_shares = pooled_stratum_shares(sample_1, sample_2, "mean", "bootstrap")
    np.testing.assert_allclose(mean_shares, np.array([0.55, 0.45]), rtol=1e-15)
