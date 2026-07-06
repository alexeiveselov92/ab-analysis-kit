# Reading a readout

A **readout** is abkit's answer to the only question that matters at the end of an
experiment: *did the treatment win, lose, do nothing, or do we not know yet?* This
guide teaches you to read one correctly — the verdict, the effect and its
confidence band, how significance is judged against the row's effective alpha, and
the three gates (SRM, insufficient data, and the horizon) that can withhold a call.

You see readouts in three places, all built from the same numbers:

- **`abk run --report`** — a self-contained HTML file per experiment, shareable
  with stakeholders (`data-contract-and-reporting.md §5.2`).
- **[`abk explore`](explore.md)** — the live cockpit, where the same verdict rides
  over the stabilization chart.
- **Your own BI**, pointed at the `_ab_results` warehouse table.

One thing to know up front: **the verdict is computed at read time, never stored.**
Every time a report renders, `abkit/pipeline/readout.py` re-evaluates the verdict
from the persisted result rows. The warehouse holds the *numbers* (effect, CI,
p-value, flags); the *decision* is derived fresh (`data-contract-and-reporting.md
§1`, D5(f)). Change the correction scheme or the min-effect and re-render, and the
verdict updates without recomputing anything.

## The verdict

Each readout carries **one verdict per (main metric × control-vs-treatment pair)**
— there is no invented scalar that aggregates metrics or arms
(`data-contract-and-reporting.md §1`). A three-arm experiment with one main metric
produces two verdicts: control-vs-B and control-vs-C. Guardrail and secondary
metrics do not get their own verdict; they *modify* the main-metric verdict (see
[Guardrails](#guardrails-can-block-a-win) below).

| Verdict | Meaning |
|---|---|
| **WIN** | The CI excludes zero in the **desired** direction, and that has held over the stabilization window. |
| **LOSE** | The CI excludes zero in the **adverse** direction, stably. |
| **FLAT** | The CI includes zero **and** the test is powered enough to rule out a business-meaningful effect. A confident "no effect", not "we didn't look hard enough". |
| **INCONCLUSIVE** | Everything else — keep running, or something is blocking a call (SRM, pre-horizon, too little data, not yet stabilized, or underpowered). |

Every verdict comes with a **rationale** (why this call) and often **caveats**
(things you should know even so). Read them — they name the exact gate that fired.

WIN vs LOSE is decided by the sign of `effect` against the comparison's
`desired_direction` (`increase` or `decrease`). A significant effect in the good
direction is a WIN; the same magnitude the wrong way is a LOSE. `desired_direction`
is a read-time verdict input, set per comparison in the experiment YAML — see
[experiments](experiments.md).

## The effect and its CI band

At the latest cutoff the readout shows three linked numbers:

- **`effect`** — the point estimate of the lift (its units depend on the method's
  `test_type`: a `relative` t-test reports a fractional lift, an `absolute` one
  the raw difference).
- **`[left_bound, right_bound]`** — the (1 − α) confidence interval around it.
- **`pvalue`** — the two-sided p-value for "no difference".

These three say the same thing three ways. **The CI excludes zero ⇔ `pvalue <
alpha` ⇔ `reject = 1`** (`data-contract-and-reporting.md §1`). When the band clears
zero, the sign of `effect` tells you who won; when it straddles zero, you have no
significant difference *at this look*.

Read the **band width**, not just whether it clears zero. A CI that barely excludes
zero at one early cutoff and re-crosses it later is not a winner — that is exactly
the volatility the stabilization rule (below) distrusts. In the chart, watch the
band **shrink** as sample accrues: a mature experiment is a point estimate settling
inside a tightening interval.

## p vs the row's effective alpha

The `alpha` on a result row is the **effective, post-correction** per-comparison
significance level — not necessarily the `alpha: 0.05` you wrote in YAML
(`data-contract-and-reporting.md §2`). Two corrections reshape it:

- **Two-tier Bonferroni** splits the budget by role: main metrics and secondary
  metrics land at **different** effective alphas, so watching several metrics
  doesn't quietly inflate your error rate. The stored CI band already reflects this
  tightened alpha — so for `none` and `bonferroni`, "significant" is simply "the
  band excludes zero".
- **Benjamini-Hochberg** (`correction: benjamini_hochberg`) is applied **at read
  time**, per cutoff, across the experiment's comparisons (`readout.py`
  `_build_sig_map`). Compute-time rows deliberately store the *raw* alpha; the
  readout adjusts the family of p-values and compares against it when it builds the
  verdict. This means a row's stored `reject` flag is pre-BH — the **verdict's**
  significance is the BH-aware one, so under BH the two can differ. Trust the
  verdict's `significant`, and pass your project config to whatever renders the
  readout so the correction resolves correctly (`abk run --report` does this for
  you).

The practical upshot: don't eyeball `pvalue < 0.05`. Compare `pvalue` to the row's
own `alpha`, and remember BH does the comparison for you at read time.

## The SRM gate — a hard block

**Sample Ratio Mismatch (SRM)** checks that observed arm sizes match your declared
`expected_split`. If they don't, your randomization or assignment query is broken,
and **every effect is untrustworthy** — a 52/48 split when you expected 50/50 means
you're comparing non-comparable populations, and no amount of significance rescues
that.

SRM is a **hard, blocking, non-dropping gate** (`data-contract-and-reporting.md
§1, §6`):

- On failure the rows are still written, but with **`srm_flag`** set and
  **`decision_blocked`** set. Data is never silently dropped.
- The verdict is forced to **INCONCLUSIVE** with a rationale naming the failed gate
  (`readout.py` step 1), regardless of how clean the effect looks.
- `abk run` prints a loud red line to stderr, e.g.
  `SRM FAILED (observed 0.62/0.38 vs expected 0.50/0.50, chi2 p=2.3e-11) — effects
  untrustworthy` (below `1d` cadence the evidence term reads `anytime e=… p=…`).
- The HTML report and explore both show a red SRM chip.

The gate is a **chi-square** test at daily-and-coarser cadence, and switches to the
**anytime-valid sequential multinomial** test below `1d` cadence (Lindon & Malek) —
checking χ² at every sub-day look would itself be peeking on the gate
(`data-contract-and-reporting.md §6`). The readout names whichever gate produced
the flag so the chip reads correctly.

Because a broken assignment poisons everything, the SRM chip in the report is
**window-independent**: it reflects current experiment health from the latest
persisted row overall, so a pinned or empty replay never silences a failing gate.
**Fix the assignment first** — re-check SRM before you believe any effect.

## Insufficient data — demotion, not deletion

When a cutoff has fewer than `min_units_per_arm` units in an arm (project default
**100**, in `abkit_project.yml` under `limits`), the pipeline still writes the row
but **withholds inference**: the test columns are NULL, `reject` is null, and
**`insufficient_data`** is set (`pipeline/analyze.py`, `enrich.py`). This is a
demotion, not a drop — the counts and the SRM check stay real, but there is no CI
to judge.

How this reads:

- In a series, a demoted point carries `ins = 1` and a **null** `reject` (in the
  baked payload; `builder.py` `_reject_flag`). BI should render these as **gaps**,
  never as a zero effect.
- The stabilization scan **skips** demoted (and degenerate, NULL-bound) cutoffs
  entirely — they are gaps, not evidence for or against (`readout.py`
  `_informative`).
- If the **latest** cutoff is demoted, the verdict is INCONCLUSIVE with a rationale
  naming the arm sizes ("insufficient data at the latest cutoff (…/… units) —
  inference withheld"). Let it accrue more sample.

## Stabilization and the horizon

A single significant look is not a win. abkit demands that significance be
**persistent** before it calls a verdict — this is what defends you against the
false positives that daily peeking would otherwise manufacture.

**Stabilization** (`data-contract-and-reporting.md §1, §4`; `readout.py` step 4):
over the trailing `readout.stabilization_days` window (default **7** elapsed-days,
one weekly cycle), *every* informative cutoff must agree — all excluding zero with
one consistent sign (→ WIN/LOSE), or all including zero (→ candidate FLAT). If the
sign flips, or the CI crosses zero at some cutoffs and not others, the verdict is
**INCONCLUSIVE** ("not stabilized"). Coarser cadences widen the window to the last
3 informative cutoffs (`MIN_STABLE_CUTOFFS`); fewer than 3 informative cutoffs is
itself INCONCLUSIVE. Stabilization is judged strictly over **elapsed time**, never
look count — otherwise an hourly grid would "stabilize" in six hours.

**The horizon** (`data-contract-and-reporting.md §4`; `readout.py` step 2): the
default confidence intervals are **fixed-horizon** — mathematically valid only *at*
the planned `end_date`. Reading them early and stopping is peeking, which inflates
your real error rate. So under the default (`ci_kind = "fixed"`), the readout
**withholds WIN, LOSE, and FLAT before `is_horizon`** — FLAT is equally a stop
decision. The pre-horizon series is informational; the chart de-emphasizes those
points, and the rationale says how far along you are.

If you need a legitimate early call, opt into **always-valid confidence sequences**
with `sequential: {enabled: true}` on a sequential-eligible method (see
[sequential analysis](sequential.md)). Those intervals are peeking-safe by
construction, and the readout will call WIN/LOSE/FLAT pre-horizon under them — with
a rationale noting the interval is always-valid. Any decisive verdict reached
before one full weekly cycle also carries a **"covers X% of a weekly cycle"** caveat
(`weekly_cycle_pct`), because day-of-week effects may not be represented yet.

## FLAT needs a min_effect

FLAT is the one verdict you can only earn deliberately. To distinguish "genuinely
no effect" from "we couldn't have detected one", the readout requires **both** the
CI to include zero across the stabilization window **and** the pair MDE (minimum
detectable effect) to have dropped to or below the comparison's configured
**`min_effect`** (`data-contract-and-reporting.md §1`; `readout.py` step 5):

- **No `min_effect` configured** ⇒ FLAT is unreachable; the verdict is
  INCONCLUSIVE with "cannot distinguish flat from underpowered". Set
  `comparisons[].min_effect` (in the units of that comparison's `effect`) to enable
  it — see [experiments](experiments.md).
- **MDE > `min_effect`** ⇒ still underpowered ⇒ INCONCLUSIVE, keep running.
- **MDE ≤ `min_effect`** with an all-quiet CI ⇒ **FLAT**.

MDE comes from the stored `mde_1/2` columns when the method computed them
(`calculate_mde: true`), or is recomputed read-time for t-test and z-test rows.
Methods with no MDE capability (ratio-delta, the bootstrap family) leave FLAT
honestly unreachable, and the rationale says so. Under always-valid mode, FLAT is
judged against the (wider) confidence-sequence half-width, not the fixed MDE.

## Guardrails can block a win

Guardrail comparisons (`is_guardrail: true`) never win — they only watch for
**regression**: their CI excluding zero against `desired_direction` at the stored
alpha (any significant harm flags, no stabilization required, and deliberately
correction-independent so BH can't un-flag a real harm). A regressed guardrail then
applies your `readout.guardrail_policy` (`readout.py` `_apply_guardrail_policy`):

- **`block`** (default) — a WIN is capped at **INCONCLUSIVE** with a rationale
  naming the regressed guardrail. You shipped a win that broke something you said
  you'd protect; abkit refuses to bless it.
- **`warn`** — the WIN stands, but with a **mandatory loud caveat** naming the
  regression.
- A **LOSE is never upgraded or blocked** by a guardrail — bad news is always
  delivered straight.

## A worked reading

A verdict card reading **INCONCLUSIVE** with:

> *pre-horizon: latest cutoff covers 4.0 of 14.0 planned days and fixed-horizon CIs
> are not peeking-valid — WIN/LOSE/FLAT withheld until the horizon*

means the numbers may already look decisive, but you're 4 days into a 14-day
fixed-horizon experiment. Nothing is wrong; abkit is refusing to let you peek. Wait
for the horizon, or switch the method to sequential for a peeking-safe early call.

Contrast a card reading **WIN** with:

> *CI excludes zero in the desired direction (up) at every informative cutoff in the
> trailing 7-day window (5 cutoffs)*

— a real, stabilized win: significant, same direction, held for a full weekly cycle.

## See also

- [experiments](experiments.md) — where `desired_direction`, `min_effect`,
  `is_main_metric`, `is_guardrail`, `guardrail_policy`, and `stabilization_days`
  are configured.
- [sequential analysis](sequential.md) — always-valid CIs and pre-horizon verdicts.
- [`abk validate`](validate.md) — the A/A false-positive matrix that tells you
  whether your method's real error rate matches nominal α.
- [The explore cockpit](explore.md) — read and retune the verdict live.
- [Configuration](configuration.md) — `abkit_project.yml`, including
  `limits.min_units_per_arm` and the correction defaults.
