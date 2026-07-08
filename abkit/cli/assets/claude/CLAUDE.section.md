## ab-analysis-kit — declarative A/B experiment analysis

This workspace contains one or more **ab-analysis-kit** (abkit) projects. abkit is
a dbt-like Python tool for **A/B experiment analysis**: each experiment is
declarative YAML that references reusable **metrics** (YAML + SQL), run through a
`load → compute → readout` pipeline with the `abk` CLI. The statistical `compute`
stage (t/z-test, CUPED, ratio-delta, bootstrap) writes a stable results table and
a WIN / LOSE / FLAT / INCONCLUSIVE readout. A directory is an abkit project when it
contains an `abkit_project.yml` file.

**Help the user operate abkit**: create and edit experiments and metrics, choose
and tune the compute method, run the pipeline, read a readout, validate that a
method is calibrated on their data (A/A), size an experiment before launch, and
debug why a verdict is what it is. Stay numpy/SQL/YAML-first and follow the
project's existing conventions.

**Database access for _you_ (recommended, not required).** abkit itself connects to
the warehouse directly via its drivers — it **never** needs an MCP to run. But you
assist far better with **read access to the same database** (e.g. a database MCP for
the project's ClickHouse / PostgreSQL / MySQL): you can sanity-check a metric's SQL
returns one row per unit, inspect the assignment cohort, confirm arm sizes before
worrying about SRM, and read the `_ab_results` / `_ab_aa_runs` tables to explain a
verdict — instead of asking the user to run every query by hand. Without it, fall
back to the `abk explore` cockpit (which reads persisted results and recomputes
live) and to asking the user. This access is optional and separate from the
`profiles.yml` connection abkit uses for the pipeline.

### Where to look (read the matching file before answering)

The full, authoritative reference lives in `.claude/rules/ab-analysis-kit/`. These
files are installed by `abk init-claude` and track the installed abkit version —
**read the relevant one on demand** instead of guessing:

| If the task is about… | Read |
|---|---|
| What abkit is, the `load → compute → readout` pipeline, the `_ab_*` tables, glossary | `.claude/rules/ab-analysis-kit/overview.md` |
| `abk` commands, `--select`/`--metric`/`--method` selectors, `--from`/`--to`, `--full-refresh`, locks, `abk clean`, `abk test-report` | `.claude/rules/ab-analysis-kit/cli.md` |
| `abkit_project.yml`, `profiles.yml`, DB connections, statistical defaults, two-tier alpha, `notification_channels` | `.claude/rules/ab-analysis-kit/project.md` |
| An experiment YAML: variants, comparisons, cadence/horizon, SRM, the sequential toggle | `.claude/rules/ab-analysis-kit/experiments.md` |
| A reusable metric YAML: `type` (sample/fraction/ratio), column roles, one-row-per-unit SQL, CUPED covariate | `.claude/rules/ab-analysis-kit/metrics.md` |
| Choosing/tuning the compute method (t/z/CUPED/ratio-delta/bootstrap), params, `method_config_id` identity | `.claude/rules/ab-analysis-kit/methods.md` |
| The interactive explore cockpit — live knob tuning, the calibration chip, write-back (`abk explore`) | `.claude/rules/ab-analysis-kit/explore.md` |
| The A/A false-positive + power matrix — is a method calibrated on this data? (`abk validate`) | `.claude/rules/ab-analysis-kit/validate.md` |
| Pre-launch sizing — required N / achievable MDE / power (`abk plan`) | `.claude/rules/ab-analysis-kit/plan.md` |

### Skills

- **First-time setup** — use the **`abk-setup-project`** skill to configure the
  database connection in `profiles.yml` (the `abk init` placeholder ships example
  values that need your real connection details).
- **A new experiment** — use the **`abk-new-experiment`** skill; it walks the
  config out to a validated experiment YAML (variants, comparisons, method,
  cadence/horizon) that references existing metrics and is ready to run.
- **A new metric** — use the **`abk-new-metric`** skill; it scaffolds a reusable
  `metrics/<name>.yml` plus a one-row-per-unit SQL that joins the assignment cohort
  macro and validates.
- **Tune the method (hands-on, recommended)** — use the **`abk-explore`** skill: it
  serves an interactive browser **cockpit** where the user turns the method's knobs
  on their **real** persisted series and watches the cumulative effect + CI
  stabilization recompute live, with the A/A calibration always visible, then
  **Apply**s the result back into the experiment YAML in place. Prefer this whenever
  the user wants to be in the loop.
- **Check a method is calibrated (A/A)** — use the **`abk-validate`** skill: it runs
  placebo A/A splits on the experiment's own cohort and reports whether the method's
  false-positive rate ≈ α (single-look and the honest peeking rate), plus power and
  coverage, and lights the explore calibration chip. This is **not** a config lint.
- **Size before launch** — use the **`abk-plan`** skill: read-only required-N /
  achievable-MDE / achieved-power at the effective alpha, before an experiment runs.
- **Hit an abkit bug, or have feedback** — once you've ruled out a local config fix
  (see the gotchas below), use the **`abk-feedback`** skill to file a redacted bug
  report, feature request, or comment as a GitHub issue on the upstream repo. It
  auto-collects diagnostic context, strips secrets, and never submits without showing
  you the exact text first.

### Gotchas that bite (keep these in mind)

- **Check SRM before trusting any effect.** A sample-ratio-mismatch (observed arm
  split ≠ assigned split) means the randomization or the cohort query is broken;
  abkit flags it (`srm_flag`) and blocks the decision. A significant effect on top of
  an SRM-failed experiment is not trustworthy — fix the assignment first.
- **The daily series is peeking-prone; sequential is opt-in, NOT the default.** The
  cumulative fixed-horizon CIs are only valid at the planned horizon — reading them
  early and stopping ("peeking") inflates the false-positive rate. Enable
  `sequential: {enabled: true}` on a sequential-eligible method for always-valid
  (peeking-safe) CIs; by default it is **off** and the readout withholds WIN/LOSE
  before the horizon.
- **Experiment AND metric names are globally unique — one namespace.** A name is the
  database key, not the filename. An experiment cannot share a name with a metric.
- **Editing `method_params` orphans the prior results.** Method identity
  (`method_config_id`) is a hash of the method + its non-default identity params;
  changing one starts a *new* series and strands the old rows. After retuning, recompute
  and run `abk clean --select <exp>` to prune the orphaned series.
- **Every metric SQL must be one row per unit and join the cohort macro.** Import it
  with `{% import 'abkit_assignment.jinja' as ab %}` and join `ab.exposed_units(...)`;
  the loader guards one-row-per-unit and the cumulative window. Don't hand-roll the
  assignment join.
- **Sum/count metrics are additive; medians/quantiles are not.** `sample`/`fraction`
  metrics accumulate over the cumulative window; a median or quantile is not additive
  and is not a supported metric shape — model the additive quantity instead.
- **`_ab_results` is the BI contract.** It is the stable, documented table teams point
  Grafana / Lightdash / Metabase / Superset at. Read from it; abkit owns the numbers.

> Installed by `abk init-claude`. Re-run it after upgrading abkit to refresh these
> instructions and the files under `.claude/rules/ab-analysis-kit/`.
