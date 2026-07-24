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


#: ``source_table`` column budget (``tables.get_unit_state_table_model``) —
#: a MySQL VARCHAR overflow would silently truncate-and-merge two series
_SOURCE_TABLE_MAX_LENGTH = 128


def compute_state_source_id(experiment: str, metric_name: str) -> str:
    """The v1 state-series ``source_table`` key: ``"{experiment}/{metric}"``.

    m9 WP3 deliberately narrows §5.3's source-table-sharing ideal (recorded in
    m9-implementation-plan.md §8 Q1): the per-day render joins THIS
    experiment's cohort with the exposure filter applied, so the moments are
    cohort-dependent and the series must be scoped per (experiment, metric) —
    two experiments sharing a metric would otherwise clobber each other
    through replace-not-sum. ``/`` cannot appear in either validated name, so
    the composite never collides; an overlong composite keeps a readable
    prefix and appends a hash tail to stay inside the column budget.
    """
    composite = f"{experiment}/{metric_name}"
    if len(composite) <= _SOURCE_TABLE_MAX_LENGTH:
        return composite
    digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:16]
    return f"{composite[:_SOURCE_TABLE_MAX_LENGTH - 17]}#{digest}"


def compute_metric_state_id(
    column_roles: dict[str, str],
    metric_sql: str,
    cohort_config: dict[str, Any] | None = None,
) -> str:
    """State-series identity of a metric's (role map, SQL body) — 16 hex chars.

    The m9 WP3 metric-hash invalidation: editing the SQL body (not just the
    column roles) must orphan stale day state — metrics have no
    ``method_config_id`` analogue, so this hash introduces that mechanism.
    The SQL text is whitespace-normalized first, so reformatting alone never
    orphans a series.

    ``cohort_config`` folds the cohort-shaping experiment config into the
    identity (an R1 review fix): the day render joins the experiment's
    cohort, so an edit that reshapes cohort membership (the assignment SQL,
    ``added_filters``, ``unit_key``, ``variants``, ``timezone``,
    ``start_date``) must orphan the series exactly like a metric-SQL edit —
    a merged series would otherwise mix two cohort definitions across days,
    an inconsistency the full-window recompute path can never have.
    """
    normalized = " ".join(metric_sql.split())
    sql_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    payload = json_dumps_sorted(
        {
            "columns": column_roles,
            "metric_sql_sha256": sql_hash,
            "cohort": cohort_config or {},
        }
    )
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

    def list_state_column_sets(self, source_table: str) -> list[str]:
        """Distinct ``column_set_id`` series stored under one source key.

        The m9 WP3 orphan sweep reads this to find series whose identity a
        metric-SQL edit superseded (deleted via :meth:`delete_state_series`
        so a future reader can never sum a stale definition).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT DISTINCT column_set_id FROM {full_table_name}"
            f"{self._manager.final_modifier} WHERE source_table = %(s)s",
            {"s": source_table},
        )
        return sorted(row["column_set_id"] for row in rows)

    def delete_state_series(self, source_table: str, column_set_id: str) -> None:
        """Drop one whole state series (orphan cleanup / ``--resync-cohort``)."""
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        self._manager.delete_rows(
            full_table_name,
            "source_table = %(s)s AND column_set_id = %(c)s",
            {"s": source_table, "c": column_set_id},
            sync=True,
        )

    def delete_state_days_from(self, source_table: str, column_set_id: str, from_day: date) -> None:
        """Truncate a state series from ``from_day`` (inclusive) onward.

        The m9 WP3 tail truncation: keeps every earlier day intact so
        ``get_last_state_day`` falls back to the last still-valid day and the
        contiguity invariant (every day <= it is materialized) survives both
        a full-refresh restart and the non-finite bailout.
        """
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        self._manager.delete_rows(
            full_table_name,
            "source_table = %(s)s AND column_set_id = %(c)s AND day >= %(d)s",
            {"s": source_table, "c": column_set_id, "d": from_day},
            sync=True,
        )

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
