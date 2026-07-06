"""Sequential analysis: always-valid confidence sequences (M5, opt-in).

Pure, primitive-only (docs/specs/m5-implementation-plan.md D5 — no config/DB/Jinja/
click type crosses the ``abkit.stats`` boundary). See:

- :mod:`abkit.stats.sequential.confidence_sequence` — the estimator (an asymptotic
  Gaussian confidence sequence) + the CI-inversion SE recovery.
- :mod:`abkit.stats.sequential.mixture` — the fixed-by-policy mixture variance
  ``tau^2`` (the single source, shared by the pipeline and the A/A column).
"""

from __future__ import annotations

from abkit.stats.sequential.apply import to_always_valid
from abkit.stats.sequential.confidence_sequence import se_from_ci_length, sequentialize
from abkit.stats.sequential.mixture import mixture_tau2

__all__ = ["mixture_tau2", "se_from_ci_length", "sequentialize", "to_always_valid"]
