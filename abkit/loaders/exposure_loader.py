"""Exposure loader: assignment SQL → the persisted ``_ab_exposures`` cohort.

Runs ONCE per experiment per run (quorum must-fix "persist the cohort once" —
metric SQL joins the persisted cohort instead of re-deriving it every
interval). The assignment source is READ-ONLY: abkit never randomizes and
never writes back into it; ``replace_exposures`` is idempotent per experiment
(delete-then-insert), so a re-run self-heals and the SRM gate re-checks the
fresh counts (plan R9).

M8 WP2 moved the validation/dedup mechanism into ``exposure_source``: the
row-by-row Python loop is now a single pushdown ``GROUP BY`` query (see that
module's docstring). Since WP4 the driver goes through
``exposure_source.build_cohort_backend``; the no-copy default never writes
``_ab_exposures``. Since WP5 ALL copy-mode driver writes go through the
incremental engine (``exposure_copy`` — ``--resync-cohort`` is a delete plus
a from-scratch reload through the same engine); :func:`persist_snapshot` and
:func:`load_exposures` remain the one-call render+validate+persist
orchestration for external callers (m8-implementation-plan.md).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from abkit.config.experiment_config import ExperimentConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.exposure_source import (
    ExposureLoadError,
    ExposureSnapshot,
    validate_and_snapshot,
)
from abkit.loaders.query_template import QueryTemplate

__all__ = ["ExposureLoadError", "load_exposures", "persist_snapshot"]


def persist_snapshot(
    tables: InternalTablesManager, experiment_name: str, snapshot: ExposureSnapshot
) -> int:
    """Persist a validated snapshot as the full ``_ab_exposures`` cohort.

    The full-reload write path (delete + chunked reinsert —
    ``replace_exposures``): since m8 WP5 the driver never calls this — every
    driver copy-mode write (``--resync-cohort`` included) goes through the
    incremental engine's closed/matured discipline — so this remains the
    external one-call orchestrator's persist only. Returns the number of
    rows written.
    """
    units = list(snapshot.by_unit)
    data = {
        "unit_id": np.array([str(u) for u in units], dtype=object),
        "variant": np.array([snapshot.by_unit[u][0] for u in units], dtype=object),
        "exposure_ts": np.array([snapshot.by_unit[u][1] for u in units], dtype=object),
        "stratum": np.array([snapshot.by_unit[u][2] for u in units], dtype=object),
    }
    return tables.replace_exposures(experiment_name, data)


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

    # The pushdown GROUP BY validation + dedup (exposure_source.validate_and_snapshot):
    # same contract, same error/warning wording, one aggregated result set instead
    # of the whole raw cohort materialized in Python.
    snapshot = validate_and_snapshot(manager, experiment, rendered)

    persist_snapshot(tables, experiment.name, snapshot)
    return snapshot.counts
