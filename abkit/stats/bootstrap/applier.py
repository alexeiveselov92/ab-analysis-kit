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

#: Named statistics available to the bootstrap methods (``STAT_PARAM`` values).
#: The legacy engine accepted arbitrary ``stat_func`` callables; abkit accepts
#: NAMES registered here instead (statistics-changes.md §7) so the statistic
#: stays part of the hashable, BI-stable method identity. Extend via
#: :func:`register_stat`.
STAT_FUNCS: dict[str, StatFunc] = {"mean": np.mean, "median": np.median}


def register_stat(name: str, func: StatFunc) -> None:
    """Register a custom named statistic usable as ``stat=<name>`` in bootstrap params.

    E.g. ``register_stat("p90", lambda a: float(np.quantile(a, 0.9)))``. The name
    (not the callable) enters ``method_config_id``, so re-registering a DIFFERENT
    function under an existing name is refused — it would silently change the
    numbers behind a published series identity.
    """
    if not name or not isinstance(name, str):
        raise MethodParamError("stat name must be a non-empty string")
    existing = STAT_FUNCS.get(name)
    if existing is not None and existing is not func:
        raise MethodParamError(
            f"stat {name!r} is already registered; pick a new name (the name is part of "
            "the method identity — rebinding it would silently change published numbers)"
        )
    STAT_FUNCS[name] = func


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
    """One statistic per resample row (baseline §4.1; vectorised fast path, H3).

    The built-in names take the vectorised fast path; registered custom stats
    fall back to the row-wise ``np.apply_along_axis`` (the legacy cost model —
    acceptable for opt-in custom statistics).
    """
    if isinstance(stat, str):
        if stat == "mean":
            return np.asarray(matrix.mean(axis=1), dtype=np.float64)
        if stat == "median":
            return np.asarray(np.median(matrix, axis=1), dtype=np.float64)
        try:
            stat = STAT_FUNCS[stat]
        except KeyError:
            raise MethodParamError(
                f"unknown stat {stat!r}; known stats: {sorted(STAT_FUNCS)}"
            ) from None
    return np.asarray(np.apply_along_axis(stat, 1, matrix), dtype=np.float64)
