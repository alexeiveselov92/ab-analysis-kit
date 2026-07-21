"""
Base database manager interface.

Provides universal methods for database operations WITHOUT hardcoding
specific table logic (e.g., _ab_exposures, _ab_results).

The manager is database-agnostic and provides generic operations:
- execute_query(): Run SQL and return results
- create_table(): Create table from TableModel
- table_exists(): Check if table exists
- insert_batch(): Insert batch of data
- get_max_timestamp(): Max of a timestamp column under a WHERE clause
- try_acquire_lock(): Atomically claim a lock row (task locking)

Invariant (CLAUDE.md): the manager stays generic and ``table_name``-keyed;
``_ab_*`` semantics (row shapes, staleness policy, lock grain) live in
``database/internal_tables/``, never here.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import numpy as np

from abkit.core.models import ColumnDefinition, TableModel


class BaseDatabaseManager(ABC):
    """
    Universal database manager interface.

    This class provides GENERIC methods for database operations.
    It does NOT hardcode logic for internal tables (_ab_results, etc.).

    Internal table management is handled by higher-level classes that
    use these generic methods.

    Key Design Principles:
    1. Universal methods (not table-specific)
    2. Works with any table via table_name parameter
    3. Type conversion handled internally
    4. Connection pooling and error handling
    """

    @abstractmethod
    def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Execute SQL query and return results as list of dictionaries.

        Args:
            query: SQL query to execute
            params: Optional query parameters for parameterized queries

        Returns:
            List of dictionaries where each dict represents a row

        Raises:
            DatabaseError: If query execution fails

        Example:
            >>> results = manager.execute_query(
            ...     "SELECT * FROM _ab_results WHERE experiment = %(exp)s",
            ...     {"exp": "signup_test"}
            ... )
            >>> for row in results:
            ...     print(row['end_ts'], row['effect'])
        """
        pass

    @abstractmethod
    def create_table(
        self, table_name: str, table_model: TableModel, if_not_exists: bool = True
    ) -> None:
        """
        Create table from TableModel definition.

        Converts database-agnostic TableModel into database-specific DDL.

        Args:
            table_name: Name of table to create
            table_model: Table schema definition
            if_not_exists: Add IF NOT EXISTS clause

        Raises:
            DatabaseError: If table creation fails

        Example:
            >>> model = TableModel(
            ...     columns=[
            ...         ColumnDefinition("id", "Int32"),
            ...         ColumnDefinition("value", "Float64", nullable=True),
            ...     ],
            ...     primary_key=["id"],
            ...     engine="MergeTree",
            ...     order_by=["id"]
            ... )
            >>> manager.create_table("my_table", model)
        """
        pass

    @abstractmethod
    def table_exists(self, table_name: str, schema: str | None = None) -> bool:
        """
        Check if table exists in database.

        Args:
            table_name: Name of table to check
            schema: Optional schema/database name (if None, use default)

        Returns:
            True if table exists, False otherwise

        Example:
            >>> if not manager.table_exists("_ab_results"):
            ...     manager.create_table("_ab_results", results_model)
        """
        pass

    @abstractmethod
    def list_columns(self, table_name: str, schema: str | None = None) -> list[str]:
        """
        List the live column names of an existing table, in storage order.

        Backends introspect their catalog (``system.columns`` on ClickHouse,
        ``information_schema.columns`` on PostgreSQL/MySQL). Returns an empty
        list when the table does not exist.

        Args:
            table_name: Bare table name (no schema/database prefix)
            schema: Schema/database holding the table (if None, use the
                internal location)

        Returns:
            Column names as stored, ordered by position
        """
        pass

    @abstractmethod
    def _add_column(self, table_name: str, column: ColumnDefinition) -> None:
        """
        Emit the backend's ``ALTER TABLE ... ADD COLUMN`` for one column.

        Must be safe against a concurrent identical ALTER: backends either use
        ``ADD COLUMN IF NOT EXISTS`` (ClickHouse, PostgreSQL) or swallow the
        duplicate-column error (MySQL, which has no ``IF NOT EXISTS``).
        Only :meth:`ensure_columns` calls this, after diffing the live schema.

        Args:
            table_name: Fully qualified table name
            column: The abstract column definition to add
        """
        pass

    def ensure_columns(self, table_name: str, table_model: TableModel) -> list[str]:
        """
        Additive-only schema sync: ADD every model column missing from the
        live table (the project's post-release migration primitive, M9 WP1).

        Diffs ``table_model``'s declared columns against the live table's
        columns and emits ``ALTER TABLE ... ADD COLUMN`` for anything missing.
        Never drops, renames, or retypes a column — a column present in the
        live table but absent from the model is left untouched (loud failures
        stay with the insert path's column-mismatch checks). Idempotent: on a
        second call the diff is empty and no DDL is emitted.

        Additive columns must be nullable or carry a default: adding a
        NOT-NULL, no-default column to a table with existing rows fails on the
        SQL backends, so the contract is enforced here — deterministically,
        for every backend — before any DDL runs.

        Physical placement caveat: an added column lands at the END of the
        live table (PostgreSQL has no positional ADD COLUMN at all), so a
        migrated table's storage order differs from a freshly created one's
        model order. Harmless by design — every read/write in the codebase is
        column-NAME-keyed, never positional.

        Args:
            table_name: Fully qualified table name (``location.table``)
            table_model: The current (target) table schema

        Returns:
            Names of the columns actually added, in model order (empty when
            the live table already matches)
        """
        location, _, bare = table_name.rpartition(".")
        live = set(self.list_columns(bare, schema=location or None))
        if not live:
            # Empty listing = table missing OR an unreadable catalog. Both
            # no-op DELIBERATELY: creation is create_table's job, and a
            # misread catalog must trigger no DDL at all — the insert path's
            # column-mismatch check stays the loud failure, an ALTER storm
            # against a table we cannot see would be the dangerous reaction.
            return []
        missing = [col for col in table_model.columns if col.name not in live]
        for col in missing:
            if not col.is_nullable and col.default is None:
                raise ValueError(
                    f"ensure_columns: cannot add NOT-NULL column {col.name!r} with no "
                    f"default to existing table {table_name} — additive migrations "
                    "require nullable-or-defaulted columns"
                )
        added: list[str] = []
        for col in missing:
            self._add_column(table_name, col)
            added.append(col.name)
        return added

    @abstractmethod
    def insert_batch(
        self, table_name: str, data: dict[str, np.ndarray], conflict_strategy: str = "ignore"
    ) -> int:
        """
        Insert batch of data into table.

        Universal method that works with any table - NOT specific to
        internal tables.

        Args:
            table_name: Name of table to insert into
            data: Dictionary mapping column names to numpy arrays
                 All arrays must have same length
            conflict_strategy: How to handle conflicts:
                - "ignore": Skip rows with duplicate primary keys
                - "replace": Replace existing rows
                - "fail": Raise error on conflict

        Returns:
            Number of rows inserted (may be less than input if conflicts ignored)

        Raises:
            ValueError: If arrays have different lengths
            DatabaseError: If insertion fails

        Example:
            >>> data = {
            ...     "experiment": np.array(["signup_test", "signup_test"]),
            ...     "unit_id": np.array(["u1", "u2"]),
            ...     "variant": np.array(["control", "treatment"]),
            ... }
            >>> rows_inserted = manager.insert_batch(
            ...     "_ab_exposures", data, conflict_strategy="ignore"
            ... )
        """
        pass

    @abstractmethod
    def get_max_timestamp(
        self,
        table_name: str,
        where_clause: str = "",
        params: dict[str, Any] | None = None,
        timestamp_column: str = "timestamp",
    ) -> datetime | None:
        """
        Get ``max(timestamp_column)`` for rows matching a WHERE clause.

        Universal method that works with any table containing a timestamp
        column. Backends must normalise their NULL representation: ClickHouse
        returns the epoch (1970-01-01) for an empty aggregate, which is
        translated to ``None``.

        Args:
            table_name: Table to query
            where_clause: SQL predicate placed after ``WHERE`` (may use
                ``%(name)s`` placeholders resolved from ``params``); empty
                string means no filter
            params: Optional query parameters for the WHERE clause
            timestamp_column: Name of timestamp column (default: "timestamp")

        Returns:
            Max timestamp or None if no data found

        Example:
            >>> last = manager.get_max_timestamp(
            ...     "_ab_results", "experiment = %(e)s", {"e": "signup_test"},
            ...     timestamp_column="end_ts",
            ... )
        """
        pass

    @abstractmethod
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
        """
        Atomically claim a lock row; return True iff this caller now owns it.

        The claim succeeds when the row does not exist, is not in the running
        state, or its heartbeat is older than ``timeout_seconds`` (a stale lock
        left by a dead process may be stolen). On success the full ``row`` is
        written; on failure the existing row is left untouched.

        The manager stays generic: the lock-table semantics (which column is
        the status, what "running" means, the row shape) are injected by the
        caller — ``internal_tables/_tasks`` owns the ``_ab_tasks`` policy.

        Atomicity by backend (quorum must-fix "atomic lock"):
        - PostgreSQL: single-statement ``INSERT ... ON CONFLICT (pk) DO UPDATE
          ... WHERE <not running or stale>``; claim detected via rowcount.
        - MySQL: single-statement row-alias ``INSERT ... AS new ON DUPLICATE
          KEY UPDATE`` with the claim condition latched into a session
          variable (MySQL 8.0.19+); claim detected via affected-rows.
        - ClickHouse: ADVISORY only (no atomic upsert primitive exists):
          staleness check -> synchronous DELETE -> INSERT -> read-back with a
          deterministic winner tie-break on ``(heartbeat, token)``. Requires
          ``token_column`` for the read-back verification.

        Args:
            table_name: Fully qualified lock table name
            key_columns: PK columns identifying the lock row
            row: The full row to write on a successful claim (must contain all
                key columns with identical values, the status column set to
                ``running_value``, and a fresh heartbeat)
            status_column: Column holding the task status
            running_value: Status value that means "actively held"
            heartbeat_column: Timestamp column used for staleness
            timeout_seconds: Age after which a running lock counts as stale
            token_column: Column holding a caller-unique token; required for
                the ClickHouse read-back, ignored by SQL backends

        Returns:
            True if the lock was acquired, False if it is held by another
            live owner
        """
        pass

    @abstractmethod
    def upsert_record(
        self,
        table_name: str,
        key_columns: dict[str, Any],
        data: dict[str, np.ndarray],
        sync: bool = False,
    ) -> int:
        """
        Delete record by key columns, then insert new record.

        This is a universal database-agnostic upsert pattern that guarantees
        uniqueness by explicitly deleting old record before inserting new one.

        Use this when ReplacingMergeTree or native UPSERT is not suitable
        (e.g., for informational tables where guaranteed uniqueness is required).

        Implementation varies by database:
        - ClickHouse: ALTER TABLE ... DELETE + INSERT
        - PostgreSQL: DELETE + INSERT (in transaction)
        - MySQL: DELETE + INSERT (in transaction)

        Args:
            table_name: Fully qualified table name
            key_columns: Dict of column names to values for WHERE clause
                        (e.g., {"experiment": "signup_test"})
            data: Dict of column names to numpy arrays for INSERT
                  (must include all key columns)
            sync: If True, the delete is fully applied before the insert
                (ClickHouse: ``mutations_sync = 1``). Use for rows that must be
                immediately uniquely visible (e.g. lock/status rows). SQL
                backends are always synchronous so this is a no-op there.

        Returns:
            Number of rows inserted (typically 1)

        Raises:
            DatabaseError: If operation fails

        Example:
            >>> manager.upsert_record(
            ...     table_name="abkit_internal._ab_experiments",
            ...     key_columns={"experiment": "signup_test"},
            ...     data={
            ...         "experiment": np.array(["signup_test"]),
            ...         "status": np.array(["running"]),
            ...         ...
            ...     }
            ... )
        """
        pass

    @abstractmethod
    def delete_rows(
        self,
        table_name: str,
        where_clause: str,
        params: dict[str, Any] | None = None,
        sync: bool = False,
    ) -> int:
        """
        Delete rows matching a WHERE clause from a table.

        This is the single generic delete primitive. It exists so that
        higher-level code (InternalTablesManager) never has to write
        backend-specific delete syntax: ClickHouse renders an
        ``ALTER TABLE ... DELETE`` mutation, while SQL backends render a plain
        ``DELETE FROM ... WHERE ...``.

        Args:
            table_name: Fully qualified table name to delete from
            where_clause: SQL predicate placed after ``WHERE`` (may use
                ``%(name)s`` placeholders resolved from ``params``)
            params: Optional query parameters for the WHERE clause
            sync: If True, wait for the delete to be fully applied before
                returning. On ClickHouse this appends ``SETTINGS
                mutations_sync = 1`` (async mutations are synchronous-on-request);
                SQL backends are always synchronous so this is a no-op there.

        Returns:
            Number of rows deleted when the backend can report it (SQL
            backends), else 0 (ClickHouse mutations do not return a count).

        Example:
            >>> manager.delete_rows(
            ...     "abkit_internal._ab_exposures",
            ...     "experiment = %(e)s",
            ...     {"e": "signup_test"},
            ... )
        """
        pass

    @property
    def final_modifier(self) -> str:
        """
        SQL fragment appended after a table name to collapse duplicate versions
        on read.

        ClickHouse's ReplacingMergeTree may hold transient duplicate primary
        keys until a background merge runs, so dedup reads append ``FINAL``
        (quorum must-fix "correctness under async merge" — every
        correctness-sensitive read of a versioned table must use this or an
        argMax/LIMIT 1 BY equivalent). Backends with an enforced unique primary
        key (PostgreSQL, MySQL) never have duplicates, so this is the empty
        string for them.

        Returns:
            ``" FINAL"`` on ClickHouse, ``""`` (no modifier) by default.
        """
        return ""

    @property
    @abstractmethod
    def internal_location(self) -> str:
        """
        Get full location path for internal tables.

        Format depends on database:
        - ClickHouse: "database_name"
        - PostgreSQL: "schema_name"

        Returns:
            Full path to internal schema/database

        Example:
            >>> manager.internal_location
            'abkit_internal'
        """
        pass

    @property
    @abstractmethod
    def data_location(self) -> str:
        """
        Get full location path for user data tables.

        Format depends on database:
        - ClickHouse: "database_name"
        - PostgreSQL: "schema_name"

        Returns:
            Full path to data schema/database

        Example:
            >>> manager.data_location
            'analytics'
        """
        pass

    def register_table(self, table_name: str, table_model: TableModel) -> None:
        """
        Register a table's schema (primary key, version column) with the manager.

        Backends that need per-table schema knowledge on the insert path — to
        build conflict handling / version-aware upserts — record it here. This is
        called for every internal table on every run (even when the table already
        exists, so the DDL step is skipped), so a fresh manager instance always
        knows the schema. The default is a no-op (ClickHouse derives nothing from
        it; dedup is handled by the table engine).

        Args:
            table_name: Fully qualified table name
            table_model: Table schema definition
        """
        return None

    def get_full_table_name(self, table_name: str, use_internal: bool = True) -> str:
        """
        Get fully qualified table name.

        Args:
            table_name: Table name
            use_internal: If True, use internal_location, else data_location

        Returns:
            Fully qualified table name

        Example:
            >>> manager.get_full_table_name("_ab_results", use_internal=True)
            'abkit_internal._ab_results'
        """
        location = self.internal_location if use_internal else self.data_location
        return f"{location}.{table_name}"

    @abstractmethod
    def close(self) -> None:
        """
        Close database connection and cleanup resources.

        Example:
            >>> manager.close()
        """
        pass

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close connection."""
        self.close()
