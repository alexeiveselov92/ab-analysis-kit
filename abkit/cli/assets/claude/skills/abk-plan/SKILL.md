---
name: abk-plan
description: >-
  Size an A/B experiment before launch with `abk plan`: required sample size,
  achievable minimum detectable effect (MDE), and achieved power at the effective
  two-tier alpha. Use when the user asks how many users / how big a sample they
  need, how long to run an experiment, whether it is powered, what effect they can
  detect at the current size, or to compute power / MDE for a planned or running
  test. Read-only — never runs the pipeline or writes results. For a greenfield
  experiment with no data yet, supply baseline moments with `--baseline`.
---

# Size an experiment before launch (`abk plan`)

`abk plan` answers three sizing questions per comparison — **required N** per arm
to detect a target MDE, the **achievable MDE** at the current sample size, and the
**achieved power** for that MDE — at the experiment's *effective* (post-correction)
alpha, plus the projected look count and cost shape. It is **strictly read-only**:
no lock, no `_ab_*` writes, no compute. It reads the latest persisted baseline
moments from `_ab_results`, or takes them from a `--baseline` override for an
experiment that has not run yet.

This skill is the procedure; for field detail read
`.claude/rules/ab-analysis-kit/plan.md`.

## Step 0 — Confirm the experiment and its baseline source

A project root contains `abkit_project.yml`. The experiment must already be a
defined `experiments/<name>.yml` (if not, use the **`abk-new-experiment`** skill
first). Then decide where baseline moments come from — this is the one real choice:

- **The experiment (or a similar one) has already run** → moments come from the
  latest usable per-arm row in `_ab_results` automatically. Nothing extra needed.
- **Greenfield — nothing persisted yet** → you must pass `--baseline` (see Step 2),
  otherwise the comparison is **SKIPPED: no baseline**. Get the numbers from the
  user or from a database MCP query against their historical data.

`abk plan` needs a connection (to read `_ab_results` and confirm the table), but it
only ever reads. If `profiles.yml` is still the `abk init` placeholder, do the
**`abk-setup-project`** skill first.

## Step 1 — Run the planner

```bash
abk plan --select <exp>                       # every comparison, defaults
abk plan --select <exp> --metric <metric>     # one comparison only
abk plan --select <exp> --mde 0.03            # size for a 3% target effect
abk plan --select <exp> --power 0.9           # override target power (default: project)
abk plan --select <exp> --alpha 0.01          # override pre-correction alpha
```

Flags (confirm the live set with `abk plan --help`):

| Flag | Meaning |
|---|---|
| `--select`, `-s` | Experiment selector: name, path glob, `tag:<tag>`, or `*` (repeatable) |
| `--metric` | Plan only this comparison (default: every declared comparison) |
| `--mde` | Target MDE in the comparison's effect units (default: the comparison's `min_effect`) |
| `--power` | Target power (default: `statistics.power`, typically 0.8) |
| `--alpha` | Experiment-level significance **before** correction (default: experiment/project alpha) |
| `--baseline` | Baseline moments for a metric with no data (repeatable — Step 2) |
| `--arrival-rate` | Total units/day across arms, for the runtime (days-to-N) + always-valid ASN estimates (default: derived read-only from the cohort source — the persisted `_ab_exposures` copy in copy mode, otherwise a fresh re-execution of the live assignment SQL at invocation time; must be `> 0`) |
| `--profile` | Profile name (default: `profiles.yml` `default_profile`) |

`--mde` and `--alpha` are in the comparison's own units: a `relative` test type reads
MDE as a fraction (`0.03` = 3%); `absolute` reads it as a raw delta. `--alpha` is the
raw experiment-level alpha — the planner re-applies the **two-tier Bonferroni**
correction on top (main metric vs secondary/guardrail get different effective alphas),
so the printed per-comparison alpha is the corrected one the readout will use.

## Step 2 — Greenfield: supply baseline moments

For a metric with no persisted rows, give the control-arm moments directly. Grammar
is `<metric>:<field>=<value>,...`, repeatable per metric:

```bash
# sample metric (mean + std): required fields mean, std, n
abk plan --select checkout_test --baseline arpu:mean=12.5,std=8,n=5000

# fraction metric (proportion): required fields prop (in 0..1), n
abk plan --select checkout_test --baseline signup_cr:prop=0.10,n=10000
```

- `sample` metrics need `mean`, `std` (>0), `n`. `fraction` metrics need `prop`
  (in (0,1)) and `n` (`prop` may also be given as `mean`).
- `n` is the control-arm size; add `n_other=<N>` for a different treatment size
  (defaults to `n`). Get `mean`/`std`/`prop` from historical data — a database MCP
  query over the same population is the reliable way; do not invent them.
- A malformed spec or a missing required field exits **non-zero** with a hint.

## Step 3 — Read the output

Per comparison the tree prints a line like:

```
arpu [main · cuped-t-test · relative] — baseline mean=12.5 std=8 · n=5000/5000 (persisted @ 2024-07-14…)
  target MDE 3.00% → required 7,842/arm ✗ underpowered · power@MDE 0.62 · achievable MDE 4.10%
  ⚠ sized on RAW variance — CUPED (ρ not persisted) lowers required-N further
```

Interpret it:

- **required N/arm** — control-arm size to reach the target power at the target MDE
  and the planned treatment:control split. `∞ (underpowered)` means the target is
  infeasible at this baseline (e.g. a proportion pushed out of (0,1)).
- **✓ powered / ✗ underpowered** — whether the current arm N already meets required N.
- **achievable MDE** — the smallest effect detectable *at the current sample size*
  (the retrospective bound). If a full run still can't reach the target MDE, either
  the effect is too small to catch or the experiment needs more traffic/time.
- **power@MDE** — achieved power for the target MDE at the current N.
- The **looks** line reports the planned cutoff count, cadence, horizon date, and
  `~N _ab_results rows/full-refresh` — the compute cost shape, not sizing.

If no target MDE is available (no `--mde` and no `comparison.min_effect`), required-N
is omitted and only the achievable MDE is shown — pass `--mde` or set `min_effect`.

## Step 4 — Understand the honest refusals (SKIPPED, not failures)

`abk plan` sizes only closed-form families and refuses the rest cleanly (it prints
`SKIPPED: <reason>` and still exits 0 unless a real error occurred):

| Reason | Why | What to do |
|---|---|---|
| **ratio metric** | No versioned closed-form power formula for `ratio-delta` | Size a related `sample`/`fraction` proxy, or measure empirically |
| **resampling method** | Bootstrap methods have no closed-form power | Measure it with `abk validate --inject-effect` (empirical power) |
| **paired design** | Paired methods aren't sized here | Size the unpaired analogue as a bound |
| **no baseline** | No persisted rows and no `--baseline` | Run `abk run` first, or pass `--baseline` (Step 2) |

**CUPED caveat:** a `cuped-t-test` is sized on the **raw** persisted variance (the
covariate correlation ρ isn't stored per row), so its required-N is a *conservative
upper bound* — real CUPED needs fewer units. The `⚠ sized on RAW variance` note flags
this; tell the user the true requirement is lower.

## Step 5 — Act on the result

- **Underpowered at the horizon?** Extend `end_date`, raise traffic/allocation, pick a
  lower-variance or CUPED method, or accept a larger detectable MDE. Re-plan.
- **Comfortably powered?** The design is sound to launch. Remind the user that reading
  the daily series early is peeking — if they want to stop early, enable
  `sequential: {enabled: true}` (opt-in, off by default), and validate calibration
  with the **`abk-validate`** skill before trusting verdicts.
- **Multi-arm note:** for >2 variants, sizing is shown for the first contrast
  (`variants[0]` vs `variants[1]`) only; the other pairs share the same alpha.

`abk plan` is planning-only. Sizing (required-N / MDE / power) is the core; **runtime /
ASN** ship alongside it. If the user asks "how long", `abk plan` reports **runtime**
(days-to-required-N) from a unit-arrival rate — derived read-only by re-executing
the cohort source at invocation time (the live assignment SQL in the default
no-copy mode — the documented cost/freshness tradeoff — or the persisted
`_ab_exposures` copy in copy mode) or supplied with `--arrival-rate <units/day>` —
and, for a `sequential.enabled`,
sequential-eligible comparison, the **ASN** (the always-valid design's average sample
number: expected stopping N under H1/H0, horizon-capped). Without an arrival rate both
are SKIPPED with a reason (never invented); a fixed-horizon/resampling design shows
`sequential ASN: n/a`. Relay that ASN is an expected *stopping* N against the horizon,
not a lower requirement than the fixed required-N. `scheme: alpha_spending`
(group-sequential) is a named future item and is not implemented — refuse it with no
version promise.

## Final checklist

- [ ] Experiment exists and is selected; baseline source is clear (persisted vs `--baseline`).
- [ ] `--mde` (or `comparison.min_effect`) is set so required-N is computed.
- [ ] You read required-N, achievable MDE, achieved power, and the ✓/✗ powered flag.
- [ ] Any SKIPPED comparison's reason was explained (ratio / bootstrap / paired / no baseline).
- [ ] CUPED's raw-variance caveat and any multi-arm/peeking warnings were relayed.
