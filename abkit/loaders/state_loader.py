"""Per-day moment extraction for the STATE stage (m9-implementation-plan.md WP3).

Reshapes one single-day :class:`MetricLoadResult` (the m8-factory day render)
into the ``replace_day_state`` batch for ``_ab_unit_state``. Pure array
reshaping — no I/O, no method knowledge beyond the metric-type → moment-column
mapping (cumulative-intervals.md §3's additive sufficient statistics):

- ``sample``:   ``n=1, Σx=value, Σx²=value²``. Metrics declaring an explicit
  ``columns.covariate`` role are STATE-ineligible (``pipeline/state.py``, an
  R2 review exclusion): that author-computed column may be a static
  per-unit snapshot, which is not additive across day renders — and the
  CUPED pre-period covariate (the second render over the fixed lookback
  window) stays a separate one-time load, untouched by the STATE stage.
  The ``sum_cov*`` schema columns stay reserved for a future day-additive
  covariate contract.
- ``fraction``: ``n=nobs, Σx=count`` (the §3 ``{count, nobs}`` suffstats).
- ``ratio``:    ``n=1, Σx=numerator`` plus ``{Σd, Σd², Σxd}``.

Non-finite moments (a NULL warehouse value became NaN in the loader) raise
:class:`StateMomentError` — the STATE stage drops the whole series and the
metric stays on full-window recompute (m9 §0.2: a gap falls back to
recompute, never a silent undercount).
"""

from __future__ import annotations

import numpy as np

from abkit.config.metric_config import MetricConfig
from abkit.loaders.metric_loader import MetricLoadResult


class StateMomentError(Exception):
    """Raised when a day's moments cannot be materialized faithfully."""


def _concat_units(loaded: MetricLoadResult) -> np.ndarray:
    parts = [loaded.units_by_variant[variant] for variant in loaded.variants()]
    if not parts:
        return np.empty(0, dtype=object)
    return np.concatenate(parts)


def _concat_role(loaded: MetricLoadResult, role: str, metric_name: str) -> np.ndarray:
    parts = []
    for variant in loaded.variants():
        values = loaded.roles_by_variant[variant].get(role)
        if values is None:
            raise StateMomentError(
                f"metric '{metric_name}': the day render is missing role '{role}'"
            )
        parts.append(values)
    if not parts:
        return np.empty(0, dtype=np.float64)
    out: np.ndarray = np.concatenate(parts)
    if out.size and not np.isfinite(out).all():
        n_bad = int((~np.isfinite(out)).sum())
        raise StateMomentError(
            f"metric '{metric_name}': {n_bad} non-finite values in role '{role}' "
            "(NULLs in the warehouse result) cannot be materialized into day state"
        )
    return out


def day_moments(metric: MetricConfig, loaded: MetricLoadResult) -> dict[str, np.ndarray]:
    """One day's ``replace_day_state`` batch from a single-day load."""
    units = _concat_units(loaded)
    data: dict[str, np.ndarray] = {"unit_id": units}
    n_units = len(units)

    if metric.type == "sample":
        value = _concat_role(loaded, "value", metric.name)
        data["n"] = np.ones(n_units, dtype=np.int64)
        data["sum_value"] = value
        data["sum_value_sq"] = value * value
    elif metric.type == "fraction":
        count = _concat_role(loaded, "count", metric.name)
        nobs = _concat_role(loaded, "nobs", metric.name)
        if nobs.size and not np.array_equal(nobs, np.rint(nobs)):
            raise StateMomentError(
                f"metric '{metric.name}': non-integer 'nobs' values cannot be "
                "materialized into the integer day-state trial count"
            )
        data["n"] = np.rint(nobs).astype(np.int64)
        data["sum_value"] = count
    else:  # ratio (MetricType is a closed Literal)
        numerator = _concat_role(loaded, "numerator", metric.name)
        denominator = _concat_role(loaded, "denominator", metric.name)
        data["n"] = np.ones(n_units, dtype=np.int64)
        data["sum_value"] = numerator
        data["sum_value_sq"] = numerator * numerator
        data["sum_denominator"] = denominator
        data["sum_denominator_sq"] = denominator * denominator
        data["sum_value_denominator"] = numerator * denominator

    return data
