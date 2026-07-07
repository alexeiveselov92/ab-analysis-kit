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

1. Take real data for the unit population. **As built (D1):** the source is the
   experiment's **own pooled cohort** rendered over the real cadence grid (reusing
   `RecomputeBackend.load_cutoff` + the metric loaders), *not* a separate historical
   window. Pooling the per-variant arrays and permuting the unit→arm labels destroys
   any true treatment effect and yields an exact null by construction (the standard
   permutation-A/A) while exercising the real grid, cadence, cohort, and metric SQL —
   no exposure-free loader, no torn `_ab_exposures` write (shuffling is in-memory
   only). A dedicated pre-experiment historical window is a recorded follow-up, not a
   correctness gap (the permutation already removes the effect).
2. **A/A (false-positive):** repeatedly (N iterations, e.g. 1000–10000) draw
   **placebo splits** where, by construction, there is **no** true effect; run the
   candidate method(s); record whether each falsely rejects H₀ at the configured α.
   **Empirical FPR** = share of placebo runs that flagged significance. A
   well-calibrated method gives FPR ≈ α. The significance primitive is the readout's
   own **CI-excludes-zero** rule (`_build_sig_map`), not the raw `reject` flag, so
   z-test / bootstrap edge cases follow the readout.
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
  grid, daily or sub-day — cumulative-intervals.md §6) and record whether it *ever*
  falsely calls a winner across the experiment lifetime. **As built (D3):** the
  peeking FPR is the naive **optional-stopping** hazard — the share of placebos whose
  CI **excludes zero at *any* look** (pre-horizon refusal OFF, the horizon look
  included, so peeking ≥ single-look by construction), modelling the analyst who
  eyeballs the daily chart and stops the first time the CI clears zero. It is
  deliberately **not** the official readout verdict ("CI excludes zero *and*
  stabilized"): that rule's trailing-window stabilization-persistence is the tool's
  *defense* against peeking — run literally it drops the peeking FPR *below* the
  single-look rate, the opposite of the hazard this column exists to expose. The
  stabilized-with-persistence rule stays the official verdict (`pipeline/readout.py`
  untouched); this column measures the trap it defends against. The **single-look
  FPR** (horizon cutoff only) is reported *beside* the peeking FPR so the jump is
  visible. For closed-form methods this is near-free: per-interval sufficient
  statistics are computed once per placebo split and prefix-summed across the grid
  (the `accumulate.py` merge primitive). Very dense grids may be subsampled (cap ~100
  points, denser early where the FPR accrues fastest) — the matrix states the
  `(kept, total)` count when it did.
- Report **effect exaggeration at stop** (winner's curse) as a first-class column
  beside FPR: conditional on stopping early, the effect estimate is biased away
  from zero — at hundreds of looks this is the analyst's biggest practical trap
  and FPR alone does not show it.
- Surface this as a **headline number** beside the nominal α — e.g. "nominal α 5%,
  **real peeking FPR 14%**" — in the matrix, the HTML report, and the explore chip.
- **Sequential side-by-side is deferred to M5 (D8).** Showing the same metric with
  `sequential.enabled` beside the fixed-horizon peeking FPR — so the analyst sees the
  always-valid CI bring it back to ≈ α — needs the sequential engine (`stats/sequential/`,
  mSPRT/alpha-spending), which lands in M5; all M4 rows carry `ci_kind='fixed'`. M4
  renders the fixed-horizon peeking FPR + the single-look FPR (the honest jump) and
  leaves the "with sequential" column as a documented placeholder. This is how we keep
  the fixed-horizon default *honest* without changing it — completed in M5.
- **Composed multiple-testing (partial in M4; full sweep in M5 — D9).** M4 measures
  each cell's peeking FPR over the grid and writes rows at the correct **two-tier
  Bonferroni** effective per-comparison alphas (so the composition's alpha half is
  exercised and the chip reads the same alpha the readout does). The full empirical
  composed FDR/FWER (config-time Bonferroni × read-time BH × peeking) over the
  **multi-metric** family is deferred to M5; read-time BH already shipped in M3.

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

**As-built columns (D15 — this supersedes the earlier sketch).** The shipped model
(`abkit/database/tables.py`, `_AaRunsMixin`) is a superset-with-renames of the
original sketch; `find_calibration`, the chip, and the D3 gate are all built against
it, so M4 writes these and this section matches the code:

`experiment`, `run_id`, `metric`, `method_name`, `method_params`, `method_config_id`,
`mode` (the `--scoring` selection objective: `fpr`|`power`|`mde`), `iterations`,
`alpha` (the **effective post-correction per-comparison** alpha the chip looks up),
`injected_effect`, `fpr` (single-look), `peeking_fpr`, `power`, `achieved_mde`,
`coverage`, `effect_exaggeration`, `verdict`, `details` (canonical JSON: the peeking
curve, `(kept,total)` subsample note, the selection rationale, warnings),
`status` (`success`|`failed`), `error_message`, `created_at` (the strictly-monotonic
LWW version, stamped by `save_aa_run`).

Renames from the sketch: `inject_effect`→`injected_effect`, `empirical_fpr`→`fpr`,
`ci_coverage`→`coverage`. The PK stays `(experiment, run_id)` under
`ReplacingMergeTree(created_at)`; a matrix is one row **per cell** via a per-cell
`run_id = "{run_stamp}:{cell_hash}"` (D4), never a shared id that would collapse. A
`status='failed'` row (or one with `fpr` null) is kept for audit but never counted by
`find_calibration`. Informational; never pruned by `clean`. Feeds the
[blind-rederivation arbitration](statistics-changes.md#0-the-process) — when legacy
vs blind-derived methods disagree, this table decides.

## 8. Worked example

A concrete worked matrix — the readability deliverable (the analyst-facing clarity
*is* the feature). The numbers below are the deterministic output of `abk validate`
over the synthetic A/A fixture in `tests/_helpers/synthetic_ab.py` (320 units, a
14-day daily grid, 2000 placebo splits, nominal α = 5%, `aa_fpr_budget` = α × 1.5 =
7.5%), pinned by the exit-gate e2e `tests/e2e/test_validate_matrix.py`. The original
sketch imagined a "well-calibrated z-test / inflated t-test on a ratio"; the real
fixtures **invert which method breaks** — the mechanism (a variance-underestimating
test on non-independent observations) is identical, but here it is the z-test on a
*clustered proportion*, while the ratio method is well-calibrated. The matrix reports
what is true, not the hypothetical.

| metric (kind) | method | single-look FPR | peeking FPR (14 looks) | power @ δ=15% | CI coverage | verdict |
|---|---|---|---|---|---|---|
| `arpu` (sample) | `t-test` | **5.3%** ✅ | 8.6% | 96% | 95% ✅ | well-calibrated |
| `conversion` (fraction, `nobs`>1) | `z-test` | **42.4%** ❌ | 43.5% | 95% | 55% ❌ | FPR inflated, **do not use** |
| `ctr` (ratio) | `ratio-delta` | **4.8%** ✅ | **12.7%** ⚠ | 100% | 95% ✅ | calibrated single-look; peeking breaks budget |

Reading it — the three classic failures the matrix exists to catch:

1. **Well-calibrated (`arpu` / `t-test`).** A t-test on a per-unit continuous metric.
   The placebo split is exchangeable, so the single-look FPR sits on α (5.3%, inside
   the 7.5% budget) and the CI covers the injected truth at the nominal 95%. This is
   the reference row: *this is what calibrated looks like*.

2. **FPR inflated — a naive test on non-independent data (`conversion` / `z-test`).**
   `conversion` is a proportion whose per-unit `nobs` > 1 (each user contributes
   several correlated trials). A two-proportion z-test pools the trials as independent
   Bernoulli draws, so it **underestimates the variance** — and the more days of
   clustered trials accumulate, the worse it gets (12% at a 4-day horizon, **42% at
   14 days**). The *same* underestimate collapses CI coverage to **55%** (vs the 95%
   it claims). This is the A/B analog of "a naive t-test on a ratio metric": both
   ignore within-unit correlation. Verdict: **do not use** — reach for a method that
   accounts for the clustering (a delta-method ratio, or the metric re-expressed
   per-unit).

3. **Peeking breaks a calibrated method (`ctr` / `ratio-delta`).** `ratio-delta` is
   *correctly* specified for the ratio metric — its single-look FPR is a healthy 4.8%.
   But the product **is** the daily cumulative chart, and an analyst who stops the
   first time the CI clears zero is running 14 correlated looks. That optional-stopping
   FPR is **12.7%** — over budget on a method that passes every single-look check. FPR
   alone would green-light it; the peeking column is what exposes the trap. Sequential
   analysis brings this back toward α without changing the fixed-horizon default (§3, D8).

The **Recommended** row and its one-line rationale, the budget-band colors, and this
peeking headline ("nominal α 5%, real peeking FPR 12.7%") are what `abk validate
--report` renders and the explore calibration chip surfaces live (§4, §5).

### 8.1 The sequential column (D8) and the composed family (D9) — shipped in M5; sequential × composed in M6 (WP-B)

With `sequential: {enabled: true}` on a sequential-eligible method, `abk validate` adds
the always-valid twin **beside** the fixed peeking column (never asserting it, only
measuring — §6.5). The always-valid confidence sequence widens the CI (~1.55× at the
horizon) so it is peeking-valid at *every* look, which is exactly what pulls the
optional-stopping hazard back to ≈α:

| metric (kind) | method | peeking FPR (fixed) | peeking (always-valid) | CI width fixed → AV |
|---|---|---|---|---|
| `ctr` (ratio) | `ratio-delta` | **12.7%** ⚠ | **≈5%** ✅ | ×1.55 |

That is the honest completion of the peeking story: the fixed column *diagnoses* the
optional-stopping trap; the always-valid column *is* the defense, at the cost of a wider
interval. (Pinned by the D8 headline test in `tests/validate/test_scoring.py`; the report
renders it as a "peeking (AV)" column + a second green curve.)

**The composed family (D9).** Per-cell FPR is necessary but not sufficient: an experiment
runs a *family* of metrics under a shared assignment, corrected by two-tier Bonferroni
(compute-time) ∘ Benjamini-Hochberg (read-time). `abk validate` sweeps the empirical
**family-wise error rate** (any false rejection across the family) and **false-discovery
rate** (mean false fraction among rejections) over one shared union-cohort placebo
assignment per iteration, under the *same* composed rule the readout applies. On the
placebo (complete) null the two coincide by construction (every rejection is false), and
they sit at the composed rule's **nominal rate** — ≈α *per tier*, so ≈2α whole-family
under the default two-tier Bonferroni (which protects the main tier and the secondary
tier each at α, by design). The budget is therefore anchored to that nominal rate (× the
`aa_fpr_budget` headroom), so "over budget" means the **methods** are miscalibrated
(clustering / variance underestimation — the family analog of the §8 z-test), not that
the correction is loose. A planted true effect in one metric leaves the null metrics'
family error controlled (the D12 story, pinned by `tests/validate/test_family_sweep.py`).
It is one sentinel `_ab_aa_runs` row and a composed-family band above the report matrix.

**Sequential × composed — shipped in M6 (WP-B).** The composed sweep now mirrors the
per-cell D8 trio at the family level, composing three matched families over the *same*
shared assignments under the *same* composed rule (only the marginals differ):

| composed family error | how each member is scored | controlled? |
|---|---|---|
| `fwer`/`fdr` (single-look) | fixed CI at the **horizon** — the readout's honest fixed decision | ≈ nominal (unchanged from M5) |
| `fwer_peeking`/`fdr_peeking` | fixed CI **peeked across every look** — the composed optional-stopping hazard | **inflated** ⚠ |
| `fwer_sequential`/`fdr_sequential` | always-valid CI peeked across every look (D8 estimator) | **≈ nominal** ✅ |

The peeking pair is the family analog of the per-cell "peeking FPR → always-valid"
recovery: where the fixed column breaks budget across looks, the always-valid twin returns
to ≈ the composed nominal. It is computed in one walk per member and gated on a
sequential-eligible family (≥1 member has a frozen τ²) — an all-ineligible family (e.g.
bootstrap-only) shows only the single-look column. The only sequential-ineligible methods
are the bootstrap family, which cannot be scored from sufficient statistics at all (they
need per-unit samples), so an ineligible member is a **full gap** in every family (honestly
disclosed by the "scored in 0 iterations — excluded from the family error" warning), not a
fixed-peeking-only rider — the peeking pair therefore always composes over the *same*
eligible member set, keeping the recovery story apples-to-apples. The numbers live in the
sentinel row's `details.family`
(no schema column — additive to the M5 sentinel); the report renders a "peeking →
always-valid" stat in the composed band. Zero method-math changes (no `ALGORITHM_VERSION`
bump — this is a validate-layer MODE transform, the D8 estimator reused verbatim). Pinned
by the D8×D9 headline tests in `tests/validate/test_family_sweep.py` + the sequential-matrix
e2e. **Only `alpha_spending`/group-sequential remains a v2 deferral** (a
`scheme: alpha_spending` config error names it).
