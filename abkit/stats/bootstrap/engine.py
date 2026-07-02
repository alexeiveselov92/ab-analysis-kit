"""Vectorised bootstrap resampling engines (baseline §4.1/§4.4; hygiene H1/H6/H10).

DRAW-ORDER CONTRACT (binding; regression-tested)
================================================

All randomness flows from ONE ``np.random.Generator`` (H1) built by the calling
method via :func:`abkit.stats.rng.make_rng` once per compare call. Replicates
are ALWAYS drawn in fixed quanta of :data:`BLOCK_QUANTUM` (= 128) replicates
from that single stream, sequentially from replicate 0 to ``n_samples`` (the
last quantum may be short). The memory cap (``max_block_bytes``, default
:data:`DEFAULT_MAX_BLOCK_BYTES`) only controls how many quanta of VALUE
matrices are materialised at once — it must NEVER affect the random stream, so
any ``max_block_bytes`` yields byte-identical results.

Index engine (:func:`bootstrap_statistics`), per variant:

- Strata are visited in ``np.unique`` order (unstratified = one stratum = the
  whole array). Within a quantum, for each stratum the draw is exactly
  ``indices = rng.integers(0, len(stratum_values), size=(quantum_rows,
  stratum_size))`` — see :func:`draw_stratum_indices`, kept trivial so the
  golden-reference transcription can share the identical stream — and
  ``values_block[:, offset:offset + stratum_size] = stratum_values[indices]``.
- With a covariate channel (``cov_bootstrap``, baseline §4.1) the SAME indices
  are applied to the covariate values — no extra draws — preserving the
  per-unit (Y, X) pairing.
- Variant 1 is drawn fully (all quanta) before variant 2. Paired variants
  instead share ONE set of index/weight draws by passing both arms as aligned
  channels of a single engine call.

Poisson engine (:func:`poisson_bootstrap_means`, baseline §4.4): per quantum
``weights = rng.poisson(1.0, (quantum_rows, n_units))``; the replicate value is
``(weights_scaled @ values) / weights_scaled.sum(axis=1)`` where
``weights_scaled`` applies the per-unit post-stratification scale
(1 / count-of-its-category, :func:`poisson_unit_scale`) when stratified. Its
working set is a single quantum of weights regardless of the cap (already
minimal), and the fixed per-quantum matmul shape keeps the produced bytes
cap-invariant, so ``max_block_bytes`` is accepted and ignored here.

Byte-identity under any cap holds by construction: draws happen per quantum
and statistic application is row-wise (each replicate reduced independently),
so block grouping cannot change any replicate's value.

Stratification (H6): pooled per-stratum shares (baseline §4.2 ``weight_method``
min/mean) are apportioned with the largest-remainder (Hamilton) method
(:func:`hamilton_apportion`) so per-stratum resample counts sum EXACTLY to each
variant's ``n`` — replacing the legacy ``max(1, int(...))`` truncation drift.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from abkit.stats.bootstrap.applier import StatFunc, apply_stat
from abkit.stats.exceptions import MethodParamError, SampleValidationError
from abkit.stats.samples import FloatArray, Sample

IntArray = npt.NDArray[np.int64]
CategoryArray = npt.NDArray[np.generic]

#: Fixed replicate quantum of the random stream (see the draw-order contract).
BLOCK_QUANTUM = 128
#: Default memory cap for materialised value matrices (H10): 256 MiB.
DEFAULT_MAX_BLOCK_BYTES = 256 * 1024 * 1024
#: float64 value matrices / int64 Poisson weight matrices.
_ITEM_BYTES = 8


def draw_stratum_indices(
    rng: np.random.Generator, n_units: int, rows: int, stratum_size: int
) -> IntArray:
    """The single index draw of the contract: ``rng.integers(0, n_units, (rows, size))``.

    Deliberately trivial (one ``rng.integers`` call) so the golden-reference
    transcription of the legacy engine can share the identical random stream.
    """
    return rng.integers(0, n_units, size=(rows, stratum_size))


def hamilton_apportion(shares: FloatArray, total: int) -> IntArray:
    """Largest-remainder (Hamilton) apportionment of ``total`` over ``shares`` (H6).

    Per-stratum counts sum EXACTLY to ``total``. Ties on fractional remainders
    break deterministically toward the earlier stratum (``np.unique`` order,
    stable sort). After apportionment any zero-count stratum is bumped to 1,
    taking one unit from the (current) largest stratum, keeping the total exact
    — feasible because ``total >= len(shares)`` is required.
    """
    share_array = np.asarray(shares, dtype=np.float64)
    if share_array.ndim != 1 or share_array.size == 0:
        raise SampleValidationError("hamilton_apportion: shares must be a non-empty 1-D array")
    if not np.all(np.isfinite(share_array)) or np.any(share_array < 0.0):
        raise SampleValidationError("hamilton_apportion: shares must be finite and non-negative")
    share_sum = float(share_array.sum())
    if share_sum <= 0.0:
        raise SampleValidationError("hamilton_apportion: shares must sum to a positive value")
    total = int(total)
    if total < share_array.size:
        raise SampleValidationError(
            f"hamilton_apportion: cannot give every stratum at least one unit "
            f"(total={total} < strata={share_array.size})"
        )

    quotas = share_array / share_sum * total
    counts = np.floor(quotas).astype(np.int64)
    remainders = quotas - counts
    missing = total - int(counts.sum())
    while missing < 0:  # float-noise overshoot only; deterministic trim from the largest
        donor = int(np.argmax(counts))
        counts[donor] -= 1
        missing += 1
    if missing > 0:
        order = np.argsort(-remainders, kind="stable")
        counts[order[:missing]] += 1
    for index in np.flatnonzero(counts == 0):
        donor = int(np.argmax(counts))
        counts[donor] -= 1
        counts[index] = 1
    return counts


def require_common_categories(
    method_name: str, sample_1: Sample, sample_2: Sample
) -> CategoryArray:
    """Validate stratification inputs: categories on both arms, identical SETS (H6).

    Returns the shared categories in ``np.unique`` (sorted) order — the stratum
    visiting order of the draw contract.
    """
    for label, sample in (("first", sample_1), ("second", sample_2)):
        if sample.categories_array is None:
            raise SampleValidationError(
                f"{method_name}: stratify=True requires categories_array on the {label} sample"
            )
    assert sample_1.categories_array is not None and sample_2.categories_array is not None
    categories_1 = np.unique(sample_1.categories_array)
    categories_2 = np.unique(sample_2.categories_array)
    if categories_1.shape != categories_2.shape or not np.array_equal(categories_1, categories_2):
        raise SampleValidationError(
            f"{method_name}: the two variants must have identical stratum category sets; "
            f"got {list(categories_1)} vs {list(categories_2)}"
        )
    return categories_1


def pooled_stratum_shares(
    sample_1: Sample, sample_2: Sample, weight_method: str, method_name: str
) -> tuple[CategoryArray, FloatArray]:
    """Pool per-variant stratum shares across variants (baseline §4.2, H6).

    Per-variant shares are ``counts / n`` over the (validated-identical) category
    set; pooled elementwise by ``weight_method`` ("min" or "mean") and normalised
    to sum 1. Both variants then resample to this common stratum mix
    (poststratification by design).
    """
    categories = require_common_categories(method_name, sample_1, sample_2)
    assert sample_1.categories_array is not None and sample_2.categories_array is not None
    _, counts_1 = np.unique(sample_1.categories_array, return_counts=True)
    _, counts_2 = np.unique(sample_2.categories_array, return_counts=True)
    shares_1 = counts_1 / sample_1.sample_size
    shares_2 = counts_2 / sample_2.sample_size
    if weight_method == "min":
        pooled = np.minimum(shares_1, shares_2)
    elif weight_method == "mean":
        pooled = (shares_1 + shares_2) / 2.0
    else:
        raise MethodParamError(
            f"{method_name}: weight_method must be 'min' or 'mean', got {weight_method!r}"
        )
    return categories, pooled / pooled.sum()


def poisson_unit_scale(categories_array: CategoryArray) -> FloatArray:
    """Per-unit Poisson post-stratification scale: 1 / count-of-its-category.

    Legacy semantics (catalogue §4.4) vectorised via ``np.unique`` inverse
    indices instead of the legacy per-column Python loop.
    """
    _, inverse, counts = np.unique(categories_array, return_inverse=True, return_counts=True)
    return 1.0 / counts[inverse]


@dataclass(frozen=True)
class ResamplePlan:
    """Per-variant resampling layout: strata (``np.unique`` order) and resample counts.

    ``strata_positions[s]`` are the unit positions of stratum ``s`` in the
    variant's arrays; ``resample_counts[s]`` is how many units that stratum
    contributes per replicate (Hamilton-apportioned, summing exactly to the
    resample width). Unstratified = one stratum spanning the whole array.
    """

    strata_positions: tuple[IntArray, ...]
    resample_counts: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.strata_positions) != len(self.resample_counts):
            raise SampleValidationError("ResamplePlan: strata_positions/resample_counts mismatch")
        if not self.strata_positions:
            raise SampleValidationError("ResamplePlan: at least one stratum is required")
        for positions, count in zip(self.strata_positions, self.resample_counts, strict=True):
            if positions.size == 0:
                raise SampleValidationError("ResamplePlan: empty stratum")
            if count < 1:
                raise SampleValidationError("ResamplePlan: resample counts must be >= 1")

    @property
    def total_width(self) -> int:
        """Columns per replicate row (== the variant's ``n`` after apportionment)."""
        return int(sum(self.resample_counts))


def unstratified_plan(n_units: int) -> ResamplePlan:
    """One stratum = the whole array; resample width = ``n_units`` (baseline §4.1)."""
    if n_units < 1:
        raise SampleValidationError("unstratified_plan: n_units must be >= 1")
    return ResamplePlan(
        strata_positions=(np.arange(n_units, dtype=np.int64),),
        resample_counts=(int(n_units),),
    )


def stratified_plan(
    categories_array: CategoryArray, categories: CategoryArray, shares: FloatArray
) -> ResamplePlan:
    """Build one variant's plan from pooled shares (Hamilton-apportioned to its ``n``)."""
    counts = hamilton_apportion(shares, int(categories_array.size))
    positions = tuple(np.flatnonzero(categories_array == category) for category in categories)
    return ResamplePlan(
        strata_positions=positions,
        resample_counts=tuple(int(count) for count in counts),
    )


def _validate_channels(channels: Sequence[FloatArray], n_units: int) -> None:
    if not channels:
        raise SampleValidationError("resampling requires at least one channel")
    for channel in channels:
        if channel.ndim != 1 or channel.size != n_units:
            raise SampleValidationError(
                "all resampling channels must be 1-D and aligned to the same units"
            )


def iter_resample_blocks(
    rng: np.random.Generator,
    channels: Sequence[FloatArray],
    plan: ResamplePlan,
    n_samples: int,
    max_block_bytes: int | None,
) -> Iterator[tuple[FloatArray, ...]]:
    """Yield resampled value matrices per channel, in blocks of whole quanta.

    Implements the module draw-order contract: indices are drawn per quantum
    (per stratum, in plan order) from the single ``rng`` stream; the cap only
    sizes the materialised block (``>= 1`` quantum always), never the stream.
    All channels of one stratum reuse the SAME index matrix (covariate pairing,
    baseline §4.1).
    """
    if n_samples < 1:
        raise MethodParamError(f"n_samples must be >= 1, got {n_samples}")
    n_units = int(channels[0].size)
    _validate_channels(channels, n_units)

    stratum_values = [
        tuple(channel[positions] for channel in channels) for positions in plan.strata_positions
    ]
    width = plan.total_width
    bytes_per_quantum = BLOCK_QUANTUM * width * _ITEM_BYTES * len(channels)
    cap = DEFAULT_MAX_BLOCK_BYTES if max_block_bytes is None else int(max_block_bytes)
    quanta_per_block = max(1, cap // max(1, bytes_per_quantum))

    produced = 0
    while produced < n_samples:
        block_rows = min(n_samples - produced, quanta_per_block * BLOCK_QUANTUM)
        blocks = tuple(np.empty((block_rows, width), dtype=np.float64) for _ in channels)
        row = 0
        while row < block_rows:
            quantum_rows = min(BLOCK_QUANTUM, block_rows - row)
            offset = 0
            for positions, count, values_by_channel in zip(
                plan.strata_positions, plan.resample_counts, stratum_values, strict=True
            ):
                indices = draw_stratum_indices(rng, int(positions.size), quantum_rows, count)
                for block, values in zip(blocks, values_by_channel, strict=True):
                    block[row : row + quantum_rows, offset : offset + count] = values[indices]
                offset += count
            row += quantum_rows
        produced += block_rows
        yield blocks


def bootstrap_statistics(
    rng: np.random.Generator,
    channels: Sequence[FloatArray],
    plan: ResamplePlan,
    n_samples: int,
    stat: str | StatFunc,
    max_block_bytes: int | None,
) -> tuple[FloatArray, ...]:
    """Index-engine bootstrap: one length-``n_samples`` statistic vector per channel.

    Streams :func:`iter_resample_blocks` and applies ``stat`` row-wise per block
    (H3 fast path in :func:`abkit.stats.bootstrap.applier.apply_stat`).
    """
    outputs = tuple(np.empty(n_samples, dtype=np.float64) for _ in channels)
    produced = 0
    for blocks in iter_resample_blocks(rng, channels, plan, n_samples, max_block_bytes):
        rows = blocks[0].shape[0]
        for output, block in zip(outputs, blocks, strict=True):
            output[produced : produced + rows] = apply_stat(block, stat)
        produced += rows
    return outputs


def poisson_bootstrap_means(
    rng: np.random.Generator,
    arrays: Sequence[FloatArray],
    unit_scale: FloatArray | None,
    n_samples: int,
    max_block_bytes: int | None = None,
) -> tuple[FloatArray, ...]:
    """Poisson-weighted bootstrap means (baseline §4.4), one vector per array.

    Per quantum: ``weights = rng.poisson(1.0, (quantum_rows, n_units))``;
    replicate value = ``(weights_scaled @ array) / weights_scaled.sum(axis=1)``.
    All arrays share the SAME weights per quantum (paired variants pass both
    arms). Zero weight sums produce non-finite replicates (kept; handled under
    H5 downstream). ``max_block_bytes`` is accepted and ignored — see the module
    docstring (per-quantum working set is already minimal and cap-invariant).
    """
    del max_block_bytes  # cap-invariant by construction (module docstring)
    if n_samples < 1:
        raise MethodParamError(f"n_samples must be >= 1, got {n_samples}")
    n_units = int(arrays[0].size)
    _validate_channels(arrays, n_units)
    if unit_scale is not None and unit_scale.size != n_units:
        raise SampleValidationError("unit_scale must be aligned to the arrays")

    outputs = tuple(np.empty(n_samples, dtype=np.float64) for _ in arrays)
    produced = 0
    while produced < n_samples:
        quantum_rows = min(BLOCK_QUANTUM, n_samples - produced)
        weights = rng.poisson(1.0, size=(quantum_rows, n_units))
        weights_scaled = weights if unit_scale is None else weights * unit_scale
        weight_sums = weights_scaled.sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            for output, array in zip(outputs, arrays, strict=True):
                output[produced : produced + quantum_rows] = (weights_scaled @ array) / weight_sums
        produced += quantum_rows
    return outputs
