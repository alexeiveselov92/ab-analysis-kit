"""m13 STAT-4: ``interval: delta | fieller`` on the mean methods — the deviation.

What this file pins is the *contract*, not the math (that is
``test_fieller_interval.py``): the default moves nothing, opting in forks the
series identity, the p-value becomes the absolute test's **exactly**, the
inert combination is refused rather than ignored, and the capability flag
reaches the bound instance so every STAT-3a entry point sees it.

Every method that routes its relative branch through the shared dispatcher is
swept, because the whole point of the shared ``ParamSpec`` is that adopting it
cannot come with a per-class mistake.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from abkit.stats import available_methods, create_method, get_method_class
from abkit.stats.base import RELATIVE_INTERVAL_PARAM
from abkit.stats.exceptions import MethodParamError
from abkit.stats.relative_interval import DELTA, FIELLER
from abkit.stats.samples import (
    PairedSufficientStats,
    RatioSufficientStats,
    Sample,
    SufficientStats,
)

ALPHA = 0.05

#: The methods that adopted the shared param, and a suffstats pair for each.
#: Derived from the registry in ``test_the_roster_is_exactly_the_mean_methods``
#: so this list cannot silently fall behind the code.
FIELLER_METHODS = ("t-test", "cuped-t-test", "paired-t-test", "paired-cuped-t-test", "ratio-delta")


def _pair(method_name: str):
    rng = np.random.default_rng(4242)
    if method_name == "ratio-delta":
        return (
            RatioSufficientStats(
                n=4000, mean_num=2.0, m2_num=1200.0, mean_den=20.0, m2_den=90_000.0, c_nd=2400.0
            ),
            RatioSufficientStats(
                n=4100, mean_num=2.2, m2_num=1300.0, mean_den=20.5, m2_den=92_000.0, c_nd=2500.0
            ),
        ), None
    if method_name in ("paired-t-test", "paired-cuped-t-test"):
        size = 3000
        covariate_1 = rng.normal(10.0, 2.0, size)
        covariate_2 = rng.normal(10.0, 2.0, size)
        joint = PairedSufficientStats.from_samples(
            Sample(rng.normal(10.0, 2.0, size), cov_array=covariate_1, name="control"),
            Sample(rng.normal(10.6, 2.0, size), cov_array=covariate_2, name="treatment"),
        )
        return (joint, None), None
    values_1 = rng.normal(10.0, 2.0, 3000)
    values_2 = rng.normal(10.6, 2.0, 3100)
    covariate_1 = values_1 * 0.8 + rng.normal(0.0, 1.0, 3000)
    covariate_2 = values_2 * 0.8 + rng.normal(0.0, 1.0, 3100)
    stats_1 = SufficientStats.from_sample(Sample(values_1, cov_array=covariate_1, name="control"))
    stats_2 = SufficientStats.from_sample(Sample(values_2, cov_array=covariate_2, name="treatment"))
    return (stats_1, stats_2), None


def _result(method_name: str, **params):
    method = create_method(method_name, alpha=ALPHA, params=params)
    (stats_1, stats_2), _ = _pair(method_name)
    return method.from_suffstats(stats_1, stats_2)


# --- the roster ---------------------------------------------------------------------


def test_the_dispatch_vocabulary_is_the_schema_vocabulary() -> None:
    """Two literals in two modules cannot be held together by a comment.

    A value the schema accepts but the dispatcher does not recognise would take
    the legacy branch silently — the worst shape available here, since the
    operator wrote a knob and got the thing they were opting out of.
    """
    assert set(RELATIVE_INTERVAL_PARAM.choices or ()) == {DELTA, FIELLER}
    assert RELATIVE_INTERVAL_PARAM.default == DELTA


def test_the_roster_is_exactly_the_mean_methods() -> None:
    """Derived from the registry, so a sixth adopter cannot go untested.

    ``z-test`` is deliberately absent: its relative interval was answered by
    STAT-3's ratio-scale score construction, which is the exact analogue for
    proportions rather than Fieller's normal-theory approximation of it.
    """
    adopters = {
        name
        for name in available_methods()
        if any(spec is RELATIVE_INTERVAL_PARAM for spec in get_method_class(name).param_specs)
    }
    assert adopters == set(FIELLER_METHODS)
    assert "z-test" not in adopters


# --- the default does not move ------------------------------------------------------


@pytest.mark.parametrize("method_name", FIELLER_METHODS)
def test_the_default_is_the_legacy_delta_branch(method_name: str) -> None:
    """Omitting the param and writing it out give byte-identical results."""
    implicit = _result(method_name, test_type="relative")
    explicit = _result(method_name, test_type="relative", interval="delta")
    assert implicit.to_dict() == explicit.to_dict()
    assert implicit.method_params == {}  # a default never enters the identity


@pytest.mark.parametrize("method_name", FIELLER_METHODS)
def test_opting_in_forks_the_series_identity(method_name: str) -> None:
    """D4's safety: no ``ALGORITHM_VERSION`` bump, because the param orphans the
    series of the operator who opts in, at the moment they opt in."""
    legacy = create_method(method_name, alpha=ALPHA, params={"test_type": "relative"})
    opted = create_method(
        method_name, alpha=ALPHA, params={"test_type": "relative", "interval": "fieller"}
    )
    assert legacy.method_config_id != opted.method_config_id
    assert opted.method_params == {"interval": "fieller"}
    assert legacy.ALGORITHM_VERSION == 1


# --- the refusal of the inert combination -------------------------------------------


@pytest.mark.parametrize("method_name", FIELLER_METHODS)
def test_fieller_beside_an_absolute_estimand_is_refused_not_ignored(method_name: str) -> None:
    """It would compute nothing and still fork ``method_config_id``.

    Accepting it is the STAT-1b failure in miniature: a knob that reads as
    working while doing nothing. The message must name BOTH knobs, because
    either one is a legitimate thing to have meant.
    """
    with pytest.raises(MethodParamError) as exc:
        create_method(
            method_name, alpha=ALPHA, params={"test_type": "absolute", "interval": "fieller"}
        )
    message = str(exc.value)
    assert "interval" in message and "test_type" in message
    assert "'delta'" in message  # the way out is spelled, not implied


@pytest.mark.parametrize("method_name", FIELLER_METHODS)
def test_the_default_is_still_accepted_beside_an_absolute_estimand(method_name: str) -> None:
    """The refusal is about a NON-DEFAULT value — an absolute comparison must
    stay constructible without anyone deleting a param they never wrote."""
    create_method(method_name, alpha=ALPHA, params={"test_type": "absolute"})
    create_method(method_name, alpha=ALPHA, params={"test_type": "absolute", "interval": "delta"})


# --- what opting in changes ----------------------------------------------------------


@pytest.mark.parametrize("method_name", FIELLER_METHODS)
def test_the_relative_pvalue_becomes_the_absolute_one_bit_for_bit(method_name: str) -> None:
    """The coherence claim, as an EQUALITY.

    Under ``fieller`` the relative p-value is the absolute comparison's, because
    "θ = 0" and "μ₂ − μ₁ = 0" are one hypothesis and the interval inverts that
    test. Asserted with ``==``: the two go through the same expression in the
    same operand order, so a tolerance here would hide a second transcription.
    """
    absolute = _result(method_name, test_type="absolute")
    fieller = _result(method_name, test_type="relative", interval="fieller")
    delta = _result(method_name, test_type="relative")
    assert fieller.pvalue == absolute.pvalue
    assert fieller.reject == absolute.reject
    # ...and the legacy branch is the one that does NOT agree — otherwise this
    # test would pass against a no-op implementation.
    assert delta.pvalue != absolute.pvalue


@pytest.mark.parametrize("method_name", FIELLER_METHODS)
def test_the_point_estimate_is_untouched(method_name: str) -> None:
    """Fieller moves the interval and the p-value; the reported lift is the same
    number it always was."""
    assert _result(method_name, test_type="relative", interval="fieller").effect == (
        _result(method_name, test_type="relative").effect
    )


@pytest.mark.parametrize("method_name", FIELLER_METHODS)
def test_the_interval_is_asymmetric_and_says_so(method_name: str) -> None:
    """The flag is what every STAT-3a entry point reads, and the interval is in
    fact off-centre — asserted together so the flag cannot be a decoration."""
    method = create_method(
        method_name, alpha=ALPHA, params={"test_type": "relative", "interval": "fieller"}
    )
    assert method.asymmetric_ci is True
    assert get_method_class(method_name).asymmetric_ci is False  # class stays symmetric

    result = _result(method_name, test_type="relative", interval="fieller")
    centre = 0.5 * (result.left_bound + result.right_bound)
    assert centre != result.effect
    assert abs(centre - result.effect) < 0.5 * (result.right_bound - result.left_bound)


@pytest.mark.parametrize("method_name", FIELLER_METHODS)
def test_no_effect_distribution_is_offered(method_name: str) -> None:
    """A Fieller set is not the quantile range of any normal, so handing one out
    would let a caller re-derive bounds that are not the ones reported."""
    result = _result(method_name, test_type="relative", interval="fieller")
    assert result.effect_distribution is None
    assert _result(method_name, test_type="relative").effect_distribution is not None


@pytest.mark.parametrize("method_name", FIELLER_METHODS)
def test_the_interval_brackets_the_estimate_and_excludes_zero_with_the_pvalue(
    method_name: str,
) -> None:
    """One end-to-end sanity leg per method, so a mis-wired argument (the
    denominator's variance passed where the numerator's belongs, say) shows up
    on the real suffstats containers rather than only in the pure-math file."""
    result = _result(method_name, test_type="relative", interval="fieller")
    assert math.isfinite(result.left_bound) and math.isfinite(result.right_bound)
    assert result.left_bound < result.effect < result.right_bound
    excludes_zero = result.left_bound > 0.0 or result.right_bound < 0.0
    assert excludes_zero == (result.pvalue < ALPHA)
