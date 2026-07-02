"""The per-comparison output record (legacy ``TestResult`` parity, baseline §1)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TestResult:
    """One pairwise variant comparison — maps 1:1 onto the ``_ab_results`` row.

    Field semantics follow docs/specs/statistics-baseline.md §1 exactly:
    ``name_1``/``*_1`` is the control arm (first variant), ``*_2`` the treatment.
    ``effect`` is always the point estimate on the REAL data (hygiene fix H9);
    bootstrap methods report ``boot_mean`` in :attr:`diagnostics` as a bias
    diagnostic instead of overloading ``effect``.

    ``method_params`` is the identity-bearing, non-default params dict — the same
    object that is canonically serialised and hashed into ``method_config_id``
    (docs/specs/declarative-config.md §7), so BI series filters and identity can
    never disagree.
    """

    name_1: str | None
    name_2: str | None
    value_1: float
    value_2: float
    std_1: float
    std_2: float
    size_1: int
    size_2: int
    method_name: str
    method_params: dict[str, Any]
    alpha: float
    pvalue: float
    effect: float
    ci_length: float
    left_bound: float
    right_bound: float
    reject: bool
    cov_value_1: float | None = None
    cov_value_2: float | None = None
    mde_1: float | None = None
    mde_2: float | None = None
    effect_distribution: Any | None = field(default=None, repr=False, compare=False)
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe flat dict (drops ``effect_distribution``; NaN/inf → None)."""

        def _clean(value: Any) -> Any:
            if isinstance(value, float) and not math.isfinite(value):
                return None
            return value

        return {
            "name_1": self.name_1,
            "name_2": self.name_2,
            "value_1": _clean(self.value_1),
            "value_2": _clean(self.value_2),
            "std_1": _clean(self.std_1),
            "std_2": _clean(self.std_2),
            "cov_value_1": _clean(self.cov_value_1),
            "cov_value_2": _clean(self.cov_value_2),
            "size_1": self.size_1,
            "size_2": self.size_2,
            "mde_1": _clean(self.mde_1),
            "mde_2": _clean(self.mde_2),
            "method_name": self.method_name,
            "method_params": self.method_params,
            "alpha": self.alpha,
            "pvalue": _clean(self.pvalue),
            "effect": _clean(self.effect),
            "ci_length": _clean(self.ci_length),
            "left_bound": _clean(self.left_bound),
            "right_bound": _clean(self.right_bound),
            "reject": bool(self.reject),
            "warnings": list(self.warnings),
            "diagnostics": {key: _clean(value) for key, value in self.diagnostics.items()},
        }
