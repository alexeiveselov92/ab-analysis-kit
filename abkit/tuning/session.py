"""The explore session: persisted series + the bounded Tier-S cache.

Data source & freshness are D2 (m3-implementation-plan.md): explore reads the
**persisted** ``_ab_results`` series for the baseline (what actually ran) and
performs exactly ONE warehouse load pass at session start to fill the per-unit
cache — read-only, lock-free. Freshness is whatever the last ``abk run``
produced; no rows ⇒ the caller shows the friendly "run ``abk run`` first"
noop (WP8).

Thread discipline (WP4): the load pass runs on the main thread with one
manager **before** serving; per-knob recompute (``recompute.py``) touches no
DB. Tier-R reloads create their own manager inside the serialized handler
(WP6), never through this module.

Once serving, the session is read-mostly but NOT immutable, and since m10 WP4
``POST /recompute`` is no longer serialized against ``/reload``, so the two
mutable surfaces have explicit disciplines:

* **the Tier-S cache** (``cache``/``cache_lookback``/``cache_values``) is
  guarded by :attr:`ExploreSession.cache_lock`, and every access — read or
  write — goes through the accessor methods below. A Tier-S read must see a
  consistent (entry, lookback-it-was-rendered-with) PAIR, and a scan must not
  iterate a dict ``/reload`` is mutating; the accessors are the only place
  that discipline lives (``tests/tuning/test_session_cache_lock.py`` is the
  gate).
* **``aa_rows``** is deliberately lock-free: Auto mode (``/validate``)
  replaces the WHOLE list object, which is atomic under the GIL, and readers
  only ever read it. Never "fix" that into an in-place ``append``/``clear``
  without adding a lock.

Cache budget: the latest persisted cutoff of every comparison is loaded
first; older cutoffs fill newest-first while the total stays under
``EXPLORE_CACHE_BUDGET`` numeric values. If even the latest cutoffs do not
fit, the cache is dropped entirely and the session degrades to
suffstats-only mode (``cache_disabled_reason`` set; Tier-S knobs disabled
with that reason — never a silent partial cache the UI would misread as
"bootstrap is live").
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.period_planner import Cutoff, Grid
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
    """The in-memory state one explore serve runs against.

    Read-mostly, not immutable: see the module docstring's thread discipline —
    the Tier-S cache is lock-guarded behind the accessors below, ``aa_rows`` is
    replaced wholesale by Auto mode.
    """

    experiment: ExperimentConfig
    project: ProjectConfig
    grid: Grid
    series_by_metric: dict[str, ComparisonSeries]
    aa_rows: list[dict] = field(default_factory=list)
    cache: dict[tuple[str, datetime], MetricLoadResult] = field(default_factory=dict)
    #: the covariate_lookback each cached entry was RENDERED with — a Tier-R
    #: /reload may re-render a cutoff under a different lookback, and the
    #: cache gate must compare against what is actually in the entry, not the
    #: configured method (recompute._cache_serves)
    cache_lookback: dict[tuple[str, datetime], str | int | None] = field(default_factory=dict)
    cache_values: int = 0
    cache_disabled_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    #: m10 WP4: the fine-grained lock over the three cache fields above — held
    #: only across the dict access itself, NEVER across warehouse I/O or the
    #: resample math, so a cheap Tier-S read is not queued behind a Reload's
    #: slow render. Not part of the session's value identity.
    cache_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def series(self, metric: str) -> ComparisonSeries:
        try:
            return self.series_by_metric[metric]
        except KeyError:
            raise KeyError(
                f"metric '{metric}' is not a configured comparison of experiment "
                f"'{self.experiment.name}' (have: {sorted(self.series_by_metric)})"
            ) from None

    # -- the Tier-S cache: these accessors are the ONLY sanctioned access -----
    #
    # Every one of them takes ``cache_lock`` itself, so no caller can forget it
    # and none of them may be called with the lock already held (a plain
    # ``threading.Lock`` is not reentrant).

    def loaded(self, metric: str, end_ts: datetime) -> MetricLoadResult | None:
        with self.cache_lock:
            return self.cache.get((metric, end_ts))

    def cached_entry(
        self, metric: str, end_ts: datetime
    ) -> tuple[MetricLoadResult | None, str | int | None]:
        """The cached entry AND the ``covariate_lookback`` it was rendered with.

        Read as one pair under the lock: the Tier-S gate compares the two, and a
        concurrent ``/reload`` replaces them one after the other — reading them
        separately could pair a fresh entry with the previous lookback tag (or
        the reverse) and serve a cutoff the gate would have refused.
        """
        key = (metric, end_ts)
        with self.cache_lock:
            return self.cache.get(key), self.cache_lookback.get(key)

    def cached_cutoffs(self, metric: str) -> list[datetime]:
        with self.cache_lock:
            return sorted(ts for (m, ts) in self.cache if m == metric)

    def cached_entries(self, metric: str) -> list[tuple[datetime, MetricLoadResult]]:
        """Every cached ``(end_ts, entry)`` for one metric, ascending — a snapshot.

        A scan that walked ``cached_cutoffs()`` and then re-read each entry could
        iterate the dict while ``/reload`` mutates it ("dictionary changed size
        during iteration"); one locked snapshot cannot.
        """
        with self.cache_lock:
            items = [(ts, entry) for (m, ts), entry in self.cache.items() if m == metric]
        return sorted(items, key=lambda item: item[0])

    def install_cutoff(
        self,
        metric: str,
        end_ts: datetime,
        loaded: MetricLoadResult,
        lookback: str | int | None,
    ) -> None:
        """Replace one cutoff's (entry, lookback) pair + the budget counter.

        The ONE writer of the cache while the server is up (``/reload``). The
        warehouse render that produced ``loaded`` happens OUTSIDE the lock by
        design — holding it across a slow read would re-serialize exactly what
        m10 WP4 unserialized.
        """
        key = (metric, end_ts)
        with self.cache_lock:
            previous = self.cache.get(key)
            if previous is not None:
                self.cache_values -= loaded_value_count(previous)
            self.cache[key] = loaded
            self.cache_lookback[key] = lookback
            self.cache_values += loaded_value_count(loaded)

    def cached_value_count(self) -> int:
        """The Tier-S budget counter — read under the lock like everything else."""
        with self.cache_lock:
            return self.cache_values

    def disable_cache(self, reason: str) -> None:
        """Drop the whole cache and degrade to suffstats-only, honestly.

        Never a partial cache: the UI would misread it as "bootstrap is live".
        """
        with self.cache_lock:
            self.cache.clear()
            self.cache_lookback.clear()
            self.cache_values = 0
        self.cache_disabled_reason = reason


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
    grid = experiment.grid(limit=project.limits.max_looks)

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

    # This pass is single-threaded and pre-serve (nothing is answering requests
    # yet), so reading ``cache_values`` between installs needs no lock — the
    # installs themselves still go through the one writer.
    def _load_one(metric_name: str, end_ts: datetime) -> None:
        series = session.series_by_metric[metric_name]
        loaded = loader(series.comparison, series.metric, grid, Cutoff(end_ts=end_ts))
        session.install_cutoff(
            metric_name, end_ts, loaded, series.comparison.method.covariate_lookback
        )

    for metric_name, end_ts in latest_loads:
        _load_one(metric_name, end_ts)
        log(f"CACHE {experiment.name}/{metric_name}: latest cutoff {end_ts}")
        if session.cache_values > budget:
            break  # degrading anyway — bound the transient peak too

    if session.cache_values > budget:
        # Even the latest cutoffs bust the budget: degrade honestly to
        # suffstats-only — a partial cache would misreport bootstrap as live.
        # (The reason quotes the peak, so build it BEFORE the counter resets.)
        reason = (
            f"session cache over budget: the latest cutoffs alone hold "
            f"{session.cache_values} values (> {budget}) — suffstats-only "
            "session (Tier-S knobs disabled; raise the budget or reduce arms)"
        )
        session.disable_cache(reason)
        session.warnings.append(reason)
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
        session.install_cutoff(
            metric_name, end_ts, loaded, series.comparison.method.covariate_lookback
        )

    return session
