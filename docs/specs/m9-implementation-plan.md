# M9 Implementation Plan — additive compute engine + CUPED Tier-E

> **Status: as-designed contract for M9 (track approved 2026-07-18), NOT yet
> implemented.** Targets release `0.4.0`. This is the contract the M9
> implementation sessions execute WP by WP; it becomes the implementation
> record (as-built table + adversarial-review log) at the exit gate, the
> [m4](m4-implementation-plan.md)/[m5](m5-implementation-plan.md)/
> [m6](m6-implementation-plan.md)-implementation-plan.md pattern. Nothing in
> this document describes code that exists yet — every WP is written in
> contract/future tense ("WP2 adds…", "the gate asserts…"); do not read it as
> an as-built record.
>
> Governing specs: [cumulative-intervals.md §4–6](cumulative-intervals.md)
> (the STATE/incremental contract this milestone finally wires),
> [architecture.md](architecture.md) (module map, invariants),
> [statistics-changes.md](statistics-changes.md) (why this milestone never
> bumps `ALGORITHM_VERSION`), [ROADMAP.md](../../ROADMAP.md) M9. Sibling
> milestone docs: [m8-implementation-plan.md](m8-implementation-plan.md) (the
> `build_cohort_backend`/`ab_cohort_source` factory this milestone depends
> on — a hard blocker, see §0.2), [m10-implementation-plan.md](m10-implementation-plan.md)
> (the schema-break milestone that follows this one's additive-only schema
> change). Source plan: `~/.claude/plans/report-md-replicated-truffle.md` M9
> section; detailed WP breakdown: `~/.claude/plans/abkit-v2-details/design_additive.json`;
> code-verified facts: `~/.claude/plans/abkit-v2-details/verify_additive.json`.

## 0. Scope, posture & decisions

**M9 kills the O(D²) full-window warehouse rescan that dominates `abk run`
cost for closed-form methods** ([recompute_backend.py:3-8](../../abkit/compute/recompute_backend.py)
— every cutoff re-renders the metric SQL over the *entire* cumulative window
and re-executes it; `recompute_backend.py:111`'s `RenderWindow` always pins
`start_ts=grid.start_ts`) by finally wiring the STATE stage and
`_ab_unit_state` that [cumulative-intervals.md §4](cumulative-intervals.md)
has specified and the schema has carried, untouched, since M2
([`_unit_state.py:31-41`](../../abkit/database/internal_tables/_unit_state.py)'s
`MOMENT_COLUMNS`; [`driver.py:18-21`](../../abkit/pipeline/driver.py) states
outright: "The STATE stage … is deliberately NOT wired in v1 … activates
when v2 flips the read path"). Independently, and shipped *first* because it
needs none of the engine, M9 makes CUPED cheap in `abk explore` by persisting
the two missing covariate moments so `cuped-t-test` becomes Tier-E (instant
reconstruction, no raw-cache fallback).

**Bootstrap/percentile methods stay on full-window Recompute forever** — there
is no additive-suffstats path for a percentile CI (`accumulate.py` proposes no
bootstrap primitive, and none is proposed here); stratified metrics are
explicitly out of scope for the incremental read path in this milestone (no
stratum dimension in `_ab_unit_state`'s key — adding one multiplies
cardinality, exactly the concern cumulative-intervals.md §5.3 already raises).

### 0.1 The no-numbers-move posture (M7–M12 discipline, restated for M9)

**Statistical numbers do not move anywhere in M9.** Every change in this
milestone is schema (new nullable columns, a new internal table's write path)
or plumbing (an alternate read path that must reproduce the existing one).
Concretely:

- **No `ALGORITHM_VERSION` bump anywhere in the diff** (grep-checkable at the
  exit gate) — nothing here is a formula change.
- **Parity gates:** exact equality on integer counts (row counts, unit
  counts); **rel-1e-9** on every continuous statistical value (effect,
  bounds, p-value, θ diagnostics) — never `==` on a float. This mirrors the
  existing Tier-E precedent: `tests/tuning/test_recompute.py`'s
  `TestTierERoundTrip` already reconstructs `m2 = std² · n` from a persisted
  `std` and asserts parity via `assert_close(rel_tol=1e-9, abs_tol=1e-12)`
  (`tests/_helpers/synthetic_ab.py:26,248-253`), not bit-identical equality.
- **The flag on/off must not change a single persisted number** — flipping
  `incremental_reads` changes *how* a number is computed, never *what* it is.
  This is the single most important assertion in the whole milestone (WP6).
- Golden tests (`tests/golden/`) pin the baseline unchanged; new tests extend
  them at the same tolerance, never loosen it.

### 0.2 Plan-review record (pre-implementation) — corrections this doc carries as loud contracts

This plan was adversarially reviewed before implementation (the M1–M8
discipline). The load-bearing corrections below are not optional footnotes —
each is restated inside its owning WP as a hard gate, not just here.

- **TRACK BLOCKER — the STATE-stage writer AND the `IncrementalBackend`
  tail-scan build their cohort SQL ONLY through the M8 factory
  `build_cohort_backend`/`ab_cohort_source`.** M8 ships a single builtin
  (`ab_cohort_source`) and a single factory (`build_cohort_backend`) that
  every call-site — driver, validate, explore, tuning-server, plan, reporting
  — goes through so that under the M8 no-copy default, `_ab_exposures` may
  not exist at all; cohort resolution instead joins the caller's own
  assignment source directly. If WP3's per-day STATE render or WP4's
  sub-day tail-scan hand-rolls its own `FROM _ab_exposures` instead of going
  through the factory, it silently joins a table that is not there under the
  no-copy default and produces **silent zeros**, not an error — the single
  most dangerous failure mode in this milestone precisely because it doesn't
  crash. Both WP3 and WP4 test **both** M8 cohort modes (copy-enabled and
  no-copy) explicitly, not just the mode a developer happens to have running
  locally.
- **The REPORT claim that CUPED θ reconstructs "byte-for-byte" is REFUTED.**
  `std = sqrt(m2/n)` followed by `m2_reconstructed = std² · n` is not a
  bit-identical round-trip in IEEE-754 double precision — squaring a
  correctly-rounded square root reproduces the operand only to within a few
  ULPs, and θ additionally divides a sum of such reconstructed terms across
  two arms, compounding rounding. The report's own next sentence recommends a
  `rel-1e-9` golden test, contradicting its own "byte-for-byte" framing two
  sentences earlier. The gate this milestone actually holds itself to is
  **rel-1e-9** (the exact tolerance the existing Tier-E round-trip tests
  already use — `rel_tol=1e-9, abs_tol=1e-12`, precedent above). "No
  `ALGORITHM_VERSION` bump" is justified as **a schema/plumbing change, not a
  byte-identity guarantee** — the honest characterization is "exact up to
  floating-point round-off (~1e-9 relative), same as the rest of the Tier-E
  suffstats reconstruction path," never "bit-identical."
- **Column additions are non-breaking, but only if a real migration
  primitive ships.** `InternalTablesManager.ensure_tables()`
  ([`_schema.py:10-23`](../../abkit/database/internal_tables/_schema.py))
  today only CREATEs tables that don't exist — it has no ALTER-TABLE-ADD-COLUMN
  path. Since `0.1.2` is already on PyPI, WP1 is the project's first
  post-release schema change: every upgrading user's `abk run` breaks on
  `save_results`'s column-mismatch check
  ([`_results.py:24-27,45-51`](../../abkit/database/internal_tables/_results.py))
  unless a real additive-diff-and-ALTER primitive ships in the same PR — this
  is a hard prerequisite, not scope creep. The primitive is a **Python-side
  diff** over `system.columns` (ClickHouse) / `information_schema.columns`
  (PG/MySQL) against the model's declared columns, emitting
  `ALTER TABLE … ADD COLUMN` for anything missing, additive-only (never
  drops/renames), idempotent, per-backend — **MySQL has no
  `ADD COLUMN IF NOT EXISTS`**, so the MySQL path must catch/ignore the
  duplicate-column error or pre-check via `information_schema` before issuing
  the ALTER.
- **Any gap in materialized state falls back to full Recompute, never a
  silent undercount.** If `_ab_unit_state` has no row for a required closed
  day (STATE hasn't run yet, or a hole from an unre-materialized backfill),
  `IncrementalBackend` must detect the gap and fall back to `RecomputeBackend`
  for that cutoff — slower, but correct — rather than serve a partial sum as
  if it were complete. Bootstrap and stratified comparisons are **always**
  `RecomputeBackend`, regardless of the opt-in flag.
- **M9 is a perf milestone — an executable perf gate is an exit criterion,**
  the same track lesson M7 encodes: a performance rule with no executable
  test does not hold (the 800k-iteration nested `for` loop in `validate`
  slipped past the numpy-first rule for exactly this reason). The exit gate
  (§7) names the concrete perf assertion.
- **The `incremental_reads` flag on/off must not change a single number** —
  restated here because it is graded as a P0 blocker at the exit gate (WP6),
  not a rounding footnote.

### 0.3 Sequencing correction versus REPORT.md

REPORT.md's step order interleaves the CUPED schema fast-win with the bigger
STATE/incremental engine under one "priority #1" banner, but they have very
different risk profiles: persisting two extra columns is a pure schema+write
change with no new read path; flipping the read path is a new backend class +
driver changes + a reconciliation gate. **This plan corrects the order: WP1–2
(CUPED Tier-E) ship first and depend only on the schema, not on WP3–5's
engine** — CUPED Tier-E should not wait for the riskiest WP in the milestone.

### 0.4 Decided elsewhere, not reopened here

Per the track's "Решено не перерешивается" rule: the M8 no-copy default and
`build_cohort_backend` contract are M8's decisions, consumed here as a fixed
dependency; the M10 schema-break bundling (drop `_ab_results` date columns +
widen `_ab_experiments`) does not touch this milestone's additive columns;
`abkit.stats` purity, the mixed-ddof convention, and the sign p-value are
untouched (nothing in M9 imports DB/warehouse code into `abkit/stats/`).

Session estimates are **not contracts** (track rule). Where a per-WP number
below exceeds the approved plan's compressed milestone table (sharpest: WP5
carries the detailed breakdown's 2 sessions vs. the table's 1, summing to ~10
vs. the plan's "~8"), the detailed estimate is carried deliberately — the
post-M7/M8 retro-calibration reconciles the totals.

---

## WP1 — Persist the 2 missing CUPED covariate moments + the schema-migration primitive

**Goal:** add `cov_std_1`, `cov_std_2`, `corr_coef_1`, `corr_coef_2`
(`Nullable(Float64)`) to `_ab_results` and wire the CUPED write path to
populate them from the already-computed `SufficientStats.cov_std`/`corr_coef`
properties. This is the report's priority #1 fast win and is fully
independent of the STATE/incremental engine (WP3–5) — it needs only the row
schema and must not wait for the bigger engine work. It also closes the real
gap discovered during verification: `ensure_tables()` has no
ALTER-TABLE-ADD-COLUMN path at all, and since `0.1.2` is already on PyPI this
is the project's first real post-release migration.

**Steps:**

1. Add a generic `ensure_columns()`/`sync_schema()` step to the schema mixin's
   `ensure_tables()`: for each existing internal table, diff the model's
   declared columns against the live table's columns
   (`information_schema.columns` on PG/MySQL; `system.columns` on
   ClickHouse) and emit `ALTER TABLE … ADD COLUMN … DEFAULT NULL` for
   anything missing — additive-only (never drops/renames), idempotent,
   implemented per-backend in the three manager classes. MySQL has no
   `ADD COLUMN IF NOT EXISTS`; guard via a pre-check against
   `information_schema.columns` or by catching the duplicate-column error.
2. Add `cov_std_1`, `cov_std_2`, `corr_coef_1`, `corr_coef_2`
   (`Nullable(Float64)`) to `get_results_table_model()` right after
   `cov_value_1/2` (`tables.py`, per-arm CUPED input block, ~lines 171-236);
   `RESULT_COLUMNS` picks them up automatically since it derives from the
   model.
3. Add the 4 matching optional fields to `TestResult` (`result.py`),
   defaulting to `None`.
4. In `CupedTTest.from_suffstats` (`cuped_ttest.py`), populate
   `cov_std_1=stats_1.cov_std`, `cov_std_2=stats_2.cov_std`,
   `corr_coef_1=stats_1.corr_coef`, `corr_coef_2=stats_2.corr_coef` on the
   returned `TestResult`. `corr_coef` can be NaN when pooled covariate
   variance is 0 (`samples.py:357-360`) — leave it as NaN; `enrich`'s
   existing NaN→`None` cleaning picks it up, matching the `cov_value_1/2`
   null-ability convention already in place.
5. Thread the 4 new keys through `enrich.rows_for_cutoff`'s row dict
   (mirrors the existing `cov_value_1/2` lines, ~lines 96-108) — `None` for
   every non-CUPED method (the `TestResult` default), so no other method's
   row shape changes.
6. Migration test: create `_ab_results` on an OLD (pre-WP1) schema fixture,
   run `ensure_tables()`, assert the 4 new columns exist and existing rows
   read back with `NULL`; run it twice, assert idempotent (no error on
   already-added columns).
7. Golden test: a CUPED run persists `cov_std_i`/`corr_coef_i` matching
   `SufficientStats.from_sample(...).cov_std`/`.corr_coef` computed
   independently from the same raw arrays, at `rel_tol=1e-9`.
8. CHANGELOG entry: schema addition, no `ALGORITHM_VERSION` bump, no
   statistical number changed — cite `statistics-changes.md`'s existing
   "schema change vs statistics change" framing.

**Files touched:** `abkit/database/tables.py` (`get_results_table_model`,
~171-236), `abkit/database/internal_tables/_schema.py` (`ensure_tables`),
`abkit/database/internal_tables/_results.py` (`RESULT_COLUMNS`), the
ClickHouse/PG/MySQL manager classes (backend-specific ALTER TABLE ADD
COLUMN), `abkit/stats/result.py` (`TestResult`), `abkit/stats/parametric/cuped_ttest.py`
(`from_suffstats` return), `abkit/pipeline/enrich.py` (`rows_for_cutoff`),
`tests/database/test_internal_tables.py`, `tests/golden/test_golden_parametric.py`,
`tests/database/test_tables_contract.py`, `CHANGELOG.md`,
`docs/specs/data-contract-and-reporting.md`.

**Tests / gates:**
- `tests/database/test_internal_tables.py::TestSchemaMigration` (new) —
  add-missing-columns is idempotent, per backend fixture (CH/PG/MySQL, as
  already parametrized elsewhere in that file).
- `tests/golden/test_golden_parametric.py` — extend the CUPED golden tests
  to also assert the 2 new columns at `rel_tol=1e-9`.
- `tests/database/test_tables_contract.py` — `RESULT_COLUMNS` count/shape
  assertion updated.

**Risks / hotspots:**
- Without the migration primitive this WP silently breaks every existing
  installed project on upgrade (an INSERT column-count mismatch) — flag to
  the maintainer as a decision point (build ALTER-ADD-COLUMN now vs. document
  a manual `ALTER TABLE` upgrade step in CHANGELOG instead); the plan assumes
  the former.
- `corr_coef` can be NaN (zero pooled variance) — must not raise on write;
  handle identically to the existing "theta non-finite" warning path in
  `cuped_ttest.py` (~lines 107-111).
- This WP may run long (3 backends × migration + schema + write path); if
  so, split into WP1a (generic migration primitive) / WP1b (CUPED columns +
  write path) rather than cramming, per the session-sizing convention.

**Session estimate:** 2 sessions (or 1+1 if split into WP1a/WP1b).

---

## WP2 — CUPED becomes Tier-E in `abk explore` (relax the three gates)

**Goal:** depends only on WP1's persisted columns, **not** on the
STATE/incremental engine (WP3–5) — this WP is sequenced right after WP1, not
last. Relax the three gates in `abkit/tuning/recompute.py`
(`_exact_suffstats` at ~line 394, `classify_knob` at ~lines 106-112, and the
`alpha_knob_tier` "alpha" branch at ~line 119) so `cuped-t-test`
reconstructs a full covariate `SufficientStats` from a persisted row and
reruns `compare_pair` exactly, for every knob except `covariate_lookback`
itself — which correctly stays Tier R, since a different lookback needs a
real pre-period re-render (`recompute_backend.py:83-96`).

**Steps:**

1. Add `_covariate_suffstats_fields(row, size, mean, m2, suffix) -> tuple[float,
   float, float] | None`: from `cov_value_{suffix}` (already persisted),
   `cov_std_{suffix} → cov_m2 = cov_std² · size`, and
   `corr_coef_{suffix} → cross_c = corr_coef · sqrt(m2 · cov_m2)` (inverting
   `samples.py:353-360`'s `corr_coef` formula); return `None` when any of the
   3 new columns is `NULL` (pre-migration rows, or NaN `corr_coef`) so it
   gracefully falls through to the existing Tier S/baseline path — never
   crashes on old rows.
2. In `_exact_suffstats`: drop the blanket
   `method_cls.requires_covariate or _needs_covariate(method_cls)` bail
   (line 394). When `method_cls.requires_covariate` and `kind == 'sample'`,
   build `SufficientStats(n=size_i, mean=value_i, m2=std_i**2*size_i,
   cov_mean=cov_value_i, cov_m2=…, cross_c=…)` via the new helper for both
   arms; if either arm's helper returns `None`, return `None` (unchanged
   fallback, e.g. legacy pre-migration rows).
3. Gate the covariate reconstruction on the CURRENT `covariate_lookback`
   param matching the value the row was computed with (reuse the existing
   `_lookback_seconds` comparison already used in `_cache_serves`, ~lines
   895-900) — a live lookback change must still route to Tier R (a new
   pre-period render), never silently reconstruct against a stale
   pre-period. This is the load-bearing safety check for this WP.
4. `classify_knob`: keep `covariate_lookback → 'R'` as the only exception;
   drop `_needs_covariate(method_cls)` from the blanket `'S'` branch (line
   110) so every OTHER CUPED knob (test_type, calculate_mde, power, alpha,
   correction) classifies `'E'`.
5. `alpha_knob_tier`: drop the `_needs_covariate(method_cls): return 'alpha'`
   branch (line 119) — alpha changes for CUPED now go through the same
   exact-suffstats-rebuild-and-rerun path as t-test/z-test, falling through
   to the default `'E'`.
6. Rewrite the 4 existing tests in `TestCupedRouting`
   (`test_recompute.py:229-330`) that currently assert Tier S/R for
   non-lookback CUPED knobs: `test_cuped_off_is_exact_over_the_whole_grid`
   (230, unaffected), `test_cuped_on_from_a_plain_series_is_a_reload` (260,
   still valid — switching a plain series TO CUPED with no cached covariate
   stays a reload), `test_cuped_param_edit_serves_cached_cutoffs_only` (288,
   must become "Tier E over the whole grid", not "cached cutoffs only"),
   `test_lookback_change_is_a_reload_not_a_cache_hit` (309, unchanged — still
   Tier R).
7. New golden round-trip test (the decision-(d) gate): persist a real CUPED
   run's rows, reconstruct via `_exact_suffstats` + `CupedTTest.from_suffstats`,
   and assert `effect`/`left_bound`/`right_bound`/`pvalue`/θ diagnostics match
   a from-scratch `from_samples` recompute at `rel_tol=1e-9, abs_tol=1e-12`
   (the same `assert_close` helper as `TestTierERoundTrip`) — NEVER assert
   `==`; this is the concrete evidence for "schema change, not
   `ALGORITHM_VERSION` bump" in the WP1 CHANGELOG entry.

**Files touched:** `abkit/tuning/recompute.py` (`_exact_suffstats` ~391-443,
`classify_knob` ~106-112, `alpha_knob_tier` ~115-121), `abkit/stats/samples.py`
(`SufficientStats` constructor, `cov_std`/`corr_coef` properties, ~325-360),
`tests/tuning/test_recompute.py` (`TestCupedRouting`, ~229-330 — rewritten,
not just extended; `TestKnobSurface::test_tier_classification_table`, ~497).

**Tests / gates:**
- `TestCupedRouting` (rewritten) — CUPED non-lookback knobs are Tier E over
  the whole grid; a lookback change is still Tier R.
- `TestKnobSurface::test_tier_classification_table` — expected tier map for
  `cuped-t-test` updated.
- New golden CUPED Tier-E round-trip at `rel_tol=1e-9` (as above).
- Backward-compat test: a pre-migration row (4 new columns NULL) still
  falls through to Tier S/baseline without raising.

**Risks / hotspots:**
- Silently mislabeling a reconstructed point as `tier='exact'` when the
  covariate columns are stale relative to a changed lookback would be a
  correctness regression worse than the current Tier-S fallback — the
  lookback-match guard (step 3) must be tested explicitly, not just assumed.
- `corr_coef` NaN (zero pooled covariate variance) must degrade to
  `None`/Tier-S fallback, not propagate a NaN `cross_c` into a "successful"
  `TestResult`.

**Session estimate:** 1 session.

> **As-built note (2026-07-21, the WP2 session).** Shipped as specified with
> four disclosed deviations: (1) `abkit/stats/samples.py` was listed in
> "Files touched" but needed **no change** — the `SufficientStats`
> constructor and `cov_std`/`corr_coef` properties already support the
> reconstruction; the stats core is untouched by this WP. (2) The helper's
> shipped signature is `_covariate_suffstats_fields(row, size, m2, suffix)`
> — the plan's `mean` parameter is unused by the inversion and was dropped.
> (3) The step-3 lookback guard is **stricter than the `_cache_serves`
> mirror the plan pointed at**: the comparison is unconditional (no
> declared-covariate skip). A persisted row is frozen — its moments were
> computed under whatever covariate source the config had at write time —
> so the live-config-driven skip would happily serve stale moments as
> "exact" after a metric-covariate-source edit (an R1 review MAJOR; the
> skip was shipped first, then removed). This also keeps `_exact_suffstats`
> consistent with `classify_knob`'s unconditional `R` for the lookback
> knob. (4) Beyond the plan's file list, the WP also rewrote the two other
> tests encoding the pre-WP2 tiering (`test_sequential_recompute.py`'s
> drop-hint leg, `test_explore_session.py`'s e2e alpha leg — both split
> into a new-behavior test + a pre-migration-rows test that pins the old
> fallback), updated the tier tables in `docs/guides/explore.md` and the
> packaged `rules/explore.md`, and narrowed the retired `'alpha'` literal
> out of `web/src/explore/payload.ts`.
> **Adversarial review R1** (4 sonnet lenses → 2 skeptics per finding with
> mandatory repro): 12 raised → 8 confirmed (2 = one duplicate defect), all
> fixed: the declared-covariate guard bypass (MAJOR, above); the CUPED
> power chip still cache-gated beside a cache-free exact point (MAJOR —
> the chip now reads `result.corr_coef_1` first, session-cache fallback);
> the client's reload demand on switching back to the configured CUPED
> method (the surface now bakes `cache.covariate_moment_rows`, the client
> exempts that switch; `explore.js` rebuilt); the `_sequentialize_points`
> docstring, a missing `identity_changed` assert, and the two plan-text
> disclosures above. 4 rejected as pre-existing/non-defects by the skeptic
> majority.
> **Adversarial review R2** (fresh: 3 lenses — R1-fix correctness, cold
> full-diff sweep, no-numbers-move proof — → skeptics with mandatory
> repro): 2 raised → 2 confirmed (one root defect): the R1 reload
> exemption silenced only `needsReload`'s FIRST gate — the unconditional
> R-tier knob scan still re-demanded the reload on the switch-away-then-
> back flow (`prevParams = {}` after a method change ⇒ the configured
> `covariate_lookback` diffed against `undefined`; reproduced by both
> skeptics in jsdom against the committed bundle). Fixed: on a switch back
> to the CONFIGURED method the R-scan baselines against the CONFIGURED
> params (the persisted series' own state); pinned by two new jsdom tests
> (exempt flow recomputes with the reload bar hidden / pre-migration rows
> still demand the reload). The numbers-immovability lens confirmed the
> write path untouched, both e2e matrix gates + cross-mode parity green,
> and the `ALGORITHM_VERSION` grep clean — zero findings.

---

## WP3 — Wire the STATE stage: per-(unit, day) moment materialization into `_ab_unit_state`

**Goal:** the write-only half of the committed v1 strategy
([cumulative-intervals.md §4](cumulative-intervals.md)). Add a new
`PipelineStep.STATE` between LOAD and COMPUTE that, for STATE-eligible
metrics (closed-form family: sample/fraction/ratio, non-stratified —
stratified is explicitly out of scope, see §8), re-renders the metric SQL
over each NOT-YET-materialized closed day `[day, day+1)` (the existing
`RenderWindow`/`build_builtins` machinery) to get one row per unit for that
day, and upserts it into `_ab_unit_state` via the already-tested
`replace_day_state` (`_unit_state.py:56-105`). This is always-on (write path
only — no reader yet, WP4 is the reader) and cheap per §4's own claim ("~80%
of the saving at near-zero added complexity").

**⚠ Blocker fix carried from §0.2: builtins ONLY through the M8 factory.**
The per-day render this WP adds must build its cohort SQL exclusively
through `build_cohort_backend`/`ab_cohort_source` (the M8 contract) — never a
hand-rolled `FROM _ab_exposures`. Under the M8 no-copy default,
`_ab_exposures` may not exist at all; a self-rolled render would silently
join a nonexistent table and yield silent zeros, not an error. Both M8
cohort modes (copy-enabled and no-copy) are tested explicitly for this WP.

**Steps:**

1. Scope v1's state identity to `(metric.name, columns.role_map(),
   sha256(normalized metric SQL text))` rather than a true cross-metric
   (source-table-level) sharing, since `MetricConfig` has no declared
   `source_table` today (its SQL is arbitrary Jinja) — a deliberate
   narrowing of §5.3's "co-located metrics share one state series" ideal.
   Document this as an explicit open decision (§8), not a silent
   under-delivery.
2. Folding the metric SQL's own hash into the identity is the only way an
   edit to a metric's SQL body (not just its column roles) correctly orphans
   stale state — mirroring how editing `method_params` already orphans
   `_ab_results` via `method_config_id`; metrics currently have no analogous
   mechanism for STATE, so this WP introduces one.
3. New per-day moment query: for each closed day since
   `get_last_state_day(source_table, column_set_id)` (existing helper,
   `_unit_state.py:134-148`) up to the watermark, render `metric_sql` with
   `window=RenderWindow(day_start, day_start+1day)` (single-day,
   non-cumulative — reuses `build_builtins`/`QueryTemplate` **through the M8
   factory**) and execute via the existing `load_metric`
   (`metric_loader.py:87-…`) to get one row per unit for that day; for
   `type=sample`: `n=1, sum_value=value, sum_value_sq=value**2` (and
   `sum_cov`/`sum_cov_sq`/`sum_value_cov` similarly IF the metric declares an
   explicit `columns.covariate` role — note this is NOT the CUPED
   pre-period covariate, which stays a separate fixed one-time load per
   `recompute_backend.py:10-14,127-139` and is untouched by this WP); for
   `type=ratio`: `sum_denominator`/`sum_denominator_sq`/`sum_value_denominator`
   from numerator/denominator roles; for `type=fraction`: treat `count`/`nobs`
   as `sum_value`/`n`.
4. Call `replace_day_state(source_table=<derived id>, column_set_id, day,
   data)` per day (idempotent by construction, existing tested invariant).
5. Insert the new `STATE` step into `run_experiment` right after LOAD,
   before the per-comparison PLAN+COMPUTE loop (~`driver.py:266-268`); gate
   it on the metric being STATE-eligible (closed-form, non-stratified,
   `comparison.method` not `_needs_seed`) so bootstrap-only metrics never
   pay this write cost.
6. `abk run --full-refresh --from/--to` (existing flag, `driver.py:280-288`,
   already deletes `_ab_results` rows in the window) must ALSO force
   re-materialization of the corresponding `_ab_unit_state` day rows in that
   window — otherwise a backfill silently leaves stale state that WP4's
   reader would trust. Add this to the same `full_refresh_window` branch.
7. Add `PipelineStep.STATE` to the `--steps` CLI parser
   (`pipeline/_types.py:17-24`) and to `abk run`'s help text / rules docs —
   **pending sign-off on open question (4) (§8)**: if the maintainer's answer
   is "always bundle with LOAD", drop the standalone `--steps state` surface
   from this step (the enum member itself still lands either way — the
   pipeline needs the stage internally).

**Files touched:** `abkit/pipeline/_types.py` (`PipelineStep` enum),
`abkit/pipeline/driver.py` (`run_experiment`, ~173-266 for the STATE
insertion point), `abkit/database/internal_tables/_unit_state.py`
(`compute_column_set_id`, `replace_day_state`), `abkit/loaders/metric_loader.py`
or a new `abkit/loaders/state_loader.py` (per-day moment extraction),
`abkit/config/metric_config.py` (if the metric-identity hash needs to fold
in the SQL body, not just `columns.role_map()`), `tests/database/test_internal_tables.py::TestUnitState`
(existing twice-run invariant test, line 257+), new `tests/pipeline/`
state-materialization tests.

**Tests / gates:**
- `TestUnitState` extended: the existing twice-run invariant, now triggered
  via the new pipeline step, not just direct `replace_day_state` calls.
- New: editing a metric's SQL body (same column roles, different aggregate
  logic) changes the derived state identity and orphans old rows (the
  metric-hash invariant).
- New: `--full-refresh --from/--to` re-materializes the affected day-state
  rows, not just `_ab_results`.
- New: a sample/ratio/fraction metric each materialize the correct
  `MOMENT_COLUMNS` shape end-to-end from a fixture warehouse.
- New: the per-day render under **both** M8 cohort modes (copy-enabled,
  no-copy) produces the same per-unit moments — the blocker-fix regression
  test.

**Risks / hotspots:**
- Stratified metrics have no stratum dimension in `_ab_unit_state`'s key
  `(source_table, column_set_id, unit_id, day)` — adding one would multiply
  cardinality (exactly the concern §5.3 already raises about per-metric
  duplication); explicitly OUT of scope for this WP, stratified stays
  full-window recompute forever until a follow-up decision.
- The metric-SQL-hash invalidation is new ground — no analogous mechanism
  exists elsewhere for metrics (only methods have `method_config_id`); needs
  explicit maintainer sign-off since it changes what "editing a metric"
  means operationally (silent re-materialization cost on next run).
- Writing per-day state on every run adds warehouse write volume even before
  anyone reads it — acceptable per §4's own "~80% of the saving at
  near-zero complexity" framing, but should be measured, not assumed (feeds
  WP5's cost observability).

**Session estimate:** 2 sessions.

> **As-built note (2026-07-24, the WP3 session).** Shipped as specified with
> the §8 Q4 decision (`--steps state` supported; the `abk run` default is
> now `validate,plan,load,state,compute`; the stage slots after LOAD+SRM,
> before COMPUTE — a standalone `--steps state` still runs the LOAD section
> as its render source) and these disclosed deviations/corrections:
> (1) **The identity is experiment-scoped** — the plan's step-1 tuple
> `(metric.name, role_map, sql-hash)` lacked it, but the per-day render
> joins THIS experiment's cohort with the exposure filter applied, so two
> experiments sharing a metric would clobber each other through
> replace-not-sum: `source_table = compute_state_source_id() =
> "{experiment}/{metric}"` (hash-tail-compacted inside the 128-char column
> budget). (2) **The identity also folds in the cohort-shaping config**
> (assignment-SQL hash, `added_filters`, `unit_key`, `variants`,
> `timezone`, `start_date` — `state_series_key()` in `pipeline/state.py` is
> THE composition every consumer must use): an R1 finding — a mid-flight
> `added_filters` edit reshapes cohort membership, and a merged series
> would mix two cohort definitions across days, an inconsistency the
> full-window recompute path can never have. (3) **Every failure path
> truncates the tail instead of leaving stale rows or dropping history**,
> preserving the contiguity invariant (every day `<= get_last_state_day()`
> is materialized; days past it are absent, not stale — the WP4
> gap-detection contract): `--full-refresh` deletes from the first touched
> day BEFORE re-rendering (a crash mid-refresh leaves a self-healing
> prefix; the tail past the window re-renders — the accepted price of
> contiguity without a per-day ledger), and a non-finite moment truncates
> from the failing day (earlier days retained, one-render retry per run).
> (4) **Metrics whose SQL references `ab_cov_*` are STATE-ineligible** —
> their render depends on the comparison's covariate window, so their day
> moments are not comparison-independent. (5) **Copy mode clamps day-close
> to the copy's coverage** (a day materialized from a partial cohort would
> freeze that way) **and `--resync-cohort` force-rebuilds day state** with
> the copy it rebuilds. (6) The orphan sweep is ACTIVE: stale
> `column_set_id` series under the source key are deleted on the next run
> (`list_state_column_sets`/`delete_state_series`;
> `delete_state_days_from` is the truncation primitive). Step 3's covariate
> co-moments ship for the explicit `columns.covariate` role only, per plan.
> **Adversarial review R1** (4 sonnet lenses → 2 skeptics per finding with
> mandatory repro; 2 lenses — driver/CLI and moment-math — were re-run in
> R2 after an infra failure): 3 raised → 3 confirmed, all fixed in-session:
> the mid-refresh crash leaving silently stale day rows (P1 → the
> truncate-then-advance fix above, pinned by
> `test_crash_mid_refresh_leaves_no_stale_day_and_self_heals`); the
> non-finite whole-series drop re-rendering full history every run with
> zero retained state (P1 → truncate-from-day, pinned by
> `test_nan_moment_truncates_from_the_failing_day`); the cohort-config
> identity gap (P1, split skeptic verdict adjudicated as
> fix-now-cheaply → deviation (2), pinned by
> `test_cohort_config_edit_orphans_the_old_series`).

---

## WP4 — `IncrementalBackend`: the opt-in read path

**Goal:** implement a new `IncrementalBackend` matching `RecomputeBackend`'s
`load_cutoff` interface — the single, confirmed injection point
(`driver.py:268` constructs the backend; `driver.py:95,371` call
`load_cutoff`) — that reads per-unit cumulative moments from
`_ab_unit_state` instead of re-scanning the raw fact table, reshapes them
into the SAME `MetricLoadResult` shape `load_metric` produces
(`metric_loader.py:65-83`) so `analyze_cutoff`/`build_container`
(`pipeline/analyze.py:87-113`) are UNCHANGED downstream. Per-comparison
backend selection is method-family-aware: bootstrap (`_needs_seed`) and
stratified comparisons always use `RecomputeBackend`; closed-form,
non-stratified comparisons use `IncrementalBackend` only when the
project/experiment opts in.

**⚠ Blocker fix carried from §0.2: the tail-scan builds cohort SQL ONLY
through the M8 factory,** same as WP3's writer — the sub-day tail read must
never hand-roll a join against `_ab_exposures`.

**This is the highest-risk WP in the milestone.**

**Steps:**

1. New `_unit_state.py` method `per_unit_cumulative(source_table,
   column_set_id, unit_ids, from_day, to_day) -> dict[unit_id, dict[moment,
   float]]`: `SELECT unit_id, sum(n), sum(sum_value), … FROM _ab_unit_state
   <FINAL/argMax dedup> WHERE source_table=… AND column_set_id=… AND day
   BETWEEN from_day AND to_day GROUP BY unit_id` — a cheap additive SUM per
   unit (no subtraction, no cancellation risk), replacing the raw fact
   rescan. The existing `sum_moments` (`_unit_state.py:107-132`) is
   deliberately test-only ("v1 uses this only in tests; the v2 incremental
   backend will read per-unit rows") — this step is exactly that v2 reader.
2. `IncrementalBackend.load_cutoff`: (a) resolve `column_set_id` for the
   metric (the same identity function WP3's writer defines); (b) split the
   window into a closed-day part `[grid.start_ts, last_midnight)` and, for
   sub-day cadence, a tail part `[last_midnight, cutoff.end_ts)` per
   [cumulative-intervals.md §6.4](cumulative-intervals.md); (c) read
   closed-day per-unit cumulative moments (step 1); (d) for the tail,
   re-render+execute the metric SQL over JUST the tail window (a small,
   bounded fact scan — at most one day of rows regardless of experiment
   age, per §6.4) **through the M8 factory** and add its per-unit
   contribution to the closed-day cumulative dict; (e) reshape the combined
   per-unit dict into the joined `_ab_exposures`-variant split (the same
   join `load_metric` does) — producing a `MetricLoadResult` with one
   'value' role array per variant, i.e. an array of PER-UNIT CUMULATIVE
   totals (not raw events), so `build_container`'s existing
   `Sample(...)`/`RatioSample(...)`/`Fraction(...)` construction and
   `SufficientStats.from_sample`'s stable two-pass numpy reduction
   (`samples.py:290-307`) are reused UNCHANGED — no new numerical code path
   for the arm-level statistic itself.
3. CUPED's pre-period covariate stays exactly as today
   (`recompute_backend.py`'s `_covariate_cache`, unaffected by this WP) —
   `IncrementalBackend` only replaces the METRIC's own cumulative-value
   load, not the covariate load; `attach_covariate` is called identically
   regardless of backend.
4. Backend selection in `driver.py` (currently one `RecomputeBackend` per
   run, line 268): change to a per-comparison resolver — `_needs_seed(method_cls)`
   or the comparison's metric using `stratify` → always `RecomputeBackend`;
   else `IncrementalBackend` iff the opt-in flag is set for this
   experiment/project, else `RecomputeBackend` (today's default, unchanged
   behavior when the flag is off).
5. Add the opt-in config flag (e.g. `project.compute.incremental_reads:
   bool = false` with an experiment-level override) — default `false` until
   WP5's `verify-incremental` gate bakes, per §0.2's decision.
6. Fallback / holes / late data: if `_ab_unit_state` has no materialized day
   inside `[grid.start_ts, last_midnight)` for this `column_set_id` (STATE
   hasn't run yet, or a hole from a late-arriving backfill not yet
   re-materialized), `IncrementalBackend` must NOT silently under-count —
   detect the gap (`get_last_state_day` < required day) and fall back to
   `RecomputeBackend` for that cutoff (safe, correct, just not fast) rather
   than serve a partial sum as if complete; log a warning surfaced in
   `RunOutcome.warnings`.
7. Multi-arm: per-unit state rows are arm-agnostic by construction (keyed by
   unit_id, joined to the cohort at read time, same as today) — no
   special-casing needed; document this as a natural consequence, not new
   code.
8. Ratio metrics: build `RatioSample`-equivalent stats from reconstructed
   per-unit `(numerator, denominator)` cumulative pairs via
   `RatioSufficientStats.from_ratio_sample` (`samples.py:398-410`) or a
   raw-array `RatioSample` if `from_samples` is required for a given method.

**Files touched:** `abkit/compute/incremental_backend.py` (NEW),
`abkit/pipeline/driver.py` (backend selection per comparison, ~line 268),
`abkit/config/project_config.py` / `abkit/config/experiment_config.py` (the
opt-in flag), `abkit/database/internal_tables/_unit_state.py` (the new
`per_unit_cumulative` reader), `abkit/stats/samples.py` (`Sample`/`RatioSample`
construction from reconstructed per-unit arrays), new `tests/compute/`.

**Tests / gates:**
- `tests/compute/test_incremental_backend.py`: `IncrementalBackend.load_cutoff`
  output is byte-identical in SHAPE and within `rel_tol=1e-9` of
  `RecomputeBackend.load_cutoff`'s resulting `SufficientStats`/`TestResult`,
  across sample/ratio/fraction metric types, daily and sub-day cadence,
  single- and multi-arm.
- Holes/late-data test: delete a mid-series `_ab_unit_state` day row, assert
  `IncrementalBackend` detects the gap and falls back to `RecomputeBackend`
  for the affected cutoff (not a silent undercount).
- Bootstrap/stratified comparisons never route to `IncrementalBackend` even
  when the flag is on (an explicit assertion, not just an omission).
- Twice-run idempotence: running `abk run` twice with the flag on produces
  byte-identical `_ab_results` rows (LWW on unchanged identity, per the
  existing `created_at` convention).
- Both M8 cohort modes exercised for the tail-scan (the blocker-fix
  regression test, same discipline as WP3).

**Risks / hotspots:**
- This is the highest-risk WP in the milestone — a silent undercount from a
  materialization gap would corrupt a live experiment's readout; the
  fallback-to-Recompute-on-gap behavior (step 6) is the single most
  load-bearing safety net and must be adversarially tested, not just
  happy-pathed.
- Sub-day tail fact-scan still touches raw events (bounded to one day per
  §6.4, but not zero) — must be measured to confirm it's actually cheap at
  realistic tail cadences (30m–1h).
- Per-unit `GROUP BY unit_id` reads over a long-running experiment's full
  state history could itself become large (though far smaller than the raw
  fact table) — no pagination/batching in this WP; flag as a possible
  follow-up if WP5's profiling shows it matters.

**Session estimate:** 2 sessions.

---

## WP5 — `abk verify-incremental` reconciliation gate + cost observability + the staged default-flip

**Goal:** build the ROADMAP-promised whole-series reconciliation command and
the profiling instrumentation that makes flipping the default (the
`incremental_reads` opt-in) a data-driven decision, exactly as
[cumulative-intervals.md §4](cumulative-intervals.md) already specifies.

**Steps:**

1. `abk verify-incremental --select <exp> [--metric <m>]`: for every
   already-computed cutoff in the series, recompute BOTH via
   `RecomputeBackend` (ground truth) and `IncrementalBackend`, diff
   `effect`/`left_bound`/`right_bound`/`pvalue`/per-arm `value`/`std`/`size`
   at a relative tolerance (default matching the existing golden 1e-9,
   configurable), report per-cutoff and whole-series pass/fail — this is
   the "whole series, not just the latest cutoff" framing §4 explicitly
   demands (a single-cutoff check would miss a state-accumulation drift
   that only shows up after many days).
2. **Naming collision, found during verification: do NOT reuse `--profile`.**
   §4's proposed observability flag text ("`abk run --profile` emits
   rows-scanned/bytes-read/wall-time") collides with the DB-connection
   -profile selector already present on every `abk` command
   (`abkit/cli/main.py:95,159,227,288,320,341,363`; `cli.md` throughout).
   Name the new flag `--explain`/`--cost-report`/`--diagnostics` instead —
   this must be resolved before writing the flag, not discovered mid-review.
3. Add per-stage counters (rows scanned, bytes read where the backend
   exposes it, wall-time) to `run_experiment`'s LOAD/STATE/COMPUTE stages,
   surfaced via the renamed flag as a table or JSON summary — the concrete
   evidence the maintainer needs to decide when/whether to flip
   `incremental_reads`'s default to `true` per-project.
4. Document the default-flip criteria explicitly (a concrete threshold,
   e.g. "`incremental_reads` defaults `true` only after N consecutive clean
   `verify-incremental` runs across the project's experiments, or a
   documented cost threshold from the renamed flag") in
   cumulative-intervals.md §4/ROADMAP — not left as a vibe.
5. `abk clean` (existing orphan-GC command) should also report/GC
   `_ab_unit_state` rows whose `column_set_id` no longer matches any current
   metric's identity (WP3's metric-hash invalidation) — the STATE-side
   analogue of its existing `_ab_results` orphan detection (`driver.py:350-362`).

**Files touched:** `abkit/cli/main.py` (new `verify-incremental` command),
`abkit/compute/reconcile.py` (NEW — the comparison engine),
`abkit/pipeline/driver.py` (per-stage rows-scanned/bytes-read/wall-time
instrumentation), `abkit/config/project_config.py` (rollout flag +
default-flip criteria doc), `docs/specs/cli-and-dx.md`,
`abkit/cli/assets/claude/rules/cli.md`.

**Tests / gates:**
- `tests/cli/test_verify_incremental.py`: a synthetic experiment with both
  backends agreeing (pass) and deliberately disagreeing (fail, non-zero
  exit) end-to-end.
- `tests/pipeline/test_driver.py`: the renamed cost-observability flag
  emits rows-scanned/wall-time without colliding with `--profile`'s existing
  meaning (an explicit regression test asserting both flags coexist).
- `tests/database/test_internal_tables.py`: `abk clean`-equivalent GC
  removes orphaned `_ab_unit_state` rows after a metric SQL edit, spares
  live ones.

**Risks / hotspots:**
- If `verify-incremental` itself is expensive (it runs BOTH backends over
  the whole series), it must never run as part of normal `abk run` — it's
  an explicit, on-demand maintainer command only.
- The `--profile` naming collision is easy to miss if someone implements
  straight from the spec text without cross-checking `cli/main.py` —
  explicitly called out here so it isn't rediscovered as a late review
  finding.

**Session estimate:** 2 sessions.

---

## WP6 — Exit gate: e2e, twice-run idempotence, ≥2 adversarial review rounds, three-way docs sync

**Goal:** the milestone's discipline gate, matching the M4/M5/M6
WP-final-closer pattern (m5-implementation-plan.md's "the M5 exit gate: e2e,
worked example, ≥2 adversarial review rounds, docs sync").

**Steps:**

1. e2e test: scaffold a project, `abk run` twice with
   `incremental_reads: true` (byte-stable second run), `abk
   verify-incremental` green over the whole series, `abk explore` shows
   `cuped-t-test` as Tier E for every non-lookback knob, then flip
   `incremental_reads` off and confirm `_ab_results` numbers are UNCHANGED
   (the flag only changes HOW the number is computed, never the number
   itself — the single most important assertion in the whole milestone).
2. Run ≥2 adversarial review rounds specifically targeting: (a) the WP4
   gap-fallback logic (can it be tricked into a silent undercount?); (b) the
   WP3 metric-SQL-hash invalidation (does every metric edit that changes
   semantics actually change the hash?); (c) the WP1 migration primitive on
   all 3 backends against a real pre-existing table fixture; (d) — carried
   from §0.2 — that WP3/WP4 build cohort SQL only through the M8 factory
   under both cohort modes, never a hand-rolled join.
3. Docs sync: `cumulative-intervals.md §4` (mark v2 as delivered, not
   "deferred"), §5.2/§5.3 (record the v1-scoped `column_set_id`
   simplification as a decision, not a silent under-delivery),
   `statistics-changes.md` (record the schema-not-statistics framing + the
   rel-1e-9 tolerance, no `ALGORITHM_VERSION` line), `ROADMAP.md` (mark M9
   items done), `CHANGELOG.md`, `.claude/rules/` + `abkit/cli/assets/claude/`
   (the packaged docs bundle must match, per the project's existing
   three-way sync convention).

**Files touched:** `tests/e2e/test_incremental_run.py` (NEW),
`docs/specs/cumulative-intervals.md`, `docs/specs/statistics-changes.md`,
`docs/specs/architecture.md`, `ROADMAP.md`, `CHANGELOG.md`, `CLAUDE.md`,
`.claude/rules/*`, `abkit/cli/assets/claude/rules/*`.

**Tests / gates:**
- `tests/e2e/test_incremental_run.py`: the full scaffold→run→verify-incremental→
  explore→flag-off-reproduces-identical-numbers cycle.
- `tests/stats/test_purity.py` still green (`abkit.stats` stays
  numpy/scipy/statsmodels-only — nothing in this milestone imports DB/warehouse
  code into `abkit/stats/`).
- Full test suite + golden suite green; no `ALGORITHM_VERSION` bump anywhere
  in the diff (grep-checkable).

**Risks / hotspots:**
- This WP is where the flag-off/flag-on number-identity assertion either
  holds or the whole milestone's central promise ("schema change, not a
  statistics change") is falsified — treat any mismatch here as a P0
  blocker, not a rounding footnote.

**Session estimate:** 1 session.

---

## 6. Dependencies

```
WP1 (CUPED schema + migration primitive)
  └─▶ WP2 (CUPED Tier-E)                       ─┐
WP3 (STATE writer, needs the M8 factory)         │
  └─▶ WP4 (IncrementalBackend, needs WP3's        │
            state + column_set_id identity,       │
            needs the M8 factory)                 │
        └─▶ WP5 (verify-incremental, needs both    │
                  backends to exist)                │
              └─▶ WP6 (exit gate, needs WP1-WP5) ◀──┘
```

- **WP2 depends only on WP1 (schema); it does NOT depend on WP3/WP4/WP5 and
  ships second, not last** — correcting a naive "explore last" assumption
  (§0.3).
- **WP4 depends on WP3** (needs materialized state to read) and on the
  `column_set_id`/metric-hash identity function WP3 defines.
- **WP5 depends on WP4** (`verify-incremental` needs both backends to
  exist).
- **WP6 depends on all of WP1–WP5.**
- **WP1 and WP3 are independent of each other** and could run in parallel
  across two sessions if resourced, but WP2 should still land right after
  WP1 regardless of WP3/WP4/WP5 progress.
- **None of this milestone depends on the `abk` dashboard (M11) or
  notifications (M12) work** — those are separate milestones.
- **Inter-milestone collision (hard dependency): M8's `build_cohort_backend`/
  `ab_cohort_source` factory is a blocking prerequisite** — WP3 and WP4
  cannot start (in the sense of writing real cohort SQL) before M8 WP4 has
  landed the factory and all call-sites route through it. This milestone
  does not introduce a second cohort-resolution path.
- **M9's additive schema stays disjoint from M10's schema breaks** — the
  4 new nullable columns here are never touched by M10's `_ab_results`
  date-column drop or the `_ab_experiments` widen; both are collected into
  M10's single recreate guide, not this milestone's.

---

## 7. Exit gate

`abk run --twice` is byte-stable with `incremental_reads` on or off; `abk
verify-incremental` passes whole-series reconciliation at `rel_tol=1e-9`
across sample/fraction/ratio metric types, daily and sub-day cadence,
single- and multi-arm; `abk explore` shows `cuped-t-test` as Tier E for
every knob except `covariate_lookback`; flipping `incremental_reads` never
changes a single persisted `_ab_results` number (only how it was computed);
no `ALGORITHM_VERSION` bump; ≥2 adversarial review rounds recorded; docs
three-way sync (`docs/specs/` + `.claude/rules/` + `abkit/cli/assets/claude/`)
complete; CHANGELOG + ROADMAP updated.

**The executable perf gate (the M9-specific instance of the M7/M9 perf-milestone
rule):** an executable test asserting that, for a synthetic experiment with a
non-trivial cumulative window (D days × N units), `IncrementalBackend.load_cutoff`
does strictly less warehouse work than `RecomputeBackend.load_cutoff` for the
same cutoff — measured via the WP5 per-stage counters (rows scanned), not
wall-clock alone (wall-clock is noisy in CI; rows-scanned is deterministic
and directly tied to the O(D²)→O(D) claim this milestone exists to prove).
This is the executable gate the track's WP5/WP9-style perf lesson demands —
a performance claim with no test does not hold.

---

## 8. Open questions / decisions before start

1. **`column_set_id` v1 scoping.** This plan derives it from `(metric.name,
   columns.role_map(), metric-SQL-hash)` rather than a true source-table-level
   identity, so [cumulative-intervals.md §5.3](cumulative-intervals.md)'s
   "co-located metrics sharing a fact source share one set of per-unit
   moments" is **NOT** delivered by this milestone (no cross-metric sharing)
   — `MetricConfig` has no declared `source_table` today and adding one is
   its own design question. **Needs explicit maintainer sign-off** on
   deferring true §5.3 sharing to a follow-up.
2. **Stratified metrics stay out of scope for the incremental engine in this
   milestone** (no stratum dimension in `_ab_unit_state`'s key, and adding
   one multiplies cardinality per §5.3's own concern) — confirm this is
   acceptable (stratified stays full-window recompute forever, or is a named
   follow-up milestone).
3. **The `--profile` naming collision** (cumulative-intervals.md §4's
   proposed observability flag vs. the existing DB-connection `--profile` on
   every `abk` command) needs a naming decision before WP5 starts.
   **Recommendation: `--cost-report`** (or `--explain`/`--diagnostics`) —
   pick one before WP5, not during it.
4. **Should WP3's STATE write step run standalone via `--steps state` (like
   today's `--steps load,compute`), or always bundle with LOAD?** Affects
   the `PipelineStep` enum's public CLI surface. The plan leans toward
   supporting `--steps state` for symmetry with the existing `--steps`
   surface, but this is a maintainer call.
   **DECIDED (2026-07-22, before WP3 started — the maintainer delegated the
   call and the plan's lean was adopted): `--steps state` IS supported.**
   `PipelineStep.STATE` sits between `load` and `compute`; the `abk run`
   default becomes `validate,plan,load,state,compute`. A standalone
   `--steps state` still runs the LOAD section internally (the cohort
   backend is the render source) but writes no results.
5. **Granularity of the `incremental_reads` opt-in flag: per-project,
   per-experiment, or per-metric?**
   [cumulative-intervals.md §4](cumulative-intervals.md) says "enabled per
   metric only after profiling proves the bottleneck" — this plan currently
   proposes project/experiment-level for simplicity; per-metric is more
   faithful to the spec but adds config surface. **Recommendation:
   experiment-level** for v1 (simplest opt-in surface, matches the "staged
   default-flip" framing of WP5), with per-metric recorded as a named
   follow-up if profiling shows heterogeneous metric costs within one
   experiment.

Per the source plan's "Перед стартом" line: settle (3) and (5) before WP5
starts and **(4) before WP3 starts** (WP3 step 7 is written conditional on
it — settled above, `--steps state` shipped); (1) and (2) can be recorded
as open decisions folded into the WP6 docs sync rather than blocking
earlier WPs, since they narrow rather than change WP3/WP4's shipped
behavior.
