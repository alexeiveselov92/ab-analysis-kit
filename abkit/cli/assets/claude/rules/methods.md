# abkit — Compute Methods

Each comparison in an experiment binds ONE statistical **method** to a metric.
The method turns the metric's per-unit values into an effect, a confidence
interval, a p-value, and a `reject` flag — written to `_ab_results` and rendered
in the readout. Methods are **plugins**: pick one by registry `name`, pass its
`params`; the pipeline never special-cases a method.

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

The method must match the metric's `type` (sample / fraction / ratio — see
`metrics.md`): a fraction wants `z-test`, a sample wants `t-test`/`cuped-t-test`,
a ratio wants `ratio-delta`.

## Choosing a method

| Metric shape | Method | Use when |
|---|---|---|
| proportion / rate (`fraction`) | `z-test` | binary outcome per unit (conversion, click) — normal-approx of a proportion |
| continuous mean (`sample`) | `t-test` | revenue, duration, counts per unit; the general default |
| continuous + pre-period covariate | `cuped-t-test` | variance reduction: a pre-experiment covariate is available → tighter CI, more power |
| ratio of two sums (`ratio`) | `ratio-delta` | metric is Σnum / Σden (e.g. CTR = clicks/impressions) with per-unit correlation — delta-method variance |
| any of the above, robust / non-normal | `bootstrap` | heavy tails, non-mean statistic, or you distrust the normal approx |
| Poisson / count resampling | `poisson-bootstrap` | very large N where multinomial resampling is cheaper (mean-only) |
| variance-reduced bootstrap | `post-normed-bootstrap` | bootstrap analog of CUPED (see quarantine note below) |

Quick decision: binary → `z-test`; continuous → `t-test`; have a good
pre-period covariate → `cuped-t-test`; a ratio-of-sums → `ratio-delta`;
non-normal / robust → `bootstrap`; unsure → `t-test`.

**Paired variants** (`paired-t-test`, `paired-cuped-t-test`, `paired-bootstrap`,
`paired-poisson-bootstrap`, `paired-post-normed-bootstrap`) align two arms by
unit — same-unit before/after or crossover designs. They require the metric to
yield matched units; most standard A/B tests are **unpaired** — use the plain
variant unless the design is genuinely paired.

## The 12 registered methods

| Family | Registry name | Notes |
|---|---|---|
| parametric | `t-test`, `z-test`, `cuped-t-test`, `ratio-delta` | closed-form; normal/t approx |
| parametric paired | `paired-t-test`, `paired-cuped-t-test` | align arms by unit |
| bootstrap | `bootstrap`, `poisson-bootstrap`, `post-normed-bootstrap` | resampled CI/p-value |
| bootstrap paired | `paired-bootstrap`, `paired-poisson-bootstrap`, `paired-post-normed-bootstrap` | |

## Params

**All methods** — `test_type` (`relative` default = lift over control, or
`absolute` = raw difference). The persisted `effect` and any `min_effect`
verdict threshold are in these units, so pick deliberately.

**Parametric** (`t-test`, `z-test`, `cuped-t-test`, `ratio-delta`, paired
variants):

| Param | Default | Meaning |
|---|---|---|
| `test_type` | `relative` | `relative` \| `absolute` |
| `calculate_mde` | `false` | also solve the per-arm MDE at `power` |
| `power` | `0.8` | target power for the MDE solve |
| `covariate_lookback` | — | **CUPED only**: pre-period window, e.g. `14d`. IDENTITY-BEARING |

CUPED needs **no extra SQL**: with `covariate_lookback` set, abkit re-renders the
same metric query over the pre-period window (exposure filter dropped) and uses
the pre-period value as the covariate. A different lookback is a different
covariate → a different series (see identity below).

**Bootstrap** (`bootstrap`, `poisson-bootstrap`, `post-normed-bootstrap`, paired
variants):

| Param | Default | Identity? | Meaning |
|---|---|---|---|
| `test_type` | `relative` | yes | `relative` \| `absolute` |
| `n_samples` | `1000` | yes | number of resamples |
| `stratify` | `false` | yes | resample within strata (needs `strata` on the metric) |
| `weight_method` | `min` | yes | how per-stratum weights pool (`min`/`mean`; not on Poisson) |
| `stat` | `mean` | yes | statistic resampled (`mean`/`median`; Poisson is mean-only) |
| `pvalue_kind` | baseline | yes | opt-in `plugin` = `(#extreme+1)/(n+1)` p-value |
| `seed` | — | **no** | RNG seed — identity-EXCLUDED (see below) |
| `max_block_bytes` | — | no | memory cap for the resample matrix; never changes results |

## `method_config_id` — identity & orphaning

`method_config_id` is a hash of the method `name` + its **non-default identity
params**. It is the key of a result *series* in `_ab_results`.

- **Editing an identity param ORPHANS the prior series.** Changing `test_type`,
  `n_samples`, `covariate_lookback`, `stratify`, etc. starts a *new*
  `method_config_id`; the old rows stay stranded. After retuning, recompute
  (`abk run --select <exp>`) and prune the orphans with
  `abk clean --select <exp> --execute`.
- **`seed` is identity-EXCLUDED.** Two runs at the same config produce
  byte-identical bootstrap rows regardless of seed — abkit derives a
  deterministic per-row seed from the row's identity (experiment, metric, arms,
  cutoff, `n_samples`). Bootstrap results are reproducible, not random per run.
- **`alpha` is NOT in identity.** It is the post-correction, experiment-level
  significance level (two-tier Bonferroni: main vs secondary metrics land at
  different alphas; read-time BH across a family). Changing `alpha` re-decides
  `reject` without orphaning the series.
- Execution-only params (`max_block_bytes`) never enter the hash.

## Sequential eligibility

The opt-in always-valid (peeking-safe) mode (`sequential: {enabled: true}` on
the experiment) applies only to **parametric methods** (`t-test`, `z-test`,
`cuped-t-test`, `ratio-delta` and their paired variants). **Bootstrap methods
are NOT sequential-eligible** — enabling sequential leaves their CIs
fixed-horizon, and the readout still withholds WIN/LOSE AND FLAT before the
horizon. If
you need peeking-valid early reads, choose a parametric method. See
`experiments.md` for the toggle and `overview.md` for why peeking matters.

## Quarantined branches (raise, never silently substitute)

Some legacy method/param combinations are known-broken and raise
`QuarantinedMethodError` at construction rather than returning a wrong number:

- `post-normed-bootstrap` with `test_type: absolute` (unusual legacy estimand)
- `paired-post-normed-bootstrap` with `test_type: relative` (denominator ~0, the
  ratio explodes)
- `poisson-post-normed-bootstrap` — the **whole method** is quarantined (raises at
  construction): the legacy class did NO post-normalisation, it is a verbatim copy
  of `poisson-bootstrap`. Use `poisson-bootstrap` (identical behaviour) or
  `ratio-delta` for ratio metrics.

The error message names the fix (usually the other `test_type`, or
`ratio-delta`/`cuped-t-test` for the principled path). abkit never silently swaps
in a different estimator — see `statistics-changes.md §3`.

## Gotchas

- **Match method to metric type**: `z-test`↔fraction, `t-test`/`cuped-t-test`↔
  sample, `ratio-delta`↔ratio. A mismatch is a config error.
- **Never change a number silently.** The math reproduces a captured legacy
  baseline (golden-tested); any deviation is an owned `ALGORITHM_VERSION` bump +
  a documented change + A/A re-validation. As a user you tune *config*, not the
  estimator internals.
- **After choosing/tuning a method, validate it on your data.** Run
  `abk validate` (the A/A matrix) to confirm the method's false-positive rate ≈
  α and it has power on *this* cohort — see `validate.md`. Use `abk explore` to
  turn the knobs live against your real series before committing.
- **Bootstrap is slower and heavier** than the closed-form methods; prefer a
  parametric method unless robustness genuinely requires resampling.
