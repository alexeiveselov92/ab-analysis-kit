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
| provenance | `metric_query`, `metric_rendered_query`, `watermark_ts` (completeness boundary in force), `created_at` (strictly-monotonic LWW version) |

Notes:
- `avg_group_size = (size_1 + size_2)/2` and the `zero_effect = 0` reference line
  are **derived in the BI query**, not stored (as the legacy dashboard did).
- `method_params` is written via the single `json_dumps_sorted` path so exact-string
  BI filters never split a series.
- `metric_description` is **not** stored in `_ab_results`; it lives in
  `_ab_experiments`/metric metadata and is joined by BI (one source of truth).
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

### `abk explore` — the chart-first cockpit (PRIORITY)

The detectkit-`tune` port and the **first thing we build**. A localhost cockpit
where the analyst runs the pipeline and **plays with method params live**:

- **Windshield:** the cumulative-effect + CI **stabilization chart** (effect with
  its shrinking CI as time accrues), with pinned live chips — estimated lift, CI
  half-width, p-value, current power, **A/A calibration (real α)**, and the **SRM
  flag**.
- **Side rail (mode-aware, Basic/Advanced disclosure):** `test_type`, alpha +
  one/two-sided, CUPED on/off + covariate + lookback, stratification keys, bootstrap
  iters, correction, winsorization, analysis unit. Every change live-recomputes via
  the **Python `from_suffstats` path** (one source of truth for the math; no JS
  stats fork, no DB round-trip).
- **Calibration is always visible (must-fix):** the A/A real-α chip is in the
  cockpit, not a separate command; **Apply is gated/confirmed** when the active
  params have not passed `validate`, so an analyst can't ship a mis-calibrated
  method without seeing the FPR cost.
- **Modes:** Tune / Auto (run `validate` server-side, re-seed knobs) / Segment
  (heterogeneous effects) / Review (mark guardrail vs primary, confirm the
  decision). **Apply** validates, archives the prior YAML to
  `experiments/.history/<exp>/`, writes `method_params` back. `--no-serve` emits a
  static read-only HTML.

### `abk run --report` — the self-contained readout

A single offline HTML per experiment (inline JS + baked payload): variant
means/lift, the stabilization chart, MDE/power, p-value-vs-alpha, the SRM panel,
the A/A matrix, and the WIN/LOSE/FLAT/INCONCLUSIVE verdict with its rationale.
Shareable with stakeholders without standing up BI.

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
