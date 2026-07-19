"""M7 WP2 parity: the array-wise confidence-sequence siblings vs the scalar path.

``se_from_ci_length_array``/``sequentialize_array``
(docs/specs/m7-implementation-plan.md §WP2) must reproduce the scalar
functions row-for-row. Parity classes mirror
``tests/stats/test_vectorized_parity.py``:

- ``se_from_ci_length_array`` is one division by a shared cached constant →
  **exact** on every platform;
- ``sequentialize_array`` evaluates ``log``/``exp`` through numpy ufuncs where
  the scalar uses libm via ``math`` — not guaranteed bit-identical across
  builds, so the committed bound is the repo golden tolerance (rel 1e-9);
  measured BYTE-IDENTICAL (max relative error 0.0) on the capture environment
  (2026-07-19). Unlike the delta-method variance sum (which forced the
  ``_libm_pow`` routing in effects.py), the radius/p-value formulas SUM only
  same-sign terms — a 1-ULP ``log``/``exp`` divergence stays ~1 ULP in the
  output, never amplified, so the tolerance is safe by structure. Degenerate
  rows (the scalar early-return triple) stay exact: NaN is NaN.
"""

from __future__ import annotations

import numpy as np
import pytest

from abkit.stats.sequential.confidence_sequence import (
    se_from_ci_length,
    se_from_ci_length_array,
    sequentialize,
    sequentialize_array,
)

SEED = 20260719
N_RANDOM = 500
RELATIVE_TOLERANCE = 1e-9

NAN = float("nan")
INF = float("inf")


def battery(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    effect = rng.normal(0.0, 1.0, N_RANDOM)
    se = np.abs(rng.normal(0.2, 0.1, N_RANDOM)) + 1e-6
    effect[0], se[0] = NAN, 0.1  # non-finite effect
    effect[1], se[1] = 0.5, 0.0  # zero SE (degenerate look)
    effect[2], se[2] = 0.5, -1.0  # negative SE (degenerate look)
    effect[3], se[3] = INF, 0.1  # non-finite effect
    effect[4], se[4] = 0.5, NAN  # non-finite SE
    effect[5], se[5] = 25.0, 0.001  # extreme z: deep-tail exp/log
    effect[6], se[6] = 0.0, 0.2  # exact-zero effect → p-value 1 branch
    effect[7], se[7] = 1e-12, 1e3  # inv_lambda ≥ 1 → the else-branch 1.0
    return effect, se


@pytest.mark.parametrize("alpha", [0.05, 0.001, 1e-8])
def test_se_from_ci_length_array_parity(alpha: float) -> None:
    rng = np.random.default_rng(SEED)
    ci_length = np.abs(rng.normal(0.5, 0.3, N_RANDOM))
    ci_length[0] = 0.0
    ci_length[1] = -0.5
    ci_length[2] = NAN
    ci_length[3] = INF
    got = se_from_ci_length_array(ci_length, alpha)
    want = np.array([se_from_ci_length(float(value), alpha) for value in ci_length])
    np.testing.assert_array_equal(got, want)  # division only → exact everywhere


@pytest.mark.parametrize(("tau2", "alpha"), [(0.01, 0.05), (1.0, 0.001), (1e-6, 0.2)])
def test_sequentialize_array_parity(tau2: float, alpha: float) -> None:
    effect, se = battery(np.random.default_rng(SEED + 1))
    lo_got, hi_got, pvalue_got = sequentialize_array(effect, se, tau2, alpha)
    scalar = [sequentialize(float(effect[i]), float(se[i]), tau2, alpha) for i in range(N_RANDOM)]
    for got, want in zip(
        (lo_got, hi_got, pvalue_got),
        (np.array(column) for column in zip(*scalar, strict=False)),
        strict=False,
    ):
        np.testing.assert_allclose(got, want, rtol=RELATIVE_TOLERANCE, atol=0.0, equal_nan=True)


def test_sequentialize_array_degenerate_rows_are_nan_triples() -> None:
    effect = np.array([NAN, 0.5, 0.5, INF, 0.5])
    se = np.array([0.1, 0.0, -1.0, 0.1, NAN])
    lo, hi, pvalue = sequentialize_array(effect, se, tau2=0.01, alpha=0.05)
    assert np.isnan(lo).all() and np.isnan(hi).all() and np.isnan(pvalue).all()


def test_sequentialize_array_contract_errors_match_scalar() -> None:
    effect, se = np.array([0.5]), np.array([0.1])
    with pytest.raises(ValueError, match="alpha must be in"):
        sequentialize_array(effect, se, tau2=0.01, alpha=1.5)
    with pytest.raises(ValueError, match="mixture variance"):
        sequentialize_array(effect, se, tau2=0.0, alpha=0.05)
    with pytest.raises(ValueError, match="mixture variance"):
        sequentialize_array(effect, se, tau2=NAN, alpha=0.05)
