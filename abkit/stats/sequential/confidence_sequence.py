"""The always-valid confidence sequence — a closed-form widening of the fixed CI.

M5 decision D2 (docs/specs/m5-implementation-plan.md; docs/specs/statistics-changes.md
§4): the pure ``abkit.stats`` core exposes a per-look ``(effect, SE)`` sufficient
statistic, not the raw observation stream, so the always-valid interval is the
**asymptotic Gaussian confidence sequence** (Waudby-Smith & Ramdas 2021 — the
Robbins/Howard normal mixture applied to the estimate), NOT the exact finite-sample
Robbins/Howard mSPRT (which needs running sums). It is an experiment-level MODE
transform (D1) — never a method plugin, never a name special-case — that consumes
whatever ``(effect, SE)`` the bound method already produced and **never re-derives a
variance**.

The math. With ``V = SE^2`` and a fixed mixing variance ``tau^2`` (mixture.py), the
two-sided normal-mixture confidence sequence at a look is ``effect ± r`` where

    r = sqrt( (2 * V * (V + tau^2) / tau^2) * ( ln(1/alpha) + 0.5 * ln((V + tau^2)/V) ) ).

Derivation: the mixture likelihood ratio against a ``N(theta_0, tau^2)`` alternative,
``Lambda(theta_0) = sqrt(V/(V+tau^2)) * exp( tau^2 (effect - theta_0)^2 / (2 V (V+tau^2)) )``,
is a non-negative martingale under ``theta_0``; by Ville's inequality the set
``{ theta_0 : Lambda(theta_0) <= 1/alpha }`` covers the truth simultaneously at every
look with probability >= 1 - alpha. Inverting the inequality gives ``r`` above. The
always-valid p-value is the dual, ``min(1, 1/Lambda(0))``, so ``p <= alpha`` iff the
interval excludes zero (pinned by a test). The guarantee is finite-sample if the
estimate were exactly Gaussian and **asymptotic-anytime** otherwise (documented, never
over-claimed as exact mSPRT).

The always-valid interval always strictly contains the fixed-horizon interval (it is
wider by construction — the price of anytime validity), so an early WIN/LOSE read off
it is honest under optional stopping (the readout lifts its pre-horizon refusal only
for ``ci_kind='always_valid'``).

Purity (D5): plain primitives only.
"""

from __future__ import annotations

import math

import scipy.stats as sps


def se_from_ci_length(ci_length: float, alpha: float) -> float:
    """Recover the effect standard error by inverting a symmetric normal CI (D3).

    Every parametric method builds its fixed CI as
    ``effect ± norm.ppf(1 - alpha/2) * SE`` (``effects.normal_test``), so
    ``ci_length = 2 * norm.ppf(1 - alpha/2) * SE`` and therefore

        SE = ci_length / (2 * norm.ppf(1 - alpha/2)).

    This CI-inversion is the ONLY sanctioned SE recovery: it preserves the
    delta-method covariance already baked into ``ci_length`` (relative / CUPED /
    ratio-delta), is method-agnostic, and never re-derives the per-arm variances
    (which would drop the covariance term). Returns NaN for a NaN/degenerate CI
    (the fixed path's NaN-bound bucket — never an exception).
    """
    if not math.isfinite(ci_length) or ci_length < 0.0:
        return float("nan")
    z = float(sps.norm.ppf(1.0 - alpha / 2.0))
    return ci_length / (2.0 * z)


def sequentialize(
    effect: float, se: float, tau2: float, alpha: float
) -> tuple[float, float, float]:
    """Return ``(lo, hi, av_pvalue)`` — the always-valid interval + p-value at a look.

    ``se`` is the effect standard error (recover it from a fixed CI via
    :func:`se_from_ci_length`); ``tau2`` is the fixed mixture variance from
    :func:`abkit.stats.sequential.mixture.mixture_tau2`. A degenerate look
    (non-finite ``effect``/``se`` or ``se <= 0``) returns ``(nan, nan, nan)`` — it
    is tallied in the NaN-bound bucket, never a silent non-rejection and never an
    exception. Programming-contract violations (``alpha`` out of range, non-positive
    ``tau2``) raise ``ValueError``.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")
    if not math.isfinite(tau2) or tau2 <= 0.0:
        raise ValueError(f"tau2 (mixture variance) must be finite and positive, got {tau2!r}")
    if not (math.isfinite(effect) and math.isfinite(se)) or se <= 0.0:
        nan = float("nan")
        return (nan, nan, nan)

    var = se * se
    ln_inv_alpha = -math.log(alpha)
    denom = 2.0 * var * (var + tau2)  # 2 V (V + tau^2), shared by radius and p-value
    ratio = (var + tau2) / var
    radius = math.sqrt((denom / tau2) * (ln_inv_alpha + 0.5 * math.log(ratio)))
    lo = effect - radius
    hi = effect + radius

    # Always-valid p-value = min(1, 1/Lambda(0)); p <= alpha iff 0 is excluded.
    inv_lambda = math.sqrt(ratio) * math.exp(-(tau2 * effect * effect) / denom)
    pvalue = inv_lambda if inv_lambda < 1.0 else 1.0
    return (lo, hi, pvalue)
