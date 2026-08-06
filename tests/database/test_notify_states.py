"""NTF-3 storage: ``_ab_notify_states`` (m12-implementation-plan.md NTF-3).

The accessor half — the RULE it feeds lives in ``tests/notify/test_cooldown.py``.
Run over both fake-manager flavours, because the clickhouse-like one keeps
pre-merge duplicates and this row decides whether a message is sent at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from abkit.database.internal_tables import InternalTablesManager
from abkit.database.tables import TABLE_NOTIFY_STATES, get_notify_states_table_model
from tests._helpers.fake_db import FakeDatabaseManager

KEY = ("exp", "revenue", "control", "treatment", "mcid-1")
NOW = datetime(2026, 8, 3, 12, 0, 0)


@pytest.fixture(params=[False, True], ids=["sql-like", "clickhouse-like"])
def tables(request) -> InternalTablesManager:
    manager = InternalTablesManager(FakeDatabaseManager(clickhouse_like=request.param))
    manager.ensure_tables()
    return manager


class TestSchema:
    def test_the_key_is_the_full_comparison_identity(self):
        """§0.4 point 2: a re-tuned comparison starts a fresh announcement
        history instead of inheriting the previous method's."""
        model = get_notify_states_table_model()

        assert model.primary_key == [
            "experiment",
            "metric",
            "name_1",
            "name_2",
            "method_config_id",
        ]
        assert model.order_by == model.primary_key
        assert model.version_column == "updated_at"
        assert model.version_column in model.engine

    def test_the_srm_flag_is_part_of_the_stored_signature(self):
        """Deduping on the verdict word alone would swallow an SRM breach on a
        pair that was already INCONCLUSIVE."""
        model = get_notify_states_table_model()

        assert model.get_column("last_srm_flag") is not None
        assert not model.get_column("last_srm_flag").nullable

    def test_the_rollup_column_is_additively_migratable(self):
        """m14 DEC-4 adds `last_rollup` to a table `0.7.0` already ships, so an
        installed project meets it through `ensure_columns` — which REFUSES a
        NOT-NULL/no-default addition (m13 STAT-6, where exactly that shape
        would have killed the first `0.8.0` run of every install). Nullable is
        also the honest value: a row written before `0.9.0` announced no rollup.
        """
        column = get_notify_states_table_model().get_column("last_rollup")

        assert column is not None
        assert column.nullable, "ensure_columns refuses a NOT-NULL column with no default"
        assert column.default is None

    def test_no_recovery_column_leaked_in_from_the_donor(self):
        """abkit has no recovery concept — a verdict flipping back is just
        another flip."""
        names = {col.name for col in get_notify_states_table_model().columns}

        assert "last_recovery_sent" not in names


class TestRoundTrip:
    def test_an_unseen_key_answers_with_the_empty_state(self, tables):
        state = tables.get_notify_state(*KEY)

        assert state == {
            "last_verdict": None,
            "last_rollup": None,
            "last_srm_flag": False,
            "last_notified_at": None,
            "notify_count": 0,
        }

    def test_recording_round_trips_and_counts(self, tables):
        tables.record_notification(*KEY, verdict="WIN", srm_flag=False, notified_at=NOW)

        state = tables.get_notify_state(*KEY)
        assert state["last_verdict"] == "WIN"
        assert state["last_srm_flag"] is False
        assert state["last_notified_at"] == NOW
        assert state["notify_count"] == 1

    def test_a_second_record_replaces_the_row_and_increments(self, tables):
        tables.record_notification(*KEY, verdict="WIN", srm_flag=False, notified_at=NOW)
        later = NOW + timedelta(hours=1)

        tables.record_notification(*KEY, verdict="LOSE", srm_flag=True, notified_at=later)

        state = tables.get_notify_state(*KEY)
        assert (state["last_verdict"], state["last_srm_flag"]) == ("LOSE", True)
        assert state["last_notified_at"] == later
        assert state["notify_count"] == 2

    def test_a_different_method_config_id_is_a_different_track(self, tables):
        tables.record_notification(*KEY, verdict="WIN", srm_flag=False, notified_at=NOW)
        retuned = (*KEY[:4], "mcid-2")

        assert tables.get_notify_state(*retuned)["notify_count"] == 0
        assert tables.get_notify_state(*KEY)["notify_count"] == 1

    def test_a_different_arm_pair_is_a_different_track(self, tables):
        tables.record_notification(*KEY, verdict="WIN", srm_flag=False, notified_at=NOW)
        other_arm = ("exp", "revenue", "control", "t2", "mcid-1")

        assert tables.get_notify_state(*other_arm)["notify_count"] == 0

    def test_purging_the_experiment_resets_the_dedup(self, tables):
        """`abk clean --orphaned-experiments` must clear it — and not for
        tidiness: a deleted name that is later REUSED would otherwise inherit
        the old experiment's history and have its first verdict deduped away."""
        tables.record_notification(*KEY, verdict="WIN", srm_flag=False, notified_at=NOW)

        tables.purge_experiment("exp")

        assert tables.get_notify_state(*KEY)["notify_count"] == 0

    def test_the_table_is_in_the_experiment_keyed_sweep(self):
        from abkit.database.internal_tables._maintenance import EXPERIMENT_KEYED_TABLES

        assert TABLE_NOTIFY_STATES in EXPERIMENT_KEYED_TABLES


class TestMissingTable:
    def test_a_pre_m12_project_reads_as_empty_and_never_raises(self):
        """`ensure_tables` was never called (or the install predates m12): the
        dedup must degrade to "send", never to a crash inside the notify path
        and never to a silent skip."""
        bare = InternalTablesManager(FakeDatabaseManager())

        assert bare.notify_states_table_exists() is False
        assert bare.get_notify_state(*KEY)["notify_count"] == 0

    def test_ensure_tables_creates_it(self, tables):
        assert tables.notify_states_table_exists() is True
        assert tables._manager.table_exists(TABLE_NOTIFY_STATES)
