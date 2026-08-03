# M13 Implementation Plan — versioned statistical improvements → `0.8.0`

> **STATUS: DRAFT (2026-08-03), no longer blocked.** Written in the M13 design
> session. Inputs: the maintainer's four sign-offs (§3); three blind
> re-derivations produced under `statistics-changes.md` §0 step 3 — the relative
> effect, the multiplicity layer, and the proportion interval (which absorbed the
> pooled-vs-unpooled question); and a code audit whose every claim is anchored to
> a line or derived in place
> ([code-audit.md](../research/2026-08-m13-blind-rederive/code-audit.md)).
> All three derivations are in the same directory with their contamination
> disclosures. **Nothing blocking remains open** — the last technical unknown
> (the sequential layer vs asymmetric intervals) was investigated on the same day
> and answered in §6(a)/D14. What is left is a UX design task (§5.2).
>
> **This is the first milestone of the track that MOVES statistical numbers.**
> M7–M12 all shipped under a no-numbers-move posture with parity gates. That
> posture inverts here, and §0.3 is its replacement.

## 0. Scope, posture & decisions

### 0.1 The six changes, regrouped

The ROADMAP contour lists six items. The audit shows they are **not six
independent changes** — they regroup into four, one of which is DROPPED (D13),
and two more WPs the contour never named (STAT-1b, STAT-1c) were added:

| Contour item | Regrouped as | Why |
|---|---|---|
| Holm over Bonferroni | **STAT-1** (correction layer) | read-time, whole-family rule |
| main-tier `metrics_count=1` FWER fix | **STAT-1** | same layer, same enum, same instrument |
| unpooled SE in the z-test CI | **STAT-3** | *one decision* with the item below — see §0.2 |
| Agresti-Caffo / Wilson proportion CIs | **STAT-3** | inverting the score test *is* Wilson |
| restore the relative-z "covariance term" | **STAT-4** | misnamed; it is the `R²` coefficient |
| uniform ddof=1 | ~~STAT-5~~ **DROPPED** | below the noise floor of our own instrument (D13) |

### 0.2 The two regroupings, stated once

**(a) "Unpooled SE" and "Wilson" are the same decision.** The z-test's p-value
is computed from the *pooled* (null) variance, and so is its CI
(`ztest.py:70-72, 101-102`). That is what currently makes "CI excludes zero" and
"p < α" agree **exactly** — and `pipeline/readout.py` decides significance by CI
exclusion. Replacing the CI's SE with the unpooled one, and nothing else, yields
a Wald interval beside a score p-value: they disagree near the boundary, and the
readout cannot express the disagreement. Inverting the score test instead gives
a **Wilson**-type interval, which is the other contour item, and preserves the
coherence by construction. One work package, one choice.

**(b) "The relative-z covariance term" is a misleading name.** The arms are
independent; there is no between-arm covariance. `relative_delta_effect`'s
`covariance` argument is `Cov(m̂₂−m̂₁, m̂₁) = −Var(m̂₁)`, an artifact of the shared
`m₁`. Expanding the t-test's call gives exactly `(R²V₁ + V₂)/m₁²`; the z-test's
shortcut gives `(V₁+V₂)/m₁²`. **What is missing is the `R²` coefficient on `V₁`.**
Spec wording must say that, or an implementer hunts for a correlation that does
not exist and concludes the change is a no-op.

### 0.3 The posture: numbers move, but no default does

The M7–M12 rule was "no statistical number moves". M13's is narrower and must be
stated precisely, because it is what makes the milestone safe:

1. **No default changes in `0.8.0`.** Every new estimator and every new
   correction scheme is opt-in (maintainer decision D1, §3). A project upgrading
   to `0.8.0` and changing nothing reproduces `0.7.0` byte-for-byte — and that is
   an executable gate, not a promise (§4).
2. **No `ALGORITHM_VERSION` bump is required, and none should be taken.**
   `method_config_id` hashes the method name, the **non-default identity
   params**, and the version. A new estimator arriving as an identity-flagged
   param whose default is the legacy value therefore orphans the series **of the
   operator who opts in, at the moment they opt in** — which is exactly the
   desired signal, scoped to the person who asked for it.
   `ALGORITHM_VERSION` is the tool for changing a *default*, and M13 changes no
   default. (This also closes the "migrate or recompute" question the design
   session was supposed to answer: nothing is orphaned that the operator did not
   orphan themselves.)

   **One caveat, found while confirming D4 and NOT specific to M13.** α is
   deliberately outside method identity (D3), so a config-level α change writes
   rows at a new α into an *existing* `method_config_id` series — a series can
   therefore contain looks decided at two different α. **D8's guardrail
   re-tiering is exactly such a change** (it moves a guardrail from the secondary
   α to the raw α at COMPUTE time). This is pre-existing behaviour — any operator
   editing `alpha:` today does the same — and it is *visible*, since `alpha` is
   persisted per row. The obligations are: say so in the CHANGELOG, and note that
   `--full-refresh` is what makes a series homogeneous again. Read-time schemes
   (Holm, BH) do **not** have this property: they change nothing persisted, only
   today's readout.
3. **Baseline goldens stay untouched.** Legacy parity keeps its own tests at
   rel-1e-9; every new number gets a **new** golden. The tolerance is never
   loosened (`contributing.md`, "Changing a statistical number").
4. **Every change still passes A/A revalidation** — *where the instrument can
   see it*. §0.4 is about where it cannot.

### 0.4 Where `abk validate` cannot arbitrate, and what replaces it

`statistics-changes.md` §0 step 4 says the A/A matrix arbitrates legacy vs blind.
For two of these changes it **provably cannot**, and the plan must say so rather
than run a sweep and read tea leaves:

- **The relative-effect shortcut is invisible at the null.** Its rejection set at
  θ = 0 is algebraically identical to the correct one, so the measured FPRs agree
  *to the last false positive* — not merely within Monte-Carlo noise. The
  discriminating signal is the **sign split** of the false positives, which the
  matrix does not currently record (STAT-2 fixes that), plus coverage under an
  injected effect.
- **Uniform ddof is below the noise floor.** Moving the measured FPR by 0.001
  needs the SE to move ~2%, i.e. n ≈ 50 — two to three orders below the engine's
  operating range. It can only be arbitrated by algebraic identity (§7 of the
  audit), and that limitation belongs in `statistics-changes.md` beside the
  change, not in a reviewer's head.

## 1. Work packages (ordered by value — see §2; none blocks another)

### STAT-1 — the correction layer

**The blind derivation landed** ([multiplicity.derivation.json](../research/2026-08-m13-blind-rederive/multiplicity.derivation.json))
and it changes this WP substantially. Four results, each decision-bearing:

**(a) The defect is a CLAIM defect before it is a numeric one — worst case
exactly `2α`, flat in `g` and `k`.** The budget sums to `2α` (main tier spends a
full α, secondary tier spends a second full α), attained by an explicit
construction and realised at `1 − e^{−2α}` = 0.0952 under independence. It is a
constant factor of two, not something that degrades with the metric count. And
the **main tier — the one the ship decision reads — is already at exactly
Bonferroni-α**, so "the probability of shipping on a spurious main-metric win is
≤ α" is true today and needs no change. What is false is the unqualified
experiment-wide "FWER ≤ α".

**(b) The current numbers are the levels of a VALID procedure whose gate is not
enforced.** Per arm pair: test the main comparison at `α/P`; only if it rejects,
test that pair's `k` secondaries at `α/(P·k)`. That is serial gatekeeping, it
controls FWER ≤ α over the whole family under arbitrary dependence, and its
levels are *exactly* the ones in the code today. ⇒ **There is a zero-number route
to an exactly-α claim: enforce the gate in the readout.** Stored intervals are
unchanged and remain exactly the intervals used; only the readout gains a
condition. Its cost is a UX/semantics change — a secondary metric's verdict
becomes conditional on its arm pair's main-metric win — which is a maintainer
decision, not a statistical one.

**(c) The largest available power gain is not Holm — it is declaring the
contrast set.** If the decision is "treatment vs incumbent", the family is `g−1`
many-to-one contrasts, not `C(g,2)`. That multiplies every main-tier level by
`g/2`: ≈ +10 points of power at `g=4` (an 18% sample-size saving at fixed MDE),
+6 at `g=3`. Holm, by comparison, gives **exactly zero** for the most significant
comparison and +2…+3 per already-rejected comparison (up to +27 for the last).
See **STAT-1b**.

**(d) The milestone contour has the guardrail direction backwards.** Correcting a
guardrail metric makes you *less* able to detect harm — an error in the dangerous
direction. Treating guardrails and screening metrics as one tier with one
direction of correction is, per the derivation, the deepest error in the current
scheme. A per-metric **role** declaration (screening / guardrail / decision) is
the fix; a uniformly tighter α is the opposite of one.

*What this WP builds — settled by D9: **it moves no number.***

- `holm` as a new enum value, implemented **read-time** beside BH in
  `composed_significance` (the Fork below is why it cannot be a compute-time
  level). Read-time means it changes nothing persisted — only today's readout.
- the `statistics-changes.md` entry recording (a) precisely: main tier at α,
  secondary tier at α, union ≤ 2α, ship decision at α. Not "it was wrong".
- **Neither** the readout gate nor a budget-corrected enum value ships. The gate
  suppresses secondary metrics exactly when they are most diagnostic — "the main
  metric is flat but retention dropped" is the reading it forbids — and after D8
  it would gate only the screening metrics whose purpose is to generate
  hypotheses. Halving the budget costs the secondary tier ~11 points of power to
  defend against an error nobody has articulated.

*What already exists and must not be rebuilt:* the `correction` enum
(`config/project_config.py:91`) with an experiment override; the read-time seam
`composed_significance`, shared by `readout.py` and the A/A family sweep; the
instrument `abk validate --family-sweep`; the client mirror `explore.ts:133`,
which passes any new read-time value through correctly by construction.

#### The Fork this WP must settle first (it is not a detail)

**No fixed per-comparison level can reproduce Holm — proven, not argued.** With
α=0.05, m=2, p₂=0.03: if p₁=0.001 Holm rejects both; if p₁=0.9 Holm rejects
nothing. Same p₂, opposite decisions ⇒ no pre-data level `ℓ₂` satisfies
`{Holm rejects H₂} = {p₂ ≤ ℓ₂}`. The same two lines kill Hochberg, Hommel, BH
and BY.

⇒ "significant ⟺ the stored interval excludes zero" and **any** step procedure
are incompatible. The engine had to choose, and **the maintainer chose Fork B on
2026-08-03 (D7): a decision and its stored interval MAY diverge, deliberately and
in writing.** Holm is therefore reachable.

*The seam already exists and is already shaped for this.* `pipeline/readout.py`
does **not** read the persisted `reject` column — it recomputes significance
through `composed_significance`, and its docstring already documents the two
regimes (Bonferroni/none → the CI excludes zero; BH → adjusted p against the
stored raw α). Holm slots in beside BH.

*What Fork B obliges this milestone to do:*

1. **Three things can now disagree, not two** — the stored interval, the
   readout's family decision, and the persisted **`reject`** column. `reject` is
   computed at COMPUTE time from one comparison at its stored α; under a step
   procedure it is by construction *pre-family*.
   **`_ab_results` is the project's BI contract**, and `reject` is the column an
   operator would naturally chart. Either rename/redocument it as a
   per-comparison pre-family flag, or add the family decision as its own column.
   Silence here ships a Grafana panel that disagrees with the product.
2. **The interval's level must be recoverable per row.** Already satisfied:
   `_ab_results.alpha` stores the effective per-comparison α (audit §8a).
3. **Any test asserting `decision == interval-excludes-zero` is now a
   lock-in** — it must be *replaced* by one that **pins the known divergence**
   (the `m=2, α=0.05, p₁=0.001, p₂=0.03` case: Holm rejects both while the
   α/2-level interval for comparison 2 covers zero). Candidates found:
   `tests/pipeline/test_correction_rule.py`, `tests/pipeline/test_readout.py`.
4. **The three renderers show a CI beside a verdict** (report, explore,
   dashboard). Under Fork B they can legitimately disagree, so the surfaces need
   a way to say so — otherwise the first divergence reads as a bug to the
   operator who hits it.

**This is already live under BH, undocumented.** `analyze.py:76-78` leaves
compute-time α raw under BH while the decision uses the BH-adjusted p — so abkit
is *already* in Fork B for one scheme without having chosen it. (Corroboration,
not an independent finding: the derivation's context was contaminated by the
auto-injected project rules, which state that BH is read-time. The code audit is
the primary source here.)

**A test asserting `decision == interval-excludes-zero` is a lock-in** — it
encodes Fork A as an invariant and would have to be deleted, not amended, to
adopt Holm.

*Persistence minimum, if Fork B is ever intended:* a row storing only
`(lo, hi, level)` is lossy — it cannot be re-inverted to another level without SE
and df, and that permanently forecloses every step procedure.

### STAT-1b — declare the contrast set (`vs_control` | `all_pairs`)

**New, from the derivation (c).** The largest power gain in the milestone, and it
is a *config declaration*, not new math: correcting for treatment-vs-treatment
contrasts nobody claims costs a factor of `g/2` in level. Under D1 it is opt-in
by construction (default `all_pairs` = today's behaviour). Interacts with M14
(the multi-arm decision layer), which is where an explicit `control:` field is
already planned — check for collision before building.

The maintainer's decision D2 puts this first and standalone: it is a different
layer from the estimators, it fails different tests, and its A/A revalidation
measures a different quantity (family FWER, not CI coverage).

*What already exists and must not be rebuilt:*

- the config knob — `correction: none|bonferroni|benjamini_hochberg`
  (`config/project_config.py:91`) with an experiment-level override;
- the read-time seam — `stats.correction.composed_significance(inputs,
  correction)`, shared by `pipeline/readout.py` and the A/A family sweep;
- the instrument — `abk validate --family-sweep` (M5 D9 / M7 WP7) already
  measures composed FWER/FDR over the family;
- the client mirror — `explore.ts:133` falls through to the raw alpha for any
  non-`bonferroni` scheme, so a new **read-time** value passes correctly by
  construction.

*What this WP builds:*

- `holm` as a new enum value, implemented **read-time** beside BH in
  `composed_significance` — Holm is a whole-family, p-value-ordering rule and
  cannot be expressed as a per-comparison α fixed at COMPUTE time;
- a **new** enum value for the budget-corrected two-tier scheme. The existing
  `bonferroni` keeps its exact current arithmetic, byte-frozen (§3, D5);
- the `statistics-changes.md` entry + a new golden; `test_golden_power_correction.py`
  keeps pinning the legacy scheme.

⏳ *Awaiting the derivation:* whether the current two-tier scheme controls FWER
at α at all (main spends `α/C(g,2)`, secondaries spend `α/(C(g,2)·k)` **on top**);
what the correct weighted allocation is; and — the question that decides whether
the readout survives unchanged — **what confidence interval is compatible with a
step-down rule**, given that this engine persists an interval per comparison and
reads significance off it.

*Known and to be documented either way:* CI-vs-verdict divergence is **already**
a live property under BH — compute-time α stays raw (`analyze.py:76-78`) while
the decision uses the BH-adjusted p. Holm inherits the same shape. No document
states this today.

### STAT-1c — guardrails stop being corrected like growth metrics

**The one defect in this milestone that costs SAFETY rather than power**, and the
cheapest to fix. Correcting a guardrail metric makes the engine *less* likely to
detect harm — the error points the dangerous way — yet `analyze.effective_alphas`
gives every non-main comparison the same tightened secondary α.

The declaration already exists end-to-end: `is_guardrail` is a config field
(`experiment_config.py:331`, *"Checked for regression only"*), validated as
mutually exclusive with `is_main_metric`, persisted to `_ab_results`, and shown
in the report, in `abk plan` and in the explore editor. **Only the alpha resolver
ignores it.** So this WP wires an existing declaration into an existing resolver;
it adds no schema, no config surface and no UI.

**Decided (D8, 2026-08-03): a guardrail is UNCORRECTED** — maximum sensitivity to
harm. Its per-comparison α is the raw experiment-level α, and it leaves the
secondary tier's budget entirely (so removing guardrails from that tier also
loosens α for the screening metrics that remain — a second, free gain).

*What that touches, and why it needs one seam rather than eight edits:*

- `TwoTierAlphas` becomes three-tier. It is returned by
  `analyze.effective_alphas` and consumed by `abk run`, `abk validate`,
  `abk plan`, `compute/reconcile`, `tuning/server`, `tuning/payload`,
  `cli/test_report` — **and mirrored in the browser** by
  `explore.ts#effectiveAlpha`, which is pinned in lockstep by
  `tests/tuning/test_explore_bundle.py`. This is exactly the shape of the M10
  lesson (a new knob reached none of eight hand-copied call sites): resolve the
  tier in **one** place and let every surface read it.
- **The A/A calibration chip keys on the EFFECTIVE alpha** (M4 D3/D16), so a
  guardrail's cells recalibrate at the raw α. Existing `_ab_aa_runs` rows for
  guardrail metrics become `alpha_mismatch` until re-run — expected, and it must
  be said in the CHANGELOG rather than discovered.
- Under D1 the current behaviour stays the default; the new treatment is opt-in.

### STAT-2 — the A/A matrix records the SIGN of each false positive

**Depends on:** nothing. **Blocks:** STAT-4. Runs in parallel with STAT-1.

A measurement-only WP that moves no number and must land **before** the change it
exists to arbitrate. The three candidate relative-effect estimators are
indistinguishable by FPR count; they differ in the *sign balance* of their false
positives (predicted left-tail share `0.5 + φ(z)z²·CV₁√w₁/α` for the delta
variant — ≈0.66 at CV₁=0.05, and **growing as α shrinks**, i.e. worst in exactly
the corrected two-tier regime STAT-1 introduces).

The sign is already computed — `validate/scoring.py:184` `_significance()`
returns `(significant, sign)` — so this is plumbing it through the result and the
matrix report, not new measurement.

### STAT-3 — the proportion interval: **Miettinen–Nurminen**, one statistic used three ways

**The derivation landed** ([proportion-interval.derivation.json](../research/2026-08-m13-blind-rederive/proportion-interval.derivation.json))
and it dissolves the pooled-vs-unpooled fork rather than choosing a side.

**The answer.** Define the two-sample score statistic with constrained-ML
variance, `Z(δ) = (p̂₂ − p̂₁ − δ)/σ̃(δ)`, where `(p̃₁, p̃₂)` maximise the binomial
likelihood subject to `p̃₂ − p̃₁ = δ`. Then use it three ways:

| Use | Form | Consequence |
|---|---|---|
| p-value | `2(1 − Φ(\|Z(0)\|))` | **identically the current pooled z** (Pearson χ²) — the reported p-value does not move |
| absolute CI | `{δ : Z(δ)² ≤ z²}` | a valid confidence set at *every* δ, not only at 0 |
| relative CI | same construction on the ratio scale | at ratio = 1 it is again the same pooled `Z` |

⇒ Coherence ("interval excludes zero" ⟺ "p < α") is preserved **by
construction**, and the interval stops being valid only at the null. §0.2(a)'s
worry is answered: the fix is not "switch the SE", it is "invert the statistic
you already compute".

**Why the current interval must nevertheless be fixed, and why in this
milestone.** The master law: an SE mis-scaled by factor `r` inflates the achieved
error rate by `exp(z²(1−r²)/2)`. For the pooled interval at a 900/100 split
(`r = 0.764`) that is **2.7× at α=0.05, 7.0× at α=0.004, 30× at α=1e-4**. The
damage grows exponentially as α shrinks — and α shrinks because of the
multiple-testing correction this same milestone is tightening. **The two defects
compound.**

**The magnitude, honestly split.** On *balanced* arms the whole controversy is a
non-event: switching only the SE would flip a verdict with probability ~7.6e-6.
Under imbalance it is first-order — at an 80/20 holdout with p=0.01, N=1e5, the
CI and the p-value disagree at 2.4e-3, i.e. **61% as often as there are
rejections** at α=0.004 — and the disagreement is *systematic*, its sign fixed by
which arm is larger and which has the higher rate.

**A third instrument warning, and the sharpest yet** (see D6): the FPR difference
between the two rules is second-order — the tails shift in opposite directions
and largely cancel — so **the A/A matrix would report "still calibrated" while
persisted verdicts on live imbalanced experiments had already moved.** In the
derivation's words: *a calibration that cannot see the change it is being asked
to certify is worse than no calibration.*

*Shipping shape.* An identity-flagged method param on `z-test`, default = today's
behaviour (D1); opting in changes `method_config_id` and orphans that operator's
series — the intended, scoped signal (§0.3).

*The one sub-decision:* the MN `N/(N−1)` variance factor. **Dropping it
(Farrington–Manning) makes `Z(0)` bit-identical to the classical pooled z, so no
reported p-value moves at all** — which is the cheapest option under D1. Keeping
it matches the published method and the R/SAS implementations a golden test would
compare against. It must be applied to the interval and the p-value together, or
to neither.

*Required sub-tasks the derivation names:* root-find robustness (is
`Z(δ)² − z²` guaranteed to have exactly two sign changes? at `x_j ∈ {0, n_j}` the
constrained MLE sits on a boundary and the shape changes — a bracketing scan plus
a **tested** fallback); and a suppression rule for the relative interval stated in
**conversions**, not units (below a few hundred per arm a relative effect is
unidentified, and a technically-correct `[−72%, +260%]` is misleading UX).

### STAT-4 — the relative effect: what the z-test should compute

**Depends on:** STAT-2 (without the sign instrument this cannot be arbitrated).

Three candidates, and the cheap one is not obviously right:

| Candidate | Cost | A/A FPR | Sign balance | Coverage |
|---|---|---|---|---|
| shortcut (today's z-test) | — | nominal | 0.50 | drifts with θ |
| delta (today's t-test) | one call to the existing `relative_delta_effect` | nominal | **asymmetric**, worse as α shrinks | drifts with CV₁ |
| Fieller | new code + the Tier-α consequence below | nominal | 0.50 | nominal |

**Settled by D10: Fieller — and it is the *less* disruptive of the two.**

The decisive fact is easy to miss and inverts the intuition that "cheap = safe":
**Fieller's rejection set at θ = 0 is identical to today's shortcut's**, so
adopting it changes **no verdict** — only the reported interval endpoints. The
cheap fix (routing the z-test through the existing `relative_delta_effect`, as
the t-test already does) *does* change the rejection set — its sign asymmetry is
precisely that change. So the correct estimator is also the one that leaves
decisions alone, while the cheap parity fix would silently move them.

Fieller also shares STAT-3's construction — both invert a score-type statistic —
so the asymmetry problem it creates for M5's sequential layer (§6a) is the *same*
problem, solved once for both rather than twice.

**STAT-2's role changes accordingly**: it is no longer the arbiter between two
candidates (D10 settles that on algebra), it is the **verification** instrument —
the sign balance and the unbounded-branch rate are how we prove the shipped code
is Fieller and not accidentally delta. Its dependency on STAT-4 is soft, not
blocking.

**If Fieller is chosen, explore's Tier α breaks — silently.**
`tuning/recompute.py:538` `_alpha_inverted_bounds` re-derives a symmetric normal
CI at a new α from persisted numbers. Fieller's half-width is **not**
proportional to `z` (the factor `g = z²V̂₁/m̂₁²` depends on α), so a
cached-SE × new-z path degrades to a delta interval at every α except the
computed one — and the tier is already labelled "approx", so the drift would not
look like a fault. Either Fieller recomputes `g` (making it Tier E, not an
inversion) or the relative effect leaves α-inversion. **This is a required
sub-task of choosing Fieller, not a follow-up.**

### ~~STAT-5 — uniform ddof~~ — **DROPPED (D13)**

The audit (§7) derives the whole effect, and it does not justify a WP:
the mixed convention lives only in θ, where it inflates it by exactly `n/(n−1)`;
CUPED's variance is minimised at θ*, so the cost enters **quadratically** in
`1/(n−1)` — ~1e-8 relative at n = 10⁴ — and the point estimate is unbiased for
any fixed θ under randomisation. The absolute branch is ddof-uniform internally
and non-negative by construction; the relative branch's genuine
positive-semidefiniteness hazard is **already guarded** with an explicit warning
(`effects.py:171-175`).

At the small n where ddof *would* matter (~1% on the variance at n = 100) it is
**dominated** by the normal-vs-Student-t approximation error (~2% at the same n),
which is deferred to M15. Fixing the smaller term while leaving the larger one is
not an improvement anyone can measure — and, per §0.4, our own instrument cannot
see either.

If it ships anyway, it ships as hygiene with the "not A/A-arbitrable" limitation
written into `statistics-changes.md`.

### STAT-6 — batch A/A revalidation + the exit gate

**Depends on:** all of the above.

## 2. Dependency graph

```
STAT-1c (guardrails)  ────────────────────────────────────┐   safety; independent
STAT-1b (contrast set) ───────────────────────────────────┤   biggest power win
STAT-1  (Holm / the Fork) ────────────────────────────────┤
STAT-2  (sign instrument) ──▶ STAT-4 (relative effect) ───┼──▶ STAT-6 (exit gate)
STAT-3  (proportions) ⏳ ─────────────────────────────────┤
STAT-5  (ddof — recommended dropped) ─────────────────────┘
```

The correction layer split into three independent WPs once the derivation
landed, and their order is by **value, not dependency** — none blocks another:

1. **STAT-1c** first. It is the only safety-directed item, and its declaration,
   storage and UI already exist (audit §8b).
2. **STAT-1b** next. Declaring the contrast set buys `g/2` in level — more power
   than Holm gives, for a config field rather than new math.
3. **STAT-1** last, because it is the one that needs the Fork settled, and the
   Fork is the maintainer's call.

STAT-2 is small and can run in parallel with any of them.

**Fork B is reachable** — `_ab_results` already stores `pvalue`, the effective
`alpha`, `std_1/2` and `size_1/2`, so the family p-vector assembles at read time
and the SE is recoverable (audit §8a). Degrees of freedom are the one absent
field, and only M15's Student-t would need them.

## 3. Decisions

| # | Decision | Status |
|---|---|---|
| D1 | **Opt-in first.** No default moves in `0.8.0`; a project that changes nothing reproduces `0.7.0` byte-for-byte. | signed off 2026-08-03 |
| D2 | **Split by layer.** The correction WP ships first and standalone; the estimator WPs follow. They fail different tests and their A/A revalidation measures different quantities. | signed off 2026-08-03 |
| D3 | **The correction scheme is versioned by the config field**, not by `method_config_id` (α is experiment-level and deliberately outside method identity). | signed off 2026-08-03 |
| D4 | **No `ALGORITHM_VERSION` bump in M13.** Under D1 an identity-flagged param with a legacy default already orphans the opting-in operator's series; the version field exists for changing a *default*. This also closes "migrate vs recompute" — nothing is orphaned that the operator did not orphan themselves. | confirmed 2026-08-03, delegated |
| D5 | **The `metrics_count=1` fix is a NEW enum value; `bonferroni` is byte-frozen.** Changing what an existing YAML value computes between `0.7.0` and `0.8.0` is a silent number change routed through config. | derived, signed off 2026-08-03 |
| D6 | **Where A/A cannot arbitrate, say so in `statistics-changes.md`** rather than running a sweep that cannot answer (§0.4). | confirmed 2026-08-03, delegated |
| D7 | **Fork B: a decision and its stored interval MAY diverge**, deliberately and in writing. Holm becomes reachable; `reject`'s meaning in the BI contract must be settled, and the identity tests become divergence tests. | signed off 2026-08-03 |
| D8 | **Guardrails are UNCORRECTED** — raw experiment-level α, out of the secondary tier's budget entirely (which also loosens α for the screening metrics remaining in it). | signed off 2026-08-03 |
| D9 | **The FWER item moves NO number: fix the claim, add Holm, leave the levels alone.** The gate is rejected (it suppresses secondary metrics exactly when they are most diagnostic, and after D8 it would gate only the screening metrics whose job is to generate hypotheses). Halving the budget is rejected too (~11 points of secondary power to defend against an error nobody has articulated). `statistics-changes.md` states precisely what is controlled: main tier at α, secondary tier at α, union ≤ 2α, and the ship decision — which reads the main tier — at α. | decided 2026-08-03, delegated |
| D10 | **STAT-4 = Fieller, not the cheap delta parity.** Decisive fact: Fieller's rejection set at θ=0 is *identical* to today's shortcut, so adopting it changes **no verdict** — only interval endpoints. The "cheap" delta fix *does* change the rejection set (that is what its sign asymmetry is). The correct change is the non-disruptive one. It also shares STAT-3's score-inversion shape, so the sequential-asymmetry problem is solved once for both. | decided 2026-08-03, delegated |
| D11 | **MN ships WITHOUT the `N/(N−1)` factor** (Farrington–Manning form), making `Z(0)` bit-identical to today's pooled z so no reported p-value moves. Byte-stability of the p-value outranks matching R/SAS; the difference is `1/(2N)`. Golden tests compare against FM, and the docstring says why. | decided 2026-08-03, delegated |
| D12 | **`_ab_results.reject` keeps its name and is REDOCUMENTED as pre-family** — "rejection of this one comparison at its stored α, before any read-time family rule". No family-decision column is added: under a read-time scheme that value is re-derived on every read and a persisted copy would go stale the moment `correction` changes. The BI recipes state that the family decision lives in the readout. | decided 2026-08-03, delegated |
| D14 | **The sequential layer DOES extend to score intervals** — the always-valid rule is a standardised test with `c(V)` in place of `z`, so the confidence sequence is `{δ : \|Z(δ)\| ≤ c(V)}`, a critical-value substitution inside the root-find MN/Fieller already run. The blocker is architectural, not mathematical: `to_always_valid` *infers* the SE from the CI width assuming symmetry, unvalidated, at six call sites (the A/A sequential column among them). **`asymmetric_ci: ClassVar[bool] = False` is a hard prerequisite of STAT-3** — it turns a silent miscomputation into a loud refusal. | decided 2026-08-03, delegated |
| D13 | **STAT-5 (uniform ddof) is DROPPED from M13** — second-order, below the instrument's noise floor, dominated at small n by the normal-vs-Student-t error deferred to M15 (audit §7). Dropping it edits the ROADMAP contour. | decided 2026-08-03, delegated |

## 4. Exit gate (sketch)

1. **The byte-compatibility gate is the milestone's №1 assertion**: the scaffolded
   project, unchanged, produces `_ab_results` identical to `0.7.0` — discrete
   columns exactly, continuous at rel-1e-9, JSON payload columns parsed before
   comparison (the M9 lesson: a θ differs in its last ULP and comparing serialized
   strings demands a property IEEE-754 does not offer).
2. Every new estimator/scheme has a **new** golden; every legacy golden still
   passes at rel-1e-9.
3. Opting into any new value changes `method_config_id` — pinned, since it is
   what makes D4 safe.
4. `abk validate --family-sweep` over the corrected scheme shows FWER ≤ α where
   the old scheme does not (STAT-1's whole claim).
5. The A/A matrix reports false-positive **signs** (STAT-2), and the reported
   split matches the derivation's prediction for whichever estimator is chosen.
6. `grep ALGORITHM_VERSION` shows no bump (D4).

## 5. Before start — open questions

**Everything the design session had to settle is settled** (D1–D13). What remains
is one technical question the *first WP* must answer, and one UX question:

1. ~~§6(a) — the sequential collision~~ — **investigated and answered (D14).**
   The confidence sequence *does* re-derive on the score scale: the always-valid
   rule is a standardised test with a variance-dependent critical value `c(V)`
   substituted for `z`, inside the root-find MN and Fieller already perform. What
   the investigation *did* find is worse than the original worry and is now a
   hard prerequisite: the transform silently mis-recovers the SE from an
   asymmetric interval (it infers `SE = ci_length/2z`, unvalidated) at six call
   sites including the A/A instrument's own sequential column. **STAT-3 cannot
   ship without the `asymmetric_ci` capability flag** (§6a item 1).
2. **How the three renderers show a CI that legitimately disagrees with the
   verdict beside it** (D7's consequence). Report, explore and dashboard all
   display both. The first divergence an operator meets will read as a bug
   unless the surface says otherwise — this is a design task, and the §4 marker
   discipline (`abk-prehorizon` / `abk-insufficient` / `abk-srm-fail`) is the
   existing precedent for how such a state gets expressed.
7. **Derivations: two of five produced, and the remaining three are no longer
   blocking.** Done: the relative effect and the multiplicity layer, plus the
   proportion interval (which absorbed the pooled/unpooled question — they were
   one question). Not produced: a standalone ddof derivation, which the code
   audit (§7) already answers well enough to recommend dropping the change.
   If any further derivation is run, note that **an agent inside this tree is
   never blind** — the harness auto-injects `CLAUDE.md` and `.claude/rules/*.md`.
   Both delivered derivations disclosed exactly what leaked; require that.

## 6. Inter-milestone collisions

**(a) M5's sequential layer vs score intervals — INVESTIGATED 2026-08-03, and the
answer inverts the problem.**

*First, the collision is worse than "cannot consume".* The transform does not
take `(effect, SE)` at all: `to_always_valid` calls
`se_from_ci_length(result.ci_length, alpha)`, which **infers** the SE from the
interval's width by assuming it is symmetric-normal — the docstring states the
assumption outright ("every parametric method builds its fixed CI as
`effect ± z·SE`"). That is true today of all 12 methods, because they all route
through `effects.normal_test`. **Nothing validates it.** For an asymmetric
interval `se_from_ci_length` returns a finite number that is not the SE (it is
the mean half-width over `z`), and `sequentialize` then centres a symmetric
always-valid interval on the point estimate with a radius built from it. No NaN,
no exception — **silently wrong**.

Severity is highest exactly where the new intervals are worth having: the
recovery is nearly right when the interval is nearly symmetric (large n, small
`CV₁`, `p` away from 0) and degrades as asymmetry grows. Fieller's *unbounded*
branch is safe by accident — `ci_length = ∞` fails the `math.isfinite(se)` guard
and lands in the NaN bucket.

*And the defect would reach the instrument.* Six call sites, none checking:
`pipeline/analyze.py:214`, `tuning/recompute.py:1055`, `planning/sizing.py:391`,
and — the bad one — `validate/scoring.py:249` + `validate/family.py:291`, i.e.
**the A/A matrix's own sequential column**. The instrument would not merely fail
to see the problem; it would compute on the same mis-recovered SE.

*Second, the mathematical answer is YES.* Standardising the shipped boundary
(`confidence_sequence.sequentialize`, with `V = SE²`):

```
radius = √( (2V(V+τ²)/τ²) · (ln(1/α) + ½·ln((V+τ²)/V)) )
⇒ radius/√V = c(V) = √( 2(1 + V/τ²) · (ln(1/α) + ½·ln(1 + τ²/V)) )
```

So the always-valid rule is an ordinary test on a **standardised** statistic with
a variance-dependent critical value `c(V)` in place of `z`. That applies to any
statistic asymptotically N(0,1) under its null — **including the score statistic
`Z(δ)`**. The confidence sequence is then

```
{ δ : |Z(δ)| ≤ c(V) }        instead of        { δ : |Z(δ)| ≤ z }
```

— a substitution of the critical value **inside the root-find MN and Fieller
already perform**. Nothing new is needed mathematically; `mixture_tau2` is
untouched.

*So the obstacle is our post-hoc architecture, not the score interval.* The
current design must **recover** an SE it was never given; the score design merely
swaps `z` for `c`. An asymmetric interval is harder to *widen after the fact*, and
no harder to *construct sequentially*.

*What STAT-3/STAT-4 must therefore build:*

1. **A capability flag, following the project's own pattern.**
   `supports_vectorized` and `supports_resample_memo` are both `ClassVar[bool]`
   on `BaseMethod` with a "False default keeps every method working" discipline
   and an explicit raise when a method lies. Add **`asymmetric_ci: ClassVar[bool]
   = False`** — default False is today's truth for all 12 methods, and a score
   method opts *in*. Every `se_from_ci_length` caller refuses an asymmetric
   method **loudly** instead of mis-recovering. This alone converts the silent
   failure into a stated limitation, and is the minimum STAT-3 cannot ship
   without.
2. **The critical value must enter the construction**, so a score method needs an
   entry point that builds its interval at a given critical value rather than at
   `z`. `to_always_valid(TestResult) → TestResult` cannot express that and stays
   the path for symmetric methods.
3. **One documented choice:** `c` depends on `V`, and for a score interval the
   variance `σ̃(δ)²` varies with `δ`. Evaluating `c` at the null variance, at
   `δ̂`, or δ-dependently are all defensible; the δ-dependent form keeps the
   "the sequence is the set of δ not rejected by the always-valid test at δ"
   reading exact, at the cost of making the root-find's bracketing question
   (already flagged in STAT-3) strictly harder.

**Fallback if (2) proves expensive:** ship (1) alone. The new intervals are then
unavailable under `sequential.enabled` — acceptable under D1, and now a *stated*
limitation with a loud error rather than a silent miscomputation.

**(b) `abk plan`'s power/MDE machinery is Wald-based.** If the analysis rule
becomes score-based, a stated MDE no longer inverts the rule that will actually
be applied — a planned effect would not be the effect the analysis detects at the
stated power. The discrepancy is `O(z²/N)` balanced and larger under imbalance;
probably ignorable, but it must be **measured** rather than assumed, and `abk
plan` is a shipped surface with its own tests.

**(c) M14 wants the same declaration as STAT-1b.** The multi-arm decision layer
plans an explicit `control:` field; the contrast-set declaration needs exactly
that information. Build it once.

**(d) BH already contradicts the interval semantics.** Under a data-dependent
threshold there is no fixed α to build an interval at, so "the interval excludes
zero" is not an interval statement — it is a p-value statement wearing an
interval's clothes. This is live today, undocumented, and it is the same Fork
STAT-1 must settle.

**(e) Overdispersion outranks all of this, and is out of scope.** If conversion
is counted per session while randomisation is per user — or a unit can convert
twice — every SE here understates the truth by a factor no choice of
pooled/unpooled/score affects. The A/A permutation **would** see it (it permutes
units, inheriting the true within-unit correlation), which is a case where the
instrument is the right one. Worth a documented note, not a WP.
