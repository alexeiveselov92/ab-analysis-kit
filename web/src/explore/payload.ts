// Contract for the abkit explore payload + the explore server's wire replies.
//
// The explore payload is the report payload (../shared/payload.ts, lockstep
// with abkit/reporting/builder.py) riding VERBATIM, extended by
// abkit/tuning/payload.py (build_explore_payload) with the `explore` block
// and four TOP-LEVEL endpoint slots, then baked by abkit/tuning/html.py.
// Keep this file in documented lockstep with abkit/tuning/payload.py,
// abkit/tuning/recompute.py (knob_surface + the reply dataclasses) and
// abkit/tuning/server.py (_result_json, /apply) — same keys, same units.
//
// THE naming pitfall (m3-implementation-plan.md WP7): the baked baseline
// series uses the TERSE report point keys (SeriesPoint: t/ed/e/lo/hi/…),
// while /recompute + /reload replies use FULL key names (ReplyPoint:
// end_ts/elapsed_days/effect/…) — the client codes against BOTH and never
// mixes them. All timestamps are integer ms-epoch UTC; NaN/±inf are null.

import type { ReportPayload } from '../shared/payload';

// ----------------------------------------------------------------------------
// The knob surface (payload.explore.metrics[<name>], recompute.knob_surface)
// ----------------------------------------------------------------------------

/** D1 knob-classification letters (NOT the reply point tiers). */
export type KnobTier = 'E' | 'S' | 'R';
/** Tier the experiment-level alpha/correction knob recomputes through
 * (the 'alpha' α-inversion value retired in M9 WP2 — CUPED is Tier E now). */
export type AlphaKnobTier = 'E' | 'S';

/** One ParamSpec as baked by recompute._spec_payload (D12: the rail is
 * auto-derived from these — a knob without a spec cannot appear). */
export interface KnobSpec {
  name: string;
  /** pipe-joined Python type names, e.g. "str" / "int" / "str|int" */
  type: string;
  default: unknown;
  /** identity-bearing → the "⚠ changes the results series" badge */
  identity: boolean;
  choices: string[] | null;
  minimum: number | null;
  maximum: number | null;
  exclusive_bounds: boolean;
  description: string;
}

export interface MethodSurface {
  name: string;
  /** resampling family (has a `seed` spec) — no power solve, Tier-S grid */
  seeded: boolean;
  /** requires_covariate — the ↻ badge substrate when cache.covariate_cutoffs
   * is empty (a covariate must be reloaded before this method can answer) */
  needs_covariate: boolean;
  alpha_tier: AlphaKnobTier;
  correction_tier: AlphaKnobTier;
  params: KnobSpec[];
  /** per-knob D1 tier; covariate_lookback is always "R" */
  tiers: Record<string, KnobTier>;
}

/** The D3 calibration chip state — identical key set in the baked payload
 * (per metric, keyed by the CONFIGURED (method_config_id, alpha)) and in
 * every /recompute reply (re-keyed live). */
export interface CalibrationStatus {
  state: 'uncalibrated' | 'calibrated' | 'alpha_mismatch';
  alpha: number;
  fpr: number | null;
  peeking_fpr: number | null;
  /** M5 D8 — the always-valid peeking FPR (the recovery to ~α); surfaced in `headline` */
  peeking_fpr_sequential?: number | null;
  calibrated_alpha: number | null;
  budget: number | null;
  over_budget: boolean | null;
  runs: number;
  /** render-ready one-liner (aa-false-positive-matrix.md §3 format) */
  headline: string;
}

export interface MetricSurface {
  metric: string;
  metric_type: 'sample' | 'fraction' | 'ratio';
  configured: {
    method: string;
    params: Record<string, unknown>;
    method_config_id: string;
    /** the CONFIGURED effective per-comparison alpha */
    alpha: number;
  };
  /** only methods valid for this metric type, non-paired */
  methods: MethodSurface[];
  cache: {
    /** Tier-S-cached cutoffs (ms-epoch ints) */
    cutoffs: number[];
    /** cached cutoffs where every variant carries a covariate */
    covariate_cutoffs: number[];
    disabled_reason: string | null;
  };
  calibration: CalibrationStatus;
}

/** The experiment-level knob substrate (WP7): the client mirrors
 * analyze.effective_alphas over these to resolve raw alpha + correction into
 * the EFFECTIVE per-comparison alpha every /recompute sends. */
export interface ExperimentKnobs {
  /** resolved RAW experiment alpha (experiment.alpha ?? project default) */
  alpha: number;
  correction: string;
  correction_choices: string[];
  groups_count: number;
  /** comparisons with is_main_metric=false (the secondary Bonferroni tier) */
  non_main_count: number;
}

export interface ExploreBlock {
  metrics: Record<string, MetricSurface>;
  /** the first is_main_metric comparison, else the first metric; null = none */
  default_metric: string | null;
  experiment: ExperimentKnobs;
  cache: { values: number; disabled_reason: string | null };
  warnings: string[];
}

/** The baked explore payload: the report payload + the explore block + the
 * TOP-LEVEL endpoint slots (null in the static `--no-serve` page; the server
 * injects tokened URLs post-bind — the nested report `endpoints` block stays
 * all-null even when served, read the top-level slots). */
export interface ExplorePayload extends ReportPayload {
  explore: ExploreBlock;
  save_url: string | null;
  recompute_url: string | null;
  reload_url: string | null;
  validate_url: string | null;
}

// ----------------------------------------------------------------------------
// /recompute + /reload replies (server._result_json — FULL key names)
// ----------------------------------------------------------------------------

/** Reply point tier strings (NOT the knob-classification letters):
 * "exact" = Tier-E suffstats or Tier-S cache recompute; "approx" =
 * α-inversion of a closed-form row (render hatched); "baseline" = persisted
 * numbers passed through untouched (demoted/NULLed/same-identity rows). */
export type PointTier = 'exact' | 'approx' | 'baseline';

export interface ReplyPoint {
  end_ts: number;
  elapsed_days: number | null;
  tier: PointTier;
  effect: number | null;
  left_bound: number | null;
  right_bound: number | null;
  pvalue: number | null;
  reject: boolean | null;
  /** null on tier="approx" (the stored MDE was solved at the old alpha) */
  mde_1: number | null;
  mde_2: number | null;
  value_1: number | null;
  value_2: number | null;
  std_1: number | null;
  std_2: number | null;
  /** ALWAYS the row's persisted unit counts, every tier */
  size_1: number | null;
  size_2: number | null;
  /** the demoted-row pass-through flag */
  insufficient: boolean;
  warnings: string[];
}

/** The windshield chips, off the latest point WITH inference (§5.1). */
export interface ReplyChips {
  lift: number | null;
  ci_half: number | null;
  pvalue: number | null;
  power: number | null;
  /** honest reason string when power is null */
  power_note: string | null;
  latest_end_ts: number | null;
  tier: PointTier | null;
}

export interface ReplyPair {
  name_1: string;
  name_2: string;
  chips: ReplyChips;
  points: ReplyPoint[];
}

export interface RecomputeReply {
  /** echoed; null if none was sent */
  request_id: number | null;
  metric: string;
  /** canonical method name */
  method: string;
  /** the LIVE identity of this knob state */
  method_config_id: string;
  alpha: number;
  /** live id != the configured comparison's id — a DIFFERENT results series */
  identity_changed: boolean;
  warnings: string[];
  calibration: CalibrationStatus;
  pairs: ReplyPair[];
}

/** The 409 body of a dropped-stale /recompute or /reload. */
export interface StaleReply {
  stale: true;
  request_id: number;
}

// ----------------------------------------------------------------------------
// /validate reply (Auto mode — server _run_validate, WP6)
// ----------------------------------------------------------------------------

/** One metric's Auto-mode recommendation: the knob state to re-seed the rail
 * with + the REFRESHED calibration (read off the just-mutated session.aa_rows
 * so the chip greens without an explore restart, D11). Keep in lockstep with
 * abkit/tuning/server.py `_run_validate`. */
export interface ValidateRecommendation {
  method: { name: string; params: Record<string, unknown> };
  /** the effective per-comparison alpha the FPR was measured at */
  alpha: number;
  verdict: string;
  calibration: CalibrationStatus;
}

/** The POST /validate reply (Auto mode): the recommended knob state + refreshed
 * calibration per metric, plus the decision log. */
export interface ValidateReply {
  /** echoed; null if none was sent */
  request_id: number | null;
  recommended: Record<string, ValidateRecommendation>;
  log: string[];
}

// ----------------------------------------------------------------------------
// /apply (server._handle_apply — request and 200 reply)
// ----------------------------------------------------------------------------

/** One DIRTY comparison (the donor's dirty-slot discipline — a merely-viewed
 * comparison is never sent). A method/param edit carries the FULL param set;
 * a role-only flip carries NO method key (an absent `params` key must stay
 * absent — the writer's method-switch guard reads the absence). */
export interface ApplyComparison {
  metric: string;
  method?: { name: string; params: Record<string, unknown> };
  is_main_metric?: boolean;
  is_guardrail?: boolean;
}

export interface ApplyRequest {
  comparisons: ApplyComparison[];
  /** RAW experiment-level alpha (written to YAML) — NOT the effective one */
  alpha?: number;
  correction?: string;
  /** required (true) while the D3 gate is not green — the confirmed cost */
  confirm_uncalibrated?: boolean;
}

export interface OrphanedSeries {
  metric: string;
  old_id: string;
  new_id: string;
  rows: number;
}

export interface ApplyReply {
  saved: string;
  archived: string;
  updated: string[];
  preserved: string[];
  experiment_fields: string[];
  orphaned: OrphanedSeries[];
  /** null when `orphaned` is empty; quotes `abk clean` when present */
  orphan_warning: string | null;
}

/** The explore renderer's global entry, exposed by the bundled IIFE. */
export interface AbkExploreGlobal {
  render(payload: ExplorePayload, mount: HTMLElement): void;
}
