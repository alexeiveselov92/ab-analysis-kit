# M7 Implementation Plan — validate: vectorization + iteration policy

> **Implementation record — M7 shipped in full (WP0–WP7 including the
> stretch), 2026-07-19/20; version bumped to `0.2.0`, release-ready pending
> the maintainer's `v0.2.0` tag/publish (G1) step.** Written 2026-07-18 as the
> as-designed contract for M7 (part of the approved polish track M7–M17,
> [ROADMAP.md "The polish track"](../../ROADMAP.md)), in the shape of
> [m6-implementation-plan.md](m6-implementation-plan.md) /
> [m4-implementation-plan.md](m4-implementation-plan.md); amended in place at
> the milestone exit gate into this record (the M4–M6 pattern). The WP bodies
> below keep the original future-tense contract wording ("WP2 adds…") as the
> designed baseline; the **"done" table** below, the **per-WP as-built notes**
> (blockquotes at each WP), and the **exit-gate record** appended to §3 are
> the authoritative as-built account, including the adversarial-review log
> (two rounds ran on *every* WP, not only WP4/WP5).
>
> Governing specs: [aa-false-positive-matrix.md](aa-false-positive-matrix.md) (the
> `abk validate` contract this milestone reimplements the *engine* of, without
> touching its numbers or its UX), [statistics-baseline.md §7](statistics-baseline.md)
> (the "do not drift" numerical conventions this milestone's every gate protects),
> [architecture.md](../../.claude/rules/architecture.md) (`abkit.stats` purity,
> the plugin-registry invariant WP2 extends), and
> [ROADMAP.md "M7 — validate: vectorization + iteration policy"](../../ROADMAP.md).
> Source track plan: `~/.claude/plans/report-md-replicated-truffle.md`; detailed
> WP breakdown: `~/.claude/plans/abkit-v2-details/design_validate.json`;
> code-verified facts: `~/.claude/plans/abkit-v2-details/verify_validate.json`.
> No donor — every WP below is new abkit-native work (unlike M2/M3/M6's
> detectkit ports).

## Status — all work packages shipped (the "done" table)

| WP | Landed as | Squash-merge | Load-bearing as-built delta (details in the per-WP notes) |
|---|---|---|---|
| WP0 — multi-arm Review-mode fix + limitations note | PR #38 | `80650b6`, 2026-07-19 | docs home = `docs/guides/experiments.md` (resolves §4.6) |
| WP1 — scalar hot path, hardening bucket A1–A8 | PR #38 | `80650b6`, 2026-07-19 | frozen-fixture golden gate is rel-1e-9 on floats (BLAS ULPs don't cross machines); bit-parity old-vs-new proven once on the capture machine |
| WP2 — array-wise significance kernel | PR #39 | `af370ad`, 2026-07-19 | **bit-exact** scalar↔batch parity via `_libm_pow` routing (resolves §4.3) |
| WP3 — `vector_resample` block-streamed engine | PR #40 | `b7b6e57`, 2026-07-19 | float aggregates are byte-repro under **fixed** blocking only (block-size bit-invariance is unachievable in principle); §4.4 closed: prefix-sum permanently inapplicable |
| WP4 — `score_cell` dispatcher + vectorized body | PR #41 | `aae8140`, 2026-07-19 | D13 restated "under a fixed BLAS configuration" (thread count moves continuous columns ~1e-15) |
| WP5 — parity gate + executable perf gate | PR #42 | `f1f692a`, 2026-07-19 | exactly-solved CI boundaries may flip one decision between engines (pinned); MDE seam anchors control stats via scalar `build_arm` |
| WP6 — policy: opt-in `--family-sweep` + per-cell auto-N | PR #44 | `fd50ca3`, 2026-07-20 | §4.1: warn above 100 000, never hard-cap; one-release migration notice |
| WP7 — (stretch) `family.py` vectorization | PR #43 | `fc8d796`, 2026-07-20 | landed *inside* M7 (resolves §4.5); family parity gate is exact-only, stricter than planned |

**Zero statistical numbers moved anywhere in the milestone** — no
`ALGORITHM_VERSION` bump in any PR (the exit-gate grep over
`68d3fa8..fd50ca3` finds no version change; the only textual mention added is
a golden-test docstring), no `statistics-changes.md` entry, `abkit.stats`
purity intact, both e2e matrix gates byte-identical. The engine speedups the
milestone set out to deliver, as measured at the WP5/WP7 gates: ~10× per
whole validate cell, ~18× for the family sweep, up to ~149× on the WP1
`normal_test` kernel alone.

## 0. Scope, posture & decisions

**M7 is a performance + policy milestone inside `abkit/validate/` and
`abkit/stats/`. It changes zero statistical numbers.** No `ALGORITHM_VERSION`
bump anywhere in this milestone, no golden retolerancing, `abkit.stats` purity
stays intact (`tests/stats/test_purity.py`). Every WP either (a) makes an
existing scalar code path faster without changing its output, (b) adds a new,
strictly additive numpy-vectorized sibling of an existing scalar path gated
behind an opt-in capability flag, or (c) changes *iteration policy* (how many
Monte-Carlo draws run, whether a second sweep runs at all) — never *what a
draw computes*.

### 0.1 What already exists (the problem this milestone fixes)

- **The hot loop.** `score_cell` (`abkit/validate/scoring.py`) runs
  `for i in range(iterations):` (scoring.py:322) with an inner
  `for k, cut in enumerate(panel.cutoffs):` (scoring.py:335) that calls
  `build_arm` (`resample.py:60-91`) fresh for every `(iteration, cutoff)` pair —
  `values[pos]` then `.sum()`/`SufficientStats.from_sample`, no incremental
  reuse across cutoffs. At the milestone's reference shape (2 methods × 2000
  iterations × 100 grid cutoffs ≈ 800k inner-loop passes) this is the "minutes"
  REPORT measured. `family.py` (the D9 composed multi-metric sweep) has **its
  own, separate** outer loop (`family.py:358`, `for i in range(iterations):
  union_mask = placebo_mask(...)`) and its own per-look walk
  (`_member_peeked_marginals`, `family.py:200-252`) that calls
  `build_arm`/`present_positions`/`placebo_mask` directly rather than going
  through `score_cell` — **vectorizing `scoring.py` alone does not speed up
  `family.py`** (plan-review correction #2, carried below).
- **The scalar scipy hot path.** `effects.py:127` builds a fresh frozen
  `sps.norm(loc, scale)` object per call and invokes `.ppf(...)`/`.cdf(0.0)`/
  `.sf(0.0)` on it (same pattern at `ztest.py:84`); this feeds
  `TestResult.effect_distribution` (`result.py:55`), which is **write-only** on
  the validate/family hot path — `scoring.py`/`family.py` only ever read
  `.left_bound`/`.right_bound`/`.effect`/`.ci_length`/`.pvalue`, never
  `.effect_distribution`, and it is dropped in `to_dict()` (`result.py:73`).
  `power.py:22-23` eagerly imports `statsmodels.stats.power` +
  `statsmodels.stats.proportion` at module load, on every `import abkit.stats`.
  These three are exactly the "0.1.x safe wins" A1/A2/A3 named in
  `docs/research/2026-07-multi-arm-and-stats-core/stats-core-review.md:70-72`
  and restated in ROADMAP's hardening tiers — proposed there, **implemented
  here**.
- **Iteration policy, as it stands today.** `DEFAULT_ITERATIONS = 2000`
  (`runner.py:32`); the composed family sweep silently auto-runs whenever no
  `--metric` filter is given (`runner.py:259-262`,
  `if metric_filter is None: … family = _run_family_sweep(...)`) — doubling
  total work by default, the REPORT "400k+400k=800k" story.
- **The block-streaming precedent to copy.** `abkit/stats/bootstrap/engine.py`
  already solved "vectorize a Monte-Carlo loop without changing a single
  result": a hard **DRAW-ORDER CONTRACT** (engine.py:1-13) — replicates are
  always drawn in fixed quanta of `BLOCK_QUANTUM = 128` (engine.py:64),
  sequentially, and the memory cap `DEFAULT_MAX_BLOCK_BYTES` (256 MiB,
  engine.py:66) only bounds how many quanta are materialized at once, never
  the random stream or the result. WP3 below reuses this contract rather than
  re-deriving it.
- **`supports_sequential` is the existing precedent for an opt-in plugin
  capability flag** (`base.py:259`, `ClassVar[bool] = True` on the base,
  `False` on bootstrap) — WP2's `supports_vectorized` mirrors this shape
  exactly, so adding a new capability to `BaseMethod` is not a novel pattern
  for this codebase.
- **Validate is single-threaded today** — no threading/multiprocessing/
  `concurrent.futures` anywhere under `abkit/validate/`; block-streamed numpy
  vectorization is intended to make a process/thread pool unnecessary, not to
  be combined with one.

### 0.2 What M7 builds

An immediate byte-identical scipy hot-path fix (WP1); a new, purely additive
array-wise significance kernel in `abkit.stats` gated behind
`supports_vectorized` (WP2); a block-streamed permutation-mask + suffstats
aggregation engine in `abkit/validate/` modeled on the bootstrap engine's
contract (WP3); a `score_cell` rewrite that consumes both, with a scalar
fallback for any method that hasn't opted in (WP4); the parity + performance
regression gate this milestone's numeric safety rests on, plus the ≥2
adversarial review rounds (WP5); the two REPORT-named policy fixes —
`--family-sweep` opt-in, N tied to alpha (WP6); and, as a stretch item if it
fits the milestone, `family.py`'s own vectorization reusing WP2/WP3's
primitives (WP7). WP0, riding alongside WP1, closes the one live multi-arm
correctness bug the hardening backlog names for "Now."

### 0.3 Plan-review record — milestone-specific corrections (read before starting any WP)

The track plan (`report-md-replicated-truffle.md`) and its detailed WP
breakdown were adversarially reviewed before implementation (the M4–M6
discipline: 3 critics, 11 findings incl. 1 blocker, folded in). Four
corrections are **specific to M7** and must be carried into every session that
touches this milestone, not re-litigated:

1. **`family.py` has its own hot loop; vectorizing `scoring.py` does not speed
   it up.** REPORT's original framing ("validation = one hot loop") is wrong —
   verified: `family.py:358` and `family.py:200-252` are a second,
   independent loop over the same low-level `resample.py` primitives.
   **Consequence:** the family sweep's default-on behavior is exactly the pain
   point vectorization does *not* relieve on its own, which is why **WP6 makes
   it opt-in** (the pain-by-default goes away regardless of whether it is ever
   vectorized) and **its own vectorization is a stretch WP (WP7)**, not folded
   into WP2–WP5's scope.
2. **The ndtri/ndtr swap must use `ndtr(-z)`, never `1 - ndtr(z)`, for the
   sf-equivalent tail.** `stats-core-review.md:70` is explicit that these are
   **not bit-identical for extreme z** — getting this backwards silently drifts
   results for exactly the tail region the milestone's own invariant ("never
   change a number silently") forbids touching. WP1's golden test must include
   extreme-z fixtures (an effect many SEs from zero) specifically to catch a
   backwards implementation; this is called out again in WP1's risk list below
   because it is the single easiest way to violate the milestone's own
   founding promise while looking like a safe refactor.
3. **WP5's count-exactness claim is an empirical observation, not a theorem.**
   `reject`/significance is derived from *continuous* CI bounds
   (`left_bound > 0` or `right_bound < 0`); a vectorized aggregation that sums
   in a different order than today's per-cutoff `.sum()` (matmul reduction
   order vs. `.sum()` over a fancy-indexed slice) can differ at the ULP level.
   Exact equality of integer *count* fields (valid_iterations, hit counts) across
   **many** random seeds is strong empirical evidence, not a proof — a boundary
   case where an effect sits within machine epsilon of a CI bound is exactly
   where a reordered sum could flip a reject decision that random seeds would
   almost never land on by chance. **WP5's parity suite must therefore include
   a dedicated near-boundary stress fixture** (an effect placed within ~1e-9 of
   the CI-excludes-zero boundary) in addition to broad random-seed coverage —
   this is a mandatory fixture, not an optional nice-to-have.
4. **M7 also carries the live multi-arm bug (WP0) and hardening bucket A (WP1),
   per the maintainer's decision to fold the entire hardening backlog into this
   track.** The bug: `explore.ts:1483` does
   `payload.verdicts.find((v) => v.metric === name)` inside Review mode's
   per-metric row renderer — `verdicts` is one `VerdictBlock` **per (metric,
   control-vs-treatment pair)** (`payload.ts:100`, `VerdictBlock` docstring:
   "per main-metric × control-vs-treatment pair"), so in any experiment with
   more than 2 arms, `.find` silently renders only the **first** matching
   pair's verdict and drops every other treatment arm's verdict for that
   metric — a near-decision UX bug (not a statistical one; the underlying
   per-pair verdicts are all computed and persisted correctly) that a
   3+-arm user could easily misread as "this metric only has one verdict."
   Bucket A is the **whole** eight-item byte-identical list from
   `stats-core-review.md` (restated in ROADMAP's "0.1.x safe wins"): the hot
   path A1–A3 (ndtri/ndtr, lazy statsmodels, lazy `effect_distribution`) *and*
   the cleanup/test items A4–A8 (bootstrap `stat_point` double-compute dedup,
   registry-parametrized method-contract tests, the registry-completeness
   test, the shared parametric `_result_from_normal_test` helper, the
   `samples.py` micro-dedups). All of it lands in WP1 per the approved plan's
   decision #4 and the WP1 row's own wording ("+ параметрический
   `_finalize`-хелпер, registry-контракт-тесты, дедуп double-compute").

### 0.4 Posture for M7–M12 (repeated here per the common track discipline)

- **Parity/golden gates:** exact equality on integer counts (with the
  near-boundary stress fixture mandatory per §0.3(3) above), rel-1e-9 on
  continuous aggregates. A grep for `ALGORITHM_VERSION` diffs across every PR
  in this milestone must be empty; no `docs/specs/statistics-changes.md` entry
  is needed (validate's sample size, iteration policy, and internal loop
  structure are not "statistical numbers" in that invariant's sense — only a
  genuine formula/algorithm change would require one).
- **Session estimates are not contracts.** A WP that does not fit one session
  simply continues into the next; do not compress correctness work to hit an
  estimate. Where this doc's per-WP numbers exceed the approved plan's
  compressed milestone table (WP2/WP3 here carry the detailed breakdown's
  2 sessions vs. the table's 1 each), the detailed estimate is the one carried
  deliberately — the plan's own retro-calibration step after M7/M8 reconciles
  the totals.
- **Perf milestones (M7, M9) carry an executable perf test as an exit
  criterion** — the lesson this track explicitly names is that "a rule without
  an executable gate does not hold" (the 800k-iteration nested loop is itself
  the example of a numpy-first rule that had no such gate).
- **Release checklist:** version bump to `0.2.0`, `CHANGELOG.md` cut from
  `[Unreleased]`, the three-way docs sync (`docs/`, `.claude/rules/`, the
  packaged `init-claude` assets) if any of this milestone's changes are
  user-visible (WP6's CLI flags are), the wheel-namelist + `pip install` smoke,
  tag → `publish.yml` (maintainer's G1 step, never autonomous).

---

## 1. Work packages

WP1 and WP6 are logically independent of the WP2→WP3→WP4→WP5 chain and of each
other; WP0 rides with WP1. WP2, WP3, WP4, WP5 are strictly sequential (each
consumes the previous WP's new module or flag). WP7 is a stretch item that
reuses WP2/WP3's primitives once they exist. See §3 for the full dependency
graph.

### WP0 — the live multi-arm Review-mode bug (mini, rides with WP1)

> **As-built note (WP0 shipped with WP1, PR #38, 2026-07-19).** Landed per the
> steps: the single `payload.verdicts.find(...)` became a per-pair `.filter`
> render (one verdict line per declared pair, existing marker classes reused),
> pinned by new 2-arm-unchanged + 3-arm jsdom smoke assertions over a 3-arm
> fixture variant; the rebuilt `explore.js` shipped in the same PR through the
> CI freshness gate. The "known multi-arm limitations" note lives in
> `docs/guides/experiments.md` (the §4.6 open point, resolved by the session as
> planned) and says exactly what the plan asked: control-vs-each readout (no
> winner rollup until M14), `abk plan` sizes off the first declared pair,
> `abk validate`'s placebo split is two-arm. No downstream Review-mode reader
> assumed one-verdict-per-metric (the risk-list check came back clean).

**Goal:** fix the one near-decision multi-arm correctness-adjacent bug the
hardening backlog names for "Now" — Review mode in `abk explore` silently
shows only the first control-vs-treatment pair's verdict per metric in any
experiment with more than two arms — and pair it with an honest documentation
pass of the known multi-arm limitations, so a 3+-arm user is told the truth
rather than shown a misleading single-verdict row.

**Files:**
- `web/src/explore/explore.ts` (the Review-mode row renderer, `explore.ts:1441-1493`)
- `web/test/fixtures.mjs` (a 3-arm fixture with two `VerdictBlock`s sharing one metric)
- `web/test/smoke-explore.mjs` (a new Review-mode assertion)
- the rebuilt, committed `abkit/tuning/assets/explore.js` (this milestone's
  only web-touching WP; WP2–WP7 are pure Python)
- a documentation home for the "known multi-arm limitations" note — the
  natural candidates are `docs/guides/explore.md` or
  `docs/guides/experiments.md`; the session picks whichever reads more
  naturally next to the existing content and records the choice (flagged as an
  open point in §4, not a blocker)

**Steps:**
1. Replace the single `payload.verdicts.find((v) => v.metric === name)`
   (`explore.ts:1483`) with a filter that collects **every** `VerdictBlock`
   whose `metric === name`, and render one verdict line per matching pair
   (labeled by `verdict.pair.c`/`verdict.pair.t`, reusing the existing
   `abk-review-verdict`/`abk-verdict-<word>` marker classes so the CI
   marker-class grep keeps passing unchanged) instead of the current
   single-verdict `<div>`.
2. Extend `web/test/fixtures.mjs`'s payload builder with a 3-arm variant (a
   third treatment arm plus a second `VerdictBlock` for the same metric,
   different pair) so a jsdom smoke test can assert **both** verdict lines
   render in Review mode, not just the first.
3. Add the regression assertion to `web/test/smoke-explore.mjs` (or a new
   focused test file if that suite is already large) — Review mode with a
   3-arm payload renders one verdict row per declared pair for a metric that
   has more than one.
4. `cd web && npm run build`; commit the rebuilt `explore.js` in the same PR
   (the CI freshness gate: `git status --porcelain -- ':(glob)abkit/*/assets/**'`).
5. Add a short, honest "known multi-arm limitations" note to the chosen docs
   home, naming plainly what is and isn't covered today: the readout is
   control-vs-each (no experiment-level winner rollup — that is M14's job);
   `abk plan` sizes off the first declared pair only; `abk validate`'s placebo
   split is two-arm (pooled cohort permuted into two shares, not a k-way
   split). This is documentation of an existing, unchanged limitation, not a
   new capability — no code behind the doc note beyond the render fix above.
6. `CHANGELOG.md`: one entry, explicitly a UI/UX fix with **no statistical
   number touched** (the underlying per-pair verdicts were always computed and
   persisted correctly; only the Review-mode rendering silently dropped
   rows).

**Tests / gates:**
- The new jsdom smoke assertion: a 3-arm payload with two verdicts sharing a
  metric renders **both** in Review mode (regression-pins the exact bug).
- Existing `web/test/smoke-explore.mjs` assertions stay green unmodified for
  the 2-arm case (one verdict per metric renders exactly as today).
- The bundle-freshness/marker-class CI gates pass with the rebuilt `explore.js`.

**Risks / hotspots:**
- This is a rendering-only fix — verify no other Review-mode code path (e.g.
  the main/guardrail toggle handlers around `explore.ts:1441-1479`) implicitly
  assumed exactly one verdict per metric row before this WP; if a downstream
  read does, that read needs the same map/filter treatment, not just the
  render line.
- Keep the fix scoped to Review mode's row renderer — the Tune/Auto/Segment
  modes' own chip renderers are untouched by this WP (a different code path;
  do not "fix" them speculatively without a matching bug and a fixture proving
  it).

**Session estimate:** 0.5 session (rides with WP1).

---

### WP1 — scalar hot-path quick wins: ndtri/ndtr swap, lazy statsmodels import, lazy `effect_distribution`

> **As-built note (WP1 shipped, PR #38, 2026-07-19).** The full bucket A1–A8
> landed per the steps: the ndtri/ndtr swap measured **~149×** on
> `normal_test` alone (283.8 µs → 1.9 µs per call), the lazy statsmodels
> import removed ~0.5 s from cold `import abkit.stats`, and lazy
> `effect_distribution` shipped as a `LazyNormal` proxy — where review caught
> and fixed a pickle-recursion bug (the proxy's `__getattr__` recursed on
> copy/pickle `__slots__`-protocol probes). A7's `_result_from_normal_test`,
> the A4/A8 dedups, and A5/A6 (registry-parametrized contract suites + the
> registry-completeness gate) all landed as planned. **The one real deviation
> is the golden gate's tolerance discipline, a lesson for every future frozen
> -fixture gate:** bit-for-bit comparison against a frozen fixture does NOT
> transfer across machines — the method fixtures pass through BLAS (`np.dot`
> inside `SufficientStats.from_sample`), and CI runners produce different
> last-ULP floats for the *unchanged pre-WP1 code*. The first CI run failed
> exactly there (cuped-absolute, all three Python jobs). The shipped pattern:
> old-vs-new bit-parity is proven once on the capture machine (including a
> 200 000-case bitwise property check in review round 2); the committed gate
> — `tests/stats/test_normal_path_golden.py` + the fixture
> `tests/stats/fixtures/normal_path_golden.json` (float.hex, extreme-z rows,
> frozen from the pre-WP1 code at `68d3fa8`; regenerate only from a pre-WP1
> checkout) — compares floats at rel-1e-9 (the `tests/golden/` standard) and
> integers/flags/warnings exactly. Adversarial rounds: round 1 confirmed two
> findings (a trip-wire for the `METHOD_CLASSES` roster; a CHANGELOG accuracy
> fix), round 2 found zero.

**Goal:** apply **hardening bucket A in full** (A1–A8,
`stats-core-review.md:70-77`) directly to the **existing scalar** code path
used by both `scoring.py`'s current loop and `family.py`'s loop — byte-identical,
no `ALGORITHM_VERSION` bump. The hot-path trio A1–A3 is the cheapest,
lowest-risk, highest-ratio win in the whole milestone (the hardening audit's
own estimate, `stats-core-review.md:70-72`: up to ~250× on this path alone)
and lands first as a safety net that benefits the *current* loop even before
WP2–WP4's bigger rewrite exists; the cleanup/test items A4–A8 ride in the same
WP per the approved plan's WP1 row (the `_finalize` helper, registry contract
tests, double-compute dedups).

**Files:** `abkit/stats/effects.py`, `abkit/stats/parametric/ztest.py`,
`abkit/stats/power.py`, `abkit/stats/result.py`,
`abkit/stats/sequential/confidence_sequence.py`, `abkit/stats/base.py`,
`abkit/stats/parametric/ttest.py` (+ the 4 parametric siblings, A7),
`abkit/stats/bootstrap/bootstrap.py` (A4), `abkit/stats/samples.py` (A8),
`tests/stats/test_effects.py`, `tests/stats/test_ztest.py` (or the equivalent
existing method test files), `tests/stats/test_bootstrap_methods.py` +
`tests/stats/test_identity.py` (A5), a new registry-completeness test (A6),
`CHANGELOG.md`.

**Steps:**
1. `effects.py:127-131` (`normal_test()`): replace the frozen
   `sps.norm(loc, scale).ppf([...])`/`.cdf(0.0)`/`.sf(0.0)` construction with
   `scipy.special.ndtri`/`scipy.special.ndtr` applied to standardized z-values
   (`z = (bound - effect) / scale`), using **`ndtr(-z)`** for the
   sf-equivalent tail exactly as `stats-core-review.md:70` specifies (§0.3(2)
   above) — stop constructing the frozen `sps.norm(...)` object on this path
   at all.
2. `ztest.py:84-87` (`from_suffstats()`): the same swap for
   `sps.norm(effect, std_effect)` + `.ppf([...])`/`.cdf`/`.sf(z_stat)`;
   precompute the two alpha-only quantiles (`ndtri(alpha/2)`,
   `ndtri(1 - alpha/2)`) once, since they depend only on `self.alpha`, never
   on data.
3. `power.py:22-23`: move
   `from statsmodels.stats.power import NormalIndPower, TTestIndPower` and
   `from statsmodels.stats.proportion import proportion_effectsize` inside the
   functions that use them (`get_ttest_mde`, `get_fraction_mde`, the
   sample-size solves), instead of at module import time — stops eagerly
   loading statsmodels + pandas + patsy on every `import abkit.stats` (~100ms),
   which matters for `abk validate`'s process startup and honors the
   "no pandas in stats core" pledge.
4. `result.py:55,73` (`TestResult.effect_distribution`): make it lazily
   constructed (a cached-property-style proxy, or a zero-arg thunk in place of
   the realized `sps.norm` object) so it is built only if code actually reads
   `.effect_distribution` — preserving the `is not None` truthiness contract
   `stats-core-review.md:72` requires, verified by a new test asserting
   `result.effect_distribution is not None` still holds and `to_dict()` still
   drops it (`result.py:73`) without materializing it.
5. `sequential/confidence_sequence.py` (`se_from_ci_length()`): the same
   `sps.norm.ppf(1 - alpha/2)` recompute-every-call pattern exists here
   (`confidence_sequence.py:44`) — precompute via a small
   `functools.lru_cache`-decorated helper keyed on `alpha` (only a handful of
   distinct alphas exist per process — the declared per-comparison alphas), or
   accept a precomputed `z` as an optional kwarg from callers that already
   know it is fixed per cell (`scoring.py`'s `_cell_tau2`/`_always_valid_sig`
   loop calls this once per iteration per look today).
6. Add or extend a byte-identity golden test: for a grid of representative
   `(effect, var, alpha)` triples — **including edge cases** (`var <= 0`,
   non-finite, near-zero denominator, and **extreme-z fixtures**, §0.3(2)) —
   assert the new ndtri/ndtr-based `normal_test()`/ztest path produces
   IDENTICAL (or, if empirically not bit-identical, a documented, measured max
   ULP delta gated at that observed tolerance) `left_bound`/`right_bound`/
   `pvalue`/`reject` vs. a saved reference captured from the OLD `sps.norm`
   -based code (freeze the old code's output as fixture data before editing —
   the project's golden-test discipline used elsewhere in `tests/stats/`).
7. **A7** — add a shared `_result_from_normal_test` helper on `BaseMethod`
   (the parametric mirror of the bootstrap `_finalize`,
   `stats-core-review.md:76`) and route the copy-pasted `TestResult` assembly
   of the parametric methods (`ttest.py:81-105` + the 5 siblings) through it —
   removes ~50 lines of duplication and the field-drift risk; field-by-field
   parity is pinned by the existing method tests plus the WP1 golden battery.
8. **A4** — dedup the double `stat_point` recompute + the two-pass sign scan
   in bootstrap result assembly (`bootstrap.py:306-310,253-254`) — honestly
   <1% of a bootstrap call, a tidy-up that rides along, not a lever.
9. **A8** — `samples.py` micro-dedups: the doubled `np.mean` in
   `RatioSufficientStats.from_ratio_sample`, reuse `sample.cov_mean` in
   `SufficientStats.from_sample`, and add the `m2_num`/`m2_den ≥ 0` validation
   parity (`samples.py:296,388-408`).
10. **A5 + A6** — registry-parametrize the method-contract test suites
    (dual-entry, seed-exclusion, `to_dict`, quarantine —
    `test_bootstrap_methods.py:25-42`, `test_identity.py`) so any new plugin
    is auto-swept in, and add a **registry-completeness test** (every
    `BaseMethod` subclass reachable via `available_methods()`) — closes the
    "forgotten import silently un-registers" gap. (These tests are also the
    contract net under WP2's `from_suffstats_array` additions.)
11. `CHANGELOG.md`: one entry noting the internal perf fix + bucket-A
    cleanups, explicitly stating "no statistical numbers changed" and citing
    the golden test as evidence.

**Tests / gates:**
- The new/extended golden test: old-`sps.norm`-path vs. new-ndtri/ndtr-path
  identical (or measured-tolerance) CI bounds/pvalue across a battery
  including degenerate cases (`var <= 0`, `mean_den == 0`) and extreme-z
  fixtures.
- Existing `tests/stats/*` method-contract tests (dual-entry equivalence,
  `to_dict`, quarantine) pass unmodified — proves the public `TestResult`
  contract is untouched.
- A microbenchmark (a skipped-by-default pytest-benchmark case, or a simple
  timeit script referenced in the PR description) substantiating the
  CHANGELOG speedup claim.
- `grep ALGORITHM_VERSION` diff across the PR is empty.

**Risks / hotspots:**
- **`ndtr(-z)` vs. `1 - ndtr(z)` is the single easiest way to silently violate
  this milestone's founding invariant** — get the tail formula backwards and
  results drift for extreme z-values with no test failure unless the golden
  battery specifically includes extreme-z fixtures (§0.3(2); this risk is
  restated here deliberately because it is the milestone's own review-flagged
  landmine).
- Lazy `effect_distribution` must not break any downstream code that does
  `TestResult(...).effect_distribution.cdf(...)` synchronously right after
  construction outside the validate/family path (e.g. `abk run`'s own report
  rendering) — grep every real (non-validate/family) call site of
  `.effect_distribution` before assuming laziness is transparent everywhere;
  if a renderer reads it synchronously right after construction the laziness
  is transparent anyway (first access just does the work then), so the risk
  is low but must be verified with a grep, not assumed.
- Moving the statsmodels import inside functions changes import-time
  behavior other code may rely on for fail-fast import-error detection —
  check for any test asserting import-time failure and adjust if found.

**Session estimate:** 1 session.

---

### WP2 — pure array-wise significance kernel in `abkit/stats` (`BaseMethod.supports_vectorized` + `from_suffstats_array`)

> **As-built note (WP2 shipped, PR #39, 2026-07-19).** Landed per the steps:
> `supports_vectorized` on exactly the five planned methods (roster-pinned;
> `paired-cuped-t-test` deliberately does **not** inherit the capability — it
> derives from `BasePairedMethod`, outside the suffstats-scorable set), the
> slim 5-field `BatchEffectResult`, three array kernels in `effects.py`, and
> the sequential siblings `se_from_ci_length_array`/`sequentialize_array`.
> **§4.3 is resolved: bit-exact scalar↔batch parity IS achievable on every
> platform — but only by construction, not by accident.** Review round 1
> found that numpy's `**` differs from C-library `pow` by 1 ULP (even for
> `x**2`; glibc `pow` is not correctly rounded), and the catastrophic
> cancellation in the three-term delta-method variance amplifies that to
> ~1.8e-4 *relative* at CI boundaries — far past rel-1e-9. Fix: every power
> term on the batch path routes through `effects._libm_pow`
> (`np.frompyfunc(math.pow)`, `OverflowError → ±inf`), making parity
> bit-exact by construction; the parity tests demand exact equality for all
> 5 methods × 2 test types. The sequential siblings keep `np.log`/`np.exp`
> (same-sign sums, no cancellation to amplify) under the golden rel-1e-9
> bound — measured byte-identical on the capture environment. Round 2 found
> two more: 0-d columns broke the `frompyfunc → .astype` chain (fix: a
> "column = 1-D" input contract raising `SampleValidationError`) and
> fractional `n` diverged from the scalar path's `int(n)` truncation (fix: a
> `np.trunc` mirror in ttest/cuped/ratio; z-test/paired don't truncate —
> matching their scalar behavior, deliberate). Documented batch divergences,
> pinned by tests: a ddof-1 row with `n < 2` yields a NaN row where the
> scalar raises ("gaps, never zeros" — validate never feeds such a row to the
> scalar either). Measured cost of the libm routing: ~120 ms per 200k-row
> relative batch (~16 ms pow-free) — still orders past the scalar loop.

**Goal:** add an **optional** plugin capability, mirroring the existing
`supports_sequential` precedent (`base.py:259`), so the suffstats-scorable
parametric families that `abk validate` already calls via `from_suffstats` —
`ztest`, `ttest`, `cuped_ttest`, `paired_ttest`, `ratio_delta` — can each
expose an array-wise significance path: given **arrays** of per-arm sufficient
-statistic components, return arrays of `(effect, left_bound, right_bound,
ci_length, pvalue)` in one shot via numpy broadcasting plus WP1's ndtri/ndtr
primitives, with the alpha-only critical value computed once outside the
array call. This is new, additive code — it does **not** touch the existing
scalar `from_suffstats` path (zero regression risk to `abk run`/explore), and
it is the piece that makes WP3+WP4's vectorized engine actually fast
(suffstats aggregation alone would not help if the significance test were
still called scalar-wise 200,000 times).

**Files:** `abkit/stats/base.py`, `abkit/stats/effects.py`,
`abkit/stats/parametric/ztest.py`, `abkit/stats/parametric/ttest.py`,
`abkit/stats/parametric/cuped_ttest.py`,
`abkit/stats/parametric/paired_ttest.py`,
`abkit/stats/parametric/ratio_delta.py`,
`abkit/stats/sequential/confidence_sequence.py`,
`tests/stats/test_vectorized_parity.py` (new).

**Steps:**
1. `base.py`: add `supports_vectorized: ClassVar[bool] = False` alongside
   `supports_sequential` (`base.py:259`), plus an abstract-but-optional
   `from_suffstats_array(self, arrays_1, arrays_2) -> BatchEffectResult` — a
   new, lightweight dataclass (`effect`, `left_bound`, `right_bound`,
   `ci_length`, `pvalue`, all `np.ndarray`) that deliberately does **not**
   build `effect_distribution`/`mde_1`/`mde_2`/`name_1`/etc (validate never
   reads them, per the write-only finding at `result.py:55,73`) — document
   this explicitly as a slim, validate-only capability, not a `TestResult`
   replacement.
2. `effects.py`: add `normal_test_array(effect, var, alpha) -> BatchEffectResult`
   — the array-wise sibling of `normal_test()` (`effects.py:94`), using one
   precomputed `z = ndtri(1 - alpha/2)` scalar, `np.where`/boolean masking for
   the degenerate branch (`var <= 0` or non-finite → NaN row, matching
   `normal_test`'s per-case semantics but a bool mask instead of per-row
   warning strings, since validate never reads warnings on this path), and
   array-wise `ndtr` for the two-sided p-value.
3. `ttest.py`/`cuped_ttest.py`/`paired_ttest.py`/`ratio_delta.py`: each already
   funnels through `effects.absolute_effect`/`relative_delta_effect`/
   `normal_test` (`ttest.py:48-58`) — add array-wise counterparts
   `absolute_effect_array`/`relative_delta_effect_array` in `effects.py` (same
   formulas, numpy broadcasting, `np.where` for the H5 zero-denominator
   hygiene guard instead of a scalar `if`), then wire each method's
   `from_suffstats_array` to call these plus set `supports_vectorized = True`.
4. `ztest.py`: add `from_suffstats_array` reproducing the same inline formula
   (`ztest.py:56-91`) array-wise, **including the documented legacy sign
   quirk verbatim** (`z` uses `prop_1 - prop_2`, `effect` uses
   `prop_2 - prop_1`) and the relative-branch zero-`prop_1` guard as a mask;
   set `supports_vectorized = True`.
5. `sequential/confidence_sequence.py`: add
   `se_from_ci_length_array(ci_length, alpha) -> np.ndarray` and
   `sequentialize_array(effect, se, tau2, alpha) -> tuple[np.ndarray, ...]` —
   the same closed-form math (`confidence_sequence.py:44-70`-ish) vectorized,
   one precomputed alpha-dependent constant, `np.where` for the degenerate
   -look branch instead of the scalar early-return. `tau2`/`alpha` stay
   scalar (frozen once per cell, unchanged from `_cell_tau2` — no change to
   that function).
6. Leave `bootstrap.py`'s `supports_vectorized` at the inherited `False`
   default (it is already excluded from validate's suffstats-scorable set, so
   this is a no-op there — it just confirms the fallback path WP4 needs is
   exercised by at least one real method).
7. New golden test `tests/stats/test_vectorized_parity.py`: for each of the 5
   vectorized methods, generate a batch of `N` random (reproducible-seeded)
   suffstats-array inputs, run `from_suffstats_array` once, and compare
   **every row** against calling the existing scalar `from_suffstats()` `N`
   times in a python loop with the same per-row inputs — assert exact
   equality first; if any method empirically fails exact equality (numpy ufunc
   loop-unrolling vs. scalar scipy call), fall back to rel-1e-9 for **that
   method only** and record the measured max relative error in a code comment
   plus this test's docstring (the empirical answer to open question §4.3).

**Tests / gates:**
- `test_vectorized_parity.py`: `from_suffstats_array` output exactly matches
  (or, per-method, a documented rel-1e-9) the row-by-row scalar
  `from_suffstats` output across randomized fixtures including degenerate
  rows (`var <= 0`, zero denominators, extreme z).
- Existing per-method test suites (ttest/ztest/cuped_ttest/paired_ttest/
  ratio_delta) unmodified and green — proves `from_suffstats_array` is purely
  additive.
- A completeness test enumerating `abkit.stats.registry.available_methods()`
  and asserting **exactly** the expected 5 methods carry
  `supports_vectorized = True` (protects against silent scope-creep or a
  forgotten method) and that bootstrap stays `False`.

**Risks / hotspots:**
- Silent formula drift between the scalar and array paths (two independent
  implementations of the same math) is the single biggest correctness risk of
  this WP — mitigated **only** by the golden parity test being exhaustive
  (every branch: absolute vs. relative `test_type`, degenerate var/denominator,
  the ztest sign-quirk and relative-branch guard) and run in CI on every PR
  touching either path, not just once at introduction time.
- `np.where` evaluates **both** branches eagerly (unlike a scalar `if`), so a
  degenerate-row computation (e.g. division by a zero `mean_den`) must not
  raise/warn under numpy's default error state — wrap array-kernel bodies in
  `np.errstate(divide='ignore', invalid='ignore')` deliberately (mirroring how
  the scalar H5 guard avoids exceptions), or a batch with even one degenerate
  row will spam `RuntimeWarning` or, worse, propagate a warning-as-error under
  a `pytest -W error` configuration used elsewhere in the suite.
- `BatchEffectResult` must not get accidentally treated as replacing
  `TestResult` in any other pipeline path — keep it in a clearly
  validate-scoped location/naming (e.g. a consistent `_array`/`_batch` suffix)
  so a future contributor does not wire `abk run` through it and inherit its
  deliberately-missing fields (`mde`, `warnings`, `name_1`/`name_2`).

**Session estimate:** 2 sessions.

---

### WP3 — vectorized permutation-mask + block-streamed suffstats aggregation engine

> **As-built note (WP3 shipped, PR #40, 2026-07-19).** `vector_resample.py`
> landed per the steps — `placebo_mask_block` (row *i* is literally
> `placebo_mask(derive_seed(*parts, start + i))`, bit-identical by
> construction), `block_rows`/`iter_blocks` (the engine's cap arithmetic plus
> a sub-quantum floor down to one row — legal here because masks are
> seed-per-row, blocking-independent, which the donor bootstrap engine's
> draw-order contract cannot allow), and `build_arm_batch` → one GEMM per arm
> per cutoff over pooled-shifted one-pass co-moments (sample/CUPED/fraction/
> ratio; column keys shared with WP2's `*_ARRAY_KEYS`), degenerate flags per
> `(iteration, cutoff)` with NaN-poisoned stat rows. Two WP4 seams shipped
> here: `prepare_cutoff` (hoisting, identity-guarded `prepared.cut is cut`)
> and `weights_scratch` (~−25%). `inject.py` gained
> `inject_multiplicative_columns`/`injection_clamped_columns` (bit-exact
> mirror of scalar injection; one deliberate pinned divergence — scalar
> `max(0.0, nan) == 0.0` swallows a NaN-m2 gap, the batch path preserves it).
> **The load-bearing empirical finding: bit-invariance of float aggregates to
> block size is unachievable in principle** — both BLAS (M-dependent kernels)
> and plain `np.sum(axis=1)` round the same row differently under different
> buffer heights. The honest, shipped contract: masks/counts/flags exact
> under ANY blocking; float aggregates byte-reproducible under a FIXED
> blocking (D13 holds — WP4 derives its blocking deterministically from
> `(iterations, n_units)`), rtol-1e-12 across blockings. A BLAS-free
> multiply-and-sum variant was 10–20× slower with no stability gain — GEMM
> stays (the Poisson engine precedent: matmul already lives under a
> byte-repro e2e). Second finding: the rel-1e-9 scalar-parity claim has a
> conditioning boundary at `|value|/σ ≲ 1e10` — beyond it the *scalar* itself
> drifts (m2 inflation from the rounded arm mean, `count·ulp(|y|)²/4`;
> ~5e-9 measured at 1e12/3) — pinned by 1e8/1e10/1e12 fixtures. §4.4 is
> closed empirically: **cross-cutoff prefix-sum is permanently inapplicable**
> (refund-style and max-style metrics make per-cutoff values non-monotone;
> recorded in the module comment as the plan required). Reviews: round 1
> (numerics + contracts) 2 major / 6 minor, round 2 (fresh reviewer) 2 major
> / 3 minor — all fixed, incl. float32-input normalization (`asarray(f64)`,
> without which parity broke on ordinary value offsets) and the memory-claim
> split into capped vs fixed parts (k ≤ 5 per-unit columns of `8·k·n_units`
> bytes sit outside the block cap). 58 new tests.

**Goal:** a new module implementing the `(iterations × n_units)`
permutation-mask generation and the per-cutoff sufficient-statistic
aggregation via batched matmul, block-streamed under a memory budget exactly
like `abkit/stats/bootstrap/engine.py`'s `BLOCK_QUANTUM = 128` +
`max_block_bytes` contract (engine.py:1-13,64-68) — block size must never
change results, only memory footprint. This WP also resolves the
prefix-sum-vs-dense-matmul design question empirically: because
`RecomputeBackend` re-renders the **full window** `[start_ts, end_ts)` fresh
per cutoff (`recompute_backend.py:3-4`), a continuing unit's per-cutoff value
is **not** a simple one-time append (unlike a naive donor-pattern assumption)
— so the correct, provably-safe primitive is a **per-cutoff dense matmul**
against that cutoff's own values array (exactly what today's `build_arm`
already receives), not a cross-cutoff prefix-sum recurrence. This still
collapses `2000 × 100 = 200,000` python-level `build_arm` calls into ~100
matmul calls per iteration-block — the dominant win.

**Files:** `abkit/validate/vector_resample.py` (new), `abkit/validate/resample.py`,
`tests/validate/test_vector_resample.py` (new).

**Steps:**
1. `placebo_mask_block(n_units, share_a, seed_parts, block_start, block_size)
   -> np.ndarray`: returns a `(block_size × n_units)` int8/bool matrix where
   **row `i` of the block is exactly** `placebo_mask(n_units, share_a,
   derive_seed(*seed_parts, block_start + i))` (`resample.py:26-42`) — i.e.
   reuse `make_rng`/`derive_seed` per-row unchanged. This is the one place
   that must be bit-identical **by construction**, not by empirical test,
   since it is literally calling the same existing function per row.
2. `iter_blocks(iterations, quantum=BLOCK_QUANTUM) -> Iterator[tuple[int,int]]`:
   mirrors `bootstrap/engine.py`'s fixed-quantum block iteration
   (`BLOCK_QUANTUM = 128`, `engine.py:64`) exactly — import/reuse
   `BLOCK_QUANTUM` and a `max_block_bytes`-driven block-count-per-materialization
   policy from `abkit.stats.bootstrap.engine` rather than reimplementing the
   memory-budget arithmetic (DRY, and inherits
   `DEFAULT_MAX_BLOCK_BYTES = 256 MiB`).
3. `build_arm_batch(input_kind, cut, covariate, mask_block) -> ArmStatsBatch`:
   for the block's mask columns restricted to `cut.unit_idx` (an
   `(block_size × n_present)` slice), compute via matmul —
   `sumA = mask_a_present.astype(float64) @ cut.values`,
   `sumsqA = mask_a_present.astype(float64) @ (cut.values ** 2)`,
   `countA = mask_a_present.sum(axis=1)` — one matmul per statistic per arm
   per cutoff, covering the whole block's iterations in one BLAS call; branch
   on `input_kind` exactly like `build_arm` (`resample.py:79-94`) for
   `fraction` (count/nobs sums) and `ratio` (numerator/denominator/covariance
   sums) so the same three `input_kind`s are supported. Covariate handling:
   since covariate is a fixed per-unit constant (not per-cutoff), compute
   covariate-weighted sums (needed for CUPED/`SufficientStats.from_sample`'s
   cov terms) the same matmul way.
4. Degenerate-arm handling: `MIN_ARM_UNITS = 2` (`resample.py:21`) gap
   semantics must be preserved **per `(iteration, cutoff)`** — an arm with
   `countA[i] < 2` for a given iteration must be flagged degenerate for that
   iteration/cutoff, not just at the cutoff level (today's `build_arm` returns
   `None` per `(iteration, cutoff)` pair, tallied into `degenerate_horizon`
   only at the horizon look) — return a boolean degenerate mask array
   `(block_size,)` alongside the stat arrays so WP4 can replicate the exact
   per-iteration gap bookkeeping.
5. New test file `test_vector_resample.py`:
   (a) row-for-row `placebo_mask_block` identity vs. `resample.placebo_mask`
   for a battery of seeds/`n_units`/`share_a` combos — **exact equality**, the
   master identity contract;
   (b) `build_arm_batch`'s `(sum, sumsq, count)` vs. looping
   `resample.build_arm` + extracting suffstats scalar-by-scalar for the same
   mask rows, across `sample`/`fraction`/`ratio` `input_kind`s and several
   `n_present` sizes including `MIN_ARM_UNITS` boundary cases — rel-1e-9
   (documented reason: matmul reduction order differs from `.sum()`'s
   reduction order over a fancy-indexed slice);
   (c) a block-size invariance test (running the same iterations at
   `quantum = 32` vs. `128` vs. one single block, asserting byte-identical
   aggregate arrays) mirroring the bootstrap engine's own contract test,
   since validate currently has no such test at all (a real gap the risk
   list flags).
6. A short code comment at the top of `vector_resample.py` records the
   full-window-recompute finding (`recompute_backend.py:3-4`) as the reason a
   cross-cutoff prefix-sum was **not** used, so a future contributor does not
   "optimize" it back into an incorrect append-only scheme; resolve open
   question §4.4 (refund/non-monotone check) empirically as part of this WP
   and update the comment with the finding either way.

**Tests / gates:**
- `placebo_mask_block` row-for-row **exact** match vs. `resample.placebo_mask`
  across a seed/`n_units`/`share_a` matrix.
- `build_arm_batch` rel-1e-9 match vs. scalar `build_arm`-derived suffstats
  across `sample`/`fraction`/`ratio`, including `MIN_ARM_UNITS = 2` boundary
  and empty-arm degenerate cases.
- Block-size invariance test: `quantum ∈ {32, 128, 1000, iterations}` all
  produce byte-identical aggregate arrays for a fixed iterations count (new
  test, no precedent in validate today, modeled on the bootstrap engine's
  implicit contract).

**Risks / hotspots:**
- The degenerate-per-iteration gap bookkeeping is easy to get subtly wrong in
  vectorized form (e.g. accidentally counting a degenerate iteration as a
  "non-significant zero" instead of excluding it from `valid_iterations`) —
  exactly the failure mode the module's docstring must warn "gaps, never
  zeros" about (`scoring.py:26-27`); the test suite must specifically include
  a fixture where **some** iterations degenerate and **some** don't, and
  assert the **denominator** (`valid_iterations`-equivalent) matches, not just
  the numerator.
- Memory: a naive `(block_size × n_units)` dense mask before restricting to
  `cut.unit_idx` could be wasteful for panels with many cutoffs each touching
  a small present-subset of a huge population — restrict/slice by `unit_idx`
  **before** casting to float64 for the matmul (bool/int8 → float64 cast only
  on the already-narrowed slice) to avoid a `block_size × n_units` float64
  blowup; verify with a memory-profiled test on a large synthetic `n_units`
  (e.g. 1e6) to substantiate the "block-streamed under budget" claim is not
  just aspirational.
- Covariate-weighted sums for CUPED suffstats (mean/var of the covariate and
  its covariance with the primary value) require the same per-unit covariate
  array indexed by `unit_idx[pos]` (`resample.py:93`) — double-check the
  matmul form reproduces `SufficientStats.from_sample`'s exact covariance
  formula (ddof convention etc.), since this is a place a silent formula
  deviation could sneak in without WP2's golden test catching it (WP2 tests
  the *significance* kernel, not suffstats construction).

**Session estimate:** 2 sessions.

---

### WP4 — rewrite `score_cell` to run the vectorized engine, with scalar fallback for non-opted-in methods

> **As-built note (WP4 shipped, PR #41, 2026-07-19).** `score_cell` became a
> dispatcher on `method.supports_vectorized` → `_score_cell_vectorized`
> (block-streamed: `iter_blocks` × `build_arm_batch` × `from_suffstats_array`,
> with O(block) *streaming* first-crossing state — the argmax-on-all-False
> footgun the plan warned about is impossible by construction, no
> `(iterations × cutoffs)` significance matrix is ever materialized) /
> `_score_cell_scalar` (a verbatim code move, confirmed by mechanical diff).
> The MDE/exaggeration loop stayed `iterations`-shaped via per-row horizon
> stats; the injected pass reuses the horizon batch; `_value_1_rows` anchors
> the value_1 fallback. Ten smoke-parity cases (5 input kinds × ±inject)
> pinned counts/curves/ratio fields EXACT and continuous fields rel-1e-9
> ahead of WP5's full battery. Measured ~10× on the reference cell (≈2.5 s vs
> ≈25 s, CUPED 2000 iterations × 100 cutoffs × 2000 units, with injection).
> Reviews (round 1: two hunters, 2 major / 5 minor; round 2: fresh, 0 major /
> 5 minor — all fixed) produced four load-bearing findings: (1) **the GEMM
> engine's byte-reproducibility depends on the BLAS thread configuration**
> (OpenBLAS 1 vs ≥2 threads moves continuous columns ~1e-15 rel; counts
> stable; the scalar engine is thread-invariant) → D13 is now stated "under a
> fixed BLAS configuration" everywhere, per the Poisson-engine precedent;
> (2) the hoisted prepared-cutoff buffers now take the *remainder* of the
> single 256 MiB cap after the block working set (the draft had two additive
> budgets); (3) a lying plugin (`supports_vectorized = True` without a
> kernel) raises `ValidateError` — caught per cell — instead of a
> `NotImplementedError` that would kill the whole matrix; (4) a pre-existing
> crash shared by both engines: an exactly-zero pooled ratio `mean_den`
> reached `ZeroDivisionError` in `_point_estimate` past the runner's catch →
> guarded like `_arm_linearisation`, which also unblocked the value_1
> fallback (integration-covered with ±1 denominators). Round-2 polish: the
> hoist no longer prepares *empty* cutoffs (`load.py` only guards the horizon
> — the "Mean of empty slice" RuntimeWarning was production-reachable), and
> the missing-kernel diagnostic honestly reads "missing OR refusing this
> input".

**Goal:** replace `scoring.py`'s
`for i in range(iterations): for k, cut in enumerate(panel.cutoffs):`
(`scoring.py:322,335`) with a block-streamed loop over WP3's `iter_blocks()`,
each block computing every cutoff's arm suffstats via `build_arm_batch` and
every look's significance via WP2's `from_suffstats_array` (single-look FPR,
cumulative-peeking FPR + curve, power, coverage, achieved MDE, effect
exaggeration, the D8 always-valid twin) using numpy reductions instead of
python accumulation. Gate on `method.supports_vectorized`: when `False` (any
future/custom plugin without a batch kernel), fall back **unchanged** to
today's scalar per-iteration loop so validate never breaks for a method that
has not opted in (the "methods are plugins" invariant). This is the
highest-risk WP in the milestone because `CellScore` has many interacting
fields (τ² anchor, always-valid twin, injected pass, MDE, exaggeration,
degenerate tallies) that must all be reproduced.

**Files:** `abkit/validate/scoring.py`, `tests/validate/test_scoring.py`.

**Steps:**
1. Keep `_cell_tau2` (`scoring.py:156-192`) unchanged and scalar — it runs
   once per cell (not per iteration), is already cheap, and touches the
   sequential anchor identity the D8 parity requirement depends on; do not
   vectorize this function.
2. Keep `_horizon_index`, `_analytic_mde`, `_injected_truth`, `_point_estimate`
   (`scoring.py:209-238,523-528,547-574`) unchanged as scalar helpers —
   MDE/injected-truth are reporting-only best-effort values computed once per
   iteration on the horizon arm only, not part of the FPR/power decision path,
   so they stay a lightweight per-block python loop over the
   (already-vectorized) horizon arm-stats arrays without meaningfully hurting
   perf (100 cutoffs' worth of work disappears; only the horizon's worth of
   MDE calls remain — `iterations` calls total instead of
   `iterations × cutoffs`).
3. Rewrite the main body: for each block from `vector_resample.iter_blocks`,
   call `build_arm_batch` for every cutoff (still a ~100-iteration python
   loop, but each iteration is one matmul over the whole block, not one
   python-object construction per `(iteration, cutoff)` pair) to get
   `arm_a`/`arm_b` stat-batches per cutoff; feed each cutoff's `(arm_a,
   arm_b)` batch into `method.from_suffstats_array` to get `(effect,
   left_bound, right_bound)` arrays; compute the CI-excludes-zero
   significance array via vectorized boolean ops replacing `_significance`
   (`scoring.py:140-153`) elementwise (`np.isfinite` mask, `left > 0` /
   `right < 0` boolean arrays).
4. Reproduce `_first_significant_look`'s semantics (`scoring.py:531-544`)
   vectorized: for each iteration in the block, the **first** cutoff index
   (in grid order) whose significance is `True` — implementable as
   `np.argmax(sig_matrix, axis=1)` with an explicit guard for rows that are
   all-`False` (no crossing) via `sig_matrix.any(axis=1)`, since `argmax` on
   an all-`False` boolean row returns 0 (a false positive at look 0) unless
   masked out — a classic vectorization footgun WP5's parity test must
   specifically exercise (a fixture where an iteration never crosses).
5. Reproduce the always-valid twin (`scoring.py:354-361,394-402`) using WP2's
   `sequentialize_array` against the same `(effect, se)` arrays derived from
   `ci_length` via `se_from_ci_length_array`, applying the frozen per-cell
   `tau2` (unchanged scalar) — same first-crossing-index logic as the fixed
   column.
6. Reproduce the injected pass (`scoring.py:404-444`) — horizon-only, so this
   is `iterations`-shaped (not `iterations × cutoffs`): call
   `from_suffstats_array` once per block on the horizon arm with the injected
   treatment arm, vectorized coverage/power hit counting.
7. Sum/tally all counts (`single_look_hits`, `peek_hits`, `valid_iterations`,
   `degenerate_horizon`, `power_hits`, `coverage_hits`, `coverage_n`, and the
   `_seq` siblings, width sums) as running scalars **across** blocks
   (accumulate per-block partial sums — this is where block-streaming pays
   off: no need to hold all `iterations` results in memory at once, matching
   the bootstrap engine's memory discipline) and assemble the final
   `CellScore` exactly as today's tail code (`scoring.py:446-520`) already
   does — that assembly code is pure arithmetic on final scalars and needs no
   changes.
8. Add the `method.supports_vectorized` branch at the top of `score_cell`: if
   `False`, call a renamed-but-otherwise-untouched `_score_cell_scalar(...)`
   containing today's existing loop body verbatim (a pure code move, not a
   rewrite) so the fallback path is provably identical to pre-milestone
   behavior.
9. Update `tests/validate/test_scoring.py`: existing tests pass **unchanged**
   against the new vectorized path for the 5 `supports_vectorized` methods
   (proves the public `score_cell` contract didn't change); add one test
   using a synthetic `BaseMethod` stub with `supports_vectorized = False` to
   exercise and pin the scalar-fallback branch.

**Tests / gates:**
- `tests/validate/test_scoring.py::test_scoring_is_byte_reproducible` still
  passes unmodified against the new code path.
- New fixture-driven test: an iteration that never crosses significance
  across the whole grid must **not** be miscounted via the
  argmax-on-all-False footgun (explicit regression test for this specific
  vectorization bug class).
- New scalar-fallback test: a stub method with `supports_vectorized = False`
  produces an **identical** `CellScore` to calling the WP4-renamed
  `_score_cell_scalar` directly, proving the fallback path is a pure
  code-move with no behavior change.
- Full `tests/validate/` suite green (`test_resample.py`, `test_load.py`,
  `test_inject.py`, `test_persistence.py`, `test_runner.py` untouched by this
  WP).

**Risks / hotspots:**
- This WP touches the single most field-dense function in the codebase
  (`CellScore` has ~20 fields, all interacting through shared per-iteration
  state) — the biggest risk is a silent off-by-one or miscounted denominator
  (`valid_iterations` vs. `coverage_n` vs. `width_n` are three subtly
  different denominators today, `scoring.py:298,302,320`) that only shows up
  as a small percentage-point drift in the rounded e2e golden test, or worse
  doesn't show up there at all because it rounds to .1%.
- The MDE/exaggeration per-iteration scalar loop (kept unvectorized by
  design) must still iterate over `iterations` (not `iterations × cutoffs`) —
  implemented carelessly, it could regress back to iterating per
  `(iteration, cutoff)` and silently reintroduce most of the original cost; a
  perf assertion in WP5 catches this, but a code-review pass in this WP should
  specifically confirm the loop bound.
- Block-streaming accumulation (partial sums across blocks) must not
  double-count or drop the last partial block (block size may not evenly
  divide iterations — the same edge case `bootstrap/engine.py`'s
  `BLOCK_QUANTUM` contract already solved once; reuse its block-iteration
  helper rather than re-deriving the boundary arithmetic).

**Session estimate:** 2 sessions.

---

### WP5 — parity gate, perf gate, and the milestone exit-gate hardening pass

> **As-built note (WP5 shipped, PR #42, 2026-07-19).**
> `tests/validate/test_vector_parity.py` landed wider than the plan's floor:
> **8 fixture shapes** (sample/cuped/absolute/fraction/ratio + adversarial
> sparse/cuped-sparse/clamp) × 50 seeds per shape by default
> (`ABKIT_PARITY_SEEDS` env raises it; the exit run used 200 = 1600
> scalar↔vectorized pairs, all green), with the exact class covering counts +
> curves + warnings + `achieved_mde`, the continuous class at rel-1e-9
> (measured ≤ 2e-14), a trip-wire pinning `CellScore` field completeness,
> multi-block runs (quantum 1/7/128), and rare branches pinned on scanned
> deterministic seeds (τ²-unanchorable {2029, 14168, 18071, 18617};
> no-valid-horizon; overcounted-fraction {0, 1, 3}; negative-root boundary
> {108, 124}). **The §0.3(3) near-boundary stress fixture produced the
> milestone's subtlest finding: at an *exactly solved* CI boundary (brentq
> inversion, `|left_bound| ≲ 1e-15`) the two engines legitimately flip one
> decision (GEMM vs `.sum()` last-ULP; measured as power 0.5 vs 0.6 on one
> seed) — pinned by a dedicated "≤ 1 hit, power only" test; at ±1e-9 offsets
> parity is strictly exact.** The perf gate runs the reference case
> (2 methods × 2000 iterations × 100 cutoffs × 1000 units) under 10 s
> CI-safe (the CI Test job runs under `--cov`, ~2×: measured ~1.3–1.7 s bare,
> ~2.2–2.5 s cov-on, vs ~25 s scalar). Review round 1's MAJOR (sonnet fuzz,
> 26/9000 cases): CUPED at `n = 2` ⇒ correlation ≡ ±1, a knife-edge where
> `achieved_mde` flipped None↔0.0 between engines (persisted; feeds the
> Recommended-row tie-break) → fixed by anchoring the MDE seam's control
> stats through scalar `build_arm` on the mask row (bit-identity by
> construction; `_control_stats_from_row` deleted). Round 2's MAJOR: that fix
> crashed on corrupt fraction data (per-unit successes > trials with a finite
> pooled CI) → try/except-skip per row; the residual divergence class —
> scalar fails the cell, batch scores it, on *corrupt* data only — is
> documented in `aa-false-positive-matrix.md §9` and regression-pinned.
> Hardening the batch degenerate flag (`count > nobs`) is a named follow-up,
> deliberately NOT taken: an ULP-tolerant `count ≈ nobs` check would kill
> legitimate cells. The spec's §9 "Implementation note" (step 5) shipped in
> the same PR.

**Goal:** build the honest cross-implementation regression gate this
milestone's entire numeric safety rests on, plus the performance regression
test that proves the "minutes to sub-second" claim, then run the two
adversarial review rounds the project's discipline requires before closing
the milestone.

**Files:** `tests/validate/test_vector_parity.py` (new),
`tests/validate/test_vector_perf.py` (new),
`tests/e2e/test_validate_matrix.py`, `tests/validate/test_sequential_parity.py`,
`tests/validate/test_family_sweep.py`, `docs/specs/aa-false-positive-matrix.md`.

**Steps:**
1. `tests/validate/test_vector_parity.py`: build a handful of small-to-medium
   synthetic `PlaceboPanel` fixtures (reuse `tests/validate/_panels.py`'s
   existing builders) spanning `sample`/`fraction`/`ratio` `input_kind`s, with
   and without a covariate (CUPED), with and without injected effect, with a
   grid dense enough to exercise the peeking/always-valid columns. For each
   fixture, run **both** `_score_cell_scalar` (WP4's preserved-verbatim old
   loop) and the new vectorized `score_cell` with identical
   `seed_parts`/`iterations`, and assert: (a) **exact** equality of every
   count field (`valid_iterations`, `degenerate_horizon`, and the raw hit
   counts backing `fpr`/`peeking_fpr`/`power`/`coverage`/`fpr_sequential`/etc
   — these are integers, so "exact" is well-defined and is the real
   golden-style invariant); (b) rel-1e-9 equality of every continuous mean
   field (`achieved_mde`, `effect_exaggeration`, `ci_width`,
   `ci_width_sequential`, `tau2`), reasoning documented inline (matmul
   reduction order vs. `.sum()` reduction order, WP3's finding).
2. Run `test_vector_parity.py` across **many seeds** (e.g. 50-200 distinct
   `seed_parts` tuples per fixture) rather than one, since a single lucky
   seed could hide a boundary-flip bug — this is the practical answer to the
   risk list's "probability ~0 but not provably zero" concern: empirical
   breadth substitutes for a formal proof. **Include the mandatory
   near-boundary stress fixture** (§0.3(3)): an effect placed deliberately
   within ~1e-9 of the CI-excludes-zero boundary, exercised across the same
   seed battery.
3. `tests/validate/test_vector_perf.py`: construct (or reuse an existing large
   fixture from `tests/e2e/` if one exists at sufficient scale) a panel sized
   to the reference case (2 methods × N iterations × 100 grid cutoffs) and
   assert wall-clock time for `score_cell` is under a generous CI-safe bound
   (a proposed `< 5s` to absorb CI noise; the actual measured number — expected
   well under 1s on typical dev hardware per the milestone's perf target — is
   recorded in the PR description/CHANGELOG rather than baked into the
   assertion itself, to avoid a flaky/aspirational test).
4. Run the **full existing suite**
   (`tests/e2e/test_validate_matrix.py`, `tests/validate/test_sequential_parity.py`,
   `tests/validate/test_family_sweep.py`, `tests/e2e/test_sequential_matrix.py`)
   unmodified against the new default vectorized path and confirm
   byte-for-byte / documented-tolerance agreement with their pinned numbers —
   these are the project's existing golden references and must not move.
5. `docs/specs/aa-false-positive-matrix.md`: add a short "Implementation note"
   section documenting the vectorized engine's existence, the exact/rel-1e-9
   parity design, and the block-streaming memory contract, mirroring how
   `bootstrap/engine.py`'s docstring documents its own contract — so a future
   contributor reading the spec finds the invariant, not just the code
   comment.
6. Adversarial review round 1: hunt specifically for (a) any code path where
   the vectorized degenerate/gap handling could silently convert a gap into a
   zero (the module's own stated invariant, `scoring.py:26-27`) and (b) any
   place a block boundary could leak into a random-stream or aggregate-count
   difference (mirroring the bootstrap engine's own draw-order contract
   test).
7. Adversarial review round 2 (after round-1 fixes): re-run the full
   parity+perf+e2e suite and specifically re-examine WP2's per-method golden
   test tolerances (exact vs. rel-1e-9 per open question §4.3) now that real
   numbers exist, tightening any tolerance that was set defensively-loose
   during development.

**Tests / gates:**
- `test_vector_parity.py`: exact count-field agreement + rel-1e-9
  continuous-field agreement, across ≥50 seeds × ≥4 fixture shapes
  (`sample`/`fraction`/`ratio`, ± covariate, ± injection), plus the
  near-boundary stress fixture.
- `test_vector_perf.py`: the 2-method × N × 100-cutoff reference case
  completes within the stated CI-safe bound; the actual measured number is
  recorded in the PR/CHANGELOG.
- `tests/e2e/test_validate_matrix.py`, `test_sequential_parity.py`,
  `test_family_sweep.py`, `test_sequential_matrix.py` all green unmodified.
- Two adversarial review rounds completed and their findings resolved or
  explicitly deferred with a tracked follow-up.

**Risks / hotspots:**
- A parity test that is too narrow (few seeds, few fixture shapes) gives
  false confidence — the single biggest risk to the whole milestone's safety
  claim, since the e2e golden tests round to .1% and would **not** catch a
  boundary-decision flip on their own; breadth of the new parity suite is
  doing real safety work here, not just checking a box.
- A perf assertion baked at an aspirational threshold (e.g. hard-asserting
  `< 200ms`) will be flaky on loaded CI runners and get silenced/skipped over
  time, defeating its purpose — prefer a generous bound plus a recorded
  measured number.
- If WP2's per-method golden test needed a rel-1e-9 fallback for any method,
  this WP's overall count-level exactness claim is only as strong as how
  close that per-method tolerance sits to the boundary-decision epsilon (a CI
  bound sitting within 1e-9 of zero) — worth a dedicated fixture placing an
  effect deliberately near a CI boundary to stress-test this specific
  interaction rather than relying on random seeds alone (this is the same
  mandatory fixture named in §0.3(3) and step 2 above — one fixture serving
  both the milestone-review correction and this WP's own risk list).

**Session estimate:** 1 session.

---

### WP6 — policy: opt-in family sweep + N tied to alpha, CLI/docs/CHANGELOG

> **As-built note (WP6 shipped, 2026-07-20).** Landed per the steps below with
> these recorded specifics: (a) open question §4.1 resolved per the plan's own
> recommendation — **warn above 100 000, never hard-cap**
> (`AUTO_ITERATIONS_WARN_ABOVE`, a log-and-continue `DecisionEntry`); (b) the
> deprecation-cycle risk item is addressed with a **one-release migration
> notice** (a decision-log entry from the runner + a yellow CLI line) on any
> bare multi-metric run, not a deprecation cycle; (c) `--family-sweep`
> combined with `--metric` is logged-and-skipped rather than composing a
> half-family over whichever panels happened to load; (d) the family sweep's
> shared draw count resolves at the **tightest** member alpha (min α ⇒ max N)
> so every member's rate gets the per-cell policy's precision; (e) the
> persisted row's `iterations` column records the **resolved** per-cell N
> (never the unresolved `None`), while the run-stamp keeps the raw setting so
> auto and explicit-2000 runs stamp differently; (f) Auto mode
> (`POST /validate`) keeps its explicit reduced N and deliberately does not
> opt in to the family sweep (the D3 chip keys on per-cell rows only); (g) the
> exit-gate e2e gained `family_sweep=True` at both `ValidateSettings` call
> sites — its numbers are pinned byte-for-byte, `iterations=` was already
> explicit — plus the packaged init-claude assets/guides/spec were synced in
> the same PR (the three-bodies rule). Adversarial rounds (2 code/contract
> hunters + 1 fresh): round 1 found five docs/test-polish items (reference
> CLI page drift was the largest), round 2 found the one real wiring gap —
> the §4.1 warning lived only in `decision_log`, whose sole other consumer
> is the Auto-mode JSON reply, so the CLI now echoes resample warnings as
> yellow terminal lines (wiring pinned by
> `test_auto_n_warning_reaches_the_terminal`). A tight alpha shared by a
> cell and the family sweep may warn once per surface (distinct labels) —
> intended, bounded by the cell count.

**Goal:** ship the two behavior-changing policy decisions REPORT calls out
(items 7 and 8): the family-sweep second pass (`runner.py:259-262`) stops
auto-enabling and becomes an explicit opt-in, and the flat
`DEFAULT_ITERATIONS = 2000` (`runner.py:32`) is replaced by a per-cell default
tied to that cell's alpha (`N ≥ ~200/alpha`), while `-n`/`--iterations`
remains a hard CLI override. Both are pure policy/plumbing changes independent
of the WP1–WP5 engine rewrite (they change *what* gets run and *how many*
times, not the per-iteration math), so this WP can be developed in parallel —
though it is far more valuable to users once WP1–WP5 have made the larger
default N and the (now opt-in, still-unvectorized-unless-WP7-lands) family
sweep both fast.

**Files:** `abkit/validate/runner.py`, `abkit/cli/main.py`,
`abkit/cli/commands/validate.py`, `docs/guides/validate.md`,
`docs/specs/aa-false-positive-matrix.md`, `CHANGELOG.md`,
`tests/validate/test_runner.py`.

**Steps:**
1. `cli/main.py:200` — change
   `@click.option("--iterations", "-n", default=2000, ...)` to `default=None`
   (so click passes `None` through when the user doesn't pass `-n`) and update
   the help text to `'Placebo A/A splits per cell (default: tied to each
   cell's alpha, N ~ 200/alpha)'`.
2. `cli/main.py`: add a new opt-in flag, e.g.
   `@click.option("--family-sweep/--no-family-sweep", default=False, help=
   "Also run the composed multi-metric FWER/FDR sweep (D9) — doubles the
   iteration cost; previously always ran when --metric was omitted")`,
   threaded through `run_validate`/`_validate_one`
   (`validate.py:56-134`) into a new `ValidateSettings` field.
3. `runner.py`: add `family_sweep: bool = False` to `ValidateSettings`
   (`runner.py:34-40`) and change the gate at `runner.py:259` from
   `if metric_filter is None:` to `if settings.family_sweep:` — the literal
   fix for the "silently doubles work" finding (`runner.py:259-262`).
4. `runner.py`: change `ValidateSettings.iterations` type to
   `int | None = None` (default `None` = auto-policy) and add a small pure
   helper, e.g.
   `def _default_iterations(alpha: float, *, floor: int = 200) -> int:
   return max(DEFAULT_ITERATIONS, math.ceil(floor / alpha))` (so the 5%
   main tier still gets a sensible floor even if `200/alpha` alone would round
   below today's 2000 for unusually large alpha, and a tighter secondary-tier
   alpha scales up as REPORT's table shows: ~4000 at 5%, ~40000 at 0.5%) —
   wire it into the per-cell scoring call in `_score_one`/wherever
   `settings.iterations` is currently read verbatim, resolving to
   `settings.iterations if settings.iterations is not None else
   _default_iterations(spec.alpha)` **per cell** (each `_CellSpec` already
   carries its own `alpha`, `runner.py:52`), not once globally, since
   main-tier and secondary-tier cells have different alphas in the same run.
5. Confirm (and add a regression test asserting) that
   `tests/e2e/test_validate_matrix.py` is unaffected: both call sites
   (`test_validate_matrix.py:95,271`) pass `iterations=` explicitly into
   `ValidateSettings`, bypassing the new auto-policy entirely — a
   verified-safe fact, not an assumption, pinned by a comment in the test file
   plus a new `test_runner.py` case that explicitly passes `iterations=None`
   and asserts the resolved per-cell N matches `_default_iterations(alpha)`
   for a couple of representative alphas (0.05 and a tighter secondary-tier
   value).
6. `docs/guides/validate.md` and `docs/specs/aa-false-positive-matrix.md`:
   document both policy changes plainly — the new `--family-sweep` opt-in
   flag (with a callout that this is a **behavior change** from the previous
   default-on), and the new N-per-alpha default table (mirroring REPORT's own
   N/SE table, so users understand why their run count went up for
   secondary-tier metrics).
7. `CHANGELOG.md`: two clearly-behavior-changing entries (family sweep
   opt-in; N-per-alpha default) distinguished from WP1's byte-identical-fix
   entry, explicitly noting these are **not** statistical-number changes (they
   change Monte-Carlo sample size / which passes run, not any computed
   statistic) so they don't need `docs/specs/statistics-changes.md` or an
   `ALGORITHM_VERSION` bump — call this distinction out explicitly since it is
   easy to conflate with the hard invariant.
8. Resolve open question §4.1 (N ceiling) with the maintainer before
   finalizing `_default_iterations` — implement whichever answer is chosen
   (uncapped, or capped-with-warning) as a one-line change to the helper plus
   a docs line.

**Tests / gates:**
- `test_runner.py`: `_default_iterations(0.05)` and `_default_iterations(0.005)`
  (or whatever the project's actual secondary-tier alpha is) match the
  documented N~200/alpha table.
- `test_runner.py`: family sweep does **not** run when `--family-sweep` is
  omitted (default `False`), does run when passed, for a fixture experiment
  with more than one declared comparison.
- `tests/e2e/test_validate_matrix.py` passes unmodified (explicit
  `iterations=` bypasses the new default in both call sites). *(As built: the
  `iterations=` half held, but the file DID gain `family_sweep=True` at both
  `ValidateSettings` call sites — without it the default flip would silently
  drop the D9 sentinel-row assertions this gate pins; see the as-built note
  above, point (g). Its numbers are unchanged byte-for-byte.)*
- CLI help text (`abk validate --help`) reflects both new/changed flags; the
  CLI smoke test (if one exists in `tests/cli/`) is updated.

**Risks / hotspots:**
- Flipping the family-sweep default from always-on to opt-in is a genuine,
  user-visible behavior change (some users' existing scripts/dashboards may
  implicitly rely on `_ab_aa_runs` family sentinel rows being populated even
  without a new flag) — this needs a clear CHANGELOG callout and possibly a
  deprecation-cycle discussion (a warning-if-would-have-run-family-sweep
  -but-didn't message for one release) rather than a silent flip; flag this
  explicitly for maintainer sign-off before merging.
- The N-per-alpha default **increases** compute cost for every existing
  user's default (no `-n`) run (main tier goes from 2000 to ~4000 per
  REPORT's own table) — this WP's value is entirely contingent on WP1–WP5
  having landed first (or concurrently) so the higher N stays fast; merging
  WP6 before WP4/WP5 land would reintroduce a real slowdown for users still on
  the old scalar engine, so sequence WP6's release/rollout (even if
  developed in parallel) to ship no earlier than WP4/WP5.
- Uncapped N for very small secondary-tier alphas (open question §4.1) could
  produce multi-hour runs even under vectorization if alpha is pathologically
  small (e.g. a project misconfigures a 0.001% tier) — until the maintainer
  answers §4.1, land a conservative log-and-continue safeguard (e.g. warn if
  computed N > 100,000) rather than leaving it fully uncapped by default.

**Session estimate:** 1 session.

---

### WP7 — (stretch) vectorize `family.py` on WP2/WP3's primitives

**Goal:** apply the same vectorization discipline to `family.py`'s own,
separate hot loop (`family.py:358` outer, `family.py:200-252` per-look walk),
reusing WP2's `from_suffstats_array`/`sequentialize_array` and WP3's
`vector_resample` primitives, so the composed multi-metric FWER/FDR sweep is
fast **when it does run** (now opt-in per WP6, but still worth making cheap
for the users who turn it on). This is explicitly a stretch item: it rides on
the milestone-review correction (§0.3(1)) that vectorizing `scoring.py` does
**not** touch `family.py` at all, and it lands only if WP0–WP6 have already
closed with capacity to spare — a session estimate that doesn't fit simply
pushes this WP into a later session or a follow-up milestone without
blocking M7's `0.2.0` release.

**Files:** `abkit/validate/family.py`, `tests/validate/test_family_sweep.py`.

**Steps:**
1. Reuse WP3's `placebo_mask_block`/`iter_blocks`/`build_arm_batch` for
   `family.py`'s own union-cohort mask generation (`family.py:358`,
   `union_mask = placebo_mask(n_union, share_a, derive_seed(*seed_parts, i))`)
   and per-member arm construction, in place of the scalar per-iteration
   `placebo_mask`/`build_arm` calls.
2. Reuse WP2's `from_suffstats_array` per family member to replace the
   scalar `member.method.from_suffstats(arm_a, arm_b)` calls
   (`family.py:_member_marginal`/`_member_peeked_marginals`) with a
   vectorized per-member significance pass; `composed_significance`
   (`abkit/stats/correction.py:97`) itself is not touched — it already
   operates on a list of `SignificanceInput`s per iteration and can be called
   once per look over the vectorized marginal arrays, or left as a per
   -iteration call if vectorizing the Bonferroni∘BH composition itself proves
   not worth the complexity for this stretch WP (a scope call the
   implementation session makes and records).
3. Preserve every per-member scorability gate verbatim: a member whose cohort
   is too small to ever split ≥2 units/arm stays a persistent gap (never
   silently rides in the family verdict as if validated — the M5 exit-gate
   round-2 finding this code already guards against, `member_scored`
   tracking) and a sequential-ineligible member (`tau2 is None`) stays a gap
   in the always-valid twin only, not the fixed marginal.
4. Extend `tests/validate/test_family_sweep.py` with the same parity
   discipline as WP5: exact count-field agreement + rel-1e-9 continuous-field
   agreement between the old scalar `family.py` loop and the new vectorized
   one, across the same seed-battery breadth.

**Tests / gates:**
- A parity suite for `family.py` mirroring WP5's shape (exact counts,
  rel-1e-9 continuous fields, many seeds).
- `tests/validate/test_family_sweep.py`'s existing assertions (the
  scorability-gate/gap semantics) pass unmodified.
- No perf regression asserted as a hard gate for this stretch WP (WP5's gate
  already covers the milestone's primary reference case); a recorded
  before/after timing in the PR is sufficient.

**Risks / hotspots:**
- The same "own hot loop" correction that motivates this WP (§0.3(1)) means
  none of WP4's scoring.py-specific work transfers directly — this is a
  second, parallel vectorization effort, not a reuse of WP4's rewrite, and
  should be estimated and reviewed as such rather than assumed to be "the
  same fix, twice."
- If this WP does not fit inside M7, it is explicitly safe to defer — the
  family sweep is opt-in as of WP6, so an unvectorized-but-opt-in family
  sweep is materially safer for users than today's silently-always-on one,
  even without WP7.

**Session estimate:** 1 session (stretch — may slip past M7's close without
blocking the `0.2.0` release; WP6's opt-in flip is the load-bearing fix
either way).

> **As-built note (WP7 shipped, 2026-07-19/20; PR #43, `fc8d796`).** Two deliberate deviations from
> the steps above, both stricter than the plan's ask: (a) the parity gate is a
> NEW file, `tests/validate/test_family_vector_parity.py` (mirroring WP5's own
> delivery shape), leaving `test_family_sweep.py` untouched and green; (b) the
> gate asserts **exact** equality on every `FamilyScore` field — no rel-1e-9
> class at all — because every family column is a count ratio, an
> exact-fraction sum accumulated in identical iteration order (the composition
> stays the scalar `composed_significance` per iteration in both engines), or
> a passthrough; there is no GEMM-order-dependent continuous mean at the
> family level. `composed_significance` itself was left scalar per step 2's
> scope option. The corrupt-fraction divergence class (scalar crashes /
> batch scores) carries over from the scoring engines and is spec-documented
> + regression-pinned for the family surface (aa-false-positive-matrix.md §9);
> the batch engine additionally carries the scalar `except Exception` net so
> a structural kernel raise gaps the member, never the sweep (review round 1).

---

## 2. Dependency graph / parallelism

```
WP0 (multi-arm bug, web-only) ─── independent, rides with WP1

WP1 (scipy hot path, scalar) ──── independent, first (safety net)

WP2 (array-wise stats kernel) ─┬─▶ WP4 (score_cell rewrite) ─▶ WP5 (parity + perf + exit gate)
WP3 (vector_resample engine)  ─┘                                        │
                                                                          ▼
WP6 (policy: opt-in family sweep + N/alpha) ── independent, but should not
    ship before WP4/WP5 land (else the higher default N slows the OLD
    scalar engine for users still on it)

WP7 (stretch: family.py vectorization) ── reuses WP2+WP3's primitives;
    lands after WP2/WP3 exist, independent of WP4/WP5/WP6
```

- **WP0 and WP1 are independent of the WP2→WP5 engine chain** and of each
  other; both can land first as the milestone's cheapest wins.
- **WP2 and WP3 are the two new primitive layers WP4 consumes** — they do not
  depend on each other (one is a pure-stats array kernel, the other is a
  validate-local permutation/aggregation engine) and can be developed in
  parallel, but both must land before WP4 starts.
- **WP4 is strictly gated on WP2 + WP3.** WP5 is strictly gated on WP4 (it
  parity-tests WP4's output against the preserved scalar path and perf-tests
  the assembled engine).
- **WP6 is logically independent of WP1–WP5** (per the design JSON's own
  dependency note: "WP6's CLI/runner policy change only needs WP4 merged if
  you want the perf win to already exist, but is logically independent and
  could ship before or after") — but its *rollout* should not precede WP4/WP5
  per the risk noted in WP6 itself.
- **WP7 is the milestone's only stretch item** — it depends on WP2/WP3
  existing (it reuses their primitives) but not on WP4/WP5/WP6; if it does
  not fit inside M7 it defers cleanly without blocking the `0.2.0` release.
- **No dependency on any other in-flight polish-track milestone.** M7 is
  self-contained inside `abkit/validate/` + `abkit/stats/` (design JSON's own
  `dependencies` field) — it does not touch `abkit/loaders/`, the assignment
  macro, or any DB-layer code M8 will later change. See §5 for the one
  forward-looking dependency this milestone creates for later milestones.

---

## 3. Exit gate

`tests/e2e/test_validate_matrix.py` stays green **byte-for-byte**:
`test_worked_example_numbers_match_the_spec_table` (the `.1%`-rounded
fpr/peeking_fpr/power/coverage per metric against the `aa-false-positive
-matrix.md §8` worked table) and `test_run_is_byte_reproducible` (exact
-equality two-fresh-runs check) — both currently pass `ITERATIONS=2000`
explicitly into `ValidateSettings` (`test_validate_matrix.py:95,271`), so they
are the milestone's primary regression backstop and are unaffected by the
N-per-alpha default change. `tests/validate/test_sequential_parity.py` and
`tests/validate/test_family_sweep.py` stay green (D8/D9 keep working, per the
non-negotiable).

The new parity suite (WP5) passes: exact-count agreement between the scalar
and vectorized `score_cell` on a battery of seeded synthetic fixtures
(≥50 seeds × ≥4 fixture shapes, plus the mandatory near-boundary stress
fixture), plus rel-1e-9 agreement on continuous aggregates. The new perf
regression test asserts the 2-method × N × 100-cutoff reference case
completes within the WP5 CI-safe bound.

**No `ALGORITHM_VERSION` bump anywhere** (a grep for `ALGORITHM_VERSION`
diffs in the milestone's PRs must be empty) and **no
`docs/specs/statistics-changes.md` entry** is needed (validate's sample size
and internal loop structure are not "statistical numbers" in that
invariant's sense — only a genuine formula/algorithm change would require
one).

**Two adversarial review rounds** are run on WP4 and WP5 specifically (the
riskiest WPs), focused on: (a) hunting for any code path where the vectorized
engine's degenerate-arm/NaN-CI handling diverges from `build_arm`'s
`MIN_ARM_UNITS = 2` gap semantics; (b) hunting for any place where a
block-boundary (`BLOCK_QUANTUM`-style) could leak into the random stream or
the aggregate counts. Findings are written up and either fixed in the same
round or explicitly deferred with a tracked follow-up, per the M1–M6
discipline.

**Release step** (per the common track discipline, §0.4): `__version__` bumps
to `0.2.0`; `CHANGELOG.md`'s `[Unreleased]` section is cut into a dated
`0.2.0` section; the three-way docs sync runs if WP0/WP6's user-visible
changes (the `--family-sweep`/`-n` help text, the multi-arm limitations note)
touch any of `docs/`, `.claude/rules/`, or the packaged `init-claude` assets;
the wheel-namelist + `pip install` smoke gates pass; tag → `publish.yml` is
the maintainer's G1 step, never taken autonomously.

> **✅ Exit-gate record (run 2026-07-20, the `0.2.0` release session).**
> Every gate above was executed against `main` at `fd50ca3` (all eight WP
> squashes merged):
>
> - **Full suite:** 1859 passed, 3 skipped (1 Docker-dependent e2e —
>   `test_first_run_clickhouse.py`; 1 backend-parametrize skip on a mocked
>   MySQL affected-rows assertion; 1 opt-in `ABK_BENCH` microbenchmark),
>   including `tests/e2e/test_validate_matrix.py` byte-for-byte
>   (worked-example table + two-fresh-runs reproducibility, explicit
>   `iterations=2000` + `family_sweep=True` per the WP6 as-built note),
>   `tests/e2e/test_sequential_matrix.py` (now exercising the vectorized
>   family path), `tests/validate/test_sequential_parity.py`, and
>   `tests/validate/test_family_sweep.py` — all green unmodified. Web suite
>   38/38.
> - **Parity gates:** `test_vector_parity.py` — 8 shapes × 50 seeds standard
>   (the exit run at `ABKIT_PARITY_SEEDS=200` = 1600 pairs, green);
>   `test_family_vector_parity.py` — exact-only across its 5 shapes (exit run
>   1000 pairs, green). Exact counts everywhere; continuous fields measured
>   ≤ 2e-14 against the rel-1e-9 gate.
> - **Perf gate:** `test_vector_perf.py` green in CI under coverage
>   (reference case < 10 s bound; measured ~1.3–1.7 s bare, ~2.2–2.5 s
>   cov-on, vs ~25 s scalar).
> - **No `ALGORITHM_VERSION` bump:** the grep over the milestone diff
>   (`68d3fa8..fd50ca3`) finds zero version changes in `abkit/` (the single
>   textual hit is the WP1 golden-test docstring *naming* the invariant); no
>   `statistics-changes.md` entry exists for M7, as required.
> - **Adversarial review:** two rounds ran on **every** WP (the per-WP
>   as-built notes above carry each round's findings and fixes) — the §3
>   requirement named WP4/WP5 specifically; both got their two rounds
>   (WP4: 2 major/10 minor total; WP5: the R1 `achieved_mde` knife-edge, the
>   R1 gate-critic pair — parity-coverage gaps on the τ²-unanchorable /
>   `valid_iterations==0` branches + an overstated perf margin — and the R2
>   corrupt-fraction crash; PR #42's body carries the itemized list, and the
>   WP5 as-built note above narrates the two engine-changing majors), and the
>   two named hunt targets came back with the two documented boundary findings
>   (the fixed-blocking byte-repro contract; the exactly-solved-boundary
>   single-decision flip), both pinned by dedicated tests rather than fixed
>   away (they are properties of floating-point GEMM, not bugs).
> - **Release step:** executed in the exit-gate PR — `__version__ = "0.2.0"`,
>   CHANGELOG cut dated 2026-07-20, ROADMAP/CLAUDE.md/`.claude/rules/`
>   flipped to "M7 shipped", the three-way docs sync already landed with WP6
>   (packaged assets/guides/spec state the opt-in flag + auto-N); the
>   wheel-namelist + `pip install` smoke gates run in the same PR's CI; the
>   `v0.2.0` tag push remains the maintainer's G1 call.

---

## 4. Open questions / before-start decisions

These are the design JSON's own `open_questions`, plus the plan's "before
start" line for M7 — each needs either a maintainer call or an empirical
answer produced by the WP that can resolve it, before the *next* WP that
depends on the answer proceeds.

1. **Per-cell N-policy ceiling — RESOLVED (WP6, 2026-07-20): warn above
   100 000, never hard-cap** (the plan's own recommendation, adopted as the
   maintainer call; `runner.AUTO_ITERATIONS_WARN_ABOVE`). Original question:
   REPORT's `N ≥ ~200/alpha` grows unboundedly as alpha shrinks (e.g. a
   hypothetical 0.01% secondary tier → `N = 2,000,000`/cell). Should
   `runner.py` cap the auto-computed N at some ceiling (e.g. 50,000) with a
   warning, or let it grow freely now that vectorization makes it cheap? The
   plan's own recommendation: **warn above 100,000**, do not hard-cap — a
   log-and-continue safeguard rather than silently truncating a user's
   configured alpha tier. Affects `docs/specs/aa-false-positive-matrix.md`
   wording (WP6, step 6).
2. **Is `BaseMethod.from_suffstats_array` required or optional? — RESOLVED
   (WP2, 2026-07-19): optional, exactly as recommended.**
   `supports_vectorized = False` is the base-class default (mirroring
   `supports_sequential`, `base.py:259`); `score_cell` falls back to the
   scalar loop for any non-opted-in method (bootstrap exercises the fallback
   for real). Purely additive to the plugin contract, not breaking — the
   roster test pins exactly five opted-in methods.
3. **Is WP2's array-kernel bit-parity claim actually achievable? — RESOLVED
   (WP2, 2026-07-19): yes, bit-exact on every platform, but only by
   construction.** The empirical arbiter (WP2's own golden test) found the
   hazard was not ufunc loop-unrolling but numpy `**` vs C-library `pow`
   (1 ULP apart, amplified to ~1.8e-4 rel by the cancelling delta-method
   variance sum); routing all batch power terms through `effects._libm_pow`
   made scalar↔batch parity bit-exact for all five methods and both test
   types, enforced as exact equality by the tests. Only the sequential
   siblings' `log`/`exp` keep a rel-1e-9 gate (same-sign sums, measured
   byte-identical on the capture environment).
4. **Does full-window recompute rule out ANY cross-cutoff optimization? —
   RESOLVED (WP3, 2026-07-19): yes, permanently.** Refund-style (a
   continuing unit's cumulative value can decrease) and max-style metric SQL
   both make per-cutoff values non-monotone-appendable, ruling out every
   prefix-sum-across-cutoffs scheme, not just the naive append-only one.
   Recorded in `vector_resample.py`'s module comment as "permanently
   inapplicable" per this question's own protocol.
5. **Stretch WP7 scheduling — RESOLVED (2026-07-19/20): attempted immediately
   after WP5 closed, landed inside M7** (PR #43, `fc8d796`, merged before
   WP6's policy WP #44). The capacity was there because WP2–WP5 each fit their
   sessions; the family sweep is therefore both opt-in (WP6) *and* vectorized
   (~18×) in `0.2.0`.
6. **Where does WP0's "known multi-arm limitations" note live? — RESOLVED
   (WP0, 2026-07-19): `docs/guides/experiments.md`**, next to the existing
   experiment-shape content, as recorded in the WP0/WP1 PR (#38).

---

## 5. Dependencies (recap) and the forward-looking contract this milestone creates

- **No dependency on any other in-flight polish-track milestone** — M7 is
  self-contained inside `abkit/validate/` + `abkit/stats/`; it does not touch
  `abkit/loaders/`, the assignment macro, or `RecomputeBackend`'s public
  surface (it only reads the fact that `RecomputeBackend.load_cutoff` renders
  the full window per cutoff — WP3's design constraint, not a code change to
  that module). It has no dependency on M8's forthcoming
  `build_cohort_backend`/`ab_cohort_source` factory: `abkit/validate/load.py`
  keeps calling `RecomputeBackend.load_cutoff` exactly as it does today
  throughout M7; migrating `load.py` onto the M8 factory is M8's own WP4
  concern, not touched here.
- **M7 is a forward dependency for M13 and M15.** Both later milestones move
  actual statistical numbers under full change control (`ALGORITHM_VERSION` +
  `statistics-changes.md` + A/A revalidation); that revalidation runs through
  `abk validate`, and the whole point of doing M7 *first* in the track's
  ordering is that the revalidation step is cheap (sub-second, not minutes)
  by the time M13/M15 need it. This doc does not restate M13/M15's WPs (they
  have no detailed breakdown yet — each opens with its own design session,
  per ROADMAP), but the dependency runs one direction only: M13/M15 depend on
  M7's vectorized engine existing; M7 does not depend on anything from them.
- **Inter-milestone collisions that do NOT involve M7:** M8's
  `build_cohort_backend`/`ab_cohort_source` factory (which M9's STATE writer
  and tail-scan must build on exclusively), the two schema breaks collected
  in M10, and M11 cloning `tuning/server.py` after M10 WP4 — all downstream of
  M7, none of them touch or depend on this milestone's WPs. They are recorded
  here only so a reader of this doc knows M7 is not the milestone that
  introduces or resolves them.

## Related specs and sibling milestone docs

- [aa-false-positive-matrix.md](aa-false-positive-matrix.md) — the `abk
  validate` contract (mechanism, the matrix, the honest peeking FPR, §7's
  `_ab_aa_runs` shape, the §8 worked example) this milestone's engine
  reimplements without touching.
- [statistics-baseline.md §7](statistics-baseline.md) — the numerical
  conventions ("do not drift" list) every parity gate in this milestone
  exists to protect.
- [statistics-changes.md](statistics-changes.md) — the change-control process
  this milestone explicitly does **not** invoke (no entry expected).
- [m4-implementation-plan.md](m4-implementation-plan.md) — the original `abk
  validate` engine this milestone accelerates (WP1 "engine core", D1-D13).
- [m5-implementation-plan.md](m5-implementation-plan.md) — the sequential
  engine (`stats/sequential/`) whose `se_from_ci_length`/`sequentialize` WP2
  vectorizes, and the D8/D9 family-sweep machinery WP7 would extend.
- [m6-implementation-plan.md](m6-implementation-plan.md) — the format donor
  for this document's shape (§0 posture, per-WP goal/steps/tests/risks,
  plan-review record, exit gate, open questions).
- [m8-implementation-plan.md](m8-implementation-plan.md) (forthcoming) — the
  next milestone in the track; introduces `build_cohort_backend`, which
  `abkit/validate/load.py` will migrate onto only in M8's own WP4, not here.
- [ROADMAP.md](../../ROADMAP.md) — "The polish track" section (versioning,
  cross-milestone discipline, the coverage map) and the "M7" entry this
  document is the design contract for.
