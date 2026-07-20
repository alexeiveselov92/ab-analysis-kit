"""Incremental cohort copy engine (m8-implementation-plan.md WP5).

When ``assignment.cohort_copy.enabled``, this module replaces the historical
delete-then-reinsert-everything full reload with the detectkit donor's
watermark / closed-interval / batch discipline
(``detectkit/orchestration/task_manager/_load_step.py``):

1. **Grid-anchored closed intervals** — batch buckets are
   ``[grid.start_ts + k·batch_interval, grid.start_ts + (k+1)·batch_interval)``,
   anchored to the experiment's tz-snapped start exactly like the donor's
   interval grid (never to a floating data maximum). Only buckets that have
   fully CLOSED — their end is at or before ``now - maturity_delay`` — are
   loaded; the open bucket waits (never persisted half-full, then never
   re-read). The covered boundary is therefore a deterministic function of
   ``(grid.start_ts, now, maturity_delay, batch_interval)`` — see
   :func:`closed_interval_bound` — not of what data happened to arrive.
2. **Watermark resume** — with the default ``update_column='exposure_ts'``
   the resume point is ``MAX(exposure_ts)`` over the persisted copy, snapped
   DOWN to its bucket floor (the containing bucket is re-scanned; re-sent
   units are idempotent LWW upserts). A custom ``update_column`` has no
   persisted cursor to resume from — ``exposure_ts``'s maximum says nothing
   about another column's frontier — so it re-scans from the experiment
   start EVERY run (still batched, still closed-intervals-only on the custom
   column; the cost equals the old full reload's read, minus the delete).
3. **Batched round trips** — the window is covered in chunks of
   ``batch_intervals_per_round_trip × batch_interval``; each chunk re-renders
   the assignment SQL with the chunk's bounds appended to
   ``assignment.added_filters`` (the EXISTING ``{{ ab_added_filters }}``
   injection point — no new jinja surface) and appends the deduped result via
   ``insert_exposures_incremental`` (no delete, ever).

**The assignment SQL must render ``{{ ab_added_filters }}`` live** — it is
the one place the batch bounds can land. The engine PROVES the reference by
rendering the template once with a sentinel filter and checking the sentinel
survives into the SQL (a bare substring test would be fooled by the token
sitting in a SQL or jinja comment); without it the whole source would be
silently re-read every batch AND the closed-interval discipline would break,
so the engine refuses loudly instead (and ``abk run``'s config lint runs the
same sentinel check before any DB work).

**KNOWN LIMITATION (donor behavior, settled doc-only — m8 §4 Q3):** a row
whose ``update_column`` value lands in an already-scanned closed bucket but
which only APPEARS in the source later (a backfilled or corrected
assignment) is silently missed by the persisted copy — the opposite
asymmetry from the no-copy default, which re-reads the full live source
every run. A mutating or backfilling source should stay on the no-copy
default, or recover with ``abk run --resync-cohort``: the driver deletes the
persisted cohort and rebuilds it through THIS engine from the experiment
start (one write path — the resync honors the same closed/matured
discipline, never persists unmatured rows, and the from-scratch re-scan is
what picks the late rows up).

**KNOWN LIMITATION (malformed duplicate input only):** ``MIN(exposure_ts)``
is computed per scan window and rows land via a last-write-wins upsert, so a
unit with MULTIPLE source rows (already loudly warned about by the run-level
validation) whose duplicates straddle two scan windows — two round trips of
one run, or a previous run's window vs a later run's watermark-bucket
re-scan — resolves to the LATER window's minimum, not the full reload's
global earliest. On a well-formed one-row-per-unit cohort no divergence is
possible.

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
when a computable cutoff exceeds the deterministic covered boundary).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from abkit.config.experiment_config import CohortCopyConfig, ExperimentConfig
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

__all__ = [
    "CopyOutcome",
    "closed_interval_bound",
    "copy_exposures_incremental",
]

#: SQL-safe timestamp format for the injected bounds (shared by CH/PG/MySQL)
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

#: rendered-through proof that {{ ab_added_filters }} is a LIVE reference —
#: harmless SQL, greppable, never matches real user filters
BOUNDS_PROBE_SENTINEL = "AND 1 = 1 /* abk-bounds-probe */"


@dataclass
class CopyOutcome:
    """What one incremental copy pass did (the driver's warning/log source)."""

    rows_written: int = 0
    round_trips: int = 0
    #: inclusive lower bound of the window this pass scanned (None = no scan ran)
    covered_from: datetime | None = None
    #: EXCLUSIVE upper bound the persisted copy covers after this pass — the
    #: deterministic grid-anchored closed boundary (closed_interval_bound),
    #: reported even when this pass had nothing new to scan. None = no bucket
    #: has closed yet, the copy is necessarily empty.
    covered_through: datetime | None = None
    #: True when the exposure_ts watermark fast-path resumed from a persisted
    #: cohort (always False for a custom update_column — no persisted cursor)
    resumed: bool = False


def closed_interval_bound(
    cfg: CohortCopyConfig, origin: datetime, now: datetime
) -> datetime | None:
    """The EXCLUSIVE grid-anchored boundary of the last closed, matured bucket.

    ``origin + k·batch_interval`` for the largest ``k`` with the bucket end at
    or before ``now - maturity_delay``; ``None`` when no bucket has closed
    yet. Deterministic — never a function of the data — so the driver's
    coverage warning and the resync gate can share it with the engine.
    """
    matured = now - timedelta(seconds=cfg.maturity_delay_seconds())
    step = cfg.batch_interval_seconds()
    if matured <= origin:
        return None
    periods = int((matured - origin).total_seconds() // step)
    if periods < 1:
        return None
    return origin + timedelta(seconds=periods * step)


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
    template = template or QueryTemplate()

    def _log(line: str) -> None:
        if log is not None:
            log(line)

    # Prove {{ ab_added_filters }} is a LIVE render reference: a sentinel
    # filter must survive into the rendered SQL. A substring test on the
    # template text would be fooled by the token inside a SQL/jinja comment
    # (review-confirmed) — then every batch would silently re-read the whole
    # source and the closed-interval discipline would be fiction.
    probe = render_assignment_sql(
        manager,
        experiment,
        project_root,
        grid,
        template,
        added_filters_override=BOUNDS_PROBE_SENTINEL,
    )
    if BOUNDS_PROBE_SENTINEL not in probe:
        raise ExposureLoadError(
            f"assignment SQL for experiment '{name}' must render "
            "{{ ab_added_filters }} when cohort_copy.enabled — the incremental "
            "copy injects its watermark batch bounds there (add e.g. "
            "'WHERE 1 = 1 {{ ab_added_filters }}' to the assignment query, or "
            "disable cohort_copy)"
        )

    origin = grid.start_ts
    bound = closed_interval_bound(cfg, origin, now)
    if bound is None:
        _log(f"LOAD  {name}: cohort copy — no batch interval has closed yet, waiting")
        return CopyOutcome()

    # The exposure_ts watermark fast-path only applies to the column it
    # actually measures; a custom update_column re-scans from the experiment
    # start every run (module docstring point 2 — review-confirmed: bounding
    # updated_at by MAX(exposure_ts) silently drops legitimate rows).
    fast_path = cfg.update_column == "exposure_ts"
    watermark = tables.get_last_exposure_timestamp(name) if fast_path else None
    resumed = watermark is not None
    if watermark is not None:
        step = cfg.batch_interval_seconds()
        offset = int((watermark - origin).total_seconds() // step)
        # snap DOWN to the containing bucket's floor: the partially-persisted
        # bucket is re-scanned, re-sent units are idempotent LWW upserts
        scan_from = origin + timedelta(seconds=max(0, offset) * step)
    else:
        scan_from = origin
    if scan_from >= bound:
        # everything closed is already covered (only reachable when persisted
        # data sits at/past the boundary — e.g. an ungated legacy write)
        _log(f"LOAD  {name}: cohort copy — nothing matured yet, waiting")
        return CopyOutcome(covered_through=bound, resumed=resumed)

    step_seconds = cfg.batch_interval_seconds()
    chunk_seconds = step_seconds * cfg.batch_intervals_per_round_trip
    num_round_trips = -(-int((bound - scan_from).total_seconds()) // chunk_seconds)
    origin_label = "watermark bucket" if resumed else "experiment start"
    _log(
        f"LOAD  {name}: cohort copy from {scan_from:{_TS_FORMAT}} ({origin_label}) "
        f"to {bound:{_TS_FORMAT}} in {num_round_trips} round trip(s)"
    )

    unit_key = experiment.unit_key
    outcome = CopyOutcome(covered_from=scan_from, covered_through=bound, resumed=resumed)
    current = scan_from
    while current < bound:
        batch_to = min(current + timedelta(seconds=chunk_seconds), bound)
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
