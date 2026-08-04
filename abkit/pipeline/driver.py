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
(``pipeline/state.py``); WP4's opt-in ``IncrementalBackend`` is the reader
(``compute.incremental_reads``, default off).

Concurrency (§5.7): experiments are independent series — ``run_experiments``
fans them out on a thread pool, ONE manager per worker (DB-API connections
are not thread-safe), locks keeping cross-process runs safe. The M1
Generator-based RNG made the stats core process/thread-safe.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

from abkit.compute.incremental_backend import IncrementalBackend, build_incremental_backend
from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.exposure_counting import bucket_timestamps, count_stream
from abkit.core.period_planner import backlog_seconds, last_due_cutoff, pending_cutoffs
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.exposure_copy import copy_exposures_incremental
from abkit.loaders.exposure_source import build_cohort_backend
from abkit.loaders.query_template import RenderWindow
from abkit.pipeline._types import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    BacklogEntry,
    PipelineStep,
    RunOutcome,
    StageCost,
)
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


@contextmanager
def _stage_cost(outcome: RunOutcome, manager: BaseDatabaseManager, *stages: str) -> Iterator[None]:
    """Accumulate one stage's wall-time + warehouse cost into the outcome.

    The m9 WP5 observability seam. Re-entering the same stage name ADDS to it
    (COMPUTE is entered once per comparison), so the reported number is the
    whole stage, not its last slice.

    PERF-1 passes TWO names for a day-additive look — `"compute"` and the
    derived `"compute.additive"` — so the same measured delta lands in the
    stage total and in its additive slice. They overlap by construction: the
    slice is part of the total, never a sibling of it.
    """
    before = manager.query_cost
    started = perf_counter()
    try:
        yield
    finally:
        elapsed = perf_counter() - started
        delta = manager.query_cost - before
        for stage in stages:
            previous = outcome.stage_costs.get(stage)
            if previous is None:
                outcome.stage_costs[stage] = StageCost(seconds=elapsed, queries=delta)
            else:
                outcome.stage_costs[stage] = StageCost(
                    seconds=previous.seconds + elapsed,
                    queries=previous.queries + delta,
                )


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
    declared_pairs: frozenset[tuple[str, str]] | None = None,
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
    match what this same predicate expects, so the next run plans zero — but
    only over pairs this run can REWRITE. ``declared_pairs`` (m13 STAT-1b) is
    the experiment's contrast set: a pair left behind by a renamed arm, or by a
    family narrowed to ``vs_control``, keeps whatever ``ci_kind`` it was written
    with — no re-plan can supersede a pair nothing recomputes — so counting it
    would make the predicate permanently true and re-plan the FULL series on
    every scheduled run, for rows no surface reads. ``None`` means "judge every
    persisted pair", the pre-STAT-1b behaviour kept for direct callers.
    """
    tau2 = sequential_tau2 or {}
    for pair, kinds in per_pair_kinds.items():
        if not kinds:
            continue
        if declared_pairs is not None and pair not in declared_pairs:
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
    metric_filter: str | None = None,
    log: Logger = _noop_log,
) -> RunOutcome:
    """Run the recompute pipeline for one experiment. Returns the outcome.

    ``resync_cohort`` (m8 §4 Q2 — ``abk run --resync-cohort``) forces the OLD
    full delete + reinsert of the persisted cohort in copy mode (disaster
    recovery for a copy poisoned by the watermark's late-arrival limitation);
    a no-op in direct mode. Never overloads ``--full-refresh``, which keeps
    its results-window semantics.

    ``metric_filter`` (m11 DASH-4a — ``abk run --metric``) narrows the STATE
    stage and the COMPUTE loop to the comparisons of ONE metric, so a
    per-metric recompute neither materializes nor loads what it will not
    compute. It filters by metric NAME — the same grain ``abk validate
    --metric`` uses — which inside one experiment resolves to exactly one
    comparison, because the config binds each metric at most once
    (``ExperimentConfig.validate_comparisons``). Three things stay deliberately
    unfiltered: the cohort resolve and the SRM gate (both experiment-level —
    and the gate must still block), and the alpha scheme, which
    ``effective_alphas`` derives from the CONFIG's comparison list rather than
    from what a given run happens to compute — so a filtered run writes
    byte-identical ``alpha`` values (this WP's #1 assertion).
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

    if metric_filter is not None and not experiment.declares_metric(metric_filter):
        # `abk run` filters the selection before it reaches here (a loud error
        # when NOTHING matches, a printed skip when only some experiments do);
        # this is the same answer for any other API caller — never a lock, a
        # cohort render and an SRM gate for an experiment with nothing to
        # compute.
        outcome.status = "skipped"
        outcome.error = f"no '{metric_filter}' comparison"
        return outcome

    try:
        tables.ensure_tables()
    except Exception as exc:
        # Before the lock exists, so this cannot go through the main handler
        # below — and it must NOT escape as a traceback: the one failure a
        # real operator hits here is the breaking-release schema guard
        # (``ensure_columns`` refusing a NOT-NULL add), whose whole value is
        # the drop-and-recreate remedy it names. Click's standalone mode would
        # print a stack trace and bury it (the M7 WP6 lesson: a message the
        # user must read has to be echoed as a CLI line).
        outcome.status = STATUS_FAILED
        outcome.error = f"{type(exc).__name__}: {exc}"
        return outcome

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
        grid = experiment.grid(limit=project.limits.max_looks)

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
        with _stage_cost(outcome, manager, "load"):
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
            with _stage_cost(outcome, manager, "load"):
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
        # The write half of the incremental engine (WP4's IncrementalBackend
        # is the opt-in reader); runs through the SAME m8 factory backend as
        # the compute loads below — never a hand-rolled cohort join (§0.2).
        # Copy mode clamps day-close to the copy's coverage: the day render
        # joins the persisted copy, and a day materialized from a partial
        # cohort would freeze that way (unlike results, state days are never
        # re-planned); --resync-cohort rebuilds day state with the copy it
        # just rebuilt.
        if PipelineStep.STATE in steps:
            state_watermark = min(watermark_ts, coverage) if copy_enabled else watermark_ts
            with _stage_cost(outcome, manager, "state"):
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
                    # --metric narrows the write side with the read side — with
                    # ONE exception: --resync-cohort rebuilt a cohort EVERY
                    # series was derived from, so its force-rebuild stays
                    # experiment-wide. Narrowing it would leave the other
                    # metrics' day state derived from the copy this run just
                    # declared poisoned, and stale-in-place state is exactly
                    # what the WP4 gap check cannot detect.
                    metric_filter=None if (copy_enabled and resync_cohort) else metric_filter,
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
        # PERF-1: an ABSENT flag is not the same as an explicit `false`. m9
        # shipped the fast path silent, so the hint below nags an undecided
        # project — and writing `incremental_reads: false` records the decision
        # and silences it. `model_fields_set` is what distinguishes the two
        # (the field itself stays a plain bool, so every reader is untouched);
        # an experiment-level override is declared iff it is not None.
        outcome.additive.declared = (
            experiment.incremental_reads is not None
            or "incremental_reads" in project.compute.model_fields_set
        )
        outcome.additive.configured = incremental_reads
        outcome.additive.total_comparisons = len(
            [
                comparison
                for comparison in experiment.comparisons
                if metric_filter is None or comparison.metric == metric_filter
            ]
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
        outcome.additive.enabled = incremental_reads

        # PERF-1: the hook fires at most once per load_cutoff (the reader
        # returns the recompute result immediately after), so counting calls
        # counts CUTOFFS — unlike `on_warning`, which is deduped per (metric,
        # reason) and therefore cannot measure extent. But `load_cutoff` has
        # TWO callers on this backend: the look loop, and the sequential τ²
        # anchor scan, which walks `grid.cutoffs` (NOT `pending`) and runs even
        # when nothing is pending. Counting those too made `fallbacks` exceed
        # `looks_computed` — "fell back for 15 of 14 looks", and a negative
        # served count in --cost-report. So the counter is armed only while the
        # look loop is running.
        counting_looks = False

        def _count_fallback(_metric: str, _kind: str, _end_ts: datetime) -> None:
            if counting_looks:
                outcome.additive.fallbacks += 1

        incremental_backend: IncrementalBackend | None = None
        if incremental_reads:
            # ONE construction path, shared with `abk verify-incremental`
            # (m9 WP5): the command that certifies the incremental read must
            # certify exactly the backend this loop runs.
            incremental_backend = build_incremental_backend(
                manager,
                tables,
                experiment,
                backend,
                snapshot,
                grid,
                project_root=project_root,
                on_warning=outcome.warnings.append,
                on_fallback=_count_fallback,
            )

        # ── PLAN + COMPUTE per comparison (backend built by the WP4 factory) ─
        for comparison in experiment.comparisons:
            # m11 DASH-4a: `--metric` recomputes one metric's series. Filtering
            # the LOOP (never `experiment.comparisons` itself) is what keeps the
            # two-tier alphas invariant — they are derived from the config above.
            if metric_filter is not None and comparison.metric != metric_filter:
                continue
            metric = metrics_by_name[comparison.metric]
            method_config_id = comparison.method.method_config_id
            metric_sql = metric.get_query_text(project_root)
            effective_alpha = comparison_alpha(comparison, alphas)
            # PERF-1 measures eligibility even when the flag is OFF: the whole
            # point of the hint is that a project pays the STATE write for
            # these comparisons and then re-scans the window anyway.
            eligible = comparison_state_eligible(comparison, metric, metric_sql)
            if eligible:
                outcome.additive.eligible_comparisons += 1
            comp_backend: IncrementalBackend | RecomputeBackend = (
                incremental_backend if incremental_backend is not None and eligible else backend
            )
            # Every COMPUTE load for an eligible comparison lands in the stage
            # total AND in the additive slice --cost-report prints (the
            # counterfactual: this is the part the fast path would move off the
            # fact table). The sequential τ² load below is one of them.
            cost_stages = ("compute", "compute.additive") if eligible else ("compute",)

            method_cls = get_method_class(comparison.method.name)
            seq_eligible = experiment.sequential.enabled and method_cls.supports_sequential

            # m13 STAT-1b: complete at (cutoff × declared pair), not merely
            # touched — widening the contrast set adds pairs the historical
            # cutoffs do not carry, and a pair-blind anti-join would leave them
            # missing forever while their siblings kept the narrower family's
            # alpha.
            declared_pairs = experiment.contrast_pairs()
            computed = tables.list_complete_cutoffs(
                experiment.name, metric.name, method_config_id, declared_pairs
            )
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
                # Counted as COMPUTE: it is a real warehouse load on the
                # compute path, and leaving it unattributed made --cost-report
                # (and the perf gate built on it) understate the stage (an R1
                # review finding).
                with _stage_cost(outcome, manager, *cost_stages):
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
                frozenset(declared_pairs),
            ):
                computed = set()
                pending = pending_cutoffs(grid, computed, watermark_ts, full_refresh_window)
                log(
                    f"MODE  {experiment.name}/{metric.name}: sequential mode changed "
                    f"(now {'always_valid' if seq_eligible else 'fixed'}) — re-planning "
                    "the full series"
                )

            outcome.cutoffs_planned += len(pending)
            if eligible:
                # The series the recompute scan is quadratic in: every complete
                # look re-reads the whole window once. Derived from the GRID,
                # never from `computed ∪ pending` — those two are NOT disjoint
                # (`pending_cutoffs` deliberately re-includes an already-
                # computed cutoff inside a --full-refresh window), and
                # `computed` is not intersected with the current grid either,
                # so cutoffs orphaned by a cadence/horizon edit would inflate
                # it. Summing them read a 14-look series as 27 and could nag
                # about a 4-look one.
                outcome.additive.series_looks = sum(
                    1 for cutoff in grid.cutoffs if cutoff.end_ts <= watermark_ts
                )
                outcome.additive.looks_computed += len(pending)
            log(
                f"PLAN  {experiment.name}/{metric.name}: {len(pending)} pending "
                f"of {len(grid)} looks (alpha={effective_alpha:.6g})"
            )
            # threshold on the TAIL segment's cadence: a dense-early schedule
            # that coarsened to daily must not warn forever on its 1h segment
            # measured against the last DUE cutoff, never the watermark: past
            # its horizon an experiment has no more looks to compute, and the
            # watermark keeps moving (see `last_due_cutoff`)
            due_ts = last_due_cutoff(grid, watermark_ts)
            lag = None if due_ts is None else backlog_seconds(computed, due_ts)
            tail_cadence = experiment.cadence_segments()[-1][0]
            if lag is not None and lag > 3 * tail_cadence:
                outcome.warnings.append(
                    f"{experiment.name}/{metric.name}: computed series is "
                    f"{lag / 3600.0:.1f}h behind the looks already due "
                    f"(> 3 cadence steps) — backlog"
                )
                # ONE condition, two consumers (m12 NTF-5): the line above is
                # what the terminal prints, this is what `--notify` routes.
                # Deriving the second from the first would mean parsing prose.
                outcome.backlog.append(BacklogEntry(metric.name, lag))

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
            counting_looks = True
            for look_index, cutoff in enumerate(pending, start=1):
                with _stage_cost(outcome, manager, *cost_stages):
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
            counting_looks = False
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
    metric_filter: str | None = None,
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
        except Exception as exc:
            # The same schema-guard failure `run_experiment` handles, on the
            # pool path: report it as every selected experiment's outcome so the
            # CLI prints the drop-and-recreate remedy instead of a traceback.
            # Missing this second call site left `--workers N>1` over 2+
            # experiments burying the message the release depends on.
            return [
                RunOutcome(
                    experiment=experiment.name,
                    status=STATUS_FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                )
                for _, experiment in experiments
            ]
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
                metric_filter=metric_filter,
                log=log,
            )
        finally:
            manager.close()

    if max_workers <= 1 or len(experiments) <= 1:
        return [_run_one(item) for item in experiments]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_run_one, experiments))
