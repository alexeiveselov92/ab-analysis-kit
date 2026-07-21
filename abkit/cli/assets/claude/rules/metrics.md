# abkit — Metric configuration (`metrics/*.yml`)

One YAML file per metric under `metrics/`. A metric is the **reusable library
item**: experiments reference it by `name`, never by filename. The `name` is the
DB key and is **globally unique across the whole project — one namespace shared
with experiments** (a metric cannot share a name with an experiment). Keep the
`name` field and the filename in sync by convention.

A metric = a **`type`** (how to read the numbers) + a **column-role map** (which
result columns fill which role) + **SQL** that returns **one row per unit**.
Experiments are covered in `experiments.md`; picking/tuning the method in
`methods.md`.

## Anatomy

```yaml
name: arpu                       # required, globally unique (DB key)
description: "Average revenue per user"   # optional
type: sample                     # required: sample | fraction | ratio
unit_key: user_id                # analysis unit; must match (or inherit from) the experiment
tags: [revenue, guardrail]       # optional; `--select tag:<t>` and family selection
columns:                         # required — map result columns to stats roles
  variant: variant               # arm label (comes from the cohort macro)
  value:   gross_usd             # per-unit value  (type=sample)
  covariate: prev_gross_usd      # optional CUPED covariate (type=sample)
  stratum: country               # optional stratification key (any type)
sql: |                           # inline SQL (or `query_file: sql/arpu.sql`)
  {% import 'abkit_assignment.jinja' as ab %}
  ...
aa_fpr_budget: 0.07              # optional — per-metric A/A false-positive budget
```

Provide **exactly one** of `sql:` (inline; `query:` is an accepted alias) or
`query_file:` (a path, relative to the project root) — never both, never neither.

## `type` and the column-role map

`type` tells the loader how to build the stats container; `columns` names the
result columns for each role. Roles are validated against the type — a missing
required role or an off-type role fails config-lint.

| `type`     | Required roles              | Optional roles        | Container |
|------------|-----------------------------|-----------------------|-----------|
| `sample`   | `value`                     | `covariate`, `stratum`| per-unit values (mean-style: ARPU, latency, counts-per-user) |
| `fraction` | `count`, `nobs`             | `stratum`             | successes / trials (conversion, CTR) |
| `ratio`    | `numerator`, `denominator`  | `stratum`             | ratio-of-sums with the delta method (e.g. revenue / sessions) |

`variant` is **always required** (it is the arm label, produced by the cohort
macro — see below). `stratum` is always optional. `covariate` is a `sample`-only
role and is usually **not** hand-authored — see CUPED below.

- **`sample`** → each row's `value` is one unit's number. `fraction` and `sample`
  aggregates must be **additive over the cumulative window** (`sum`, `count`).
- **`fraction`** → `count` = successes, `nobs` = trials. For a per-unit
  conversion, `nobs = 1` and `count = max(converted)` (see the example).
- **`ratio`** → the unit is still one row, but the metric is a ratio of two
  additive sums across units; the loader keeps numerator/denominator paired so
  the delta-method variance is correct. Do **not** pre-divide into `sample`.

## The one-row-per-unit + additive contract (do not break this)

Every metric SQL must return **exactly one row per `unit_key`** with columns that
are **additive over the cumulative window**. The window is **pinned-start /
moving-end**: the left edge (`ab_start_date`) never moves; the right edge
(`ab_end_ts`) advances one cutoff at a time, so each run re-aggregates from
experiment start through that cutoff — those points are the stabilization series.

- The loader **guards** one-row-per-unit: more rows than distinct units errors
  loudly ("did you forget `GROUP BY unit_key`?"). Always `GROUP BY` the unit.
- Additive means `sum(...)` / `count(...)` / `max(...)`. **Medians, quantiles,
  percentiles, and distinct-counts are NOT additive over a moving window and are
  an unsupported metric shape** — model the additive quantity instead (e.g. a
  per-user sum), or the cumulative points will be wrong.

## The mandatory cohort macro

Every metric SQL **must** import and join the packaged assignment macro — the
correctness-critical cohort join, cumulative-window filter, and per-unit dedup
live there once, so metric SQL describes only its own aggregation:

```jinja
{% import 'abkit_assignment.jinja' as ab %}
```

- `{{ ab.exposed_units() }}` — `INNER JOIN`s the ONE `ab_cohort_source`
  builtin: by default (`assignment.cohort_copy` unset) a live deduping subquery
  over the rendered assignment SQL (re-validated every invocation); with
  `cohort_copy.enabled: true` the persisted, append-only-copied `_ab_exposures`
  table (`FINAL` on ClickHouse). Metric authors never choose between the two.
  Either mode applies both the coarse `event_date` predicate and the precise
  half-open `event_time` window. Optional args override the fact-side column
  names: `ab.exposed_units('dt', 'ts')`.
- `{{ ab.variant_col() }}` — the arm label from the cohort (project it `AS
  variant` and reference it in `columns.variant`).
- `{{ ab.stratum_col() }}` — the stratum label, when stratifying.

Config-lint asserts the rendered SQL joins the cohort through the macro
(present identically in both cohort modes); a metric authored
without the macro fails. Never hand-roll the assignment join or the window
filter. `{{ data_database }}` is the data-location built-in on **every** dialect
(on Postgres it resolves to the profile's `data_schema` value); there is no
`{{ data_schema }}` built-in.

## CUPED covariate = a whole-day pre-period lookback

For variance reduction you almost never author a `covariate` column. Instead, set
`covariate_lookback` on the **method** (e.g. `cuped-t-test` with
`params: {covariate_lookback: 14d}` in the experiment's comparison). The loader
then renders **this same metric SQL a second time** over the pre-period window
`[start_ts − lookback, start_ts)` with the exposure filter dropped, and uses that
pre-period `value` as the covariate keyed by unit (units absent pre-period → 0.0).
This is the legacy CUPED semantics — the covariate is the metric's own pre-period
value — at zero extra authoring cost (declarative-config.md §3).

Set an explicit `columns.covariate` **only** when you compute a different
covariate yourself (e.g. a snapshot column in your SQL); an explicit covariate
role takes precedence and skips the second render.

## `aa_fpr_budget` (optional)

A per-metric A/A false-positive budget (a fraction in `(0, 1]`) that `abk
validate` colours **this** metric's FPR against, overriding the project-wide
`statistics.aa_fpr_budget`. Validation-only — it never changes the pipeline math.

## Editing a metric that already has data

- **Changing the SQL** changes what is loaded but not stored history; recompute
  the window with `abk run --select <exp> --full-refresh --from <start> --to <end>`
  (`--full-refresh` requires both bounds).
- Metric results are keyed by the method identity (`method_config_id`), which
  lives on the **experiment's comparison**, not here — see `methods.md`. Editing
  an identity param orphans the prior result series (`abk clean` prunes).
- **Renaming/deleting a metric** strands its rows under the old name and breaks
  every experiment that references it.

## Real example — a fraction metric (per-user conversion)

```yaml
name: signup_cr
description: "Signup conversion (fraction, z-test)"
type: fraction
unit_key: user_id
tags: [activation]
columns:
  variant: variant
  count: signed_up
  nobs: visits
sql: |
  {% import 'abkit_assignment.jinja' as ab %}
  -- ONE ROW PER UNIT, additive over the pinned-start / moving-end window.
  SELECT
      {{ ab.variant_col() }}  AS variant,
      user_id,
      max(signed_up)          AS signed_up,   -- converted within the window?
      1                       AS visits       -- one trial per exposed unit
  FROM {{ data_database }}.signup_events
  {{ ab.exposed_units() }}
  GROUP BY variant, user_id
```

A `sample` metric is the same shape with `type: sample` and
`columns: {variant, value}` over an additive `sum(...)`; a `ratio` metric sets
`type: ratio` and `columns: {variant, numerator, denominator}` over two additive
sums. `abk init` scaffolds runnable `example_signup_cr` (fraction) and
`example_arpu` (sample + CUPED) metrics you can copy.
