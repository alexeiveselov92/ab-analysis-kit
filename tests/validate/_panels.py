"""Synthetic placebo panels with analytic ground truth for the WP1 scorer tests.

The values are drawn from a fixed local RNG so the FPR/power numbers are
reproducible; the tests assert them inside a Binomial(N, p) band, never at a
point (at N=2000, FPR=0.05 has σ≈0.0049).
"""

from __future__ import annotations

import numpy as np

from abkit.validate.panel import PanelCutoff, PlaceboPanel


def normal_panel(
    *,
    n_units: int,
    n_cutoffs: int,
    seed: int,
    mu: float = 10.0,
    sigma: float = 3.0,
    with_covariate: bool = False,
) -> PlaceboPanel:
    """A cumulative-random-walk A/A panel: unit value at cutoff k = Σ increments≤k.

    All units are present from the first cutoff; ``elapsed_days`` is ``1..n_cutoffs``;
    the last cutoff is the horizon. With ``with_covariate`` a correlated per-unit
    covariate is attached (fixed pre-period constant), exercising the CUPED path.
    """
    rng = np.random.default_rng(seed)
    increments = rng.normal(mu, sigma, size=(n_units, n_cutoffs))
    cumulative = np.cumsum(increments, axis=1)

    covariate = None
    if with_covariate:
        # a covariate correlated with the horizon total (CUPED's variance handle)
        noise = rng.normal(0.0, sigma, size=n_units)
        covariate = cumulative[:, -1] * 0.6 + noise

    unit_idx = np.arange(n_units)
    cutoffs = tuple(
        PanelCutoff(
            elapsed_days=float(k + 1),
            is_horizon=(k == n_cutoffs - 1),
            unit_idx=unit_idx,
            values=cumulative[:, k].copy(),
        )
        for k in range(n_cutoffs)
    )
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=cutoffs,
        covariate=covariate,
        input_kind="sample",
        kept_grid_points=n_cutoffs,
        total_grid_points=n_cutoffs,
    )


def fraction_panel(*, n_units: int, seed: int, base_rate: float = 0.2) -> PlaceboPanel:
    """A single-cutoff Bernoulli A/A panel (0/1 outcomes) for the z-test path."""
    rng = np.random.default_rng(seed)
    outcomes = (rng.random(n_units) < base_rate).astype(np.float64)
    unit_idx = np.arange(n_units)
    cutoff = PanelCutoff(elapsed_days=14.0, is_horizon=True, unit_idx=unit_idx, values=outcomes)
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=(cutoff,),
        covariate=None,
        input_kind="fraction",
        kept_grid_points=1,
        total_grid_points=1,
    )
