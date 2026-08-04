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
import re

import numpy as np
import pytest
import scipy.special as special

from abkit.stats import create_method, get_method_class
from abkit.stats.parametric.ztest import RELATIVE_IDENTIFICATION_HALF_WIDTH
from abkit.stats.proportion_score import score_interval_ratio
from abkit.stats.samples import Fraction

TABLES = [
    (500, 10_000, 560, 10_000),  # the ordinary case
    (9, 900, 2, 100),  # the derivation's 900/100 imbalance
    (50, 5_000, 12, 500),  # 10:1 the other way
    (2_000, 100_000, 30, 1_000),  # 100:1 — the harmonic-n regime
    (1, 50_000, 12, 50_000),  # sparse both arms
    (25_000, 50_000, 25_500, 50_000),  # p ≈ 0.5 (where the separation VANISHES)
    (0, 1_000, 3, 1_000),  # empty control cell
    (5, 1_000, 0, 1_000),  # empty treatment cell
    (1_000, 1_000, 999, 1_000),  # saturated arms
    (1_000, 1_000, 1_000, 1_000),  # doubly FULL — the mirror of the doubly-empty KAT
]

#: The tables where the POOLED path has no p-value at all, and why. Named rather
#: than detected, so an exemption cannot grow silently: a formula change that
#: started NaN-ing ordinary tables would otherwise turn the equality test green by
#: quietly exempting them.
NO_POOLED_PVALUE = {
    ("relative", (0, 1_000, 3, 1_000)),  # H5: a lift over a zero baseline
    ("absolute", (1_000, 1_000, 1_000, 1_000)),  # pooled variance 0 (both arms at 1)
    ("relative", (1_000, 1_000, 1_000, 1_000)),
}


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
        explicit = _method("pooled", test_type).from_suffstats(*_pair(row))
        unset = create_method("z-test", alpha=0.05, params={"test_type": test_type})
        assert unset.from_suffstats(*_pair(row)).to_dict() == explicit.to_dict()
        # `effect_distribution` is dropped by to_dict(), and the diff restructured
        # exactly that assignment — so assert it separately or the claim has a hole.
        assert (unset.from_suffstats(*_pair(row)).effect_distribution is None) == (
            explicit.effect_distribution is None
        )


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
            assert (test_type, row) in NO_POOLED_PVALUE, "an unexpected table lost its p-value"
            # score answers 1.0 where the table is exactly null, NaN where the
            # relative effect itself is undefined — never the other way round
            assert score.pvalue == 1.0 or math.isnan(score.pvalue)
            return
        assert (test_type, row) not in NO_POOLED_PVALUE
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

        # The fixture's REGIME, not abkit's arithmetic: both sides are literals, and
        # the point is that this row sits where the derivation says the separation
        # lives (SE_pooled/SE_unpooled = 0.764). It guards the fixture against being
        # "tidied" into a balanced one, where the whole comparison above is a
        # rounding difference and certifies nothing.
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

    @pytest.mark.parametrize("test_type", ["absolute", "relative"])
    @pytest.mark.parametrize("row", TABLES)
    def test_no_row_ever_carries_a_non_finite_bound(self, test_type, row):
        """ "This is the common case at an early cutoff on a 1e-3 metric, not an
        exotic one" — the derivation on single-empty-cell tables, where a Wald
        interval contributes zero variance from the empty arm and can exclude zero
        on the strength of the other arm alone.

        BOTH scales, because only the relative one can produce ``+inf`` at all — and
        an infinite bound is not cosmetic downstream: ``enrich`` cleans a non-finite
        float to NULL and the readout's ``_informative`` then drops the row from the
        stabilization scan, so a rejecting look would silently stop being a look.
        The kernel CAN return ``inf`` (an empty CONTROL arm bounds no ratio from
        above); what makes it unreachable here is H5 refusing that row first — a
        dependency worth pinning rather than assuming."""
        result = _method("score", test_type).from_suffstats(*_pair(row))
        for bound in (result.left_bound, result.right_bound):
            assert math.isfinite(bound) or math.isnan(bound), (row, bound)
        if math.isfinite(result.left_bound):
            assert result.left_bound <= result.right_bound
            if test_type == "absolute":
                assert -1.0 <= result.left_bound and result.right_bound <= 1.0

    def test_the_kernel_bound_that_h5_makes_unreachable(self):
        """The ``+inf`` branch of the ratio search, exercised where it IS reachable.

        With no conversions in the control arm the ratio has no upper bound at all,
        and the kernel says so instead of inventing a large finite number. The
        z-test never sees it because the relative POINT estimate is undefined there
        (H5) — which is what the test above depends on, asserted directly."""
        lower, upper = score_interval_ratio(
            np.array([0.0]), np.array([1_000.0]), np.array([5.0]), np.array([1_000.0]), 1.96
        )
        assert lower[0] > 0.0 and math.isinf(upper[0])
        refused = _method("score", "relative").from_suffstats(*_pair((0, 1_000, 5, 1_000)))
        assert math.isnan(refused.right_bound)


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

        # Read the figure back OUT of the warning rather than trusting this file's
        # copy of the formula: a message that quotes a constant the code does not use
        # is the exact failure the warning's own docstring names, and two
        # transcriptions of one formula cannot detect a change in either.
        noisy_message = next(
            w
            for w in _method("score", "relative")
            .from_suffstats(*_pair((count - 2, 100_000, count - 2, 100_000)))
            .warnings
            if "weakly identified" in w
        )
        quoted = int(re.search(r"~(\d+) CONVERSIONS", noisy_message).group(1))
        assert quoted == round(needed)
        assert not any(
            "weakly identified" in w
            for w in _method("score", "relative")
            .from_suffstats(*_pair((quoted, 100_000, quoted, 100_000)))
            .warnings
        )
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


def test_the_fixture_set_can_actually_separate_the_two_constructions():
    """The guard on every `score`-vs-`pooled` test in this file.

    Coherence, bounded intervals and an unchanged p-value are all properties the
    LEGACY interval has too, so a hostile `interval: score` that silently returned
    the pooled bounds would pass most of them. What it cannot survive is a fixture
    that separates the two — and the derivation is explicit that balanced arms and
    p = 0.5 are exactly where the separation vanishes, so "some table differs" is a
    claim about THIS table set, not a generality."""
    imbalanced = [row for row in TABLES if max(row[1], row[3]) >= 5 * min(row[1], row[3])]
    assert len(imbalanced) >= 3, "the table set must keep genuinely imbalanced rows"
    for row in imbalanced:
        pooled = _method("pooled").from_suffstats(*_pair(row))
        score = _method("score").from_suffstats(*_pair(row))
        relative_gap = abs(score.ci_length - pooled.ci_length) / abs(pooled.ci_length)
        assert relative_gap > 1e-3, (row, pooled.ci_length, score.ci_length)


def test_the_p_value_moves_on_exactly_one_kind_of_table_and_nowhere_else():
    """The precise form of "no p-value moves", swept rather than asserted on seven rows.

    The claim the CHANGELOG and the spec make is an equality with ONE exception; a
    fixture list can only ever illustrate that. This walks a wide grid — sparse
    rates, 100:1 imbalance, empty and full cells — and requires: equal p, or a
    pooled NaN paired with a score answer, and never a score NaN where pooled had a
    number. If the `p = 1.0` branch ever widened beyond the degenerate table, this
    is what would catch it."""
    rng = np.random.default_rng(19)
    size = 900
    nobs_1 = rng.integers(5, 200_000, size)
    nobs_2 = np.maximum((nobs_1 * 10 ** rng.uniform(-2, 2, size)).astype(int), 5)
    count_1 = np.minimum((nobs_1 * 10 ** rng.uniform(-5, 0, size)).astype(int), nobs_1)
    count_2 = np.minimum((nobs_2 * 10 ** rng.uniform(-5, 0, size)).astype(int), nobs_2)
    count_1[:70] = 0
    count_2[70:140] = 0
    count_1[140:210] = 0
    count_2[140:210] = 0
    count_1[210:280] = nobs_1[210:280]
    count_2[210:280] = nobs_2[210:280]

    moved = 0
    for index in range(size):
        row = (int(count_1[index]), int(nobs_1[index]), int(count_2[index]), int(nobs_2[index]))
        pooled = _method("pooled").from_suffstats(*_pair(row))
        score = _method("score").from_suffstats(*_pair(row))
        if math.isnan(pooled.pvalue):
            assert score.pvalue == 1.0, row  # absolute scale: the exactly-null table
            moved += 1
            continue
        assert score.pvalue == pooled.pvalue, row
    assert moved > 20, "the sweep must actually reach the degenerate branch"


def test_the_scalar_and_batch_entries_agree_under_a_DIFFERENT_array_length():
    """The premise behind the bit-exact parity gate, pinned so it fails loudly.

    "Same kernel, so parity is structural" holds for IEEE `+ − × ÷ √`, but the
    constrained-MLE seed goes through ``arccos``/``cos``, whose numpy loops dispatch
    on CPU features AND on array length. A 1-ULP seed difference would survive four
    Newton steps and land in bisection comparisons whose last halvings differ by
    less than an ULP — so the endpoint could move and ``assert_array_equal`` would
    fail somewhere far away, on someone else's numpy build. This is the project's
    ``_libm_pow`` hazard without a structural fix available; making the premise its
    own test means a future numpy change reports the CAUSE."""
    rng = np.random.default_rng(23)
    size = 977  # deliberately not a multiple of any SIMD width
    nobs_1 = rng.integers(5, 200_000, size).astype(float)
    nobs_2 = rng.integers(5, 200_000, size).astype(float)
    count_1 = np.minimum(np.floor(nobs_1 * 10 ** rng.uniform(-5, 0, size)), nobs_1)
    count_2 = np.minimum(np.floor(nobs_2 * 10 ** rng.uniform(-5, 0, size)), nobs_2)

    for test_type in ("absolute", "relative"):
        method = _method("score", test_type)
        batch = method.from_suffstats_array(
            {"count": count_1, "nobs": nobs_1}, {"count": count_2, "nobs": nobs_2}
        )
        for index in range(0, size, 61):
            scalar = method.from_suffstats(
                Fraction(count=count_1[index], nobs=nobs_1[index]),
                Fraction(count=count_2[index], nobs=nobs_2[index]),
            )
            for field in ("left_bound", "right_bound"):
                got, want = getattr(batch, field)[index], getattr(scalar, field)
                assert (got == want) or (math.isnan(got) and math.isnan(want)), (index, field)
