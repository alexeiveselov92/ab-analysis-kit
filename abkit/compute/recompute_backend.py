"""The v1 compute strategy: full-window recompute (the golden reference).

Each cutoff re-renders the metric SQL over the FULL cumulative window
``[start_ts, end_ts)`` and re-executes it — cumulative-intervals.md §4:
correctness-first, made cheap-to-skip by the planner anti-join, with the
cohort read through the ``ab_cohort_source`` builtin (the persisted
``_ab_exposures`` copy in cohort-copy mode, or — the M8 default — a
live-rendered dedup subquery over the assignment SQL; see the class
docstring below). The v2
incremental backend (reading ``_ab_unit_state`` moments) is deferred behind
``abk verify-incremental``.

The CUPED covariate is loaded ONCE per (comparison, run) — the fixed
whole-day lookback window ``[start_ts − lookback, start_ts)`` never moves
with the cutoff (statistics-changes.md §5) — and attached to every cutoff's
load.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.core.interval import Interval
from abkit.core.period_planner import Cutoff, Grid, local_date, tz_midnight_utc
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.metric_loader import (
    MetricLoadResult,
    load_covariate_from_preperiod,
    load_metric,
)
from abkit.loaders.query_template import (
    POPULATION_VARIANT,
    QueryTemplate,
    RenderWindow,
    build_builtins,
)


def dialect_of(manager: BaseDatabaseManager) -> str:
    """The ``ab_dialect`` built-in from the concrete manager class."""
    name = type(manager).__name__.lower()
    if "clickhouse" in name:
        return "clickhouse"
    if "postgres" in name:
        return "postgres"
    if "mysql" in name:
        return "mysql"
    return "clickhouse"  # fixture/unknown backends get the richest dialect


class RecomputeBackend:
    """Loads one comparison's data per cutoff by full-window recomputation.

    The cohort-source mode (m8-implementation-plan.md WP3) is fixed at
    construction and threads into EVERY render through ``_builtins()`` — the
    metric window render and the CUPED covariate pre-period render alike:
    ``direct_source_sql=None`` (default) keeps today's persisted
    ``exposures_table`` join; a rendered assignment SQL string switches the
    ``ab_cohort_source`` builtin to the deduping direct-join subquery
    (``has_stratum`` shapes its stratum projection — WP4's
    ``build_cohort_backend`` factory supplies both from the probe).
    """

    def __init__(
        self,
        manager: BaseDatabaseManager,
        experiment: ExperimentConfig,
        exposures_table: str = "_ab_exposures",
        direct_source_sql: str | None = None,
        has_stratum: bool = True,
    ) -> None:
        self._manager = manager
        self._experiment = experiment
        self._exposures_table = exposures_table
        self._direct_source_sql = direct_source_sql
        self._has_stratum = has_stratum
        self._template = QueryTemplate()
        self._covariate_cache: dict[str, dict[str, float]] = {}

    def _builtins(
        self,
        window: RenderWindow,
        apply_exposure_filter: bool = True,
        cov_window: RenderWindow | None = None,
        apply_cohort_join: bool = True,
    ) -> dict[str, Any]:
        experiment = self._experiment
        return build_builtins(
            experiment_id=experiment.name,
            unit_key=experiment.unit_key,
            variants=experiment.assignment.variants,
            added_filters=experiment.assignment.added_filters,
            window=window,
            data_database=self._manager.data_location,
            internal_database=self._manager.internal_location,
            exposures_table=self._exposures_table,
            dialect=dialect_of(self._manager),
            apply_exposure_filter=apply_exposure_filter,
            cov_window=cov_window,
            direct_source_sql=self._direct_source_sql,
            has_stratum=self._has_stratum,
            apply_cohort_join=apply_cohort_join,
        )

    def _preperiod_window(self, lookback: str | int, grid: Grid) -> RenderWindow:
        """The fixed pre-period, WHOLE-DAY aligned in the experiment timezone.

        ``[tz-midnight(D − lookback_days), tz-midnight(D))`` where ``D`` is the
        local calendar day the window starts on — day arithmetic in the
        experiment tz (a UTC-seconds subtraction would misalign local days
        across a DST transition inside the lookback; statistics-changes.md §5
        defines the lookback in whole days).

        Both edges come off ``grid.start_ts``, never off the raw config field:
        with a sub-day ``start_ts`` (m10) the window still ends at the local
        midnight that OPENS the start day, so the lookback stays exactly
        ``lookback_days`` whole days instead of gaining a partial tail. At a
        midnight start — every pre-m10 config — that midnight IS
        ``grid.start_ts`` and the window is byte-identical to before.
        """
        lookback_days = Interval(lookback).seconds // 86400
        zone = ZoneInfo(self._experiment.timezone)
        start_day = local_date(grid.start_ts, zone)
        pre_start = tz_midnight_utc(start_day - timedelta(days=lookback_days), zone)
        return RenderWindow(start_ts=pre_start, end_ts=tz_midnight_utc(start_day, zone))

    def render(self, metric_sql: str, window: RenderWindow) -> str:
        """The provenance copy of the executed SQL."""
        return self._template.render(metric_sql, self._builtins(window))

    def load_window(
        self,
        metric: MetricConfig,
        metric_sql: str,
        window: RenderWindow,
    ) -> MetricLoadResult:
        """A bare one-window load — the m9 WP3 STATE stage's per-day render.

        Threads the SAME cohort-mode builtins as :meth:`load_cutoff` (the m8
        §0.5(e) factory contract): under the no-copy default the render joins
        the live assignment source, never a hand-rolled ``_ab_exposures``.
        No covariate attachment — day state persists only the window's own
        additive moments.
        """
        return load_metric(
            self._manager,
            metric,
            metric_sql,
            self._builtins(window),
            declared_variants=self._experiment.assignment.variants,
            template=self._template,
        )

    def preperiod_covariate(
        self,
        metric: MetricConfig,
        metric_sql: str,
        lookback: str | int,
        grid: Grid,
    ) -> dict[str, float]:
        """The cached CUPED pre-period covariate load (once per metric per run).

        Shared with the m9 WP4 ``IncrementalBackend``: the incremental read
        replaces only the METRIC's cumulative-value load — the covariate stays
        this exact fixed-window render either way, so both backends draw from
        the ONE cache and a mid-run fallback can never re-load it.
        """
        covariate = self._covariate_cache.get(metric.name)
        if covariate is None:
            pre_window = self._preperiod_window(lookback, grid)
            covariate = load_covariate_from_preperiod(
                self._manager,
                metric,
                metric_sql,
                self._builtins(pre_window, apply_exposure_filter=False, cov_window=pre_window),
                declared_variants=self._experiment.assignment.variants,
                template=self._template,
            )
            self._covariate_cache[metric.name] = covariate
        return covariate

    def load_population_window(
        self,
        metric: MetricConfig,
        metric_sql: str,
        lookback: str | int,
        grid: Grid,
    ) -> MetricLoadResult:
        """A COHORT-FREE pre-period load — the PLAN-2 pre-launch baseline render.

        Unlike :meth:`load_window` (the m9 STATE render, cohort-filtered by
        design) this renders with ``apply_cohort_join=False``, so the macro
        joins a one-row synthetic relation instead of the cohort and the query
        measures the whole population the metric SQL yields. That is the ONLY
        thing that can answer "size this experiment" before it has enrolled
        anybody — an inner join to an empty cohort returns zero rows — and it is
        why the plan line discloses that the baseline is population-wide rather
        than the cohort ``added_filters`` will actually select.

        Every row comes back under :data:`POPULATION_VARIANT`, which is passed as
        the sole declared variant: ``load_metric`` validates observed variants
        against that list, so a real arm label can never appear here.

        It takes the LOOKBACK, not a window: the whole-day pre-period rule lives
        in :meth:`_preperiod_window` and must have exactly one implementation
        (the m10 ``ExperimentConfig.grid()`` discipline — a second copy of a
        window rule is how a knob reaches none of its call sites).
        """
        window = self._preperiod_window(lookback, grid)
        return load_metric(
            self._manager,
            metric,
            metric_sql,
            self._builtins(window, apply_exposure_filter=False, apply_cohort_join=False),
            declared_variants=[POPULATION_VARIANT],
            template=self._template,
        )

    def load_cutoff(
        self,
        comparison: ComparisonConfig,
        metric: MetricConfig,
        metric_sql: str,
        grid: Grid,
        cutoff: Cutoff,
    ) -> MetricLoadResult:
        """Load one (comparison, cutoff): full window + cached covariate."""
        window = RenderWindow(start_ts=grid.start_ts, end_ts=cutoff.end_ts)
        lookback = comparison.method.covariate_lookback
        pre_window = (
            self._preperiod_window(lookback, grid)
            if lookback is not None and metric.columns.covariate is None
            else None
        )
        loaded = load_metric(
            self._manager,
            metric,
            metric_sql,
            self._builtins(window, cov_window=pre_window),
            declared_variants=self._experiment.assignment.variants,
            template=self._template,
        )

        if pre_window is not None:
            assert lookback is not None  # pre_window derives from it above
            loaded.attach_covariate(self.preperiod_covariate(metric, metric_sql, lookback, grid))
        return loaded
