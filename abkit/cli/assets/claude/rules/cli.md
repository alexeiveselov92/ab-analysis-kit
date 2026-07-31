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
| `abk dashboard` | Serve the project-level cockpit: one row per experiment, buttons that spawn `abk` commands |
| `abk validate --select <exp>` | The A/A false-positive + power matrix — is a method calibrated on this data? (see `validate.md`) |
| `abk plan --select <exp>` | Read-only pre-launch sizing: required-N / achievable-MDE / power (see `plan.md`) |
| `abk unlock --select <exp>` | Clear a stuck pipeline / validate lock |
| `abk clean --select <exp>` | Prune internal rows that no longer match the config |
| `abk verify-incremental --select <exp>` | Reconcile the incremental read path against full recompute across the whole computed series — the gate for `compute.incremental_reads`; read-only, no lock, non-zero exit on any divergence |
| `abk test-report <exp>` | Send a **mock** WIN readout through the configured notification channels — a connectivity smoke test (no lock, no warehouse read); `--channel <name>` (repeatable) / `--profile`; non-zero exit if any channel fails. See `project.md` `notification_channels` |
| `abk --version` | Show the installed abkit version |

## The two-level selector model (read this first)

Single-selector tools assume ONE selector; abkit has **two levels**, because an
experiment references reusable metrics:

- **`--select` / `-s` selects an EXPERIMENT** (`experiments/<name>.yml`). Forms:
  bare **name** (`example_signup_test` — do NOT add `.yml`), **path / glob**
  (`"experiments/checkout/*.yml"`, `"signup_*"`), **tag** (`tag:actual`), and
  `"*"` for all. Repeatable. `run`/`validate`/`plan`/`unlock`/`clean`/`dashboard`
  default to **all experiments** when `--select` is omitted; `explore` requires
  exactly one.
- **`--metric <name>` selects a LIBRARY metric** within the chosen experiment(s)
  — a single metric name, never a glob. It narrows a command to that metric's
  comparison(s) (`run`, `explore`, `validate`, `plan`, `verify-incremental`).
- **`--method <name>` (validate only) is the method-grid axis** — an extra
  registered method to score *beyond* the declared comparison. It is NOT a
  selector; do not confuse it with `--select`.

Experiment AND metric names share ONE global namespace and are the DB key —
selection/uniqueness errors name the namespace and the colliding file. `--exclude`
(on `run` and `dashboard`) removes matches
(`--select "*" --exclude "experiments/staging/*"`).

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
abk run [--select <exp>] [--exclude <sel>] [--metric <m>] \
        [--steps validate,plan,load,state,compute] \
        [--from TS] [--to TS] [--full-refresh] [--resync-cohort] [--workers N] \
        [--report [PATH]] [--force] [--profile NAME]
```

The pipeline: **validate → plan → load → SRM → state → compute → persist**,
streaming `VALIDATE → PLAN → LOAD → SRM → STATE → COMPUTE → RESULT` (`state`,
M9: materializes per-unit day moments into `_ab_unit_state` for closed-form
metrics; with the opt-in `compute.incremental_reads: true` those moments then
replace the full-window fact rescan on the compute path — any gap falls back
to recompute, and results math is unchanged either way).
It is incremental by
an anti-join — only cutoffs past the `data_lag` watermark and not already computed
are (re)computed, so re-running is idempotent.

- `--metric <m>` (0.6.0) — recompute only that metric's comparison(s); the same
  metric axis `explore`/`validate`/`plan`/`verify-incremental` take. Other
  metrics' **results** are left exactly as they are, and the **alphas do not
  move** (the two-tier scheme comes from the config, not from what one run
  computes). `--full-refresh --metric <m>` is the "reprocess just this metric"
  path. Day state is the one thing a narrowed run may still touch elsewhere,
  because a stale-but-contiguous day is invisible to the reader's gap check: a
  scoped `--full-refresh` TRUNCATES the other eligible metrics' day state from
  the first day the window touches through the end of that series (not
  re-rendered — reads fall back to recompute until a run that includes them
  re-derives it), and in copy mode `--resync-cohort` rebuilds the whole cohort so
  day state is re-materialized for every eligible metric. Both follow the `state`
  step (omit it from `--steps` and no day state is touched), both are decided PER
  EXPERIMENT, and the run prints which applies. Only compute ever narrows them. The cohort load and the SRM gate stay
  experiment-level. An experiment without that comparison is skipped with a
  printed line; a metric matching nowhere is an error.
- `--steps` (default `validate,plan,load,state,compute`) — comma-separated steps.
  **`--steps validate` alone is the config lint** (no DB, no lock): it parses the
  YAML, lints every metric SQL for the one-row-per-unit contract and the cohort
  macro, and instantiates each method. This is the ONLY meaning of "validate" on
  `run` — it is a *config* gate and is NOT `abk validate` (the A/A matrix).
- `--from TS` / `--to TS` — a full-refresh window (`YYYY-MM-DD` or with time, UTC);
  use with `--full-refresh`.
- `--full-refresh` — re-open already-computed cutoffs in `[--from, --to)` and
  recompute them. Use after changing a metric query or a method param.
- `--resync-cohort` — copy mode only (`assignment.cohort_copy.enabled: true`):
  delete the persisted `_ab_exposures` copy and rebuild it from the experiment
  start through the same incremental engine — the recovery for late-arriving/
  corrected assignment rows the watermark cannot heal. A documented no-op in
  the direct (no-copy) default. Distinct from `--full-refresh` (results-window
  recompute) — the two never overload each other.
- `--workers N` (default 1) — worker threads across experiments (each gets its own
  DB connection).
- `--report [PATH]` — after the run, emit a self-contained HTML readout per
  experiment (best-effort — never fails the run). Tri-state: bare `--report` →
  `reports/<exp>.html`; a directory → `<dir>/<exp>.html`; a `.html` path → that
  file. Reads persisted rows, so even a load-only run can produce one.
- `--cost-report` — print per-stage warehouse cost (wall-time, queries, rows returned,
  rows scanned where the backend reports them). The evidence for turning
  `compute.incremental_reads` on; unrelated to `--profile` (the DB connection).
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

## `abk dashboard`

```bash
abk dashboard [--select <sel>]... [--exclude <sel>]... [--window 24h|7d|30d|90d|all] \
              [--no-open] [--profile NAME]
```

The project-level cockpit (0.6.0): one row per selected experiment — headline
verdict, effect, p-value, last look and a sparkline — served on localhost with a
per-start token that authorizes EVERY request, `GET` included. The page boots on
metadata only and fills each row on demand (3 requests in flight), so a
hundred-experiment project renders instantly.

**A launcher, not a worker.** It runs no pipeline step, computes no statistic,
takes **no pipeline lock** and writes no config: verdicts come from the same
`readout.evaluate()` decision `abk run --report` bakes, and the Run / Explore /
Unlock / Clean buttons each spawn a real `abk` subprocess (inheriting
`--profile`), streaming its log into a job drawer. `run`/`unlock`/`clean` are
one-at-a-time project-wide (a second request is refused — they contend for the
pipeline lock); `explore` is exempt and deduped per experiment. Ctrl-C stops the
server and terminates every job it spawned.

- `--window` (default `30d`) bounds the **sparkline only** — every verdict,
  effect and p-value is the FULL cumulative series' (dropping the oldest look
  would truncate a stabilization history, not shorten the experiment). Switchable
  on the page.
- `--exclude` removes matches from a broad `--select`, as on `abk run`.
- Configs are read ONCE at boot: restart after editing an experiment YAML.
- A project that has never run serves fine — every row reads `no data — press
  Run`, and nothing here creates internal schema.
- **Read-only YAML.** `Show YAML` prints the file + its path for you to open in
  your editor; there is no save endpoint. The config-writing surface is
  `abk explore`'s Apply.
- **The URL is a credential** — whoever has it can spawn `abk run`/`abk clean`
  in your project. Localhost-bound; do not share it or forward the port.

An empty selection warns and exits 0 without serving; an unknown `--window` is a
non-zero startup error, raised before the port is bound. Full reference:
https://abkit.pipelab.dev/guides/dashboard/.

## `abk validate`

```bash
abk validate [--select <exp>] [--method <m>]... [--metric <m>] [--iterations N] \
             [--family-sweep] [--inject-effect PCT] [--scoring fpr|power|mde] \
             [--report [PATH]] [--force] [--profile NAME]
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
- `--iterations / -n` — placebo A/A splits per cell (default: auto,
  `max(2000, ⌈200/α⌉)` at each cell's effective alpha; an explicit N overrides
  every cell).
- `--family-sweep` — also run the composed multi-metric FWER/FDR sweep (D9);
  roughly doubles the cost. Opt-in since 0.2.0 (it used to auto-run whenever
  `--metric` was omitted).
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
- **State sweep** — runs alongside drift mode, no flag: drops `_ab_unit_state`
  series no live `(experiment, metric)` pair claims (a removed comparison, a renamed
  metric, a deleted experiment, or a comparison that stopped being state-eligible).
  A normal run only drops series superseded by an edit to a metric it still
  materializes. Deliberately NOT narrowed by `--select` — state rows are keyed by
  `(source_table, column_set_id)`, not by experiment.

## `abk verify-incremental`

```bash
abk verify-incremental [--select <exp>] [--metric <m>] [--rel-tol 1e-9] [--profile NAME]
```

The gate before turning `compute.incremental_reads` on (and the drift detector
after). For EVERY already-computed cutoff of every state-eligible comparison it
loads the data both ways — `_ab_unit_state` day moments vs a full-window fact
rescan — and diffs the numbers field by field. Whole-series by design: a drift
that accumulates over many days cannot hide behind a green latest cutoff.

Read-only and lock-free; never part of `abk run` (it costs more than the run it
checks). Outcomes: **matched** (agree within `--rel-tol`), **DIVERGED** (prints
the offending fields, exits non-zero — usually an event backfilled into an
already-materialized day later than `data_lag`; heal with `abk run --full-refresh
--from/--to`), and **unverified** (the incremental read fell back to recompute for
that cutoff, so both sides ran the same code — reported separately, never counted
as a pass).

## Common workflows

```bash
# Lint configs (no DB), then run the example
abk run --steps validate
abk run --select example_signup_test

# Emit an HTML readout alongside the run
abk run --select example_signup_test --report

# Reprocess after changing a metric query or a method param
abk run --select example_signup_test --full-refresh --from 2024-07-01 --to 2024-07-15
# ...or only the metric that changed (the other series keep their rows)
abk run --select example_signup_test --metric example_arpu \
        --full-refresh --from 2024-07-01 --to 2024-07-15
abk clean --select example_signup_test              # dry-run: preview orphaned rows
abk clean --select example_signup_test --execute    # then prune them

# Size before launch, check calibration, tune live
abk plan   --select example_signup_test --mde 0.05
abk validate --select example_signup_test
abk explore  --select example_signup_test

# Watch the whole portfolio (and drive it) from one page
abk dashboard --select tag:actual

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
