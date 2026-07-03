"""Shared state for the internal-tables mixins.

The split of :class:`InternalTablesManager` into mixins relies on every
mixin reading ``self._manager`` and a couple of small helpers that handle
ClickHouse quirks (notably the "epoch-as-NULL" return for ``MAX(t)`` over
empty ranges) plus the strictly-monotonic version source every LWW write
must use (quorum must-fix "created_at strictly-increasing & distinct").
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from abkit.database.manager import BaseDatabaseManager
from abkit.utils.datetime_utils import now_utc_naive

_EPOCH_NAIVE = datetime(1970, 1, 1, 0, 0, 0)
_ONE_MS = timedelta(milliseconds=1)


class _InternalTablesBase:
    """Holds the underlying database manager and shared helpers."""

    def __init__(self, manager: BaseDatabaseManager):
        self._manager = manager
        self._version_lock = threading.Lock()
        self._last_version_ts: datetime | None = None

    @staticmethod
    def _normalize_max_timestamp(value: datetime | None) -> datetime | None:
        """Treat the Unix epoch sentinel as a missing value.

        ClickHouse's ``max(timestamp)`` over an empty selection returns
        ``1970-01-01 00:00:00`` instead of NULL. Without normalisation,
        idempotency checks would think we already processed everything up
        to 1970 and refuse to do work.
        """
        if value is None:
            return None
        epoch = _EPOCH_NAIVE
        if value.tzinfo is not None:
            epoch = epoch.replace(tzinfo=value.tzinfo)
        if value == epoch:
            return None
        return value

    def next_version_ts(self) -> datetime:
        """Return a strictly-increasing, distinct naive-UTC version timestamp.

        Every LWW-versioned write (``created_at`` on ``_ab_results``,
        ``version`` on ``_ab_unit_state``, ``loaded_at`` on ``_ab_exposures``)
        stamps its version through this method. Wall-clock ms precision would
        tie for two writes within the same millisecond, making
        ReplacingMergeTree/LWW dedup ambiguous — so ties are broken by
        advancing at least 1ms past the previous issued version (per manager
        instance, thread-safe). Cross-process ties are excluded by the
        ``_ab_tasks`` lock serializing writers at (experiment[, metric]) grain
        — this coupling is deliberate (plan R5).
        """
        with self._version_lock:
            now = now_utc_naive()
            # floor to ms so the DateTime64(3) round-trip is exact
            now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
            if self._last_version_ts is not None and now <= self._last_version_ts:
                now = self._last_version_ts + _ONE_MS
            self._last_version_ts = now
            return now
