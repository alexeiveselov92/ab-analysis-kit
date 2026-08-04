"""Score-test inversion for two independent proportions (m13 STAT-3).

The one statistic used three ways (docs/specs/m13-implementation-plan.md §STAT-3,
derived in ``docs/research/2026-08-m13-blind-rederive/proportion-interval.derivation.json``).
With ``(p̃₁, p̃₂)`` the binomial MLEs constrained to the candidate contrast, the
two-sample score statistic is

    difference scale:  Z(δ) = (p̂₂ − p̂₁ − δ) / σ̃(δ),   σ̃(δ)² = p̃₁q̃₁/n₁ + p̃₂q̃₂/n₂
    ratio scale:       Z(θ) = (p̂₂ − θ·p̂₁) / σ̃(θ),      σ̃(θ)² = θ²p̃₁q̃₁/n₁ + p̃₂q̃₂/n₂

and the confidence set is ``{contrast : Z² ≤ z²}``. **At the null both reduce to
the classical pooled two-sample z** — ``Z(0)`` and ``Z(1)`` are the same number,
Pearson's χ² for the 2×2 table — so "the interval excludes zero", "the ratio
interval excludes 1" and "p < α" are ONE event, by construction rather than by
coincidence. That is the whole reason this construction was chosen over a Wald
interval with an unpooled SE: the coherence the pooled interval bought by being
invalid away from the null is kept, and the validity is bought back.

**The MN ``N/(N−1)`` variance factor is deliberately NOT applied** (D11 — the
Farrington–Manning 1990 form of Miettinen–Nurminen 1985). Applying it to the
interval alone would break the coherence above at a relative distance ``1/(2N)``
from the boundary; applying it to both would move every reported p-value by that
much. Dropping it makes ``Z(0)`` *bit-identical* to the pooled z the z-test
already computes, so m13 moves no p-value at all. If it is ever added it must be
applied to the interval and the p-value together, or to neither.

Numerics, and why each choice is here:

- The constrained MLE solves a **cubic** (difference) or a **quadratic** (ratio)
  in ``p̃₁``. Both have a closed form, and both closed forms lose ~1e-8 to
  cancellation exactly where the root is small relative to the coefficients —
  which is the sparse-metric regime this construction exists to serve. The
  closed form is therefore a SEED, polished by Newton steps on ``dℓ/dp̃₁``
  (well-conditioned: its terms are ``x/p`` ratios, not differences of large
  coefficients). Measured: seed ~1e-12, polished ~1e-16.
- The endpoints come from a **fixed-iteration bisection**, not a convergence
  loop. Fixed work makes the result independent of block size and thread count
  (the M7 D13 byte-reproducibility discipline) and makes the scalar and batch
  entries bit-identical by construction — they are the same code.
- The brackets are the FEASIBLE range, not a scan: ``δ ∈ [−1, 1]`` and
  ``θ ∈ (0, ∞)`` mapped to ``v = θ/(1+θ) ∈ (0, 1)``. ``Z`` decreases in the
  contrast, so an endpoint that does not exist inside the range is the range's
  own boundary — which is the derivation's required "bracketing scan plus a
  tested fallback", with the fallback being the only answer a bounded
  confidence set can have. A degenerate table therefore yields ``[−1, 1]`` or
  ``θ_L = 0``, never a NaN that propagates into a persisted interval.

Purity: numpy + stdlib only, arrays in and arrays out. The scalar entry
(``ztest``) wraps its four counts in length-1 arrays, so there is exactly one
implementation of this math in the package.
"""

from __future__ import annotations

import numpy as np

from abkit.stats.effects import FloatArray

#: Newton refinements of the closed-form constrained-MLE seed. Four is two more
#: than convergence needs (quadratic from ~1e-12) and costs one multiply-add per
#: bisection step — the margin is deliberate, since the seed's error grows in
#: exactly the sparse regime the score interval is FOR.
MLE_NEWTON_STEPS = 4

#: Bisection steps per endpoint. 70 halvings of a width-2 bracket resolve
#: 1.7e-21 absolute — below the ULP of any endpoint above 1e-5, and still
#: rel-1e-12 for an endpoint as small as 1e-9. Fixed, never a tolerance loop.
BISECTION_ITERATIONS = 70


def _asarray(value: object) -> FloatArray:
    return np.asarray(value, dtype=np.float64)


def _signed_infinity(numerator: FloatArray, variance: FloatArray) -> FloatArray:
    """``Z`` where the constrained variance vanishes — the feasible boundary.

    A zero constrained variance means the candidate contrast forces both arms to
    a degenerate proportion, so any observed departure from it is infinitely
    many standard errors away. Returning ±inf keeps the bisection's sign logic
    total; returning NaN would silently widen the interval to the whole range.
    """
    return np.where(numerator > 0.0, np.inf, np.where(numerator < 0.0, -np.inf, 0.0))


# ── difference scale ─────────────────────────────────────────────────────────


def constrained_mle_difference(
    count_1: FloatArray,
    nobs_1: FloatArray,
    count_2: FloatArray,
    nobs_2: FloatArray,
    delta: FloatArray,
) -> FloatArray:
    """``p̃₁`` maximising the binomial likelihood subject to ``p̃₂ − p̃₁ = delta``.

    The stationarity condition ``(x₁ − n₁s)·t(1−t) + (x₂ − n₂t)·s(1−s) = 0``
    (``t = s + delta``) expands to the cubic

        N·s³ + [(2n₁+n₂)δ − X − N]·s² + [X − (2x₁+N)δ + n₁δ²]·s + x₁δ(1−δ) = 0

    with ``X = x₁+x₂`` and ``N = n₁+n₂``. **At δ = 0 its roots are 0, 1 and
    X/N** — the admissible one being the pooled proportion, which is the
    algebraic statement of this module's coherence claim and the cheapest guard
    against a mistyped coefficient (pinned by a test).

    Of the three real roots the constrained MLE is the ``k=1`` branch of the
    trigonometric solution; it is then Newton-polished and clamped to the
    feasible range ``[max(0,−δ), min(1,1−δ)]``. The clamp is not defensive
    tidying — for a table with an empty cell the likelihood's maximum sits ON
    that boundary, and the polish would otherwise walk off it.
    """
    total_count = count_1 + count_2
    total_nobs = nobs_1 + nobs_2
    lower = np.maximum(0.0, -delta)
    upper = np.minimum(1.0, 1.0 - delta)

    a2 = (2.0 * nobs_1 + nobs_2) * delta - total_count - total_nobs
    a1 = total_count - (2.0 * count_1 + total_nobs) * delta + nobs_1 * delta * delta
    a0 = count_1 * delta * (1.0 - delta)
    with np.errstate(divide="ignore", invalid="ignore"):
        norm_2 = a2 / total_nobs
        norm_1 = a1 / total_nobs
        norm_0 = a0 / total_nobs
        radius = np.sqrt(np.maximum(norm_2 * norm_2 / 9.0 - norm_1 / 3.0, 0.0))
        offset = norm_2 * norm_2 * norm_2 / 27.0 - norm_2 * norm_1 / 6.0 + norm_0 / 2.0
        cosine = np.where(radius > 0.0, offset / np.where(radius > 0.0, radius**3, 1.0), 0.0)
        seed = 2.0 * radius * np.cos((np.pi + np.arccos(np.clip(cosine, -1.0, 1.0))) / 3.0)
        seed -= norm_2 / 3.0
    root = np.clip(np.where(np.isfinite(seed), seed, lower), lower, upper)

    fail_1 = nobs_1 - count_1
    fail_2 = nobs_2 - count_2
    for _ in range(MLE_NEWTON_STEPS):
        other = root + delta
        with np.errstate(divide="ignore", invalid="ignore"):
            slope = (
                np.where(count_1 > 0.0, count_1 / root, 0.0)
                - np.where(fail_1 > 0.0, fail_1 / (1.0 - root), 0.0)
                + np.where(count_2 > 0.0, count_2 / other, 0.0)
                - np.where(fail_2 > 0.0, fail_2 / (1.0 - other), 0.0)
            )
            curvature = -(
                np.where(count_1 > 0.0, count_1 / (root * root), 0.0)
                + np.where(fail_1 > 0.0, fail_1 / ((1.0 - root) ** 2), 0.0)
                + np.where(count_2 > 0.0, count_2 / (other * other), 0.0)
                + np.where(fail_2 > 0.0, fail_2 / ((1.0 - other) ** 2), 0.0)
            )
            step = np.where(np.isfinite(slope) & (curvature < 0.0), slope / curvature, 0.0)
        candidate = np.clip(root - step, lower, upper)
        root = np.where(np.isfinite(candidate), candidate, root)
    return root


def score_z_difference(
    count_1: FloatArray,
    nobs_1: FloatArray,
    count_2: FloatArray,
    nobs_2: FloatArray,
    delta: FloatArray,
) -> FloatArray:
    """``Z(delta)`` on the difference scale — see the module docstring."""
    root = constrained_mle_difference(count_1, nobs_1, count_2, nobs_2, delta)
    other = root + delta
    with np.errstate(divide="ignore", invalid="ignore"):
        variance = root * (1.0 - root) / nobs_1 + other * (1.0 - other) / nobs_2
        numerator = count_2 / nobs_2 - count_1 / nobs_1 - delta
        standardised = numerator / np.sqrt(variance)
    return np.where(variance > 0.0, standardised, _signed_infinity(numerator, variance))


def score_interval_difference(
    count_1: FloatArray,
    nobs_1: FloatArray,
    count_2: FloatArray,
    nobs_2: FloatArray,
    critical: float,
) -> tuple[FloatArray, FloatArray]:
    """``{δ : |Z(δ)| ≤ critical}`` as ``(left, right)`` over the feasible ``[−1, 1]``.

    ``Z`` decreases in ``δ`` (its numerator does, and the constrained variance
    varies smoothly), so the set is an interval and each endpoint is bracketed by
    the point estimate on one side and the feasible boundary on the other. An
    endpoint that does not exist inside the range converges to that boundary,
    which is the correct answer for a bounded confidence set — never NaN.
    """
    count_1, nobs_1, count_2, nobs_2 = map(_asarray, (count_1, nobs_1, count_2, nobs_2))
    with np.errstate(divide="ignore", invalid="ignore"):
        estimate = count_2 / nobs_2 - count_1 / nobs_1

    def standardised(delta: FloatArray) -> FloatArray:
        return score_z_difference(count_1, nobs_1, count_2, nobs_2, delta)

    low, high = np.full_like(estimate, -1.0), estimate.copy()
    for _ in range(BISECTION_ITERATIONS):
        middle = 0.5 * (low + high)
        outside = standardised(middle) > critical
        low = np.where(outside, middle, low)
        high = np.where(outside, high, middle)
    left = 0.5 * (low + high)

    low, high = estimate.copy(), np.full_like(estimate, 1.0)
    for _ in range(BISECTION_ITERATIONS):
        middle = 0.5 * (low + high)
        outside = standardised(middle) < -critical
        high = np.where(outside, middle, high)
        low = np.where(outside, low, middle)
    right = 0.5 * (low + high)
    return left, right


# ── ratio scale ──────────────────────────────────────────────────────────────


def constrained_mle_ratio(
    count_1: FloatArray,
    nobs_1: FloatArray,
    count_2: FloatArray,
    nobs_2: FloatArray,
    theta: FloatArray,
) -> FloatArray:
    """``p̃₁`` maximising the likelihood subject to ``p̃₂ = theta·p̃₁`` (Route C).

    The same stationarity condition on the ratio scale is a QUADRATIC,
    ``Nθ·s² − [θ(x₁+n₂) + (n₁+x₂)]·s + X = 0``, whose admissible root is the
    smaller one — taken in the cancellation-free form ``2X / (B + √(B²−4NθX))``
    rather than ``(B − √(...))/(2Nθ)``, which loses most of its digits whenever
    ``B² ≫ 4NθX`` (i.e. whenever the metric is sparse). **At θ = 1 the root is
    X/N**, the same pooled proportion the difference scale gives at δ = 0 — the
    three-way coherence.
    """
    total_count = count_1 + count_2
    total_nobs = nobs_1 + nobs_2
    linear = theta * (count_1 + nobs_2) + (nobs_1 + count_2)
    with np.errstate(divide="ignore", invalid="ignore"):
        discriminant = np.sqrt(
            np.maximum(linear * linear - 4.0 * total_nobs * theta * total_count, 0.0)
        )
        seed = 2.0 * total_count / (linear + discriminant)
    upper = np.minimum(1.0, np.where(theta > 0.0, 1.0 / np.where(theta > 0.0, theta, 1.0), 1.0))
    root = np.clip(np.where(np.isfinite(seed), seed, 0.0), 0.0, upper)

    fail_1 = nobs_1 - count_1
    fail_2 = nobs_2 - count_2
    for _ in range(MLE_NEWTON_STEPS):
        with np.errstate(divide="ignore", invalid="ignore"):
            other = theta * root
            slope = (
                np.where(total_count > 0.0, total_count / root, 0.0)
                - np.where(fail_1 > 0.0, fail_1 / (1.0 - root), 0.0)
                - np.where(fail_2 > 0.0, fail_2 * theta / (1.0 - other), 0.0)
            )
            curvature = -(
                np.where(total_count > 0.0, total_count / (root * root), 0.0)
                + np.where(fail_1 > 0.0, fail_1 / ((1.0 - root) ** 2), 0.0)
                + np.where(fail_2 > 0.0, fail_2 * theta * theta / ((1.0 - other) ** 2), 0.0)
            )
            step = np.where(np.isfinite(slope) & (curvature < 0.0), slope / curvature, 0.0)
        candidate = np.clip(root - step, 0.0, upper)
        root = np.where(np.isfinite(candidate), candidate, root)
    return root


def score_z_ratio(
    count_1: FloatArray,
    nobs_1: FloatArray,
    count_2: FloatArray,
    nobs_2: FloatArray,
    theta: FloatArray,
) -> FloatArray:
    """``Z(theta)`` on the ratio scale — see the module docstring."""
    root = constrained_mle_ratio(count_1, nobs_1, count_2, nobs_2, theta)
    with np.errstate(divide="ignore", invalid="ignore"):
        other = theta * root
        variance = theta * theta * root * (1.0 - root) / nobs_1 + other * (1.0 - other) / nobs_2
        numerator = count_2 / nobs_2 - theta * count_1 / nobs_1
        standardised = numerator / np.sqrt(variance)
    return np.where(variance > 0.0, standardised, _signed_infinity(numerator, variance))


def score_interval_ratio(
    count_1: FloatArray,
    nobs_1: FloatArray,
    count_2: FloatArray,
    nobs_2: FloatArray,
    critical: float,
) -> tuple[FloatArray, FloatArray]:
    """``{θ : |Z(θ)| ≤ critical}`` as ``(lower, upper)`` over ``θ ∈ (0, ∞)``.

    The search runs on ``v = θ/(1+θ) ∈ (0, 1)`` — a monotone reparametrisation
    of the whole positive line onto a bounded bracket, so the unbounded scale
    needs no expansion loop and no cap: ``v → 1`` IS ``θ → ∞``. 70 halvings
    resolve θ out to ~1e21, and an upper endpoint that large is reported as the
    number it is rather than clipped, because the caller's job (a warning about
    an unidentified relative effect) needs to see it.

    ``θ_L = 0`` is a real answer, not a failure: a treatment arm with no
    conversions cannot exclude a −100% lift. Arm 1 having no conversions is
    refused upstream — the relative POINT estimate is undefined there (H5), so
    there is nothing for an interval to be about.
    """
    count_1, nobs_1, count_2, nobs_2 = map(_asarray, (count_1, nobs_1, count_2, nobs_2))
    with np.errstate(divide="ignore", invalid="ignore"):
        prop_1 = count_1 / nobs_1
        prop_2 = count_2 / nobs_2
        estimate = np.where(prop_1 > 0.0, prop_2 / np.where(prop_1 > 0.0, prop_1, 1.0), np.inf)
        split = np.clip(estimate / (1.0 + estimate), 0.0, 1.0)
    split = np.where(np.isfinite(split), split, 1.0)

    def standardised(fraction: FloatArray) -> FloatArray:
        with np.errstate(divide="ignore", invalid="ignore"):
            theta = fraction / (1.0 - fraction)
        return score_z_ratio(count_1, nobs_1, count_2, nobs_2, theta)

    low, high = np.zeros_like(split), split.copy()
    for _ in range(BISECTION_ITERATIONS):
        middle = 0.5 * (low + high)
        outside = standardised(middle) > critical
        low = np.where(outside, middle, low)
        high = np.where(outside, high, middle)
    left_fraction = 0.5 * (low + high)

    low, high = split.copy(), np.ones_like(split)
    for _ in range(BISECTION_ITERATIONS):
        middle = 0.5 * (low + high)
        outside = standardised(middle) < -critical
        high = np.where(outside, middle, high)
        low = np.where(outside, low, middle)
    right_fraction = 0.5 * (low + high)

    with np.errstate(divide="ignore", invalid="ignore"):
        lower = left_fraction / (1.0 - left_fraction)
        upper = right_fraction / (1.0 - right_fraction)
    return np.where(left_fraction >= 1.0, np.inf, lower), np.where(
        right_fraction >= 1.0, np.inf, upper
    )
