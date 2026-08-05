"""Independent references for the M13 estimators — transcribed from definitions.

The M1 golden discipline (statistics-changes.md §0 step 2, §1.1): the anchor is
a transcription written from the *definition*, never a second call into the
engine, and the engine is what gets fixed when they disagree. M13 ships new
numbers rather than reproducing legacy ones, so its goldens anchor on the
mathematical statement of each estimator instead of on the legacy catalogue —
and each reference deliberately uses a **different algorithm** from the shipped
one, because a reference that shares the implementation's technique agrees with
it on the shared mistake (the STAT-3 lesson: two wrong references were tried
before the objective function was used as the anchor).

* the score interval — the shipped path forms a cubic in closed form, polishes
  it with Newton, then bisects a fixed number of times. The reference below
  maximises the constrained likelihood with ``brentq`` on ``dℓ/dp̃₁`` and finds
  each endpoint with ``brentq`` on ``|Z(δ)| = c``. Nothing is shared.
* Fieller — the shipped path solves ``Aθ² − 2Bθ + C ≤ 0`` through a
  cancellation-free discriminant and a stabilised quadratic formula. The
  reference uses ``numpy.roots`` on the same polynomial.
* Holm — the reference is the step-down definition itself, written out.

Both root-finders are only valid where the constrained maximum is INTERIOR, so
the golden tables are chosen accordingly; the boundary cases (an empty cell,
saturated arms) are covered by the KATs in ``tests/stats/`` and by the
objective-function test there, which is the only reference that can see them.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq

_EPS = 1e-12


def _mle_constrained(
    count_1: float,
    nobs_1: float,
    count_2: float,
    nobs_2: float,
    p2_of_p1,
    dp2_dp1,
    lo: float,
    hi: float,
) -> float:
    """``argmax ℓ(p₁)`` under ``p₂ = p2_of_p1(p₁)``, by a sign change in ``dℓ/dp₁``."""

    def derivative(p1: float) -> float:
        p2 = p2_of_p1(p1)
        return (
            count_1 / p1
            - (nobs_1 - count_1) / (1.0 - p1)
            + (count_2 / p2 - (nobs_2 - count_2) / (1.0 - p2)) * dp2_dp1(p1)
        )

    # The feasible width collapses as δ approaches ±1, so the padding is
    # proportional AND at least a few ULPs of the boundary's own magnitude:
    # ``lo + 1e-21`` is ``lo`` in float64, and the constrained ``p₂`` is then
    # exactly 0 (a zero divide, not a large number).
    span = hi - lo
    pad = min(max(span * 1e-12, 8.0 * math.ulp(max(abs(lo), abs(hi), 1.0))), span * 0.5)
    left, right = lo + pad, hi - pad
    d_left, d_right = derivative(left), derivative(right)
    if d_left <= 0.0:  # the maximum sits on (or below) the lower boundary
        return left
    if d_right >= 0.0:
        return right
    return brentq(derivative, left, right, xtol=1e-15, rtol=8.9e-16, maxiter=200)


def score_z_difference(count_1, nobs_1, count_2, nobs_2, delta: float) -> float:
    """The Miettinen–Nurminen statistic at ``p₂ − p₁ = δ`` (Farrington–Manning form)."""
    lo, hi = max(0.0, -delta), min(1.0, 1.0 - delta)
    p1 = _mle_constrained(
        count_1, nobs_1, count_2, nobs_2, lambda p: p + delta, lambda _p: 1.0, lo, hi
    )
    p2 = p1 + delta
    variance = p1 * (1.0 - p1) / nobs_1 + p2 * (1.0 - p2) / nobs_2
    estimate = count_2 / nobs_2 - count_1 / nobs_1
    return (estimate - delta) / math.sqrt(variance)


def score_z_ratio(count_1, nobs_1, count_2, nobs_2, theta: float) -> float:
    """The same statistic at ``p₂ / p₁ = θ``."""
    hi = min(1.0, 1.0 / theta)
    p1 = _mle_constrained(
        count_1, nobs_1, count_2, nobs_2, lambda p: theta * p, lambda _p: theta, 0.0, hi
    )
    p2 = theta * p1
    variance = p2 * (1.0 - p2) / nobs_2 + theta * theta * p1 * (1.0 - p1) / nobs_1
    return (count_2 / nobs_2 - theta * count_1 / nobs_1) / math.sqrt(variance)


def _bracketed_root(statistic, target: float, lo: float, hi: float) -> float:
    """``brentq`` on ``statistic(x) = target`` over a bracket known to contain it."""
    pad = 1e-9 * max(hi - lo, _EPS)
    return brentq(
        lambda x: statistic(x) - target, lo + pad, hi - pad, xtol=1e-15, rtol=8.9e-16, maxiter=200
    )


def score_interval_difference_reference(
    count_1, nobs_1, count_2, nobs_2, critical: float
) -> tuple[float, float]:
    """``{δ : |Z(δ)| ≤ c}``. ``Z`` decreases in δ, so each endpoint is bracketed
    by the point estimate on one side and the feasible boundary on the other."""
    estimate = count_2 / nobs_2 - count_1 / nobs_1

    def z(delta: float) -> float:
        return score_z_difference(count_1, nobs_1, count_2, nobs_2, delta)

    left = _bracketed_root(z, critical, -1.0, estimate)
    right = _bracketed_root(z, -critical, estimate, 1.0)
    return left, right


def score_interval_ratio_reference(
    count_1, nobs_1, count_2, nobs_2, critical: float
) -> tuple[float, float]:
    """``{θ : |Z(θ)| ≤ c}`` on the ratio scale, returned as θ (not as a lift)."""
    estimate = (count_2 / nobs_2) / (count_1 / nobs_1)

    def z(theta: float) -> float:
        return score_z_ratio(count_1, nobs_1, count_2, nobs_2, theta)

    lower = _bracketed_root(z, critical, 0.0, estimate)
    upper = _bracketed_root(z, -critical, estimate, 1e6)
    return lower, upper


def fieller_bounds_reference(
    mean_num: float,
    var_num: float,
    mean_den: float,
    var_den: float,
    covariance: float,
    critical: float,
) -> tuple[float, float]:
    """``{θ : (a − θb)² ≤ c²(V_a − 2θV_ab + θ²V_b)}`` via ``numpy.roots``.

    Only the bounded branch: the caller's tables are chosen so the leading
    coefficient is positive (``b`` clearly distinguishable from zero), which is
    where a bounded confidence set exists at all (Gleser–Hwang).
    """
    import numpy as np

    c2 = critical * critical
    a_coef = mean_den * mean_den - c2 * var_den
    b_coef = -2.0 * (mean_num * mean_den - c2 * covariance)
    c_coef = mean_num * mean_num - c2 * var_num
    assert a_coef > 0.0, "reference covers the bounded branch only"
    roots = np.roots([a_coef, b_coef, c_coef])
    assert np.all(np.isreal(roots)), "reference covers the non-empty branch only"
    lo, hi = sorted(float(r.real) for r in roots)
    return lo, hi


def holm_adjusted_reference(pvalues) -> list[float]:
    """The step-down definition: ``p₍ᵢ₎ ← max over j ≤ i of (m − j + 1)·p₍ⱼ₎``, capped at 1."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, (m - rank) * pvalues[index])
        adjusted[index] = min(1.0, running)
    return adjusted
