# BI integration — connect your own dashboard

abkit owns the numbers, not the dashboard. `_ab_results` is a **stable, BI-friendly
warehouse table** (the [data contract](../../specs/data-contract-and-reporting.md) §2–§3);
point **Grafana, Lightdash, Metabase, or Superset** at it and build. This folder ships
reference SQL you copy into any of them, plus one importable Grafana dashboard.

| File | What it is |
|---|---|
| [`queries.sql`](queries.sql) | 8 tool-agnostic recipes: headline scoreboard, the effect+CI stabilization chart, raw/CUPED arm values, significance-vs-effective-alpha, MDE/power, cross-experiment board, freshness, and a config-drift detector. |
| [`srm_panel.sql`](srm_panel.sql) | The optional **SRM** (sample-ratio-mismatch) monitoring panel — the "is the experiment even valid?" guard. |
| [`grafana_dashboard.json`](grafana_dashboard.json) | An importable Grafana dashboard wiring the core recipes together (ClickHouse datasource). Lightdash/Metabase/Superset users: paste the `queries.sql` recipes as chart SQL — the recipes are the portable first-class deliverable. |

## Quick start

1. Create a read-only warehouse user scoped to abkit's internal database (the
   `abkit_internal` schema by default — where `_ab_results` lives).
2. Add that connection to your BI tool.
3. Copy a recipe from `queries.sql`, set its `{experiment:String}` / `{metric:String}`
   parameters to your tool's syntax, and build the panel.
4. For Grafana + ClickHouse, import `grafana_dashboard.json` and pick your datasource.

## The five invariants (every recipe already honors these — keep them if you write your own)

`_ab_results` has sharp edges. Get these wrong and the dashboard lies:

1. **Read `FINAL` on ClickHouse.** The table is a `ReplacingMergeTree(created_at)`: a
   recomputed cutoff leaves *both* the old and new version until a background merge. A
   naive `SELECT` double-counts. Every recipe reads `FINAL` (or dedups via
   `argMax(col, end_ts)`). On **PostgreSQL/MySQL** abkit upserts on the primary key, so
   the base table is already deduped — delete the `FINAL` keyword and the recipes work.
2. **Group/filter by `method_config_id`.** It is a hash of the method + its identity
   params; editing a param starts a *new* series and orphans the old rows. More than one
   `method_config_id` per `(experiment, metric)` draws duplicate stabilization lines —
   `queries.sql` recipe 8 detects it; `abk clean` prunes it.
3. **Compare `pvalue` to the row's own `alpha`, never `0.05`.** abkit applies a two-tier
   (main vs secondary/guardrail) Bonferroni correction, so the effective `alpha` differs
   per row and is stored per row. `alpha = 0.05` hardcoded mis-reports secondary metrics.
4. **Respect the peeking guard.** A fixed-horizon CI read *before* the horizon is **not**
   peeking-valid — treating it as a stop signal is the optional-stopping error that
   `abk validate` exists to expose. Trust a decision only when `is_horizon = true` **or**
   `ci_kind = 'always_valid'` (a peeking-safe confidence sequence). The recipes surface
   both flags and gate their derived verdict on them.
5. **Handle NULLs.** `effect`, `left_bound`, `right_bound`, `ci_length`, `pvalue`, `std_*`,
   `mde_*`, and `srm_pvalue` are nullable — a row demoted by `insufficient_data` or blocked
   by SRM carries NULLs. Filter or coalesce; never assume non-null.

## The columns you'll bind to (see the full schema in [internal-tables](../../specs/data-contract-and-reporting.md))

| Column(s) | Meaning |
|---|---|
| `experiment`, `metric`, `method_config_id` | series identity |
| `name_1`, `name_2` | control arm, treatment arm |
| `start_ts`, `end_ts`, `elapsed_days`, `is_horizon` | the cumulative window; `is_horizon` marks the planned decision cutoff |
| `effect`, `left_bound`, `right_bound`, `ci_length`, `ci_kind` | the estimate + CI; `ci_kind` ∈ {`fixed`, `always_valid`} |
| `pvalue`, `alpha`, `reject` | test result; `alpha` is the effective (post-correction) threshold; `reject` is abkit's composed decision |
| `value_1/2`, `std_1/2`, `cov_value_1/2`, `size_1/2` | per-arm means, std, CUPED-adjusted means, arm sizes |
| `mde_1/2` | achieved minimum detectable effect per arm |
| `srm_flag`, `srm_pvalue`, `decision_blocked`, `insufficient_data` | validity gates (see `srm_panel.sql`) |
| `created_at` | the LWW version stamp (drives ClickHouse `FINAL` / PK dedup) |

> **Always put the SRM panel above the effect charts.** A significant effect on an
> SRM-failed experiment (`srm_flag = true` / `decision_blocked = true`) is not trustworthy —
> fix the assignment first.
