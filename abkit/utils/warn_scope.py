"""Thread-scoped warning capture and suppression (stdlib only).

``warnings.catch_warnings`` is documented as **not thread-safe**: it works by
saving PROCESS-global state — the filter list and ``warnings.showwarning`` — on
entry and restoring it on exit. Two threads whose scopes overlap therefore
interleave their save/restore windows, with three observable failures:

* **cross-attribution** — a warning raised by thread A is appended to thread
  B's recorder, so it is reported against the wrong metric/experiment;
* **loss** — a thread whose recorder was restored out from under it captures
  nothing at all (and an "ignore" filter installed by another thread silences
  it wholesale);
* **a permanent leak** — with A entering before B and leaving before B, B's
  exit restores *A's* recorder, which nobody owns any more: every warning
  raised in the process afterwards is appended to a dead list, silently.

abkit is exposed to all three: the driver fans experiments out over a
``ThreadPoolExecutor`` (``pipeline/driver.py``), and since m10 WP4 the explore
server answers ``POST /recompute`` concurrently — with other recomputes, and
with the Auto-mode ``POST /validate`` whose A/A scoring suppresses the same
warning category.

So abkit's own warning scopes go through this module instead of touching
``catch_warnings`` directly. ONE recorder is installed process-globally by the
**outermost** scope (ref-counted, under a lock) and every scope pushes a frame
onto a **per-thread** stack; the recorder walks the calling thread's stack from
the innermost frame outwards and delegates to the saved ``showwarning`` when no
frame claims the warning. A thread's frames are invisible to every other
thread, so concurrent scopes can neither see, swallow, nor restore each other's
state.

Scope of the guarantee: abkit's OWN warning scopes. A third-party
``catch_warnings(record=True)`` (pytest's ``recwarn``, say) still resets
``showwarning`` to the module default for its window and so displaces the
router process-wide while it is open — nothing inside this module can prevent
that. What it does prevent is abkit scopes doing it to each other, which is the
only case that arises while a server or a run is doing work.

Nothing here changes a number: warning *routing* only.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

__all__ = ["capture_warnings", "suppress_warnings"]


@dataclass
class _Frame:
    """One active scope on one thread. ``sink=None`` ⇒ a suppress frame."""

    category: type[Warning]
    sink: list[warnings.WarningMessage] | None


#: Guards the ref-counted process-global install ONLY — never held while user
#: code (or ``_route``) runs, so it can never serialize a compute.
_install_lock = threading.Lock()
#: Per-thread frame stack: the whole point of this module.
_stacks = threading.local()
_depth = 0
#: The one ``catch_warnings`` that saves/restores the global state for a whole
#: nest, plus the ``showwarning`` it displaced (typed loosely: both are stdlib
#: internals whose exact types are not worth pinning).
_guard: Any = None
_delegate: Any = None
#: what ``showwarning`` was handed back to at the last uninstall — the value a
#: zombie router is evicted in favour of (see :func:`_evict_zombie_router`).
_last_handling: Any = None


def _fallback_show(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: Any = None,
    line: str | None = None,
) -> None:
    """Last-resort delivery: print like the module default would.

    Only reached if the interpreter has no ``_showwarning_orig`` to hand back,
    which no CPython does — but a warning must never be silently dropped just
    because this module could not identify the previous handling.
    """
    stream = file if file is not None else sys.stderr
    if stream is None:
        return
    with contextlib.suppress(OSError):
        stream.write(warnings.formatwarning(message, category, filename, lineno, line))


def _evict_zombie_router() -> None:
    """Hand ``showwarning`` back if it is an UNOWNED router.

    A foreign ``catch_warnings`` window that opens after the router is installed
    and closes after the last scope leaves restores ``_route`` behind our back
    (the stdlib restores the value it snapshotted, not the one we handed over).
    Nobody owns it then: ``_depth`` is 0. Put the last handling we displaced back
    in its place, so warnings resume reaching the application's own handler
    instead of the module default. (Review round 1.)
    """
    if warnings.showwarning is not _route or _depth != 0:
        return
    recovered = _last_handling or getattr(warnings, "_showwarning_orig", None)
    warnings.showwarning = recovered if recovered is not None else _fallback_show


def _current_handling() -> Any:
    """What warnings did BEFORE the router was installed, as a callable.

    ``catch_warnings(record=True)`` — pytest's ``recwarn``/``pytest.warns``, for
    one — records by hooking the private ``_showwarnmsg_impl`` and *resetting*
    ``showwarning`` to the module default, so delegating to ``showwarning``
    alone would print to stderr what an enclosing recorder should collect.
    Prefer the impl hook while ``showwarning`` is untouched; otherwise the
    installed ``showwarning`` is the handling.
    """
    current = warnings.showwarning
    original = getattr(warnings, "_showwarning_orig", None)
    impl = getattr(warnings, "_showwarnmsg_impl", None)
    if current is _route:
        # A ZOMBIE router: some other ``catch_warnings`` window opened after we
        # installed and closed after the last scope left, restoring ``_route``
        # behind our back. Delegating to it would recurse until RecursionError —
        # raised from inside ``warnings.warn``, i.e. out of a live compute. Treat
        # the module default as the handling instead. (Review round 1.)
        return original if original is not None else _fallback_show
    if impl is not None and original is not None and current is original:

        def _via_impl(
            message: Warning | str,
            category: type[Warning],
            filename: str,
            lineno: int,
            file: Any = None,
            line: str | None = None,
        ) -> None:
            impl(warnings.WarningMessage(message, category, filename, lineno, file, line))

        return _via_impl
    return current


def _thread_stack() -> list[_Frame]:
    stack: list[_Frame] | None = getattr(_stacks, "stack", None)
    if stack is None:
        stack = []
        _stacks.stack = stack
    return stack


def _route(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: Any = None,
    line: str | None = None,
) -> None:
    """The one installed ``showwarning``: dispatch to the CALLING thread's frames.

    Innermost frame first. A capture frame claims every warning (verbatim
    ``record=True`` semantics — it records the category it was asked about and
    swallows the rest just as ``catch_warnings(record=True)`` does); a suppress
    frame claims only its own category and lets anything else fall through to
    an enclosing frame. With no frame on this thread the warning belongs to
    whoever was handling warnings before the scope opened.
    """
    stack: list[_Frame] | None = getattr(_stacks, "stack", None)
    if stack:
        for frame in reversed(stack):
            if frame.sink is not None:
                frame.sink.append(
                    warnings.WarningMessage(message, category, filename, lineno, file, line)
                )
                return
            if isinstance(category, type) and issubclass(category, frame.category):
                return
    # No frame claimed it. ``_delegate`` is None only if the router outlived its
    # own install (a foreign ``catch_warnings`` restored it after the last scope
    # left) — resolve the handling live rather than DROP the warning, which is
    # how a zombie router becomes a silent process-wide blackhole. (Review round 1.)
    _evict_zombie_router()
    delegate = _delegate or _current_handling()
    if delegate is not None:
        delegate(message, category, filename, lineno, file, line)


def _install() -> None:
    """Take over ``showwarning`` for the whole nest. Caller holds ``_install_lock``."""
    global _guard, _delegate
    # BEFORE the guard snapshots the global state — otherwise a zombie router
    # would be snapshotted as the thing to restore, and outlive us again.
    _evict_zombie_router()
    # ONE catch_warnings for the whole nest: it snapshots the filter list and
    # showwarning here and restores both when the LAST scope leaves, so no
    # individual scope ever writes global state a peer thread owns.
    guard = warnings.catch_warnings()
    guard.__enter__()
    _guard = guard
    _delegate = _current_handling()
    # exactly what the stdlib's own catch_warnings(record=True) does
    warnings.showwarning = _route


def _uninstall() -> None:
    """Hand ``showwarning`` back. Caller holds ``_install_lock``.

    Order matters: restore FIRST, clear ``_delegate`` after. Clearing it while
    the router is still installed opens a window in which a frameless thread's
    warning reaches ``_route`` with nothing to hand it to (review round 1).
    """
    global _guard, _delegate, _last_handling
    guard, _guard = _guard, None
    if guard is not None:
        guard.__exit__(None, None, None)  # restores the filter list AND showwarning
    if warnings.showwarning is not _route:
        _last_handling = warnings.showwarning
    _delegate = None


@contextmanager
def _scope(frame: _Frame) -> Iterator[_Frame]:
    global _depth
    if not (isinstance(frame.category, type) and issubclass(frame.category, Warning)):
        # Rejected BEFORE any global is touched: a category ``filterwarnings``
        # refuses used to orphan a half-installed router (review round 1).
        raise TypeError(f"category must be a Warning subclass, got {frame.category!r}")
    with _install_lock:
        installed_here = _depth == 0
        if installed_here:
            _install()
        try:
            # "always" so the per-module registry cannot dedupe repeats BEFORE
            # they reach the recorder — routing decides what happens to a
            # warning, never a filter (an "ignore" filter would silence a
            # concurrent thread too).
            warnings.filterwarnings("always", category=frame.category)
        except BaseException:
            # the install is all-or-nothing: never leave an unowned router behind
            if installed_here:
                _uninstall()
            raise
        _depth += 1
    stack = _thread_stack()
    stack.append(frame)
    try:
        yield frame
    finally:
        stack.pop()
        with _install_lock:
            _depth -= 1
            if _depth == 0:
                _uninstall()


@contextmanager
def capture_warnings(
    category: type[Warning] = Warning,
) -> Iterator[list[warnings.WarningMessage]]:
    """Record every warning raised in THIS thread inside the block.

    The drop-in for ``catch_warnings(record=True)`` + ``simplefilter("always",
    category)``: the yielded list (readable after the block, like the
    ``as caught`` list) receives one ``WarningMessage`` per warning, nothing
    propagates out of the block, and **other threads are unaffected**.
    ``category`` sets the filter that keeps repeats from being deduped; the
    recording itself is category-blind, exactly as ``record=True`` is.
    """
    frame = _Frame(category=category, sink=[])
    with _scope(frame) as active:
        sink = active.sink
        assert sink is not None
        yield sink


@contextmanager
def suppress_warnings(category: type[Warning]) -> Iterator[None]:
    """Drop ``category`` warnings raised in THIS thread inside the block.

    The drop-in for ``catch_warnings()`` + ``simplefilter("ignore", category)``
    minus the process-global blast radius: a concurrent thread's capture keeps
    seeing its own warnings. Other categories fall through to the enclosing
    scope (or the process default).
    """
    with _scope(_Frame(category=category, sink=None)):
        yield
