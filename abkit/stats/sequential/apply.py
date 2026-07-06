"""Apply the always-valid transform to a fixed :class:`TestResult` (M5 WP3).

The experiment-level sequential MODE (docs/specs/m5-implementation-plan.md D1): given a
fixed-horizon ``TestResult`` and the frozen mixture variance ``tau2``, widen its CI into
the always-valid confidence sequence and re-derive the p-value / reject flag from it,
stamping ``ci_kind='always_valid'``. Pure — takes a ``TestResult``, returns a new one
(``dataclasses.replace``), never mutating; consumes only the shipped ``(effect, SE)``
via CI-inversion, so it is method-agnostic and never re-derives a variance.
"""

from __future__ import annotations

from dataclasses import replace

from abkit.stats.result import TestResult
from abkit.stats.sequential.confidence_sequence import se_from_ci_length, sequentialize


def to_always_valid(result: TestResult, tau2: float, alpha: float) -> TestResult:
    """Return a copy of ``result`` with always-valid bounds, p-value, and ci_kind.

    A degenerate fixed result (NaN ci_length) yields NaN always-valid bounds — the
    downstream NULLs it exactly like the fixed NaN-bound path, never an exception.
    ``alpha`` is the result's own effective alpha (``result.alpha``).
    """
    se = se_from_ci_length(result.ci_length, alpha)
    lo, hi, av_pvalue = sequentialize(result.effect, se, tau2, alpha)
    reject = (lo > 0.0) or (hi < 0.0)  # CI-excludes-zero ≡ av_pvalue <= alpha
    return replace(
        result,
        left_bound=lo,
        right_bound=hi,
        ci_length=hi - lo,
        pvalue=av_pvalue,
        reject=reject,
        ci_kind="always_valid",
    )
