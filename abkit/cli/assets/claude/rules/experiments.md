# abkit — Experiment configuration (`experiments/*.yml`)

One YAML file per experiment. The **experiment is the primary entity**: it names
its read-only assignment source, its variants and expected split, the cumulative
window, the cadence, and a list of `comparisons` that bind reusable **metrics**
(see `metrics.md`) to statistical **methods** (see `methods.md`). The experiment
is identified by its `name` field — globally unique across the project, **one
namespace shared with metrics** (the name is the DB key, not the filename).

Files may nest under `experiments/`. Spec: `declarative-config.md §2, §4–§5`.

## Anatomy

```yaml
name: signup_redesign_v2         # required, globally unique (DB key; alnum/_/-)
description: "Onboarding redesign for the signup funnel"   # optional
status: running                  # design | running | concluded | archived (default running)
is_actual: true                  # catalog flag only (persisted to _ab_experiments); NOT read for selection (default true)
tags: [growth, onboarding]       # optional — selectable via `--select tag:<t>` (e.g. tag:actual matches this list)

start_date: 2024-07-01           # required — PINNED left edge of every cumulative window
end_date:   2024-07-14           # required — planner horizon (also drives the power plan)
unit_key:   user_id              # required — randomization + default analysis unit

cadence: 1d                      # cumulative cutoff step (default "1d"); see "Cadence" below
data_lag: 0                      # completeness watermark; REQUIRED when cadence < 1d
timezone: UTC                    # interprets date fields & midnight snapping; storage is UTC (default UTC)

assignment:                      # READ-ONLY exposure source — abkit never randomizes
  query_file: sql/assignment.sql # or inline `query:` (exactly one, never both)
  added_filters: ""              # optional extra SQL fragment (must start with AND)
  variants: [control, treatment] # FIRST is control (name_1); >= 2, unique
  expected_split: {control: 0.5, treatment: 0.5}   # must cover every variant, sum to 1.0; drives SRM
  # cohort_copy:                 # opt-in (default OFF): persist an incremental,
  #   enabled: true              # append-only _ab_exposures copy instead of
  #   update_column: exposure_ts # re-reading the live source every invocation
  #   batch_interval: 1d         # (grid-anchored closed-interval batches;
  #   maturity_delay: 0          # batch_intervals_per_round_trip: 30 — intervals, not rows)

alpha: 0.05                      # experiment-level significance (unset -> project default)
correction: bonferroni           # none | bonferroni | benjamini_hochberg (unset -> project default)
sequential: {enabled: false, scheme: always_valid}   # opt-in peeking-safe CIs (default OFF)

readout:                         # READ-TIME verdict knobs — NEVER enter method_config_id
  stabilization_days: 7          # trailing elapsed-DAYS window for "persistent significance" (default 7)
  guardrail_policy: block        # block (default): regressed guardrail caps WIN at INCONCLUSIVE; warn: keep WIN + caveat

comparisons:                     # required — each binds one library metric to one method
  - metric: signup_cr            # references metrics/signup_cr.yml by name
    is_main_metric: true         # primary winner criterion (drives the verdict + two-tier Bonferroni)
    min_effect: 0.01             # optional: business-meaningful effect (enables FLAT vs underpowered)
    desired_direction: increase  # increase | decrease — which sign is GOOD (orients WIN vs LOSE)
    method: {name: z-test, params: {test_type: relative, calculate_mde: true}}
  - metric: arpu
    method: {name: cuped-t-test, params: {test_type: relative, covariate_lookback: 14d}}
  - metric: crash_rate
    is_guardrail: true           # checked for regression only, never for winning
    desired_direction: decrease
    method: {name: z-test, params: {test_type: relative}}
```

## Variants, split & SRM

- `assignment.variants` lists the arm names; **the first is control (`name_1`)**.
  Every subsequent variant is compared against it. Names must be unique, non-empty,
  and within the storage key budget.
- `expected_split` is the *assigned* share per arm (fractions in `(0,1)` that sum
  to `1.0`, one entry per variant). It is the input to the **SRM gate**: a
  chi-square test (anytime-valid multinomial below `1d` cadence) comparing observed
  arm sizes to this split.
- **SRM is a blocking, non-dropping gate.** On failure the rows are still written
  with `srm_flag` set and `decision_blocked` — the CLI prints a red `SRM FAILED`
  line and no verdict is trusted. Check SRM before believing any effect; fix the
  assignment query or randomization first.
- The assignment SQL is a **read-only** source: it SELECTs `unit_key`, `variant`,
  and an exposure timestamp — abkit never randomizes. By default
  (`assignment.cohort_copy` unset) nothing is persisted: every
  `abk run`/`plan`/`validate`/`explore` invocation re-renders and validates the
  assignment SQL live and metric queries join a deduping subquery over it (the
  no-copy cost/freshness tradeoff — never a stale row, at a render+validate
  query each time). Set `assignment.cohort_copy.enabled: true` to persist an
  append-only incremental copy into `_ab_exposures` instead (worth it for a
  heavy multi-join or a mutating source; in copy mode the assignment SQL must
  reference `{{ ab_added_filters }}` — the copy engine injects batch bounds
  through it, and `abk run --resync-cohort` rebuilds a poisoned copy). Use
  `added_filters` (must start with `AND`) as the only escape hatch for extra
  WHERE conditions.

## Cumulative window, cadence & data_lag

- The analysis window is **pinned-start / moving-end**: `start_date` never moves;
  each cutoff advances the end through the horizon, producing the stabilization
  series (one point per cutoff, effect + CI over `[start_date .. cutoff]`).
- `end_date` is the **horizon**: the last daily cutoff covers this day, and it
  drives the power plan and the sequential pre-horizon rule below.
- `cadence` is the cutoff step. Either a **duration scalar** (`"1h"`, `"30m"`,
  `"1d"`, or integer seconds) or a **coarsening schedule** — a dense-early list
  that must be strictly coarsening with strictly increasing `until` bounds (only
  the last segment may omit `until` and run to the horizon):

  ```yaml
  cadence:
    - {every: 1h, until: 48h}    # hourly for the first two days
    - {every: 1d}                # daily thereafter
  ```

- `data_lag` is the completeness watermark: a cutoff is analyzed only once
  `end_ts <= now() - data_lag`. **Required when cadence < 1d** (declare your
  ingestion SLA). At daily cadence the default `0` reproduces the legacy
  whole-day-excluding-today behavior.

## Comparisons (metric x method)

Each entry binds one library metric to one method. Rules the validator enforces:

- **At least one `is_main_metric: true`** — it drives the verdict and the two-tier
  Bonferroni (main vs secondary metrics get different post-correction alphas).
- `is_main_metric` and `is_guardrail` cannot both be true. A guardrail is checked
  for regression only; `guardrail_policy` decides whether a regressed guardrail
  blocks a WIN.
- Each metric may be bound **at most once** per experiment (no duplicates).
- `method` is `{name, params}` — see `methods.md` for the 12 registered methods and
  their params. `method_config_id` is a hash of the method name + its non-default
  **identity** params; `alpha` is post-correction, experiment-level, and never part
  of the identity. **Editing an identity param orphans the prior results series**
  (recompute, then `abk clean --select <exp>` to prune).
- `min_effect` and `desired_direction` are **read-time verdict inputs**, not method
  params — they never enter `method_config_id`. `min_effect` (in the units of this
  comparison's persisted effect) enables the FLAT verdict; without it, flat cannot
  be distinguished from underpowered.

Verdicts per comparison: **WIN / LOSE / FLAT / INCONCLUSIVE** (plus blocked-on-SRM
and insufficient-data states). `_ab_results` is the stable BI contract table.

## Sequential analysis (opt-in, default OFF)

Fixed-horizon CIs are only valid **at** the planned horizon — reading the daily
series early and stopping ("peeking") inflates the false-positive rate. Therefore:

- With `sequential.enabled: false` (the default), the readout **withholds WIN/LOSE
  and FLAT before the horizon** (FLAT is equally a stop decision) — the pre-horizon
  series is informational only.
- Set `sequential: {enabled: true}` on a sequential-eligible method to get
  **always-valid (peeking-safe) confidence sequences**, so WIN/LOSE can be read
  before the horizon. Toggling it re-plans the series (a bare `abk run` re-plans).
- `scheme: always_valid` is the only implemented scheme. `scheme: alpha_spending`
  (group-sequential) is **not implemented** and fails cleanly at validate time —
  use `always_valid`.

## Validate & size before you trust or launch

- **`abk validate`** runs the A/A false-positive + power **matrix** (placebo
  label-permutation splits on the experiment's own cohort): it answers "is FPR ~=
  alpha?" (single-look **and** the honest peeking rate), plus power, achieved-MDE,
  and coverage; it persists `_ab_aa_runs` and lights the explore calibration chip.
  It is **not** a config lint — the config lint is `abk run --steps validate`.
- **`abk plan`** is read-only pre-launch sizing (required-N / achievable-MDE /
  achieved-power at the effective two-tier alpha; CUPED sized on raw variance). It
  refuses ratio and bootstrap methods (no versioned power formula).

## Minimal valid example

```yaml
name: pricing_test
start_date: 2024-07-01
end_date:   2024-07-14
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
