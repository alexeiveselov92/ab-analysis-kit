# Principles

The non-negotiable rules `ab-analysis-kit` is built by. They exist so the project
stays coherent as it grows and as multiple authors (human and AI) contribute.

## Product principles

1. **Declarative first (dbt / detectkit-style).** An analyst defines an experiment
   and its metrics in YAML + SQL, never Python. Everything correctness-critical
   (cohort join, window filter, per-unit dedup, alpha) is packaged, never
   hand-repeated.
2. **The local explore cockpit is the priority interface.** The chart-first
   localhost cockpit where an analyst plays with method params and watches the
   stabilization chart recompute live is the first thing we build and the experience
   everything grows from.
3. **BI-agnostic: own the numbers, not the dashboard.** Results land in one clean,
   stable, documented warehouse table that any BI (Grafana, Lightdash, Metabase,
   Superset) can read. Orchestration is via Prefect (the CLI is the unit of
   automation).
4. **AI-native onboarding.** `abkit init-claude` ships assistant context + skills so
   an assistant can scaffold and tune experiments *for* the analyst — the same
   crown-jewel mechanism as detectkit.
5. **App-seed-shaped.** Keep the renderer/payload split framework-free, `abkit.stats`
   pure, and the data contract BI-first, so this composes into a future app
   (agentic analysis + detectkit + abkit + embedded Lightdash) without a rewrite.

## Statistical principles

6. **Never change a number silently.** The legacy math is a captured baseline; every
   deviation is an `ALGORITHM_VERSION` bump + a `statistics-changes.md` entry +
   A/A-validated justification. Defaults stay baseline-faithful until a fix is proven.
7. **Capture → reproduce → blind-rederive → arbitrate.** Improvements come from
   independently re-deriving the estimand and letting the A/A false-positive matrix
   pick the winner — not from guessing.
8. **Be honest about peeking.** The daily cumulative chart peeks. Default is
   fixed-horizon (parity), but the real cumulative-peeking FPR is always measured and
   surfaced, the readout refuses pre-horizon WIN/LOSE, and sequential CIs are one
   toggle away.
9. **Data integrity is a gate.** SRM is checked before any effect is trusted —
   blocking-but-non-dropping (loud flag, preserved row).
10. **The statistical core is pure.** `abkit.stats` imports only numpy/scipy/
    statsmodels — never config, DB, Jinja, or click. It is independently testable and
    notebook-usable. The same math serves the pipeline, the cockpit, and the A/A
    harness via dual entry (`from_suffstats` / `from_samples`), golden-tested equal.

## Engineering principles

11. **numpy-first, vectorised.** No pandas in the core hot path; preserve and extend
    the legacy's vectorised resampling philosophy.
12. **db-agnostic by construction.** A generic `table_name`-keyed manager; ClickHouse
    first, PostgreSQL/MySQL correct & supported via the same contract. No
    backend-specific logic leaks into the pipeline.
13. **Idempotent & re-runnable.** An experiment is a finite, re-runnable full
    recomputation (last-writer-wins on `method_config_id × end_date`), not a resumed
    cursor. Re-runs are byte-stable (deterministic seeds).
14. **Methods are plugins.** A new estimator is one `BaseMethod` class + a registry
    entry; the pipeline/DB/CLI never special-case a method name.
15. **Small, focused modules; type-safe.** pydantic configs, type hints throughout,
    files kept small (split into mixins, detectkit-style).
16. **Single-source docs.** One Markdown body of domain truth renders both as the
    `abkit.pipelab.dev` site and as the `.claude/rules` shipped by `init-claude`.
17. **Performance is staged, not premature.** v1 recomputes per interval with a
    warehouse sufficient-statistics seam; the Python incremental layer is built only
    when profiling proves the bottleneck, behind a reconciliation gate.
18. **The quorum must-fixes are a definition of done.** See
    [docs/specs/quorum-review.md](docs/specs/quorum-review.md).
