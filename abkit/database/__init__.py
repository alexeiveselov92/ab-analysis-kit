"""Database managers for abkit.

The generic, ``table_name``-keyed manager layer plus the internal ``_ab_*``
tables (CLAUDE.md invariant: ``_ab_*`` semantics live in ``internal_tables/``,
never in the base managers).
"""

from abkit.database._sql_manager import SQLDatabaseManager
from abkit.database.clickhouse_manager import ClickHouseDatabaseManager
from abkit.database.internal_tables import InternalTablesManager, compute_column_set_id
from abkit.database.manager import BaseDatabaseManager
from abkit.database.mysql_manager import MySQLDatabaseManager
from abkit.database.postgres_manager import PostgresDatabaseManager
from abkit.database.tables import (
    INTERNAL_TABLES,
    TABLE_AA_RUNS,
    TABLE_EXPERIMENTS,
    TABLE_EXPOSURES,
    TABLE_RESULTS,
    TABLE_TASKS,
    TABLE_UNIT_STATE,
    get_aa_runs_table_model,
    get_experiments_table_model,
    get_exposures_table_model,
    get_results_table_model,
    get_tasks_table_model,
    get_unit_state_table_model,
)

__all__ = [
    "INTERNAL_TABLES",
    "BaseDatabaseManager",
    "ClickHouseDatabaseManager",
    "InternalTablesManager",
    "MySQLDatabaseManager",
    "PostgresDatabaseManager",
    "SQLDatabaseManager",
    "TABLE_AA_RUNS",
    "TABLE_EXPERIMENTS",
    "TABLE_EXPOSURES",
    "TABLE_RESULTS",
    "TABLE_TASKS",
    "TABLE_UNIT_STATE",
    "compute_column_set_id",
    "get_aa_runs_table_model",
    "get_experiments_table_model",
    "get_exposures_table_model",
    "get_results_table_model",
    "get_tasks_table_model",
    "get_unit_state_table_model",
]
