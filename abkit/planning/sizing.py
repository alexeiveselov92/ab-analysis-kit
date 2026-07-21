"""Pure pre-launch sizing engine for ``abk plan`` (m5-implementation-plan.md WP6, D10).

Answers three questions per comparison from baseline moments, using the
legacy-transcribed solves in :mod:`abkit.stats.power` (never re-deriving the math):

- **required-N** per control arm to detect a target MDE at the configured power/alpha;
- **achievable MDE** at the current sample size (the retrospective bound);
- **achieved power** for the target MDE at the current sample size.

Scope boundary (honest refusal, D10): only the closed-form families in ``power.py`` are
sized — continuous (``t-test`` / CUPED) via the standardized-effect solve and
proportions (``z-test``) via Cohen's h. Ratio metrics and resampling (bootstrap)
methods have **no versioned power formula**, so the caller refuses them rather than
invent math. CUPED is sized on the **raw** persisted variance (the covariate
correlation is not persisted per ``_ab_results`` row — see ``cuped_ttest.py``: per-arm
``std`` reports the *original* std): a conservative upper bound on required-N, which the
caller flags.

**Runtime / ASN (WP-A, m6-implementation-plan.md).** Two forward-looking timing
answers layered on the sizing solves, both keyed on a unit-arrival rate the caller
derives read-only from the cohort source (or a ``--arrival-rate`` override):

- **runtime** — days-to-required-N = ``required_n / arrival_rate`` (a deterministic
  division); and
- **ASN** — the always-valid sequential design's *average sample number*: the expected
  control-arm N at which the confidence sequence first excludes zero under the true
  target effect (H1) and under the null (H0). It reuses the EXACT shipped CS boundary
  (``abkit.stats.sequential``) — never a second estimator — and is a Monte-Carlo
  estimate over the canonical information-time Gaussian process of the per-look
  estimate (Jennison & Turnbull; a deterministic fixed seed, so a re-plan is stable).
  ASN is emitted only for a sequential-eligible design; a fixed-horizon design reports
  "ASN n/a". Without an arrival rate BOTH are skipped, never invented.

Pure: numpy + ``abkit.stats.power`` + ``abkit.stats.sequential`` + stdlib only — no
config types, no DB. The CLI command reads the persisted moments, the effective
two-tier alpha, and the arrival rate, and translates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from abkit.stats.power import (
    get_fraction_mde,
    get_fraction_power,
    get_fraction_sample_size,
    get_ttest_mde,
    get_ttest_power,
    get_ttest_sample_size,
)
from abkit.stats.sequential import mixture_tau2, sequentialize

#: sizing families this engine can honestly compute (dispatched on the metric's
#: declarative ``input_kind``; ``ratio`` and resampling are refused by the caller).
SAMPLE = "sample"
FRACTION = "fraction"


@dataclass(frozen=True)
class BaselineMoments:
    """The per-arm baseline the sizing math needs, from one control/treatment pair.

    ``n`` / ``n_other`` are analysis units for ``sample`` and **trials** (nobs) for
    ``fraction`` (inverted from the persisted proportion SE ``sqrt(p(1−p)/nobs)`` —
    the same inversion the readout MDE fallback uses). ``std`` is ``None`` for a
    ``fraction`` (the proportion carries its own variance).
    """

    kind: str  # SAMPLE | FRACTION
    baseline: float  # control mean (sample) or control proportion (fraction)
    n: float  # control-arm N (units for sample; trials for fraction)
    n_other: float  # treatment-arm N (same unit)
    std: float | None = None  # control std (sample only)
    source: str = ""  # human label — "persisted @ <ts>" or "--baseline override"

    @property
    def observed_ratio(self) -> float:
        """treatment:control allocation actually observed (for the retrospective bounds)."""
        return self.n_other / self.n if self.n > 0 else 1.0


@dataclass(frozen=True)
class SizingResult:
    """The three sizing answers for one comparison at one (alpha, power, target)."""

    required_n: int | float | None  # per control arm at ``plan_ratio``; ``inf`` if
    # the target is unachievable at this baseline; None when there is no target MDE
    achievable_mde: float | None  # at the current sample size (may be ``inf``)
    achieved_power: float | None  # for ``target_mde`` at the current size; None w/o target


@dataclass(frozen=True)
class ComparisonPlan:
    """A fully-labelled plan line for one comparison (or a refusal)."""

    metric: str
    method_name: str
    role: str  # "main" | "guardrail" | "secondary"
    refused: str | None = None  # non-None ⇒ SKIPPED with this reason (unsizable/no baseline)
    kind: str | None = None
    test_type: str | None = None
    alpha: float | None = None
    power: float | None = None
    baseline: BaselineMoments | None = None
    target_mde: float | None = None
    plan_ratio: float = 1.0  # treatment:control allocation used for required-N
    result: SizingResult | None = None
    runtime: RuntimePlan | None = None  # WP-A timing (days-to-N + ASN); None ⇒ no rate
    notes: list[str] = field(default_factory=list)


def size_comparison(
    moments: BaselineMoments,
    *,
    test_type: str,
    alpha: float,
    power: float,
    target_mde: float | None,
    plan_ratio: float,
) -> SizingResult:
    """The three sizing solves for one comparison — a thin, pure wrapper over ``power.py``.

    ``target_mde`` is in the units of this comparison's effect (``relative`` ⇒ a
    fraction such as ``0.05``; ``absolute`` ⇒ an absolute delta) and is sized on its
    magnitude (direction is a read-time concern, not a sizing one — the solves use
    ``abs``). ``plan_ratio`` = treatment:control allocation for the forward-looking
    required-N (from the experiment's ``expected_split``); the retrospective bounds use
    the observed ratio baked into ``moments``.
    """
    magnitude = abs(target_mde) if target_mde is not None else None
    obs_ratio = moments.observed_ratio
    n = int(round(moments.n))

    if moments.kind == FRACTION:
        prop = moments.baseline
        # An infeasible target — one that lifts the proportion out of (0, 1) — has no
        # finite N (proportion_effectsize is NaN there and the solve would raise). Report
        # it as ∞ ("underpowered"), which is_powered/_fmt_n already handle, rather than
        # crashing the whole experiment's plan. power.py stays untouched (golden-pinned).
        feasible = magnitude is not None and _fraction_target_feasible(prop, magnitude, test_type)
        if magnitude is None:
            required: int | float | None = None
        elif feasible:
            required = get_fraction_sample_size(
                prop, magnitude, test_type=test_type, alpha=alpha, power=power, ratio=plan_ratio
            )
        else:
            required = float("inf")
        achievable = (
            get_fraction_mde(
                prop, n, test_type=test_type, alpha=alpha, power=power, ratio=obs_ratio
            )
            if n > 1
            else float("inf")
        )
        achieved = (
            get_fraction_power(
                prop, n, magnitude, test_type=test_type, alpha=alpha, ratio=obs_ratio
            )
            if (feasible and n > 1)
            else None
        )
        return SizingResult(required, achievable, achieved)

    # SAMPLE (t-test / CUPED on the raw std)
    assert moments.std is not None
    mean, std = moments.baseline, moments.std
    # A zero standardized effect (a relative MDE on a zero baseline mean) has no finite N
    # — statsmodels raises "Cannot detect an effect-size of 0". Report ∞ instead of
    # aborting the plan (mirrors get_ttest_mde's own size≤1/std=0 → inf convention).
    if magnitude is None:
        required = None
    elif _sample_effect_size(mean, std, magnitude, test_type) == 0.0:
        required = float("inf")
    else:
        required = get_ttest_sample_size(
            mean, std, magnitude, test_type=test_type, alpha=alpha, power=power, ratio=plan_ratio
        )
    achievable = get_ttest_mde(
        mean, std, n, test_type=test_type, alpha=alpha, power=power, ratio=obs_ratio
    )
    # get_ttest_power tolerates a zero effect (power → alpha), so no guard is needed here.
    achieved = (
        get_ttest_power(mean, std, n, magnitude, test_type=test_type, alpha=alpha, ratio=obs_ratio)
        if magnitude is not None
        else None
    )
    return SizingResult(required, achievable, achieved)


def _fraction_target_feasible(prop: float, magnitude: float, test_type: str) -> bool:
    """A proportion target is sizable only if it keeps ``prop ± delta`` inside (0, 1)."""
    delta = prop * magnitude if test_type == "relative" else magnitude
    return 0.0 < prop + delta < 1.0


def _sample_effect_size(mean: float, std: float, magnitude: float, test_type: str) -> float:
    """The standardized effect size the required-N solve would use (0 ⇒ no finite N)."""
    adjusted = mean * (1.0 + magnitude) if test_type == "relative" else mean + magnitude
    return abs(adjusted - mean) / std


# ── runtime + ASN (WP-A) ─────────────────────────────────────────────────────────

#: default Monte-Carlo trajectory count for the ASN estimate — large enough that the
#: rounded control-arm N is stable run-to-run (a fixed seed makes each run identical).
ASN_TRAJECTORIES = 20_000
#: fixed seed: the planner is a deterministic read-only tool, so ASN must not wobble
#: between two `abk plan` invocations on the same inputs (mirrors the A/A derive_seed
#: discipline — no wall-clock entropy).
ASN_SEED = 20240706


@dataclass(frozen=True)
class AsnResult:
    """The always-valid sequential design's average sample number (control-arm units).

    ``asn_n_h1`` / ``asn_days_h1`` — expected stopping size / duration under the true
    target effect (the headline: "you'll usually conclude by here"). ``prob_win_by_horizon``
    is the sequential design's projected power (share of trajectories that cross the CS
    boundary by the planned horizon). ``asn_n_h0`` is the expected size under the null
    (≈ the horizon — a true null rarely crosses, so a sequential run mostly runs long).
    All are Monte-Carlo estimates over the canonical information-time process, capped at
    the planned horizon (a design that never crosses stops at the horizon by construction).
    """

    asn_n_h1: float
    asn_days_h1: float
    prob_win_by_horizon: float
    asn_n_h0: float
    asn_days_h0: float
    horizon_n: float
    horizon_days: float


@dataclass(frozen=True)
class RuntimePlan:
    """The timing companion to a :class:`SizingResult` for one comparison.

    ``rate_control_per_day`` is ``None`` ⇒ runtime SKIPPED (``rate_source`` holds the
    reason — no arrival data). ``days_to_required_n`` is ``required_n / rate`` (``inf``
    when the target is unachievable, ``None`` when there is no target). ``horizon_days``
    is the config's planned calendar length (context). ``asn`` is present only for a
    sequential-eligible ``sequential.enabled`` design; when it is ``None`` the
    ``asn_note`` says why ASN is n/a (fixed-horizon design / not sequential-eligible).
    """

    rate_control_per_day: float | None
    rate_source: str
    days_to_required_n: float | None
    horizon_days: float | None
    asn: AsnResult | None = None
    asn_note: str | None = None
    #: ASN_H1 < the fixed required-N — the horizon-capped/underpowered regime, where the
    #: expected-stop can dip below the requirement; NOT a smaller sample requirement.
    asn_below_required: bool = False


def runtime_for(required_n: int | float | None, rate_control_per_day: float) -> float | None:
    """Days to accrue ``required_n`` control-arm units at ``rate_control_per_day``.

    ``None`` when there is no target (``required_n is None``) or no usable rate;
    ``inf`` when the target itself is unachievable (``required_n`` is ``inf``).
    A pure division — the honest days-to-N once an arrival rate exists.
    """
    if required_n is None or rate_control_per_day <= 0.0:
        return None
    if not math.isfinite(required_n):
        return float("inf")
    return required_n / rate_control_per_day


def _cs_radius(variance: float, tau2: float, alpha: float) -> float:
    """Half-width of the shipped always-valid CS at estimator ``variance`` (= SE²).

    Reuses :func:`abkit.stats.sequential.sequentialize` at ``effect=0`` (so the interval
    is ``±radius``) — the SAME boundary the pipeline and the A/A column cross, never a
    re-derivation. Returns NaN for a degenerate variance (propagated as no-crossing).
    """
    if not math.isfinite(variance) or variance <= 0.0:
        return float("nan")
    _lo, hi, _p = sequentialize(0.0, math.sqrt(variance), tau2, alpha)
    return hi


def _absolute_effect(moments: BaselineMoments, magnitude: float, test_type: str) -> float:
    """The target effect on the ABSOLUTE (difference) scale the ASN process lives on.

    The CS crossing ``|effect| > radius`` is scale-invariant (both sides carry the same
    1/baseline factor for a relative effect), so the ASN simulation runs on the absolute
    difference scale for every family: ``mean * magnitude`` (sample, relative) / ``p *
    magnitude`` (fraction, relative) / ``magnitude`` (absolute).
    """
    if test_type == "relative":
        return abs(moments.baseline) * magnitude
    return magnitude


def _base_variance(moments: BaselineMoments) -> float:
    """Per-unit variance of one arm: ``σ²`` (sample) or the null ``p(1−p)`` (fraction)."""
    if moments.kind == FRACTION:
        p = moments.baseline
        return p * (1.0 - p)
    assert moments.std is not None
    return moments.std * moments.std


def asn_for(
    moments: BaselineMoments,
    *,
    test_type: str,
    target_mde: float | None,
    alpha: float,
    plan_ratio: float,
    look_days: list[float],
    rate_control_per_day: float,
    n_trajectories: int = ASN_TRAJECTORIES,
    seed: int = ASN_SEED,
) -> AsnResult | None:
    """Average sample number for the always-valid sequential design, or ``None``.

    Simulates the canonical information-time Gaussian process of the per-look effect
    estimate (Jennison & Turnbull §11): with control-arm size ``n_k = rate * look_days[k]``
    the estimator variance is ``V_k = base_var*(1 + 1/ratio)/n_k`` and information
    ``I_k = 1/V_k``; the sufficient process ``S_k = I_k·effect_hat_k`` has independent
    Gaussian increments ``S_k − S_{k−1} ~ N(δ·ΔI_k, ΔI_k)``. A trajectory stops at the
    first look whose estimate leaves the shipped CS boundary (``_cs_radius`` — the exact
    interval the pipeline peeks), else at the horizon. ``δ`` is the true target effect
    for H1 and ``0`` for H0.

    τ² is anchored to the FIRST usable look's variance — the same anchor the live
    pipeline freezes (``mixture.py`` D-Seq-anchor) — so the simulated boundary is the
    one an actual run would peek.

    Returns ``None`` (⇒ "ASN n/a" upstream) when there is no sizable target, a degenerate
    baseline, or fewer than two usable looks (a sequential design needs ≥2 looks to peek).
    Deterministic given the fixed seed.
    """
    if target_mde is None:
        return None
    magnitude = abs(target_mde)
    delta = _absolute_effect(moments, magnitude, test_type)
    base_var = _base_variance(moments)
    if delta <= 0.0 or not math.isfinite(base_var) or base_var <= 0.0:
        return None
    if rate_control_per_day <= 0.0:
        return None

    var_factor = base_var * (1.0 + 1.0 / plan_ratio)
    # usable looks: strictly increasing days that accrue ≥1 control-arm unit (a finite SE)
    days: list[float] = []
    for d in look_days:
        if d > 0.0 and rate_control_per_day * d >= 1.0 and (not days or d > days[-1]):
            days.append(d)
    if len(days) < 2:
        return None

    n_arr = np.array([rate_control_per_day * d for d in days], dtype=float)
    v_arr = var_factor / n_arr  # V_k = base_var*(1+1/ratio)/n_k
    info = 1.0 / v_arr  # I_k, strictly increasing
    radius = np.array([_cs_radius(v, mixture_tau2(v_arr[0], alpha), alpha) for v in v_arr])
    if not np.all(np.isfinite(radius)):
        return None

    d_info = np.diff(info, prepend=0.0)  # ΔI_k, with I_0 = 0
    horizon_n = float(n_arr[-1])
    horizon_days = float(days[-1])

    def _mean_stop(true_delta: float, rng: np.random.Generator) -> tuple[float, float]:
        # S_k = Σ N(δ·ΔI, ΔI); effect_hat_k = S_k / I_k; stop at first |effect_hat|>radius
        z = rng.standard_normal((n_trajectories, len(days)))
        incr = true_delta * d_info + np.sqrt(d_info) * z
        s = np.cumsum(incr, axis=1)
        effect_hat = s / info
        crossed = np.abs(effect_hat) > radius
        any_cross = crossed.any(axis=1)
        first = np.argmax(crossed, axis=1)  # 0 when none crossed — overridden below
        stop_n = np.where(any_cross, n_arr[first], horizon_n)
        return float(stop_n.mean()), float(any_cross.mean())

    rng = np.random.default_rng(seed)
    asn_n_h1, prob_win = _mean_stop(delta, rng)
    asn_n_h0, _ = _mean_stop(0.0, rng)
    return AsnResult(
        asn_n_h1=asn_n_h1,
        asn_days_h1=asn_n_h1 / rate_control_per_day,
        prob_win_by_horizon=prob_win,
        asn_n_h0=asn_n_h0,
        asn_days_h0=asn_n_h0 / rate_control_per_day,
        horizon_n=horizon_n,
        horizon_days=horizon_days,
    )


def is_powered(plan: ComparisonPlan) -> bool | None:
    """True iff the current control-arm N meets required-N (None when either is absent)."""
    if plan.result is None or plan.result.required_n is None or plan.baseline is None:
        return None
    if not math.isfinite(plan.result.required_n):
        return False
    return moments_n_control(plan.baseline) >= plan.result.required_n


def moments_n_control(moments: BaselineMoments) -> float:
    return moments.n


def parse_baseline_overrides(specs: tuple[str, ...]) -> dict[str, dict[str, float]]:
    """Parse repeatable ``--baseline`` specs into ``{metric: {field: value}}``.

    Grammar: ``<metric>:<field>=<value>[,<field>=<value>...]`` — e.g.
    ``arpu:mean=12.5,std=8,n=5000`` (sample) or ``signup:prop=0.1,n=10000`` (fraction).
    Optional ``n_other`` overrides the treatment-arm size (defaults to ``n``). Raises
    :class:`ValueError` on a malformed spec so the CLI can exit non-zero with a hint.
    """
    known = {"mean", "std", "n", "n_other", "prop"}
    out: dict[str, dict[str, float]] = {}
    for spec in specs:
        if ":" not in spec:
            raise ValueError(
                f"--baseline '{spec}': expected '<metric>:<field>=<value>,...' "
                "(e.g. arpu:mean=12.5,std=8,n=5000)"
            )
        metric, _, body = spec.partition(":")
        metric = metric.strip()
        if not metric:
            raise ValueError(f"--baseline '{spec}': empty metric name")
        fields: dict[str, float] = {}
        for pair in body.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "=" not in pair:
                raise ValueError(f"--baseline '{spec}': '{pair}' is not '<field>=<value>'")
            key, _, raw = pair.partition("=")
            key = key.strip()
            if key not in known:
                raise ValueError(
                    f"--baseline '{spec}': unknown field '{key}' (allowed: {sorted(known)})"
                )
            try:
                fields[key] = float(raw)
            except ValueError as exc:
                raise ValueError(f"--baseline '{spec}': '{raw}' is not a number") from exc
        if not fields:
            raise ValueError(f"--baseline '{spec}': no fields given")
        out[metric] = fields
    return out


def moments_from_override(kind: str, fields: dict[str, float]) -> BaselineMoments:
    """Build :class:`BaselineMoments` from a parsed ``--baseline`` override.

    Raises :class:`ValueError` naming the missing/invalid field so the CLI reports it.
    """
    n = fields.get("n")
    if n is None or n <= 0:
        raise ValueError("--baseline needs a positive 'n'")
    n_other = fields.get("n_other", n)
    if n_other <= 0:
        raise ValueError("--baseline 'n_other' must be positive")
    if kind == FRACTION:
        prop = fields.get("prop", fields.get("mean"))
        if prop is None or not 0.0 < prop < 1.0:
            raise ValueError("--baseline for a fraction metric needs 'prop' in (0, 1)")
        return BaselineMoments(FRACTION, prop, n, n_other, None, "--baseline override")
    mean = fields.get("mean")
    std = fields.get("std")
    if mean is None or std is None or std <= 0:
        raise ValueError("--baseline for a sample metric needs 'mean' and a positive 'std'")
    return BaselineMoments(SAMPLE, mean, n, n_other, std, "--baseline override")
