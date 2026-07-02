"""Tests for the method registry, quarantine policy and factory.

docs/specs/statistics-changes.md §3: quarantined legacy methods raise a hard,
explanatory error — never a silent substitution. Registry names are canonical
kebab-case; lookup normalises case/underscores and resolves aliases.
"""

from __future__ import annotations

from typing import Any

import pytest

from abkit.stats.exceptions import QuarantinedMethodError, UnknownMethodError
from abkit.stats.factory import create_method
from abkit.stats.parametric.ttest import TTest
from abkit.stats.registry import (
    QUARANTINED_METHODS,
    available_methods,
    get_method_class,
    normalize_method_name,
    register,
)


def test_normalize_method_name() -> None:
    assert normalize_method_name("T_Test") == "t-test"
    assert normalize_method_name("  CUPED_T_TEST  ") == "cuped-t-test"
    assert normalize_method_name("z-test") == "z-test"


def test_lookup_normalises_underscores_and_case() -> None:
    assert get_method_class("T_Test") is TTest
    assert get_method_class("t-test") is TTest


def test_alias_ttest_resolves() -> None:
    assert get_method_class("ttest") is TTest
    assert get_method_class("TTEST") is TTest


def test_unknown_method_error_lists_available() -> None:
    with pytest.raises(UnknownMethodError) as excinfo:
        get_method_class("no-such-method")
    message = str(excinfo.value)
    assert "no-such-method" in message
    assert "available:" in message
    assert "t-test" in message


def test_available_methods_sorted_and_contains_ttest() -> None:
    methods = available_methods()
    assert "t-test" in methods
    assert list(methods) == sorted(methods)


# --- quarantine (statistics-changes.md §3) --------------------------------------------


def test_quarantined_method_raises_with_pointer_to_changes_doc() -> None:
    with pytest.raises(QuarantinedMethodError, match="statistics-changes"):
        get_method_class("poisson-post-normed-bootstrap")


def test_quarantined_method_lookup_is_normalised() -> None:
    with pytest.raises(QuarantinedMethodError):
        get_method_class("Poisson_Post_Normed_Bootstrap")


def test_quarantined_method_via_factory() -> None:
    with pytest.raises(QuarantinedMethodError, match="statistics-changes"):
        create_method("poisson-post-normed-bootstrap")


def test_quarantine_list_is_canonical() -> None:
    assert "poisson-post-normed-bootstrap" in QUARANTINED_METHODS
    for name in QUARANTINED_METHODS:
        assert normalize_method_name(name) == name


# --- registration rules -----------------------------------------------------------------


def test_duplicate_name_registration_raises() -> None:
    with pytest.raises(ValueError, match="already registered"):

        @register
        class _DuplicateTTest(TTest):
            name = "t-test"


def test_non_kebab_name_registration_raises() -> None:
    with pytest.raises(ValueError, match="kebab-case"):

        @register
        class _BadName(TTest):
            name = "T_Test"


def test_missing_name_registration_raises() -> None:
    with pytest.raises(ValueError, match="must define"):

        @register
        class _NoName(TTest):
            name = None  # type: ignore[assignment]


def test_registering_quarantined_name_raises() -> None:
    with pytest.raises(ValueError, match="quarantined"):

        @register
        class _Sneaky(TTest):
            name = "poisson-post-normed-bootstrap"


def test_reregistering_same_class_is_idempotent() -> None:
    assert register(TTest) is TTest
    assert get_method_class("t-test") is TTest


# --- factory -------------------------------------------------------------------------------


def test_create_method_passes_alpha_and_params() -> None:
    method = create_method("ttest", alpha=0.01, params={"test_type": "absolute"})
    assert isinstance(method, TTest)
    assert method.alpha == 0.01
    assert method.params["test_type"] == "absolute"


def test_create_method_defaults() -> None:
    method = create_method("t-test")
    assert method.alpha == 0.05
    assert method.params["test_type"] == "relative"


def test_create_method_none_params() -> None:
    method: Any = create_method("t-test", alpha=0.1, params=None)
    assert method.alpha == 0.1
