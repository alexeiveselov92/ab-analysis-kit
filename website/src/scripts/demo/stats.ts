// The parity-checked statistical core of the abkit stabilization demo.
//
// This file is a faithful TypeScript port of TWO abkit.stats formulas — nothing
// else. It has NO DOM / canvas dependency (so `check-demo-parity.mjs` can bundle
// it standalone with esbuild and import `runCumulative` in Node):
//
//   1. `mergeSuffstats`  ≡  abkit/stats/accumulate.py `merge_suffstats`
//      (Chan's parallel/Welford update on centered moments — same operation
//      order, so cumulative folds match abkit to the ULP).
//   2. `absoluteEffect`  ≡  abkit/stats/parametric/ttest.py (test_type="absolute")
//      + effects.py `absolute_effect` / `normal_test`: the "t-test" whose
//      statistic is actually the large-sample NORMAL on the mean difference
//      (legacy parity). Population variance (ddof=0): var = m2 / n.
//
// `check-demo-parity.mjs` feeds the frozen daily suffstats from golden.json to
// `runCumulative` and asserts every per-day (effect, lo, hi, p, reject) matches
// the real abkit.stats output within 1e-6. The live landing path (main.ts +
// synth.ts) drives the SAME functions, so what ships is exactly what is proven.

import type { CumulativePoint, DayIncrement, RunConfig, Suffstats, Verdict } from './types';

// ----------------------------------------------------------------------------
// Normal-distribution helpers
// ----------------------------------------------------------------------------

/**
 * scipy.stats.norm.ppf(0.975) to full double precision. The demo alpha is fixed
 * at 0.05, so the CI bounds match abkit's `norm(...).ppf` to the ULP. A ±1-ULP
 * disagreement in this constant would move a bound by ~2e-16·scale — five orders
 * of magnitude inside the 1e-6 parity gate.
 */
const Z_0975 = 1.959963984540054;

/**
 * The two-sided critical z for a per-comparison `alpha`. Exact for 0.05 (the
 * demo alpha); other alphas use Peter Acklam's inverse-normal-CDF approximation
 * (|relerror| < 1.15e-9 — far inside the 1e-6 gate).
 */
export function zForAlpha(alpha: number): number {
  if (Math.abs(alpha - 0.05) < 1e-12) return Z_0975;
  return invNormCdf(1 - alpha / 2);
}

/** Peter Acklam's inverse standard-normal CDF; |relerror| < 1.15e-9. */
function invNormCdf(p: number): number {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  const a = [
    -3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2, 1.38357751867269e2,
    -3.066479806614716e1, 2.506628277459239e0,
  ];
  const b = [
    -5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2, 6.680131188771972e1,
    -1.328068155288572e1,
  ];
  const c = [
    -7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838e0, -2.549732539343734e0,
    4.374664141464968e0, 2.938163982698783e0,
  ];
  const d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996e0, 3.754408661907416e0];
  const plow = 0.02425;
  const phigh = 1 - plow;
  let q: number;
  let r: number;
  if (p < plow) {
    q = Math.sqrt(-2 * Math.log(p));
    return (
      (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    );
  }
  if (p <= phigh) {
    q = p - 0.5;
    r = q * q;
    return (
      ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) /
      (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    );
  }
  q = Math.sqrt(-2 * Math.log(1 - p));
  return -(
    (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
    ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
  );
}

/**
 * Complementary error function (Numerical Recipes rational Chebyshev fit);
 * fractional error < 1.2e-7 everywhere — inside the 1e-6 gate with ~8× margin.
 */
function erfc(x: number): number {
  const z = Math.abs(x);
  const t = 1 / (1 + z / 2);
  const ans =
    t *
    Math.exp(
      -z * z -
        1.26551223 +
        t *
          (1.00002368 +
            t *
              (0.37409196 +
                t *
                  (0.09678418 +
                    t *
                      (-0.18628806 +
                        t *
                          (0.27886807 +
                            t *
                              (-1.13520398 +
                                t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))),
    );
  return x >= 0 ? ans : 2 - ans;
}

/** Standard-normal CDF Φ(x). */
function normCdf(x: number): number {
  return 0.5 * erfc(-x / Math.SQRT2);
}

// ----------------------------------------------------------------------------
// Sufficient-statistics merge (abkit/stats/accumulate.py parity)
// ----------------------------------------------------------------------------

/**
 * Merge two disjoint per-unit populations of ONE variant. Byte-for-byte the same
 * arithmetic as abkit's `merge_suffstats` (Chan's parallel update): merged n &
 * mean, then the centered second moment via the mean-delta correction. Never the
 * catastrophic Σx²/n − x̄² form.
 */
export function mergeSuffstats(a: Suffstats, b: Suffstats): Suffstats {
  const n = a.n + b.n;
  const delta = b.mean - a.mean;
  const weight = (a.n * b.n) / n;
  const mean = a.mean + (delta * b.n) / n;
  const m2 = a.m2 + b.m2 + delta * delta * weight;
  return { n, mean, m2 };
}

// ----------------------------------------------------------------------------
// The absolute two-sample effect + Normal CI (abkit t-test, test_type="absolute")
// ----------------------------------------------------------------------------

/** The estimated effect + interval for one cumulative pair. */
export interface EffectResult {
  effect: number;
  lo: number | null;
  hi: number | null;
  p: number | null;
  reject: boolean;
  scored: boolean;
}

/**
 * Absolute effect (mean_2 − mean_1) with a Normal CI at `alpha` — the closed-form
 * path abkit's `t-test` takes for `test_type="absolute"`:
 *
 *   var_i        = m2_i / n_i                      (population, ddof=0 baseline)
 *   var(effect)  = var_1 / n_1 + var_2 / n_2
 *   [lo, hi]     = effect ∓ z · sqrt(var(effect))
 *   p            = 2 · min(Φ(0), 1 − Φ(0))  on Normal(effect, sqrt(var))
 *
 * `reject` is derived from the BOUNDS (lo > 0 || hi < 0), which is algebraically
 * identical to abkit's `pvalue < alpha` for a symmetric Normal CI at the same
 * critical z — so the decision flag can never flip on erfc round-off. Degenerate
 * effect variance (≤ 0, e.g. a day-1 single-unit arm) returns `scored: false`
 * with null bounds, matching abkit's `normal_test` NaN path.
 */
export function absoluteEffect(control: Suffstats, treatment: Suffstats, alpha: number): EffectResult {
  const var1 = control.m2 / control.n;
  const var2 = treatment.m2 / treatment.n;
  const effect = treatment.mean - control.mean;
  const varEff = var1 / control.n + var2 / treatment.n;

  if (!Number.isFinite(effect) || !Number.isFinite(varEff) || varEff <= 0) {
    return { effect, lo: null, hi: null, p: null, reject: false, scored: false };
  }

  const scale = Math.sqrt(varEff);
  const z = zForAlpha(alpha);
  const lo = effect - z * scale;
  const hi = effect + z * scale;
  const cdf0 = normCdf((0 - effect) / scale);
  const p = 2 * Math.min(cdf0, 1 - cdf0);
  const reject = lo > 0 || hi < 0;
  return { effect, lo, hi, p, reject, scored: true };
}

// ----------------------------------------------------------------------------
// The cumulative run (the thing the parity gate calls)
// ----------------------------------------------------------------------------

/**
 * Fold daily per-arm increments into one cumulative readout point per day. This
 * is the exact shape `check-demo-parity.mjs` exercises: the same left-fold merge
 * order and the same effect math abkit uses cutoff-by-cutoff.
 */
export function runCumulative(days: DayIncrement[], config: RunConfig): CumulativePoint[] {
  let cumC: Suffstats | null = null;
  let cumT: Suffstats | null = null;
  const out: CumulativePoint[] = [];
  for (const day of days) {
    cumC = cumC === null ? day.control : mergeSuffstats(cumC, day.control);
    cumT = cumT === null ? day.treatment : mergeSuffstats(cumT, day.treatment);
    const r = absoluteEffect(cumC, cumT, config.alpha);
    out.push({
      ed: day.ed,
      n1: cumC.n,
      n2: cumT.n,
      effect: r.effect,
      lo: r.lo,
      hi: r.hi,
      p: r.p,
      reject: r.reject,
      scored: r.scored,
    });
  }
  return out;
}

// ----------------------------------------------------------------------------
// Verdict (the readout headline — brand WIN / LOSE / FLAT / INCONCLUSIVE)
// ----------------------------------------------------------------------------

/**
 * The decision at the latest revealed day — honest to "call it once it
 * stabilizes": WIN/LOSE/FLAT are only decision-grade AT OR AFTER the horizon;
 * before it (or on a degenerate point) the experiment is still converging, so it
 * reads INCONCLUSIVE. Post-horizon: a rejected CI is WIN (lift) or LOSE (harm);
 * a null CI is FLAT when powered (or when no power gate is set) and INCONCLUSIVE
 * when still too wide.
 */
export function classifyVerdict(latest: CumulativePoint | null, config: RunConfig): Verdict {
  if (!latest || !latest.scored) return 'inconclusive';
  if (latest.ed < config.horizonDay) return 'inconclusive';
  if (latest.reject) return latest.effect > 0 ? 'win' : 'lose';
  if (
    config.flatCiLength != null &&
    latest.lo != null &&
    latest.hi != null &&
    latest.hi - latest.lo > config.flatCiLength
  ) {
    return 'inconclusive';
  }
  return 'flat';
}
