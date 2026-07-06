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
    # M5 D8 always-valid sequential column (None when the method is sequential-ineligible
    # or τ² could not be anchored). ``tau2`` is the frozen mixture variance (provenance).
    tau2: float | None = None
    fpr_sequential: float | None = None
    peeking_fpr_sequential: float | None = None
    power_sequential: float | None = None
    coverage_sequential: float | None = None
    effect_exaggeration_sequential: float | None = None
    ci_width: float | None = None
    ci_width_sequential: float | None = None


@dataclass(frozen=True)
class FamilyResult:
    """The composed multi-metric FWER/FDR family sweep (D9/WP8), persisted as a sentinel
    ``_ab_aa_runs`` row (``metric='__family__'``) whose ``details`` carry these numbers."""

    correction: str
    n_metrics: int
    n_null_metrics: int
    metrics: tuple[str, ...]
    iterations: int
    valid_iterations: int
    fwer: float | None  # empirical family-wise error (any false rejection)
    fdr: float | None  # empirical false-discovery rate (mean FDP)
    any_rejection_rate: float | None
    budget: float | None
    over_budget: bool
    alpha: float  # the family-wide (pre-correction) significance the sweep ran at
    verdict: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AaValidateResult:
    """The whole validate run for one experiment."""

    experiment: str
    run_stamp: str
    cells: tuple[CellResult, ...]
    decision_log: tuple[DecisionEntry, ...] = field(default_factory=tuple)
    #: The composed multi-metric FWER/FDR sweep (D9), or None when there are fewer than
    #: two declared comparisons / a single-metric filter (no family to compose).
    family: FamilyResult | None = None
