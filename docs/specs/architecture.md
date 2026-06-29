# Architecture

> Validated by a 5-lens adversarial quorum (scalability, reliability, declarative
> design, DX, statistical correctness) — all five returned **approve-with-changes**.
> The blocking changes are tracked in [quorum-review.md](quorum-review.md).

## 1. One-line shape

**abkit is detectkit's twin with one organ transplanted:** detectkit's `detect`
stage becomes a statistical **`compute`** stage, and the primary entity flips from
*metric* to *experiment*. Everything domain-neutral in detectkit is reused
near-verbatim; the A/B statistical domain is swapped onto its exact extension
points.

```
detectkit:  metric (YAML+SQL) ──▶ load ──▶ detect (MAD/Z/IQR) ──▶ alert
abkit:      experiment (YAML)  ──▶ load ──▶ compute (t/z/CUPED/bootstrap) ──▶ readout
            └ references reusable metrics (YAML+SQL)
```

## 2. The three approaches we evaluated

A judge panel produced three independent designs; the synthesis takes the
strongest of each.

| Approach | Core idea | Strength | Weakness |
|---|---|---|---|
| **A. detectkit-symmetry** | Mirror detectkit's module map 1:1; A/B is "a new compute stage" | ~60–70% code reuse, instant familiarity, proven DX | A/B is metric-primary-shaped unless stretched to two levels |
| **B. statistics-engine-first** | A pure, testable `abkit.stats` core is the centerpiece; everything serves it | Statistical rigor, notebook-usable, easy to extend methods | Risks under-investing in the pipeline/DB/DX plumbing |
| **C. scale / incremental-first** | Design storage & compute around incremental sufficient-statistics | Best at many experiments × metrics × days | Incremental layer is a large correctness surface, premature in v1 |

**Synthesis:** adopt **A** as the skeleton (maximal detectkit reuse), fuse in **B**'s
pure importable `abkit.stats` core and **C**'s *sufficient-statistics seam* — but
**defer C's full Python incremental layer to v2** (see
[cumulative-intervals.md](cumulative-intervals.md)). All three proposals converged
here independently.

## 3. Three non-negotiable pillars

1. **The math is a captured baseline, then deliberately improved.** The legacy
   algorithms are frozen in [statistics-baseline.md](statistics-baseline.md) and
   golden-tested against the legacy *engine* (run the old Python on identical
   `Sample` inputs). Every deviation is an `ALGORITHM_VERSION` bump + a
   [statistics-changes.md](statistics-changes.md) entry, arbitrated by the A/A
   harness — never a silent number change. (We are **not** bound to the legacy
   table or its production numbers — storage is greenfield.)
2. **The cumulative daily expanding-window is a first-class compute primitive.**
   One row per `(experiment, metric, variant-pair, method, end_date)`, cumulative
   from a pinned start. This is the stabilization chart's data and the heart of
   the product ([cumulative-intervals.md](cumulative-intervals.md)).
3. **A pure, importable, numpy-first statistical core (`abkit.stats`)** with zero
   IO/DB/config dependencies and dual entry (`from_suffstats` for the closed-form
   majority, `from_samples` for bootstrap & golden reproduction) so the same math
   serves the pipeline, the explore cockpit, the A/A harness, and notebook users.

## 4. Module map

> Reused-from-detectkit components are marked ⟲ (port near-verbatim, rename `dtk`→`abkit`).
> Greenfield storage: the `_ab_*` tables are **our own clean contract**, not a copy
> of the legacy `marts.*` layout.

```
abkit/
  __init__.py                  # __version__ (single source); top-level re-exports
  cli/                         # ⟲ Click CLI "abkit"
    main.py                    # ⟲ lazy-import command group
    _output.py                 # ⟲ echo_tree / StageLogRenderer (verbatim)
    commands/
      init.py                  # ⟲ scaffolder (ab_kit_project.yml, profiles.yml, experiments/, metrics/, sql/)
      init_claude.py           # ⟲ managed CLAUDE.md block + .claude/rules + skills
      run.py                   # project-root discovery + selector + per-experiment driver; --report
      explore.py               # ⟲(tune) localhost cockpit: live method_params recompute + .history write-back
      validate.py              # ⟲(autotune) A/A false-positive + power matrix → _ab_aa_runs
      plan.py                  # NEW: pre-launch power / sample-size / runtime planner
      clean.py                 # ⟲ prune _ab_results by method_config_id drift; orphaned experiments
      unlock.py                # ⟲ clear stale locks (verbatim)
      test_report.py           # ⟲(test_alert) mock readout through channels
    assets/claude/             # packaged init-claude payload (CLAUDE.section.md, rules/, skills/)
  core/
    interval.py                # ⟲ duration parser ("14d"/"1d"/seconds) — horizon + cadence
    models.py                  # ⟲ TableModel / ColumnDefinition (db-agnostic DDL + version_column LWW)
    period_planner.py          # expanding cumulative grid (start pinned, end=start+day) + is_calculated anti-join
  config/
    project_config.py          # ⟲+ ProjectConfig (alpha / test_type / correction / power / aa_fpr_budget / compute.mode)
    profile.py                 # ⟲ ProfilesConfig + {{ env_var() }}/${VAR} interpolation
    experiment_config.py       # ExperimentConfig (PRIMARY entity): assignment, variants, unit_key, comparisons[]
    metric_config.py           # MetricConfig (reusable library: fraction|sample|ratio + column roles)
    method_config.py           # MethodConfig (name + params) → method_config_id hash
    validator.py               # two-level reference integrity, name uniqueness, SRM/unit-key checks
  database/                    # ⟲ generic manager (verbatim) + one new capability: emit agg-state DDL
    manager.py                 # BaseDatabaseManager ABC (execute_query/create_table/insert_batch/upsert/delete/locks)
    clickhouse_manager.py postgres_manager.py mysql_manager.py
    internal_tables/           # mirrors dtk; _ab_ prefix (our own greenfield schema)
      manager.py _base.py _schema.py _maintenance.py
      _experiments.py _exposures.py _unit_state.py _results.py _aa_runs.py _tasks.py
  loaders/
    query_template.py          # ⟲ Jinja2 StrictUndefined (built-ins swapped to ab_*)
    exposure_loader.py         # assignment SQL → per-unit (unit_id, variant, exposure_ts, stratum)
    metric_loader.py           # metric SQL → SuffStats or per-variant numpy arrays (one row per unit; no time-grid)
  stats/                       # THE pure numpy core (no IO/DB/config) — importable standalone
    base.py                    # BaseMethod ABC: validate_samples / compare / from_suffstats / from_samples / hash; ALGORITHM_VERSION
    result.py registry.py factory.py
    samples.py                 # Sample / Fraction / PairedSample (ddof=0); SufficientStats (mixed-ddof aware)
    effects.py                 # absolute/relative estimand + delta-method linearisation (the preserved formula)
    accumulate.py              # SufficientStats merge()/delta() (v2 primitive); Welford/Kahan stable
    parametric/  ttest.py paired_ttest.py ztest.py cuped_ttest.py paired_cuped_ttest.py ratio_delta.py
    bootstrap/   engine.py applier.py bootstrap.py paired_bootstrap.py poisson_bootstrap.py post_normed_bootstrap.py ci.py
    sequential/  always_valid.py group_sequential.py   # opt-in (Q2): mSPRT / alpha-spending
    power.py correction.py srm.py rng.py sketches.py
  compute/
    recompute_backend.py       # v1 default: full-window aggregation (golden reference)
    incremental_backend.py     # v2: reads _ab_unit_state moments (deferred, gated by verify-incremental)
    accumulator.py             # v2: per-unit SuffStats accumulator
  pipeline/
    planner.py analyze.py driver.py enrich.py readout.py
  validate/                    # the A/A engine (ported autotune scaffolding)
    aa_runner.py splitter.py inject.py scoring.py matrix.py result.py
  reporting/                   # ⟲ self-contained offline HTML readout (renderer verbatim; payload swapped)
    builder.py html_report.py assets/
  tuning/                      # ⟲(tune) the explore cockpit (localhost server + live recompute + write-back)
    payload.py server.py html.py config_writer.py assets/
  utils/                       # ⟲ env_interpolation / json_utils (json_dumps_sorted → hash) / datetime_utils
docs/  tests/  pyproject.toml  README.md  CHANGELOG.md  website/
```

## 5. The pipeline (`abkit run --select <exp>`)

`--steps` defaults to all; each stage is idempotent (last-writer-wins, not a
resumed cursor — an experiment is a finite, re-runnable recomputation).

0. **discover + validate** — find project root, parse Experiment/Metric/Method
   YAML (pydantic, fail-fast), resolve comparison→metric references, enforce
   name uniqueness, validate method params against each method's schema, compute
   `method_config_id`. No DB/compute. ([declarative-config.md](declarative-config.md))
1. **plan** — `core/period_planner` reproduces the legacy cumulative grid: for
   each experiment day `d`, emit `[start_date (pinned), start_date+d]`; LEFT-ANTI-JOIN
   against already-computed `(exp, metric, method_config_id, end_date)` rows;
   keep only complete days (`end_date <= data_complete_through`, an explicit
   single-source boundary, **not** `today()`). ([cumulative-intervals.md](cumulative-intervals.md))
2. **maintain unit-state** *(the scalability seam)* — advance `_ab_unit_state`
   with the new day's per-unit deltas. ClickHouse: a merge-tree agg state keyed
   per **(source-table, column-set, unit)** so co-located metrics share moments;
   writes are **idempotent per (exp, day)** (replace-not-sum). PG/MySQL: upserted
   state table. In v1 this is a thin materialization (read path is recompute).
3. **load** — `exposure_loader` runs the assignment SQL once → `_ab_exposures`
   (SRM source + per-variant sizes). `metric_loader` renders each metric's Jinja
   SQL for the period and returns `SufficientStats` per `(variant[,stratum])`
   (closed-form) or cached per-unit numpy arrays (bootstrap/quantile). Metric SQL
   **joins the persisted `_ab_exposures`** (via a packaged macro) instead of
   re-deriving the cohort every interval.
4. **SRM gate** *(data integrity, blocking-but-non-dropping)* — `stats/srm.py`
   chi-square of observed vs `expected_split` **before** any effect. Failure ⇒
   the row is still written with `srm_flag=1`, surfaced as a red gate, and
   (configurably) blocks the readout. No detectkit analog.
5. **analyze** *(the statistical core)* — build `Sample`/`Fraction`/`SufficientStats`;
   the bound method runs all pairwise variant comparisons via `from_suffstats`
   (closed-form, microseconds) or `from_samples` (bootstrap). alpha is
   Bonferroni-adjusted; optional Benjamini-Hochberg cross-metric at read time;
   sequential CIs replace fixed-horizon when `sequential.enabled`. Pure numpy.
6. **enrich + persist** — flatten each `TestResult` to the clean results row
   (identifiers + window + per-arm stats + test outputs + `srm_flag` + provenance
   + `method_config_id` + a strictly-monotonic `created_at` LWW version), upsert
   to `_ab_results`. ([data-contract-and-reporting.md](data-contract-and-reporting.md))
7. **readout + report** *(optional)* — `readout.py` applies the WIN / LOSE / FLAT /
   INCONCLUSIVE decision (significance ∧ stabilization ∧ power; SRM is a hard
   gate; pre-horizon WIN/LOSE refused unless sequential). `reporting` emits the
   self-contained HTML readout (effect+CI stabilization chart, MDE/power,
   p-value-vs-alpha, SRM, A/A matrix).

**A. validate** *(out-of-band)* — `abkit validate` is **not** in the hot path: it
draws placebo A/A splits, scores empirical FPR vs nominal alpha (incl. the honest
**cumulative-peeking** FPR over the full day-grid) and power via injected effects.
([aa-false-positive-matrix.md](aa-false-positive-matrix.md))

## 6. Database & storage (greenfield)

`BaseDatabaseManager` is ported verbatim from detectkit (domain-neutral). The
internal/data split is kept (all `_ab_*` in `internal_database`; experiment fact
& assignment tables in `data_database`). The **only** new capability is emitting
backend-specific incremental-aggregate DDL.

| Table | Role |
|---|---|
| `_ab_experiments` | experiment catalog (name, dates, status, usage) |
| `_ab_exposures` | per-unit assignment (exp, unit, variant, exposure_ts, stratum); SRM source — **read-only** loaded from the assignment SQL |
| `_ab_unit_state` | cumulative per-unit moments; ClickHouse agg-state seam (keyed per source-table+column-set+unit; idempotent per day). The scalability substrate |
| `_ab_results` | **our clean BI contract** — one cumulative row per (exp, metric, pair, method, end_date). Designed from the decision logic, not the legacy schema |
| `_ab_aa_runs` | A/A validation audit (FPR, power, peeking-FPR, verdict) |
| `_ab_tasks` | run locks + idempotency (⟲ verbatim) |

Idempotency: every row carries `method_config_id = hash(method_name + sorted
non-default params + ALGORITHM_VERSION)`; editing params orphans old rows, GC'd by
`abkit clean`. ClickHouse reads use FINAL/argMax dedup; `created_at` is a
strictly-monotonic distinct version (reliability must-fix).

## 7. Key decisions (with rationale)

1. **detectkit-symmetry skeleton + pure `abkit.stats` core + dual-entry engine.**
   Symmetry buys ~60–70% reuse and familiarity; the pure core is independently
   testable & notebook-usable; dual entry powers both the instant explore cockpit
   and the v2 incremental path from **one** math implementation.
2. **Greenfield storage; legacy is a reference for UX/decision-logic only.** *(Your
   decision Q1.)* The legacy marts/storage are explicitly bad; we owe nothing to
   their schema. We owe everything to the *algorithms* (preserved) and the *user
   decision logic* (reproduced by a clean contract).
3. **Baseline-faithful then improved, A/A-arbitrated.** The math is captured,
   golden-tested vs the legacy engine, then improved deliberately — supports your
   plan to blind-rederive and synthesize better algorithms.
4. **Compute: v1 recompute + warehouse sufficient-statistics seam; defer the
   Python incremental layer to v2** behind `verify-incremental`. ~80% of the cost
   saving at near-zero risk. ([cumulative-intervals.md](cumulative-intervals.md))
5. **Experiment is primary; metrics are a reusable library; the method is the
   tunable, hashed object.** The genuine A/B shape; enables metric reuse + per-
   comparison method binding.
6. **Methods are a plugin registry** (`BaseMethod` + `@register` + factory); the
   pipeline/DB/CLI never special-case a method name. A new estimator is one class.
7. **SRM as a blocking-but-non-dropping gate.** The A/B data-integrity failure
   detectkit has no analog for; loud flag + preserved row beats a silent drop.
8. **Fixed-horizon default; sequential opt-in; peeking-FPR measured.** *(Your
   decision Q2.)* Honest about the daily-peeking risk without changing defaults.
9. **ClickHouse-first; read-only exposures.** *(Your decisions Q3, Q4.)*

## 8. Differences from detectkit the architecture must respect

- **Data shape:** per-unit observations grouped by variant, **not** a per-timestamp
  grid — no gap-fill/seasonality machinery in the core.
- **Detector → statistical method:** estimate an *effect* + uncertainty, not flag
  anomalies. Different math, params, identity.
- **Statistical correctness is the risk surface:** peeking/sequential, SRM,
  multiple-testing, analysis-unit/randomization-unit mismatch (clustered SE),
  ratio-metric variance — none exist in detectkit.
- **Ground truth is synthetic** (A/A placebo splits + injected effects), not
  human-labeled incidents.
- **Finite lifecycle:** design → launch → accrue → read out → decision → archive;
  re-runnable full recomputation, not an incremental append. A pre-launch
  power/sample-size planner has no detectkit analog.
- **Output is a readout/decision**, not a paged alert — no cooldown/recovery/quorum.

## 9. Deployment & interface posture

abkit targets analysts who want a **declarative dbt/detectkit-style** workflow with
a **convenient interface**. Three usage surfaces, in priority order:

1. **Local explore cockpit (`abkit explore`) — the PRIORITY interface.** A
   localhost, chart-first cockpit (the detectkit-`tune` port) where the analyst
   runs the pipeline and *plays with* method params live — turning CUPED on,
   changing alpha, stratifying — and watches the cumulative effect+CI stabilization
   chart recompute instantly, with the A/A calibration always visible. This is the
   first thing we build and the experience everything else grows from. It is
   deliberately **web-first and framework-free** (baked payload + a self-contained
   JS renderer, exactly like detectkit's `report.js`/`tune.js`) so the same core
   can later be embedded in a larger app.
2. **Orchestrated runs via Prefect.** abkit is orchestration-friendly by design:
   an analyst schedules a **Prefect** deployment that calls `abkit run`
   (the legacy system already ran on Prefect). The CLI is the unit of automation;
   `init` scaffolds a runnable Prefect flow + deployment example. Results land in
   the warehouse on a cadence with no human in the loop.
3. **BI-agnostic results, connect any BI.** The `_ab_results` contract is a clean,
   stable, documented warehouse table that analysts point **their own BI** at
   (Grafana, **Lightdash**, Metabase, Superset). abkit does not own the dashboard;
   it owns the correct, BI-friendly numbers. ([data-contract-and-reporting.md](data-contract-and-reporting.md))

**Trajectory to a full app.** The explore cockpit + the framework-free renderer +
the BI-agnostic contract are deliberately app-seed-shaped: the same pieces compose
into a future product that integrates **agentic analysis**, **detectkit** + abkit,
and an embedded open-source BI (**Lightdash**). Architectural implication: keep the
renderer/payload split clean and dependency-free, keep `abkit.stats` pure and
importable, and keep the data contract BI-first — so none of this has to be rebuilt
to become an app.

