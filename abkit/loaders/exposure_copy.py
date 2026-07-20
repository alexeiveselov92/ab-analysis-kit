"""Incremental cohort copy engine (m8-implementation-plan.md WP5).

When ``assignment.cohort_copy.enabled``, this module replaces the historical
delete-then-reinsert-everything full reload with the detectkit donor's
watermark / closed-interval / batch discipline
(``detectkit/orchestration/task_manager/_load_step.py``):

1. **Watermark resume** — ``MAX(exposure_ts)`` over the persisted copy; a
   first run backfills from the experiment's tz-snapped start
   (``grid.start_ts``, the driver-identical window origin).
2. **Closed intervals only** — the copy window ``[actual_from, actual_to)``
   is snapped back to the last whole ``batch_interval`` boundary after
   subtracting ``maturity_delay``; the still-open interval is withheld until
   it closes (never persisted half-full, then never re-read).
3. **Batched round trips** — the window is covered in chunks of
   ``batch_intervals_per_round_trip × batch_interval``; each chunk re-renders
   the assignment SQL with the chunk's bounds appended to
   ``assignment.added_filters`` (the EXISTING ``{{ ab_added_filters }}``
   injection point — no new jinja surface) and appends the deduped result via
   ``insert_exposures_incremental`` (no delete, ever).

**The assignment SQL must reference ``{{ ab_added_filters }}``** — it is the
one place the batch bounds can land. A template without it would silently
re-read the whole source every batch AND break the closed-interval discipline,
so the engine refuses loudly instead (and ``abk run``'s config lint catches it
before any DB work).

**KNOWN LIMITATION (donor behavior, settled doc-only — m8 §4 Q3):** a row
whose ``update_column`` value is EARLIER than the current watermark but which
only APPEARS in the source later (a backfilled or corrected assignment) is
silently and permanently missed by the persisted copy — the opposite asymmetry
from the no-copy default, which re-reads the full live source every run.
A mutating or backfilling source should stay on the no-copy default, or
recover with ``abk run --resync-cohort`` (the full delete + reinsert).

**Ordering dependency (NOT safe to call standalone):** the run-level
whole-cohort validation (``validate_and_snapshot``) must have passed moments
before — it is what hard-fails a cross-variant conflict. This engine only
dedupes WITHIN each batch (earliest ``exposure_ts`` wins, mirroring the
first-exposure semantics) and never re-detects conflicts: a conflicting unit
reaching it would be silently resolved by the ``(experiment, unit_id)``
last-write-wins upsert instead of raising.

Freshness note: the copy trails the live source by ``maturity_delay`` plus
the open interval (< one ``batch_interval``). Cutoffs are computed through
``now - data_lag``, so keep ``data_lag >= maturity_delay + batch_interval``
or the newest cutoffs read a partial cohort (the driver surfaces a warning
when the coverage falls short).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from abkit.config.experiment_config import ExperimentConfig
from abkit.core.period_planner import Grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.exposure_source import (
    ExposureLoadError,
    _pushdown_sql,
    render_assignment_sql,
)
from abkit.loaders.query_template import QueryTemplate
from abkit.utils.datetime_utils import to_naive_utc

__all__ = ["CopyOutcome", "copy_exposures_incremental"]

#: SQL-safe timestamp format for the injected bounds (shared by CH/PG/MySQL)
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class CopyOutcome:
    """What one incremental copy pass did (the driver's warning/log source)."""

    rows_written: int = 0
    round_trips: int = 0
    #: inclusive lower bound of the window this pass scanned (None = no pass ran)
    covered_from: datetime | None = None
    #: EXCLUSIVE upper bound the persisted copy is known to cover after this
    #: pass — the snapped closed-interval edge (or the resumed watermark when
    #: nothing new had matured). None = the copy is empty and nothing matured.
    covered_through: datetime | None = None
    #: True when a watermark existed (resume), False on the first-run backfill
    resumed: bool = False


def _batch_added_filters(base: str, update_column: str, lo: datetime, hi: datetime) -> str:
    """The chunk's filter fragment: the experiment's own filters + the bounds.

    ``added_filters`` is documented as "must start with AND", so the composed
    fragment stays a valid tail for the template's ``WHERE … {{ ab_added_filters }}``.
    """
    bounds = (
        f"AND {update_column} >= '{lo.strftime(_TS_FORMAT)}' "
        f"AND {update_column} < '{hi.strftime(_TS_FORMAT)}'"
    )
    return f"{base} {bounds}" if base else bounds


def copy_exposures_incremental(
    manager: BaseDatabaseManager,
    tables: InternalTablesManager,
    experiment: ExperimentConfig,
    project_root: Path | None,
    grid: Grid,
    *,
    now: datetime,
    has_stratum: bool,
    template: QueryTemplate | None = None,
    log: Callable[[str], None] | None = None,
) -> CopyOutcome:
    """Incrementally append the matured assignment rows to ``_ab_exposures``.

    ``has_stratum`` comes from the caller's probe of the SAME source (the
    driver's ``validate_and_snapshot``) — never guessed here, so the batch
    pushdown can only reference a ``stratum`` column that actually exists
    (the WP3 review contract). See the module docstring for the discipline,
    the known limitation and the ordering dependency.
    """
    cfg = experiment.assignment.cohort_copy
    name = experiment.name

    def _log(line: str) -> None:
        if log is not None:
            log(line)

    query_text = experiment.assignment.get_query_text(project_root)
    if "ab_added_filters" not in query_text:
        raise ExposureLoadError(
            f"assignment SQL for experiment '{name}' must reference "
            "{{ ab_added_filters }} when cohort_copy.enabled — the incremental "
            "copy injects its watermark batch bounds there (add e.g. "
            "'WHERE 1 = 1 {{ ab_added_filters }}' to the assignment query, or "
            "disable cohort_copy)"
        )

    watermark = tables.get_last_exposure_timestamp(name)
    resumed = watermark is not None
    actual_from = watermark if watermark is not None else grid.start_ts
    actual_to = now - timedelta(seconds=cfg.maturity_delay_seconds())

    step_seconds = cfg.batch_interval_seconds()
    if actual_to <= actual_from:
        _log(f"LOAD  {name}: cohort copy — nothing matured yet, waiting")
        return CopyOutcome(covered_through=watermark, resumed=resumed)
    total_points = int((actual_to - actual_from).total_seconds() // step_seconds)
    if total_points < 1:
        _log(f"LOAD  {name}: cohort copy — nothing matured yet, waiting")
        return CopyOutcome(covered_through=watermark, resumed=resumed)
    # Snap back to the last CLOSED interval boundary (donor _load_step arithmetic).
    actual_to = actual_from + timedelta(seconds=total_points * step_seconds)

    chunk_seconds = step_seconds * cfg.batch_intervals_per_round_trip
    num_round_trips = -(-int((actual_to - actual_from).total_seconds()) // chunk_seconds)
    origin = "watermark" if resumed else "experiment start"
    _log(
        f"LOAD  {name}: cohort copy from {actual_from:{_TS_FORMAT}} ({origin}) "
        f"to {actual_to:{_TS_FORMAT}} in {num_round_trips} round trip(s)"
    )

    template = template or QueryTemplate()
    unit_key = experiment.unit_key
    outcome = CopyOutcome(covered_from=actual_from, covered_through=actual_to, resumed=resumed)
    current = actual_from
    while current < actual_to:
        batch_to = min(current + timedelta(seconds=chunk_seconds), actual_to)
        rendered = render_assignment_sql(
            manager,
            experiment,
            project_root,
            grid,
            template,
            added_filters_override=_batch_added_filters(
                experiment.assignment.added_filters, cfg.update_column, current, batch_to
            ),
        )
        rows = manager.execute_query(_pushdown_sql(unit_key, rendered, has_stratum))
        outcome.round_trips += 1
        current = batch_to
        if not rows:
            continue

        # In-batch dedup only: earliest exposure_ts wins (first-exposure
        # semantics). Cross-variant conflicts are NOT re-detected here — the
        # run-level validation already hard-failed them (module docstring).
        earliest: dict[Any, tuple[str, datetime | None, Any]] = {}
        for row in rows:
            unit = row[unit_key]
            exposure_ts = to_naive_utc(row["exposure_ts"])
            prev = earliest.get(unit)
            if prev is not None and prev[1] is not None:
                if exposure_ts is None or exposure_ts >= prev[1]:
                    continue
            stratum = row.get("stratum") if has_stratum else None
            earliest[unit] = (row["variant"], exposure_ts, stratum)

        units = list(earliest)
        written = tables.insert_exposures_incremental(
            name,
            {
                "unit_id": np.array([str(u) for u in units], dtype=object),
                "variant": np.array([earliest[u][0] for u in units], dtype=object),
                "exposure_ts": np.array([earliest[u][1] for u in units], dtype=object),
                "stratum": np.array([earliest[u][2] for u in units], dtype=object),
            },
        )
        outcome.rows_written += written
        _log(
            f"LOAD  {name}: cohort copy round trip {outcome.round_trips}/"
            f"{num_round_trips}: +{written} units (total {outcome.rows_written})"
        )
    return outcome
