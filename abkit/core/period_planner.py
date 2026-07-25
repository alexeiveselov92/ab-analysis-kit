"""The expanding cumulative grid — abkit's compute heart.

Pure functions only (no config/DB imports): ONE grid generator consumed by
BOTH the config validator's look-count gates and the pipeline planner, so the
counts can never drift (plan R1; cumulative-intervals.md §6.1/§6.3).

Inputs are LOCAL wall-clock values interpreted in the experiment timezone; the
``Grid`` this module returns is naive UTC throughout (``end_ts`` EXCLUSIVE,
half-open windows). A bare ``date`` is shorthand for local midnight of that
day, so a whole-day window reads the same as it always did.

Semantics:

- ``start_ts``  = the pinned left edge of every window.
- ``horizon_ts`` = the EXCLUSIVE right edge; the horizon cutoff lands exactly
  on it. Always emitted, flagged ``is_horizon`` — even when the cadence does
  not divide the duration.
- Cutoffs are ``anchor + k·every``, kept STRICTLY after the segment's left
  edge (m10 D2). ``interval_anchor`` decides where that lattice sits:
  ``midnight`` (the default — local midnight of the opening day, i.e. whole
  calendar days, which is the pre-m10 rule), ``start`` (count from the start
  instant), or an explicit local instant that may precede the start — in
  which case the first window is legitimately partial.
- Day-or-coarser segments step in CALENDAR days at the anchor's local
  wall-clock time (DST-safe: local time first, then converted), so a
  schedule's daily tail is point-for-point comparable with a pure-daily
  series under the same anchor. Sub-day segments step in absolute duration.
- A segment covers offsets ``(prev_until, until]`` from ``start_ts`` — from
  the START, never from the anchor; the last segment runs to the horizon.
- ``cadence: 1d`` with ``data_lag: 0`` + half-open windows reproduces the
  legacy ``*_wo_curr_day`` convention exactly (§6.2).
- No ``look_index`` anywhere — ordinality is ORDER BY end_ts (§6.3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

DAY_SECONDS = 86400

#: ``interval_anchor``: two symbolic forms plus an explicit local instant.
AnchorSpec = Literal["midnight", "start"] | date | datetime


def _ceil_div(numerator: int, denominator: int) -> int:
    """Integer ceiling division (``denominator > 0``), negatives included."""
    return -(-numerator // denominator)


#: Both branches seed ``k`` in closed form and are provably within one step;
#: the cap turns "measured" into "structural".
_MAX_SNAP_STEPS = 4


class GridInvariantError(Exception):
    """Raised when the lattice snap fails to converge — a tzdata pathology.

    Loud is the right failure direction: a spin here would emit nothing, and
    the ``limit`` gate only counts points actually ADDED, so an unbounded
    correction loop would hang rather than raise.
    """


def _snap_forward(
    point_at: Callable[[int], datetime], k: int, bound: datetime, what: str
) -> int:
    """Smallest ``k`` with ``point_at(k) > bound``, from a closed-form estimate."""
    for _ in range(_MAX_SNAP_STEPS):
        if point_at(k) > bound:
            k -= 1
        else:
            break
    else:
        raise GridInvariantError(f"{what}: snap-down exceeded {_MAX_SNAP_STEPS} steps")
    for _ in range(_MAX_SNAP_STEPS + 1):
        if point_at(k) <= bound:
            k += 1
        else:
            return k
    raise GridInvariantError(f"{what}: snap-up exceeded {_MAX_SNAP_STEPS} steps")


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
    # naive UTC — the resolved lattice anchor the cutoffs hang off. Kept so
    # consumers assert/display it instead of re-deriving and drifting.
    anchor_ts: datetime | None = None

    def __len__(self) -> int:
        return len(self.cutoffs)


def tz_localize_utc(local: datetime, zone: ZoneInfo) -> datetime:
    """A naive LOCAL wall-clock instant in *zone*, as naive UTC.

    The generalized primitive behind every anchor in this module: local time
    in, UTC out, so DST is absorbed by ``zoneinfo`` rather than by second
    arithmetic. An ambiguous (fall-back) or non-existent (spring-forward)
    wall-clock time resolves through ``fold=0`` — the earlier offset, and for
    a skipped time the instant one offset-jump later; documented rather than
    guessed at, so a grid anchored at ``02:30`` stays enumerable.
    """
    return local.replace(tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)


def tz_midnight_utc(day: date, zone: ZoneInfo) -> datetime:
    """Local midnight of *day* in *zone*, as naive UTC.

    Takes a CALENDAR DATE. A ``datetime`` is rejected loudly: ``date`` is a
    base class of ``datetime``, so the pre-m10 body silently discarded the
    time component of an instant — exactly the truncation the sub-day window
    work exists to remove.
    """
    if isinstance(day, datetime):
        raise TypeError(
            "tz_midnight_utc takes a calendar date, not a datetime "
            "(its time-of-day would be silently dropped) — use tz_localize_utc "
            "for an instant, or pass `value.date()` if a whole-day snap is meant"
        )
    return tz_localize_utc(datetime.combine(day, time.min), zone)


def as_local_datetime(value: date | datetime) -> datetime:
    """A window edge as a naive LOCAL wall-clock ``datetime``.

    A bare ``date`` is shorthand for local midnight of that day; a
    ``datetime`` is already the exact wall-clock instant. Type-preserving by
    construction — never use ``isinstance(x, date)`` to tell them apart
    (``datetime`` passes it too).
    """
    return value if isinstance(value, datetime) else datetime.combine(value, time.min)


def resolve_instant(value: date | datetime, zone: ZoneInfo) -> datetime:
    """A config window edge (local ``date``/``datetime``) as naive UTC."""
    return tz_localize_utc(as_local_datetime(value), zone)


def local_date(ts: datetime, zone: ZoneInfo) -> date:
    """The experiment-timezone calendar date of a naive-UTC instant.

    The inverse of :func:`tz_midnight_utc`, and the one way to ask "which
    local day does this instant fall on" — the question every day-keyed
    surface (state days, the CUPED pre-period) must ask of the GRID rather
    than of the raw config field, which may now carry a time-of-day.
    """
    return ts.replace(tzinfo=timezone.utc).astimezone(zone).date()


def resolve_anchor_local(interval_anchor: AnchorSpec, start_local: datetime) -> datetime:
    """The lattice anchor as a naive LOCAL wall-clock instant.

    ``midnight`` — local midnight of the day the window opens (the absent-key
    behavior, and the pre-m10 rule); ``start`` — the start instant itself; an
    explicit ``date``/``datetime`` — that local instant, which MAY precede or
    follow the start (the forward snap is what makes either well-defined).
    """
    if isinstance(interval_anchor, str):
        if interval_anchor == "midnight":
            return datetime.combine(start_local.date(), time.min)
        if interval_anchor == "start":
            return start_local
        raise ValueError(
            f"interval_anchor: {interval_anchor!r} is not 'midnight', 'start', "
            "or an ISO timestamp"
        )
    return as_local_datetime(interval_anchor)


def generate_grid(
    start_ts: date | datetime,
    horizon_ts: date | datetime,
    cadence_segments: list[tuple[int, int | None]],
    tz: str = "UTC",
    limit: int | None = None,
    interval_anchor: AnchorSpec = "midnight",
) -> Grid:
    """Enumerate the cumulative cutoff grid.

    Args:
        start_ts: pinned experiment start — a LOCAL ``date`` (midnight of that
            day) or ``datetime``, interpreted in ``tz``
        horizon_ts: the EXCLUSIVE right edge, same local interpretation; the
            horizon cutoff lands exactly on it
        cadence_segments: normalised ``[(every_seconds, until_seconds|None)]``
            (``ExperimentConfig.cadence_segments()``); segments are validated
            by the config layer (strictly coarsening, increasing until)
        tz: experiment timezone (interprets the local edges, the anchor, and
            the DST-safe day lattice)
        limit: raise :class:`GridLimitExceeded` when the grid would exceed
            this many looks — the validator's ``max_looks`` gate runs through
            the SAME enumeration the planner uses
        interval_anchor: where the lattice sits — ``"midnight"`` (default),
            ``"start"``, or an explicit local instant

    Returns:
        :class:`Grid` with cutoffs ascending, all edges naive UTC; the horizon
        point is always present and flagged, deduplicating an aligned point.
    """
    zone = ZoneInfo(tz)
    start_local = as_local_datetime(start_ts)
    start_utc = tz_localize_utc(start_local, zone)
    horizon_utc = resolve_instant(horizon_ts, zone)
    if horizon_utc <= start_utc:
        raise ValueError(
            f"horizon_ts ({horizon_ts}) is not after start_ts ({start_ts}) — "
            "the horizon is the EXCLUSIVE right edge of the window"
        )
    anchor_local = resolve_anchor_local(interval_anchor, start_local)
    anchor_utc = tz_localize_utc(anchor_local, zone)
    # Segment bounds are elapsed offsets from the START, but the lattice hangs
    # off the ANCHOR. The two only share a phase when the anchor keeps the
    # start's wall-clock time — which is the case for every `start` anchor and
    # for `midnight` on a midnight start (i.e. every pre-m10 config). Only
    # then may a whole-day bound be compared in DAY space; see below for why
    # day space exists at all.
    same_phase = anchor_local.time() == start_local.time()

    points: set[datetime] = set()

    def add(point: datetime) -> None:
        points.add(point)
        if limit is not None and len(points) > limit:
            raise GridLimitExceeded(limit)

    prev_until = 0
    for index, (every, until) in enumerate(cadence_segments):
        is_last = index == len(cadence_segments) - 1
        seg_start_ts = start_utc + timedelta(seconds=prev_until)
        if until is None:
            seg_end_ts = horizon_utc
        else:
            seg_end_ts = min(start_utc + timedelta(seconds=until), horizon_utc)

        if every % DAY_SECONDS == 0:
            # Day-or-coarser: the lattice is CALENDAR days at the anchor's
            # local wall-clock time (DST-safe — a local day is 23h or 25h
            # across a transition, never a fixed 86400s), so a schedule's
            # daily tail stays point-for-point comparable with a pure-daily
            # grid under the same anchor.
            #
            # Whole-day segment bounds then compare in DAY SPACE, not absolute
            # seconds: across a DST fall-back a lattice point lands more than
            # `until` seconds after start_ts and the boundary look would be
            # silently dropped by a timestamp comparison.
            every_days = every // DAY_SECONDS
            in_day_space = same_phase and prev_until % DAY_SECONDS == 0
            start_days = prev_until // DAY_SECONDS if in_day_space else None
            until_days = (
                until // DAY_SECONDS
                if until is not None and same_phase and until % DAY_SECONDS == 0
                else None
            )
            anchor_day = anchor_local.date()
            anchor_time = anchor_local.time()

            def day_point(
                k: int,
                _day: date = anchor_day,
                _time: time = anchor_time,
                _step: int = every_days,
            ) -> datetime:
                return tz_localize_utc(
                    datetime.combine(_day + timedelta(days=k * _step), _time), zone
                )

            # First lattice step strictly after the segment's left edge, in
            # closed form: an anchor years before the start must not be walked
            # to one step at a time (it would emit nothing and spin past the
            # `limit` gate, which only counts ADDED points).
            k = _snap_forward(
                day_point,
                _ceil_div((local_date(seg_start_ts, zone) - anchor_day).days, every_days),
                seg_start_ts,
                f"day lattice (every={every_days}d)",
            )
            while True:
                point = day_point(k)
                day_offset = (anchor_day + timedelta(days=k * every_days) - start_local.date()).days
                if point > horizon_utc:
                    break
                if until_days is not None:
                    if day_offset > until_days:
                        break
                elif point > seg_end_ts:
                    break
                past_start = (
                    day_offset > start_days if start_days is not None else point > seg_start_ts
                )
                if past_start:
                    add(point)
                k += 1
        else:
            # Sub-day: absolute-duration arithmetic off the anchor (no local
            # snapping — dense points march straight through a DST jump).
            step = timedelta(seconds=every)

            def sub_day_point(
                k: int, _step: timedelta = step, _anchor: datetime = anchor_utc
            ) -> datetime:
                return _anchor + _step * k

            k = _snap_forward(
                sub_day_point,
                (seg_start_ts - anchor_utc) // step,
                seg_start_ts,
                f"sub-day lattice (every={every}s)",
            )
            while True:
                point = sub_day_point(k)
                if point > seg_end_ts:
                    break
                add(point)  # strictly past seg_start_ts by construction
                k += 1

        if until is not None:
            prev_until = until
        if is_last or seg_end_ts >= horizon_utc:
            break

    add(horizon_utc)  # always planned, even when cadence doesn't divide the duration

    cutoffs = tuple(Cutoff(end_ts=ts, is_horizon=(ts == horizon_utc)) for ts in sorted(points))
    return Grid(start_ts=start_utc, horizon_ts=horizon_utc, cutoffs=cutoffs, anchor_ts=anchor_utc)


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
