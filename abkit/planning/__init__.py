"""Pre-launch sizing for ``abk plan`` (m5-implementation-plan.md WP6).

A pure sizing engine (``sizing.py``) over :mod:`abkit.stats.power`, plus the CLI
command (``abkit.cli.commands.plan``) that reads baseline moments from the persisted
``_ab_results`` and translates config → primitives. Strictly read-only (D11): no lock,
no ``_ab_*`` writes. runtime / ASN are deferred to M6 (D10; cli-and-dx.md §1).
"""

from __future__ import annotations

from abkit.planning.sizing import (
    BaselineMoments,
    ComparisonPlan,
    SizingResult,
    parse_baseline_overrides,
    size_comparison,
)

__all__ = [
    "BaselineMoments",
    "ComparisonPlan",
    "SizingResult",
    "parse_baseline_overrides",
    "size_comparison",
]
