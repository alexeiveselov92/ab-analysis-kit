"""In-memory :class:`BaseDatabaseManager` for unit tests.

Behaves like the SQL backends (enforced primary key, LWW version upserts,
honest atomic lock claim) and evaluates the small SQL dialect the
internal-tables mixins actually emit — SELECT with optional DISTINCT,
aggregates (count/min/max/sum), WHERE equality/range predicates, GROUP BY and
ORDER BY. Anything outside that dialect raises, so a mixin quietly drifting
into unsupported SQL fails loudly here.

Also the fixture backend for the pipeline-level tests (idempotent re-run,
planner anti-join) in later work packages.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from abkit.core.models import TableModel
from abkit.database.manager import BaseDatabaseManager
from abkit.utils.datetime_utils import now_utc_naive

_WHERE_RE = re.compile(
    r"^(\w+)\s*(=|<>|>=|<=|>|<)\s*(%\((\w+)\)s|'([^']*)')$",
)
_SELECT_RE = re.compile(
    r"^SELECT\s+(DISTINCT\s+)?(?P<items>.+?)\s+FROM\s+(?P<table>\S+?)(?P<final>\s+FINAL)?"
    r"(?:\s+WHERE\s+(?P<where>.+?))?"
    r"(?:\s+GROUP\s+BY\s+(?P<group>.+?))?"
    r"(?:\s+ORDER\s+BY\s+(?P<order>.+?))?"
    r"(?:\s+LIMIT\s+(?P<limit>\S+))?$",
    re.IGNORECASE,
)
_ITEM_RE = re.compile(
    r"^(?:(?P<func>\w+)\((?P<arg>\*|\w+)\)|(?P<col>\w+))(?:\s+AS\s+(?P<alias>\w+))?$", re.IGNORECASE
)


def _coerce(value: Any) -> Any:
    """numpy → plain Python, NaN → None (mirrors the real managers)."""
    if value is None:
        return None
    if isinstance(value, np.datetime64):
        if np.isnat(value):
            return None
        seconds = (value - np.datetime64("1970-01-01T00:00:00")) / np.timedelta64(1, "s")
        return datetime.fromtimestamp(float(seconds), tz=timezone.utc).replace(tzinfo=None)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        f = float(value)
        return None if math.isnan(f) else f
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


class FakeDatabaseManager(BaseDatabaseManager):
    """Dict-backed manager with SQL-backend semantics."""

    def __init__(
        self,
        internal_location: str = "abkit_internal",
        data_location: str = "analytics",
        clickhouse_like: bool = False,
    ) -> None:
        self._internal = internal_location
        self._data = data_location
        self._clickhouse_like = clickhouse_like
        self._rows: dict[str, list[dict[str, Any]]] = {}
        self._models: dict[str, TableModel] = {}  # bare name -> model
        self.queries: list[tuple[str, dict | None]] = []  # every execute_query call
        self.closed = False

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _bare(table_name: str) -> str:
        return table_name.split(".")[-1]

    def _store(self, table_name: str) -> list[dict[str, Any]]:
        return self._rows.setdefault(self._bare(table_name), [])

    def _model(self, table_name: str) -> TableModel | None:
        return self._models.get(self._bare(table_name))

    def _parse_where(
        self, where_clause: str, params: dict[str, Any] | None
    ) -> list[tuple[str, str, Any]]:
        conditions: list[tuple[str, str, Any]] = []
        for raw in re.split(r"\s+AND\s+", where_clause.strip(), flags=re.IGNORECASE):
            match = _WHERE_RE.match(raw.strip())
            if not match:
                raise ValueError(f"FakeDatabaseManager cannot parse predicate: {raw!r}")
            col, op, _, param_name, literal = match.groups()
            value = literal if param_name is None else (params or {})[param_name]
            conditions.append((col, op, value))
        return conditions

    @staticmethod
    def _matches(row: dict[str, Any], conditions: list[tuple[str, str, Any]]) -> bool:
        for col, op, value in conditions:
            have = row.get(col)
            if op == "=":
                ok = have == value
            elif op == "<>":
                ok = have != value
            elif have is None or value is None:
                ok = False
            elif op == ">=":
                ok = have >= value
            elif op == "<=":
                ok = have <= value
            elif op == ">":
                ok = have > value
            else:
                ok = have < value
            if not ok:
                return False
        return True

    # ── ABC implementation ───────────────────────────────────────────────────

    def execute_query(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.queries.append((query, params))
        normalized = " ".join(query.split())
        if not normalized.upper().startswith("SELECT"):
            raise ValueError(f"FakeDatabaseManager only evaluates SELECT, got: {normalized!r}")
        match = _SELECT_RE.match(normalized)
        if not match:
            raise ValueError(f"FakeDatabaseManager cannot parse query: {normalized!r}")

        distinct = bool(match.group(1))
        table_bare = self._bare(match.group("table"))
        rows = [dict(r) for r in self._rows.get(table_bare, [])]
        model = self._models.get(table_bare)
        if self._clickhouse_like and model is not None and model.version_column:
            if match.group("final"):
                collapsed: dict[tuple, dict] = {}
                for r in rows:
                    key = tuple(repr(r.get(c)) for c in model.primary_key)
                    held = collapsed.get(key)
                    if held is None or (
                        r.get(model.version_column) is not None
                        and (
                            held.get(model.version_column) is None
                            or r[model.version_column] >= held[model.version_column]
                        )
                    ):
                        collapsed[key] = r
                rows = list(collapsed.values())
            else:
                keys = [tuple(repr(r.get(c)) for c in model.primary_key) for r in rows]
                if len(keys) != len(set(keys)):
                    raise AssertionError(
                        f"non-FINAL read of versioned table {table_bare} saw "
                        "duplicate primary keys — a correctness-sensitive read "
                        "is missing its dedup (quorum 'correctness under async merge')"
                    )
        if match.group("where"):
            conditions = self._parse_where(match.group("where"), params)
            rows = [r for r in rows if self._matches(r, conditions)]

        items_raw = [i.strip() for i in match.group("items").split(",")]
        group_cols = (
            [c.strip() for c in match.group("group").split(",")] if match.group("group") else None
        )

        result = self._project(rows, items_raw, distinct, group_cols)

        if match.group("order"):
            for spec in reversed([s.strip() for s in match.group("order").split(",")]):
                parts = spec.split()
                col = parts[0]
                desc = len(parts) > 1 and parts[1].upper() == "DESC"
                result.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if match.group("limit"):
            limit_raw = match.group("limit")
            limit = (
                (params or {}).get(limit_raw[2:-2])
                if limit_raw.startswith("%(")
                else int(limit_raw)
            )
            result = result[: int(limit)]
        return result

    def _project(
        self,
        rows: list[dict[str, Any]],
        items_raw: list[str],
        distinct: bool,
        group_cols: list[str] | None,
    ) -> list[dict[str, Any]]:
        items = []
        for raw in items_raw:
            if raw == "*":
                items.append(("*", None, None))
                continue
            m = _ITEM_RE.match(raw)
            if not m:
                raise ValueError(f"FakeDatabaseManager cannot parse select item: {raw!r}")
            if m.group("func"):
                items.append((m.group("func").lower(), m.group("arg"), m.group("alias")))
            else:
                items.append(("col", m.group("col"), m.group("alias") or m.group("col")))

        has_aggregate = any(f not in ("*", "col") for f, _, _ in items)

        if items == [("*", None, None)]:
            projected = [dict(r) for r in rows]
        elif has_aggregate:
            groups: dict[tuple, list[dict]] = {}
            if group_cols:
                for r in rows:
                    groups.setdefault(tuple(r.get(c) for c in group_cols), []).append(r)
            else:
                groups[()] = rows
            projected = []
            for key, grouped in groups.items():
                out: dict[str, Any] = {}
                if group_cols:
                    out.update(dict(zip(group_cols, key, strict=True)))
                for func, arg, alias in items:
                    if func == "col":
                        if group_cols is None or arg not in group_cols:
                            raise ValueError(f"non-grouped bare column {arg!r} with aggregates")
                        continue
                    name = alias or f"{func}({arg})"
                    values = (
                        [r.get(arg) for r in grouped if r.get(arg) is not None]
                        if arg != "*"
                        else grouped
                    )
                    if func == "count":
                        out[name] = len(grouped) if arg == "*" else len(values)
                    elif func == "min":
                        out[name] = min(values) if values else None
                    elif func == "max":
                        out[name] = max(values) if values else None
                    elif func == "sum":
                        out[name] = sum(values) if values else None
                    else:
                        raise ValueError(f"unsupported aggregate: {func}")
                projected.append(out)
        else:
            projected = [
                {alias: r.get(col) for kind, col, alias in items if kind == "col"} for r in rows
            ]

        if distinct:
            seen = set()
            unique = []
            for r in projected:
                key = tuple(sorted((k, repr(v)) for k, v in r.items()))
                if key not in seen:
                    seen.add(key)
                    unique.append(r)
            projected = unique
        return projected

    def create_table(
        self, table_name: str, table_model: TableModel, if_not_exists: bool = True
    ) -> None:
        bare = self._bare(table_name)
        if bare in self._models and not if_not_exists:
            raise ValueError(f"table exists: {table_name}")
        self._models[bare] = table_model
        self._rows.setdefault(bare, [])

    def register_table(self, table_name: str, table_model: TableModel) -> None:
        self._models[self._bare(table_name)] = table_model

    def table_exists(self, table_name: str, schema: str | None = None) -> bool:
        return self._bare(table_name) in self._rows

    def insert_batch(
        self, table_name: str, data: dict[str, np.ndarray], conflict_strategy: str = "ignore"
    ) -> int:
        if not data:
            return 0
        lengths = {len(arr) for arr in data.values()}
        if len(lengths) > 1:
            raise ValueError("All arrays must have same length")
        num_rows = lengths.pop()
        model = self._model(table_name)
        store = self._store(table_name)
        pk = model.primary_key if model else []
        version_col = model.version_column if model else None

        inserted = 0
        for i in range(num_rows):
            row = {col: _coerce(data[col][i]) for col in data}
            if self._clickhouse_like and version_col is not None:
                # ReplacingMergeTree semantics: duplicates coexist until a
                # merge; only FINAL reads collapse them (see execute_query).
                store.append(row)
                inserted += 1
                continue
            if pk:
                key = tuple(row.get(c) for c in pk)
                existing = next((r for r in store if tuple(r.get(c) for c in pk) == key), None)
                if existing is not None:
                    if conflict_strategy == "fail":
                        raise ValueError(f"duplicate primary key: {key}")
                    if conflict_strategy == "replace" or (
                        version_col is not None
                        and row.get(version_col) is not None
                        and (
                            existing.get(version_col) is None
                            or row[version_col] >= existing[version_col]
                        )
                    ):
                        existing.clear()
                        existing.update(row)
                    inserted += 1
                    continue
            store.append(row)
            inserted += 1
        return inserted

    def get_max_timestamp(
        self,
        table_name: str,
        where_clause: str = "",
        params: dict[str, Any] | None = None,
        timestamp_column: str = "timestamp",
    ) -> datetime | None:
        rows = self._store(table_name)
        if where_clause:
            conditions = self._parse_where(where_clause, params)
            rows = [r for r in rows if self._matches(r, conditions)]
        values = [r.get(timestamp_column) for r in rows if r.get(timestamp_column) is not None]
        return max(values) if values else None

    def try_acquire_lock(
        self,
        table_name: str,
        key_columns: dict[str, Any],
        row: dict[str, Any],
        *,
        status_column: str = "status",
        running_value: str = "running",
        heartbeat_column: str = "started_at",
        timeout_seconds: int = 3600,
        token_column: str | None = None,
    ) -> bool:
        for key, value in key_columns.items():
            if row.get(key) != value:
                raise ValueError(f"lock row value for {key!r} != key_columns value")
        store = self._store(table_name)
        stale_before = now_utc_naive() - timedelta(seconds=timeout_seconds)
        matching = [r for r in store if all(r.get(k) == v for k, v in key_columns.items())]
        for r in matching:
            heartbeat = r.get(heartbeat_column)
            if (
                r.get(status_column) == running_value
                and heartbeat is not None
                and heartbeat >= stale_before
            ):
                return False
        for r in matching:
            store.remove(r)
        store.append({k: _coerce(v) for k, v in row.items()})
        return True

    def upsert_record(
        self,
        table_name: str,
        key_columns: dict[str, Any],
        data: dict[str, np.ndarray],
        sync: bool = False,
    ) -> int:
        store = self._store(table_name)
        store[:] = [r for r in store if not all(r.get(k) == v for k, v in key_columns.items())]
        return self.insert_batch(table_name, data, conflict_strategy="fail")

    def delete_rows(
        self,
        table_name: str,
        where_clause: str,
        params: dict[str, Any] | None = None,
        sync: bool = False,
    ) -> int:
        store = self._store(table_name)
        conditions = self._parse_where(where_clause, params)
        keep = [r for r in store if not self._matches(r, conditions)]
        deleted = len(store) - len(keep)
        store[:] = keep
        return deleted

    @property
    def final_modifier(self) -> str:
        return " FINAL" if self._clickhouse_like else ""

    @property
    def internal_location(self) -> str:
        return self._internal

    @property
    def data_location(self) -> str:
        return self._data

    def close(self) -> None:
        self.closed = True
