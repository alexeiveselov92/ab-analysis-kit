"""Composite :class:`InternalTablesManager` assembled from per-table mixins."""

from __future__ import annotations

from abkit.database.internal_tables._aa_runs import _AaRunsMixin
from abkit.database.internal_tables._experiments import _ExperimentsMixin
from abkit.database.internal_tables._exposures import _ExposuresMixin
from abkit.database.internal_tables._maintenance import _MaintenanceMixin
from abkit.database.internal_tables._results import _ResultsMixin
from abkit.database.internal_tables._schema import _SchemaMixin
from abkit.database.internal_tables._tasks import _TasksMixin
from abkit.database.internal_tables._unit_state import _UnitStateMixin


class InternalTablesManager(
    _SchemaMixin,
    _ExperimentsMixin,
    _ExposuresMixin,
    _UnitStateMixin,
    _ResultsMixin,
    _AaRunsMixin,
    _TasksMixin,
    _MaintenanceMixin,
):
    """High-level façade over a :class:`BaseDatabaseManager` for ``_ab_*`` tables.

    The class itself adds no behaviour; each mixin owns the methods for
    one logical table. Splitting them keeps every file small and makes it
    obvious where to look when tracking down a bug.
    """
