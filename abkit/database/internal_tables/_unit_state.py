"""Unit-state mixin: ``_ab_unit_state`` operations (the scalability seam).

v1 is a THIN materialization — the compute read path stays recompute — but the
two invariants that would be silent corruption by the time v2 flips the read
path are enforced NOW (cumulative-intervals.md §5.2/§5.3):

1. **Idempotent per (source, column-set, day)**: replace-not-sum. Writing a
   day twice leaves aggregates unchanged (the twice-run invariant test).
2. **Cardinality key** ``(source_table, column_set_id, unit_id, day)`` — NOT
   per (experiment, metric) — so co-located metrics sharing a fact source
   share one set of per-unit moments. State rows are deliberately
   experiment-independent: the exposure join happens at read time.

The state stage advances only at day close (§6.4); sub-day cutoffs read
closed-day state plus a current-day fact tail.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import numpy as np

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_UNIT_STATE
from abkit.utils.json_utils import json_dumps_sorted

#: moment columns in table order; absent moments must be written as 0.0
MOMENT_COLUMNS = (
    "n",
    "sum_value",
    "sum_value_sq",
    "sum_cov",
    "sum_cov_sq",
    "sum_value_cov",
    "sum_denominator",
    "sum_denominator_sq",
    "sum_value_denominator",
)


def compute_column_set_id(source_table: str, column_roles: dict[str, str]) -> str:
    """Identity of a (source table, column-role set) pair — 16 hex chars.

    Two metrics reading the same columns of the same fact table share one
    state series (§5.3). Hashed over the canonical JSON of the source table
    plus the role->column mapping so identity survives dict ordering.
    """
    payload = json_dumps_sorted({"source_table": source_table, "columns": column_roles})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class _UnitStateMixin(_InternalTablesBase):
    def replace_day_state(
        self,
        source_table: str,
        column_set_id: str,
        day: date,
        data: dict[str, np.ndarray],
    ) -> int:
        """Replace one closed day's per-unit moments (replace-not-sum, §5.2).

        Synchronously deletes every row for ``(source_table, column_set_id,
        day)`` then inserts the new batch, so a re-run/backfill/lost-lock
        retry can never double-count. ``data`` must contain ``unit_id`` plus
        any subset of :data:`MOMENT_COLUMNS` (missing moments are written as
        zeros); the ``version`` is stamped here.
        """
        if "unit_id" not in data:
            raise ValueError("unit state data must contain a unit_id column")
        unknown = [c for c in data if c != "unit_id" and c not in MOMENT_COLUMNS]
        if unknown:
            raise ValueError(f"unknown unit-state moment columns: {unknown}")

        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        self._manager.delete_rows(
            full_table_name,
            "source_table = %(s)s AND column_set_id = %(c)s AND day = %(d)s",
            {"s": source_table, "c": column_set_id, "d": day},
            sync=True,
        )

        num_rows = len(data["unit_id"])
        if num_rows == 0:
            return 0

        version = self.next_version_ts()
        insert_data: dict[str, np.ndarray] = {
            "source_table": np.full(num_rows, source_table, dtype=object),
            "column_set_id": np.full(num_rows, column_set_id, dtype=object),
            "unit_id": data["unit_id"],
            "day": np.full(num_rows, day, dtype=object),
        }
        for moment in MOMENT_COLUMNS:
            if moment in data:
                insert_data[moment] = data[moment]
            elif moment == "n":
                insert_data[moment] = np.zeros(num_rows, dtype=np.int64)
            else:
                insert_data[moment] = np.zeros(num_rows, dtype=np.float64)
        insert_data["version"] = np.full(num_rows, version, dtype=object)

        return self._manager.insert_batch(full_table_name, insert_data, conflict_strategy="ignore")

    def sum_moments(
        self,
        source_table: str,
        column_set_id: str,
        from_day: date,
        to_day: date,
    ) -> dict[str, float]:
        """Aggregate moments over ``[from_day, to_day]`` (both inclusive).

        Deduped (FINAL on ClickHouse) so replace-not-sum versions never
        double-count mid-merge — the read side of the §5.2 invariant, and the
        assertion surface for the twice-run test. v1 uses this only in tests;
        the v2 incremental backend will read per-unit rows.
        """
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        select = ", ".join(f"sum({m}) AS {m}" for m in MOMENT_COLUMNS)
        rows = self._manager.execute_query(
            f"SELECT {select} FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE source_table = %(s)s AND column_set_id = %(c)s "
            "AND day >= %(from)s AND day <= %(to)s",
            {"s": source_table, "c": column_set_id, "from": from_day, "to": to_day},
        )
        if not rows:
            return dict.fromkeys(MOMENT_COLUMNS, 0.0)
        row: dict[str, Any] = rows[0]
        return {m: float(row[m]) if row.get(m) is not None else 0.0 for m in MOMENT_COLUMNS}

    def get_last_state_day(self, source_table: str, column_set_id: str) -> date | None:
        """Latest closed day materialized for this state series."""
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT max(day) AS last_day FROM {full_table_name} "
            "WHERE source_table = %(s)s AND column_set_id = %(c)s",
            {"s": source_table, "c": column_set_id},
        )
        if not rows or rows[0].get("last_day") is None:
            return None
        last_day = rows[0]["last_day"]
        # ClickHouse returns the epoch date for an empty max(); normalise.
        if last_day == date(1970, 1, 1):
            return None
        return last_day
