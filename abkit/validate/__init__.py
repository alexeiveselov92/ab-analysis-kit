"""The A/A false-positive-matrix engine (``abk validate`` — M4).

This package scores a method configuration by empirical false-positive rate and
power against synthetic ground truth (placebo A/A splits + injected effects),
answering *"is this method actually calibrated on this data, or does it lie about
its α?"* (docs/specs/aa-false-positive-matrix.md; docs/specs/m4-implementation-plan.md).

The engine is pure: it imports ``abkit.stats`` freely but never touches the DB,
the filesystem, Jinja, or click — the donor's "engine never touches I/O; the
caller loads and persists" contract. WP1 ships the numeric heart (placebo split,
effect injection, scoring); the loaders, runner, CLI, and report land in WP2–WP7.
"""

from __future__ import annotations

from abkit.validate._types import DecisionEntry, ValidateError
from abkit.validate.panel import PanelCutoff, PlaceboPanel
from abkit.validate.persistence import aa_run_records
from abkit.validate.report import render_validate_report
from abkit.validate.result import AaValidateResult, CellResult
from abkit.validate.runner import ValidateSettings, enumerate_cells, run_validation
from abkit.validate.scoring import CellScore, score_cell

__all__ = [
    "DecisionEntry",
    "ValidateError",
    "PanelCutoff",
    "PlaceboPanel",
    "CellScore",
    "score_cell",
    "AaValidateResult",
    "CellResult",
    "ValidateSettings",
    "enumerate_cells",
    "run_validation",
    "aa_run_records",
    "render_validate_report",
]
