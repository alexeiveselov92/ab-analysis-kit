"""Data containers for the statistical core: raw samples and sufficient statistics.

Baseline fact #1 (docs/specs/statistics-baseline.md §1): the legacy variance
convention is MIXED, not uniform. ``np.var``/``np.std`` terms use ``ddof=0``
(population) while ``np.cov`` terms (CUPED θ, the paired/CUPED covariance
corrections) use numpy's default ``ddof=1``. The sufficient-statistics classes
here store *raw centered co-moments* and expose the exact per-term convention
through explicit accessors (``var`` → ddof=0, ``cov1_*`` → ddof=1). A blanket-ddof
rewrite is forbidden — it fails every CUPED/paired golden test.

Accumulation stability (docs/specs/statistics-changes.md §1.1): moments are built
two-pass (via numpy's stable routines) and merged Welford/Chan-style in
``accumulate.py`` — never as ``Σx²/n − x̄²``.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from abkit.stats.exceptions import AbkitStatsWarning, SampleValidationError

FloatArray = npt.NDArray[np.float64]


def _as_float_array(values: object, *, what: str) -> FloatArray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise SampleValidationError(f"{what} must be one-dimensional, got shape {array.shape}")
    if array.size == 0:
        raise SampleValidationError(f"{what} must not be empty")
    return array


class Sample:
    """Per-unit observations for ONE variant (the legacy ``Sample`` model).

    - ``array`` — the metric value per randomisation unit.
    - ``cov_array`` — optional covariate per unit (CUPED / post-normalisation),
      row-aligned with ``array``.
    - ``categories_array`` — optional stratum label per unit.
    - ``pair_ids`` — optional pair key per unit (paired designs; see
      :func:`align_paired`).

    Derived statistics are eager and follow the baseline exactly:
    ``std``/``var`` are population (``ddof=0``); ``corr_coef`` is
    ``np.corrcoef(array, cov_array)[0, 1]``.
    """

    def __init__(
        self,
        array: Sequence[float] | FloatArray,
        cov_array: Sequence[float] | FloatArray | None = None,
        categories_array: Sequence[object] | npt.NDArray[np.generic] | None = None,
        pair_ids: Sequence[object] | npt.NDArray[np.generic] | None = None,
        name: str | None = None,
    ) -> None:
        self.array = _as_float_array(array, what="Sample.array")
        self.name = name
        self.sample_size = int(self.array.size)

        self.mean = float(np.mean(self.array))
        self.std = float(np.std(self.array))  # ddof=0 (baseline)
        self.var = self.std**2

        self.cov_array: FloatArray | None = None
        self.cov_mean: float | None = None
        self.cov_std: float | None = None
        self.cov_var: float | None = None
        self.corr_coef: float | None = None
        if cov_array is not None:
            cov = _as_float_array(cov_array, what="Sample.cov_array")
            if cov.size != self.sample_size:
                raise SampleValidationError(
                    f"cov_array length {cov.size} != array length {self.sample_size}"
                )
            self.cov_array = cov
            self.cov_mean = float(np.mean(cov))
            self.cov_std = float(np.std(cov))  # ddof=0 (baseline)
            self.cov_var = self.cov_std**2
            self.corr_coef = float(np.corrcoef(self.array, cov)[0, 1])

        self.categories_array: npt.NDArray[np.generic] | None = None
        if categories_array is not None:
            categories = np.asarray(categories_array)
            if categories.ndim != 1 or categories.size != self.sample_size:
                raise SampleValidationError(
                    "categories_array must be one-dimensional and aligned with array"
                )
            self.categories_array = categories

        self.pair_ids: npt.NDArray[np.generic] | None = None
        if pair_ids is not None:
            ids = np.asarray(pair_ids)
            if ids.ndim != 1 or ids.size != self.sample_size:
                raise SampleValidationError(
                    "pair_ids must be one-dimensional and aligned with array"
                )
            if np.unique(ids).size != ids.size:
                raise SampleValidationError("pair_ids must be unique within a variant")
            self.pair_ids = ids

    def category_counts(self) -> dict[object, int]:
        """Observed per-stratum unit counts (empty dict when unstratified)."""
        if self.categories_array is None:
            return {}
        values, counts = np.unique(self.categories_array, return_counts=True)
        return {
            value.item() if hasattr(value, "item") else value: int(count)
            for value, count in zip(values, counts, strict=True)
        }

    def __repr__(self) -> str:
        return (
            f"Sample(name={self.name!r}, n={self.sample_size}, mean={self.mean:.6g}, "
            f"std={self.std:.6g}, covariate={self.cov_array is not None})"
        )


class Fraction:
    """A proportion metric for ONE variant: ``count`` successes of ``nobs`` trials.

    ``count``/``nobs`` ARE the sufficient statistics for the two-proportion z-test,
    so this class serves both the ``from_samples`` and ``from_suffstats`` entries.
    ``std`` matches the legacy ``TestResult`` per-arm column: the standard error of
    the proportion, ``sqrt(p·(1−p)/nobs)``.
    """

    def __init__(self, count: float, nobs: float, name: str | None = None) -> None:
        if nobs <= 0:
            raise SampleValidationError(f"Fraction.nobs must be positive, got {nobs}")
        if count < 0 or count > nobs:
            raise SampleValidationError(
                f"Fraction.count must be within [0, nobs], got {count}/{nobs}"
            )
        self.count = float(count)
        self.nobs = float(nobs)
        self.name = name
        self.prop = self.count / self.nobs
        self.std = float(np.sqrt(self.prop * (1.0 - self.prop) / self.nobs))
        self.sample_size = int(self.nobs)

    def __repr__(self) -> str:
        return f"Fraction(name={self.name!r}, count={self.count:g}, nobs={self.nobs:g}, prop={self.prop:.6g})"


class RatioSample:
    """Per-unit (numerator, denominator) observations for a ratio metric.

    Used by the principled ``ratio-delta`` method (docs/specs/statistics-changes.md
    §4): the estimand is ``R = mean(numerator) / mean(denominator)`` per variant.
    """

    def __init__(
        self,
        numerator: Sequence[float] | FloatArray,
        denominator: Sequence[float] | FloatArray,
        name: str | None = None,
    ) -> None:
        self.numerator = _as_float_array(numerator, what="RatioSample.numerator")
        self.denominator = _as_float_array(denominator, what="RatioSample.denominator")
        if self.numerator.size != self.denominator.size:
            raise SampleValidationError(
                f"numerator length {self.numerator.size} != denominator length {self.denominator.size}"
            )
        self.name = name
        self.sample_size = int(self.numerator.size)

    def __repr__(self) -> str:
        return f"RatioSample(name={self.name!r}, n={self.sample_size})"


@dataclass(frozen=True)
class JointMoments:
    """Centered joint second moments of ``k`` aligned per-unit series.

    ``comoment[i, j] = Σ_u (z_i,u − mean_i)(z_j,u − mean_j)`` (raw, un-normalised).
    The mixed-ddof convention is applied at read time: :meth:`var0` divides by
    ``n`` (``np.var`` parity) and :meth:`cov1` by ``n − 1`` (``np.cov`` parity).
    Linear combinations (paired differences, CUPED adjustment) are exact:
    ``comoment(w·z, v·z) = w · C · vᵀ``.
    """

    n: int
    mean: FloatArray
    comoment: FloatArray
    labels: tuple[str, ...] = field(default=())

    @classmethod
    def from_arrays(cls, *arrays: FloatArray, labels: tuple[str, ...] = ()) -> JointMoments:
        if not arrays:
            raise SampleValidationError("JointMoments requires at least one array")
        stacked = np.vstack([np.asarray(a, dtype=np.float64) for a in arrays])
        n = stacked.shape[1]
        if any(np.asarray(a).size != n for a in arrays):
            raise SampleValidationError("JointMoments arrays must be equal-length and aligned")
        mean = stacked.mean(axis=1)
        centered = stacked - mean[:, None]
        comoment = centered @ centered.T
        return cls(
            n=int(n),
            mean=mean,
            comoment=comoment,
            labels=labels or tuple(f"z{i}" for i in range(len(arrays))),
        )

    def index(self, label: str) -> int:
        try:
            return self.labels.index(label)
        except ValueError:
            raise KeyError(f"unknown series label {label!r}; have {self.labels}") from None

    def var0(self, i: int) -> float:
        """Population variance (``np.var`` parity, ddof=0) of series ``i``."""
        return float(self.comoment[i, i] / self.n)

    def cov1(self, i: int, j: int) -> float:
        """Sample covariance (``np.cov`` parity, ddof=1) of series ``i`` and ``j``."""
        if self.n < 2:
            raise SampleValidationError("cov1 requires at least two units (ddof=1)")
        return float(self.comoment[i, j] / (self.n - 1))

    def linear_mean(self, weights: FloatArray) -> float:
        """Mean of the per-unit linear combination ``w · z``."""
        return float(np.dot(weights, self.mean))

    def linear_comoment(self, weights_a: FloatArray, weights_b: FloatArray) -> float:
        """Raw centered co-moment between two linear combinations of the series."""
        return float(weights_a @ self.comoment @ weights_b)

    def linear_var0(self, weights: FloatArray) -> float:
        return float(self.linear_comoment(weights, weights) / self.n)

    def linear_cov1(self, weights_a: FloatArray, weights_b: FloatArray) -> float:
        if self.n < 2:
            raise SampleValidationError("linear_cov1 requires at least two units (ddof=1)")
        return float(self.linear_comoment(weights_a, weights_b) / (self.n - 1))


class SufficientStats:
    """Sufficient statistics of ONE variant for the closed-form methods.

    Stores ``n``, ``mean`` and the raw centered second moment ``m2 = Σ(y−ȳ)²``,
    plus (optionally) covariate moments ``cov_mean``/``cov_m2`` and the raw
    Y↔X co-moment ``cross_c = Σ(y−ȳ)(x−x̄)``. Every quantity the t-test/CUPED
    family needs is derivable from these six numbers per variant
    (docs/specs/cumulative-intervals.md §3), preserving the exact mixed-ddof
    convention:

    - ``var`` (== ``np.var(y)``, ddof=0) = ``m2 / n``
    - ``cov1_value_covariate`` (== ``np.cov(y, x)[0, 1]``, ddof=1) = ``cross_c / (n−1)``
    """

    def __init__(
        self,
        n: int,
        mean: float,
        m2: float,
        cov_mean: float | None = None,
        cov_m2: float | None = None,
        cross_c: float | None = None,
        name: str | None = None,
    ) -> None:
        if n <= 0:
            raise SampleValidationError(f"SufficientStats.n must be positive, got {n}")
        if m2 < 0:
            raise SampleValidationError(f"SufficientStats.m2 must be non-negative, got {m2}")
        covariate_fields = (cov_mean, cov_m2, cross_c)
        if any(value is not None for value in covariate_fields) and any(
            value is None for value in covariate_fields
        ):
            raise SampleValidationError(
                "covariate moments must be provided together: cov_mean, cov_m2, cross_c"
            )
        self.n = int(n)
        self.mean = float(mean)
        self.m2 = float(m2)
        self.cov_mean = None if cov_mean is None else float(cov_mean)
        self.cov_m2 = None if cov_m2 is None else float(cov_m2)
        self.cross_c = None if cross_c is None else float(cross_c)
        self.name = name

    @classmethod
    def from_sample(cls, sample: Sample) -> SufficientStats:
        """Build from raw arrays via numpy's stable two-pass routines."""
        n = sample.sample_size
        if sample.cov_array is None:
            return cls(n=n, mean=sample.mean, m2=float(np.var(sample.array) * n), name=sample.name)
        centered_y = sample.array - sample.mean
        centered_x = sample.cov_array - float(np.mean(sample.cov_array))
        return cls(
            n=n,
            mean=sample.mean,
            m2=float(np.dot(centered_y, centered_y)),
            cov_mean=float(np.mean(sample.cov_array)),
            cov_m2=float(np.dot(centered_x, centered_x)),
            cross_c=float(np.dot(centered_y, centered_x)),
            name=sample.name,
        )

    # -- value moments -------------------------------------------------------
    @property
    def sample_size(self) -> int:
        return self.n

    @property
    def var(self) -> float:
        """Population variance of Y (``np.var`` parity, ddof=0)."""
        return self.m2 / self.n

    @property
    def std(self) -> float:
        return float(np.sqrt(self.var))

    # -- covariate moments (mixed ddof — see module docstring) ----------------
    @property
    def has_covariate(self) -> bool:
        return self.cov_mean is not None

    def _require_covariate(self) -> None:
        if not self.has_covariate:
            raise SampleValidationError("this operation requires covariate moments (cov_array)")

    @property
    def cov_var(self) -> float:
        """Population variance of X (``np.var`` parity, ddof=0)."""
        self._require_covariate()
        assert self.cov_m2 is not None
        return self.cov_m2 / self.n

    @property
    def cov_std(self) -> float:
        return float(np.sqrt(self.cov_var))

    @property
    def cov1_value_covariate(self) -> float:
        """``np.cov(y, x)[0, 1]`` parity (ddof=1) — the CUPED θ numerator term."""
        self._require_covariate()
        assert self.cross_c is not None
        if self.n < 2:
            raise SampleValidationError("cov1 requires at least two units (ddof=1)")
        return self.cross_c / (self.n - 1)

    @property
    def corr_coef(self) -> float:
        """``np.corrcoef(y, x)[0, 1]`` parity (scale-free — ddof cancels)."""
        self._require_covariate()
        assert self.cov_m2 is not None and self.cross_c is not None
        denominator = float(np.sqrt(self.m2 * self.cov_m2))
        if denominator == 0.0:
            return float("nan")
        return self.cross_c / denominator

    def __repr__(self) -> str:
        return (
            f"SufficientStats(name={self.name!r}, n={self.n}, mean={self.mean:.6g}, "
            f"var={self.var:.6g}, covariate={self.has_covariate})"
        )


class RatioSufficientStats:
    """Sufficient statistics of ONE variant for the ``ratio-delta`` method.

    ``{n, mean_num, m2_num, mean_den, m2_den, c_nd}`` where ``m2_*`` are raw
    centered second moments and ``c_nd = Σ(n_u−n̄)(d_u−d̄)``. ``ratio-delta`` is a
    NEW method (no legacy baseline), so its variance terms use ``ddof=0``
    uniformly — documented in docs/specs/statistics-changes.md §4.
    """

    def __init__(
        self,
        n: int,
        mean_num: float,
        m2_num: float,
        mean_den: float,
        m2_den: float,
        c_nd: float,
        name: str | None = None,
    ) -> None:
        if n <= 0:
            raise SampleValidationError(f"RatioSufficientStats.n must be positive, got {n}")
        self.n = int(n)
        self.mean_num = float(mean_num)
        self.m2_num = float(m2_num)
        self.mean_den = float(mean_den)
        self.m2_den = float(m2_den)
        self.c_nd = float(c_nd)
        self.name = name

    @classmethod
    def from_ratio_sample(cls, sample: RatioSample) -> RatioSufficientStats:
        centered_num = sample.numerator - float(np.mean(sample.numerator))
        centered_den = sample.denominator - float(np.mean(sample.denominator))
        return cls(
            n=sample.sample_size,
            mean_num=float(np.mean(sample.numerator)),
            m2_num=float(np.dot(centered_num, centered_num)),
            mean_den=float(np.mean(sample.denominator)),
            m2_den=float(np.dot(centered_den, centered_den)),
            c_nd=float(np.dot(centered_num, centered_den)),
            name=sample.name,
        )

    @property
    def sample_size(self) -> int:
        return self.n

    @property
    def ratio(self) -> float:
        """The per-variant estimand ``R = mean(numerator) / mean(denominator)``."""
        return self.mean_num / self.mean_den

    def __repr__(self) -> str:
        return f"RatioSufficientStats(name={self.name!r}, n={self.n})"


#: Series labels for paired sufficient statistics, in canonical order.
PAIRED_LABELS = ("y1", "y2")
PAIRED_CUPED_LABELS = ("y1", "y2", "x1", "x2")


class PairedSufficientStats:
    """Joint per-pair moments of two aligned variants (paired designs).

    Wraps a :class:`JointMoments` over ``(y1, y2)`` — or ``(y1, y2, x1, x2)`` when
    both variants carry a covariate — so the paired t-test / paired CUPED
    formulas (variance of differences, θ on differences, the negative covariance
    term against the control arm) are exact linear-combination reads.
    """

    def __init__(
        self,
        moments: JointMoments,
        name_1: str | None = None,
        name_2: str | None = None,
    ) -> None:
        if moments.labels not in (PAIRED_LABELS, PAIRED_CUPED_LABELS):
            raise SampleValidationError(
                f"PairedSufficientStats requires labels {PAIRED_LABELS} or {PAIRED_CUPED_LABELS}, "
                f"got {moments.labels}"
            )
        self.moments = moments
        self.name_1 = name_1
        self.name_2 = name_2

    @classmethod
    def from_samples(cls, sample_1: Sample, sample_2: Sample) -> PairedSufficientStats:
        """Build from two position-aligned samples (see :func:`align_paired`)."""
        if sample_1.sample_size != sample_2.sample_size:
            raise SampleValidationError(
                "paired samples must be equal-size and aligned by pair: "
                f"{sample_1.sample_size} != {sample_2.sample_size}"
            )
        has_cov_1 = sample_1.cov_array is not None
        has_cov_2 = sample_2.cov_array is not None
        if has_cov_1 != has_cov_2:
            raise SampleValidationError(
                "either both or neither paired sample may carry a covariate"
            )
        if has_cov_1:
            assert sample_1.cov_array is not None and sample_2.cov_array is not None
            moments = JointMoments.from_arrays(
                sample_1.array,
                sample_2.array,
                sample_1.cov_array,
                sample_2.cov_array,
                labels=PAIRED_CUPED_LABELS,
            )
        else:
            moments = JointMoments.from_arrays(sample_1.array, sample_2.array, labels=PAIRED_LABELS)
        return cls(moments, name_1=sample_1.name, name_2=sample_2.name)

    @property
    def n(self) -> int:
        return self.moments.n

    @property
    def has_covariate(self) -> bool:
        return self.moments.labels == PAIRED_CUPED_LABELS

    def weights(self, **coefficients: float) -> FloatArray:
        """Weight vector over the labelled series, e.g. ``weights(y2=1, y1=-1)``."""
        vector = np.zeros(len(self.moments.labels), dtype=np.float64)
        for label, coefficient in coefficients.items():
            vector[self.moments.index(label)] = coefficient
        return vector


def align_paired(sample_1: Sample, sample_2: Sample) -> tuple[Sample, Sample, int]:
    """Align two variants by ``pair_ids`` (legacy semantics, baseline §5).

    Keeps the sorted intersection of pair ids, drops unmatched pairs with a
    warning, and returns position-aligned copies plus the dropped-pair count.
    """
    if sample_1.pair_ids is None or sample_2.pair_ids is None:
        raise SampleValidationError("align_paired requires pair_ids on both samples")
    common = np.intersect1d(sample_1.pair_ids, sample_2.pair_ids)
    dropped = (sample_1.sample_size - common.size) + (sample_2.sample_size - common.size)
    if common.size == 0:
        raise SampleValidationError("no common pair_ids between the two samples")
    if dropped:
        warnings.warn(
            f"align_paired dropped {dropped} unmatched pair(s) " f"({common.size} pairs kept)",
            AbkitStatsWarning,
            stacklevel=2,
        )

    def _take(sample: Sample) -> Sample:
        assert sample.pair_ids is not None
        order = np.argsort(sample.pair_ids)
        sorted_ids = sample.pair_ids[order]
        positions = order[np.searchsorted(sorted_ids, common)]
        return Sample(
            sample.array[positions],
            cov_array=None if sample.cov_array is None else sample.cov_array[positions],
            categories_array=(
                None if sample.categories_array is None else sample.categories_array[positions]
            ),
            pair_ids=common,
            name=sample.name,
        )

    return _take(sample_1), _take(sample_2), int(dropped)
