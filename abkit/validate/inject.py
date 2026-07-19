"""Effect injection at the sufficient-statistics level (docs/specs/m4-implementation-plan.md D2).

A known effect is injected into one placebo arm to measure power / achieved-MDE /
CI-coverage. The algebra is exact and numpy-only (purity-safe — this lives in
``abkit.validate``, never ``abkit.stats``): scaling ``y → y·(1+δ)`` maps the raw
centered moments a ``SufficientStats`` stores (``m2 = Σ(y−ȳ)²``,
``cross_c = Σ(y−ȳ)(x−x̄)``, samples.py:290–307) by ``(1+δ)²`` and ``(1+δ)``
respectively, leaving the covariate moments untouched — so ``corr_coef`` is
invariant and CUPED needs no special case.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt

from abkit.stats.samples import Fraction, RatioSufficientStats, SufficientStats
from abkit.validate._types import ValidateError

FloatArray = npt.NDArray[np.float64]

#: Returned suffstats type for a multiplicative injection.
InjectableStats = SufficientStats | RatioSufficientStats | Fraction


def inject_multiplicative(stats: InjectableStats, delta: float) -> InjectableStats:
    """Return a copy of ``stats`` with the metric scaled by ``(1+δ)``.

    ``SufficientStats`` (t-test / z-test-on-means / CUPED): ``mean·(1+δ)``,
    ``m2·(1+δ)²``, ``cross_c·(1+δ)``; covariate moments unchanged. The tiny
    negative ``m2`` that float cancellation can produce is clamped to ``0.0``
    (samples.py:273–274 rejects ``m2<0``; the ratio_delta.py:52 precedent).

    ``RatioSufficientStats``: scale ``mean_num``, ``m2_num·(1+δ)²``, ``c_nd·(1+δ)``;
    denominator moments unchanged (the numerator is the metric).

    ``Fraction``: ``count·(1+δ)`` **clamped to ``≤nobs``** — a high base-rate
    proportion cannot be lifted past 100 %; the caller treats a clamped injection
    as an unreachable MDE (WP5), never a crash.
    """
    factor = 1.0 + float(delta)

    if isinstance(stats, SufficientStats):
        return SufficientStats(
            n=stats.n,
            mean=stats.mean * factor,
            m2=max(0.0, stats.m2 * factor * factor),
            cov_mean=stats.cov_mean,
            cov_m2=stats.cov_m2,
            cross_c=None if stats.cross_c is None else stats.cross_c * factor,
            name=stats.name,
        )
    if isinstance(stats, RatioSufficientStats):
        return RatioSufficientStats(
            n=stats.n,
            mean_num=stats.mean_num * factor,
            m2_num=max(0.0, stats.m2_num * factor * factor),
            mean_den=stats.mean_den,
            m2_den=stats.m2_den,
            c_nd=stats.c_nd * factor,
            name=stats.name,
        )
    if isinstance(stats, Fraction):
        return Fraction(
            count=min(stats.nobs, stats.count * factor),
            nobs=stats.nobs,
            name=stats.name,
        )
    raise ValidateError(f"cannot inject an effect into {type(stats).__name__}")


def inject_multiplicative_columns(
    input_kind: str, columns: Mapping[str, FloatArray], delta: float
) -> dict[str, FloatArray]:
    """The batch mirror of :func:`inject_multiplicative` over
    ``ArmStatsBatch.columns`` (the M7 WP3→WP4 injected-pass seam).

    Same algebra, same op order per element (``x * factor``,
    ``m2 * factor * factor`` clamped at 0, the Fraction ``count ≤ nobs``
    clamp), applied array-wise — bit-exact vs the scalar path per row for
    finite inputs, pinned by ``tests/validate/test_vector_resample.py``. One
    DELIBERATE divergence on NaN ``m2``/``m2_num`` (adversarial review round
    2): the scalar's ``max(0.0, nan)`` swallows a NaN to an exact-zero
    variance — a latent scalar-side quirk (reachable only from overflow-scale
    moments, out of WP3 scope) — while ``np.maximum(0.0, nan)`` here keeps
    the NaN, preserving the "gaps, never zeros" poison; a regression test
    pins the divergence so the batch is never "fixed" back to the scalar
    quirk. ``input_kind`` follows ``BaseMethod.input_kind`` (sample |
    fraction | ratio) exactly like ``build_arm_batch``.
    """
    factor = 1.0 + float(delta)

    if input_kind == "fraction":
        return {
            "count": np.minimum(columns["nobs"], columns["count"] * factor),
            "nobs": columns["nobs"],
        }
    if input_kind == "ratio":
        return {
            "n": columns["n"],
            "mean_num": columns["mean_num"] * factor,
            "m2_num": np.maximum(0.0, columns["m2_num"] * factor * factor),
            "mean_den": columns["mean_den"],
            "m2_den": columns["m2_den"],
            "c_nd": columns["c_nd"] * factor,
        }
    injected = {
        "n": columns["n"],
        "mean": columns["mean"] * factor,
        "m2": np.maximum(0.0, columns["m2"] * factor * factor),
    }
    if "cross_c" in columns:  # CUPED: covariate moments unchanged, cross scales
        injected["cov_mean"] = columns["cov_mean"]
        injected["cov_m2"] = columns["cov_m2"]
        injected["cross_c"] = columns["cross_c"] * factor
    return injected


def injection_clamped_columns(
    input_kind: str, columns: Mapping[str, FloatArray], delta: float
) -> npt.NDArray[np.bool_]:
    """Per-row batch mirror of :func:`injection_clamped` (Fraction saturation).

    NaN rows compare False — a degenerate gap is never reported as clamped.
    """
    if not columns:
        raise ValueError("injection_clamped_columns requires at least one suffstats column")
    if input_kind == "fraction":
        with np.errstate(invalid="ignore"):
            return columns["count"] * (1.0 + float(delta)) > columns["nobs"]
    first = next(iter(columns.values()))
    return np.zeros(first.shape[0], dtype=bool)


def injection_clamped(stats: InjectableStats, delta: float) -> bool:
    """True when a multiplicative injection would saturate (Fraction count > nobs).

    The scorer flags such a cell as "MDE unreachable" instead of reporting a
    truncated effect as if it were the requested δ.
    """
    if isinstance(stats, Fraction):
        return stats.count * (1.0 + float(delta)) > stats.nobs
    return False
