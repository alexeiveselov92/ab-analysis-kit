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
or a proportion for a `fraction` metric. `abk plan` draws them from one of two
sources — an explicit `--baseline` override always takes precedence, and it falls
back to persisted data otherwise:

1. **`--baseline` override** — for a greenfield experiment with no data yet (or to
   override a stale persisted look), you supply the moments by hand (grammar below).
2. **Persisted** — the most recent *usable* `_ab_results` row for the
   control/first-treatment pair (labeled `persisted @ <ts>` in the output). This
   requires at least one `abk run` to have landed. Rows flagged as
   insufficient-data or with a null value are skipped; the latest data-rich look
   wins.

If **neither** is available, that comparison is reported
`SKIPPED: no baseline — run abk run first, or pass --baseline ...`. `abk plan`
never guesses a baseline it does not have.

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

### CUPED is sized on the raw variance

`cuped-t-test` **is** sized, but on the **raw** persisted variance — the covariate
correlation ρ is not persisted per `_ab_results` row. That makes the reported
required-N a **conservative upper bound**: the real CUPED-deflated N is *lower*. The
plan flags this with a `⚠ sized on RAW variance` note so you never mistake the
ceiling for the true number.

## Reading the output

Output is the same tree style as the rest of the CLI. Here is a full example:

```
┌─ signup_test: plan · α raw=0.05 → per-comparison 0.05 · power 0.80
│   signup_cr [main · z-test · relative] — baseline prop=0.2 · n=300/300 trials (persisted @ …)
│     target MDE 5.00% → required 25,580/arm ✗ underpowered · power@MDE 0.06 · achievable MDE 49.26%
│   arpu [secondary · cuped-t-test · relative] — baseline mean=62.86 std=42 · n=300/300 (persisted @ …)
│     target MDE 5.00% → required 2,804/arm ✗ underpowered · power@MDE 0.15 · achievable MDE 15.31%
│     ⚠ sized on RAW variance — CUPED (ρ not persisted) lowers required-N further
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

For an experiment with more than two arms, sizing is shown for the **first-pair
contrast only** (the other pairs share the same alpha). The plan says so explicitly
in a warning line.

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
  horizon length. The arrival rate is derived **read-only** from `_ab_exposures`
  (distinct units per observed day over the whole-cohort window, split to the control
  arm), or supplied directly with `--arrival-rate <units/day>` (total across arms) for
  a greenfield experiment with no exposures yet.
- **ASN** — for a `sequential.enabled`, sequential-eligible comparison, the always-valid
  design's **average sample number**: the expected control-arm N at which the confidence
  sequence first excludes zero, under the true target effect (H1) and the null (H0). It
  is a deterministic (fixed-seed) Monte-Carlo estimate crossing the *exact* shipped CS
  boundary, capped at the planned horizon.

Without an arrival rate — neither derivable from `_ab_exposures` nor passed via
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
