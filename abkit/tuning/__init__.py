"""The explore cockpit's server-side engine (m3-implementation-plan.md WP4+).

``session`` holds the one-load-pass state (persisted series + the bounded
Tier-S per-unit cache, D2); ``recompute`` answers knob changes from it
(Tiers E/α/S/R, D1) and carries the calibration lookup (D3). The localhost
server (WP6) and ``abk explore`` (WP8) bind these; nothing here touches the
DB after session load.
"""

from abkit.tuning.recompute import (
    CalibrationStatus,
    ExplorePoint,
    KnobState,
    PairRecompute,
    RecomputeEngine,
    RecomputeResult,
    find_calibration,
    resolve_fpr_budget,
)
from abkit.tuning.session import (
    EXPLORE_CACHE_BUDGET,
    ComparisonSeries,
    ExploreSession,
    backend_cutoff_loader,
    load_session,
)

__all__ = [
    "EXPLORE_CACHE_BUDGET",
    "CalibrationStatus",
    "ComparisonSeries",
    "ExplorePoint",
    "ExploreSession",
    "KnobState",
    "PairRecompute",
    "RecomputeEngine",
    "RecomputeResult",
    "backend_cutoff_loader",
    "find_calibration",
    "load_session",
    "resolve_fpr_budget",
]
