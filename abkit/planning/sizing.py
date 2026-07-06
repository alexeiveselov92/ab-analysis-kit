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
caller flags. runtime / ASN (days-to-N from an arrival rate + the sequential design's
average sample number) are deferred to M6.

Pure: numpy + ``abkit.stats.power`` + stdlib only — no config types, no DB. The CLI
command reads the persisted moments and the effective two-tier alpha and translates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from abkit.stats.power import (
    get_fraction_mde,
    get_fraction_power,
    get_fraction_sample_size,
    get_ttest_mde,
    get_ttest_power,
    get_ttest_sample_size,
)

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
