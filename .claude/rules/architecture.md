# abkit architecture — as built

> The contributor/assistant condensation of the system **as it exists in code**.
> Reflects: **M1–M11 shipped** and **BOTH `0.6.x` interstitials released** —
> the `abk plan` one (PLAN-1 as `0.6.1`, PLAN-2 as `0.6.2`), `0.6.3` (the
> `paths.experiments` selection fix), and the cockpit/perf one as `0.6.4`:
> **UI-1** (the dashboard's YAML editor), **UI-2** (`abk ui`) and **PERF-1**
> (the additive read path made discoverable; the scaffold flipped to
> `incremental_reads: true`). All tagged and on PyPI; `[Unreleased]` is empty.
> M3's WP9 testcontainers hardening deferred to a Docker-equipped environment.
> **M12 is open** (`0.7.0`, notifications): NTF-1 — the send seam
> `abk run --notify` — is merged and sits in `[Unreleased]`.
> Design contracts for what is being *built next* (the M12–M17 polish track)
> live in [docs/specs/](../../docs/specs/) + [ROADMAP.md](../../ROADMAP.md);
> this file must never claim unbuilt code exists.
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
                         #   ✅ M5: plan (read-only pre-launch power/sizing);
                         #   ✅ M11 DASH-6: dashboard (the project-level cockpit
                         #   launcher; DASH-4a added `run --metric`)
  core/                  # ✅ M2: interval (N{s,m,h,d,w}), models (TableModel +
                         #   version_column LWW), period_planner (THE grid — one
                         #   enumeration for validator gates AND the anti-join);
                         #   ✅ M10 WP1: date|datetime anchors + interval_anchor
                         #   (cutoffs = anchor + k·cadence, kept after start_ts);
                         #   reachable ONLY via ExperimentConfig.grid() (AST gate)
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
                         #   ✅ M9 WP3: load_window — the STATE day render);
                         #   ✅ M9 WP4: incremental_backend (the opt-in
                         #   additive read path over _ab_unit_state);
                         #   ✅ M9 WP5: reconcile (the verify-incremental
                         #   cross-backend diff engine)
  pipeline/              # ✅ M2: driver (lock→load→SRM→plan→compute→persist),
                         #   analyze, enrich, _types; worker pool;
                         #   ✅ M9 WP3: state (the write-only STATE stage)
  reporting/             # ✅ M3: builder (the §5.3 terse payload + verdicts),
    assets/report.js     #   html_report (hardened bake), the committed bundle;
                         #   ✅ M4: calibration.py (the payload calibration block)
  tuning/                # ✅ M3: session (bounded Tier-S cache), recompute
    assets/explore.js    #   (Tiers E/α/S/R + D3 calibration), config_writer
    assets/dashboard.js  #   (Apply seam + .history + orphans), server (WP6:
                         #   ✅ M4 POST /validate Auto mode), payload, html;
                         #   ✅ M11: jobs (the subprocess registry, DASH-1),
                         #   overview (the one-row-per-experiment shaper,
                         #   DASH-2), dashboard_server (the launcher server —
                         #   DASH-3 page/stats routes + DASH-4 job routes),
                         #   html.render_dashboard_html, the committed
                         #   dashboard.js bundle (DASH-5);
                         #   ✅ UI-1: config_files (the editor's CRUD seam —
                         #   validate both levels → archive verbatim → atomic
                         #   write; owns the archive/atomic primitives
                         #   config_writer now imports) + the editor routes
                         #   and reload_selection in dashboard_server
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
  notify/                # ✅ M6: the 5 channels + BaseChannel/ReadoutData/factory
                         #   (`abk test-report`'s synthetic smoke test);
                         #   ✅ M12 NTF-1: dispatch (the `abk run --notify` seam —
                         #   persisted rows → readout.evaluate → one payload per
                         #   verdict) + factory.ROUTING_KEYS. `dispatch` is NOT
                         #   re-exported from the package __init__: it pulls in
                         #   config+pipeline, and `test-report` must resolve a
                         #   channel without either
  stats/                 # ✅ M1: the pure numpy core (details below);
                         #   ✅ M7: supports_vectorized + from_suffstats_array
                         #   (5-method roster) + effects._libm_pow batch kernels;
                         #   ✅ M10 WP5: the bootstrap _resample/_finalize split
                         #   + supports_resample_memo (6 classes; pure refactor)
    sequential/          # ✅ M5: the always-valid confidence sequence
                         #   (confidence_sequence, mixture τ², apply.to_always_valid;
                         #   ✅ M7: *_array siblings)
  utils/                 # stdlib-only: json_utils (canonical hash path),
                         #   datetime_utils (naive-UTC), env_interpolation;
                         #   ✅ M10 WP4: warn_scope (thread-scoped warning
                         #   capture — catch_warnings is process-global)
web/                     # ✅ M3: the dev-only TS toolchain (never wheel-shipped)
  src/shared/            #   chart.ts (canvas primitives + TOKEN_FALLBACKS —
                         #   THE brand-token layer), payload.ts (lockstep types)
  src/report/ src/explore/  # the renderers → committed assets (build.mjs)
  src/dashboard/         #   ✅ M11 DASH-5: the third renderer + its payload
                         #   types → abkit/tuning/assets/dashboard.js
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
                         # ✅ M10: core/test_grid_factory_is_the_only_entry.py,
                         #   core/test_period_planner.py::TestIntervalAnchor,
                         #   database/test_tables_contract.py (the _ab_experiments
                         #   catalog contract), tuning/test_session_cache_lock.py,
                         #   docs/test_no_stale_window_keys.py, and the exit gate
                         #   e2e/test_sub_day_anchors_and_explore.py (+ its
                         #   fixtures/window_golden_pre_m10.json, captured from
                         #   the pre-M10 code — regenerate ONLY from f85371d)
                         # ✅ M11: tuning/test_{jobs,overview,dashboard_server}.py,
                         #   cli/test_dashboard_command.py, the exit gate
                         #   e2e/test_dashboard_session.py (a real server over
                         #   live HTTP + a real `abk` child), and web/test/
                         #   smoke-dashboard.mjs
                         # ✅ UI-1: tuning/test_config_files.py + the editor
                         #   route class in test_dashboard_server.py + the
                         #   editor legs of the dashboard-session exit gate
  _helpers/fake_db.py    # in-memory manager with SQL-backend semantics
  _helpers/synthetic_ab.py  # SyntheticWarehouse (3 metric kinds, shuffle mode,
                         #   seed_null_events — the exact-null A/A fixture)
```

Every module in the map above exists; M3's WP9 (PG/MySQL testcontainers + the
two-process lock race) is deferred to a Docker-equipped environment.

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
  `POST /reload` (its own manager, serialized). **Since m10 WP4 the
  serialization is scoped:** `heavy_lock` covers `/reload`+`/validate`+`/apply`
  only, `/recompute` runs concurrently, the Tier-S cache is reached ONLY
  through `ExploreSession`'s `cache_lock`-guarded accessors (AST-gated), and
  `/recompute` re-checks staleness AFTER computing. Warning capture in
  `_compare`/`analyze`/A-A scoring goes through `utils/warn_scope` — never
  `catch_warnings`, which is process-global.
- **Tier-S bootstrap draws are memoized (m10 WP5).** Every bootstrap method
  splits into `_resample` (the replicates — alpha-free) and `_finalize` (CI +
  verdict at ONE alpha); the base class composes `from_samples` from the two
  and `supports_resample_memo` declares the capability (the M7
  `supports_vectorized` pattern — the engine falls back to the verbatim
  `_compare` otherwise). The engine memoizes the outcome on the session under
  `BootMemoKey(metric, name_1, name_2, end_ts, generation, method, resolved
  params)` — **compose it ONLY through `ExploreSession.boot_memo_key()`**
  (AST-gated, the m9 `state_series_key` discipline). Alpha is absent on purpose;
  dropping any other field collides — across metrics, across arm pairs, across
  the identity-EXCLUDED `seed` (which IS the draw), and across two methods that
  share a param set (`bootstrap` / `post-normed-bootstrap`). `max_block_bytes`
  and `pvalue_kind` ride along as belt-and-braces only: both are draw-invariant
  (measured), so they cost at most a missed hit — narrowing the key behind a
  declarative `ParamSpec` flag is a named follow-up. `generation` is
  `install_cutoff`'s per-cutoff counter, returned by `cached_entry()` in the
  same critical section as the entry, so a resample that lost the race to a
  `/reload` is unreachable rather than stale. `boot_memo` is reached only
  through the session's `boot_memo_lock` accessors (same AST gate as the
  cache), and the two locks are never nested.
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

### M9 facts an assistant must know (shipped: WP1–WP6)

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
  (a snapshot covariate is not day-additive), SQL body free of `ab_cov_*`,
  and the metric DECLARING `state_additive: true` (m9 WP5) — additivity
  cannot be read off SQL (a dead CTE, an outer re-aggregation, a UNION
  branch, an identity `sum()` over a renamed `max()` all look additive), so
  the author promises it and `_role_projections_are_additive` is a
  VETO-ONLY filter that refuses visibly contradicting projections
  (`max(...)`, a constant, `DISTINCT`, `OVER`, multi-branch SQL). The
  scaffolded `example_signup_cr` is exactly the hazard shape — caught by
  WP5's `verify-incremental`, which is the empirical oracle.
  Identity: `source_table = "{experiment}/{metric}"`
  (`compute_state_source_id` — the §5.3 sharing ideal deliberately
  narrowed: the render is cohort-filtered, so cross-experiment sharing
  would clobber) + `column_set_id = compute_metric_state_id(role_map,
  whitespace-normalized SQL, cohort_config)` where `cohort_config` folds in
  the cohort-shaping experiment config (assignment-SQL hash, added_filters,
  unit_key, variants, timezone, start_ts; horizon_ts only when the
  assignment SQL references `ab_end_*`; `interval_anchor` deliberately
  EXCLUDED — it moves cutoffs, never day boundaries) — compose the key ONLY through
  `pipeline/state.state_series_key()`. Any such edit orphans the series and
  the next run sweeps the stale ids. **Every failure path TRUNCATES the
  tail** (`delete_state_days_from`), preserving contiguity — every day
  `<= get_last_state_day()` is materialized, days past it are absent, not
  stale: `--full-refresh --from/--to` deletes from the first touched day
  BEFORE re-rendering through the end of the series (crash mid-refresh ⇒ a
  self-healing prefix); a non-finite moment truncates from the failing day
  (earlier days retained, one-render retry per run, a loud CLI warning).
  Copy mode clamps day-close to the copy's coverage; `--resync-cohort`
  force-rebuilds day state with the copy. WP4's `IncrementalBackend` is the
  (opt-in) reader.
- **WP4 (shipped): `IncrementalBackend` — the opt-in additive read path.**
  With `compute.incremental_reads: true` (project-level, experiment
  override; **default false** until WP5's `verify-incremental` bakes), the
  driver routes each STATE-eligible comparison (the SAME
  `comparison_state_eligible` predicate the WP3 writer uses — bootstrap/
  stratified/explicit-covariate always stay recompute) to
  `compute/incremental_backend.py`: closed days come from ONE
  `per_unit_cumulative` SUM over `_ab_unit_state` (cached per
  `(series, required_last)` — sub-day looks of one day share one read);
  a sub-day tail `[last tz-midnight, end_ts)` renders through the SAME m8
  factory backend (`load_window`); the per-unit totals reshape into the
  UNCHANGED `MetricLoadResult → build_container → SufficientStats` path
  (no new numerical code); the CUPED covariate keeps the one cached
  recompute-side load. **Safety net:** any state gap (absent/trailing/
  truncated series) falls back to full recompute for that cutoff with a
  per-(metric, reason) warning in `RunOutcome.warnings`; a non-finite tail
  falls back too; `--full-refresh`/`--resync-cohort` without the `state`
  step disable incremental reads for the run (stale-in-place state is
  undetectable by the gap check). Arm split: tail units carry the live
  tail render's arm; state-only units join the LOAD snapshot (direct) /
  `_ab_exposures` (copy), with ONE quiet refresh-on-miss re-read
  (`loaders/exposure_source.load_variant_map`) for units enrolled between
  LOAD and the STATE render; still-unmapped units drop (the INNER JOIN
  mirror). **Cross-path parity is rel-1e-9, never byte** (summation order
  differs by design — the M7 lesson); flag-off behavior is untouched.
  **Documented limitation** (m8 copy-mode precedent): an event backfilled
  into an already-materialized day LATER than `data_lag` freezes in day
  state — `data_lag` is the declared SLA; `--full-refresh` re-materializes
  + recomputes; WP5's `verify-incremental` is the drift detector.
- **WP5 (shipped): the reconciliation gate + cost observability.**
  `abk verify-incremental` (`compute/reconcile.py`) loads every
  already-computed cutoff through BOTH backends and diffs the `TestResult`
  dicts at rel-1e-9 — whole-series, read-only, lock-free, non-zero exit on
  divergence, and **never part of `abk run`**. A cutoff the incremental
  read fell back on is reported `unverified`, NOT as a pass (both sides
  ran the same code) — the reader's undeduped `on_fallback` hook gives the
  per-cutoff resolution. Both the driver and the reconciler construct the
  reader through the ONE `build_incremental_backend` factory, so the gate
  certifies the backend the pipeline runs. `abk run --cost-report` prints
  per-stage cost from counters on the manager (`QueryCost`): queries/rows
  returned/seconds everywhere, rows+bytes SCANNED only where the backend
  reports them (ClickHouse progress; PG/MySQL print `n/a`). `abk clean`
  sweeps `_ab_unit_state` series no live `(experiment, metric)` claims —
  selection-INDEPENDENT, since state rows are not experiment-keyed. The
  §7 perf gate (`tests/compute/test_incremental_perf.py`) asserts fact
  rows scanned exactly: `N·D(D+1)/2` recompute vs `N·D` incremental, zero
  inside COMPUTE at daily cadence. Default-flip criteria:
  [cumulative-intervals.md §4.1](../../docs/specs/cumulative-intervals.md).
- **WP6 (shipped): the exit gate.** `tests/e2e/test_incremental_run.py`
  drives the whole cycle through the CLI over the scaffolded project:
  twice-run byte-stability (and a `--full-refresh` that reproduces every
  number exactly), whole-series `verify-incremental` with **zero
  `unverified` cutoffs**, day state for the declared-additive metric only
  (one row per (unit, day)), CUPED Tier E on every knob but the lookback,
  a real drift → `DIVERGED` → non-zero exit → `--full-refresh` heals, and
  **the milestone's №1 assertion**: flag on vs flag off persists the same
  `_ab_results` — discrete columns exactly, continuous at rel-1e-9, with
  JSON payload columns PARSED before comparison (a CUPED θ differs in its
  last ULP; comparing the serialized strings would demand a property
  IEEE-754 does not offer). The ClickHouse leg
  (`tests/e2e/test_first_run_clickhouse.py`, Docker-gated) migrates a real
  **pre-M9 `_ab_results`** in place and reconciles the additive path
  against real SQL — the two claims the in-memory fake cannot settle.
- **Identity normalization (WP6 R1 fix):** compose state identity ONLY
  through `state_series_key()`, and hash SQL ONLY through
  `normalize_sql_for_identity()` — whitespace is formatting OUTSIDE quoted
  spans and DATA inside them (`'Summer  Sale'` ≠ `'Summer Sale'`), and
  comments are scanned as spans so an apostrophe in `-- don't sum` cannot
  open a phantom literal. A blanket `" ".join(sql.split())` let a semantic
  literal edit reuse stale day state — reproduced as a real divergence at
  the exit gate.
- **Catalog lookups are dialect-folded (WP6 R1 fix):** `table_exists` /
  `list_columns` compare `information_schema` STRINGS, while schema/table
  names reach the DDL unquoted — so PostgreSQL stores them lower-cased.
  Both go through `_catalog_name` (identity by default, `.lower()` on
  PostgreSQL; MySQL keeps the case deliberately). Without it a
  mixed-case `internal_schema` made every lookup miss, so `ensure_columns`
  never ran and an existing install silently skipped the M9 migration.

### M10 facts an assistant must know (shipped: WP1–WP5)

*(The WP4 lock-split and WP5 memo contracts live with the cockpit they change
— see the M3 explore facts above. What follows is the window/schema half.)*

- **An experiment's window is a pair of INSTANTS, and the config keys say so.**
  `start_date`/`end_date` are **gone**, renamed `start_ts`/`horizon_ts` with no
  aliases (D1) — an old key fails validation with a message naming the new one.
  Each accepts a bare date **or** a full timestamp (`2024-07-01 14:30:00`) and
  the union is type-PRESERVING (`date | datetime`) — which matters because
  `str(field)` reaches the m9 state-identity hash, so a re-parse that flipped
  the type would orphan every materialized series. Discrimination always tests
  `isinstance(value, datetime)` FIRST, because `datetime` subclasses `date` and
  the `date` branch would otherwise swallow both. A raw int/float is REJECTED
  rather than read as a Unix timestamp (`start_ts: 20240101` unquoted would be
  1970-08-23).
- **A bare date is local midnight of THAT day for BOTH edges (D6)**, so
  `horizon_ts` is the **EXCLUSIVE** right edge and the config value equals
  `grid.horizon_ts` exactly — one vocabulary, no `+1 day` translation anywhere.
  Porting a pre-m10 config shifts the horizon by one day
  (`end_date: 2024-07-14` → `horizon_ts: 2024-07-15`); every other number stays
  byte-identical (pinned by the exit-gate golden captured at `f85371d` over 22
  window shapes) **with two disclosed exceptions**: `horizon_seconds()` (below)
  and — for a `start_ts` on a local calendar day that never existed, which
  tzdata puts on exactly 3 dates between 1970 and 2036 (1993-08-21 Kwajalein,
  1994-12-31 Enderbury/Kanton/Kiritimati, 2011-12-30 Apia/Fakaofo; 7 zone
  entries counting aliases, all historical) — the pre-m10 series' ZERO-LENGTH
  opening look, which the m10 planner drops because it keeps cutoffs strictly
  after the start.
- **`interval_anchor` decides WHERE the cutoff lattice sits** (D2): `midnight`
  (the default the scaffold writes out — local midnight of the opening day,
  i.e. whole calendar days, the pre-m10 rule), `start` (count from the start
  instant), or an explicit local instant that MAY precede the start (the first
  window is then legitimately partial — config-lint notes it, never errors).
  One engine rule: **cutoffs = `anchor + k·cadence`, kept strictly after the
  segment's left edge**. Day-or-coarser steps hold the anchor's local
  wall-clock time across DST; sub-day steps stay absolute-duration. A
  whole-day `until` bound is compared in DAY space only while the anchor shares
  `start_ts`'s wall clock — off-phase it reads as elapsed seconds.
- **`ExperimentConfig.grid()` is THE factory** — nothing under `abkit/` may
  call `generate_grid` directly (AST gate:
  `tests/core/test_grid_factory_is_the_only_entry.py`). This is m8's
  `build_cohort_backend` discipline applied to the planner, and it exists
  because the new knob reached NONE of the eight hand-copied call sites; one of
  them passed `timezone` positionally as the 4th argument, so any parameter
  inserted before `tz` would have silently re-bound it.
- **`horizon_seconds()` is true elapsed time**, not a nominal day count, so it
  now agrees with `grid.horizon_ts − grid.start_ts`, which it contradicted
  pre-m10. The law, measured against the pre-m10 code across 19 window shapes:
  it differs from the old value by exactly **the UTC-offset change between the
  window's local edges**, and by nothing anywhere else. Not "±1h across DST" —
  that is wrong in both halves: the delta is −30 min in Australia/Lord_Howe, −2h
  in Antarctica/Troll, −24h across Pacific/Apia's 2011 line jump, and +1h with
  **no DST on either side** (Moscow's 2014 permanent +4→+3 shift). Consumers:
  config-lint's cadence gate — where a sub-day cadence sitting between the two
  lengths can flip accept↔reject — and the readout's pre-horizon rationale line.
  No persisted column derives from it.
- **A sub-day start never sums pre-experiment facts:** the STATE stage clamps
  the opening day's render window to `grid.start_ts`, and the CUPED pre-period
  stays WHOLE-DAY (`[midnight(D − lookback), midnight(D))`) instead of gaining
  a partial trailing day. `tz_midnight_utc` now REJECTS a `datetime` rather
  than silently dropping its time — but credit it with only the ONE M9 surface
  that actually reached it, the STATE stage's day-loop seed. The other two
  failed LOUDLY: `IncrementalBackend` compared a `date` against a `datetime`,
  which raises `TypeError` on every cutoff.
- **Both breaking schema changes of the whole track ship in `0.5.0`, with one
  recreate instruction** (§0.3): `_ab_results.start_date`/`end_date` are
  **dropped** — group BI by `end_ts`, and derive the calendar day a look covers
  as `end_ts − 1µs` read in the EXPERIMENT timezone (both corrections matter:
  `end_ts` is exclusive AND stored in UTC — `TestTimezoneDates` transcribes the
  recipe to Python and gates each correction separately against a real Moscow
  and a real New York run; the three dialect SQL forms in
  `docs/reference/internal-tables.md` are documentation, executed by no test) —
  and the
  `_ab_experiments` window is renamed + widened to `DateTime64(3)` holding the
  **RESOLVED** window in naive UTC (the same frame as `_ab_results.start_ts`,
  so a BI join lines up), plus an `interval_anchor` `String` column.
- **A type change is not auto-migratable, and the refusal is the upgrade
  path.** `ensure_columns` is ADD-only, so a pre-m10 `_ab_experiments` makes
  `ensure_tables()` raise a `ValueError` naming `DROP TABLE …` + the CHANGELOG.
  That failure now reaches the terminal as a CLI error line in **both**
  `abk run` and `abk unlock` (it used to escape the driver's handler as a
  traceback). Upgrading `_ab_results` is backend-asymmetric: PG/MySQL declare
  the dropped columns `DATE NOT NULL` so an omitting INSERT errors loudly,
  while **ClickHouse fills them with the type default and silently stamps
  `1970-01-01`** — the one silent path, which is why the recreate note is
  explicit about it.
- `interval_anchor` is deliberately **NOT** folded into the m9 state identity
  (it moves cutoffs, never day boundaries); the window fields ARE, so the
  rename orphans every existing `_ab_unit_state` series once — the next run
  re-materializes and `abk clean` sweeps the stale ids.

### M11 dashboard facts an assistant must know (shipped: DASH-1…DASH-7)

- **The dashboard COMPUTES NO STATISTIC and TAKES NO PIPELINE LOCK — this is
  the milestone's binding invariant, as restated by UI-1.** M11 wrote it as
  "computes a statistic, turns a knob, writes a config or takes the pipeline
  lock"; the fourth clause was never gated, and UI-1 (the YAML editor) drops
  it — a config write is the operator's own declaration, not a result, so no
  number on the page derives from it and it cannot block a pipeline. What the
  gates enforce is unchanged and is the real invariant. Gated twice, because
  one gate cannot see the other's hole: an AST scan proves
  `tuning/dashboard_server.py` never names the lock API (and the gate is proven
  to BITE on a hostile source), and a spy proves no helper takes it either —
  over **every job route** (`TestLauncherOnly`) and, since UI-1, over **every
  editor route**, which additionally proves `readout.evaluate` is never called
  there. The read-only `check_lock` probe behind a row's `locked` chip does
  run — that is the distinction. The token gate's POST route list is now
  AST-checked against `_route_post` too; M11 checked only the GET list, which
  left the routes that MUTATE covered by a hand-maintained list nothing
  verified.
- **A verdict on the page is `readout.evaluate()`'s over the FULL cumulative
  series.** `_ab_results` rows are cumulative looks from a fixed start, not a
  plain time series, so windowing them is not "a shorter series" — it is a
  truncated stabilization history: filtering the left edge read a 14-day daily
  WIN as INCONCLUSIVE and inverted a 6h-cadence series into a WIN the full
  readout refuses. `?window=`/`--window` therefore bounds **only the
  sparkline's x-range**, never the verdict. Two more shape rules: rows for an
  arm pair the config no longer declares are dropped **before** the series
  lookup (they still enter the BH family and would tighten the threshold — the
  `builder.py` discipline), and an experiment with **no rows of its own** must
  never reach `evaluate()` — zero rows rendered INCONCLUSIVE, i.e. a verdict
  about *data* on a row nobody computed. `verdict: null` + `error: null` is
  "no data — press Run"; with `error` set it is the error chip.
- **`insufficient` is the HEADLINE look's own persisted cell**, read through
  the readout's `_flag` (the report's `_flag01` is a bare `bool()` and
  disagrees on a `"0"` string cell) over the UNWINDOWED pair series, so no
  display window can move it and the chip cannot contradict the rationale
  beside it. The §4 markers ride the chip and a one-line note:
  `abk-srm-fail` / `abk-insufficient` / `abk-prehorizon` — a verdict taken
  early **under an always-valid sequence is deliberately NOT marked**.
- **The token gates EVERY request, GET included** (unlike `abk explore`, which
  gates only POSTs) — `GET /` enumerates the project and `GET /api/stats/…`
  reads the warehouse. Authorization runs **before** routing (a 403 is not a
  path oracle) and compares **bytes**: `compare_digest` refuses a non-ASCII
  `str`, so `?token=α` would raise before the handler's wrapper and never be
  answered. The token is never baked into the page (the client reads
  `location.search`). The gate's own coverage is machine-checked: the
  `parametrize` list is asserted against an **AST extraction of what
  `_route_get` actually dispatches on** (DASH-4's review found that list rotted
  once already — a new file-serving route was simply missing from it and would
  have shipped ungated), so the list is only as honest as that extraction.
- **The server never shuts itself down** (AST-gated over the module, the gate
  itself proven to bite on the explore server's copy-paste shape — and to
  ALLOW `server.jobs.shutdown()`, the registry teardown): `abk explore`'s
  Apply is terminal, the dashboard has no terminal action. It serves until
  Ctrl-C and then terminates every job it spawned.
- **`tuning/jobs.py` is the subprocess registry.** `JOB_KINDS` /
  `PIPELINE_KINDS` are validated **whitelists** at both entry points (the
  donor's blacklist let a typo fall UNDER the gate); `spawn_pipeline` is the
  one-at-a-time gate (`400` while a pipeline job runs), `spawn_deduped` is the
  atomic per-experiment dedup (`running_job_for()` + `spawn()` is check-then-act
  — a double-clicked Explore started two cockpits each rewriting the YAML from
  its own snapshot); `snapshot(offset=)` counts **absolute** line indices, so a
  job chattier than the 5000-line buffer keeps streaming and discards are
  disclosed as `dropped`/`truncated`. A spawn racing `shutdown()` raises
  `JobManagerClosed` (⇒ **503**, "busy" is what `None` means) and kills **and
  reaps** the child it just created.
- **Every button spawns `sys.executable` through a bootstrap that drops the CWD
  from `sys.path`.** `-m` puts the child's CWD (the operator's project root) on
  `sys.path[0]`, where a stray `click.py` breaks every button and an `abkit/`
  directory runs a *different* abkit than the one serving the page — neither of
  which happens when you type `abk`. Consequence: **every spawned job needs an
  installed abkit**; `abk dashboard` warns once at startup, and only on the
  CONJUNCTION of two probes (a `sys.path` search with the CWD dropped, then the
  `sys.meta_path` finders that answer for a strict editable install) — dist-info
  metadata is NOT a signal, a checkout whose install was removed keeps it.
- **`--select` is the experiment's YAML path** (glob metacharacters `*?[`
  escaped, not abandoned), and every job route **re-resolves** it through
  `select_experiments` — the child's own resolver — before spawning, answering
  400 unless it lands on exactly the clicked experiment. Two reasons, both
  silent otherwise: a bare name resolves file-first, so a file named after
  another experiment shadows it; and `abk run/unlock/clean` meet an unmatched
  selector with "Nothing selected." and **exit 0**, which would show a green,
  successful job that computed nothing.
- **`dashboard.js` is the third committed bundle** and obeys the M3 build
  discipline verbatim (edit `web/src/dashboard/**` → `cd web && npm run build`
  → commit the asset in the same PR; the marker/hex/freshness gates cover it by
  glob). It is named in **two** hardcoded wheel namelists — `.github/workflows/
  ci.yml` and `tests/e2e/test_release_readiness.py` — and a missing bundle now
  RAISES rather than degrading to a "run npm build" note (a `pip install` user
  cannot fix that).
- **`abk run --metric <m>` (DASH-4a) is the CLI capability the per-metric Run
  button needs.** The alphas are invariant **by construction** —
  `effective_alphas()` derives the two-tier scheme from the *config's*
  comparison list, never from what a run computes — and that is the WP's #1
  pinned assertion. The one thing a narrowed run must still touch outside its
  filter is **day state**: a stale-but-contiguous `_ab_unit_state` day is
  invisible to the M9 gap check (it detects ABSENCE only), so a scoped
  `--full-refresh` **truncates** the withheld metrics' series from the first
  touched day onward instead of leaving it; the cohort load, the SRM gate and
  copy-mode `--resync-cohort`'s rebuild stay experiment-level.

### UI-1 / UI-2 facts an assistant must know (the dashboard's editor)

- **`tuning/config_files.py` is the editor's seam, and it is NOT
  `config_writer.py`.** Apply (explore) merges a *structured* edit and RE-EMITS
  the parsed document, so comments die and the archive is the recovery (D4);
  the editor round-trips the operator's raw TEXT, so comments and layout
  survive (normalized only to end with a newline). The two share only the
  filesystem primitives — `archive_config_text` / `atomic_write_bytes` /
  `stamp` now live in `config_files` and `config_writer` imports them, so both
  surfaces land in the SAME `<dir>/.history/<name>/` tree.
- **Order is validate → archive → write, and validation is BOTH levels.**
  Level 1 is `ExperimentConfig`; level 2 is `validate_experiment_level2`, the
  §8 matrix `abk run --steps validate` runs (reference integrity, CUPED rules,
  the cadence/looks gates over the real grid, the no-DB SQL render smoke). The
  metric library is re-read from disk **per save** rather than taken from the
  boot snapshot, and leniently — a metric that fails to parse is simply absent,
  so a broken metric cannot block an unrelated experiment's save.
- **`force` overrides level 2 and NOTHING else.** A file pydantic rejects
  cannot be served as a row, so level 1 is never forceable; level 2 is a
  statement about the whole PROJECT, and an editor that refuses until the
  project is coherent is unusable in the situation it is opened for. A forced
  save returns its findings as `SAVED WITH AN ERROR — abk run will refuse
  this: …`.
- **The digest is the concurrency token, and it is checked BEFORE the text is
  parsed.** `GET /api/experiment-source` hands out a sha256 of the file;
  `save`/`delete` echo it and are refused on a mismatch. Two writers make that
  ordinary: a second tab, and an `abk explore` Apply — which the dashboard can
  itself spawn on the experiment being edited. A **truncated** read (>512 kB,
  `_MAX_SOURCE_BYTES` — the same constant on both sides) returns
  `digest: null, editable: false`: a digest over a prefix would let a save
  write the prefix back and drop the tail. A stale buffer has to be reopened
  either way, which is why the digest is reported before a parse error.
- **A save/delete is refused while the cockpit has a running job on that
  experiment** (`_refuse_if_busy`, over the `JOB_KINDS` whitelist). Not a lock —
  a lock is exactly what this server may not take; it is the narrow check a
  launcher CAN make, over the jobs it started itself. A job started from a
  terminal is invisible to it, and the digest catches the explore half of that
  after the fact.
- **The boot snapshot is gone: `reload_selection()` is the re-derivation
  seam.** Every mutation route calls it, `POST /api/reload` is the manual form
  (M11's named follow-up), and it re-resolves the cockpit's OWN
  `--select`/`--exclude` — which is why `build_dashboard_server` takes
  `selectors`/`excludes` and `abk dashboard` passes them: re-deriving them
  server-side would silently widen an edited page to the whole project. It
  **never raises**: the write has already landed, so a broken sibling YAML or a
  name collision keeps the previous selection and rides back in `warnings`
  ("restart the dashboard"), rather than reporting a successful save as a 500.
  `experiments` / `_by_name` / `metrics` / `html` are written and read under
  `selection_lock` and re-baked together.
- **Delete removes the YAML only** (`-deleted.yml` tombstone in the archive);
  `_ab_results` / `_ab_unit_state` rows stay until `abk clean
  --orphaned-experiments`, and the reply says so. A **rename** keeps the file's
  path, archives under the OLD name, and warns that the persisted history does
  not follow. Name uniqueness is checked over the WHOLE project and across the
  ONE experiment+metric namespace (cli-and-dx §1), never against the served
  selection.
- **UI-2: `abk ui` is `cli.add_command(dashboard, name="ui")`** — the same
  callback object, so options and help cannot drift. `dashboard`/`explore` name
  the surface where `ui` does not, so the canonical name stays.

### PERF-1 facts an assistant must know (the additive read path, made loud)

- **`AdditiveReadStatus` (`pipeline/_types.py`) is the whole surface**, and it is
  PURE — `hint()` takes no config and no warehouse, so the rules about when
  abkit nags are unit-tested without a DB. The driver fills it on every run that REACHES COMPUTE
  (eligibility is measured even with the flag off — that is the point; a
  `--steps …,state` run that stops before COMPUTE reports nothing, since
  eligibility is resolved per comparison inside that loop) and
  `abk run` echoes `hint()` through `outcome.warnings`, because that is the
  channel which reaches the terminal (the M7 `decision_log` lesson).
- **An ABSENT `compute.incremental_reads` and an explicit `false` are different
  things**, distinguished by pydantic's `model_fields_set` (the field is still a
  plain `bool`; nothing that reads it changed). They resolve identically — only
  the first is *undecided*, and only the first is nagged. Without the
  distinction the warning could never be answered, and an unanswerable warning
  is just a different silence. An experiment-level override counts as declared
  iff it is not `None`.
- **The threshold is LOOKS, not days** (`MIN_LOOKS_TO_MATTER = 6`).
  cumulative-intervals §4.1 states it in days because it assumed a daily grid;
  the recompute scan is quadratic in looks and an hourly cadence re-reads the
  window 24× a day. `series_looks` is `len(computed) + len(pending)` — disjoint
  by construction — maxed over the eligible comparisons.
- **`_stage_cost` is variadic and `compute.additive` is a SLICE, not a sibling.**
  An eligible look's measured delta lands in both `"compute"` and
  `"compute.additive"`; summing the two printed lines double-counts. Every
  COMPUTE load for an eligible comparison goes through the same `cost_stages`
  tuple, the sequential τ² load included.
- **Fallback extent comes from `on_fallback`, never from the warnings.** The
  reader's `_warn_once` is deduped per (metric, reason), so it can name the
  cause but can never say how many looks paid for it; `on_fallback` fires at
  most once per `load_cutoff` (the reader returns the recompute result
  immediately after), so counting calls counts cutoffs.
- **`abk init` writes `incremental_reads: true`; the library default is still
  `false`.** The two differ on purpose (§4.2): the scaffold's seed data never
  backfills, and the flag guards exactly one thing — a backfill later than
  `data_lag` freezing in day state — which is the operator's ingestion SLA.
- **The scaffold flip made the M9 parity gate vacuous and the suite stayed
  green.** `test_incremental_run`'s "flag off" leg used to turn the flag ON by
  *not* appending a `compute:` block, so leg 4 compared the incremental path
  against itself. Both legs now call
  `tests/_helpers/scaffold.py::set_incremental_reads`, which asserts the edit
  landed AND re-parses to confirm, and each leg proves from `--cost-report`
  output which path it actually took. Any new test scaffolding a project must
  state the flag rather than rely on silence.

### M12 NTF-1 facts an assistant must know (the notification send seam)

- **`abkit/notify/dispatch.py` computes nothing.** It loads the persisted rows,
  calls `readout.evaluate()` — the function `build_report_payload` and the
  dashboard already call — and copies each `PairVerdict` field onto a
  `ReadoutData`. This is the M11 launcher discipline applied to a second
  surface: a message that re-derived a number could disagree with the report
  about the same experiment, and the operator would have no way to tell which
  was right.
- **Two `on:` filters, INTERSECTED.** `NotificationChannelConfig.on` (what a
  channel accepts) and `ExperimentConfig.notify.on` (what an experiment sends)
  answer different questions; `passes_filter` requires both, so neither can
  re-open what the other closed. Both accept all six `SignalKind`s
  (`abkit/config/signals.py` — a leaf module, because both config models need
  the literal and neither may import the other's dependency tree). Only
  `readout` fires until NTF-2.
- **A declared field on `notification_channels` reaches the channel
  constructor unless the factory strips it.** The block is `extra="allow"`, so
  every sibling key is a kwarg — and because `on:` is *declared*,
  `model_dump()` emits `on: None` even for a config that never writes it.
  Without `ChannelFactory.ROUTING_KEYS`, adding the field alone breaks **every**
  channel, `abk test-report` included (18 tests red under the mutation probe).
  The gate is `test_every_declared_field_is_classified`: declared fields minus
  `type` must EQUAL `ROUTING_KEYS`, so a future routing key cannot be added
  without classifying it.
- **D1 (signed off 2026-08-02): `--notify` is the switch, `notify:` is only
  routing.** An experiment with no block — or a block with no `channels` —
  sends to every configured channel. `--notify` with *no* configured channels
  prints a loud line: silence there is indistinguishable from a broken flag.
- **Fail-soft is doubled on purpose** (§0.4 point 1): `dispatch` catches per
  channel (one bad channel cannot block the rest) and `run.py` wraps the whole
  call (a failure before any channel — a warehouse read, a config surprise —
  cannot fail the run). A simplify pass must not collapse them.
- **Nothing is sent about an experiment nobody computed.**
  `load_experiment_readout` returns `None` for a missing results table, zero
  rows, or rows only for undeclared arm pairs — the m11 DASH-7 finding in
  message form (`evaluate()` over zero rows answers INCONCLUSIVE, a verdict
  about *data*). A `completed` outcome notifies a readout; a `failed` one
  notifies an **error notice** instead (NTF-2), and that path is deliberately
  NOT gated on rows — the absence of a result is what it reports. `locked` and
  `skipped` stay silent: neither produced a new look, and neither is a failure.
- **NTF-2: a notice is a `ReadoutData` with `kind` set, not a second payload
  type.** `NOTICE_KINDS` (`error`/`calibration_red`/`stale`) means *nothing was
  measured*, so every renderer branches once and omits the effect/CI/p block
  rather than printing `N/A` — a crashed run showing "Effect: N/A · Flat" is a
  claim about the experiment where the truth is that abkit never looked. The
  kind rides ON the payload because `verdict_color()` is called deep inside each
  channel's payload builder, where a `send_notice(notice, kind)` argument could
  not reach it — the plan's two-argument signature would have been a second
  source of truth. `send_notice` refuses a verdict payload loudly.
- **No sixth brand hex.** The five tokens in `docs/design/brand-tokens.md` are
  VERDICT tokens; a notice is not a verdict, so all three notice kinds reuse
  `--srm` `#B23A6B` — the one token that already means "no trustworthy result,
  look at this" — and the word + emoji carry the distinction
  (`BaseChannel._NOTICE_PRESENTATION`). A designer adding a real error token
  later changes that one map.
- **SRM is a RE-CLASSIFICATION, never a re-evaluation or a second message.**
  `signal_kinds_for()` answers `("readout", "srm")` for an SRM-failed readout
  and delivery asks "does ANY kind pass both filters", so a channel accepting
  both still receives exactly one message.
- **NTF-3: the dedup signature is `(verdict, srm_flag)`, and it lives in
  `_ab_notify_states`.** `notify/cooldown.py` is the pure rule (no DB, no
  config): a change always announces, an unchanged value never re-announces
  (D2). Deduping on the verdict WORD alone is the trap — a pre-horizon pair says
  INCONCLUSIVE either way, so a newly broken SRM gate would be silenced on the
  experiments most likely to need the alarm. `cooldown_seconds` is not consulted
  here at all; `is_in_cooldown` exists for a future recurring kind, and the
  config field is deliberately NOT added until one needs it.
- **State is recorded only after a channel ACCEPTED the message** — `_deliver`
  returns per-payload success counts for exactly this. An announcement that
  reached nobody must not become history: nothing re-derives what was never
  sent, so the flip would be lost permanently.
- **`_ab_notify_states` is in `EXPERIMENT_KEYED_TABLES`**, so `abk clean
  --orphaned-experiments` purges it — a deleted-and-reused experiment name would
  otherwise inherit the old announcement history and have its first verdict
  deduped away. `states` is a REQUIRED keyword on
  `dispatch_experiment_signals` (explicit `None` disables dedup) so no caller
  can turn the quiet off by forgetting it.
- **The notify block sits BEFORE the report block in `run.py`'s outcome loop**,
  because the report block's `if report_path is None: continue` would skip
  everything after it. Both share one lazily-built manager, now honestly named
  `readback_*` rather than `report_*`.

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

## M5–M10 as built (specs are canonical)

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
#46–#51 + the WP7 docs-sync/release PR; **released as `0.3.0`** — tagged and
published to PyPI): the no-copy
assignment default + the opt-in incremental `assignment.cohort_copy` engine +
`abk run --resync-cohort` + the both-mode e2e legs + the three-way docs sync —
see "M8 cohort facts" above for the working contracts. **Zero statistical
numbers moved** (cross-mode parity gates; no `ALGORITHM_VERSION` bump).

**M9 shipped** (the record is
[m9-implementation-plan.md](../../docs/specs/m9-implementation-plan.md); PRs
#53–#56 + #58 + the WP6 exit-gate PR #59; **released as `0.4.0`** — tagged and
published to PyPI): the additive compute engine + CUPED
Tier-E — see "M9 facts an assistant must know" above for the working
contracts. **Zero statistical numbers moved** (the flag on/off parity gate;
no `ALGORITHM_VERSION` bump). The **library** default of
`compute.incremental_reads` is still off, but since PERF-1 `abk init` writes
`true` and `abk run` will not stay quiet about an undecided project — the flip
criteria in
[cumulative-intervals.md §4.1](../../docs/specs/cumulative-intervals.md) have
been executed, with the numbers in §4.2 (facts below).

**M10 shipped** (the record is
[m10-implementation-plan.md](../../docs/specs/m10-implementation-plan.md) —
done table, per-WP as-built notes, the §3 exit-gate record, the §6 review log;
PRs #61–#64 + the exit-gate PR; **released as `0.5.0`** — tagged and published
to PyPI): timestamps + both track schema breaks +
explore polish — the renamed `start_ts`/`horizon_ts` window with
`interval_anchor` and the one `ExperimentConfig.grid()` factory (WP1–WP2), the
dropped `_ab_results` date columns + the renamed/widened `_ab_experiments`
window (WP2–WP3), the decoupled `heavy_lock` (WP4) and the bootstrap resample
memo (WP5). See "M10 facts an assistant must know" and the M3 explore facts
above for the working contracts. **Zero statistical numbers moved** (no
`ALGORITHM_VERSION` bump; the exit gate's window golden was captured from the
pre-M10 code itself) — with one disclosed derived-number change,
`horizon_seconds()` across a DST transition.

**M11 shipped** (the record is
[m11-implementation-plan.md](../../docs/specs/m11-implementation-plan.md) —
done table, per-WP as-built notes, the exit-gate log; PRs #66, #68, #69,
#71–#75 + the docs-only decisions PR #67; **released as `0.6.0`** — tagged
and published to PyPI): `abk dashboard`, the
project-level cockpit — the job registry `tuning/jobs.py` (DASH-1), the row
shaper `overview.py` (DASH-2), the launcher server `dashboard_server.py`
(DASH-3 page/stats routes + DASH-4 job routes), `abk run --metric` (DASH-4a),
the third committed bundle `dashboard.js` from `web/src/dashboard/` (DASH-5),
the `abk dashboard` command + docs + both wheel-namelist gates (DASH-6), and
the live-HTTP exit gate `tests/e2e/test_dashboard_session.py` (DASH-7). See
"M11 dashboard facts an assistant must know" above for the working contracts.
**Zero statistical numbers moved** (no `ALGORITHM_VERSION` bump; every verdict
the page shows is `readout.evaluate()`'s). CRUD config editing was explicitly
phase 2 — it shipped as the `0.6.x` UI-1 interstitial (facts below).

**Next — the polish track continues: M12–M17 → `0.7.0`…`0.12.0`** (track
approved 2026-07-18; it absorbs the whole "Post-baseline hardening" backlog —
see the track section in [ROADMAP.md](../../ROADMAP.md) and the as-designed
contract
[m12](../../docs/specs/m12-implementation-plan.md)
([m7](../../docs/specs/m7-implementation-plan.md),
[m8](../../docs/specs/m8-implementation-plan.md),
[m9](../../docs/specs/m9-implementation-plan.md),
[m10](../../docs/specs/m10-implementation-plan.md) and
[m11](../../docs/specs/m11-implementation-plan.md) are now implementation
records); M13–M17 are contours, each opens with a design session). The `0.6.x`
**PLAN-1/PLAN-2** interstitial is closed (released as `0.6.1`/`0.6.2`; design
contract: [cli-and-dx.md](../../docs/specs/cli-and-dx.md) "`abk plan` sizing
gaps"); the second `0.6.x` interstitial is closed too, released as `0.6.4` —
**UI-1** (CRUD YAML editing in `abk dashboard`; it restated the launcher
invariant it does not actually violate — facts above), **UI-2** (`abk ui`
alias) and **PERF-1** (the additive read path made discoverable; the scaffold
flipped to `incremental_reads: true`), tagged and published to PyPI.
One WP = one session =
one PR; **M7–M12 move no statistical number** (parity gates + empty
`ALGORITHM_VERSION` grep); M13/M15 use full change control. Two binding
inter-milestone contracts: the M8→M9 one (honored — STATE/tail-scan SQL builds
ONLY through `build_cohort_backend`) and M10's (the planner is reached ONLY
through `ExperimentConfig.grid()`). Only the second is AST-gated
(`tests/core/test_grid_factory_is_the_only_entry.py`); the cohort-factory
contract is still honor-system, which is exactly the shape that let a
decorative knob reach none of eight call sites — a gate for it is a named
follow-up.
Read before coding:

- The M5 as-built + the math → [m5-implementation-plan.md](../../docs/specs/m5-implementation-plan.md),
  [statistics-changes.md §4](../../docs/specs/statistics-changes.md),
  [cumulative-intervals.md §6](../../docs/specs/cumulative-intervals.md)
- The A/A matrix contracts (M4 + M5 + M6 + M7 as-built, incl. the §9
  implementation note) → [aa-false-positive-matrix.md](../../docs/specs/aa-false-positive-matrix.md)
- The blocking must-fix checklist → [quorum-review.md](../../docs/specs/quorum-review.md)
- The cockpit & readout as-built contracts → [data-contract-and-reporting.md §5](../../docs/specs/data-contract-and-reporting.md),
  [cli-and-dx.md §2](../../docs/specs/cli-and-dx.md); the **dashboard's** own
  contract (launcher discipline, row shape, job routes) →
  [m11-implementation-plan.md](../../docs/specs/m11-implementation-plan.md)
- The implementation records → [m2](../../docs/specs/m2-implementation-plan.md),
  [m3](../../docs/specs/m3-implementation-plan.md),
  [m4](../../docs/specs/m4-implementation-plan.md),
  [m5](../../docs/specs/m5-implementation-plan.md),
  [m7](../../docs/specs/m7-implementation-plan.md),
  [m8](../../docs/specs/m8-implementation-plan.md),
  [m9](../../docs/specs/m9-implementation-plan.md),
  [m10](../../docs/specs/m10-implementation-plan.md),
  [m11](../../docs/specs/m11-implementation-plan.md)

## Invariants (do not violate)

1. `abkit.stats` stays pure (numpy/scipy/statsmodels only).
2. Never change a number silently (version bump + changes entry + A/A).
3. Methods are plugins; nothing special-cases a method name.
4. The DB manager stays generic (`table_name`-keyed); `_ab_*` semantics live
   in `internal_tables/` only.
5. Greenfield storage — never copy the legacy `marts.*` schema.
6. Renderer stays framework-free (baked payload + self-contained JS).
7. Keep `init-claude` assets, `docs/`, and these rules in sync on release.
