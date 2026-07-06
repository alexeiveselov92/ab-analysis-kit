# Statistics Baseline (legacy A/B engine)

> **Status:** frozen reference. This document captures the *exact* statistical
> behaviour of the legacy A/B engine
> (`playneta/analytics-data-pipelines` → `app/ab_testing`) so the new
> `ab-analysis-kit` can **reproduce it bit-for-bit as a baseline**, then improve
> it deliberately and measurably (see [statistics-changes.md](statistics-changes.md)).
>
> Rule: we never *silently* change a number. Any deviation from this baseline is
> a documented, tested decision validated against the legacy output and against
> the A/A false-positive matrix ([aa-false-positive-matrix.md](aa-false-positive-matrix.md)).

This is the math the user calls "выстраданное" — assembled over time from many
A/B-testing papers, with heavy effort spent on **numpy-vectorised** computation.
We treat the algorithms as serious and correct-until-proven-otherwise, and we
preserve the vectorisation philosophy.

---

## 1. Core data model

### `Sample` — the unit-level container (continuous / "sample" metrics)

Source: `app/ab_testing/models/sample.py`. A `Sample` holds one randomisation
unit per element (e.g. one user) for **one variant**:

- `array` — the metric value per unit (e.g. cumulative `gross_usd` per user).
- `cov_array` *(optional)* — the **covariate** per unit (CUPED / post-normalisation;
  typically the pre-experiment value of the same metric).
- `categories_array` *(optional)* — the **stratum** label per unit (stratification).
- Derived (computed eagerly at construction):
  - `mean = np.mean(array)`
  - `std  = np.std(array)`  → **population std, `ddof=0`**
  - `var  = std**2`         → **population variance, `ddof=0`**
  - `sample_size = len(array)`
  - covariate stats: `cov_mean`, `cov_std`, `cov_var` (all `ddof=0`),
    `corr_coef = np.corrcoef(array, cov_array)[0,1]`

> ⚠️ **Baseline fact #1 — the variance convention is MIXED, not uniformly
> `ddof=0`.** `Sample.std`/`Sample.var` and every `np.var`/`np.std` use `ddof=0`
> (population). **But `np.cov`** — used for the CUPED θ and for the negative
> covariance term in the paired/CUPED relative-variance formulas — uses numpy's
> **default `ddof=1`**. So within a single relative-variance expression the
> variance terms are `ddof=0` and the covariance terms are `ddof=1`. This is a
> real, load-bearing inconsistency: the new engine must encode the **exact
> per-term convention** (verified against captured legacy *outputs*, with a
> golden test on θ itself), **not** a blanket `ddof`. A uniform-`ddof` rewrite
> will fail CUPED/paired golden tests. (Confirmed by the quorum's
> stats-correctness + reliability reviewers against `cuped_ttest.py:72-75` and
> the paired processors.)

`get_category_weights(sample_size, stratify)` returns, per stratum, the integer
count `max(1, round(count * sample_size / N))` — used to size stratified
bootstrap resamples.

### `Fraction` — proportion metrics

Source: `app/ab_testing/models/fraction.py` + `processors/tests/ztest.py`.
Holds `count` (successes) and `nobs` (trials) per variant — a proportion
`p = count / nobs`. Used for conversion-rate / share metrics via a two-proportion
z-test.

### `TestResult` — the per-comparison output record

Source: `app/ab_testing/models/test_result.py`. Every test, for every **pair** of
variants, emits exactly this:

```
name_1, value_1, std_1, cov_value_1, size_1, mde_1     # control
name_2, value_2, std_2, cov_value_2, size_2, mde_2     # variant
method_name, method_params (dict)
alpha, pvalue, effect, ci_length, left_bound, right_bound, reject  # bool
effect_distribution  # optional scipy frozen dist, for plotting
```

This maps 1:1 onto the results table / dashboard contract — see
[data-contract-and-reporting.md](data-contract-and-reporting.md).

---

## 2. Effect definitions: relative vs absolute

Every test supports `test_type ∈ {"relative", "absolute"}`.

- **absolute**: effect = `mean_2 − mean_1` (variant minus control).
- **relative**: effect = `(mean_2 − mean_1) / mean_1` (lift over control).

The relative effect is the default and is what the production dashboard shows
(`test_type: relative`, y-axis in %). The relative variance is **not** a naive
ratio of variances — it is a **delta-method (Taylor) linearisation** that keeps
the covariance between numerator and denominator (they share `mean_1`). This is
the single most important formula to preserve.

---

## 3. Parametric tests

### 3.1 `TTest` (`processors/tests/ttest.py`) — "t-test" (normal approximation)

Despite the name, the test statistic uses the **normal** distribution
(`scipy.stats.norm`) on the mean difference — a large-sample Welch-style z-test.
Pairwise over all variant combinations (`itertools.combinations`).

Per-mean variance: `var_mean_i = sample_i.var / sample_i.sample_size`.

**Absolute:**
```python
difference_mean      = mean_2 - mean_1
difference_mean_var  = var_mean_1 + var_mean_2
distribution = sps.norm(loc=difference_mean, scale=sqrt(difference_mean_var))
effect = difference_mean
```

**Relative (delta method):**
```python
covariance  = -var_mean_1                      # num & denom share mean_1
relative_mu = difference_mean / mean_1
relative_var = ( difference_mean_var / mean_1**2
               + var_mean_1 * (difference_mean**2 / mean_1**4)
               - 2 * (difference_mean / mean_1**3) * covariance )
distribution = sps.norm(loc=relative_mu, scale=sqrt(relative_var))
effect = relative_mu
```

**Results (shared by all parametric tests):**
```python
left_bound, right_bound = distribution.ppf([alpha/2, 1 - alpha/2])
ci_length = right_bound - left_bound
pvalue    = 2 * min(distribution.cdf(0), distribution.sf(0))   # two-sided vs H0: effect=0
reject    = pvalue < alpha
```

MDE per variant via `get_ttest_mde(...)` (see §6), with
`ratio = size_other / size_this`.

### 3.2 `ZTest` (`processors/tests/ztest.py`) — two-proportion z-test

For `fraction` metrics. Two-proportion test on `count/nobs`, relative/absolute
with the analogous delta-method linearisation for the relative case. (Full code
in the legacy file; the catalogue in the appendix lists its exact formula.)

### 3.3 `CupedTTest` (`processors/tests/cuped_ttest.py`) — CUPED variance reduction

CUPED removes pre-experiment variance using the covariate `cov_array` (X). The
adjustment coefficient **θ is pooled across both variants**:

```python
theta = ( cov(Y1, X1) + cov(Y2, X2) ) / ( var(X1) + var(X2) )   # np.cov, np.var (ddof=0)
cup_1 = Y1 - theta * X1
cup_2 = Y2 - theta * X2
```

**Absolute:** identical to the t-test but computed on `cup_1`, `cup_2`
(means, per-mean variances, normal CI/p-value).

**Relative (delta method, with a subtlety):** the *denominator* uses the
**original** control mean, while the *numerator* uses the CUPED-adjusted means:
```python
mean_den    = mean(Y1)                       # original control mean
mean_num    = mean(cup_2) - mean(cup_1)
var_mean_den = var(Y1) / n1
var_mean_num = var(cup_2)/n2 + var(cup_1)/n1
cov          = -cov(cup_1, Y1) / n1
relative_mu  = mean_num / mean_den
relative_var = ( var_mean_num/mean_den**2
               + var_mean_den*(mean_num**2/mean_den**4)
               - 2*(mean_num/mean_den**3)*cov )
```

Guards: warns if `corr_coef < 0.5` (CUPED gains will be small); errors if the
covariate is missing. MDE via `get_cuped_ttest_mde(...)` which deflates the std
by `sqrt(1 - corr**2)` (see §6).

> ⚠️ **Baseline fact #2 — θ is estimated on the *same* data used for the test**
> (no cross-fitting / sample-splitting). This is the common practice and is the
> baseline; a leakage-controlled variant is a candidate improvement.

---

## 4. Bootstrap family

All bootstraps share: a configurable `stat_func` (default `np.mean`, but e.g.
`np.median` for quantile metrics), `test_type` relative/absolute, `n_samples`
(default 1000), optional `stratify`, and a percentile CI.

**Common result computation:**
```python
left_bound, right_bound = np.quantile(boot_data, [alpha/2, 1 - alpha/2])
ci_length = right_bound - left_bound
effect    = test_function(stat_func(array_1), stat_func(array_2))   # on the real data
pvalue    = 2 * min(np.mean(boot_data > 0), np.mean(boot_data < 0))
reject    = pvalue < alpha
```
A Kolmogorov–Smirnov normality check on `boot_data` logs a warning if the
bootstrap distribution looks non-normal (diagnostic only).

### 4.1 Vectorised resampling engine (`generators/bootstrap_samples_generator.py`)

The heart of the performance story. Instead of a Python loop over `n_samples`,
it builds the **entire** resample matrix at once:

```python
indices = np.random.randint(0, len(values), (n_samples, category_size))
values_samples[:, start_idx:end_idx] = values[indices]      # fancy-index, vectorised
if cov_bootstrap:
    cov_values_samples[:, start_idx:end_idx] = cov_values[indices]   # SAME indices → preserves pairing
```

- `stratify=True`: resamples **within each stratum** (`categories_array == category`)
  and concatenates, with per-stratum sizes from `get_category_weights`.
- `cov_bootstrap=True`: resamples the covariate with the **same indices**, so the
  (Y, X) pairing per unit is preserved (needed for post-normalisation).
- Statistic application (`utils/bootstrap_utils.py::StatFuncApplier`):
  `np.apply_along_axis(stat_func, 1, values_samples)` → one statistic per resample row.

### 4.2 `BootstrapTest` (`processors/tests/bootstrap_test.py`)

Plain IID (or stratified) bootstrap of `stat_func`.
```python
boot_data = boot_2 - boot_1                  # absolute
boot_data = boot_2 / boot_1 - 1              # relative
```
Stratified mode aligns stratum weights **across variants** via
`weight_method ∈ {"min","mean"}` so both variants resample to a common stratum
mix (poststratification by design).

### 4.3 `PostNormedBootstrapTest` (`processors/tests/post_normed_bootstrap_test.py`)

Ratio / post-normalised metric: divides out the covariate ratio (variance
reduction for ratio metrics). Requires `cov_array`; uses `cov_bootstrap=True`.
```python
# absolute:  S2 - (S2_cov / S1_cov) * S1
# relative: (S2 / S1) / (S2_cov / S1_cov) - 1
```
> ⚠️ **Baseline fact #3 — `random_seed` is deliberately *excluded* from
> `method_params`** here (a code comment explains: each cumulative "update cycle"
> generates a fresh seed; including it would change the method identity every day
> and break the per-day series identity). This matters for how the new engine
> keys/deduplicates results across the cumulative timeline.

### 4.4 `PoissonBootstrapTest` (`processors/tests/poisson_bootstrap_test.py`)

The **streaming-friendly** bootstrap — weights instead of index resampling:
```python
weights = np.random.poisson(1, (n_samples, sample_size))     # one weight per unit per resample
stat    = stat_func( np.dot(weights, array) / weights.sum(axis=1) )   # weighted mean
```
Poststratification multiplies each unit's weights by `1 / stratum_count`. Because
it never materialises an `n_samples × sample_size` *value* matrix (only weights ×
the value vector), it is the most memory-efficient variant and the natural fit
for an incremental / large-N design.

### 4.5 Paired & Poisson-post-normed variants

`paired_bootstrap_test`, `paired_post_normed_bootstrap_test`,
`paired_poisson_bootstrap_test`, `poisson_post_normed_bootstrap_test`,
`paired_ttest`, `paired_ttest_cuped` — paired designs align units by
`pair_id_col` (intersection of pair ids across variants, sorted) before applying
the same machinery. Full per-method formulas are catalogued in the
[appendix](#appendix-full-method-catalogue).

---

## 5. Pairing & multi-variant handling

- Every test runs over **all pairs** of variants (`combinations(samples, 2)`),
  emitting one `TestResult` per pair. With 2 groups → 1 comparison.
- Paired metrics intersect `pair_id_col` across variants and sort, dropping
  unmatched pairs (with a warning).

---

## 6. Power, MDE and α-adjustment

### MDE / power / sample-size (`utils/sample_utils.py`, `utils/fraction_utils.py`)

Built on `statsmodels` `TTestIndPower` / `NormalIndPower`:

- `get_ttest_mde(mean, std, size, type, alpha, power, ratio)` — solve for the
  effect size at the given power, convert to relative/absolute MDE.
- `get_ttest_power(...)`, `get_ttest_sample_size(...)` — the inverse problems.
- **CUPED variants** deflate the std: `adjusted_std = std * sqrt(1 - corr**2)`
  before the same solve — i.e. CUPED's smaller MDE for the same N.
- Proportions (`fraction_utils.py`) use `NormalIndPower` +
  `proportion_effectsize` (arcsine transform).

The dashboard's `calculate_mde: true` / `power: 0.8` come straight from these.

### α-adjustment (`utils/alpha_adjustment_utils.py`) — Bonferroni

```python
comparisons     = groups_count * (groups_count - 1) / 2 * metrics_count
alpha_adjusted  = alpha / comparisons
```
Applied **at experiment-definition time**: each `ExpComparison` receives an
already-adjusted alpha (main metric vs secondary metrics can use different
counts). The number of *cumulative time points* is **not** part of this
correction — see the peeking discussion in
[statistics-changes.md](statistics-changes.md).

---

## 7. Numerical conventions to preserve (the "do not drift" list)

1. **Mixed `ddof` by term** (baseline fact #1): `np.var`/`np.std`/`Sample.var`
   use `ddof=0`; `np.cov` (θ and the covariance correction terms) uses `ddof=1`.
   Encode per-term, verified against captured legacy outputs — never normalise to
   a single `ddof`.
2. **Normal approximation** (`scipy.stats.norm`) for parametric CI/p-value, not
   Student-t.
3. **Two-sided p-value** = `2 * min(cdf(0), sf(0))` for parametric;
   `2 * min(P(boot>0), P(boot<0))` for bootstrap.
4. **Percentile bootstrap CI** = `np.quantile(boot, [α/2, 1−α/2])` (not BCa).
5. **Delta-method relative variance** with the negative covariance term — exact
   formulas in §3.
6. **Pooled θ** for CUPED across both variants; CUPED relative effect divides by
   the **original** control mean.
7. **Bonferroni** over `C(groups,2) × metrics`, applied at config time.
8. Effect is computed on the **real** data; the bootstrap distribution only sets
   the CI and p-value.

These are encoded as **golden tests** in the new engine: the same inputs must
reproduce these outputs within tolerance before any improvement is layered on.

---

## Appendix: full method catalogue

The complete, line-referenced catalogue of every test (including `ztest`,
`paired_ttest`, `paired_ttest_cuped`, `paired_bootstrap_test`,
`paired_post_normed_bootstrap_test`, `paired_poisson_bootstrap_test`,
`poisson_post_normed_bootstrap_test`) — exact statistic, CI/p-value,
relative/absolute handling, vectorisation notes and dependencies — is maintained
in [reference/legacy-method-catalogue.md](../reference/legacy-method-catalogue.md),
generated from the source extraction. Each method there links back to the legacy
file and line range.
