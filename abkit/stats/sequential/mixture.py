"""The mixture-variance policy ``tau^2`` for the always-valid confidence sequence.

ONE source of ``tau^2`` (M5 decision D4, docs/specs/m5-implementation-plan.md): the
same helper feeds the pipeline activation (WP3) and the A/A validation column (WP2),
so the interval that ships is exactly the interval the A/A calibrates — otherwise the
"peeking FPR back to ~alpha" proof validates a different estimator (a WP2 byte-identity
test pins this).

``tau^2`` is **fixed-by-policy**, not user config, and anchored to a **reference look**:
the *first usable grid cutoff* (the earliest look with a finite positive SE). This is
the anchor decision (m5-implementation-plan.md D-Seq-anchor, maintainer-confirmed):
- it is **stable across runs** (the first cutoff is idempotent — same data every run),
- it is **computable live** during an ongoing experiment (the horizon is in the future
  and cannot be seen pre-conclusion; the first look always exists),
- it makes the confidence sequence **tightest early**, which is exactly aligned with
  the always-valid use-case (the impatient experimenter peeks early and often).
Crucially, **validity holds for ANY fixed positive ``tau^2``** — Ville's inequality only
needs a prior fixed in advance — so the choice affects tightness/power, never coverage;
that is why the anchor must be fixed at design time (the first look), never the current
look (which would make the mixing prior data-dependent and void the guarantee). A change
to this policy is a docs/specs/statistics-changes.md §4 event and triggers the
sequential-mode re-plan (D7), never a silent CI move. (Horizon-anchoring — statistically
tightest at the planned stop — is a possible **future** refinement now that ``abk plan``
can supply a planned-N; it stays unimplemented because moving the anchor is a
docs/specs/statistics-changes.md §4 event, and it is not live-computable during an
ongoing experiment.)

Purity: plain primitives only.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq


def _optimal_ratio(alpha: float) -> float:
    """``u* = tau^2 / V_ref`` that minimises the CS width at the reference look.

    Writing ``V = SE^2`` and ``u = tau^2 / V``, the mixture radius satisfies
    ``r(V)^2 / V = ((1 + u)/u) * (ln(1/alpha) + 0.5 * ln(1 + u))``. Minimising over
    ``u`` at the reference variance gives the stationarity condition

        u = 2 * ln(1/alpha) + ln(1 + u)

    (a scalar fixed point; e.g. ``u* ~= 8.2`` at ``alpha = 0.05``, where the
    always-valid half-width is ``sqrt(2*h(u*))*SE ~= 3.04*SE`` at the reference look vs
    the fixed ``1.96*SE`` — a ~1.55x anytime price at the anchor, ~1.6-1.9x at a later
    look with more data — the honest cost of any-time peeking). Derivation recorded in
    docs/specs/statistics-changes.md §4.
    """
    two_ln_inv_alpha = 2.0 * (-math.log(alpha))
    upper = 4.0 * (-math.log(alpha)) + 100.0  # f(upper) > 0 for any alpha in (0,1)
    return float(brentq(lambda u: u - two_ln_inv_alpha - math.log1p(u), 1e-12, upper, xtol=1e-12))


def mixture_tau2(reference_variance: float, alpha: float) -> float:
    """``tau^2`` anchored to the reference-look estimator variance (D-Seq-anchor).

    ``reference_variance`` is ``Var(effect estimate) = SE^2`` at the first usable grid
    cutoff; the pipeline and the A/A both compute it from the earliest usable look and
    freeze it for the experiment. Returns ``u*(alpha) * reference_variance``. Any
    positive ``tau^2`` is valid; this policy only sets where the sequence is tightest.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")
    if not math.isfinite(reference_variance) or reference_variance <= 0.0:
        raise ValueError(
            f"reference_variance must be finite and positive, got {reference_variance!r}"
        )
    return _optimal_ratio(alpha) * reference_variance
