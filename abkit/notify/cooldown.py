"""When is an announcement due? (m12 NTF-3)

Pure rules over a state dict — no database, no config objects — so the one
decision that can go silently wrong in a notification system is unit-testable
without a warehouse.

**D2, signed off by the maintainer 2026-08-02 and binding here:** a verdict
CHANGE always sends, even inside a cooldown window; an UNCHANGED verdict is
never re-sent, however long the cooldown. Getting that backwards would let a
timer swallow the single WIN→LOSE message the whole feature exists to deliver —
a silence, not a crash, which is why it was arbitrated before any code was
written. ``cooldown_seconds`` is therefore NOT consulted for verdict dedup; it
is reserved for a future recurring kind (a repeating ``stale``) that
legitimately re-fires with the same value, and :func:`is_in_cooldown` is
exported for it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

#: What the state store answers for a comparison nobody has announced yet.
EMPTY_STATE: dict[str, Any] = {
    "last_verdict": None,
    "last_srm_flag": False,
    "last_notified_at": None,
    "notify_count": 0,
}


def announcement_signature(verdict: str | None, srm_flag: bool) -> tuple[str | None, bool]:
    """What a message ANNOUNCES, which is more than its verdict word.

    The SRM gate is part of it because the two can move independently in the
    one direction that matters: a pair that is already INCONCLUSIVE — the
    normal state of every experiment before its horizon — keeps that exact word
    when its sample-ratio gate breaks. Deduping on the word alone would
    therefore swallow the SRM alarm, silencing the urgent signal NTF-2 exists
    to deliver, on the experiments most likely to need it.
    """
    return (verdict, bool(srm_flag))


def should_announce(state: dict[str, Any], verdict: str | None, srm_flag: bool) -> bool:
    """Has anything changed since the last message about this comparison?

    A never-announced comparison (``last_verdict is None``,
    ``notify_count == 0``) always announces: its first readout is news.
    """
    current = announcement_signature(verdict, srm_flag)
    previous = announcement_signature(
        state.get("last_verdict"), bool(state.get("last_srm_flag", False))
    )
    if not state.get("notify_count"):
        # Nothing was ever delivered for this comparison. Do not compare
        # signatures: a first verdict that happens to be None-vs-None would
        # otherwise read as "unchanged" and never announce at all.
        return True
    return current != previous


def is_in_cooldown(
    state: dict[str, Any],
    cooldown_seconds: float | None,
    now: datetime,
) -> bool:
    """Is a REPEAT of the same value still muted?

    Not consulted by :func:`should_announce` — see the module docstring. It is
    the primitive a recurring kind will need, kept here so the rule and its
    exception live in one file.
    """
    if not cooldown_seconds or cooldown_seconds <= 0:
        return False
    last = state.get("last_notified_at")
    if last is None:
        return False
    return (now - last).total_seconds() < float(cooldown_seconds)
