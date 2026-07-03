"""Schema management mixin (creates internal tables on demand)."""

from __future__ import annotations

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import INTERNAL_TABLES


class _SchemaMixin(_InternalTablesBase):
    def ensure_tables(self) -> None:
        """Create every internal ``_ab_*`` table that doesn't exist yet.

        Idempotent: safe to call on every CLI invocation.
        """
        for table_name, model_factory in INTERNAL_TABLES.items():
            full_table_name = self._manager.get_full_table_name(table_name, use_internal=True)
            table_model = model_factory()
            # Always register the schema so the manager knows each table's primary
            # key / version column on the insert path — even on a fresh manager
            # whose tables already exist (so create_table below is skipped).
            self._manager.register_table(full_table_name, table_model)
            if not self._manager.table_exists(table_name, schema=self._manager.internal_location):
                self._manager.create_table(full_table_name, table_model, if_not_exists=True)
