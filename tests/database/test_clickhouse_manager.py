"""Unit tests for the ClickHouse manager's new M2 surface.

Mock-based (no clickhouse-driver needed): a fake client records executed
statements and serves scripted results. Covers the advisory lock protocol
(staleness check → sync delete → insert → read-back winner tie-break), the
epoch-sentinel in get_max_timestamp, and the sync flag on upsert_record.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

import abkit.database.clickhouse_manager as ch_mod
from abkit.database.clickhouse_manager import ClickHouseDatabaseManager
from abkit.utils.datetime_utils import now_utc_naive


class FakeClient:
    """Scripted stand-in for clickhouse_driver.Client."""

    def __init__(self, **kwargs) -> None:
        self.executed: list[tuple[str, object]] = []
        # queue of (rows, columns_with_types) served to with_column_types calls
        self.select_responses: list[tuple[list, list]] = []

    def execute(self, query: str, params=None, with_column_types: bool = False):
        self.executed.append((query, params))
        if with_column_types:
            if self.select_responses:
                return self.select_responses.pop(0)
            return ([], [])
        return []

    def disconnect(self) -> None:
        pass

    @property
    def sqls(self) -> list[str]:
        return [q for q, _ in self.executed]


@pytest.fixture
def mgr(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(ch_mod, "CLICKHOUSE_AVAILABLE", True)
    monkeypatch.setattr(ch_mod, "Client", lambda **kwargs: fake, raising=False)
    manager = ClickHouseDatabaseManager(internal_database="abk_int", data_database="abk_data")
    return manager, fake


KEY = {"experiment": "signup", "scope": "pipeline", "process_type": "run"}
#: column order of lock_row() rows as inserted (dict order is insertion order)
KEY_COLUMNS = [
    "experiment",
    "scope",
    "process_type",
    "status",
    "started_at",
    "updated_at",
    "locked_by",
    "error_message",
    "timeout_seconds",
]


def lock_row(token: str = "host:1:abc", started_at: datetime | None = None) -> dict:
    now = started_at or now_utc_naive()
    return {
        **KEY,
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "locked_by": token,
        "error_message": None,
        "timeout_seconds": 3600,
    }


class TestTryAcquireLockAdvisory:
    TABLE = "abk_int._ab_tasks"

    def test_clean_claim_no_existing_row(self, mgr):
        manager, fake = mgr
        row = lock_row()
        fake.select_responses = [
            ([], []),  # staleness check: no row
            ([(row["locked_by"], row["started_at"])], [("t", "String"), ("hb", "DateTime")]),
        ]
        assert (
            manager.try_acquire_lock(
                self.TABLE, KEY, row, token_column="locked_by", settle_seconds=0
            )
            is True
        )
        # protocol order: SELECT, sync DELETE, INSERT, SELECT
        kinds = [q.split()[0] for q in fake.sqls[2:]]  # skip the 2 ensure-database DDLs
        assert kinds == ["SELECT", "ALTER", "INSERT", "SELECT"]
        delete_sql = fake.sqls[3]
        assert "mutations_sync = 1" in delete_sql
        # the claim DELETE is CONDITIONAL: it can clear stale/finished/own rows
        # but never a rival's live running claim (review finding: an
        # unconditional delete let a racer erase a confirmed claim)
        assert "status <> %(_abk_running)s" in delete_sql
        assert "started_at < %(_abk_stale_before)s" in delete_sql
        assert "locked_by = %(_abk_token)s" in delete_sql

    def test_heartbeat_is_stamped_at_insert_time(self, mgr):
        """The winner rule relies on heartbeat order tracking INSERT order —
        a row assembled before the slow sync delete must not carry its
        assembly-time heartbeat into the race."""
        manager, fake = mgr
        stale_assembly = now_utc_naive() - timedelta(seconds=30)
        row = lock_row(started_at=stale_assembly)
        fake.select_responses = [
            ([], []),
            ([(row["locked_by"], now_utc_naive())], [("t", "String"), ("hb", "DateTime")]),
        ]
        manager.try_acquire_lock(self.TABLE, KEY, row, token_column="locked_by", settle_seconds=0)
        insert_call = next(p for q, p in fake.executed if q.startswith("INSERT"))
        inserted_hb = insert_call[0][KEY_COLUMNS.index("started_at")]
        assert inserted_hb > stale_assembly

    def test_loser_cleans_up_its_own_row(self, mgr):
        manager, fake = mgr
        mine = lock_row(token="zzz")
        winner_hb = mine["started_at"] - timedelta(seconds=5)
        fake.select_responses = [
            ([], []),
            (
                [("aaa", winner_hb), ("zzz", now_utc_naive())],
                [("t", "String"), ("hb", "DateTime")],
            ),
        ]
        assert (
            manager.try_acquire_lock(
                self.TABLE, KEY, mine, token_column="locked_by", settle_seconds=0
            )
            is False
        )
        cleanup_sql = fake.sqls[-1]
        assert cleanup_sql.startswith("ALTER TABLE")
        assert "locked_by = %(_abk_token)s" in cleanup_sql

    def test_denied_by_live_running_row(self, mgr):
        manager, fake = mgr
        fresh_hb = now_utc_naive() - timedelta(seconds=10)
        fake.select_responses = [
            ([("running", fresh_hb)], [("s", "String"), ("hb", "DateTime")]),
        ]
        assert (
            manager.try_acquire_lock(
                self.TABLE, KEY, lock_row(), token_column="locked_by", settle_seconds=0
            )
            is False
        )
        # nothing was deleted or inserted
        assert not any(q.startswith(("ALTER", "INSERT")) for q in fake.sqls[2:])

    def test_stale_running_row_is_stolen(self, mgr):
        manager, fake = mgr
        stale_hb = now_utc_naive() - timedelta(seconds=7200)
        row = lock_row()
        fake.select_responses = [
            ([("running", stale_hb)], [("s", "String"), ("hb", "DateTime")]),
            ([(row["locked_by"], row["started_at"])], [("t", "String"), ("hb", "DateTime")]),
        ]
        assert (
            manager.try_acquire_lock(
                self.TABLE,
                KEY,
                row,
                timeout_seconds=3600,
                token_column="locked_by",
                settle_seconds=0,
            )
            is True
        )

    def test_completed_row_is_claimable(self, mgr):
        manager, fake = mgr
        row = lock_row()
        fake.select_responses = [
            ([("completed", now_utc_naive())], [("s", "String"), ("hb", "DateTime")]),
            ([(row["locked_by"], row["started_at"])], [("t", "String"), ("hb", "DateTime")]),
        ]
        assert (
            manager.try_acquire_lock(
                self.TABLE, KEY, row, token_column="locked_by", settle_seconds=0
            )
            is True
        )

    def test_race_earlier_heartbeat_wins(self, mgr):
        manager, fake = mgr
        mine = lock_row(token="host:me")
        other_hb = mine["started_at"] - timedelta(milliseconds=5)  # other started earlier
        fake.select_responses = [
            ([], []),
            (
                [("host:other", other_hb), ("host:me", mine["started_at"])],
                [("t", "String"), ("hb", "DateTime")],
            ),
        ]
        assert (
            manager.try_acquire_lock(
                self.TABLE, KEY, mine, token_column="locked_by", settle_seconds=0
            )
            is False
        )

    def test_race_tie_broken_by_token(self, mgr):
        manager, fake = mgr
        mine = lock_row(token="aaa")
        fake.select_responses = [
            ([], []),
            (
                [("zzz", mine["started_at"]), ("aaa", mine["started_at"])],
                [("t", "String"), ("hb", "DateTime")],
            ),
        ]
        assert (
            manager.try_acquire_lock(
                self.TABLE, KEY, mine, token_column="locked_by", settle_seconds=0
            )
            is True
        )

    def test_token_column_required(self, mgr):
        manager, _ = mgr
        with pytest.raises(ValueError, match="requires token_column"):
            manager.try_acquire_lock(self.TABLE, KEY, lock_row())

    def test_row_must_contain_token(self, mgr):
        manager, _ = mgr
        row = lock_row()
        del row["locked_by"]
        with pytest.raises(ValueError, match="token column"):
            manager.try_acquire_lock(
                self.TABLE, KEY, row, token_column="locked_by", settle_seconds=0
            )


class TestGetMaxTimestamp:
    def test_epoch_sentinel_is_none(self, mgr):
        manager, fake = mgr
        fake.select_responses = [([(datetime(1970, 1, 1),)], [("last_ts", "DateTime")])]
        assert manager.get_max_timestamp("abk_int._ab_results", timestamp_column="end_ts") is None

    def test_value_returned_and_where_rendered(self, mgr):
        manager, fake = mgr
        dt = datetime(2024, 5, 6, 7, 8, 9)
        fake.select_responses = [([(dt,)], [("last_ts", "DateTime")])]
        got = manager.get_max_timestamp(
            "abk_int._ab_results",
            "experiment = %(e)s",
            {"e": "signup"},
            timestamp_column="end_ts",
        )
        assert got == dt
        assert "WHERE experiment = %(e)s" in fake.sqls[-1]
        assert "max(end_ts)" in fake.sqls[-1]


class TestUpsertRecordSync:
    def test_sync_flag_reaches_delete_mutation(self, mgr):
        manager, fake = mgr
        data = {
            "experiment": np.array(["signup"], dtype=object),
            "status": np.array(["done"], dtype=object),
        }
        manager.upsert_record("abk_int._ab_experiments", {"experiment": "signup"}, data, sync=True)
        delete_sql = next(q for q in fake.sqls if q.startswith("ALTER TABLE"))
        assert "mutations_sync = 1" in delete_sql

    def test_default_is_async_delete(self, mgr):
        manager, fake = mgr
        data = {
            "experiment": np.array(["signup"], dtype=object),
            "status": np.array(["done"], dtype=object),
        }
        manager.upsert_record("abk_int._ab_experiments", {"experiment": "signup"}, data)
        delete_sql = next(q for q in fake.sqls if q.startswith("ALTER TABLE"))
        assert "mutations_sync" not in delete_sql
