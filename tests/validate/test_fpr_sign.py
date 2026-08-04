"""m13 STAT-2: the A/A matrix records WHICH SIDE each false positive fell on.

Why the count alone cannot do this job: several relative-effect formulas share an
*identical* rejection set at the null, so their measured FPRs agree to the last
false positive while their false positives lean opposite ways. The sign share is
the only column that separates them — and the lean it detects GROWS as α shrinks,
i.e. it is worst in exactly the corrected tier.

The tests below are about the instrument, not about any estimator: an instrument
that can only ever report 0.5 is indistinguishable from one that is not looking.
"""

from __future__ import annotations

import math

import pytest

from abkit.validate.runner import (
    _MIN_HITS_FOR_SIGN_LEAN,
    _SIGN_LEAN_SIGMAS,
    _sign_lean_note,
)
from abkit.validate.scoring import CellScore


def _score(*, share: float | None, fpr: float | None, valid: int) -> CellScore:
    """A CellScore carrying only the fields the note reads."""
    return CellScore(
        iterations=valid,
        valid_iterations=valid,
        fpr=fpr,
        fpr_negative_share=share,
        peeking_fpr=None,
        peeking_curve=(),
        power=None,
        coverage=None,
        achieved_mde=None,
        effect_exaggeration=None,
        injected_effect=None,
        kept_grid_points=1,
        total_grid_points=1,
        degenerate_horizon=0,
        warnings=(),
    )


class TestTheNoteCanSayMoreThanNothing:
    """A gate that never fires is not a gate."""

    def test_symmetric_false_positives_say_nothing(self) -> None:
        note = _sign_lean_note(_score(share=0.5, fpr=0.05, valid=40_000))
        assert note == ""

    def test_a_real_lean_is_reported_with_its_side(self) -> None:
        """2000 hits ⇒ SE = 1.1%, so 66% is ~14 sigma out — the delta-method
        signature the blind derivation predicts at CV₁ = 0.05."""
        note = _sign_lean_note(_score(share=0.66, fpr=0.05, valid=40_000))
        assert "lean below zero" in note
        assert "66%" in note

    def test_the_other_side_is_named_correctly(self) -> None:
        note = _sign_lean_note(_score(share=0.34, fpr=0.05, valid=40_000))
        assert "lean above zero" in note

    def test_the_threshold_is_a_TEST_not_a_fixed_percentage(self) -> None:
        """The same share is noise on a small cell and a finding on a large one.

        A fixed-percentage gate would fire constantly on small cells and never on
        large ones; this one scales with sqrt(0.25/hits), so it does the opposite
        of the naive thing — which is the whole point of stating it as a test.
        """
        # 200 hits ⇒ SE = 3.5%, so 0.56 is 1.7 sigma — noise, stays silent
        assert _sign_lean_note(_score(share=0.56, fpr=0.05, valid=4_000)) == ""
        # 20 000 hits ⇒ SE = 0.35%, the SAME share is now 17 sigma — reported
        assert _sign_lean_note(_score(share=0.56, fpr=0.05, valid=400_000)) != ""

    def test_too_few_hits_is_silence_however_extreme_the_share(self) -> None:
        """At the floor the share's own SE is 5 points; a lean read off fewer is
        a coin flip dressed as a finding."""
        hits = _MIN_HITS_FOR_SIGN_LEAN - 1
        assert _sign_lean_note(_score(share=1.0, fpr=hits / 10_000, valid=10_000)) == ""

    def test_the_sigma_gate_is_the_documented_one(self) -> None:
        """Pin the boundary rather than trusting the constant's name: just inside
        stays silent, just outside speaks."""
        valid, fpr = 40_000, 0.05
        hits = round(fpr * valid)
        se = math.sqrt(0.25 / hits)
        inside = 0.5 + (_SIGN_LEAN_SIGMAS - 0.1) * se
        outside = 0.5 + (_SIGN_LEAN_SIGMAS + 0.1) * se
        assert _sign_lean_note(_score(share=inside, fpr=fpr, valid=valid)) == ""
        assert _sign_lean_note(_score(share=outside, fpr=fpr, valid=valid)) != ""

    @pytest.mark.parametrize(
        "share,fpr,valid",
        [(None, 0.05, 40_000), (0.9, None, 40_000), (0.9, 0.05, 0)],
    )
    def test_unmeasurable_cells_are_silent_never_crashing(
        self, share: float | None, fpr: float | None, valid: int
    ) -> None:
        assert _sign_lean_note(_score(share=share, fpr=fpr, valid=valid)) == ""


class TestTheShareIsMeasuredNotAssumed:
    def test_no_hits_means_no_share_rather_than_a_fabricated_half(self) -> None:
        """The denominator is the HITS. With none, there is no side to report —
        and 0.5 would be a claim about data that does not exist."""
        assert _score(share=None, fpr=0.0, valid=40_000).fpr_negative_share is None
