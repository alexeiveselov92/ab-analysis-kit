"""WP1 anytime-coverage simulation for the always-valid confidence sequence.

The defining property (D2): under the null the family of intervals covers the truth
SIMULTANEOUSLY at every look with probability >= 1 - alpha, so an analyst may peek at
every look and the miscoverage rate stays bounded by alpha. We verify this on an exact
Gaussian sequence (running mean of iid Normal(0,1)); the guarantee is asymptotic in
general, so this is a large-n Monte-Carlo check within a documented tolerance band, not
a finite-sample exact assertion. Because we sample a finite look grid, the measured
miscoverage is a lower bound on the continuous-time rate — it must sit comfortably under
alpha.
"""

from __future__ import annotations

import math

import numpy as np

from abkit.stats.sequential import mixture_tau2, sequentialize


def test_anytime_coverage_under_null_within_band() -> None:
    alpha = 0.05
    reps = 4000
    n_max = 1000
    grid = [25, 50, 100, 200, 400, 700, 1000]
    rng = np.random.default_rng(20260706)

    # tau^2 anchored to the horizon estimator variance (known sigma=1 -> SE_h^2 = 1/n_max).
    tau2 = mixture_tau2(reference_variance=1.0 / n_max, alpha=alpha)

    cum = np.cumsum(rng.standard_normal((reps, n_max)), axis=1)
    ever_excluded_zero = np.zeros(reps, dtype=bool)
    for n in grid:
        means = cum[:, n - 1] / n
        se = 1.0 / math.sqrt(n)  # known-variance SE at this look
        radius = _radius(se, tau2, alpha)
        lo = means - radius
        hi = means + radius
        ever_excluded_zero |= (lo > 0.0) | (hi < 0.0)

    miscoverage = float(ever_excluded_zero.mean())
    band = 3.0 * math.sqrt(alpha * (1.0 - alpha) / reps)  # ~0.010 MC slack at reps=4000
    assert (
        miscoverage <= alpha + band
    ), f"anytime miscoverage {miscoverage:.4f} > {alpha + band:.4f}"


def _radius(se: float, tau2: float, alpha: float) -> float:
    lo, hi, _ = sequentialize(0.0, se, tau2, alpha)
    return (hi - lo) / 2.0
