"""The placebo panel — per-cutoff pooled per-unit values the scorer resamples.

The load stage (WP2) fills this from the experiment's own cohort over the actual
cadence grid (docs/specs/m4-implementation-plan.md D1); WP1 defines the contract
and scores against it. All arrays are numpy, in canonical sorted-unit order (the
D11 byte-stability guarantee, metric_loader.py:186–205).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy.typing as npt

FloatArray = npt.NDArray  # float64 per-unit values
IntArray = npt.NDArray  # int64 global unit indices


@dataclass(frozen=True)
class PanelCutoff:
    """Pooled per-unit data at one cadence cutoff.

    A unit's arm is fixed at enrollment and constant across the grid (D1), so the
    cutoffs carry *global* unit indices (``unit_idx`` into ``[0, n_units)``) rather
    than re-indexing per cutoff. The unit set grows monotonically over cumulative
    windows (a unit present at cutoff ``k`` is present at every later cutoff).
    """

    elapsed_days: float
    is_horizon: bool
    #: Global unit indices present by this cutoff (into ``[0, PlaceboPanel.n_units)``).
    unit_idx: IntArray
    #: Primary per-unit value aligned to ``unit_idx`` — the metric value for a
    #: sample/CUPED metric, the per-unit numerator for a ratio metric, or the
    #: per-unit success count for a fraction metric.
    values: FloatArray
    #: Per-unit secondary array aligned to ``unit_idx`` — the denominator for a
    #: ratio metric, the trials (``nobs``) for a fraction metric, else None.
    secondary: FloatArray | None = None


@dataclass(frozen=True)
class PlaceboPanel:
    """The full panel for one (experiment, metric) over the subsampled grid.

    ``input_kind`` mirrors ``BaseMethod.input_kind`` (sample | ratio | fraction) and
    drives how each arm's sufficient statistics are built (resample.py).
    ``kept_grid_points``/``total_grid_points`` disclose any peeking-grid subsampling
    (aa-fpr §3 "the matrix must state when it did").
    """

    n_units: int
    cutoffs: tuple[PanelCutoff, ...]
    #: Per-unit covariate aligned to global indices ``[0, n_units)`` — a fixed
    #: pre-period constant (CUPED), present-independent; None when no covariate.
    covariate: FloatArray | None
    input_kind: str
    kept_grid_points: int
    total_grid_points: int
