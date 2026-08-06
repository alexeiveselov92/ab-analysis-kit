// Contract for the abkit experiment report payload.
//
// This JSON object is produced by abkit/reporting/builder.py
// (build_report_payload over persisted _ab_results rows) and baked into a
// self-contained HTML file by abkit/reporting/html_report.py. The report
// renderer (src/report/report.ts, bundled to abkit/reporting/assets/report.js)
// consumes EXACTLY this shape. Keep the Python builder and this file in
// documented lockstep — same keys, same units
// (docs/specs/data-contract-and-reporting.md §5.3).
//
// All timestamps are integer ms-epoch (UTC). Every nullable numeric maps
// NaN and ±inf to null on the Python side. The empty-experiment contract
// keeps every key present with the same shapes (empty series, zero-filled
// observed counts, period.end = 0 sentinel) — the renderer never branches on
// key presence. Explore (M3 WP6/WP7) extends this payload with extra keys;
// the report renderer ignores unknown keys.

/** One cumulative cutoff of one DECLARED arm pair's series (terse §5.3 keys). */
export interface SeriesPoint {
  /** cutoff end_ts, ms epoch */
  t: number;
  /** elapsed_days — the chart x-axis (stabilization is judged over elapsed time) */
  ed: number | null;
  /** effect; null = withheld (demoted row) or degenerate */
  e: number | null;
  /** CI bounds */
  lo: number | null;
  hi: number | null;
  /** p-value */
  p: number | null;
  /** reject at the stored per-row alpha; null = inference withheld */
  rj: 0 | 1 | null;
  /** per-arm sizes (real even on demoted rows) */
  s1: number;
  s2: number;
  /** per-arm stored value/std (WP3 additive keys — §5.2 variant means/lift) */
  v1: number | null;
  v2: number | null;
  sd1: number | null;
  sd2: number | null;
  /** per-arm CUPED covariate means; null unless the method used CUPED */
  cv1: number | null;
  cv2: number | null;
  /** pair MDE from the STORED mde_1/2 columns; null when the row did not compute MDE */
  mde: number | null;
  /** 0/1 flags: is_horizon / decision_blocked (SRM) / insufficient_data */
  hz: 0 | 1;
  blk: 0 | 1;
  ins: 0 | 1;
}

export interface PairBlock {
  /** control variant name */
  c: string;
  /** treatment variant name */
  t: string;
  /** cumulative cutoffs, ascending end_ts; may be empty (never absent) */
  series: SeriesPoint[];
  /** parsed diagnostics of the latest row, or null */
  diag: Record<string, unknown> | null;
}

export interface MethodBlock {
  name: string;
  /** parsed canonical params of the latest stored row (config fallback) */
  params: Record<string, unknown>;
  /** method_config_id — the identity of the persisted series (never null:
   * the builder always emits the config hash, §5.3) */
  id: string;
  /** latest stored row alpha — what actually ran; null for a never-run comparison */
  alpha: number | null;
}

export interface MetricBlock {
  name: string;
  /** from the metric YAML config (D6); null when unknown */
  description: string | null;
  main: boolean;
  guardrail: boolean;
  method: MethodBlock;
  /** metric_query template, deduped to one entry; rendered SQL never enters the payload */
  query: string | null;
  /** the experiment's DECLARED contrast set, in config order, always present:
   * every combinations(arms, 2) pair, or only the control-vs-treatment ones
   * under `contrasts: vs_control` (m13 STAT-1b) */
  pairs: PairBlock[];
  /** parsed + deduped row warnings, order-preserving */
  warnings: string[];
}

export interface GuardrailNote {
  metric: string;
  pair: { c: string; t: string };
  regressed: boolean;
  effect: number | null;
  desired_direction: string;
}

export type VerdictWord = 'WIN' | 'LOSE' | 'FLAT' | 'INCONCLUSIVE';

/**
 * What a verdict is ABOUT (m14 DEC-2/DEC-3). `vs_control` is a ship decision;
 * `treatment_pair` is evidence about two treatments and says nothing about
 * either against the baseline — a `WIN` on `(B, C)` means "C beat B", never
 * "ship C". Absent on every payload baked before 0.9.0, all of which are
 * control-anchored by construction, so `undefined` reads as `vs_control`.
 */
export type PairRole = 'vs_control' | 'treatment_pair';

/** One WP1 readout verdict — per main-metric × DECLARED arm pair. */
export interface VerdictBlock {
  metric: string;
  pair: { c: string; t: string };
  verdict: VerdictWord;
  /** m14 DEC-3; see `PairRole`. Optional so older baked payloads type-check. */
  role?: PairRole;
  rationale: string[];
  caveats: string[];
  significant: boolean;
  effect: number | null;
  pvalue: number | null;
  lo: number | null;
  hi: number | null;
  alpha: number | null;
  mde: number | null;
  min_effect: number | null;
  end_ts: number | null;
  elapsed_days: number | null;
  is_horizon: boolean;
  /**
   * Weekly-cycle coverage fraction (elapsed / 7d) on a decisive verdict called
   * before one full weekly cycle, else null — rendered as a representativeness
   * chip (§6.5). Optional so older baked payloads type-check.
   */
  weekly_cycle_pct?: number | null;
  guardrails: GuardrailNote[];
}

/**
 * How well the leader is separated from the other treatments (m14 DEC-2).
 *
 * `no_leader` is the COMMON state — most experiments do not win — and it is
 * distinct from `separated` on purpose: with no winning arm the
 * `indistinguishable` set is empty, which would otherwise read as "the leader
 * beat everyone" said of an experiment that has no leader. `untested` means
 * "we could not look" (a `contrasts: vs_control` family, or missing/demoted
 * treatment-pair rows), which is a different statement again.
 */
export type SeparationState = 'separated' | 'co_leaders' | 'untested' | 'no_leader';

/**
 * One main metric's arm-level summary (m14 DEC-2/DEC-3) — a read-time
 * composition over the verdicts above, recomputing nothing and persisted
 * nowhere. Present at two arms too (one candidate, a uniform shape); the
 * renderer draws the cross-arm affordances only at 3+ arms.
 */
export interface RollupBlock {
  metric: string;
  /** the arm to ship, or null — chosen ONLY among arms that beat the control */
  leader: string | null;
  /** treatments the leader is not decisively better than */
  indistinguishable: string[];
  separation: SeparationState;
  /** treatments the control beat */
  losers: string[];
  /** arms whose guardrail regressed AGAINST THE CONTROL */
  guardrail_regressed: string[];
  /** the readout's own voice — rendered verbatim, never re-worded here */
  rationale: string[];
  caveats: string[];
}

/** CURRENT experiment health — window-independent (§6 "SRM loud"). */
export interface SrmBlock {
  flag: boolean;
  pvalue: number | null;
  /** whole-cohort exposure counts, declared arms zero-filled */
  observed: Record<string, number>;
  expected: Record<string, number>;
  /**
   * which gate produced flag/pvalue: "chi2" (daily+) or
   * "sequential_multinomial" (the anytime-valid e-process below 1d, WP5). Names
   * the test in the chip; optional so an older bundle defaults to χ².
   */
  kind?: string;
  /**
   * m14 DEC-5(c): WHICH arm the mismatch is concentrated in — a decomposition
   * of the same chi-square, computed server-side so the three surfaces cannot
   * drift. `null` at two arms (the residuals mirror each other, so naming one
   * is a tautology) and absent from every pre-`0.9.0` bake.
   */
  culprit?: { arm: string; residual: number; direction: 'under' | 'over' } | null;
}

/**
 * One scored A/A matrix cell — the renderer projection of an `_ab_aa_runs` row
 * (abkit/reporting/calibration.py `_matrix_row`, lockstep). Every field optional/
 * nullable so a schema addition never breaks an older bundle.
 */
export interface CalibrationRow {
  metric?: string | null;
  method?: string | null;
  method_config_id?: string | null;
  /** single-look (horizon) FPR — the official fixed-horizon rate */
  fpr?: number | null;
  single_look_fpr?: number | null;
  /** cumulative optional-stopping FPR over the grid (the peeking hazard) */
  peeking_fpr?: number | null;
  power?: number | null;
  achieved_mde?: number | null;
  coverage?: number | null;
  effect_exaggeration?: number | null;
  /** nominal per-comparison alpha this cell was scored at */
  alpha?: number | null;
  /** the aa_fpr_budget the cell is coloured against */
  budget?: number | null;
  over_budget?: boolean;
  recommended?: boolean;
  /** why this cell was recommended (in-budget max-power selection, R14) */
  rationale?: string | null;
  verdict?: string | null;
  status?: string | null;
  iterations?: number | null;
  injected_effect?: number | null;
  /** (elapsed_days, cumulative_fpr) per grid look — the peeking-vs-looks curve */
  peeking_curve?: Array<[number, number]> | null;
  /** subsample disclosure ("K/total looks scored"), when the grid was downsampled */
  note?: string | null;
  // M5 D8 — the always-valid (sequential) column, side-by-side with the fixed
  // measurements above (abkit/reporting/calibration.py `_matrix_row`, lockstep).
  /** single-look FPR under the always-valid CI */
  fpr_sequential?: number | null;
  /** cumulative peeking FPR under the always-valid CI — should return to ~α */
  peeking_fpr_sequential?: number | null;
  power_sequential?: number | null;
  coverage_sequential?: number | null;
  effect_exaggeration_sequential?: number | null;
  /** mean fixed / always-valid horizon CI width (the anytime widening) */
  ci_width?: number | null;
  ci_width_sequential?: number | null;
  /** the always-valid peeking curve — flat near α where `peeking_curve` climbs */
  peeking_curve_sequential?: Array<[number, number]> | null;
}

/**
 * M3: always null. The M4 shape lands without a version bump, so the report
 * consumes it tolerantly (every field optional). `matrix_rows` present ⇒ the
 * report renders the A/A calibration matrix section; the chip reads `headline`.
 */
/** The composed multi-metric FWER/FDR sweep (D9/WP8) — the family-level loop that the
 * per-cell peeking FPR does not close. Rendered as a small band above the matrix. */
export interface CalibrationFamily {
  correction?: string | null;
  fwer?: number | null;
  fdr?: number | null;
  /** WP-B (D8×D9) — the composed peeking pair: the fixed-CI family-wise error peeked
   * across looks (the optional-stopping hazard, inflated) and its always-valid twin
   * (controlled, ≈ single-look). null on a sequential-ineligible family. */
  fwer_peeking?: number | null;
  fdr_peeking?: number | null;
  fwer_sequential?: number | null;
  fdr_sequential?: number | null;
  budget?: number | null;
  over_budget?: boolean;
  n_metrics?: number | null;
  n_null_metrics?: number | null;
  metrics?: string[];
  iterations?: number | null;
  valid_iterations?: number | null;
  verdict?: string | null;
}

export interface CalibrationBlock {
  fpr?: number | null;
  peeking_fpr?: number | null;
  alpha?: number | null;
  budget?: number | null;
  headline?: string | null;
  matrix_rows?: CalibrationRow[];
  family?: CalibrationFamily | null;
  report_link?: string | null;
}

export interface ReportPayload {
  /** schema version; bumped on breaking key/unit changes */
  v: number;
  experiment: string;
  project: string | null;
  /** caller-supplied preformatted stamp (never set by the pure builder) */
  generated_at: string | null;
  description: string | null;
  /** ms; end = 0 means no persisted cutoffs; start/horizon are grid facts, always real */
  period: { start: number; end: number; horizon: number };
  /** min cadence step; < 86400 = sub-day (drives the look counter, §4) */
  cadence_seconds: number;
  /** experiment timezone (IANA) */
  tz: string;
  /** variant names, config order. The first is the control ONLY when
   * `control` is absent (a pre-0.9.0 payload) or equal to it — m14 DEC-1 lets
   * an experiment declare any arm as the baseline. */
  arms: string[];
  /** m14 DEC-1: the resolved baseline arm — the one every `effect` on the page
   * is measured against, and `name_1` of every pair that contains it. Optional
   * so older baked payloads type-check; absent means the positional default. */
  control?: string;
  /** m13 STAT-1b: 'all_pairs' | 'vs_control' — which pairs the experiment
   * claims, and therefore what the per-row alpha was divided by. Optional so
   * older baked payloads type-check. */
  contrasts?: string;
  srm: SrmBlock;
  calibration: CalibrationBlock | null;
  /** every DECLARED pair since m14 DEC-3, ship decisions first (the 0.8.0 list
   * is a literal prefix); each carries `role` */
  verdicts: VerdictBlock[];
  /** m14 DEC-3: one per main metric, config order. Optional so older baked
   * payloads type-check — absent means a pre-0.9.0 bake, not "no rollup". */
  rollups?: RollupBlock[];
  /** do the per-metric leaders coincide? null when fewer than two rollups name
   * one — there is nothing to agree about. Optional for the same reason. */
  leaders_agree?: boolean | null;
  metrics: MetricBlock[];
  /** n = cutoffs with ≥1 non-demoted row; planned = the planner grid length */
  look: { n: number; planned: number } | null;
  /** all null in a baked report; the explore server injects at serve time */
  endpoints: Record<string, string | null>;
  /** readout + builder warnings (point-budget clip, orphaned series, …) */
  warnings: string[];
}

/** The report renderer's global entry, exposed by the bundled IIFE. */
export interface AbkReportGlobal {
  render(payload: ReportPayload, mount: HTMLElement): void;
}

/**
 * How the header names the baseline arm (m14 DEC-1).
 *
 * ONE rule, shared, because the report and the explore cockpit both print it
 * and two transcriptions are how two surfaces end up naming different
 * baselines for the same experiment (the STAT-1 `family_divergence` lesson: a
 * fact more than one renderer needs is API, not something each re-infers).
 *
 * The old wording is kept whenever it is TRUE — an absent `control` (any
 * payload baked before 0.9.0) or a control that is the first arm — so a
 * two-arm experiment and every experiment that never declares the field render
 * byte-identically to 0.8.0. It only changes where it had become a lie: with
 * `control: c` on `[a, b, c]` the pair blocks read "c vs a" while the header
 * said the baseline was `a`.
 */
export function baselineNote(payload: ReportPayload): string {
  const control = payload.control;
  if (!control || control === payload.arms[0]) return 'first = control';
  return `control: ${control}`;
}

/**
 * The baseline arm every effect on the page is measured against (m14 DEC-1).
 *
 * Shared for the same reason `baselineNote` is: a surface that re-derives the
 * baseline can name a different arm than the sentence printed above it. The
 * fallback is the positional convention, which is exactly what a payload
 * without the key was baked under.
 */
export function controlArm(payload: ReportPayload): string {
  return payload.control ?? payload.arms[0];
}
