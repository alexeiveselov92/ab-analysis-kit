# A/A false-positive matrix (`abk validate`)

> The A/B analog of detectkit's autotune scoring. detectkit scores a detector by
> recall/FDR against **labeled** incidents; abkit scores a method by **empirical
> FPR and power** against **synthetic** ground truth (A/A placebo splits + injected
> effects). It answers: *"is this method actually calibrated on this data, or does
> it lie about its α?"* — the single most important trust artifact, because the
> daily cumulative chart invites peeking and several legacy methods have correctness
> issues.

## 1. Mechanism

Out-of-band (not in the `run` hot path). Reuses the ported autotune scaffolding
(load → resolve → resample → score → persist → emit + lock/finalize):

1. Take real pre-experiment / historical data for the unit population.
2. **A/A (false-positive):** repeatedly (N iterations, e.g. 1000–10000) draw
   **placebo splits** where, by construction, there is **no** true effect; run the
   candidate method(s); record whether each falsely rejects H₀ at the configured α.
   **Empirical FPR** = share of placebo runs that flagged significance. A
   well-calibrated method gives FPR ≈ α.
3. **Power:** inject a known synthetic effect of size **MDE** into one placebo arm
   and record the rejection rate. **Power** = share that correctly detect it. Also
   report **achieved MDE** at the target power and **CI coverage**.
4. Persist an `_ab_aa_runs` audit row; emit the calibrated recommendation.

This catches the classic failures: a ratio metric under a naive t-test (FPR ≫ α
from unaccounted clustering), peeking-induced inflation (§3), and CUPED/stratification
that tightens the band in-sample but doesn't generalize (no held-out power gain).

## 2. The matrix

Rows = method configurations (`test_type` × CUPED on/off × stratify × correction);
columns = **empirical FPR**, **power @ MDE**, **achieved MDE @ target power**, **CI
coverage**. Selection = the config whose FPR is **closest-to-nominal while
maximizing power** (the analog of detectkit's MCC/F-β selection).

## 3. Honest peeking FPR (decision Q2 — first-class)

Because the product *is* the cumulative chart, the matrix MUST report the
**real cumulative-peeking FPR**, not a single-look FPR:

- Run each A/A placebo through the **full cadence grid** (the experiment's actual
  grid, daily or sub-day — cumulative-intervals.md §6) and the **actual readout
  decision rule** ("CI excludes zero and stabilized"), and record whether it *ever*
  falsely calls a winner across the experiment lifetime. For closed-form methods
  this is near-free: per-interval sufficient statistics are computed once per
  placebo split and prefix-summed across the grid (the `accumulate.py` merge
  primitive). Very dense grids may be subsampled (cap ~100 points, denser early
  where the FPR accrues fastest) — the matrix must state when it did.
- Report **effect exaggeration at stop** (winner's curse) as a first-class column
  beside FPR: conditional on stopping early, the effect estimate is biased away
  from zero — at hundreds of looks this is the analyst's biggest practical trap
  and FPR alone does not show it.
- Surface this as a **headline number** beside the nominal α — e.g. "nominal α 5%,
  **real peeking FPR 14%**" — in the matrix, the HTML report, and the explore chip.
- Show the **same metric with `sequential.enabled`** side-by-side so the analyst
  sees the always-valid CI brings it back to ≈ α. This is how we keep the
  fixed-horizon default *honest* without changing it.
- The composed multiple-testing FDR/FWER (config-time Bonferroni × read-time BH ×
  peeking) is validated **empirically** over the day-grid, never assumed.

## 4. UX (must-fix — a raw grid will be misread)

Analysts pick the highest-power row and ignore an inflated FPR. So the matrix is
**not** a bare number grid:

1. **Color FPR cells against the `aa_fpr_budget` band** — green in-band, red out
   (the analog of detectkit's `false_alert_budget` chip). `aa_fpr_budget` resolves
   metric → project → built-in default (e.g. flag if FPR > α × 1.5).
2. **An explicit "Recommended" row** with a one-line rationale ("lowest CI width
   among methods with FPR within budget").
3. **A plain-language per-method verdict** — e.g. *"z-test on this metric:
   well-calibrated, FPR 5.1%"* / *"naive t-test on this ratio metric: FPR inflated
   to 11%, do not use."*
4. **`abk validate --report`** is the canonical artifact; **explore links to it**
   and shows the live calibration chip.

## 5. Calibration is always visible in explore (must-fix)

The real-α signal must **not** live only in a separate command. In the explore
cockpit:
- a persistent chip shows **calibrated / FPR=X.X% vs nominal α** for the *current*
  knob combination (red when out of budget);
- the **Auto** mode runs `validate` server-side and re-seeds the knobs;
- **Apply is gated/confirmed** when the active `method_params` have never passed
  `validate` — so an analyst cannot ship a mis-calibrated method without seeing the
  cost.

## 6. Cost & scaling (must-fix)

`validate` multiplies per-interval compute by N iterations × the method grid, and
bootstrap A/A is the expensive corner. Therefore:
- **default to closed-form methods** (the `from_suffstats` path, microseconds);
- **gate bootstrap A/A** behind explicit opt-in with reduced `n_samples` and a
  **subsampled** unit population (a representative subsample is sufficient for FPR
  calibration);
- parallelize placebo iterations across the now-reentrant `default_rng` generators;
- document expected runtime/memory as a function of `N × grid × method_class`.

## 7. `_ab_aa_runs` (audit)

`run_id`, `experiment`, `metric`, `method_config_id`, `iterations`, `inject_effect`,
`empirical_fpr`, `peeking_fpr`, `power`, `achieved_mde`, `ci_coverage`, `verdict`,
`created_at`. Informational; never pruned by `clean`. Feeds the
[blind-rederivation arbitration](statistics-changes.md#0-the-process) — when legacy
vs blind-derived methods disagree, this table decides.

## 8. Worked example (to author with the implementation)

A concrete worked matrix on a real metric — showing a well-calibrated z-test, an
FPR-inflated naive t-test on a ratio metric, and the peeking-FPR jump fixed by
sequential — lands here as the readability deliverable (the matrix's analyst-facing
clarity *is* the feature, not the numbers).
