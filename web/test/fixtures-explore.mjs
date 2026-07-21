// Typed explore-payload fixtures for the jsdom smoke suite.
//
// This file IS type-checked (`npm run check` — tsconfig checkJs) against the
// lockstep contract in src/explore/payload.ts, so a payload.py / server.py
// key rename that updates payload.ts breaks this file at check time — the
// fixtures can never silently drift from the schema the cockpit consumes
// (the WP3 discipline extended to WP7).

import { makePayload, makeThreeArmPayload } from './fixtures.mjs';

/**
 * @param {string} name
 * @param {Partial<import('../src/explore/payload').KnobSpec>} [overrides]
 * @returns {import('../src/explore/payload').KnobSpec}
 */
export function makeSpec(name, overrides = {}) {
  return {
    name,
    type: 'str',
    default: null,
    identity: true,
    choices: null,
    minimum: null,
    maximum: null,
    exclusive_bounds: false,
    description: `the ${name} knob`,
    ...overrides,
  };
}

/**
 * @returns {import('../src/explore/payload').MethodSurface[]}
 */
export function makeMethods() {
  return [
    {
      name: 't-test',
      seeded: false,
      needs_covariate: false,
      alpha_tier: 'E',
      correction_tier: 'E',
      params: [
        makeSpec('test_type', { default: 'relative', choices: ['relative', 'absolute'] }),
        makeSpec('calculate_mde', { type: 'bool', default: false, identity: true }),
        makeSpec('power', {
          type: 'float',
          default: 0.8,
          minimum: 0,
          maximum: 1,
          exclusive_bounds: true,
        }),
      ],
      tiers: { test_type: 'E', calculate_mde: 'E', power: 'E' },
    },
    {
      name: 'cuped-t-test',
      seeded: false,
      needs_covariate: true,
      alpha_tier: 'E',
      correction_tier: 'E',
      params: [
        makeSpec('test_type', { default: 'relative', choices: ['relative', 'absolute'] }),
        makeSpec('covariate_lookback', { type: 'str|int', default: null }),
      ],
      tiers: { test_type: 'E', covariate_lookback: 'R' },
    },
    {
      name: 'bootstrap',
      seeded: true,
      needs_covariate: false,
      alpha_tier: 'S',
      correction_tier: 'S',
      params: [
        makeSpec('n_samples', { type: 'int', default: 1000, minimum: 1 }),
        makeSpec('stat', { default: 'mean' }),
        makeSpec('pvalue_kind', { default: 'sign', choices: ['plugin', 'sign'] }),
        makeSpec('seed', { type: 'int', identity: false }),
      ],
      tiers: { n_samples: 'S', stat: 'S', pvalue_kind: 'S', seed: 'S' },
    },
  ];
}

/**
 * @param {Partial<import('../src/explore/payload').CalibrationStatus>} [overrides]
 * @returns {import('../src/explore/payload').CalibrationStatus}
 */
export function makeCalibration(overrides = {}) {
  return {
    state: 'uncalibrated',
    alpha: 0.05,
    fpr: null,
    peeking_fpr: null,
    calibrated_alpha: null,
    budget: 0.075,
    over_budget: null,
    runs: 0,
    headline: 'uncalibrated — run `abk validate` (M4)',
    ...overrides,
  };
}

/**
 * @param {Partial<import('../src/explore/payload').MetricSurface>} [overrides]
 * @returns {import('../src/explore/payload').MetricSurface}
 */
export function makeSurface(overrides = {}) {
  return {
    metric: 'revenue',
    metric_type: 'sample',
    configured: {
      method: 't-test',
      params: { test_type: 'relative' },
      method_config_id: 'a'.repeat(16),
      alpha: 0.05,
    },
    methods: makeMethods(),
    cache: {
      cutoffs: Array.from({ length: 14 }, (_, i) => Date.UTC(2026, 0, 2 + i)),
      covariate_cutoffs: [],
      disabled_reason: null,
    },
    calibration: makeCalibration(),
    ...overrides,
  };
}

/**
 * The baked explore payload: the report payload riding verbatim + the
 * explore block + null endpoint slots (the static `--no-serve` shape).
 * @param {Partial<import('../src/explore/payload').ExplorePayload>} [overrides]
 * @returns {import('../src/explore/payload').ExplorePayload}
 */
export function makeExplorePayload(overrides = {}) {
  return {
    ...makePayload({ experiment: 'explore_exp' }),
    explore: {
      metrics: { revenue: makeSurface() },
      default_metric: 'revenue',
      experiment: {
        alpha: 0.05,
        correction: 'bonferroni',
        correction_choices: ['none', 'bonferroni', 'benjamini_hochberg'],
        groups_count: 2,
        non_main_count: 0,
      },
      cache: { values: 28000, disabled_reason: null },
      warnings: [],
    },
    save_url: null,
    recompute_url: null,
    reload_url: null,
    validate_url: null,
    ...overrides,
  };
}

/**
 * A 3-arm variant of makeExplorePayload — control + two treatments, riding
 * `makeThreeArmPayload`'s two VerdictBlocks for the "revenue" metric
 * verbatim. WP0 Review-mode regression fixture (see makeThreeArmPayload).
 * @param {Partial<import('../src/explore/payload').ExplorePayload>} [overrides]
 * @returns {import('../src/explore/payload').ExplorePayload}
 */
export function makeThreeArmExplorePayload(overrides = {}) {
  const threeArm = makeThreeArmPayload({ experiment: 'explore_exp' });
  return {
    ...threeArm,
    explore: {
      metrics: { revenue: makeSurface() },
      default_metric: 'revenue',
      experiment: {
        alpha: 0.05,
        correction: 'bonferroni',
        correction_choices: ['none', 'bonferroni', 'benjamini_hochberg'],
        groups_count: 3,
        non_main_count: 0,
      },
      cache: { values: 28000, disabled_reason: null },
      warnings: [],
    },
    save_url: null,
    recompute_url: null,
    reload_url: null,
    validate_url: null,
    ...overrides,
  };
}

/**
 * One /validate reply (server._run_validate — Auto mode, WP6).
 * @param {number | null} requestId
 * @param {Partial<import('../src/explore/payload').ValidateReply>} [overrides]
 * @returns {import('../src/explore/payload').ValidateReply}
 */
export function makeValidateReply(requestId, overrides = {}) {
  return {
    request_id: requestId,
    recommended: {
      revenue: {
        method: { name: 't-test', params: { test_type: 'relative' } },
        alpha: 0.05,
        verdict: 't-test on revenue: well-calibrated, FPR 4.9%',
        calibration: makeCalibration({
          state: 'calibrated',
          fpr: 0.049,
          calibrated_alpha: 0.05,
          over_budget: false,
          runs: 1,
          headline: 'calibrated — FPR 4.9% vs nominal α=0.05',
        }),
      },
    },
    log: ['select: revenue: highest power among methods with FPR within budget'],
    ...overrides,
  };
}

/**
 * One /recompute reply (server._result_json — FULL key names, ms-epoch ints).
 * @param {number | null} requestId
 * @param {Partial<import('../src/explore/payload').RecomputeReply>} [overrides]
 * @returns {import('../src/explore/payload').RecomputeReply}
 */
export function makeReply(requestId, overrides = {}) {
  /** @type {import('../src/explore/payload').ReplyPoint[]} */
  const points = Array.from({ length: 14 }, (_, i) => ({
    end_ts: Date.UTC(2026, 0, 2 + i),
    elapsed_days: i + 1,
    tier: 'exact',
    effect: 0.12,
    left_bound: 0.06,
    right_bound: 0.18,
    pvalue: 0.002,
    reject: true,
    mde_1: 0.05,
    mde_2: 0.05,
    value_1: 10.0,
    value_2: 11.2,
    std_1: 2.0,
    std_2: 2.1,
    size_1: 1000,
    size_2: 1000,
    insufficient: false,
    warnings: [],
  }));
  return {
    request_id: requestId,
    metric: 'revenue',
    method: 't-test',
    method_config_id: 'a'.repeat(16),
    alpha: 0.05,
    identity_changed: false,
    warnings: [],
    calibration: makeCalibration(),
    pairs: [
      {
        name_1: 'control',
        name_2: 'treatment',
        chips: {
          lift: 0.12,
          ci_half: 0.06,
          pvalue: 0.002,
          power: 0.91,
          power_note: null,
          latest_end_ts: Date.UTC(2026, 0, 15),
          tier: 'exact',
        },
        points,
      },
    ],
    ...overrides,
  };
}
