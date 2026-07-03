"""PostgreSQL database manager implementation.

Implements :class:`BaseDatabaseManager` for PostgreSQL on top of the shared
:class:`SQLDatabaseManager`. The internal/data *locations* are PostgreSQL
**schemas** inside a single connected database; abkit creates the schemas
(``CREATE SCHEMA IF NOT EXISTS``) but the database itself must already exist.
Dedup for the ``ReplacingMergeTree`` tables is reproduced with an enforced
primary key plus a version-aware ``INSERT ... ON CONFLICT DO UPDATE``.

The lock claim is a SINGLE statement (``INSERT ... ON CONFLICT (pk) DO UPDATE
... WHERE <not running or stale>``), so two concurrent claimers serialize on
the row and exactly one observes an affected row — the quorum "atomic lock"
must-fix, with no TOCTOU window.
"""

from __future__ import annotations

from typing import Any

from abkit.database._sql_manager import SQLDatabaseManager

try:
    import psycopg2  # type: ignore[import-untyped]

    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


class PostgresDatabaseManager(SQLDatabaseManager):
    """PostgreSQL implementation of :class:`BaseDatabaseManager`.

    Args:
        host: PostgreSQL host.
        port: PostgreSQL port (default 5432).
        user: Database user.
        password: Database password.
        database: Database to connect to (must already exist).
        internal_schema: Schema for internal ``_ab_*`` tables.
        data_schema: Schema for user data tables.
        settings: Extra ``psycopg2.connect`` keyword arguments.
    """

    _TYPE_MAP = {
        "datetime": "TIMESTAMP(3)",
        "date": "DATE",
        "float": "DOUBLE PRECISION",
        "int": "INTEGER",
        "bool": "BOOLEAN",
        "string": "TEXT",
    }

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        user: str = "postgres",
        password: str = "",
        database: str = "postgres",
        internal_schema: str = "abkit",
        data_schema: str = "public",
        settings: dict[str, Any] | None = None,
    ) -> None:
        if not PSYCOPG2_AVAILABLE:
            raise ImportError(
                "psycopg2 is not installed. Install with: pip install ab-analysis-kit[postgres]"
            )
        super().__init__(
            host=host,
            port=port,
            user=user,
            password=password,
            internal_location=internal_schema,
            data_location=data_schema,
            database=database,
            settings=settings,
        )

    def _connect(self) -> Any:
        return psycopg2.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            dbname=self._database,
            **self._settings,
        )

    def _ensure_locations(self) -> None:
        for schema in (self._internal_location, self._data_location):
            self.execute_query(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    def _build_insert_sql(
        self,
        table_name: str,
        columns: list[str],
        primary_key: list[str],
        version_column: str | None,
        conflict_strategy: str,
    ) -> str:
        placeholders = ", ".join(["%s"] * len(columns))
        col_list = ", ".join(self._q(c) for c in columns)
        base = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"

        if conflict_strategy == "fail" or not primary_key:
            return base

        pk_target = f"ON CONFLICT ({', '.join(self._q(c) for c in primary_key)})"
        if conflict_strategy == "ignore" and version_column is None:
            return f"{base} {pk_target} DO NOTHING"

        non_pk = [c for c in columns if c not in primary_key]
        if not non_pk:
            return f"{base} {pk_target} DO NOTHING"

        set_clause = ", ".join(f"{self._q(c)} = EXCLUDED.{self._q(c)}" for c in non_pk)
        stmt = f"{base} {pk_target} DO UPDATE SET {set_clause}"
        # Versioned "ignore" == last-writer-wins: only overwrite when the
        # incoming row's version is newer-or-equal (mirrors ReplacingMergeTree).
        if version_column is not None and conflict_strategy == "ignore":
            bare = table_name.split(".")[-1]
            ver = self._q(version_column)
            stmt += f" WHERE {bare}.{ver} <= EXCLUDED.{ver}"
        return stmt

    def _build_claim_sql(
        self,
        table_name: str,
        columns: list[str],
        primary_key: list[str],
        status_column: str,
        heartbeat_column: str,
    ) -> str:
        col_list = ", ".join(self._q(c) for c in columns)
        placeholders = ", ".join(f"%({c})s" for c in columns)
        non_pk = [c for c in columns if c not in primary_key]
        set_clause = ", ".join(f"{self._q(c)} = EXCLUDED.{self._q(c)}" for c in non_pk)
        bare = table_name.split(".")[-1]
        return (
            f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(self._q(c) for c in primary_key)}) "
            f"DO UPDATE SET {set_clause} "
            f"WHERE {bare}.{self._q(status_column)} <> %(_abk_running)s "
            f"OR {bare}.{self._q(heartbeat_column)} < %(_abk_stale_before)s"
        )
