"""Golden tests for the numbers M13 ADDED (m13-implementation-plan.md §4 item 2).

The legacy goldens in this directory pin the captured baseline and are untouched
by this milestone — that is asserted by their continuing to pass, not restated
here. What M13 needs is the other half of the change-control rule
(`.claude/rules/contributing.md`, "Changing a statistical number"): *a
deliberate deviation gets a NEW test*. These are those tests.

Each case is asserted twice, and the pair is the point:

* against an **independent reference** (``m13_reference``) that computes the
  same quantity by a different algorithm — brentq on the constrained likelihood
  and on ``|Z| = c`` for the score interval, ``numpy.roots`` for Fieller, the
  step-down definition written out for Holm. This is what makes the numbers
  *right* rather than merely stable, and it is where a transposed coefficient
  or an off-by-one in the step-down would surface.
* against a **literal**, pinned at the house tolerance. This is what makes them
  a golden: a reference living in the test tree can be edited alongside the
  engine, and then the first assertion agrees with the mistake. The literals
  were produced from the engine and *verified by the reference above* to ~1e-15
  relative — three to six orders inside the tolerance they are pinned at.

Tolerance is the house **relative 1e-9** (§1.1) and is never to be loosened to
make a test pass. Boundary tables (an empty cell, saturated arms, an unbounded
Fieller set) are deliberately absent: both references are root-finders and are
valid only where the constrained maximum is interior, and the objective-function
KATs in ``tests/stats/`` are the anchor that CAN see those.
"""

from __future__ import annotations

import m13_reference as reference
import numpy as np
import pytest

from abkit.stats.correction import holm_adjusted
from abkit.stats.proportion_score import score_interval_difference, score_interval_ratio
from abkit.stats.relative_interval import fieller_bounds

pytestmark = pytest.mark.golden

#: The two-sided 95% normal quantile — the critical value every case below uses.
Z95 = 1.959963984540054

#: ``(name, count_1, nobs_1, count_2, nobs_2)`` — the ordinary case, the
#: derivation's 900/100 imbalance, a 10:1 the other way, the harmonic-n regime,
#: and p ≈ 0.5 where the pooled and score intervals nearly coincide.
SCORE_TABLES = [
    ("ordinary", 500.0, 10_000.0, 560.0, 10_000.0),
    ("imbalanced_900_100", 9.0, 900.0, 2.0, 100.0),
    ("imbalanced_other_way", 50.0, 5_000.0, 12.0, 500.0),
    ("harmonic_n", 2_000.0, 100_000.0, 30.0, 1_000.0),
    ("near_half", 25_000.0, 50_000.0, 25_500.0, 50_000.0),
]

#: m13 STAT-3, difference scale: ``{δ : |Z(δ)| ≤ z}``.
SCORE_DIFFERENCE_GOLDEN = {
    "ordinary": (-0.00021009770756825899, 0.012226071039380997),
    "imbalanced_900_100": (-0.007087212937873049, 0.060210173564728423),
    "imbalanced_other_way": (0.0032922456908527692, 0.031633102861446591),
    "harmonic_n": (0.0010442228308614266, 0.022528515019499845),
    "near_half": (0.0038024044196884295, 0.016196827471713603),
}

#: The same statistic on the RATIO scale, as θ (the engine reports ``θ − 1``).
SCORE_RATIO_GOLDEN = {
    "ordinary": (0.99605033175108504, 1.2594029059537151),
    "imbalanced_900_100": (0.48935024941531602, 7.9931128524832848),
    "imbalanced_other_way": (1.2962975028579322, 4.4183540021851044),
    "harmonic_n": (1.0519477942546573, 2.1308426689509896),
    "near_half": (1.0075578793273994, 1.0325973521742737),
}

#: ``(name, mean_1, var_1, mean_2, var_2)`` — per-arm MEANS and the variances
#: OF those means, the shape the relative branch feeds Fieller: ``a = m₂ − m₁``,
#: ``V_a = V₁ + V₂``, ``b = m₁``, ``V_b = V₁``, ``V_ab = −V₁`` (the arms are
#: independent; the covariance is the shared ``m₁``, m13 plan §0.2(b)).
FIELLER_CASES = [
    ("ordinary", 50.0, 0.25, 55.0, 0.30),
    # a denominator noisy enough to skew the set visibly, still bounded
    ("noisy_denominator", 10.0, 4.0, 12.0, 5.0),
    ("tight", 2.0, 0.001, 2.4, 0.0015),
]

#: m13 STAT-4: ``{θ : (a − θb)² ≤ z²(V_a − 2θV_ab + θ²V_b)}``, as a LIFT.
FIELLER_GOLDEN = {
    "ordinary": (0.069987117949900146, 0.13085832776514253),
    "noisy_denominator": (-0.31415169909894136, 1.1498859537215163),
    "tight": (0.14797914307585516, 0.25432794786527269),
}

#: m13 STAT-1: Holm's step-down adjusted p-values over one family.
HOLM_PVALUES = (0.001, 0.012, 0.03, 0.2, 0.7)
HOLM_GOLDEN = (0.005, 0.048, 0.09, 0.4, 0.7)


def _assert_rel(actual: float, expected: float, what: str) -> None:
    assert actual == pytest.approx(
        expected, rel=1e-9, abs=1e-12
    ), f"{what}: {actual!r} != {expected!r}"


@pytest.mark.parametrize(("name", "count_1", "nobs_1", "count_2", "nobs_2"), SCORE_TABLES)
class TestScoreProportionInterval:
    """STAT-3 — Miettinen–Nurminen in its Farrington–Manning form (D11)."""

    def test_difference_scale(self, name, count_1, nobs_1, count_2, nobs_2):
        engine = score_interval_difference(
            np.array([count_1]), np.array([nobs_1]), np.array([count_2]), np.array([nobs_2]), Z95
        )
        expected = reference.score_interval_difference_reference(
            count_1, nobs_1, count_2, nobs_2, Z95
        )
        for side, bound, want, literal in zip(
            ("left", "right"), engine, expected, SCORE_DIFFERENCE_GOLDEN[name], strict=True
        ):
            _assert_rel(float(bound[0]), want, f"{name}/{side} vs reference")
            _assert_rel(float(bound[0]), literal, f"{name}/{side} vs golden")

    def test_ratio_scale(self, name, count_1, nobs_1, count_2, nobs_2):
        engine = score_interval_ratio(
            np.array([count_1]), np.array([nobs_1]), np.array([count_2]), np.array([nobs_2]), Z95
        )
        expected = reference.score_interval_ratio_reference(count_1, nobs_1, count_2, nobs_2, Z95)
        for side, bound, want, literal in zip(
            ("lower", "upper"), engine, expected, SCORE_RATIO_GOLDEN[name], strict=True
        ):
            _assert_rel(float(bound[0]), want, f"{name}/{side} vs reference")
            _assert_rel(float(bound[0]), literal, f"{name}/{side} vs golden")

    def test_the_endpoints_solve_the_equation_they_claim_to(
        self, name, count_1, nobs_1, count_2, nobs_2
    ):
        """The definition, not a value: an endpoint of ``{δ : |Z(δ)| ≤ z}`` is a
        δ where ``|Z| = z``. This is the one assertion that survives a change of
        BOTH the engine and the literals above."""
        left, right = SCORE_DIFFERENCE_GOLDEN[name]
        for bound, expected_sign in ((left, +1.0), (right, -1.0)):
            z_at_bound = reference.score_z_difference(count_1, nobs_1, count_2, nobs_2, bound)
            _assert_rel(z_at_bound, expected_sign * Z95, f"{name}: |Z| at the bound")


@pytest.mark.parametrize(("name", "mean_1", "var_1", "mean_2", "var_2"), FIELLER_CASES)
class TestFiellerRelativeInterval:
    """STAT-4 — the relative interval as the inversion of the absolute test."""

    def test_bounds(self, name, mean_1, var_1, mean_2, var_2):
        moments = (mean_2 - mean_1, var_1 + var_2, mean_1, var_1, -var_1)
        engine = fieller_bounds(*(np.array([m]) for m in moments), Z95)
        expected = reference.fieller_bounds_reference(*moments, Z95)
        for side, bound, want, literal in zip(
            ("left", "right"), engine, expected, FIELLER_GOLDEN[name], strict=True
        ):
            _assert_rel(float(bound[0]), want, f"{name}/{side} vs reference")
            _assert_rel(float(bound[0]), literal, f"{name}/{side} vs golden")

    def test_the_endpoints_sit_on_the_membership_boundary(self, name, mean_1, var_1, mean_2, var_2):
        """``(a − θb)² = z²(V_a − 2θV_ab + θ²V_b)`` at both ends — the objective
        itself, which is the only reference that catches a transposed
        coefficient (its error vanishes at θ = 0, so the coherence tests do not
        see it — the STAT-3 lesson, applied to the second interval)."""
        a, v_a, b, v_b, v_ab = (mean_2 - mean_1, var_1 + var_2, mean_1, var_1, -var_1)
        for theta in FIELLER_GOLDEN[name]:
            left_side = (a - theta * b) ** 2
            right_side = Z95**2 * (v_a - 2.0 * theta * v_ab + theta * theta * v_b)
            _assert_rel(left_side, right_side, f"{name}: membership at θ={theta}")


class TestHolmAdjustment:
    """STAT-1 — the step-down adjuster behind ``correction: holm``."""

    def test_adjusted_pvalues_match_the_definition_and_the_golden(self):
        engine = holm_adjusted(np.array(HOLM_PVALUES))
        expected = reference.holm_adjusted_reference(list(HOLM_PVALUES))
        for index, (got, want, literal) in enumerate(
            zip(engine, expected, HOLM_GOLDEN, strict=True)
        ):
            _assert_rel(float(got), want, f"p[{index}] vs reference")
            _assert_rel(float(got), literal, f"p[{index}] vs golden")

    def test_the_step_down_is_monotone_and_never_below_bonferroni_at_the_first_step(self):
        """Holm's first step IS ``α/m``; every later step is looser. A reversed
        cumulative max would break the second half while the first still held."""
        engine = [float(p) for p in holm_adjusted(np.array(HOLM_PVALUES))]
        ordered = sorted(zip(HOLM_PVALUES, engine, strict=True))
        assert [adjusted for _raw, adjusted in ordered] == sorted(
            adjusted for _raw, adjusted in ordered
        )
        smallest_raw, smallest_adjusted = ordered[0]
        _assert_rel(smallest_adjusted, len(HOLM_PVALUES) * smallest_raw, "the first step")
