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
        report its own guard in the reply."""
        captured: list[str] = []
        suppressing, captured_done = threading.Event(), threading.Event()

        def suppressor() -> None:
            with suppress_warnings(Alpha):
                warnings.warn("A/A spam", Alpha, stacklevel=1)
                suppressing.set()
                assert captured_done.wait(timeout=10)
                warnings.warn("more A/A spam", Alpha, stacklevel=1)

        def capturer() -> None:
            assert suppressing.wait(timeout=10)
            with capture_warnings(Alpha) as caught:
                warnings.warn("the real guard", Alpha, stacklevel=1)
                captured.extend(_messages(caught))
            captured_done.set()

        _run_both(suppressor, capturer)
        assert captured == ["the real guard"]

    def test_interleaved_exits_do_not_leave_a_dead_recorder_installed(self):
        """Hazard 3, the worst one: A enters before B and leaves before B, so
        with the stdlib B's exit restores A's recorder — nobody owns it, and
        every later warning in the process is appended to a list nobody reads.
        A silent, permanent loss of every stats warning for the session."""
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

        _run_both(thread_a, thread_b)
        assert warnings.showwarning is original

        seen: list[str] = []
        with _delegating_to(seen):
            warnings.warn("after the interleave", Alpha, stacklevel=1)
        assert seen == ["after the interleave"]

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


class TestTheAbkitCallSites:
    """The two production scopes, exercised through their real entry points."""

    def test_concurrent_compares_keep_their_own_warnings(self):
        """``tuning.recompute._compare`` — the explore reply's warning chips."""
        from abkit.stats import AbkitStatsWarning
        from abkit.tuning.recompute import _compare

        out: dict[str, list[str]] = {}
        first_in, second_warned = threading.Event(), threading.Event()

        class Warny:
            def __init__(self, tag: str, before: threading.Event | None) -> None:
                self.tag, self.before = tag, before

            def compare_pair(self, group_1: object, group_2: object) -> str:
                if self.before is not None:
                    first_in.set()
                    assert second_warned.wait(timeout=10)
                warnings.warn(f"guard {self.tag}", AbkitStatsWarning, stacklevel=1)
                return f"result-{self.tag}"

        def slow() -> None:
            _, messages = _compare(Warny("slow", first_in), None, None)  # type: ignore[arg-type]
            out["slow"] = messages

        def quick() -> None:
            assert first_in.wait(timeout=10)
            _, messages = _compare(Warny("quick", None), None, None)  # type: ignore[arg-type]
            out["quick"] = messages
            second_warned.set()

        _run_both(slow, quick)
        assert out == {"slow": ["guard slow"], "quick": ["guard quick"]}

    def test_validate_scoring_suppression_is_thread_local(self):
        """``validate.scoring.suppress_resample_warnings`` — Auto mode's
        per-split silence must not reach a concurrent explore capture."""
        from abkit.stats import AbkitStatsWarning
        from abkit.tuning.recompute import _compare
        from abkit.validate.scoring import suppress_resample_warnings

        inside, captured_done = threading.Event(), threading.Event()
        out: dict[str, list[str]] = {}

        @suppress_resample_warnings
        def scoring_like() -> None:
            warnings.warn("per-split guard", AbkitStatsWarning, stacklevel=1)
            inside.set()
            assert captured_done.wait(timeout=10)
            warnings.warn("another per-split guard", AbkitStatsWarning, stacklevel=1)

        class Warny:
            def compare_pair(self, group_1: object, group_2: object) -> str:
                warnings.warn("the real guard", AbkitStatsWarning, stacklevel=1)
                return "result"

        def explore_side() -> None:
            assert inside.wait(timeout=10)
            _, messages = _compare(Warny(), None, None)  # type: ignore[arg-type]
            out["explore"] = messages
            captured_done.set()

        _run_both(scoring_like, explore_side)
        assert out == {"explore": ["the real guard"]}


# -- helpers ------------------------------------------------------------------


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
