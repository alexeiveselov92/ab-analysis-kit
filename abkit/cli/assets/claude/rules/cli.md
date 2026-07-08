# ab-analysis-kit — CLI (`abk`)

Run every command from a project directory (the one containing
`abkit_project.yml`). `abk --help` and `abk <command> --help` always work.
**`abk` exits NON-ZERO on failure** — it is the unit of automation (a Prefect
task = one `abk` invocation), so a broken run fails the job instead of exiting 0.

## Commands

| Command | Purpose |
|---|---|
| `abk init <name>` | Scaffold a project directory + a runnable example experiment |
| `abk init-claude` | (Re)install this AI context: managed `CLAUDE.md` block + `.claude/rules/ab-analysis-kit/` + `.claude/skills/` |
| `abk run --select <exp>` | Run the load → compute → readout pipeline for an experiment |
| `abk explore --select <exp>` | Serve the interactive cockpit — tune the method live, write it back (see `explore.md`) |
| `abk validate --select <exp>` | The A/A false-positive + power matrix — is a method calibrated on this data? (see `validate.md`) |
| `abk plan --select <exp>` | Read-only pre-launch sizing: required-N / achievable-MDE / power (see `plan.md`) |
| `abk unlock --select <exp>` | Clear a stuck pipeline / validate lock |
| `abk clean --select <exp>` | Prune internal rows that no longer match the config |
| `abk test-report <exp>` | Send a **mock** WIN readout through the configured notification channels — a connectivity smoke test (no lock, no warehouse read); `--channel <name>` (repeatable) / `--profile`; non-zero exit if any channel fails. See `project.md` `notification_channels` |
| `abk --version` | Show the installed abkit version |

## The two-level selector model (read this first)

Single-selector tools assume ONE selector; abkit has **two levels**, because an
experiment references reusable metrics:

- **`--select` / `-s` selects an EXPERIMENT** (`experiments/<name>.yml`). Forms:
  bare **name** (`example_signup_test` — do NOT add `.yml`), **path / glob**
  (`"experiments/checkout/*.yml"`, `"signup_*"`), **tag** (`tag:actual`), and
  `"*"` for all. Repeatable. `run`/`validate`/`plan`/`unlock`/`clean` default to
  **all experiments** when `--select` is omitted; `explore` requires exactly one.
- **`--metric <name>` selects a LIBRARY metric** within the chosen experiment(s)
  — a single metric name, never a glob. It narrows `run`-adjacent commands to one
  comparison (`explore`, `validate`, `plan`).
- **`--method <name>` (validate only) is the method-grid axis** — an extra
  registered method to score *beyond* the declared comparison. It is NOT a
  selector; do not confuse it with `--select`.

Experiment AND metric names share ONE global namespace and are the DB key —
selection/uniqueness errors name the namespace and the colliding file. `--exclude`
(on `run`) removes matches (`--select "*" --exclude "experiments/staging/*"`).

## `abk init`

```bash
abk init <name> [-d DIR] [--db-type clickhouse|postgres|mysql]
```

Scaffolds `abkit_project.yml`, `profiles.yml` (env-var secrets via
`{{ env_var('...') }}`), `experiments/`, `metrics/`, `sql/`, a synthetic `seed/`
dataset, a Prefect `runners/` example, and README — a **runnable example**
experiment (`example_signup_test` + two metrics + an assignment SQL) so
`abk run --select example_signup_test` produces real results on a fresh machine.
`--db-type` (default `clickhouse`) picks which `profiles.yml` + seed SQL to emit.
Every scaffolded file round-trips through the real config validator before init
reports success. Refuses to overwrite an existing directory.

## `abk init-claude`

```bash
abk init-claude [-d DIR]
```

Idempotently (re)writes the managed `CLAUDE.md` block, `.claude/rules/ab-analysis-kit/`,
and the `.claude/skills/`. Version-stamped — **re-run it after upgrading abkit**
to refresh this context.

## `abk run`

```bash
abk run [--select <exp>] [--exclude <sel>] [--steps validate,plan,load,compute] \
        [--from TS] [--to TS] [--full-refresh] [--workers N] \
        [--report [PATH]] [--force] [--profile NAME]
```

The pipeline: **validate → plan → load → SRM → compute → persist**, streaming
`VALIDATE → PLAN → LOAD → SRM → COMPUTE → RESULT`. It is incremental by
an anti-join — only cutoffs past the `data_lag` watermark and not already computed
are (re)computed, so re-running is idempotent.

- `--steps` (default `validate,plan,load,compute`) — comma-separated steps.
  **`--steps validate` alone is the config lint** (no DB, no lock): it parses the
  YAML, lints every metric SQL for the one-row-per-unit contract and the cohort
  macro, and instantiates each method. This is the ONLY meaning of "validate" on
  `run` — it is a *config* gate and is NOT `abk validate` (the A/A matrix).
- `--from TS` / `--to TS` — a full-refresh window (`YYYY-MM-DD` or with time, UTC);
  use with `--full-refresh`.
- `--full-refresh` — re-open already-computed cutoffs in `[--from, --to)` and
  recompute them. Use after changing a metric query or a method param.
- `--workers N` (default 1) — worker threads across experiments (each gets its own
  DB connection).
- `--report [PATH]` — after the run, emit a self-contained HTML readout per
  experiment (best-effort — never fails the run). Tri-state: bare `--report` →
  `reports/<exp>.html`; a directory → `<dir>/<exp>.html`; a `.html` path → that
  file. Reads persisted rows, so even a load-only run can produce one.
- `--force` — take over a held lock (prefer `abk unlock`; risky with concurrent runs).
- `--profile` — override `profiles.yml`'s `default_profile` (e.g. run against staging).

**SRM is a blocking gate, not a drop.** Rows are always written with `srm_flag` /
`decision_blocked`; a failed check prints a red `SRM FAILED` line and the readout
withholds a verdict. A significant effect on top of an SRM failure is not
trustworthy — fix the assignment cohort first.

## `abk explore`

```bash
abk explore --select <exp> [--metric <m>] [--no-serve] [--no-open] [--profile NAME]
```

Serves the localhost cockpit for ONE experiment (the selector must resolve to
exactly one). Reads the persisted results (run `abk run` first), tunes
`method_params` live via the real Python `from_suffstats` path, keeps the A/A
calibration chip always visible, and — only on an explicit **Apply** — writes the
tuned config back into the experiment YAML (the prior file archived under
`experiments/.history/`). `--metric` opens on a specific comparison (default: the
main metric). `--no-serve` writes a static snapshot to
`reports/<exp>__explore.html` instead of serving; `--no-open` prints the URL
without launching a browser. Takes no pipeline lock (it only edits a config file);
re-run `abk run` afterward to recompute under the new config. Full reference:
`explore.md`.

## `abk validate`

```bash
abk validate [--select <exp>] [--method <m>]... [--metric <m>] [--iterations N] \
             [--inject-effect PCT] [--scoring fpr|power|mde] [--report [PATH]] \
             [--force] [--profile NAME]
```

The A/A false-positive + power **matrix** — placebo label-permutation splits on
the experiment's OWN pooled cohort (permuting unit→arm labels destroys any true
effect ⇒ an exact null). Streams `LOAD → RESAMPLE → SCORE → PERSIST`. It measures
whether a method is actually calibrated on this data: **single-look FPR ≈ α?**, the
**honest cumulative-peeking FPR** (the optional-stopping hazard, always ≥
single-look), power, achieved MDE, and CI coverage. Persists one row per cell to
`_ab_aa_runs` at the EFFECTIVE (two-tier-resolved) alpha, which lights the explore
calibration chip.

- `--method / -m` (repeatable) — score EXTRA registered methods beyond the declared
  comparison (the method-grid axis; see the selector model above).
- `--metric` — validate only this metric (default: every declared comparison).
- `--iterations / -n` (default 2000) — placebo A/A splits per cell.
- `--inject-effect PCT` — inject a relative effect (e.g. `0.05`) to measure
  power / achieved MDE / coverage.
- `--scoring fpr|power|mde` (default `fpr`) — the objective for the "Recommended"
  row only; **all columns are always computed** regardless.
- `--report [PATH]` — self-contained HTML matrix report (best-effort; defaults to
  `reports/<exp>__validate.html`).
- `--force` — take over a held validate lock.

This is **NOT a config lint** (that is `abk run --steps validate`) and it has its
OWN out-of-band lock (`process_type='validate'`), separate from the pipeline lock.
Exits non-zero on any cell/harness failure. Full reference: `validate.md`.

## `abk plan`

```bash
abk plan [--select <exp>] [--metric <m>] [--mde PCT] [--power 0.8] [--alpha 0.05] \
         [--baseline '<metric>:mean=..,std=..,n=..']... [--profile NAME]
```

**Read-only** pre-launch sizing — no lock, no `_ab_*` writes. Reports required
sample size, achievable MDE, and achieved power **at the effective two-tier alpha**,
plus the projected look count, per comparison. Baseline per-arm moments come from
the latest persisted `_ab_results` row; a `--baseline` override sizes a greenfield
experiment (`<metric>:mean=..,std=..,n=..` for sample, `<metric>:prop=..,n=..` for
fraction; repeatable). `--mde` defaults to the comparison's `min_effect`; `--power`
/ `--alpha` default to the project/experiment values.

Only closed-form power families are sized. **Ratio metrics and bootstrap methods
are refused** (`SKIPPED` — no versioned power formula, never invented math); CUPED
is sized on the RAW persisted variance as a flagged conservative upper bound. A
by-design refusal exits zero; a genuine harness failure exits non-zero. Runtime /
ASN (days-to-N from an arrival rate) is the pre-launch timing companion to this
sizing core. Full reference: `plan.md`.

## `abk unlock`

```bash
abk unlock [--select <exp>] [--profile NAME]
```

Every run records a lock in `_ab_tasks` and clears it on exit. A run killed
mid-flight (commonly the DB restarting mid-run) leaves the lock behind, and later
runs fail with `Failed to acquire lock … Use --force`. `abk unlock` clears it
immediately without running anything. It clears **both** the pipeline lock and a
stuck `abk validate` lock for the selected experiment(s).

## `abk clean`

```bash
abk clean [--select <exp>] [--orphaned-experiments] [--execute] [--yes] [--profile NAME]
```

Editing configs over time strands rows in the internal tables. **Dry-run by
default** — pass `--execute` to actually delete.

- **Drift mode** — `abk clean --select <exp>`: for each still-existing experiment,
  deletes `_ab_results` rows whose `method_config_id` the YAML no longer produces.
  Method identity is a hash of the method + its non-default identity params, so
  **editing `method_params` orphans the prior results series** (the BI chart would
  show duplicate stabilization lines). After retuning + recompute, run this to prune
  the old series. (`seed` is identity-EXCLUDED — a re-run is byte-stable, not an orphan.)
- **GC mode** — `abk clean --orphaned-experiments`: purges all internal rows for
  experiment names present in the DB but no longer defined by any YAML (renamed or
  deleted experiments). Asks for confirmation on `--execute` unless `--yes`.

## Common workflows

```bash
# Lint configs (no DB), then run the example
abk run --steps validate
abk run --select example_signup_test

# Emit an HTML readout alongside the run
abk run --select example_signup_test --report

# Reprocess after changing a metric query or a method param
abk run --select example_signup_test --full-refresh --from 2024-07-01 --to 2024-07-15
abk clean --select example_signup_test              # dry-run: preview orphaned rows
abk clean --select example_signup_test --execute    # then prune them

# Size before launch, check calibration, tune live
abk plan   --select example_signup_test --mde 0.05
abk validate --select example_signup_test
abk explore  --select example_signup_test

# Scheduled recompute of every experiment whose `tags:` list contains "actual" (cron / Prefect)
abk run --select tag:actual

# Recover a stuck lock
abk unlock --select example_signup_test
```

## Troubleshooting

- **"Failed to acquire lock"** — a crashed run left a lock; `abk unlock --select <exp>`.
- **`SRM FAILED` (red)** — the observed arm split ≠ the expected split; the
  randomization or the cohort query is broken. Fix the assignment before trusting
  any effect.
- **No verdict before the horizon** — expected: fixed-horizon CIs are not
  peeking-valid, so the readout withholds WIN/LOSE early. Enable
  `sequential: {enabled: true}` on a sequential-eligible method for always-valid CIs.
- **`SKIPPED` in `abk plan`** — the comparison uses a ratio or bootstrap method
  (no versioned power formula) — expected, not an error.
- **Connection errors** — check `profiles.yml` and warehouse connectivity;
  `--profile` selects a non-default connection.
