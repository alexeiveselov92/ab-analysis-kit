# M8 Implementation Plan — assignments: no-copy default + incremental copy

> **As-designed contract for M8** (polish track M7–M17, approved by the maintainer
> 2026-07-18 — see [ROADMAP.md](../../ROADMAP.md) "The polish track"). Targets release
> **`0.3.0`**. **Not yet implemented** — this document is the contract the
> implementation sessions execute, in the shape of
> [m6-implementation-plan.md](m6-implementation-plan.md) /
> [m4-implementation-plan.md](m4-implementation-plan.md). It becomes the
> implementation record at the WP7 exit gate (the m4–m6 pattern): as-built notes,
> the adversarial-review log, and any settled open questions are appended in place,
> never rewritten. Nothing below should be read as claiming shipped code — every
> section is written "WP2 adds…", "the gate asserts…", contract/future tense
> throughout.
>
> Governing specs: [declarative-config.md §4](declarative-config.md) (the packaged
> assignment macro) + [§5](declarative-config.md) (the `ab_*` builtins table),
> [data-contract-and-reporting.md §6](data-contract-and-reporting.md) (SRM
> surfacing — the chip this milestone must keep byte-identical in both modes),
> [architecture.md](architecture.md) (the `load` stage + the `_ab_exposures`
> internal-table row), [ROADMAP.md](../../ROADMAP.md) M8. Sibling milestone docs:
> [m4](m4-implementation-plan.md) (the `abk validate` shape this milestone's call
> sites must keep wired), [m6](m6-implementation-plan.md) (the docs three-way-sync
> discipline this milestone's WP7 follows). Source audit:
> [docs/research/2026-07-data-flow-audit/REPORT.md §1](../research/2026-07-data-flow-audit/REPORT.md)
> (items #3–#4 — "no-copy default", the incremental-copy ask).
> Donor for WP5: `/home/aleksei/wsl_analytics/detektkit`
> (`detectkit/loaders/metric_loader.py`,
> `detectkit/orchestration/task_manager/_load_step.py`).

## 0. Scope, posture & decisions

### 0.1 Goal

Flip the exposures read path so metric SQL joins the user's assignment source
**directly by default** (no `_ab_exposures` write on the hot path), keep a persisted
copy available as an **opt-in** flag for heavy or mutating sources, and — when that
copy is on — load it **incrementally** (watermark + closed intervals + batches,
ported from detectkit) instead of delete+reinsert. **Zero statistical numbers
change** — this milestone is purely about where reads come from, never about the
math over them.

### 0.2 Posture: statistical numbers do not move anywhere

M8 is one of the M7–M12 core-track milestones under the track-wide discipline (see
[ROADMAP.md](../../ROADMAP.md) "The polish track"): **no `ALGORITHM_VERSION` bump**,
no golden retolerancing, `abkit.stats` purity intact
(`tests/stats/test_purity.py`), and a `grep ALGORITHM_VERSION` diff across the
milestone stays empty. Every deliverable is a data-provenance / performance change:
a config surface, a pushdown query, a Jinja builtin, a call-site refactor, a
loader, or documentation. The parity discipline this milestone's gates enforce:
**exact** equality on integer counts (SRM observed counts, row counts, arrival-rate
numerators) and **rel-1e-9** on any continuous value that transits the new path
(effect, CI bounds, p-values) — the same convention `abk validate`'s parity suite
uses. Where a per-WP estimate below exceeds the approved plan's compressed
milestone table (WP5/WP6 carry the detailed breakdown's 2 sessions vs. the
table's 1 each, nudging the WP-sum past the plan's "~7–8"), the detailed
estimate is carried deliberately — the post-M7/M8 retro-calibration step
reconciles the totals. Session estimates below are **not contracts**: a WP that doesn't fit a
session simply continues into the next one (per-WP estimates are a planning aid,
not a gate).

### 0.3 Today's shape (grounded in code — the baseline this milestone changes)

- **Full delete+reinsert, every run, no watermark.** `replace_exposures`
  (`abkit/database/internal_tables/_exposures.py:25-69`) unconditionally
  `delete_rows`s the experiment's rows (lines 44-46), then chunked-inserts the
  **entire** freshly-loaded cohort (`EXPOSURE_INSERT_CHUNK=100_000`, line 21).
  `exposure_loader.load_exposures()` (`abkit/loaders/exposure_loader.py:29-121`)
  always executes the full assignment SQL client-side (`manager.execute_query`,
  line 56), materializes every row into Python, runs a row-by-row Python dedup/
  conflict loop (lines 74-107), then calls `tables.replace_exposures(...)` (line
  116) unconditionally.
- **The packaged macro joins the persisted copy in every metric render.**
  `abkit_assignment.jinja`'s `exposed_units()` macro (lines 41-58) does
  `INNER JOIN {{ ab_exposures_table }}`, where `ab_exposures_table` is built in
  Python as `f"{internal_database}.{exposures_table}"`
  (`abkit/loaders/query_template.py:88`) — always the internal copy, never the
  user's source directly. Declarative-config §4/§5 documents this as the only
  mode today.
- **`AssignmentConfig` is SQL-only.** `abkit/config/experiment_config.py:62-109`
  has only `query`/`query_file` (mutually exclusive, validated by
  `validate_query_source`) — no table/view reference field, and (per the
  decision this milestone locks in, WP1) none is needed: `assignment.query`/
  `query_file` already accept arbitrary SQL text including
  `SELECT * FROM my_table`, so the no-copy default reuses that field verbatim as
  the direct-join source.
- **The cohort loads once per run today, in front of the compute loop.**
  `abkit/pipeline/driver.py:196-212` — "LOAD: the cohort, once per run (§5.5)" —
  then `RecomputeBackend(...)` is constructed once (line 268) and iterated per
  comparison. This milestone's factory (WP4) preserves that one-render-per-run
  shape for the validation/SRM pass while making the *metric* render re-execute
  the (deduped) source per `(metric × cutoff)` in direct mode — the accepted
  cost/freshness tradeoff the audit names.
- **Blast radius is fully mapped (nothing left to discover):** the macro join
  (`abkit_assignment.jinja:41-58`); whole-cohort + sub-day SRM counting
  (`_exposures.py:79-141`, consumed by `driver.py:207-233`); `abk plan`
  runtime/ASN (`_exposures.py:143-199`, `abkit/cli/commands/plan.py:211-225`,
  `abkit/planning/sizing.py:21`); BI/reporting's SRM block
  (`abkit/reporting/builder.py:388-390`); `abkit/validate/load.py`, an *indirect*
  consumer through `RecomputeBackend.load_cutoff` (never a direct `_ab_exposures`
  reader); `abkit/compute/recompute_backend.py:54,102` (the constructor
  parameterization point that already exists); maintenance/purge
  (`_maintenance.py:21,29`); `config/validator.py:340`'s render-smoke stub; and
  docs/init-claude scaffold text throughout.
- **Explicitly out of the blast radius: the Grafana reference dashboard.**
  `docs/examples/bi/grafana_dashboard.json` and
  `docs/reference/legacy_grafana_dashboard.json` query `_ab_results` only — a
  repo-wide grep finds zero `_ab_exposures` references in either file. No WP in
  this milestone touches them; this is stated once here so no WP re-derives it.

### 0.4 Decisions carried into this contract (from the approved track plan)

These were settled with the maintainer before WP work starts and are **not**
re-opened by any WP below:

1. **No separate "reference an existing table" field.** `assignment.query`/
   `query_file` already accept `SELECT * FROM my_table`; WP1 adds only the
   `assignment.copy` block (the incremental-load knobs), not a new
   source-reference field.
2. **`ProjectTablesConfig.reject_overrides` stays untouched in this milestone.**
   Unblocking internal-table-name overrides
   (`abkit/config/project_config.py:28-52`) is a separate, larger change (every
   mixin is keyed off the `TABLE_EXPOSURES` constant, not a configured name) and
   is explicitly out of scope here — WP1 adds one clarifying docstring sentence,
   nothing else.
3. **"Never change a number silently" applies to this milestone's read-path
   swap too.** Direct-join output must match copy-mode output exactly (or
   rel-1e-9) on a well-formed (no-duplicate, no-conflict) cohort — this is the
   milestone's core numeric-parity gate, not a stats change, but held to the
   same discipline.
4. **detectkit's watermark/closed-interval/batch discipline is the WP5 donor**,
   ported with its known limitation (late-arriving rows below the watermark are
   silently missed) disclosed rather than re-engineered away.

### 0.5 Plan-review record (pre-implementation)

The track plan underwent a 3-critic adversarial review before any WP work
started; the following milestone-specific corrections apply to M8 and are
carried into the WPs below rather than left as ambient context:

- **(a) Grafana is out of the blast radius (confirmed).** See §0.3 above — no WP
  touches the Grafana reference dashboard; stated once so the exit gate does not
  look for a Grafana regression that cannot exist.
- **(b) The WP2 review fixture: duplicate exposure rows differing in BOTH
  timestamp AND stratum.** The naive `MIN(exposure_ts)`/`MIN(stratum)` pushdown
  aggregation can pick the earliest-`exposure_ts` row's timestamp but a
  *different* row's stratum (each `MIN()` resolves independently), whereas
  today's Python loop resolves both fields off the **same** winning row. WP2
  must add this exact fixture (dup rows, same unit, differing ts *and*
  stratum) to its parity test and either (i) prove the aggregation still picks
  the same winning row's stratum in every case tested, pinning parity, or (ii)
  document the divergence explicitly as an accepted, disclosed behavior change
  — it must not go unnoticed.
- **(c) `assignment.query` already accepts `SELECT * FROM my_table`.** No
  separate table-reference field is introduced anywhere in this milestone (see
  §0.4.1) — WP1's config surface is additive-only (the `copy` block).
- **(d) Existing error-message texts are a compatibility gate.** WP2's
  pushdown refactor of `load_exposures()` must keep every existing
  `tests/loaders/test_loaders.py` assertion passing **unmodified** — byte-identical
  error/warning text is the regression proof that the pushdown query is
  behavior-preserving, not just a performance change. No WP in this milestone
  may edit those assertions to make them pass; a text change would need its own
  justified test update, not a silent rewrite.
- **(e) `build_cohort_backend` / `ab_cohort_source` is a binding inter-milestone
  contract, not an internal implementation detail.** This milestone introduces
  the single factory (WP4) and the single Jinja builtin (WP3) that every
  cohort-source render must go through. **M9 is REQUIRED to build its STATE
  writer and tail-scan cohort SQL exclusively through this factory** — a
  plan-review finding flagged this as a **track-level blocker**: a hand-rolled
  render in M9 that assumes `_ab_exposures` exists would silently join a
  non-existent table under the no-copy default and produce silent zeros, not an
  error. This contract is stated here in full (see also
  [ROADMAP.md](../../ROADMAP.md) "Inter-milestone contracts") so M9's design
  session cannot miss it: **no future milestone renders cohort SQL by hand.**

---

## 1. Work packages in strict dependency order

### WP1 — Config surface: opt-in incremental-copy block on `AssignmentConfig`

**Goal:** add `assignment.copy` (default disabled) carrying the incremental-load
knobs the donor pattern needs (update column, batch interval, batch count,
maturity delay) — additive-only, per §0.4.1 no new source-reference field is
introduced.

**Files:** `abkit/config/experiment_config.py`, `abkit/config/project_config.py`,
`docs/specs/declarative-config.md`, `tests/config/test_experiment_config.py`.

**Steps:**
1. In `experiment_config.py` add `class CohortCopyConfig(BaseModel)`:
   `enabled: bool = Field(default=False)`, `update_column: str =
   Field(default='exposure_ts')`, `batch_interval: int | str =
   Field(default='1d')`, `batch_intervals_per_round_trip: int =
   Field(default=30, gt=0)` (mirrors detectkit's `loading_batch_size`, measured
   in interval-*counts* not row-counts —
   `/home/aleksei/wsl_analytics/detektkit/detectkit/orchestration/task_manager/_load_step.py:112-124`),
   `maturity_delay: int | str = Field(default=0)`.
2. Add a `@field_validator` on `batch_interval`/`maturity_delay` that parses
   them through `abkit.core.interval.Interval(...)` at config-parse time (the
   same pattern as `CadenceSegment._parses_as_interval`,
   `experiment_config.py:44-49`) so a bad grammar fails fast, not at run time.
3. Add `copy: CohortCopyConfig = Field(default_factory=CohortCopyConfig)` to
   `AssignmentConfig` (`experiment_config.py:62`), right after `added_filters`.
4. Add a model-validator on `AssignmentConfig`: when `copy.enabled` is `True`,
   `update_column` must be a non-empty identifier-looking string (a cheap
   sanity check only — the real existence check is WP2's run-time column
   probe).
5. Explicitly leave `project_config.py:28-52`
   (`ProjectTablesConfig.reject_overrides`) untouched; add one docstring
   sentence: "Per-experiment source reference does not need this — see
   `AssignmentConfig.copy`; unblocking table-name overrides generally remains
   future work."
6. Document `assignment.copy.*` in `declarative-config.md` next to
   `added_filters` (§2, line ~43 today).

**Tests:**
- `tests/config/test_experiment_config.py`: default `assignment.copy.enabled is
  False`; `batch_interval`/`maturity_delay` accept both int seconds and
  `Interval` strings (`'1d'`); bad `Interval` grammar raises `ValueError` at
  parse time.
- `tests/config/test_project_config.py`: existing `reject_overrides` tests pass
  **unchanged** (a regression guard proving this WP did not loosen that gate).

**Risks / hotspots:**
- Bikeshedding the field name (`assignment.copy` vs `assignment.cohort_copy`)
  — pick one and thread it consistently through every later WP; carried as an
  open question below.
- If a future milestone does want per-project internal-table renames, this
  WP's `project_config.py` docstring note must be revisited so it doesn't read
  as a permanent decision.

**Session estimate:** 1 session.

---

### WP2 — New `exposure_source` module: render once, ONE validation query, in-memory snapshot

**Goal:** replace today's full-materialize-in-Python loop
(`exposure_loader.py:74-107`, fed by `manager.execute_query(rendered)` at line
56) with a pushdown validation query that returns at most one row per
`(unit_id, variant)` — `SELECT unit_id, variant, MIN(exposure_ts) AS
exposure_ts[, MIN(stratum) AS stratum] FROM (<rendered assignment sql>) t GROUP
BY unit_id, variant` — then run the **same** cross-variant/duplicate-row Python
checks over that much smaller result. `MIN()` is portable across
ClickHouse/Postgres/MySQL (sidestepping the dialect-specific argMin/DISTINCT-ON
problem); stratum ties are broken arbitrarily, documented, matching today's
duplicate-row warning path (subject to the §0.5(b) parity fixture).

**Files:** `abkit/loaders/exposure_source.py` (new), `abkit/loaders/exposure_loader.py`,
`tests/loaders/test_exposure_source.py` (new), `tests/loaders/test_loaders.py`.

**Steps:**
1. New module `abkit/loaders/exposure_source.py`. Dataclass `ExposureSnapshot`:
   `counts: dict[str, int]` (SRM observed counts, the same contract as today's
   `load_exposures` return), `by_unit: dict[str, tuple[str, datetime, Any]]`
   (unit → `(variant, exposure_ts, stratum)`, the exact shape of today's `seen`
   dict at `exposure_loader.py:75`), `has_stratum: bool`.
2. `probe_has_stratum(manager, rendered_sql) -> bool`: executes `SELECT * FROM
   (<rendered_sql>) t LIMIT 1`, returns `'stratum' in rows[0]` if `rows`
   non-empty else `False` (mirrors `exposure_loader.py:72`'s `has_stratum =
   'stratum' in rows[0]`, but fetches ONE row instead of the whole result set).
3. `validate_and_snapshot(manager, experiment, rendered_sql, has_stratum) ->
   ExposureSnapshot`: builds the `GROUP BY` query (stratum column only in the
   `SELECT` list when `has_stratum`), executes it, then runs the **identical**
   cross-variant hard-error / duplicate-row warning loop currently at
   `exposure_loader.py:74-107` (same `ExposureLoadError` wording; same
   `warnings.warn(...)` wording — the §0.5(d) compatibility gate) but iterating
   over the aggregated rows instead of raw rows. Raises
   `ExposureLoadError('... returned no rows ...')` on zero rows (parity with
   `exposure_loader.py:58-62`).
4. Refactor `exposure_loader.py`'s `load_exposures()` into a thin orchestrator:
   render once, `probe_has_stratum`, `validate_and_snapshot` for `counts`; when
   `experiment.assignment.copy.enabled` call the WP5 incremental-copy engine
   (the WP2 `rendered_sql` is **not** reused as-is — copy re-renders per batch
   with different `added_filters`, see WP5) and persist; else do nothing
   further. Keep the function's public signature/return type (`dict[str,
   int]`) unchanged so `driver.py`'s SRM-gate call site
   (`abkit/pipeline/driver.py:207-208`) needs no change.
5. Delete the now-dead raw-row Python loop from `exposure_loader.py` (lines
   74-107) once `exposure_source.py` owns it; keep the docstring contract
   section (lines 39-52) verbatim — the *contract* is unchanged, only the
   mechanism.

**Tests:**
- `tests/loaders/test_exposure_source.py` (new): cross-variant conflict raises
  the same message; duplicate-row-same-variant warns and keeps the earliest
  `exposure_ts` (verify `MIN()` picks the same winner the old Python `if
  exposure_ts < prev_ts` loop picked); `has_stratum` True/False branches; empty
  result raises.
- `tests/loaders/test_loaders.py`: existing `load_exposures` error/warning
  assertions pass **unchanged** (byte-identical error text) against the
  refactored implementation — the regression gate proving the pushdown query
  is behavior-preserving.
- A parametrized property test on a synthetic `FakeDatabaseManager` cohort with
  duplicate rows: `validate_and_snapshot(...).counts` == the OLD
  `load_exposures(...)` counts on the same input, for at least 3 fixtures
  (clean cohort, same-variant duplicate, cross-variant conflict) — **plus the
  §0.5(b) fixture**: duplicate rows for the same unit differing in **both**
  timestamp and stratum, asserting either pinned parity or an explicitly
  documented divergence.

**Risks / hotspots:**
- `MIN(stratum)` is an arbitrary tie-break when duplicate rows for the same
  unit carry different stratum values (today's Python loop also does not
  update stratum on a later-but-earlier-ts duplicate in a fully consistent
  way — verify exact parity in the property test rather than assuming it; this
  is the §0.5(b) review fixture).
- The `LIMIT 1` column probe assumes the assignment SQL is side-effect-free and
  cheap for 1 row — true for a `SELECT`, but an expensive CTE with no `LIMIT`
  pushdown may still materialize fully on some backends; acceptable, the same
  class of cost the direct-join macro pays anyway (WP3).

**Session estimate:** 1 session.

---

### WP3 — Jinja macro + `query_template`: direct-join cohort source builtin

**Goal:** introduce one unified builtin `ab_cohort_source`, built in Python
(`query_template.py`), whose value is **either** the persisted-table+`FINAL`
fragment (today's `ab_exposures_table`, unchanged when `copy.enabled`) **or** a
deduping subquery wrapping the rendered assignment SQL (new, default). The
macro body (`abkit_assignment.jinja:41-58`) changes by exactly one line —
`FROM {{ ab_exposures_table }}{% if ab_dialect == 'clickhouse' %} FINAL{% endif
%}` becomes `FROM {{ ab_cohort_source }}` — so the correctness-critical
join/window/dedup logic stays in the one place the module's own header already
promises (jinja file header, lines 1-9).

**Files:** `abkit/loaders/templates/abkit_assignment.jinja`,
`abkit/loaders/query_template.py`, `abkit/compute/recompute_backend.py`,
`tests/loaders/test_query_template.py`.

**Steps:**
1. `query_template.py` `build_builtins()` (lines 56-98): add params
   `direct_source_sql: str | None = None`, `has_stratum: bool = True`. Move the
   `FINAL`-suffix logic that currently lives in the macro (jinja line 48) into
   Python: when `direct_source_sql is None` (copy mode), `ab_cohort_source =
   f"{internal_database}.{exposures_table}"` (+ `' FINAL'` on ClickHouse); when
   `direct_source_sql` is given (default), `ab_cohort_source = f"(SELECT
   unit_id, variant, MIN(exposure_ts) AS exposure_ts{', MIN(stratum) AS
   stratum' if has_stratum else ', NULL AS stratum'} FROM ({direct_source_sql})
   _abk_raw GROUP BY unit_id, variant)"`. Keep the `ab_exposures_table` builtin
   present (unused by the macro post-change, kept for any external
   consumer/back-compat).
2. `abkit_assignment.jinja`: change line 48's `FROM` clause to `FROM {{
   ab_cohort_source }}` (drop the inline `FINAL` conditional — now baked into
   the builtin). Update the module docstring (lines 1-33) to describe both
   source modes and that dedup now happens in `ab_cohort_source` itself (direct
   mode) rather than relying solely on the persisted `ReplacingMergeTree`.
3. `recompute_backend.py` `RecomputeBackend.__init__` (lines 47-56): replace
   the single `exposures_table: str = '_ab_exposures'` param with
   `direct_source_sql: str | None = None`, `has_stratum: bool = True` (keep
   `exposures_table`, used only when `direct_source_sql is None`), threaded
   into `_builtins()` (lines 58-76) → `build_builtins(...,
   direct_source_sql=..., has_stratum=...)`.
4. Verify the CUPED covariate render path (`load_cutoff`, lines 100-124,
   `apply_exposure_filter=False`) automatically gets the same
   `ab_cohort_source` since it goes through the same `_builtins()` — add an
   explicit test asserting this rather than assuming it.

**Tests:**
- `tests/loaders/test_query_template.py`: extend the existing builtins tests
  (lines ~26-58, ~101-116) with a `direct_source_sql` case asserting
  `ab_cohort_source` is the `GROUP BY`-wrapped subquery containing the exact
  rendered SQL text verbatim, and a `has_stratum=False` case asserting `NULL AS
  stratum` appears instead of a `stratum` column reference (so
  `StrictUndefined` never trips on a metric referencing `ab.stratum_col()`
  against a stratum-less source).
- A rendered-SQL snapshot test: render a metric importing
  `abkit_assignment.jinja` under both modes and diff only the
  `ab_cohort_source` substitution — everything else (window predicates,
  `variant_col`, `stratum_col`) must be byte-identical outside that
  substitution.
- A golden/byte-parity check (ties to the milestone exit gate): for a
  **well-formed** (no duplicate/cross-variant) cohort, direct-mode metric
  output == copy-mode metric output, exactly.

**Risks / hotspots:**
- Every metric query now executes an extra `GROUP BY` over the cohort-sized
  result on every render in direct mode — acceptable for a flat table (the
  audit's own argument) but a genuine added cost for a heavy multi-join
  assignment query, repeated per metric × cutoff; exactly why the opt-in copy
  flag exists — document prominently (WP7), never silently absorb the cost.
- `ab_exposures_table` staying present-but-unused could bit-rot; add a code
  comment pointing at `ab_cohort_source` as canonical so a future edit doesn't
  silently diverge the two.

**Session estimate:** 1 session.

---

### WP4 — Wire every `RecomputeBackend`/exposures-reader call site onto the source-mode switch

**Goal:** centralize the copy-vs-direct branch in ONE factory so the 5+ call
sites that build a `RecomputeBackend` or read exposures counts do not each
re-implement the if/else. Blast radius confirmed by grep: `driver.py:268`,
`validate.py:166`, `explore.py:136`, `tuning/server.py:594` and `:665` all call
`RecomputeBackend(manager, experiment, ...)` directly today; `plan.py:211-225`
and `reporting/builder.py:388-390` read persisted-table counts/arrival-rate
directly. **This is the factory §0.5(e) names as the binding contract M9 must
build on** — nothing downstream may hand-roll cohort SQL again.

**Files:** `abkit/loaders/exposure_source.py`, `abkit/pipeline/driver.py`,
`abkit/cli/commands/validate.py`, `abkit/cli/commands/explore.py`,
`abkit/tuning/server.py`, `abkit/cli/commands/plan.py`,
`abkit/reporting/builder.py`, `tests/cli/test_plan_command.py`,
`tests/reporting/test_builder.py`.

**Steps:**
1. In `exposure_source.py` add `render_assignment_sql(manager, experiment,
   project_root, grid, dialect) -> str` (thin wrapper: `build_builtins(...,
   window=RenderWindow(start_ts=grid.start_ts, end_ts=grid.horizon_ts), ...)` +
   `QueryTemplate().render(experiment.assignment.get_query_text(project_root),
   builtins)` — mirrors `driver.py:194-207` exactly).
2. Add `build_cohort_backend(manager, experiment, project_root, grid, dialect)
   -> tuple[RecomputeBackend, ExposureSnapshot]`: renders once, probes
   `has_stratum`, calls `validate_and_snapshot` (WP2); if
   `experiment.assignment.copy.enabled` returns `RecomputeBackend(manager,
   experiment, exposures_table=TABLE_EXPOSURES)` (today's path, unchanged) else
   `RecomputeBackend(manager, experiment, direct_source_sql=rendered_sql,
   has_stratum=has_stratum)`.
3. `driver.py` (lines 194-268): replace the manual
   `build_builtins`+`load_exposures`+`RecomputeBackend(...,
   exposures_table=TABLE_EXPOSURES)` sequence with `build_cohort_backend(...)`;
   keep `observed_counts = snapshot.counts` feeding the same SRM-gate code at
   lines 207-256 unchanged.
4. For the sub-day SRM stream (driver.py lines 224-233, today calling
   `tables.get_exposure_count_stream`): when `not
   experiment.assignment.copy.enabled`, derive the boundary counts from
   `snapshot.by_unit` in-process using the **same** bisect logic as
   `_exposures.py`'s `get_exposure_count_stream` (lines 99-141) — extract that
   bucketing math into a shared pure function
   `abkit/core/exposure_counting.py: count_stream(per_variant_sorted_ts,
   boundaries, variants)` used by **both** `_ExposuresMixin.get_exposure_count_stream`
   (copy mode) and the driver's direct-mode path, so there is exactly one
   bisect implementation, never two that can drift.
5. `validate.py:166`, `explore.py:136`, `tuning/server.py:594` and `:665`:
   replace bare `RecomputeBackend(manager, experiment)` with
   `build_cohort_backend(manager, experiment, project_root, grid,
   dialect_of(manager))[0]` (each site already computes or can compute a
   `grid` — `validate.py:167-172` and `explore.py` already call
   `generate_grid`; verify whether `tuning/server.py`'s session caches one and
   reuse it, per the risk below).
6. `plan.py` `_control_arrival_rate` (lines 190-222): replace
   `tables.exposures_table_exists()` / `tables.get_arrival_rate(...)` /
   `tables.count_exposures(...)` with a snapshot obtained via
   `build_cohort_backend(...)[1]` (or a lighter `get_snapshot_only(...)` that
   skips backend construction when only counts/arrival-rate are needed) when
   `not copy.enabled`; keep the existing persisted-table path when
   `copy.enabled`. Update the skip-reason strings (e.g. "no `_ab_exposures`
   yet") to mode-aware wording — same control flow, different message.
7. `reporting/builder.py` `build_report_payload` (lines 387-390): same swap —
   `tables.exposures_table_exists()`/`tables.get_exposure_counts(...)` becomes
   the snapshot-derived counts in direct mode. Confirm/extend
   `build_report_payload`'s signature to accept `project_root`/`grid`/`dialect`
   as needed (its 3 callers — `run.py:78`, `explore.py:147`, `validate.py:246`
   — already have a manager/experiment/project_root in scope).

**Tests:**
- `tests/e2e/test_first_run.py` stays green with the **default** (no-copy)
  config — no `_ab_exposures` table needs to exist for the run to produce
  identical `_ab_results` numbers.
- `tests/cli/test_plan_command.py`: update stubs from
  `exposures_table_exists`/`get_arrival_rate`/`count_exposures` to the new
  snapshot path for the default (no-copy) fixture, and add a parallel case
  with `copy.enabled: true` asserting the old stubbed path is still reachable.
- `tests/reporting/test_builder.py`: same dual-path coverage for the SRM
  chip's observed counts.
- A dedicated test asserting `abk validate` / `abk explore` / tuning `RELOAD`
  produce **identical** scoring/series output whether `copy.enabled` is true
  or false, on a fixture with a clean (no dup/conflict) cohort — the
  cross-command byte-parity gate.

**Risks / hotspots:**
- This is the widest blast-radius WP — 6+ call sites, some (`tuning/server.py`)
  inside a long-lived session object whose `grid`/`dialect` caching needs
  checking rather than assuming; budget the full session for wiring + the
  dual-path tests, do not compress into WP3.
- `abk plan`/BI/`abk explore` running standalone (no LOAD stage just executed)
  now re-execute the assignment SQL at invocation time in default mode — a
  cost + freshness change from today's "read the last run's frozen snapshot"
  behavior; call out prominently in docs (WP7) as an **accepted tradeoff**, not
  a bug.
- If `tuning/server.py`'s session object already caches `grid`/`dialect` per
  session, prefer reusing that cache over recomputing per RELOAD call (a perf
  regression otherwise on repeated tuning iterations).

**Session estimate:** 2 sessions.

---

### WP5 — Incremental copy engine (opt-in), ported from detectkit's watermark/closed-interval/batch pattern

**Goal:** when `assignment.copy.enabled`, replace the current
delete-then-reinsert-everything (`replace_exposures`,
`abkit/database/internal_tables/_exposures.py:25-69`, called unconditionally
from `exposure_loader.py:116`) with an append-only, watermark-driven,
closed-interval, batched load — the exact discipline in detectkit's
`_load_step.py:43-148` and `metric_loader.py:344-399`. The whole-cohort
validation query (WP2) still runs every run regardless of copy mode, so
cross-variant conflicts are still caught even though the **persisted** copy is
only appended to.

**Files:** `abkit/database/internal_tables/_exposures.py`,
`abkit/loaders/exposure_copy.py` (new), `abkit/loaders/exposure_loader.py`,
`tests/database/test_internal_tables.py`, `tests/loaders/test_exposure_copy.py`
(new).

**Steps:**
1. `_exposures.py`: add `get_last_exposure_timestamp(self, experiment: str) ->
   datetime | None` (`MAX(exposure_ts)`, the mirror-image of the existing
   `get_first_exposure_ts` `MIN` at lines 189-199; same `full_table_name`/
   `_normalize_max_timestamp` pattern).
2. Add `insert_exposures_incremental(self, experiment: str, data: dict[str,
   np.ndarray]) -> int`: chunked `insert_batch(full_table_name, chunk,
   conflict_strategy='ignore')` exactly like `replace_exposures`'s insert loop
   (lines 55-68) but **without** the preceding `delete_rows` call (lines
   44-46) — append-only. Stamp `loaded_at`/`experiment` the same way.
3. New module `abkit/loaders/exposure_copy.py`, function
   `copy_exposures_incremental(manager, tables, experiment, project_root,
   grid, dialect, now, template=None) -> int`:
   1. `watermark = tables.get_last_exposure_timestamp(experiment.name)`;
      `actual_from = watermark or tz_midnight_utc(experiment.start_date,
      ZoneInfo(experiment.timezone))` (first-run backfill from experiment
      start, mirroring detectkit's `config.loading_start_time` fallback at
      `_load_step.py:52-55`).
   2. `batch_interval = Interval(experiment.assignment.copy.batch_interval)`;
      `actual_to = now - Interval(experiment.assignment.copy.maturity_delay).seconds`,
      then snap back to the last **closed** interval boundary from
      `actual_from` using the exact arithmetic at `detectkit/_load_step.py:106-111`
      (`total_points = int((actual_to-actual_from).total_seconds() //
      batch_interval.seconds)`; bail early ("nothing to load yet") if `< 1`;
      `actual_to = actual_from + total_points*batch_interval.seconds`).
   3. Loop in chunks of `batch_intervals_per_round_trip * batch_interval.seconds`
      (mirrors `_load_step.py:120-145`): for each `[batch_from, batch_to)`,
      render the assignment SQL with `added_filters =
      f"{experiment.assignment.added_filters} AND {update_column} >=
      '{batch_from:%Y-%m-%d %H:%M:%S}' AND {update_column} <
      '{batch_to:%Y-%m-%d %H:%M:%S}'"` (reuses the **existing**
      `ab_added_filters` builtin injection point at `query_template.py:81` /
      the jinja file — no new jinja surface), execute, dedupe THIS batch's rows
      only (small, bounded by one batch — same seen-dict loop shape as WP2, but
      does **not** hard-fail on cross-variant here since the run-level WP2
      check already gated that moments earlier against the full current
      source), and call `tables.insert_exposures_incremental(...)`.
4. `exposure_loader.py`: when `experiment.assignment.copy.enabled`,
   `load_exposures()` calls `copy_exposures_incremental(...)` instead of
   `tables.replace_exposures(...)`; `replace_exposures` stays in the codebase
   as the explicit full-resync path (see risk below).
5. Add a CLI escape hatch: extend `abk run`'s existing `--full-refresh`
   semantics (or add `abk run --resync-cohort`) to call the old
   `replace_exposures` full delete+reinsert for disaster recovery / schema
   migration of the persisted copy — confirm exact flag name with the
   maintainer (open question below).

**Tests:**
- `tests/database/test_internal_tables.py`: `get_last_exposure_timestamp`
  returns `None` on empty cohort, `MAX` otherwise; `insert_exposures_incremental`
  never calls `delete_rows` (spy/mock assertion) and is idempotent under re-run
  (`conflict_strategy='ignore'` — inserting the same batch twice does not
  double-count on `FINAL`-deduped read).
- `tests/loaders/test_exposure_copy.py` (new, mirrors detectkit's own
  load-step test shapes): watermark resume picks up exactly the units with
  `update_column >= watermark`; the **current open interval** is never pulled
  (assert a unit whose `exposure_ts` falls in the still-open bucket is absent
  after copy, present after the interval closes); multi-batch loop matches
  single-batch loop's total row count for the same overall window
  (batch-boundary-invariance test, direct port of detectkit's donor test
  intent).
- An end-to-end "second run only appends" test: run `abk run` twice against a
  seed dataset where the second run's source has strictly MORE rows (new units
  with later `exposure_ts`); assert `_ab_exposures` row count grows by exactly
  the delta and `replace_exposures`/`delete_rows` is never called on the
  second run.

**Risks / hotspots:**
- **Late-arriving / out-of-order rows** — a KNOWN LIMITATION carried verbatim
  from detectkit's own watermark model: if a unit's `exposure_ts` (the default
  `update_column`) is EARLIER than the current watermark but the row only
  appears in the source LATER (e.g. a backfilled or corrected assignment), the
  `update_column >= watermark` filter silently drops it forever from the
  persisted copy. This is the **opposite asymmetry** from the no-copy default
  (which always re-reads the full live source, so it would NOT miss such a
  row). Must be documented prominently (WP7) as a reason to prefer the
  no-copy default unless the source is guaranteed append-only-and-monotonic on
  `update_column`.
- A genuinely conflicting unit (assigned to a different variant AFTER already
  being copied) is caught by the run-level whole-cohort validation query (WP2,
  hard error) BEFORE the copy step runs, so the copy step itself does not need
  to re-detect it — but if some future caller invokes
  `copy_exposures_incremental` without first running the WP2 validation gate,
  the `(experiment, unit_id)` PK's LWW upsert would silently resolve the
  conflict by version/recency instead of raising. Document this ordering
  dependency explicitly (the copy engine is **not** safe to call standalone).
- Choosing `--full-refresh`/`--resync-cohort` flag semantics needs a
  maintainer decision (open question below) — do not invent a new CLI flag
  name unilaterally without confirming it doesn't collide with the existing
  per-comparison `--full-refresh` results-window flag already in `driver.py`.

**Session estimate:** 2 sessions.

---

### WP6 — Tests: full unit-test migration + e2e no-copy/copy-enabled parity legs

**Goal:** update every test that pins today's full-reload/persisted-copy
behavior, and add the two e2e legs the exit gate needs: the existing first-run
test stays green under the NEW default (no-copy), and a new copy-enabled leg
proves byte-parity plus append-only persistence. **Sequenced deliberately
last** among the code WPs — it is the integration pass that surfaces
interaction bugs between WP2–WP5 that unit tests in isolation missed.

**Files:** `tests/e2e/test_first_run.py`,
`tests/e2e/test_first_run_copy_enabled.py` (new), `tests/loaders/test_loaders.py`,
`tests/loaders/test_query_template.py`, `tests/database/test_internal_tables.py`,
`tests/database/test_sql_managers.py`, `tests/cli/test_plan_command.py`,
`tests/reporting/test_builder.py`.

**Steps:**
1. Audit `tests/e2e/test_first_run.py` for any direct assertion against
   `_ab_exposures` row contents (vs. only asserting final `_ab_results`/report
   numbers). If it reads `_ab_exposures` directly, move that specific
   assertion into the new copy-enabled leg and keep the base test asserting
   only end-to-end NUMBERS (the byte-parity contract for the new default).
2. New `tests/e2e/test_first_run_copy_enabled.py`: same seed dataset +
   assignment SQL as `test_first_run.py`, with `assignment.copy.enabled: true`
   in the experiment YAML fixture; assert (a) `_ab_results` numbers are
   byte-identical (or rel-1e-9, per the project's numeric-parity convention)
   to the default no-copy run's `_ab_results`; (b) a second `abk run`
   invocation only inserts the DELTA of newly-seeded rows into `_ab_exposures`
   (spy on the manager to assert `delete_rows`/`replace_exposures` is never
   invoked on the second run, only `insert_exposures_incremental`).
3. `tests/database/test_sql_managers.py:264-266` (pins the `DELETE FROM
   abk._ab_exposures WHERE ...` statement shape from `replace_exposures`):
   keep this test but re-scope its docstring/comment to state it pins the
   RESYNC/full-refresh path specifically, not the default incremental path.
4. `tests/loaders/test_query_template.py`, `tests/loaders/test_loaders.py`,
   `tests/database/test_internal_tables.py`: apply the changes already
   itemized in WP2/WP3/WP5's own test lists (cross-reference, do not duplicate
   work already scheduled there — this WP runs the full suite together to
   catch interaction bugs).
5. `tests/cli/test_plan_command.py`, `tests/reporting/test_builder.py`: finish
   the dual-path (copy on/off) stub updates started in WP4.

**Tests:**
- Full `pytest` run green, including the new e2e legs, before this WP is
  considered done.
- A CHANGELOG-adjacent numeric-parity check script (if the project has one,
  e.g. an existing golden-diff harness referenced in `statistics-changes.md
  §1.1`) run against both modes' `_ab_results` output.

**Risks / hotspots:**
- This WP will surface integration bugs from WP2–WP5 that unit tests in
  isolation missed (e.g. `has_stratum` mismatches between the macro's
  `ab_cohort_source` and the snapshot's column probe); budget slack for fixes
  discovered here, do not treat it as pure test-writing.

**Session estimate:** 2 sessions.

---

### WP7 — Docs three-way sync + init scaffold + CHANGELOG

**Goal:** update `docs/`, `.claude/rules/` (mirrored under
`abkit/cli/assets/claude/`), and the init scaffold so the new default and the
opt-in copy flag are documented consistently everywhere the audit found
`_ab_exposures` referenced, per the project's release-time three-way sync
invariant (the M6 discipline — see [m6-implementation-plan.md](m6-implementation-plan.md)).

**Files:** `docs/reference/internal-tables.md`, `docs/guides/experiments.md`,
`docs/guides/plan.md`, `docs/guides/validate.md`, `docs/guides/databases.md`,
`docs/specs/architecture.md`, `docs/specs/cumulative-intervals.md`,
`docs/specs/declarative-config.md`, `abkit/cli/assets/claude/rules/overview.md`,
`abkit/cli/assets/claude/rules/experiments.md`,
`abkit/cli/assets/claude/rules/metrics.md`, `abkit/cli/assets/claude/rules/plan.md`,
`abkit/cli/assets/claude/rules/validate.md`,
`abkit/cli/assets/claude/skills/abk-setup-project/SKILL.md`,
`abkit/cli/assets/claude/skills/abk-plan/SKILL.md`,
`abkit/cli/assets/claude/skills/abk-validate/SKILL.md`,
`abkit/cli/assets/claude/skills/abk-new-metric/SKILL.md`,
`abkit/cli/commands/init.py`, `CHANGELOG.md`.

**Steps:**
1. `docs/reference/internal-tables.md`: mark `_ab_exposures` as OPTIONAL —
   populated only when `assignment.copy.enabled: true`; document its new
   append-only/incremental write pattern (watermark on `update_column`,
   closed-interval snap, batch loop) replacing the delete+reinsert
   description.
2. `docs/guides/experiments.md` (currently documents `added_filters` at line
   ~69/167): add the `assignment.copy` block reference, when to use it (a
   heavy multi-join assignment source; a source that mutates during the run
   window), and the KNOWN LIMITATION callout from WP5's risk (late-arriving
   backfilled rows are silently missed by the incremental copy's watermark —
   use the no-copy default or a manual resync for such sources).
3. `docs/specs/architecture.md` / `docs/specs/cumulative-intervals.md`: update
   the "cohort persisted once per run" language to describe the new default
   explicitly (persisted-once-per-run becomes copy-mode-only; default is
   direct-join-every-execution).
4. `docs/guides/plan.md`: note that `abk plan`'s arrival-rate derivation now
   re-executes the assignment source at invocation time by default (the cost
   caveat from WP4).
5. `docs/guides/databases.md`: if it documents the `_ab_exposures` schema for
   BI authors, add the existence caveat.
6. `abkit/cli/commands/init.py`: update `ASSIGNMENT_SQL`'s comment (line ~239,
   currently "abkit persists this ONCE per run into `_ab_exposures`") to
   describe the new default; add a commented-out `copy:` block example in the
   generated experiment YAML showing how to opt in.
7. Mirror every docs/ wording change into the matching
   `abkit/cli/assets/claude/rules/*.md` and `skills/*/SKILL.md` file (the
   three-way sync invariant) — do this as the LAST step so docs/ is the single
   source of truth being copied, not re-derived independently.
8. `CHANGELOG.md` `[Unreleased]`: new entry describing the default flip + the
   opt-in copy flag, explicitly stating "no `ALGORITHM_VERSION` bump — zero
   statistical numbers changed, this is a data-provenance/performance change"
   (mirrors the 0.1.2 entry's own such disclaimer).

**Tests:**
- A grep-based CI/docs-sync check (if one exists per the three-way-sync
  convention) confirms docs/, `.claude/rules/`, and `abkit/cli/assets/claude/`
  agree — run it manually if no automated gate exists yet.
- Manual re-read of `docs/reference/internal-tables.md` and
  `docs/guides/experiments.md` against the actual shipped config schema (WP1)
  for field-name drift.

**Risks / hotspots:**
- Docs drift is the most likely SILENT failure mode of this whole milestone (a
  stale doc describing the old delete+reinsert behavior misleads users into
  distrusting a correct new incremental copy) — treat the mirrored rules/skills
  files as mandatory, not optional polish.

**Session estimate:** 1 session.

---

## 2. Dependency graph / parallelism

```
WP1 ──────────────────────────────────────┐
                                           ▼
WP2 ──▶ WP3 ──▶ WP4 ───────────────────▶ WP6 ──▶ WP7
  └───────┴───────────────▶ WP5 ─────────▶┘
```

- **WP1 blocks WP5** (the copy config fields must exist before the incremental
  engine can read them).
- **WP2 blocks WP3** (the snapshot/validation-query module's
  `has_stratum`/rendered-SQL contract is what the macro builtin wraps).
- **WP3 blocks WP4** (call sites need the finished `RecomputeBackend`
  constructor signature before they can be wired).
- **WP2+WP3 block WP5** (the copy engine reuses the same rendered-SQL/
  `added_filters` mechanism and must not duplicate the validation loop).
- **WP1–WP5 block WP6** (the integration test pass needs all code paths
  finished).
- **WP6 should substantially complete before WP7's docs are finalized** (docs
  describe the tested, not the intended, behavior).
- **No dependency on other milestones' work packages** — this milestone
  touches `abkit/loaders`, `abkit/database/internal_tables`, `abkit/pipeline`,
  `abkit/compute`, `abkit/cli/commands/{plan,validate,explore}`,
  `abkit/tuning`, `abkit/reporting`; coordinate with any concurrently-planned
  milestone touching `RecomputeBackend`'s constructor or the `_exposures.py`
  mixin to avoid a merge collision on the same call sites.

**Inter-milestone contract this milestone produces (binding, outgoing):**
M8's `build_cohort_backend` factory (WP4) and `ab_cohort_source` builtin (WP3)
are the **only** sanctioned way to render cohort SQL from this point forward.
Per the approved track plan and [ROADMAP.md](../../ROADMAP.md) "Inter-milestone
contracts", **M9's STATE writer and tail-scan MUST build their cohort SQL
exclusively through this factory** — a plan-review-flagged blocker: a
hand-rolled render in M9 would silently join a non-existent `_ab_exposures`
under the no-copy default and produce silent zeros, not an error. M8's exit
gate does not itself test M9 code (which doesn't exist yet), but this contract
is stated here in full so M9's design session inherits it verbatim rather than
rediscovering it.

**Explicitly not a collision:** the Grafana reference dashboard (§0.3) and
M10's schema-break work (`_ab_results` date columns, `_ab_experiments`
`Date`→`DateTime64`) are unrelated to this milestone's blast radius — no
coordination needed there.

---

## 3. Exit gate

Two adversarial review rounds focused specifically on:

1. **Semantic parity** — does the direct-join `ab_cohort_source` subquery
   (WP3) dedupe IDENTICALLY to the persisted-copy path for every render
   touchpoint, including the CUPED pre-period covariate render
   (`apply_exposure_filter=false`) and the sub-day SRM stream's in-memory
   bisect path (WP4)?
2. **The incremental-copy watermark's late-arrival/out-of-order-row
   limitation** (WP5 risk) — confirm it is prominently documented and not
   silently masked.

Then the e2e battery:

- `abk init && <load seed> && abk run --select example` green under the new
  **no-copy default** (`tests/e2e/test_first_run.py`).
- The same seed with `assignment.copy.enabled: true` green with `_ab_results`
  numbers byte-identical (or rel-1e-9) to the no-copy run
  (`tests/e2e/test_first_run_copy_enabled.py`, the milestone's core
  numeric-parity gate per the "never change a number silently" invariant).
- A second `abk run` in copy-enabled mode proven append-only (no
  `delete_rows`/`replace_exposures` call, row count grows by exactly the
  new-row delta).
- `abk plan`, `abk validate`, `abk explore`, and the BI/reporting SRM chip all
  produce identical output whether copy is enabled or not, on a clean fixture
  cohort.
- Full `pytest` suite green.
- Docs three-way sync (`docs/` + `.claude/rules/` + `abkit/cli/assets/claude/`)
  confirmed consistent.
- `CHANGELOG.md` entry present with the explicit "no `ALGORITHM_VERSION` bump"
  disclaimer.

Per the track-wide discipline (§0.2): `grep ALGORITHM_VERSION` across the diff
stays empty; parity gates are exact on integer counts (SRM observed counts,
row counts, arrival-rate numerators) and rel-1e-9 on continuous values.

---

## 4. Open questions / "before start" decisions

The approved track plan's recommendations are noted inline; each still needs
explicit sign-off before or during the WP that depends on it.

1. **Should `assignment.copy` be the final field name**, or does the
   maintainer prefer `assignment.cohort_copy` / a top-level `loading:` block
   separate from `assignment:`? (WP1) — **recommendation:** pick one name at
   WP1 and thread it consistently through every later WP; this doc uses
   `assignment.copy` throughout as the working name.
   **SETTLED at WP1: `assignment.cohort_copy`.** Not bikeshedding — a
   technical forcing: a pydantic field named `copy` shadows the
   deprecated-but-present `BaseModel.copy` and pydantic v2 emits a
   `UserWarning` at import time (verified against pydantic 2.x in-repo).
   Every later WP reads `experiment.assignment.cohort_copy.*` where this doc's
   WP2–WP7 prose says `assignment.copy.*`; the Python model is
   `CohortCopyConfig` (exported from `abkit.config`).
2. **Should a CLI escape hatch (e.g. `abk run --resync-cohort`) be added** to
   force the OLD full delete+reinsert for disaster recovery of a
   previously-copied cohort, and does its name collide with the EXISTING
   per-comparison `--full-refresh` flag in `driver.py`? (WP5) — the flag name
   must be confirmed before WP5 ships; do not invent one unilaterally.
   **SETTLED by the maintainer at WP4 (2026-07-20): `abk run
   --resync-cohort`** — a dedicated flag; `--full-refresh` keeps its existing
   results-window semantics untouched, the two never overload each other.
3. **Is the late-arriving/out-of-order-row limitation of the incremental
   copy acceptable as a documented limitation** matching detectkit's own
   donor behavior, or does it need a configurable overlap/grace-window on top
   of the watermark before this ships? (WP5) — the track plan's recommendation
   is **doc-only** (yes, ship the documented limitation as-is); needs
   maintainer sign-off, not a default assumption.
   **SETTLED by the maintainer at WP4 (2026-07-20): doc-only.** WP5 ships the
   donor watermark behavior as-is with the prominent WP7 documentation
   callout ("a mutating/backfilling source should use the no-copy default or
   a `--resync-cohort` recovery"); no overlap/grace-window knob is added.
4. **For `abk plan`/`abk explore`/BI re-executing the assignment SQL fresh on
   every invocation in no-copy default (WP4)** — is this an acceptable cost
   tradeoff for all users, or should these commands warn/refuse when the
   assignment source looks expensive (no static way to detect this — would
   need a manual opt-out documented in the guides instead)? Does the
   maintainer want a runtime WARNING when a live source's row count changes
   measurably between the run's validation-query pass and later metric
   executions within the SAME run (the within-run consistency drift risk), or
   is a docs-only callout ("enable copy for a mutating source") sufficient?
   The current design recommends **docs-only** to avoid adding a round-trip
   that partially defeats the no-copy performance goal — needs sign-off.
   **SETTLED by the maintainer at WP4 (2026-07-20): docs-only**, as
   implemented — no runtime drift warning; the WP7 guides carry the "enable
   `cohort_copy` for a heavy or mutating source" caveat.

---

## 5. Dependencies (summary)

See §2 for the full graph. In one line: **WP1 → WP5**; **WP2 → WP3 → WP4**;
**WP2+WP3 → WP5**; **WP1…WP5 → WP6 → WP7**. This milestone has no incoming
dependency from any other milestone's work packages, but produces one binding
**outgoing** dependency: M9's STATE/tail-scan work is contractually required to
build exclusively on this milestone's `build_cohort_backend`/`ab_cohort_source`
factory (§0.5(e), restated in §2) — the single highest-value fact for any
future reader of this document to carry forward.
