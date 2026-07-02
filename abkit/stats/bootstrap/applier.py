"""Statistic application over resample matrices (hygiene H3).

The legacy ``StatFuncApplier`` ran ``np.apply_along_axis(stat_func, 1, matrix)``
— a Python-level row loop that dominated bootstrap cost. H3
(docs/specs/statistics-changes.md §2): the named statistics take a vectorised
fast path (``matrix.mean(axis=1)`` / ``np.median(matrix, axis=1)``);
``np.apply_along_axis`` remains only as the fallback for arbitrary callables
(future stats). Both are row-independent reductions, so results never depend on
how replicate rows are grouped into blocks (the H10 streaming invariant).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from abkit.stats.exceptions import MethodParamError
from abkit.stats.samples import FloatArray

StatFunc = Callable[[FloatArray], float]

#: Named statistics available to the bootstrap methods (``STAT_PARAM`` choices).
STAT_FUNCS: dict[str, StatFunc] = {"mean": np.mean, "median": np.median}


def stat_point(array: FloatArray, stat: str | StatFunc) -> float:
    """Apply ``stat`` to one 1-D array — the real-data point estimate (H9)."""
    if isinstance(stat, str):
        try:
            func = STAT_FUNCS[stat]
        except KeyError:
            raise MethodParamError(
                f"unknown stat {stat!r}; known stats: {sorted(STAT_FUNCS)}"
            ) from None
        return float(func(array))
    return float(stat(array))


def apply_stat(matrix: FloatArray, stat: str | StatFunc) -> FloatArray:
    """One statistic per resample row (baseline §4.1; vectorised fast path, H3)."""
    if isinstance(stat, str):
        if stat == "mean":
            return np.asarray(matrix.mean(axis=1), dtype=np.float64)
        if stat == "median":
            return np.asarray(np.median(matrix, axis=1), dtype=np.float64)
        raise MethodParamError(f"unknown stat {stat!r}; known stats: {sorted(STAT_FUNCS)}")
    return np.asarray(np.apply_along_axis(stat, 1, matrix), dtype=np.float64)
