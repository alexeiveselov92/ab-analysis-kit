// Typed payload fixtures for the jsdom smoke suite.
//
// This file IS type-checked (`npm run check` — tsconfig checkJs) against the
// lockstep contract in src/shared/payload.ts, so a builder-side key rename or
// nullability change that updates payload.ts breaks this file at check time —
// the fixtures can never silently drift from the schema the renderer consumes
// (WP3 adversarial-review finding).

/**
 * @param {number} day
 * @param {Partial<import('../src/shared/payload').SeriesPoint>} [overrides]
 * @returns {import('../src/shared/payload').SeriesPoint}
 */
export function makePoint(day, overrides = {}) {
  return {
    t: Date.UTC(2026, 0, 1 + day),
    ed: day,
    e: 0.1,
    lo: 0.05,
    hi: 0.15,
    p: 0.001,
    rj: 1,
    s1: 1000,
    s2: 1000,
    v1: 10.0,
    v2: 11.0,
    sd1: 2.0,
    sd2: 2.0,
    cv1: null,
    cv2: null,
    mde: 0.04,
    hz: day >= 14 ? 1 : 0,
    blk: 0,
    ins: 0,
    ...overrides,
  };
}

/**
 * @param {Partial<import('../src/shared/payload').ReportPayload>} [overrides]
 * @returns {import('../src/shared/payload').ReportPayload}
 */
export function makePayload(overrides = {}) {
  const series = Array.from({ length: 14 }, (_, i) => makePoint(i + 1));
  return {
    v: 1,
    experiment: 'report_exp',
    project: 'acme',
    generated_at: '2026-01-15 12:00 UTC',
    description: 'Signup flow experiment',
    period: {
      start: Date.UTC(2026, 0, 1),
      end: Date.UTC(2026, 0, 15),
      horizon: Date.UTC(2026, 0, 15),
    },
    cadence_seconds: 86400,
    tz: 'UTC',
    arms: ['control', 'treatment'],
    srm: {
      flag: false,
      pvalue: 0.8,
      observed: { control: 1000, treatment: 1000 },
      expected: { control: 0.5, treatment: 0.5 },
    },
    calibration: null,
    verdicts: [
      {
        metric: 'revenue',
        pair: { c: 'control', t: 'treatment' },
        verdict: 'WIN',
        rationale: ['CI excludes zero with a consistent sign over the trailing 7 days'],
        caveats: [],
        significant: true,
        effect: 0.1,
        pvalue: 0.001,
        lo: 0.05,
        hi: 0.15,
        alpha: 0.05,
        mde: 0.04,
        min_effect: null,
        end_ts: Date.UTC(2026, 0, 15),
        elapsed_days: 14.0,
        is_horizon: true,
        guardrails: [],
      },
    ],
    metrics: [
      {
        name: 'revenue',
        description: 'revenue per user',
        main: true,
        guardrail: false,
        method: {
          name: 't-test',
          params: { test_type: 'relative' },
          id: 'a'.repeat(16),
          alpha: 0.05,
        },
        query: 'SELECT 1',
        pairs: [{ c: 'control', t: 'treatment', series, diag: null }],
        warnings: [],
      },
    ],
    look: { n: 14, planned: 14 },
    endpoints: { save_url: null, recompute_url: null, reload_url: null, validate_url: null },
    warnings: [],
    ...overrides,
  };
}
