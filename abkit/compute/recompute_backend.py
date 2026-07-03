"""The v1 compute strategy: full-window recompute (the golden reference).

Each cutoff re-renders the metric SQL over the FULL cumulative window
``[start_ts, end_ts)`` and re-executes it — cumulative-intervals.md §4:
correctness-first, made cheap-to-skip by the planner anti-join, with the
warehouse cohort persisted once (the macro joins ``_ab_exposures``). The v2
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

from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.core.interval import Interval
from abkit.core.period_planner import Cutoff, Grid
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.metric_loader import (
    MetricLoadResult,
    load_covariate_from_preperiod,
    load_metric,
)
from abkit.loaders.query_template import QueryTemplate, RenderWindow, build_builtins


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
    """Loads one comparison's data per cutoff by full-window recomputation."""

    def __init__(
        self,
        manager: BaseDatabaseManager,
        experiment: ExperimentConfig,
        exposures_table: str = "_ab_exposures",
    ) -> None:
        self._manager = manager
        self._experiment = experiment
        self._exposures_table = exposures_table
        self._template = QueryTemplate()
        self._covariate_cache: dict[str, dict[str, float]] = {}

    def _builtins(self, window: RenderWindow, apply_exposure_filter: bool = True) -> dict[str, Any]:
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
        )

    def render(self, metric_sql: str, window: RenderWindow) -> str:
        """The provenance copy of the executed SQL."""
        return self._template.render(metric_sql, self._builtins(window))

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
        loaded = load_metric(
            self._manager,
            metric,
            metric_sql,
            self._builtins(window),
            declared_variants=self._experiment.assignment.variants,
            template=self._template,
        )

        lookback = comparison.method.covariate_lookback
        if lookback is not None and metric.columns.covariate is None:
            covariate = self._covariate_cache.get(metric.name)
            if covariate is None:
                lookback_seconds = Interval(lookback).seconds
                pre_window = RenderWindow(
                    start_ts=grid.start_ts - timedelta(seconds=lookback_seconds),
                    end_ts=grid.start_ts,
                )
                covariate = load_covariate_from_preperiod(
                    self._manager,
                    metric,
                    metric_sql,
                    self._builtins(pre_window, apply_exposure_filter=False),
                    declared_variants=self._experiment.assignment.variants,
                    template=self._template,
                )
                self._covariate_cache[metric.name] = covariate
            loaded.attach_covariate(covariate)
        return loaded
