"""m13 STAT-3 — ``z-test`` under ``interval: score`` (Miettinen–Nurminen).

The milestone's posture (plan §0.3) is "numbers move, but no default does", so
half of this file is about what must NOT have changed: the identity hash, the
p-value, and every number a project that writes nothing new still gets. The other
half is the deviation itself and the two consequences the design named as
required sub-tasks — the relative scale's identification warning and the boundary
tables that made the pooled interval indefensible.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import scipy.special as special

from abkit.stats import create_method, get_method_class
from abkit.stats.parametric.ztest import RELATIVE_IDENTIFICATION_HALF_WIDTH
from abkit.stats.samples import Fraction

TABLES = [
    (500, 10_000, 560, 10_000),  # the ordinary case
    (9, 900, 2, 100),  # the derivation's 900/100 imbalance
    (1, 50_000, 12, 50_000),  # sparse both arms
    (25_000, 50_000, 25_500, 50_000),  # p ≈ 0.5
    (0, 1_000, 3, 1_000),  # empty control cell
    (5, 1_000, 0, 1_000),  # empty treatment cell
    (1_000, 1_000, 999, 1_000),  # saturated arms
]


def _pair(row):
    count_1, nobs_1, count_2, nobs_2 = row
    return (
        Fraction(count=count_1, nobs=nobs_1, name="control"),
        Fraction(count=count_2, nobs=nobs_2, name="treatment"),
    )


def _method(interval: str, test_type: str = "absolute", alpha: float = 0.05):
    return create_method(
        "z-test", alpha=alpha, params={"test_type": test_type, "interval": interval}
    )


class TestNoDefaultMoves:
    def test_the_param_is_absent_from_the_identity_of_an_unset_config(self):
        """D4's whole safety argument: an identity-flagged param whose default is the
        legacy value orphans the series OF THE OPERATOR WHO OPTS IN, and nobody
        else's. ``identity_params`` carries non-default values only, so adding the
        param must leave `0.7.0`'s hash byte-identical — the pre-STAT-3 literal is
        pinned in ``test_identity.py`` and reproduced here against the live method."""
        default = create_method("z-test", alpha=0.05)
        explicit = create_method("z-test", alpha=0.05, params={"interval": "pooled"})
        assert default.identity_params == {}
        assert default.method_config_id == explicit.method_config_id

    def test_opting_in_starts_a_new_series(self):
        """Exit-gate item 3. If the hash did NOT move, an operator switching
        estimators would append incompatible numbers to a published cumulative
        series — the failure D4 chose an opt-in param to make impossible."""
        opted_in = create_method("z-test", alpha=0.05, params={"interval": "score"})
        assert opted_in.method_config_id != create_method("z-test", alpha=0.05).method_config_id
        assert opted_in.identity_params == {"interval": "score"}

    @pytest.mark.parametrize("test_type", ["absolute", "relative"])
    @pytest.mark.parametrize("row", TABLES)
    def test_the_default_interval_reproduces_the_legacy_numbers_exactly(self, test_type, row):
        """``interval: pooled`` is the untouched legacy branch, and "untouched" is a
        byte claim, not a tolerance one — the golden suite pins the same code from
        the other side."""
        left, right = (_method(iv, test_type).from_suffstats(*_pair(row)) for iv in ("pooled",) * 2)
        assert left.to_dict() == right.to_dict()
        unset = create_method("z-test", alpha=0.05, params={"test_type": test_type})
        assert unset.from_suffstats(*_pair(row)).to_dict() == left.to_dict()


class TestThePValueDoesNotMove:
    @pytest.mark.parametrize("test_type", ["absolute", "relative"])
    @pytest.mark.parametrize("row", TABLES)
    def test_score_reports_the_same_p_value_bit_for_bit(self, test_type, row):
        """D11 made executable. Dropping the MN ``N/(N−1)`` factor
        (Farrington–Manning) makes ``Z(0)`` the classical pooled z, so the p-value
        branch is not merely equivalent — it is the SAME CODE, and the assertion is
        equality rather than closeness. The one exception is the table where the
        pooled statistic is 0/0; it is the next test."""
        pooled = _method("pooled", test_type).from_suffstats(*_pair(row))
        score = _method("score", test_type).from_suffstats(*_pair(row))
        if math.isnan(pooled.pvalue):
            # Naming the ONE row allowed to land here is the difference between a
            # skip and a hole: a formula change that started NaN-ing ordinary tables
            # would otherwise turn this test green by exempting them.
            assert (test_type, row) == ("relative", (0, 1_000, 3, 1_000))
            assert math.isnan(score.pvalue)
            return
        assert score.pvalue == pooled.pvalue
        assert score.effect == pooled.effect
        assert score.reject == pooled.reject

    def test_a_doubly_empty_table_reports_p_equal_to_one_and_a_real_interval(self):
        """Derivation KAT 1, through the public entry. ``x₁ = x₂ = 0`` is the table
        where the pooled construction returns ``[0, 0]`` beside a NaN p — an
        interval of infinite precision from a table with no information, and a row
        no reader can act on. Under ``score`` the arms are simply at the same
        degenerate proportion: the most perfectly null table there is, so p = 1,
        beside the Wilson zero bound.

        Under ``relative`` the refusal STANDS: a lift over a zero baseline is
        undefined whatever the interval method, so H5 is not an interval question."""
        absolute = _method("score", "absolute").from_suffstats(*_pair((0, 1_000, 0, 1_000)))
        critical = float(special.ndtri(0.975))
        expected = critical**2 / (1_000.0 + critical**2)
        assert absolute.pvalue == 1.0
        assert absolute.reject is False
        assert absolute.left_bound == pytest.approx(-expected, rel=1e-15)
        assert absolute.right_bound == pytest.approx(expected, rel=1e-15)
        assert absolute.warnings == []

        legacy = _method("pooled", "absolute").from_suffstats(*_pair((0, 1_000, 0, 1_000)))
        assert math.isnan(legacy.pvalue) and math.isnan(legacy.left_bound)

        relative = _method("score", "relative").from_suffstats(*_pair((0, 1_000, 0, 1_000)))
        assert math.isnan(relative.pvalue) and math.isnan(relative.left_bound)
        assert any("relative effect undefined" in w for w in relative.warnings)


class TestWhatTheIntervalBuys:
    @pytest.mark.parametrize("test_type", ["absolute", "relative"])
    @pytest.mark.parametrize("alpha", [0.05, 0.004])
    @pytest.mark.parametrize("row", TABLES)
    def test_the_verdict_and_the_interval_never_disagree(self, test_type, alpha, row):
        """The property the construction was chosen for (§0.2(a)): swapping in an
        unpooled Wald SE would have produced a valid interval beside a score
        p-value that disagree near the boundary, and ``pipeline/readout.py`` decides
        significance BY CI EXCLUSION under a compute-time correction — so the two
        must not be allowed to part company. Swept at the corrected alpha too,
        because the damage of a mis-scaled SE grows as alpha shrinks."""
        result = _method("score", test_type, alpha).from_suffstats(*_pair(row))
        if math.isnan(result.pvalue):
            return
        excludes_zero = result.left_bound > 0.0 or result.right_bound < 0.0
        assert excludes_zero is result.reject

    def test_under_imbalance_the_pooled_interval_is_the_NARROWER_one(self):
        """Derivation KAT 3, and the reason this WP is not cosmetic: at 900/100 with
        p₁ = 1%, ``SE_pooled / SE_unpooled = 0.764`` — the pooled interval is 24%
        too narrow, i.e. ANTI-conservative, against the widespread belief that
        pooling is the safe choice. The score interval, whose variance is
        re-estimated under each candidate difference, is wider here.

        (Balanced arms are the non-event: the same comparison there is a rounding
        difference, which is why a fixture built at ``w = 1/2`` would certify
        nothing.)"""
        imbalanced = _pair((9, 900, 2, 100))
        pooled = _method("pooled").from_suffstats(*imbalanced)
        score = _method("score").from_suffstats(*imbalanced)
        assert score.ci_length > pooled.ci_length

        pooled_se = math.sqrt(0.011 * 0.989 * (1 / 900 + 1 / 100))
        unpooled_se = math.sqrt(0.01 * 0.99 / 900 + 0.02 * 0.98 / 100)
        assert pooled_se / unpooled_se == pytest.approx(0.764174, rel=1e-5)

        balanced = _pair((500, 10_000, 560, 10_000))
        assert _method("score").from_suffstats(*balanced).ci_length == pytest.approx(
            _method("pooled").from_suffstats(*balanced).ci_length, rel=2e-3
        )

    def test_the_interval_is_asymmetric_about_the_point_estimate(self):
        """The whole reason STAT-3a had to ship first. The bounds must be reported
        as ``[L, U]``; anything that re-derives them from a half-width is inventing
        a standard error this method does not have."""
        result = _method("score").from_suffstats(*_pair((1, 50_000, 12, 50_000)))
        below = result.effect - result.left_bound
        above = result.right_bound - result.effect
        assert abs(above - below) / below > 0.05
        assert result.effect_distribution is None

    @pytest.mark.parametrize("row", TABLES)
    def test_an_empty_cell_still_yields_a_finite_two_sided_interval(self, row):
        """ "This is the common case at an early cutoff on a 1e-3 metric, not an
        exotic one" — the derivation on single-empty-cell tables, where a Wald
        interval contributes zero variance from the empty arm and can exclude zero
        on the strength of the other arm alone."""
        result = _method("score").from_suffstats(*_pair(row))
        assert math.isfinite(result.left_bound) and math.isfinite(result.right_bound)
        assert -1.0 <= result.left_bound <= result.right_bound <= 1.0


class TestTheRelativeIdentificationWarning:
    """The derivation's required sub-task: a rule stated in CONVERSIONS.

    ``half-width ≈ critical·√(1/x₁ + 1/x₂)`` depends on the counts alone — ten
    times the traffic at a tenth of the rate buys exactly nothing — so a
    threshold expressed in exposed units would be the wrong quantity. It warns
    rather than suppresses: the interval is correct, and hiding a correct number
    is a worse failure than printing an unhelpful one.
    """

    def test_it_fires_when_the_lift_is_not_pinned_down(self):
        result = _method("score", "relative").from_suffstats(*_pair((10, 5_000, 12, 5_000)))
        assert any("weakly identified" in w for w in result.warnings)
        assert any("CONVERSIONS" in w for w in result.warnings)

    def test_it_is_silent_when_the_lift_is_measurable(self):
        result = _method("score", "relative").from_suffstats(*_pair((5_000, 50_000, 5_200, 50_000)))
        assert not any("weakly identified" in w for w in result.warnings)

    def test_it_tightens_by_itself_as_the_correction_shrinks_alpha(self):
        """The threshold carries the critical value, so a metric that is adequately
        identified at α = 0.05 can stop being so at the corrected α — which is the
        alpha the decision is actually taken at. A fixed count would have had to be
        re-chosen per correction scheme, and would have been wrong under all but
        one of them."""
        row = _pair((60, 20_000, 66, 20_000))
        assert not any(
            "weakly identified" in w
            for w in _method("score", "relative", 0.05).from_suffstats(*row).warnings
        )
        assert any(
            "weakly identified" in w
            for w in _method("score", "relative", 0.0001).from_suffstats(*row).warnings
        )

    def test_the_legacy_path_gains_no_new_warning(self):
        """A warning is a persisted cell, and `0.8.0`'s byte-compatibility claim
        covers the whole row — not just the numbers in it."""
        row = _pair((10, 5_000, 12, 5_000))
        assert not any(
            "weakly identified" in w
            for w in _method("pooled", "relative").from_suffstats(*row).warnings
        )

    def test_the_stated_threshold_is_the_one_the_code_applies(self):
        """A constant quoted in a message that the code does not use is how a
        docstring becomes a lie. Solve the width law for the count it names and
        check the warning stops firing there."""
        critical = float(special.ndtri(0.975))
        needed = 2.0 * (critical / RELATIVE_IDENTIFICATION_HALF_WIDTH) ** 2
        count = int(math.ceil(needed))
        quiet = _method("score", "relative").from_suffstats(
            *_pair((count, 100_000, count, 100_000))
        )
        noisy = _method("score", "relative").from_suffstats(
            *_pair((count - 2, 100_000, count - 2, 100_000))
        )
        assert not any("weakly identified" in w for w in quiet.warnings)
        assert any("weakly identified" in w for w in noisy.warnings)


class TestTheCapabilityFlag:
    def test_the_flag_is_resolved_per_instance_not_per_class(self):
        """STAT-3a's load-bearing delta, and the configuration it exists for: the
        interval shape is a PARAM here, so a ``ClassVar`` would have answered for
        the default params of every instance and the guard could never fire."""
        assert get_method_class("z-test").asymmetric_ci is False
        assert _method("pooled").asymmetric_ci is False
        assert _method("score").asymmetric_ci is True

    def test_the_always_valid_transform_refuses_a_score_interval(self):
        """The refusal is the shipped answer for the sequential mode (§6a fallback):
        the confidence sequence CAN be built on the score scale, but only by
        substituting the critical value inside the root-find — not by widening a
        finished interval, which is all ``to_always_valid`` can do. Config-lint
        catches the pair before a run; this is the backstop under it."""
        from abkit.stats.exceptions import AsymmetricCIError
        from abkit.stats.sequential import to_always_valid

        result = _method("score").from_suffstats(*_pair((500, 10_000, 560, 10_000)))
        with pytest.raises(AsymmetricCIError):
            to_always_valid(result, 0.01, 0.05, method=_method("score"))


def test_the_wald_sizing_and_the_score_rule_agree_to_order_z_squared_over_n():
    """§6(b), measured rather than assumed.

    ``abk plan`` sizes on the normal power formula while the analysis inverts the
    score statistic, so a stated MDE does not exactly invert the rule that will be
    applied. The plan claims the gap is ``O(z²/N)``; asserting a small number would
    prove nothing (every gap is small somewhere), so what is pinned is the SHAPE:
    ``gap · n / z²`` must be a CONSTANT in n. Measured, it is 4.01 at a 5% baseline
    and 0.060 at 30% — i.e. 0.15% of the half-width at 10k per arm and a tenth of
    that at 100k, which is what ``statistics-changes.md`` §6 quotes.
    """
    critical = float(special.ndtri(0.975))
    for rate, expected in ((0.05, 4.01), (0.3, 0.0595)):
        coefficients = []
        for nobs in (10_000.0, 100_000.0, 1_000_000.0):
            count = round(rate * nobs)
            arms = (Fraction(count=count, nobs=nobs), Fraction(count=count, nobs=nobs))
            pooled = _method("pooled").from_suffstats(*arms)
            score = _method("score").from_suffstats(*arms)
            gap = abs(score.ci_length - pooled.ci_length) / pooled.ci_length
            coefficients.append(gap * nobs / critical**2)
        for coefficient in coefficients:
            assert coefficient == pytest.approx(expected, rel=0.01), (rate, coefficients)


def test_the_batch_entry_is_the_same_code_as_the_scalar_one():
    """The score bounds come from ONE array kernel; the scalar entry wraps its
    counts in a length-1 batch. Bit-exactness is therefore structural — the M7 WP2
    parity gate covers the same ground with a wider fixture, and this is the
    statement of WHY it can be an equality rather than a tolerance."""
    counts = np.array([float(row[0]) for row in TABLES])
    nobs_1 = np.array([float(row[1]) for row in TABLES])
    counts_2 = np.array([float(row[2]) for row in TABLES])
    nobs_2 = np.array([float(row[3]) for row in TABLES])
    for test_type in ("absolute", "relative"):
        method = _method("score", test_type)
        batch = method.from_suffstats_array(
            {"count": counts, "nobs": nobs_1}, {"count": counts_2, "nobs": nobs_2}
        )
        for index, row in enumerate(TABLES):
            scalar = method.from_suffstats(*_pair(row))
            for field in ("left_bound", "right_bound", "pvalue"):
                got, want = getattr(batch, field)[index], getattr(scalar, field)
                assert (got == want) or (math.isnan(got) and math.isnan(want)), (row, field)
