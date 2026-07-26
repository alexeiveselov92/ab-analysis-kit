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
* **the bootstrap resample memo** (m10 WP5, ``boot_memo``) is guarded by its
  own :attr:`ExploreSession.boot_memo_lock` and, like the cache, is reachable
  only through the accessors below. The two locks are NEVER nested — the memo
  purge in :meth:`ExploreSession.install_cutoff` runs after the ``cache_lock``
  section returns — so no lock-ordering rule exists to get wrong.

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
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.period_planner import Cutoff, Grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.loaders.metric_loader import MetricLoadResult
from abkit.pipeline.analyze import comparison_alpha, effective_alphas
from abkit.stats.bootstrap import ResampleOutcome

#: Tier-S cache budget in stored numeric values (role-array floats across
#: variants and cutoffs; ≈160 MB of float64) — m3-implementation-plan.md WP4.
EXPLORE_CACHE_BUDGET = 20_000_000

#: Bootstrap resample-memo budget in stored replicate values (m10 WP5) —
#: 2 000 000 float64 ≈ 16 MB. Counted in VALUES, not entries: ``n_samples`` is
#: a live knob, so one entry is anywhere from a few hundred to a few million
#: replicates and an entry cap could not bound the memory at all.
EXPLORE_BOOT_MEMO_BUDGET = 2_000_000

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


class BootMemoKey(NamedTuple):
    """What a memoized bootstrap resample is a function of (m10 WP5).

    Alpha is deliberately ABSENT — that is the whole point: the percentile CI
    and the ``reject`` verdict are re-derived per alpha in ``_finalize`` while
    the replicates are drawn once. Everything else the draw reads is present:

    * ``metric`` / ``name_1`` / ``name_2`` / ``end_ts`` — WHICH per-unit arrays
      the resample ran over. ``method_config_id`` alone would collide across
      metrics and across the arm pairs of a multi-arm experiment (same method,
      same cutoff, different data).
    * ``generation`` — WHICH VERSION of that cached cutoff, bumped by every
      :meth:`ExploreSession.install_cutoff`. A ``/reload`` re-renders the entry
      under the running server, so a memo entry keyed to the previous render
      must be unreachable, not merely purged: a Tier-S reader that read the old
      entry, resampled, and inserts AFTER the reload lands (the interleaving
      m10 WP4's review demonstrated) then costs a discarded resample instead of
      a stale hit.
    * ``method`` / ``params`` — the resolved parameter set of the constructed
      method, canonically serialized. Not ``method_config_id``: ``seed`` and
      ``max_block_bytes`` are identity-EXCLUDED yet both reach the draw.
    """

    metric: str
    name_1: str
    name_2: str
    end_ts: datetime
    generation: int
    method: str
    params: str


class BootMemoEntry(NamedTuple):
    """One memoized resample: the outcome plus the warnings it emitted.

    ``caught`` replays the ``AbkitStatsWarning``s the resample raised, so a memo
    HIT reports exactly the warnings a miss would (they are user-visible on the
    point). ``values`` is ``boot_data.size`` — the budget unit.
    """

    outcome: ResampleOutcome
    caught: tuple[str, ...]
    values: int


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
    #: m10 WP5: how many times each cutoff has been installed. Read out with
    #: the entry itself (``cached_entry``) so a memoized resample can be keyed
    #: to the exact render it consumed.
    cache_generation: dict[tuple[str, datetime], int] = field(default_factory=dict)
    cache_values: int = 0
    cache_disabled_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    #: m10 WP4: the fine-grained lock over the four cache fields above — held
    #: only across the dict access itself, NEVER across warehouse I/O or the
    #: resample math, so a cheap Tier-S read is not queued behind a Reload's
    #: slow render. Not part of the session's value identity.
    cache_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    #: m10 WP5: the bootstrap resample memo — one entry per
    #: :class:`BootMemoKey`, insertion-ordered so the oldest is evicted first.
    boot_memo: OrderedDict[BootMemoKey, BootMemoEntry] = field(default_factory=OrderedDict)
    boot_memo_values: int = 0
    boot_memo_budget: int = EXPLORE_BOOT_MEMO_BUDGET
    #: Guards the three ``boot_memo*`` fields. NEVER taken while ``cache_lock``
    #: is held (see the module docstring) — the two are independent, not ordered.
    boot_memo_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

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
    ) -> tuple[MetricLoadResult | None, str | int | None, int]:
        """The cached entry, the ``covariate_lookback`` it was rendered with, and
        its install generation.

        Read as one triple under the lock: the Tier-S gate compares the first
        two, and a concurrent ``/reload`` replaces them one after the other —
        reading them separately could pair a fresh entry with the previous
        lookback tag (or the reverse) and serve a cutoff the gate would have
        refused. The generation (m10 WP5) rides along for the same reason: a
        resample memoized against this read must be keyed to the render it
        actually consumed, and a torn read would key it to another one.
        """
        key = (metric, end_ts)
        with self.cache_lock:
            return (
                self.cache.get(key),
                self.cache_lookback.get(key),
                self.cache_generation.get(key, 0),
            )

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

        Two m10 WP5 duties come with being that one writer: bump the cutoff's
        generation (which makes every resample memoized against the previous
        render unreachable — correctness), and drop those entries (memory).
        The drop runs AFTER the ``cache_lock`` section, so the two locks are
        never nested; it is housekeeping, and the generation is what makes a
        stale hit impossible.
        """
        key = (metric, end_ts)
        with self.cache_lock:
            previous = self.cache.get(key)
            if previous is not None:
                self.cache_values -= loaded_value_count(previous)
            self.cache[key] = loaded
            self.cache_lookback[key] = lookback
            self.cache_generation[key] = self.cache_generation.get(key, 0) + 1
            self.cache_values += loaded_value_count(loaded)
        self.drop_memoized_cutoff(metric, end_ts)

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
            # generations are NOT reset: a monotonic counter per cutoff is what
            # keeps a memo entry from an earlier render unreachable, and a
            # re-populated cache must never reuse a generation number.
        with self.boot_memo_lock:
            self.boot_memo.clear()
            self.boot_memo_values = 0
        self.cache_disabled_reason = reason

    # -- the bootstrap resample memo (m10 WP5) --------------------------------
    #
    # Same discipline as the cache: these accessors take ``boot_memo_lock``
    # themselves, none may be called with it already held, and nothing outside
    # this class touches ``boot_memo``. The lock is NEVER held across the
    # resample itself — only across the dict access.

    def memoized_resample(self, key: BootMemoKey) -> BootMemoEntry | None:
        """The memoized outcome for this exact key, or ``None``."""
        with self.boot_memo_lock:
            return self.boot_memo.get(key)

    def memoize_resample(self, key: BootMemoKey, entry: BootMemoEntry) -> bool:
        """Store one outcome, evicting oldest-first past the budget.

        Returns whether it was stored. An entry larger than the whole budget is
        REFUSED rather than stored — admitting it would evict every other entry
        and still bust the cap; a huge ``n_samples`` then simply resamples every
        time, which is correct and bounded (the m3 cache's "never a partial
        state the UI would misread" principle, applied to memory instead).
        """
        if entry.values > self.boot_memo_budget:
            return False
        with self.boot_memo_lock:
            previous = self.boot_memo.pop(key, None)
            if previous is not None:
                self.boot_memo_values -= previous.values
            self.boot_memo[key] = entry
            self.boot_memo_values += entry.values
            while self.boot_memo_values > self.boot_memo_budget and len(self.boot_memo) > 1:
                _, evicted = self.boot_memo.popitem(last=False)
                self.boot_memo_values -= evicted.values
        return True

    def drop_memoized_cutoff(self, metric: str, end_ts: datetime) -> None:
        """Forget every memoized resample of one (metric, cutoff).

        Housekeeping after a re-install: those entries are already unreachable
        (their key carries the previous generation), this reclaims their bytes.
        """
        with self.boot_memo_lock:
            stale = [key for key in self.boot_memo if key.metric == metric and key.end_ts == end_ts]
            for key in stale:
                self.boot_memo_values -= self.boot_memo.pop(key).values

    def memoized_count(self) -> int:
        """How many resamples are memoized — the instrumentation seam."""
        with self.boot_memo_lock:
            return len(self.boot_memo)

    def memoized_value_count(self) -> int:
        """The memo budget counter — read under the lock like everything else."""
        with self.boot_memo_lock:
            return self.boot_memo_values


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
