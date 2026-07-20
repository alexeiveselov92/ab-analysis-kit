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

## Status: M1–M7 shipped — `0.2.0` release-ready (M1–M6 on PyPI @ `0.1.2`); polish track M7–M17 in flight

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
`ALGORITHM_VERSION` bump). The sequential side-by-side column (D8) and the composed-FDR
sweep (D9) **shipped in M5**; sidedness/winsorization stay a named future stats-core
change the harness arbitrates (D14).

**Done — M5, sequential analysis + the planner + composed corrections** (see
[ROADMAP.md](ROADMAP.md) M5 and
[m5-implementation-plan.md](docs/specs/m5-implementation-plan.md) for the record):
the opt-in always-valid **confidence sequence** (`abkit/stats/sequential/`, a pure MODE
transform over the fixed `(effect, SE)`; `ci_kind='always_valid'`; default-off byte parity
so no `ALGORITHM_VERSION` moved), the readout calling WIN/LOSE pre-horizon only under it +
the weekly-cycle chip, the **toggle self-invalidation** (a bare `abk run` re-plans the
series), the sub-day anytime-valid **multinomial SRM** (Lindon & Malek) below 1d, `abk
plan` (read-only pre-launch power/sizing, `abkit/planning/`), the A/A **D8** sequential
side-by-side peeking column, and the **D9** composed multi-metric FWER/FDR sweep (via the
shared `stats.correction.composed_significance` extracted from the readout). Adversarially
reviewed per WP + a ≥2-round exit gate; 1550+ tests incl. the sequential-matrix e2e.
**Named future deferral** (no version promise): `alpha_spending`/group-sequential. (The
A/A sequential × composed sweep and `abk plan` runtime/ASN — once M6 deferrals — shipped
in M6, WP-B / WP-A.)

**Decided** (recorded in the specs + CHANGELOG): sub-day cumulative intervals
([cumulative-intervals.md §6](docs/specs/cumulative-intervals.md)); CUPED
covariate = fixed whole-day lookback implemented as the pre-period second
render ([declarative-config.md §3](docs/specs/declarative-config.md)); Jinja
built-ins win over context; CLI exits non-zero on failure.

**Done — M6, the DX / docs / orchestration / release layer** (see
[ROADMAP.md](ROADMAP.md) M6 and [m6-implementation-plan.md](docs/specs/m6-implementation-plan.md)
for the record): `abk init-claude` + the packaged `.claude` assets (the managed
`CLAUDE.md` block, 9 operator rules, 7 skills), the single-source docs site
(Astro, live at abkit.pipelab.dev), Prefect flow/deployment scaffolding in
`abk init`, BI reference (tool-agnostic SQL recipes + one Grafana dashboard),
`abk test-report` + the `abkit/notify/` channel layer, `abk plan` **runtime/ASN**
(WP-A), the A/A **sequential × composed** family sweep (WP-B), and the release
engineering (`__version__ = 0.1.0`, classifier `3 - Alpha`, the wheel-namelist +
`pip install` DoD gates, the docs single-source drift gate) behind the WP10 exit
gate (release-readiness e2e + ≥2 adversarial rounds). **Zero statistical-number
changes across M2–M6** (no `ALGORITHM_VERSION` moved, goldens intact at rel-1e-9,
`abkit.stats` purity held). **Named future deferral** (no version promise):
`alpha_spending`/group-sequential. Released: `0.1.0` → `0.1.1` (docs fact-check)
→ `0.1.2` (explore/CLI DX polish), all on PyPI.

**Done — M7, validate vectorization + iteration policy → `0.2.0`** (see
[ROADMAP.md](ROADMAP.md) M7 and
[m7-implementation-plan.md](docs/specs/m7-implementation-plan.md) for the
record — done table, per-WP as-built notes, exit-gate log): all eight WPs
incl. the stretch — the live multi-arm Review-mode fix (WP0), the scalar hot
path + hardening bucket A1–A8 (up to ~149× on `normal_test`; WP1), the opt-in
batch kernels (`supports_vectorized`/`from_suffstats_array`, bit-exact vs the
scalar path via `_libm_pow`, 5 methods; WP2), the block-streamed
`vector_resample` engine (masks bit-identical to `placebo_mask` by
construction; WP3), the `score_cell` dispatcher with verbatim scalar fallback
(~10×/cell; WP4), the exhaustive parity + executable perf gates (WP5), the
vectorized family sweep (~18×; stretch WP7), and the WP6 policy —
`--family-sweep` opt-in + per-cell auto-N `max(2000, ⌈200/α⌉)`,
warn-never-cap above 100k. **Zero statistical numbers moved** (no
`ALGORITHM_VERSION` bump; both e2e matrix gates byte-identical; the two
documented engine-parity boundaries — fixed-BLAS byte-repro, the
exactly-solved-boundary flip — are test-pinned properties). Release-ready as
`0.2.0` — version bumped, CHANGELOG cut; the `v0.2.0` tag/publish is the
maintainer's pending G1 step (latest on PyPI is still `0.1.2`).

**Next — the polish track continues: M8–M17 → `0.3.0`…`0.12.0` (track
approved 2026-07-18).** The code-verified pain audit
([docs/research/2026-07-data-flow-audit/REPORT.md](docs/research/2026-07-data-flow-audit/REPORT.md))
plus the entire hardening backlog, one minor release per milestone: M8
assignment no-copy default → M9 additive engine + CUPED Tier-E → M10
timestamps + both schema breaks → M11 `abk dashboard` → M12 notifications →
M13–M17 (versioned stats, multi-arm decisions, new methods, owned
randomization, app integration — contours, design-session-first). The
track section in [ROADMAP.md](ROADMAP.md) is the map; the as-designed contracts
are [m8](docs/specs/m8-implementation-plan.md)–[m12](docs/specs/m12-implementation-plan.md)
implementation plans ([m7](docs/specs/m7-implementation-plan.md) is now the
implementation record). Discipline: one WP = one session = one PR; **M7–M12
move no statistical number** (parity gates); M13/M15 go through full change
control.

Design contracts stay in [docs/specs/](docs/specs/) (canonical). Read the relevant
spec before writing code:

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

> The contributor condensation lives in `.claude/rules/` (see the routing
> table above); `docs/specs/` stays canonical for design contracts. The
> *user-facing* `init-claude` payload (`abkit/cli/assets/claude/`) + the docs
> site now ship — keep all three (`docs/`, `.claude/rules/`, the packaged
> init-claude assets) telling one story on every release, detectkit-style.

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
