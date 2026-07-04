"""Results mixin: ``_ab_results`` operations — the BI contract's write/read path.

Write path: the enrich stage flattens ``TestResult`` rows and calls
:meth:`save_results`; the strictly-monotonic ``created_at`` LWW version is
stamped HERE (one distinct tick per row) so no caller can accidentally write
tying versions.

Read paths: the planner anti-join (:meth:`list_computed_cutoffs` — a SET
reader, not a cursor: the cumulative grid is not resumed, late/backfilled
cutoffs make ``max()`` insufficient), the ``abk clean`` drift scan
(:meth:`list_method_config_ids`), and result loading for reports/e2e.
Every correctness-sensitive read is deduped (FINAL on ClickHouse).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_RESULTS, get_results_table_model
from abkit.utils.datetime_utils import to_naive_utc

#: contract columns the enrich stage must supply (everything except created_at)
RESULT_COLUMNS: tuple[str, ...] = tuple(
    col.name for col in get_results_table_model().columns if col.name != "created_at"
)


class _ResultsMixin(_InternalTablesBase):
    def results_table_exists(self) -> bool:
        """True when ``_ab_results`` exists — a never-run project has none.

        Read-only surfaces (``abk run --report`` on a fresh project, explore)
        guard with this instead of ``ensure_tables()``: reporting must never
        create schema (m3-implementation-plan.md WP2).
        """
        return self._manager.table_exists(TABLE_RESULTS, schema=self._manager.internal_location)

    def save_results(self, data: dict[str, np.ndarray]) -> int:
        """Persist a batch of enriched result rows (LWW upsert semantics).

        ``data`` must contain exactly the contract columns (RESULT_COLUMNS);
        ``created_at`` is stamped here with one strictly-increasing distinct
        version per row (quorum must-fix).
        """
        missing = [c for c in RESULT_COLUMNS if c not in data]
        if missing:
            raise ValueError(f"result batch is missing contract columns: {missing}")
        extra = [c for c in data if c not in RESULT_COLUMNS]
        if extra:
            raise ValueError(f"result batch has unknown columns: {extra}")

        num_rows = len(data["experiment"])
        if num_rows == 0:
            return 0

        insert_data = dict(data)
        insert_data["created_at"] = np.array(
            [self.next_version_ts() for _ in range(num_rows)], dtype=object
        )

        full_table_name = self._manager.get_full_table_name(TABLE_RESULTS, use_internal=True)
        return self._manager.insert_batch(full_table_name, insert_data, conflict_strategy="ignore")

    def list_computed_cutoffs(
        self, experiment: str, metric: str, method_config_id: str
    ) -> set[datetime]:
        """The planner anti-join source: every ``end_ts`` already computed.

        Returns naive-UTC datetimes. A SET, not a max-cursor: a late hole in
        the middle of the grid must be re-planned (plan WP7).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_RESULTS, use_internal=True)
        query = f"""
        SELECT DISTINCT end_ts
        FROM {full_table_name}{self._manager.final_modifier}
        WHERE experiment = %(e)s
          AND metric = %(m)s
          AND method_config_id = %(mc)s
        """
        rows = self._manager.execute_query(
            query, {"e": experiment, "m": metric, "mc": method_config_id}
        )
        cutoffs: set[datetime] = set()
        for row in rows:
            ts = to_naive_utc(row.get("end_ts"))
            if ts is not None:
                cutoffs.add(ts)
        return cutoffs

    def list_method_config_ids(
        self, experiment: str, metric: str | None = None
    ) -> dict[tuple[str, str], int]:
        """``{(metric, method_config_id): row_count}`` stored for an experiment.

        The ``abk clean`` drift scan diffs this against the ids the current
        YAML produces (through the SAME MethodConfig.build path the pipeline
        stamps rows with); ``run``/``explore`` warn when a metric has more
        than one id (duplicate stabilization lines in BI).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_RESULTS, use_internal=True)
        where = "experiment = %(e)s"
        params: dict[str, Any] = {"e": experiment}
        if metric is not None:
            where += " AND metric = %(m)s"
            params["m"] = metric
        query = f"""
        SELECT metric, method_config_id, count(*) AS cnt
        FROM {full_table_name}{self._manager.final_modifier}
        WHERE {where}
        GROUP BY metric, method_config_id
        """
        rows = self._manager.execute_query(query, params)
        return {
            (row["metric"], row["method_config_id"]): int(row["cnt"])
            for row in rows
            if row.get("metric") and row.get("method_config_id")
        }

    def delete_results(
        self,
        experiment: str,
        metric: str | None = None,
        method_config_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        mutations_sync: bool = False,
    ) -> int:
        """Delete result rows for the supplied filter set.

        Used by ``abk clean`` (drift pruning; passes ``mutations_sync=True``
        so a follow-up dry-run reflects the deletion) and by
        ``run --full-refresh --from/--to`` (re-opening frozen cutoffs;
        ``[from_ts, to_ts)`` filters on ``end_ts``).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_RESULTS, use_internal=True)
        where_parts = ["experiment = %(experiment)s"]
        params: dict[str, Any] = {"experiment": experiment}
        if metric:
            where_parts.append("metric = %(metric)s")
            params["metric"] = metric
        if method_config_id:
            where_parts.append("method_config_id = %(method_config_id)s")
            params["method_config_id"] = method_config_id
        if from_ts:
            where_parts.append("end_ts >= %(from_ts)s")
            params["from_ts"] = from_ts
        if to_ts:
            where_parts.append("end_ts < %(to_ts)s")
            params["to_ts"] = to_ts

        return self._manager.delete_rows(
            full_table_name, " AND ".join(where_parts), params, sync=mutations_sync
        )

    def load_results(
        self,
        experiment: str,
        metric: str | None = None,
        method_config_id: str | None = None,
    ) -> list[dict]:
        """Load result rows ascending by ``end_ts`` (deduped).

        Timestamps are normalised to naive UTC. Serves reports and the e2e
        byte-stability assertions; BI reads the table directly.
        """
        full_table_name = self._manager.get_full_table_name(TABLE_RESULTS, use_internal=True)
        where_parts = ["experiment = %(experiment)s"]
        params: dict[str, Any] = {"experiment": experiment}
        if metric:
            where_parts.append("metric = %(metric)s")
            params["metric"] = metric
        if method_config_id:
            where_parts.append("method_config_id = %(method_config_id)s")
            params["method_config_id"] = method_config_id

        query = f"""
        SELECT *
        FROM {full_table_name}{self._manager.final_modifier}
        WHERE {" AND ".join(where_parts)}
        ORDER BY metric, name_1, name_2, method_config_id, end_ts
        """
        rows = self._manager.execute_query(query, params)
        for row in rows:
            for ts_col in ("start_ts", "end_ts", "watermark_ts", "created_at"):
                if row.get(ts_col) is not None:
                    row[ts_col] = to_naive_utc(row[ts_col])
        return rows
