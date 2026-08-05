"""m13 STAT-4: the Fieller relative interval — the derivation's KATs.

The reference for every claim here is the OBJECTIVE — the membership inequality
``(a − θb)² ≤ c²(V_a − 2θV_ab + θ²V_b)`` transcribed independently of the
``A/B/C`` coefficients the implementation forms — and not a second root-finder.
That is STAT-3's transferable lesson: a solver checked against another solver
agrees with it on the shared mistake, and a transposed coefficient's error
vanishes at the null, so the coherence tests sail past it.

Monte-Carlo legs use SUFFICIENT STATISTICS drawn directly from the normal model
Fieller assumes (known arm variances), which is what isolates the interval
construction from variance estimation; they are seeded, so they are
deterministic rather than merely probable.
"""

from __future__ import annotations

import math
from decimal import Decimal, getcontext

import numpy as np
import pytest

from abkit.stats.effects import normal_test_array, relative_delta_effect_array
from abkit.stats.relative_interval import (
    FIELLER_DEGENERATE_WARNING,
    FIELLER_EMPTY_WARNING,
    FIELLER_UNBOUNDED_WARNING,
    fieller_bounds,
    leading_coefficient,
    relative_normal_test,
)

Z95 = 1.959963984540054
SEED = 20260805


def _arm_moments(mean_1: float, mean_2: float, var_1: float, var_2: float):
    """``(a, V_a, b, V_b, V_ab)`` for the plain two-independent-arms shape."""
    return mean_2 - mean_1, var_1 + var_2, mean_1, var_1, -var_1


def _bounds(moments, critical: float = Z95):
    left, right = fieller_bounds(*(np.array([value]) for value in moments), critical)
    return float(left[0]), float(right[0])


def _inside(theta: float, moments, critical: float = Z95) -> bool:
    """The DEFINITION, transcribed here and nowhere near the implementation."""
    a_num, v_num, a_den, v_den, cov = moments
    left = (a_num - theta * a_den) ** 2
    right = critical**2 * (v_num - 2.0 * theta * cov + theta * theta * v_den)
    return bool(left <= right)


# --- the closed form the derivation states (§5, branch 1) ----------------------------


def test_it_matches_the_derivations_closed_form_for_independent_arms() -> None:
    """``(1/(1−g))·[R̂ ± (z/|m̂₁|)·√(R̂²V̂₁ + (1−g)V̂₂)] − 1``, g = z²V̂₁/m̂₁².

    An independent transcription of the same set from the derivation's own
    algebra, so it fails on any error in the coefficients — and it is stated in
    terms of ``R`` where the implementation works in ``θ``, which is where a
    forgotten ``−1`` would show.
    """
    rng = np.random.default_rng(SEED)
    worst = 0.0
    for _ in range(500):
        mean_1 = rng.uniform(0.5, 5.0)
        mean_2 = mean_1 * rng.uniform(0.5, 2.0)
        var_1 = (mean_1 * rng.uniform(1e-4, 0.15)) ** 2
        var_2 = (mean_2 * rng.uniform(1e-4, 0.15)) ** 2
        moments = _arm_moments(mean_1, mean_2, var_1, var_2)
        left, right = _bounds(moments)

        g = Z95**2 * var_1 / mean_1**2
        assert g < 1.0  # the sampled regime is the bounded branch by construction
        ratio = mean_2 / mean_1
        half = (Z95 / abs(mean_1)) * math.sqrt(ratio * ratio * var_1 + (1.0 - g) * var_2)
        expected_left = (ratio - half) / (1.0 - g) - 1.0
        expected_right = (ratio + half) / (1.0 - g) - 1.0
        worst = max(
            worst,
            abs(left - expected_left) / abs(expected_left),
            abs(right - expected_right) / abs(expected_right),
        )
    assert worst < 1e-9, worst


def test_the_endpoints_solve_the_membership_objective() -> None:
    """Just inside is in the set, just outside is not — the mutation-proof leg.

    This is the only test here that fails on a transposed coefficient (swapping
    ``V_a`` and ``V_b``, or dropping the ``θ²`` factor): such an error leaves a
    perfectly plausible interval whose endpoints simply are not the crossings,
    and it vanishes identically at ``θ = 0``, where every coherence check looks.
    """
    cases = [
        _arm_moments(10.0, 11.0, 0.04, 0.05),
        _arm_moments(0.02, 0.024, 4e-8, 5e-8),
        _arm_moments(1.0, 0.6, 0.002, 0.001),
        # a genuinely non-``−V_b`` covariance: the CUPED shape, where the
        # numerator is the adjusted difference and the denominator the RAW mean.
        (0.5, 0.02, 4.0, 0.01, -0.004),
    ]
    for moments in cases:
        left, right = _bounds(moments)
        assert math.isfinite(left) and math.isfinite(right)
        width = right - left
        step = 1e-7 * width
        assert _inside(left + step, moments), moments
        assert _inside(right - step, moments), moments
        assert not _inside(left - step, moments), moments
        assert not _inside(right + step, moments), moments
        assert _inside(0.5 * (left + right), moments), moments


def test_the_discriminant_keeps_its_digits_where_the_naive_form_loses_them() -> None:
    """The cancellation-free ``disc`` is a live choice, so it gets a live gate.

    Reference: the same quadratic solved in 60-digit ``Decimal``. At
    ``z_stat = 10⁴`` — the regime where ``B² − AC`` cancels down to 6.5e-10
    relative, i.e. past the project's rel-1e-9 floor — the shipped form must
    stay three orders below it. Swapping the expression back is what this test
    exists to turn red; nothing else in the file can see the difference.
    """
    n = 1e11
    prop_1 = 0.01
    prop_2 = prop_1 * 1.5
    var_1, var_2 = prop_1 * (1 - prop_1) / n, prop_2 * (1 - prop_2) / n
    moments = _arm_moments(prop_1, prop_2, var_1, var_2)
    assert abs(moments[0]) / math.sqrt(moments[1]) == pytest.approx(1e4, rel=0.05)

    getcontext().prec = 60
    a_num, v_num, a_den, v_den, cov = (Decimal(value) for value in moments)
    critical = Decimal(Z95)
    quadratic = a_den * a_den - critical * critical * v_den
    linear = a_num * a_den - critical * critical * cov
    constant = a_num * a_num - critical * critical * v_num
    root = (linear * linear - quadratic * constant).sqrt()
    exact = sorted(((linear - root) / quadratic, (linear + root) / quadratic))

    left, right = _bounds(moments)
    width = float(exact[1] - exact[0])
    for got, expected in ((left, exact[0]), (right, exact[1])):
        assert abs(float(Decimal(got) - expected)) / width < 1e-12, (got, expected)


def test_an_exactly_vanishing_leading_coefficient_is_unbounded_not_infinite() -> None:
    """``A == 0`` exactly — the boundary the ``> 0`` guard exists for.

    Found by search rather than by construction, because ``b² − z²V_b`` cancelling
    to exactly zero is a float coincidence and a fixture that merely *aims* at it
    lands one ULP away, where a ``>=`` guard passes. With ``>=`` the divisor is
    zero and the row reports an infinite endpoint as if it were an answer.
    """
    mean_den = 1.000000001
    var_den = 0.2603177721476413
    assert mean_den * mean_den - Z95 * Z95 * var_den == 0.0  # the coincidence, pinned
    left, right = _bounds((0.3, 2.0, mean_den, var_den, 0.0))
    assert math.isnan(left) and math.isnan(right)


def test_the_covariance_argument_is_actually_consulted() -> None:
    """Two moment sets differing ONLY in ``V_ab`` must give different endpoints.

    Without this, an implementation that silently used ``−V_b`` everywhere would
    pass every other test in this file — and CUPED, whose numerator is adjusted
    while its denominator is not, is exactly where that assumption is false.
    """
    base = (0.5, 0.02, 4.0, 0.01, -0.01)
    shifted = (0.5, 0.02, 4.0, 0.01, -0.004)
    assert _bounds(base) != _bounds(shifted)


# --- coherence: the whole reason this construction was chosen ------------------------


@pytest.mark.parametrize("scale", [1.0, 1e-4, 1e4])
def test_zero_is_in_the_set_exactly_when_the_absolute_test_does_not_reject(scale: float) -> None:
    """``0 ∈ set ⟺ |a| ≤ z·√V_a`` — swept ACROSS the boundary, not near one point.

    The factor ``1.0`` — the exact boundary — is deliberately absent, and for the
    reason STAT-3 recorded about its own coherence: the two sides are the SAME
    comparison in two roundings (``a² > z²V_a`` here, ``|a| > z√V_a`` there), so a
    table sitting within an ULP of the critical value may be answered differently
    by each. The claim is algebraic, not bit-wise, and the adjacent factors below
    (±0.1% of the critical effect, ~7 orders above the ULP) are what it means.
    """
    var_1 = var_2 = (0.05 * scale) ** 2
    v_num = var_1 + var_2
    critical_effect = Z95 * math.sqrt(v_num)
    for factor in (0.0, 0.5, 0.99, 0.999, 1.001, 1.01, 2.0, -0.999, -1.001, -3.0):
        a_num = factor * critical_effect
        moments = (a_num, v_num, 1.0 * scale, var_1, -var_1)
        left, right = _bounds(moments)
        assert math.isfinite(left), moments
        covers_zero = left <= 0.0 <= right
        assert covers_zero == (abs(a_num) <= critical_effect), (factor, left, right)


def test_it_approaches_the_delta_interval_at_the_rate_the_algebra_predicts() -> None:
    """Fieller − delta is the ``R̂·g/(1−g)`` centre shift, g = z²V̂₁/m̂₁².

    Asserted as a RATE, not as a threshold at one sample size: the gap scales as
    ``g / width ∝ n⁻¹/n⁻¹ᐟ² = n⁻¹ᐟ²``, so every 100× in n must divide it by
    exactly 10. A threshold alone would pass for a wrong-but-small difference;
    the rate pins which difference it is, and a forgotten ``1/(1−g)`` shift
    leaves a gap that does not shrink at all.
    """
    gaps = []
    for n in (1e4, 1e6, 1e8, 1e10):
        mean_1, mean_2 = 2.0, 2.3
        var_1, var_2 = 4.0 / n, 5.0 / n
        moments = _arm_moments(mean_1, mean_2, var_1, var_2)
        left, right = _bounds(moments)
        delta_left, delta_right = _delta_bounds([np.array([value]) for value in moments])
        gaps.append(
            max(abs(left - float(delta_left[0])), abs(right - float(delta_right[0])))
            / (right - left)
        )
    for previous, current in zip(gaps, gaps[1:], strict=False):
        assert current / previous == pytest.approx(0.1, abs=0.005), gaps
    assert gaps[-1] < 1e-5, gaps


# --- the unbounded branch ------------------------------------------------------------


def test_a_control_mean_indistinguishable_from_zero_has_no_bounded_interval() -> None:
    """``g ≥ 1`` ⇒ NaN bounds, and the boundary is where the theory puts it."""
    var_den = 1.0
    for g in (0.81, 0.98, 1.02, 4.0):
        mean_den = Z95 * math.sqrt(var_den / g)
        moments = (0.3, 2.0, mean_den, var_den, 0.0)
        left, right = _bounds(moments)
        assert math.isnan(left) == (g >= 1.0), (g, left)
        assert math.isnan(right) == (g >= 1.0), (g, right)


def test_the_unbounded_branch_still_reports_the_effect_and_the_pvalue() -> None:
    """Gleser-Hwang honesty: no bounds, but the measurement that DOES exist stands."""
    test = relative_normal_test(
        mean_num=3.0,
        var_num=1.0,
        mean_den=0.1,
        var_den=1.0,
        covariance=0.0,
        alpha=0.05,
        interval="fieller",
    )
    assert test.effect == pytest.approx(30.0)
    assert test.pvalue == pytest.approx(2.0 * (1.0 - 0.9986501019683699), rel=1e-6)
    assert math.isnan(test.left_bound) and math.isnan(test.right_bound)
    assert test.warnings == [FIELLER_UNBOUNDED_WARNING]


def test_an_inconsistent_moment_triple_gives_an_EMPTY_set_not_an_unbounded_one() -> None:
    """``A > 0`` with no crossing — reachable only through a non-PSD triple.

    ``V_ab² > V_a·V_b`` is exactly the anomaly abkit's mixed-ddof convention can
    produce and that ``normal_test`` already reports as a negative variance, so
    the branch is a real one and not a theoretical courtesy. The construction is
    the algebra's own: the ``disc < 0`` window in ``V_ab`` is non-empty iff
    ``A·C > 0``, and its centre is ``ab/c²``.
    """
    a_num, v_num, a_den, v_den = 1.0, 0.01, 1.0, 0.01
    covariance = a_num * a_den / (Z95 * Z95)  # the vertex of the disc-in-V_ab parabola
    assert covariance**2 > v_num * v_den  # the triple is genuinely inconsistent
    assert float(leading_coefficient(a_den, v_den, Z95)) > 0.0  # bounded SHAPE
    left, right = _bounds((a_num, v_num, a_den, v_den, covariance))
    assert math.isnan(left) and math.isnan(right)

    test = relative_normal_test(
        mean_num=a_num,
        var_num=v_num,
        mean_den=a_den,
        var_den=v_den,
        covariance=covariance,
        alpha=0.05,
        interval="fieller",
    )
    assert test.warnings == [FIELLER_EMPTY_WARNING]


def test_each_refusal_says_which_cause_fired() -> None:
    """A reader told "near-zero control mean" about a zero-variance table looks
    at the wrong half of their data — so each cause gets its own sentence.

    The fifth (an empty set) has its own test above; the four here are the ones
    reachable from ordinary data.
    """
    common = {"alpha": 0.05, "interval": "fieller"}
    undefined = relative_normal_test(
        mean_num=1.0, var_num=1.0, mean_den=0.0, var_den=1.0, covariance=0.0, **common
    )
    degenerate = relative_normal_test(
        mean_num=1.0, var_num=0.0, mean_den=1.0, var_den=0.0, covariance=0.0, **common
    )
    unbounded = relative_normal_test(
        mean_num=1.0, var_num=1.0, mean_den=0.1, var_den=1.0, covariance=0.0, **common
    )
    bounded = relative_normal_test(
        mean_num=1.0, var_num=0.01, mean_den=10.0, var_den=0.01, covariance=0.0, **common
    )
    assert "undefined: control (denominator) mean is zero" in undefined.warnings[0]
    assert degenerate.warnings == [FIELLER_DEGENERATE_WARNING]
    assert unbounded.warnings == [FIELLER_UNBOUNDED_WARNING]
    assert bounded.warnings == []
    assert math.isfinite(bounded.left_bound) and math.isfinite(bounded.right_bound)


# --- the properties the estimator was chosen FOR (seeded Monte Carlo) ----------------


def _null_draws(rng, size: int, theta: float, cv: float):
    mean_1_true = 1.0
    var_1 = (mean_1_true * cv) ** 2
    mean_2_true = mean_1_true * (1.0 + theta)
    var_2 = (mean_2_true * cv) ** 2
    drawn_1 = rng.normal(mean_1_true, math.sqrt(var_1), size)
    drawn_2 = rng.normal(mean_2_true, math.sqrt(var_2), size)
    return (
        drawn_2 - drawn_1,
        np.full(size, var_1 + var_2),
        drawn_1,
        np.full(size, var_1),
        np.full(size, -var_1),
    )


def _delta_bounds(moments):
    effect, var = relative_delta_effect_array(
        mean_num=moments[0],
        var_num=moments[1],
        mean_den=moments[2],
        var_den=moments[3],
        covariance=moments[4],
    )
    result = normal_test_array(effect, var, 0.05)
    return result.left_bound, result.right_bound


@pytest.mark.parametrize("theta", [0.0, 0.5])
def test_the_one_sided_error_rates_are_what_delta_gets_wrong(theta: float) -> None:
    """Delta buys 2.5% per side and spends 3.3% on one of them.

    This is the defect, stated at the granularity abkit's verdicts live at: WIN
    and LOSE are ONE-SIDED claims, and delta's two-sided coverage is nominal
    (0.049–0.050) while its tails are 0.017/0.033 at CV₁ = 0.05 and 0.008/0.039
    at CV₁ = 0.10. A two-sided reading — which is all the A/A matrix's FPR column
    is — cannot see that; STAT-2's sign column is the one that can.

    Both true lifts run because the imbalance is a property of the DENOMINATOR's
    noise and not of the effect: it is identical at θ = 0 and θ = +0.5, so an
    A/A run at the null measures the live experiment's error faithfully and
    still reports "calibrated".
    """
    rng = np.random.default_rng(SEED + 3)
    moments = _null_draws(rng, 200_000, theta, 0.05)
    left, right = fieller_bounds(*moments, Z95)
    delta_left, delta_right = _delta_bounds(moments)

    def tails(low, high) -> tuple[float, float]:
        answered = np.isfinite(low) & np.isfinite(high)
        return (
            float(np.mean(answered & (low > theta))),
            float(np.mean(answered & (high < theta))),
        )

    fieller_low, fieller_high = tails(left, right)
    delta_low, delta_high = tails(delta_left, delta_right)

    assert fieller_low == pytest.approx(0.025, abs=0.002), fieller_low
    assert fieller_high == pytest.approx(0.025, abs=0.002), fieller_high
    # The two-sided total is the column that stays calibrated for BOTH — which is
    # why this test asserts the split and not the sum.
    assert delta_low + delta_high == pytest.approx(0.05, abs=0.003)
    assert delta_high - delta_low > 0.01, (delta_low, delta_high)


def test_the_sign_split_of_false_positives_identifies_the_estimator() -> None:
    """STAT-2's instrument, used for what it was built for.

    The two estimators' A/A false-positive RATES agree to the third decimal —
    which is §0.4's point: the matrix's headline column is structurally blind
    here. The sign split is not: delta's left-tail share tracks the derivation's
    ``0.5 + φ(z)z²·CV₁·√w₁/α`` (0.66 at CV₁ = 0.05), Fieller's is 0.50 because
    its rejection rule IS the symmetric absolute test.
    """
    rng = np.random.default_rng(SEED + 4)
    moments = _null_draws(rng, 200_000, 0.0, 0.05)
    left, right = fieller_bounds(*moments, Z95)
    delta_left, delta_right = _delta_bounds(moments)

    def split(low, high) -> tuple[float, float]:
        significant = np.isfinite(low) & np.isfinite(high) & ((low > 0.0) | (high < 0.0))
        hits = int(significant.sum())
        return hits / low.size, float((significant & (high < 0.0)).sum()) / hits

    fieller_fpr, fieller_negative = split(left, right)
    delta_fpr, delta_negative = split(delta_left, delta_right)

    assert fieller_fpr == pytest.approx(0.05, abs=0.002)
    assert delta_fpr == pytest.approx(fieller_fpr, abs=0.002)  # the blind column
    assert fieller_negative == pytest.approx(0.5, abs=0.02)
    predicted = (
        0.5 + (math.exp(-(Z95**2) / 2) / math.sqrt(2 * math.pi)) * Z95**2 * 0.05 * (0.5**0.5) / 0.05
    )
    assert delta_negative == pytest.approx(predicted, abs=0.02), (delta_negative, predicted)


def test_the_scalar_entry_is_the_batch_kernel() -> None:
    """Bit-identical by construction (a length-1 batch), asserted with ``==``."""
    rng = np.random.default_rng(SEED + 5)
    rows = _null_draws(rng, 64, 0.2, 0.08)
    batch_left, batch_right = fieller_bounds(*rows, Z95)
    for index in range(rows[0].size):
        scalar = relative_normal_test(
            mean_num=float(rows[0][index]),
            var_num=float(rows[1][index]),
            mean_den=float(rows[2][index]),
            var_den=float(rows[3][index]),
            covariance=float(rows[4][index]),
            alpha=0.05,
            interval="fieller",
        )
        assert scalar.left_bound == float(batch_left[index])
        assert scalar.right_bound == float(batch_right[index])
