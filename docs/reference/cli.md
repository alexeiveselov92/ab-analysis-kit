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

One command carries a second name: **`abk ui` is an alias for `abk dashboard`** — the
same Click callback registered twice, so the two can never drift. It gets its own entry
below, pointing at the canonical one.

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
  `"*"` for all. Repeatable. `run`, `validate`, `plan`, `unlock`, `clean`, and
  `dashboard` / `ui` default to **all experiments** when `--select` is omitted; `explore`
  requires the selection to resolve to **exactly one**.
- **`--metric <name>` selects a LIBRARY metric** within the chosen experiment(s) — a
  single metric name, never a glob. It narrows a command to that metric's comparison(s)
  (`run`, `explore`, `validate`, `plan`, `verify-incremental`).
- **`--method <name>` (validate only) is the method-grid axis** — an extra registered
  method to score *beyond* the declared comparison. It is not a selector; do not
  confuse it with `--select`.

Experiment and metric names share one global namespace and are the database key, so
selection and uniqueness errors name the namespace and the colliding file. `--exclude`
(on `run` and `dashboard` / `ui`) removes matches from a broad selection
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
state → compute → persist.

```bash
abk run [--select <exp>]... [--exclude <sel>]... [--metric <m>] \
        [--steps validate,plan,load,state,compute] \
        [--from TS] [--to TS] [--full-refresh] [--resync-cohort] [--workers N] \
        [--report [PATH]] [--notify] [--force] [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | all experiments | Experiment selector (repeatable) |
| `--exclude` | — | Selectors to remove from the selection (same forms) |
| `--metric` | every declared comparison | Recompute only this metric's comparison(s) |
| `--steps` | `validate,plan,load,state,compute` | Comma-separated pipeline steps |
| `--from` | — | Full-refresh window start (with `--full-refresh`) |
| `--to` | — | Full-refresh window end, exclusive (with `--full-refresh`) |
| `--full-refresh` | off | Re-open already-computed cutoffs in `[--from, --to)` and recompute |
| `--resync-cohort` | off | Copy mode only: delete the persisted cohort and rebuild it from the experiment start through the incremental engine |
| `--workers` | `1` | Worker threads across experiments (each gets its own DB connection) |
| `--report [PATH]` | off | Emit a self-contained HTML readout per experiment |
| `--notify` / `--no-notify` | off | Push each **completed** experiment's readout to the configured `notification_channels` — the same `readout.evaluate()` decision the report bakes, never recomputed. Best-effort: a failing channel is a yellow line, never a non-zero exit. Route it per experiment with a `notify:` block ([guide](../guides/notification-channels.md)) |
| `--cost-report` | off | Print per-stage warehouse cost (wall-time, queries, rows returned, rows scanned where the backend reports them), plus the **day-additive slice** of COMPUTE and what the other read path would have done with it — the evidence for turning `compute.incremental_reads` on. Unrelated to `--profile`. |
| `--force` | off | Take over a held lock (use with care) |
| `--profile` | `profiles.yml` `default_profile` | Connection profile to use |

The run is **incremental by an anti-join**: only cutoffs past the `data_lag`
watermark and not already computed are (re)computed, so re-running is idempotent. Use
`--full-refresh` with both `--from` and `--to` to reprocess a window after changing a
metric query or a method param.

With `assignment.cohort_copy.enabled`, the persisted cohort is loaded
**incrementally** (watermark + grid-anchored closed-interval batches): the
still-open `batch_interval` bucket and rows younger than `maturity_delay` wait
until they mature, and a row backfilled *below* the watermark is not picked up
by routine runs — that is the documented cost of the copy. `--resync-cohort`
recovers such a copy by deleting it and rebuilding from the experiment start
**through the same engine** — the re-scan picks the backfilled rows up, while
the rebuild still honors the closed/matured discipline (units inside the
still-open or maturing bucket return on a later run, never half-fresh). It
never touches results windows (`--full-refresh` keeps that job) and is a no-op
in the direct (no-copy) default.

**`--metric` narrows the run to one metric** (since 0.6.0) — the same metric axis
`explore`/`validate`/`plan`/`verify-incremental` already take. It filters comparisons by
metric *name*: inside one experiment that resolves to exactly one comparison (a metric
binds at most once per experiment), and across a broader `--select` every experiment
declaring that metric is recomputed. The cohort load and the SRM gate stay
experiment-level (the gate must still block). `--full-refresh --metric <m>` is the
"recompute just this metric" path after a SQL or method-param edit: it deletes and
rebuilds only that metric's series and leaves every other series' **results** exactly as
they were. Three properties are worth knowing:

- **The alphas do not move.** The two-tier scheme is derived from the config's
  comparison list, not from what one invocation computes, so a filtered run writes
  byte-identical `alpha` values (a pinned test).
- **Day state is the one thing a narrowed run may still touch elsewhere**, because a
  stale-but-contiguous `_ab_unit_state` day is invisible to the reader's gap check (it
  only detects absence). So a scoped `--full-refresh` **truncates** the other eligible
  metrics' day state — from the first day the window touches *through the end of that
  series* (the same tail semantics an unfiltered refresh has) — instead of leaving it. The
  truncated days are not re-rendered (you scoped that cost away): reads past the
  truncation fall back to full-window recompute, and the next run that includes the metric
  re-derives them from the current facts. Without this, a fact backfill healed for one
  metric only would make a later routine run silently persist an undercount for the others
  whenever `compute.incremental_reads` is on. It follows the `state` step: a run whose
  `--steps` omits `state` touches no day state at all, and the run says which of the three
  outcomes applies (naming the experiments when a selection mixes them).
- **In copy mode, `--resync-cohort` is not per-metric.** The cohort belongs to the
  experiment, so it is rebuilt whole and day state is re-materialized for *every*
  eligible metric (the run prints a line saying so) — narrowing that would leave the
  other metrics' day state derived from the copy the resync just declared poisoned. Only
  the compute stays narrow. In the direct (no-copy) default the flag is a no-op, so day
  state narrows with everything else.
- An experiment in the selection that does not declare the metric is **skipped with a
  printed line**; a value matching nowhere is an error naming what is declared. The run
  also prints which comparisons it is withholding. Since the readout reads persisted
  rows, a `--report` after a filtered run still covers every metric — the untargeted ones
  simply show the numbers they already had.

**`--steps` tokens** are `validate`, `plan`, `load`, `state`, `compute` (any unknown
token errors with the valid list). The `state` step (M9) materializes per-unit,
per-day moments for closed-form metrics into `_ab_unit_state`; with
`compute.incremental_reads` off nothing reads them, and skipping the step
skips only that write, never a result. With `incremental_reads: true` eligible
comparisons read those moments instead of re-scanning the fact window, so
skipping `state` there just means the reads fall back to full recompute
(reported as a warning) until the series catches up. **`--steps validate` alone is the config lint** — it
parses the YAML, lints every metric SQL for the one-row-per-unit contract and the
cohort macro, and instantiates each method, all with no database and no lock. This is
the only meaning of "validate" on `run`; it is a *config* gate and is **not**
`abk validate` (the A/A matrix). Because the lint never touches the DB — and lints the
whole project by construction — combining `--steps validate` with `--report` or
`--metric` is rejected.

**`abk run` tells you when the fast path would pay off.** If a comparison is on
the additive contract — the metric declares `state_additive: true` AND the
comparison is closed-form, unstratified and has no explicit covariate, so the
`state` step materializes its day moments on every run — but
`compute.incremental_reads` is **not written anywhere**, the run
prints a warning once the series reaches six looks: the write is being paid for
and the read is not, which is the one configuration strictly worse than either
choice. Writing the flag **either way** records the decision and silences it;
`false` is a legitimate answer (see
[configuration](../guides/configuration.md#the-compute-block) for what it
actually guards). Two related disclosures: with the flag **on** but no metric
declaring `state_additive`, the run says the flag is doing nothing; and when
eligible reads **fell back** to full recompute, it reports how many looks did so
(the per-reason warnings above it say why).

Add `--cost-report` for the numbers behind that: under the `compute:` line it
prints `of which day-additive:` — the same measured cost, attributed to the
eligible comparisons only — and then what the other path would do with it. The
slice is **part of** the `compute` total, not a sibling; never add them.

**`--report` is tri-state** (the donor's flag shape): omit it for no report; a bare
`--report` writes `reports/<experiment>.html`; a directory value writes
`<dir>/<experiment>.html`; a `.html` path value writes exactly that file (which is
rejected when more than one experiment is selected). The readout reads persisted rows,
so it is emitted even when zero cutoffs were pending — the "just give me the report"
path. Report emission is **best-effort**: a report failure yellow-skips and never
fails the run (the one recorded exception to the exit-non-zero rule).

**`--notify` is best-effort on the same terms**, and it is the only other flag
that reads rows back after the pipeline (both share one connection). It fires
on a **completed** experiment (the verdicts `readout.evaluate()` returns, one
message per verdict) and on a **failed** one (an error notice carrying the
reason, with no statistics block — nothing was measured). `locked` and `skipped`
stay silent. Messages go to the channels an experiment's `notify:` block selects,
or to all configured channels when it has none. A **failed SRM gate** does not
add a message: the readout already built answers to the `srm` kind as well, so an
`on: [srm, error]` channel hears about a broken split without receiving routine
readouts. Every completed run sends: verdict-change dedup is the next NTF work
package. See the
[notification-channels guide](../guides/notification-channels.md).

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

## `abk dashboard`

Serve the project-level cockpit — one row per experiment, buttons that spawn `abk`
commands, and CRUD editing of the experiment YAML (m11 plan DASH-6; the editor is UI-1).
Also available as **`abk ui`**.

```bash
abk dashboard [--select <sel>]... [--exclude <sel>]... [--window 24h|7d|30d|90d|all] \
              [--no-open] [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | all experiments | Experiment selector (repeatable) |
| `--exclude` | — | Selector(s) to remove from `--select` |
| `--window` | `30d` | Initial sparkline window: `24h`, `7d`, `30d`, `90d`, `all` |
| `--no-open` | off | Do not launch a browser (the URL still prints) |
| `--profile` | `default_profile` | Connection profile — and the one every spawned command inherits |

The page boots on **metadata only** and fills each row on demand (three requests in
flight at a time) from the persisted `_ab_results`, so the verdicts are the same
`readout.evaluate()` decisions `abk run --report` bakes. `--window` bounds the
**sparkline only** — every verdict, effect and p-value is always the full cumulative
series', and can be switched on the page without a restart.

**The dashboard is a launcher, not a worker.** It computes no statistic and takes **no
pipeline lock**: the Run / Explore / Unlock / Clean buttons each spawn a real `abk`
subprocess (inheriting `--profile`) and stream its log into a job drawer.
`run`/`unlock`/`clean` are one-at-a-time across the project (a second request is
refused); `explore` is exempt and deduped per experiment. Ctrl-C terminates every job
the dashboard spawned. The YAML editor below is the one thing the server writes itself,
and it does not weaken that invariant: a config is the operator's own declaration, not a
result — no number on the page comes from it, and it cannot block a pipeline.

**Editing configs.** A row's **Edit YAML** button opens the experiment file in a
textarea with **Save**, **Revert** and **Delete…**; the header adds **New experiment**
(a seeded template, plus an optional subfolder of `paths.experiments`) and **Reload
configs**. A save is **validate → archive → write**: both validation levels run first —
the pydantic `ExperimentConfig` parse *and* `validate_experiment_level2`, the same §8
matrix `abk run --steps validate` applies (reference integrity, the CUPED rules, the
cadence/looks gates over the real grid, the no-DB SQL render smoke) — then the previous
file is archived byte-verbatim under `.history/<name>/` beside it (the same archive tree
`abk explore`'s Apply writes), and only then is the new text written
atomically. **The text round-trips verbatim**: comments and layout survive, normalized
only to end with a newline. That is the difference from `abk explore`'s Apply, which
re-emits a parsed document and loses comments — and the reason the editor is not built
on it.

A save or a delete is refused, with the reason in the panel, when:

- **the file changed under you** — a read hands out a sha256 digest that the write echoes
  back, so a second browser tab, or an `abk explore` Apply *this dashboard spawned* on
  the experiment you are editing, is caught instead of clobbered (the digest is checked
  before the text is parsed: a stale buffer has to be reopened either way);
- **a job is running** on that experiment — a run / unlock / clean / explore this
  dashboard started. One started from a terminal is invisible to the check; the digest is
  what catches its explore half, after the fact;
- **level-2 findings** — unless you press **Save anyway**, which downgrades the §8 errors
  to loud warnings (`SAVED WITH AN ERROR — abk run will refuse this: …`) so the editor is
  usable on a project that does not lint yet. **Level 1 is never forceable**, and no
  button offers it.

Two more refusals have no override: a file too large to display (>512 kB) is served
read-only and cannot be saved at all (writing the shown prefix back would drop the tail),
and a **duplicate name** — checked across the whole project, over the one
experiment + metric namespace — is rejected, as is a create whose file already exists.

**A rename is allowed** (edit the YAML's `name:`): the file keeps its path, the archive
is keyed by the *old* name, and the reply warns that persisted rows still carry it
(`abk clean --orphaned-experiments`). **Delete removes the YAML only** — the rows in
`_ab_results` / `_ab_unit_state` survive until that same `abk clean` prunes them, and the
archived copy (`<name>-<stamp>-deleted.yml`) makes the delete reversible by hand. A new
experiment that lands outside this cockpit's `--select` is reported as such, never
silently missing.

**Every successful edit re-reads the project.** The server re-resolves the selection it
was started with, re-bakes the boot page and hands back the refreshed list, so an edit
takes effect without a restart. **Reload configs** is the manual form of the same thing,
for a YAML changed outside the page. A reload that *fails* (a broken sibling YAML, a name
collision) keeps the previous selection and rides back as a warning — the write has
already landed, so it never turns into a 500. Note that **`Refresh` re-reads the
warehouse** for row data while **`Reload configs` re-reads the YAML on disk**; they are
different buttons on purpose. Nothing here creates internal schema: a project that has
never run serves fine, with every row reading `no data — press Run`.

**Security:** binds `127.0.0.1` only, with a fresh token per start that authorizes
**every** request, `GET` included (the row reads hit your warehouse; the boot page
enumerates your experiments). The token rides in the URL, not in the page. Treat that
URL as a credential — whoever holds it can spawn `abk run` / `abk clean` and edit or
delete your experiment YAML.

**Exit behavior:** an empty selection prints the unmatched-selector warning and exits 0
without serving; an unknown `--window` is a non-zero error raised at startup, before the
port is bound. Ctrl-C is the normal exit (0). Full guide:
[dashboard](../guides/dashboard.md).

## `abk ui`

An alias for [`abk dashboard`](#abk-dashboard) — the *same* Click callback registered
under a second name, so the two take exactly the same options, defaults, help text and
exit behavior, and cannot drift apart.

```bash
abk ui [--select <sel>]... [--exclude <sel>]... [--window 24h|7d|30d|90d|all] \
       [--no-open] [--profile NAME]
```

`dashboard` stays the canonical name — `dashboard` and `explore` say *which* surface you
are opening, where `ui` does not. The alias exists because the donor tool calls its
project-level cockpit `dtk ui` (its per-metric sibling, `dtk tune`, is abkit's
`abk explore`).

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
         [--baseline '<metric>:mean=..,std=..,n=..']... [--from-history <N d>] \\
         [--arrival-rate N] [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | all experiments | Experiment selector (repeatable) |
| `--metric` | every declared comparison | Plan only this comparison |
| `--mde` | the comparison's `min_effect` | Target minimum detectable effect (must be > 0) |
| `--power` | project default | Target power (must be in `(0, 1)`) |
| `--alpha` | experiment / project alpha | Experiment-level significance before correction (must be in `(0, 1)`) |
| `--baseline` | — | Baseline moments override for a greenfield metric (repeatable, see below) |
| `--from-history` | — | Derive baselines from the N whole days before the start (e.g. `14d`) — population-wide; loses to `--baseline`, wins over persisted rows |
| `--arrival-rate` | derived from the cohort source (persisted copy or a live re-render) | Total units/day across arms, for the runtime (days-to-N) + always-valid ASN estimates (must be > 0) |
| `--profile` | `default_profile` | Connection profile to use |

Reports required sample size, achievable MDE, and achieved power **at the effective
two-tier alpha**, plus the projected look count and cost shape, per comparison.
Baseline per-arm moments come from the latest persisted `_ab_results` row for the
control / first-treatment pair; a `--baseline` override sizes an experiment with no
persisted data. The override format is `<metric>:mean=..,std=..,n=..[,corr=..]` for a sample
metric and `<metric>:prop=..,n=..` for a fraction metric.

Only the closed-form power families are sized. **Ratio metrics and bootstrap /
resampling methods are refused** (reported as `SKIPPED` — they have no versioned power
formula, and abkit never invents math; measure their power empirically with
`abk validate --inject-effect`). CUPED is sized on the **deflated** variance from the
baseline row's own persisted covariate correlation (`corr_coef_1`, stored since
`0.4.0`) — required-N is `(1 − ρ²)×` the raw-variance number — and every line names the
variance it used; a row without a usable ρ (pre-`0.4.0`, or a correlation so close to
±1 that the residual variance is rounding noise) keeps the raw bound and says so.
`--baseline <metric>:…,corr=0.6` supplies ρ for an experiment that has never run, and
is accepted only on a comparison whose method applies a covariate. When an arrival rate is available — derived read-only from the cohort source
(the persisted `_ab_exposures` copy under `assignment.cohort_copy.enabled`, otherwise a
live re-render of the assignment SQL at invocation time — the documented no-copy
cost/freshness tradeoff) or supplied via `--arrival-rate` — `plan` also reports the
**runtime** (days to reach the
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

Two modes (plus one always-on sweep):

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
- **State sweep** (runs alongside drift mode, no flag): drops `_ab_unit_state`
  series no live `(experiment, metric)` pair claims — a removed comparison, a
  renamed metric, a deleted experiment, or a comparison that stopped being
  state-eligible. A normal `abk run` already drops series superseded by an edit to
  a metric it still materializes; only this sweep can reach the rest. It is
  deliberately **not** narrowed by `--select`: state rows are keyed by
  `(source_table, column_set_id)`, not by experiment, so pruning under a narrow
  selection could delete another experiment's live series.

**Exit behavior:** prints `DRY RUN` and changes nothing unless `--execute` is given;
exits non-zero on a database error.

## `abk verify-incremental`

Reconcile the incremental read path against full recompute — the gate for turning
`compute.incremental_reads` on.

```bash
abk verify-incremental [--select <exp>]... [--metric <m>] [--rel-tol <x>] [--profile NAME]
```

| Option | Default | Meaning |
|---|---|---|
| `--select`, `-s` | all experiments | Experiment selector (repeatable) |
| `--metric` | every eligible comparison | Verify only this metric |
| `--rel-tol` | `1e-9` | Relative tolerance for the per-field diff |
| `--profile` | `default_profile` | Connection profile to use |

For **every already-computed cutoff** of every state-eligible comparison it loads
the data both ways — `_ab_unit_state` day moments vs a full-window fact rescan —
and diffs the resulting numbers field by field (effect, bounds, p-value, per-arm
value/std/size, CUPED moments, diagnostics). Whole-series by design: a drift that
only accumulates after many days cannot hide behind a green latest cutoff.

Read-only and lock-free — it persists nothing, so it never races a running
pipeline. It costs strictly more than the run it checks, so it is an explicit
maintainer command and never part of `abk run`.

Three outcomes per pair comparison:

- **matched** — the two paths agree within `--rel-tol`.
- **DIVERGED** — they disagree; the command prints the offending fields and
  **exits non-zero**. The usual cause is an event backfilled into an
  already-materialized day later than `data_lag` (the documented incremental-read
  limitation); `abk run --full-refresh --from/--to` re-materializes and heals it.
- **unverified** — the incremental read fell back to recompute for that cutoff
  (a state gap), so both sides ran the same code and agreement proves nothing.
  Reported separately, never counted as a pass. Run the `state` step so the
  series covers those days, then verify again.

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

# ...or reprocess only the metric whose SQL changed (the others keep their rows)
abk run --select example_signup_test --metric example_arpu \
        --full-refresh --from 2024-07-01 --to 2024-07-15
abk clean --select example_signup_test            # dry-run preview
abk clean --select example_signup_test --execute  # prune the old series

# Size before launch, check calibration, tune live
abk plan     --select example_signup_test --mde 0.05
abk validate --select example_signup_test
abk explore  --select example_signup_test

# Watch the whole portfolio, and drive it from one page
abk dashboard --select tag:actual

# Scheduled recompute of every experiment whose tags list contains "actual"
abk run --select tag:actual

# Recover a stuck lock
abk unlock --select example_signup_test
```

The `abk run --select tag:actual` invocation is the scheduled-recompute path; the
scaffolded Prefect example in `runners/` (from `abk init`) wraps exactly that call on a
daily cadence (cli-and-dx §3).
