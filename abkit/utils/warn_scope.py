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
#: nest, plus the ``showwarning`` it displaced. Typed loosely because mypy
#: rejects assigning to a module-level function (hence the ``setattr`` below).
_guard: Any = None
_delegate: Any = None


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
    delegate = _delegate
    if delegate is not None:
        delegate(message, category, filename, lineno, file, line)


@contextmanager
def _scope(frame: _Frame) -> Iterator[_Frame]:
    global _depth, _guard, _delegate
    with _install_lock:
        if _depth == 0:
            # ONE catch_warnings for the whole nest: it snapshots the filter list
            # and showwarning here and restores both when the LAST scope leaves,
            # so no individual scope ever writes global state a peer thread owns.
            guard = warnings.catch_warnings()
            guard.__enter__()
            _guard = guard
            _delegate = _current_handling()
            # exactly what the stdlib's own catch_warnings(record=True) does
            warnings.showwarning = _route
        # "always" so the per-module registry cannot dedupe repeats BEFORE they
        # reach the recorder — routing decides what happens to a warning, never
        # a filter (an "ignore" filter would silence a concurrent thread too).
        warnings.filterwarnings("always", category=frame.category)
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
                guard, _guard = _guard, None
                _delegate = None
                if guard is not None:
                    # restores BOTH the filter list and showwarning
                    guard.__exit__(None, None, None)


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
