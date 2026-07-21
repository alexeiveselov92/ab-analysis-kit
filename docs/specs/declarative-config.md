# Declarative config (YAML + SQL)

> The dbt/detectkit-style declarative model. Goal: an analyst defines an
> experiment and its metrics **without touching Python**, the way detectkit users
> define metrics. Everything correctness-critical (cohort join, window filter,
> per-unit dedup, alpha) is **packaged**, never hand-repeated.

## 1. Three config objects

| Object | File | Role |
|---|---|---|
| **Experiment** | `experiments/<name>.yml` | **Primary entity.** Assignment source, variants, unit key, the list of comparisons (metric × method) |
| **Metric** | `metrics/<name>.yml` (+ inline or `sql/`) | **Reusable library item** referenced by experiments by name |
| **Method** | inline in a comparison | The tunable statistical object; identified by `method_config_id` |

Globally-unique names per namespace are DB keys (validator-enforced).

## 2. Experiment YAML

```yaml
# experiments/dating_intro_v2.yml — THE PRIMARY ENTITY
name: dating_intro_v2            # globally unique (DB key); legacy exp_id
description: "Onboarding redesign for the dating intro funnel"
status: running                  # design | running | concluded | archived
is_actual: true                  # scheduled (Prefect) runs pick it up
start_date: 2024-07-31           # PINNED left edge of every cumulative window
end_date:   2024-08-27           # planner horizon (also drives the power plan)
unit_key: user_id                # randomization + default analysis unit
cadence: 1d                      # cumulative cutoff step — any duration ("1h", "30m", "1d");
                                 # or a coarsening schedule (dense-early, the sanctioned
                                 # impatience path — see cumulative-intervals.md §6):
                                 #   cadence:
                                 #     - {every: 1h, until: 48h}
                                 #     - {every: 1d}
data_lag: 0                      # completeness watermark: data assumed complete through
                                 # now() - data_lag. REQUIRED when cadence < 1d (declare your
                                 # ingestion SLA); default 0 reproduces *_wo_curr_day at 1d
timezone: UTC                    # interprets date-typed fields & daily midnight snapping;
                                 # storage/comparison is always UTC

assignment:                      # READ-ONLY exposure source (abkit does not randomize)
  query_file: sql/assignment.sql # must SELECT unit_key, variant, exposure_ts [, stratum]
  added_filters: ""              # optional extra SQL fragment (must start with AND); escape hatch
  cohort_copy:                   # opt-in persisted cohort copy (M8; named at WP1 — a field named
                                 # `copy` shadows pydantic's BaseModel.copy). Default off: metric
                                 # SQL joins the deduped assignment source directly and nothing is
                                 # persisted. Enable for a heavy multi-join source that is
                                 # APPEND-ONLY and monotone on update_column. KNOWN LIMITATION
                                 # (donor watermark model, m8 §4 Q3): a row backfilled/corrected
                                 # BELOW the watermark is silently and permanently missed by the
                                 # copy — a mutating source should stay on the no-copy default, or
                                 # recover with `abk run --resync-cohort` (full delete + reinsert).
                                 # When enabled, the assignment SQL MUST reference
                                 # {{ ab_added_filters }} — the incremental engine injects its
                                 # watermark batch bounds there (config-lint enforces it). Keep
                                 # data_lag >= maturity_delay + batch_interval, or the newest
                                 # cutoffs compute over a copy that does not yet cover them
                                 # (`abk run` warns when that happens).
    enabled: false               # true → persist into _ab_exposures incrementally (watermark +
                                 # closed-interval batches, the detectkit donor discipline; M8 WP5)
    update_column: exposure_ts   # watermark column the incremental copy filters on (must be a
                                 # plain identifier; existence is probed at run time). Only the
                                 # default exposure_ts carries a persisted resume cursor; a custom
                                 # column re-scans from the experiment start every run (still
                                 # batched, closed-intervals-only on that column)
    batch_interval: 1d           # closed-interval batch step of the copy loop
    batch_intervals_per_round_trip: 30   # intervals per load round trip (interval count, not rows)
    maturity_delay: 0            # ignore source rows younger than now() - maturity_delay (0 = none)
  variants: [control, treatment] # name_1 = first = control; name_2 = treatment
  expected_split: {control: 0.5, treatment: 0.5}   # drives the SRM chi-square gate

alpha: 0.05                      # experiment-level significance (see §6 — inspectable)
correction: bonferroni           # none | bonferroni (config-time, legacy) | benjamini_hochberg (read-time)
sequential: {enabled: false, scheme: always_valid}   # opt-in peeking-correct CIs (default off = legacy)

readout:                         # READ-TIME verdict knobs (M3, plan D5) — never enter method_config_id
  stabilization_days: 7          # trailing elapsed-days window for "persistent significance"
                                 # (judged over elapsed time, never look count; default 7 = one weekly cycle)
  guardrail_policy: block        # block (default): a regressed guardrail caps WIN at INCONCLUSIVE;
                                 # warn: WIN is kept with a mandatory loud caveat (owner-ratified)

comparisons:                     # each binds a library metric to a method
  - metric: social_r1            # references metrics/social_r1.yml by name
    is_main_metric: true         # primary winner criterion (drives the two-tier Bonferroni)
    min_effect: 0.01             # optional: the business-meaningful effect, in the units of this
                                 # comparison's persisted effect (test_type-dependent); enables FLAT —
                                 # without it flat cannot be distinguished from underpowered (D5(b))
    method: {name: z-test, params: {test_type: relative, calculate_mde: true, power: 0.8}}
  - metric: arpu
    method: {name: cuped-t-test, params: {test_type: relative, covariate: prev_gross_usd, covariate_lookback: 14d}}
  - metric: avg_session_time
    method: {name: poisson-bootstrap, params: {test_type: relative, n_samples: 1000, stratify_by: [country]}}
  - metric: bottle_cr
    is_guardrail: true           # checked for regression, not for winning
    desired_direction: increase  # which effect sign is GOOD for this metric (default increase);
                                 # orients WIN/LOSE for mains and the regression check for guardrails
    method: {name: z-test, params: {test_type: relative}}
```

## 3. Metric YAML

```yaml
# metrics/arpu.yml — reusable, referenced by experiments
name: arpu                       # globally unique (DB key)
description: "Average revenue per user"
type: sample                     # fraction | sample | ratio
unit_key: user_id                # must match (or be inherited from) the experiment
tags: [revenue, guardrail]       # selectors apply 1:1 (name / path glob / tag:)
columns:                         # column-role mapping
  variant: group                 # arm label column
  value:   gross_usd             # per-unit value (type=sample)
  covariate: prev_gross_usd      # optional CUPED covariate
  stratum: country               # optional stratification key
# fraction-type → columns: {variant, count, nobs}
# ratio-type    → columns: {variant, numerator, denominator}

sql: |
  {% import 'abkit_assignment.jinja' as ab %}
  SELECT
      {{ ab.variant_col() }}      AS group,        -- arm from the persisted cohort
      user_id,
      sum(gross_usd)              AS gross_usd,     -- ADDITIVE: one row per unit
      any(country)                AS country
  FROM {{ data_database }}.user_revenue
  {{ ab.exposed_units() }}        -- JOIN the cohort (ab_cohort_source, M8) + window filter + dedup
  GROUP BY group, user_id         -- one row per unit; loader builds per-variant arrays / suffstats
```

**Contract:** a metric query returns **one row per unit** with additive
`sum`/`count` columns over `[ab_start_date, ab_end_date]`. The loader **guards**
this: if a query returns more rows than distinct `unit_key`s, it errors loudly
("did you forget `GROUP BY unit_key`? metrics must be one row per unit").

**CUPED covariate mechanics** *(amended in M2 WP5; supersedes the original
`ab.covariate_window()` sketch, which would have required conditional
aggregation — a plain `sum(gross_usd)` over an extended scan double-counts the
pre-period)*: when a comparison's method declares `covariate_lookback`, the
loader renders the **same metric SQL a second time** over the pre-period
window `[start_ts − lookback, start_ts)` with the exposure filter dropped
(`ab_apply_exposure_filter=false` — the pre-period precedes exposure by
construction), and the pre-period **value** becomes the covariate keyed by
unit (absent units → 0.0). This is exactly the legacy CUPED semantics — the
covariate is the same metric over the pre-period — with zero extra authoring.
An explicit `columns.covariate` role (a covariate column the author computes
in their own SQL, e.g. a snapshot) takes precedence and skips the second
render.

## 4. The packaged assignment macro (must-fix: no leaked boilerplate)

The legacy system factored cohort/window/dedup into `exp_users_macros.jinja`. abkit
**ships** an equivalent so a metric SQL describes *only* the metric aggregation:

- `ab.exposed_units(event_date_col='event_date', event_time_col='event_time')` —
  `JOIN`s `{{ ab_cohort_source }}` (the M8 mode switch — see the callout below),
  **deduped per dialect** (`FINAL` on ClickHouse in copy mode — a mid-merge
  ReplacingMergeTree must never yield a unit twice; a
  `MIN(exposure_ts)`-deduped `GROUP BY` in the live-subquery default; PG/MySQL
  enforce the PK in copy mode), and applies BOTH the coarse `event_date`
  predicate (Date partition pruning) and the precise half-open
  `event_time >= ab_start_ts AND event_time < ab_end_ts` filter plus
  `event_time >= exposure_ts` (dropped on the covariate pre-period render).
- `ab.variant_col()` / `ab.stratum_col()` — arm/stratum labels from the cohort.
- *(The `ab.covariate_window()` sketch is superseded by the two-render
  covariate mechanics in §3 — M2 WP5.)*

> **M8: `ab_cohort_source` is the one cohort-mode switch.** Default
> (`assignment.cohort_copy.enabled: false`): a live deduping subquery over the
> rendered assignment SQL, validated once per run, nothing persisted. With
> `cohort_copy.enabled: true`: the persisted `_ab_exposures` table (+ `FINAL`
> on ClickHouse). The packaged macro joins ONLY this builtin — metric authors
> never choose between the modes; `build_cohort_backend`
> (`abkit/loaders/exposure_source.py`) is the single place the branch lives.

Validation asserts the rendered SQL joins the cohort through the macro (present
identically in both modes); a metric authored without the macro fails
config-lint, so correctness-critical join/dedup logic is never silently
re-implemented by hand.

## 5. Jinja built-ins (authoritative, StrictUndefined)

Rendered by `loaders/query_template.py`; an undeclared variable hard-fails. Tested
against the scaffolded example so docs & examples cannot drift.

| Variable | Meaning |
|---|---|
| `ab_experiment_id` | experiment name |
| `ab_start_date` | **pinned** experiment start → cumulative left edge (date part) |
| `ab_end_date` | the **moving** cutoff (date part; partition-pruning predicate) |
| `ab_start_ts` / `ab_end_ts` | the precise UTC window bounds; `ab_end_ts` is **exclusive** (`event_time >= ab_start_ts AND event_time < ab_end_ts`) — the canonical filter at sub-day cadence |
| `ab_cov_start` / `ab_cov_end` | covariate window bounds (per the chosen lookback) |
| `ab_variants` | the variant list |
| `ab_unit_key` | the unit/randomization key |
| `ab_added_filters` | the experiment's optional SQL fragment |
| `data_database` / `internal_database` | profile-resolved schemas |
| `ab_cohort_source` | M8: the one cohort-mode switch — `ab_exposures_table` (+ `FINAL` on ClickHouse) under `assignment.cohort_copy.enabled`, else a live `MIN(exposure_ts)`-deduped subquery over the assignment SQL; the packaged macro's `exposed_units()` joins ONLY this |
| `ab_exposures_table` | the fully-qualified persisted cohort table name; kept for external/back-compat templates — the packaged macro reads the cohort through `ab_cohort_source` (M8) |
| `ab_dialect` | `clickhouse` \| `postgres` \| `mysql` (dialect-aware dedup in the macro) |
| `ab_apply_exposure_filter` | internal: `false` only on the covariate pre-period render |
| `ab.*` (macro) | `exposed_units()`, `variant_col()`, `stratum_col()` |

Built-ins **win** over caller context: a context key shadowing an `ab_*`
variable raises a render error (a silently shadowed `ab_end_ts` would change
the analysis window) — a deliberate, recorded deviation from the detectkit
donor's context-wins behaviour.

## 6. Alpha & multiple-testing (must-fix: inspectable, not hidden)

The legacy applied a **two-tier Bonferroni** at config time: `adjust_alpha(alpha,
groups, 1)` for the main metric, `adjust_alpha(alpha, groups, main_metrics_count)`
for the rest (`alpha / (C(groups,2) × metrics)`). abkit makes this **declared and
inspectable**:

- `alpha` + `correction` are declared at experiment (or project) level.
- `abk run` / `validate` / the HTML report **echo the effective per-comparison
  alpha** and the `C(groups,2) × metrics` divisor in the `StageLogRenderer`.
- A golden test reproduces the exact two-tier scheme keyed off `is_main_metric`.
- Benjamini-Hochberg (`correction: benjamini_hochberg`) is applied **read-time**
  across an experiment's metrics; its interaction with peeking is documented in
  [aa-false-positive-matrix.md](aa-false-positive-matrix.md).

## 7. `method_config_id` (must-fix: ONE canonical spec)

```
method_config_id = sha256( method_name              # registry name (NOT class name)
                         + json_dumps_sorted(params) # non-default params only; canonical JSON
                         + ALGORITHM_VERSION )       # appended only when > 1 (match detectkit)
```

- Pinned with a **byte-exact unit test** (the exact bytes hashed for a known
  method+params).
- **Seed policy (uniform):** `seed` is **excluded** from `method_config_id` for
  **all** bootstrap methods (stable per-day series identity); the param schema
  marks `seed` identity-excluded and rejects it for closed-form methods. Re-runs
  stay byte-stable via a deterministic per-row seed derived from
  `(exp, metric, name_1, name_2, end_date, n_samples)` — see
  [statistics-changes.md](statistics-changes.md).
- Editing any identity-bearing param orphans the prior series (new id);
  `abk clean` GCs it, and `run`/`explore` warn when an experiment has >1 `method_config_id`
  for a metric ("the dashboard will show two stabilization lines — clean to resolve").

## 8. Validation matrix (`config/validator.py`)

Tested, fail-fast, two-level:

- every `comparison.metric` resolves to a `metrics/` file (no dangling refs);
- experiment & metric names unique within their namespace; explicit cross-namespace
  collision rule;
- `metric.unit_key` equals `experiment.unit_key` (or omitted → inherited);
- no duplicate metric refs within one experiment;
- method `name` ∈ registry, params ∈ that method's schema (e.g. CUPED requires a
  covariate; Poisson bootstrap is mean-only; paired requires aligned sizes);
- `is_main_metric` / `is_guardrail` not both true; at least one main metric;
- assignment SQL selects `unit_key`, `variant`, `exposure_ts`;
- `expected_split` variants ⊆ `assignment.variants`;
- **metric `aa_fpr_budget`** (optional; M4/D12) — a fraction in `(0, 1]`; the
  per-metric A/A false-positive budget the validate matrix colours this metric
  against, overriding `project.statistics.aa_fpr_budget` (resolution:
  metric → project → `α × 1.5`, `resolve_fpr_budget`);
- **cadence & looks** (cumulative-intervals.md §6): cadence parses to whole
  seconds ≥ 1s; schedule segments strictly coarsening with increasing `until`;
  planned looks > `max_looks` (project default 5000) → **error** (the only hard
  gate — there is deliberately NO time floor); looks > `warn_looks` (default
  100) without `sequential.enabled` → peeking warning quoting the look count and
  the measured A/A FPR for this grid; `cadence < 1d` requires `data_lag`;
  `cadence < 1d` with `scheme: alpha_spending` → error (mSPRT/always_valid is
  the sub-day path); `24h % cadence != 0` → drift warning; `cadence > horizon`
  → error; `covariate_lookback < 1d` → error, `< 7d` → warning;
- **`assignment.cohort_copy`** (optional; M8): when `enabled`, `update_column`
  must be a valid identifier (parse-time sanity check — real existence is a
  run-time column probe), and the assignment SQL must reference
  `{{ ab_added_filters }}` **live** — the incremental copy engine injects its
  batch bounds through that hook, and config-lint proves the reference by
  rendering a sentinel filter through it (a token parked in a comment cannot
  pass); missing → error with a fix hint.

A `abk run --steps validate` (config-lint) runs the full parse + reference
resolution + SQL render-smoke-test under StrictUndefined **without touching the DB**
— runnable in CI before any compute (the legacy `ExpMetricQueriesCheckingPipeline`).
