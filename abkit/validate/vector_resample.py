"""Block-streamed vectorized placebo resampling (M7 WP3 — m7-implementation-plan.md §WP3).

The scalar A/A hot loop calls :func:`abkit.validate.resample.build_arm` once per
``(iteration, cutoff, arm)`` — 800k+ python-level passes at the reference shape.
This module collapses a whole *block* of iterations into a handful of BLAS calls
per cutoff: a ``(block × n_present)`` arm-membership weight matrix contracted
against the cutoff's per-unit value columns, one matrix-vector product per
sufficient-statistic component per arm.

MASK IDENTITY CONTRACT (bit-identical by construction)
======================================================

Row ``i`` of :func:`placebo_mask_block` **is** ``placebo_mask(n_units, share_a,
derive_seed(*seed_parts, block_start + i))`` — the same function, called per row.
Iteration seeds are independently derived from identity parts (D13, rng.py), so
unlike the bootstrap engine's single sequential stream there is NO draw-order
coupling between rows: any block partition of ``range(iterations)`` yields the
same masks, and therefore the same aggregates, byte for byte.

BLOCK / MEMORY CONTRACT (mirrors ``abkit/stats/bootstrap/engine.py``)
=====================================================================

Blocks default to whole quanta of :data:`BLOCK_QUANTUM` (= 128) iterations and a
:data:`DEFAULT_MAX_BLOCK_BYTES` (256 MiB) cap sizes how many rows are
materialised at once (:func:`block_rows`). One deliberate divergence from the
bootstrap contract: because mask rows are seed-independent (above), a block may
shrink BELOW one quantum (floor: one row) when a single quantum of
``n_units``-wide temporaries would not fit the cap — the bootstrap engine
cannot do this (its random stream is drawn in whole quanta), this engine can.
(The engine's own ``iter_resample_blocks`` arithmetic is structurally unable to
produce sub-quantum blocks, which is why only its constants are imported here
rather than the block-count policy itself.) The cap governs the BLOCK-scaled
working set only (:data:`_ROW_TEMP_BYTES` per row per unit); each cutoff
additionally materialises ``k ≤ 5`` value columns of ``8·n_present`` bytes
(:func:`prepare_cutoff`) that are cap-INDEPENDENT — the same class of fixed
overhead as the bootstrap engine's ``stratum_values`` working set. At 1e6
units a CUPED/ratio cutoff carries ~40 MB of such columns regardless of the
cap; the memory tests assert both parts separately.

WHAT THE BLOCK SIZE CAN AND CANNOT MOVE (measured — the honest contract)
========================================================================

The bootstrap engine promises "any cap yields byte-identical results" and can
keep it: its per-replicate reductions are row-wise over a fixed length. This
engine's per-arm sums CANNOT keep that promise bit-for-bit, and no float
reduction can: it was measured (OpenBLAS 0.3.30 + numpy 2.3) that (a) BLAS
GEMV/GEMM pick M-dependent kernels, and (b) even numpy's own pairwise
``.sum(axis=1)`` reduces the SAME row to a different ULP inside buffers with a
different number of rows (an internal blocking effect; a bitwise-identical
product row summed alone vs inside a 50-row buffer differed by 1 ULP). The
contract is therefore:

- **Masks, per-arm unit counts, degenerate flags** — bit-identical under ANY
  block partition, by construction (integer/boolean work).
- **Float suffstats columns** — bit-reproducible run-to-run under a FIXED
  block partition (one ``weights @ value_matrix`` GEMM per arm per cutoff:
  deterministic for fixed shapes, the same BLAS reliance the Poisson
  bootstrap engine already ships under the byte-reproducibility e2e gate,
  engine.py:33); across DIFFERENT block partitions they may move at the ULP
  level (gated at rtol 1e-12 by the invariance tests — far inside the
  rel-1e-9 scalar-parity budget).

The WP4 scorer derives its blocking deterministically from
``(iterations, n_units)`` via :func:`block_rows`, so persisted A/A numbers
stay byte-reproducible (D13) exactly like the scalar loop's. (A plain
``multiply`` + ``sum(axis=1)`` aggregation was tried first for its
BLAS-independence and measured 10-20x slower — ten unfused memory passes per
cutoff versus GEMM's one — without buying the cross-partition bit-stability
it aimed for: numpy's own axis reduction rounds the same row differently in
buffers with different row counts.)

WHY PER-CUTOFF DENSE CONTRACTION, NOT A CROSS-CUTOFF PREFIX SUM (§4.4 — resolved)
=================================================================================

``RecomputeBackend`` re-renders the metric SQL over the FULL cumulative window
``[start_ts, end_ts)`` fresh per cutoff (recompute_backend.py module docstring),
and the SQL is arbitrary per-unit aggregation. A continuing unit's per-cutoff
value is therefore NOT an append-only stream — empirically, both packaged
example metrics already break appendability: ``example_arpu``'s
``sum(gross_usd)`` DECREASES when a refund lands as a negative later event, and
``example_signup``'s ``max(signed_up)``/AVG-shaped patterns are not additive at
all. Only the unit SET grows monotonically across cutoffs (panel.py); the
values move arbitrarily. A value-level prefix-sum across cutoffs is thus
**permanently inapplicable** at this layer (not merely deferred) — do not
"optimize" it back in. The dominant win stays: ~5 GEMV calls per arm per cutoff
replace ``block × cutoffs × arms`` python-level ``build_arm`` constructions.

SHIFTED ONE-PASS CO-MOMENTS (why this does not violate samples.py's rule)
=========================================================================

``samples.py`` forbids the raw one-pass ``Σx²/n − x̄²`` form (catastrophic
cancellation on offset data). The batch kernels use the *pooled-shifted*
one-pass instead: every value column is centered ONCE per cutoff on its pooled
mean (iteration-independent), and per-arm moments are recovered as
``Σ_arm c² − (Σ_arm c)²/count``. The shift subtraction is exact (values and
their pooled mean sit within a factor of two of each other — Sterbenz), after
which the identity yields moments about the arm's EXACT mean; the scalar
two-pass path instead centers on the float-ROUNDED arm mean, inflating its
``m2`` by ``count · δ²`` with ``δ ≤ ulp(|value|)/2``. The two paths therefore
diverge as roughly ``(eps · |value| / σ)² / 4`` relative (adversarial review
round 1 corrected an earlier "arm mean is near the shift point" rationale —
that deviation is real but is NOT what bounds the error): measured
~1e-15 at ``|value|/σ = 1e8/3``, ~1e-10 at ``1e11/3``, breaking rel-1e-9 at
``~1e12/3``. A metric with ``|value|/σ ≳ 1e11`` is conditioning-degenerate in
float64 for ANY variance algorithm (adjacent representable values are
``eps·|value|`` apart — the spread itself is quantization-dominated), so the
rel-1e-9 parity gate is scoped to ``|value|/σ ≲ 1e10``; the tests pin a deep
pass at 1e8, an edge pass at 1e10, and the measured ULP-inflation boundary at
1e12. Roundoff can leave a tiny negative ``m2`` where the scalar path is
exactly 0 — clamped to 0.0 to keep the ``m2 ≥ 0`` container contract.

DEGENERATE ARMS ARE GAPS, NEVER ZEROS (scoring.py:26-27)
========================================================

``build_arm`` returns ``None`` per ``(iteration, cutoff)`` when an arm has
fewer than :data:`MIN_ARM_UNITS` present units (or a fraction arm has no
trials). The batch mirror is the per-row ``degenerate`` mask on
:class:`ArmStatsBatch`; degenerate rows' columns are NaN-poisoned so an
accidental read can never contribute a silent zero to an FPR denominator.
Unlike the scalar ``Fraction``/``SufficientStats`` constructors the batch path
re-validates nothing else (panel hygiene is upstream, load.py) — a
``count > nobs`` row that would raise scalar-side flows through as numbers here.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from abkit.stats.bootstrap.engine import BLOCK_QUANTUM, DEFAULT_MAX_BLOCK_BYTES
from abkit.stats.rng import derive_seed
from abkit.validate.panel import PanelCutoff
from abkit.validate.resample import MIN_ARM_UNITS, placebo_mask

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
IntArray = npt.NDArray[np.int64]

#: Working-set bytes per mask row per unit (the engine's honesty rule — count the
#: temporaries, engine.py:289-293): the bool mask row (1) + its present-slice
#: copy (1) + the float64 weights buffer the GEMM consumes (8). NOTE:
#: ``placebo_mask_block``'s construction phase has its OWN transient of the same
#: order — each ``placebo_mask`` call holds an int64 permutation (8) plus a bool
#: mask (1) per unit for one row at a time — which happens to fit the same
#: budget only because int64 == float64 width; if ``placebo_mask`` internals
#: ever change, re-check this coincidence (adversarial review round 1).
_ROW_TEMP_BYTES = 10


def placebo_mask_block(
    n_units: int,
    share_a: float,
    seed_parts: tuple[object, ...],
    block_start: int,
    block_size: int,
) -> BoolArray:
    """A ``(block_size × n_units)`` block of placebo masks — row ``i`` is EXACTLY
    ``placebo_mask(n_units, share_a, derive_seed(*seed_parts, block_start + i))``.

    The master identity contract of the vectorized engine: masks are produced by
    the unchanged scalar function per row, so the permutation layer is
    bit-identical to the scalar loop by construction, not by empirical test.
    """
    if block_start < 0:
        raise ValueError(f"block_start must be non-negative, got {block_start}")
    if block_size <= 0:
        raise ValueError(f"block_size must be positive, got {block_size}")
    block = np.empty((block_size, n_units), dtype=bool)
    for i in range(block_size):
        block[i] = placebo_mask(n_units, share_a, derive_seed(*seed_parts, block_start + i))
    return block


def block_rows(
    n_units: int,
    max_block_bytes: int | None = None,
    *,
    quantum: int = BLOCK_QUANTUM,
) -> int:
    """Mask rows per materialised block under the byte cap (engine.py arithmetic).

    Groups whole quanta while they fit (bootstrap parity); when even ONE quantum
    of per-unit temporaries exceeds the cap, shrinks below the quantum down to a
    one-row floor — safe here because mask rows are seed-independent (module
    docstring), so the block size can never change a result, only memory.
    """
    if n_units <= 0:
        raise ValueError(f"n_units must be positive, got {n_units}")
    if quantum <= 0:
        raise ValueError(f"quantum must be positive, got {quantum}")
    cap = DEFAULT_MAX_BLOCK_BYTES if max_block_bytes is None else int(max_block_bytes)
    bytes_per_row = n_units * _ROW_TEMP_BYTES
    bytes_per_quantum = quantum * bytes_per_row
    if cap >= bytes_per_quantum:
        return (cap // bytes_per_quantum) * quantum
    return max(1, cap // bytes_per_row)


def iter_blocks(iterations: int, quantum: int = BLOCK_QUANTUM) -> Iterator[tuple[int, int]]:
    """Yield ``(block_start, block_size)`` covering ``range(iterations)`` in order.

    The last block may be short. Size ``quantum`` with :func:`block_rows` so
    the per-cutoff temporaries stay under the byte cap.
    """
    if iterations <= 0:
        raise ValueError(f"iterations must be positive, got {iterations}")
    if quantum <= 0:
        raise ValueError(f"quantum must be positive, got {quantum}")
    produced = 0
    while produced < iterations:
        size = min(quantum, iterations - produced)
        yield (produced, size)
        produced += size


@dataclass(frozen=True)
class PreparedCutoff:
    """A cutoff's iteration-independent GEMM operands, hoistable out of the
    block loop (recomputing them per block would re-touch ``n_present``-sized
    columns once per block per cutoff — wasted bandwidth at large ``n_units``).

    ``shifts`` are the pooled per-cutoff means the moment columns were centered
    on; ``value_matrix`` is the stacked ``(n_present × k)`` column matrix each
    arm's weights contract against. Built by :func:`prepare_cutoff`; consuming
    a prepared cutoff is bit-identical to letting ``build_arm_batch`` build it
    inline (same arrays, same call shapes). ``cut`` records which cutoff the
    operands were built from — ``build_arm_batch`` asserts identity, so a
    wrong-loop-variable mixup can never silently score one cutoff's masks
    against another cutoff's values (adversarial review round 2; a shape
    check alone would miss equal-sized cutoffs).
    """

    shifts: tuple[float, ...]
    value_matrix: FloatArray
    cut: PanelCutoff


def prepare_cutoff(
    input_kind: str,
    cut: PanelCutoff,
    covariate: FloatArray | None,
) -> PreparedCutoff:
    """Precompute one cutoff's :class:`PreparedCutoff` (see its docstring)."""
    shifts, value_columns = _shifted_columns(input_kind, cut, covariate)
    return PreparedCutoff(shifts=shifts, value_matrix=np.stack(value_columns, axis=1), cut=cut)


@dataclass(frozen=True)
class ArmStatsBatch:
    """One arm's per-iteration sufficient-statistic columns over a mask block.

    ``columns`` is keyed to feed ``BaseMethod.from_suffstats_array`` directly
    (the M7 WP2 kernels), per the panel's ``input_kind``:

    - ``sample``:              ``n``, ``mean``, ``m2``
    - ``sample`` + covariate:  + ``cov_mean``, ``cov_m2``, ``cross_c``
    - ``fraction``:            ``count``, ``nobs``
    - ``ratio``:               ``n``, ``mean_num``, ``m2_num``, ``mean_den``,
      ``m2_den``, ``c_nd``

    ``degenerate`` marks rows where the scalar ``build_arm`` would return
    ``None`` — a gap, never a zero; those rows' columns are NaN. ``arm_sizes``
    is the raw per-row present-unit count (the ``MIN_ARM_UNITS`` gate input).
    ``degenerate`` is the BUILD-level gap signal only: a non-degenerate row can
    still carry inf/NaN moments from overflow-scale data, which surface as NaN
    CI bounds out of the WP2 kernels — a consumer must treat non-finite bounds
    as a gap exactly like the scalar path's ``_significance`` non-finite check
    (scoring.py:140-153), never as a non-rejection. ``frozen=True`` blocks
    attribute rebinding only — the arrays themselves are freshly-owned but
    mutable; consumers must not write into them (make a copy to transform,
    e.g. ``inject_multiplicative_columns`` returns new arrays).
    """

    columns: dict[str, FloatArray]
    degenerate: BoolArray
    arm_sizes: IntArray


def build_arm_batch(
    input_kind: str,
    cut: PanelCutoff,
    covariate: FloatArray | None,
    mask_block: BoolArray,
    *,
    weights_scratch: FloatArray | None = None,
    prepared: PreparedCutoff | None = None,
) -> tuple[ArmStatsBatch, ArmStatsBatch]:
    """Both arms' sufficient-statistic batches at one cutoff for a mask block.

    ``mask_block`` is ``(block × n_units)`` boolean arm-A membership over global
    units (rows from :func:`placebo_mask_block`); the MASK's present columns are
    sliced via ``cut.unit_idx`` while still boolean, BEFORE the float64 weights
    cast — never cast the full-width block (the module's memory-contract rule).
    Value/covariate arrays are normalized to float64 like the scalar
    constructors (``_as_float_array``). Returns ``(arm_a, arm_b)`` where arm A
    is the ``True`` side — the batch mirror of the scalar ``present_positions``
    + per-arm ``build_arm`` pair.

    ``weights_scratch`` is a pure perf knob for a scorer loop: a preallocated
    float64 buffer of at least ``(block × n_units)`` reused across cutoffs
    saves one large allocation + page-fault pass per cutoff (~25% of the
    aggregation wall-clock at the reference shape). Passing it changes the
    GEMM operand's stride, so results vs the non-scratch call may differ at
    the ULP level (the same cross-partition class the module contract already
    documents); a FIXED scratch shape keeps run-to-run byte-reproducibility.
    ``prepared`` (from :func:`prepare_cutoff`) hoists the cutoff's
    iteration-independent operands out of a block loop — bit-identical to the
    inline build.
    """
    if mask_block.ndim != 2 or mask_block.dtype != np.bool_:
        raise ValueError(
            f"mask_block must be a 2-D boolean array, got ndim={mask_block.ndim} "
            f"dtype={mask_block.dtype}"
        )
    if input_kind == "fraction" and cut.secondary is None:
        raise ValueError("fraction input_kind requires an nobs (trials) array")
    if input_kind == "ratio" and cut.secondary is None:
        raise ValueError("ratio input_kind requires a denominator array")

    block_size = mask_block.shape[0]
    n_present = int(cut.unit_idx.size)
    if n_present == 0:
        empty = _degenerate_batch(input_kind, block_size, covariate is not None)
        return empty, empty

    present = mask_block[:, cut.unit_idx]  # (block × n_present) bool, sliced pre-cast
    count_a = present.sum(axis=1, dtype=np.int64)
    count_b = n_present - count_a

    # The pooled per-cutoff shifts + shifted value columns (iteration-independent)
    # — see the module docstring's shifted-one-pass rationale.
    if prepared is None:
        prepared = prepare_cutoff(input_kind, cut, covariate)
    elif prepared.cut is not cut:
        raise ValueError(
            "prepared was built from a different PanelCutoff than the one passed — "
            "a mismatched hoist would silently score this cutoff's masks against "
            "another cutoff's values"
        )
    shifts, value_matrix = prepared.shifts, prepared.value_matrix

    # One GEMM per arm: every statistic of every iteration in the block in a
    # single BLAS call (the poisson-engine matmul precedent, engine.py:33).
    # Arm B's weights are the complement, rebuilt in place — never a second
    # (block × n_present) float64 buffer (the memory-contract line item).
    if weights_scratch is None:
        weights = np.empty(present.shape, dtype=np.float64)
    else:
        if (
            weights_scratch.ndim != 2
            or weights_scratch.dtype != np.float64
            or weights_scratch.shape[0] < mask_block.shape[0]
            or weights_scratch.shape[1] < n_present
        ):
            raise ValueError(
                f"weights_scratch must be a 2-D float64 buffer of at least "
                f"{(mask_block.shape[0], n_present)}, got "
                f"{weights_scratch.shape} {weights_scratch.dtype}"
            )
        weights = weights_scratch[: mask_block.shape[0], :n_present]
    np.copyto(weights, present)
    with np.errstate(over="ignore", invalid="ignore"):  # inf·0 inside the GEMM → NaN row
        sums_a = weights @ value_matrix  # (block × k)
        np.subtract(1.0, weights, out=weights)
        sums_b = weights @ value_matrix

    arm_a = _finish_arm(input_kind, shifts, count_a, tuple(sums_a.T))
    arm_b = _finish_arm(input_kind, shifts, count_b, tuple(sums_b.T))
    return arm_a, arm_b


def _shifted_columns(
    input_kind: str,
    cut: PanelCutoff,
    covariate: FloatArray | None,
) -> tuple[tuple[float, ...], tuple[FloatArray, ...]]:
    """The pooled shifts + per-unit columns the arm GEMMs contract against.

    Moment columns are centered on the cutoff's POOLED mean (fixed across
    iterations); products use ``np.square``/``*`` (exact multiplies — no ``**``,
    so the WP2 libm-pow ULP hazard never enters this layer). Fraction sums are
    plain non-negative totals — no centering, matching the scalar ``.sum()``.
    ``errstate`` mirrors the WP2 kernels: ~1e150-scale values overflow
    ``np.square`` to inf without warnings-as-errors noise; the inf/NaN then
    flows to NaN CI bounds downstream — the same gap the scalar path reaches
    via ``_significance``'s non-finite check (adversarial review round 1).
    """
    # float64 normalization mirrors the scalar constructors' _as_float_array
    # (samples.py:30): a float32 panel would otherwise stay float32 through the
    # centering under NEP-50 promotion and break the rel-1e-9 parity gate at
    # ORDINARY offsets (adversarial review round 2). No-copy when already f64.
    values = np.asarray(cut.values, dtype=np.float64)
    secondary = None if cut.secondary is None else np.asarray(cut.secondary, dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        if input_kind == "fraction":
            assert secondary is not None  # validated by the caller
            return (), (values, secondary)
        if input_kind == "ratio":
            assert secondary is not None  # validated by the caller
            shift_num = float(np.mean(values))
            shift_den = float(np.mean(secondary))
            centered_num = values - shift_num
            centered_den = secondary - shift_den
            return (shift_num, shift_den), (
                centered_num,
                np.square(centered_num),
                centered_den,
                np.square(centered_den),
                centered_num * centered_den,
            )
        shift_y = float(np.mean(values))
        centered_y = values - shift_y
        if covariate is None:
            return (shift_y,), (centered_y, np.square(centered_y))
        covariate_present = np.asarray(covariate[cut.unit_idx], dtype=np.float64)
        shift_x = float(np.mean(covariate_present))
        centered_x = covariate_present - shift_x
        return (shift_y, shift_x), (
            centered_y,
            np.square(centered_y),
            centered_x,
            np.square(centered_x),
            centered_y * centered_x,
        )


def _finish_arm(
    input_kind: str,
    shifts: tuple[float, ...],
    count: IntArray,
    sums: tuple[FloatArray, ...],
) -> ArmStatsBatch:
    """Assemble one arm's suffstats columns from its weighted sums + unit count."""
    degenerate = count < MIN_ARM_UNITS
    count_f = count.astype(np.float64)

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        if input_kind == "fraction":
            successes, nobs = sums
            # a zero-trial arm is degenerate — a gap, not a crash (build_arm parity);
            # contiguous copies so no column aliases the shared GEMM output buffer
            degenerate = degenerate | (nobs <= 0)
            columns = {
                "count": np.ascontiguousarray(successes),
                "nobs": np.ascontiguousarray(nobs),
            }
        elif input_kind == "ratio":
            shift_num, shift_den = shifts
            s_num, s_num2, s_den, s_den2, s_nd = sums
            columns = {
                "n": count_f,
                "mean_num": shift_num + s_num / count_f,
                "m2_num": np.maximum(s_num2 - s_num * s_num / count_f, 0.0),
                "mean_den": shift_den + s_den / count_f,
                "m2_den": np.maximum(s_den2 - s_den * s_den / count_f, 0.0),
                "c_nd": s_nd - s_num * s_den / count_f,
            }
        elif len(shifts) == 1:  # sample without covariate
            (shift_y,) = shifts
            s_y, s_y2 = sums
            columns = {
                "n": count_f,
                "mean": shift_y + s_y / count_f,
                "m2": np.maximum(s_y2 - s_y * s_y / count_f, 0.0),
            }
        else:  # sample with covariate (CUPED)
            shift_y, shift_x = shifts
            s_y, s_y2, s_x, s_x2, s_yx = sums
            columns = {
                "n": count_f,
                "mean": shift_y + s_y / count_f,
                "m2": np.maximum(s_y2 - s_y * s_y / count_f, 0.0),
                "cov_mean": shift_x + s_x / count_f,
                "cov_m2": np.maximum(s_x2 - s_x * s_x / count_f, 0.0),
                "cross_c": s_yx - s_y * s_x / count_f,
            }

    if degenerate.any():
        for column in columns.values():
            column[degenerate] = np.nan  # gaps, never zeros (scoring.py:26-27)
    return ArmStatsBatch(columns=columns, degenerate=degenerate, arm_sizes=count)


def _degenerate_batch(input_kind: str, block_size: int, has_covariate: bool) -> ArmStatsBatch:
    """An all-degenerate batch for a cutoff with no present units."""
    if input_kind == "fraction":
        keys: tuple[str, ...] = ("count", "nobs")
    elif input_kind == "ratio":
        keys = ("n", "mean_num", "m2_num", "mean_den", "m2_den", "c_nd")
    elif has_covariate:
        keys = ("n", "mean", "m2", "cov_mean", "cov_m2", "cross_c")
    else:
        keys = ("n", "mean", "m2")
    return ArmStatsBatch(
        columns={key: np.full(block_size, np.nan) for key in keys},
        degenerate=np.ones(block_size, dtype=bool),
        arm_sizes=np.zeros(block_size, dtype=np.int64),
    )
