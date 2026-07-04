"""Shared base for SQL (PostgreSQL / MySQL) database managers.

Both PostgreSQL and MySQL are standard SQL engines with an *enforced* primary
key and native upsert, so they share almost all of the implementation. This
module owns the DB-API 2.0 flow once (connection, cursor → dict rows,
transactions, numpy → driver value coercion, DDL rendering, version-aware
upserts, the atomic lock claim) and exposes a handful of dialect hooks that the
two concrete backends override:

- ``_connect()`` — open the DB-API connection.
- ``_ensure_locations()`` — create the internal/data schema-or-database.
- ``_TYPE_MAP`` / ``_string_type()`` — map the abstract ``TableModel`` column
  types onto native column types.
- ``_build_insert_sql()`` — render the dialect's insert + conflict handling.
- ``_build_claim_sql()`` — render the dialect's single-statement lock claim.

The contract matches the ClickHouse backend's behaviour: versioned tables
(``version_column`` set, e.g. ``_ab_results``) get last-writer-wins semantics
via a version-aware upsert mirroring ``ReplacingMergeTree``; plain tables are
replaced transactionally via :meth:`upsert_record`.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from abkit.core.models import ColumnDefinition, TableModel
from abkit.database.manager import BaseDatabaseManager
from abkit.utils.datetime_utils import now_utc_naive

_EPOCH = np.datetime64("1970-01-01T00:00:00")
_ONE_SECOND = np.timedelta64(1, "s")


class SQLDatabaseManager(BaseDatabaseManager):
    """Base class for standard-SQL backends (PostgreSQL, MySQL).

    Args:
        host: Database host.
        port: Database port.
        user: Database user.
        password: Database password.
        internal_location: Schema (PostgreSQL) or database (MySQL) for the
            internal ``_ab_*`` tables.
        data_location: Schema (PostgreSQL) or database (MySQL) for user data.
        database: Connection-target database. Required for PostgreSQL (the
            database to connect to, inside which the schemas live); optional for
            MySQL (which addresses tables as ``database.table``).
        settings: Extra driver-specific connection options.
    """

    #: Maps the canonical column kind to a native SQL type. Subclasses override.
    _TYPE_MAP: dict[str, str] = {}

    #: Identifier quote character (double-quote for PostgreSQL, backtick MySQL).
    _IDENT_QUOTE = '"'

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        user: str = "",
        password: str = "",
        internal_location: str = "abkit",
        data_location: str = "public",
        database: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._internal_location = internal_location
        self._data_location = data_location
        self._database = database
        self._settings = settings or {}
        # bare table name -> (primary_key columns, version column) recorded at
        # create_table time and consulted by the insert path.
        self._table_meta: dict[str, tuple[list[str], str | None]] = {}

        self._conn: Any = self._connect()
        self._ensure_locations()

    # ── dialect hooks (subclasses override) ─────────────────────────────────

    def _connect(self) -> Any:
        """Open and return a DB-API 2.0 connection."""
        raise NotImplementedError

    def _ensure_locations(self) -> None:
        """Create the internal/data schema (PostgreSQL) or database (MySQL)."""
        raise NotImplementedError

    def _string_type(self, in_primary_key: bool, max_length: int | None = None) -> str:
        """Native type for an abstract ``String`` column."""
        return self._TYPE_MAP["string"]

    def _build_insert_sql(
        self,
        table_name: str,
        columns: list[str],
        primary_key: list[str],
        version_column: str | None,
        conflict_strategy: str,
    ) -> str:
        """Render the dialect's INSERT statement (single VALUES row template)."""
        raise NotImplementedError

    def _build_claim_sql(
        self,
        table_name: str,
        columns: list[str],
        primary_key: list[str],
        status_column: str,
        heartbeat_column: str,
    ) -> str:
        """Render the dialect's single-statement conditional lock claim.

        The statement must insert the row when absent, overwrite it when the
        claim condition holds (status <> ``%(_abk_running)s`` OR heartbeat <
        ``%(_abk_stale_before)s``), leave it untouched otherwise, and report
        the outcome via the cursor's affected-row count (>= 1 means claimed).
        """
        raise NotImplementedError

    # ── type mapping / DDL ───────────────────────────────────────────────────

    def _q(self, identifier: str) -> str:
        """Quote a SQL identifier (column/table name) for the dialect.

        Necessary because some column names (e.g. ``interval``) are reserved
        words on MySQL.
        """
        return f"{self._IDENT_QUOTE}{identifier}{self._IDENT_QUOTE}"

    @staticmethod
    def _canonical_type(ch_type: str) -> str:
        """Reduce a ClickHouse-flavored ``TableModel`` type to a canonical kind."""
        t = ch_type.strip()
        if t.startswith("Nullable(") and t.endswith(")"):
            t = t[len("Nullable(") : -1].strip()
        if t.startswith("DateTime"):
            return "datetime"
        if t.startswith("Date"):
            return "date"
        if t.startswith("String") or t.startswith("FixedString"):
            return "string"
        if t.startswith("Float"):
            return "float"
        if t.startswith("UInt") or t.startswith("Int"):
            return "int"
        if t.startswith("Bool"):
            return "bool"
        raise ValueError(f"Cannot map column type to a SQL dialect: {ch_type!r}")

    def _map_type(self, ch_type: str, in_primary_key: bool, max_length: int | None = None) -> str:
        kind = self._canonical_type(ch_type)
        if kind == "string":
            return self._string_type(in_primary_key, max_length)
        return self._TYPE_MAP[kind]

    @staticmethod
    def _is_nullable(col: ColumnDefinition) -> bool:
        return col.nullable or (col.type.startswith("Nullable(") and col.type.endswith(")"))

    @staticmethod
    def _render_default(col: ColumnDefinition) -> str:
        if col.default is None:
            return ""
        d = col.default
        if isinstance(d, (int, float)):
            return f" DEFAULT {d}"
        if isinstance(d, str) and d.lstrip("-").isdigit():
            return f" DEFAULT {d}"
        return f" DEFAULT '{d}'"

    def _render_column(self, col: ColumnDefinition, in_primary_key: bool) -> str:
        native = self._map_type(col.type, in_primary_key, col.max_length)
        # Primary-key columns are always NOT NULL; otherwise honor the model.
        nullable = self._is_nullable(col) and not in_primary_key
        null_sql = "" if nullable else " NOT NULL"
        return f"{self._q(col.name)} {native}{null_sql}{self._render_default(col)}"

    def register_table(self, table_name: str, table_model: TableModel) -> None:
        """Record the PK / version column for the insert path.

        Keyed by the bare table name (``table_name`` is schema/database-qualified).
        Called for every internal table on every run so a fresh manager instance
        knows the schema even when the table already exists and the DDL is skipped.
        """
        bare = table_name.split(".")[-1]
        self._table_meta[bare] = (table_model.primary_key, table_model.version_column)

    def create_table(
        self, table_name: str, table_model: TableModel, if_not_exists: bool = True
    ) -> None:
        """Create a table with an enforced PRIMARY KEY (no ClickHouse engine)."""
        pk = table_model.primary_key
        col_defs = [self._render_column(col, col.name in pk) for col in table_model.columns]
        col_defs.append(f"PRIMARY KEY ({', '.join(self._q(c) for c in pk)})")
        body = ",\n    ".join(col_defs)
        if_not_exists_clause = "IF NOT EXISTS " if if_not_exists else ""
        ddl = f"CREATE TABLE {if_not_exists_clause}{table_name} (\n    {body}\n)"
        self.execute_query(ddl)
        self.register_table(table_name, table_model)

    # ── queries ──────────────────────────────────────────────────────────────

    def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute a query and return rows as dicts (commits the transaction)."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(query, params if params else None)
                if cur.description is None:
                    rows: list[dict[str, Any]] = []
                else:
                    columns = [desc[0] for desc in cur.description]
                    rows = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
            self._conn.commit()
            return rows
        except Exception:
            self._conn.rollback()
            raise

    def table_exists(self, table_name: str, schema: str | None = None) -> bool:
        """Check ``information_schema.tables`` for the table in one/both locations."""
        locations = [schema] if schema else [self._internal_location, self._data_location]
        query = (
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %(schema)s AND table_name = %(table)s"
        )
        for loc in locations:
            if self.execute_query(query, {"schema": loc, "table": table_name}):
                return True
        return False

    def get_max_timestamp(
        self,
        table_name: str,
        where_clause: str = "",
        params: dict[str, Any] | None = None,
        timestamp_column: str = "timestamp",
    ) -> datetime | None:
        """Return ``max(timestamp_column)`` under the WHERE clause, or ``None``."""
        query = f"SELECT max({self._q(timestamp_column)}) AS last_ts FROM {table_name}"
        if where_clause:
            query += f" WHERE {where_clause}"
        result = self.execute_query(query, params)
        if result and result[0]["last_ts"] is not None:
            return result[0]["last_ts"]
        return None

    # ── writes ────────────────────────────────────────────────────────────────

    def _coerce(self, value: Any) -> Any:
        """Convert a numpy scalar to a driver-friendly Python value."""
        if value is None:
            return None
        if isinstance(value, np.datetime64):
            if np.isnat(value):
                return None
            seconds = (value - _EPOCH) / _ONE_SECOND
            # Store naive UTC (matches the codebase's naive-UTC convention).
            return datetime.fromtimestamp(float(seconds), tz=timezone.utc).replace(tzinfo=None)
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            f = float(value)
            return None if math.isnan(f) else f
        if isinstance(value, float) and math.isnan(value):
            return None
        if isinstance(value, np.ndarray):
            return value.tolist()
        return value

    def _rows_from_arrays(
        self, data: dict[str, np.ndarray]
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        columns = list(data.keys())
        lengths = {len(arr) for arr in data.values()}
        if len(lengths) > 1:
            raise ValueError(
                "All arrays must have same length, got: "
                f"{ {k: len(v) for k, v in data.items()} }"
            )
        num_rows = lengths.pop() if lengths else 0
        rows = [tuple(self._coerce(data[c][i]) for c in columns) for i in range(num_rows)]
        return columns, rows

    def _insert(
        self, cur: Any, table_name: str, data: dict[str, np.ndarray], conflict_strategy: str
    ) -> int:
        columns, rows = self._rows_from_arrays(data)
        if not rows:
            return 0
        bare = table_name.split(".")[-1]
        primary_key, version_column = self._table_meta.get(bare, ([], None))
        sql = self._build_insert_sql(
            table_name, columns, primary_key, version_column, conflict_strategy
        )
        cur.executemany(sql, rows)
        return len(rows)

    def insert_batch(
        self, table_name: str, data: dict[str, np.ndarray], conflict_strategy: str = "ignore"
    ) -> int:
        """Insert rows honoring ``conflict_strategy`` against the enforced PK."""
        if not data:
            return 0
        try:
            with self._conn.cursor() as cur:
                inserted = self._insert(cur, table_name, data, conflict_strategy)
            self._conn.commit()
            return inserted
        except Exception:
            self._conn.rollback()
            raise

    def delete_rows(
        self,
        table_name: str,
        where_clause: str,
        params: dict[str, Any] | None = None,
        sync: bool = False,
    ) -> int:
        """Delete rows with a plain ``DELETE FROM`` (``sync`` is a no-op here)."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {table_name} WHERE {where_clause}", params if params else None
                )
                affected = int(cur.rowcount)
            self._conn.commit()
            return max(affected, 0)
        except Exception:
            self._conn.rollback()
            raise

    def upsert_record(
        self,
        table_name: str,
        key_columns: dict[str, Any],
        data: dict[str, np.ndarray],
        sync: bool = False,
    ) -> int:
        """Atomically replace a row: DELETE by key + INSERT, in one transaction.

        ``sync`` is a no-op here — the transaction already guarantees the old
        row is gone before the insert commits.
        """
        where = " AND ".join(f"{col} = %({col})s" for col in key_columns)
        try:
            with self._conn.cursor() as cur:
                cur.execute(f"DELETE FROM {table_name} WHERE {where}", dict(key_columns))
                inserted = self._insert(cur, table_name, data, conflict_strategy="fail")
            self._conn.commit()
            return inserted
        except Exception:
            self._conn.rollback()
            raise

    # ── locking ──────────────────────────────────────────────────────────────

    def try_acquire_lock(
        self,
        table_name: str,
        key_columns: dict[str, Any],
        row: dict[str, Any],
        *,
        status_column: str = "status",
        running_value: str = "running",
        heartbeat_column: str = "started_at",
        timeout_seconds: int = 3600,
        token_column: str | None = None,
    ) -> bool:
        """Claim the lock row in ONE conditional upsert statement (atomic).

        ``token_column`` is accepted for interface parity but unused: the
        enforced primary key + single-statement claim make the outcome exact,
        so no read-back verification is needed.
        """
        for key, value in key_columns.items():
            if key not in row:
                raise ValueError(f"lock row must contain key column {key!r}")
            if row[key] != value:
                raise ValueError(
                    f"lock row value for {key!r} ({row[key]!r}) != key_columns value ({value!r})"
                )
        columns = list(row.keys())
        primary_key = list(key_columns.keys())
        if not [c for c in columns if c not in primary_key]:
            raise ValueError("lock row must have at least one non-key column")

        stale_before = now_utc_naive() - timedelta(seconds=timeout_seconds)
        sql = self._build_claim_sql(
            table_name, columns, primary_key, status_column, heartbeat_column
        )
        params = {c: self._coerce(v) for c, v in row.items()}
        params["_abk_running"] = running_value
        params["_abk_stale_before"] = stale_before
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                affected = int(cur.rowcount)
            self._conn.commit()
            return affected >= 1
        except Exception:
            self._conn.rollback()
            raise

    # ── locations / lifecycle ─────────────────────────────────────────────────

    @property
    def internal_location(self) -> str:
        return self._internal_location

    @property
    def data_location(self) -> str:
        return self._data_location

    def close(self) -> None:
        conn = getattr(self, "_conn", None)
        if conn is not None:
            conn.close()
