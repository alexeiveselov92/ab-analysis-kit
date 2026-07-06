# The A/A false-positive matrix (`abk validate`)

`abk validate` answers one question: **is this method actually calibrated on *this*
data, or does it lie about its α?** It draws **placebo A/A splits** on the
experiment's own cohort — where by construction there is no true effect — runs the
candidate method(s) over the real cadence grid, and reports the empirical
**false-positive rate**, **power**, **achieved MDE**, and **CI coverage**. A
well-calibrated method gives FPR ≈ α; an inflated one is the single most important
thing to catch before an analyst trusts a verdict.

It is out-of-band from `abk run` (its own lock, never in the pipeline hot path) and
**writes only `_ab_aa_runs`** — never `_ab_results` / `_ab_exposures`. The placebo
shuffle is in-memory only; it never touches the real assignment.

> **`abk validate` is NOT a config lint.** The config lint is `abk run --steps
> validate` (schema/SQL/reference checks, no DB, no statistics). They deliberately
> share no copy: the word "validate" in `--steps` is the config gate; the `abk
> validate` **command** is the A/A statistical matrix. The stage vocabularies differ
> too — the matrix streams `LOAD → RESAMPLE → SCORE → PERSIST`, the config gate a
> single `VALIDATE` step. Never conflate them.

## Mechanism — the placebo split

1. **Source = the experiment's own pooled cohort** rendered over the real cadence
   grid (the same `generate_grid` + metric loaders the pipeline uses). No separate
   historical window, no exposure-free loader.
2. **Pool the per-variant unit arrays and permute the unit→arm labels.** Permuting
   labels destroys any true treatment effect → an **exact null** by construction (the
   standard permutation-A/A), while still exercising the real grid, cadence, cohort,
   and metric SQL.
3. **A/A (false-positive):** repeat N times (default 2000); each split runs the
   candidate method(s) and records whether it *falsely* rejects H₀ at the effective
   α. **FPR** = share of placebo runs that flagged significance. The significance
   primitive is the readout's own **CI-excludes-zero** rule, not the raw `reject`
   flag, so z-test / bootstrap edge cases match what the readout would say.
4. **Power:** inject a known synthetic effect (`--inject-effect`) into one placebo arm
   and record the rejection rate → **power**, plus **achieved MDE** at the target
   power and **CI coverage**.
5. Persist one `_ab_aa_runs` row **per cell**; emit the recommendation.

Seeds are `derive_seed("aa", experiment, metric, method_config_id, iteration)` — no
wall-clock — so the FPR numbers are a deterministic, reproducible invariant.

## Honest peeking FPR (the headline)

Because the product **is** the daily cumulative chart, the matrix reports the
**cumulative-peeking FPR** beside the single-look FPR — the two together expose the
optional-stopping trap:

- **single-look FPR** — significance at the **horizon cutoff only** (the honest
  fixed-horizon number).
- **peeking FPR** — the share of placebos whose CI **excludes zero at *any* look**
  across the grid (pre-horizon refusal OFF, horizon included, so peeking ≥
  single-look). This models the analyst who eyeballs the daily chart and stops the
  first time the CI clears zero. It is the **optional-stopping hazard**, *not* the
  official readout verdict (the readout's stabilization-persistence rule is the
  *defense* against exactly this — `pipeline/readout.py` is untouched).
- **effect exaggeration at stop** (winner's curse) — conditional on stopping early,
  the effect is biased away from zero; a first-class column beside FPR.

Surfaced as a headline, e.g. *"nominal α 5%, real peeking FPR 12.7%"*. Dense grids
may be subsampled (cap ~100 points, denser early); the matrix states the
`(kept, total)` count when it did.

## The sequential column (D8)

With `sequential: {enabled: true}` on a sequential-eligible method, `abk validate`
adds the **always-valid twin beside** the fixed peeking column — it only *measures*,
never asserts. The always-valid confidence sequence widens the CI (~1.55× at the
anchor — the first/reference look — growing to ~1.6–1.9× at the horizon, which has the
most data) so it is peeking-valid at *every* look, pulling the optional-stopping hazard
back toward ≈ α:

| metric (kind) | method | peeking FPR (fixed) | peeking (always-valid) | CI width fixed → AV |
|---|---|---|---|---|
| `ctr` (ratio) | `ratio-delta` | **12.7%** ⚠ | **≈5%** ✅ | ×1.55 anchor → ×1.6–1.9 horizon |

The fixed column *diagnoses* the trap; the always-valid column *is* the defense, at
the cost of a wider interval. This is how the fixed-horizon default stays honest
without being changed.

## The composed family sweep (D9)

Per-cell FPR is necessary but not sufficient: an experiment runs a **family** of
metrics under one shared assignment, corrected by two-tier Bonferroni (compute-time)
∘ Benjamini-Hochberg (read-time). `abk validate` sweeps the empirical **family-wise
error rate** (any false rejection across the family) and **false-discovery rate**
(mean false fraction among rejections) over one shared union-cohort placebo
assignment per iteration, under the *same* composed rule the readout applies.

On the complete null the two coincide (every rejection is false) and sit at the
composed rule's **nominal rate** — ≈ α *per tier*, so ≈ 2α whole-family under the
default two-tier Bonferroni (which protects the main tier and the secondary tier each
at α, by design). The budget is anchored to that nominal rate, so "over budget" means
the **methods** are miscalibrated (clustering / variance underestimation), not that
the correction is loose. It surfaces as one sentinel `_ab_aa_runs` row and a
composed-family band above the report matrix. (Sequential × composed is an M6
follow-up — the sweep is fixed-horizon.)

## Command

```bash
abk validate --select <exp> [--method <m>]... [--metric <m>] [--iterations N] \
             [--inject-effect <rel>] [--scoring fpr|power|mde] [--report] [--force] [--profile]
```

| Flag | Meaning |
|---|---|
| `--select`, `-s` | Experiment selector (name / glob / `tag:<tag>` / `*`; repeatable, default all). |
| `--method`, `-m` | **Extra** registered method(s) to score **beyond** the declared comparison — this is the method-grid axis (repeatable). NOT `--select`. |
| `--metric` | Validate only this metric (default: every declared comparison). |
| `--iterations`, `-n` | Placebo A/A splits per cell (default 2000). More = tighter FPR estimate, more cost. |
| `--inject-effect` | Inject this **relative** effect (e.g. `0.05`) to measure power / achieved MDE / coverage. |
| `--scoring` | `fpr` (default) / `power` / `mde` — the objective for the **"Recommended" row only**. All columns are always computed regardless. |
| `--report` | Emit a self-contained HTML matrix report (best-effort). Bare → `reports/<exp>__validate.html`; a dir or `.html` path overrides. |
| `--force` | Take over a held validate lock (use with care). |

- **Own out-of-band lock** — `(experiment, "pipeline", "validate")`, distinct from the
  run lock; cleared by `abk unlock`. It writes only `_ab_aa_runs`, so it need not
  serialize behind nightly runs.
- **Exits non-zero** on any cell/harness failure; `--report` is the one best-effort
  exception (a bake failure yellow-skips, never fails validation).
- Reads the experiment's persisted cohort — run `abk run --select <exp>` at least
  once first so there is data to resample.

## Reading the matrix — the three classic failures

A worked matrix over the synthetic fixture (α = 5%, budget = α × 1.5 = 7.5%, 14-day
grid):

| metric (kind) | method | single-look FPR | peeking FPR | power @ δ=15% | coverage | verdict |
|---|---|---|---|---|---|---|
| `arpu` (sample) | `t-test` | **5.3%** ✅ | 8.6% | 96% | 95% ✅ | well-calibrated |
| `conversion` (fraction, `nobs`>1) | `z-test` | **42.4%** ❌ | 43.5% | 95% | 55% ❌ | FPR inflated, **do not use** |
| `ctr` (ratio) | `ratio-delta` | **4.8%** ✅ | **12.7%** ⚠ | 100% | 95% ✅ | calibrated single-look; peeking breaks budget |

1. **Well-calibrated** — a t-test on a per-unit continuous metric: single-look FPR
   sits on α, coverage at the nominal 95%. This is the reference row.
2. **FPR inflated** — `z-test` on a clustered proportion (`nobs`>1, correlated
   within-unit trials): it pools trials as independent Bernoulli draws,
   underestimates variance, and gets **worse as days accumulate**. Coverage collapses
   to 55%. Reach for a delta-method ratio or re-express the metric per-unit.
3. **Peeking breaks a calibrated method** — `ratio-delta` is correctly specified
   (single-look 4.8%), but 14 correlated looks push the optional-stopping FPR to
   12.7%. FPR alone would green-light it; the peeking column exposes the trap.
   Sequential brings it back toward α (D8).

The report renders: **budget-band colors** (green in-band, red out — against
`aa_fpr_budget`), an explicit **"Recommended" row** with a one-line rationale
("lowest CI width among methods with FPR within budget"), a **plain-language
per-method verdict**, and the peeking headline.

## Budget bands & two-tier alpha

- **`aa_fpr_budget`** colors the FPR cells: green in-band, red out. It resolves
  **metric override → project `statistics.aa_fpr_budget` → built-in default**
  (flag if FPR > α × 1.5). Set `aa_fpr_budget:` on a metric YAML (a fraction in
  (0, 1]) to tighten/loosen it for that metric.
- **Two-tier alpha** — the persisted per-cell `alpha` is the **effective
  post-correction per-comparison** alpha (the SAME resolver the chip/Apply/readout
  use): main metrics and secondary metrics land at **different** alphas. This is why
  the chip can look the cell up by `(metric, method_config_id, effective α)`.

## `_ab_aa_runs` (audit table)

One row **per cell** (`run_id = "{run_stamp}:{cell_hash}"`, no ReplacingMergeTree
collapse). Informational; never pruned by `abk clean`. Columns:

`experiment`, `run_id`, `metric`, `method_name`, `method_params`, `method_config_id`,
`mode` (the `--scoring` objective), `iterations`, `alpha` (effective post-correction),
`injected_effect`, `fpr` (single-look), `peeking_fpr`, `power`, `achieved_mde`,
`coverage`, `effect_exaggeration`, `verdict`, `details` (JSON: peeking curve,
`(kept,total)` note, rationale, warnings), `status` (`success`|`failed`),
`error_message`, `created_at` (LWW version). A `status='failed'` row (or `fpr` null)
is kept for audit but never counted by `find_calibration`.

## Explore integration — the calibration chip

The real-α signal must not live only in a separate command. In `abk explore`:

- a persistent **calibration chip** shows **calibrated / FPR = X.X% vs nominal α** for
  the *current* knob combination (red when out of budget), read from the
  `_ab_aa_runs` rows validate wrote;
- **Auto mode** runs validate server-side (`POST /validate`, reduced N) and greens the
  live chip in place — no explore restart;
- **Apply is gated/confirmed** when the active `method_params` have never passed
  validate — so an analyst cannot ship a mis-calibrated method without seeing the cost.

On an empty `_ab_aa_runs` every Apply takes the `confirm_uncalibrated` path; running
`abk validate` (or Auto mode) is what flips the chip to `calibrated`.

## Cost & scaling

`validate` multiplies per-interval compute by N × the method grid, and **bootstrap
A/A is the expensive corner**. So:

- **default to closed-form methods** (the `from_suffstats` path — microseconds; the
  per-interval sufficient statistics are computed once per split and prefix-summed
  across the grid);
- **gate bootstrap A/A** behind explicit opt-in with reduced `n_samples` and a
  subsampled unit population (a representative subsample is enough for FPR
  calibration);
- lower `--iterations` for a quick look, raise it for a tight FPR estimate.

## Gotchas

- **Not a config lint.** `abk validate` = the A/A statistical matrix; `abk run
  --steps validate` = the config/schema/SQL gate. Different purpose, different lock,
  different stage names — never conflate.
- **Run the pipeline first.** validate resamples the experiment's persisted cohort —
  `abk run --select <exp>` must have persisted data or there is nothing to split.
- **Peeking FPR is the hazard, not the verdict.** It deliberately measures the
  optional-stopping trap the readout's stabilization rule defends against; a high
  peeking FPR on a low single-look FPR means "enable `sequential`", not "the method
  is broken".
- **Editing `method_params` orphans the calibration.** The chip keys off
  `method_config_id`; retuning starts a new series and the old `_ab_aa_runs` rows no
  longer match — re-run validate on the new params.

> Installed by `abk init-claude`. Re-run it after upgrading abkit to refresh this file.
