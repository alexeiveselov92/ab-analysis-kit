"""Shared deterministic fixtures for the ``abkit.stats`` foundation tests.

Every fixture builds its own seeded ``np.random.default_rng`` so tests are
order-independent and reproducible (hygiene H1, docs/specs/statistics-changes.md §2).
The heavy-tailed fixture is the quorum must-fix revenue fixture
(docs/specs/quorum-review.md "Golden tolerance = relative 1e-9"):
lognormal(sigma=2) with zeros mixed in, n ~ 10k.
"""

from __future__ import annotations

import numpy as np
import pytest

from abkit.stats.samples import Sample

HEAVY_TAILED_SEED = 20260702


@pytest.fixture()
def rng() -> np.random.Generator:
    """A deterministic generator for per-test randomisation (chunk splits etc.)."""
    return np.random.default_rng(12345)


@pytest.fixture()
def heavy_tailed_values() -> np.ndarray:
    """Revenue-like values: lognormal(sigma=2) with 20% zeros, n=10000 (shuffled)."""
    generator = np.random.default_rng(HEAVY_TAILED_SEED)
    values = np.concatenate([generator.lognormal(mean=0.0, sigma=2.0, size=8000), np.zeros(2000)])
    generator.shuffle(values)
    return values


@pytest.fixture()
def heavy_tailed_covariate(heavy_tailed_values: np.ndarray) -> np.ndarray:
    """A correlated heavy-tailed covariate (pre-period revenue analogue)."""
    generator = np.random.default_rng(HEAVY_TAILED_SEED + 1)
    noise = generator.lognormal(mean=0.0, sigma=2.0, size=heavy_tailed_values.size)
    return 0.6 * heavy_tailed_values + noise


@pytest.fixture()
def heavy_tailed_sample(
    heavy_tailed_values: np.ndarray, heavy_tailed_covariate: np.ndarray
) -> Sample:
    return Sample(heavy_tailed_values, cov_array=heavy_tailed_covariate, name="control")
