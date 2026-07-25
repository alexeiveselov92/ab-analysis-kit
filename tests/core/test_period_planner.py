"""Grid tests — the highest-value test surface in M2 (plan WP7).

Pins: the legacy daily-grid enumeration incl. *_wo_curr_day parity at
data_lag=0, scalar ≡ single-segment identity (plan R1), the dense-early
schedule point set, midnight snapping in non-UTC timezones + DST, horizon
append/dedupe, the anti-join (holes re-planned), full-refresh re-inclusion,
watermark determinism, and the max_looks limit running through the SAME
enumeration the planner uses.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from abkit.core.period_planner import (
    GridLimitExceeded,
    backlog_seconds,
    generate_grid,
    pending_cutoffs,
)

START = date(2024, 7, 1)
# The EXCLUSIVE right edge: the window covers July 1..28 in full.
HORIZON = date(2024, 7, 29)
DAILY = [(86400, None)]


class TestDailyGridGolden:
    """The legacy cumulative enumeration: end = start + day, day = 1..horizon."""

    def test_shape_and_points(self):
        grid = generate_grid(START, HORIZON, DAILY)
        assert grid.start_ts == datetime(2024, 7, 1)
        assert grid.horizon_ts == datetime(2024, 7, 29)  # the exclusive right edge
        assert len(grid) == 28
        expected = [datetime(2024, 7, 1) + timedelta(days=d) for d in range(1, 29)]
        assert [c.end_ts for c in grid.cutoffs] == expected

    def test_only_horizon_flagged(self):
        grid = generate_grid(START, HORIZON, DAILY)
        assert [c.is_horizon for c in grid.cutoffs] == [False] * 27 + [True]

    def test_wo_curr_day_parity(self):
        """data_lag=0 + half-open windows ≡ the legacy *_wo_curr_day source:
        mid-day runs plan only fully-elapsed days."""
        grid = generate_grid(START, HORIZON, DAILY)
        now_utc = datetime(2024, 7, 10, 15, 30)  # mid-day July 10
        watermark = now_utc  # data_lag = 0
        pending = pending_cutoffs(grid, set(), watermark)
        # end_ts 2024-07-10T00 covers through July 9 23:59:59.999 — complete.
        # end_ts 2024-07-11T00 needs the rest of July 10 — not plannable yet.
        assert pending[-1].end_ts == datetime(2024, 7, 10)
        assert len(pending) == 9

    def test_single_day_experiment(self):
        grid = generate_grid(START, date(2024, 7, 2), DAILY)
        assert [c.end_ts for c in grid.cutoffs] == [datetime(2024, 7, 2)]
        assert grid.cutoffs[0].is_horizon


class TestScheduleGrids:
    def test_scalar_equals_single_segment(self):
        """Plan R1 comparability promise."""
        assert generate_grid(START, HORIZON, [(86400, None)]) == generate_grid(
            START, HORIZON, [(86400, None)]
        )
        scalar = generate_grid(START, HORIZON, DAILY)
        one_segment = generate_grid(START, HORIZON, [(86400, None)])
        assert scalar.cutoffs == one_segment.cutoffs

    def test_dense_early_then_daily(self):
        grid = generate_grid(START, HORIZON, [(3600, 172800), (86400, None)])
        points = [c.end_ts for c in grid.cutoffs]
        start = datetime(2024, 7, 1)
        hourly = [start + timedelta(hours=h) for h in range(1, 49)]
        daily_tail = [start + timedelta(days=d) for d in range(3, 29)]
        assert points == hourly + daily_tail
        # 48 hourly + 26 daily (July 4 .. July 29)
        assert len(grid) == 74

    def test_daily_tail_matches_pure_daily(self):
        """§6.1: the schedule's daily tail is point-for-point comparable."""
        schedule = generate_grid(START, HORIZON, [(3600, 172800), (86400, None)])
        pure = generate_grid(START, HORIZON, DAILY)
        boundary = datetime(2024, 7, 3)
        tail = {c.end_ts for c in schedule.cutoffs if c.end_ts > boundary}
        pure_tail = {c.end_ts for c in pure.cutoffs if c.end_ts > boundary}
        assert tail == pure_tail

    def test_non_midnight_until_snaps_daily_tail(self):
        """until: 36h — the daily tail still lands on the pure-daily grid."""
        grid = generate_grid(START, HORIZON, [(3600, 129600), (86400, None)])
        points = [c.end_ts for c in grid.cutoffs]
        # daily points after +36h: +2d, +3d, ... (on the midnight grid)
        assert datetime(2024, 7, 3) in points
        assert datetime(2024, 7, 3, 12) not in points  # daily tail never anchors at 36h+24h

    def test_three_segments(self):
        grid = generate_grid(
            START,
            HORIZON,
            [(3600, 21600), (21600, 172800), (86400, None)],  # 1h→6h→1d
        )
        points = [c.end_ts for c in grid.cutoffs]
        start = datetime(2024, 7, 1)
        assert points[:6] == [start + timedelta(hours=h) for h in range(1, 7)]
        # 6h points: +12h, +18h, ..., +48h
        assert start + timedelta(hours=12) in points
        assert start + timedelta(hours=7) not in points

    def test_weekly_cadence(self):
        grid = generate_grid(START, HORIZON, [(7 * 86400, None)])
        points = [c.end_ts for c in grid.cutoffs]
        start = datetime(2024, 7, 1)
        assert points == [start + timedelta(days=d) for d in (7, 14, 21, 28)]
        assert points[-1] == grid.horizon_ts  # aligned horizon deduped...
        assert grid.cutoffs[-1].is_horizon  # ...and flagged


class TestHorizon:
    def test_horizon_appended_when_cadence_does_not_divide(self):
        grid = generate_grid(START, HORIZON, [(5 * 86400, None)])
        points = [c.end_ts for c in grid.cutoffs]
        start = datetime(2024, 7, 1)
        assert points == [start + timedelta(days=d) for d in (5, 10, 15, 20, 25, 28)]
        assert grid.cutoffs[-1].is_horizon
        assert not grid.cutoffs[-2].is_horizon

    def test_no_duplicate_when_aligned(self):
        grid = generate_grid(START, HORIZON, DAILY)
        assert len({c.end_ts for c in grid.cutoffs}) == len(grid)


class TestTimezones:
    def test_moscow_midnights(self):
        grid = generate_grid(START, HORIZON, DAILY, tz="Europe/Moscow")
        # Moscow midnight = 21:00 UTC the previous day, year-round (UTC+3)
        assert grid.start_ts == datetime(2024, 6, 30, 21, 0)
        assert grid.cutoffs[0].end_ts == datetime(2024, 7, 1, 21, 0)

    def test_dst_spring_forward(self):
        """America/New_York, March 2024: the local-midnight grid absorbs DST."""
        grid = generate_grid(date(2024, 3, 8), date(2024, 3, 13), DAILY, tz="America/New_York")
        points = [c.end_ts for c in grid.cutoffs]
        assert points[0] == datetime(2024, 3, 9, 5, 0)  # EST midnight
        assert points[1] == datetime(2024, 3, 10, 5, 0)  # EST midnight
        assert points[2] == datetime(2024, 3, 11, 4, 0)  # EDT midnight (23h day)
        deltas = [(b - a).total_seconds() for a, b in zip(points, points[1:], strict=False)]
        assert 23 * 3600 in deltas

    def test_dst_fall_back_keeps_the_daily_until_boundary_look(self):
        """A whole-day `until` bound compares in DAY space: the 25h fall-back
        day (2024-11-03 America/New_York) must not drop the boundary look."""
        grid = generate_grid(
            date(2024, 11, 1),
            date(2024, 11, 7),
            [(86400, 3 * 86400), (2 * 86400, None)],
            tz="America/New_York",
        )
        points = [c.end_ts for c in grid.cutoffs]
        # day-3 local midnight = 2024-11-04 05:00 UTC (EST after fall-back) —
        # 73h after start_ts, beyond a naive seconds bound of 72h
        assert datetime(2024, 11, 4, 5, 0) in points

    def test_sub_day_segments_are_absolute_durations(self):
        """Dense points anchor at start_ts in absolute time (no local snapping)."""
        grid = generate_grid(
            date(2024, 3, 10),
            date(2024, 3, 12),
            [(3600, 21600), (86400, None)],
            tz="America/New_York",
        )
        start = grid.start_ts
        hourly = [c.end_ts for c in grid.cutoffs][:6]
        assert hourly == [start + timedelta(hours=h) for h in range(1, 7)]


class TestSubDayEdges:
    """m10 WP1: the window edges are instants, not calendar days."""

    def test_explicit_start_is_not_snapped_to_midnight(self):
        grid = generate_grid(datetime(2024, 7, 1, 14, 30), date(2024, 7, 5), DAILY)
        assert grid.start_ts == datetime(2024, 7, 1, 14, 30)
        assert grid.horizon_ts == datetime(2024, 7, 5)

    def test_explicit_horizon_is_the_exact_instant(self):
        grid = generate_grid(START, datetime(2024, 7, 3, 18, 0), DAILY)
        assert grid.horizon_ts == datetime(2024, 7, 3, 18, 0)
        assert [c.end_ts for c in grid.cutoffs] == [
            datetime(2024, 7, 2),
            datetime(2024, 7, 3),
            datetime(2024, 7, 3, 18, 0),  # the off-lattice horizon, appended
        ]
        assert grid.cutoffs[-1].is_horizon

    def test_bare_date_horizon_is_the_exclusive_right_edge(self):
        """`horizon_ts: 2024-07-05` means midnight OPENING July 5, not closing it."""
        grid = generate_grid(START, date(2024, 7, 5), DAILY)
        assert grid.horizon_ts == datetime(2024, 7, 5)
        assert len(grid) == 4

    def test_a_date_and_its_midnight_datetime_are_the_same_grid(self):
        assert generate_grid(START, date(2024, 7, 5), DAILY) == generate_grid(
            datetime(2024, 7, 1, 0, 0), datetime(2024, 7, 5, 0, 0), DAILY
        )

    def test_sub_day_start_keeps_the_local_midnight_lattice_by_default(self):
        """The default anchor is midnight, so a 14:30 start still reads whole days —
        only the FIRST window is short (9h30m)."""
        grid = generate_grid(datetime(2024, 7, 1, 14, 30), date(2024, 7, 5), DAILY)
        assert [c.end_ts for c in grid.cutoffs] == [datetime(2024, 7, d) for d in (2, 3, 4, 5)]
        assert grid.cutoffs[0].end_ts - grid.start_ts == timedelta(hours=9, minutes=30)

    def test_horizon_not_after_start_is_refused(self):
        with pytest.raises(ValueError, match="is not after start_ts"):
            generate_grid(START, START, DAILY)
        with pytest.raises(ValueError, match="is not after start_ts"):
            generate_grid(START, date(2024, 6, 30), DAILY)


class TestIntervalAnchor:
    """m10 D2: cutoffs are ``anchor + k·interval``, snapped forward past start."""

    def test_start_anchor_equals_midnight_anchor_for_a_bare_date_start(self):
        """The cheapest proof the default path did not move: at a midnight start
        the two anchors are the same instant, so the grids must be identical."""
        assert generate_grid(START, HORIZON, DAILY, interval_anchor="start") == generate_grid(
            START, HORIZON, DAILY, interval_anchor="midnight"
        )
        assert generate_grid(START, HORIZON, DAILY) == generate_grid(
            START, HORIZON, DAILY, interval_anchor="start"
        )

    def test_start_anchor_holds_the_wall_clock_time(self):
        grid = generate_grid(
            datetime(2024, 7, 1, 14, 30), date(2024, 7, 5), DAILY, interval_anchor="start"
        )
        assert [c.end_ts for c in grid.cutoffs] == [
            datetime(2024, 7, 2, 14, 30),
            datetime(2024, 7, 3, 14, 30),
            datetime(2024, 7, 4, 14, 30),
            datetime(2024, 7, 5),  # the horizon, off the lattice
        ]

    def test_explicit_anchor_before_the_start_snaps_forward(self):
        """The decided case: 3-day windows at 00:00 MSK (21:00 UTC the day
        before) on a UTC experiment that starts mid-cycle."""
        grid = generate_grid(
            datetime(2024, 7, 1, 10, 0),
            date(2024, 7, 15),
            [(3 * 86400, None)],
            interval_anchor=datetime(2024, 6, 29, 21, 0),
        )
        points = [c.end_ts for c in grid.cutoffs]
        assert points[:3] == [
            datetime(2024, 7, 2, 21, 0),
            datetime(2024, 7, 5, 21, 0),
            datetime(2024, 7, 8, 21, 0),
        ]
        # the anchor precedes the start, so the first window is legitimately partial
        assert points[0] - grid.start_ts < timedelta(days=3)

    def test_explicit_anchor_after_the_start_still_lands_on_its_lattice(self):
        """The first point comes from a NEGATIVE k — the lattice extends both
        ways from the anchor, and only the forward snap is start-relative."""
        grid = generate_grid(
            START, date(2024, 7, 21), [(5 * 86400, None)], interval_anchor=date(2024, 7, 16)
        )
        assert [c.end_ts for c in grid.cutoffs] == [
            datetime(2024, 7, 6),
            datetime(2024, 7, 11),
            datetime(2024, 7, 16),
            datetime(2024, 7, 21),
        ]

    def test_an_anchor_decades_before_the_start_is_solved_not_walked(self):
        """A k=0 walk-up would spin ~470k iterations emitting nothing, and the
        `limit` gate would not fire (it only counts points actually added)."""
        grid = generate_grid(
            START, HORIZON, [(3600, None)], interval_anchor=date(1970, 1, 1), limit=1000
        )
        assert grid.cutoffs[0].end_ts == datetime(2024, 7, 1, 1, 0)
        assert len(grid) == 28 * 24

    def test_limit_still_fires_under_an_explicit_anchor(self):
        with pytest.raises(GridLimitExceeded, match="exceeds 10 looks"):
            generate_grid(
                START, HORIZON, [(3600, None)], limit=10, interval_anchor=date(1970, 1, 1)
            )

    def test_sub_day_cadence_rides_the_anchor_lattice(self):
        """A 6h lattice hung off midnight, entered at 10:00: the points are
        12:00/18:00/00:00 — not 16:00/22:00 counted from the start."""
        grid = generate_grid(
            datetime(2024, 7, 1, 10, 0),
            date(2024, 7, 2),
            [(6 * 3600, None)],
            interval_anchor=datetime(2024, 7, 1, 0, 0),
        )
        assert [c.end_ts for c in grid.cutoffs] == [
            datetime(2024, 7, 1, 12, 0),
            datetime(2024, 7, 1, 18, 0),
            datetime(2024, 7, 2),
        ]

    def test_day_cadence_holds_local_wall_clock_across_a_dst_jump(self):
        """DST-safe by construction: the lattice steps in CALENDAR days at the
        anchor's local time, so the UTC instants shift by the offset change and
        one 'day' is 23h — never a fixed 86400s."""
        grid = generate_grid(
            datetime(2024, 3, 8, 14, 30),
            date(2024, 3, 13),
            DAILY,
            tz="America/New_York",
            interval_anchor="start",
        )
        points = [c.end_ts for c in grid.cutoffs]
        assert points[:4] == [
            datetime(2024, 3, 9, 19, 30),  # EST (UTC-5)
            datetime(2024, 3, 10, 18, 30),  # EDT (UTC-4) — the 23h day
            datetime(2024, 3, 11, 18, 30),
            datetime(2024, 3, 12, 18, 30),
        ]
        assert (points[1] - points[0]) == timedelta(hours=23)

    def test_a_phase_mismatched_anchor_bounds_segments_in_TIME_space(self):
        """Segment ``until`` bounds are offsets from the START; the day-space
        comparison that makes them DST-proof is only valid while the lattice
        shares the start's wall-clock phase. Off-phase, the bound must be read
        as elapsed seconds — this test fails in BOTH directions (dropping the
        day-space branch, or applying it unconditionally)."""
        grid = generate_grid(
            START,  # midnight
            date(2024, 7, 10),
            [(86400, 3 * 86400), (2 * 86400, None)],
            interval_anchor=datetime(2024, 7, 1, 6, 0),  # phase 06:00 vs 00:00
        )
        points = [c.end_ts for c in grid.cutoffs]
        # `until: 3d` is 72h after the start = 07-04 00:00. The 07-04 06:00
        # lattice point sits at 78h — day-space would keep it (its day offset
        # is 3), elapsed time correctly drops it.
        assert datetime(2024, 7, 4, 6, 0) not in points
        assert points == [
            datetime(2024, 7, 1, 6, 0),
            datetime(2024, 7, 2, 6, 0),
            datetime(2024, 7, 3, 6, 0),
            datetime(2024, 7, 5, 6, 0),  # the 2d segment, still on the lattice
            datetime(2024, 7, 7, 6, 0),
            datetime(2024, 7, 9, 6, 0),
            datetime(2024, 7, 10),  # horizon
        ]

    @pytest.mark.parametrize(
        ("start", "horizon", "segments", "anchor"),
        [
            (date(9999, 12, 30), date(9999, 12, 31), DAILY, "midnight"),
            (date(9999, 12, 1), date(9999, 12, 31), [(7 * 86400, None)], "midnight"),
            (datetime(9999, 12, 31, 20, 0), datetime(9999, 12, 31, 23, 0), [(3600, None)], "start"),
            (date(1, 1, 1), date(1, 1, 10), DAILY, "midnight"),
            (date(2024, 7, 1), date(2024, 12, 1), [(30 * 86400, None)], date(1, 1, 1)),
        ],
    )
    def test_a_window_against_the_calendar_edge_ends_instead_of_crashing(
        self, start, horizon, segments, anchor
    ):
        """A lattice step off the end of the representable calendar is, for
        every comparison in the enumeration, simply 'beyond that edge'. It
        used to raise OverflowError straight out of the planner — through
        config validation and `abk run` alike."""
        grid = generate_grid(start, horizon, segments, interval_anchor=anchor)
        assert grid.cutoffs, "the horizon is always planned"
        assert grid.cutoffs[-1].end_ts == grid.horizon_ts
        assert grid.cutoffs[-1].is_horizon

    def test_an_unknown_anchor_keyword_is_refused(self):
        with pytest.raises(ValueError, match="is not 'midnight', 'start'"):
            generate_grid(START, HORIZON, DAILY, interval_anchor="noon")


class TestLimit:
    def test_max_looks_gate_through_the_same_enumeration(self):
        with pytest.raises(GridLimitExceeded, match="exceeds 10 looks"):
            generate_grid(START, HORIZON, [(3600, None)], limit=10)

    def test_limit_not_hit(self):
        grid = generate_grid(START, HORIZON, DAILY, limit=5000)
        assert len(grid) == 28


class TestPendingCutoffs:
    def make_grid(self):
        return generate_grid(START, HORIZON, DAILY)

    def test_anti_join_skips_computed(self):
        grid = self.make_grid()
        computed = {datetime(2024, 7, 2), datetime(2024, 7, 3)}
        pending = pending_cutoffs(grid, computed, watermark_ts=datetime(2024, 7, 6))
        assert [c.end_ts for c in pending] == [
            datetime(2024, 7, 4),
            datetime(2024, 7, 5),
            datetime(2024, 7, 6),
        ]

    def test_middle_hole_is_replanned(self):
        """A late hole must be re-planned — the set semantics, not a cursor."""
        grid = self.make_grid()
        computed = {datetime(2024, 7, d) for d in (2, 3, 5, 6)}  # hole at the 4th
        pending = pending_cutoffs(grid, computed, watermark_ts=datetime(2024, 7, 6, 12))
        assert [c.end_ts for c in pending] == [datetime(2024, 7, 4)]

    def test_watermark_excludes_incomplete_tail(self):
        grid = self.make_grid()
        watermark = datetime(2024, 7, 10) - timedelta(hours=2)  # data_lag 2h at 00:00
        pending = pending_cutoffs(grid, set(), watermark)
        assert pending[-1].end_ts == datetime(2024, 7, 9)

    def test_full_refresh_reincludes_window(self):
        grid = self.make_grid()
        computed = {datetime(2024, 7, d) for d in range(2, 8)}
        pending = pending_cutoffs(
            grid,
            computed,
            watermark_ts=datetime(2024, 7, 7),
            full_refresh_window=(datetime(2024, 7, 3), datetime(2024, 7, 5)),
        )
        assert [c.end_ts for c in pending] == [datetime(2024, 7, 3), datetime(2024, 7, 4)]

    def test_deterministic_for_fixed_inputs(self):
        grid = self.make_grid()
        a = pending_cutoffs(grid, {datetime(2024, 7, 2)}, datetime(2024, 7, 5))
        b = pending_cutoffs(grid, {datetime(2024, 7, 2)}, datetime(2024, 7, 5))
        assert a == b


class TestBacklog:
    def test_none_when_fresh(self):
        assert backlog_seconds(set(), datetime(2024, 7, 10)) is None

    def test_measures_trailing_gap(self):
        computed = {datetime(2024, 7, 5), datetime(2024, 7, 8)}
        assert backlog_seconds(computed, datetime(2024, 7, 10)) == 2 * 86400
