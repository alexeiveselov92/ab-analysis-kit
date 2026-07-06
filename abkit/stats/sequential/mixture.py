"""The mixture-variance policy ``tau^2`` for the always-valid confidence sequence.

ONE source of ``tau^2`` (M5 decision D4, docs/specs/m5-implementation-plan.md): the
same helper feeds the pipeline activation (WP3) and the A/A validation column (WP2),
so the interval that ships is exactly the interval the A/A calibrates — otherwise the
"peeking FPR back to ~alpha" proof validates a different estimator (a WP2 byte-identity
test pins this).

``tau^2`` is **fixed-by-policy**, not user config, and anchored to the horizon
information: it is chosen to make the confidence sequence *tightest at the planned
horizon* (best power for a full-length experiment). Crucially, **validity holds for ANY
fixed positive ``tau^2``** — Ville's inequality only needs a prior fixed in advance —
so the choice affects tightness/power, never coverage. That is also why ``tau^2`` is
anchored to the horizon (known at design time) and never to the current look (which
would make the mixing prior data-dependent and void the guarantee). A change to this
policy is a docs/specs/statistics-changes.md §4 event and triggers the sequential-mode
re-plan (D7), never a silent CI move.

Purity: plain primitives only.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq


def _optimal_ratio(alpha: float) -> float:
    """``u* = tau^2 / V_horizon`` that minimises the CS width at the horizon.

    Writing ``V = SE^2`` and ``u = tau^2 / V``, the mixture radius satisfies
    ``r(V)^2 / V = ((1 + u)/u) * (ln(1/alpha) + 0.5 * ln(1 + u))``. Minimising over
    ``u`` at the horizon variance gives the stationarity condition

        u = 2 * ln(1/alpha) + ln(1 + u)

    (a scalar fixed point; e.g. ``u* ~= 8.2`` at ``alpha = 0.05``, making the
    always-valid interval ~2.15*SE at the horizon vs the fixed 1.96*SE — a ~10%
    anytime price). Derivation recorded in docs/specs/statistics-changes.md §4.
    """
    two_ln_inv_alpha = 2.0 * (-math.log(alpha))
    upper = 4.0 * (-math.log(alpha)) + 100.0  # f(upper) > 0 for any alpha in (0,1)
    return float(brentq(lambda u: u - two_ln_inv_alpha - math.log1p(u), 1e-12, upper, xtol=1e-12))


def mixture_tau2(horizon_variance: float, alpha: float) -> float:
    """``tau^2`` anchored to the horizon estimator variance.

    ``horizon_variance`` is ``Var(effect estimate)`` at the planned horizon N (i.e.
    ``SE^2`` there); the pipeline computes it once and freezes it for the experiment
    (WP3). Returns ``u*(alpha) * horizon_variance``. Any positive ``tau^2`` is valid;
    this policy only sets where the sequence is tightest.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")
    if not math.isfinite(horizon_variance) or horizon_variance <= 0.0:
        raise ValueError(f"horizon_variance must be finite and positive, got {horizon_variance!r}")
    return _optimal_ratio(alpha) * horizon_variance
