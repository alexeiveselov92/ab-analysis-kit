// Deterministic synthetic A/B experiment generator for the LIVE landing demo.
//
// A visitor dials in "an experiment like theirs" — a true effect, a noise level,
// a daily traffic — and we fabricate a per-day accrual of two arms (control vs
// treatment) as sufficient statistics. This is the LIVE-interactivity path only:
// it is NOT parity-checked (like the donor's synth.ts). The frozen parity gate
// covers the math that CONSUMES this output — `stats.ts` (merge + effect) — which
// the live path also drives, so what a visitor watches is exactly what is proven.
//
// Everything here is pure and deterministic: the same `SynthOptions` (including
// `seed`) produce byte-identical output. No Math.random, no Date.now.

import type { DayIncrement, Suffstats, SynthOptions } from './types';

/** mulberry32: a tiny, well-distributed 32-bit seeded PRNG → [0, 1). */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4_294_967_296;
  };
}

/** Standard-normal sample via Box-Muller (u1 drawn in (0, 1] to avoid log(0)). */
function gaussian(rand: () => number): number {
  const u1 = 1 - rand();
  const u2 = rand();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

/**
 * Reduce one day's per-unit draws to abkit `SufficientStats` (n, mean, m2). The
 * two-pass centered second moment `m2 = Σ(y − ȳ)²` mirrors
 * `SufficientStats.from_sample` (`np.var(y) * n`); exact numpy summation order is
 * not required here because this feeds only the live path, not the parity gate.
 */
function suffstatsOf(values: number[]): Suffstats {
  const n = values.length;
  let sum = 0;
  for (const v of values) sum += v;
  const mean = sum / n;
  let m2 = 0;
  for (const v of values) {
    const d = v - mean;
    m2 += d * d;
  }
  return { n, mean, m2 };
}

/**
 * Build a per-day accrual of two arms. Control ~ Normal(baseMean, sigma);
 * treatment ~ Normal(baseMean + trueEffect, sigma). Cumulative N grows linearly
 * with the day, so the CI band tightens ∝ 1/√N — the convergence the demo is
 * built to show.
 */
export function generateExperiment(opts: SynthOptions): DayIncrement[] {
  const rand = mulberry32(opts.seed);
  const days: DayIncrement[] = [];
  for (let d = 1; d <= opts.days; d++) {
    const control: number[] = [];
    const treatment: number[] = [];
    for (let u = 0; u < opts.unitsPerArmPerDay; u++) {
      control.push(opts.baseMean + gaussian(rand) * opts.sigma);
      treatment.push(opts.baseMean + opts.trueEffect + gaussian(rand) * opts.sigma);
    }
    days.push({ ed: d, control: suffstatsOf(control), treatment: suffstatsOf(treatment) });
  }
  return days;
}
