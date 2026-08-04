"""The shared composed-correction rule (m5-implementation-plan.md WP7).

Pins ``stats.correction.composed_significance`` — the compute-time two-tier
Bonferroni ∘ the read-time family rules (Benjamini-Hochberg; Holm since m13
STAT-1) that the readout and the A/A composed FWER/FDR sweep both use — against a
faithful transcription of the pre-extraction inline rule, plus explicit
hand-computed cases. The behaviour-preservation guarantee at the readout level is
covered by the unchanged ``tests/pipeline/test_readout.py::TestBenjaminiHochberg``
verdict tests; ``TestSchemeRoster`` is what forces a NEW scheme to be classified
rather than silently taking the per-row CI branch.
"""

from __future__ import annotations

import itertools

import pytest

from abkit.stats.correction import (
    COMPUTE_TIME_CORRECTIONS,
    READ_TIME_CORRECTIONS,
    Significance,
    SignificanceInput,
    benjamini_hochberg,
    composed_significance,
    holm_adjusted,
)
from abkit.stats.exceptions import MethodParamError


def _reference(inputs, correction):
    """A faithful transcription of the pre-WP7 ``readout._build_sig_map`` inner rule,
    applied to ONE family — the snapshot the extraction must reproduce byte-for-byte."""
    if correction != "benjamini_hochberg":
        out = []
        for it in inputs:
            if it.left_bound is not None and it.left_bound > 0:
                out.append(Significance(True, 1))
            elif it.right_bound is not None and it.right_bound < 0:
                out.append(Significance(True, -1))
            else:
                out.append(Significance(False, 0))
        return out
    results = [Significance(False, 0)] * len(inputs)
    fam = [i for i, it in enumerate(inputs) if it.pvalue is not None]
    if not fam:
        return results
    adjusted = benjamini_hochberg([inputs[i].pvalue for i in fam])
    for pos, adj in zip(fam, adjusted, strict=True):
        it = inputs[pos]
        significant = it.alpha is not None and float(adj) < it.alpha
        sign = 0
        if significant and it.effect is not None and it.effect != 0:
            sign = 1 if it.effect > 0 else -1
        if significant and sign == 0:
            significant = False
        results[pos] = Significance(significant, sign)
    return results


# ── Bonferroni / none: CI-excludes-zero, sign from the bound ─────────────────────


@pytest.mark.parametrize("correction", ["none", "bonferroni"])
def test_bonferroni_none_reads_ci_sign(correction):
    inputs = [
        SignificanceInput(left_bound=0.1, right_bound=0.5, pvalue=0.01, effect=0.3, alpha=0.05),
        SignificanceInput(left_bound=-0.5, right_bound=-0.1, pvalue=0.01, effect=-0.3, alpha=0.05),
        SignificanceInput(left_bound=-0.2, right_bound=0.4, pvalue=0.6, effect=0.1, alpha=0.05),
        SignificanceInput(left_bound=None, right_bound=None, pvalue=None, effect=None, alpha=None),
    ]
    out = composed_significance(inputs, correction)
    assert out == [
        Significance(True, 1),
        Significance(True, -1),
        Significance(False, 0),
        Significance(False, 0),
    ]


# ── Benjamini-Hochberg: family rejection + sign-from-effect + None-p excluded ─────


def test_bh_rejects_family_adjusted_below_raw_alpha():
    # p=0.04 raw-significant, but adjusted across 3 metrics (0.04*3/1=0.12) is not
    inputs = [
        SignificanceInput(left_bound=0.01, right_bound=0.4, pvalue=0.04, effect=0.2, alpha=0.05),
        SignificanceInput(left_bound=-0.4, right_bound=0.5, pvalue=0.9, effect=0.05, alpha=0.05),
        SignificanceInput(left_bound=-0.4, right_bound=0.5, pvalue=0.85, effect=0.05, alpha=0.05),
    ]
    out = composed_significance(inputs, "benjamini_hochberg")
    assert benjamini_hochberg([0.04, 0.9, 0.85])[0] > 0.05  # premise
    assert out[0] == Significance(False, 0)  # BH rescues the false positive


def test_bh_keeps_strongly_significant_and_orients_by_effect():
    inputs = [
        SignificanceInput(left_bound=0.2, right_bound=0.5, pvalue=0.0001, effect=0.3, alpha=0.05),
        SignificanceInput(left_bound=-0.4, right_bound=0.5, pvalue=0.9, effect=-0.9, alpha=0.05),
    ]
    out = composed_significance(inputs, "benjamini_hochberg")
    assert out[0] == Significance(True, 1)  # p=0.0001 survives BH, effect>0 ⇒ +1
    assert out[1] == Significance(False, 0)


def test_bh_none_pvalue_member_is_excluded_from_family_and_nonsignificant():
    # a None-p member must not change m for the others (it is not in the family)
    with_none = [
        SignificanceInput(left_bound=0.01, right_bound=0.4, pvalue=0.02, effect=0.2, alpha=0.05),
        SignificanceInput(left_bound=None, right_bound=None, pvalue=None, effect=None, alpha=0.05),
    ]
    without_none = [with_none[0]]
    a = composed_significance(with_none, "benjamini_hochberg")
    b = composed_significance(without_none, "benjamini_hochberg")
    assert a[1] == Significance(False, 0)  # the None-p member is non-significant
    assert a[0] == b[0]  # and it did not inflate m for the real member


def test_bh_significant_but_zero_effect_cannot_orient_so_not_significant():
    inputs = [
        SignificanceInput(left_bound=0.0, right_bound=0.0, pvalue=0.001, effect=0.0, alpha=0.05),
    ]
    out = composed_significance(inputs, "benjamini_hochberg")
    assert out[0] == Significance(False, 0)


# ── equivalence to the pre-extraction inline rule over a matrix ──────────────────


@pytest.mark.parametrize("correction", ["none", "bonferroni", "benjamini_hochberg"])
def test_matches_reference_over_a_matrix(correction):
    bounds = [(0.1, 0.5), (-0.5, -0.1), (-0.2, 0.4), (None, None)]
    pvals = [0.001, 0.04, 0.6, None]
    effects = [0.3, -0.2, 0.0, None]
    alphas = [0.05, 0.025, None]
    # a spread of 3-member families across the combination space
    combos = list(itertools.product(range(len(bounds)), range(len(pvals)), range(len(alphas))))
    for i in range(0, len(combos) - 2, 3):
        family = []
        for j in range(3):
            bi, pi, ai = combos[i + j]
            lo, hi = bounds[bi]
            family.append(
                SignificanceInput(
                    left_bound=lo,
                    right_bound=hi,
                    pvalue=pvals[pi],
                    effect=effects[pi],
                    alpha=alphas[ai],
                )
            )
        assert composed_significance(family, correction) == _reference(family, correction)


def test_empty_family_returns_empty():
    assert composed_significance([], "benjamini_hochberg") == []
    assert composed_significance([], "none") == []


# ── Holm (m13 STAT-1): step-down FWER, read-time ────────────────────────────────


class TestHolmAdjuster:
    """The arithmetic, against hand-computed values and Holm's own definition."""

    def test_hand_computed_step_down(self):
        # ascending 0.01, 0.03, 0.04 × multipliers 3, 2, 1 → 0.03, 0.06, 0.04,
        # then the running maximum lifts the last to 0.06
        adj = holm_adjusted([0.01, 0.04, 0.03])
        assert adj == pytest.approx([0.03, 0.06, 0.06])

    def test_the_running_maximum_is_not_optional(self):
        """Without it a LARGER raw p could end up rejected while a smaller one is not.

        Bare per-rank multipliers give [0.06, 0.031]: at alpha=0.05 the second member
        (raw p=0.031) would be rejected and the first (raw p=0.03) would not — Holm's
        step-down is a sequence, not m independent thresholds.
        """
        adj = holm_adjusted([0.03, 0.031])
        assert adj == pytest.approx([0.06, 0.06])
        assert all(a >= 0.05 for a in adj)

    def test_equal_pvalues_get_equal_adjustments(self):
        adj = holm_adjusted([0.02, 0.02])
        assert adj[0] == adj[1] == pytest.approx(0.04)

    def test_capped_at_one_and_order_preserved(self):
        adj = holm_adjusted([0.4, 0.9, 0.5])
        assert max(adj) <= 1.0
        assert adj[0] <= adj[2] <= adj[1]

    def test_single_member_is_unadjusted(self):
        assert holm_adjusted([0.031]) == pytest.approx([0.031])

    @pytest.mark.parametrize("bad", [[], [0.1, 1.5], [0.1, float("nan")], [[0.1, 0.2]]])
    def test_rejects_malformed_input(self, bad):
        with pytest.raises(MethodParamError):
            holm_adjusted(bad)

    def test_uniformly_at_least_as_powerful_as_one_step_bonferroni(self):
        """The claim m13 STAT-1 makes: Holm rejects everything Bonferroni does, and more."""
        alpha, pvals = 0.05, [0.001, 0.012, 0.02, 0.4]
        m = len(pvals)
        bonf = [p < alpha / m for p in pvals]
        holm = [a < alpha for a in holm_adjusted(pvals)]
        assert all(h or not b for b, h in zip(bonf, holm, strict=True))
        assert sum(holm) > sum(bonf)  # 0.012 and 0.02 clear Holm but not alpha/4


class TestHolmComposedRule:
    def test_holm_is_not_the_ci_rule(self):
        """The regression this WP exists to prevent: a name test would silently hand
        Holm the per-row CI rule, which controls nothing across the family."""
        inputs = [
            SignificanceInput(
                left_bound=0.01, right_bound=0.4, pvalue=0.03, effect=0.2, alpha=0.05
            ),
            SignificanceInput(
                left_bound=0.01, right_bound=0.4, pvalue=0.04, effect=0.2, alpha=0.05
            ),
        ]
        assert composed_significance(inputs, "none") == [Significance(True, 1)] * 2
        # Holm: sorted 0.03·2 = 0.06 > 0.05 ⇒ nothing is rejected
        assert composed_significance(inputs, "holm") == [Significance(False, 0)] * 2

    def test_holm_rejects_the_family_when_the_smallest_p_clears_alpha_over_m(self):
        inputs = [
            SignificanceInput(
                left_bound=0.2, right_bound=0.5, pvalue=0.001, effect=0.3, alpha=0.05
            ),
            SignificanceInput(
                left_bound=-0.5, right_bound=-0.01, pvalue=0.03, effect=-0.2, alpha=0.05
            ),
        ]
        # 0.001*2 = 0.002 < 0.05 and 0.03*1 = 0.03 < 0.05 ⇒ both rejected, signs from effect
        assert composed_significance(inputs, "holm") == [
            Significance(True, 1),
            Significance(True, -1),
        ]

    def test_the_same_member_is_decided_by_its_neighbours(self):
        """The two-line proof that no fixed per-comparison level reproduces Holm
        (m13 STAT-1, the Fork): same p2, opposite decisions."""
        p2 = SignificanceInput(
            left_bound=0.01, right_bound=0.4, pvalue=0.03, effect=0.2, alpha=0.05
        )
        strong = SignificanceInput(
            left_bound=0.2, right_bound=0.5, pvalue=0.001, effect=0.3, alpha=0.05
        )
        weak = SignificanceInput(
            left_bound=-0.4, right_bound=0.5, pvalue=0.9, effect=0.05, alpha=0.05
        )
        assert composed_significance([strong, p2], "holm")[1].significant is True
        assert composed_significance([weak, p2], "holm")[1].significant is False

    def test_none_pvalue_member_is_excluded_from_m(self):
        with_none = [
            SignificanceInput(
                left_bound=0.01, right_bound=0.4, pvalue=0.03, effect=0.2, alpha=0.05
            ),
            SignificanceInput(
                left_bound=None, right_bound=None, pvalue=None, effect=None, alpha=0.05
            ),
        ]
        assert composed_significance(with_none, "holm")[0].significant is True  # m=1, not 2
        assert composed_significance(with_none, "holm")[1] == Significance(False, 0)

    def test_significant_but_zero_effect_cannot_orient(self):
        inputs = [SignificanceInput(0.0, 0.0, pvalue=0.001, effect=0.0, alpha=0.05)]
        assert composed_significance(inputs, "holm") == [Significance(False, 0)]

    def test_holm_never_rejects_more_than_the_stored_interval_does(self):
        """Fork B's divergence is ONE-directional — the property the readout's caveat
        and the docs both state. A member Holm rejects always has p < its own alpha,
        so its stored raw-alpha interval excludes zero too; the reverse can fail."""
        inputs = [
            SignificanceInput(
                left_bound=0.01, right_bound=0.4, pvalue=0.02, effect=0.2, alpha=0.05
            ),
            SignificanceInput(
                left_bound=0.01, right_bound=0.4, pvalue=0.04, effect=0.2, alpha=0.05
            ),
            SignificanceInput(
                left_bound=-0.4, right_bound=0.5, pvalue=0.5, effect=0.05, alpha=0.05
            ),
        ]
        holm = composed_significance(inputs, "holm")
        ci = composed_significance(inputs, "none")
        assert all(not h.significant or c.significant for h, c in zip(holm, ci, strict=True))
        assert [c.significant for c in ci] == [True, True, False]
        assert [h.significant for h in holm] == [False, False, False]  # 0.02*3 = 0.06 > 0.05


class TestSchemeRoster:
    """A scheme must be classified as compute-time or read-time — the m12 NTF-1
    roster-gate pattern. Adding a config value without a classification here (or an
    adjuster) silently degrades it to the per-row CI rule."""

    def test_the_config_literal_equals_the_two_classification_sets(self):
        from typing import get_args

        from abkit.config.experiment_config import CorrectionKind

        declared = set(get_args(CorrectionKind))
        assert declared == READ_TIME_CORRECTIONS | COMPUTE_TIME_CORRECTIONS
        assert not (READ_TIME_CORRECTIONS & COMPUTE_TIME_CORRECTIONS)

    def test_the_project_default_literal_matches_the_experiment_one(self):
        from typing import get_args

        from abkit.config.experiment_config import CorrectionKind
        from abkit.config.project_config import ProjectStatisticsConfig

        project_literal = ProjectStatisticsConfig.model_fields["correction"].annotation
        assert set(get_args(project_literal)) == set(get_args(CorrectionKind))

    def test_every_read_time_scheme_has_an_adjuster_that_is_actually_used(self):
        from abkit.stats.correction import _FAMILY_ADJUSTERS

        assert set(_FAMILY_ADJUSTERS) == READ_TIME_CORRECTIONS
        # and each one is REACHED — a family whose per-row CI rule and family rule
        # disagree, so a scheme that quietly fell through to the CI branch fails here
        inputs = [
            SignificanceInput(
                left_bound=0.01, right_bound=0.4, pvalue=0.04, effect=0.2, alpha=0.05
            ),
            SignificanceInput(
                left_bound=-0.4, right_bound=0.5, pvalue=0.9, effect=0.05, alpha=0.05
            ),
        ]
        ci_rule = composed_significance(inputs, "none")
        assert [s.significant for s in ci_rule] == [True, False]
        for scheme in READ_TIME_CORRECTIONS:
            assert composed_significance(inputs, scheme) != ci_rule

    def test_an_unknown_scheme_takes_the_compute_time_branch(self):
        inputs = [SignificanceInput(0.1, 0.5, pvalue=0.9, effect=0.3, alpha=0.05)]
        assert composed_significance(inputs, "not-a-scheme") == [Significance(True, 1)]
