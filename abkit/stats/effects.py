"""Effect estimands and the preserved delta-method linearisation.

The relative effect ``(mean_2 − mean_1) / mean_1`` is NOT a naive ratio of
variances — it is a first-order Taylor (delta-method) linearisation that keeps
the covariance between numerator and denominator (they share ``mean_1``). This is
the single most important formula to preserve (docs/specs/statistics-baseline.md
§2–§3):

    relative_mu  = mean_num / mean_den
    relative_var = var_num / mean_den²
                 + var_den · mean_num² / mean_den⁴
                 − 2 · (mean_num / mean_den³) · covariance

Hygiene fix H5 (docs/specs/statistics-changes.md): division by a zero control
mean yields NaN plus a recorded warning instead of silent ±inf.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import scipy.stats as sps


@dataclass
class EffectEstimate:
    """A point effect with its (delta-method) variance and any diagnostics."""

    effect: float
    var: float
    warnings: list[str] = field(default_factory=list)


def absolute_effect(
    mean_1: float, mean_2: float, var_mean_1: float, var_mean_2: float
) -> EffectEstimate:
    """Absolute estimand: ``mean_2 − mean_1`` (variant minus control)."""
    return EffectEstimate(effect=mean_2 - mean_1, var=var_mean_1 + var_mean_2)


def relative_delta_effect(
    mean_num: float,
    var_num: float,
    mean_den: float,
    var_den: float,
    covariance: float,
) -> EffectEstimate:
    """The preserved delta-method relative effect (baseline §3.1/§3.3).

    ``covariance`` is the covariance between the numerator and denominator
    estimators — for the plain t-test relative effect it is ``−var_mean_1``
    (numerator ``mean_2 − mean_1`` and denominator ``mean_1`` share ``mean_1``).
    """
    result_warnings: list[str] = []
    if mean_den == 0.0 or not math.isfinite(mean_den):
        result_warnings.append(
            "relative effect undefined: control (denominator) mean is zero or non-finite; "
            "returning NaN (see statistics-changes.md H5)"
        )
        return EffectEstimate(effect=float("nan"), var=float("nan"), warnings=result_warnings)

    relative_mu = mean_num / mean_den
    relative_var = (
        var_num / mean_den**2
        + var_den * (mean_num**2 / mean_den**4)
        - 2.0 * (mean_num / mean_den**3) * covariance
    )
    if not (math.isfinite(relative_mu) and math.isfinite(relative_var)):
        result_warnings.append(
            "relative effect numerically unstable (near-zero control mean); "
            "returning NaN (see statistics-changes.md H5)"
        )
        return EffectEstimate(effect=float("nan"), var=float("nan"), warnings=result_warnings)
    return EffectEstimate(effect=relative_mu, var=relative_var, warnings=result_warnings)


@dataclass
class NormalTest:
    """CI, p-value and reject flag from a Normal effect distribution."""

    effect: float
    left_bound: float
    right_bound: float
    ci_length: float
    pvalue: float
    reject: bool
    distribution: Any | None
    warnings: list[str] = field(default_factory=list)


def normal_test(estimate: EffectEstimate, alpha: float) -> NormalTest:
    """The shared parametric result computation (baseline §3.1).

    ``left, right = norm(mu, sqrt(var)).ppf([alpha/2, 1 − alpha/2])``;
    ``pvalue = 2 · min(cdf(0), sf(0))``; ``reject = pvalue < alpha``.
    """
    result_warnings = list(estimate.warnings)
    if not (math.isfinite(estimate.effect) and math.isfinite(estimate.var)) or estimate.var <= 0.0:
        if estimate.var == 0.0 and math.isfinite(estimate.effect):
            result_warnings.append(
                "effect variance is zero (degenerate samples); returning NaN test outputs"
            )
        elif math.isfinite(estimate.var) and estimate.var < 0.0:
            result_warnings.append(
                "effect variance is negative (anomalous covariance term — possible with the "
                "mixed-ddof convention on adversarial data); returning NaN test outputs"
            )
        elif not result_warnings:
            result_warnings.append(
                "effect or its variance is non-finite; returning NaN test outputs"
            )
        nan = float("nan")
        return NormalTest(
            effect=estimate.effect,
            left_bound=nan,
            right_bound=nan,
            ci_length=nan,
            pvalue=nan,
            reject=False,
            distribution=None,
            warnings=result_warnings,
        )

    distribution = sps.norm(loc=estimate.effect, scale=float(np.sqrt(estimate.var)))
    left_bound, right_bound = (
        float(bound) for bound in distribution.ppf([alpha / 2.0, 1.0 - alpha / 2.0])
    )
    pvalue = float(2.0 * min(distribution.cdf(0.0), distribution.sf(0.0)))
    return NormalTest(
        effect=estimate.effect,
        left_bound=left_bound,
        right_bound=right_bound,
        ci_length=right_bound - left_bound,
        pvalue=pvalue,
        reject=bool(pvalue < alpha),
        distribution=distribution,
        warnings=result_warnings,
    )
