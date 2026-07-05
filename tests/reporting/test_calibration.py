"""``build_calibration_block`` — the ``_ab_aa_runs`` → payload ``calibration`` map (WP5).

Covers the empty state, the single-invocation projection (over-budget colouring, the
subsample note, the peeking curve, the recommended lead + headline), the latest-
invocation selection (a matrix is never a mix of runs), and NaN/NULL scrubbing.
"""

from __future__ import annotations

from datetime import datetime

from abkit.reporting.calibration import build_calibration_block
from abkit.utils.json_utils import json_dumps_sorted


def aa_row(
    *,
    run_stamp: str = "stamp0",
    cell: str = "cellA",
    metric: str = "arpu",
    method: str = "t-test",
    mcid: str = "mc-1",
    fpr: float | None = 0.051,
    peeking_fpr: float | None = 0.14,
    power: float | None = None,
    coverage: float | None = 0.95,
    achieved_mde: float | None = None,
    exaggeration: float | None = None,
    alpha: float = 0.05,
    budget: float = 0.075,
    recommended: bool = False,
    rationale: str | None = None,
    single_look_fpr: float | None = 0.051,
    curve: list | None = None,
    kept: int = 40,
    total: int = 40,
    status: str = "success",
    created_at: datetime = datetime(2026, 7, 5, 12, 0, 0),
) -> dict:
    details = {
        "single_look_fpr": single_look_fpr,
        "peeking_fpr": peeking_fpr,
        "peeking_curve": curve if curve is not None else [[1.0, 0.05], [7.0, peeking_fpr or 0.0]],
        "budget": budget,
        "recommended": recommended,
        "kept_grid_points": kept,
        "total_grid_points": total,
    }
    if rationale is not None:
        details["recommended_rationale"] = rationale
    return {
        "run_id": f"{run_stamp}:{cell}",
        "experiment": "exp",
        "metric": metric,
        "method_name": method,
        "method_config_id": mcid,
        "mode": "fpr",
        "iterations": 2000,
        "alpha": alpha,
        "injected_effect": None,
        "fpr": fpr,
        "peeking_fpr": peeking_fpr,
        "power": power,
        "achieved_mde": achieved_mde,
        "coverage": coverage,
        "effect_exaggeration": exaggeration,
        "verdict": f"{method} on {metric}: verdict",
        "budget": budget,
        "details": json_dumps_sorted(details),
        "status": status,
        "error_message": "",
        "created_at": created_at,
    }


def test_empty_rows_return_none():
    assert build_calibration_block([]) is None


def test_single_row_projection():
    block = build_calibration_block([aa_row(recommended=True, rationale="max power in budget")])
    assert block is not None
    assert len(block["matrix_rows"]) == 1
    row = block["matrix_rows"][0]
    assert row["metric"] == "arpu"
    assert row["method"] == "t-test"
    assert row["fpr"] == 0.051
    assert row["over_budget"] is False  # 0.051 < 0.075
    assert row["recommended"] is True
    assert row["rationale"] == "max power in budget"
    assert row["peeking_curve"] == [[1.0, 0.05], [7.0, 0.14]]
    # the lead row drives the top-level fields + headline
    assert block["fpr"] == 0.051
    assert block["peeking_fpr"] == 0.14
    assert "nominal α 5.0%" in block["headline"]
    assert "peeking FPR 14.0%" in block["headline"]


def test_over_budget_flagged():
    block = build_calibration_block([aa_row(fpr=0.11, budget=0.075)])
    row = block["matrix_rows"][0]
    assert row["over_budget"] is True
    assert "1 method(s) over budget" in block["headline"]


def test_subsample_note_only_when_downsampled():
    full = build_calibration_block([aa_row(kept=40, total=40)])
    assert full["matrix_rows"][0]["note"] is None
    sub = build_calibration_block([aa_row(kept=5, total=40)])
    assert sub["matrix_rows"][0]["note"] == "5/40 looks scored (denser-early subsample)"


def test_latest_invocation_only():
    """Two validate runs → only the newest run_stamp's rows appear (never a mix)."""
    old = aa_row(
        run_stamp="old", metric="arpu", fpr=0.20, created_at=datetime(2026, 7, 1)
    )
    new = aa_row(
        run_stamp="new", metric="arpu", fpr=0.05, created_at=datetime(2026, 7, 5)
    )
    block = build_calibration_block([new, old])  # get_aa_runs order: newest first
    assert len(block["matrix_rows"]) == 1
    assert block["matrix_rows"][0]["fpr"] == 0.05  # the new run, not the old 0.20


def test_recommended_row_sorts_first_and_leads():
    rows = [
        aa_row(cell="a", method="naive", fpr=0.12, recommended=False),
        aa_row(cell="b", method="cuped", fpr=0.049, recommended=True, rationale="why"),
    ]
    block = build_calibration_block(rows)
    # recommended sorts first within the metric
    assert block["matrix_rows"][0]["method"] == "cuped"
    # the lead (recommended) anchors the top-level fpr
    assert block["fpr"] == 0.049


def test_failed_row_kept_but_never_leads():
    rows = [
        aa_row(cell="f", method="broken", fpr=None, status="failed"),
        aa_row(cell="g", method="t-test", fpr=0.05, status="success"),
    ]
    block = build_calibration_block(rows)
    methods = {r["method"] for r in block["matrix_rows"]}
    assert methods == {"broken", "t-test"}
    # the lead is the successful FPR-bearing row, not the failed one
    assert block["fpr"] == 0.05


def test_nan_and_null_scrubbed():
    row = aa_row(fpr=float("nan"), power=float("inf"), coverage=None)
    block = build_calibration_block([row])
    cell = block["matrix_rows"][0]
    assert cell["fpr"] is None
    assert cell["power"] is None
    assert cell["coverage"] is None
