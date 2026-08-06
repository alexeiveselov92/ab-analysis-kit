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

## Status: M1–M13 shipped; latest on PyPI: `0.8.0` (M13 = versioned statistics, all opt-in); BOTH `0.6.x` interstitials closed (PLAN-1/PLAN-2 in `0.6.1`/`0.6.2`, UI-1 + UI-2 + PERF-1 in `0.6.4`); polish track continues with M14–M17 (**M14 IN PROGRESS — DEC-1…DEC-4 merged; DEC-5 and the DEC-6 exit gate remain**)

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
exactly-solved-boundary flip — are test-pinned properties). **Released as
`0.2.0`** — tagged `v0.2.0` and published to PyPI (the latest release).

**Done — M8, assignments: no-copy default + incremental copy → `0.3.0`** (the
implementation record is
[m8-implementation-plan.md](docs/specs/m8-implementation-plan.md) — done
table, per-WP as-built notes, exit-gate log; PRs #46–#51 + the WP7
docs-sync/release PR): by default (`assignment.cohort_copy.enabled: false`)
every cohort reader (`abk run`/`plan`/`validate`/`explore`, reporting) goes
through the one `build_cohort_backend` switch and reads the **live**
assignment SQL — `_ab_exposures` is never written; opt-in
`cohort_copy.enabled: true` persists it via the append-only incremental
engine (grid-anchored closed-interval batches, watermark resume, `abk run
--resync-cohort` for a full rebuild; late-backfilled rows are a documented
copy-mode limitation). The cross-command parity e2e pins `_ab_results`
identical across modes — **zero statistical numbers moved** (no
`ALGORITHM_VERSION` bump). WP7 synced all three docs bodies (a code-grounded
audit fixed 75 stale spots across 36 files) and cut `0.3.0`. **Released as
`0.3.0`** — tagged and published to PyPI.

**Done — M9, the additive compute engine + CUPED Tier-E → `0.4.0`** (the
implementation record is
[m9-implementation-plan.md](docs/specs/m9-implementation-plan.md) — done
table, per-WP as-built notes, exit-gate log; PRs #53–#56, #58 + the WP6
exit-gate PR): the O(D²) full-window rescan is gone for declared-additive
closed-form metrics — a write-only **STATE stage** materializes per-(unit,
day) moments into `_ab_unit_state` through the M8 cohort factory (WP3), the
opt-in **`compute.incremental_reads`** path sums those closed days plus a
live sub-day tail into the UNCHANGED `SufficientStats` pipeline with a
fall-back-never-undercount gap check (WP4), `abk verify-incremental`
reconciles both backends over the whole series at rel-1e-9 with a non-zero
exit (WP5, plus `abk run --cost-report`, the `abk clean` state sweep and the
executable perf gate: `N·D(D+1)/2` → `N·D` fact rows scanned, zero inside
COMPUTE at daily cadence), and CUPED became **Tier E** in explore off four
newly persisted covariate moments added by the additive `ensure_columns`
migration primitive (WP1–WP2). **Day-additivity is an explicit metric
declaration** (`state_additive: true`, default off): three review rounds each
defeated a textual additivity check with a new SQL shape, so the text check
is veto-only and `verify-incremental` is the empirical oracle — it caught the
scaffolded `example_signup_cr` (`max()` + a literal trial count) inflating
`size_1` 11×. `incremental_reads` stayed **default off** with the flip
criteria in [cumulative-intervals.md §4.1](docs/specs/cumulative-intervals.md);
PERF-1 has since run them (§4.2) — `abk init` now scaffolds it ON, the library
default stays off.
**Zero statistical numbers moved** (the flag on/off parity gate is the
milestone's №1 assertion; no `ALGORITHM_VERSION` bump). **Released as
`0.4.0`** — tagged and published to PyPI.

**Done — M10, timestamps + both schema breaks + explore polish → `0.5.0`**
(the implementation record is
[m10-implementation-plan.md](docs/specs/m10-implementation-plan.md) — done
table, per-WP as-built notes, §3 exit-gate record, §6 review log; PRs
#61–#64 + the exit-gate PR): an experiment's window is a pair of real
instants and the config keys say so — **`start_date`/`end_date` are renamed
`start_ts`/`horizon_ts`** with no aliases, both accept a bare date **or** a
full timestamp, and a bare date is local midnight of THAT day for **both**
edges, so `horizon_ts` is the EXCLUSIVE right edge (port `end_date:
2024-07-14` as `horizon_ts: 2024-07-15`; the rename error spells it out). The
new **`interval_anchor`** knob decides where the cutoff lattice sits
(`midnight` | `start` | an explicit instant; cutoffs are `anchor + k·cadence`
kept strictly after the start), and `ExperimentConfig.grid()` is now the ONE
factory composing window + cadence + anchor — an AST gate forbids calling
`generate_grid` from anywhere else, because the knob had reached none of the
eight hand-copied call sites (WP1–WP2). **Both breaking schema changes of the
whole track land here, with one recreate guide**: `_ab_results.start_date`/
`end_date` are dropped (BI groups by `end_ts`; the calendar day a look covers
is `end_ts − 1µs` read in the experiment timezone — a tested contract, not
just a doc) and the `_ab_experiments` window is renamed + widened to
`DateTime64(3)` holding the RESOLVED window in naive UTC, plus an
`interval_anchor` column (WP2–WP3). In `abk explore`, `heavy_lock` now guards
only `/reload`/`/validate`/`/apply` so a knob turn answers while Auto mode
runs — with superseded work CANCELLED (`should_stop=` between points, 409
`{stale: true}`) rather than queued, a 2-slot admission semaphore, and
thread-scoped warning capture (`utils/warn_scope.py` — `catch_warnings` is
process-global) (WP4); and dragging alpha over a bootstrap series stops
redrawing the replicates — the draw is memoized per (metric, arm pair,
cutoff, cache generation, method, resolved params), alpha excluded, measured
6.01 s → 1.01 s over six turns (WP5). **Zero statistical numbers moved** (no
`ALGORITHM_VERSION` bump; the exit gate's golden was captured from the
pre-M10 code itself). One derived number legitimately did:
`horizon_seconds()` is true elapsed time rather than a nominal day count, so it
differs from the old value by exactly the window's UTC-offset change — ±30 min,
±1h, ±2h or ±24h depending on the zone, and it fires for a permanent zone shift
with no DST involved. It now agrees with its own grid (which pre-m10 it
contradicted), and no persisted column derives from it. **Released as
`0.5.0`** — tagged and published to PyPI.

**Done — M11, `abk dashboard` — the project-level cockpit → `0.6.0`** (the
implementation record is
[m11-implementation-plan.md](docs/specs/m11-implementation-plan.md) — done
table, per-WP as-built notes, exit-gate log; PRs #66, #68, #69, #71–#75 +
the docs-only decisions PR #67): the whole selection as **one row per
experiment** — headline verdict, effect + CI, p/α, elapsed, a canvas
sparkline of the cumulative series — with buttons that spawn **real `abk`
subprocesses** and stream their logs. The binding invariant is that the
dashboard is a **launcher, never a worker**: no route computes a statistic or
takes the pipeline lock (an AST gate over the module *plus* a spy over every
job route; UI-1 dropped the never-gated "writes a config" clause and extended
the spy to the editor routes), so every verdict on the page
is `readout.evaluate()`'s — the same one `abk run --report` bakes, over the
FULL cumulative series (`?window=` bounds only the sparkline's x-range;
truncating the left edge would read a 14-day WIN as INCONCLUSIVE, because
`_ab_results` rows are cumulative looks from a fixed start, not a plain time
series). Shipped as `abkit/tuning/jobs.py` (the subprocess registry, DASH-1),
`overview.py` (the row shaper, DASH-2), `dashboard_server.py` (the stdlib
localhost server, DASH-3 + the job routes DASH-4), the third committed client
bundle `assets/dashboard.js` from `web/src/dashboard/` (DASH-5), and the
`abk dashboard` command + docs + the two hardcoded wheel-namelist gates
(DASH-6), behind the DASH-7 exit gate
([tests/e2e/test_dashboard_session.py]) — a real server over live HTTP, a
real `abk` child through `/api/run`, three distinct row states in one list,
and a whole-session spy proving no pipeline lock is ever taken. The pipeline
gained the one capability a per-metric Run button needs: **`abk run --metric
<m>`** (DASH-4a), whose alphas are invariant by construction
(`effective_alphas()` reads the CONFIG, not the run) and which **truncates**
the withheld metrics' day state rather than leaving a stale-but-contiguous
`_ab_unit_state` the M9 gap check cannot see. **Zero statistical numbers
changed** (no `ALGORITHM_VERSION` bump). CRUD config editing was explicitly
phase 2 — it shipped in the `0.6.x` **UI-1** interstitial. **Released as
`0.6.0`** — tagged and published to PyPI.

**Done — M12, notifications → `0.7.0`** (the implementation record is
[m12-implementation-plan.md](docs/specs/m12-implementation-plan.md) — the §1
per-WP as-built notes, the §4 exit-gate result and the §7 record; PRs #89–#94):
`abkit/notify/` (shipped in M6, reachable only through `abk test-report`) is
wired to real signals behind the opt-in `abk run --notify` and
`abk validate --notify`. **Nothing in a message is recomputed** — a verdict is
`readout.evaluate()`'s over the persisted rows, the same decision `--report`
bakes, so a notification cannot disagree with the report or the dashboard about
the same experiment (the m11 launcher discipline, applied to a second surface).
Six routable signal kinds: `readout`, its narrow views `verdict_change` and
`srm` (one payload re-CLASSIFIED, never a second message), `error` (a failed
run — no statistics block, because nothing was measured), and the two RECURRING
ones, `stale` and `calibration_red`, whose condition outlives the run that
reports it and which therefore dedup on a SIGNATURE of what is wrong plus an
optional `notify.cooldown_seconds`. Routing is two `on:` filters that INTERSECT
(the experiment's and the channel's), and `_ab_notify_states` remembers what was
last ANNOUNCED — written only after a channel accepted it — so a scheduled run
is quiet until something moves. Nine channel types (NTF-4 added discord, teams,
googlechat, ntfy). **Fail-soft is the binding property**, doubled on purpose and
proven at the exit gate: no channel failure can change an exit code, and one bad
channel cannot block the rest. NTF-5's routing work also fixed the detector it
routed — the backlog warning measured against the ever-advancing watermark, so a
FINISHED, fully computed experiment reported a backlog growing by a day every
day. **Zero statistical numbers moved** (no `ALGORITHM_VERSION` bump).

**Done — M13, versioned statistical improvements → `0.8.0`**: STAT-1c (guardrails
uncorrected), STAT-2 (the false-positive sign instrument), STAT-1b (the declared
contrast set), STAT-1 (Holm + the precise FWER claim), STAT-3a (the
`asymmetric_ci` guard), STAT-3 (the score proportion interval), STAT-4 (the
Fieller relative interval) and STAT-6 (the exit gate + the batch A/A
revalidation) are merged; STAT-5 (uniform ddof) was dropped by D13. **The
milestone moves numbers but no DEFAULT** — every new estimator and scheme is
opt-in, no `ALGORITHM_VERSION` was bumped, and a project that writes nothing new
reproduces `0.7.0` row for row (proved against a real `v0.7.0` checkout, not
against HEAD). Record:
[m13-implementation-plan.md](docs/specs/m13-implementation-plan.md).
**Released as `0.8.0`** — tagged and published to PyPI.

**Next — M14–M17 → `0.9.0`…`0.12.0` (track approved 2026-07-18)**. The `0.6.x` **PLAN-1/PLAN-2** interstitial is
closed (released as `0.6.1`/`0.6.2`); the second `0.6.x` interstitial —
**UI-1/UI-2/PERF-1**, added 2026-08-02 — is closed too: **UI-1** (CRUD
YAML editing in `abk dashboard`, `abkit/tuning/config_files.py` — validate both
levels → archive byte-verbatim → atomic write, with the boot snapshot replaced
by a re-resolution seam), **UI-2** (`abk ui`) and **PERF-1** (the M9 additive
read path made discoverable — `abk run` warns about an undecided
`compute.incremental_reads` when a metric is day-additive and the series has
reached six looks, `--cost-report` prints the measured day-additive slice of
COMPUTE beside the counterfactual, and `abk init` now scaffolds
`incremental_reads: true`; the library default stays `false` because the flag
guards the operator's ingestion SLA, and §4.1's criteria were finally executed
— evidence in [cumulative-intervals.md §4.2](docs/specs/cumulative-intervals.md))
shipped together as ONE **`0.6.4`** — tagged and published to PyPI, and M12
followed as **`0.7.0`**. The
interstitial renumbers nothing either. Metric-YAML editing
(UI-3) is deliberately deferred past M12 — a metric edit needs its `sql/` file
too, which is new surface rather than an addendum. The code-verified pain audit
([docs/research/2026-07-data-flow-audit/REPORT.md](docs/research/2026-07-data-flow-audit/REPORT.md))
plus the entire hardening backlog, one minor release per milestone: M12
notifications shipped as `0.7.0` and M13 (versioned stats) as `0.8.0`;
**M14 (the multi-arm decision layer → `0.9.0`) is IN PROGRESS against its
contract [m14-implementation-plan.md](docs/specs/m14-implementation-plan.md):
six WPs (DEC-1…DEC-6), ten decisions, and the posture that it moves NO
persisted number, no alpha and no verdict `0.8.0` already issues, with a
two-arm experiment byte-identical on every surface. **✅ DEC-1** shipped
`assignment.control` — the declared baseline — behind the one AST-gated
resolver `ExperimentConfig.control`/`.treatments`, with `contrast_pairs()`
orienting (never resizing) the family, the seven positional sites rerouted
(one of which, the SRM rollup, had failed *silently*), the additive
`_ab_experiments.control` column, and a config-lint warning for the
re-orientation an experiment with existing rows pays for. **✅ DEC-2** shipped
the decision layer — a verdict for every declared pair behind the new
`PairVerdict.role`, and a `MetricRollup` per main metric whose leader is chosen
only among arms that beat the control and whose separation is tested against
every other treatment, not the runner-up. **✅ DEC-3** put it on the report:
the payload carries every declared pair plus the rollups, and the page grows a
role chip, a cross-arm overview and a pair selector — all gated at 3+ arms, so a
two-arm readout renders the DOM `0.8.0` rendered. Opening the payload opened
THREE readers, so `abk explore` and the `abk run --report` summary line were
held at control-anchored (one shared `ship_decisions`). **✅ DEC-4** released
both and finished the layer: the dashboard row's headline is the first declared
main metric's ROLLUP LEADER rather than an arbitrary arm, explore's Review mode
labels the role and names the leader, the CLI line names it per metric, and the
notification dedup signature gained the rollup identity so a leader flip with no
verdict word moving is no longer silent — with notifications themselves staying
control-anchored by decision (D7)**; M15–M17 (new methods, owned
randomization, app integration) stay contours, design-session-first. The
track section in [ROADMAP.md](ROADMAP.md) is the map;
[m7](docs/specs/m7-implementation-plan.md),
[m8](docs/specs/m8-implementation-plan.md),
[m9](docs/specs/m9-implementation-plan.md),
[m10](docs/specs/m10-implementation-plan.md),
[m11](docs/specs/m11-implementation-plan.md),
[m12](docs/specs/m12-implementation-plan.md) and
[m13](docs/specs/m13-implementation-plan.md) are all implementation records now.
Discipline: one WP = one session = one PR; **M7–M12
moved no statistical number** (parity gates) and **M13 moved no DEFAULT** (the
new estimators and schemes are opt-in; the byte-compatibility gate is
[tests/e2e/test_m13_exit_gate.py](tests/e2e/test_m13_exit_gate.py)); M13/M15 go
through full change control.

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
| `abk dashboard` — the launcher discipline, the row shape, the job routes | [docs/specs/m11-implementation-plan.md](docs/specs/m11-implementation-plan.md) |
| **The multi-arm decision layer being built now** (✅ `control:`, ✅ the verdicts + the rollup, ✅ the report, ✅ the other surfaces; next: DEC-5 validate·plan·SRM) | [docs/specs/m14-implementation-plan.md](docs/specs/m14-implementation-plan.md) |
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
