# Validating with A/A (abk validate)

`abk validate` answers one question about your experiment analysis:
**is this method actually calibrated on _your_ data, or does it lie about its α?**

It draws **placebo A/A splits** on the experiment's own cohort — where by
construction there is no true effect — reruns the candidate method(s) over the real
cadence grid, and reports the empirical **false-positive rate (FPR)**, **power**,
**achieved MDE**, and **CI coverage**. A well-calibrated method gives FPR ≈ α; an
inflated one is the single most important thing to catch before you (or a
stakeholder) trust a WIN or LOSE verdict.

`abk validate` is a statistical audit, not a config check. It runs out-of-band from
`abk run` — its own lock, never in the pipeline hot path — and **writes only the
`_ab_aa_runs` audit table**, never `_ab_results` or `_ab_exposures`. The placebo
shuffle is in-memory only; it never touches your real assignment.

## `abk validate` is NOT a linter

This is the most common confusion, so state it up front. There are two different
things called "validate":

- **`abk run --steps validate`** — the **config lint**: schema / SQL / cross-reference
  checks, no database, no statistics. (`--steps` defaults to
  `validate,plan,load,compute`; naming `validate` alone runs only the lint.)
- **`abk validate`** — the **A/A statistical matrix** described on this page: placebo
  splits, empirical FPR, power, coverage. It reads persisted data and writes
  `_ab_aa_runs`.

They share no code and serve opposite purposes. If you want to know "does my YAML
parse and do my metric references resolve?", that is `abk run --steps validate`. If
you want to know "does my `z-test` actually hold its 5% error rate on this clustered
metric?", that is `abk validate`.

## Prerequisite: run the pipeline first

`abk validate` resamples the experiment's **persisted cohort** — it re-reads the same
data `abk run` wrote and permutes labels in memory. So you must have run the pipeline
at least once:

```bash
abk run --select checkout_flow_v3
abk validate --select checkout_flow_v3
```

If there is no persisted data to split, validate has nothing to resample.

## How the placebo split works

The mechanism (aa-false-positive-matrix §1) is a standard permutation-A/A:

1. **Source = the experiment's own pooled cohort**, rendered over the real cadence
   grid — the same grid, cadence, cohort, and metric SQL the pipeline uses. There is
   no separate historical window and no exposure-free loader.
2. **Pool the per-variant unit arrays and permute the unit→arm labels.** Permuting
   labels destroys any true treatment effect, so the split is an **exact null** by
   construction, while still exercising your real metric SQL and cadence.
3. **A/A (false-positive):** repeat N times (default 2000). Each split runs the
   candidate method(s) and records whether it _falsely_ rejects H₀ at the effective α.
   The **FPR** is the share of placebo runs that flagged significance. The
   significance rule is the readout's own **CI-excludes-zero** test — not the raw
   `reject` flag — so `z-test` and bootstrap edge cases match what the readout would
   actually say.
4. **Power:** with `--inject-effect`, a known synthetic effect is injected into one
   placebo arm; the rejection rate becomes **power**, plus **achieved MDE** at the
   target power and **CI coverage**.
5. Persist one `_ab_aa_runs` row **per cell**, then emit the recommendation.

Seeds are derived from `("aa", experiment, metric, method_config_id, iteration)` with
no wall-clock input (aa-false-positive-matrix §1), so the FPR numbers are a
deterministic, reproducible invariant — the same command on the same data returns the
same matrix.

## Single-look vs cumulative-peeking FPR

This is the headline of the matrix, because the abkit product **is** the daily
cumulative chart, and a chart invites peeking. The matrix reports two FPRs side by
side (aa-false-positive-matrix §3):

- **single-look FPR** — significance at the **horizon cutoff only**. This is the
  honest fixed-horizon number.
- **peeking FPR** — the share of placebos whose CI **excludes zero at _any_ look**
  across the grid (pre-horizon refusal off, horizon included, so peeking is always ≥
  single-look). This models the analyst who eyeballs the daily chart and stops the
  first time the CI clears zero. It is the **optional-stopping hazard**, _not_ the
  official readout verdict.

The readout's own rule (CI-excludes-zero **and** stabilized) is the _defense_ against
exactly this trap, and validate deliberately does **not** measure the defended rule —
it measures the trap, so you can see how bad naive peeking would be. A high peeking
FPR on a low single-look FPR means "turn on sequential" (see below), not "the method
is broken".

Validate also reports **effect exaggeration at stop** (the winner's curse): conditional
on stopping early, the estimated effect is biased away from zero. FPR alone hides this;
it is a first-class column beside FPR.

The headline reads, e.g., _"nominal α 5%, real peeking FPR 12.7%"_. Very dense grids
may be subsampled (cap ~100 points, denser early); the matrix states the
`(kept, total)` count when it did.

## Reading the matrix — the three classic failures

A worked matrix over the synthetic fixture (α = 5%, budget = α × 1.5 = 7.5%, 14-day
grid; aa-false-positive-matrix §8):

| metric (kind) | method | single-look FPR | peeking FPR | power @ δ=15% | coverage | verdict |
|---|---|---|---|---|---|---|
| `arpu` (sample) | `t-test` | **5.3%** | 8.6% | 96% | 95% | well-calibrated |
| `conversion` (fraction, `nobs`>1) | `z-test` | **42.4%** | 43.5% | 95% | 55% | FPR inflated, do not use |
| `ctr` (ratio) | `ratio-delta` | **4.8%** | **12.7%** | 100% | 95% | calibrated single-look; peeking breaks budget |

1. **Well-calibrated** — a `t-test` on a per-unit continuous metric: single-look FPR
   sits on α, coverage at the nominal 95%. This is the reference row.
2. **FPR inflated** — a `z-test` on a clustered proportion (per-unit `nobs` > 1,
   correlated within-unit trials): it pools trials as independent Bernoulli draws,
   underestimates variance, and gets **worse as days accumulate**. Coverage collapses
   to 55%. Reach for a delta-method ratio (`ratio-delta`) or re-express the metric
   per-unit.
3. **Peeking breaks a calibrated method** — `ratio-delta` is correctly specified
   (single-look 4.8%), but 14 correlated looks push the optional-stopping FPR to
   12.7%. FPR alone would green-light it; the peeking column exposes the trap.
   Enabling sequential brings it back toward α.

The `--report` matrix renders budget-band colors (green in-band, red out), an explicit
**Recommended** row with a one-line rationale, a plain-language per-method verdict, and
the peeking headline.

## The sequential column

If you set `sequential: {enabled: true}` on a sequential-eligible experiment (see the
[sequential guide](sequential.md)), `abk validate` adds the **always-valid twin beside**
the fixed peeking column (aa-false-positive-matrix §8.1). The always-valid confidence
sequence widens the CI so it is peeking-valid at _every_ look, pulling the
optional-stopping hazard back toward ≈ α:

| metric (kind) | method | peeking FPR (fixed) | peeking (always-valid) |
|---|---|---|---|
| `ctr` (ratio) | `ratio-delta` | **12.7%** | **≈5%** |

The fixed column _diagnoses_ the trap; the always-valid column _is_ the defense, at the
cost of a wider interval. This is how the fixed-horizon default stays honest without
being changed. (The sequential × composed-family sweep is not yet built.)

## The composed family sweep

An experiment runs a **family** of metrics under one shared assignment, corrected by
two-tier Bonferroni (compute-time) then Benjamini-Hochberg (read-time). Per-cell FPR is
necessary but not sufficient, so `abk validate` also sweeps the empirical **family-wise
error rate** and **false-discovery rate** over one shared union-cohort placebo
assignment per iteration, under the _same_ composed rule the readout applies
(aa-false-positive-matrix §8.1).

On the complete null the two rates coincide (every rejection is false) and sit at the
composed rule's **nominal rate** — ≈ α _per tier_, so ≈ 2α whole-family under the
default two-tier Bonferroni (which protects the main and secondary tiers each at α, by
design). The budget is anchored to that nominal rate, so "over budget" means the
**methods** are miscalibrated (clustering, variance underestimation), not that the
correction is loose. It surfaces as one sentinel `_ab_aa_runs` row and a composed-family
band above the report matrix.

## Command and flags

```bash
abk validate --select <exp> [--method <m>]... [--metric <m>] [--iterations N] \
             [--inject-effect <rel>] [--scoring fpr|power|mde] [--report] [--force] \
             [--profile <name>]
```

| Flag | Meaning |
|---|---|
| `--select`, `-s` | Experiment selector — name, path glob, `tag:<tag>`, or `*` (repeatable; default all). |
| `--method`, `-m` | **Extra** registered method(s) to score **beyond** the declared comparison — this is the method-grid axis (repeatable). |
| `--metric` | Validate only this metric (default: every declared comparison). |
| `--iterations`, `-n` | Placebo A/A splits per cell (default `2000`). More = a tighter FPR estimate, more cost. |
| `--inject-effect` | Inject this **relative** effect (e.g. `0.05`) to measure power / achieved MDE / coverage. |
| `--scoring` | `fpr` (default) / `power` / `mde` — the objective for the **Recommended row only**. All columns are always computed regardless. |
| `--report` | Emit a self-contained HTML matrix report (best-effort). Bare flag → `reports/<exp>__validate.html`; pass a directory or an `.html` path to override. |
| `--force` | Take over a held validate lock (use with care). |
| `--profile` | Profile name (default: `profiles.yml` `default_profile`). |

The `--method` axis lets you compare candidates without editing the experiment. To ask
"my declared `z-test` is inflated on `conversion` — would `ratio-delta` or `t-test` do
better?", score them side by side:

```bash
abk validate --select checkout_flow_v3 --metric conversion \
             --method ratio-delta --method t-test
```

To measure power (not just FPR), inject a known effect:

```bash
abk validate --select checkout_flow_v3 --inject-effect 0.05 --scoring power
```

Notes on behavior (aa-false-positive-matrix; validate.py):

- **Own out-of-band lock** — `(experiment, "pipeline", "validate")`, distinct from the
  run lock; clear a stranded one with `abk unlock`. Because it writes only
  `_ab_aa_runs`, it need not serialize behind nightly `abk run`.
- **Exits non-zero** on any experiment-level (harness) failure, so it is safe to wire
  into CI. A single unscoreable cell is recorded as a `status='failed'` row rather than
  aborting the run. `--report` is the one best-effort exception — a bake failure
  yellow-skips and never fails the validation itself.
- `--scoring` sets only which row is marked **Recommended** (persisted as the `mode`
  column); the FPR / peeking / power / coverage columns always compute so the explore
  calibration chip can light regardless.

## The `aa_fpr_budget` override

`aa_fpr_budget` colors the FPR cells: green in-band, red out. It resolves in this order
(aa-false-positive-matrix §"Budget bands"):

1. the metric YAML `aa_fpr_budget:` field, then
2. the project `statistics.aa_fpr_budget`, then
3. the built-in default (flag if FPR > α × 1.5).

Set it on a metric to tighten or loosen the band for that metric. The value is a
fraction in `(0, 1]`:

```yaml
# metrics/conversion.yml
name: conversion
# ... sql, type ...
aa_fpr_budget: 0.06   # this metric is judged against a 6% budget, not the default
```

Or set a project-wide floor in `abkit_project.yml`:

```yaml
statistics:
  aa_fpr_budget: 0.07
```

## The `_ab_aa_runs` audit table

Validate persists one row **per cell** (`run_id = "{run_stamp}:{cell_hash}"`; no
`ReplacingMergeTree` collapse). The table is informational and is **never pruned by
`abk clean`** (aa-false-positive-matrix §7). Columns include:

`experiment`, `run_id`, `metric`, `method_name`, `method_params`, `method_config_id`,
`mode` (the `--scoring` objective), `iterations`, `alpha` (the **effective
post-correction per-comparison** alpha), `injected_effect`, `fpr` (single-look),
`peeking_fpr`, `power`, `achieved_mde`, `coverage`, `effect_exaggeration`, `verdict`,
`details` (JSON: peeking curve, `(kept,total)` note, rationale, warnings), `status`
(`success` | `failed`), `error_message`, and `created_at`.

The persisted `alpha` is the **same** effective per-comparison alpha the readout, chip,
and Apply seam use — main and secondary metrics land at **different** alphas under
two-tier Bonferroni. That is what lets the explore chip look a cell up by
`(metric, method_config_id, effective α)`.

## The calibration chip and Auto mode in explore

The real-α signal must not live only in a separate command, so `abk validate` feeds the
[explore cockpit](explore.md):

- A persistent **calibration chip** shows **calibrated / FPR = X.X% vs nominal α** for
  the current knob combination (red when out of budget), read from the `_ab_aa_runs`
  rows validate wrote.
- **Auto mode** runs validate server-side (reduced N) and greens the live chip in place
  — no explore restart needed.
- **Apply is gated** when the active `method_params` have never passed validate — on an
  empty `_ab_aa_runs`, every Apply takes the `confirm_uncalibrated` path. Running
  `abk validate` (or Auto mode) is what flips the chip to `calibrated`, so you can't
  ship a mis-calibrated method without seeing the cost.

## Gotchas

- **Editing `method_params` orphans the calibration.** The chip keys off
  `method_config_id`; retuning a method starts a new series and the old `_ab_aa_runs`
  rows no longer match. Re-run `abk validate` on the new params.
- **Peeking FPR is the hazard, not the verdict.** A high peeking FPR on a low
  single-look FPR is a signal to enable sequential — it is not evidence the method is
  broken.
- **Bootstrap A/A is the expensive corner.** Validate multiplies per-interval compute
  by N × the method grid. Closed-form methods run in microseconds per split; prefer
  them for the FPR sweep and lower `--iterations` for a quick look, raise it for a tight
  estimate.

## See also

- [Metrics](metrics.md) — declaring the reusable metrics validate resamples.
- [Sequential analysis](sequential.md) — the always-valid CI that closes the peeking
  gap this matrix exposes.
- [Explore cockpit](explore.md) — the calibration chip and Auto mode.
- [Planning experiments](plan.md) — `abk plan` for pre-launch power and sizing.
