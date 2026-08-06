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
belongs to the RECURRING kinds (m12 NTF-5's ``stale`` and ``calibration_red``),
whose condition legitimately persists across runs with an identical value —
:func:`should_announce_recurring` is where the two rules meet.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

#: What the state store answers for a comparison nobody has announced yet.
EMPTY_STATE: dict[str, Any] = {
    "last_verdict": None,
    "last_srm_flag": False,
    "last_rollup": None,
    "last_notified_at": None,
    "notify_count": 0,
}


def rollup_signature(leader: str | None, separation: str | None) -> str | None:
    """The decision-layer half of the signature (m14 DEC-4).

    **The leader is in the signature only when the rollup SEPARATED it**, and
    that is not a simplification — it is the difference between a decision and
    a coin flip. `leader` is a raw argmax over point estimates, the one dedup
    term the readout's stabilization scan does not smooth; under `co_leaders`
    the rollup is *saying these arms are indistinguishable*, so recording which
    of them happened to poll higher makes the key flip on about half of all runs
    for genuinely tied arms — a message every run, which is exactly what NTF-3
    exists to prevent. Under `separated` the ordering is a claim the readout
    stands behind, and a flip from B to C there is the ship decision changing.

    ``None`` for a readout with no rollup at all. Reachable only from a caller
    that has none to offer — every `evaluate()` readout carries one per main
    comparison — so it is the "this term does not apply" value rather than a
    state the pipeline produces.
    """
    if leader is None and separation is None:
        return None
    if separation != "separated":
        return f"|{separation or ''}"
    return f"{leader or ''}|{separation}"


def announcement_signature(
    verdict: str | None,
    srm_flag: bool,
    rollup: str | None = None,
) -> tuple[str | None, bool, str | None]:
    """What a message ANNOUNCES, which is more than its verdict word.

    The SRM gate is part of it because the two can move independently in the
    one direction that matters: a pair that is already INCONCLUSIVE — the
    normal state of every experiment before its horizon — keeps that exact word
    when its sample-ratio gate breaks. Deduping on the word alone would
    therefore swallow the SRM alarm, silencing the urgent signal NTF-2 exists
    to deliver, on the experiments most likely to need it.

    **The rollup identity is part of it for the same reason, one arm-count up**
    (m14 DEC-4). At three arms the leader can flip from B to C while every
    verdict word stays ``WIN``: the ship decision changed and, without this
    term, nobody would be told. Separation rides along because
    "we could not compare them" → "they are tied" is also a changed decision.
    This is NTF-3's own trap — deduping on the verdict word alone — in its
    multi-arm form.

    **It cannot fire at two arms**, structurally: with one treatment the leader
    is that arm iff its verdict is WIN and the separation follows from the same
    fact, so both terms are functions of the word already in the signature.
    """
    return (verdict, bool(srm_flag), rollup)


def should_announce(
    state: dict[str, Any],
    verdict: str | None,
    srm_flag: bool,
    rollup: str | None = None,
) -> bool:
    """Has anything changed since the last message about this comparison?

    A never-announced comparison (``last_verdict is None``,
    ``notify_count == 0``) always announces: its first readout is news.
    """
    if not state.get("notify_count"):
        # Nothing was ever delivered for this comparison. Do not compare
        # signatures: a first verdict that happens to be None-vs-None would
        # otherwise read as "unchanged" and never announce at all.
        return True

    # A row announced BEFORE `0.9.0` stored no rollup, and every readout since
    # carries one — so comparing the two would make the first `0.9.0` run
    # re-announce every comparison in the project, most of them with a message
    # textually identical to the one already delivered. The term is dropped for
    # exactly those rows; the NEXT announcement writes one and dedup resumes at
    # full strength. (An announced row can never have signed `None` itself: the
    # only `None` producer is a caller with no rollup, and the verdict path
    # always has one.)
    previous_rollup = state.get("last_rollup")
    compare_rollup = previous_rollup is not None
    current = announcement_signature(verdict, srm_flag, rollup if compare_rollup else None)
    previous = announcement_signature(
        state.get("last_verdict"),
        bool(state.get("last_srm_flag", False)),
        previous_rollup,
    )
    return current != previous


def recurring_signature(items: Sequence[str]) -> str:
    """What a RECURRING signal announces: WHICH things are wrong, not how badly.

    Sorted and de-duplicated so the answer does not depend on enumeration
    order, and deliberately free of magnitudes: a backlog's lag grows every
    run and an A/A cell's FPR moves with every resample, so a signature
    carrying either would differ every time and dedup nothing at all. The
    empty string is the honest "nothing is wrong" value — it is a signature
    like any other, so the transition out of it announces.
    """
    return "\n".join(sorted(set(items)))


def should_announce_recurring(
    state: dict[str, Any],
    signature: str,
    cooldown_seconds: float | None,
    now: datetime,
) -> bool:
    """Is a recurring condition due to be announced again?

    Two rules, in D2's order and for D2's reason. A CHANGED signature always
    announces, cooldown or not — a timer must never swallow "a second metric
    just fell behind". An UNCHANGED one announces only once its cooldown has
    elapsed, which is what separates these kinds from a verdict: the condition
    is still true on the next run, and re-sending it every run is the noise
    NTF-3 exists to prevent.

    ``None`` — the default — is "never repeat", and is NOT the same as ``0``:
    :func:`is_in_cooldown` answers False for both (neither mutes anything), so
    deferring to it alone would re-announce an unchanged condition on every
    single run, which is the exact behaviour the default must not have. Zero
    keeps its literal reading: a window that has always already elapsed.
    """
    if not state.get("notify_count"):
        return True
    if state.get("last_verdict") != signature:
        return True
    if cooldown_seconds is None:
        return False
    return not is_in_cooldown(state, cooldown_seconds, now)


def is_in_cooldown(
    state: dict[str, Any],
    cooldown_seconds: float | None,
    now: datetime,
) -> bool:
    """Is a REPEAT of the same value still muted?

    Never consulted by :func:`should_announce` (a verdict is not recurring —
    see the module docstring); it is the second half of
    :func:`should_announce_recurring`, kept here so the rule and its exception
    live in one file.
    """
    if not cooldown_seconds or cooldown_seconds <= 0:
        return False
    last = state.get("last_notified_at")
    if last is None:
        return False
    return (now - last).total_seconds() < float(cooldown_seconds)
