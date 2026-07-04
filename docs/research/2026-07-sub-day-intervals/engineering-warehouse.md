All required specs read; external evidence gathered (network works). Report follows.

---

# Sub-day cadence as a first-class option: engineering & warehouse implications

**Verdict up front.** Support duration-typed `cadence` from M2 with **no hard floor** (parser accepts whole seconds ≥ 1s), but make honesty and cost structural, not advisory: (1) generalize the window key from `end_date` to a half-open UTC `end_ts` with `end_date` kept as a derived, friction-free column for daily users; (2) replace the `*_wo_curr_day` convention with an explicit `data_lag` watermark that is **required** config when `cadence < 1d`; (3) keep `_ab_unit_state` **day-grained** and serve sub-day cutoffs with a hybrid "closed-days state + current-day fact tail" read; (4) make the **non-uniform grid (dense-early)** first-class in v1 — it is ~10 lines of planner code and collapses the hourly cost blowup from ~23× to ~1.1×, and it is exactly what the impatient-experimenter use case needs. Industry validation: Statsig computes results **hourly for the first 24h, then daily** ([Statsig: Reading experiment results](https://docs.statsig.com/experiments/interpreting-results/read-results)) — precisely the dense-early grid — while explicitly warning not to decide on that early window; Eppo defaults to a 24h refresh with a 48h late-data lookback ([Eppo: Data pipeline](https://docs.geteppo.com/data-management/data-pipeline/)).

---

## 1. Contract generalization: from `end_date` to a duration-typed window

### 1.1 The key becomes a timestamp; the date stays as a derived convenience

Today's contract (`data-contract-and-reporting.md` §2) keys one row per `(exp, metric, pair, method, end_date)` with `start_date` (pinned), `end_date` (cutoff), `day` (cutoff length). A `Date`-typed key cannot address two cutoffs inside one day. Greenfield, so choose once, correctly:

**Proposed window column group for `_ab_results` (replaces the current one):**

| Column | Type (CH / PG / MySQL) | Semantics |
|---|---|---|
| `start_ts` | `DateTime('UTC')` / `timestamptz` / `DATETIME`(UTC convention) | pinned left edge; for date-authored experiments = exp-tz midnight of `start_date` |
| `end_ts` | same | **canonical cutoff key**; **exclusive** upper bound (`event_time >= start_ts AND event_time < end_ts`) |
| `end_date` | `Date` | **derived, stored:** `toDate(end_ts − 1s, exp_tz)` — for daily cadence this reproduces the legacy `end_date` byte-for-byte (day-D cutoff has `end_ts` = midnight of D+1, `end_date` = D) |
| `window_seconds` | `UInt64` / `bigint` | `end_ts − start_ts`; stored so BI needs no cross-backend date math |
| `elapsed_days` | `Float64` | `window_seconds / 86400` — the legacy `day` x-axis, fractional at sub-day (day 0.5 = hour 12) |

Justifications and consequences:

- **Half-open `end_ts` is the load-bearing choice.** It makes adjacent windows partition event time exactly (no double-count of the midnight second), makes daily parity exact, and — see §2 — makes the completeness rule a one-liner. The Jinja built-ins grow `ab_start_ts` / `ab_end_ts` (rendered as UTC literals); `ab_start_date` / `ab_end_date` **stay** and the packaged `ab.exposed_units()` macro emits **both** filters: the coarse `event_date BETWEEN ab_start_date AND ab_end_date` (so ClickHouse Date-partitioned fact tables still get partition pruning — a bare DateTime predicate does not prune a Date partition key) plus the precise `event_time >= ab_start_ts AND event_time < ab_end_ts`. Daily-only metric SQL that predates the change keeps working because for `cadence: 1d` the two filters are equivalent.
- **Idempotency key** becomes `(experiment, metric, name_1, name_2, method_config_id, end_ts)` with the same LWW `created_at`. `DateTime` second precision suffices (validator: cadence is whole seconds); avoid `DateTime64(3)` — plain second-precision timestamps round-trip identically across CH/PG/MySQL drivers, sub-second does not reliably.
- **Cadence is NOT part of `method_config_id`.** It is a *sampling schedule* of the same series, not method identity. Consequence worth stating in the spec: changing cadence mid-experiment is additive — new grid points appear, old ones remain valid points on the same curve; nothing is orphaned, `abk clean` untouched. This is a genuinely nice property of the pinned-start design.
- **`window_index` — rejected.** An ordinal index breaks under non-uniform grids and cadence edits (the same physical cutoff would change index). The anti-join and BI both want `end_ts`; ordinality is `ORDER BY end_ts` for free.
- **`is_horizon` semantics unchanged but needs a planner guarantee:** the horizon timestamp (`end_date + 1d` at exp-tz midnight, exclusive) must **always be emitted as a grid point even when cadence doesn't divide the duration** (cadence `7h` over 28d never lands on it otherwise). One `is_horizon=1` row per series, exactly as today; the pre-horizon WIN/LOSE refusal (decision Q2) is untouched.
- **BI x-axis compatibility:** Grafana/Metabase/Lightdash time-series axes take DateTime natively (`$__timeFilter(end_ts)`), and the legacy dashboard's `end_date BETWEEN toDate($from)…` pattern (see `docs/reference/legacy_grafana_dashboard.json`) ports to either column. Daily users see `end_date` behaving exactly as before — **friction-free default preserved**: a user who writes `start_date: 2024-07-31`, `cadence: 1d`, no timezone, gets midnight-aligned windows, a legacy-identical `end_date`, and never sees a timestamp.
- Add one provenance column: **`watermark_ts`** — the completeness boundary in force when the row was computed (§2). Cheap, and it is the only way to debug "why is the frozen hour-14 point different from what I'd recompute now" (late data).

### 1.2 Timezone discipline (one rule, stated once)

- Everything stored and compared in **UTC**; the experiment gains an optional `timezone:` field (default: project tz, default UTC) used for exactly three things: interpreting date-typed YAML (`start_date` → exp-tz midnight), deriving `end_date`, and aligning daily grid points to exp-tz midnights.
- CH: declare `DateTime('UTC')` in DDL so server tz can't leak in. PG: `timestamptz` (stored UTC by definition). MySQL: `DATETIME` + UTC convention (not `TIMESTAMP` — session-tz conversion and the 2038 limit). This lands in `internal_tables/_schema.py` via `TableModel`, once.
- **Never `today()`/`now()` in SQL.** The watermark and grid are computed once per run in Python (UTC) and passed as parameters — this was already quorum must-fix "deterministic completeness boundary"; sub-day cadence just makes it non-negotiable.

## 2. Completeness boundary at sub-day grain: `data_lag` replaces `*_wo_curr_day`

At daily grain "skip the current day" is a passable lag heuristic. At hourly grain, event-time lag *is the norm*: streaming-pipeline practice treats mobile events as arriving up to 24–48h late and server-side events ~1–2h late, and every serious system makes the lateness tolerance an explicit watermark parameter rather than a convention ([Azure Stream Analytics time handling](https://learn.microsoft.com/en-us/azure/stream-analytics/stream-analytics-time-handling); the specific 24–48h-mobile / 1–2h-server figures come from a secondary blog source and I could not verify them against a primary — treat as illustrative, not normative: [oneuptime on allowed lateness](https://oneuptime.com/blog/post/2026-02-17-how-to-handle-late-data-in-dataflow-with-allowed-lateness-and-watermarks/view)). Eppo's production answer is the same shape: incremental refresh scans a **48h lookback** before the last run to catch stragglers, with an opt-in "always full refresh" for pathological sources like refunds ([Eppo: Data pipeline](https://docs.geteppo.com/data-management/data-pipeline/), [Eppo: Warehouse best practices](https://docs.geteppo.com/data-management/warehouse-best-practices/)).

**The deterministic rule** (replaces `end_date <= today()-1` everywhere — planner, `cumulative-intervals.md` §5.6, architecture §5 step 1):

```
watermark_ts        = now_utc − data_lag          # evaluated ONCE per run, in Python
cutoff is plannable ⇔ end_ts ≤ watermark_ts        # planner keeps it; anti-join as before
```

With half-open `end_ts` and `data_lag: 0`, daily cadence reproduces `*_wo_curr_day` **exactly** (day D becomes plannable the instant exp-tz midnight passes — end_ts = midnight-of-D+1 ≤ now). So daily parity costs nothing.

**Config:**

```yaml
# experiment YAML (resolves experiment → project → error/default)
cadence: 1h
data_lag: 3h        # "my events table is complete through now − 3h"
```

- **`data_lag` is REQUIRED (config-lint error) when `cadence < 1d`**; defaulted to `0` when `cadence ≥ 1d` (legacy parity). This is the opinionated part: forcing the user to declare their ingestion lag *is* what "computed honestly at small intervals" means. A silent default here would just re-create the `*_wo_curr_day` folklore at hourly grain, and a wrong implicit lag produces systematically-biased frozen points (undercounted tails) that the stabilization chart then displays as a fake early trend.
- **The recompute design self-heals prospectively — say so in the spec.** Because every cutoff re-scans `[start_ts, end_ts)`, an event arriving later than `data_lag` is automatically included in *every cutoff computed after it lands*; only already-frozen historical rows stay stale. So `data_lag` bounds *how wrong frozen points can be*, not correctness of the ongoing series. Backfill of frozen rows = existing `abk run --full-refresh --from/--to` (the quorum's "backfill runbook" nice-to-have becomes one paragraph). Note this property is **lost** by the v2 incremental backend — add it to the already-listed v2 correctness surface in `cumulative-intervals.md` §4.
- **Defer to v2:** a data-driven watermark (`watermark: probe`, i.e. `min over sources(max(event_ts)) − safety_margin`). It needs per-source freshness introspection and per-source config; a static duration covers v1 and is deterministic/testable. Eppo's fixed-lookback model shows a static bound is production-viable.
- One more sub-day reality the spec should name: **planner-vs-wall-clock backlog.** If per-run wall time exceeds `cadence`, pending cutoffs accumulate unboundedly. The planner already handles this correctly (each run computes *all* plannable-not-computed cutoffs, thanks to the anti-join), but `run` should log a lag warning when `watermark_ts − max(computed end_ts)` exceeds a few cadence steps.

## 3. Cost: quantified, and how to not pay it

### 3.1 The blowup, precisely

Let `r` = fact rows/day, uniform. Total row-touches of pure recompute over horizon `T` days with cadence `c` is `Σ_k r·k·c ≈ r·T²/(2c)` — **linear in look frequency**, not quadratic (the D² in `cumulative-intervals.md` §2 is for fixed cadence). Concretely for a 28-day experiment (in "day-equivalents of fact rows", i.e. multiples of `r`):

| Grid | Cutoffs D | Fact rows scanned | vs daily |
|---|---|---|---|
| daily | 28 | Σd = **406 r** | 1× |
| hourly throughout | 672 | Σh/24 = **9,422 r** | **23.2×** |
| 10-min throughout | 4,032 | **56,466 r** | 139× |
| **hourly first 48h, then daily** | 48 + 26 | 49 + 403 = **452 r** | **1.11×** |
| hourly 48h → 6-hourly to day 7 → daily | 48+20+21 | ≈ **520 r** | 1.28× |

Two conclusions fall straight out. First, uniform sub-day cadence for a full experiment is an anti-feature — 23× warehouse bill for chart points nobody reads in week 3 at hour-resolution. Second, **the dense-early grid delivers the entire impatient-experimenter value at ~11% over daily cost**, because the expensive cutoffs are the *late* ones (big windows) and sub-day demand is *early*. This is also exactly the shape Statsig ships (hourly Scorecard for the first 24h only, then daily; they additionally tell users not to make decisions on that window) ([Statsig: Reading experiment results](https://docs.statsig.com/experiments/interpreting-results/read-results), [Statsig: Pulse FAQ](https://docs.statsig.com/experiments/interpreting-results/faq)) — I verified this only via search-result summaries of those docs pages, not a full page fetch; flagging accordingly, but multiple Statsig doc pages repeat it.

### 3.2 The agg-state seam: keep `_ab_unit_state` day-grained; hybrid tail for sub-day

Should `_ab_unit_state` be keyed per interval instead of per day? **No.** Recommendation: **state stays day-bucketed; a sub-day cutoff reads (closed-day state) + (current-day fact tail).**

- Read cost per hourly cutoff on day `d`: `U·(d−1)` state rows + ≤ `r` tail fact rows (average `r/2`). With `k = r/U` events per unit-day (fact-to-state compression; **assumption, not measured** — typical 10–100× for event tables), a 28-day *uniform hourly* grid costs ≈ `9,744·U` state + `336·r` tail ≈ `(9.7 + 336·k/… )` — at `k = 20`: ≈ **16.5k·U** row-touches vs **188k·U** fact-only: ~**11× cheaper**, and the tail term is bounded by one day of facts regardless of experiment age. Combined with the dense-early grid the whole thing collapses to ≈ daily cost.
- Interval-keyed state is strictly worse on every axis: up to ×24 state rows and inserts; the bucket grid is wrong the moment cadence changes mid-experiment (day buckets are cadence-independent); and it multiplies the exact part-churn ClickHouse tells you to avoid — partition/bucket proliferation and per-partition merge overhead, "avoid hourly partitioning in production", keep partition counts low ([ClickHouse: choosing a partitioning key](https://clickhouse.com/docs/best-practices/choosing-a-partitioning-key); the "<1,000 partitions/table" figure is from doc summaries — verify against the current page before pinning it in a spec).
- **Even better for v1: advance the state stage only at day close.** Sub-day runs then write *zero* state (tail is always a fact scan), so the `(exp, day)` idempotency must-fix (`cumulative-intervals.md` §5.2), the replace-not-sum design, and the twice-run invariant test all carry over **unchanged**. Hourly cadence adds no new write path to the scalability seam. This is the single strongest engineering argument for the hybrid.
- `_ab_results` itself is a non-issue: even uniform-hourly it's `E·M·pairs·672` rows — thousands per experiment. Partition by month or not at all; the churn concern is inserts-per-run creating parts, which at 24 runs/day × small inserts is well within CH tolerance.
- The **cohort persist** must-fix (§5.5) quietly becomes 24× more valuable: without it, the exposure sub-query re-expands per cutoff and the O(D) multiplier applies to the session scan too.

### 3.3 Non-uniform grid: first-class in v1

Given §3.1, **yes — first-class, not v2.** It is planner-only work (pure-Python grid generation; anti-join, contract, stats, BI all operate on `end_ts` and don't care about spacing):

```yaml
cadence: 1d                      # scalar — unchanged, friction-free default
# or a schedule:
cadence:
  - {every: 1h, until: 48h}      # dense while impatient
  - {every: 1d}                  # then daily to horizon
```

Grid rules (pin these in the spec): dense segments anchor at `start_ts` (exposure-relative); the trailing daily segment **snaps to exp-tz midnights** (so the daily tail of the series is point-for-point comparable with a pure-daily experiment and with legacy charts); segments must be non-overlapping and coarsening; the horizon point is always appended. Validation in `config/validator.py`: `until` strictly increasing, `every` whole seconds ≥ 1s, last segment open-ended.

## 4. Honesty at small intervals: what the engine must enforce (not merely document)

The statistics angle is presumably covered elsewhere; here is what has *engineering hooks*:

1. **Peeking scales with looks.** Continuous monitoring inflates type-I error dramatically — Johari–Koomen–Pekelis–Walsh measure inflation to several times nominal α, with damage growing fast for the first looks then roughly logarithmically ([Always Valid Inference, Johari et al.](https://arxiv.org/pdf/1512.04922), [KDD'17 paper](http://library.usc.edu.ph/ACM/KKD%202017/pdfs/p1517.pdf); the popular "5×"/"26% at frequent peeks" figures circulating are from secondary write-ups — [acolyer's summary](https://blog.acolyer.org/2017/09/28/peeking-at-ab-tests-continuous-monitoring-without-pain/) — don't quote them as measured without running your own A/A, which is exactly what `abk validate` is for). Engine hooks: (a) `abk validate`'s peeking-FPR **must run over the experiment's actual cadence grid**, not a hardcoded day-grid (`aa-false-positive-matrix.md` §3 wording needs "day-grid" → "cadence grid"); (b) config-lint **warning** when `cadence < 1d ∧ ¬sequential.enabled` ("672 looks under fixed-horizon CIs; the readout will refuse pre-horizon WIN/LOSE; expect a large measured peeking-FPR"); (c) mSPRT/always-valid CIs are valid under *arbitrary* look schedules, so `sequential: always_valid` needs no per-cadence math — but **alpha-spending (GST) requires a pre-committed look grid**, so M5 must either bind the spending schedule to the planner grid at config time or forbid `group_sequential` with schedule-typed cadence. Optimizely built its Stats Engine on mSPRT precisely to make real-time result viewing safe ([Optimizely Stats Engine story](https://www.optimizely.com/insights/blog/statistics-for-the-internet-age-the-story-behind-optimizelys-new-stats-engine/)). Sub-day cadence is, frankly, the strongest argument for pulling M5's `always_valid` earlier or shipping them in the same release.
2. **Small-n degeneracy.** Hour-1 cutoffs will hit n in the dozens, zero-conversion proportions, zero-variance samples. The compute stage needs a deterministic guard: emit the row with a `insufficient_data` flag (and NULL test columns) below a floor (e.g. `min_units_per_arm`, default ~50, configurable) instead of writing a fake CI or crashing on divide-by-zero. That keeps the grid dense and the chart honest ("greyed-out early points") without special-casing methods.
3. **Cohort immaturity + early-hour artifacts.** A cumulative metric at hour h mixes exposure ages 0…h; users exposed minutes ago have had no time to convert, so early points are *systematically* biased versus horizon values, and hourly data is riddled with uncontrolled artifacts (Kohavi documents an hourly-CTR investigation where a 7-hour apparent effect was an uncontrolled headline difference; early "primacy" trends are usually statistical artifacts) ([Kohavi et al., Trustworthy Online Controlled Experiments: Five Puzzling Outcomes](https://exp-platform.com/Documents/puzzlingOutcomesInControlledExperiments.pdf)). The honest framing — which should go verbatim into the docs and the report UI — is Statsig's: **sub-day points are for launch monitoring (exposures flowing, SRM, logging bugs), not for decisions.** The existing pre-horizon refusal already enforces the decision half; the report should visually segregate the dense-early segment.
4. **SRM during ramp.** Gradual rollouts change the split during the dense phase, and because SRM is computed on *cumulative* exposures, an early ramp contaminates the cumulative split long after. This bug exists at daily grain too, but hourly cadence will surface it on day one. v1: document it and let `expected_split` checks flag; a ramp-aware `expected_split` schedule is a clean v2 item.

## 5. Concrete recommendations (M2 planner + what to defer)

**Do in M2 (small, load-bearing):**
1. `core/interval.py`: duration grammar `N{s,m,h,d,w}`, whole seconds, ≥ 1s. **No hard floor** — a floor buys nothing (the honesty gates above do the real work) and forecloses legitimate uses (5-min cadence on a 2-hour ops experiment); instead: lint-warn `< 1h`, lint-error `cadence > horizon`, and require `data_lag` when `< 1d`.
2. `core/period_planner.py`: generate the grid in UTC timestamps (`start_ts + Σ every`, schedule segments, midnight-snap for daily segments, horizon point appended); plannable ⇔ `end_ts ≤ now_utc − data_lag`; anti-join keyed on `end_ts`. Schedule-typed `cadence` (scalar = one open segment) — first-class.
3. `_ab_results`: window group = `start_ts, end_ts, end_date, window_seconds, elapsed_days` (+ `watermark_ts` provenance). Idempotency key on `end_ts`. Update `data-contract-and-reporting.md` §2 and the Jinja built-ins table (`ab_start_ts`/`ab_end_ts`, macro emits date-prune + ts-precise filters).
4. State stage: day-grained `_ab_unit_state` advanced **only at day close**; sub-day cutoffs read state + fact tail. Zero change to the idempotency/cardinality must-fixes.
5. Compute guard: `insufficient_data` small-n row flag.
6. `abk validate` (M4): peeking-FPR over the cadence grid; document that sub-day A/A multiplies iterations ×grid-size and inherits the §6 cost bounds (closed-form default, subsampling) with extra force.

**Defer:** probe-based watermarks (v2); ramp-aware SRM expected-split schedules (v2); alpha-spending on non-uniform grids (M5, with the pre-committed-grid constraint stated now); interval-keyed unit state (never — hybrid dominates); sub-second anything (parser allows seconds; docs say why you don't want them).

**Unverified-claims register:** Statsig hourly-first-24h and Eppo 48h-lookback details come from search summaries of vendor docs (multiple consistent pages, not fully fetched); the numeric peeking-FPR figures (5×, 26%) are secondary retellings of Johari et al.; stream-processing lateness norms (24–48h mobile) and the LinkedIn/Uber watermark anecdotes trace to a low-authority blog; ClickHouse "<1,000 partitions" is a doc-summary figure; the `k = r/U ≈ 20` compression factor and uniform-arrival cost model in §3 are my assumptions — the `run --profile` hooks already planned in `cumulative-intervals.md` §4 are the right instrument to replace them with measured numbers.

Spec files this lands in: `/home/user/ab-analysis-kit/docs/specs/cumulative-intervals.md` (§2 cost model, §5.2/5.6 rewrite, §6 key), `/home/user/ab-analysis-kit/docs/specs/declarative-config.md` (§2 cadence/data_lag/timezone, §5 built-ins), `/home/user/ab-analysis-kit/docs/specs/data-contract-and-reporting.md` (§2 window group, §4), `/home/user/ab-analysis-kit/docs/specs/aa-false-positive-matrix.md` (§3 "day-grid" → "cadence grid"), `/home/user/ab-analysis-kit/docs/specs/architecture.md` (§5 steps 1–2).
