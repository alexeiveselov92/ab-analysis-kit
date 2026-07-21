# Quorum review — the must-fix development gate

> The architecture was validated by a 5-lens adversarial quorum. All five returned
> **approve-with-changes**. This is the consolidated, de-duplicated list of
> **blocking** items (must be resolved before / during development) plus high-value
> nice-to-haves. Each links to where it is addressed. Treat the must-fixes as a
> definition-of-done checklist per area.

## Verdicts

| Lens | Verdict |
|---|---|
| Scalability & performance | approve-with-changes |
| Reliability & correctness (engineering) | approve-with-changes |
| Declarative design | approve-with-changes |
| DX / interface | approve-with-changes |
| Statistical correctness | approve-with-changes |

## Must-fix (blocking)

### Statistical correctness
- [ ] **Peeking is the product, not an edge case.** `validate` measures FPR over the
  **full day-grid through the actual readout rule**; `readout` refuses pre-horizon
  WIN/LOSE unless `sequential.enabled`; the daily fixed-horizon CI is rendered "not
  peeking-valid". → [aa-false-positive-matrix.md §3](aa-false-positive-matrix.md), [data-contract-and-reporting.md §4](data-contract-and-reporting.md)
- [ ] **CUPED covariate window** — decide growing (parity) vs fixed (version-bumped);
  golden-test it; fix the example metric. → [cumulative-intervals.md §5.1](cumulative-intervals.md), [statistics-changes.md §5](statistics-changes.md)
- [ ] **Mixed ddof** — encode the exact per-term `np.cov`=ddof1 / `np.var`=ddof0;
  golden test on θ; correct the baseline prose. → [statistics-baseline.md](statistics-baseline.md), [statistics-changes.md §1.2](statistics-changes.md)
- [ ] **Golden tolerance = relative 1e-9**, Welford/two-pass variance, heavy-tailed
  revenue fixture. → [statistics-changes.md §1.1](statistics-changes.md)
- [ ] **Bootstrap seed policy** — exclude from `method_config_id` for all bootstrap
  methods; deterministic per-row seed; byte-stable re-runs. → [declarative-config.md §7](declarative-config.md), [statistics-changes.md H2](statistics-changes.md)
- [ ] **Quarantine broken ratio methods** (PoissonPostNormed, PairedPostNormed
  relative, PostNormed absolute); `ratio_delta` known-answer test. → [statistics-changes.md §3](statistics-changes.md)

### Reliability
- [ ] **`_ab_unit_state` idempotent per (exp, day)** — replace-not-sum; twice-run
  test. → [cumulative-intervals.md §5.2](cumulative-intervals.md)
- [ ] **Atomic lock** on PG/MySQL (`INSERT … ON CONFLICT` / `FOR UPDATE`); advisory
  on ClickHouse + (exp,metric) serialization + the unit-state idempotency fix (the
  TOCTOU race + additive state is a corruption path). → [cumulative-intervals.md §5.2, §5.7](cumulative-intervals.md)
- [ ] **Deterministic completeness boundary** — explicit `data_complete_through`, not
  `today()`; normalised tz across backends. → [cumulative-intervals.md §5.6](cumulative-intervals.md)
- [ ] **`created_at` strictly-increasing & distinct** per write (BI dedup uses
  argMax/LIMIT 1 BY, no FINAL); canonical `method_params` JSON everywhere. → [data-contract-and-reporting.md §2](data-contract-and-reporting.md)
- [ ] **Correctness under async merge** — `-Merge`/`FINAL`/`argMax` on all
  correctness-sensitive reads. → [cumulative-intervals.md §5.4](cumulative-intervals.md)

### Scalability
- [ ] **Bootstrap memory wall** — Poisson default above a unit threshold; block-stream
  resampling under a memory cap; pre-flight estimate; no Python array-cache at scale.
  → [cumulative-intervals.md §5.8](cumulative-intervals.md), [statistics-changes.md H10](statistics-changes.md)
- [ ] **`_ab_unit_state` cardinality** — key per (source-table, column-set, unit), not
  per metric. → [cumulative-intervals.md §5.3](cumulative-intervals.md)
- [ ] **Resolve the cohort once per run** — the metric loader joins the cohort
  source once per run (the persisted `_ab_exposures` copy, or a live-rendered
  subquery in the M8 no-copy default), never a per-interval visitor re-scan.
  → [cumulative-intervals.md §5.5](cumulative-intervals.md), [declarative-config.md §4](declarative-config.md)
- [ ] **Concurrency model** — lock at (exp)/(exp,metric); worker-pool driver. → [cumulative-intervals.md §5.7](cumulative-intervals.md)
- [ ] **Bound `validate` cost** — closed-form default; bootstrap A/A opt-in with
  reduced N + subsampled population. → [aa-false-positive-matrix.md §6](aa-false-positive-matrix.md)

### Declarative design
- [ ] **Packaged assignment macro** — no leaked cohort/window/dedup boilerplate; the
  example uses it; lint asserts the JOIN. → [declarative-config.md §4](declarative-config.md)
- [ ] **Canonical `method_config_id`** with a byte-exact test (registry name, sorted
  JSON, version tag at v1). → [declarative-config.md §7](declarative-config.md)
- [ ] **Inspectable alpha** — declared `alpha`+`correction`; effective per-comparison
  alpha echoed; two-tier Bonferroni golden test. → [declarative-config.md §6](declarative-config.md)
- [ ] **Two-level reference integrity** tests (dangling refs, namespace collisions,
  unit-key consistency, duplicate refs). → [declarative-config.md §8](declarative-config.md)
- [ ] **Authoritative Jinja built-ins table** (incl. `data_database`) + a render test
  of the example. → [declarative-config.md §5](declarative-config.md)

### DX
- [ ] **A/A calibration inside explore** (always-visible chip; gate Apply when
  uncalibrated). → [aa-false-positive-matrix.md §5](aa-false-positive-matrix.md)
- [ ] **A/A matrix UX** — color vs budget, "Recommended" row, plain-language verdict;
  worked example. → [aa-false-positive-matrix.md §4, §8](aa-false-positive-matrix.md)
- [ ] **Runnable first-run example** against a seed dataset. → [cli-and-dx.md §6](cli-and-dx.md)
- [ ] **Loader one-row-per-unit guard** (warn when rows > distinct units). → [declarative-config.md §3](declarative-config.md)
- [ ] **SRM loud in CLI** + optional BI panel; document the BI gap. → [data-contract-and-reporting.md §6](data-contract-and-reporting.md)

## High-value nice-to-haves
- Profiling/observability (`run --profile`: rows-scanned/bytes/wall-time per stage)
  to make the v2-incremental trigger data-driven. → [cumulative-intervals.md §4](cumulative-intervals.md)
- A migration/parity command that diffs the analyst's real legacy outputs vs the new
  engine within tolerance (onboarding proof).
- Backfill/late-data runbook (how `*_wo_curr_day` completeness interacts with
  re-opening a closed cumulative day).
- `verify-incremental` asserts equality across the **whole** series, not just the
  latest cutoff.
- Structured segment-filter block instead of the raw `added_filters` "starts with
  AND" escape hatch.
- Guardrail-metric multiplicity note (guardrails are still tests; document their
  role in the correction).
