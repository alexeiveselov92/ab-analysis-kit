"""Tests for the randomness policy (hygiene H1/H2, docs/specs/statistics-changes.md §2).

``derive_seed`` is the deterministic per-row seed mapping; it is pinned by a
known-answer computed independently (hashlib over the documented payload) so it
can never silently change — that would re-draw every published bootstrap CI.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from abkit.stats.rng import derive_seed, make_rng

_PINNED_PARTS = ("exp", "gross_usd", "control", "treatment", "2026-01-31", 1000)
#: hand-derived: int.from_bytes(sha256("exp\x1fgross_usd\x1f...\x1f1000")[:8], "big") >> 1
_PINNED_SEED = 7953211253123758138


def test_derive_seed_deterministic() -> None:
    assert derive_seed(*_PINNED_PARTS) == derive_seed(*_PINNED_PARTS)
    assert derive_seed("a", "b") == derive_seed("a", "b")


def test_derive_seed_pinned_known_answer() -> None:
    """Recompute the mapping by hand: str parts joined by \\x1f, sha256, top 63 bits."""
    payload = "\x1f".join(str(part) for part in _PINNED_PARTS)
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    expected = int.from_bytes(digest[:8], "big") >> 1
    assert expected == _PINNED_SEED  # pins the mapping forever
    assert derive_seed(*_PINNED_PARTS) == _PINNED_SEED


def test_derive_seed_part_boundaries_matter() -> None:
    assert derive_seed("ab", "c") != derive_seed("a", "bc")


def test_derive_seed_order_matters() -> None:
    assert derive_seed("a", "b") != derive_seed("b", "a")


def test_derive_seed_int_and_str_parts_equivalent() -> None:
    """Parts are joined via ``str()`` — 1000 and "1000" are the same part."""
    assert derive_seed("exp", 1000) == derive_seed("exp", "1000")


@pytest.mark.parametrize(
    "parts",
    [
        (),
        ("",),
        ("exp", "metric", "c", "t", "2026-01-31", 1000),
        ("другой", "эксперимент"),  # non-ASCII goes through UTF-8
        (0,),
    ],
)
def test_derive_seed_range_is_63_bit_non_negative(parts: tuple[object, ...]) -> None:
    seed = derive_seed(*parts)
    assert 0 <= seed < 2**63


def test_make_rng_same_seed_same_stream() -> None:
    values_a = make_rng(5).random(16)
    values_b = make_rng(5).random(16)
    np.testing.assert_array_equal(values_a, values_b)


def test_make_rng_different_seed_different_stream() -> None:
    assert not np.array_equal(make_rng(5).random(16), make_rng(6).random(16))


def test_make_rng_returns_local_generator_and_leaves_global_state_alone() -> None:
    """H1: never mutate global numpy state — drawing from make_rng must not touch it."""
    state_before = np.random.get_state()
    generator = make_rng(123)
    assert isinstance(generator, np.random.Generator)
    generator.normal(size=100)
    state_after = np.random.get_state()
    assert state_before[0] == state_after[0]
    np.testing.assert_array_equal(np.asarray(state_before[1]), np.asarray(state_after[1]))


def test_make_rng_accepts_derived_seed() -> None:
    seed = derive_seed("exp", "metric", "c", "t", "2026-01-31", 1000)
    values_a = make_rng(seed).random(8)
    values_b = make_rng(seed).random(8)
    np.testing.assert_array_equal(values_a, values_b)
