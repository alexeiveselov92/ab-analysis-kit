"""The per-comparison output record (legacy ``TestResult`` parity, baseline §1)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar


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

    # The class name matches pytest's Test* collection pattern; it is a result
    # record, not a test case.
    __test__: ClassVar[bool] = False

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
    # "fixed" (default, legacy parity) | "always_valid" (the M5 sequential mode —
    # an always-valid confidence sequence; docs/specs/m5-implementation-plan.md D7).
    # A row FIELD, never part of method_config_id (sequential is a mode, not identity).
    ci_kind: str = "fixed"
    cov_value_1: float | None = None
    cov_value_2: float | None = None
    # CUPED covariate moments (M9 WP1): populated by cuped-t-test only, so a
    # persisted row carries the full per-arm covariate SufficientStats
    # (cov_m2 = cov_std²·n, cross_c = corr_coef·√(m2·cov_m2)). corr_coef may be
    # NaN (zero pooled covariate variance) — serialisation NaN→None-cleans it.
    cov_std_1: float | None = None
    cov_std_2: float | None = None
    corr_coef_1: float | None = None
    corr_coef_2: float | None = None
    mde_1: float | None = None
    mde_2: float | None = None
    effect_distribution: Any | None = field(default=None, repr=False, compare=False)
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe flat dict (drops ``effect_distribution``; NaN/inf → None).

        Derived from ``dataclasses.fields`` so a future field cannot be silently
        forgotten in serialisation (review finding).
        """

        def _clean(value: Any) -> Any:
            if isinstance(value, float) and not math.isfinite(value):
                return None
            return value

        result: dict[str, Any] = {}
        for spec in fields(self):
            if spec.name == "effect_distribution":
                continue
            value = getattr(self, spec.name)
            if spec.name == "reject":
                result[spec.name] = bool(value)
            elif spec.name == "warnings":
                result[spec.name] = list(value)
            elif isinstance(value, dict) and spec.name == "diagnostics":
                result[spec.name] = {key: _clean(entry) for key, entry in value.items()}
            else:
                result[spec.name] = _clean(value)
        return result
