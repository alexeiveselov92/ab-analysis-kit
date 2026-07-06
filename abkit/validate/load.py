"""The A/A load stage: build a :class:`PlaceboPanel` from the experiment's own cohort
over the actual cadence grid (docs/specs/m4-implementation-plan.md D1, WP2).

The placebo data source is the experiment's *own* pooled per-unit metric values,
loaded per cutoff through the pipeline's real loaders (``RecomputeBackend.load_cutoff``
→ ``metric_loader``, so the packaged assignment macro, the cumulative window semantics,
the CUPED pre-period render, and the D11 canonical unit order all come for free). The
scorer then permutes the pooled units into arms (resample.py) — an exact null by
construction. Nothing here writes to the warehouse: the split is in-memory only.

Dense grids are subsampled to ``cap`` points, denser early (where the peeking FPR
accrues fastest), with the horizon always retained and the ``(kept, total)`` count
disclosed on the panel (aa-fpr §3 "the matrix must state when it did").
"""

from __future__ import annotations

import numpy as np

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ComparisonConfig
from abkit.config.metric_config import MetricConfig
from abkit.core.period_planner import Cutoff, Grid
from abkit.loaders.metric_loader import MetricLoadResult
from abkit.validate._types import ValidateError
from abkit.validate.panel import PanelCutoff, PlaceboPanel

#: Peeking-grid cap (aa-fpr §3 "~100 points, denser early").
DEFAULT_GRID_CAP = 100

_DAY_SECONDS = 86400.0

#: Metric-kind → (primary role, secondary role) in ``MetricLoadResult.roles_by_variant``.
_ROLES: dict[str, tuple[str, str | None]] = {
    "sample": ("value", None),
    "fraction": ("count", "nobs"),
    "ratio": ("numerator", "denominator"),
}


def subsample_grid(cutoffs: tuple[Cutoff, ...], cap: int) -> tuple[list[Cutoff], int, int]:
    """Downsample an ascending cutoff grid to ``cap`` points, denser early.

    The horizon (last) and first looks are always kept; the interior is sampled with
    quadratic spacing so points cluster early where the peeking FPR accrues fastest,
    then backfilled with the earliest unused looks toward ``cap``. Returns
    ``(kept_cutoffs, kept_count, total_count)`` for the disclosure note.
    """
    total = len(cutoffs)
    if cap < 2:
        raise ValidateError(f"grid cap must be >= 2, got {cap}")
    if total <= cap:
        return list(cutoffs), total, total

    picks: set[int] = {0, total - 1}
    for k in range(cap):
        frac = (k / (cap - 1)) ** 2  # quadratic → denser near 0
        picks.add(int(round(frac * (total - 1))))
    i = 0
    while len(picks) < cap and i < total:  # backfill earliest-first (denser early)
        picks.add(i)
        i += 1
    kept = sorted(picks)
    return [cutoffs[i] for i in kept], len(kept), total


def _pool(
    loaded: MetricLoadResult, input_kind: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]:
    """Pool a per-variant load into one per-unit (units, values, secondary, covariate)."""
    if input_kind not in _ROLES:
        raise ValidateError(f"unsupported metric input_kind {input_kind!r}")
    primary_role, secondary_role = _ROLES[input_kind]
    variants = loaded.variants()
    if not variants:
        empty = np.array([], dtype=np.float64)
        return np.array([], dtype=object), empty, None, None

    def _stack(role: str) -> np.ndarray:
        return np.concatenate([loaded.roles_by_variant[v][role] for v in variants])

    units = np.concatenate([loaded.units_by_variant[v] for v in variants])
    values = _stack(primary_role)
    secondary = _stack(secondary_role) if secondary_role is not None else None
    covariate = None
    if all("covariate" in loaded.roles_by_variant[v] for v in variants):
        covariate = _stack("covariate")
    return units, values, secondary, covariate


def load_placebo_panel(
    backend: RecomputeBackend,
    comparison: ComparisonConfig,
    metric: MetricConfig,
    metric_sql: str,
    grid: Grid,
    *,
    input_kind: str,
    cap: int = DEFAULT_GRID_CAP,
) -> PlaceboPanel:
    """Load the pooled per-unit panel for one (metric, comparison) over the grid.

    ``input_kind`` mirrors the method family (``sample`` | ``fraction`` | ``ratio``).
    The covariate is loaded when the comparison's method declares ``covariate_lookback``
    (a fixed pre-period constant per unit — the same value across cutoffs).
    """
    kept_cutoffs, kept, total = subsample_grid(grid.cutoffs, cap)
    if not kept_cutoffs:
        raise ValidateError("empty cadence grid")

    loads = [
        (cut, backend.load_cutoff(comparison, metric, metric_sql, grid, cut))
        for cut in kept_cutoffs
    ]

    # The horizon (last, by construction the superset of every earlier cumulative
    # cutoff) defines the global unit universe and the fixed covariate.
    horizon_load = loads[-1][1]
    h_units, _h_values, _h_secondary, h_cov = _pool(horizon_load, input_kind)
    if h_units.size == 0:
        raise ValidateError(
            f"metric '{metric.name}': no units at the horizon cutoff — nothing to validate"
        )

    order = np.argsort(h_units, kind="stable")
    global_units = h_units[order]
    global_index = {unit: idx for idx, unit in enumerate(global_units)}
    n_units = int(global_units.size)
    covariate = None if h_cov is None else np.asarray(h_cov[order], dtype=np.float64)

    panel_cutoffs: list[PanelCutoff] = []
    for cut, loaded in loads:
        units, values, secondary, _cov = _pool(loaded, input_kind)
        # map present units → global indices (units absent from the horizon cannot
        # occur under cumulative growth; guard drops any stray rather than crashing)
        keep = np.array([unit in global_index for unit in units], dtype=bool)
        if not keep.all():
            units, values = units[keep], values[keep]
            if secondary is not None:
                secondary = secondary[keep]
        unit_idx = np.array([global_index[unit] for unit in units], dtype=np.int64)
        elapsed = (cut.end_ts - grid.start_ts).total_seconds() / _DAY_SECONDS
        panel_cutoffs.append(
            PanelCutoff(
                elapsed_days=elapsed,
                is_horizon=cut.is_horizon,
                unit_idx=unit_idx,
                values=np.asarray(values, dtype=np.float64),
                secondary=None if secondary is None else np.asarray(secondary, dtype=np.float64),
            )
        )

    return PlaceboPanel(
        n_units=n_units,
        cutoffs=tuple(panel_cutoffs),
        covariate=covariate,
        input_kind=input_kind,
        kept_grid_points=kept,
        total_grid_points=total,
        # the sorted horizon unit ids at each global index — the composed family sweep
        # (D9/WP8) aligns ONE shared unit→arm assignment across metrics through these.
        unit_ids=np.asarray(global_units, dtype=object),
    )
