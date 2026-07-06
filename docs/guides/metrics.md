# Metrics

A **metric** is abkit's reusable library item: one YAML file under `metrics/` that
says *how to read a number* (the `type`), *which SQL columns fill which statistical
role* (the `columns` map), and *the SQL* that produces **one row per unit**.
Experiments reference metrics by `name` and bind a statistical method to each —
so a single `arpu` metric can be reused across every experiment that cares about
revenue per user.

Metrics are deliberately method-free. Which test runs (t-test, CUPED, bootstrap,
ratio-delta), the significance level, and the main-vs-secondary designation all
live on the **experiment's comparison**, not here (see [experiments](experiments.md)
and [methods](compute-methods.md)). This page is only about authoring the reusable data
source.

## Where a metric lives

One YAML file per metric under `metrics/` (e.g. `metrics/arpu.yml`). Experiments
reference it by its `name` field, never by filename — keep the two in sync by
convention.

The `name` is the **DB storage key** and is **globally unique across the whole
project — metrics and experiments share one namespace**. A metric cannot share a
name with an experiment. Allowed characters are alphanumeric, `_`, and `-`
(validated at config-lint).

## Anatomy

```yaml
name: arpu                       # required — globally unique (DB key)
description: "Average revenue per user"   # optional
type: sample                     # required — sample | fraction | ratio
unit_key: user_id                # analysis unit; must match (or inherit from) the experiment
tags: [revenue, guardrail]       # optional — used by --select tag:<t> and family selection
columns:                         # required — map SQL result columns to stats roles
  variant: variant               # arm label (from the cohort macro) — ALWAYS required
  value:   gross_usd             # per-unit value (type=sample)
  stratum: country               # optional stratification key (any type)
sql: |                           # inline SQL — or `query_file: sql/arpu.sql`
  {% import 'abkit_assignment.jinja' as ab %}
  SELECT
      {{ ab.variant_col() }}  AS variant,
      user_id,
      sum(gross_usd)          AS gross_usd
  FROM {{ data_database }}.revenue_events
  {{ ab.exposed_units() }}
  GROUP BY variant, user_id
aa_fpr_budget: 0.07              # optional — per-metric A/A false-positive budget
```

Provide **exactly one** of `sql:` (inline; `query:` is an accepted alias) or
`query_file:` (a path relative to the project root) — never both, never neither
(declarative-config §3). The field names above are the authoritative pydantic
model in `abkit/config/metric_config.py`.

`abk init` scaffolds two runnable metrics you can copy — `example_signup_cr`
(fraction) and `example_arpu` (sample + CUPED).

## `type` and the column-role map

`type` tells the loader which stats container to build; `columns` names the SQL
result column for each role. Roles are validated against the type — a missing
required role or an off-type role fails config-lint (declarative-config §3).

| `type`     | Required roles              | Optional roles          | What it models |
|------------|-----------------------------|-------------------------|----------------|
| `sample`   | `value`                     | `covariate`, `stratum`  | per-unit values (ARPU, latency, counts-per-user) |
| `fraction` | `count`, `nobs`             | `stratum`               | successes / trials (conversion, CTR) |
| `ratio`    | `numerator`, `denominator`  | `stratum`               | ratio-of-sums via the delta method (e.g. revenue / sessions) |

- **`variant` is always required** — it is the arm label, produced by the cohort
  macro (below). Project it `AS variant` (or any name) and name it in
  `columns.variant`.
- **`stratum` is always optional** and valid for any type; set it only when you
  stratify.
- **`covariate` is a `sample`-only role** and is usually **not** hand-authored —
  see [CUPED](#cuped-the-covariate-is-your-metrics-own-pre-period) below.

Guidance per type:

- **`sample`** — each row's `value` is one unit's number, built from an additive
  aggregate (`sum(...)`, `count(...)`).
- **`fraction`** — `count` = successes, `nobs` = trials. For a per-unit
  conversion, `nobs = 1` and `count = max(converted)`.
- **`ratio`** — still one row per unit, but the metric is a ratio of two additive
  sums *across* units; the loader keeps numerator and denominator paired so the
  delta-method variance is correct. Do **not** pre-divide a ratio into a `sample`.

## The one-row-per-unit + additive contract

Every metric SQL must return **exactly one row per `unit_key`**, with columns
that are **additive over the cumulative window** (declarative-config §3). This is
the single contract that makes abkit's cumulative stabilization series correct.

The window is **pinned-start / moving-end**: the left edge (`ab_start_date` /
`ab_start_ts`) never moves; the right edge (`ab_end_ts`) advances one cutoff at a
time. Each run re-aggregates from experiment start through that cutoff — those
points are the stabilization series you see in the report.

- **Always `GROUP BY` the unit.** The loader guards the contract: more rows than
  distinct units is rejected loudly ("did you forget `GROUP BY <unit_key>`?").
  Duplicate unit rows are an error, never silently aggregated
  (`abkit/loaders/metric_loader.py`).
- **Additive means `sum(...)`, `count(...)`, `max(...)`.** Medians, quantiles,
  percentiles, and distinct-counts are **not** additive over a moving window and
  are an unsupported metric shape — model the additive quantity instead (e.g. a
  per-user sum) or the cumulative points will be wrong.

## The mandatory cohort macro

Every metric SQL **must** import and use the packaged assignment macro. The
correctness-critical cohort join, the cumulative-window filter, the exposure
filter, and per-unit dedup all live in the macro once — so your metric SQL
describes only its own aggregation (declarative-config §4):

```jinja
{% import 'abkit_assignment.jinja' as ab %}
```

The macro exposes three helpers (from
`abkit/loaders/templates/abkit_assignment.jinja`):

- **`{{ ab.exposed_units() }}`** — emits the `INNER JOIN` against the persisted
  `_ab_exposures` cohort (loaded once per run, deduped per dialect — `FINAL` on
  ClickHouse, PK on PostgreSQL/MySQL). It applies both the coarse `event_date`
  partition-pruning predicate **and** the precise half-open `event_time` window
  (`>= ab_start_ts AND < ab_end_ts`), plus the exposure filter
  (`event_time >= exposure_ts`). By default it reads fact-side columns named
  `event_date` and `event_time`; override them positionally when your fact table
  differs:

  ```jinja
  {{ ab.exposed_units('dt', 'ts') }}   {# event_date_col='dt', event_time_col='ts' #}
  ```

- **`{{ ab.variant_col() }}`** — the arm label from the cohort. Project it
  `AS variant` and reference that name in `columns.variant`.

- **`{{ ab.stratum_col() }}`** — the stratum label from the cohort, when you
  stratify.

Config-lint (and the loader at runtime) assert that the rendered SQL joins the
cohort; a metric authored without the macro fails. **Never hand-roll the
assignment join, the window filter, or the exposure filter** — doing so would
change numbers invisibly. The macro projects the cohort's columns under
`_abk_`-prefixed aliases, so a fact table that happens to have columns named
`unit_id`/`variant`/`exposure_ts`/`stratum` can never collide with the join.

## Jinja built-ins

Metric SQL is rendered by `abkit/loaders/query_template.py` under
**`StrictUndefined`** — referencing a variable that is not a built-in is a hard
render error, never a silent empty string. The authoritative variables
(declarative-config §5):

| Variable | Meaning |
|---|---|
| `ab_experiment_id` | the experiment name |
| `ab_start_date` | pinned experiment start (date part) — the cumulative left edge |
| `ab_end_date` | the moving cutoff (date part) — the partition-pruning predicate |
| `ab_start_ts` / `ab_end_ts` | the precise UTC window bounds; `ab_end_ts` is **exclusive** (`event_time >= ab_start_ts AND event_time < ab_end_ts`) |
| `ab_variants` | the experiment's variant list |
| `ab_unit_key` | the unit / randomization key |
| `ab_added_filters` | the experiment's optional SQL fragment |
| `data_database` | profile-resolved data location — see the note below |
| `internal_database` | profile-resolved location of the `_ab_*` internal tables |
| `ab_exposures_table` | the fully-qualified cohort table (used by the macro) |
| `ab_dialect` | `clickhouse` \| `postgres` \| `mysql` |

The macro also relies on internal built-ins (`ab_apply_exposure_filter`, and
`ab_cov_start` / `ab_cov_end` during the CUPED pre-period render) — you rarely
reference those directly.

**`data_database` resolves to the profile's data location** — the database on
ClickHouse and MySQL, and the configured `data_schema` on PostgreSQL. Write
`{{ data_database }}.<table>` in every dialect; the value is filled from the
active profile (see [profiles / project setup](configuration.md)).

**Built-ins win over caller context.** This is a deliberate, recorded deviation
from the donor's context-wins behaviour: a context key that shadows an `ab_*`
built-in raises a render error rather than silently changing the analysis window
(declarative-config §5).

## `is_main_metric` and the two-tier alpha

A metric YAML has **no** significance knobs. Whether a metric is the *primary
winner criterion* or a *secondary metric* is set per-experiment on its
**comparison** via `is_main_metric: true` — see [experiments](experiments.md).
It is called out here because it changes the effective alpha applied to *this
metric's* result.

abkit applies a **two-tier Bonferroni** keyed off `is_main_metric`
(declarative-config §6; `abkit/stats/correction.py`):

- the **main** metric is corrected as `adjust_alpha(alpha, groups, 1)`;
- every **secondary** metric shares the secondary budget:
  `adjust_alpha(alpha, groups, metrics_count)`, where `metrics_count` is the
  number of non-main metrics.

So the same reusable metric can land at a stricter or looser effective alpha
depending on how a given experiment classifies it. Read-time
Benjamini-Hochberg (`correction: benjamini_hochberg`) is the other supported
correction, applied across an experiment's metrics at readout. Every `abk run` /
`abk validate` / HTML report echoes the effective per-comparison alpha and the
divisor, so the correction is inspectable rather than hidden.

## Method binding and CUPED

The statistical method is bound on the experiment's comparison, not the metric —
one metric can be read with different methods by different experiments. abkit
never special-cases a method name; methods are plugins (12 registered). See
[methods](compute-methods.md) for the full catalogue and how `input_kind` must match the
metric `type` (e.g. `z-test` needs a `fraction`, `ratio-delta` needs a `ratio`).

### CUPED: the covariate is your metric's own pre-period

For variance reduction you almost never author a `covariate` column. Instead,
bind the `cuped-t-test` method (`sample`-only) on the experiment's comparison and
set the **method param** `covariate_lookback` (a `paired-cuped-t-test` is also
registered but is notebook-only — the v1 pipeline serves independent-arm
experiments, so config-lint rejects any paired method on a comparison):

```yaml
# in the experiment's comparison (experiments/<name>.yml), NOT the metric YAML
method:
  name: cuped-t-test
  params: {covariate_lookback: 14d}
```

The loader then renders **this same metric SQL a second time** over the
pre-period window `[start_ts − lookback, start_ts)` with the exposure filter
dropped, and uses that pre-period `value` as the covariate, keyed by unit (units
absent pre-period default to `0.0`). This reproduces the legacy CUPED semantics —
the covariate is the metric's own pre-period value — at zero extra authoring
cost (statistics-changes §5; declarative-config §3). Because CUPED needs no extra
SQL, keep the metric a plain additive `sum(...)`.

`covariate_lookback` rules enforced at config-lint
(`abkit/config/validator.py`):

- the metric `type` must be `sample`;
- the duration must be **whole days** (`>= 1d`; a fractional-day or sub-day
  duration is an error);
- `< 7d` warns (the covariate won't cover a weekly cycle);
- `covariate_lookback` is **identity-bearing** — a different lookback is a
  different covariate and therefore a different persisted result series.

Set an explicit `columns.covariate` **only** when you compute a different
covariate yourself (e.g. a snapshot column in your SQL). An explicit covariate
role takes precedence and skips the second render.

## `aa_fpr_budget` (optional)

`aa_fpr_budget` is a per-metric A/A false-positive budget — a fraction in
`(0, 1]` that [`abk validate`](validate.md) colours **this** metric's measured
FPR against, overriding the project-wide `statistics.aa_fpr_budget`. It is
validation-only and never changes the pipeline math.

## A worked fraction example

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
sums.

## Editing a metric that already has data

- **Changing the SQL** changes what is loaded but not the stored history.
  Recompute the window with
  `abk run --select <exp> --full-refresh --from <start> --to <end>`
  (`--full-refresh` requires both bounds).
- **Result identity is the method's**, not the metric's — results are keyed by
  `method_config_id`, which lives on the experiment's comparison (see
  [methods](compute-methods.md)). Editing an identity-bearing method param (including
  `covariate_lookback`) orphans the prior result series; `abk clean` prunes
  orphans.
- **Renaming or deleting a metric** strands its rows under the old name and
  breaks every experiment that references it. Rename deliberately.
