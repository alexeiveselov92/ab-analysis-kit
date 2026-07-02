All research complete. Here is the report.

---

# Sub-day cadence for abkit: config/DX surface, competitive practice, and the floor decision

**Network status:** WebSearch worked; direct WebFetch/curl to `docs.statsig.com`, `docs.geteppo.com` returned 403 (bot protection / proxy). All competitor quotes below come from search-result snippets of the cited pages, not full-page reads — individually flagged where that weakens confidence.

## 1. TL;DR recommendations

1. **Ship sub-day cadence in v1 as a first-class value of the existing `cadence` field** (`30m`, `1h`, `6h`, `1d`), parsed by the already-planned `core/interval.py` duration parser (architecture.md §4 already says it handles "14d"/"1d"/seconds — no new machinery).
2. **No hard cadence floor. The hard limit is on *look count*, not on the time unit** (`max_looks`, project-level, default ~5,000 → error; `warn_looks` ~100 → warning). Cost, storage, and statistical validity all scale with the number of grid points, not with whether the unit is minutes or days — a 30m cadence on a 4-hour experiment (8 looks) is healthier than 1d on a 2-year one (730 looks). A 1h hard floor would block that legitimate short experiment while permitting the pathological long one.
3. **Cadence < 1d without `sequential.enabled` is a loud, numbered warning — never a block.** Every competitor that offers sub-day readouts either makes always-valid inference the default (Optimizely, Amplitude, Eppo) or explicitly demotes hourly results to "setup debugging, not decisions" (Statsig). abkit's existing honesty machinery (`ci_kind`, `is_horizon`, pre-horizon WIN/LOSE refusal, measured peeking FPR) already carries the load — sub-day just multiplies the looks, so surface the *measured* FPR for the actual grid in the warning.
4. **Honesty for small intervals = a timestamp-grained completeness boundary + a `completeness_lag` config + a min-sample gate.** `data_complete_through` (cumulative-intervals.md §5.6) becomes a timestamp; a cutoff is only planned when `end_ts <= now() - completeness_lag`. Rows below a `min_group_size` threshold are written but flagged (Amplitude-style), never silently CI'd.
5. **Keep the warehouse agg-state seam at day grain; compute sub-day looks as "complete-day state + partial-tail scan".** This bounds each sub-day look at O(1-day scan) instead of exploding the O(D²) bill to O((24·D)²), and requires no change to the `_ab_unit_state` keying decided in cumulative-intervals.md §5.2–5.3.

---

## 2. Competitor scan

| Platform | Refresh cadence | Sub-day floor | Peeking posture / messaging |
|---|---|---|---|
| **GrowthBook** | Auto-update default **6h**, configurable in Settings; manual "Update" anytime ([FAQ](https://docs.growthbook.io/faq), [Experiment Results](https://docs.growthbook.io/app/experiment-results)) | No explicit floor documented; scheduling UI is coarse (hours). *Could not verify whether <1h is selectable.* | Bayesian engine default; frequentist engine documents the peeking problem bluntly ("inflated FPR far above your nominal 5%") and offers **opt-in sequential testing** (asymptotic confidence sequences, tuned by expected decision-time N*) ([Sequential Testing](https://docs.growthbook.io/statistics/sequential)) |
| **Statsig (Cloud)** | **Hourly for the first 24h, then daily** ([Read Pulse](https://docs.statsig.com/pulse/read-pulse/)) | Effective floor: hourly, and only for 24h | The hourly window is explicitly framed as *"confirm that exposures and metrics are being calculated as expected and debug your setup"*; *"You should not make any experiment decisions based on real-time results data in this first 24 hour window."* Sequential testing is an opt-in p-value/CI correction for early looks ([Sequential Testing update](https://www.statsig.com/updates/update/sequential-testing-capabilities)). *(Quotes via search snippets; page fetch 403.)* |
| **Statsig (Warehouse Native)** | **On-demand or scheduled daily** reloads (incremental recommended) ([Scheduled Reloads](https://docs.statsig.com/statsig-warehouse-native/connecting-your-warehouse/scheduled-reloads), [Loading Pulse](https://docs.statsig.com/statsig-warehouse-native/features/reloads)) | Effective floor: **daily** for the warehouse-native product | Cost-framed: incremental daily reloads "without using unnecessary compute" |
| **Eppo (now Datadog Experiments)** | **Nightly incremental** refresh default (48h lookback rescans); manual UI refresh ([Data Pipeline](https://docs.geteppo.com/data-management/data-pipeline/), [Experiment Schedule Settings](https://docs.geteppo.com/administration/experiment-schedule-settings/)) | Nightly default; per-experiment update schedule exists — *could not verify whether hourly is offered (403)* | **Sequential confidence sequences are the DEFAULT CI method** — "peek at results as many times as you want and stop at any time"; Fixed-sample and Bayesian are the alternatives ([Analysis Methods](https://docs.geteppo.com/statistics/confidence-intervals/analysis-methods/)). Update schedule is explicitly a **warehouse-cost** knob ([Warehouse Best Practices](https://docs.geteppo.com/data-management/warehouse-best-practices/)) |
| **Optimizely** | Continuous / near-real-time | None — "check whenever you want" | Real-time is only offered *because* Stats Engine is always-valid sequential + FDR control by design; marketing claims peeking with fixed-horizon stats yields ~**30% false winners vs 5%** ([Stats Engine](https://www.optimizely.com/statistics/), [Stats Engine story](https://www.optimizely.com/insights/blog/statistics-for-the-internet-age-the-story-behind-optimizelys-new-stats-engine/)) — vendor-reported figure, not independently verified |
| **Amplitude** | Real-time | None | Sequential testing default ("results are valid whenever you view them"), **but inference is withheld below minimum data thresholds**: ≥100 exposures/arm, ≥25 conversions before p-values/CIs appear ([Sequential testing](https://amplitude.com/docs/feature-experiment/under-the-hood/experiment-sequential-testing), [Experiment Analysis FAQ](https://amplitude.com/docs/faq/experiment-analysis)) |
| **VWO** | Real-time reports | None | Bayesian SmartStats with sequential correction "auto-adjusts for peeking"; fixed-horizon offered as an explicit alternative mode ([SmartStats](https://vwo.com/why-us/technology/statistics/), [SmartStats configurations](https://help.vwo.com/hc/en-us/articles/34052623554457)) |

**The pattern, distilled:**

- **Nobody imposes a hard floor as a *rejection*.** The floors that exist are implicit: coarse scheduling menus (GrowthBook), product-tier defaults (Statsig WHN daily), or managed pipelines. No platform exposes a free-form duration field and then errors below a threshold — abkit's free-form `cadence` is actually *more* expressive than any competitor's surface, which fits the dbt-style "you own the warehouse and the bill" positioning (the same framing Eppo/Statsig WHN use for their schedule knobs).
- **Sub-day readouts and sequential inference are a package deal everywhere.** Real-time platforms (Optimizely, Amplitude, VWO) made always-valid stats the *default and precondition* of real-time display. Warehouse-native platforms that default to fixed-horizon stay at daily/6h cadence. The one hybrid — Statsig's hourly-first-24h — is explicitly repositioned as **setup/SRM debugging, not decision data**. This is the strongest external validation of abkit's Q2 posture: if `cadence < 1d` and `ci_kind = fixed`, the product must say what Statsig says, in the analyst's face.
- **Early-hours volatility is handled by *withholding or demoting*, not by hiding the data**: Amplitude's min-sample thresholds; Statsig's "debug window" framing; Eppo's progress bar. None of them refuse to show raw counts early — they refuse to bless them with inference.

---

## 3. YAML / config surface (concrete proposal)

### 3.1 Field semantics

```yaml
start_date: 2024-07-31          # pinned left edge; with sub-day cadence, an optional
                                # time component is honored: 2024-07-31 14:00 (else 00:00, project tz)
end_date:   2024-08-27          # horizon stays IN DAYS — the planning/power concept is unchanged
cadence: 1h                     # expanding-window cutoff step; any interval.py duration; default 1d
completeness_lag: 2h            # NEW (optional): data assumed complete through now() - lag;
                                # default 1d-equivalent (yesterday) when cadence >= 1d — preserves
                                # the *_wo_curr_day convention exactly; required-or-warned when cadence < 1d
sequential: {enabled: true, scheme: always_valid}   # strongly recommended (not forced) when cadence < 1d
```

- **Grid:** `end_ts_k = start_ts + k·cadence`, `k = 1, 2, …` (pinned start, moving end — semantics unchanged). Horizon = last grid point ≤ `end_date + 1d`; `is_horizon` marks it.
- **Keep `horizon` in days, `cadence` free-form.** Don't invent "duration in looks" — the analyst thinks in "run 2 weeks, check hourly". Look count is *derived* and echoed (`≈336 looks`), never authored.
- **Recommend (warn, don't require) that `cadence` divides 24h evenly** ({1,2,3,4,6,8,12}h, {1,5,10,15,20,30}m…). A 7h cadence drifts across midnight: daily BI rollups misalign and diurnal composition oscillates across looks. Warning, not error — it's odd, not wrong.
- **Timezone must be pinned per experiment/project** once cadence < 1d. This sharpens the existing quorum must-fix (normalized tz comparison, cumulative-intervals.md §5.6) from "nice hygiene" to "load-bearing".

### 3.2 Contract & Jinja deltas (minimal, backward-compatible)

- `_ab_results`: key on **`end_ts` (DateTime)**; keep `end_date` as the derived date (BI compat); replace/augment integer `day` with `look_index` (k) and `day = k·cadence/1d` (float). Daily-cadence rows are bit-identical in meaning to today's contract.
- Jinja built-ins: add **`ab_end_ts`** / `ab_start_ts` alongside `ab_start_date`/`ab_end_date` (which stay, resolved as the date parts). The packaged `ab.exposed_units()` macro filters `event_time BETWEEN ab_start_ts AND ab_end_ts` when cadence < 1d, plus the existing `event_date` partition-pruning predicate — metric SQL authors change **nothing**.
- **CUPED:** the legacy *growing* covariate lookback (cumulative-intervals.md §1 warning, §5.1) becomes incoherent at sub-day grain (fractional `agg_dates_count`, covariate moments jittering hourly). Sub-day cadence effectively **forces decision (b), fixed `covariate_lookback`** — worth stating in statistics-changes.md §5 as an argument that tips the pending decision.

### 3.3 Validation matrix additions (`config/validator.py`)

| Rule | Level | Trigger |
|---|---|---|
| `cadence` parses to > 0 | error | always |
| planned looks = ceil(horizon/cadence) > `max_looks` (project default ~5,000) | **error** | the only hard gate — bounds `_ab_results` rows, planner anti-join size, and A/A grid cost |
| planned looks > `warn_looks` (default ~100) **and** `sequential.enabled: false` | warning | the peeking warning, with the measured FPR if `_ab_aa_runs` has one for this grid |
| `cadence < 1d` and `completeness_lag` unset | warning | "intraday data is assumed complete through now()−2h (default); set `completeness_lag` to your ingestion SLA" |
| `cadence < completeness_lag` | warning | "looks will trail real time by ≥ lag; points appear in batches" (planner handles this correctly anyway — it just batches) |
| `24h % cadence != 0` | warning | grid drifts across midnight |
| `cadence < 1d` and correction `benjamini_hochberg` | info | composed FDR×peeking must be validated empirically (existing aa-matrix §3 rule, now more acute) |

### 3.4 Compute strategy for sub-day (ties to cumulative-intervals.md §4)

The naive bill goes from O(D²) to O((D·k)²) — hourly over 28 days is ~576× the daily row-touches. Do **not** re-key `_ab_unit_state` to hour grain (×24 state storage for every co-located metric, and §5.2 idempotency gets 24× more delicate). Instead:

- **Hybrid read:** cumulative window at `end_ts` = (sum of day-grain agg states through the last complete midnight) + (direct fact-scan of the partial tail `[last_midnight, end_ts]`). Each sub-day look costs one ≤1-day scan — linear, not quadratic, in looks-per-day.
- The planner anti-join and LWW idempotency work unchanged on `end_ts`.
- **A/A peeking FPR over a 336-point grid × N iterations is the real cost bomb** (aa-false-positive-matrix.md §6). Recommendation: `validate` subsamples the look grid (cap ~100 points, denser early where the FPR accrues fastest) and states that it did. Otherwise sub-day cadence makes `validate` — the trust artifact that sub-day *most needs* — the thing analysts skip.

---

## 4. Cockpit & readout for sub-day grids

- **X-axis: elapsed time since start, not calendar dates.** Auto-unit: hours while total elapsed < 3d, days after; day boundaries stay as gridlines (hourly points cycle diurnally — the day banding makes the oscillation legible as *time-of-day composition*, not "the effect is swinging").
- **Look counter chip** (new, pinned): `look 37 / ~336 planned`. Next to the existing A/A calibration chip: with `ci_kind=fixed`, the chip reads *"fixed-horizon CIs, 336 looks — measured peeking FPR ≈ X% (nominal α 5%)"*, red when out of `aa_fpr_budget`. This is the aa-matrix §3 headline number made cadence-aware.
- **"Not peeking-valid" treatment** (data-contract §4) needs to *strengthen* with density: pre-horizon fixed CIs rendered dashed/de-emphasized; the sequential band, when enabled, is the solid decision surface. At hourly density a solid fixed-CI band crossing zero 40 times *looks like* information; muting it is the honest rendering.
- **Early-look demotion, Amplitude-style:** below `min_group_size` (project default, e.g. 100/arm) write the row but suppress the CI band and verdict chips, show counts + SRM only. The first hours of an hourly grid are then what Statsig says they are: an exposure/SRM/pipeline debugging view — which is the *actual* value impatient experimenters get from hour-grain (catching SRM, broken assignment, guardrail crashes at hour 3 instead of day 2). Sell sub-day cadence as *that*, honestly, and it's a genuinely differentiated feature since no warehouse-native competitor offers hour-grain SRM.
- **Readout:** the existing rule already holds — pre-horizon WIN/LOSE refused unless sequential; verdict logic keys on `look_index`/`is_horizon`, unchanged. Stabilization ("stopped crossing zero over recent days") should be defined over *elapsed time*, not look count, or hourly grids will "stabilize" in 6 looks = 6 hours.

### Warning copy (written to be heeded, not clicked past)

Config-lint, hourly cadence without sequential:

> `WARN [peeking]: cadence 1h × 14d horizon = 336 looks with fixed-horizon CIs.`
> `A/A measurement of this exact grid: a true-null experiment shows a false winner ~X% of the time (nominal α 5%).`
> `You will reach a *trustworthy* early decision sooner with: sequential: {enabled: true} — CIs stay valid at every look and the readout can call WIN before the horizon.`
> `Without it, WIN/LOSE is refused until 2024-08-27 regardless of what the chart shows. Proceeding.`

The lever that works on an impatient analyst is the middle line: sequential is framed as **the thing that lets them decide earlier**, not as a statistical tax. That is exactly Optimizely's/Eppo's pitch, and it's true.

Sub-hour cadence, additional line:

> `NOTE [sub-hour]: cadence 15m assumes exposure and event timestamps are complete and ordered to the minute at read time. Most warehouse ingestion is not. completeness_lag=2h means each look reflects data ≥2h old — you are adding chart resolution, not freshness. Not blocked.`

---

## 5. The floor decision

**Case for a hard floor at 1h:** every look below the ingestion SLA is pure cost with zero information gain; sub-hour grids multiply `_ab_results` rows, planner anti-join width, and A/A cost; support burden skews toward "my 5-minute points look insane" tickets; no competitor ships sub-hour warehouse analysis, so there's no practice to lean on; a floor is one `if` today versus deprecation pain later if a floor proves needed.

**Case for no floor (recommended):** (1) The dangerous variable is **look count, not the time unit** — a 1h floor blocks a legitimate 4-hour ops experiment at 30m cadence (8 looks, statistically pristine) while happily permitting 730 daily looks; a `max_looks` bound is the *correct* invariant and catches both. (2) The system **self-throttles honestly anyway**: the timestamp completeness boundary means a 5m cadence with a 2h lag just emits points in batched arrears — wasteful-ish, never *wrong*; that's a warning-shaped problem. (3) Legitimate sub-hour uses exist at the margins: seed/demo datasets (`abk init` first-run), integration tests of the planner itself, streaming-ingestion ClickHouse shops with minute-level completeness, short-horizon infra/pricing experiments. (4) It matches the product's DNA — dbt-style tools expose free-form scheduling and let the warehouse bill discipline the user; the config surface already leads with declared-and-inspectable over hidden-and-enforced (declarative-config §6). (5) It honors the user instinct ("maybe we should NOT impose a hard floor at all") without abandoning safety, because the safety was never going to come from a duration check — it comes from `ci_kind`, the measured peeking FPR, the completeness boundary, and the min-sample gate.

**Recommendation: no cadence floor.** Hard-gate on `max_looks` (error, project-overridable), soft-gate everything else: the sequential warning at cadence < 1d, the completeness/ingestion note at cadence < 1h, `warn_looks` at >100 looks without sequential. Default stays `1d`; docs and `init` scaffolding show `6h`/`1h` as the sanctioned impatience path *with* `sequential.enabled: true` in the same example — the example config is itself the messaging.

---

## 6. Claims I could not fully verify

- Statsig hourly-first-24h behavior and its "do not make decisions" wording: from search snippets of [docs.statsig.com/pulse/read-pulse/](https://docs.statsig.com/pulse/read-pulse/); page fetch 403.
- Eppo per-experiment update-schedule granularity (whether anything finer than nightly is selectable): [Experiment Schedule Settings](https://docs.geteppo.com/administration/experiment-schedule-settings/) exists but was unfetchable (403); "nightly incremental, 48h lookback" is snippet-confirmed.
- GrowthBook's auto-update frequency *option set* (whether <1h is selectable): default 6h confirmed via [FAQ](https://docs.growthbook.io/faq) snippets and a [community thread](https://linen.growthbook.io/t/29148772/hi-i-was-able-to-succesfully-setup-an-experiment-a-b-test-un); the full dropdown contents unverified.
- Optimizely's "30% → 5%" false-winner figure is vendor marketing ([Stats Engine](https://www.optimizely.com/statistics/)), not an independent measurement — consistent in magnitude with the peeking literature, but treat as directional.
- Amplitude thresholds (100 exposures / 25 conversions) from [docs FAQ](https://amplitude.com/docs/faq/experiment-analysis) snippets; exact current values may have drifted.
- "No warehouse-native competitor offers hour-grain SRM" is an inference from the cadence table above, not an exhaustively verified negative.

Spec files this report is grounded in: `/home/user/ab-analysis-kit/docs/specs/cumulative-intervals.md`, `/home/user/ab-analysis-kit/docs/specs/declarative-config.md`, `/home/user/ab-analysis-kit/docs/specs/data-contract-and-reporting.md`, `/home/user/ab-analysis-kit/docs/specs/aa-false-positive-matrix.md`, `/home/user/ab-analysis-kit/docs/specs/architecture.md`, `/home/user/ab-analysis-kit/docs/specs/cli-and-dx.md`, `/home/user/ab-analysis-kit/ROADMAP.md`.