# ab-analysis-kit — contributor & AI-assistant guide

**ab-analysis-kit** (CLI `abk`; Python import package `abkit`) is a Python library + CLI for **A/B experiment
analysis**: declarative YAML + SQL run through a `load → compute → readout`
pipeline. It is detectkit's sibling — same DNA (numpy-first, db-agnostic
[ClickHouse-first; PostgreSQL/MySQL], CLI-first, AI-native onboarding, self-contained
reports, a chart-first cockpit), with the `detect` stage replaced by a statistical
`compute` stage and the primary entity flipped from *metric* to *experiment*.

> **Using abkit, not hacking on it?** (Once shipped:) see the README and
> `abk init-claude`, which sets up assistant context inside *your own* project.

## Working context lives in `.claude/rules/`

The as-built condensation for contributors/assistants (detectkit-style):

| If you're… | Read |
|---|---|
| Touching code — the system **as it exists** (stats-core API, gotchas, layout) | [.claude/rules/architecture.md](.claude/rules/architecture.md) |
| Setting up, testing, adding a method, changing a number, porting from detectkit, releasing | [.claude/rules/contributing.md](.claude/rules/contributing.md) |

Design contracts for what is being *built next* stay in [docs/specs/](docs/specs/)
(canonical for M2+ work — table below). Keep rules ↔ docs in sync per milestone.

## Status: M1 + M2 + M3 + M4 shipped; next up M5

**Done — M1, the pure statistical core** (`abkit.stats`, importable standalone;
see [ROADMAP.md](ROADMAP.md) for the deferred-cleanup list): data model with the
legacy mixed-ddof convention, plugin registry + canonical `method_config_id`,
6 closed-form + 6 bootstrap methods with dual entry, power/MDE, Bonferroni + BH,
SRM gate, deterministic seeds; 520+ tests incl. golden tests vs an independent
legacy transcription at rel-1e-9. Adversarially reviewed (8 angles, 30 verified
findings fixed or recorded).

**Done — M2, declarative config + DB layer + recompute pipeline** (see
[ROADMAP.md](ROADMAP.md) M2 for the DoD and recorded deferrals, and
[m2-implementation-plan.md](docs/specs/m2-implementation-plan.md) for the
implementation record): pydantic configs + the §8 validation matrix, generic
CH/PG/MySQL managers with the atomic lock, the greenfield `_ab_*` schema, the
packaged assignment macro, the one-enumeration period planner
(scalar/schedule cadence, `data_lag` watermark), the recompute pipeline
(SRM gate, two-tier alphas, deterministic bootstrap seeds, demotion), and the
`abk` CLI (`init` with a runnable seed example, `run`, `unlock`, `clean`);
900+ tests incl. a first-run e2e gate.

**Done — M3, the explore cockpit + reporting (the PRIORITY interface)** (see
[ROADMAP.md](ROADMAP.md) M3 and
[m3-implementation-plan.md](docs/specs/m3-implementation-plan.md) §5 for the
record): the readout core + WIN/LOSE/FLAT/INCONCLUSIVE verdicts, the §5.3
terse experiment payload, the self-contained HTML readout
(`abk run --report`), and the explore cockpit (`abk explore` — localhost
server, Tiers E/α/S/R recompute over a bounded session cache, the D3
calibration gate with `confirm_uncalibrated`, the Apply seam with `.history`
archives + orphan detection, the browser client with the donor's stale-drop
discipline), plus the `web/` TS toolchain with committed wheel-shipped
bundles and CI freshness/marker/token gates; 1250+ tests incl. the report and
explore-session e2e gates. Deferred: WP9 testcontainers hardening (needs
Docker), D9 Segment mode, D12 sidedness/winsorization (M4 change control).

**Done — M4, `abk validate` — the A/A false-positive matrix** (see
[ROADMAP.md](ROADMAP.md) M4 and
[m4-implementation-plan.md](docs/specs/m4-implementation-plan.md) for the
record, incl. the §5 adversarial-review log): the pure `abkit/validate/` engine
(placebo label-permutation splits over the experiment's own pooled cohort;
single-look + honest cumulative-**peeking** FPR — the optional-stopping hazard,
not the readout's stabilized defense; power/achieved-MDE/coverage/exaggeration),
`_ab_aa_runs` persistence (per-cell `run_id`, effective two-tier alphas), the
recommendation + plain-language verdicts + budget-band matrix UX, the `abk
validate` CLI (own out-of-band lock, non-zero exit, `--report` reusing the
committed report bundle), the `metric.aa_fpr_budget` override, and **Auto mode**
(server-side `POST /validate` greens the live explore chip in place). The
exit-gate e2e proves the three classic failures in Binomial bands
([tests/e2e/test_validate_matrix.py]); zero method-math changes (no
`ALGORITHM_VERSION` bump). Deferred to M5: the sequential side-by-side column
(D8) and the full composed-FDR sweep (D9); sidedness/winsorization stay a named
future stats-core change the harness arbitrates (D14).

**Decided** (recorded in the specs + CHANGELOG): sub-day cumulative intervals
([cumulative-intervals.md §6](docs/specs/cumulative-intervals.md)); CUPED
covariate = fixed whole-day lookback implemented as the pre-period second
render ([declarative-config.md §3](docs/specs/declarative-config.md)); Jinja
built-ins win over context; CLI exits non-zero on failure.

**Next — M5** (sequential analysis + the planner + composed corrections: `stats/
sequential/` mSPRT/alpha-spending, `abk plan`, the A/A matrix's sequential
side-by-side column + the full composed-FDR sweep deferred from M4). The source of
truth is [docs/specs/](docs/specs/). Read the relevant spec before writing code:

| If you're working on… | Read |
|---|---|
| Module map, pipeline, the chosen architecture, key decisions | [docs/specs/architecture.md](docs/specs/architecture.md) |
| The statistical engine (the math to reproduce) | [docs/specs/statistics-baseline.md](docs/specs/statistics-baseline.md) + [../reference/legacy-method-catalogue.md](docs/reference/legacy-method-catalogue.md) |
| Deliberate deviations / new methods / the rederivation process | [docs/specs/statistics-changes.md](docs/specs/statistics-changes.md) |
| Cumulative windows, compute strategy, incremental v2 | [docs/specs/cumulative-intervals.md](docs/specs/cumulative-intervals.md) |
| YAML/SQL config, the assignment macro, `method_config_id`, validation | [docs/specs/declarative-config.md](docs/specs/declarative-config.md) |
| The results contract, decision logic, reporting, explore, BI | [docs/specs/data-contract-and-reporting.md](docs/specs/data-contract-and-reporting.md) |
| The A/A FPR matrix (`abk validate`) | [docs/specs/aa-false-positive-matrix.md](docs/specs/aa-false-positive-matrix.md) |
| CLI, explore cockpit, init-claude, Prefect, docs | [docs/specs/cli-and-dx.md](docs/specs/cli-and-dx.md) |
| **What must be true before/after each milestone** | [docs/specs/quorum-review.md](docs/specs/quorum-review.md) (the must-fix gate) |

The master plan in Russian: [docs/ru/project-initiation-spec.md](docs/ru/project-initiation-spec.md).
Reference material (legacy dashboard JSON, results chart, method catalogue):
[docs/reference/](docs/reference/).

> The contributor condensation now lives in `.claude/rules/` (see the routing
> table above); `docs/specs/` stays canonical for design contracts. The
> *user-facing* `init-claude` payload + docs site render arrive in M6 — keep all
> three in sync from then on, detectkit-style.

## Invariants (do not violate)

- **`abkit.stats` is pure** — numpy/scipy/statsmodels only; never config/DB/Jinja/click.
  (Sole intra-package dependency: the stdlib-only `abkit.utils.json_utils`
  canonical-hash path; enforced by `tests/stats/test_purity.py`.)
- **Never change a number silently** — every deviation from the baseline is an
  `ALGORITHM_VERSION` bump + a `statistics-changes.md` entry + A/A validation.
- **Methods are plugins** — a new estimator is one `BaseMethod` class + registry
  entry; the pipeline/DB/CLI never special-case a method name.
- **The DB manager stays generic** — `table_name`-keyed; `_ab_*` semantics live in
  `internal_tables/`, never in the base manager.
- **Greenfield storage** — we do **not** copy the legacy `marts.*` schema; the legacy
  dashboard is reference only.
- **Renderer stays framework-free** — baked payload + self-contained JS (so it can
  embed in a future app).
- **Keep `init-claude` assets in sync on release** with `docs/` and `__version__`.

## Quick reference (planned)

- **Tests:** `python3 -m pytest tests/` (golden / stats / aa / e2e).
- **Lint/format/types:** `pre-commit run --all-files`.
- `__version__` in `abkit/__init__.py`; `CHANGELOG.md` authoritative for behavior.
- The math reproduces a captured baseline first (golden-tested vs the legacy
  *engine* at rel-1e-9), then improves it via the documented process.

Repo (planned): https://github.com/<org>/ab-analysis-kit · Docs: abkit.pipelab.dev
