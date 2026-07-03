"""
Core data models for abkit.

Defines table schemas and column definitions for database abstraction.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ColumnDefinition:
    """
    Definition of a table column.

    Attributes:
        name: Column name
        type: SQL type (database-specific)
        nullable: Whether column can be NULL
        default: Default value for column
    """

    name: str
    type: str
    nullable: bool = False
    default: Any | None = None

    def __post_init__(self):
        """Validate column definition."""
        if not self.name:
            raise ValueError("Column name cannot be empty")
        if not self.type:
            raise ValueError("Column type cannot be empty")


@dataclass
class TableModel:
    """
    Model for table schema definition.

    This is used by BaseDatabaseManager.create_table() to create tables
    in a database-agnostic way.

    Attributes:
        columns: List of column definitions
        primary_key: List of column names forming primary key
        engine: Database engine (ClickHouse-specific, e.g., "MergeTree")
        order_by: Columns for ORDER BY clause (ClickHouse-specific)
        indexes: Additional indexes to create
        version_column: Column that drives last-writer-wins deduplication.
            On ClickHouse this is the version encoded in the engine string
            (e.g. ``ReplacingMergeTree(created_at)``); on SQL backends with an
            enforced primary key it drives a version-aware upsert so a re-insert
            with a newer ``version_column`` replaces the existing row. ``None``
            for tables that do not deduplicate by version (e.g. ``_ab_tasks``).

    Example:
        >>> model = TableModel(
        ...     columns=[
        ...         ColumnDefinition("id", "Int32"),
        ...         ColumnDefinition("name", "String"),
        ...     ],
        ...     primary_key=["id"],
        ...     engine="MergeTree",
        ...     order_by=["id"]
        ... )
    """

    columns: list[ColumnDefinition]
    primary_key: list[str]
    engine: str | None = None
    order_by: list[str] | None = None
    indexes: list[str] = field(default_factory=list)
    version_column: str | None = None

    def __post_init__(self):
        """Validate table model."""
        if not self.columns:
            raise ValueError("Table must have at least one column")

        if not self.primary_key:
            raise ValueError("Table must have a primary key")

        # Validate primary key columns exist
        column_names = {col.name for col in self.columns}
        for pk_col in self.primary_key:
            if pk_col not in column_names:
                raise ValueError(f"Primary key column '{pk_col}' not found in table columns")

        # Validate order_by columns exist (if specified)
        if self.order_by:
            for order_col in self.order_by:
                if order_col not in column_names:
                    raise ValueError(f"ORDER BY column '{order_col}' not found in table columns")

        # Validate version column exists (if specified)
        if self.version_column and self.version_column not in column_names:
            raise ValueError(f"Version column '{self.version_column}' not found in table columns")

    def get_column(self, name: str) -> ColumnDefinition | None:
        """
        Get column definition by name.

        Args:
            name: Column name

        Returns:
            ColumnDefinition or None if not found
        """
        for col in self.columns:
            if col.name == name:
                return col
        return None
