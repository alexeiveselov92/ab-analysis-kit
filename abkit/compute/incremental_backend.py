"""The v2 compute strategy: additive state reads (m9 WP4, opt-in).

``IncrementalBackend`` matches :class:`RecomputeBackend`'s ``load_cutoff``
interface but reads per-unit cumulative moments from ``_ab_unit_state``
(the WP3 STATE stage's materialization) instead of re-scanning the raw fact
table — cumulative-intervals.md §4's committed v1 strategy, read side:
a cutoff costs (one additive SUM over closed-day state rows) + (for sub-day
cutoffs, a fact scan of at most the current-day tail, §6.4) instead of the
O(window) full rescan.

Correctness posture (m9 §0.2 — the load-bearing safety net):

- **Any gap falls back to full recompute, never a silent undercount.** The
  WP3 contiguity invariant makes detection one comparison: every day
  ``<= get_last_state_day()`` is materialized (a trailing day with zero
  qualifying events keeps ``get_last_state_day`` below it — the reader then
  conservatively falls back until a non-empty day advances the series); days
  past it are absent, not stale. If the last state day trails the cutoff's
  last closed day, this backend delegates the WHOLE cutoff to the wrapped
  ``RecomputeBackend`` and surfaces one warning per metric per run.
- **A non-finite tail contribution falls back too**: the closed-day writer
  rejects non-finite moments (``StateMomentError``), so the summed state is
  finite by construction, but the live tail render can carry warehouse NULLs
  (NaN after the loader) whose full-window SQL aggregation would have
  skipped them — recompute is the only faithful answer there.
- **The reshaped result reuses the recompute containers unchanged**: the
  per-unit cumulative totals feed the SAME ``MetricLoadResult`` shape
  ``load_metric`` produces, so ``analyze_cutoff``/``build_container`` and
  ``SufficientStats.from_sample``'s stable reduction stay the single
  numerical path for the arm-level statistic (m9 WP4 step 2e).

The arm split happens at read time (state rows are arm-agnostic by design,
§5.2): tail units take the variant of the LIVE tail render's own cohort join
— exactly the arm the full-window recompute would land the unit's whole
value on — and state-only units join this run's cohort mapping (the LOAD
snapshot in direct mode, the persisted ``_ab_exposures`` in copy mode; both
via the driver-supplied loader). A unit absent from the current cohort is
dropped, mirroring the recompute render's INNER JOIN.

The CUPED pre-period covariate is untouched (m9 WP4 step 3): it loads
through the wrapped backend's one ``preperiod_covariate`` cache, so the
metric's own value load is the only thing this class replaces.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.core.period_planner import Cutoff, Grid, tz_midnight_utc
from abkit.database.internal_tables import InternalTablesManager
from abkit.loaders.metric_loader import MetricLoadResult
from abkit.loaders.query_template import RenderWindow
from abkit.pipeline.state import state_series_key

#: metric type -> {role: moment column} — how per-unit cumulative moments
#: reshape into the loader's role arrays (the inverse of state_loader's
#: day_moments mapping; sum_value_sq and friends are day-level squares and
#: deliberately unused — the cross-unit variance comes from the per-unit
#: totals through SufficientStats, never from summed day squares)
_ROLE_MOMENTS: dict[str, dict[str, str]] = {
    "sample": {"value": "sum_value"},
    "fraction": {"count": "sum_value", "nobs": "n"},
    "ratio": {"numerator": "sum_value", "denominator": "sum_denominator"},
}


def _local_date(ts: datetime, zone: ZoneInfo) -> date:
    """The experiment-timezone calendar date of a naive-UTC timestamp."""
    return ts.replace(tzinfo=timezone.utc).astimezone(zone).date()


class IncrementalBackend:
    """Loads one comparison's data per cutoff from materialized day state.

    Constructed once per run by the driver alongside the factory-built
    ``RecomputeBackend`` it wraps (fallback + tail renders + covariate cache
    all go through that ONE instance, so the m8 cohort-mode threading is
    inherited, never reimplemented — m9 §0.2). ``variant_map_loader`` is
    called lazily at most once; ``on_warning`` receives the fallback
    disclosures the driver surfaces in ``RunOutcome.warnings``.
    """

    def __init__(
        self,
        tables: InternalTablesManager,
        recompute: RecomputeBackend,
        experiment: ExperimentConfig,
        variant_map_loader: Callable[[], dict[str, str]],
        project_root: Path | None = None,
        on_warning: Callable[[str], None] | None = None,
    ) -> None:
        self._tables = tables
        self._recompute = recompute
        self._experiment = experiment
        self._variant_map_loader = variant_map_loader
        self._project_root = project_root
        self._on_warning = on_warning or (lambda _: None)
        self._zone = ZoneInfo(experiment.timezone)
        self._series_keys: dict[str, tuple[str, str]] = {}
        self._last_state_day: dict[tuple[str, str], date | None] = {}
        self._variant_map: dict[str, str] | None = None
        self._warned: set[str] = set()

    # -- delegation: provenance render + covariate stay the recompute path --

    def render(self, metric_sql: str, window: RenderWindow) -> str:
        """The provenance copy of the FULL-WINDOW SQL (what recompute runs).

        Persisted rows keep the full-window render either way: it documents
        the window/cohort semantics of the number, not the read strategy.
        """
        return self._recompute.render(metric_sql, window)

    # -- the incremental read ----------------------------------------------

    def _series_key(self, metric: MetricConfig, metric_sql: str) -> tuple[str, str]:
        key = self._series_keys.get(metric.name)
        if key is None:
            key = state_series_key(self._experiment, metric, metric_sql, self._project_root)
            self._series_keys[metric.name] = key
        return key

    def _cached_last_state_day(self, key: tuple[str, str]) -> date | None:
        # Stable within one run: STATE (when selected) runs before COMPUTE
        # under the run lock, and nothing else writes the series.
        if key not in self._last_state_day:
            self._last_state_day[key] = self._tables.get_last_state_day(*key)
        return self._last_state_day[key]

    def _cohort_variants(self) -> dict[str, str]:
        if self._variant_map is None:
            self._variant_map = self._variant_map_loader()
        return self._variant_map

    def _warn_once(self, metric_name: str, message: str) -> None:
        if metric_name not in self._warned:
            self._warned.add(metric_name)
            self._on_warning(message)

    def _fallback(
        self,
        comparison: ComparisonConfig,
        metric: MetricConfig,
        metric_sql: str,
        grid: Grid,
        cutoff: Cutoff,
        reason: str,
    ) -> MetricLoadResult:
        self._warn_once(
            metric.name,
            f"{self._experiment.name}/{metric.name}: incremental read fell back "
            f"to full recompute — {reason}",
        )
        return self._recompute.load_cutoff(comparison, metric, metric_sql, grid, cutoff)

    def load_cutoff(
        self,
        comparison: ComparisonConfig,
        metric: MetricConfig,
        metric_sql: str,
        grid: Grid,
        cutoff: Cutoff,
    ) -> MetricLoadResult:
        """Load one (comparison, cutoff) from state + at most one day of tail."""
        end_ts = cutoff.end_ts
        cutoff_day = _local_date(end_ts, self._zone)
        last_midnight = tz_midnight_utc(cutoff_day, self._zone)
        required_last = cutoff_day - timedelta(days=1)
        role_moments = _ROLE_MOMENTS[metric.type]

        totals: dict[str, dict[str, float]] = {}
        if required_last >= self._experiment.start_date:
            key = self._series_key(metric, metric_sql)
            last_state = self._cached_last_state_day(key)
            if last_state is None or last_state < required_last:
                have = "no materialized days" if last_state is None else f"state through {last_state}"
                return self._fallback(
                    comparison,
                    metric,
                    metric_sql,
                    grid,
                    cutoff,
                    f"{have}, cutoffs need closed days through {required_last} "
                    "(run the STATE step to advance the series)",
                )
            moments = self._tables.per_unit_cumulative(
                key[0], key[1], self._experiment.start_date, required_last
            )
            for unit, unit_moments in moments.items():
                totals[unit] = {role: unit_moments[m] for role, m in role_moments.items()}

        tail_variant: dict[str, str] = {}
        if last_midnight < end_ts:
            tail = self._recompute.load_window(
                metric, metric_sql, RenderWindow(last_midnight, end_ts)
            )
            for variant in tail.variants():
                units = tail.units_by_variant[variant]
                roles = tail.roles_by_variant[variant]
                for role in role_moments:
                    values = roles.get(role)
                    if values is None or (values.size and not np.isfinite(values).all()):
                        # NULL-bearing tail: full-window SQL would have
                        # skipped the NULLs inside its aggregates — only the
                        # recompute render answers that faithfully.
                        return self._fallback(
                            comparison,
                            metric,
                            metric_sql,
                            grid,
                            cutoff,
                            f"the current-day tail render carries "
                            f"{'a missing' if values is None else 'non-finite'} "
                            f"'{role}' role",
                        )
                for index, unit in enumerate(units):
                    tail_variant[unit] = variant
                    bucket = totals.setdefault(unit, dict.fromkeys(role_moments, 0.0))
                    for role in role_moments:
                        bucket[role] += float(roles[role][index])

        loaded = self._reshape(metric, totals, tail_variant)

        # CUPED: the fixed pre-period covariate stays the recompute-side load
        # (one shared cache — m9 WP4 step 3); eligibility already excluded
        # explicit ``columns.covariate`` metrics, so only the lookback form
        # can reach here.
        lookback = comparison.method.covariate_lookback
        if lookback is not None and metric.columns.covariate is None:
            loaded.attach_covariate(
                self._recompute.preperiod_covariate(metric, metric_sql, lookback, grid)
            )
        return loaded

    def _reshape(
        self,
        metric: MetricConfig,
        totals: dict[str, dict[str, float]],
        tail_variant: dict[str, str],
    ) -> MetricLoadResult:
        """Per-unit cumulative totals -> the ``load_metric`` result shape.

        Tail units carry the live render's own arm; state-only units join the
        run's cohort mapping; unmapped units drop (the INNER JOIN mirror).
        Units sort per variant — the loader's canonical order (m3 D11), so
        downstream containers are order-identical to the recompute path.
        """
        variant_map = self._cohort_variants()
        units_of: dict[str, list[str]] = {}
        for unit in totals:
            variant = tail_variant.get(unit) or variant_map.get(unit)
            if variant is None:
                continue
            units_of.setdefault(variant, []).append(unit)

        roles = list(_ROLE_MOMENTS[metric.type])
        units_by_variant: dict[str, np.ndarray] = {}
        roles_by_variant: dict[str, dict[str, np.ndarray]] = {}
        strata_by_variant: dict[str, np.ndarray | None] = {}
        for variant, units in units_of.items():
            units.sort()
            units_by_variant[variant] = np.array(units, dtype=object)
            roles_by_variant[variant] = {
                role: np.array([totals[u][role] for u in units], dtype=np.float64)
                for role in roles
            }
            strata_by_variant[variant] = None

        return MetricLoadResult(
            metric=metric.name,
            units_by_variant=units_by_variant,
            roles_by_variant=roles_by_variant,
            strata_by_variant=strata_by_variant,
        )
