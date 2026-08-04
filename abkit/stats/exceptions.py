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


class AsymmetricCIError(StatsError):
    """SE recovery was attempted on a method whose fixed CI is not symmetric.

    ``sequential.se_from_ci_length`` infers ``SE = ci_length / (2z)``, which is the
    SE only for an ``effect ± z·SE`` interval. For a score/Fieller-type interval it
    returns a finite number that is NOT the SE, and the always-valid transform then
    centres a symmetric sequence on it — no NaN, no exception, silently wrong
    (docs/specs/m13-implementation-plan.md §6a). A method that declares
    :attr:`~abkit.stats.base.BaseMethod.asymmetric_ci` turns that into this refusal.
    """


class AbkitStatsWarning(UserWarning):
    """Warning category for statistical diagnostics (also recorded on TestResult)."""
