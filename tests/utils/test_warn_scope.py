"""m10 WP4: the thread-scoped warning capture/suppression primitive.

``POST /recompute`` stopped being serialized in WP4, which made three latent
hazards live at once — the same three the parallel-experiment driver already
had. Each gets a test that FAILS against the stdlib's ``catch_warnings`` and
passes against ``abkit.utils.warn_scope``:

1. cross-attribution — thread A's warning recorded in thread B's list;
2. suppression bleed — one thread's "ignore" filter silencing another
   thread's capture (Auto-mode ``/validate`` × ``/recompute``);
3. the permanent leak — interleaved exits leaving a dead recorder installed,
   after which every warning in the process vanishes silently.

The interleaving is driven by events, never by sleeps: the overlap is forced,
so a green run means the property holds rather than that the race did not fire.
"""

from __future__ import annotations

import contextlib
import threading
import warnings

import pytest

from abkit.utils.warn_scope import capture_warnings, suppress_warnings


class Alpha(UserWarning):
    """Category one thread cares about."""


class Beta(UserWarning):
    """A different category, for the fall-through cases."""


def _messages(sink: list[warnings.WarningMessage]) -> list[str]:
    return [str(w.message) for w in sink]


class TestSingleThread:
    """The drop-in properties: same shape as catch_warnings(record=True)."""

    def test_capture_records_and_does_not_propagate(self):
        seen: list[str] = []
        with _delegating_to(seen), capture_warnings(Alpha) as caught:
            warnings.warn("inside", Alpha, stacklevel=1)
        assert _messages(caught) == ["inside"]
        assert seen == []  # captured, not printed

    def test_capture_is_category_blind_like_record_true(self):
        """``record=True`` records everything raised inside; so does a frame."""
        with capture_warnings(Alpha) as caught:
            warnings.warn("a", Alpha, stacklevel=1)
            warnings.warn("b", Beta, stacklevel=1)
        assert _messages(caught) == ["a", "b"]

    def test_repeats_are_not_deduped(self):
        """The registry must not swallow the second identical guard — the
        reason the scope installs an "always" filter for its category."""

        def warn_twice() -> None:
            for _ in range(2):
                warnings.warn("same message", Alpha, stacklevel=1)

        with capture_warnings(Alpha) as caught:
            warn_twice()
        assert _messages(caught) == ["same message", "same message"]

    def test_suppress_drops_its_category_and_passes_the_rest(self):
        seen: list[str] = []
        with _delegating_to(seen), suppress_warnings(Alpha):
            warnings.warn("dropped", Alpha, stacklevel=1)
            warnings.warn("kept", Beta, stacklevel=1)
        assert seen == ["kept"]

    def test_nested_suppress_inside_capture(self):
        with capture_warnings(Alpha) as caught:
            with suppress_warnings(Alpha):
                warnings.warn("dropped", Alpha, stacklevel=1)
                warnings.warn("falls through to the capture", Beta, stacklevel=1)
            warnings.warn("captured", Alpha, stacklevel=1)
        assert _messages(caught) == ["falls through to the capture", "captured"]

    def test_state_is_restored_after_the_outermost_scope(self):
        before = warnings.showwarning
        filters_before = warnings.filters[:]
        with capture_warnings(Alpha):
            assert warnings.showwarning is not before  # the router is installed
        assert warnings.showwarning is before
        assert warnings.filters == filters_before

    def test_an_exception_still_restores(self):
        before = warnings.showwarning
        with pytest.raises(RuntimeError):
            with capture_warnings(Alpha):
                raise RuntimeError("boom")
        assert warnings.showwarning is before

    def test_delegation_reaches_the_previous_handler(self):
        """A warning raised on a thread with NO frame belongs to whoever was
        handling warnings before the scope opened — even while another scope
        is open elsewhere."""
        seen: list[str] = []
        with _delegating_to(seen):
            done = threading.Event()

            def worker() -> None:
                with capture_warnings(Alpha):
                    done.wait(timeout=10)

            thread = threading.Thread(target=worker)
            thread.start()
            try:
                warnings.warn("main thread, no frame", Alpha, stacklevel=1)
            finally:
                done.set()
                thread.join(timeout=10)
            assert not thread.is_alive()
        assert seen == ["main thread, no frame"]


class TestConcurrency:
    """The three hazards, each with the overlap forced by events."""

    def test_two_captures_never_see_each_others_warnings(self):
        """Hazard 1. Ordering: A enters, B enters, A warns, B warns, A exits,
        B exits — with the stdlib, A's warning lands in B's recorder (B
        installed last), which is how a guard gets reported against the wrong
        metric."""
        sinks: dict[str, list[str]] = {}
        a_entered, b_entered, a_warned = (threading.Event() for _ in range(3))

        def thread_a() -> None:
            with capture_warnings(Alpha) as caught:
                a_entered.set()
                assert b_entered.wait(timeout=10)
                warnings.warn("from A", Alpha, stacklevel=1)
                a_warned.set()
                sinks["a"] = _messages(caught)

        def thread_b() -> None:
            assert a_entered.wait(timeout=10)
            with capture_warnings(Alpha) as caught:
                b_entered.set()
                assert a_warned.wait(timeout=10)
                warnings.warn("from B", Alpha, stacklevel=1)
                sinks["b"] = _messages(caught)

        _run_both(thread_a, thread_b)
        assert sinks == {"a": ["from A"], "b": ["from B"]}

    def test_a_suppressing_thread_does_not_silence_a_capturing_one(self):
        """Hazard 2 — Auto-mode ``/validate`` (which suppresses the per-split
        A/A guards) running concurrently with a ``/recompute`` that must still
        report its own guard in the reply.

        Order matters: the CAPTURE opens first and the suppressor is open
        ACROSS the capturer's warn. With the stdlib the suppressor's "ignore"
        filter is process-global by then, so the capture comes back empty
        (verified by reverting the module — review round 1 caught the original
        ordering, which the stdlib survived).
        """
        captured: list[str] = []
        capturing, suppressing, captured_done = (threading.Event() for _ in range(3))

        def capturer() -> None:
            with capture_warnings(Alpha) as caught:
                capturing.set()
                assert suppressing.wait(timeout=10)
                warnings.warn("the real guard", Alpha, stacklevel=1)
                captured.extend(_messages(caught))
            captured_done.set()

        def suppressor() -> None:
            assert capturing.wait(timeout=10)
            with suppress_warnings(Alpha):
                warnings.warn("A/A spam", Alpha, stacklevel=1)
                suppressing.set()
                assert captured_done.wait(timeout=10)
                warnings.warn("more A/A spam", Alpha, stacklevel=1)

        _run_both(capturer, suppressor)
        assert captured == ["the real guard"]

    def test_interleaved_exits_do_not_leave_a_dead_recorder_installed(self):
        """Hazard 3, the worst one: A enters before B and leaves before B, so
        with the stdlib B's exit restores A's recorder — nobody owns it, and
        every later warning in the process is appended to a list nobody reads.
        A silent, permanent loss of every stats warning for the session.

        Asserted through DELIVERY, not through which hook holds what, and the
        ambient sink is installed BEFORE the interleave — installing it after
        would overwrite the leaked recorder and repair the very thing under
        test. (Both corrections come from review round 1: the original assertion
        — ``showwarning is original`` — held against the stdlib too, which
        records via the private ``_showwarnmsg_impl``.)
        """
        original = warnings.showwarning
        a_entered, b_entered, a_exited = (threading.Event() for _ in range(3))

        def thread_a() -> None:
            with capture_warnings(Alpha):
                a_entered.set()
                assert b_entered.wait(timeout=10)
            a_exited.set()

        def thread_b() -> None:
            assert a_entered.wait(timeout=10)
            with capture_warnings(Alpha):
                b_entered.set()
                assert a_exited.wait(timeout=10)

        delivered: list[str] = []
        with _ambient_sink(delivered):
            _run_both(thread_a, thread_b)
            assert warnings.showwarning is original
            warnings.warn("after the interleave", Alpha, stacklevel=1)
        assert delivered == ["after the interleave"]

    def test_the_stdlib_really_does_fail_these(self):
        """The hazard is real, not theoretical — if this test ever fails
        because ``catch_warnings`` became thread-safe, ``warn_scope`` can be
        reconsidered. It is the reason the module exists.

        Both failures are asserted through BEHAVIOR, not through which private
        hook the stdlib happens to swap (3.12 records via
        ``_showwarnmsg_impl``, leaving ``showwarning`` at the default).
        """
        a_seen: list[str] = []
        a_entered, b_entered, a_warned, a_exited = (threading.Event() for _ in range(4))

        def thread_a() -> None:
            nonlocal a_recorder
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", Alpha)
                a_entered.set()
                assert b_entered.wait(timeout=10)
                warnings.warn("from A", Alpha, stacklevel=1)
                a_warned.set()
                a_seen.extend(_messages(caught))
                a_recorder = caught  # the SAME list object A recorded into
            a_exited.set()

        def thread_b() -> None:
            assert a_entered.wait(timeout=10)
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always", Alpha)
                b_entered.set()
                assert a_warned.wait(timeout=10)
                assert a_exited.wait(timeout=10)

        a_recorder: list[warnings.WarningMessage] | None = None
        # The outer non-record catch_warnings snapshots (and restores) the filter
        # list, showwarning AND the private impl hook — the only public way to
        # clean up after a leak this test deliberately provokes.
        with warnings.catch_warnings():
            _run_both(thread_a, thread_b)
            # (1) cross-attribution: A's own warning never reached A's recorder.
            assert a_seen == []
            # (2) the leak: A returned, yet a warning raised now — by the MAIN
            # thread, with no scope anywhere — is still appended to A's dead list.
            assert a_recorder is not None
            before = len(a_recorder)
            warnings.warn("raised long after A returned", Alpha, stacklevel=1)
            assert len(a_recorder) == before + 1


class TestHostileGlobalState:
    """Someone else owns ``warnings`` too. (All from review round 1.)"""

    def test_a_foreign_window_that_outlives_the_scope_cannot_wedge_warnings(self):
        """A plain ``catch_warnings()`` that opens INSIDE a scope and closes
        after it restores the router behind our back — the stdlib restores what
        it snapshotted, not what we handed over. Delegating to that zombie would
        recurse until ``RecursionError``, raised out of ``warnings.warn`` — i.e.
        out of a live compute — after silently swallowing everything first."""
        delivered: list[str] = []
        opened, foreign_in, scope_gone, foreign_done = (threading.Event() for _ in range(4))

        with _delegating_to(delivered):

            def foreign() -> None:
                assert opened.wait(timeout=10)
                with warnings.catch_warnings():  # snapshots the router…
                    foreign_in.set()
                    assert scope_gone.wait(timeout=10)
                foreign_done.set()  # …and restores it after we handed it back

            def scoped() -> None:
                with capture_warnings(Alpha):
                    opened.set()
                    assert foreign_in.wait(timeout=10)
                scope_gone.set()

            _run_both(foreign, scoped)
            assert foreign_done.is_set()

            # no exception, and the warning still reaches the app's handler
            warnings.warn("after the zombie", Alpha, stacklevel=1)
            with capture_warnings(Alpha) as caught:
                warnings.warn("own-thread", Alpha, stacklevel=1)

        assert delivered == ["after the zombie"]
        assert _messages(caught) == ["own-thread"]

    def test_a_bad_category_leaves_no_half_installed_router(self):
        """``filterwarnings`` validates its category; before review round 1 that
        rejection happened AFTER the router was installed and before the
        try/finally, orphaning it permanently."""
        import abkit.utils.warn_scope as scope_mod

        before = warnings.showwarning
        with pytest.raises(TypeError):
            with capture_warnings("not a warning class"):  # type: ignore[arg-type]
                pass
        assert warnings.showwarning is before
        assert scope_mod._depth == 0
        seen: list[str] = []
        with _delegating_to(seen):
            warnings.warn("still delivered", Alpha, stacklevel=1)
        assert seen == ["still delivered"]

    def test_the_filter_list_does_not_grow_with_scope_count(self):
        """500 scopes must not leave 500 filters behind — a long explore session
        would otherwise walk an ever-longer list on every warning."""
        before = len(warnings.filters)
        for _ in range(500):
            with capture_warnings(Alpha):
                pass
        assert len(warnings.filters) == before

    def test_the_frame_stack_does_not_leak_across_requests_on_one_thread(self):
        import abkit.utils.warn_scope as scope_mod

        for _ in range(50):
            with capture_warnings(Alpha):
                with suppress_warnings(Beta):
                    pass
        assert scope_mod._thread_stack() == []


class TestTheAbkitCallSites:
    """The two production scopes, exercised through their real entry points."""

    def test_concurrent_compares_keep_their_own_warnings(self):
        """``tuning.recompute._compare`` — the explore reply's warning chips.

        A must warn while B's scope is OPEN (the interleaving the stdlib loses:
        B installed its recorder last, so A's guard lands in B's list and A's
        reply reports none). Review round 1: the first version let B finish
        first, which ``catch_warnings`` survives.
        """
        from abkit.stats import AbkitStatsWarning
        from abkit.tuning.recompute import _compare

        out: dict[str, list[str]] = {}
        a_inside, b_inside, a_warned = (threading.Event() for _ in range(3))

        class Warny:
            def __init__(self, tag: str, enter: threading.Event, wait_for: threading.Event) -> None:
                self.tag, self.enter, self.wait_for = tag, enter, wait_for

            def compare_pair(self, group_1: object, group_2: object) -> str:
                self.enter.set()
                assert self.wait_for.wait(timeout=10)
                warnings.warn(f"guard {self.tag}", AbkitStatsWarning, stacklevel=1)
                if self.tag == "a":
                    a_warned.set()
                return f"result-{self.tag}"

        def thread_a() -> None:
            _, messages = _compare(Warny("a", a_inside, b_inside), None, None)  # type: ignore[arg-type]
            out["a"] = messages

        def thread_b() -> None:
            assert a_inside.wait(timeout=10)
            _, messages = _compare(Warny("b", b_inside, a_warned), None, None)  # type: ignore[arg-type]
            out["b"] = messages

        _run_both(thread_a, thread_b)
        assert out == {"a": ["guard a"], "b": ["guard b"]}

    def test_validate_scoring_suppression_is_thread_local(self):
        """``validate.scoring.suppress_resample_warnings`` — Auto mode's
        per-split silence must not reach a concurrent explore capture.

        The capture opens FIRST and the suppression is open across its warn:
        with the stdlib the "ignore" filter is global by then and the explore
        reply loses its guard (review round 1 — the reverse order passes).
        """
        from abkit.stats import AbkitStatsWarning
        from abkit.tuning.recompute import _compare
        from abkit.validate.scoring import suppress_resample_warnings

        capturing, suppressing, captured_done = (threading.Event() for _ in range(3))
        out: dict[str, list[str]] = {}

        @suppress_resample_warnings
        def scoring_like() -> None:
            warnings.warn("per-split guard", AbkitStatsWarning, stacklevel=1)
            suppressing.set()
            assert captured_done.wait(timeout=10)
            warnings.warn("another per-split guard", AbkitStatsWarning, stacklevel=1)

        class Warny:
            def compare_pair(self, group_1: object, group_2: object) -> str:
                capturing.set()
                assert suppressing.wait(timeout=10)
                warnings.warn("the real guard", AbkitStatsWarning, stacklevel=1)
                return "result"

        def explore_side() -> None:
            _, messages = _compare(Warny(), None, None)  # type: ignore[arg-type]
            out["explore"] = messages
            captured_done.set()

        def scoring_side() -> None:
            assert capturing.wait(timeout=10)
            scoring_like()

        _run_both(explore_side, scoring_side)
        assert out == {"explore": ["the real guard"]}


# -- helpers ------------------------------------------------------------------


@contextlib.contextmanager
def _ambient_sink(sink: list[str]):
    """Collect warnings at the level a LEAKED recorder would steal them from.

    Deliberately hooks the private ``_showwarnmsg_impl`` rather than
    ``showwarning``: an overridden ``showwarning`` takes precedence in
    ``_showwarnmsg``, so it would receive the warning even when a dead
    ``catch_warnings(record=True)`` recorder is still installed — masking
    exactly the leak under test. Entering ``pytest.warns`` here would mask it
    too (it installs its own impl for its window).
    """
    saved = warnings._showwarnmsg_impl  # type: ignore[attr-defined]
    warnings._showwarnmsg_impl = lambda msg: sink.append(str(msg.message))  # type: ignore[attr-defined]
    try:
        yield sink
    finally:
        warnings._showwarnmsg_impl = saved  # type: ignore[attr-defined]


class _delegating_to:
    """Install a list-collecting ``showwarning`` for the duration of the block.

    Deliberately NOT ``catch_warnings``: these tests assert what the scope
    delegates to, so the baseline handler must be one we installed ourselves
    and restore ourselves.
    """

    def __init__(self, sink: list[str]) -> None:
        self.sink = sink
        self._saved: object = None

    def __enter__(self) -> _delegating_to:
        self._saved = warnings.showwarning

        def collect(message, category, filename, lineno, file=None, line=None):  # type: ignore[no-untyped-def]
            self.sink.append(str(message))

        warnings.showwarning = collect
        return self

    def __exit__(self, *exc: object) -> None:
        warnings.showwarning = self._saved  # type: ignore[assignment]


def _run_both(first, second, timeout: float = 15.0) -> None:  # type: ignore[no-untyped-def]
    """Run two thread bodies to completion, re-raising whatever they raised."""
    errors: list[BaseException] = []

    def wrap(fn):  # type: ignore[no-untyped-def]
        def run() -> None:
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 — re-raised below
                errors.append(exc)

        return run

    threads = [threading.Thread(target=wrap(fn)) for fn in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)
    alive = [t for t in threads if t.is_alive()]
    assert not alive, f"{len(alive)} thread(s) never finished — deadlock or missed event"
    if errors:
        raise errors[0]


def test_the_teardown_window_never_drops_a_frameless_warning():
    """``_uninstall`` restores ``showwarning`` BEFORE clearing the delegate.

    The other order leaves the router installed with nothing to delegate to,
    and a warning raised by a frameless thread in that window is dropped
    (review round 1, found by instrumenting the guard's ``__exit__``).
    """
    import abkit.utils.warn_scope as scope_mod

    delivered: list[str] = []
    observed: list[str] = []

    class _WatchingGuard(warnings.catch_warnings):
        def __exit__(self, *exc):  # type: ignore[no-untyped-def]
            # exactly the moment the old order had already cleared _delegate
            scope_mod._route("mid-teardown", Alpha, __file__, 0)
            observed.append("ran")
            return super().__exit__(*exc)

    original_guard = warnings.catch_warnings
    with _delegating_to(delivered):
        scope_mod.warnings.catch_warnings = _WatchingGuard  # type: ignore[assignment]
        try:
            with capture_warnings(Alpha):
                pass
        finally:
            scope_mod.warnings.catch_warnings = original_guard  # type: ignore[assignment]
    assert observed == ["ran"]
    assert delivered == ["mid-teardown"]
