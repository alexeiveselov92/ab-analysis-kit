// abkit HTML report renderer — the self-contained offline readout.
//
// Consumes the ReportPayload contract (../shared/payload.ts, lockstep with
// abkit/reporting/builder.py) baked by abkit/reporting/html_report.py into a
// standalone HTML file, and paints the experiment readout into a mount:
// header + SRM/calibration/look chips, one verdict banner per main-metric ×
// pair (rationale + caveats + guardrails), and per metric × pair the
// stabilization chart (cumulative effect + CI band vs elapsed days, zero
// line, horizon marker, §4 peeking-honesty treatments) with hover readout and
// wheel-zoom/drag-pan, four small-multiple views (variant means, pair MDE,
// p-value vs alpha, avg group size — one axis each, never dual-axis), and a
// results/audit table.
//
// Peeking honesty (data-contract-and-reporting.md §4) carries stable
// machine-checkable markers so WP10 and the CI bundle gate can assert them:
//   .abk-prehorizon   — pre-horizon fixed CIs are dashed/de-emphasized
//   .abk-insufficient — insufficient_data cutoffs greyed, counts + SRM only
//   .abk-srm-fail     — the red SRM gate chip
//
// It is bundled (esbuild → IIFE) to abkit/reporting/assets/report.js, which
// assigns `window.__ABK_REPORT__ = { render }`. Nothing is exported for ESM —
// the global is the public surface (AbkReportGlobal). Styling is injected
// once, scoped under the .abk-report root class; all colors resolve through
// the one brand-token layer (shared/chart.ts TOKEN_FALLBACKS +
// branding-and-site.md §3) so the finalized palette drops in without touching
// renderer code.

import {
  type BandPoint,
  type Domain,
  type Margins,
  type Scales,
  TOKEN_FALLBACKS,
  drawGridAndAxes,
  drawHLine,
  drawSeriesDecimated,
  drawVDivider,
  fillBand,
  fillVSpan,
  fit,
  fmtDate,
  fmtEd,
  fmtP,
  fmtSigned,
  fmtTs,
  fmtVal,
  makeScales,
  plotRect,
  rgba,
  scoredRuns,
  token,
} from '../shared/chart';
import type {
  CalibrationRow,
  MetricBlock,
  PairBlock,
  ReportPayload,
  SeriesPoint,
  VerdictBlock,
} from '../shared/payload';

// ----------------------------------------------------------------------------
// Constants + tiny helpers
// ----------------------------------------------------------------------------

const ROOT_CLASS = 'abk-report';
const MARGINS: Margins = { l: 56, r: 16, t: 14, b: 28 };
const MS_PER_DAY = 86400000;

const esc = (s: unknown): string =>
  String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

const clamp = (x: number, a: number, b: number): number => Math.max(a, Math.min(b, x));

function el(tag: string, cls?: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

const num = (v: number | null): number => (v === null ? NaN : v);
const dash = (v: number | null, fmt: (x: number) => string = fmtVal): string =>
  v === null ? '—' : fmt(v);

interface Chart {
  resize(): void;
}

// ----------------------------------------------------------------------------
// Renderer entry
// ----------------------------------------------------------------------------

function render(payload: ReportPayload, mount: HTMLElement): void {
  injectStyle();
  mount.classList.add(ROOT_CLASS);
  mount.innerHTML = '';

  const root = el('div', 'abk-root');
  mount.appendChild(root);

  const charts: Chart[] = [];

  root.appendChild(buildHeader(payload));
  if (payload.warnings.length > 0) root.appendChild(buildWarnings(payload.warnings));
  root.appendChild(buildVerdicts(payload));
  const calibration = buildCalibrationSection(payload, charts);
  if (calibration) root.appendChild(calibration);
  for (const metric of payload.metrics) {
    root.appendChild(buildMetricSection(metric, payload, charts));
  }
  if (payload.metrics.length === 0) {
    root.appendChild(el('div', 'abk-empty', 'No comparisons configured for this experiment.'));
  }

  for (const c of charts) c.resize();
  let raf = 0;
  window.addEventListener('resize', () => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      for (const c of charts) c.resize();
    });
  });
}

// ----------------------------------------------------------------------------
// Header: title, meta, description, chips (SRM / calibration / look)
// ----------------------------------------------------------------------------

function humanCadence(seconds: number): string {
  if (seconds % 86400 === 0) return seconds / 86400 + 'd';
  if (seconds % 3600 === 0) return seconds / 3600 + 'h';
  if (seconds % 60 === 0) return seconds / 60 + 'min';
  return seconds + 's';
}

function buildHeader(payload: ReportPayload): HTMLElement {
  const h = el('div', 'abk-header');

  const title = payload.project
    ? `${esc(payload.project)} · ${esc(payload.experiment)}`
    : esc(payload.experiment);
  // period timestamps render in UTC (ms-epoch bake) — label them so a
  // non-UTC experiment tz shown next door can't read as an off-by-one date
  const period =
    payload.period.end > 0
      ? `${fmtDate(payload.period.start)} – ${fmtTs(payload.period.end)} UTC`
      : `${fmtDate(payload.period.start)} UTC – (no cutoffs yet)`;
  const meta =
    `${period} · horizon ${fmtDate(payload.period.horizon)} (UTC)` +
    ` · cadence ${humanCadence(payload.cadence_seconds)} · tz ${esc(payload.tz)}` +
    (payload.generated_at ? ` · generated ${esc(payload.generated_at)}` : '');

  const top = el('div', 'abk-h-top');
  const h1 = el('h1', 'abk-title');
  h1.innerHTML = title;
  top.appendChild(h1);
  top.appendChild(el('div', 'abk-meta', meta));
  h.appendChild(top);

  if (payload.description) h.appendChild(el('p', 'abk-desc', payload.description));

  h.appendChild(
    el('div', 'abk-arms', `arms: ${payload.arms.join(' vs ')} · first = control`),
  );

  const chips = el('div', 'abk-chips');
  chips.appendChild(buildSrmChip(payload));
  chips.appendChild(buildCalibrationChip(payload));
  if (payload.look !== null && payload.cadence_seconds < 86400) {
    chips.appendChild(
      el('span', 'abk-chip abk-look', `look ${payload.look.n} / ~${payload.look.planned} planned`),
    );
  }
  h.appendChild(chips);
  return h;
}

/** The red SRM gate chip (§6 must-fix) — window-independent experiment health. */
function buildSrmChip(payload: ReportPayload): HTMLElement {
  const srm = payload.srm;
  const total = payload.arms.reduce((acc, arm) => acc + (srm.observed[arm] || 0), 0);
  const chip = el('span', 'abk-chip abk-srm');
  if (srm.flag) {
    const obs = payload.arms
      .map((arm) => (total > 0 ? ((srm.observed[arm] || 0) / total).toFixed(2) : '0.00'))
      .join('/');
    const exp = payload.arms.map((arm) => (srm.expected[arm] ?? 0).toFixed(2)).join('/');
    const p = srm.pvalue === null ? 'p=—' : `p${srm.pvalue < 0.001 ? '<0.001' : '=' + fmtP(srm.pvalue)}`;
    chip.classList.add('abk-srm-fail');
    chip.textContent =
      `SRM FAILED (observed ${obs} vs expected ${exp}, χ² ${p}) — effects untrustworthy`;
    chip.setAttribute('data-abk-srm', 'fail');
  } else if (total === 0) {
    chip.textContent = 'SRM — no exposure data';
    chip.setAttribute('data-abk-srm', 'na');
  } else {
    chip.textContent = `SRM ok (p=${dash(srm.pvalue, fmtP)})`;
    chip.classList.add('abk-srm-ok');
    chip.setAttribute('data-abk-srm', 'ok');
  }
  return chip;
}

/** The calibration slot — M3 renders the empty state; tolerant of the M4 shape. */
function buildCalibrationChip(payload: ReportPayload): HTMLElement {
  const chip = el('span', 'abk-chip abk-calibration');
  const cal = payload.calibration;
  if (cal === null || cal === undefined) {
    chip.textContent = 'uncalibrated — run `abk validate` (M4)';
    chip.setAttribute('data-abk-calibration', 'empty');
    return chip;
  }
  // A non-null block with no measured FPR means every cell failed — the A/A matrix
  // ran but nothing is calibrated, so it must NOT read as the green "calibrated"
  // success state (m4 exit-gate review). Only a finite FPR earns the green chip.
  if (typeof cal.fpr !== 'number') {
    chip.textContent = cal.headline || 'A/A ran — no method measurable';
    chip.setAttribute('data-abk-calibration', 'failed');
    return chip;
  }
  const headline =
    (typeof cal.headline === 'string' && cal.headline) ||
    `calibrated / FPR=${(cal.fpr * 100).toFixed(1)}%`;
  chip.textContent = headline;
  chip.classList.add('abk-calibrated');
  chip.setAttribute('data-abk-calibration', 'present');
  return chip;
}

function buildWarnings(warnings: string[]): HTMLElement {
  const wrap = el('div', 'abk-warnings');
  for (const w of warnings) wrap.appendChild(el('div', 'abk-warning', `⚠ ${w}`));
  return wrap;
}

// ----------------------------------------------------------------------------
// A/A calibration matrix (M4) — the `abk validate` results, aa-fpr §4
// ----------------------------------------------------------------------------

const pct = (v: number | null | undefined): string =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(1)}%`;

const CAL_COLS: string[] = [
  'method',
  'FPR',
  'peeking FPR',
  'peeking (AV)',
  'power',
  'achieved MDE',
  'coverage',
  'exaggeration',
  'α',
  'verdict',
];

/** The A/A matrix section — one table per metric + the recommended cell's peeking
 * curve. Rendered only when `calibration.matrix_rows` is present (the M4 shape); the
 * M3 empty state stays a bare "uncalibrated" chip (buildCalibrationChip). */
function buildCalibrationSection(payload: ReportPayload, charts: Chart[]): HTMLElement | null {
  const cal = payload.calibration;
  const rows = cal?.matrix_rows;
  if (!cal || !rows || rows.length === 0) return null;

  const section = el('section', 'abk-calibration-matrix');
  const head = el('div', 'abk-cal-head');
  // this literal is the machine-checkable section title (the --report CLI test greps it)
  head.appendChild(el('h2', 'abk-cal-title', 'A/A false-positive matrix'));
  if (cal.headline) head.appendChild(el('div', 'abk-cal-headline', cal.headline));
  section.appendChild(head);

  // group rows by metric, preserving first-seen order (no Map iteration downlevel)
  const order: string[] = [];
  const byMetric: Record<string, CalibrationRow[]> = {};
  for (const r of rows) {
    const key = r.metric ?? '—';
    if (!byMetric[key]) {
      byMetric[key] = [];
      order.push(key);
    }
    byMetric[key].push(r);
  }
  for (const metric of order) {
    section.appendChild(buildCalibrationMetric(metric, byMetric[metric], charts));
  }
  return section;
}

function buildCalibrationMetric(
  metric: string,
  cells: CalibrationRow[],
  charts: Chart[],
): HTMLElement {
  const wrap = el('div', 'abk-cal-metric');
  wrap.appendChild(el('div', 'abk-cal-metric-name', metric));

  const table = el('table', 'abk-cal-table');
  const thead = el('tr');
  for (const label of CAL_COLS) thead.appendChild(el('th', undefined, label));
  table.appendChild(thead);

  for (const cell of cells) {
    const tr = el('tr');
    if (cell.recommended) tr.classList.add('abk-cal-rec');
    if (cell.status && cell.status !== 'success') tr.classList.add('abk-cal-failed');
    tr.setAttribute('data-abk-calibration-row', cell.recommended ? 'recommended' : 'cell');

    const methodTd = el('td');
    methodTd.appendChild(el('span', undefined, cell.method ?? '—'));
    if (cell.recommended) methodTd.appendChild(el('span', 'abk-cal-badge', 'Recommended'));
    if (cell.note) methodTd.appendChild(el('div', 'abk-cal-rationale', cell.note));
    tr.appendChild(methodTd);

    // FPR coloured against the budget band (green in-budget / red over)
    const fprCls = cell.over_budget
      ? 'abk-cal-fpr-over'
      : cell.fpr !== null && cell.fpr !== undefined
        ? 'abk-cal-fpr-ok'
        : undefined;
    const fprTd = el('td', fprCls, pct(cell.fpr));
    if (cell.budget !== null && cell.budget !== undefined) fprTd.title = `budget ${pct(cell.budget)}`;
    tr.appendChild(fprTd);

    tr.appendChild(el('td', undefined, pct(cell.peeking_fpr)));
    // M5 D8: the always-valid peeking twin — "—" when the method is ineligible; the
    // widening is disclosed on hover so the ~α recovery reads as principled, not free.
    const avTd = el('td', 'abk-cal-av', pct(cell.peeking_fpr_sequential));
    if (cell.ci_width != null && cell.ci_width_sequential != null) {
      avTd.title = `CI width ${fmtVal(cell.ci_width)} → ${fmtVal(cell.ci_width_sequential)} (always-valid)`;
    }
    tr.appendChild(avTd);
    tr.appendChild(el('td', undefined, pct(cell.power)));
    tr.appendChild(
      el('td', undefined, cell.achieved_mde == null ? '—' : fmtVal(cell.achieved_mde)),
    );
    tr.appendChild(el('td', undefined, pct(cell.coverage)));
    tr.appendChild(
      el('td', undefined, cell.effect_exaggeration == null ? '—' : fmtSigned(cell.effect_exaggeration)),
    );
    tr.appendChild(el('td', undefined, cell.alpha == null ? '—' : fmtP(cell.alpha)));

    const verdictTd = el('td', 'abk-cal-verdict', cell.verdict ?? '');
    if (cell.recommended && cell.rationale) {
      verdictTd.appendChild(el('div', 'abk-cal-rationale', `recommended — ${cell.rationale}`));
    }
    tr.appendChild(verdictTd);

    table.appendChild(tr);
  }
  wrap.appendChild(table);

  // the peeking-vs-looks curve for the recommended cell (else the first with a curve)
  const hasCurve = (c: CalibrationRow): boolean => !!c.peeking_curve && c.peeking_curve.length > 1;
  const curveCell = cells.find((c) => c.recommended && hasCurve(c)) ?? cells.find(hasCurve);
  if (curveCell && curveCell.peeking_curve) {
    const curve = curveCell.peeking_curve;
    const curveSeq =
      curveCell.peeking_curve_sequential && curveCell.peeking_curve_sequential.length > 1
        ? curveCell.peeking_curve_sequential
        : null;
    const alpha = curveCell.alpha ?? null;
    const legend = [
      { label: 'cumulative FPR', colorVar: '--abk-series-1' },
      { label: 'nominal α', colorVar: '--abk-st-warn' },
    ];
    if (curveSeq) legend.splice(1, 0, { label: 'always-valid', colorVar: '--abk-series-2' });
    wrap.appendChild(
      buildMiniPanel(
        `peeking FPR vs looks · ${curveCell.method ?? ''}`,
        legend,
        charts,
        (canvas, g) => drawPeekingCurve(canvas, g, curve, alpha, curveSeq),
        curveSeq
          ? 'optional-stopping hazard (blue) vs the always-valid CI (green), which holds the false-positive rate near α at every look'
          : 'optional-stopping hazard: cumulative false-positive rate as an analyst peeks at more looks',
      ),
    );
  }
  return wrap;
}

/** The cumulative peeking-FPR curve vs looks, with a dashed nominal-α reference. */
function drawPeekingCurve(
  canvas: HTMLCanvasElement,
  g: CanvasRenderingContext2D,
  curve: Array<[number, number]>,
  alpha: number | null,
  curveSeq: Array<[number, number]> | null = null,
): void {
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  if (canvas.width === 0 || canvas.height === 0) return;
  const xs = curve.map((p) => p[0]);
  const ys = curve.map((p) => p[1]);
  const finiteXs = xs.filter((x) => Number.isFinite(x));
  const xmin = 0;
  const xmax = Math.max(1, ...finiteXs);
  let hi = 0;
  for (const y of ys) if (Number.isFinite(y) && y > hi) hi = y;
  if (alpha !== null && Number.isFinite(alpha) && alpha > hi) hi = alpha;
  if (hi <= 0) hi = 1;
  const dom: Domain = { xmin, xmax, vmin: 0, vmax: hi * 1.15 };
  const sc = makeScales(canvas, MINI_MARGINS, dom, dpr);

  g.fillStyle = token('--abk-chart-bg');
  g.fillRect(0, 0, canvas.width, canvas.height);
  drawGridAndAxes(
    g, canvas, MINI_MARGINS, dom, sc.px, sc.py, xmin, xmax,
    token('--abk-chart-grid'), token('--abk-muted'), dpr, fmtEd,
  );

  const r = plotRect(canvas, MINI_MARGINS, dpr);
  g.save();
  g.beginPath();
  g.rect(r.left, r.top, sc.plotW(), sc.plotH());
  g.clip();
  if (alpha !== null && Number.isFinite(alpha)) {
    drawHLine(g, canvas, MINI_MARGINS, dpr, sc.py, alpha, rgba(token('--abk-st-warn'), 0.85), '', [4, 3]);
  }
  drawSeriesDecimated(
    g, xs, ys, xmin, xmax, r.left, sc.plotW(), sc.px, sc.py, token('--abk-series-1'), 1.75, dpr,
  );
  // M5 D8: overlay the always-valid curve — it holds near α while the fixed curve climbs.
  if (curveSeq) {
    const xsSeq = curveSeq.map((p) => p[0]);
    const ysSeq = curveSeq.map((p) => p[1]);
    drawSeriesDecimated(
      g, xsSeq, ysSeq, xmin, xmax, r.left, sc.plotW(), sc.px, sc.py, token('--abk-series-2'), 1.75, dpr,
    );
  }
  g.restore();
}

// ----------------------------------------------------------------------------
// Verdict banners
// ----------------------------------------------------------------------------

function buildVerdicts(payload: ReportPayload): HTMLElement {
  const wrap = el('div', 'abk-verdicts');
  if (payload.verdicts.length === 0) {
    wrap.appendChild(
      el(
        'div',
        'abk-empty',
        'No verdict yet — no main-metric results persisted. Run `abk run` first.',
      ),
    );
    return wrap;
  }
  for (const v of payload.verdicts) wrap.appendChild(buildVerdictCard(v));
  return wrap;
}

function buildVerdictCard(v: VerdictBlock): HTMLElement {
  const kind = v.verdict.toLowerCase();
  const card = el('div', `abk-verdict abk-verdict-${kind}`);
  card.setAttribute('data-abk-verdict', v.verdict);

  const head = el('div', 'abk-verdict-head');
  head.appendChild(el('span', 'abk-verdict-word', v.verdict));
  head.appendChild(el('span', 'abk-verdict-target', `${v.metric} — ${v.pair.c} vs ${v.pair.t}`));
  // §6.5 representativeness chip: an early decisive verdict covers < one weekly
  // cycle. Promoted from a caveat bullet to a glanceable chip (WP4); the full
  // "day-of-week" sentence stays in the tooltip + is filtered from the list below.
  if (v.weekly_cycle_pct != null) {
    const pct = Math.round(v.weekly_cycle_pct * 100);
    const chip = el('span', 'abk-chip abk-weekly-chip', `covers ${pct}% of a weekly cycle`);
    chip.setAttribute('data-abk-weekly', '1');
    chip.title = 'day-of-week effects may not be represented';
    head.appendChild(chip);
  }
  card.appendChild(head);

  const stats = el('div', 'abk-verdict-stats');
  const stat = (label: string, value: string): void => {
    const s = el('span', 'abk-stat');
    s.appendChild(el('span', 'abk-stat-l', label));
    s.appendChild(el('span', 'abk-stat-v', value));
    stats.appendChild(s);
  };
  stat('effect', v.effect === null ? '—' : fmtSigned(v.effect));
  stat('CI', v.lo === null || v.hi === null ? '—' : `[${fmtVal(v.lo)}, ${fmtVal(v.hi)}]`);
  stat('p', dash(v.pvalue, fmtP));
  stat('α', dash(v.alpha, (x) => String(x)));
  if (v.mde !== null) stat('MDE', fmtVal(v.mde));
  if (v.min_effect !== null) stat('min effect', fmtVal(v.min_effect));
  if (v.elapsed_days !== null) {
    stat('elapsed', `${fmtVal(v.elapsed_days)}d${v.is_horizon ? ' (at horizon)' : ' (pre-horizon)'}`);
  }
  card.appendChild(stats);

  if (v.rationale.length > 0) {
    const ul = el('ul', 'abk-rationale');
    for (const r of v.rationale) ul.appendChild(el('li', undefined, r));
    card.appendChild(ul);
  }
  // The weekly-cycle caveat is promoted to the chip above, so drop it here to
  // avoid saying the same thing twice; every other caveat still renders.
  const caveats =
    v.weekly_cycle_pct != null
      ? v.caveats.filter((c) => !c.includes('of a weekly cycle'))
      : v.caveats;
  if (caveats.length > 0) {
    const ul = el('ul', 'abk-caveats');
    for (const c of caveats) ul.appendChild(el('li', 'abk-caveat', `⚠ ${c}`));
    card.appendChild(ul);
  }
  if (v.guardrails.length > 0) {
    const ul = el('ul', 'abk-guardrails');
    for (const g of v.guardrails) {
      const li = el(
        'li',
        g.regressed ? 'abk-guardrail abk-guardrail-regressed' : 'abk-guardrail',
        `guardrail ${g.metric} (${g.pair.c} vs ${g.pair.t}): ` +
          (g.regressed
            ? `REGRESSED — effect ${g.effect === null ? '—' : fmtSigned(g.effect)}, desired ${g.desired_direction}`
            : 'ok'),
      );
      ul.appendChild(li);
    }
    card.appendChild(ul);
  }
  return card;
}

// ----------------------------------------------------------------------------
// Metric sections
// ----------------------------------------------------------------------------

function buildMetricSection(
  metric: MetricBlock,
  payload: ReportPayload,
  charts: Chart[],
): HTMLElement {
  const section = el('section', 'abk-metric');

  const head = el('div', 'abk-metric-head');
  const nameRow = el('div', 'abk-metric-name-row');
  nameRow.appendChild(el('h2', 'abk-metric-name', metric.name));
  if (metric.main) nameRow.appendChild(el('span', 'abk-badge abk-badge-main', 'main'));
  if (metric.guardrail) {
    nameRow.appendChild(el('span', 'abk-badge abk-badge-guardrail', 'guardrail'));
  }
  head.appendChild(nameRow);
  if (metric.description) head.appendChild(el('p', 'abk-metric-desc', metric.description));

  const method = metric.method;
  const paramsStr = JSON.stringify(method.params);
  const methodLine =
    `${method.name} · params ${paramsStr}` +
    (method.alpha !== null ? ` · α=${method.alpha}` : '') +
    ` · id ${method.id.slice(0, 12)}…`;
  head.appendChild(el('div', 'abk-method', methodLine));

  for (const w of metric.warnings) head.appendChild(el('div', 'abk-warning', `⚠ ${w}`));
  section.appendChild(head);

  const verdictFor = (pair: PairBlock): VerdictBlock | null => {
    for (const v of payload.verdicts) {
      if (v.metric === metric.name && v.pair.c === pair.c && v.pair.t === pair.t) return v;
    }
    return null;
  };

  for (const pair of metric.pairs) {
    section.appendChild(buildPairBlock(metric, pair, payload, verdictFor(pair), charts));
  }
  return section;
}

function buildPairBlock(
  metric: MetricBlock,
  pair: PairBlock,
  payload: ReportPayload,
  verdict: VerdictBlock | null,
  charts: Chart[],
): HTMLElement {
  const block = el('div', 'abk-pair');
  const multiPair = payload.arms.length > 2;
  if (multiPair) block.appendChild(el('h3', 'abk-pair-title', `${pair.c} vs ${pair.t}`));

  if (pair.series.length === 0) {
    block.appendChild(
      el('div', 'abk-empty', 'No persisted cutoffs for this pair yet — run `abk run` first.'),
    );
    return block;
  }

  const pts = [...pair.series].sort((a, b) => a.t - b.t);
  const horizonEd = (payload.period.horizon - payload.period.start) / MS_PER_DAY;

  // §4 notes with stable machine-checkable markers -------------------------
  const latest = pts[pts.length - 1];
  const insCount = pts.reduce((acc, p) => acc + p.ins, 0);
  if (latest.hz === 0) {
    block.setAttribute('data-abk-prehorizon', '1');
    block.appendChild(
      el(
        'div',
        'abk-note abk-prehorizon',
        `pre-horizon — fixed CIs are dashed/de-emphasized and not peeking-valid; ` +
          `planned horizon ${fmtDate(payload.period.horizon)} (${fmtVal(horizonEd)}d)`,
      ),
    );
  }
  if (insCount > 0) {
    block.appendChild(
      el(
        'div',
        'abk-note abk-insufficient',
        `${insCount} insufficient-data cutoff${insCount === 1 ? '' : 's'} greyed — ` +
          `counts + SRM only, no inference`,
      ),
    );
  }

  // main stabilization chart ------------------------------------------------
  const chartWrap = el('div', 'abk-chart abk-chart-main');
  const canvas = document.createElement('canvas');
  chartWrap.appendChild(canvas);
  block.appendChild(chartWrap);

  const readout = el('div', 'abk-readout', 'hover for a cutoff readout · scroll to zoom · drag to pan · double-click to reset');
  block.appendChild(readout);

  const chart = createStabilizationChart(canvas, pts, horizonEd, payload, (html) => {
    readout.innerHTML = html;
  });
  if (chart !== null) {
    charts.push(chart);
  } else {
    chartWrap.replaceChildren(el('div', 'abk-chart-fallback', 'chart unavailable (no canvas 2D context)'));
  }

  // small multiples (one axis each — never dual-axis) ------------------------
  const minis = el('div', 'abk-minis');
  minis.appendChild(
    buildMiniPanel(
      'variant means',
      [
        { label: pair.c, colorVar: '--abk-series-1' },
        { label: pair.t, colorVar: '--abk-series-2' },
      ],
      charts,
      (cv, g) =>
        drawMiniSeries(cv, g, pts, horizonEd, [
          { y: (p) => num(p.v1), colorVar: '--abk-series-1' },
          { y: (p) => num(p.v2), colorVar: '--abk-series-2' },
          { y: (p) => num(p.cv1), colorVar: '--abk-series-1', dash: [3, 3] },
          { y: (p) => num(p.cv2), colorVar: '--abk-series-2', dash: [3, 3] },
        ]),
      pts.some((p) => p.cv1 !== null || p.cv2 !== null)
        ? 'dashed = CUPED covariate mean'
        : undefined,
    ),
  );
  minis.appendChild(
    buildMiniPanel('pair MDE', [], charts, (cv, g) =>
      drawMiniSeries(
        cv,
        g,
        pts,
        horizonEd,
        [{ y: (p) => num(p.mde), colorVar: '--abk-series-1' }],
        {
          includeZero: true,
          refLine:
            verdict !== null && verdict.min_effect !== null
              ? { v: verdict.min_effect, label: 'min effect', colorVar: '--abk-st-warn' }
              : undefined,
        },
      ),
    ),
  );
  minis.appendChild(
    buildMiniPanel('p-value vs α', [], charts, (cv, g) =>
      drawMiniSeries(
        cv,
        g,
        pts,
        horizonEd,
        [{ y: (p) => num(p.p), colorVar: '--abk-series-1' }],
        {
          includeZero: true,
          refLine:
            metric.method.alpha !== null
              ? { v: metric.method.alpha, label: `α=${metric.method.alpha}`, colorVar: '--abk-st-critical' }
              : undefined,
        },
      ),
    ),
  );
  minis.appendChild(
    buildMiniPanel('avg group size', [], charts, (cv, g) =>
      drawMiniSeries(cv, g, pts, horizonEd, [
        { y: (p) => (p.s1 + p.s2) / 2, colorVar: '--abk-series-1' },
      ], { includeZero: true }),
    ),
  );
  block.appendChild(minis);

  block.appendChild(buildAuditTable(pts));
  return block;
}

// ----------------------------------------------------------------------------
// The stabilization chart (effect + CI band vs elapsed days)
// ----------------------------------------------------------------------------

function createStabilizationChart(
  canvas: HTMLCanvasElement,
  pts: SeriesPoint[],
  horizonEd: number,
  payload: ReportPayload,
  setReadout: (html: string) => void,
): Chart | null {
  const g = canvas.getContext('2d');
  if (!g) return null;

  const xs = pts.map((p) => (p.ed === null ? NaN : p.ed));
  const es = pts.map((p) => num(p.e));

  // §4: pre-horizon fixed CIs render dashed/de-emphasized; the band splits at
  // the first at-horizon point. The bridge point is PREPENDED to the post
  // band so the closing segment into the horizon renders SOLID — the planner
  // yields exactly one hz=1 cutoff (the last), and a post band of one point
  // would otherwise paint the decision-grade cutoff with the pre-horizon
  // de-emphasis (WP3 review finding). A stored hz=1 row counts only while it
  // still IS the current config horizon: after an end_date extension the old
  // horizon row goes stale mid-series (the planner never rewrites computed
  // rows) and everything must render pre-horizon again (milestone-review
  // finding).
  const isDecisionGrade = (p: SeriesPoint): boolean =>
    p.hz === 1 && p.t >= payload.period.horizon;
  const firstHz = pts.findIndex(isDecisionGrade);
  const bandPoint = (p: SeriesPoint, i: number): BandPoint => ({ x: xs[i], lo: p.lo, hi: p.hi });
  const preBand: BandPoint[] = [];
  const postBand: BandPoint[] = [];
  pts.forEach((p, i) => {
    if (Number.isNaN(xs[i])) return;
    if (firstHz === -1 || i < firstHz) preBand.push(bandPoint(p, i));
    else postBand.push(bandPoint(p, i));
  });
  if (firstHz !== -1 && preBand.length > 0 && postBand.length > 0) {
    postBand.unshift(preBand[preBand.length - 1]);
  }

  // greyed insufficient_data spans + subtle SRM-blocked tint -----------------
  const finiteXs = xs.filter((x) => !Number.isNaN(x));
  const gaps: number[] = [];
  for (let i = 1; i < finiteXs.length; i++) gaps.push(finiteXs[i] - finiteXs[i - 1]);
  gaps.sort((a, b) => a - b);
  const step = gaps.length > 0 ? gaps[Math.floor(gaps.length / 2)] : payload.cadence_seconds / 86400;
  const spansFor = (flag: (p: SeriesPoint) => boolean): Array<[number, number]> => {
    const spans: Array<[number, number]> = [];
    pts.forEach((p, i) => {
      if (!flag(p) || Number.isNaN(xs[i])) return;
      const a = xs[i] - step / 2;
      const b = xs[i] + step / 2;
      const last = spans[spans.length - 1];
      if (last && a <= last[1]) last[1] = b;
      else spans.push([a, b]);
    });
    return spans;
  };
  const insSpans = spansFor((p) => p.ins === 1);
  const blkSpans = spansFor((p) => p.blk === 1 && p.ins === 0);

  const storedHz = firstHz !== -1 && pts[firstHz].ed !== null ? pts[firstHz] : undefined;
  const dividerEd = storedHz !== undefined ? (storedHz.ed as number) : horizonEd;

  // domains -------------------------------------------------------------------
  const xmin = 0;
  const xmax = Math.max(dividerEd, horizonEd, ...finiteXs, 1);
  const fullSpan = xmax - xmin || 1;
  const minSpan = Math.min(Math.max(payload.cadence_seconds / 86400, fullSpan / 200), fullSpan);

  let vmin = 0;
  let vmax = 1;
  (function computeValueDomain(): void {
    let lo = Infinity;
    let hi = -Infinity;
    const fold = (v: number | null): void => {
      if (v !== null && Number.isFinite(v)) {
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    };
    for (const p of pts) {
      fold(p.e);
      fold(p.lo);
      fold(p.hi);
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
      lo = -1;
      hi = 1;
    }
    // effects read against zero: always fold 0 into the extent
    if (lo > 0) lo = 0;
    if (hi < 0) hi = 0;
    if (hi <= lo) hi = lo + 1;
    const pad = (hi - lo) * 0.06;
    vmin = lo - pad;
    vmax = hi + pad;
  })();

  let viewMin = xmin;
  let viewMax = xmax;
  let dpr = 1;
  let hoverX: number | null = null;

  const domain = (): Domain => ({ xmin: viewMin, xmax: viewMax, vmin, vmax });
  const scales = (): Scales => makeScales(canvas, MARGINS, domain(), dpr);

  function setView(a: number, b: number): void {
    let s = b - a;
    if (s < minSpan) {
      const m = (a + b) / 2;
      a = m - minSpan / 2;
      b = m + minSpan / 2;
      s = minSpan;
    }
    if (s >= fullSpan) {
      a = xmin;
      b = xmax;
    }
    if (a < xmin) {
      b += xmin - a;
      a = xmin;
    }
    if (b > xmax) {
      a -= b - xmax;
      b = xmax;
    }
    viewMin = clamp(a, xmin, xmax);
    viewMax = clamp(b, xmin, xmax);
    paint();
  }

  let raf = 0;
  function schedule(): void {
    if (raf === 0) raf = requestAnimationFrame(paint);
  }

  function paint(): void {
    raf = 0;
    if (canvas.width === 0 || canvas.height === 0) return;
    const sc = scales();
    const grid = token('--abk-chart-grid');
    const ink = token('--abk-chart-ink');
    const accent = token('--abk-series-1');

    g!.fillStyle = token('--abk-chart-bg');
    g!.fillRect(0, 0, canvas.width, canvas.height);

    drawGridAndAxes(g!, canvas, MARGINS, domain(), sc.px, sc.py, viewMin, viewMax, grid, token('--abk-muted'), dpr, fmtEd);

    // zero reference line — the winner chart reads against it (no gutter
    // label: it would collide with a near-zero gridline label)
    drawHLine(g!, canvas, MARGINS, dpr, sc.py, 0, rgba(ink, 0.6), '');

    g!.save();
    g!.beginPath();
    const r = plotRect(canvas, MARGINS, dpr);
    g!.rect(r.left, r.top, sc.plotW(), sc.plotH());
    g!.clip();

    // greyed insufficient_data spans (counts + SRM only) — under everything
    for (const [a, b] of insSpans) {
      if (b < viewMin || a > viewMax) continue;
      fillVSpan(g!, canvas, MARGINS, dpr, sc.px, a, b, token('--abk-muted'), 0.22);
    }
    // subtle tint over SRM-blocked cutoffs (the chip is the loud surface)
    for (const [a, b] of blkSpans) {
      if (b < viewMin || a > viewMax) continue;
      fillVSpan(g!, canvas, MARGINS, dpr, sc.px, a, b, token('--abk-st-critical'), 0.05);
    }

    // CI band: dashed/de-emphasized before the horizon, solid at/after (§4)
    fillBand(g!, preBand, scoredRuns(preBand), sc.px, sc.py, accent, 0.07, 0.28, dpr, [4, 4]);
    fillBand(g!, postBand, scoredRuns(postBand), sc.px, sc.py, accent, 0.13, 0.4, dpr);

    // the cumulative effect line (NaN gaps break the pen on demoted cutoffs)
    drawSeriesDecimated(g!, xs, es, viewMin, viewMax, r.left, sc.plotW(), sc.px, sc.py, accent, 2, dpr);

    // planned-horizon marker — anchored at the stored hz=1 cutoff (data
    // truth) when one exists, so an end_date edit after runs cannot make the
    // divider contradict the band split (WP3 review finding); the current-
    // config grid horizon is the fallback for a not-yet-reached horizon
    drawVDivider(g!, canvas, MARGINS, dpr, sc.px, dividerEd, grid, 'planned horizon →');

    if (hoverX !== null) drawHover(sc, r.top, sc.plotH());
    g!.restore();
  }

  function nearestIndex(x: number): number {
    let best = -1;
    let bestDist = Infinity;
    for (let i = 0; i < xs.length; i++) {
      if (Number.isNaN(xs[i])) continue;
      const d = Math.abs(xs[i] - x);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    }
    return best;
  }

  function drawHover(sc: Scales, top: number, h: number): void {
    const idx = nearestIndex(hoverX as number);
    if (idx < 0) return;
    const x = xs[idx];
    if (x < viewMin || x > viewMax) return;
    const X = sc.px(x);
    g!.strokeStyle = rgba(token('--abk-chart-grid'), 0.45);
    g!.lineWidth = 1 * dpr;
    g!.setLineDash([2 * dpr, 2 * dpr]);
    g!.beginPath();
    g!.moveTo(X, top);
    g!.lineTo(X, top + h);
    g!.stroke();
    g!.setLineDash([]);
    const e = es[idx];
    if (Number.isFinite(e)) {
      const Y = sc.py(e);
      g!.fillStyle = token('--abk-chart-bg');
      g!.beginPath();
      g!.arc(X, Y, 4 * dpr, 0, Math.PI * 2);
      g!.fill();
      g!.strokeStyle = token('--abk-series-1');
      g!.lineWidth = 2 * dpr;
      g!.beginPath();
      g!.arc(X, Y, 4 * dpr, 0, Math.PI * 2);
      g!.stroke();
    }
    updateReadout(idx);
  }

  function updateReadout(idx: number): void {
    const p = pts[idx];
    let html =
      `<span class="abk-ro-t">${esc(fmtTs(p.t))} (${p.ed === null ? '—' : fmtVal(p.ed) + 'd'})</span>`;
    if (p.ins === 1) {
      // §4: insufficient_data — counts + the cutoff's own as-of SRM state
      // (p.blk), not the window-independent current health the header chip
      // carries (WP3 review finding — the audit table uses p.blk too)
      html +=
        `<span class="abk-ro-flag abk-insufficient">insufficient data — ` +
        `n₁=${p.s1}, n₂=${p.s2} · SRM ${p.blk === 1 ? 'FAILED' : 'ok'}</span>`;
    } else {
      html += `<span>effect ${p.e === null ? '—' : esc(fmtSigned(p.e))}`;
      if (p.lo !== null && p.hi !== null) html += ` [${esc(fmtVal(p.lo))}, ${esc(fmtVal(p.hi))}]`;
      html += `</span>`;
      html += `<span>p ${p.p === null ? '—' : esc(fmtP(p.p))}</span>`;
      html += `<span>n₁=${p.s1} n₂=${p.s2}</span>`;
      if (p.mde !== null) html += `<span>MDE ${esc(fmtVal(p.mde))}</span>`;
      const flags: string[] = [];
      if (isDecisionGrade(p)) flags.push('at horizon');
      else flags.push('pre-horizon');
      if (p.blk === 1) flags.push('SRM-blocked');
      if (flags.length > 0) html += `<span class="abk-ro-flag">${esc(flags.join(' · '))}</span>`;
    }
    setReadout(html);
  }

  // interaction ---------------------------------------------------------------
  function xAtClientX(clientX: number): number {
    const r = canvas.getBoundingClientRect();
    const fr = (clientX - r.left - MARGINS.l) / (r.width - (MARGINS.l + MARGINS.r) || 1);
    return viewMin + clamp(fr, 0, 1) * (viewMax - viewMin);
  }

  canvas.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault();
      const x = xAtClientX(e.clientX);
      const cur = viewMax - viewMin;
      const s = clamp(cur * Math.pow(1.0015, e.deltaY), minSpan, fullSpan);
      const f = (x - viewMin) / (cur || 1);
      setView(x - f * s, x - f * s + s);
    },
    { passive: false },
  );

  let drag: { x: number; vMin: number; vMax: number } | null = null;
  canvas.addEventListener('mousedown', (e) => {
    drag = { x: e.clientX, vMin: viewMin, vMax: viewMax };
    canvas.style.cursor = 'grabbing';
  });
  window.addEventListener('mousemove', (e) => {
    if (!drag) return;
    const r = canvas.getBoundingClientRect();
    const perPx = (drag.vMax - drag.vMin) / (r.width - (MARGINS.l + MARGINS.r) || 1);
    const d = (e.clientX - drag.x) * perPx;
    setView(drag.vMin - d, drag.vMax - d);
  });
  window.addEventListener('mouseup', () => {
    if (drag) {
      drag = null;
      canvas.style.cursor = 'crosshair';
    }
  });
  canvas.addEventListener('mousemove', (e) => {
    if (drag) return;
    hoverX = xAtClientX(e.clientX);
    schedule();
  });
  canvas.addEventListener('mouseleave', () => {
    if (hoverX !== null) {
      hoverX = null;
      setReadout('hover for a cutoff readout · scroll to zoom · drag to pan · double-click to reset');
      schedule();
    }
  });
  canvas.addEventListener('dblclick', () => setView(xmin, xmax));
  canvas.style.cursor = 'crosshair';

  return {
    resize(): void {
      dpr = fit(canvas);
      paint();
    },
  };
}

// ----------------------------------------------------------------------------
// Small multiples
// ----------------------------------------------------------------------------

interface MiniSeriesSpec {
  y: (p: SeriesPoint) => number;
  colorVar: string;
  dash?: number[];
}

interface MiniOpts {
  includeZero?: boolean;
  refLine?: { v: number; label: string; colorVar: string };
}

function buildMiniPanel(
  title: string,
  legend: Array<{ label: string; colorVar: string }>,
  charts: Chart[],
  draw: (canvas: HTMLCanvasElement, g: CanvasRenderingContext2D) => void,
  note?: string,
): HTMLElement {
  const panel = el('div', 'abk-mini');
  const head = el('div', 'abk-mini-head');
  head.appendChild(el('span', 'abk-mini-title', title));
  for (const item of legend) {
    const li = el('span', 'abk-legend-item');
    const swatch = el('span', 'abk-swatch');
    swatch.style.background = token(item.colorVar);
    li.appendChild(swatch);
    li.appendChild(el('span', undefined, item.label));
    head.appendChild(li);
  }
  panel.appendChild(head);
  if (note) panel.appendChild(el('div', 'abk-mini-note', note));

  const wrap = el('div', 'abk-chart abk-chart-mini');
  const canvas = document.createElement('canvas');
  wrap.appendChild(canvas);
  panel.appendChild(wrap);

  const g = canvas.getContext('2d');
  if (!g) {
    wrap.replaceChildren(el('div', 'abk-chart-fallback', 'chart unavailable'));
    return panel;
  }
  charts.push({
    resize(): void {
      fit(canvas);
      draw(canvas, g);
    },
  });
  return panel;
}

const MINI_MARGINS: Margins = { l: 48, r: 10, t: 8, b: 22 };

function drawMiniSeries(
  canvas: HTMLCanvasElement,
  g: CanvasRenderingContext2D,
  pts: SeriesPoint[],
  horizonEd: number,
  series: MiniSeriesSpec[],
  opts: MiniOpts = {},
): void {
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  if (canvas.width === 0 || canvas.height === 0) return;

  const xs = pts.map((p) => (p.ed === null ? NaN : p.ed));
  const finiteXs = xs.filter((x) => !Number.isNaN(x));
  const xmin = 0;
  const xmax = Math.max(horizonEd, ...finiteXs, 1);

  let lo = Infinity;
  let hi = -Infinity;
  for (const s of series) {
    for (const p of pts) {
      const v = s.y(p);
      if (Number.isFinite(v)) {
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
  }
  if (opts.refLine) {
    lo = Math.min(lo, opts.refLine.v);
    hi = Math.max(hi, opts.refLine.v);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    lo = 0;
    hi = 1;
  }
  if (opts.includeZero) {
    if (lo > 0) lo = 0;
    if (hi < 0) hi = 0;
  }
  if (hi <= lo) hi = lo + 1;
  const pad = (hi - lo) * 0.08;
  const dom: Domain = { xmin, xmax, vmin: lo - pad, vmax: hi + pad };
  const sc = makeScales(canvas, MINI_MARGINS, dom, dpr);

  g.fillStyle = token('--abk-chart-bg');
  g.fillRect(0, 0, canvas.width, canvas.height);
  drawGridAndAxes(g, canvas, MINI_MARGINS, dom, sc.px, sc.py, xmin, xmax, token('--abk-chart-grid'), token('--abk-muted'), dpr, fmtEd);

  const r = plotRect(canvas, MINI_MARGINS, dpr);
  g.save();
  g.beginPath();
  g.rect(r.left, r.top, sc.plotW(), sc.plotH());
  g.clip();
  if (opts.refLine) {
    drawHLine(g, canvas, MINI_MARGINS, dpr, sc.py, opts.refLine.v, rgba(token(opts.refLine.colorVar), 0.85), '', [4, 3]);
  }
  for (const s of series) {
    const ys = pts.map((p) => s.y(p));
    drawSeriesDecimated(g, xs, ys, xmin, xmax, r.left, sc.plotW(), sc.px, sc.py, token(s.colorVar), 1.5, dpr, s.dash);
  }
  g.restore();

  // the ref-line label sits in the gutter, outside the clip
  if (opts.refLine && opts.refLine.label) {
    drawHLine(g, canvas, MINI_MARGINS, dpr, sc.py, opts.refLine.v, 'rgba(0,0,0,0)', '');
    g.fillStyle = rgba(token(opts.refLine.colorVar), 0.95);
    g.font = `${9 * dpr}px ui-monospace, Menlo, Consolas, monospace`;
    g.textAlign = 'left';
    g.textBaseline = 'bottom';
    const y = sc.py(opts.refLine.v);
    if (y > r.top && y < r.bottom) g.fillText(opts.refLine.label, r.left + 4 * dpr, y - 2 * dpr);
  }
}

// ----------------------------------------------------------------------------
// Audit table
// ----------------------------------------------------------------------------

function buildAuditTable(pts: SeriesPoint[]): HTMLElement {
  const details = document.createElement('details');
  details.className = 'abk-audit';
  const summary = el('summary', undefined, `results table (${pts.length} cutoffs)`);
  details.appendChild(summary);

  const scroll = el('div', 'abk-audit-scroll');
  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const h of ['cutoff (UTC)', 'd', 'effect', 'CI', 'p', 'reject', 'n₁', 'n₂', 'value₁', 'value₂', 'MDE', 'flags']) {
    headRow.appendChild(el('th', undefined, h));
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const p of pts) {
    const tr = document.createElement('tr');
    if (p.ins === 1) tr.className = 'abk-insufficient';
    const cells = [
      fmtTs(p.t),
      p.ed === null ? '—' : fmtVal(p.ed),
      p.e === null ? '—' : fmtSigned(p.e),
      p.lo === null || p.hi === null ? '—' : `[${fmtVal(p.lo)}, ${fmtVal(p.hi)}]`,
      dash(p.p, fmtP),
      p.rj === null ? '—' : p.rj === 1 ? '✓' : '✗',
      String(p.s1),
      String(p.s2),
      dash(p.v1),
      dash(p.v2),
      dash(p.mde),
      [p.hz === 1 ? 'horizon' : '', p.blk === 1 ? 'SRM' : '', p.ins === 1 ? 'insufficient' : '']
        .filter(Boolean)
        .join(' · ') || '—',
    ];
    for (const c of cells) tr.appendChild(el('td', undefined, c));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  scroll.appendChild(table);
  details.appendChild(scroll);
  return details;
}

// ----------------------------------------------------------------------------
// Styling (injected once, scoped under .abk-report)
// ----------------------------------------------------------------------------

let styleInjected = false;
function injectStyle(): void {
  if (styleInjected) return;
  styleInjected = true;
  // The token block is GENERATED from shared/chart.ts TOKEN_FALLBACKS — the
  // ONE brand-token layer (branding-and-site.md §3) — and declared on
  // :where(:root) (zero specificity), so canvas token() reads and DOM CSS
  // var() resolve through the SAME node and any host `:root{--abk-*:…}` rule
  // overrides both at once (WP3 review finding: split-brain theming).
  const tokenBlock = Object.entries(TOKEN_FALLBACKS)
    .map(([name, value]) => `${name}:${value}`)
    .join(';');
  const css = `
:where(:root){${tokenBlock};
  --abk-sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --abk-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.${ROOT_CLASS}{font-family:var(--abk-sans);color:var(--abk-ink);background:var(--abk-page);}
.${ROOT_CLASS} *{box-sizing:border-box;}
.${ROOT_CLASS} .abk-root{max-width:1100px;margin:0 auto;padding:20px 18px 48px;}
/* header ------------------------------------------------------------------ */
.${ROOT_CLASS} .abk-header{margin-bottom:16px;padding-left:12px;border-left:3px solid var(--abk-series-1);}
.${ROOT_CLASS} .abk-h-top{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;}
.${ROOT_CLASS} .abk-title{font-size:21px;font-weight:700;margin:0;letter-spacing:-0.01em;}
.${ROOT_CLASS} .abk-meta{font-size:12px;color:var(--abk-ink-2);font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-desc{margin:8px 0 0;font-size:13px;color:var(--abk-ink-2);max-width:760px;line-height:1.5;}
.${ROOT_CLASS} .abk-arms{margin-top:6px;font-size:12px;color:var(--abk-ink-2);font-family:var(--abk-mono);}
/* chips -------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;}
.${ROOT_CLASS} .abk-chip{display:inline-flex;align-items:center;gap:7px;padding:5px 11px;
  background:var(--abk-card);border:1px solid var(--abk-border);border-radius:10px;
  font-size:12px;font-family:var(--abk-mono);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-srm-ok{border-color:var(--abk-st-good);}
.${ROOT_CLASS} .abk-srm-fail{background:var(--abk-st-critical);border-color:var(--abk-st-critical);
  color:var(--abk-card);font-weight:700;}
.${ROOT_CLASS} .abk-calibration{border-style:dashed;}
.${ROOT_CLASS} .abk-calibrated{border-style:solid;border-color:var(--abk-st-good);}
.${ROOT_CLASS} .abk-weekly-chip{padding:2px 9px;font-size:11px;align-self:center;
  border-color:var(--abk-st-warn);color:var(--abk-ink);
  background:color-mix(in srgb, var(--abk-st-warn) 12%, transparent);}
/* warnings / notes ---------------------------------------------------------- */
.${ROOT_CLASS} .abk-warnings{margin:10px 0;}
.${ROOT_CLASS} .abk-warning{font-size:12px;color:var(--abk-ink);
  background:color-mix(in srgb, var(--abk-st-warn) 14%, transparent);
  border:1px solid var(--abk-st-warn);border-radius:8px;padding:6px 10px;margin:4px 0;}
.${ROOT_CLASS} .abk-note{font-size:12px;font-family:var(--abk-mono);border-radius:8px;
  padding:5px 10px;margin:6px 0;border:1px dashed var(--abk-border);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-note.abk-insufficient{background:color-mix(in srgb, var(--abk-muted) 12%, transparent);}
/* verdicts ------------------------------------------------------------------ */
.${ROOT_CLASS} .abk-verdicts{display:flex;flex-direction:column;gap:10px;margin:14px 0 22px;}
.${ROOT_CLASS} .abk-verdict{background:var(--abk-card);border:1px solid var(--abk-border);
  border-left-width:4px;border-radius:10px;padding:12px 14px;}
.${ROOT_CLASS} .abk-verdict-win{border-left-color:var(--abk-st-good);}
.${ROOT_CLASS} .abk-verdict-lose{border-left-color:var(--abk-st-critical);}
.${ROOT_CLASS} .abk-verdict-flat{border-left-color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-verdict-inconclusive{border-left-color:var(--abk-st-warn);}
.${ROOT_CLASS} .abk-verdict-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;}
.${ROOT_CLASS} .abk-verdict-word{font-size:17px;font-weight:800;letter-spacing:0.02em;}
.${ROOT_CLASS} .abk-verdict-win .abk-verdict-word{color:var(--abk-good-text);}
.${ROOT_CLASS} .abk-verdict-lose .abk-verdict-word{color:var(--abk-st-critical);}
.${ROOT_CLASS} .abk-verdict-flat .abk-verdict-word{color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-verdict-inconclusive .abk-verdict-word{color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-verdict-target{font-size:13px;color:var(--abk-ink-2);font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-verdict-stats{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:8px;}
.${ROOT_CLASS} .abk-stat{display:inline-flex;gap:6px;font-size:12px;font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-stat-l{color:var(--abk-muted);}
.${ROOT_CLASS} .abk-stat-v{color:var(--abk-ink);font-weight:600;}
.${ROOT_CLASS} .abk-rationale{margin:8px 0 0;padding-left:20px;font-size:13px;color:var(--abk-ink-2);line-height:1.5;}
.${ROOT_CLASS} .abk-caveats{margin:8px 0 0;padding-left:20px;list-style:none;}
.${ROOT_CLASS} .abk-caveat{font-size:12.5px;color:var(--abk-ink);
  background:color-mix(in srgb, var(--abk-st-warn) 14%, transparent);
  border-radius:6px;padding:4px 8px;margin:3px 0;}
.${ROOT_CLASS} .abk-guardrails{margin:8px 0 0;padding-left:20px;font-size:12.5px;color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-guardrail-regressed{color:var(--abk-st-critical);font-weight:600;}
/* A/A calibration matrix (M4) ------------------------------------------------ */
.${ROOT_CLASS} .abk-calibration-matrix{margin:18px 0 26px;}
.${ROOT_CLASS} .abk-cal-head{margin-bottom:10px;}
.${ROOT_CLASS} .abk-cal-title{font-size:16px;font-weight:700;margin:0;}
.${ROOT_CLASS} .abk-cal-headline{margin-top:4px;font-size:12.5px;font-family:var(--abk-mono);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-cal-metric{margin:14px 0;}
.${ROOT_CLASS} .abk-cal-metric-name{font-size:13px;font-weight:600;margin:0 0 6px;color:var(--abk-ink);}
.${ROOT_CLASS} .abk-cal-table{width:100%;border-collapse:collapse;font-size:12px;font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-cal-table th,.${ROOT_CLASS} .abk-cal-table td{border:1px solid var(--abk-border);
  padding:5px 8px;text-align:left;vertical-align:top;}
.${ROOT_CLASS} .abk-cal-table th{background:var(--abk-card);color:var(--abk-ink-2);font-weight:600;white-space:nowrap;}
.${ROOT_CLASS} .abk-cal-rec{background:color-mix(in srgb, var(--abk-st-good) 8%, transparent);}
.${ROOT_CLASS} .abk-cal-rec td{font-weight:600;}
.${ROOT_CLASS} .abk-cal-fpr-over{color:var(--abk-st-critical);font-weight:700;}
.${ROOT_CLASS} .abk-cal-fpr-ok{color:var(--abk-good-text);}
.${ROOT_CLASS} .abk-cal-failed td{color:var(--abk-muted);font-style:italic;}
.${ROOT_CLASS} .abk-cal-badge{display:inline-block;font-size:10px;padding:1px 6px;border-radius:6px;
  background:var(--abk-st-good);color:var(--abk-card);margin-left:6px;font-weight:700;letter-spacing:0.02em;}
.${ROOT_CLASS} .abk-cal-verdict{max-width:360px;white-space:normal;}
.${ROOT_CLASS} .abk-cal-rationale{font-size:11px;color:var(--abk-ink-2);margin-top:2px;font-style:normal;}
/* metric sections ------------------------------------------------------------ */
.${ROOT_CLASS} .abk-metric{margin:26px 0;}
.${ROOT_CLASS} .abk-metric-name-row{display:flex;align-items:baseline;gap:10px;}
.${ROOT_CLASS} .abk-metric-name{font-size:16px;font-weight:700;margin:0;}
.${ROOT_CLASS} .abk-badge{font-size:10px;font-family:var(--abk-mono);text-transform:uppercase;
  letter-spacing:0.06em;padding:2px 7px;border-radius:8px;border:1px solid var(--abk-border);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-badge-main{border-color:var(--abk-series-1);color:var(--abk-series-1);}
.${ROOT_CLASS} .abk-badge-guardrail{border-color:var(--abk-st-serious);color:var(--abk-st-serious);}
.${ROOT_CLASS} .abk-metric-desc{margin:4px 0 0;font-size:12.5px;color:var(--abk-ink-2);max-width:760px;}
.${ROOT_CLASS} .abk-method{margin-top:4px;font-size:11.5px;color:var(--abk-muted);font-family:var(--abk-mono);
  overflow-wrap:anywhere;}
.${ROOT_CLASS} .abk-pair{margin:12px 0 20px;}
.${ROOT_CLASS} .abk-pair-title{font-size:13px;font-weight:600;margin:10px 0 6px;color:var(--abk-ink-2);
  font-family:var(--abk-mono);}
/* charts ---------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-chart{position:relative;width:100%;background:var(--abk-chart-bg);
  border:1px solid var(--abk-chart-border);border-radius:12px;overflow:hidden;}
.${ROOT_CLASS} .abk-chart canvas{width:100%;height:100%;display:block;}
.${ROOT_CLASS} .abk-chart-main{height:340px;margin-top:8px;}
.${ROOT_CLASS} .abk-chart-mini{height:150px;}
.${ROOT_CLASS} .abk-chart-fallback{color:var(--abk-chart-ink);font-size:12px;padding:20px;
  font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-readout{min-height:20px;margin-top:6px;font-size:11px;color:var(--abk-ink-2);
  font-family:var(--abk-mono);display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center;}
.${ROOT_CLASS} .abk-ro-t{font-weight:700;color:var(--abk-ink);}
.${ROOT_CLASS} .abk-ro-flag{color:var(--abk-muted);}
.${ROOT_CLASS} .abk-readout .abk-insufficient{color:var(--abk-ink);
  background:color-mix(in srgb, var(--abk-muted) 16%, transparent);
  border-radius:6px;padding:2px 6px;}
/* small multiples -------------------------------------------------------------- */
.${ROOT_CLASS} .abk-minis{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
  gap:10px;margin-top:10px;}
.${ROOT_CLASS} .abk-mini-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin-bottom:4px;}
.${ROOT_CLASS} .abk-mini-title{font-size:11px;font-weight:600;color:var(--abk-muted);
  font-family:var(--abk-mono);letter-spacing:0.06em;}
.${ROOT_CLASS} .abk-legend-item{display:inline-flex;align-items:center;gap:5px;font-size:11px;
  color:var(--abk-ink-2);font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-swatch{width:10px;height:10px;border-radius:2px;display:inline-block;}
.${ROOT_CLASS} .abk-mini-note{font-size:10.5px;color:var(--abk-muted);font-family:var(--abk-mono);
  margin-bottom:4px;}
/* audit table -------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-audit{margin-top:10px;}
.${ROOT_CLASS} .abk-audit summary{font-size:12px;color:var(--abk-ink-2);cursor:pointer;
  font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-audit-scroll{overflow-x:auto;margin-top:8px;}
.${ROOT_CLASS} .abk-audit table{border-collapse:collapse;font-size:11.5px;font-family:var(--abk-mono);
  min-width:720px;font-variant-numeric:tabular-nums;}
.${ROOT_CLASS} .abk-audit th{text-align:left;color:var(--abk-muted);font-weight:600;
  border-bottom:1px solid var(--abk-border);padding:4px 10px 4px 0;white-space:nowrap;}
.${ROOT_CLASS} .abk-audit td{border-bottom:1px solid var(--abk-border);padding:4px 10px 4px 0;
  white-space:nowrap;color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-audit tr.abk-insufficient td{color:var(--abk-muted);
  background:color-mix(in srgb, var(--abk-muted) 8%, transparent);}
/* empty states --------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-empty{font-size:13px;color:var(--abk-ink-2);background:var(--abk-card);
  border:1px dashed var(--abk-border);border-radius:10px;padding:14px;}
`;
  const style = document.createElement('style');
  style.setAttribute('data-abk-report', '');
  style.textContent = css;
  document.head.appendChild(style);
}

// ----------------------------------------------------------------------------
// Global entry (the only public surface — no ESM exports)
// ----------------------------------------------------------------------------

(window as unknown as { __ABK_REPORT__: { render: typeof render } }).__ABK_REPORT__ = { render };
