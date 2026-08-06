# Experiments

In abkit the **experiment is the primary entity**. One YAML file declares an
experiment: where its arm assignment comes from, its variants and expected
split, the cumulative analysis window, the cadence, and a list of
`comparisons` that bind reusable [metrics](metrics.md) to statistical
[methods](compute-methods.md). Everything else — the pipeline, the readout, the A/A
matrix, the planner — is driven off this file.

An experiment lives under your project's `experiments/` directory (one file per
experiment; files may nest into subdirectories). It is identified by its `name`
field, which is the database key and must be globally unique across the project
(experiments and metrics share one namespace). The filename is not the key.

The authoritative field list is the pydantic model in
`abkit/config/experiment_config.py`; the governing contract is
declarative-config §2. This page documents every field that model accepts.

## Minimal experiment

The shortest valid experiment declares the window, the unit key, an assignment
source, and at least one main comparison:

```yaml
name: pricing_test
start_ts:   2024-07-01
horizon_ts: 2024-07-15
unit_key:   user_id
assignment:
  query_file: sql/assignment.sql
  variants: [control, treatment]
  expected_split: {control: 0.5, treatment: 0.5}
comparisons:
  - metric: signup_cr
    is_main_metric: true
    method: {name: z-test, params: {test_type: relative}}
```

Validate it without touching the database:

```bash
abk run --steps validate --select pricing_test
```

`--steps validate` runs the config lint alone (cross-file reference checks, SQL
render checks, the look-count gates) and never reads or writes the warehouse.
This is not the same as `abk validate`, which is the A/A false-positive matrix —
see [Validate](validate.md).

## Full anatomy

```yaml
name: signup_redesign_v2         # required — globally unique DB key (alnum / _ / -)
description: "Onboarding redesign for the signup funnel"   # optional
status: running                  # design | running | concluded | archived (default: running)
is_actual: true                  # catalog flag persisted to _ab_experiments (default: true)
tags: [growth, onboarding]       # optional — selectable via --select tag:<tag>

start_ts:   2024-07-01           # required — PINNED left edge of every cumulative window
horizon_ts: 2024-07-15           # required — the horizon, EXCLUSIVE (also drives the power plan)
unit_key:   user_id              # required — randomization + default analysis unit

cadence: 1d                      # cumulative cutoff step (default: "1d")
interval_anchor: midnight        # where the cutoffs land (default: midnight)
data_lag: 0                      # completeness watermark; REQUIRED when cadence < 1d
timezone: UTC                    # interprets bare-date edges + the day lattice (default: UTC)

assignment:                      # READ-ONLY exposure source — abkit never randomizes
  query_file: sql/assignment.sql # or inline `query:` — exactly one, never both
  added_filters: ""              # optional extra SQL fragment (must start with AND)
  variants: [control, treatment] # the FIRST is control (name_1) unless `control:` says otherwise
  # control: control             # OPTIONAL: which arm is the baseline (default: variants[0])
  expected_split: {control: 0.5, treatment: 0.5}   # per variant, sum to 1.0; drives SRM
  # cohort_copy: {enabled: true} # opt-in persisted _ab_exposures copy (default OFF — see below)

alpha: 0.05                      # experiment-level significance (unset -> project default)
correction: bonferroni           # none | bonferroni | benjamini_hochberg | holm (unset -> project default)
contrasts: all_pairs             # all_pairs (default) | vs_control (only control-vs-treatment)
sequential: {enabled: false, scheme: always_valid}   # opt-in peeking-safe CIs (default OFF)
# incremental_reads: true        # override project.compute.incremental_reads for this experiment
                                 # (unset -> project default). Changes HOW a number is computed
                                 # (state moments vs a full-window rescan), never the number.

readout:                         # READ-TIME verdict knobs — never enter method_config_id
  stabilization_days: 7          # trailing elapsed-days window for persistent significance (default: 7)
  guardrail_policy: block        # block (default) | warn

# notify:                        # optional routing for `abk run --notify` (see the
#   channels: [team_slack]       # notification-channels guide). Routing only: with no block, a
#   mentions: [growth-team]      # notified run sends to every configured channel
#   on: [readout]                # signal kinds this experiment sends (default: all)

comparisons:                     # required — each binds one metric to one method
  - metric: signup_cr
    is_main_metric: true
    min_effect: 0.01
    desired_direction: increase
    method: {name: z-test, params: {test_type: relative, calculate_mde: true}}
  - metric: arpu
    method: {name: cuped-t-test, params: {test_type: relative, covariate_lookback: 14d}}
  - metric: crash_rate
    is_guardrail: true
    desired_direction: decrease
    method: {name: z-test, params: {test_type: relative}}
```

A file may also nest everything under a top-level `experiment:` key; the flat
form above is the norm.

## Identity and catalog fields

- **`name`** (required) — the DB key. Only alphanumerics, `_`, and `-`; capped
  at the storage key budget. Unique across the experiment/metric namespace.
- **`description`** (optional) — free text.
- **`status`** — one of `design`, `running`, `concluded`, `archived` (default
  `running`). A catalog label; it does not by itself gate execution.
- **`is_actual`** (default `true`) — a catalog flag persisted to
  `_ab_experiments`. It records whether scheduled runs should pick the
  experiment up; abkit does not itself filter `abk run` on it (selection is by
  name / tag / glob).
- **`tags`** (optional list) — selectable on the CLI with `--select tag:<tag>`
  (e.g. `--select tag:growth`).

## The cumulative window: `start_ts`, `horizon_ts`, cadence

The analysis window is **pinned-start, moving-end**. `start_ts` never moves;
each cutoff advances the end through the horizon, producing the stabilization
series — one point per cutoff, with the effect and CI computed over
`[start_ts .. cutoff]`.

- **`start_ts`** / **`horizon_ts`** (required, a `date` **or** a `datetime`) —
  the pinned left edge and the horizon. A bare date is shorthand for local
  midnight of that day in `timezone`; a full timestamp
  (`2024-07-01 14:30:00`) is that exact instant, with no midnight snap.
  `horizon_ts` is **EXCLUSIVE**: the window is `[start_ts, horizon_ts)`, so an
  experiment running July 1..14 inclusive sets `horizon_ts: 2024-07-15`. It
  must be after `start_ts`, and it drives the power plan and the sequential
  pre-horizon rule below.
- **`unit_key`** (required) — the randomization unit and the default analysis
  unit. Each bound metric's own `unit_key` must match (or inherit) this.
- **`timezone`** (default `UTC`) — a valid IANA zone; it interprets bare-date
  edges, an explicit `interval_anchor`, and the DST-safe day lattice. Storage
  and comparison are always UTC.

### `cadence`

The cutoff step (default `"1d"`). Two forms:

- A **duration scalar** — `"1h"`, `"30m"`, `"1d"`, or an integer number of
  seconds. It must parse to whole seconds ≥ 1s and be no longer than the
  horizon.
- A **coarsening schedule** — a dense-early list of segments. Each segment is
  `{every: <step>, until: <bound>}`, where `until` is measured from the start.
  The list must be strictly coarsening (each `every` longer than the last) with
  strictly increasing `until` bounds; only the last segment may omit `until`
  (it then runs to the horizon):

```yaml
cadence:
  - {every: 1h, until: 48h}    # hourly for the first two days
  - {every: 1d}                # daily thereafter
```

A scalar and the equivalent single-segment schedule produce identical grids
(cumulative-intervals §6).

### `interval_anchor`

`cadence` says how far apart the cutoffs are; `interval_anchor` says **where
that lattice sits**. The rule is one sentence: cutoffs are
`anchor + k × cadence`, kept strictly after `start_ts`.

| value | meaning |
|---|---|
| `midnight` (the default when the key is absent) | local midnight of the day the window opens — whole calendar days, which is what daily BI rollups read |
| `start` | count from the start instant: a 14:00 start gives 14:00 cutoffs |
| an explicit timestamp | align the grid to an external cycle |

The `abk init` scaffold writes `interval_anchor: midnight` out explicitly, so
the choice is visible in the config rather than implicit.

Two properties worth knowing:

- **The anchor may precede `start_ts`.** That is what makes the explicit form
  useful — e.g. 3-day windows that must close at 00:00 Moscow time on a UTC
  warehouse, for an experiment that started mid-cycle. The forward snap makes
  it well-defined, and the **first window is then legitimately partial** (a
  config-lint note tells you so; it is not an error).
- **`midnight` anchors to the experiment's own opening day**, not to a global
  calendar cycle. Two experiments starting a day apart get interleaved 3-day
  lattices under `cadence: 3d`. When the cycle must be shared across
  experiments, name it with the explicit form.

Day-or-coarser steps hold the anchor's **local wall-clock time** across a DST
transition (a local day is 23 h or 25 h, never a fixed 86 400 s); sub-day steps
advance in absolute duration, as they always have.

### `data_lag`

The completeness watermark: a cutoff is analyzed only once
`end_ts <= now() - data_lag`. It is **required when the densest cadence is below
`1d`** (declare your ingestion SLA). At daily cadence the default `0` reproduces
the legacy whole-day-excluding-today behavior. Accepts a duration string or `0`.

## Assignment: variants, split, and SRM

`assignment` names the **read-only** exposure source. abkit does not randomize —
it reads your existing assignment and joins metric SQL against it directly,
live, on every invocation (the default, no-copy mode — nothing is persisted).
If your assignment source is expensive to join repeatedly or can mutate under
you between reads, opt into `assignment.cohort_copy` (below) to persist an
incrementally-updated copy into `_ab_exposures` instead.

- **`query_file`** or **`query`** — the assignment SQL. Provide exactly one
  (both, or neither, is an error). The query must SELECT `unit_key`, `variant`,
  and an exposure timestamp (`exposure_ts`), optionally a `stratum`
  (declarative-config §8).
- **`added_filters`** (optional) — an extra SQL fragment appended to the
  packaged cohort WHERE clause. If set, it must start with `AND`. This is the
  only escape hatch for extra conditions.
- **`variants`** (required) — the arm names. At least two, unique, non-empty,
  within the key budget. **The first variant is the control (`name_1`)** unless
  `control` names another arm.
- **`control`** (optional) — which arm is the baseline. Must be one of
  `variants`; unset, it is the first declared variant, exactly as through
  `0.8.0`. There is no project-level default: the baseline a surface measures
  against must not depend on whether that surface resolved a project config.

  Declaring the first variant is the default written out and changes nothing.
  Declaring a **different** arm on a running experiment does four things, and
  `abk run --steps validate` warns about all of them:

  1. every pair containing it is stored as `(control, other)`, so the rows
     persisted under the previous orientation leave the declared contrast set
     and every read surface drops them with a warning;
  2. the re-oriented pair's effect is measured against the other arm — on the
     absolute scale the negation of the old number, but **on the relative scale
     it is not**, because the denominator swaps arms too (`(m₂−m₁)/m₁` becomes
     `(m₁−m₂)/m₂`). `test_type: relative` is the common configuration, so do
     not expect a chart to simply mirror;
  3. the next plain `abk run` recomputes the whole series — no `--full-refresh`
     needed, since no look carries the re-oriented pair. Until it does, the
     read-time correction families are built from the surviving rows only, so a
     verdict read in between can be looser than the one that settles;
  4. the previous orientation's rows are never deleted. abkit's own surfaces
     drop them; raw SQL over `_ab_results` sees both orientations (join
     `_ab_experiments.control` to tell them apart).

  Prefer reordering `variants` over declaring a non-first control on an
  experiment that has already produced rows.
- **`expected_split`** (required) — the expected assigned share per arm. One
  entry per variant, each a fraction strictly in `(0, 1)`, summing to `1.0`. It
  is the input to the **SRM gate**.

The SRM gate is a chi-square test (anytime-valid multinomial below `1d` cadence)
of observed arm sizes against `expected_split`. It is **blocking but
non-dropping**: on failure the rows are still written with `srm_flag` set and
`decision_blocked`, the CLI prints a red `SRM FAILED` line, and no verdict is
trusted. A failing SRM means the assignment or randomization is broken — fix
that before believing any effect.

### Persisting the cohort: `assignment.cohort_copy` (opt-in)

By default abkit re-reads and re-validates your live assignment source on every
invocation (`abk run`/`plan`/`validate`/`explore`) — always fresh, never a stale
row, at the cost of one render + validation query each time. If your assignment
SQL is expensive (a heavy multi-join) or its source keeps growing during the
run window, opt into a persisted, incrementally-updated copy:

```yaml
assignment:
  query_file: sql/assignment.sql
  variants: [control, treatment]
  expected_split: {control: 0.5, treatment: 0.5}
  cohort_copy:
    enabled: true                       # default false — nothing persisted
    update_column: exposure_ts          # watermark column (the default)
    batch_interval: 1d                  # grid-anchored closed-interval step
    batch_intervals_per_round_trip: 30  # intervals (NOT rows) per DB round trip
    maturity_delay: 0                   # withhold rows younger than now() - delay
```

With copy mode on, `abk run` appends only the newly-matured closed batches
since the last watermark — append-only, never a delete + reinsert on a routine
run. A custom `update_column` has no persisted watermark to resume from and
re-scans from the experiment start every run (still batched). The copy engine
injects its batch bounds through the `{{ ab_added_filters }}` hook, so in copy
mode your assignment SQL **must** reference it (config-lint enforces this).

> **Known limitation (copy mode).** The watermark only moves forward: a row
> backfilled or corrected into an **already-scanned closed bucket** is silently
> missed by the incremental copy. For a source that backfills or mutates
> historical rows, either stay on the no-copy default (which re-reads
> everything, so it never misses a late row) or recover the copy with
> `abk run --resync-cohort` — it deletes the persisted copy and rebuilds it
> from the experiment start through the same engine. In copy mode the SRM gate
> still measures the **live** source, and the persisted copy metrics join
> trails it by the open bucket + `maturity_delay`; `abk run` warns when a
> computable cutoff exceeds the copy's coverage (align
> `data_lag >= maturity_delay + batch_interval`).

## Comparisons: metric × method

`comparisons` (required, at least one) is the heart of the file. Each entry
binds one library metric to one statistical method.

- **`metric`** (required) — references `metrics/<name>.yml` by its `name`. Each
  metric may be bound **at most once** per experiment.
- **`is_main_metric`** (default `false`) — a primary winner criterion. **At
  least one comparison must set this to `true`.** Main metrics drive the verdict
  and get the tighter tier of the two-tier Bonferroni correction.
- **`is_guardrail`** (default `false`) — the metric is checked for regression
  only, never for winning. `is_main_metric` and `is_guardrail` cannot both be
  true on the same comparison.
- **`method`** (required) — `{name, params}`. See below.
- **`min_effect`** (optional, must be `> 0`) — the business-meaningful effect,
  in the units of this comparison's persisted effect (which depends on the
  method's `test_type`). It enables the **FLAT** verdict; without it, a flat
  result cannot be distinguished from an underpowered one.
- **`desired_direction`** — `increase` (default) or `decrease`. Which effect
  sign is *good* for this metric. It orients WIN vs LOSE for main metrics and
  the regression check for guardrails.

`min_effect` and `desired_direction` are **read-time verdict inputs**: they are
not method parameters and never enter `method_config_id`, so editing them does
not orphan the results series.

### Selecting a method

`method.name` must be a registered method; `method.params` are that method's
parameters. The twelve registered methods are:

| Family | Names |
|---|---|
| Parametric | `t-test`, `paired-t-test`, `z-test`, `cuped-t-test`, `paired-cuped-t-test`, `ratio-delta` |
| Bootstrap | `bootstrap`, `paired-bootstrap`, `poisson-bootstrap`, `paired-poisson-bootstrap`, `post-normed-bootstrap`, `paired-post-normed-bootstrap` |

Methods are plugins — nothing in the pipeline special-cases a method name. Each
method's parameter schema (for example `test_type`, `calculate_mde`,
`n_samples`, `stratify`, `covariate_lookback`) is documented on the
[Methods](compute-methods.md) page; an unknown name or a bad parameter fails at
validate/plan time, never mid-run.

**`method_config_id`** is a hash of the method name plus its non-default
*identity* parameters (declarative-config §7). `alpha` is post-correction,
experiment-level, and is deliberately **not** part of this identity. Editing an
identity-bearing parameter changes the id and **orphans the prior results
series** — recompute, then prune with `abk clean --select <experiment>`.

**CUPED covariate.** For `cuped-t-test` / `paired-cuped-t-test`, the covariate
is set with the method param `covariate_lookback` (e.g. `14d`). The loader
renders the same metric SQL a second time over the pre-period window
`[start_ts - lookback, start_ts)` and uses that pre-period value as the
covariate (declarative-config §3). Units absent from the pre-period default to
`0`.

## Alpha and multiple-testing correction

- **`alpha`** (optional, in `(0, 1)`) — the experiment-level significance. If
  unset it falls back to the project statistics default (see
  [Project setup](configuration.md#the-statistics-block)).
- **`correction`** (optional) — `none`, `bonferroni`, `benjamini_hochberg`, or
  `holm`. Unset falls back to the project default. Bonferroni is applied as an
  inspectable **two-tier** scheme (main metrics get a tighter alpha than
  secondary metrics); Benjamini-Hochberg (FDR) and Holm (FWER) are **read-time**
  rules applied across the experiment's metrics at every read — the rows keep
  the raw alpha, and under them a verdict may legitimately differ from the
  interval stored beside it (declarative-config §6; the trade-offs are in
  [Project setup](configuration.md#correction)).

- **`guardrail_correction`** (optional, project or experiment) — `inherit`
  (default, the pre-`0.8.0` scheme: a guardrail shares the secondary tier) or
  `none` (the guardrail is tested at the **raw** alpha and leaves the secondary
  divisor, which also loosens alpha for the screening metrics that remain). A
  guardrail exists to catch harm, so correcting it makes you *less* likely to
  catch it — but the flip moves persisted numbers, so it is opt-in
  (declarative-config §6.1).
- **`contrasts`** (optional, experiment level) — `all_pairs` (default: every
  variant pair is compared and corrected for) or `vs_control` (only the `g−1`
  contrasts against the **control arm** — `assignment.control`, defaulting to
  the first declared variant). Declaring the narrower
  family multiplies every tier's alpha by `g/2` — ≈ +10 points of power at four
  arms — because you stop paying for treatment-vs-treatment comparisons you
  never make. Under `vs_control` those pairs are *not computed at all*: they
  get no result rows and no verdicts (declarative-config §6.2).

```yaml
contrasts: vs_control            # all_pairs (default) | vs_control
```

`abk run`, `abk validate`, and the HTML report all echo the effective
per-comparison alpha and the divisor it was derived from — `C(variants, 2) ×
metrics`, or `(variants − 1) × metrics` under `contrasts: vs_control` — so the
applied correction is never hidden.

## Sequential analysis (opt-in, default off)

Fixed-horizon confidence intervals are only valid **at** the planned horizon.
Reading the daily series early and stopping ("peeking") inflates the
false-positive rate. Accordingly:

- With `sequential.enabled: false` (the default), the readout **withholds
  WIN / LOSE and FLAT before the horizon** — the pre-horizon series is
  informational only.
- Set `sequential: {enabled: true}` on a sequential-eligible method to get
  **always-valid (peeking-safe) confidence sequences**, so WIN / LOSE can be
  read before the horizon (statistics-changes §4). Toggling `enabled` re-plans
  the series; a bare `abk run` re-plans it for you.
- **`scheme`** defaults to `always_valid`, which is the only implemented scheme.
  `scheme: alpha_spending` (group-sequential) is **not implemented** and is
  rejected cleanly at validation time — it is a named future item. Use
  `always_valid`.

## Running, sizing, and validating

Once the file validates, drive it through the CLI (see [CLI](../reference/cli.md) for the
full flag list):

```bash
abk run --select signup_redesign_v2            # validate -> plan -> load -> SRM -> compute -> persist
abk run --select signup_redesign_v2 --report   # + a self-contained HTML readout per experiment
abk run --steps validate                        # config lint only, no DB
```

`--select` accepts a name, a path glob, `tag:<tag>`, or `*` and is repeatable;
`--exclude` removes selectors in the same forms. Each per-experiment run prints
a verdict per comparison — **WIN / LOSE / FLAT / INCONCLUSIVE** — plus any SRM
or insufficient-data blocks. `_ab_results` is the stable BI contract table.

Before you trust or launch an experiment:

- **`abk plan`** — read-only pre-launch sizing (required-N, achievable MDE, or
  achieved power at the effective two-tier alpha). It refuses ratio and
  bootstrap methods, which have no versioned power formula. See [Plan](plan.md).
- **`abk validate`** — the A/A false-positive + power **matrix** (placebo
  label-permutation splits on the experiment's own cohort). It answers "is the
  FPR about equal to alpha?" for each cell, persists `_ab_aa_runs`, and lights
  the explore calibration chip. It is **not** a config lint. See
  [Validate](validate.md).

To inspect the stabilization series and re-slice alpha, correction, and metrics
interactively, open the cockpit with [`abk explore`](explore.md).

## Known multi-arm limitations

`variants` accepts any number `>= 2` — the control is the first declared variant
unless `assignment.control` names another, and abkit computes a full comparison
for every DECLARED contrast: every pair under `all_pairs`, the
control-vs-treatment ones under `vs_control` (declarative-config §2). A 3+-arm
experiment runs end to end: the pipeline computes every pair, the readout
verdicts each one, and the HTML report shows a card per pair plus a **cross-arm
overview** naming the leader and whether it separated from the other treatments
— see [Multi-arm: who won?](reading-a-readout.md#multi-arm-who-won). A few
adjacent surfaces are honestly not (yet) k-arm-aware, though:

- **`abk validate` and `abk plan` are not yet arm-aware** — see the two bullets
  below. Every *presentation* surface reads the whole decision layer: the
  report, the dashboard row, [`abk explore`](explore.md)'s Review mode,
  notifications and the `abk run --report` line.
- **`abk plan` sizes off one contrast only** — the control against the FIRST
  declared treatment. (Deliberately not `contrast_pairs()[0]`: with a control
  declared late in `variants` that entry is a treatment-vs-treatment pair, which
  carries no baseline moments.) Required-N / achievable
  MDE / achieved power is computed for that pair; every other
  pair rides the same alpha and sample size rather than being sized
  independently. The plan output says so in an explicit warning line (see
  [Plan](plan.md#multi-arm-experiments)).
- **`abk validate`'s placebo split is two-arm.** The A/A engine pools the
  experiment's whole cohort and splits it into exactly two placebo shares —
  control's expected share vs. every other variant pooled together — never a
  k-way split that mirrors each declared arm individually. For a 3+-arm
  experiment the measured FPR is a control-vs-rest number, not a
  per-treatment-arm one. See [Validate](validate.md).

None of this is new behavior — it is what has always run. This section just
names it plainly so a 3+-arm user knows what is and isn't covered today.
