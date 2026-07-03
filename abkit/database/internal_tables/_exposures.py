"""Exposures mixin: ``_ab_exposures`` operations.

The persisted assignment cohort — loaded ONCE per run by the exposure loader
(quorum must-fix "persist the cohort once"), JOINed by every metric query via
the packaged macro, and the SRM gate's count source. READ-ONLY for compute:
only ``replace_exposures`` (the loader) and ``purge_experiment`` write here.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_EXPOSURES

#: insert chunk size — bounds driver memory on multi-million-unit cohorts
EXPOSURE_INSERT_CHUNK = 100_000


class _ExposuresMixin(_InternalTablesBase):
    def replace_exposures(self, experiment: str, data: dict[str, np.ndarray]) -> int:
        """Replace the full cohort for *experiment*: sync delete + chunked insert.

        Idempotent per experiment (delete-then-insert keyed by experiment —
        plan R9): a re-run reloads the cohort from the assignment SQL and the
        SRM gate re-checks it. Returns the number of exposure rows written.

        ``data`` must contain ``unit_id``, ``variant``, ``exposure_ts`` and
        optionally ``stratum`` arrays (the exposure loader validates shapes);
        ``experiment`` and the ``loaded_at`` version are stamped here.
        """
        required = ("unit_id", "variant", "exposure_ts")
        missing = [c for c in required if c not in data]
        if missing:
            raise ValueError(f"exposure data is missing columns: {missing}")

        num_rows = len(data["unit_id"])
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)

        self._manager.delete_rows(
            full_table_name, "experiment = %(e)s", {"e": experiment}, sync=True
        )
        if num_rows == 0:
            return 0

        loaded_at = self.next_version_ts()
        stratum = data.get("stratum")
        if stratum is None:
            stratum = np.array([None] * num_rows, dtype=object)

        written = 0
        for start in range(0, num_rows, EXPOSURE_INSERT_CHUNK):
            end = min(start + EXPOSURE_INSERT_CHUNK, num_rows)
            chunk = {
                "experiment": np.full(end - start, experiment, dtype=object),
                "unit_id": data["unit_id"][start:end],
                "variant": data["variant"][start:end],
                "exposure_ts": data["exposure_ts"][start:end],
                "stratum": stratum[start:end],
                "loaded_at": np.full(end - start, loaded_at, dtype=object),
            }
            written += self._manager.insert_batch(
                full_table_name, chunk, conflict_strategy="ignore"
            )
        return written

    def exposures_table_exists(self) -> bool:
        """True when ``_ab_exposures`` exists — a never-run project has none.

        Read-only surfaces guard with this instead of ``ensure_tables()``;
        mirrors :meth:`results_table_exists` (m3-implementation-plan.md WP2).
        """
        return self._manager.table_exists(TABLE_EXPOSURES, schema=self._manager.internal_location)

    def get_exposure_counts(self, experiment: str, until: datetime | None = None) -> dict[str, int]:
        """Per-variant unit counts — the SRM gate's observed counts.

        Deduped (FINAL on ClickHouse) so a mid-merge ReplacingMergeTree never
        double-counts a unit (quorum "correctness under async merge").
        ``until`` bounds the cohort as-of a cutoff (``exposure_ts < until``,
        half-open like every window) so a replayed report shows the counts
        that existed then, not today's.
        """
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        until_filter = " AND exposure_ts < %(until)s" if until is not None else ""
        query = f"""
        SELECT variant, count(*) AS cnt
        FROM {full_table_name}{self._manager.final_modifier}
        WHERE experiment = %(e)s{until_filter}
        GROUP BY variant
        """
        params: dict[str, object] = {"e": experiment}
        if until is not None:
            params["until"] = until
        rows = self._manager.execute_query(query, params)
        return {row["variant"]: int(row["cnt"]) for row in rows if row.get("variant")}

    def get_first_exposure_ts(self, experiment: str) -> datetime | None:
        """Earliest exposure timestamp (diagnostics/plan)."""
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT min(exposure_ts) AS first_ts FROM {full_table_name} "
            "WHERE experiment = %(e)s",
            {"e": experiment},
        )
        if not rows:
            return None
        return self._normalize_max_timestamp(rows[0].get("first_ts"))

    def count_exposures(self, experiment: str) -> int:
        """Total cohort size (deduped)."""
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT count(*) AS cnt FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE experiment = %(e)s",
            {"e": experiment},
        )
        return int(rows[0]["cnt"]) if rows else 0
