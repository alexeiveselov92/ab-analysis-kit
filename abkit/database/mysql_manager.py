"""MySQL database manager implementation.

Implements :class:`BaseDatabaseManager` for MySQL on top of the shared
:class:`SQLDatabaseManager`. MySQL has no schema-vs-database distinction, so the
internal/data *locations* are real **databases** (``CREATE DATABASE IF NOT
EXISTS``). Dedup for the ``ReplacingMergeTree`` tables is reproduced with an
enforced primary key plus a version-aware ``INSERT ... ON DUPLICATE KEY UPDATE``
(row-alias form, MySQL 8.0.19+ — the documented floor).

MySQL cannot index ``TEXT`` columns in a primary key without a prefix length, so
``String`` columns that are part of the primary key are rendered as
``VARCHAR(255)`` while the rest stay ``TEXT``. Composite ``_ab_*`` keys must fit
InnoDB's 3072-byte index cap — size key columns deliberately.

The lock claim is a SINGLE row-alias ``INSERT ... ON DUPLICATE KEY UPDATE``
whose claim condition is latched into a session variable by the FIRST assignment
(SET clauses evaluate left to right), so every column sees the SAME verdict —
computed against the pre-update row — and the claim is atomic. Affected-rows
semantics: 1 = inserted (claimed), 2 = updated (claimed), 0 = left untouched
(held by a live owner).
"""

from __future__ import annotations

from typing import Any

from abkit.database._sql_manager import SQLDatabaseManager

try:
    import pymysql  # type: ignore[import-untyped]

    PYMYSQL_AVAILABLE = True
except ImportError:
    PYMYSQL_AVAILABLE = False


class MySQLDatabaseManager(SQLDatabaseManager):
    """MySQL implementation of :class:`BaseDatabaseManager`.

    Args:
        host: MySQL host.
        port: MySQL port (default 3306).
        user: Database user.
        password: Database password.
        internal_database: Database for internal ``_ab_*`` tables.
        data_database: Database for user data tables.
        database: Optional default database for the connection.
        settings: Extra ``pymysql.connect`` keyword arguments.
    """

    _TYPE_MAP = {
        "datetime": "DATETIME(3)",
        "date": "DATE",
        "float": "DOUBLE",
        "int": "INT",
        "bool": "TINYINT(1)",
        "string": "TEXT",
    }

    # MySQL quotes identifiers with backticks (``interval`` etc. are reserved).
    _IDENT_QUOTE = "`"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        internal_database: str = "abkit",
        data_database: str = "analytics",
        database: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        if not PYMYSQL_AVAILABLE:
            raise ImportError(
                "pymysql is not installed. Install with: pip install ab-analysis-kit[mysql]"
            )
        super().__init__(
            host=host,
            port=port,
            user=user,
            password=password,
            internal_location=internal_database,
            data_location=data_database,
            database=database,
            settings=settings,
        )

    def _connect(self) -> Any:
        return pymysql.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            database=self._database,
            charset="utf8mb4",
            **self._settings,
        )

    def _ensure_locations(self) -> None:
        for db in (self._internal_location, self._data_location):
            self.execute_query(f"CREATE DATABASE IF NOT EXISTS {db}")

    def _string_type(self, in_primary_key: bool) -> str:
        # TEXT cannot be part of a PRIMARY KEY without a prefix length.
        return "VARCHAR(255)" if in_primary_key else "TEXT"

    def _build_insert_sql(
        self,
        table_name: str,
        columns: list[str],
        primary_key: list[str],
        version_column: str | None,
        conflict_strategy: str,
    ) -> str:
        placeholders = ", ".join(["%s"] * len(columns))
        cols = ", ".join(self._q(c) for c in columns)
        plain = f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})"

        if conflict_strategy == "fail" or not primary_key:
            return plain

        non_pk = [c for c in columns if c not in primary_key]
        if conflict_strategy == "ignore" and (version_column is None or not non_pk):
            return f"INSERT IGNORE INTO {table_name} ({cols}) VALUES ({placeholders})"

        # Row-alias form (MySQL 8.0.19+) avoids the deprecated VALUES() function.
        # Existing-row references are qualified with the table name: with the
        # `AS new` alias in scope, an unqualified column in the UPDATE expression
        # is ambiguous ("Column '…' in field list is ambiguous").
        aliased = f"{plain} AS new"
        tbl = self._q(table_name.split(".")[-1])
        if conflict_strategy == "replace":
            sets = ", ".join(f"{self._q(c)} = new.{self._q(c)}" for c in non_pk)
        else:  # versioned "ignore" -> last-writer-wins by version column
            ver = self._q(version_column) if version_column else None
            sets = ", ".join(
                f"{self._q(c)} = IF(new.{ver} >= {tbl}.{ver}, new.{self._q(c)}, {tbl}.{self._q(c)})"
                for c in non_pk
            )
        return f"{aliased} ON DUPLICATE KEY UPDATE {sets}"

    def _build_claim_sql(
        self,
        table_name: str,
        columns: list[str],
        primary_key: list[str],
        status_column: str,
        heartbeat_column: str,
    ) -> str:
        cols = ", ".join(self._q(c) for c in columns)
        placeholders = ", ".join(f"%({c})s" for c in columns)
        non_pk = [c for c in columns if c not in primary_key]
        tbl = self._q(table_name.split(".")[-1])
        # Latch the claim verdict into a session variable in the FIRST SET
        # clause: MySQL evaluates SET clauses left to right, so without the
        # latch a later IF would see columns already overwritten by an earlier
        # clause (e.g. a fresh heartbeat) and the row could be torn.
        condition = (
            f"@abk_claim := ({tbl}.{self._q(status_column)} <> %(_abk_running)s "
            f"OR {tbl}.{self._q(heartbeat_column)} < %(_abk_stale_before)s)"
        )
        first, *rest = non_pk
        sets = [f"{self._q(first)} = IF({condition}, new.{self._q(first)}, {tbl}.{self._q(first)})"]
        sets += [
            f"{self._q(c)} = IF(@abk_claim, new.{self._q(c)}, {tbl}.{self._q(c)})" for c in rest
        ]
        return (
            f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders}) AS new "
            f"ON DUPLICATE KEY UPDATE {', '.join(sets)}"
        )
