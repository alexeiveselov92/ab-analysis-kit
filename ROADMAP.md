# Roadmap (technical plan)

Milestones from greenfield to a shippable v1, then v2. Ordered so the **priority
explore cockpit** and a **runnable first-run** arrive early. Each milestone's
definition-of-done includes the relevant
[quorum must-fixes](docs/specs/quorum-review.md).

## M0 — Project scaffolding & contracts ✅ (this session)
- References analyzed; architecture synthesized & quorum-validated.
- Specs written ([docs/specs/](docs/specs/)); statistics baseline + legacy catalogue
  captured; founding decisions locked.
- **Repo infra laid (detectkit-style):** packaging (`pyproject.toml` → `pip install
  ab-analysis-kit`, `abk` entry point; `setup.py`, `MANIFEST.in`, `requirements.txt`),
  `.gitignore`, `.pre-commit-config.yaml`, GitHub workflows (CI, publish-to-PyPI on
  tags, website), `CHANGELOG.md` (Keep a Changelog), a minimal importable `abkit`
  package + `abk --version`, and smoke tests (CI green from day one). Pushed to
  `github.com/alexeiveselov92/ab-analysis-kit` (`main`).
- **Next:** flesh out the package layout from
  [architecture.md §4](docs/specs/architecture.md) starting with M1.

## M1 — Pure statistical core (`abkit.stats`) ✅
- `BaseMethod` ABC + registry + factory; `Sample`/`Fraction`/`SufficientStats`
  (mixed-ddof aware); `effects.py` (delta-method linearisation); `TestResult`.
- Parametric: `ttest`, `paired_ttest`, `ztest`, `cuped_ttest`, `paired_cuped_ttest`,
  `ratio_delta`. Bootstrap: vectorised engine (mean fast-path + Poisson matmul),
  `bootstrap`, `paired_bootstrap`, `poisson_bootstrap`, `post_normed_bootstrap`,
  percentile CI + `(#extreme+1)/(n+1)` p-value (opt-in `pvalue_kind`; the default
  stays the baseline sign p-value per statistics-changes §2). Power/MDE; Bonferroni.
- `rng.py` (`default_rng`, deterministic per-row seeds). Dual entry
  (`from_suffstats` ≡ `from_samples`).
- **DoD (met):** golden tests vs an independent legacy transcription at rel-1e-9
  (incl. θ; see statistics-changes §0 note on transcription provenance);
  known-answer tests; canonical `method_config_id` byte test; quarantine policy
  for broken ratio methods; 8-angle adversarial review applied (30 verified
  findings fixed or recorded). *(Must-fixes: ddof, tolerance, seed policy, hash,
  quarantine — all done.)*
- **Deferred M1 cleanups (tracked, non-blocking):** shared NormalTest→TestResult
  builder for the 5 parametric methods; `_finalize_from_boots` epilogue helper
  for the 4 bootstrap methods (also dedupes the double `stat_point`); route
  `ratio_delta._arm_linearisation` through `effects.relative_delta_effect`;
  `JointMoments.corr(i, j)` accessor replacing `paired_cuped_ttest._corr`;
  declarative introspectable quarantined-branch map (schema-visible, replacing
  imperative `_validate_params` raises); unify warn-vs-record warning channels;
  unify golden-bootstrap tolerance helper with `tests/golden/conftest.py`;
  z-test could route through `effects.normal_test` (kept as a verbatim legacy
  transcription deliberately).

## M2 — Declarative config + DB layer + the pipeline (recompute) ✅
- pydantic Experiment/Metric/Method configs + two-level validator; Jinja templating
  + the **packaged assignment macro**; project/profiles + env interpolation.
- Generic DB manager (CH/PG/MySQL) + internal tables (`_ab_experiments`,
  `_ab_exposures`, `_ab_results`, `_ab_tasks`); `core/period_planner` (expanding
  grid, anti-join, explicit completeness boundary).
- **Sub-day cadence first-class** (decision: cumulative-intervals.md §6):
  duration/schedule-typed `cadence` (dense-early grids), UTC `end_ts` window
  contract with derived `end_date`, `data_lag` watermark planner rule,
  `max_looks`/`warn_looks` gates, `ab_start_ts`/`ab_end_ts` Jinja built-ins,
  `insufficient_data` small-n row flag; CUPED covariate = fixed lookback
  (statistics-changes §5, implemented as the pre-period second render —
  declarative-config §3 amended).
- `pipeline`: discover → plan → load (cohort once) → SRM gate → compute → enrich →
  persist. `abk run`, `abk run --steps validate` (config-lint), `unlock`,
  `clean`, `init` (runnable example + seed dataset). Read-only exposures.
- **DoD (met):** `abk init && abk run --select example_signup_test` produces
  real results against the seed dataset (machine-independent e2e + a
  testcontainers ClickHouse gate); idempotent byte-stable re-run (incl.
  bootstrap via derived seeds); atomic lock (PG/MySQL single-statement, CH
  advisory); strictly-monotonic `created_at`; one-row-per-unit guard.
  *(Must-fixes: macro, alpha inspectability, completeness boundary, lock,
  SRM-in-CLI — all done.)*
- **Deferred (recorded):** the `_ab_unit_state` STATE stage is schema/invariant-
  complete (twice-run test) but not wired into the v1 driver — the v1 read path
  is recompute, so writing day-state would double the warehouse scan for data
  nothing reads; it activates when v2 flips the read path. Paired methods are
  notebook-only (the pipeline serves independent-arm experiments). Sequential
  CIs land in M5 (`ci_kind` is always `fixed` in M2 rows). The PG/MySQL
  testcontainers integration suite (incl. the two-process atomic-claim race
  test) is deferred to the M3 hardening list — CI runs the ClickHouse
  first-run e2e gate; the PG/MySQL claim SQL is unit-tested per dialect.
  Internal table-name overrides (`tables:` block) validate but are rejected
  until the mixins are parameterized.

## M3 — The explore cockpit (PRIORITY) + reporting ✅ SHIPPED
- ✅ `abk explore`: localhost server, live `from_suffstats` recompute (Tiers
  E/α/S/R over a bounded session cache), stabilization chart with tier-styled
  segments, Basic/Advanced knobs auto-derived from `param_specs`,
  `.history` write-back, orphan detection, the D3 calibration gate.
- ✅ Self-contained HTML readout (`abk run --report`); readout decision logic
  (WIN/LOSE/FLAT/INCONCLUSIVE; pre-horizon refusal; SRM gate); the `web/`
  toolchain with committed bundles + CI freshness/marker/token gates.
- ✅ **DoD held:** Apply gated when uncalibrated (`confirm_uncalibrated`
  against the empty `_ab_aa_runs`, e2e-tested); calibration chip wired (all
  D3 states; goes green via M4). *(Must-fixes discharged: calibration-in-
  explore, SRM surfacing, peeking rendering — see
  m3-implementation-plan.md §5.)*
- **Deferred from M3:** WP9 PG/MySQL testcontainers + the two-process lock
  race (needs Docker — run in CI or a Docker-equipped box; the plan-§4 DoD
  row and the exit-gate "integration matrix"/"both e2e variants" words move
  with it); the real-ClickHouse explore e2e leg exercising D11 over a live
  warehouse read order (D11 is unit-proven by the order-permutation test —
  milestone-review record); Segment mode (D9); the `--metric` narrowing
  beyond default-metric selection is as built.

## M4 — A/A false-positive matrix (`abk validate`) ✅ SHIPPED
- ✅ Ported the autotune scaffolding → the pure `abkit/validate/` engine (placebo
  label-permutation splits over the experiment's own pooled cohort, D1; FPR + power +
  achieved-MDE + coverage + effect-exaggeration; **honest cumulative-peeking FPR** —
  the naive optional-stopping hazard, D3 — over the one-enumeration day-grid, denser-
  early ≤100-point cap with disclosure), `_ab_aa_runs` persistence (per-cell `run_id`,
  D4; effective two-tier alphas), the recommendation + plain-language verdicts, and the
  matrix UX (budget-band colors, Recommended row + rationale).
- ✅ `abk validate` CLI (own out-of-band `validate` lock, D5, `abk unlock`-clearable;
  non-zero exit on failure), `--report` reusing the committed report bundle (D10, no
  third JS bundle), the `metric.aa_fpr_budget` override completing the resolver (D12),
  and **Auto mode** — server-side `POST /validate` that greens the live explore chip in
  place and re-seeds the knobs, Apply gate unchanged (R19).
- ✅ **DoD held:** closed-form default (bootstrap A/A left an opt-in follow-up, D7);
  worked example authored (`aa-false-positive-matrix.md §8`); powers the explore
  calibration chip and the blind-rederivation arbitration; the exit-gate e2e proves the
  three classic failures in Binomial bands (`tests/e2e/test_validate_matrix.py`); zero
  method-math changes (goldens untouched, no `ALGORITHM_VERSION` bump). *(Must-fixes
  discharged: matrix UX, peeking FPR, validate cost bound.)*
- **Deferred to M5:** the sequential side-by-side column (D8 — needs `stats/sequential/`,
  all M4 rows are `ci_kind='fixed'`) and the full empirical **composed** FDR/FWER sweep
  over the multi-metric family (D9 — M4 ships each cell's peeking FPR at the correct
  two-tier alphas; read-time BH already shipped in M3).
- **Arbitrated, not implemented (D14, ex-D12, change control):** one/two-sided tests
  and winsorization — neither exists as a stats-core method param (p-values are
  hardcoded two-sided; no winsor code anywhere), and the explore rail is auto-derived
  from `param_specs`, so neither can be faked in the UI. Adding either is a stats-core
  change with the full obligations (identity impact, `statistics-changes.md` entry, A/A
  validation *through this harness*) — a named future change, not a milestone gap.

## M5 — Sequential analysis + planner + corrections ✅ SHIPPED
The implementation record + decisions are in
[m5-implementation-plan.md](docs/specs/m5-implementation-plan.md); the math in
[statistics-changes.md §4](docs/specs/statistics-changes.md).
- **`stats/sequential/`** — an opt-in (`sequential: {enabled: true}`, **default off**,
  byte-identical fixed path) asymptotic Gaussian **confidence sequence** (Waudby-Smith &
  Ramdas), computed as a pure MODE transform over the fixed `(effect, SE)`, never a method
  plugin. Rows carry `ci_kind='always_valid'`; the readout calls WIN/LOSE pre-horizon only
  under it; the toggle self-invalidates (a bare `abk run` re-plans the series). ~~alpha-
  spending / group-sequential~~ → **M6** (a `scheme: alpha_spending` config error names it).
- **The A/A matrix's `sequential.enabled` side-by-side column (D8):** `abk validate`
  renders the always-valid peeking FPR + power + CI-width beside the fixed ones — the CI
  brought back to ≈ α, the honest completion of the peeking story.
- **The composed multiple-testing FDR/FWER empirical validation (D9):** the read-time
  composed rule (two-tier Bonferroni ∘ BH) is one shared helper (`stats.correction.
  composed_significance`); `abk validate` sweeps the empirical **FWER + FDR** over the
  multi-metric family (one shared union-cohort assignment per iteration). Fixed-horizon
  only; ~~sequential × composed~~ → **M6**.
- **`abk plan`** — the read-only pre-launch power/sizing planner (required-N / achievable-
  MDE / achieved-power at the effective two-tier alpha + look count & cost). ~~runtime /
  ASN~~ → **M6**.
- **Sub-day** (cumulative-intervals.md §6): the config lint recommends `always_valid` when
  the planned **look count** exceeds `warn_looks` (the dangerous variable is the look
  count, not the time unit — dense sub-day grids trip it first); `alpha_spending` is a
  config error at sub-day cadence; the anytime-valid sequential multinomial SRM (Lindon &
  Malek) replaces per-cutoff χ² below `1d`.
- Benjamini-Hochberg read-time was *pulled forward to M3 WP1* (`pipeline/readout.py`
  rescoring — an M2-accepted `correction: benjamini_hochberg` would otherwise verdict at
  the wrong alpha).
- **Deferred to M6** (named): `alpha_spending`/group-sequential, the A/A sequential ×
  composed sweep, `abk plan` runtime/ASN.

## M6 — DX, docs, orchestration, release
- `abk init-claude` + packaged `.claude` assets (rules + 7 skills);
  single-source docs site (`abkit.pipelab.dev`, Astro + sync-docs) — **detectkit-
  analogous machinery with our own palette, logo, and landing page** (design
  finalized in Claude design; interfaces stay on a themeable brand-token layer) per
  [branding-and-site.md](docs/specs/branding-and-site.md); Prefect flow/deployment
  scaffolding; BI reference queries/dashboards (Grafana, Lightdash, Metabase,
  Superset) + optional SRM panel; `test-report` channels.
- **DoD:** PyPI release `pip install ab-analysis-kit`; `CHANGELOG.md` authoritative;
  contributor `CLAUDE.md` + `.claude/rules` in sync.

## Post-baseline hardening (multi-arm UX + stats-core), tiered by version

From the 2026-07-07 audits ([docs/research/2026-07-multi-arm-and-stats-core/](docs/research/2026-07-multi-arm-and-stats-core/)).
Both baselines are **sound**: multi-arm (>2 groups) is correct end-to-end statistically
(all-pairwise compute, joint K-way SRM, `C(N,2)×metrics` Bonferroni, per-pair persistence);
the stats core is minimal-dep, vectorized, and scipy-delegated. What follows is **hardening**,
biased to *ship the MVP fast, improve in 1.x*. The baseline locks **numeric results** (golden
rel-1e-9), not the implementation or correctness-forever — byte-identical wins are free; number
changes are legitimate as a versioned deviation (`ALGORITHM_VERSION` + `statistics-changes.md` +
A/A revalidation).

- **Now / 0.1.0 (MVP, no numbers move):**
  - Fix `abk explore` Review mode showing only the **first** arm's verdict per metric
    (`.find` → map) — the one near-decision multi-arm bug (`web/src/explore/explore.ts:1516`).
  - **Document** the known multi-arm limitations honestly (control-vs-each readout, no
    experiment-level winner, `abk plan` first-pair sizing, `abk validate` two-arm placebo).
- **0.1.x safe wins (byte-identical, no version bump — opportunistic):**
  - Stats hot path: `ndtri/ndtr` swap (~60×) + lazy `statsmodels` import + lazy never-read
    `effect_distribution` (~250× on the `validate`/`explore` path); parametric `_finalize`
    helper + registry-parametrized contract/completeness tests + double-compute dedup.
  - Multi-arm: B-vs-C (non-control) **verdict card** + on-page asymmetry note; per-pair
    labels in `abk run --report` text; explore `activePair` memory.
- **1.x (versioned statistical improvements — ALGORITHM_VERSION + A/A; opt-in first):**
  - **Holm** (step-down) over Bonferroni (strict power gain, same FWER); z-test **unpooled**
    CI SE; restore the **relative-z covariance** term; **uniform ddof=1**; **Agresti-Caffo /
    Wilson** proportion CIs; **main-tier `metrics_count=1` FWER** fix. *(Student-t /
    Welch–Satterthwaite, BCa bootstrap, cross-fitted CUPED/CUPAC, cluster-robust SE are the
    same items already named under v2 below — promote per demand.)*
  - Multi-arm decision layer: **experiment-level winner rollup** on `ExperimentReadout` +
    treatment-vs-treatment verdicts + a cross-arm overview; an explicit **`control:`** field
    (or validate the positional convention).
- **v2 / bets:** incremental Chan-merge cumulative recompute (the real warehouse-cost lever;
  see below); drop-`statsmodels` scipy reimplementation (or `[power]` optional extra);
  bootstrap `PCG64→SFC64`.

## v2 (deferred, profiling-gated)
- Python incremental accumulator + array-cache + quantile sketches +
  `incremental_backend`; `abk verify-incremental` gate (whole-series reconciliation);
  `run --profile` observability to trigger it on a concrete cost threshold.
- Cross-fitted CUPED/CUPAC, Student-t (Welch–Satterthwaite), BCa bootstrap,
  Mann-Whitney, cluster-robust SE; full PG/MySQL incremental parity (if needed);
  optional owned randomization; app integration (agentic analysis + embedded Lightdash).

## Backlog / open items for the user
Tracked in the RU initiation spec ([docs/ru/project-initiation-spec.md](docs/ru/project-initiation-spec.md))
— covariate-window choice, v2 trigger threshold, docs domain confirmation, SRM
`expected_split` source, guardrail multiplicity handling.

### Tooling debt (non-blocking; ~~discovered M3 WP2~~ root-caused + partly fixed M6 WP1)
- **~~`mypy` fails on clean HEAD~~ — ROOT-CAUSED + FIXED (M6 WP1).** The real cause
  was **not** numpy: `abkit/config/metric_config.py:48` held a stray comment
  `# type: (required, optional)` that mypy parsed as a **PEP-484 type comment**;
  `(required, optional)` is invalid type syntax → `Invalid syntax [syntax]`, and mypy
  **bailed before checking anything else** (hence "errors prevented further checking"
  and the mis-anchor). The numpy 2.5 PEP-695 stub error was real but *secondary* — it
  only surfaced once the parser got past the comment, and `python_version = "3.12"`
  clears it (mypy 1.10.0 parses the stubs fine at 3.12 — verified). WP1 fixes: reword
  the comment; `python_version` → `3.12`; add `yaml.*` to `ignore_missing_imports`.
  **Now mypy RUNS TO COMPLETION and reports ~124 real strict-mode errors** (41 arg-type,
  38 operator, 28 no-untyped-def, …; ~half in `tuning/recompute.py` + `pipeline/readout.py`,
  mostly `X | None` Optional-handling that the runtime guards but mypy can't prove). CI
  keeps `mypy abkit` `continue-on-error: true` — clearing the 124 is **tracked debt**, held
  separate from WP1 because the fixes live in numeric hot paths (a careless narrowing could
  change a number — the cardinal sin). The pre-commit `mypy` hook stays red until then.
- **`black` version drift pre-commit ↔ CI — FIXED (M6 WP1).** `[dev]` now pins
  `black==24.4.2` and `mypy==1.10.0` to match `.pre-commit-config.yaml` exactly, so CI and
  local pre-commit can no longer diverge on formatting/type results. (`abkit/` verified clean
  under both black 24.4.2 and 26.x, so the pin caused zero reformat churn.) Minor residual:
  `ruff` has the same latent drift shape (pre-commit `v0.4.8` vs unpinned `[dev]`) — left as
  a small follow-up since it was not a reported debt and abkit is clean under both.
