"""Exposures mixin: ``_ab_exposures`` operations.

The persisted assignment cohort — loaded ONCE per run by the exposure loader
(quorum must-fix "persist the cohort once"), JOINed by every metric query via
the packaged macro, and the SRM gate's count source. READ-ONLY for compute:
only ``replace_exposures`` (the loader) and ``purge_experiment`` write here.
"""

from __future__ import annotations

import bisect
from datetime import datetime

import numpy as np

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_EXPOSURES
from abkit.utils.datetime_utils import to_naive_utc

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

    def get_exposure_counts(self, experiment: str) -> dict[str, int]:
        """Per-variant unit counts — the SRM gate's observed counts.

        Deduped (FINAL on ClickHouse) so a mid-merge ReplacingMergeTree never
        double-counts a unit (quorum "correctness under async merge"). Whole
        cohort by design: M2 SRM is a single whole-cohort check, so the report
        pairs these counts with that whole-run flag/pvalue (an as-of subset
        would mismatch it — review finding); per-cutoff SRM lands with
        sequential (M5).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        query = f"""
        SELECT variant, count(*) AS cnt
        FROM {full_table_name}{self._manager.final_modifier}
        WHERE experiment = %(e)s
        GROUP BY variant
        """
        rows = self._manager.execute_query(query, {"e": experiment})
        return {row["variant"]: int(row["cnt"]) for row in rows if row.get("variant")}

    def get_exposure_count_stream(
        self, experiment: str, boundaries: list[datetime], variants: list[str]
    ) -> list[dict[str, int]]:
        """Cumulative per-variant unit counts as-of each look boundary.

        The sub-day anytime-valid SRM (Lindon & Malek, cumulative-intervals.md
        §6.5) needs the running count history: for each half-open cumulative
        window ``[start, end_ts)`` the count of units first exposed BEFORE
        ``end_ts`` (exclusive, matching the metric-load windows). Reads the
        deduped persisted cohort ONCE and buckets in-process (one round trip,
        not one query per look — a sub-day grid has many looks); every variant
        in ``variants`` is zero-filled so a missing arm reads as 0 (the worst
        SRM). Returns one dict per boundary, aligned and ascending.

        (v1 reconstructs the full stream each run from ``_ab_exposures``, in
        keeping with the recompute-not-incremental read path; a bucketed-SQL or
        running-max-carry optimisation is a future v2.)
        """
        if not boundaries:
            return []
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT variant, exposure_ts FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE experiment = %(e)s",
            {"e": experiment},
        )
        # sorted exposure timestamps per declared variant; bisect_left(b) then
        # yields exactly the count with exposure_ts < b (the exclusive edge).
        per_variant: dict[str, list[datetime]] = {v: [] for v in variants}
        for row in rows:
            variant = row.get("variant")
            timestamps = per_variant.get(variant)
            if timestamps is None:
                continue  # a variant not declared this run — never counted
            ts = to_naive_utc(row.get("exposure_ts"))
            if ts is not None:
                timestamps.append(ts)
        for timestamps in per_variant.values():
            timestamps.sort()
        return [
            {v: bisect.bisect_left(per_variant[v], boundary) for v in variants}
            for boundary in boundaries
        ]

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
