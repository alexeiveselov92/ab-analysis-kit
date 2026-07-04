"""A/A runs mixin: ``_ab_aa_runs`` operations.

Thin in M2 — the writer is exercised by ``abk validate`` (M4). An audit
trail: informational, never read by the run pipeline, deliberately NOT pruned
by ``abk clean`` (aa-false-positive-matrix.md).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_AA_RUNS, get_aa_runs_table_model

#: record keys the caller must supply (created_at is stamped here)
AA_RUN_COLUMNS: tuple[str, ...] = tuple(
    col.name for col in get_aa_runs_table_model().columns if col.name != "created_at"
)


class _AaRunsMixin(_InternalTablesBase):
    def save_aa_run(self, record: dict[str, Any]) -> None:
        """Persist one A/A validation run row (``created_at`` stamped here)."""
        missing = [c for c in AA_RUN_COLUMNS if c not in record]
        if missing:
            raise ValueError(f"aa run record is missing fields: {missing}")

        full_record = {c: record[c] for c in AA_RUN_COLUMNS}
        full_record["created_at"] = self.next_version_ts()

        full_table_name = self._manager.get_full_table_name(TABLE_AA_RUNS, use_internal=True)
        data = {col: np.array([value], dtype=object) for col, value in full_record.items()}
        self._manager.insert_batch(full_table_name, data, conflict_strategy="ignore")

    def get_aa_runs(self, experiment: str) -> list[dict]:
        """All A/A runs for an experiment, newest first (deduped)."""
        full_table_name = self._manager.get_full_table_name(TABLE_AA_RUNS, use_internal=True)
        return self._manager.execute_query(
            f"SELECT * FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE experiment = %(e)s ORDER BY created_at DESC",
            {"e": experiment},
        )
