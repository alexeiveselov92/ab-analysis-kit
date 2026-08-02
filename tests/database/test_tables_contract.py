"""Schema regression tests for the greenfield ``_ab_*`` tables.

The ``_ab_results`` column list IS the BI contract
(docs/specs/data-contract-and-reporting.md §2, as amended by plan R7) — any
change here must be a deliberate contract change, mirrored in the spec.
"""

from __future__ import annotations

from abkit.database.tables import (
    INTERNAL_TABLES,
    TABLE_AA_RUNS,
    TABLE_EXPERIMENTS,
    TABLE_EXPOSURES,
    TABLE_NOTIFY_STATES,
    TABLE_RESULTS,
    TABLE_TASKS,
    TABLE_UNIT_STATE,
    get_experiments_table_model,
    get_exposures_table_model,
    get_results_table_model,
    get_tasks_table_model,
    get_unit_state_table_model,
)

# The §2 contract, in storage order. Deliberate: spelled out in full so any
# drift fails this test and forces a spec amendment in the same PR.
RESULTS_CONTRACT_COLUMNS = [
    # identity
    "experiment",
    "metric",
    "is_main_metric",
    "is_guardrail",
    "method_name",
    "method_params",
    "method_config_id",
    "name_1",
    "name_2",
    # window (m10 WP3 dropped the derived start_date/end_date Dates)
    "start_ts",
    "end_ts",
    "window_seconds",
    "elapsed_days",
    # per-arm
    "value_1",
    "value_2",
    "std_1",
    "std_2",
    "cov_value_1",
    "cov_value_2",
    # CUPED covariate moments (M9 WP1) — complete the per-arm covariate
    # SufficientStats; NULL for non-CUPED methods and pre-migration rows
    "cov_std_1",
    "cov_std_2",
    "corr_coef_1",
    "corr_coef_2",
    "size_1",
    "size_2",
    # test
    "alpha",
    "pvalue",
    "effect",
    "left_bound",
    "right_bound",
    "ci_length",
    "reject",
    "mde_1",
    "mde_2",
    # integrity
    "srm_flag",
    "srm_pvalue",
    "decision_blocked",
    "insufficient_data",
    # sequence
    "ci_kind",
    "is_horizon",
    # diagnostics (R7)
    "warnings",
    "diagnostics",
    # provenance
    "metric_query",
    "metric_rendered_query",
    "watermark_ts",
    "created_at",
]


class TestResultsContract:
    def test_exact_column_list_and_order(self):
        model = get_results_table_model()
        assert [c.name for c in model.columns] == RESULTS_CONTRACT_COLUMNS

    def test_identity_and_versioning(self):
        model = get_results_table_model()
        assert model.primary_key == [
            "experiment",
            "metric",
            "name_1",
            "name_2",
            "method_config_id",
            "end_ts",
        ]
        assert model.engine == "ReplacingMergeTree(created_at)"
        assert model.version_column == "created_at"
        assert model.order_by == model.primary_key

    def test_test_columns_are_nullable_for_demotion(self):
        """insufficient_data rows carry NULLed inference, visible counts."""
        model = get_results_table_model()
        for col in ("pvalue", "effect", "left_bound", "right_bound", "reject", "mde_1", "mde_2"):
            assert model.get_column(col).nullable, f"{col} must be nullable (demotion)"
        for col in ("size_1", "size_2", "srm_flag", "alpha"):
            assert not model.get_column(col).nullable, f"{col} must stay visible"

    def test_the_window_is_instants_only(self):
        """m10 WP3: the derived Date columns are gone.

        They were never read — not by the pipeline, the readout, explore, the
        report or the shipped BI examples — and an exclusive `end_ts` plus the
        experiment timezone reconstructs them exactly (`end_ts - 1µs`, read in
        that zone). A `Date` column also cannot represent a sub-day cutoff
        (first-class since M2), so it collapsed same-day looks onto one value
        from the day it shipped.
        """
        model = get_results_table_model()
        assert model.get_column("end_ts").type.startswith("DateTime64")
        assert model.get_column("start_ts").type.startswith("DateTime64")
        assert model.get_column("elapsed_days").type == "Float64"
        assert model.get_column("start_date") is None
        assert model.get_column("end_date") is None


class TestExperimentsCatalogSchema:
    """m10 WP1/D3: the catalog mirrors the window as INSTANTS, in UTC."""

    def test_window_columns_are_timestamps_not_dates(self):
        model = get_experiments_table_model()
        for column in ("start_ts", "horizon_ts"):
            assert model.get_column(column).type == "DateTime64(3, 'UTC')", column
        # the pre-m10 spellings are gone — a `Date` here silently truncated a
        # sub-day window, and the names must match the config keys
        assert [c.name for c in model.columns if c.name in ("start_date", "end_date")] == []

    def test_the_anchor_is_persisted_for_bi(self):
        assert get_experiments_table_model().get_column("interval_anchor").type == "String"

    def test_every_catalog_record_key_is_a_column(self):
        """The three-way lockstep (catalog_record keys ↔ _EXPERIMENT_FIELDS ↔
        columns): a partial rename raises 'missing fields' at write time, far
        from the edit that caused it."""
        from abkit.database.internal_tables._experiments import _ExperimentsMixin

        columns = {c.name for c in get_experiments_table_model().columns}
        assert set(_ExperimentsMixin._EXPERIMENT_FIELDS) <= columns


class TestTableRegistry:
    def test_every_table_is_registered(self):
        assert set(INTERNAL_TABLES) == {
            TABLE_EXPERIMENTS,
            TABLE_EXPOSURES,
            TABLE_UNIT_STATE,
            TABLE_RESULTS,
            TABLE_AA_RUNS,
            TABLE_TASKS,
            TABLE_NOTIFY_STATES,
        }

    def test_factories_produce_valid_models(self):
        for name, factory in INTERNAL_TABLES.items():
            model = factory()
            assert model.columns, name
            assert model.primary_key, name

    def test_versioned_tables_have_version_column(self):
        versioned = {
            TABLE_EXPOSURES,
            TABLE_UNIT_STATE,
            TABLE_RESULTS,
            TABLE_AA_RUNS,
            TABLE_NOTIFY_STATES,
        }
        for name, factory in INTERNAL_TABLES.items():
            model = factory()
            if name in versioned:
                assert model.version_column is not None, name
                assert model.version_column in model.engine, name
            else:
                assert model.version_column is None, name


class TestUnitStateSchema:
    def test_cardinality_key_is_source_not_experiment(self):
        """§5.3: keyed per (source-table, column-set, unit, day) — no experiment column."""
        model = get_unit_state_table_model()
        assert model.primary_key == ["source_table", "column_set_id", "unit_id", "day"]
        assert model.get_column("experiment") is None

    def test_replace_not_sum_engine(self):
        """§5.2: ReplacingMergeTree(version), never Summing (re-runs must not add)."""
        model = get_unit_state_table_model()
        assert model.engine == "ReplacingMergeTree(version)"
        assert "Summing" not in model.engine

    def test_moments_are_plain_floats_not_agg_states(self):
        """Plan R2: moments must round-trip through PG/MySQL."""
        model = get_unit_state_table_model()
        for col in model.columns:
            assert "AggregateFunction" not in col.type, col.name


class TestTasksSchema:
    def test_lock_grain_and_owner_token(self):
        model = get_tasks_table_model()
        assert model.primary_key == ["experiment", "scope", "process_type"]
        assert model.get_column("locked_by") is not None

    def test_no_resume_cursor_or_alerting_columns(self):
        """The planner anti-join replaces detectkit's cursor (§7)."""
        model = get_tasks_table_model()
        for legacy in ("last_processed_timestamp", "last_alert_sent", "alert_count"):
            assert model.get_column(legacy) is None, legacy


class TestExposuresSchema:
    def test_shape(self):
        model = get_exposures_table_model()
        assert model.primary_key == ["experiment", "unit_id"]
        assert model.version_column == "loaded_at"
        assert model.get_column("stratum").nullable


class TestMySQLIndexBudget:
    def test_composite_string_pks_fit_innodb_cap(self):
        """Every string PK column must be sized; composite keys must fit 3072
        bytes under utf8mb4 (4 bytes/char) + 8 bytes per temporal column."""
        for name, factory in INTERNAL_TABLES.items():
            model = factory()
            total = 0
            for pk_col in model.primary_key:
                col = model.get_column(pk_col)
                if col.type.startswith(("String", "FixedString")):
                    assert col.max_length is not None, f"{name}.{pk_col} needs max_length"
                    total += 4 * col.max_length
                else:
                    total += 8
            assert total <= 3072, f"{name} PK is {total} bytes > InnoDB cap"
