"""Serialize an :class:`AaValidateResult` into ``_ab_aa_runs`` records (m4 WP3).

Each :class:`CellResult` becomes one ``save_aa_run`` record with a per-cell-unique
``run_id`` (D4) — every ``AA_RUN_COLUMNS`` key present (``save_aa_run`` stamps
``created_at`` itself). The command (WP4) calls ``save_aa_run`` for each record under
the validate lock.
"""

from __future__ import annotations

from typing import Any

from abkit.utils.json_utils import json_dumps_sorted
from abkit.validate.result import AaValidateResult, FamilyResult
from abkit.validate.run_id import cell_hash, make_run_id

#: The composed family sweep (D9) is persisted as one sentinel row per run whose
#: ``details.family`` carries the FWER/FDR — a reserved (metric, method_config_id) that
#: cannot collide with a real cell, so ``find_calibration``/the chip never match it and
#: the report extracts it into its own block (calibration.py).
FAMILY_METRIC = "__family__"
FAMILY_METHOD_CONFIG_ID = "__composed__"


def _family_record(experiment: str, run_stamp: str, family: FamilyResult) -> dict[str, Any]:
    """The composed-family sentinel ``_ab_aa_runs`` row — numerics NULL, family in details."""
    details = {
        "family": {
            "correction": family.correction,
            "n_metrics": family.n_metrics,
            "n_null_metrics": family.n_null_metrics,
            "metrics": list(family.metrics),
            "iterations": family.iterations,
            "valid_iterations": family.valid_iterations,
            "fwer": family.fwer,
            "fdr": family.fdr,
            "any_rejection_rate": family.any_rejection_rate,
            # WP-B: the composed peeking pair (fixed hazard → always-valid twin), or None
            # on a sequential-ineligible family — the report's recovery story.
            "fwer_peeking": family.fwer_peeking,
            "fdr_peeking": family.fdr_peeking,
            "any_rejection_rate_peeking": family.any_rejection_rate_peeking,
            "fwer_sequential": family.fwer_sequential,
            "fdr_sequential": family.fdr_sequential,
            "any_rejection_rate_sequential": family.any_rejection_rate_sequential,
            "budget": family.budget,
            "over_budget": family.over_budget,
            "warnings": list(family.warnings),
        }
    }
    record: dict[str, Any] = {
        "experiment": experiment,
        "run_id": make_run_id(
            run_stamp, cell_hash(FAMILY_METRIC, FAMILY_METHOD_CONFIG_ID, "family", family.alpha)
        ),
        "metric": FAMILY_METRIC,
        "method_name": "composed-fdr",
        "method_params": "{}",
        "method_config_id": FAMILY_METHOD_CONFIG_ID,
        "mode": "family",
        "iterations": family.iterations,
        "alpha": family.alpha,
        "injected_effect": None,
        "verdict": family.verdict,
        "details": json_dumps_sorted(details),
        "status": "success",
        "error_message": "",
    }
    # every other numeric/sequential column is NULL on the sentinel (it is not a cell)
    for col in (
        "fpr",
        "peeking_fpr",
        "power",
        "achieved_mde",
        "coverage",
        "effect_exaggeration",
        "tau2",
        "fpr_sequential",
        "peeking_fpr_sequential",
        "power_sequential",
        "coverage_sequential",
        "effect_exaggeration_sequential",
        "ci_width",
        "ci_width_sequential",
    ):
        record[col] = None
    return record


def aa_run_records(result: AaValidateResult) -> list[dict[str, Any]]:
    """Every scored cell as a ``save_aa_run``-ready record (``created_at`` excluded).

    Appends one composed-family sentinel row (D9/WP8) when the run scored a family.
    """
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
                "tau2": cell.tau2,
                "fpr_sequential": cell.fpr_sequential,
                "peeking_fpr_sequential": cell.peeking_fpr_sequential,
                "power_sequential": cell.power_sequential,
                "coverage_sequential": cell.coverage_sequential,
                "effect_exaggeration_sequential": cell.effect_exaggeration_sequential,
                "ci_width": cell.ci_width,
                "ci_width_sequential": cell.ci_width_sequential,
                "verdict": cell.verdict,
                "details": json_dumps_sorted(details),
                "status": cell.status,
                "error_message": cell.error_message,
            }
        )
    if result.family is not None:
        records.append(_family_record(result.experiment, result.run_stamp, result.family))
    return records
