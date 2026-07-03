# M2 Implementation Plan — declarative config + DB layer + recompute pipeline + CLI

> **Working plan, not a design contract.** Synthesized 2026-07-03 from the specs
> plus a 7-subsystem survey of the detectkit donor codebase and an audit of the
> as-built `abkit.stats` API surface. The specs stay canonical; where this plan
> proposes a contract change (R7: `warnings`/`diagnostics` columns) the spec is
> amended in the same PR. Updated as work packages land; archive at M2 close.

Sources: architecture.md §4–6, declarative-config.md, cumulative-intervals.md §5–7, data-contract-and-reporting.md §2, quorum-review.md, ROADMAP M2, cli-and-dx.md §1/§6, the six detectkit subsystem surveys, and the abkit.stats API surface report.

Conventions: `⟲` = port near-verbatim, `A` = adapt, `RW` = rewrite on donor skeleton, `NEW` = no donor. All abkit paths relative to repo root. Every WP is one reviewable PR (~300–900 net LOC).

---

## 1. Work packages in strict dependency order

### WP1 — Foundation leaves: `core/` + `utils/` (all stdlib, purity-gated)

**Goal:** land every zero-dependency module the rest of M2 imports, without breaking `tests/stats/test_purity.py`.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/core/interval.py` | `abkit/core/interval.py` | A |
| `detectkit/core/models.py` | `abkit/core/models.py` | ⟲ (docstring `_dtk_tasks`→`_ab_tasks`) |
| `detectkit/core/__init__.py` | `abkit/core/__init__.py` | A |
| `detectkit/utils/datetime_utils.py` | `abkit/utils/datetime_utils.py` | ⟲ |
| `detectkit/utils/env_interpolation.py` | `abkit/utils/env_interpolation.py` | ⟲ |
| `detectkit/utils/json_utils.py` | **extend** `abkit/utils/json_utils.py` | A — port `json_loads` ONLY |
| `detectkit/utils/__init__.py` | **extend** `abkit/utils/__init__.py` | A |
| `detectkit/utils/stats.py` | — | skip (detect-specific recency weighting) |

**Hotspots (from the core+utils survey):**
- Interval: "the spec grammar N{s,m,h,d,w} needs 'w'=604800 added, and the schedule-typed (dense-early phase list) cadence must NOT be pushed into Interval — it stays a scalar parser, phases live in config + period_planner." Add `to_timedelta()` for planner arithmetic.
- json_utils: "port ONLY json_loads, adapted to stdlib-only… never port detectkit's json_dumps_sorted (its stdlib fallback also uses default ', '/': ' separators vs orjson's compact output — the exact backend-dependent hash instability abkit's version was written to eliminate)." orjson is on the purity FORBIDDEN list; `abkit/utils/__init__.py` executes on every `import abkit.stats` — no numpy/pydantic/yaml/click/jinja2/orjson may ever join it.
- `models.py.version_column` "is precisely the LWW created_at mechanism the quorum review requires for `_ab_results`, already built" — port untouched.

**Tests:** port `test_interval.py` (+ week-unit cases), `test_models.py` (+ the missing `version_column`-existence case flagged by the survey), `test_datetime_utils.py` (swap deprecated `datetime.utcnow()`), `test_env_interpolation.py` (rename `DETECTK_*` vars); new small `test_json_loads.py` (str-subclass/bytes coercion); assert `tests/stats/test_purity.py` still green in the same PR.

**Must-fixes discharged:** none directly; unblocks everything.

---

### WP2 — Generic DB managers + the atomic lock primitive

**Goal:** the four-manager surface, with the two detect-shaped ABC methods removed and the quorum atomic-claim primitive added natively per backend.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/database/manager.py` | `abkit/database/manager.py` | A |
| `detectkit/database/_sql_manager.py` | `abkit/database/_sql_manager.py` | A |
| `detectkit/database/clickhouse_manager.py` | `abkit/database/clickhouse_manager.py` | A |
| `detectkit/database/postgres_manager.py` | `abkit/database/postgres_manager.py` | A |
| `detectkit/database/mysql_manager.py` | `abkit/database/mysql_manager.py` | A |
| `detectkit/database/__init__.py` | `abkit/database/__init__.py` | A (trim the `internal_tables` re-export line until WP3) |

**Hotspots (from the database-managers survey):**
- Invert the pre-existing invariant violation: "the GENERIC managers lazy-import TABLE_TASKS and hardcode the full `_dtk_tasks` row (including three alerting columns) inside `upsert_task_status` in BOTH `_sql_manager.py` and `clickhouse_manager.py`" — delete `upsert_task_status` from the ABC; row construction moves to `internal_tables/_tasks` (WP3). Replace with two generic primitives:
  - `try_acquire_lock(table_name, key_columns: dict, row: dict, timeout_seconds: int) -> bool` — **single-statement atomic** on PG (`INSERT … ON CONFLICT (pk) DO UPDATE SET … WHERE tasks.status <> 'running' OR now() − started_at > timeout`, claim by rowcount/RETURNING) and MySQL (`INSERT … AS new ON DUPLICATE KEY UPDATE` with conditional `IF(...)`, affected-rows 1=inserted/2=claimed; document the 8.0.19+ floor); **advisory** on ClickHouse (sync `mutations_sync=1` DELETE + INSERT + read-back — "no atomic primitive exists — keep the mutations_sync=1 discipline on the lock path and document the advisory contract").
  - `upsert_record(..., sync: bool = False)` — the survey caveat: "CH upsert_record's delete is NOT sync while upsert_task_status's is (lock rows must be immediately uniquely visible) — add a sync flag."
- Drop/generalize `get_last_timestamp(table_name, metric_name, …)` → `get_max_timestamp(table_name, where_clause, params)` keeping the CH epoch-sentinel (1970-01-01 → None) handling.
- Preserve the dialect quirk inventory verbatim: MySQL `VARCHAR(255)` PK strings ("composite `_ab_results` keys must fit MySQL's 3072-byte InnoDB index cap — size the key columns deliberately"), PG LWW `WHERE existing.ver <= EXCLUDED.ver` with the bare unquoted table name ("keep `_ab_*` table names lowercase"), CH engine-string pass-through (this is how `_ab_unit_state`'s `AggregatingMergeTree`/`ReplacingMergeTree(version)` DDL is emitted for free — the architecture §6 "one new capability" is likely pure schema work), `_canonical_type` raising on unmappable types (so AggregateFunction columns must never reach SQL backends — forces WP3's per-backend unit-state model).
- Renames: `detectk_internal`→`abkit_internal`, extras messages `detectkit[clickhouse]`→`abkit[clickhouse]`, reconsider MySQL `data_database` default `'analytics'`.

**Tests:** port `tests/unit/test_sql_managers.py` (FakeConn/FakeCursor, both dialects — DDL/type-map/LWW-upsert-SQL/NaN-coercion) + new unit tests for the claim SQL per dialect; port `tests/integration/conftest.py` (testcontainers CH 24.3 / PG 16 / MySQL 8.0, rename DBs `abkit_*_it`).

**Must-fixes discharged:** *Atomic lock on PG/MySQL, advisory on CH* (Reliability) — primitive level; contract level in WP3.

---

### WP3 — Greenfield `_ab_*` schema + internal_tables package

**Goal:** all six tables + the mixin package, including the monotonic version source and the unit-state idempotency invariant.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/database/tables.py` | `abkit/database/tables.py` | RW (keep factory/constants/registry PATTERN; 100% new content) |
| `internal_tables/__init__.py` | same | ⟲ |
| `internal_tables/_base.py` | same | ⟲ (epoch normalizer stays load-bearing) + NEW `next_version_ts()` monotonic source |
| `internal_tables/_schema.py` | same | ⟲ (`ensure_tables` + unconditional `register_table` — "the register step is what arms PG/MySQL version_column LWW emulation — do not drop it") |
| `internal_tables/_tasks.py` | same | A |
| `internal_tables/_metrics.py` | `internal_tables/_experiments.py` | A (donor) |
| `internal_tables/_datapoints.py` | `internal_tables/_exposures.py` | A (donor) |
| `internal_tables/_detections.py` | `internal_tables/_results.py` | A (donor — "the highest-value donor") |
| `internal_tables/_autotune_runs.py` | `internal_tables/_aa_runs.py` | A (thin; writer unexercised until M4 `abk validate`) |
| — | `internal_tables/_unit_state.py` | **NEW** (no donor anywhere in detectkit) |
| `internal_tables/_maintenance.py` | same | A |
| `internal_tables/_alert_states.py` | — | skip |
| `internal_tables/manager.py` | same | A (new mixin roster) |

**Hotspots (from the internal-tables survey):**
- Schemas: `_ab_results` PK `(experiment, metric, name_1, name_2, method_config_id, end_ts)`, `ReplacingMergeTree(created_at)` + `version_column='created_at'`, full data-contract §2 column set (~35 cols incl. `srm_flag/srm_pvalue`, `ci_kind`, `is_horizon`, `insufficient_data`, `decision_blocked`, `watermark_ts`); `_ab_exposures` PK `(experiment, unit_id)`, read-only after load; `_ab_tasks` plain MergeTree, key `(experiment, scope, process_type)` — "drops last_alert_sent/alert_count/last_recovery_sent"; `_ab_unit_state` keyed `(experiment, source_table/column_set_id, unit, day)` per §5.2/5.3, `ReplacingMergeTree(version)` replace-not-sum on CH, plain upserted moments table on PG/MySQL — "the single place the one-model-fits-all assumption breaks"; choose the model by manager type inside `_unit_state.py`, keeping `_schema` generic.
- Strict monotonicity: "detectkit versions LWW rows with `now_utc_naive()` per batch (ms precision)… two upserts within the same millisecond would tie — abkit needs a monotonic version source (e.g., last-value+1 tick guard)." Implement `next_version_ts()` in `_base.py`: `last = max(now_ms, last + 1ms)` per manager instance; cross-process ties are excluded by the WP2 lock at (experiment[, metric]) grain — state this in the docstring.
- `_results.py` new readers: `list_computed_cutoffs(experiment, metric, method_config_id) -> set[end_ts]` ("a SET reader, not just get_last_* — the cumulative grid is not a cursor; late/backfilled cutoffs make max() insufficient"), FINAL-deduped + epoch-normalized; `list_method_config_ids(experiment[, metric])` for `abk clean` drift pruning.
- `_tasks.py`: keep the whole state machine (stale-timeout override, force, clear/release/check) but acquire via the WP2 `try_acquire_lock`; drop `last_processed_timestamp` resume-cursor role (or keep as informational last end_ts); note `update_task_progress` is dead code in detectkit — drop.
- `_maintenance.py`: `EXPERIMENT_KEYED_TABLES = (_ab_experiments, _ab_exposures, _ab_unit_state, _ab_results, _ab_tasks)`; `_ab_aa_runs` excluded (audit trail).

**Tests:** rewrite `test_tables.py` content as the **`_ab_results` §2 contract regression test** (exact columns/PK/engine/version_column per table); port `test_internal_tables.py` locking matrix adapted to the claim API; port `test_internal_tables_agnostic.py` and extend the drive-list to all new mixins (cheapest enforcement of the generic-manager invariant); adapt `test_internal_tables_e2e.py` (LWW reinsert, epoch sentinel, SQL-injection) + **ADD**: (a) two-process atomic-claim race test on PG/MySQL (testcontainers), (b) the §5.2 **twice-run `_ab_unit_state` invariant** (state stage twice for one day ⇒ aggregates unchanged), (c) same-millisecond double-upsert `created_at` distinctness test.

**Must-fixes discharged:** *`_ab_unit_state` idempotent per (exp, day)*; *`_ab_unit_state` cardinality (source-table+column-set+unit)*; *`created_at` strictly-increasing & distinct*; *correctness under async merge* (FINAL/argMax on all mixin reads); completes *atomic lock*.

---### WP4 — Config layer (pydantic models; can start after WP1, parallel to WP2/WP3)

**Goal:** the five config models + level-1 validation, with `method_config_id` delegated to the M1 stats core.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/config/project_config.py` | `abkit/config/project_config.py` | A |
| `detectkit/config/profile.py` | `abkit/config/profile.py` | ⟲ (make the CH manager import **lazy** so config doesn't hard-require WP2 at import time) |
| `detectkit/config/metric_config.py` | `abkit/config/metric_config.py` | A — write fresh keeping ~300/897 loc ("do not port-then-delete") |
| `DetectorConfig` (template) | `abkit/config/method_config.py` | RW |
| `MetricConfig` (template) | `abkit/config/experiment_config.py` | RW |
| `detectkit/config/validator.py` | `abkit/config/validator.py` | A — level-1 only in this WP (discovery + uniqueness, parameterized by namespace) |
| `detectkit/config/__init__.py` | `abkit/config/__init__.py` | A |

**Hotspots (from the config survey):**
- project_config: drop `resolve_alert_help_url` ("imports detectkit.alerting at call time — must go or it drags alerting into M2"), `false_alert_budget`; ADD the statistical-defaults block: `alpha, test_type, correction, power, aa_fpr_budget, compute: {mode}, max_looks (5000), warn_looks (100), min_units_per_arm (~100)` with range validators; tables block = the six `_ab_*` names; timeouts `load/compute`.
- method_config: "CRITICAL: do NOT re-implement hashing or a parallel param schema… MethodConfig must resolve the class via the abkit.stats registry and delegate both param validation (instantiate the method class) and the method_config_id" — i.e. `MethodConfig.build(alpha) -> BaseMethod` via `create_method`, `method_config_id` read off the instance. Per stats-report awkward-point 4: instantiation IS validation ("`paired-post-normed-bootstrap` cannot even be instantiated with default params — config validation must catch `QuarantinedMethodError` at plan time, not run time").
- experiment_config: fields per declarative-config §2 incl. the **cadence union** (duration string OR `[{every, until}]` coarsening schedule — "NO dtk parsing ancestor"); `data_lag`, `timezone`, assignment block (`query_file`, `added_filters` must-start-with-AND, ordered `variants` first=control, `expected_split`), `sequential`, `comparisons[]` with inline MethodConfig. Intra-file validators only; cross-file rules go to WP6.
- metric_config: keep name/tags/query-XOR-query_file/`get_query_text`/`from_yaml_file`; ADD `type: fraction|sample|ratio` + a columns role-mapping model with a model_validator enforcing role-set ↔ type; optional `unit_key` (inherited).
- validator level-1: parameterize `discover_*`/`is_discoverable_*` by (dir, config class), run over `experiments/` + `metrics/`, add the explicit cross-namespace collision rule; "keep the `.history` exclusion + its regression test even though tune ships in M3."

**Tests:** port `test_profile.py` (358 loc), `test_metric_config.py` core subset (~40%), `test_validator.py` level-1 incl. the `.history` regression suite, `test_env_interpolation.py` (WP1); write fresh: project-config statistical-defaults ranges, MethodConfig delegation (byte-identity of `method_config_id` vs `abkit.stats.compute_method_config_id`, quarantine fail-fast, seed-rejected-for-closed-form pass-through), ExperimentConfig cadence-schedule validators (strictly coarsening, increasing `until`), variants/expected_split rules.

**Must-fixes discharged:** *Canonical `method_config_id` (single spec, byte test at config level)*; groundwork for *two-level reference integrity* and *inspectable alpha*.

---

### WP5 — Templating + the packaged macro + the two loaders

**Goal:** the render/execute/validate path and the `_ab_exposures`↔macro↔loader contract (design the three together — the loaders survey's explicit instruction).

| Source | Target | Verdict |
|---|---|---|
| `detectkit/loaders/query_template.py` | `abkit/loaders/query_template.py` | A |
| — | `abkit/loaders/templates/abkit_assignment.jinja` | **NEW** (+ pyproject `[tool.setuptools.package-data] "abkit.loaders" = ["templates/*.jinja"]`, MANIFEST.in `recursive-include abkit/loaders/templates *.jinja`) |
| — | `abkit/loaders/exposure_loader.py` | **NEW** |
| `detectkit/loaders/metric_loader.py` | `abkit/loaders/metric_loader.py` | RW (~80% dropped) |
| `detectkit/loaders/__init__.py` | `abkit/loaders/__init__.py` | ⟲ + ExposureLoader |

**Hotspots (from the loaders survey):**
- query_template: swap built-ins to the declarative-config §5 set (`ab_experiment_id, ab_start_date/ab_end_date, ab_start_ts/ab_end_ts` (end **exclusive**), `ab_cov_start/ab_cov_end, ab_variants, ab_unit_key, ab_added_filters, data_database, internal_database`) as one builtins dict; **add a template loader** — "detectkit has NO Jinja template loader… `{% import 'abkit_assignment.jinja' %}` raises TemplateNotFound; use `PackageLoader('abkit.loaders', 'templates')`"; **flip precedence** so ab_* built-ins win / collision raises ("shadowing ab_end_ts must not be silent" — a deliberate deviation from tested detectkit behavior); typed `TemplateRenderError`.
- macro: `exposed_units()` (JOIN `_ab_exposures` + BOTH the coarse `event_date` partition-pruning predicate AND the precise `event_time >= ab_start_ts AND < ab_end_ts` filter per §6.4 + per-unit dedup), `variant_col()`, `covariate_window(col, lookback)` on the **fixed whole-day lookback** (statistics-changes §5). Dialect risk: "the legacy LIMIT 1 BY dedup is ClickHouse-only" — see risk R6.
- metric_loader: DROP `_fill_gaps`, seasonality, `save()`, `load_and_save` cursor; KEEP render→execute→validate-columns→numpy skeleton and the exact error idiom; NEW: role mapping per metric type → `SufficientStats` per (variant[,stratum]) for closed-form OR per-unit `Sample`/`RatioSample` arrays for bootstrap (the stats dual entry); the **one-row-per-unit guard** ("len(rows) vs distinct unit_key count → loud warning 'did you forget GROUP BY unit_key?'"); observed variants ⊆ declared variants.
- exposure_loader: idempotent per experiment (delete-then-insert keyed by experiment), required columns `unit_key, variant, exposure_ts [, stratum]`, per-variant counts for SRM, **duplicate-unit-in-two-variants = hard error**; insert chunking lives in `internal_tables/_exposures`, not the loader.

**Tests:** port `test_query_template.py` (~24 tests; rewrite the precedence tests to the new built-ins-win behavior); new macro render tests (rendered SQL contains the `_ab_exposures` join, both window predicates, per-dialect dedup); fresh metric_loader tests (fraction/sample/ratio role mapping, suffstats vs per-unit output, guard, unknown-variant rejection, empty result); exposure_loader tests (idempotent re-run, duplicate-unit error, SRM counts).

**Must-fixes discharged:** *Packaged assignment macro*; *persist cohort once* (loader side); *loader one-row-per-unit guard*; *authoritative Jinja built-ins table + render test* (drift test vs the scaffolded example finalized in WP10).

---

### WP6 — Validator level-2 matrix + discovery/selector seam

**Goal:** the full declarative-config §8 battery behind `abk run --steps validate` (no DB), plus the shared project-root/selector machinery hoisted out of the CLI.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/cli/commands/run.py` lines ~375–509 (`find_project_root` / `select_metrics` / `find_metrics_by_tag`) | `abkit/config/discovery.py` | A — hoist, parameterize by (dir, config class) |
| `abkit/config/validator.py` | same file, level-2 extension | NEW (~2–3× the ported loc) |

**Hotspots:** the §8 matrix verbatim from the spec: comparison→metric resolution; unit_key equality/inheritance; no duplicate metric refs; method name ∈ registry + params validated **by instantiation** (catches CUPED-needs-covariate, Poisson-mean-only, quarantined branches); main/guardrail exclusivity + ≥1 main; assignment SQL selects the three columns; `expected_split ⊆ variants`; the cadence-and-looks gates (whole-second cadence; strictly-coarsening schedule; planned looks > `max_looks` → **error**; > `warn_looks` without sequential → peeking warning; `cadence < 1d` requires `data_lag`; sub-day + `alpha_spending` → error; `24h % cadence != 0` drift warning; `cadence > horizon` error; `covariate_lookback < 1d` error / `< 7d` warning); the StrictUndefined render smoke + **rendered-SQL-joins-`_ab_exposures` lint**. Look counting must call the same WP7 grid function the planner uses (single source — see R1). Selector: "--select resolves experiments only, --metric is a distinct flag, uniqueness errors name the namespace, experiment+metric names share one namespace."

**Tests:** port `test_select_metrics.py` (glob/star/.gitkeep robustness) adapted to `experiments/` + a namespace-collision error-message case; a fresh parameterized matrix test per §8 rule (happy + failing YAML fixture each); render-smoke test against a fixture project.

**Must-fixes discharged:** *Two-level reference integrity*; the cadence/looks gate battery; the macro-usage lint half of *packaged macro*.

---

### WP7 — `core/period_planner.py` + `pipeline/planner.py` (greenfield heart)

**Goal:** the expanding cumulative grid — scalar and schedule cadence, watermark, anti-join.

| Source | Target | Verdict |
|---|---|---|
| — | `abkit/core/period_planner.py` | **NEW** — pure function layer: `generate_grid(start_ts, horizon_ts, cadence, tz) -> list[Cutoff(end_ts, is_horizon)]` |
| — | `abkit/pipeline/planner.py` | **NEW** — `plan(experiment, metric, method_config_id, internal_manager, now_utc) -> list[Cutoff]` |

**Hotspots (specs, not surveys — this has no donor):**
- Grid rules (§6.1/6.3): dense segments anchor at `start_ts`; daily segments snap to experiment-timezone midnights; horizon point always appended with `is_horizon=1`; NO `look_index` stored; half-open `[start_ts, end_ts)` windows.
- Watermark (§6.2): `watermark_ts = now_utc − data_lag` computed **once per run in Python** (never `now()` in SQL); `plannable ⇔ end_ts ≤ watermark_ts`; `data_lag: 0` + half-open windows reproduces `*_wo_curr_day` exactly at 1d.
- Anti-join: `grid − internal_manager.list_computed_cutoffs(...)` (the WP3 set reader, FINAL-deduped) — replaces every detectkit cursor; "do NOT port any resume/boundary-snap/context-window arithmetic" (orchestration survey).
- Backlog warning: "run warns when `watermark_ts − max(computed end_ts)` exceeds a few cadence steps" (§6.4).

**Tests (the highest-value test surface in M2):** golden daily-grid test reproducing the legacy `[start..start+d]` enumeration incl. the `*_wo_curr_day` parity at `data_lag=0`; schedule-grid tests (1h-until-48h-then-1d point set, midnight snapping, non-UTC timezone, horizon append when cadence doesn't divide duration); look-count consistency test (validator's count == len(grid)); anti-join skip/backfill tests (a late hole in the middle is re-planned); watermark determinism (grid depends only on `(config, now_utc)`).

**Must-fixes discharged:** *Deterministic completeness boundary* (§5.6, sharpened by §6.2).

---

### WP8 — Pipeline: driver, analyze, enrich, SRM gate, worker pool

**Goal:** `run_experiment()` end-to-end: lock → catalog upsert → plan → (unit-state) → load → SRM → compute → enrich/persist.

| Source | Target | Verdict |
|---|---|---|
| `orchestration/task_manager/_types.py` | `abkit/pipeline/_types.py` | A — `PipelineStep {VALIDATE, PLAN, STATE, LOAD, COMPUTE}`, `TaskStatus` ⟲; **delete `make_alert_config_id`** |
| `orchestration/task_manager/manager.py` + `_base.py` | `abkit/pipeline/driver.py` | A |
| `orchestration/task_manager/_detect_step.py` | `abkit/pipeline/analyze.py` | RW (structural ancestor only) |
| — | `abkit/pipeline/enrich.py` | **NEW** |
| — | `abkit/compute/recompute_backend.py` | **NEW** thin (keeps the architecture module map; v1 full-window aggregation; `incremental_backend` deferred to v2) |
| `_load_step.py`, `_alert_step.py`, `error_dispatch.py` | — | rewrite-into-analyze / skip / skip |
| `orchestration/*/__init__.py` | `abkit/pipeline/__init__.py` | A (merged) |

**Hotspots (orchestration survey + stats report):**
- driver: keep verbatim "PIPELINE_LOCK_TIMEOUT_SECONDS=3600 stale-override, force takes-ownership-and-releases healing, mark-failed-before-finally **BaseException** handling (Ctrl+C recorded as failed then re-raised — a reviewed regression fix), never-release-a-lock-you-did-not-acquire"; keep `abort_run` semantics with alerting stubbed; result dict → `{exposures_loaded, cutoffs_planned, results_written, srm_flagged, status, error, abort_run}`; move the catalog upsert **inside** the locked section (survey risk: "two concurrent runs could race the catalog upsert — re-check under the worker pool"); NEW worker pool (`concurrent.futures`) across experiments — "zero concurrency anywhere in detectkit; the M1 default_rng change already made abkit.stats process-safe."
- analyze: per pending cutoff → exposure counts → `srm_check(observed, expected_split)` → per comparison: build containers per the method-family matrix (stats report §3) — closed-form via `from_suffstats` (reusable instances), bootstrap via `from_samples` with a **fresh instance per row** carrying `params["seed"] = derive_seed(experiment, metric, name_1, name_2, end_ts, n_samples)` (stats report: "instances are not reusable across cumulative looks for bootstrap"); paired methods need ONE `PairedSufficientStats` per pair (arity break, awkward-point 2); `insufficient_data` demotion below `min_units_per_arm` (row written, NULLed test columns, counts+SRM kept). Alpha: `two_tier_alphas(alpha, groups, non_main_metrics)` → `create_method(..., alpha=tier)`; echo the effective alpha + divisor via the stage renderer.
- enrich: the full stats-report §4 mapping — everything TestResult lacks: identity (`method_config_id` **from the instance**, `method_params` via `json_dumps_sorted(result.method_params)` — "any other serialisation breaks the BI-filters==identity invariant"), window (`start_ts/end_ts/start_date/end_date/window_seconds/elapsed_days`), integrity (`srm_flag/srm_pvalue` broadcast, `decision_blocked`, `insufficient_data`), sequence (`ci_kind='fixed'`, `is_horizon`), provenance (`metric_query`, `metric_rendered_query`, `watermark_ts`, `created_at = next_version_ts()`). NaN→None ⇒ nullable test columns (already in WP3 schema). Decide warnings/diagnostics homes (R7).
- STATE stage: thin v1 materialization writing `_ab_unit_state` day rows at day close only (§6.4); read path stays recompute.

**Tests:** adapt `test_task_manager.py` (lock contract, partial steps, error propagation) + `TestBaseExceptionLockStatus`; enrich contract test (every §2 column present + typed; canonical `method_params` string byte test); the **idempotent byte-stable re-run** test (run twice against a fixture backend ⇒ identical `_ab_results` payload rows incl. bootstrap p-values via derived seeds; second run plans zero cutoffs); two-tier Bonferroni **golden test** keyed off `is_main_metric` (declarative-config §6); SRM broadcast + `decision_blocked` test; `insufficient_data` demotion test; worker-pool smoke (two experiments concurrently, distinct locks).

**Must-fixes discharged:** *Concurrency model (lock grain + worker pool)*; *inspectable alpha* (compute + echo halves); *SRM* (gate half); *bootstrap seed policy* (pipeline binding of the M1 mechanism); *persist cohort once* (pipeline enforcement: assignment SQL runs once per run).

---

### WP9 — CLI: `main`, `_output`, `run`, `unlock`, `clean`

| Source | Target | Verdict |
|---|---|---|
| `detectkit/cli/_output.py` | `abkit/cli/_output.py` | ⟲ (swap `AUTOTUNE_STAGE_TITLES` for `RUN_STAGE_TITLES` VALIDATE/PLAN/STATE/LOAD/SRM/COMPUTE/RESULT) |
| `detectkit/cli/main.py` | `abkit/cli/main.py` | A |
| `detectkit/cli/commands/run.py` | `abkit/cli/commands/run.py` | A (selector/root-discovery already hoisted to `config/discovery.py` in WP6) |
| `detectkit/cli/commands/unlock.py` | `abkit/cli/commands/unlock.py` | ⟲ |
| `detectkit/cli/commands/clean.py` | `abkit/cli/commands/clean.py` | A |
| `cli/__init__.py`, `commands/__init__.py` | same | ⟲ |
| `init_claude.py, autotune.py, tune.py, test_alert.py, osi.py` | — | skip (M3/M4/M6; OSI removed entirely incl. its non-lazy import) |

**Hotspots (CLI survey):** keep the lazy-import group + `version_option`; pyproject `abk = abkit.cli.main:cli` (exists since M0 — verify); `run --steps` default `'validate,plan,load,compute'`; drop `--report` for M2 but preserve the tri-state click pattern note; **fix the exit-code wart** ("every command swallows errors and returns → exit 0 on failure; cli-and-dx §3 makes the CLI the Prefect unit of automation — abk must exit non-zero," a documented deviation); clean: "the 'valid set' must be computed through the SAME code path the pipeline uses to stamp rows" — enumerate each experiment's comparisons' `MethodConfig.build().method_config_id`, diff vs `list_method_config_ids`, keep dry-run/`--execute`/`--yes`/mid-edit-warning guards verbatim; `--orphaned-metrics` → `--orphaned-experiments`; SRM red line in the StageLogRenderer (`SRM FAILED (observed … vs expected …) — effects untrustworthy`); orphan warning at run when an experiment has >1 `method_config_id` per metric.

**Tests:** port `test_cli_output.py`, `test_clean.py` guard layers; new: exit-code-on-failure test, SRM-line rendering test, effective-alpha echo test, `run --steps validate` no-DB test (monkeypatched manager asserting zero connections).

**Must-fixes discharged:** *SRM loud in CLI*; alpha-echo half of *inspectable alpha*; clean-side of the `method_config_id` idempotency story.

---

### WP10 — `abk init` scaffolder + runnable example + seed dataset + M2 e2e gate

| Source | Target | Verdict |
|---|---|---|
| `detectkit/cli/commands/init.py` | `abkit/cli/commands/init.py` | A — mechanism ⟲ (string constants, sentinel substitution, refuse-existing-dir), content ~80% rewritten |
| — | scaffold payload: `abkit_project.yml`, `profiles.yml`, `experiments/example_signup_test.yml`, `sql/assignment.sql`, `metrics/signup_cr.yml` (+ e.g. `metrics/arpu.yml` with CUPED), seed-dataset SQL/loader per backend, `runners//deployments/` Prefect scaffold, README | **NEW** |
| — | `tests/e2e/test_first_run.py` | **NEW** |

**Hotspots (CLI survey + cli-and-dx §6):** "the §6 must-fix runnable example (experiment YAML + assignment.sql + metric SQL + synthetic seed dataset) has NO detectkit analog and is the largest new-code area in this subsystem"; the example metric carries the annotated pinned-start/moving-end Jinja + one-row-per-unit comment block and **uses the packaged macro** and the fixed covariate lookback; keep detectkit's discipline that "every scaffolded artifact round-trips through the real pydantic config classes" (this is also where declarative-config §5's "tested against the scaffolded example so docs & examples cannot drift" render test lands, closing the WP5/WP6 loop); `_BUCKET_SQL` sentinels replaced by per-dialect assignment/metric snippets.

**Tests:** port `test_init.py` (parametrized db_type, refuse-existing) + `test_review_regressions.py::TestInitScaffolding` pattern; the **M2 DoD e2e**: testcontainers ClickHouse → `abk init` → seed load → `abk run --select example_signup_test` → assert real `_ab_results` rows → re-run → byte-stable + zero planned cutoffs → `abk unlock`/`abk clean` smoke.

**Must-fixes discharged:** *Runnable first-run example against a seed dataset*; final integration proof of macro/alpha/boundary/lock/SRM.

---

## 2. Dependency graph / parallelism

```
WP1 (core+utils leaves)
 ├─► WP2 (DB managers) ─► WP3 (schema + internal_tables) ─┬─► WP7 (planner)
 ├─► WP4 (config; profile's CH import made lazy)          │
 │        └───────────────┬───────────────────────────────┤
 └─► WP5a (query_template + macro skeleton)               │
              WP5b (loaders) needs WP3 + WP4 ─────────────┤
                     WP6 (validator L2 + discovery) needs WP4 + WP5
                            WP8 (pipeline) needs WP3+WP5+WP6+WP7 + M1 stats
                                   WP9 (CLI) needs WP6 + WP8
                                          WP10 (init + e2e) needs everything
```

Three parallel tracks after WP1: **Track A** WP2→WP3 (DB), **Track B** WP4 (config), **Track C** WP5a (query_template can land against fixture built-ins before the exposures schema exists). WP7 needs only WP1 for the pure grid function (`core/period_planner.py` first, `pipeline/planner.py` after WP3). WP6/WP8/WP9/WP10 are the serial spine. The exposures-macro-loader triangle (WP3 `_exposures.py` + WP5 macro + exposure_loader) is one contract — coordinate those reviews even though they're in different WPs.

## 3. Riskiest design points (decide during implementation)

**R1 — Sub-day grid semantics as ONE pure function.** Risks: validator look-count vs planner grid drift; tz-midnight snapping vs dense anchoring at `start_ts`; horizon append duplication when cadence divides the duration exactly. **Recommendation:** a single pure `generate_grid()` in `core/period_planner.py` consumed by BOTH `config/validator.py` (look gates, `abk plan` later) and `pipeline/planner.py`; snap daily-cadence points with `zoneinfo` on the experiment tz then convert to naive-UTC; dedupe the horizon point if it coincides with a grid point (keep `is_horizon=1` on it); property-test that grids for `cadence: 1d` and `[{every: 1d}]` are identical, and that a `1h→1d` schedule's daily tail is point-for-point equal to a pure-daily grid (spec §6.1 comparability promise).

**R2 — `_ab_unit_state` per-backend schema + `column_set_id`.** Risks: AggregateFunction columns crashing `_canonical_type` on PG/MySQL; the (source-table, column-set) key needing an identity for "column set". **Recommendation:** `_unit_state.py` owns two TableModel factories chosen by `isinstance(manager, ClickHouseDatabaseManager)` (keeps `_schema.py` and the base manager generic, per the internal-tables survey); v1 CH model = plain `ReplacingMergeTree(version)` moments columns (NOT AggregateFunction — defers real agg-state DDL to v2 where the read path flips); `column_set_id = sha256(json_dumps_sorted({source_table, sorted(column_roles)}))[:16]`; the STATE stage is wired but cheap in v1 (read path stays recompute), and the twice-run test is non-negotiable now ("the corruption is silent until v2 flips the read path").

**R3 — Atomic lock API shape.** Risk: re-smuggling `_ab_tasks` semantics into the managers while adding atomicity. **Recommendation:** manager exposes only `try_acquire_lock(table_name, key_columns, row, timeout_seconds) -> bool` + `upsert_record(..., sync=)`; `internal_tables/_tasks.py` assembles the row and the staleness policy; PG claims via `INSERT..ON CONFLICT DO UPDATE..WHERE` + rowcount, MySQL via row-alias conditional upsert + affected-rows, CH stays advisory (sync delete+insert+read-back) with the contract documented in `_tasks.py` and the compute docs; lock grain default `(experiment, 'pipeline', 'pipeline')` with `(experiment, metric, 'compute')` reserved in the key shape for later per-metric parallelism.

**R4 — `method_config_id` wiring = instantiation.** Risk: a second hashing path or a config-time param schema fork. **Recommendation:** `MethodConfig` stores only `{name, params}`; `bind(alpha) -> BaseMethod` calls `create_method`; validation, quarantine, and the id all come from the one instantiation, done at **validate/plan time** and cached per comparison; closed-form instances reused across cutoffs, bootstrap re-bound per row with the derived seed injected into a params copy (`seed` is identity-excluded so the id is stable). Persist the resolved full `method.params` nowhere in v1 but note stats-report awkward-point 7 (frozen defaults = ALGORITHM_VERSION territory) in `statistics-changes.md`.

**R5 — Strictly-monotonic `created_at`.** Risk: same-ms ties under ReplacingMergeTree/LWW within one process; cross-process ties. **Recommendation:** `next_version_ts()` in `internal_tables/_base.py` (`max(now_ms, last+1ms)` per manager instance), applied to every LWW-table write; cross-process serialization is delegated to the R3 lock (document this coupling); note the quorum nuance that **BI dedup examples use `argMax`/`LIMIT 1 BY`, not FINAL** — internal correctness reads keep `final_modifier`, the WP10 README/BI notes use argMax.

**R6 — Jinja precedence flip + macro dialect.** Risks: silently shadowed `ab_end_ts` (correctness) vs. `LIMIT 1 BY` being CH-only. **Recommendation:** built-ins win, collision with an `ab_*` name raises `TemplateRenderError` (deviation from detectkit's tested behavior — record it in CHANGELOG); add an `ab_dialect` built-in and make `exposed_units()` emit `LIMIT 1 BY` on ClickHouse and a `ROW_NUMBER() OVER (PARTITION BY unit) = 1` subquery on PG/MySQL — the managers already support three backends, shipping a CH-only macro would strand them; the survey's fallback ("ClickHouse-first with documented restriction") only if the ROW_NUMBER form fails review time-box.

**R7 — `warnings`/`diagnostics` have no `_ab_results` columns** (stats-report awkward-point 6). Risk: losing θ, boot_mean, H5 explanations — "the only human-readable failure signal." **Recommendation:** since §2 is "the proposed v1 contract" and greenfield, add two nullable String columns `warnings` (JSON array via `json_dumps_sorted`) and `diagnostics` (JSON object) in WP3, and amend data-contract-and-reporting.md §2 in the same PR (never change the contract silently). Wrap analyze in a `catch_warnings` fence routing python-level `AbkitStatsWarning`s into the row instead of stderr noise.

**R8 — Loader output shape / method-family capability dispatch.** Risk: fragile `isinstance` imports from `abkit.stats.bootstrap` / `abkit.stats.parametric.paired_ttest` (stats-report awkward-point 1). **Recommendation:** add declarative class attributes to `BaseMethod` in a tiny M1-followup commit inside WP8 — `supports_suffstats: ClassVar[bool]`, `is_paired: ClassVar[bool]`, `input_kind: ClassVar[str]` ("sample"|"fraction"|"ratio") — a zero-number-change addition (no ALGORITHM_VERSION bump; add a stats unit test). analyze.py then dispatches on `(metric.type, method.input_kind, method.supports_suffstats, method.is_paired)` with a config-lint error for impossible combos (e.g. fraction metric × bootstrap).

**R9 — Exposure loader idempotency vs "read-only" exposures.** Risk: §5.5 says load-once; re-runs must not mutate the cohort mid-experiment silently, but late-arriving exposures are real. **Recommendation:** default = delete-then-insert per experiment each run (self-healing recompute matches the pipeline philosophy; SRM re-checked each run), and the row set is naturally stable for a concluded window; "read-only" means *abkit never writes back into it from compute* and users never edit it. Log a loud diff when the reloaded cohort size changes vs `_ab_exposures` (assignment-source drift signal). Revisit a `has_exposures` skip-guard only if the assignment scan proves expensive.

## 4. M2 definition-of-done → WP map

| DoD item (ROADMAP M2 + quorum) | Proven by | WP |
|---|---|---|
| `abk init && abk run --select example_signup_test` → real results on a fresh machine vs a seed dataset | `tests/e2e/test_first_run.py` (testcontainers) | **WP10** (built on 2–9) |
| Idempotent re-run is byte-stable (incl. bootstrap via derived per-row seeds; second run plans 0 cutoffs) | byte-stable re-run test | **WP8** (seeds), **WP7** (anti-join), e2e in **WP10** |
| Atomic lock (PG/MySQL single-statement; CH advisory documented; (exp[,metric]) grain) | claim-SQL unit tests + two-process race test | **WP2**, **WP3** |
| Strictly-monotonic distinct `created_at` | same-ms double-upsert test; LWW e2e | **WP3** |
| One-row-per-unit guard | metric_loader guard test | **WP5** |
| Packaged assignment macro (no leaked boilerplate; example uses it; lint asserts the JOIN) | macro render tests + validator lint test + scaffold drift test | **WP5**, **WP6**, **WP10** |
| Inspectable alpha (declared alpha+correction; effective per-comparison alpha echoed; two-tier Bonferroni golden test) | two-tier golden test + StageLogRenderer echo test | **WP8**, **WP9** |
| Deterministic completeness boundary (`watermark_ts = now_utc − data_lag`, never `today()` in SQL; `*_wo_curr_day` parity at 1d) | planner golden/parity tests; `watermark_ts` provenance column check | **WP7**, **WP3** |
| SRM in CLI (red line; blocking-but-non-dropping; `srm_flag=1` row still written) | SRM broadcast test + CLI rendering test | **WP8**, **WP9** |
| Sub-day cadence first-class (duration/schedule cadence, `end_ts` contract, `data_lag` lint, `max_looks`/`warn_looks`, `insufficient_data` demotion, `ab_start_ts/ab_end_ts` built-ins) | cadence validators + grid tests + demotion test + built-ins test | **WP4**, **WP5**, **WP6**, **WP7**, **WP8** |
| `_ab_unit_state` idempotent per (exp, day), correct cardinality key | twice-run invariant test (integration) | **WP3** |
| Correctness under async merge (FINAL/argMax on all correctness-sensitive reads) | agnostic-guard + `list_computed_cutoffs` dedup tests | **WP3** |
| Two-level reference integrity + Jinja built-ins table + render smoke (`run --steps validate`, no DB) | §8 matrix tests + no-DB-connection test | **WP6**, **WP9** |
| Canonical `method_config_id` (one spec; byte-exact at config level; `clean` prunes drift through the same path) | MethodConfig delegation byte test + clean valid-set test | **WP4**, **WP9** |
| Read-only exposures / cohort persisted once | exposure-loader idempotency test + macro-join lint | **WP5**, **WP6** |

Exit gate: all WP test suites green on CI (unit + testcontainers integration for all three backends), the WP10 e2e green, CHANGELOG entries for the two deliberate behavior deviations (Jinja precedence flip, non-zero CLI exit codes), and data-contract-and-reporting.md §2 amended if R7's warnings/diagnostics columns are adopted.
---

## 5. Adversarial review record (M2 exit gate, 2026-07-03)

Six finder lenses (locks/concurrency, time & grids, SQL rendering, statistical
binding, storage contract & backend fidelity, DoD audit) → 27 findings, each
adversarially verified by an independent refuter (0 refuted) → **all 27
applied** in the review-fixes commit. The majors:

1. **ClickHouse advisory claim hardened**: the step-2 DELETE is now conditional
   (never erases a rival's live confirmed claim), the heartbeat is re-stamped
   at INSERT time (winner order tracks insert order), a settle pause precedes
   the read-back, and a loser deletes its own row. Residual advisory
   limitations (cross-host clock skew, insert-visibility skew) are documented.
2. **Ownership-checked release**: `release_lock` verifies the stored
   `locked_by` token against the one recorded at acquire time — a run whose
   lock aged out and was stolen no longer wipes the new owner's row
   (`abk unlock` keeps its deliberate `force=True`). The driver surfaces a
   lock-takeover warning.
3. **Experiment-timezone dates**: `_ab_results.start_date`/`end_date` are now
   experiment-tz calendar dates; the exposure-load window uses the grid's
   tz-snapped bounds (was naive calendar midnights); the CUPED pre-period is
   whole-day aligned in the experiment tz.
4. **Macro hardened**: cohort subquery columns are `_abk_`-prefixed
   (collision-proof against fact tables with `unit_id`/`variant`/... columns);
   the fact-side unit key is cast to string per dialect; `added_filters` no
   longer auto-injects into metric fact scans (assignment scope only).
5. **`to_naive_utc` converts** aware non-UTC values to UTC (was re-labelling).
6. **CI e2e gate**: the testcontainers ClickHouse first-run job added to CI;
   the PG/MySQL integration suite is a recorded deferral (ROADMAP M2).

Minors: SRM zero-arm flag-not-crash, CH NaN→NULL insert coercion, sync catalog
upsert, table-override rejection guard, R8 capability lint at validate time,
whole-day lookback lint, stratum-column typed error, `ab_cov_*` built-ins
StrictUndefined-honest, DST fall-back day-space segment bounds, tail-cadence
backlog threshold, pool DDL serialization, fake-backend FINAL honesty (the
internal-tables suite now runs in both `sql-like` and `clickhouse-like` modes).
