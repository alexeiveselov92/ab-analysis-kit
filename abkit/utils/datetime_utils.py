"""UTC datetime utilities.

Contract: all internal timestamps are naive UTC (tzinfo=None).
- ClickHouse DateTime64(3, 'UTC') stores and returns naive UTC
- numpy datetime64 has no timezone representation
- Comparisons between timestamps must use the same convention

Experiment-local timezones (the ``timezone:`` config field) are interpreted at
the planner boundary only (docs/specs/cumulative-intervals.md §6.3); storage
and comparison are always UTC.

Functions:
    now_utc()       -> aware UTC datetime (for calculations requiring timezone)
    now_utc_naive() -> naive UTC datetime (for numpy / ClickHouse inserts)
    to_naive_utc()  -> normalize any datetime to naive UTC
    to_aware_utc()  -> normalize any datetime to aware UTC
"""

from datetime import datetime, timezone


def now_utc() -> datetime:
    """Return current time as timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def now_utc_naive() -> datetime:
    """Return current time as naive UTC datetime (for numpy / ClickHouse)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """Strip tzinfo from a UTC datetime, returning naive UTC.

    Args:
        dt: datetime object (aware or naive) or None

    Returns:
        Naive UTC datetime, or None if input is None
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        # CONVERT to UTC first: an aware non-UTC value (e.g. a tz-aware
        # exposure_ts column) must not be re-labelled by stripping tzinfo.
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_aware_utc(dt: datetime | None) -> datetime | None:
    """Attach UTC timezone to a naive datetime.

    Args:
        dt: datetime object (aware or naive) or None

    Returns:
        Timezone-aware UTC datetime, or None if input is None
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def format_duration(seconds: int | float) -> str:
    """Format a span of seconds as a compact human string (max two units).

    Used by CLI/report output to express windows and elapsed spans in plain
    language:

        >>> format_duration(600)      # 10 minutes
        '10m'
        >>> format_duration(9000)     # 2h 30m
        '2h 30m'
        >>> format_duration(90000)    # 1d 1h
        '1d 1h'
        >>> format_duration(30)
        '30s'

    Keeps at most the two most-significant non-zero units so the result
    stays glanceable. Sub-minute spans render in seconds; zero/negative
    inputs degrade to ``"0m"`` rather than raising.
    """
    total = int(round(seconds))
    if total <= 0:
        return "0m"
    if total < 60:
        return f"{total}s"

    parts: list[str] = []
    remaining = total
    for label, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if remaining >= size:
            qty, remaining = divmod(remaining, size)
            parts.append(f"{qty}{label}")
        if len(parts) == 2:
            break
    return " ".join(parts)
