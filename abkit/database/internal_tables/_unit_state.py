"""Unit-state mixin: ``_ab_unit_state`` operations (the scalability seam).

The two invariants that would be silent corruption once the read path flips
(m9 WP4's opt-in ``compute.incremental_reads``) — cumulative-intervals.md
§5.2/§5.3:

1. **Idempotent per (source, column-set, day)**: replace-not-sum. Writing a
   day twice leaves aggregates unchanged (the twice-run invariant test).
2. **Cardinality key** ``(source_table, column_set_id, unit_id, day)``, one
   row per unit per day — never one row per cumulative window.

``source_table`` is NOT a warehouse table name: v1 scopes it per
``"{experiment}/{metric}"`` (:func:`compute_state_source_id`), because the
day render is cohort-filtered, so two experiments over one fact table produce
DIFFERENT per-unit moments and sharing a series between them would clobber
rather than save. §5.3's co-located-metric sharing is therefore a deliberate
v1 non-goal, recorded as a decision in cumulative-intervals.md §5.3.

The state stage advances only at day close (§6.4); sub-day cutoffs read
closed-day state plus a current-day fact tail.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

import numpy as np

from abkit.database.internal_tables._base import _InternalTablesBase
from abkit.database.tables import TABLE_UNIT_STATE
from abkit.utils.json_utils import json_dumps_sorted

#: moment columns in table order; absent moments must be written as 0.0
MOMENT_COLUMNS = (
    "n",
    "sum_value",
    "sum_value_sq",
    "sum_cov",
    "sum_cov_sq",
    "sum_value_cov",
    "sum_denominator",
    "sum_denominator_sq",
    "sum_value_denominator",
)


def compute_column_set_id(source_table: str, column_roles: dict[str, str]) -> str:
    """Identity of a (source table, column-role set) pair — 16 hex chars.

    Two metrics reading the same columns of the same fact table share one
    state series (§5.3). Hashed over the canonical JSON of the source table
    plus the role->column mapping so identity survives dict ordering.
    """
    payload = json_dumps_sorted({"source_table": source_table, "columns": column_roles})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


#: ``source_table`` column budget (``tables.get_unit_state_table_model``) —
#: a MySQL VARCHAR overflow would silently truncate-and-merge two series
_SOURCE_TABLE_MAX_LENGTH = 128


def compute_state_source_id(experiment: str, metric_name: str) -> str:
    """The v1 state-series ``source_table`` key: ``"{experiment}/{metric}"``.

    m9 WP3 deliberately narrows §5.3's source-table-sharing ideal (recorded in
    m9-implementation-plan.md §8 Q1): the per-day render joins THIS
    experiment's cohort with the exposure filter applied, so the moments are
    cohort-dependent and the series must be scoped per (experiment, metric) —
    two experiments sharing a metric would otherwise clobber each other
    through replace-not-sum. ``/`` cannot appear in either validated name, so
    the composite never collides; an overlong composite keeps a readable
    prefix and appends a hash tail to stay inside the column budget.
    """
    composite = f"{experiment}/{metric_name}"
    if len(composite) <= _SOURCE_TABLE_MAX_LENGTH:
        return composite
    digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:16]
    return f"{composite[:_SOURCE_TABLE_MAX_LENGTH - 17]}#{digest}"


#: opening delimiters of a span whose BYTES are data, not formatting
_QUOTE_CHARS = "'\"`"
#: a dollar-quoted body (``$$…$$``, ``$tag$…$tag$``) — PostgreSQL only, and a
#: shape this scanner deliberately refuses to interpret (see ``_scan_spans``)
_DOLLAR_TAG = re.compile(r"\$[A-Za-z_0-9]*\$")


def _collapse(text: str) -> str:
    """Whitespace-collapse one fragment, preserving that separation existed.

    A fragment that had leading/trailing whitespace keeps exactly one space
    there, so ``'a' 'b'`` (two literals) can never normalize onto ``'a''b'``
    (one literal containing a quote).
    """
    if not text:
        return ""
    core = " ".join(text.split())
    if not core:
        return " "
    lead = " " if text[:1].isspace() else ""
    trail = " " if text[-1:].isspace() else ""
    return f"{lead}{core}{trail}"


def _scan_spans(sql: str) -> list[tuple[int, int, bool]] | None:
    """Tokenize into ``(start, end, is_data)`` spans, or ``None`` if unsure.

    ``is_data`` marks a quoted span (string literal or quoted identifier):
    its bytes are content, so whitespace inside it is significant. Comments
    are returned with ``is_data=False`` — their whitespace is formatting like
    any other — but they MUST be recognized, because an apostrophe inside one
    (``-- don't sum``) would otherwise open a phantom literal and shift every
    later boundary.

    Returning ``None`` is the safety valve, and the whole design rests on it:
    the two ways to be wrong are NOT symmetric. Treating data as formatting
    silently reuses stale day state under changed semantics (the P1 this
    function exists to prevent); treating formatting as data merely orphans a
    series that is then re-materialized (a wasted render). So anything this
    scanner cannot read unambiguously — a backslash inside a literal (an
    escape on MySQL/ClickHouse, a plain character on standard-conforming
    PostgreSQL), a dollar-quoted body, a ``#`` (a comment on MySQL and
    ClickHouse, the XOR operator on PostgreSQL, the tail of a Jinja ``{#``),
    an unterminated quote or block comment — makes the caller hash the RAW
    text instead of guessing which halves are data.
    """
    spans: list[tuple[int, int, bool]] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "#":
            return None
        if ch == "$" and _DOLLAR_TAG.match(sql, i):
            return None
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            spans.append((i, end, False))
            i = end
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end == -1:
                return None
            spans.append((i, end + 2, False))
            i = end + 2
            continue
        if ch in _QUOTE_CHARS:
            j = i + 1
            while j < n:
                if sql[j] == "\\":
                    return None  # escape semantics are dialect-dependent
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:  # '' — the embedded quote
                        j += 2
                        continue
                    break
                j += 1
            else:
                return None  # unterminated
            if j >= n:
                return None
            spans.append((i, j + 1, True))
            i = j + 1
            continue
        i += 1
    return spans


def normalize_sql_for_identity(sql: str) -> str:
    """Canonical form of a SQL body for identity hashing.

    Whitespace is collapsed everywhere EXCEPT inside quoted spans (string
    literals and quoted identifiers), which are carried through byte for
    byte. Both halves are load-bearing:

    - collapsing outside them is what keeps a pure reformat (indentation,
      line breaks, a rewrapped comment) from orphaning a materialized state
      series;
    - preserving them is what keeps a semantic edit from being INVISIBLE.
      ``WHERE campaign = 'Summer  Sale'`` and ``… 'Summer Sale'`` select
      different rows, yet a blanket ``" ".join(sql.split())`` maps them to the
      same string — the series id would not move, the stale-series supersede
      loop would not fire, and days materialized under the old filter would be
      summed under the new one (an R1 review finding at the m9 exit gate).

    When :func:`_scan_spans` cannot read the text unambiguously the raw body
    is hashed verbatim: an unreadable query orphans its series on any edit
    (cheap, self-healing) rather than risking a silent stale reuse.
    """
    spans = _scan_spans(sql)
    if spans is None:
        return sql
    pieces: list[str] = []
    last = 0
    for start, end, is_data in spans:
        pieces.append(_collapse(sql[last:start]))
        body = sql[start:end]
        pieces.append(body if is_data else _collapse(body))
        last = end
    pieces.append(_collapse(sql[last:]))
    return "".join(pieces).strip()


def compute_metric_state_id(
    column_roles: dict[str, str],
    metric_sql: str,
    cohort_config: dict[str, Any] | None = None,
) -> str:
    """State-series identity of a metric's (role map, SQL body) — 16 hex chars.

    The m9 WP3 metric-hash invalidation: editing the SQL body (not just the
    column roles) must orphan stale day state — metrics have no
    ``method_config_id`` analogue, so this hash introduces that mechanism.
    The SQL text goes through :func:`normalize_sql_for_identity` first, so
    reformatting alone never orphans a series while an edit inside a string
    literal always does.

    ``cohort_config`` folds the cohort-shaping experiment config into the
    identity (an R1 review fix): the day render joins the experiment's
    cohort, so an edit that reshapes cohort membership (the assignment SQL,
    ``added_filters``, ``unit_key``, ``variants``, ``timezone``,
    ``start_ts``) must orphan the series exactly like a metric-SQL edit —
    a merged series would otherwise mix two cohort definitions across days,
    an inconsistency the full-window recompute path can never have.
    """
    normalized = normalize_sql_for_identity(metric_sql)
    sql_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    payload = json_dumps_sorted(
        {
            "columns": column_roles,
            "metric_sql_sha256": sql_hash,
            "cohort": cohort_config or {},
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class _UnitStateMixin(_InternalTablesBase):
    def replace_day_state(
        self,
        source_table: str,
        column_set_id: str,
        day: date,
        data: dict[str, np.ndarray],
    ) -> int:
        """Replace one closed day's per-unit moments (replace-not-sum, §5.2).

        Synchronously deletes every row for ``(source_table, column_set_id,
        day)`` then inserts the new batch, so a re-run/backfill/lost-lock
        retry can never double-count. ``data`` must contain ``unit_id`` plus
        any subset of :data:`MOMENT_COLUMNS` (missing moments are written as
        zeros); the ``version`` is stamped here.
        """
        if "unit_id" not in data:
            raise ValueError("unit state data must contain a unit_id column")
        unknown = [c for c in data if c != "unit_id" and c not in MOMENT_COLUMNS]
        if unknown:
            raise ValueError(f"unknown unit-state moment columns: {unknown}")

        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        self._manager.delete_rows(
            full_table_name,
            "source_table = %(s)s AND column_set_id = %(c)s AND day = %(d)s",
            {"s": source_table, "c": column_set_id, "d": day},
            sync=True,
        )

        num_rows = len(data["unit_id"])
        if num_rows == 0:
            return 0

        version = self.next_version_ts()
        insert_data: dict[str, np.ndarray] = {
            "source_table": np.full(num_rows, source_table, dtype=object),
            "column_set_id": np.full(num_rows, column_set_id, dtype=object),
            "unit_id": data["unit_id"],
            "day": np.full(num_rows, day, dtype=object),
        }
        for moment in MOMENT_COLUMNS:
            if moment in data:
                insert_data[moment] = data[moment]
            elif moment == "n":
                insert_data[moment] = np.zeros(num_rows, dtype=np.int64)
            else:
                insert_data[moment] = np.zeros(num_rows, dtype=np.float64)
        insert_data["version"] = np.full(num_rows, version, dtype=object)

        return self._manager.insert_batch(full_table_name, insert_data, conflict_strategy="ignore")

    def sum_moments(
        self,
        source_table: str,
        column_set_id: str,
        from_day: date,
        to_day: date,
    ) -> dict[str, float]:
        """Aggregate moments over ``[from_day, to_day]`` (both inclusive).

        Deduped (FINAL on ClickHouse) so replace-not-sum versions never
        double-count mid-merge — the read side of the §5.2 invariant, and the
        assertion surface for the twice-run test. v1 uses this only in tests;
        the v2 incremental backend will read per-unit rows.
        """
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        select = ", ".join(f"sum({m}) AS {m}" for m in MOMENT_COLUMNS)
        rows = self._manager.execute_query(
            f"SELECT {select} FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE source_table = %(s)s AND column_set_id = %(c)s "
            "AND day >= %(from)s AND day <= %(to)s",
            {"s": source_table, "c": column_set_id, "from": from_day, "to": to_day},
        )
        if not rows:
            return dict.fromkeys(MOMENT_COLUMNS, 0.0)
        row: dict[str, Any] = rows[0]
        return {m: float(row[m]) if row.get(m) is not None else 0.0 for m in MOMENT_COLUMNS}

    def per_unit_cumulative(
        self,
        source_table: str,
        column_set_id: str,
        from_day: date,
        to_day: date,
    ) -> dict[str, dict[str, float]]:
        """Per-unit cumulative moments over ``[from_day, to_day]`` (inclusive).

        The v2 incremental read (m9 WP4): one cheap additive ``SUM`` per unit
        over the closed-day state rows — no subtraction, no cancellation risk —
        replacing the raw fact rescan. Deduped (FINAL on ClickHouse) so
        replace-not-sum versions never double-count mid-merge (§5.2). Returns
        ``{unit_id: {moment: float}}`` with every :data:`MOMENT_COLUMNS` key
        present; unit ids are strings (the metric loader's convention — state
        rows are written from its arrays).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        select = ", ".join(f"sum({m}) AS {m}" for m in MOMENT_COLUMNS)
        rows = self._manager.execute_query(
            f"SELECT unit_id, {select} FROM {full_table_name}{self._manager.final_modifier} "
            "WHERE source_table = %(s)s AND column_set_id = %(c)s "
            "AND day >= %(from)s AND day <= %(to)s GROUP BY unit_id",
            {"s": source_table, "c": column_set_id, "from": from_day, "to": to_day},
        )
        return {
            str(row["unit_id"]): {
                m: float(row[m]) if row.get(m) is not None else 0.0 for m in MOMENT_COLUMNS
            }
            for row in rows
        }

    def list_state_sources(self) -> list[str]:
        """Every ``source_table`` key present in ``_ab_unit_state``.

        The wide net `abk clean` needs (m9 WP5): the per-run sweep in
        ``pipeline/state.py`` only revisits the source keys THIS run touches,
        so a series orphaned by a removed comparison, a renamed metric or a
        deleted experiment would otherwise sit there forever — nothing else
        enumerates the table (state rows are not experiment-keyed, so the
        ``purge_experiment`` machinery cannot reach them either).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT DISTINCT source_table FROM {full_table_name}{self._manager.final_modifier}"
        )
        return sorted(row["source_table"] for row in rows)

    def unit_state_table_exists(self) -> bool:
        """True when ``_ab_unit_state`` exists — a never-run project has none."""
        return self._manager.table_exists(TABLE_UNIT_STATE, schema=self._manager.internal_location)

    def list_state_column_sets(self, source_table: str) -> list[str]:
        """Distinct ``column_set_id`` series stored under one source key.

        The m9 WP3 orphan sweep reads this to find series whose identity a
        metric-SQL edit superseded (deleted via :meth:`delete_state_series`
        so a future reader can never sum a stale definition).
        """
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT DISTINCT column_set_id FROM {full_table_name}"
            f"{self._manager.final_modifier} WHERE source_table = %(s)s",
            {"s": source_table},
        )
        return sorted(row["column_set_id"] for row in rows)

    def delete_state_series(self, source_table: str, column_set_id: str) -> None:
        """Drop one whole state series (orphan cleanup / ``--resync-cohort``)."""
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        self._manager.delete_rows(
            full_table_name,
            "source_table = %(s)s AND column_set_id = %(c)s",
            {"s": source_table, "c": column_set_id},
            sync=True,
        )

    def delete_state_days_from(self, source_table: str, column_set_id: str, from_day: date) -> None:
        """Truncate a state series from ``from_day`` (inclusive) onward.

        The m9 WP3 tail truncation: keeps every earlier day intact so
        ``get_last_state_day`` falls back to the last still-valid day and the
        contiguity invariant (every day <= it is materialized) survives both
        a full-refresh restart and the non-finite bailout.
        """
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        self._manager.delete_rows(
            full_table_name,
            "source_table = %(s)s AND column_set_id = %(c)s AND day >= %(d)s",
            {"s": source_table, "c": column_set_id, "d": from_day},
            sync=True,
        )

    def get_last_state_day(self, source_table: str, column_set_id: str) -> date | None:
        """Latest closed day materialized for this state series."""
        full_table_name = self._manager.get_full_table_name(TABLE_UNIT_STATE, use_internal=True)
        rows = self._manager.execute_query(
            f"SELECT max(day) AS last_day FROM {full_table_name} "
            "WHERE source_table = %(s)s AND column_set_id = %(c)s",
            {"s": source_table, "c": column_set_id},
        )
        if not rows or rows[0].get("last_day") is None:
            return None
        last_day = rows[0]["last_day"]
        # ClickHouse returns the epoch date for an empty max(); normalise.
        if last_day == date(1970, 1, 1):
            return None
        return last_day
