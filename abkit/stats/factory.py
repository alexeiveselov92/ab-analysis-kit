"""Factory: build a configured method instance from (name, params).

This is the single construction path used by the pipeline, the explore cockpit
and the A/A harness — parameter validation and identity hashing happen here (in
the method constructor), never downstream.
"""

from __future__ import annotations

from typing import Any

from abkit.stats.base import BaseMethod
from abkit.stats.registry import get_method_class


def create_method(
    name: str, alpha: float = 0.05, params: dict[str, Any] | None = None
) -> BaseMethod:
    """Instantiate a registered method with validated params.

    ``alpha`` is the effective (post-correction) per-comparison alpha — it is an
    experiment-level setting, not a method param, and never enters
    ``method_config_id`` (docs/specs/declarative-config.md §6–§7).
    """
    method_cls = get_method_class(name)
    return method_cls(alpha=alpha, **(params or {}))
