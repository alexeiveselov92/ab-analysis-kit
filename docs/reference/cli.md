# CLI reference

`abk` is the ab-analysis-kit command-line interface: a dbt-like command group over
your declarative experiment and metric YAML. Run every command from a project
directory — the one containing `abkit_project.yml`. This page documents each shipped
command, its options (as defined in the code), and its exit behavior.

```bash
abk --version          # print the installed abkit version
abk --help             # list commands
abk <command> --help   # options for one command
```

**`abk` exits non-zero on failure.** This is deliberate (and a recorded deviation
from the detectkit donor's swallow-and-return-0 behavior, cli-and-dx §1): the CLI is
the unit of automation — a Prefect task or cron job is one `abk` invocation, so a
broken run fails the job instead of silently exiting 0. Command-specific exit rules
are noted per entry below.

**Lazy command group.** The Click group imports each command body lazily, so
`abk --version` and `abk --help` stay instant and no database driver is loaded until
a command actually needs one. You can install abkit without a DB extra and still lint
configs (`abk run --steps validate`) or scaffold a project.

## The two-level selector model

Read this first — it is the one place abkit diverges from single-selector tools.
Because an experiment references reusable metrics, selection has **two levels** plus a
validate-only method axis:

- **`--select` / `-s` selects an EXPERIMENT** (`experiments/<name>.yml`). Accepted
  forms: a bare **name** (`example_signup_test` — do not add `.yml`), a **path or
  glob** (`"experiments/checkout/*.yml"`, `"signup_*"`), a **tag** (`tag:actual`), and
  `"*"` for all. Repeatable. `run`, `validate`, `plan`, `unlock`, and `clean` default
  to **all experiments** when `--select` is omitted; `explore` requires the selection
  to resolve to **exactly one**.
- **`--metric <name>` selects a LIBRARY metric** within the chosen experiment(s) — a
  single metric name, never a glob. It narrows a command to one comparison (`explore`,
  `validate`, `plan`).
- **`--method <name>` (validate only) is the method-grid axis** — an extra registered
  method to score *beyond* the declared comparison. It is not a selector; do not
  confuse it with `--select`.

Experiment and metric names share one global namespace and are the database key, so
selection and uniqueness errors name the namespace and the colliding file. `--exclude`
(on `run`) removes matches from a broad selection
(`--select "*" --exclude "experiments/staging/*"`).

See [experiments](../guides/experiments.md) and
[configuration](../guides/configuration.md) for how these names are declared.

---

## `abk init`

Scaffold a new project directory with a runnable example experiment.

```bash
abk init <project_name> [-d DIR] [--db-type clickhouse|postgres|mysql]
```

| Option | Default | Meaning |
|---|---|---|
| `project_name` (argument) | — | Directory name to create |
| `--target-dir`, `-d` | `.` | Where to create the project |
| `--db-type` | `clickhouse` | Which `profiles.yml` connection template to emit (`clickhouse`, `postgres`, `mysql`); the seed dataset always ships as ClickHouse SQL |

Creates `abkit_project.yml`, `profiles.yml` (env-var secrets via
`{{ env_var('...') }}`), `experiments/`, `metrics/`, `sql/`, a synthetic `seed/`
dataset (shipped as ClickHouse SQL regardless of `--db-type`), a Prefect
`runners/` example, and a `README.md`. The scaffolded
`example_signup_test` experiment (a z-test fraction metric plus a CUPED sample metric)
runs against the seed dataset so `abk run --select example_signup_test` produces real
results on a fresh machine (cli-and-dx §6). Every scaffolded file round-trips through
the real config validator before init reports success.

**Exit behavior:** refuses to overwrite an existing directory (non-zero). A scaffold
that fails its own validation is an abkit bug and exits non-zero.

## `abk init-claude`

Install (or refresh) AI-assistant context for operating this project.

```bash
abk init-claude [-d DIR]
```

| Option | Default | Meaning |
|---|---|---|
| `--target-dir`, `-d` | `.` | Directory to install the Claude context into |

Idempotently writes three things (cli-and-dx §5): a managed block in `CLAUDE.md`
(delimited by HTML-comment markers, so your own content is preserved), the reference
rules under `.claude/rules/ab-analysis-kit/`, and the `abk-*` skills under
`.claude/skills/`. The source is packaged with the wheel, so the context matches the
installed version — **re-run this after upgrading abkit** to refresh it. Re-running
with no upstream change reports everything unchanged.

**Exit behavior:** succeeds idempotently; no database, no lock.

## `abk run`

Run the pipeline for the selected experiments: validate → plan → load → SRM →
compute → persist.

```bash
abk run [--select <exp>]... [--exclude <sel>]... [--steps validate,plan,load,compute] \
        [--from TS] [--to TS] [--full-refresh] [--workers N] \
        [--report [PATH]] [--force] [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | all experiments | Experiment selector (repeatable) |
| `--exclude` | — | Selectors to remove from the selection (same forms) |
| `--steps` | `validate,plan,load,compute` | Comma-separated pipeline steps |
| `--from` | — | Full-refresh window start (with `--full-refresh`) |
| `--to` | — | Full-refresh window end, exclusive (with `--full-refresh`) |
| `--full-refresh` | off | Re-open already-computed cutoffs in `[--from, --to)` and recompute |
| `--workers` | `1` | Worker threads across experiments (each gets its own DB connection) |
| `--report [PATH]` | off | Emit a self-contained HTML readout per experiment |
| `--force` | off | Take over a held lock (use with care) |
| `--profile` | `profiles.yml` `default_profile` | Connection profile to use |

The run is **incremental by an anti-join**: only cutoffs past the `data_lag`
watermark and not already computed are (re)computed, so re-running is idempotent. Use
`--full-refresh` with both `--from` and `--to` to reprocess a window after changing a
metric query or a method param.

**`--steps` tokens** are `validate`, `plan`, `load`, `compute` (any unknown token
errors with the valid list). **`--steps validate` alone is the config lint** — it
parses the YAML, lints every metric SQL for the one-row-per-unit contract and the
cohort macro, and instantiates each method, all with no database and no lock. This is
the only meaning of "validate" on `run`; it is a *config* gate and is **not**
`abk validate` (the A/A matrix). Because the lint never touches the DB, combining
`--steps validate` with `--report` is rejected.

**`--report` is tri-state** (the donor's flag shape): omit it for no report; a bare
`--report` writes `reports/<experiment>.html`; a directory value writes
`<dir>/<experiment>.html`; a `.html` path value writes exactly that file (which is
rejected when more than one experiment is selected). The readout reads persisted rows,
so it is emitted even when zero cutoffs were pending — the "just give me the report"
path. Report emission is **best-effort**: a report failure yellow-skips and never
fails the run (the one recorded exception to the exit-non-zero rule).

The effective per-comparison alphas (the inspectable two-tier Bonferroni scheme —
main metric vs the rest, declarative-config §6) are echoed before compute.

**SRM is a blocking gate, not a drop** (data-contract §6): rows are always written
with `srm_flag` / `decision_blocked`, a failed check prints a red `SRM FAILED` line,
and the readout withholds a verdict. A significant effect on top of an SRM failure is
untrustworthy — fix the assignment cohort first.

**Exit behavior:** exits non-zero if any selected experiment failed. A held lock
reports the experiment as `locked` (use `abk unlock` or `--force`); an empty selection
is a clean no-op (exit 0).

## `abk explore`

Serve the interactive explore cockpit for one experiment (cli-and-dx §2).

```bash
abk explore --select <exp> [--metric <m>] [--no-serve] [--no-open] [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | — (must match exactly one) | Experiment selector |
| `--metric` | the main metric | Open the cockpit on this comparison |
| `--no-serve` | off | Write a static snapshot to `reports/<exp>__explore.html` instead of serving |
| `--no-open` | off | Do not launch a browser (the URL still prints) |
| `--profile` | `default_profile` | Connection profile to use |

Reads the persisted results (run `abk run` first), lets you tune `method_params` live
against a localhost page through the real Python `from_suffstats` path, keeps the A/A
calibration chip always visible, and — only on an explicit **Apply** — writes the
tuned config back into the experiment YAML (the prior file is archived under
`experiments/.history/`). It takes no pipeline lock (it only edits a config file); after
an Apply, re-run `abk run` to recompute the new series. `--metric` must name a
configured comparison of the experiment.

**Exit behavior:** the selection must resolve to exactly one experiment (otherwise a
non-zero error naming the matches). A never-run project is a friendly no-op (exit 0)
telling you to `abk run` first. Other failures exit non-zero. Full guide:
[explore](../guides/explore.md).

## `abk validate`

Score each method's empirical false-positive rate on placebo A/A splits — the A/A
false-positive + power matrix (aa-false-positive-matrix). It is **not** a config linter.

```bash
abk validate [--select <exp>]... [--method <m>]... [--metric <m>] [--iterations N] \
             [--family-sweep] [--inject-effect PCT] [--scoring fpr|power|mde] \
             [--report [PATH]] [--force] [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | all experiments | Experiment selector (repeatable) |
| `--method`, `-m` | — | Extra registered method(s) to score beyond the declared comparison (repeatable) |
| `--metric` | every declared comparison | Validate only this metric |
| `--iterations`, `-n` | auto: `max(2000, ⌈200/α⌉)` per cell | Placebo A/A splits per cell, resolved at each cell's effective alpha (≈4000 at 5%, ≈40000 at 0.5%); an explicit N overrides every cell |
| `--family-sweep` | off | Also run the composed multi-metric FWER/FDR sweep — roughly doubles the cost (opt-in since 0.2.0; it previously always ran when `--metric` was omitted) |
| `--inject-effect` | none | Inject this relative effect (e.g. `0.05`) to measure power / achieved MDE / coverage |
| `--scoring` | `fpr` | Selection objective for the "Recommended" row (`fpr`, `power`, `mde`) |
| `--report [PATH]` | off | Emit a self-contained HTML matrix report (best-effort) |
| `--force` | off | Take over a held validate lock (use with care) |
| `--profile` | `default_profile` | Connection profile to use |

Draws N placebo A/A splits over the experiment's own pooled cohort (permuting
unit→arm labels destroys any true effect, giving an exact null), and scores per cell:
whether each method is actually calibrated on this data (single-look **FPR ≈ α?**), the
honest cumulative-**peeking** FPR (the optional-stopping hazard, always ≥ single-look),
power, achieved MDE, and CI coverage. It streams `LOAD → RESAMPLE → SCORE → PERSIST` —
a distinct vocabulary from `abk run`'s config-lint `validate` step. Results persist one
row per cell to `_ab_aa_runs` at the **effective (two-tier-resolved) alpha**, which
lights the explore calibration chip. `--scoring` sets only the "Recommended" row's
objective; all columns are always computed regardless.

Validate has its **own out-of-band lock** (`process_type='validate'`), separate from
the pipeline lock and cleared by `abk unlock`. `--report` defaults to
`reports/<exp>__validate.html` and is best-effort.

**Exit behavior:** exits non-zero on any failed cell or harness error; an empty
selection is a clean no-op (exit 0). Full guide: [validate](../guides/validate.md).

## `abk plan`

Read-only pre-launch power / sample-size planner (cli-and-dx §1). No lock, no writes.

```bash
abk plan [--select <exp>]... [--metric <m>] [--mde PCT] [--power P] [--alpha A] \
         [--baseline '<metric>:mean=..,std=..,n=..']... [--arrival-rate N] [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | all experiments | Experiment selector (repeatable) |
| `--metric` | every declared comparison | Plan only this comparison |
| `--mde` | the comparison's `min_effect` | Target minimum detectable effect (must be > 0) |
| `--power` | project default | Target power (must be in `(0, 1)`) |
| `--alpha` | experiment / project alpha | Experiment-level significance before correction (must be in `(0, 1)`) |
| `--baseline` | — | Baseline moments override for a greenfield metric (repeatable, see below) |
| `--arrival-rate` | derived from `_ab_exposures` | Total units/day across arms, for the runtime (days-to-N) + always-valid ASN estimates (must be > 0) |
| `--profile` | `default_profile` | Connection profile to use |

Reports required sample size, achievable MDE, and achieved power **at the effective
two-tier alpha**, plus the projected look count and cost shape, per comparison.
Baseline per-arm moments come from the latest persisted `_ab_results` row for the
control / first-treatment pair; a `--baseline` override sizes an experiment with no
persisted data. The override format is `<metric>:mean=..,std=..,n=..` for a sample
metric and `<metric>:prop=..,n=..` for a fraction metric.

Only the closed-form power families are sized. **Ratio metrics and bootstrap /
resampling methods are refused** (reported as `SKIPPED` — they have no versioned power
formula, and abkit never invents math; measure their power empirically with
`abk validate --inject-effect`). CUPED is sized on the raw persisted variance (the
covariate correlation is not persisted per row) and flagged as a conservative upper
bound. When an arrival rate is available — derived read-only from `_ab_exposures` or
supplied via `--arrival-rate` — `plan` also reports the **runtime** (days to reach the
required N) and, for a `sequential.enabled` design, the always-valid **ASN** (expected /
average sample number, horizon-capped); without arrival data both are skipped.

**Exit behavior:** a by-design refusal (`SKIPPED`) exits **zero** — it is expected,
not an error. A genuine harness failure (bad selection, a malformed `--baseline`, or a
warehouse error) exits non-zero. Invalid `--alpha` / `--power` / `--mde` values are
rejected as bad parameters.

## `abk unlock`

Clear stale pipeline locks left by a run that died.

```bash
abk unlock [--select <exp>]... [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | all experiments | Experiment selector (repeatable) |
| `--profile` | `default_profile` | Connection profile to use |

Every run records a lock in `_ab_tasks` and clears it on exit. A run killed
mid-flight (commonly the database restarting mid-run) can leave the lock behind, so
later runs fail with a "Failed to acquire lock" message. `abk unlock` clears it
immediately without running anything, and clears **both** the pipeline (`run`) lock and
a stuck `abk validate` lock for each selected experiment.

**Exit behavior:** exits non-zero if clearing a lock errored; an empty selection is a
clean no-op.

## `abk clean`

Prune internal rows that no longer match the config. **Dry-run by default.**

```bash
abk clean [--select <exp>]... [--orphaned-experiments] [--execute] [--yes] [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | all experiments | Experiment selector (repeatable) |
| `--orphaned-experiments` | off | Purge experiments that have DB rows but no YAML in the project |
| `--execute` | off | Apply the changes (default is a dry run) |
| `--yes` | off | Skip the per-experiment purge confirmation |
| `--profile` | `default_profile` | Connection profile to use |

Two modes:

- **Drift mode** (`abk clean --select <exp>`): for each still-existing experiment,
  deletes `_ab_results` rows whose `method_config_id` the current YAML no longer
  produces. Method identity is a hash of the method plus its non-default identity
  params, so **editing `method_params` orphans the prior results series** (the BI chart
  would show duplicate stabilization lines). After retuning and recompute, run this to
  prune the old series. (`seed` is identity-excluded — a bootstrap re-run is
  byte-stable, not an orphan.)
- **GC mode** (`abk clean --orphaned-experiments`): purges all internal rows for
  experiment names present in the DB but no longer defined by any YAML. It asks for
  confirmation per experiment on `--execute` unless `--yes` is passed.

**Exit behavior:** prints `DRY RUN` and changes nothing unless `--execute` is given;
exits non-zero on a database error.

## `abk test-report`

Send a **mock** readout through the configured notification channels — a
connectivity / formatting smoke test. **No lock, no warehouse read, no statistics**:
it builds a synthetic WIN readout for the experiment and pushes it to the channels
declared in `profiles.yml` `notification_channels:` (see the
[configuration guide](../guides/notification-channels.md)).

```bash
abk test-report <experiment> [--channel NAME]... [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `EXPERIMENT` | required | The experiment name to stamp on the mock readout |
| `--channel` | all configured | Send only to these channels (repeatable) |
| `--profile` | `default_profile` | Connection profile whose `notification_channels` to use |

Prints a per-channel ✓/✗ line and **exits non-zero if any channel fails or is
misconfigured** — so you can wire it into CI before trusting an orchestrator to
deliver real readouts. Supported channel types: `slack`, `mattermost`, `webhook`,
`telegram`, `email`.

## Common workflows

```bash
# Lint configs (no DB), then run the runnable example
abk run --steps validate
abk run --select example_signup_test

# Emit an HTML readout alongside the run
abk run --select example_signup_test --report

# Reprocess after changing a metric query or a method param, then prune orphans
abk run --select example_signup_test --full-refresh --from 2024-07-01 --to 2024-07-15
abk clean --select example_signup_test            # dry-run preview
abk clean --select example_signup_test --execute  # prune the old series

# Size before launch, check calibration, tune live
abk plan     --select example_signup_test --mde 0.05
abk validate --select example_signup_test
abk explore  --select example_signup_test

# Scheduled recompute of every experiment whose tags list contains "actual"
abk run --select tag:actual

# Recover a stuck lock
abk unlock --select example_signup_test
```

The `abk run --select tag:actual` invocation is the scheduled-recompute path; the
scaffolded Prefect example in `runners/` (from `abk init`) wraps exactly that call on a
daily cadence (cli-and-dx §3).
