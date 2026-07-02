"""Method registry: name → class, plus the quarantine list for broken legacy methods.

The pipeline/DB/CLI never special-case a method name — everything routes through
:func:`get_method_class`. Quarantined names (docs/specs/statistics-changes.md §3)
raise a hard, explanatory error and are never silently substituted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from abkit.stats.base import BaseMethod
from abkit.stats.exceptions import QuarantinedMethodError, UnknownMethodError

_REGISTRY: dict[str, type[BaseMethod]] = {}
_ALIASES: dict[str, str] = {}

#: Legacy methods verified broken — hard error, never a silent substitution
#: (quorum must-fix "quarantine broken ratio methods").
QUARANTINED_METHODS: dict[str, str] = {
    "poisson-post-normed-bootstrap": (
        "the legacy PoissonPostNormedBootstrapTest performs NO post-normalisation — it is a "
        "verbatim copy of PoissonBootstrapTest. Use 'poisson-bootstrap' (identical behaviour) "
        "or the principled 'ratio-delta' for ratio metrics. See statistics-changes.md §3."
    ),
}


def normalize_method_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def register(
    cls: type[BaseMethod] | None = None, *, aliases: Iterable[str] = ()
) -> type[BaseMethod] | Callable[[type[BaseMethod]], type[BaseMethod]]:
    """Class decorator: ``@register`` or ``@register(aliases=("ttest",))``."""

    def _register(method_cls: type[BaseMethod]) -> type[BaseMethod]:
        name = getattr(method_cls, "name", None)
        if not name or not isinstance(name, str):
            raise ValueError(f"{method_cls.__name__} must define a class-level registry `name`")
        canonical = normalize_method_name(name)
        if canonical != name:
            raise ValueError(f"registry name must be canonical kebab-case, got {name!r}")
        existing = _REGISTRY.get(canonical)
        if existing is not None and existing is not method_cls:
            raise ValueError(f"method name {canonical!r} already registered by {existing.__name__}")
        if canonical in QUARANTINED_METHODS:
            raise ValueError(f"method name {canonical!r} is quarantined and cannot be registered")
        _REGISTRY[canonical] = method_cls
        for alias in aliases:
            _ALIASES[normalize_method_name(alias)] = canonical
        return method_cls

    if cls is not None:
        return _register(cls)
    return _register


def get_method_class(name: str) -> type[BaseMethod]:
    canonical = normalize_method_name(name)
    canonical = _ALIASES.get(canonical, canonical)
    if canonical in QUARANTINED_METHODS:
        raise QuarantinedMethodError(
            f"method {name!r} is quarantined: {QUARANTINED_METHODS[canonical]}"
        )
    try:
        return _REGISTRY[canonical]
    except KeyError:
        raise UnknownMethodError(
            f"unknown method {name!r}; available: {', '.join(available_methods())}"
        ) from None


def available_methods() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
