# M13 batch A/A revalidation (2026-08-05, STAT-6)

The change-control step every deviation owes
([statistics-changes.md §0](../../specs/statistics-changes.md) step 4,
[contributing.md](../../../.claude/rules/contributing.md) "Changing a statistical
number"): each M13 option is put through the A/A instrument beside the default
it does not replace, and the numbers are recorded — **including the places where
the instrument cannot arbitrate** (D6: "where A/A cannot arbitrate, say so
rather than running a sweep that cannot answer").

Reproduce with `.venv/bin/python docs/research/2026-08-m13-revalidation/revalidate.py out.json`
from the repo root. Deterministic (derived seeds, no wall-clock — D13), 20 000
placebo iterations per cell; the raw output is
[revalidation.json](revalidation.json). Binomial(20 000, 0.05) has σ ≈ 0.0015,
so a ±3σ band around a nominal α = 0.05 is [0.0455, 0.0545].

**Every knob pair below is measured on the SAME placebo draws** (the seed does
not include the option under test), so a difference between two rows of a pair
is a difference in the estimator, never Monte-Carlo noise — and an *identical*
number is exact agreement rather than a coincidence. That pairing is what makes
§1's result readable at all.

## 1. STAT-3 — `interval: pooled | score` (`z-test`, 4 000 Bernoulli units at p = 8%)

| scale | interval | FPR | CI coverage | sign split | power (Δ = 2pp / +25%) |
|---|---|---|---|---|---|
| absolute | `pooled` | 0.04585 | 0.94135 | 0.5289 | 0.05565 |
| absolute | `score` | 0.04585 | 0.94135 | 0.5289 | 0.05565 |
| relative | `pooled` | 0.04415 | **0.90645** | 0.5187 | 0.61090 |
| relative | `score` | 0.04415 | **0.93370** | 0.5187 | 0.61090 |

**Under identical draws, every column but one is identical to the last digit.**
That is not a fixture artifact: D11 chose the Farrington–Manning form precisely
so `Z(0)` is bit-identical to today's pooled z, so the two intervals exclude
zero on exactly the same placebos (verified separately on 1 400 hand-built
tables: zero decision disagreements). The FPR column, the sign column and the
power column are therefore **structurally blind** to this deviation.

The column that is not blind is **coverage on the relative scale**: the pooled
interval covers the injected +25% lift 90.6% of the time against a nominal 95%,
the score interval 93.4% — a 4.4-point undercoverage cut to 1.6. On the absolute
scale the two agree exactly at this table size, which is the expected shape: the
Wald and score intervals differ by `O(z²/n)` there, and it is the RATIO scale
where the pooled variance is furthest from the truth away from the null.

**A note on the level, since 0.0442 sits below the ±3σ band.** That conservatism
belongs to the A/A *placebo design*, not to either interval: the split has a
fixed arm size, so the pooled-z statistic lives on a coarse lattice at these
counts. Measured on this very panel with an independently transcribed pooled z:
**0.0420** under a fixed-size permutation, **0.0492** under a per-unit coin flip
that smears the lattice. Both interval rows inherit it identically, which is
exactly why a paired design is the only readable one here.

## 2. STAT-4 — `interval: delta | fieller` (`t-test`, relative, 400 units/arm)

| control-mean CV | interval | FPR | CI coverage | **sign split** | power (+15% injected) |
|---|---|---|---|---|---|
| ~5% (σ/μ = 1) | `delta` | 0.05205 | 0.94795 | **0.6407** | 0.5099 |
| ~5% | `fieller` | 0.05355 | 0.94645 | **0.4930** | 0.5616 |
| ~10% (σ/μ = 2) | `delta` | 0.04985 | 0.95015 | **0.7613** | 0.1307 |
| ~10% | `fieller` | 0.05115 | 0.94885 | **0.5015** | 0.2043 |

**This is the milestone's clearest case of an instrument being blind by shape
rather than by resolution.** Both the FPR and the two-sided coverage column read
*calibrated* for both estimators, at both noise levels — 0.052 vs 0.054, 0.948
vs 0.946 — on the same draws. The delta interval's error is **one-sided**: 64%
of its false positives fall below zero at a 5% control-mean CV, and 76% at 10%.
Every abkit verdict (WIN, LOSE) is a one-sided claim, so that lean IS the error
rate the operator cares about, and only `fpr_negative_share` (STAT-2) can see
it. The measured 0.64 brackets the derivation's `0.5 + φ(z)z²·CV₁√w₁/α ≈ 0.659`
(the panel's realised control-mean CV is a little under the nominal 5%; STAT-4's
own 200k-draw measurement on exactly-specified moments read 0.664 against 0.659).

The power column is a **secondary finding the design did not predict**: Fieller
detects the injected lift more often (0.510 → 0.562, and 0.131 → 0.204 at the
higher denominator noise). That is not free power — unlike §1 the two rejection
sets genuinely differ here, and Fieller's is the absolute comparison's, which
does not pay for the denominator's variance twice. It is reported because a
reader otherwise expects an interval that is *wider on one side* to cost power.

## 3. STAT-1c — `guardrail_correction: inherit | none` (3 000 units)

| resolution | α handed to the method | FPR |
|---|---|---|
| `inherit` (3 comparisons ⇒ α/3) | 0.0167 | 0.0177 |
| `none` (raw α) | 0.0500 | 0.0503 |

The knob resolves a LEVEL rather than an estimator, so what A/A can confirm is
that the level is honoured — it is, to within a fifth of a σ on both rows. The
substantive claim behind D8 is not statistical but directional: a guardrail
exists to detect harm, and testing it at α/k makes the engine *less* able to,
which is an error in the dangerous direction. The instrument cannot rank those
two preferences; it can only certify that each is what it says.

## 4. STAT-1 — `correction: bonferroni | holm | none` (family of 4 null metrics)

| scheme | member α | family FWER | FDR |
|---|---|---|---|
| `bonferroni` | α/4 | 0.0488 | 0.0488 |
| `holm` | α (raw) | 0.0488 | 0.0488 |
| `none` | α (raw) | 0.1837 | 0.1837 |

Holm controls the family at α, and the uncorrected family does not (0.18 ≈ the
1 − (1−α)⁴ an operator would get by ignoring multiplicity).

**The identical 0.0488 is a mathematical identity, not a measurement.** Under the
COMPLETE null "at least one rejection" is `min p ≤ α/m` for both rules — Holm's
first step *is* the one-step level, and no later step is ever reached. So a
complete-null sweep can never separate them, and the test that claimed to
("Holm rejects at least as often", `x >= x`) could not fail. The claim becomes
measurable only when some hypotheses are false:

| scheme (2 of 4 metrics carry a real effect) | family FWER over the surviving nulls | FDR |
|---|---|---|
| `bonferroni` at α/4 | 0.0248 | 0.0083 |
| `holm` at raw α | 0.0510 | 0.0173 |

Holm removes the true effects at its first steps and then tests the surviving
nulls at levels that reach α — spending the whole budget, validly. The one-step
rule stops at half of it, and that unspent α is exactly the power it gives up.
Both are valid; only one is tight. (`tests/validate/test_family_sweep.py` now
pins both facts, the identity as an equality.)

## 5. What the instrument cannot arbitrate (D6)

- **`contrasts: all_pairs | vs_control` (STAT-1b).** The composed sweep
  (`--family-sweep`) composes over METRICS, not over arm pairs, so it can
  neither confirm nor refute the `g/2` level change. Recorded rather than
  answered with a sweep that does not address it — the same position STAT-1b
  itself took.
- **Holm's power gain on the planted metrics.** `FamilyScore` carries no
  detection rate for planted members, so the table above expresses the gain as
  the *unspent* α of the one-step rule rather than as a detection difference.
- **Overdispersion** (plan §6(e)) outranks every interval choice here and is out
  of scope: if randomisation is per unit while the metric counts sessions, every
  SE understates the truth by a factor no choice of pooled/score/delta/Fieller
  affects. The A/A permutation *would* see it, since it permutes units — a case
  where this instrument is the right one, for a question M13 does not ask.
- **Uniform ddof (STAT-5, dropped by D13)** was dropped partly *because* the
  instrument cannot see it: second-order in `1/(n−1)`, below the noise floor of
  every column above.

## 6. Verdict

Every M13 option is calibrated at its declared level — the mean-method and
guardrail cells inside the ±3σ Binomial band around theirs, the proportion cells
at the placebo design's own conservative level, identically for both intervals
(§1) — and no default moved: the
`pooled`/`delta`/`inherit`/`bonferroni`/`all_pairs` rows above are the `0.7.0`
engine, and `tests/e2e/test_m13_exit_gate.py` pins their persisted output
against a surface captured from the `v0.7.0` release itself.
