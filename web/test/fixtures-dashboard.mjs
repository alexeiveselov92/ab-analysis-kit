// Typed dashboard-payload fixtures for the jsdom smoke suite.
//
// This file IS type-checked (`npm run check` — tsconfig checkJs) against the
// lockstep contract in src/dashboard/payload.ts, so an overview.py /
// dashboard_server.py / jobs.py key rename that updates payload.ts breaks this
// file at check time — the fixtures can never silently drift from the shapes
// the cockpit consumes (the WP3/WP7 discipline extended to DASH-5).

/**
 * One boot entry: config metadata only, exactly what `GET /` bakes.
 * @param {string} name
 * @param {Partial<import('../src/dashboard/payload').BootEntry>} [overrides]
 * @returns {import('../src/dashboard/payload').BootEntry}
 */
export function makeBootEntry(name, overrides = {}) {
  return {
    name,
    dir: '',
    file: `experiments/${name}.yml`,
    tags: ['growth'],
    status: 'running',
    timezone: 'UTC',
    start_ts: Date.UTC(2026, 0, 1),
    horizon_ts: Date.UTC(2026, 0, 15),
    main_metric: 'revenue',
    comparisons: [
      { metric: 'revenue', is_main_metric: true },
      { metric: 'refunds', is_main_metric: false },
    ],
    ...overrides,
  };
}

/**
 * The baked boot payload (metadata only — no statistics, no token).
 * @param {Partial<import('../src/dashboard/payload').DashboardPayload>} [overrides]
 * @returns {import('../src/dashboard/payload').DashboardPayload}
 */
export function makeDashboardPayload(overrides = {}) {
  return {
    project: 'acme',
    profile: 'dev',
    version: '0.5.0',
    initial_window: '30d',
    window_presets: ['24h', '7d', '30d', '90d', 'all'],
    generated_at: Date.UTC(2026, 0, 15, 12, 30),
    experiments: [makeBootEntry('dash_exp')],
    ...overrides,
  };
}

/** N boot entries named exp_0 … exp_{n-1}, for the worker-pool assertions.
 * @param {number} n
 * @returns {import('../src/dashboard/payload').BootEntry[]}
 */
export function makeManyEntries(n) {
  return Array.from({ length: n }, (_, i) => makeBootEntry(`exp_${i}`));
}

/**
 * One statistics row (`GET /api/stats/<experiment>`), the full DASH-2 shape:
 * every key present at a real value, so an override is the only difference a
 * test has to reason about.
 * @param {string} name
 * @param {Partial<import('../src/dashboard/payload').ExperimentRow>} [overrides]
 * @returns {import('../src/dashboard/payload').ExperimentRow}
 */
export function makeRow(name, overrides = {}) {
  return {
    name,
    dir: '',
    file: `experiments/${name}.yml`,
    tags: ['growth'],
    status: 'running',
    timezone: 'UTC',
    start_ts: Date.UTC(2026, 0, 1),
    horizon_ts: Date.UTC(2026, 0, 15),
    main_metric: 'revenue',
    locked: false,
    verdict: 'WIN',
    srm_flag: false,
    srm_pvalue: 0.8,
    effect: 0.12,
    ci: [0.06, 0.18],
    pvalue: 0.002,
    alpha: 0.05,
    elapsed_days: 14,
    is_horizon: true,
    weekly_cycle_pct: null,
    insufficient: false,
    rationale: ['significant and sign-consistent over the trailing 3 days'],
    caveats: [],
    guardrail_regressed: false,
    last_end_ts: Date.UTC(2026, 0, 15),
    spark: Array.from({ length: 14 }, (_, i) => [Date.UTC(2026, 0, 2 + i), 0.1 + i / 200]),
    verdicts: [
      {
        metric: 'revenue',
        pair: { c: 'control', t: 'treatment' },
        verdict: 'WIN',
        effect: 0.12,
        caveats: [],
        guardrail_regressed: false,
      },
    ],
    warnings: [],
    error: null,
    ...overrides,
  };
}

/**
 * One job summary (`GET /api/jobs` → `jobs[]`).
 * @param {Partial<import('../src/dashboard/payload').JobSummary>} [overrides]
 * @returns {import('../src/dashboard/payload').JobSummary}
 */
export function makeJobSummary(overrides = {}) {
  return {
    id: 'ab12cd34',
    kind: 'run',
    label: 'abk run --select experiments/dash_exp.yml',
    experiment: 'dash_exp',
    status: 'running',
    returncode: null,
    url: null,
    started_at: Date.UTC(2026, 0, 15, 12, 0),
    finished_at: null,
    ...overrides,
  };
}

/**
 * `GET /api/jobs` — the list plus the SERVER's one-at-a-time flag.
 * @param {import('../src/dashboard/payload').JobSummary[]} jobs
 * @param {boolean} pipelineActive
 * @returns {import('../src/dashboard/payload').JobsReply}
 */
export function makeJobsReply(jobs = [], pipelineActive = false) {
  return { jobs, pipeline_active: pipelineActive };
}

/**
 * One job poll reply (`GET /api/job/<id>?offset=`) — ABSOLUTE line offsets.
 * @param {Partial<import('../src/dashboard/payload').JobSnapshot>} [overrides]
 * @returns {import('../src/dashboard/payload').JobSnapshot}
 */
export function makeJobSnapshot(overrides = {}) {
  const lines = overrides.lines ?? ['LOAD …', 'COMPUTE …'];
  return {
    id: 'ab12cd34',
    kind: 'run',
    label: 'abk run --select experiments/dash_exp.yml',
    experiment: 'dash_exp',
    status: 'running',
    returncode: null,
    url: null,
    next_offset: lines.length,
    dropped: 0,
    truncated: false,
    ...overrides,
    lines,
  };
}
