# Statistics: deliberate changes vs the baseline

> Companion to [statistics-baseline.md](statistics-baseline.md). The baseline is the
> legacy math captured verbatim. This document is the **changelog of intent**: every
> way the new engine deviates from or extends it. The discipline is *process*, not
> frozen output: **never change a number silently.** Each change is an
> `ALGORITHM_VERSION` bump + an entry here + an A/A-validated justification.
>
> We are **not** bound to the legacy production numbers (storage is greenfield); the
> baseline is a *reference point* we reproduce first (golden-tested against the
> legacy *engine*) so that every later improvement is measured against a known
> anchor.

## 0. The process: capture → reproduce → blind-rederive → synthesize

Per the project intent, improving the math is a deliberate, measurable loop:

1. **Capture** the legacy algorithm verbatim (done — baseline + catalogue).
2. **Reproduce** it in `abkit.stats` and golden-test against the legacy engine
   (the same `Sample` inputs through the old Python) to a relative tolerance
   (§1.1). This proves we captured it before touching it.
   > *M1 honesty note:* the legacy engine source is not available inside this
   > repository, so the M1 golden anchor is an **independent transcription** of
   > the frozen catalogue (written from the documents only, blind to the new
   > engine). This breaks the tie to catalogue mis-captures only partially — a
   > true engine-vs-engine parity run (the quorum's "migration/parity command")
   > remains an explicit onboarding-time follow-up before any legacy migration
   > is declared verified.
3. **Blind-rederive** each estimand from first principles with **no sight of the
   legacy code** (a separate agent/author task), producing an independent "what the
   textbook-correct method should be".
4. **Synthesize & arbitrate** the legacy vs blind versions with the **A/A
   false-positive matrix** ([aa-false-positive-matrix.md](aa-false-positive-matrix.md)):
   whichever has empirical FPR closest to nominal α (and better power / CI coverage
   on held-out splits) wins. The loser is kept as a version-bumped alternative.
5. **Record** the chosen default + rationale here.

This is how we honor "my old implementation is probably good, but not certainly
optimal" without guessing.

## 1. Corrections to the baseline spec itself

### 1.1 Golden-test tolerance: **relative 1e-9**, not exact equality
Routing through `SufficientStats` (`var = Σx²/n − x̄²`, or Welford) cannot be
**bit-identical** to the legacy `np.var` over raw arrays on large-revenue sums.
Decision: declare "reproduced" at **relative 1e-9** with a documented justification,
use a **two-pass / Welford** variance (not `Σx²/n − x̄²`) in accumulation, and ship
a **heavy-tailed revenue golden fixture** proving the chosen path matches `np.var`.

### 1.2 The mixed-ddof convention is real (baseline fact #1, corrected)
The legacy uses `np.var`/`np.std` with `ddof=0` **but** `np.cov` (θ and the paired/
CUPED covariance terms) with numpy's default `ddof=1`. The engine encodes the
**exact per-term `ddof`** in the `SufficientStats` co-moment formulas, with a golden
test on **θ itself**. A blanket-`ddof` rewrite is forbidden (it fails every
CUPED/paired golden test). A uniform-ddof variant is offered only as a v2 version bump.

## 2. Engine hygiene (version-bumped fixes, A/A-arbitrated, never silent)

These are real legacy issues (catalogued in
[../reference/legacy-method-catalogue.md](../reference/legacy-method-catalogue.md));
each ships as a documented, opt-in-or-version-bumped correction so defaults stay
baseline-faithful until A/A proves the fix helps. **Exception:** fixes the
[quorum gate](quorum-review.md) lists as *blocking must-fixes* (H6 exact
stratum apportionment; the H2 seed policy) ship enabled at v1 — the quorum
itself is their arbitration — with a §7 record.

| # | Legacy issue | Change |
|---|---|---|
| H1 | Global `np.random.seed` mutates process-wide state (non-reentrant, breaks parallelism) | Thread `np.random.default_rng(seed)` Generators; paired bootstrap shares one explicit Generator. Process-safe (enables the concurrency model). |
| H2 | Bootstrap CIs non-deterministic run-to-run; seed excluded from identity ⇒ an idempotent re-write silently changes published CIs (can flip `reject`) | **Deterministic per-row seed** from `(exp, metric, name_1, name_2, end_ts, n_samples)`; seed excluded from `method_config_id` for **all** bootstrap methods. Golden test: two runs over the same window → byte-identical rows. |
| H3 | `np.apply_along_axis(mean,…)` loops in Python — the dominant bootstrap cost | Fast path `matrix.mean(axis=1)`; `apply_along_axis` only for arbitrary callables. |
| H4 | Bootstrap p-value can be exactly 0 (no smoothing); ties at 0 uncounted | `(#extreme + 1)/(n + 1)` plug-in; documented tie convention. |
| H5 | Relative effect divides by control mean with no guard (inf/NaN on sparse metrics) | Small-denominator guard → warn/NaN, surfaced in the report. |
| H6 | Stratified weight rounding (`int`, `max(1,…)`) changes total N | Largest-remainder (Hamilton) apportionment so per-stratum counts sum exactly. |
| H7 | Poisson bootstrap is only correct for the **mean** but accepts any `stat_func` | Assert/validate `stat == mean`; document; (weighted quantiles are a separate future method). |
| H8 | KS normality check uses estimated params (uncalibrated p) | Drop the per-comparison KS warning or use a calibrated test (Lilliefors). Diagnostic only. |
| H9 | `'effect'` means different things across methods (point estimate vs `mean(boot)`) | Standardize: `effect` = original-sample point estimate everywhere; bootstrap mean reported separately as a bias diagnostic. |
| H10 | Bootstrap value-matrix OOMs at scale (`n_samples × sample_size`, doubled with covariate) | Default/auto-select the **Poisson** engine above a unit threshold; stream resampling in replicate blocks under a memory cap; pre-flight memory estimate in `plan`/`run`. (See [cumulative-intervals.md](cumulative-intervals.md) §5.8.) |

## 3. Quarantined / broken legacy methods (do **not** silently substitute)

The extraction + quorum confirmed three post-normed classes are broken or
mislabeled. Policy: **reproduce only if an experiment actually used them** (check
the historical method usage); otherwise raise a hard error + an entry here. Never
silently map them to the principled `ratio_delta`.

- **`PoissonPostNormedBootstrap`** — verified a verbatim copy of `PoissonBootstrap`
  (no post-norming at all). Either implement real covariate-ratio post-norming with
  Poisson weights, or **remove**. A regression test must assert it differs from
  `PoissonBootstrap`.
- **`PairedPostNormedBootstrap` (relative)** — verified it z-score-standardises then
  takes a ratio of ~zero-centered values (denominator ≈ 0 ⇒ explodes). The relative
  branch is **dropped** (hard error + entry); not reproduced.
- **`PostNormedBootstrap` (absolute)** — `S2 − (S2_cov/S1_cov)·S1` is an unusual
  estimand. Reproduce verbatim under `ALGORITHM_VERSION=1` only where used; offer the
  principled `ratio_delta` as the v2 default. **Known-answer test:** `ratio_delta`
  reduces to `S2 − S1` when the covariate ratio = 1.

Also flagged (reproduce-for-parity, document the asymmetry):
- **`ZTest` relative** lacks the delta-method covariance term the t-test has (it
  naively divides `std_effect` by `prop_1`). Since z-test is the default for fraction
  & main metrics, document it and offer a delta-consistent z-test relative as a
  version bump **if** the A/A matrix shows under-coverage.
- **`TTest` family uses Normal, not Student-t** — fine at large N; ship an opt-in
  Student-t + Welch–Satterthwaite df variant (v2) for small-N experiments.

## 4. New families the legacy lacked (opt-in or validation-layer)

Defaults stay baseline-faithful; these are additive.

- **SRM gate** — before every comparison; blocking-but-non-dropping (`srm_flag`).
  Cadence-dispatched: χ² at daily-and-coarser, an anytime-valid sequential
  multinomial e-process below 1d (§4.2).
  ([data-contract-and-reporting.md](data-contract-and-reporting.md))
- **Sequential / always-valid CIs** (mSPRT) + alpha-spending — opt-in
  (`sequential.enabled`) to make the cumulative daily series honest about peeking
  (decision Q2). Default off (legacy parity); the readout refuses pre-horizon
  WIN/LOSE under fixed-horizon.
- **Benjamini-Hochberg** cross-metric correction — opt-in, read-time. Its
  composition with peeking must be validated empirically (not applied to
  peeking-inflated marginal p-values blindly).
- **`ratio_delta`** — principled delta-method ratio metric (the correct sibling of
  the quarantined post-normed methods).
- **Cross-fitted CUPED/CUPAC** — θ estimated on held-out data (removes the plug-in-θ
  optimism the baseline shares); v2, version-bumped.
- **BCa bootstrap**, **Mann-Whitney**, **cluster-robust SE** (analysis-unit ≠
  randomization-unit) — candidate methods, each one `BaseMethod` class.

### 4.1 Sequential always-valid CIs — as built (M5 WP1)

The always-valid family shipped in M5 is an **asymptotic Gaussian confidence
sequence** (Waudby-Smith & Ramdas 2021 — the Robbins/Howard normal mixture applied
to the estimate), **not** the exact finite-sample Robbins/Howard mSPRT. The reason
is architectural: the pure `abkit.stats` core exposes a per-look `(effect, SE)`
sufficient statistic, not the raw observation stream an exact mSPRT needs (which
would have to be threaded through the core *and* every backend loader). Decision
recorded in [m5-implementation-plan.md](m5-implementation-plan.md) D2.

- **It is a MODE transform, not a method.** `sequential.enabled: true` wraps
  whichever bound method is configured; there is no registry entry and nothing
  special-cases a name (invariant 3). Eligibility is the declarative
  `BaseMethod.supports_sequential` flag (True for the symmetric-normal parametric
  family; **False** for bootstrap — its percentile CI is asymmetric, so the SE is
  not recoverable by CI-inversion).
- **SE by CI-inversion** (`sequential.se_from_ci_length`): every parametric method
  builds `ci_length = 2·norm.ppf(1−α/2)·SE`, so `SE = ci_length / (2·norm.ppf(1−α/2))`.
  This preserves the delta-method covariance already baked into `ci_length`
  (relative / CUPED / ratio-delta) and never re-derives arm variances — the naive
  per-arm rebuild would drop the covariance term and silently miscalibrate.
  **The symmetry premise is enforced, not assumed** (m13 STAT-3a): an interval that
  is not `effect ± z·SE` inverts to the mean half-width over `z` — a finite number
  that is not the SE, which `sequentialize` would then centre a symmetric sequence
  on, with no NaN and no exception. `BaseMethod.asymmetric_ci` (default `False`,
  resolved per bound instance so an interval selected by a *param* can declare it)
  makes every inversion entry raise `AsymmetricCIError` instead. Nothing shipped
  today declares it, so no number moved; a method that wants to stay usable outside
  the sequential mode declares `supports_sequential = False` and is simply left
  fixed. Two functions carry the premise — the helper above and explore's Tier-α
  `_alpha_inverted_bounds`, which open-codes it — and an AST gate
  (`tests/stats/sequential/test_ci_inversion_is_the_only_entry.py`) fails on a third.
- **The interval.** With `V = SE²` and a fixed mixing variance `τ²`, the two-sided
  CS half-width is
  `r = sqrt( (2·V·(V+τ²)/τ²) · ( ln(1/α) + 0.5·ln((V+τ²)/V) ) )`, from inverting the
  normal-mixture likelihood ratio `Λ(θ₀) = sqrt(V/(V+τ²))·exp(τ²(θ̂−θ₀)²/(2V(V+τ²)))`
  (a non-negative martingale under θ₀ ⇒ Ville's inequality ⇒ simultaneous coverage
  ≥ 1−α at every look). The always-valid p-value is the dual `min(1, 1/Λ(0))`, so
  `p ≤ α` iff the interval excludes zero. Always strictly wider than the fixed CI.
- **The mixture variance τ² is fixed-by-policy** (`sequential.mixture_tau2`), anchored
  to the horizon estimator variance: `τ² = u*(α)·V_horizon`, where `u*` solves the
  width-at-horizon stationarity condition `u = 2·ln(1/α) + ln(1+u)` (e.g. `u* ≈ 8.2`
  at α=0.05 ⇒ half-width `sqrt(2·h(u*))·SE ≈ 3.04·SE` at the horizon vs the fixed
  1.96·SE, a ~1.55× anytime price — the honest cost of peeking at any time).
  **Validity holds for any fixed positive τ²** — Ville needs a prior fixed in advance
  — so the choice only sets where the sequence is tightest, and τ² is anchored to the
  design-time horizon, never to the current look. The *numeric* τ² policy is
  **A/A-arbitrated** by the D8 column (WP2): the peeking FPR must return to ≈α at
  acceptable power (measured side-by-side, never asserted — cumulative-intervals §6.5).
- **The guarantee wording:** finite-sample-exact *if* the estimate were exactly
  Gaussian with known V; **asymptotic-anytime** in practice. Never claimed as exact
  mSPRT. The coverage test is a large-n Monte-Carlo within a documented tolerance
  band (`tests/stats/sequential/test_coverage.py`).
- **Change-control.** Default off ⇒ **no existing method's number moves**; no
  `ALGORITHM_VERSION` bump on any registered method; golden tests untouched. The
  transform is not a registered method, so its versioning is this entry + the
  sequential-mode provenance the pipeline persists (a τ²-policy change forces a
  re-plan, D7) — never a silent CI move. `alpha_spending` (group-sequential) is a
  **future item** (no version promise); `scheme: alpha_spending` raises a clean
  config error naming `always_valid` as the supported scheme.

### 4.2 Sub-day sequential-multinomial SRM — as built (M5 WP5)

The SRM gate is **cadence-dispatched** (data-contract-and-reporting.md §6,
cumulative-intervals.md §6.5): daily-and-coarser keep the χ² goodness-of-fit
(§4 bullet 1) — a bounded daily look count on the strict 0.001 hard gate makes
the peeking inflation negligible — while **sub-day** (`cadence < 1d`) swaps to an
**anytime-valid Dirichlet-multinomial e-process** (Lindon & Malek,
*Anytime-Valid Inference for Multinomial Count Data*, NeurIPS 2022,
arXiv:2011.03567 §2.2). A dense sub-day cadence would peek the χ² hard gate dozens
of times a day → false SRM alarms; the e-process is valid at **every** look by
construction. Decision recorded in [m5-implementation-plan.md](m5-implementation-plan.md)
D9; dispatch on `ExperimentConfig.is_sub_day()`.

- **It is an additive GATE, not a registered method** (invariant 3): nothing
  special-cases a name, no `method_config_id`, and — like the χ² gate — it is
  cadence-dispatched by the driver, never selected by config. **No
  `ALGORITHM_VERSION` bump; golden tests untouched.**
- **The e-value.** The null `M0` is iid `Multinomial(1, θ0)` (`θ0 =
  expected_split`); the alternative `M1` mixes `θ ~ Dirichlet(α0)`. By conjugacy
  the Bayes factor at cumulative counts `S = (S₁,…,S_d)` is closed-form and depends
  on the data **only through `S`** (arrival order is irrelevant — so a stream of
  *cumulative* per-variant count vectors is the exact input), computed in log space
  with `gammaln` (never factorials — they overflow at A/B N). With `A0 = Σ α0,ᵢ`,
  `N = Σ Sᵢ`:
  `log BF = gammaln(A0) − gammaln(A0+N) + Σᵢ [ gammaln(α0,ᵢ+Sᵢ) − gammaln(α0,ᵢ) − Sᵢ·log(θ0,ᵢ) ]`.
- **The rejection rule.** `{BFₙ}` is a non-negative martingale under `M0` with
  `BF₀ = 1`, so by Ville's inequality `P(supₙ BFₙ ≥ 1/α) ≤ α` over **any**
  data-dependent look schedule. The per-look verdict is the **running maximum**
  e-value; the anytime p-value is its dual `pₙ = min(1, 1/ supₖ≤ₙ BFₖ)`
  (non-increasing, so once the gate trips it stays tripped). The gate uses the same
  strict `DEFAULT_SRM_ALPHA = 0.001` as χ² (`1/α = 1000`).
- **The prior is fixed-by-policy** (`sequential_multinomial_srm(prior=…)`): the
  default is the paper's **named** default, a **uniform `Dir(1,…,1)`** — no magic
  concentration constant is invented. **Validity holds for ANY fixed positive
  prior** (Ville needs a prior fixed in advance, not tuned to the data) — only the
  stopping time (power) depends on `α0`. A mean-pinned `k·θ0` concentration is
  exposed as an opt-in power knob (unused in M5). A future change to the default
  prior is a change to this entry, never a silent gate move.
- **The guarantee wording:** anytime-valid by construction; the false-alarm
  Monte-Carlo (`tests/stats/test_srm_sequential.py`) is large-n over a
  peek-at-every-look schedule within a Binomial band — mixture e-processes are
  conservative, so the observed FPR sits at/below α, never materially above.
- **False-alarm KAT (pinned to the default prior).** θ0 = ½, uniform `Beta(1,1)`,
  counts `(10, 0)` ⇒ `BF = 1024/11 = 93.0909…` (`log BF = 4.5335765328`; anytime
  `p = 11/1024`). It **does not** trip the strict 0.001 gate (93 < 1000) but does at
  α=0.05 (93 > 20); the general k-variant form is pinned against a direct `gammaln`
  re-derivation at rel-1e-12.
- **Per-cutoff, truthful as-of.** Each look's rows carry **their** look's running
  verdict (`srm_flag`/`srm_pvalue`/`decision_blocked`), stamped from the cumulative
  as-of exposure counts (`get_exposure_count_stream`, reading `_ab_exposures` with an
  exclusive `exposure_ts < end_ts` edge). The readout/report already key off the
  latest row per series, so the reported status is the current anytime verdict. The
  gate runs **even on demoted rows** (counts/SRM stay visible where inference is
  withheld — cumulative-intervals §6.1(4)). No schema change (reuses the existing
  `srm_flag`/`srm_pvalue` columns).

### 4.3 The FWER claim, and Holm — as built (M13 STAT-1)

Two things ship here, and only one of them is code. **No number moved**: `holm`
is a new opt-in enum value, the two-tier Bonferroni levels are byte-identical,
and no `ALGORITHM_VERSION` was bumped (nothing method-level changed).

**(a) What the two-tier scheme actually guarantees** (the blind re-derivation,
[multiplicity.derivation.json](../research/2026-08-m13-blind-rederive/multiplicity.derivation.json)).
Under `correction: bonferroni` abkit tests the main comparison of each declared
arm pair at `α/P` (`P` = the declared pair count) and that pair's `k` secondary
comparisons at `α/(P·k)`. Stated precisely:

- the **main tier** spends a full α **per main comparison**. With the usual ONE
  main metric — the shape every guide writes, and the only shape the blind
  re-derivation considered — that is *"the probability of shipping on a spurious
  main-metric win is ≤ α"*, the claim the ship decision rests on. The config
  permits **several** main comparisons (`is_main_metric` is a per-comparison
  flag; the validator asks only for at least one), and `two_tier_alphas` hands
  each of them `α/P` regardless of how many there are, so with `M` main
  comparisons the main tier spends up to `M·α`;
- the **secondary tier** independently spends a second full α;
- so the **experiment-wide** family error is bounded by `(M+1)·α` — `2α` at the
  single-main-metric default, attained by explicit construction (`1 − e^{−2α}` =
  0.0952 under independence). It is **flat in the arm count and in the number of
  secondary metrics** — the two the operator scales — and linear only in the
  number of comparisons the experiment declares as *main*, i.e. in the number of
  independent ship decisions it is making;
- `guardrail_correction: none` (STAT-1c/D8) adds one more raw-α test per
  guardrail comparison: `+G·P·α`. That is the price of the guardrail flip, and
  it is the intended direction — a guardrail exists to fire.

These are exactly the levels of a **valid** procedure — per arm pair, test the
main comparison at `α/P`, and only if it rejects test that pair's secondaries at
`α/(P·k)` (serial gatekeeping, FWER ≤ α under arbitrary dependence). abkit does
not enforce the gate, and deliberately does not: it would suppress a secondary
metric exactly when it is most diagnostic ("the main metric is flat but retention
dropped" is the reading it forbids), and after D8 it would gate only the
*screening* metrics whose job is to generate hypotheses. So the defect was in
the **claim**, and this entry is the fix; the arithmetic is unchanged.

**(b) `correction: holm` — read-time, opt-in, FWER ≤ α over the whole family.**
`stats.correction.holm_adjusted` computes `adj_(i) = max_{j≤i} (m−j+1)·p_(j)`
over the ascending order (capped at 1); `composed_significance` rejects a member
whose adjusted p is below its **stored raw alpha**, exactly as BH already does —
the two schemes share one body and differ only in the adjuster.

- **It is uniformly more powerful than one-step Bonferroni at the same FWER**,
  under arbitrary dependence, and it is *not* uniformly more powerful than the
  current two-tier scheme: Holm's first step is `α/m` over the whole family,
  where the two-tier main tier is `α/P`. The two-tier scheme buys that looseness
  by spending 2α (see (a)); Holm's α is the honest one.
- **Read-time, and it cannot be anything else.** No fixed per-comparison level
  reproduces a step procedure: with α=0.05, m=2, p₂=0.03, Holm rejects H₂ when
  p₁=0.001 and refuses when p₁=0.9 — same p₂, opposite decisions. The same two
  lines kill Hochberg, Hommel, BH and BY.
- **Fork B (D7) is therefore the milestone's ratified semantics: a verdict and
  the interval stored beside it MAY disagree.** The persisted row carries the raw
  alpha and its own interval; the decision is the family's. This has been live
  under BH since M3 without being written down. The divergence is
  **one-directional** — a family rule is never looser than the member's own raw
  alpha — so what an operator sees is an interval excluding zero under a verdict
  that declines to call it, and `readout.evaluate()` attaches an explicit caveat
  saying so rather than leaving it to be discovered.
- **Three consequences the review of this WP forced, all of them read-time-only
  and all conservative:**
  1. **FLAT is withheld when the pair's own interval excludes zero.** Under
     `none`/`bonferroni`, "no cutoff was significant" *is* "the interval covers
     zero", so FLAT's claim of no meaningful effect follows. Under a family rule
     it does not: a comparison can be refused by the family while its own
     interval sits entirely off zero, and calling FLAT there would read the
     family's refusal as evidence of absence. The readout answers INCONCLUSIVE
     with a rationale saying exactly that.
  2. **FLAT's power story is disclosed as optimistic.** `pair_mde` solves at the
     row's raw alpha; the family threshold is never looser and is `α/m` at worst,
     so "adequately powered" is optimistic by the ratio of the two critical
     values. A caveat says so rather than letting a stop decision inherit a power
     claim from a level nothing was judged at.
  3. **A `guardrail_correction: none` guardrail leaves the read-time family too.**
     D8's declaration has two halves — raw alpha, and out of the divisor — and at
     read time the family IS the divisor. Honouring only the first would have made
     D8 a silent no-op under every read-time scheme while the docs promised it
     loosens the level for the metrics that remain. A guardrail's own decision was
     already correction-independent (D5(c)), so nothing about its flagging changed.
- **A family whose rows carry MIXED alphas is reported loudly.** The composed rule
  compares each member's adjusted p against that member's own stored alpha, so
  such a family is controlled at the LOOSEST of them. It is reachable because
  alpha is outside `method_config_id` (lowering it never re-plans a series, and a
  scoped `abk run --metric … --full-refresh` rewrites one metric's rows at the new
  level), and the readout cannot know which alpha was meant — so it warns and
  names `--full-refresh`.
- **`_ab_results.reject` is a PRE-family flag** (`pvalue < alpha` for that one
  comparison), not the composed decision. It is not renamed — it is a published
  BI contract (data-contract §1, `docs/examples/bi/`) — but every document that
  called it "abkit's composed decision" now says what it is. Under a read-time
  scheme the decision exists only at read time, which is why it is not persisted:
  a stored copy would go stale the moment the family changed.
- **A/A arbitration**: `abk validate --family-sweep` measures the composed family
  FWER/FDR and anchors Holm's budget at the members' level (α), like BH — under
  Holm a family measuring ≈Σα means the *methods* are miscalibrated, which is
  what the sweep exists to catch.

### 4.4 The proportion interval: `interval: score` — as built (M13 STAT-3)

**No p-value moves and no default moves.** `interval` is a new identity-flagged
param on `z-test` whose default (`pooled`) is the legacy branch, byte-for-byte;
`ALGORITHM_VERSION` is untouched (D4 — an opt-in param orphans the series of the
operator who opts in, at the moment they opt in, which is the whole signal).

**What was wrong.** The z-test's p-value comes from the *pooled* (null) variance
and so did its CI (`ztest.py`). That is what made "the CI excludes zero" and
"p < α" agree exactly — and `pipeline/readout.py` decides significance by CI
exclusion — but it makes the interval a valid confidence set at **zero only**.
The damage is not uniform: an SE mis-scaled by `r` inflates the achieved error
rate by `(1/r)·exp(z²(1−r²)/2)` (the derivation's master law; the figures below are
the exact tail ratio `Φ̄(zr)/Φ̄(z)` it approximates), so at a 900/100 holdout
(`r = 0.764`, i.e. the pooled SE is 24% too *small* — pooling is not the conservative choice it is widely
believed to be) that is **2.7× at α = 0.05, 7.0× at α = 0.004, 30× at α = 1e-4**.
It worsens exactly as the multiple-testing correction shrinks α, so the two
defects compound.

**What ships.** The blind re-derivation
([proportion-interval.derivation.json](../research/2026-08-m13-blind-rederive/proportion-interval.derivation.json))
dissolves the pooled-vs-unpooled fork instead of choosing a side: define the
score statistic with constrained-ML variance once and use it three ways.

| Use | Form | Consequence |
|---|---|---|
| p-value | `2(1 − Φ(\|Z(0)\|))` | **identically** the pooled z — the reported p does not move |
| absolute CI | `{δ : Z(δ)² ≤ z²}` | valid at *every* δ, asymmetric, inside `[−1, 1]` |
| relative CI | the same construction on the ratio scale | `Z(1)` is again the same pooled `Z` |

So coherence is preserved **by construction**, on both scales at once: "the
difference interval excludes 0", "the lift interval excludes 0" and "p < α" are
one event at every α and every sample size, with no asymptotics. Replacing the
CI's SE with the unpooled one — the ROADMAP's original contour item — would have
bought validity by breaking that, and the readout has no way to express the
disagreement.

**Sub-decisions, all of them recorded rather than assumed:**

- **The MN `N/(N−1)` factor is DROPPED** (D11 — the Farrington–Manning form).
  Applied to the interval alone it breaks the coherence above at a relative
  distance `1/(2N)` from the boundary; applied to both it moves every printed
  p-value by that much. Dropping it makes `Z(0)` *the same computation*, so the
  p-value branch is literally untouched code. It must never be added to one side
  only.
- **Boundary tables stop being special cases.** `x₁ = x₂ = 0` — the first cutoff
  of any sparse metric — returns the Wilson zero bound `±z²/(n+z²)` beside
  `p = 1`, where the pooled path returns a **NaN p and NaN bounds**: a row no
  reader can act on. (The textbook Wald interval returns `[0, 0]` there, an
  assertion of infinite precision from a table with no information; abkit's guarded
  branch never did, and the spec should not credit it with that defect.) Under
  `test_type: relative` the H5 refusal **stands**: a lift over a zero baseline is
  undefined whatever the interval method. This is the ONE table where a p-value
  moves, and it moves from "absent" to 1.
- **A newly-informative degenerate row joins a READ-TIME family.** That same table
  used to carry NULL bounds, so `readout._informative` skipped it; under `score` it
  is a real row and enters the BH/Holm family, whose threshold every other metric is
  then judged against. The direction is conservative (the comparison WAS tested;
  excluding tested hypotheses is the anti-conservative error), but it means flipping
  `interval` on one metric can move another metric's verdict — a cross-metric
  consequence of a per-metric knob, stated here because "no p-value moves" would
  otherwise be read as "nothing else moves".
- **The relative scale is the score construction on the ratio scale (Route C)**,
  not the difference interval divided by `p̂₁` (which omits the denominator's
  sampling error, can return a lift below −100%, and inverts no test) and not a
  delta-method log-RR interval (coherent with neither the difference interval nor
  the p-value). Route C is also exactly equivariant under swapping the arms.
- **A weak-identification WARNING, not a suppression.** The relative half-width
  is `z·√(1/x₁ + 1/x₂)` — a law in **conversions**, not units: ten times the
  traffic at a tenth of the rate buys nothing. Above ±50% the row carries a
  warning naming the conversions it would take. Suppressing a correct interval
  would be the worse failure; and the threshold carries `z`, so it tightens by
  itself as the correction shrinks α.
- **The always-valid mode is unavailable under `interval: score`** (§6a's stated
  fallback). The confidence sequence *does* extend to score intervals — by
  substituting `c(V)` for `z` inside the root-find — but `to_always_valid` cannot
  express that: it widens a finished interval, recovering an SE the method does
  not have. Declaring both is now a **level-2 config error** naming both knobs,
  refused identically at the explore knob and at the explore Apply seam (STAT-3a's
  `AsymmetricCIError` remains the backstop under all three).
- **STAT-3a's A/A behaviour is AMENDED here.** It shipped "an asymmetric method's
  cell fails, carrying its reason", which was right while no method could declare the
  flag and wrong the moment one can: `_cell_tau2` runs unconditionally at the top of
  both scoring engines, so that refusal failed EVERY cell — leaving `abk validate`
  unable to measure the estimator the change-control process invokes it to certify,
  and explore's D3 calibration chip permanently `uncalibrated` with no command able to
  clear it. The cell now scores its fixed columns and simply has no always-valid
  column, exactly as a bootstrap method does, with a note naming that reason instead
  of "τ² could not be anchored".
- **`abk plan` sizes on the normal power formula** while the analysis inverts the
  score statistic, so a stated MDE does not exactly invert the applied rule
  (§6b). Measured rather than assumed: the half-widths differ by
  `C·z²/n_arm` with `C` stable in `n` to three significant digits — **4.01 at a
  5% baseline, 0.0595 at 30%**, i.e. 0.15% of the half-width at 10k units per arm
  and 0.015% at 100k. Ignorable, and `abk plan` says so on the comparison.
- **A/A arbitration is DELIBERATELY NOT the referee here** (§0.4, and the
  derivation's own warning): the FPR difference between the two rules is
  second-order — the tails shift in opposite directions and largely cancel — so
  the matrix would report "still calibrated" while persisted verdicts on live
  imbalanced experiments had already moved. *A calibration that cannot see the
  change it is being asked to certify is worse than no calibration.* What
  arbitrates instead is the coherence identity above, checked exhaustively, plus
  the closed-form boundary answers.

### 4.5 The relative interval: `interval: fieller` — as built (M13 STAT-4)

**No default moves.** `interval` is a new identity-flagged param on the five
mean-based closed-form methods (`t-test`, `cuped-t-test`, `paired-t-test`,
`paired-cuped-t-test`, `ratio-delta`), default `delta` = today's branch
byte-for-byte; `ALGORITHM_VERSION` is untouched (D4). `z-test` is deliberately
NOT among them — §4.4's ratio-scale score construction is the exact analogue for
proportions, and Fieller would be a normal-theory approximation of it.

**What was wrong, stated at the granularity abkit's verdicts live at.** The
relative branch reports `θ̂ ± z·SÊ` with the variance evaluated at the ESTIMATE
(a Wald interval; §0.2(b) shows the shortcut/delta difference is the `R²`
coefficient on `V₁`, not a covariance). Two consequences, and the second is the
one that matters:

- **It is a different test from the p-value printed beside it.** Wald statistics
  are not invariant to reparametrisation, so "θ = 0" and "μ₂ − μ₁ = 0" — one
  hypothesis — get two p-values, and a report can carry "the absolute effect is
  significant" next to "the lift CI contains 0".
- **Its two-sided coverage is nominal while its tails are not.** Measured over
  200k draws from the exact normal model, at CV₁ (the CV of the control MEAN)
  = 0.05: total miss 0.0495, split **0.0168 / 0.0327**; at CV₁ = 0.10: 0.0475,
  split **0.0083 / 0.0393**. A WIN or a LOSE is a **one-sided** claim, so the
  error rate an abkit verdict actually runs at is up to 1.6× the one bought. The
  imbalance is a property of the denominator's noise, not of the effect —
  identical at θ = 0 and θ = +0.5 — which is why an A/A run measures the live
  experiment's error faithfully and still reports "calibrated".

**What ships.** The blind re-derivation
([relative-effect.derivation.json](../research/2026-08-m13-blind-rederive/relative-effect.derivation.json))
recommends Fieller, and D10 adopted it. It inverts the same statistic at every
candidate lift instead of only at the estimate:

    { θ : (a − θ·b)² ≤ z²·(V_a − 2θ·V_ab + θ²·V_b) }

with `a` the numerator effect, `b` the control mean and `V_ab = Cov(a, b)` — the
five moments `relative_delta_effect` already takes, so CUPED (whose numerator is
adjusted and whose denominator is not) is covered by the same code rather than
by a special case. Endpoints are the roots of `A·θ² − 2B·θ + C ≤ 0`.

| Property | Delta (default) | Fieller (opt-in) |
|---|---|---|
| p-value for θ = 0 | its own Wald p | **the absolute comparison's, bit-for-bit** |
| interval excludes 0 ⟺ p < α | not guaranteed | **by construction** |
| one-sided error rates | 0.017 / 0.033 at CV₁ = 0.05 | **0.025 / 0.025 at every CV₁ ≤ 0.10** |
| A/A false-positive RATE | 0.0498 | 0.0499 — the column is blind (§0.4) |
| A/A false-positive SIGN split | 0.66 at CV₁ = 0.05 | **0.50** |
| near-zero control mean | always a finite interval | declines, and says why |

**The sub-decisions, recorded rather than assumed:**

- **The p-value moves, and that is the point.** Under `fieller` the relative
  p-value IS the absolute test's — same expression, same operand order, asserted
  with `==` across all five methods. Keeping the Wald p beside an inverted-test
  interval would have rebuilt the incoherence the change exists to remove. (The
  ⟺ is algebraic, not bit-wise: `a² > z²V_a` and `|a| > z√V_a` are the same
  comparison in two roundings, so a table within an ULP of the critical value may
  be answered differently by each — §4.4's caveat, unchanged.)
- **The point estimate is untouched.** Fieller's centre is shifted by
  `R̂·g/(1−g)`, `g = z²V̂_b/b²`, but that shift belongs to the confidence set's
  geometry, not to the estimator; abkit reports the same lift it always did and
  moves only the bounds. (Nor is it a bias correction — it moves the same way
  the O(CV₁²) bias does.)
- **The unbounded branch is reported as MISSING BOUNDS, not as a wide interval.**
  When `g ≥ 1` — the control mean is not distinguishable from zero at this α — no
  bounded confidence set for a ratio exists at level 1−α. Gleser & Hwang (1987):
  any procedure with guaranteed coverage must produce unbounded sets with
  positive probability, so delta's always-finite interval has guaranteed coverage
  **zero**. abkit reports the effect and the p-value with NULL bounds, which
  `readout._informative` already treats as a gap rather than a zero. The cost is
  stated: a comparison whose absolute test rejects while its relative interval is
  unbounded loses a WIN it would have been given under `delta`, on evidence that
  could not support a lift figure anyway. Measured share of unbounded answers: 0%
  at CV₁ ≤ 0.10, 8.5% at 0.30.
- **A DISCLOSED limitation the unbounded branch creates.** An unbounded row is
  the first row in the project's history to carry a **valid p-value with NULL
  bounds** — before STAT-4 the two were always NULL together. `readout._informative`
  keys on the bounds, so such a row is skipped: correct under a compute-time
  correction (it cannot exclude zero), but under a **read-time** scheme (BH/Holm)
  it also leaves the family, which shrinks `m` for its siblings — the
  anti-conservative direction. It is pinned as behaviour rather than fixed here,
  because relaxing `_informative` is a readout-wide semantics change (the
  stabilization scan reads the same predicate) and belongs to STAT-6, not to the
  estimator that made it reachable.
- **An EMPTY confidence set is a distinct sentence.** Reachable only through a
  non-PSD moment triple (`V_ab² > V_aV_b`), which abkit's mixed-ddof convention
  can produce on adversarial data — the same anomaly the delta branch reports as
  a negative variance. Five causes of missing bounds, five messages: a reader
  told "near-zero control mean" about a zero-variance table looks at the wrong
  half of their data.
- **`interval: fieller` beside `test_type: absolute` is REFUSED**, not ignored.
  It would compute nothing and still fork `method_config_id`, splitting a
  published series for no numeric reason. Declared on the `ParamSpec`
  (`relative_only`) and enforced once in `BaseMethod`, so a sixth adopter cannot
  reintroduce it.
- **The interval is asymmetric, so `asymmetric_ci` is True on the bound
  instance** and every STAT-3a consequence follows with no new surface code: the
  always-valid mode is a level-2 config error naming both knobs, `abk validate`
  scores the fixed columns and omits the sequential one, explore's α tier answers
  with a gap, and the `±CI` chip renders `[low, high]`. STAT-3 resolved that flag
  in `ZTest.__init__`; STAT-4 moves the resolution onto the `ParamSpec`
  (`asymmetric_values`) — two param-switched intervals is where a per-class
  resolution starts to rot.
- **`abk plan`'s sizing is closer under Fieller than under the default, and the
  note says less than it used to.** `get_ttest_mde`'s relative branch sizes the
  ABSOLUTE difference and divides by the control mean — the null-variance rule,
  which is exactly Fieller's own rejection boundary. So the planner and the
  analysis rule have disagreed under `delta` all along, and opting in closes that
  gap rather than opening one. The comparison note therefore claims a difference
  in **half-widths** (`O(z²/N)`, true for both asymmetric intervals) instead of
  "the two rules differ", which was never true for Fieller.
- **A/A arbitration is again NOT the referee** (§0.4, D6), and this is the case
  the plan predicted: the rejection sets at the null are *algebraically*
  different but their measured rates agree to the third decimal. The instrument
  that can tell them apart is STAT-2's `fpr_negative_share`, which reads 0.50 for
  Fieller against the derivation's predicted `0.5 + φ(z)z²·CV₁√w₁/α` for delta
  (0.66 at CV₁ = 0.05, measured 0.664).

## 5. CUPED covariate window — DECIDED: fixed lookback (2026-07)

The legacy CUPED covariate uses a **growing** symmetric pre-window. The choice was
(a) reproduce the growing window (baseline-faithful) vs (b) a **fixed** lookback
(e.g. `14d`) as a documented deviation — arguably *more* correct (a stationary
covariate across the series).

**Resolution: (b) — `covariate_lookback` is a fixed duration in whole days,
independent of cadence.** The tiebreaker was sub-day cadence support
(cumulative-intervals.md §6): the growing rule `agg_dates_count = end − start + 1`
is incoherent below a day (fractional lookbacks; a diurnally-confounded,
hour-jittering covariate; θ instability at small n destroys the variance
reduction CUPED exists for). Consequences:
- the default config path will NOT reproduce the legacy CUPED number over the
  daily series — this is the documented, version-recorded deviation;
- an (a)-mode growing-window reproduction exists only inside legacy-parity golden
  fixtures, never as user config;
- config-lint: `covariate_lookback < 1d` → error; `< 7d` → warning (shorter than
  one weekly cycle — diurnal/weekday confounding erodes the covariate
  correlation);
- the scaffolded example metric uses the fixed lookback (no silent mismatch).

## 6. What stays exactly as the baseline (the "do not drift" defaults, v1)

Delta-method relative variance with the negative covariance term; pooled-θ CUPED
dividing by the original control mean; percentile bootstrap CI; sign-based bootstrap
p-value; effect computed on real data; config-time two-tier Bonferroni; the mixed
per-term ddof; **the z-test's pooled-SE interval** (§4.4's `interval: pooled`, the
default — the score form is opt-in and never silently substituted). These are
golden-tested against the legacy engine and only change via the §0 process.

## 7. M1 implementation record (`abkit.stats` v0.1, ALGORITHM_VERSION=1)

The M1 engine reproduces the baseline at rel-1e-9 (golden-tested against an
independent transcription of the legacy formulas written from the catalogue only)
with these documented, deliberate exceptions — each per the sections above:

1. **H1/H2 applied.** All randomness flows from one injected
   `np.random.default_rng` Generator; bootstrap re-runs are byte-stable given a
   seed; `seed`/`max_block_bytes` are identity-excluded for all bootstrap methods.
   Distributionally equivalent to the baseline (which re-seeded per run).
2. **H4 available, NOT the default.** Bootstrap methods default to the
   baseline sign-based p-value (`pvalue_kind: sign` — §2/§6 discipline:
   defaults stay baseline-faithful). The `(#extreme+1)/(n+1)` plug-in ships as
   the opt-in, identity-bearing `pvalue_kind: plugin` (ties at 0 counted as
   extreme on both sides, capped at 1); promoting it to the default awaits the
   M4 A/A arbitration and is an `ALGORITHM_VERSION` bump.
3. **H3/H10 applied (numbers unchanged).** Mean/median fast paths; replicates are
   drawn in fixed 128-replicate quanta so the memory cap can never change results.
4. **H5 applied.** Zero/near-zero control denominators yield NaN + a recorded
   warning (never a raise, never silent ±inf). Corner deviations vs the legacy,
   all NaN-voided for consistency with the engine-wide H5 convention:
   (a) z-test relative with `prop_1 == 0` — legacy produced a finite pooled-z
   p-value alongside an infinite effect; (b) z-test with a degenerate pooled
   proportion (both arms all-0 or all-1) — legacy produced zero-width bounds
   `[effect, effect]` from `std_effect = 0`; the M1 engine reports NaN bounds
   (a zero-width CI from zero estimated variance is false certainty).
5. **H6 applied.** Stratified resample counts use largest-remainder (Hamilton)
   apportionment (with a min-1 floor bump), replacing the legacy
   `max(1, int(...))` truncation drift. Sanctioned by the quorum blocking
   must-fix (see §2 exception); golden-tested on strata where both agree.
6. **H7 applied.** Poisson bootstrap methods reject `stat != "mean"` at
   construction.
7. **H8 applied.** The uncalibrated per-comparison KS normality warning is
   dropped.
8. **H9 applied.** `effect` is the real-data point estimate everywhere;
   `diagnostics["boot_mean"]` carries the bootstrap mean as a bias diagnostic.
   Sole exception: `paired-post-normed-bootstrap` (absolute) has no real-data
   analog of its standardized estimand, so it keeps `effect = mean(boot_data)`
   with a warning recommending `post-normed-bootstrap` / `ratio-delta`.
9. **§3 quarantine enforced.** `poisson-post-normed-bootstrap` is blocked at the
   registry; `post-normed-bootstrap` `test_type=absolute` and
   `paired-post-normed-bootstrap` `test_type=relative` raise
   `QuarantinedMethodError` at construction. `ratio-delta` ships as the
   principled alternative (ddof=0 uniformly; known-answer: reduces exactly to
   `t-test` when the denominator ≡ 1).
10. **Named statistics replace raw `stat_func` callables.** The legacy bootstrap
    accepted arbitrary callables; abkit accepts registered NAMES
    (`stat: "mean" | "median" | <register_stat(...)>`) so the statistic stays
    part of the hashable, BI-stable method identity. Custom stats (e.g. a p90
    quantile) are one `register_stat("p90", fn)` call away; rebinding an
    existing name to a different function is refused (it would silently change
    published numbers).
11. **Degenerate inputs raise early instead of emitting NaN rows.** `n < 2`
    with a covariate (or a single paired pair) raises `SampleValidationError`
    where the legacy emitted an all-NaN result row (`np.cov` of one unit is
    NaN). The pipeline (M2) catches per-comparison validation errors and
    surfaces them as voided rows — the stats core itself never manufactures a
    meaningless row.
12. **Paired strata must travel with the pair.** Paired bootstrap methods
    require elementwise-identical `categories_array` on both arms; the legacy
    silently resampled per-arm strata with a shared seed, which breaks the
    pairing whenever strata differ — that input is now a hard validation error.

## 8. Canonical unit order in the loaders (M3 D11 — determinism note, no version bump)

**Decision (2026-07, m3-implementation-plan.md D11):** `load_metric` sorts every
variant's per-unit arrays (units, all role columns, strata) by unit key after
fetch, and the explore session cache preserves that order.

Why: bootstrap replicates are order-dependent by construction (resample indices
index the per-unit array), and the loader previously kept warehouse result-set
order — which ClickHouse does not guarantee. The M2 "byte-stable re-run" and the
M3 "unchanged knobs reproduce persisted rows" claims therefore only held on
order-deterministic backends (fake_db, the seed dataset).

This is a **pipeline-level input-assembly fix, not a method change**: identical
`Sample` inputs still produce identical outputs, so no `ALGORITHM_VERSION` bump.
It is still recorded here (never change a number silently, even at the
assembly layer): bootstrap rows persisted **before** the sort may differ from
re-computed ones on backends that happened to return a different physical
order. Closed-form methods are order-invariant and unaffected.

## 9. The M9 additive read path — schema and assembly, not statistics (no version bump)

**Decision (2026-07, m9-implementation-plan.md §0.1, delivered WP1–WP6):** M9
adds a second *route to the same inputs* — the STATE stage materializes
per-(unit, day) moments into `_ab_unit_state` and, with the opt-in
`compute.incremental_reads: true`, `IncrementalBackend` sums those closed days
(plus a live sub-day tail) instead of re-scanning the whole cumulative window.
Nothing downstream of the loader changes: the per-unit totals feed the SAME
`MetricLoadResult → build_container → SufficientStats → method` path. **No
`ALGORITHM_VERSION` moved in the milestone** (grep-checkable in the diff), and
the golden suite pins the baseline untouched.

It is recorded here for the same reason §8 is — the assembly layer can move a
number even when no formula does:

- **The parity tolerance is relative 1e-9, never byte equality.** Summing
  eleven per-day partial sums and scanning the eleven days in one pass are
  mathematically identical and floating-point *different* (they associate the
  additions differently); the observed gap on the scaffolded example is ~1 ULP
  (e.g. a CUPED `θ` of `0.9078684646492412` vs `…13`). Demanding byte equality
  across the two paths would be demanding a property IEEE-754 does not offer —
  the same lesson M7 recorded for GEMM blocking. Discrete outputs (unit counts,
  sizes, `reject`, warnings, identity hashes) stay **exact** in both paths, and
  the gates assert them exactly.
- **Which numbers a re-read can legitimately move:** none, within that
  tolerance — with one documented exception inherited from the day-state
  contract. An event backfilled into an already-materialized day *later than
  `data_lag`* is frozen in that day's moments, so the incremental path keeps
  reporting the pre-backfill number while recompute would pick it up.
  `data_lag` is the declared SLA (cumulative-intervals §6.2); `abk
  verify-incremental` is the detector (whole-series, rel-1e-9, non-zero exit)
  and `abk run --full-refresh --from/--to` is the recovery.
- **Bootstrap and stratified comparisons never take the additive path** —
  seeded resampling needs the per-unit arrays and Hamilton apportionment needs
  the strata, so those comparisons stay on full recompute regardless of the
  flag. A method's byte-stable seed contract (`derive_seed` from row identity)
  is therefore untouched.
- **Eligibility is a metric-author declaration** (`state_additive: true`), not
  an inference from SQL text: per-day partials add up to the window total only
  if every projected role column is a day-additive aggregate, and that cannot
  be read off the query (m9 WP5 — three review rounds each defeated a textual
  check with a new shape). The text check that remains is veto-only, and
  `verify-incremental` is the empirical oracle.
