"""Shared deterministic fixtures for the golden tests vs the transcribed legacy engine.

Every fixture builds its own ``np.random.default_rng`` with a pinned seed, so the
golden inputs are byte-stable across runs and machines (hygiene H1 — no global RNG).
The heavy-tailed arms are the quorum-required sparse-revenue fixture
(docs/specs/quorum-review.md "Golden tolerance = relative 1e-9 ... heavy-tailed
revenue fixture"): lognormal(sigma=2) with ~70% exact zeros.

The tolerance helpers are exposed as session fixtures so both golden test modules
share one ``math.isclose``-semantics implementation (rel 1e-9, abs floor 1e-12).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from abkit.stats.result import TestResult

NORMAL_SEED = 20260701
HEAVY_SEED = 20260702
PAIRED_NORMAL_SEED = 20260703
PAIRED_HEAVY_SEED = 20260704

#: (y1, x1, y2, x2) — per-arm values with a row-aligned covariate.
Arms = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]

#: Fields compared against the legacy reference whenever the legacy defines them.
RESULT_FIELDS = (
    "effect",
    "pvalue",
    "left_bound",
    "right_bound",
    "ci_length",
    "value_1",
    "value_2",
    "std_1",
    "std_2",
    "cov_value_1",
    "cov_value_2",
)

AssertRel = Callable[..., None]
AssertResultMatches = Callable[[TestResult, dict[str, Any]], None]


def _assert_rel(
    actual: float, expected: float, rel: float = 1e-9, abs_tol: float = 1e-12, what: str = "value"
) -> None:
    """``math.isclose`` semantics at the quorum tolerance; NaN only matches NaN."""
    if isinstance(expected, float) and math.isnan(expected):
        assert isinstance(actual, float) and math.isnan(actual), f"{what}: {actual!r} != NaN"
        return
    assert math.isclose(actual, expected, rel_tol=rel, abs_tol=abs_tol), (
        f"{what}: engine {actual!r} != legacy {expected!r} " f"(rel={rel}, abs_tol={abs_tol})"
    )


def _assert_result_matches(
    result: TestResult, legacy: dict[str, Any], rel: float = 1e-9, abs_tol: float = 1e-12
) -> None:
    """Compare a TestResult against a legacy-reference dict at relative 1e-9."""
    for field in RESULT_FIELDS:
        if field in legacy:
            _assert_rel(getattr(result, field), legacy[field], rel, abs_tol, what=field)
    if "reject" in legacy:
        assert result.reject == legacy["reject"], (
            f"reject: engine {result.reject} != legacy {legacy['reject']} "
            f"(pvalue {result.pvalue!r} vs {legacy['pvalue']!r})"
        )


@pytest.fixture(scope="session")
def assert_rel() -> AssertRel:
    return _assert_rel


@pytest.fixture(scope="session")
def assert_result_matches() -> AssertResultMatches:
    return _assert_result_matches


# --- independent continuous arms -----------------------------------------------------


def _normal_arm(
    rng: np.random.Generator, n: int, mean_shift: float
) -> tuple[np.ndarray, np.ndarray]:
    """Normal-ish metric with a corr≈0.7 covariate (y = x + noise, equal stds)."""
    covariate = rng.normal(loc=10.0, scale=2.0, size=n)
    values = covariate + rng.normal(loc=mean_shift, scale=2.0, size=n)
    return values, covariate


def _heavy_arm(rng: np.random.Generator, n: int, lift: float) -> tuple[np.ndarray, np.ndarray]:
    """Sparse-revenue arm: lognormal(sigma=2) with ~70% exact zeros (shared mask),
    covariate = the correlated heavy-tailed pre-period value."""
    zero_mask = rng.random(n) < 0.7
    pre = np.where(zero_mask, 0.0, rng.lognormal(mean=0.0, sigma=2.0, size=n))
    noise = np.where(zero_mask, 0.0, rng.lognormal(mean=0.0, sigma=1.0, size=n))
    values = lift * (0.8 * pre + noise)
    return values, pre


@pytest.fixture(scope="session")
def normal_arms() -> Arms:
    rng = np.random.default_rng(NORMAL_SEED)
    y1, x1 = _normal_arm(rng, n=4000, mean_shift=0.0)
    y2, x2 = _normal_arm(rng, n=5000, mean_shift=0.12)
    return y1, x1, y2, x2


@pytest.fixture(scope="session")
def heavy_arms() -> Arms:
    rng = np.random.default_rng(HEAVY_SEED)
    y1, x1 = _heavy_arm(rng, n=6000, lift=1.0)
    y2, x2 = _heavy_arm(rng, n=6400, lift=1.06)
    return y1, x1, y2, x2


@pytest.fixture(params=("normal", "heavy-tailed"))
def continuous_arms(request: pytest.FixtureRequest, normal_arms: Arms, heavy_arms: Arms) -> Arms:
    return normal_arms if request.param == "normal" else heavy_arms


# --- paired (equal-size, position-aligned) arms ---------------------------------------


@pytest.fixture(scope="session")
def paired_normal_arms() -> Arms:
    rng = np.random.default_rng(PAIRED_NORMAL_SEED)
    n = 3000
    base = rng.normal(loc=20.0, scale=4.0, size=n)
    noise_1 = rng.normal(loc=0.0, scale=2.0, size=n)
    noise_2 = rng.normal(loc=0.15, scale=2.0, size=n)
    y1 = base + noise_1
    y2 = base + noise_2
    # Covariates share both the pair base and the per-arm noise, so the paired-CUPED
    # θ (on differences) is well away from zero and per-arm corr(y, x) > 0.5.
    x1 = 0.7 * base + 0.5 * noise_1 + rng.normal(loc=0.0, scale=1.0, size=n)
    x2 = 0.7 * base + 0.5 * noise_2 + rng.normal(loc=0.0, scale=1.0, size=n)
    return y1, x1, y2, x2


@pytest.fixture(scope="session")
def paired_heavy_arms() -> Arms:
    rng = np.random.default_rng(PAIRED_HEAVY_SEED)
    n = 4000
    zero_mask = rng.random(n) < 0.7
    latent = np.where(zero_mask, 0.0, rng.lognormal(mean=0.0, sigma=2.0, size=n))
    y1 = latent + np.where(zero_mask, 0.0, rng.lognormal(mean=0.0, sigma=1.0, size=n))
    y2 = 1.05 * latent + np.where(zero_mask, 0.0, rng.lognormal(mean=0.0, sigma=1.0, size=n))
    x1 = 0.6 * y1 + np.where(zero_mask, 0.0, rng.lognormal(mean=0.0, sigma=1.0, size=n))
    x2 = 0.6 * y2 + np.where(zero_mask, 0.0, rng.lognormal(mean=0.0, sigma=1.0, size=n))
    return y1, x1, y2, x2


@pytest.fixture(params=("normal", "heavy-tailed"))
def paired_arms(
    request: pytest.FixtureRequest, paired_normal_arms: Arms, paired_heavy_arms: Arms
) -> Arms:
    return paired_normal_arms if request.param == "normal" else paired_heavy_arms


# --- proportions ----------------------------------------------------------------------

#: (count_1, nobs_1, count_2, nobs_2) — mid, small, extreme-high and tiny proportions.
PROPORTION_CASES: tuple[tuple[float, float, float, float], ...] = (
    (450.0, 1000.0, 481.0, 1000.0),
    (30.0, 2400.0, 52.0, 2600.0),
    (1978.0, 2000.0, 1969.0, 2000.0),
    (6.0, 5000.0, 11.0, 5200.0),
)


@pytest.fixture(params=PROPORTION_CASES, ids=("mid", "small-prop", "extreme-high", "tiny-prop"))
def proportion_case(request: pytest.FixtureRequest) -> tuple[float, float, float, float]:
    return request.param
