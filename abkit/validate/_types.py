"""Small shared types for the validate engine (ported from the donor autotune).

Dependency-free (no imports from other validate modules) so every stage can
import these without cycles — the donor ``detectkit/autotune/_types.py`` +
``_base.py::AutoTuneError`` convention, reshaped experiment-primary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ValidateError(RuntimeError):
    """Raised when a cell cannot be scored (no data, degenerate population, …).

    The A/A analog of the donor's ``AutoTuneError`` — the command records it on
    the lock row and writes a ``status='failed'`` audit row (WP3/WP4).
    """


@dataclass
class DecisionEntry:
    """One ordered, human-readable rationale entry for the decision log.

    Ported verbatim from ``detectkit/autotune/_types.py`` — the runner walks
    these into the ``details`` JSON and the ``#``-comment report header (WP3/WP5).
    """

    stage: str
    message: str
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "message": self.message, "fields": self.fields}
