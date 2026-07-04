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

/** One cumulative cutoff of one control-vs-treatment series (terse §5.3 keys). */
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
  /** all combinations(arms, 2) in config order, always present */
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

/** One WP1 readout verdict — per main-metric × control-vs-treatment pair. */
export interface VerdictBlock {
  metric: string;
  pair: { c: string; t: string };
  verdict: VerdictWord;
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
  guardrails: GuardrailNote[];
}

/** CURRENT experiment health — window-independent (§6 "SRM loud"). */
export interface SrmBlock {
  flag: boolean;
  pvalue: number | null;
  /** whole-cohort exposure counts, declared arms zero-filled */
  observed: Record<string, number>;
  expected: Record<string, number>;
}

/**
 * M3: always null. The M4 shape lands without a version bump, so the report
 * consumes it tolerantly (every field optional).
 */
export interface CalibrationBlock {
  fpr?: number | null;
  peeking_fpr?: number | null;
  headline?: string | null;
  matrix_rows?: unknown[];
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
  /** variant names, config order; first = control */
  arms: string[];
  srm: SrmBlock;
  calibration: CalibrationBlock | null;
  verdicts: VerdictBlock[];
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
