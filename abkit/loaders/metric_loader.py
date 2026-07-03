"""Metric loader: metric SQL → per-unit, per-variant role arrays.

Render → execute → validate → group. The loader returns typed per-unit
arrays keyed by variant and column role; the analyze stage (WP8) converts
them into stats-core containers (``Sample``/``Fraction``/``RatioSample`` or
sufficient statistics) per the method family — the loader knows nothing
about methods.

CUPED covariate mechanics (statistics-changes.md §5, fixed whole-day
lookback): when a comparison's method declares ``covariate_lookback``, the
SAME metric SQL is rendered a SECOND time over the pre-period window
``[start_ts − lookback, start_ts)`` with the exposure filter dropped
(``ab_apply_exposure_filter=false`` — pre-period precedes exposure by
construction), and the pre-period VALUE becomes the covariate, keyed by
unit (absent units → 0.0, the standard new-user convention). This is the
legacy CUPED semantics — the covariate is the same metric over the
pre-period — expressed as two renders of one query instead of a conditional
aggregate, so plain additive ``sum()``/``count()`` metric SQL stays correct.
An explicit ``columns.covariate`` role (a covariate column computed by the
author's own SQL) takes precedence and skips the second render.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np

from abkit.config.metric_config import MetricConfig
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.query_template import QueryTemplate


class MetricLoadError(Exception):
    """Raised when a metric result set violates the one-row-per-unit contract."""


#: roles carried through per metric type (unit + these become arrays)
_NUMERIC_ROLES = ("value", "covariate", "count", "nobs", "numerator", "denominator")


def _to_float_array(values: list[Any], role: str, metric: str) -> np.ndarray:
    out = np.empty(len(values), dtype=np.float64)
    for i, v in enumerate(values):
        if v is None:
            out[i] = np.nan
        else:
            try:
                out[i] = float(v)
            except (TypeError, ValueError) as exc:
                raise MetricLoadError(
                    f"metric '{metric}': column role '{role}' has a non-numeric "
                    f"value {v!r} at row {i}"
                ) from exc
    if np.isnan(out).any():
        n_nan = int(np.isnan(out).sum())
        warnings.warn(
            f"metric '{metric}': {n_nan} NULL values in role '{role}' treated as NaN",
            stacklevel=3,
        )
    return out


class MetricLoadResult:
    """Per-variant, per-role per-unit arrays for one (metric, window) load."""

    def __init__(
        self,
        metric: str,
        units_by_variant: dict[str, np.ndarray],
        roles_by_variant: dict[str, dict[str, np.ndarray]],
        strata_by_variant: dict[str, np.ndarray | None],
    ):
        self.metric = metric
        self.units_by_variant = units_by_variant
        self.roles_by_variant = roles_by_variant
        self.strata_by_variant = strata_by_variant

    def variants(self) -> list[str]:
        return sorted(self.units_by_variant)

    def size(self, variant: str) -> int:
        return len(self.units_by_variant.get(variant, ()))

    def attach_covariate(self, covariate_by_unit: dict[str, float]) -> None:
        """Join a pre-period covariate onto every variant's units (absent → 0.0)."""
        for variant, units in self.units_by_variant.items():
            self.roles_by_variant[variant]["covariate"] = np.array(
                [covariate_by_unit.get(u, 0.0) for u in units], dtype=np.float64
            )


def load_metric(
    manager: BaseDatabaseManager,
    metric: MetricConfig,
    metric_sql: str,
    builtins: dict[str, Any],
    declared_variants: list[str],
    template: QueryTemplate | None = None,
) -> MetricLoadResult:
    """Render + execute + validate one metric query for one window.

    Contract (declarative-config.md §3): ONE ROW PER UNIT with additive
    aggregate columns over the window. Guards:

    - rendered SQL must join the persisted cohort (``_abk_exposures``) — the
      macro-usage lint's runtime half;
    - required role columns present in the result set;
    - rows > distinct units → loud one-row-per-unit warning naming the fix
      ("did you forget GROUP BY <unit_key>?"); duplicate unit rows are
      REJECTED, not silently aggregated;
    - observed variants ⊆ declared variants.
    """
    template = template or QueryTemplate()
    rendered = template.render(metric_sql, builtins)
    if "_abk_exposures" not in rendered:
        raise MetricLoadError(
            f"metric '{metric.name}': rendered SQL does not join the persisted "
            "cohort — use the packaged macro ({% import 'abkit_assignment.jinja' "
            "as ab %} ... {{ ab.exposed_units() }})"
        )
    rows = manager.execute_query(rendered)

    role_map = metric.columns.role_map()
    variant_col = role_map["variant"]
    unit_col = builtins["ab_unit_key"]
    stratum_col = role_map.get("stratum")

    result_units: dict[str, list[str]] = {}
    result_roles: dict[str, dict[str, list[Any]]] = {}
    result_strata: dict[str, list[Any]] = {}

    if rows:
        present = rows[0].keys()
        needed = {unit_col: "unit"} | {
            col: role for role, col in role_map.items() if role != "stratum"
        }
        missing = [col for col in needed if col not in present]
        if missing:
            raise MetricLoadError(
                f"metric '{metric.name}': result set is missing columns {missing} "
                f"(have: {sorted(present)}). The query must return the unit key "
                "and every declared column role."
            )

    seen_units: dict[str, set[str]] = {}
    duplicates = 0
    for row in rows:
        variant = row[variant_col]
        if variant not in declared_variants:
            raise MetricLoadError(
                f"metric '{metric.name}': variant '{variant}' is not declared "
                f"in the experiment ({declared_variants})"
            )
        unit = str(row[unit_col])
        bucket = seen_units.setdefault(variant, set())
        if unit in bucket:
            duplicates += 1
            continue
        bucket.add(unit)

        result_units.setdefault(variant, []).append(unit)
        roles = result_roles.setdefault(variant, {})
        for role, col in role_map.items():
            if role in ("variant", "stratum"):
                continue
            roles.setdefault(role, []).append(row[col])
        if stratum_col is not None:
            result_strata.setdefault(variant, []).append(row[stratum_col])

    if duplicates:
        raise MetricLoadError(
            f"metric '{metric.name}': {duplicates} duplicate unit rows — the "
            f"contract is ONE ROW PER UNIT. Did you forget "
            f"GROUP BY {unit_col} in the metric SQL?"
        )

    units_by_variant = {v: np.array(units, dtype=object) for v, units in result_units.items()}
    roles_by_variant = {
        v: {role: _to_float_array(values, role, metric.name) for role, values in roles.items()}
        for v, roles in result_roles.items()
    }
    strata_by_variant: dict[str, np.ndarray | None] = {
        v: (np.array(result_strata[v], dtype=object) if v in result_strata else None)
        for v in units_by_variant
    }

    return MetricLoadResult(
        metric=metric.name,
        units_by_variant=units_by_variant,
        roles_by_variant=roles_by_variant,
        strata_by_variant=strata_by_variant,
    )


def load_covariate_from_preperiod(
    manager: BaseDatabaseManager,
    metric: MetricConfig,
    metric_sql: str,
    preperiod_builtins: dict[str, Any],
    declared_variants: list[str],
    template: QueryTemplate | None = None,
) -> dict[str, float]:
    """The covariate render: the same metric SQL over the pre-period window.

    ``preperiod_builtins`` must carry the ``[start_ts − lookback, start_ts)``
    window and ``ab_apply_exposure_filter=False``. Returns ``{unit: value}``;
    NaNs are dropped (a NULL pre-period value means "no pre-period signal" —
    the join defaults those units to 0.0).
    """
    if preperiod_builtins.get("ab_apply_exposure_filter", True):
        raise ValueError(
            "covariate render requires ab_apply_exposure_filter=False "
            "(the pre-period precedes exposure by construction)"
        )
    result = load_metric(
        manager, metric, metric_sql, preperiod_builtins, declared_variants, template
    )
    covariate: dict[str, float] = {}
    for variant in result.variants():
        units = result.units_by_variant[variant]
        values = result.roles_by_variant[variant].get("value")
        if values is None:
            raise MetricLoadError(
                f"metric '{metric.name}': the pre-period covariate render needs "
                "a 'value' role (CUPED covariate = the same metric pre-period)"
            )
        for unit, value in zip(units, values, strict=True):
            if not math.isnan(value):
                covariate[unit] = float(value)
    return covariate
