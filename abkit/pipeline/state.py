"""The STATE stage: per-(unit, day) moment materialization (m9 WP3).

The write-only half of cumulative-intervals.md §4's committed v1 strategy:
for every STATE-eligible metric, each not-yet-materialized closed local day
``[tz-midnight(d), tz-midnight(d+1))`` is rendered through the m8 cohort
factory backend (a single-day, non-cumulative window — never a hand-rolled
cohort join, m9 §0.2) and replaced into ``_ab_unit_state`` via the §5.2
replace-not-sum primitive. No reader exists in this milestone — WP4's
``IncrementalBackend`` flips the read path.

Eligibility (per metric, within one experiment):

- non-stratified — no stratum dimension exists in the state key (m9 §8 Q2);
- referenced by at least one closed-form (unseeded) comparison — a
  bootstrap-only metric never pays the write cost (WP3 step 5);
- the SQL body does not reference ``ab_cov_*`` — such a render depends on
  the comparison's covariate window, so its day moments would not be
  comparison-independent; the metric stays on full-window recompute;
- no explicit ``columns.covariate`` role (an R2 review exclusion) — that
  role is documented as an author-computed column that may be a static
  per-unit snapshot, which is NOT additive across day renders: per-day
  ``sum_cov`` rows would inflate by the unit's active-day count once
  summed. Additivity cannot be verified from config, so such metrics stay
  on full-window recompute.

Contiguity invariant (the WP4 gap-detection contract): days advance strictly
in order and a failed day TRUNCATES the series from that day before the run
aborts the metric's advancement, so every day ``<= get_last_state_day()`` is
materialized (possibly legitimately empty — trailing empty days are
re-rendered each run, the accepted v1 cost) and days past it are absent, not
stale. Non-finite moments truncate from the failing day with a loud warning
— reads past the last valid day stay on full recompute, never a
partially-written or stale day the future reader would trust.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.core.period_planner import Grid, tz_midnight_utc
from abkit.database.internal_tables import (
    InternalTablesManager,
    compute_metric_state_id,
    compute_state_source_id,
)
from abkit.loaders.query_template import RenderWindow
from abkit.loaders.state_loader import StateMomentError, day_moments
from abkit.stats import get_method_class

Logger = Callable[[str], None]


def _noop_log(_: str) -> None:  # pragma: no cover - trivial
    return None


@dataclass(frozen=True)
class StateDay:
    """One closed local day and its UTC render window."""

    day: date
    window: RenderWindow


@dataclass
class StateOutcome:
    """One experiment's STATE-stage summary."""

    days_materialized: int = 0
    rows_written: int = 0
    warnings: list[str] = field(default_factory=list)


def closed_state_days(
    experiment: ExperimentConfig, grid: Grid, watermark_ts: datetime
) -> list[StateDay]:
    """The closed local days of the grid window, in order.

    A day is closed iff its full window ``[tz-midnight(d), tz-midnight(d+1))``
    — clamped to the grid horizon — has passed the compute watermark (§6.4:
    the state stage advances only at day close; the same
    ``end_ts <= watermark_ts`` completeness rule the cutoff planner uses).
    """
    zone = ZoneInfo(experiment.timezone)
    days: list[StateDay] = []
    day = experiment.start_date
    while True:
        window_start = tz_midnight_utc(day, zone)
        if window_start >= grid.horizon_ts:
            break
        window_end = min(tz_midnight_utc(day + timedelta(days=1), zone), grid.horizon_ts)
        if window_end > watermark_ts:
            break
        days.append(StateDay(day=day, window=RenderWindow(window_start, window_end)))
        day += timedelta(days=1)
    return days


def _needs_seed(method_name: str) -> bool:
    method_cls = get_method_class(method_name)
    return any(spec.name == "seed" for spec in method_cls.param_specs)


def _cohort_identity(experiment: ExperimentConfig, project_root: Path | None) -> dict[str, Any]:
    """The cohort-shaping config folded into the series identity (R1 fix).

    Everything here changes which units (or which day boundaries) the per-day
    render sees; an edit therefore orphans the series and re-materializes it
    under the current definition — the same self-consistency the full-window
    recompute gets for free by re-rendering the whole window every cutoff.
    """
    assignment_sql = experiment.assignment.get_query_text(project_root)
    identity: dict[str, Any] = {
        "assignment_sql_sha256": hashlib.sha256(
            " ".join(assignment_sql.split()).encode("utf-8")
        ).hexdigest(),
        "added_filters": experiment.assignment.added_filters,
        "unit_key": experiment.unit_key,
        "variants": list(experiment.assignment.variants),
        "timezone": experiment.timezone,
        "start_date": str(experiment.start_date),
    }
    # The assignment render's window ends at the GRID HORIZON, so a cohort
    # SQL referencing ab_end_date/ab_end_ts renders differently when
    # end_date moves (R2 review) — fold end_date in for exactly those SQLs.
    # End-invariant assignment SQL (the common case) skips it, so the most
    # routine edit there is — extending an experiment — never orphans state.
    if "ab_end_" in assignment_sql:
        identity["end_date"] = str(experiment.end_date)
    return identity


def state_series_key(
    experiment: ExperimentConfig,
    metric: MetricConfig,
    metric_sql: str,
    project_root: Path | None = None,
) -> tuple[str, str]:
    """The ``(source_table, column_set_id)`` key of one metric's state series.

    THE identity function (WP3 steps 1-2, as corrected in review) — the WP4
    reader and every test must derive the key through here, never by
    composing the hash inputs by hand.
    """
    source_id = compute_state_source_id(experiment.name, metric.name)
    series_id = compute_metric_state_id(
        metric.columns.role_map(),
        metric_sql,
        cohort_config=_cohort_identity(experiment, project_root),
    )
    return source_id, series_id


def state_eligible_metrics(
    experiment: ExperimentConfig,
    metrics_by_name: dict[str, MetricConfig],
    project_root: Path | None,
) -> list[tuple[MetricConfig, str]]:
    """The ``(metric, metric_sql)`` list this run materializes.

    One series per metric: the experiment config binds each metric to at
    most one comparison, so the comparison's method decides eligibility.
    """
    chosen: dict[str, tuple[MetricConfig, str]] = {}
    for comparison in experiment.comparisons:
        if _needs_seed(comparison.method.name):
            continue
        metric = metrics_by_name[comparison.metric]
        if metric.columns.stratum is not None:
            continue
        if metric.columns.covariate is not None:
            continue
        metric_sql = metric.get_query_text(project_root)
        if "ab_cov_" in metric_sql:
            continue
        chosen[metric.name] = (metric, metric_sql)
    return list(chosen.values())


def materialize_state(
    tables: InternalTablesManager,
    experiment: ExperimentConfig,
    metrics_by_name: dict[str, MetricConfig],
    backend: RecomputeBackend,
    grid: Grid,
    watermark_ts: datetime,
    project_root: Path | None = None,
    full_refresh_window: tuple[datetime, datetime] | None = None,
    force_rebuild: bool = False,
    log: Logger = _noop_log,
) -> StateOutcome:
    """Materialize every pending closed day for the eligible metrics.

    ``full_refresh_window`` (``abk run --full-refresh --from/--to``) forces
    re-materialization from the first day its half-open window touches
    through the end of the series (truncate-then-advance — see the inline
    comment) — otherwise a backfill would leave stale day state the WP4
    reader would trust (WP3 step 6).

    ``force_rebuild`` (``abk run --resync-cohort``, copy mode): day state was
    derived from the persisted copy the resync just declared poisoned, so it
    must not outlive it — every eligible series is dropped and re-rendered
    from the rebuilt cohort.

    In copy mode the caller passes ``watermark_ts`` CLAMPED to the copy's
    coverage: the day render joins the persisted copy, and a day
    materialized from a partial cohort would freeze that way (state days,
    unlike results cutoffs, are never re-planned once written).
    """
    outcome = StateOutcome()
    days = closed_state_days(experiment, grid, watermark_ts)
    if not days:
        return outcome

    for metric, metric_sql in state_eligible_metrics(experiment, metrics_by_name, project_root):
        source_id, series_id = state_series_key(experiment, metric, metric_sql, project_root)

        # The identity invalidation (WP3 step 2 + the R1 cohort fix): an
        # edited SQL body, role map, or cohort-shaping config orphans the old
        # series; superseded series under this source key are dropped so a
        # future reader can never sum a stale definition.
        for stale_id in tables.list_state_column_sets(source_id):
            if stale_id != series_id:
                tables.delete_state_series(source_id, stale_id)
        if force_rebuild:
            tables.delete_state_series(source_id, series_id)

        last_day = tables.get_last_state_day(source_id, series_id)
        if full_refresh_window is not None and last_day is not None:
            # Truncate-then-advance (an R1 crash-safety fix): deleting from
            # the first touched day BEFORE re-rendering guarantees a crash
            # mid-refresh leaves a contiguous, self-healing prefix — never a
            # freshly-covered get_last_state_day() over silently stale rows.
            # The tail past the window re-renders too; that is the price of
            # keeping "every day <= last_state_day is materialized" true
            # without a per-day ledger.
            refresh_from, refresh_to = full_refresh_window
            touched = [
                sd.day
                for sd in days
                if sd.day <= last_day
                and sd.window.start_ts < refresh_to
                and sd.window.end_ts > refresh_from
            ]
            if touched:
                tables.delete_state_days_from(source_id, series_id, min(touched))
                last_day = tables.get_last_state_day(source_id, series_id)
        pending = [sd for sd in days if last_day is None or sd.day > last_day]
        if not pending:
            continue

        n_days = len(pending)
        log(f"STATE {experiment.name}/{metric.name}: {n_days} closed days to materialize")
        beat_every = max(1, n_days // 20)
        for day_index, state_day in enumerate(pending, start=1):
            loaded = backend.load_window(metric, metric_sql, state_day.window)
            try:
                data = day_moments(metric, loaded)
            except StateMomentError as exc:
                # Truncate from the failing day (R1: never drop the whole
                # series — that re-rendered the full history every run with
                # zero retained state). Earlier days stay valid and
                # contiguous; the retry next run costs ONE render; the WP4
                # reader sees days past get_last_state_day as unmaterialized
                # and falls back to full recompute (§0.2 — never a
                # materialized-and-empty silent undercount).
                tables.delete_state_days_from(source_id, series_id, state_day.day)
                outcome.warnings.append(
                    f"{experiment.name}/{metric.name}: day state truncated at "
                    f"{state_day.day} — {exc}; reads past the previous day stay "
                    "on full-window recompute"
                )
                break
            outcome.rows_written += tables.replace_day_state(
                source_id, series_id, state_day.day, data
            )
            outcome.days_materialized += 1
            if n_days > 1 and (day_index % beat_every == 0 or day_index == n_days):
                log(
                    f"STATE {experiment.name}/{metric.name}: "
                    f"{day_index}/{n_days} days materialized"
                )
    return outcome
