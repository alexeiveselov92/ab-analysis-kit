# Examples

Worked, runnable examples for abkit. Start with the scaffolded seed project that
`abk init` writes for you, then wire your own BI dashboard to the results table
using the reference SQL. Every command and config key below is real — copy them
verbatim.

## The `abk init` seed example

`abk init <project>` scaffolds a complete project **with a runnable example
experiment**, so a fresh machine produces real numbers, not a placeholder-table
error (cli-and-dx §6). The scaffold ships a synthetic ClickHouse seed dataset,
two example metrics, and an example experiment that references them:

```bash
abk init my_experiments                 # ClickHouse profiles (default)
abk init my_experiments --db-type postgres   # or: mysql
cd my_experiments
```

`--db-type` accepts `clickhouse` (default), `postgres`, or `mysql`, and shapes
`profiles.yml`; the seed dataset always ships as ClickHouse SQL (see below),
and the example metric/assignment SQL is the same across backends. Use
`-d/--target-dir` to create the project somewhere other than the current directory.

What you get:

| Path | What it is |
|---|---|
| `abkit_project.yml`, `profiles.yml` | project config + database connections |
| `experiments/example_signup_test.yml` | the example experiment (14-day window, `1d` cadence, two comparisons) |
| `metrics/example_signup_cr.yml` | a `fraction` metric measured with `z-test` (the main metric) |
| `metrics/example_arpu.yml` | a `sample` metric measured with `cuped-t-test` (`covariate_lookback: 14d`) |
| `sql/example_assignment.sql` | the read-only exposure source (abkit never randomizes) |
| `seed/seed_dataset.clickhouse.sql` | 600 users, 50/50 split, deterministic synthetic data |
| `runners/prefect_flow.py`, `runners/prefect.yaml` | a Prefect 3 orchestration example (schedules `abk run`) |

Run it (ClickHouse — the seed dataset ships as ClickHouse SQL):

```bash
clickhouse-client --multiquery < seed/seed_dataset.clickhouse.sql
abk run --steps validate                 # lint the configs, no database needed
abk run --select example_signup_test     # compute the full 14-point series
abk run --select example_signup_test --report   # + a self-contained HTML readout
```

`abk run` writes the cumulative results series to `abkit_internal._ab_results`
and is idempotent (already-computed cutoffs are skipped). From there:
`abk explore` opens the tuning cockpit and `abk validate` runs the A/A
false-positive matrix on the same experiment.

The example is a live, working walkthrough of the config surface — the deeper
references are the [experiments](../guides/experiments.md),
[metrics](../guides/metrics.md), and [compute-methods](../guides/compute-methods.md)
guides, and the end-to-end [Quickstart](../getting-started/quickstart.md).

## BI dashboard recipes

abkit owns the numbers, not the dashboard. `_ab_results` is a stable,
BI-friendly warehouse table (the [data contract](../specs/data-contract-and-reporting.md)
§2–§3) — point Grafana, Lightdash, Metabase, or Superset at it and build.

**[BI integration recipes →](bi/README.md)** ship tool-agnostic reference SQL
you paste into any BI tool: a headline scoreboard, the effect + CI stabilization
chart, per-arm raw/CUPED values, significance-vs-effective-alpha, MDE/power, a
cross-experiment board, freshness and config-drift detectors, an SRM monitoring
panel, and one importable Grafana dashboard. The page also documents the five
`_ab_results` invariants (read `FINAL` on ClickHouse, group by
`method_config_id`, compare `pvalue` to the row's own `alpha`, respect the
peeking guard, handle NULLs) — get any wrong and the dashboard lies.
