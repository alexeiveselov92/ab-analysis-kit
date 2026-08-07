# Pre-launch planning (abk plan)

`abk plan` answers one question **before any experiment data lands**: *is this
experiment worth launching, and how big must it be?* It is a read-only power and
sizing calculator that reports, per comparison, the **required sample size**, the
**achievable MDE**, and the **achieved power** — all at the exact effective alpha
your `abk run` and readout will use (cli-and-dx §1).

Think of it as the pre-launch sibling of [`abk validate`](validate.md): validate
audits a *live* cohort's false-positive rate after data lands; plan sizes the
experiment *before* it starts.

## It is strictly read-only

`abk plan` takes **no lock**, writes **nothing** to the `_ab_*` tables, and closes
its own database connection in a `finally`. It only *reads* the latest persisted
baseline moments (and even that is optional — see below). It is safe to run against
a production warehouse at any time, including while a real experiment is live.

This is a deliberate contract, not an accident: sizing must never mutate state.

## What it reports (per comparison)

For each comparison it can size, `abk plan` gives you three numbers:

| Answer | Question it answers |
|---|---|
| **required-N / arm** | How many units per control arm are needed to detect the target MDE at the configured power and alpha, at the experiment's `expected_split` allocation. |
| **achievable MDE** | The smallest effect the *current* sample size could detect — a retrospective bound on what you can already resolve. |
| **achieved power** | The power for the target MDE at the current sample size. |

All three are computed at the **effective per-comparison alpha** — the two-tier
Bonferroni resolve, so main metrics and secondary/guardrail metrics get different
alphas (the header echoes the split). See [Corrections](configuration.md) for the
two-tier scheme.

Each sized line ends with `✓ powered` (current N already meets required-N) or
`✗ underpowered`.

## Where the baseline comes from

Sizing needs per-arm moments — a mean and standard deviation for a `sample` metric,
or a proportion for a `fraction` metric. `abk plan` draws them from one of three
sources, in this order — and the plan line always names the one it used:

1. **`--baseline` override** — for a greenfield experiment with no data yet (or to
   override a stale persisted look), you supply the moments by hand (grammar below).
   An explicit number is the operator's deliberate statement, so it wins.
2. **`--from-history <N d>`** — read each metric over the **N whole days before the
   experiment's start** and derive the moments from that (labeled `history 14d @ …`).
   This is the pre-launch answer: the experiment has enrolled nobody, so the read is
   **population-wide** — it measures everything the metric SQL yields, not the units
   this experiment will actually enroll, and the line says so. When
   `assignment.added_filters` narrows the real cohort, a population read cannot apply
   it, and the line says that too. Strictly better than a hand-typed guess, strictly
   worse than a pilot run.
3. **Persisted** — the most recent *usable* `_ab_results` row for the control
   (`assignment.control`, default the first declared variant) against the first
   declared treatment (labeled `persisted @ <ts>` in the output). This
   requires at least one `abk run` to have landed. Rows flagged as
   insufficient-data or with a null value are skipped; the latest data-rich look
   wins.

If **none** is available, that comparison is reported
`SKIPPED: no baseline — run abk run first, or pass --baseline ...`. `abk plan`
never guesses a baseline it does not have. A `--from-history` render that fails
skips only *its* comparison, with the failure named on the line.

The **target MDE** defaults to the comparison's `min_effect` (from the experiment
YAML) and is overridable with `--mde`.

## Usage

```bash
# Size every comparison of one experiment from persisted baselines
abk plan --select checkout_flow_v3

# A greenfield experiment with no data yet — supply the baseline by hand
abk plan --select signup_test \
  --baseline signup_cr:prop=0.2,n=300 \
  --baseline arpu:mean=62.86,std=42,n=300

# Size a single comparison at a custom target effect and power
abk plan --select checkout_flow_v3 --metric arpu --mde 0.03 --power 0.9
```

### Flags

| Flag | Meaning |
|---|---|
| `--select <exp>` / `-s` | Experiment selector: a name, path glob, `tag:<tag>`, or `*`. Repeatable; defaults to all. |
| `--metric <m>` | Plan only this one comparison (default: every declared comparison). |
| `--mde <x>` | Target minimum detectable effect, in the comparison's effect units. For a `relative` test that is a fraction (e.g. `0.05` = 5%); for an `absolute` test it is a raw delta. Default: the comparison's `min_effect`. |
| `--power <p>` | Target power in (0, 1). Default: the project statistics default (`0.8`). |
| `--alpha <a>` | Experiment-level significance *before* correction, in (0, 1). The two-tier scheme still divides it. Default: the experiment/project alpha. |
| `--baseline <spec>` | Baseline moments for a metric with no persisted data. Repeatable. |
| `--from-history <N d>` | Derive baselines from the N whole days before the start (population-wide; loses to `--baseline`, wins over persisted rows). |
| `--profile <name>` | Profile name (default: `profiles.yml` `default_profile`). |

### The `--baseline` grammar

A `--baseline` spec is `<metric>:<field>=<value>[,<field>=<value>...]`:

- **sample metric** — `<metric>:mean=..,std=..,n=..` (mean and a positive std are
  required; `n` must be positive).
- **fraction metric** — `<metric>:prop=..,n=..` (`prop` must be in `(0, 1)`).
- **optional** — `n_other=..` sets the treatment-arm size; it defaults to `n`.

```bash
abk plan -s arpu_experiment --baseline arpu:mean=12.5,std=8,n=5000
abk plan -s signup_test     --baseline signup_cr:prop=0.1,n=10000,n_other=10000
```

A malformed `--baseline` (missing `n`, a non-numeric value, an unknown field, a
proportion outside `(0, 1)`) is a hard error and exits non-zero, naming the
problem.

## What it refuses to size — and why

`abk plan` sizes **only the closed-form power families** it has a versioned formula
for: continuous metrics (`t-test`, and `cuped-t-test`) via the standardized-effect
solve, and proportions (`z-test`) via Cohen's h. Anything else is reported
`SKIPPED: <reason>` — this is a **by-design refusal, not an error, and the exit
stays 0**. `abk plan` will not invent math it cannot stand behind.

The refusals dispatch on each method's declarative capability (never on a method
name), so:

- **ratio metrics** (e.g. `ratio-delta`) — no closed-form power formula → SKIPPED.
- **bootstrap / resampling methods** (all `*-bootstrap` variants) — no closed-form
  power → SKIPPED. To measure their power empirically, use
  `abk validate --inject-effect` instead (see [Validating with A/A](validate.md)).
- **paired designs** (`paired-*`) → SKIPPED.

### CUPED is sized on its own covariate correlation

`cuped-t-test` is sized on the **deflated** variance whenever the baseline row carries
a covariate correlation: since `0.4.0` every `_ab_results` row persists `corr_coef_1/2`,
and `0.6.x` uses it, so required-N is `(1 − ρ²)×` the raw-variance number instead of the
old upper bound. The plan line always names the variance it used:

```
⚠ sized on CUPED-deflated variance (ρ = 0.62) — required-N is 0.616× the raw-variance bound
```

Three cases fall back to the raw variance, each with its own note, because a
required-N whose basis you cannot see is the number you would plan a launch around:

| Note | When | What it means |
|---|---|---|
| `no covariate correlation on the baseline row (rows written before 0.4.0 carry none)` | the row predates `0.4.0`, so no ρ was stored | required-N is a **conservative upper bound**; re-run the experiment (or pass `corr=`) to size on the real ρ |
| `the baseline's covariate correlation is ρ = 0 …` | ρ really is zero | a **measurement**, not a missing value: this covariate reduces no variance, so the raw number *is* the answer |
| `ρ = 1 leaves no usable residual variance …` | `1 − ρ² < 1e-12` | the covariate reproduces the metric (a leak, or a synthetic fixture). Deflating would size the experiment to nothing off rounding noise, so the raw bound stands — **check what your covariate is** |
| `the --baseline override carries no 'corr'` | you typed a baseline without `corr=` | add `corr=<ρ>` to size on CUPED; until then the number is an upper bound |

A ρ that clears the degeneracy floor can still imply a **>100× drop** in required-N
(`1 − ρ² < 0.01`). That is the measurement, so it is used — and the line adds *"check
that the covariate is not derived from the metric"*, because a covariate built from the
outcome is the usual explanation.

You can also supply ρ by hand for an experiment that has never run:
`--baseline arpu:mean=12.5,std=8,n=5000,corr=0.6`. It is accepted only on a comparison
whose method actually applies a covariate — deflating a plain `t-test` would promise a
reduction the analysis will never perform.

## Reading the output

Output is the same tree style as the rest of the CLI. Here is a full example:

```
┌─ signup_test: plan · α raw=0.05 → per-comparison 0.05 · power 0.80
│   signup_cr [main · z-test · relative] — baseline prop=0.2 · n=300/300 trials (persisted @ …)
│     target MDE 5.00% → required 25,580/arm ✗ underpowered · power@MDE 0.06 · achievable MDE 49.26%
│   arpu [secondary · cuped-t-test · relative] — baseline mean=62.86 std=42 · n=300/300 (persisted @ …)
│     target MDE 5.00% → required 1,727/arm ✗ underpowered · power@MDE 0.21 · achievable MDE 12.01%
│     ⚠ sized on CUPED-deflated variance (ρ = 0.62) — required-N is 0.616× the raw-variance bound
└─ looks: 14 planned · cadence 1d · horizon 2024-07-15 · ~28 _ab_results rows/full-refresh
```

Things worth reading closely:

- The **header** echoes the raw alpha and the two-tier resolve (`main .. /
  secondary ..` when they differ, else `per-comparison ..`) and the target power.
- Each comparison line is tagged `[role · method · test_type]`, where role is
  `main`, `secondary`, or `guardrail`.
- An **unachievable target** — for example a relative MDE off a proportion near 1,
  or off a zero mean — reports required-N as `∞ (underpowered)` rather than
  crashing the plan.
- The **footer** is the timing/cost companion: the projected look count, the
  cadence, the horizon date, and the cost shape (`~N _ab_results rows/full-refresh`).
  These come from the **same** grid enumeration the pipeline and validator use, so
  the numbers match what a real run will produce.

### Multi-arm experiments

Every declared **control-vs-treatment** contrast is sized, each at its own
allocation ratio from `expected_split`:

```
signup_cr [main · z-test · relative] — baseline prop=0.1 · n=150,000/75,000 (…)
  target MDE 5.00% → required 115,503/arm ✓ powered · power@MDE 0.98 · achievable MDE 3.57%
  control vs t2 (ratio 0.167) — required 269,506/arm ✗ underpowered
  the binding contrast is control vs t2 (269,506/arm) — the timing below is the
  headline contrast's
```

Contrasts sharing an allocation ratio — every contrast, under an even split —
collapse to one line instead of repeating the same numbers:

```
  all 3 declared vs-control contrasts share this allocation ratio, so they size the same
```

Three things are worth knowing about that output:

- **Only the required N is per contrast.** The achievable MDE and the achieved
  power are *retrospective* bounds solved from the observed ratio in the baseline
  you passed, so they are the same for every contrast; printing them per pair
  would be the headline pair's number wearing another pair's name.
- **Each contrast carries its own ✓/✗ powered flag**, and when the binding
  contrast is not the headline the plan names it — the runtime line below
  describes the headline contrast only.
- **Treatment-vs-treatment pairs are NOT sized.** Under the default
  `contrasts: all_pairs` they are in the family and share the alpha, but sizing
  needs the baseline arm's moments and a pre-launch plan has them for the control
  only. The plan says so in a warning; `contrasts: vs_control` drops those pairs
  (and the warning with them).

The calibrated pair is deliberately not `contrast_pairs()[0]`: with a control
declared late in `variants` that entry is a treatment-vs-treatment pair, which
carries no baseline moments.

### The peeking warning

If the projected look count exceeds the project's `warn_looks` without
`sequential.enabled`, the plan warns that peeking inflates the false-positive rate
— and points you at enabling sequential analysis or coarsening the cadence. See
[Sequential analysis](sequential.md) for the always-valid CI that closes this gap.

## Estimating calendar time

The footer's look-count and cost-shape line is one part of the pre-launch timing
companion; **runtime** and **ASN** complete it. Given a **unit-arrival rate**, each
sizable comparison also reports how long the experiment will take:

- **runtime** — `days-to-required-N = required_n / arrival_rate`, plus the planned
  horizon length. The arrival rate is derived **read-only** from the cohort source
  (distinct units per observed day over the whole-cohort window, split to the control
  arm): the persisted `_ab_exposures` copy when `assignment.cohort_copy.enabled:
  true`, otherwise — the default — a fresh snapshot of the live assignment source,
  re-executed at invocation time. Or supply it directly with
  `--arrival-rate <units/day>` (total across arms) for a greenfield experiment with
  no exposures yet.
- **ASN** — for a `sequential.enabled`, sequential-eligible comparison, the always-valid
  design's **average sample number**: the expected control-arm N at which the confidence
  sequence first excludes zero, under the true target effect (H1) and the null (H0). It
  is a deterministic (fixed-seed) Monte-Carlo estimate crossing the *exact* shipped CS
  boundary, capped at the planned horizon.

> **Cost note.** In the default no-copy mode, deriving the arrival rate re-executes
> and re-validates your assignment SQL live, once per `abk plan` invocation — the
> same documented cost/freshness tradeoff every read-only command pays. If your
> assignment source is expensive to join, either pass `--arrival-rate` explicitly or
> opt into `assignment.cohort_copy` so the rate reads the persisted table instead.

Without an arrival rate — neither derivable from the cohort source nor passed via
`--arrival-rate` — both runtime and ASN are **SKIPPED with a reason** (a backfilled
cohort spanning ~one instant is underivable), never invented. A fixed-horizon or
resampling design reports `sequential ASN: n/a`.

> **ASN is not a smaller sample requirement.** The reported ASN is the expected
> *stopping* N, stated **against the horizon**: under a true effect the sequence usually
> stops well before the horizon (ASN ≪ horizon-N when well-powered); under the null it
> runs essentially to the horizon. The always-valid design's *sample requirement* (the N
> to reach a given power) is actually **larger** than the fixed required-N, because the
> mixture CI is deliberately wider — that width is the price of unlimited peeking.

## Exit codes

- A **by-design refusal** — a ratio/bootstrap/paired method, or a comparison with
  no baseline — is `SKIPPED` and the command **exits 0**. These are informational,
  not failures.
- A **genuine harness failure** exits **non-zero**: an out-of-range
  `--alpha`/`--power`/`--mde`, a `--metric` that matches no declared comparison, a
  malformed `--baseline`, a cadence grid that exceeds `max_looks`, or a warehouse
  read error. An **empty selection** is *not* a failure — it prints `Nothing
  selected.` and exits 0.

## See also

- [Validating with A/A](validate.md) — the post-launch FPR/power audit, and
  `--inject-effect` for measuring bootstrap/paired power the planner refuses to size.
- [Configuration](configuration.md) — `min_effect`, `expected_split`, the two-tier
  correction, and the `sequential` and `limits` blocks the plan reads.
- [Sequential analysis](sequential.md) — enabling always-valid CIs when the plan
  warns about peeking.
- [Quickstart](../getting-started/quickstart.md) — running the pipeline once so the
  planner has a persisted baseline to size from.
