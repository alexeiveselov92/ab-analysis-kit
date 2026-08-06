// abkit dashboard — the project-level cockpit (M11 DASH-5).
//
// Consumes the baked boot payload (./payload.ts, lockstep with
// abkit/tuning/dashboard_server.py `_boot_payload`) rendered by
// abkit/tuning/html.py, and drives the DASH-3/DASH-4 localhost server:
//
//   * the initial render is METADATA ONLY — one row per boot entry, every
//     verdict cell `pending`, zero requests (the boot payload is fetched
//     exactly once: with the page);
//   * a fixed-concurrency-3 client worker pool then fills the rows one
//     GET /api/stats/<experiment> at a time (the donor's `Vn=3`/`Promise.all`
//     shape — JS-only concurrency; there is no server-side pool or cache, and
//     `db_lock` serializes the one DB connection anyway);
//   * one row's failure is one row's error cell: a rejected fetch or a
//     `row.error` field paints THAT row and the pool keeps pulling;
//   * every mutation is a DASH-4 job route, i.e. a real `abk` subprocess, with
//     a log drawer polling GET /api/job/<id>?offset= on ABSOLUTE offsets.
//
// Written from scratch against the donor's PATTERNS (§0.5(a) — dtk ships no TS
// source for its cockpit, only a minified bundle) plus abkit's own primitives:
// the canvas sparkline goes through shared/chart.ts (the same scale/line code
// the report and explore charts use), the brand lockup through shared/logo.ts,
// and every color through the one brand-token layer — no new hex.
//
// Peeking honesty (data-contract-and-reporting.md §4) carries the same stable
// machine-checkable markers the other two bundles do, on the verdict chip and
// its note line rather than on a chart annotation — a withheld verdict is the
// same state, just rendered per row:
//   .abk-prehorizon   — pre-horizon look, fixed CIs not peeking-valid
//   .abk-insufficient — the headline look was DEMOTED (persisted
//                       `insufficient_data`), counts only
//   .abk-srm-fail     — the red SRM gate
//
// Bundled (esbuild → IIFE) to abkit/tuning/assets/dashboard.js, which assigns
// `window.__ABK_DASHBOARD__ = { render }`. Nothing is exported for ESM — the
// global is the public surface (AbkDashboardGlobal). Styling is injected once,
// scoped under the .abk-dashboard root class.

import {
  type Domain,
  type Margins,
  TOKEN_FALLBACKS,
  drawHLine,
  drawSeriesDecimated,
  fit,
  fmtDate,
  fmtP,
  fmtSigned,
  fmtTs,
  fmtVal,
  makeScales,
  plotRect,
  rgba,
  token,
} from '../shared/chart';
import { makeBrandLockup } from '../shared/logo';
import type {
  BootEntry,
  DashboardPayload,
  DeleteReply,
  ExperimentRow,
  ExploreReply,
  JobSnapshot,
  JobSummary,
  JobsReply,
  SelectionReply,
  SourceReply,
  SpawnReply,
  WriteReply,
} from './payload';

// ----------------------------------------------------------------------------
// Constants + tiny helpers
// ----------------------------------------------------------------------------

const ROOT_CLASS = 'abk-dashboard';

/** The donor's hardcoded `Vn=3`: at most three stats requests in flight. */
const POOL_SIZE = 3;

/** Sparkline canvas margins — a row-height strip, not a chart (no axes). */
const SPARK_MARGINS: Margins = { l: 3, r: 3, t: 5, b: 5 };

const MS_PER_DAY = 86400000;

/** `/api/jobs` cadence: brisk while anything runs, lazy when nothing does. */
const JOBS_POLL_BUSY_MS = 1200;
const JOBS_POLL_IDLE_MS = 8000;
/** `/api/job/<id>` cadence while the drawer follows a running job. */
const DRAWER_POLL_MS = 600;
/** Log lines kept in the drawer's DOM (the server's own buffer is 5000). */
const DRAWER_MAX_LINES = 2000;

/** What the "New experiment" box opens with (UI-1).
 *
 * Every key here is one the validator REQUIRES, and nothing else: a template
 * carrying optional knobs would teach them as mandatory, and a blank box would
 * make the first refusal be about a shape rather than about a value. The
 * window keys are the post-0.5.0 ones (`start_ts`/`horizon_ts`, the horizon
 * EXCLUSIVE), so a copy of this never reintroduces the renamed pair.
 */
const NEW_EXPERIMENT_TEMPLATE = `name: my_experiment
start_ts: 2026-01-01
horizon_ts: 2026-01-15
unit_key: user_id
assignment:
  query: SELECT user_id, variant, exposure_ts FROM assignments
  variants: [control, treatment]
  expected_split: {control: 0.5, treatment: 0.5}
comparisons:
  - metric: my_metric
    is_main_metric: true
    method: {name: t-test}
`;

function el(tag: string, cls?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function button(cls: string, text: string, title?: string): HTMLButtonElement {
  const b = document.createElement('button');
  b.className = cls;
  b.textContent = text;
  b.type = 'button';
  if (title) b.title = title;
  return b;
}

const dash = (v: number | null, fmt: (x: number) => string = fmtVal): string =>
  v === null ? '—' : fmt(v);

/** An error's message, whatever the runtime handed us. */
function message(err: unknown): string {
  if (err instanceof Error) return err.message || err.name;
  return String(err);
}

function isAbort(err: unknown): boolean {
  return err instanceof Error && err.name === 'AbortError';
}

/** Days between two ms-epoch instants, or null when either is missing. */
function daysBetween(from: number | null, to: number | null): number | null {
  if (from === null || to === null) return null;
  return (to - from) / MS_PER_DAY;
}

// ----------------------------------------------------------------------------
// Render
// ----------------------------------------------------------------------------

/** Per-row handle the fill pool and the job poller paint through. */
interface RowView {
  root: HTMLElement;
  /** back to the `pending` skeleton (a window change re-fills every row) */
  pending(): void;
  paint(row: ExperimentRow): void;
  paintError(detail: string): void;
  /** re-run the canvas draw (a resize changes the backing store) */
  redraw(): void;
}

let teardown: (() => void) | null = null;

function render(payload: DashboardPayload, mount: HTMLElement): void {
  injectStyle();
  if (teardown) teardown(); // idempotent re-render: drop prior timers/listeners
  const disposers: Array<() => void> = [];
  teardown = (): void => {
    for (const d of disposers) d();
    disposers.length = 0;
  };
  mount.classList.add(ROOT_CLASS);
  mount.innerHTML = '';

  const root = el('div', 'abk-root');
  mount.appendChild(root);

  // The token is NOT baked into the page (the served HTML is not a credential
  // at rest); it rides in the URL the cockpit printed, and every request —
  // GET and POST alike — carries it.
  const token_ = new URLSearchParams(window.location.search).get('token') ?? '';

  let windowPreset = payload.initial_window;
  /** Stats-fill epoch: a window change or a refresh supersedes the pool in
   * flight, and a superseded worker paints nothing (the house stale-drop
   * discipline — replies are adopted iff their epoch is still current). */
  let generation = 0;
  let statsAbort: AbortController | null = null;
  let pendingCount = 0;

  const rows = new Map<string, RowView>();
  const lastRows = new Map<string, ExperimentRow>();
  /** The last status seen per job id — the edge that refreshes a row. */
  const jobStatus = new Map<string, string>();
  let pipelineActive = false;

  // ---- transport ------------------------------------------------------------

  function url(path: string, query: Record<string, string> = {}): string {
    const params = new URLSearchParams({ ...query, token: token_ });
    return `${path}?${params.toString()}`;
  }

  async function failure(response: Response): Promise<Error> {
    // Every server error carries its detail in a text/plain body (never the
    // latin-1 status line), so surface that and fall back to the status.
    let detail = '';
    try {
      detail = (await response.text()).trim();
    } catch {
      detail = '';
    }
    const error = new Error(detail || `HTTP ${response.status}`);
    error.name = `HTTP${response.status}`;
    return error;
  }

  async function getJson<T>(
    path: string,
    query: Record<string, string> = {},
    signal?: AbortSignal,
  ): Promise<T> {
    const response = await fetch(url(path, query), signal ? { signal } : undefined);
    if (!response.ok) throw await failure(response);
    return (await response.json()) as T;
  }

  async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
    const response = await fetch(url(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw await failure(response);
    return (await response.json()) as T;
  }

  // ---- header ---------------------------------------------------------------

  const header = el('div', 'abk-header');
  root.appendChild(header);
  header.appendChild(makeBrandLockup());

  const titleRow = el('div', 'abk-h-top');
  header.appendChild(titleRow);
  const title = el('h1', 'abk-title', `dashboard · ${payload.project}`);
  titleRow.appendChild(title);
  titleRow.appendChild(el('span', 'abk-badge-page', `v${payload.version}`));
  if (payload.profile !== null) {
    titleRow.appendChild(el('span', 'abk-badge-page', `profile: ${payload.profile}`));
  }

  const meta = el('div', 'abk-meta');
  header.appendChild(meta);
  /** The header's count — re-read from `entries`, which a create/delete moves. */
  function setCountMeta(): void {
    const n = entries.length;
    meta.textContent = [
      `${n} experiment${n === 1 ? '' : 's'}`,
      `booted ${fmtTs(payload.generated_at)} UTC`,
    ].join(' · ');
  }

  if (token_ === '') {
    // Without it every route answers 403, so say so once instead of painting N
    // identical row errors.
    const warn = el(
      'div',
      'abk-warning',
      '⚠ this page was opened without its ?token= — every request will be refused. ' +
        'Reopen the URL `abk dashboard` printed.',
    );
    header.appendChild(warn);
  }

  /** A project-level notice (a reload that could not re-read the project).
   *
   * One line under the header rather than a per-row message: what it reports is
   * about the SELECTION, and painting it on every row would say N times what
   * happened once. Appended AFTER the token warning on purpose — that one is
   * the first thing anyone should read on a page whose every request will 403,
   * and it is `.abk-warning`'s first match. */
  const banner = el('div', 'abk-warning abk-banner');
  banner.style.display = 'none';
  header.appendChild(banner);
  function setBanner(text: string): void {
    banner.textContent = text;
    banner.style.display = text === '' ? 'none' : '';
  }

  // ---- controls (window preset, refresh, job chip) --------------------------

  const controls = el('div', 'abk-controls');
  root.appendChild(controls);

  const windowWrap = el('div', 'abk-seg');
  controls.appendChild(el('span', 'abk-ctl-label', 'sparkline window'));
  controls.appendChild(windowWrap);
  const windowButtons = new Map<string, HTMLButtonElement>();
  for (const preset of payload.window_presets) {
    const btn = button('abk-seg-btn', preset, `bound the sparkline to the last ${preset}`);
    if (preset === windowPreset) btn.classList.add('on');
    btn.addEventListener('click', () => {
      if (preset === windowPreset) return;
      windowPreset = preset;
      for (const [name, node] of windowButtons) node.classList.toggle('on', name === preset);
      // The window bounds the SPARKLINE only (every verdict cell is the full
      // series'), so this re-fills the rows; the boot payload is never re-read.
      fillRows(allNames());
    });
    windowButtons.set(preset, btn);
    windowWrap.appendChild(btn);
  }

  const refreshBtn = button('abk-btn abk-btn-ghost', 'Refresh', 're-read every row');
  refreshBtn.addEventListener('click', () => fillRows(allNames()));
  controls.appendChild(refreshBtn);

  // UI-1's two project-level affordances. `Refresh` above re-reads the
  // WAREHOUSE for rows that already exist; `Reload configs` re-reads the YAML
  // on DISK, which is what picks up an experiment added by an editor, a `git
  // pull`, or an `abk explore` Apply — the M11 follow-up that used to require
  // a restart.
  const reloadBtn = button(
    'abk-btn abk-btn-ghost',
    'Reload configs',
    're-read the project’s experiment YAML from disk',
  );
  controls.appendChild(reloadBtn);

  const newBtn = button('abk-btn', 'New experiment', 'write a new experiment YAML');
  controls.appendChild(newBtn);

  const fillChip = el('span', 'abk-chip abk-chip-fill', 'idle');
  controls.appendChild(fillChip);

  const jobChip = button('abk-chip abk-chip-job', 'jobs: idle', 'open the job drawer');
  jobChip.addEventListener('click', () => drawer.toggleList());
  controls.appendChild(jobChip);

  // ---- the list -------------------------------------------------------------

  const list = el('div', 'abk-list');
  root.appendChild(list);

  /** The served selection, as the boot payload spells it.
   *
   * A `let`, not `payload.experiments`, since UI-1: create / delete / rename /
   * reload all change the SET, and every derived thing (the list, the fill
   * queue, the header count) reads this one array so they cannot disagree
   * about which experiments exist. */
  let entries: BootEntry[] = payload.experiments;

  /** (Re)build the row list from `entries`.
   *
   * Called at boot and again whenever the SET changes — never for a plain save,
   * which keeps its row (and any open detail pane) and just re-reads its
   * statistics. */
  function renderList(): void {
    list.innerHTML = '';
    rows.clear();
    if (entries.length === 0) {
      list.appendChild(
        el(
          'div',
          'abk-empty',
          'No experiments in this dashboard’s selection — restart with `abk dashboard --select …`.',
        ),
      );
      return;
    }
    const groups = new Map<string, BootEntry[]>();
    for (const entry of entries) {
      const bucket = groups.get(entry.dir);
      if (bucket === undefined) groups.set(entry.dir, [entry]);
      else bucket.push(entry);
    }
    const grouped = groups.size > 1;
    for (const [dir, group] of groups) {
      if (grouped) list.appendChild(el('div', 'abk-group', dir === '' ? '(top level)' : dir));
      const table = el('div', 'abk-table');
      table.appendChild(buildHeadRow());
      for (const entry of group) {
        const view = buildRow(entry);
        rows.set(entry.name, view);
        table.appendChild(view.root);
      }
      list.appendChild(table);
    }
  }

  // Both read `entries`, so they run after its declaration, not up in the
  // header block where the element was created (a `let` is in TDZ until then).
  setCountMeta();
  renderList();

  // ---- the create panel (UI-1) ----------------------------------------------

  const createPanel = buildPane('');
  createPanel.root.classList.add('abk-create');
  createPanel.root.style.display = 'none';
  root.insertBefore(createPanel.root, list);

  const folderInput = document.createElement('input');
  folderInput.className = 'abk-text';
  folderInput.type = 'text';
  folderInput.placeholder = 'subfolder under experiments/ (optional)';
  createPanel.root.insertBefore(folderInput, createPanel.root.firstChild);

  const createBtn = button('abk-btn abk-btn-run', 'Create', 'validate, then write a new file');
  const cancelCreate = button('abk-btn abk-btn-ghost', 'Cancel');
  createPanel.buttons.appendChild(createBtn);
  createPanel.buttons.appendChild(cancelCreate);

  function submitCreate(force: boolean): void {
    createPanel.clearForce();
    createBtn.disabled = true;
    createPanel.say(force ? 'creating (forced)…' : 'creating…');
    void postJson<WriteReply>('/api/experiment/create', {
      text: createPanel.area.value,
      folder: folderInput.value.trim() === '' ? null : folderInput.value.trim(),
      force,
    })
      .then((reply) => {
        createBtn.disabled = false;
        createPanel.say([`created ${reply.path}`, ...reply.warnings].join('\n'), 'ok');
        applyEntries(reply.experiments);
        if (reply.in_selection) createPanel.root.style.display = 'none';
      })
      .catch((err: unknown) => {
        createBtn.disabled = false;
        const detail = message(err);
        createPanel.say(detail, 'err');
        if (isForceable(detail)) createPanel.offerForce(() => submitCreate(true));
      });
  }

  createBtn.addEventListener('click', () => submitCreate(false));
  cancelCreate.addEventListener('click', () => {
    createPanel.root.style.display = 'none';
  });
  newBtn.addEventListener('click', () => {
    if (createPanel.root.style.display === '') {
      createPanel.root.style.display = 'none';
      return;
    }
    createPanel.root.style.display = '';
    createPanel.clearForce();
    // Seeded rather than blank: the fields below are the ones the validator
    // requires, so an operator who has never written one by hand gets a
    // refusal about VALUES, not about a shape they have to guess.
    if (createPanel.area.value.trim() === '') createPanel.area.value = NEW_EXPERIMENT_TEMPLATE;
    createPanel.say('a new experiments/<name>.yml — the file is named after `name:`');
    createPanel.area.focus();
  });

  reloadBtn.addEventListener('click', () => {
    reloadBtn.disabled = true;
    void postJson<SelectionReply>('/api/reload', {})
      .then((reply) => {
        reloadBtn.disabled = false;
        applyEntries(reply.experiments);
        if (reply.warnings.length > 0) setBanner(reply.warnings.join('\n'));
        else setBanner('');
      })
      .catch((err: unknown) => {
        reloadBtn.disabled = false;
        setBanner(`reload failed: ${message(err)}`);
      });
  });

  const drawer = buildDrawer();
  root.appendChild(drawer.root);

  // ---- the stats fill pool --------------------------------------------------

  function allNames(): string[] {
    return entries.map((entry) => entry.name);
  }

  /** Adopt a selection the server just re-resolved (UI-1).
   *
   * The list is rebuilt and re-filled only when something actually changed, so
   * a no-op save leaves the row — and whatever the operator had open on it —
   * alone. When anything did change the rows are rebuilt from the new metadata,
   * because a row's name, tags, file path and per-comparison Run buttons all
   * come off the boot entry its closure captured. */
  function applyEntries(next: BootEntry[]): void {
    const before = entriesKey(entries);
    entries = next;
    setCountMeta();
    if (entriesKey(next) === before) return;
    renderList();
    fillRows(allNames());
  }

  /** A stable key for the WHOLE served selection, not just its names.
   *
   * Comparing NAME LISTS was wrong in both directions a metadata edit takes:
   * adding a comparison, editing tags or moving a file changes what a row must
   * draw while leaving every name identical — so `Reload configs`, the button
   * documented as what you press after editing a YAML elsewhere, repainted
   * nothing at all. Serializing the entries is exact, and it costs a JSON pass
   * over a metadata list the page already holds. */
  function entriesKey(list: BootEntry[]): string {
    return JSON.stringify(list);
  }

  function setFillChip(): void {
    fillChip.textContent = pendingCount > 0 ? `loading ${pendingCount}…` : 'idle';
    fillChip.classList.toggle('abk-chip-busy', pendingCount > 0);
  }

  /**
   * Fill *names* through at most :data:`POOL_SIZE` concurrent requests.
   *
   * The workers pull off ONE shared queue, so the bound holds however long the
   * list is, and a rejection is per row: the queue keeps moving. Superseded
   * work (a window change, a refresh) is dropped by epoch AND aborted, so a
   * reply that lost the race can never paint over a newer one.
   */
  function fillRows(names: string[]): void {
    generation += 1;
    const epoch = generation;
    statsAbort?.abort();
    const controller = new AbortController();
    statsAbort = controller;
    for (const name of names) rows.get(name)?.pending();
    const queue = names.slice();
    pendingCount = queue.length;
    setFillChip();

    const worker = async (): Promise<void> => {
      for (;;) {
        if (epoch !== generation) return;
        const name = queue.shift();
        if (name === undefined) return;
        try {
          const row = await getJson<ExperimentRow>(
            `/api/stats/${encodeURIComponent(name)}`,
            { window: windowPreset },
            controller.signal,
          );
          if (epoch !== generation) return;
          lastRows.set(name, row);
          rows.get(name)?.paint(row);
        } catch (err) {
          // Superseded or deliberately aborted: leave the row to the newer
          // fill. Anything else is THIS row's error and the pool continues.
          if (isAbort(err) || epoch !== generation) return;
          rows.get(name)?.paintError(message(err));
        } finally {
          if (epoch === generation) {
            pendingCount = Math.max(0, pendingCount - 1);
            setFillChip();
          }
        }
      }
    };

    const workers: Array<Promise<void>> = [];
    for (let i = 0; i < Math.min(POOL_SIZE, queue.length); i++) workers.push(worker());
    void Promise.all(workers);
  }

  /** Re-read ONE row (a finished job's experiment), never the whole list. */
  function refreshRow(name: string): void {
    if (!rows.has(name)) return;
    const epoch = generation;
    const view = rows.get(name);
    view?.pending();
    void getJson<ExperimentRow>(
      `/api/stats/${encodeURIComponent(name)}`,
      { window: windowPreset },
      statsAbort?.signal,
    )
      .then((row) => {
        if (epoch !== generation) return; // a full re-fill overtook us
        lastRows.set(name, row);
        view?.paint(row);
      })
      .catch((err: unknown) => {
        if (isAbort(err) || epoch !== generation) return;
        view?.paintError(message(err));
      });
  }

  // ---- job chip + drawer ----------------------------------------------------

  let jobsTimer = 0;
  /** Latched by the teardown below. The job poll re-arms itself from its own
   * completion, so clearing the timer is not enough: a poll whose fetch was in
   * flight when the page was torn down would schedule the next one afterwards
   * and the abandoned page would keep polling for the life of the tab. */
  let disposed = false;

  function scheduleJobs(delay: number): void {
    if (disposed) return;
    if (jobsTimer) window.clearTimeout(jobsTimer);
    jobsTimer = window.setTimeout(() => void pollJobs(), delay);
  }

  async function pollJobs(): Promise<void> {
    try {
      const reply = await getJson<JobsReply>('/api/jobs');
      if (disposed) return;
      adoptJobs(reply);
    } catch {
      // A transient poll failure keeps the last chip state: the authoritative
      // gate is the server's own atomic check, and this chip is advisory.
    }
    scheduleJobs(pipelineActive || drawer.following() ? JOBS_POLL_BUSY_MS : JOBS_POLL_IDLE_MS);
  }

  function adoptJobs(reply: JobsReply): void {
    // `pipeline_active` is the SERVER's flag, never re-derived here: the rule
    // (kind ∈ PIPELINE_KINDS ∧ running) lives in jobs.py, and a second copy in
    // JS is exactly the divergence that vocabulary exists to prevent.
    pipelineActive = reply.pipeline_active;
    const running = reply.jobs.filter((job) => job.status === 'running');
    // The LABEL names running jobs (a per-job field); it never decides whether
    // Run is allowed — that is `pipeline_active` above, and ultimately the
    // route's 400.
    jobChip.textContent =
      running.length === 0
        ? 'jobs: idle'
        : `jobs: ${running.map((job) => `${job.kind} ${job.experiment ?? job.label}`).join(', ')}`;
    jobChip.classList.toggle('abk-chip-busy', running.length > 0);
    for (const view of rows.values()) view.root.classList.toggle('abk-busy', pipelineActive);

    for (const job of reply.jobs) {
      const was = jobStatus.get(job.id);
      jobStatus.set(job.id, job.status);
      // A job that just finished changed the warehouse (or the lock): re-read
      // that ONE row rather than the whole list.
      if (was === 'running' && job.status !== 'running' && job.experiment !== null) {
        refreshRow(job.experiment);
      }
    }
    // Forget ids the registry no longer lists (it keeps the last 20), so a
    // long-lived page does not accumulate one entry per job it ever saw. A
    // RUNNING job is never evicted server-side, so the edge above cannot be
    // lost this way — only a job that finished AND aged out between two polls,
    // which costs one row refresh the Refresh button can redo.
    const listed = new Set(reply.jobs.map((job) => job.id));
    for (const id of [...jobStatus.keys()]) if (!listed.has(id)) jobStatus.delete(id);
    drawer.adoptList(reply.jobs);
  }

  interface Drawer {
    root: HTMLElement;
    follow(jobId: string): void;
    toggleList(): void;
    adoptList(jobs: JobSummary[]): void;
    following(): boolean;
    /** stop the poll loop — its timer is closure-local, so a re-render would
     * otherwise leave the abandoned drawer polling forever */
    dispose(): void;
  }

  function buildDrawer(): Drawer {
    const node = el('div', 'abk-drawer');
    node.style.display = 'none';
    const head = el('div', 'abk-drawer-head');
    const label = el('span', 'abk-drawer-label', 'jobs');
    const status = el('span', 'abk-drawer-status', '');
    const stopBtn = button('abk-btn abk-btn-danger', 'Stop', 'SIGTERM, then SIGKILL after 5s');
    const closeBtn = button('abk-btn abk-btn-ghost', 'Close');
    head.appendChild(label);
    head.appendChild(status);
    head.appendChild(stopBtn);
    head.appendChild(closeBtn);
    const summary = el('div', 'abk-drawer-list');
    const log = el('pre', 'abk-drawer-log');
    node.appendChild(head);
    node.appendChild(summary);
    node.appendChild(log);

    let jobId: string | null = null;
    /** ABSOLUTE line offset (dropped + buffered), the server's own scheme: the
     * next poll asks for lines from here, so nothing is rendered twice. */
    let offset = 0;
    let timer = 0;
    let listOpen = false;

    function stopPolling(): void {
      if (timer) {
        window.clearTimeout(timer);
        timer = 0;
      }
    }

    function show(): void {
      node.style.display = '';
    }

    function close(): void {
      stopPolling();
      jobId = null;
      listOpen = false;
      node.style.display = 'none';
    }

    closeBtn.addEventListener('click', close);
    stopBtn.addEventListener('click', () => {
      const id = jobId;
      if (id === null) return;
      stopBtn.disabled = true;
      void postJson<{ ok: true }>(`/api/job/${encodeURIComponent(id)}/stop`, {})
        .then(() => void poll())
        .catch((err: unknown) => {
          status.textContent = `stop failed: ${message(err)}`;
          stopBtn.disabled = false;
        });
    });

    function appendLines(lines: string[]): void {
      if (lines.length === 0) return;
      for (const line of lines) log.appendChild(el('div', 'abk-log-line', line));
      while (log.childElementCount > DRAWER_MAX_LINES && log.firstElementChild !== null) {
        log.removeChild(log.firstElementChild);
      }
      log.scrollTop = log.scrollHeight;
    }

    function adopt(snapshot: JobSnapshot): void {
      label.textContent = snapshot.label;
      const bits: string[] = [snapshot.status];
      if (snapshot.returncode !== null) bits.push(`exit ${snapshot.returncode}`);
      if (snapshot.truncated) {
        bits.push(`${snapshot.dropped} earlier line(s) discarded — the buffer is capped`);
      }
      status.textContent = bits.join(' · ');
      status.className = `abk-drawer-status abk-job-${snapshot.status}`;
      stopBtn.style.display = snapshot.status === 'running' ? '' : 'none';
      stopBtn.disabled = snapshot.status !== 'running';
      appendLines(snapshot.lines);
      offset = snapshot.next_offset;
      if (snapshot.url !== null && snapshot.status === 'running') {
        const open = button('abk-btn abk-btn-ghost', 'Open cockpit');
        const cockpitUrl = snapshot.url;
        open.addEventListener('click', () => openTab(cockpitUrl));
        status.appendChild(open);
      }
    }

    async function poll(): Promise<void> {
      const id = jobId;
      if (id === null) return;
      stopPolling();
      try {
        const snapshot = await getJson<JobSnapshot>(`/api/job/${encodeURIComponent(id)}`, {
          offset: String(offset),
        });
        if (jobId !== id) return; // the drawer moved to another job meanwhile
        adopt(snapshot);
        if (snapshot.status === 'running') {
          timer = window.setTimeout(() => void poll(), DRAWER_POLL_MS);
        }
      } catch (err) {
        if (jobId !== id) return;
        status.textContent = `poll failed: ${message(err)}`;
      }
    }

    return {
      root: node,
      follow(id: string): void {
        stopPolling();
        jobId = id;
        offset = 0;
        listOpen = false;
        summary.style.display = 'none';
        log.style.display = '';
        log.textContent = '';
        status.textContent = 'starting…';
        show();
        void poll();
        // A brand-new job flips the chip without waiting for the idle cadence.
        scheduleJobs(0);
      },
      toggleList(): void {
        if (listOpen) {
          close();
          return;
        }
        stopPolling();
        jobId = null;
        listOpen = true;
        label.textContent = 'jobs';
        status.textContent = '';
        stopBtn.style.display = 'none';
        summary.style.display = '';
        log.style.display = 'none';
        show();
        scheduleJobs(0);
      },
      adoptList(jobs: JobSummary[]): void {
        if (!listOpen) return;
        summary.textContent = '';
        if (jobs.length === 0) {
          summary.appendChild(el('div', 'abk-log-line', 'no jobs spawned yet'));
          return;
        }
        for (const job of jobs) {
          const line = el('div', 'abk-job-row');
          line.appendChild(el('span', `abk-job-${job.status}`, job.status));
          line.appendChild(el('span', 'abk-job-label', job.label));
          const openLog = button('abk-btn abk-btn-ghost', 'log');
          openLog.addEventListener('click', () => drawer.follow(job.id));
          line.appendChild(openLog);
          summary.appendChild(line);
        }
      },
      following(): boolean {
        return jobId !== null;
      },
      dispose(): void {
        stopPolling();
        jobId = null;
      },
    };
  }

  /** Open *href* in a tab, falling back to a visible link when blocked. */
  function openTab(href: string, fallback?: (link: HTMLElement) => void): void {
    let opened: Window | null = null;
    try {
      opened = window.open(href, '_blank');
    } catch {
      opened = null;
    }
    if (opened !== null || fallback === undefined) return;
    const link = el('a', 'abk-link', href) as HTMLAnchorElement;
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener';
    fallback(link);
  }

  // ---- the YAML editor (UI-1) -----------------------------------------------

  /** A textarea + its buttons, styled and wired the same way everywhere.
   *
   * Shared by the per-row editor and the "New experiment" panel so the two
   * cannot drift on what a validation failure looks like — which matters more
   * than it sounds: the failure pane IS the feature. A save is refused for
   * five different reasons (bad YAML, a config pydantic rejects, a §8 finding,
   * a stale digest, a running job) and the operator can only act on the one
   * that happened.
   */
  interface Pane {
    root: HTMLElement;
    area: HTMLTextAreaElement;
    buttons: HTMLElement;
    /** show a message; `kind` picks the status token, never a new hex */
    say(text: string, kind?: 'info' | 'ok' | 'err'): void;
    /** the level-2 override, shown only after the server asks for it */
    offerForce(retry: () => void): void;
    clearForce(): void;
  }

  function buildPane(placeholder: string): Pane {
    const root = el('div', 'abk-editor');
    const area = document.createElement('textarea');
    area.className = 'abk-yaml';
    area.spellcheck = false;
    area.rows = 18;
    area.placeholder = placeholder;
    root.appendChild(area);
    const msg = el('div', 'abk-editor-msg');
    msg.style.display = 'none';
    root.appendChild(msg);
    const forceRow = el('div', 'abk-btn-row');
    forceRow.style.display = 'none';
    root.appendChild(forceRow);
    const buttons = el('div', 'abk-btn-row');
    root.appendChild(buttons);

    function say(text: string, kind: 'info' | 'ok' | 'err' = 'info'): void {
      // textContent, never innerHTML: this pane echoes server messages that
      // quote the operator's own YAML back at them.
      msg.textContent = text;
      msg.className = `abk-editor-msg abk-editor-msg-${kind}`;
      msg.style.display = text === '' ? 'none' : '';
    }

    function clearForce(): void {
      forceRow.innerHTML = '';
      forceRow.style.display = 'none';
    }

    function offerForce(retry: () => void): void {
      clearForce();
      const btn = button(
        'abk-btn abk-btn-danger',
        'Save anyway',
        'write the file even though `abk run` will refuse it',
      );
      btn.addEventListener('click', () => {
        clearForce();
        retry();
      });
      forceRow.appendChild(btn);
      forceRow.style.display = '';
    }

    return { root, area, buttons, say, offerForce, clearForce };
  }

  /** True when a refusal is a level-2 finding, i.e. one `force` can override.
   *
   * Keyed on the server's own sentence rather than a status code, because
   * every refusal on this route is a 400 — and offering "Save anyway" for a
   * YAML syntax error would be offering something the server will refuse
   * again (level 1 is never forceable). */
  function isForceable(detail: string): boolean {
    return detail.includes('not valid for this project');
  }

  /** The per-row editor pane, toggled by the row's `Edit YAML` button. */
  function buildEditor(entry: BootEntry, toggle: HTMLButtonElement): HTMLElement {
    const pane = buildPane('');
    pane.root.style.display = 'none';
    /** The text this editor was opened with — the concurrency token AND the
     * Revert target. `null` until a load succeeds, which is what keeps a Save
     * from posting an empty textarea over a file that never loaded. */
    let opened: SourceReply | null = null;
    /** The experiment this pane addresses. A rename changes it, and the row's
     * own `entry.name` is boot metadata that does not follow. */
    let target = entry.name;

    const saveBtn = button('abk-btn abk-btn-run', 'Save', 'validate, archive, then write');
    const revertBtn = button('abk-btn abk-btn-ghost', 'Revert', 'discard the edits in this box');
    const deleteBtn = button('abk-btn abk-btn-danger', 'Delete…', 'archive the YAML, then remove it');
    pane.buttons.appendChild(saveBtn);
    pane.buttons.appendChild(revertBtn);
    pane.buttons.appendChild(deleteBtn);

    const confirm = el('div', 'abk-confirm');
    confirm.style.display = 'none';
    confirm.appendChild(
      el(
        'div',
        'abk-confirm-text',
        'Delete archives the YAML under .history/ and removes the file. The ' +
          'experiment’s persisted rows are NOT deleted — `abk clean ' +
          '--orphaned-experiments` prunes those.',
      ),
    );
    const confirmBtns = el('div', 'abk-btn-row');
    const confirmYes = button('abk-btn abk-btn-danger', 'Delete anyway');
    const confirmNo = button('abk-btn abk-btn-ghost', 'Cancel');
    confirmBtns.appendChild(confirmYes);
    confirmBtns.appendChild(confirmNo);
    confirm.appendChild(confirmBtns);
    pane.root.appendChild(confirm);

    function load(): void {
      pane.clearForce();
      pane.say('loading…');
      saveBtn.disabled = true;
      void getJson<SourceReply>(`/api/experiment-source/${encodeURIComponent(target)}`)
        .then((reply) => {
          opened = reply;
          pane.area.value = reply.yaml_text;
          if (reply.editable) {
            saveBtn.disabled = false;
            pane.say(reply.path);
          } else {
            // A truncated read has no digest, and saving the prefix back would
            // drop the tail — the one case where the editor refuses itself.
            pane.say(
              `${reply.path} is too large to edit here — it was truncated for display; ` +
                'open it in your editor',
              'err',
            );
          }
        })
        .catch((err: unknown) => {
          opened = null;
          pane.area.value = '';
          pane.say(`could not read the YAML: ${message(err)}`, 'err');
        });
    }

    function save(force: boolean): void {
      if (opened === null) return;
      pane.clearForce();
      saveBtn.disabled = true;
      pane.say(force ? 'saving (forced)…' : 'saving…');
      const text = pane.area.value;
      void postJson<WriteReply>('/api/experiment/save', {
        select: target,
        text,
        digest: opened.digest,
        force,
      })
        .then((reply) => {
          saveBtn.disabled = false;
          target = reply.name;
          opened = {
            name: reply.name,
            path: reply.path,
            yaml_text: text,
            truncated: false,
            digest: reply.digest,
            editable: true,
          };
          pane.say(
            [`saved ${reply.path} · previous archived at ${reply.archived ?? '—'}`, ...reply.warnings].join(
              '\n',
            ),
            reply.warnings.length > 0 ? 'err' : 'ok',
          );
          applyEntries(reply.experiments);
          // A rename rebuilt the list, so this row is gone; otherwise the row
          // is still ours and its numbers may have moved (alpha, correction).
          if (reply.renamed_from === null) refreshRow(reply.name);
        })
        .catch((err: unknown) => {
          saveBtn.disabled = false;
          const detail = message(err);
          pane.say(detail, 'err');
          if (isForceable(detail)) pane.offerForce(() => save(true));
        });
    }

    saveBtn.addEventListener('click', () => save(false));
    revertBtn.addEventListener('click', () => load());
    deleteBtn.addEventListener('click', () => {
      confirm.style.display = '';
    });
    confirmNo.addEventListener('click', () => {
      confirm.style.display = 'none';
    });
    confirmYes.addEventListener('click', () => {
      confirm.style.display = 'none';
      pane.say('deleting…');
      void postJson<DeleteReply>('/api/experiment/delete', {
        select: target,
        digest: opened?.digest ?? null,
      })
        .then((reply) => {
          // The page-level banner, NOT the row's own message line: the very
          // next statement removes that row, so the archive path — the only
          // pointer to the recoverable copy — would be destroyed before it
          // ever painted. The server's warnings ride along for the same
          // reason (they say the persisted rows are still there).
          setBanner([`deleted ${reply.path} — archived at ${reply.archived}`, ...reply.warnings].join('\n'));
          applyEntries(reply.experiments);
        })
        .catch((err: unknown) => {
          pane.say(`delete refused: ${message(err)}`, 'err');
        });
    });

    toggle.addEventListener('click', () => {
      if (pane.root.style.display === '') {
        pane.root.style.display = 'none';
        return;
      }
      pane.root.style.display = '';
      load();
    });

    return pane.root;
  }

  // ---- one row --------------------------------------------------------------

  function buildHeadRow(): HTMLElement {
    const head = el('div', 'abk-row abk-head');
    for (const [cls, text] of [
      ['abk-cell-disclose', ''],
      ['abk-cell-name', 'experiment'],
      ['abk-cell-verdict', 'verdict'],
      ['abk-cell-effect', 'effect (CI)'],
      ['abk-cell-p', 'p / α'],
      ['abk-cell-time', 'elapsed'],
      ['abk-cell-spark', 'effect over the window'],
      ['abk-cell-actions', ''],
    ] as Array<[string, string]>) {
      head.appendChild(el('div', `abk-cell ${cls}`, text));
    }
    return head;
  }

  function buildRow(entry: BootEntry): RowView {
    const node = el('div', 'abk-row');
    node.setAttribute('data-abk-experiment', entry.name);
    const main = el('div', 'abk-row-main');
    node.appendChild(main);

    const discloseCell = el('div', 'abk-cell abk-cell-disclose');
    const disclose = button('abk-disclose', '▸', 'show verdicts, warnings and actions');
    disclose.setAttribute('aria-expanded', 'false');
    discloseCell.appendChild(disclose);
    main.appendChild(discloseCell);

    const nameCell = el('div', 'abk-cell abk-cell-name');
    nameCell.appendChild(el('div', 'abk-name', entry.name));
    const subBits = [entry.file];
    if (entry.status !== null) subBits.push(entry.status);
    if (entry.main_metric !== null) subBits.push(entry.main_metric);
    nameCell.appendChild(el('div', 'abk-sub', subBits.join(' · ')));
    if (entry.tags.length > 0) {
      const tags = el('div', 'abk-tags');
      for (const tag of entry.tags) tags.appendChild(el('span', 'abk-tag', tag));
      nameCell.appendChild(tags);
    }
    main.appendChild(nameCell);

    const verdictCell = el('div', 'abk-cell abk-cell-verdict');
    const chip = el('span', 'abk-chip abk-v-pending', 'pending');
    verdictCell.appendChild(chip);
    const badges = el('span', 'abk-badges');
    verdictCell.appendChild(badges);
    main.appendChild(verdictCell);

    const effectCell = el('div', 'abk-cell abk-cell-effect', '—');
    main.appendChild(effectCell);
    const pCell = el('div', 'abk-cell abk-cell-p', '—');
    main.appendChild(pCell);
    const timeCell = el('div', 'abk-cell abk-cell-time', '—');
    main.appendChild(timeCell);

    const sparkCell = el('div', 'abk-cell abk-cell-spark');
    const canvas = document.createElement('canvas');
    canvas.className = 'abk-spark';
    sparkCell.appendChild(canvas);
    main.appendChild(sparkCell);

    const actions = el('div', 'abk-cell abk-cell-actions');
    const openBtn = button('abk-btn', 'Open', 'the full report for this experiment');
    const exploreBtn = button('abk-btn', 'Explore', 'launch the tuning cockpit (may take a while)');
    const runBtn = button('abk-btn abk-btn-run', 'Run', 'spawn `abk run` for this experiment');
    actions.appendChild(openBtn);
    actions.appendChild(exploreBtn);
    actions.appendChild(runBtn);
    main.appendChild(actions);

    const noteLine = el('div', 'abk-row-note');
    noteLine.style.display = 'none';
    node.appendChild(noteLine);
    const msgLine = el('div', 'abk-row-msg');
    msgLine.style.display = 'none';
    node.appendChild(msgLine);
    const detail = el('div', 'abk-detail');
    detail.style.display = 'none';
    node.appendChild(detail);

    let expanded = false;
    let spark: Array<[number, number | null]> = [];

    function setMsg(text: string, kind: 'info' | 'err' = 'info'): void {
      msgLine.textContent = text;
      msgLine.className = `abk-row-msg${kind === 'err' ? ' abk-row-msg-err' : ''}`;
      msgLine.style.display = text === '' ? 'none' : '';
    }

    function setNote(text: string, classes: string): void {
      noteLine.textContent = text;
      noteLine.className = `abk-row-note ${classes}`.trim();
      noteLine.style.display = text === '' ? 'none' : '';
    }

    // -- actions -------------------------------------------------------------

    openBtn.addEventListener('click', () => {
      // The SAME report `abk run --report` writes, rendered on demand by
      // GET /experiment/<name> — one row, one tab, one full reload (the only
      // other one is Explore); the list itself is never re-read for this.
      const href = url(`/experiment/${encodeURIComponent(entry.name)}`);
      openTab(href, (link) => {
        setMsg('the browser blocked the tab — ', 'info');
        msgLine.appendChild(link);
      });
    });

    exploreBtn.addEventListener('click', () => {
      exploreBtn.disabled = true;
      const restore = (): void => {
        exploreBtn.disabled = false;
      };
      // POST /api/explore is the ONE long route: it holds the response until
      // the spawned cockpit prints its URL (up to 90 s server-side), so the
      // button stays busy instead of pretending to be idle.
      setMsg('starting the explore cockpit — this can take up to 90 s…');
      void postJson<ExploreReply>('/api/explore', { select: entry.name })
        .then((reply) => {
          restore();
          setMsg('');
          drawer.follow(reply.job_id);
          openTab(reply.url, (link) => {
            setMsg('cockpit ready — ', 'info');
            msgLine.appendChild(link);
          });
        })
        .catch((err: unknown) => {
          restore();
          setMsg(`explore failed: ${message(err)}`, 'err');
        });
    });

    function spawn(path: string, body: Record<string, unknown>, what: string): void {
      setMsg(`starting ${what}…`);
      void postJson<SpawnReply>(path, body)
        .then((reply) => {
          setMsg('');
          drawer.follow(reply.job_id);
        })
        .catch((err: unknown) => {
          // 400 "a pipeline job is already running" lands here too — the
          // advisory chip can be stale, the route's answer cannot.
          setMsg(`${what} refused: ${message(err)}`, 'err');
        });
    }

    runBtn.addEventListener('click', () => spawn('/api/run', { select: entry.name }, 'run'));

    // -- the expandable detail ------------------------------------------------
    //
    // Two halves, deliberately: a READOUT block rebuilt from every stats reply,
    // and a persistent shell (the buttons, the clean confirm, the YAML pane)
    // built once. Rebuilding the whole detail on each paint looks harmless and
    // is not: a fill or a finished job's row refresh lands seconds after a
    // click, and it would collapse an open YAML pane, dismiss a confirm box the
    // operator is reading, and drop the reply of a source fetch still in flight
    // into a detached node.

    const readoutBlock = el('div', 'abk-readout');
    let shellBuilt = false;

    function refreshReadout(): void {
      readoutBlock.textContent = '';
      const row = lastRows.get(entry.name);
      if (row === undefined) return;
      if (row.error !== null) {
        readoutBlock.appendChild(el('div', 'abk-warning', `⚠ ${row.error}`));
      }
      for (const warning of row.warnings) {
        readoutBlock.appendChild(el('div', 'abk-warning', `⚠ ${warning}`));
      }
      for (const caveat of row.caveats) {
        readoutBlock.appendChild(el('div', 'abk-caveat', `! ${caveat}`));
      }
      if (row.rationale.length > 0) {
        const why = el('div', 'abk-block');
        why.appendChild(el('div', 'abk-block-title', 'why this verdict'));
        for (const line of row.rationale) why.appendChild(el('div', 'abk-rationale', line));
        readoutBlock.appendChild(why);
      }
      if (row.verdicts.length > 0) {
        const pairs = el('div', 'abk-block');
        pairs.appendChild(el('div', 'abk-block-title', 'per arm pair'));
        for (const verdict of row.verdicts) {
          const line = el('div', 'abk-pair');
          line.appendChild(
            el('span', `abk-v-word abk-v-${verdict.verdict.toLowerCase()}`, verdict.verdict),
          );
          line.appendChild(
            el('span', 'abk-pair-name', `${verdict.metric}: ${verdict.pair.c} vs ${verdict.pair.t}`),
          );
          // m14 DEC-4: an arm-vs-arm verdict is EVIDENCE. Unlabelled, a `WIN`
          // on `t1 vs t2` in this list reads as a third ship decision.
          if ((verdict.role ?? 'vs_control') !== 'vs_control') {
            line.appendChild(el('span', 'abk-pair-role', 'arm vs arm'));
          }
          line.appendChild(el('span', 'abk-pair-effect', dash(verdict.effect, fmtSigned)));
          if (verdict.guardrail_regressed) {
            line.appendChild(el('span', 'abk-badge-guardrail', 'guardrail regressed'));
          }
          for (const caveat of verdict.caveats) {
            line.appendChild(el('div', 'abk-caveat', `! ${caveat}`));
          }
          pairs.appendChild(line);
        }
        readoutBlock.appendChild(pairs);
      }
      const facts = [
        `SRM p ${dash(row.srm_pvalue, fmtP)}`,
        `last look ${row.last_end_ts === null ? '—' : `${fmtTs(row.last_end_ts)} UTC`}`,
        `timezone ${row.timezone ?? '—'}`,
        row.locked ? 'the pipeline lock is HELD' : 'unlocked',
      ];
      readoutBlock.appendChild(el('div', 'abk-facts', facts.join(' · ')));
    }

    function buildDetailShell(): void {
      detail.appendChild(readoutBlock);

      // Per-metric Run: off the CONFIGURED comparisons, because a secondary
      // metric never appears in the readout's verdict list and still needs its
      // own recompute (the DASH-4a flag exists for exactly this).
      const perMetric = el('div', 'abk-block');
      perMetric.appendChild(el('div', 'abk-block-title', 'run one comparison'));
      const metricRow = el('div', 'abk-btn-row');
      for (const comparison of entry.comparisons) {
        const label = comparison.is_main_metric
          ? comparison.metric
          : `${comparison.metric} (secondary)`;
        const btn = button('abk-btn', `Run ${label}`, `abk run --metric ${comparison.metric}`);
        btn.addEventListener('click', () =>
          spawn(
            '/api/run',
            { select: entry.name, metric: comparison.metric },
            `run ${comparison.metric}`,
          ),
        );
        metricRow.appendChild(btn);
      }
      perMetric.appendChild(metricRow);
      detail.appendChild(perMetric);

      // Maintenance: unlock is harmless, clean is not — hence the confirm.
      const maintenance = el('div', 'abk-block');
      maintenance.appendChild(el('div', 'abk-block-title', 'maintenance'));
      const maintRow = el('div', 'abk-btn-row');
      const unlockBtn = button(
        'abk-btn',
        'Unlock',
        'release a stale pipeline lock (`abk unlock`)',
      );
      unlockBtn.addEventListener('click', () =>
        spawn('/api/unlock', { select: entry.name }, 'unlock'),
      );
      maintRow.appendChild(unlockBtn);

      const cleanBtn = button(
        'abk-btn abk-btn-danger',
        'Clean…',
        'delete orphaned rows (`abk clean --execute`)',
      );
      maintRow.appendChild(cleanBtn);

      const sourceBtn = button('abk-btn abk-btn-ghost', 'Edit YAML', entry.file);
      maintRow.appendChild(sourceBtn);
      maintenance.appendChild(maintRow);

      const confirm = el('div', 'abk-confirm');
      confirm.style.display = 'none';
      confirm.appendChild(
        el(
          'div',
          'abk-confirm-text',
          'Clean runs `abk clean --select … --execute`: it DELETES orphaned ' +
            '_ab_results / _ab_unit_state rows for this experiment. There is no undo.',
        ),
      );
      const confirmBtns = el('div', 'abk-btn-row');
      const confirmYes = button('abk-btn abk-btn-danger', 'Clean anyway');
      const confirmNo = button('abk-btn abk-btn-ghost', 'Cancel');
      confirmYes.addEventListener('click', () => {
        confirm.style.display = 'none';
        spawn('/api/clean', { select: entry.name }, 'clean');
      });
      confirmNo.addEventListener('click', () => {
        confirm.style.display = 'none';
      });
      confirmBtns.appendChild(confirmYes);
      confirmBtns.appendChild(confirmNo);
      confirm.appendChild(confirmBtns);
      cleanBtn.addEventListener('click', () => {
        confirm.style.display = '';
      });
      maintenance.appendChild(confirm);
      detail.appendChild(maintenance);

      // The YAML editor (UI-1). The text is round-tripped verbatim — this is
      // not `abk explore`'s Apply, which re-emits a parsed document and loses
      // comments — and every save is validated, archived and atomic
      // server-side (abkit/tuning/config_files.py).
      detail.appendChild(buildEditor(entry, sourceBtn));
    }

    disclose.addEventListener('click', () => {
      expanded = !expanded;
      disclose.textContent = expanded ? '▾' : '▸';
      disclose.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      detail.style.display = expanded ? '' : 'none';
      if (!expanded) return;
      if (!shellBuilt) {
        shellBuilt = true;
        buildDetailShell();
      }
      refreshReadout();
    });

    // -- painting -------------------------------------------------------------

    function pending(): void {
      chip.className = 'abk-chip abk-v-pending';
      chip.textContent = 'pending';
      badges.textContent = '';
      effectCell.textContent = '—';
      pCell.textContent = '—';
      timeCell.textContent = '—';
      setNote('', '');
      spark = [];
      redraw();
    }

    function paint(row: ExperimentRow): void {
      badges.textContent = '';
      // The four states a chip can be in, in precedence order. `verdict: null`
      // means either "nothing computed yet" or "this row degraded" — the two
      // are told apart by `error`, which is null only in the first case.
      if (row.error !== null) {
        chip.className = 'abk-chip abk-v-error';
        chip.textContent = 'error';
        setNote(row.error, 'abk-v-error');
      } else if (row.verdict === null) {
        chip.className = 'abk-chip abk-v-none';
        chip.textContent = 'no data';
        setNote('no computed results yet — press Run', '');
      } else {
        const classes = ['abk-chip', `abk-v-${row.verdict.toLowerCase()}`];
        // §4 markers. The SRM gate outranks the rest (its own verdict is
        // already withheld); a demoted headline look and a pre-horizon refusal
        // are both "no decision from these numbers", so both are said out loud
        // on the collapsed row rather than only in the detail.
        let note = '';
        if (row.srm_flag) {
          classes.push('abk-srm-fail');
          note = `SRM FAILED (p ${dash(row.srm_pvalue, fmtP)}) — effects untrustworthy`;
        } else if (row.insufficient) {
          classes.push('abk-insufficient');
          note = 'insufficient data at the latest look — inference withheld';
        } else if (!row.is_horizon && row.verdict === 'INCONCLUSIVE') {
          classes.push('abk-prehorizon');
          note = 'pre-horizon: fixed CIs are not peeking-valid, so a verdict is withheld';
        }
        chip.className = classes.join(' ');
        chip.textContent = row.verdict;
        setNote(note, note === '' ? '' : classes.slice(1).join(' '));
      }

      // m14 DEC-4. The leader chip is the row's answer to "which arm", which
      // the verdict word alone cannot give at 3+ arms. Gated on the ROLLUP
      // COUNT rather than the arm count — the row does not carry one — via the
      // pair list: a treatment-pair verdict exists only above two arms, and
      // with two arms `leader` merely restates the WIN beside it.
      const multiArm = row.verdicts.some((v) => v.role === 'treatment_pair');
      if (multiArm && row.leader !== null) {
        const badge = el('span', 'abk-badge-leader', `→ ${row.leader}`);
        badge.title = row.rollups.find((r) => r.leader === row.leader)?.rationale.join('\n') ?? '';
        badges.appendChild(badge);
      }
      if (row.leaders_agree === false) {
        const badge = el('span', 'abk-badge-caveat', 'leaders split');
        badge.title = row.rollups
          .map((r) => `${r.metric}: ${r.leader ?? 'no leader'}`)
          .join('\n');
        badges.appendChild(badge);
      }
      if (row.guardrail_regressed) {
        badges.appendChild(el('span', 'abk-badge-guardrail', 'guardrail'));
      }
      if (row.caveats.length > 0) {
        const badge = el('span', 'abk-badge-caveat', `⚠ ${row.caveats.length}`);
        badge.title = row.caveats.join('\n');
        badges.appendChild(badge);
      }
      if (row.weekly_cycle_pct !== null) {
        const badge = el('span', 'abk-badge-caveat', `${Math.round(row.weekly_cycle_pct * 100)}% wk`);
        badge.title = 'decided before one full weekly cycle';
        badges.appendChild(badge);
      }
      if (row.locked) {
        const badge = el('span', 'abk-badge-lock', 'locked');
        badge.title = 'the pipeline lock is held — Run would refuse';
        badges.appendChild(badge);
      }

      effectCell.textContent = `${dash(row.effect, fmtSigned)} [${dash(row.ci[0])}, ${dash(row.ci[1])}]`;
      pCell.textContent = `${dash(row.pvalue, fmtP)} / ${dash(row.alpha)}`;
      const planned = daysBetween(row.start_ts, row.horizon_ts);
      const elapsed = row.elapsed_days === null ? '—' : `${fmtVal(row.elapsed_days)}d`;
      timeCell.textContent =
        planned === null ? elapsed : `${elapsed} / ${fmtVal(planned)}d${row.is_horizon ? ' ✓' : ''}`;
      timeCell.title =
        row.last_end_ts === null
          ? 'no computed look yet'
          : `latest look ${fmtTs(row.last_end_ts)} UTC`;

      spark = row.spark;
      redraw();
      if (expanded) refreshReadout();
    }

    function paintError(detailText: string): void {
      chip.className = 'abk-chip abk-v-error';
      chip.textContent = 'error';
      badges.textContent = '';
      setNote(detailText, 'abk-v-error');
    }

    function redraw(): void {
      drawSpark(canvas, spark);
    }

    return { root: node, pending, paint, paintError, redraw };
  }

  // ---- boot ----------------------------------------------------------------

  // The pool starts on the next tick, so the metadata-only list paints FIRST
  // (the point of the two-phase payload) and nothing is fetched by the render
  // itself.
  const kickoff = window.setTimeout(() => {
    if (payload.experiments.length > 0) fillRows(allNames());
    scheduleJobs(0);
  }, 0);
  disposers.push(() => window.clearTimeout(kickoff));
  disposers.push(() => {
    disposed = true;
    if (jobsTimer) window.clearTimeout(jobsTimer);
    drawer.dispose();
    statsAbort?.abort();
    // Bump the epoch too: an in-flight reply must not paint into a torn-down
    // page (abort alone loses the race with a reply already parsing).
    generation += 1;
  });

  let resizeTimer = 0;
  const onResize = (): void => {
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      for (const view of rows.values()) view.redraw();
    }, 120);
  };
  window.addEventListener('resize', onResize);
  disposers.push(() => {
    window.removeEventListener('resize', onResize);
    if (resizeTimer) window.clearTimeout(resizeTimer);
  });
}

// ----------------------------------------------------------------------------
// The sparkline — chart.ts primitives at row height, no axes
// ----------------------------------------------------------------------------

/**
 * Draw one row's `[ms-epoch, effect]` buckets as a tiny effect-over-time line
 * with a zero reference, through the SAME decimating line primitive the report
 * and explore charts use (a null bucket is a NaN, which breaks the pen — a gap
 * stays a gap). Plotted against the emitted timestamp, never the index: the
 * server buckets by STRIDE, so a gapped series has time-irregular buckets.
 */
function drawSpark(canvas: HTMLCanvasElement, points: Array<[number, number | null]>): void {
  let g: CanvasRenderingContext2D | null = null;
  try {
    g = canvas.getContext('2d');
  } catch {
    g = null; // jsdom / a canvas-less environment: the numbers carry the row
  }
  if (g === null) {
    canvas.classList.add('abk-spark-blank');
    return;
  }
  const dpr = fit(canvas);
  g.clearRect(0, 0, canvas.width, canvas.height);
  if (canvas.width === 0 || canvas.height === 0 || points.length === 0) {
    // A window with no looks in it draws nothing — and must not keep the
    // previous window's tooltip describing a range it no longer shows.
    canvas.title = '';
    return;
  }

  const xs = points.map(([ts]) => ts);
  // null → NaN so the pen breaks over an all-non-finite bucket.
  const values = points.map(([, value]) => (value === null ? NaN : value));
  const finite = values.filter((value) => Number.isFinite(value));
  const xmin = xs[0];
  const xmax = xs[xs.length - 1] === xmin ? xmin + 1 : xs[xs.length - 1];
  // Zero is always in frame: an effect reads against it, and a sparkline whose
  // baseline floated would make a tiny positive series look like a big one.
  const lo = Math.min(0, ...finite);
  const hi = Math.max(0, ...finite);
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.15;
  const domain: Domain = { xmin, xmax, vmin: lo - pad, vmax: hi + pad };
  const scales = makeScales(canvas, SPARK_MARGINS, domain, dpr);
  const rect = plotRect(canvas, SPARK_MARGINS, dpr);

  drawHLine(
    g,
    canvas,
    SPARK_MARGINS,
    dpr,
    scales.py,
    0,
    rgba(token('--abk-chart-grid'), 0.45),
    '',
    [3, 3],
  );
  drawSeriesDecimated(
    g,
    xs,
    values,
    xmin,
    xmax,
    rect.left,
    rect.right - rect.left,
    scales.px,
    scales.py,
    token('--abk-series-1'),
    1.4,
    dpr,
  );
  canvas.title = `${points.length} bucket(s), ${fmtDate(xmin)} → ${fmtDate(xmax)} UTC`;
}

// ----------------------------------------------------------------------------
// Styling (injected once, scoped under .abk-dashboard)
// ----------------------------------------------------------------------------

let styleInjected = false;
function injectStyle(): void {
  if (styleInjected) return;
  styleInjected = true;
  // The token block comes from the ONE brand-token layer and is declared on
  // :where(:root) (zero specificity), so canvas `token()` reads and DOM
  // `var()` resolve through the same node — a host override hits both.
  const tokenBlock = Object.entries(TOKEN_FALLBACKS)
    .map(([name, value]) => `${name}:${value}`)
    .join(';');
  const css = `
:where(:root){${tokenBlock};
  --abk-sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --abk-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.${ROOT_CLASS}{font-family:var(--abk-sans);color:var(--abk-ink);background:var(--abk-page);}
.${ROOT_CLASS} *{box-sizing:border-box;}
.${ROOT_CLASS} .abk-root{min-height:100vh;padding:16px 18px 96px;}
/* header ------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-header{padding-left:12px;border-left:3px solid var(--abk-explore-accent);
  margin-bottom:10px;}
.${ROOT_CLASS} .abk-brand{display:flex;align-items:center;gap:8px;margin-bottom:6px;}
.${ROOT_CLASS} .abk-logomark{width:22px;height:22px;border-radius:6px;display:block;}
.${ROOT_CLASS} .abk-wordmark{font:700 14px var(--abk-sans);color:var(--abk-explore-accent);
  letter-spacing:-0.01em;}
.${ROOT_CLASS} .abk-h-top{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;}
.${ROOT_CLASS} .abk-title{font-size:19px;font-weight:700;margin:0;letter-spacing:-0.01em;}
.${ROOT_CLASS} .abk-badge-page{font-size:10px;font-family:var(--abk-mono);text-transform:uppercase;
  letter-spacing:0.08em;padding:2px 8px;border-radius:8px;border:1px solid var(--abk-border);
  color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-meta{font-size:11.5px;color:var(--abk-ink-2);font-family:var(--abk-mono);
  margin-top:3px;}
.${ROOT_CLASS} .abk-warning{font-size:12px;color:var(--abk-ink);
  background:color-mix(in srgb, var(--abk-st-warn) 14%, transparent);
  border:1px solid var(--abk-st-warn);border-radius:8px;padding:4px 10px;margin:4px 0;}
.${ROOT_CLASS} .abk-caveat{font-size:11.5px;color:var(--abk-ink-2);font-family:var(--abk-mono);
  margin:3px 0;}
/* controls ----------------------------------------------------------------- */
.${ROOT_CLASS} .abk-controls{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;
  margin:10px 0;}
.${ROOT_CLASS} .abk-ctl-label{font:600 10.5px var(--abk-mono);color:var(--abk-muted);
  text-transform:uppercase;letter-spacing:0.06em;}
.${ROOT_CLASS} .abk-seg{display:flex;gap:4px;}
.${ROOT_CLASS} .abk-seg-btn{font:600 11px var(--abk-mono);padding:4px 9px;border-radius:8px;
  border:1px solid var(--abk-border);background:var(--abk-card);color:var(--abk-ink-2);
  cursor:pointer;}
.${ROOT_CLASS} .abk-seg-btn.on{border-color:var(--abk-explore-accent);
  color:var(--abk-explore-accent);background:color-mix(in srgb, var(--abk-explore-accent) 8%, transparent);}
.${ROOT_CLASS} .abk-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;
  background:var(--abk-card);border:1px solid var(--abk-border);border-radius:10px;
  font:11.5px var(--abk-mono);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-chip-job{cursor:pointer;}
.${ROOT_CLASS} .abk-chip-busy{border-color:var(--abk-explore-accent);
  color:var(--abk-explore-accent);}
/* the list ----------------------------------------------------------------- */
.${ROOT_CLASS} .abk-group{font:700 11px var(--abk-mono);text-transform:uppercase;
  letter-spacing:0.06em;color:var(--abk-muted);margin:14px 0 4px;}
.${ROOT_CLASS} .abk-table{border:1px solid var(--abk-border);border-radius:12px;
  background:var(--abk-card);overflow:hidden;}
.${ROOT_CLASS} .abk-row{border-top:1px solid var(--abk-border);}
.${ROOT_CLASS} .abk-table > .abk-row:first-child{border-top:none;}
.${ROOT_CLASS} .abk-row-main{display:grid;align-items:center;gap:10px;padding:8px 12px;
  grid-template-columns:22px minmax(180px,2fr) 140px 150px 110px 96px minmax(90px,1fr) auto;}
.${ROOT_CLASS} .abk-head .abk-row-main,.${ROOT_CLASS} .abk-head{background:transparent;}
.${ROOT_CLASS} .abk-head{display:grid;align-items:center;gap:10px;padding:6px 12px;
  grid-template-columns:22px minmax(180px,2fr) 140px 150px 110px 96px minmax(90px,1fr) auto;
  font:600 9.5px var(--abk-mono);text-transform:uppercase;letter-spacing:0.07em;
  color:var(--abk-muted);border-bottom:1px solid var(--abk-border);}
.${ROOT_CLASS} .abk-cell{min-width:0;font:11.5px var(--abk-mono);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-cell-name{font-family:var(--abk-sans);}
.${ROOT_CLASS} .abk-name{font:600 13px var(--abk-sans);color:var(--abk-ink);
  overflow-wrap:anywhere;}
.${ROOT_CLASS} .abk-sub{font:10.5px var(--abk-mono);color:var(--abk-muted);overflow-wrap:anywhere;}
.${ROOT_CLASS} .abk-tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:3px;}
.${ROOT_CLASS} .abk-tag{font:9.5px var(--abk-mono);border:1px solid var(--abk-border);
  border-radius:6px;padding:0 5px;color:var(--abk-muted);}
.${ROOT_CLASS} .abk-badges{display:inline-flex;flex-wrap:wrap;gap:4px;margin-left:6px;}
.${ROOT_CLASS} .abk-disclose{background:none;border:none;cursor:pointer;color:var(--abk-muted);
  font-size:12px;padding:0;}
.${ROOT_CLASS} .abk-spark{width:100%;height:26px;display:block;}
.${ROOT_CLASS} .abk-spark-blank{opacity:0.35;}
.${ROOT_CLASS} .abk-cell-actions{display:flex;gap:6px;justify-content:flex-end;}
/* A dashed Run while a pipeline job runs: a HINT, not a disabled button — the
   chip is advisory (it can lag by one finished job) and the route's 400 is the
   authority, so the button must stay clickable. */
.${ROOT_CLASS} .abk-busy .abk-btn-run{border-style:dashed;}
/* verdict chips + the §4 markers -------------------------------------------- */
.${ROOT_CLASS} .abk-v-pending{color:var(--abk-muted);border-style:dashed;}
.${ROOT_CLASS} .abk-v-none{color:var(--abk-muted);border-style:dashed;}
.${ROOT_CLASS} .abk-v-win{border-color:var(--abk-st-good);color:var(--abk-good-text);
  font-weight:700;}
.${ROOT_CLASS} .abk-v-lose{border-color:var(--abk-st-serious);color:var(--abk-st-serious);
  font-weight:700;}
.${ROOT_CLASS} .abk-v-flat{border-color:var(--abk-border);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-v-inconclusive{border-color:var(--abk-st-warn);color:var(--abk-ink);}
.${ROOT_CLASS} .abk-v-error{border-color:var(--abk-st-critical);color:var(--abk-st-critical);}
.${ROOT_CLASS} .abk-prehorizon{border-style:dashed;}
.${ROOT_CLASS} .abk-insufficient{background:color-mix(in srgb, var(--abk-muted) 14%, transparent);}
.${ROOT_CLASS} .abk-srm-fail{background:var(--abk-st-critical);border-color:var(--abk-st-critical);
  color:var(--abk-card);font-weight:700;}
.${ROOT_CLASS} .abk-badge-guardrail{font:600 9.5px var(--abk-mono);padding:1px 6px;
  border-radius:7px;border:1px solid var(--abk-st-serious);color:var(--abk-st-serious);}
.${ROOT_CLASS} .abk-badge-caveat{font:600 9.5px var(--abk-mono);padding:1px 6px;border-radius:7px;
  border:1px solid var(--abk-st-warn);color:var(--abk-ink-2);cursor:help;}
.${ROOT_CLASS} .abk-badge-lock{font:600 9.5px var(--abk-mono);padding:1px 6px;border-radius:7px;
  border:1px solid var(--abk-border);color:var(--abk-muted);cursor:help;}
.${ROOT_CLASS} .abk-badge-leader{font:600 9.5px var(--abk-mono);padding:1px 6px;border-radius:7px;
  border:1px solid var(--abk-explore-accent);color:var(--abk-explore-accent);cursor:help;}
.${ROOT_CLASS} .abk-pair-role{margin-left:6px;font:500 9px var(--abk-mono);padding:0 4px;
  border:1px solid var(--abk-border);border-radius:3px;color:var(--abk-muted);}
/* row note / message / detail ----------------------------------------------- */
.${ROOT_CLASS} .abk-row-note{font:11px var(--abk-mono);padding:0 12px 7px 44px;
  color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-row-note.abk-srm-fail{color:var(--abk-st-critical);background:none;
  font-weight:700;}
.${ROOT_CLASS} .abk-row-note.abk-v-error{color:var(--abk-st-critical);overflow-wrap:anywhere;}
.${ROOT_CLASS} .abk-row-msg{font:11px var(--abk-mono);padding:0 12px 7px 44px;
  color:var(--abk-ink-2);overflow-wrap:anywhere;}
.${ROOT_CLASS} .abk-row-msg-err{color:var(--abk-st-critical);}
.${ROOT_CLASS} .abk-detail{padding:2px 12px 12px 44px;border-top:1px dashed var(--abk-border);}
.${ROOT_CLASS} .abk-block{margin:8px 0;}
.${ROOT_CLASS} .abk-block-title{font:600 9.5px var(--abk-mono);text-transform:uppercase;
  letter-spacing:0.07em;color:var(--abk-muted);margin-bottom:4px;}
.${ROOT_CLASS} .abk-rationale{font:11px var(--abk-mono);color:var(--abk-ink-2);margin:2px 0;
  line-height:1.5;}
.${ROOT_CLASS} .abk-pair{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin:3px 0;
  font:11px var(--abk-mono);}
.${ROOT_CLASS} .abk-v-word{font-weight:700;}
.${ROOT_CLASS} .abk-pair-effect{color:var(--abk-ink);}
.${ROOT_CLASS} .abk-facts{font:10.5px var(--abk-mono);color:var(--abk-muted);margin-top:6px;}
.${ROOT_CLASS} .abk-btn-row{display:flex;flex-wrap:wrap;gap:6px;}
/* the YAML editor (UI-1) ---------------------------------------------------- */
.${ROOT_CLASS} .abk-editor{margin-top:8px;display:flex;flex-direction:column;gap:8px;}
.${ROOT_CLASS} .abk-create{margin:0 0 12px;padding:10px 12px;background:var(--abk-card);
  border:1px solid var(--abk-border);border-radius:11px;}
.${ROOT_CLASS} .abk-yaml{width:100%;box-sizing:border-box;font:11px var(--abk-mono);
  line-height:1.55;padding:9px 10px;border:1px solid var(--abk-border);border-radius:9px;
  background:var(--abk-page);color:var(--abk-ink);resize:vertical;min-height:180px;
  white-space:pre;overflow-wrap:normal;overflow:auto;}
.${ROOT_CLASS} .abk-yaml:focus{outline:2px solid var(--abk-explore-accent);outline-offset:1px;}
.${ROOT_CLASS} .abk-text{width:100%;box-sizing:border-box;font:11px var(--abk-mono);
  padding:5px 8px;border:1px solid var(--abk-border);border-radius:8px;
  background:var(--abk-page);color:var(--abk-ink);}
.${ROOT_CLASS} .abk-editor-msg{font:11px var(--abk-mono);line-height:1.5;white-space:pre-wrap;
  overflow-wrap:anywhere;color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-editor-msg-ok{color:var(--abk-good-text);}
.${ROOT_CLASS} .abk-editor-msg-err{color:var(--abk-st-critical);}
.${ROOT_CLASS} .abk-confirm{margin-top:8px;border:1px solid var(--abk-st-warn);border-radius:9px;
  padding:8px 10px;background:color-mix(in srgb, var(--abk-st-warn) 10%, transparent);}
.${ROOT_CLASS} .abk-confirm-text{font-size:11.5px;line-height:1.5;margin-bottom:8px;}
/* buttons ------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-btn{font:600 11px var(--abk-sans);padding:5px 10px;border-radius:8px;
  cursor:pointer;border:1px solid var(--abk-border);background:var(--abk-page);
  color:var(--abk-ink);}
.${ROOT_CLASS} .abk-btn:disabled{opacity:0.5;cursor:progress;}
.${ROOT_CLASS} .abk-btn-run{border-color:var(--abk-explore-accent);
  color:var(--abk-explore-accent);}
.${ROOT_CLASS} .abk-btn-danger{border-color:var(--abk-st-critical);color:var(--abk-st-critical);}
.${ROOT_CLASS} .abk-btn-ghost{background:transparent;color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-link{color:var(--abk-explore-accent);overflow-wrap:anywhere;}
/* the job drawer ------------------------------------------------------------ */
.${ROOT_CLASS} .abk-drawer{position:fixed;left:0;right:0;bottom:0;max-height:46vh;
  display:flex;flex-direction:column;background:var(--abk-card);
  border-top:1px solid var(--abk-border);padding:8px 14px 10px;}
.${ROOT_CLASS} .abk-drawer-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px;
  margin-bottom:6px;}
.${ROOT_CLASS} .abk-drawer-label{font:700 11.5px var(--abk-mono);overflow-wrap:anywhere;}
.${ROOT_CLASS} .abk-drawer-status{font:11px var(--abk-mono);color:var(--abk-ink-2);
  display:inline-flex;align-items:center;gap:8px;}
.${ROOT_CLASS} .abk-drawer-list{overflow:auto;max-height:32vh;}
.${ROOT_CLASS} .abk-drawer-log{margin:0;overflow:auto;max-height:34vh;background:var(--abk-page);
  border:1px solid var(--abk-border);border-radius:8px;padding:8px;
  font:10.5px var(--abk-mono);white-space:pre-wrap;}
.${ROOT_CLASS} .abk-log-line{overflow-wrap:anywhere;}
.${ROOT_CLASS} .abk-job-row{display:flex;align-items:baseline;gap:8px;font:10.5px var(--abk-mono);
  padding:2px 0;}
.${ROOT_CLASS} .abk-job-label{flex:1;overflow-wrap:anywhere;}
.${ROOT_CLASS} .abk-job-running{color:var(--abk-explore-accent);font-weight:700;}
.${ROOT_CLASS} .abk-job-done{color:var(--abk-good-text);}
.${ROOT_CLASS} .abk-job-failed{color:var(--abk-st-serious);font-weight:700;}
.${ROOT_CLASS} .abk-job-stopped{color:var(--abk-muted);}
/* empty state + narrow screens --------------------------------------------- */
.${ROOT_CLASS} .abk-empty{font-size:13px;color:var(--abk-ink-2);background:var(--abk-card);
  border:1px dashed var(--abk-border);border-radius:10px;padding:14px;}
@media (max-width: 1100px){
  .${ROOT_CLASS} .abk-head{display:none;}
  .${ROOT_CLASS} .abk-row-main{grid-template-columns:22px 1fr;grid-auto-rows:min-content;}
  .${ROOT_CLASS} .abk-cell-actions{justify-content:flex-start;grid-column:2;}
  .${ROOT_CLASS} .abk-cell-verdict,.${ROOT_CLASS} .abk-cell-effect,
  .${ROOT_CLASS} .abk-cell-p,.${ROOT_CLASS} .abk-cell-time,
  .${ROOT_CLASS} .abk-cell-spark{grid-column:2;}
  .${ROOT_CLASS} .abk-detail,.${ROOT_CLASS} .abk-row-note,
  .${ROOT_CLASS} .abk-row-msg{padding-left:12px;}
}
`;
  const style = document.createElement('style');
  style.setAttribute('data-abk-dashboard', '');
  style.textContent = css;
  document.head.appendChild(style);
}

// ----------------------------------------------------------------------------
// Global entry (the only public surface — no ESM exports)
// ----------------------------------------------------------------------------

(window as unknown as { __ABK_DASHBOARD__: { render: typeof render } }).__ABK_DASHBOARD__ = {
  render,
};
