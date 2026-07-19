"""M7 WP7 — the scalar ↔ vectorized ``sweep_family`` parity gate.

The family sweep has its OWN hot loop (m7-implementation-plan.md §0.3(1) — the
WP4/WP5 scoring gate does not cover it), so it carries its own parity gate with
the WP5 discipline: many seeds × adversarially-chosen family shapes, EXACT
equality on every ``FamilyScore`` field. Unlike ``CellScore`` there is no
rel-1e-9 class at all: every column is a passthrough, an integer-count ratio,
or a sum of exact fractions accumulated in identical iteration order (the
per-iteration ``composed_significance`` stays the unchanged scalar helper in
both engines), so exactness is the honest expectation — a knife-edge ULP flip
of a single composed decision remains possible in principle (the WP5 boundary
finding), which is why breadth across seeds is doing the safety work here.

Both engines run in one process ⇒ one BLAS configuration (the D13 scope), so
the exact asserts are CI-safe.

Seed breadth is env-tunable via ``ABKIT_PARITY_SEEDS`` (default 50 per shape).
"""

from __future__ import annotations

import dataclasses
import os

import numpy as np
import pytest

import abkit.validate.family as family_mod
from abkit.stats.factory import create_method
from abkit.stats.parametric.ttest import TTest
from abkit.validate._types import ValidateError
from abkit.validate.family import (
    FamilyMember,
    FamilyScore,
    _sweep_family_scalar,
    sweep_family,
)
from abkit.validate.panel import PanelCutoff, PlaceboPanel
from tests.validate._panels import fraction_panel, normal_panel

#: Shared splits per (shape, seed) run — small on purpose; breadth over depth.
ITERATIONS = 20
#: Distinct seed_parts tuples per shape (the WP5 ≥50 floor).
N_SEEDS = int(os.environ.get("ABKIT_PARITY_SEEDS", "50"))


def test_every_familyscore_field_is_exact_classified():
    """Trip-wire: a future FamilyScore field cannot silently escape the gate —
    every field is asserted EXACT below; adding one forces a conscious look."""
    expected = {
        "correction",
        "n_metrics",
        "n_null_metrics",
        "planted_metrics",
        "iterations",
        "valid_iterations",
        "fwer",
        "fdr",
        "any_rejection_rate",
        "budget",
        "warnings",
        "fwer_peeking",
        "fdr_peeking",
        "any_rejection_rate_peeking",
        "fwer_sequential",
        "fdr_sequential",
        "any_rejection_rate_sequential",
    }
    assert {f.name for f in dataclasses.fields(FamilyScore)} == expected


def _assert_family_parity(vec: FamilyScore, sca: FamilyScore, ctx: str) -> None:
    for f in dataclasses.fields(FamilyScore):
        assert getattr(vec, f.name) == getattr(sca, f.name), f"{ctx}: field {f.name}"


# ── Family builders (union-cohort semantics via unit_offset / unit_ids) ───────


def _ratio_panel_with_ids(
    *, n_units: int, n_cutoffs: int, seed: int, unit_offset: int
) -> PlaceboPanel:
    """A cumulative ratio panel carrying unit_ids (the family union requires them)."""
    rng = np.random.default_rng(seed)
    den_inc = rng.uniform(1.0, 3.0, size=(n_units, n_cutoffs))
    num_inc = den_inc * 0.3 + rng.normal(0.0, 0.5, size=(n_units, n_cutoffs))
    den = np.cumsum(den_inc, axis=1)
    num = np.cumsum(num_inc, axis=1)
    unit_idx = np.arange(n_units)
    cutoffs = tuple(
        PanelCutoff(
            elapsed_days=float(k + 1),
            is_horizon=(k == n_cutoffs - 1),
            unit_idx=unit_idx,
            values=num[:, k].copy(),
            secondary=den[:, k].copy(),
        )
        for k in range(n_cutoffs)
    )
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=cutoffs,
        covariate=None,
        input_kind="ratio",
        kept_grid_points=n_cutoffs,
        total_grid_points=n_cutoffs,
        unit_ids=np.array([f"u{unit_offset + i}" for i in range(n_units)], dtype=object),
    )


def _tiny_panel(*, n_units: int, seed: int, unit_offset: int) -> PlaceboPanel:
    """A cohort too small to ever split ≥2 units/arm — the persistent-gap member
    (the M5 exit-gate 'scored in 0 iterations' disclosure)."""
    rng = np.random.default_rng(seed)
    cutoff = PanelCutoff(
        elapsed_days=7.0,
        is_horizon=True,
        unit_idx=np.arange(n_units),
        values=rng.normal(10.0, 3.0, size=n_units),
    )
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=(cutoff,),
        covariate=None,
        input_kind="sample",
        kept_grid_points=1,
        total_grid_points=1,
        unit_ids=np.array([f"u{unit_offset + i}" for i in range(n_units)], dtype=object),
    )


def _members_mixed() -> list[FamilyMember]:
    """Three kinds, overlapping cohorts (offsets 0/80/150) — the D11 union stress."""
    return [
        FamilyMember(
            "revenue",
            normal_panel(n_units=200, n_cutoffs=5, seed=601),
            create_method("t-test", alpha=0.1),
            0.1,
        ),
        FamilyMember(
            "engagement",
            normal_panel(n_units=160, n_cutoffs=5, seed=602, with_covariate=True, unit_offset=80),
            create_method("cuped-t-test", alpha=0.1),
            0.1,
        ),
        FamilyMember(
            "conversion",
            fraction_panel(n_units=300, seed=603, unit_offset=150),
            create_method("z-test", alpha=0.1),
            0.1,
            True,  # planted — injected seeds exercise D12 (null members stay controlled)
        ),
    ]


def _members_ratio_disjoint() -> list[FamilyMember]:
    """A ratio member + a sample member over DISJOINT cohorts (each unit in one)."""
    return [
        FamilyMember(
            "rpo",
            _ratio_panel_with_ids(n_units=180, n_cutoffs=4, seed=604, unit_offset=0),
            create_method("ratio-delta", alpha=0.1),
            0.1,
        ),
        FamilyMember(
            "spend",
            normal_panel(n_units=180, n_cutoffs=4, seed=605, unit_offset=1000),
            create_method("t-test", alpha=0.1),
            0.1,
            True,
        ),
    ]


def _members_sparse() -> list[FamilyMember]:
    """A persistent-gap member (3 units — never splits ≥2/arm) beside a healthy
    null and a healthy planted member: the 'scored in 0 iterations' warning +
    the τ²-unanchorable seq gap, both pinned, while false rejections stay
    possible through the healthy null member (the honesty tallies need them)."""
    return [
        FamilyMember(
            "micro",
            _tiny_panel(n_units=3, seed=606, unit_offset=0),
            create_method("t-test", alpha=0.1),
            0.1,
        ),
        FamilyMember(
            "baseline",
            normal_panel(n_units=200, n_cutoffs=4, seed=612, unit_offset=50),
            create_method("t-test", alpha=0.1),
            0.1,
        ),
        FamilyMember(
            "healthy",
            normal_panel(n_units=200, n_cutoffs=4, seed=607, unit_offset=300),
            create_method("t-test", alpha=0.1),
            0.1,
            True,
        ),
    ]


def _members_clamp() -> list[FamilyMember]:
    """A planted high-rate fraction member (0.9 × δ=0.5 saturates) — pins the
    one-shot clamp warning's presence, text and ORDER between the engines."""
    return [
        FamilyMember(
            "highrate",
            fraction_panel(n_units=300, seed=608, base_rate=0.9),
            create_method("z-test", alpha=0.1),
            0.1,
            True,
        ),
        FamilyMember(
            "null",
            normal_panel(n_units=200, n_cutoffs=4, seed=609, unit_offset=400),
            create_method("t-test", alpha=0.1),
            0.1,
        ),
    ]


#: shape → (members builder, correction, sequential, inject δ for odd seeds)
_FAMILIES = {
    "mixed_bonferroni": (_members_mixed, "bonferroni", True, 0.15),
    "mixed_bh": (_members_mixed, "benjamini_hochberg", True, 0.15),
    "ratio_disjoint": (_members_ratio_disjoint, "bonferroni", True, 0.12),
    "sparse_member": (_members_sparse, "benjamini_hochberg", True, 0.2),
    "clamp_planted": (_members_clamp, "bonferroni", False, 0.5),
}


@pytest.mark.parametrize("shape", sorted(_FAMILIES))
def test_many_seed_family_parity(shape):
    """N_SEEDS distinct seed_parts per family shape, injection alternating by
    seed parity; every FamilyScore field must agree EXACTLY. Honesty tallies at
    the bottom prove the branches the shape exists for actually ran."""
    build, correction, sequential, delta = _FAMILIES[shape]
    members = build()
    any_fwer = any_planted_rej = any_peek = False
    for s in range(N_SEEDS):
        inject = delta if s % 2 else None
        kwargs = {
            "correction": correction,
            "iterations": ITERATIONS,
            "share_a": 0.5,
            "seed_parts": ("wp7-parity", shape, s),
            "inject_effect": inject,
            "budget": 0.1,
            "sequential": sequential,
        }
        vec = sweep_family(members, **kwargs)
        sca = _sweep_family_scalar(members, **kwargs)
        _assert_family_parity(vec, sca, ctx=f"shape={shape} seed={s} inject={inject}")
        any_fwer = any_fwer or bool(vec.fwer)
        any_planted_rej = any_planted_rej or (
            inject is not None and (vec.any_rejection_rate or 0) > (vec.fwer or 0)
        )
        any_peek = any_peek or vec.fwer_peeking is not None
        if shape == "sparse_member":
            assert any("scored in 0/" in w for w in vec.warnings), f"seed={s}"
        if shape == "clamp_planted" and inject is not None:
            assert any("saturates" in w for w in vec.warnings), f"seed={s}"

    # Fixture honesty — a dead shape would pass parity vacuously.
    assert any_fwer, f"{shape}: no seed ever produced a false family rejection"
    assert any_planted_rej, f"{shape}: planted member never rejected — dead injected branch"
    if sequential:
        assert any_peek, f"{shape}: peeking pair never populated — dead sequential branch"


# ── Cross-block accumulation parity ───────────────────────────────────────────


@pytest.mark.parametrize("shape", sorted(_FAMILIES))
@pytest.mark.parametrize("quantum", [1, 7])
def test_multi_block_family_parity(quantum, shape, monkeypatch):
    """Family unions are small, so the whole sweep normally streams as one
    block; shrinking the quantum forces the cross-block path (ragged final
    block: 20 = 2·7 + 6) — parity must hold under ANY deterministic partition,
    for EVERY shape (R1: clamp/τ²-gap/BH/ratio members each cross blocks too)."""
    monkeypatch.setattr(family_mod, "block_rows", lambda n_units: quantum)
    build, correction, sequential, delta = _FAMILIES[shape]
    members = build()
    for s in range(4):
        kwargs = {
            "correction": correction,
            "iterations": ITERATIONS,
            "share_a": 0.5,
            "seed_parts": ("wp7-blocks", shape, s),
            "inject_effect": delta if s % 2 else None,
            "budget": None,
            "sequential": sequential,
        }
        vec = sweep_family(members, **kwargs)
        sca = _sweep_family_scalar(members, **kwargs)
        _assert_family_parity(vec, sca, ctx=f"quantum={quantum} shape={shape} seed={s}")


# ── Boundary cardinality + pinned rare/divergence classes (R1) ────────────────


def test_single_member_family_parity():
    """Cardinality boundary between the tested empty case (ValidateError) and
    the ≥2-member shapes: a one-member family must agree exactly too."""
    members = [
        FamilyMember(
            "solo",
            normal_panel(n_units=200, n_cutoffs=4, seed=613),
            create_method("t-test", alpha=0.1),
            0.1,
        )
    ]
    for s in range(10):
        kwargs = {
            "correction": "bonferroni",
            "iterations": ITERATIONS,
            "share_a": 0.5,
            "seed_parts": ("wp7-solo", s),
            "inject_effect": 0.15 if s % 2 else None,
            "sequential": True,
        }
        vec = sweep_family(members, **kwargs)
        sca = _sweep_family_scalar(members, **kwargs)
        _assert_family_parity(vec, sca, ctx=f"solo seed={s}")


def _overcounted_fraction_panel() -> PlaceboPanel:
    """The WP5 over-count construction (12 corrupt units, successes > trials by
    0.6, against 18 healthy with slack 0.8) with unit_ids for the family union."""
    n_units = 30
    trials = np.full(n_units, 4.0)
    successes = np.full(n_units, 3.2)
    successes[:12] = 4.6
    cutoff = PanelCutoff(
        elapsed_days=7.0,
        is_horizon=True,
        unit_idx=np.arange(n_units),
        values=successes,
        secondary=trials,
    )
    return PlaceboPanel(
        n_units=n_units,
        cutoffs=(cutoff,),
        covariate=None,
        input_kind="fraction",
        kept_grid_points=1,
        total_grid_points=1,
        unit_ids=np.array([f"u{i}" for i in range(n_units)], dtype=object),
    )


#: Scanned seeds where ≥1 iteration's arm aggregate over-counts (count > nobs).
_OVERCOUNT_FAMILY_SEEDS = (0, 1, 2)


@pytest.mark.parametrize("s", _OVERCOUNT_FAMILY_SEEDS)
def test_overcounted_fraction_family_divergence_is_pinned(s):
    """The documented corrupt-input divergence, family edition (R1 finding #1;
    the same class the WP5 gate pinned for score_cell): a fraction arm whose
    aggregate successes exceed its trials CRASHES the scalar engine at
    ``Fraction`` construction (build_arm sits OUTSIDE `_member_marginal`'s
    try), while the batch engine scores the family (the pooled proportion can
    stay finite). Pinned so it remains a conscious, spec-documented decision
    (aa-false-positive-matrix.md §9) — hardening the batch fraction validity
    flag stays the named follow-up shared with score_cell."""
    from abkit.stats.exceptions import SampleValidationError

    members = [
        FamilyMember(
            "corrupt", _overcounted_fraction_panel(), create_method("z-test", alpha=0.1), 0.1
        ),
        FamilyMember(
            "healthy",
            normal_panel(n_units=100, n_cutoffs=2, seed=620, unit_offset=100),
            create_method("t-test", alpha=0.1),
            0.1,
        ),
    ]
    kwargs = {
        "correction": "bonferroni",
        "iterations": 25,
        "share_a": 0.5,
        "seed_parts": ("wp7-overcount", s),
        "sequential": False,
    }
    vec = sweep_family(members, **kwargs)
    assert vec.valid_iterations == 25  # the batch engine scores it...
    with pytest.raises(SampleValidationError):  # ...where the scalar fails loudly
        _sweep_family_scalar(members, **kwargs)


def test_raising_kernel_gaps_the_member_instead_of_crashing():
    """R1 finding #2: a structural kernel raise (a programmatically-built
    member whose method demands columns the panel lacks — CUPED without a
    covariate) must gap the member exactly like the scalar engine's
    ``except Exception`` net, never crash the sweep. Full-field parity incl.
    the never-scored disclosure."""
    members = [
        FamilyMember(
            "miscfg",
            normal_panel(n_units=100, n_cutoffs=2, seed=621),  # NO covariate
            create_method("cuped-t-test", alpha=0.1),
            0.1,
        ),
        FamilyMember(
            "ok",
            normal_panel(n_units=100, n_cutoffs=2, seed=622, unit_offset=200),
            create_method("t-test", alpha=0.1),
            0.1,
        ),
    ]
    kwargs = {
        "correction": "bonferroni",
        "iterations": 8,
        "share_a": 0.5,
        "seed_parts": ("wp7-miscfg",),
        "sequential": False,
    }
    vec = sweep_family(members, **kwargs)
    sca = _sweep_family_scalar(members, **kwargs)
    _assert_family_parity(vec, sca, ctx="miscfg")
    assert any("scored in 0/" in w for w in vec.warnings)


def test_raising_kernel_in_peek_walk_gaps_the_look():
    """R2: the peeking walk's own exception net, exercised. A covariate-less
    CUPED member on a TINY cohort reaches it: the τ² anchor degenerates at
    every cutoff (3 units never split ≥2/arm) and returns None WITHOUT calling
    the kernel — so the shared `_cell_tau2` does not crash the sweep — while
    the healthy member anchors, `peek_active` is True, and the walk then hits
    the miscfg member's structural kernel raise at every look (the scalar
    engine never even reaches `from_suffstats` there: its per-look `build_arm`
    returns None first). Both engines must agree field-for-field. A member
    whose anchor itself raises (a big misconfigured cohort under
    sequential=True) crashes BOTH engines identically inside `_cell_tau2` —
    pre-existing, symmetric, caught by the runner's family isolation."""
    members = [
        FamilyMember(
            "miscfg_tiny",
            _tiny_panel(n_units=3, seed=623, unit_offset=0),  # NO covariate
            create_method("cuped-t-test", alpha=0.1),
            0.1,
        ),
        FamilyMember(
            "ok",
            normal_panel(n_units=200, n_cutoffs=4, seed=624, unit_offset=100),
            create_method("t-test", alpha=0.1),
            0.1,
        ),
    ]
    for s in range(5):
        kwargs = {
            "correction": "bonferroni",
            "iterations": ITERATIONS,
            "share_a": 0.5,
            "seed_parts": ("wp7-walkraise", s),
            "sequential": True,
        }
        vec = sweep_family(members, **kwargs)
        sca = _sweep_family_scalar(members, **kwargs)
        _assert_family_parity(vec, sca, ctx=f"walkraise seed={s}")
        assert vec.fwer_peeking is not None  # peek_active really engaged
        assert any("scored in 0/" in w for w in vec.warnings)


# ── Dispatch contract ─────────────────────────────────────────────────────────


class _ScalarOnlyTTest(TTest):
    """The registered t-test math with the batch opt-in turned off — the
    stand-in for any plugin that only implements ``from_suffstats``."""

    supports_vectorized = False


class _KernellessTTest(TTest):
    """A lying plugin: declares the capability, has no working kernel."""

    supports_vectorized = True

    def from_suffstats_array(self, arrays_1, arrays_2):  # noqa: ARG002
        raise NotImplementedError


def _two_member_family(method_factory) -> list[FamilyMember]:
    return [
        FamilyMember("a", normal_panel(n_units=150, n_cutoffs=3, seed=610), method_factory(), 0.1),
        FamilyMember(
            "b",
            normal_panel(n_units=150, n_cutoffs=3, seed=611, unit_offset=75),
            create_method("t-test", alpha=0.1),
            0.1,
        ),
    ]


_KW = {
    "correction": "bonferroni",
    "iterations": 10,
    "share_a": 0.5,
    "seed_parts": ("wp7-dispatch",),
    "sequential": True,
}


def test_all_vectorized_family_never_enters_scalar(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("the scalar engine must not run for an all-vectorized family")

    monkeypatch.setattr(family_mod, "_sweep_family_scalar", _boom)
    score = sweep_family(_two_member_family(lambda: create_method("t-test", alpha=0.1)), **_KW)
    assert score.valid_iterations > 0


def test_mixed_family_falls_back_to_scalar(monkeypatch):
    """ONE non-opted-in member routes the WHOLE family through the scalar
    engine — the shared-assignment semantics are per-family, never per-member."""

    def _boom(*args, **kwargs):
        raise AssertionError("the vectorized engine must not run for a mixed family")

    monkeypatch.setattr(family_mod, "_sweep_family_vectorized", _boom)
    members = _two_member_family(lambda: _ScalarOnlyTTest(alpha=0.1))
    score = sweep_family(members, **_KW)
    assert score.valid_iterations > 0


def test_lying_vectorized_flag_fails_the_sweep_loudly():
    members = _two_member_family(lambda: _KernellessTTest(alpha=0.1))
    with pytest.raises(ValidateError, match="supports_vectorized=True"):
        sweep_family(members, **_KW)


def test_empty_family_still_raises_validate_error():
    with pytest.raises(ValidateError, match="at least one member"):
        sweep_family([], **_KW)
