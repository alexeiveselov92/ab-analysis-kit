"""The shared composed-correction rule (m5-implementation-plan.md WP7).

Pins ``stats.correction.composed_significance`` — the extracted two-tier
Bonferroni ∘ read-time Benjamini-Hochberg rule the readout and the A/A composed
FWER/FDR sweep both use — against a faithful transcription of the pre-extraction
inline rule, plus explicit hand-computed cases. The behaviour-preservation
guarantee at the readout level is covered by the unchanged
``tests/pipeline/test_readout.py::TestBenjaminiHochberg`` verdict tests.
"""

from __future__ import annotations

import itertools

import pytest

from abkit.stats.correction import (
    Significance,
    SignificanceInput,
    benjamini_hochberg,
    composed_significance,
)


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
