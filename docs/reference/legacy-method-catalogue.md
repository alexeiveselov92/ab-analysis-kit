# Legacy method catalogue (reference)

> Auto-extracted from the legacy engine (`analytics-data-pipelines/app/ab_testing`) by the project-initiation analysis workflow, then human-reviewed. This is a **reference** for the new engine: the algorithms we re-derive, compare against, and improve (see [../specs/statistics-baseline.md](../specs/statistics-baseline.md) and [../specs/statistics-changes.md](../specs/statistics-changes.md)). The legacy *storage/marts* layer is intentionally NOT carried over — only the math.


## Summary

This legacy A/B library implements a family of pairwise (itertools.combinations(samples,2)) two-group comparison processors, all returning a uniform TestResult dataclass with effect, percentile/Normal CI [left_bound,right_bound], two-sided p-value, reject flag, and optional MDE. There are three statistical families. (1) Closed-form delta-method tests built on sps.norm (NOT Student-t despite the 't-test' names): TTest (independent), PairedTTest, CupedTTest, PairedCupedTTest. Each forms a Normal effect distribution; absolute effect = mean2-mean1, relative effect = (mean2-mean1)/mean1 via a first-order delta method that includes the negative covariance between numerator and denominator (relative_var = diff_var/m1^2 + var_mean1*diff^2/m1^4 - 2*(diff/m1^3)*cov). CI = norm.ppf([alpha/2,1-alpha/2]); p-value = 2*min(cdf(0),sf(0)). CUPED subtracts theta*covariate (theta = pooled cov(value,covariate)/var(covariate)) to shrink variance. (2) Two-proportion pooled ZTest (count/nobs Fractions): z_stat with pooled variance, Cohen's-h based MDE. (3) Bootstrap tests with two resampling engines. Classic engine = BootstrapSamplesGenerator: vectorized with-replacement index matrices (np.random.randint(0,len,(n_samples,size))) per stratum, preallocated output, same index matrix reused for covariate to keep value/cov row-aligned; identical random_seed across two generators yields implicit pairing for the paired variants; stat_func applied via np.apply_along_axis (a Python row loop, the main efficiency weakness). Poisson engine = np.random.poisson(1,(n_samples,size)) weights with np.dot(weights,array)/weights.sum(axis=1) (true single-matmul vectorization, but only valid for the mean). Bootstrap CIs are percentile (np.quantile); p-values are sign-based 2*min(mean(boot>0),mean(boot<0)). Ratio-metric handling: PostNormedBootstrapTest does empirical covariate-ratio normalization (S2-(S2_cov/S1_cov)*S1 absolute; (S2/S1)/(S2_cov/S1_cov)-1 relative); stratification reweights strata (resample engine via per-category index draws and group-pooled min/mean category weights; Poisson via 1/count column scaling). Multiple-comparison control is external Bonferroni (adjust_alpha = alpha / (C(groups,2)*metrics)). Key risks found: PoissonPostNormed is a non-post-normed copy of PoissonBootstrap (likely bug); PairedPostNormed standardizes (z-score) instead of covariate-normalizing and its relative branch divides by ~0; Poisson tests are only correct for the mean yet accept arbitrary stat_func; t-tests use Normal with no df correction; mixed ddof between np.var(0) and np.cov(1); apply_along_axis defeats vectorization for classic bootstrap; global np.random.seed mutation; relative effects lack divide-by-zero guards; and the power analyzers import from an inconsistent 'pipelines.*' path.


## Alpha adjustment

Bonferroni multiple-comparison correction in alpha_adjustment_utils.adjust_alpha(alpha, groups_count, metrics_count=1). Number of pairwise comparisons = (groups_count*(groups_count-1))/2 * metrics_count (the count of all unordered group pairs across all metrics). alpha_adjusted = alpha / comparisons. Validation: alpha must be in (0,1) else ValueError; metrics_count>=1 else ValueError; groups_count>=2 else ValueError ('Number of groups must be more than 1'). This matches the itertools.combinations(samples,2) pairwise comparison loop used by every test processor (each processor compares all C(n,2) pairs). The adjustment is computed OUTSIDE the test classes and must be passed in as the alpha argument -- the test processors themselves do NOT apply any correction internally; they use whatever self.alpha they were constructed with for both CI bounds (alpha/2, 1-alpha/2) and the reject threshold (pvalue<alpha).


## Vectorised sampling engine

BootstrapSamplesGenerator.process(n_samples, sample_size, stratify, cov_bootstrap, random_seed, categories_weights) builds vectorized resamples as follows: (1) np.random.seed(random_seed) is called globally (so two generators given the SAME seed produce identical index matrices -> implicit pairing in the paired bootstrap tests). (2) sample_size defaults to sample.sample_size; if categories_weights is None it is derived via sample.get_category_weights(sample_size, stratify). (3) _generate_samples preallocates output matrices: values_samples = np.empty((n_samples, total_category_size), dtype=array.dtype) and (if cov_bootstrap) cov_values_samples of same shape from cov_array.dtype, where total_category_size = sum(categories_weights.values()). (4) For each category it builds the resample block: if stratify, category_mask = (categories_array == category) selects that stratum's values (and aligned cov_values); else uses the full array. It draws indices = np.random.randint(0, len(values), (n_samples, category_size)) -- a vectorized integer index matrix with REPLACEMENT -- and fills values_samples[:, start:end] = values[indices] (fancy indexing). When cov_bootstrap, the SAME index matrix is applied to cov_values so value/covariate pairs stay row-aligned (critical for the ratio/post-norm delta computation). Blocks are concatenated column-wise by advancing start_idx. STRATIFICATION: implemented by per-category index draws so each stratum contributes exactly category_size resampled units; category sizes come from Sample.get_category_weights = max(1, int(count*sample_size/sample_size)) per unique category, optionally re-balanced across the two groups by BootstrapTest._get_category_weights which pools per-group weight dicts via 'min' or 'mean', normalizes to sum 1, then scales by each group's sample_size and int()-truncates. Because of max(1,...) and int() truncation, the stratified resample total may differ from the nominal sample_size. POISSON WEIGHTING (separate engine, only in the *poisson* test classes, NOT in BootstrapSamplesGenerator): weights = np.random.poisson(1, (n_samples, sample_size)); per-replicate weighted mean = np.dot(weights, array) / weights.sum(axis=1). Poisson(1) weights approximate multinomial/with-replacement resampling and let the whole bootstrap be a single matrix multiply (no per-row loop). Post-stratification for the Poisson path scales each column's weight by 1/count_of_its_category (category_weights[cat] = 1/count) via a Python for-loop over columns -- this reweights strata inversely to their frequency. After resampling, StatFuncApplier.process applies stat_func via np.apply_along_axis(stat_func, 1, values_samples) (row-wise Python loop, not vectorized) and likewise to cov_values_samples if present, returning (values_results, cov_results).


## Methods


### TTest (independent two-sample, delta-method via normal approx)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/ttest.py`
- **Purpose:** Compare means of two independent Samples. Despite the name 't-test', it is actually a large-sample Z/normal-approximation test (uses sps.norm, never sps.t).
- **Statistic:** Not an explicit t/z scalar. Builds the effect's sampling distribution as a Normal and derives p-value from it. Absolute: loc=mean_2-mean_1, scale=sqrt(var1/n1 + var2/n2). Relative: loc & scale from delta-method (see key_formula).
- **CI & p-value:** CI: left,right = distribution.ppf([alpha/2, 1-alpha/2]) (Normal quantiles around loc). ci_length = right-left. p-value: pvalue = 2*min(distribution.cdf(0), distribution.sf(0)) -- two-sided test that the effect distribution is centered away from 0. reject = pvalue < alpha. Note: CI is symmetric (Normal) but p-value is from the same Normal evaluated at 0, so they are consistent.
- **Effect types:** Relative and absolute. Absolute effect = mean_2-mean_1. Relative effect = (mean_2-mean_1)/mean_1 (a ratio of estimators, handled by delta method including the negative covariance term -var_mean_1 between numerator (mean_2-mean_1) and denominator mean_1).
- **Inputs:** Two Sample objects with .mean, .var, .std, .sample_size, .array (iterated pairwise via itertools.combinations over a list). alpha, test_type, power, calculate_mde.
- **Outputs:** TestResult dataclass: value_1/2 (means), std_1/2, size_1/2, pvalue, effect, ci_length, left_bound, right_bound, reject, mde_1, mde_2, optional effect_distribution (sps.norm object).
- **Key formula:** var_mean_i = sample_i.var / sample_i.sample_size; difference_mean = mean_2-mean_1; difference_mean_var = var_mean_1+var_mean_2. Relative delta-method: covariance = -var_mean_1; relative_mu = difference_mean/mean_1; relative_var = difference_mean_var/(mean_1**2) + var_mean_1*((difference_mean**2)/(mean_1**4)) - 2*(difference_mean/(mean_1**3))*covariance. distribution = sps.norm(loc=relative_mu, scale=sqrt(relative_var)).
- **Vectorisation:** No bootstrap; closed-form. var uses population variance (np.std/np.var with ddof=0 via Sample model). distribution.ppf called with a 2-element list to get both bounds in one call.
- **Dependencies:** numpy, scipy.stats (sps.norm), itertools.combinations, get_ttest_mde/get_ttest_power from sample_utils (statsmodels TTestIndPower).

### PairedTTest (paired/dependent, normal approx)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/paired_ttest.py`
- **Purpose:** Paired comparison of two equal-size, aligned Samples (Nth element of one corresponds to Nth of the other). Normal-approx, not Student-t.
- **Statistic:** Same Normal-distribution machinery as TTest, but variance of the difference is computed elementwise on paired arrays.
- **CI & p-value:** Identical CI/p-value scheme as TTest: ppf([alpha/2,1-alpha/2]); pvalue=2*min(cdf(0),sf(0)); reject=pvalue<alpha.
- **Effect types:** Relative and absolute. Absolute effect = mean_2-mean_1. Relative effect = (mean_2-mean_1)/mean_1 via delta method with paired covariance term.
- **Inputs:** Two equal-length aligned Sample objects (.array, .mean, .var, .sample_size). Requires sample1.sample_size == sample2.sample_size (raises otherwise). Logs a warning that arrays must be sorted/aligned by pair.
- **Outputs:** TestResult including cov_value_1/2 = sample.cov_mean (often None for plain paired).
- **Key formula:** difference_mean_var = np.var(array2 - array1) / n1 (population var of paired diffs, divided by n). Relative: covariance = -np.cov(array2-array1, array1)[0,1] / n1; relative_var = difference_mean_var/(mean_1**2) + var_mean_1*((difference_mean**2)/(mean_1**4)) - 2*(difference_mean/(mean_1**3))*covariance, where var_mean_1 = sample1.var/n1.
- **Vectorisation:** Uses np.array(sample2.array)-np.array(sample1.array) elementwise difference, np.var (ddof=0) and np.cov (ddof=1 default -> inconsistent ddof between np.var and np.cov within same formula).
- **Dependencies:** numpy, scipy.stats, itertools.combinations.

### ZTest (two-proportion z-test, pooled)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/ztest.py`
- **Purpose:** Compare two binomial proportions (Fraction objects: count/nobs).
- **Statistic:** z_stat = (prop_1 - prop_2) / sqrt(prop_combined*(1-prop_combined)*(1/nobs1 + 1/nobs2)), where prop_combined = (count1+count2)/(nobs1+nobs2) is the pooled proportion.
- **CI & p-value:** p-value: pvalue = 2*min(sps.norm.cdf(z_stat), sps.norm.sf(z_stat)). CI is built SEPARATELY from the statistic using a POOLED std for the effect: std_effect = sqrt(prop_combined*(1-prop_combined)*(1/nobs1+1/nobs2)); left,right = norm.ppf([alpha/2,1-alpha/2])*std_effect + effect. reject = pvalue < alpha.
- **Effect types:** Absolute effect = prop_2 - prop_1; std_effect as above. Relative: effect = (prop_2-prop_1)/prop_1; std_effect = std_effect/prop_1 (naive division by control prop, no full delta-method covariance term); bounds recomputed with the scaled std.
- **Inputs:** Two Fraction objects (.count, .nobs, .prop, .std) via combinations. alpha, test_type, power, calculate_mde.
- **Outputs:** TestResult with value_1/2=prop_1/2, std_1/2 = sqrt(p*(1-p)/nobs) (UNpooled per-group std, different from std_effect used in CI), effect_distribution = sps.norm(loc=effect, scale=std_effect), mde_1/2.
- **Key formula:** prop_combined=(count1+count2)/(nobs1+nobs2); z_stat=(prop_1-prop_2)/sqrt(prop_combined*(1-prop_combined)*(1/nobs1+1/nobs2)); std_effect=sqrt(prop_combined*(1-prop_combined)*(1/nobs1+1/nobs2)); relative: std_effect/=prop_1.
- **Vectorisation:** norm.ppf([alpha/2,1-alpha/2]) vectorized for both bounds. Note z_stat sign uses prop_1-prop_2 while effect uses prop_2-prop_1 (opposite signs); since p-value is symmetric 2*min(cdf,sf) this does not change the p-value but is an inconsistency.
- **Dependencies:** numpy, scipy.stats, get_fraction_mde/get_fraction_power (statsmodels NormalIndPower, proportion_effectsize).

### CupedTTest (CUPED variance reduction, independent)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/cuped_ttest.py`
- **Purpose:** CUPED (Controlled-experiment Using Pre-Experiment Data) variance reduction on two independent Samples using a covariate cov_array, then normal-approx test on adjusted values.
- **Statistic:** Computes a single pooled theta from both samples, forms CUPED-adjusted values cup_i = array_i - theta*cov_array_i, then runs the same Normal-approx mean comparison on cup_1, cup_2.
- **CI & p-value:** Same as TTest: ppf([alpha/2,1-alpha/2]); pvalue=2*min(cdf(0),sf(0)); reject=pvalue<alpha.
- **Effect types:** Absolute: difference of CUPED-adjusted means. Relative: delta-method ratio with denominator = mean of ORIGINAL sample1.array (not CUPED-adjusted) and numerator = mean(cup_2)-mean(cup_1).
- **Inputs:** Two Sample objects with cov_array set (.array, .cov_array, .corr_coef). Warns if corr_coef<0.5. Requires cov_array present.
- **Outputs:** TestResult with cov_value_1/2 = sample.cov_mean, mde via get_cuped_ttest_mde (uses corr_coef to shrink std).
- **Key formula:** theta = (cov(array1,cov1)[0,1] + cov(array2,cov2)[0,1]) / (var(cov1)+var(cov2)). cup_i = array_i - theta*cov_array_i. Absolute: var_mean_cup_i = var(cup_i)/len(cup_i); distribution=norm(mean2_cup-mean1_cup, sqrt(var_mean_cup_1+var_mean_cup_2)). Relative: mean_den=mean(array1) [original]; mean_num=mean(cup_2)-mean(cup_1); var_mean_den=var(array1)/n1; var_mean_num=var(cup_2)/n2+var(cup_1)/n1; cov=-cov(cup_1,array1)[0,1]/n1; relative_var=var_mean_num/mean_den**2 + var_mean_den*(mean_num**2/mean_den**4) - 2*(mean_num/mean_den**3)*cov.
- **Vectorisation:** np.cov default ddof=1 used for theta numerator; np.var default ddof=0 used for theta denominator and variances -> mixed ddof. theta is a single scalar applied to both groups.
- **Dependencies:** numpy, scipy.stats, get_cuped_ttest_mde/get_cuped_ttest_power.

### PairedCupedTTest (CUPED, paired)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/paired_ttest_cuped.py`
- **Purpose:** CUPED variance reduction for paired/aligned equal-size Samples.
- **Statistic:** theta computed on the DIFFERENCES of arrays and covariates; CUPED-adjust each side; Normal-approx paired comparison.
- **CI & p-value:** Same scheme: ppf([alpha/2,1-alpha/2]); pvalue=2*min(cdf(0),sf(0)); reject=pvalue<alpha.
- **Effect types:** Absolute: mean(cup_2)-mean(cup_1) with var(cup_2-cup_1)/n. Relative: delta-method with mean_den=mean(array1) original, paired covariance cov(cup_2-cup_1, array1).
- **Inputs:** Two equal-length aligned Samples with cov_array. Requires equal sizes and cov present. Warns corr<0.5 and alignment requirement.
- **Outputs:** TestResult with cov_value_1/2 = sample.cov_mean. No MDE for paired variants.
- **Key formula:** theta = cov(array2-array1, cov2-cov1)[0,1] / var(cov2-cov1). cup_i = array_i - theta*cov_array_i. Absolute: difference_mean_var_cup = var(cup_2-cup_1)/n. Relative: mean_num=mean(cup_2)-mean(cup_1); var_mean_num=var(cup_2-cup_1)/n; var_mean_den=var(array1)/n; cov=-cov(cup_2-cup_1, array1)[0,1]/n; relative_var same delta form as others.
- **Vectorisation:** Differences computed elementwise; mixed ddof again (np.cov ddof=1, np.var ddof=0).
- **Dependencies:** numpy, scipy.stats.

### BootstrapTest (classic resampling bootstrap, independent)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/bootstrap_test.py`
- **Purpose:** Nonparametric bootstrap of an arbitrary stat_func (default np.mean) over two independent Samples, with optional stratification.
- **Statistic:** boot_data distribution of effect across n_samples resamples. boot_i = stat_func applied row-wise to resampled matrices.
- **CI & p-value:** CI: percentile method np.quantile(boot_data,[alpha/2,1-alpha/2]). p-value: percentile/sign-based pvalue = 2*min(np.mean(boot_data>0), np.mean(boot_data<0)) (fraction of bootstrap effects on each side of 0). reject=pvalue<alpha. NOTE: effect reported is the POINT estimate from the original arrays (stat_func(sample2)-stat_func(sample1) etc.), not the bootstrap mean, so effect can lie outside [left,right] only rarely; CI is percentile-of-bootstrap.
- **Effect types:** Absolute: boot_2-boot_1. Relative: boot_2/boot_1 - 1 (ratio per bootstrap replicate, elementwise).
- **Inputs:** Two Samples; n_samples, stratify, weight_method (min/mean), random_seed, stat_func. Uses BootstrapSamplesGenerator + StatFuncApplier.
- **Outputs:** TestResult; value_1/2 = stat_func(array); std_1/2 = np.std(array); effect_distribution optionally fit as norm(mean,std) of boot_data.
- **Key formula:** boot_data = boot_2-boot_1 (absolute) or boot_2/boot_1-1 (relative). left,right=np.quantile(boot_data,[alpha/2,1-alpha/2]); pvalue=2*min(mean(boot_data>0),mean(boot_data<0)).
- **Vectorisation:** Resampling fully vectorized in BootstrapSamplesGenerator (matrix of indices). StatFuncApplier uses np.apply_along_axis(stat_func,1,matrix) -- a Python-level loop over rows, NOT true vectorization (slow for large n_samples). _check_normality runs sps.kstest against fitted normal and warns if p<0.05.
- **Dependencies:** numpy, scipy.stats, BootstrapSamplesGenerator, StatFuncApplier.

### PairedBootstrapTest (paired resampling bootstrap)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/paired_bootstrap_test.py`
- **Purpose:** Bootstrap for paired equal-size Samples. random_seed defaults to np.random.randint(0,2**30-1) if None.
- **Statistic:** Same as BootstrapTest but both samples generated; effect = np.mean(boot_data) (NOT the original point estimate, unlike BootstrapTest).
- **CI & p-value:** CI np.quantile(boot_data,[alpha/2,1-alpha/2]); pvalue=2*min(mean(boot_data>0),mean(boot_data<0)); reject=pvalue<alpha.
- **Effect types:** Absolute boot_2-boot_1; relative boot_2/boot_1-1. Each side resampled INDEPENDENTLY (separate generators, but with the SAME random_seed passed to both -> same RNG indices, effectively pairing the resamples).
- **Inputs:** Two equal-size Samples + bootstrap params. Requires equal sizes.
- **Outputs:** TestResult with effect = mean(boot_data), cov_value_1/2 = stat_func(sample.cov_array) (will error if cov_array is None).
- **Key formula:** boot_data = boot_2-boot_1 or boot_2/boot_1-1; effect=np.mean(boot_data); pvalue=2*min(mean(boot_data>0),mean(boot_data<0)).
- **Vectorisation:** Both generators seeded with identical random_seed so np.random.randint produces the SAME index matrix -> paired resampling achieved implicitly (rows aligned across groups). apply_along_axis loop again.
- **Dependencies:** numpy, scipy.stats, BootstrapSamplesGenerator, StatFuncApplier.

### PoissonBootstrapTest (Poisson-weighted bootstrap, independent)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/poisson_bootstrap_test.py`
- **Purpose:** Fully vectorized bootstrap using Poisson(1) weights instead of index resampling (approximates multinomial resampling for large n). Memory- and speed-efficient.
- **Statistic:** Weighted mean per replicate: np.dot(weights, sample.array) / weights.sum(axis=1), then stat_func applied to the resulting length-n_samples vector.
- **CI & p-value:** CI np.quantile(boot_data,[alpha/2,1-alpha/2]); pvalue=2*min(mean(boot_data>0),mean(boot_data<0)); reject=pvalue<alpha.
- **Effect types:** Absolute stat_2-stat_1; relative stat_2/stat_1-1.
- **Inputs:** Two Samples; n_samples, stratify, random_seed (defaults to random if None). np.random.seed(random_seed) set globally before drawing weights.
- **Outputs:** TestResult; effect = original point estimate stat_func(sample2)-stat_func(sample1) etc.
- **Key formula:** weights = np.random.poisson(1,(n_samples, sample_size)); stat = stat_func(np.dot(weights, array)/weights.sum(axis=1)); boot_data=stat_2-stat_1 or stat_2/stat_1-1.
- **Vectorisation:** TRULY vectorized: matrix-vector dot product computes all n_samples weighted means at once (only works correctly for mean-type stats because the dot/sum is the weighted MEAN regardless of stat_func; applying a non-mean stat_func to the resulting per-replicate vector is mathematically incoherent). weights1 and weights2 drawn separately but in sequence after one global seed.
- **Dependencies:** numpy only (no scipy here).

### PairedPoissonBootstrapTest (paired Poisson bootstrap)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/paired_poisson_bootstrap_test.py`
- **Purpose:** Poisson bootstrap for paired equal-size Samples; SAME weight matrix applied to both groups (true pairing).
- **Statistic:** stat_1 and stat_2 both use the identical weights matrix -> paired weighted means.
- **CI & p-value:** CI np.quantile(boot_data,[alpha/2,1-alpha/2]); pvalue=2*min(mean(boot_data>0),mean(boot_data<0)); reject=pvalue<alpha.
- **Effect types:** Absolute stat_2-stat_1; relative stat_2/stat_1-1.
- **Inputs:** Two equal-size Samples. One weights matrix weights=np.random.poisson(1,(n_samples, sample1.sample_size)).
- **Outputs:** TestResult; effect = original point estimate.
- **Key formula:** weights=poisson(1,(n_samples,n)); stat_i=stat_func(np.dot(weights, array_i)/weights.sum(axis=1)); boot_data per test_type.
- **Vectorisation:** Single shared weights matrix gives genuine paired resampling. Vectorized dot product. Same mean-only caveat for stat_func.
- **Dependencies:** numpy only.

### PostNormedBootstrapTest (ratio-metric post-normalization bootstrap, independent)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/post_normed_bootstrap_test.py`
- **Purpose:** Post-normalized (ratio-metric) bootstrap: normalizes the test-group statistic by the ratio of a covariate statistic, to reduce variance / handle ratio metrics (e.g. revenue-per-user normalized by a pre-period denominator).
- **Statistic:** Bootstraps both the value stat (boot_i) and covariate stat (cov_boot_i) using cov_bootstrap=True (resamples value and cov together with same indices). Then post-norms.
- **CI & p-value:** CI np.quantile(boot_data,[alpha/2,1-alpha/2]); pvalue=2*min(mean(boot_data>0),mean(boot_data<0)); reject=pvalue<alpha.
- **Effect types:** Absolute: S2 - (S2_cov/S1_cov)*S1. Relative: (S2/S1)/(S2_cov/S1_cov) - 1. (S = value stat, S_cov = covariate stat per replicate.)
- **Inputs:** Two Samples WITH cov_array (required). cov_bootstrap=True so generator returns paired value+cov resamples.
- **Outputs:** TestResult; effect computed via same _apply_test_function on the ORIGINAL point statistics of array and cov_array.
- **Key formula:** boot_data = S2 - (S2_cov/S1_cov)*S1 (absolute) or (S2/S1)/(S2_cov/S1_cov)-1 (relative), where S1,S2,S1_cov,S2_cov are per-replicate stat_func outputs. This is a delta-method-free, fully empirical ratio normalization rather than a Taylor-expansion delta method.
- **Vectorisation:** Generator draws ONE index matrix per call and applies to both value and cov arrays -> value/cov stay row-aligned. apply_along_axis loop for stat_func. _get_method_params intentionally OMITS random_seed (comment: avoids breaking update cycle).
- **Dependencies:** numpy, scipy.stats, BootstrapSamplesGenerator (cov_bootstrap), StatFuncApplier.

### PairedPostNormedBootstrapTest (paired post-normed bootstrap)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/paired_post_normed_bootstrap_test.py`
- **Purpose:** Paired variant; despite the name it does NOT use covariate ratio normalization -- it z-score normalizes each bootstrap distribution (standardization) before differencing.
- **Statistic:** boot_i_normed = (boot_i - mean(boot_i)) / std(boot_i) (standardize each group's bootstrap distribution), then difference/ratio.
- **CI & p-value:** CI np.quantile(boot_data,[alpha/2,1-alpha/2]); pvalue=2*min(mean(boot_data>0),mean(boot_data<0)); reject=pvalue<alpha. effect=np.mean(boot_data).
- **Effect types:** Absolute: boot_2_normed - boot_1_normed. Relative: boot_2_normed/boot_1_normed - 1 (relative on z-scored data -> mathematically dubious since z-scores are centered at 0).
- **Inputs:** Two equal-size Samples with cov_array required by validation (but cov is not actually used in the math here). Same random_seed to both generators -> paired resamples.
- **Outputs:** TestResult; effect=mean(boot_data).
- **Key formula:** boot_i_normed=(boot_i-mean(boot_i))/std(boot_i); boot_data=boot_2_normed-boot_1_normed or boot_2_normed/boot_1_normed-1.
- **Vectorisation:** Standardization is the 'post-norming'; this differs fundamentally from PostNormedBootstrapTest's covariate-ratio approach. Same seed gives paired indices. apply_along_axis loop.
- **Dependencies:** numpy, scipy.stats, BootstrapSamplesGenerator, StatFuncApplier.

### PoissonPostNormedBootstrapTest (Poisson + naming-mismatched normalization)

- **File:** `analytics-data-pipelines/app/ab_testing/processors/tests/poisson_post_normed_bootstrap_test.py`
- **Purpose:** Named 'post-normed' but the implementation is identical to PoissonBootstrapTest -- no covariate post-normalization or standardization is actually performed.
- **Statistic:** Weighted mean per replicate via Poisson(1) weights, same as PoissonBootstrapTest.
- **CI & p-value:** CI np.quantile(boot_data,[alpha/2,1-alpha/2]); pvalue=2*min(mean(boot_data>0),mean(boot_data<0)); reject=pvalue<alpha.
- **Effect types:** Absolute stat_2-stat_1; relative stat_2/stat_1-1.
- **Inputs:** Two Samples; n_samples, stratify, random_seed. No cov_array requirement enforced (no validate_samples override shown; relies on base? Actually no validate guard for cov).
- **Outputs:** TestResult; effect = original point estimate.
- **Key formula:** weights=poisson(1,(n_samples,n)); stat_i=stat_func(np.dot(weights,array_i)/weights.sum(axis=1)); boot_data per test_type. NO normalization step present despite class name.
- **Vectorisation:** Vectorized dot product. Effectively a duplicate of PoissonBootstrapTest -> the 'post-normed' label is misleading/likely a bug.
- **Dependencies:** numpy only.

### ZTest power/MDE helpers (fraction_utils)

- **File:** `analytics-data-pipelines/app/ab_testing/utils/fraction_utils.py`
- **Purpose:** MDE/sample-size/power for proportion tests using arcsine effect size.
- **Statistic:** Cohen's h via proportion_effectsize; inverse arcsine transform to recover absolute MDE.
- **CI & p-value:** N/A (power analysis, not a test).
- **Effect types:** relative: mde_absolute=prop*mde; absolute: mde_absolute=mde. MDE back-transform divides by prop for relative.
- **Inputs:** prop, size/mde, alpha, power, ratio.
- **Outputs:** int sample size / float mde / float power.
- **Key formula:** effect_size=proportion_effectsize(prop, prop+mde_absolute); size=NormalIndPower().solve_power(effect_size,power,alpha,'two-sided',ratio). MDE: effect_size=solve_power(None,power,alpha,nobs1=size,ratio); mde_absolute=sin(arcsin(sqrt(prop))+effect_size/2)**2 - prop.
- **Vectorisation:** Scalar statsmodels calls.
- **Dependencies:** numpy, statsmodels NormalIndPower, proportion_effectsize.

## Power / MDE / sample-size functions


### `get_ttest_mde / get_ttest_sample_size / get_ttest_power (sample_utils.py)`
- **File:** `analytics-data-pipelines/app/ab_testing/utils/sample_utils.py`
- **Purpose:** MDE, required sample size, and achieved power for the (Z-approx) t-test on continuous metrics.
- **Formula:** MDE: if size<=1 or std==0 return inf; effect_size = TTestIndPower().solve_power(power=power, nobs1=size, alpha=alpha, ratio=ratio, alternative='two-sided'); mean_adj = mean + effect_size*std; relative mde=(mean_adj-mean)/mean, absolute mde=mean_adj-mean; round to 4. sample_size: effect_size=abs(mean-mean_adj)/std with mean_adj=mean*(1+mde) (relative) or mean+mde (absolute); sample_size=TTestIndPower().solve_power(effect_size,power,alpha,ratio,'two-sided'); int(round()). power: effect_size=abs(mean-mean_adj)/std; power=solve_power(effect_size,nobs1=size,...).

### `get_cuped_ttest_mde / _sample_size / _power (sample_utils.py)`
- **File:** `analytics-data-pipelines/app/ab_testing/utils/sample_utils.py`
- **Purpose:** Same as t-test power funcs but with variance shrunk by the covariate correlation.
- **Formula:** adjusted_var = std**2 * (1 - correlation**2); adjusted_std = sqrt(adjusted_var). Then identical to t-test formulas but using adjusted_std in place of std. e.g. mean_adj = mean + effect_size*adjusted_std.

### `get_fraction_mde / _sample_size / _power (fraction_utils.py)`
- **File:** `analytics-data-pipelines/app/ab_testing/utils/fraction_utils.py`
- **Purpose:** Power analysis for proportions via Cohen's h arcsine effect size.
- **Formula:** effect_size = proportion_effectsize(prop, prop+mde_absolute); size = NormalIndPower().solve_power(effect_size, power, alpha, 'two-sided', ratio). MDE: effect_size = solve_power(None, power, alpha, nobs1=size, ratio); mde_absolute = sin(arcsin(sqrt(prop)) + effect_size/2)**2 - prop; relative mde = mde_absolute/prop.

### `SamplePowerAnalyzer.calculate_sample_size / calculate_mde (sample_power_analyzer.py)`
- **File:** `analytics-data-pipelines/app/ab_testing/processors/power_analyzers/sample_power_analyzer.py`
- **Purpose:** Object wrapper for continuous-metric power analysis returning a PowerAnalysisResult; mde_realism = effect_size (Cohen's d).
- **Formula:** calculate_sample_size: mean_adj=mean*(1+mde) or mean+mde; effect_size=abs(mean-mean_adj)/std; sample_size=TTestIndPower().solve_power(effect_size,power,alpha,ratio=1,'two-sided'). calculate_mde: effect_size=solve_power(power,nobs1=sample_size,alpha,ratio=1); mean_adj=mean+effect_size*std; mde=(mean_adj-mean)/mean (relative) or mean_adj-mean. NOTE: imports from 'pipelines.ab_testing...' not 'app.ab_testing...' (path inconsistency vs the rest of the package).

### `FractionPowerAnalyzer.calculate_sample_size / calculate_mde / get_sample_size / get_mde (fraction_power_analyzer.py)`
- **File:** `analytics-data-pipelines/app/ab_testing/processors/power_analyzers/fraction_power_analyzer.py`
- **Purpose:** Object wrapper for proportion power analysis; mde_realism = mde_absolute/fraction.std.
- **Formula:** sample_size: mde_absolute=prop*mde or mde; effect_size=proportion_effectsize(prop,prop+mde_absolute); size=NormalIndPower().solve_power(effect_size,power,alpha,'two-sided'). mde: effect_size=solve_power(None,power,alpha,'two-sided',nobs1=size); mde_absolute=sin(arcsin(sqrt(prop))+effect_size/2)**2 - prop. Also imports from 'pipelines.ab_testing...' (path inconsistency).

## Potential issues & improvement opportunities (blind critique)


> These feed [../specs/statistics-changes.md](../specs/statistics-changes.md). Severity is the extractor's assessment, confirmed by the quorum's stats-correctness reviewer.


- **[high] Naming vs implementation (PoissonPostNormedBootstrapTest)** — poisson_post_normed_bootstrap_test.py performs NO post-normalization at all; its compare_samples is a verbatim copy of PoissonBootstrapTest (plain Poisson weighted-mean difference). The 'post-normed' semantics (covariate ratio normalization) are entirely absent, so results are silently identical to the non-post-normed Poisson test.
    - *Fix:* Either implement covariate ratio normalization (analogous to PostNormedBootstrapTest._apply_test_function: S2 - (S2_cov/S1_cov)*S1) using Poisson-weighted covariate means, or remove the class. Add a regression test asserting it differs from PoissonBootstrapTest.

- **[high] PairedPostNormedBootstrapTest semantics** — Despite the name, it does covariate-free z-score STANDARDIZATION (boot-mean)/boot-std of each group's bootstrap distribution rather than covariate ratio normalization. The relative branch then computes boot_2_normed/boot_1_normed - 1 on z-scored data centered at ~0, so the denominator is near zero and the ratio explodes / is statistically meaningless. cov_array is required by validation but never used.
    - *Fix:* Clarify intent. If ratio-metric post-norming is intended, mirror PostNormedBootstrapTest using cov_boot. If standardization is intended, drop the relative branch (ratio of z-scores is ill-defined) and rename to avoid 'post-normed'.

- **[medium] Mislabeled test family (t-test uses Normal, not Student-t)** — TTest/PairedTTest/CupedTTest/PairedCupedTTest all build sps.norm distributions and never use Student-t (sps.t) or any degrees-of-freedom correction. For small samples this understates tail probability (too-narrow CIs, anti-conservative p-values).
    - *Fix:* Either rename to z-test/asymptotic, or use sps.t with appropriate df (Welch-Satterthwaite for the unpaired case) when sample sizes are small.

- **[medium] Inconsistent ddof in variance/covariance** — Within a single formula np.var (population, ddof=0 via Sample and direct np.var) is mixed with np.cov (sample, ddof=1 default). E.g. paired/CUPED relative_var uses var_mean (ddof=0) and covariance from np.cov (ddof=1). This biases the variance estimate inconsistently.
    - *Fix:* Standardize ddof across all variance/covariance terms (pick ddof=1 for inferential statistics) and document it.

- **[medium] apply_along_axis is not vectorized** — StatFuncApplier.process uses np.apply_along_axis(stat_func, 1, matrix), which loops in Python over n_samples rows. For n_samples=1000+ and large samples this is the dominant cost and defeats the point of preallocating big matrices. The classic-bootstrap tests (BootstrapTest, Paired, PostNormed) all pay this.
    - *Fix:* For stat_func == np.mean (the default), compute matrix.mean(axis=1) directly. Provide an axis-aware fast path and only fall back to apply_along_axis for arbitrary callables.

- **[high] Poisson bootstrap only correct for the MEAN** — PoissonBootstrap/PairedPoisson/PoissonPostNormed compute np.dot(weights, array)/weights.sum(axis=1) which is the weighted MEAN, then pass that length-n_samples vector through stat_func. If stat_func != np.mean (e.g. np.median, np.sum, a quantile), the result is stat_func applied to a vector of per-replicate means, which is NOT the bootstrap distribution of that statistic. Silent incorrectness.
    - *Fix:* Restrict Poisson tests to mean-like statistics (assert/validate stat_func is mean), or implement weighted versions of the supported statistics; document the limitation.

- **[medium] Relative-effect division-by-zero / sign instability** — Relative test_type everywhere divides by mean_1 / boot_1 / prop_1 / S1 with no guard. If the control mean is 0 or near 0 (common for sparse metrics), relative_mu and relative_var blow up or produce inf/nan; boot ratio boot_2/boot_1-1 can include divide-by-zero across replicates. ZTest relative also lacks the covariance term in its delta method (just divides std_effect by prop_1).
    - *Fix:* Add small-denominator guards / NaN handling and warnings. For ZTest relative, use a proper delta-method variance for the ratio of proportions including covariance, or document the approximation.

- **[low] BootstrapTest pvalue can be exactly 0 (no smoothing)** — pvalue = 2*min(mean(boot>0), mean(boot<0)) returns 0 when all bootstrap effects fall on one side, which is impossible to distinguish from p<1/n_samples and makes reject trivially true. Also ties at exactly 0 are counted in neither >0 nor <0.
    - *Fix:* Use the (#extreme+1)/(n+1) plug-in to bound p away from 0, and decide a tie-handling convention for boot==0.

- **[medium] Global np.random.seed side effects** — BootstrapSamplesGenerator.process and the Poisson tests call np.random.seed(random_seed) on the GLOBAL numpy RNG, mutating process-wide random state (non-reentrant, breaks parallelism and any other RNG users). Several classes also default random_seed = np.random.randint(...) which is then stored, but PostNormed deliberately omits random_seed from method_params to avoid persisting it.
    - *Fix:* Use np.random.default_rng(seed) / a local Generator instance instead of the global seed; thread the generator through the pipeline.

- **[low] Stratified weight rounding loses/changes total N** — Sample.get_category_weights uses max(1, int(v*sample_size/sample_size)) and BootstrapTest._get_category_weights uses int(sample_size*norm_weight) -> truncation plus the max(1,..) floor means the resampled total_category_size can differ from the real sample_size, subtly biasing stratified estimates and CI widths.
    - *Fix:* Use largest-remainder (Hamilton) apportionment so the per-stratum counts sum exactly to sample_size.

- **[low] BootstrapTest vs PairedBootstrapTest effect inconsistency** — BootstrapTest.effect = original point estimate (stat on raw arrays); PairedBootstrapTest.effect = np.mean(boot_data); PoissonBootstrapTest.effect = original point estimate; PairedPostNormed.effect = mean(boot_data). The 'effect' field thus means different things across methods, which will confuse downstream consumers comparing methods.
    - *Fix:* Standardize the definition of 'effect' (recommend the original-sample point estimate, with bootstrap mean reported separately as a bias diagnostic).

- **[medium] PostNormedBootstrapTest absolute formula correctness** — Absolute branch returns S2 - (S2_cov/S1_cov)*S1 which rescales the control value S1 by the covariate ratio; this is an unusual definition of an 'absolute' difference and may not equal the intended counterfactual S2 - S1_normalized. The semantics differ from the standard CUPED-style additive adjustment.
    - *Fix:* Document the exact estimand and add unit tests against a known-answer synthetic ratio metric; verify it reduces to S2-S1 when the covariate ratio is 1.

- **[medium] Import path inconsistency in power analyzers** — sample_power_analyzer.py and fraction_power_analyzer.py import models from 'pipelines.ab_testing.models...' while the rest of the package uses 'app.ab_testing.models...'. Likely an unported leftover; will ImportError in the app context.
    - *Fix:* Unify imports to 'app.ab_testing...' (or whatever the canonical root is) and add an import smoke test.

- **[low] ZTest sign mismatch between z_stat and effect** — z_stat uses (prop_1 - prop_2) but effect/CI use (prop_2 - prop_1). p-value is symmetric so unaffected, but the stored effect_distribution and z_stat have opposite orientations -- a latent foot-gun if anyone reuses z_stat.
    - *Fix:* Make orientation consistent (define test group minus control consistently everywhere).

- **[low] kstest normality check uses estimated params** — _check_normality calls sps.kstest(boot_data,'norm', args=(mean,std)) with parameters ESTIMATED from the same data; the standard KS test assumes fully specified parameters, so the reported p-value is not calibrated (Lilliefors correction needed). It only warns, but the warning threshold is misleading.
    - *Fix:* Use scipy's normaltest/Shapiro or a Lilliefors test, or drop the per-comparison KS warning.