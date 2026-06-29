# Statistics: deliberate changes vs the baseline

> Companion to [statistics-baseline.md](statistics-baseline.md). The baseline is the
> legacy math captured verbatim. This document is the **changelog of intent**: every
> way the new engine deviates from or extends it. The discipline is *process*, not
> frozen output: **never change a number silently.** Each change is an
> `ALGORITHM_VERSION` bump + an entry here + an A/A-validated justification.
>
> We are **not** bound to the legacy production numbers (storage is greenfield); the
> baseline is a *reference point* we reproduce first (golden-tested against the
> legacy *engine*) so that every later improvement is measured against a known
> anchor.

## 0. The process: capture → reproduce → blind-rederive → synthesize

Per the project intent, improving the math is a deliberate, measurable loop:

1. **Capture** the legacy algorithm verbatim (done — baseline + catalogue).
2. **Reproduce** it in `abkit.stats` and golden-test against the legacy engine
   (the same `Sample` inputs through the old Python) to a relative tolerance
   (§1.1). This proves we captured it before touching it.
3. **Blind-rederive** each estimand from first principles with **no sight of the
   legacy code** (a separate agent/author task), producing an independent "what the
   textbook-correct method should be".
4. **Synthesize & arbitrate** the legacy vs blind versions with the **A/A
   false-positive matrix** ([aa-false-positive-matrix.md](aa-false-positive-matrix.md)):
   whichever has empirical FPR closest to nominal α (and better power / CI coverage
   on held-out splits) wins. The loser is kept as a version-bumped alternative.
5. **Record** the chosen default + rationale here.

This is how we honor "my old implementation is probably good, but not certainly
optimal" without guessing.

## 1. Corrections to the baseline spec itself

### 1.1 Golden-test tolerance: **relative 1e-9**, not exact equality
Routing through `SufficientStats` (`var = Σx²/n − x̄²`, or Welford) cannot be
**bit-identical** to the legacy `np.var` over raw arrays on large-revenue sums.
Decision: declare "reproduced" at **relative 1e-9** with a documented justification,
use a **two-pass / Welford** variance (not `Σx²/n − x̄²`) in accumulation, and ship
a **heavy-tailed revenue golden fixture** proving the chosen path matches `np.var`.

### 1.2 The mixed-ddof convention is real (baseline fact #1, corrected)
The legacy uses `np.var`/`np.std` with `ddof=0` **but** `np.cov` (θ and the paired/
CUPED covariance terms) with numpy's default `ddof=1`. The engine encodes the
**exact per-term `ddof`** in the `SufficientStats` co-moment formulas, with a golden
test on **θ itself**. A blanket-`ddof` rewrite is forbidden (it fails every
CUPED/paired golden test). A uniform-ddof variant is offered only as a v2 version bump.

## 2. Engine hygiene (version-bumped fixes, A/A-arbitrated, never silent)

These are real legacy issues (catalogued in
[../reference/legacy-method-catalogue.md](../reference/legacy-method-catalogue.md));
each ships as a documented, opt-in-or-version-bumped correction so defaults stay
baseline-faithful until A/A proves the fix helps.

| # | Legacy issue | Change |
|---|---|---|
| H1 | Global `np.random.seed` mutates process-wide state (non-reentrant, breaks parallelism) | Thread `np.random.default_rng(seed)` Generators; paired bootstrap shares one explicit Generator. Process-safe (enables the concurrency model). |
| H2 | Bootstrap CIs non-deterministic run-to-run; seed excluded from identity ⇒ an idempotent re-write silently changes published CIs (can flip `reject`) | **Deterministic per-row seed** from `(exp, metric, name_1, name_2, end_date, n_samples)`; seed excluded from `method_config_id` for **all** bootstrap methods. Golden test: two runs over the same window → byte-identical rows. |
| H3 | `np.apply_along_axis(mean,…)` loops in Python — the dominant bootstrap cost | Fast path `matrix.mean(axis=1)`; `apply_along_axis` only for arbitrary callables. |
| H4 | Bootstrap p-value can be exactly 0 (no smoothing); ties at 0 uncounted | `(#extreme + 1)/(n + 1)` plug-in; documented tie convention. |
| H5 | Relative effect divides by control mean with no guard (inf/NaN on sparse metrics) | Small-denominator guard → warn/NaN, surfaced in the report. |
| H6 | Stratified weight rounding (`int`, `max(1,…)`) changes total N | Largest-remainder (Hamilton) apportionment so per-stratum counts sum exactly. |
| H7 | Poisson bootstrap is only correct for the **mean** but accepts any `stat_func` | Assert/validate `stat == mean`; document; (weighted quantiles are a separate future method). |
| H8 | KS normality check uses estimated params (uncalibrated p) | Drop the per-comparison KS warning or use a calibrated test (Lilliefors). Diagnostic only. |
| H9 | `'effect'` means different things across methods (point estimate vs `mean(boot)`) | Standardize: `effect` = original-sample point estimate everywhere; bootstrap mean reported separately as a bias diagnostic. |
| H10 | Bootstrap value-matrix OOMs at scale (`n_samples × sample_size`, doubled with covariate) | Default/auto-select the **Poisson** engine above a unit threshold; stream resampling in replicate blocks under a memory cap; pre-flight memory estimate in `plan`/`run`. (See [cumulative-intervals.md](cumulative-intervals.md) §5.8.) |

## 3. Quarantined / broken legacy methods (do **not** silently substitute)

The extraction + quorum confirmed three post-normed classes are broken or
mislabeled. Policy: **reproduce only if an experiment actually used them** (check
the historical method usage); otherwise raise a hard error + an entry here. Never
silently map them to the principled `ratio_delta`.

- **`PoissonPostNormedBootstrap`** — verified a verbatim copy of `PoissonBootstrap`
  (no post-norming at all). Either implement real covariate-ratio post-norming with
  Poisson weights, or **remove**. A regression test must assert it differs from
  `PoissonBootstrap`.
- **`PairedPostNormedBootstrap` (relative)** — verified it z-score-standardises then
  takes a ratio of ~zero-centered values (denominator ≈ 0 ⇒ explodes). The relative
  branch is **dropped** (hard error + entry); not reproduced.
- **`PostNormedBootstrap` (absolute)** — `S2 − (S2_cov/S1_cov)·S1` is an unusual
  estimand. Reproduce verbatim under `ALGORITHM_VERSION=1` only where used; offer the
  principled `ratio_delta` as the v2 default. **Known-answer test:** `ratio_delta`
  reduces to `S2 − S1` when the covariate ratio = 1.

Also flagged (reproduce-for-parity, document the asymmetry):
- **`ZTest` relative** lacks the delta-method covariance term the t-test has (it
  naively divides `std_effect` by `prop_1`). Since z-test is the default for fraction
  & main metrics, document it and offer a delta-consistent z-test relative as a
  version bump **if** the A/A matrix shows under-coverage.
- **`TTest` family uses Normal, not Student-t** — fine at large N; ship an opt-in
  Student-t + Welch–Satterthwaite df variant (v2) for small-N experiments.

## 4. New families the legacy lacked (opt-in or validation-layer)

Defaults stay baseline-faithful; these are additive.

- **SRM chi-square gate** — before every comparison; blocking-but-non-dropping
  (`srm_flag`). ([data-contract-and-reporting.md](data-contract-and-reporting.md))
- **Sequential / always-valid CIs** (mSPRT) + alpha-spending — opt-in
  (`sequential.enabled`) to make the cumulative daily series honest about peeking
  (decision Q2). Default off (legacy parity); the readout refuses pre-horizon
  WIN/LOSE under fixed-horizon.
- **Benjamini-Hochberg** cross-metric correction — opt-in, read-time. Its
  composition with peeking must be validated empirically (not applied to
  peeking-inflated marginal p-values blindly).
- **`ratio_delta`** — principled delta-method ratio metric (the correct sibling of
  the quarantined post-normed methods).
- **Cross-fitted CUPED/CUPAC** — θ estimated on held-out data (removes the plug-in-θ
  optimism the baseline shares); v2, version-bumped.
- **BCa bootstrap**, **Mann-Whitney**, **cluster-robust SE** (analysis-unit ≠
  randomization-unit) — candidate methods, each one `BaseMethod` class.

## 5. CUPED covariate window (decision pending — see cumulative-intervals §5.1)

The legacy CUPED covariate uses a **growing** symmetric pre-window. We must pick and
golden-test ONE of: (a) reproduce the growing window (baseline-faithful), or (b) a
**fixed** lookback (e.g. `14d`) as a documented version-bumped deviation — arguably
*more* correct (a stationary covariate across the daily series). This is the one
place baseline fidelity and correctness genuinely conflict; the choice is recorded
here once made, and the scaffolded example metric must match it.

## 6. What stays exactly as the baseline (the "do not drift" defaults, v1)

Delta-method relative variance with the negative covariance term; pooled-θ CUPED
dividing by the original control mean; percentile bootstrap CI; sign-based bootstrap
p-value; effect computed on real data; config-time two-tier Bonferroni; the mixed
per-term ddof. These are golden-tested against the legacy engine and only change via
the §0 process.
