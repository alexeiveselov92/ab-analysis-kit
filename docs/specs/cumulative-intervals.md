# Cumulative intervals & compute strategy

> Answers the founding question: *must we re-query & recompute the whole window
> from the DB every interval, or can we accumulate incrementally?*
> **Verdict: PARTIALLY REFUTE.** Most of the belief is avoidable; one part is real.

## 1. How the legacy cumulative chart actually works (confirmed)

The stabilization chart is **not** an in-SQL window/`groupArray`/cumsum trick. It
emerges from two things:

1. **The period planner** (`metric_calc_periods.sql`) enumerates expanding windows:
   `start_date` is **pinned** to experiment start; `end_date = start_date + day`
   for `day = 0, 1, 2, …`. So intervals are `[start..start]`, `[start..start+1]`,
   `[start..start+2]`, … — all anchored at the same start. It LEFT-ANTI-JOINs
   already-computed `(exp, metric, start, end)` rows (`is_calculated`) and keeps
   only complete days (`end_date <= today()-1`, matching `*_wo_curr_day` sources).
2. **The metric SQL** always filters `event_date BETWEEN start_date AND end_date`.
   Since `start_date` never moves, day *N*'s run aggregates the **full window from
   experiment start through day N** → cumulative-from-start, one row per arm.

`day` (the cutoff length 0,1,2,…) is the chart's x-axis; each point is a fully
recomputed cumulative result. **It is full recomputation per day-cutoff**, made
cheap-to-skip by the anti-join. The user's description is exactly right.

> ⚠️ **The CUPED covariate window GROWS too.** In `exp_all_arpu.sql` the covariate
> (`prev_gross_usd`) is summed over `event_date BETWEEN start_date - agg_dates_count
> - 1 AND start_date - 1`, where `agg_dates_count = end_date - start_date + 1`. So
> the pre-period lookback **grows symmetrically** with the cumulative window (≈2
> pre-days on day 1, ≈28 on day 28). The covariate is therefore **not**
> fixed-per-unit; θ and the covariate moments change every interval. This is a
> must-fix decision — see §5.

## 2. The cost, precisely

Naive recompute re-scans `[start..end]` once per day-cutoff, so one
`(experiment, metric)` over *D* days costs ≈ **O(D²)** row-touches, and across
*E* experiments × *M* metrics it is **O(E·M·D²)**. The test math is microseconds;
**the warehouse window re-scan is the entire bill.** The exposure/cohort
sub-query re-expands every interval too (a second growing scan) unless we persist
the cohort.

## 3. The investigation, per method family

The key insight: the user's belief conflates **two aggregation levels**.

- **Level 1 — the per-unit cumulative metric** (e.g. cumulative ARPU per user).
  The legacy SQL shows each unit's value is a `sum`/`count`/`uniq` over the window
  — **additive across day-chunks per unit**. This is where the cost lives, and for
  the sum/count metrics that dominate the registry it is incrementally maintainable
  (cached per-unit prefix + new-day delta + newly-entering units).
- **Level 2 — the cross-unit test statistic.** On top of per-unit values:

| Method family | Incremental? | Sufficient statistics / approach |
|---|---|---|
| mean / t-test (Welch) | **yes** | `{n, Σx, Σx²}` per group — exact, trivially mergeable |
| proportions / z-test | **yes** | `{count, nobs}` per group (legacy `Fraction` already proves this) |
| CUPED t-test | **yes** | 6 co-moments `{n, ΣY, ΣX, ΣY², ΣX², ΣXY}` per group → θ, var(CUP), cov(CUP,Y) all derivable; exact (caveat: growing covariate window, §5) |
| ratio / post-normed (delta) | **yes** | `{ΣN, ΣD, ΣN², ΣD², ΣND}` → per-unit linearisation `L_u = N_u/D̄ − (N̄/D̄²)·D_u` |
| stratified | **yes** | per-stratum sufficient statistics |
| bootstrap (plain/paired/poisson/post-normed) | **partial** | needs per-unit arrays, **not** a DB re-scan; cache arrays + day-delta, re-resample **in memory** |
| quantiles / median (as the cross-unit stat) | **no** | genuinely non-additive — needs full per-unit data or a mergeable sketch (t-digest/KLL) |

So **"must recompute from the DB each interval" is false for everything except**
(a) the per-unit aggregation of *non-additive* per-unit metrics (rare here) and
(b) *quantile* cross-unit statistics. The user is right about medians; wrong as a
general rule.

## 4. The committed strategy (v1 / v2)

**Incrementality lives in the WAREHOUSE, not Python.**

- **v1 (default): recompute per interval, BUT push per-unit aggregation into a
  warehouse agg-state seam (`_ab_unit_state`) from day one, and drive the test
  layer through additive sufficient statistics.** The cumulative window becomes a
  cheap range-read of partial aggregates instead of a full fact re-scan.
  ClickHouse: a `SummingMergeTree`/`AggregatingMergeTree`-backed state;
  PG/MySQL: an upserted state table. This captures ~80% of the saving at
  **near-zero added complexity and zero statistical risk**, and matches the
  legacy crutch's proven behavior (recompute from arrays every run).
- **v2 (deferred, gated): the Python incremental accumulator + array-cache
  re-resampling + quantile sketches**, enabled per metric only after profiling
  proves the bottleneck, behind `abkit verify-incremental` (reconciles the
  incremental backend against recompute to a relative tolerance across the
  **whole** cumulative series, not just the latest cutoff). The Python delta store
  owns a real correctness surface — per-unit memory growing for the experiment
  lifetime, late/backfill events, stratum-membership changes, covariate updates,
  reproducible seeds — so it is premature before the win is proven.

`abkit run --profile` emits rows-scanned / bytes-read / wall-time per stage so the
v2 trigger is **data-driven** (a concrete p95 cost/latency threshold over E·M·D),
not guessed.

## 5. Compute must-fixes (from the quorum — blocking)

1. **Covariate-window semantics (decide & pin).** The legacy CUPED covariate uses
   a **growing** lookback. Choose, document, and golden-test ONE of:
   - **(a) reproduce the growing window** bit-for-baseline (covariate moments
     re-derived each interval; the state seam only helps the current-window Y side); or
   - **(b) fixed lookback** (e.g. `covariate_lookback: 14d`) as an
     `ALGORITHM_VERSION`-bumped, documented deviation — arguably *more* correct (a
     stationary covariate across the daily series) but it will **not** match the
     legacy CUPED number. Record the choice in [statistics-changes.md](statistics-changes.md).
   The scaffolded example metric must use whichever is chosen (no silent mismatch).
2. **`_ab_unit_state` idempotency per (exp, day).** A `SummingMergeTree` re-inserted
   on re-run/backfill/lost-lock **double-counts** with no dedup. Use a `day`/version
   dimension with replace-not-sum (`ReplacingMergeTree(version)` keyed by
   `(…, unit, day)`, sum across days at read), or a per-(exp, metric, day)
   materialization guard. Invariant test: running the state stage twice for one day
   leaves aggregates unchanged. (This corruption is silent in v1 and only surfaces
   when v2 flips the read path — fix it now.)
3. **`_ab_unit_state` cardinality.** Key per **(source-table, column-set, unit)**,
   not `(exp, metric, unit)`, so co-located metrics sharing a fact source share
   one set of per-unit moments (avoids ×M storage/writes).
4. **Correctness under async merge.** All correctness-sensitive reads (cumulative
   read, planner anti-join, BI datasource) must use `-Merge`/`FINAL`/`argMax` dedup
   so partial pre-merge state is never read.
5. **Persist the cohort once.** The metric loader must JOIN the persisted
   `_ab_exposures` (loaded once per experiment) instead of re-rendering the visitor
   sub-query every interval, so the session-table cohort scan is paid O(1), not O(D).
6. **Deterministic completeness boundary.** Pin an explicit, single-source
   `data_complete_through` date (from the `*_wo_curr_day` convention) instead of
   `today()`, and normalise date/timezone comparison across backends, so "which
   day-cutoffs are pending" is deterministic and backend-consistent.
7. **Concurrency model.** Lock at `(experiment)` (or `(experiment, metric)`) grain
   so independent series run concurrently; add a worker-pool driver (the
   `default_rng` change makes the stats core process-safe). State this in `compute.md`.
8. **Bootstrap memory wall.** Do **not** cache full per-unit arrays in Python at
   scale. Default/auto-select the **Poisson** engine (weights × value-vector, a
   single matmul, no value matrix) above a unit threshold; stream resampling in
   replicate blocks under a memory cap; add a pre-flight memory estimate in
   `plan`/`run` that downgrades or refuses rather than OOMing. (See
   [statistics-changes.md](statistics-changes.md) §engine-hygiene.)

## 6. Idempotency model

An experiment is a **finite, re-runnable full recomputation** over the accrued
window — **not** a resumed timestamp cursor. Idempotency is last-writer-wins on
`(experiment, metric, variant-pair, method_config_id, end_date)` via a
strictly-monotonic `created_at` version. The planner's `is_calculated` anti-join
skips computed day-cutoffs; editing `method_params` changes `method_config_id`,
orphaning old rows that `abkit clean` GCs. Bootstrap seeds are derived
deterministically per-row (see [statistics-changes.md](statistics-changes.md)) so
re-runs are byte-stable.
