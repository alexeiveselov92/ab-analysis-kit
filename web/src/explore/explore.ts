// abkit explore cockpit — the live method-tuning windshield (M3 WP7).
//
// Consumes the ExplorePayload contract (./payload.ts, lockstep with
// abkit/tuning/payload.py) baked by abkit/tuning/html.py, and drives the
// WP6 localhost server: every knob change POSTs the full knob state to
// /recompute (debounced 130 ms, expensive path on `change`, echo on `input`)
// and adopts the reply — stabilization chart with D1-tier-styled segments
// (solid exact / hatched "approx (α-only)" / the persisted baseline line
// always visible), pinned windshield chips (lift, CI half-width, p, power,
// the D3 calibration chip, the SRM gate, the look counter), the Basic /
// Advanced side rail auto-derived from param_specs (D12), Tier-R knobs
// routed through a confirm → /reload, and the Apply flow with the
// uncalibrated-cost confirm (server-mirrored gate).
//
// The donor's worker terminate-respawn discipline (detectkit tune.ts
// 1185-1252) is re-expressed over HTTP: a monotonic request_id seeded from
// Date.now() (ids are a single global on the server — a reloaded page must
// outrank its predecessor), AbortController kills the in-flight fetch on a
// new knob change (never queues behind it), replies are adopted iff their
// captured id is still current, and ONLY the current request's completion
// clears the in-flight flag — a stale/aborted reply leaves the spinner alone
// because the replacement compute is live.
//
// Peeking honesty (data-contract-and-reporting.md §4) carries the same
// stable machine-checkable markers as the report renderer:
//   .abk-prehorizon   — pre-horizon fixed CIs dashed/de-emphasized
//   .abk-insufficient — insufficient_data cutoffs greyed, counts + SRM only
//   .abk-srm-fail     — the red SRM gate chip
//
// Bundled (esbuild → IIFE) to abkit/tuning/assets/explore.js, which assigns
// `window.__ABK_EXPLORE__ = { render }`. Nothing is exported for ESM — the
// global is the public surface (AbkExploreGlobal). Styling is injected once,
// scoped under the .abk-explore root class; every color resolves through the
// one brand-token layer (shared/chart.ts TOKEN_FALLBACKS).

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
import type { MetricBlock, PairBlock, ReportPayload, SeriesPoint } from '../shared/payload';
import { makeBrandLockup } from '../shared/logo';
import type {
  ApplyComparison,
  ApplyReply,
  ApplyRequest,
  CalibrationStatus,
  ExplorePayload,
  KnobSpec,
  MethodSurface,
  MetricSurface,
  RecomputeReply,
  ReplyPair,
  ReplyPoint,
  ValidateReply,
} from './payload';

// ----------------------------------------------------------------------------
// Constants + tiny helpers
// ----------------------------------------------------------------------------

const ROOT_CLASS = 'abk-explore';
const MARGINS: Margins = { l: 56, r: 16, t: 14, b: 28 };
const MS_PER_DAY = 86400000;
const DEBOUNCE_MS = 130;

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

function humanCadence(seconds: number): string {
  if (seconds % 86400 === 0) return seconds / 86400 + 'd';
  if (seconds % 3600 === 0) return seconds / 3600 + 'h';
  if (seconds % 60 === 0) return seconds / 60 + 'min';
  return seconds + 's';
}

/** Alpha needs more precision than fmtVal (two-tier splits like 0.016667). */
const fmtAlpha = (a: number): string => Number(a.toPrecision(4)).toString();

// ----------------------------------------------------------------------------
// The client half of the alpha resolution — a mirror of
// abkit/pipeline/analyze.effective_alphas + abkit/stats/correction.two_tier_alphas
// (declarative-config §6): /recompute takes the EFFECTIVE post-correction
// per-comparison alpha, so the raw alpha/correction knobs resolve here.
// Guardrails count as tests; `correction: none` (and read-time BH) collapse
// both tiers to the raw alpha.
// ----------------------------------------------------------------------------

function effectiveAlpha(
  rawAlpha: number,
  correction: string,
  groupsCount: number,
  nonMainCount: number,
  isMain: boolean,
): number {
  if (correction !== 'bonferroni') return rawAlpha;
  const pairs = (groupsCount * (groupsCount - 1)) / 2;
  if (pairs <= 0) return rawAlpha;
  if (isMain || nonMainCount === 0) return rawAlpha / pairs;
  return rawAlpha / (pairs * nonMainCount);
}

// ----------------------------------------------------------------------------
// Control factories (the donor rail conventions: `set()` never fires
// onChange — the caller drives ONE recompute after a programmatic re-seed;
// sliders echo on `input`, fire on `change`)
// ----------------------------------------------------------------------------

interface CtlBadge {
  cls: string;
  text: string;
  hint: string;
}

function ctlLabel(text: string, hint?: string, badges?: CtlBadge[]): HTMLElement {
  const l = el('div', 'abk-ctl-label');
  const name = el('span', undefined, text);
  if (hint) {
    name.title = hint;
    name.appendChild(el('span', 'abk-hint', ' ⓘ'));
  }
  l.appendChild(name);
  for (const b of badges || []) {
    const badge = el('span', `abk-knob-badge ${b.cls}`, b.text);
    badge.title = b.hint;
    l.appendChild(badge);
  }
  return l;
}

interface SegSpec {
  label: string;
  value: string;
}

interface Ctl<T> {
  row: HTMLElement;
  get(): T;
  set(v: T): void;
}

function segControl(
  label: string,
  options: SegSpec[],
  initial: string,
  onChange: (v: string) => void,
  hint?: string,
  badges?: CtlBadge[],
): Ctl<string> {
  const row = el('div', 'abk-ctl');
  row.appendChild(ctlLabel(label, hint, badges));
  const seg = el('div', 'abk-seg');
  let current = initial;
  const btns: HTMLButtonElement[] = [];
  const paint = (): void => {
    for (const b of btns) b.classList.toggle('on', b.dataset.v === current);
  };
  for (const opt of options) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'abk-seg-btn';
    b.textContent = opt.label;
    b.dataset.v = opt.value;
    b.onclick = (): void => {
      if (current === opt.value) return;
      current = opt.value;
      paint();
      onChange(current);
    };
    btns.push(b);
    seg.appendChild(b);
  }
  paint();
  row.appendChild(seg);
  return {
    row,
    get: () => current,
    set: (v: string): void => {
      current = v;
      paint();
    },
  };
}

interface RangeOpts {
  min: number;
  max: number;
  step: number;
  value: number;
  fmt?: (v: number) => string;
  hint?: string;
  badges?: CtlBadge[];
}

interface RangeCtl extends Ctl<number> {
  setMax(m: number): void;
}

function rangeControl(label: string, opts: RangeOpts, onChange: (v: number) => void): RangeCtl {
  const fmt = opts.fmt || ((v: number): string => String(v));
  const row = el('div', 'abk-ctl');
  const head = el('div', 'abk-ctl-head');
  head.appendChild(ctlLabel(label, opts.hint, opts.badges));
  const echo = el('span', 'abk-ctl-val', fmt(opts.value));
  head.appendChild(echo);
  row.appendChild(head);
  const input = document.createElement('input');
  input.type = 'range';
  input.className = 'abk-range';
  input.min = String(opts.min);
  input.max = String(opts.max);
  input.step = String(opts.step);
  input.value = String(opts.value);
  // echo on every drag frame; the expensive recompute only on release
  // (keyboard arrows fire both — donor tune.ts 242-252)
  input.oninput = (): void => {
    echo.textContent = fmt(Number(input.value));
  };
  input.onchange = (): void => {
    echo.textContent = fmt(Number(input.value));
    onChange(Number(input.value));
  };
  row.appendChild(input);
  return {
    row,
    get: () => Number(input.value),
    set: (v: number): void => {
      input.value = String(v);
      echo.textContent = fmt(v);
    },
    setMax: (m: number): void => {
      input.max = String(m);
      if (Number(input.value) > m) {
        input.value = String(m);
        echo.textContent = fmt(m);
      }
    },
  };
}

interface NumberOpts {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
  badges?: CtlBadge[];
}

function numberControl(
  label: string,
  opts: NumberOpts,
  onChange: (v: number) => void,
): Ctl<number> {
  const row = el('div', 'abk-ctl');
  row.appendChild(ctlLabel(label, opts.hint, opts.badges));
  const input = document.createElement('input');
  input.type = 'number';
  input.className = 'abk-num';
  if (opts.min !== undefined) input.min = String(opts.min);
  if (opts.max !== undefined) input.max = String(opts.max);
  input.step = opts.step !== undefined ? String(opts.step) : 'any';
  input.value = String(opts.value);
  let lastValid = opts.value;
  input.onchange = (): void => {
    const v = Number(input.value);
    const bad =
      input.value.trim() === '' ||
      !Number.isFinite(v) ||
      (opts.min !== undefined && v < opts.min) ||
      (opts.max !== undefined && v > opts.max);
    if (bad) {
      input.value = String(lastValid); // revert; never send garbage
      return;
    }
    lastValid = v;
    onChange(v);
  };
  row.appendChild(input);
  return {
    row,
    get: () => lastValid,
    set: (v: number): void => {
      lastValid = v;
      input.value = String(v);
    },
  };
}

function textControl(
  label: string,
  opts: { value: string; placeholder?: string; hint?: string; badges?: CtlBadge[] },
  onChange: (v: string) => void,
): Ctl<string> {
  const row = el('div', 'abk-ctl');
  row.appendChild(ctlLabel(label, opts.hint, opts.badges));
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'abk-text';
  if (opts.placeholder) input.placeholder = opts.placeholder;
  input.value = opts.value;
  input.onchange = (): void => onChange(input.value.trim());
  row.appendChild(input);
  return {
    row,
    get: () => input.value.trim(),
    set: (v: string): void => {
      input.value = v;
    },
  };
}

function checkControl(
  label: string,
  initial: boolean,
  onChange: (v: boolean) => void,
  hint?: string,
  badges?: CtlBadge[],
): Ctl<boolean> {
  const row = el('div', 'abk-ctl abk-ctl-check');
  const lab = document.createElement('label');
  lab.className = 'abk-check';
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = initial;
  input.onchange = (): void => onChange(input.checked);
  lab.appendChild(input);
  const name = el('span', undefined, label);
  if (hint) {
    name.title = hint;
    name.appendChild(el('span', 'abk-hint', ' ⓘ'));
  }
  lab.appendChild(name);
  for (const b of badges || []) {
    const badge = el('span', `abk-knob-badge ${b.cls}`, b.text);
    badge.title = b.hint;
    lab.appendChild(badge);
  }
  row.appendChild(lab);
  return {
    row,
    get: () => input.checked,
    set: (v: boolean): void => {
      input.checked = v;
    },
  };
}

// ----------------------------------------------------------------------------
// Knob-spec → control mapping (D12: the rail is auto-derived from param_specs;
// a knob without a spec cannot appear). Identity-EXCLUDED specs (seed,
// max_block_bytes) never get a control: seed is stripped server-side and
// derived per row; neither changes results.
// ----------------------------------------------------------------------------

interface ParamCtl {
  spec: KnobSpec;
  row: HTMLElement;
  /** typed current value, or undefined when the knob should be OMITTED from
   * params (empty text, unset optional) */
  get(): unknown;
  set(v: unknown): void;
}

function specBadges(spec: KnobSpec, tier: string | undefined): CtlBadge[] {
  const badges: CtlBadge[] = [];
  if (spec.identity) {
    badges.push({
      cls: 'abk-badge-identity',
      text: '⚠ series',
      hint:
        'identity-bearing: a different value is a different method_config_id — ' +
        'Apply starts a NEW results series and orphans the persisted one',
    });
  }
  if (tier === 'R') {
    badges.push({
      cls: 'abk-badge-reload',
      text: '↻ reload',
      hint:
        'Tier R: changing this re-renders the cached cutoffs from the warehouse ' +
        '(a confirm step, then POST /reload)',
    });
  }
  return badges;
}

function buildParamControl(
  spec: KnobSpec,
  tier: string | undefined,
  seedValue: unknown,
  onChange: () => void,
): ParamCtl {
  const badges = specBadges(spec, tier);
  const hint = spec.description;

  if (spec.choices !== null) {
    const options = spec.choices.map((c) => ({ label: c, value: c }));
    const initial = seedValue !== undefined && seedValue !== null ? String(seedValue) : String(spec.default ?? '');
    const ctl = segControl(spec.name, options, initial, () => onChange(), hint, badges);
    return {
      spec,
      row: ctl.row,
      get: () => ctl.get(),
      set: (v: unknown): void => ctl.set(v === null || v === undefined ? String(spec.default ?? '') : String(v)),
    };
  }

  if (spec.type === 'bool') {
    const initial = seedValue !== undefined && seedValue !== null ? Boolean(seedValue) : Boolean(spec.default);
    const ctl = checkControl(spec.name, initial, () => onChange(), hint, badges);
    return {
      spec,
      row: ctl.row,
      get: () => ctl.get(),
      set: (v: unknown): void => ctl.set(v === null || v === undefined ? Boolean(spec.default) : Boolean(v)),
    };
  }

  if (spec.type === 'int') {
    // Slider identity hazard (donor tune.ts 1411-1416): the range must
    // include the seeded value exactly and step=1 keeps it addressable — a
    // snapped value would silently mint a new method_config_id on Apply.
    const seed = typeof seedValue === 'number' ? Math.round(seedValue) : Number(spec.default ?? 1);
    const lo = Math.min(spec.minimum !== null ? spec.minimum : 1, seed);
    const hi =
      spec.maximum !== null
        ? Math.max(spec.maximum, seed)
        : Math.max(seed * 4, Number(spec.default ?? 0) * 4, 4000);
    const ctl = rangeControl(
      spec.name,
      { min: lo, max: hi, step: 1, value: seed, hint, badges },
      () => onChange(),
    );
    return {
      spec,
      row: ctl.row,
      get: () => ctl.get(),
      set: (v: unknown): void => {
        const n = typeof v === 'number' ? Math.round(v) : Number(spec.default ?? lo);
        ctl.setMax(Math.max(hi, n)); // raise the max FIRST so set() can't clamp
        ctl.set(n);
      },
    };
  }

  if (spec.type === 'float') {
    // exact typed values — number inputs dodge the float-step snap hazard
    const seed = typeof seedValue === 'number' ? seedValue : Number(spec.default ?? 0);
    const pad = spec.exclusive_bounds ? 1e-9 : 0;
    const ctl = numberControl(
      spec.name,
      {
        value: seed,
        min: spec.minimum !== null ? spec.minimum + pad : undefined,
        max: spec.maximum !== null ? spec.maximum - pad : undefined,
        hint,
        badges,
      },
      () => onChange(),
    );
    return {
      spec,
      row: ctl.row,
      get: () => ctl.get(),
      set: (v: unknown): void => ctl.set(typeof v === 'number' ? v : Number(spec.default ?? 0)),
    };
  }

  // str, and str|int grammars (covariate_lookback '14d'); empty = unset
  const initial = seedValue === undefined || seedValue === null ? '' : String(seedValue);
  const ctl = textControl(
    spec.name,
    {
      value: initial,
      placeholder: spec.default === null ? 'unset' : String(spec.default),
      hint,
      badges,
    },
    () => onChange(),
  );
  return {
    spec,
    row: ctl.row,
    get: (): unknown => {
      const v = ctl.get();
      if (v === '') return undefined;
      if (spec.type.includes('int') && /^-?\d+$/.test(v)) return parseInt(v, 10);
      return v;
    },
    set: (v: unknown): void => ctl.set(v === null || v === undefined ? '' : String(v)),
  };
}

// ----------------------------------------------------------------------------
// Renderer entry
// ----------------------------------------------------------------------------

interface KnobValues {
  method: string;
  /** FULL params — every rendered knob's current value (unset optionals
   * omitted). Storing minimal params here would make "edited back to the
   * spec default" indistinguishable from "never touched", so a rail rebuild
   * would silently resurrect the configured non-default value
   * (milestone-review finding). Wire bodies are minimalized at send time —
   * identity hashes only non-default identity params, so the YAML stays
   * minimal (the donor discipline) with the same method_config_id. */
  params: Record<string, unknown>;
}

interface RoleState {
  main: boolean;
  guardrail: boolean;
}

let teardown: (() => void) | null = null;

function render(payload: ExplorePayload, mount: HTMLElement): void {
  injectStyle();
  if (teardown) teardown(); // idempotent re-render: drop prior window listeners
  const disposers: Array<() => void> = [];
  teardown = (): void => {
    for (const d of disposers) d();
    disposers.length = 0;
  };
  mount.classList.add(ROOT_CLASS);
  mount.innerHTML = '';

  const root = el('div', 'abk-root');
  mount.appendChild(root);

  const live = payload.recompute_url !== null;
  const canApply = payload.save_url !== null;
  const surfaces = payload.explore.metrics;
  const expKnobs = payload.explore.experiment;

  // metrics in report (config) order, restricted to explorable surfaces
  const metricBlocks = new Map<string, MetricBlock>();
  for (const m of payload.metrics) if (!metricBlocks.has(m.name)) metricBlocks.set(m.name, m);
  const metricNames = [...metricBlocks.keys()].filter((n) => n in surfaces);

  // ---- state ----------------------------------------------------------------
  let activeMetric: string | null =
    payload.explore.default_metric !== null && metricNames.includes(payload.explore.default_metric)
      ? payload.explore.default_metric
      : metricNames[0] || null;
  let activePair = 0;

  const roles = new Map<string, RoleState>();
  const initialMain = new Map<string, boolean>();
  for (const name of metricNames) {
    const b = metricBlocks.get(name) as MetricBlock;
    roles.set(name, { main: b.main, guardrail: b.guardrail });
    initialMain.set(name, b.main);
  }
  const roleDirty = new Set<string>();

  let rawAlpha = expKnobs.alpha;
  let correction = expKnobs.correction;
  let alphaDirty = false;
  let correctionDirty = false;

  const edited = new Map<string, KnobValues>(); // live knob state per metric
  const dirty = new Set<string>(); // genuine user edits only
  const lastReply = new Map<string, RecomputeReply>();
  const lastComputed = new Map<string, KnobValues>(); // for Tier-R revert
  // metrics whose session cache now holds covariates thanks to a completed
  // /reload — the BAKED covariate_cutoffs fact goes stale the moment the
  // server re-renders, and forgetting that would demand a redundant full
  // warehouse reload on every method round-trip (milestone-review finding)
  const covariateReloaded = new Set<string>();

  // Seeded from the BAKED count (experiment.comparisons — it includes
  // duplicate-metric comparisons explore does not surface), then adjusted by
  // the user's live role flips on the surfaced ones. Counting the surfaced
  // subset alone would send a wrong effective alpha whenever a metric
  // appears in more than one comparison.
  const nonMainCount = (): number => {
    let n = expKnobs.non_main_count;
    for (const [name, r] of roles) {
      const was = initialMain.get(name) ? 0 : 1;
      n += (r.main ? 0 : 1) - was;
    }
    return Math.max(0, n);
  };
  const effAlphaFor = (metric: string): number =>
    effectiveAlpha(
      rawAlpha,
      correction,
      expKnobs.groups_count,
      nonMainCount(),
      roles.get(metric)?.main ?? false,
    );

  const configuredKnobs = (metric: string): KnobValues => {
    const s = surfaces[metric];
    return { method: s.configured.method, params: { ...s.configured.params } };
  };

  // ---- header ---------------------------------------------------------------
  root.appendChild(buildHeader(payload, live));

  const staticWarnings = [
    ...payload.warnings,
    ...payload.explore.warnings,
    ...(payload.explore.cache.disabled_reason !== null ? [payload.explore.cache.disabled_reason] : []),
  ];
  if (staticWarnings.length > 0) {
    const wrap = el('div', 'abk-warnings');
    for (const w of staticWarnings) wrap.appendChild(el('div', 'abk-warning', `⚠ ${w}`));
    root.appendChild(wrap);
  }

  if (activeMetric === null) {
    root.appendChild(
      el('div', 'abk-empty', 'No comparisons to explore — run `abk run` first, then reopen.'),
    );
    return;
  }

  // ---- cockpit skeleton -------------------------------------------------------
  const cockpit = el('div', 'abk-cockpit');
  root.appendChild(cockpit);

  const stage = el('div', 'abk-stage');
  cockpit.appendChild(stage);

  const hud = el('div', 'abk-hud');
  const chipsBar = el('div', 'abk-hud-chips');
  const modeRow = el('div', 'abk-modes');
  hud.appendChild(chipsBar);
  hud.appendChild(modeRow);
  stage.appendChild(hud);

  const legend = el('div', 'abk-legend');
  stage.appendChild(legend);

  const chartWrap = el('div', 'abk-chart abk-chart-main');
  const spinner = el('div', 'abk-spin', 'computing…');
  chartWrap.appendChild(spinner);
  stage.appendChild(chartWrap);

  const stageFoot = el('div', 'abk-stagefoot');
  const readout = el(
    'div',
    'abk-readout',
    'hover for a cutoff readout · scroll to zoom · drag to pan · double-click to reset',
  );
  const statBar = el('div', 'abk-stat');
  const warnBar = el('div', 'abk-warnbar');
  warnBar.style.display = 'none';
  stageFoot.appendChild(readout);
  stageFoot.appendChild(warnBar);
  stageFoot.appendChild(statBar);
  stage.appendChild(stageFoot);

  const rail = el('div', 'abk-rail');
  cockpit.appendChild(rail);
  const railHead = el('div', 'abk-railhead');
  const railTitle = el('div', 'abk-railtitle', 'Tune');
  railHead.appendChild(railTitle);
  rail.appendChild(railHead);
  const controls = el('div', 'abk-controls');
  rail.appendChild(controls);

  const topCommon = el('div', 'abk-rail-group');
  const tuneGroup = el('div', 'abk-rail-group');
  const reviewGroup = el('div', 'abk-rail-group');
  controls.appendChild(topCommon);
  controls.appendChild(tuneGroup);
  controls.appendChild(reviewGroup);

  const railFoot = el('div', 'abk-railfoot');
  rail.appendChild(railFoot);

  // ---- windshield chips -------------------------------------------------------
  const chipLift = statChip('lift');
  const chipCi = statChip('±CI');
  const chipP = statChip('p');
  const chipPower = statChip('power');
  const chipTier = el('span', 'abk-chip abk-tier');
  chipTier.style.display = 'none';
  const chipIdentity = el('span', 'abk-chip abk-identity', '⚠ different results series');
  chipIdentity.title =
    'the live knob state has a different method_config_id than the persisted series — ' +
    'Apply will start a new series and orphan the old rows';
  chipIdentity.style.display = 'none';
  const chipCal = el('span', 'abk-chip abk-calibration');
  chipsBar.appendChild(chipLift.chip);
  chipsBar.appendChild(chipCi.chip);
  chipsBar.appendChild(chipP.chip);
  chipsBar.appendChild(chipPower.chip);
  chipsBar.appendChild(chipTier);
  chipsBar.appendChild(chipIdentity);
  chipsBar.appendChild(chipCal);
  chipsBar.appendChild(buildSrmChip(payload));
  if (payload.look !== null && payload.cadence_seconds < 86400) {
    chipsBar.appendChild(
      el('span', 'abk-chip abk-look', `look ${payload.look.n} / ~${payload.look.planned} planned`),
    );
  }

  function statChip(label: string): { chip: HTMLElement; set(v: string): void } {
    const chip = el('span', 'abk-chip abk-live-chip');
    chip.appendChild(el('span', 'abk-chip-l', label));
    const val = el('span', 'abk-chip-v', '—');
    chip.appendChild(val);
    return {
      chip,
      set: (v: string): void => {
        val.textContent = v;
      },
    };
  }

  function setCalibrationChip(cal: CalibrationStatus): void {
    chipCal.className = 'abk-chip abk-calibration';
    chipCal.textContent = cal.headline || cal.state;
    if (cal.state === 'calibrated' && cal.over_budget) {
      chipCal.classList.add('abk-cal-over');
      chipCal.setAttribute('data-abk-calibration', 'over-budget');
    } else if (cal.state === 'calibrated') {
      chipCal.classList.add('abk-cal-ok');
      chipCal.setAttribute('data-abk-calibration', 'calibrated');
    } else if (cal.state === 'alpha_mismatch') {
      chipCal.classList.add('abk-cal-mismatch');
      chipCal.setAttribute('data-abk-calibration', 'alpha-mismatch');
    } else {
      chipCal.setAttribute('data-abk-calibration', 'uncalibrated');
    }
  }

  // ---- legend -----------------------------------------------------------------
  (function buildLegend(): void {
    const item = (cls: string, label: string, hint: string): void => {
      const li = el('span', 'abk-legend-item');
      li.appendChild(el('span', `abk-swatch ${cls}`));
      const t = el('span', undefined, label);
      t.title = hint;
      li.appendChild(t);
      legend.appendChild(li);
    };
    item('abk-sw-live', 'live effect ± CI', 'the current knob state, recomputed server-side');
    item('abk-sw-baseline', 'persisted baseline', 'what actually ran (_ab_results) — always visible');
    item('abk-sw-approx', 'approx (α-only)', 'hatched: α-inverted from the stored CI, not recomputed');
    item(
      'abk-sw-prehorizon',
      'pre-horizon (dashed)',
      'fixed CIs before the planned horizon are not peeking-valid (§4)',
    );
    item('abk-sw-insufficient', 'insufficient data', 'greyed cutoffs: counts + SRM only, no inference');
  })();

  // ---- chart --------------------------------------------------------------------
  interface ExploreChart {
    resize(): void;
    setLive(pts: ReplyPoint[] | null): void;
    dispose(): void;
  }
  let chart: ExploreChart | null = null;

  function rebuildChart(): void {
    const block = metricBlocks.get(activeMetric as string) as MetricBlock;
    const pair = block.pairs[activePair] || block.pairs[0];
    // unlike the report (one chart per page life), explore rebuilds per
    // metric/pair switch — the old chart's window listeners must go with it
    chart?.dispose();
    chartWrap.querySelector('canvas')?.remove();
    chartWrap.querySelector('.abk-chart-fallback')?.remove();
    chart = null;
    if (!pair || pair.series.length === 0) {
      chartWrap.appendChild(
        el('div', 'abk-chart-fallback', 'no persisted cutoffs for this pair yet — run `abk run` first'),
      );
      return;
    }
    const canvas = document.createElement('canvas');
    chartWrap.insertBefore(canvas, spinner);
    const created = createExploreChart(canvas, pair, payload, (html) => {
      readout.innerHTML = html;
    });
    if (created === null) {
      canvas.remove();
      chartWrap.appendChild(el('div', 'abk-chart-fallback', 'chart unavailable (no canvas 2D context)'));
      return;
    }
    chart = created;
    chart.resize();
    const reply = lastReply.get(activeMetric as string);
    if (reply) chart.setLive(replyPointsFor(reply, pair));
  }

  function replyPointsFor(reply: RecomputeReply, pair: PairBlock): ReplyPoint[] | null {
    const rp = reply.pairs.find((p: ReplyPair) => p.name_1 === pair.c && p.name_2 === pair.t);
    return rp ? rp.points : null;
  }

  // ---- §4 notes for the active pair (stable marker classes) ---------------------
  const notes = el('div', 'abk-notes');
  stage.insertBefore(notes, chartWrap);
  function rebuildNotes(): void {
    notes.textContent = '';
    // clear FIRST: an empty-series pair must not inherit the previous pair's
    // machine-checkable §4 marker attribute
    notes.removeAttribute('data-abk-prehorizon');
    const block = metricBlocks.get(activeMetric as string) as MetricBlock;
    const pair = block.pairs[activePair] || block.pairs[0];
    if (!pair || pair.series.length === 0) return;
    const pts = pair.series;
    const latest = pts[pts.length - 1];
    const horizonEd = (payload.period.horizon - payload.period.start) / MS_PER_DAY;
    if (latest.hz === 0) {
      notes.setAttribute('data-abk-prehorizon', '1');
      notes.appendChild(
        el(
          'div',
          'abk-note abk-prehorizon',
          `pre-horizon — fixed CIs are dashed/de-emphasized and not peeking-valid; ` +
            `planned horizon ${fmtDate(payload.period.horizon)} (${fmtVal(horizonEd)}d)`,
        ),
      );
    }
    const insCount = pts.reduce((acc, p) => acc + p.ins, 0);
    if (insCount > 0) {
      notes.appendChild(
        el(
          'div',
          'abk-note abk-insufficient',
          `${insCount} insufficient-data cutoff${insCount === 1 ? '' : 's'} greyed — counts + SRM only`,
        ),
      );
    }
  }

  // ---- net: debounce + stale-drop (the donor discipline over HTTP) --------------
  // Seed from Date.now(): request ids are a single global on the server that
  // survives a browser reload — a fresh page's ids must outrank any prior
  // page's or every request would be 409-dropped forever.
  let requestId = Date.now();
  let controller: AbortController | null = null;
  let debounceTimer = 0;

  const setStat = (text: string, kind: 'info' | 'err' = 'info'): void => {
    statBar.textContent = text;
    statBar.classList.toggle('abk-stat-err', kind === 'err');
  };

  function recompute(): void {
    if (debounceTimer) window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(runRecompute, DEBOUNCE_MS);
  }

  function runRecompute(): void {
    if (debounceTimer) {
      window.clearTimeout(debounceTimer);
      debounceTimer = 0;
    }
    if (!live || activeMetric === null) return;
    const knobs = readKnobs();
    edited.set(activeMetric, knobs);
    dispatch(payload.recompute_url as string, knobs, 'recompute');
  }

  function dispatch(url: string, knobs: KnobValues, kind: 'recompute' | 'reload'): void {
    const metric = activeMetric as string;
    requestId += 1;
    const myId = requestId;
    // Kill an in-flight compute instead of queuing behind it (the donor's
    // terminate-respawn, re-expressed): the server also drops the stale id.
    controller?.abort();
    controller = new AbortController();
    // the spinner is the in-flight flag: set here, cleared ONLY by the
    // current request's completion (stale/aborted replies leave it alone —
    // the replacement compute is live)
    spinner.classList.add('on');
    const body = JSON.stringify({
      metric,
      method: { name: knobs.method, params: minimalParams(knobs.method, knobs.params) },
      alpha: effAlphaFor(metric),
      request_id: myId,
    });
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      signal: controller.signal,
    })
      .then((r): Promise<void> | void => {
        // Only the CURRENT request's completion may touch the in-flight
        // flag/spinner — a reply to an outdated id leaves both alone (the
        // replacement compute is live).
        if (myId !== requestId) return;
        if (r.status === 409) {
          // our newest request was outranked server-side: another tab is
          // driving the same session — yield quietly, and re-seed the
          // counter so the NEXT knob turn actually outranks that tab
          // (incrementing an old seed by 1 would be 409-dropped forever)
          requestId = Math.max(requestId, Date.now());
          spinner.classList.remove('on');
          setStat('another explore tab is ahead — turn a knob to retake this one');
          return;
        }
        if (!r.ok) {
          return r.text().then((t) => {
            throw new Error(t || `HTTP ${r.status}`);
          });
        }
        return r.json().then((reply: RecomputeReply) => {
          if (myId !== requestId) return; // outdated by the time the body parsed
          spinner.classList.remove('on');
          adopt(reply, knobs, kind);
        });
      })
      .catch((e: Error) => {
        // our own abort = the deliberately-killed stale compute; the spinner
        // stays on for the replacement request
        if (e.name === 'AbortError' || myId !== requestId) return;
        spinner.classList.remove('on');
        setStat(`${kind} failed: ${e.message}`, 'err');
      });
  }

  // ---- adoption (exactly one place) ----------------------------------------------
  function adopt(reply: RecomputeReply, knobs: KnobValues, kind: 'recompute' | 'reload'): void {
    lastReply.set(reply.metric, reply);
    lastComputed.set(reply.metric, knobs);
    if (kind === 'reload') {
      // clear the REPLY metric's pending flag only; another metric's bar
      // (shown after a switch mid-reload) must survive this reply
      reloadPendingFor.delete(reply.metric);
      if (reply.metric === activeMetric) reloadBar.style.display = 'none';
      const reloadedWith = surfaces[reply.metric]?.methods.find((m) => m.name === reply.method);
      if (reloadedWith?.needs_covariate) covariateReloaded.add(reply.metric);
    }
    if (reply.metric !== activeMetric) return; // a metric switch raced the reply

    const block = metricBlocks.get(reply.metric) as MetricBlock;
    const pair = block.pairs[activePair] || block.pairs[0];
    const rp = pair ? reply.pairs.find((p) => p.name_1 === pair.c && p.name_2 === pair.t) : undefined;

    // chips off the reply (full names — server._result_json)
    const chips = rp ? rp.chips : null;
    chipLift.set(chips === null || chips.lift === null ? '—' : fmtSigned(chips.lift));
    chipCi.set(chips === null || chips.ci_half === null ? '—' : `±${fmtVal(chips.ci_half)}`);
    chipP.set(chips === null || chips.pvalue === null ? '—' : fmtP(chips.pvalue));
    if (chips !== null && chips.power !== null) {
      chipPower.set(`${(chips.power * 100).toFixed(0)}%`);
      chipPower.chip.title = 'achieved power to detect min_effect at the current knob state';
    } else {
      chipPower.set('—');
      chipPower.chip.title = (chips && chips.power_note) || 'power unavailable';
    }
    if (chips !== null && chips.tier !== null) {
      chipTier.style.display = '';
      chipTier.textContent = `tier: ${chips.tier}`;
      chipTier.title =
        chips.tier === 'approx'
          ? 'the latest chip source point is α-inverted (approx), not recomputed'
          : chips.tier === 'baseline'
            ? 'the latest chip source point is the persisted row passed through'
            : 'the latest chip source point was recomputed exactly';
    } else {
      chipTier.style.display = 'none';
    }
    chipIdentity.style.display = reply.identity_changed ? '' : 'none';
    setCalibrationChip(reply.calibration);

    if (chart && rp) chart.setLive(rp.points);
    else if (chart) chart.setLive(null);

    // stat bar: recomputable coverage + tier mix
    const baseCount = pair ? pair.series.length : 0;
    const pts = rp ? rp.points : [];
    const tiers = { exact: 0, approx: 0, baseline: 0 };
    for (const p of pts) tiers[p.tier] += 1;
    const mix = (['exact', 'approx', 'baseline'] as const)
      .filter((t) => tiers[t] > 0)
      .map((t) => `${tiers[t]} ${t}`)
      .join(' · ');
    let stat = `${pts.length}/${baseCount} cutoffs at this knob state (${mix || 'none'})`;
    if (reply.identity_changed && pts.length < baseCount) {
      stat += ' — gaps need a Tier-R reload or are not reconstructable';
    }
    setStat(stat);

    // engine + point warnings
    const pointWarnings = new Set<string>();
    for (const p of pts) for (const w of p.warnings) pointWarnings.add(w);
    const allWarnings = [...reply.warnings, ...pointWarnings];
    warnBar.textContent = '';
    warnBar.style.display = allWarnings.length > 0 ? '' : 'none';
    for (const w of allWarnings) warnBar.appendChild(el('div', 'abk-warning', `⚠ ${w}`));

    configEcho.textContent = configText(reply, knobs);
  }

  function configText(reply: RecomputeReply, knobs: KnobValues): string {
    const paramsStr = JSON.stringify(minimalParams(knobs.method, knobs.params));
    return (
      `// ${reply.metric}: method=${reply.method} params=${paramsStr} ` +
      `α=${fmtAlpha(reply.alpha)} (effective; raw ${fmtAlpha(rawAlpha)}, ${correction}) ` +
      `id=${reply.method_config_id.slice(0, 12)}…`
    );
  }

  // ---- the rail: knob machinery ----------------------------------------------------
  let methodCtl: Ctl<string> | null = null;
  let paramCtls: ParamCtl[] = [];
  const basicHost = el('div');
  const advancedHost = document.createElement('details');
  advancedHost.className = 'abk-advanced';
  advancedHost.appendChild(el('summary', undefined, 'advanced knobs'));
  const advancedBody = el('div');
  advancedHost.appendChild(advancedBody);

  const BASIC_PARAMS = new Set(['test_type']);

  function currentSurface(): MetricSurface {
    return surfaces[activeMetric as string];
  }
  function methodSurface(name: string): MethodSurface | undefined {
    return currentSurface().methods.find((m) => m.name === name);
  }

  function seedValueFor(spec: KnobSpec, method: string, saved: KnobValues | undefined): unknown {
    if (saved && saved.method === method && spec.name in saved.params) return saved.params[spec.name];
    const cfg = currentSurface().configured;
    if (cfg.method === method && spec.name in cfg.params) return cfg.params[spec.name];
    return spec.default;
  }

  function buildKnobControls(): void {
    basicHost.textContent = '';
    advancedBody.textContent = '';
    paramCtls = [];
    const surface = currentSurface();
    const saved = edited.get(activeMetric as string);
    const methodName = saved ? saved.method : surface.configured.method;
    const m = methodSurface(methodName);

    // method picker — Basic; the ↻ badge on covariate-needing methods with
    // no cached covariate (the reload substrate)
    const options = surface.methods.map((ms) => ({
      label:
        ms.needs_covariate &&
        surface.cache.covariate_cutoffs.length === 0 &&
        !covariateReloaded.has(activeMetric as string)
          ? `${ms.name} ↻`
          : ms.name,
      value: ms.name,
    }));
    methodCtl = segControl(
      'method',
      options,
      methodName,
      (v) => onMethodSwitch(v),
      'statistical method for this comparison — the full list valid for this metric type',
      m
        ? [
            {
              cls: 'abk-badge-identity',
              text: '⚠ series',
              hint: 'switching methods changes method_config_id — a new results series',
            },
          ]
        : [],
    );
    basicHost.appendChild(methodCtl.row);

    // (CUPED is chosen directly in the method picker above — `t-test` vs `cuped-t-test`.
    // The separate "CUPED on/off" checkbox was a redundant alias of that same switch and
    // was removed to avoid two controls doing one thing.)

    if (!m) return;
    for (const spec of m.params) {
      if (!spec.identity) continue; // seed / max_block_bytes: engine-managed, never knobs
      const tier = m.tiers[spec.name];
      const ctl = buildParamControl(spec, tier, seedValueFor(spec, methodName, saved), () =>
        knobChanged(),
      );
      paramCtls.push(ctl);
      (BASIC_PARAMS.has(spec.name) ? basicHost : advancedBody).appendChild(ctl.row);
    }

    // experiment-level knobs: alpha in Basic, correction in Advanced
    basicHost.appendChild(alphaCtl.row);
    advancedBody.appendChild(correctionCtl.row);
    advancedBody.appendChild(effAlphaEcho);
    advancedBody.appendChild(
      el(
        'div',
        'abk-ctl-note',
        'analysis unit — configured in the metric YAML (preview-only; not tunable here)',
      ),
    );
    refreshEffAlphaEcho();
  }

  // alpha + correction are experiment-level: they re-key EVERY comparison's
  // calibration and (under bonferroni) shift every effective alpha
  const alphaCtl = numberControl(
    'alpha (experiment)',
    {
      value: rawAlpha,
      min: 1e-9,
      max: 1 - 1e-9,
      step: 0.001,
      hint:
        'raw experiment-level significance level; the correction resolves it to the ' +
        'effective per-comparison α sent to /recompute. Identity-excluded (never enters ' +
        'method_config_id) but it re-keys the A/A calibration chip.',
    },
    (v) => {
      rawAlpha = v;
      alphaDirty = true;
      refreshEffAlphaEcho();
      knobChanged();
    },
  );
  const correctionCtl = segControl(
    'correction (experiment)',
    expKnobs.correction_choices.map((c) => ({ label: c === 'benjamini_hochberg' ? 'BH' : c, value: c })),
    correction,
    (v) => {
      correction = v;
      correctionDirty = true;
      refreshEffAlphaEcho();
      knobChanged();
    },
    'multiple-testing correction. bonferroni = the two-tier compute-time scheme; ' +
      'benjamini_hochberg is applied read-time (explore shows uncorrected per-comparison inference)',
  );
  const effAlphaEcho = el('div', 'abk-ctl-note');
  function refreshEffAlphaEcho(): void {
    if (activeMetric === null) return;
    const eff = effAlphaFor(activeMetric);
    let note = `effective α for ${activeMetric}: ${fmtAlpha(eff)}`;
    if (correction === 'bonferroni') note += ' (two-tier bonferroni)';
    if (correction === 'benjamini_hochberg') note += ' (BH is read-time — raw α at compute time)';
    effAlphaEcho.textContent = note;
  }

  // ---- Tier-R interception -------------------------------------------------------
  const reloadBar = el('div', 'abk-reloadbar');
  reloadBar.style.display = 'none';
  const reloadText = el('div', 'abk-reloadbar-text');
  const reloadBtns = el('div', 'abk-reloadbar-btns');
  const reloadGo = document.createElement('button');
  reloadGo.type = 'button';
  reloadGo.className = 'abk-btn abk-btn-reload';
  reloadGo.textContent = '↻ Reload cutoffs';
  const reloadRevert = document.createElement('button');
  reloadRevert.type = 'button';
  reloadRevert.className = 'abk-btn abk-btn-ghost';
  reloadRevert.textContent = 'Revert';
  reloadBtns.appendChild(reloadGo);
  reloadBtns.appendChild(reloadRevert);
  reloadBar.appendChild(reloadText);
  reloadBar.appendChild(reloadBtns);
  // pending Tier-R state is PER METRIC (reason kept for switch-back re-show):
  // a metric switch hides the bar UI but must not forget the pending change —
  // Apply's guard reads this map, not the bar's visibility
  const reloadPendingFor = new Map<string, string>();

  function showReloadBar(reason: string): void {
    reloadPendingFor.set(activeMetric as string, reason);
    reloadText.textContent = reason;
    reloadBar.style.display = '';
  }
  function hideReloadBar(metric?: string): void {
    reloadPendingFor.delete(metric ?? (activeMetric as string));
    reloadBar.style.display = 'none';
  }
  reloadGo.onclick = (): void => {
    if (payload.reload_url === null) {
      setStat('reload is unavailable in this session (static preview / no warehouse)', 'err');
      return;
    }
    if (activeMetric === null) return;
    const knobs = readKnobs();
    edited.set(activeMetric, knobs);
    dispatch(payload.reload_url, knobs, 'reload');
  };
  reloadRevert.onclick = (): void => {
    if (activeMetric === null) return;
    const back = lastComputed.get(activeMetric) || configuredKnobs(activeMetric);
    edited.set(activeMetric, back);
    buildKnobControls();
    hideReloadBar();
    recompute();
  };

  /** The knob state needs a warehouse reload when any Tier-R knob
   * (covariate_lookback) diverges from the last-computed state, or a METHOD
   * SWITCH lands on a covariate-needing method the cache cannot serve (the ↻
   * badge substrate: needs_covariate + empty covariate_cutoffs). EVERY R-tier
   * knob is scanned — not just the one that changed — so a pending R-edit
   * keeps demanding its reload while other knobs turn, and an edit back to
   * the computed value self-clears. Alpha/correction edits alone never
   * trigger it — α-inversion answers without the cache. */
  function needsReload(knobs: KnobValues, methodSwitched = false): boolean {
    const m = methodSurface(knobs.method);
    if (!m) return false;
    const surface = currentSurface();
    if (
      methodSwitched &&
      m.needs_covariate &&
      surface.cache.covariate_cutoffs.length === 0 &&
      !covariateReloaded.has(activeMetric as string)
    ) {
      return true;
    }
    const prev = lastComputed.get(activeMetric as string) || configuredKnobs(activeMetric as string);
    const prevParams = prev.method === knobs.method ? prev.params : {};
    for (const [name, tier] of Object.entries(m.tiers)) {
      if (tier !== 'R') continue;
      if (knobs.params[name] !== prevParams[name]) return true;
    }
    return false;
  }

  function readKnobs(): KnobValues {
    const method = methodCtl ? methodCtl.get() : currentSurface().configured.method;
    const params: Record<string, unknown> = {};
    for (const ctl of paramCtls) {
      const v = ctl.get();
      if (v === undefined) continue; // unset optional (empty text) — no value
      params[ctl.spec.name] = v; // FULL capture — see the KnobValues contract
    }
    return { method, params };
  }

  /** Drop knobs at their spec default for the wire (identity-equal — the
   * hash uses non-default identity params only; the applied YAML stays
   * minimal, the donor discipline). */
  function minimalParams(method: string, params: Record<string, unknown>): Record<string, unknown> {
    const m = methodSurface(method);
    const out: Record<string, unknown> = {};
    for (const [name, v] of Object.entries(params)) {
      const spec = m?.params.find((s) => s.name === name);
      if (spec && v === spec.default) continue;
      out[name] = v;
    }
    return out;
  }

  function knobChanged(): void {
    if (activeMetric === null) return;
    dirty.add(activeMetric);
    applyMsgReset();
    const knobs = readKnobs();
    edited.set(activeMetric, knobs);
    if (needsReload(knobs)) {
      // hold the debounced recompute back too — a timer armed by an earlier
      // edit would otherwise fire carrying the un-reloaded R-param and paint
      // a misleading gap state under the confirm bar
      if (debounceTimer) {
        window.clearTimeout(debounceTimer);
        debounceTimer = 0;
      }
      const cached = currentSurface().cache.cutoffs.length;
      showReloadBar(
        `this change re-renders ${cached || 'the'} cached cutoff${cached === 1 ? '' : 's'} from the ` +
          `warehouse (Tier R). If the metric has no covariate column, set covariate_lookback first.`,
      );
      return;
    }
    if (reloadPendingFor.has(activeMetric)) hideReloadBar(activeMetric);
    recompute();
  }

  function onMethodSwitch(name: string): void {
    if (activeMetric === null) return;
    dirty.add(activeMetric);
    applyMsgReset();
    // re-seed the knob set for the new method: an edited state for THIS
    // method wins, then the last-computed state (a method round-trip after a
    // reload must keep the rendered covariate_lookback), then defaults
    const saved = edited.get(activeMetric);
    const computed = lastComputed.get(activeMetric);
    edited.set(activeMetric, {
      method: name,
      params:
        saved && saved.method === name
          ? saved.params
          : computed && computed.method === name
            ? { ...computed.params }
            : {},
    });
    buildKnobControls();
    const knobs = readKnobs();
    edited.set(activeMetric, knobs);
    if (needsReload(knobs, true)) {
      if (debounceTimer) {
        window.clearTimeout(debounceTimer);
        debounceTimer = 0;
      }
      const m = methodSurface(name);
      showReloadBar(
        `'${name}' needs a per-unit covariate the session cache does not hold — a warehouse ` +
          `reload re-renders the cached cutoffs${
            m && m.params.some((s) => s.name === 'covariate_lookback')
              ? ' (set covariate_lookback for a pre-period covariate)'
              : ''
          }.`,
      );
      return;
    }
    if (reloadPendingFor.has(activeMetric)) hideReloadBar(activeMetric);
    recompute();
  }

  // ---- metric + pair pickers ---------------------------------------------------------
  function buildTopCommon(): void {
    topCommon.textContent = '';
    if (metricNames.length > 1) {
      const picker = segControl(
        'comparison',
        metricNames.map((n) => ({ label: n, value: n })),
        activeMetric as string,
        (v) => switchMetric(v),
        'each comparison tunes independently; Apply writes every dirty one',
      );
      topCommon.appendChild(picker.row);
    }
    const block = metricBlocks.get(activeMetric as string) as MetricBlock;
    if (block.pairs.length > 1) {
      const pairPicker = segControl(
        'pair',
        block.pairs.map((p, i) => ({ label: `${p.c} vs ${p.t}`, value: String(i) })),
        String(activePair),
        (v) => {
          activePair = Number(v);
          rebuildNotes();
          rebuildChart();
          const reply = lastReply.get(activeMetric as string);
          if (reply) adopt(reply, lastComputed.get(activeMetric as string) || readKnobs(), 'recompute');
        },
        'view a different variant pair — recomputes cover all pairs at once',
      );
      topCommon.appendChild(pairPicker.row);
    }
  }

  function switchMetric(name: string): void {
    if (activeMetric === name || activeMetric === null) return;
    // flush the outgoing state FIRST: a just-released slider only scheduled a
    // debounced capture — reseeding would silently lose an edit made <130 ms
    // before the switch (donor tune.ts 1748-1762)
    if (debounceTimer) {
      window.clearTimeout(debounceTimer);
      debounceTimer = 0;
    }
    edited.set(activeMetric, readKnobs());
    // kill the outgoing metric's in-flight compute: its reply would be
    // dropped by the metric guard anyway, but a late FAILURE must not paint
    // an error (nor the spinner survive) over the incoming view
    controller?.abort();
    controller = null;
    spinner.classList.remove('on');
    activeMetric = name;
    activePair = 0;
    reloadBar.style.display = 'none'; // UI only — pending flags are per metric
    buildTopCommon();
    buildKnobControls();
    rebuildNotes();
    rebuildChart();
    seedChipsFromSurface();
    refreshEffAlphaEcho();
    buildReviewGroup();
    const pendingReason = reloadPendingFor.get(name);
    if (pendingReason !== undefined) showReloadBar(pendingReason);
    const reply = lastReply.get(name);
    if (reply) adopt(reply, lastComputed.get(name) || readKnobs(), 'recompute');
    if (live && pendingReason === undefined) {
      // a cached reply paints instantly, but it may be STALE — knobs edited
      // <130 ms before the last switch away, or an alpha/correction/role
      // change since it was computed (the donor unconditionally recomputes
      // on switch; we skip only a provably fresh reply)
      const knobsNow = readKnobs();
      const prev = lastComputed.get(name);
      const fresh =
        reply !== undefined &&
        prev !== undefined &&
        reply.alpha === effAlphaFor(name) &&
        JSON.stringify(knobsNow) === JSON.stringify(prev);
      if (!fresh) runRecompute(); // direct — first paint shouldn't wait 130 ms
    }
  }

  function seedChipsFromSurface(): void {
    // before the first reply: the configured calibration chip (D3 initial key)
    setCalibrationChip(currentSurface().calibration);
    chipLift.set('—');
    chipCi.set('—');
    chipP.set('—');
    chipPower.set('—');
    chipTier.style.display = 'none';
    chipIdentity.style.display = 'none';
  }

  // ---- Review mode (D9: guardrail/primary marking only) ---------------------------
  function buildReviewGroup(): void {
    reviewGroup.textContent = '';
    reviewGroup.appendChild(
      el(
        'div',
        'abk-ctl-note',
        'mark each comparison\'s role — flips ride into Apply and re-tier the bonferroni ' +
          'budget (all calibration re-keys conservatively)',
      ),
    );
    for (const name of metricNames) {
      const state = roles.get(name) as RoleState;
      const rowWrap = el('div', 'abk-review-row');
      rowWrap.appendChild(el('div', 'abk-review-name', name));
      const mainCtl = checkControl(
        'main',
        state.main,
        (v) => {
          // ANY main-flip can re-tier the bonferroni budget and move the
          // ACTIVE metric's effective alpha — recompute whenever it did
          const effBefore = activeMetric === null ? null : effAlphaFor(activeMetric);
          state.main = v;
          roleDirty.add(name);
          applyMsgReset();
          refreshEffAlphaEcho();
          if (activeMetric !== null && effAlphaFor(activeMetric) !== effBefore) recompute();
        },
        'is_main_metric — the main tier gets the undivided bonferroni budget',
      );
      const guardCtl = checkControl(
        'guardrail',
        state.guardrail,
        (v) => {
          state.guardrail = v;
          roleDirty.add(name);
          applyMsgReset();
        },
        'is_guardrail — regressions surface in the verdict rationale',
      );
      const checks = el('div', 'abk-review-checks');
      checks.appendChild(mainCtl.row);
      checks.appendChild(guardCtl.row);
      rowWrap.appendChild(checks);
      const verdict = payload.verdicts.find((v) => v.metric === name);
      if (verdict) {
        rowWrap.appendChild(
          el(
            'div',
            `abk-review-verdict abk-verdict-${verdict.verdict.toLowerCase()}`,
            `${verdict.verdict} (${verdict.pair.c} vs ${verdict.pair.t})`,
          ),
        );
      }
      reviewGroup.appendChild(rowWrap);
    }
  }

  // ---- modes ---------------------------------------------------------------------
  type UiMode = 'tune' | 'review' | 'auto' | 'segment';
  const MODES: Array<{ v: UiMode; label: string; hint: string; inert?: boolean }> = [
    { v: 'tune', label: 'Tune', hint: 'turn method knobs, watch the windshield' },
    { v: 'review', label: 'Review', hint: 'mark guardrail vs primary before applying (D9)' },
    {
      v: 'auto',
      label: 'Auto',
      hint: 'run a reduced A/A validation server-side and green the calibration chip (a fast estimate — `abk validate` for the full run)',
    },
    { v: 'segment', label: 'Segment', hint: 'heterogeneous effects — deferred (D9, ROADMAP)', inert: true },
  ];
  const RAIL_TITLES: Record<UiMode, string> = {
    tune: 'Tune',
    review: 'Review — roles',
    auto: 'Auto',
    segment: 'Segment',
  };
  let uiMode: UiMode = 'tune';
  const modeBtns: HTMLButtonElement[] = [];
  let autoBtn: HTMLButtonElement | null = null;
  for (const m of MODES) {
    const b = document.createElement('button');
    b.type = 'button';
    // Auto is live only when a server route is bound (WP6); a static `--no-serve`
    // preview or a saved report keeps it disabled (validate_url null), Segment is inert.
    const noServerAuto = m.v === 'auto' && payload.validate_url === null;
    const disabled = m.inert || noServerAuto;
    b.className = 'abk-mode-btn' + (disabled ? ' abk-mode-disabled' : '');
    b.textContent = m.label;
    // An honest tooltip: say WHY Auto is dead and what to do, instead of a silent grey.
    b.title = noServerAuto
      ? 'Auto needs a live server — run `abk explore` (without --no-serve) and open the printed localhost (127.0.0.1) URL, not a saved report'
      : m.hint;
    if (disabled) b.setAttribute('aria-disabled', 'true');
    b.dataset.v = m.v;
    if (m.v === 'auto') autoBtn = b;
    b.onclick = (): void => {
      if (m.inert) return;
      if (m.v === 'auto') {
        pokeValidate();
        return;
      }
      setUiMode(m.v);
    };
    modeBtns.push(b);
    modeRow.appendChild(b);
  }
  function setUiMode(mode: UiMode): void {
    uiMode = mode;
    for (const b of modeBtns) b.classList.toggle('on', b.dataset.v === uiMode);
    tuneGroup.style.display = uiMode === 'tune' ? '' : 'none';
    reviewGroup.style.display = uiMode === 'review' ? '' : 'none';
    railTitle.textContent = RAIL_TITLES[uiMode];
  }
  function pokeValidate(): void {
    // Auto mode (WP6): a reduced server-side `abk validate` that refreshes the
    // live session's calibration in place and answers with the recommended knob
    // state per metric. Shares the monotonic request_id stale-drop with
    // /recompute + /reload so a knob turn mid-validate supersedes it cleanly.
    if (payload.validate_url === null) {
      setStat('Auto mode needs a served session — run `abk explore` without --no-serve');
      return;
    }
    // Flush any pending debounced /recompute FIRST (the switchMetric/runRecompute
    // discipline): a stale timer armed by a knob edit just before this click would
    // otherwise fire mid-validate and abort the in-flight /validate, dropping the
    // re-seed and the chip-green. The superseded edit is intentional — adoptValidate
    // re-seeds the rail to the recommendation and drives its own recompute (R18).
    if (debounceTimer) {
      window.clearTimeout(debounceTimer);
      debounceTimer = 0;
    }
    requestId += 1;
    const myId = requestId;
    controller?.abort();
    controller = new AbortController();
    spinner.classList.add('on');
    autoBtn?.classList.add('busy');
    setStat('Auto: running a reduced A/A validation (fast estimate — `abk validate` for the full run)…');
    fetch(payload.validate_url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: myId }),
      signal: controller.signal,
    })
      .then((r): Promise<void> | void => {
        if (myId !== requestId) return; // superseded by a newer request
        if (r.status === 409) {
          requestId = Math.max(requestId, Date.now());
          spinner.classList.remove('on');
          autoBtn?.classList.remove('busy');
          setStat('another explore tab is ahead — turn a knob to retake this one');
          return;
        }
        if (!r.ok) {
          return r.text().then((t) => {
            throw new Error(t || `HTTP ${r.status}`);
          });
        }
        return r.json().then((reply: ValidateReply) => {
          if (myId !== requestId) return; // outdated by the time the body parsed
          spinner.classList.remove('on');
          autoBtn?.classList.remove('busy');
          adoptValidate(reply);
        });
      })
      .catch((e: Error) => {
        if (e.name === 'AbortError' || myId !== requestId) return;
        spinner.classList.remove('on');
        autoBtn?.classList.remove('busy');
        setStat(`Auto validate failed: ${e.message}`, 'err');
      });
  }

  function adoptValidate(reply: ValidateReply): void {
    const recommended = reply.recommended || {};
    for (const [name, rec] of Object.entries(recommended)) {
      if (!(name in surfaces)) continue;
      // refresh the baked-surface calibration fallback for EVERY metric — the
      // Apply gate and seedChipsFromSurface read it when a metric has no live
      // reply yet, so a metric switched-to after Auto shows the green chip (D3).
      // (A metric the user is actively editing has a live lastReply that takes
      // precedence, so this never over-greens an uncalibrated edit.)
      surfaces[name].calibration = rec.calibration;
    }
    const active = activeMetric;
    const rec = active !== null ? recommended[active] : undefined;
    if (active !== null && rec !== undefined) {
      // re-seed only the VISIBLE rail to the recommendation (R18), paint the
      // refreshed chip now, then recompute so the series repaints against the
      // freshly-greened rows. A background metric's in-progress edit is never
      // silently overwritten — only its calibration fact is refreshed above.
      edited.set(active, { method: rec.method.name, params: { ...rec.method.params } });
      buildKnobControls();
      setCalibrationChip(rec.calibration);
      runRecompute();
    }
    const parts = Object.entries(recommended).map(([name, r]) => `${name}: ${r.calibration.headline}`);
    setStat(parts.length > 0 ? `Auto — ${parts.join(' · ')}` : 'Auto: no method could be calibrated on this data');
  }

  // ---- Apply ------------------------------------------------------------------------
  const configToggle = el('div', 'abk-cfg-toggle', '▸ effective config');
  const configEcho = el('div', 'abk-cfg-echo');
  configEcho.style.display = 'none';
  configToggle.onclick = (): void => {
    const open = configEcho.style.display === 'none';
    configEcho.style.display = open ? '' : 'none';
    configToggle.textContent = (open ? '▾' : '▸') + ' effective config';
  };
  railFoot.appendChild(reloadBar);
  railFoot.appendChild(configToggle);
  railFoot.appendChild(configEcho);

  const applyBtn = document.createElement('button');
  applyBtn.type = 'button';
  applyBtn.className = 'abk-btn abk-btn-apply';
  applyBtn.textContent = 'Apply';
  applyBtn.title =
    'validate + archive the prior YAML to experiments/.history/ and write the dirty ' +
    'comparisons back. Viewing a comparison never writes it — only tuned ones.';
  const applyMsg = el('div', 'abk-apply-msg');
  const confirmBox = el('div', 'abk-confirm');
  confirmBox.style.display = 'none';

  if (canApply) {
    railFoot.appendChild(applyBtn);
    railFoot.appendChild(confirmBox);
    railFoot.appendChild(applyMsg);
  } else {
    railFoot.appendChild(
      el(
        'div',
        'abk-preview-note',
        'static preview — Apply is disabled. Run `abk explore` without --no-serve to tune and apply.',
      ),
    );
  }

  function applyMsgReset(): void {
    if (applyMsg.textContent !== '') {
      applyMsg.textContent = '';
      applyMsg.className = 'abk-apply-msg';
    }
    // a knob turned while the confirm box is open makes its cost text stale —
    // force the user back through the guarded Apply button
    if (confirmBox.style.display !== 'none') {
      confirmBox.style.display = 'none';
      applyBtn.disabled = false;
    }
  }

  /** The shared Apply preflight: flush the on-screen state, refuse the
   * no-op and pending-Tier-R cases. BOTH entry points (the Apply button and
   * the confirm box's "Apply anyway") must run it — the confirm path used to
   * skip the guards, letting an un-computed Tier-R edit ride into the YAML
   * (milestone-review finding). */
  function preflightApply(): ApplyRequest | null {
    if (activeMetric !== null) {
      // capture the on-screen state even if no change event fired (donor 1798)
      if (debounceTimer) {
        window.clearTimeout(debounceTimer);
        debounceTimer = 0;
      }
      if (dirty.has(activeMetric)) edited.set(activeMetric, readKnobs());
    }
    const body = collectApplyBody();
    if (body.comparisons.length === 0 && body.alpha === undefined && body.correction === undefined) {
      applyMsg.className = 'abk-apply-msg info';
      applyMsg.textContent = 'nothing to apply — no knob has been tuned';
      return null;
    }
    if (reloadPendingFor.size > 0) {
      applyMsg.className = 'abk-apply-msg err';
      applyMsg.textContent =
        `a Tier-R change is pending on ${[...reloadPendingFor.keys()].join(', ')} — ` +
        'reload or revert it before applying';
      return null;
    }
    return body;
  }

  function collectApplyBody(): ApplyRequest {
    const comparisons: ApplyComparison[] = [];
    const touched = new Set<string>([...dirty, ...roleDirty]);
    for (const name of metricNames) {
      if (!touched.has(name)) continue; // dirty-slot discipline: viewed ≠ written
      const entry: ApplyComparison = { metric: name };
      if (dirty.has(name)) {
        const k = edited.get(name) || configuredKnobs(name);
        // params key ALWAYS present on a method edit ("the cockpit sends
        // what it shows"); role-only flips carry no method key at all
        entry.method = { name: k.method, params: minimalParams(k.method, k.params) };
      }
      if (roleDirty.has(name)) {
        const r = roles.get(name) as RoleState;
        entry.is_main_metric = r.main;
        entry.is_guardrail = r.guardrail;
      }
      comparisons.push(entry);
    }
    const body: ApplyRequest = { comparisons };
    if (alphaDirty) body.alpha = rawAlpha;
    if (correctionDirty) body.correction = correction;
    return body;
  }

  /** Client half of the D3 gate: green iff every affected comparison's last
   * known calibration is calibrated within budget. An alpha/correction edit
   * or any role flip re-keys EVERYTHING — gate conservatively (the server
   * enforces the same rule independently). */
  function applyGateGreen(): boolean {
    const affected = new Set<string>([...dirty, ...roleDirty]);
    if (alphaDirty || correctionDirty || roleDirty.size > 0) {
      for (const name of metricNames) affected.add(name);
    }
    for (const name of affected) {
      const cal = lastReply.get(name)?.calibration ?? surfaces[name].calibration;
      if (cal.state !== 'calibrated' || cal.over_budget) return false;
    }
    return true;
  }

  function gateHeadlines(): string[] {
    const out: string[] = [];
    const affected = new Set<string>([...dirty, ...roleDirty]);
    for (const name of affected) {
      const cal = lastReply.get(name)?.calibration ?? surfaces[name].calibration;
      if (cal.state !== 'calibrated' || cal.over_budget) out.push(`${name}: ${cal.headline}`);
    }
    return out;
  }

  function showConfirm(detail: string): void {
    confirmBox.textContent = '';
    confirmBox.style.display = '';
    confirmBox.appendChild(el('div', 'abk-confirm-text', detail));
    const row = el('div', 'abk-confirm-btns');
    const go = document.createElement('button');
    go.type = 'button';
    go.className = 'abk-btn abk-btn-danger';
    go.textContent = 'Apply anyway';
    go.onclick = (): void => {
      confirmBox.style.display = 'none';
      const body = preflightApply(); // the SAME guards as the Apply button
      if (body === null) {
        applyBtn.disabled = false;
        return;
      }
      sendApply({ ...body, confirm_uncalibrated: true });
    };
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'abk-btn abk-btn-ghost';
    cancel.textContent = 'Cancel';
    cancel.onclick = (): void => {
      confirmBox.style.display = 'none';
      applyBtn.disabled = false;
    };
    row.appendChild(go);
    row.appendChild(cancel);
    confirmBox.appendChild(row);
  }

  applyBtn.onclick = (): void => {
    const body = preflightApply();
    if (body === null) return;
    applyBtn.disabled = true;
    if (!applyGateGreen()) {
      const lines = gateHeadlines();
      showConfirm(
        'these params have never passed `abk validate` — the real FPR is unknown and the ' +
          'nominal α may understate it.' +
          (lines.length > 0 ? ` (${lines.join('; ')})` : ''),
      );
      return;
    }
    sendApply(body);
  };

  function sendApply(body: ApplyRequest): void {
    applyMsg.className = 'abk-apply-msg info';
    applyMsg.textContent = 'Applying…';
    applyBtn.disabled = true;
    fetch(payload.save_url as string, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then((r) => {
        if (r.ok) return r.json();
        return r.text().then((t) => {
          if (r.status === 409 && !body.confirm_uncalibrated && /confirm_uncalibrated/.test(t)) {
            // the server-side half of the gate disagreed with our cached
            // view — show ITS cost text and re-offer the confirmed path
            showConfirm(t);
            applyMsg.textContent = '';
            applyMsg.className = 'abk-apply-msg';
            return null;
          }
          throw new Error(t || `HTTP ${r.status}`);
        });
      })
      .then((res: ApplyReply | null) => {
        if (res === null) return;
        applyMsg.className = 'abk-apply-msg ok';
        applyMsg.textContent = '';
        applyMsg.appendChild(
          el(
            'div',
            undefined,
            `Applied → ${res.saved} (previous archived: ${res.archived}).`,
          ),
        );
        const parts: string[] = [];
        if (res.updated.length > 0) parts.push(`updated: ${res.updated.join(', ')}`);
        if (res.preserved.length > 0) parts.push(`preserved: ${res.preserved.join(', ')}`);
        if (res.experiment_fields.length > 0) parts.push(`experiment fields: ${res.experiment_fields.join(', ')}`);
        if (parts.length > 0) applyMsg.appendChild(el('div', undefined, parts.join(' · ')));
        if (res.orphan_warning !== null) {
          applyMsg.appendChild(el('div', 'abk-orphan', `⚠ ${res.orphan_warning}`));
        }
        applyMsg.appendChild(
          el('div', undefined, `re-run \`abk run --select ${payload.experiment}\` — you can close this tab.`),
        );
        // one apply per page life: the server is already shutting down
      })
      .catch((e: Error) => {
        applyBtn.disabled = false;
        applyMsg.className = 'abk-apply-msg err';
        applyMsg.textContent = `Apply failed: ${e.message}`;
      });
  }

  // ---- static-preview degradation ---------------------------------------------------
  if (!live) {
    controls.classList.add('abk-rail-off');
    const note = el(
      'div',
      'abk-preview-note',
      'knobs are read-only in this static page — the recompute endpoint is not available',
    );
    controls.insertBefore(note, controls.firstChild);
  }

  // ---- init (order matters: DOM → mode → rail → chart → first recompute) -------------
  tuneGroup.appendChild(basicHost);
  tuneGroup.appendChild(advancedHost);
  buildTopCommon();
  buildKnobControls();
  buildReviewGroup();
  setUiMode('tune');
  rebuildNotes();
  rebuildChart();
  seedChipsFromSurface();
  if (live) {
    runRecompute(); // direct, not debounced — first paint shouldn't wait 130 ms
  } else {
    setStat('static preview — knob recompute needs the live `abk explore` server');
  }

  // resize: rAF-coalesced; ResizeObserver on the chart wrap (rail collapse and
  // font reflow re-fit too), window resize as the fallback
  let raf = 0;
  const refit = (): void => {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      raf = 0;
      chart?.resize();
    });
  };
  const onWinResize = (): void => refit();
  window.addEventListener('resize', onWinResize);
  disposers.push(() => window.removeEventListener('resize', onWinResize));
  disposers.push(() => chart?.dispose());
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(refit);
    ro.observe(chartWrap);
    disposers.push(() => ro.disconnect());
  }
}

// ----------------------------------------------------------------------------
// Header (title, badge, meta with the freshness watermark, SRM echo source)
// ----------------------------------------------------------------------------

function buildHeader(payload: ExplorePayload, live: boolean): HTMLElement {
  const h = el('div', 'abk-header');
  h.appendChild(makeBrandLockup());
  const top = el('div', 'abk-h-top');
  const h1 = el('h1', 'abk-title');
  h1.innerHTML = payload.project
    ? `${esc(payload.project)} · ${esc(payload.experiment)}`
    : esc(payload.experiment);
  top.appendChild(h1);
  top.appendChild(
    el('span', `abk-badge-page ${live ? 'abk-badge-live' : 'abk-badge-preview'}`, live ? 'explore' : 'preview'),
  );
  h.appendChild(top);

  // period timestamps render in UTC (ms-epoch bake) — label them so a
  // non-UTC experiment tz shown next door can't read as an off-by-one date
  const watermark =
    payload.period.end > 0
      ? `latest cutoff ${fmtTs(payload.period.end)} UTC (whatever the last \`abk run\` produced)`
      : 'no persisted cutoffs yet';
  const meta =
    `${fmtDate(payload.period.start)} – horizon ${fmtDate(payload.period.horizon)} (UTC)` +
    ` · cadence ${humanCadence(payload.cadence_seconds)} · tz ${esc(payload.tz)}` +
    ` · arms: ${payload.arms.map(esc).join(' vs ')} (first = control) · ${esc(watermark)}`;
  const metaDiv = el('div', 'abk-meta');
  metaDiv.innerHTML = meta;
  h.appendChild(metaDiv);
  if (payload.description) h.appendChild(el('p', 'abk-desc', payload.description));
  return h;
}

/** The red SRM gate chip — identical semantics to the report's (§6 must-fix). */
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
    chip.textContent = `SRM FAILED (observed ${obs} vs expected ${exp}, χ² ${p}) — effects untrustworthy`;
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

// ----------------------------------------------------------------------------
// The explore stabilization chart: persisted baseline always visible; the
// live knob-state series with D1-tier-styled segments (solid exact, hatched
// approx, §4 dashed pre-horizon), greyed insufficient spans, zero line,
// horizon divider, hover readout, wheel-zoom/drag-pan.
// ----------------------------------------------------------------------------

interface StyledRun {
  approx: boolean;
  pre: boolean;
  band: BandPoint[];
  xs: number[];
  es: number[];
}

function createExploreChart(
  canvas: HTMLCanvasElement,
  pair: PairBlock,
  payload: ExplorePayload,
  setReadout: (html: string) => void,
): { resize(): void; setLive(pts: ReplyPoint[] | null): void; dispose(): void } | null {
  const g = canvas.getContext('2d');
  if (!g) return null;

  const base = [...pair.series].sort((a, b) => a.t - b.t);
  const xsB = base.map((p) => (p.ed === null ? NaN : p.ed));
  const esB = base.map((p) => num(p.e));
  const horizonEd = (payload.period.horizon - payload.period.start) / MS_PER_DAY;
  // §4 corroborated horizon: a stored hz=1 row is decision-grade only while
  // it still IS the current config horizon — after an end_date extension the
  // old horizon row goes stale mid-series (the planner never rewrites
  // computed rows) and everything must render pre-horizon again
  // (milestone-review finding).
  const isDecisionGrade = (p: SeriesPoint): boolean =>
    p.hz === 1 && p.t >= payload.period.horizon;
  const firstHzIdx = base.findIndex(isDecisionGrade);
  const storedHz = firstHzIdx !== -1 && base[firstHzIdx].ed !== null ? base[firstHzIdx] : undefined;
  const dividerEd = storedHz !== undefined ? (storedHz.ed as number) : horizonEd;
  const firstHzTs = firstHzIdx !== -1 ? base[firstHzIdx].t : null;
  const isPre = (endTs: number): boolean => (firstHzTs === null ? true : endTs < firstHzTs);

  // baseline pre/post bands (drawn only while no live series is adopted)
  const preBandB: BandPoint[] = [];
  const postBandB: BandPoint[] = [];
  base.forEach((p, i) => {
    if (Number.isNaN(xsB[i])) return;
    const bp: BandPoint = { x: xsB[i], lo: p.lo, hi: p.hi };
    if (firstHzIdx === -1 || i < firstHzIdx) preBandB.push(bp);
    else postBandB.push(bp);
  });
  if (firstHzIdx !== -1 && preBandB.length > 0 && postBandB.length > 0) {
    postBandB.unshift(preBandB[preBandB.length - 1]);
  }

  // greyed insufficient + SRM-blocked spans from the BAKED series (data truth)
  const finiteXsB = xsB.filter((x) => !Number.isNaN(x));
  const gapsB: number[] = [];
  for (let i = 1; i < finiteXsB.length; i++) gapsB.push(finiteXsB[i] - finiteXsB[i - 1]);
  gapsB.sort((a, b) => a - b);
  const step =
    gapsB.length > 0 ? gapsB[Math.floor(gapsB.length / 2)] : payload.cadence_seconds / 86400;
  const spansFor = (flag: (p: SeriesPoint) => boolean): Array<[number, number]> => {
    const spans: Array<[number, number]> = [];
    base.forEach((p, i) => {
      if (!flag(p) || Number.isNaN(xsB[i])) return;
      const a = xsB[i] - step / 2;
      const b = xsB[i] + step / 2;
      const last = spans[spans.length - 1];
      if (last && a <= last[1]) last[1] = b;
      else spans.push([a, b]);
    });
    return spans;
  };
  const insSpans = spansFor((p) => p.ins === 1);
  const blkSpans = spansFor((p) => p.blk === 1 && p.ins === 0);

  // live state ------------------------------------------------------------------
  let livePts: ReplyPoint[] | null = null;
  let liveRuns: StyledRun[] = [];
  let xsL: number[] = [];
  let esL: number[] = [];

  // sorted persisted cutoffs — the coverage grid the live layer is honest to
  const baseTsSorted = base.filter((p) => p.ed !== null).map((p) => p.t);
  function hasBaseCutoffBetween(a: number, b: number): boolean {
    let lo = 0;
    let hi = baseTsSorted.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (baseTsSorted[mid] <= a) lo = mid + 1;
      else hi = mid - 1;
    }
    return lo < baseTsSorted.length && baseTsSorted[lo] < b;
  }

  function rebuildLive(): void {
    liveRuns = [];
    xsL = [];
    esL = [];
    if (livePts === null) return;
    const pts = [...livePts].sort((a, b) => a.end_ts - b.end_ts);
    xsL = pts.map((p) => (p.elapsed_days === null ? NaN : p.elapsed_days));
    esL = pts.map((p) => num(p.effect));
    let run: StyledRun | null = null;
    let prevTs: number | null = null;
    pts.forEach((p, i) => {
      if (Number.isNaN(xsL[i])) {
        run = null;
        return;
      }
      // §4/D1 coverage honesty: a persisted cutoff BETWEEN two live points
      // that the server refused to compute (absent from the reply — identity
      // changed, cache can't serve) is a gap; break the run so the corridor
      // and line never claim it (the baseline's NaN-pen discipline mirrored)
      if (run !== null && prevTs !== null && hasBaseCutoffBetween(prevTs, p.end_ts)) {
        run = null;
      }
      prevTs = p.end_ts;
      const approx = p.tier === 'approx';
      const pre = isPre(p.end_ts);
      const bp: BandPoint = { x: xsL[i], lo: p.left_bound, hi: p.right_bound };
      if (run === null || run.approx !== approx || run.pre !== pre) {
        const prev = run;
        run = { approx, pre, band: [], xs: [], es: [] };
        if (prev !== null && prev.band.length > 0) {
          // bridge: the corridor and line stay continuous across style splits
          run.band.push(prev.band[prev.band.length - 1]);
          run.xs.push(prev.xs[prev.xs.length - 1]);
          run.es.push(prev.es[prev.es.length - 1]);
        }
        liveRuns.push(run);
      }
      run.band.push(bp);
      run.xs.push(xsL[i]);
      run.es.push(esL[i]);
    });
  }

  // domain: FIXED to the persisted baseline (effects + CI + zero) so turning a
  // knob visibly moves the live layer instead of the axis rescaling in
  // lockstep and making the change look like a no-op (the donor's yFit:'data'
  // lesson, tune.ts 930-931); the live band may clip — that IS the signal.
  const xmin = 0;
  const xmax = Math.max(dividerEd, horizonEd, ...finiteXsB, 1);
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
    for (const p of base) {
      fold(p.e);
      fold(p.lo);
      fold(p.hi);
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
      lo = -1;
      hi = 1;
    }
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
  let hatch: CanvasPattern | null = null;

  const domain = (): Domain => ({ xmin: viewMin, xmax: viewMax, vmin, vmax });
  const scales = (): Scales => makeScales(canvas, MARGINS, domain(), dpr);

  function hatchPattern(color: string): CanvasPattern | null {
    if (hatch !== null) return hatch;
    const tile = document.createElement('canvas');
    const side = Math.max(6, Math.round(6 * dpr));
    tile.width = side;
    tile.height = side;
    const tg = tile.getContext('2d');
    if (!tg) return null;
    tg.strokeStyle = color;
    tg.globalAlpha = 0.3;
    tg.lineWidth = Math.max(1, dpr);
    tg.beginPath();
    tg.moveTo(0, side);
    tg.lineTo(side, 0);
    tg.stroke();
    hatch = g!.createPattern(tile, 'repeat');
    return hatch;
  }

  function fillBandHatched(run: StyledRun, sc: Scales, accent: string): void {
    const pattern = hatchPattern(accent);
    const runs = scoredRuns(run.band);
    if (pattern !== null) {
      g!.fillStyle = pattern;
      for (const [a, b] of runs) {
        if (a === b) continue;
        g!.beginPath();
        g!.moveTo(sc.px(run.band[a].x), sc.py(run.band[a].hi as number));
        for (let i = a + 1; i <= b; i++) g!.lineTo(sc.px(run.band[i].x), sc.py(run.band[i].hi as number));
        for (let i = b; i >= a; i--) g!.lineTo(sc.px(run.band[i].x), sc.py(run.band[i].lo as number));
        g!.closePath();
        g!.fill();
      }
    }
    // dashed edges mark the approx corridor even where the pattern is faint
    fillBand(g!, run.band, runs, sc.px, sc.py, accent, 0, 0.35, dpr, [3, 3]);
  }

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
    drawHLine(g!, canvas, MARGINS, dpr, sc.py, 0, rgba(ink, 0.6), '');

    g!.save();
    g!.beginPath();
    const r = plotRect(canvas, MARGINS, dpr);
    g!.rect(r.left, r.top, sc.plotW(), sc.plotH());
    g!.clip();

    for (const [a, b] of insSpans) {
      if (b < viewMin || a > viewMax) continue;
      fillVSpan(g!, canvas, MARGINS, dpr, sc.px, a, b, token('--abk-muted'), 0.22);
    }
    for (const [a, b] of blkSpans) {
      if (b < viewMin || a > viewMax) continue;
      fillVSpan(g!, canvas, MARGINS, dpr, sc.px, a, b, token('--abk-st-critical'), 0.05);
    }

    if (livePts === null) {
      // no live series yet (static preview / first paint): the baseline IS
      // the chart — report-identical band treatment (§4 split)
      fillBand(g!, preBandB, scoredRuns(preBandB), sc.px, sc.py, accent, 0.07, 0.28, dpr, [4, 4]);
      fillBand(g!, postBandB, scoredRuns(postBandB), sc.px, sc.py, accent, 0.13, 0.4, dpr);
      drawSeriesDecimated(g!, xsB, esB, viewMin, viewMax, r.left, sc.plotW(), sc.px, sc.py, accent, 2, dpr);
    } else {
      // the persisted baseline stays visible as a reference line
      drawSeriesDecimated(g!, xsB, esB, viewMin, viewMax, r.left, sc.plotW(), sc.px, sc.py, rgba(ink, 0.55), 1.25, dpr, [2, 3]);
      for (const run of liveRuns) {
        const runsIdx = scoredRuns(run.band);
        if (run.approx) {
          fillBandHatched(run, sc, accent);
        } else if (run.pre) {
          fillBand(g!, run.band, runsIdx, sc.px, sc.py, accent, 0.07, 0.28, dpr, [4, 4]);
        } else {
          fillBand(g!, run.band, runsIdx, sc.px, sc.py, accent, 0.13, 0.4, dpr);
        }
      }
      for (const run of liveRuns) {
        drawSeriesDecimated(
          g!,
          run.xs,
          run.es,
          viewMin,
          viewMax,
          r.left,
          sc.plotW(),
          sc.px,
          sc.py,
          accent,
          2,
          dpr,
          run.approx ? [5, 3] : undefined,
        );
      }
      // The domain is fixed to the baseline (turning a knob must move the
      // layer, not the axis) — but a live series ENTIRELY outside it would
      // be clipped invisible and read as "the knob did nothing". Flag it.
      let liveLo = Infinity;
      let liveHi = -Infinity;
      for (const p of livePts) {
        for (const v of [p.effect, p.left_bound, p.right_bound]) {
          if (v !== null && Number.isFinite(v)) {
            if (v < liveLo) liveLo = v;
            if (v > liveHi) liveHi = v;
          }
        }
      }
      if (liveLo !== Infinity && (liveLo > vmax || liveHi < vmin)) {
        const above = liveLo > vmax;
        g!.fillStyle = accent;
        g!.font = `${11 * dpr}px ui-monospace, Menlo, Consolas, monospace`;
        g!.textAlign = 'right';
        g!.textBaseline = above ? 'top' : 'bottom';
        g!.fillText(
          `live series off-scale ${above ? '↑' : '↓'} (${fmtSigned(above ? liveLo : liveHi)})`,
          r.right - 8 * dpr,
          above ? r.top + 6 * dpr : r.bottom - 6 * dpr,
        );
      }
    }

    drawVDivider(g!, canvas, MARGINS, dpr, sc.px, dividerEd, grid, 'planned horizon →');
    if (hoverX !== null) drawHover(sc, r.top, sc.plotH());
    g!.restore();
  }

  function nearestBaseIndex(x: number): number {
    let best = -1;
    let bestDist = Infinity;
    for (let i = 0; i < xsB.length; i++) {
      if (Number.isNaN(xsB[i])) continue;
      const d = Math.abs(xsB[i] - x);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    }
    return best;
  }

  function drawHover(sc: Scales, top: number, h: number): void {
    const idx = nearestBaseIndex(hoverX as number);
    if (idx < 0) return;
    const x = xsB[idx];
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
    const lp = livePointAt(base[idx].t);
    const ringY = lp && lp.effect !== null ? lp.effect : esB[idx];
    if (Number.isFinite(ringY)) {
      const Y = sc.py(ringY as number);
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
    updateReadout(idx, lp);
  }

  function livePointAt(endTs: number): ReplyPoint | null {
    if (livePts === null) return null;
    for (const p of livePts) if (p.end_ts === endTs) return p;
    return null;
  }

  function updateReadout(idx: number, lp: ReplyPoint | null): void {
    const p = base[idx];
    let html = `<span class="abk-ro-t">${esc(fmtTs(p.t))} (${p.ed === null ? '—' : fmtVal(p.ed) + 'd'})</span>`;
    const insufficient = lp !== null ? lp.insufficient : p.ins === 1;
    if (insufficient) {
      html +=
        `<span class="abk-ro-flag abk-insufficient">insufficient data — ` +
        `n₁=${p.s1}, n₂=${p.s2} · SRM ${p.blk === 1 ? 'FAILED' : 'ok'}</span>`;
    } else if (lp !== null) {
      html += `<span>live ${lp.effect === null ? '—' : esc(fmtSigned(lp.effect))}`;
      if (lp.left_bound !== null && lp.right_bound !== null) {
        html += ` [${esc(fmtVal(lp.left_bound))}, ${esc(fmtVal(lp.right_bound))}]`;
      }
      html += ` <span class="abk-ro-tier">${esc(lp.tier)}</span></span>`;
      html += `<span>p ${lp.pvalue === null ? '—' : esc(fmtP(lp.pvalue))}</span>`;
      html += `<span>baseline ${p.e === null ? '—' : esc(fmtSigned(p.e))}</span>`;
      html += `<span>n₁=${lp.size_1 ?? p.s1} n₂=${lp.size_2 ?? p.s2}</span>`;
      if (lp.warnings.length > 0) {
        html += `<span class="abk-ro-flag">⚠ ${esc(lp.warnings.join(' · '))}</span>`;
      }
      const flags: string[] = [isPre(p.t) ? 'pre-horizon' : 'at horizon'];
      if (p.blk === 1) flags.push('SRM-blocked');
      html += `<span class="abk-ro-flag">${esc(flags.join(' · '))}</span>`;
    } else {
      html += `<span>effect ${p.e === null ? '—' : esc(fmtSigned(p.e))}`;
      if (p.lo !== null && p.hi !== null) html += ` [${esc(fmtVal(p.lo))}, ${esc(fmtVal(p.hi))}]`;
      html += `</span>`;
      html += `<span>p ${p.p === null ? '—' : esc(fmtP(p.p))}</span>`;
      html += `<span>n₁=${p.s1} n₂=${p.s2}</span>`;
      const flags: string[] = [isPre(p.t) ? 'pre-horizon' : 'at horizon'];
      if (p.blk === 1) flags.push('SRM-blocked');
      html += `<span class="abk-ro-flag">${esc(flags.join(' · '))}</span>`;
      if (livePts !== null) html += `<span class="abk-ro-flag">no live point at this cutoff (reload?)</span>`;
    }
    setReadout(html);
  }

  // interaction (report-identical wheel-zoom / drag-pan / hover) ----------------
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
  const onMove = (e: MouseEvent): void => {
    if (!drag) return;
    const r = canvas.getBoundingClientRect();
    const perPx = (drag.vMax - drag.vMin) / (r.width - (MARGINS.l + MARGINS.r) || 1);
    const d = (e.clientX - drag.x) * perPx;
    setView(drag.vMin - d, drag.vMax - d);
  };
  const onUp = (): void => {
    if (drag) {
      drag = null;
      canvas.style.cursor = 'crosshair';
    }
  };
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
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
      hatch = null; // dpr-dependent tile
      paint();
    },
    setLive(pts: ReplyPoint[] | null): void {
      livePts = pts;
      rebuildLive();
      paint();
    },
    dispose(): void {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    },
  };
}

// ----------------------------------------------------------------------------
// Styling (injected once, scoped under .abk-explore)
// ----------------------------------------------------------------------------

let styleInjected = false;
function injectStyle(): void {
  if (styleInjected) return;
  styleInjected = true;
  // Token block generated from the ONE brand-token layer and declared on
  // :where(:root) (zero specificity) — canvas token() reads and DOM var()
  // resolve through the same node, so a host `:root{--abk-*:…}` override hits
  // both at once (the WP3 split-brain-theming finding).
  const tokenBlock = Object.entries(TOKEN_FALLBACKS)
    .map(([name, value]) => `${name}:${value}`)
    .join(';');
  const css = `
:where(:root){${tokenBlock};
  --abk-sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --abk-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.${ROOT_CLASS}{font-family:var(--abk-sans);color:var(--abk-ink);background:var(--abk-page);}
.${ROOT_CLASS} *{box-sizing:border-box;}
.${ROOT_CLASS} .abk-root{height:100vh;display:flex;flex-direction:column;overflow:hidden;padding:14px 16px 10px;}
/* header ------------------------------------------------------------------ */
.${ROOT_CLASS} .abk-header{flex:none;margin-bottom:8px;padding-left:12px;border-left:3px solid var(--abk-explore-accent);}
.${ROOT_CLASS} .abk-brand{display:flex;align-items:center;gap:8px;margin-bottom:6px;}
.${ROOT_CLASS} .abk-logomark{width:22px;height:22px;border-radius:6px;display:block;}
.${ROOT_CLASS} .abk-wordmark{font:700 14px var(--abk-sans);color:var(--abk-explore-accent);letter-spacing:-0.01em;}
.${ROOT_CLASS} .abk-h-top{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;}
.${ROOT_CLASS} .abk-title{font-size:19px;font-weight:700;margin:0;letter-spacing:-0.01em;}
.${ROOT_CLASS} .abk-badge-page{font-size:10px;font-family:var(--abk-mono);text-transform:uppercase;
  letter-spacing:0.08em;padding:2px 8px;border-radius:8px;border:1px solid var(--abk-border);}
.${ROOT_CLASS} .abk-badge-live{border-color:var(--abk-explore-accent);color:var(--abk-explore-accent);}
.${ROOT_CLASS} .abk-badge-preview{color:var(--abk-muted);border-style:dashed;}
.${ROOT_CLASS} .abk-meta{font-size:11.5px;color:var(--abk-ink-2);font-family:var(--abk-mono);margin-top:2px;}
.${ROOT_CLASS} .abk-desc{margin:6px 0 0;font-size:12.5px;color:var(--abk-ink-2);max-width:820px;line-height:1.45;}
/* warnings / notes ---------------------------------------------------------- */
.${ROOT_CLASS} .abk-warnings{flex:none;margin:4px 0;}
.${ROOT_CLASS} .abk-warning{font-size:12px;color:var(--abk-ink);
  background:color-mix(in srgb, var(--abk-st-warn) 14%, transparent);
  border:1px solid var(--abk-st-warn);border-radius:8px;padding:4px 10px;margin:3px 0;}
.${ROOT_CLASS} .abk-notes{flex:none;}
.${ROOT_CLASS} .abk-note{font-size:11.5px;font-family:var(--abk-mono);border-radius:8px;
  padding:4px 10px;margin:4px 0;border:1px dashed var(--abk-border);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-note.abk-insufficient{background:color-mix(in srgb, var(--abk-muted) 12%, transparent);}
/* cockpit layout ------------------------------------------------------------- */
.${ROOT_CLASS} .abk-cockpit{flex:1;display:flex;gap:12px;min-height:0;}
.${ROOT_CLASS} .abk-stage{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;}
.${ROOT_CLASS} .abk-hud{flex:none;display:flex;flex-wrap:wrap;align-items:center;
  justify-content:space-between;gap:6px 12px;margin-bottom:6px;}
.${ROOT_CLASS} .abk-hud-chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;}
/* chips ---------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;
  background:var(--abk-card);border:1px solid var(--abk-border);border-radius:10px;
  font-size:11.5px;font-family:var(--abk-mono);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-chip-l{color:var(--abk-muted);}
.${ROOT_CLASS} .abk-chip-v{color:var(--abk-ink);font-weight:700;}
.${ROOT_CLASS} .abk-srm-ok{border-color:var(--abk-st-good);}
.${ROOT_CLASS} .abk-srm-fail{background:var(--abk-st-critical);border-color:var(--abk-st-critical);
  color:var(--abk-card);font-weight:700;}
.${ROOT_CLASS} .abk-calibration{border-style:dashed;}
.${ROOT_CLASS} .abk-cal-ok{border-style:solid;border-color:var(--abk-st-good);color:var(--abk-good-text);}
.${ROOT_CLASS} .abk-cal-over{border-style:solid;background:var(--abk-st-critical);
  border-color:var(--abk-st-critical);color:var(--abk-card);font-weight:700;}
.${ROOT_CLASS} .abk-cal-mismatch{border-style:solid;border-color:var(--abk-st-warn);}
.${ROOT_CLASS} .abk-identity{border-color:var(--abk-st-serious);color:var(--abk-st-serious);font-weight:600;}
.${ROOT_CLASS} .abk-tier{color:var(--abk-muted);}
/* modes ----------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-modes{display:flex;gap:4px;}
.${ROOT_CLASS} .abk-mode-btn{font:600 11.5px var(--abk-mono);padding:5px 12px;border-radius:9px;
  border:1px solid var(--abk-border);background:var(--abk-card);color:var(--abk-ink-2);cursor:pointer;}
.${ROOT_CLASS} .abk-mode-btn.on{border-color:var(--abk-explore-accent);color:var(--abk-explore-accent);}
.${ROOT_CLASS} .abk-mode-btn.busy{border-color:var(--abk-explore-accent);color:var(--abk-explore-accent);opacity:0.85;cursor:progress;}
.${ROOT_CLASS} .abk-mode-disabled{opacity:0.55;cursor:default;}
/* legend ----------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-legend{flex:none;display:flex;flex-wrap:wrap;gap:5px 14px;margin-bottom:4px;}
.${ROOT_CLASS} .abk-legend-item{display:inline-flex;align-items:center;gap:5px;font-size:10.5px;
  color:var(--abk-ink-2);font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-swatch{width:14px;height:9px;border-radius:2px;display:inline-block;}
.${ROOT_CLASS} .abk-sw-live{background:var(--abk-series-1);}
.${ROOT_CLASS} .abk-sw-baseline{background:transparent;border-bottom:2px dashed var(--abk-chart-grid);height:5px;border-radius:0;}
.${ROOT_CLASS} .abk-sw-approx{background:repeating-linear-gradient(45deg,transparent,transparent 2px,var(--abk-series-1) 2px,var(--abk-series-1) 3px);}
.${ROOT_CLASS} .abk-sw-prehorizon{background:transparent;border:1px dashed var(--abk-series-1);}
.${ROOT_CLASS} .abk-sw-insufficient{background:color-mix(in srgb, var(--abk-muted) 40%, transparent);}
/* chart + stage foot ------------------------------------------------------------ */
.${ROOT_CLASS} .abk-chart{position:relative;background:var(--abk-chart-bg);
  border:1px solid var(--abk-chart-border);border-radius:12px;overflow:hidden;}
.${ROOT_CLASS} .abk-chart-main{flex:1;min-height:220px;}
.${ROOT_CLASS} .abk-chart canvas{width:100%;height:100%;display:block;}
.${ROOT_CLASS} .abk-chart-fallback{color:var(--abk-chart-ink);font-size:12px;padding:20px;
  font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-spin{display:none;position:absolute;top:10px;right:12px;
  font:600 11px var(--abk-mono);color:var(--abk-chart-ink);background:color-mix(in srgb, var(--abk-chart-bg) 70%, transparent);
  border:1px solid var(--abk-chart-border);border-radius:8px;padding:4px 10px;}
.${ROOT_CLASS} .abk-spin.on{display:inline-flex;}
.${ROOT_CLASS} .abk-stagefoot{flex:none;padding-top:5px;}
.${ROOT_CLASS} .abk-readout{min-height:18px;font-size:11px;color:var(--abk-ink-2);
  font-family:var(--abk-mono);display:flex;flex-wrap:wrap;gap:3px 14px;align-items:center;}
.${ROOT_CLASS} .abk-ro-t{font-weight:700;color:var(--abk-ink);}
.${ROOT_CLASS} .abk-ro-flag{color:var(--abk-muted);}
.${ROOT_CLASS} .abk-ro-tier{color:var(--abk-muted);font-size:10px;text-transform:uppercase;letter-spacing:0.05em;}
.${ROOT_CLASS} .abk-readout .abk-insufficient{color:var(--abk-ink);
  background:color-mix(in srgb, var(--abk-muted) 16%, transparent);border-radius:6px;padding:2px 6px;}
.${ROOT_CLASS} .abk-warnbar .abk-warning{margin:3px 0 0;}
.${ROOT_CLASS} .abk-stat{min-height:16px;margin-top:3px;font-size:11px;color:var(--abk-muted);
  font-family:var(--abk-mono);}
.${ROOT_CLASS} .abk-stat-err{color:var(--abk-st-critical);}
/* rail --------------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-rail{flex:none;width:330px;display:flex;flex-direction:column;min-height:0;
  background:var(--abk-card);border:1px solid var(--abk-border);border-radius:12px;}
.${ROOT_CLASS} .abk-railhead{flex:none;padding:10px 14px 6px;border-bottom:1px solid var(--abk-border);}
.${ROOT_CLASS} .abk-railtitle{font:700 12px var(--abk-mono);letter-spacing:0.05em;text-transform:uppercase;
  color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-controls{flex:1;overflow-y:auto;padding:10px 14px;min-height:0;}
.${ROOT_CLASS} .abk-rail-off{opacity:0.6;pointer-events:none;}
.${ROOT_CLASS} .abk-rail-group{margin-bottom:6px;}
.${ROOT_CLASS} .abk-ctl{margin:0 0 12px;}
.${ROOT_CLASS} .abk-ctl-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;}
.${ROOT_CLASS} .abk-ctl-label{font-size:12px;color:var(--abk-ink);display:flex;align-items:baseline;
  flex-wrap:wrap;gap:6px;margin-bottom:4px;}
.${ROOT_CLASS} .abk-hint{color:var(--abk-muted);font-size:10px;cursor:help;}
.${ROOT_CLASS} .abk-knob-badge{font:600 9.5px var(--abk-mono);padding:1px 6px;border-radius:7px;
  border:1px solid var(--abk-border);color:var(--abk-muted);cursor:help;}
.${ROOT_CLASS} .abk-badge-identity{border-color:var(--abk-st-serious);color:var(--abk-st-serious);}
.${ROOT_CLASS} .abk-badge-reload{border-color:var(--abk-series-1);color:var(--abk-series-1);}
.${ROOT_CLASS} .abk-ctl-val{font:600 11.5px var(--abk-mono);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-seg{display:flex;flex-wrap:wrap;gap:4px;}
.${ROOT_CLASS} .abk-seg-btn{font:600 11px var(--abk-mono);padding:4px 9px;border-radius:8px;
  border:1px solid var(--abk-border);background:var(--abk-page);color:var(--abk-ink-2);cursor:pointer;}
.${ROOT_CLASS} .abk-seg-btn.on{border-color:var(--abk-explore-accent);color:var(--abk-explore-accent);
  background:color-mix(in srgb, var(--abk-explore-accent) 8%, transparent);}
.${ROOT_CLASS} .abk-range{width:100%;accent-color:var(--abk-explore-accent);}
.${ROOT_CLASS} .abk-num,.${ROOT_CLASS} .abk-text{width:100%;font:12px var(--abk-mono);padding:5px 8px;
  border:1px solid var(--abk-border);border-radius:8px;background:var(--abk-page);color:var(--abk-ink);}
.${ROOT_CLASS} .abk-check{display:flex;align-items:center;gap:8px;font-size:12px;cursor:pointer;}
.${ROOT_CLASS} .abk-check input{accent-color:var(--abk-explore-accent);}
.${ROOT_CLASS} .abk-ctl-note{font-size:10.5px;color:var(--abk-muted);font-family:var(--abk-mono);
  margin:0 0 10px;line-height:1.5;}
.${ROOT_CLASS} .abk-advanced{margin-top:2px;}
.${ROOT_CLASS} .abk-advanced summary{font:600 11.5px var(--abk-mono);color:var(--abk-ink-2);
  cursor:pointer;margin-bottom:8px;}
/* review ---------------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-review-row{border:1px solid var(--abk-border);border-radius:9px;
  padding:8px 10px;margin-bottom:8px;}
.${ROOT_CLASS} .abk-review-name{font:700 12px var(--abk-mono);margin-bottom:5px;}
.${ROOT_CLASS} .abk-review-checks{display:flex;gap:16px;}
.${ROOT_CLASS} .abk-review-checks .abk-ctl{margin:0;}
.${ROOT_CLASS} .abk-review-verdict{margin-top:5px;font:600 10.5px var(--abk-mono);color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-verdict-win{color:var(--abk-good-text);}
.${ROOT_CLASS} .abk-verdict-lose{color:var(--abk-st-critical);}
/* rail foot ---------------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-railfoot{flex:none;border-top:1px solid var(--abk-border);padding:10px 14px;}
.${ROOT_CLASS} .abk-cfg-toggle{font:600 10.5px var(--abk-mono);color:var(--abk-muted);cursor:pointer;
  margin-bottom:6px;}
.${ROOT_CLASS} .abk-cfg-echo{font:10.5px var(--abk-mono);color:var(--abk-ink-2);
  overflow-wrap:anywhere;margin-bottom:8px;line-height:1.5;}
.${ROOT_CLASS} .abk-btn{font:700 12px var(--abk-sans);padding:8px 14px;border-radius:9px;cursor:pointer;
  border:1px solid var(--abk-border);background:var(--abk-page);color:var(--abk-ink);}
.${ROOT_CLASS} .abk-btn:disabled{opacity:0.5;cursor:default;}
.${ROOT_CLASS} .abk-btn-apply{width:100%;background:var(--abk-explore-accent);
  border-color:var(--abk-explore-accent);color:var(--abk-card);}
.${ROOT_CLASS} .abk-btn-danger{background:var(--abk-st-critical);border-color:var(--abk-st-critical);
  color:var(--abk-card);}
.${ROOT_CLASS} .abk-btn-ghost{background:transparent;}
.${ROOT_CLASS} .abk-btn-reload{border-color:var(--abk-series-1);color:var(--abk-series-1);}
.${ROOT_CLASS} .abk-apply-msg{margin-top:8px;font-size:11.5px;line-height:1.5;overflow-wrap:anywhere;}
.${ROOT_CLASS} .abk-apply-msg.ok{color:var(--abk-good-text);}
.${ROOT_CLASS} .abk-apply-msg.err{color:var(--abk-st-critical);}
.${ROOT_CLASS} .abk-apply-msg.info{color:var(--abk-ink-2);}
.${ROOT_CLASS} .abk-orphan{color:var(--abk-ink);margin-top:4px;
  background:color-mix(in srgb, var(--abk-st-warn) 16%, transparent);border-radius:6px;padding:4px 8px;}
.${ROOT_CLASS} .abk-confirm{margin-top:8px;border:1px solid var(--abk-st-warn);border-radius:9px;
  padding:8px 10px;background:color-mix(in srgb, var(--abk-st-warn) 10%, transparent);}
.${ROOT_CLASS} .abk-confirm-text{font-size:11.5px;line-height:1.5;margin-bottom:8px;}
.${ROOT_CLASS} .abk-confirm-btns{display:flex;gap:8px;}
.${ROOT_CLASS} .abk-reloadbar{border:1px solid var(--abk-series-1);border-radius:9px;
  padding:8px 10px;margin-bottom:8px;background:color-mix(in srgb, var(--abk-series-1) 8%, transparent);}
.${ROOT_CLASS} .abk-reloadbar-text{font-size:11.5px;line-height:1.5;margin-bottom:8px;}
.${ROOT_CLASS} .abk-reloadbar-btns{display:flex;gap:8px;}
.${ROOT_CLASS} .abk-preview-note{font-size:11px;color:var(--abk-muted);font-family:var(--abk-mono);
  line-height:1.5;margin-bottom:8px;}
/* empty states ---------------------------------------------------------------------------- */
.${ROOT_CLASS} .abk-empty{font-size:13px;color:var(--abk-ink-2);background:var(--abk-card);
  border:1px dashed var(--abk-border);border-radius:10px;padding:14px;}
/* responsive fallback ----------------------------------------------------------------------- */
@media (max-width: 900px){
  .${ROOT_CLASS} .abk-root{height:auto;overflow:visible;}
  .${ROOT_CLASS} .abk-cockpit{flex-direction:column;}
  .${ROOT_CLASS} .abk-chart-main{height:54vh;flex:none;}
  .${ROOT_CLASS} .abk-rail{width:100%;}
  .${ROOT_CLASS} .abk-controls{max-height:50vh;}
}
`;
  const style = document.createElement('style');
  style.setAttribute('data-abk-explore', '');
  style.textContent = css;
  document.head.appendChild(style);
}

// ----------------------------------------------------------------------------
// Global entry (the only public surface — no ESM exports)
// ----------------------------------------------------------------------------

(window as unknown as { __ABK_EXPLORE__: { render: typeof render } }).__ABK_EXPLORE__ = { render };
