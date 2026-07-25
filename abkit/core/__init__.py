"""Core functionality for abkit (stdlib-only: intervals, table models, the grid)."""

from abkit.core.interval import Interval
from abkit.core.models import ColumnDefinition, TableModel
from abkit.core.period_planner import (
    Cutoff,
    Grid,
    GridLimitExceeded,
    as_local_datetime,
    backlog_seconds,
    generate_grid,
    local_date,
    pending_cutoffs,
    resolve_instant,
    tz_localize_utc,
    tz_midnight_utc,
)

__all__ = [
    "ColumnDefinition",
    "Cutoff",
    "Grid",
    "GridLimitExceeded",
    "Interval",
    "TableModel",
    "as_local_datetime",
    "backlog_seconds",
    "generate_grid",
    "local_date",
    "pending_cutoffs",
    "resolve_instant",
    "tz_localize_utc",
    "tz_midnight_utc",
]
