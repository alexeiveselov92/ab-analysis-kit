"""Shared pipeline types: steps, statuses, outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from abkit.database.manager import QueryCost


class PipelineStep(str, Enum):
    """The selectable ``--steps`` stages (architecture.md §5)."""

    VALIDATE = "validate"
    PLAN = "plan"
    LOAD = "load"
    STATE = "state"
    COMPUTE = "compute"

    @classmethod
    def parse(cls, steps: str) -> list[PipelineStep]:
        """Parse a ``--steps`` string; unknown names raise with the valid list."""
        parsed = []
        for raw in steps.split(","):
            raw = raw.strip().lower()
            if not raw:
                continue
            try:
                parsed.append(cls(raw))
            except ValueError:
                valid = ", ".join(step.value for step in cls)
                raise ValueError(f"unknown step '{raw}' (valid: {valid})") from None
        if not parsed:
            raise ValueError("no steps selected")
        return parsed


#: task-status values in ``_ab_tasks``
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass
class AdditiveReadStatus:
    """Whether this run could take the m9 additive read path — and did (PERF-1).

    The m9 fast path shipped silent: nothing in `abk run` ever mentioned it, so
    the scaffold's own project paid the STATE write and never took the read.
    This is what breaks that silence — collected on every run, turned into the
    CLI hint, and (with ``--cost-report``) printed beside the measured
    day-additive slice of COMPUTE.
    """

    #: the RESOLVED flag for this run — NOT the config value: a
    #: --full-refresh/--resync-cohort without the `state` step turns it off.
    enabled: bool = False
    #: what the CONFIG asked for, before that force-off. When this is true and
    #: `enabled` is false, the run disabled the path for its own reasons and
    #: must not advise setting a flag that is already set.
    configured: bool = False
    #: did any config level state a preference at all? An absent flag is the
    #: undecided m9 default the hint nags about; an explicit ``false`` is a
    #: recorded decision and stays quiet.
    declared: bool = False
    #: comparisons on the additive contract (``comparison_state_eligible``),
    #: i.e. the ones the STATE stage materializes day moments for
    eligible_comparisons: int = 0
    total_comparisons: int = 0
    #: the longest eligible series this run touched (computed ∪ pending).
    #: LOOKS, not days: the recompute scan is quadratic in looks — an hourly
    #: cadence re-reads the window 24× a day (cumulative-intervals §4.1 states
    #: the same threshold in days because it assumes a daily grid).
    series_looks: int = 0
    #: eligible cutoffs this run actually computed, and how many of those the
    #: reader had to fall back to full recompute for (``enabled`` only)
    looks_computed: int = 0
    fallbacks: int = 0

    #: Restraint, NOT a break-even: the measured crossover is at one look
    #: (cumulative-intervals §4.2 — the additive path already scans 60% fewer
    #: fact rows at two looks, so §4.1's "within noise below ~5" is wrong). We
    #: stay quiet until the ABSOLUTE saving is worth an operator's attention.
    MIN_LOOKS_TO_MATTER = 6

    def hint(self) -> str | None:
        """The one line `abk run` prints about the additive read path, if any.

        Pure and name-free — the CLI renders it under the experiment it
        belongs to. Returns None when there is nothing honest to say, which
        is the common case: a project that declared its choice either way and
        is getting what it asked for hears nothing.
        """
        if self.enabled:
            if self.eligible_comparisons == 0 and self.total_comparisons > 0:
                return (
                    "compute.incremental_reads is on, but no comparison in this run is "
                    "on the additive contract — every look still re-scans the full "
                    "window. Eligibility needs `state_additive: true` on the metric AND "
                    "a closed-form unstratified comparison with no explicit covariate "
                    "(see `abk verify-incremental` for the empirical check)."
                )
            if self.fallbacks:
                # The reader's own warnings name the REASON but are deduped per
                # (metric, reason), so only this can say how much it cost.
                return (
                    f"incremental reads fell back to full recompute for {self.fallbacks} "
                    f"of {self.looks_computed} looks this run (reasons above) — those "
                    "looks paid the recompute scan on top of the state read."
                )
            return None
        if self.configured:
            # The config asked for the fast path and this run disabled it; the
            # driver has already said why. Telling the operator to set a flag
            # they have set would be worse than silence.
            return None
        if self.declared or not self.eligible_comparisons:
            return None
        if self.series_looks < self.MIN_LOOKS_TO_MATTER:
            return None
        return (
            f"{self.eligible_comparisons} of {self.total_comparisons} comparisons are "
            f"day-additive and their series is {self.series_looks} looks long, but "
            "compute.incremental_reads is unset (default off): the `state` step "
            "materializes their day moments and COMPUTE re-scans the full window "
            "anyway. `abk verify-incremental` certifies the fast path; set "
            "compute.incremental_reads to true to take it, or to false to record the "
            "decision and silence this."
        )


@dataclass(frozen=True)
class BacklogEntry:
    """One metric series that trailed its DUE looks when this run PLANNED it.

    The same condition the §6.4 backlog warning already reports, recorded as
    data as well as prose (m12 NTF-5 routes it as the `stale` signal). The
    structure is not decoration: the warning string carries the lag in hours,
    so a notifier deduping on the sentence would re-announce on every run as
    that number drifts, while the metric NAME is what actually changed or did
    not.

    Strictly RETROSPECTIVE, and the notice says so. Detection sits in the PLAN
    stage of a run that goes on to compute every pending look, so by the time
    the message is delivered the gap it reports is closed — what it tells the
    operator is that the schedule slipped (runs failed, were locked out, or
    never fired), not that the warehouse is behind right now. A "still stale"
    variant would need a condition nothing in the pipeline measures.
    """

    metric: str
    lag_seconds: float


@dataclass
class RunOutcome:
    """One experiment's run summary (the driver's return value)."""

    experiment: str
    status: str = "completed"  # completed | failed | locked | skipped
    error: str | None = None
    exposures_loaded: int = 0
    #: per-variant deduped unit counts from this run's validated snapshot
    #: (m8 WP4) — lets `--report` reuse them instead of re-executing the
    #: assignment source; empty when the LOAD stage did not run
    exposure_counts: dict[str, int] = field(default_factory=dict)
    srm_flagged: bool = False
    cutoffs_planned: int = 0
    results_written: int = 0
    #: closed (metric, day) renders replaced into ``_ab_unit_state`` this run
    #: (m9 WP3 — the write-only STATE stage; 0 when the stage did not run)
    state_days_materialized: int = 0
    warnings: list[str] = field(default_factory=list)
    #: metric series more than 3 cadence steps behind the looks already DUE
    #: when this run planned them (m12 NTF-5 — the `stale` signal's data half;
    #: the prose half is the matching ``warnings`` line, unchanged)
    backlog: list[BacklogEntry] = field(default_factory=list)
    #: per-stage warehouse cost, keyed by stage name (m9 WP5 — the evidence
    #: behind ``abk run --cost-report`` and the incremental-read default-flip
    #: decision). Always collected (the counters are ~free); the flag only
    #: decides whether the CLI PRINTS them. PERF-1 adds the derived key
    #: ``compute.additive`` — the day-additive SLICE of ``compute``, so the
    #: two are NOT disjoint and must never be summed.
    stage_costs: dict[str, StageCost] = field(default_factory=dict)
    #: PERF-1: what this run learned about the m9 additive read path.
    additive: AdditiveReadStatus = field(default_factory=AdditiveReadStatus)


@dataclass(frozen=True)
class StageCost:
    """One pipeline stage's cost: wall-time plus the manager's query deltas."""

    seconds: float
    queries: QueryCost

    def describe(self) -> str:
        """One human line — honest about what each backend can measure."""
        scans = (
            f", {self.queries.scanned_rows:,} rows scanned"
            if self.queries.scan_stats
            else ", scans n/a"
        )
        return (
            f"{self.seconds:.2f}s, {self.queries.queries} queries, "
            f"{self.queries.rows:,} rows returned{scans}"
        )
