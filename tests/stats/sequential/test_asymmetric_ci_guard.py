"""STAT-3a: SE-by-CI-inversion refuses an asymmetric interval, loudly.

``se_from_ci_length`` infers ``SE = ci_length / (2z)``. That is the standard error
only for an ``effect ± z·SE`` interval; for a score/Fieller-type interval it returns
the mean half-width over ``z`` — a finite number that is not the SE — and
``sequentialize`` then centres a symmetric always-valid interval on it. No NaN, no
exception, silently wrong, and seven of the eleven entry points are inside the A/A
matrix that would have to certify the error (docs/specs/m13-implementation-plan.md
§6a, D17).

These tests pin the guard's three properties: it FIRES for an asymmetric method at
every entry, it is resolved per INSTANCE (a param can switch the interval shape —
STAT-3 ships Miettinen–Nurminen as an identity-flagged param on ``z-test``, so a
class-level flag would answer for the default params and miss it), and it is a
no-op for every method that ships today (byte parity — no number moves).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from abkit.stats.base import BaseMethod, ParamSpec
from abkit.stats.exceptions import AsymmetricCIError, StatsError
from abkit.stats.factory import create_method
from abkit.stats.registry import available_methods, get_method_class
from abkit.stats.samples import Fraction
from abkit.stats.sequential import (
    require_symmetric_ci,
    se_from_ci_length,
    sequentialize,
    to_always_valid,
)
from abkit.stats.sequential.confidence_sequence import se_from_ci_length_array

ALPHA = 0.05


class _Dummy(BaseMethod):
    """Unregistered, never scores: the guard reads only ``asymmetric_ci``/``name``."""

    name = "symmetric-dummy"

    def from_samples(self, sample_1: Any, sample_2: Any) -> Any:
        raise NotImplementedError

    def from_suffstats(self, stats_1: Any, stats_2: Any) -> Any:
        raise NotImplementedError


class _AsymmetricDummy(_Dummy):
    """The hostile method: declares the interval shape the inversion cannot handle."""

    name = "asymmetric-dummy"
    asymmetric_ci = True


class _ParamSwitchedDummy(_Dummy):
    """STAT-3's shape: one class whose INTERVAL is chosen by an identity-flagged param."""

    name = "param-switched-dummy"
    param_specs = (ParamSpec("interval", (str,), "wald", choices=("wald", "score")),)

    def __init__(self, alpha: float = 0.05, **params: Any) -> None:
        super().__init__(alpha=alpha, **params)
        self.asymmetric_ci = self.params["interval"] == "score"


def _a_real_result() -> Any:
    """A genuine symmetric z-test result — the payload ``to_always_valid`` widens."""
    method = create_method("z-test", alpha=ALPHA)
    return method.compare_pair(Fraction(500, 5000, name="a"), Fraction(560, 5000, name="b"))


# --- the guard fires at every entry point -------------------------------------------


def test_scalar_inversion_refuses_an_asymmetric_method() -> None:
    with pytest.raises(AsymmetricCIError) as exc:
        se_from_ci_length(0.4, ALPHA, method=_AsymmetricDummy(alpha=ALPHA))
    assert "asymmetric-dummy" in str(exc.value)
    assert "se_from_ci_length" in str(exc.value)


def test_array_inversion_refuses_an_asymmetric_method() -> None:
    """The batch entry is the A/A hot path — the instrument must not compute either."""
    with pytest.raises(AsymmetricCIError):
        se_from_ci_length_array(np.array([0.4, 0.5]), ALPHA, method=_AsymmetricDummy(alpha=ALPHA))


def test_to_always_valid_refuses_an_asymmetric_method() -> None:
    with pytest.raises(AsymmetricCIError):
        to_always_valid(_a_real_result(), 0.01, ALPHA, method=_AsymmetricDummy(alpha=ALPHA))


def test_the_refusal_names_the_way_out() -> None:
    """A stated limitation, not just a stack trace: the message says what to declare."""
    with pytest.raises(AsymmetricCIError) as exc:
        require_symmetric_ci(_AsymmetricDummy(alpha=ALPHA), entry="probe")
    assert "supports_sequential=False" in str(exc.value)


# --- resolved per INSTANCE, which is the whole point ---------------------------------


def test_the_flag_is_resolved_per_instance_not_per_class() -> None:
    """A param-switched interval is the case a ClassVar guard would have missed.

    The class default is symmetric — a caller reading ``cls.asymmetric_ci`` would sail
    straight through the configuration STAT-3 actually ships.
    """
    assert _ParamSwitchedDummy.asymmetric_ci is False

    wald = _ParamSwitchedDummy(alpha=ALPHA)
    score = _ParamSwitchedDummy(alpha=ALPHA, interval="score")
    assert wald.asymmetric_ci is False
    assert score.asymmetric_ci is True

    assert se_from_ci_length(0.4, ALPHA, method=wald) == pytest.approx(0.4 / (2 * 1.959963985))
    with pytest.raises(AsymmetricCIError):
        se_from_ci_length(0.4, ALPHA, method=score)


def test_a_class_is_refused_as_a_programming_error() -> None:
    """Handing the class in must not be answered as 'symmetric' — it is unanswerable."""
    with pytest.raises(TypeError) as exc:
        se_from_ci_length(0.4, ALPHA, method=_ParamSwitchedDummy)  # type: ignore[arg-type]
    assert "instance" in str(exc.value)


def test_a_non_method_is_refused() -> None:
    with pytest.raises(TypeError):
        se_from_ci_length(0.4, ALPHA, method="z-test")  # type: ignore[arg-type]


# --- behaviour neutrality: nothing that ships today is asymmetric --------------------


#: Every configuration that legitimately builds an asymmetric interval, as
#: ``(method, param, value)``. STAT-3 put the first entry here; anything else the
#: roster below discovers is an unrecorded deviation.
DECLARED_ASYMMETRIC = {("z-test", "interval", "score")}


def test_no_method_declares_a_symmetric_ci_and_then_builds_an_asymmetric_one() -> None:
    """The roster gate, ENUMERATING PARAMS — the form STAT-3 made reachable.

    STAT-3a's version read ``get_method_class(name).asymmetric_ci``, i.e. the class
    DEFAULT, and asserted the set was empty. That is still true and now means almost
    nothing: STAT-3 ships the first method whose asymmetry is selected by a *param*
    (``z-test`` + ``interval: score``), so the gate written to anticipate exactly
    that shape could not see it — the guard's own blind spot, in the guard's own
    test. The sweep below constructs every choice of every identity-flagged param and
    checks the BOUND instance, which is the granularity the flag is defined at.

    A new asymmetric configuration is exactly the change that must be conscious: it
    makes the always-valid mode unavailable for that configuration (or demands the
    critical value enter the construction — plan §6a item 2).
    """
    found: set[tuple[str, str, object]] = set()
    for name in available_methods():
        method_cls = get_method_class(name)
        assert not method_cls.asymmetric_ci, f"{name} declares asymmetric_ci at CLASS level"
        for spec in method_cls.param_specs:
            for choice in spec.choices or ():
                if choice == spec.default:
                    continue
                try:
                    bound = create_method(name, alpha=0.05, params={spec.name: choice})
                except StatsError:
                    continue  # quarantined / invalid combination — not a configuration
                if bound.asymmetric_ci:
                    found.add((name, spec.name, choice))
    assert found == DECLARED_ASYMMETRIC, (
        f"undeclared asymmetric configurations {sorted(found - DECLARED_ASYMMETRIC)} / "
        f"missing {sorted(DECLARED_ASYMMETRIC - found)} — the sequential mode cannot widen "
        "their intervals; record the deviation in docs/specs/statistics-changes.md"
    )


def test_a_symmetric_method_round_trips_unchanged() -> None:
    """The guarded recovery is the same arithmetic it always was (no number moves)."""
    method = create_method("z-test", alpha=ALPHA)
    se = se_from_ci_length(0.4, ALPHA, method=method)
    assert se == pytest.approx(0.4 / (2 * 1.959963985), rel=1e-9)
    lo, hi, _ = sequentialize(0.1, se, 0.01, ALPHA)
    assert math.isfinite(lo) and math.isfinite(hi)


def test_an_asymmetric_method_that_declares_itself_non_sequential_is_never_inverted() -> None:
    """The documented way to ship one before plan §6a item 2 exists: opt out, no error.

    ``supports_sequential=False`` is what every gate (driver, analyze, explore's
    ``av_pairs``, the A/A ``_cell_tau2``) already tests, so such a method's series
    simply stays fixed — the guard is never reached and nothing raises.
    """

    class _OptedOut(_AsymmetricDummy):
        name = "asymmetric-opted-out"
        supports_sequential = False

    from abkit.validate.scoring import _cell_tau2

    assert _cell_tau2(None, _OptedOut(alpha=ALPHA), share_a=0.5, anchor_seed=1) is None
