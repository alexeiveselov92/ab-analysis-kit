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
| `interval` | `pooled` | **`z-test` only**: `pooled` (legacy) \| `score`. IDENTITY-BEARING |
| `interval` | `delta` | **mean methods**: `delta` (legacy) \| `fieller` — the RELATIVE interval. IDENTITY-BEARING |

`interval: score` swaps the z-test's interval for the inversion of the test it
already runs (Miettinen–Nurminen). **P-values do not change** — the statistic at
the null is the same pooled z — with ONE exception, which is a fix: a table with no
conversions in either arm has no pooled statistic at all, and now reports `p = 1`
beside a real interval instead of a blank row (visible under `test_type: absolute`;
under `relative` a lift over a zero baseline stays undefined). Otherwise the interval
becomes valid at every effect size instead of only at zero, asymmetric (render it
`[low, high]`, never `±`), and inside a possible range. It matters most where the arms are IMBALANCED: at a
900/100 holdout the pooled SE is 24% too small, i.e. the legacy interval is
anti-conservative, and worse as the correction shrinks alpha. Two constraints: it
is identity-bearing (switching starts a new series), and it CANNOT be combined
with `sequential: {enabled: true}` — config validation refuses the pair (as do the
explore knob and its Apply seam), because the always-valid transform needs a
symmetric interval. `abk validate` still scores such a metric: it simply has no
always-valid column, exactly like a bootstrap method. On a relative metric with
few CONVERSIONS (not few users — the precision law reads counts) the row carries a
"weakly identified" warning; the interval is still reported.

`interval: fieller` (m13 STAT-4) does the same thing for the mean methods'
RELATIVE branch: it inverts the test at every candidate lift instead of building
`θ̂ ± z·SE` at the observed one. Here the p-value DOES move — under `fieller` the
relative p-value is the ABSOLUTE comparison's, which is the point: "the lift is 0"
and "the difference is 0" are one hypothesis, and the legacy branch gives them two
answers that can disagree in a report. The reported lift is unchanged. What it
fixes is one-sided: delta's two-sided coverage is nominal while its TAILS are
0.017/0.033 at a control-mean CV of 5% (0.008/0.039 at 10%), and every verdict
(WIN/LOSE) is a one-sided claim — so the real directional error rate is up to 1.6×
the configured one, at every true effect, which is why an A/A run cannot see it.
The honest cost: when the control mean is not clearly different from zero, NO
bounded lift interval exists at that level (a theorem, not a limit), and abkit
reports the effect and p-value with EMPTY bounds plus a warning rather than a
finite interval it cannot stand behind — the readout then treats the row as a gap,
so such a comparison is not called a WIN. Same three constraints as `score`:
identity-bearing, incompatible with `sequential: {enabled: true}`, and — because
it only governs the relative branch — writing it beside `test_type: absolute` is a
config ERROR, not a silent no-op (it would fork the series for nothing).

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
  `n_samples`, `covariate_lookback`, `interval`, `stratify`, etc. starts a *new*
  `method_config_id`; the old rows stay stranded. After retuning, recompute
  (`abk run --select <exp>`) and prune the orphans with
  `abk clean --select <exp> --execute`.
- **`seed` is identity-EXCLUDED.** Two runs at the same config produce
  byte-identical bootstrap rows regardless of seed — abkit derives a
  deterministic per-row seed from the row's identity (experiment, metric, arms,
  cutoff, `n_samples`). Bootstrap results are reproducible, not random per run.
- **`alpha` is NOT in identity.** It is the post-correction, experiment-level
  significance level (two-tier Bonferroni: main vs secondary metrics land at
  different alphas; the read-time schemes BH / Holm decide over the whole family
  instead and leave the raw alpha on the row). Changing `alpha` re-decides
  `reject` without orphaning the series — and under a read-time scheme `reject`
  is the pre-family flag, not the verdict.
- **`guardrail_correction: none`** (project or experiment level; default
  `inherit` = the old behaviour) takes guardrails OUT of the corrected family:
  they are tested at the raw alpha, because correcting a metric whose job is to
  catch harm makes you less likely to catch it. It also **loosens the secondary
  alpha** for the screening metrics that remain, since guardrails stop counting
  in the divisor. Inert unless `correction: bonferroni`. It moves persisted
  numbers, so it writes new-alpha rows into an existing series — run
  `abk run --full-refresh` if you want the whole series at one alpha, and expect
  guardrail `_ab_aa_runs` rows to read `alpha_mismatch` until `abk validate`
  re-runs them.
- **`contrasts: vs_control`** (experiment level; default `all_pairs` = the old
  behaviour) declares that the family is the `g-1` contrasts against the CONTROL
  arm (`assignment.control`, default the first declared variant), not all
  `C(g,2)` pairs. The divisor drops accordingly, so
  every tier's alpha is multiplied by `g/2` (≈ +10 points of power at four
  arms). It is one declaration with two halves: the treatment-vs-treatment pairs
  are also **not computed** — no result rows, no verdicts, no BH family members
  — because keeping them at the loosened level is exactly what would break the
  FWER claim the loosening was bought with. Inert at two arms. Like
  `guardrail_correction`, it moves persisted alphas, so `abk run --full-refresh`
  re-homogenises a series and A/A rows read `alpha_mismatch` until re-run;
  rows for pairs the declared family no longer claims are ignored loudly by every
  read surface — three causes now: a renamed arm, this narrowing, and a declared
  `assignment.control` re-orienting a pair. (`abk run --full-refresh --from
  <start> --to <past the horizon>` removes them; `--to` is EXCLUSIVE on `end_ts`
  and the horizon cutoff's `end_ts` IS `horizon_ts`, so naming the horizon leaves
  the last look behind. `abk clean` prunes by `method_config_id` and never
  touched them.) Widening the
  family back (or adding an arm) is the direction abkit heals for you: a look
  missing a declared pair is re-planned automatically.
- Execution-only params (`max_block_bytes`) never enter the hash.

## Sequential eligibility

The opt-in always-valid (peeking-safe) mode (`sequential: {enabled: true}` on
the experiment) applies only to **parametric methods** (`t-test`, `z-test`,
`cuped-t-test`, `ratio-delta` and their paired variants). **Bootstrap methods
are NOT sequential-eligible** — enabling sequential leaves their CIs
fixed-horizon, and the readout still withholds WIN/LOSE AND FLAT before the
horizon. If
you need peeking-valid early reads, choose a parametric method.

The two inverted-test intervals are the parametric exceptions, and they fail
LOUDLY rather than silently: `z-test` with `interval: score` and the mean methods
with `interval: fieller` are both asymmetric, so either pair is a config ERROR
naming both settings. See `experiments.md` for the toggle and
`overview.md` for why peeking matters.

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
