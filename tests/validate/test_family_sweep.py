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


def _null_members(k: int, alpha: float, n_cutoffs: int = 1):
    # k independent well-behaved metrics on the SAME cohort (one shared mask, distinct
    # values ⇒ ~independent tests) — the canonical family-error fixture. ``n_cutoffs>1``
    # gives the peeking columns multiple looks to accumulate over (WP-B).
    return [
        FamilyMember(
            metric=f"m{i}",
            panel=normal_panel(n_units=3000, n_cutoffs=n_cutoffs, seed=100 + i, mu=50.0, sigma=10.0),
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


def test_two_tier_members_are_within_the_nominal_family_budget():
    """M5 exit-gate round-1 fix: the DEFAULT two-tier Bonferroni puts the main tier and
    the secondary tier each at α (they are NOT budget-shared), so a calibrated two-metric
    family sits at its nominal composed rate ≈2α, not α. Judged against the single-cell
    α×1.5 it would falsely read over-budget; anchored to the nominal rate it does not."""
    members = _null_members(2, ALPHA)  # both at the full α — the runner's two-tier reality
    nominal = 1.0 - (1.0 - ALPHA) ** 2  # ≈ 0.0975
    budget = nominal * 1.5  # the runner's headroom over the family's own nominal rate
    s = sweep_family(
        members,
        correction="bonferroni",
        iterations=5000,
        share_a=0.5,
        seed_parts=("tt",),
        budget=budget,
    )
    assert 0.07 < s.fwer < 0.12  # ≈ nominal 0.0975
    assert not s.over_budget  # within the nominal-anchored budget (the old α×1.5 would trip)
    assert s.fwer > ALPHA * 1.5  # and it genuinely exceeds the old single-cell budget


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


# ── the composed peeking pair: fixed hazard → always-valid recovery (D8×D9, WP-B) ──


def test_sequential_off_by_default_leaves_no_peeking_columns():
    """The shipped single-look family (``sequential`` unset) computes ONLY ``fwer``/``fdr``
    — the peeking pair is absent (None), never zero-filled, so the M5 byte-shape holds."""
    members = _null_members(3, ALPHA / 3, n_cutoffs=8)
    s = sweep_family(members, correction="bonferroni", iterations=500, share_a=0.5, seed_parts=("q",))
    assert s.fwer is not None  # the single-look family still measured
    assert s.fwer_peeking is None and s.fdr_peeking is None
    assert s.fwer_sequential is None and s.fdr_sequential is None
    assert s.any_rejection_rate_peeking is None and s.any_rejection_rate_sequential is None


def test_sequential_composed_peeking_hazard_recovers_to_control():
    """The D8×D9 headline: across an 8-look grid the composed FIXED-CI peeking family-wise
    error inflates well past nominal (the optional-stopping hazard the per-cell column warns
    about), while the ALWAYS-VALID twin — same shared assignments, same composed rule, only
    the marginals widened — is brought back to ≈ the single-look rate. The single-look
    ``fwer`` is unchanged from the fixed sweep."""
    members = _null_members(3, ALPHA / 3, n_cutoffs=8)  # Bonferroni α/3 ⇒ composed ≈ α
    s = sweep_family(
        members, correction="bonferroni", iterations=2000, share_a=0.5, seed_parts=("seq",),
        sequential=True,
    )
    assert s.fwer_peeking is not None and s.fwer_sequential is not None
    # the single-look composed family sits at ≈ α (unchanged by the peeking pair)
    assert 0.02 < s.fwer < 0.09
    # fixed-CI peeking is inflated across the 8 looks — strictly worse than a single look
    assert s.fwer_peeking > 2 * ALPHA
    assert s.fwer_peeking > s.fwer
    # the always-valid twin is strictly better and controlled at/below the single-look rate
    # (anytime-valid CIs are conservative → it sits below, never materially above, ``fwer``)
    assert s.fwer_sequential < s.fwer_peeking
    assert s.fwer_sequential <= s.fwer + 0.02
    # complete-null identity holds for BOTH new families (every rejection is false)
    assert s.fwer_peeking == pytest.approx(s.fdr_peeking, abs=1e-12)
    assert s.fwer_sequential == pytest.approx(s.fdr_sequential, abs=1e-12)
    assert s.any_rejection_rate_peeking == pytest.approx(s.fwer_peeking, abs=1e-12)
    assert s.any_rejection_rate_sequential == pytest.approx(s.fwer_sequential, abs=1e-12)


def test_sequential_bh_peeking_pair_also_recovers():
    """The recovery holds under read-time BH too (the p-value path): the composed peeking
    FDR inflates and the always-valid twin returns to control."""
    members = _null_members(3, ALPHA, n_cutoffs=8)  # BH members carry the RAW alpha
    s = sweep_family(
        members, correction="benjamini_hochberg", iterations=2000, share_a=0.5,
        seed_parts=("seqbh",), sequential=True,
    )
    assert s.fdr_peeking is not None and s.fdr_sequential is not None
    assert s.fdr_peeking > 2 * ALPHA  # peeking inflates the BH family FDR
    assert s.fdr_sequential < s.fdr_peeking  # the always-valid twin recovers
    assert s.fdr_sequential <= s.fdr + 0.03


def test_sequential_ineligible_member_is_a_full_gap_and_twin_uses_the_eligible_member():
    """A sequential-ineligible member is a bootstrap method, which cannot be scored from
    suffstats at all (it needs per-unit samples) — so it is a FULL gap in every family, not
    a fixed-peeking-only rider. It is honestly disclosed by the 'scored in 0 iterations —
    excluded' warning; the peeking pair is still computed from the eligible t-test (which
    supplies the ≥1 τ² that lights ``peek_active``), over the SAME member set as the fixed
    peeking family (no asymmetry, so the recovery story is honest)."""
    tt = _null_members(1, ALPHA / 2, n_cutoffs=8)[0]
    boot = FamilyMember(
        "boot",
        normal_panel(n_units=3000, n_cutoffs=8, seed=900, mu=50.0, sigma=10.0),
        MethodConfig(name="bootstrap", params={"n_samples": 200}).bind(alpha=ALPHA / 2),
        ALPHA / 2,
    )
    s = sweep_family(
        [tt, boot], correction="bonferroni", iterations=300, share_a=0.5,
        seed_parts=("mix",), sequential=True,
    )
    assert s.fwer_peeking is not None and s.fwer_sequential is not None  # eligible t-test drives both
    # the bootstrap member is a full gap, disclosed honestly — no false "rides in the hazard" claim
    assert any("boot" in w and "scored in 0" in w for w in s.warnings)
    assert not any("no always-valid option" in w for w in s.warnings)


def test_sequential_deterministic_same_seed_parts():
    a = sweep_family(
        _null_members(2, ALPHA / 2, n_cutoffs=6), correction="bonferroni", iterations=800,
        share_a=0.5, seed_parts=("sd",), sequential=True,
    )
    b = sweep_family(
        _null_members(2, ALPHA / 2, n_cutoffs=6), correction="bonferroni", iterations=800,
        share_a=0.5, seed_parts=("sd",), sequential=True,
    )
    assert a.fwer_peeking == b.fwer_peeking and a.fwer_sequential == b.fwer_sequential


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


def test_persistently_gapped_member_is_warned_not_silent():
    """M5 exit-gate round-2 fix: a member whose cohort is too small to ever split ≥2
    units/arm scores in 0 iterations — it must be named in the warnings, never ride
    silently in the 'over N metrics' verdict as if validated."""
    big = normal_panel(n_units=3000, n_cutoffs=1, seed=11, mu=50.0, sigma=10.0, unit_offset=0)
    tiny = normal_panel(n_units=3, n_cutoffs=1, seed=12, mu=50.0, sigma=10.0, unit_offset=9000)
    members = [
        FamilyMember("big", big, _ttest(ALPHA / 2), ALPHA / 2),
        FamilyMember("tiny", tiny, _ttest(ALPHA / 2), ALPHA / 2),
    ]
    s = sweep_family(
        members, correction="bonferroni", iterations=500, share_a=0.5, seed_parts=("g",)
    )
    assert s.valid_iterations == 500  # the big metric is always scorable
    assert any("tiny" in w and "scored in 0" in w for w in s.warnings)


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
