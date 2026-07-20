"""The shared cohort-count math (m8 WP4 step 4) — the ONE bisect/rate
implementation both source modes call: the ``_ExposuresMixin`` buckets
warehouse rows, the direct-mode driver/plan bucket the in-memory snapshot.
These tests pin the pure functions; the mixin's own suite pins the DB path."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from abkit.core.exposure_counting import arrival_rate, bucket_timestamps, count_stream

T0 = datetime(2024, 7, 1)


class TestBucketTimestamps:
    def test_sorts_and_zero_fills_declared_variants(self):
        per_variant = bucket_timestamps(
            [
                ("control", T0 + timedelta(hours=2)),
                ("control", T0),
                ("treatment", T0 + timedelta(hours=1)),
            ],
            ["control", "treatment", "ghost_arm"],
        )
        assert per_variant["control"] == [T0, T0 + timedelta(hours=2)]
        assert per_variant["treatment"] == [T0 + timedelta(hours=1)]
        assert per_variant["ghost_arm"] == []  # declared-but-absent stays present

    def test_drops_undeclared_variants_and_null_timestamps(self):
        per_variant = bucket_timestamps(
            [("control", T0), ("rogue", T0), (None, T0), ("control", None)],
            ["control"],
        )
        assert per_variant == {"control": [T0]}


class TestCountStream:
    def test_boundary_edge_is_exclusive(self):
        # exposure exactly AT a boundary belongs to the NEXT window — the
        # half-open [start, end_ts) convention the metric loads use
        per_variant = bucket_timestamps([("control", T0)], ["control"])
        stream = count_stream(per_variant, [T0, T0 + timedelta(seconds=1)], ["control"])
        assert stream == [{"control": 0}, {"control": 1}]

    def test_cumulative_ascending_counts(self):
        per_variant = bucket_timestamps(
            [("control", T0 + timedelta(hours=h)) for h in (1, 5, 9)]
            + [("treatment", T0 + timedelta(hours=h)) for h in (1, 5)],
            ["control", "treatment"],
        )
        boundaries = [T0 + timedelta(hours=6 * k) for k in (1, 2)]
        assert count_stream(per_variant, boundaries, ["control", "treatment"]) == [
            {"control": 2, "treatment": 2},
            {"control": 3, "treatment": 2},
        ]

    def test_empty_boundaries(self):
        assert count_stream({"control": []}, [], ["control"]) == []


class TestArrivalRate:
    def test_rates_over_the_shared_calendar_window(self):
        # 4 control + 2 treatment over exactly 2 days ⇒ 2.0 and 1.0 units/day
        per_variant = bucket_timestamps(
            [("control", T0 + timedelta(hours=12 * k)) for k in range(4)]
            + [("treatment", T0), ("treatment", T0 + timedelta(days=2))],
            ["control", "treatment"],
        )
        result = arrival_rate(per_variant, ["control", "treatment"])
        assert result is not None
        rates, window_days = result
        assert window_days == pytest.approx(2.0)
        assert rates == {"control": pytest.approx(2.0), "treatment": pytest.approx(1.0)}

    def test_empty_cohort_returns_none(self):
        assert arrival_rate({"control": [], "treatment": []}, ["control", "treatment"]) is None

    def test_one_instant_window_returns_none(self):
        # a backfilled cohort (max == min) must SKIP, never invent a rate
        per_variant = bucket_timestamps(
            [("control", T0), ("treatment", T0)], ["control", "treatment"]
        )
        assert arrival_rate(per_variant, ["control", "treatment"]) is None
