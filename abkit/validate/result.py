"""The validate result model — per-cell rows + the recommendation (m4 WP3).

Flattens the donor ``AutoTuneResult`` to the as-built ``_ab_aa_runs`` shape
(docs/specs/aa-false-positive-matrix.md §7 as amended, tables.py:224–263): one
:class:`CellResult` per scored (metric × method × alpha) cell, plus the recommended
method per metric.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from abkit.validate._types import DecisionEntry


@dataclass(frozen=True)
class CellResult:
    """One scored matrix cell — maps 1:1 onto an ``_ab_aa_runs`` row."""

    metric: str
    method_name: str
    method_params: str  # canonical JSON
    method_config_id: str
    mode: str  # fpr | power | mde (the selection objective)
    alpha: float  # effective post-correction per-comparison alpha
    iterations: int
    injected_effect: float | None
    fpr: float | None
    peeking_fpr: float | None
    power: float | None
    achieved_mde: float | None
    coverage: float | None
    effect_exaggeration: float | None
    verdict: str  # plain-language per-method verdict
    budget: float | None  # the aa_fpr_budget the verdict judged against
    recommended: bool
    details: dict[str, Any]  # peeking curve, subsample note, selection rationale, warnings
    status: str = "success"  # success | failed
    error_message: str = ""


@dataclass(frozen=True)
class AaValidateResult:
    """The whole validate run for one experiment."""

    experiment: str
    run_stamp: str
    cells: tuple[CellResult, ...]
    decision_log: tuple[DecisionEntry, ...] = field(default_factory=tuple)
