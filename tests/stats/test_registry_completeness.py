"""Registry-completeness gate (M7 WP1 A6, docs/specs/m7-implementation-plan.md).

The plugin-registry invariant (CLAUDE.md "Methods are plugins" / architecture.md
"Methods — a plugin registry") is enforced entirely by *import side effects*: a
``BaseMethod`` subclass only reaches ``available_methods()`` if its module is
imported and its ``@register`` decorator runs. Nothing stops a future author
from adding a new method file under ``abkit/stats/parametric/`` or
``abkit/stats/bootstrap/`` without wiring it into the family package's
``__init__.py`` — the class would then be dead code: never registered, never
reachable via ``get_method_class``, never exercised by the registry-driven
contract sweeps in test_bootstrap_methods.py / test_parametric_methods.py (A5),
and CI would stay green.

This module closes that gap two ways:

1. ``test_every_concrete_method_class_is_registered`` walks the two family
   packages with ``pkgutil`` (independent of what each ``__init__.py`` chooses
   to re-export) so every module is imported at least once, then asserts every
   concrete (non-abstract) ``BaseMethod`` subclass found in memory is reachable
   by name through the registry.
2. ``test_registry_matches_documented_method_set`` pins the exact 12-name set
   documented in .claude/rules/architecture.md / CLAUDE.md (6 closed-form + 6
   bootstrap). This is a **completeness pin, not a freeze**: adding a 13th
   method is expected over time (M7-M17 adds new estimators) — the assertion
   just forces the author to consciously update this list (and the docs) in
   the same PR, rather than silently drifting.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import ModuleType

import abkit.stats.bootstrap as bootstrap_package
import abkit.stats.parametric as parametric_package
from abkit.stats.base import BaseMethod
from abkit.stats.registry import available_methods, get_method_class

#: The documented canonical set (architecture.md "Methods — a plugin registry
#: (12 registered)"): 6 closed-form + 6 bootstrap. Update deliberately alongside
#: the docs when a method is added or removed — see the module docstring.
DOCUMENTED_METHOD_NAMES: frozenset[str] = frozenset(
    {
        # closed-form (parametric)
        "t-test",
        "paired-t-test",
        "z-test",
        "cuped-t-test",
        "paired-cuped-t-test",
        "ratio-delta",
        # bootstrap
        "bootstrap",
        "paired-bootstrap",
        "poisson-bootstrap",
        "paired-poisson-bootstrap",
        "post-normed-bootstrap",
        "paired-post-normed-bootstrap",
    }
)


def _import_every_submodule(package: ModuleType) -> None:
    """Import every module under ``package``, regardless of its ``__init__.py``.

    ``pkgutil.walk_packages`` enumerates modules on disk; importing each one
    directly (rather than trusting the package's re-exports) is what catches a
    method file that exists but was never wired in — the actual "forgotten
    import silently un-registers" bug this test module exists to gate.
    """
    prefix = package.__name__ + "."
    for module_info in pkgutil.walk_packages(package.__path__, prefix):
        importlib.import_module(module_info.name)


def _all_base_method_subclasses() -> set[type[BaseMethod]]:
    """Recursively collect every ``BaseMethod`` subclass currently in memory.

    ``type.__subclasses__()`` is direct-children-only, so this walks the tree
    (covers e.g. ``PairedPoissonBootstrapTest(PoissonBootstrapTest)``, two
    levels below ``BaseBootstrapMethod``).
    """
    seen: set[type[BaseMethod]] = set()
    frontier: list[type[BaseMethod]] = [BaseMethod]
    while frontier:
        cls = frontier.pop()
        for subclass in cls.__subclasses__():
            if subclass not in seen:
                seen.add(subclass)
                frontier.append(subclass)
    return seen


def _concrete_method_classes() -> set[type[BaseMethod]]:
    """Every concrete (instantiable) ``BaseMethod`` subclass under the two family packages.

    Abstract family bases (``BaseBootstrapMethod``, ``BasePairedMethod``) are
    excluded via ``inspect.isabstract`` — they never carry a registry ``name``
    and are never registered themselves (base.py / registry.py ``register``).

    Scoped to classes whose ``__module__`` lives under ``abkit.stats.parametric``
    or ``abkit.stats.bootstrap``: ``BaseMethod.__subclasses__()`` walks EVERY
    subclass currently in the process, including test-only dummies such as
    test_identity.py's ``_SeededDummy`` (collected as a side effect of pytest
    importing that module) — those are deliberately unregistered fixtures, not
    production plugins, and must not trip this gate.
    """
    _import_every_submodule(parametric_package)
    _import_every_submodule(bootstrap_package)
    family_prefixes = (parametric_package.__name__ + ".", bootstrap_package.__name__ + ".")
    return {
        cls
        for cls in _all_base_method_subclasses()
        if not inspect.isabstract(cls) and cls.__module__.startswith(family_prefixes)
    }


def test_every_concrete_method_class_is_registered() -> None:
    """Every concrete BaseMethod subclass must be reachable via the registry.

    A class that fails this either forgot its ``@register`` decorator, forgot
    the import in its family ``__init__.py`` (closed over by the pkgutil walk
    above), or is a quarantined name (which never registers at all — see
    registry.QUARANTINED_METHODS) and should not exist as live code.
    """
    concrete_classes = _concrete_method_classes()
    assert concrete_classes, "sanity: the pkgutil walk found no concrete BaseMethod subclasses"

    registered_names = set(available_methods())
    for method_cls in sorted(concrete_classes, key=lambda cls: cls.name):
        qualified = f"{method_cls.__module__}.{method_cls.__qualname__}"
        assert method_cls.name in registered_names, (
            f"{qualified} defines name={method_cls.name!r} but it is not in "
            f"available_methods() ({sorted(registered_names)}) — did you forget the "
            "@register decorator, or forget to import the module from its family "
            "__init__.py (abkit/stats/parametric or abkit/stats/bootstrap)?"
        )
        assert get_method_class(method_cls.name) is method_cls, (
            f"available_methods() lists {method_cls.name!r} but get_method_class resolves it "
            f"to a different class than {qualified} — a duplicate/aliasing bug in registry.py"
        )


def test_registry_matches_documented_method_set() -> None:
    """Pin the registry's canonical name set to the documented 12 (see module docstring).

    This is a completeness pin, not a freeze: a deliberate new method should
    update DOCUMENTED_METHOD_NAMES here alongside CLAUDE.md / architecture.md
    in the same PR (CLAUDE.md invariant: "Methods are plugins").
    """
    actual = set(available_methods())
    missing = DOCUMENTED_METHOD_NAMES - actual
    extra = actual - DOCUMENTED_METHOD_NAMES
    assert not missing and not extra, (
        "registry method set drifted from the documented 12 "
        "(.claude/rules/architecture.md 'Methods — a plugin registry'): "
        f"missing={sorted(missing)}, undocumented-extra={sorted(extra)}. "
        "If this is a deliberate new/removed method, update DOCUMENTED_METHOD_NAMES "
        "here AND the architecture.md / CLAUDE.md method count in the same PR."
    )
