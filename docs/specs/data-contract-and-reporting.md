# Data contract & reporting

> **Greenfield.** Per the founding decision, the legacy `marts.exp_comparison_results`
> schema and storage internals are **not** carried over. The legacy Grafana
> dashboard is a **reference only** — it tells us *what an analyst needs to see and
> how they decide*. We design a clean, BI-friendly contract from scratch that
> *supports that decision logic*, owing nothing to the legacy table layout.

## 1. The decision logic we must support (from the legacy dashboard)

A winner is called on a single series keyed by `(experiment, metric, variant-pair,
method, params)` tracked over `end_date`, where every point is **cumulative** from
`start_date`. Three signals, read together:

1. **Significance** — at the latest `end_date`, the (1−α) CI `[left_bound,
   right_bound]` excludes zero (≡ `pvalue < alpha` ≡ `reject = 1`); the sign of
   `effect` says who wins.
2. **Stabilization** — significance must be **persistent**: the CI narrows as
   sample size grows and has stopped crossing zero over recent days; a CI that
   excludes zero only briefly then re-crosses is **not** a winner. (Early-experiment
   volatility is distrusted — this is the daily-peeking surface, see §4.)
3. **Power / MDE** — if the CI includes zero **and** `mde` is still larger than the
   business-meaningful effect ⇒ **underpowered ⇒ INCONCLUSIVE** (keep running), not
   "no effect". A confident **FLAT** call needs the CI to include zero **while**
   `mde` has dropped below the meaningful effect.

**Verdict (`readout.py`):** WIN/LOSE when CI excludes zero in a direction **and**
stabilized; FLAT when CI includes zero **and** adequately powered **and** stable;
INCONCLUSIVE otherwise. The main metric drives the verdict; guardrails are checked
for regression; **SRM is a hard gate**; **pre-horizon WIN/LOSE is refused unless
`sequential.enabled`** (decision Q2).

*The exact rules (amended with M3 WP1 — m3-implementation-plan.md D5; the
verdict is READ-TIME only, recomputed at render, never persisted):*

- **Stabilization** = over the trailing `readout.stabilization_days` (default
  **7** elapsed-days — one weekly cycle; widened to the last 3 informative
  cutoffs when the cadence is coarser), every informative cutoff's CI excludes
  zero with one consistent sign (WIN/LOSE) or includes zero (FLAT). Judged
  strictly over `elapsed_days`, never look count (§4). Demoted
  (`insufficient_data`) and degenerate (NULL-bound) rows are gaps, never zeros.
- **FLAT** additionally needs `comparisons[].min_effect` (in the units of that
  comparison's persisted `effect`) and pair MDE ≤ `min_effect` at the latest
  cutoff. NULL `mde_1/2` fall back to a read-time `stats/power.py` solve for
  t-test/z-test rows (the z-test `nobs` inverted from the persisted SE);
  methods without MDE capability leave FLAT honestly unreachable, with the
  rationale saying so. Without `min_effect`, FLAT is unreachable by
  construction ("cannot distinguish flat from underpowered").
- **Guardrail regression** = the guardrail's CI excludes zero against its
  `desired_direction` at the stored per-row alpha at its latest informative
  cutoff (no stabilization requirement — any significant harm flags).
  Consequence is `readout.guardrail_policy` (owner-ratified): `block`
  (default) caps WIN at INCONCLUSIVE; `warn` keeps WIN with a mandatory loud
  caveat. LOSE is never upgraded or blocked.
- **Pre-horizon** (rows carry `ci_kind="fixed"` until M5): WIN/LOSE **and
  FLAT** are withheld before `is_horizon` — FLAT is equally a stop decision.
- **Multi-arm**: one verdict per (main metric × control-vs-treatment pair);
  no invented scalar aggregate.
- **Benjamini-Hochberg** (`correction: benjamini_hochberg`) is applied at
  read time by `readout.py`, per cutoff across the experiment's comparisons —
  compute-time rows deliberately carry the raw alpha.
- Verdicts covering under 7 elapsed days carry the "covers X% of a weekly
  cycle" representativeness caveat (§4).

## 2. The results contract (`_ab_results`) — clean & BI-first

One row per `(experiment, metric, variant-pair, method, end_date)`. Designed so any
BI tool can build the chart + tables above with plain SQL. Columns (greenfield —
names/types are ours to choose; this is the proposed v1 contract):

| Group | Columns |
|---|---|
| identity | `experiment`, `metric`, `is_main_metric`, `is_guardrail`, `method_name`, `method_params` (canonical JSON), `method_config_id`, `name_1`, `name_2` |
| window | `start_ts`/`end_ts` (UTC DateTimes; `end_ts` **exclusive** — the canonical cutoff key), `start_date`/`end_date` (derived Dates — legacy-identical at `cadence: 1d`), `window_seconds`, `elapsed_days` (fractional; the chart x-axis) — see cumulative-intervals.md §6.3 |
| per-arm | `value_1/2`, `std_1/2`, `cov_value_1/2`, `size_1/2` |
| test | `alpha` (effective, post-correction), `pvalue`, `effect`, `left_bound`, `right_bound`, `ci_length`, `reject`, `mde_1/2` |
| integrity | `srm_flag`, `srm_pvalue`, `decision_blocked`, `insufficient_data` (small-n demotion: row written, inference withheld) |
| sequence | `ci_kind` (`fixed` \| `always_valid`), `is_horizon` (this cutoff == planned horizon) |
| diagnostics | `warnings` (canonical-JSON array, nullable), `diagnostics` (canonical-JSON object, nullable) — the row's human-readable failure/context signal (θ, boot diagnostics, H5 zero-denominator explanations) routed from the stats core instead of stderr. *(Added in M2 WP3; amends the original proposal which had no home for `TestResult.warnings`/`diagnostics`.)* |
| provenance | `metric_query`, `metric_rendered_query`, `watermark_ts` (completeness boundary in force), `created_at` (strictly-monotonic LWW version) |

Notes:
- `avg_group_size = (size_1 + size_2)/2` and the `zero_effect = 0` reference line
  are **derived in the BI query**, not stored (as the legacy dashboard did).
- `method_params` is written via the single `json_dumps_sorted` path so exact-string
  BI filters never split a series.
- `metric_description` is **not** stored in `_ab_results`; it lives in
  `_ab_experiments`/metric metadata and is joined by BI (one source of truth).
  *(Amended with M3 WP2 — D6: the report payload sources metric descriptions
  from the metric YAML configs, `MetricConfig.description` — `_ab_experiments`
  stores only the experiment description; see §5.3.)*
- New, non-legacy columns (`srm_flag`, `ci_kind`, `is_horizon`, `decision_blocked`)
  are first-class because we are not constrained by the old schema.

## 3. BI-agnostic by design (connect any tool)

abkit **owns the correct numbers, not the dashboard.** `_ab_results` is a stable,
documented warehouse table that analysts point **their own BI** at:

- **Grafana, Lightdash, Metabase, Superset** — all can render the four core views
  from this one table: (1) effect + CI band vs zero + avg_group_size (the primary
  winner chart); (2) raw values + std + CUPED; (3) MDE + sizes (power); (4)
  p-value vs alpha. Plus a results/audit table and a cross-experiment summary.
- We ship **reference queries** (and example dashboards) for each tool in
  `docs/examples/bi/` so a team is productive in minutes — but the contract, not
  any one dashboard, is the deliverable. A team can also keep their existing
  Grafana by repointing it at `_ab_results` and adjusting column names (the legacy
  dashboard JSON in `docs/reference/` documents what each panel needed).
- An **optional SRM panel** snippet is shipped, because the canonical SRM gate is
  the CLI / HTML report (BI dashboards won't necessarily surface `srm_flag`).

## 4. Honest peeking (decision Q2) reflected in the contract

The daily cumulative chart inherently peeks. The contract makes this **visible**:

- `ci_kind` distinguishes `fixed` (legacy parity, default) from `always_valid`
  (sequential, opt-in). BI renders fixed-horizon CIs with a "not peeking-valid"
  visual treatment.
- `is_horizon` lets the readout/BI refuse a WIN/LOSE before the planned horizon
  under fixed-horizon mode.
- `abk validate` measures the **real cumulative-peeking FPR** (running A/A through
  the experiment's ACTUAL cadence grid and the readout rule) and surfaces it next
  to the chart — so an analyst sees the true error rate of watching at their
  chosen frequency, not just nominal α.
  ([aa-false-positive-matrix.md](aa-false-positive-matrix.md))
- **Sub-day grids** (cumulative-intervals.md §6): the explore cockpit adds a
  pinned look counter ("look 37 / ~336 planned") next to the calibration chip;
  pre-horizon fixed CIs render dashed/de-emphasized (at hourly density a solid
  band crossing zero 40 times *looks like* information); `insufficient_data`
  segments are greyed with counts+SRM only; a WIN/LOSE called before
  `min(7d, horizon)` — even under sequential — carries a "covers X% of a weekly
  cycle" representativeness caveat; stabilization is judged over *elapsed time*,
  never look count (or hourly grids would "stabilize" in 6 hours).

## 5. Reporting — the priority local interface

Two surfaces, both **web-first and framework-free** (baked payload + self-contained
JS renderer, the detectkit `report.js`/`tune.js` pattern) so they can later be
embedded in a full app.

### 5.1 `abk explore` — the chart-first cockpit (PRIORITY)

The detectkit-`tune` port and the **first thing we build**. A localhost cockpit
where the analyst **plays with method params live** over the experiment's
persisted results.

*Data source (amended with M3 WP2 — m3-implementation-plan.md D2): explore
reads the **persisted** `_ab_results` series for the baseline chart (the
donor's "what actually ran" stance) and performs exactly one read-only,
lock-free warehouse load pass at session start to fill the recompute cache —
it never runs the pipeline in-cockpit. A prior `abk run` is required; no rows
⇒ a friendly "run `abk run --select <exp>` first" noop. Freshness is whatever
the last run produced; the header shows the latest `end_ts`/watermark so
staleness is visible.*

- **Windshield:** the cumulative-effect + CI **stabilization chart** (effect with
  its shrinking CI as time accrues), with pinned live chips — estimated lift, CI
  half-width, p-value, current power, **A/A calibration (real α)**, and the **SRM
  flag**.
- **Side rail (mode-aware, Basic/Advanced disclosure):** `test_type`, alpha,
  CUPED on/off + covariate + lookback, stratification keys, bootstrap
  iters, correction, analysis unit (preview-only). Every change live-recomputes via
  the **Python `from_suffstats` path** (one source of truth for the math; no JS
  stats fork, no DB round-trip). *(Amended per m3-implementation-plan.md D12:
  sidedness and winsorization are struck from the M3 rail — neither exists in
  the shipped stats core (p-values are hardcoded two-sided; no winsor param),
  and the rail is auto-derived from `param_specs`, so faking either would
  special-case UI against math that isn't there. Both are queued behind M4
  change control — see ROADMAP.)*
- **Calibration is always visible (must-fix):** the A/A real-α chip is in the
  cockpit, not a separate command; **Apply is gated/confirmed** when the active
  params have not passed `validate`, so an analyst can't ship a mis-calibrated
  method without seeing the FPR cost.
- **Modes:** Tune / Auto (run `validate` server-side, re-seed knobs) / Segment
  (heterogeneous effects) / Review (mark guardrail vs primary, confirm the
  decision). **Apply** validates, archives the prior YAML to
  `experiments/.history/<exp>/`, writes `method_params` back. `--no-serve` emits a
  static read-only HTML.

### 5.2 `abk run --report` — the self-contained readout

A single offline HTML per experiment (inline JS + baked payload): variant
means/lift, the stabilization chart, MDE/power, p-value-vs-alpha, the SRM panel,
the A/A matrix, and the WIN/LOSE/FLAT/INCONCLUSIVE verdict with its rationale.
Shareable with stakeholders without standing up BI.

### 5.3 The baked payload contract (added with M3 WP2 — m3-implementation-plan.md D6)

One versioned, JSON-serializable payload per **experiment** — produced by
`abkit/reporting/builder.py` (`build_report_payload`), consumed by both the
readout renderer and the explore shell. The Python builder and the renderer-side
`web/src/shared/payload.ts` (M3 WP3) are kept in **documented lockstep** — same
keys, same units (the donor's `payload.ts` discipline). Explore extends the
payload with `param_specs`/tier-map/seed blocks; the report ignores unknown keys.

Units and null discipline: timestamps are integer **ms-epoch (UTC)**; every
nullable numeric maps NaN **and ±inf** to JSON `null` (H5 zero-denominator NaNs;
`pair_mde`'s `math.inf` "configured but unavailable" — the rationale strings
carry the explanation). The payload derives from **stored** rows (persisted
`method_params`/`alpha`/`srm_flag`), never from re-evaluating the current YAML;
header metadata comes from the experiment config (the truth) — `_ab_experiments`
is informational only. Every `<` is escaped (`\u003c`) at HTML-bake time
(WP3 — `</`-only escaping would leave the tokenizer's `<!--`+`<script`
double-escape hazard open), not here.

```
{
  v: 1,                        // schema version; bump on breaking key/unit changes
  experiment, project|null, generated_at|null,   // generated_at: caller-supplied string
  description|null,            // the experiment description (config)
  period: {start, end, horizon},  // ms; end=0 = no persisted cutoffs (empty sentinel);
                                  // start/horizon are grid facts, always real
  cadence_seconds,             // min cadence step (sub-day detection: < 86400)
  tz,                          // experiment timezone (IANA)
  arms: [..],                  // variant names, config order; first = control
  srm: {flag, pvalue|null,     // CURRENT experiment health, window-INDEPENDENT
                               // (§6 "SRM loud"): flag/pvalue from the latest
                               // persisted row overall (not the latest charted
                               // row), so a pinned/empty replay never silences
                               // a failing gate
        observed: {arm: count},   // WHOLE-cohort exposure counts, declared arms
                                  // zero-filled — coherent with the whole-run
                                  // flag/pvalue (M2 SRM is one whole-cohort
                                  // check; per-cutoff SRM = M5 sequential)
        expected: {arm: split}},
  calibration: null,           // M3: always null. M4 shape (no v-bump): {fpr,
                               // peeking_fpr, headline, matrix_rows, report_link}
  verdicts: [{metric, pair:{c,t}, verdict, rationale:[..], caveats:[..],
              significant, effect, pvalue, lo, hi, alpha, mde, min_effect,
              end_ts|null, elapsed_days, is_horizon,
              guardrails:[{metric, pair, regressed, effect, desired_direction}]}],
                               // one per main-metric × control-vs-treatment pair,
                               // the readout.evaluate output verbatim (JSON-safe)
  metrics: [{name, description|null,   // description from the METRIC YAML (D6)
             main, guardrail,
             method: {name, params, id, alpha|null},  // params: parsed canonical
                               // JSON from the latest stored row (config fallback);
                               // alpha: latest stored row alpha — what actually ran
             query|null,       // metric_query deduped to ONE entry per metric;
                               // metric_rendered_query NEVER enters the payload
             pairs: [{c, t,    // all combinations(arms, 2), config order, always
                               // present (series may be empty)
                      series: [{t, ed, e, lo, hi, p, rj, s1, s2,
                                v1, v2, sd1, sd2, cv1, cv2, mde, hz, blk, ins}],
                               // terse point keys: t ms-epoch; ed elapsed_days
                               // (float, chart x-axis); e/lo/hi effect + CI;
                               // p pvalue; rj reject 1/0/null (null = withheld);
                               // s1/s2 sizes; v1/v2 + sd1/sd2 per-arm stored
                               // value/std, cv1/cv2 CUPED covariate means (null
                               // unless CUPED) — added with WP3 (additive, no
                               // v-bump) for the §5.2 variant-means/lift and
                               // §3 view-2 renderings; mde = per-point pair MDE
                               // from the STORED mde_1/2 columns (null when the
                               // row did not compute MDE — no per-point
                               // read-time solve; the read-time D5(b) fallback
                               // is verdict-level); hz/blk/ins 0/1 flags
                               // (is_horizon / decision_blocked /
                               // insufficient_data)
                      diag|null}],  // parsed diagnostics of the latest row
             warnings: [..]}], // row warnings, parsed + deduped, order-preserving
  look: {n, planned}|null,     // n = cutoffs with ≥1 non-demoted row (§4);
                               // planned = the one-enumeration planner grid length
  endpoints: {save_url, recompute_url, reload_url, validate_url},  // all null in a
                               // baked report; the explore server injects at serve
  warnings: [..]               // readout warnings + builder warnings (e.g. the
                               // point-budget clip — no silent caps)
}
```

`start`/`end` args bound the window on `end_ts` (inclusive); pinning `end`
**replays** the chart + verdict as-of a historical cutoff. The `srm` block is
the one exception — it is **window-independent** (current experiment health):
flag/pvalue come from the latest persisted row overall and `observed` is the
whole cohort, so the loud §6 gate never goes silent or incoherent under a
pinned or empty window (M2 stores only the current cohort and runs one
whole-cohort SRM check; a truthful as-of SRM needs the M5 per-cutoff
sequential gate). A global point budget (`REPORT_POINT_BUDGET`,
20 000 across metrics × pairs × cutoffs) clips every series to its trailing
window **after** the verdict is evaluated on the full series, and appends a
loud payload warning. The empty-experiment contract keeps every key present
with the same shapes (empty series, zero-filled `observed`, `period.end = 0`)
— the renderer never branches on key presence. On a never-run project (no
`_ab_results` table) the builder skips all reads and returns the empty shape;
reporting never creates schema.

Consistency rules (no silent drops): rows for variant pairs outside the
declared `arms` (a mid-flight rename) are excluded from **every** payload
surface — series, verdict input, `look`, `period.end`, the latest-row method
block — with a loud warning; orphaned `method_config_id` series detected by
the driver's scan are surfaced as a payload warning too (`run \`abk clean\``),
so a report over an edited method never shows a silently truncated history.

## 6. SRM surfacing (must-fix)

SRM is the safest A/B guardrail and must be **loud** where analysts actually look:

- **CLI:** the `run` `StageLogRenderer` prints a red line — `SRM FAILED (observed
  0.62/0.38 vs expected 0.50/0.50, χ² p<0.001) — effects untrustworthy`.
- **HTML report & explore:** a red SRM gate chip; `decision_blocked` set.
- **BI:** ship an optional panel; document that a plain dashboard won't show
  `srm_flag` and that the CLI/HTML report is the canonical gate surface.
- **Sub-day cadence:** checking χ² at every cutoff is itself peeking on the SRM
  test (false alarms on a hard gate are expensive), so at `cadence < 1d` the
  gate switches to the **anytime-valid sequential multinomial test**
  (Lindon & Malek, NeurIPS 2022) — valid at every look by construction. This is
  also sub-day cadence's headline genuine payoff: catching assignment bugs
  within hours instead of days.

## 7. Trajectory to a full app

The explore cockpit + framework-free renderer + BI-agnostic contract are
deliberately **app-seed-shaped**: the same pieces compose into a future product
integrating agentic analysis, detectkit + abkit, and an embedded open-source BI
(Lightdash). Implication for this spec: keep the renderer/payload split clean and
dependency-free, and keep `_ab_results` BI-first and stable.
