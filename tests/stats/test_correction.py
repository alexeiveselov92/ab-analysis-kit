"""Tests for multiple-testing corrections (baseline §6, declarative-config.md §6).

Bonferroni is the legacy transcription — including the exact legacy error message.
Benjamini-Hochberg is validated against statsmodels ``fdr_bh`` at relative 1e-12.
"""

from __future__ import annotations

import numpy as np
import pytest
from statsmodels.stats.multitest import multipletests

from abkit.stats.correction import (
    adjust_alpha,
    benjamini_hochberg,
    n_comparisons,
    two_tier_alphas,
)
from abkit.stats.exceptions import MethodParamError


def test_n_comparisons_values() -> None:
    assert n_comparisons(2, 1) == 1
    assert n_comparisons(3, 1) == 3
    assert n_comparisons(3, 2) == 6
    assert n_comparisons(4, 3) == 18


def test_adjust_alpha_legacy_values() -> None:
    assert adjust_alpha(0.05, 3, 2) == 0.05 / 6
    assert adjust_alpha(0.05, 2, 1) == 0.05
    assert adjust_alpha(0.1, 4, 2) == 0.1 / 12


def test_groups_error_message_is_exact_legacy_string() -> None:
    with pytest.raises(MethodParamError) as excinfo:
        adjust_alpha(0.05, 1)
    assert str(excinfo.value) == "Number of groups must be more than 1"
    with pytest.raises(MethodParamError) as excinfo:
        n_comparisons(0)
    assert str(excinfo.value) == "Number of groups must be more than 1"


def test_metrics_count_below_one_raises() -> None:
    with pytest.raises(MethodParamError, match="metrics_count"):
        adjust_alpha(0.05, 2, 0)
    with pytest.raises(MethodParamError, match="metrics_count"):
        n_comparisons(2, -1)


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.05, 2.0])
def test_adjust_alpha_bounds(alpha: float) -> None:
    with pytest.raises(MethodParamError, match="alpha"):
        adjust_alpha(alpha, 2)


def test_two_tier_alphas_fields() -> None:
    tiers = two_tier_alphas(alpha=0.05, groups_count=3, metrics_count=4)
    assert tiers.alpha == 0.05
    assert tiers.groups_count == 3
    assert tiers.metrics_count == 4
    assert tiers.main == 0.05 / 3  # main metric: C(3,2) comparisons, 1 metric
    assert tiers.secondary == 0.05 / 12  # secondary: C(3,2) × 4 metrics


# --- Benjamini-Hochberg ----------------------------------------------------------------


@pytest.mark.parametrize("size,seed", [(5, 0), (25, 1), (100, 2)])
def test_bh_matches_statsmodels_fdr_bh(size: int, seed: int) -> None:
    pvalues = np.random.default_rng(seed).uniform(size=size)
    ours = benjamini_hochberg(pvalues)
    theirs = multipletests(pvalues, method="fdr_bh")[1]
    np.testing.assert_allclose(ours, theirs, rtol=1e-12)


def test_bh_matches_statsmodels_with_ties_and_extremes() -> None:
    pvalues = np.array([0.01, 0.01, 0.5, 0.5, 0.02, 1.0, 0.0])
    ours = benjamini_hochberg(pvalues)
    theirs = multipletests(pvalues, method="fdr_bh")[1]
    np.testing.assert_allclose(ours, theirs, rtol=1e-12)


def test_bh_capped_at_one_and_monotone_in_rank() -> None:
    pvalues = np.array([0.2, 0.4, 0.6, 0.8, 0.99])
    adjusted = benjamini_hochberg(pvalues)
    assert np.all(adjusted <= 1.0)
    # adjusted p-values ordered by raw p must be non-decreasing (step-up monotonicity)
    order = np.argsort(pvalues)
    assert np.all(np.diff(adjusted[order]) >= 0)


def test_bh_input_validation() -> None:
    with pytest.raises(MethodParamError, match="non-empty 1-d"):
        benjamini_hochberg(np.array([]))
    with pytest.raises(MethodParamError, match="non-empty 1-d"):
        benjamini_hochberg(np.array([[0.1, 0.2]]))
    with pytest.raises(MethodParamError, match="within"):
        benjamini_hochberg(np.array([0.1, 1.2]))
    with pytest.raises(MethodParamError, match="within"):
        benjamini_hochberg(np.array([0.1, -0.2]))
    with pytest.raises(MethodParamError, match="finite"):
        benjamini_hochberg(np.array([0.1, float("nan")]))
