"""Unit tests for the PostgreSQL and MySQL backends.

These are mock-based: the DB-API connection is faked so the tests run without a
real database or the psycopg2/pymysql drivers installed. They assert the SQL the
managers generate (DDL with enforced PK, version-aware upserts, plain DELETE,
the single-statement atomic lock claim) and the numpy → driver value coercion.
End-to-end behaviour against real servers is covered by the testcontainers
integration suite.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

import abkit.database.mysql_manager as mysql_mod
import abkit.database.postgres_manager as pg_mod
from abkit.core.models import ColumnDefinition, TableModel

# Local stand-ins for the WP3 _ab_* schemas: one versioned (LWW) table and one
# plain lock table, exercising the same shapes the internal tables will use.


def results_like_model() -> TableModel:
    return TableModel(
        columns=[
            ColumnDefinition("experiment", "String"),
            ColumnDefinition("metric", "String"),
            ColumnDefinition("end_ts", "DateTime64(3, 'UTC')"),
            ColumnDefinition("end_date", "Date"),
            ColumnDefinition("effect", "Nullable(Float64)", nullable=True),
            ColumnDefinition("method_params", "String"),
            ColumnDefinition("reject", "Bool"),
            ColumnDefinition("size_1", "UInt64"),
            ColumnDefinition("created_at", "DateTime64(3, 'UTC')"),
        ],
        primary_key=["experiment", "metric", "end_ts"],
        engine="ReplacingMergeTree(created_at)",
        order_by=["experiment", "metric", "end_ts"],
        version_column="created_at",
    )


def tasks_like_model() -> TableModel:
    return TableModel(
        columns=[
            ColumnDefinition("experiment", "String"),
            ColumnDefinition("scope", "String"),
            ColumnDefinition("process_type", "String"),
            ColumnDefinition("status", "String"),
            ColumnDefinition("started_at", "DateTime64(3, 'UTC')"),
            ColumnDefinition("updated_at", "DateTime64(3, 'UTC')"),
            ColumnDefinition("locked_by", "String"),
            ColumnDefinition("error_message", "Nullable(String)", nullable=True),
            ColumnDefinition("timeout_seconds", "Int32"),
        ],
        primary_key=["experiment", "scope", "process_type"],
        engine="MergeTree",
    )


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    @property
    def description(self):
        return self.conn.next_description

    @property
    def rowcount(self) -> int:
        return self.conn.next_rowcount

    def execute(self, sql: str, params=None) -> None:
        self.conn.executed.append((sql, params))

    def executemany(self, sql: str, seq) -> None:
        rows = list(seq)
        self.conn.executed.append((sql, rows))
        self.conn.next_rowcount = len(rows)

    def fetchall(self):
        return self.conn.next_result


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple] = []
        self.next_description = None
        self.next_result: list[tuple] = []
        self.next_rowcount = 0
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass

    # convenience: SQL of the last executed statement
    @property
    def last_sql(self) -> str:
        return self.executed[-1][0]


def _make_manager(backend: str, monkeypatch):
    conn = FakeConn()
    if backend == "postgres":
        monkeypatch.setattr(pg_mod, "PSYCOPG2_AVAILABLE", True)
        monkeypatch.setattr(pg_mod.PostgresDatabaseManager, "_connect", lambda self: conn)
        monkeypatch.setattr(pg_mod.PostgresDatabaseManager, "_ensure_locations", lambda self: None)
        mgr = pg_mod.PostgresDatabaseManager(
            database="db", internal_schema="abk", data_schema="public"
        )
    else:
        monkeypatch.setattr(mysql_mod, "PYMYSQL_AVAILABLE", True)
        monkeypatch.setattr(mysql_mod.MySQLDatabaseManager, "_connect", lambda self: conn)
        monkeypatch.setattr(mysql_mod.MySQLDatabaseManager, "_ensure_locations", lambda self: None)
        mgr = mysql_mod.MySQLDatabaseManager(internal_database="abk", data_database="analytics")
    return mgr, conn


@pytest.fixture(params=["postgres", "mysql"])
def backend(request):
    return request.param


@pytest.fixture
def mgr_conn(backend, monkeypatch):
    mgr, conn = _make_manager(backend, monkeypatch)
    return mgr, conn, backend


class TestDDL:
    def test_create_table_emits_pk_and_no_clickhouse_engine(self, mgr_conn):
        mgr, conn, backend = mgr_conn
        q = mgr._IDENT_QUOTE
        mgr.create_table("abk._ab_results", results_like_model())
        ddl = conn.last_sql
        assert f"PRIMARY KEY ({q}experiment{q}, {q}metric{q}, {q}end_ts{q})" in ddl
        assert "ENGINE" not in ddl and "ReplacingMergeTree" not in ddl and "ORDER BY" not in ddl
        # version/PK metadata is recorded for the insert path
        assert mgr._table_meta["_ab_results"] == (
            ["experiment", "metric", "end_ts"],
            "created_at",
        )

    def test_type_mapping_per_dialect(self, mgr_conn):
        mgr, conn, backend = mgr_conn
        q = mgr._IDENT_QUOTE
        mgr.create_table("abk._ab_results", results_like_model())
        ddl = conn.last_sql
        if backend == "postgres":
            assert f"{q}experiment{q} TEXT NOT NULL" in ddl
            assert f"{q}end_ts{q} TIMESTAMP(3) NOT NULL" in ddl
            assert f"{q}end_date{q} DATE NOT NULL" in ddl
            assert f"{q}effect{q} DOUBLE PRECISION" in ddl
            assert f"{q}size_1{q} INTEGER NOT NULL" in ddl
            assert f"{q}reject{q} BOOLEAN NOT NULL" in ddl
        else:
            # MySQL: PK String must be VARCHAR (TEXT can't be PK-indexed); the
            # JSON/text columns stay TEXT.
            assert f"{q}experiment{q} VARCHAR(255) NOT NULL" in ddl
            assert f"{q}method_params{q} TEXT NOT NULL" in ddl
            assert f"{q}end_ts{q} DATETIME(3) NOT NULL" in ddl
            assert f"{q}end_date{q} DATE NOT NULL" in ddl
            assert f"{q}effect{q} DOUBLE" in ddl
            assert f"{q}reject{q} TINYINT(1) NOT NULL" in ddl

    def test_nullable_value_column_has_no_not_null(self, mgr_conn):
        mgr, conn, _ = mgr_conn
        q = mgr._IDENT_QUOTE
        mgr.create_table("abk._ab_results", results_like_model())
        ddl = conn.last_sql
        # effect is Nullable(Float64) -> no NOT NULL
        effect_line = next(
            line for line in ddl.splitlines() if line.strip().startswith(f"{q}effect{q} ")
        )
        assert "NOT NULL" not in effect_line

    def test_unmappable_type_raises(self, mgr_conn):
        mgr, _, _ = mgr_conn
        model = TableModel(
            columns=[
                ColumnDefinition("unit", "String"),
                ColumnDefinition("state", "AggregateFunction(sum, Float64)"),
            ],
            primary_key=["unit"],
        )
        with pytest.raises(ValueError, match="Cannot map column type"):
            mgr.create_table("abk._ab_unit_state", model)


class TestInsertConflict:
    def _insert_one(self, mgr):
        data = {
            "experiment": np.array(["signup"]),
            "metric": np.array(["arpu"]),
            "end_ts": np.array([np.datetime64("2024-01-02T00:00:00", "ms")]),
            "end_date": np.array(["2024-01-01"]),
            "effect": np.array([0.5]),
            "method_params": np.array(['{"test_type":"relative"}']),
            "reject": np.array([True]),
            "size_1": np.array([100], dtype=np.int64),
            "created_at": np.array([np.datetime64("2024-01-02T03:04:05", "ms")]),
        }
        mgr.insert_batch("abk._ab_results", data, conflict_strategy="ignore")

    def test_versioned_ignore_is_last_writer_wins_upsert(self, mgr_conn):
        mgr, conn, backend = mgr_conn
        mgr.create_table("abk._ab_results", results_like_model())
        self._insert_one(mgr)
        sql = conn.last_sql
        q = mgr._IDENT_QUOTE
        if backend == "postgres":
            assert (
                f"ON CONFLICT ({q}experiment{q}, {q}metric{q}, {q}end_ts{q}) DO UPDATE SET" in sql
            )
            assert f"WHERE _ab_results.{q}created_at{q} <= EXCLUDED.{q}created_at{q}" in sql
        else:
            assert "AS new ON DUPLICATE KEY UPDATE" in sql
            assert f"IF(new.{q}created_at{q} >= {q}_ab_results{q}.{q}created_at{q}" in sql
        assert conn.commits >= 1

    def test_insert_coerces_nan_to_none(self, mgr_conn):
        mgr, conn, _ = mgr_conn
        mgr.create_table("abk._ab_results", results_like_model())
        data = {
            "experiment": np.array(["signup"]),
            "metric": np.array(["arpu"]),
            "end_ts": np.array([np.datetime64("2024-01-02T00:00:00", "ms")]),
            "end_date": np.array(["2024-01-01"]),
            "effect": np.array([np.nan]),
            "method_params": np.array(["{}"]),
            "reject": np.array([False]),
            "size_1": np.array([100], dtype=np.int64),
            "created_at": np.array([np.datetime64("2024-01-02T03:04:05", "ms")]),
        }
        mgr.insert_batch("abk._ab_results", data, conflict_strategy="ignore")
        rows = conn.executed[-1][1]
        # the effect cell (index 4) was NaN -> None
        assert rows[0][4] is None


class TestDeleteAndUpsert:
    def test_delete_rows_uses_plain_delete(self, mgr_conn):
        mgr, conn, _ = mgr_conn
        conn.next_rowcount = 3
        n = mgr.delete_rows("abk._ab_exposures", "experiment = %(e)s", {"e": "signup"}, sync=True)
        sql = conn.last_sql
        assert sql.startswith("DELETE FROM abk._ab_exposures WHERE")
        assert "ALTER TABLE" not in sql and "mutations_sync" not in sql
        assert n == 3

    def test_upsert_record_deletes_then_inserts_one_commit(self, mgr_conn):
        mgr, conn, _ = mgr_conn
        mgr.create_table("abk._ab_tasks", tasks_like_model())
        conn.executed.clear()
        conn.commits = 0
        now = np.datetime64("2024-01-01T00:00:00", "ms")
        mgr.upsert_record(
            "abk._ab_tasks",
            key_columns={"experiment": "signup", "scope": "pipeline", "process_type": "run"},
            data={
                "experiment": np.array(["signup"]),
                "scope": np.array(["pipeline"]),
                "process_type": np.array(["run"]),
                "status": np.array(["completed"]),
                "started_at": np.array([now]),
                "updated_at": np.array([now]),
                "locked_by": np.array(["host:1"]),
                "error_message": np.array([None]),
                "timeout_seconds": np.array([3600], dtype=np.int32),
            },
        )
        kinds = [sql.split()[0] for sql, _ in conn.executed]
        assert kinds[0] == "DELETE"
        assert kinds[1] == "INSERT"
        # delete + insert committed together, once (atomic)
        assert conn.commits == 1

    def test_get_max_timestamp_null_is_none(self, mgr_conn):
        mgr, conn, _ = mgr_conn
        conn.next_description = [("last_ts",)]
        conn.next_result = [(None,)]
        assert mgr.get_max_timestamp("abk._ab_results", timestamp_column="end_ts") is None

    def test_get_max_timestamp_returns_value_with_where(self, mgr_conn):
        mgr, conn, _ = mgr_conn
        dt = datetime(2024, 1, 1, 12, 0, 0)
        conn.next_description = [("last_ts",)]
        conn.next_result = [(dt,)]
        got = mgr.get_max_timestamp(
            "abk._ab_results",
            "experiment = %(e)s",
            {"e": "signup"},
            timestamp_column="end_ts",
        )
        assert got == dt
        assert "WHERE experiment = %(e)s" in conn.last_sql

    def test_get_max_timestamp_no_filter_has_no_where(self, mgr_conn):
        mgr, conn, _ = mgr_conn
        conn.next_description = [("last_ts",)]
        conn.next_result = [(None,)]
        mgr.get_max_timestamp("abk._ab_results", timestamp_column="end_ts")
        assert "WHERE" not in conn.last_sql


class TestTryAcquireLock:
    KEY = {"experiment": "signup", "scope": "pipeline", "process_type": "run"}

    def _row(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        return {
            **self.KEY,
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "locked_by": "host:1:abc",
            "error_message": None,
            "timeout_seconds": 3600,
        }

    def test_claim_sql_shape(self, mgr_conn):
        mgr, conn, backend = mgr_conn
        conn.next_rowcount = 1
        claimed = mgr.try_acquire_lock("abk._ab_tasks", self.KEY, self._row(), timeout_seconds=1800)
        assert claimed is True
        sql, params = conn.executed[-1]
        q = mgr._IDENT_QUOTE
        if backend == "postgres":
            assert f"ON CONFLICT ({q}experiment{q}, {q}scope{q}, {q}process_type{q})" in sql
            assert "DO UPDATE SET" in sql
            assert f"WHERE _ab_tasks.{q}status{q} <> %(_abk_running)s" in sql
            assert f"OR _ab_tasks.{q}started_at{q} < %(_abk_stale_before)s" in sql
        else:
            assert "AS new ON DUPLICATE KEY UPDATE" in sql
            assert "@abk_claim :=" in sql
            # every non-key column is guarded by the latched verdict
            assert sql.count("IF(") == 6
        assert params["_abk_running"] == "running"
        assert isinstance(params["_abk_stale_before"], datetime)
        # single statement, single commit
        assert conn.commits == 1

    def test_claim_denied_when_no_rows_affected(self, mgr_conn):
        mgr, conn, _ = mgr_conn
        conn.next_rowcount = 0
        assert mgr.try_acquire_lock("abk._ab_tasks", self.KEY, self._row()) is False

    def test_mysql_updated_rowcount_two_is_claimed(self, backend, monkeypatch):
        if backend != "mysql":
            pytest.skip("MySQL affected-rows semantics")
        mgr, conn = _make_manager("mysql", monkeypatch)
        conn.next_rowcount = 2  # ON DUPLICATE KEY UPDATE that changed the row
        assert mgr.try_acquire_lock("abk._ab_tasks", self.KEY, self._row()) is True

    def test_row_missing_key_column_raises(self, mgr_conn):
        mgr, _, _ = mgr_conn
        row = self._row()
        del row["scope"]
        with pytest.raises(ValueError, match="must contain key column"):
            mgr.try_acquire_lock("abk._ab_tasks", self.KEY, row)

    def test_row_key_value_mismatch_raises(self, mgr_conn):
        mgr, _, _ = mgr_conn
        row = self._row()
        row["experiment"] = "other"
        with pytest.raises(ValueError, match="!= key_columns value"):
            mgr.try_acquire_lock("abk._ab_tasks", self.KEY, row)

    def test_all_key_row_raises(self, mgr_conn):
        mgr, _, _ = mgr_conn
        with pytest.raises(ValueError, match="at least one non-key column"):
            mgr.try_acquire_lock("abk._ab_tasks", self.KEY, dict(self.KEY))

    def test_mysql_condition_latched_in_first_set_clause(self, monkeypatch):
        """The @abk_claim latch must be assigned in the FIRST SET clause so later
        clauses cannot see partially-updated condition columns."""
        mgr, conn = _make_manager("mysql", monkeypatch)
        conn.next_rowcount = 1
        mgr.try_acquire_lock("abk._ab_tasks", self.KEY, self._row())
        sql = conn.executed[-1][0]
        update_part = sql.split("ON DUPLICATE KEY UPDATE", 1)[1]
        first_clause = update_part.split(", `")[0]
        assert "@abk_claim :=" in first_clause


class TestCoerce:
    def test_numpy_datetime_to_naive_utc(self, mgr_conn):
        mgr, _, _ = mgr_conn
        out = mgr._coerce(np.datetime64("2024-03-04T05:06:07", "ms"))
        assert out == datetime(2024, 3, 4, 5, 6, 7)
        assert out.tzinfo is None

    def test_scalar_conversions(self, mgr_conn):
        mgr, _, _ = mgr_conn
        assert mgr._coerce(np.int32(7)) == 7 and isinstance(mgr._coerce(np.int32(7)), int)
        assert mgr._coerce(np.bool_(True)) is True
        assert mgr._coerce(np.float64("nan")) is None
        assert mgr._coerce(None) is None
        assert mgr._coerce("text") == "text"
