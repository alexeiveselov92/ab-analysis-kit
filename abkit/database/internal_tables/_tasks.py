"""Task locking mixin: ``_ab_tasks`` operations.

Owns the ``_ab_tasks`` POLICY (row shape, staleness semantics, owner token);
the atomicity PRIMITIVE lives in the generic manager
(``BaseDatabaseManager.try_acquire_lock``) — PG/MySQL claim in a single
statement, ClickHouse is advisory with a read-back tie-break (see the manager
docstrings for the per-backend contract).

Lock grain: ``(experiment, scope, process_type)``. The default whole-pipeline
lock is ``scope="pipeline"``; the key shape reserves per-metric scopes for
later parallelism (cumulative-intervals.md §5.7).
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime
from typing import Any

import numpy as np

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_TASKS
from abkit.utils.datetime_utils import now_utc_naive, to_naive_utc

DEFAULT_SCOPE = "pipeline"
DEFAULT_PROCESS_TYPE = "run"


def _make_owner_token() -> str:
    """Owner token: unique per claim attempt (host:pid:nonce).

    Required by the ClickHouse advisory claim's read-back verification; also
    makes ``check_lock`` output human-attributable.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class _TasksMixin(_InternalTablesBase):
    def acquire_lock(
        self,
        experiment: str,
        scope: str = DEFAULT_SCOPE,
        process_type: str = DEFAULT_PROCESS_TYPE,
        timeout_seconds: int = 3600,
        force: bool = False,
    ) -> bool:
        """Try to acquire the task lock; return False if it's actively held.

        A ``running`` row whose age exceeds ``timeout_seconds`` is treated as
        stale and overridden — the owning process likely died without
        releasing the lock (e.g. the database restarted mid-run), and a hung
        row must never block future runs.

        With ``force=True`` the running-status check is skipped entirely and
        the lock is taken unconditionally (synchronous replace); the row is
        still (re)written as ``running`` so the forced run owns the lock and
        releases it on exit.
        """
        row = self._task_row(
            experiment, scope, process_type, status="running", timeout_seconds=timeout_seconds
        )
        full_table_name = self._manager.get_full_table_name(TABLE_TASKS, use_internal=True)
        key_columns = {"experiment": experiment, "scope": scope, "process_type": process_type}

        if force:
            self._manager.upsert_record(
                full_table_name, key_columns, self._row_arrays(row), sync=True
            )
            return True

        return self._manager.try_acquire_lock(
            full_table_name,
            key_columns,
            row,
            status_column="status",
            running_value="running",
            heartbeat_column="started_at",
            timeout_seconds=timeout_seconds,
            token_column="locked_by",
        )

    def clear_lock(
        self,
        experiment: str,
        scope: str = DEFAULT_SCOPE,
        process_type: str = DEFAULT_PROCESS_TYPE,
    ) -> bool:
        """Force-release a (possibly stale) lock; return True if one was held.

        Used by ``abk unlock`` to recover from a hung run that left a
        ``running`` row behind. The age check is ignored so even a not-yet-
        stale lock is cleared. Marks the task ``completed`` so future runs
        proceed without ``--force``.
        """
        existing = self.check_lock(experiment, scope, process_type, ignore_timeout=True)
        if existing is None:
            return False

        self.release_lock(experiment, scope, process_type, status="completed")
        return True

    def release_lock(
        self,
        experiment: str,
        scope: str,
        process_type: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """Mark the task as ``completed`` or ``failed``.

        Implemented as a synchronous full replace (delete by key + insert), so
        on ClickHouse it also sweeps any loser rows a racing advisory claim
        left behind.
        """
        row = self._task_row(
            experiment, scope, process_type, status=status, error_message=error_message
        )
        full_table_name = self._manager.get_full_table_name(TABLE_TASKS, use_internal=True)
        self._manager.upsert_record(
            full_table_name,
            {"experiment": experiment, "scope": scope, "process_type": process_type},
            self._row_arrays(row),
            sync=True,
        )

    def check_lock(
        self,
        experiment: str,
        scope: str = DEFAULT_SCOPE,
        process_type: str = DEFAULT_PROCESS_TYPE,
        ignore_timeout: bool = False,
    ) -> dict | None:
        """Return an active running-task row, or ``None`` if no lock is active.

        A ``running`` row whose age (``now - started_at``) exceeds its stored
        ``timeout_seconds`` is considered stale and reported as released
        (returns ``None``), so a hung process never blocks future runs. Pass
        ``ignore_timeout=True`` to get the raw running row regardless of age
        (used by ``abk unlock`` to detect and report even stale locks).

        Scans ALL rows for the key (a ClickHouse advisory race can leave more
        than one): any live running row means locked.
        """
        full_table_name = self._manager.get_full_table_name(TABLE_TASKS, use_internal=True)
        query = f"""
        SELECT *
        FROM {full_table_name}
        WHERE experiment = %(experiment)s
          AND scope = %(scope)s
          AND process_type = %(process_type)s
          AND status = 'running'
        """
        results = self._manager.execute_query(
            query,
            {"experiment": experiment, "scope": scope, "process_type": process_type},
        )
        if not results:
            return None

        if ignore_timeout:
            return results[0]

        now = now_utc_naive()
        for row in results:
            started_at = to_naive_utc(row.get("started_at"))
            timeout_seconds = row.get("timeout_seconds")
            if started_at is None or timeout_seconds is None:
                return row
            if (now - started_at).total_seconds() <= timeout_seconds:
                return row
        # Every running row is stale: the owners never released. Treat as free.
        return None

    # ── row assembly ─────────────────────────────────────────────────────────

    @staticmethod
    def _task_row(
        experiment: str,
        scope: str,
        process_type: str,
        status: str,
        timeout_seconds: int = 3600,
        error_message: str | None = None,
        started_at: datetime | None = None,
    ) -> dict[str, Any]:
        now = now_utc_naive()
        return {
            "experiment": experiment,
            "scope": scope,
            "process_type": process_type,
            "status": status,
            "started_at": started_at or now,
            "updated_at": now,
            "locked_by": _make_owner_token(),
            "error_message": error_message,
            "timeout_seconds": timeout_seconds,
        }

    @staticmethod
    def _row_arrays(row: dict[str, Any]) -> dict[str, np.ndarray]:
        return {col: np.array([value], dtype=object) for col, value in row.items()}
