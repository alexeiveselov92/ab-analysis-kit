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

from abkit.stats.samples import Fraction, RatioSufficientStats, SufficientStats
from abkit.validate._types import ValidateError

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


def injection_clamped(stats: InjectableStats, delta: float) -> bool:
    """True when a multiplicative injection would saturate (Fraction count > nobs).

    The scorer flags such a cell as "MDE unreachable" instead of reporting a
    truncated effect as if it were the requested δ.
    """
    if isinstance(stats, Fraction):
        return stats.count * (1.0 + float(delta)) > stats.nobs
    return False
