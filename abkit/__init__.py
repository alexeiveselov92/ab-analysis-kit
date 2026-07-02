"""ab-analysis-kit — A/B experiment analysis as declarative YAML + SQL.

``abkit.stats`` (the pure numpy-first statistical core) is available and
re-exported lazily below; the declarative config, DB layer, pipeline and the
full CLI arrive per ``ROADMAP.md``. ``__version__`` is the single source of
truth for the CLI and the ``abk init-claude`` version stamp.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.0.1.dev0"

#: Symbols re-exported lazily from ``abkit.stats`` (PEP 562) so importing
#: ``abkit`` for ``__version__`` (the CLI path) stays numpy-free and fast.
_STATS_EXPORTS = frozenset(
    {
        "BaseMethod",
        "Fraction",
        "RatioSample",
        "Sample",
        "SufficientStats",
        "TestResult",
        "available_methods",
        "create_method",
    }
)


def __getattr__(name: str) -> Any:
    if name in _STATS_EXPORTS:
        import abkit.stats

        return getattr(abkit.stats, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _STATS_EXPORTS)
