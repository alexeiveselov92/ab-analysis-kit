"""M7 WP2 golden parity: ``from_suffstats_array`` vs the scalar ``from_suffstats``.

The array-wise significance kernel (docs/specs/m7-implementation-plan.md §WP2)
must be a pure re-expression of the scalar math: for every method that opts in
via ``supports_vectorized``, running the batch entry once over N rows must
reproduce calling the existing scalar entry N times with the same per-row
inputs. Both paths are computed HERE, in the same environment — there is no
frozen cross-machine fixture, so parity is asserted **bit-exact for every
method and both test types, on every platform**, and that exactness is
structural, not empirical:

- basic ops (+, −, ×, ÷) and ``sqrt`` are IEEE-754 correctly rounded in both
  CPython and numpy — identical by definition;
- ``scipy.special.ndtr`` is the SAME compiled function called scalar-wise and
  ufunc-wise;
- power terms (``mean_den**2/3/4``, CUPED's ``theta**2``, ratio-delta's
  ``ratio**2``) go through ``effects._libm_pow`` — the SAME C-library ``pow``
  CPython's scalar ``**`` uses — because numpy's own integer-exponent power
  fast paths are NOT bit-identical to libm ``pow`` (1-ULP divergences that the
  cancelling delta-method sum amplifies far past rel-1e-9; adversarial review
  round 1, reproduced in test_ratio_delta_cancellation_regression below).

Every battery deliberately includes the scalar guard branches — degenerate
variance (``m2 = 0``), H5 zero/relative denominators, pooled proportions 0/1,
extreme-z rows (the §0.3(2) tail landmine), exact-zero effects — plus
heterogeneous magnitude mixes (per-row scales spanning 1e-4…1e4) that excite
catastrophic cancellation in the relative-variance sum. Deliberate
batch-vs-scalar contract divergences (per-row NaN instead of a batch-wide
exception for ddof-1 ``n < 2`` rows) get their own tests.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pytest

from abkit.stats import create_method
from abkit.stats.effects import BatchEffectResult
from abkit.stats.exceptions import AbkitStatsWarning, SampleValidationError
from abkit.stats.registry import available_methods, get_method_class
from abkit.stats.samples import (
    Fraction,
    JointMoments,
    PairedSufficientStats,
    RatioSufficientStats,
    SufficientStats,
)

SEED = 20260719
N_RANDOM = 300
FIELDS = ("effect", "left_bound", "right_bound", "ci_length", "pvalue")

#: The exact WP2 capability roster — scope creep in either direction fails
#: test_vectorized_capability_roster.
EXPECTED_VECTORIZED = {"z-test", "t-test", "cuped-t-test", "paired-t-test", "ratio-delta"}


def assert_rows_match(batch: BatchEffectResult, scalar_results: list[Any]) -> None:
    """Bit-exact field-by-field row comparison; NaN rows must be NaN on both sides."""
    for field in FIELDS:
        got = getattr(batch, field)
        want = np.array([getattr(result, field) for result in scalar_results], dtype=np.float64)
        np.testing.assert_array_equal(got, want, err_msg=field)


def scalar_loop(method: Any, pairs: list[tuple[Any, Any]]) -> list[Any]:
    """Run the scalar entry per row, silencing advisory warnings (CUPED corr)."""
    results = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", AbkitStatsWarning)
        for stats_1, stats_2 in pairs:
            results.append(method.from_suffstats(stats_1, stats_2))
    return results


def magnitude_mix(rng: np.random.Generator, size: int) -> np.ndarray:
    """Per-row scale factors 1e-4…1e4 — the cancellation-amplification regime."""
    return 10.0 ** rng.integers(-4, 5, size).astype(np.float64)


# --- fixtures: crafted guard-branch rows + a broad random battery per method -----


def ttest_rows(rng: np.random.Generator) -> dict[str, np.ndarray]:
    n_1 = rng.integers(2, 2000, N_RANDOM).astype(np.float64)
    n_2 = rng.integers(2, 2000, N_RANDOM).astype(np.float64)
    mean_1 = rng.normal(0.0, 2.0, N_RANDOM) * magnitude_mix(rng, N_RANDOM)
    mean_2 = rng.normal(0.0, 2.0, N_RANDOM) * magnitude_mix(rng, N_RANDOM)
    m2_1 = rng.gamma(2.0, 5.0, N_RANDOM) * n_1 * magnitude_mix(rng, N_RANDOM)
    m2_2 = rng.gamma(2.0, 5.0, N_RANDOM) * n_2 * magnitude_mix(rng, N_RANDOM)
    # Crafted guard rows (in place of random ones):
    mean_1[0] = 0.0  # relative H5: zero control mean
    m2_1[1] = 0.0  # degenerate variance, arm 1
    m2_2[2] = 0.0  # degenerate variance, arm 2
    m2_1[3] = 0.0
    m2_2[3] = 0.0  # both arms degenerate
    mean_2[4] = mean_1[4]  # exact-zero effect
    mean_1[5], mean_2[5] = 0.0, 50.0
    m2_1[5] = m2_2[5] = 1e-6 * n_1[5]  # extreme z (§0.3(2) tail)
    n_1[6] = n_2[6] = 2.0  # MIN_ARM_UNITS boundary
    return {"n_1": n_1, "mean_1": mean_1, "m2_1": m2_1, "n_2": n_2, "mean_2": mean_2, "m2_2": m2_2}


@pytest.mark.parametrize("test_type", ["absolute", "relative"])
def test_ttest_parity(test_type: str) -> None:
    rows = ttest_rows(np.random.default_rng(SEED))
    method = create_method(
        "t-test", alpha=0.05, params={"test_type": test_type, "calculate_mde": False}
    )
    batch = method.from_suffstats_array(
        {"n": rows["n_1"], "mean": rows["mean_1"], "m2": rows["m2_1"]},
        {"n": rows["n_2"], "mean": rows["mean_2"], "m2": rows["m2_2"]},
    )
    pairs = [
        (
            SufficientStats(int(rows["n_1"][i]), rows["mean_1"][i], rows["m2_1"][i]),
            SufficientStats(int(rows["n_2"][i]), rows["mean_2"][i], rows["m2_2"][i]),
        )
        for i in range(N_RANDOM)
    ]
    assert_rows_match(batch, scalar_loop(method, pairs))


@pytest.mark.parametrize("test_type", ["absolute", "relative"])
def test_ztest_parity(test_type: str) -> None:
    rng = np.random.default_rng(SEED + 1)
    nobs_1 = rng.integers(1, 10_000, N_RANDOM).astype(np.float64)
    nobs_2 = rng.integers(1, 10_000, N_RANDOM).astype(np.float64)
    count_1 = np.floor(nobs_1 * rng.uniform(0.0, 1.0, N_RANDOM))
    count_2 = np.floor(nobs_2 * rng.uniform(0.0, 1.0, N_RANDOM))
    count_1[0] = 0.0  # zero control proportion (relative H5)
    count_2[1] = nobs_2[1]  # saturated treatment arm
    count_1[2], count_2[2] = nobs_1[2], nobs_2[2]  # pooled proportion 1 → zero variance
    count_1[3] = count_2[3] = 0.0  # pooled proportion 0 → zero variance
    count_1[4], count_2[4] = 0.0, nobs_2[4]  # maximal split
    nobs_1[5], nobs_2[5] = 1_000_000.0, 1_000_000.0
    count_1[5], count_2[5] = 1.0, 2000.0  # extreme z (§0.3(2) tail)
    count_2[6] = count_1[6] * (nobs_2[6] / nobs_1[6])  # ≈zero effect

    method = create_method(
        "z-test", alpha=0.05, params={"test_type": test_type, "calculate_mde": False}
    )
    batch = method.from_suffstats_array(
        {"count": count_1, "nobs": nobs_1}, {"count": count_2, "nobs": nobs_2}
    )
    pairs = [
        (Fraction(count_1[i], nobs_1[i]), Fraction(count_2[i], nobs_2[i])) for i in range(N_RANDOM)
    ]
    assert_rows_match(batch, scalar_loop(method, pairs))


def cuped_rows(rng: np.random.Generator) -> dict[str, np.ndarray]:
    columns: dict[str, np.ndarray] = {}
    for arm in ("1", "2"):
        n = rng.integers(2, 2000, N_RANDOM).astype(np.float64)
        m2 = rng.gamma(2.0, 5.0, N_RANDOM) * n * magnitude_mix(rng, N_RANDOM)
        cov_m2 = rng.gamma(2.0, 5.0, N_RANDOM) * n * magnitude_mix(rng, N_RANDOM)
        # |corr| ≤ 0.9 keeps cross_c inside Cauchy–Schwarz (real-moment rows).
        cross_c = rng.uniform(-0.9, 0.9, N_RANDOM) * np.sqrt(m2 * cov_m2)
        columns |= {
            f"n_{arm}": n,
            f"mean_{arm}": rng.normal(0.0, 2.0, N_RANDOM) * magnitude_mix(rng, N_RANDOM),
            f"m2_{arm}": m2,
            f"cov_mean_{arm}": rng.normal(0.0, 2.0, N_RANDOM) * magnitude_mix(rng, N_RANDOM),
            f"cov_m2_{arm}": cov_m2,
            f"cross_c_{arm}": cross_c,
        }
    columns["mean_1"][0] = 0.0  # relative H5: zero ORIGINAL control mean
    columns["cov_m2_1"][1] = columns["cross_c_1"][1] = 0.0
    columns["cov_m2_2"][1] = columns["cross_c_2"][1] = 0.0  # θ = 0/0 → NaN row
    columns["m2_1"][2] = columns["cross_c_1"][2] = 0.0  # degenerate value variance
    columns["cross_c_1"][3] = columns["cross_c_2"][3] = 0.0  # θ = 0 (no correlation)
    columns["mean_1"][4], columns["mean_2"][4] = 0.0, 80.0
    columns["m2_1"][4] = columns["m2_2"][4] = 1e-6 * columns["n_1"][4]  # extreme z
    return columns


@pytest.mark.parametrize("test_type", ["absolute", "relative"])
def test_cuped_ttest_parity(test_type: str) -> None:
    rows = cuped_rows(np.random.default_rng(SEED + 2))
    method = create_method(
        "cuped-t-test", alpha=0.05, params={"test_type": test_type, "calculate_mde": False}
    )
    batch = method.from_suffstats_array(
        {key: rows[f"{key}_1"] for key in ("n", "mean", "m2", "cov_mean", "cov_m2", "cross_c")},
        {key: rows[f"{key}_2"] for key in ("n", "mean", "m2", "cov_mean", "cov_m2", "cross_c")},
    )
    pairs = [
        (
            SufficientStats(
                int(rows["n_1"][i]),
                rows["mean_1"][i],
                rows["m2_1"][i],
                rows["cov_mean_1"][i],
                rows["cov_m2_1"][i],
                rows["cross_c_1"][i],
            ),
            SufficientStats(
                int(rows["n_2"][i]),
                rows["mean_2"][i],
                rows["m2_2"][i],
                rows["cov_mean_2"][i],
                rows["cov_m2_2"][i],
                rows["cross_c_2"][i],
            ),
        )
        for i in range(N_RANDOM)
    ]
    assert_rows_match(batch, scalar_loop(method, pairs))


def ratio_rows(rng: np.random.Generator) -> dict[str, np.ndarray]:
    columns: dict[str, np.ndarray] = {}
    for arm in ("1", "2"):
        n = rng.integers(2, 2000, N_RANDOM).astype(np.float64)
        m2_num = rng.gamma(2.0, 5.0, N_RANDOM) * n * magnitude_mix(rng, N_RANDOM)
        m2_den = rng.gamma(2.0, 5.0, N_RANDOM) * n * magnitude_mix(rng, N_RANDOM)
        c_nd = rng.uniform(-0.9, 0.9, N_RANDOM) * np.sqrt(m2_num * m2_den)
        columns |= {
            f"n_{arm}": n,
            f"mean_num_{arm}": rng.normal(5.0, 2.0, N_RANDOM) * magnitude_mix(rng, N_RANDOM),
            f"m2_num_{arm}": m2_num,
            f"mean_den_{arm}": rng.normal(3.0, 1.0, N_RANDOM) * magnitude_mix(rng, N_RANDOM),
            f"m2_den_{arm}": m2_den,
            f"c_nd_{arm}": c_nd,
        }
    columns["mean_den_1"][0] = 0.0  # H5: zero denominator mean, control
    columns["mean_den_2"][1] = 0.0  # H5, treatment
    columns["mean_num_1"][2] = 0.0  # R₁ = 0 → relative H5 downstream
    # Quadratic-clamp row: c_nd far outside Cauchy–Schwarz forces the
    # max(…, 0.0) branch (a raw-columns-only state — the scalar constructor
    # accepts it too, only m2 ≥ 0 is validated).
    columns["m2_num_1"][3], columns["c_nd_1"][3], columns["m2_den_1"][3] = 1.0, 10.0, 1.0
    columns["mean_num_1"][3] = columns["mean_den_1"][3] = 1.0
    columns["mean_num_1"][4], columns["mean_num_2"][4] = 0.5, 400.0
    columns["m2_num_1"][4] = columns["m2_num_2"][4] = 1e-6  # extreme z
    return columns


@pytest.mark.parametrize("test_type", ["absolute", "relative"])
def test_ratio_delta_parity(test_type: str) -> None:
    rows = ratio_rows(np.random.default_rng(SEED + 3))
    method = create_method("ratio-delta", alpha=0.05, params={"test_type": test_type})
    keys = ("n", "mean_num", "m2_num", "mean_den", "m2_den", "c_nd")
    batch = method.from_suffstats_array(
        {key: rows[f"{key}_1"] for key in keys}, {key: rows[f"{key}_2"] for key in keys}
    )
    pairs = [
        (
            RatioSufficientStats(
                int(rows["n_1"][i]),
                rows["mean_num_1"][i],
                rows["m2_num_1"][i],
                rows["mean_den_1"][i],
                rows["m2_den_1"][i],
                rows["c_nd_1"][i],
            ),
            RatioSufficientStats(
                int(rows["n_2"][i]),
                rows["mean_num_2"][i],
                rows["m2_num_2"][i],
                rows["mean_den_2"][i],
                rows["m2_den_2"][i],
                rows["c_nd_2"][i],
            ),
        )
        for i in range(N_RANDOM)
    ]
    assert_rows_match(batch, scalar_loop(method, pairs))


def test_ratio_delta_cancellation_regression() -> None:
    """Adversarial review round 1's exact reproduction, pinned bit-exact.

    With numpy's native ``**`` these inputs diverged from the scalar path by
    4.5e-8 relative on the CI bounds (1-ULP power-term difference amplified by
    catastrophic cancellation in the delta-method variance sum). The
    ``_libm_pow`` routing must keep them bit-identical.
    """
    method = create_method("ratio-delta", alpha=0.05, params={"test_type": "relative"})
    arm_1 = RatioSufficientStats(
        29,
        7752.938125617748,
        35.72040715448859,
        1.2907779925940976,
        333475.3380173092,
        473.355727940722,
    )
    arm_2 = RatioSufficientStats(
        15,
        4.123662615325683,
        22.64323030645414,
        4303.698109594784,
        24.97350908777478,
        11.116562575540632,
    )
    keys = ("n", "mean_num", "m2_num", "mean_den", "m2_den", "c_nd")
    batch = method.from_suffstats_array(
        {key: np.array([getattr(arm_1, key)], dtype=np.float64) for key in keys},
        {key: np.array([getattr(arm_2, key)], dtype=np.float64) for key in keys},
    )
    assert_rows_match(batch, [method.from_suffstats(arm_1, arm_2)])


def paired_rows(rng: np.random.Generator) -> dict[str, np.ndarray]:
    n = rng.integers(2, 2000, N_RANDOM).astype(np.float64)
    scale = magnitude_mix(rng, N_RANDOM)
    m2_y1 = rng.gamma(2.0, 5.0, N_RANDOM) * n * scale
    m2_y2 = rng.gamma(2.0, 5.0, N_RANDOM) * n * scale
    c_y1y2 = rng.uniform(-0.9, 0.9, N_RANDOM) * np.sqrt(m2_y1 * m2_y2)
    mean_y1 = rng.normal(0.0, 2.0, N_RANDOM) * magnitude_mix(rng, N_RANDOM)
    mean_y2 = rng.normal(0.0, 2.0, N_RANDOM) * magnitude_mix(rng, N_RANDOM)
    mean_y1[0] = 0.0  # relative H5: zero control mean
    m2_y1[1] = m2_y2[1] = c_y1y2[1] = 4.0 * n[1]  # y1 ≡ y2 shape → zero diff variance
    mean_y2[2] = mean_y1[2]  # exact-zero effect
    mean_y1[3], mean_y2[3] = 0.0, 60.0
    m2_y1[3] = m2_y2[3] = 1e-6 * n[3]
    c_y1y2[3] = 0.0  # extreme z
    n[4] = 2.0  # smallest ddof-1-legal pair count
    return {
        "n": n,
        "mean_y1": mean_y1,
        "mean_y2": mean_y2,
        "m2_y1": m2_y1,
        "m2_y2": m2_y2,
        "c_y1y2": c_y1y2,
    }


def paired_joint(rows: dict[str, np.ndarray], i: int) -> PairedSufficientStats:
    moments = JointMoments(
        n=int(rows["n"][i]),
        mean=np.array([rows["mean_y1"][i], rows["mean_y2"][i]]),
        comoment=np.array(
            [
                [rows["m2_y1"][i], rows["c_y1y2"][i]],
                [rows["c_y1y2"][i], rows["m2_y2"][i]],
            ]
        ),
        labels=("y1", "y2"),
    )
    return PairedSufficientStats(moments)


@pytest.mark.parametrize("test_type", ["absolute", "relative"])
def test_paired_ttest_parity(test_type: str) -> None:
    rows = paired_rows(np.random.default_rng(SEED + 4))
    method = create_method("paired-t-test", alpha=0.05, params={"test_type": test_type})
    batch = method.from_suffstats_array(rows)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", AbkitStatsWarning)
        scalars = [method.from_suffstats(paired_joint(rows, i)) for i in range(N_RANDOM)]
    assert_rows_match(batch, scalars)


# --- deliberate batch-vs-scalar contract divergences (documented, pinned) --------


def test_cuped_n1_row_is_nan_not_exception() -> None:
    """ddof-1 needs n ≥ 2: the scalar RAISES, a batch ROW NaN-poisons (a gap)."""
    method = create_method(
        "cuped-t-test", alpha=0.05, params={"test_type": "absolute", "calculate_mde": False}
    )
    columns = {
        "n": np.array([1.0]),
        "mean": np.array([1.0]),
        "m2": np.array([0.0]),
        "cov_mean": np.array([1.0]),
        "cov_m2": np.array([0.0]),
        "cross_c": np.array([0.0]),
    }
    batch = method.from_suffstats_array(columns, columns)
    assert np.isnan(batch.left_bound[0]) and np.isnan(batch.pvalue[0])
    with pytest.raises(SampleValidationError, match="two units"):
        method.from_suffstats(
            SufficientStats(1, 1.0, 0.0, 1.0, 0.0, 0.0),
            SufficientStats(1, 1.0, 0.0, 1.0, 0.0, 0.0),
        )


def test_paired_relative_n1_row_is_nan_not_exception() -> None:
    method = create_method("paired-t-test", alpha=0.05, params={"test_type": "relative"})
    columns = {
        "n": np.array([1.0]),
        "mean_y1": np.array([1.0]),
        "mean_y2": np.array([2.0]),
        "m2_y1": np.array([0.0]),
        "m2_y2": np.array([0.0]),
        "c_y1y2": np.array([0.0]),
    }
    batch = method.from_suffstats_array(columns)
    assert np.isnan(batch.left_bound[0]) and np.isnan(batch.pvalue[0])
    moments = JointMoments(
        n=1, mean=np.array([1.0, 2.0]), comoment=np.zeros((2, 2)), labels=("y1", "y2")
    )
    with pytest.raises(SampleValidationError, match="two units"):
        method.from_suffstats(PairedSufficientStats(moments))


# --- capability roster + input-contract errors ------------------------------------


def test_vectorized_capability_roster() -> None:
    """Exactly the 5 planned methods opt in; every other plugin stays scalar-only."""
    flagged = {name for name in available_methods() if get_method_class(name).supports_vectorized}
    assert flagged == EXPECTED_VECTORIZED


def test_non_vectorized_method_raises_not_implemented() -> None:
    method = create_method("bootstrap", alpha=0.05)
    assert method.supports_vectorized is False
    with pytest.raises(NotImplementedError, match="supports_vectorized=False"):
        method.from_suffstats_array({}, {})


def test_missing_column_raises_validation_error() -> None:
    method = create_method("t-test", alpha=0.05, params={"calculate_mde": False})
    good = {"n": np.array([10.0]), "mean": np.array([1.0]), "m2": np.array([5.0])}
    with pytest.raises(SampleValidationError, match="missing suffstats column"):
        method.from_suffstats_array({"n": good["n"], "mean": good["mean"]}, good)
    with pytest.raises(SampleValidationError, match="arrays_2 is required"):
        method.from_suffstats_array(good, None)


def test_scalar_and_2d_columns_raise_validation_error() -> None:
    """0-d/2-D columns fail loudly — 0-d inputs crashed `_libm_pow` or silently
    malformed the batch result before round 2's 1-D contract."""
    method = create_method("t-test", alpha=0.05, params={"calculate_mde": False})
    good = {"n": np.array([10.0]), "mean": np.array([1.0]), "m2": np.array([5.0])}
    scalar_cols = {"n": 10.0, "mean": 1.0, "m2": 5.0}
    with pytest.raises(SampleValidationError, match="must be a 1-D array"):
        method.from_suffstats_array(scalar_cols, good)
    two_d = {key: np.full((2, 2), value[0]) for key, value in good.items()}
    with pytest.raises(SampleValidationError, match="must be a 1-D array"):
        method.from_suffstats_array(two_d, two_d)


@pytest.mark.parametrize("test_type", ["absolute", "relative"])
def test_fractional_n_matches_scalar_truncation(test_type: str) -> None:
    """Scalar constructors truncate n via int(n); the batch kernels must mirror
    that (np.trunc) or a fractional-n row silently diverges (round 2)."""
    n_frac_1, n_frac_2 = 1999.7, 1500.3
    ttest = create_method(
        "t-test", alpha=0.05, params={"test_type": test_type, "calculate_mde": False}
    )
    batch = ttest.from_suffstats_array(
        {"n": np.array([n_frac_1]), "mean": np.array([5.0]), "m2": np.array([400.0 * 2000])},
        {"n": np.array([n_frac_2]), "mean": np.array([5.2]), "m2": np.array([420.0 * 1500])},
    )
    scalar = ttest.from_suffstats(
        SufficientStats(int(n_frac_1), 5.0, 400.0 * 2000),
        SufficientStats(int(n_frac_2), 5.2, 420.0 * 1500),
    )
    assert_rows_match(batch, [scalar])

    ratio = create_method("ratio-delta", alpha=0.05, params={"test_type": test_type})
    batch = ratio.from_suffstats_array(
        {
            "n": np.array([n_frac_1]),
            "mean_num": np.array([5.0]),
            "m2_num": np.array([90.0]),
            "mean_den": np.array([2.0]),
            "m2_den": np.array([40.0]),
            "c_nd": np.array([12.0]),
        },
        {
            "n": np.array([n_frac_2]),
            "mean_num": np.array([5.5]),
            "m2_num": np.array([80.0]),
            "mean_den": np.array([2.1]),
            "m2_den": np.array([42.0]),
            "c_nd": np.array([11.0]),
        },
    )
    scalar = ratio.from_suffstats(
        RatioSufficientStats(int(n_frac_1), 5.0, 90.0, 2.0, 40.0, 12.0),
        RatioSufficientStats(int(n_frac_2), 5.5, 80.0, 2.1, 42.0, 11.0),
    )
    assert_rows_match(batch, [scalar])

    cuped = create_method(
        "cuped-t-test", alpha=0.05, params={"test_type": test_type, "calculate_mde": False}
    )
    columns_1 = {
        "n": np.array([n_frac_1]),
        "mean": np.array([5.0]),
        "m2": np.array([400.0 * 2000]),
        "cov_mean": np.array([3.0]),
        "cov_m2": np.array([300.0 * 2000]),
        "cross_c": np.array([150.0 * 2000]),
    }
    columns_2 = {
        "n": np.array([n_frac_2]),
        "mean": np.array([5.2]),
        "m2": np.array([420.0 * 1500]),
        "cov_mean": np.array([3.1]),
        "cov_m2": np.array([310.0 * 1500]),
        "cross_c": np.array([160.0 * 1500]),
    }
    batch = cuped.from_suffstats_array(columns_1, columns_2)
    scalar_pairs = [
        (
            SufficientStats(int(n_frac_1), 5.0, 400.0 * 2000, 3.0, 300.0 * 2000, 150.0 * 2000),
            SufficientStats(int(n_frac_2), 5.2, 420.0 * 1500, 3.1, 310.0 * 1500, 160.0 * 1500),
        )
    ]
    assert_rows_match(batch, scalar_loop(cuped, scalar_pairs))


def test_ragged_columns_raise_validation_error() -> None:
    method = create_method("t-test", alpha=0.05, params={"calculate_mde": False})
    good = {"n": np.array([10.0]), "mean": np.array([1.0]), "m2": np.array([5.0])}
    ragged = {"n": np.array([10.0, 20.0]), "mean": np.array([1.0]), "m2": np.array([5.0])}
    with pytest.raises(SampleValidationError, match="share one shape"):
        method.from_suffstats_array(ragged, good)


def test_mismatched_arm_row_counts_raise_validation_error() -> None:
    """Cross-arm row-count mismatch fails loudly, never broadcasts (review round 1)."""
    two = {"n": np.array([10.0, 12.0]), "mean": np.array([1.0, 1.1]), "m2": np.array([5.0, 6.0])}
    one = {"n": np.array([10.0]), "mean": np.array([1.0]), "m2": np.array([5.0])}
    for name in ("t-test", "cuped-t-test", "ratio-delta", "z-test"):
        method = create_method(name, alpha=0.05)
        keys = {
            "t-test": ("n", "mean", "m2"),
            "cuped-t-test": ("n", "mean", "m2", "cov_mean", "cov_m2", "cross_c"),
            "ratio-delta": ("n", "mean_num", "m2_num", "mean_den", "m2_den", "c_nd"),
            "z-test": ("count", "nobs"),
        }[name]
        arrays_two = {key: two["n"] if i == 0 else two["mean"] for i, key in enumerate(keys)}
        arrays_one = {key: one["n"] if i == 0 else one["mean"] for i, key in enumerate(keys)}
        with pytest.raises(SampleValidationError, match="same row count"):
            method.from_suffstats_array(arrays_two, arrays_one)


def test_paired_rejects_second_mapping() -> None:
    method = create_method("paired-t-test", alpha=0.05)
    columns = {
        "n": np.array([3.0]),
        "mean_y1": np.array([1.0]),
        "mean_y2": np.array([2.0]),
        "m2_y1": np.array([1.0]),
        "m2_y2": np.array([1.0]),
        "c_y1y2": np.array([0.5]),
    }
    with pytest.raises(SampleValidationError, match="joint by construction"):
        method.from_suffstats_array(columns, columns)
