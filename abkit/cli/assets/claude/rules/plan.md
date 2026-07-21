# abk plan — pre-launch sizing

`abk plan` answers "**is this experiment worth launching, and how big must it
be?**" *before* any data lands — read-only, at the same effective two-tier alpha
the run and readout will use. It is the pre-launch sibling of `abk validate`
(which needs a live cohort). Spec: `cli-and-dx.md §1`.

**Strictly read-only.** No lock, no `_ab_*` writes, its own DB connection closed
in a `finally`. Safe to run against a production warehouse any time.

## What it reports (per comparison)

| Answer | Question |
|---|---|
| **required-N** / arm | Sample per control arm to detect the target MDE at the configured power + alpha (at the experiment's `expected_split` allocation). |
| **achievable MDE** | The smallest effect the *current* sample size could detect (retrospective bound). |
| **achieved power** | Power for the target MDE at the current size. |

All three are computed at the **effective per-comparison alpha** — the two-tier
Bonferroni resolve (main metrics vs secondary/guardrail get different alphas),
shown in the tree header. Plus a footer line: the **projected look count**,
cadence, horizon date, and the **cost shape** (`~N _ab_results rows/full-refresh`)
from the *same* grid enumeration the pipeline uses.

## Where the baseline comes from

Sizing needs per-arm moments (mean+std for `sample`, proportion for `fraction`):

1. **Persisted** — the latest usable `_ab_results` row for the control/first-
   treatment pair (source shown as `persisted @ <ts>`). Requires at least one
   `abk run` to have landed.
2. **`--baseline` override** — for a greenfield experiment with no data yet.
   Grammar: `<metric>:mean=..,std=..,n=..` (sample) or `<metric>:prop=..,n=..`
   (fraction). Optional `n_other=..` sets the treatment arm (defaults to `n`).

Without either, that comparison is reported **un-sizable — not guessed**. The
**target MDE** defaults to the comparison's `min_effect` (override with `--mde`).

## Flags

| Flag | Meaning |
|---|---|
| `--select <exp>` / `-s` | Experiment selector (name, glob, `tag:<t>`, `*`; repeatable). |
| `--metric <m>` | Plan only this one comparison. |
| `--mde <x>` | Target MDE in the comparison's effect units (relative ⇒ a fraction like `0.05`; absolute ⇒ a raw delta). Default: `min_effect`. |
| `--power 0.8` | Target power. Default: the project statistics default. |
| `--alpha 0.05` | Experiment-level alpha *before* correction; the two-tier scheme still divides it. Default: experiment/project alpha. |
| `--baseline <spec>` | Greenfield baseline moments (repeatable; see above). |
| `--arrival-rate <units/day>` | Total units/day across arms, for the **runtime** (days-to-required-N) + always-valid **ASN** estimates. Default: derived read-only from the cohort source — the persisted `_ab_exposures` copy under `assignment.cohort_copy.enabled`, otherwise (the default) a fresh snapshot of the live assignment source re-executed at invocation time (the documented no-copy cost/freshness tradeoff); without arrival data both are skipped. Must be `> 0`. |
| `--profile` | Profile name (default: `profiles.yml` `default_profile`). |

## What it refuses (honest, never invented math)

Only the closed-form power families are sizable. A comparison is reported
`SKIPPED: <reason>` (not an error, exit stays 0) when the method has no versioned
power formula:

- **ratio** metrics (e.g. `ratio-delta`) — no closed-form power → SKIPPED.
- **bootstrap / resampling** methods — no closed-form power; measure power with
  `abk validate --inject-effect` instead → SKIPPED.
- **paired** designs — SKIPPED.

**CUPED** (`cuped-t-test`) *is* sized, but on the **raw** persisted variance (the
covariate correlation ρ is not persisted per row). This is a **conservative upper
bound** on required-N — the real CUPED-deflated N is lower; the plan flags this
with a `⚠ sized on RAW variance` note.

## Reading the output

Each sized line ends with `✓ powered` (current N ≥ required-N) or
`✗ underpowered`. An unachievable target (e.g. a relative MDE off a near-1
proportion, or off a zero mean) shows required-N as `∞ (underpowered)` rather
than crashing the plan.

```
┌─ example_signup_test: plan · α raw=0.05 → per-comparison 0.05 · power 0.80
│   example_signup_cr [main · z-test · relative] — baseline prop=0.2 · n=300/300 trials (persisted @ …)
│     target MDE 5.00% → required 25,580/arm ✗ underpowered · power@MDE 0.06 · achievable MDE 49.26%
│   example_arpu [secondary · cuped-t-test · relative] — baseline mean=62.86 std=42 · n=300/300 (persisted @ …)
│     target MDE 5.00% → required 2,804/arm ✗ underpowered · power@MDE 0.15 · achievable MDE 15.31%
│     ⚠ sized on RAW variance — CUPED (ρ not persisted) lowers required-N further
└─ looks: 14 planned · cadence 1d · horizon 2024-07-15 · ~28 _ab_results rows/full-refresh
```

For a **>2-arm** experiment, sizing is shown for the first-pair contrast only
(the other pairs share the same alpha) — the plan says so. If the look count
exceeds `warn_looks` without `sequential.enabled`, it warns that peeking inflates
the false-positive rate (enable sequential or coarsen the cadence).

## Timing companion (look count, cost shape, runtime & ASN)

The footer look-count + cost-shape line is the pre-launch timing/cost companion.
**Runtime and ASN** ship too: given a **unit-arrival rate** — derived read-only from
the cohort source (the persisted `_ab_exposures` copy under
`assignment.cohort_copy.enabled: true`, otherwise a live re-render + re-validation
of the assignment SQL at invocation time — the default no-copy cost/freshness
tradeoff; distinct units per observed day, whole-cohort window, split to the
control arm) or supplied with `--arrival-rate <units/day>` — each sizable comparison
also reports **runtime** (`days-to-required-N = required_n / rate` plus the horizon
length) and, for a `sequential.enabled`, sequential-eligible comparison, the **ASN**
(the always-valid design's *average sample number* — the expected control-arm N at
which the confidence sequence first excludes zero under H1/H0, a fixed-seed Monte-Carlo
capped at the horizon). Without an arrival rate BOTH are SKIPPED with a reason (a
backfilled cohort spanning ~one instant is underivable) — never invented; a
fixed-horizon or resampling design reports `sequential ASN: n/a`. ASN is the expected
*stopping* N against the horizon, **not** a lower sample requirement than the fixed
required-N (the mixture CI is wider — the price of peeking).

## Exit codes

A **by-design refusal** (ratio/bootstrap/no-baseline SKIPPED) exits **0**. A
genuine harness failure — bad selection, malformed `--baseline`, a grid over
`max_looks`, or a warehouse read error — exits **non-zero**.
