# Roadmap (technical plan)

Milestones from greenfield to a shippable v1, then v2. Ordered so the **priority
explore cockpit** and a **runnable first-run** arrive early. Each milestone's
definition-of-done includes the relevant
[quorum must-fixes](docs/specs/quorum-review.md).

## M0 — Project scaffolding & contracts ✅ (this session)
- References analyzed; architecture synthesized & quorum-validated.
- Specs written ([docs/specs/](docs/specs/)); statistics baseline + legacy catalogue
  captured; founding decisions locked.
- **Next:** repo skeleton (`pyproject.toml`, package layout from
  [architecture.md §4](docs/specs/architecture.md), CI, pre-commit).

## M1 — Pure statistical core (`abkit.stats`)
- `BaseMethod` ABC + registry + factory; `Sample`/`Fraction`/`SufficientStats`
  (mixed-ddof aware); `effects.py` (delta-method linearisation); `TestResult`.
- Parametric: `ttest`, `paired_ttest`, `ztest`, `cuped_ttest`, `paired_cuped_ttest`,
  `ratio_delta`. Bootstrap: vectorised engine (mean fast-path + Poisson matmul),
  `bootstrap`, `paired_bootstrap`, `poisson_bootstrap`, `post_normed_bootstrap`,
  percentile CI + `(#extreme+1)/(n+1)` p-value. Power/MDE; Bonferroni.
- `rng.py` (`default_rng`, deterministic per-row seeds). Dual entry
  (`from_suffstats` ≡ `from_samples`).
- **DoD:** golden tests vs the legacy engine at rel-1e-9 (incl. θ); known-answer
  tests; canonical `method_config_id` byte test; quarantine policy for broken
  ratio methods. *(Must-fixes: ddof, tolerance, seed policy, hash, quarantine.)*

## M2 — Declarative config + DB layer + the pipeline (recompute)
- pydantic Experiment/Metric/Method configs + two-level validator; Jinja templating
  + the **packaged assignment macro**; project/profiles + env interpolation.
- Generic DB manager (CH/PG/MySQL) + internal tables (`_ab_experiments`,
  `_ab_exposures`, `_ab_results`, `_ab_tasks`); `core/period_planner` (expanding
  grid, anti-join, explicit completeness boundary).
- `pipeline`: discover → plan → load (cohort once) → SRM gate → compute → enrich →
  persist. `abkit run`, `abkit run --steps validate` (config-lint), `unlock`,
  `clean`. Read-only exposures.
- **DoD:** `abkit init && abkit run --select example` produces real results on a
  fresh machine against a seed dataset; idempotent re-run is byte-stable; atomic
  lock; strictly-monotonic `created_at`; one-row-per-unit guard. *(Must-fixes:
  macro, alpha inspectability, completeness boundary, lock, SRM-in-CLI.)*

## M3 — The explore cockpit (PRIORITY) + reporting
- Port the detectkit `tune` package → `abkit explore`: localhost server, live
  `from_suffstats` recompute, stabilization chart, Basic/Advanced knobs,
  `.history` write-back, orphan detection.
- Port `reporting` → self-contained HTML readout; `readout.py` decision logic
  (WIN/LOSE/FLAT/INCONCLUSIVE; pre-horizon refusal; SRM gate).
- **DoD:** Apply gated when uncalibrated; calibration chip wired (depends on M4).
  *(Must-fixes: calibration-in-explore, SRM surfacing.)*

## M4 — A/A false-positive matrix (`abkit validate`)
- Port the autotune scaffolding → placebo A/A splits, FPR + power + achieved-MDE +
  coverage, **honest cumulative-peeking FPR** over the day-grid; `_ab_aa_runs`;
  recommendation; the matrix UX (color vs budget, Recommended row, plain verdicts).
- **DoD:** closed-form default, bootstrap A/A opt-in with subsampling; worked
  example in the spec; powers the explore calibration chip and the blind-rederivation
  arbitration. *(Must-fixes: matrix UX, peeking FPR, validate cost bound.)*

## M5 — Sequential analysis + planner + corrections
- `sequential/` (mSPRT always-valid + alpha-spending), opt-in; `ci_kind`/`is_horizon`
  in the contract. `abkit plan` (pre-launch power/sizing). Benjamini-Hochberg
  read-time; composed-FDR empirical validation.

## M6 — DX, docs, orchestration, release
- `abkit init-claude` + packaged `.claude` assets (rules + 7 skills);
  single-source docs site (`abkit.pipelab.dev`, Astro + sync-docs); Prefect
  flow/deployment scaffolding; BI reference queries/dashboards (Grafana, Lightdash,
  Metabase, Superset) + optional SRM panel; `test-report` channels.
- **DoD:** PyPI release `pip install ab-analysis-kit`; `CHANGELOG.md` authoritative;
  contributor `CLAUDE.md` + `.claude/rules` in sync.

## v2 (deferred, profiling-gated)
- Python incremental accumulator + array-cache + quantile sketches +
  `incremental_backend`; `abkit verify-incremental` gate (whole-series reconciliation);
  `run --profile` observability to trigger it on a concrete cost threshold.
- Cross-fitted CUPED/CUPAC, Student-t (Welch–Satterthwaite), BCa bootstrap,
  Mann-Whitney, cluster-robust SE; full PG/MySQL incremental parity (if needed);
  optional owned randomization; app integration (agentic analysis + embedded Lightdash).

## Backlog / open items for the user
Tracked in the RU initiation spec ([docs/ru/project-initiation-spec.md](docs/ru/project-initiation-spec.md))
— covariate-window choice, v2 trigger threshold, docs domain confirmation, SRM
`expected_split` source, guardrail multiplicity handling.
