# Internal tables (_ab_*)

abkit keeps all of its own state in a small set of **greenfield** tables prefixed
`_ab_`. They are created and maintained for you — you never write DDL by hand —
but you *will* read them: `_ab_results` is the BI contract your dashboards query,
and the rest are useful when you need to understand what a run did, debug a stuck
lock, or audit an A/A calibration.

This page documents the schema **as it exists in code**
(`abkit/database/internal_tables/` + `abkit/database/tables.py`). It is a
reference, not a tutorial — for how results are produced see
[data-contract-and-reporting](../specs/data-contract-and-reporting.md); for the
config that drives it see [declarative-config](../specs/declarative-config.md).

## Greenfield, not `marts.*`

By founding decision, abkit does **not** carry over the legacy
`marts.exp_comparison_results` layout or any of its storage internals
(data-contract-and-reporting §2). The legacy Grafana dashboard is a *reference
only* — it told us what an analyst needs to see and how they decide. The `_ab_*`
schema is designed from scratch to support that decision logic with plain SQL,
owing nothing to the old table shapes.

There are six internal tables:

| Table | Engine | Primary key | What it is |
|---|---|---|---|
| [`_ab_results`](#_ab_results--the-bi-contract) | `ReplacingMergeTree(created_at)` | `(experiment, metric, name_1, name_2, method_config_id, end_ts)` | The BI contract — one row per comparison per cutoff. |
| [`_ab_exposures`](#_ab_exposures--the-assignment-cohort-copy-optional) | `ReplacingMergeTree(loaded_at)` | `(experiment, unit_id)` | The persisted assignment cohort — **optional, copy-mode only** (`assignment.cohort_copy.enabled: true`); absent by default. |
| [`_ab_tasks`](#_ab_tasks--run-locks) | `MergeTree` | `(experiment, scope, process_type)` | Run locks + idempotency. |
| [`_ab_aa_runs`](#_ab_aa_runs--the-aa-validation-audit-trail) | `ReplacingMergeTree(created_at)` | `(experiment, run_id)` | `abk validate` A/A audit trail. |
| [`_ab_experiments`](#_ab_experiments--the-catalog) | `MergeTree` | `(experiment)` | Informational experiment catalog. |
| [`_ab_unit_state`](#_ab_unit_state--the-scalability-seam) | `ReplacingMergeTree(version)` | `(source_table, column_set_id, unit_id, day)` | Internal scalability seam (v1: reserved schema; writer stage not yet wired). |

### Where they live

The tables live in the **internal location** you configure in `profiles.yml`,
separate from your source data:

- ClickHouse / MySQL: `internal_database`
- PostgreSQL: `internal_schema`

Fully qualified, that is `<internal_location>.<table>` — e.g.
`abkit_internal._ab_results`. Your fact/event tables live in the *data* location
(`data_database`, or `data_schema` on PostgreSQL) and are only ever read.

The tables are created on demand and idempotently: every CLI invocation that
needs them calls `ensure_tables()`, which creates any `_ab_*` table that does not
yet exist, additively adds any column a newer abkit version has introduced to an
existing table (`ALTER TABLE … ADD COLUMN`; never drops or renames), and is safe
to call repeatedly. Read-only surfaces (a report on a
never-run project, the explore cockpit, the calibration chip) deliberately do
**not** create schema — they guard with existence checks (`results_table_exists`,
`exposures_table_exists`, `aa_runs_table_exists`) so reading never mutates your
warehouse.

### Types across backends

Column types below are given in ClickHouse spelling (`DateTime64(3, 'UTC')`,
`Nullable(Float64)`, `UInt64`, `Bool`, …). The PostgreSQL and MySQL backends map
these to their native equivalents through the generic manager; the semantics are
the same. All timestamps are UTC and are normalised to naive-UTC on the read
path.

### Last-writer-wins dedup — read with the grain

Four of the tables use `ReplacingMergeTree(<version_column>)`. On ClickHouse a
background merge collapses rows that share a primary key, keeping the one with
the largest version — but that merge is **asynchronous**, so a naive `SELECT *`
can transiently return more than one row per key. Every version stamp comes from
a single strictly-increasing, distinct source (advancing at least 1 ms past the
previous write) so the "latest" is always unambiguous.

- **abkit's own reads** append `FINAL` on ClickHouse (no-op elsewhere) to dedup.
- **Your BI queries** must dedup too: take the row with the max version per key
  (`argMax(...)` / `LIMIT 1 BY <pk>` on ClickHouse, or a window
  `row_number()` filter on PostgreSQL/MySQL). Do not assume one physical row per
  key.
- **PostgreSQL / MySQL** get the same last-writer-wins effect via a version-aware
  upsert that mirrors `ReplacingMergeTree`, so the physical table already holds
  one row per key there.

The version column per table: `created_at` (`_ab_results`, `_ab_aa_runs`),
`loaded_at` (`_ab_exposures`), `version` (`_ab_unit_state`). The plain
`MergeTree` tables (`_ab_tasks`, `_ab_experiments`) maintain uniqueness through
their write protocol (atomic claim / synchronous upsert), not a version merge.

---

## `_ab_results` — the BI contract

The one table you will query for dashboards. One row per
`(experiment, metric, variant-pair, method_config_id, end_ts)`, where `end_ts`
is the canonical **exclusive** cutoff of a cumulative window measured from the
experiment start. Idempotency is last-writer-wins on the primary key via the
strictly-monotonic `created_at` version.

A comparison series is the set of rows sharing
`(experiment, metric, name_1, name_2, method_config_id)` ordered by `end_ts`;
each point is cumulative from `start_ts`. `method_config_id` pins the exact
method + identity params, so editing an identity param starts a *new* series
rather than mutating the old one.

Test columns are nullable because a small-sample cutoff can be **demoted**
(`insufficient_data = true`): the row is still written — counts, SRM and window
stay visible — but inference (`pvalue`, `effect`, bounds, `reject`) is withheld
(cumulative-intervals §6.1.4).

**Engine** `ReplacingMergeTree(created_at)` ·
**PK** `(experiment, metric, name_1, name_2, method_config_id, end_ts)`

### Identity

| Column | Type | Purpose |
|---|---|---|
| `experiment` | `String` | Experiment name. |
| `metric` | `String` | Metric name. |
| `is_main_metric` | `Bool` | Drives the verdict; sets the alpha tier. |
| `is_guardrail` | `Bool` | Checked for regression, never for a WIN. |
| `method_name` | `String` | Registered method (e.g. `t-test`, `cuped-t-test`, `ratio-delta`). |
| `method_params` | `String` | Canonical JSON of the method params (single `json_dumps_sorted` path — exact-string BI filters never split a series). |
| `method_config_id` | `String` | Stable hash of method + identity params; the series key. |
| `name_1`, `name_2` | `String` | The variant pair (control vs treatment). |

### Window

| Column | Type | Purpose |
|---|---|---|
| `start_ts` | `DateTime64(3,'UTC')` | Window start (experiment start). |
| `end_ts` | `DateTime64(3,'UTC')` | Cutoff — **exclusive** half-open edge, the canonical key. |
| `start_date`, `end_date` | `Date` | Derived dates (legacy-identical at `cadence: 1d`). |
| `window_seconds` | `Int64` | Window length in seconds. |
| `elapsed_days` | `Float64` | Fractional elapsed days — the chart x-axis. |

### Per-arm

| Column | Type | Purpose |
|---|---|---|
| `value_1`, `value_2` | `Nullable(Float64)` | Per-arm metric value (arm 1 / arm 2). |
| `std_1`, `std_2` | `Nullable(Float64)` | Per-arm standard deviation. |
| `cov_value_1`, `cov_value_2` | `Nullable(Float64)` | Per-arm covariate value (CUPED / ratio context). |
| `cov_std_1`, `cov_std_2` | `Nullable(Float64)` | Per-arm covariate standard deviation (`cuped-t-test` only; NULL otherwise and on pre-0.4.0 rows). |
| `corr_coef_1`, `corr_coef_2` | `Nullable(Float64)` | Per-arm value↔covariate correlation (`cuped-t-test` only; NULL otherwise, on pre-0.4.0 rows, and when degenerate). |
| `size_1`, `size_2` | `UInt64` | Per-arm unit counts (always populated). |

### Test

| Column | Type | Purpose |
|---|---|---|
| `alpha` | `Float64` | Effective **post-correction** per-comparison alpha stamped at compute time (see note below). |
| `pvalue` | `Nullable(Float64)` | Test p-value. |
| `effect` | `Nullable(Float64)` | Point estimate of the effect. |
| `left_bound`, `right_bound` | `Nullable(Float64)` | (1−α) confidence-interval bounds. |
| `ci_length` | `Nullable(Float64)` | Interval width. |
| `reject` | `Nullable(Bool)` | Whether the null is rejected (≡ CI excludes zero). |
| `mde_1`, `mde_2` | `Nullable(Float64)` | Minimum detectable effect per arm. |

### Integrity

| Column | Type | Purpose |
|---|---|---|
| `srm_flag` | `Bool` | Sample-ratio-mismatch tripped. |
| `srm_pvalue` | `Nullable(Float64)` | SRM chi-square p-value. |
| `decision_blocked` | `Bool` | Verdict withheld (SRM gate is blocking-but-non-dropping — rows are still written). |
| `insufficient_data` | `Bool` | Small-n demotion — inference withheld, counts/SRM kept. |

### Sequence

| Column | Type | Purpose |
|---|---|---|
| `ci_kind` | `String` | `fixed` (default fixed-horizon) or `always_valid` (opt-in sequential — statistics-changes §4). |
| `is_horizon` | `Bool` | This cutoff is the planned horizon. |

### Diagnostics

| Column | Type | Purpose |
|---|---|---|
| `warnings` | `Nullable(String)` | Canonical-JSON array of warnings routed from the stats core (e.g. H5 zero-denominator explanations). |
| `diagnostics` | `Nullable(String)` | Canonical-JSON object of context (θ, bootstrap diagnostics, …). |

### Provenance

| Column | Type | Purpose |
|---|---|---|
| `metric_query` | `String` | The metric SQL as authored. |
| `metric_rendered_query` | `String` | The SQL after Jinja rendering for this window. |
| `watermark_ts` | `DateTime64(3,'UTC')` | Completeness boundary in force for this row. |
| `created_at` | `DateTime64(3,'UTC')` | Strictly-monotonic LWW version. |

**BI notes.** `avg_group_size = (size_1 + size_2)/2` and the `zero_effect = 0`
reference line are **derived in your query**, not stored. Metric *descriptions*
are **not** stored here — they live in `_ab_experiments` / metric YAML and are
joined by BI, so there is one source of truth. Corrections that are applied at
read time — read-time Benjamini-Hochberg, and the verdict WIN/LOSE/FLAT/
INCONCLUSIVE logic — are **not** persisted: compute-time rows deliberately carry
the raw effective alpha, and the verdict is recomputed at render. Two-tier
Bonferroni *is* reflected here: main metrics and secondary metrics land at
different `alpha` values.

---

## `_ab_exposures` — the assignment cohort copy (optional)

The persisted per-unit assignment cohort — **optional, copy-mode only**: it
exists only when the experiment sets `assignment.cohort_copy.enabled: true`.
By default (no-copy, the M8 default) abkit never creates or writes this table:
every metric query joins a deduping subquery over your **live** assignment SQL
instead (the `ab_cohort_source` builtin behind the packaged
`ab.exposed_units(...)` macro), re-rendered and re-validated on every
invocation. Either way the cohort is resolved once per run, never re-derived
per interval, and stays **read-only for compute**: the pipeline never writes
back into it and never randomizes. The SRM gate always measures the live
validated assignment source, not this table.

With copy mode on, the table is written **incrementally and append-only**
(`insert_exposures_incremental`): each run appends only the newly-matured,
grid-anchored closed-interval batches since the last watermark
(`MAX(exposure_ts)`, `FINAL`-deduped, snapped down to its bucket floor) — a
routine run never deletes rows. The one exception is `abk run
--resync-cohort`, which deletes the experiment's copy and rebuilds it from the
experiment start through the same engine — the recovery for rows backfilled
into an already-scanned closed bucket, which the watermark alone silently
misses (the documented copy-mode limitation).

**Engine** `ReplacingMergeTree(loaded_at)` · **PK** `(experiment, unit_id)`

| Column | Type | Purpose |
|---|---|---|
| `experiment` | `String` | Experiment name. |
| `unit_id` | `String` | The randomization unit (user, session, …). |
| `variant` | `String` | Assigned arm. |
| `exposure_ts` | `DateTime64(3,'UTC')` | First-exposure timestamp (drives cumulative counts and the sub-day SRM stream). |
| `stratum` | `Nullable(String)` | Optional stratum label. |
| `loaded_at` | `DateTime64(3,'UTC')` | LWW version (stamped at load). |

---

## `_ab_tasks` — run locks

Run locks + idempotency. Each pipeline run claims a lock row before doing any
work and releases it on exit; a second run against the same experiment finds the
lock held and no-ops. The lock grain is `(experiment, scope, process_type)`:

- `abk run` uses scope `pipeline`, process_type `run`.
- `abk validate` uses scope `pipeline`, process_type `validate` (its own
  out-of-band lock, so a validation and a run can't clobber each other).

A `running` row whose age exceeds its stored `timeout_seconds` is treated as
**stale** and can be overridden — so a process that died mid-run (or a database
restart) never blocks future runs forever. Release is ownership-checked: a run
whose lock aged out and was legitimately stolen will not wipe the new owner's
live row on exit. `locked_by` is a per-claim owner token (`host:pid:nonce`),
which also makes a held lock human-attributable.

**Engine** `MergeTree` · **PK** `(experiment, scope, process_type)`

| Column | Type | Purpose |
|---|---|---|
| `experiment` | `String` | Experiment name. |
| `scope` | `String` | Lock grain — `pipeline` today (the key shape reserves per-metric scopes for later parallelism). |
| `process_type` | `String` | `run` or `validate`. |
| `status` | `String` | `running` \| `completed` \| `failed`. |
| `started_at` | `DateTime64(3,'UTC')` | Claim time (staleness is `now - started_at` vs `timeout_seconds`). |
| `updated_at` | `DateTime64(3,'UTC')` | Last update. |
| `locked_by` | `String` | Owner token `host:pid:nonce`. |
| `error_message` | `Nullable(String)` | Failure detail, recorded before the error propagates. |
| `timeout_seconds` | `Int32` | Staleness horizon for this claim. |

If a run is killed uncleanly and leaves a `running` row behind, clear it with
`abk unlock` — it force-releases the lock (ignoring the age check) and marks the
task `completed` so future runs proceed without `--force`.

---

## `_ab_aa_runs` — the A/A validation audit trail

Written by `abk validate` (the A/A false-positive matrix — a placebo
label-permutation experiment, **not** a linter). One row per scored
`(experiment, metric, method)` cell: empirical false-positive rate vs the nominal
alpha, the honest cumulative-**peeking** FPR over the real cadence grid, power /
achieved-MDE under injected effects, CI coverage, effect exaggeration at stop,
plus a plain-language verdict. When sequential is in play the same measurements
appear again in the `*_sequential` columns, side by side with the fixed-horizon
ones.

This is an **audit trail**: it is informational, never read by the `run`
pipeline, and deliberately **not** pruned by `abk clean` — it is kept forever.
The rows also drive the explore cockpit's calibration chip: the persisted `alpha`
is the same effective post-correction alpha the chip and Apply seam use, so a
matching cell lights the chip as `calibrated`. `run_id` is
`{run_stamp}:{cell_hash}` — one row per cell, no version collapse across cells.

**Engine** `ReplacingMergeTree(created_at)` · **PK** `(experiment, run_id)`

| Column | Type | Purpose |
|---|---|---|
| `experiment` | `String` | Experiment name. |
| `run_id` | `String` | `{run_stamp}:{cell_hash}` — one row per scored cell. |
| `metric` | `String` | Metric name. |
| `method_name` | `String` | Method scored. |
| `method_params` | `String` | Canonical JSON of the method params. |
| `method_config_id` | `String` | Method + identity-param hash. |
| `mode` | `String` | Recommended-row objective: `fpr` \| `power` \| `mde`. |
| `iterations` | `Int32` | Number of placebo iterations. |
| `alpha` | `Float64` | Effective post-correction alpha (matches the run / chip). |
| `injected_effect` | `Nullable(Float64)` | Effect injected for power/MDE modes. |
| `fpr` | `Nullable(Float64)` | Single-look false-positive rate (horizon only). |
| `peeking_fpr` | `Nullable(Float64)` | Cumulative-peeking FPR across all looks. |
| `power` | `Nullable(Float64)` | Power under the injected effect. |
| `achieved_mde` | `Nullable(Float64)` | Achieved minimum detectable effect. |
| `coverage` | `Nullable(Float64)` | CI coverage. |
| `effect_exaggeration` | `Nullable(Float64)` | Effect exaggeration at stop. |
| `tau2` | `Nullable(Float64)` | Frozen mixture variance (sequential). |
| `fpr_sequential` | `Nullable(Float64)` | Single-look FPR under the always-valid CI. |
| `peeking_fpr_sequential` | `Nullable(Float64)` | Peeking FPR under the always-valid CI. |
| `power_sequential` | `Nullable(Float64)` | Power under the always-valid CI. |
| `coverage_sequential` | `Nullable(Float64)` | Coverage under the always-valid CI. |
| `effect_exaggeration_sequential` | `Nullable(Float64)` | Exaggeration under the always-valid CI. |
| `ci_width` | `Nullable(Float64)` | Fixed-horizon CI width. |
| `ci_width_sequential` | `Nullable(Float64)` | Always-valid CI width. |
| `verdict` | `String` | Plain-language verdict for the cell. |
| `details` | `String` | Canonical JSON of supporting detail. |
| `status` | `String` | `success` \| `failed`. |
| `error_message` | `Nullable(String)` | Failure detail when `status = failed`. |
| `created_at` | `DateTime64(3,'UTC')` | LWW version. |

---

## `_ab_experiments` — the catalog

An **informational** catalog: one row per experiment carrying its resolved
metadata (dates, variants, split, cadence, alpha/correction, sequential settings,
tags, config path). The `run` pipeline never reads it back for a decision — it
exists so BI can join human-readable metadata (descriptions, tags, the config
path) to `_ab_results` from one source of truth. It is upserted once per run
(delete + insert), preserving the first-seen `created_at`.

**Engine** `MergeTree` · **PK** `(experiment)`

| Column | Type | Purpose |
|---|---|---|
| `experiment` | `String` | Experiment name. |
| `description` | `Nullable(String)` | Free-text description. |
| `status` | `String` | `design` \| `running` \| `concluded` \| `archived`. |
| `is_actual` | `Bool` | Whether this config is the current one. |
| `start_date`, `end_date` | `Date` | Experiment window. |
| `unit_key` | `String` | Randomization unit key. |
| `cadence` | `String` | Canonical JSON — scalar or schedule. |
| `data_lag_seconds` | `Int64` | Completeness watermark lag. |
| `timezone` | `String` | Experiment timezone. |
| `variants` | `String` | Canonical JSON array (config order). |
| `expected_split` | `String` | Canonical JSON object. |
| `alpha` | `Nullable(Float64)` | Effective alpha. |
| `correction` | `Nullable(String)` | Correction method. |
| `sequential_enabled` | `Bool` | Sequential opt-in flag. |
| `sequential_scheme` | `String` | Sequential scheme name. |
| `comparisons` | `String` | Canonical JSON comparison summary. |
| `path` | `String` | `experiments/<name>.yml`. |
| `tags` | `String` | Canonical JSON array. |
| `created_at`, `updated_at` | `DateTime64(3,'UTC')` | First-seen / last-write times. |

---

## `_ab_unit_state` — the scalability seam

An internal seam for a future incremental compute path, **not** part of the BI
contract. In v1 the writer (the pipeline "STATE" stage) is deliberately **not
wired**: the read path stays full-window recompute, so `abk run` never populates
this table — materializing day-state would double the warehouse scan for data
nothing reads. Only the schema, cardinality key, and idempotency invariant are
locked now (cumulative-intervals §5.2); the stage activates when v2 flips the read
path. It is *designed* to hold cumulative per-unit statistical moments,
day-bucketed, keyed by the **fact source** — `(source_table, column_set_id,
unit_id, day)` — not by experiment, so co-located metrics sharing a fact source
would share one set of per-unit moments. Because no experiment owns these rows,
`abk clean` deliberately leaves them alone (as it does `_ab_aa_runs`).

**Engine** `ReplacingMergeTree(version)` ·
**PK** `(source_table, column_set_id, unit_id, day)`

| Column | Type | Purpose |
|---|---|---|
| `source_table` | `String` | Fact source table. |
| `column_set_id` | `String` | Identifies the column-role set (value/covariate/ratio). |
| `unit_id` | `String` | Randomization unit. |
| `day` | `Date` | Day bucket. |
| `n` | `UInt64` | Unit-day observation count. |
| `sum_value`, `sum_value_sq` | `Float64` | Mean / t-test moments. |
| `sum_cov`, `sum_cov_sq`, `sum_value_cov` | `Float64` | CUPED co-moments. |
| `sum_denominator`, `sum_denominator_sq`, `sum_value_denominator` | `Float64` | Ratio moments. |
| `version` | `DateTime64(3,'UTC')` | LWW version (replace-not-sum: re-running a day leaves aggregates unchanged). |

Moment columns unused by a given column set stay `0`.

---

## How the CLI touches these tables

| Command | Effect on `_ab_*` |
|---|---|
| `abk run` | Claims/releases the `_ab_tasks` lock; upserts `_ab_experiments`; writes `_ab_results`. Reads `_ab_results` for the planner anti-join. With `assignment.cohort_copy.enabled: true` only, appends incrementally to `_ab_exposures` (or rebuilds it with `--resync-cohort`); the no-copy default never touches that table. |
| `abk validate` | Claims its own `_ab_tasks` lock (`process_type=validate`); writes `_ab_aa_runs`. Never writes `_ab_exposures` — a placebo split is in-memory only. |
| `abk explore` | Read-only over `_ab_results`; reads `_ab_aa_runs` for the calibration chip (Auto mode can write `_ab_aa_runs`). |
| `abk plan` | Read-only pre-launch sizing — no internal-table writes. |
| `abk clean` | Prunes orphaned result series (`delete_results`) and, with `--orphaned-experiments`, purges every experiment-keyed table (`_ab_experiments`, `_ab_exposures`, `_ab_results`, `_ab_tasks`). Never touches `_ab_aa_runs` or `_ab_unit_state`. |
| `abk unlock` | Force-clears a stale/held `_ab_tasks` lock. |
| `abk init` / `abk init-claude` | Scaffold files only — no database access. |

## See also

- [data-contract-and-reporting](../specs/data-contract-and-reporting.md) — the decision logic and results contract in full.
- [declarative-config](../specs/declarative-config.md) — the YAML/SQL and the `ab.exposed_units(...)` assignment macro.
- [cumulative-intervals](../specs/cumulative-intervals.md) — cutoffs, windows, and the `_ab_unit_state` seam.
