"""Exceptions and warnings raised by the pure statistical core."""

from __future__ import annotations


class StatsError(Exception):
    """Base class for every error raised by ``abkit.stats``."""


class SampleValidationError(StatsError):
    """The provided samples/statistics cannot be analysed by this method."""


class MethodParamError(StatsError):
    """A method parameter is unknown, has the wrong type, or an invalid value."""


class UnknownMethodError(StatsError):
    """The requested method name is not in the registry."""


class QuarantinedMethodError(StatsError):
    """The requested method (or branch) is quarantined as broken.

    See docs/specs/statistics-changes.md §3 — these legacy methods are known-broken
    or mislabeled and are never silently substituted.
    """


class AbkitStatsWarning(UserWarning):
    """Warning category for statistical diagnostics (also recorded on TestResult)."""
