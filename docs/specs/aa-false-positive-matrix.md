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
2. **A/A (false-positive):** repeatedly (N iterations; since M7 WP6 the default N
   is resolved **per cell** as `max(2000, ⌈200/α⌉)` at the cell's effective alpha —
   see §6) draw
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

**Iteration policy (M7 WP6, `0.2.0`).** The flat `DEFAULT_ITERATIONS = 2000` is
replaced by a per-cell default tied to that cell's **effective** alpha:
`N = max(2000, ⌈200/α⌉)` (`runner._default_iterations`) — ≈4000 at the 5% main tier,
≈40000 at a 0.5% secondary tier — so the FPR estimate's relative SE stays roughly
constant across tiers instead of starving tight secondary alphas. `-n`/`--iterations`
remains a hard override applied to every cell; the persisted row's `iterations`
column always records the **resolved** N that actually ran. Per the §4.1 maintainer
call the auto-N is **never hard-capped**: above 100 000 the runner logs a
warn-and-continue decision entry (silently truncating a configured alpha tier would
be worse than a long run now that the engine is vectorized). The family sweep sizes
its one shared draw count at the **tightest** member alpha. In the same WP the
composed family sweep (D9) stopped auto-running on every multi-metric invocation —
it is **opt-in via `--family-sweep`** (`ValidateSettings.family_sweep`, default
`False`); a bare multi-metric run logs a one-release migration notice, and
`--family-sweep` combined with `--metric` is logged-and-skipped (one metric has no
family). Auto mode (`POST /validate`) keeps its explicit reduced N and does not opt
in — the D3 chip keys on per-cell rows only. Neither change moves a statistical
number (Monte-Carlo sample size and which passes run are not method math): no
`ALGORITHM_VERSION` bump, no `statistics-changes.md` entry.

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
14-day daily grid, an **explicit** 2000 placebo splits — the e2e passes `iterations=`
so the WP6 auto-N policy is bypassed — nominal α = 5%, `aa_fpr_budget` = α × 1.5 =
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

### 8.1 The sequential column (D8) and the composed family (D9) — shipped in M5; sequential × composed shipped in M6 (WP-B)

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

## 9. Implementation note — the vectorized scoring engine (M7)

**As of M7 (WP2–WP5), `score_cell` runs a block-streamed vectorized engine by
default — with zero statistical numbers moved** (no `ALGORITHM_VERSION` bump;
the exit-gate e2e above is byte-identical). The spec-level contract, so a
future contributor finds the invariant here and not only in code comments:

- **Dispatch is a plugin capability.** The five closed-form methods
  (`t-test`, `z-test`, `cuped-t-test`, `paired-t-test`, `ratio-delta`) opt in
  via `supports_vectorized` + `from_suffstats_array` (`abkit/stats`, WP2 —
  bit-exact vs the scalar kernels by construction, `_libm_pow`); the bootstrap
  family and any plugin without a batch kernel run the original per-iteration
  loop, preserved verbatim as `_score_cell_scalar`. Validate never breaks for
  a method that only implements `from_suffstats`.
- **Block-streaming under one memory budget** (`vector_resample.py`, WP3 —
  the bootstrap engine's `BLOCK_QUANTUM` discipline): placebo-mask blocks where
  row *i* IS `placebo_mask(derive_seed(*parts, start+i))` (bit-identical by
  construction), per-cutoff arm suffstats via one GEMM per block, everything —
  block working set plus the hoisted prepared cutoffs — under a single shared
  256 MiB cap. Blocking is a pure function of `(iterations, n_units)` plus
  module constants, so persisted A/A numbers stay byte-reproducible
  run-to-run (D13) **under a fixed BLAS configuration** — a different BLAS
  build/thread count re-rounds continuous columns at ~1e-15 rel (counts and
  curves unaffected; the Poisson bootstrap engine ships with the same scope).
- **The scalar↔vectorized parity contract**, exhaustively gated by
  `tests/validate/test_vector_parity.py` (≥50 seeds × 8 shapes —
  sample/CUPED/absolute/fraction/ratio plus three adversarial stress shapes:
  gap-heavy sparse, CUPED-at-the-`MIN_ARM_UNITS`-floor, saturating-clamp
  injection — ± injection, a trip-wire pinning every `CellScore` field to a
  parity class, plus scanned-and-pinned deterministic seeds for the rare
  τ²-unanchorable and no-valid-horizon states): **integer counts, decisions,
  curves, warnings and `achieved_mde` agree exactly** (both engines in one
  process share one BLAS configuration, so exact asserts are CI-safe;
  `achieved_mde` is bit-identical by construction — the vectorized MDE seam
  rebuilds the control arm through the scalar `build_arm` on the row's own
  mask after review round 1 caught the GEMM-column read flipping None↔0.0 at
  the 2-unit-CUPED corr≡±1 knife-edge, a divergence that fed the
  Recommended-row tie-break); **continuous means agree at rel-1e-9**
  (GEMM/pairwise vs sequential `.sum()` reduction order, inside the
  `|value|/σ ≲ 1e10` conditioning band — the WP3 finding; measured battery
  deviations sit at ≤ ~2e-14, the bound is the principled band, not slop).
  One **documented corrupt-input divergence** (pre-existing, WP4's batch main
  pass): a fraction arm whose aggregate successes exceed its trials (per-unit
  over-counting — a metric-SQL bug, statistically nonsensical) fails the cell
  loudly on the scalar engine at `Fraction` construction, while the batch
  main pass scores it (the pooled proportion can stay finite) and the MDE
  seam skips such rows (reporting-only never kills a cell) — pinned by a
  dedicated regression test; hardening the batch degenerate flag to also
  reject `count > nobs` is a named follow-up, deliberately NOT done in M7
  (an ULP-level `count ≈ nobs` knife-edge on legitimate fractional panels
  could then fail cells the scalar engine scores). **The same class holds
  for the family sweep (WP7)**: the scalar engine crashes the sweep at
  `Fraction` construction (surfaced by the runner as a failed family) while
  the batch engine scores the family — pinned by its own regression test in
  `test_family_vector_parity.py`; the named follow-up covers both engines.
- **The honest limit of count exactness** (the §0.3(3) mandate, measured): a
  CI bound *manufactured* onto the decision boundary (brentq-solved injected δ,
  `|left_bound| ≲ 1e-15`) can flip that single decision between the engines —
  both roundings are correct; the input is ill-conditioned at the boundary.
  At ≥1e-9 offset from the boundary parity is exact. Both regimes are pinned
  (`test_near_boundary_parity`, `test_at_exact_boundary_divergence_…`). This
  is a property of *any* reduction-order change, not a defect: real cells sit
  at generic positions, and the e2e matrices are byte-identical.
- **The perf gate is executable** (`tests/validate/test_vector_perf.py` — the
  track's "a rule without an executable gate does not hold" lesson): the
  REPORT reference cell (2000 iterations × 100 grid cutoffs × 1000 units,
  null + injected + sequential columns) must finish under a generous 10 s CI
  bound sized against the **coverage-instrumented** measurement — the CI Test
  job traces `--cov=abkit`, and the tracer roughly doubles the cell
  (dev-measured ~1.3–1.7 s bare, ~2.2–2.5 s under coverage, vs ~25 s scalar —
  the WP4 ~10× record).
- **The composed family sweep (D9) has its own vectorized engine (WP7)** —
  `family.py`'s hot loop is separate from `score_cell`'s (the §0.3(1)
  plan-review correction), so `sweep_family` carries the same
  dispatch-on-`supports_vectorized` contract: block-streamed union masks +
  per-member GEMM batches feed the UNCHANGED per-iteration
  `composed_significance`, the scalar loop survives verbatim as the
  any-member-not-opted-in fallback, and the parity gate
  (`tests/validate/test_family_vector_parity.py`) asserts **every
  `FamilyScore` field exact** on inputs both engines score (all columns are
  count ratios, exact-fraction sums, or passthroughs — no rel-1e-9 class at
  the family level; the corrupt-input divergence above is the one documented
  exception, where the scalar engine refuses what the batch engine scores).
  A structural kernel raise (a member whose method demands columns its panel
  lacks) gaps that member in both engines — the batch engine carries the
  scalar `except Exception` net, with `NotImplementedError` re-raised so a
  lying `supports_vectorized` flag still fails loudly. Scope caveat (review
  round 2): under `sequential=True` a member whose τ² *anchor* raises
  structurally crashes BOTH engines identically inside the shared, unguarded
  `_cell_tau2` (pre-existing, symmetric, runner-isolated); the engine net
  covers members whose anchor degenerates to None instead — guarding
  `_cell_tau2` itself is a named follow-up (it changes both engines at
  once). Measured ~18× on a 3-member × 2000-iteration sequential reference,
  byte-identical output.
