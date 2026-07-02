"""``abkit.stats`` — the pure, importable, numpy-first statistical core.

Invariant (CLAUDE.md): this package depends only on numpy/scipy/statsmodels (plus
the stdlib) — never on config/DB/Jinja/click. It serves the pipeline, the explore
cockpit, the A/A harness and notebook users from one math implementation.

>>> from abkit.stats import Sample, create_method
>>> method = create_method("t-test", alpha=0.05, params={"test_type": "relative"})
>>> result = method.compare_pair(Sample([...], name="control"), Sample([...], name="treatment"))
"""

from abkit.stats import bootstrap, parametric  # noqa: F401  (register all methods)
from abkit.stats.accumulate import merge_joint_moments, merge_ratio_suffstats, merge_suffstats
from abkit.stats.base import (
    BaseMethod,
    ParamSpec,
    compute_method_config_id,
    method_config_payload,
)
from abkit.stats.correction import (
    TwoTierAlphas,
    adjust_alpha,
    benjamini_hochberg,
    n_comparisons,
    two_tier_alphas,
)
from abkit.stats.exceptions import (
    AbkitStatsWarning,
    MethodParamError,
    QuarantinedMethodError,
    SampleValidationError,
    StatsError,
    UnknownMethodError,
)
from abkit.stats.factory import create_method
from abkit.stats.registry import available_methods, get_method_class, register
from abkit.stats.result import TestResult
from abkit.stats.rng import derive_seed, make_rng
from abkit.stats.samples import (
    Fraction,
    JointMoments,
    PairedSufficientStats,
    RatioSample,
    RatioSufficientStats,
    Sample,
    SufficientStats,
    align_paired,
)
from abkit.stats.srm import DEFAULT_SRM_ALPHA, SrmResult, srm_check

__all__ = [
    "AbkitStatsWarning",
    "BaseMethod",
    "DEFAULT_SRM_ALPHA",
    "Fraction",
    "JointMoments",
    "MethodParamError",
    "PairedSufficientStats",
    "ParamSpec",
    "QuarantinedMethodError",
    "RatioSample",
    "RatioSufficientStats",
    "Sample",
    "SampleValidationError",
    "SrmResult",
    "StatsError",
    "SufficientStats",
    "TestResult",
    "TwoTierAlphas",
    "UnknownMethodError",
    "adjust_alpha",
    "align_paired",
    "available_methods",
    "benjamini_hochberg",
    "compute_method_config_id",
    "create_method",
    "derive_seed",
    "get_method_class",
    "make_rng",
    "merge_joint_moments",
    "merge_ratio_suffstats",
    "merge_suffstats",
    "method_config_payload",
    "n_comparisons",
    "register",
    "srm_check",
    "two_tier_alphas",
]
