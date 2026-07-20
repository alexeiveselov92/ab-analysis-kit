"""Exposures mixin: ``_ab_exposures`` operations.

The persisted assignment cohort (cohort-copy mode only since m8 WP4 — the
no-copy default never writes here), JOINed by every metric query via the
packaged macro, and the SRM gate's count source in copy mode. READ-ONLY for
compute: only ``replace_exposures`` (the full resync), the WP5 incremental
append (``insert_exposures_incremental``) and ``purge_experiment`` write here.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from abkit.core.exposure_counting import arrival_rate, bucket_timestamps, count_stream
from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_EXPOSURES
from abkit.utils.datetime_utils import to_naive_utc

#: insert chunk size — bounds driver memory on multi-million-unit cohorts
EXPOSURE_INSERT_CHUNK = 100_000


class _ExposuresMixin(_InternalTablesBase):
    def replace_exposures(self, experiment: str, data: dict[str, np.ndarray]) -> int:
        """Replace the full cohort for *experiment*: sync delete + chunked insert.

        Idempotent per experiment (delete-then-insert keyed by experiment —
        plan R9): a re-run reloads the cohort from the assignment SQL and the
        SRM gate re-checks it. Returns the number of exposure rows written.

        ``data`` must contain ``unit_id``, ``variant``, ``exposure_ts`` and
        optionally ``stratum`` arrays (the exposure loader validates shapes);
        ``experiment`` and the ``loaded_at`` version are stamped here.
        """
        self._require_exposure_columns(data)
        self.delete_exposures(experiment)
        return self._insert_exposure_rows(experiment, data)

    def delete_exposures(self, experiment: str) -> None:
        """Drop the experiment's persisted cohort (sync — a rebuild follows).

        The ``abk run --resync-cohort`` first step (m8 WP5 round 2): the copy
        is then rebuilt through the incremental engine, so the rewrite honors
        the same closed/matured discipline as routine operation.
        """
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        self._manager.delete_rows(
            full_table_name, "experiment = %(e)s", {"e": experiment}, sync=True
        )

    def insert_exposures_incremental(self, experiment: str, data: dict[str, np.ndarray]) -> int:
        """Append exposure rows WITHOUT the preceding delete (m8 WP5).

        The incremental-copy write path: chunked ``conflict_strategy='ignore'``
        inserts exactly like :meth:`replace_exposures`'s loop, append-only.
        Idempotent under re-insert: the ``(experiment, unit_id)`` PK plus the
        ``loaded_at`` LWW version collapse a re-sent unit to one row
        (``ReplacingMergeTree`` FINAL on ClickHouse, version-aware upsert on
        PG/MySQL) — NOTE the LWW means a unit re-appearing in a LATER batch
        with a different ``exposure_ts`` keeps the LATER batch's value, unlike
        the full reload's global earliest-wins dedup; only malformed
        (duplicate-row) input can reach that divergence, and the run-level
        validation warning has already fired on it.
        """
        self._require_exposure_columns(data)
        return self._insert_exposure_rows(experiment, data)

    @staticmethod
    def _require_exposure_columns(data: dict[str, np.ndarray]) -> None:
        required = ("unit_id", "variant", "exposure_ts")
        missing = [c for c in required if c not in data]
        if missing:
            raise ValueError(f"exposure data is missing columns: {missing}")

    def _insert_exposure_rows(self, experiment: str, data: dict[str, np.ndarray]) -> int:
        """The one chunked, stamped insert loop both write paths share."""
        num_rows = len(data["unit_id"])
        if num_rows == 0:
            return 0
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)

        loaded_at = self.next_version_ts()
        stratum = data.get("stratum")
        if stratum is None:
            stratum = np.array([None] * num_rows, dtype=object)

        written = 0
        for start in range(0, num_rows, EXPOSURE_INSERT_CHUNK):
            end = min(start + EXPOSURE_INSERT_CHUNK, num_rows)
            chunk = {
                "experiment": np.full(end - start, experiment, dtype=object),
                "unit_id": data["unit_id"][start:end],
                "variant": data["variant"][start:end],
                "exposure_ts": data["exposure_ts"][start:end],
                "stratum": stratum[start:end],
                "loaded_at": np.full(end - start, loaded_at, dtype=object),
            }
            written += self._manager.insert_batch(
                full_table_name, chunk, conflict_strategy="ignore"
            )
        return written

    def exposures_table_exists(self) -> bool:
        """True when ``_ab_exposures`` exists — a never-run project has none.

        Read-only surfaces guard with this instead of ``ensure_tables()``;
        mirrors :meth:`results_table_exists` (m3-implementation-plan.md WP2).
        """
        return self._manager.table_exists(TABLE_EXPOSURES, schema=self._manager.internal_location)

    def get_exposure_counts(self, experiment: str) -> dict[str, int]:
        """Per-variant unit counts — the SRM gate's observed counts.

        Deduped (FINAL on ClickHouse) so a mid-merge ReplacingMergeTree never
        double-counts a unit (quorum "correctness under async merge"). Whole
        cohort by design: M2 SRM is a single whole-cohort check, so the report
        pairs these counts with that whole-run flag/pvalue (an as-of subset
        would mismatch it — review finding); per-cutoff SRM lands with
        sequential (M5).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        query = f"""
        SELECT variant, count(*) AS cnt
        FROM {full_table_name}{self._manager.final_modifier}
        WHERE experiment = %(e)s
        GROUP BY variant
        """
        rows = self._manager.execute_query(query, {"e": experiment})
        return {row["variant"]: int(row["cnt"]) for row in rows if row.get("variant")}

    def get_exposure_count_stream(
        self, experiment: str, boundaries: list[datetime], variants: list[str]
    ) -> list[dict[str, int]]:
        """Cumulative per-variant unit counts as-of each look boundary.

        The sub-day anytime-valid SRM (Lindon & Malek, cumulative-intervals.md
        §6.5) needs the running count history: for each half-open cumulative
        window ``[start, end_ts)`` the count of units first exposed BEFORE
        ``end_ts`` (exclusive, matching the metric-load windows). Reads the
        deduped persisted cohort ONCE and buckets in-process (one round trip,
        not one query per look — a sub-day grid has many looks); every variant
        in ``variants`` is zero-filled so a missing arm reads as 0 (the worst
        SRM). Returns one dict per boundary, aligned and ascending.

        (v1 reconstructs the full stream each run from ``_ab_exposures``, in
        keeping with the recompute-not-incremental read path; a bucketed-SQL or
        running-max-carry optimisation is a future v2.) The bucketing/bisect
        math is the shared ``core.exposure_counting`` implementation — the
        direct-mode driver buckets its in-memory snapshot through the SAME
        functions (m8 WP4), so the two source modes can never drift.
        """
        if not boundaries:
            return []
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT variant, exposure_ts FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE experiment = %(e)s",
            {"e": experiment},
        )
        per_variant = bucket_timestamps(
            ((row.get("variant"), to_naive_utc(row.get("exposure_ts"))) for row in rows),
            variants,
        )
        return count_stream(per_variant, boundaries, variants)

    def get_arrival_rate(
        self, experiment: str, variants: list[str]
    ) -> tuple[dict[str, float], float] | None:
        """Observed unit-arrival rate (units/day) per variant from ``_ab_exposures``.

        The read-only arrival source ``abk plan`` runtime/ASN needs (WP-A;
        m6-implementation-plan.md): reads the deduped cohort ONCE (one round trip,
        mirroring :meth:`get_exposure_count_stream`) and derives, per declared
        variant, ``count / observed-window-days`` where the window spans the WHOLE
        cohort's ``[min, max] exposure_ts`` (a shared calendar window, so the per-arm
        rates are mutually consistent). Every variant in ``variants`` is zero-filled.

        Returns ``(rates, window_days)`` or ``None`` when the window is degenerate —
        an empty cohort, or all exposures at ~one instant (``max == min``, e.g. a
        backfilled cohort). The caller then SKIPS runtime rather than inventing a rate
        (never extrapolates from a zero window). Never writes. The rate arithmetic is
        the shared ``core.exposure_counting.arrival_rate`` — direct mode derives the
        same numbers from its in-memory snapshot (m8 WP4).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT variant, exposure_ts FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE experiment = %(e)s",
            {"e": experiment},
        )
        per_variant = bucket_timestamps(
            ((row.get("variant"), to_naive_utc(row.get("exposure_ts"))) for row in rows),
            variants,
        )
        return arrival_rate(per_variant, variants)

    def get_first_exposure_ts(self, experiment: str) -> datetime | None:
        """Earliest exposure timestamp (diagnostics/plan).

        FINAL-deduped: since the m8 WP5 incremental append, multiple physical
        versions of a unit's row are ROUTINE on ClickHouse pre-merge (the
        boundary-bucket re-scan legitimately re-inserts units), so every
        timestamp read must collapse versions or risk reading a superseded
        value (quorum "correctness under async merge").
        """
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT min(exposure_ts) AS first_ts "
            f"FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE experiment = %(e)s",
            {"e": experiment},
        )
        if not rows:
            return None
        return self._normalize_max_timestamp(rows[0].get("first_ts"))

    def get_last_exposure_timestamp(self, experiment: str) -> datetime | None:
        """Latest persisted ``exposure_ts`` — the incremental copy's watermark.

        The mirror image of :meth:`get_first_exposure_ts` (``MAX`` for ``MIN``,
        same normalisation: ClickHouse's epoch-sentinel ``max()`` over an empty
        selection reads as ``None``). ``None`` means no cohort rows exist yet —
        the copy engine backfills from the experiment start (m8 WP5).

        FINAL-deduped — a correctness-sensitive read: a non-FINAL ``MAX`` over
        coexisting pre-merge row versions could return a stale, superseded
        timestamp and permanently inflate the resume watermark (a
        review-confirmed failure mode; the LWW value is the truth).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT max(exposure_ts) AS last_ts "
            f"FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE experiment = %(e)s",
            {"e": experiment},
        )
        if not rows:
            return None
        return self._normalize_max_timestamp(rows[0].get("last_ts"))

    def count_exposures(self, experiment: str) -> int:
        """Total cohort size (deduped)."""
        full_table_name = self._manager.get_full_table_name(TABLE_EXPOSURES, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT count(*) AS cnt FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE experiment = %(e)s",
            {"e": experiment},
        )
        return int(rows[0]["cnt"]) if rows else 0
