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
from abkit.core.models import ColumnDefinition, TableModel
from abkit.database.internal_tables import (
    InternalTablesManager,
    compute_column_set_id,
    compute_metric_state_id,
    compute_state_source_id,
    normalize_sql_for_identity,
)
from abkit.database.internal_tables._results import RESULT_COLUMNS
from abkit.database.tables import (
    INTERNAL_TABLES,
    TABLE_EXPERIMENTS,
    TABLE_RESULTS,
    TABLE_TASKS,
    get_experiments_table_model,
    get_results_table_model,
)
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
    def test_creates_and_registers_every_internal_table(self, backend):
        """Derived from INTERNAL_TABLES, not from a hand-copied list: a new
        table that nobody adds here would otherwise ship uncreated."""
        manager = InternalTablesManager(backend)
        manager.ensure_tables()
        for table in INTERNAL_TABLES:
            assert backend.table_exists(table), table
            assert backend._model(table) is not None, table

    def test_idempotent(self, tables):
        tables.ensure_tables()  # second call must not raise or duplicate
        assert len(tables._manager._rows) == len(INTERNAL_TABLES)


#: the M9 WP1 additive columns — the project's first post-release schema change
NEW_CUPED_COLUMNS = ("cov_std_1", "cov_std_2", "corr_coef_1", "corr_coef_2")


def _pre_wp1_results_model() -> TableModel:
    """``_ab_results`` as shipped before M9 WP1 (0.3.0 and earlier)."""
    model = get_results_table_model()
    return TableModel(
        columns=[col for col in model.columns if col.name not in NEW_CUPED_COLUMNS],
        primary_key=model.primary_key,
        engine=model.engine,
        order_by=model.order_by,
        version_column=model.version_column,
    )


class TestSchemaMigration:
    """M9 WP1: ``ensure_tables`` additively migrates a pre-WP1 ``_ab_results``.

    Without this primitive every installed 0.3.0 project would break on
    upgrade: ``save_results`` checks the batch against the CURRENT
    ``RESULT_COLUMNS`` while the live table still has the old shape.
    """

    def _seed_old_project(self, backend) -> None:
        """A project whose ``_ab_results`` predates WP1, with one stored row."""
        full = backend.get_full_table_name(TABLE_RESULTS, use_internal=True)
        old_model = _pre_wp1_results_model()
        backend.create_table(full, old_model)
        batch = make_result_batch()
        old_batch = {c: v for c, v in batch.items() if c not in NEW_CUPED_COLUMNS}
        old_batch["created_at"] = np.array([datetime(2024, 1, 2, 12)], dtype=object)
        backend.insert_batch(full, old_batch)

    def test_ensure_tables_adds_columns_and_old_rows_read_null(self, backend):
        self._seed_old_project(backend)
        manager = InternalTablesManager(backend)
        manager.ensure_tables()

        live = backend.list_columns(TABLE_RESULTS)
        for col in NEW_CUPED_COLUMNS:
            assert col in live, col
        rows = manager.load_results("exp1")
        assert len(rows) == 1
        for col in NEW_CUPED_COLUMNS:
            assert rows[0][col] is None, col

    def test_migration_is_idempotent(self, backend):
        self._seed_old_project(backend)
        manager = InternalTablesManager(backend)
        manager.ensure_tables()
        full = backend.get_full_table_name(TABLE_RESULTS, use_internal=True)
        assert backend.ensure_columns(full, get_results_table_model()) == []
        manager.ensure_tables()  # the full pass stays safe end-to-end
        assert len(manager.load_results("exp1")) == 1

    def test_current_write_path_works_after_migration(self, backend):
        """The strict save_results contract check passes on a migrated table."""
        self._seed_old_project(backend)
        manager = InternalTablesManager(backend)
        manager.ensure_tables()

        manager.save_results(
            make_result_batch(end_ts=datetime(2024, 1, 3), cov_std_1=1.5, corr_coef_1=0.7)
        )
        by_ts = {row["end_ts"]: row for row in manager.load_results("exp1")}
        assert by_ts[datetime(2024, 1, 3)]["cov_std_1"] == 1.5
        assert by_ts[datetime(2024, 1, 3)]["corr_coef_1"] == 0.7
        assert by_ts[datetime(2024, 1, 2)]["cov_std_1"] is None  # legacy row

    def test_not_null_no_default_addition_is_refused(self, backend):
        """The additive contract: new columns must be nullable or defaulted."""
        self._seed_old_project(backend)
        full = backend.get_full_table_name(TABLE_RESULTS, use_internal=True)
        old = _pre_wp1_results_model()
        bad = TableModel(
            columns=[*old.columns, ColumnDefinition("strict_new", "Float64")],
            primary_key=old.primary_key,
            engine=old.engine,
            order_by=old.order_by,
            version_column=old.version_column,
        )
        with pytest.raises(ValueError, match="nullable-or-defaulted"):
            backend.ensure_columns(full, bad)

    def test_ensure_columns_never_creates_a_missing_table(self, backend):
        full = backend.get_full_table_name(TABLE_RESULTS, use_internal=True)
        assert backend.ensure_columns(full, get_results_table_model()) == []
        assert not backend.table_exists(TABLE_RESULTS)


#: Every ``_ab_experiments`` column added since ``0.7.0``, newest release last.
#: **A work package that adds a catalog column MUST extend this**, and the
#: reason is that the omission is INVISIBLE: the pre-release model below is
#: derived as "the current model minus these", so a column missing from the set
#: is already present in the supposedly-old table and its migration is never
#: exercised. Measured during the m14 DEC-1 review — with ``control`` absent
#: here, declaring it ``String`` NOT-NULL/no-default (the exact shape m13
#: STAT-1b shipped, which would kill every installed project's first run) left
#: 775 tests and the m13 exit gate green.
POST_0_7_0_CATALOG_COLUMNS = (
    "contrasts",  # m13 STAT-1b (0.8.0)
    "control",  # m14 DEC-1 (0.9.0)
)

#: Kept as the old name for the assertions that name the STAT-1b column alone.
NEW_CATALOG_COLUMNS = POST_0_7_0_CATALOG_COLUMNS


def _pre_stat1b_experiments_model() -> TableModel:
    """``_ab_experiments`` as shipped through 0.7.0."""
    model = get_experiments_table_model()
    return TableModel(
        columns=[col for col in model.columns if col.name not in POST_0_7_0_CATALOG_COLUMNS],
        primary_key=model.primary_key,
        engine=model.engine,
        order_by=model.order_by,
        version_column=model.version_column,
    )


class TestCatalogSchemaMigration:
    """m13 STAT-6: a 0.7.0 ``_ab_experiments`` migrates additively.

    STAT-1b declared the column NOT NULL with no default — a shape
    ``ensure_columns`` REFUSES — so the first ``0.8.0`` run of every installed
    project would have died with a drop-and-recreate error, for a column whose
    own source comment promised it needed no recreate. The default also makes
    the historical rows honest: before the knob existed every experiment's
    family WAS ``all_pairs``.
    """

    def _seed_old_catalog(self, backend) -> None:
        full = backend.get_full_table_name(TABLE_EXPERIMENTS, use_internal=True)
        old_model = _pre_stat1b_experiments_model()
        backend.create_table(full, old_model)
        row = {
            col.name: np.array([_catalog_placeholder(col.name)], dtype=object)
            for col in old_model.columns
        }
        backend.insert_batch(full, row)

    def test_the_seeded_model_is_really_older_than_the_current_one(self):
        """The gate's own precondition, asserted rather than assumed.

        ``_pre_stat1b_experiments_model`` is derived by SUBTRACTION, so it is
        only "0.7.0" while ``POST_0_7_0_CATALOG_COLUMNS`` is complete. A WP that
        adds a column and forgets the set seeds a table that already has it,
        and this class silently stops testing a migration at all — which is
        exactly what happened to ``control`` before the DEC-1 review.
        """
        current = {col.name for col in get_experiments_table_model().columns}
        old = {col.name for col in _pre_stat1b_experiments_model().columns}
        assert old < current, "the seeded catalog is not a strict subset of the current one"
        assert current - old == set(POST_0_7_0_CATALOG_COLUMNS)

    def test_ensure_tables_adds_the_columns_without_a_recreate(self, backend):
        self._seed_old_catalog(backend)
        InternalTablesManager(backend).ensure_tables()

        live = backend.list_columns(TABLE_EXPERIMENTS)
        for col in POST_0_7_0_CATALOG_COLUMNS:
            assert col in live, col
        row = backend._rows[TABLE_EXPERIMENTS][0]
        # the DEFAULT, not NULL: the column is NOT NULL, and a pre-0.8.0
        # experiment did compare all pairs
        assert row["contrasts"] == "all_pairs"
        # NULL, not a default: no literal could be right for a per-experiment
        # variant name, and NULL is the honest reading of a pre-0.9.0 row
        assert row["control"] is None

    def test_the_next_run_writes_the_declared_family(self, backend):
        """The other half of the same defect: the writer's field whitelist."""
        self._seed_old_catalog(backend)
        manager = InternalTablesManager(backend)
        manager.ensure_tables()
        record = _catalog_record_for("legacy_exp", contrasts="vs_control")
        manager.upsert_experiment(record)

        stored = manager.get_experiment("legacy_exp")
        assert stored is not None and stored["contrasts"] == "vs_control"

    def test_a_field_the_catalog_would_drop_is_refused(self, backend):
        """A whitelist that silently drops is how STAT-1b's column went unwritten."""
        self._seed_old_catalog(backend)
        manager = InternalTablesManager(backend)
        manager.ensure_tables()
        record = {**_catalog_record_for("exp"), "invented_later": "x"}
        with pytest.raises(ValueError, match="would drop"):
            manager.upsert_experiment(record)


def _catalog_placeholder(column: str):
    if column in ("created_at", "updated_at", "start_ts", "horizon_ts"):
        return datetime(2024, 7, 1)
    if column in ("is_actual", "sequential_enabled"):
        return True
    if column in ("data_lag_seconds",):
        return 0
    if column in ("alpha",):
        return 0.05
    return "legacy"


def _catalog_record_for(name: str, **overrides):
    from abkit.database.internal_tables._experiments import _ExperimentsMixin

    record = {field: _catalog_placeholder(field) for field in _ExperimentsMixin._EXPERIMENT_FIELDS}
    record["experiment"] = name
    record.update(overrides)
    return record


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

    def test_last_exposure_timestamp_none_on_empty_cohort(self, tables):
        assert tables.get_last_exposure_timestamp("exp1") is None

    def test_last_exposure_timestamp_is_the_max(self, tables):
        tables.replace_exposures("exp1", self._cohort(3))
        assert tables.get_last_exposure_timestamp("exp1") == datetime(2024, 1, 1, 10, 0, 2)
        # scoped per experiment, like the MIN mirror
        assert tables.get_last_exposure_timestamp("other") is None

    def test_watermark_reads_are_final_deduped_under_pre_merge_duplicates(self, tables):
        """Review-confirmed: the WP5 incremental append makes coexisting
        pre-merge row versions ROUTINE on ClickHouse; a non-FINAL MIN/MAX
        could read a stale, superseded timestamp and permanently inflate the
        resume watermark. Both watermark reads must see the LWW value."""
        one = {
            "unit_id": np.array(["u1"], dtype=object),
            "variant": np.array(["control"], dtype=object),
            "exposure_ts": np.array([datetime(2024, 1, 5, 10)], dtype=object),
        }
        corrected = dict(one, exposure_ts=np.array([datetime(2024, 1, 1, 10)], dtype=object))
        tables.insert_exposures_incremental("exp1", one)
        tables.insert_exposures_incremental("exp1", corrected)  # newer loaded_at wins
        assert tables.get_last_exposure_timestamp("exp1") == datetime(2024, 1, 1, 10)
        assert tables.get_first_exposure_ts("exp1") == datetime(2024, 1, 1, 10)

    def test_insert_incremental_appends_without_delete(self, tables, backend):
        deletes: list[tuple] = []
        original = backend.delete_rows

        def spy(*args, **kwargs):
            deletes.append(args)
            return original(*args, **kwargs)

        backend.delete_rows = spy
        tables.replace_exposures("exp1", self._cohort(2))
        assert len(deletes) == 1  # the full reload deletes...

        extra = {
            "unit_id": np.array(["u8", "u9"], dtype=object),
            "variant": np.array(["control", "treatment"], dtype=object),
            "exposure_ts": np.array(
                [datetime(2024, 1, 2, 10), datetime(2024, 1, 2, 11)], dtype=object
            ),
        }
        written = tables.insert_exposures_incremental("exp1", extra)
        assert written == 2
        assert len(deletes) == 1  # ...the incremental append NEVER does
        assert tables.count_exposures("exp1") == 4

    def test_insert_incremental_is_idempotent_under_reinsert(self, tables):
        cohort = self._cohort(3)
        tables.insert_exposures_incremental("exp1", cohort)
        tables.insert_exposures_incremental("exp1", cohort)  # the same batch twice
        # (experiment, unit_id) PK + loaded_at LWW collapse the re-send
        assert tables.count_exposures("exp1") == 3
        assert tables.get_exposure_counts("exp1") == {"control": 2, "treatment": 1}

    def test_insert_incremental_missing_columns_raises(self, tables):
        with pytest.raises(ValueError, match="missing columns"):
            tables.insert_exposures_incremental("exp1", {"unit_id": np.array(["u1"], dtype=object)})

    def test_insert_incremental_empty_batch_writes_nothing(self, tables):
        empty = {
            "unit_id": np.array([], dtype=object),
            "variant": np.array([], dtype=object),
            "exposure_ts": np.array([], dtype=object),
        }
        assert tables.insert_exposures_incremental("exp1", empty) == 0
        assert tables.count_exposures("exp1") == 0

    def test_exposure_count_stream_asof_boundaries(self, tables):
        """The sub-day SRM stream (WP5): cumulative counts with exposure_ts <
        each EXCLUSIVE boundary. cohort(4) exposes control@:00/:02, treatment@:01/:03."""
        tables.replace_exposures("exp1", self._cohort(4))
        boundaries = [
            datetime(2024, 1, 1, 10, 0, 0),  # exclusive: nobody yet
            datetime(2024, 1, 1, 10, 0, 2),  # control@:00, treatment@:01
            datetime(2024, 1, 1, 10, 0, 4),  # all four
        ]
        stream = tables.get_exposure_count_stream("exp1", boundaries, ["control", "treatment"])
        assert stream == [
            {"control": 0, "treatment": 0},
            {"control": 1, "treatment": 1},
            {"control": 2, "treatment": 2},
        ]

    def test_exposure_count_stream_zero_fills_missing_arm(self, tables):
        cohort = {
            "unit_id": np.array(["u0", "u1"], dtype=object),
            "variant": np.array(["control", "control"], dtype=object),
            "exposure_ts": np.array(
                [datetime(2024, 1, 1, 10, 0, 0), datetime(2024, 1, 1, 10, 0, 1)], dtype=object
            ),
        }
        tables.replace_exposures("exp1", cohort)
        stream = tables.get_exposure_count_stream(
            "exp1", [datetime(2024, 1, 1, 10, 0, 5)], ["control", "treatment"]
        )
        assert stream == [{"control": 2, "treatment": 0}]  # missing arm ⇒ 0, the worst SRM

    def test_exposure_count_stream_empty_boundaries(self, tables):
        tables.replace_exposures("exp1", self._cohort(4))
        assert tables.get_exposure_count_stream("exp1", [], ["control", "treatment"]) == []

    def test_arrival_rate_derives_units_per_day_per_arm(self, tables):
        # 4 units spanning :00..:03 ⇒ window = 3s = 3/86400 days; 2 per arm ⇒ rate = 2/window
        tables.replace_exposures("exp1", self._cohort(4))
        result = tables.get_arrival_rate("exp1", ["control", "treatment"])
        assert result is not None
        rates, window_days = result
        assert window_days == pytest.approx(3.0 / 86400.0)
        assert rates["control"] == pytest.approx(2.0 / window_days)
        assert rates["treatment"] == pytest.approx(2.0 / window_days)

    def test_arrival_rate_degenerate_window_is_none(self, tables):
        # all exposures at one instant ⇒ window == 0 ⇒ underivable (the seed-mirror case)
        cohort = {
            "unit_id": np.array(["u0", "u1"], dtype=object),
            "variant": np.array(["control", "treatment"], dtype=object),
            "exposure_ts": np.array(
                [datetime(2024, 1, 1, 8, 0, 0), datetime(2024, 1, 1, 8, 0, 0)], dtype=object
            ),
        }
        tables.replace_exposures("exp1", cohort)
        assert tables.get_arrival_rate("exp1", ["control", "treatment"]) is None

    def test_arrival_rate_empty_cohort_is_none(self, tables):
        assert tables.get_arrival_rate("ghost", ["control", "treatment"]) is None

    def test_arrival_rate_zero_fills_undeclared_variant(self, tables):
        tables.replace_exposures("exp1", self._cohort(4))
        result = tables.get_arrival_rate("exp1", ["control", "treatment", "t2"])
        assert result is not None
        rates, _ = result
        assert rates["t2"] == 0.0  # a declared arm with no exposures reads as a zero rate


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

    def test_metric_state_id_identity(self):
        """m9 WP3: the metric-hash invalidation is whitespace-normalized."""
        roles = {"variant": "variant", "value": "gross_usd"}
        base = compute_metric_state_id(roles, "SELECT  user_id,\n  sum(x) FROM t")
        reformatted = compute_metric_state_id(roles, "SELECT user_id, sum(x) FROM t")
        edited_sql = compute_metric_state_id(roles, "SELECT user_id, sum(y) FROM t")
        edited_roles = compute_metric_state_id(
            {"variant": "variant", "value": "net_usd"}, "SELECT  user_id,\n  sum(x) FROM t"
        )
        assert base == reformatted  # reformatting never orphans a series
        assert base != edited_sql
        assert base != edited_roles
        assert len(base) == 16 and all(ch in "0123456789abcdef" for ch in base)
        # the cohort-shaping config is part of the identity (the R1 fix):
        # an added_filters edit must orphan the series like a SQL edit does
        cohort_a = {"added_filters": "", "unit_key": "user_id"}
        cohort_b = {"added_filters": "AND user_id != 'bot'", "unit_key": "user_id"}
        with_a = compute_metric_state_id(roles, "SELECT 1", cohort_config=cohort_a)
        with_b = compute_metric_state_id(roles, "SELECT 1", cohort_config=cohort_b)
        assert with_a != with_b
        assert with_a == compute_metric_state_id(roles, "SELECT 1", cohort_config=dict(cohort_a))

    def test_identity_normalization_separates_formatting_from_data(self):
        """m9 WP6 (an R1 exit-gate finding): whitespace is FORMATTING outside a
        quoted span and DATA inside one — and an apostrophe in a comment must
        not be read as the start of a literal."""
        pairs = [
            # (differs only in formatting → same identity?, a, b)
            (True, "-- don't  sum\nSELECT   x FROM t", "-- don't sum\nSELECT x   FROM  t"),
            (True, "/* a\n   b */ SELECT x", "/* a b */ SELECT   x"),
            (True, "SELECT   x\n\nFROM t", "SELECT x FROM t"),
            (
                True,
                "SELECT x FROM t WHERE a = 'x' AND b = 'y'",
                "SELECT  x FROM t\n WHERE a = 'x' AND b = 'y'",
            ),
            (False, "SELECT x WHERE c = 'A  B'", "SELECT x WHERE c = 'A B'"),
            (False, "SELECT 'a' 'b'", "SELECT 'a''b'"),
            (False, 'SELECT "a  b" FROM t', 'SELECT "a b" FROM t'),
            (False, "SELECT x -- note\n", "SELECT x -- other note\n"),
        ]
        for same, a, b in pairs:
            assert (normalize_sql_for_identity(a) == normalize_sql_for_identity(b)) is same, (a, b)

    def test_unreadable_sql_is_hashed_raw_not_guessed(self):
        """m9 WP6 R2: the two ways to be wrong are not symmetric. A shape the
        scanner cannot read unambiguously — a backslash escape (an escape on
        MySQL/ClickHouse, a plain character on standard-conforming
        PostgreSQL), a dollar-quoted body, a `#` (comment on MySQL/ClickHouse,
        XOR on PostgreSQL), an unterminated quote or block comment — must
        fall back to hashing the RAW text, so a whitespace edit anywhere in it
        orphans the series instead of silently reusing it."""
        ambiguous = [
            ("SELECT x WHERE c = 'It\\'s  a' AND y", "SELECT x WHERE c = 'It\\'s a' AND y"),
            ("SELECT $$a  b$$ FROM t", "SELECT $$a b$$ FROM t"),
            ("# don't\nSELECT c = 'A  B'", "# don't\nSELECT c = 'A B'"),
            ("SELECT 'unterminated  ", "SELECT 'unterminated "),
            ("SELECT /* unterminated  ", "SELECT /* unterminated "),
        ]
        for a, b in ambiguous:
            assert normalize_sql_for_identity(a) == a, a  # raw, not normalized
            assert normalize_sql_for_identity(a) != normalize_sql_for_identity(b), a

    def test_state_source_id_stays_inside_the_column_budget(self):
        assert compute_state_source_id("exp", "arpu") == "exp/arpu"
        long_a = compute_state_source_id("e" * 100, "m" * 100 + "a")
        long_b = compute_state_source_id("e" * 100, "m" * 100 + "b")
        assert len(long_a) <= 128
        assert long_a != long_b  # the hash tail keeps overlong names distinct

    def test_list_and_delete_state_series(self, tables):
        """m9 WP3: the orphan sweep primitives."""
        source = "exp1/arpu"
        tables.replace_day_state(source, "old_series_0000/1", self.DAY, self._day_state())
        tables.replace_day_state(source, "new_series_0000/2", self.DAY, self._day_state())
        assert tables.list_state_column_sets(source) == [
            "new_series_0000/2",
            "old_series_0000/1",
        ]
        tables.delete_state_series(source, "old_series_0000/1")
        assert tables.list_state_column_sets(source) == ["new_series_0000/2"]
        kept = tables.sum_moments(source, "new_series_0000/2", self.DAY, self.DAY)
        assert kept["sum_value"] == pytest.approx(4.5)

    def test_delete_state_days_from_truncates_the_tail_only(self, tables):
        """m9 WP3: the crash-safety / non-finite truncation primitive."""
        source, series = "exp1/arpu", "series_0000000001"
        for offset in range(3):
            tables.replace_day_state(
                source, series, self.DAY + timedelta(days=offset), self._day_state(1)
            )
        tables.delete_state_days_from(source, series, self.DAY + timedelta(days=1))
        assert tables.get_last_state_day(source, series) == self.DAY
        kept = tables.sum_moments(source, series, self.DAY, self.DAY + timedelta(days=2))
        assert kept["sum_value"] == pytest.approx(1.5)  # only the first day survives


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
        "window_seconds": 86400,
        "elapsed_days": 1.0,
        "value_1": 1.0,
        "value_2": 1.1,
        "std_1": 0.5,
        "std_2": 0.6,
        "cov_value_1": None,
        "cov_value_2": None,
        "cov_std_1": None,
        "cov_std_2": None,
        "corr_coef_1": None,
        "corr_coef_2": None,
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
