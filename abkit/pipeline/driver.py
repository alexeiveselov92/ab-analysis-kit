"""The per-experiment pipeline driver + the cross-experiment worker pool.

Stage order per experiment (architecture.md §5), all under ONE ``_ab_tasks``
lock at ``(experiment, "pipeline", "run")`` grain:

    lock → catalog upsert → LOAD exposures → SRM gate → per comparison:
    plan (grid − computed, ≤ watermark) → per cutoff: load → analyze →
    enrich → persist → release

Reliability contract (kept from the reviewed donor):
- the catalog upsert happens INSIDE the locked section (two concurrent runs
  must not race it);
- failures are recorded on the lock row BEFORE propagating; ``BaseException``
  (Ctrl+C, SystemExit) is recorded as failed then RE-RAISED;
- a lock this run did not acquire is never released;
- the watermark is computed ONCE per run in Python (never now() in SQL).

The STATE stage (``_ab_unit_state`` materialization) is deliberately NOT
wired in v1: the read path is recompute (architecture §5.2 "thin
materialization"), so writing day-state would double the warehouse scan for
data nothing reads. The schema, mixins and the §5.2 idempotency invariant are
locked and tested (WP3); the stage activates when v2 flips the read path.

Concurrency (§5.7): experiments are independent series — ``run_experiments``
fans them out on a thread pool, ONE manager per worker (DB-API connections
are not thread-safe), locks keeping cross-process runs safe. The M1
Generator-based RNG made the stats core process/thread-safe.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from abkit.compute.recompute_backend import RecomputeBackend, dialect_of
from abkit.config.experiment_config import ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.period_planner import backlog_seconds, generate_grid, pending_cutoffs
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.exposure_loader import load_exposures
from abkit.loaders.query_template import RenderWindow, build_builtins
from abkit.pipeline._types import STATUS_COMPLETED, STATUS_FAILED, PipelineStep, RunOutcome
from abkit.pipeline.analyze import analyze_cutoff, comparison_alpha, effective_alphas
from abkit.pipeline.enrich import rows_for_cutoff
from abkit.stats import srm_check
from abkit.utils.datetime_utils import now_utc_naive

LOCK_SCOPE = "pipeline"
LOCK_PROCESS = "run"

Logger = Callable[[str], None]


def _noop_log(_: str) -> None:  # pragma: no cover - trivial
    return None


def run_experiment(
    experiment: ExperimentConfig,
    metrics_by_name: dict[str, MetricConfig],
    project: ProjectConfig,
    manager: BaseDatabaseManager,
    tables: InternalTablesManager,
    steps: Sequence[PipelineStep] = tuple(PipelineStep),
    project_root: Path | None = None,
    experiment_path: Path | None = None,
    now_utc: datetime | None = None,
    force: bool = False,
    full_refresh_window: tuple[datetime, datetime] | None = None,
    log: Logger = _noop_log,
) -> RunOutcome:
    """Run the recompute pipeline for one experiment. Returns the outcome."""
    outcome = RunOutcome(experiment=experiment.name)
    steps = list(steps)
    now = now_utc or now_utc_naive()

    if PipelineStep.LOAD not in steps and PipelineStep.COMPUTE not in steps:
        outcome.status = "skipped"
        return outcome

    tables.ensure_tables()
    timeout = project.timeouts.compute
    if not tables.acquire_lock(
        experiment.name, LOCK_SCOPE, LOCK_PROCESS, timeout_seconds=timeout, force=force
    ):
        outcome.status = "locked"
        outcome.error = (
            f"experiment '{experiment.name}' is locked by a running pipeline "
            "(abk unlock clears a stale lock)"
        )
        return outcome

    try:
        # Catalog upsert inside the lock (concurrent runs must not race it).
        alphas = effective_alphas(experiment, project)
        correction = experiment.correction or project.statistics.correction
        tables.upsert_experiment(
            experiment.catalog_record(
                path=str(experiment_path or ""),
                effective_alpha=alphas.alpha,
                effective_correction=correction,
            )
        )

        # ── LOAD: the cohort, once per run (§5.5) ────────────────────────────
        log(f"LOAD  {experiment.name}: loading exposures")
        assignment_sql = experiment.assignment.get_query_text(project_root)
        start_probe = RenderWindow(
            start_ts=datetime.combine(experiment.start_date, datetime.min.time()),
            end_ts=datetime.combine(experiment.end_date, datetime.min.time()) + timedelta(days=1),
        )
        assignment_builtins = build_builtins(
            experiment_id=experiment.name,
            unit_key=experiment.unit_key,
            variants=experiment.assignment.variants,
            added_filters=experiment.assignment.added_filters,
            window=start_probe,
            data_database=manager.data_location,
            internal_database=manager.internal_location,
            exposures_table=project.tables.exposures,
            dialect=dialect_of(manager),
        )
        observed_counts = load_exposures(
            manager, tables, experiment, assignment_sql, assignment_builtins
        )
        outcome.exposures_loaded = sum(observed_counts.values())

        # ── SRM gate: blocking-but-non-dropping (§5.4) ───────────────────────
        srm = srm_check(observed_counts, experiment.assignment.expected_split)
        outcome.srm_flagged = srm.srm_flag
        if srm.srm_flag:
            log(f"SRM   {experiment.name}: {srm.describe()}")
            outcome.warnings.append(srm.describe())

        if PipelineStep.COMPUTE not in steps:
            tables.release_lock(experiment.name, LOCK_SCOPE, LOCK_PROCESS, STATUS_COMPLETED)
            return outcome

        # ── PLAN + COMPUTE per comparison ────────────────────────────────────
        watermark_ts = now - timedelta(seconds=experiment.data_lag_seconds())
        grid = generate_grid(
            experiment.start_date,
            experiment.end_date,
            experiment.cadence_segments(),
            tz=experiment.timezone,
            limit=project.limits.max_looks,
        )
        backend = RecomputeBackend(manager, experiment, exposures_table=project.tables.exposures)

        for comparison in experiment.comparisons:
            metric = metrics_by_name[comparison.metric]
            method_config_id = comparison.method.method_config_id
            metric_sql = metric.get_query_text(project_root)
            effective_alpha = comparison_alpha(comparison, alphas)

            computed = tables.list_computed_cutoffs(experiment.name, metric.name, method_config_id)
            if full_refresh_window is not None:
                tables.delete_results(
                    experiment.name,
                    metric=metric.name,
                    method_config_id=method_config_id,
                    from_ts=full_refresh_window[0],
                    to_ts=full_refresh_window[1],
                    mutations_sync=True,
                )
            pending = pending_cutoffs(grid, computed, watermark_ts, full_refresh_window)
            outcome.cutoffs_planned += len(pending)
            log(
                f"PLAN  {experiment.name}/{metric.name}: {len(pending)} pending "
                f"of {len(grid)} looks (alpha={effective_alpha:.6g})"
            )
            lag = backlog_seconds(computed, watermark_ts)
            if lag is not None and lag > 3 * experiment.cadence_seconds_min():
                outcome.warnings.append(
                    f"{experiment.name}/{metric.name}: computed series trails the "
                    f"watermark by {lag / 3600.0:.1f}h (> 3 cadence steps) — backlog"
                )

            # Orphan detection: >1 stored id per metric = duplicate BI lines.
            stored_ids = {
                mc_id
                for (m, mc_id) in tables.list_method_config_ids(experiment.name, metric.name)
                if m == metric.name
            }
            orphaned = stored_ids - {method_config_id}
            if orphaned:
                outcome.warnings.append(
                    f"{experiment.name}/{metric.name}: {len(orphaned)} orphaned "
                    "method_config_id series in _ab_results (the BI chart will "
                    "show duplicate stabilization lines) — run `abk clean`"
                )

            for cutoff in pending:
                loaded = backend.load_cutoff(comparison, metric, metric_sql, grid, cutoff)
                outcomes = analyze_cutoff(
                    experiment, comparison, metric, loaded, cutoff.end_ts, alphas, project
                )
                rows = rows_for_cutoff(
                    experiment,
                    comparison,
                    metric,
                    outcomes,
                    cutoff,
                    grid,
                    effective_alpha,
                    srm,
                    watermark_ts,
                    metric_query=metric_sql,
                    metric_rendered_query=backend.render(
                        metric_sql, RenderWindow(grid.start_ts, cutoff.end_ts)
                    ),
                )
                outcome.results_written += tables.save_results(rows)
            log(f"RESULT {experiment.name}/{metric.name}: " f"{outcome.results_written} rows total")

    except BaseException as exc:
        # Record the failure on the lock row BEFORE propagating; Ctrl+C /
        # SystemExit are recorded then re-raised (the reviewed donor contract).
        tables.release_lock(
            experiment.name, LOCK_SCOPE, LOCK_PROCESS, STATUS_FAILED, error_message=str(exc)
        )
        if not isinstance(exc, Exception):
            raise
        outcome.status = STATUS_FAILED
        outcome.error = f"{type(exc).__name__}: {exc}"
        return outcome

    tables.release_lock(experiment.name, LOCK_SCOPE, LOCK_PROCESS, STATUS_COMPLETED)
    return outcome


def run_experiments(
    experiments: Sequence[tuple[Path, ExperimentConfig]],
    metrics_by_name: dict[str, MetricConfig],
    project: ProjectConfig,
    manager_factory: Callable[[], BaseDatabaseManager],
    steps: Sequence[PipelineStep] = tuple(PipelineStep),
    project_root: Path | None = None,
    max_workers: int = 1,
    now_utc: datetime | None = None,
    force: bool = False,
    full_refresh_window: tuple[datetime, datetime] | None = None,
    log: Logger = _noop_log,
) -> list[RunOutcome]:
    """Run many experiments, optionally on a worker pool (§5.7).

    ``manager_factory`` builds ONE manager per worker (DB-API connections are
    not thread-safe); the shared ``now_utc`` keeps every experiment's
    watermark consistent within one invocation.
    """
    now = now_utc or now_utc_naive()

    def _run_one(item: tuple[Path, ExperimentConfig]) -> RunOutcome:
        path, experiment = item
        manager = manager_factory()
        try:
            tables = InternalTablesManager(manager)
            return run_experiment(
                experiment,
                metrics_by_name,
                project,
                manager,
                tables,
                steps=steps,
                project_root=project_root,
                experiment_path=path,
                now_utc=now,
                force=force,
                full_refresh_window=full_refresh_window,
                log=log,
            )
        finally:
            manager.close()

    if max_workers <= 1 or len(experiments) <= 1:
        return [_run_one(item) for item in experiments]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_run_one, experiments))
