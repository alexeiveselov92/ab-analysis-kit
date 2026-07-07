// The abkit "signature chart" (docs/design/brand-tokens.md §"The signature
// chart") for the landing demo: the cumulative effect + its confidence interval,
// one point per day, watched CONVERGING past a decision horizon.
//
// This is a thin FRAME that composes the shared, framework-free renderer core —
// `web/src/shared/chart.ts`, the `abkit-web` workspace package (the same canvas
// primitives the report/explore bundles use). We do NOT re-port the donor's
// canvas: scales, the translucent CI band, the decimated series line, and the
// dashed zero reference all come from the shared module. Only the brand chrome
// specific to THIS light-paper view (the warm grid, the win-dashed horizon, the
// end-point dot + halo) is drawn here.
//
// Token mapping (brand-tokens.md §"The signature chart"), read live off :root
// from website/src/styles/brand.css (the single source of real values):
//   effect line = --iris (3px, round caps/joins) + end dot --iris r5, halo @18%
//   CI band     = --iris fill @ 14% (the tightening IS the point — always drawn)
//   zero line   = --zero-line, dashed 4 4
//   horizon     = --win, dashed 3 4, labelled "horizon"
//   grid/labels = --border / --grid gridlines, --subtle axis labels

import {
  type BandPoint,
  type Domain,
  type Margins,
  type Scales,
  drawHLine,
  drawSeriesDecimated,
  fillBand,
  fit,
  makeScales,
  plotRect,
  rgba,
  scoredRuns,
  fmtEd,
  fmtSigned,
} from '../../../../web/src/shared/chart';
import type { CumulativePoint, RunConfig } from './types';

const MARGINS: Margins = { l: 56, r: 20, t: 16, b: 28 };
const MONO = "'JetBrains Mono', ui-monospace, Menlo, Consolas, monospace";

// The signature-chart tokens (brand-tokens.md). Read from :root at runtime; the
// fallbacks mirror the single source (brand-tokens.md / brand.css) so the chart
// is correct even if the stylesheet has not applied yet.
const TOKENS: Record<string, string> = {
  '--iris': '#6a45c4',
  '--zero-line': '#b7af9e',
  '--win': '#1e9e6a',
  '--lose': '#d6453d',
  '--flat': '#7a8595',
  '--inconclusive': '#e0a23b',
  '--border': '#e6e0d4',
  '--grid': '#eee9de',
  '--subtle': '#9a9384',
};

/** Read a brand CSS custom property off :root, falling back to the brand value. */
function t(name: string): string {
  if (typeof document !== 'undefined') {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    if (v) return v;
  }
  return TOKENS[name] ?? '#888';
}

/** One frame's worth of state. */
export interface RenderState {
  /** the revealed prefix of the series (what is drawn / read out) */
  points: CumulativePoint[];
  config: RunConfig;
  /**
   * The point set the y/x domain is fitted to. During an animated reveal pass
   * this is the FULL series, so the frame stays fixed and the band visibly
   * shrinks inside it. Defaults to `points`.
   */
  domainPoints?: CumulativePoint[];
}

export interface DemoChart {
  render(state: RenderState): void;
  resize(): void;
}

/** Build the domain from a point set: x over the day range, v over CI ∪ effect ∪ 0. */
function domainOf(pts: CumulativePoint[]): Domain {
  let vmin = 0;
  let vmax = 0; // always include the zero reference line
  for (const p of pts) {
    if (p.lo != null && Number.isFinite(p.lo)) vmin = Math.min(vmin, p.lo);
    if (p.hi != null && Number.isFinite(p.hi)) vmax = Math.max(vmax, p.hi);
    if (Number.isFinite(p.effect)) {
      vmin = Math.min(vmin, p.effect);
      vmax = Math.max(vmax, p.effect);
    }
  }
  const padV = (vmax - vmin) * 0.1 || 1;
  const xmin = pts[0].ed;
  const xmax = pts[pts.length - 1].ed;
  const padX = (xmax - xmin) * 0.02 || 0.5;
  return { xmin: xmin - padX, xmax: xmax + padX, vmin: vmin - padV, vmax: vmax + padV };
}

/** The win-dashed decision-horizon divider + label (brand: --win, 1.5px, dash 3 4). */
function drawHorizon(
  g: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  dpr: number,
  px: (x: number) => number,
  atX: number,
): void {
  const r = plotRect(canvas, MARGINS, dpr);
  const x = px(atX);
  if (x < r.left - 1 || x > r.right + 1) return;
  const color = t('--win');
  g.save();
  g.strokeStyle = color;
  g.lineWidth = 1.5 * dpr;
  g.setLineDash([3 * dpr, 4 * dpr]);
  g.beginPath();
  g.moveTo(x, r.top);
  g.lineTo(x, r.bottom);
  g.stroke();
  g.setLineDash([]);
  g.fillStyle = color;
  g.font = `${10 * dpr}px ${MONO}`;
  const label = 'horizon';
  const fitsRight = x + 6 * dpr + g.measureText(label).width <= r.right;
  g.textAlign = fitsRight ? 'left' : 'right';
  g.textBaseline = 'top';
  g.fillText(label, fitsRight ? x + 6 * dpr : x - 6 * dpr, r.top + 5 * dpr);
  g.restore();
}

/** Warm value gridlines + labels and the elapsed-day x-axis (light-paper brand). */
function drawGrid(
  g: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  dom: Domain,
  sc: Scales,
  dpr: number,
): void {
  const r = plotRect(canvas, MARGINS, dpr);
  g.font = `${11 * dpr}px ${MONO}`;
  // horizontal value gridlines + signed value labels in the left gutter
  g.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const v = dom.vmin + ((dom.vmax - dom.vmin) * i) / 4;
    const yy = sc.py(v);
    g.strokeStyle = t('--grid');
    g.lineWidth = 1 * dpr;
    g.beginPath();
    g.moveTo(r.left, yy);
    g.lineTo(r.right, yy);
    g.stroke();
    g.fillStyle = t('--subtle');
    g.textAlign = 'right';
    g.fillText(fmtSigned(v), (MARGINS.l - 8) * dpr, yy);
  }
  // bottom elapsed-day ticks
  g.textBaseline = 'top';
  const span = dom.xmax - dom.xmin || 1;
  for (let i = 0; i <= 5; i++) {
    const x = dom.xmin + (span * i) / 5;
    g.fillStyle = t('--subtle');
    g.textAlign = i === 0 ? 'left' : i === 5 ? 'right' : 'center';
    g.fillText(fmtEd(x, span), sc.px(x), canvas.height - (MARGINS.b - 7) * dpr);
  }
}

export function createDemoChart(canvas: HTMLCanvasElement): DemoChart {
  let last: RenderState | null = null;

  function paint(state: RenderState): void {
    last = state;
    const { points } = state;
    const domainPts = state.domainPoints ?? points;
    const g = canvas.getContext('2d');
    if (!g || domainPts.length === 0) return;

    const dpr = fit(canvas);
    g.clearRect(0, 0, canvas.width, canvas.height);
    if (points.length === 0) return;

    const dom = domainOf(domainPts);
    const sc = makeScales(canvas, MARGINS, dom, dpr);
    const r = plotRect(canvas, MARGINS, dpr);

    // 1) grid + axes (warm, behind everything)
    drawGrid(g, canvas, dom, sc, dpr);

    // 2) the CI band — --iris @ 14%, drawn over contiguous scored runs
    const iris = t('--iris');
    const band: BandPoint[] = points.map((p) => ({ x: p.ed, lo: p.lo, hi: p.hi }));
    fillBand(g, band, scoredRuns(band), sc.px, sc.py, iris, 0.14, 0.32, dpr);

    // 3) the zero / no-effect reference — --zero-line, dashed 4 4
    drawHLine(g, canvas, MARGINS, dpr, sc.py, 0, t('--zero-line'), '0', [4, 4]);

    // 4) the decision horizon — --win, dashed 3 4
    drawHorizon(g, canvas, dpr, sc.px, state.config.horizonDay);

    // 5) the cumulative-effect line — --iris, 3px, round caps/joins
    const xs = points.map((p) => p.ed);
    const vs = points.map((p) => p.effect);
    g.lineCap = 'round';
    drawSeriesDecimated(g, xs, vs, dom.xmin, dom.xmax, r.left, r.right - r.left, sc.px, sc.py, iris, 3, dpr);

    // 6) the end-point dot + halo (the "current call")
    const lastPt = points[points.length - 1];
    if (lastPt && Number.isFinite(lastPt.effect)) {
      const X = sc.px(lastPt.ed);
      const Y = sc.py(lastPt.effect);
      g.fillStyle = rgba(iris, 0.18);
      g.beginPath();
      g.arc(X, Y, 11 * dpr, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = iris;
      g.beginPath();
      g.arc(X, Y, 5 * dpr, 0, Math.PI * 2);
      g.fill();
    }
  }

  return {
    render: paint,
    resize: () => {
      if (last) paint(last);
    },
  };
}
