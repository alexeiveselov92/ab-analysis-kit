# M5 Implementation Plan — sequential analysis + `abk plan` + the deferred A/A columns

> The as-designed contract for M5, in the shape of
> [m4-implementation-plan.md](m4-implementation-plan.md). Canonical for M5 work.
> Governing specs: [cumulative-intervals.md §6](cumulative-intervals.md),
> [statistics-changes.md §4](statistics-changes.md),
> [aa-false-positive-matrix.md](aa-false-positive-matrix.md),
> [cli-and-dx.md §1](cli-and-dx.md),
> [data-contract-and-reporting.md](data-contract-and-reporting.md). Keep
> `.claude/rules/` + ROADMAP + CHANGELOG in sync at the WP9 exit gate.

## 0. Progress & resume note (2026-07-06)

**Status: WP1 + WP2 + WP3 (all 3 parts) + WP4 + WP5 shipped; WP6 / WP7→WP8
(independent tracks) + WP9 exit gate remain.** WP5 = the sub-day anytime-valid SRM:
below 1d cadence (`experiment.is_sub_day()`) the χ² gate — which would peek the strict
0.001 hard gate dozens of times a day — swaps to the Dirichlet-multinomial e-process
(Lindon & Malek 2022; `abkit/stats/srm.py::sequential_multinomial_srm`,
`statistics-changes.md §4.2`), valid at every look by construction. Daily & coarser are
byte-unchanged. The verdict is stamped **per look** from the cumulative as-of exposure
counts (`_exposures.py::get_exposure_count_stream`, exclusive `exposure_ts < end_ts`
edge) — the truthful as-of series the M2 whole-cohort broadcast deferred; the driver
dispatches on `is_sub_day()` and threads the per-cutoff `SrmResult` into
`rows_for_cutoff` (the readout/report already key off the latest row per series, so no
readout change). Default prior = the paper's uniform `Dir(1,…,1)`; the anytime
false-alarm rate holds ≤ α for **any** fixed prior (only power depends on it), so the
prior is a documented power knob, not an A/A-arbitrated correctness constant. Additive
gate, not a registered method: **no `ALGORITHM_VERSION` bump, goldens untouched**, no
schema change (reuses `srm_flag`/`srm_pvalue`). Tests: `tests/stats/test_srm_sequential.py`
(the `BF(10,0)=1024/11` exact-rational KAT + a `gammaln` re-derivation at rel-1e-12, the
anytime-false-alarm Binomial-band sim, planted-imbalance power, running-max stickiness),
`tests/database/test_internal_tables.py::TestExposures` (the as-of stream), and
`tests/pipeline/test_pipeline.py::TestSubDaySrmGate` (sub-day per-look dispatch vs daily χ²
broadcast). Full suite green (1490 passed / 1 skipped). WP4 = the readout
under sequential: the pre-horizon withholding branch (`readout.py`) refuses
WIN/LOSE/FLAT before the planned horizon only when the persisted row's `ci_kind ==
'fixed'`, so an `always_valid` row reads early; an early decisive verdict now names
its own justification in the rationale ("called before the planned horizon under an
always-valid confidence sequence — peeking-safe by construction"), and the M3
placeholder wording ("sequential CIs land in M5") is replaced by the shipped-toggle
message. The weekly-cycle representativeness caveat is promoted to a structured
`PairVerdict.weekly_cycle_pct` (baked by `reporting/builder.py`) that
`report.ts#buildVerdictCard` renders as a chip on the verdict card (`report.js`
rebuilt + committed; the human caveat string is kept in the data for CLI/BI/JSON and
filtered from the report's caveat list to avoid duplication). The daily-SRM-under-
sequential posture (plan D9) is recorded in `data-contract-and-reporting.md §6`: daily
& coarser keep χ² (bounded looks on a 3.3σ hard gate ⇒ negligible peeking inflation);
only sub-day (WP5) swaps to the anytime multinomial. No `ALGORITHM_VERSION` moved,
goldens untouched. Tests: `tests/pipeline/test_readout_sequential.py`,
`tests/reporting/test_builder.py` (chip bake), `web/test/smoke.mjs` (chip render +
caveat filter). WP1 = the sequential
engine (`abkit/stats/sequential/`, `TestResult.ci_kind`, `supports_sequential`, 81
tests, `statistics-changes.md §4.1`). WP2 = the A/A D8 column end-to-end (scorer +
`_ab_aa_runs` persistence + report/chip), incl. the peeking→always-valid recovery test,
the one-engine/one-τ² parity test, and the rebuilt `report.js`. **WP3 part 1** = the
pipeline activation: `analyze_cutoff` widens each eligible pair via `to_always_valid`
(`stats/sequential/apply.py`), the driver freezes per-pair τ² from the **first usable
look** (D-Seq-anchor, below), `enrich` emits `result.ci_kind`, and `scheme:
alpha_spending` is a "planned M6" config error. **WP3 part 2 (B4 — toggle
self-invalidation):** `_results.series_pair_ci_kinds` reads the persisted per-pair
`ci_kind` (non-demoted, FINAL); the driver's `_sequential_mode_changed` predicate
compares it against the mode this run stamps (`always_valid` iff `seq_eligible` **and**
the pair has a frozen τ²) and force-re-plans the series on a mismatch by dropping
`computed` (the re-saved rows supersede the stale ones by LWW — `ci_kind` is not
identity-bearing so the PK is stable; **no delete**, which would strand a cutoff a
widened `data_lag` pushed past the watermark). Idempotent (a steady sequential experiment
plans zero) and correct under the multi-pair anchor quirk (a pair usable only after the
anchor is legitimately left fixed → no false re-plan). The catalog is **not** consulted
(it is informational — "the pipeline never reads it back for decisions"), so the
provenance is grounded in `_ab_results`. **WP3 part 3 (B5 — explore threading):** the
cockpit is a read-view over persisted rows, so `recompute()` widens a pair live **iff its
baked rows are already always_valid** (`av_pairs` off the persisted `ci_kind`) — this
reproduces the baked per-pair vocabulary exactly, including the multi-pair anchor case and
a not-yet-applied config toggle (either direction), and is gated on the live method's
`supports_sequential`. `_sequentialize_points` widens each reconstructed (Tier E/S) point
of an `av_pair` with `to_always_valid` under the first-usable-look τ²; α-inversion cutoffs
are dropped with a Reload hint (they cannot be honestly widened from an already-widened
persisted CI); a bootstrap knob switch turns the mode off. **Server-only — no `web/src`
edit, no bundle rebuild** (the client draws whatever bounds the reply carries). This WP3
completion was adversarially reviewed (3 lenses → refute-by-default verifiers); the one
confirmed defect (a per-pair τ² anchor over-widening a pair the pipeline left fixed) is
fixed by the persisted-`ci_kind` `av_pairs` gate and pinned by a 3-arm late-rollout e2e
test. Goldens untouched, no `ALGORITHM_VERSION` moved, ruff/black clean; the full suite is
green. **Next: WP4–WP9.** Branch `claude/m5-wp3-rest` off `main`
(all of M4 merged; working tree was clean at cut). This plan was produced by a
design workflow (6 parallel spec+code readers → a synthesizer → 3 adversarial
critics under refute-by-default) whose critics found six real defects in the
naive plan — each folded in below. Three milestone-shaping scope questions were
put to the maintainer and **answered** (2026-07-06):

1. **Sequential schemes in M5 → always-valid ONLY.** `alpha_spending`
   (group-sequential) is **deferred to M6** as a named follow-up (exactly how
   D8/D9 were deferred from M4). Rationale (maintainer): mSPRT/CS covers the core
   "I look at the dashboard whenever I want" use-case; group-sequential is the
   rarer fixed-readout-schedule/efficiency case; shipping a half-wired,
   un-A/A-validated numeric family is worse than deferring it cleanly. This
   right-sizes WP1 (L→M) and removes the orphaned `alpha_spending` activation
   + config work the critics flagged as invariant-violating.
2. **Always-valid estimator → asymptotic Gaussian confidence sequence**
   (Waudby-Smith & Ramdas 2021 / GAVI), **not** exact Robbins/Howard mSPRT. The
   pure `abkit.stats` core exposes `(effect, SE)` sufficient statistics, not raw
   observation streams; an exact mSPRT would need running sums threaded through
   the core **and every backend loader**. The asymptotic CS is a closed-form
   transform of the already-computed fixed CI, is method-agnostic, and delivers
   the same anytime-valid peeking defense (the guarantee is asymptotic-anytime,
   which is exact enough at A/B-test N — and is documented as such, never
   over-claimed as finite-sample-exact mSPRT).
3. **`abk plan` scope → sizing now, runtime/ASN → M6.** v1 ships required-N /
   achievable-MDE / achieved-power / projected look-count + cost & memory
   pre-flight. The `cli-and-dx.md §1` word "runtime" (days-to-N from a unit-
   arrival rate + the sequential design's expected/average sample number) is
   deferred to M6 with an explicit spec amendment so the contract word is
   honestly qualified, not silently unmet.

**Six defects the review caught in the naive plan (all verified in code, all
fixed in the WPs below):**

| # | Defect | Fix (WP) |
|---|---|---|
| B1 | `alpha_spending`/`group_sequential` would ship un-activated (no readout path, no config) **and** un-A/A-validated → violates "every new numeric family gets A/A validation" | Cut to M6 (scope answer 1); always-valid is the single M5 scheme |
| B2 | Engine signature took a pydantic `SequentialConfig` → breaks `tests/stats/test_purity.py` (pydantic forbidden under `abkit.stats`) | WP1: engine takes **plain primitives**; `pipeline/analyze.py` translates config→primitives |
| B3 | SE rebuilt from per-arm `std/size` drops the delta-method covariance term → silently miscalibrated for relative/CUPED/ratio-delta | WP1: **SE = `ci_length / (2·norm.ppf(1−α/2))`** (invert the already-computed CI; preserves the covariance baked into `ci_length`) |
| B4 | Toggling `sequential.enabled` silently no-ops — planner anti-join keys on `method_config_id` (correctly excludes sequential), so a bare `abk run` recomputes nothing and leaves stale `ci_kind='fixed'` rows | WP3: **sequential-mode provenance** + planner forced re-plan on config↔row mode mismatch (also guards a future τ² change) |
| B5 | Explore live recompute (`tuning/recompute.py`) never threaded → the PRIORITY interface mixes fixed & always-valid CIs on one chart | WP3: thread the transform into the explore recompute tiers + session/payload lockstep |
| B6 | Estimator mislabeled as exact Robbins mSPRT; a `(effect, SE)` transform is an **asymptotic** CS → wrong KAT target & over-claimed guarantee | WP1 + scope answer 2: name it asymptotic Gaussian CS, cite WSR, pin the KAT to the CS radius, large-n coverage sim with a tolerance band |

Plus the ordering fix (validation WP2 **gates** activation WP3/WP4), two WP
splits, one spurious dependency edge dropped, and the ≥2-review-round exit-gate
bar carried over from M4.

---

## 1. Work packages in strict dependency order

### WP1 — `abkit/stats/sequential/` engine: the always-valid confidence sequence (pure, NEW) ✅ DONE

**Goal:** a pure, I/O-free module that turns a fixed-horizon `(effect, SE)` into
an **asymptotic Gaussian confidence sequence** (always-valid CI + always-valid
p-value) at a given look, as an **experiment-level mode transform** — never a
method plugin and never a name special-case (invariant 3). Default off ⇒ zero
existing numbers move (invariant 2).

| Source | Target | Verdict |
|---|---|---|
| — (the asymptotic CS radius, WSR 2021 / GAVI) | `abkit/stats/sequential/confidence_sequence.py` (`sequentialize(effect, se, tau2, alpha) -> (lo, hi, av_pvalue)` + `se_from_ci_length(ci_length, alpha)`) | **NEW ✅** |
| — (the mixture-variance policy τ²) | `abkit/stats/sequential/mixture.py` (`mixture_tau2(horizon_variance, alpha)` — the ONE source of τ², shared by pipeline + A/A) | **NEW ✅** |
| `abkit/stats/result.py` (`TestResult`) | `+ ci_kind: str = "fixed"` field (additive; `to_dict` picks it up via `fields()`) | A |
| `abkit/stats/base.py:244-253` (declarative capability attrs) | `+ supports_sequential: ClassVar[bool] = True` (bootstrap/paired-without-SE families set False) | A |
| `abkit/stats/effects.py` (`normal_test`, `norm.ppf`) | read-only — the fixed CI whose `ci_length` we invert | — |

**Hotspots:**
- **SE recovery by CI-inversion, not arm-variance rebuild (B3).** Every
  parametric method builds its fixed CI via `effects.normal_test` using
  `scipy.stats.norm.ppf` (the normal-approx baseline — even `t-test`), so the CI
  is symmetric about `effect`: `SE = ci_length / (2 · norm.ppf(1 − alpha/2))`.
  This **exactly** preserves the delta-method relative variance (with its
  negative covariance term, `effects.py:relative_delta_effect`) and the pooled-θ
  CUPED variance already baked into `ci_length` — it is method-agnostic and never
  re-derives arm variances. A KAT pins that the recovered SE round-trips the
  fixed CI at rel-1e-9, and that on a fixed method the sequential CI **contains**
  the fixed CI (always-valid ⇒ wider) at the same look.
- **The estimator is an asymptotic CS, named honestly (B6).** With `V = SE²` and
  fixed mixture variance τ², `sequentialize` computes the normal-mixture radius
  `r = sqrt( (2·V·(V+τ²)/τ²) · ( ln(1/α) + 0.5·ln((V+τ²)/V) ) )` (inverting the
  mixture LR `Λ(θ₀)=sqrt(V/(V+τ²))·exp(τ²(θ̂−θ₀)²/(2V(V+τ²)))`, a martingale ⇒
  Ville); `[effect − r, effect + r]` is the always-valid CI and the always-valid
  p-value is its dual `min(1, 1/Λ(0))` (`p ≤ α` iff 0 excluded). The guarantee is
  documented as **asymptotic-anytime** (finite-sample if the estimate were exactly
  Gaussian; the coverage sim is large-n with a stated tolerance band, never a
  finite-sample exact assertion).
- **τ² is fixed-by-policy, from ONE helper (B4/D4/D5).** `mixture_tau2(horizon_
  variance, α) = u*(α)·horizon_variance`, `u*` solving the width-at-horizon
  stationarity `u = 2·ln(1/α) + ln(1+u)` (so the CS is tightest at the planned
  horizon; validity holds for any fixed positive τ²). It is **not** user-facing
  config (kept out
  of `SequentialConfig`); it lives in the pure core and is called identically by
  the pipeline activation (WP3) and the A/A column (WP2) — otherwise the A/A
  validates a different estimator than ships (the WP2 byte-identity test pins
  this). Any future change to `mixture_tau2` is a `statistics-changes.md §4` entry
  + the mode-provenance re-plan (WP3), never a silent CI move.
- **Purity (B2).** `abkit/stats/sequential/` imports only numpy/scipy + stdlib +
  `abkit.stats.*`; **no pydantic/config type crosses the boundary** — the engine
  takes plain primitives (`effect`/`se`/`tau2`/`alpha` floats).
  `analyze.py` (the impure layer) does the `SequentialConfig → primitives`
  translation. `tests/stats/test_purity.py` gains explicit coverage that
  importing `abkit.stats.sequential` pulls in no forbidden module.
- **`alpha_spending` is a clean deferral, not dead code.** `scheme` stays a
  `Literal["always_valid", "alpha_spending"]` in the schema, but WP1 ships only
  the `always_valid` branch; `alpha_spending` raises a clear
  `"scheme: alpha_spending is planned for M6 — use always_valid"` at the config
  layer (WP3), so no half-wired numeric path ships.

**Tests:** `tests/stats/sequential/test_confidence_sequence.py` (KAT: recovered
SE round-trips the fixed CI at rel-1e-9; the CS strictly contains the fixed CI;
KAT radius vs a hand-computed normal-mixture constant; p ≤ α ⇔ 0 excluded);
`test_coverage.py` (large-n Monte-Carlo: anytime coverage ≥ 1−α across a
**data-dependent** look schedule within a documented tolerance band — the
peeking property, asymptotic); `test_mixture.py` (τ² policy KAT: `u*` fixed-point,
linear in `horizon_variance`); purity extension in `tests/stats/test_purity.py`.
Golden tests **untouched** (default-off parity, invariant 2). **✅ 81 sequential
tests + 606 stats/golden green; ruff/black clean.**

**DoD:** ✅ `sequentialize(effect, se, tau2, alpha) -> (lo, hi, av_pvalue)`
is pure, deterministic, primitive-only, and produces an always-valid CI that
provably contains the fixed CI; τ² comes from the one shared `mixture_tau2`
helper; `TestResult.ci_kind` exists; `statistics-changes.md §4.1` carries the new
family (asymptotic-anytime, opt-in, no existing `ALGORITHM_VERSION` moved).

**Must-fixes discharged:** *never change a number silently* — new family is
change-controlled here; existing methods keep `ALGORITHM_VERSION=1`.

---

### WP2 — A/A matrix D8: the sequential side-by-side peeking-FPR column — **validation gates activation** (engine + persistence, A) ✅ DONE

**Goal:** run the WP1 transform over the M4 placebo panel to measure the
always-valid **peeking FPR** beside the fixed one, and — because §6.5 says
"sequential power/CI-width is measured **side-by-side, never asserted**" — also
the sequential **power, CI-width, and effect-exaggeration**. This IS the A/A
validation the change-control invariant mandates for the new family, so it
**must green before** the pipeline ships user-visible always-valid CIs (WP3/WP4).

| Source | Target | Verdict |
|---|---|---|
| `abkit/validate/scoring.py` (`score_cell`, the peeking/CI-excludes-zero primitive over `TestResult` streams) | `+ sequential columns` (peeking_fpr_sequential, power_sequential, ci_width_sequential, exaggeration_sequential) | A |
| `abkit/stats/sequential/{confidence_sequence,mixture}.py` | consumed via `abkit.stats` (validate is I/O-pure like the M4 engine) | — |
| `abkit/validate/runner.py` + `_ab_aa_runs` persistence | `+ the sequential-column fields on the per-cell row` | A |
| `abkit/reporting/calibration.py` + `web/src/report/report.ts` (`buildCalibrationSection`) | `+ fixed-vs-sequential side-by-side block` (reuse `--abk-st-*` tokens; no new hex) | A |
| `abkit/tuning/payload.py` + `web/src/explore/explore.ts` | `+ the sequential FPR on the live calibration chip` (matrix/report/chip stay in sync, aa-fpr §3) | A |

**Hotspots:**
- **Same panel, same τ², one estimator.** The D8 column reuses the M4 in-memory
  placebo panel and the exact `_significance` (CI-excludes-zero) primitive, but
  over the WP1 always-valid bounds instead of the fixed ones. `info_n` and τ² come
  from the **same `mixture_tau2` helper** the pipeline uses (WP1) — a byte-identity
  test pins that the A/A cell and a pipeline row produce identical CS bounds on the
  same suffstats, so the "peeking FPR back to ≈α" proof is not vacuous.
- **Peeking, defined identically to M4/D3.** `peeking_fpr_sequential` = the share
  of placebos whose **always-valid** CI excludes zero at **any** look across the
  real cadence grid. The always-valid construction is exactly what should bring it
  back to ≈α where the fixed peeking FPR blew the budget — the honest completion
  of the M4 peeking story.
- **Power/width guard against a mis-scaled τ² (major finding).** FPR-only lets a
  τ² that "fixes" FPR by *never rejecting* pass silently. WP2 also measures
  sequential power (on the injected-effect fixture), CI-width, and
  effect-exaggeration side-by-side; a test asserts sequential power stays
  materially above α on the injected fixture.
- **Persistence: additive columns, same `run_id` discipline.** The sequential
  numbers ride on the existing per-cell `_ab_aa_runs` row (D4 `run_id =
  "{run_stamp}:{cell_hash}"`, effective two-tier alpha) — no new table, no
  `ReplacingMergeTree` collapse. `aa_runs_table_exists()` still guards the report
  block.
- **Three surfaces in sync.** The report calibration section, the HTML matrix, and
  the **live explore chip** all gain the fixed-vs-sequential view (aa-fpr §3/§5);
  rebuild + commit `report.js` **and** `explore.js` (CI freshness gate, pathspec
  `:(glob)abkit/*/assets/**`).

**Tests:** `tests/validate/test_scoring.py` (+ sequential FPR≈α on the null panel
within a Binomial band; sequential power > α on the injected fixture; width ≥ fixed
width); `tests/validate/test_sequential_parity.py` (A/A CS bounds ≡ a pipeline row's
CS bounds byte-for-byte on shared suffstats); `tests/reporting/` + `tests/tuning/`
bundle tests for the new block/chip; `web/test/` jsdom smoke.

**DoD:** the matrix renders fixed **and** sequential peeking FPR + power + width per
cell; the sequential FPR returns to ≈α on the null panel; the chip/report/matrix
agree; τ² is provably pipeline-identical. **This WP is the gate**: WP3/WP4 do not
merge until D8 is green on the seeded fixture.

**Must-fixes discharged:** *peeking is the product* (the sequential completion) +
*new family gets A/A validation* (this is that validation).

---

### WP3 — pipeline + explore activation: thread `ci_kind`, self-invalidate the toggle, keep the cockpit consistent (A) — ✅ DONE (part 1 transform + first-look τ² + ci_kind + config; part 2 toggle self-invalidation B4; part 3 explore threading B5)

**Goal:** make `sequential.enabled: true` actually emit always-valid rows on a bare
`abk run` **and** in the live explore recompute — the two compute paths — without
either silently no-op'ing or mixing CI vocabularies. Gated on WP2 (validated τ²).

| Source | Target | Verdict |
|---|---|---|
| `abkit/pipeline/analyze.py` (`compare_pair` wrap) | translate `SequentialConfig`→primitives, call `sequentialize`, set `ci_kind="always_valid"` when enabled & `method.supports_sequential` | A |
| `abkit/pipeline/enrich.py:114` (hardcoded `"ci_kind": "fixed"`) | emit the actual `ci_kind` from the result | A |
| `abkit/compute/recompute_backend.py` | pass the sequential mode through the closed-form recompute path | A |
| `abkit/pipeline/driver.py:162-166` (anti-join on `method_config_id`) + `abkit/database/internal_tables/` | **sequential-mode provenance** + forced re-plan on mismatch (B4) | A + **NEW** |
| `abkit/tuning/recompute.py:679-698` + `session.py` + `payload.py` (+ `web/src/explore/explore.ts`) | thread `sequentialize` into the explore recompute tiers; mark bounds `always_valid` (B5) | A |
| `abkit/config/experiment_config.py:366` + `validator.py` | `alpha_spending` → clear "planned M6" error; keep the sub-1d honesty lints (already M2) | A |

**Hotspots:**
- **The toggle must self-invalidate (B4).** `ci_kind` and `sequential.enabled` are
  correctly **not** in `method_config_id` (D7 — else toggling would orphan the fixed
  series), so the planner's `list_computed_cutoffs` anti-join treats a
  sequential-enabled experiment as fully computed and skips it. Fix: persist the
  effective sequential mode (`enabled` + `scheme` + a hash of the τ² policy +
  version) in **row provenance**; the planner treats a config↔latest-row mode
  mismatch as a forced re-plan of that experiment's grid (an implicit full-refresh
  for the affected series), **or** the CLI fails loud with "sequential mode changed
  — re-run with `--full-refresh`". A pipeline test flips `sequential.enabled`, runs
  **without** `--full-refresh`, and asserts rows recompute to `ci_kind='always_valid'`
  (not skipped). This same mechanism guards a future τ²-policy change from silently
  overwriting a published always-valid CI at the same key.
- **The explore cockpit is the PRIORITY interface (B5).** `tuning/recompute.py`
  builds a method via `create_method` and writes `left_bound/right_bound` with no
  sequential transform; unthreaded, a sequential experiment's baked (session-load)
  series carries always-valid CIs while any live knob recompute (Tiers E/α/S)
  produces fixed CIs at the same points — two vocabularies on one chart. WP3 threads
  the transform into the tiers and marks emitted bounds `always_valid`. The
  **α-inversion Tier** cannot invert an always-valid CI the fixed way → it gets an
  explicit unsupported/degradation path (honest, not a silent fixed fallback).
- **Config guards are mostly M2 already.** `max_looks`/`warn_looks` (project),
  `data_lag`-required-sub-1d, `covariate_lookback` lints, the "sequential lets you
  decide earlier" messaging (`validator.py:297-301`) all shipped in M2; WP3 only
  adds the `alpha_spending`→M6 error and the `always_valid` auto-recommendation
  copy below 1d.

**Tests:** `tests/pipeline/test_sequential_activation.py` (enabled ⇒ rows are
`ci_kind='always_valid'`; toggle self-invalidates without `--full-refresh`; disabled
⇒ byte-identical to today); `tests/tuning/test_sequential_recompute.py` (live
recompute returns always-valid bounds; α-inversion Tier degrades honestly);
`tests/config/` (`alpha_spending`→M6 error). `web/test/` chart smoke.

**DoD:** a sequential-enabled experiment emits `ci_kind='always_valid'` on a plain
`abk run` and in explore; the toggle can never silently no-op; the cockpit never
mixes CI vocabularies; disabled path is byte-identical (goldens green).

**Must-fixes discharged:** *the toggle silently no-ops* + *explore consistency*
(both blocking completeness gaps).

---

### WP4 — readout under sequential: lift pre-horizon withholding for always-valid, caveat chip, daily-SRM decision (S) — **DONE**

**Goal:** let the readout call WIN/LOSE/FLAT pre-horizon **only** when
`ci_kind=='always_valid'`, with the representativeness caveat surfaced, and decide
the daily-cadence-under-sequential SRM question explicitly. Small — the readout
branch already exists.

| Source | Target | Verdict |
|---|---|---|
| `abkit/pipeline/readout.py:566-567` (`if not is_horizon and ci_kind == "fixed"`) | already lifts the refusal for `always_valid` — WP4 adds verdict **wording** + tests | A |
| `abkit/pipeline/readout.py:519` (weekly-cycle caveat string) | surface as a **rendered chip** on early sequential verdicts (§6.5 "chip") | A |
| `abkit/reporting/builder.py` + `web/src/report/report.ts` (+ `report.js` rebuild) | the caveat-chip field + render | A |

**Hotspots:**
- **The branch is already there.** `readout.py:567` withholds only when
  `ci_kind=='fixed'`, so an `always_valid` row already skips pre-horizon
  withholding once WP3 emits it. WP4 is mostly: verdict rationale wording ("called
  early under an always-valid CI"), the caveat **chip**, and tests — not new
  decision logic.
- **Representativeness stays mandatory even under sequential (§6.5).** Any WIN/LOSE
  before `min(7d, horizon)` carries the "covers X% of a weekly cycle" caveat. The
  spec says **chip**; `readout.py:519` already emits the string and the report keeps
  it in rationale — WP4 promotes it to a rendered chip (builder flag + report.ts +
  `report.js` rebuild/commit, CI freshness gate). Decision recorded in D13.
- **Daily-cadence SRM under sequential — explicit decision (D9).** A
  sequential-enabled **daily** experiment peeks the χ² SRM hard gate across daily
  looks. Decision: **keep χ² at daily cadence** (the gate runs at α=0.001 with a
  bounded daily look count, so the peeking inflation on a 3σ hard gate is
  negligible), and swap to the anytime-valid multinomial SRM (WP5) only **below
  1d** per §6.5. Recorded in `data-contract-and-reporting.md §6`, not left open.

**Tests:** `tests/pipeline/test_readout_sequential.py` (always-valid row ⇒ WIN/LOSE
allowed pre-horizon; caveat chip present before 7d; fixed row ⇒ still withheld);
`tests/reporting/` chip render; `web/test/` smoke.

**DoD:** early WIN/LOSE is permitted **only** under `always_valid`, always carries
the weekly-cycle chip, and the daily-SRM posture is documented.

---

### WP5 — sub-day sequential-multinomial SRM (Lindon & Malek) — independent (stats + gate, A) ✅ DONE

**Goal:** below `1d` cadence, replace the per-cutoff χ² SRM (itself peeking on a
hard gate → false alarms) with the anytime-valid sequential multinomial test
(Lindon & Malek, NeurIPS 2022). **Independent of WP1** (it does not consume the
`(effect, SE)` transform — the spurious edge was dropped).

| Source | Target | Verdict |
|---|---|---|
| `abkit/stats/srm.py` (`srm_check`, χ² gate) | `+ sequential_multinomial_srm(counts_stream, expected_split, prior)` (anytime-valid) | **NEW** |
| `abkit/pipeline/driver.py` SRM call site | dispatch on `experiment.is_sub_day()` → sequential vs χ² | A |
| `abkit/database/internal_tables/` (persisted per-cutoff counts) | read prior-cutoff counts to assemble the cumulative count stream at each look | A |

**Hotspots:**
- **The mixture/prior must be specified (minor finding).** The anytime multinomial
  test needs its own mixture/prior parameter (the multinomial analog of τ²); an
  unspecified prior makes the false-alarm KAT non-reproducible. WP5 fixes the
  default prior in `statistics-changes.md §4` and pins the false-alarm KAT to it.
- **Assemble the cumulative stream from persisted cutoffs.** The driver plans
  cutoffs via the incremental anti-join and writes per-cutoff; the anytime test
  needs the running count history, so WP5 reads prior-cutoff counts from the
  persisted rows to build the stream at each look — without breaking the
  incremental read path.
- **Blocking-but-non-dropping is preserved.** Like χ², the sequential gate writes
  `srm_flag`/`decision_blocked` and never drops rows.

**Tests:** `tests/stats/test_srm_sequential.py` (null-stream anytime false-alarm ≤ α
across a data-dependent look schedule; KAT vs the pinned prior; a planted imbalance
fires); `tests/pipeline/` dispatch test (sub-day ⇒ sequential, daily ⇒ χ²).

**DoD:** below 1d the SRM gate is anytime-valid with a documented prior + a pinned
false-alarm KAT; daily is unchanged; `statistics-changes.md §4` carries it.

**Must-fixes discharged:** *SRM peeking on a hard gate* (sub-day).

---

### WP6 — `abk plan` — read-only pre-launch power/sizing planner — independent (CLI + pure engine, NEW)

**Goal:** a pre-launch command that reports required-N / achievable-MDE /
achieved-power at the configured α, plus the projected **look count** and **cost &
memory** pre-flight before accepting a (sub-day) grid. **runtime/ASN deferred to
M6** (scope answer 3).

| Source | Target | Verdict |
|---|---|---|
| `abkit/stats/power.py` (power/MDE: t-test, CUPED-deflated, proportions) | reused for the sizing math | — |
| `abkit/core/period_planner.py` (`generate_grid`) | reused to echo the projected look count for the configured cadence | — |
| `abkit/cli/commands/plan.py` (lazy Click group; tree `_output`; non-zero exit) | **NEW** command | **NEW** |
| `abkit/validate/plan.py` or `abkit/planning/` (pure sizing engine) | **NEW** pure module | **NEW** |

**Hotspots:**
- **Baseline moments source (D10).** Sizing needs per-arm baseline
  mean/std/prop/corr. `abk plan` reads them from the latest persisted `_ab_results`
  per-arm stats (a running experiment), with a `--baseline` CLI-flag fallback and a
  clear **refuse-if-absent** path (a truly greenfield experiment with no data and no
  flags cannot be sized — say so, don't guess).
- **Strictly read-only (D11).** Like `explore`, `abk plan` takes **no lock** and
  writes **no `_ab_*` rows**. It may read the warehouse for baseline moments (its
  own serialized manager, closed in `finally`).
- **Cost & look-count echo (§6.4).** For a sub-day / schedule cadence, `plan` echoes
  `generate_grid`'s look count and the projected warehouse-load cost + memory shape
  **before** accepting the grid — the same numbers `run`/config-lint use.
- **Honest scope boundary.** `ratio-delta` has no unversioned MDE/N formula in
  `power.py` → `plan` **refuses** ratio metrics with a clear message rather than
  invent math (adding ratio power is a future stats-core change-control item, not an
  M5 side effect). "runtime"/ASN is deferred with a `cli-and-dx.md §1` amendment so
  the contract word is qualified.

**Tests:** `tests/cli/test_plan_command.py` (required-N/MDE/power golden on a seeded
config; look-count echo matches `generate_grid`; refuse-if-no-baseline; ratio
refusal; read-only — no lock, no rows written); `tests/planning/test_sizing.py`
(pure KAT vs `power.py`).

**DoD:** `abk plan <experiment>` prints sizing + look-count + cost/memory, is
strictly read-only, refuses what it cannot size honestly; runtime/ASN carried as an
explicit M6 deferral in the decision log + `cli-and-dx.md`.

---

### WP7 — extract the composed-correction rule from the readout into a shared helper — independent, M4-only (refactor, A)

**Goal:** a behavior-preserving extraction of the composed multiple-testing rule
(config-time two-tier Bonferroni ∘ read-time Benjamini-Hochberg) from the readout's
`_build_sig_map` into a shared pure helper, so WP8's family sweep and the readout
apply **one** rule. No new feature. Depends only on M4-shipped code (not the
sequential core) → parallelizable from day one.

| Source | Target | Verdict |
|---|---|---|
| `abkit/pipeline/readout.py` (`_build_sig_map`, two-tier Bonferroni ∘ BH) | `abkit/stats/correction.py` (or `abkit/pipeline/_correction_rule.py`) shared helper | A |
| `abkit/stats/correction.py` (`two_tier_alphas`, `benjamini_hochberg`, `n_comparisons`) | reused; the helper composes them | — |

**Hotspots:**
- **Behavior-preserving, snapshot-pinned.** The extraction must not move a single
  verdict; a readout run-verdict snapshot test pins the composed rule byte-for-byte
  before and after. This is the higher-risk half of the old monolithic D9 WP,
  isolated so it reviews on its own regression evidence.

**Tests:** `tests/pipeline/test_correction_rule.py` (the helper ≡ the old inline rule
on a matrix of `(is_main, n_comparisons, p-values)`); the existing readout verdict
tests stay green unchanged.

**DoD:** the composed rule is one shared, tested helper; the readout is refactored to
call it with zero verdict change (snapshot-pinned).

---

### WP8 — A/A matrix D9: the composed multi-metric FDR/FWER empirical sweep (engine + persistence + report, A)

**Goal:** the full composed multiple-testing validation — empirical family-wise
error (from two-tier Bonferroni) **and** false-discovery rate (from read-time BH)
over the **multi-metric family**, using WP7's shared rule. M4 validated only the
per-cell peeking FPR at the correct two-tier alphas; D9 closes the family-level loop.

| Source | Target | Verdict |
|---|---|---|
| `abkit/validate/scoring.py` + `runner.py` | `+ a joint-family scoring path` (per-cell marginals untouched) | A |
| `abkit/validate/resample.py` / `load.py` | one shared unit→arm placebo split per iteration across the whole metric family | A |
| `abkit/stats/correction.py` (WP7 helper) | applied to the family's marginal p-values per iteration | — |
| `_ab_aa_runs` + `abkit/reporting/calibration.py` | a family-level sentinel row + report block (empirical FWER + FDR + budget band) | A |

**Hotspots:**
- **One shared arm mask over the pooled cohort (D11).** Each unit gets **one**
  placebo arm assignment per iteration (the real single-assignment semantics); each
  metric is scored on the units for which it is defined under that shared mask. The
  unit universe is the **union** of the metrics' cohorts; a unit present in metric A
  but absent in B simply doesn't contribute to B (no imputation — that would bias
  FDR). Documented + tested.
- **Report BOTH rates (D11).** The composition mixes compute-time Bonferroni (FWER
  control) and read-time BH (FDR control), so D9 reports the empirical **FWER**
  (any false rejection in the family) **and** the empirical **FDR** (expected false
  fraction among rejections), with the budget band on the family FWER
  (`aa_fpr_budget` generalized to the family). Both computed per iteration via the
  WP7 helper, averaged across iterations.
- **Fixed-horizon only in M5.** D9 composes Bonferroni × BH at the horizon;
  **sequential × composed** (Bonferroni × BH × mSPRT-peeking) is a recorded M6
  follow-up. Soft-serialize this WP's `validate/` + `report.js` edits after WP2 for
  merge hygiene (not a logical dependency).

**Tests:** `tests/validate/test_family_sweep.py` (empirical FWER ≈ nominal on the
null family within a band; FDR ≈ 0 under the complete null; a planted true effect in
one metric leaves the other metrics' FDR controlled; union-cohort handling);
`tests/reporting/` family block render.

**DoD:** the matrix reports empirical family FWER + FDR over the multi-metric family
under the composed rule; the shared-mask/union-cohort semantics are pinned;
sequential×composed is an explicit M6 deferral.

---

### WP9 — the M5 exit gate: e2e, worked example, ≥2 adversarial review rounds, docs sync (last)

**Goal:** prove M5 end-to-end, run the milestone review at the M4 bar, and flip all
the as-built docs.

**Scope:**
- **e2e** (`tests/e2e/test_sequential_matrix.py` + extensions): a sequential-enabled
  experiment where the fixed peeking FPR breaks budget and the **always-valid** column
  brings it back to ≈α (the honest completion, in a Binomial band, byte-repro); the
  toggle self-invalidates and recomputes to `ci_kind='always_valid'`; `abk plan`
  golden output; the D9 family sweep controls FWER/FDR on the seeded family; WP5
  sub-day SRM anytime false-alarm.
- **Worked example** in `aa-false-positive-matrix.md §8` (the sequential column) +
  `cli-and-dx.md` (`abk plan` transcript).
- **≥2 full adversarial review ROUNDS** (the M4 lesson, recorded in the M4 progress
  memory): round-1 (N lenses, refute-by-default, second verifier per finding) → land
  verified fixes → round-2 re-review over the patched tree → only then the closing
  `fix(m5)` commit. **Both rounds' records** appended to this doc's §5. (A single
  pass is below the bar — round-2 caught an incompleteness in a round-1 fix in M4.)
- **Docs/rules sync:** `ROADMAP.md` (M5 shipped; alpha_spending/group-sequential +
  sequential×composed carried as named M6 deferrals; `abk plan` runtime/ASN → M6),
  `CHANGELOG.md` (the new sequential family + `abk plan` + D8/D9),
  `.claude/rules/architecture.md` + `contributing.md` (flip "M4 shipped → M5
  shipped"; add `stats/sequential/` to the layout), `CLAUDE.md` status line.

**DoD:** CI fully green (all Python versions + E2E-ClickHouse + bundle freshness);
≥2 review rounds recorded; goldens untouched; docs tell one story.

---

## 2. Dependency graph / parallelism

```
WP1 (sequential engine) ──▶ WP2 (A/A D8 — validates & freezes τ²) ──▶ WP3 (activate: pipeline+explore) ──▶ WP4 (readout)
                                                                                                              │
WP5 (sub-day multinomial SRM) ── independent ───────────────────────────────────────────────────────────────┤
WP6 (abk plan) ─────────────── independent ───────────────────────────────────────────────────────────────┤
WP7 (extract composed rule) ── independent (M4 only) ──▶ WP8 (A/A D9 family sweep) ─────────────────────────┤
                                                                                                              ▼
                                                                                                    WP9 (exit gate)
```

- **Critical path:** WP1 → WP2 → WP3 → WP4 (validation gates activation).
- **Three parallel tracks from day one:** WP5 (SRM), WP6 (`abk plan`), WP7→WP8
  (composed-family). WP8 soft-serializes its `validate/`+`report.js` edits after WP2
  for merge hygiene only — no logical dependency on the sequential core.
- **Dropped edges:** WP5→WP1 (spurious — SRM doesn't consume the transform);
  WP8→WP1 (D9 is fixed-horizon in M5).

## 3. Decisions — the open points, settled here

- **D1 — Sequential is an experiment-level MODE, not a method plugin.** A transform
  over each base method's already-computed `(effect, SE)`, dispatched on the
  declarative `supports_sequential` capability flag (never an isinstance/name check).
  Families without a well-defined normal-approx SE (some bootstraps) set
  `supports_sequential=False` and are gated declaratively.
- **D2 — Always-valid CI = asymptotic Gaussian confidence sequence (WSR 2021 / GAVI),
  not exact Robbins/Howard mSPRT.** *(maintainer-confirmed)* Forced by the pure
  suffstats contract (`(effect, SE)`, not raw streams). Documented as
  asymptotic-anytime; the coverage sim is large-n with a tolerance band.
- **D3 — SE by CI-inversion:** `SE = ci_length / (2·norm.ppf(1−α/2))`. Preserves the
  delta-method covariance already baked into `ci_length`; method-agnostic; never
  re-derives arm variances. Rejected: rebuilding SE from per-arm std/size (drops the
  covariance term for relative/CUPED/ratio-delta — B3).
- **D-Seq-anchor — τ² is anchored to the FIRST usable look** (maintainer-confirmed,
  WP3). Anchoring to the horizon (statistically tightest at the planned stop) is not
  live-computable during an ongoing experiment (the horizon is in the future) and would
  make the pipeline and the A/A disagree; the first usable grid cutoff is **stable across
  runs** (idempotent), **computable live**, and **tightest early** (aligned with the
  always-valid use-case: the impatient experimenter peeks early). Both the pipeline
  (`driver._sequential_tau2`) and the A/A (`scoring._cell_tau2`) use this rule + the
  shared `mixture_tau2`. `mixture_tau2`'s arg was renamed `horizon_variance →
  reference_variance`. Rejected: horizon-anchor (not live-computable; deferred to M6 with
  `abk plan`'s planned-N), config planned-N (would block WP3 behind WP6).
- **D4 — τ² is fixed-by-policy from one shared helper** (`mixture_tau2(reference_variance, α)`),
  anchored to horizon information N, called identically by pipeline (WP3) and A/A
  (WP2). Not user-facing config. A future τ² change is a `statistics-changes.md §4`
  entry **and** triggers the mode-provenance re-plan (D7) — never a silent CI move.
- **D5 — The engine takes plain primitives; no config type crosses into
  `abkit.stats`** (purity, B2). `analyze.py` translates `SequentialConfig`→primitives.
- **D6 — `alpha_spending` / group-sequential deferred to M6** *(maintainer-confirmed)*
  as a named follow-up. `always_valid` is the single M5 scheme; `scheme:
  alpha_spending` raises a clear "planned M6" config error. Rejected: shipping it
  un-activated + un-A/A-validated (violates invariant 2/the new-family rule — B1).
- **D7 — `ci_kind` is a row field (`fixed|always_valid`), not part of
  `method_config_id` or the LWW key; `sequential.enabled` is not identity-bearing.**
  BUT the effective sequential mode (scheme + τ²-policy hash + version) is carried in
  **row provenance**, and the planner forces a re-plan on config↔row mode mismatch —
  this is what makes the toggle self-invalidating (B4) and guards a future τ² change.
- **D8 — Default off = byte-identical legacy parity:** no existing `ALGORITHM_VERSION`
  moves; goldens untouched. The new sequential math is change-controlled via
  `statistics-changes.md §4` + CHANGELOG + A/A (WP2 is that A/A).
- **D9 — Sub-day SRM swaps to anytime-valid multinomial (Lindon & Malek) below 1d
  only; daily stays χ²** (α=0.001 hard gate + bounded daily look count ⇒ negligible
  peeking inflation). Recorded in `data-contract-and-reporting.md §6`. The multinomial
  prior default is pinned in `statistics-changes.md §4`.
- **D9a — the sub-day gate default prior is the paper's uniform `Dir(1,…,1)`** (WP5,
  as-built). The anytime false-alarm guarantee (Ville on the mixture martingale) holds
  for **any** fixed positive prior — only the stopping time (power) depends on it — so
  the prior is a documented power knob, **not** an A/A-arbitrated correctness constant
  (unlike τ², whose numeric value D8/WP2 arbitrated). No magic concentration constant is
  invented; a mean-pinned `k·θ0` concentration is exposed as an opt-in, unused in M5.
  The gate uses the same strict `DEFAULT_SRM_ALPHA = 0.001` as χ². Rejected: inventing a
  concentration `k` the paper does not name. (`statistics-changes.md §4.2`.)
- **D9b — the sub-day verdict is PER LOOK, not the M2 whole-cohort broadcast** (WP5,
  as-built). Each cutoff's rows carry their look's running anytime verdict, stamped from
  the cumulative as-of exposure counts (`get_exposure_count_stream` over `_ab_exposures`,
  exclusive `exposure_ts < end_ts` — matching the metric-load windows), reconstructed in
  full from the persisted cohort each run (recompute-not-incremental, in keeping with the
  v1 read path). The readout/report already key SRM off the *latest* row per series, so
  the reported status is the current anytime verdict with **no readout change**; the gate
  runs even on demoted rows (§6.1(4)). Rejected: broadcasting the frontier verdict to all
  rows (loses the truthful as-of history that §5.3 reserves the slot for).
- **D10 — `abk plan` baseline moments** from the latest persisted `_ab_results`
  per-arm stats, with a `--baseline` flag fallback and a refuse-if-absent path; ratio
  metrics are refused (no unversioned ratio-power math). runtime/ASN deferred to M6
  *(maintainer-confirmed)*.
- **D11 — `abk plan` is strictly read-only** (no lock, no `_ab_*` writes), like
  explore.
- **D12 — D9 reports empirical FWER and FDR** over the multi-metric family under the
  composed Bonferroni∘BH rule (WP7 helper), one shared unit→arm mask per iteration,
  union-of-cohorts unit universe (no imputation). Fixed-horizon only in M5;
  sequential×composed → M6.
- **D13 — the representativeness caveat renders as a chip** (§6.5 wording), added
  in WP4 (builder flag + report.ts + `report.js` rebuild).

**Still genuinely open (A/A-arbitrated, not blocking):** the exact numeric τ² policy
constant inside `mixture_tau2` — proposed anchor-to-horizon-N, but the final value is
whatever WP2's A/A shows returns peeking FPR to ≈α at acceptable power (measured, not
asserted — §6.5). Recorded in `statistics-changes.md §4` once WP2 fixes it.

## 4. M5 definition-of-done → WP map

| M5 obligation (ROADMAP / specs) | WP |
|---|---|
| `stats/sequential/` always-valid engine (opt-in, `ci_kind`) | WP1 |
| A/A `sequential.enabled` side-by-side peeking-FPR column (D8) | WP2 |
| Sequential CIs replace fixed-horizon when enabled (contract activation) | WP3 |
| Readout decides pre-horizon under always-valid; weekly-cycle chip | WP4 |
| Sub-day anytime-valid multinomial SRM (Lindon & Malek) | WP5 |
| `abk plan` pre-launch power/sizing + look-count/cost echo | WP6 |
| Composed multi-metric FDR/FWER empirical sweep (D9) | WP7 + WP8 |
| Sub-day cadence honesty guards (max_looks/warn_looks/data_lag/…) | **already M2**; WP3 adds only `alpha_spending`→M6 + `always_valid` recommend |
| `alpha_spending` group-sequential; A/A sequential×composed; `abk plan` runtime/ASN | **deferred M6** (D6/D12/D10) |
| Milestone exit gate (e2e + ≥2 review rounds + docs sync) | WP9 |

## 5. Adversarial review record (M5 exit gate)

_TBD at WP9 — ≥2 full rounds (round-1 → fixes → round-2 re-review), both recorded
here, per the M4 lesson._
