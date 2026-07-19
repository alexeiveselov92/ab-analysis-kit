"""Bootstrap CI and p-value helpers (baseline §4; hygiene H4/H8).

The percentile CI and the sign p-value reproduce the legacy verbatim (golden
parity) and the sign p-value is the DEFAULT — statistics-changes.md §2/§6:
defaults stay baseline-faithful until the A/A matrix (M4) proves a fix helps.
H4's plug-in p-value ``(#extreme + 1)/(n + 1)`` — bounded away from an exact 0 —
ships as the opt-in, identity-bearing ``pvalue_kind: plugin``; promoting it to
the default is an ALGORITHM_VERSION bump arbitrated by the §0 process.

Tie convention (documented, conservative): replicates at exactly 0 count as
extreme on BOTH sides — they enter both the ``>= 0`` and the ``<= 0`` counts of
:func:`pvalue_plugin`. (The legacy sign p-value counted ties on neither side.)

H8: the legacy per-comparison Kolmogorov–Smirnov normality warning is DROPPED —
it tested against parameters estimated from the same data (uncalibrated
p-value); no replacement diagnostic ships in M1.
"""

from __future__ import annotations

import numpy as np

from abkit.stats.base import ParamSpec
from abkit.stats.exceptions import MethodParamError
from abkit.stats.samples import FloatArray

#: p-value estimator selector for every bootstrap method. Identity-bearing: it
#: changes published p-values, so it is part of ``method_config_id``.
PVALUE_KIND_PARAM = ParamSpec(
    name="pvalue_kind",
    types=(str,),
    default="sign",
    identity=True,
    choices=("plugin", "sign"),
    description=(
        "Bootstrap p-value estimator: 'sign' = legacy 2*min(P(boot>0), P(boot<0)) "
        "(baseline-faithful default); 'plugin' = (#extreme+1)/(n+1) smoothing (H4, opt-in; "
        "ties at 0 count as extreme on both sides)."
    ),
)


def percentile_ci(boot_data: FloatArray, alpha: float) -> tuple[float, float]:
    """Percentile bootstrap CI ``np.quantile(boot, [α/2, 1−α/2])`` (baseline §4, not BCa)."""
    left_bound, right_bound = np.quantile(boot_data, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(left_bound), float(right_bound)


def pvalue_sign(boot_data: FloatArray) -> float:
    """Legacy sign-based p-value ``2·min(P(boot>0), P(boot<0))`` (baseline §4, golden parity).

    Counted per side and divided once (M7 WP1 A4) — byte-identical to
    ``2·min(np.mean(boot>0), np.mean(boot<0))``: a boolean mean is an exact
    integer count over ``n``, and division by a positive constant is monotone,
    so ``min`` commutes with it.
    """
    n_positive = int(np.count_nonzero(boot_data > 0.0))
    n_negative = int(np.count_nonzero(boot_data < 0.0))
    return float(2.0 * (min(n_positive, n_negative) / boot_data.size))


def pvalue_plugin(boot_data: FloatArray) -> float:
    """H4 plug-in p-value ``min(1, 2·min(#(boot≥0)+1, #(boot≤0)+1)/(n+1))``.

    Never exactly 0 (distinguishable from ``p < 1/n_samples``); ties at exactly
    0 count as extreme on both sides (conservative — see module docstring).
    """
    n = int(boot_data.size)
    n_at_or_above = int(np.count_nonzero(boot_data >= 0.0))
    n_at_or_below = int(np.count_nonzero(boot_data <= 0.0))
    return min(1.0, 2.0 * min(n_at_or_above + 1, n_at_or_below + 1) / (n + 1))


def bootstrap_pvalue(boot_data: FloatArray, kind: str) -> float:
    """Dispatch on ``pvalue_kind`` (see :data:`PVALUE_KIND_PARAM`)."""
    if kind == "plugin":
        return pvalue_plugin(boot_data)
    if kind == "sign":
        return pvalue_sign(boot_data)
    raise MethodParamError(f"unknown pvalue_kind {kind!r}; choices: ('plugin', 'sign')")
