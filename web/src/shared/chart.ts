// Framework-free HTML5 Canvas 2D primitives, shared by the report renderer
// (src/report/report.ts) and the explore cockpit (M3 WP7).
//
// Ported from the detectkit donor's core/canvas.ts (behaviour-identical where
// kept): a brand-token reader, hex → rgb(a) parsing, DPR-aware canvas fitting,
// a scales factory (px/py over a domain + margins), a min/max-decimated series
// line (NaN breaks the pen), a translucent confidence band over contiguous
// scored runs (edge dash added for the pre-horizon treatment), gridlines +
// axis ticks with a pluggable x-tick formatter (abkit's x-axis is
// elapsed_days, not wall time), vertical dividers/spans, and formatters.
//
// Nothing here is report-specific; both renderers compose these into their
// own frames.

export interface Margins {
  l: number;
  r: number;
  t: number;
  b: number;
}

// ----------------------------------------------------------------------------
// Brand tokens — THE placeholder palette (branding-and-site.md §3)
// ----------------------------------------------------------------------------

// One brand-token layer: every color any abkit surface uses lives here as a
// semantic CSS custom property with a placeholder fallback. The finalized
// abkit palette (branding-and-site.md — decided in Claude design) drops in by
// editing THIS table (and the mirrored CSS block in each renderer's
// injectStyle) — no other code names a color. Placeholder values are the
// validated dataviz reference palette (CVD-checked against the surfaces they
// render on); status roles never impersonate series hues and always ship with
// a text label.
export const TOKEN_FALLBACKS: Record<string, string> = {
  // page + cards (light)
  '--abk-page': '#f9f9f7',
  '--abk-card': '#fcfcfb',
  '--abk-ink': '#0b0b0b',
  '--abk-ink-2': '#52514e',
  '--abk-muted': '#898781',
  '--abk-border': '#e1e0d9',
  // the dark chart panel
  '--abk-chart-bg': '#1a1a19',
  '--abk-chart-border': '#2c2c2a',
  '--abk-chart-ink': '#c3c2b7',
  '--abk-chart-grid': '#898781',
  // series (validated for the dark chart surface; slot order is fixed)
  '--abk-series-1': '#3987e5',
  '--abk-series-2': '#199e70',
  // status roles (fixed; icon/word always accompanies the color)
  '--abk-st-good': '#0ca30c',
  '--abk-st-warn': '#fab219',
  '--abk-st-serious': '#ec835a',
  '--abk-st-critical': '#d03b3b',
  // success-colored TEXT on the light surface needs the darker step
  '--abk-good-text': '#006300',
};

/** Read a brand CSS custom property off :root, falling back to the placeholder. */
export function token(name: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || TOKEN_FALLBACKS[name] || '#888';
}

// Parse "#rgb" / "#rrggbb" into [r,g,b] for translucent fills.
export function rgb(hex: string): [number, number, number] {
  let h = hex.replace('#', '').trim();
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  const n = parseInt(h, 16);
  if (h.length !== 6 || Number.isNaN(n)) return [137, 135, 129]; // --abk-muted
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rgba(hex: string, a: number): string {
  const [r, g, b] = rgb(hex);
  return `rgba(${r},${g},${b},${a})`;
}

// ----------------------------------------------------------------------------
// Sizing
// ----------------------------------------------------------------------------

/**
 * DPR-aware backing-store fit. Sizes the canvas' pixel buffer to its CSS box ×
 * devicePixelRatio. Returns the dpr used so the caller can scale line widths /
 * fonts.
 */
export function fit(canvas: HTMLCanvasElement): number {
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  const w = canvas.clientWidth || canvas.offsetWidth || 0;
  const h = canvas.clientHeight || canvas.offsetHeight || 0;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  return dpr;
}

// ----------------------------------------------------------------------------
// Scales
// ----------------------------------------------------------------------------

/** x is the generic horizontal domain — elapsed days for abkit charts. */
export interface Domain {
  xmin: number;
  xmax: number;
  vmin: number;
  vmax: number;
}

export interface Scales {
  /** x-domain → device-px X */
  px(x: number): number;
  /** value-axis → device-px Y */
  py(v: number): number;
  /** inverse of px: device-px X → x-domain */
  xAt(devX: number): number;
  /** device-px width of the plot rect */
  plotW(): number;
  /** device-px height of the plot rect */
  plotH(): number;
}

/**
 * Build the px/py mapping for a domain + margins on a canvas at a given dpr.
 * Device-px space, origin top-left, y inverted (donor math).
 */
export function makeScales(
  canvas: HTMLCanvasElement,
  m: Margins,
  dom: Domain,
  dpr: number,
): Scales {
  const plotW = (): number => canvas.width - (m.l + m.r) * dpr;
  const plotH = (): number => canvas.height - (m.t + m.b) * dpr;
  const xspan = (): number => dom.xmax - dom.xmin || 1;
  const vspan = (): number => dom.vmax - dom.vmin || 1;
  const px = (x: number): number => m.l * dpr + ((x - dom.xmin) / xspan()) * plotW();
  const py = (v: number): number => canvas.height - m.b * dpr - ((v - dom.vmin) / vspan()) * plotH();
  const xAt = (devX: number): number => dom.xmin + ((devX - m.l * dpr) / (plotW() || 1)) * xspan();
  return { px, py, xAt, plotW, plotH };
}

/** Device-px bounds of the plot area for a canvas + margins. */
export interface PlotRect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export function plotRect(canvas: HTMLCanvasElement, m: Margins, dpr: number): PlotRect {
  return {
    left: m.l * dpr,
    top: m.t * dpr,
    right: canvas.width - m.r * dpr,
    bottom: canvas.height - m.b * dpr,
  };
}

// ----------------------------------------------------------------------------
// Series line (min/max decimation)
// ----------------------------------------------------------------------------

const isFiniteNum = Number.isFinite;

/**
 * Draw a value series as a min/max-decimated envelope (one column per device
 * pixel) so a long sub-day-cadence series stays fast and spikes stay visible.
 * When few points are visible (zoomed in) it falls back to a direct polyline.
 * A non-finite value (NaN gap — a demoted cutoff) breaks the pen.
 *
 * `lo`/`hi` bound the x range that should be drawn (the current view); points
 * outside it are skipped. `left`/`width` are device-px geometry of the plot rect.
 */
export function drawSeriesDecimated(
  g: CanvasRenderingContext2D,
  xs: ArrayLike<number>,
  values: ArrayLike<number>,
  lo: number,
  hi: number,
  left: number,
  width: number,
  px: (x: number) => number,
  py: (v: number) => number,
  color: string,
  lw: number,
  dpr: number,
  dash?: number[],
): void {
  const n = xs.length;
  const cols = Math.max(1, Math.round(width));
  const sp = hi - lo || 1;

  let vis = 0;
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (!isFiniteNum(v) || xs[i] < lo || xs[i] > hi) continue;
    vis++;
  }

  g.strokeStyle = color;
  g.lineWidth = lw * dpr;
  g.lineJoin = 'round';
  if (dash) g.setLineDash(dash.map((d) => d * dpr));
  g.beginPath();

  // A lone point in a subpath strokes nothing under canvas semantics, so
  // isolated points — a day-1 report, or an informative cutoff between
  // demoted neighbors — are collected and drawn as dots in their own path
  // after the polyline stroke (WP3 review finding).
  const isolated: Array<[number, number]> = [];

  if (vis <= cols) {
    // Direct polyline; NaN / out-of-range breaks the pen.
    const visible = (i: number): boolean =>
      i >= 0 && i < n && isFiniteNum(values[i]) && xs[i] >= lo && xs[i] <= hi;
    let pen = false;
    for (let i = 0; i < n; i++) {
      if (!visible(i)) {
        pen = false;
        continue;
      }
      const X = px(xs[i]);
      const Y = py(values[i]);
      if (!visible(i - 1) && !visible(i + 1)) {
        isolated.push([X, Y]);
        pen = false;
        continue;
      }
      if (!pen) {
        g.moveTo(X, Y);
        pen = true;
      } else {
        g.lineTo(X, Y);
      }
    }
  } else {
    // One envelope column per pixel: track per-column min/max, draw high→low.
    const cmin = new Array<number | null>(cols).fill(null);
    const cmax = new Array<number | null>(cols).fill(null);
    for (let i = 0; i < n; i++) {
      const v = values[i];
      const x = xs[i];
      if (!isFiniteNum(v) || x < lo || x > hi) continue;
      let col = Math.floor(((x - lo) / sp) * (cols - 1));
      col = col < 0 ? 0 : col > cols - 1 ? cols - 1 : col;
      if (cmin[col] === null || v < (cmin[col] as number)) cmin[col] = v;
      if (cmax[col] === null || v > (cmax[col] as number)) cmax[col] = v;
    }
    let pen = false;
    for (let col = 0; col < cols; col++) {
      if (cmax[col] === null) {
        pen = false;
        continue;
      }
      const X = left + col;
      const yh = py(cmax[col] as number);
      const yl = py(cmin[col] as number);
      if (!pen) {
        g.moveTo(X, yh);
        pen = true;
      } else {
        g.lineTo(X, yh);
      }
      g.lineTo(X, yl);
    }
  }
  g.stroke();
  if (dash) g.setLineDash([]);
  if (isolated.length > 0) {
    g.fillStyle = color;
    g.beginPath();
    for (const [X, Y] of isolated) {
      g.moveTo(X + Math.max(1.5, lw) * dpr, Y);
      g.arc(X, Y, Math.max(1.5, lw) * dpr, 0, Math.PI * 2);
    }
    g.fill();
  }
}

// ----------------------------------------------------------------------------
// Confidence band
// ----------------------------------------------------------------------------

/** One scored point of a band: x + lower/upper bound (null = un-scored). */
export interface BandPoint {
  x: number;
  lo: number | null;
  hi: number | null;
}

/**
 * Contiguous runs of points with finite band bounds, as [start, end] inclusive
 * index pairs. A demoted-cutoff / NaN-band gap breaks a run so the corridor
 * polygon never bridges an un-scored region.
 */
export function scoredRuns(pts: ArrayLike<BandPoint>): Array<[number, number]> {
  const runs: Array<[number, number]> = [];
  let start = -1;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    const ok = p.lo !== null && p.hi !== null && isFiniteNum(p.lo) && isFiniteNum(p.hi);
    if (ok) {
      if (start === -1) start = i;
    } else if (start !== -1) {
      runs.push([start, i - 1]);
      start = -1;
    }
  }
  if (start !== -1) runs.push([start, pts.length - 1]);
  return runs;
}

/**
 * Fill a translucent corridor between lower/upper over each contiguous scored
 * run, then stroke faint top/bottom edges. `hexColor` is the band's accent;
 * the fill uses `fillAlpha` and the edges `edgeAlpha`. `edgeDash` (CSS-px)
 * renders the edges dashed — the §4 "not peeking-valid" de-emphasis for
 * pre-horizon fixed CIs.
 */
export function fillBand(
  g: CanvasRenderingContext2D,
  pts: ArrayLike<BandPoint>,
  runs: Array<[number, number]>,
  px: (x: number) => number,
  py: (v: number) => number,
  hexColor: string,
  fillAlpha: number,
  edgeAlpha: number,
  dpr: number,
  edgeDash?: number[],
): void {
  g.fillStyle = rgba(hexColor, fillAlpha);
  for (const [a, b] of runs) {
    if (a === b) continue; // single-point runs render as whiskers below
    g.beginPath();
    g.moveTo(px(pts[a].x), py(pts[a].hi as number));
    for (let i = a + 1; i <= b; i++) g.lineTo(px(pts[i].x), py(pts[i].hi as number));
    for (let i = b; i >= a; i--) g.lineTo(px(pts[i].x), py(pts[i].lo as number));
    g.closePath();
    g.fill();
  }
  g.strokeStyle = rgba(hexColor, edgeAlpha);
  g.lineWidth = 1 * dpr;
  if (edgeDash) g.setLineDash(edgeDash.map((d) => d * dpr));
  for (const [a, b] of runs) {
    if (a === b) {
      // a lone scored point (day-1 report, isolated cutoff between demoted
      // neighbors) fills a zero-area polygon — draw a capped vertical
      // whisker between lo and hi instead (WP3 review finding)
      const X = px(pts[a].x);
      const yHi = py(pts[a].hi as number);
      const yLo = py(pts[a].lo as number);
      const cap = 3 * dpr;
      g.beginPath();
      g.moveTo(X, yHi);
      g.lineTo(X, yLo);
      g.moveTo(X - cap, yHi);
      g.lineTo(X + cap, yHi);
      g.moveTo(X - cap, yLo);
      g.lineTo(X + cap, yLo);
      g.stroke();
      continue;
    }
    for (const bound of ['hi', 'lo'] as const) {
      g.beginPath();
      for (let i = a; i <= b; i++) {
        const X = px(pts[i].x);
        const Y = py(pts[i][bound] as number);
        if (i === a) g.moveTo(X, Y);
        else g.lineTo(X, Y);
      }
      g.stroke();
    }
  }
  if (edgeDash) g.setLineDash([]);
}

// ----------------------------------------------------------------------------
// Gridlines + axis ticks
// ----------------------------------------------------------------------------

/**
 * Paint horizontal value gridlines + right-aligned value labels in the left
 * gutter, and bottom-axis x ticks across the view. `tickLo`/`tickHi` bound the
 * visible x range so labels track zoom/pan; `fmtX` formats an x tick (abkit
 * charts pass an elapsed-days formatter).
 */
export function drawGridAndAxes(
  g: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  m: Margins,
  dom: Domain,
  px: (x: number) => number,
  py: (v: number) => number,
  tickLo: number,
  tickHi: number,
  faintHex: string,
  mutedHex: string,
  dpr: number,
  fmtX: (x: number, span: number) => string,
): void {
  g.font = `${11 * dpr}px ui-monospace, Menlo, Consolas, monospace`;
  g.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const v = dom.vmin + ((dom.vmax - dom.vmin) * i) / 4;
    const yy = py(v);
    g.strokeStyle = rgba(faintHex, 0.1);
    g.lineWidth = 1 * dpr;
    g.beginPath();
    g.moveTo(m.l * dpr, yy);
    g.lineTo(canvas.width - m.r * dpr, yy);
    g.stroke();
    g.fillStyle = mutedHex;
    g.textAlign = 'right';
    g.fillText(fmtVal(v), (m.l - 8) * dpr, yy);
  }
  g.textBaseline = 'top';
  const span = tickHi - tickLo || 1;
  for (let i = 0; i <= 5; i++) {
    const x = tickLo + (span * i) / 5;
    const xx = px(x);
    g.fillStyle = mutedHex;
    g.textAlign = i === 0 ? 'left' : i === 5 ? 'right' : 'center';
    // canvas.height is already device-px (fit() multiplied by dpr) — only
    // the margin offset scales by dpr. The donor multiplied the whole
    // expression, throwing HiDPI x labels off-canvas (WP3 review finding).
    g.fillText(fmtX(x, span), xx, canvas.height - (m.b - 7) * dpr);
  }
}

// ----------------------------------------------------------------------------
// Reference lines, dividers, spans
// ----------------------------------------------------------------------------

/** A horizontal reference line (zero line, alpha line) with a gutter label. */
export function drawHLine(
  g: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  m: Margins,
  dpr: number,
  py: (v: number) => number,
  v: number,
  color: string,
  label: string,
  dash?: number[],
): void {
  const y = py(v);
  const r = plotRect(canvas, m, dpr);
  if (y < r.top - 0.5 || y > r.bottom + 0.5) return;
  g.strokeStyle = color;
  g.lineWidth = 1.25 * dpr;
  if (dash) g.setLineDash(dash.map((d) => d * dpr));
  g.beginPath();
  g.moveTo(r.left, y);
  g.lineTo(r.right, y);
  g.stroke();
  if (dash) g.setLineDash([]);
  if (label) {
    g.fillStyle = color;
    g.textAlign = 'right';
    g.textBaseline = 'middle';
    g.font = `${10 * dpr}px ui-monospace, Menlo, Consolas, monospace`;
    g.fillText(label, (m.l - 8) * dpr, y);
  }
}

/**
 * A dashed vertical divider + a small label — the planned-horizon marker
 * (the donor's warm-up divider without the dim: pre-horizon data is real,
 * just not decision-grade; the band's dashed de-emphasis carries that).
 */
export function drawVDivider(
  g: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  m: Margins,
  dpr: number,
  px: (x: number) => number,
  atX: number,
  color: string,
  label: string,
): void {
  const r = plotRect(canvas, m, dpr);
  const x = px(atX);
  if (x < r.left - 1 || x > r.right + 1) return;
  g.save();
  g.strokeStyle = rgba(color, 0.7);
  g.lineWidth = 1 * dpr;
  g.setLineDash([4 * dpr, 4 * dpr]);
  g.beginPath();
  g.moveTo(x, r.top);
  g.lineTo(x, r.bottom);
  g.stroke();
  g.setLineDash([]);
  g.fillStyle = rgba(color, 0.95);
  g.font = `${10 * dpr}px ui-monospace, Menlo, Consolas, monospace`;
  const fitsRight = x + 6 * dpr + g.measureText(label).width <= r.right;
  g.textAlign = fitsRight ? 'left' : 'right';
  g.textBaseline = 'top';
  g.fillText(label, fitsRight ? x + 6 * dpr : x - 6 * dpr, r.top + 5 * dpr);
  g.restore();
}

/**
 * A translucent vertical span [x0, x1] — the greyed insufficient_data /
 * SRM-blocked region treatment (counts + SRM only; no inference shown).
 */
export function fillVSpan(
  g: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  m: Margins,
  dpr: number,
  px: (x: number) => number,
  x0: number,
  x1: number,
  color: string,
  alpha: number,
): void {
  const r = plotRect(canvas, m, dpr);
  const a = Math.max(r.left, Math.min(px(x0), r.right));
  const b = Math.max(r.left, Math.min(px(x1), r.right));
  if (b <= a && b < r.left) return;
  g.fillStyle = rgba(color, alpha);
  g.fillRect(a, r.top, Math.max(b - a, 1 * dpr), r.bottom - r.top);
}

// ----------------------------------------------------------------------------
// Formatters
// ----------------------------------------------------------------------------

/** Compact value formatter: more decimals as the magnitude shrinks. */
export function fmtVal(v: number): string {
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  if (a >= 0.001 || a === 0) return v.toFixed(3);
  return v.toExponential(1);
}

/** Signed effect formatter (an effect always reads against zero). */
export function fmtSigned(v: number): string {
  return (v > 0 ? '+' : '') + fmtVal(v);
}

/** p-value: three decimals, honest floor below 0.001. */
export function fmtP(p: number): string {
  if (p < 0.001) return '<0.001';
  return p.toFixed(3);
}

/** Elapsed-days axis tick: "3d" / "3.5d"; hours below one day. */
export function fmtEd(ed: number, span: number): string {
  if (span <= 2) {
    const h = Math.round(ed * 24);
    return `${h}h`;
  }
  const r = Math.round(ed * 10) / 10;
  return `${Number.isInteger(r) ? r.toFixed(0) : r.toFixed(1)}d`;
}

/** Full timestamp "YYYY-MM-DD HH:MM" (UTC) from ms-epoch. */
export function fmtTs(ts: number): string {
  return new Date(ts).toISOString().slice(0, 16).replace('T', ' ');
}

/** Date only "YYYY-MM-DD" (UTC) from ms-epoch. */
export function fmtDate(ts: number): string {
  return new Date(ts).toISOString().slice(0, 10);
}
