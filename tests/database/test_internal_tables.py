"""Behavioural tests for the internal-tables mixins on the in-memory backend.

Covers the WP3 must-fix surface: the lock state machine, the strictly-
monotonic version source, exposure replacement, the §5.2 unit-state twice-run
invariant, the results contract write path and set-reader anti-join source,
and the maintenance purge exclusions.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

import numpy as np
import pytest
from fake_db import FakeDatabaseManager

import abkit.database.internal_tables._base as base_mod
from abkit.database.internal_tables import InternalTablesManager, compute_column_set_id
from abkit.database.internal_tables._results import RESULT_COLUMNS
from abkit.database.tables import TABLE_RESULTS, TABLE_TASKS
from abkit.utils.datetime_utils import now_utc_naive


@pytest.fixture(params=[False, True], ids=["sql-like", "clickhouse-like"])
def backend(request):
    return FakeDatabaseManager(clickhouse_like=request.param)


@pytest.fixture
def tables(backend):
    manager = InternalTablesManager(backend)
    manager.ensure_tables()
    return manager


class TestEnsureTables:
    def test_creates_and_registers_all_six(self, backend):
        manager = InternalTablesManager(backend)
        manager.ensure_tables()
        for table in (
            "_ab_experiments",
            "_ab_exposures",
            "_ab_unit_state",
            "_ab_results",
            "_ab_aa_runs",
            "_ab_tasks",
        ):
            assert backend.table_exists(table), table
            assert backend._model(table) is not None, table

    def test_idempotent(self, tables):
        tables.ensure_tables()  # second call must not raise or duplicate
        assert len(tables._manager._rows) == 6


class TestNextVersionTs:
    def test_strictly_increasing_within_same_millisecond(self, tables, monkeypatch):
        frozen = datetime(2024, 6, 1, 12, 0, 0, 123000)
        monkeypatch.setattr(base_mod, "now_utc_naive", lambda: frozen)
        versions = [tables.next_version_ts() for _ in range(5)]
        assert versions == sorted(set(versions)), "must be strictly increasing & distinct"
        assert versions[0] == frozen
        assert versions[1] == frozen + timedelta(milliseconds=1)

    def test_thread_safety_all_distinct(self, tables):
        out: list[datetime] = []
        lock = threading.Lock()

        def grab():
            for _ in range(50):
                v = tables.next_version_ts()
                with lock:
                    out.append(v)

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(out) == len(set(out)) == 400


class TestTasksLock:
    def test_acquire_release_cycle(self, tables):
        assert tables.acquire_lock("exp1") is True
        assert tables.acquire_lock("exp1") is False  # actively held
        assert tables.check_lock("exp1") is not None
        tables.release_lock("exp1", "pipeline", "run", status="completed")
        assert tables.check_lock("exp1") is None
        assert tables.acquire_lock("exp1") is True  # re-acquirable

    def test_distinct_experiments_do_not_contend(self, tables):
        assert tables.acquire_lock("exp1") is True
        assert tables.acquire_lock("exp2") is True

    def test_stale_lock_is_stolen(self, tables, backend):
        # Plant a running row whose heartbeat is 2h old, timeout 1h.
        stale_row = tables._task_row("exp1", "pipeline", "run", status="running")
        stale_row["started_at"] = now_utc_naive() - timedelta(hours=2)
        full = backend.get_full_table_name(TABLE_TASKS)
        backend._store(full).append(stale_row)
        assert tables.acquire_lock("exp1", timeout_seconds=3600) is True

    def test_check_lock_reports_stale_as_free_but_ignore_timeout_sees_it(self, tables, backend):
        stale_row = tables._task_row("exp1", "pipeline", "run", status="running")
        stale_row["started_at"] = now_utc_naive() - timedelta(hours=2)
        stale_row["timeout_seconds"] = 60
        backend._store(backend.get_full_table_name(TABLE_TASKS)).append(stale_row)
        assert tables.check_lock("exp1") is None
        assert tables.check_lock("exp1", ignore_timeout=True) is not None

    def test_clear_lock(self, tables):
        assert tables.clear_lock("exp1") is False  # nothing held
        tables.acquire_lock("exp1")
        assert tables.clear_lock("exp1") is True
        assert tables.check_lock("exp1") is None

    def test_force_takes_over_live_lock(self, tables):
        assert tables.acquire_lock("exp1") is True
        assert tables.acquire_lock("exp1", force=True) is True
        # forced owner holds a running lock
        assert tables.check_lock("exp1") is not None

    def test_release_is_ownership_checked(self, tables, backend):
        """A run whose lock was legitimately stolen must NOT wipe the new
        owner's live row on exit (review finding)."""
        assert tables.acquire_lock("exp1", timeout_seconds=1) is True
        # the second manager instance steals the stale lock
        thief = InternalTablesManager(backend)
        stale = backend._store(backend.get_full_table_name(TABLE_TASKS))
        stale[-1]["started_at"] = now_utc_naive() - timedelta(hours=2)
        assert thief.acquire_lock("exp1", timeout_seconds=3600) is True

        # the original holder finishes: release must be refused
        released = tables.release_lock("exp1", "pipeline", "run", status="completed")
        assert released is False
        rows = backend._store(backend.get_full_table_name(TABLE_TASKS))
        assert rows[-1]["status"] == "running"  # the thief's row survives

        # the rightful owner releases fine
        assert thief.release_lock("exp1", "pipeline", "run", status="completed") is True

    def test_release_without_acquire_is_a_noop(self, tables):
        assert tables.release_lock("ghost", "pipeline", "run", status="completed") is False

    def test_clear_lock_is_forced(self, tables, backend):
        """abk unlock deliberately bypasses the ownership check."""
        other = InternalTablesManager(backend)
        other.acquire_lock("exp1")
        assert tables.clear_lock("exp1") is True

    def test_release_failed_records_error(self, tables, backend):
        tables.acquire_lock("exp1")
        tables.release_lock("exp1", "pipeline", "run", status="failed", error_message="boom")
        rows = backend._store(backend.get_full_table_name(TABLE_TASKS))
        assert rows[-1]["status"] == "failed"
        assert rows[-1]["error_message"] == "boom"


class TestExposures:
    def _cohort(self, n=4, variants=("control", "treatment")):
        return {
            "unit_id": np.array([f"u{i}" for i in range(n)], dtype=object),
            "variant": np.array([variants[i % len(variants)] for i in range(n)], dtype=object),
            "exposure_ts": np.array(
                [datetime(2024, 1, 1, 10, 0, i) for i in range(n)], dtype=object
            ),
        }

    def test_replace_and_counts(self, tables):
        written = tables.replace_exposures("exp1", self._cohort(4))
        assert written == 4
        assert tables.get_exposure_counts("exp1") == {"control": 2, "treatment": 2}
        assert tables.count_exposures("exp1") == 4

    def test_replace_is_idempotent_and_removes_old_cohort(self, tables):
        tables.replace_exposures("exp1", self._cohort(10))
        tables.replace_exposures("exp1", self._cohort(4))
        assert tables.count_exposures("exp1") == 4

    def test_missing_required_column_raises(self, tables):
        with pytest.raises(ValueError, match="missing columns"):
            tables.replace_exposures("exp1", {"unit_id": np.array(["u1"], dtype=object)})

    def test_first_exposure_ts(self, tables):
        tables.replace_exposures("exp1", self._cohort(3))
        assert tables.get_first_exposure_ts("exp1") == datetime(2024, 1, 1, 10, 0, 0)


class TestUnitState:
    DAY = date(2024, 1, 5)

    def _day_state(self, n=3, scale=1.0):
        return {
            "unit_id": np.array([f"u{i}" for i in range(n)], dtype=object),
            "n": np.array([2] * n, dtype=np.int64),
            "sum_value": np.array([1.5 * scale] * n, dtype=np.float64),
            "sum_value_sq": np.array([2.25 * scale] * n, dtype=np.float64),
        }

    def test_twice_run_invariant(self, tables):
        """§5.2: running the state stage twice for one day leaves aggregates unchanged."""
        cs_id = compute_column_set_id("db.revenue", {"value": "gross_usd"})
        tables.replace_day_state("db.revenue", cs_id, self.DAY, self._day_state())
        first = tables.sum_moments("db.revenue", cs_id, self.DAY, self.DAY)
        tables.replace_day_state("db.revenue", cs_id, self.DAY, self._day_state())
        second = tables.sum_moments("db.revenue", cs_id, self.DAY, self.DAY)
        assert first == second
        assert second["sum_value"] == pytest.approx(4.5)

    def test_replace_heals_a_shrunken_batch(self, tables):
        cs_id = compute_column_set_id("db.revenue", {"value": "gross_usd"})
        tables.replace_day_state("db.revenue", cs_id, self.DAY, self._day_state(5))
        tables.replace_day_state("db.revenue", cs_id, self.DAY, self._day_state(2))
        moments = tables.sum_moments("db.revenue", cs_id, self.DAY, self.DAY)
        assert moments["n"] == 4  # 2 units × 2 events, not 5+2

    def test_day_range_aggregation(self, tables):
        cs_id = compute_column_set_id("db.revenue", {"value": "gross_usd"})
        for offset in range(3):
            tables.replace_day_state(
                "db.revenue", cs_id, self.DAY + timedelta(days=offset), self._day_state(1)
            )
        moments = tables.sum_moments("db.revenue", cs_id, self.DAY, self.DAY + timedelta(days=1))
        assert moments["sum_value"] == pytest.approx(3.0)  # 2 of 3 days

    def test_unknown_moment_column_raises(self, tables):
        with pytest.raises(ValueError, match="unknown unit-state moment"):
            tables.replace_day_state(
                "db.revenue",
                "abc",
                self.DAY,
                {"unit_id": np.array(["u1"], dtype=object), "bogus": np.array([1.0])},
            )

    def test_column_set_id_identity(self):
        a = compute_column_set_id("db.revenue", {"value": "gross_usd", "covariate": "prev"})
        b = compute_column_set_id("db.revenue", {"covariate": "prev", "value": "gross_usd"})
        c = compute_column_set_id("db.revenue", {"value": "net_usd", "covariate": "prev"})
        assert a == b  # dict order must not matter
        assert a != c
        assert len(a) == 16 and all(ch in "0123456789abcdef" for ch in a)


def make_result_batch(**overrides) -> dict[str, np.ndarray]:
    """One full contract row as arrays, with sane defaults."""
    defaults = {
        "experiment": "exp1",
        "metric": "arpu",
        "is_main_metric": True,
        "is_guardrail": False,
        "method_name": "t-test",
        "method_params": '{"test_type":"relative"}',
        "method_config_id": "a" * 16,
        "name_1": "control",
        "name_2": "treatment",
        "start_ts": datetime(2024, 1, 1),
        "end_ts": datetime(2024, 1, 2),
        "start_date": date(2024, 1, 1),
        "end_date": date(2024, 1, 1),
        "window_seconds": 86400,
        "elapsed_days": 1.0,
        "value_1": 1.0,
        "value_2": 1.1,
        "std_1": 0.5,
        "std_2": 0.6,
        "cov_value_1": None,
        "cov_value_2": None,
        "size_1": 100,
        "size_2": 101,
        "alpha": 0.05,
        "pvalue": 0.2,
        "effect": 0.1,
        "left_bound": -0.05,
        "right_bound": 0.25,
        "ci_length": 0.3,
        "reject": False,
        "mde_1": 0.15,
        "mde_2": 0.15,
        "srm_flag": False,
        "srm_pvalue": 0.9,
        "decision_blocked": False,
        "insufficient_data": False,
        "ci_kind": "fixed",
        "is_horizon": False,
        "warnings": None,
        "diagnostics": None,
        "metric_query": "SELECT ...",
        "metric_rendered_query": "SELECT 1",
        "watermark_ts": datetime(2024, 1, 2),
    }
    defaults.update(overrides)
    return {col: np.array([defaults[col]], dtype=object) for col in RESULT_COLUMNS}


class TestResults:
    def test_save_stamps_distinct_monotonic_created_at(self, tables, backend, monkeypatch):
        frozen = datetime(2024, 6, 1, 12, 0, 0)
        monkeypatch.setattr(base_mod, "now_utc_naive", lambda: frozen)
        batch = {
            col: np.concatenate(
                [
                    make_result_batch(end_ts=datetime(2024, 1, 2))[col],
                    make_result_batch(end_ts=datetime(2024, 1, 3))[col],
                ]
            )
            for col in RESULT_COLUMNS
        }
        tables.save_results(batch)
        rows = backend._store(backend.get_full_table_name(TABLE_RESULTS))
        created = [r["created_at"] for r in rows]
        assert len(created) == 2
        assert created[0] != created[1], "same-ms writes must get distinct versions"

    def test_missing_contract_column_raises(self, tables):
        batch = make_result_batch()
        del batch["srm_flag"]
        with pytest.raises(ValueError, match="missing contract columns"):
            tables.save_results(batch)

    def test_unknown_column_raises(self, tables):
        batch = make_result_batch()
        batch["created_at"] = np.array([datetime(2024, 1, 1)], dtype=object)
        with pytest.raises(ValueError, match="unknown columns"):
            tables.save_results(batch)

    def test_list_computed_cutoffs_is_a_set_reader(self, tables):
        for day in (2, 3, 5):  # deliberate hole at day 4
            tables.save_results(make_result_batch(end_ts=datetime(2024, 1, day)))
        cutoffs = tables.list_computed_cutoffs("exp1", "arpu", "a" * 16)
        assert cutoffs == {
            datetime(2024, 1, 2),
            datetime(2024, 1, 3),
            datetime(2024, 1, 5),
        }

    def test_rewrite_same_cutoff_stays_one_row_lww(self, tables, backend):
        tables.save_results(make_result_batch(effect=0.1))
        tables.save_results(make_result_batch(effect=0.2))
        rows = tables.load_results("exp1")
        assert len(rows) == 1
        assert rows[0]["effect"] == pytest.approx(0.2)  # last writer won

    def test_list_method_config_ids(self, tables):
        tables.save_results(make_result_batch())
        tables.save_results(
            make_result_batch(method_config_id="b" * 16, end_ts=datetime(2024, 1, 3))
        )
        ids = tables.list_method_config_ids("exp1")
        assert ids == {("arpu", "a" * 16): 1, ("arpu", "b" * 16): 1}

    def test_delete_results_window(self, tables):
        for day in (2, 3, 4):
            tables.save_results(make_result_batch(end_ts=datetime(2024, 1, day)))
        deleted = tables.delete_results(
            "exp1", from_ts=datetime(2024, 1, 3), to_ts=datetime(2024, 1, 4)
        )
        assert deleted == 1
        assert tables.list_computed_cutoffs("exp1", "arpu", "a" * 16) == {
            datetime(2024, 1, 2),
            datetime(2024, 1, 4),
        }


class TestMaintenance:
    def _aa_record(self):
        return {
            "experiment": "exp1",
            "run_id": "r1",
            "metric": "arpu",
            "method_name": "t-test",
            "method_params": "{}",
            "method_config_id": "a" * 16,
            "mode": "fpr",
            "iterations": 100,
            "alpha": 0.05,
            "injected_effect": None,
            "fpr": 0.05,
            "peeking_fpr": 0.21,
            "power": None,
            "achieved_mde": None,
            "coverage": None,
            "effect_exaggeration": None,
            "tau2": None,
            "fpr_sequential": None,
            "peeking_fpr_sequential": None,
            "power_sequential": None,
            "coverage_sequential": None,
            "effect_exaggeration_sequential": None,
            "ci_width": None,
            "ci_width_sequential": None,
            "verdict": "calibrated",
            "details": "{}",
            "status": "success",
            "error_message": None,
        }

    def test_purge_experiment_spares_audit_and_unit_state(self, tables):
        # populate every surface
        tables.acquire_lock("exp1")
        tables.replace_exposures(
            "exp1",
            {
                "unit_id": np.array(["u1"], dtype=object),
                "variant": np.array(["control"], dtype=object),
                "exposure_ts": np.array([datetime(2024, 1, 1)], dtype=object),
            },
        )
        tables.save_results(make_result_batch())
        tables.save_aa_run(self._aa_record())
        cs_id = compute_column_set_id("db.revenue", {"value": "gross_usd"})
        tables.replace_day_state(
            "db.revenue",
            cs_id,
            date(2024, 1, 5),
            {"unit_id": np.array(["u1"], dtype=object), "n": np.array([1], dtype=np.int64)},
        )

        assert tables.list_known_experiments() == {"exp1"}
        tables.purge_experiment("exp1")

        assert tables.list_known_experiments() == set()
        assert tables.count_exposures("exp1") == 0
        assert tables.load_results("exp1") == []
        # audit trail + shared state survive
        assert len(tables.get_aa_runs("exp1")) == 1
        assert tables.sum_moments("db.revenue", cs_id, date(2024, 1, 5), date(2024, 1, 5))["n"] == 1

    def test_count_experiment_rows(self, tables):
        tables.save_results(make_result_batch())
        counts = tables.count_experiment_rows("exp1")
        assert counts["_ab_results"] == 1
        assert counts["_ab_exposures"] == 0
