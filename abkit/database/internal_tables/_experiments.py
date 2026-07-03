"""Experiment catalog mixin: ``_ab_experiments`` operations.

INFORMATIONAL table (BI joins descriptions/metadata from here; the pipeline
never reads it back for decisions). Callers pass a fully-prepared flat record
— JSON-typed fields (cadence, variants, expected_split, comparisons, tags)
must already be canonical-JSON strings via ``json_dumps_sorted``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_EXPERIMENTS
from abkit.utils.datetime_utils import now_utc_naive


class _ExperimentsMixin(_InternalTablesBase):
    #: record keys the caller must supply (created_at/updated_at are stamped here)
    _EXPERIMENT_FIELDS = (
        "experiment",
        "description",
        "status",
        "is_actual",
        "start_date",
        "end_date",
        "unit_key",
        "cadence",
        "data_lag_seconds",
        "timezone",
        "variants",
        "expected_split",
        "alpha",
        "correction",
        "sequential_enabled",
        "sequential_scheme",
        "comparisons",
        "path",
        "tags",
    )

    def upsert_experiment(self, record: dict[str, Any]) -> None:
        """Replace the catalog row for ``record["experiment"]``.

        ``created_at`` is preserved from an existing row (first-seen time);
        ``updated_at`` is stamped now.
        """
        missing = [f for f in self._EXPERIMENT_FIELDS if f not in record]
        if missing:
            raise ValueError(f"experiment record is missing fields: {missing}")

        existing = self.get_experiment(record["experiment"])
        now = now_utc_naive()
        created_at = existing["created_at"] if existing else now

        full_record = {f: record[f] for f in self._EXPERIMENT_FIELDS}
        full_record["created_at"] = created_at
        full_record["updated_at"] = now

        full_table_name = self._manager.get_full_table_name(TABLE_EXPERIMENTS, use_internal=True)
        data = {col: np.array([value], dtype=object) for col, value in full_record.items()}
        self._manager.upsert_record(full_table_name, {"experiment": record["experiment"]}, data)

    def get_experiment(self, experiment: str) -> dict | None:
        """Return the catalog row for *experiment*, or None."""
        full_table_name = self._manager.get_full_table_name(TABLE_EXPERIMENTS, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT * FROM {full_table_name} WHERE experiment = %(e)s",
            {"e": experiment},
        )
        return rows[0] if rows else None

    def list_experiments(self) -> list[dict]:
        """Return every catalog row (for ``abk clean --orphaned-experiments``)."""
        full_table_name = self._manager.get_full_table_name(TABLE_EXPERIMENTS, use_internal=True)
        return self._manager.execute_query(f"SELECT * FROM {full_table_name}")
