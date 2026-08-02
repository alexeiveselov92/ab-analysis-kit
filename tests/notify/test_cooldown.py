"""NTF-3 rules: when is an announcement due? (m12-implementation-plan.md NTF-3)

Pure — no warehouse, no config objects — because the one thing a notification
system can get wrong SILENTLY is deciding not to speak. D2 (maintainer-signed
2026-08-02) is the contract under test: a change always announces, an unchanged
value never re-announces, and ``cooldown_seconds`` is not consulted for verdict
dedup at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from abkit.notify.cooldown import (
    EMPTY_STATE,
    announcement_signature,
    is_in_cooldown,
    should_announce,
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

    def test_the_signature_is_the_pair(self):
        assert announcement_signature("WIN", False) == ("WIN", False)
        assert announcement_signature("WIN", True) != announcement_signature("WIN", False)


class TestIsInCooldown:
    """The primitive a future recurring kind needs — deliberately NOT consulted
    by `should_announce` (D2)."""

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
