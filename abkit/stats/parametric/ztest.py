"""``z-test`` — two-proportion pooled z-test (baseline §3.2, catalogue "ZTest").

Reproduced verbatim, INCLUDING two documented legacy quirks (flagged in
docs/specs/statistics-changes.md §3 — never fixed silently):

- sign quirk: the z statistic uses ``prop_1 − prop_2`` while the reported effect
  uses ``prop_2 − prop_1``. The p-value is symmetric (``2·min(cdf, sf)``) so it
  is unaffected, but the orientations deliberately differ (legacy parity).
- relative branch: ``std_effect`` is naively divided by ``prop_1`` — there is NO
  delta-method covariance term (unlike the t-test family). A delta-consistent
  relative z-test is a possible v2 version bump if the A/A matrix shows
  under-coverage.

:class:`Fraction` inputs ARE the sufficient statistics (count/nobs), so
``from_samples`` simply delegates to ``from_suffstats`` — one math path.
Hygiene H5: a zero control proportion under ``relative`` (and a degenerate
pooled proportion of 0 or 1) yields NaN outputs plus a recorded warning, never
an exception.

**``interval: score`` (m13 STAT-3, opt-in)** replaces the interval — and ONLY
the interval — with the inversion of the score test the p-value already is
(:mod:`abkit.stats.proportion_score`). The p-value branch below is untouched
byte-for-byte, so opting in moves no reported p — with ONE exception it also
fixes: a table whose pooled variance is zero (both arms at the same degenerate
proportion) has no pooled statistic at all, and the score construction answers
``p = 1`` beside a real interval where the legacy branch returned NaN. What else
moves is the pair of bounds, which stop being ``effect ± z·σ̂₀`` (a variance frozen at the null,
valid nowhere else) and become the set of contrasts the same statistic does not
reject. See ``docs/specs/statistics-changes.md`` §4.4 for the deviation record.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import scipy.special as special

from abkit.stats.base import (
    CALCULATE_MDE_PARAM,
    POWER_PARAM,
    TEST_TYPE_PARAM,
    BaseMethod,
    ParamSpec,
    require_pair_type,
    suffstats_pair_columns,
)
from abkit.stats.effects import (
    BatchEffectResult,
    FloatArray,
    LazyNormal,
    NormalTest,
    _two_sided_quantiles,
)
from abkit.stats.power import get_fraction_mde
from abkit.stats.proportion_score import score_interval_difference, score_interval_ratio
from abkit.stats.registry import register
from abkit.stats.result import TestResult
from abkit.stats.samples import Fraction

#: Column keys of the batch entry — the ``Fraction`` sufficient statistics.
ZTEST_ARRAY_KEYS = ("count", "nobs")

INTERVAL_PARAM = ParamSpec(
    name="interval",
    types=(str,),
    default="pooled",
    choices=("pooled", "score"),
    # The instance-level asymmetry (m13 STAT-3a) is now DECLARED here rather than
    # resolved in __init__: STAT-4 added a second param-switched interval, and a
    # knob-dependent capability resolved per class is the shape that rots.
    asymmetric_values=("score",),
    description=(
        "Confidence-interval construction. 'pooled' (default, legacy parity) is "
        "effect ± z·σ̂₀ with σ̂₀ the NULL (pooled) standard error — coherent with the "
        "p-value but a valid interval only at zero. 'score' inverts the same "
        "statistic at every candidate contrast (Miettinen–Nurminen in its "
        "Farrington–Manning form): valid everywhere, asymmetric, inside [-1, 1], "
        "and coherent with the p-value BY CONSTRUCTION. Identity-bearing — opting "
        "in starts a new results series (m13 STAT-3)."
    ),
)

#: Relative half-width above which the lift is reported but called out as weakly
#: identified. The precision law is ``critical·√(1/x₁ + 1/x₂)`` — a function of
#: CONVERSIONS, not of units: ten times the traffic at a tenth of the rate buys
#: nothing. 0.5 means "not pinned down to better than ±50%", and because the
#: critical value is in it the threshold tightens by itself as the correction
#: shrinks alpha. A warning, never a suppression: the interval is correct, it is
#: the reading of it as a measurement that is not.
RELATIVE_IDENTIFICATION_HALF_WIDTH = 0.5


def _identification_warning(count_1: float, count_2: float, critical: float) -> list[str]:
    """Warn when the relative interval is arithmetically right and practically empty.

    Under ``interval: score`` only — the default path is byte-frozen, and a new
    warning is still a new persisted cell. The threshold is on the interval's own
    half-width so it cannot be read as a rule about traffic.

    An empty TREATMENT cell is the widest case the score construction still bounds:
    the lift interval runs down to −100% and up to a finite value, so the message
    must not call it unbounded. (An empty CONTROL cell never arrives — the relative
    point estimate is undefined there and H5 refuses the whole row first.)
    """
    if count_2 <= 0.0:
        precision = "a [−100%, +…] "
    else:
        half_width = critical * math.sqrt(1.0 / count_1 + 1.0 / count_2)
        if half_width <= RELATIVE_IDENTIFICATION_HALF_WIDTH:
            return []
        precision = f"a ±{half_width:.0%} "
    needed = 2.0 * (critical / RELATIVE_IDENTIFICATION_HALF_WIDTH) ** 2
    return [
        f"relative effect weakly identified: {count_1:.0f} and {count_2:.0f} conversions "
        f"give {precision}interval at this alpha; ±"
        f"{RELATIVE_IDENTIFICATION_HALF_WIDTH:.0%} needs ~{needed:.0f} CONVERSIONS per arm "
        "(the width law reads counts, not exposed units)"
    ]


@register(aliases=("ztest",))
class ZTest(BaseMethod):
    name = "z-test"
    input_kind = "fraction"
    param_specs = (TEST_TYPE_PARAM, CALCULATE_MDE_PARAM, POWER_PARAM, INTERVAL_PARAM)
    supports_vectorized = True

    @property
    def _score_interval(self) -> bool:
        return bool(self.params["interval"] == "score")

    def from_samples(self, sample_1: Fraction, sample_2: Fraction) -> TestResult:
        return self.from_suffstats(sample_1, sample_2)

    def from_suffstats(self, stats_1: Fraction, stats_2: Fraction) -> TestResult:
        require_pair_type(self.name, stats_1, stats_2, Fraction)
        result_warnings: list[str] = []
        nan = float("nan")

        prop_1, prop_2 = stats_1.prop, stats_2.prop
        nobs_1, nobs_2 = stats_1.nobs, stats_2.nobs
        prop_combined = (stats_1.count + stats_2.count) / (nobs_1 + nobs_2)
        pooled_var = prop_combined * (1.0 - prop_combined) * (1.0 / nobs_1 + 1.0 / nobs_2)
        std_effect = math.sqrt(pooled_var)

        countable = nobs_1 > 0 and nobs_2 > 0 and math.isfinite(prop_combined)
        if std_effect > 0.0 and math.isfinite(std_effect):
            # Legacy sign quirk kept verbatim: z uses prop_1 − prop_2, effect prop_2 − prop_1.
            # WP1 A1: ndtr(z)/ndtr(−z) ARE norm.cdf/norm.sf — byte parity golden-pinned.
            z_stat = (prop_1 - prop_2) / std_effect
            pvalue = float(2.0 * min(special.ndtr(z_stat), special.ndtr(-z_stat)))
        elif self._score_interval and countable:
            # Both arms sit at the SAME degenerate proportion (all-zero or all-one),
            # so the pooled statistic is 0/0 — not missing information, but the most
            # perfectly null table there is. The score construction is defined here
            # (it is where the whole boundary argument was made) and reports p = 1
            # beside a real interval, instead of a NaN row nobody can read.
            pvalue = 1.0
        else:
            result_warnings.append(
                "pooled proportion variance is zero (pooled proportion is 0 or 1); "
                "returning NaN test outputs"
            )
            pvalue = nan

        effect = prop_2 - prop_1
        if self.test_type == "relative":
            if prop_1 == 0.0 or not math.isfinite(prop_1):
                result_warnings.append(
                    "relative effect undefined: control proportion is zero or non-finite; "
                    "returning NaN (see statistics-changes.md H5)"
                )
                effect = std_effect = pvalue = nan
            else:
                effect /= prop_1
                std_effect /= prop_1

        distribution: LazyNormal | None = None
        if self._score_interval and countable and math.isfinite(effect):
            # The bounds come from the ONE array kernel (a length-1 batch), so the
            # scalar and vectorized entries are bit-identical by construction rather
            # than by two implementations agreeing — the M7 WP2 discipline.
            counts = (
                np.array([stats_1.count], dtype=np.float64),
                np.array([float(nobs_1)]),
                np.array([stats_2.count], dtype=np.float64),
                np.array([float(nobs_2)]),
            )
            _, z_high = _two_sided_quantiles(self.alpha)
            if self.test_type == "relative":
                lower, upper = score_interval_ratio(*counts, z_high)
                left_bound, right_bound = float(lower[0]) - 1.0, float(upper[0]) - 1.0
                result_warnings.extend(
                    _identification_warning(stats_1.count, stats_2.count, z_high)
                )
            else:
                lower, upper = score_interval_difference(*counts, z_high)
                left_bound, right_bound = float(lower[0]), float(upper[0])
            ci_length = right_bound - left_bound
        elif math.isfinite(effect) and math.isfinite(std_effect) and std_effect > 0.0:
            distribution = LazyNormal(effect, std_effect)
            z_low, z_high = _two_sided_quantiles(self.alpha)
            left_bound = float(z_low * std_effect + effect)
            right_bound = float(z_high * std_effect + effect)
            ci_length = right_bound - left_bound
        else:
            left_bound = right_bound = ci_length = nan

        mde_1 = mde_2 = None
        if self.params["calculate_mde"]:
            mde_1 = get_fraction_mde(
                prop_1,
                stats_1.sample_size,
                test_type=self.test_type,
                alpha=self.alpha,
                power=self.params["power"],
                ratio=nobs_2 / nobs_1,
            )
            mde_2 = get_fraction_mde(
                prop_2,
                stats_2.sample_size,
                test_type=self.test_type,
                alpha=self.alpha,
                power=self.params["power"],
                ratio=nobs_1 / nobs_2,
            )

        # The z-test computes its test inline (the legacy sign quirk above), so the
        # shared assembly consumes a NormalTest-shaped container, not normal_test().
        test = NormalTest(
            effect=effect,
            left_bound=left_bound,
            right_bound=right_bound,
            ci_length=ci_length,
            pvalue=pvalue,
            reject=bool(pvalue < self.alpha),
            distribution=distribution,
        )
        return self._result_from_normal_test(
            test,
            name_1=stats_1.name,
            name_2=stats_2.name,
            value_1=prop_1,
            value_2=prop_2,
            std_1=stats_1.std,
            std_2=stats_2.std,
            size_1=stats_1.sample_size,
            size_2=stats_2.sample_size,
            mde_1=mde_1,
            mde_2=mde_2,
            method_warnings=result_warnings,
        )

    def from_suffstats_array(
        self,
        arrays_1: Mapping[str, FloatArray],
        arrays_2: Mapping[str, FloatArray] | None = None,
    ) -> BatchEffectResult:
        """Array-wise ``from_suffstats`` (M7 WP2). Column keys: ``count``, ``nobs``.

        The inline scalar formula reproduced verbatim — INCLUDING the legacy
        sign quirk (z uses ``prop_1 − prop_2``, effect ``prop_2 − prop_1``) and
        the relative-branch zero-``prop_1`` H5 guard, both as masks. Degenerate
        rows (pooled proportion 0/1, zero control proportion, ``nobs = 0``) →
        NaN, never an exception; parity is pinned by
        ``tests/stats/test_vectorized_parity.py``.
        """
        (count_1, nobs_1), (count_2, nobs_2) = suffstats_pair_columns(
            arrays_1, arrays_2, ZTEST_ARRAY_KEYS, self.name
        )
        nan = float("nan")

        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            prop_1 = count_1 / nobs_1
            prop_2 = count_2 / nobs_2
            prop_combined = (count_1 + count_2) / (nobs_1 + nobs_2)
            pooled_var = prop_combined * (1.0 - prop_combined) * (1.0 / nobs_1 + 1.0 / nobs_2)
            std_effect = np.sqrt(pooled_var)

            # Legacy sign quirk kept verbatim: z uses prop_1 − prop_2, effect prop_2 − prop_1.
            valid_z = (std_effect > 0.0) & np.isfinite(std_effect)
            countable = (nobs_1 > 0.0) & (nobs_2 > 0.0) & np.isfinite(prop_combined)
            z_stat = (prop_1 - prop_2) / std_effect
            # The exactly-null degenerate table under `interval: score` — see the scalar
            # entry; both arms at one degenerate proportion is p = 1, not missing
            # information.
            degenerate = np.where(countable, 1.0, nan) if self._score_interval else nan
            pvalue = np.where(
                valid_z,
                2.0 * np.minimum(special.ndtr(z_stat), special.ndtr(-z_stat)),
                degenerate,
            )

            effect = prop_2 - prop_1
            if self.test_type == "relative":
                relative_bad = (prop_1 == 0.0) | ~np.isfinite(prop_1)
                effect = np.where(relative_bad, nan, effect / prop_1)
                std_effect = np.where(relative_bad, nan, std_effect / prop_1)
                pvalue = np.where(relative_bad, nan, pvalue)

            z_low, z_high = _two_sided_quantiles(self.alpha)
            if self._score_interval:
                score_valid = countable & np.isfinite(effect)
                if self.test_type == "relative":
                    lower, upper = score_interval_ratio(count_1, nobs_1, count_2, nobs_2, z_high)
                    lower, upper = lower - 1.0, upper - 1.0
                else:
                    lower, upper = score_interval_difference(
                        count_1, nobs_1, count_2, nobs_2, z_high
                    )
                left_bound = np.where(score_valid, lower, nan)
                right_bound = np.where(score_valid, upper, nan)
            else:
                ci_valid = np.isfinite(effect) & np.isfinite(std_effect) & (std_effect > 0.0)
                left_bound = np.where(ci_valid, z_low * std_effect + effect, nan)
                right_bound = np.where(ci_valid, z_high * std_effect + effect, nan)
            ci_length = right_bound - left_bound
        return BatchEffectResult(
            effect=effect,
            left_bound=left_bound,
            right_bound=right_bound,
            ci_length=ci_length,
            pvalue=pvalue,
        )
