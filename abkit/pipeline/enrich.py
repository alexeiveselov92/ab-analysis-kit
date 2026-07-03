"""The enrich stage: PairOutcomes → the full ``_ab_results`` contract rows.

Everything ``TestResult`` lacks is bridged here (the stats-surface §4 map):
identity (``method_config_id`` off the bound instance; ``method_params`` via
the ONE canonical serialisation), the window columns, integrity flags
(SRM broadcast, ``decision_blocked``, ``insufficient_data``), sequence
columns, the R7 ``warnings``/``diagnostics`` JSON payloads, and provenance.
``created_at`` is stamped by ``save_results`` (the strictly-monotonic source).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.core.period_planner import Cutoff, Grid
from abkit.database.internal_tables._results import RESULT_COLUMNS
from abkit.pipeline.analyze import PairOutcome
from abkit.stats import SrmResult
from abkit.utils.json_utils import json_dumps_sorted

DAY_SECONDS = 86400.0
_ONE_US = timedelta(microseconds=1)


def _clean(value: float | None) -> float | None:
    """NaN/inf → None (nullable contract columns)."""
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def rows_for_cutoff(
    experiment: ExperimentConfig,
    comparison: ComparisonConfig,
    metric: MetricConfig,
    outcomes: list[PairOutcome],
    cutoff: Cutoff,
    grid: Grid,
    effective_alpha: float,
    srm: SrmResult,
    watermark_ts: datetime,
    metric_query: str,
    metric_rendered_query: str,
) -> dict[str, np.ndarray]:
    """Flatten one (comparison, cutoff)'s outcomes into contract-column arrays."""
    rows: list[dict[str, Any]] = []
    window_seconds = int((cutoff.end_ts - grid.start_ts).total_seconds())
    method_config_id = comparison.method.method_config_id
    method_params_json = comparison.method.canonical_params_json
    # Derived dates are EXPERIMENT-timezone dates (§6.3 'legacy-identical at
    # daily cadence' — a Moscow daily experiment's end_date must be the Moscow
    # date, not the UTC date of the naive timestamp).
    zone = ZoneInfo(experiment.timezone)
    utc = ZoneInfo("UTC")
    start_date_local = grid.start_ts.replace(tzinfo=utc).astimezone(zone).date()
    end_date_local = (cutoff.end_ts - _ONE_US).replace(tzinfo=utc).astimezone(zone).date()

    for outcome in outcomes:
        result = outcome.result
        demoted = result is None
        row: dict[str, Any] = {
            # identity
            "experiment": experiment.name,
            "metric": metric.name,
            "is_main_metric": comparison.is_main_metric,
            "is_guardrail": comparison.is_guardrail,
            "method_name": comparison.method.name,
            "method_params": method_params_json,
            "method_config_id": method_config_id,
            "name_1": outcome.name_1,
            "name_2": outcome.name_2,
            # window (§6.3: end_ts exclusive; dates derived; fractional x-axis)
            "start_ts": grid.start_ts,
            "end_ts": cutoff.end_ts,
            "start_date": start_date_local,
            "end_date": end_date_local,
            "window_seconds": window_seconds,
            "elapsed_days": window_seconds / DAY_SECONDS,
            # per-arm
            "value_1": _clean(result.value_1) if result else None,
            "value_2": _clean(result.value_2) if result else None,
            "std_1": _clean(result.std_1) if result else None,
            "std_2": _clean(result.std_2) if result else None,
            "cov_value_1": _clean(result.cov_value_1) if result else None,
            "cov_value_2": _clean(result.cov_value_2) if result else None,
            "size_1": outcome.size_1,
            "size_2": outcome.size_2,
            # test (withheld under demotion)
            "alpha": effective_alpha,
            "pvalue": _clean(result.pvalue) if result else None,
            "effect": _clean(result.effect) if result else None,
            "left_bound": _clean(result.left_bound) if result else None,
            "right_bound": _clean(result.right_bound) if result else None,
            "ci_length": _clean(result.ci_length) if result else None,
            "reject": bool(result.reject) if result else None,
            "mde_1": _clean(result.mde_1) if result else None,
            "mde_2": _clean(result.mde_2) if result else None,
            # integrity: SRM broadcast; a failed gate blocks the decision but
            # NEVER drops the row (architecture §5.4 — loud, not silent)
            "srm_flag": srm.srm_flag,
            "srm_pvalue": _clean(srm.pvalue),
            "decision_blocked": srm.srm_flag,
            "insufficient_data": demoted,
            # sequence: M2 computes fixed-horizon CIs only (sequential lands M5)
            "ci_kind": "fixed",
            "is_horizon": cutoff.is_horizon,
            # diagnostics (plan R7 — the human-readable failure signal)
            "warnings": (
                json_dumps_sorted(list(outcome.warnings) + (result.warnings if result else []))
                if (outcome.warnings or (result and result.warnings))
                else None
            ),
            "diagnostics": (
                json_dumps_sorted({k: _clean(v) for k, v in result.diagnostics.items()})
                if result and result.diagnostics
                else None
            ),
            # provenance
            "metric_query": metric_query,
            "metric_rendered_query": metric_rendered_query,
            "watermark_ts": watermark_ts,
        }
        rows.append(row)

    return {
        column: np.array([row[column] for row in rows], dtype=object) for column in RESULT_COLUMNS
    }
