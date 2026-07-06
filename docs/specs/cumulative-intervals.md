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
  proves the bottleneck, behind `abk verify-incremental` (reconciles the
  incremental backend against recompute to a relative tolerance across the
  **whole** cumulative series, not just the latest cutoff). The Python delta store
  owns a real correctness surface — per-unit memory growing for the experiment
  lifetime, late/backfill events, stratum-membership changes, covariate updates,
  reproducible seeds — so it is premature before the win is proven.

`abk run --profile` emits rows-scanned / bytes-read / wall-time per stage so the
v2 trigger is **data-driven** (a concrete p95 cost/latency threshold over E·M·D),
not guessed.

## 5. Compute must-fixes (from the quorum — blocking)

1. **Covariate-window semantics — DECIDED: (b) fixed lookback.** The legacy CUPED
   covariate used a **growing** lookback; the choice was between:
   - (a) reproduce the growing window bit-for-baseline; or
   - **(b) fixed lookback** (e.g. `covariate_lookback: 14d`) as a documented,
     version-recorded deviation — a stationary covariate across the series, which
     will **not** match the legacy CUPED number.
   **Resolution (2026-07, recorded in [statistics-changes.md §5](statistics-changes.md)):
   (b), fixed lookback in whole days, independent of cadence.** Sub-day cadences
   (§6) tipped it: the growing-window rule (`agg_dates_count = end − start + 1`)
   is incoherent below a day — fractional lookbacks, diurnally-confounded
   covariates, θ jittering per hour. The scaffolded example metric uses the fixed
   lookback; an (a)-mode reproduction is available only for legacy-parity golden
   runs, never as config.
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
   completeness boundary instead of `today()`, and normalise date/timezone
   comparison across backends, so "which cutoffs are pending" is deterministic
   and backend-consistent. **Superseded/sharpened by §6.2:** the boundary is the
   timestamp watermark `now_utc − data_lag`, computed once per run in Python;
   with `data_lag: 0` and half-open windows it reproduces `*_wo_curr_day`
   exactly at daily cadence.
7. **Concurrency model.** Lock at `(experiment)` (or `(experiment, metric)`) grain
   so independent series run concurrently; add a worker-pool driver (the
   `default_rng` change makes the stats core process-safe). State this in `compute.md`.
8. **Bootstrap memory wall.** Do **not** cache full per-unit arrays in Python at
   scale. Default/auto-select the **Poisson** engine (weights × value-vector, a
   single matmul, no value matrix) above a unit threshold; stream resampling in
   replicate blocks under a memory cap; add a pre-flight memory estimate in
   `plan`/`run` that downgrades or refuses rather than OOMing. (See
   [statistics-changes.md](statistics-changes.md) §engine-hygiene.)

## 6. Sub-day cadences (DECIDED 2026-07 — first-class, no time floor)

> User requirement: sub-day intervals must be a built-in option from the start —
> for the most impatient experimenters — with HONEST computation at small
> intervals. Synthesized from a three-angle research pass (statistical honesty,
> warehouse engineering, config/DX + competitor scan). Every guard below is a
> named mechanism, never folklore.

### 6.1 The decision

1. **`cadence` is a true duration** (whole seconds ≥ 1s, `core/interval.py`
   grammar `N{s,m,h,d,w}`), default `1d`. **No hard time floor** — the dangerous
   variable is the *look count*, not the time unit (30m cadence on a 4-hour ops
   experiment is 8 pristine looks; 1d on a two-year experiment is 730 bad ones).
   The hard gate is **`max_looks`** (project-level, default 5000 → config
   error); `warn_looks` (default 100) without `sequential.enabled` → loud
   warning quoting the look count and the measured peeking FPR.
2. **Schedule-typed cadence is first-class in v1** (dense-early — the entire
   impatient-experimenter value at ~1.1× daily cost, vs 23× for uniform hourly;
   the shape Statsig ships as hourly-first-24h-then-daily):
   ```yaml
   cadence: 1d                      # scalar — unchanged, friction-free default
   # or a coarsening schedule:
   cadence:
     - {every: 1h, until: 48h}      # dense while impatient
     - {every: 1d}                  # then daily to horizon
   ```
   Grid rules: dense segments anchor at `start_ts`; daily segments snap to
   experiment-timezone midnights (point-for-point comparable with pure-daily
   series); segments non-overlapping and coarsening; the horizon point is always
   appended and carries `is_horizon=1` even when cadence does not divide the
   duration.
3. **Honesty posture (extends decision Q2).** `cadence < 1d` with
   `sequential.enabled: false` is allowed but is **monitoring mode**: rows carry
   `ci_kind: fixed`, the readout still refuses pre-horizon WIN/LOSE, and the
   fixed-CI band renders de-emphasized ("not peeking-valid"). The sanctioned
   early-decision path is `sequential: {enabled: true, scheme: always_valid}`
   (mSPRT holds at ANY data-dependent look schedule); `scheme: alpha_spending`
   with `cadence < 1d` is a config ERROR (group-sequential assumes a small
   pre-committed look grid). The warning copy sells sequential as *the thing
   that lets you decide earlier* — that framing is true and is what works.
4. **Early looks are demoted, not hidden** (the Amplitude/Statsig pattern):
   below `min_units_per_arm` (project default ~100) the row is written with an
   `insufficient_data` flag and NULLed test columns — counts and SRM stay
   visible (hour-grain SRM/logging-bug detection is the genuine sub-day payoff),
   inference is withheld. Bootstrap methods get a stricter floor + a cost
   warning on sub-day grids.
5. **Rejected alternative (recorded):** a `first_cutoff_after: 24h` warm-up
   before the first cutoff. It would cheaply remove most incremental peeking
   exposure (the damage concentrates in the earliest looks — FPR grows roughly
   with log(T/t₀)), but it also removes the monitoring value (SRM at hour 3),
   which demotion preserves. The dense-early schedule + demotion + the
   pre-horizon refusal cover the same risk without a new knob.

### 6.2 Completeness: `data_lag` watermark replaces `*_wo_curr_day`

`data_complete_through` becomes a **timestamp**, computed ONCE per run in
Python (never `now()`/`today()` in SQL):

```
watermark_ts        = now_utc − data_lag
cutoff is plannable ⇔ end_ts ≤ watermark_ts     # planner anti-join as before
```

- `data_lag` is **required config when `cadence < 1d`** (lint error) — declaring
  the ingestion SLA *is* what "honest at small intervals" means; a silent
  default would recreate the `*_wo_curr_day` folklore at hourly grain. For
  `cadence ≥ 1d` it defaults to `0`, which with half-open `end_ts` reproduces
  the legacy convention exactly.
- Late data: recompute self-heals prospectively (every later cutoff re-scans the
  full window), so `data_lag` bounds how wrong FROZEN points can be, not the
  ongoing series; frozen rows are re-opened by `abk run --full-refresh
  --from/--to`. Named failure mode: treatment-changed event latency (client
  batching/retries) biases short tail windows in ways neither randomization nor
  the A/A matrix detects — conservative `data_lag` is the mitigation.
- Each row stores the `watermark_ts` in force when computed (provenance).
- v2 (deferred): probe-based watermark from per-source freshness.

### 6.3 Window contract (timestamps canonical, dates derived)

One row per `(experiment, metric, pair, method_config_id, end_ts)`:
`start_ts`/`end_ts` are UTC DateTimes, `end_ts` **exclusive** (half-open windows
partition event time exactly; daily parity is byte-clean); `end_date` stays as a
derived stored Date (legacy-identical for `cadence: 1d` — daily users never see
a timestamp); plus `window_seconds` and fractional `elapsed_days` (the chart
x-axis; day 0.5 = hour 12). An ordinal `look_index` is deliberately NOT stored —
it breaks under schedule grids and cadence edits; ordinality is `ORDER BY
end_ts`. **Cadence is NOT part of `method_config_id`** — it is a sampling
schedule of the same series, so changing it mid-experiment is purely additive
(new grid points appear; nothing is orphaned). Experiments gain an optional
`timezone:` (default project tz → UTC) used to interpret date-typed YAML and
snap daily grid points; storage/comparison is always UTC.

### 6.4 Compute strategy (state stays day-grained)

`_ab_unit_state` remains **day-bucketed**; a sub-day cutoff reads (closed-day
state through the last midnight) + (fact-scan of the current-day tail) — each
sub-day look costs at most one day of fact rows regardless of experiment age,
and the state stage advances **only at day close**, so the §5.2 idempotency
invariant, replace-not-sum design and twice-run test carry over unchanged.
Interval-keyed state is rejected (×24 rows/inserts, wrong the moment cadence
changes, ClickHouse part churn). The packaged macro emits BOTH the coarse
`event_date` predicate (Date partition pruning) and the precise
`event_time >= ab_start_ts AND event_time < ab_end_ts` filter — metric SQL
authors change nothing. The §5.5 cohort-persist must-fix becomes ~24× more
valuable at hourly grain. `abk plan`/config-lint echo the projected look count
and cost before accepting a sub-day grid; `run` warns when
`watermark_ts − max(computed end_ts)` exceeds a few cadence steps (backlog).

### 6.5 Statistics at sub-day grain (details in the companion specs)

- **A/A matrix:** peeking-FPR runs over the experiment's ACTUAL cadence grid
  (prefix-summed sufficient statistics keep it tractable; the grid may be
  subsampled to ~100 points, denser early, and must say so); a new
  **effect-exaggeration-at-stop** (winner's curse) column lands next to FPR;
  sequential power/CI-width is measured side-by-side, never asserted.
  → aa-false-positive-matrix.md §3/§6.
- **SRM:** χ² at every sub-day cutoff is itself peeking on the SRM test (false
  alarms on a hard gate); at `cadence < 1d` the SRM gate uses the anytime-valid
  sequential multinomial test (Lindon & Malek, NeurIPS 2022 — the Netflix/
  Optimizely approach). **Shipped (M5 WP5)**, one verdict per look off the
  cumulative as-of exposure counts. → data-contract-and-reporting.md §6,
  statistics-changes.md §4.2.
- **CUPED:** sub-day cadence resolves the §5.1 pending decision to **(b) fixed
  lookback in whole days** — the legacy growing window is incoherent below a
  day (fractional `agg_dates_count`, θ jittering hourly). Lint: error on
  `covariate_lookback < 1d`, warn `< 7d` (diurnal/weekday confounding).
  → statistics-changes.md §5.
- **Representativeness (display honesty, not a stats change):** any WIN/LOSE
  called before `min(7d, horizon)` — even under sequential — carries a
  "covers X% of a weekly cycle" caveat chip: early cumulative estimates
  describe the population exposed so far (heavy users, one timezone slice,
  novelty effects), not steady state. Under H0 randomization keeps both arms
  identically mixed, so the test itself is not invalidated — early points are
  noisy and unrepresentative, not biased.
- **Grid alignment:** warn when `24h % cadence != 0` (the grid drifts across
  midnight; diurnal composition oscillates across looks and daily BI rollups
  misalign).

## 7. Idempotency model

An experiment is a **finite, re-runnable full recomputation** over the accrued
window — **not** a resumed timestamp cursor. Idempotency is last-writer-wins on
`(experiment, metric, variant-pair, method_config_id, end_ts)` (§6.3; for daily
cadence `end_ts` maps 1:1 onto the legacy `end_date`) via a strictly-monotonic
`created_at` version. The planner's `is_calculated` anti-join skips computed
cutoffs; editing `method_params` changes `method_config_id`, orphaning old rows
that `abk clean` GCs. Bootstrap seeds are derived deterministically per-row
(from the row identity including `end_ts` — see
[statistics-changes.md](statistics-changes.md)) so re-runs are byte-stable.
