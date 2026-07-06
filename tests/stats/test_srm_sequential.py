"""Tests for the sub-day anytime-valid sequential-multinomial SRM gate.

Lindon & Malek (2022, NeurIPS; arXiv:2011.03567 §2.2). The e-value is a
Dirichlet-multinomial mixture Bayes factor; the KAT pins it against a direct
``scipy.special.gammaln`` re-derivation (rel-1e-12) and the exact rational
anchor ``BF(10,0) = 1024/11`` (θ0=½, uniform Beta(1,1) prior). The anytime
property (false-alarm ≤ α over a data-dependent look schedule) is a large-n
Monte-Carlo within a Binomial band (statistics-changes.md §4.2).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.special import gammaln

from abkit.stats import DEFAULT_SRM_ALPHA, sequential_multinomial_srm
from abkit.stats.exceptions import SampleValidationError


def _log_bf(counts: dict[str, float], theta0: dict[str, float], alpha0: dict[str, float]) -> float:
    """Independent re-derivation of the log Bayes factor (pins the formula)."""
    variants = sorted(theta0)
    n = np.array([counts[v] for v in variants], dtype=float)
    p0 = np.array([theta0[v] for v in variants], dtype=float)
    p0 = p0 / p0.sum()
    a0 = np.array([alpha0[v] for v in variants], dtype=float)
    return float(
        gammaln(a0.sum())
        - gammaln(a0.sum() + n.sum())
        + np.sum(gammaln(a0 + n) - gammaln(a0) - n * np.log(p0))
    )


# ─── the exact-rational golden anchor ────────────────────────────────────────


def test_kat_10_0_is_exact_rational_1024_over_11() -> None:
    """θ0=½, uniform Beta(1,1), counts (10,0) ⇒ BF = 1024/11 (hand-checkable)."""
    r = sequential_multinomial_srm([{"a": 10, "b": 0}], {"a": 0.5, "b": 0.5})[0]
    assert r.e_value == pytest.approx(1024 / 11, rel=1e-12)
    assert math.log(r.e_value) == pytest.approx(4.5335765328, rel=1e-9)
    # the anytime p-value is the e-value's dual, min(1, 1/e) = 11/1024
    assert r.pvalue == pytest.approx(11 / 1024, rel=1e-12)
    assert r.kind == "sequential_multinomial"


def test_kat_matches_gammaln_rederivation_two_variants() -> None:
    counts = {"a": 68, "b": 32}
    r = sequential_multinomial_srm([counts], {"a": 0.5, "b": 0.5})[0]
    expected = math.exp(_log_bf(counts, {"a": 0.5, "b": 0.5}, {"a": 1.0, "b": 1.0}))
    assert r.e_value == pytest.approx(expected, rel=1e-12)


def test_kat_three_variants_dirichlet_multinomial() -> None:
    """k>2 uses ONE Dirichlet-multinomial mixture vs the point null (not pairwise)."""
    counts = {"a": 20, "b": 30, "c": 50}
    theta0 = {"a": 0.1, "b": 0.4, "c": 0.5}
    r = sequential_multinomial_srm([counts], theta0)[0]
    expected = math.exp(_log_bf(counts, theta0, {"a": 1.0, "b": 1.0, "c": 1.0}))
    assert r.e_value == pytest.approx(expected, rel=1e-12)
    assert r.e_value == pytest.approx(3.987, rel=1e-3)  # hand-checked magnitude


# ─── the rejection rule (e ≥ 1/α) ────────────────────────────────────────────


def test_default_alpha_is_the_chi_square_alpha() -> None:
    r = sequential_multinomial_srm([{"a": 10, "b": 0}], {"a": 0.5, "b": 0.5})[0]
    assert r.alpha == DEFAULT_SRM_ALPHA  # the sub-day gate is as strict as χ²


def test_decision_edge_at_alpha_0_05() -> None:
    """Threshold 1/0.05 = 20: (68,32) e≈87.8 fires, (65,35) e≈11.5 does not."""
    fired = sequential_multinomial_srm([{"a": 68, "b": 32}], {"a": 0.5, "b": 0.5}, alpha=0.05)[0]
    quiet = sequential_multinomial_srm([{"a": 65, "b": 35}], {"a": 0.5, "b": 0.5}, alpha=0.05)[0]
    assert fired.srm_flag is True and fired.e_value > 20
    assert quiet.srm_flag is False and quiet.e_value < 20


def test_moderate_imbalance_below_strict_gate_does_not_fire() -> None:
    # (10,0) e≈93 fires at 0.05 (thr 20) but NOT at the strict 0.001 (thr 1000)
    r = sequential_multinomial_srm([{"a": 10, "b": 0}], {"a": 0.5, "b": 0.5})[0]
    assert r.srm_flag is False


def test_balanced_stream_is_evidence_for_the_null() -> None:
    """(60,40) at fixed-n χ² is p≈0.046 (significant) but the mixture e<1 —
    the sequential test is not fooled by a single peek at a noisy split."""
    r = sequential_multinomial_srm([{"a": 60, "b": 40}], {"a": 0.5, "b": 0.5}, alpha=0.05)[0]
    assert r.e_value < 1.0
    assert r.srm_flag is False
    assert r.pvalue == pytest.approx(1.0)


# ─── the anytime (running-max) construction ──────────────────────────────────


def test_e_value_is_running_max_flag_sticks_once_crossed() -> None:
    """A look that dips after crossing keeps the flag (running-max e / running-min p)."""
    stream = [{"a": 68, "b": 32}, {"a": 60, "b": 40}]  # crosses, then dips
    res = sequential_multinomial_srm(stream, {"a": 0.5, "b": 0.5}, alpha=0.05)
    assert [r.srm_flag for r in res] == [True, True]
    # the reported e-value never decreases; the p-value never increases
    assert res[1].e_value >= res[0].e_value
    assert res[1].pvalue <= res[0].pvalue


def test_growing_imbalance_trips_at_a_later_look_not_early() -> None:
    """The truthful as-of series: 60/40 accruing 150c/100t per look trips at
    look 2 under the strict 0.001 gate, not look 1."""
    stream = [{"control": 150 * k, "treatment": 100 * k} for k in (1, 2, 3, 4)]
    res = sequential_multinomial_srm(stream, {"control": 0.5, "treatment": 0.5})
    assert [r.srm_flag for r in res] == [False, True, True, True]


def test_zero_counts_early_looks_have_unit_e_value() -> None:
    stream = [{"a": 0, "b": 0}, {"a": 3, "b": 2}]
    res = sequential_multinomial_srm(stream, {"a": 0.5, "b": 0.5})
    assert res[0].e_value == pytest.approx(1.0)  # N=0 ⇒ BF=1
    assert res[0].pvalue == pytest.approx(1.0)
    assert res[0].srm_flag is False


def test_empty_stream_returns_empty() -> None:
    assert sequential_multinomial_srm([], {"a": 0.5, "b": 0.5}) == []


# ─── anytime false-alarm ≤ α (the peeking property) ──────────────────────────


def test_anytime_false_alarm_within_binomial_band() -> None:
    """Under the null, the share of streams that EVER cross ≤ α across a
    data-dependent (peek-at-every-look) schedule — the property χ² lacks."""
    rng = np.random.default_rng(20260706)
    alpha, n_sims, max_units = 0.05, 3000, 1600
    looks = list(range(20, max_units + 1, 20))  # 80 looks
    false_alarms = 0
    for _ in range(n_sims):
        cum = np.cumsum(rng.binomial(1, 0.5, size=max_units))
        stream = [{"a": int(cum[n - 1]), "b": int(n - cum[n - 1])} for n in looks]
        if sequential_multinomial_srm(stream, {"a": 0.5, "b": 0.5}, alpha=alpha)[-1].srm_flag:
            false_alarms += 1
    rate = false_alarms / n_sims
    se = math.sqrt(alpha * (1 - alpha) / n_sims)
    # anytime-valid ⇒ conservative: expect at/below α, never materially above.
    assert rate <= alpha + 3 * se


def test_power_against_a_planted_imbalance() -> None:
    """A 58/42 bug is caught with high probability at the strict 0.001 gate —
    the FPR guard is not achieved by a gate that never rejects."""
    rng = np.random.default_rng(11)
    looks = list(range(50, 4001, 50))
    hits = 0
    for _ in range(200):
        cum = np.cumsum(rng.binomial(1, 0.58, size=4000))
        stream = [{"a": int(cum[n - 1]), "b": int(n - cum[n - 1])} for n in looks]
        if sequential_multinomial_srm(stream, {"a": 0.5, "b": 0.5})[-1].srm_flag:
            hits += 1
    assert hits / 200 > 0.9


# ─── the prior is a fixed-in-advance power knob (FPR holds regardless) ────────


def test_explicit_concentration_prior_changes_evidence_not_validity() -> None:
    counts = {"a": 20, "b": 30, "c": 50}
    theta0 = {"a": 0.1, "b": 0.4, "c": 0.5}
    uniform = sequential_multinomial_srm([counts], theta0)[0]
    # a mean-pinned k·θ0 concentration (paper's opt-in) yields a different BF
    concentrated = sequential_multinomial_srm(
        [counts], theta0, prior={"a": 10.0, "b": 40.0, "c": 50.0}
    )[0]
    assert concentrated.e_value != pytest.approx(uniform.e_value)
    expected = math.exp(_log_bf(counts, theta0, {"a": 10.0, "b": 40.0, "c": 50.0}))
    assert concentrated.e_value == pytest.approx(expected, rel=1e-12)


# ─── validation (mirrors the χ² gate's raisers) ──────────────────────────────


def test_single_variant_raises() -> None:
    with pytest.raises(SampleValidationError, match="at least two"):
        sequential_multinomial_srm([{"a": 5}], {"a": 1.0})


def test_mismatched_look_variants_raise() -> None:
    with pytest.raises(SampleValidationError, match="expected_split variants"):
        sequential_multinomial_srm([{"a": 5, "c": 5}], {"a": 0.5, "b": 0.5})


def test_negative_count_raises() -> None:
    with pytest.raises(SampleValidationError, match="non-negative"):
        sequential_multinomial_srm([{"a": -1, "b": 5}], {"a": 0.5, "b": 0.5})


def test_non_positive_expected_share_raises() -> None:
    with pytest.raises(SampleValidationError, match="positive"):
        sequential_multinomial_srm([{"a": 5, "b": 5}], {"a": 0.0, "b": 1.0})


def test_mismatched_prior_variants_raise() -> None:
    with pytest.raises(SampleValidationError, match="prior variants"):
        sequential_multinomial_srm([{"a": 5, "b": 5}], {"a": 0.5, "b": 0.5}, prior={"a": 1.0})


def test_non_positive_prior_raises() -> None:
    with pytest.raises(SampleValidationError, match="prior concentrations must be positive"):
        sequential_multinomial_srm(
            [{"a": 5, "b": 5}], {"a": 0.5, "b": 0.5}, prior={"a": 0.0, "b": 1.0}
        )


# ─── describe() surfaces the anytime evidence ────────────────────────────────


def test_describe_flagged_reads_anytime_not_chi2() -> None:
    r = sequential_multinomial_srm([{"a": 300, "b": 200}], {"a": 0.5, "b": 0.5})[0]
    msg = r.describe()
    assert "SRM FAILED" in msg
    assert "anytime e=" in msg and "chi2" not in msg
    assert "0.60" in msg and "0.40" in msg  # observed shares


def test_describe_ok_when_not_flagged() -> None:
    msg = sequential_multinomial_srm([{"a": 500, "b": 500}], {"a": 0.5, "b": 0.5})[0].describe()
    assert msg.startswith("SRM ok")
    assert "anytime e=" in msg
