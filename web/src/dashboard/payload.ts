// Contract for the abkit dashboard's baked boot payload + every wire reply the
// dashboard server (abkit/tuning/dashboard_server.py) answers.
//
// Keep this file in documented lockstep with the three Python modules it
// mirrors — same keys, same units:
//   * abkit/tuning/dashboard_server.py `_boot_payload`  → DashboardPayload
//   * abkit/tuning/overview.py `_empty_row` / `build_overview_boot_entries`
//                                                      → ExperimentRow / BootEntry
//   * abkit/tuning/jobs.py `JobManager.snapshot` / `list_snapshots`
//                                                      → JobSnapshot / JobSummary
//
// Two vocabularies that must never be mixed (the DASH-2 as-built rename):
// a BOOT entry carries `comparisons` — the CONFIGURED comparison list, which is
// where a secondary metric's per-metric Run button comes from — while a STATS
// row carries `verdicts`, the readout's per-pair verdict list, which only ever
// holds main-metric × treatment pairs. The client merges the two by experiment
// name, so one key holding two shapes would be a trap.
//
// All timestamps are integer ms-epoch UTC (naive-UTC instants on the Python
// side); NaN/±inf are scrubbed to null before they reach the wire.

// ----------------------------------------------------------------------------
// GET / — the baked, metadata-only boot payload (no statistics, no token)
// ----------------------------------------------------------------------------

/** One configured comparison of an experiment (boot entry, `is_main_metric`
 * straight off the config — a secondary metric appears HERE and never in a
 * row's `verdicts`). */
export interface BootComparison {
  metric: string;
  is_main_metric: boolean;
}

/** One experiment as the boot shell knows it: config metadata only. */
export interface BootEntry {
  name: string;
  /** parent directory relative to `paths.experiments` ("" = top level) — the
   * grouping key; posix-separated on every platform */
  dir: string;
  /** the YAML path relative to the project root (the "open in your editor"
   * target, and the same string `GET /api/experiment-source` echoes as `path`) */
  file: string;
  tags: string[];
  status: string | null;
  /** the experiment's OWN timezone — every instant below is naive UTC */
  timezone: string | null;
  start_ts: number | null;
  /** the EXCLUSIVE right edge (m10 D6) */
  horizon_ts: number | null;
  main_metric: string | null;
  comparisons: BootComparison[];
}

/** The payload baked into the dashboard page by `render_dashboard_html`. */
export interface DashboardPayload {
  project: string;
  profile: string | null;
  version: string;
  /** the preset `abk dashboard --window` booted with */
  initial_window: string;
  /** every accepted preset, shortest first with "all" last (server-derived, so
   * the selector never spells a second copy of the list) */
  window_presets: string[];
  generated_at: number;
  experiments: BootEntry[];
}

// ----------------------------------------------------------------------------
// GET /api/stats/<experiment> — one row, lazily fetched (DASH-2)
// ----------------------------------------------------------------------------

/** One (main metric × DECLARED arm pair) verdict inside a row's `verdicts`. */
export interface RowVerdict {
  metric: string;
  /** the report payload's arm vocabulary: control / treatment */
  pair: { c: string; t: string };
  verdict: string;
  /** m14 DEC-4. `vs_control` is a ship decision; `treatment_pair` is evidence
   * about two treatments and says nothing about either against the baseline.
   * Absent on a pre-`0.9.0` server, whose list is control-anchored anyway. */
  role?: 'vs_control' | 'treatment_pair';
  effect: number | null;
  caveats: string[];
  guardrail_regressed: boolean;
}

/** One main metric's arm-level summary (m14 DEC-2), as the row carries it. */
export interface RowRollup {
  metric: string;
  leader: string | null;
  indistinguishable: string[];
  separation: 'separated' | 'co_leaders' | 'untested' | 'no_leader';
  losers: string[];
  guardrail_regressed: string[];
  rationale: string[];
  caveats: string[];
}

/**
 * One experiment's statistics row.
 *
 * Every stat cell is the FULL series' — the window preset bounds `spark` and
 * nothing else (DASH-2 as-built (1)), so a row can never disagree with what
 * `abk run --report` shows. The client tests VALUES, never key existence: a
 * degraded row carries every key at its "no data" default plus `error`.
 */
export interface ExperimentRow {
  name: string;
  dir: string;
  file: string;
  tags: string[];
  status: string | null;
  timezone: string | null;
  start_ts: number | null;
  horizon_ts: number | null;
  main_metric: string | null;
  /** the pipeline ("run") lock is held — Run would refuse */
  locked: boolean;
  /** WIN | LOSE | FLAT | INCONCLUSIVE; null = no results yet (when `error` is
   * null) or the row degraded (when it is not) */
  verdict: string | null;
  srm_flag: boolean;
  srm_pvalue: number | null;
  effect: number | null;
  ci: [number | null, number | null];
  pvalue: number | null;
  /** the EFFECTIVE post-correction per-comparison alpha */
  alpha: number | null;
  elapsed_days: number | null;
  is_horizon: boolean;
  weekly_cycle_pct: number | null;
  /** the headline look's persisted `insufficient_data` cell — inference
   * withheld, counts only (the §4 `abk-insufficient` state) */
  insufficient: boolean;
  rationale: string[];
  caveats: string[];
  /** ORed across every listed pair */
  guardrail_regressed: boolean;
  /** the cutoff every stat cell above is as of (the HEADLINE pair's latest
   * look, not the experiment's latest row) */
  last_end_ts: number | null;
  /** ≤160 `[ms-epoch, mean effect | null]` buckets over the window */
  spark: Array<[number, number | null]>;
  verdicts: RowVerdict[];
  /** m14 DEC-4: the HEADLINE metric's leader and separation — the same scope
   * as `rationale`/`caveats` above, so every top-level cell describes one
   * thing. `null` on a pre-`0.9.0` server and whenever no arm beat control. */
  leader: string | null;
  separation: RowRollup['separation'] | null;
  /** every main metric's rollup, config order */
  rollups: RowRollup[];
  /** do the per-metric leaders coincide? `null` = fewer than two name one */
  leaders_agree: boolean | null;
  warnings: string[];
  /** `"<ExcType>: <message>"` when this ONE row degraded; null otherwise */
  error: string | null;
}

// ----------------------------------------------------------------------------
// The job registry (DASH-1) — GET /api/jobs, GET /api/job/<id>?offset=
// ----------------------------------------------------------------------------

export type JobKind = 'run' | 'unlock' | 'clean' | 'explore';
export type JobStatus = 'running' | 'done' | 'failed' | 'stopped';

/** One job's chip summary (`list_snapshots` — no `lines`). */
export interface JobSummary {
  id: string;
  kind: JobKind;
  /** the command an operator would have typed, derived from the argv that ran */
  label: string;
  /** null whenever the spawn named no single experiment — fall back to `label` */
  experiment: string | null;
  status: JobStatus;
  returncode: number | null;
  /** an explore job's scraped cockpit URL */
  url: string | null;
  started_at: number;
  finished_at: number | null;
}

/** `GET /api/jobs` — the list PLUS the server's own one-at-a-time flag, which
 * the client must never re-derive (DASH-3 as-built (3)). */
export interface JobsReply {
  jobs: JobSummary[];
  pipeline_active: boolean;
}

/**
 * One job's poll reply (`snapshot`). `offset`/`next_offset` are ABSOLUTE line
 * indices over the job's whole lifetime, so a job chattier than the server's
 * 5000-line buffer keeps streaming; lines that already fell off the front are
 * gone, and `dropped`/`truncated` say so rather than leaving the client to
 * infer it from a hole.
 */
export interface JobSnapshot {
  id: string;
  kind: JobKind;
  label: string;
  experiment: string | null;
  status: JobStatus;
  returncode: number | null;
  url: string | null;
  next_offset: number;
  dropped: number;
  truncated: boolean;
  lines: string[];
}

// ----------------------------------------------------------------------------
// The job routes (DASH-4) — every one of them spawns a real `abk` subprocess
// ----------------------------------------------------------------------------

/** `POST /api/run` — `{select}` runs the whole experiment; `metric` narrows it
 * to ONE configured comparison (DASH-4a). A field this route does not act on is
 * REFUSED unless it is null, so nothing else may ride along. */
export interface RunRequest {
  select: string;
  metric?: string | null;
}

/** `POST /api/unlock` | `/api/clean` | `/api/explore` — `{select}` only. */
export interface SelectRequest {
  select: string;
}

/** `POST /api/run` | `/api/unlock` | `/api/clean` — 200 as soon as the child
 * exists (400 "a pipeline job is already running" when one is). */
export interface SpawnReply {
  job_id: string;
}

/** `POST /api/explore` — a LONG request: it holds the response until the
 * spawned cockpit prints its URL (up to 90 s). */
export interface ExploreReply {
  job_id: string;
  url: string;
}

/** `POST /api/job/<id>/stop`. */
export interface StopReply {
  ok: true;
}

/** `GET /api/experiment-source/<name>` — the raw YAML text the editor opens
 * with. Read live off disk, so it can legitimately disagree with the parsed
 * config every other route uses (that is what `digest` is for). */
export interface SourceReply {
  name: string;
  /** the same root-relative string the row carries as `file` */
  path: string;
  yaml_text: string;
  /** the file exceeded the server's 512 kB cap */
  truncated: boolean;
  /** the concurrency token a save echoes back; `null` when truncated */
  digest: string | null;
  /** false when the text above is a PREFIX — saving it would drop the tail */
  editable: boolean;
}

/** `POST /api/experiment/save` — `{select, text}` plus the optimistic-
 * concurrency `digest` and the level-2 override `force`. */
export interface SaveRequest {
  select: string;
  text: string;
  digest?: string | null;
  force?: boolean;
}

/** `POST /api/experiment/create` — no `select`: the target does not exist yet,
 * and the file name comes from the config's own `name:`. */
export interface CreateRequest {
  text: string;
  folder?: string;
  force?: boolean;
}

/** `POST /api/experiment/save` | `/api/experiment/create` (UI-1). Carries the
 * refreshed selection, so a client never has to poll to find out whether the
 * row it just edited still exists. */
export interface WriteReply {
  name: string;
  path: string;
  /** the verbatim copy of the previous file; `null` for a create */
  archived: string | null;
  /** the digest of what is now on disk — the editor's next save token */
  digest: string;
  /** set when the saved text changed the experiment's `name:` */
  renamed_from: string | null;
  /** false when the written experiment is outside this cockpit's `--select` */
  in_selection: boolean;
  warnings: string[];
  experiments: BootEntry[];
}

/** `POST /api/experiment/delete` — the YAML only; the persisted rows stay. */
export interface DeleteReply {
  name: string;
  path: string;
  archived: string;
  warnings: string[];
  experiments: BootEntry[];
}

/** `GET /api/experiments` and `POST /api/reload` — the refreshable half of the
 * boot payload. */
export interface SelectionReply {
  experiments: BootEntry[];
  generated_at: number;
  warnings: string[];
}

/** The dashboard renderer's global entry, exposed by the bundled IIFE. */
export interface AbkDashboardGlobal {
  render(payload: DashboardPayload, mount: HTMLElement): void;
}
