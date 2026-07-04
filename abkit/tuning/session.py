"""The explore session: persisted series + the bounded Tier-S cache.

Data source & freshness are D2 (m3-implementation-plan.md): explore reads the
**persisted** ``_ab_results`` series for the baseline (what actually ran) and
performs exactly ONE warehouse load pass at session start to fill the per-unit
cache — read-only, lock-free. Freshness is whatever the last ``abk run``
produced; no rows ⇒ the caller shows the friendly "run ``abk run`` first"
noop (WP8).

Thread discipline (WP4): the load pass runs on the main thread with one
manager **before** serving; after :func:`load_session` returns, the session is
immutable in-memory state — per-knob recompute (``recompute.py``) touches no
DB. Tier-R reloads create their own manager inside the serialized handler
(WP6), never through this module.

Cache budget: the latest persisted cutoff of every comparison is loaded
first; older cutoffs fill newest-first while the total stays under
``EXPLORE_CACHE_BUDGET`` numeric values. If even the latest cutoffs do not
fit, the cache is dropped entirely and the session degrades to
suffstats-only mode (``cache_disabled_reason`` set; Tier-S knobs disabled
with that reason — never a silent partial cache the UI would misread as
"bootstrap is live").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.period_planner import Cutoff, Grid, generate_grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.loaders.metric_loader import MetricLoadResult
from abkit.pipeline.analyze import comparison_alpha, effective_alphas

#: Tier-S cache budget in stored numeric values (role-array floats across
#: variants and cutoffs; ≈160 MB of float64) — m3-implementation-plan.md WP4.
EXPLORE_CACHE_BUDGET = 20_000_000

#: One (comparison, cutoff) load — ``RecomputeBackend.load_cutoff`` bound to
#: its metric SQL (see :func:`backend_cutoff_loader`); tests may stub it.
CutoffLoader = Callable[[ComparisonConfig, MetricConfig, Grid, Cutoff], MetricLoadResult]


def backend_cutoff_loader(
    backend: RecomputeBackend, metric_sql_by_name: dict[str, str]
) -> CutoffLoader:
    """Adapt a ``RecomputeBackend`` to the session's loader callable."""

    def _load(
        comparison: ComparisonConfig, metric: MetricConfig, grid: Grid, cutoff: Cutoff
    ) -> MetricLoadResult:
        return backend.load_cutoff(
            comparison, metric, metric_sql_by_name[metric.name], grid, cutoff
        )

    return _load


def loaded_value_count(loaded: MetricLoadResult) -> int:
    """Numeric values one cached cutoff holds — the cache-budget unit."""
    return sum(arr.size for roles in loaded.roles_by_variant.values() for arr in roles.values())


@dataclass
class ComparisonSeries:
    """One configured comparison's persisted series (FINAL-deduped, ascending).

    ``rows`` carry only the CONFIGURED ``method_config_id`` — orphaned series
    are the startup warning's job (WP8), never silently merged into explore.
    """

    comparison: ComparisonConfig
    metric: MetricConfig
    configured_alpha: float
    rows: list[dict]
    cutoffs: list[datetime]  # distinct end_ts, ascending


@dataclass
class ExploreSession:
    """Immutable in-memory state one explore serve runs against."""

    experiment: ExperimentConfig
    project: ProjectConfig
    grid: Grid
    series_by_metric: dict[str, ComparisonSeries]
    aa_rows: list[dict] = field(default_factory=list)
    cache: dict[tuple[str, datetime], MetricLoadResult] = field(default_factory=dict)
    cache_values: int = 0
    cache_disabled_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def series(self, metric: str) -> ComparisonSeries:
        try:
            return self.series_by_metric[metric]
        except KeyError:
            raise KeyError(
                f"metric '{metric}' is not a configured comparison of experiment "
                f"'{self.experiment.name}' (have: {sorted(self.series_by_metric)})"
            ) from None

    def loaded(self, metric: str, end_ts: datetime) -> MetricLoadResult | None:
        return self.cache.get((metric, end_ts))

    def cached_cutoffs(self, metric: str) -> list[datetime]:
        return sorted(ts for (m, ts) in self.cache if m == metric)


def load_session(
    experiment: ExperimentConfig,
    metrics_by_name: dict[str, MetricConfig],
    project: ProjectConfig,
    tables: InternalTablesManager,
    loader: CutoffLoader | None = None,
    budget: int = EXPLORE_CACHE_BUDGET,
    log: Callable[[str], None] = lambda _: None,
) -> ExploreSession:
    """The one warehouse load pass (D2): series + the bounded Tier-S cache.

    ``loader=None`` builds a suffstats-only session (no Tier S) — the
    ``--no-serve`` static path and unit tests use it.
    """
    alphas = effective_alphas(experiment, project)
    grid = generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        tz=experiment.timezone,
        limit=project.limits.max_looks,
    )

    session = ExploreSession(
        experiment=experiment,
        project=project,
        grid=grid,
        series_by_metric={},
    )

    for comparison in experiment.comparisons:
        if comparison.metric in session.series_by_metric:
            session.warnings.append(
                f"metric '{comparison.metric}' appears in more than one comparison — "
                "explore serves the first"
            )
            continue
        metric = metrics_by_name[comparison.metric]
        rows = tables.load_results(
            experiment.name,
            metric=metric.name,
            method_config_id=comparison.method.method_config_id,
        )
        cutoffs = sorted({row["end_ts"] for row in rows if row.get("end_ts") is not None})
        session.series_by_metric[metric.name] = ComparisonSeries(
            comparison=comparison,
            metric=metric,
            configured_alpha=comparison_alpha(comparison, alphas),
            rows=rows,
            cutoffs=cutoffs,
        )
        log(f"SERIES {experiment.name}/{metric.name}: {len(rows)} rows, {len(cutoffs)} cutoffs")

    # The calibration chip's source (D3) — tolerate a never-validated project.
    if tables.aa_runs_table_exists():
        session.aa_rows = tables.get_aa_runs(experiment.name)

    if loader is None:
        session.cache_disabled_reason = (
            "no warehouse loader — suffstats-only session (Tier-S knobs disabled)"
        )
        return session

    # ── Tier-S load: latest cutoffs first, then older newest-first ──────────
    latest_loads: list[tuple[str, datetime]] = []
    older_loads: list[tuple[str, datetime]] = []
    for name, series in session.series_by_metric.items():
        if not series.cutoffs:
            continue
        latest_loads.append((name, series.cutoffs[-1]))
        older_loads.extend((name, ts) for ts in series.cutoffs[:-1])
    older_loads.sort(key=lambda item: item[1], reverse=True)

    def _load_one(metric_name: str, end_ts: datetime) -> int:
        series = session.series_by_metric[metric_name]
        loaded = loader(series.comparison, series.metric, grid, Cutoff(end_ts=end_ts))
        session.cache[(metric_name, end_ts)] = loaded
        return loaded_value_count(loaded)

    for metric_name, end_ts in latest_loads:
        session.cache_values += _load_one(metric_name, end_ts)
        log(f"CACHE {experiment.name}/{metric_name}: latest cutoff {end_ts}")
        if session.cache_values > budget:
            break  # degrading anyway — bound the transient peak too

    if session.cache_values > budget:
        # Even the latest cutoffs bust the budget: degrade honestly to
        # suffstats-only — a partial cache would misreport bootstrap as live.
        session.cache.clear()
        session.cache_disabled_reason = (
            f"session cache over budget: the latest cutoffs alone hold "
            f"{session.cache_values} values (> {budget}) — suffstats-only "
            "session (Tier-S knobs disabled; raise the budget or reduce arms)"
        )
        session.cache_values = 0
        session.warnings.append(session.cache_disabled_reason)
        return session

    for metric_name, end_ts in older_loads:
        series = session.series_by_metric[metric_name]
        loaded = loader(series.comparison, series.metric, grid, Cutoff(end_ts=end_ts))
        count = loaded_value_count(loaded)
        if session.cache_values + count > budget:
            session.warnings.append(
                f"session cache budget reached at {session.cache_values} values — "
                f"older cutoffs before {end_ts} stay suffstats-only"
            )
            break
        session.cache[(metric_name, end_ts)] = loaded
        session.cache_values += count

    return session
