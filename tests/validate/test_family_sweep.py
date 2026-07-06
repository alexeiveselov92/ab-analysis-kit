"""The composed multi-metric FWER/FDR family sweep (m5-implementation-plan.md WP8, D12).

Deterministic (fixed panels + derived seeds), asserted inside bands. Covers: the null
family-wise error ≈ nominal, the complete-null FWER==FDR identity, a planted effect
leaving the null metrics controlled while FDR stays low, the shared-mask union-cohort
handling, determinism, and the degenerate guards.
"""

from __future__ import annotations

import pytest

from abkit.config.method_config import MethodConfig
from abkit.validate._types import ValidateError
from abkit.validate.family import FamilyMember, sweep_family
from tests.validate._panels import normal_panel

ALPHA = 0.05


def _ttest(alpha: float):
    return MethodConfig(name="t-test", params={"test_type": "absolute"}).bind(alpha=alpha)


def _null_members(k: int, alpha: float):
    # k independent well-behaved metrics on the SAME cohort (one shared mask, distinct
    # values ⇒ ~independent tests) — the canonical family-error fixture.
    return [
        FamilyMember(
            metric=f"m{i}",
            panel=normal_panel(n_units=3000, n_cutoffs=1, seed=100 + i, mu=50.0, sigma=10.0),
            method=_ttest(alpha),
            alpha=alpha,
            planted=False,
        )
        for i in range(k)
    ]


# ── null family error ≈ nominal + the identity ──────────────────────────────────


def test_null_bonferroni_fwer_near_nominal_and_equals_fdr():
    members = _null_members(4, ALPHA / 4)  # Bonferroni: each at α/K ⇒ family FWER ≈ α
    s = sweep_family(
        members, correction="bonferroni", iterations=5000, share_a=0.5, seed_parts=("f",)
    )
    assert 0.035 < s.fwer < 0.068  # Binomial band around 0.05 at 5000 iterations
    assert s.fwer == pytest.approx(s.fdr, abs=1e-12)  # complete null ⇒ every rejection false
    assert s.any_rejection_rate == pytest.approx(s.fwer, abs=1e-12)
    assert s.valid_iterations == 5000
    assert s.n_null_metrics == 4 and s.planted_metrics == ()


def test_null_bh_controls_fdr_near_nominal():
    members = _null_members(4, ALPHA)  # BH members carry the RAW alpha
    s = sweep_family(
        members, correction="benjamini_hochberg", iterations=5000, share_a=0.5, seed_parts=("bh",)
    )
    assert 0.035 < s.fdr < 0.068
    assert s.fwer == pytest.approx(s.fdr, abs=1e-12)  # complete-null identity holds for BH too


# ── a planted effect leaves the null metrics controlled (D12) ────────────────────


def test_planted_effect_keeps_null_metrics_controlled_and_fdr_low():
    members = _null_members(4, ALPHA / 4)
    planted = [
        FamilyMember(
            members[0].metric, members[0].panel, members[0].method, members[0].alpha, planted=True
        )
    ]
    members = planted + members[1:]
    s = sweep_family(
        members,
        correction="bonferroni",
        iterations=5000,
        share_a=0.5,
        seed_parts=("p",),
        inject_effect=0.5,  # a strong true effect in m0's treatment arm
    )
    assert s.n_null_metrics == 3 and s.planted_metrics == ("m0",)
    assert s.any_rejection_rate > 0.95  # m0 (the true positive) essentially always rejects
    assert s.fwer < 0.07  # the 3 null metrics' family error stays controlled
    assert s.fdr < 0.04  # and FDR is low — the true positive dominates the denominator
    assert s.fdr < s.fwer  # the injected true positive pulls FDP below the false-only rate


# ── shared-mask union-cohort (D11: union universe, no imputation) ────────────────


def test_union_cohort_partial_overlap_scores_every_metric():
    # metric A owns u0..u2999, metric B owns u1500..u4499 — overlap u1500..u2999. One
    # shared assignment over the union; each metric scores only its own cohort.
    a = normal_panel(n_units=3000, n_cutoffs=1, seed=7, mu=50.0, sigma=10.0, unit_offset=0)
    b = normal_panel(n_units=3000, n_cutoffs=1, seed=8, mu=50.0, sigma=10.0, unit_offset=1500)
    members = [
        FamilyMember("a", a, _ttest(ALPHA / 2), ALPHA / 2),
        FamilyMember("b", b, _ttest(ALPHA / 2), ALPHA / 2),
    ]
    s = sweep_family(
        members, correction="bonferroni", iterations=3000, share_a=0.5, seed_parts=("u",)
    )
    assert s.valid_iterations == 3000  # every metric scorable under the shared union mask
    assert 0.02 < s.fwer < 0.08  # sane null family error on disjoint-ish cohorts


# ── determinism ──────────────────────────────────────────────────────────────────


def test_deterministic_same_seed_parts():
    m1 = _null_members(3, ALPHA / 3)
    m2 = _null_members(3, ALPHA / 3)
    a = sweep_family(m1, correction="bonferroni", iterations=1500, share_a=0.5, seed_parts=("d",))
    b = sweep_family(m2, correction="bonferroni", iterations=1500, share_a=0.5, seed_parts=("d",))
    assert a.fwer == b.fwer and a.fdr == b.fdr and a.valid_iterations == b.valid_iterations


# ── degenerate guards ────────────────────────────────────────────────────────────


def test_empty_family_raises():
    with pytest.raises(ValidateError):
        sweep_family([], correction="none", iterations=10, share_a=0.5, seed_parts=("x",))


def test_zero_iterations_raises():
    with pytest.raises(ValidateError):
        sweep_family(
            _null_members(2, ALPHA), correction="none", iterations=0, share_a=0.5, seed_parts=("x",)
        )


def test_panel_without_unit_ids_raises():
    m = _null_members(2, ALPHA)
    from dataclasses import replace

    broken = replace(m[0], panel=replace(m[0].panel, unit_ids=None))
    with pytest.raises(ValidateError, match="unit_ids"):
        sweep_family(
            [broken, m[1]], correction="none", iterations=10, share_a=0.5, seed_parts=("x",)
        )


def test_all_planted_warns_and_zero_error():
    members = [
        FamilyMember(m.metric, m.panel, m.method, m.alpha, planted=True)
        for m in _null_members(2, ALPHA)
    ]
    s = sweep_family(
        members,
        correction="bonferroni",
        iterations=500,
        share_a=0.5,
        seed_parts=("ap",),
        inject_effect=0.3,
    )
    assert s.n_null_metrics == 0
    assert s.fwer == 0.0 and s.fdr == 0.0  # no null member ⇒ no false rejection possible
    assert any("no null member" in w for w in s.warnings)
