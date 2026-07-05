"""Placebo split mechanics: determinism, shares, non-empty arms, gaps (m4 D1)."""

from __future__ import annotations

import numpy as np
import pytest

from abkit.stats.rng import derive_seed
from abkit.stats.samples import SufficientStats
from abkit.validate.resample import build_arm, placebo_mask, present_positions


def test_placebo_mask_is_deterministic_and_split_shares():
    seed = derive_seed("aa", "exp", "metric", "cfg", 7)
    a = placebo_mask(1000, 0.5, seed)
    b = placebo_mask(1000, 0.5, seed)
    assert np.array_equal(a, b)  # byte-identical for the same seed
    assert a.sum() == 500  # rounded to the share
    # a different iteration -> a different partition
    other = placebo_mask(1000, 0.5, derive_seed("aa", "exp", "metric", "cfg", 8))
    assert not np.array_equal(a, other)


def test_placebo_mask_keeps_both_arms_nonempty():
    mask = placebo_mask(10, 0.99, seed=1)
    assert 1 <= mask.sum() <= 9  # never all-A or all-B even at an extreme share


def test_present_positions_partitions_present_units():
    mask = np.array([True, False, True, False, True])
    unit_idx = np.array([0, 2, 4])  # a subset present at this cutoff
    pos_a, pos_b = present_positions(mask, unit_idx)
    # units 0, 2, 4 are all arm-A -> all positions in A, none in B
    assert list(pos_a) == [0, 1, 2]
    assert list(pos_b) == []


def test_build_arm_returns_none_on_tiny_arm():
    values = np.array([1.0, 2.0, 3.0])
    unit_idx = np.array([0, 1, 2])
    assert build_arm("sample", values, None, None, unit_idx, np.array([0])) is None  # 1 < MIN
    arm = build_arm("sample", values, None, None, unit_idx, np.array([0, 1, 2]))
    assert isinstance(arm, SufficientStats)
    assert arm.n == 3
    assert arm.mean == pytest.approx(2.0)
