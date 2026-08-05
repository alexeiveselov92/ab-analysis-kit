"""The relative effect's interval: the delta shortcut, or Fieller (m13 STAT-4).

The estimand is ``θ = a/b`` with ``a`` the numerator effect (the arm difference,
CUPED-adjusted or not) and ``b`` the control mean — exactly the four moments
:func:`~abkit.stats.effects.relative_delta_effect` already takes, plus their
covariance ``Cov(a, b)`` (non-zero because the two share the control arm; for the
plain t-test it is ``−Var(m̂₁)``).

**Delta** (the legacy branch, ``interval: delta``) evaluates the variance at the
ESTIMATE ``θ̂`` and reports ``θ̂ ± z·SE`` — a Wald interval. Wald tests are not
invariant to reparametrisation, so the relative interval and the absolute
p-value printed beside it are two different tests of ONE hypothesis and can
disagree: "the absolute effect is significant" next to "the lift CI contains 0".

**Fieller** (``interval: fieller``) evaluates the variance at each CANDIDATE θ
and inverts, which is the score test for every θ rather than only at the null:

    { θ : (a − θ·b)² ≤ c²·(V_a − 2θ·V_ab + θ²·V_b) }

a quadratic ``A·θ² − 2B·θ + C ≤ 0`` with ``A = b² − c²V_b``,
``B = ab − c²V_ab``, ``C = a² − c²V_a``. Three consequences, each load-bearing:

- **Coherence, exactly.** ``0`` is in the set ⟺ ``C ≤ 0`` ⟺ ``|a| ≤ c·√V_a`` —
  the absolute test. So "the lift interval excludes zero" and "the absolute
  p-value is below α" are ONE event, at every α and every sample size. That is
  why the p-value reported with a Fieller interval IS the absolute test's (the
  same code, same op order — an equality assertion in the tests, not a
  tolerance), and why switching a series from delta to Fieller cannot move a
  false positive at the null while it does move every endpoint away from it.
- **Both one-sided error rates land on their nominal α/2**, which delta's do not.
  Delta's *two-sided* coverage is nominal, so nothing that counts misses without
  regard to side can see the defect; its tails are 0.0168/0.0327 at a
  control-mean CV of 5% and 0.0083/0.0393 at 10%, and the imbalance depends on
  the denominator's noise rather than on the true effect — identical at θ = 0 and
  θ = +0.5. Every abkit verdict (WIN, LOSE) is a one-sided claim.
- **An unbounded branch, which is the honest answer and not a defect.** When
  ``A ≤ 0`` — i.e. ``g = c²V_b/b² ≥ 1``, the control mean is not distinguishable
  from zero at this α — no bounded confidence set for a ratio exists at level
  1−α (Gleser & Hwang 1987: any procedure with guaranteed coverage must produce
  unbounded sets with positive probability, so delta's always-finite interval
  has guaranteed coverage ZERO). abkit reports the point estimate and the
  p-value and leaves the bounds NULL, which every reader already treats as a
  gap rather than a zero (``readout._informative``).

Purity: numpy + stdlib only, arrays in and arrays out. The scalar entry wraps
its five moments in length-1 arrays, so there is exactly one implementation of
this math in the package and scalar↔batch parity is bit-exact by construction
(the m13 STAT-3 discipline, itself the M7 WP2 one).
"""

from __future__ import annotations

import math

import numpy as np
import scipy.special as special

from abkit.stats.effects import (
    H5_UNDEFINED_DENOMINATOR,
    H5_UNSTABLE,
    BatchEffectResult,
    FloatArray,
    NormalTest,
    _two_sided_quantiles,
    normal_test,
    normal_test_array,
    relative_delta_effect,
    relative_delta_effect_array,
)

#: ``interval`` values this module dispatches on — the choices of
#: :data:`~abkit.stats.base.RELATIVE_INTERVAL_PARAM`, kept here so the dispatch
#: and the schema cannot drift apart.
DELTA = "delta"
FIELLER = "fieller"

FIELLER_UNBOUNDED_WARNING = (
    "relative interval unbounded: the control mean is not distinguishable from zero at "
    "this alpha (z²·Var(control mean)/mean² >= 1), so no bounded relative confidence set "
    "exists at this level (Fieller; Gleser-Hwang 1987) — the effect and p-value stand, "
    "the bounds are reported as missing rather than guessed"
)

FIELLER_DEGENERATE_WARNING = (
    "relative interval undefined: the effect's variance is zero or non-finite "
    "(degenerate samples); returning NaN test outputs"
)

FIELLER_EMPTY_WARNING = (
    "relative confidence set is empty: no candidate lift is consistent with the moments "
    "(anomalous covariance term — possible with the mixed-ddof convention on adversarial "
    "data, the same anomaly the delta branch reports as a negative variance); "
    "returning NaN bounds"
)


def _asarray(value: object) -> FloatArray:
    return np.asarray(value, dtype=np.float64)


def leading_coefficient(
    mean_den: FloatArray | float, var_den: FloatArray | float, critical: float
) -> FloatArray:
    """``A = b² − c²V_b`` — positive exactly when a bounded interval exists.

    Exported because the caller must distinguish the two ways bounds go missing
    (``A ≤ 0`` is "the control mean is not distinguishable from zero"; ``A > 0``
    with no crossing is an anomalous covariance) and re-deriving the expression
    at the call site is how the two drift apart.
    """
    return _asarray(mean_den) * _asarray(mean_den) - critical * critical * _asarray(var_den)


def fieller_bounds(
    mean_num: FloatArray,
    var_num: FloatArray,
    mean_den: FloatArray,
    var_den: FloatArray,
    covariance: FloatArray,
    critical: float,
) -> tuple[FloatArray, FloatArray]:
    """``{θ : (a − θb)² ≤ c²(V_a − 2θV_ab + θ²V_b)}`` as ``(left, right)``.

    NaN on both bounds where the set is not a bounded interval — see the module
    docstring's third bullet. With a positive-semidefinite moment matrix ``A > 0``
    forces ``disc ≥ 0`` (``A > 0 ⇒ b²V_a > c²V_aV_b``, and ``disc = c²(b²V_a −
    2abV_ab + a²V_b) − c⁴(V_aV_b − V_ab²)``), so a downward-opening parabola is
    then the only way to lose boundedness. The ``disc ≥ 0`` guard is nonetheless
    real, because the triple is NOT guaranteed PSD: abkit's mixed-ddof convention
    can produce ``V_ab² > V_aV_b`` on adversarial data — the same anomaly
    :func:`~abkit.stats.effects.normal_test` already reports as a negative
    variance — and the confidence set is then EMPTY rather than unbounded.

    ONE numerical choice, and it is MEASURED rather than assumed: the
    discriminant is taken in the **cancellation-free** form above rather than as
    ``B² − AC``, whose leading ``a²b²`` terms cancel. The naive form's relative
    error is ``ε·(z_stat/z)²``, so it stays under the house rel-1e-9 until
    ``|z_stat| ≈ 10⁴`` — unreachable in practice, which is why the
    30×-better-and-free form is the one to keep rather than the one to argue
    about (pinned by a Decimal-referenced test at ``z_stat = 10⁴``, where the
    naive form reads 6.5e-10 and this one 2e-13).

    The textbook ``s = B + sign(B)√disc`` root pairing is deliberately NOT used.
    It is the standard defence against cancellation in ``B − √disc``, and here
    it buys nothing measurable (2.3e-15 vs 3.3e-15 relative to the interval's
    width, and it is the *worse* of the two at three of four probed regimes):
    the cancellation only bites when the left endpoint is near zero, i.e. right
    at the significance boundary, where an endpoint's own relative accuracy is
    not what any reader is using. Carrying it would have meant a branch no test
    could justify.
    """
    a_num, v_num = _asarray(mean_num), _asarray(var_num)
    a_den, v_den = _asarray(mean_den), _asarray(var_den)
    cov = _asarray(covariance)
    squared = critical * critical

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        quadratic = leading_coefficient(a_den, v_den, critical)
        linear = a_num * a_den - squared * cov
        form = a_den * a_den * v_num - 2.0 * a_num * a_den * cov + a_num * a_num * v_den
        determinant = v_num * v_den - cov * cov
        discriminant = squared * form - squared * squared * determinant

        root = np.sqrt(np.maximum(discriminant, 0.0))
        bounded = (
            (quadratic > 0.0)
            & (discriminant >= 0.0)
            & np.isfinite(quadratic)
            & np.isfinite(linear)
            & np.isfinite(root)
        )
        # A > 0 on every bounded row, so the two roots come out already ordered
        # and the divisor is never zero — the guard below only keeps the eagerly
        # evaluated other branch from raising.
        divisor = np.where(bounded, quadratic, 1.0)
        left = np.where(bounded, (linear - root) / divisor, np.nan)
        right = np.where(bounded, (linear + root) / divisor, np.nan)
    return left, right


def _fieller_pieces(
    mean_num: FloatArray,
    var_num: FloatArray,
    mean_den: FloatArray,
    var_den: FloatArray,
    covariance: FloatArray,
    alpha: float,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray]:
    """``(effect, left, right, ci_length, pvalue)`` for the Fieller branch.

    The p-value is the ABSOLUTE two-sample p-value, computed with the same
    expression and operand order as :func:`~abkit.stats.effects.normal_test`'s
    — that is what makes the coherence above an equality rather than an
    approximation, and it is pinned by a test comparing the two branches' p
    with ``==``.
    """
    _, z_high = _two_sided_quantiles(alpha)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        undefined = (mean_den == 0.0) | ~np.isfinite(mean_den)
        effect = np.where(undefined, np.nan, mean_num / np.where(undefined, 1.0, mean_den))
        usable = (
            np.isfinite(effect)
            & np.isfinite(var_num)
            & (var_num > 0.0)
            & np.isfinite(var_den)
            & np.isfinite(covariance)
        )
        effect = np.where(usable, effect, np.nan)

        left, right = fieller_bounds(mean_num, var_num, mean_den, var_den, covariance, z_high)
        left = np.where(usable, left, np.nan)
        right = np.where(usable, right, np.nan)

        scale = np.sqrt(np.where(usable, var_num, np.nan))
        z_zero = (0.0 - mean_num) / scale  # cdf(0) standardization, scipy op order
        pvalue = 2.0 * np.minimum(special.ndtr(z_zero), special.ndtr(-z_zero))
    return effect, left, right, right - left, pvalue


def relative_normal_test(
    *,
    mean_num: float,
    var_num: float,
    mean_den: float,
    var_den: float,
    covariance: float,
    alpha: float,
    interval: str,
) -> NormalTest:
    """The relative branch of every closed-form mean method — one dispatch point.

    ``interval="delta"`` is the legacy path, reached through the untouched
    :func:`~abkit.stats.effects.relative_delta_effect` +
    :func:`~abkit.stats.effects.normal_test` pair, so the default is byte-frozen.
    ``interval="fieller"`` computes the same point estimate, the inverted-test
    bounds, and the absolute p-value; it reports no ``effect_distribution``,
    because a Fieller set is not the quantile range of any normal.
    """
    if interval == DELTA:
        return normal_test(
            relative_delta_effect(
                mean_num=mean_num,
                var_num=var_num,
                mean_den=mean_den,
                var_den=var_den,
                covariance=covariance,
            ),
            alpha,
        )

    batch = np.array([mean_num]), np.array([var_num])
    denominator = np.array([mean_den]), np.array([var_den])
    effect, left, right, ci_length, pvalue = _fieller_pieces(
        *batch, *denominator, np.array([covariance]), alpha
    )
    # Five causes, five sentences: a reader told "near-zero control mean" about a
    # zero-variance table, or "unbounded" about an EMPTY set, goes looking at the
    # wrong half of their data.
    result_warnings: list[str] = []
    _, z_high = _two_sided_quantiles(alpha)
    if mean_den == 0.0 or not math.isfinite(mean_den):
        result_warnings.append(H5_UNDEFINED_DENOMINATOR)
    elif not math.isfinite(mean_num / mean_den):
        result_warnings.append(H5_UNSTABLE)
    elif not (
        math.isfinite(var_num)
        and var_num > 0.0
        and math.isfinite(var_den)
        and math.isfinite(covariance)
    ):
        result_warnings.append(FIELLER_DEGENERATE_WARNING)
    elif not math.isfinite(float(left[0])):
        bounded_shape = float(leading_coefficient(mean_den, var_den, z_high)) > 0.0
        result_warnings.append(
            FIELLER_EMPTY_WARNING if bounded_shape else FIELLER_UNBOUNDED_WARNING
        )
    return NormalTest(
        effect=float(effect[0]),
        left_bound=float(left[0]),
        right_bound=float(right[0]),
        ci_length=float(ci_length[0]),
        pvalue=float(pvalue[0]),
        reject=bool(float(pvalue[0]) < alpha),
        distribution=None,
        warnings=result_warnings,
    )


def relative_normal_test_array(
    *,
    mean_num: FloatArray,
    var_num: FloatArray,
    mean_den: FloatArray,
    var_den: FloatArray,
    covariance: FloatArray,
    alpha: float,
    interval: str,
) -> BatchEffectResult:
    """Array-wise :func:`relative_normal_test` — the validate hot path (M7 WP2).

    Degenerate and unbounded rows are NaN, never an exception and never a zero
    ("gaps, never zeros"); the scalar entry's warning strings have no place on
    this path, which is why the two share ``_fieller_pieces`` rather than a
    result type.
    """
    if interval == DELTA:
        effect, var = relative_delta_effect_array(
            mean_num=mean_num,
            var_num=var_num,
            mean_den=mean_den,
            var_den=var_den,
            covariance=covariance,
        )
        return normal_test_array(effect, var, alpha)

    effect, left, right, ci_length, pvalue = _fieller_pieces(
        mean_num, var_num, mean_den, var_den, covariance, alpha
    )
    return BatchEffectResult(
        effect=effect,
        left_bound=left,
        right_bound=right,
        ci_length=ci_length,
        pvalue=pvalue,
    )
