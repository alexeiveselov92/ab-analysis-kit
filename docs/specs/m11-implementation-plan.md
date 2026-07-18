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
`abk run --report` would show for the same window).

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
   `end_ts` (donor's `overview.py:38-39` pattern, field renamed from the
   metric-timestamp axis to `end_ts`).
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
   (`experiment_config.py:393-395`). Guard the theoretical empty-`verdicts`
   case (defensively, not as a designed state) by degrading the row via
   step 6's error path rather than indexing blind.
5. Row shape (mirrors `_empty_row`'s full-shape-with-error-degrade
   discipline, `detektkit/detectkit/ui/overview.py:293-319`): `{name, dir,
   file, tags, status, start_date, end_date, main_metric, locked (via
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
   start_date/end_date/main_metric — **no** stats, **no** DB read), mirroring
   `detektkit/detectkit/ui/server.py`'s `metric_entries()`.

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
  show for the same window).

**Risks / hotspots:** REPORT's phrasing "the list of experiments AND their
metrics" is genuinely ambiguous between row-per-experiment (chosen here) and
row-per-comparison — flagged as an open question (§3); if the maintainer
wants row-per-comparison instead, DASH-2/3/5's row key changes from
experiment to `(experiment, metric, name_1, name_2)`, and the JobManager
dedup key from DASH-1 stays experiment-scoped regardless (buttons remain
experiment-scoped either way).

**Session estimate:** 1 session.

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
   (`urllib.parse.unquote`) → `build_experiment_row_safe(..., window)` under
   `db_lock`, JSON reply; `/api/jobs` → `jobs.list_snapshots()`;
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
   unknown); `job = srv.jobs.spawn_pipeline('run', f'run --select
   {select}', _run_argv(...), cwd=srv.project_root, env=_subprocess_env())`;
   `None` → 400 "a pipeline job is already running" (the donor's exact
   one-at-a-time UX, `detektkit/detectkit/ui/server.py:614-620`).
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
   then switch the job chip to running and open the drawer.
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
1. `web/build.mjs`: add `{ entry: path.join(here,'src','dashboard',
   'dashboard.ts'), outFile: path.join(REPO,'abkit','tuning','assets',
   'dashboard.js'), global: '__ABK_DASHBOARD__', markers: ['abk-prehorizon',
   'abk-insufficient','abk-srm-fail'] }` to `BUNDLES` — the same 3 markers as
   `report.ts`/`explore.ts` since DASH-5 deliberately reuses them.
2. Run `cd web && npm run build` and commit the resulting
   `abkit/tuning/assets/dashboard.js` in **this** PR (the CI freshness gate
   fails the build otherwise — this repo's committed-asset discipline
   applies unconditionally via the glob pathspec).
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

- **Dashboard row grain: one row per EXPERIMENT, or one row per (experiment ×
  comparison)?** This plan's assumption — and the plan's stated
  recommendation — is **one row per experiment**, matching the
  experiment-scoped open/explore/run/edit buttons (REPORT's "the list of
  experiments AND their metrics" phrasing could also mean row-per-comparison).
  This changes DASH-2's row key and DASH-5's list rendering if reversed,
  though not DASH-1/3/4's button plumbing (buttons stay experiment-scoped
  either way). Either grain is still bounded by the `evaluate()` contract
  (DASH-2 Goal): only main-metric × treatment pairs carry verdicts —
  row-per-comparison would NOT unlock secondary-metric verdicts without the
  M14 decision-layer readout work. **Decide before DASH-2 starts** —
  reversing it later reshapes the row schema DASH-3/4/5 all consume.

No other open questions from the shared design JSON apply to the DASH track;
the remaining four (default channel selection, cooldown-vs-dedup semantics,
the explore-Apply calibration-red hook, and whether `abk explore` gains its
own `--notify` flag) are all NTF-* and belong to M12.

## 4. Dependencies

### 4.1 Intra-track (DASH-1..7)

```
DASH-1 (JobManager port) ─┐
                           ├─▶ DASH-3 (server skeleton) ─▶ DASH-4 (job routes) ─▶ DASH-5 (dashboard.ts) ─▶ DASH-6 (build+CLI+docs) ─▶ DASH-7 (exit gate)
DASH-2 (overview.py)      ─┘
```

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
