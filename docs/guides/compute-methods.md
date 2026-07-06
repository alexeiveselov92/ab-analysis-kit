# Compute methods

Every comparison in an experiment binds **one statistical method** to a
[metric](metrics.md). The method is the `compute` stage of abkit's
`load → compute → readout` pipeline: it turns a metric's per-unit values into an
**effect**, a **confidence interval**, a **p-value**, and a `reject` flag, writes
them to `_ab_results`, and hands them to the readout that renders the
WIN / LOSE / FLAT / INCONCLUSIVE verdict.

Methods are **plugins**. You pick one by its registry `name` and pass its
`params`; the pipeline, database layer, and CLI never special-case a method name.
This page is the catalogue: the 12 registered methods, when to use each, their
params, the identity rules that decide when a result series is orphaned, and the
legacy branches that are deliberately quarantined.

## Binding a method to a metric

A method lives inside a comparison, under the experiment YAML's `comparisons`
list:

```yaml
comparisons:
  - metric: example_signup_cr
    is_main_metric: true
    method: {name: z-test, params: {test_type: relative, calculate_mde: true}}

  - metric: example_arpu
    method:
      name: cuped-t-test
      params: {test_type: relative, covariate_lookback: 14d}
```

`method` is a `{name, params}` object (`abkit/config/method_config.py`). Params
are validated **at config-validation time by instantiating the method** — an
unknown param, a bad value, or a quarantined combination fails when you run
`abk run` or `abk validate`, not silently at compute time.

## Match the method to the metric shape

A method declares which container family it consumes (`input_kind`), and it must
match the metric's `type` or the config layer rejects the experiment
(declarative-config §8):

| Metric `type` | `input_kind` | Method family |
|---|---|---|
| `fraction` | `fraction` | `z-test` (proportion / rate) |
| `sample` | `sample` | `t-test`, `cuped-t-test`, `bootstrap`, `poisson-bootstrap`, `post-normed-bootstrap` |
| `ratio` | `ratio` | `ratio-delta` |

A mismatch (e.g. a `z-test` on a `sample` metric) is a config error with an
explicit message. See [metrics](metrics.md) for how a metric's `type` is set.

## The 12 registered methods

| Family | Registry `name` | Aliases | Params beyond `test_type` |
|---|---|---|---|
| parametric | `t-test` | `ttest` | `calculate_mde`, `power` |
| parametric | `z-test` | `ztest` | `calculate_mde`, `power` |
| parametric | `cuped-t-test` | `cuped-ttest` | `calculate_mde`, `power`, `covariate_lookback` |
| parametric | `ratio-delta` | — | (none) |
| parametric, paired | `paired-t-test` | `paired-ttest` | (none) |
| parametric, paired | `paired-cuped-t-test` | — | `covariate_lookback` |
| bootstrap | `bootstrap` | `bootstrap-test` | `n_samples`, `stratify`, `weight_method`, `stat`, `pvalue_kind`, `seed`, `max_block_bytes` |
| bootstrap | `poisson-bootstrap` | — | `n_samples`, `stratify`, `stat`, `pvalue_kind`, `seed`, `max_block_bytes` |
| bootstrap | `post-normed-bootstrap` | — | as `bootstrap` (relative-only — see quarantine) |
| bootstrap, paired | `paired-bootstrap` | — | as `bootstrap` |
| bootstrap, paired | `paired-poisson-bootstrap` | — | as `poisson-bootstrap` |
| bootstrap, paired | `paired-post-normed-bootstrap` | — | as `bootstrap` (absolute-only — see quarantine) |

Names are case-insensitive and normalise `_` to `-` (`Z_Test` → `z-test`).

## Choosing a method

Quick decision:

- Binary outcome per unit (conversion, click-through) → **`z-test`** — normal
  approximation of a proportion.
- Continuous mean per unit (revenue, duration, counts) → **`t-test`** — the
  general default.
- Continuous **and** you have a pre-experiment covariate → **`cuped-t-test`** —
  variance reduction gives a tighter CI and more power at the same N.
- Metric is a ratio of two sums (CTR = Σclicks / Σimpressions) with per-unit
  correlation → **`ratio-delta`** — delta-method variance.
- Heavy tails, a non-mean statistic, or you distrust the normal approximation →
  **`bootstrap`**.
- Unsure → **`t-test`**.

Bootstrap methods are **slower and more memory-hungry** than the closed-form
parametric methods. Prefer a parametric method unless robustness genuinely
requires resampling.

## Parametric methods

Closed-form estimators (normal / t approximation), golden-tested at relative
`1e-9` against the captured legacy baseline (statistics-baseline §3).

| Param | Applies to | Default | Meaning |
|---|---|---|---|
| `test_type` | all | `relative` | `relative` = lift over control; `absolute` = raw difference |
| `calculate_mde` | `t-test`, `z-test`, `cuped-t-test` | `false` | also solve the per-arm MDE at `power` |
| `power` | `t-test`, `z-test`, `cuped-t-test` | `0.8` | target power for the MDE solve (must be in `(0, 1)`) |
| `covariate_lookback` | `cuped-t-test`, `paired-cuped-t-test` | — | pre-period covariate window, e.g. `14d` — **identity-bearing** |

`ratio-delta` and the paired variants take only `test_type`.

### CUPED — variance reduction with no extra SQL

`cuped-t-test` reduces variance by regressing out a pre-experiment covariate.
With `covariate_lookback` set, abkit re-renders **the same metric query** over the
pre-period window (with the exposure filter dropped) and uses the pre-period value
as the covariate — you write no additional SQL (declarative-config §3;
statistics-changes §5).

The lookback is a **fixed whole-day window**: it must be a whole number of days
and at least `1d` (`14d`, `28d`); a sub-day or fractional-day value is a config
error, and a lookback under `7d` warns. Because the covariate *is* the pre-period
render, a different lookback is a different covariate — and therefore a different
result series (see identity below).

## Bootstrap methods

Resampling estimators with a percentile CI and a sign-based p-value, reproduced
verbatim from the legacy engine (statistics-baseline §4). Results are
**reproducible, not random** — see `seed` below.

| Param | Default | Identity? | Meaning |
|---|---|---|---|
| `test_type` | `relative` | yes | `relative` \| `absolute` |
| `n_samples` | `1000` | yes | number of resamples (≥ 1) |
| `stratify` | `false` | yes | resample within strata (needs a strata column on the metric) |
| `weight_method` | `min` | yes | how per-stratum weights pool (`min` / `mean`); **not on Poisson** |
| `stat` | `mean` | yes | statistic resampled: `mean` or `median` built in (Poisson is **mean-only**) |
| `pvalue_kind` | `sign` | yes | `sign` = legacy `2·min(P(boot>0), P(boot<0))` (default); `plugin` = `(#extreme+1)/(n+1)` smoothing (statistics-changes §2) |
| `seed` | — | **no** | RNG seed — **identity-excluded** |
| `max_block_bytes` | — | no | memory cap for the resample matrix; never changes results |

`poisson-bootstrap` drops `weight_method` — it reweights with independent
Poisson(1) draws instead of index resampling, and post-stratification uses a
fixed 1/count unit scale rather than pooled per-stratum weights — and is
mean-only; it is cheaper than `bootstrap` at very large N.
`post-normed-bootstrap` divides out a covariate ratio for variance reduction (the
bootstrap analogue of CUPED) and requires a covariate on the metric.

### Bootstrap results are deterministic

`seed` is **identity-excluded**: two runs at the same config produce
byte-identical bootstrap rows regardless of the seed value, because abkit derives
a deterministic per-row seed from the row's identity (experiment, metric, arms,
cutoff, `n_samples`). You get reproducibility for free; changing `seed` never
starts a new series and never re-shuffles a published one.

## Paired variants are notebook-only

The five paired methods (`paired-t-test`, `paired-cuped-t-test`,
`paired-bootstrap`, `paired-poisson-bootstrap`, and
`paired-post-normed-bootstrap`) align two arms by unit for same-unit
before/after or crossover designs. **The v1 declarative pipeline serves
independent-arm experiments only** — configuring a paired method in an experiment
YAML is a validation error that points you at the notebook API. Use a plain
(unpaired) variant unless the design is genuinely paired, and reach for the
paired methods through `abkit.stats` directly in a notebook.

## `test_type`: relative vs absolute

`test_type` controls the estimand for every method. `relative` (the default)
reports lift over control; `absolute` reports the raw difference. The persisted
`effect` — and any `min_effect` FLAT-verdict threshold you set on the comparison —
is in **these units**, so choose deliberately: a `min_effect: 0.02` means "2 %
lift" under `relative` but "0.02 raw units" under `absolute`.

## `method_config_id` — identity and orphaning

Each result *series* in `_ab_results` is keyed by a `method_config_id`: a
SHA-256 of the method `name` plus its **non-default identity params**, with
`ALGORITHM_VERSION` appended only when greater than 1 (declarative-config §7):

```
method_config_id = sha256( name + json_dumps_sorted(non-default identity params) + [ALGORITHM_VERSION] )
```

- **Editing any non-default identity param orphans the prior series.** Changing
  `test_type`, `calculate_mde`, `power`, `n_samples`, `stratify`, `weight_method`,
  `stat`, `pvalue_kind`, or `covariate_lookback` away from its default produces a
  *new* `method_config_id`; the old cumulative rows stay stranded. After retuning a method, recompute the experiment
  (`abk run`) and prune orphans with `abk clean` (see [the CLI guide](../reference/cli.md)).
- **`seed` and `max_block_bytes` are identity-excluded** — they never change the
  series identity (`seed` because results are deterministic, `max_block_bytes`
  because it only bounds memory).
- **`alpha` is not identity, either.** It is the post-correction,
  experiment-level significance level: two-tier Bonferroni splits main vs
  secondary metrics to different alphas, and read-time Benjamini–Hochberg is
  applied across the family. Changing `alpha` re-decides `reject` without
  orphaning the series.

## Sequential (peeking-safe) eligibility

The opt-in always-valid confidence sequence — enabled per experiment with
`sequential: {enabled: true}` — makes early reads peeking-safe (statistics-changes
§4; see [experiments](experiments.md)). It applies **only to parametric methods**,
which have the symmetric normal CI the transform inverts. **Bootstrap methods are
not sequential-eligible**: their percentile CIs are asymmetric, so enabling
sequential leaves them fixed-horizon and the readout still withholds
WIN / LOSE / FLAT before the horizon. If you need valid early stopping, choose a
parametric method.

## Quarantined branches

Some legacy method/param combinations are known to be numerically broken. abkit
**raises `QuarantinedMethodError` rather than returning a wrong number** — it never
silently substitutes a different estimator (statistics-changes §3):

- **`poisson-post-normed-bootstrap`** — the **whole method** is quarantined. The
  legacy class did no post-normalisation (it was a verbatim copy of
  `poisson-bootstrap`), so the name refuses to resolve. Use `poisson-bootstrap`
  for identical behaviour, or `ratio-delta` for a principled ratio estimator.
- **`post-normed-bootstrap` with `test_type: absolute`** — the legacy absolute
  branch is an unusual estimand; only `relative` is reproduced. Use `relative`,
  or `ratio-delta`.
- **`paired-post-normed-bootstrap` with `test_type: relative`** — the legacy
  relative branch divides by a near-zero denominator (the ratio explodes); only
  `absolute` is reproduced. Use `absolute`, or `ratio-delta` for a ratio metric.

Each error message names the fix. This is a facet of a hard project rule: a
statistical number never changes silently — any deviation from the baseline is an
owned `ALGORITHM_VERSION` bump plus an A/A re-validation.

## After choosing a method, validate it

Picking a method is a hypothesis; confirm it on **your** cohort:

- Run [`abk validate`](validate.md) — the A/A false-positive matrix. It uses
  placebo label-permutation splits of your own data to confirm the method's
  false-positive rate is ≈ α and that it has power on this cohort. It is not a
  linter; it exercises the actual estimator.
- Use [`abk explore`](explore.md) to turn `test_type`, `n_samples`, alpha, and the
  method choice live against your real series before committing the config.
