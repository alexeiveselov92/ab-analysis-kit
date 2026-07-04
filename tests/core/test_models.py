"""Tests for core data models."""

import pytest

from abkit.core.models import ColumnDefinition, TableModel


class TestColumnDefinition:
    """Test ColumnDefinition dataclass."""

    def test_basic_column(self):
        """Test creating basic column."""
        col = ColumnDefinition("id", "Int32")
        assert col.name == "id"
        assert col.type == "Int32"
        assert col.nullable is False
        assert col.default is None

    def test_nullable_column(self):
        """Test nullable column."""
        col = ColumnDefinition("value", "Float64", nullable=True)
        assert col.nullable is True

    def test_column_with_default(self):
        """Test column with default value."""
        col = ColumnDefinition("status", "String", default="pending")
        assert col.default == "pending"

    def test_empty_name(self):
        """Test error on empty column name."""
        with pytest.raises(ValueError, match="Column name cannot be empty"):
            ColumnDefinition("", "String")

    def test_empty_type(self):
        """Test error on empty column type."""
        with pytest.raises(ValueError, match="Column type cannot be empty"):
            ColumnDefinition("col", "")


class TestTableModel:
    """Test TableModel dataclass."""

    def test_basic_table(self):
        """Test creating basic table model."""
        model = TableModel(
            columns=[
                ColumnDefinition("id", "Int32"),
                ColumnDefinition("name", "String"),
            ],
            primary_key=["id"],
        )
        assert len(model.columns) == 2
        assert model.primary_key == ["id"]
        assert model.engine is None
        assert model.order_by is None
        assert model.version_column is None

    def test_table_with_engine(self):
        """Test table with ClickHouse engine."""
        model = TableModel(
            columns=[
                ColumnDefinition("id", "Int32"),
                ColumnDefinition("value", "Float64"),
            ],
            primary_key=["id"],
            engine="MergeTree",
            order_by=["id"],
        )
        assert model.engine == "MergeTree"
        assert model.order_by == ["id"]

    def test_composite_primary_key(self):
        """Test table with composite primary key."""
        model = TableModel(
            columns=[
                ColumnDefinition("experiment", "String"),
                ColumnDefinition("end_ts", "DateTime64(3, 'UTC')"),
                ColumnDefinition("value", "Float64"),
            ],
            primary_key=["experiment", "end_ts"],
            engine="MergeTree",
            order_by=["experiment", "end_ts"],
        )
        assert model.primary_key == ["experiment", "end_ts"]

    def test_no_columns(self):
        """Test error on empty columns."""
        with pytest.raises(ValueError, match="at least one column"):
            TableModel(columns=[], primary_key=["id"])

    def test_no_primary_key(self):
        """Test error on missing primary key."""
        with pytest.raises(ValueError, match="must have a primary key"):
            TableModel(
                columns=[ColumnDefinition("id", "Int32")],
                primary_key=[],
            )

    def test_invalid_primary_key_column(self):
        """Test error when primary key column doesn't exist."""
        with pytest.raises(ValueError, match="Primary key column 'missing'"):
            TableModel(
                columns=[ColumnDefinition("id", "Int32")],
                primary_key=["missing"],
            )

    def test_invalid_order_by_column(self):
        """Test error when order by column doesn't exist."""
        with pytest.raises(ValueError, match="ORDER BY column 'missing'"):
            TableModel(
                columns=[ColumnDefinition("id", "Int32")],
                primary_key=["id"],
                order_by=["missing"],
            )

    def test_version_column(self):
        """Test LWW version column (the created_at dedup mechanism)."""
        model = TableModel(
            columns=[
                ColumnDefinition("id", "Int32"),
                ColumnDefinition("created_at", "DateTime64(3, 'UTC')"),
            ],
            primary_key=["id"],
            engine="ReplacingMergeTree(created_at)",
            version_column="created_at",
        )
        assert model.version_column == "created_at"

    def test_invalid_version_column(self):
        """Test error when the version column doesn't exist."""
        with pytest.raises(ValueError, match="Version column 'created_at'"):
            TableModel(
                columns=[ColumnDefinition("id", "Int32")],
                primary_key=["id"],
                version_column="created_at",
            )

    def test_get_column(self):
        """Test getting column by name."""
        model = TableModel(
            columns=[
                ColumnDefinition("id", "Int32"),
                ColumnDefinition("name", "String"),
            ],
            primary_key=["id"],
        )

        col = model.get_column("id")
        assert col is not None
        assert col.name == "id"
        assert col.type == "Int32"

        col = model.get_column("name")
        assert col is not None
        assert col.name == "name"

        col = model.get_column("missing")
        assert col is None

    def test_nullable_columns(self):
        """Test model with nullable columns."""
        model = TableModel(
            columns=[
                ColumnDefinition("id", "Int32"),
                ColumnDefinition("value", "Nullable(Float64)", nullable=True),
                ColumnDefinition("error", "Nullable(String)", nullable=True),
            ],
            primary_key=["id"],
        )

        assert model.get_column("value").nullable is True
        assert model.get_column("error").nullable is True
        assert model.get_column("id").nullable is False
