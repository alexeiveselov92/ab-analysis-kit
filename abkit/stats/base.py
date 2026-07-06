"""``BaseMethod`` — the plugin contract every statistical method implements.

Methods are plugins (CLAUDE.md invariant): a new estimator is one subclass plus a
registry entry; the pipeline/DB/CLI never special-case a method name. Each method
declares its parameter schema (:class:`ParamSpec`), and its identity is the
canonical ``method_config_id`` hash (docs/specs/declarative-config.md §7):

    method_config_id = sha256( method_name                    # registry name
                             + json_dumps_sorted(params)      # non-default identity params
                             + ALGORITHM_VERSION )             # appended only when > 1

``seed`` (and ``alpha``, which is experiment-level in the declarative model) are
identity-EXCLUDED so re-keying never orphans a published cumulative series; re-run
byte-stability comes from the deterministic per-row seed (``rng.derive_seed``).
"""

from __future__ import annotations

import hashlib
import itertools
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np

from abkit.stats.exceptions import MethodParamError, SampleValidationError
from abkit.stats.result import TestResult
from abkit.stats.samples import RatioSample, Sample
from abkit.utils.json_utils import json_dumps_sorted


@dataclass(frozen=True)
class ParamSpec:
    """Schema entry for one method parameter.

    ``identity=False`` marks the parameter as excluded from ``method_config_id``
    (it never changes the statistical series identity — e.g. ``seed``).
    Numeric params are validated for finiteness always (a NaN/inf param value can
    never be a valid identity — ``json_dumps_sorted`` forbids it) and against the
    optional ``minimum``/``maximum`` bounds — validation fails at construction
    with :class:`MethodParamError`, never downstream in scipy/statsmodels.
    """

    name: str
    types: tuple[type, ...]
    default: Any
    identity: bool = True
    choices: tuple[Any, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    exclusive_bounds: bool = False
    description: str = ""

    def validate(self, value: Any, method_name: str) -> Any:
        # Normalise numpy scalars (e.g. an np.int64 seed from the pipeline) to
        # plain Python types before type-checking.
        if isinstance(value, np.bool_):
            value = bool(value)
        elif isinstance(value, np.integer):
            value = int(value)
        elif isinstance(value, np.floating):
            value = float(value)
        if bool not in self.types and isinstance(value, bool):
            raise MethodParamError(
                f"{method_name}: param {self.name!r} must be {self._type_names()}, got bool"
            )
        if not isinstance(value, self.types):
            if float in self.types and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            else:
                raise MethodParamError(
                    f"{method_name}: param {self.name!r} must be {self._type_names()}, "
                    f"got {type(value).__name__}"
                )
        if self.choices is not None and value not in self.choices:
            raise MethodParamError(
                f"{method_name}: param {self.name!r} must be one of {list(self.choices)}, got {value!r}"
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise MethodParamError(
                f"{method_name}: param {self.name!r} must be finite, got {value!r}"
            )
        if isinstance(value, (int, float)):
            below = self.minimum is not None and (
                value < self.minimum or (self.exclusive_bounds and value == self.minimum)
            )
            above = self.maximum is not None and (
                value > self.maximum or (self.exclusive_bounds and value == self.maximum)
            )
            if below or above:
                left, right = ("(", ")") if self.exclusive_bounds else ("[", "]")
                low = "-inf" if self.minimum is None else self.minimum
                high = "+inf" if self.maximum is None else self.maximum
                raise MethodParamError(
                    f"{method_name}: param {self.name!r} must be within "
                    f"{left}{low}, {high}{right}, got {value!r}"
                )
        return value

    def _type_names(self) -> str:
        return " | ".join(t.__name__ for t in self.types)


# --- shared parameter specs (single source; methods compose their schemas) ------

TEST_TYPE_PARAM = ParamSpec(
    name="test_type",
    types=(str,),
    default="relative",
    choices=("relative", "absolute"),
    description="Effect estimand: relative lift over control (default) or absolute difference.",
)
CALCULATE_MDE_PARAM = ParamSpec(
    name="calculate_mde",
    types=(bool,),
    default=False,
    description="Compute per-arm MDE at the configured power (statsmodels solve).",
)
POWER_PARAM = ParamSpec(
    name="power",
    types=(float,),
    default=0.8,
    minimum=0.0,
    maximum=1.0,
    exclusive_bounds=True,
    description="Target power for the MDE solve.",
)
COVARIATE_LOOKBACK_PARAM = ParamSpec(
    name="covariate_lookback",
    types=(str, int),
    default=None,
    description=(
        "Pre-period covariate window (fixed whole-day lookback, e.g. '14d' — "
        "statistics-changes.md §5). IDENTITY-BEARING: a different lookback is a "
        "different covariate, hence a different series. The math here never "
        "reads it — the pipeline's loader materialises the covariate values; "
        "the duration grammar is validated by the config layer (the pure core "
        "does not parse durations)."
    ),
)
N_SAMPLES_PARAM = ParamSpec(
    name="n_samples",
    types=(int,),
    default=1000,
    minimum=1,
    description="Number of bootstrap resamples.",
)
STRATIFY_PARAM = ParamSpec(
    name="stratify",
    types=(bool,),
    default=False,
    description="Resample within strata (requires categories_array on the samples).",
)
WEIGHT_METHOD_PARAM = ParamSpec(
    name="weight_method",
    types=(str,),
    default="min",
    choices=("min", "mean"),
    description="How per-stratum weights are pooled across variants (baseline §4.2).",
)
STAT_PARAM = ParamSpec(
    name="stat",
    types=(str,),
    default="mean",
    description=(
        "Named statistic bootstrapped per resample — a key of "
        "abkit.stats.bootstrap.applier.STAT_FUNCS ('mean'/'median' built in; extend via "
        "register_stat — names, not callables, so identity stays hashable). "
        "The Poisson engine is mean-only (H7)."
    ),
)
SEED_PARAM = ParamSpec(
    name="seed",
    types=(int,),
    default=None,
    identity=False,
    description=(
        "Bootstrap RNG seed. Identity-excluded for ALL bootstrap methods "
        "(declarative-config.md §7); the pipeline derives it per row via rng.derive_seed."
    ),
)
MAX_BLOCK_BYTES_PARAM = ParamSpec(
    name="max_block_bytes",
    types=(int,),
    default=None,
    identity=False,
    minimum=1,
    description=(
        "Memory cap for the resample matrix; replicates stream in blocks under it "
        "(H10). Never changes results — block boundaries are stream-invariant."
    ),
)


def require_pair_type(method_name: str, group_1: Any, group_2: Any, expected: type) -> None:
    """Raise unless both groups are instances of ``expected`` (clear input errors)."""
    for label, group in (("first", group_1), ("second", group_2)):
        if not isinstance(group, expected):
            raise SampleValidationError(
                f"{method_name}: {label} group must be {expected.__name__}, got {type(group).__name__}"
            )


def method_config_payload(
    method_name: str,
    identity_params: dict[str, Any],
    algorithm_version: int,
) -> bytes:
    """The exact bytes hashed into ``method_config_id`` — pinned by a byte test."""
    payload = method_name + json_dumps_sorted(identity_params)
    if algorithm_version > 1:  # appended only when > 1 (match detectkit)
        payload += str(algorithm_version)
    return payload.encode("utf-8")


def compute_method_config_id(
    method_name: str,
    identity_params: dict[str, Any],
    algorithm_version: int,
) -> str:
    return hashlib.sha256(
        method_config_payload(method_name, identity_params, algorithm_version)
    ).hexdigest()


class BaseMethod(ABC):
    """Abstract statistical method: pairwise comparisons with dual entry.

    Dual entry (architecture pillar 3): ``from_suffstats`` powers the closed-form
    pipeline/explore path, ``from_samples`` the bootstrap & golden-reproduction
    path; for closed-form methods the two are one math path (``from_samples``
    reduces raw arrays to sufficient statistics and delegates).
    """

    #: Registry name (kebab-case, e.g. ``"cuped-t-test"``) — part of the identity hash.
    name: ClassVar[str]
    #: Bumped on any deliberate deviation from the captured baseline — never silently.
    ALGORITHM_VERSION: ClassVar[int] = 1
    #: The method's parameter schema.
    param_specs: ClassVar[tuple[ParamSpec, ...]] = ()
    #: Declarative capability attributes (plan R8) — the pipeline dispatches on
    #: these instead of isinstance checks against concrete classes. Purely
    #: descriptive: no numeric behaviour depends on them (no version bump).
    #: Which container family ``from_samples`` expects: sample | fraction | ratio.
    input_kind: ClassVar[str] = "sample"
    #: Paired designs need unit-aligned arms (not served by the v1 pipeline).
    is_paired: ClassVar[bool] = False
    #: Needs a per-unit ``cov_array`` on both samples (CUPED / post-normed) —
    #: the explore Tier-S gate reads this instead of guessing from param names.
    requires_covariate: ClassVar[bool] = False
    #: Eligible for the M5 always-valid sequential transform. True requires a
    #: symmetric normal fixed CI, whose SE is recoverable by CI-inversion
    #: (``sequential.se_from_ci_length``); the pipeline dispatches on this flag
    #: instead of name-checking. Bootstrap percentile CIs are asymmetric → the
    #: bootstrap base sets this False (docs/specs/m5-implementation-plan.md D1).
    supports_sequential: ClassVar[bool] = True

    def __init__(self, alpha: float = 0.05, **params: Any) -> None:
        if not 0.0 < alpha < 1.0:
            raise MethodParamError(f"{self.name}: alpha must be in (0, 1), got {alpha}")
        self.alpha = float(alpha)

        specs = {spec.name: spec for spec in self.param_specs}
        unknown = set(params) - set(specs)
        if unknown:
            hints = []
            if "seed" in unknown and "seed" not in specs:
                hints.append(
                    "seed is only accepted by bootstrap methods (closed-form methods are "
                    "deterministic; declarative-config.md §7)"
                )
            if "alpha" in unknown:
                hints.append("alpha is a direct argument, not a method param")
            raise MethodParamError(
                f"{self.name}: unknown param(s) {sorted(unknown)}; "
                f"valid params: {sorted(specs)}" + (". " + " ".join(hints) if hints else "")
            )

        self.params: dict[str, Any] = {}
        for name, spec in specs.items():
            if name in params and params[name] is not None:
                self.params[name] = spec.validate(params[name], self.name)
            else:
                self.params[name] = spec.default
        self._validate_params()

    def _validate_params(self) -> None:  # noqa: B027 — optional hook, deliberately non-abstract
        """Hook for cross-parameter validation (override as needed)."""

    # --- identity -----------------------------------------------------------
    @property
    def test_type(self) -> str:
        return str(self.params["test_type"])

    @property
    def identity_params(self) -> dict[str, Any]:
        """Non-default, identity-bearing params — the dict that is hashed & stored."""
        specs = {spec.name: spec for spec in self.param_specs}
        return {
            name: value
            for name, value in self.params.items()
            if specs[name].identity and value != specs[name].default
        }

    @property
    def method_params(self) -> dict[str, Any]:
        """Alias for :attr:`identity_params` — what lands in ``TestResult``/``_ab_results``."""
        return self.identity_params

    @property
    def method_config_id(self) -> str:
        return compute_method_config_id(self.name, self.identity_params, self.ALGORITHM_VERSION)

    # --- comparison ---------------------------------------------------------
    def compare(self, groups: Sequence[Any]) -> list[TestResult]:
        """Run all pairwise variant comparisons (baseline §5: ``combinations(groups, 2)``)."""
        if len(groups) < 2:
            raise SampleValidationError(f"{self.name}: compare requires at least two groups")
        return [
            self.compare_pair(group_1, group_2)
            for group_1, group_2 in itertools.combinations(groups, 2)
        ]

    def compare_pair(self, group_1: Any, group_2: Any) -> TestResult:
        """Compare one (control, treatment) pair, dispatching on the input kind."""
        raw = isinstance(group_1, (Sample, RatioSample)), isinstance(group_2, (Sample, RatioSample))
        if raw[0] != raw[1]:
            raise SampleValidationError(
                f"{self.name}: cannot mix raw samples and sufficient statistics in one pair"
            )
        if raw[0]:
            return self.from_samples(group_1, group_2)
        return self.from_suffstats(group_1, group_2)

    @abstractmethod
    def from_samples(self, sample_1: Any, sample_2: Any) -> TestResult:
        """Compare from per-unit arrays (bootstrap & golden-reproduction entry)."""

    @abstractmethod
    def from_suffstats(self, stats_1: Any, stats_2: Any) -> TestResult:
        """Compare from sufficient statistics (closed-form pipeline/explore entry)."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(alpha={self.alpha}, params={self.params})"
