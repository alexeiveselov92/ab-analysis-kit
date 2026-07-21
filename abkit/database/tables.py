"""
Internal table models for abkit — the greenfield ``_ab_*`` schema.

Founding decision (CLAUDE.md / architecture.md §6): storage is GREENFIELD.
The legacy ``marts.*`` layout is reference only; these schemas are designed
from the decision logic and the specs:

- ``_ab_experiments`` — experiment catalog (informational)
- ``_ab_exposures``   — OPTIONAL per-unit assignment cohort copy, populated
  only under ``assignment.cohort_copy.enabled`` (M8); read-only for compute
- ``_ab_unit_state``  — cumulative per-unit day-bucketed moments (the
  scalability seam; cumulative-intervals.md §5.2/§5.3/§6.4)
- ``_ab_results``     — the BI contract (data-contract-and-reporting.md §2)
- ``_ab_aa_runs``     — A/A validation audit trail (written by ``abk validate``, M4)
- ``_ab_tasks``       — run locks + idempotency

Sizing note: string primary-key columns carry ``max_length`` so composite keys
fit MySQL InnoDB's 3072-byte index cap (utf8mb4 = 4 bytes/char). The config
validator enforces the same bounds on names (experiment/metric <= 128 chars,
variant names <= 64).
"""

from abkit.core.models import ColumnDefinition, TableModel

#: Length bounds mirrored by the config validator.
MAX_EXPERIMENT_NAME_LENGTH = 128
MAX_METRIC_NAME_LENGTH = 128
MAX_VARIANT_NAME_LENGTH = 64


def get_experiments_table_model() -> TableModel:
    """``_ab_experiments`` — the experiment catalog.

    INFORMATIONAL ONLY (BI joins metric/experiment metadata from here — the
    one source of truth for descriptions; data-contract §2 note). Replaced on
    every run via the DELETE + INSERT upsert pattern.

    Primary Key: (experiment); Engine: MergeTree (upsert_record keeps it unique).
    """
    return TableModel(
        columns=[
            ColumnDefinition("experiment", "String", max_length=MAX_EXPERIMENT_NAME_LENGTH),
            ColumnDefinition("description", "Nullable(String)", nullable=True),
            ColumnDefinition("status", "String"),  # design|running|concluded|archived
            ColumnDefinition("is_actual", "Bool"),
            ColumnDefinition("start_date", "Date"),
            ColumnDefinition("end_date", "Date"),
            ColumnDefinition("unit_key", "String"),
            ColumnDefinition("cadence", "String"),  # canonical JSON: scalar or schedule
            ColumnDefinition("data_lag_seconds", "Int64"),
            ColumnDefinition("timezone", "String"),
            ColumnDefinition("variants", "String"),  # canonical JSON array, order = config
            ColumnDefinition("expected_split", "String"),  # canonical JSON object
            ColumnDefinition("alpha", "Nullable(Float64)", nullable=True),
            ColumnDefinition("correction", "Nullable(String)", nullable=True),
            ColumnDefinition("sequential_enabled", "Bool"),
            ColumnDefinition("sequential_scheme", "String"),
            ColumnDefinition("comparisons", "String"),  # canonical JSON summary
            ColumnDefinition("path", "String"),  # experiments/<name>.yml
            ColumnDefinition("tags", "String"),  # canonical JSON array
            ColumnDefinition("created_at", "DateTime64(3, 'UTC')"),
            ColumnDefinition("updated_at", "DateTime64(3, 'UTC')"),
        ],
        primary_key=["experiment"],
        engine="MergeTree",
        order_by=["experiment"],
    )


def get_exposures_table_model() -> TableModel:
    """``_ab_exposures`` — the OPTIONAL persisted per-unit assignment cohort copy.

    Populated only under ``assignment.cohort_copy.enabled`` (M8), and then
    incrementally — watermark resume + grid-anchored closed-interval batches,
    append-only (``insert_exposures_incremental``), never a whole-table reload
    on a routine run (``--resync-cohort`` is the one exception). By default
    (no-copy) this table is never created: metric SQL joins a live dedup
    subquery over the assignment SQL instead (the ``ab_cohort_source``
    builtin). READ-ONLY for compute: abkit never writes back into it from the
    pipeline and never randomizes.

    Primary Key: (experiment, unit_id); Engine: ReplacingMergeTree(loaded_at)
    so a re-load self-heals duplicates on ClickHouse; SQL backends get the
    version-aware LWW upsert.
    """
    return TableModel(
        columns=[
            ColumnDefinition("experiment", "String", max_length=MAX_EXPERIMENT_NAME_LENGTH),
            ColumnDefinition("unit_id", "String", max_length=255),
            ColumnDefinition("variant", "String", max_length=MAX_VARIANT_NAME_LENGTH),
            ColumnDefinition("exposure_ts", "DateTime64(3, 'UTC')"),
            ColumnDefinition("stratum", "Nullable(String)", nullable=True),
            ColumnDefinition("loaded_at", "DateTime64(3, 'UTC')"),
        ],
        primary_key=["experiment", "unit_id"],
        engine="ReplacingMergeTree(loaded_at)",
        order_by=["experiment", "unit_id"],
        version_column="loaded_at",
    )


def get_unit_state_table_model() -> TableModel:
    """``_ab_unit_state`` — cumulative per-unit moments, day-bucketed.

    The scalability seam (cumulative-intervals.md §4/§6.4). v1 is a thin
    materialization: the read path stays recompute; the schema, cardinality
    key, and idempotency invariant are locked NOW because the corruption is
    silent until v2 flips the read path (§5.2).

    - Keyed per (source_table, column_set_id, unit_id, day) — NOT per
      (experiment, metric) — so co-located metrics sharing a fact source share
      one set of per-unit moments (§5.3). ``column_set_id`` identifies the
      column-role set (see ``internal_tables/_unit_state.compute_column_set_id``).
    - ``day``-bucketed: sub-day cutoffs read closed-day state + a current-day
      tail fact-scan; the state stage advances only at day close (§6.4).
    - Replace-not-sum (§5.2): ``ReplacingMergeTree(version)`` on ClickHouse,
      version-aware LWW upsert on SQL backends. Running the state stage twice
      for one day leaves aggregates unchanged (the twice-run invariant test).
    - Plain Float64 moments, deliberately NOT AggregateFunction states: they
      must round-trip through PG/MySQL, and v2's Python accumulator reads
      moments, not CH agg states (plan R2).

    Moment columns cover the closed-form method families (§3): mean/t-test
    {n, Σx, Σx²}, CUPED co-moments {Σc, Σc², Σxc}, ratio {Σd, Σd², Σxd}.
    Unused moments stay 0 for a given column set.
    """
    return TableModel(
        columns=[
            ColumnDefinition("source_table", "String", max_length=128),
            ColumnDefinition("column_set_id", "String", max_length=64),
            ColumnDefinition("unit_id", "String", max_length=255),
            ColumnDefinition("day", "Date"),
            ColumnDefinition("n", "UInt64"),
            ColumnDefinition("sum_value", "Float64", default="0"),
            ColumnDefinition("sum_value_sq", "Float64", default="0"),
            ColumnDefinition("sum_cov", "Float64", default="0"),
            ColumnDefinition("sum_cov_sq", "Float64", default="0"),
            ColumnDefinition("sum_value_cov", "Float64", default="0"),
            ColumnDefinition("sum_denominator", "Float64", default="0"),
            ColumnDefinition("sum_denominator_sq", "Float64", default="0"),
            ColumnDefinition("sum_value_denominator", "Float64", default="0"),
            ColumnDefinition("version", "DateTime64(3, 'UTC')"),
        ],
        primary_key=["source_table", "column_set_id", "unit_id", "day"],
        engine="ReplacingMergeTree(version)",
        order_by=["source_table", "column_set_id", "unit_id", "day"],
        version_column="version",
    )


def get_results_table_model() -> TableModel:
    """``_ab_results`` — the clean BI contract (data-contract §2).

    One row per (experiment, metric, variant-pair, method_config_id, end_ts).
    ``end_ts`` is the canonical cutoff key (UTC, EXCLUSIVE half-open window);
    ``end_date``/``start_date`` are derived Dates, legacy-identical at daily
    cadence. Idempotency is last-writer-wins on the PK via the
    strictly-monotonic ``created_at`` version (quorum must-fix); BI dedup
    reads use argMax/LIMIT 1 BY, internal reads use FINAL.

    Test columns are Nullable: ``insufficient_data`` rows are written with
    inference withheld (counts and SRM stay visible — cumulative-intervals
    §6.1.4). ``warnings``/``diagnostics`` are canonical-JSON payloads carrying
    the human-readable failure signal (θ, boot_mean, H5 zero-denominator
    explanations) — data-contract §2 as amended in this WP (plan R7).
    """
    return TableModel(
        columns=[
            # identity
            ColumnDefinition("experiment", "String", max_length=MAX_EXPERIMENT_NAME_LENGTH),
            ColumnDefinition("metric", "String", max_length=MAX_METRIC_NAME_LENGTH),
            ColumnDefinition("is_main_metric", "Bool"),
            ColumnDefinition("is_guardrail", "Bool"),
            ColumnDefinition("method_name", "String"),
            ColumnDefinition("method_params", "String"),  # THE canonical JSON string
            ColumnDefinition("method_config_id", "String", max_length=64),
            ColumnDefinition("name_1", "String", max_length=MAX_VARIANT_NAME_LENGTH),
            ColumnDefinition("name_2", "String", max_length=MAX_VARIANT_NAME_LENGTH),
            # window (cumulative-intervals §6.3)
            ColumnDefinition("start_ts", "DateTime64(3, 'UTC')"),
            ColumnDefinition("end_ts", "DateTime64(3, 'UTC')"),  # EXCLUSIVE
            ColumnDefinition("start_date", "Date"),
            ColumnDefinition("end_date", "Date"),
            ColumnDefinition("window_seconds", "Int64"),
            ColumnDefinition("elapsed_days", "Float64"),  # fractional; chart x-axis
            # per-arm
            ColumnDefinition("value_1", "Nullable(Float64)", nullable=True),
            ColumnDefinition("value_2", "Nullable(Float64)", nullable=True),
            ColumnDefinition("std_1", "Nullable(Float64)", nullable=True),
            ColumnDefinition("std_2", "Nullable(Float64)", nullable=True),
            ColumnDefinition("cov_value_1", "Nullable(Float64)", nullable=True),
            ColumnDefinition("cov_value_2", "Nullable(Float64)", nullable=True),
            # CUPED covariate moments (M9 WP1): with cov_value_i these complete
            # the per-arm covariate SufficientStats, making cuped-t-test
            # reconstructible from a persisted row (Tier-E in explore, M9 WP2).
            # NULL for every non-CUPED method and for pre-migration rows.
            ColumnDefinition("cov_std_1", "Nullable(Float64)", nullable=True),
            ColumnDefinition("cov_std_2", "Nullable(Float64)", nullable=True),
            ColumnDefinition("corr_coef_1", "Nullable(Float64)", nullable=True),
            ColumnDefinition("corr_coef_2", "Nullable(Float64)", nullable=True),
            ColumnDefinition("size_1", "UInt64"),
            ColumnDefinition("size_2", "UInt64"),
            # test (Nullable: withheld under insufficient_data demotion)
            ColumnDefinition("alpha", "Float64"),  # effective, post-correction
            ColumnDefinition("pvalue", "Nullable(Float64)", nullable=True),
            ColumnDefinition("effect", "Nullable(Float64)", nullable=True),
            ColumnDefinition("left_bound", "Nullable(Float64)", nullable=True),
            ColumnDefinition("right_bound", "Nullable(Float64)", nullable=True),
            ColumnDefinition("ci_length", "Nullable(Float64)", nullable=True),
            ColumnDefinition("reject", "Nullable(Bool)", nullable=True),
            ColumnDefinition("mde_1", "Nullable(Float64)", nullable=True),
            ColumnDefinition("mde_2", "Nullable(Float64)", nullable=True),
            # integrity
            ColumnDefinition("srm_flag", "Bool"),
            ColumnDefinition("srm_pvalue", "Nullable(Float64)", nullable=True),
            ColumnDefinition("decision_blocked", "Bool"),
            ColumnDefinition("insufficient_data", "Bool"),
            # sequence
            ColumnDefinition("ci_kind", "String"),  # fixed | always_valid
            ColumnDefinition("is_horizon", "Bool"),
            # diagnostics (plan R7 — spec amended in this WP)
            ColumnDefinition("warnings", "Nullable(String)", nullable=True),  # JSON array
            ColumnDefinition("diagnostics", "Nullable(String)", nullable=True),  # JSON object
            # provenance
            ColumnDefinition("metric_query", "String"),
            ColumnDefinition("metric_rendered_query", "String"),
            ColumnDefinition("watermark_ts", "DateTime64(3, 'UTC')"),
            ColumnDefinition("created_at", "DateTime64(3, 'UTC')"),  # strictly-monotonic LWW
        ],
        primary_key=["experiment", "metric", "name_1", "name_2", "method_config_id", "end_ts"],
        engine="ReplacingMergeTree(created_at)",
        order_by=["experiment", "metric", "name_1", "name_2", "method_config_id", "end_ts"],
        version_column="created_at",
    )


def get_aa_runs_table_model() -> TableModel:
    """``_ab_aa_runs`` — the A/A validation audit trail.

    Written by ``abk validate`` (M4): one row per scored (experiment, metric,
    method) cell — empirical FPR vs nominal alpha, honest cumulative-peeking
    FPR over the actual cadence grid, power / achieved-MDE under injected
    effects, CI coverage, effect-exaggeration-at-stop, and the plain-language
    verdict. An audit trail: informational, never read by the run pipeline,
    deliberately NOT pruned by ``abk clean`` (aa-false-positive-matrix.md).
    The M4 work package may extend the payload before the first release.
    """
    return TableModel(
        columns=[
            ColumnDefinition("experiment", "String", max_length=MAX_EXPERIMENT_NAME_LENGTH),
            ColumnDefinition("run_id", "String", max_length=64),
            ColumnDefinition("metric", "String"),
            ColumnDefinition("method_name", "String"),
            ColumnDefinition("method_params", "String"),  # canonical JSON
            ColumnDefinition("method_config_id", "String"),
            ColumnDefinition("mode", "String"),  # fpr | power | mde
            ColumnDefinition("iterations", "Int32"),
            ColumnDefinition("alpha", "Float64"),
            ColumnDefinition("injected_effect", "Nullable(Float64)", nullable=True),
            ColumnDefinition("fpr", "Nullable(Float64)", nullable=True),
            ColumnDefinition("peeking_fpr", "Nullable(Float64)", nullable=True),
            ColumnDefinition("power", "Nullable(Float64)", nullable=True),
            ColumnDefinition("achieved_mde", "Nullable(Float64)", nullable=True),
            ColumnDefinition("coverage", "Nullable(Float64)", nullable=True),
            ColumnDefinition("effect_exaggeration", "Nullable(Float64)", nullable=True),
            # M5 D8: the always-valid sequential column, side-by-side with the fixed
            # measurements above (aa-false-positive-matrix.md; m5-implementation-plan §WP2).
            ColumnDefinition("tau2", "Nullable(Float64)", nullable=True),  # frozen mixture variance
            ColumnDefinition("fpr_sequential", "Nullable(Float64)", nullable=True),
            ColumnDefinition("peeking_fpr_sequential", "Nullable(Float64)", nullable=True),
            ColumnDefinition("power_sequential", "Nullable(Float64)", nullable=True),
            ColumnDefinition("coverage_sequential", "Nullable(Float64)", nullable=True),
            ColumnDefinition("effect_exaggeration_sequential", "Nullable(Float64)", nullable=True),
            ColumnDefinition("ci_width", "Nullable(Float64)", nullable=True),  # fixed horizon width
            ColumnDefinition("ci_width_sequential", "Nullable(Float64)", nullable=True),
            ColumnDefinition("verdict", "String"),
            ColumnDefinition("details", "String"),  # canonical JSON
            ColumnDefinition("status", "String"),  # success | failed
            ColumnDefinition("error_message", "Nullable(String)", nullable=True),
            ColumnDefinition("created_at", "DateTime64(3, 'UTC')"),
        ],
        primary_key=["experiment", "run_id"],
        engine="ReplacingMergeTree(created_at)",
        order_by=["experiment", "run_id"],
        version_column="created_at",
    )


def get_tasks_table_model() -> TableModel:
    """``_ab_tasks`` — run locks + idempotency.

    Primary Key: (experiment, scope, process_type). ``scope`` is the lock
    grain: ``"pipeline"`` for the whole-experiment lock; the key shape
    reserves per-metric locks (scope = metric name) for later parallelism
    (cumulative-intervals §5.7). ``locked_by`` is the owner token
    (host:pid:nonce) — required by the ClickHouse advisory claim read-back.

    Deliberately drops detectkit's resume-cursor (``last_processed_timestamp``)
    and alerting columns: an experiment is a finite re-runnable recomputation,
    not a resumed cursor (§7) — the planner anti-join replaces the cursor.

    Engine: plain MergeTree; uniqueness is maintained by the lock protocol
    (atomic claim / sync upsert_record), not by a version merge.
    """
    return TableModel(
        columns=[
            ColumnDefinition("experiment", "String", max_length=MAX_EXPERIMENT_NAME_LENGTH),
            ColumnDefinition("scope", "String", max_length=MAX_METRIC_NAME_LENGTH),
            ColumnDefinition("process_type", "String", max_length=32),
            ColumnDefinition("status", "String"),  # running | completed | failed
            ColumnDefinition("started_at", "DateTime64(3, 'UTC')"),
            ColumnDefinition("updated_at", "DateTime64(3, 'UTC')"),
            ColumnDefinition("locked_by", "String"),
            ColumnDefinition("error_message", "Nullable(String)", nullable=True),
            ColumnDefinition("timeout_seconds", "Int32"),
        ],
        primary_key=["experiment", "scope", "process_type"],
        engine="MergeTree",
        order_by=["experiment", "scope", "process_type"],
    )


# Table names as constants
TABLE_EXPERIMENTS = "_ab_experiments"
TABLE_EXPOSURES = "_ab_exposures"
TABLE_UNIT_STATE = "_ab_unit_state"
TABLE_RESULTS = "_ab_results"
TABLE_AA_RUNS = "_ab_aa_runs"
TABLE_TASKS = "_ab_tasks"

# Map of table names to model factories
INTERNAL_TABLES = {
    TABLE_EXPERIMENTS: get_experiments_table_model,
    TABLE_EXPOSURES: get_exposures_table_model,
    TABLE_UNIT_STATE: get_unit_state_table_model,
    TABLE_RESULTS: get_results_table_model,
    TABLE_AA_RUNS: get_aa_runs_table_model,
    TABLE_TASKS: get_tasks_table_model,
}
