---
name: abk-validate
description: >-
  Check whether an abkit compute method is actually calibrated on the
  experiment's own data by running placebo A/A splits — does its false-positive
  rate really equal α? Use when the user asks "is this method trustworthy on my
  data", "run an A/A", worries about false positives, peeking / optional
  stopping, whether a verdict can be believed, or before shipping a metric that
  a p-value looks too good on. Runs `abk validate`, reads the FPR (single-look
  AND peeking) / power / coverage matrix, acts on the recommendation and the
  budget bands, persists `_ab_aa_runs`, and lights the explore calibration chip.
  This is A/A CALIBRATION, not a config lint.
---

# Validate a method's calibration (A/A false-positive matrix)

`abk validate` answers one question: **is this method actually calibrated on
*this* data, or does it lie about its α?** It draws **placebo A/A splits** on the
experiment's own pooled cohort (unit→arm labels permuted → an exact null by
construction), runs the candidate method(s) over the real cadence grid, and
reports the empirical **false-positive rate**, **power**, **achieved MDE**, and
**CI coverage**. A well-calibrated method gives FPR ≈ α; an inflated one is the
single most important thing to catch before an analyst trusts a verdict.

> **This is NOT a config lint.** `abk validate` = the A/A statistical matrix.
> The config/schema/SQL lint is `abk run --steps validate` (no DB, no stats).
> Different purpose, different lock, different stage names — never conflate them.
> If the user wants "check my YAML is valid", that's `--steps validate`, not this.

Work the steps in order. This skill is the procedure; for field detail read
`.claude/rules/ab-analysis-kit/validate.md` (the mechanism, the `_ab_aa_runs`
schema, the sequential/composed columns, budget-band resolution).

## Step 0 — Preconditions

- A project root has `abkit_project.yml`; `profiles.yml` must point at the real
  warehouse (if it's still the `abk init` placeholder, use **`abk-setup-project`**
  first).
- **The pipeline must have run at least once.** validate resamples the
  experiment's **persisted** cohort — if nothing is loaded there is nothing to
  split. Run `abk run --select <exp>` first if needed.

## Step 1 — Run validate

```bash
abk validate --select <exp>                          # every declared comparison, auto N = max(2000, ceil(200/alpha)) per cell
abk validate --select <exp> --family-sweep           # + the composed multi-metric FWER/FDR sweep (opt-in since 0.2.0)
abk validate --select <exp> --metric <m>             # one metric only
abk validate --select <exp> --method z-test          # score an EXTRA method beyond the declared one
abk validate --select <exp> --iterations 500         # quick look (looser FPR estimate)
abk validate --select <exp> --inject-effect 0.05     # also measure power / achieved MDE / coverage
```

| Flag | Meaning |
|---|---|
| `--select`, `-s` | Experiment selector (name / glob / `tag:<tag>` / `*`; repeatable, default all). |
| `--method`, `-m` | **Extra** registered method(s) to score **beyond** the declared comparison — the method-grid axis (repeatable). Not a selector. |
| `--metric` | Validate only this metric (default: every declared comparison). |
| `--iterations`, `-n` | Placebo A/A splits per cell (default: auto, `max(2000, ⌈200/α⌉)` at each cell's effective alpha). An explicit N overrides every cell. |
| `--family-sweep` | Also run the composed multi-metric FWER/FDR sweep — roughly doubles the cost. Opt-in since 0.2.0 (previously always ran when `--metric` was omitted). |
| `--inject-effect` | Inject this **relative** effect (e.g. `0.05`) to measure power / achieved MDE / coverage. Without it, only FPR/peeking compute. |
| `--scoring` | `fpr` (default) / `power` / `mde` — the objective for the **"Recommended" row only**. All columns compute regardless. |
| `--report` | Emit a self-contained HTML matrix (best-effort). Bare → `reports/<exp>__validate.html`; a dir or `.html` path overrides. |
| `--force` | Take over a held validate lock (use with care). `abk unlock` clears it. |

- **Costs scale as N × the method grid; bootstrap A/A is the expensive corner.**
  Default to closed-form methods; drop `--iterations` for a quick look, raise it
  for a tight estimate. Runs are deterministic (seeded, no wall-clock) — re-running
  gives the same FPR.
- **Exits non-zero** on any cell/harness failure (`--report` is the one
  best-effort exception).

## Step 2 — Read the matrix (FPR single-look AND peeking)

Two FPR columns, and you must read **both**:

- **single-look FPR** — significance at the **horizon cutoff only** (the honest
  fixed-horizon number). This is what should sit on α.
- **peeking FPR** — the share of placebos whose CI **excludes zero at *any* look**
  across the grid. It models the analyst who eyeballs the daily chart and stops
  the first time the CI clears zero. Peeking ≥ single-look by construction. It is
  the **optional-stopping hazard**, *not* the readout verdict — the readout's
  stabilization rule is the defense against exactly this.

The three classic failures to recognize:

| pattern | reading | action |
|---|---|---|
| single-look ≈ α, coverage ≈ 95% | **well-calibrated** (the reference row) | use it |
| single-look ≫ α, coverage collapses | **FPR inflated** — e.g. `z-test` on a clustered proportion (`nobs`>1) underestimates variance; worse as days accumulate | do **not** use; reach for a delta-method ratio or re-express per-unit |
| single-look ≈ α but peeking ≫ budget | **peeking breaks a calibrated method** — correctly specified, but correlated daily looks inflate optional-stopping FPR | enable `sequential: {enabled: true}` — it is NOT broken |

## Step 3 — Act on the recommendation and budget bands

- The report/matrix marks a **★ "Recommended" row** with a one-line rationale
  ("highest power among methods with FPR within budget"; tiebreak: tightest
  achieved MDE), plus a plain-language
  per-method verdict and the peeking headline (e.g. *"nominal α 5%, real peeking
  FPR 12.7%"*).
- **Budget bands** color FPR cells green (in-band) / red (out). The budget
  resolves **metric `aa_fpr_budget` override → project
  `statistics.aa_fpr_budget` → built-in default** (flag if FPR > α × 1.5). Set
  `aa_fpr_budget:` (a fraction in (0,1]) on a metric YAML to tighten/loosen.
- If FPR is out of budget → switch method or re-express the metric, then re-run
  validate. If only *peeking* is out of budget → enable sequential and re-validate
  (the sequential column measures the always-valid twin beside the fixed one).
- **Two-tier alpha:** main vs secondary metrics land at **different** effective
  post-correction alphas; the persisted per-cell `alpha` is that effective value.

## Step 4 — It persists and lights the explore chip

- validate writes **one `_ab_aa_runs` row per cell** (never `_ab_results` /
  `_ab_exposures`; the placebo shuffle is in-memory only). The table is
  informational and never pruned by `abk clean`.
- Those rows **light the `abk explore` calibration chip**: it shows
  *calibrated / FPR = X.X% vs α* for the current knob combination (red when out
  of budget). On an empty `_ab_aa_runs` every explore **Apply** takes the
  `confirm_uncalibrated` path — running validate (or explore's Auto mode) is what
  flips the chip to `calibrated`.
- **Editing `method_params` orphans the calibration** — the chip keys off
  `method_config_id`; after retuning, the old rows no longer match, so re-run
  validate on the new params.

## Final checklist — verify before declaring done

- [ ] Confirmed this is A/A calibration, not a config lint (`abk run --steps
      validate` if the user actually wanted the YAML gate).
- [ ] `abk run --select <exp>` had persisted data before validating.
- [ ] Read **both** single-look and peeking FPR against α / the budget band.
- [ ] Reported the ★ Recommended row + per-method verdict, and named the fix for
      any red cell (switch method / re-express metric / enable sequential).
- [ ] Noted the `_ab_aa_runs` rows now light the explore calibration chip.
