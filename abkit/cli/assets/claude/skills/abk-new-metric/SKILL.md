---
name: abk-new-metric
description: >-
  Scaffold a new reusable abkit metric as a validated YAML file under metrics/.
  Use when the user wants to add or create an abkit metric, define a conversion /
  revenue / ratio measure for experiments, or set up a metric SQL query. Produces
  a metrics/<name>.yml plus one-row-per-unit SQL that joins the assignment cohort
  macro, passes config-lint, and is ready to reference from an experiment.
---

# Create a new abkit metric

Scaffold one metric YAML that **validates and is ready to run**. A metric is the
reusable library item — experiments reference it by `name`; it is not run on its
own. Work through the steps in order. Do not invent SQL, table names, or column
names — gather them from the user. For field detail read
`.claude/rules/ab-analysis-kit/metrics.md`; this skill is the procedure, that
file is the reference.

## Step 0 — Confirm you're in an abkit project

A project root contains `abkit_project.yml`. Verify it exists in the target
directory (or find the nearest ancestor that has it). If there is none, stop and
tell the user to run `abk init <name>` first, or ask which project directory to
use. Metrics live under `metrics/` (one YAML per metric); shared SQL can go under
`sql/`.

If `profiles.yml` is still the `abk init` placeholder (or a run fails with
`internal_database must be set` / `Connection refused`), the database connection
comes first — use the **`abk-setup-project`** skill, then come back here. You can
still author and config-lint a metric with no database.

## Step 1 — Name and file path

- Ask for / confirm the metric `name`: lowercase snake_case, descriptive
  (`signup_cr`, `revenue_per_user`, not `metric1`).
- The file is `metrics/<name>.yml`. Keep filename == `name` by convention.
- **Uniqueness is mandatory and the namespace is shared with experiments.** The
  `name` is the database key, globally unique across the whole project — a metric
  cannot share a name with another metric **or an experiment**. Grep both
  `metrics/` and `experiments/` for `name: <name>` and abort on any clash,
  suggesting a more specific name.

## Step 2 — Pick the metric `type` and column roles

The `type` tells the loader how to read the numbers; `columns` maps result
columns to stats roles. Pick with the user (see `metrics.md` for detail):

| `type`     | Required roles             | Optional roles         | Use for |
|------------|----------------------------|------------------------|---------|
| `sample`   | `value`                    | `covariate`, `stratum` | per-unit number: ARPU, latency, events-per-user |
| `fraction` | `count`, `nobs`            | `stratum`              | conversion / CTR (successes over trials) |
| `ratio`    | `numerator`, `denominator` | `stratum`              | ratio-of-sums via the delta method (revenue / sessions) |

- `variant` is **always required** — it is the arm label produced by the cohort
  macro (project it `AS variant`).
- **Do not pre-divide a ratio into a `sample`.** A ratio-of-sums needs
  numerator/denominator kept paired so the delta-method variance is correct.
- `covariate` is a `sample`-only role and is usually **not** hand-authored — see
  Step 4 (CUPED). `stratum` is optional on any type.

A missing required role, or a role that doesn't belong to the `type`, fails
config-lint.

## Step 3 — Write the one-row-per-unit SQL (the core contract)

Never fabricate SQL. Get the source table, the value/count/ratio expressions,
and the `unit_key` from the user. Then assemble a query that obeys the two hard
rules:

1. **One row per unit.** Always `GROUP BY` the unit key. The loader guards this —
   more rows than distinct units errors loudly ("did you forget `GROUP BY`?").
2. **Additive aggregates over the cumulative window.** The window is
   **pinned-start / moving-end**: the left edge never moves, the right edge
   advances one cutoff at a time, so each run re-aggregates from experiment start
   through that cutoff (those points are the stabilization series). Additive means
   `sum(...)` / `count(...)` / `max(...)`. **Medians, quantiles, percentiles, and
   distinct-counts are NOT additive over a moving window and are an unsupported
   metric shape** — model the additive quantity instead (e.g. a per-user sum).

Every metric SQL **must** import and join the packaged assignment macro — the
cohort join, cumulative-window filter, and per-unit dedup live there once. Never
hand-roll the assignment join.

```jinja
{% import 'abkit_assignment.jinja' as ab %}
```

- `{{ ab.exposed_units() }}` — `INNER JOIN`s the persisted `_ab_exposures`
  cohort and applies the window + exposure filters and dedup. Override fact-side
  column names with args if needed: `ab.exposed_units('dt', 'ts')`.
- `{{ ab.variant_col() }}` — the arm label (project it `AS variant`).
- `{{ ab.stratum_col() }}` — the stratum label, only when stratifying.
- `{{ data_database }}` is the single data-location built-in on every backend
  (on Postgres it resolves to the profile's `data_schema` value; there is no
  `{{ data_schema }}` built-in). Mind the dialect (ClickHouse vs Postgres vs MySQL).

A fraction metric (per-user conversion): `count = max(converted)`, `nobs = 1`:

```sql
{% import 'abkit_assignment.jinja' as ab %}
SELECT
    {{ ab.variant_col() }}  AS variant,
    user_id,
    max(signed_up)          AS signed_up,   -- converted within the window?
    1                       AS visits       -- one trial per exposed unit
FROM {{ data_database }}.signup_events
{{ ab.exposed_units() }}
GROUP BY variant, user_id
```

A `sample` metric is the same shape over an additive `sum(...)`; a `ratio` metric
projects two additive sums as `numerator` / `denominator`. For long SQL, offer to
put it in `sql/<name>.sql` and use `query_file:` instead of inline `sql:`.

## Step 4 — CUPED covariate (usually don't author it)

For variance reduction you almost never write a `covariate` column. Instead the
**experiment's comparison** picks a CUPED method with a lookback, e.g.
`method: {name: cuped-t-test, params: {covariate_lookback: 14d}}`. The loader
then renders **this same metric SQL a second time** over the pre-period window
(exposure filter dropped) and uses the pre-period `value` as the covariate
(units absent pre-period → 0.0). So a plain `sample` metric is CUPED-ready with
no extra SQL. Set an explicit `columns.covariate` only if you compute a different
covariate yourself.

## Step 5 — Write the file

Write `metrics/<name>.yml`. Set exactly one of `sql:` (inline; `query:` is an
accepted alias) or `query_file:` — never both, never neither. Keep comments for
any non-obvious choice. A typical result:

```yaml
name: signup_cr
description: "Signup conversion (fraction, z-test)"
type: fraction
unit_key: user_id                # must match (or inherit from) the experiment
tags: [activation]               # optional; enables --select tag:<t>
columns:
  variant: variant               # arm label from the cohort macro
  count: signed_up
  nobs: visits
sql: |
  {% import 'abkit_assignment.jinja' as ab %}
  SELECT
      {{ ab.variant_col() }}  AS variant,
      user_id,
      max(signed_up)          AS signed_up,
      1                       AS visits
  FROM {{ data_database }}.signup_events
  {{ ab.exposed_units() }}
  GROUP BY variant, user_id
# aa_fpr_budget: 0.07            # optional per-metric A/A budget for `abk validate`
```

## Step 6 — Validate before declaring done

Re-check every item; fix any failure before finishing:

- [ ] `name` is unique across **all** metrics and experiments, and matches the
      filename.
- [ ] Exactly one of `sql` / `query_file` is set.
- [ ] `columns` provides every role the `type` requires and no off-type role;
      `variant` is present.
- [ ] The SQL imports `abkit_assignment.jinja`, joins `{{ ab.exposed_units() }}`,
      is one row per `unit_key` (`GROUP BY`), and aggregates additively.
- [ ] `unit_key` matches (or is inherited from) the experiments that will use it.

Then config-lint — it round-trips the metric through the real validator (macro
import, one-row-per-unit shape, role↔type matrix) and needs **no** database:

```bash
abk run --steps validate      # config lint only — no DB
```

This is the config lint. It is **not** `abk validate`, which is the A/A
false-positive matrix and needs data.

To verify against the real database, reference the metric from an experiment
(add it to the experiment's `comparisons:`, or use the scaffolded example — a
metric is never run on its own), then do a non-destructive load-only run of that
experiment (`abk run` selects at the experiment level with `--select`):

```bash
abk run --select <experiment> --steps load
```

Editing a metric's SQL later changes what is loaded but not stored history —
recompute the affected cutoffs with `abk run --select <experiment>
--full-refresh --from <start> --to <end>` (`--full-refresh` requires both
`--from` and an exclusive `--to`).

## Step 7 — Report and hand off

Report the created file path and the config-lint result. A metric earns its value
inside an experiment, so offer the next steps:

- **Use it in an experiment** → the **`abk-new-experiment`** skill wires it into
  a comparison with a method (t-test / z-test / CUPED / ratio-delta / bootstrap).
- **Size it before launch** → **`abk plan`** (required-N / achievable-MDE / power
  at the effective alpha).
- **Tune the method on the real series** → the **`abk-explore`** skill (live
  cockpit); check it is calibrated with the **`abk-validate`** skill (A/A matrix).

Remember: never trust an effect before checking SRM, and the daily series is
peeking-prone unless the experiment opts into `sequential: {enabled: true}`.
