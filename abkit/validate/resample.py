"""Placebo A/A splits (docs/specs/m4-implementation-plan.md D1).

One iteration draws a single unit-level permutation, **held constant across the
whole cadence grid** (a unit's arm is fixed at enrollment — the real assignment
semantics). Permuting the pooled population destroys any true treatment effect,
so both arms are exchangeable draws from one distribution → an exact null by
construction (the standard permutation-A/A). Seeds are always derived
(``derive_seed``, rng.py:24–34) — never RNG-global, never wall-clock (D13).
"""

from __future__ import annotations

import numpy as np

from abkit.stats.rng import make_rng
from abkit.stats.samples import Fraction, RatioSample, RatioSufficientStats, Sample, SufficientStats

#: Minimum units per arm to build sufficient statistics (variance / covariance
#: need ≥2; below this the cutoff is a gap, never a zero — mirrors the readout's
#: demotion discipline).
MIN_ARM_UNITS = 2

ArmStats = SufficientStats | RatioSufficientStats | Fraction


def placebo_mask(n_units: int, share_a: float, seed: int) -> np.ndarray:
    """Boolean arm-A membership over global units ``[0, n_units)``.

    ``share_a`` is arm A's expected split share (e.g. 0.5). The count is rounded
    to the nearest unit; the permutation is drawn from a fresh local generator.
    """
    if n_units <= 0:
        raise ValueError(f"n_units must be positive, got {n_units}")
    if not 0.0 < share_a < 1.0:
        raise ValueError(f"share_a must be in (0, 1), got {share_a}")
    rng = make_rng(seed)
    perm = rng.permutation(n_units)
    n_a = int(round(n_units * share_a))
    n_a = min(max(n_a, 1), n_units - 1)  # keep both arms non-empty
    mask = np.zeros(n_units, dtype=bool)
    mask[perm[:n_a]] = True
    return mask


def present_positions(mask: np.ndarray, unit_idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split a cutoff's present units into (arm-A, arm-B) positions.

    ``unit_idx`` are the global indices present at the cutoff; the returned arrays
    index *into the cutoff's own value arrays* (``PanelCutoff.values``), so
    ``values[pos_a]`` are arm A's values at that cutoff.
    """
    arm_of_present = mask[unit_idx]
    pos_a = np.flatnonzero(arm_of_present)
    pos_b = np.flatnonzero(~arm_of_present)
    return pos_a, pos_b


def build_arm(
    input_kind: str,
    values: np.ndarray,
    secondary: np.ndarray | None,
    covariate: np.ndarray | None,
    unit_idx: np.ndarray,
    pos: np.ndarray,
    *,
    name: str | None = None,
) -> ArmStats | None:
    """Build one arm's sufficient statistics from its present-unit positions.

    Returns ``None`` when the arm is too small to score (``< MIN_ARM_UNITS``) — a
    gap, not a zero. ``covariate`` is indexed by *global* unit id
    (``unit_idx[pos]``) since it is a fixed per-unit constant; the value arrays are
    indexed by cutoff position (``pos``). ``secondary`` is the per-unit trials
    (fraction) or denominator (ratio).
    """
    if pos.size < MIN_ARM_UNITS:
        return None
    arm_values = values[pos]
    if input_kind == "fraction":
        if secondary is None:
            raise ValueError("fraction input_kind requires an nobs (trials) array")
        # per-unit (successes, trials) summed into the arm's proportion suffstats
        return Fraction(count=float(arm_values.sum()), nobs=float(secondary[pos].sum()), name=name)
    if input_kind == "ratio":
        if secondary is None:
            raise ValueError("ratio input_kind requires a denominator array")
        return RatioSufficientStats.from_ratio_sample(
            RatioSample(arm_values, secondary[pos], name=name)
        )
    cov = None if covariate is None else covariate[unit_idx[pos]]
    return SufficientStats.from_sample(Sample(arm_values, cov_array=cov, name=name))
