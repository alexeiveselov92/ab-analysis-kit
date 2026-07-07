// Shared types for the abkit stabilization demo (website/src/scripts/demo/*).
//
// The landing demo re-implements abkit's cumulative-effect computation in
// TypeScript: per-day sufficient statistics are folded with abkit's Chan/Welford
// merge (abkit/stats/accumulate.py `merge_suffstats`) and reduced to an absolute
// two-sample effect + Normal confidence interval (abkit/stats/parametric/ttest.py
// + effects.py `normal_test`). `website/scripts/gen-demo-golden.py` freezes the
// REAL abkit.stats output; `website/scripts/check-demo-parity.mjs` bundles
// `stats.ts` and asserts this port reproduces every frozen point within 1e-6.
//
// Two vocabularies never mix here: `Suffstats`/`DayIncrement` are the INPUT the
// port folds; `CumulativePoint` is the per-day OUTPUT the chart draws and the
// parity gate checks.

/** One variant's sufficient statistics — abkit `SufficientStats` (n, mean, m2). */
export interface Suffstats {
  /** unit count for this variant */
  n: number;
  /** arithmetic mean of the metric */
  mean: number;
  /** raw centered second moment Σ(y − ȳ)²  (population var = m2 / n, ddof=0 baseline) */
  m2: number;
}

/** One day's per-arm increment on the accrual grid (one point per day). */
export interface DayIncrement {
  /** elapsed days since launch (the x-axis) */
  ed: number;
  /** control (first) arm increment */
  control: Suffstats;
  /** treatment (second) arm increment */
  treatment: Suffstats;
}

/** A per-day CUMULATIVE readout point — the chart series AND the parity record. */
export interface CumulativePoint {
  /** elapsed days (x) */
  ed: number;
  /** cumulative control units */
  n1: number;
  /** cumulative treatment units */
  n2: number;
  /** absolute effect: mean_2 − mean_1 (treatment minus control). Always finite. */
  effect: number;
  /** left CI bound; null ⇒ degenerate (effect variance ≤ 0), no interval */
  lo: number | null;
  /** right CI bound; null ⇒ degenerate */
  hi: number | null;
  /** two-sided p-value; null ⇒ degenerate */
  p: number | null;
  /** CI excludes zero at the effective alpha (equivalently p < alpha) */
  reject: boolean;
  /** false ⇒ variance ≤ 0 (degenerate); the band is not drawn for this point */
  scored: boolean;
}

/** Decision config for a cumulative run. */
export interface RunConfig {
  /** effective (post-correction) per-comparison alpha — 0.05 for the demo */
  alpha: number;
  /** decision horizon (elapsed day); WIN/LOSE/FLAT are decision-grade at/after it */
  horizonDay: number;
  /**
   * Optional power gate: post-horizon, a null result (CI includes zero) whose CI
   * is WIDER than this reads INCONCLUSIVE (underpowered / still converging)
   * rather than FLAT. Omit to always call a post-horizon null FLAT.
   */
  flatCiLength?: number;
}

/** The four abkit verdicts (docs/design/brand-tokens.md). */
export type Verdict = 'win' | 'lose' | 'flat' | 'inconclusive';

/** Live-demo synthetic-experiment knobs (client-side only; NOT parity-checked). */
export interface SynthOptions {
  /** number of daily reports on the accrual grid */
  days: number;
  /** new randomization units per arm per day */
  unitsPerArmPerDay: number;
  /** control-arm baseline mean */
  baseMean: number;
  /** true ABSOLUTE lift applied to the treatment arm */
  trueEffect: number;
  /** per-unit noise standard deviation */
  sigma: number;
  /** PRNG seed (deterministic output) */
  seed: number;
}
