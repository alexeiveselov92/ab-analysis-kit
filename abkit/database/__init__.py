"""Database managers for abkit.

The generic, ``table_name``-keyed manager layer (CLAUDE.md invariant: ``_ab_*``
semantics live in ``internal_tables/``, never here). Internal tables and the
greenfield ``_ab_*`` schema land with the WP3 work package.
"""

from abkit.database._sql_manager import SQLDatabaseManager
from abkit.database.clickhouse_manager import ClickHouseDatabaseManager
from abkit.database.manager import BaseDatabaseManager
from abkit.database.mysql_manager import MySQLDatabaseManager
from abkit.database.postgres_manager import PostgresDatabaseManager

__all__ = [
    "BaseDatabaseManager",
    "ClickHouseDatabaseManager",
    "MySQLDatabaseManager",
    "PostgresDatabaseManager",
    "SQLDatabaseManager",
]
