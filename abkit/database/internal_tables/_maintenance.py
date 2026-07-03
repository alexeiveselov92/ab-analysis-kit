"""Maintenance mixin: cross-table cleanup helpers for ``abk clean``.

These support pruning data left behind when an analyst edits experiment
configs — most importantly removing all rows for an experiment whose YAML no
longer exists in the project. They are used only by the ``abk clean`` CLI
command, never by the run pipeline.

Deliberate exclusions from the experiment purge:

- ``_ab_aa_runs`` — audit trail, kept forever.
- ``_ab_unit_state`` — keyed by (source_table, column_set_id), NOT by
  experiment (cumulative-intervals.md §5.3): state rows are shared across
  experiments reading the same fact source, so no experiment owns them.
"""

from __future__ import annotations

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import (
    TABLE_EXPERIMENTS,
    TABLE_EXPOSURES,
    TABLE_RESULTS,
    TABLE_TASKS,
)

#: tables keyed by ``experiment`` (a removed experiment orphans rows in each)
EXPERIMENT_KEYED_TABLES: tuple[str, ...] = (
    TABLE_EXPERIMENTS,
    TABLE_EXPOSURES,
    TABLE_RESULTS,
    TABLE_TASKS,
)


class _MaintenanceMixin(_InternalTablesBase):
    def list_known_experiments(self) -> set[str]:
        """Return every ``experiment`` that has rows in any internal table.

        Unions ``SELECT DISTINCT experiment`` across all experiment-keyed
        tables so an experiment is reported even if it only ever loaded
        exposures (and thus never wrote a result).
        """
        names: set[str] = set()
        for table in EXPERIMENT_KEYED_TABLES:
            full_table_name = self._manager.get_full_table_name(table, use_internal=True)
            query = f"SELECT DISTINCT experiment FROM {full_table_name}"
            result = self._manager.execute_query(query)
            names.update(row["experiment"] for row in result if row.get("experiment"))
        return names

    def count_experiment_rows(self, experiment: str) -> dict[str, int]:
        """Return per-table row counts for *experiment* (for dry-run reports)."""
        counts: dict[str, int] = {}
        for table in EXPERIMENT_KEYED_TABLES:
            full_table_name = self._manager.get_full_table_name(table, use_internal=True)
            query = f"SELECT count(*) AS cnt FROM {full_table_name} WHERE experiment = %(e)s"
            result = self._manager.execute_query(query, {"e": experiment})
            counts[table] = int(result[0]["cnt"]) if result else 0
        return counts

    def purge_experiment(self, experiment: str) -> None:
        """Delete every row for *experiment* across experiment-keyed tables.

        Each delete is issued synchronously (``sync=True``) so the purge is
        fully applied when this returns. ``_ab_aa_runs`` (audit) and
        ``_ab_unit_state`` (not experiment-owned) are deliberately untouched.
        """
        for table in EXPERIMENT_KEYED_TABLES:
            full_table_name = self._manager.get_full_table_name(table, use_internal=True)
            self._manager.delete_rows(
                full_table_name, "experiment = %(e)s", {"e": experiment}, sync=True
            )
