"""Shared reporting fixtures: the parametrized in-memory tables manager.

Both backend flavours run every test: the clickhouse-like leg keeps duplicate
PK rows until a FINAL read (ReplacingMergeTree semantics), so payload reads
inherit the missing-dedup guard for free.
"""

from __future__ import annotations

import pytest

from abkit.database.internal_tables import InternalTablesManager
from tests._helpers.fake_db import FakeDatabaseManager


@pytest.fixture(params=[False, True], ids=["sql-like", "clickhouse-like"])
def tables(request) -> InternalTablesManager:
    manager = InternalTablesManager(FakeDatabaseManager(clickhouse_like=request.param))
    manager.ensure_tables()
    return manager
