"""Serialize an :class:`AaValidateResult` into ``_ab_aa_runs`` records (m4 WP3).

Each :class:`CellResult` becomes one ``save_aa_run`` record with a per-cell-unique
``run_id`` (D4) — every ``AA_RUN_COLUMNS`` key present (``save_aa_run`` stamps
``created_at`` itself). The command (WP4) calls ``save_aa_run`` for each record under
the validate lock.
"""

from __future__ import annotations

from typing import Any

from abkit.utils.json_utils import json_dumps_sorted
from abkit.validate.result import AaValidateResult
from abkit.validate.run_id import cell_hash, make_run_id


def aa_run_records(result: AaValidateResult) -> list[dict[str, Any]]:
    """Every scored cell as a ``save_aa_run``-ready record (``created_at`` excluded)."""
    records: list[dict[str, Any]] = []
    for cell in result.cells:
        run_id = make_run_id(
            result.run_stamp,
            cell_hash(cell.metric, cell.method_config_id, cell.mode, cell.alpha),
        )
        details = {**cell.details, "recommended": cell.recommended, "budget": cell.budget}
        records.append(
            {
                "experiment": result.experiment,
                "run_id": run_id,
                "metric": cell.metric,
                "method_name": cell.method_name,
                "method_params": cell.method_params,
                "method_config_id": cell.method_config_id,
                "mode": cell.mode,
                "iterations": cell.iterations,
                "alpha": cell.alpha,
                "injected_effect": cell.injected_effect,
                "fpr": cell.fpr,
                "peeking_fpr": cell.peeking_fpr,
                "power": cell.power,
                "achieved_mde": cell.achieved_mde,
                "coverage": cell.coverage,
                "effect_exaggeration": cell.effect_exaggeration,
                "verdict": cell.verdict,
                "details": json_dumps_sorted(details),
                "status": cell.status,
                "error_message": cell.error_message,
            }
        )
    return records
