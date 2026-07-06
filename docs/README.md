# Overview

**ab-analysis-kit** (import package `abkit`, CLI `abk`) is a Python library and
command-line tool for **A/B experiment analysis**. It is **dbt-like**: your
experiments and the reusable metrics they measure live as declarative YAML + SQL
in a project directory, and you run them with one command. The statistical core
is pure numpy (no pandas), and **ClickHouse, PostgreSQL and MySQL** are all
first-class — only the connection and the SQL dialect of your metric queries
differ between them.

abkit is the sibling of [detectkit](https://dtk.pipelab.dev): the same design DNA
(CLI-first, declarative config, self-contained reports, a chart-first tuning
cockpit) with detectkit's anomaly-`detect` stage replaced by a statistical
`compute` stage, and the primary entity flipped from *metric* to **experiment**
(architecture.md).

A directory becomes an abkit project when it contains an `abkit_project.yml`.
Database connections live in `profiles.yml`, with secrets pulled from the
environment via `${ENV}` / `env_var(...)`. The `abk` CLI **exits non-zero on
failure**, so it drops straight into an orchestrator (cron, Prefect) as the unit
of automation.

## The primary entity is the experiment

Unlike a metrics tool, abkit's primary entity is the **experiment**. One
experiment YAML declares:

- its **cohort** — the assignment/exposure source (abkit reads it; it does not
  randomize for you),
- its **variants** and the expected traffic **split**,
- its **window and cadence** (a pinned start date, a horizon end date, and how
  often cumulative cutoffs are computed),
- and a list of **comparisons** — each pointing one reusable **metric** at one
  statistical **method**.

Metrics are a shared library: a metric is its own YAML + SQL file (a
one-row-per-unit query), and many experiments can reuse the same metric
(declarative-config §2).

> **One namespace, globally unique names.** An experiment name and a metric name
> share a single namespace and are the **database key** — not the filename. An
> experiment cannot share a name with a metric.

A typical project looks like this:

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
└── seed/                  # the synthetic example dataset
```

## The pipeline: load → compute → readout

Every `abk run` executes three stages per experiment (architecture.md):

1. **load** — persists the assignment cohort once into the `_ab_exposures` table,
   then runs each metric's SQL against your source database over the cumulative
   window. Each metric query joins the cohort through the packaged assignment
   macro, so you never hand-roll the join: import it and select the exposed units,
   for example `{% import 'abkit_assignment.jinja' as ab %}` then join
   `ab.exposed_units(...)`. One row per unit goes in; additive aggregates come out
   (declarative-config §4).

2. **compute** — runs the configured statistical **method** at each planned
   cutoff, gates for **SRM** (sample-ratio mismatch), and writes one row per
   `(experiment, metric, variant-pair, method_config_id, end_ts)` to
   `_ab_results`. This is idempotent: cutoffs already computed are skipped via an
   anti-join on `end_ts`, honoring the `data_lag` completeness watermark
   (cumulative-intervals §2).

3. **readout** — the WIN / LOSE / FLAT / INCONCLUSIVE decision. It is
   recomputed at render time from `_ab_results` (never persisted), applying the
   two-tier alpha correction and the SRM / horizon gates
   (data-contract-and-reporting §5). You see it via `abk run --report`, the
   `abk explore` cockpit, and any BI tool pointed at `_ab_results`.

The statistical methods are **plugins** — twelve are registered today (`t-test`,
`paired-t-test`, `z-test`, `cuped-t-test`, `paired-cuped-t-test`, `ratio-delta`,
plus six bootstrap variants). Nothing in the pipeline special-cases a method by
name; a comparison just names the one it wants. See
[compute methods](guides/compute-methods.md).

## Reading the verdict

The readout turns a cumulative effect and its confidence interval into one of
four calls, driven by the experiment's **main metric** while guardrail metrics
watch for regressions (data-contract-and-reporting §5):

| Verdict | Meaning |
|---|---|
| **WIN** / **LOSE** | The CI excludes zero in one direction **and** is stable (excluding-then-recrossing zero is not a winner). |
| **FLAT** | The CI includes zero and the comparison is adequately powered — a real "no difference". Requires a `min_effect` on the comparison to distinguish it from noise. |
| **INCONCLUSIVE** | Anything else — most often not yet enough data / power. |

Two states can withhold a verdict entirely:

- **SRM (sample-ratio mismatch)** — a χ² gate (anytime-valid multinomial below a
  1-day cadence). If the observed arm split does not match the expected split,
  randomization or the cohort query is broken; abkit sets `srm_flag` and
  **blocks** the decision. Check SRM before trusting any effect.
- **insufficient data** — the comparison has not accumulated enough to speak.

A crucial default: **fixed-horizon CIs are only valid at the planned horizon.**
Reading the daily cumulative chart early and stopping ("peeking") inflates the
false-positive rate, so by default the readout **withholds WIN/LOSE (and FLAT)
before the horizon**. Peeking-safe, always-valid intervals are one opt-in toggle
away — `sequential: {enabled: true}` on a sequential-eligible method — after
which pre-horizon WIN/LOSE become legitimate (statistics-changes §4). See
[sequential analysis](guides/sequential.md).

## The cockpit-first workflow

The recommended way to work is hands-on and iterative:

1. **`abk init my_project`** scaffolds a project with a runnable seed example, so
   `abk run --select example_signup_test` produces real results on a fresh
   machine.
2. **`abk run`** loads and computes; add **`--report`** to emit a self-contained
   HTML readout per experiment.
3. **`abk explore --select <experiment>`** serves the interactive **cockpit** on
   localhost. You turn the method's knobs on your **real** persisted series and
   watch the cumulative effect and CI stabilization recompute live, with the A/A
   calibration status always visible. Only on an explicit **Apply** does it write
   the tuned config back into the experiment YAML (archiving the previous file
   under `experiments/.history/`). This is the primary interface — prefer it
   whenever you want to be in the loop. See [explore](guides/explore.md).
4. **`abk validate --select <experiment>`** answers "is this method actually
   calibrated on *my* data?" by running placebo **A/A** splits (label
   permutation) on the experiment's own cohort and reporting the empirical
   false-positive rate against α — both single-look and the honest
   cumulative-peeking rate — plus power and coverage (aa-false-positive-matrix
   §1). It is an audit, **not** a config lint. Its results light the explore
   cockpit's calibration chip.
5. **`abk plan --select <experiment>`** sizes an experiment **before** it runs —
   read-only required-N / achievable-MDE / achieved-power at the effective alpha.

The full command surface is `init`, `init-claude`, `run`, `explore`, `validate`,
`plan`, `unlock`, and `clean` (cli-and-dx §1). Two things that are easy to
confuse: `abk run --steps validate` is the **config lint** (no database), while
`abk validate` is the **A/A false-positive matrix**.

## Where results land: the `_ab_*` tables

abkit creates its internal state tables automatically on first run (no manual
migration). They live in the profile's `internal_database` / `internal_schema`,
separate from the `data_database` your queries read. The one to know is
**`_ab_results`** — the stable, documented **BI contract**: one row per
cumulative cutoff carrying the effect, CI, p-value, per-arm stats, SRM flag, and
horizon marker. Point Grafana / Lightdash / Metabase / Superset here; abkit owns
the numbers (data-contract-and-reporting §2). The other tables
(`_ab_exposures`, `_ab_experiments`, `_ab_aa_runs`, `_ab_tasks`,
`_ab_unit_state`) are covered in [visualizing results](guides/visualizing-results.md).

## AI-assisted onboarding

Running **`abk init-claude`** installs assistant context into your project — a
managed block in `CLAUDE.md`, reference rules under
`.claude/rules/ab-analysis-kit/`, and task skills — so an AI assistant can help
you author experiments and metrics, tune methods, and debug a verdict against
your own project. Re-run it after upgrading abkit to refresh the context.

## Where to go next

**Get running**

- [Installation](getting-started/installation.md) — install `ab-analysis-kit`
  (Python ≥ 3.10) and pick your database extras.
- [Quickstart](getting-started/quickstart.md) — `abk init && abk run` to a first
  result on the seed dataset.

**Build your project**

- [Configuration](guides/configuration.md) — `abkit_project.yml` and statistical
  defaults.
- [Databases](guides/databases.md) — `profiles.yml`, connections, and the `_ab_*`
  internal tables across ClickHouse / PostgreSQL / MySQL.
- [Experiments](guides/experiments.md) — variants, split, comparisons, cadence,
  and the horizon.
- [Metrics](guides/metrics.md) — the reusable metric YAML, metric shapes, the
  one-row-per-unit SQL, and the assignment macro.
- [Compute methods](guides/compute-methods.md) — choosing and tuning t/z/CUPED/
  ratio-delta/bootstrap methods, their params, and `method_config_id` identity.

**Run, read, and trust the numbers**

- [The explore cockpit](guides/explore.md) — live tuning and Apply.
- [Reading a readout](guides/reading-a-readout.md) — WIN/LOSE/FLAT/INCONCLUSIVE,
  SRM, and the horizon gate in depth.
- [Sequential analysis](guides/sequential.md) — always-valid CIs and safe
  peeking.
- [Validate (A/A)](guides/validate.md) — proving a method is calibrated on your
  data.
- [Plan](guides/plan.md) — pre-launch sizing.
- [Visualizing results](guides/visualizing-results.md) — the `_ab_results`
  contract and BI recipes.

**Reference**

- [CLI reference](reference/cli.md) — the authoritative command surface: every
  `abk` command, its options, the two-level selector model, and exit behavior.
- [Internal tables](reference/internal-tables.md) — the `_ab_*` schema in full,
  including the `_ab_results` BI contract, column by column.

See the [examples](examples/README.md) for end-to-end walkthroughs.
