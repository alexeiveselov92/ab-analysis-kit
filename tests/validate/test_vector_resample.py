"""The M7 WP3 vectorized resampling engine's contract tests
(docs/specs/m7-implementation-plan.md §WP3).

Three gates, in order of strictness:

- **Mask identity — EXACT.** ``placebo_mask_block`` row ``i`` must equal
  ``placebo_mask(..., derive_seed(*parts, block_start + i))`` byte-for-byte
  (it calls the same function per row — bit-identical by construction).
- **Suffstats parity — rel-1e-9.** ``build_arm_batch`` vs the scalar
  ``build_arm`` per row: the vectorized reduction order differs from ``.sum()``
  over a fancy-indexed slice, so bit-parity is not the contract here; integer
  counts stay exact.
- **Block-size invariance — the measured honest contract.** Masks, per-arm
  counts and degenerate flags are byte-identical under ANY block partition
  (integer/boolean work); float columns are byte-identical run-to-run under a
  FIXED partition and within rtol 1e-12 across DIFFERENT partitions — it was
  measured that no float reduction (BLAS or numpy's own ``sum(axis=1)``) keeps
  the same row bit-stable across buffers with different row counts (see the
  module docstring's "what the block size can and cannot move").
"""

from __future__ import annotations

import tracemalloc

import numpy as np
import pytest

from abkit.stats.bootstrap.engine import BLOCK_QUANTUM, DEFAULT_MAX_BLOCK_BYTES
from abkit.stats.factory import create_method
from abkit.stats.parametric.cuped_ttest import CUPED_ARRAY_KEYS
from abkit.stats.parametric.ratio_delta import RATIO_DELTA_ARRAY_KEYS
from abkit.stats.parametric.ttest import TTEST_ARRAY_KEYS
from abkit.stats.parametric.ztest import ZTEST_ARRAY_KEYS
from abkit.stats.rng import derive_seed
from abkit.stats.samples import Fraction, RatioSufficientStats, SufficientStats
from abkit.validate.panel import PanelCutoff, PlaceboPanel
from abkit.validate.resample import MIN_ARM_UNITS, build_arm, placebo_mask, present_positions
from abkit.validate.vector_resample import (
    _ROW_TEMP_BYTES,
    ArmStatsBatch,
    block_rows,
    build_arm_batch,
    iter_blocks,
    placebo_mask_block,
    prepare_cutoff,
)
from tests.validate._panels import fraction_panel, normal_panel

SEED_PARTS = ("aa", "exp-vec", "metric", "cfg")

# ── fixture builders ────────────────────────────────────────────────────────


def growing_sample_panel(
    *, n_units: int, n_cutoffs: int, seed: int, with_covariate: bool, offset: float = 10.0
) -> PlaceboPanel:
    """A sample panel whose unit set GROWS across cutoffs (n_present < n_units),
    exercising the ``unit_idx`` slice path ``normal_panel`` (full presence) skips."""
    rng = np.random.default_rng(seed)
    values = offset + rng.normal(0.0, 3.0, size=(n_units, n_cutoffs)).cumsum(axis=1)
    covariate = None
    if with_covariate:
        covariate = values[:, -1] * 0.6 + rng.normal(0.0, 3.0, size=n_units)
    cutoffs = []
    for k in range(n_cutoffs):
        n_present = int(n_units * (k + 1) / n_cutoffs)
        cutoffs.append(
            PanelCutoff(
                elapsed_days=float(k + 1),
                is_horizon=(k == n_cutoffs - 1),
                unit_idx=np.arange(n_present),
                values=values[:n_present, k].copy(),
            )
        )
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=tuple(cutoffs),
        covariate=covariate,
        input_kind="sample",
        kept_grid_points=n_cutoffs,
        total_grid_points=n_cutoffs,
    )


def ratio_panel(*, n_units: int, n_cutoffs: int, seed: int) -> PlaceboPanel:
    """A ratio panel (per-unit numerator/denominator) with a growing unit set."""
    rng = np.random.default_rng(seed)
    den = 20.0 + rng.normal(0.0, 2.0, size=(n_units, n_cutoffs)).cumsum(axis=1)
    num = den * 0.4 + rng.normal(0.0, 1.5, size=(n_units, n_cutoffs))
    cutoffs = []
    for k in range(n_cutoffs):
        n_present = int(n_units * (k + 1) / n_cutoffs)
        cutoffs.append(
            PanelCutoff(
                elapsed_days=float(k + 1),
                is_horizon=(k == n_cutoffs - 1),
                unit_idx=np.arange(n_present),
                values=num[:n_present, k].copy(),
                secondary=den[:n_present, k].copy(),
            )
        )
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=tuple(cutoffs),
        covariate=None,
        input_kind="ratio",
        kept_grid_points=n_cutoffs,
        total_grid_points=n_cutoffs,
    )


def _scalar_arm_columns(arm: object) -> dict[str, float] | None:
    """Extract the batch column values from a scalar ``build_arm`` result."""
    if arm is None:
        return None
    if isinstance(arm, Fraction):
        return {"count": arm.count, "nobs": arm.nobs}
    if isinstance(arm, RatioSufficientStats):
        return {
            "n": float(arm.n),
            "mean_num": arm.mean_num,
            "m2_num": arm.m2_num,
            "mean_den": arm.mean_den,
            "m2_den": arm.m2_den,
            "c_nd": arm.c_nd,
        }
    assert isinstance(arm, SufficientStats)
    columns = {"n": float(arm.n), "mean": arm.mean, "m2": arm.m2}
    if arm.has_covariate:
        assert arm.cov_mean is not None and arm.cov_m2 is not None and arm.cross_c is not None
        columns.update({"cov_mean": arm.cov_mean, "cov_m2": arm.cov_m2, "cross_c": arm.cross_c})
    return columns


def _assert_batch_matches_scalar(
    panel: PlaceboPanel, mask_block: np.ndarray, *, rtol: float = 1e-9
) -> int:
    """Row-for-row batch-vs-scalar parity over every cutoff; returns the number
    of non-degenerate (row, cutoff, arm) comparisons actually made (so a test
    can assert the battery wasn't vacuously green)."""
    compared = 0
    for cut in panel.cutoffs:
        batch_a, batch_b = build_arm_batch(panel.input_kind, cut, panel.covariate, mask_block)
        for i, mask_row in enumerate(mask_block):
            pos_a, pos_b = present_positions(mask_row, cut.unit_idx)
            for batch, pos in ((batch_a, pos_a), (batch_b, pos_b)):
                scalar = _scalar_arm_columns(
                    build_arm(
                        panel.input_kind,
                        cut.values,
                        cut.secondary,
                        panel.covariate,
                        cut.unit_idx,
                        pos,
                    )
                )
                assert batch.arm_sizes[i] == pos.size
                if scalar is None:
                    assert bool(batch.degenerate[i]), "scalar gap must be a batch gap"
                    for column in batch.columns.values():
                        assert np.isnan(column[i]), "gap rows must be NaN-poisoned"
                    continue
                assert not bool(batch.degenerate[i]), "batch gap where scalar built an arm"
                assert set(batch.columns) == set(scalar)
                for key, expected in scalar.items():
                    got = batch.columns[key][i]
                    if key in ("n", "count", "nobs"):
                        assert got == expected, f"{key}[{i}]: {got} != {expected} (exact)"
                    else:
                        assert np.isclose(
                            got, expected, rtol=rtol, atol=0.0
                        ), f"{key}[{i}]: {got} vs scalar {expected}"
                compared += 1
    return compared


# ── the mask identity contract (EXACT) ──────────────────────────────────────


@pytest.mark.parametrize("n_units", [5, 100, 1001])
@pytest.mark.parametrize("share_a", [0.5, 0.1, 0.9])
def test_placebo_mask_block_rows_match_scalar_exactly(n_units, share_a):
    for block_start, block_size in ((0, 5), (7, 3), (128, 1)):
        block = placebo_mask_block(n_units, share_a, SEED_PARTS, block_start, block_size)
        assert block.shape == (block_size, n_units) and block.dtype == np.bool_
        for i in range(block_size):
            expected = placebo_mask(n_units, share_a, derive_seed(*SEED_PARTS, block_start + i))
            assert np.array_equal(block[i], expected)  # bit-identical, per row


def test_placebo_mask_block_matches_the_scoring_seed_convention():
    """scoring.py derives ``derive_seed(*seed_parts, i)`` for iteration ``i`` —
    the block must reproduce iteration i at row ``i - block_start``."""
    block = placebo_mask_block(50, 0.5, SEED_PARTS, 10, 4)
    for iteration in (10, 11, 12, 13):
        expected = placebo_mask(50, 0.5, derive_seed(*SEED_PARTS, iteration))
        assert np.array_equal(block[iteration - 10], expected)


def test_placebo_mask_block_validation():
    with pytest.raises(ValueError, match="block_start"):
        placebo_mask_block(10, 0.5, SEED_PARTS, -1, 2)
    with pytest.raises(ValueError, match="block_size"):
        placebo_mask_block(10, 0.5, SEED_PARTS, 0, 0)
    with pytest.raises(ValueError, match="n_units"):  # delegated to placebo_mask
        placebo_mask_block(0, 0.5, SEED_PARTS, 0, 1)


# ── block arithmetic ────────────────────────────────────────────────────────


def test_block_rows_groups_whole_quanta_under_the_cap():
    # 1000 units -> one quantum of temporaries is 128 * 1000 * 10 = 1.28 MB;
    # a 4 MiB cap fits 3 whole quanta.
    assert block_rows(1000, 4 * 1024 * 1024) == 3 * BLOCK_QUANTUM
    # the default cap comfortably fits many quanta of small populations:
    # 268435456 // (128 * 1000 * 10) = 209 whole quanta -> 209 * 128 rows
    # (hand-computed literal, not the function's own formula)
    assert block_rows(1000) == 26752


def test_block_rows_shrinks_below_one_quantum_for_huge_populations():
    """The deliberate divergence from the bootstrap contract: 1e6 units at the
    default 256 MiB cap cannot fit one 128-row quantum (1.4 GB), so the block
    shrinks — results are seed-independent per row, so only memory moves."""
    assert block_rows(1_000_000) == 26  # 268435456 // 10_000_000
    assert block_rows(1_000_000, 1) == 1  # one-row floor, never zero


def test_block_rows_validation():
    with pytest.raises(ValueError, match="n_units"):
        block_rows(0)
    with pytest.raises(ValueError, match="quantum"):
        block_rows(10, quantum=0)


def test_iter_blocks_partitions_exactly():
    assert list(iter_blocks(300, 128)) == [(0, 128), (128, 128), (256, 44)]
    assert list(iter_blocks(5, 128)) == [(0, 5)]
    assert list(iter_blocks(4, 2)) == [(0, 2), (2, 2)]
    with pytest.raises(ValueError, match="iterations"):
        list(iter_blocks(0))
    with pytest.raises(ValueError, match="quantum"):
        list(iter_blocks(10, 0))


# ── suffstats parity vs the scalar path (rel-1e-9; counts exact) ────────────


@pytest.mark.parametrize(
    "panel",
    [
        normal_panel(n_units=300, n_cutoffs=4, seed=11),
        normal_panel(n_units=300, n_cutoffs=4, seed=12, with_covariate=True),
        growing_sample_panel(n_units=240, n_cutoffs=3, seed=13, with_covariate=False),
        growing_sample_panel(n_units=240, n_cutoffs=3, seed=14, with_covariate=True),
        fraction_panel(n_units=400, seed=15),
        ratio_panel(n_units=240, n_cutoffs=3, seed=16),
    ],
    ids=["sample", "cuped", "sample-growing", "cuped-growing", "fraction", "ratio"],
)
def test_build_arm_batch_matches_scalar_build_arm(panel):
    mask_block = placebo_mask_block(panel.n_units, 0.5, SEED_PARTS, 0, 32)
    compared = _assert_batch_matches_scalar(panel, mask_block)
    assert compared > 0


def test_build_arm_batch_parity_at_extreme_share():
    """share_a = 0.9 makes arm B small at early growing cutoffs — the same
    parity must hold near the MIN_ARM_UNITS boundary reached organically."""
    panel = growing_sample_panel(n_units=40, n_cutoffs=4, seed=21, with_covariate=True)
    mask_block = placebo_mask_block(panel.n_units, 0.9, SEED_PARTS, 0, 64)
    compared = _assert_batch_matches_scalar(panel, mask_block)
    assert compared > 0


@pytest.mark.parametrize("offset", [1e8, 1e10], ids=["deep-pass-1e8", "edge-pass-1e10"])
def test_build_arm_batch_parity_on_offset_data(offset):
    """The shifted-one-pass rationale made executable: offset values would fail
    catastrophically under the raw one-pass form samples.py forbids; the
    pooled-shifted form holds rel-1e-9 through the |value|/sigma ~ 1e10/3 edge
    of the documented conditioning band (module docstring)."""
    panel = growing_sample_panel(
        n_units=500, n_cutoffs=2, seed=31, with_covariate=True, offset=offset
    )
    mask_block = placebo_mask_block(panel.n_units, 0.5, SEED_PARTS, 0, 16)
    compared = _assert_batch_matches_scalar(panel, mask_block)
    assert compared > 0


def test_offset_conditioning_boundary_is_documented_not_silent():
    """Beyond |value|/sigma ~ 1e11 float64 itself is the limit: the scalar
    two-pass centers on the ROUNDED arm mean (inflating its m2 by
    count*ulp(|y|)^2/4) while the batch identity centers exactly, so the two
    paths diverge past rel-1e-9 with NO bug on either side (adversarial review
    round 1 measured ~5e-9 at offset 1e12, sigma 3). Pins that the divergence
    stays ULP-inflation-sized (well under 1e-6), not one-pass-catastrophic."""
    panel = growing_sample_panel(
        n_units=500, n_cutoffs=2, seed=31, with_covariate=True, offset=1e12
    )
    mask_block = placebo_mask_block(panel.n_units, 0.5, SEED_PARTS, 0, 16)
    compared = _assert_batch_matches_scalar(panel, mask_block, rtol=1e-6)
    assert compared > 0


def test_constant_valued_arm_keeps_m2_non_negative_and_tiny():
    """All-equal values: the scalar path lands at (near-)exact zero variance;
    the batch path may see tiny roundoff — it must clamp at 0, never go NaN."""
    values = np.full(12, 0.3)
    cut = PanelCutoff(elapsed_days=1.0, is_horizon=True, unit_idx=np.arange(12), values=values)
    mask_block = placebo_mask_block(12, 0.5, SEED_PARTS, 0, 8)
    batch_a, batch_b = build_arm_batch("sample", cut, None, mask_block)
    for batch in (batch_a, batch_b):
        assert not batch.degenerate.any()
        assert (batch.columns["m2"] >= 0.0).all()
        assert (batch.columns["m2"] < 1e-12).all()
        assert np.allclose(batch.columns["mean"], 0.3, rtol=1e-9)


# ── degenerate semantics: gaps, never zeros ─────────────────────────────────


def test_degenerate_rows_are_flagged_per_iteration_not_per_cutoff():
    """A block where SOME rows degenerate and SOME don't: the per-row flags (the
    valid_iterations denominator input) must match the scalar Nones exactly."""
    n_units = 6
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    cut = PanelCutoff(elapsed_days=1.0, is_horizon=True, unit_idx=np.arange(n_units), values=values)
    mask_block = np.array(
        [
            [False, False, False, False, False, False],  # arm A empty -> A gap
            [True, False, False, False, False, False],  # 1 < MIN_ARM_UNITS -> A gap
            [True, True, False, False, False, False],  # exactly MIN -> both fine
            [True, True, True, False, False, False],  # both fine
            [True, True, True, True, True, False],  # arm B = 1 -> B gap
            [True, True, True, True, True, True],  # arm B empty -> B gap
        ]
    )
    batch_a, batch_b = build_arm_batch("sample", cut, None, mask_block)
    assert list(batch_a.degenerate) == [True, True, False, False, False, False]
    assert list(batch_b.degenerate) == [False, False, False, False, True, True]
    assert list(batch_a.arm_sizes) == [0, 1, 2, 3, 5, 6]
    assert list(batch_b.arm_sizes) == [6, 5, 4, 3, 1, 0]
    # the non-degenerate DENOMINATOR (usable rows per arm pair) matches scalar
    usable_batch = int((~(batch_a.degenerate | batch_b.degenerate)).sum())
    usable_scalar = 0
    for mask_row in mask_block:
        pos_a, pos_b = present_positions(mask_row, cut.unit_idx)
        arm_a = build_arm("sample", values, None, None, cut.unit_idx, pos_a)
        arm_b = build_arm("sample", values, None, None, cut.unit_idx, pos_b)
        usable_scalar += int(arm_a is not None and arm_b is not None)
    assert usable_batch == usable_scalar == 2  # only rows 2 and 3 keep BOTH arms >= MIN
    # NaN poisoning on gap rows; MIN_ARM_UNITS row (exactly 2) is a real arm
    assert np.isnan(batch_a.columns["mean"][0]) and np.isnan(batch_a.columns["mean"][1])
    assert batch_a.columns["n"][2] == float(MIN_ARM_UNITS)
    assert batch_a.columns["mean"][2] == pytest.approx(1.5, rel=1e-9)


def test_fraction_zero_trial_arm_is_a_gap_like_the_scalar_path():
    """nobs <= 0 is a gap even when the arm has >= MIN_ARM_UNITS units."""
    n_units = 6
    values = np.array([1.0, 0.0, 1.0, 0.0, 1.0, 1.0])
    trials = np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0])  # units 0-2 carry no trials
    cut = PanelCutoff(
        elapsed_days=1.0,
        is_horizon=True,
        unit_idx=np.arange(n_units),
        values=values,
        secondary=trials,
    )
    mask_block = np.array(
        [
            [True, True, True, False, False, False],  # arm A: 3 units, 0 trials -> gap
            [True, True, False, True, False, False],  # arm A: 1 trial -> fine
        ]
    )
    batch_a, batch_b = build_arm_batch("fraction", cut, None, mask_block)
    assert list(batch_a.degenerate) == [True, False]
    assert list(batch_b.degenerate) == [False, False]
    # scalar agreement on both rows
    for i, mask_row in enumerate(mask_block):
        pos_a, _ = present_positions(mask_row, cut.unit_idx)
        scalar_a = build_arm("fraction", values, trials, None, cut.unit_idx, pos_a)
        assert (scalar_a is None) == bool(batch_a.degenerate[i])


@pytest.mark.parametrize(
    ("input_kind", "with_covariate", "expected_keys"),
    [
        ("sample", False, TTEST_ARRAY_KEYS),
        ("sample", True, CUPED_ARRAY_KEYS),
        ("fraction", False, ZTEST_ARRAY_KEYS),
        ("ratio", False, RATIO_DELTA_ARRAY_KEYS),
    ],
    ids=["sample", "cuped", "fraction", "ratio"],
)
def test_empty_cutoff_yields_all_degenerate_batches(input_kind, with_covariate, expected_keys):
    empty = np.array([], dtype=np.float64)
    cut = PanelCutoff(
        elapsed_days=1.0,
        is_horizon=True,
        unit_idx=np.arange(0),
        values=empty,
        secondary=empty if input_kind in ("fraction", "ratio") else None,
    )
    covariate = np.ones(10) if with_covariate else None
    mask_block = placebo_mask_block(10, 0.5, SEED_PARTS, 0, 4)
    batch_a, batch_b = build_arm_batch(input_kind, cut, covariate, mask_block)
    for batch in (batch_a, batch_b):
        assert batch.degenerate.all()
        assert set(batch.columns) == set(expected_keys)
        for column in batch.columns.values():
            assert np.isnan(column).all()


def test_build_arm_batch_validation_matches_scalar_messages():
    cut = PanelCutoff(elapsed_days=1.0, is_horizon=True, unit_idx=np.arange(3), values=np.ones(3))
    mask_block = placebo_mask_block(3, 0.5, SEED_PARTS, 0, 2)
    with pytest.raises(ValueError, match="nobs"):
        build_arm_batch("fraction", cut, None, mask_block)
    with pytest.raises(ValueError, match="denominator"):
        build_arm_batch("ratio", cut, None, mask_block)
    with pytest.raises(ValueError, match="2-D boolean"):
        build_arm_batch("sample", cut, None, mask_block[0])
    with pytest.raises(ValueError, match="2-D boolean"):
        build_arm_batch("sample", cut, None, mask_block.astype(np.int8))


# ── block-size invariance (EXACT) ───────────────────────────────────────────


def _accumulate_columns(panel: PlaceboPanel, iterations: int, block: int):
    """Run the block pipeline and concatenate every per-cutoff column across
    blocks — the aggregate a WP4 scorer would consume."""
    per_cutoff: list[dict[str, list[np.ndarray]]] = [
        {"degenerate_a": [], "degenerate_b": []} for _ in panel.cutoffs
    ]
    for block_start, block_size in iter_blocks(iterations, block):
        mask_block = placebo_mask_block(panel.n_units, 0.5, SEED_PARTS, block_start, block_size)
        for k, cut in enumerate(panel.cutoffs):
            batch_a, batch_b = build_arm_batch(panel.input_kind, cut, panel.covariate, mask_block)
            store = per_cutoff[k]
            store["degenerate_a"].append(batch_a.degenerate)
            store["degenerate_b"].append(batch_b.degenerate)
            for arm_label, batch in (("a", batch_a), ("b", batch_b)):
                for key, column in batch.columns.items():
                    store.setdefault(f"{key}_{arm_label}", []).append(column)
    return [{key: np.concatenate(chunks) for key, chunks in store.items()} for store in per_cutoff]


#: Columns that are integer/boolean work — bit-identical under ANY partition.
_EXACT_KEYS = ("degenerate_a", "degenerate_b", "n_a", "n_b")


def _assert_partition_invariance(reference, candidate, *, context: str) -> None:
    for ref_store, cand_store in zip(reference, candidate, strict=True):
        assert set(ref_store) == set(cand_store)
        for key, ref_column in ref_store.items():
            cand_column = cand_store[key]
            if key in _EXACT_KEYS:
                assert np.array_equal(
                    ref_column, cand_column, equal_nan=True
                ), f"integer/boolean work moved under {context} in {key}"
            else:
                # float reductions are NOT bit-stable across row counts (module
                # docstring, measured) — but any drift is strictly ULP-class,
                # far inside the rel-1e-9 scalar-parity budget.
                nan_ref = np.isnan(ref_column)
                assert np.array_equal(nan_ref, np.isnan(cand_column))
                assert np.allclose(
                    ref_column[~nan_ref], cand_column[~nan_ref], rtol=1e-12, atol=0.0
                ), f"block-size drift beyond ULP-class in {key} under {context}"


def test_block_size_invariance_across_quanta():
    """quantum in {32, 128, 1000, iterations, 7}: masks/counts/degenerate flags
    are byte-identical; float columns stay within rtol 1e-12 (the measured
    honest contract — see the module docstring)."""
    iterations = 200
    panel = growing_sample_panel(n_units=150, n_cutoffs=3, seed=41, with_covariate=True)
    reference = _accumulate_columns(panel, iterations, 128)
    for quantum in (32, 1000, iterations, 7, 1):
        candidate = _accumulate_columns(panel, iterations, quantum)
        _assert_partition_invariance(reference, candidate, context=f"quantum={quantum}")


def test_block_size_invariance_under_byte_caps():
    """Cap-driven block sizes (incl. the one-row floor) keep the same contract."""
    iterations = 50
    panel = ratio_panel(n_units=120, n_cutoffs=2, seed=42)
    reference = _accumulate_columns(panel, iterations, block_rows(panel.n_units))
    for cap in (1, 50_000, 10_000_000):  # 1 byte -> one-row floor
        block = block_rows(panel.n_units, cap)
        candidate = _accumulate_columns(panel, iterations, block)
        _assert_partition_invariance(reference, candidate, context=f"cap={cap}")


def test_fixed_blocking_is_byte_reproducible():
    """Under ONE fixed partition, a re-run reproduces every float byte — the
    D13 guarantee the WP4 scorer inherits (its blocking is a deterministic
    function of (iterations, n_units))."""
    iterations = 96
    panel = growing_sample_panel(n_units=130, n_cutoffs=3, seed=43, with_covariate=True)
    first = _accumulate_columns(panel, iterations, 32)
    second = _accumulate_columns(panel, iterations, 32)
    for store_1, store_2 in zip(first, second, strict=True):
        for key, column in store_1.items():
            assert np.array_equal(column, store_2[key], equal_nan=True), key


# ── the WP2 handoff: columns feed from_suffstats_array directly ─────────────


def test_batch_columns_carry_the_wp2_kernel_keys():
    """The ArmStatsBatch column vocabulary IS the WP2 batch-entry vocabulary —
    a key drift here would break the WP4 engine at runtime."""
    sample = growing_sample_panel(n_units=60, n_cutoffs=2, seed=51, with_covariate=False)
    cuped = growing_sample_panel(n_units=60, n_cutoffs=2, seed=52, with_covariate=True)
    fraction = fraction_panel(n_units=60, seed=53)
    ratio = ratio_panel(n_units=60, n_cutoffs=2, seed=54)
    mask_block = placebo_mask_block(60, 0.5, SEED_PARTS, 0, 4)
    for panel, keys in (
        (sample, TTEST_ARRAY_KEYS),
        (cuped, CUPED_ARRAY_KEYS),
        (fraction, ZTEST_ARRAY_KEYS),
        (ratio, RATIO_DELTA_ARRAY_KEYS),
    ):
        batch_a, _ = build_arm_batch(
            panel.input_kind, panel.cutoffs[0], panel.covariate, mask_block
        )
        assert set(batch_a.columns) == set(keys)


@pytest.mark.parametrize(
    ("method_name", "panel"),
    [
        ("t-test", growing_sample_panel(n_units=200, n_cutoffs=2, seed=61, with_covariate=False)),
        (
            "cuped-t-test",
            growing_sample_panel(n_units=200, n_cutoffs=2, seed=62, with_covariate=True),
        ),
        ("z-test", fraction_panel(n_units=300, seed=63)),
        ("ratio-delta", ratio_panel(n_units=200, n_cutoffs=2, seed=64)),
    ],
    ids=["t-test", "cuped", "z-test", "ratio-delta"],
)
def test_batches_flow_through_from_suffstats_array(method_name, panel):
    """End-to-end WP3 -> WP2 wiring: feed both arms' batches into the method's
    array kernel and compare against the scalar from_suffstats per row.

    Tolerance is rel-1e-6, looser than the suffstats gate: the batch INPUTS
    differ from the scalar ones at rel-1e-9 (reduction order), and the
    delta-method variance can amplify input-level differences (the WP2 round-1
    cancellation finding) — this test pins the wiring and the NaN/gap flow, not
    a second numeric gate (that is WP5's job at the CellScore level)."""
    method = create_method(method_name, alpha=0.05)
    mask_block = placebo_mask_block(panel.n_units, 0.5, SEED_PARTS, 0, 16)
    cut = panel.cutoffs[-1]
    batch_a, batch_b = build_arm_batch(panel.input_kind, cut, panel.covariate, mask_block)
    result = method.from_suffstats_array(batch_a.columns, batch_b.columns)
    for i, mask_row in enumerate(mask_block):
        pos_a, pos_b = present_positions(mask_row, cut.unit_idx)
        arm_a = build_arm(
            panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_a
        )
        arm_b = build_arm(
            panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_b
        )
        assert arm_a is not None and arm_b is not None  # 50/50 split over these panels
        scalar = method.from_suffstats(arm_a, arm_b)
        assert np.isclose(result.effect[i], scalar.effect, rtol=1e-6, atol=0.0)
        assert np.isclose(result.left_bound[i], scalar.left_bound, rtol=1e-6, atol=0.0)
        assert np.isclose(result.right_bound[i], scalar.right_bound, rtol=1e-6, atol=0.0)
        assert np.isclose(result.pvalue[i], scalar.pvalue, rtol=1e-6, atol=1e-15)


def test_degenerate_rows_nan_poison_through_the_wp2_kernel():
    """A gap row's NaN columns must come out of from_suffstats_array as NaN
    bounds (a gap, never a zero/significant row) while other rows stay real."""
    method = create_method("t-test", alpha=0.05)
    values = np.arange(1.0, 9.0)
    cut = PanelCutoff(elapsed_days=1.0, is_horizon=True, unit_idx=np.arange(8), values=values)
    mask_block = np.array(
        [
            [True, False, False, False, False, False, False, False],  # A gap
            [True, True, True, True, False, False, False, False],  # fine
        ]
    )
    batch_a, batch_b = build_arm_batch("sample", cut, None, mask_block)
    result = method.from_suffstats_array(batch_a.columns, batch_b.columns)
    assert np.isnan(result.left_bound[0]) and np.isnan(result.right_bound[0])
    assert np.isfinite(result.left_bound[1]) and np.isfinite(result.right_bound[1])


# ── the memory budget is real, not aspirational ─────────────────────────────


def test_block_temporaries_stay_within_the_cap_at_a_million_units():
    """1e6 units under a 32 MiB cap: block_rows shrinks the block to 3 rows and
    the traced peak stays within a small multiple of the cap. The cap governs
    the BLOCK-scaled temporaries (mask rows / arm slices / the product buffer);
    the per-cutoff value columns are n_units-sized once per cutoff regardless
    of the cap (like the bootstrap engine's stratum_values working set), which
    is why the assertion allows the fixed ~3x-cap overhead."""
    n_units = 1_000_000
    cap = 32 * 1024 * 1024
    rows = block_rows(n_units, cap)
    assert rows == 3  # 33554432 // 10_000_000
    rng = np.random.default_rng(71)
    values = 5.0 + rng.normal(0.0, 1.0, size=n_units)
    cut = PanelCutoff(elapsed_days=1.0, is_horizon=True, unit_idx=np.arange(n_units), values=values)
    tracemalloc.start()
    try:
        mask_block = placebo_mask_block(n_units, 0.5, SEED_PARTS, 0, rows)
        batch_a, batch_b = build_arm_batch("sample", cut, None, mask_block)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert not batch_a.degenerate.any() and not batch_b.degenerate.any()
    assert peak < 3 * cap, f"peak {peak / 2**20:.1f} MiB breached 3x the {cap / 2**20:.0f} MiB cap"


def test_cuped_block_temporaries_split_capped_and_fixed_parts_at_a_million_units():
    """The CUPED/ratio worst case the round-1 contracts review flagged: the cap
    governs ONLY the block-scaled working set; the k=5 shifted value columns +
    the covariate slice are a cap-INDEPENDENT ~8*k*n_units fixed overhead per
    cutoff (module docstring "the memory tests assert both parts separately").
    The allowance is therefore 3x cap (block-scaled part + slack) PLUS the
    explicit fixed-column budget — measured ~82 MB at these sizes."""
    n_units = 1_000_000
    cap = 32 * 1024 * 1024
    rows = block_rows(n_units, cap)
    rng = np.random.default_rng(72)
    values = 5.0 + rng.normal(0.0, 1.0, size=n_units)
    covariate = values * 0.6 + rng.normal(0.0, 1.0, size=n_units)
    cut = PanelCutoff(elapsed_days=1.0, is_horizon=True, unit_idx=np.arange(n_units), values=values)
    fixed_columns_budget = (5 + 1) * 8 * n_units  # k=5 columns + the covariate slice
    tracemalloc.start()
    try:
        mask_block = placebo_mask_block(n_units, 0.5, SEED_PARTS, 0, rows)
        build_arm_batch("sample", cut, covariate, mask_block)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 3 * cap + fixed_columns_budget, (
        f"peak {peak / 2**20:.1f} MiB breached the split budget "
        f"({(3 * cap + fixed_columns_budget) / 2**20:.0f} MiB)"
    )


def test_default_block_at_a_million_units_would_not_fit_the_default_cap():
    """Documents WHY the sub-quantum floor exists: one 128-row quantum's float64
    product buffer alone at 1e6 units is 1.02 GB >> the 256 MiB default cap."""
    one_quantum_product = BLOCK_QUANTUM * 1_000_000 * 8
    assert one_quantum_product > DEFAULT_MAX_BLOCK_BYTES
    assert block_rows(1_000_000) * 1_000_000 * _ROW_TEMP_BYTES <= DEFAULT_MAX_BLOCK_BYTES


def test_malformed_fraction_count_above_nobs_flows_to_a_nan_gap_downstream():
    """The one documented build-level divergence: successes > trials data
    CRASHES the scalar path (Fraction validation) but flows through the batch
    path as numbers (panel hygiene is upstream). Pin what actually happens
    downstream: prop > 1 makes the z-test variance negative -> NaN CI bounds,
    i.e. a gap — never a finite fake-significant row."""
    method = create_method("z-test", alpha=0.05)
    values = np.array([5.0, 4.0, 3.0, 2.0])  # per-unit "successes"
    trials = np.ones(4)  # < successes: malformed upstream data
    cut = PanelCutoff(
        elapsed_days=1.0,
        is_horizon=True,
        unit_idx=np.arange(4),
        values=values,
        secondary=trials,
    )
    mask_block = np.array([[True, True, False, False]])
    batch_a, batch_b = build_arm_batch("fraction", cut, None, mask_block)
    assert not batch_a.degenerate[0] and not batch_b.degenerate[0]
    # scalar-side the same arm raises at construction
    with pytest.raises(Exception, match="count"):
        Fraction(count=9.0, nobs=2.0)
    result = method.from_suffstats_array(batch_a.columns, batch_b.columns)
    assert np.isnan(result.left_bound[0]) and np.isnan(result.right_bound[0])


def test_inject_multiplicative_columns_matches_the_scalar_injection():
    """The WP3->WP4 injected-pass seam: the batch injection over
    ArmStatsBatch.columns is BIT-exact vs inject_multiplicative given the same
    inputs (same per-element op order), and clamping mirrors injection_clamped
    per row — including a saturating Fraction row."""
    from abkit.validate.inject import (
        inject_multiplicative,
        inject_multiplicative_columns,
        injection_clamped,
        injection_clamped_columns,
    )

    delta = 0.07
    panels = {
        "sample": growing_sample_panel(n_units=80, n_cutoffs=2, seed=95, with_covariate=False),
        "cuped": growing_sample_panel(n_units=80, n_cutoffs=2, seed=96, with_covariate=True),
        "fraction": fraction_panel(n_units=80, seed=97, base_rate=0.95),  # high rate: clamps
        "ratio": ratio_panel(n_units=80, n_cutoffs=2, seed=98),
    }
    for label, panel in panels.items():
        cut = panel.cutoffs[-1]
        mask_block = placebo_mask_block(panel.n_units, 0.5, SEED_PARTS, 0, 8)
        _, batch_b = build_arm_batch(panel.input_kind, cut, panel.covariate, mask_block)
        injected = inject_multiplicative_columns(panel.input_kind, batch_b.columns, delta)
        clamped = injection_clamped_columns(panel.input_kind, batch_b.columns, delta)
        assert set(injected) == set(batch_b.columns), label
        for i, mask_row in enumerate(mask_block):
            _, pos_b = present_positions(mask_row, cut.unit_idx)
            arm_b = build_arm(
                panel.input_kind, cut.values, cut.secondary, panel.covariate, cut.unit_idx, pos_b
            )
            assert arm_b is not None
            assert clamped[i] == injection_clamped(arm_b, delta), label
            # bit-exactness of the injection ALGEBRA: feed the scalar arm's own
            # column values through the batch function and compare exactly
            scalar_columns = _scalar_arm_columns(arm_b)
            assert scalar_columns is not None
            exact_in = {key: np.array([value]) for key, value in scalar_columns.items()}
            exact_out = inject_multiplicative_columns(panel.input_kind, exact_in, delta)
            scalar_injected = _scalar_arm_columns(inject_multiplicative(arm_b, delta))
            assert scalar_injected is not None
            for key, expected in scalar_injected.items():
                assert exact_out[key][0] == expected, f"{label}:{key} (bit-exact)"
            # and the end-to-end batch row stays within the build-parity budget
            for key, expected in scalar_injected.items():
                assert np.isclose(
                    injected[key][i], expected, rtol=1e-9, atol=0.0
                ), f"{label}:{key}[{i}]"


def test_inject_multiplicative_columns_preserves_nan_poison():
    """A degenerate row's NaN poison must survive injection (gaps stay gaps)."""
    from abkit.validate.inject import inject_multiplicative_columns, injection_clamped_columns

    values = np.arange(1.0, 7.0)
    cut = PanelCutoff(elapsed_days=1.0, is_horizon=True, unit_idx=np.arange(6), values=values)
    mask_block = np.array([[True, False, False, False, False, False]])  # arm A degenerate
    batch_a, _ = build_arm_batch("sample", cut, None, mask_block)
    assert batch_a.degenerate[0]
    injected = inject_multiplicative_columns("sample", batch_a.columns, 0.05)
    for key in ("mean", "m2"):
        assert np.isnan(injected[key][0]), key
    fraction_nan = {"count": np.array([np.nan]), "nobs": np.array([np.nan])}
    injected_fraction = inject_multiplicative_columns("fraction", fraction_nan, 0.05)
    assert np.isnan(injected_fraction["count"][0])
    assert not injection_clamped_columns("fraction", fraction_nan, 0.05)[0]


def test_weights_scratch_is_a_pure_perf_knob():
    """A reused scratch buffer changes nothing but ULP-class float noise:
    counts/flags exact, float columns within rtol 1e-12, and validation
    rejects an undersized or wrong-dtype buffer."""
    panel = growing_sample_panel(n_units=90, n_cutoffs=3, seed=91, with_covariate=True)
    mask_block = placebo_mask_block(panel.n_units, 0.5, SEED_PARTS, 0, 20)
    scratch = np.empty((20, panel.n_units), dtype=np.float64)
    for cut in panel.cutoffs:
        plain_a, plain_b = build_arm_batch("sample", cut, panel.covariate, mask_block)
        scr_a, scr_b = build_arm_batch(
            "sample", cut, panel.covariate, mask_block, weights_scratch=scratch
        )
        for plain, scr in ((plain_a, scr_a), (plain_b, scr_b)):
            assert np.array_equal(plain.degenerate, scr.degenerate)
            assert np.array_equal(plain.arm_sizes, scr.arm_sizes)
            for key, column in plain.columns.items():
                assert np.allclose(column, scr.columns[key], rtol=1e-12, atol=0.0), key
    with pytest.raises(ValueError, match="weights_scratch"):
        build_arm_batch(
            "sample",
            panel.cutoffs[0],
            panel.covariate,
            mask_block,
            weights_scratch=np.empty((3, panel.n_units), dtype=np.float64),
        )
    with pytest.raises(ValueError, match="weights_scratch"):
        build_arm_batch(
            "sample",
            panel.cutoffs[0],
            panel.covariate,
            mask_block,
            weights_scratch=np.empty((20, panel.n_units), dtype=np.float32),
        )


def test_prepared_cutoff_is_bit_identical_to_the_inline_build():
    """Hoisting prepare_cutoff out of the block loop is a pure code motion:
    the same arrays feed the same GEMM shapes, so every byte matches."""
    panel = ratio_panel(n_units=80, n_cutoffs=2, seed=92)
    mask_block = placebo_mask_block(panel.n_units, 0.5, SEED_PARTS, 0, 12)
    for cut in panel.cutoffs:
        prepared = prepare_cutoff(panel.input_kind, cut, panel.covariate)
        inline_a, inline_b = build_arm_batch(panel.input_kind, cut, panel.covariate, mask_block)
        hoist_a, hoist_b = build_arm_batch(
            panel.input_kind, cut, panel.covariate, mask_block, prepared=prepared
        )
        for inline, hoisted in ((inline_a, hoist_a), (inline_b, hoist_b)):
            assert np.array_equal(inline.degenerate, hoisted.degenerate)
            assert np.array_equal(inline.arm_sizes, hoisted.arm_sizes)
            for key, column in inline.columns.items():
                assert np.array_equal(column, hoisted.columns[key], equal_nan=True), key


def test_arm_stats_batch_shape_contract():
    """Every column, the degenerate mask and arm_sizes share the block length."""
    panel = normal_panel(n_units=50, n_cutoffs=2, seed=81)
    mask_block = placebo_mask_block(50, 0.5, SEED_PARTS, 0, 9)
    batch_a, batch_b = build_arm_batch("sample", panel.cutoffs[0], None, mask_block)
    for batch in (batch_a, batch_b):
        assert isinstance(batch, ArmStatsBatch)
        assert batch.degenerate.shape == (9,) and batch.arm_sizes.shape == (9,)
        for column in batch.columns.values():
            assert column.shape == (9,) and column.dtype == np.float64
