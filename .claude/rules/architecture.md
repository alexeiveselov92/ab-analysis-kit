# abkit architecture — as built

> The contributor/assistant condensation of the system **as it exists in code**.
> Reflects: **M1–M8 shipped** (`__version__ = 0.3.0`, release-ready — the
> `v0.3.0` tag/publish is the maintainer's step; latest on PyPI is `0.2.0`;
> M3's WP9 testcontainers hardening deferred to a Docker-equipped
> environment).
> Design contracts for what is being *built next* (1.x hardening) live in
> [docs/specs/](../../docs/specs/) + [ROADMAP.md](../../ROADMAP.md); this file must
> never claim unbuilt code exists.
> Keep in sync with `docs/` and the packaged `init-claude` payload
> (`abkit/cli/assets/claude/`) on every release.

## The shape

**abkit is detectkit's twin with one organ transplanted:** the `detect` stage
becomes a statistical `compute` stage; the primary entity flips from *metric*
to *experiment*. Declarative YAML + SQL run through `load → compute → readout`.

```
experiment (YAML) ──▶ load ──▶ compute (t/z/CUPED/bootstrap) ──▶ readout
   └ references reusable metrics (YAML + SQL)
```

Donor codebase: `/home/aleksei/wsl_analytics/detektkit` (import package
`detectkit`) — components marked ⟲ in
[architecture.md §4](../../docs/specs/architecture.md) port near-verbatim
(`dtk`→`abk`, `detectkit`→`abkit`).

## Package layout — what exists today

```
abkit/
  __init__.py            # __version__ (single source; numpy-free import path)
  cli/                   # ✅ M2: main (lazy Click group), _output (tree style),
    commands/            #   init/run/unlock/clean (M2), explore (M3), validate (M4),
                         #   ✅ M5: plan (read-only pre-launch power/sizing)
  core/                  # ✅ M2: interval (N{s,m,h,d,w}), models (TableModel +
                         #   version_column LWW), period_planner (THE grid — one
                         #   enumeration for validator gates AND the anti-join)
  config/                # ✅ M2: project/profile/experiment/metric/method models,
                         #   validator L1+L2 (§8 matrix), discovery/selector
  database/              # ✅ M2: generic CH/PG/MySQL managers + try_acquire_lock
    internal_tables/     #   + the greenfield _ab_* schema & mixins (see below)
  loaders/               # ✅ M2: query_template (ab_* built-ins, StrictUndefined,
    templates/           #   incl. ab_cohort_source — M8 WP3), the packaged
                         #   abkit_assignment.jinja macro, metric_loader;
                         #   ✅ M8: exposure_source (build_cohort_backend — the ONE
                         #   copy-vs-direct switch every cohort reader uses, WP2/WP4)
                         #   + exposure_copy (the append-only incremental engine,
                         #   WP5); exposure_loader's full-reload path is dead from
                         #   the driver since WP5 (external callers only);
                         #   ✅ M9 WP3: state_loader (per-day moment extraction)
  compute/               # ✅ M2: recompute_backend (v1 full-window strategy;
                         #   ✅ M9 WP3: load_window — the STATE day render)
  pipeline/              # ✅ M2: driver (lock→load→SRM→plan→compute→persist),
                         #   analyze, enrich, _types; worker pool;
                         #   ✅ M9 WP3: state (the write-only STATE stage)
  reporting/             # ✅ M3: builder (the §5.3 terse payload + verdicts),
    assets/report.js     #   html_report (hardened bake), the committed bundle;
                         #   ✅ M4: calibration.py (the payload calibration block)
  tuning/                # ✅ M3: session (bounded Tier-S cache), recompute
    assets/explore.js    #   (Tiers E/α/S/R + D3 calibration), config_writer
                         #   (Apply seam + .history + orphans), server (WP6:
                         #   ✅ M4 POST /validate Auto mode), payload, html
  validate/              # ✅ M4: the pure A/A engine (panel/resample/inject/
                         #   scoring), load (placebo panel + denser-early grid
                         #   subsample), runner (cell enum + effective alpha +
                         #   select + verdicts), persistence/result/run_id
                         #   (per-cell _ab_aa_runs rows, D4), _types;
                         #   ✅ M5: family (D9 composed FWER/FDR union-cohort sweep);
                         #   ✅ M7: vector_resample (block-streamed GEMM engine) +
                         #   score_cell/sweep_family dispatchers w/ verbatim scalar
                         #   fallback; opt-in --family-sweep; per-cell auto-N
  planning/              # ✅ M5: sizing (pure required-N/MDE/power over stats.power) —
                         #   the `abk plan` engine; read-only, refuses ratio/bootstrap
  stats/                 # ✅ M1: the pure numpy core (details below);
                         #   ✅ M7: supports_vectorized + from_suffstats_array
                         #   (5-method roster) + effects._libm_pow batch kernels
    sequential/          # ✅ M5: the always-valid confidence sequence
                         #   (confidence_sequence, mixture τ², apply.to_always_valid;
                         #   ✅ M7: *_array siblings)
  utils/                 # stdlib-only: json_utils (canonical hash path),
                         #   datetime_utils (naive-UTC), env_interpolation
web/                     # ✅ M3: the dev-only TS toolchain (never wheel-shipped)
  src/shared/            #   chart.ts (canvas primitives + TOKEN_FALLBACKS —
                         #   THE brand-token layer), payload.ts (lockstep types)
  src/report/ src/explore/  # the two renderers → committed assets (build.mjs)
  test/                  #   jsdom smoke suites + type-checked fixtures
tests/
  stats/ golden/         # M1 (incl. test_purity.py; golden rel-1e-9)
  core/ config/ database/ loaders/ pipeline/ cli/ e2e/   # M2
  reporting/ tuning/     # M3 (+ cli/test_explore_command.py, the report/
                         #   explore e2e gates in tests/e2e/)
  validate/              # M4 (+ cli/test_validate_command.py, the validate-
                         #   matrix exit-gate e2e in tests/e2e/)
  stats/sequential/ planning/  # ✅ M5 (+ validate/test_family_sweep.py,
                         #   pipeline/test_correction_rule.py, cli/test_plan_command.py,
                         #   the sequential-matrix exit-gate e2e in tests/e2e/)
                         # ✅ M7: stats/test_vectorized_parity.py + test_normal_path_golden.py,
                         #   validate/test_vector_{resample,parity,perf}.py,
                         #   validate/test_family_vector_parity.py (exact-only)
  _helpers/fake_db.py    # in-memory manager with SQL-backend semantics
  _helpers/synthetic_ab.py  # SyntheticWarehouse (3 metric kinds, shuffle mode,
                         #   seed_null_events — the exact-null A/A fixture)
```

Not yet present (v2): `compute/incremental_backend`.
M3's WP9 (PG/MySQL testcontainers + the two-process lock race) is deferred to a
Docker-equipped environment.

### M2 pipeline facts an assistant must know

- **Anti-join, not a cursor:** a cutoff is pending iff `end_ts ≤ now_utc −
  data_lag` (watermark computed ONCE per run in Python) and not in
  `list_computed_cutoffs()` (a SET — holes re-plan).
- **Locks:** `_ab_tasks` at `(experiment, "pipeline", "run")`; PG/MySQL claims
  are single-statement atomic, ClickHouse is advisory (read-back tie-break);
  failures are recorded on the lock row before propagating.
- **SRM is blocking-but-non-dropping:** rows are always written with
  `srm_flag`/`decision_blocked`; the CLI prints the red gate line.
- **CUPED covariate = a second render** of the same metric SQL over the fixed
  pre-period window with `ab_apply_exposure_filter=false` (declarative-config
  §3 as amended); loaded once per run, absent units default to 0.
- **Bootstrap rows are byte-stable:** per-row `seed =
  derive_seed(exp, metric, name_1, name_2, end_ts, n_samples)`, identity-excluded.
- **`ci_kind` is always `"fixed"` in M2** (sequential lands M5); paired
  methods are notebook-only. *(The STATE stage, "deliberately not wired"
  through M8, is wired write-only since M9 WP3 — see the M9 facts below;
  the read path stays recompute until WP4.)*

### M3 reporting/explore facts an assistant must know

- **Two point vocabularies, never mixed:** the baked report series uses TERSE
  keys (`t/ed/e/lo/hi/p/rj/s1…/hz/blk/ins` — `web/src/shared/payload.ts`);
  `/recompute`+`/reload` replies use FULL names (`server._result_json`).
  Timestamps are ms-epoch ints everywhere; NaN/±inf → null.
- **Explore reads persisted rows (D2):** one lock-free session-load pass fills
  the bounded Tier-S cache (`EXPLORE_CACHE_BUDGET`); over budget ⇒ honest
  suffstats-only degradation, never a partial cache. Recompute tiers: E exact
  suffstats, α-inversion (approx), S from the cache, R = warehouse reload via
  `POST /reload` (its own manager, serialized).
- **The client mirrors `analyze.effective_alphas`** over
  `payload["explore"]["experiment"]` (raw alpha/correction/counts baked by
  `tuning/payload.py`) — keep `explore.ts#effectiveAlpha` and that block in
  lockstep (pinned by `tests/tuning/test_explore_bundle.py`).
- **The D3 calibration gate** keys by `(metric, method_config_id, EFFECTIVE
  alpha)`; on an empty `_ab_aa_runs` every Apply takes the `confirm_uncalibrated`
  path — server-enforced, client-mirrored. `abk validate` / Auto mode (M4)
  populate the rows that flip the chip to `calibrated`.
- **Committed bundles are build artifacts:** edit `web/src/**`, run
  `cd web && npm run build`, commit the changed `abkit/*/assets/*.js` in the
  same PR (CI diffs freshness, greps the §4 marker classes
  `abk-prehorizon`/`abk-insufficient`/`abk-srm-fail`, and asserts the wheel
  ships both bundles). All colors go through `TOKEN_FALLBACKS` — the CI hex
  loop rejects a page-shell hex missing from the token layer.
- **request_id stale-drop:** ids are a single global on the server; the client
  seeds from `Date.now()` (and re-seeds after a two-tab 409) — never restart
  the counter at 0/1.

### M4 validate facts an assistant must know

- **`abkit/validate/` is I/O-pure like the runner:** the engine (`panel/resample/
  inject/scoring`) touches only `abkit.stats`; the CLI
  (`cli/commands/validate.py`) resolves the cohort through
  `build_cohort_backend` (M8 — the persisted `_ab_exposures` in copy mode, the
  live assignment source in the no-copy default) and hands `load.py` the
  resulting backend; `load.py` **never writes** (a placebo split is in-memory
  only — in copy mode a persisted shuffle would clobber the real cohort; in
  the default there is no persisted cohort at all); the CLI takes the lock and
  persists.
- **Placebo source = the experiment's own pooled cohort, label-permuted (D1)** over
  the real one-enumeration grid (`generate_grid` — same as driver/explore). Permuting
  unit→arm labels destroys any true effect ⇒ an exact null. Seeds are
  `derive_seed("aa", experiment, metric, method_config_id, iteration)` — byte-repro,
  no wall-clock (D13); FPR numbers are a deterministic, golden-style invariant.
- **Peeking FPR is the optional-stopping hazard, NOT the readout rule (D3):** the
  share of placebos whose CI **excludes zero at any look** (readout `_build_sig_map`
  significance, pre-horizon refusal OFF, horizon included ⇒ peeking ≥ single-look).
  The stabilized-with-persistence readout rule is the *defense* and is deliberately
  **not** what this column measures; `pipeline/readout.py` is untouched. The
  single-look FPR (horizon only) is reported beside it.
- **One row per cell at the EFFECTIVE alpha (D4/D16):** `run_id =
  "{run_stamp}:{cell_hash}"` (no `ReplacingMergeTree` collapse); the persisted `alpha`
  is `comparison_alpha ∘ effective_alphas` (the SAME resolver the chip/Apply use) — a
  re-derivation would fail `find_calibration`'s `isclose` and read `alpha_mismatch`.
  `--scoring` sets only the Recommended-row objective (the `mode` column); FPR always
  computes so the chip can light. Two-tier: main vs secondary metrics land at
  different alphas.
- **The matrix report reuses the report bundle (D10) — no third JS bundle:** the
  payload `calibration` block (`reporting/calibration.py`, guarded by
  `aa_runs_table_exists()`) fills the reserved slot; `report.ts#buildCalibrationSection`
  renders it; band colors reuse the `--abk-st-*` status tokens (no new hex). Rebuild +
  commit `report.js` on any `web/src/report/**` edit (CI freshness gate — pathspec
  `:(glob)abkit/*/assets/**`).
- **Auto mode mutates `session.aa_rows` in place (D11):** `POST /validate`
  (`tuning/server.py`, own manager under an OUTER try/finally, `'validate'` lock,
  request_id stale-drop, reduced N) greens the live chip without an explore restart;
  the Apply gate is unchanged. Bootstrap A/A stayed an opt-in follow-up (D7);
  sidedness/winsorization are arbitrated-not-implemented (D14).

### M7 vectorization facts an assistant must know

- **`score_cell` and `sweep_family` are dispatchers** on
  `method.supports_vectorized`: the vectorized bodies block-stream
  `vector_resample.iter_blocks × build_arm_batch × from_suffstats_array`; the
  scalar bodies are verbatim code moves — a method without a batch kernel
  (all bootstrap, any new plugin) automatically takes the scalar path.
  A lying flag (`True` without a kernel) raises `ValidateError`, caught
  per cell.
- **Batch-kernel pow terms route through `effects._libm_pow`** — numpy `**`
  is 1 ULP off C-library `pow` and the cancelling delta-method variance sum
  amplifies that to ~1e-4 rel at CI bounds; with libm routing the
  scalar↔batch parity is **bit-exact by construction** (parity tests demand
  exact for all 5 opted-in methods; roster-pinned: t-test, z-test,
  cuped-t-test, paired-t-test, ratio-delta).
- **Float aggregates are byte-reproducible only under FIXED blocking + a
  fixed BLAS configuration (D13 as restated in M7)** — block-size and
  thread-count bit-invariance is unachievable in principle (GEMM and even
  `np.sum(axis=1)` round per buffer height). Masks/counts/flags are exact
  under ANY blocking; continuous columns get rtol-1e-12 across blockings.
  Never write a byte-equality assertion on continuous columns across block
  sizes or BLAS thread counts.
- **The parity gates are the milestone's safety net** —
  `tests/validate/test_vector_parity.py` (8 shapes × 50 seeds, env
  `ABKIT_PARITY_SEEDS` raises it; exact counts/curves/warnings, continuous
  rel-1e-9) and `test_family_vector_parity.py` (**exact-only** — every family
  column is a count fraction/exact sum/passthrough); `test_vector_perf.py` is
  the executable perf gate (<10 s reference under coverage). At an *exactly
  solved* CI boundary (|bound| ≲ 1e-15) the engines may legitimately flip one
  decision — pinned, not a bug.
- **Iteration policy (WP6):** `ValidateSettings.iterations=None` → per-cell
  `max(2000, ⌈200/α⌉)` at the cell's EFFECTIVE alpha (family sweep sizes at
  the tightest member alpha); auto-N warns above 100 000, never hard-caps;
  persisted rows record the RESOLVED N. `--family-sweep` is opt-in
  (default off; with `--metric` it is logged-and-skipped; explore Auto mode
  never opts in — the D3 chip keys on per-cell rows only).
- **`decision_log` entries do NOT reach the CLI user** — their only other
  consumer is the Auto-mode JSON reply; any user-facing warning must be
  explicitly echoed as a CLI line (the WP6 round-2 lesson, pinned by
  `test_auto_n_warning_reaches_the_terminal`).

### M8 cohort facts an assistant must know

- **`_ab_exposures` is OPTIONAL — the no-copy default writes nothing.** With
  `assignment.cohort_copy.enabled: false` (the default) no run ever creates
  the table: metric SQL joins a live `MIN(exposure_ts)`-deduped subquery over
  the rendered assignment SQL via the `ab_cohort_source` builtin, re-rendered
  + re-validated on every invocation (the documented cost/freshness tradeoff —
  a late-arriving row is never missed; a render + validation query is paid
  each time).
- **`build_cohort_backend(manager, experiment, project_root, grid,
  with_snapshot=...)`** (`loaders/exposure_source.py`) is **the ONE
  copy-vs-direct switch** every cohort reader goes through — driver, `abk
  plan` arrival rate, `abk validate` load, explore session-load, reporting SRM
  counts. The binding M8→M9 contract (§0.5(e)): no caller, present or future,
  hand-rolls cohort SQL. Read-only callers in copy mode stay query-free
  (`with_snapshot=False` ⇒ snapshot `None`); direct mode renders + validates
  once (cross-variant corruption fails loudly at every surface).
- **The incremental copy engine (`loaders/exposure_copy.py`, copy mode) is
  append-only**: grid-anchored closed-interval buckets
  (`grid.start_ts + k·batch_interval`; the open bucket + rows younger than
  `maturity_delay` are withheld), watermark resume from the FINAL-deduped
  `MAX(exposure_ts)` snapped to its bucket floor, round trips of
  `batch_intervals_per_round_trip` intervals with bounds injected through the
  EXISTING `{{ ab_added_filters }}` hook (required in copy mode — config-lint
  and the engine prove the reference is LIVE via a rendered sentinel; a token
  in a comment cannot pass). A custom `update_column` has no persisted cursor
  and re-scans from the experiment start every run. A routine run never
  deletes; `abk run --resync-cohort` (copy mode only, no-op in direct) deletes
  + rebuilds through the SAME engine — the recovery for the documented
  limitation: a row backfilled into an already-scanned closed bucket is
  silently missed by the watermark.
- **SRM always measures the LIVE validated source** (both modes); in copy mode
  the persisted metrics join trails it by the open bucket + `maturity_delay`,
  and `abk run` warns when a computable cutoff exceeds the copy's coverage
  (align `data_lag >= maturity_delay + batch_interval`).
- **The cross-mode parity gates** (`tests/e2e/test_cohort_mode_parity.py`,
  `tests/pipeline/test_pipeline.py::TestCohortModeParity`,
  `tests/e2e/test_first_run_copy_enabled.py`) pin `_ab_results`/`_ab_aa_runs`/
  the baked explore payload identical across modes (`watermark_ts` is the one
  legitimately differing column) — zero statistical numbers moved in M8.

### M9 facts an assistant must know (milestone in flight)

- **WP1 (shipped):** `_ab_results` carries the 4 persisted CUPED covariate
  moments (`cov_std_1/2`, `corr_coef_1/2`, nullable) + the
  `ensure_columns()` additive ALTER-ADD-COLUMN migration primitive (the
  project's first post-release schema change; idempotent, never drops).
- **WP2 (shipped):** `cuped-t-test` is Tier E in explore — covariate
  suffstats reconstruct from the persisted moments for every knob except
  `covariate_lookback` (unconditionally Tier R); pre-migration rows keep the
  old fallbacks.
- **WP3 (shipped): the STATE stage is wired, write-only.**
  `PipelineStep.STATE` sits between LOAD and COMPUTE (`--steps state`
  supported; the `abk run` default is `validate,plan,load,state,compute`).
  `pipeline/state.py` renders each STATE-eligible metric per closed local
  day THROUGH the m8 factory backend (`RecomputeBackend.load_window` — never
  a hand-rolled cohort join, both modes parity-tested) and replaces the
  moments via `replace_day_state`. Eligibility: closed-form (unseeded)
  comparison, non-stratified metric, no explicit `columns.covariate` role
  (a snapshot covariate is not day-additive), SQL body free of `ab_cov_*`.
  Identity: `source_table = "{experiment}/{metric}"`
  (`compute_state_source_id` — the §5.3 sharing ideal deliberately
  narrowed: the render is cohort-filtered, so cross-experiment sharing
  would clobber) + `column_set_id = compute_metric_state_id(role_map,
  whitespace-normalized SQL, cohort_config)` where `cohort_config` folds in
  the cohort-shaping experiment config (assignment-SQL hash, added_filters,
  unit_key, variants, timezone, start_date; end_date only when the
  assignment SQL references `ab_end_*`) — compose the key ONLY through
  `pipeline/state.state_series_key()`. Any such edit orphans the series and
  the next run sweeps the stale ids. **Every failure path TRUNCATES the
  tail** (`delete_state_days_from`), preserving contiguity — every day
  `<= get_last_state_day()` is materialized, days past it are absent, not
  stale: `--full-refresh --from/--to` deletes from the first touched day
  BEFORE re-rendering through the end of the series (crash mid-refresh ⇒ a
  self-healing prefix); a non-finite moment truncates from the failing day
  (earlier days retained, one-render retry per run, a loud CLI warning).
  Copy mode clamps day-close to the copy's coverage; `--resync-cohort`
  force-rebuilds day state with the copy. Nothing reads the rows until
  WP4's `IncrementalBackend`.

## The stats core (`abkit.stats`) — the implemented system

**Purity invariant (hard):** numpy/scipy/statsmodels + stdlib only; never
config/DB/Jinja/click. Sole intra-package import: `abkit.utils.json_utils`.
Enforced by `tests/stats/test_purity.py`.

### Data model (`samples.py`)

- `Sample` (per-unit values, optional `covariate`, `strata`), `Fraction`
  (count/nobs), `RatioSample` (numerator/denominator pairs).
- `SufficientStats`, `RatioSufficientStats`, `PairedSufficientStats`,
  `JointMoments` — closed-form entry; **mixed-ddof convention preserved from
  legacy**: `np.var`-shaped terms use ddof=0, `np.cov`-shaped terms ddof=1.
  Merges are Welford/Chan-stable (`accumulate.py`).
- `align_paired` aligns paired samples by unit.

### Methods — a plugin registry (12 registered)

| Family | Registry names |
|---|---|
| Parametric (`from_suffstats` + `from_samples`) | `t-test`, `paired-t-test`, `z-test`, `cuped-t-test`, `paired-cuped-t-test`, `ratio-delta` |
| Bootstrap (vectorised block-streaming engine) | `bootstrap`, `paired-bootstrap`, `poisson-bootstrap`, `paired-poisson-bootstrap`, `post-normed-bootstrap`, `paired-post-normed-bootstrap` |

- One method = one `BaseMethod` subclass + `@register` (+aliases). The
  pipeline/DB/CLI never special-case a method name.
- `create_method(name, alpha=0.05, params={...})` — `alpha` is the effective
  **post-correction** per-comparison alpha; it is experiment-level and never
  enters `method_config_id`.
- Param schemas are declarative `ParamSpec`s (`base.py`): typed, defaulted,
  identity-flagged; validated at construction (`MethodParamError`).
- **Quarantined legacy-broken branches** raise `QuarantinedMethodError`
  (never silently substituted): PoissonPostNormed, PairedPostNormed relative,
  PostNormed absolute — see [statistics-changes.md §3](../../docs/specs/statistics-changes.md).
- Entry points: `compare(groups)` → all pairwise, `compare_pair(g1, g2)`,
  and the dual entry `from_samples(s1, s2)` ≡ `from_suffstats(st1, st2)`.

### Identity (`method_config_id`)

`sha256(method_name + json_dumps_sorted(non-default identity params) +
ALGORITHM_VERSION appended only when > 1)` — byte-exact-tested. `seed` is
identity-**excluded** for all bootstrap methods; re-runs stay byte-stable via
deterministic per-row seeds (`rng.derive_seed` from row identity). Editing an
identity param orphans the prior results series.

### Results & supporting modules

- `TestResult` (`result.py`): `method_name`, `method_params`, `alpha`,
  `pvalue`, `effect`, `ci_length`, `left_bound`, `right_bound`, `reject`,
  plus per-arm stats, optional `effect_distribution`, `warnings`,
  `diagnostics`, `to_dict()`.
- `srm.py`: `srm_check(observed_counts, expected_split, alpha=0.001)` →
  `SrmResult` (chi-square gate).
- `correction.py`: `adjust_alpha`, `two_tier_alphas` (the legacy two-tier
  Bonferroni keyed off `is_main_metric`), read-time `benjamini_hochberg`,
  `n_comparisons`.
- `power.py`: power/MDE (t-test, CUPED-deflated, proportions).
- Default p-value stays the **baseline sign p-value**; `(#extreme+1)/(n+1)`
  is opt-in `pvalue_kind: plugin` (statistics-changes §2).

### Gotchas that will bite you

- Never "fix" the mixed ddof, the sign p-value, or θ's `np.cov` ddof=1 — they
  are the captured baseline, golden-tested at rel-1e-9.
- **Never change a number silently**: deviation ⇒ `ALGORITHM_VERSION` bump +
  [statistics-changes.md](../../docs/specs/statistics-changes.md) entry +
  CHANGELOG + A/A validation.
- Stratification uses Hamilton apportionment; Poisson bootstrap is mean-only
  (guarded); zero denominators → NaN + warning (H5), never an exception.

## M5 + M6 + M7 as built (specs are canonical)

**M5 shipped** (the implementation record is
[m5-implementation-plan.md](../../docs/specs/m5-implementation-plan.md)): the always-valid
sequential engine (`stats/sequential/`, opt-in `ci_kind='always_valid'`), the readout under
sequential + weekly-cycle chip, the sub-day anytime-valid multinomial SRM (Lindon & Malek),
`abk plan` (`planning/`), and the two A/A columns deferred from M4 — the `sequential.enabled`
side-by-side peeking FPR (D8) and the composed FWER/FDR sweep over the multi-metric family
(D9, via the shared `stats.correction.composed_significance`).

**M6 shipped** (the record is
[m6-implementation-plan.md](../../docs/specs/m6-implementation-plan.md)): the DX / docs /
orchestration / release layer — `abk init-claude` + the packaged `.claude` assets
(`abkit/cli/assets/claude/`: the managed `CLAUDE.md` block, 9 operator rules, 7 skills), the
single-source docs site (`website/` Astro, live at abkit.pipelab.dev), Prefect scaffolding in
`abk init` (`runners/`), BI reference (tool-agnostic SQL + one Grafana dashboard), `abk
test-report` + the `abkit/notify/` channel layer, `abk plan` **runtime/ASN** (WP-A, from
the cohort's arrival rate + always-valid ASN; M8 later made the cohort source
conditional — see "M8 cohort facts"), the A/A **sequential × composed** family sweep
(WP-B, `validate/family.py`), and the release engineering (`__version__ = 0.1.0`, classifier
`3 - Alpha`, the wheel-namelist + `pip install` DoD gates, `tests/docs/test_docs_single_source.py`)
behind the WP10 exit gate (`tests/e2e/test_release_readiness.py` + ≥2 adversarial rounds).
**Zero statistical-number changes across M2–M6** (no `ALGORITHM_VERSION` moved, goldens intact,
`abkit.stats` purity held). The sole remaining **named future deferral** (no version promise)
is `alpha_spending`/group-sequential (a `scheme: alpha_spending` config error names it); the
tagged PyPI publish is the maintainer's G1 step.

**M7 shipped** (the record is
[m7-implementation-plan.md](../../docs/specs/m7-implementation-plan.md) — done
table, per-WP as-built notes, exit-gate log; released as `0.2.0` — tagged and
published to PyPI): the validate
vectorization + iteration-policy milestone — the WP0 live multi-arm
Review-mode fix, the WP1 scalar hot path + hardening bucket A1–A8 (~149× on
`normal_test`), the WP2 batch significance kernels
(`supports_vectorized`/`from_suffstats_array`, bit-exact via `_libm_pow`),
the WP3 `vector_resample` block-streamed GEMM engine, the WP4 `score_cell`
dispatcher (~10×/cell), the WP5 parity + executable perf gates, the stretch
WP7 vectorized family sweep (~18×), and the WP6 policy (opt-in
`--family-sweep`, per-cell auto-N, warn-never-cap). **Zero statistical
numbers moved** — no `ALGORITHM_VERSION` bump, both e2e matrix gates
byte-identical; see "M7 vectorization facts" above for the working contracts.

**M8 shipped** (the record is
[m8-implementation-plan.md](../../docs/specs/m8-implementation-plan.md); PRs
#46–#51 + the WP7 docs-sync/release PR; release-ready as `0.3.0` — the
`v0.3.0` tag/publish is the maintainer's step): the no-copy
assignment default + the opt-in incremental `assignment.cohort_copy` engine +
`abk run --resync-cohort` + the both-mode e2e legs + the three-way docs sync —
see "M8 cohort facts" above for the working contracts. **Zero statistical
numbers moved** (cross-mode parity gates; no `ALGORITHM_VERSION` bump).

**Next — the polish track continues: M9–M17 → `0.4.0`…`0.12.0`** (track
approved 2026-07-18; it absorbs the whole "Post-baseline hardening" backlog —
see the track section in [ROADMAP.md](../../ROADMAP.md) and the as-designed
contracts
[m9](../../docs/specs/m9-implementation-plan.md)…[m12](../../docs/specs/m12-implementation-plan.md)
([m7](../../docs/specs/m7-implementation-plan.md) and
[m8](../../docs/specs/m8-implementation-plan.md) are now implementation
records); M13–M17 are contours, each opens with a design session). One WP = one session =
one PR; **M7–M12 move no statistical number** (parity gates + empty
`ALGORITHM_VERSION` grep); M13/M15 use full change control. The M8→M9 contract:
STATE/tail-scan SQL builds ONLY through M8's `build_cohort_backend` factory.
Read before coding:

- The M5 as-built + the math → [m5-implementation-plan.md](../../docs/specs/m5-implementation-plan.md),
  [statistics-changes.md §4](../../docs/specs/statistics-changes.md),
  [cumulative-intervals.md §6](../../docs/specs/cumulative-intervals.md)
- The A/A matrix contracts (M4 + M5 + M6 + M7 as-built, incl. the §9
  implementation note) → [aa-false-positive-matrix.md](../../docs/specs/aa-false-positive-matrix.md)
- The blocking must-fix checklist → [quorum-review.md](../../docs/specs/quorum-review.md)
- The cockpit & readout as-built contracts → [data-contract-and-reporting.md §5](../../docs/specs/data-contract-and-reporting.md),
  [cli-and-dx.md §2](../../docs/specs/cli-and-dx.md)
- The implementation records → [m2](../../docs/specs/m2-implementation-plan.md),
  [m3](../../docs/specs/m3-implementation-plan.md),
  [m4](../../docs/specs/m4-implementation-plan.md),
  [m5](../../docs/specs/m5-implementation-plan.md),
  [m7](../../docs/specs/m7-implementation-plan.md)

## Invariants (do not violate)

1. `abkit.stats` stays pure (numpy/scipy/statsmodels only).
2. Never change a number silently (version bump + changes entry + A/A).
3. Methods are plugins; nothing special-cases a method name.
4. The DB manager stays generic (`table_name`-keyed); `_ab_*` semantics live
   in `internal_tables/` only.
5. Greenfield storage — never copy the legacy `marts.*` schema.
6. Renderer stays framework-free (baked payload + self-contained JS).
7. Keep `init-claude` assets, `docs/`, and these rules in sync on release.
