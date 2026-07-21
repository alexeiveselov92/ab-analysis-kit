"""Schema management mixin (creates internal tables on demand)."""

from __future__ import annotations

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import INTERNAL_TABLES


class _SchemaMixin(_InternalTablesBase):
    def ensure_tables(self) -> None:
        """Create every internal ``_ab_*`` table that doesn't exist yet, and
        additively sync existing ones to the current model (M9 WP1).

        Idempotent: safe to call on every CLI invocation. An existing table
        goes through ``ensure_columns`` — the additive-only migration
        primitive that ALTERs in any column the model has gained since the
        table was created (never drops/renames), so a post-release schema
        addition upgrades installed projects on their next run instead of
        breaking the insert path's column-mismatch check.
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
            else:
                self._manager.ensure_columns(full_table_name, table_model)
