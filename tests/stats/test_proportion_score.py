"""m13 STAT-3 — the score-inversion kernel (:mod:`abkit.stats.proportion_score`).

Every assertion here is one of the known-answer tests the blind derivation names
(``docs/research/2026-08-m13-blind-rederive/proportion-interval.derivation.json``,
``known_answer_tests``) or one of the two premises the shipped code relies on and
the derivation flagged as OPEN: that ``Z`` crosses each critical value exactly
once, and that a root-find which finds no crossing lands on the feasible
boundary rather than on a NaN.

The reference for the constrained MLE is not a second transcription of the same
formula — it is the log-likelihood the MLE maximises, so an error in the cubic
cannot be reproduced by the reference.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import scipy.special as special

from abkit.stats.proportion_score import (
    MLE_NEWTON_STEPS,
    constrained_mle_difference,
    constrained_mle_ratio,
    score_interval_difference,
    score_interval_ratio,
    score_z_difference,
    score_z_ratio,
)


def _critical(alpha: float) -> float:
    return float(special.ndtri(1.0 - alpha / 2.0))


def _tables(seed: int = 17, size: int = 4000):
    """A grid spanning the regimes the derivation says the separations live in:
    sparse rates, strong arm imbalance, and — one fifth of the rows — the empty
    and full cells that make every Wald formula degenerate."""
    rng = np.random.default_rng(seed)
    nobs_1 = rng.integers(20, 200_000, size).astype(np.float64)
    nobs_2 = np.maximum(
        np.floor(nobs_1 * 10 ** rng.uniform(-2, 2, size)), 20.0
    )  # imbalance up to 100:1 both ways
    count_1 = np.minimum(np.floor(nobs_1 * 10 ** rng.uniform(-5, 0, size)), nobs_1)
    count_2 = np.minimum(np.floor(nobs_2 * 10 ** rng.uniform(-5, 0, size)), nobs_2)
    step = size // 10
    count_1[:step] = 0.0
    count_2[step : 2 * step] = 0.0
    count_1[2 * step : 3 * step] = 0.0
    count_2[2 * step : 3 * step] = 0.0
    count_1[3 * step : 4 * step] = nobs_1[3 * step : 4 * step]
    count_2[4 * step : 5 * step] = nobs_2[4 * step : 5 * step]
    return count_1, nobs_1, count_2, nobs_2


def _pooled_z(count_1, nobs_1, count_2, nobs_2):
    pooled = (count_1 + count_2) / (nobs_1 + nobs_2)
    with np.errstate(divide="ignore", invalid="ignore"):
        std = np.sqrt(pooled * (1.0 - pooled) * (1.0 / nobs_1 + 1.0 / nobs_2))
        return (count_2 / nobs_2 - count_1 / nobs_1) / std


def _loglik(root, count_1, nobs_1, count_2, nobs_2, other):
    """The constrained binomial log-likelihood — the OBJECTIVE, not the cubic.

    ``0·log 0`` is 0 (an absent cell contributes nothing); an impossible cell
    (a positive count at a zero probability) is −inf.
    """
    total = 0.0
    for count, nobs, prop in ((count_1, nobs_1, root), (count_2, nobs_2, other)):
        for successes, probability in ((count, prop), (nobs - count, 1.0 - prop)):
            if successes > 0:
                if probability <= 0.0:
                    return -math.inf
                total += successes * math.log(probability)
    return total


class TestKnownAnswers:
    def test_a_double_zero_table_gives_exactly_the_wilson_zero_bound(self):
        """Derivation KAT 1 — the single table that separates every candidate.

        ``x₁ = x₂ = 0`` is where Wald and the pooled interval both return the
        zero-width ``[0, 0]``: an assertion of infinite precision from a table that
        contains no information at all. The score set is available in closed form
        there — ``±z²/(n + z²)``, the Wilson zero bound — so the assertion is to the
        last few bits of the closed form, not to a statistical tolerance. (The
        bisection's fixed budget resolves the root to below one ULP of the answer;
        landing on the neighbouring float is the only slack allowed.)"""
        for nobs in (100.0, 1000.0, 25_000.0):
            for alpha in (0.05, 0.004):
                critical = _critical(alpha)
                left, right = score_interval_difference(
                    np.array([0.0]), np.array([nobs]), np.array([0.0]), np.array([nobs]), critical
                )
                expected = critical * critical / (nobs + critical * critical)
                assert right[0] == pytest.approx(expected, rel=1e-15, abs=0.0)
                assert left[0] == pytest.approx(-expected, rel=1e-15, abs=0.0)

    def test_the_constrained_mle_at_the_null_is_the_pooled_proportion(self):
        """Derivation KAT 2, and the algebraic statement of the coherence claim.

        At ``δ = 0`` the cubic's roots are 0, 1 and X/N, and the admissible one is
        the pooled proportion — so ``σ̃(0)²`` IS the pooled SE² and ``Z(0)`` IS the
        statistic behind the reported p-value. This is the cheapest possible guard
        against a mistyped coefficient: get one wrong and the interval and the
        p-value are silently different statistics. The ratio scale must agree at
        ``θ = 1`` for the same reason."""
        count_1, nobs_1, count_2, nobs_2 = _tables()
        pooled = (count_1 + count_2) / (nobs_1 + nobs_2)
        assert np.isfinite(pooled).all()
        zeros = np.zeros_like(pooled)
        ones = np.ones_like(pooled)
        for got in (
            constrained_mle_difference(count_1, nobs_1, count_2, nobs_2, zeros),
            constrained_mle_ratio(count_1, nobs_1, count_2, nobs_2, ones),
        ):
            assert np.allclose(got, pooled, rtol=1e-13, atol=0.0)

    def test_the_score_statistic_at_the_null_is_the_classical_pooled_z(self):
        """The Farrington–Manning choice (D11) made executable: dropping the MN
        ``N/(N−1)`` factor is what keeps ``Z(0)`` — and hence every reported
        p-value — the number `0.7.0` already printed. ``Z(1)`` on the ratio scale
        is the SAME number, which is why "the lift interval excludes zero" cannot
        contradict "the difference interval excludes zero"."""
        count_1, nobs_1, count_2, nobs_2 = _tables()
        expected = _pooled_z(count_1, nobs_1, count_2, nobs_2)
        usable = np.isfinite(expected)
        assert usable.sum() > count_1.size // 2, "the fixture must mostly produce a real z"
        for got in (
            score_z_difference(count_1, nobs_1, count_2, nobs_2, np.zeros_like(expected)),
            score_z_ratio(count_1, nobs_1, count_2, nobs_2, np.ones_like(expected)),
        ):
            assert np.allclose(got[usable], expected[usable], rtol=1e-13, atol=0.0)

    def test_the_ratio_interval_is_equivariant_under_swapping_the_arms(self):
        """Derivation KAT 7. Relabelling the arms sends ``θ → 1/θ``, so the interval
        must map to its reciprocal — a property the naive "divide the difference
        interval by p̂₁" route does not have AT ALL, and a cheap detector of a sign
        or index error in the constrained-MLE quadratic."""
        count_1, nobs_1, count_2, nobs_2 = _tables(seed=23, size=2000)
        critical = _critical(0.05)
        lower, upper = score_interval_ratio(count_1, nobs_1, count_2, nobs_2, critical)
        swapped_lower, swapped_upper = score_interval_ratio(
            count_2, nobs_2, count_1, nobs_1, critical
        )
        usable = (
            np.isfinite(lower) & np.isfinite(upper) & (lower > 1e-6) & (upper < 1e6) & (upper > 0)
        )
        assert usable.sum() > 500
        assert np.allclose(swapped_lower[usable], 1.0 / upper[usable], rtol=1e-7, atol=0.0)
        assert np.allclose(swapped_upper[usable], 1.0 / lower[usable], rtol=1e-7, atol=0.0)


class TestTheOpenQuestionsTheDerivationLeft:
    @pytest.mark.parametrize("scale", ["difference", "ratio"])
    def test_the_statistic_crosses_each_critical_value_exactly_once(self, scale):
        """The derivation's root-find question, answered as a TESTED premise.

        The bisection assumes ``Z`` decreases in the contrast, which makes the
        confidence set an interval and each endpoint uniquely bracketed. If ``Z``
        ever rose, a bisection would return *a* crossing and the reported interval
        would be a claim nobody derived. Checked at 60 contrasts over the feasible
        range on tables that deliberately include empty and full cells — where the
        constrained MLE sits on a boundary and the shape could change."""
        count_1, nobs_1, count_2, nobs_2 = _tables(seed=31, size=2000)
        previous = None
        for step in np.linspace(0.002, 0.998, 60):
            if scale == "difference":
                contrast = np.full(count_1.shape, 2.0 * step - 1.0)
                current = score_z_difference(count_1, nobs_1, count_2, nobs_2, contrast)
            else:
                contrast = np.full(count_1.shape, step / (1.0 - step))
                current = score_z_ratio(count_1, nobs_1, count_2, nobs_2, contrast)
            if previous is not None:
                rising = current > previous + 1e-9 * np.abs(previous)
                assert not rising.any(), f"Z rose at {step}: {int(rising.sum())} tables"
            previous = current

    def test_no_crossing_lands_on_the_feasible_boundary_never_on_a_nan(self):
        """The derivation's "bracketing scan plus a tested fallback".

        The fallback is not an error branch — it is the only answer a BOUNDED
        confidence set can give: a table with no crossing inside the feasible range
        has the range's own edge as its endpoint. A treatment arm with no
        conversions cannot exclude a −100% lift, so ``θ_L = 0``; a table whose point
        estimate already SITS on the feasible edge has that edge as an endpoint,
        where the statistic is 0/0 and no bracketing scan can find a crossing."""
        # (a) A REAL bracket with no crossing. An empty control arm bounds no ratio
        # from above: Z stays above −critical for every θ, so the search runs the
        # full width of (0, ∞) and still finds nothing. This is the case that
        # distinguishes the fallback from an initialisation — the two fixtures below
        # are degenerate brackets and would pass against a constant Z.
        lower, upper = score_interval_ratio(
            np.array([0.0]), np.array([500.0]), np.array([7.0]), np.array([500.0]), 1.96
        )
        assert lower[0] > 0.0, "the LOWER endpoint is a genuine crossing"
        assert math.isinf(upper[0])

        # (b) Degenerate brackets: the point estimate already sits on the feasible
        # edge, so the endpoint IS the edge and no search happens. Asserted for what
        # they are — that the engine reports the edge rather than a NaN.
        few = np.array([12.0])
        lower, upper = score_interval_ratio(few, np.array([40.0]), np.array([0.0]), few, 1.96)
        assert lower[0] == 0.0
        assert math.isfinite(upper[0]) and upper[0] > 0.0

        # every unit converts in arm 1 and none in arm 2 ⇒ δ̂ = −1, the feasible edge
        full, none = np.array([40.0]), np.array([0.0])
        left, right = score_interval_difference(full, full, none, full, 1.96)
        assert left[0] == -1.0
        assert -1.0 < right[0] < 0.0

    def test_the_newton_polish_is_what_makes_the_mle_machine_precise(self):
        """The closed-form cubic loses ~1e-12 to cancellation exactly where the root
        is small relative to the coefficients — i.e. on the sparse metrics this
        construction exists for. The residual ``|ℓ'|/|ℓ''|`` IS the distance to the
        true root for a simple root, so it measures the answer rather than
        comparing two transcriptions of one formula.

        Mutation probe: with ``MLE_NEWTON_STEPS = 0`` the bound below is exceeded by
        four orders of magnitude."""
        assert MLE_NEWTON_STEPS >= 1
        count_1, nobs_1, count_2, nobs_2 = _tables(seed=5, size=2000)
        for delta in (0.0, 1e-4, -0.01, 0.25):
            contrast = np.full(count_1.shape, delta)
            root = constrained_mle_difference(count_1, nobs_1, count_2, nobs_2, contrast)
            other = root + contrast
            interior = (root > 1e-9) & (root < 1.0 - 1e-9) & (other > 1e-9) & (other < 1.0 - 1e-9)
            with np.errstate(divide="ignore", invalid="ignore"):
                slope = (
                    count_1 / root
                    - (nobs_1 - count_1) / (1.0 - root)
                    + count_2 / other
                    - (nobs_2 - count_2) / (1.0 - other)
                )
                curvature = (
                    count_1 / root**2
                    + (nobs_1 - count_1) / (1.0 - root) ** 2
                    + count_2 / other**2
                    + (nobs_2 - count_2) / (1.0 - other) ** 2
                )
            implied = np.abs(slope / curvature)[interior]
            assert implied.max() < 1e-14, f"delta={delta}: implied |s−s*| {implied.max():.2e}"

    def test_the_mle_maximises_the_likelihood_it_claims_to(self):
        """The cubic is a stationarity condition; this asserts the OBJECTIVE.

        Every neighbour of the returned root — the feasible endpoints and points
        either side of it — must have a lower constrained log-likelihood. A cubic
        with a transposed coefficient still has roots; it does not still maximise
        this."""
        count_1, nobs_1, count_2, nobs_2 = _tables(seed=41, size=300)
        for delta in (0.0, 0.02, -0.15):
            contrast = np.full(count_1.shape, delta)
            root = constrained_mle_difference(count_1, nobs_1, count_2, nobs_2, contrast)
            low, high = max(0.0, -delta), min(1.0, 1.0 - delta)
            for index in range(count_1.size):
                best = _loglik(
                    root[index],
                    count_1[index],
                    nobs_1[index],
                    count_2[index],
                    nobs_2[index],
                    root[index] + delta,
                )
                span = high - low
                for candidate in (low, high, root[index] - 1e-4 * span, root[index] + 1e-4 * span):
                    if not low <= candidate <= high:
                        continue
                    rival = _loglik(
                        candidate,
                        count_1[index],
                        nobs_1[index],
                        count_2[index],
                        nobs_2[index],
                        candidate + delta,
                    )
                    assert rival <= best + 1e-9 * max(1.0, abs(best))


class TestCoherence:
    @pytest.mark.parametrize("alpha", [0.05, 0.004, 1e-4])
    def test_both_intervals_and_the_p_value_are_one_decision(self, alpha):
        """Derivation KAT 4 — the property the whole construction was chosen for,
        swept across the alphas the two-tier correction actually produces.

        "The difference interval excludes 0", "the ratio interval excludes 1" and
        "p < α" must be the SAME event on every table. This is what a Wald interval
        with an unpooled SE would have broken, and it is what silently breaks if a
        later change applies a variance correction factor to one side only."""
        count_1, nobs_1, count_2, nobs_2 = _tables(seed=7)
        critical = _critical(alpha)
        pooled = _pooled_z(count_1, nobs_1, count_2, nobs_2)
        pvalue = 2.0 * np.minimum(special.ndtr(pooled), special.ndtr(-pooled))
        usable = np.isfinite(pvalue)
        assert usable.sum() > count_1.size // 2, "the fixture must mostly produce a real p"
        rejects = pvalue < alpha

        left, right = score_interval_difference(count_1, nobs_1, count_2, nobs_2, critical)
        lower, upper = score_interval_ratio(count_1, nobs_1, count_2, nobs_2, critical)
        assert np.array_equal(((left > 0.0) | (right < 0.0))[usable], rejects[usable])
        assert np.array_equal(((lower > 1.0) | (upper < 1.0))[usable], rejects[usable])

    def test_the_interval_contains_the_point_estimate_and_respects_the_bounds(self):
        """A difference of proportions lives in ``[-1, 1]`` and a ratio in
        ``[0, ∞)``. Wald and pooled-Wald both leave those ranges routinely at small
        n; the score construction cannot, and the assertion is here so nobody
        "simplifies" the feasible clamp out of the MLE."""
        count_1, nobs_1, count_2, nobs_2 = _tables(seed=13)
        estimate = count_2 / nobs_2 - count_1 / nobs_1
        left, right = score_interval_difference(count_1, nobs_1, count_2, nobs_2, _critical(0.05))
        assert np.all(left >= -1.0) and np.all(right <= 1.0)
        assert np.all(left <= estimate + 1e-12) and np.all(right >= estimate - 1e-12)

        lower, upper = score_interval_ratio(count_1, nobs_1, count_2, nobs_2, _critical(0.05))
        assert np.all(lower >= 0.0) and np.all(upper >= lower)
