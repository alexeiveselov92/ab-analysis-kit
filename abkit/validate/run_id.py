"""Deterministic, wall-clock-free run ids for ``_ab_aa_runs`` (m4 D4).

The table's PK is ``(experiment, run_id)`` under ``ReplacingMergeTree(created_at)``, so
a matrix written under one shared ``run_id`` would collapse to a single row after a
merge. Each row therefore gets a per-cell-unique id ``f"{run_stamp}:{cell_hash}"``:

- ``cell_hash`` identifies the scored cell (metric, method_config_id, mode, alpha) —
  stable across invocations;
- ``run_stamp`` identifies the invocation (the frozen run timestamp + selection
  inputs) — so re-running validate appends a fresh audit row rather than silently
  replacing the prior one, while ``find_calibration`` still picks the newest
  ``created_at`` among matching cells.

Both are ``sha256`` over ``json_dumps_sorted`` — no wall clock inside the hash (the
timestamp is an explicit input, the donor ``config_emitter.compute_run_id`` discipline).
"""

from __future__ import annotations

import hashlib
from typing import Any

from abkit.utils.json_utils import json_dumps_sorted


def _digest(payload: dict[str, Any], length: int) -> str:
    return hashlib.sha256(json_dumps_sorted(payload).encode("utf-8")).hexdigest()[:length]


def cell_hash(metric: str, method_config_id: str, mode: str, alpha: float) -> str:
    """A stable 12-hex id for one scored matrix cell."""
    return _digest(
        {"metric": metric, "method_config_id": method_config_id, "mode": mode, "alpha": alpha},
        12,
    )


def run_stamp(experiment: str, now_iso: str, selection: dict[str, Any]) -> str:
    """An 8-hex id for one validate invocation (the frozen run time is an input)."""
    return _digest({"experiment": experiment, "now": now_iso, "selection": selection}, 8)


def make_run_id(stamp: str, cell: str) -> str:
    """Combine the invocation stamp and the cell hash into the row's ``run_id``."""
    return f"{stamp}:{cell}"
