"""Mergeable sufficient statistics — the v2 incremental primitive.

Chan's parallel (Welford-family) update on centered moments: numerically stable,
never the catastrophic ``Σx²/n − x̄²`` form (docs/specs/statistics-changes.md §1.1).
In v1 this powers cross-day/stratum merges in memory; in v2 the warehouse
``_ab_unit_state`` seam reads reduce to exactly these merges.
"""

from __future__ import annotations

import numpy as np

from abkit.stats.exceptions import SampleValidationError
from abkit.stats.samples import JointMoments, RatioSufficientStats, SufficientStats


def _merged_name(name_a: str | None, name_b: str | None) -> str | None:
    return name_a if name_a == name_b else None


def _chan_terms(
    n_a: int, mean_a: float, n_b: int, mean_b: float
) -> tuple[int, float, float, float]:
    """Common Chan-update pieces: merged n & mean, the mean delta, and n_a·n_b/n."""
    n = n_a + n_b
    delta = mean_b - mean_a
    weight = n_a * n_b / n
    mean = mean_a + delta * n_b / n
    return n, mean, delta, weight


def merge_suffstats(a: SufficientStats, b: SufficientStats) -> SufficientStats:
    """Merge two disjoint per-unit populations of the same variant."""
    if a.has_covariate != b.has_covariate:
        raise SampleValidationError("cannot merge suffstats with and without covariate moments")
    n, mean, delta_y, weight = _chan_terms(a.n, a.mean, b.n, b.mean)
    m2 = a.m2 + b.m2 + delta_y**2 * weight
    if not a.has_covariate:
        return SufficientStats(n=n, mean=mean, m2=m2, name=_merged_name(a.name, b.name))

    assert a.cov_mean is not None and a.cov_m2 is not None and a.cross_c is not None
    assert b.cov_mean is not None and b.cov_m2 is not None and b.cross_c is not None
    _, cov_mean, delta_x, _ = _chan_terms(a.n, a.cov_mean, b.n, b.cov_mean)
    cov_m2 = a.cov_m2 + b.cov_m2 + delta_x**2 * weight
    cross_c = a.cross_c + b.cross_c + delta_y * delta_x * weight
    return SufficientStats(
        n=n,
        mean=mean,
        m2=m2,
        cov_mean=cov_mean,
        cov_m2=cov_m2,
        cross_c=cross_c,
        name=_merged_name(a.name, b.name),
    )


def merge_ratio_suffstats(a: RatioSufficientStats, b: RatioSufficientStats) -> RatioSufficientStats:
    n, mean_num, delta_num, weight = _chan_terms(a.n, a.mean_num, b.n, b.mean_num)
    _, mean_den, delta_den, _ = _chan_terms(a.n, a.mean_den, b.n, b.mean_den)
    return RatioSufficientStats(
        n=n,
        mean_num=mean_num,
        m2_num=a.m2_num + b.m2_num + delta_num**2 * weight,
        mean_den=mean_den,
        m2_den=a.m2_den + b.m2_den + delta_den**2 * weight,
        c_nd=a.c_nd + b.c_nd + delta_num * delta_den * weight,
        name=_merged_name(a.name, b.name),
    )


def merge_joint_moments(a: JointMoments, b: JointMoments) -> JointMoments:
    """Chan's update generalised to the full co-moment matrix."""
    if a.labels != b.labels:
        raise SampleValidationError(
            f"cannot merge JointMoments with labels {a.labels} != {b.labels}"
        )
    n = a.n + b.n
    delta = b.mean - a.mean
    weight = a.n * b.n / n
    mean = a.mean + delta * b.n / n
    comoment = a.comoment + b.comoment + np.outer(delta, delta) * weight
    return JointMoments(n=n, mean=mean, comoment=comoment, labels=a.labels)
