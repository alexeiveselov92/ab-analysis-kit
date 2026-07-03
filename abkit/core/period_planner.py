"""The expanding cumulative grid — abkit's compute heart.

Pure functions only (no config/DB imports): ONE grid generator consumed by
BOTH the config validator's look-count gates and the pipeline planner, so the
counts can never drift (plan R1; cumulative-intervals.md §6.1/§6.3).

Semantics (all timestamps naive UTC; ``end_ts`` EXCLUSIVE half-open windows):

- ``start_ts``  = experiment-timezone midnight of ``start_date``.
- ``horizon_ts`` = tz midnight AFTER ``end_date`` (the horizon cutoff covers
  ``end_date`` in full). Always emitted, flagged ``is_horizon`` — even when
  the cadence does not divide the duration.
- Sub-day segments anchor at ``start_ts`` (``start_ts + k·every``).
- Day-or-coarser segments snap to experiment-timezone midnights (DST-safe:
  midnights are computed in local time then converted), day-counted from
  ``start_date`` — so a schedule's daily tail is point-for-point comparable
  with a pure-daily series.
- A segment covers offsets ``(prev_until, until]`` from ``start_ts``; the
  last segment runs to the horizon.
- ``cadence: 1d`` with ``data_lag: 0`` + half-open windows reproduces the
  legacy ``*_wo_curr_day`` convention exactly (§6.2).
- No ``look_index`` anywhere — ordinality is ORDER BY end_ts (§6.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DAY_SECONDS = 86400


class GridLimitExceeded(Exception):
    """Raised when a grid would exceed the caller's look limit (``max_looks``)."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(
            f"the cadence grid exceeds {limit} looks (the max_looks gate — "
            "coarsen the cadence or raise limits.max_looks)"
        )


@dataclass(frozen=True, order=True)
class Cutoff:
    """One cumulative look: the window is ``[grid.start_ts, end_ts)``."""

    end_ts: datetime  # naive UTC, EXCLUSIVE
    is_horizon: bool = field(default=False, compare=False)


@dataclass(frozen=True)
class Grid:
    """The full planned look grid for one experiment."""

    start_ts: datetime  # naive UTC — the pinned left edge of every window
    horizon_ts: datetime  # naive UTC — the planned final cutoff
    cutoffs: tuple[Cutoff, ...]  # ascending by end_ts; horizon always last

    def __len__(self) -> int:
        return len(self.cutoffs)


def _tz_midnight_utc(day: date, zone: ZoneInfo) -> datetime:
    """Local midnight of *day* in *zone*, as naive UTC."""
    local = datetime.combine(day, time.min).replace(tzinfo=zone)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def generate_grid(
    start_date: date,
    end_date: date,
    cadence_segments: list[tuple[int, int | None]],
    tz: str = "UTC",
    limit: int | None = None,
) -> Grid:
    """Enumerate the cumulative cutoff grid.

    Args:
        start_date: pinned experiment start (interpreted in ``tz``)
        end_date: planner horizon date (the horizon cutoff covers it in full)
        cadence_segments: normalised ``[(every_seconds, until_seconds|None)]``
            (``ExperimentConfig.cadence_segments()``); segments are validated
            by the config layer (strictly coarsening, increasing until)
        tz: experiment timezone (date interpretation + midnight snapping)
        limit: raise :class:`GridLimitExceeded` when the grid would exceed
            this many looks — the validator's ``max_looks`` gate runs through
            the SAME enumeration the planner uses

    Returns:
        :class:`Grid` with cutoffs ascending; the horizon point is always
        present and flagged, deduplicating an aligned grid point.
    """
    if end_date < start_date:
        raise ValueError(f"end_date ({end_date}) is before start_date ({start_date})")
    zone = ZoneInfo(tz)
    start_ts = _tz_midnight_utc(start_date, zone)
    horizon_ts = _tz_midnight_utc(end_date + timedelta(days=1), zone)

    points: set[datetime] = set()

    def add(point: datetime) -> None:
        points.add(point)
        if limit is not None and len(points) > limit:
            raise GridLimitExceeded(limit)

    prev_until = 0
    for index, (every, until) in enumerate(cadence_segments):
        is_last = index == len(cadence_segments) - 1
        seg_start_ts = start_ts + timedelta(seconds=prev_until)
        if until is None:
            seg_end_ts = horizon_ts
        else:
            seg_end_ts = min(start_ts + timedelta(seconds=until), horizon_ts)

        if every % DAY_SECONDS == 0:
            # Day-or-coarser: snap to tz midnights, day-counted from start_date
            # so schedule tails stay point-for-point comparable with pure-daily.
            every_days = every // DAY_SECONDS
            day_offset = every_days
            while True:
                point = _tz_midnight_utc(start_date + timedelta(days=day_offset), zone)
                if point > seg_end_ts:
                    break
                if point > seg_start_ts:
                    add(point)
                day_offset += every_days
        else:
            # Sub-day: anchor at start_ts in absolute-duration arithmetic.
            step = timedelta(seconds=every)
            k = prev_until // every  # skip straight to the segment
            while True:
                k += 1
                point = start_ts + step * k
                if point > seg_end_ts:
                    break
                if point > seg_start_ts:
                    add(point)

        if until is not None:
            prev_until = until
        if is_last or seg_end_ts >= horizon_ts:
            break

    add(horizon_ts)  # always planned, even when cadence doesn't divide the duration

    cutoffs = tuple(Cutoff(end_ts=ts, is_horizon=(ts == horizon_ts)) for ts in sorted(points))
    return Grid(start_ts=start_ts, horizon_ts=horizon_ts, cutoffs=cutoffs)


def pending_cutoffs(
    grid: Grid,
    computed_end_ts: set[datetime],
    watermark_ts: datetime,
    full_refresh_window: tuple[datetime, datetime] | None = None,
) -> list[Cutoff]:
    """The planner anti-join: which looks to compute this run.

    A cutoff is pending iff it is COMPLETE (``end_ts <= watermark_ts``, where
    ``watermark_ts = now_utc − data_lag`` computed once per run in Python —
    never ``now()`` in SQL, §6.2) and not already computed. The computed set
    comes from ``list_computed_cutoffs`` — a SET, so a late hole in the middle
    of the series is re-planned, not skipped past by a max-cursor.

    ``full_refresh_window=[from_ts, to_ts)`` re-includes already-computed
    cutoffs inside the window (``run --full-refresh --from/--to`` re-opens
    frozen points; the caller deletes the stale rows).
    """
    pending = []
    for cutoff in grid.cutoffs:
        if cutoff.end_ts > watermark_ts:
            continue
        refreshed = full_refresh_window is not None and (
            full_refresh_window[0] <= cutoff.end_ts < full_refresh_window[1]
        )
        if cutoff.end_ts in computed_end_ts and not refreshed:
            continue
        pending.append(cutoff)
    return pending


def backlog_seconds(computed_end_ts: set[datetime], watermark_ts: datetime) -> float | None:
    """How far the computed series trails the watermark (§6.4 backlog warning).

    None when nothing is computed yet (a fresh experiment isn't "backlogged").
    """
    if not computed_end_ts:
        return None
    return (watermark_ts - max(computed_end_ts)).total_seconds()
