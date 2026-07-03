"""Byte-exact ``method_config_id`` identity tests (quorum must-fix).

docs/specs/declarative-config.md §7 / docs/specs/quorum-review.md "Canonical
method_config_id with a byte-exact test": the hash payload is
``registry_name + canonical_sorted_JSON(non-default identity params)`` with the
``ALGORITHM_VERSION`` appended only when > 1. Expectations here are literal bytes —
never computed via the helper under test.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from abkit.stats.base import (
    N_SAMPLES_PARAM,
    SEED_PARAM,
    TEST_TYPE_PARAM,
    BaseMethod,
    ParamSpec,
    compute_method_config_id,
    method_config_payload,
)
from abkit.stats.exceptions import MethodParamError, UnknownMethodError
from abkit.stats.factory import create_method
from abkit.stats.parametric.ttest import TTest
from abkit.stats.registry import get_method_class
from abkit.utils.json_utils import json_dumps_sorted


class _SeededDummy(BaseMethod):
    """Unregistered bootstrap-shaped method: exposes seed/n_samples for identity tests."""

    name = "seeded-dummy"
    param_specs = (TEST_TYPE_PARAM, N_SAMPLES_PARAM, SEED_PARAM)

    # importing TestResult here would trip pytest's Test* class collection; the
    # dummy never produces results, so the abstract hooks just raise.
    def from_samples(self, sample_1: Any, sample_2: Any) -> Any:
        raise NotImplementedError

    def from_suffstats(self, stats_1: Any, stats_2: Any) -> Any:
        raise NotImplementedError


# --- payload bytes (byte-exact) ------------------------------------------------------


def test_payload_empty_params_v1() -> None:
    assert method_config_payload("z-test", {}, 1) == b"z-test{}"


def test_payload_single_param_v1() -> None:
    assert (
        method_config_payload("cuped-t-test", {"test_type": "absolute"}, 1)
        == b'cuped-t-test{"test_type":"absolute"}'
    )


def test_payload_version_2_appends_version_tag() -> None:
    assert method_config_payload("z-test", {}, 2) == b"z-test{}2"
    assert (
        method_config_payload("cuped-t-test", {"test_type": "absolute"}, 2)
        == b'cuped-t-test{"test_type":"absolute"}2'
    )


def test_payload_params_sorted_canonically() -> None:
    """Insertion order must not matter — keys are sorted, JSON is whitespace-free."""
    params = {"test_type": "absolute", "calculate_mde": True}
    assert (
        method_config_payload("t-test", params, 1)
        == b't-test{"calculate_mde":true,"test_type":"absolute"}'
    )


def test_method_config_id_pinned_hex() -> None:
    """Pin the hash over the literal expected bytes — the mapping must never change."""
    expected = hashlib.sha256(b"z-test{}").hexdigest()
    assert expected == "207c93e4891ce849e30b56f1b5e5d9bacd774e2434a2a22e195cae9cc9c69a77"
    assert compute_method_config_id("z-test", {}, 1) == expected


def test_ttest_method_config_id_matches_literal_bytes() -> None:
    method = TTest(alpha=0.05, test_type="absolute", calculate_mde=True)
    expected = hashlib.sha256(b't-test{"calculate_mde":true,"test_type":"absolute"}').hexdigest()
    assert method.method_config_id == expected


# --- identity_params ------------------------------------------------------------------


def test_identity_params_all_defaults_is_empty() -> None:
    method = TTest(alpha=0.05)
    assert method.identity_params == {}
    assert method.method_params == {}
    assert method_config_payload(method.name, method.identity_params, 1) == b"t-test{}"


def test_identity_params_drops_defaults_keeps_non_defaults() -> None:
    method = TTest(alpha=0.05, test_type="absolute", power=0.8)  # power == default
    assert method.identity_params == {"test_type": "absolute"}


def test_identity_params_two_non_defaults_serialise_sorted() -> None:
    method = TTest(alpha=0.05, calculate_mde=True, test_type="absolute")
    assert (
        json_dumps_sorted(method.identity_params) == '{"calculate_mde":true,"test_type":"absolute"}'
    )


def test_seed_is_identity_excluded_on_bootstrap_shaped_method() -> None:
    """SEED_PARAM is identity=False — a non-default seed never enters the hash (H2)."""
    assert SEED_PARAM.identity is False
    method = _SeededDummy(alpha=0.05, seed=42, n_samples=500)
    assert method.params["seed"] == 42
    assert method.identity_params == {"n_samples": 500}
    assert (
        method.method_config_id == _SeededDummy(alpha=0.05, seed=7, n_samples=500).method_config_id
    )


def test_method_params_alias_equals_identity_params() -> None:
    method = TTest(alpha=0.05, test_type="absolute")
    assert method.method_params == method.identity_params


# --- parameter validation --------------------------------------------------------------


def test_unknown_param_lists_valid_ones() -> None:
    with pytest.raises(MethodParamError) as excinfo:
        TTest(alpha=0.05, bogus=1)
    message = str(excinfo.value)
    assert "bogus" in message
    for valid in ("calculate_mde", "power", "test_type"):
        assert valid in message


def test_seed_on_ttest_raises_with_bootstrap_hint() -> None:
    with pytest.raises(MethodParamError, match="bootstrap"):
        TTest(alpha=0.05, seed=42)


def test_bool_rejected_for_int_param_via_paramspec() -> None:
    with pytest.raises(MethodParamError, match="got bool"):
        N_SAMPLES_PARAM.validate(True, "bootstrap")


def test_bool_rejected_for_int_param_on_bootstrap_shaped_method() -> None:
    with pytest.raises(MethodParamError, match="got bool"):
        _SeededDummy(alpha=0.05, n_samples=True)


def test_bool_rejected_for_int_param_on_registered_bootstrap_method() -> None:
    """Guarded: only runs once the real bootstrap method lands in the registry."""
    try:
        get_method_class("bootstrap")
    except UnknownMethodError:
        pytest.skip("bootstrap method not registered yet (M1 bootstrap files in progress)")
    with pytest.raises(MethodParamError, match="bool"):
        create_method("bootstrap", params={"n_samples": True})


def test_choices_enforced() -> None:
    with pytest.raises(MethodParamError, match="one of"):
        TTest(alpha=0.05, test_type="weird")


def test_float_param_accepts_int() -> None:
    spec = ParamSpec(name="x", types=(float,), default=0.5)
    value = spec.validate(1, "test-method")
    assert value == 1.0
    assert isinstance(value, float)


def test_float_param_range_and_finiteness_enforced() -> None:
    # power is bounded to (0, 1) exclusive; NaN/inf are never valid param values.
    with pytest.raises(MethodParamError, match="within \\(0.0, 1.0\\)"):
        TTest(alpha=0.05, power=1.5)
    with pytest.raises(MethodParamError, match="within"):
        TTest(alpha=0.05, power=1)
    with pytest.raises(MethodParamError, match="finite"):
        TTest(alpha=0.05, power=float("nan"))


def test_wrong_type_rejected() -> None:
    with pytest.raises(MethodParamError, match="must be str"):
        TTest(alpha=0.05, test_type=1)


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
def test_alpha_bounds(alpha: float) -> None:
    with pytest.raises(MethodParamError, match="alpha"):
        TTest(alpha=alpha)


def test_none_param_value_falls_back_to_default() -> None:
    method = TTest(alpha=0.05, test_type=None)
    assert method.params["test_type"] == "relative"


def test_paramspec_choices_and_types_direct() -> None:
    spec = ParamSpec(name="k", types=(int,), default=1, choices=(1, 2, 3))
    assert spec.validate(2, "m") == 2
    with pytest.raises(MethodParamError, match="one of"):
        spec.validate(4, "m")
