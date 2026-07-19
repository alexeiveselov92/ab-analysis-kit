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

/**
 * A 3-arm variant of makePayload: control + two treatments, with the main
 * metric ("revenue") carrying TWO VerdictBlocks — one per declared
 * control-vs-treatment pair (control-vs-treatment, control-vs-treatment_b).
 * `abkit/pipeline/readout.py` verdicts control-vs-EACH-arm (never
 * treatment-vs-treatment), so this is the realistic multi-arm shape.
 *
 * Regression fixture for the WP0 Review-mode bug: `payload.verdicts` holds
 * one block per (metric, pair); a naive `.find` on metric name alone renders
 * only the first pair and silently drops the rest.
 * @param {Partial<import('../src/shared/payload').ReportPayload>} [overrides]
 * @returns {import('../src/shared/payload').ReportPayload}
 */
export function makeThreeArmPayload(overrides = {}) {
  const base = makePayload();
  const seriesB = Array.from({ length: 14 }, (_, i) =>
    makePoint(i + 1, { e: -0.05, lo: -0.09, hi: -0.01 }),
  );
  return {
    ...base,
    arms: ['control', 'treatment', 'treatment_b'],
    srm: {
      flag: false,
      pvalue: 0.8,
      observed: { control: 1000, treatment: 1000, treatment_b: 1000 },
      expected: { control: 1 / 3, treatment: 1 / 3, treatment_b: 1 / 3 },
    },
    verdicts: [
      ...base.verdicts,
      {
        metric: 'revenue',
        pair: { c: 'control', t: 'treatment_b' },
        verdict: 'LOSE',
        rationale: ['CI excludes zero (negative) with a consistent sign over the trailing 7 days'],
        caveats: [],
        significant: true,
        effect: -0.05,
        pvalue: 0.01,
        lo: -0.09,
        hi: -0.01,
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
        ...base.metrics[0],
        pairs: [
          ...base.metrics[0].pairs,
          { c: 'control', t: 'treatment_b', series: seriesB, diag: null },
          { c: 'treatment', t: 'treatment_b', series: seriesB, diag: null },
        ],
      },
    ],
    ...overrides,
  };
}

/**
 * A filled M4 calibration block (the `abk validate` matrix) for the matrix-section
 * smoke tests — one in-budget recommended cell with a peeking curve, one over-budget
 * cell with a subsample note.
 * @param {Partial<import('../src/shared/payload').CalibrationBlock>} [overrides]
 * @returns {import('../src/shared/payload').CalibrationBlock}
 */
export function makeCalibration(overrides = {}) {
  return {
    fpr: 0.052,
    peeking_fpr: 0.14,
    alpha: 0.05,
    budget: 0.075,
    headline:
      'nominal α 5.0% · single-look FPR 5.2% · peeking FPR 14.0% · 1 method(s) over budget',
    report_link: null,
    matrix_rows: [
      {
        metric: 'revenue',
        method: 'cuped-t-test',
        method_config_id: 'c'.repeat(16),
        fpr: 0.052,
        single_look_fpr: 0.052,
        peeking_fpr: 0.14,
        power: null,
        achieved_mde: 0.031,
        coverage: 0.95,
        effect_exaggeration: null,
        alpha: 0.05,
        budget: 0.075,
        over_budget: false,
        recommended: true,
        rationale: 'highest power among methods with FPR within budget',
        verdict: 'cuped-t-test on revenue: well-calibrated, FPR 5.2%',
        status: 'success',
        iterations: 2000,
        injected_effect: null,
        peeking_curve: [
          [1, 0.05],
          [7, 0.1],
          [14, 0.14],
        ],
        note: null,
      },
      {
        metric: 'revenue',
        method: 'naive-t-test',
        method_config_id: 'd'.repeat(16),
        fpr: 0.11,
        single_look_fpr: 0.11,
        peeking_fpr: 0.28,
        power: null,
        achieved_mde: null,
        coverage: 0.88,
        effect_exaggeration: 0.02,
        alpha: 0.05,
        budget: 0.075,
        over_budget: true,
        recommended: false,
        rationale: null,
        verdict: 'naive-t-test on revenue: FPR inflated to 11%, do not use',
        status: 'success',
        iterations: 2000,
        injected_effect: null,
        peeking_curve: null,
        note: '5/40 looks scored (denser-early subsample)',
      },
    ],
    ...overrides,
  };
}
