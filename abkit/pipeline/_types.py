"""Shared pipeline types: steps, statuses, outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PipelineStep(str, Enum):
    """The selectable ``--steps`` stages (architecture.md §5)."""

    VALIDATE = "validate"
    PLAN = "plan"
    LOAD = "load"
    COMPUTE = "compute"

    @classmethod
    def parse(cls, steps: str) -> list[PipelineStep]:
        """Parse a ``--steps`` string; unknown names raise with the valid list."""
        parsed = []
        for raw in steps.split(","):
            raw = raw.strip().lower()
            if not raw:
                continue
            try:
                parsed.append(cls(raw))
            except ValueError:
                valid = ", ".join(step.value for step in cls)
                raise ValueError(f"unknown step '{raw}' (valid: {valid})") from None
        if not parsed:
            raise ValueError("no steps selected")
        return parsed


#: task-status values in ``_ab_tasks``
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass
class RunOutcome:
    """One experiment's run summary (the driver's return value)."""

    experiment: str
    status: str = "completed"  # completed | failed | locked | skipped
    error: str | None = None
    exposures_loaded: int = 0
    srm_flagged: bool = False
    cutoffs_planned: int = 0
    results_written: int = 0
    warnings: list[str] = field(default_factory=list)
