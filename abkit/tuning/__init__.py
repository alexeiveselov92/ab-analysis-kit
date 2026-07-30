"""The explore cockpit's server-side engine (m3-implementation-plan.md WP4+).

``session`` holds the one-load-pass state (persisted series + the bounded
Tier-S per-unit cache, D2); ``recompute`` answers knob changes from it
(Tiers E/α/S/R, D1) and carries the calibration lookup (D3). The localhost
server (WP6) and ``abk explore`` (WP8) bind these; nothing here touches the
DB after session load.

``jobs`` joins them for the ``abk dashboard`` cockpit
(m11-implementation-plan.md DASH-1): a subprocess registry, sharing only this
package — it has no session, no DB and no statistics of its own.
``overview`` (DASH-2) is the same cockpit's read side: one row per experiment
off the persisted ``_ab_results``, with every verdict sourced from
``pipeline.readout.evaluate`` — it computes no statistic and holds no session.
``dashboard_server`` (DASH-3) serves those two: the metadata-only boot page
plus one lazily-fetched row per experiment, token-gated on EVERY request and
never shutting itself down — a launcher that takes no pipeline lock. Its job
routes (DASH-4) spawn the real ``abk`` CLI through ``jobs`` — run / unlock /
clean one at a time, explore deduped per experiment — and read an experiment's
YAML back out for the read-only "open in your editor" affordance; none of them
writes a config or computes anything.
"""

from abkit.tuning.config_writer import (
    AppliedConfig,
    OrphanedSeries,
    TunedComparison,
    apply_tuned_config,
)
from abkit.tuning.dashboard_server import (
    DEFAULT_WINDOW_PRESET,
    build_dashboard_server,
    serve_dashboard,
)
from abkit.tuning.html import render_dashboard_html, render_explore_html
from abkit.tuning.jobs import Job, JobManager, JobManagerClosed
from abkit.tuning.overview import (
    ALL_WINDOW_PRESETS,
    MAX_STAT_POINTS,
    WINDOW_PRESETS,
    UnknownWindowPreset,
    build_experiment_row,
    build_experiment_row_safe,
    build_overview_boot_entries,
    validate_window_preset,
)
from abkit.tuning.payload import build_explore_payload
from abkit.tuning.recompute import (
    CalibrationStatus,
    ExplorePoint,
    KnobState,
    PairRecompute,
    RecomputeEngine,
    RecomputeResult,
    RecomputeSuperseded,
    find_calibration,
    resolve_fpr_budget,
)
from abkit.tuning.server import build_explore_server, serve_explore
from abkit.tuning.session import (
    EXPLORE_CACHE_BUDGET,
    ComparisonSeries,
    ExploreSession,
    backend_cutoff_loader,
    load_session,
)

__all__ = [
    "ALL_WINDOW_PRESETS",
    "DEFAULT_WINDOW_PRESET",
    "EXPLORE_CACHE_BUDGET",
    "MAX_STAT_POINTS",
    "WINDOW_PRESETS",
    "AppliedConfig",
    "CalibrationStatus",
    "ComparisonSeries",
    "ExplorePoint",
    "ExploreSession",
    "Job",
    "JobManager",
    "JobManagerClosed",
    "KnobState",
    "OrphanedSeries",
    "PairRecompute",
    "RecomputeEngine",
    "RecomputeResult",
    "RecomputeSuperseded",
    "TunedComparison",
    "UnknownWindowPreset",
    "apply_tuned_config",
    "backend_cutoff_loader",
    "build_dashboard_server",
    "build_experiment_row",
    "build_experiment_row_safe",
    "build_explore_payload",
    "build_explore_server",
    "build_overview_boot_entries",
    "find_calibration",
    "load_session",
    "render_dashboard_html",
    "render_explore_html",
    "resolve_fpr_budget",
    "serve_dashboard",
    "serve_explore",
    "validate_window_preset",
]
