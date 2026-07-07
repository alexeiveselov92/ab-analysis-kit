// Entry point for the interactive landing "stabilization" demo.
//
// A visitor dials in an experiment (true effect / noise / daily traffic), and we
// fabricate it (synth.ts), fold it into a cumulative effect + CI per day
// (stats.ts — the parity-checked core), paint the signature chart (chart.ts,
// which composes the shared abkit-web canvas), and fill the readout scorecard.
// Everything is client-side: the VPS only ever serves the static bundle.
//
// DOM CONTRACT — a container `#abk-demo` holding:
//   canvas   #abk-demo-canvas
//   ranges   #abk-demo-effect   (true effect, thousandths: -40..40 → ∓0.040)
//            #abk-demo-noise    (per-unit σ, hundredths: 10..40 → 0.10..0.40)
//            #abk-demo-traffic  (units per arm per day: 20..80)
//   echoes   #abk-demo-effect-val #abk-demo-noise-val #abk-demo-traffic-val
//   buttons  #abk-demo-reseed  #abk-demo-replay
//   outputs  #abk-demo-verdict (gets class abk-v-{win|lose|flat|inconclusive})
//            #abk-demo-headline #abk-demo-ci #abk-demo-p #abk-demo-n
//            #abk-demo-day #abk-demo-config
// Any missing element is tolerated (each write is a no-op), and the whole demo
// no-ops if `#abk-demo` / the canvas is absent — so the script is safe to ship
// site-wide even on pages without the block.

import { createDemoChart } from './chart';
import { classifyVerdict, runCumulative } from './stats';
import { generateExperiment } from './synth';
import type { CumulativePoint, RunConfig, SynthOptions, Verdict } from './types';
import { fmtP, fmtSigned, fmtVal } from '../../../../web/src/shared/chart';

const DAYS = 28;
const HORIZON = 14;
const BASE_MEAN = 0.5;

const CONFIG: RunConfig = {
  alpha: 0.05,
  horizonDay: HORIZON,
  // Post-horizon, a null wider than this reads INCONCLUSIVE (still underpowered)
  // rather than FLAT — ~2× a 0.010 practically-meaningful absolute effect.
  flatCiLength: 0.02,
};

const VERDICT_LABEL: Record<Verdict, string> = {
  win: 'WIN',
  lose: 'LOSE',
  flat: 'FLAT',
  inconclusive: 'INCONCLUSIVE',
};

function init(): void {
  const root = document.getElementById('abk-demo');
  if (!root) return;
  const canvas = root.querySelector<HTMLCanvasElement>('#abk-demo-canvas');
  if (!canvas) return;

  const chart = createDemoChart(canvas);
  let seed = 0x51a2b3;

  // ---- helpers ---------------------------------------------------------------
  const num = (id: string, fallback: number): number => {
    const el = root.querySelector<HTMLInputElement>(`#${id}`);
    const v = el ? Number(el.value) : NaN;
    return Number.isFinite(v) ? v : fallback;
  };
  const out = (id: string, text: string): void => {
    const el = root.querySelector<HTMLElement>(`#${id}`);
    if (el) el.textContent = text;
  };

  const readSynth = (): SynthOptions => ({
    days: DAYS,
    unitsPerArmPerDay: Math.round(num('abk-demo-traffic', 45)),
    baseMean: BASE_MEAN,
    trueEffect: num('abk-demo-effect', 20) / 1000,
    sigma: num('abk-demo-noise', 20) / 100,
    seed,
  });

  // ---- scorecard -------------------------------------------------------------
  const setVerdict = (v: Verdict): void => {
    const el = root.querySelector<HTMLElement>('#abk-demo-verdict');
    if (!el) return;
    el.textContent = VERDICT_LABEL[v];
    el.className = `abk-demo-verdict abk-v-${v}`;
  };

  const scorecard = (latest: CumulativePoint | undefined): void => {
    if (!latest) return;
    setVerdict(classifyVerdict(latest, CONFIG));
    out('abk-demo-headline', fmtSigned(latest.effect));
    out(
      'abk-demo-ci',
      latest.scored && latest.lo != null && latest.hi != null
        ? `[${fmtVal(latest.lo)}, ${fmtVal(latest.hi)}]`
        : '—',
    );
    out('abk-demo-p', latest.scored && latest.p != null ? `p ${fmtP(latest.p)}` : 'p —');
    out('abk-demo-n', `${latest.n1.toLocaleString()} vs ${latest.n2.toLocaleString()}`);
    out('abk-demo-day', `day ${latest.ed} / horizon ${CONFIG.horizonDay}`);
  };

  const echoConfig = (): void => {
    out('abk-demo-config', `t-test · absolute · alpha=${CONFIG.alpha} · horizon=${CONFIG.horizonDay}d`);
  };

  // ---- reveal (watch it converge) --------------------------------------------
  let revealRAF = 0;
  const prefersReduced =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const reveal = (points: CumulativePoint[]): void => {
    cancelAnimationFrame(revealRAF);
    const n = points.length;
    if (n === 0) return;
    if (prefersReduced || n <= 2) {
      chart.render({ points, config: CONFIG });
      scorecard(points[n - 1]);
      return;
    }
    const DUR = 1600;
    const start = performance.now();
    const step = (now: number): void => {
      const tt = Math.min(1, (now - start) / DUR);
      const eased = 1 - Math.pow(1 - tt, 3);
      const k = Math.max(2, Math.round(2 + eased * (n - 2)));
      const shown = points.slice(0, k);
      // fix the frame to the FULL series so the band visibly shrinks inside it
      chart.render({ points: shown, config: CONFIG, domainPoints: points });
      scorecard(shown[shown.length - 1]);
      if (tt < 1) revealRAF = requestAnimationFrame(step);
    };
    revealRAF = requestAnimationFrame(step);
  };

  const compute = (): CumulativePoint[] => runCumulative(generateExperiment(readSynth()), CONFIG);

  const renderStatic = (): void => {
    cancelAnimationFrame(revealRAF);
    const points = compute();
    chart.render({ points, config: CONFIG });
    scorecard(points[points.length - 1]);
  };

  // ---- wire controls ---------------------------------------------------------
  const ranges: Array<[string, string, (v: number) => string]> = [
    ['abk-demo-effect', 'abk-demo-effect-val', (v) => fmtSigned(v / 1000)],
    ['abk-demo-noise', 'abk-demo-noise-val', (v) => (v / 100).toFixed(2)],
    ['abk-demo-traffic', 'abk-demo-traffic-val', (v) => `${Math.round(v)}/day`],
  ];

  let queued = false;
  for (const [id, valId, fmt] of ranges) {
    const el = root.querySelector<HTMLInputElement>(`#${id}`);
    el?.addEventListener('input', () => {
      out(valId, fmt(Number(el.value)));
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        renderStatic();
      });
    });
  }

  root.querySelector<HTMLButtonElement>('#abk-demo-reseed')?.addEventListener('click', () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    reveal(compute());
  });
  root.querySelector<HTMLButtonElement>('#abk-demo-replay')?.addEventListener('click', () => {
    reveal(compute());
  });

  window.addEventListener('resize', () => chart.resize());

  // ---- first paint -----------------------------------------------------------
  for (const [id, valId, fmt] of ranges) {
    const el = root.querySelector<HTMLInputElement>(`#${id}`);
    if (el) out(valId, fmt(Number(el.value)));
  }
  echoConfig();
  reveal(compute());
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init, { once: true });
} else {
  init();
}
