# ab-analysis-kit — Overview

ab-analysis-kit (abkit) is a Python library and CLI (`abk`) for **A/B experiment
analysis**. It is **dbt-like**: experiments and their reusable metrics live as
declarative YAML + SQL in a project directory, and you run them with one command.
Core statistics are pure numpy (no pandas). **ClickHouse, PostgreSQL and MySQL**
are all supported — only the connection and the SQL dialect of your metric
queries differ. A directory is an abkit project when it holds an
`abkit_project.yml`; connections live in `profiles.yml` (secrets via `${ENV}` /
`env_var(...)`). The `abk` CLI exits **non-zero on failure**.

## The primary entity is the EXPERIMENT

Unlike a metrics tool, abkit's primary entity is the **experiment**. An
experiment (one YAML) declares its cohort (the assignment source), its variants
and expected split, its window/cadence, and a list of **comparisons** — each
pointing a **metric** (a separate reusable YAML + SQL) at a statistical
**method**. Metrics are a shared library; many experiments reuse one metric.

> **One namespace, globally unique names.** An experiment name and a metric name
> share one namespace and are the **database key** — not the filename. An
> experiment cannot share a name with a metric.

## The pipeline: load → compute → readout

Every `abk run` executes three stages per experiment:

1. **load** — persists the assignment cohort ONCE into `_ab_exposures`, then runs
   each metric's SQL against the source DB over the cumulative window (the macro
   joins the cohort). One row per unit in, additive aggregates out.
2. **compute** — runs the configured statistical method (t/z-test, CUPED,
   ratio-delta, bootstrap) at each planned cutoff, gates SRM, and writes rows to
   `_ab_results` (the BI contract). Idempotent: already-computed cutoffs are
   skipped (anti-join on `end_ts`, honoring `data_lag`).
3. **readout** — recomputes the WIN / LOSE / FLAT / INCONCLUSIVE verdict at
   render time from `_ab_results` (never persisted), applies two-tier alpha and
   the SRM/horizon gates. Emitted by `abk run --report`, `abk explore`.

Run a subset of stages with `--steps` (e.g. `abk run --steps validate` is the
**config lint** — do not confuse it with `abk validate`, the A/A matrix).

## Project layout

```
my_project/
├── abkit_project.yml      # project config (statistical defaults, limits)
├── profiles.yml           # database connections (secrets via ${ENV})
├── experiments/           # experiment YAML — the PRIMARY entity
│   └── example_signup_test.yml
├── metrics/               # reusable metric YAML (one per metric)
│   ├── example_signup_cr.yml
│   └── example_arpu.yml
├── sql/                   # shared SQL (assignment sources, metric queries)
├── seed/                  # the synthetic example dataset
└── runners/               # orchestration examples (Prefect)
```

## Internal tables (`_ab_*`)

Created automatically on first run (no manual migration); they live in the
profile's `internal_database` / `internal_schema`, separate from the
`data_database` your queries read.

| Table | Holds |
|---|---|
| `_ab_results` | **The BI contract.** One row per `(experiment, metric, variant-pair, method_config_id, end_ts)` cumulative cutoff: effect, CI, p-value, per-arm stats, SRM flag, `insufficient_data`, `ci_kind`, `is_horizon`. Point BI here. |
| `_ab_exposures` | The persisted assignment cohort (unit → variant → exposure_ts), loaded once per run; joined by every metric query and the SRM count source. |
| `_ab_experiments` | Informational experiment catalog (descriptions, variants, split, alpha, cadence, tags) for BI joins; the pipeline never reads it back for decisions. |
| `_ab_aa_runs` | The `abk validate` A/A audit trail (per-cell FPR/power at the effective alpha); lights the explore calibration chip. Never pruned by `abk clean`. |
| `_ab_tasks` | Pipeline run/lock bookkeeping — the atomic per-experiment lock (`abk unlock` clears a stale one). |
| `_ab_unit_state` | A scalability seam (thin per-unit-moment materialization); v1 does NOT read it on the compute path. |

## Glossary

- **effect / relative-effect** — the estimated difference between arms;
  `test_type: absolute` or `relative` (percent lift) is a per-method param.
- **CI** — confidence interval on the effect; a WIN/LOSE needs it to exclude zero
  in one direction **and** be stable (excluding-then-recrossing is not a winner).
- **verdict** — the read-time call: **WIN/LOSE** (CI excludes zero, stabilized),
  **FLAT** (CI includes zero, adequately powered — needs `min_effect`),
  **INCONCLUSIVE** (otherwise / underpowered). Also **blocked** (SRM) and
  **insufficient data**. The main metric drives it; guardrails check regression.
- **SRM (sample-ratio-mismatch)** — a χ² gate (anytime-valid multinomial below 1d
  cadence): observed arm split ≠ expected split ⇒ randomization/cohort is broken.
  A **hard, blocking** gate — check it before trusting any effect.
- **CUPED** — variance reduction using a pre-period covariate; abkit renders the
  SAME metric SQL a second time over a whole-day `covariate_lookback` window (no
  extra SQL). Sized on raw variance.
- **cadence** — the spacing of cumulative cutoffs (`1d`, or a coarsening schedule
  `[{every: 1h, until: 48h}, {every: 1d}]`). Window start is PINNED; end moves.
- **horizon** — the planned end (`end_date`); fixed-horizon CIs are only valid
  here. `is_horizon` marks the row where a WIN/LOSE/FLAT may be called.
- **data_lag** — the watermark: a cutoff is pending only once late data has
  landed (`end_ts ≤ now − data_lag`). Required when cadence < 1d.
- **method_config_id** — hash of the method + its non-default **identity** params
  (`alpha` and `seed` excluded). Editing an identity param starts a NEW series
  and orphans the old rows (recompute, then `abk clean`).
- **two-tier alpha** — main vs secondary metrics get different post-correction
  (Bonferroni) alphas; read-time Benjamini-Hochberg spans a family.
- **always-valid / sequential** — opt-in (`sequential: {enabled: true}`)
  peeking-safe CIs. **Default OFF**: without it the readout WITHHOLDS WIN/LOSE
  AND FLAT before the horizon (FLAT is equally a stop decision). `scheme: alpha_spending` (group-sequential) is NOT
  implemented — a future item; it refuses cleanly with no version promise.

See `data-contract-and-reporting.md §2` (the `_ab_results` contract) and
`architecture.md` for the authoritative detail behind this summary.
