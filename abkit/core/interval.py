"""Interval (duration) parsing and handling.

Used for the experiment ``cadence`` scalar and horizon arithmetic
(docs/specs/cumulative-intervals.md §6.1: grammar ``N{s,m,h,d,w}``, whole
seconds >= 1s). Long unit names ("10min", "2hours") are also accepted.

Schedule-typed cadence (the ``[{every, until}]`` coarsening list) is NOT this
class's job — segments are validated in config and expanded by
``core/period_planner`` (each segment's ``every``/``until`` is one Interval).

Supports:
- Integer seconds: 600
- String format: "30s", "10min", "1h", "1d", "2w"
"""

import re
from datetime import timedelta


class Interval:
    """
    Represents a time interval in whole seconds.

    Supports parsing from:
    - Integer (seconds): 600
    - String: "10min", "1h", "1d", "30s", "1w"

    Examples:
        >>> interval = Interval("10min")
        >>> interval.seconds
        600
        >>> interval = Interval(3600)
        >>> interval.seconds
        3600
    """

    UNITS = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
        "w": 604800,
        "week": 604800,
        "weeks": 604800,
    }

    def __init__(self, value: int | str):
        """
        Initialize interval from integer or string.

        Args:
            value: Interval as integer (seconds) or string ("10min")

        Raises:
            ValueError: If string format is invalid
        """
        if isinstance(value, bool):
            raise TypeError(f"Interval must be int or str, got {type(value)}")
        if isinstance(value, int):
            if value <= 0:
                raise ValueError(f"Interval must be positive, got {value}")
            self._seconds = value
        elif isinstance(value, str):
            self._seconds = self._parse_string(value)
        else:
            raise TypeError(f"Interval must be int or str, got {type(value)}")

    def _parse_string(self, s: str) -> int:
        """
        Parse interval string.

        Args:
            s: String like "10min", "1h", "30s"

        Returns:
            Interval in seconds

        Raises:
            ValueError: If format is invalid
        """
        s = s.strip().lower()

        # Match pattern: digits followed by unit
        match = re.match(r"^(\d+)([a-z]+)$", s)
        if not match:
            raise ValueError(
                f"Invalid interval format: '{s}'. "
                f"Expected format: <number><unit> (e.g., '10min', '1h')"
            )

        value_str, unit = match.groups()
        value = int(value_str)

        if value <= 0:
            raise ValueError(f"Interval value must be positive, got {value}")

        if unit not in self.UNITS:
            raise ValueError(
                f"Unknown time unit: '{unit}'. "
                f"Supported units: {', '.join(sorted(set(self.UNITS.keys())))}"
            )

        return value * self.UNITS[unit]

    @property
    def seconds(self) -> int:
        """Get interval in seconds."""
        return self._seconds

    def to_timedelta(self) -> timedelta:
        """Get interval as a :class:`datetime.timedelta` (planner arithmetic)."""
        return timedelta(seconds=self._seconds)

    def __eq__(self, other) -> bool:
        """Check equality based on seconds."""
        if isinstance(other, Interval):
            return self._seconds == other._seconds
        return False

    def __hash__(self) -> int:
        """Hash based on seconds."""
        return hash(self._seconds)

    def __repr__(self) -> str:
        """String representation."""
        return f"Interval({self._seconds})"

    def __str__(self) -> str:
        """User-friendly string representation."""
        # Try to represent in human-readable format
        if self._seconds % 86400 == 0:
            return f"{self._seconds // 86400}d"
        elif self._seconds % 3600 == 0:
            return f"{self._seconds // 3600}h"
        elif self._seconds % 60 == 0:
            return f"{self._seconds // 60}min"
        else:
            return f"{self._seconds}s"
