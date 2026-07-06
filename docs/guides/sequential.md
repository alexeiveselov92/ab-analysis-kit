# Sequential (always-valid) analysis

abkit recomputes each experiment as a **cumulative series**: `[start .. start+1d]`,
`[start .. start+2d]`, and so on to the horizon (`end_date`). Every cutoff produces
a fresh effect, confidence interval, and p-value — the stabilization chart you see
in [`abk explore`](explore.md).

That series is a temptation. The natural move is to watch it and stop the moment a
CI clears zero. **With the default fixed-horizon confidence intervals, that is a
statistical error.** Each look is its own 5%-false-positive coin flip; looking many
times and stopping on the first "significant" one — *peeking* — drives the real
false-positive rate far above your nominal α. `abk validate` measures exactly how
far (often 2-3× on a daily grid; see [validate](validate.md)).

Sequential analysis is abkit's opt-in fix. Turning it on replaces the per-cutoff
fixed CI with an **always-valid confidence sequence** that is honest at *every*
look — so you can peek continuously and stop whenever the interval excludes zero,
without inflating your error rate. This page covers when to reach for it, how to
turn it on, and what changes when you do.

## The default is fixed-horizon, and it is peeking-invalid on purpose

Out of the box, `sequential.enabled` is `false`. Every row carries
`ci_kind: fixed`, and the readout **refuses to call WIN, LOSE, or FLAT before the
planned horizon** — a pre-horizon fixed-CI series is informational only
(data-contract-and-reporting §1; the numeric rule is m3-implementation-plan D5(d)).
This is the "peeking is the product" discipline: rather than let you stop early on
an invalid CI, abkit withholds the verdict until `is_horizon`, and the readout says
so in its rationale:

```
pre-horizon: latest cutoff covers 3.0 of 14.0 planned days and fixed-horizon
CIs are not peeking-valid — WIN/LOSE/FLAT withheld until the horizon (enable
`sequential: {enabled: true}` on a sequential-eligible method for peeking-valid
early readouts)
```

FLAT is withheld too — deciding "no effect, ship neither" early is equally a stop
decision. If you want early decisions, you must adopt an always-valid CI, which is
the whole point of this mode.

## Turning it on

Sequential mode is an experiment-level toggle in the experiment YAML
(`abkit/config/experiment_config.py`, `SequentialConfig`):

```yaml
name: pricing_test
start_date: 2024-07-01
end_date:   2024-07-14
unit_key:   user_id

sequential:
  enabled: true          # opt in to always-valid CIs (default: false)
  scheme: always_valid   # the only implemented scheme (this is the default)

assignment:
  query_file: sql/assignment.sql
  variants: [control, treatment]
  expected_split: {control: 0.5, treatment: 0.5}

comparisons:
  - metric: revenue_per_user
    is_main_metric: true
    method: {name: t-test, params: {test_type: relative}}
```

There are exactly two keys:

- **`sequential.enabled`** (`bool`, default `false`) — the switch.
- **`sequential.scheme`** (default `always_valid`) — see
  [scheme: alpha_spending is not implemented](#scheme-alpha_spending-is-not-implemented)
  below. `always_valid` is the only value M5 supports.

`ci_kind` is **not** something you set — it is the mode abkit *stamps on each row*.
When sequential mode is active and the method is eligible, rows persist with
`ci_kind: always_valid`; otherwise `ci_kind: fixed`. The readout, the report, and
BI all key off that persisted column, so `ci_kind` is how you *confirm* the mode
took effect, not how you request it.

## What always-valid mode actually is

It is an **experiment-level MODE transform, not a new method** (statistics-changes
§4.1). abkit does not add a registry entry or special-case any method name — it
takes whatever `(effect, SE)` your configured method already produced and widens
the interval into an always-valid confidence sequence.

Concretely, it is an *asymptotic Gaussian confidence sequence* (Waudby-Smith &
Ramdas 2021 — the Robbins/Howard normal-mixture applied to the estimate). With
`V = SE²` and a fixed mixture variance `τ²`, the half-width is

```
r = sqrt( (2·V·(V + τ²) / τ²) · ( ln(1/α) + 0.5·ln((V + τ²)/V) ) )
```

The underlying mixture likelihood ratio is a non-negative martingale, so by
Ville's inequality the sequence covers the true effect **simultaneously at every
look** with probability ≥ 1 − α. The always-valid p-value is its dual and satisfies
`p ≤ α` exactly when the interval excludes zero — the same "does the CI cross zero"
reading you already use, now valid under optional stopping.

Two facts worth internalizing:

- **The always-valid CI is always strictly wider than the fixed CI.** That extra
  width is the honest price of anytime validity — roughly **~1.55× the fixed
  half-width** at the reference look, and somewhat wider (≈1.6-1.9×) at later
  looks with more data (statistics-changes §4.1). You trade a bit of width for the
  right to look whenever you want.
- **The guarantee is exact if the estimate were exactly Gaussian, and
  asymptotic-anytime in practice.** It is *not* claimed as an exact finite-sample
  mSPRT — the pure stats core only exposes `(effect, SE)` per look, not the raw
  observation stream an exact mSPRT would need. Whether the real peeking FPR
  actually returns to ≈ α is something you *measure*, not assume — that is what
  `abk validate`'s sequential column is for (see below).

## What changes in the readout

Under `ci_kind: always_valid`, the readout **lifts its pre-horizon refusal** for
that pair: a decisive verdict reached before the horizon is legitimate, and it is
annotated as such (`abkit/pipeline/readout.py`):

```
called before the planned horizon under an always-valid confidence sequence —
peeking-safe by construction (its cumulative-peeking FPR is measured by `abk validate`)
```

Everything else in the decision order still applies — the SRM gate, the
insufficient-data demotion, and the elapsed-time stabilization window (the effect
must stay significant and same-signed across the trailing `readout.stabilization_days`,
default 7). Sequential mode makes an early WIN/LOSE *permissible*; it does not skip
the other guardrails.

## Which methods are eligible

Eligibility is a declarative flag on the method (`BaseMethod.supports_sequential`),
not a name check. The widening works by inverting the symmetric fixed CI to recover
`SE` — which only works for the **parametric, symmetric-normal family**:

| Sequential-eligible (parametric) | Not eligible (bootstrap) |
|---|---|
| `t-test`, `paired-t-test`, `z-test`, `cuped-t-test`, `paired-cuped-t-test`, `ratio-delta` | `bootstrap`, `paired-bootstrap`, `poisson-bootstrap`, `paired-poisson-bootstrap`, `post-normed-bootstrap`, `paired-post-normed-bootstrap` |

Bootstrap methods report an **asymmetric percentile CI**, so the SE is not
recoverable by inversion and the transform cannot apply. If you set
`sequential.enabled: true` on a bootstrap comparison, those rows stay `ci_kind:
fixed` and the pre-horizon refusal still holds — the readout adds a caveat naming
the reason. **If you need peeking-valid early reads, choose a parametric method**
(see [compute methods](compute-methods.md)).

## Toggling re-plans the whole series (self-invalidation)

`sequential.enabled` is *not* part of a method's identity (`method_config_id`), so
flipping it does not orphan rows — but a series computed under fixed CIs cannot be
mixed with one computed under always-valid CIs. abkit handles this automatically:
on a **bare `abk run`**, the driver compares the mode each cutoff *would* be
stamped with against the mode already persisted, and if they disagree it
**force-re-plans the entire series** for that pair (`abkit/pipeline/driver.py`):

```
MODE  pricing_test/revenue_per_user: sequential mode changed
      (now always_valid) — re-planning ...
```

The re-plan is idempotent: after it runs, every cutoff carries the new `ci_kind`
and the next bare `abk run` is a no-op. So the workflow to switch modes is simply:
edit the YAML, run `abk run`. No `--full-refresh`, no manual cleanup.

## The weekly-cycle representativeness chip

Always-valid mode lets you *decide* early, but an early cumulative estimate still
describes only the population exposed **so far** — heavy users first, one timezone
slice, novelty effects — not steady state. This is a display-honesty concern, not a
validity one (under H0, randomization keeps both arms identically mixed, so the test
is not biased — early points are just noisy and unrepresentative).

So any decisive verdict called before one full weekly cycle (`WEEKLY_CYCLE_DAYS =
7`) carries a caveat chip naming the coverage fraction:

```
covers 43% of a weekly cycle — day-of-week effects may not be represented
```

It appears in the readout and the HTML report. Treat it as a
prompt to confirm the effect holds across a full week before you fully commit,
especially for metrics with strong weekday/weekend structure.

## Sub-day cadence: anytime-valid multinomial SRM

Sequential mode is the sanctioned path for **impatient, sub-day cadences**. A
coarsening `cadence` schedule (dense early, then daily) can produce dozens of looks
per day — exactly the peeking regime that fixed CIs cannot survive
(cumulative-intervals §6). A sub-day cadence (densest step `< 1d`) also **requires
`data_lag`** — you must declare your ingestion SLA so "which cutoffs are complete"
is deterministic (see [experiments](experiments.md)):

```yaml
cadence:
  - {every: 1h, until: 48h}   # hourly for the first two days
  - {every: 1d}               # then daily to the horizon
data_lag: 30m                 # required when cadence < 1d
sequential: {enabled: true}   # the honest way to read a dense grid
```

Running a sub-day grid *without* `sequential.enabled` is allowed but is
**monitoring mode**: rows stay `ci_kind: fixed`, the readout still withholds
pre-horizon verdicts, and the fixed band renders de-emphasized in explore. The
counts and SRM stay visible (hour-grain SRM and logging-bug detection is the real
sub-day payoff); inference is what's withheld. Looks below `min_units_per_arm`
(project default 100) are further **demoted** to `insufficient_data` — the row is
written with NULLed test columns so counts and SRM remain, but no verdict is
attempted.

One thing switches over automatically at sub-day grain: **the SRM gate**. Peeking
a χ² goodness-of-fit test dozens of times a day would itself throw false SRM
alarms, so for `cadence < 1d` abkit swaps χ² for an **anytime-valid
Dirichlet-multinomial e-process** (Lindon & Malek, NeurIPS 2022), valid at every
look by construction (statistics-changes §4.2). This is **cadence-dispatched by the
driver, never configured** — you do not select it and there is no method name for
it; daily-and-coarser experiments keep the χ² gate. Both use the same strict
`0.001` gate.

## `scheme: alpha_spending` is not implemented

`SequentialConfig.scheme` accepts `always_valid` (the default and only working
value) or `alpha_spending`. The latter — classic group-sequential / alpha-spending
boundaries — is **deferred to a future release** and fails cleanly at config
validation:

```
scheme: alpha_spending (group-sequential) is not implemented — a future item,
no version promise; use scheme: always_valid (the mSPRT/asymptotic always-valid
mode)
```

Use `always_valid`. It holds at *any* data-dependent look schedule, whereas
alpha-spending assumes a small, pre-committed look grid — a poor fit for the dense,
open-ended peeking abkit is built around.

## Measure it before you trust it

Sequential mode *should* pull the peeking FPR back to ≈ α, but abkit's discipline is
to measure, never assert. With `sequential: {enabled: true}` on an eligible method,
[`abk validate`](validate.md) adds an **always-valid column beside** the fixed
peeking column of the A/A matrix (the D8 side-by-side):

| metric (kind) | method | peeking FPR (fixed) | peeking (always-valid) | CI width fixed → AV |
|---|---|---|---|---|
| `ctr` (ratio) | `ratio-delta` | 12.7% ⚠ | ~5% ✅ | 1.0× → ~1.6× |

The fixed column *diagnoses* the peeking trap; the always-valid column shows the
defense working — at the cost of the wider interval. Run `abk validate` on your
experiment's own cohort and grid before you rely on early stopping in production.

## See also

- [Experiments](experiments.md) — the full experiment YAML, cadence, `data_lag`,
  and horizon.
- [Compute methods](compute-methods.md) — which methods are sequential-eligible and
  why bootstrap is not.
- [`abk validate`](validate.md) — the A/A false-positive matrix and the sequential
  peeking column.
- [Explore](explore.md) — the live cockpit where the stabilization series and the
  SRM flag surface.
