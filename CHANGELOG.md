# Changelog

All notable changes to ab-analysis-kit will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Once implementation begins, `CHANGELOG.md` is **authoritative for behavior changes**
— in particular every statistical deviation from the captured legacy baseline is
recorded here alongside an `ALGORITHM_VERSION` bump and a
[`statistics-changes.md`](docs/specs/statistics-changes.md) entry (never a silent
number change).

## [Unreleased]

### Fixed
- **MDE solve crash + report cost** (M3 WP2 review-closure, adversarial
  re-verification): `abkit.stats.power` — statsmodels' `solve_power` returns a
  shape-`(1,)` ndarray from its `fsolve` fallback for a data-dependent
  few-percent of ordinary `(nobs, ratio)` inputs (e.g. n=139, ratio=1.0);
  under numpy ≥ 2.0 `float(ndarray)` raised, crashing the readout verdict and
  report MDE paths. `_as_scalar` now extracts the value (value-preserving —
  golden tests unchanged, **zero statistical numbers changed**). And the report
  payload's per-point `mde` reads the **stored** `mde_1/2` columns only (null
  when the row did not compute MDE) instead of a read-time statsmodels solve
  per point — the read-time D5(b) fallback stays verdict-level (one solve per
  pair on the latest cutoff). A worst-case sub-day payload dropped from
  ~40–100 s (and a hard crash) to milliseconds; data-contract §5.3 amended.

### Added
- **M3 WP2 — the experiment-primary report payload** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md) D6):
  - `abkit.reporting.builder`: `build_report_payload(experiment, tables, ...)`
    — one versioned JSON-serializable payload per experiment from persisted
    `_ab_results` rows, the shared contract for the WP3 readout renderer and
    the WP6/WP7 explore shell: WP1 verdict block, experiment-level SRM block
    (driver-mirrored zero-filled exposure counts), M4-shaped
    `calibration: null`, `look: {n, planned}` from the one-enumeration
    planner grid, terse ms-epoch series points, NaN **and ±inf** → null,
    provenance projection (rendered SQL never enters the payload; one
    `metric_query` per metric), metric descriptions from the metric YAML,
    caller-supplied `generated_at`, inclusive `start`/`end` window pinning
    (historical readout replay), a global point budget with trailing-window
    clipping + a loud payload warning, and the full-key empty-experiment
    contract. Zero statistical numbers changed.
  - `InternalTablesManager`: `results_table_exists()` /
    `exposures_table_exists()` — the never-run-project guards for read-only
    surfaces (reporting never creates schema) — and an `until=` bound on
    `get_exposure_counts` (`exposure_ts < until`, half-open) so a replayed
    report shows the as-of cohort, not today's.
  - Review-driven consistency rules (adversarial review, 4 lenses): rows for
    variant pairs outside the declared arms are excluded from every payload
    surface with a loud warning (never silently mixed into look/period/BH);
    the driver's orphaned-`method_config_id` scan is surfaced as a payload
    warning on the read path too.
  - Specs amended (data-contract-and-reporting.md §5: subsections numbered
    5.1/5.2, the D2 explore data-source rewording, new §5.3 payload contract;
    §2 metric-description sourcing note).
- **M3 WP1 — the readout decision core** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md) D5):
  - `abkit.pipeline.readout`: pure read-time WIN/LOSE/FLAT/INCONCLUSIVE
    verdicts over persisted `_ab_results` rows — SRM hard gate; pre-horizon
    withholding (extends to FLAT); elapsed-time stabilization over the
    trailing `readout.stabilization_days` (default 7, floored at 3
    informative cutoffs); FLAT gated on `min_effect` vs the pair MDE with a
    read-time MDE fallback for t-test/z-test rows (the z-test `nobs` inverted
    from the persisted SE, never the unit count); guardrail regression under
    the owner-ratified `guardrail_policy: block | warn`; read-time
    Benjamini-Hochberg rescoring (pulled forward from the M5 roadmap line —
    compute-time BH rows carry the raw alpha); orphaned/unconfigured row
    filtering with warnings. Verdicts are read-time only, never persisted.
    Zero statistical numbers changed.
  - Experiment config: `readout: {stabilization_days, guardrail_policy}` and
    per-comparison `min_effect` / `desired_direction` (read-time only — never
    part of `method_config_id`); specs amended
    (data-contract-and-reporting.md §1, declarative-config.md §2).
- **M2 — declarative config + DB layer + the recompute pipeline** (per
  [`docs/specs/m2-implementation-plan.md`](docs/specs/m2-implementation-plan.md)):
  - `abkit.core`: duration parser (`N{s,m,h,d,w}`), `TableModel`/`ColumnDefinition`
    (+`max_length` for MySQL key budgets), and `period_planner` — ONE pure grid
    enumeration (scalar + dense-early schedule cadence, experiment-tz midnight
    snapping, DST-safe, horizon always flagged) consumed by BOTH the validator's
    look gates and the planner anti-join; `data_lag: 0` + half-open windows
    reproduce `*_wo_curr_day` exactly.
  - `abkit.database`: generic CH/PG/MySQL managers with the quorum **atomic
    lock** primitive (PG single-statement `INSERT…ON CONFLICT…DO UPDATE…WHERE`;
    MySQL row-alias upsert with the claim verdict latched into a session
    variable; ClickHouse advisory claim with a deterministic read-back
    tie-break) and the greenfield `_ab_*` schema: `_ab_experiments`,
    `_ab_exposures` (persisted cohort), `_ab_unit_state` (replace-not-sum,
    keyed per source-table+column-set+unit+day; twice-run invariant tested),
    `_ab_results` (the BI contract incl. new `warnings`/`diagnostics` JSON
    columns — spec §2 amended), `_ab_aa_runs`, `_ab_tasks`; strictly-monotonic
    distinct `created_at` via `next_version_ts()`.
  - `abkit.config`: pydantic Experiment (primary entity; cadence
    duration-or-schedule union; sub-day gates) / Metric (type + column roles) /
    Method (delegates validation AND `method_config_id` to the stats factory —
    one hashing path; quarantined branches fail at validate time) / Project
    (statistical defaults + `max_looks`/`warn_looks`/`min_units_per_arm`) /
    Profiles (env-interpolated, lazy driver imports); the full
    declarative-config §8 level-2 validation matrix incl. the macro-usage lint
    and the peeking warnings; project-root discovery + the two-level selector.
  - `abkit.loaders`: StrictUndefined Jinja with the authoritative `ab_*`
    built-ins and the **packaged assignment macro** (`ab.exposed_units()` —
    dialect-aware cohort dedup, both window predicates, exposure filter);
    exposure loader (idempotent per experiment; unit-in-two-variants is a hard
    error) and metric loader (one-row-per-unit REJECTED on violation with the
    GROUP BY hint).
  - `abkit.pipeline` + `abkit.compute`: the v1 full-window recompute pipeline —
    lock → catalog → exposures once → SRM gate (blocking-but-non-dropping,
    broadcast to every row) → per-comparison anti-join plan (Python-computed
    watermark) → analyze (declarative `input_kind`/`is_paired` dispatch;
    two-tier Bonferroni; deterministic per-row bootstrap seeds;
    `insufficient_data` demotion) → enrich (the full contract row) → LWW
    persist; worker pool across experiments; backlog + orphaned-series
    warnings.
  - `abk` CLI: `run` (validate/plan/load/compute steps, `--full-refresh
    --from/--to`, the inspectable effective-alphas echo, the red `SRM FAILED`
    gate line), `unlock`, `clean` (method_config_id drift GC + orphaned
    experiments; dry-run default), and `init` — a **runnable example**
    (z-test fraction + CUPED sample metrics, assignment SQL, a deterministic
    ClickHouse seed dataset, Prefect flow example) that round-trips through
    the real config classes and the L2 validator at scaffold time.
  - Tests: 905 (incl. an in-memory SQL-semantics fake backend, a synthetic
    warehouse that aggregates a real event log per rendered window, the
    machine-independent first-run e2e mirroring the seed generation rule, and
    a testcontainers ClickHouse e2e gate that runs where Docker is available).
- **M2 stats-core additions (zero number changes; goldens untouched):**
  `COVARIATE_LOOKBACK_PARAM` on the two CUPED methods (the lookback is
  identity-bearing — a different pre-period is a different covariate series);
  declarative `BaseMethod.input_kind`/`is_paired` capability attributes.

### Changed (M2 recorded deviations — no statistical numbers changed)
- **Jinja precedence flip vs the detectkit donor:** `ab_*` built-ins WIN over
  caller context; a colliding context key raises instead of silently moving
  the analysis window.
- **CLI exit codes:** every `abk` command exits non-zero on failure (the donor
  echoed and returned 0) — the CLI is the Prefect unit of automation.
- **CUPED covariate mechanics (declarative-config §3/§4 amended):** the
  covariate comes from a SECOND render of the same metric SQL over the fixed
  pre-period window with the exposure filter dropped (legacy semantics — the
  covariate is the same metric pre-period); the original `ab.covariate_window()`
  conditional-aggregate sketch is superseded (its own spec example would have
  double-counted the pre-period under plain `sum()`).
- `_ab_results` gains nullable `warnings`/`diagnostics` canonical-JSON columns
  (plan R7) — the stats core's human-readable failure signal is persisted, not
  lost to stderr; data-contract-and-reporting.md §2 amended in the same change.

- **M1 — the pure statistical core `abkit.stats`** (importable standalone;
  numpy/scipy/statsmodels only). Data model: `Sample` / `Fraction` /
  `RatioSample`, sufficient statistics with the exact legacy **mixed-ddof**
  convention (`np.var`→ddof=0, `np.cov`→ddof=1), `JointMoments`,
  `PairedSufficientStats`, Welford/Chan-stable merges (`accumulate`). Plugin
  method registry + factory + canonical `method_config_id`
  (sha256 over registry name + sorted non-default identity params, version
  appended only when >1; byte-exact-tested; `seed` identity-excluded).
  Closed-form methods (`t-test`, `paired-t-test`, `z-test`, `cuped-t-test`,
  `paired-cuped-t-test`, the new `ratio-delta`) with dual entry
  (`from_samples` ≡ `from_suffstats`); bootstrap family (`bootstrap`,
  `paired-bootstrap`, `poisson-bootstrap`, `paired-poisson-bootstrap`,
  `post-normed-bootstrap`, `paired-post-normed-bootstrap`) on a vectorised
  block-streaming engine with deterministic per-seed draws. Power/MDE
  (t-test, CUPED-deflated, proportions), Bonferroni (incl. the legacy
  two-tier scheme) + read-time Benjamini-Hochberg, SRM chi-square gate,
  deterministic seed derivation (`rng.derive_seed`).
- **Tests (760+):** golden tests vs an independent transcription of the legacy
  engine at rel-1e-9 (incl. the CUPED θ golden and a heavy-tailed sparse-revenue
  fixture), byte-exact identity-hash tests, bootstrap byte-stability /
  block-invariance tests, quarantine and known-answer tests
  (`ratio-delta` ≡ `t-test` at denominator ≡ 1), A/A calibration smoke.

### Changed
- Engine-hygiene fixes H1–H10 applied per
  [`statistics-changes.md` §7](docs/specs/statistics-changes.md) (M1
  implementation record): Generator-based RNG + deterministic per-row seeds,
  baseline-faithful sign p-value default with the H4 plug-in as opt-in
  `pvalue_kind: plugin`, Hamilton stratum apportionment (quorum-mandated),
  Poisson mean-only guard, H5 zero-denominator NaN+warning policy, H9
  point-estimate effect convention, named-stat registry (`register_stat`)
  replacing raw `stat_func` callables; broken legacy ratio methods quarantined
  (never silently substituted).
- Adversarial post-M1 review (8 finder angles → 30 verified findings) applied:
  registry alias-shadowing guard + reload-safe re-registration; param range /
  finiteness validation at construction (`power`, `n_samples`,
  `max_block_bytes`); `weight_method` removed from Poisson schemas and rejected
  without `stratify` (a no-op value could fork `method_config_id`);
  two-tier Bonferroni supports main-metric-only experiments; paired methods
  drive through the uniform `compare()` (a sequence of `PairedSufficientStats`
  is a list of ready comparisons); bootstrap memory cap accounts for index
  matrices + fancy-indexing temporaries; Poisson engine reuses one float64
  weight buffer; stratified planning is a single `np.unique` pass; power/MDE
  effect-size solves are LRU-cached; `TestResult.to_dict` derives from dataclass
  fields; purity of `abkit.stats` enforced by test.
- **Project initiation contract.** Architecture synthesized from the legacy
  `ab_testing` engine (statistical baseline) and detectkit (architecture / DX),
  validated by a 5-lens adversarial subagent quorum (all approve-with-changes).
  See the master plan [`docs/ru/project-initiation-spec.md`](docs/ru/project-initiation-spec.md)
  and the [specs index](docs/specs/00-overview.md): architecture, statistics
  baseline + changes + legacy method catalogue, cumulative-intervals/compute
  strategy, declarative config, data contract & reporting, A/A false-positive
  matrix, CLI & DX, branding & site, and the quorum must-fix gate.
- **Development scaffolding** (this session): packaging (`pyproject.toml`,
  `setup.py`, `MANIFEST.in`, `requirements.txt`), `pre-commit`, GitHub workflows
  (CI, publish-to-PyPI on tags, website), a minimal importable `abkit` package with
  a working `abk` CLI entry point (`abk --version`), and smoke tests.

### Decisions
- **Sub-day cumulative intervals (abk-intervals, 2026-07).** `cadence` is a true
  duration with schedule support (dense-early grids first-class); NO hard time
  floor — the hard gate is `max_looks` (look count is the dangerous variable,
  not the time unit); `data_lag` completeness watermark required below `1d`;
  window contract keyed on exclusive UTC `end_ts` with derived `end_date`
  (daily parity byte-clean); fixed-horizon sub-day = monitoring mode (readout
  still refuses pre-horizon WIN/LOSE), `sequential: always_valid` is the
  sanctioned early-decision path; early rows demoted via `insufficient_data`,
  never hidden; anytime-valid sequential SRM below `1d`; A/A peeking-FPR runs
  the actual cadence grid + gains an exaggeration-at-stop column; unit-state
  stays day-grained (sub-day reads = closed-day state + current-day tail).
  Full record: `docs/specs/cumulative-intervals.md` §6.
- **CUPED covariate window resolved to fixed lookback** (whole days, cadence-
  independent) — the legacy growing window is incoherent at sub-day grain.
  Record: `docs/specs/statistics-changes.md` §5.

### Locked decisions
- Greenfield storage (legacy dashboard is reference only); statistical math
  preserved as a baseline then improved deliberately.
- Fixed-horizon CI by default with honest cumulative-peeking FPR in `abk validate`;
  sequential (always-valid) CIs opt-in.
- ClickHouse-first; PostgreSQL/MySQL supported. Read-only exposures.

_Pre-development: no PyPI release yet. The first tagged release will populate a
versioned section here. Roadmap: [`ROADMAP.md`](ROADMAP.md)._
