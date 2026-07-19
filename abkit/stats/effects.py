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
from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt
import scipy.special as special
import scipy.stats as sps

FloatArray = npt.NDArray[np.float64]


@lru_cache(maxsize=64)
def _two_sided_quantiles(alpha: float) -> tuple[float, float]:
    """``(ndtri(alpha/2), ndtri(1 − alpha/2))`` — data-independent, cached per alpha.

    Bit-identical to the frozen ``sps.norm.ppf`` pair it replaces (M7 WP1 A1):
    scipy's ``norm._ppf`` IS ``ndtri``, and only a handful of distinct alphas
    exist per process (the declared per-comparison alphas), so the pair is
    computed once instead of per comparison.
    """
    return float(special.ndtri(alpha / 2.0)), float(special.ndtri(1.0 - alpha / 2.0))


class LazyNormal:
    """A lazily-frozen ``scipy.stats.norm(loc, scale)`` stand-in (M7 WP1 A3).

    Freezing a scipy distribution costs ~190 µs — orders more than the
    ndtri/ndtr math around it — and ``TestResult.effect_distribution`` is
    write-only on the validate/family hot path (``to_dict`` drops it). The
    proxy keeps the ``is not None`` truthiness contract; the first attribute
    read freezes the real ``sps.norm(loc, scale)`` and every read delegates to
    it, so downstream ``.cdf``/``.ppf``/``.sf`` results are byte-identical to
    the eager object's.
    """

    __slots__ = ("loc", "scale", "_frozen")

    def __init__(self, loc: float, scale: float) -> None:
        self.loc = loc
        self.scale = scale
        self._frozen: Any = None

    def _materialize(self) -> Any:
        if self._frozen is None:
            self._frozen = sps.norm(self.loc, self.scale)
        return self._frozen

    def __getattr__(self, name: str) -> Any:
        # Reached only for names not on the proxy itself (slots always resolve).
        # Never materialize for underscore/dunder probes: pickle/copy/display
        # protocols probe them on half-initialised instances (unset slots), which
        # would otherwise recurse through _materialize forever — and a mere
        # hasattr probe must not defeat the laziness.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._materialize(), name)

    def __reduce__(self) -> tuple[Any, tuple[float, float]]:
        # The frozen distribution is a cache — rebuild lazily after unpickling.
        return (type(self), (self.loc, self.scale))

    def __repr__(self) -> str:
        return f"LazyNormal(loc={self.loc!r}, scale={self.scale!r})"


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

    M7 WP1 (A1/A3): the frozen-norm formulas are evaluated directly via
    ``scipy.special.ndtri``/``ndtr`` — the sf tail as ``ndtr(-z)``, never
    ``1 − ndtr(z)``, which is NOT bit-identical for extreme z — and the
    effect distribution is a :class:`LazyNormal`. Byte parity with the
    pre-WP1 ``sps.norm`` path is pinned by ``test_normal_path_golden.py``.
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

    scale = float(np.sqrt(estimate.var))
    z_low, z_high = _two_sided_quantiles(alpha)
    left_bound = z_low * scale + estimate.effect
    right_bound = z_high * scale + estimate.effect
    z_zero = (0.0 - estimate.effect) / scale  # cdf(0) standardization, scipy op order
    pvalue = float(2.0 * min(special.ndtr(z_zero), special.ndtr(-z_zero)))
    return NormalTest(
        effect=estimate.effect,
        left_bound=left_bound,
        right_bound=right_bound,
        ci_length=right_bound - left_bound,
        pvalue=pvalue,
        reject=bool(pvalue < alpha),
        distribution=LazyNormal(estimate.effect, scale),
        warnings=result_warnings,
    )


# --- the array-wise significance kernel (M7 WP2) ----------------------------------
#
# The validate hot path calls the significance test once per (iteration, cutoff)
# pair — hundreds of thousands of scalar calls per cell. These kernels are the
# strictly additive array-wise siblings of the scalar functions above
# (docs/specs/m7-implementation-plan.md §WP2): the SAME formulas evaluated with
# numpy broadcasting, one row per comparison, with the alpha-only quantiles
# computed once. The scalar paths are untouched; parity is pinned row-by-row by
# tests/stats/test_vectorized_parity.py.
#
# Degenerate rows follow the "gaps, never zeros" contract of the scalar H5/NaN
# branches: NaN outputs in place of per-row warning strings (validate never
# reads warnings on this path), never an exception and never a silent zero.
#
# Power terms go through :func:`_libm_pow`, NOT numpy's ``**`` (adversarial
# review round 1): numpy's integer-exponent power fast paths (multiply chains)
# are not bit-identical to CPython's libm-``pow``-backed scalar ``**`` (glibc
# pow is within ~0.5 ulp, not correctly rounded — even ``x**2`` can differ by
# 1 ULP), and the three-term delta-method variance sum cancels catastrophically
# for adversarial magnitude mixes, amplifying a 1-ULP term divergence far past
# rel-1e-9 on the CI bounds (measured up to ~1.8e-4 in a fuzz before the fix).
# Routing the array path through the SAME libm pow makes scalar↔array parity
# exact BY CONSTRUCTION, on every platform — which is what lets the WP5
# count-exactness gate rest on structure rather than luck.


def _pow_or_inf(base: float, exponent: float) -> float:
    """libm ``pow`` with IEEE overflow semantics (``±inf``, never a raise).

    The scalar path's ``x ** k`` raises ``OverflowError`` past ~1e308 (a
    pre-existing scalar hazard outside H5's reach); a batch must never be
    poisoned by one such row, so overflow maps to the IEEE result instead.
    """
    try:
        return math.pow(base, exponent)
    except OverflowError:
        return math.inf if base > 0.0 or exponent % 2.0 == 0.0 else -math.inf


_LIBM_POW = np.frompyfunc(_pow_or_inf, 2, 1)


def _libm_pow(x: FloatArray, exponent: float) -> FloatArray:
    """Elementwise ``x ** exponent`` via the exact libm ``pow`` the scalar path uses."""
    return _LIBM_POW(x, exponent).astype(np.float64)


@dataclass
class BatchEffectResult:
    """Array-wise significance results — the slim, validate-only result rows.

    One position per comparison row; NaN rows mark degenerate inputs (the
    scalar NaN/H5 branches). Deliberately carries ONLY the five fields the
    validate scoring loop reads (``effect``/bounds/``ci_length``/``pvalue``) —
    no ``effect_distribution``, ``mde_*``, ``name_*``, per-arm stats or
    ``warnings`` (all write-only on that path, m7-implementation-plan.md §0.1).
    This is NOT a :class:`~abkit.stats.result.TestResult` replacement: the
    pipeline/explore/report contract stays ``TestResult`` — never wire
    ``abk run`` through this type.
    """

    effect: FloatArray
    left_bound: FloatArray
    right_bound: FloatArray
    ci_length: FloatArray
    pvalue: FloatArray


def absolute_effect_array(
    mean_1: FloatArray, mean_2: FloatArray, var_mean_1: FloatArray, var_mean_2: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Array-wise :func:`absolute_effect`: ``(effect, var)`` per row, same formulas.

    Self-contained ``np.errstate`` like every sibling kernel — a non-finite row
    (e.g. ``inf − inf``) must not warn even when a caller invokes this outside
    its own errstate block.
    """
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        return mean_2 - mean_1, var_mean_1 + var_mean_2


def relative_delta_effect_array(
    mean_num: FloatArray,
    var_num: FloatArray,
    mean_den: FloatArray,
    var_den: FloatArray,
    covariance: FloatArray,
) -> tuple[FloatArray, FloatArray]:
    """Array-wise :func:`relative_delta_effect`: ``(effect, var)`` per row.

    The H5 hygiene guard (zero/non-finite denominator mean → NaN) and the
    numeric-instability guard (non-finite mu/var → NaN) become boolean masks.
    ``np.where`` evaluates both branches eagerly, so the kernel body runs under
    ``np.errstate`` — a degenerate row must never raise or warn (the array
    mirror of the scalar guard's early return).
    """
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        relative_mu = mean_num / mean_den
        # Power terms via libm pow — see the module-section comment: numpy's
        # `**` is 1-ULP-off the scalar path's and the cancelling sum amplifies.
        relative_var = (
            var_num / _libm_pow(mean_den, 2.0)
            + var_den * (_libm_pow(mean_num, 2.0) / _libm_pow(mean_den, 4.0))
            - 2.0 * (mean_num / _libm_pow(mean_den, 3.0)) * covariance
        )
        degenerate = (
            (mean_den == 0.0)
            | ~np.isfinite(mean_den)
            | ~np.isfinite(relative_mu)
            | ~np.isfinite(relative_var)
        )
        effect = np.where(degenerate, np.nan, relative_mu)
        var = np.where(degenerate, np.nan, relative_var)
    return effect, var


def normal_test_array(effect: FloatArray, var: FloatArray, alpha: float) -> BatchEffectResult:
    """Array-wise :func:`normal_test` (baseline §3.1, the WP1 ndtri/ndtr form).

    Per the scalar NaN branch: a degenerate row (non-finite effect/var or
    ``var <= 0``) keeps its ``effect`` value verbatim and gets NaN
    bounds/``ci_length``/``pvalue`` — a mask in place of the per-case warning
    strings. ``reject`` is deliberately absent: validate derives significance
    from the CI bounds (finite ``left > 0`` / ``right < 0``), never from a
    pre-baked flag.
    """
    effect = np.asarray(effect, dtype=np.float64)
    var = np.asarray(var, dtype=np.float64)
    z_low, z_high = _two_sided_quantiles(alpha)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        degenerate = ~np.isfinite(effect) | ~np.isfinite(var) | (var <= 0.0)
        # NaN-poisoning the scale propagates NaN through bounds/pvalue exactly
        # like the scalar early return — no second masking pass needed.
        scale = np.sqrt(np.where(degenerate, np.nan, var))
        left_bound = z_low * scale + effect
        right_bound = z_high * scale + effect
        z_zero = (0.0 - effect) / scale  # cdf(0) standardization, scipy op order
        pvalue = 2.0 * np.minimum(special.ndtr(z_zero), special.ndtr(-z_zero))
        ci_length = right_bound - left_bound
    return BatchEffectResult(
        effect=effect,
        left_bound=left_bound,
        right_bound=right_bound,
        ci_length=ci_length,
        pvalue=pvalue,
    )
