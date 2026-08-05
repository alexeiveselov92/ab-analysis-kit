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
| restore the relative-z "covariance term" | **STAT-4** | misnamed; it is the `R²` coefficient — and STAT-3 answered it for the z-test, so STAT-4 shipped on the MEAN methods |
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

### STAT-1 — the correction layer ✅ SHIPPED

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

#### As built

Shipped exactly as decided: **no number moved and no `ALGORITHM_VERSION` was
bumped**; the two-tier levels are byte-identical and `holm` is a fourth,
default-off enum value.

- **`stats.correction.holm_adjusted`** — `adj_(i) = max_{j≤i} (m−j+1)·p_(j)`,
  capped at 1 — plus the classification pair `READ_TIME_CORRECTIONS` /
  `COMPUTE_TIME_CORRECTIONS`. `composed_significance` now dispatches on
  `_FAMILY_ADJUSTERS`, so BH and Holm share one body and differ only in the
  adjuster; an unknown scheme name still takes the compute-time branch (a stale
  persisted string must not crash a report).
- **The load-bearing delta the design did not have: the scheme classification is
  in ONE place, with a roster gate.** Three modules tested `!=
  "benjamini_hochberg"` by NAME (`readout._build_sig_map`, `validate/runner`'s
  family-budget anchor, and the compute-time branch itself). Two would have
  silently handed Holm the per-row CI rule — a scheme that appears to work while
  controlling nothing; the A/A anchor would have judged it against the Bonferroni
  composition ≈Σα rather than ≈α, so the instrument itself would have called a
  miscalibrated Holm family green. `tests/pipeline/test_correction_rule.py`'s
  `TestSchemeRoster` asserts `READ_TIME ∪ COMPUTE_TIME` **equals** the config
  literal, that the two are disjoint, that every read-time scheme has an adjuster
  that is actually reached, that every one has an operator-facing LABEL (the map
  the gate first forgot), and that the project and experiment literals agree.
  This is the m12 NTF-1 roster-gate pattern, and it is what makes STAT-1's own
  regression impossible to reintroduce.
- **Fork B is disclosed, not merely permitted.** Two changes in
  `pipeline/readout.py`: the rationale stops saying "CI excludes zero" when a
  family rule decided (`_sig_phrase`/`_quiet_phrase` — it named a per-comparison
  fact as the reason for a family-level decision), and a pair whose stored
  interval excludes zero while the family rule declines to reject carries an
  explicit caveat plus the structured `PairVerdict.family_divergence`. The report
  and the dashboard render `caveats` verbatim; notifications render their own
  sentence off the FLAG, because a message shows an interval beside a verdict
  with no report to click through to (M12: a notification cannot disagree with
  the report about the same experiment) and sniffing a caveat STRING is how prose
  becomes API; `abk explore` shows neither — it never calls `evaluate`, by design.
  The caveat is deliberately gated on the family having been **consulted**: SRM,
  the pre-horizon refusal and the small-sample demotion each answer INCONCLUSIVE
  for a reason of their own, and blaming the correction for them would be a
  different lie.
- **The divergence is one-directional, and that is a pinned property**
  (`test_holm_never_rejects_more_than_the_stored_interval_does`): a family rule is
  never looser than the member's own raw alpha, so an operator can see an
  interval excluding zero under a refusing verdict but never the reverse. That is
  what makes a single caveat sufficient.
- **`_ab_results.reject` was redocumented, not renamed** (D12), in all five
  places that described it — the data contract, the internal-tables reference,
  the BI example README, the visualizing-results guide and the packaged operator
  rules. Two of them called it "abkit's composed decision", which is exactly the
  Grafana-disagrees-with-the-product failure this WP existed to prevent.
- **`abk plan` now names the read-time regime in its header** and
  `_correction_note` takes the resolved scheme as a REQUIRED argument: under a
  read-time scheme every level it prints is the raw alpha, and a caller that
  forgot to pass the scheme would print the most misleading header of the three.
- **The A/A family sweep** anchors Holm's nominal rate at the members' level (α),
  where BH already sat — under Holm a family measuring ≈Σα means the *methods*
  are miscalibrated, which is what the sweep exists to catch.

### STAT-1b — declare the contrast set (`vs_control` | `all_pairs`) ✅ SHIPPED

**New, from the derivation (c).** The largest power gain in the milestone, and it
is a *config declaration*, not new math: correcting for treatment-vs-treatment
contrasts nobody claims costs a factor of `g/2` in level. Under D1 it is opt-in
by construction (default `all_pairs` = today's behaviour). D15 settled the M14
question: the contrast SET and M14's `control:` field are different declarations,
and the second is already resolved positionally, so this WP did not wait.

*What already existed and was not rebuilt:* the two-tier resolver
`analyze.effective_alphas` + `stats.correction.two_tier_alphas`; the client
mirror `explore.ts#effectiveAlpha`; the `abk validate --family-sweep` instrument.

*What shipped:*

- **`contrasts: all_pairs | vs_control` on the experiment**, with **no
  project-level default** — the one deliberate asymmetry against the
  `correction`/`guardrail_correction` precedent (D16 below).
- **`ExperimentConfig.contrast_pairs()`** — the factory, and the WP's
  load-bearing delta. `contrasts` reaches the alpha divisor
  (`n_comparisons`/`adjust_alpha`/`two_tier_alphas` gained a `contrasts`
  argument defaulting to the legacy family, and `TwoTierAlphas` carries it) AND
  the enumeration in `analyze_cutoff`, so both halves resolve from one place.
- **The four filters that decide which persisted rows are still declared** —
  `reporting/builder.py`, `tuning/overview.py`, `notify/dispatch.py` and the
  producer — now read that factory, gated by
  `tests/config/test_contrast_pairs_is_the_only_entry.py`. Each had carried its
  own `combinations(experiment.assignment.variants, 2)`; `notify/dispatch.py`
  said in a comment that "a fourth copy should force the extraction", and a
  knob-dependent set is exactly the case where four copies stop being a style
  question.
- **The display surfaces**: `pairs_phrase` (shared by `abk run`/`abk validate`),
  `abk plan`'s header note, its per-refresh row estimate and its multi-arm
  warning, and the explore client mirror + baked knob block.

*As-built deltas the design did not have:*

1. **The sequential re-plan predicate had to learn the family.**
   `driver._sequential_mode_changed` re-plans a whole series when a persisted
   `ci_kind` disagrees with the mode the run would stamp. A row for a pair the
   narrowed family no longer claims can never be superseded — nothing recomputes
   it — so the predicate would have stayed true forever: a full-series re-plan on
   every scheduled run, silent, for rows no surface reads. It now judges declared
   pairs only.
2. **The stale-pair warning named the wrong remedy, and had before this WP.**
   It advised `abk clean`, which prunes series by `method_config_id` and has
   never had a pair-level sweep; `abk run --full-refresh --from … --to …` is
   what deletes the window before rewriting the declared pairs (the window
   bounds are required, so the first replacement string was itself unrunnable —
   caught by the review). STAT-1b gave the same warning a second cause, which is
   how the wrong advice surfaced.
3. **The anti-join had to become pair-complete.** `list_computed_cutoffs` asks
   "has this `end_ts` been touched?", which stopped being the same question once
   the family was declarable: WIDENING it (back to `all_pairs`, or by adding an
   arm) leaves every historical look touched-but-incomplete, so the new
   contrasts exist only from the flip onward while the surviving pairs keep an
   alpha bought for the narrower family — the anti-conservative direction, and
   silent. `list_complete_cutoffs` counts a cutoff as computed iff it carries a
   row for every declared pair; a re-planned cutoff rewrites all of them by LWW,
   so the alpha re-homogenises for exactly the affected looks and the next run
   plans zero.
4. **The filter belonged in `evaluate()` too.** The three surface copies protect
   their own warnings, but the read-time BH family is built inside the readout —
   so a direct caller (a notebook) scored a family of `C(g,2)` for an experiment
   that declared `g−1`. `_filter_rows` now drops undeclared pairs with its own
   warning, alongside the orphaned-`method_config_id` and unbound-metric cases.
5. **The cockpit was an unfiltered fourth reader.** `tuning/session.py` loaded
   every persisted pair, so `/recompute` spent Tier-S budget on series that are
   not on the page and could raise the sequential-reload warning for a contrast
   the experiment does not claim.
6. **A project-level `contrasts:` is refused loudly** (D16's own consequence):
   pydantic's `extra="ignore"` would have accepted the key beside `alpha`,
   `correction` and `guardrail_correction` — all of which DO have project
   defaults — and changed nothing.
7. **Two surfaces that state a level had to name the family**: the HTML report
   (arms line) and `_ab_experiments.contrasts` (added additively, for the BI
   join). `guardrail_correction` is still absent from that catalog — a recorded
   gap from STAT-1c, with the per-row `alpha` the authority in both cases.

*What the instrument cannot say (D6 posture).* `abk validate --family-sweep`
composes over **metrics**, never over arm pairs, so both the measured family
FWER and the nominal budget move together when the divisor changes: the sweep
can neither confirm nor refute the `g/2` claim. The claim is arithmetic and is
pinned as arithmetic (`tests/pipeline/test_contrast_set.py`); a pair-dimensioned
sweep is a named follow-up, not something this WP pretended to have.

### STAT-1c — guardrails stop being corrected like growth metrics ✅ SHIPPED

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

#### Build spec (mapped against the code, 2026-08-03)

1. **The knob.** `ProjectStatisticsConfig.guardrail_correction: Literal["inherit",
   "none"] = "inherit"` (`config/project_config.py`, beside `correction`), plus
   the experiment-level `| None` override — the exact shape `correction` already
   uses. `"inherit"` is today's behaviour: a guardrail shares the secondary
   budget. `"none"` is D8. Nothing here enters `method_config_id`.
2. **`TwoTierAlphas` gains `guardrail: float | None`** (`stats/correction.py`).
   `None` means "no separate tier — guardrails use `secondary`", so every
   existing consumer that ignores the field keeps working.
3. **`effective_alphas` (`pipeline/analyze.py:59`) changes in TWO places, and the
   second is the free gain.** Under `"none"`: `guardrail = alpha` (raw), **and
   `metrics_count` must stop counting guardrails** —
   `non_main = Σ(not is_main_metric)` today includes them, so removing them from
   the tier also *loosens* α for the screening metrics that remain. Forgetting
   the second half yields a change that helps guardrails and silently taxes
   nothing else — half the intended effect, and no test would notice.
4. **`comparison_alpha` (`analyze.py:81`)** routes `is_guardrail` to
   `alphas.guardrail` when it is not `None`. Keep the existing
   `secondary is None` fallback ahead of it.
5. **The browser mirror is the m10 hazard, and it needs THREE new inputs.**
   `explore.ts#effectiveAlpha` (`web/src/explore/explore.ts:126`) recomputes α
   live as the operator drags the alpha/correction knobs, so it cannot be handed
   a resolved number — it must learn the rule. It needs: the mode, a per-metric
   `guardrail` flag (the roles map at `explore.ts:629` carries `main` only), and
   a `non_main_count` that already **excludes** guardrails under `"none"`
   (`tuning/payload.py:112` bakes it). Pinned in lockstep by
   `tests/tuning/test_explore_bundle.py`; the bundle must be rebuilt and
   committed in the same PR (the `abk-web-bundle` discipline).
6. **Other consumers to visit** (the signature change makes them visible, which
   is the point): `cli/commands/plan.py:796` prints the tier line and would need
   a third tier; `validate/runner.py` sizes per-cell iterations off the effective
   α; `compute/reconcile.py`, `tuning/server.py`, `cli/commands/test_report.py`
   pass it through.
7. **Tests.** Unit coverage on `effective_alphas` for both modes × (main,
   secondary, guardrail) × `k = 0`; the `k = 0` edge matters because removing the
   only non-main metric from the tier makes `secondary` `None`. Plus the explore
   lockstep test, and an assertion that a guardrail's persisted `alpha` equals the
   raw α under `"none"`.
8. **CHANGELOG must state the A/A consequence** (M4 D3/D16): the calibration chip
   keys on the **effective** α, so existing `_ab_aa_runs` rows for guardrail
   metrics read `alpha_mismatch` until re-run.

### STAT-2 — the A/A matrix records the SIGN of each false positive ✅ SHIPPED

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

### STAT-3a — the `asymmetric_ci` guard ✅ SHIPPED

**Depends on:** nothing. **Blocks:** STAT-3 and STAT-4 (D17).

Turns §6a's silent mis-recovery into a loud refusal, and moves **no number**: no
shipped method builds an asymmetric interval, so every refusal it adds is
unreachable today and the whole suite is byte-identical. That is the point of
shipping it alone — the gate is byte-parity, which stops being cheap the moment a
new interval lands in the same PR.

**As built.**

1. **`BaseMethod.asymmetric_ci`, default `False`** — "this method's fixed CI is not
   `effect ± z·SE`". Pinned empty across the registry by a roster gate, so a future
   method flipping it is a conscious act.
2. **`sequential.require_symmetric_ci(method, entry=…)`** is the one gate, and the
   `method` argument is **required and keyword-only** on `se_from_ci_length`,
   `se_from_ci_length_array` and `to_always_valid`. A caller may not invert a CI
   without saying whose it is — a structural forcing function, not an AST rule: a
   new call site does not run until it names its method.
3. **Resolved per bound INSTANCE, not per class** — the design said `ClassVar`
   (§6a item 1), following `supports_vectorized`, and that would have been a guard
   that cannot fire for the configuration STAT-3 actually ships: an
   identity-flagged **param** on `z-test` (§ STAT-3's shipping shape) leaves the
   class default symmetric. `asymmetric_ci` is therefore a plain class attribute a
   subclass narrows from `self.params`, every entry takes an instance, and handing
   a class in raises `TypeError` rather than being answered `False`. The one caller
   holding only a class — `driver._sequential_tau2` — now binds the method the way
   `analyze_cutoff` does.
4. **An asymmetric method is not blocked, only un-widened.** Declaring
   `supports_sequential: false` is what every eligibility gate (driver, analyze,
   explore's `av_pairs`, the A/A `_cell_tau2`) already tests, so such a method's
   series stays fixed with no error. The refusal fires only for a method claiming
   both — which is a contradiction, since `supports_sequential` has always *meant*
   a symmetric CI.
5. **The refusal reaches the operator on every surface.** `AsymmetricCIError` is a
   `StatsError`, so `abk validate`'s per-cell isolation (the m4 F1 bootstrap
   precedent) reports it as a **failed cell carrying its reason** rather than a
   quietly missing sequential column; `abk run` fails the experiment with the
   message on the outcome; explore raises out of the recompute.

**The load-bearing delta: a TWELFTH entry point the design's count missed.**
D17 counted eleven entries into `se_from_ci_length`. `tuning/recompute.
_alpha_inverted_bounds` — explore's Tier α — is a twelfth: it does not call the
helper at all, it **open-codes** `se = (right − left) / 2z` and re-derives a
symmetric normal CI at the new α from persisted numbers. STAT-4 already knew this
function was a problem for Fieller (it is a named required sub-task there); what
the count missed is that it carries the *same* premise and would have been left
unguarded by a fix aimed only at the helper. It now takes the same refusal, and
an AST gate (`tests/stats/sequential/test_ci_inversion_is_the_only_entry.py`)
fails on any new open-coded inversion — a CI width divided by a quantile — outside
a function that calls the guard, and on any guarded call that omits its method.
Both rules are derived from the source and both are proven to bite on hostile
fixtures; a `/2` half-width is deliberately not an inversion (it recovers nothing),
which keeps the rule narrow enough that guarding everything is not the only escape.

**Named follow-up for STAT-3/STAT-4** (found by that gate, deliberately not fixed
here): two surfaces summarise an interval by its **mean half-width** —
`readout.py`'s always-valid FLAT power check (`av_half ≤ min_effect`) and explore's
`ci_half` chip. Neither recovers an SE, so neither is wrong today; both would
under-describe an asymmetric interval, whose reach differs by side.

**Test-isolation lesson worth carrying:** `tests/stats/test_registry_factory.py`
reloads `abkit.stats.parametric.ttest`, after which the imported `TTest` symbol is
a stale object the registry no longer resolves to. A test that flips a capability
flag must patch `get_method_class("t-test")`, never an imported class — the
difference is invisible when the file runs alone.

### STAT-3 — the proportion interval: **Miettinen–Nurminen**, one statistic used three ways ✅ SHIPPED

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

*Unblocked: **STAT-3a** (D17) shipped* — the `asymmetric_ci` capability flag and
its refusals (the WP above). The guard belongs to the functions that ASSUME
symmetry — `se_from_ci_length` and, as built, explore's open-coded α inversion —
never to `to_always_valid`; **twelve** entry points, seven of them inside `abk
validate`'s own scoring and family sweep. **STAT-3 must set `asymmetric_ci` on the
bound instance** whenever its interval param selects the score form (the flag is
instance-resolved precisely so this is expressible), and decide the two things the
guard deliberately leaves open: whether an MN comparison declares
`supports_sequential: false` or gains the §6a item 2 construction, and what
explore's α tier shows instead of a re-derived symmetric CI.

*Required sub-tasks the derivation names:* root-find robustness (is
`Z(δ)² − z²` guaranteed to have exactly two sign changes? at `x_j ∈ {0, n_j}` the
constrained MLE sits on a boundary and the shape changes — a bracketing scan plus
a **tested** fallback); and a suppression rule for the relative interval stated in
**conversions**, not units (below a few hundred per arm a relative effect is
unidentified, and a technically-correct `[−72%, +260%]` is misleading UX).

#### As built

`interval: pooled | score` on `z-test` — identity-flagged, defaulted to the legacy
branch. **No p-value moved and no default moved**; `ALGORITHM_VERSION` untouched.
Deviation record: [statistics-changes.md §4.4](statistics-changes.md).
The math lives in one pure module (`abkit/stats/proportion_score.py`), the method
only chooses between two branches, and the gates are
`tests/stats/test_proportion_score.py` (the derivation's KATs) +
`tests/stats/test_ztest_score_interval.py` (the deviation and what must NOT move).

**Every sub-decision the WP was told to make, and how it went:**

- **The `N/(N−1)` factor is dropped** (D11 confirmed): the p-value branch is then
  not merely equivalent but *the same code*, so "no p-value moves" is an equality
  assertion rather than a tolerance one.
- **Both scales ship together.** The relative interval is the ratio-scale score
  construction (Route C, a quadratic rather than the difference scale's cubic), so
  three-way coherence holds. Shipping only the absolute scale would have left the
  default `test_type` — `relative` — on the shortcut it was opting out of.
- **`supports_sequential` was the WRONG vehicle for the refusal**, and finding out
  why is the WP's transferable lesson. It is a `ClassVar` read at CLASS level in
  five places (`plan`, `recompute`, `analyze`, `driver` ×2), so an instance-level
  narrowing would have been invisible to every eligibility gate — the exact shape
  STAT-3a warned about, one level up. The refusal therefore ships where the
  contradiction is STATIC: **a level-2 config error** naming both knobs. The
  STAT-3a `AsymmetricCIError` stays the backstop, and its test moved from "explore
  raises" to a direct call, since the caller now skips the tier.
- **Explore's α tier answers with a GAP**, not an approximation. Tier E is tried
  first and is exact for a fraction row, so a row that reaches the α tier has no
  point at the dragged alpha — and a tier already labelled "approx" is where a
  wrong-shaped interval would go unnoticed.
- **`abk plan` had to learn the interval shape** (the third surface — STAT-1's
  rule, with "scheme" read as "estimator"): it suppresses the ASN, since `abk run`
  refuses that mode, and notes that its sizing is Wald-based. §6(b) is now
  **measured**: the half-widths differ by `C·z²/n_arm` with C stable in n to three
  digits (4.01 at a 5% baseline, 0.060 at 30%). The bind that reads the shape also
  makes `abk plan` the first surface to validate method params at all — an invalid
  comparison is refused by name instead of sized against defaults it never had.
- **The identification rule is a WARNING stated in conversions**, not a
  suppression: hiding a correct interval is the worse failure, and the threshold
  carries `z` so it tightens by itself as the correction shrinks α.

**Numerics, which the design underestimated.** The published closed-form
constrained MLE (the trigonometric cubic root) is accurate to ~1e-12 — and its
error concentrates on exactly the sparse tables this construction exists for,
because the root is then small relative to coefficients of order `N`. It is
therefore a **seed**, polished by Newton on `dℓ/dp̃₁` (well-conditioned: ratios,
not differences of large numbers), measured at ~1e-16. A first attempt to use a
bisection on the score equation as the *reference* was wrong in a more
instructive way: it silently converged to the wrong endpoint whenever the
likelihood's maximum sits ON a feasible boundary (any empty cell), and a
golden-section search over the likelihood — derivative-free — cannot beat
`√ε ≈ 1e-8` and *looked* like evidence against the closed form. **The reference
that works is the objective itself**: the returned root must beat its neighbours
and both feasible endpoints on the constrained log-likelihood. Four mutation
probes (no polish; a transposed cubic coefficient; a swapped ratio-quadratic
term; a dropped `θ²` in the ratio variance) all go red, and the transposed
coefficient is caught ONLY by the objective test — the coherence and null tests
pass it, because its error vanishes at `δ = 0`.

**The two open questions the derivation left are now tested premises**, not
assumptions: `Z` is monotone in the contrast (checked at 60 contrasts × 2000
tables per scale, including empty and full cells), and a root-find that finds no
crossing lands on the FEASIBLE BOUNDARY — which is the only answer a bounded
confidence set can give, not an error branch.

**Endpoints come from a fixed-iteration bisection**, never a tolerance loop: fixed
work is what makes the scalar entry (a length-1 batch through the same kernel)
bit-identical to the vectorized one, so the M7 parity gate keeps its equality
assertion. `test_ztest_parity` is now parametrized on `interval` as well.

### STAT-4 — the relative effect: what the mean methods should compute ✅ SHIPPED

**Depends on:** STAT-2 (without the sign instrument this cannot be arbitrated).

**Scope correction made at build time, and it is the WP's first as-built delta.**
The heading said "what the **z-test** should compute", because the contour item
was "restore the relative-z covariance term". STAT-3 answered that question for
the z-test — its relative interval is the ratio-scale score construction, the
exact analogue of Fieller for proportions rather than a normal-theory
approximation of it — so what remained was the **mean-based** family, whose
relative branch is the `delta` variant of the table below. STAT-4 therefore ships
on `t-test`, `cuped-t-test`, `paired-t-test`, `paired-cuped-t-test` and
`ratio-delta`, and `z-test` is deliberately not an adopter (pinned by a
registry-derived roster test).

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
sub-task of choosing Fieller, not a follow-up.** *(As built: it needed no code
at all. STAT-3a's guard already refuses `_alpha_inverted_bounds` for any method
whose bound instance declares `asymmetric_ci`, and STAT-3 already decided what
the tier shows instead — a reported gap, never an approximation. The required
sub-task was paid a WP early, by the twelfth entry point STAT-3a's count found.)*

#### As built

`interval: delta | fieller` on the five mean methods — identity-flagged,
defaulted to the legacy branch. **No default moved**; `ALGORITHM_VERSION`
untouched. Deviation record:
[statistics-changes.md §4.5](statistics-changes.md). The math is one pure module
(`abkit/stats/relative_interval.py`), which also owns the ONE dispatch point that
replaced the `relative_delta_effect` + `normal_test` pair each method used to
compose for itself; the gates are `tests/stats/test_fieller_interval.py` (the
derivation's KATs) and `tests/stats/test_relative_interval_param.py` (the
contract, swept over a registry-derived roster).

**The load-bearing deltas, none of which the design had:**

- **The defect is ONE-SIDED, not a coverage loss**, and stating it correctly is
  what makes the WP worth shipping. The design (and §0.4) predicted the A/A matrix
  would be blind because the rejection sets coincide *at the null*; measured, the
  two-sided rates agree to the third decimal (0.0498 vs 0.0499) — but delta's
  TAILS are 0.0168/0.0327 at a control-mean CV of 5% and 0.0083/0.0393 at 10%,
  **independent of the true effect**. Every abkit verdict is a one-sided claim, so
  the real directional error rate runs at up to 1.6× the configured one, and an
  A/A run at the null measures that faithfully and still reports "calibrated".
  STAT-2's `fpr_negative_share` reads 0.664 against the derivation's predicted
  0.659 — the instrument doing exactly what it was built a WP early for.
- **The p-value moves, deliberately.** Under `fieller` the relative p-value is the
  ABSOLUTE comparison's, bit-for-bit (asserted with `==`, not a tolerance, across
  all five methods). D10 said "changes no verdict" of replacing the *shortcut*;
  the mean methods carry *delta*, whose rejection set genuinely differs, and
  keeping its Wald p beside an inverted-test interval would have rebuilt the
  incoherence the WP exists to remove.
- **The unbounded branch is reported as MISSING BOUNDS.** Not a wide interval, not
  an error: `readout._informative` already treats NULL bounds as a gap. The
  disclosed cost is that a comparison can reject on the absolute scale and not be
  called a WIN — on evidence (`g ≥ 1`) that could not support a lift figure
  anyway. An EMPTY set is a separate sentence, reachable only through a non-PSD
  moment triple; five causes of missing bounds now carry five messages.
- **`ParamSpec.asymmetric_values` replaced STAT-3's per-class resolution.** With a
  second param-switched interval across five classes, resolving the capability in
  each `__init__` is five copies of a knob-dependent fact — the STAT-1b lesson.
  `BaseMethod` folds every spec's declaration into the bound instance, and the
  entire STAT-3a consequence set followed with **no new surface code** (proved by
  a real-config leg added to `tests/validate/test_asymmetric_ci_refusal.py`, whose
  earlier probes could only `setattr` the flag because no value method could
  declare one). `ParamSpec.relative_only` is its sibling: the inert
  `fieller` + `absolute` pair is refused at construction rather than forking
  `method_config_id` for nothing.
- **`abk plan`'s sizing turned out to be CLOSER under the new estimator.**
  `get_ttest_mde`'s relative branch sizes the absolute difference and divides by
  the control mean — the null-variance rule, i.e. Fieller's own rejection
  boundary. So the planner has disagreed with the shipped DEFAULT all along;
  STAT-3's note ("the two rules differ by O(z²/N)") would have been a false caveat
  here, and now claims a difference in half-widths instead.
- **Two numerical choices were measured, and one of them lost.** The
  cancellation-free discriminant is 30× better and gets a Decimal-referenced gate
  at `z_stat = 10⁴` (where `B² − AC` reads 6.5e-10, past rel-1e-9). The textbook
  `s = B + sign(B)√disc` root pairing buys **nothing** (2.3e-15 vs 3.3e-15
  relative to the width, worse in three of four probed regimes) and was deleted
  rather than kept as an unfalsifiable comment. Mutation probes: 8 hostile edits,
  6 caught by the KATs; the two survivors became the two tests above.

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
STAT-1c (guardrails) ✅ ───────────────────────────────────┐   safety; independent
STAT-1b (contrast set) ✅ ─────────────────────────────────┤   biggest power win
STAT-1  (Holm / the Fork) ✅ ─────────────────────────────┤
STAT-2  (sign instrument) ✅ ─▶ STAT-4 (relative effect) ✅ ┼──▶ STAT-6 (exit gate)
STAT-3a (asymmetric_ci guard) ✅ ─▶ STAT-3 (proportions) ✅ ┤
STAT-5  (ddof — recommended dropped) ─────────────────────┘
```

The correction layer split into three independent WPs once the derivation
landed, and their order is by **value, not dependency** — none blocks another:

1. **STAT-1c** first (✅ shipped). It is the only safety-directed item, and its
   declaration, storage and UI already exist (audit §8b).
2. **STAT-1b** next (✅ shipped). Declaring the contrast set buys `g/2` in
   level — more power than Holm gives, for a config field rather than new math.
3. **STAT-1** last, because it is the one that needs the Fork settled, and the
   Fork is the maintainer's call (✅ shipped — D7 settled it, Fork B).

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
| D15 | **STAT-1b stays in M13 and does NOT wait for M14's `control:` field.** They are different declarations: STAT-1b declares the FAMILY (`contrasts: vs_control \| all_pairs`), M14 declares WHICH ARM is control — and the latter is already resolved positionally today (first declared variant = control = `name_1`, baseline §5 `combinations`). So the knob is expressible over the existing convention, and M14 later replaces the positional resolution in one place instead of the family being blocked on it. | decided 2026-08-04, delegated |
| D14 | **The sequential layer DOES extend to score intervals** — the always-valid rule is a standardised test with `c(V)` in place of `z`, so the confidence sequence is `{δ : \|Z(δ)\| ≤ c(V)}`, a critical-value substitution inside the root-find MN/Fieller already run. The blocker is architectural, not mathematical: the SE is *inferred* from the CI width assuming symmetry, unvalidated. **`asymmetric_ci: ClassVar[bool] = False` is a hard prerequisite of STAT-3** — it turns a silent miscomputation into a loud refusal. *Amended 2026-08-04 (see D17): the count "six call sites" was measured on `to_always_valid`; the assumption actually lives in `se_from_ci_length`, entered from **eleven** places, seven of them inside the A/A instrument.* *Amended again 2026-08-04 by the STAT-3a build: **twelve**, not eleven (explore's α tier open-codes the inversion), and the flag is NOT a `ClassVar` — a class-level flag cannot fire for the param-switched interval STAT-3 itself ships.* | decided 2026-08-03, SHIPPED as STAT-3a |
| D17 | **The `asymmetric_ci` guard ships as its own WP (STAT-3a) BEFORE STAT-3**, not inside it. Three grounds, all measured rather than argued: (a) the assumption is not `to_always_valid`'s, it is `se_from_ci_length`'s, and that function is called directly from **nine** sites outside the sequential package (`validate/scoring.py` ×5, `validate/family.py` ×2, `tuning/recompute.py`, `pipeline/driver.py`) plus `to_always_valid`'s own two callers — a flag checked only inside `to_always_valid` would leave the A/A instrument, which is the majority of them, unguarded; (b) no shipped method has an asymmetric CI, so the change is **provably behaviour-neutral** and its gate is byte-parity over the existing suite — a property that stops being cheap the moment a new interval lands in the same PR; (c) its exit criterion needs no new math (a hostile fake method declaring `asymmetric_ci = True` must make every entry point refuse LOUDLY), and a review that cannot separate "the guard works" from "the interval is right" is exactly what this milestone keeps punishing. *SHIPPED 2026-08-04 — and ground (a) understated itself: the twelfth site does not call the helper at all.* | decided 2026-08-04, SHIPPED |
| D16 | **`contrasts` is experiment-level with NO project default** — unlike `correction`/`guardrail_correction`. The family five call sites read must never depend on whether the surface reading it resolved a project default, and the factory that serves them therefore takes no `ProjectConfig`. It is also a statement about an experiment's design (which arms it compares), not a project-wide statistical policy. | decided 2026-08-04, delegated |
| D13 | **STAT-5 (uniform ddof) is DROPPED from M13** — second-order, below the instrument's noise floor, dominated at small n by the normal-vs-Student-t error deferred to M15 (audit §7). Dropping it edits the ROADMAP contour. | decided 2026-08-03, delegated |

## 4. Exit gate (sketch)

1. **The byte-compatibility gate is the milestone's №1 assertion**: the scaffolded
   project, unchanged, produces `_ab_results` identical to `0.7.0` — discrete
   columns exactly, continuous at rel-1e-9, JSON payload columns parsed before
   comparison (the M9 lesson: a θ differs in its last ULP and comparing serialized
   strings demands a property IEEE-754 does not offer).
2. Every new estimator/scheme has a **new** golden; every legacy golden still
   passes at rel-1e-9.
3. Opting into a new **method param** (STAT-3/STAT-4) changes `method_config_id`
   — pinned, since it is what makes D4 safe. It does NOT hold for the correction
   layer: `correction` is deliberately outside `method_config_id` (§6.3), so
   `holm` re-decides an existing series in place rather than orphaning it.
4. `abk validate --family-sweep` over `correction: holm` shows the family FWER at
   ≈α against the members' own level (STAT-1's claim; the two-tier default sits at
   its own nominal ≈2α by design, which is what §4.3(a) now states rather than
   calls a defect).
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
that information. Build it once — and note that D15's "M14 replaces the
positional resolution in one place" is off by two: `ExperimentConfig.contrast_pairs()`
is one, and `pipeline/readout.py` resolves `variants[0]` / `variants[1:]`
independently for the verdict list and for the SRM rollup. A `control:` field
that reaches only the factory would leave `evaluate()` looking up series under
the wrong control and reporting "no computed results for this pair" on a fully
computed experiment. Pinned by
`tests/config/test_contrast_pairs_is_the_only_entry.py`, which fails if that
shape moves or multiplies.

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
