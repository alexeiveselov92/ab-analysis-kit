# Quickstart

Get from an empty directory to a real A/B readout in about five minutes. `abk
init` scaffolds a project with a **fully runnable example** — a synthetic seed
dataset, an example experiment, and two example metrics — so `abk run` produces
real numbers on a fresh machine instead of a placeholder-table error
(cli-and-dx §6).

This page assumes ClickHouse (the default backend), because the scaffolded seed
dataset ships as ClickHouse SQL. If you already have your own warehouse, the
same three steps apply once you point `profiles.yml` at your data — see
[databases](../guides/databases.md) and [configuration](../guides/configuration.md).

> **Prerequisites:** Python ≥ 3.10 and abkit installed (`pip install
> ab-analysis-kit`). See [installation](installation.md).

## 1. Scaffold a project

```bash
abk init my_ab_project
cd my_ab_project
```

`abk init <name>` creates a new directory (it refuses to overwrite an existing
one) and writes a complete, valid project. Every scaffolded config file is
round-tripped through abkit's real config models and the level-2 validator
before `init` reports success, so the example cannot drift from the code
(declarative-config §5).

```
my_ab_project/
├── abkit_project.yml                  # project settings + statistical defaults
├── profiles.yml                       # database connections (secrets via env vars)
├── .gitignore
├── README.md
├── experiments/
│   └── example_signup_test.yml        # the example experiment (the PRIMARY entity)
├── metrics/
│   ├── example_signup_cr.yml          # a fraction metric (z-test)
│   └── example_arpu.yml               # a sample metric (CUPED)
├── sql/
│   └── example_assignment.sql         # the read-only exposure source
├── seed/
│   └── seed_dataset.clickhouse.sql    # the synthetic dataset to load
└── runners/
    ├── prefect_flow.py                # a runnable Prefect flow example
    └── prefect.yaml                   # a Prefect 3 deployment example
```

Pick a different backend with `--db-type {clickhouse,postgres,mysql}` (default
`clickhouse`); that only changes what `profiles.yml` is scaffolded for. Install
somewhere other than the current directory with `--target-dir DIR`.

### What the example is

`experiments/example_signup_test.yml` is an onboarding experiment with a
`control`/`treatment` split and two comparisons:

- **`example_signup_cr`** — signup conversion, a `fraction` metric scored with
  `z-test` (marked `is_main_metric: true`, so it drives the verdict and the
  two-tier Bonferroni budget).
- **`example_arpu`** — revenue per user, a `sample` metric scored with
  `cuped-t-test` (variance-reduced against a pre-period covariate via the
  `covariate_lookback` method param — declarative-config §3).

The dates are pinned in the past (`start_date: 2024-07-01`, `end_date:
2024-07-14`, `cadence: 1d`), so a first run immediately computes the full
14-point cumulative stabilization series for both metrics.

Each metric is one YAML file plus a SQL query that returns **one row per unit**
and joins the persisted exposure cohort through the packaged assignment macro
(`{{ ab.exposed_units() }}`). You never re-implement the windowing or dedup —
the macro does it. See [metrics](../guides/metrics.md) and
[experiments](../guides/experiments.md) for the anatomy.

## 2. Load the seed dataset

The example runs against a small synthetic dataset (600 users, a 50/50 split,
14 experiment days plus a 14-day pre-period for the CUPED covariate). Load it
into ClickHouse:

```bash
clickhouse-client --multiquery < seed/seed_dataset.clickhouse.sql
```

This creates and fills `analytics.example_ab_assignments` and
`analytics.example_signup_events`. It is deterministic — reloading reproduces
identical numbers.

Before touching the database at all, you can lint every config file (macros,
method params, look-count gates) with a pure, no-DB check:

```bash
abk run --steps validate
```

This is the config linter, **not** the A/A `abk validate` matrix (that is a
different command — see [validate](../guides/validate.md)). It exits non-zero on
any problem, so it is safe to wire into CI.

## 3. Run the pipeline

```bash
abk run --select example_signup_test
```

`abk run` executes the pipeline `validate → plan → load → SRM → compute →
persist`, streaming a stage line per step (`VALIDATE → PLAN → LOAD → SRM →
COMPUTE → RESULT`). It always lints the config first, then echoes the
**effective per-comparison alphas** — the two-tier Bonferroni scheme resolves
the main metric and the secondary metrics to different budgets, and the run
prints exactly what it applied so you never compute alpha by hand
(declarative-config §6).

Results are written to `_ab_results` in your internal database (for the
scaffolded ClickHouse `dev` profile, `abkit_internal._ab_results`) — the
stable, BI-friendly contract table, one row per `(experiment, metric,
variant-pair, method_config_id, cutoff)`. Re-running is idempotent: cutoffs
that were already computed are skipped.

Inspect the raw numbers directly:

```sql
SELECT metric, end_date, effect, pvalue, left_bound, right_bound
FROM abkit_internal._ab_results
WHERE experiment = 'example_signup_test'
ORDER BY metric, end_ts;
```

Common flags:

| Flag | Effect |
|---|---|
| `--select`, `-s` | Experiment selector: a name, path glob, `tag:<tag>`, or `*` (repeatable; default all). |
| `--exclude` | Selectors to exclude (same forms). |
| `--steps` | Comma-separated pipeline steps (default `validate,plan,load,state,compute`); `validate` alone = no-DB config lint. |
| `--profile` | Connection profile (default: `profiles.yml`'s `default_profile`), e.g. `--profile prod`. |
| `--report` | Emit a self-contained HTML readout after the run (see below). |
| `--full-refresh` `--from` `--to` | Re-open and recompute already-computed cutoffs inside `[--from, --to)`. |
| `--workers` | Worker threads across experiments (default 1). |
| `--force` | Take over a held run lock (use with care). |

### Get the HTML readout

Add `--report` to emit a self-contained HTML page per experiment after its
pipeline finishes:

```bash
abk run --select example_signup_test --report
```

`--report` is tri-state (cli-and-dx §1):

- bare `--report` → writes `reports/<experiment>.html` under the project root;
- `--report <dir>/` → writes `<dir>/<experiment>.html`;
- `--report path.html` → writes that exact file (only valid when one experiment
  is selected).

The report is best-effort: a report failure yellow-skips and **never** fails
the run (the one recorded exception to the non-zero-exit contract). The readout
recomputes the verdict at render time from `_ab_results` — **WIN / LOSE / FLAT /
INCONCLUSIVE** per comparison, plus the SRM gate, which withholds results and
prints a loud `SRM FAILED` line when the observed split disagrees with the
declared one. Because emission happens even when zero cutoffs were pending,
re-running an up-to-date experiment with `--report` is the "just give me the
report" path.

For how to read those verdicts, the stabilization chart, and the SRM gate, see
[reading a readout](../guides/reading-a-readout.md).

## 4. Explore interactively

```bash
abk explore --select example_signup_test
```

`abk explore` serves the localhost **cockpit**: the cumulative effect + CI
stabilization chart with live chips (lift, CI half-width, p-value, power, the
A/A calibration chip, the SRM flag), and a side rail of method knobs you can
retune live. It reads the persisted `_ab_results` rows and recomputes locally —
it takes no pipeline lock and never writes to the warehouse; only an explicit
**Apply** writes a tuned config back to the experiment YAML. Run `abk run` first: with
no persisted rows it prints a friendly noop telling you to.

`--select` must resolve to **exactly one** experiment. Useful flags:

- `--metric <name>` — open on a specific comparison (default: the main metric).
- `--no-open` — serve but don't launch a browser (the URL still prints).
- `--no-serve` — write a static read-only snapshot to
  `reports/<experiment>__explore.html` instead of serving.
- `--profile <name>` — a non-default connection.

## Next steps

- [Explore cockpit](../guides/explore.md) — live method tuning and write-back.
- [Reading a readout](../guides/reading-a-readout.md) — verdicts, the
  stabilization chart, the SRM gate, and what each number means.
- [Experiments](../guides/experiments.md) and [metrics](../guides/metrics.md) —
  replace the example with your own once the demo runs.
