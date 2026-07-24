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

The STATE stage (``_ab_unit_state`` materialization, m9 WP3) is the
write-only half of cumulative-intervals.md §4's v1 strategy: after LOAD,
every STATE-eligible metric's not-yet-materialized closed days are rendered
through the SAME m8 cohort backend and replaced into ``_ab_unit_state``
(``pipeline/state.py``). The read path stays recompute until WP4's
``IncrementalBackend`` flips it.

Concurrency (§5.7): experiments are independent series — ``run_experiments``
fans them out on a thread pool, ONE manager per worker (DB-API connections
are not thread-safe), locks keeping cross-process runs safe. The M1
Generator-based RNG made the stats core process/thread-safe.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from abkit.compute.incremental_backend import IncrementalBackend
from abkit.config.experiment_config import ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.exposure_counting import bucket_timestamps, count_stream
from abkit.core.period_planner import backlog_seconds, generate_grid, pending_cutoffs
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.exposure_copy import copy_exposures_incremental
from abkit.loaders.exposure_source import build_cohort_backend
from abkit.loaders.query_template import RenderWindow
from abkit.pipeline._types import STATUS_COMPLETED, STATUS_FAILED, PipelineStep, RunOutcome
from abkit.pipeline.analyze import analyze_cutoff, comparison_alpha, effective_alphas
from abkit.pipeline.enrich import rows_for_cutoff
from abkit.pipeline.state import comparison_state_eligible, materialize_state
from abkit.stats import (
    DEFAULT_SRM_ALPHA,
    SrmResult,
    get_method_class,
    sequential_multinomial_srm,
    srm_check,
)
from abkit.stats.sequential import mixture_tau2, se_from_ci_length
from abkit.utils.datetime_utils import now_utc_naive

LOCK_SCOPE = "pipeline"
LOCK_PROCESS = "run"

Logger = Callable[[str], None]


def _noop_log(_: str) -> None:  # pragma: no cover - trivial
    return None


def _sequential_tau2(
    backend,
    experiment,
    comparison,
    metric,
    metric_sql,
    grid,
    alphas,
    project,
    effective_alpha: float,
) -> dict[tuple[str, str], float]:
    """Per-pair mixture variance τ² from the FIRST usable grid cutoff (M5 WP3, D-Seq-anchor).

    τ² is anchored to the earliest look with a usable fixed CI: scan the grid from the
    start, running the fixed analysis (``sequential_tau2=None``), and return
    ``{(name_1, name_2): tau2}`` from the first cutoff that yields usable pairs — stable
    across runs (the first look is idempotent) and computable live (no horizon data
    needed). Empty when the method is sequential-ineligible or no look is usable, so the
    series stays fixed. One extra cutoff load per comparison per run (normally the first).
    """
    method_cls = get_method_class(comparison.method.name)
    if not method_cls.supports_sequential:
        return {}
    for cutoff in grid.cutoffs:
        loaded = backend.load_cutoff(comparison, metric, metric_sql, grid, cutoff)
        outcomes = analyze_cutoff(
            experiment, comparison, metric, loaded, cutoff.end_ts, alphas, project
        )
        tau2: dict[tuple[str, str], float] = {}
        for outcome in outcomes:
            if outcome.result is None:
                continue
            se = se_from_ci_length(outcome.result.ci_length, effective_alpha)
            if math.isfinite(se) and se > 0.0:
                tau2[(outcome.name_1, outcome.name_2)] = mixture_tau2(se * se, effective_alpha)
        if tau2:
            return tau2
    return {}


def _sequential_mode_changed(
    per_pair_kinds: dict[tuple[str, str], set[str]],
    seq_eligible: bool,
    sequential_tau2: dict[tuple[str, str], float] | None,
) -> bool:
    """Does the persisted series' ``ci_kind`` disagree with the mode this run stamps?

    The toggle self-invalidation predicate (M5 WP3, plan B4). Per pair, the mode
    this run would stamp is ``always_valid`` iff the experiment's sequential mode
    is on, the method supports it (``seq_eligible``), AND this pair has a frozen
    τ² — i.e. it is exactly the condition under which ``analyze_cutoff`` widens
    the pair (a pair usable only after the first-usable-look anchor is legitimately
    left ``fixed``). Any persisted non-demoted row of a different kind means the
    ``sequential.enabled`` toggle flipped since the series was written, so the
    driver force-re-plans it. Idempotent: after the re-plan the persisted kinds
    match what this same predicate expects, so the next run plans zero.
    """
    tau2 = sequential_tau2 or {}
    for pair, kinds in per_pair_kinds.items():
        if not kinds:
            continue
        expected = "always_valid" if (seq_eligible and pair in tau2) else "fixed"
        if kinds != {expected}:
            return True
    return False


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
    resync_cohort: bool = False,
    log: Logger = _noop_log,
) -> RunOutcome:
    """Run the recompute pipeline for one experiment. Returns the outcome.

    ``resync_cohort`` (m8 §4 Q2 — ``abk run --resync-cohort``) forces the OLD
    full delete + reinsert of the persisted cohort in copy mode (disaster
    recovery for a copy poisoned by the watermark's late-arrival limitation);
    a no-op in direct mode. Never overloads ``--full-refresh``, which keeps
    its results-window semantics.
    """
    outcome = RunOutcome(experiment=experiment.name)
    steps = list(steps)
    now = now_utc or now_utc_naive()

    if (
        PipelineStep.LOAD not in steps
        and PipelineStep.STATE not in steps
        and PipelineStep.COMPUTE not in steps
    ):
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

        # The grid is the single source of the experiment's window bounds —
        # the exposure load below must use the SAME tz-snapped edges the
        # analysis windows use, never naive calendar midnights.
        grid = generate_grid(
            experiment.start_date,
            experiment.end_date,
            experiment.cadence_segments(),
            tz=experiment.timezone,
            limit=project.limits.max_looks,
        )

        # The compute watermark (cutoffs pend iff end_ts ≤ it) — computed once
        # per run, never now() in SQL (§6.2); the copy-coverage check below
        # and the SRM gate both need it.
        watermark_ts = now - timedelta(seconds=experiment.data_lag_seconds())

        # ── LOAD: the cohort source, once per run (§5.5; m8 WP4) ────────────
        # ONE factory call decides copy-vs-direct for the whole run: the
        # compute backend below and the SRM counts here read the same
        # validated source. Direct mode (the default) never writes
        # ``_ab_exposures``; copy mode appends incrementally (the m8 WP5
        # watermark/closed-interval engine), or full-reloads under
        # ``--resync-cohort`` (disaster recovery, §4 Q2).
        log(f"LOAD  {experiment.name}: loading exposures")
        backend, snapshot = build_cohort_backend(
            manager, experiment, project_root, grid, with_snapshot=True
        )
        assert snapshot is not None  # with_snapshot=True always renders one
        copy_enabled = experiment.assignment.cohort_copy.enabled
        if copy_enabled:
            if resync_cohort:
                # Rebuild THROUGH the incremental engine (delete + reload from
                # the experiment start): one write path, so the resync honors
                # the same closed/matured discipline as routine operation — an
                # ungated snapshot rewrite would persist unmatured rows and
                # advance the watermark past what the engine ever produces
                # (review rounds 1+2). The from-scratch re-scan is also what
                # HEALS a copy poisoned by the late-arrival limitation.
                log(
                    f"LOAD  {experiment.name}: --resync-cohort — rebuilding the "
                    "persisted cohort (delete + incremental reload)"
                )
                tables.delete_exposures(experiment.name)
            copy_result = copy_exposures_incremental(
                manager,
                tables,
                experiment,
                project_root,
                grid,
                now=now,
                has_stratum=snapshot.has_stratum,
                log=log,
            )
            # Freshness disclosure (m8 WP5 risk note): metrics join the
            # persisted copy, which only covers CLOSED, matured intervals
            # (the SRM counts below deliberately stay on the LIVE validated
            # snapshot — randomization health is measured at the source).
            # A cutoff computed past that coverage reads a partial cohort
            # and stays frozen that way (recompute never revisits a
            # computed cutoff) — warn iff a computable cutoff exceeds it.
            coverage = copy_result.covered_through or grid.start_ts
            last_computable = max(
                (c.end_ts for c in grid.cutoffs if c.end_ts <= watermark_ts),
                default=None,
            )
            if last_computable is not None and coverage < last_computable:
                outcome.warnings.append(
                    f"cohort copy trails the compute watermark: exposures "
                    f"copied through {coverage:%Y-%m-%d %H:%M:%S}, cutoffs "
                    f"computed through {last_computable:%Y-%m-%d %H:%M:%S} — "
                    "cutoffs in between see a partial cohort; set data_lag >= "
                    "cohort_copy.maturity_delay + batch_interval to align"
                )
        elif resync_cohort:
            log(
                f"LOAD  {experiment.name}: --resync-cohort has no effect in "
                "direct mode (no persisted cohort)"
            )
        observed_counts = dict(snapshot.counts)
        outcome.exposures_loaded = sum(observed_counts.values())
        outcome.exposure_counts = dict(observed_counts)

        # ── SRM gate: blocking-but-non-dropping (§5.4) ───────────────────────
        # Sub-day evaluates every COMPLETE look (end_ts ≤ the watermark above).
        srm_by_cutoff: dict[datetime, SrmResult] | None = None
        if experiment.is_sub_day():
            # Sub-day: a dense cadence peeks the χ² hard gate dozens of times a
            # day → false alarms. Swap to the anytime-valid Dirichlet-multinomial
            # e-process (Lindon & Malek 2022; statistics-changes.md §4.2), valid
            # at EVERY look by construction. ONE verdict per look, stamped on
            # that look's rows (the truthful as-of SRM, cumulative-intervals.md
            # §6.5); the run headline is the latest complete look's running
            # verdict. The gate is NOT gated by demotion — counts/SRM stay
            # visible even where inference is withheld (§6.1(4)).
            looks = [c.end_ts for c in grid.cutoffs if c.end_ts <= watermark_ts]
            variants = list(experiment.assignment.variants)
            if copy_enabled:
                stream = tables.get_exposure_count_stream(experiment.name, looks, variants)
            else:
                # direct mode: the persisted copy does not exist — bucket the
                # in-memory snapshot through the SAME core.exposure_counting
                # math the mixin uses (one bisect implementation, WP4 step 4)
                per_variant = bucket_timestamps(
                    ((variant, ts) for variant, ts, _ in snapshot.by_unit.values()), variants
                )
                stream = count_stream(per_variant, looks, variants)
            look_results = sequential_multinomial_srm(stream, experiment.assignment.expected_split)
            srm_by_cutoff = dict(zip(looks, look_results, strict=True))
            srm = (
                look_results[-1]
                if look_results
                # no complete look yet ⇒ nothing to gate or write; a benign ok.
                else SrmResult(
                    pvalue=1.0,
                    srm_flag=False,
                    alpha=DEFAULT_SRM_ALPHA,
                    kind="sequential_multinomial",
                    e_value=1.0,
                )
            )
        else:
            # Daily & coarser keep the χ² gate (a bounded daily look count on the
            # strict 0.001 hard gate ⇒ negligible peeking inflation). Zero-fill
            # declared variants absent from the cohort: a missing arm is the
            # worst SRM there is — it must FLAG, not crash the chi-square.
            observed_counts = {
                variant: observed_counts.get(variant, 0)
                for variant in experiment.assignment.variants
            }
            srm = srm_check(observed_counts, experiment.assignment.expected_split)
        outcome.srm_flagged = srm.srm_flag
        if srm.srm_flag:
            log(f"SRM   {experiment.name}: {srm.describe()}")
            outcome.warnings.append(srm.describe())

        # ── STATE: per-(unit, day) moment materialization (m9 WP3) ───────────
        # Write-only in this milestone (the read path stays recompute until
        # WP4 flips it); runs through the SAME m8 factory backend as the
        # compute loads below — never a hand-rolled cohort join (m9 §0.2).
        # Copy mode clamps day-close to the copy's coverage: the day render
        # joins the persisted copy, and a day materialized from a partial
        # cohort would freeze that way (unlike results, state days are never
        # re-planned); --resync-cohort rebuilds day state with the copy it
        # just rebuilt.
        if PipelineStep.STATE in steps:
            state_watermark = min(watermark_ts, coverage) if copy_enabled else watermark_ts
            state_outcome = materialize_state(
                tables,
                experiment,
                metrics_by_name,
                backend,
                grid,
                state_watermark,
                project_root=project_root,
                full_refresh_window=full_refresh_window,
                force_rebuild=copy_enabled and resync_cohort,
                log=log,
            )
            outcome.state_days_materialized = state_outcome.days_materialized
            outcome.warnings.extend(state_outcome.warnings)

        if PipelineStep.COMPUTE not in steps:
            tables.release_lock(experiment.name, LOCK_SCOPE, LOCK_PROCESS, STATUS_COMPLETED)
            return outcome

        # ── The m9 WP4 read-path resolver: opt-in, per comparison ────────────
        # STATE-eligible comparisons (the SAME predicate the WP3 writer uses —
        # bootstrap/stratified/explicit-covariate always stay recompute) read
        # `_ab_unit_state` when the experiment/project opts in; any state gap
        # falls back inside the backend. The flag changes HOW a number is
        # computed, never the number (m9 §0.1) — with it off nothing below
        # this block changes.
        incremental_reads = (
            experiment.incremental_reads
            if experiment.incremental_reads is not None
            else project.compute.incremental_reads
        )
        # A skipped STATE step can only leave day state ABSENT (the gap
        # fallback's territory) — except under --full-refresh/--resync-cohort,
        # which re-plan results while leaving already-materialized days
        # IN-PLACE STALE (a backfilled window / a rebuilt copy). Staleness is
        # undetectable by the gap check, so those runs force recompute.
        if (
            incremental_reads
            and PipelineStep.STATE not in steps
            and (full_refresh_window is not None or resync_cohort)
        ):
            outcome.warnings.append(
                f"{experiment.name}: incremental reads disabled for this run — "
                "--full-refresh/--resync-cohort without the 'state' step would "
                "read day state the refresh made stale; include 'state' in "
                "--steps to re-materialize it"
            )
            incremental_reads = False
        incremental_backend: IncrementalBackend | None = None
        if incremental_reads:
            snap = snapshot

            def _cohort_variant_map() -> dict[str, str]:
                # Copy mode joins the persisted cohort (what the renders see);
                # direct mode reuses this run's validated LOAD snapshot.
                if copy_enabled:
                    return tables.get_exposure_variant_map(experiment.name)
                return {str(unit): variant for unit, (variant, _, _) in snap.by_unit.items()}

            incremental_backend = IncrementalBackend(
                tables,
                backend,
                experiment,
                variant_map_loader=_cohort_variant_map,
                project_root=project_root,
                on_warning=outcome.warnings.append,
            )

        # ── PLAN + COMPUTE per comparison (backend built by the WP4 factory) ─
        for comparison in experiment.comparisons:
            metric = metrics_by_name[comparison.metric]
            method_config_id = comparison.method.method_config_id
            metric_sql = metric.get_query_text(project_root)
            effective_alpha = comparison_alpha(comparison, alphas)
            comp_backend = (
                incremental_backend
                if incremental_backend is not None
                and comparison_state_eligible(comparison, metric, metric_sql)
                else backend
            )

            method_cls = get_method_class(comparison.method.name)
            seq_eligible = experiment.sequential.enabled and method_cls.supports_sequential

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

            # M5 WP3: freeze τ² once per comparison, anchored to the first usable
            # look (D-Seq-anchor), so every cutoff's always-valid CI shares one
            # mixing prior. It is computed here (not lazily) because it also
            # classifies which pairs SHOULD be always_valid when checking the
            # persisted series for a sequential-mode toggle. Cost: one first-look
            # load per sequential comparison per run (the accepted anytime price).
            sequential_tau2: dict[tuple[str, str], float] | None = None
            if seq_eligible and (pending or computed):
                sequential_tau2 = _sequential_tau2(
                    comp_backend,
                    experiment,
                    comparison,
                    metric,
                    metric_sql,
                    grid,
                    alphas,
                    project,
                    effective_alpha,
                )

            # M5 WP3 (B4): the toggle self-invalidates. ``sequential.enabled`` is
            # (correctly) not in ``method_config_id``, so the anti-join would skip
            # a flipped-but-fully-computed series and leave stale rows. When the
            # persisted ci_kind disagrees with the mode this run stamps, force a
            # re-plan of the whole series: dropping ``computed`` re-plans every
            # complete cutoff, and the re-saved rows supersede the stale ones by LWW
            # (same PK — ci_kind is not identity-bearing — newer ``created_at``;
            # FINAL/argMax reads collapse to the new rows on every backend). We do
            # NOT delete first: a delete-all would strand any cutoff that a widened
            # ``data_lag`` pushed past the watermark this run (it would be removed
            # but not re-planned), whereas LWW leaves such a cutoff untouched.
            if computed and _sequential_mode_changed(
                tables.series_pair_ci_kinds(experiment.name, metric.name, method_config_id),
                seq_eligible,
                sequential_tau2,
            ):
                computed = set()
                pending = pending_cutoffs(grid, computed, watermark_ts, full_refresh_window)
                log(
                    f"MODE  {experiment.name}/{metric.name}: sequential mode changed "
                    f"(now {'always_valid' if seq_eligible else 'fixed'}) — re-planning "
                    "the full series"
                )

            outcome.cutoffs_planned += len(pending)
            log(
                f"PLAN  {experiment.name}/{metric.name}: {len(pending)} pending "
                f"of {len(grid)} looks (alpha={effective_alpha:.6g})"
            )
            # threshold on the TAIL segment's cadence: a dense-early schedule
            # that coarsened to daily must not warn forever on its 1h segment
            lag = backlog_seconds(computed, watermark_ts)
            tail_cadence = experiment.cadence_segments()[-1][0]
            if lag is not None and lag > 3 * tail_cadence:
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

            # Heartbeat so a large pending series is not a silent multi-minute freeze
            # (each look is one full-window warehouse query, + bootstrap resampling for
            # bootstrap methods). Throttled to ~20 lines so a dense sub-day grid stays
            # readable; the final look always prints.
            n_pending = len(pending)
            beat_every = max(1, n_pending // 20)
            for look_index, cutoff in enumerate(pending, start=1):
                loaded = comp_backend.load_cutoff(comparison, metric, metric_sql, grid, cutoff)
                outcomes = analyze_cutoff(
                    experiment,
                    comparison,
                    metric,
                    loaded,
                    cutoff.end_ts,
                    alphas,
                    project,
                    sequential_tau2=sequential_tau2,
                )
                # sub-day stamps each look its OWN anytime-valid verdict; daily &
                # coarser broadcast the one whole-cohort χ² gate to every row.
                cutoff_srm = (
                    srm_by_cutoff.get(cutoff.end_ts, srm) if srm_by_cutoff is not None else srm
                )
                rows = rows_for_cutoff(
                    experiment,
                    comparison,
                    metric,
                    outcomes,
                    cutoff,
                    grid,
                    effective_alpha,
                    cutoff_srm,
                    watermark_ts,
                    metric_query=metric_sql,
                    metric_rendered_query=backend.render(
                        metric_sql, RenderWindow(grid.start_ts, cutoff.end_ts)
                    ),
                )
                outcome.results_written += tables.save_results(rows)
                if n_pending > 1 and (look_index % beat_every == 0 or look_index == n_pending):
                    log(
                        f"LOOK  {experiment.name}/{metric.name}: "
                        f"{look_index}/{n_pending} looks computed"
                    )
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

    if not tables.release_lock(experiment.name, LOCK_SCOPE, LOCK_PROCESS, STATUS_COMPLETED):
        outcome.warnings.append(
            f"{experiment.name}: the run outlived its lock timeout and the lock "
            "was taken over — this run's tail may have interleaved with the new "
            "owner (raise timeouts.compute)"
        )
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
    resync_cohort: bool = False,
    log: Logger = _noop_log,
) -> list[RunOutcome]:
    """Run many experiments, optionally on a worker pool (§5.7).

    ``manager_factory`` builds ONE manager per worker (DB-API connections are
    not thread-safe); the shared ``now_utc`` keeps every experiment's
    watermark consistent within one invocation.
    """
    now = now_utc or now_utc_naive()

    if max_workers > 1 and len(experiments) > 1:
        # Serialize the first-run DDL: concurrent CREATE SCHEMA/TABLE IF NOT
        # EXISTS intermittently races on PostgreSQL (unique-violation on the
        # catalog); one up-front ensure_tables makes the pool's calls no-ops.
        bootstrap = manager_factory()
        try:
            InternalTablesManager(bootstrap).ensure_tables()
        finally:
            bootstrap.close()

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
                resync_cohort=resync_cohort,
                log=log,
            )
        finally:
            manager.close()

    if max_workers <= 1 or len(experiments) <= 1:
        return [_run_one(item) for item in experiments]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_run_one, experiments))
