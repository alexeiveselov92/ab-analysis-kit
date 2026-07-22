"""The explore recompute engine — Tiers E/α/S/R over one session (D1).

One knob-change request is answered entirely from in-memory state
(m3-implementation-plan.md WP4; cli-and-dx §2 as amended by D1 — "no DB
round-trip" means no *warehouse* round-trip per knob change):

- **Tier E (exact, whole grid):** suffstats reconstructed from persisted
  rows. t-test — ``SufficientStats(n=size_i, mean=value_i, m2=std_i²·size_i)``;
  z-test — ``nobs`` inverted from the persisted SE (``nobs = p(1−p)/std_i²``),
  NEVER taken from ``size_i`` (the one-row-per-unit count — the critique
  blocker); ratio-delta — the exact surrogate ``RatioSufficientStats(n,
  value_i, std_i²·n, mean_den=1, m2_den=0, c_nd=0)`` (``_arm_linearisation``
  then reproduces ``R = value_i`` and ``var0_L = std_i²`` exactly);
  cuped-t-test (M9 WP2) — the full covariate ``SufficientStats`` from the
  M9 WP1 persisted moments (``cov_m2 = cov_std_i²·size_i``, ``cross_c =
  corr_coef_i·√(m2·cov_m2)``), gated on the live ``covariate_lookback``
  matching the one the row was computed with (else Tier R — see below).
- **Tier α (approx, whole grid):** alpha-inversion for closed-form rows —
  every parametric CI here is ``effect ± z·se`` (``effects.normal_test``, the
  z-test's explicit quantiles), so ``se = (right − left) / (2·z_{1−α/2})``
  recovers the SE and the α-independent p-value passes through. Percentile
  bootstrap CIs are NOT normal-invertible — resampling methods (declaratively:
  a ``seed`` param spec) never take this path.
- **Tier S (exact, cached cutoffs):** ``from_samples`` over the session
  cache — bootstrap knobs, the stratify toggle, and pre-M9 CUPED rows (the
  covariate-moment columns are NULL, so Tier E cannot reconstruct them).
  Bootstrap seeds are re-derived per the persisted-row convention
  (``derive_seed(exp, metric, name_1, name_2, end_ts, n_samples)``,
  analyze-parity) so unchanged knobs reproduce stored rows byte-exactly under
  the D11 canonical unit order.
- **Tier R (classified only):** CUPED off→on without a cached covariate and
  ``covariate_lookback`` changes need a new pre-period render — the knob
  surface marks them ``R``; the serialized ``/reload`` action (WP6) executes
  them, never a silent per-knob warehouse hit.

Identity is hashed ONLY through the bound probe (``MethodConfig`` →
``BaseMethod.method_config_id`` — one canonical path); the knob panel
metadata is auto-derived from ``param_specs`` (invariant 3 — nothing here
special-cases a method name); ``QuarantinedMethodError`` surfaces verbatim.

The calibration lookup (D3) lives here as :func:`find_calibration` — keyed by
``(metric, method_config_id, alpha)`` against the as-built ``_ab_aa_runs``
schema — one function ``abk validate`` (M4) reuses.

Thread discipline: everything in this module reads the immutable session —
no DB handles, safe under the WP6 request lock.
"""

from __future__ import annotations

import math
import warnings as _warnings
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

import scipy.stats as sps

from abkit.config.method_config import MethodConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.interval import Interval
from abkit.loaders.metric_loader import MetricLoadResult
from abkit.pipeline.analyze import build_container
from abkit.stats import (
    AbkitStatsWarning,
    Fraction,
    MethodParamError,
    RatioSufficientStats,
    SufficientStats,
    TestResult,
    available_methods,
    create_method,
    derive_seed,
    get_method_class,
)
from abkit.stats.base import BaseMethod, ParamSpec
from abkit.stats.power import get_cuped_ttest_power, get_fraction_power, get_ttest_power
from abkit.stats.sequential import mixture_tau2, se_from_ci_length, to_always_valid
from abkit.tuning.session import ComparisonSeries, ExploreSession
from abkit.utils.json_utils import json_loads

Tier = Literal["exact", "approx", "baseline"]

#: knob → tier letters used by the side rail (WP7): E exact whole-grid,
#: S session-cache (cached cutoffs), R explicit reload.
KnobTier = Literal["E", "S", "R"]

_KIND_ROLES: dict[str, tuple[str, ...]] = {
    "sample": ("value",),
    "fraction": ("count", "nobs"),
    "ratio": ("numerator", "denominator"),
}


# ── declarative family probes (never method names — invariant 3) ────────────


def _needs_seed(method_cls: type[BaseMethod]) -> bool:
    """Resampling family: per-row derived seeds; percentile (non-normal) CIs."""
    return any(spec.name == "seed" for spec in method_cls.param_specs)


def _needs_covariate(method_cls: type[BaseMethod]) -> bool:
    """CUPED family probe: declares ``covariate_lookback`` (the pre-period
    render knob). Since M9 WP2 this no longer demotes the family's tier —
    the persisted covariate moments make it Tier-E reconstructable."""
    return any(spec.name == "covariate_lookback" for spec in method_cls.param_specs)


def classify_knob(method_cls: type[BaseMethod], knob: str) -> KnobTier:
    """The D1 tier a change of ``knob`` recomputes through, per family."""
    if knob == "covariate_lookback":
        return "R"  # a different lookback is a new pre-period render
    if _needs_seed(method_cls):
        return "S"
    return "E"


def alpha_knob_tier(method_cls: type[BaseMethod]) -> KnobTier:
    """Tier for the experiment-level alpha knob under this family."""
    if _needs_seed(method_cls):
        return "S"  # percentile CI: re-resample from the cache
    return "E"


def _spec_payload(spec: ParamSpec) -> dict[str, Any]:
    """One ``ParamSpec`` as a JSON-safe knob descriptor (D12: the rail is
    auto-derived from these — a knob that has no spec cannot appear)."""
    return {
        "name": spec.name,
        "type": "|".join(t.__name__ for t in spec.types),
        "default": spec.default,
        "identity": spec.identity,
        "choices": list(spec.choices) if spec.choices is not None else None,
        "minimum": spec.minimum,
        "maximum": spec.maximum,
        "exclusive_bounds": spec.exclusive_bounds,
        "description": spec.description,
    }


def _lookback_seconds(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return Interval(value).seconds
    except Exception:
        return None


# ── request / reply shapes ───────────────────────────────────────────────────


@dataclass(frozen=True)
class KnobState:
    """One live knob combination: method + params + effective alpha.

    ``alpha`` is the effective per-comparison alpha (experiment-level knobs —
    raw alpha and correction — resolve to it upstream, WP6); it never enters
    ``method_config_id``. A ``seed`` in ``params`` is ignored with a warning —
    the engine always injects the deterministic per-row seed (analyze-parity;
    identity-excluded).
    """

    method_name: str
    params: Mapping[str, Any] = field(default_factory=dict)
    alpha: float = 0.05


@dataclass
class ExplorePoint:
    """One recomputed cutoff of one pair (§5.3 point semantics, full names —
    WP6 maps to the terse payload keys)."""

    end_ts: datetime
    elapsed_days: float | None
    tier: Tier
    effect: float | None
    left_bound: float | None
    right_bound: float | None
    pvalue: float | None
    reject: bool | None
    mde_1: float | None
    mde_2: float | None
    value_1: float | None
    value_2: float | None
    std_1: float | None
    std_2: float | None
    size_1: int | None
    size_2: int | None
    insufficient: bool = False
    warnings: list[str] = field(default_factory=list)
    result: TestResult | None = field(default=None, repr=False, compare=False)


@dataclass
class PairRecompute:
    """One (control, treatment) pair's recomputed series + windshield chips."""

    name_1: str
    name_2: str
    points: list[ExplorePoint]
    chips: dict[str, Any]


@dataclass
class CalibrationStatus:
    """The D3 chip state for one (metric, method_config_id, alpha) key."""

    state: Literal["uncalibrated", "calibrated", "alpha_mismatch"]
    alpha: float
    fpr: float | None = None
    peeking_fpr: float | None = None
    #: M5 D8 — the always-valid peeking FPR beside the fixed one (the recovery to ~α).
    peeking_fpr_sequential: float | None = None
    calibrated_alpha: float | None = None
    budget: float | None = None
    over_budget: bool | None = None
    runs: int = 0
    headline: str = ""


@dataclass
class RecomputeResult:
    """One knob state answered: per-pair series, live identity, calibration."""

    metric: str
    method_name: str
    method_config_id: str
    alpha: float
    identity_changed: bool
    pairs: list[PairRecompute]
    calibration: CalibrationStatus
    warnings: list[str] = field(default_factory=list)


# ── calibration (D3) ─────────────────────────────────────────────────────────


def resolve_fpr_budget(
    project: ProjectConfig, alpha: float, metric: MetricConfig | None = None
) -> float:
    """metric ``aa_fpr_budget`` → project ``aa_fpr_budget`` → ``α × 1.5``
    (aa-false-positive-matrix.md §4.1; D12). One resolver, never a hardcode."""
    if metric is not None and metric.aa_fpr_budget is not None:
        return float(metric.aa_fpr_budget)
    budget = project.statistics.aa_fpr_budget
    return float(budget) if budget is not None else alpha * 1.5


def find_calibration(
    aa_rows: list[dict],
    metric: str,
    method_config_id: str,
    alpha: float,
    budget: float | None = None,
) -> CalibrationStatus:
    """The chip lookup, keyed by ``(metric, method_config_id, alpha)`` (D3).

    Alpha is in the key: it is identity-excluded from ``method_config_id``,
    but empirical FPR is measured at a specific nominal α — an alpha edit
    downgrades the chip to ``alpha_mismatch`` and gates like uncalibrated.
    ``status='failed'`` rows and rows without an ``fpr`` never count. Any
    identity or alpha edit flips the chip automatically — that IS the
    staleness semantics (no separate "stale" state).
    """
    matches = [
        row
        for row in aa_rows
        if row.get("metric") == metric
        and row.get("method_config_id") == method_config_id
        and row.get("status") == "success"
        and row.get("fpr") is not None
    ]
    matches.sort(
        key=lambda row: (row.get("created_at") is not None, row.get("created_at")), reverse=True
    )
    if not matches:
        return CalibrationStatus(
            state="uncalibrated",
            alpha=alpha,
            budget=budget,
            headline="uncalibrated — run `abk validate` (M4)",
        )

    same_alpha = [
        row
        for row in matches
        if math.isclose(float(row["alpha"]), alpha, rel_tol=1e-9, abs_tol=1e-12)
    ]
    if not same_alpha:
        calibrated_alpha = float(matches[0]["alpha"])
        return CalibrationStatus(
            state="alpha_mismatch",
            alpha=alpha,
            calibrated_alpha=calibrated_alpha,
            budget=budget,
            runs=len(matches),
            headline=(
                f"calibrated at α={calibrated_alpha:g}, current α={alpha:g} — "
                "re-run `abk validate` for this alpha"
            ),
        )

    newest = same_alpha[0]
    fpr = float(newest["fpr"])
    peeking = newest.get("peeking_fpr")
    peeking_seq = newest.get("peeking_fpr_sequential")
    over = None if budget is None else fpr > budget
    headline = f"calibrated — FPR {fpr:.1%} vs nominal α={alpha:g}"
    if over:
        headline += f" (over the {budget:.1%} budget)"
    # M5 D8: surface the always-valid recovery when peeking inflates past budget.
    if peeking is not None and peeking_seq is not None:
        headline += f"; peeking {float(peeking):.1%}→{float(peeking_seq):.1%} (always-valid)"
    return CalibrationStatus(
        state="calibrated",
        alpha=alpha,
        fpr=fpr,
        peeking_fpr=None if peeking is None else float(peeking),
        peeking_fpr_sequential=None if peeking_seq is None else float(peeking_seq),
        budget=budget,
        over_budget=over,
        runs=len(same_alpha),
        headline=headline,
    )


# ── row-level reconstruction helpers ─────────────────────────────────────────


def _row_float(row: dict, key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _row_int(row: dict, key: str) -> int | None:
    value = row.get(key)
    return None if value is None else int(value)


def _invert_fraction(prop: float, std: float, name: str | None) -> Fraction | None:
    """The z-test inversion: ``nobs = p(1−p)/std²`` from the persisted SE.

    Degenerate rows (``p ∈ {0, 1}`` ⇒ ``std = 0``) are not invertible —
    they route to Tier S / pass-through.
    """
    if not (0.0 < prop < 1.0) or not (math.isfinite(std) and std > 0.0):
        return None
    nobs = prop * (1.0 - prop) / (std * std)
    if not math.isfinite(nobs) or nobs <= 0.0:
        return None
    return Fraction(count=prop * nobs, nobs=nobs, name=name)


def _parse_params(value: Any) -> dict[str, Any]:
    """The row's canonical ``method_params`` JSON cell as a dict; never raises."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json_loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_serves_suffstats(row: dict) -> bool:
    """Whether this row's per-arm columns carry MEAN/std semantics.

    Rows written by a resampling method with a non-mean ``stat`` persist the
    bootstrapped statistic (e.g. the median) in ``value_i`` — reconstructing
    mean-based suffstats from them would be silently wrong, never "exact".
    Unknown/quarantined legacy row methods are never reconstructed.
    """
    method_name = row.get("method_name")
    if not method_name:
        return False
    try:
        row_cls = get_method_class(method_name)
    except Exception:
        return False
    if any(spec.name == "stat" for spec in row_cls.param_specs):
        if _parse_params(row.get("method_params")).get("stat", "mean") != "mean":
            return False
    return True


def _covariate_suffstats_fields(
    row: dict, size: int, m2: float, suffix: str
) -> tuple[float, float, float] | None:
    """One arm's ``(cov_mean, cov_m2, cross_c)`` from the persisted covariate
    moments (M9 WP1), or ``None`` when the row cannot serve them.

    Inverts the ``SufficientStats`` properties: ``cov_m2 = cov_std²·n`` and
    ``cross_c = corr_coef·√(m2·cov_m2)``. Pre-migration rows persist NULL in
    the three columns, and a degenerate covariate persists a NULL
    ``corr_coef`` (NaN→None on write) — both degrade to the Tier S/baseline
    fallback, never a NaN riding into a "successful" result.
    """
    cov_mean = _row_float(row, f"cov_value_{suffix}")
    cov_std = _row_float(row, f"cov_std_{suffix}")
    corr_coef = _row_float(row, f"corr_coef_{suffix}")
    if cov_mean is None or cov_std is None or corr_coef is None:
        return None
    cov_m2 = cov_std * cov_std * size
    return cov_mean, cov_m2, corr_coef * math.sqrt(m2 * cov_m2)


def _exact_suffstats(
    method_cls: type[BaseMethod],
    row: dict,
    resolved_params: Mapping[str, Any] | None = None,
) -> tuple[Any, Any] | None:
    """Tier E: both arms' suffstats containers from one persisted row, or
    ``None`` when this family/row is not exactly reconstructable.

    The covariate family (``requires_covariate``) is additionally gated on the
    live ``covariate_lookback`` matching the value the row was computed with
    (its persisted ``method_params``) — a changed lookback is a NEW pre-period
    render (Tier R, matching ``classify_knob``'s unconditional ``R`` for this
    knob), never a silent reconstruction against a stale covariate. The
    comparison is UNCONDITIONAL — unlike ``_cache_serves``, which skips it for
    declared-covariate metrics because the cache is a fresh live load, a
    persisted row is frozen: its moments were computed under whatever
    covariate source the config had at write time, so equal lookbacks are the
    strongest reconstruction claim this function can honestly make.
    """
    if _needs_seed(method_cls):
        return None
    if method_cls.requires_covariate and method_cls.input_kind != "sample":
        return None  # no persisted covariate-moment shape for these kinds
    if not _row_serves_suffstats(row):
        return None
    value_1, value_2 = _row_float(row, "value_1"), _row_float(row, "value_2")
    std_1, std_2 = _row_float(row, "std_1"), _row_float(row, "std_2")
    size_1, size_2 = _row_int(row, "size_1"), _row_int(row, "size_2")
    if value_1 is None or value_2 is None or std_1 is None or std_2 is None:
        return None
    if size_1 is None or size_2 is None or size_1 <= 0 or size_2 <= 0:
        return None
    name_1, name_2 = row.get("name_1"), row.get("name_2")

    kind = method_cls.input_kind
    if kind == "sample":
        # std_i is the persisted np.std (ddof=0) ⇒ m2 = std² · n exactly.
        m2_1, m2_2 = std_1 * std_1 * size_1, std_2 * std_2 * size_2
        if method_cls.requires_covariate:
            if size_1 < 2 or size_2 < 2:
                return None  # θ's ddof=1 terms need n ≥ 2 per arm
            requested = _lookback_seconds((resolved_params or {}).get("covariate_lookback"))
            row_lookback = _parse_params(row.get("method_params")).get("covariate_lookback")
            if requested != _lookback_seconds(row_lookback):
                return None  # a different pre-period — Tier R, not a reconstruction
            fields_1 = _covariate_suffstats_fields(row, size_1, m2_1, "1")
            fields_2 = _covariate_suffstats_fields(row, size_2, m2_2, "2")
            if fields_1 is None or fields_2 is None:
                return None  # pre-migration row (or degenerate covariate)
            cov_mean_1, cov_m2_1, cross_c_1 = fields_1
            cov_mean_2, cov_m2_2, cross_c_2 = fields_2
            return (
                SufficientStats(
                    n=size_1,
                    mean=value_1,
                    m2=m2_1,
                    cov_mean=cov_mean_1,
                    cov_m2=cov_m2_1,
                    cross_c=cross_c_1,
                    name=name_1,
                ),
                SufficientStats(
                    n=size_2,
                    mean=value_2,
                    m2=m2_2,
                    cov_mean=cov_mean_2,
                    cov_m2=cov_m2_2,
                    cross_c=cross_c_2,
                    name=name_2,
                ),
            )
        return (
            SufficientStats(n=size_1, mean=value_1, m2=m2_1, name=name_1),
            SufficientStats(n=size_2, mean=value_2, m2=m2_2, name=name_2),
        )
    if kind == "fraction":
        fraction_1 = _invert_fraction(value_1, std_1, name_1)
        fraction_2 = _invert_fraction(value_2, std_2, name_2)
        if fraction_1 is None or fraction_2 is None:
            return None
        return (fraction_1, fraction_2)
    if kind == "ratio":
        # std_i is the per-unit linearised std ⇒ the den≡1 surrogate is exact:
        # _arm_linearisation gives R = value_i and var0_L = std_i² back.
        return (
            RatioSufficientStats(
                n=size_1,
                mean_num=value_1,
                m2_num=std_1 * std_1 * size_1,
                mean_den=1.0,
                m2_den=0.0,
                c_nd=0.0,
                name=name_1,
            ),
            RatioSufficientStats(
                n=size_2,
                mean_num=value_2,
                m2_num=std_2 * std_2 * size_2,
                mean_den=1.0,
                m2_den=0.0,
                c_nd=0.0,
                name=name_2,
            ),
        )
    return None


def _alpha_inverted_bounds(row: dict, new_alpha: float) -> tuple[float, float, float, bool] | None:
    """Tier α: ``(left, right, pvalue, reject)`` at ``new_alpha`` from a
    closed-form row's symmetric normal CI; ``None`` when not invertible."""
    effect = _row_float(row, "effect")
    left = _row_float(row, "left_bound")
    right = _row_float(row, "right_bound")
    pvalue = _row_float(row, "pvalue")
    row_alpha = _row_float(row, "alpha")
    if None in (effect, left, right, pvalue, row_alpha):
        return None
    if not 0.0 < row_alpha < 1.0 or right < left:
        return None
    z_old = float(sps.norm.ppf(1.0 - row_alpha / 2.0))
    if not math.isfinite(z_old) or z_old <= 0.0:
        return None
    se = (right - left) / (2.0 * z_old)
    z_new = float(sps.norm.ppf(1.0 - new_alpha / 2.0))
    return (
        effect - z_new * se,
        effect + z_new * se,
        pvalue,
        bool(pvalue < new_alpha),
    )


def _clean(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


# ── the engine ───────────────────────────────────────────────────────────────


class RecomputeEngine:
    """Answers knob states from one immutable :class:`ExploreSession`."""

    def __init__(self, session: ExploreSession) -> None:
        self._session = session

    # -- knob metadata --------------------------------------------------------

    def default_knobs(self, metric: str) -> KnobState:
        """The configured baseline knob state for ``metric``."""
        series = self._session.series(metric)
        return KnobState(
            method_name=series.comparison.method.name,
            params=dict(series.comparison.method.params),
            alpha=series.configured_alpha,
        )

    def knob_surface(self, metric: str) -> dict[str, Any]:
        """The side-rail metadata block (D12): methods valid for this metric
        type, their ``param_specs`` verbatim, and the per-knob tier map."""
        series = self._session.series(metric)
        methods: list[dict[str, Any]] = []
        for name in available_methods():
            method_cls = get_method_class(name)
            if method_cls.input_kind != series.metric.type or method_cls.is_paired:
                continue
            methods.append(
                {
                    "name": name,
                    "seeded": _needs_seed(method_cls),
                    # the ↻ badge substrate: WP7 marks the method Tier R when
                    # this is true and the cache holds no covariate cutoffs
                    "needs_covariate": method_cls.requires_covariate,
                    "alpha_tier": alpha_knob_tier(method_cls),
                    # correction resolves to the effective per-comparison alpha
                    # upstream (analyze.effective_alphas), so it recomputes
                    # through the same tier the alpha knob does (WP4 DoD)
                    "correction_tier": alpha_knob_tier(method_cls),
                    "params": [_spec_payload(spec) for spec in method_cls.param_specs],
                    "tiers": {
                        spec.name: classify_knob(method_cls, spec.name)
                        for spec in method_cls.param_specs
                    },
                }
            )
        cached = self._session.cached_cutoffs(metric)
        covariate_cutoffs = []
        for ts in cached:
            loaded = self._session.loaded(metric, ts)
            if loaded is not None and loaded.roles_by_variant:
                if all("covariate" in roles for roles in loaded.roles_by_variant.values()):
                    covariate_cutoffs.append(ts)
        # M9 WP2: whether the CONFIGURED series' persisted rows carry the full
        # covariate moments (Tier-E reconstructable without any cache) — the
        # client's reload heuristic exempts a switch BACK to the configured
        # covariate method when this is true (no warehouse trip needed).
        covariate_moment_rows = any(
            all(
                _row_float(row, f"{column}_{suffix}") is not None
                for column in ("cov_value", "cov_std", "corr_coef")
                for suffix in ("1", "2")
            )
            for row in series.rows
        )
        return {
            "metric": metric,
            "metric_type": series.metric.type,
            "configured": {
                "method": series.comparison.method.name,
                "params": dict(series.comparison.method.params),
                "method_config_id": series.comparison.method.method_config_id,
                "alpha": series.configured_alpha,
            },
            "methods": methods,
            "cache": {
                "cutoffs": cached,
                "covariate_cutoffs": covariate_cutoffs,
                "covariate_moment_rows": covariate_moment_rows,
                "disabled_reason": self._session.cache_disabled_reason,
            },
        }

    # -- recompute ------------------------------------------------------------

    def recompute(self, metric: str, knobs: KnobState) -> RecomputeResult:
        """One knob state → per-pair series + chips + identity + calibration.

        Raises the stats-core exceptions verbatim (``UnknownMethodError``,
        ``QuarantinedMethodError``, ``MethodParamError``) — WP6 maps them to
        HTTP 400s; they are never swallowed or substituted.
        """
        session = self._session
        series = session.series(metric)
        engine_warnings: list[str] = []

        if not 0.0 < knobs.alpha < 1.0:
            raise MethodParamError(f"alpha must be in (0, 1), got {knobs.alpha}")

        params = dict(knobs.params)
        if "seed" in params:
            params.pop("seed")
            engine_warnings.append(
                "seed is derived per row (identity-excluded) — the supplied value is ignored"
            )

        # ONE canonical identity/validation path: config model → bound probe.
        method_config = MethodConfig(name=knobs.method_name, params=params)
        probe = method_config.bind(alpha=knobs.alpha)
        method_cls = type(probe)

        # The analyze_cutoff-parity gate (analyze.py): without it a cross-kind
        # knob state would reconstruct nonsense suffstats (a fraction row's
        # std_i is the SE, not a sample std) and label it tier="exact".
        if method_cls.is_paired:
            raise MethodParamError(
                f"method '{knobs.method_name}' is a paired design — explore serves "
                "independent-arm experiments (use the notebook API for paired data)"
            )
        if method_cls.input_kind != series.metric.type:
            raise MethodParamError(
                f"method '{knobs.method_name}' expects a '{method_cls.input_kind}' "
                f"metric, got '{series.metric.type}' — pick a method from the knob "
                "surface's method list"
            )

        live_id = probe.method_config_id
        identity_changed = live_id != series.comparison.method.method_config_id

        reusable = None if _needs_seed(method_cls) else probe

        pair_rows: dict[tuple[str, str], list[dict]] = {}
        for row in series.rows:
            pair_rows.setdefault((row["name_1"], row["name_2"]), []).append(row)

        # M5 WP3c: the cockpit is a READ-VIEW over persisted rows (D2), so it must
        # never mix CI vocabularies within a pair's series. A pair is widened into
        # the always-valid CI live iff its BAKED rows are already always_valid —
        # NOT off config.sequential (which only governs the next `abk run`). This
        # reproduces the baked per-pair vocabulary exactly: the multi-pair case
        # where the driver's first-usable-look anchor left a late-usable pair fixed,
        # and a config toggle not yet applied by a re-run (either direction). Gated
        # on the LIVE method's declarative supports_sequential — a knob switch to a
        # percentile-CI method (bootstrap) can never be widened.
        av_pairs: set[tuple[str, str]] = set()
        if method_cls.supports_sequential:
            for pair, rows in pair_rows.items():
                if any(
                    r.get("ci_kind") == "always_valid" and not r.get("insufficient_data")
                    for r in rows
                ):
                    av_pairs.add(pair)

        seq_reload_needed = False
        pairs: list[PairRecompute] = []
        for (name_1, name_2), rows in pair_rows.items():
            points: list[ExplorePoint] = []
            for row in rows:
                point = self._compute_point(
                    series, row, method_cls, probe.params, knobs, reusable, identity_changed
                )
                if point is not None:
                    points.append(point)
            if (name_1, name_2) in av_pairs:
                points, dropped = self._sequentialize_points(points, knobs.alpha)
                seq_reload_needed = seq_reload_needed or dropped
            chips = self._chips(series, points, method_cls, probe.params, knobs, name_1)
            pairs.append(PairRecompute(name_1=name_1, name_2=name_2, points=points, chips=chips))

        if seq_reload_needed:
            engine_warnings.append(
                "alpha recompute is unavailable for some cutoffs under the sequential "
                "mode (their always-valid CI cannot be re-derived by α-inversion) — "
                "use Reload to recompute them"
            )

        calibration = find_calibration(
            session.aa_rows,
            metric,
            live_id,
            knobs.alpha,
            budget=resolve_fpr_budget(session.project, knobs.alpha, series.metric),
        )
        if session.cache_disabled_reason is not None:
            engine_warnings.append(session.cache_disabled_reason)

        return RecomputeResult(
            metric=metric,
            method_name=method_cls.name,
            method_config_id=live_id,
            alpha=knobs.alpha,
            identity_changed=identity_changed,
            pairs=pairs,
            calibration=calibration,
            warnings=engine_warnings,
        )

    # -- internals -------------------------------------------------------------

    def _compute_point(
        self,
        series: ComparisonSeries,
        row: dict,
        method_cls: type[BaseMethod],
        resolved_params: dict[str, Any],
        knobs: KnobState,
        reusable: BaseMethod | None,
        identity_changed: bool,
    ) -> ExplorePoint | None:
        """One row → the best-tier point, or ``None`` (a baseline-only gap)."""
        if row.get("insufficient_data"):
            # Demoted rows pass through UNTOUCHED (WP4: NULL test columns,
            # real sizes) — the client greys them; they are method-independent
            # facts, so they ride along under every knob state.
            return self._baseline_point(row)

        # Tier E — exact reconstruction, whole grid.
        containers = _exact_suffstats(method_cls, row, resolved_params)
        if containers is not None and reusable is not None:
            result, caught = _compare(reusable, *containers)
            return self._point_from_result(row, result, caught, tier="exact")

        # Tier S — the session cache (from_samples), cached cutoffs only.
        loaded = self._session.loaded(series.metric.name, row["end_ts"])
        entry_lookback = self._session.cache_lookback.get((series.metric.name, row["end_ts"]))
        if loaded is not None and self._cache_serves(
            series,
            method_cls,
            resolved_params,
            loaded,
            row["name_1"],
            row["name_2"],
            entry_lookback,
        ):
            group_1 = build_container(method_cls.input_kind, row["name_1"], loaded)
            group_2 = build_container(method_cls.input_kind, row["name_2"], loaded)
            if reusable is not None:
                method = reusable
            else:
                seeded_params = dict(knobs.params)
                seeded_params.pop("seed", None)
                seeded_params["seed"] = derive_seed(
                    self._session.experiment.name,
                    series.metric.name,
                    row["name_1"],
                    row["name_2"],
                    row["end_ts"],
                    seeded_params.get("n_samples", 1000),
                )
                method = create_method(knobs.method_name, alpha=knobs.alpha, params=seeded_params)
            result, caught = _compare(method, group_1, group_2)
            return self._point_from_result(row, result, caught, tier="exact")

        if identity_changed:
            return None  # a different series with no way to compute it here

        # Same identity: pass persisted numbers through, or α-invert them.
        if math.isclose(knobs.alpha, _row_float(row, "alpha") or -1.0, rel_tol=1e-12):
            return self._baseline_point(row)
        if not _needs_seed(method_cls):
            inverted = _alpha_inverted_bounds(row, knobs.alpha)
            if inverted is not None:
                left, right, pvalue, reject = inverted
                return ExplorePoint(
                    end_ts=row["end_ts"],
                    elapsed_days=_row_float(row, "elapsed_days"),
                    tier="approx",
                    effect=_row_float(row, "effect"),
                    left_bound=_clean(left),
                    right_bound=_clean(right),
                    pvalue=_clean(pvalue),
                    reject=reject,
                    mde_1=None,  # the stored MDE was solved at the old alpha
                    mde_2=None,
                    value_1=_row_float(row, "value_1"),
                    value_2=_row_float(row, "value_2"),
                    std_1=_row_float(row, "std_1"),
                    std_2=_row_float(row, "std_2"),
                    size_1=_row_int(row, "size_1"),
                    size_2=_row_int(row, "size_2"),
                )
        if _row_float(row, "effect") is None:
            # NULLed rows (H5 NaN outputs) pass through untouched under any
            # same-identity knob state — there is no number to mislabel.
            return self._baseline_point(row)
        return None

    def _sequentialize_points(
        self, points: list[ExplorePoint], alpha: float
    ) -> tuple[list[ExplorePoint], bool]:
        """Widen ONE always-valid pair's reconstructed points into the CS (M5 WP3c).

        Called only for pairs the baked series persisted as ``always_valid`` (the
        caller's ``av_pairs`` gate). Freeze τ² from the first look with a usable fixed
        CI — mirroring ``driver._sequential_tau2``'s D-Seq-anchor via the SAME
        ``se_from_ci_length``/``mixture_tau2`` helpers — then widen every reconstructed
        (Tier E/S) point with ``to_always_valid``. For fully Tier-E-reconstructable
        families (t/z/ratio, and CUPED since M9 WP2) the recovered fixed CI equals the
        pipeline's pre-widening CI, so the configured knob state reproduces the baked
        always-valid bounds. (A partially-cached PRE-MIGRATION CUPED series — NULL
        covariate-moment columns — may anchor τ² a look later than the pipeline: its
        uncached first look has no ``result`` to invert — still a valid confidence
        sequence, never a vocabulary mix.)

        - **Tier E/S** points carry the raw fixed ``result`` → widened.
        - **Baseline** points are persisted pass-throughs already carrying the mode's
          bounds (this pair is always_valid) → left untouched.
        - **α-inverted** (``tier='approx'``) points recovered their SE from an
          already-widened persisted CI, so they cannot be honestly widened → dropped
          (returns ``dropped=True`` so the caller surfaces a Reload hint), never shown
          as a silent fixed CI on a sequential chart.

        Returns ``(points, dropped_any_approx)``. When no look has a usable fixed CI
        (τ² undefined — e.g. an all-baseline CUPED pair) the points are passed through:
        baseline points already carry the persisted always-valid bounds.
        """
        tau2: float | None = None
        for point in points:
            if point.result is None:
                continue
            se = se_from_ci_length(point.result.ci_length, alpha)
            if math.isfinite(se) and se > 0.0:
                tau2 = mixture_tau2(se * se, alpha)
                break

        out: list[ExplorePoint] = []
        dropped = False
        for point in points:
            if point.result is not None:
                if tau2 is None:
                    out.append(point)  # degenerate everywhere; nothing to widen
                    continue
                av = to_always_valid(point.result, tau2, alpha)
                out.append(
                    replace(
                        point,
                        left_bound=_clean(av.left_bound),
                        right_bound=_clean(av.right_bound),
                        pvalue=_clean(av.pvalue),
                        reject=bool(av.reject),
                        result=av,
                    )
                )
            elif point.tier == "approx":
                dropped = True  # α-inversion is not valid vocabulary under sequential
            else:
                out.append(point)  # baseline pass-through — already always-valid
        return out, dropped

    def _baseline_point(self, row: dict) -> ExplorePoint:
        reject = row.get("reject")
        return ExplorePoint(
            end_ts=row["end_ts"],
            elapsed_days=_row_float(row, "elapsed_days"),
            tier="baseline",
            effect=_row_float(row, "effect"),
            left_bound=_row_float(row, "left_bound"),
            right_bound=_row_float(row, "right_bound"),
            pvalue=_row_float(row, "pvalue"),
            reject=None if reject is None else bool(reject),
            mde_1=_row_float(row, "mde_1"),
            mde_2=_row_float(row, "mde_2"),
            value_1=_row_float(row, "value_1"),
            value_2=_row_float(row, "value_2"),
            std_1=_row_float(row, "std_1"),
            std_2=_row_float(row, "std_2"),
            size_1=_row_int(row, "size_1"),
            size_2=_row_int(row, "size_2"),
            insufficient=bool(row.get("insufficient_data")),
        )

    def _point_from_result(
        self, row: dict, result: TestResult, caught: list[str], tier: Tier
    ) -> ExplorePoint:
        # Point sizes keep the ROW's persisted unit-count semantics across
        # every tier (a fraction result's size_i is round(nobs) — exposing it
        # here would make sizes jump between tiers of one series); the raw
        # ``result`` rides along for consumers that need method sizes.
        return ExplorePoint(
            end_ts=row["end_ts"],
            elapsed_days=_row_float(row, "elapsed_days"),
            tier=tier,
            effect=_clean(result.effect),
            left_bound=_clean(result.left_bound),
            right_bound=_clean(result.right_bound),
            pvalue=_clean(result.pvalue),
            reject=bool(result.reject),
            mde_1=_clean(result.mde_1),
            mde_2=_clean(result.mde_2),
            value_1=_clean(result.value_1),
            value_2=_clean(result.value_2),
            std_1=_clean(result.std_1),
            std_2=_clean(result.std_2),
            size_1=_row_int(row, "size_1"),
            size_2=_row_int(row, "size_2"),
            warnings=[*caught, *result.warnings],
            result=result,
        )

    def _cache_serves(
        self,
        series: ComparisonSeries,
        method_cls: type[BaseMethod],
        resolved_params: dict[str, Any],
        loaded: MetricLoadResult,
        name_1: str,
        name_2: str,
        entry_lookback: str | int | None = None,
    ) -> bool:
        """Whether this cached cutoff can feed ``method_cls`` for this pair.

        ``entry_lookback`` is the ``covariate_lookback`` the cache entry was
        RENDERED with (a Tier-R reload may differ from the configured method).
        """
        needed_roles = _KIND_ROLES.get(method_cls.input_kind)
        if needed_roles is None:
            return False
        for variant in (name_1, name_2):
            if loaded.size(variant) == 0:
                return False
            roles = loaded.roles_by_variant.get(variant, {})
            if any(role not in roles for role in needed_roles):
                return False
            # declared capability, not param-name guessing: post-normed
            # bootstrap needs cov_array yet has no covariate_lookback param
            if method_cls.requires_covariate and "covariate" not in roles:
                return False
            if resolved_params.get("stratify") and loaded.strata_by_variant.get(variant) is None:
                return False
        if _needs_covariate(method_cls) and series.metric.columns.covariate is None:
            # The cached covariate was rendered over the ENTRY's lookback; a
            # different lookback is a new pre-period render — Tier R.
            requested = _lookback_seconds(resolved_params.get("covariate_lookback"))
            if requested != _lookback_seconds(entry_lookback):
                return False
        return True

    def _chips(
        self,
        series: ComparisonSeries,
        points: list[ExplorePoint],
        method_cls: type[BaseMethod],
        resolved_params: dict[str, Any],
        knobs: KnobState,
        name_1: str,
    ) -> dict[str, Any]:
        """The windshield chips off the latest point WITH inference (§5.1) —
        a demoted/NULLed latest cutoff must not blank the chips when an older
        cutoff still carries numbers (it is flagged, not hidden)."""
        latest = next((point for point in reversed(points) if point.effect is not None), None)
        if latest is None:
            return {
                "lift": None,
                "ci_half": None,
                "pvalue": None,
                "power": None,
                "power_note": "no recomputable cutoffs for this knob state",
                "latest_end_ts": None,
                "tier": None,
            }
        ci_half = None
        if latest.left_bound is not None and latest.right_bound is not None:
            ci_half = (latest.right_bound - latest.left_bound) / 2.0
        power, power_note = self._pair_power(
            series, latest, method_cls, resolved_params, knobs, name_1
        )
        return {
            "lift": latest.effect,
            "ci_half": ci_half,
            "pvalue": latest.pvalue,
            "power": power,
            "power_note": power_note,
            "latest_end_ts": latest.end_ts,
            "tier": latest.tier,
        }

    def _pair_power(
        self,
        series: ComparisonSeries,
        latest: ExplorePoint,
        method_cls: type[BaseMethod],
        resolved_params: dict[str, Any],
        knobs: KnobState,
        name_1: str,
    ) -> tuple[float | None, str | None]:
        """Achieved power to detect ``min_effect`` at the current knob state.

        Family capability is declarative: resampling and MDE-less families
        report an honest reason instead of a faked number (D5(b) spirit).
        """
        min_effect = series.comparison.min_effect
        if min_effect is None:
            return None, "no min_effect configured — the power chip needs one (D5(b))"
        if _needs_seed(method_cls):
            return None, f"no power solve for resampling methods ({method_cls.name})"
        if "calculate_mde" not in {spec.name for spec in method_cls.param_specs}:
            return None, f"method '{method_cls.name}' has no power/MDE capability"
        if None in (latest.value_1, latest.std_1, latest.size_1, latest.size_2):
            return None, "per-arm stats unavailable at the latest cutoff"

        test_type = str(resolved_params.get("test_type", "relative"))
        ratio = latest.size_2 / latest.size_1
        try:
            if method_cls.input_kind == "fraction":
                # the proportion solve runs on TRIAL counts (nobs), which the
                # point's persisted unit-count sizes deliberately are not
                if latest.result is not None:
                    nobs_1, nobs_2 = latest.result.size_1, latest.result.size_2
                else:
                    inv_1 = _invert_fraction(latest.value_1, latest.std_1, None)
                    inv_2 = _invert_fraction(latest.value_2, latest.std_2, None)
                    if inv_1 is None or inv_2 is None:
                        return None, "trial counts unavailable at the latest cutoff"
                    nobs_1, nobs_2 = inv_1.sample_size, inv_2.sample_size
                power = get_fraction_power(
                    latest.value_1,
                    nobs_1,
                    min_effect,
                    test_type=test_type,
                    alpha=knobs.alpha,
                    ratio=nobs_2 / nobs_1,
                )
            elif _needs_covariate(method_cls):
                # M9 WP2: a Tier-E/S point's raw result carries the control
                # arm's correlation (the same persisted/reconstructed moments
                # the point itself was computed from) — read it first so the
                # chip agrees with the exact point beside it; fall back to the
                # session cache for pre-migration (moment-less) rows.
                corr = _clean(latest.result.corr_coef_1) if latest.result is not None else None
                if corr is None:
                    corr = self._control_corr(series, latest.end_ts, name_1)
                if corr is None:
                    return None, (
                        "CUPED power needs the covariate correlation — not "
                        "reconstructable from this row and not in the session cache"
                    )
                power = get_cuped_ttest_power(
                    latest.value_1,
                    latest.std_1,
                    corr,
                    latest.size_1,
                    min_effect,
                    test_type=test_type,
                    alpha=knobs.alpha,
                    ratio=ratio,
                )
            else:
                power = get_ttest_power(
                    latest.value_1,
                    latest.std_1,
                    latest.size_1,
                    min_effect,
                    test_type=test_type,
                    alpha=knobs.alpha,
                    ratio=ratio,
                )
        except Exception as exc:  # statsmodels solve failures are data-dependent
            return None, f"power solve failed: {exc}"
        return (power if math.isfinite(power) else None), None

    def _control_corr(
        self, series: ComparisonSeries, end_ts: datetime, name_1: str
    ) -> float | None:
        loaded = self._session.loaded(series.metric.name, end_ts)
        if loaded is None:
            return None
        roles = loaded.roles_by_variant.get(name_1, {})
        if "value" not in roles or "covariate" not in roles:
            return None
        sample = build_container("sample", name_1, loaded)
        corr = sample.corr_coef
        return None if corr is None or not math.isfinite(corr) else float(corr)


def _compare(method: BaseMethod, group_1: Any, group_2: Any) -> tuple[TestResult, list[str]]:
    """``compare_pair`` with the analyze-stage warning capture (plan R7)."""
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always", AbkitStatsWarning)
        result = method.compare_pair(group_1, group_2)
    messages = [str(w.message) for w in caught if issubclass(w.category, AbkitStatsWarning)]
    return result, messages
