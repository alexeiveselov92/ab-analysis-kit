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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np

from abkit.stats.effects import BatchEffectResult, FloatArray, NormalTest
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
    #: Values of THIS param that make the method's fixed CI asymmetric
    #: (:attr:`BaseMethod.asymmetric_ci`). Declaring it here rather than in each
    #: method's ``__init__`` is the m13 STAT-1b lesson applied to a capability:
    #: two methods now switch their interval by param, and a hand-written
    #: resolution per method is a knob-dependent fact copied per class.
    asymmetric_values: tuple[Any, ...] = ()
    #: True when this param is consulted only under ``test_type: relative`` — a
    #: non-default value beside ``absolute`` is then refused at construction
    #: rather than silently ignored, because the param is IDENTITY-BEARING and a
    #: no-op that still forks ``method_config_id`` splits a series for nothing.
    relative_only: bool = False

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
RELATIVE_INTERVAL_PARAM = ParamSpec(
    name="interval",
    types=(str,),
    default="delta",
    choices=("delta", "fieller"),
    relative_only=True,
    asymmetric_values=("fieller",),
    description=(
        "Confidence-interval construction for the RELATIVE effect (m13 STAT-4). "
        "'delta' (default, legacy parity) is the Wald interval effect ± z·SE with the "
        "variance evaluated at the estimate — a different test from the absolute "
        "p-value beside it, so the two can disagree about the same hypothesis. "
        "'fieller' inverts the score test at every candidate lift: asymmetric, "
        "coherent with the absolute p-value BY CONSTRUCTION, exact coverage, and "
        "honestly unbounded when the control mean is not distinguishable from zero. "
        "Identity-bearing — opting in starts a new results series."
    ),
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


def suffstats_columns(
    arrays: Mapping[str, FloatArray] | None,
    keys: Sequence[str],
    method_name: str,
    what: str,
) -> tuple[FloatArray, ...]:
    """Fetch + float64-cast the suffstats columns a batch kernel needs (M7 WP2).

    The array mirror of ``require_pair_type``: clear input errors for the
    ``from_suffstats_array`` entry — a missing column or ragged shapes raise
    :class:`SampleValidationError` up front, never a downstream numpy error.
    Column VALUES are trusted raw arrays (the vectorized engine builds them);
    per-row degeneracy is the kernels' NaN business, not validation's.
    """
    if arrays is None:
        raise SampleValidationError(f"{method_name}: {what} is required for from_suffstats_array")
    missing = [key for key in keys if key not in arrays]
    if missing:
        raise SampleValidationError(
            f"{method_name}: {what} is missing suffstats column(s) {missing}; "
            f"required: {list(keys)}"
        )
    columns = tuple(np.asarray(arrays[key], dtype=np.float64) for key in keys)
    for key, column in zip(keys, columns, strict=True):
        if column.ndim != 1:
            raise SampleValidationError(
                f"{method_name}: {what} suffstats column {key!r} must be a 1-D array "
                f"(one row per comparison), got ndim={column.ndim} — wrap scalars in a "
                "length-1 array (adversarial review round 2: 0-d inputs crash or "
                "silently malform the batch result)"
            )
    shapes = {column.shape for column in columns}
    if len(shapes) > 1:
        raise SampleValidationError(
            f"{method_name}: {what} suffstats columns must share one shape, got "
            f"{ {key: np.asarray(arrays[key]).shape for key in keys} }"
        )
    return columns


def suffstats_pair_columns(
    arrays_1: Mapping[str, FloatArray] | None,
    arrays_2: Mapping[str, FloatArray] | None,
    keys: Sequence[str],
    method_name: str,
) -> tuple[tuple[FloatArray, ...], tuple[FloatArray, ...]]:
    """Two-arm :func:`suffstats_columns` with cross-arm row-count validation.

    Mismatched per-arm batches must fail loudly here (adversarial review
    round 1): without this check numpy would either broadcast a length-1 arm
    silently across the other arm's rows or raise a bare shape ``ValueError``
    deep inside a kernel — both worse than a clear input error.
    """
    columns_1 = suffstats_columns(arrays_1, keys, method_name, "arrays_1")
    columns_2 = suffstats_columns(arrays_2, keys, method_name, "arrays_2")
    if columns_1[0].shape != columns_2[0].shape:
        raise SampleValidationError(
            f"{method_name}: arrays_1 and arrays_2 must have the same row count, "
            f"got {columns_1[0].shape} vs {columns_2[0].shape}"
        )
    return columns_1, columns_2


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
    #: The symmetry this asserts is ENFORCED at the inversion since m13 STAT-3a —
    #: see :attr:`asymmetric_ci` below, whose True is this flag's contradiction.
    supports_sequential: ClassVar[bool] = True
    #: Exposes the M7 WP2 array-wise significance kernel ``from_suffstats_array``
    #: (the validate hot path). Opt-in, mirroring the ``supports_sequential``
    #: precedent: the False default keeps every plugin fully functional through
    #: the scalar ``from_suffstats`` fallback — a method without a batch kernel
    #: is never special-cased, only iterated (m7-implementation-plan.md §WP2).
    supports_vectorized: ClassVar[bool] = False
    #: Splits ``from_samples`` into a costly ``_resample`` step and a cheap
    #: alpha-dependent ``_finalize`` step, so a caller may reuse one resample
    #: across several alphas (the explore Tier-S memo, m10 WP5). Opt-in like
    #: :attr:`supports_vectorized`: the False default keeps every method fully
    #: functional through the whole-``compare_pair`` path — a method without the
    #: split is never special-cased, only recomputed.
    supports_resample_memo: ClassVar[bool] = False
    #: The fixed CI is NOT ``effect ± z·SE`` — a score/Fieller-type interval whose
    #: SE cannot be recovered by inverting its width (m13 STAT-3a, plan §6a item 1).
    #: Every SE-by-CI-inversion entry refuses such a method LOUDLY
    #: (:class:`~abkit.stats.exceptions.AsymmetricCIError`) instead of returning the
    #: mean half-width over ``z`` and calling it a standard error.
    #:
    #: Deliberately NOT a ``ClassVar``, unlike its four siblings above: the interval
    #: shape is switchable by a PARAM (m13 STAT-3 ships Miettinen–Nurminen as an
    #: identity-flagged param on ``z-test``, not as a new class), so the honest
    #: granularity is the bound INSTANCE — a subclass sets ``self.asymmetric_ci``
    #: from ``self.params`` after ``super().__init__()``. A class-level read would
    #: answer for the default params of every instance; the guard therefore accepts
    #: an instance only.
    #:
    #: ``supports_sequential = True`` asserts the opposite property (a symmetric
    #: normal CI). Declaring both is a contradiction the transform resolves by
    #: refusing; an asymmetric method that wants to stay usable outside the
    #: sequential mode declares ``supports_sequential = False`` and is simply left
    #: fixed everywhere, with no error.
    asymmetric_ci: bool = False

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
        # Declarative first, per-method second: the shared specs carry their own
        # cross-param rule and their own capability consequence, so a method that
        # merely ADOPTS one cannot forget either (m13 STAT-4).
        self._validate_shared_params(specs)
        self.asymmetric_ci = self.asymmetric_ci or any(
            spec.asymmetric_values and self.params[name] in spec.asymmetric_values
            for name, spec in specs.items()
        )
        self._validate_params()

    def _validate_shared_params(self, specs: Mapping[str, ParamSpec]) -> None:
        """Cross-param rules declared ON the shared specs, enforced in one place.

        Today that is ``relative_only``: an interval construction that exists only
        for a ratio estimand must not be accepted beside ``test_type: absolute``.
        Ignoring it would be worse than it sounds — the param is identity-bearing,
        so two configurations computing byte-identical numbers would carry
        different ``method_config_id``s and split a published series for nothing.
        """
        if self.params.get("test_type") != "absolute":
            return
        for name, spec in specs.items():
            if spec.relative_only and self.params[name] != spec.default:
                raise MethodParamError(
                    f"{self.name}: param {name!r}={self.params[name]!r} is only consulted "
                    "under test_type: relative — with test_type: absolute it would change "
                    f"nothing while still forking the results series; set {name}="
                    f"{spec.default!r} or test_type: relative"
                )

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

    def from_suffstats_array(
        self,
        arrays_1: Mapping[str, FloatArray],
        arrays_2: Mapping[str, FloatArray] | None = None,
    ) -> BatchEffectResult:
        """The array-wise significance kernel — one row per comparison (M7 WP2).

        Optional capability, gated by :attr:`supports_vectorized`: given
        column arrays of per-arm sufficient-statistic components (each
        method's docstring names its keys — the ``SufficientStats``/
        ``Fraction``/``RatioSufficientStats`` field names), return a
        :class:`~abkit.stats.effects.BatchEffectResult` computed via numpy
        broadcasting, row-for-row parity with the scalar ``from_suffstats``
        (pinned by ``tests/stats/test_vectorized_parity.py``). Paired methods
        take ONE joint mapping (``arrays_2`` stays None), mirroring their
        scalar signature. Degenerate rows yield NaN, never an exception
        ("gaps, never zeros"). Default: not implemented — the validate engine
        must fall back to the scalar loop, never fail the method.
        """
        raise NotImplementedError(
            f"{self.name}: no array-wise significance kernel "
            "(supports_vectorized=False); use the scalar from_suffstats path"
        )

    def _resample(self, sample_1: Any, sample_2: Any) -> Any:
        """The alpha-INDEPENDENT half of ``from_samples`` (m10 WP5).

        Optional capability, gated by :attr:`supports_resample_memo` and
        mirroring :meth:`from_suffstats_array`'s shape: draw whatever the
        comparison's expensive step produces and return it, so a caller may run
        the cheap alpha-dependent half (``_finalize``) several times over ONE
        draw — this is how ``abk explore`` answers an alpha drag over a
        bootstrap series without redrawing.

        The concrete contract lives in the family base that composes the two
        halves (today only
        :class:`~abkit.stats.bootstrap.bootstrap.BaseBootstrapMethod`, which
        returns a :class:`~abkit.stats.bootstrap.bootstrap.ResampleOutcome` and
        owns the matching ``_finalize``). Declaring the flag without both halves
        is a plugin bug, and this default says so instead of failing with a bare
        ``AttributeError`` deep inside a caller.
        """
        declared = (
            "declares supports_resample_memo but implements"
            if (self.supports_resample_memo)
            else "does not declare supports_resample_memo and implements"
        )
        raise NotImplementedError(
            f"{self.name}: {declared} no _resample/_finalize split "
            "(see BaseBootstrapMethod for the contract)"
        )

    # --- shared result assembly ----------------------------------------------
    def _result_from_normal_test(
        self,
        test: NormalTest,
        *,
        name_1: str | None,
        name_2: str | None,
        value_1: float,
        value_2: float,
        std_1: float,
        std_2: float,
        size_1: int,
        size_2: int,
        cov_value_1: float | None = None,
        cov_value_2: float | None = None,
        cov_std_1: float | None = None,
        cov_std_2: float | None = None,
        corr_coef_1: float | None = None,
        corr_coef_2: float | None = None,
        mde_1: float | None = None,
        mde_2: float | None = None,
        method_warnings: Sequence[str] = (),
        diagnostics: dict[str, float] | None = None,
    ) -> TestResult:
        """Assemble the shared closed-form ``TestResult`` tail (M7 WP1 A7).

        The parametric mirror of the bootstrap ``_finalize``: every closed-form
        method ends in the same ~20-kwarg ``TestResult(...)`` differing only in
        the per-arm display fields, so the assembly lives once here (field-drift
        risk, stats-core-review A7). ``method_warnings`` are prepended before the
        test's own warnings — each method's legacy warning order is preserved.
        """
        return TestResult(
            name_1=name_1,
            name_2=name_2,
            value_1=value_1,
            value_2=value_2,
            std_1=std_1,
            std_2=std_2,
            size_1=size_1,
            size_2=size_2,
            cov_value_1=cov_value_1,
            cov_value_2=cov_value_2,
            cov_std_1=cov_std_1,
            cov_std_2=cov_std_2,
            corr_coef_1=corr_coef_1,
            corr_coef_2=corr_coef_2,
            mde_1=mde_1,
            mde_2=mde_2,
            method_name=self.name,
            method_params=self.identity_params,
            alpha=self.alpha,
            pvalue=test.pvalue,
            effect=test.effect,
            ci_length=test.ci_length,
            left_bound=test.left_bound,
            right_bound=test.right_bound,
            reject=test.reject,
            effect_distribution=test.distribution,
            warnings=[*method_warnings, *test.warnings],
            diagnostics={} if diagnostics is None else diagnostics,
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(alpha={self.alpha}, params={self.params})"
