"""Pre-launch sizing for ``abk plan`` (m5-implementation-plan.md WP6).

A pure sizing engine (``sizing.py``) over :mod:`abkit.stats.power`, plus the CLI
command (``abkit.cli.commands.plan``) that reads baseline moments from the persisted
``_ab_results`` and translates config → primitives. Strictly read-only (D11): no lock,
no ``_ab_*`` writes. Runtime (days-to-N from a ``_ab_exposures`` arrival rate) and ASN
(the always-valid sequential design's average sample number) ship in M6 WP-A.
"""

from __future__ import annotations

from abkit.planning.sizing import (
    AsnResult,
    BaselineMoments,
    ComparisonPlan,
    RuntimePlan,
    SizingResult,
    asn_for,
    parse_baseline_overrides,
    runtime_for,
    size_comparison,
)

__all__ = [
    "AsnResult",
    "BaselineMoments",
    "ComparisonPlan",
    "RuntimePlan",
    "SizingResult",
    "asn_for",
    "parse_baseline_overrides",
    "runtime_for",
    "size_comparison",
]
