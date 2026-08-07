# M14 Implementation Plan — the multi-arm decision layer → `0.9.0`

> **STATUS: DRAFT (2026-08-05), nothing blocking open.** Written in the M14
> design session. Inputs: the maintainer's four sign-offs (§3 D1–D4) and a code
> audit run at the top of the session, whose every claim is anchored to a line
> in this repository at `bdd1321` (the `0.8.0` cut). Where this document states
> what the code does today, that statement was read, not remembered.
>
> **No blind re-derivation fleet was run, deliberately.** The M13 session needed
> one because it derived estimators, and a context that has read the
> implementation cannot re-derive it independently. M14 adds **no estimator, no
> interval and no correction** — it composes verdicts the readout already
> issues. The one item in the contour that *would* have needed a re-derivation
> is a simultaneous best-arm procedure (MCB), and D1 put that in M15 rather than
> here. If a future session promotes it, it opens with a blind derivation under
> [statistics-changes.md](statistics-changes.md) §0 step 3 like STAT-3/STAT-4 did.

## 0. Scope, posture & decisions

### 0.1 What the July audit listed, and what is still live

The source inventory is
[docs/research/2026-07-multi-arm-and-stats-core/multi-arm-support.md](../research/2026-07-multi-arm-and-stats-core/multi-arm-support.md)
(2026-07-07). Five of its fifteen items have since shipped under other
milestones; the audit's headline finding still holds — **multi-arm is
statistically and structurally correct end to end, and every remaining gap is
in the decision / presentation layer.**

| Audit gap | Status at `0.8.0` |
|---|---|
| 1. explore Review shows only the first treatment's verdict | **shipped** — M7 WP0 (`.find` → `.filter`, [explore.ts:1568](../../web/src/explore/explore.ts#L1568)) |
| 4. main-tier `metrics_count=1` FWER inflation | **shipped** — M13 STAT-1; the defect was in the CLAIM, no number moved ([statistics-changes.md §4.3](statistics-changes.md)) |
| 8. Bonferroni pays `C(N,2)` for a vs-control design | **shipped** — M13 STAT-1b, `contrasts: vs_control` |
| 11. control is a positional convention with no field | **shipped** — M14 DEC-1, `assignment.control` + the one AST-gated resolver `ExperimentConfig.control` |
| 2. no experiment-level winner / rollup | **shipped** — M14 DEC-2 composes `ExperimentReadout.rollups`, DEC-3 renders it as the report's cross-arm overview |
| 3. treatment-vs-treatment charted but never verdicted | **shipped** — M14 DEC-2 verdicts every declared pair (`role: treatment_pair`), DEC-3 renders it behind a *not a ship decision* chip |
| 5. `abk plan` sizes ONE contrast of many | **live**, warns; since DEC-1 the pair is the declared control vs the first declared treatment, and the warning names both arms (DEC-5 owns the rest) |
| 6. `abk validate` collapses N arms into a two-arm placebo | **live**, undisclosed ([runner.py:118](../../abkit/validate/runner.py#L118)) |
| 7. `abk run --report` prints unlabeled verdict words | **shipped** — M14 DEC-4: at 3+ arms the ROLLUP replaces the list (one keyed entry per main metric, plus a disagreement note); two-arm output is unchanged |
| 13. no pair selector; blocks grow C(N,2)/metric | **shipped** — M14 DEC-3 (`buildPairPicker`, 3+ arms) |
| 10. explore's `activePair` resets on metric switch | **shipped** — M14 DEC-4 (`activePairByMetric`) |
| 14/15. no SRM culprit, flat picker | **live** (cosmetic tier) |

**One surface the audit could not know about, because it did not exist yet:**
the M11 dashboard took `readout.verdicts[0]` as the experiment's headline. At
three arms that is the first main metric crossed with the **first treatment** —
an arbitrary arm presented as the experiment's result, on the project-level
cockpit. It was the highest-value single fix in the milestone and **DEC-4
shipped it**: the headline is now the first declared main metric's rollup
LEADER (`overview._headline_verdict`), falling back to the first declared
treatment when no arm beat the control.

### 0.2 The posture: nothing already computed moves

M13 inverted M7–M12's "no number moves" posture; M14 restores it, in the
stronger form the interpretation layer allows:

**M14 moves no persisted number, no alpha, and no verdict that `0.8.0` already
issues. It adds verdicts for pairs that had none, and a rollup over them.**

Three facts make that a structural claim rather than a hope, and each is an
exit-gate leg (§4):

1. **The read-time family is built from ROWS, not from verdicts.**
   `_build_sig_map` takes the filtered row list
   ([readout.py:561](../../abkit/pipeline/readout.py#L561)); under
   `benjamini_hochberg`/`holm` every informative row at a cutoff is already in
   the family, treatment-vs-treatment rows included, because they are persisted
   whenever `contrasts: all_pairs` declares them. Issuing a verdict for a row
   that is already in the family cannot move a threshold.
2. **The alphas are unchanged.** The divisor is derived from the arm count and
   the declared family, never from an enumeration
   (`stats.correction.n_comparisons(groups_count, metrics_count, contrasts)`),
   and equals `|contrast_pairs()| × metrics_count`. M14 moves none of its three
   inputs: a declared `control:` re-*orients* pairs, it does not add or remove
   any, and it changes neither the arm count nor the `contrasts` family (D3).
3. **A two-arm experiment is byte-identical on every surface** — amended by
   DEC-3 as built to *the rendered DOM is identical and every `0.8.0` payload
   field reproduces*, because the payload legitimately gains keys and the
   report's injected stylesheet grows, so the baked FILE differs on a correct
   implementation. DEC-6's gate must be written against the amended form. With two arms
   there is no treatment-vs-treatment pair and the rollup has exactly one
   candidate, so the payload, the report, the dashboard row, the messages and
   the CLI text must reproduce `0.8.0` field for field — measured against a real
   `v0.8.0` checkout, not against HEAD (the STAT-6 discipline).

### 0.3 What M14 is not

- **Not a simultaneous best-arm procedure.** Hsu's MCB (and every relative of
  it) answers "which arms cannot be excluded from being the best" with a joint
  confidence statement; it is new statistics and would take full change control.
  D1 keeps it out and M15 re-weighs it.
- **Not a new estimator, interval or correction scheme.** Every number the
  rollup reads is already in `_ab_results`.
- **Not a persisted decision.** The rollup is a read-time composition and is
  stored nowhere — the STAT-1 D12 precedent: a stored copy goes stale the moment
  a metric is added or the contrast set is narrowed, and BI disagreeing with the
  product is the failure that rule exists to prevent.

## 1. Work packages

Six, ordered by dependency. DEC-1 → DEC-2 → {DEC-3, DEC-4}; DEC-5 is
independent of all of them; DEC-6 is last.

---

### DEC-1 — `control:`, the declared baseline

**What.** `AssignmentConfig.control: str | None = None`, validated to be one of
`variants`; `ExperimentConfig.control` resolves it to `variants[0]` when unset
(D3). Every site that currently spells `variants[0]` **to mean the control**
reads the property instead.

**Why it is its own WP.** The convention has seven readers today, and they do
not fail the same way:

| Site | What it does with `variants[0]` | How it fails under a declared control |
|---|---|---|
| [`contrast_pairs()`](../../abkit/config/experiment_config.py#L828) | the `vs_control` family | wrong family — silently the wrong alphas |
| [`evaluate`](../../abkit/pipeline/readout.py#L565) | control/treatments split | verdicts against the wrong baseline |
| [`_srm_from_series`](../../abkit/pipeline/readout.py#L471) | series lookup key `(metric, control, treatment)` | **SILENT** — every lookup misses, `srm_flag=False`, `srm_pvalue=None`: a broken assignment reads healthy |
| [`plan.py:240/519/763`](../../abkit/cli/commands/plan.py#L240) | baseline moments, split, pair | sizing against the wrong arm |
| [`validate/runner._share_a`](../../abkit/validate/runner.py#L118) | placebo split share | calibration at the wrong ratio (see DEC-5) |
| [`test_report.py:104`](../../abkit/cli/commands/test_report.py#L104) | synthetic sample names | cosmetic |

The third row is the reason this is not a one-line change. A knob that reaches
none of its call sites is the M10 `interval_anchor` failure; a knob whose
missed call site turns a **safety gate quiet** is worse. The countermeasure is
the one this repo already uses twice — `ExperimentConfig.grid()` (m10) and
`contrast_pairs()` (m13 STAT-1b): make the property THE entry and add an AST
gate (`tests/config/test_control_is_the_only_entry.py`) forbidding
`variants[0]` under `abkit/` outside the resolver, allowlisting only the
resolver itself and sites that legitimately mean "the first declared arm" for a
non-control reason (there may be none — the audit found none).

**Build spec.**

1. `AssignmentConfig.control: str | None`, model-validated ∈ `variants`, with a
   message naming both the value and the declared list. A project-level default
   is deliberately **absent**, for STAT-1b D16's reason: the baseline a surface
   reads must not depend on whether that surface resolved a `ProjectConfig`.
2. `ExperimentConfig.control` property + `treatments` (declaration order,
   control removed). `contrast_pairs()` orients every pair containing the
   control as `(control, other)`; the remaining treatment pairs keep
   `itertools.combinations` order. Under the default this is a **no-op by
   construction** — `combinations` already emits `variants[0]` first in every
   pair that contains it — which is what keeps `0.8.0` byte-identical.
3. **Declaring a non-first control orphans rows, and re-bases the effect.** The
   pairs containing the new control change `(name_1, name_2)` order, so their
   persisted rows leave the declared set (the STAT-1b stale-pair path: the
   driver warns, `readout._filter_rows` drops them with a loud line) and the
   re-oriented pair's effect is measured against the other arm. On the absolute
   scale that is the negation of the old number; **on the relative scale it is
   not** — the denominator swaps arms too, so `(m₂−m₁)/m₁` becomes
   `(m₁−m₂)/m₂`, and an operator comparing an old chart against a new one will
   not find the sign flip they were promised. Say the relative case explicitly;
   `test_type: relative` is the common configuration, not the exotic one. All of
   this goes in the config docs and in the validator's message; the repair is
   `abk run --full-refresh --from … --to …` (never a bare `--full-refresh` — it
   is a `BadParameter` without its window bounds, STAT-1b again).
4. **Catalog:** `_ab_experiments.control`, `Nullable(String)`. Three edits, and
   the third is the one STAT-6 caught being forgotten: the table model, the
   `ExperimentConfig.catalog_record` emitter, and `_ExperimentsMixin.
   _EXPERIMENT_FIELDS` — a **whitelist**, so a missing field is dropped in
   silence. Nullable rather than defaulted on purpose: a defaulted `String`
   needs `max_length` or MySQL maps it to `TEXT` and rejects the literal DEFAULT
   (error 1101, STAT-6), and NULL is the honest reading for a row written before
   the field existed ("not declared, positional").
5. **Out of the m9 state identity.** `control` moves which pairs are compared,
   never which units or days are materialised — the `interval_anchor` and
   `contrasts` precedent. `cohort_config` folds in the variant LIST, which a
   control declaration does not reorder.

**Traps.** (a) `abk explore`'s Apply seam re-emits the parsed document — a
`control:` key must survive it (the UI-1 archive discipline covers the editor
path, not Apply). (b) The scaffold should **not** write `control:` — an
optional field whose default is right is noise in a starter config, and the
STAT-1b `contrasts` precedent left the scaffold alone.

#### DEC-1 as built ✅

Shipped as specified — both traps held (Apply round-trips the key, pinned;
the scaffold writes nothing) — with six deltas the plan did not have. Four are
consequences of the build, two came out of the adversarial review.

1. **A third resolver member.** `control_reorients_pairs` answers "did a
   declaration move the baseline off the convention?", which is the level-2
   warning's condition and cannot be written without the very `variants[0]`
   subscript the AST gate forbids. It lives beside the resolver, and the gate's
   scope check allows exactly those two members.
2. **`UNDECLARED_PAIR_CAUSES`, one shared string.** DEC-1 adds a THIRD cause to
   the "rows outside the declared contrast set" warning, and four surfaces
   (readout, report, dashboard overview, explore session) each carried their own
   copy of the two-cause list. A cause list wrong on three surfaces out of four
   sends the operator hunting a rename that never happened.
3. **A `control` key in the report payload.** The report and explore headers
   printed `first = control` beside the arms line — a tautology until DEC-1, and
   a lie after it: with `control: c` on `[a, b, c]` it names `a` as the baseline
   directly above pair blocks reading "c vs a". One shared `baselineNote()` in
   `web/src/shared/payload.ts` keeps the old sentence whenever it is still TRUE
   (an absent key — every pre-`0.9.0` bake — or a control that IS the first arm)
   and prints `control: <name>` otherwise, so §0.2's two-arm byte-identity claim
   survives. This is a DEC-3/DEC-4 surface touched early because DEC-1 is what
   falsified the sentence.
4. **The catalog writes the RESOLVED control**, not the declared field (the plan
   said "NULL = not declared, positional"). The column answers "which arm are
   these effects measured against", which has an answer for every experiment;
   NULL for the positional default would make BI re-derive the convention from
   the `variants` JSON — the re-derivation `contrasts` exists to stop. NULL now
   means one thing only: a row written before `0.9.0`.
5. **(Review) A shape gate is necessary and not sufficient.** The review's
   mutation probe reverted five call sites to `list(variants)[0]` — a subscript
   over a Call, invisible to the first draft of the walk — and 312 tests stayed
   green. The walk now matches any subscripted expression *mentioning*
   `variants`, and `plan` (×2), `validate/runner._share_a` and `test-report`
   each gained a behavioural assertion. The gate's own allowlist had the same
   defect in miniature: it excused `variants[1]` as "a treatment, DEC-1-neutral",
   a premise true only while the control sits at index 0.
6. **(Review) Two repairs outside DEC-1's scope, both reachable through it.**
   The catalog-migration gate derives "0.7.0" as *the current model minus a
   hand-maintained set*, so a column missing from that set is already in the
   supposedly-old table and its migration is never exercised — measured: with
   `control` absent, declaring it NOT-NULL/no-default (STAT-1b's exact shipped
   shape, which would kill every install's first run) left 775 tests and the m13
   exit gate green. And the inherited `--full-refresh --from … --to <horizon>`
   advice leaves the horizon look un-rewritten (`--to` is EXCLUSIVE on `end_ts`,
   and since m10 the horizon cutoff's `end_ts` IS `horizon_ts`; the bounds are
   parsed naive against naive-UTC `end_ts` while the YAML window is local, so it
   half-works per timezone). For the DEC-1 cause the flag is not needed at all —
   no look carries the re-oriented pair, so a plain `abk run` re-plans the whole
   series.

**Hand-offs the review surfaced, recorded rather than fixed here:**

- **DEC-5.** `validate/run_id.cell_hash` excludes `share_a`, so declaring a
  control with an uneven split re-runs the placebo at a different ratio while
  still matching a previously stored green calibration row. Pre-existing for
  `expected_split`; DEC-1 adds a second field with the property.
- **DEC-3.** `builder.py` orders pair blocks by `contrast_pairs()`, so with a
  late-declared control under `all_pairs` the FIRST block is a
  treatment-vs-treatment pair, for which `report.ts`'s `verdictFor` returns
  null. Pre-DEC-1 the first block was always control-vs-treatment. Presentation
  only — and DEC-2 gives those pairs verdicts anyway.

---

### DEC-2 — the decision layer: treatment pairs get verdicts, metrics get a rollup

**What.** `evaluate()` issues a `PairVerdict` for **every declared pair** of a
main metric, and composes a `MetricRollup` per main metric.

**Verdicts for treatment pairs.** `_pair_verdict` is reused verbatim — the same
stabilization scan, demotion gate, pre-horizon refusal, family-divergence
caveat and guardrail attachment. Two additions:

- `PairVerdict.role: Literal["vs_control", "treatment_pair"]`. A `WIN` on the
  pair `(B, C)` means *"C beat B in the desired direction"*, **not** "ship C",
  and every renderer that prints the word needs the distinction as a FIELD.
  Deriving it at the surface by testing `name_1 == control` is the STAT-1
  `family_divergence` lesson inverted: a fact three renderers need is API, not
  something each re-infers.
- Treatment pairs exist only under `contrasts: all_pairs`. Under `vs_control`
  the rows were never computed, so there is nothing to verdict and nothing
  changes — the knob stays load-bearing (see the rollup's `untested` state).

**`MetricRollup`** — one per main metric (D2), on `ExperimentReadout.rollups`:

| Field | Rule |
|---|---|
| `metric` | the main metric |
| `leader` | the arm with the best effect **in the comparison's `desired_direction`** among treatments whose vs-control verdict is `WIN`; `None` when none won |
| `indistinguishable` | the treatments the leader is **not** decisively better than, read off the existing treatment-pair verdicts |
| `separation` | `separated` (that set is empty) · `co_leaders` (it is not) · `untested` (`vs_control`, or the rows are missing/demoted) |
| `losers` | treatments whose vs-control verdict is `LOSE` |
| `guardrail_regressed` | arms with a regressed guardrail against the control |
| `rationale` / `caveats` | the readout's own voice, scheme- and knob-aware |

Four rules, each with the alternative it rejects:

1. **The leader is chosen only among `WIN` arms** (D6). "The best point estimate
   among arms that did not beat control" is exactly the uncontrolled claim D1
   refused; with no winner the rollup says so and names nothing.
2. **Separation is tested against EVERY other treatment, not the runner-up**
   (D5). Under `all_pairs` every treatment pair is already inside the family the
   alpha paid for, so "L is better than each of the others" is a controlled
   statement. "L beat the runner-up" is a comparison *selected by the data* and
   is not — and the runner-up formulation additionally leaves K−2 comparisons
   unexamined while sounding conclusive.
3. **`desired_direction` decides "best", and the pair's orientation decides the
   sign.** `effect` on a row is `name_2` against `name_1`. For a treatment pair
   the leader may be either element, so the predicate is: the leader is
   decisively better than `other` iff that pair's verdict is significant AND its
   effect's sign, re-oriented so the leader is second, matches the desired
   direction. Write it once, in the readout; a second transcription is how the
   report and the dashboard end up disagreeing about who won.
4. **A rollup exists for two-arm experiments too**, with the single treatment as
   the only candidate — a uniform payload shape. The surfaces render the
   cross-arm affordances only at 3+ arms (§0.2 point 3).

**Multiple main metrics** (D2): one rollup each, plus
`ExperimentReadout.leaders_agree: bool | None` — do the per-metric leaders
coincide (`None` when fewer than two rollups name one). It **reports**; it never
picks. `is_main_metric` is a boolean
([experiment_config.py:360](../../abkit/config/experiment_config.py#L360)), there
is no declared priority, and inventing one is D2's rejected option.

**What must not change** — the exit gate's second leg: every control-anchored
`PairVerdict` `0.8.0` already issues, field for field, including `rationale`
and `caveats` strings. Adding candidates to a rollup must not reword a verdict.

#### DEC-2 as built ✅

Shipped as specified, and the "what must not change" leg was verified early
rather than at DEC-6: `evaluate()` was diffed against a live `main` module
across eight configurations (2/3/4 arms; positional and declared non-first
control; `correction` ∈ none/bonferroni/BH/Holm; both `contrasts` values; with
and without a guardrail) and every control-anchored verdict matched field for
field. Seven deltas.

1. **`separation` has a FOURTH state, `no_leader`.** The design's table has
   three, and with no winning arm `indistinguishable` is empty — which would
   read as `separated`, i.e. "the leader beat everyone" said of an experiment
   with no leader. `untested` would be the opposite lie ("we could not look").
   Most experiments do not win, so this is the common state.
2. **Each other arm is classified into THREE states, not two** — beaten,
   undecided, and **untestable**. The spec's `untested` covers "`vs_control`,
   **or the rows are missing/demoted**"; the first draft implemented only the
   first half, so a missing or demoted treatment-pair series was reported as
   `co_leaders` — a positive claim of *measured* non-separation where nothing
   was measured. Reachable with no config edit (the treatment pair holds the
   two smallest arms and demotes first). `untested` outranks `co_leaders` when
   both apply.
3. **`PairVerdict.judged`**, the field delta 2 needs: did the readout reach a
   decision, or did a gate short-circuit it? It is the SAME flag the Fork B
   caveat is gated on (`family_consulted`), exposed — no new plumbing. The
   alternative was reading `rationale` strings, which is how prose becomes API.
4. **`guardrail_policy: block` no longer caps a treatment pair.** The cap fires
   on WIN and never on LOSE, while "B is ahead of C" is a WIN stored one way
   and a LOSE stored the other — so the rollup's separation claim depended on
   the ARBITRARY declaration order of the arms. Measured: identical data and an
   identical guardrail regression gave `separated` under one `variants` order
   and `co_leaders` under another. The cap is now scoped to ship decisions,
   which is what `guardrail_policy` was always about.
5. **`_leader_beats` reads the verdict WORD, not `significant` + a re-derived
   sign** (the design's phrasing). The word is that conjunction PLUS the SRM
   gate, the pre-horizon refusal, the demotion gate and the stabilization scan;
   re-deriving would be a second, looser decision rule for a pair whose verdict
   is sitting right there. Delta 4 is what makes the substitution safe — with
   the cap on, the word was orientation-asymmetric and the substitution was
   not merely conservative.
6. **The rollup never speaks over a gate**, and `losers` is disjoint from
   `indistinguishable`. Under a failed SRM gate the rollup named the gate
   instead of reporting "no arm beat control" (the DEC-1 `_srm_from_series`
   failure mode one level up); and an arm could otherwise appear in both
   `losers` and `indistinguishable` of one payload.
7. **Three surfaces were held control-anchored in this WP**, not in DEC-3/DEC-4.
   `notify/dispatch.py`, `reporting/builder.py` and `tuning/overview.py` all
   iterate `readout.verdicts` unconditionally, so merging DEC-2 alone would
   have tripled a three-arm experiment's notification volume and rendered
   unlabelled `B vs C` ship recommendations on the report and in explore's
   Review mode. Their own pre-existing tests caught it (10 failures in exactly
   those three places), and the holds are now pinned so DEC-3/DEC-4 open each
   surface deliberately.

**Review note for DEC-3/DEC-4.** The orientation half of `_leader_beats` was
initially untested: every decisive treatment pair in the first fixture set
favoured the leader, so an orientation-BLIND implementation passed all 21
tests. Any new rule about pair direction needs a fixture where the pair is
decisive AGAINST the leader.

---

### DEC-3 — the report: a card per declared pair, an overview per metric ✅

**Payload.** `_verdict_to_payload` gains `role`; the payload gains `rollups`
and `leaders_agree`. Terse-key discipline holds (`web/src/shared/payload.ts` in
lockstep — the M3 rule).

**Renderer.** Three changes, all in `web/src/report/**`:

1. `verdictFor` ([report.ts:618](../../web/src/report/report.ts#L618)) now finds
   a verdict for treatment pairs, so the card that today is silently absent
   appears. It renders a **role chip** and role-aware prose; a `WIN` card on a
   `B vs C` block that reads like a ship recommendation is worse than today's
   blank.
2. A **cross-arm overview** per main metric, above the pair blocks and rendered
   only at 3+ arms: the leader, the separation state in words, and a compact arm
   table (effect · CI · verdict · n) so the reader sees the ordering without
   scrolling C(N,2) charts.
3. A **pair selector / collapse** so block count stops growing as C(N,2) per
   metric (audit gap 13). Default expansion: the control-anchored pairs.

**Bundle discipline** (`abk-web-bundle`): edit `web/src/**` → `cd web && npm run
build` → commit the regenerated `abkit/reporting/assets/report.js` in the SAME
PR. **No new hex**: the leader/co-leader chips reuse the existing verdict tokens
(`docs/design/brand-tokens.md`) — M12 NTF-2's rule, where a notice reused the
SRM token rather than minting a sixth colour.

#### DEC-3 as built ✅

Shipped as specified. Five deltas, four of them about the fact the plan states
in one line and the build had to spend a WP on: **opening the payload opens
every reader of it, not the renderer the WP is named after.**

1. **The payload has THREE consumers, and two of them are not ready.** DEC-2's
   lesson (`readout.verdicts` is iterated unconditionally by three surfaces) is
   here one level down: `payload["verdicts"]` is read by `report.ts`, by
   `abkit/tuning/payload.py` — which rides the report payload VERBATIM into the
   cockpit, where Review mode renders every matching verdict and prints the word
   alone — and by `cli/commands/run.py`, which joins the bare words onto the
   `Report →` line. Merging the payload change alone would have shipped
   "`WIN · FLAT · LOSE`" with the third word about one treatment against
   another, and an unlabeled `B vs C` line in explore: the exact misreading
   `role` exists to stop, on two surfaces at once. Both now hold at
   control-anchored until DEC-4 opens them deliberately.
2. **The hold is ONE function, `builder.ship_decisions`**, not two inline
   filters. Four copies of a knob-dependent set is the STAT-1b failure
   (`combinations(variants, 2)` in four modules); two copies of a
   role-dependent one is the same shape, and DEC-4 deletes one call site each
   rather than hunting for predicates. An absent `role` reads as a ship
   decision — every payload baked before `0.9.0` is control-anchored by
   construction. `_verdict_note` was extracted from `_emit_experiment_report`
   so the CLI half of the hold has something to test that does not write a file.
3. **Every cross-arm affordance is gated at 3+ arms, and `leaders_agree` is
   why the gate is not decorative.** With two main metrics and two arms both
   rollups name the same single treatment, so `leaders_agree` is `true` and the
   chip would appear on a page `0.8.0` rendered without one — §0.2 point 3
   broken by an agreement that is a tautology. The overview, the selector, the
   evidence group and the chip are all suppressed at two arms; the role chip is
   suppressed for `vs_control` at any arm count.
4. **The arm table does not rank.** `desired_direction` is deliberately absent
   from the payload, so a renderer sorting by effect would have to guess which
   end of the scale is good and would put the leader last on a `decrease`
   metric. The table stays in declaration order and the LEADER is named by the
   rollup's own chip — the DEC-2 rule that the direction is resolved once, in
   the readout, applied to the surface that displays it. `n` per arm is the
   latest cutoff of that arm's control-anchored pair, i.e. the same row the
   verdict beside it came from.
5. **The evidence group needed its own class.** Nesting a second `abk-verdicts`
   inside the collapsed group made `.abk-verdicts > .abk-verdict` match the
   arm-vs-arm cards too — the one selector that must never be ambiguous is "the
   ship decisions", and its first form silently matched three cards where two
   were meant. Caught by the smoke test, not by review.

**What the review changed** (four lenses; every finding below was reproduced
before it was fixed):

6. **The renderer had re-transcribed a decision the readout words itself.** A
   static `SEPARATION_LABEL` mapped `no_leader` to "no arm beat the control" —
   but that state has THREE readouts, and the readout words each differently
   precisely because a rollup must not speak over a gate (DEC-2 delta 6). Under
   a failed SRM gate the chip stated a measured finding one line above "effects
   untrustworthy under a broken cohort", and *every* pre-horizon 3-arm
   experiment — the default state of the surface — read as "no arm beat the
   control". The chip now NAMES THE STATE and is suppressed entirely when there
   is no leader; the claim is the rationale's, three lines below.
7. **The arm table called untestable arms co-leaders.** `indistinguishable`
   merges "compared and undecided" with "could not be compared" (the split
   DEC-2 added its `untestable` state for), so tagging its members `co-leader`
   asserted measured parity about a pair nobody measured — reachable with no
   config edit, since the treatment pair holds the two smallest arms and demotes
   first. The tag is `not separated`, true of both. *Named limitation:* the
   payload cannot express the split, so a surface that needs it will have to
   divide `MetricRollup.indistinguishable` in a later WP.
8. **The pair selector hid exactly the blocks with data in DEC-1's documented
   window.** A declared non-first control re-orients the pairs, so until the
   next run the control-anchored blocks are present-but-EMPTY while the old
   treatment pairs hold the whole series — and an orientation-only default
   opened three "no cutoffs yet" boxes and hid three real charts, a page worse
   than `0.8.0`'s. The default now falls back to whatever has a series.
9. **The re-fit-on-reveal was unfalsifiable.** jsdom has no 2D context, so every
   chart in the suite takes the fallback path and the `charts` list is empty:
   deleting the loop left the suite green while every revealed chart would have
   stayed blank. The suite now has one harness that stubs the context and a
   layout where a canvas under a `hidden` ancestor measures zero.
10. **The CLI hold was pinned at the helper, not at the surface.** Reverting
    `_emit_experiment_report` to the pre-DEC-3 inline join — the exact leak —
    left all fifteen tests green, because they called `_verdict_note` directly.
    The DEC-1 lesson restated: a seam owes a behavioural assertion at the
    surface, not only at the seam.
11. **A printed report lost the collapsed charts.** `hidden` blocks do not print
    and a closed `<details>` prints as its summary, so a 3-arm PDF would have
    dropped the arm-vs-arm charts and cards `0.8.0` put on paper. An
    `@media print` rule reveals both; the interactive default is about
    scrolling, not about what the document contains.

**A wording correction to §0.2 point 3, for DEC-6 to write its gate against.**
"Byte-identical" is false as literally worded for the report and always was
going to be: the payload gains keys and the injected stylesheet grows, so the
baked HTML differs. What holds — and what was measured here, rendering the same
two-arm payload through the `main` bundle and this one — is that **the rendered
DOM is character-for-character identical and every `0.8.0` payload field
reproduces**. DEC-6's leg must be written that way or it will fail on a correct
implementation.

Test posture: the payload tests use a fixture where the LAST-declared arm is
the leader (declaration order and effect ranking disagree), because DEC-2's
review found its own first fixture set blind for exactly the opposite reason;
both holds were mutation-probed, and the explore fixture is shaped to fail
against three WRONG filters (keep-the-first, metric-scoped, and the positional
`pair.c == variants[0]`, which inverts under a declared late control), not
merely to pass against the right one; and the two-arm no-affordance assertion
is the §0.2 leg, proven to bite by removing the 3+ arm gate.

---

### DEC-4 — the other surfaces read the same rollup ✅

The M11/M12 invariant, extended: *one decision, many surfaces, and none of them
recomputes it.*

**Dashboard** ([overview.py:514](../../abkit/tuning/overview.py#L514)).
`readout.verdicts[0]` stops being the headline. The row's headline is the
**first declared main metric's rollup** — deterministic, config order, the same
convention today's `verdicts[0]` follows, now arm-aware and naming the leader.
Every rollup rides in the row; when `leaders_agree` is false the row raises a
chip. Choosing a "worst-of" priority across metrics instead would be inventing
the metric ordering D2 refused.
*Detail worth deciding here rather than in review:* the row's
`guardrail_regressed` flag is ORed across all verdicts today; it stays
**control-anchored**, because a guardrail regression between two treatments does
not say the experiment harms users relative to control. The treatment pair's
guardrail status still shows on its own card. (At two arms both readings
coincide, so this cannot move `0.8.0`.)

**Explore.** Review mode already renders every matching verdict (M7 WP0), so
treatment-pair lines appear **for free** the moment DEC-2 lands — which is
precisely why `role` must exist before this reaches a surface, or the operator
reads `WIN (B vs C)` as "B vs C won". Add the per-metric rollup line to Review.
State in the code where it is read that explore's verdicts are **baked**: the
report payload rides into the explore payload verbatim
([tuning/payload.py](../../abkit/tuning/payload.py)), so they do not follow a
knob turn — a live-looking rollup that is actually as-of page build is a trap
worth one comment.

**Notifications.** Per-pair messages stay **control-anchored**: a treatment pair
is evidence, not a ship decision, and a three-arm experiment must not triple its
message volume. The rollup rides ON the control-anchored payload as fields
(`leader`, `separation`); **no seventh signal kind** (D7). One consequence must
be handled explicitly: the M12 dedup signature is `(verdict, srm_flag)`
([notify/cooldown.py](../../abkit/notify/cooldown.py)), and a leader can flip
from B to C while every verdict word stays `WIN` — the decision changes and
nobody is told. The signature therefore gains the rollup identity. This is
NTF-3's own trap ("deduping on the verdict word alone") in its multi-arm form.

**CLI.** `abk run --report` prints labeled verdicts instead of
`" · ".join(words)` ([run.py:153](../../abkit/cli/commands/run.py#L153)), plus
the leader line at 3+ arms. `abk explore`'s `activePair` remembers its selection
per metric (audit gap 10).

---

#### DEC-4 as built ✅

Merged as PR #112. Four surfaces, not the three the heading counts — the CLI
line is the fourth, and `abk explore` and that line were DEC-3's two holds,
released here.

1. **The rollup names an ARM; a row still needs a PAIR.**
   `overview._headline_verdict` promotes the leader's own vs-control verdict and
   falls back to the first declared treatment when there is no leader (nobody
   beat the control, a failed gate, nothing judged yet). Every stat cell follows
   it — effect, CI, p, alpha, the last look, the demotion chip and the
   sparkline — because a chart drawn from a different arm than the number above
   it is the contradiction the row exists to avoid. At two arms both branches
   return the same verdict, so the change is a structural no-op there.
2. **The row's multi-arm gate counts DISTINCT TREATMENTS, not treatment-pair
   verdicts.** The first draft tested `role === 'treatment_pair'`, which is
   false for every `contrasts: vs_control` experiment — so a three-arm
   `vs_control` row moved its cells to the leader while rendering nothing that
   said which arm they belonged to. That is the defect DEC-4 exists to remove,
   left in place for one shipped config; the report and the CLI both gate on the
   arm count, and counting ship-decision treatments is the row's equivalent.
3. **The unreachable branch FALLS BACK rather than raising** — the opposite of
   DEC-2's control-pair guard, deliberately: a dashboard row is a read-only
   view, and losing the whole page over a headline choice is the worse failure.
4. **Notifications got D7's treatment: the dedup changed, the volume did not.**
   No seventh signal kind, no per-treatment-pair message; the rollup rides as
   `ReadoutData.leader`/`separation`/`arm_count`/`rollup_rationale`, and
   `_ab_notify_states` gains a nullable `last_rollup`.
5. **The leader is in the dedup key only when the rollup SEPARATED it.**
   `leader` is a raw argmax and the one term the stabilization scan does not
   smooth; under `co_leaders` the rollup is *saying the arms are
   indistinguishable*, so keying on which of them polled higher flips the
   signature on about half the runs of a genuinely tied pair — a message every
   run, the exact failure NTF-3 exists to prevent.
6. **A state row written before `0.9.0` drops the term.** Every `0.9.0` readout
   signs a non-null rollup while every stored row holds NULL, so comparing them
   would make the first upgraded run re-announce **every comparison in the
   project** — most of them with a message textually identical to the last one
   delivered. Measured before the fix: 100% of the dedup population.
7. **The CLI line is the rollup, not the words.** Listing per-pair verdicts with
   an arm suffix does not survive a legal config: `ship_decisions` is
   metric-blind, so at two main metrics the same arm appears twice with
   contradictory verdicts, and at five arms the line runs past 160 characters.
   One keyed entry per main metric, and a disagreement is NAMED rather than left
   for the reader to spot.
8. **`ship_decisions` survived with one caller.** DEC-3 predicted DEC-4 would
   delete both; explore's went, the CLI's stayed — it is no longer a hold but
   the rule for which verdicts belong on a one-line ship summary.

**What the review changed** (four lenses; each finding below was reproduced
before it was fixed):

9. **Two CSS custom properties did not exist.** `--abk-line` and `--abk-ink-1`
   are defined nowhere, and CSS's "invalid at computed-value time" drops the
   whole SHORTHAND — so the `arm vs arm` pill lost its box entirely, i.e. the
   one affordance that stops a treatment-vs-treatment `WIN` reading as a ship
   recommendation did not draw. Second instance in one WP (`--abk-accent` on the
   dashboard, caught during the build). The gates could not see it: they check
   token DEFINITIONS and raw hexes, never a `var()` REFERENCE. There is a gate
   now (`tests/tuning/test_brand_token_references.py`), green on `main`.
10. **A leader was recommended under a failed SRM gate.** The guard sat only on
    the no-leader branch, but `srm_flag` is the experiment-wide gate while a
    verdict is judged on its own pair's latest row — reachable with `abk run
    --metric`, one metric advancing into a broken cutoff while another sits on a
    healthy earlier look. The message read "results withheld" and, one line
    down, which arm to ship.
11. **The channels were re-wording a decision the readout had worded.**
    `no_leader` covers three facts and the renderer synthesized one sentence for
    all of them, so every young three-arm experiment's message said "No arm beat
    the control" where the truth was that nothing had been judged. The payload
    carries `rollup_rationale` now and the channels render it verbatim — the
    DEC-3 fix, applied to the surface that could not do it before.
12. **The roster gate could not fail.** It asserted over the context dict
    dumped beside the rendered body, and `build_context` computes the line for
    every channel — so deleting Telegram's render of it left all 502 tests
    green. It renders through each channel's own outermost builder now.
13. **The explore half shipped with no tests at all**, and all four behaviours
    survived simultaneous deletion. Six tests now cover them, each
    mutation-probed.
14. **The comment promised an on-page disclosure that did not exist.** Review is
    the one panel where a live knob and a baked readout sit inches apart — the
    role checkboxes re-tier alphas and recompute, while the rollup beside them
    is as of page build. Since DEC-4 the baked half carries a RECOMMENDATION, so
    the page says so rather than only the source.
15. **Two surfaces worded one rollup differently**, and explore was the lossy
    one — it dropped the rationale, which is the only place the arm NAMES
    appear. Same words as the report's chip now, with the rationale under it.
16. **The index-memory comment was factually wrong.** It claimed index 0 is a
    different pair on two metrics; `builder.py` computes `contrast_pairs()` once
    per experiment and hands the same ordered list to every metric, so
    remembering the index IS remembering the pair. The clamp stays, documented
    as defending an invariant rather than an observed shape.
17. **A green headline could hide a harmed arm.** `rollup.losers` rode in the
    payload unrendered; the row now carries an `N lost` chip, because the
    headline being the leader must not make an arm the control BEAT invisible
    until the row is expanded.

**Named limitation, recorded not fixed:** in DEC-1's re-orientation window the
row's headline says "no computed results" while the expand list one click below
shows a WIN with a full series. DEC-3 solved the equivalent for the report (the
selector falls back to whatever has data); the row's headline fallback is
`0.8.0`'s and changing it would change what "headline" means. DEC-6 may weigh it.

### DEC-5 — the supporting instruments: `validate`, `plan`, SRM

Independent of DEC-1…DEC-4; can run in parallel or be dropped last if the
milestone needs to shed a session.

**(a) `abk validate` calibrates a design nobody is running.** `_pool`
concatenates **every** arm's units
([validate/load.py:67](../../abkit/validate/load.py#L67)) and `_share_a` takes
the first variant's share of the WHOLE split
([runner.py:118](../../abkit/validate/runner.py#L118)). At three even arms the
placebo is therefore **1/3 vs 2/3 over three arms' units**, while the live
control-vs-treatment comparison is **1/2 vs 1/2 over two arms' units**. The FPR
column is robust to that; **power and achieved-MDE are not** — they are read off
per-arm n, they feed the Recommended row, and the placebo arms carry ≈1.5× the
live pair's units, so the achieved MDE is optimistic — **by 15% at three even
arms**. *(This paragraph first said ≈√1.5 ≈ 22%; the DEC-5 review measured it.
The old placebo did not split its pooled units evenly either — it split 1/3 vs
2/3 — so its effective n was `(G−1)n/G` and the law is `√(2(G−1)/G)`: 1.000 /
1.155 / 1.230 / 1.270 at 2 / 3 / 4 / 5 even arms. √1.5 is right at FOUR arms,
and the sentence named three. Putting a number in a spec means checking that
number's own derivation.)*
*Fix:* size and split the placebo like the **declared contrast being
calibrated** — deterministically the control vs the first treatment, since the
D3/D4 calibration chip is keyed `(metric, method_config_id, effective alpha)`
and is arm-pair-independent by design (m4 D4). The choice is **disclosed in the
verdict**, not only in `decision_log` — the M7 WP6 lesson, where a warning that
never reached the terminal was found by review, not by use.
*The WP's №1 assertion:* **two-arm experiments are byte-identical.** With two
arms the pool already is both arms and `share_a` already is the control's share,
so the m4/m5 matrix e2e gates (both two-arm —
[test_validate_matrix.py:65](../../tests/e2e/test_validate_matrix.py#L65)) must
not move a digit. Multi-arm A/A rows legitimately do move; that is a new
instrument reading, recorded in the CHANGELOG. No `ALGORITHM_VERSION` is
involved — the A/A matrix is the instrument, not the captured baseline.

**(b) `abk plan` sizes every declared contrast.** `contrast_pairs()` has existed
since STAT-1b; the warning at [plan.py:913](../../abkit/cli/commands/plan.py#L913)
is replaced by real numbers per pair, with the per-arm baseline where the split
is uneven (the current warning understates exactly this). The `_correction_note`
and `pairs_phrase` surfaces already name the family — keep them the single
source of the divisor.

**(c) SRM names the culprit arm.** The joint K-way gate keeps deciding; the
report / dashboard / CLI additionally name the arm with the largest standardised
contribution to the same chi-square. No new gate, no new threshold, nothing that
can change a decision — a decomposition of a statistic already computed. At 3+
arms, "assignment is broken" without "which arm" is a diagnosis the operator
cannot act on.

---

### DEC-6 — exit gate, docs, release

**Gate** (`tests/e2e/test_multi_arm_decisions.py`), four arms, two main metrics
and a guardrail, driven through the CLI over a scaffolded project:

1. **Two-arm byte-compatibility against a real `v0.8.0` checkout** — the STAT-6
   discipline: the baseline script runs unmodified in a `v0.8.0` worktree, and
   the comparison is discrete-exact / continuous-at-rel-1e-9 / JSON-parsed, with
   rows ordered by a DISCRETE key. Never regenerate the golden from HEAD.
2. **Control-anchored verdicts unchanged** at four arms when treatment-pair
   verdicts are added — field for field, `rationale` and `caveats` included.
3. **Rollup correctness on constructed data**: a clean leader; two arms that
   cannot be separated (`co_leaders`); no winner at all; a leader that loses on
   the second main metric (`leaders_agree: false`).
4. **The four surfaces agree**: report payload, dashboard row, notification
   payload and `abk run --report` text name the same leader and the same
   separation state, over identical rows.
5. **`contrasts: vs_control` ⇒ `separation: untested`** everywhere, with the
   knob named as the reason.

**Docs.** Three bodies in one story (`docs/`, `.claude/rules/`, the packaged
`abkit/cli/assets/claude/`), the multi-arm guide rewritten from "known
limitations" to a described feature, and the audit's own file gets a status
banner rather than being left to read as current.

**Release.** `0.9.0` per `abk-release`.

## 2. Dependency graph

```
DEC-1 (control:) ──▶ DEC-2 (verdicts + rollup) ──┬──▶ DEC-3 (report)
                                                 └──▶ DEC-4 (dashboard/explore/notify/CLI)
DEC-5 (validate/plan/SRM) — independent
DEC-6 (exit gate + docs + 0.9.0) — last
```

DEC-2 is the milestone. DEC-1 before it because the rollup's vocabulary is
"control vs the rest" and it must not be positional by the time it is printed;
DEC-3/DEC-4 after it because both render `role`, which DEC-2 defines.

## 3. Decisions

| # | Decision | Source |
|---|---|---|
| **D1** | **Leader + tested separation.** The rollup names the best-effect arm among the winners and states whether it is separated from the others, read off existing treatment-pair rows. A simultaneous procedure (MCB) is NOT in M14 — new statistics, full change control, re-weighed in M15. | maintainer, 2026-08-05 |
| **D2** | **A rollup per main metric.** No cross-metric pick, because no metric priority is declared and abkit does not invent one; the experiment-level statement is whether the per-metric leaders agree. | maintainer, 2026-08-05 |
| **D3** | **`control:` is optional and defaults to the first declared variant.** `0.8.0` configs stay byte-compatible; declaring a non-first control re-orients pairs, orphans their rows and flips their effect sign — documented, warned, healed by `--full-refresh --from/--to`. | maintainer, 2026-08-05 |
| **D4** | **Scope: all four buckets are in M14** — the core decision layer, the cheap UX wins, the `validate` placebo, and `plan`/SRM. Six WPs (~6 sessions) against the contour's ~4; DEC-5 is the shed-able one if the milestone must fit. | maintainer delegated ("реши сам"), 2026-08-05 |
| **D5** | Separation is tested against **every** other treatment, not the runner-up: under `all_pairs` all treatment pairs are inside the family the alpha paid for, so the statement is controlled; "beat the runner-up" is a data-selected comparison that sounds conclusive while leaving K−2 pairs unexamined. | derived |
| **D6** | The leader is chosen **only among `WIN` arms**. Ranking non-significant arms is the uncontrolled claim D1 refused. | derived |
| **D7** | Notifications stay **control-anchored** — no message per treatment pair and no seventh signal kind; the rollup rides as fields, and the M12 dedup signature gains the rollup identity so a leader flip with an unchanged verdict word is still announced. | derived |
| **D8** | The dashboard headline is the **first declared main metric's** rollup plus a disagreement chip — not a "worst-of" priority, which would be the metric ordering D2 refused. The row's `guardrail_regressed` flag stays control-anchored. | derived |
| **D9** | `abk validate` calibrates the **declared control-vs-first-treatment contrast** and discloses the choice in the verdict; **two-arm runs stay byte-identical**. | derived |
| **D10** | The rollup is **never persisted** — read-time only, the STAT-1 D12 precedent (a stored decision goes stale the moment a metric or the contrast set moves, and BI then disagrees with the product). | derived |

## 4. Exit gate (sketch)

See DEC-6. The milestone's №1 assertion — the one that must be written first and
must be able to fail — is **§0.2 point 3**: a two-arm experiment reproduces
`0.8.0` on every surface. The M13 lesson applies verbatim: compare against a
real `v0.8.0` checkout, not against HEAD, or the gate compares HEAD with itself.

## 5. Before start — open questions

Neither blocks DEC-1 or DEC-2.

1. **The cross-arm overview's visual design** (DEC-3 item 2) is a UX task, not a
   derivation — the arm table's columns, where the leader chip sits, and how the
   selector behaves at eight arms. M13 §5.2 carried the same kind of item and it
   was answered in the WP that rendered it.
2. **Should `abk validate` grow a `--contrast` selector** rather than the fixed
   control-vs-first-treatment pick of D9? A selector is more honest for an
   uneven multi-arm split, but the calibration chip is keyed
   arm-pair-independently (m4 D4), so a per-contrast row would need a chip
   change too — which is why D9 takes the deterministic pick and this stays a
   question rather than a decision.

## 6. Inter-milestone collisions

- **M15 (new methods)** re-weighs MCB / best-subset selection (D1). If it ships,
  `MetricRollup.separation` gains a fourth state and the rollup's caveat
  changes; nothing about DEC-2's shape blocks that.
- **M16 (owned randomization)** will declare arms itself. `control:` must be the
  same field there — a generator that names its own baseline differently would
  fork the vocabulary M14 just unified.
- **The standing CI / test-suite audit** (ROADMAP, maintainer request
  2026-08-02) is unaffected by M14 but grows with it: DEC-6 adds a multi-arm
  e2e, and the suite is already the slowest part of contributing.
