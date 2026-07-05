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

## M5 — Sequential analysis + planner + corrections
- `sequential/` (mSPRT always-valid + alpha-spending), opt-in; `ci_kind`/`is_horizon`
  in the contract. `abk plan` (pre-launch power/sizing). ~~Benjamini-Hochberg
  read-time~~ *(pulled forward to M3 WP1: `pipeline/readout.py` rescoring —
  an M2-accepted `correction: benjamini_hochberg` would otherwise verdict at
  the wrong alpha)*.
- **The A/A matrix's `sequential.enabled` side-by-side column (from M4/D8):** once
  the sequential engine exists, `abk validate` renders the same metric's always-valid
  peeking FPR beside the fixed-horizon one, so the analyst sees the CI brought back to
  ≈ α — the honest completion of the peeking story.
- **The full composed multiple-testing FDR/FWER empirical validation (from M4/D9):**
  the composed Bonferroni × read-time BH × peeking sweep over the *multi-metric* family
  (M4 validated only the per-cell peeking FPR at the correct two-tier alphas).
- Sub-day cadence constraints (cumulative-intervals.md §6): `always_valid` is the
  auto-recommended scheme below `1d`; `alpha_spending` requires a pre-committed
  small look grid and is a config error at sub-day cadence; anytime-valid
  sequential multinomial SRM (Lindon & Malek) replaces per-cutoff χ² below `1d`.

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

### Tooling debt (non-blocking, discovered M3 WP2)
- **`mypy` fails on clean HEAD** — `numpy` 2.5.0 ships PEP-695 `type X = ...`
  stubs (Python 3.12+ syntax), but `[tool.mypy] python_version = "3.10"` makes
  mypy reject them (`numpy/__init__.pyi: Type statement is only supported in
  Python 3.12 and greater`; the error mis-anchors to `metric_config.py:48`).
  Fails on **both** the pinned pre-commit mypy (v1.10.0) **and** a newer venv
  mypy (2.1.0) — a `mirrors-mypy` bump does **not** fix it. CI tolerates it
  (`mypy abkit` is `continue-on-error: true`), so this is local-dev friction
  (the pre-commit `mypy` hook is red) not a CI blocker. Fix: raise mypy
  `python_version` to `3.12`, or pin `numpy<2.5`, or exclude the numpy stubs.
- **`black` version drift pre-commit ↔ CI** — pre-commit pins `black` 24.4.2
  while the `[dev]` extra is `black>=23.0` (unpinned), so CI installs the latest
  (26.x). They format some constructs differently (e.g. multi-line `write_text`
  in `tests/config/`); `abkit/` currently agrees under both and CI only runs
  `black --check abkit`, so CI is green today. Fix: pin `black` to one version
  across `.pre-commit-config.yaml` and the `[dev]` extra so local and CI never
  diverge.
