# Visualizing results in BI

abkit writes everything it computes into internal `_ab_*` tables in your
warehouse. **abkit owns the correct numbers, not the dashboard**
(data-contract-and-reporting §3) — so there are two ways to see an experiment's
results:

- **Self-contained HTML reports** — the quickest look. `abk run --report`
  writes one offline HTML file per experiment (baked payload + inline JS): the
  variant means and lift, the effect + CI **stabilization chart**, MDE/power,
  p-value-vs-alpha, the SRM panel, and the WIN/LOSE/FLAT/INCONCLUSIVE verdict
  with its rationale. No BI tool, no SQL. See [HTML reports](#html-reports).
- **Your own BI / dashboarding tool** — for shared dashboards and custom
  panels, point **any** BI tool at the results table and chart it with plain
  SQL (Grafana, Lightdash, Metabase, Superset, Redash, or a `clickhouse-client`
  / `psql` session). The results contract, `_ab_results`, is a stable,
  BI-friendly warehouse table designed for exactly this
  (data-contract-and-reporting §2). See [Connecting a BI tool](#connecting-a-bi-tool).

> **Want to change the method, not just look at it?** The HTML report is
> read-only — it replays what already ran. To turn a method's knobs on the real
> series and watch the CI band recompute live — then write the config back into
> the experiment — use [`abk explore`](explore.md), the interactive cockpit.

## HTML reports

For a fast, no-setup look at how an experiment actually behaved, generate a
self-contained HTML readout. Pass `--report` to a run:

```bash
# After a normal run, write a report for the experiment
abk run --select example_signup_test --report
```

The report is built from the rows already in `_ab_results`, so even a run that
only refreshes the report reads from whatever is stored. It is **offline and
self-contained** — the chart and data are inlined into the single file, so
nothing is fetched and nothing leaves the page. Email it, or commit it as a
snapshot.

**Where it lands.** `--report` is tri-state (the donor's flag shape):

| You pass | The report is written to |
|---|---|
| `--report` (bare) | `reports/<experiment>.html` |
| `--report <dir>` | `<dir>/<experiment>.html` |
| `--report report.html` | that exact file |

A report failure never fails the run — it yellow-skips and leaves any previous
good report in place.

Two related surfaces write their own HTML the same way, so they never clobber
the `abk run` readout:

- **`abk explore --no-serve`** emits a static, read-only snapshot of the cockpit
  to `reports/<experiment>__explore.html` (drop `--no-serve` for the live
  localhost server). See [the explore guide](explore.md).
- **`abk validate --report`** writes the A/A false-positive matrix to
  `reports/<experiment>__validate.html`. See [the validate guide](validate.md).

## What's in the tables

abkit's internal `_ab_*` tables live in the **`internal_database`** (ClickHouse
/ MySQL) or **`internal_schema`** (PostgreSQL) configured in your
[profile](configuration.md) — separate from the `data_database` your metric
queries read from. The `abk init` seed names it `abkit_internal` (ClickHouse) or
`abkit` (MySQL database / PostgreSQL schema); the BI recipes use
`abkit_internal._ab_results` as a placeholder you replace with your own.

The table you chart is **`_ab_results`** — the clean, greenfield BI contract
(data-contract-and-reporting §2). One row per
`(experiment, metric, variant-pair, method_config_id, end_ts)`, where `end_ts`
is the cumulative-window cutoff (UTC, half-open/exclusive) and each point is
cumulative from `start_ts`. Plotting `effect` and its `[left_bound, right_bound]`
CI band against `end_ts` (or the fractional `elapsed_days`) gives you the
signature stabilization chart.

## Connecting a BI tool

1. Create a **read-only** warehouse user scoped to abkit's internal database
   (the `abkit_internal` schema by default — where `_ab_results` lives).
2. Add that connection to your BI tool. Use a fully qualified table name if the
   connection isn't already scoped to that database.
3. Copy a recipe from the reference SQL below, swap its `{experiment:String}` /
   `{metric:String}` parameters for your tool's variable syntax (Grafana
   `$experiment`, Superset/Metabase template parameters, or a literal string),
   and build the panel.

Rather than re-print SQL here, abkit ships **copy-pasteable, tool-agnostic
recipes** you point your BI at directly — the recipes, not any one dashboard,
are the first-class deliverable:

| Recipe file | What it covers |
|---|---|
| [`examples/bi/queries.sql`](../examples/bi/queries.sql) | 8 recipes: the headline scoreboard, the effect + CI stabilization chart, raw/CUPED per-arm values, significance-vs-effective-alpha, MDE/power read-back, the cross-experiment portfolio board, pipeline freshness, and a config-drift detector. |
| [`examples/bi/srm_panel.sql`](../examples/bi/srm_panel.sql) | The optional **SRM** (sample-ratio-mismatch) monitoring panel — the "is the experiment even valid?" guard. |
| [`examples/bi/grafana_dashboard.json`](../examples/bi/grafana_dashboard.json) | An importable Grafana dashboard wiring the core recipes together (ClickHouse datasource). Lightdash / Metabase / Superset users paste the `queries.sql` recipes as chart SQL. |

See [`examples/bi/README.md`](../examples/bi/README.md) for the full walkthrough
and the column-by-column reference.

## The invariants (do not drop them)

`_ab_results` has sharp edges. Every shipped recipe already honors these — keep
them if you write your own, because getting one wrong makes the dashboard lie.

1. **Read `FINAL` on ClickHouse.** `_ab_results` is a
   `ReplacingMergeTree(created_at)`: a recomputed cutoff leaves *both* the old
   and new version in the table until a background merge collapses them, so a
   naive `SELECT` double-counts. Every recipe reads `FINAL` (or dedups by the
   latest `created_at` via `argMax(col, created_at)`). On **PostgreSQL / MySQL**
   abkit upserts on the primary key, so the base table is already deduped —
   delete the `FINAL` keyword and the recipes work unchanged.

2. **Group and filter by `method_config_id`.** It is a hash of the method plus
   its identity params; editing an identity param starts a *new* result series
   and **orphans** the old rows. More than one `method_config_id` per
   `(experiment, metric)` draws duplicate stabilization lines. Recipe 8 in
   `queries.sql` detects the drift; `abk clean` prunes it (dry-run by default —
   pass `--execute` to apply):

   ```bash
   abk clean --select example_signup_test --execute
   ```

3. **Compare `pvalue` to the row's own `alpha`, never `0.05`.** abkit applies a
   two-tier Bonferroni correction (main metrics vs secondary/guardrail metrics),
   so the effective `alpha` differs per row and is stored per row
   (data-contract-and-reporting §1). Hardcoding `alpha = 0.05` mis-reports every
   secondary metric. Prefer abkit's own `reject` flag (`pvalue < alpha`), which
   encodes that per-row Bonferroni decision. Read-time Benjamini-Hochberg
   (opt-in `correction: benjamini_hochberg`) is applied by the readout and HTML
   report, **not** baked into the stored `alpha`/`reject` — a BI chart reading
   `_ab_results` directly sees the two-tier Bonferroni decision.

4. **Respect the peeking guard.** A fixed-horizon CI read *before* the planned
   horizon is **not** peeking-valid — treating it as a stop signal is exactly
   the optional-stopping error that [`abk validate`](validate.md) exists to
   expose (data-contract-and-reporting §4). Fixed-horizon is the default;
   always-valid confidence sequences are opt-in per experiment via
   `sequential.enabled: true`, which stamps `ci_kind = always_valid` on the
   rows (`ci_kind` is an output column, not a config key). Trust a decision only when
   `is_horizon = true` **or** `ci_kind = 'always_valid'` (a peeking-safe
   sequence). Surface both flags and render pre-horizon fixed CIs with a "not
   peeking-valid" visual treatment.

5. **Handle NULLs.** `effect`, `left_bound`, `right_bound`, `ci_length`,
   `pvalue`, `std_1/2`, `cov_value_1/2`, `mde_1/2`, and `srm_pvalue` are
   nullable. A row demoted for too little data (`insufficient_data = true`) or
   blocked by SRM is still written — with its counts and SRM verdict intact —
   but its inference columns are NULL. Filter (`WHERE effect IS NOT NULL`) or
   coalesce; never assume non-null. NULL points are gaps in a series, never
   zeros.

## The columns you'll bind to

The full schema lives in the [internal-tables reference](../reference/internal-tables.md);
the columns you'll reach for most:

| Column(s) | Meaning |
|---|---|
| `experiment`, `metric`, `method_config_id` | series identity — always group/filter by all three |
| `is_main_metric`, `is_guardrail` | metric tier (drives the two-tier alpha and the verdict) |
| `name_1`, `name_2` | control arm, treatment arm |
| `start_ts`, `end_ts`, `elapsed_days`, `is_horizon` | the cumulative window; `end_ts` is the cutoff key, `is_horizon` marks the planned decision cutoff |
| `effect`, `left_bound`, `right_bound`, `ci_length`, `ci_kind` | the estimate + CI; `ci_kind` is `fixed` or `always_valid` |
| `pvalue`, `alpha`, `reject` | test result; `alpha` is the effective (post-correction) threshold; `reject` is abkit's composed decision |
| `value_1/2`, `std_1/2`, `cov_value_1/2`, `size_1/2` | per-arm means, std, per-arm covariate (pre-period) means CUPED/post-normed methods adjust against (NULL when no covariate is used), arm sizes |
| `mde_1/2` | achieved minimum detectable effect per arm (planning read-back) |
| `srm_flag`, `srm_pvalue`, `decision_blocked`, `insufficient_data` | validity gates |
| `created_at` | the strictly-monotonic LWW version stamp (drives ClickHouse `FINAL` / PK dedup) |

Two things are **derived in the BI query, not stored**: the `zero_effect = 0`
reference line and `avg_group_size = (size_1 + size_2) / 2`. The metric
description also isn't in `_ab_results` — it lives in the metric YAML, joined by
BI if you want it (one source of truth).

## Put SRM above the effect charts

SRM (sample-ratio mismatch) means the observed arm split has drifted from the
assigned split — the randomization or the cohort query is broken. abkit blocks
the decision when it fires (`decision_blocked = true`), and a significant effect
on top of an SRM-failed experiment is **not** trustworthy. A plain effect
dashboard won't necessarily surface `srm_flag`, so the CLI and HTML report are
the canonical SRM gate (data-contract-and-reporting §6) — but you should still
pin the [`srm_panel.sql`](../examples/bi/srm_panel.sql) red/green board **above**
every effect chart. The gate runs at α = 0.001 (χ² at daily-and-coarser cadence;
an anytime-valid multinomial test below one-day cadence). The *expected* split
lives in the experiment YAML, not in `_ab_results`; the panel reports the
*observed* split and abkit's verdict.

## Tips

- **Everything is UTC.** Timestamps are stored UTC; convert to the display
  timezone in the BI tool, not in SQL.
- **Time ranges are tool-specific.** Each tool injects the dashboard time range
  its own way (Grafana `$__timeFilter(end_ts)`, Superset/Metabase time-range
  controls). The recipes filter on `end_ts` — swap in your tool's control.
- **One panel per experiment, parameterized.** An `{experiment}` (and optional
  `{metric}`) variable lets a single dashboard serve every experiment.
- **A row is a decision only when it's decision-ready.** Filter to
  `is_horizon = true OR ci_kind = 'always_valid'` before deriving a verdict, and
  exclude `decision_blocked` / `insufficient_data` rows.

## See also

- [The `abk explore` cockpit](explore.md) — tune methods live, then write the
  config back
- [`abk validate`](validate.md) — the A/A false-positive matrix that calibrates
  the peeking guard
- [Compute methods](compute-methods.md) — what each of the 12 methods computes
  and its identity params
- [Configuration & profiles](configuration.md) — where `internal_database` /
  `internal_schema` live
- [The BI reference recipes](../examples/bi/README.md) — copy-pasteable SQL for
  every panel above
