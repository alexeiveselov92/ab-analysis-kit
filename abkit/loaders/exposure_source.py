"""Exposure source: pushdown validation of the assignment cohort (m8 WP2).

Replaces the historical full-materialize-in-Python dedup loop — which pulled
EVERY raw assignment row into Python and ran a row-by-row ``seen``-dict pass —
with a single pushdown aggregation query that returns at most ONE row per
``(unit, variant)``::

    SELECT <unit_key>, variant, MIN(exposure_ts) AS exposure_ts
           [, MIN(stratum) AS stratum], COUNT(*) AS ab_row_count
    FROM (<rendered assignment sql>) _abk_raw
    GROUP BY <unit_key>, variant

The much smaller aggregated result then feeds the SAME cross-variant hard-error
and duplicate-row warning checks the Python loop used to run — byte-identical
messages (the m8 §0.5(d) compatibility gate). ``MIN()`` is portable across
ClickHouse / PostgreSQL / MySQL, sidestepping the dialect-specific
``argMin`` / ``DISTINCT ON`` problem; ``COUNT(*)`` recovers the collapsed
duplicate count so the "one-row-per-unit?" warning still fires even though the
``GROUP BY`` has already deduped the rows the old loop would have iterated.

**Stratum tie-break on MALFORMED input — a DISCLOSED, ACCEPTED divergence
(m8 §0.5(b)).** When duplicate rows for the same unit carry BOTH a different
``exposure_ts`` AND a different ``stratum``, ``MIN(exposure_ts)`` and
``MIN(stratum)`` resolve INDEPENDENTLY, so the snapshot keeps the earliest
``exposure_ts`` but the lexicographically-smallest ``stratum`` — which may be a
*different* row's stratum than the earliest-``exposure_ts`` row the historical
Python loop took both fields off. Such input already trips the loud
"one-row-per-unit?" duplicate warning, so an arbitrary stratum tie-break on
already-malformed data is acceptable and disclosed here rather than masked. On
a WELL-FORMED (one-row-per-unit) cohort there is exactly one row per group, so
no divergence is possible and the milestone's numeric-parity gate holds
exactly.

I/O posture: this module READS the assignment source through the manager and
never writes (mirrors ``abkit/validate/load.py``). Persistence stays in the
caller (``exposure_loader`` today; the WP5 incremental-copy engine when
``assignment.cohort_copy.enabled``).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from abkit.config.experiment_config import ExperimentConfig
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.query_template import as_derived_table
from abkit.utils.datetime_utils import to_naive_utc


class ExposureLoadError(Exception):
    """Raised when the assignment result violates the exposure contract."""


@dataclass
class ExposureSnapshot:
    """The validated, deduped cohort — the exact shape the old loop produced.

    ``counts`` is the SRM gate's observed per-variant unit count (the same
    contract as ``load_exposures``'s return value); ``by_unit`` maps each unit
    to its ``(variant, exposure_ts, stratum)`` (the shape of the historical
    ``seen`` dict); ``has_stratum`` records whether the source carried a
    ``stratum`` column.
    """

    counts: dict[str, int]
    by_unit: dict[Any, tuple[str, datetime | None, Any]]
    has_stratum: bool


def probe_has_stratum(manager: BaseDatabaseManager, rendered_sql: str) -> bool:
    """Fetch ONE row of the rendered source and report whether it has stratum.

    Mirrors the historical ``has_stratum = 'stratum' in rows[0]`` check
    (``exposure_loader`` pre-WP2) but reads a single row via ``LIMIT 1`` instead
    of the whole result set. Exposed for the WP4 ``build_cohort_backend``
    factory, which needs ``has_stratum`` for the ``ab_cohort_source`` builtin.
    """
    rows = manager.execute_query(
        f"SELECT * FROM {as_derived_table(rendered_sql, '_abk_probe')} LIMIT 1"
    )
    return bool(rows) and "stratum" in rows[0]


def _pushdown_sql(unit_key: str, rendered_sql: str, has_stratum: bool) -> str:
    """The one-row-per-(unit, variant) validation query (module docstring)."""
    stratum_sel = ", MIN(stratum) AS stratum" if has_stratum else ""
    return (
        f"SELECT {unit_key}, variant, MIN(exposure_ts) AS exposure_ts"
        f"{stratum_sel}, COUNT(*) AS ab_row_count "
        f"FROM {as_derived_table(rendered_sql, '_abk_raw')} "
        f"GROUP BY {unit_key}, variant"
    )


def validate_and_snapshot(
    manager: BaseDatabaseManager,
    experiment: ExperimentConfig,
    rendered_sql: str,
    has_stratum: bool | None = None,
) -> ExposureSnapshot:
    """Validate the rendered assignment source and return the deduped snapshot.

    Runs the pushdown aggregation query (module docstring) and applies the
    identical cross-variant / undeclared-variant / duplicate-row checks the
    historical Python loop applied — same error/warning wording (§0.5(d)).
    ``has_stratum`` may be supplied by a caller that already probed (WP4);
    otherwise it is derived from the source's own columns.
    """
    unit_key = experiment.unit_key

    # A raw LIMIT-1 probe carries the empty-cohort + missing-column checks:
    # they MUST run on the source's actual columns before the aggregation
    # references exposure_ts, or the DB raises an unknown-column error instead
    # of the friendly "must SELECT ..." message (the §0.5(d) text gate).
    probe = manager.execute_query(
        f"SELECT * FROM {as_derived_table(rendered_sql, '_abk_probe')} LIMIT 1"
    )
    if not probe:
        raise ExposureLoadError(
            f"assignment query for experiment '{experiment.name}' returned no rows "
            "— check the assignment SQL and its filters"
        )
    required = (unit_key, "variant", "exposure_ts")
    missing = [c for c in required if c not in probe[0]]
    if missing:
        raise ExposureLoadError(
            f"assignment query must SELECT {list(required)} (missing: {missing}). "
            "Columns present: " + ", ".join(sorted(probe[0]))
        )
    if has_stratum is None:
        has_stratum = "stratum" in probe[0]

    rows = manager.execute_query(_pushdown_sql(unit_key, rendered_sql, has_stratum))

    declared = set(experiment.assignment.variants)
    seen: dict[Any, tuple[str, Any, Any]] = {}  # unit -> (variant, exposure_ts, stratum)
    counts: dict[str, int] = {}
    duplicate_rows = 0
    for row in rows:
        unit = row[unit_key]
        variant = row["variant"]
        if variant not in declared:
            raise ExposureLoadError(
                f"assignment returned variant '{variant}' not declared in "
                f"assignment.variants {sorted(declared)}"
            )
        if unit in seen:
            # GROUP BY (unit, variant) makes each pair unique, so a repeated
            # unit here is ALWAYS a cross-variant conflict.
            prev_variant = seen[unit][0]
            raise ExposureLoadError(
                f"unit '{unit}' is assigned to BOTH '{prev_variant}' and "
                f"'{variant}' — the assignment source is corrupted; "
                "every downstream effect would be untrustworthy"
            )
        exposure_ts = to_naive_utc(row["exposure_ts"])
        stratum = row.get("stratum") if has_stratum else None
        seen[unit] = (variant, exposure_ts, stratum)
        counts[variant] = counts.get(variant, 0) + 1
        duplicate_rows += int(row["ab_row_count"]) - 1

    if duplicate_rows:
        warnings.warn(
            f"assignment for '{experiment.name}' returned {duplicate_rows} duplicate "
            f"unit rows — deduped to the earliest exposure_ts. Is the assignment "
            "query one-row-per-unit?",
            stacklevel=2,
        )

    return ExposureSnapshot(counts=counts, by_unit=seen, has_stratum=has_stratum)
