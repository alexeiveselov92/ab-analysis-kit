# abkit architecture — as built

> The contributor/assistant condensation of the system **as it exists in code**.
> Reflects: **M1 shipped** (`__version__ = 0.0.1.dev0`), M2 in progress.
> Design contracts for what is being *built next* live in [docs/specs/](../../docs/specs/)
> (canonical for M2+ work); this file must never claim unbuilt code exists.
> Keep in sync with `docs/` on every milestone (and with the `init-claude`
> payload once it exists — M6).

## The shape

**abkit is detectkit's twin with one organ transplanted:** the `detect` stage
becomes a statistical `compute` stage; the primary entity flips from *metric*
to *experiment*. Declarative YAML + SQL run through `load → compute → readout`.

```
experiment (YAML) ──▶ load ──▶ compute (t/z/CUPED/bootstrap) ──▶ readout
   └ references reusable metrics (YAML + SQL)
```

Donor codebase: `/home/aleksei/wsl_analytics/detektkit` (import package
`detectkit`) — components marked ⟲ in
[architecture.md §4](../../docs/specs/architecture.md) port near-verbatim
(`dtk`→`abk`, `detectkit`→`abkit`).

## Package layout — what exists today

```
abkit/
  __init__.py          # __version__ (single source; numpy-free import path)
  cli/main.py          # minimal Click group: `abk --version` only (M2 fleshes out)
  stats/               # ✅ M1 — COMPLETE. The pure numpy core (details below)
  utils/json_utils.py  # stdlib-only canonical JSON (json_dumps_sorted → sha256 hash path)
tests/
  stats/               # unit tests for every stats module (incl. test_purity.py)
  golden/              # golden tests vs an independent legacy transcription, rel-1e-9
```

Everything else in the [module map](../../docs/specs/architecture.md#4-module-map)
(`config/`, `database/`, `loaders/`, `core/`, `pipeline/`, `compute/`,
`validate/`, `reporting/`, `tuning/`) is **planned, not present** — M2+.

## The stats core (`abkit.stats`) — the implemented system

**Purity invariant (hard):** numpy/scipy/statsmodels + stdlib only; never
config/DB/Jinja/click. Sole intra-package import: `abkit.utils.json_utils`.
Enforced by `tests/stats/test_purity.py`.

### Data model (`samples.py`)

- `Sample` (per-unit values, optional `covariate`, `strata`), `Fraction`
  (count/nobs), `RatioSample` (numerator/denominator pairs).
- `SufficientStats`, `RatioSufficientStats`, `PairedSufficientStats`,
  `JointMoments` — closed-form entry; **mixed-ddof convention preserved from
  legacy**: `np.var`-shaped terms use ddof=0, `np.cov`-shaped terms ddof=1.
  Merges are Welford/Chan-stable (`accumulate.py`).
- `align_paired` aligns paired samples by unit.

### Methods — a plugin registry (12 registered)

| Family | Registry names |
|---|---|
| Parametric (`from_suffstats` + `from_samples`) | `t-test`, `paired-t-test`, `z-test`, `cuped-t-test`, `paired-cuped-t-test`, `ratio-delta` |
| Bootstrap (vectorised block-streaming engine) | `bootstrap`, `paired-bootstrap`, `poisson-bootstrap`, `paired-poisson-bootstrap`, `post-normed-bootstrap`, `paired-post-normed-bootstrap` |

- One method = one `BaseMethod` subclass + `@register` (+aliases). The
  pipeline/DB/CLI never special-case a method name.
- `create_method(name, alpha=0.05, params={...})` — `alpha` is the effective
  **post-correction** per-comparison alpha; it is experiment-level and never
  enters `method_config_id`.
- Param schemas are declarative `ParamSpec`s (`base.py`): typed, defaulted,
  identity-flagged; validated at construction (`MethodParamError`).
- **Quarantined legacy-broken branches** raise `QuarantinedMethodError`
  (never silently substituted): PoissonPostNormed, PairedPostNormed relative,
  PostNormed absolute — see [statistics-changes.md §3](../../docs/specs/statistics-changes.md).
- Entry points: `compare(groups)` → all pairwise, `compare_pair(g1, g2)`,
  and the dual entry `from_samples(s1, s2)` ≡ `from_suffstats(st1, st2)`.

### Identity (`method_config_id`)

`sha256(method_name + json_dumps_sorted(non-default identity params) +
ALGORITHM_VERSION appended only when > 1)` — byte-exact-tested. `seed` is
identity-**excluded** for all bootstrap methods; re-runs stay byte-stable via
deterministic per-row seeds (`rng.derive_seed` from row identity). Editing an
identity param orphans the prior results series.

### Results & supporting modules

- `TestResult` (`result.py`): `method_name`, `method_params`, `alpha`,
  `pvalue`, `effect`, `ci_length`, `left_bound`, `right_bound`, `reject`,
  plus per-arm stats, optional `effect_distribution`, `warnings`,
  `diagnostics`, `to_dict()`.
- `srm.py`: `srm_check(observed_counts, expected_split, alpha=0.001)` →
  `SrmResult` (chi-square gate).
- `correction.py`: `adjust_alpha`, `two_tier_alphas` (the legacy two-tier
  Bonferroni keyed off `is_main_metric`), read-time `benjamini_hochberg`,
  `n_comparisons`.
- `power.py`: power/MDE (t-test, CUPED-deflated, proportions).
- Default p-value stays the **baseline sign p-value**; `(#extreme+1)/(n+1)`
  is opt-in `pvalue_kind: plugin` (statistics-changes §2).

### Gotchas that will bite you

- Never "fix" the mixed ddof, the sign p-value, or θ's `np.cov` ddof=1 — they
  are the captured baseline, golden-tested at rel-1e-9.
- **Never change a number silently**: deviation ⇒ `ALGORITHM_VERSION` bump +
  [statistics-changes.md](../../docs/specs/statistics-changes.md) entry +
  CHANGELOG + A/A validation.
- Stratification uses Hamilton apportionment; Poisson bootstrap is mean-only
  (guarded); zero denominators → NaN + warning (H5), never an exception.

## M2 target (being built — specs are canonical)

Declarative config + DB layer + recompute pipeline + CLI. Read before coding:

- YAML shapes, packaged assignment macro, Jinja built-ins, validation matrix →
  [declarative-config.md](../../docs/specs/declarative-config.md)
- Cumulative grid, sub-day `cadence`, `data_lag` watermark, `_ab_unit_state`
  idempotency, compute strategy → [cumulative-intervals.md](../../docs/specs/cumulative-intervals.md)
- `_ab_results` contract → [data-contract-and-reporting.md §2](../../docs/specs/data-contract-and-reporting.md)
- Pipeline stages & storage → [architecture.md §5–6](../../docs/specs/architecture.md)
- The blocking must-fix checklist → [quorum-review.md](../../docs/specs/quorum-review.md)

## Invariants (do not violate)

1. `abkit.stats` stays pure (numpy/scipy/statsmodels only).
2. Never change a number silently (version bump + changes entry + A/A).
3. Methods are plugins; nothing special-cases a method name.
4. The DB manager stays generic (`table_name`-keyed); `_ab_*` semantics live
   in `internal_tables/` only.
5. Greenfield storage — never copy the legacy `marts.*` schema.
6. Renderer stays framework-free (baked payload + self-contained JS).
7. Keep `init-claude` assets, `docs/`, and these rules in sync on release.
