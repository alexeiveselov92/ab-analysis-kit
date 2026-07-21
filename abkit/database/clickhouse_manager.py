"""
ClickHouse database manager implementation.

Implements BaseDatabaseManager for ClickHouse using universal methods.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from abkit.utils.datetime_utils import now_utc_naive, to_naive_utc

try:
    from clickhouse_driver import Client

    CLICKHOUSE_AVAILABLE = True
except ImportError:
    CLICKHOUSE_AVAILABLE = False

from abkit.core.models import ColumnDefinition, TableModel
from abkit.database.manager import BaseDatabaseManager

_EPOCH_NAIVE = datetime(1970, 1, 1, 0, 0, 0)


class ClickHouseDatabaseManager(BaseDatabaseManager):
    """
    ClickHouse implementation of BaseDatabaseManager.

    Uses universal methods - does NOT hardcode internal table logic.

    Args:
        host: ClickHouse host
        port: ClickHouse port (default: 9000 for native protocol)
        user: Database user
        password: Database password
        internal_database: Database for internal tables (_ab_*)
        data_database: Database for user data tables
        settings: Optional ClickHouse settings
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9000,
        user: str = "default",
        password: str = "",
        internal_database: str = "abkit_internal",
        data_database: str = "default",
        settings: dict[str, Any] | None = None,
    ):
        """Initialize ClickHouse manager."""
        if not CLICKHOUSE_AVAILABLE:
            raise ImportError(
                "clickhouse-driver is not installed. "
                "Install with: pip install ab-analysis-kit[clickhouse]"
            )

        self._internal_database = internal_database
        self._data_database = data_database

        # Create client
        self._client = Client(
            host=host,
            port=port,
            user=user,
            password=password,
            settings=settings or {},
        )

        # Ensure databases exist
        self._ensure_databases()

    def _ensure_databases(self) -> None:
        """Create internal and data databases if they don't exist."""
        for db in [self._internal_database, self._data_database]:
            self._client.execute(f"CREATE DATABASE IF NOT EXISTS {db}")

    def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Execute SQL query and return results as list of dictionaries.

        Args:
            query: SQL query to execute
            params: Optional query parameters

        Returns:
            List of dictionaries where each dict represents a row
        """
        # Execute query with or without parameters
        if params:
            result = self._client.execute(query, params, with_column_types=True)
        else:
            result = self._client.execute(query, with_column_types=True)

        # result is tuple: (rows, columns_with_types)
        # columns_with_types is list of tuples: (name, type)
        rows, columns_with_types = result
        column_names = [col[0] for col in columns_with_types]

        # Convert to list of dicts
        return [dict(zip(column_names, row, strict=True)) for row in rows]

    def create_table(
        self, table_name: str, table_model: TableModel, if_not_exists: bool = True
    ) -> None:
        """
        Create ClickHouse table from TableModel.

        Converts generic TableModel to ClickHouse-specific DDL.

        Args:
            table_name: Name of table to create
            table_model: Table schema definition
            if_not_exists: Add IF NOT EXISTS clause
        """
        # Build column definitions
        col_defs = []
        for col in table_model.columns:
            col_def = f"{col.name} {col.type}"
            if col.default is not None:
                col_def += f" DEFAULT {self._format_default(col.default)}"
            col_defs.append(col_def)

        columns_sql = ",\n    ".join(col_defs)

        # Build CREATE TABLE statement
        if_not_exists_clause = "IF NOT EXISTS " if if_not_exists else ""

        # For ClickHouse, use engine and order_by from table_model
        engine = table_model.engine or "MergeTree"
        order_by = table_model.order_by or table_model.primary_key

        order_by_clause = ", ".join(order_by)

        # Add parentheses only if engine doesn't already have them
        if "(" in engine:
            engine_clause = engine
        else:
            engine_clause = f"{engine}()"

        ddl = f"""
        CREATE TABLE {if_not_exists_clause}{table_name} (
            {columns_sql}
        )
        ENGINE = {engine_clause}
        ORDER BY ({order_by_clause})
        """.strip()

        self._client.execute(ddl)

    def _format_default(self, value: Any) -> str:
        """Format default value for SQL."""
        if isinstance(value, str):
            return f"'{value}'"
        elif isinstance(value, (int, float)):
            return str(value)
        elif value is None:
            return "NULL"
        else:
            return str(value)

    def table_exists(self, table_name: str, schema: str | None = None) -> bool:
        """
        Check if table exists in ClickHouse.

        Args:
            table_name: Name of table to check
            schema: Database name (if None, check both internal and data databases)

        Returns:
            True if table exists
        """
        if schema:
            databases = [schema]
        else:
            databases = [self._internal_database, self._data_database]

        for db in databases:
            query = """
            SELECT 1
            FROM system.tables
            WHERE database = %(database)s
              AND name = %(table)s
            """
            result = self.execute_query(query, {"database": db, "table": table_name})
            if result:
                return True

        return False

    def list_columns(self, table_name: str, schema: str | None = None) -> list[str]:
        """Live column names from ``system.columns``, ordered by position."""
        query = """
        SELECT name
        FROM system.columns
        WHERE database = %(database)s
          AND table = %(table)s
        ORDER BY position
        """
        rows = self.execute_query(
            query, {"database": schema or self._internal_database, "table": table_name}
        )
        return [row["name"] for row in rows]

    def _add_column(self, table_name: str, column: ColumnDefinition) -> None:
        # Model types are ClickHouse-flavored, so they render verbatim.
        col_def = f"{column.name} {column.type}"
        if column.default is not None:
            col_def += f" DEFAULT {self._format_default(column.default)}"
        self._client.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_def}")

    def insert_batch(
        self, table_name: str, data: dict[str, np.ndarray], conflict_strategy: str = "ignore"
    ) -> int:
        """
        Insert batch of data into ClickHouse table.

        Args:
            table_name: Table to insert into
            data: Dictionary mapping column names to numpy arrays
            conflict_strategy: "ignore" or "replace" (ClickHouse doesn't support REPLACE)

        Returns:
            Number of rows inserted
        """
        if not data:
            return 0

        # Validate all arrays have same length
        lengths = [len(arr) for arr in data.values()]
        if len(set(lengths)) > 1:
            raise ValueError(
                f"All arrays must have same length, got: {dict(zip(data.keys(), lengths, strict=True))}"
            )

        num_rows = lengths[0]
        if num_rows == 0:
            return 0

        # Convert numpy arrays to lists for ClickHouse driver
        column_names = list(data.keys())
        rows = []

        for i in range(num_rows):
            row = []
            for col_name in column_names:
                value = data[col_name][i]

                # Convert numpy types to Python types
                if isinstance(value, (np.datetime64, np.timedelta64)):
                    # Convert numpy datetime64 to Python datetime
                    value = self._convert_numpy_datetime(value)
                elif isinstance(value, np.ndarray):
                    value = value.tolist()
                elif isinstance(value, (np.integer, np.floating, np.bool_)):
                    value = value.item()
                # NaN -> NULL must run AFTER the numpy unwrap (an np.float64 NaN
                # previously slipped through and was stored as NaN, not NULL)
                if isinstance(value, float) and np.isnan(value):
                    value = None

                row.append(value)
            rows.append(row)

        # For ClickHouse, conflict_strategy="ignore" is handled by PRIMARY KEY
        # Duplicates are silently ignored by MergeTree
        # Note: For ReplacingMergeTree, use conflict_strategy="replace"

        # Insert data
        self._client.execute(f"INSERT INTO {table_name} ({', '.join(column_names)}) VALUES", rows)

        return num_rows

    def _convert_numpy_datetime(self, dt: np.datetime64) -> datetime:
        """Convert numpy datetime64 to Python datetime with UTC timezone."""
        # Convert to timestamp
        timestamp = (dt - np.datetime64("1970-01-01T00:00:00")) / np.timedelta64(1, "s")
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    def get_max_timestamp(
        self,
        table_name: str,
        where_clause: str = "",
        params: dict[str, Any] | None = None,
        timestamp_column: str = "timestamp",
    ) -> datetime | None:
        """
        Get ``max(timestamp_column)`` under the WHERE clause.

        Args:
            table_name: Table to query
            where_clause: SQL predicate placed after ``WHERE``; empty = no filter
            params: Optional query parameters
            timestamp_column: Name of timestamp column

        Returns:
            Max timestamp or None if no data
        """
        query = f"SELECT max({timestamp_column}) AS last_ts FROM {table_name}"
        if where_clause:
            query += f" WHERE {where_clause}"

        result = self.execute_query(query, params)

        if result and result[0]["last_ts"]:
            last_ts = result[0]["last_ts"]

            # ClickHouse returns epoch (1970-01-01 00:00:00) for an empty
            # aggregate. Detect this and treat as None.
            epoch = _EPOCH_NAIVE

            # Handle both timezone-aware and naive datetimes
            if last_ts.tzinfo is not None:
                epoch = epoch.replace(tzinfo=last_ts.tzinfo)

            if last_ts == epoch:
                return None

            return last_ts

        return None

    def delete_rows(
        self,
        table_name: str,
        where_clause: str,
        params: dict[str, Any] | None = None,
        sync: bool = False,
    ) -> int:
        """
        Delete rows via a ClickHouse ``ALTER TABLE ... DELETE`` mutation.

        Args:
            table_name: Fully qualified table name
            where_clause: SQL predicate placed after ``WHERE``
            params: Optional query parameters
            sync: If True, append ``SETTINGS mutations_sync = 1`` so the
                mutation is fully applied before returning.

        Returns:
            0 — ClickHouse mutations are asynchronous and do not report a count.
        """
        query = f"ALTER TABLE {table_name} DELETE WHERE {where_clause}"
        if sync:
            query += " SETTINGS mutations_sync = 1"
        self._client.execute(query, params or {})
        return 0

    @property
    def final_modifier(self) -> str:
        """ClickHouse collapses ReplacingMergeTree versions on read with FINAL."""
        return " FINAL"

    def upsert_record(
        self,
        table_name: str,
        key_columns: dict[str, Any],
        data: dict[str, np.ndarray],
        sync: bool = False,
    ) -> int:
        """
        Upsert record in ClickHouse using DELETE + INSERT pattern.

        ClickHouse doesn't have native UPSERT, so we explicitly delete
        the old record (if exists) and then insert the new one.

        Args:
            table_name: Fully qualified table name
            key_columns: Dict of column names to values for WHERE clause
            data: Dict of column names to numpy arrays for INSERT
            sync: If True, the delete mutation is applied synchronously
                (``mutations_sync = 1``) so the old row is guaranteed gone
                before the insert — required for rows that must be immediately
                uniquely visible (lock/status rows).

        Returns:
            Number of rows inserted (typically 1)
        """
        # Step 1: DELETE existing record (if any)
        where_parts = [f"{col} = %({col})s" for col in key_columns.keys()]
        self.delete_rows(table_name, " AND ".join(where_parts), dict(key_columns), sync=sync)

        # Step 2: INSERT new record
        return self.insert_batch(table_name, data, conflict_strategy="ignore")

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
        settle_seconds: float = 0.25,
    ) -> bool:
        """
        ADVISORY lock claim — ClickHouse has no atomic upsert primitive.

        Protocol: staleness check → conditional synchronous DELETE
        (``mutations_sync = 1``; only rows that are NOT a live running claim —
        or our own — are removed, so a racer can never erase an
        already-confirmed claim) → INSERT with the heartbeat re-stamped at
        insert time (so heartbeat order tracks insert order, not row-assembly
        order) → a short settle pause → read-back with a deterministic winner
        rule — the row with the smallest ``(heartbeat, token)`` wins, ties
        broken by the token string. A loser deletes its OWN row before
        returning False so it never blocks later staleness checks.

        Residual advisory limitations (documented, by design — the SQL
        backends are the atomic ones): cross-host clock skew larger than the
        settle window can invert heartbeat order between racers, and a racer
        whose insert becomes visible only after the winner's read-back +
        settle can still self-confirm. Keep NTP sane and prefer one scheduler
        per project.

        ``token_column`` is REQUIRED (the read-back cannot tell two claimers
        apart without it).
        """
        if token_column is None:
            raise ValueError(
                "ClickHouse advisory lock requires token_column (read-back verification)"
            )
        for key, value in key_columns.items():
            if key not in row:
                raise ValueError(f"lock row must contain key column {key!r}")
            if row[key] != value:
                raise ValueError(
                    f"lock row value for {key!r} ({row[key]!r}) != key_columns value ({value!r})"
                )
        if token_column not in row:
            raise ValueError(f"lock row must contain token column {token_column!r}")

        where = " AND ".join(f"{col} = %({col})s" for col in key_columns)
        stale_before = now_utc_naive() - timedelta(seconds=timeout_seconds)

        # 1. Staleness check: an actively-held lock blocks the claim.
        existing = self.execute_query(
            f"SELECT {status_column} AS s, {heartbeat_column} AS hb "
            f"FROM {table_name} WHERE {where}",
            dict(key_columns),
        )
        for r in existing:
            heartbeat = to_naive_utc(r["hb"])
            if r["s"] == running_value and heartbeat is not None and heartbeat >= stale_before:
                return False

        # 2. Claim: clear only CLAIMABLE rows (stale, finished, or our own) —
        # never a rival's live running claim (a racer that passed step 1 before
        # the winner confirmed must not erase the winner's row).
        claim_params: dict[str, Any] = dict(key_columns)
        claim_params["_abk_running"] = running_value
        claim_params["_abk_stale_before"] = stale_before
        claim_params["_abk_token"] = row[token_column]
        self.delete_rows(
            table_name,
            f"{where} AND ({status_column} <> %(_abk_running)s "
            f"OR {heartbeat_column} < %(_abk_stale_before)s "
            f"OR {token_column} = %(_abk_token)s)",
            claim_params,
            sync=True,
        )
        # Re-stamp the heartbeat AT INSERT TIME: the winner rule below relies
        # on heartbeat order tracking insert order (row assembly happens before
        # the slow sync delete, which would invert it).
        insert_row = dict(row)
        insert_row[heartbeat_column] = now_utc_naive()
        insert_data = {col: np.array([value], dtype=object) for col, value in insert_row.items()}
        self.insert_batch(table_name, insert_data, conflict_strategy="ignore")

        # 3. Settle, then read back: deterministic winner among racers.
        if settle_seconds > 0:
            time.sleep(settle_seconds)
        rows_back = self.execute_query(
            f"SELECT {token_column} AS t, {heartbeat_column} AS hb "
            f"FROM {table_name} WHERE {where}",
            dict(key_columns),
        )
        if not rows_back:
            return False
        winner = min(
            rows_back,
            key=lambda r: (to_naive_utc(r["hb"]) or _EPOCH_NAIVE, str(r["t"])),
        )
        if str(winner["t"]) == str(insert_row[token_column]):
            return True
        # Loser: remove our own row so it never blocks later staleness checks.
        self.delete_rows(
            table_name,
            f"{where} AND {token_column} = %(_abk_token)s",
            {**key_columns, "_abk_token": insert_row[token_column]},
            sync=True,
        )
        return False

    @property
    def internal_location(self) -> str:
        """Get internal database name."""
        return self._internal_database

    @property
    def data_location(self) -> str:
        """Get data database name."""
        return self._data_database

    def close(self) -> None:
        """Close ClickHouse connection."""
        if hasattr(self, "_client"):
            self._client.disconnect()
