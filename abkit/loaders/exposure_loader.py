"""Exposure loader: assignment SQL → the persisted ``_ab_exposures`` cohort.

Runs ONCE per experiment per run (quorum must-fix "persist the cohort once" —
metric SQL joins the persisted cohort instead of re-deriving it every
interval). The assignment source is READ-ONLY: abkit never randomizes and
never writes back into it; ``replace_exposures`` is idempotent per experiment
(delete-then-insert), so a re-run self-heals and the SRM gate re-checks the
fresh counts (plan R9).
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np

from abkit.config.experiment_config import ExperimentConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.query_template import QueryTemplate
from abkit.utils.datetime_utils import to_naive_utc


class ExposureLoadError(Exception):
    """Raised when the assignment result violates the exposure contract."""


def load_exposures(
    manager: BaseDatabaseManager,
    tables: InternalTablesManager,
    experiment: ExperimentConfig,
    assignment_sql: str,
    builtins: dict[str, Any],
    template: QueryTemplate | None = None,
) -> dict[str, int]:
    """Render + execute the assignment SQL, validate, persist the cohort.

    Contract on the assignment result set (declarative-config.md §2):
    one row per unit with columns ``<unit_key>``, ``variant``, ``exposure_ts``
    and optional ``stratum``. Violations:

    - missing required columns → error
    - a unit appearing in MORE THAN ONE variant → hard error (a corrupted
      assignment invalidates every downstream number)
    - duplicate rows within one variant → deduped to the EARLIEST exposure
      (legacy first-exposure semantics), with a loud warning
    - observed variants not declared in the config → error (the SRM
      expected_split could not cover them)
    - an empty cohort → error (a misconfigured source, not a valid state)

    Returns the per-variant unit counts (the SRM gate's observed counts).
    """
    template = template or QueryTemplate()
    rendered = template.render(assignment_sql, builtins)
    rows = manager.execute_query(rendered)

    if not rows:
        raise ExposureLoadError(
            f"assignment query for experiment '{experiment.name}' returned no rows "
            "— check the assignment SQL and its filters"
        )

    unit_key = experiment.unit_key
    required = (unit_key, "variant", "exposure_ts")
    missing = [c for c in required if c not in rows[0]]
    if missing:
        raise ExposureLoadError(
            f"assignment query must SELECT {list(required)} (missing: {missing}). "
            "Columns present: " + ", ".join(sorted(rows[0]))
        )
    has_stratum = "stratum" in rows[0]

    declared = set(experiment.assignment.variants)
    seen: dict[Any, tuple[str, Any, Any]] = {}  # unit -> (variant, exposure_ts, stratum)
    duplicate_rows = 0
    for row in rows:
        unit = row[unit_key]
        variant = row["variant"]
        if variant not in declared:
            raise ExposureLoadError(
                f"assignment returned variant '{variant}' not declared in "
                f"assignment.variants {sorted(declared)}"
            )
        exposure_ts = to_naive_utc(row["exposure_ts"])
        stratum = row.get("stratum") if has_stratum else None
        if unit in seen:
            prev_variant, prev_ts, prev_stratum = seen[unit]
            if prev_variant != variant:
                raise ExposureLoadError(
                    f"unit '{unit}' is assigned to BOTH '{prev_variant}' and "
                    f"'{variant}' — the assignment source is corrupted; "
                    "every downstream effect would be untrustworthy"
                )
            duplicate_rows += 1
            if exposure_ts is not None and (prev_ts is None or exposure_ts < prev_ts):
                seen[unit] = (variant, exposure_ts, stratum)
        else:
            seen[unit] = (variant, exposure_ts, stratum)

    if duplicate_rows:
        warnings.warn(
            f"assignment for '{experiment.name}' returned {duplicate_rows} duplicate "
            f"unit rows — deduped to the earliest exposure_ts. Is the assignment "
            "query one-row-per-unit?",
            stacklevel=2,
        )

    units = list(seen)
    data = {
        "unit_id": np.array([str(u) for u in units], dtype=object),
        "variant": np.array([seen[u][0] for u in units], dtype=object),
        "exposure_ts": np.array([seen[u][1] for u in units], dtype=object),
        "stratum": np.array([seen[u][2] for u in units], dtype=object),
    }
    tables.replace_exposures(experiment.name, data)

    counts: dict[str, int] = {}
    for variant, _, _ in seen.values():
        counts[variant] = counts.get(variant, 0) + 1
    return counts
