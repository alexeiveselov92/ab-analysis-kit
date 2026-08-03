# M13 — code audit against the blind re-derivation (2026-08-03)

> Companion to the blind derivations in this directory. Every claim below is
> verified against the code at the cited line, or derived algebraically here.
> **No statistical number was changed and no code was touched** — this is design
> input for `docs/specs/m13-implementation-plan.md`.

## 1. The z-test stacks two defects, and one hides the other

`abkit/stats/parametric/ztest.py:70-72` builds the **pooled (null)** variance

```
p̂ = (x₁+x₂)/(n₁+n₂)
pooled_var = p̂(1−p̂)(1/n₁ + 1/n₂)
std_effect = √pooled_var
```

and uses `std_effect` for **both** the p-value (line 77-78) **and** the CI
bounds (line 101-102). The relative branch divides *both* the effect and the SE
by `prop_1` (lines 95-96).

**Consequence nobody wrote down: the pooled SE is what currently makes the CI
and the p-value agree.** Both are computed from the same SE, so
"CI excludes zero" ⟺ "p < α" holds *exactly* — and `pipeline/readout.py`
decides significance by CI exclusion. The relative branch preserves this too
(dividing both by the same positive `prop_1` cannot change the sign structure),
which is why the z-test's absolute and relative branches always reject
identically.

⇒ **M13's change #2 ("unpooled SE in the z-test CI"), applied literally, breaks
that equivalence.** A Wald interval around the observed difference paired with a
score p-value disagree near the boundary, and the readout has no way to express
the disagreement — it reads one and persists the other.

## 2. Changes #2 and #5 are ONE decision, not two

The pooled p-value **is** the score test. Inverting it properly does not give
the Wald interval — it gives the **Wilson** interval, which is exactly change
#5 (Agresti-Caffo/Wilson). So:

| Route | CI | Coherence with the p-value | Coverage |
|---|---|---|---|
| literal change #2 | Wald (unpooled) | **broken** near the boundary | under-covers, worse as α shrinks |
| invert the score test | Wilson-type | **preserved by construction** | the property Wilson is chosen for |

⇒ Plan them as one work package with one choice, not as two independent items.
The ROADMAP contour lists them separately; that is a contour artifact.

## 3. "Restore the relative-z covariance term" is a misleading name

`effects.relative_delta_effect(mean_num, var_num, mean_den, var_den, covariance)`
(`abkit/stats/effects.py:103-136`) computes

```
Var ≈ var_num/mean_den² + var_den·mean_num²/mean_den⁴ − 2·(mean_num/mean_den³)·covariance
```

The t-test calls it with numerator `m₂−m₁`, denominator `m₁`, and
`covariance = −var_mean_1`. Substituting `d = m₂−m₁`, `R = m₂/m₁ = 1 + d/m₁`,
`V_j = Var(m̂_j)`:

```
(V₁+V₂)/m₁² + d²V₁/m₁⁴ + 2(d/m₁³)V₁
  = [V₂ + V₁(1 + 2(R−1) + (R−1)²)]/m₁²
  = [V₂ + R²V₁]/m₁²
```

— i.e. **exactly** the blind derivation's delta-method SE `√(R̂²V̂₁+V̂₂)/|m̂₁|`.

So the "covariance" is `Cov(m̂₂−m̂₁, m̂₁) = −Var(m̂₁)`, an algebraic artifact of
the shared `m₁`, **not** a between-arm correlation (the arms are independent).
What the z-test actually drops is the **R² coefficient on V₁**. Spec wording
must say that, or an implementer will hunt for a between-arm covariance, fail to
find one, and conclude the change is a no-op.

The mechanical fix is cheap: route the z-test's relative branch through the
existing, golden-covered `relative_delta_effect`, as the t-test already does.

## 4. …but the delta method is not the destination

The blind derivation (`relative-effect.derivation.json`) recommends the
**Fieller** interval and predicts the three variants are separated by A/A
signatures:

- **shortcut** (today's z-test): A/A FPR exactly nominal, false-positive signs
  balanced 0.50 — *invisible to any null-based test*. Coverage error shows only
  under an injected effect, and depends on θ, not on CV₁.
- **delta** (today's t-test, and where the "cheap fix" would put the z-test):
  FPR nominal but false-positive **signs asymmetric** — predicted left-tail share
  `0.5 + φ(z)z²·CV₁√w₁/α`, i.e. ≈0.66 at CV₁=0.05, **and the asymmetry grows as
  α shrinks** — worst in exactly the corrected two-tier regime M13 introduces.
- **Fieller**: symmetric signs, nominal coverage, and it declines to produce a
  bounded interval when none exists.

⇒ The "obvious" fix moves the z-test from one flawed estimator to a different
flawed estimator. Whether that is still worth doing is a real question for the
design doc, not a foregone conclusion.

## 5. Two instrument consequences

**(a) The A/A matrix must record the SIGN of each false positive.** It already
computes it — `validate/scoring.py:184` `_significance()` returns
`(significant, sign)` — so this is plumbing, not new measurement. Without it,
the matrix cannot distinguish shortcut from delta at all (both give nominal
FPR), which is the single most important discrimination in this milestone.

**(b) Fieller would break explore's Tier α, silently.** `tuning/recompute.py:538`
`_alpha_inverted_bounds` re-derives "a closed-form row's symmetric normal CI" at
a new α from the persisted numbers. Fieller's half-width is **not** proportional
to `z`: the factor `g = z²V̂₁/m̂₁²` depends on α itself. A cached-SE × new-z path
therefore degrades to a delta interval at every α except the one it was computed
at — and the tier is already labelled "approx", so the drift would not look like
a fault. Either Fieller recomputes `g` (making it Tier E, not an inversion), or
the relative effect is excluded from α-inversion.

## 6. Correction-layer facts (from the same pass)

- The config knob **already exists**: `correction: none|bonferroni|benjamini_hochberg`
  (`config/project_config.py:91`) with an experiment override. Adding `holm` is
  extending an enum, not new config surface.
- **Holm belongs read-time, and the seam is already built.** `two_tier_alphas`
  fixes α at COMPUTE time, but Holm — like BH — is a whole-family, p-value-ordering
  rule. BH already flows through `stats.correction.composed_significance`, shared
  by `pipeline/readout.py` and the A/A family sweep. Holm slots in beside it.
- **CI-vs-verdict divergence is already a live property under BH**, not something
  M13 introduces: `pipeline/analyze.py:76-78` leaves compute-time α *raw* under
  BH, so the persisted interval is a raw-α interval while the decision uses the
  BH-adjusted p against that raw α. Holm inherits the same shape. The spec must
  state it; today no document does.
- The client mirror `web/src/explore/explore.ts:133` (`if (correction !==
  'bonferroni') return rawAlpha`) is already correct for a read-time scheme — a
  new read-time value passes through it by construction.
- Goldens pin the correction layer too: `tests/golden/test_golden_power_correction.py`.

## 7. CUPED and change #4 (uniform ddof) — the change is hygiene, not correctness

Verified in `abkit/stats/parametric/cuped_ttest.py`:

**(a) The mixed convention lives only in θ.** `theta_num = Σ cross_c/(n−1)`
(np.cov parity) over `theta_den = Σ cov_m2/n` (np.var parity), line 229. Hence

```
θ_abkit = θ_consistent · n/(n−1)      (single arm; pooled θ is the weighted analogue)
```

— an inflation of exactly `1/(n−1)`.

**(b) That inflation is second-order harmless.** `Var(Y − θX)` is *minimised* at
θ*, so a deviation θ*(1+ε) costs excess variance `ε²θ*²Var(X)` — quadratic in
`ε = 1/(n−1)`, i.e. ~1e-8 relative at n = 10⁴. And the point estimate is
unbiased for **any** fixed θ, because randomisation gives
`E[cov_mean₁] = E[cov_mean₂]`, so θ cancels out of the difference. The mixed
ddof in θ therefore damages neither the estimate nor the inference.

**(c) No PSD hazard in the absolute branch.** `var_cup_i = (m2 − 2θ·cross +
θ²·cov_m2)/n_i` (line 133) is ddof-uniform internally — a manifest sum of
squares, non-negative by construction.

**(d) The PSD hazard in the relative branch is real but already guarded.**
Line 141-147 mixes `var_num` (ddof=0 based) with `covariance` (ddof=1 based)
inside one quadratic form, so Cauchy-Schwarz can fail within `1/(n−1)` of
perfect correlation — reachable in practice by an operator pointing CUPED at the
metric's own column. `effects.normal_test` already catches it explicitly
(`effects.py:171-175`: *"effect variance is negative (anomalous covariance term
— possible with the mixed-ddof convention on adversarial data)"*) and returns
NaN with a warning. Known and handled, not a latent bug.

**(e) Planning consequence.** The blind-derivation brief for ddof asked whether
the change is detectable by an A/A matrix "or is strictly below its noise floor
— be quantitative, it decides whether the change is worth making." The code
answers it: **not detectable.** Moving the measured FPR by even 0.001 needs the
SE to move ~2%, which needs n ≈ 50 — two to three orders below this engine's
operating range (10⁴–10⁷). So change #4:

- cannot be arbitrated by `abk validate`, the project's designated instrument;
- must therefore ship either as **dropped**, or as hygiene arbitrated by
  **algebraic identity** with that limitation stated in `statistics-changes.md`;
- is, at the small n where ddof *would* matter (~1% on the variance at n = 100),
  **dominated by** the normal-vs-Student-t approximation error (~2% at the same
  n) — which is deferred to M15. Fixing the smaller term while leaving the larger
  one is not an improvement anyone can measure.

## 8. Two facts that price the correction WP (checked after the multiplicity derivation)

**(a) Fork B is reachable — the persistence minimum is already met.** The
derivation warns that a row storing only `(lo, hi, level)` is lossy and
"permanently forecloses every step procedure". `_ab_results` is not that row: it
stores `pvalue`, the effective `alpha`, `std_1`/`std_2`, `size_1`/`size_2`,
`effect` and both bounds (`database/tables.py:196-220`). So the family's
p-vector is assemblable at read time — BH already does exactly that — and the SE
is recoverable from the half-width and the stored level. The one absent field is
**degrees of freedom**, which is not needed while every method uses the normal
approximation, and becomes a forward dependency only if M15's Student-t lands.

**(b) `is_guardrail` already exists end-to-end and the correction layer ignores
it.** The field is declared in config (`config/experiment_config.py:331`,
*"Checked for regression only"*), validated as mutually exclusive with
`is_main_metric` (line 352), persisted to `_ab_results`, surfaced in the report
(`reporting/builder.py:281`), in `abk plan` (`cli/commands/plan.py:314`) and in
the explore editor. It appears **nowhere** in `analyze.effective_alphas`, which
counts only `non_main` and hands every non-main comparison the same secondary α.

⇒ A guardrail is corrected exactly like a growth metric — which is the
derivation's finding (d): for a metric whose job is to catch harm, a tighter α
makes you **less** likely to catch it. The error is in the dangerous direction,
and it is the one defect in this milestone that costs safety rather than power.

The fix is correspondingly cheap: the declaration, the storage and the UI all
exist; only the alpha resolver does not read them.

## 9. Derived decision (records the maintainer's 2026-08-03 sign-off)

Under "opt-in first" + "scheme lives in the config field", the `metrics_count=1`
FWER fix **must be a new enum value**. Leaving the name `bonferroni` and changing
what it computes would silently change the meaning of an existing YAML between
0.7.0 and 0.8.0 — the same class of silent number change the project's first
invariant forbids, routed through config instead of code.
