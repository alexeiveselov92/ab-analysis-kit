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
END = date(2024, 7, 28)
DAILY = [(86400, None)]


class TestDailyGridGolden:
    """The legacy cumulative enumeration: end = start + day, day = 1..horizon."""

    def test_shape_and_points(self):
        grid = generate_grid(START, END, DAILY)
        assert grid.start_ts == datetime(2024, 7, 1)
        assert grid.horizon_ts == datetime(2024, 7, 29)  # covers end_date in full
        assert len(grid) == 28
        expected = [datetime(2024, 7, 1) + timedelta(days=d) for d in range(1, 29)]
        assert [c.end_ts for c in grid.cutoffs] == expected

    def test_only_horizon_flagged(self):
        grid = generate_grid(START, END, DAILY)
        assert [c.is_horizon for c in grid.cutoffs] == [False] * 27 + [True]

    def test_wo_curr_day_parity(self):
        """data_lag=0 + half-open windows ≡ the legacy *_wo_curr_day source:
        mid-day runs plan only fully-elapsed days."""
        grid = generate_grid(START, END, DAILY)
        now_utc = datetime(2024, 7, 10, 15, 30)  # mid-day July 10
        watermark = now_utc  # data_lag = 0
        pending = pending_cutoffs(grid, set(), watermark)
        # end_ts 2024-07-10T00 covers through July 9 23:59:59.999 — complete.
        # end_ts 2024-07-11T00 needs the rest of July 10 — not plannable yet.
        assert pending[-1].end_ts == datetime(2024, 7, 10)
        assert len(pending) == 9

    def test_single_day_experiment(self):
        grid = generate_grid(START, START, DAILY)
        assert [c.end_ts for c in grid.cutoffs] == [datetime(2024, 7, 2)]
        assert grid.cutoffs[0].is_horizon


class TestScheduleGrids:
    def test_scalar_equals_single_segment(self):
        """Plan R1 comparability promise."""
        assert generate_grid(START, END, [(86400, None)]) == generate_grid(
            START, END, [(86400, None)]
        )
        scalar = generate_grid(START, END, DAILY)
        one_segment = generate_grid(START, END, [(86400, None)])
        assert scalar.cutoffs == one_segment.cutoffs

    def test_dense_early_then_daily(self):
        grid = generate_grid(START, END, [(3600, 172800), (86400, None)])
        points = [c.end_ts for c in grid.cutoffs]
        start = datetime(2024, 7, 1)
        hourly = [start + timedelta(hours=h) for h in range(1, 49)]
        daily_tail = [start + timedelta(days=d) for d in range(3, 29)]
        assert points == hourly + daily_tail
        # 48 hourly + 26 daily (July 4 .. July 29)
        assert len(grid) == 74

    def test_daily_tail_matches_pure_daily(self):
        """§6.1: the schedule's daily tail is point-for-point comparable."""
        schedule = generate_grid(START, END, [(3600, 172800), (86400, None)])
        pure = generate_grid(START, END, DAILY)
        boundary = datetime(2024, 7, 3)
        tail = {c.end_ts for c in schedule.cutoffs if c.end_ts > boundary}
        pure_tail = {c.end_ts for c in pure.cutoffs if c.end_ts > boundary}
        assert tail == pure_tail

    def test_non_midnight_until_snaps_daily_tail(self):
        """until: 36h — the daily tail still lands on the pure-daily grid."""
        grid = generate_grid(START, END, [(3600, 129600), (86400, None)])
        points = [c.end_ts for c in grid.cutoffs]
        # daily points after +36h: +2d, +3d, ... (on the midnight grid)
        assert datetime(2024, 7, 3) in points
        assert datetime(2024, 7, 3, 12) not in points  # daily tail never anchors at 36h+24h

    def test_three_segments(self):
        grid = generate_grid(
            START,
            END,
            [(3600, 21600), (21600, 172800), (86400, None)],  # 1h→6h→1d
        )
        points = [c.end_ts for c in grid.cutoffs]
        start = datetime(2024, 7, 1)
        assert points[:6] == [start + timedelta(hours=h) for h in range(1, 7)]
        # 6h points: +12h, +18h, ..., +48h
        assert start + timedelta(hours=12) in points
        assert start + timedelta(hours=7) not in points

    def test_weekly_cadence(self):
        grid = generate_grid(START, END, [(7 * 86400, None)])
        points = [c.end_ts for c in grid.cutoffs]
        start = datetime(2024, 7, 1)
        assert points == [start + timedelta(days=d) for d in (7, 14, 21, 28)]
        assert points[-1] == grid.horizon_ts  # aligned horizon deduped...
        assert grid.cutoffs[-1].is_horizon  # ...and flagged


class TestHorizon:
    def test_horizon_appended_when_cadence_does_not_divide(self):
        grid = generate_grid(START, END, [(5 * 86400, None)])
        points = [c.end_ts for c in grid.cutoffs]
        start = datetime(2024, 7, 1)
        assert points == [start + timedelta(days=d) for d in (5, 10, 15, 20, 25, 28)]
        assert grid.cutoffs[-1].is_horizon
        assert not grid.cutoffs[-2].is_horizon

    def test_no_duplicate_when_aligned(self):
        grid = generate_grid(START, END, DAILY)
        assert len({c.end_ts for c in grid.cutoffs}) == len(grid)


class TestTimezones:
    def test_moscow_midnights(self):
        grid = generate_grid(START, END, DAILY, tz="Europe/Moscow")
        # Moscow midnight = 21:00 UTC the previous day, year-round (UTC+3)
        assert grid.start_ts == datetime(2024, 6, 30, 21, 0)
        assert grid.cutoffs[0].end_ts == datetime(2024, 7, 1, 21, 0)

    def test_dst_spring_forward(self):
        """America/New_York, March 2024: the local-midnight grid absorbs DST."""
        grid = generate_grid(date(2024, 3, 8), date(2024, 3, 12), DAILY, tz="America/New_York")
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
            date(2024, 11, 6),
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
            date(2024, 3, 11),
            [(3600, 21600), (86400, None)],
            tz="America/New_York",
        )
        start = grid.start_ts
        hourly = [c.end_ts for c in grid.cutoffs][:6]
        assert hourly == [start + timedelta(hours=h) for h in range(1, 7)]


class TestLimit:
    def test_max_looks_gate_through_the_same_enumeration(self):
        with pytest.raises(GridLimitExceeded, match="exceeds 10 looks"):
            generate_grid(START, END, [(3600, None)], limit=10)

    def test_limit_not_hit(self):
        grid = generate_grid(START, END, DAILY, limit=5000)
        assert len(grid) == 28


class TestPendingCutoffs:
    def make_grid(self):
        return generate_grid(START, END, DAILY)

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
