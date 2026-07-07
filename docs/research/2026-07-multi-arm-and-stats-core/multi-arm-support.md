# Multi-arm (>2 group) experiments: end-to-end support review

**Verdict up front.** Multi-arm experiments are **statistically and structurally
supported end-to-end** — config, SRM, pairwise enumeration, corrections, and per-pair
persistence all generalize cleanly to N arms with **no hard-coded "exactly 2"
assumption and no crash path**. The gaps are almost entirely in the **decision / UX
layer**: the readout only ever issues *control-vs-each* verdicts, two surfaces are
first-contrast-only, and one of them (`abk explore` Review mode) silently shows only
the first treatment's verdict. Only one item touches numeric correctness (legacy-faithful,
arm-count-independent); the rest is presentation.

---

## What is solid — the statistical / pipeline layer

**Config accepts and validates N arms jointly.** `AssignmentConfig.variants` is an
unbounded list; `validate_variants` enforces only `len>=2`, uniqueness, non-empty, and a
length cap ([experiment_config.py:70](../../../abkit/config/experiment_config.py#L70),
:75-90). `validate_expected_split` requires *every* declared arm to carry a share in
(0,1) and the shares to sum to ≈1.0 within 1e-6 (:111-130) — validated across all N arms,
not two. `ComparisonConfig` is arm-agnostic (metric × method only), so nothing in config
special-cases a pair (:163-199).

**SRM is a single joint K-way gate, not pairwise.** Daily/coarser cadence zero-fills
every declared-but-absent arm and runs one chi-square goodness-of-fit with `df = K−1` over
all sorted variants ([driver.py:253-257](../../../abkit/pipeline/driver.py#L253),
[srm.py:76-109](../../../abkit/stats/srm.py#L76)). Sub-day cadence streams per-arm count
vectors into a Dirichlet-multinomial e-process that is inherently K-way (srm.py:112-221).
Both require `len>=2` with no upper bound; the single `SrmResult` stamps every persisted
row.

**Compute is genuinely all-pairwise C(N,2).** `analyze_cutoff` iterates
`itertools.combinations(variant_order, 2)` and computes an independent `PairOutcome` per
unordered pair, **including treatment-vs-treatment**
([analyze.py:161-162](../../../abkit/pipeline/analyze.py#L161), :201). `recompute_backend`
loads the full cohort for all declared variants once per cutoff and carries no pairing
logic (recompute_backend.py:123-124).

**Corrections count arms correctly.** The Bonferroni denominator is `C(N,2) × metrics`
([correction.py:20-26](../../../abkit/stats/correction.py#L20)); the two-tier scheme
derives `groups = len(variants)` and applies the arm factor correctly (:51-69,
analyze.py:59-78).

**Persistence keeps every pair.** `rows_for_cutoff` emits one contract row per
`PairOutcome` (including demoted/NULLed pairs); the driver saves them all, so each pair
becomes its own stabilization series downstream (enrich.py:67-80, driver.py:381-396,
readout.py:331-339).

**Per-pair verdicts iterate arms generically** — the readout is not wired to two arms:
`treatments = variants[1:]` and the SRM scan both iterate all treatments
([readout.py:428-429](../../../abkit/pipeline/readout.py#L428), :362-372). K-arm iteration
is correct; there is no blocker/crash.

---

## Where it is first-contrast-only or incomplete — the UX layer

**Readout: control-vs-each, no rollup, no inter-treatment verdict.** `evaluate` fixes
`control = variants[0]`, `treatments = variants[1:]`, and double-loops to emit one
`PairVerdict` per (main comparison × treatment) (readout.py:428-446). Consequences:
- **No experiment-level winner.** `ExperimentReadout` carries only a flat `verdicts` tuple
  — no `winner`/`overall` field (readout.py:140-157, :450-456). Arm B=WIN and arm C=LOSE
  coexist unreconciled.
- **Treatment-vs-treatment is never scored.** B-vs-C is computed and persisted but the
  readout only reads `(metric, control, treatment)` series, so a charted pair can exist
  with no WIN/LOSE/FLAT.

**Report (`builder.py` + `report.ts`): charts all pairs, verdicts only control-vs-each.**
Not first-pair-only for *data* — it stacks a labeled chart block for every
`combinations(variants,2)` pair, titled `"{c} vs {t}"` when arms > 2
([builder.py:260](../../../abkit/reporting/builder.py#L260),
[report.ts:614-617](../../../web/src/report/report.ts#L614)). But `verdictFor` returns null
for the non-control pair, so B-vs-C renders a full chart + audit table with **no verdict
card and no on-page explanation of the asymmetry** (report.ts:607-612, builder.py:473).
No overlaid/multi-line arm-comparison view; no collapse/selector, so block count grows as
C(N,2) per metric.

**Explore cockpit: mostly good, but Review mode is silently first-verdict-only.** The
chart path is all-pairwise (`/recompute` returns every pair; the client offers a segmented
pair picker when `block.pairs.length > 1` — recompute.py:598-634, explore.ts:1397-1411).
**However, Review mode uses `payload.verdicts.find(v => v.metric === name)` — first match
only — and renders a single verdict line**, silently hiding every other treatment's verdict
([explore.ts:1516](../../../web/src/explore/explore.ts#L1516), :1521-1523). A role decision
is therefore surfaced on incomplete verdict info. Lesser issues: `activePair` is a global
reset to 0 on metric switch (:583, :1432); Apply writes one method/params per *metric*, so
per-arm-pair tuning is impossible by design (server.py:404-428).

**`abk plan`: genuinely first-pair-only (but warns).** `_moments_from_results` hardcodes
`name_1, name_2 = variants[0], variants[1]` and `_plan_ratio` reads only variants[0]/[1]
([plan.py:424-425](../../../abkit/cli/commands/plan.py#L424), :398-406). It emits an
explicit >2-arm warning (:519-524), though the warning understates that per-arm baselines
can differ.

**`abk validate`: pools N arms into a two-arm placebo.** `_pool` concatenates all variants
into one cohort, then `placebo_mask` re-splits into two arms using `share_a` from the
*first* variant only — so a 3-way even split becomes ~1/3 vs 2/3
([validate/load.py:79-88](../../../abkit/validate/load.py#L79), resample.py:26-42,
runner.py:64-73). By design (the calibration chip is keyed by metric/method/alpha,
arm-pair-independent) but undisclosed.

**`abk run` terminal text: no per-pair output.** Only aggregate exposure/cutoff/result
counts print (run.py:224-232); with `--report`, verdict *kinds* are joined into a bare
string (`"WIN · FLAT"`) with no arm labels, so multi-arm outcomes are indistinguishable in
text (run.py:101-105).

---

## Gaps ranked by severity

**Major**
1. **Explore Review mode shows only the first treatment's verdict per metric** — `.find()`
   returns one match; other treatments' verdicts hidden → role decisions on incomplete
   info. *Near-decision bug even though the stored data is fine.*
   `explore.ts:1516`, :1521-1523 (source readout.py:434-446).
2. **No experiment-level winner / overall verdict for 3+ arms** — WIN and LOSE arms coexist
   with no reconciliation and no field to hold a pick. readout.py:140-157, :450-456.
3. **Treatment-vs-treatment pairs are charted but never verdicted** — B-vs-C gets a chart +
   audit table (report) and a picker slot (explore) but no WIN/LOSE/FLAT and no explanation
   of the asymmetry. readout.py:428-446, builder.py:260, report.ts:607-612.

**Minor**
4. **Main-metric tier hardcodes `metrics_count=1`** → FWER inflation across *multiple main*
   metrics (the one numeric-correctness item; legacy-faithful, arm-count-independent).
   correction.py:65, analyze.py:72.
5. `abk plan` sizes only variants[0]-vs-[1] (mitigated by a warning). plan.py:424-425.
6. `abk validate` collapses N arms to a two-arm placebo via `share_a` of the first variant;
   no per-pair calibration, no disclosure. validate/load.py:79-88.
7. `abk run` text shows only aggregate counts; `--report` prints an unlabeled join of
   verdict kinds. run.py:224-232, :101-105.
8. Bonferroni pays full C(N,2) even for control-vs-each designs (conservative, not a bug).
   correction.py:26.
9. No per-arm-pair method tuning in explore (Apply is per-metric). server.py:404-428.
10. `activePair` global resets to 0 on metric switch. explore.ts:583, :1432.
11. Control is a positional convention (`variants[0]`) with no `control:` field/validator;
    reordering silently redesignates control. experiment_config.py:70, readout.py:357/:428.
12. Multiple main metrics × multiple treatments → an M×(K−1) verdict matrix with no
    summarization. readout.py:434-446.

**Cosmetic**
13. No collapse/pagination/selector in the report; blocks grow C(N,2)/metric.
    report.ts:614-617.
14. SRM fail is joint with no per-arm culprit decomposition. srm.py:101, :53-73.
15. Explore pair picker is a flat segmented control with no control-vs-treatment grouping
    for large N. explore.ts:1397-1411.

---

## Recommendation (correctness vs UX)

With one exception every gap is presentation: the pairwise numbers, corrections, SRM, and
persistence are correct for N arms. The single numeric-correctness item is the **main-tier
`metrics_count=1` hardcode** (correction.py:65) — it inflates FWER across multiple *main*
metrics regardless of arm count; legacy-faithful, so any change is an `ALGORITHM_VERSION`
bump + `statistics-changes.md` + A/A validation. The **explore Review `.find()`** (gap 1)
is the one UX gap that materially risks a *wrong decision* because it silently withholds
valid verdicts at the moment a role is chosen — treat it as a near-correctness bug even
though the stored data is fine.

**To make multi-arm first-class (priority order):**
1. Fix explore Review to render **all** verdicts for a metric (map, not `.find`) — small,
   high-value (explore.ts:1516).
2. Add an **experiment-level rollup** to `ExperimentReadout` (best-treatment / per-metric
   arm summary) surfaced in the report header + explore Review — the structural change
   (new field + aggregation in readout.py:450-456, threaded through builder.py:473).
3. Give B-vs-C (and every non-control pair) a **verdict card** (extend the readout loop to
   all pairs, or add a page note explaining the control-anchored asymmetry).
4. Add an explicit **`control:`** field (or validate the positional convention) so
   reordering can't silently flip the baseline.
5. Label pairs in `abk run --report` text; add a cross-arm overview + pair selector to the
   report; disclose the `validate` two-arm collapse; size all pairs (or all baselines) in
   `abk plan`.

See [ROADMAP.md](../../../ROADMAP.md) → "Post-baseline hardening" for the now/0.1.x/1.x split.
