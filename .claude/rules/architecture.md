# abkit architecture — as built

> The contributor/assistant condensation of the system **as it exists in code**.
> Reflects: **M1 + M2 + M3 + M4 + M5 + M6 shipped** (`__version__ = 0.1.2`,
> released on PyPI; M3's WP9 testcontainers hardening deferred to a
> Docker-equipped environment).
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
  loaders/               # ✅ M2: query_template (ab_* built-ins, StrictUndefined),
    templates/           #   the packaged abkit_assignment.jinja macro,
                         #   exposure_loader, metric_loader
  compute/               # ✅ M2: recompute_backend (v1 full-window strategy)
  pipeline/              # ✅ M2: driver (lock→load→SRM→plan→compute→persist),
                         #   analyze, enrich, _types; worker pool
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
                         #   ✅ M5: family (D9 composed FWER/FDR union-cohort sweep)
  planning/              # ✅ M5: sizing (pure required-N/MDE/power over stats.power) —
                         #   the `abk plan` engine; read-only, refuses ratio/bootstrap
  stats/                 # ✅ M1: the pure numpy core (details below)
    sequential/          # ✅ M5: the always-valid confidence sequence
                         #   (confidence_sequence, mixture τ², apply.to_always_valid)
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
- **`ci_kind` is always `"fixed"` in M2** (sequential lands M5); the STATE
  stage is deliberately not wired in v1 (recompute read path — see the driver
  docstring); paired methods are notebook-only.

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
  inject/scoring`) touches only `abkit.stats`; `load.py` reads the warehouse through
  the backend loaders and **never writes** (a placebo split is in-memory only — a
  persisted shuffle would clobber the real `_ab_exposures`); the CLI takes the lock
  and persists.
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

## M5 + M6 as built (specs are canonical)

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
`_ab_exposures` arrival + always-valid ASN), the A/A **sequential × composed** family sweep
(WP-B, `validate/family.py`), and the release engineering (`__version__ = 0.1.0`, classifier
`3 - Alpha`, the wheel-namelist + `pip install` DoD gates, `tests/docs/test_docs_single_source.py`)
behind the WP10 exit gate (`tests/e2e/test_release_readiness.py` + ≥2 adversarial rounds).
**Zero statistical-number changes across M2–M6** (no `ALGORITHM_VERSION` moved, goldens intact,
`abkit.stats` purity held). The sole remaining **named future deferral** (no version promise)
is `alpha_spending`/group-sequential (a `scheme: alpha_spending` config error names it); the
tagged PyPI publish is the maintainer's G1 step.

**Next — the polish track M7–M17 → `0.2.0`…`0.12.0`** (approved 2026-07-18; it
absorbs the whole "Post-baseline hardening" backlog — see the track section in
[ROADMAP.md](../../ROADMAP.md) and the as-designed contracts
[m7](../../docs/specs/m7-implementation-plan.md)…[m12](../../docs/specs/m12-implementation-plan.md);
M13–M17 are contours, each opens with a design session). One WP = one session =
one PR; **M7–M12 move no statistical number** (parity gates + empty
`ALGORITHM_VERSION` grep); M13/M15 use full change control. The M8→M9 contract:
STATE/tail-scan SQL builds ONLY through M8's `build_cohort_backend` factory.
Read before coding:

- The M5 as-built + the math → [m5-implementation-plan.md](../../docs/specs/m5-implementation-plan.md),
  [statistics-changes.md §4](../../docs/specs/statistics-changes.md),
  [cumulative-intervals.md §6](../../docs/specs/cumulative-intervals.md)
- The A/A matrix contracts (M4 + M5 + M6 as-built) → [aa-false-positive-matrix.md](../../docs/specs/aa-false-positive-matrix.md)
- The blocking must-fix checklist → [quorum-review.md](../../docs/specs/quorum-review.md)
- The cockpit & readout as-built contracts → [data-contract-and-reporting.md §5](../../docs/specs/data-contract-and-reporting.md),
  [cli-and-dx.md §2](../../docs/specs/cli-and-dx.md)
- The implementation records → [m2](../../docs/specs/m2-implementation-plan.md),
  [m3](../../docs/specs/m3-implementation-plan.md),
  [m4](../../docs/specs/m4-implementation-plan.md),
  [m5](../../docs/specs/m5-implementation-plan.md)

## Invariants (do not violate)

1. `abkit.stats` stays pure (numpy/scipy/statsmodels only).
2. Never change a number silently (version bump + changes entry + A/A).
3. Methods are plugins; nothing special-cases a method name.
4. The DB manager stays generic (`table_name`-keyed); `_ab_*` semantics live
   in `internal_tables/` only.
5. Greenfield storage — never copy the legacy `marts.*` schema.
6. Renderer stays framework-free (baked payload + self-contained JS).
7. Keep `init-claude` assets, `docs/`, and these rules in sync on release.
