"""NTF-3 rules: when is an announcement due? (m12-implementation-plan.md NTF-3)

Pure — no warehouse, no config objects — because the one thing a notification
system can get wrong SILENTLY is deciding not to speak. D2 (maintainer-signed
2026-08-02) is the contract under test: a change always announces, an unchanged
value never re-announces, and ``cooldown_seconds`` is not consulted for verdict
dedup at all. NTF-5 adds the recurring half: the two kinds whose condition
survives the run that reports it, where an unchanged value CAN repeat — but
only on the cooldown, and never at the cost of a change.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from abkit.notify.cooldown import (
    EMPTY_STATE,
    announcement_signature,
    is_in_cooldown,
    recurring_signature,
    rollup_signature,
    should_announce,
    should_announce_recurring,
)

NOW = datetime(2026, 8, 3, 12, 0, 0)


def state(**overrides) -> dict:
    base = dict(EMPTY_STATE)
    base.update(overrides)
    return base


class TestShouldAnnounce:
    def test_the_first_readout_is_always_news(self):
        assert should_announce(EMPTY_STATE, "WIN", False) is True

    def test_an_unchanged_verdict_is_never_re_sent(self):
        previous = state(last_verdict="WIN", notify_count=1)

        assert should_announce(previous, "WIN", False) is False

    @pytest.mark.parametrize(
        "before, after",
        [("WIN", "LOSE"), ("INCONCLUSIVE", "WIN"), ("FLAT", "INCONCLUSIVE")],
    )
    def test_a_flip_always_announces(self, before, after):
        previous = state(last_verdict=before, notify_count=3)

        assert should_announce(previous, after, False) is True

    def test_a_new_srm_breach_announces_even_when_the_word_is_identical(self):
        """The hazard this rule exists for. A pre-horizon pair sits at
        INCONCLUSIVE for days; when its sample-ratio gate breaks the readout
        still says INCONCLUSIVE, so deduping on the verdict word alone would
        swallow the SRM alarm on exactly the experiments most likely to need
        it."""
        previous = state(last_verdict="INCONCLUSIVE", last_srm_flag=False, notify_count=5)

        assert should_announce(previous, "INCONCLUSIVE", True) is True

    def test_a_recovered_srm_gate_announces_too(self):
        previous = state(last_verdict="INCONCLUSIVE", last_srm_flag=True, notify_count=5)

        assert should_announce(previous, "INCONCLUSIVE", False) is True

    def test_an_unchanged_srm_failure_is_not_re_sent(self):
        previous = state(last_verdict="INCONCLUSIVE", last_srm_flag=True, notify_count=5)

        assert should_announce(previous, "INCONCLUSIVE", True) is False

    def test_a_count_of_zero_announces_even_if_the_signature_matches(self):
        """A row can exist with nothing delivered (a failed send never records,
        but a future writer might). `notify_count == 0` means "nobody has heard
        this", and a None-vs-None signature comparison would otherwise read as
        unchanged and stay silent forever."""
        previous = state(last_verdict=None, notify_count=0)

        assert should_announce(previous, None, False) is True

    def test_the_signature_is_the_triple(self):
        assert announcement_signature("WIN", False) == ("WIN", False, None)
        assert announcement_signature("WIN", True) != announcement_signature("WIN", False)

    def test_a_leader_flip_announces_although_every_word_is_unchanged(self):
        """m14 DEC-4. At three arms the ship decision can change with no verdict
        moving: B stops being the arm to ship and C starts. Without the rollup
        term the message is deduped away and nobody is told — NTF-3's own
        "deduping on the verdict word alone" trap, one arm-count up."""
        announced = {
            "last_verdict": "WIN",
            "last_srm_flag": False,
            "notify_count": 1,
            "last_rollup": rollup_signature("b", "separated"),
        }

        assert not should_announce(
            announced, "WIN", False, rollup_signature("b", "separated")
        ), "nothing moved"
        assert should_announce(announced, "WIN", False, rollup_signature("c", "separated"))
        assert should_announce(
            announced, "WIN", False, rollup_signature("b", "co_leaders")
        ), "'we could not compare them' → 'they are tied' is also a changed decision"

    def test_a_pre_0_9_0_state_row_does_not_re_announce_on_upgrade(self):
        """The stored value is NULL for every row written before `0.9.0`, and a
        readout with no rollup signs as ``None`` — so the first `0.9.0` run of a
        quiet project stays quiet."""
        announced = {
            "last_verdict": "FLAT",
            "last_srm_flag": False,
            "notify_count": 3,
            "last_rollup": None,
        }

        assert not should_announce(announced, "FLAT", False, rollup_signature(None, None))

    def test_a_two_arm_rollup_cannot_move_without_the_verdict_moving(self):
        """The §0.2 leg. With one treatment the leader is that arm iff it WON
        and the separation follows, so the rollup term is a function of the word
        already in the signature — it can add a message but never a NEW one."""
        won = rollup_signature("treatment", "separated")
        lost = rollup_signature(None, "no_leader")
        announced = {
            "last_verdict": "WIN",
            "last_srm_flag": False,
            "notify_count": 1,
            "last_rollup": won,
        }

        assert not should_announce(announced, "WIN", False, won)
        # the only way the rollup differs is the word differing too
        assert should_announce(announced, "FLAT", False, lost)


class TestRecurringSignature:
    def test_it_is_order_and_duplicate_free(self):
        assert recurring_signature(["b", "a", "b"]) == recurring_signature(["a", "b"])

    def test_nothing_wrong_is_the_empty_signature(self):
        assert recurring_signature([]) == ""

    def test_a_second_item_is_a_different_condition(self):
        assert recurring_signature(["a"]) != recurring_signature(["a", "b"])


class TestShouldAnnounceRecurring:
    """The `stale` / `calibration_red` rule (m12 NTF-5): the same condition
    persists across runs, so unlike a verdict it needs a cooldown to ever
    repeat — and, like a verdict, a CHANGE must never wait for one."""

    def test_a_first_occurrence_announces(self):
        assert should_announce_recurring(EMPTY_STATE, "arpu", None, NOW) is True

    def test_an_unchanged_condition_stays_quiet_without_a_cooldown(self):
        previous = state(last_verdict="arpu", last_notified_at=NOW, notify_count=1)

        assert should_announce_recurring(previous, "arpu", None, NOW) is False

    def test_none_and_zero_are_different_windows(self):
        """`is_in_cooldown` mutes neither, so deferring to it alone would make
        the DEFAULT re-announce an unchanged condition on every run."""
        previous = state(last_verdict="arpu", last_notified_at=NOW, notify_count=1)

        assert should_announce_recurring(previous, "arpu", None, NOW) is False
        assert should_announce_recurring(previous, "arpu", 0, NOW) is True

    def test_a_widened_condition_announces_inside_the_cooldown(self):
        """A second metric falling behind is news the timer may not swallow —
        D2's rule, applied to the kinds the cooldown actually governs."""
        previous = state(
            last_verdict=recurring_signature(["arpu"]),
            last_notified_at=NOW - timedelta(seconds=1),
            notify_count=1,
        )

        assert (
            should_announce_recurring(previous, recurring_signature(["arpu", "cr"]), 86_400, NOW)
            is True
        )

    def test_an_unchanged_condition_repeats_once_the_cooldown_elapses(self):
        previous = state(
            last_verdict="arpu", last_notified_at=NOW - timedelta(hours=2), notify_count=1
        )

        assert should_announce_recurring(previous, "arpu", 3600, NOW) is True
        assert should_announce_recurring(previous, "arpu", 86_400, NOW) is False

    def test_a_cleared_then_recurring_condition_is_news_again(self):
        """The recovery reset: an empty signature is recorded when the
        condition goes away, so its return does not dedup against a row from
        months ago."""
        cleared = state(last_verdict="", last_notified_at=NOW, notify_count=2)

        assert should_announce_recurring(cleared, "arpu", None, NOW) is True


class TestIsInCooldown:
    """The primitive the recurring kinds need — deliberately NOT consulted by
    `should_announce` (D2)."""

    @pytest.mark.parametrize("cooldown", [None, 0, -5])
    def test_no_configured_window_is_never_a_cooldown(self, cooldown):
        previous = state(last_notified_at=NOW)

        assert is_in_cooldown(previous, cooldown, NOW) is False

    def test_never_notified_is_never_in_cooldown(self):
        assert is_in_cooldown(EMPTY_STATE, 3600, NOW) is False

    def test_inside_the_window(self):
        previous = state(last_notified_at=NOW - timedelta(minutes=10))

        assert is_in_cooldown(previous, 3600, NOW) is True

    def test_outside_the_window(self):
        previous = state(last_notified_at=NOW - timedelta(hours=2))

        assert is_in_cooldown(previous, 3600, NOW) is False

    def test_it_does_not_gate_a_verdict_change(self):
        """The whole point of D2: a flip inside the cooldown window still
        announces — a timer must never swallow WIN→LOSE."""
        previous = state(
            last_verdict="WIN", last_notified_at=NOW - timedelta(seconds=1), notify_count=1
        )

        assert is_in_cooldown(previous, 86_400, NOW) is True
        assert should_announce(previous, "LOSE", False) is True
