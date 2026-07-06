"""Assemble the report/explore ``calibration`` payload block from ``_ab_aa_runs`` rows.

The A/A matrix's analyst-facing clarity IS the M4 feature (aa-false-positive-matrix.md
§4): the readout report and the explore chip both surface *"nominal α X%, real peeking
FPR Y%"* plus the per-method matrix with a Recommended row. This module maps the
persisted audit rows (the as-built ``_ab_aa_runs`` shape, tables.py:224–263) into the
``CalibrationBlock`` contract the renderer consumes (web/src/shared/payload.ts) — kept
in documented lockstep with that TS interface, the ``builder.py`` doctrine.

Row selection: one ``abk validate`` invocation writes a whole matrix under a shared
``run_stamp`` (the ``{run_stamp}:{cell_hash}`` id, D4). The matrix shown is the **latest
invocation's** — all rows whose ``run_id`` prefix matches the newest row's ``run_stamp``
— so the Recommended row and its peers are always internally coherent (never a mix of
runs). The D3 chip lookup (``find_calibration``) is independent and keeps its own
newest-per-cell semantics; this block is display-only.

Pure: no DB, no clock, no NaN/inf leaks (every number passes ``_num``).
"""

from __future__ import annotations

import math
from typing import Any

from abkit.utils.json_utils import json_loads


def _num(value: Any) -> float | None:
    """A stored Nullable number → float, or ``None`` for NULL / NaN / ±inf."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _details(row: dict) -> dict:
    """Parse the ``details`` JSON cell (stored as a string) into a dict."""
    raw = row.get("details")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json_loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_stamp(row: dict) -> str:
    """The invocation stamp — the ``run_id`` prefix before the ``:`` (D4)."""
    return str(row.get("run_id", "")).split(":", 1)[0]


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _curve(details: dict, key: str) -> list[list[float]] | None:
    """A stored ``[[x, y], ...]`` curve → a clean float list, or ``None``."""
    raw = details.get(key)
    if not isinstance(raw, list) or not raw:
        return None
    return [[float(p[0]), float(p[1])] for p in raw if isinstance(p, (list, tuple)) and len(p) == 2]


def _matrix_row(row: dict) -> dict:
    """One persisted ``_ab_aa_runs`` row → a renderer ``CalibrationRow``."""
    details = _details(row)
    fpr = _num(row.get("fpr"))
    budget = _num(row.get("budget"))
    if budget is None:
        budget = _num(details.get("budget"))
    over_budget = fpr is not None and budget is not None and fpr > budget
    peeking_curve = _curve(details, "peeking_curve")
    kept = details.get("kept_grid_points")
    total = details.get("total_grid_points")
    note = None
    if isinstance(kept, int) and isinstance(total, int) and kept < total:
        note = f"{kept}/{total} looks scored (denser-early subsample)"
    return {
        "metric": row.get("metric"),
        "method": row.get("method_name"),
        "method_config_id": row.get("method_config_id"),
        "fpr": fpr,
        "single_look_fpr": _num(details.get("single_look_fpr")),
        "peeking_fpr": _num(row.get("peeking_fpr")),
        "power": _num(row.get("power")),
        "achieved_mde": _num(row.get("achieved_mde")),
        "coverage": _num(row.get("coverage")),
        "effect_exaggeration": _num(row.get("effect_exaggeration")),
        "alpha": _num(row.get("alpha")),
        "budget": budget,
        "over_budget": over_budget,
        "recommended": bool(details.get("recommended", False)),
        "rationale": details.get("recommended_rationale"),
        "verdict": row.get("verdict") or "",
        "status": row.get("status") or "success",
        "iterations": row.get("iterations"),
        "injected_effect": _num(row.get("injected_effect")),
        "peeking_curve": peeking_curve,
        "note": note,
        # M5 D8 — the always-valid column, side-by-side (m5-implementation-plan §WP2)
        "fpr_sequential": _num(row.get("fpr_sequential")),
        "peeking_fpr_sequential": _num(row.get("peeking_fpr_sequential")),
        "power_sequential": _num(row.get("power_sequential")),
        "coverage_sequential": _num(row.get("coverage_sequential")),
        "effect_exaggeration_sequential": _num(row.get("effect_exaggeration_sequential")),
        "ci_width": _num(row.get("ci_width")),
        "ci_width_sequential": _num(row.get("ci_width_sequential")),
        "peeking_curve_sequential": _curve(details, "peeking_curve_sequential"),
    }


def _headline(rows: list[dict], lead: dict) -> str:
    """The one-line "nominal α X%, real peeking FPR Y%" story (R10)."""
    alpha = lead.get("alpha")
    single = lead.get("single_look_fpr")
    if single is None:
        single = lead.get("fpr")
    peeking = lead.get("peeking_fpr")
    alpha_txt = "—" if alpha is None else f"{alpha * 100:.1f}%"
    parts = [f"nominal α {alpha_txt}", f"single-look FPR {_pct(single)}"]
    if peeking is not None:
        seg = f"peeking FPR {_pct(peeking)}"
        peeking_seq = lead.get("peeking_fpr_sequential")
        if peeking_seq is not None:
            seg += f" → always-valid {_pct(peeking_seq)}"  # the D8 recovery story
        parts.append(seg)
    over = [r for r in rows if r["over_budget"]]
    if over:
        parts.append(f"{len(over)} method(s) over budget")
    return " · ".join(parts)


def build_calibration_block(aa_rows: list[dict], *, report_link: str | None = None) -> dict | None:
    """Map ``_ab_aa_runs`` rows → the ``calibration`` payload block, or ``None``.

    ``aa_rows`` are persisted rows (``get_aa_runs`` order — newest ``created_at``
    first); only the latest invocation's rows are surfaced. Returns ``None`` when
    there are no rows at all (the M3 empty state — the chip reads "uncalibrated").
    """
    if not aa_rows:
        return None

    # newest invocation: the run_stamp of the newest-created row (get_aa_runs is
    # already ordered created_at DESC, but don't rely on it — pick explicitly).
    def _created(row: dict) -> tuple[bool, Any]:
        c = row.get("created_at")
        return (c is not None, c)

    newest = max(aa_rows, key=_created)
    stamp = _run_stamp(newest)
    invocation = [row for row in aa_rows if _run_stamp(row) == stamp]

    matrix_rows = [_matrix_row(row) for row in invocation]
    # sort for a stable, readable matrix: by metric, recommended first, then FPR
    matrix_rows.sort(
        key=lambda r: (
            str(r["metric"]),
            not r["recommended"],
            r["fpr"] if r["fpr"] is not None else 1.0,
        )
    )
    # the lead row anchors the headline + chip: the recommended row if any,
    # else the first successful row with a measured FPR, else the first row.
    lead = next(
        (r for r in matrix_rows if r["recommended"]),
        next((r for r in matrix_rows if r["fpr"] is not None), matrix_rows[0]),
    )
    return {
        "fpr": lead["fpr"],
        "peeking_fpr": lead["peeking_fpr"],
        "alpha": lead["alpha"],
        "budget": lead["budget"],
        "headline": _headline(matrix_rows, lead),
        "matrix_rows": matrix_rows,
        "report_link": report_link,
    }
