# M4 Implementation Plan — `abk validate`, the A/A false-positive matrix

> **Working plan, not a design contract.** Synthesized 2026-07-05 from the specs
> plus a 7-extraction survey (the donor `detectkit/autotune` scaffolding + its CLI
> and tests, the abkit `stats` seams, the pipeline/loaders/config seams, the
> `tuning`/explore + `_ab_aa_runs` consumer seams, the CLI/reporting seams, and the
> canonical requirements pulled from `aa-false-positive-matrix.md`, `quorum-review`,
> ROADMAP M4, `cli-and-dx`, `data-contract-and-reporting`, `cumulative-intervals` §6,
> and `statistics-changes` §0), against the as-built code. The specs stay canonical;
> where this plan settles an open point that amends a spec (D1–D17 note which) the
> spec is amended in the same PR. Updated as work packages land; archive at M4 close
> with the §5 adversarial-review record.
>
> **Contradiction audit across the extractions:** no reader-vs-reader conflicts —
> all seven agree the M3 landing pad is already built (`_ab_aa_runs` shipped with the
> full matrix column set, `find_calibration`/`resolve_fpr_budget`/the 501 `/validate`
> stub/the report `calibration:null` slot all reserved). Five spec↔spec / spec↔code
> tensions the plan must resolve, each settled in a D-item: (1) the §3 "sequential
> side-by-side" column requires a stats engine that lands only in M5 → **D8**;
> (2) "the actual readout decision rule ('CI excludes zero and stabilized')" is
> self-defeating for peeking — the as-built readout *refuses* pre-horizon WIN/LOSE and
> requires trailing-window stabilization-persistence, which is the *defense* against
> peeking (empirically a peeking FPR at/below the single-look rate), the opposite of
> the hazard the column must expose → **D3** (optional-stopping); (3) `aa-fpr` §7's column names diverge from the shipped
> `_ab_aa_runs` model → **D15** (as-built wins, spec amended); (4) the `(experiment,
> run_id)` PK under `ReplacingMergeTree` collapses a matrix written under one shared
> `run_id` → **D4**; (5) ROADMAP's "composed-FDR empirical validation (stays
> here/M4)" vs. the fact that BH read-time already shipped in M3 and full composed
> FWER needs the multi-metric sweep → **D9**.

Sources: aa-false-positive-matrix.md §1–§8, cli-and-dx.md §1 (the `validate` row) + §5
(the `abk-validate` skill), quorum-review.md (the peeking / matrix-UX / cost
must-fixes), data-contract-and-reporting.md §4–§5, cumulative-intervals.md §6,
declarative-config.md §6, §8, statistics-changes.md §0, §3, §7, ROADMAP M4 (+ the D12
deferral), the donor `detectkit/autotune/` package + `cli/commands/autotune.py` + its
unit tests, the as-built abkit seam map (tables.py:224–263, `_aa_runs.py`,
`recompute.py:235–315`, `analyze.py:58–84`, `period_planner.py:71–168`,
`recompute_backend.py`, `metric_loader.py`, `server.py:177`, `builder.py:448`,
`build.mjs`, `ci.yml`), and m3-implementation-plan.md as the format donor.

Conventions: `⟲` = port near-verbatim, `A` = adapt, `RW` = rewrite on donor skeleton,
`NEW` = no donor. All abkit paths relative to repo root; donor paths relative to
`/home/aleksei/wsl_analytics/detektkit`. Every WP is one reviewable PR (~300–900 net
LOC target; donor-port and web WPs may run larger, as in M2/M3). One conventional
commit per WP.

---

## 1. Work packages in strict dependency order

### WP1 — `abkit/validate/` engine core: placebo split + effect injection + scoring (pure, NEW)

**Goal:** the numeric heart of the matrix as a pure module over `abkit.stats` — draw
a deterministic placebo split from a pooled per-unit value vector, inject a known
effect at the sufficient-statistics level, and score FPR / cumulative-peeking FPR /
power / achieved-MDE / CI-coverage / effect-exaggeration-at-stop. No DB, no
filesystem, no click — the donor's "engine never touches I/O; the caller loads and
persists" contract (runner.py:1–13).

| Source | Target | Verdict |
|---|---|---|
| `detectkit/autotune/_types.py` (`DecisionEntry`) + `_base.py::AutoTuneError` | `abkit/validate/_types.py` (`DecisionEntry`, `ValidateError`) | ⟲ (rename) |
| — (the placebo panel contract) | `abkit/validate/panel.py` (`PlaceboPanel`, `PanelCutoff`) | **NEW** |
| — (the placebo permutation + arm build) | `abkit/validate/resample.py` | **NEW** |
| — (suffstats effect injection algebra) | `abkit/validate/inject.py` | **NEW** |
| — (FPR / peeking / power / coverage / exaggeration from `TestResult` streams) | `abkit/validate/scoring.py` (`CellScore`, `score_cell`) | **NEW** (donor `scoring.py` MCC/F-β/AUC skipped; keep only its pure-numpy no-sklearn discipline) |
| `abkit/stats/{samples,rng,power,factory}.py` | reused, never modified | — |

_(Scope note: the donor `_AutoTuneBase` decision-log/eval-cache state-holder moves to
**WP3** where the runner actually uses it — WP1 ships only the pure numeric primitives
+ `DecisionEntry`/`ValidateError`, keeping the PR the "numeric heart".)_

**Hotspots (from the stats-seams survey):**
- **Placebo split, fixed across the grid.** One iteration = one unit-level
  permutation held constant across every cutoff (a unit's arm is fixed at
  enrollment — the real assignment semantics). `rng = make_rng(derive_seed("aa",
  experiment, metric, method_config_id, iteration))`; `perm = rng.permutation(n)`;
  partition into arm-A/arm-B index sets by `expected_split` shares. Per cutoff, build
  each arm's `SufficientStats.from_sample(Sample(values[idx], cov_array=cov[idx]))`
  (samples.py:290–307, stable two-pass). The cheaper inverse-Chan variant (compute
  arm-A only, recover arm-B by algebraic subtraction of the pooled total — the
  inverse of accumulate.py:32–54) is an optimization gated behind an equivalence test;
  **clamp tiny negative `m2` to 0.0** (float cancellation; `SufficientStats` rejects
  `m2<0`, samples.py:273–274, the ratio_delta.py:52 precedent).
- **Effect injection is exact at the suffstats level (purity-safe numpy algebra).**
  Multiplicative `y→y·(1+δ)`: `SufficientStats(n, mean·(1+δ), m2·(1+δ)², cov_mean,
  cov_m2, cross_c·(1+δ))` — `corr_coef` invariant, CUPED "just works". Additive
  `y→y+c`: only `mean` shifts. `RatioSufficientStats`: scale `mean_num`,
  `m2_num·(1+δ)²`, `c_nd·(1+δ)`. `Fraction`: `count→count·(1+δ)` **clamped to
  `≤nobs`** (samples.py:135–139) — high base-rate proportions mark the cell
  "MDE unreachable", never crash. Frozen dataclasses (`JointMoments`,
  `RatioSufficientStats`) → construct new instances, never mutate. Injection lives in
  `abkit/validate/`, **not** `abkit.stats` (purity invariant; test_purity.py).
- **Scoring primitives** consume the shipped `TestResult` (result.py:30–53). The
  significance primitive is **CI-excludes-zero** (the readout's `_build_sig_map` rule,
  readout.py:195–205 — `left_bound>0` or `right_bound<0`; ≡ `reject` for the normal
  family). Single-look **FPR** = CI-excludes-zero at the horizon cutoff;
  **cumulative-peeking FPR** = CI-excludes-zero at *any* look across the grid (optional
  stopping — D3; no stabilization-persistence, so it exposes the peeking hazard rather
  than the readout's defense; `pipeline/readout.py` is left untouched). CI **coverage**
  separately counts `left_bound ≤ truth ≤ right_bound` at the horizon.
  **NaN-bound degenerate cutoffs** (zero/negative variance, effects.py:101–125) and
  arms below 2 units are tallied in their own bucket, never as clean non-rejections
  (silent FPR deflation). The injected-truth estimand is **per test_type** (D2):
  relative → `δ` exactly; absolute → `δ·μ̂_pooled(horizon)`. Effect-exaggeration =
  mean `|effect|` at the first crossing (winner's curse against a true effect of zero).
- **Determinism (D13).** Every placebo seed is `derive_seed(...)` over row identity
  (rng.py:24–34, known-answer pinned) — never RNG-global, never wall-clock. A
  separate derived stream seeds the bootstrap `n_samples` param (WP3 opt-in). The
  part convention is frozen here and pinned by a known-answer test **before any row
  is published** — it can never change afterward (H1/H2 discipline).
- **Quarantined methods** raise `QuarantinedMethodError` via `get_method_class`
  (registry.py:92–107) — the enumerator **skips-and-records** them (a decision-log
  line), never catch-and-substitutes.

**Tests:** new `tests/validate/test_resample.py` (placebo FPR ≈ α on an iid
Normal(μ,σ) null within a Binomial(N,p) 3σ band; the split is exchangeable; empty-arm
guard on tiny populations, samples.py:34–35); `test_inject.py` (suffstats-injection ≡
sample-level injection at rel-1e-9 for mean/ratio/CUPED; `corr_coef` invariance; the
`Fraction` clamp); `test_scoring.py` (known-answer FPR/power on planted streams; the
NaN-bound bucket is counted separately; coverage of `δ` for relative test_type;
exaggeration sign); `test_determinism.py` (identical seed → byte-identical FPR; the
`derive_seed` part convention known-answer). Purity: `tests/validate/` may import
`abkit.stats`, but `abkit.stats` gains no import (test_purity.py unchanged).

**DoD:** `score_cell(pooled_by_cutoff, covariate, method, alpha, grid, iterations,
inject_effect) -> CellScore` is pure, deterministic, and produces FPR / peeking_fpr /
power / achieved_mde / coverage / effect_exaggeration; the seed convention is pinned.

**Must-fixes discharged:** *validate cost bound* (quorum) — the closed-form
`from_suffstats` path is the only path this WP exercises (bootstrap is WP3 opt-in).

---

### WP2 — `abkit/validate/load.py` + the peeking-grid subsampler (data assembly, NEW)

**Goal:** hand WP1 the pooled per-unit metric values across the experiment's actual
cadence grid, plus the fixed CUPED covariate — reusing the pipeline's own loaders, and
subsampling dense grids to ~100 points denser-early with disclosure.

| Source | Target | Verdict |
|---|---|---|
| `abkit/compute/recompute_backend.py:102` (`load_cutoff` over `[grid.start_ts, cutoff.end_ts)`) + `metric_loader.py:95,216` | `abkit/validate/load.py` (own read-only manager, the `_run_reload` serialized precedent, server.py:498) | A |
| `abkit/core/period_planner.py:71–168` (`generate_grid`) | reused verbatim for the grid; new denser-early downsampler beside it | A + **NEW** |
| `tests/_helpers/synthetic_ab.py:71–163` (`SyntheticWarehouse`, `seed_all_events`) | extend with a `seed_null_events` twin | A |

**Hotspots (from the pipeline-loaders survey):**
- **Placebo data source = the experiment's own pooled cohort, label-permuted (D1).**
  A/A calibration needs the metric's real per-unit value distribution over the real
  grid; permuting unit→arm labels destroys any true effect and yields an exact null
  (the standard permutation-A/A). Load per subsampled cutoff `k` via `load_cutoff`
  (which already renders the metric SQL over `[start_ts, cutoff.end_ts)` with the
  packaged assignment macro), then **pool** the per-variant unit arrays into one
  per-unit vector in canonical sorted-unit order (the D11 byte-stability guarantee,
  metric_loader.py:186–205). CUPED covariate = one `load_covariate_from_preperiod`
  call (metric_loader.py:216) — a fixed pre-period constant per unit, so it
  prefix-sums trivially.
- **Never persist a placebo split.** Shuffling is in-memory only; `replace_exposures`
  is delete-then-insert and would clobber the real cohort (exposure_loader.py:116) —
  the load path reads, never writes, `_ab_exposures`.
- **The grid is THE one enumeration.** `generate_grid(start, end,
  experiment.cadence_segments(), tz, limit=project.limits.max_looks)` — identical to
  the driver and explore (period_planner.py; driver.py:113; session.py:134). The
  ~100-point cap is a **separate denser-early downsampler over `grid.cutoffs`**, NOT
  `generate_grid`'s `limit` (which *raises* `GridLimitExceeded`, never subsamples —
  period_planner.py:34–43). The horizon cutoff (`is_horizon`) is always retained. The
  downsampler records `(kept, total)` into the cell `details` JSON so the matrix can
  "state when it did" (aa-fpr §3, §8).
- **Cost shape (R22).** The default closed-form matrix is `N_cutoffs` warehouse loads
  (one per subsampled grid point — the pipeline's own per-cutoff semantics), then all
  `iterations × cells` scoring runs off the in-memory vectors at microseconds each.
  Memory is bounded by looping cutoffs-outer / iterations-inner, holding one cutoff's
  pooled vector at a time (O(n + iterations) live). Documented as a function of
  `N × grid × method_class` in the WP2 docstring + the cli-and-dx cost note.
- **Own manager, serialized** (the `_run_reload` precedent) so a validate load never
  contends with a live explore session or `abk run` on the connection; closed in a
  `finally`.

**Tests:** `tests/validate/test_load.py` over `SyntheticWarehouse` — `seed_null_events`
gives an analytic null (FPR ≈ α); pooling preserves canonical unit order and unit
count; the covariate loads once; the downsampler keeps ≤cap points, always retains the
horizon, is denser early, and reports `(kept,total)`; a shuffle-mode warehouse proves
order-independence.

**DoD:** `load_placebo_panel(experiment, metric, grid, project, manager) ->
PlaceboPanel{pooled_by_cutoff, covariate, grid_note}` reuses the pipeline loaders,
never writes, and bounds memory; the subsampler is denser-early and self-disclosing.

**Must-fixes discharged:** *peeking is the product* (the data half) — the peeking pass
runs over the experiment's real one-enumeration cadence grid.

---

### WP3 — `abkit/validate/runner.py` orchestrator + persistence (engine + writer, A)

**Goal:** enumerate the method cells, run the load→resample→score passes, select the
recommended row, and persist one `_ab_aa_runs` row per scored cell at the **effective
per-comparison alpha** with per-cell-unique `run_id`s — mirroring the donor's pure
runner + the abkit persist seam.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/autotune/runner.py` (`resolve_scoring`, `build_settings`, `cap_history`, `autotune_from_data` — no DB/FS) | `abkit/validate/runner.py` (`resolve_scoring`, `build_settings`, the unit-subsample cap, `validate_from_panels`) | A |
| `detectkit/autotune/settings.py:16–62` (`TuneSettings` internal vs the YAML config block — the two-layer split) | `abkit/validate/settings.py` (`ValidateSettings`) | ⟲ (reshape) |
| `detectkit/autotune/result.py` + `config_emitter.py:32–44` (`AutoTuneResult`; deterministic `compute_run_id`, no wall clock) | `abkit/validate/result.py` (`AaValidateResult`, flattened to per-cell rows); `abkit/validate/run_id.py` | A |
| `abkit/database/internal_tables/_aa_runs.py:33–44` (`save_aa_run`) + `analyze.py:58–84` (`effective_alphas`/`comparison_alpha`) | reused, never modified | — |
| `detectkit/autotune/grid_search.py` greedy sweep | **skipped** (M4 scores the full *declared* matrix — D6 — no search); keep only its `max_candidates` cost ceiling as a guard | — |

**Hotspots (from the tuning-seam + persistence surveys):**
- **Cell enumeration (D6).** A cell = `(metric, method_config, effective alpha, mode)`.
  Without `--method`, score the experiment's **configured** comparisons/methods
  (the methods actually declared); `--method m` adds/overrides specific registered
  methods to compare, filtered by `input_kind == metric.type` and `not is_paired`
  (the `knob_surface` filter, recompute.py:489–539). The full test_type×CUPED×stratify
  ×correction cartesian stays opt-in via explicit `--method` flags — MVP does not fan
  the cartesian automatically (cost bound). Quarantined methods are skipped-and-logged
  (WP1).
- **Alpha is an explicit matrix axis (D-critical).** Rows persist the **effective
  post-correction per-comparison alpha** computed through the very same
  `comparison_alpha(comparison, effective_alphas(experiment, project))`
  (analyze.py:58–84) that the session bakes, `/recompute` sends, and the Apply gate
  resolves — never a re-derivation, or the rel-1e-9 `isclose` match in
  `find_calibration` fails and every chip reads `alpha_mismatch`. Under two-tier
  Bonferroni, main vs. non-main metrics land rows at **different** alphas; the
  enumerator writes each metric at its own tier's alpha (D-note in D3).
- **`run_id` per cell (D4).** `run_id = f"{run_stamp}:{cell_hash}"` where `cell_hash =
  compute_run_id(metric, method_config_id, mode, alpha)` and `run_stamp` is a
  per-invocation deterministic id (`compute_run_id` over the frozen `now_utc_naive` +
  the selection inputs). Distinct per (invocation, cell) → no `ReplacingMergeTree`
  collapse, full audit history retained; `find_calibration` still picks the newest
  `created_at` among matching `(metric, method_config_id, alpha)`.
- **One row per cell, `fpr` always populated (D16).** A single row carries fpr /
  peeking_fpr / power / achieved_mde / coverage / effect_exaggeration; `mode` records
  the `--scoring` **selection objective** (fpr|power|mde), which never narrows which
  passes run — the whole matrix is always computed so `fpr` is non-null and the chip
  can light. `injected_effect` = the `--inject-effect` δ.
- **Selection (R5, R14).** Recommended = FPR closest-to-nominal while maximizing power
  (ties broken by narrowest CI width among in-budget rows); the rationale string
  ("lowest CI width among methods with FPR within budget") + the full matrix + the
  peeking curve (grid points, cumulative FPR) + the subsample note go into the winning
  row's `details` JSON. Budget resolves via the one existing `resolve_fpr_budget`
  (recompute.py:235; metric override completed in WP5/D12).
- **Verdicts (R15).** A plain-language `verdict` per row ("z-test on this metric:
  well-calibrated, FPR 5.1%" / "naive t-test on this ratio metric: FPR inflated to
  11%, do not use") — a small pure formatter over the cell numbers vs. budget.
- **Failure rows (R37).** A cell that raises persists `status='failed'` +
  `error_message` (never counted by `find_calibration`) rather than vanishing;
  `created_at` stays the strictly-monotonic LWW version (`save_aa_run` stamps it via
  `next_version_ts`). The invocation itself re-raises after recording (WP4 exit code).

**Tests:** `tests/validate/test_runner.py` (deterministic `run_id`; two identical
runs → equal `cell_hash`es; the selection rule picks the in-budget max-power row;
verdict strings); `tests/validate/test_persistence.py` (rows land with every
`AA_RUN_COLUMNS` key; the effective-alpha value byte-matches
`comparison_alpha∘effective_alphas`; a two-tier experiment writes main and secondary
rows at different alphas; a `status='failed'` row is written and ignored by
`find_calibration`); `tests/tuning/test_recompute.py` extension — a freshly written row
flips `find_calibration` from `uncalibrated` → `calibrated` and an alpha edit →
`alpha_mismatch`.

**DoD:** `validate_from_panels(...)` is pure and returns an `AaValidateResult`; the
command-side finalize writes per-cell rows the shipped `find_calibration` accepts;
re-runs are byte-reproducible.

**Must-fixes discharged:** *validate cost bound* (closed-form default); the chip's data
contract (the rows that flip `find_calibration`).

---

### WP4 — `abkit/cli/commands/validate.py` + the lock/finalize command (CLI, A)

**Goal:** the `abk validate` command — lazy-registered, streaming the
load→resolve→resample→score→persist→emit stages, best-effort `--report`, its own
out-of-band lock, non-zero exit on failure.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/cli/commands/autotune.py` (the `_load_project → select loop → _tune_one → acquire_lock → try{…finalize…} → release` skeleton; :318 lock, :374/:377–390 release arms, :471–496 best-effort report, :525–556 failed-audit row) | `abkit/cli/commands/validate.py` (`run_validate` → `_validate_one`) | RW on the skeleton |
| `abkit/cli/commands/run.py:53–62,92–105,160–170` (`--report` tri-state, `_resolve_report_path`, atomic tmp+`os.replace`, the one-file-many-experiments guard) | reused | ⟲ |
| `abkit/cli/main.py:36–131` (the lazy command pattern; :10–11 already reserves `validate`) + `abkit/cli/_output.py` (`StageLogRenderer`) | extend with `VALIDATE_STAGE_TITLES` | A |
| `abkit/cli/commands/unlock.py:38` (`clear_lock` defaults to `('pipeline','run')`) | extend to also clear `process_type='validate'` | A |

**Hotspots (from the CLI survey):**
- **Command surface (R27).** `abk validate --select <exp> [--method <m>]
  [--metric <m>] [--iterations N] [--inject-effect <pct>] [--scoring fpr|power|mde]
  [--report] [--bootstrap] [--force]`. `--select` resolves **experiments** only;
  `--method` is the distinct, non-polysemous method-grid flag; every selection error
  names the namespace and offers the fix (the two-level naming must-fix). `--report`
  copies run's tri-state (`is_flag=False, flag_value="", default=None`) and defaults
  the bare form to `reports/<exp>__validate.html` (the explore `__explore` suffix
  precedent) so it never clobbers the run readout.
- **Lock scope (D5).** A distinct `_ab_tasks` claim at `(experiment, 'pipeline',
  'validate')` — validate writes only `_ab_aa_runs` (never read by the run pipeline),
  so it need not serialize behind nightly runs; it takes its own process_type.
  `abk unlock` is extended in this WP to clear both `run` and `validate` process types
  (else a crashed validate leaves an invisible running row until the 3600s timeout).
  Failures are recorded on the lock row before propagating (driver.py:229–239); the
  ownership-checked `release_lock` False return is logged, never raised
  (_tasks.py:109+).
- **`ensure_tables()` up front** (the writer creates schema; read paths deliberately
  never do — `aa_runs_table_exists` precedent). Non-zero exit on any cell/harness
  failure (`raise SystemExit(1)` — the house rule, a deliberate deviation from the
  donor's swallow-and-return-0). Effective alpha + the `C(groups,2)×metrics` divisor
  are echoed in the stage tree (R28, declarative-config §6). The stage words are
  distinct from `abk run --steps validate` (config-lint) — no reused "VALIDATE" copy
  (R-gotcha: vocabulary collision).
- **Report best-effort (D-note).** The bake is wrapped in try/except appending
  "Report skipped: {exc}" — the one recorded exception to exit-non-zero (run.py:9–15
  precedent). The report is spec-canonical (§4.4) but a bake failure must not mask a
  successful validation; documented explicitly, not inherited silently.

**Tests:** `tests/cli/test_validate_command.py` — drive `_validate_one` directly with
the `FakeDatabaseManager` (the donor's private-worker pattern, no `CliRunner`):
rows written, lock acquired-then-released, a failure releases `failed` + exits
non-zero, `--force` skips acquire, the report lands at the `__validate` path, selection
errors name the namespace; `tests/cli/test_unlock.py` extension (validate lock
cleared).

**DoD:** `abk validate --select <exp>` runs end-to-end against `FakeDatabaseManager`,
persists the matrix, echoes the effective alpha, exits non-zero on failure, and is
unlockable.

**Must-fixes discharged:** the CLI + two-level naming must-fix for validate.

---

### WP5 — the matrix report + payload calibration block + `aa_fpr_budget` metric override (reporting, A)

**Goal:** `abk validate --report` HTML, the explore/report `calibration` payload block
filled with the M4 shape (no payload v-bump), and the metric-level `aa_fpr_budget`
completing the resolver chain — reusing the existing report bundle (no third JS
bundle).

| Source | Target | Verdict |
|---|---|---|
| `abkit/reporting/html_report.py:80–109` + `tuning/html.py:63–88` (the hardened one-pass `_PLACEHOLDER_RE.sub` bake, every `<`→`&lt;`, `importlib.resources` bundle read) | `abkit/validate/html.py` (bakes the matrix page reusing the **report** bundle) | ⟲ |
| `web/src/report/report.ts:207–221` + `web/src/shared/payload.ts:129–139` (the reserved `CalibrationBlock{fpr, peeking_fpr, headline, matrix_rows, report_link}`) | extend `report.ts` with a calibration-matrix section gated on `matrix_rows` presence | A |
| `abkit/reporting/builder.py:448–450` (`calibration: None` today) | fill from the latest `_ab_aa_runs` rows | A |
| `abkit/config/metric_config.py` + `recompute.py:240` (the `M4+` metric-override stub) | add `MetricConfig.aa_fpr_budget: float\|None` (fraction in (0,1]); wire `resolve_fpr_budget`'s metric arm | **NEW** (D12) |

**Hotspots (from the CLI/reporting + tuning surveys):**
- **Reuse the report bundle, no third bundle (D10).** The report payload already
  reserves the `calibration` block; the matrix is rendered as a new section of
  `report.ts` gated on `payload.calibration.matrix_rows`. This fills R16 (`--report`)
  and R36 (payload block) with **one** renderer — avoiding a fourth package-data entry,
  a new wheel-assert line, and a new hex/token shell. The peeking-FPR-vs-looks curve
  reuses the committed `chart.ts` canvas primitives; the FPR-vs-budget band colors are
  the ready-made status tokens `--abk-st-good/-warn/-serious/-critical`
  (chart.ts:50–54) — no new hex, so the CI hex/token loop is untouched.
- **Matrix UX (R13–R15).** FPR cells colored against the `aa_fpr_budget` band
  (green in-budget / red out), an explicit **Recommended** row with its one-line
  rationale, plain-language verdicts, and the "nominal α 5%, real peeking FPR 14%"
  headline beside nominal α (R10) — all read from the persisted rows + the winning
  row's `details` JSON.
- **The bake is hardened, committed-bundle-based.** `abkit/validate/html.py` copies the
  one-pass regex substitution (never `.format`/sequential replace), `&lt;`-escapes the
  payload, and reads the committed `report.js` via `importlib.resources`. A new
  `web/src/report` section means the bundle is rebuilt (`cd web && npm run build`) and
  the changed `abkit/reporting/assets/report.js` committed in this PR (CI freshness
  gate) — but **no** new `abkit/*/assets/` directory, so `build.mjs` BUNDLES,
  pyproject package-data, MANIFEST.in, and the wheel-assert stay as-is.
- **`aa_fpr_budget` metric override (D12).** `MetricConfig.aa_fpr_budget` validated as a
  fraction in (0,1] (the project-level precedent, project_config.py:93–109), threaded
  into `resolve_fpr_budget(project, alpha, metric)` (recompute.py:235, whose `metric`
  arm is stubbed). Added to the §8 L1/L2 validation matrix.

**Tests:** `tests/validate/test_report.py` (baked-payload structural asserts: the
`calibration` block carries `matrix_rows`/`headline`/`report_link`; the Recommended row
is marked; band colors resolve through the token layer; self-containment — no external
URLs, `</script` guarded); `web/test/report.matrix.test.ts` (jsdom smoke over a matrix
fixture); `tests/config/` (metric `aa_fpr_budget` L1/L2 validation); `tests/reporting/`
(builder fills `calibration` from `_ab_aa_runs`, `null` when none, no payload v-bump).

**DoD:** `abk validate --report` writes a self-contained matrix page reusing the report
bundle; the payload calibration block fills without a v-bump; the metric-level budget
override resolves.

**Must-fixes discharged:** *A/A matrix UX* (color vs budget, Recommended row,
plain verdicts) and the report half of *peeking is the product* (the headline number).

---

### WP6 — Auto mode: server-side `POST /validate` + in-session chip refresh (explore, A)

**Goal:** wire the reserved 501 `/validate` route to a real, reduced server-side
validate that refreshes the live session's calibration in place and re-seeds the knobs
to the recommended config.

| Source | Target | Verdict |
|---|---|---|
| `abkit/tuning/server.py:177–178` (the 501 stub) + `:498–553` (`_run_reload`: own manager per request, `request_lock`, request_id stale-drop, ensure_tables) | `abkit/tuning/server.py` `_run_validate` | A |
| `abkit/validate/runner.py` (WP3) | reused with a reduced-N `ValidateSettings` | ⟲ |
| `abkit/tuning/session.py:173–174` (`aa_rows` one-time snapshot) | refreshed in place under `request_lock` | A |
| `web/src/explore/explore.ts:1574–1585` (the Auto button posts `{}`, renders raw text) | a structured reply handler that re-seeds knobs + re-renders the chip | A |

**Hotspots (from the tuning-seam survey):**
- **The in-session refresh is the whole point.** `session.aa_rows` is snapshotted once
  at load (session.py:173–174) — an out-of-process `abk validate` only lights the chip
  after an explore restart. `_run_validate` runs the engine server-side (own manager,
  `request_lock`-serialized, request_id stale-drop, an out-of-band `'validate'`
  `_ab_tasks` lock, `ensure_tables` before writing), then **mutates `session.aa_rows`
  in place** so subsequent `/recompute` D3 lookups go green without a restart.
- **Reduced-N by construction.** An HTTP handler can't run 10⁴ iterations
  synchronously; Auto uses a reduced-iteration `ValidateSettings` and a subsampled
  population (the Tier-S budget precedent), and the UX states that Auto's fast estimate
  may differ from the CLI's full-population run (aa-fpr §5 vs §6 — a legitimate,
  disclosed difference). Progress streams via `server.echo` like `/reload`.
- **Structured reply + client re-seed.** The reply carries the recommended `KnobState`
  per metric + the refreshed `CalibrationStatus`; a new `web/src/explore` handler
  re-seeds the rail and re-renders the chip verbatim (the chip renderer already exists,
  explore.ts:751–766). The bundle is rebuilt + committed (CI freshness). The Apply gate
  is **unchanged** (R19) — Auto only populates rows; it never weakens
  `confirm_uncalibrated`.

**Tests:** `tests/tuning/test_server.py` extension (`POST /validate` returns a
structured reply, writes rows, and flips a subsequent `/recompute` chip to `calibrated`
in the same session; request_id stale-drop honored; the `'validate'` lock is taken and
released); `web/test/explore.auto.test.ts` (the reply re-seeds knobs + re-renders the
chip).

**DoD:** the Auto button runs a reduced validate server-side, greens the live chip
without a restart, and re-seeds the knobs; the Apply gate semantics are unchanged.

**Must-fixes discharged:** *A/A calibration inside explore* — Auto mode (the M4 half of
the M3-stubbed seam).

---

### WP7 — the M4 e2e gate, worked example, adversarial review + docs sync (last)

**Goal:** the milestone exit gate — an end-to-end `abk validate` run over the seed
dataset proving the three named classic failures, the authored worked example in the
spec, the adversarial review, and the doc/rules/ROADMAP/CHANGELOG sync.

| Source | Target | Verdict |
|---|---|---|
| `tests/e2e/test_first_report.py:32–51` + `tests/cli/test_run_report.py:28–41` (the `SeedMirrorWarehouse` + `abk init` scaffold + frozen `now_utc` harness, `_baked_payload` extraction) | `tests/e2e/test_validate_matrix.py` | A |
| `tests/_helpers/synthetic_ab.py` `seed_null_events` (WP2) + a mis-specified ratio fixture | reused | ⟲ |
| — | `docs/specs/aa-false-positive-matrix.md §8` (the worked matrix) | **NEW** |

**Hotspots:**
- **Acceptance (R29).** The e2e demonstrably catches: (a) a well-calibrated z-test
  in-band; (b) an FPR-inflated naive t-test on a ratio metric out-of-band (the
  mis-specified fixture); (c) the peeking-FPR jump over the grid vs. the single-look
  FPR. FPR asserts use a Binomial(N,p) 3σ band around the analytic truth, never point
  equality (at N=1000, FPR=0.05 has σ≈0.007). The e2e also proves `_ab_aa_runs` rows
  land with the D3 key shape and `find_calibration` flips the chip from
  `uncalibrated`.
- **Worked example (R26).** `aa-fpr §8` is authored with a concrete matrix on the seed
  metric — the z-test/ratio-t-test/peeking-jump story — "the matrix's analyst-facing
  clarity IS the feature".
- **Docs sync (the house closing pattern).** In one closing `fix(m4)` commit: amend
  `aa-fpr §7` to the as-built column names (D15) and §3 for the peeking sub-rule
  composition (D3) + the sequential-column deferral (D8); annotate ROADMAP (composed-FDR
  → M5, D9; the sequential side-by-side deferral; D12 sidedness/winsorization stays a
  future stats-core change the harness arbitrates, D14); CHANGELOG entries; flip
  CLAUDE.md + `.claude/rules/architecture.md` status to "M4 shipped" and move
  `validate/` out of the "Not yet present" list; append §5 with every verified finding.

**Tests:** the e2e above; the CI matrix stays green (unit + the report/explore/validate
e2e gates); the bundle-freshness/marker/token/wheel gates pass with the rebuilt
`report.js` and `explore.js`.

**DoD:** the e2e gate green; the worked example authored; the adversarial-review record
appended; all docs/rules/ROADMAP/CHANGELOG in sync.

**Must-fixes discharged:** the milestone exit gate for all M4 must-fixes.

---

## 2. Dependency graph / parallelism

```
WP1 (engine core) ─┬─▶ WP3 (runner + persist) ─┬─▶ WP4 (CLI) ─┬─▶ WP7 (e2e + review)
WP2 (load + grid) ─┘                            │              │
                    (WP2 also feeds WP3)        ├─▶ WP5 (report + payload + budget) ─┘
                                                └─▶ WP6 (Auto mode) ─────────────────┘
```

- **WP1 and WP2 are parallel** (pure engine vs. data assembly) — both land before WP3.
- **WP3 unblocks WP4, WP5, WP6.** WP4 (CLI) and WP5 (report) are parallel after WP3;
  WP6 (Auto) needs WP3's runner and can proceed against a payload fixture in parallel
  with WP5. WP7 is last (needs the CLI + report + Auto to prove the exit gate).
- WP5's web change (report bundle) and WP6's web change (explore bundle) touch
  different bundles → no build conflict; each rebuilds + commits its own asset.

---

## 3. Decisions — the open points, settled here

Every reader-surfaced open decision is settled below; spec amendments ride the WP PR
that implements them (house rule, m2/m3-plan).

**D1 — Placebo data source: the experiment's own pooled cohort, label-permuted over the real grid.** The spec says "real pre-experiment / historical data for the unit population" (aa-fpr §1) but defines no loader. Resolution: load the experiment's own per-unit metric values across the actual cadence grid (reuse `RecomputeBackend.load_cutoff` + `metric_loader`, WP2), **pool** the per-variant arrays, and draw placebo splits by permuting the unit→arm labels. Permuting the pooled population destroys any true treatment effect and yields an exact null by construction (the standard permutation-A/A), while exercising the real grid, cadence, cohort, and metric SQL — no new exposure-free loader, no torn `_ab_exposures` write (shuffling is in-memory only). A dedicated pre-experiment historical window (rendering the assignment SQL over a prior window) is a recorded **follow-up**, not M4: the permutation already removes the effect, so there is no leakage to avoid. Amends aa-fpr §1 in the WP2 PR.

**D2 — Effect injection: multiplicative at the suffstats level; the coverage estimand is per test_type.** Inject `y→y·(1+δ)` into one arm by scaling `(mean, m2·(1+δ)², cross_c·(1+δ))` — exact, numpy-only, purity-safe, and `corr_coef`-invariant so CUPED needs no special case. `Fraction` scales `count` clamped to `≤nobs` (else "MDE unreachable"). The injected **truth** for CI coverage and winner's-curse is `δ` for relative test_type (δ *is* the estimand) and `δ·mean_control(cutoff)` for absolute test_type (it drifts over the grid) — the scorer parameterizes truth by the method's `test_type`. Injection lives in `abkit/validate/inject.py`, never in `abkit.stats` (purity).

**D3 — The peeking FPR is the naive optional-stopping hazard, not the stabilized rule; pre-horizon refusal OFF; spec amendment to aa-fpr §3.** "The actual readout decision rule" is self-contradictory for peeking: the as-built readout *refuses* pre-horizon WIN/LOSE (data-contract §1 / m3 D5(d)) and, past the horizon, requires the full trailing-window stabilization-persistence. Running placebos through the *literal* readout therefore gives a peeking FPR at or **below** the single-look rate (empirically ~3% vs ~5% at α=0.05) — because the stabilization-persistence requirement is the tool's *defense* against peeking, stricter than one look, the opposite of what the column must show. But the peeking FPR exists to quantify the *hazard*: the analyst who eyeballs the daily cumulative chart and stops the first time the CI clears zero, *despite* the refusal. So the pinned composition is: **optional stopping** — the peeking FPR is the share of placebos whose CI **excludes zero at *any* subsampled cutoff** (the readout's `_build_sig_map` significance primitive, readout.py:195–205 — `left_bound>0` or `right_bound<0`, either direction; ≡ `reject` for the normal family, so z-test/bootstrap edge cases follow the readout not the raw p-value); **pre-horizon refusal OFF** (the horizon look is included, so peeking ≥ single-look by construction — a monotone, honest jump); **demotion gaps honored** (an arm too small or NaN-bound cutoff is a gap, never a false zero, tallied separately); **SRM n/a** (placebo splits are balanced by construction). The **single-look FPR** (horizon cutoff only) is reported *beside* the peeking FPR so the jump is visible (R10); empirically single-look tracks α (~5%) while peeking climbs 15%→31% as the grid densifies from 5 to 40 looks — the spec's "nominal α 5%, real peeking FPR 14%" story, and sequential (M5) is what brings it back toward α. The stabilized-with-persistence rule remains the *official* readout verdict (unchanged, `pipeline/readout.py` untouched); it is deliberately **not** what the peeking column measures. Effect-exaggeration-at-stop = the |effect| at the first crossing (winner's curse against a true effect of zero). aa-fpr §3 amended in the WP2/WP3 PR to pin this composition and correct the "and stabilized" wording.

**D4 — `run_id` per cell: `{run_stamp}:{cell_hash}`; the shipped PK is kept.** The `(experiment, run_id)` PK under `ReplacingMergeTree(created_at)` collapses a matrix written under one shared `run_id`. Resolution: `cell_hash = compute_run_id(metric, method_config_id, mode, alpha)` (deterministic, no wall clock — donor `config_emitter` pattern), `run_stamp = compute_run_id(frozen now_utc_naive, selection inputs)` per invocation; `run_id = f"{run_stamp}:{cell_hash}"`. Distinct per (invocation, cell) → no collapse, full never-pruned audit history retained, and `find_calibration` still resolves the newest `created_at` among matching `(metric, method_config_id, alpha)` rows. No schema change (the PK stays); `aa-fpr §7`'s "one row per cell" is honored by the id, not a PK widening.

**D5 — Lock: a distinct `(experiment, 'pipeline', 'validate')` claim; `abk unlock` extended.** Validate writes only `_ab_aa_runs` (never read by the run pipeline), so it takes its own `process_type='validate'` rather than sharing the run lock (the donor shared because autotune mutated detections the pipeline owns — not our case). Failures are recorded on the lock row before propagating; the ownership-checked `release_lock` False return is logged, never raised. `abk unlock` is extended in WP4 to clear both `run` and `validate` process types (a crashed validate must be unlockable, not invisible until timeout). Concurrency with `abk run` is permitted; a torn `_ab_exposures` read during a concurrent run at worst shifts the placebo unit population slightly for that validate, never corrupts pipeline state.

**D6 — Method grid: score the *declared* comparisons by default; `--method` opts into more.** Without `--method`, the matrix scores the experiment's configured methods (the comparisons that actually exist), each at its effective per-comparison alpha. `--method m` adds specific registered methods (filtered `input_kind==metric.type`, `not is_paired`, quarantined skipped). The full test_type×CUPED×stratify×correction cartesian is opt-in via explicit `--method` flags — the MVP does **not** auto-fan the cartesian (cost bound + the "score the declared matrix" reading of §2). Recorded so a future `--grid` expansion is additive.

**D7 — Bootstrap A/A: opt-in, single-look only, `peeking_fpr` disclosed null.** Bootstrap methods hard-raise on `from_suffstats` (bootstrap.py:93–97) and cost `iterations × grid × n_samples` resamples for the peeking pass — unaffordable. `--bootstrap` gates opt-in bootstrap A/A with reduced `n_samples` + a subsampled unit population (retaining per-unit `Sample`s), and computes **single-look** FPR/power only (the horizon cutoff), writing `peeking_fpr=NULL` with a `details` note that the peeking pass is closed-form-only. The default closed-form matrix always runs the full grid. Satisfies the cost must-fix; disclosed in the matrix.

**D8 — Sequential side-by-side column: deferred to M5 with a spec amendment.** aa-fpr §3 requires showing the same metric with `sequential.enabled` beside fixed-horizon, but sequential (mSPRT/alpha-spending) lands only in M5 — `stats/sequential/` does not exist and all rows carry `ci_kind='fixed'`. M4 renders the fixed-horizon peeking FPR + the single-look FPR (the honest jump, R10) and leaves the "with sequential" comparison as a documented placeholder ("available when sequential lands — M5"). aa-fpr §3 amended (WP7); ROADMAP M5 gains the side-by-side column.

**D9 — Composed multiple-testing validation: the per-metric peeking FPR ships in M4; the full composed FWER/FDR sweep is M5.** M4 measures and reports each cell's peeking FPR over the grid and writes rows at the correct two-tier Bonferroni effective alphas (so the composition's alpha half is exercised), but the empirical composed Bonferroni×BH×peeking FWER/FDR over the *multi-metric* family is deferred to M5 (ROADMAP's "stays here/M4" is resolved to M5 to bound M4; BH read-time already shipped in M3). Recorded in ROADMAP + aa-fpr §3.

**D10 — Report delivery: reuse the existing report bundle; no third JS bundle.** The report payload already reserves the `calibration` block (`matrix_rows`, `headline`, `report_link`, builder.py:448, payload.ts:129–139, report.ts:207–221). `abk validate --report` bakes an HTML page (`abkit/validate/html.py`, the hardened bake) that embeds the committed `report.js`, and `report.ts` gains a calibration-matrix section gated on `matrix_rows`. This avoids a fourth package-data entry, a new wheel-assert line, a new `build.mjs` BUNDLES entry, and a new hex/token shell — the peeking curve reuses `chart.ts` canvas primitives and the band colors reuse the `--abk-st-*` status tokens (no new hex). Only the report bundle is rebuilt + committed. A dedicated `web/src/validate` bundle is recorded as a future option if the matrix outgrows a report section.

**D11 — Auto mode: a reduced server-side validate that refreshes `session.aa_rows` in place.** The 501 `/validate` becomes `_run_validate` (own manager, `request_lock`-serialized, request_id stale-drop, `'validate'` `_ab_tasks` lock, `ensure_tables`), running the WP3 engine with a reduced-N/subsampled `ValidateSettings`, then mutating the one-time `session.aa_rows` snapshot in place so the live chip greens without a restart, and returning a structured reply (recommended `KnobState` + refreshed `CalibrationStatus`) that a new client handler re-seeds from. Auto's fast estimate may legitimately differ from the CLI's full-population run — disclosed in the UX. The Apply gate is untouched (R19). WP6; de-scopable to "CLI writes rows; restart explore to see the chip" only if WP6 slips (the chip already reads correct rows either way).

**D12 — `MetricConfig.aa_fpr_budget` is added, completing the resolver chain.** `resolve_fpr_budget` (recompute.py:235–242) already reserves the metric-override arm ("does not exist yet (M4+)"). WP5 adds `MetricConfig.aa_fpr_budget: float|None` validated as a fraction in (0,1] (the project-level precedent), threaded into the resolver, and added to the §8 L1/L2 validation matrix. Resolution stays metric → project → `α×1.5`.

**D13 — Validate runs are byte-reproducible; the placebo-seed convention is pinned.** Placebo split seeds are `derive_seed("aa", experiment, metric, method_config_id, iteration)` (never RNG-global, never wall-clock); the part convention is frozen and known-answer-pinned in WP1 before any row is published (it can never change afterward — the H1/H2 byte-stability discipline). Deterministic FPR numbers are a golden-tested invariant, like M2's byte-stable rows — silently nondeterministic FPR would violate never-change-a-number in spirit.

**D14 — Sidedness + winsorization (ROADMAP D12): the harness ships; the params do not.** M4 builds the A/A harness that would *arbitrate* a sidedness or winsorization param, but implements neither — they exist nowhere in the stats core (p-values hardcoded two-sided; no winsor code), the rail is auto-derived from `param_specs`, and adding either is a stats-core change with the full change-control obligations (identity impact, statistics-changes entry, A/A validation *through this harness*). Explicitly **out** of M4 implementation scope; recorded in ROADMAP so it falls to a named future change, not to no milestone (the exact failure m3 D9 warned about).

**D15 — `_ab_aa_runs` schema: as-built wins; aa-fpr §7 is amended to match.** The shipped model (tables.py:224–263) is a superset-with-renames of aa-fpr §7 (`injected_effect`/`fpr`/`coverage` not `inject_effect`/`empirical_fpr`/`ci_coverage`, plus `method_name`/`method_params`/`mode`/`alpha`/`effect_exaggeration`/`details`/`status`/`error_message`) — and `find_calibration`, the chip, and the D3 gate are already built against it. M4 writes the as-built columns and amends aa-fpr §7 to the shipped names in the WP7 PR (the docstring already reserves "the M4 work package may extend the payload before the first release"). No schema migration.

**D16 — `--scoring` selects the selection objective, not which passes run; one row per cell with `fpr` always populated.** All passes (FPR, peeking, power, coverage, exaggeration) always run for the closed-form matrix so `fpr` is non-null and the chip can light; `--scoring fpr|power|mde` sets only the **Recommended-row selection objective** and is recorded in the `mode` column. A power-only interpretation that left `fpr=NULL` would silently fail `find_calibration` (which requires `fpr` non-null) — avoided by always computing FPR.

**D17 — Held-out generalization: fresh per-iteration permutations are the out-of-sample property; a formal unit train/test split is deferred.** Each placebo iteration draws a fresh permutation, so FPR and injected-effect power/coverage are measured on independent draws with no fitting — inherently out-of-sample for calibration, which is what statistics-changes §0.4 ("CUPED that tightens in-sample but doesn't generalize — no held-out power gain") needs to detect: a method that only tightens the in-sample band shows no power gain on the fresh draws. A formal unit-level train/test split (fit CUPED θ on a training fold, evaluate on a held-out fold) is a recorded follow-up; the columns do not yet mark in-sample vs. held-out.

**Owner decisions (ratified 2026-07-05):**
1. D5 lock scope: **distinct `process_type='validate'`**, `abk unlock` extended — validate may run concurrent with `abk run`.
2. D7 bootstrap A/A: **single-look only** (no grid peeking pass), disclosed `peeking_fpr=NULL`.
3. D8/D9: **sequential side-by-side and full composed-FDR deferred to M5**; M4 ships the fixed-horizon peeking FPR + single-look jump.

---

## 4. M4 definition-of-done → WP map

| DoD item (ROADMAP M4 + quorum + aa-fpr) | Proven by | WP |
|---|---|---|
| **R1** Out-of-band `abk validate`, autotune scaffolding ported (load→resolve→resample→score→persist→emit + lock/finalize) | the `_validate_one` command skeleton over `FakeDatabaseManager`; the pure runner | **WP3, WP4** |
| **R2** A/A empirical FPR over N placebo splits; FPR ≈ α | `test_resample.py` Binomial-band FPR; the e2e in-band z-test | **WP1, WP7** |
| **R3** Power / achieved-MDE / CI-coverage under injected effect | `test_scoring.py` known-answer power + coverage; `test_inject.py` | **WP1** |
| **R4** Matrix columns (FPR, power@MDE, achieved-MDE, coverage, peeking FPR, exaggeration) | the persisted row set; the report matrix | **WP3, WP5** |
| **R5/R14** Selection = FPR-closest-to-nominal max-power; Recommended row + rationale | `test_runner.py` selection; the report Recommended row | **WP3, WP5** |
| **R6/R7/R8/R30/R31/R32** Honest cumulative-peeking FPR over the one-enumeration grid, prefix-summed suffstats, denser-early ≤100 cap with disclosure, as-built readout sub-rules | `test_load.py` subsampler; `test_scoring.py` peeking pass; D3 sub-rule composition; e2e peeking jump | **WP2, WP1, WP7** |
| **R9** Effect-exaggeration-at-stop (winner's curse) first-class | `test_scoring.py` exaggeration; the `effect_exaggeration` column + report | **WP1, WP5** |
| **R10** Headline "nominal α X, real peeking FPR Y" in matrix + report + chip | the report headline; the chip's `peeking_fpr` | **WP5, WP6** |
| **R11** Sequential side-by-side | *deferred to M5 (D8)* — placeholder rendered + ROADMAP/aa-fpr §3 amended | **WP7 (defer)** |
| **R12** Composed FDR/FWER empirical validation | *per-metric peeking + correct two-tier alphas in M4; full multi-metric composed sweep deferred to M5 (D9)* | **WP3 (partial), WP7 (defer)** |
| **R13** FPR cells colored vs `aa_fpr_budget` band | the report band colors through the token layer; the budget resolver | **WP5** |
| **R15** Plain-language per-method verdicts | `test_runner.py` verdict strings; the `verdict` column + report | **WP3, WP5** |
| **R16/R36** `abk validate --report`; payload `calibration` block filled (no v-bump) | `test_report.py` baked-payload asserts; builder fill | **WP5** |
| **R17/R19** Rows satisfy `find_calibration`; Apply gate unchanged | `test_recompute.py` chip flip; `test_server.py` gate unchanged | **WP3, WP6** |
| **R18** Auto mode: server-side `/validate` re-seeds knobs, greens live chip | `test_server.py` in-session flip; `explore.auto.test.ts` re-seed | **WP6** |
| **R20/R21/R22** Cost bound: closed-form default, bootstrap opt-in reduced-N + subsampled, parallel over reentrant rng, documented runtime/memory | closed-form-only WP1 path; `--bootstrap` opt-in; the WP2 cost docstring | **WP1, WP2, WP4** |
| **R23/R24/R37** Persist as-built `_ab_aa_runs`; never pruned by clean; failed rows kept | `test_persistence.py`; the `_maintenance.py` exclusion (unchanged); a `status='failed'` row test | **WP3** |
| **R25** Feeds statistics-changes §0 blind-rederivation arbitration | the persisted FPR/power/coverage columns; the §0 process pointer (docs) | **WP3, WP7** |
| **R26** Worked example authored into aa-fpr §8 | the authored matrix | **WP7** |
| **R27/R28/R41** CLI surface + distinct `--method`; effective alpha echoed; non-zero exit | `test_validate_command.py` | **WP4** |
| **R33/R34** Change-control respected; D12 sidedness/winsor arbitrated-not-implemented | no `ALGORITHM_VERSION` bump (goldens untouched); ROADMAP note (D14) | **WP7** |
| **R35** ROADMAP M4 DoD (closed-form default, bootstrap opt-in, worked example, powers the chip + arbitration) | the exit gate | **WP7** |
| **R38** Own out-of-band lock; unlockable | `test_validate_command.py` lock; `test_unlock.py` extension | **WP4** |
| **R39/D12** `aa_fpr_budget` metric override; §8 validation | `tests/config/` L1/L2 | **WP5** |
| **R40** M4 plan mirrors the house format | this document | **—** |

**Exit gate:** CI green (unit + the report/explore/**validate** e2e gates + the
bundle-freshness/marker/token/wheel gates with the rebuilt `report.js` and
`explore.js`); the validate e2e proves the three classic failures in Binomial bands;
zero method-math changes (goldens untouched — no `ALGORITHM_VERSION` bump); specs
amended where D-items say so (aa-fpr §1 placebo source, §3 peeking sub-rule + sequential
deferral, §7 as-built columns, §8 worked example; declarative-config §8 the metric
`aa_fpr_budget` row; cli-and-dx §1 the validate stage-vs-`--steps validate` copy
disambiguation); ROADMAP annotated (composed-FDR → M5, the sequential side-by-side
deferral, D14 sidedness/winsorization); CHANGELOG entries for the command + the payload
calibration fill + the metric budget field; CLAUDE.md + `.claude/rules/architecture.md`
flipped to "M4 shipped" with `validate/` moved out of "Not yet present"; and the
adversarial-review record appended as section 5 with all verified findings applied in
one `fix(m4)` commit.

---

## 5. Adversarial review record (M4 exit gate)

_Appended at M4 close (WP7), the M1/M2/M3 pattern._
