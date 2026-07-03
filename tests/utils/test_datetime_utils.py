"""Tests for abkit.utils.datetime_utils."""

from datetime import datetime, timezone

from abkit.utils.datetime_utils import (
    format_duration,
    now_utc,
    now_utc_naive,
    to_aware_utc,
    to_naive_utc,
)


class TestNowUtc:
    def test_returns_aware_datetime(self):
        result = now_utc()
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc

    def test_is_close_to_current_time(self):
        before = datetime.now(timezone.utc)
        result = now_utc()
        after = datetime.now(timezone.utc)
        assert before <= result <= after


class TestNowUtcNaive:
    def test_returns_naive_datetime(self):
        result = now_utc_naive()
        assert result.tzinfo is None

    def test_is_close_to_current_time(self):
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        result = now_utc_naive()
        after = datetime.now(timezone.utc).replace(tzinfo=None)
        assert before <= result <= after

    def test_value_matches_now_utc(self):
        aware = now_utc().replace(tzinfo=None)
        naive = now_utc_naive()
        diff = abs((naive - aware).total_seconds())
        assert diff < 1.0


class TestToNaiveUtc:
    def test_none_returns_none(self):
        assert to_naive_utc(None) is None

    def test_naive_datetime_unchanged(self):
        dt = datetime(2024, 1, 15, 12, 0, 0)
        result = to_naive_utc(dt)
        assert result == dt
        assert result.tzinfo is None

    def test_aware_utc_strips_tzinfo(self):
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = to_naive_utc(dt)
        assert result.tzinfo is None
        assert result == datetime(2024, 1, 15, 12, 0, 0)

    def test_preserves_value(self):
        dt = datetime(2024, 6, 20, 8, 30, 45, tzinfo=timezone.utc)
        result = to_naive_utc(dt)
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 20
        assert result.hour == 8
        assert result.minute == 30
        assert result.second == 45

    def test_idempotent_on_naive(self):
        dt = datetime(2024, 1, 15, 12, 0, 0)
        assert to_naive_utc(to_naive_utc(dt)) == to_naive_utc(dt)


class TestToAwareUtc:
    def test_none_returns_none(self):
        assert to_aware_utc(None) is None

    def test_naive_gets_utc_attached(self):
        dt = datetime(2024, 1, 15, 12, 0, 0)
        result = to_aware_utc(dt)
        assert result.tzinfo == timezone.utc
        assert result == datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_aware_utc_unchanged(self):
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = to_aware_utc(dt)
        assert result == dt
        assert result.tzinfo == timezone.utc

    def test_preserves_value(self):
        dt = datetime(2024, 6, 20, 8, 30, 45)
        result = to_aware_utc(dt)
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 20
        assert result.hour == 8
        assert result.minute == 30
        assert result.second == 45

    def test_idempotent_on_aware(self):
        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert to_aware_utc(to_aware_utc(dt)) == to_aware_utc(dt)


class TestRoundTrip:
    def test_naive_to_aware_to_naive(self):
        dt = datetime(2024, 3, 10, 16, 45, 0)
        assert to_naive_utc(to_aware_utc(dt)) == dt

    def test_aware_to_naive_to_aware(self):
        dt = datetime(2024, 3, 10, 16, 45, 0, tzinfo=timezone.utc)
        assert to_aware_utc(to_naive_utc(dt)) == dt


class TestFormatDuration:
    def test_sub_minute_renders_seconds(self):
        assert format_duration(30) == "30s"

    def test_minutes(self):
        assert format_duration(600) == "10m"

    def test_two_units_max(self):
        assert format_duration(9000) == "2h 30m"
        assert format_duration(90000) == "1d 1h"

    def test_exact_units_drop_zero_parts(self):
        assert format_duration(3600) == "1h"
        assert format_duration(86400) == "1d"

    def test_zero_and_negative_degrade(self):
        assert format_duration(0) == "0m"
        assert format_duration(-5) == "0m"

    def test_float_input_rounds(self):
        assert format_duration(59.6) == "1m"
