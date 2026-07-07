# abkit.stats: dependencies, efficiency, maintainability + improvement backlog

**Verdict up front.** The stats core is **already in genuinely good shape** — both as
engineering and methodology. Deps are minimal and every library earns its place; the
bootstrap engine is truly vectorized over units; all distribution math is delegated to
scipy; the plugin registry and `method_config_id` are locked down. There are **no
correctness bugs and no hot-path efficiency debt hiding**. The upside splits cleanly:
(1) a large **byte-identical latency win** on the closed-form path, and (2) a cluster of
**honest, versioned statistical upgrades** where the legacy "worked but isn't necessarily
right everywhere." The mixed-ddof / sign-p / normal-approx choices are deliberate captured
baseline — not accidents to "fix."

Two independent sweeps back this: a conservative "as-built" audit (deps/efficiency/
maintainability) and an ambitious forward-looking one (statistician + numerics + ecosystem
lenses). Findings de-duplicated below.

---

## 1. Current state

### Dependencies — minimal, appropriate, correctly fenced
Exactly **numpy + scipy + statsmodels + stdlib**; the purity invariant holds in practice
(a subprocess import test forbids click/jinja2/orjson/pydantic/requests/yaml —
[test_purity.py:37-44](../../../tests/stats/test_purity.py#L37)); sole intra-dep is
stdlib-only `json_utils`; no direct pandas import anywhere.
- **numpy** — load-bearing, correct (data model, Chan accumulation, bootstrap engine).
- **scipy** — minimal: `stats.norm`, `stats.chisquare`, `special.gammaln`, `optimize.brentq`
  — all ancient, stable APIs.
- **statsmodels** — the one to scrutinise: confined to `power.py` (three power/MDE helpers)
  but reachable on the compute hot path when `calculate_mde=True`; load-bearing by baseline
  fidelity (legacy used `solve_power`, golden-pinned), and memoized with
  `@lru_cache(maxsize=4096)` ([power.py:45-73](../../../abkit/stats/power.py#L45)). Its
  transitive footprint (pandas + patsy) is disproportionate to a three-helper usage — see
  A2/C3.
- **Pins** are floors-only, no upper caps (pyproject.toml:30-32) — correct for a library;
  the golden suite in CI is the right guard against a future silent drift.

### Efficiency — genuinely vectorized, no hot-path debt
- **No Python loop over units anywhere** in the bootstrap engine — the unit-level work is a
  single `rng.integers` draw + one fancy-index copy per stratum, hoisted out of the quantum
  loop ([engine.py:285-312](../../../abkit/stats/bootstrap/engine.py#L285)). The stat fast
  path is vectorized (mean→`matrix.mean(axis=1)`; the legacy `apply_along_axis` row loop
  survives only as the fallback for custom callables — applier.py:71-81).
- **No `n_samples × n_units` matrix materialized** — the index engine streams
  memory-bounded blocks (`max_block_bytes`, default 256 MiB); the Poisson engine works one
  128-row quantum at a time via a single BLAS matmul reusing one preallocated buffer.
- **Modern RNG** — `np.random.default_rng` (PCG64), one Generator per `compare` call, never
  the legacy global `np.random.seed`; byte-stability via a deterministic derived 63-bit seed
  (rng.py:24-39).
- **Parametric path never hand-rolls a distribution** — every p-value/CI quantile delegates
  to `scipy.stats.norm` (effects.py:127-131).

### Maintainability — strong core, minor test-leverage gaps
- **Bootstrap family is exemplary DRY** — `BaseBootstrapMethod._finalize` is the single
  `TestResult` construction for all 6 methods.
- **`method_config_id`** — a pure function pinned by literal-byte + pinned-hex tests, with
  identity membership driven off `ParamSpec.identity` (base.py:207-226, test_identity.py).
- **Registry** — kebab enforced, alias/quarantine guards, reload-idempotent.
- Nits: the parametric family has no `_finalize` equivalent (6 near-identical ~20-kwarg
  `TestResult(...)` tails → field-drift risk); universal contracts (dual-entry, seed-exclusion,
  `to_dict`, quarantine) are tested via hardcoded enumerations not driven off the registry;
  registration is an import side-effect with no completeness test.

---

## 2. [A] Safe wins — byte-identical, no version bump

| # | Value | Effort | Risk | file:line |
|---|---|---|---|---|
| A1 | Replace frozen `norm.ppf/.cdf/.sf` with `scipy.special.ndtri/ndtr` on the closed-form path → **~60×**, bit-for-bit identical (use `ndtr(-z)` for `sf` to keep tail bit-parity) | S | low | effects.py:127-131; ztest.py:63,85 |
| A2 | **Lazy-import statsmodels** inside `power.py` → stop eagerly loading statsmodels+pandas+patsy (~100 ms) on every `import abkit.stats`; honors the "no pandas in core" pledge on the no-MDE path | S | low | power.py:22-23 |
| A3 | Lazily build the **never-consumed `TestResult.effect_distribution`** (dropped in `to_dict`, no reader) → removes ~188 µs/call; with A1 → **~250×** on the `abk validate`/`explore` hot path (preserve the `is not None` contract via a proxy/`cached_property`) | M | med | effects.py:127; result.py:55,73 |
| A4 | Dedup double `stat_point` recompute + two-pass sign scan in bootstrap result assembly (honestly <1% of a bootstrap call — tidy, not a lever) | S | low | bootstrap.py:306-310,253-254 |
| A5 | Registry-parametrize the method-contract test suites (dual-entry, seed-exclusion, `to_dict`, quarantine) so any new plugin is auto-swept in | M | low | test_bootstrap_methods.py:25-42; test_identity.py |
| A6 | Add a **registry-completeness test** (every `BaseMethod` subclass reachable via `available_methods()`) — closes the "forgotten import silently un-registers" gap | S | low | parametric/__init__.py; stats/__init__.py |
| A7 | Add a shared `_result_from_normal_test` helper on `BaseMethod` (mirror the bootstrap `_finalize`) → removes ~50 lines of copy-paste + field-drift risk across 6 methods | M | low | ttest.py:81-105 (+5 siblings) |
| A8 | Micro-dedup: doubled `np.mean` in `RatioSufficientStats.from_ratio_sample`; reuse `sample.cov_mean` in `SufficientStats.from_sample`; add `m2_num/m2_den ≥ 0` validation parity | S | low | samples.py:296,388-408 |

**A1+A3 are the standout** (they subsume the statistician lens's "avoid the per-comparison
frozen norm" — the cost is in the *constructor* ~188 µs, not just `.ppf` ~63 µs). Dead ends
(benchmarked slower — do **not** pursue): batching the Poisson engine by `max_block_bytes`
(0.8×), the multinomial-count `counts@values/n` reformulation (0.14-0.25×), `np.take(out=)`
(0.65-0.80×), and swapping BH onto statsmodels (1-ULP no-op that could flip a borderline p).

---

## 3. [B] Versioned statistical improvements — ALGORITHM_VERSION + statistics-changes.md + A/A revalidation

The legacy "worked but isn't necessarily correct everywhere" — these are conscious
deviations; golden keeps the old baseline pinned in parallel, each ships opt-in
(identity-bearing `ci_kind`/`dist`/`correction`) so the default only changes once A/A
greenlights it.

| # | Improvement | Risk | Effort | file:line |
|---|---|---|---|---|
| B1 | **Holm (step-down) over Bonferroni** — strictly more true rejections at the *same* FWER guarantee (a free power gain); via `statsmodels.multitest`, leave BH untouched | low | M | correction.py:29-33,97-141 |
| B2 | z-test builds the CI from the **unpooled SE** (pooled SE stays for the test statistic) → nominal coverage on the flagship default fraction/main path | med | M | ztest.py:56-58,83-88 |
| B3 | z-test relative branch **restores the delta-method covariance term** the t-test already keeps (num & denom share the control arm) — currently under-states the relative CI | low | S | ztest.py:71-82 |
| B4 | **Uniform ddof=1** (unbiased) variance → removes the O(1/n) SE down-bias and the incoherent ddof=0/ddof=1 mix inside one delta-method expression | low | S | samples.py:67,315-317,344-350 |
| B5 | Opt-in **Welch–Satterthwaite Student-t** reference dist → calibrated small-n / per-segment coverage (t→z at large N) | low | M | effects.py:94-141 |
| B6 | Proportion CI → **Agresti-Caffo / Wilson-score** → near-nominal coverage for low-rate funnels and early cutoffs (Wald under-covers near 0/1) | med | M | ztest.py:83-88 |
| B7 | Opt-in **BCa** (or studentized) bootstrap CI → second-order-accurate coverage for heavy-tailed revenue + quantile/median stats (percentile is first-order only) | med | M | ci.py:43-46 |
| B8 | Opt-in **cross-fitted CUPED θ** → removes in-sample optimism (θ fit on the arms it adjusts) so the reduced-variance CI is honest; generalizes to CUPAC | low | M | cuped_ttest.py:101-118 |
| B9 | **Main-tier `metrics_count=1` FWER fix** — multiple *main* comparisons each get `α/(C(N,2)×1)`, so the main budget is not shared → FWER inflation across multiple main metrics | low | S | correction.py:65; analyze.py:72 |

**Clusters that can ride one version bump each:** {B2, B3, B6} = the z-test/proportion-CI
default decision path; {B4, B5} = the small-n parametric pair (they compound). B1 and B9 are
correction.py and could go together.

---

## 4. [C] Efficiency / architecture bets

| # | Bet | Δnums | Risk | Effort | file:line |
|---|---|---|---|---|---|
| C1 | Wire the **incremental Chan-merge cumulative recompute** → the DB re-scan drops O(cutoffs×window) → O(total). The real dominant real-world cost for long experiments (lives in `compute/`, not stats; already a documented v2 deferral behind `abk verify-incremental`; needs a version guard for float-summation-order ULP drift) | ULP | high | L | recompute_backend.py; accumulate.py |
| C2 | **ratio-delta cluster guard** — the delta-method SE is only cluster-robust if the RatioSample row *is* the randomization unit; add a guard (byte-identical) + an optional cluster-aware variant (the highest-risk metric class for silent SE under-estimation) | guard: N | low | M | ratio_delta.py:34-54 |
| C3 | **Drop statsmodels** (thus pandas+patsy) — reimplement the ~50-line power surface on scipy (`nct`/`norm` + `brentq`, already a dep; number-changing → version bump), *or* gate it behind an optional `[power]` extra (byte-identical, packaging only) | reimpl: Y / extra: N | med | L | power.py; pyproject.toml |
| C4 | Steer mean-only poisson/index bootstrap toward the **closed-form fast path** (a warning, or an opt-in analytic path) → up to ~1000× for that common misuse | guidance: N | low | M | poisson_bootstrap.py:36-75 |
| C5 | Bootstrap bit generator **PCG64 → SFC64** — the *only* real bootstrap speedup (~1.2-1.6×, RNG-draw-bound); honestly a modest ceiling | Y | med | M | rng.py:37-39 |

---

## 5. Locked by the golden baseline — do NOT "fix"

Each would fail the rel-1e-9 golden tests and violate "never change a number silently."
Fidelity, not debt (changing any is legitimate but is a deliberate §3-style versioned move,
never a casual cleanup):
- **Mixed-ddof convention** (variance ddof=0, CUPED-θ / relative covariance ddof=1) — encoded
  per-term via named accessors (samples.py:220-244).
- **t/cuped/paired use the Normal distribution**, not Student's-t (deliberate large-sample
  z-approximation — effects.py:127). *(B5 offers this as an opt-in, not a silent swap.)*
- **Sign p-value** `2·min(P>0,P<0)` and **`boot_std` ddof=0** in the bootstrap finalize
  (ci.py:51, bootstrap.py:226).
- **Z-test quirks** — statistic uses `prop_1−prop_2` while the reported effect uses
  `prop_2−prop_1` (harmless, p symmetric); pooled-SE CI; relative branch without the
  covariance term (ztest.py:1-13). *(B2/B3/B6 are the sanctioned versioned fixes.)*
- **`relative_delta_effect` high powers of `mean_den`** — byte-locked; the near-zero-mean
  danger is guarded (NaN + warning, H5), not refactored (effects.py:58-77).
- **The 128-row quantum loop is a determinism lock, not slack** — `max_block_bytes` must
  never perturb the random stream; the quantum draw order is what shares the identical stream
  with the golden legacy transcription (engine.py:1-41).
- **Quarantined legacy-broken branches** raise `QuarantinedMethodError` at construction — a
  fail-loud correctness *feature* (registry.py:21-27).

---

## 6. Do-first recommendation

1. **A1** — `ndtri/ndtr` swap. Byte-identical, ~60× on the closed-form path, verify tail
   bit-parity against the golden fixtures.
2. **A2** — lazy statsmodels import. Trivial, ~100 ms off every `import abkit.stats`.
3. **A3** — lazy `effect_distribution`. Compounds A1 to ~250× on the validate/explore hot
   path; ship behind a proxy that preserves the `is not None` contract.
4. **B1** — Holm over Bonferroni. The one number-changing item that is a *strict* win (more
   power at the identical FWER guarantee); first A/A revalidation candidate.
5. **B3 + B4** — restore the relative-z covariance term + uniform ddof. Both cheap (S), both
   genuine-correctness, and both anti-conservative-at-small-n so they compound — the
   low-risk correctness pair before the medium-risk proportion-CI overhaul (B2/B6).

All of A1-A8 are safe for a 0.1.x patch; all of B1-B9 belong in 1.x behind the change-control
process; C1/C3/C5 are v2-shaped bets. See [ROADMAP.md](../../../ROADMAP.md).
