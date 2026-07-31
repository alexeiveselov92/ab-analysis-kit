# M11 Implementation Plan — abk dashboard (the flagship overview UI)

> **Status: as-designed contract for M11** (polish track approved 2026-07-18,
> [ROADMAP.md](../../ROADMAP.md) "The polish track — M7–M17"). Targets release
> **`0.6.0`**. **Not yet implemented** — this doc is the contract the
> implementation sessions execute, in the shape of
> [m6-implementation-plan.md](m6-implementation-plan.md) /
> [m4-implementation-plan.md](m4-implementation-plan.md). It becomes the
> implementation record at the DASH-7 exit gate (the m4–m6 pattern: WPs get
> ticked off, an adversarial-review record is appended, nothing here is
> retracted). It must never be read as claiming any of `abkit/tuning/jobs.py`,
> `overview.py`, `dashboard_server.py`, `dashboard.ts`, or `abk dashboard`
> exist yet — they don't.
>
> Governing specs: [cli-and-dx.md](cli-and-dx.md) (the CLI surface + skill
> conventions the new `abk dashboard` command joins),
> [data-contract-and-reporting.md](data-contract-and-reporting.md) (the
> results contract + `readout.evaluate()` verdict source DASH-2 must reuse,
> never re-derive), [branding-and-site.md](branding-and-site.md) (the
> `--abk-*` token layer DASH-5's verdict chip must reuse, never introduce new
> hex), and [ROADMAP.md](../../ROADMAP.md) M11.
> Sibling milestone docs: [m10-implementation-plan.md](m10-implementation-plan.md)
> (DASH-3 clones `tuning/server.py` **after** M10 WP4 — see §0.5(e)),
> [m12-implementation-plan.md](m12-implementation-plan.md) (the NTF-* work
> packages from the same source design — **out of scope here**, cross-
> referenced not restated).
>
> Canonical detailed WP breakdown:
> `~/.claude/plans/abkit-v2-details/design_ui_notify.json` (DASH-1..7 +
> NTF-1..6 in one shared design doc — this file represents only the DASH-*
> packages in full fidelity). Code-verified facts:
> `~/.claude/plans/abkit-v2-details/verify_ui_notify.json`. Donor:
> `/home/aleksei/wsl_analytics/detektkit`, package `detectkit`, the `ui/`
> subpackage (`jobs.py` 335 lines, `overview.py` 600 lines, `html.py` 84
> lines, `metric_files.py` 271 lines, `server.py` 1215 lines, ~2295 lines
> total incl. the compiled `assets/ui.js`).

## 0. Scope, posture & decisions

### 0.1 Posture: zero statistical-number changes

**M11 is a UI/DX milestone. It reads persisted rows; it computes nothing.**
Per the track-wide M7–M12 posture
([ROADMAP.md](../../ROADMAP.md) "The polish track"), no `ALGORITHM_VERSION`
bump, no golden retolerancing, `abkit.stats` purity untouched
(`tests/stats/test_purity.py` stays green with no new import). Every
dashboard verdict is sourced through the **already-shipped**
`abkit.pipeline.readout.evaluate()` — the exact function the HTML report and
explore session already call — never through a re-implementation, a
recomputation, or a shortcut over raw `_ab_results` rows. DASH-7's exit gate
adds an explicit assertion of this (no numeric divergence from what
`abk run --report` would show). **Not "for the same window"** — DASH-2's
as-built settles that the window preset scopes the sparkline only and never
the verdict, precisely because `abk run --report` passes no window at all;
DASH-2 already pins the row's headline cells against `build_report_payload`'s,
and DASH-7 extends that through the HTTP layer.

### 0.2 Reuse surface — what already exists (do not rebuild)

M11 clones and extends existing abkit infrastructure; it does not re-invent a
server or a bake pipeline from scratch. Verified reuse surface
(`verify_ui_notify.json`):

| Existing piece | Location | Reused by |
|---|---|---|
| Token-gated POST + `request_id` stale-drop skeleton | `abkit/tuning/server.py:1-40` | DASH-3 (server skeleton clone) |
| Payload bake — one-pass regex substitution, `</>`→`&lt;` escaping, committed-bundle read via `importlib.resources` | `abkit/tuning/html.py:1-84` (`render_explore_html`) | DASH-3 (`render_dashboard_html`) |
| `load_results(experiment, metric, method_config_id)` | `abkit/database/internal_tables/_results.py:199-230` | DASH-2 (`overview.py`) |
| `select_experiments(project_root, select, exclude)` | `abkit/config/discovery.py:129-150` | DASH-4 (`/api/explore` selector), DASH-6 (`abk dashboard` CLI) |
| `web/build.mjs` `BUNDLES` array (2 entries today: `report.ts`, `explore.ts`, each `{entry, outFile, global, markers}`) | `web/build.mjs:1-58` | DASH-6 (3rd entry) |
| `TOKEN_FALLBACKS` brand-hex layer (`--abk-page`, `--abk-ink`, `--abk-st-good/warn/serious/critical`, …) | `web/src/shared/chart.ts:37-57` | DASH-5 (verdict chip colors, no new hex) |
| `readout.evaluate()`, `PairVerdict`, `ExperimentReadout` | `abkit/pipeline/readout.py` | DASH-2 — the **only** verdict source |
| One-experiment-per-`serve_explore` contract | `abkit/tuning/server.py` | DASH-2's row-per-experiment grain decision (§3) |

### 0.3 What does not exist yet (the M11 build)

`abkit/tuning/jobs.py`, `abkit/tuning/overview.py`,
`abkit/tuning/dashboard_server.py`, `render_dashboard_html` in
`abkit/tuning/html.py`, `web/src/dashboard/{dashboard.ts,payload.ts}`,
`abkit/cli/commands/dashboard.py`, the registered `abk dashboard` command, and
`docs/guides/dashboard.md`. None of these are stubbed today.

*(Progress against that list, as of PR #74: everything above exists. What is
left of the milestone is DASH-7 — the e2e session gate
`tests/e2e/test_dashboard_session.py`, the two adversarial review rounds, and
this file's amendment into the implementation record.)*

### 0.4 Scope: DASH-1..7 only

This document covers **only** the DASH-* work packages (DASH-1 through
DASH-7) from the shared design JSON. The NTF-* work packages (wiring
`abkit/notify/` to real pipeline signals + 4 new channels) are a separate,
file-disjoint track that belongs to **M12** — see
[m12-implementation-plan.md](m12-implementation-plan.md). The two tracks share
no files and can proceed in parallel across two sessions/contributors (design
JSON `dependencies`); this doc does not restate NTF-* content.

### 0.5 Plan-review record (milestone-specific corrections)

The source REPORT/plan language needed seven corrections once checked against
the actual donor + abkit code (`verify_ui_notify.json`). Carry all seven into
implementation — getting any of them backwards reproduces exactly the bug the
correction exists to prevent:

| # | Naive reading | Verified reality | Consequence for M11 |
|---|---|---|---|
| a | The donor ships TypeScript sources for its dashboard client to port | Donor's `dtk ui` cockpit has **no committed TS sources** — only a minified, 114941-byte `detektkit/detectkit/ui/assets/ui.js`; the *website's* `src/scripts/ui/*` run-panel scripts are unrelated docs-site code, not the dashboard's source | `dashboard.ts` (DASH-5) is authored **from scratch** against donor *patterns* + abkit's own `web/src/shared/chart.ts` primitives — not a line-for-line port |
| b | "2 deltas from dtk" means abkit's dashboard server diverges from the donor's own dashboard server | The donor's `dtk-ui` server (`detektkit/detectkit/ui/server.py:1-8,271-280,1174-1215`) **already** gates every route incl. GET and never self-shuts-down — REPORT's phrasing was measured against the wrong donor file | The two deltas are against the **dtk-tune** pattern (`detektkit/detectkit/tuning/server.py:1-19,112-124,201`) that `abkit/tuning/server.py` currently mirrors (unauthenticated GET at `abkit/tuning/server.py:147-165`, self-shutdown via `threading.Thread(target=srv.shutdown,...)` at `abkit/tuning/server.py:399`). DASH-3's module docstring + tests must say so explicitly — "delta from `abkit/tuning/server.py` (the dtk-tune pattern), not from dtk-ui, which already behaves this way" |
| c | The lazy per-row stats loader is a server-side thread pool | It's pure client-side JS concurrency: the compiled donor bundle hardcodes `Vn=3` and runs `Math.min(Vn, N.length)` parallel fetch loops via `Promise.all` (`detektkit/detectkit/ui/assets/ui.js:1`); no `ThreadPoolExecutor`/`max_workers` exists anywhere in `server.py`/`overview.py` | DASH-5's "≤3 concurrent" test asserts via an in-flight counter on a fake `fetch`, **never** via timing; a Python-side worker pool would be over-engineering relative to the donor |
| d | The dashboard server takes the pipeline lock, like `abk run` | The dashboard is a **launcher, never a worker**: no route in DASH-2/DASH-3/DASH-4 calls `InternalTablesManager.acquire_lock`/`release_lock` — only the **spawned** `abk` subprocess (its own OS process) ever takes the pipeline lock; verdicts flow from `readout.evaluate()`, never `build_report_payload`/`load_session` | DASH-4 and DASH-7 both add an explicit spy/monkeypatch test asserting `acquire_lock` is never called from `dashboard_server.py` |
| e | DASH-3's server skeleton can clone the *current* `tuning/server.py` | M11 is scheduled to start **after M10 WP4**, which decouples `tuning/server.py`'s lock model (`heavy_lock` scoped to reload/validate/apply only, `/recompute` free — [ROADMAP.md](../../ROADMAP.md) "Inter-milestone contracts") | DASH-3 clones the **post-M10-WP4** shape of `tuning/server.py`, inheriting the decoupled lock model; cloning the pre-M10 file would drag a stale lock pattern into a brand-new server |
| f | Wiring the 3rd bundle needs no CI edits at all (or: needs whole new gates) | Split verdict: the marker-grep and hex-containment gates already iterate `abkit/*/assets/*.js` generically, and `render_dashboard_html` reuses the exact `_FAVICON` constant `render_explore_html` already uses (no new hex) — those two need zero edits. **But the wheel packaging-DoD job hardcodes the bundle namelist as a literal tuple** `("abkit/reporting/assets/report.js", "abkit/tuning/assets/explore.js")` (`ci.yml:297`) — NOT a glob — so `dashboard.js` will not be asserted as wheel-shipped without editing that line | DASH-6 **verifies** (does not assume) the glob-based gates cover the new `dashboard.js` path, and **edits the hardcoded wheel-namelist tuple** to add `abkit/tuning/assets/dashboard.js` (step 3a); chip colors reuse `--abk-st-*` tokens + the 3 existing marker classes (`abk-prehorizon`/`abk-insufficient`/`abk-srm-fail`) so the grep passes unmodified |
| g | A YAML editor for experiments ships in M11 | The donor's CRUD editor (`metric_files.py`, 271 lines, validate-before-write + `.history` archive — `detektkit/detectkit/ui/metric_files.py:1-18`) is explicitly **phase 2** | DASH-4's "edit" route is **read-only** (raw YAML text + file path for "open in your editor" / copy); no save endpoint exists in this milestone |

---

## 1. Work packages in strict dependency order

### DASH-1 — Port `JobManager` (subprocess registry) into `abkit/tuning/jobs.py`

**Goal:** a near-verbatim port of `detectkit/ui/jobs.py` (335 lines) with
`dtk`→`abk` renaming only — the `Job` dataclass, the line-buffer pump thread,
and `JobManager.spawn`/`spawn_pipeline`/`pipeline_active`/`stop`/`shutdown`/
`wait_for_line`/`snapshot`/`list_snapshots` — pure subprocess-tracking
infrastructure shared by every job route DASH-4 adds. No abkit-specific
statistical logic here at all.

**Files touched:** `abkit/tuning/jobs.py` (new), `tests/tuning/test_jobs.py`
(new).

**Steps:**
1. Copy `detektkit/detectkit/ui/jobs.py` (335 lines) into `abkit/tuning/jobs.py`
   verbatim except: the module docstring's `dtk`→`abk` wording, and the job
   `kind` vocabulary — abkit's pipeline kinds are `'run'|'unlock'|'clean'|
   'explore'` (no `'autotune'`/`'tune'` — abkit has no autotune command;
   `'explore'` takes the `JobManager.spawn()` non-pipeline path analogous to
   the donor's `'tune'`, since concurrent explores on *different* experiments
   are safe but two explores on the *same* experiment race the same
   Apply-rewrites-YAML hazard the donor's `running_tune_for()` guards
   against).
2. Keep `_MAX_LINES=5000`, `_MAX_JOBS=20`, `_STOP_GRACE_SECONDS=5.0` identical
   — no behavior change, no perf claim to re-derive.
3. `JobManager.pipeline_active()` excludes `kind=='explore'` from the
   one-at-a-time gate (mirrors the donor excluding `'tune'`) — `run`/`unlock`/
   `clean` serialize; `explore` does not.
4. Add `JobManager.running_job_for(kind, experiment)` as the abkit analog of
   the donor's `running_tune_for(metric)` — dedup key is `(kind='explore',
   experiment=name)` instead of the donor's `(kind='tune', metric=name)`.
5. Add a **dedicated `Job.experiment` field** (resolving, here and not later,
   the DASH-4 fork between overloading the donor's `Job.metric` field vs.
   adding a purpose-built field — the milestone decision is: add the field)
   to carry the experiment name for explore-job dedup.
6. Export `JobManager` from `abkit/tuning/__init__.py` alongside the existing
   `RecomputeEngine`/`serve_explore` exports.

**Tests & gates:**
- `tests/tuning/test_jobs.py`: `spawn()` pumps stdout into lines;
  `snapshot()`'s `next_offset`/`dropped`/`truncated` math against a
  >5000-line job; `spawn_pipeline()`'s one-at-a-time gate (two
  near-simultaneous `spawn_pipeline` calls from two threads — only one
  succeeds, matching the donor's TOCTOU-race regression comment);
  `pipeline_active()` ignoring `'explore'` jobs; `stop()` SIGTERM→grace→
  SIGKILL against a real short-lived subprocess (e.g.
  `python -c 'import time;time.sleep(10)'`); `wait_for_line()` timeout and
  match paths.
- ruff/black clean; no new `abkit.stats` import (this module never touches
  statistics).

**Risks / hotspots:** the kind-vocabulary swap (`autotune`/`tune` →
`explore`) is the one real deviation from "near-verbatim" — get it wrong and
DASH-4's argv builders + `running_job_for` dedup silently diverge from what
`jobs.py` actually gates.

**Session estimate:** 1 session.

**As built (PR #66, 2026-07-27) — what DASH-3/DASH-4 must know.** The port
shipped with five disclosed deviations from the donor, four of them found by
the WP's own two adversarial review rounds and all inherited-from-the-donor
rather than introduced here. The three that change a call site:

1. **`spawn()` raises `JobManagerClosed` (a `RuntimeError` subclass) once
   `shutdown()` has run**, and `spawn_pipeline` propagates it — `None` still
   means only "a pipeline job is already running". DASH-4's routes must
   distinguish them: `None` → 400 "already running", `JobManagerClosed` → 503
   "shutting down". The latch exists because the donor's snapshot-then-reap
   `shutdown()` lets a job spawned a moment later outlive the teardown
   (reproduced 300/300) — for abkit that is an `abk run` holding the pipeline
   lock with nothing left to stop it.
2. **The kind vocabulary is validated, not just documented** (`JOB_KINDS` /
   `PIPELINE_KINDS`; unknown kind → `ValueError`), because
   `pipeline_active()` became a whitelist. `validate`/`plan`/
   `verify-incremental`/`test-report` are deliberately NOT job kinds — adding a
   route for one means adding it to the vocabulary first.
3. **`spawn_pipeline` accepts `experiment=`** (not just `spawn`), so a
   single-experiment run/unlock/clean carries the name as a field. It stays
   `None` for a multi-experiment selection, so DASH-5's chip must fall back to
   `label`, which is what the donor's UI renders for every kind anyway.

Also: `snapshot()` carries `dropped`/`truncated` alongside `next_offset` (a log
drawer should say output was discarded, not infer it from a hole);
`wait_for_line` counts absolute line indices, so a job chattier than
`_MAX_LINES` before printing `Explore: <url>` is still matched; and the status
vocabulary is honest on both termination paths — a clean exit reads `done` even
after a Stop, and a job the teardown kills reads `stopped`, not `failed`.

---

### DASH-2 — `abkit/tuning/overview.py`: one-row dashboard shaper over `load_results`

**Goal:** a thin, pure-ish (DB read + verdict compute, no HTML/no rendering)
function building **one dashboard row per experiment** — latest verdict,
effect/CI, and a capped pre-aggregated sparkline — sourced from
`InternalTablesManager.load_results` (**not** `build_report_payload`, **not**
`load_session`). Row grain = one experiment. **What the row can carry is bounded by
`evaluate()`'s actual contract** (`abkit/pipeline/readout.py:430`):
`ExperimentReadout.verdicts` is built from `main_comparisons =
[c for c in experiment.comparisons if c.is_main_metric]` crossed with each
treatment arm — **secondary/guardrail comparisons never produce a
`PairVerdict`**. So the headline verdict is the first main-metric ×
treatment pair, and the `comparisons` sub-list for the row's expand carries
one mini-verdict **per (main-metric comparison × treatment arm)** — not
"every configured comparison". Surfacing secondary-metric verdicts would
require either re-implementing verdict logic (violating the "`evaluate()` is
the only verdict source" invariant, §0.1/§0.2) or a new change-controlled
readout helper — that is **M14 decision-layer work**
([m14 contour, ROADMAP](../../ROADMAP.md)), named in §3, not smuggled in
here. This resolves the "experiments AND their metrics" ambiguity (§3) in
favor of matching the button grain: open/explore/run/edit are all
experiment-scoped, per `abkit/tuning/server.py`'s existing
one-experiment-per-`serve_explore` contract.

**Files touched:** `abkit/tuning/overview.py` (new); `abkit/pipeline/readout.py`
(reused: `evaluate`, `PairVerdict`, `ExperimentReadout` — no changes);
`abkit/database/internal_tables/_results.py` (reused: `load_results` — no
changes); `tests/tuning/test_overview.py` (new).

**Steps:**
1. Define `WINDOW_PRESETS = {'24h':1,'7d':7,'30d':30,'90d':90}`,
   `ALL_WINDOW_PRESETS = frozenset({*WINDOW_PRESETS,'all'})` filtering on
   `end_ts` (the donor's `overview.py` `WINDOW_PRESETS`/`ALL_WINDOW_PRESETS` pattern,
   field renamed from the metric-timestamp axis to `end_ts`).
2. Define `_MAX_SPARK_BUCKETS = 160` and a defensive `MAX_STAT_POINTS` cap
   (e.g. `20_000`) on rows read per (experiment, metric, pair) before
   bucketing — port `_spark_series` (`detektkit/detectkit/ui/overview.py:264-281`)
   verbatim in shape but bucket `[end_ts, effect]` pairs instead of
   `[timestamp, value]` (abkit has no raw metric series here, only persisted
   `_ab_results` rows; effect is the natural sparkline axis).
3. `def build_experiment_row(*, project_root, experiment_path, experiment,
   project, tables, window_preset, now=None) -> dict`: load **all**
   comparisons' rows via **one** `tables.load_results(experiment.name)` call
   (no metric filter — cheaper than one call per comparison); filter to
   `end_ts` within the window; call `abkit.pipeline.readout.evaluate(
   experiment, rows, project=project)` **once** to get the
   `ExperimentReadout` (reused, not reimplemented — the "`evaluate()` is
   reused, `build_report_payload` is not" distinction).
4. Pick the headline `PairVerdict`: `readout.verdicts[0]`. No
   `is_main_metric` filter is needed — **every** entry in
   `readout.verdicts` is already a main-comparison verdict by construction
   (`readout.py:430`), so a filter would be vacuous; and no
   `verdicts[0]`-fallback-for-no-main-flag exists because
   `ExperimentConfig` validation forbids zero main comparisons
   (`ExperimentConfig.validate_comparisons`; ≥2 variants is
   `AssignmentConfig.validate_variants` — cited by symbol, because the m10
   window rename already rotted the line numbers this step first carried). Guard the theoretical empty-`verdicts`
   case (defensively, not as a designed state) by degrading the row via
   step 6's error path rather than indexing blind.
5. Row shape (mirrors `_empty_row`'s full-shape-with-error-degrade
   discipline, `detektkit/detectkit/ui/overview.py:293-319`): `{name, dir,
   file, tags, status, start_ts, horizon_ts, main_metric, locked (via
   tables.check_lock(experiment.name, scope='pipeline',
   process_type='run') — the pipeline lock's real key is
   (scope='pipeline', process_type='run'): `DEFAULT_PROCESS_TYPE = "run"`
   in abkit/database/internal_tables/_tasks.py:29, confirmed by
   unlock.py's lock_kinds = (("pipeline", "run"), ("pipeline", "validate"))),
   verdict, srm_flag, srm_pvalue, effect, ci:[lo,hi], pvalue, alpha,
   elapsed_days, is_horizon, weekly_cycle_pct, last_end_ts,
   spark:[[ts,effect],...], comparisons:[{metric,pair,verdict,effect} per
   PairVerdict in readout.verdicts — main-metric × treatment pairs only,
   per the Goal's contract note], error:null}`.
6. `def build_experiment_row_safe(...) -> dict` wraps
   `build_experiment_row` in try/except, degrading to an `_empty_row(name)`
   equivalent with `row['error'] = f'{type(exc).__name__}: {exc}'` — one bad
   experiment must never sink the payload (the donor's #1 discipline,
   `detektkit/detectkit/ui/overview.py:12-14,514-550`).
7. `def build_overview_boot_entries(project_root, experiments) -> list[dict]`:
   the **metadata-only** list for `GET /` (name/dir/file/tags/status/
   start_ts/horizon_ts/main_metric — **no** stats, **no** DB read), mirroring
   `detektkit/detectkit/ui/server.py`'s `metric_entries()`. It also carries
   `comparisons: [{metric, is_main_metric}]` straight off the config (still no
   DB read): the per-metric Run affordance (§3, DASH-4a) must exist for a
   secondary metric too, and those never appear in `readout.verdicts` — so the
   button list cannot be derived from the stats reply.

**Tests & gates:**
- `tests/tuning/test_overview.py`: a golden row against a fixture-seeded
  `_ab_results` (reuse the existing tuning/session test fixtures pattern from
  `tests/tuning/test_server.py`); `_spark_series` bucket count ≤160 for a
  500-cutoff synthetic series; window-preset filtering at each of the 5
  presets; a comparison whose `evaluate()` raises (a bad experiment-config
  edge) degrades the ROW to error+nulls without raising out of
  `build_experiment_row_safe`; a 3-arm fixture yields one `comparisons`
  sub-entry per (main-metric × treatment) pair and the headline =
  `verdicts[0]`; a secondary-only-metric fixture asserts the secondary
  comparison is absent from the sub-list (the evaluate() contract, not a
  bug); the defensive empty-`verdicts` guard degrades instead of raising
  `IndexError`.
- No `abkit.stats` import, no numeric recomputation — `evaluate()` is the
  **only** verdict source (byte-identical to what `abk run --report` would
  show; see the as-built record — "for the same window" turned out to be the
  wrong framing, and the gate is now an assertion against
  `build_report_payload`'s own verdict list).

**Risks / hotspots:** REPORT's phrasing "the list of experiments AND their
metrics" is genuinely ambiguous between row-per-experiment (chosen here) and
row-per-comparison — flagged as an open question (§3); if the maintainer
wants row-per-comparison instead, DASH-2/3/5's row key changes from
experiment to `(experiment, metric, name_1, name_2)`, and the JobManager
dedup key from DASH-1 stays experiment-scoped regardless (buttons remain
experiment-scoped either way).

**Session estimate:** 1 session.

**As built (PR #68, 2026-07-27) — what DASH-3/DASH-5 must know.** Shipped with
the deviations below, all but one found by this WP's own two adversarial
review rounds and each reproduced before it was accepted. The module docstring
carries the same list in the same order. **The two that change the payload
DASH-3/DASH-5 consume are (1) — the window no longer scopes the verdict — and
(7) — the per-pair list is called `verdicts`, not `comparisons`.**

1. **Step 3's "filter to `end_ts` within the window; call `evaluate`" is
   wrong, and the window is now display-only.** The step was inherited from
   the donor, whose datapoints are a plain time series where a left-bounded
   window really is a shorter series. `_ab_results` rows are **cumulative
   looks from a pinned start**: dropping the oldest deletes stabilization
   history while every surviving row still measures from `start_ts`.
   Measured on the fixture — a 14-look daily WIN reads INCONCLUSIVE at the
   `24h` preset (one look, below `MIN_STABLE_CUTOFFS`), i.e. **every** daily
   experiment; and a 6h-cadence series inverts the other way, reporting a WIN
   the full readout refuses. `abk run --report` passes no `start`/`end` at
   all, so the report's verdict IS the full-series verdict and the plan's own
   "byte-identical to what `abk run --report` would show" gate was
   unsatisfiable as written. **The preset now bounds the sparkline only**;
   every verdict cell is the full series'. A window-scoped verdict, if ever
   wanted, is an as-of replay (pin the RIGHT edge, keep history) and needs the
   look count + rationale on the row to be honest — that is not this WP.
2. **Rows for an undeclared arm pair are dropped before the readout**, the
   filter `reporting/builder.py` already applies. `readout._filter_rows`
   screens by metric and `method_config_id` only, so a mid-flight arm rename
   leaves rows that never reach a series lookup but DO join the read-time
   BH family and tighten every threshold: nine renamed-away pairs flipped the
   report's WIN to the dashboard's INCONCLUSIVE on identical rows.
3. **SRM is read window-independently** (`srm_summary` over all persisted
   rows), matching the report, so no preset can silence a red gate — and it is
   read BEFORE the readout, so it survives on a row whose verdict failed.
4. **The row carries `caveats` and `guardrail_regressed`** beside the verdict.
   Step 5's shape has the verdict word alone, but under `guardrail_policy:
   warn` the readout KEEPS a WIN and attaches a mandatory loud caveat — a row
   showing only "WIN" hands over exactly the green light the policy withheld.
5. **`project` is required, not optional, on both row builders and on
   `build_overview_boot_entries`.** Without it `evaluate` degrades to
   stored-alpha CI significance and mis-scores a project-level
   `benjamini_hochberg` (verified: WIN vs INCONCLUSIVE on the same rows), and
   the boot list would resolve `dir` against the literal `"experiments"` while
   the stats row used `project.paths.experiments` — the shell and the fill
   grouping the same experiment under two keys.
6. **`locked` is probed in a `finally`, inside its own `try` — isolated in
   BOTH directions.** It only greys a button, and `_ab_tasks` can be
   unreadable (a partially-completed `ensure_tables`, a narrow read-only
   grant) while `_ab_results` is fine; as first-and-unisolated it blanked the
   verdict, the SRM chip and the sparkline. Round 2 caught the mirror image:
   probed only after a successful read, it reported `locked: false` for every
   degraded row — the row an operator is most likely to press Run on. Failing
   to `False` is safe: the spawned `abk run` takes the real lock itself.
7. **The per-pair list is `verdicts`, not `comparisons`** (round 2). The boot
   entry's `comparisons` is the CONFIGURED list and the stats row's was the
   verdict list; DASH-5 merges the two payloads by experiment name, so one key
   with two incompatible shapes is a trap. Each entry carries its OWN
   `caveats` and `guardrail_regressed` — the row-level flag is ORed across all
   pairs, because a regression on the second arm must not leave a green flag
   on the row that lists it.
8. **The row also carries `rationale`, `warnings` and `timezone`** (round 2).
   `warnings` is `readout.warnings` plus the dropped-undeclared-pair count, so
   the two states `abk clean` exists for are visible on the surface an
   operator actually watches — without it a renamed arm is byte-identical to a
   never-run experiment. `timezone` is the experiment's own: every instant on
   the row is naive UTC and m10 made "the calendar day a look covers"
   timezone-sensitive.
9. **An unknown preset raises `UnknownWindowPreset`** (a `ValueError`
   subclass, exported from `abkit.tuning`), so DASH-3 can answer 400 for it
   and 500 for anything else instead of guessing which `ValueError` it caught.

Also as-built, for DASH-3's wiring: the row's `pair` sub-key is the report
payload's own `{"c": name_1, "t": name_2}`; `last_end_ts` is the headline
pair's latest cutoff — the instant every stat cell on the row is as of, NOT
the experiment's latest row (another metric can be ahead);
`build_experiment_row_safe`'s window argument is spelled **`window_preset=`**
(DASH-3 step 4 below says `window`); `MAX_STAT_POINTS` is exported so the
server does not hardcode a second copy of the cap; and the finite-scrub helper
is a local copy rather than an import from `tuning/recompute`.

**Two things DASH-5 must decide, because DASH-2 deliberately did not.** The
row has no field for the `abk-insufficient` marker class (§4): `is_horizon`
feeds `abk-prehorizon` and `srm_flag` feeds `abk-srm-fail`, but "insufficient"
is a per-look property whose source in the report is the persisted
`insufficient_data` cell — DASH-5 either reads it (a persisted column, not a
re-derived decision) or drops the chip. And `verdict: null` means *either*
"no `_ab_results` yet" *or* "this row degraded"; the two are told apart by
`error`, which is `null` only in the first case.

**Two named follow-ups, neither blocking.** `load_results` is `SELECT *`, so
every row drags `metric_query` + `metric_rendered_query` (~2.7 KB of SQL text
per persisted look) across the wire for a payload that never reads them — a
projected read belongs with DASH-3's perf pass, not here. And
`experiments_base_dir` honors `project.paths.experiments` while
`discovery.select_experiments` still hardcodes `"experiments"`, so a project
that renames the directory resolves `dir` correctly and then matches no
experiment at all through the selector — a pre-existing repo inconsistency
this WP surfaced but did not widen.

---

### DASH-3 — Dashboard localhost server skeleton: boot payload + stats route + token gate

**Goal:** a new module cloning `abkit/tuning/server.py`'s stdlib-http-server
shape (never modifying the explore server itself, and cloned **after** M10
WP4 per §0.5(e)) with the two deltas the decisions call out: the token gates
**every** route including GET (unlike `tuning/server.py`'s unauthenticated
GET), and the server **never self-shuts-down** (`serve_forever` until
Ctrl-C, unlike `tuning/server.py`'s post-`/apply`
`threading.Thread(target=srv.shutdown,...)`). No pipeline lock is ever taken
by this server — only a `db_lock` serializes `InternalTablesManager` reads.

**Files touched:** `abkit/tuning/dashboard_server.py` (new);
`abkit/tuning/html.py` (add `render_dashboard_html`, alongside the existing
`render_explore_html`); `tests/tuning/test_dashboard_server.py` (new).

**Steps:**
1. `abkit/tuning/html.py`: add `render_dashboard_html(payload)` following
   `render_explore_html`'s exact template mechanics (one-pass regex
   substitution, `</>`→`&lt;` escaping via `_bake_payload_json`, the **same**
   `_FAVICON` data-URI reused verbatim — keeping the CI hex-containment gate
   trivially satisfied since no new hex is introduced). Bundle read via
   `files('abkit.tuning')/'assets'/'dashboard.js'` (mirrors `_explore_js()`).
   Mount point `id='abk-dashboard'`, window globals
   `window.__ABK_DASHBOARD_PAYLOAD__` / `__ABK_DASHBOARD__.render(...)`.
2. `abkit/tuning/dashboard_server.py`: `_DashboardServer(ThreadingHTTPServer)`
   holding `token`, `html`, `project_root`, `project: ProjectConfig`,
   `profiles: ProfilesConfig`, `experiments: list[tuple[Path,
   ExperimentConfig]]`, `tables: InternalTablesManager`, `manager_factory`,
   `initial_window`, `profile: str|None`, `jobs: JobManager` (DASH-1),
   `db_lock: threading.Lock`, `echo`.
3. `_Handler._authorized(srv)` checks the token on **every** request
   (`do_GET` AND `do_POST` both call it first, mirroring
   `detektkit/detectkit/ui/server.py:271-294` — the two-delta doc comment
   must explicitly say "delta from `abkit/tuning/server.py` (dtk-tune
   pattern), not from dtk-ui, which already behaves this way" to avoid the
   §0.5(b) misattribution).
4. `_route_get`: `/` → boot payload
   (`overview.build_overview_boot_entries`, DASH-2) baked via
   `render_dashboard_html`; `/api/stats/<experiment>`
   (`urllib.parse.unquote`) → `build_experiment_row_safe(...,
   window_preset=...)` under `db_lock` (DASH-2 as-built: that is the argument's
   spelling, and `UnknownWindowPreset` — a `ValueError` subclass — is the one
   exception it raises, so 400 vs 500 needs no guessing), JSON reply; `/api/jobs` → `jobs.list_snapshots()`;
   `/api/job/<id>?offset=` → `jobs.snapshot(job, offset)`.
5. `build_dashboard_server(...) -> (server,url)` and
   `serve_dashboard(...) -> None` following `build_explore_server`/
   `serve_explore`'s exact shape (`abkit/tuning/server.py:797-880`) **except**:
   `serve_forever(poll_interval=0.3)` inside `try/except KeyboardInterrupt`
   with **no** `threading.Thread(target=srv.shutdown,...)` anywhere, and
   `finally: server.jobs.shutdown() + server.server_close()` on exit (the
   donor's `serve_ui:1207-1214` shape, not `tuning/server.py`'s
   `serve_explore` shape).
6. No caching layer: every `/api/stats/<name>` call re-reads the DB (matches
   the donor; DASH-5's 3-worker client pool bounds concurrency, not a
   server-side cache).

**Tests & gates:**
- `tests/tuning/test_dashboard_server.py` (model on
  `tests/tuning/test_server.py`, 674 lines): a bare token check on `GET /`
  (401/403 without `?token=`, 200 with it) — the FIRST regression test that
  would catch the tune-server GET-unauthenticated pattern leaking in by
  copy-paste; `GET /api/stats/<name>` for an unknown experiment → 404; a bad
  window preset → 400; concurrent `/api/stats/<a>` and `/api/stats/<b>`
  calls both succeed (`db_lock` serializes but doesn't deadlock); the server
  never calls `shutdown()` after any GET/POST in this WP (assert
  `server._BaseServer__is_shut_down` or a `serve_forever` mock is never told
  to stop) — a literal regression test for the "server does not
  self-terminate" invariant, since DASH-3 has no `/apply` yet.
- A metric read that raises inside `build_experiment_row_safe` still returns
  200 with `row['error']` set (never 500) — the row-isolation contract from
  DASH-2 surfacing correctly through the HTTP layer.

**Risks / hotspots:** copy-paste from `tuning/server.py` is the most likely
source of the exact two regressions §0.5(b) calls out (unauthenticated GET,
self-shutdown) — the test suite must assert both explicitly, not just
"happy path 200". Holding `db_lock` across a `build_experiment_row_safe` call
over a very long-running experiment could serialize concurrent tab loads for
seconds; acceptable per donor precedent (the same one-connection-per-manager
constraint), worth a comment, not a fix, in this WP.

**Session estimate:** 1 session.

**As built (PR #69, 2026-07-29) — what DASH-4/DASH-5 must know.** Both deltas
shipped as specified and are pinned by tests that were each proven to fail on
the `tuning/server.py` shape (25 mutation probes, all caught). The wiring facts
a later WP would otherwise have to rediscover:

1. **`build_dashboard_server(*, project, project_root, experiments, tables,
   initial_window=DEFAULT_WINDOW_PRESET, profile=None, jobs=None, echo=print)`
   → `(server, url)`**, and `serve_dashboard(...)` adds `open_browser`,
   `on_ready` and returns `None`. Two fields from the plan's step-2 list are
   deliberately absent: **`manager_factory`** (the dashboard has no write path
   needing a per-request connection — the one manager under `db_lock` is the
   donor's shape, and a factory nothing calls is a seam that rots) and
   **`profiles: ProfilesConfig`** (nothing in DASH-3..6 reads it; the profile
   *string* is what DASH-4's argv needs, and it rides along). `jobs=` is
   injectable so DASH-4's tests and DASH-7's e2e can watch the registry.
2. **The boot payload carries no token** — the client reads it from
   `location.search`, the dtk-ui contract, so the served page is not a
   credential at rest and works whatever port was bound. It is
   `{project, profile, version, initial_window, window_presets, generated_at,
   experiments: BootEntry[]}`; `window_presets` is derived from
   `WINDOW_PRESETS`' day counts (shortest first, `all` last) so DASH-5's
   selector does not hardcode a second copy of the list.
3. **`GET /api/jobs` replies `{jobs, pipeline_active}`**, not the donor's bare
   `{jobs}`: the client's idle/running chip must not re-derive
   `pipeline_active`'s rule (`kind ∈ PIPELINE_KINDS ∧ running`) in JS, which is
   the divergence DASH-1's validated vocabulary exists to prevent. The flag is
   advisory by construction (two registry reads, so it can lag its own list by
   one finished job) — the authoritative gate is `spawn_pipeline`'s atomic
   check, so a client acting on a stale chip still gets DASH-4's 400.
4. **Status-code map, so DASH-4 extends it rather than inventing one:** 403 for
   a bad/absent token on EVERY route, checked before routing (a 403 is not a
   path oracle); 404 for an unknown experiment, an unknown job id, and any
   unrouted path or POST; 400 for an unknown window preset (the named
   `UnknownWindowPreset` only — a stray `ValueError` on a GET is a 500, since a
   GET's arguments are looked up or regex-checked, while on POST `ValueError`
   *is* 400 because a body is arbitrary JSON and `JSONDecodeError` is one);
   413 for a body over 5 MB (drained first, so the client reads the status);
   400 for a malformed *or negative* `Content-Length`. `do_POST` already
   carries the gate and the bounded body read — DASH-4 fills in `_route_post`.
5. **A blank query value is a value.** The query is parsed with
   `keep_blank_values=True`, so `?window=`/`?offset=` are 400s rather than
   silently reading as absent (the boot window, offset 0). The offset regex is
   length-bounded (`\d{1,15}`) because past 4300 digits `int()` itself raises,
   which would surface as a 500 on a bad request.
6. **JSON replies send `Cache-Control: no-store`.** Every dynamic answer here is
   a GET at a URL that repeats between polls, and a response with no validators
   is heuristically cacheable — a cached row would show a verdict from before
   the run the operator just launched. The explore server needs none of this:
   its answers are all POST replies.
7. **Two donor hazards fixed rather than inherited** (the DASH-1 precedent):
   `handle_error`'s echo is suppressed, because socketserver calls it from
   inside its own `except` and a raise there ends `serve_forever` — `abk
   dashboard | head` would take the cockpit down through a closed stdout; and a
   post-bind construction failure (a duplicated experiment name, a config whose
   window will not resolve) closes the socket instead of leaking a listener
   nobody holds a handle to.
8. **`abkit/tuning/assets/dashboard.js` is deliberately NOT committed here.**
   `render_dashboard_html` degrades to an honest "the client bundle is not
   installed, run `cd web && npm run build`" note, because a placeholder file
   would have to smuggle the three `abk-*` marker classes past the CI gate that
   greps every `abkit/*/assets/*.js` for them — the M3 precedent (a committed
   placeholder `explore.js`) predates that gate. A missing `explore.js` still
   raises: that bundle is committed and wheel-asserted, so its absence is a
   packaging bug. **DASH-5/DASH-6 should decide whether the degradation stays**
   once the real bundle ships and the wheel namelist asserts it (step 3a).
9. Two boot-time snapshots, both matching the donor and both worth knowing
   before DASH-4 adds the read-only "open in your editor" affordance: the
   served **selection** (configs read once — an edited YAML needs a restart to
   change a verdict) and the baked page. A "reload configs" affordance is a
   named follow-up. `set_experiments` is boot-only for the same reason: it
   writes the list and its name index unlocked.
10. Also as-built: `validate_window_preset` is now **public** in
   `overview.py` (was `_validate_window_preset`), so the boot check and the
   `?window=` check raise the same message with no second copy to drift; and
   `serve_dashboard`'s Ctrl-C returns without joining handler threads
   (`daemon_threads`), so a read in flight may print one `[dashboard] request
   error` line if the caller closes its manager under it — deliberately not
   traded for a Ctrl-C that hangs for as long as the slowest query.

---

### DASH-4a — `abk run --metric`: the CLI capability the per-metric Run spawns

**Goal:** let an operator recompute ONE comparison of an experiment instead of
all of them (§3, decided 2026-07-27). This is a **pipeline/CLI** work package,
not a dashboard one — the dashboard is a launcher and cannot offer what the CLI
cannot do — but it is scheduled here because DASH-4's route and DASH-5's button
are its only planned callers. `abk run` is today the sole per-comparison
command without the flag: `validate`, `plan`, `explore` and
`verify-incremental` all take `--metric`, so the vocabulary already exists and
this WP makes `run` consistent with it.

**Files touched:** `abkit/cli/main.py` (the option), `abkit/cli/commands/run.py`
(passthrough), `abkit/pipeline/driver.py` (the filter, LOAD/STATE/COMPUTE),
`tests/pipeline/test_pipeline.py` + `tests/cli/test_run_command.py`,
`docs/reference/cli.md` + `docs/guides/*` as the three-way sync requires,
`CHANGELOG.md`.

**Steps:**
1. `--metric` on `abk run`, single-valued, matching `validate`'s spelling and
   help text. It filters **comparisons by metric name**, so an experiment
   running the same metric under two method configs recomputes both — the same
   granularity `validate --metric` already has.
2. Filter the driver's `for comparison in experiment.comparisons` loop, and the
   metric loop of the STATE stage, so a filtered run neither loads nor
   materializes what it will not compute. The cohort resolve and the SRM gate
   stay unfiltered: they are experiment-level and the gate must still block.
3. **The alphas must not move.** `analyze.effective_alphas()` derives the
   two-tier scheme from the CONFIG (`experiment.comparisons`, counting non-main
   entries), never from what a given run happens to compute — so filtering is
   alpha-invariant by construction. Pin it: a run with `--metric` writes rows
   whose `alpha` is byte-identical to the same rows written by an unfiltered
   run. This is the WP's #1 assertion; without it a per-metric recompute would
   silently re-alpha a series.
4. `--full-refresh --metric m` is the real "recompute this metric" (after a SQL
   edit). No new deletion path is needed: `delete_results` already takes
   `metric=`/`method_config_id=`, so the filtered loop deletes only that
   metric's rows. `--resync-cohort` stays experiment-level (the cohort is not
   per-metric) and a `--metric` combined with it must say so rather than imply
   a narrower rebuild.
5. Selector semantics: `--metric` that matches no comparison in ANY selected
   experiment is a loud error (the repo idiom), not a silent no-op; matching in
   some experiments and not others skips the others with a printed line.

**Tests & gates:**
- The alpha-invariance assertion above, plus: a filtered run computes exactly
  the targeted comparison's cutoffs and leaves the others' `_ab_results` rows
  byte-identical; `--full-refresh --metric` deletes and rebuilds only that
  metric's series; the no-match error; STATE materializes only the filtered
  metric's day state.
- Zero statistical numbers move (M7–M12 posture): no `ALGORITHM_VERSION` bump.

**Risks / hotspots:** step 3 is the whole risk. The second one is scope creep
into "recompute a single arm pair", which is NOT part of this decision — arms
are what a comparison already spans.

**Session estimate:** 1 session.

**As built (PR #71, 2026-07-30) — what DASH-4/DASH-5 must know.** All five
steps shipped as specified; the plumbing is one optional `metric_filter`
threaded CLI → `run_experiments` → `run_experiment` → `materialize_state`, and
`abkit/pipeline/analyze.py` was not touched at all. What a later WP would
otherwise have to rediscover:

1. **The route's contract is just the metric NAME.** `POST /api/run`'s optional
   `metric` (DASH-4 step 2) maps 1:1 onto `--metric <name>`; validate it with
   **`ExperimentConfig.declares_metric(metric)`** — the predicate this WP put on
   the config model precisely so the route's 400 gate and the CLI's selection
   narrowing cannot drift (a review finding: the first cut kept it private in
   `abkit/cli/commands/run.py`, which `abkit/tuning/**` does not import). There is no
   per-comparison or per-arm-pair addressing to expose — and step 1's stated
   reason for the name grain ("an experiment running the same metric under two
   method configs recomputes both") **is not reachable in this config model**:
   `ExperimentConfig.validate_comparisons` rejects duplicate metric bindings
   ("bind each metric at most once per experiment"), so inside one experiment a
   metric name IS one comparison. The grain still matters for the other axis —
   `--metric` applies to the whole SELECTION, so a broad `--select` recomputes
   that metric in every experiment declaring it — and `abk validate --metric`
   remains the true multi-method analogue, because its `--method` flag scores
   extra methods for the same metric. The plan sentence was carried into the
   CHANGELOG/docs and then corrected in review; do not re-introduce it.
2. **Step 3 held without new code.** `effective_alphas()` reads
   `experiment.comparisons` (config), so the filter — applied to the driver's
   *loop*, never to the config object — is alpha-invariant by construction. The
   pinning test is built to catch the leak rather than assert a tautology: 1
   main + 2 secondary metrics over 2 variants, so the secondary tier is α/2, and
   a filter reaching `effective_alphas` would write α instead. It compares the
   whole row dict (minus the volatile `created_at`) against an unfiltered run on
   a second warehouse.
3. **Day state is where a narrowed run still has to reach beyond the filter, and
   getting that wrong is a NUMBER bug.** A withheld series that stays
   materialized-but-stale is invisible to M9's gap check (it detects absence
   only), so with `compute.incremental_reads: true` a later routine run sums it
   and silently persists an undercount. Two mechanisms, both reviewed:
   - under a copy-mode `--resync-cohort` the driver passes `metric_filter=None`,
     keeping the force-rebuild experiment-wide (the rebuilt cohort re-shaped
     EVERY series). The exception is keyed off `copy_enabled and resync_cohort`
     — the SAME predicate as `force_rebuild` — so in the direct default, where
     the flag is a no-op, day state narrows with everything else. The CLI
     disclosure is therefore **mode-aware**; an unconditional line was false in
     the default mode and contradicted the notice above it in copy mode;
   - under a scoped `--full-refresh` the STATE stage **truncates** the withheld
     eligible series over the refreshed window (`delete_state_days_from`, no
     re-render). The refreshed window's facts are shared by every metric reading
     them, and `--full-refresh` is the documented heal for exactly that, so
     declining to touch the siblings turned the heal into a trap: the review
     reproduced 3334.5 vs a true 3434.5 on a two-metric fixture. Truncating
     keeps contiguity (a shorter prefix), the operator does not pay a render
     they scoped away, and the next run that includes the metric re-derives
     those days.
   Round 2 of review then corrected the DISCLOSURE of both: the first cut decided
   it once per run with `any(cohort_copy.enabled …)` and ignored `--steps`, so a
   selection mixing a copy-mode with a direct-mode experiment printed the
   copy-mode sentence on behalf of the direct one — and suppressed the truncation
   line that was the true one for it — while a run omitting the `state` step
   claimed a truncation it never performed. The disclosure now classifies each
   matching experiment (rebuilt / truncated / untouched / no-state-step) and
   prints one line per outcome, naming experiments only when the selection is
   heterogeneous; both branches are mutation-pinned.
   Both mechanisms are pinned in `tests/pipeline/test_state_stage.py::TestMetricFilter`,
   including an end-to-end backfill regression that fails (numerically, not just
   structurally) without the truncation. Note the two stamps are different
   columns: `_ab_results` rows carry `created_at`, `_ab_unit_state` rows carry
   `version` — the results-level "never re-written" assertions live in
   `tests/pipeline/test_pipeline.py::TestMetricFilter`.
4. **An unmatched filter is answered at two levels.** The CLI resolves the
   selector semantics (skip line per experiment, loud error when nothing
   matches, `--steps validate` + `--metric` rejected as a `BadParameter`
   because the config lint is project-wide); the driver additionally returns
   `status="skipped"` with `error="no '<m>' comparison"` for any non-CLI caller
   — **above** `ensure_tables()`, so such a call takes no lock, renders no
   cohort and runs no SRM gate. `abk run` now prints `outcome.error` for a
   skipped experiment instead of the hardcoded "nothing to do for the selected
   steps".
5. Tests landed in `tests/pipeline/test_pipeline.py::TestMetricFilter` (5),
   `tests/pipeline/test_state_stage.py::TestMetricFilter` (6) and
   `tests/cli/test_cli.py::TestMetricOption` (10) — the plan's guessed
   `tests/cli/test_run_command.py` does not exist in this repo; `abk run`'s CLI
   tests live in `tests/cli/test_cli.py`. **The CLI fixture had to gain a
   two-comparison experiment**: with one comparison per experiment, "only the
   filtered metric was written" is already true of the experiment-level selection
   narrowing, so the assertion could not fail — deleting the CLI's one
   `metric_filter=` keyword left all 2670 tests green (a review finding; now
   mutation-verified to fail). Same class of gap as M10's `interval_anchor`
   reaching none of eight call sites.

---

### DASH-4 — Job-spawning routes: open / explore / run / edit-stub, wired through `JobManager`

**Goal:** POST routes that spawn the real `abk` CLI as a subprocess (never
in-process, never taking the pipeline lock) using `JobManager` (DASH-1)
against the dashboard server (DASH-3). Explore scrapes the printed
`"Explore: <url>"` line exactly like the donor's `/api/tune` scrapes
`"Tuner: <url>"` (`abkit/tuning/server.py`'s `serve_explore` already echoes
`"  Explore: {url}"` — no CLI change needed, just a regex). The CRUD YAML
editor stays explicitly out of scope (§0.5(g)) — "edit" here is read-only:
return the experiment's raw YAML text + file path so the client can offer
"open in your editor" / copy, not a save endpoint.

**Files touched:** `abkit/tuning/dashboard_server.py` (extend `_route_post`,
add argv builders + `_handle_run`/`_handle_explore`/`_handle_unlock`/
`_handle_clean`/`_handle_stop`/`_handle_metric_source`-equivalent);
`tests/tuning/test_dashboard_server.py` (extend).

**Steps:**
1. `_subprocess_env()` and argv builders mirroring
   `detektkit/detectkit/ui/server.py:142-219` but for `abk` verbs:
   `_run_argv(select, profile) -> ['abk','run','--select',select]`
   (+`'--profile'` if set); `_unlock_argv`/`_clean_argv` same shape;
   `_explore_argv(select, profile) -> ['abk','explore','--select',select,
   '--no-open']` (the dashboard opens its **own** browser tab via the
   returned URL, so the spawned explore must not also try to open one —
   reuse the existing `--no-open` flag from `abkit/cli/main.py:165`).
2. `POST /api/run`: validate `select` against `srv.experiments` (400 on
   unknown); an optional `metric` in the body is validated against that
   experiment's comparisons (400 on unknown) and appended as `--metric`
   (DASH-4a) — the §3 per-metric requirement, same route, same gate;
   `job = srv.jobs.spawn_pipeline('run', f'run --select
   {select}', _run_argv(...), cwd=srv.project_root, env=_subprocess_env(),
   experiment=select_if_single)`;
   `None` → 400 "a pipeline job is already running" (the donor's exact
   one-at-a-time UX, `detektkit/detectkit/ui/server.py:614-620`);
   `JobManagerClosed` → 503 (DASH-1 as-built: `None` means busy, which a
   teardown is not).
3. `POST /api/unlock`, `POST /api/clean`: same `spawn_pipeline` shape as
   `/api/run` (`abk unlock`/`clean` already exist as CLI commands per
   `abkit/cli/main.py` — confirm exact flag names before wiring, e.g.
   `--select`/`--force`).
4. `POST /api/explore`: validate `select` resolves to exactly one experiment
   (reuse `abkit.config.select_experiments` — the **same** selector
   `abk explore --select` uses, so a selector ambiguity is caught before
   spawning, not after); dedup via `srv.jobs.running_job_for('explore',
   experiment)` — reopen the existing tab's URL on a second click, mirroring
   `detektkit/detectkit/ui/server.py:757-766`; else `job = srv.jobs.spawn(
   'explore', f'explore --select {experiment}', _explore_argv(...), cwd=...,
   env=..., experiment=experiment)` (using the dedicated `Job.experiment`
   field DASH-1 adds — see §0.5, no `.metric` overload); `line =
   srv.jobs.wait_for_line(job, lambda ln: 'Explore:' in ln, timeout=90.0)`;
   regex `r'Explore:\s*(\S+)'` extracts the URL (mirrors the donor's
   `_TUNER_URL_RE`); on timeout, `srv.jobs.stop(job.id)` + reply 400 with the
   last 20 lines of output (the donor's exact failure UX, `server.py:776-780`).
5. `GET /api/experiment-source/<name>`: reply `{name, path, yaml_text}` read
   directly off disk (no DB) for the read-only "edit" affordance — explicitly
   **not** a mutation route; document in the module docstring that CRUD
   (validate-before-write, archive-on-mutate like the donor's
   `metric_files.py`) is phase 2.
6. `POST /api/job/<id>/stop`: `srv.jobs.stop(job_id)` (the donor shape,
   `detektkit/detectkit/ui/server.py:787-791`).

**Tests & gates:**
- `tests/tuning/test_dashboard_server.py`: `/api/run` spawns a fake-abk stub
  script (a tiny python script standing in for the `abk` entrypoint in test
  envs) and asserts `job_id` in the reply, then `/api/job/<id>` polling shows
  the status transition running→done; a second `/api/run` while the first is
  still running → 400 (one-at-a-time, exercising `JobManager.spawn_pipeline`'s
  gate from DASH-1 through the HTTP layer); `/api/explore` against a stub
  that prints `"Explore: http://127.0.0.1:9/?token=x"` within timeout → 200
  with the scraped url; `/api/explore` timeout path (a stub that never
  prints the line) → 400 with tail output, and the job is stopped (not left
  running); `/api/explore` called twice for the **same** experiment while
  the first explore job is still running → returns the **same** job_id/url
  (dedup), not a second spawn.
- No pipeline lock is ever acquired by the dashboard server itself — assert
  via a spy/monkeypatch on `InternalTablesManager.acquire_lock` that it is
  never called from this module (only the spawned subprocess's own process
  takes it) — the §0.5(d) invariant, first pinned here.

**Risks / hotspots:** `wait_for_line`'s fixed 90s timeout on `/api/explore`
could time out on a legitimately slow session load (large project, cold DB),
surfacing as a false "tuner did not start" error — acceptable v1 behavior
(matches the donor exactly) but worth a `--timeout` override noted as a
follow-up, not solved here.

**Session estimate:** 1 session.

**As built (PR #72, 2026-07-30) — what DASH-5/DASH-6/DASH-7 must know.** All six
steps shipped, plus the read route the "edit" affordance needs. The routes are
`POST /api/run` (`{select, metric?}`), `POST /api/unlock`, `POST /api/clean`,
`POST /api/explore` (`{select}`), `POST /api/job/<id>/stop` and `GET
/api/experiment-source/<name>`. What a later WP would otherwise have to
rediscover:

1. **The client posts a NAME; the server derives the selector (the PATH) and then
   PROVES it.** `--select <name>` is not safe to spawn: `select_configs` resolves
   a bare name by trying `<experiments>/<name>.yml` FIRST and only then searching
   the `name:` fields, so a file named after ANOTHER experiment
   (`experiments/alpha.yml` declaring `name: beta`, with `alpha` declared
   elsewhere) shadows it and the cockpit would run, unlock, clean or explore
   something other than the row that was clicked, silently. `_selector_for()`
   therefore passes the YAML path relative to the project root, which takes the
   glob branch and resolves to exactly one file — which also satisfies `abk
   explore`'s "must match exactly ONE" by construction. A glob metacharacter in
   the file name (`checkout[v2].yml` is legal and unremarkable) is **escaped**
   rather than made a reason to fall back — `[`→`[[]`, `*`→`[*]`, `?`→`[?]`,
   pathlib's only escape — because raw `experiments/star*.yml` would match a
   SIBLING too, and the name fallback is the very hazard the path form exists to
   avoid. Only a path outside the project root or one with no directory part
   still falls back.
   Then **`_verified_selector()` re-resolves it through `select_experiments` —
   the child's own resolver — on every job POST, and refuses (400) unless it
   lands on exactly the clicked experiment.** This IS the plan's step-4 second
   resolution, and the review that put it there measured why an earlier draft's
   "not needed" was wrong: `abk run`/`unlock`/`clean` answer an unmatched
   selector with a warning line, "Nothing selected." and **exit 0**, so with the
   boot snapshot outliving a renamed or deleted YAML the drawer would show a
   green, successful Run that computed nothing. The check also covers the
   remaining name fallback and a project whose `paths.experiments` is not the
   default `experiments/`, which the CLI's selector cannot reach at all
   (`select_experiments` hard-codes the directory — a pre-existing project-wide
   gap, a named follow-up). Its costs, both accepted: one config-discovery scan
   per click, and a broken sibling YAML surfaces as a 400 naming the file (a
   `select_experiments` `ValueError`). One consequence for DASH-5: the job label
   reads `abk run --select experiments/growth/checkout.yml`.
2. **Scraping the explore URL needs the scheme in the pattern.** `abk explore`
   prints `Explore: <experiment name>` (`cli/commands/explore.py`) BEFORE
   `serve_explore` prints `  Explore: <url>`, so the donor's `"Tuner:" in line`
   predicate ported literally matches the header and hands the client an
   experiment name as a URL. `_EXPLORE_URL_RE` is `r"Explore:\s*(https?://\S+)"`,
   and one function (`_explore_url`) is both the wait predicate and the
   extractor so the two cannot drift onto different patterns.
3. **Three DASH-1 amendments ship here, all in `jobs.py`:**
   `spawn_deduped(kind, …, experiment)` → `(job, created)` makes the explore
   dedup ATOMIC under the same `_gate` the pipeline gate uses (`running_job_for`
   alone is check-then-act, and a double-clicked button — browsers do fire both
   — would start two sessions on one experiment, which is the
   Apply-rewrites-the-YAML race the dedup exists for; the test widens the window
   by slowing `spawn`, so it is deterministic rather than lucky); `url_for(job)`
   is the read half of `set_url` so no caller reaches into `job.lock`; and every
   child is spawned with **`stdin=DEVNULL`** — the donor leaves stdin inherited,
   so a prompting child (only `abk clean --orphaned-experiments` today, which no
   route spawns) would eat the operator's terminal input invisibly. Under pytest
   fd 0 is already `/dev/null`, so that one is asserted at the `Popen` call.
4. **`POST /api/explore` is a LONG request, and the only one.** It holds the
   response until the spawned cockpit prints its URL — up to
   `server.explore_url_timeout` (90 s) — so DASH-5 must give that one route a
   long fetch timeout and a spinner; Run/Unlock/Clean answer as soon as `Popen`
   returns. A second click on a live cockpit gets the SAME job and URL, and if
   the URL has not been printed yet it waits too (the donor answers an immediate
   400 "a tuner for X is already starting", which a quick double-click hits
   routinely) — but on a shorter budget, `_EXPLORE_DEDUP_WAIT = 10 s`, because
   every waiter holds a request thread and repeat clicks all land on the one
   deduped job. Measured under review: ~25 waiters block nothing else (every
   other route still answered in milliseconds), so a bounded admission
   semaphore — the shape `tuning/server.py` uses for its own long route since
   m10 WP4 — stays a named follow-up rather than a fix here.
   When no URL arrives, only a job **this** request spawned is stopped (someone
   else's session may simply be slower than our deadline), and the 400 says which
   of three things happened — the child exited without serving, our deadline
   lapsed, or another tab's cockpit is still starting. Read the status for that,
   never infer it from "did I spawn this?": the wait also ends when the child
   exits, so a second caller would be told a dead cockpit "is still starting".
   The child's last 20 lines ride along either way, which is where the D2 noop
   ("no computed results yet — run `abk run` first", exit 0 without ever serving)
   surfaces.
5. **Status codes, extending DASH-3's map:** 400 for every body-level fault —
   a missing/blank/oversized `select`, an experiment not in the served
   selection, a **selector that no longer resolves to it** (item 1), a metric the
   experiment does not declare, malformed JSON (including a body nested deeply
   enough to raise `RecursionError`, which is a `RuntimeError` and would
   otherwise reach the generic 500), a bodyless POST, **and an unknown field** —
   while a name in the PATH keeps
   404 (`GET /api/experiment-source/<unknown>`, `POST /api/job/<unknown>/stop`).
   404 vs 400 for "unknown experiment" is therefore positional, not
   inconsistent: DASH-3's map is about path-addressed resources. Also: 400 (the
   donor's exact "a pipeline job is already running") when `spawn_pipeline`
   returns `None`, **503** for `JobManagerClosed` (a teardown is not "try
   later"), 400 for a job that is no longer running vs 404 for an unknown id
   (the donor conflates both into one 400, which reads as "your id is wrong" for
   a job that finished a moment earlier), and **500 naming the project root**
   when the spawn itself raises `OSError`.
6. **An unknown body field is refused, not ignored — unless it is `null`.** The
   donor drops extras silently, which is the invisible half of a feature that
   does not exist: a client posting `{"full_refresh": true}` to `/api/run` would
   get a plain run and no hint. So DASH-5 sends `{select}` / `{select, metric?}`
   and adding a knob stays a deliberate act on both sides. The exemption matters
   for exactly one convention this WP also documents: `metric: null` means "the
   whole experiment", `metric` is allowed on `/api/run` only, and a request
   helper that always emits both keys would otherwise fail the other three
   buttons — so an unknown field whose value is `null` asks for nothing and is
   ignored, while a non-null one is refused. A blank or whitespace `metric` is a
   400 (the `keep_blank_values` discipline from the GET side).
7. **The spawned command is neither `abk` nor `python -m abkit.cli.main`** —
   it is `sys.executable -c "<bootstrap>"` (`_CLI_BOOTSTRAP`), and each half of
   that is load-bearing. Pinning `sys.executable` pins the INSTALL: a bare `abk`
   is whatever `PATH` says, which in an unactivated venv is a different abkit
   than the one serving the page. But `-m` (and a plain `-c`) then puts the
   child's CWD on `sys.path[0]`, and a job spawns with `cwd=<project root>` —
   the OPERATOR's directory. **A file there named after anything abkit imports
   (`click.py`, `yaml.py`, `statistics.py`, …) breaks every button** with a
   traceback nobody can connect to the click, and an `abkit/` directory there
   runs a different abkit than the one just pinned — reproduced in review, and a
   real console script (what typing `abk` runs) does neither, so "exactly as if
   typed" was false. The bootstrap therefore drops `''`/`os.getcwd()` from
   `sys.path` before the first import and sets `sys.argv[0] = 'abk'` so click's
   usage errors name a command an operator could retype (`-m` would say
   `main.py`, `-c` would say `-c`). Consequence, unchanged by the fix and now
   sharper: **every job needs an INSTALLED abkit** (`pip install -e .` or a
   wheel), because the directory a bare checkout would be importable from is
   exactly the one that gets dropped; the test that runs the bootstrap from an
   unrelated directory asserts in CI and skips in a bare checkout, **DASH-7's
   e2e must run under an installed abkit for the same reason**, and warning about
   it once at startup belongs to DASH-6's `abk dashboard`, not to a route.
   Also: every flag each builder passes is checked against the click command's
   own declared options, so a renamed `--metric` / `--no-open` / `--execute`
   fails a test instead of an exit-2 job in the drawer; `/api/clean` spawns the
   **`--execute`** form (a dry run would be a button that does nothing) and never
   `--orphaned-experiments` (the one prompting path, and not a one-click action)
   — DASH-5 owns the confirmation.
8. **The job label is derived from the argv that ran** (`_label_for`), not
   formatted a second time, so the drawer cannot show a command that differs
   from the process — which matters most for the flags the caller never chose
   (`--execute`, `--no-open`, the profile). Snapshots still carry no `argv`.
9. **`server.explore_url_timeout`** (default 90 s) is a per-server attribute, so
   a test can shrink it; a CLI/`--timeout` override remains the named follow-up
   the plan's risk note asked for. `GET /api/experiment-source` caps the body at
   512 kB with a `truncated` flag and decodes with `errors="replace"`, replies
   `{name, path, yaml_text, truncated}` where `path` is the **same string the
   row carries as `file`**, and addresses the experiment by NAME with the path
   taken from the boot index — a `?path=` parameter would be a traversal seam. A
   YAML that vanished since boot is a 404 that says to restart (the boot
   snapshot is never refreshed).
10. **Test-harness facts DASH-7's e2e will reuse:** the job routes need a REAL
   project root (they spawn with `cwd=project_root`, read YAML off disk, and now
   re-resolve the selector), which `test_overview`'s hermetic `/proj` cannot be,
   so `tests/tuning/test_dashboard_server.py` gained `write_experiment()` (writes
   the fixture YAML, returns the config parsed FROM that file) and a stub `abk`
   installed by pointing **`_CLI_PREFIX`** at it — deliberately not by
   monkeypatching the argv builders, so the real verb/flag composition and
   `_label_for`'s slice stay under test and the stub can echo the argv it actually
   received. `$ABK_STUB` selects its behaviour (echo-and-exit, hang, exit
   briefly, fail, print an explore URL after the CLI's own `Explore: <name>`
   header).
11. **Review record.** 16 author-side mutation probes (all caught) plus a
   five-lens adversarial pass (concurrency, HTTP security, CLI-contract,
   test-quality, claims-vs-code) with independent skeptic verification of each
   finding. What it changed, all above: the `-m`→bootstrap spawn form (the
   `sys.path[0]` injection — the pass's one CONFIRMED finding, reproduced twice),
   `_verified_selector` (the exit-0 "Nothing selected." silent-green), glob
   escaping (the metacharacter fallback ran the wrong experiment through the
   child's own resolver), the `RecursionError` 400, the `null`-field exemption,
   the three-way explore failure message + the dedup wait cap, the false "returns
   immediately" docstring claim, `spawn_pipeline`'s stale "use `spawn()`"
   pointer, and four test defects — the new GET route missing from the
   token-gate parametrize (an ungated file-content route would have shipped
   green), an unfalsifiable "a copy, not `os.environ`" assertion, an unobserved
   `_MAX_FIELD` cap, and a bodyless-POST test that passed with the guard removed.
   The lenses' negative results are worth as much: a lock-order audit plus
   barrier-synchronised run/explore races, a 7392-request 12-thread chaos fuzz
   (no 5xx, no hang, no orphaned child), teardown-racing spawns (no orphan, no
   zombie), traversal/argv-injection/header-injection probes on every new route,
   and 25 concurrent explore waiters starving nothing.
   Final: 233 tests across the two files (1 skipped where abkit is not installed),
   **100% coverage of `dashboard_server.py`**, 2781 passed / 7 skipped overall,
   mypy 111 = baseline, ruff/black clean, `ALGORITHM_VERSION` absent from the
   diff.

---

### DASH-5 — `dashboard.ts`: client bundle — boot render, lazy stats pool, sparkline, verdict chip, job drawer

**Goal:** net-new TypeScript authorship (§0.5(a) — the donor has no
committed TS source to port, only a minified `ui.js`; this is written fresh,
reusing abkit's **own** `web/src/shared/chart.ts` primitives and
`web/src/explore/explore.ts` idioms for scoped CSS / `ROOT_CLASS`
conventions, not a line-for-line port). Implements: metadata-only initial
render (every row `pending`), a fixed-concurrency-3 client worker pool over
`GET /api/stats/<experiment>` (`Promise.all`-based, matching the donor's
`Vn=3` pattern — JS-only concurrency, §0.5(c), no server thread pool), a
capped sparkline canvas draw, a WIN/LOSE/FLAT/INCONCLUSIVE/SRM verdict chip
reusing the **same** `abk-prehorizon`/`abk-insufficient`/`abk-srm-fail`
marker classes `report.ts` and `explore.ts` already use (a withheld
pre-horizon or insufficient-data row IS the same peeking-honesty state, just
rendered as a chip instead of a chart annotation), per-row error isolation,
an idle/running job chip, and open/explore/run buttons wired to DASH-4's
routes with a log-tail drawer polling `/api/job/<id>`.

**Files touched:** `web/src/dashboard/dashboard.ts` (new);
`web/src/dashboard/payload.ts` (new — the boot/row/job wire-shape types,
mirrors `web/src/explore/payload.ts`'s role); `web/test/fixtures-dashboard.mjs`
(new); `web/test/smoke-dashboard.mjs` (new).

**Steps:**
1. `web/src/dashboard/payload.ts`: types for the boot payload (`project,
   initial_window, version, experiments: BootEntry[]`), the per-row stats
   reply (`ExperimentRow`, matching DASH-2's shape), and job snapshots —
   mirrors `payload.ts`'s role, not its content.
2. `window.__ABK_DASHBOARD__ = { render(payload, mount) }` — the required
   window-global assertion `build.mjs` checks (DASH-6).
3. Render: one row per boot entry immediately (name/tags/status, verdict
   cell = `'pending'` skeleton, no fetch yet); a bounded worker-pool loop
   (`const POOL_SIZE = 3; Math.min(POOL_SIZE, rows.length)` parallel async
   workers pulling names off a shared queue, matching the donor's
   `Vn=3`/`Promise.all` shape exactly, ported as a design pattern not code)
   calls `fetch(`/api/stats/${encodeURIComponent(name)}?window=${window}
   &token=${token}`)` per row and paints the reply into that row **only** (a
   fetch rejection or a `row.error` field paints that row's error cell and
   **continues** the pool — never aborts remaining rows).
4. Sparkline: reuse `web/src/shared/chart.ts`'s canvas scale/line-draw
   primitives (the same ones `report.ts`/`explore.ts` use) over the row's
   `spark:[[ts,effect]]` pairs — **not** a new charting primitive.
5. Verdict chip: `WIN`=`--abk-st-good`, `LOSE`=`--abk-st-serious`,
   `FLAT`=neutral, `INCONCLUSIVE`=`--abk-st-warn`, `SRM`=`--abk-st-critical`
   (`TOKEN_FALLBACKS` names already exist, `web/src/shared/chart.ts:37-57` —
   no new hex). A pre-horizon-withheld verdict (`row.is_horizon===false` and
   `verdict==='INCONCLUSIVE'`) renders class `'abk-note abk-prehorizon'`;
   `row.error!=null` OR an insufficient-data demotion renders
   `'abk-insufficient'`; `row.srm_flag` renders `'abk-srm-fail'` on the chip
   — the 3 literal marker strings the CI marker gate greps for in the
   compiled `dashboard.js`.
6. Job/idle chip + drawer: fetch `/api/jobs` on an interval (or on-demand
   after a button click) to show `'idle'` vs `'<kind> <experiment>'` (the
   donor's chip-text convention), a click opens a log drawer that polls
   `/api/job/<id>?offset=N` and appends new lines (the donor's
   **absolute**-offset scheme from `JobManager.snapshot`, DASH-1 — the
   client must track `next_offset` per job, not just append blindly).
7. Buttons: **Open** → new tab/iframe to a full report render (reuse the
   **same** `abkit/reporting` `render_report_html` the CLI `--report` flag
   emits, served as a new `GET /experiment/<name>` route added to DASH-3's
   server in this WP if not already stubbed); **Explore** → `POST
   /api/explore` then `window.open(reply.url)`; **Run** → `POST /api/run`
   then switch the job chip to running and open the drawer. The expanded row
   additionally renders one **Run** per configured comparison (from the boot
   entry's `comparisons` list, DASH-2 step 7), posting `metric` alongside
   `select` — the §3 per-metric requirement. Both spawn the same
   one-at-a-time pipeline job, so the gate's 400 handling is shared.
8. Full-window reload **never** happens for the list (boot payload is
   fetched exactly once per page load) — only Open (report) and Explore
   trigger a full reload, and only for that **one** row/tab, matching the
   REPORT constraint verbatim.

**Tests & gates:**
- `web/test/smoke-dashboard.mjs` (model on `web/test/smoke-explore.mjs`,
  jsdom + a fake `window.fetch` recording calls): the bundle exposes
  `window.__ABK_DASHBOARD__.render`; a static boot payload renders every row
  as `'pending'` with **zero** fetch calls before the pool starts; a
  canned-reply fetch fake proves at most 3 concurrent in-flight requests at
  any instant (assert via a counter that never exceeds 3, mirroring the
  `Vn=3` contract); one row's stats reply carrying `error:'boom'` paints
  that row's error state and the other rows still resolve normally
  (isolation); a row with `srm_flag:true` renders the `abk-srm-fail` class; a
  row with `is_horizon:false`/`verdict:INCONCLUSIVE` renders
  `abk-prehorizon`; job drawer polling advances offset monotonically and
  never re-renders already-seen lines twice.
- `npm run check --workspace web` (`tsc --noEmit`) passes with the new
  `dashboard.ts`/`payload.ts` sources.

**Risks / hotspots:** jsdom has no real network concurrency limiting, so the
"≤3 concurrent" assertion must be enforced by the test's fake fetch counting
in-flight calls, not by timing — a flaky timing-based test here would be a
real regression risk given the `smoke-explore.mjs` precedent already avoids
timing assertions. Reusing `chart.ts`'s scale/line primitives for a much
smaller sparkline (row-height, not full chart) may need new margin/size
presets in `chart.ts` — check whether a tiny-sparkline mode already exists
before assuming the existing API fits unchanged.

**Session estimate:** 2 sessions.

**As built (PR #73, 2026-07-31) — what DASH-6/DASH-7 must know.** All eight steps
shipped in one session, plus the two decisions DASH-2 delegated and the report
route step 7 asked for. The bundle is
`abkit/tuning/assets/dashboard.js` (31.7 KB), committed. What a later WP would
otherwise have to rediscover:

1. **The two DASH-2 decisions, decided.** (a) The `abk-insufficient` chip reads a
   NEW row field — `insufficient`, the HEADLINE look's own persisted
   `insufficient_data` cell, added to `overview.py`'s row (the plan's "either
   reads it (a persisted column, not a re-derived decision) or drops the chip").
   The alternative was matching the readout's English rationale in JS, which a
   wording edit would silently break. It goes through the readout's OWN `_flag`
   (imported, not copied: `reporting/builder._flag01` is plain `bool(value)`
   while the readout is `bool(int(value))`, and they disagree on a `"0"` string
   cell — the chip must side with the branch that decided the verdict). No
   display window can move it: the flag is read off the headline's cutoff in the
   UNwindowed pair series, and `_pair_rows` is now called before the window
   filter (a suffix of a suffix is the same suffix, so the sparkline is
   byte-identical). (b) `verdict: null` + `error: null` renders "no data — press
   Run"; with an `error` it renders the error chip. **Deviation from step 5:** an
   errored row does NOT reuse `abk-insufficient`. The plan suggested it because
   no insufficiency field existed; now that one does, the marker keeps the
   report's meaning ("this look was demoted, counts only") instead of also
   meaning "this row failed to build", which is a different state with its own
   `abk-v-error` class.
2. **`GET /experiment/<name>` is the Open button's target** (step 7's route, and
   the one route that answers HTML). It renders the SAME
   `build_report_payload` → `render_report_html` pair `abk run --report` writes,
   for one experiment, on demand — never a `reports/` file off disk, which exists
   only if someone passed `--report` and would be as old as that run. It needed
   two optional server fields, both defaulted and both **DASH-6's to wire from
   `abk dashboard`** (which already holds them): `metrics=` (the project's
   `MetricConfig`s → the report's metric descriptions) and `manager=` (the raw
   manager `tables` wraps → the no-copy default's live cohort snapshot for the
   SRM chip's observed counts). Without them the page still renders: no
   descriptions, and zero observed units with the reason in the payload's
   warnings — a silent "0 / 0" beside a green chip would read as a broken
   cohort. A cohort source that raises (the m8 direct-mode validation, which
   `abk explore` turns into a CLI error) also costs only the counts: the render
   falls back to the manager-less build and names the exception. A failure the
   retry reproduces is a genuinely broken read and stays a 500. The DB half runs
   under `db_lock`; the bake does not (the lock guards the connection, not CPU).
3. **Both HTML replies now send `Cache-Control: no-store`**, which only the JSON
   ones did. Same reason, and it bites harder here: a heuristically cached
   report would show the readout from before the Run the operator just launched.
4. **The build wiring moved up from DASH-6 (step 1 of it), because DASH-5's own
   test gate needs the artifact**: `web/build.mjs` has the third `BUNDLES` entry
   (global `__ABK_DASHBOARD__`, the same three marker strings), `npm test`'s
   `pretest` builds it, and `web/test/smoke-dashboard.mjs` loads the COMMITTED
   file the way `smoke-explore.mjs` does. DASH-6 still owns the CI **wheel
   namelist** tuple (`ci.yml`'s hardcoded two-bundle literal — the marker/hex
   gates already glob), the `abk dashboard` command, and the docs. The
   `render_dashboard_html` degradation to `_PENDING_DASHBOARD_JS` is now
   unreachable in a built checkout; **DASH-6 should decide whether it stays**
   once the namelist asserts the bundle (the explore precedent raises instead).
5. **`chart.ts` needed no new preset** (the plan's risk note asked): a local
   `SPARK_MARGINS = {l:3,r:3,t:5,b:5}` plus `makeScales`/`plotRect`/
   `drawSeriesDecimated`/`drawHLine` compose a row-height sparkline as-is. Zero
   is always in the value domain (an effect reads against it, and a floating
   baseline makes a tiny series look huge), a null bucket is a NaN so a gap
   stays a gap, and the x axis is the emitted TIMESTAMP, never the index —
   the server buckets by stride, so buckets are time-irregular by construction.
6. **jsdom hangs `node --test` unless each window is closed.** The dashboard
   polls `/api/jobs` forever (an 8 s idle re-arm; it is a live cockpit), so a
   window left open keeps a pending timer and the runner never exits — the suite
   closes every window in an `afterEach`. That is also what exercises the
   client's teardown (`render` is idempotent: it drops the prior page's timers,
   aborts its fetches and bumps the fill epoch, so an in-flight reply cannot
   paint into a torn-down page).
7. **Two client contracts worth not re-deriving.** The `≤3 concurrent` assertion
   counts in-flight STATS requests only — the job-chip poll is its own single
   request, and folding it in makes the bound read as 4. And `pipeline_active`
   comes off the reply: the chip's TEXT names running jobs from their own
   `status` field, but nothing in JS re-derives `kind ∈ PIPELINE_KINDS ∧
   running`, and the Run button is only *hinted* as busy (dashed border, still
   clickable) because the flag is advisory and the route's 400 is the authority.
8. **Review record.** A self-review pass over the new client found four defects,
   all fixed before this note: a stats repaint rebuilt the whole expanded detail
   (collapsing an open YAML pane, dismissing a confirm box mid-read, and
   dropping an in-flight source reply into a detached node — the detail is now a
   persistent shell plus a refreshable readout block, and the smoke test that
   caught it asserts the YAML survives a repaint); the drawer's poll timer was
   closure-local and survived a re-render (it has a `dispose()` now, in the
   disposer list); `jobStatus` grew one entry per job ever seen (pruned to the
   registry's own list, safe because a running job is never evicted); an empty
   sparkline kept the previous window's tooltip; and one dead `RowView.note`
   seam nothing called was removed. Final: 37 jsdom tests over the committed
   bundle, 2819 Python tests (+38), `tsc --noEmit` clean, ruff/black clean, mypy
   111 = baseline, `ALGORITHM_VERSION` absent from the diff.

---

### DASH-6 — Build wiring, CI gates, CLI command, docs

**Goal:** wire `dashboard.ts` into the committed build pipeline (3rd
`build.mjs` entry, per-bundle markers), add the `abk dashboard` CLI command,
and extend/verify (never blindly assume) the existing CI freshness/marker/
hex gates cover the new bundle path.

**Files touched:** `web/build.mjs` (add 3rd `BUNDLES` entry);
`abkit/cli/commands/dashboard.py` (new); `abkit/cli/main.py` (register the
command); `docs/guides/dashboard.md` (new); `.claude/rules/` +
`abkit/cli/assets/claude/` (mirror docs three-way sync per CLAUDE.md
invariant 6); `CHANGELOG.md`.

**Steps:**
1. ~~`web/build.mjs`: add the third `BUNDLES` entry.~~ **Shipped in DASH-5**
   (PR #73): its own test gate loads the COMMITTED bundle, exactly like
   `smoke-explore.mjs`, so the entry had to exist a WP earlier. Verify it is
   still there (global `__ABK_DASHBOARD__`, the same 3 markers as
   `report.ts`/`explore.ts`) rather than adding a second one.
2. ~~Commit `abkit/tuning/assets/dashboard.js`.~~ **Shipped in DASH-5** for the
   same reason. This WP still re-runs `cd web && npm run build` and commits any
   drift, because the freshness gate applies unconditionally via the glob
   pathspec.
3. **Verify** the two glob-based `ci.yml` gates need zero edits for the new
   bundle: the marker-grep loop already iterates `abkit/*/assets/*.js` —
   `dashboard.js` is auto-covered; the hex-containment gate only scans
   `abkit/reporting/html_report.py` + `abkit/tuning/html.py` — since
   `render_dashboard_html` reuses the exact `_FAVICON` constant (DASH-3), no
   new hex is introduced and `html.py` is already in that scan list;
   explicitly **re-run the gates locally** against the new files to confirm
   before relying on "no edit needed" (§0.5(f)).
3a. **Edit the one gate that IS hardcoded**: the wheel packaging-DoD job's
   bundle namelist is a literal tuple
   `("abkit/reporting/assets/report.js", "abkit/tuning/assets/explore.js")`
   (`ci.yml:297`) — add `"abkit/tuning/assets/dashboard.js"` (or refactor
   the loop to iterate the `BUNDLES` paths generically) in the same PR, so
   the wheel gate actually asserts the third bundle ships (§0.5(f); the §4.3
   release checklist depends on this line being edited, it is not
   pre-covered).
4. `abkit/cli/commands/dashboard.py`: `run_dashboard(select, exclude,
   profile, window, no_open)` modeled on `run_explore`'s orchestration shape
   (`abkit/cli/commands/explore.py`) — `load_project_context(
   require_profiles=True)`, `select_experiments(context.root, select,
   exclude)` (**no** single-experiment restriction, unlike explore —
   dashboard serves the **whole** selection), build `InternalTablesManager`
   via `context.manager_factory(profile)()`, call
   `abkit.tuning.build_dashboard_server`/`serve_dashboard` (DASH-3).
5. `abkit/cli/main.py`: `@cli.command() def dashboard(select, exclude,
   profile, window, no_open)` — register next to the explore command block
   (`main.py:151-182`), following the identical `--select`/`--profile`/
   `--no-open` option shape plus a new `--window` (default `'30d'`, choices
   from `WINDOW_PRESETS`).
6. `docs/guides/dashboard.md`: usage doc mirroring
   `docs/guides/notification-channels.md`'s tone — what it is (a launcher,
   not a monitor), what it never does (no in-process pipeline runs, no
   pipeline lock, no CRUD YAML editing in phase 1), the 4 buttons.
7. `CHANGELOG.md` `[Unreleased]`: "Added: `abk dashboard` — the
   project-level monitoring cockpit" entry, explicitly noting **no
   statistical numbers changed** (mirrors the 0.1.2 entry's framing).

**Tests & gates:**
- CI green on: bundle freshness (`git status --porcelain -- ':(glob)
  abkit/*/assets/**'` empty after commit), marker grep, hex-containment,
  jsdom smoke (DASH-5), `tsc --noEmit`, ruff/black on the new Python files.
- `abk dashboard --select '*' --no-open` against a scratch fixture project
  starts, prints a URL, and a Ctrl-C-equivalent (SIGINT in the test) exits
  cleanly without leaving jobs orphaned (`server.jobs.shutdown()` called) —
  a CLI-level smoke test in `tests/cli/test_dashboard_command.py`.

**Risks / hotspots:** the "no `ci.yml` edit needed" claim rests on the
favicon-hex-reuse assumption in DASH-3 — if a future dashboard-specific
visual tweak introduces a new hex in `html.py`, the hex-containment gate
**will** catch it (good), but this WP's steps must not silently assume
that's impossible.

**Session estimate:** 1 session.

**As built (PR #74, 2026-07-31) — what DASH-7 must know.** All seven steps
shipped (1 and 2 were already true from DASH-5 and were re-verified, not
re-done), plus the decision DASH-3 note 8 / DASH-5 note 4 delegated here. What
a later WP would otherwise have to rediscover:

1. **§0.5(f)'s split verdict held, and was *checked* rather than assumed.** The
   marker-grep loop (`for bundle in abkit/*/assets/*.js`) and the
   hex-containment scan (`html.py` is already in its file list; the dashboard
   page shell reuses explore's two hexes and `_FAVICON`) both pass unmodified
   against the new file — re-run locally, all 9 marker checks and all 8 hexes
   green. The freshness gate needed nothing either (`npm run build` reproduced
   all three bundles byte-identically). **Only the wheel namelist was
   hardcoded**, as predicted: `dashboard.js` was added to `ci.yml`'s tuple —
   and to `tests/e2e/test_release_readiness.py`'s self-contained-bundles tuple,
   the second hardcoded list the plan did not name (the source-tree half of the
   same DoD: no external host in the shipped artifact). `pyproject.toml` needed
   no change — `"abkit.tuning" = ["assets/*.js"]` is a glob and already shipped
   the file; the gate's value is precisely that the glob cannot prove it.
2. **The pending-note degradation is REMOVED (the delegated decision).** Both
   readers now go through one undegrading `html._bundle(name)`, so a missing
   `dashboard.js` raises exactly as a missing `explore.js` does. The condition
   the explore law was keyed to — committed AND named in the wheel namelist —
   became true for the dashboard in this WP, and the alternative was keeping
   ~20 lines of unreachable JS with a second contract of its own (the old test
   asserted the *note* satisfied the window-global contract). The replacement
   test asserts what can now be asserted: the committed file appears in the
   page verbatim, and each reader raises when the resource root has no such
   file — proved by pointing `html.files` at an empty directory, because a
   hijacked-reader probe would prove nothing when the law IS "no fallback path".
3. **`run_dashboard` is deliberately thinner than `run_explore`**, and three
   divergences are load-bearing rather than cosmetic (all three are pinned):
   a never-run project **serves** instead of no-opping (its rows are the "no
   data — press Run" state, and Run is the fix); there is **no startup orphan
   scan** (explore's is one query for its one experiment — per row here it would
   put N warehouse queries in front of a metadata-only boot, contradicting
   DASH-3's whole design, so the orphan warning stays on the per-experiment
   commands); and an empty selection takes the `abk run`/`abk validate` idiom
   (`echo_done("Nothing selected.")`, exit 0, no server built) rather than
   explore's non-zero refusal, which exists only because explore needs exactly
   one.
4. **The startup "abkit is not installed" warning DASH-4 note 7 deferred here
   shipped** (`_spawned_jobs_can_import_abkit`). Every button spawns
   `python -c` with `''` and the project root dropped from `sys.path`, so from a
   bare uninstalled checkout EVERY job dies with the same
   `ModuleNotFoundError` in its own drawer — said once, before the page opens.
   It is probed **in-process, and only a conjunction warns**: `importlib.metadata`
   alone would false-alarm on a `PYTHONPATH` install (no dist-info, jobs work),
   and a `sys.path` probe alone would false-alarm on a *strict* editable install
   (setuptools registers a meta-path finder, so nothing on `sys.path` resolves
   `abkit`). Spawning `abk --version` to ask would be the most faithful probe and
   costs a process at every startup for the same answer. It warns, never refuses:
   the read-only rows work regardless.
5. **`--window` is a plain string option, not a `click.Choice`.** Choices would
   have to be read from `WINDOW_PRESETS` at *decorator* evaluation time, i.e. at
   `abkit.cli.main` import — which imports `tuning.overview` → `pipeline.readout`
   → numpy, breaking the lazy-group contract that keeps `abk --version` instant.
   Instead `build_dashboard_server`'s existing boot-time `validate_window_preset`
   raises `UnknownWindowPreset` (before the socket is bound) and the CLI turns it
   into a house `ClickException` naming the presets. The cost is honest: the
   `--help` text names the presets in prose, i.e. it IS the second copy DASH-3
   note 10 warns about — so it is **pinned in both directions** against
   `ALL_WINDOW_PRESETS` and `DEFAULT_WINDOW_PRESET`
   (`TestWindowHelpStaysInLockstep`), which is the project's answer to a mirror
   that cannot be eliminated. The `UnknownWindowPreset` handler is the ONLY
   startup translation: the server's other refusal (a duplicated experiment name)
   is left to raise, because `select_experiments` already enforces uniqueness over
   the one global namespace, so reaching it is an abkit bug and a bug deserves its
   traceback rather than a tidy `Error:` line.
6. **The `metrics=`/`manager=` wiring DASH-5 left here is asserted, not
   assumed** (`tests/cli/test_dashboard_command.py::TestWiring`): `manager` must
   be the SAME object `tables` wraps (one connection, `db_lock`-serialized), and
   dropping either kwarg fails the test — mutation-probed, along with the
   no-pipeline-lock spy, the no-schema-created assertion and the job-registry
   teardown. The no-schema test had to assert **table existence**, not row
   counts: `ensure_tables()` creates empty tables, so a row-count probe passed
   under an injected `ensure_tables()` call and was a test that could not fail.
7. **What DASH-6 did NOT touch, on purpose:** `.claude/rules/{architecture,
   contributing}.md`. The plan's file list names them, but those two bodies
   describe the system **as shipped** and carry the milestone status line — M11
   is not shipped until DASH-7's exit gate. The *operator* body (the third
   single-source body, `abkit/cli/assets/claude/`) IS updated here, since it
   ships in the wheel with this command: the `cli.md` rule gained an `abk
   dashboard` section + the selector/`--exclude` lines, `explore.md` a
   project-level pointer, `overview.md` the readout-emitter list, and
   `CLAUDE.section.md`'s routing row now names the dashboard. No new operator
   rule *file* was added, so `RULE_TO_DOCS` and the "9 rules" count that four
   other files quote stay true. **DASH-7 (or the release step) owns the
   `.claude/rules/` + root `CLAUDE.md` M11 flip.**
8. The docs site's page list and sidebar are both manual (`website/scripts/
   sync-docs.mjs` `PAGES`, `website/astro.config.mjs` `sidebar`) — a new
   `docs/guides/*.md` reaches the site only by editing both, and `sync-docs`
   reports an unresolved-link warning (not an error) for any doc that links a
   page missing from `PAGES`. Both are edited here; `sync-docs` runs clean for
   every dashboard link.

---

### DASH-7 — Exit gate: e2e dashboard session + 2 adversarial review rounds

**Goal:** the milestone's exit gate per the project's established
discipline: one end-to-end test driving the real server + a real (stubbed)
`abk` subprocess through boot→stats→run→job-poll→done, plus two adversarial
review passes focused on the two named dtk-tune-pattern deltas (GET auth, no
self-shutdown — §0.5(b)), row error-isolation, and the one-job-at-a-time
gate. Note: the design JSON names the exit-gate spec deliverable
`docs/specs/dashboard-implementation-plan.md`; **this file**
(`docs/specs/m11-implementation-plan.md`) is that deliverable, following the
project's `m4`/`m5`/`m6` naming convention instead — DASH-7 amends *this*
document into the implementation record rather than authoring a second one.

**Files touched:** `tests/e2e/test_dashboard_session.py` (new); this file
(`docs/specs/m11-implementation-plan.md`, amended in place at the exit gate
— not a second new doc).

**Steps:**
1. `tests/e2e/test_dashboard_session.py`: build a scratch abkit project
   (reuse `tests/e2e/test_explore_session.py`'s fixture-project pattern),
   seed a couple of experiments with a few `_ab_results` rows each (one
   clean WIN, one with a bad/malformed comparison to exercise row-error
   isolation, one mid-horizon INCONCLUSIVE), start `build_dashboard_server`,
   drive `GET /` (assert metadata-only, no verdict fields present),
   `GET /api/stats/<name>` per experiment (assert verdict/effect/spark
   present, the malformed one carries error+nulls), `POST /api/run` against
   a real (test-fixture) `abk` invocation in the scratch project end-to-end
   (not stubbed) confirming a **second** concurrent `/api/run` 400s, poll
   `/api/job/<id>` to completion, `GET /api/jobs` shows it done, then a
   Ctrl-C-equivalent shutdown leaves no dangling subprocess (psutil or
   `/proc` check).
2. Round 1 review (self or paired): re-verify the two dtk-tune-pattern
   deltas against the **actual committed** `dashboard_server.py` (not the
   plan) — GET without `?token=` must 403 on every route including `/`, and
   no code path anywhere calls `server.shutdown()`/
   `threading.Thread(target=srv.shutdown,...)`.
3. Round 2 review: adversarial focus on `JobManager` reuse correctness
   (DASH-1's kind-vocabulary fork resolved consistently across
   DASH-1/4/5), sparkline point-cap enforcement under a synthetic 50k-row
   experiment (perf/memory, not correctness), and that no route in
   `dashboard_server.py` ever calls `InternalTablesManager.
   acquire_lock`/`release_lock` (the "launcher only, no pipeline lock"
   invariant, §0.5(d)).
4. Amend this file's status line + append an "Adversarial review record"
   section summarizing both rounds' findings, following the `m4`/`m6` §5
   pattern — the shipped design (DASH-1..7, the row-per-experiment decision
   from DASH-2 §0.5, the two dtk-tune-pattern deltas) becomes the record for
   the same audience `m4`–`m6`'s specs serve.

**Tests & gates:**
- `tests/e2e/test_dashboard_session.py` green in CI (added to the existing
  e2e job, `.github/workflows/ci.yml`'s e2e step).
- Both adversarial review rounds produce written findings (even if "none
  found") attached to the PR, matching the project's 2-round discipline.

**Risks / hotspots:** an e2e test that spawns a **real** `abk run`
subprocess from inside pytest needs the scratch project's `profiles.yml`
pointed at whatever the existing e2e suite already uses (a Docker-free test
manager, if any) — reuse `tests/e2e/test_explore_session.py`'s manager
fixture rather than inventing a new one.

**Session estimate:** 1 session.

---

## 2. Exit gate

Per the design JSON `exit_gate` (DASH portion) + the track-wide discipline:

- `tests/e2e/test_dashboard_session.py` green — boot→lazy-stats→run→
  job-poll→done, with one experiment forced to a row-error to prove
  isolation.
- CI's bundle/marker/hex/token gates green **with the 3rd `dashboard.ts`
  entry** landed (DASH-6).
- `abk dashboard` documented (`docs/guides/dashboard.md`) and callable.
- **2 adversarial review rounds** specifically re-verifying: (1) the two
  named dtk-tune-pattern deltas (token gates GET, no self-shutdown) against
  the actual committed server code, not the plan; (2) the "no pipeline lock"
  invariant (spy on `acquire_lock`/`release_lock`); (3) the `JobManager`
  kind-vocabulary fork resolved consistently across DASH-1/4/5.
- Zero `abkit/stats` changes; the `ALGORITHM_VERSION` grep stays empty.
- `CHANGELOG.md` entries landed; the three-way docs sync (`docs/` +
  `.claude/rules/` + `abkit/cli/assets/claude/`) verified in the same PR.

## 3. Open questions / before-start decisions

From the design JSON `open_questions` (DASH-relevant only — the NTF-relevant
ones live in [m12-implementation-plan.md](m12-implementation-plan.md)) and
the source plan's "Перед стартом" line for M11:

- **~~Dashboard row grain: one row per EXPERIMENT, or one row per (experiment ×
  comparison)?~~ RESOLVED by the maintainer 2026-07-27: one row per
  EXPERIMENT** (the plan's recommendation, accepted). The row carries the
  headline main-metric verdict; expanding it lists the comparisons. The
  question was real because REPORT's "the list of experiments AND their
  metrics" phrasing admits both readings, but either grain is bounded by the
  same `evaluate()` contract (DASH-2 Goal): only main-metric × treatment pairs
  carry verdicts, so row-per-comparison would NOT have unlocked
  secondary-metric verdicts without the M14 decision-layer readout work.
  DASH-2's row key is therefore the experiment name, and DASH-5 renders one
  list entry per experiment.

- **~~Can an operator recompute ONE metric of an experiment from the
  dashboard?~~ RESOLVED by the maintainer 2026-07-27: yes, this must be
  possible** — where the affordance lives (a per-metric button inside the
  expanded row, or a metric picker on the experiment's Run) is a UI choice, the
  capability is not. This is a **CLI gap, not a UI gap**: `abk run` is the only
  per-comparison command with no `--metric` (`validate`, `plan`, `explore` and
  `verify-incremental` all have one), and the dashboard is a launcher — it can
  never offer what the CLI cannot do. Hence the new **DASH-4a** work package
  below, which DASH-4/DASH-5 then wire. Two consequences elsewhere: DASH-2's
  boot entries must carry the experiment's full comparison list (step 7), since
  a metric with no verdict still needs a button, and DASH-5's expanded row is
  where that button goes (step 7).

No other open questions from the shared design JSON apply to the DASH track;
the remaining four (default channel selection, cooldown-vs-dedup semantics,
the explore-Apply calibration-red hook, and whether `abk explore` gains its
own `--notify` flag) are all NTF-* and belong to M12.

## 4. Dependencies

### 4.1 Intra-track (DASH-1..7)

```
DASH-1 (JobManager port) ─┐
                           ├─▶ DASH-3 (server skeleton) ─▶ DASH-4 (job routes) ─▶ DASH-5 (dashboard.ts) ─▶ DASH-6 (build+CLI+docs) ─▶ DASH-7 (exit gate)
DASH-2 (overview.py)      ─┘                                    ▲
DASH-4a (`abk run --metric`, pipeline/CLI) ─────────────────────┘
```

DASH-4a is independent of DASH-1/2/3 (it touches the pipeline and the CLI, not
the dashboard) and can be taken in any earlier slot; it only has to land before
DASH-4 wires the route that spawns it.

DASH-1 and DASH-2 are parallel (no shared files — DASH-1 touches
`abkit/tuning/jobs.py`, DASH-2 touches `abkit/tuning/overview.py`); DASH-3
needs both (the server skeleton wires `JobManager` job routes and calls
`overview.build_overview_boot_entries`). DASH-3 through DASH-7 are strictly
sequential — each builds directly on the previous WP's files.

### 4.2 Inter-milestone collisions

- **M11 clones `tuning/server.py` *after* M10 WP4** (§0.5(e)) — the
  decoupled lock model (`heavy_lock` scoped to reload/validate/apply only)
  must already be in place before DASH-3 starts, or DASH-3 inherits a stale
  lock pattern from the pre-M10 file. See
  [m10-implementation-plan.md](m10-implementation-plan.md) WP4.
- **M14's dashboard surface builds on M11.** The multi-arm decision layer
  (treatment-vs-treatment verdicts, a cross-arm overview) extends the
  dashboard DASH-2/DASH-5 ship here — M14 does not modify M11's shipped
  contract, it adds to it (see [ROADMAP.md](../../ROADMAP.md) M14).
- **M8's `build_cohort_backend`/`ab_cohort_source` factory and M9's additive
  STATE engine are out of this milestone's blast radius** — M11 never
  touches cohort-source SQL or the compute engine; it only reads already-
  persisted `_ab_results` rows through the unmodified `load_results` +
  `readout.evaluate()`.
- **DASH and NTF (M12) are fully independent** — no shared files, can run in
  parallel across two contributors/sessions. Coordinate PR ordering only if
  M12's `_ab_notify_states` schema addition and M9's additive-engine schema
  addition land concurrently (both touch `abkit/database/tables.py`) — not a
  DASH-track concern, noted here only for completeness.

### 4.3 Release checklist (this milestone's `0.6.0`)

Per the track-wide discipline: three-way docs sync (`docs/` +
`.claude/rules/` + `abkit/cli/assets/claude/`), the wheel-namelist gate
(assert `dashboard.js` ships, alongside `report.js`/`explore.js`), and the
`pip install`-smoke job before tagging `v0.6.0` → `publish.yml`.
