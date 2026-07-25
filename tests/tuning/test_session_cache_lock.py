"""m10 WP4: the Tier-S cache discipline — one lock, one entry point.

``POST /recompute`` stopped being serialized against ``POST /reload``, so the
session cache became genuinely shared mutable state. Two properties have to
hold, and both are pinned here so they cannot regress into "it looked fine":

* **the (entry, lookback) pair is atomic.** ``/reload`` replaces the rendered
  entry and the ``covariate_lookback`` tag it was rendered with; a reader that
  read them separately could pair a FRESH entry with the PREVIOUS tag — the
  Tier-S gate would then accept a 14d-rendered cutoff as a 7d one and label a
  wrong number "exact". The tear is *demonstrated* below against the old
  two-read shape, then shown impossible through the accessor.
* **every access goes through the accessors** (the AST gate) — the m10 WP1
  lesson: a discipline that has to be remembered at nine call sites is a
  discipline that will be forgotten at the tenth.
"""

from __future__ import annotations

import ast
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from abkit.loaders.metric_loader import MetricLoadResult
from abkit.tuning.session import ExploreSession, loaded_value_count

CUTOFF = datetime(2024, 1, 8)


def _entry(size: int) -> MetricLoadResult:
    """A minimal load result: only its value count and identity matter here."""
    return MetricLoadResult(
        metric="arpu",
        units_by_variant={"control": np.arange(size)},
        roles_by_variant={"control": {"value": np.arange(size, dtype=float)}},
        strata_by_variant={"control": None},
    )


class _HookedLock:
    """A ``threading.Lock`` that runs a callback after every release.

    The instrument for "was the pair read in ONE critical section?": it lets a
    writer complete between two reads that only *look* atomic.
    """

    def __init__(self, on_release) -> None:  # type: ignore[no-untyped-def]
        self._lock = threading.Lock()
        self._on_release = on_release

    def acquire(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self._lock.acquire(*args, **kwargs)

    def release(self) -> None:
        self._lock.release()
        self._on_release()

    def locked(self) -> bool:
        return self._lock.locked()

    def __enter__(self) -> bool:
        return self._lock.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


def _session() -> ExploreSession:
    return ExploreSession(
        experiment=None,  # type: ignore[arg-type]
        project=None,  # type: ignore[arg-type]
        grid=None,  # type: ignore[arg-type]
        series_by_metric={},
    )


class TestPairAtomicity:
    def test_the_unlocked_two_read_shape_tears_but_the_accessor_cannot(self):
        """The gate that can fail: it reproduces the tear, then proves the fix.

        A dict subclass freezes the writer *between* its two installs — exactly
        the window a concurrent Tier-S read used to fall into.
        """
        session = _session()
        session.install_cutoff("arpu", CUTOFF, _entry(4), "7d")

        installed, torn_read_done = threading.Event(), threading.Event()

        class _FreezeAfterEntry(dict):
            def __setitem__(self, key: object, value: object) -> None:
                super().__setitem__(key, value)
                if not installed.is_set():
                    installed.set()
                    assert torn_read_done.wait(timeout=10)

        # the writer's cache dict pauses after the entry lands, before the tag
        session.cache = _FreezeAfterEntry(session.cache)  # type: ignore[assignment]

        writer = threading.Thread(
            target=lambda: session.install_cutoff("arpu", CUTOFF, _entry(9), "14d"), daemon=True
        )
        writer.start()
        assert installed.wait(timeout=10)

        # (1) the OLD shape — two separate reads — observes the tear: the fresh
        # 9-value entry paired with the stale "7d" tag.
        torn_entry = session.cache.get(("arpu", CUTOFF))
        torn_tag = session.cache_lookback.get(("arpu", CUTOFF))
        assert torn_entry is not None and loaded_value_count(torn_entry) == 9
        assert torn_tag == "7d", "the writer is mid-install: this IS the tear"

        # (2) the accessor cannot see it — it waits for the lock the writer holds.
        pair: list[tuple[object, object]] = []
        reader = threading.Thread(
            target=lambda: pair.append(session.cached_entry("arpu", CUTOFF)), daemon=True
        )
        reader.start()
        reader.join(timeout=0.3)
        assert reader.is_alive(), "cached_entry must block while install_cutoff holds the lock"

        torn_read_done.set()
        writer.join(timeout=10)
        reader.join(timeout=10)
        assert not writer.is_alive() and not reader.is_alive()

        entry, tag = pair[0]
        assert loaded_value_count(entry) == 9 and tag == "14d"  # type: ignore[arg-type]

    def test_cached_entry_reads_the_pair_in_ONE_critical_section(self):
        """The test above proves the accessor takes the lock; this one proves it
        takes it ONCE around both reads.

        Review round 1 found the gap: an accessor rewritten as two separately
        locked reads passes everything else while returning a genuinely torn
        pair. Here the lock itself is instrumented — a complete ``/reload``
        install runs at the reader's FIRST release — so a two-read
        implementation reports the fresh entry with the previous tag, and the
        shipped one cannot.
        """
        session = _session()
        session.install_cutoff("arpu", CUTOFF, _entry(4), "7d")

        reader_holder: list[threading.Thread] = []
        fired = threading.Event()

        def on_release() -> None:
            # only the reader's own release, and only once (install_cutoff
            # releases the same lock)
            if fired.is_set() or threading.current_thread() not in reader_holder:
                return
            fired.set()
            writer = threading.Thread(
                target=lambda: session.install_cutoff("arpu", CUTOFF, _entry(9), "14d")
            )
            writer.start()
            writer.join(timeout=10)
            assert not writer.is_alive()

        session.cache_lock = _HookedLock(on_release)  # type: ignore[assignment]

        pair: list[tuple[object, object]] = []
        reader = threading.Thread(
            target=lambda: pair.append(session.cached_entry("arpu", CUTOFF)), daemon=True
        )
        reader_holder.append(reader)
        reader.start()
        reader.join(timeout=20)
        assert not reader.is_alive()
        assert fired.is_set(), "the instrumented release never ran — the test proved nothing"

        entry, tag = pair[0]
        observed = (loaded_value_count(entry), tag)  # type: ignore[arg-type]
        assert observed in {(4, "7d"), (9, "14d")}, f"torn pair: {observed}"

    def test_concurrent_installs_keep_the_budget_counter_exact(self):
        """``cache_values`` is a read-modify-write pair (subtract the previous
        entry, add the new one) — two unsynchronized installs lose one.

        The contention has to be REAL: at the default 5 ms switch interval the
        interpreter never preempts between the load and the store, and the
        first version of this test passed 25/25 with the lock removed (review
        round 1). A microsecond switch interval and 3 000 installs per thread
        make the lost update reliable.
        """
        session = _session()
        for i in range(4):
            session.install_cutoff("arpu", CUTOFF + timedelta(days=i), _entry(1), None)

        def churn(worker: int) -> None:
            for i in range(3000):
                key = CUTOFF + timedelta(days=(worker + i) % 4)
                session.install_cutoff("arpu", key, _entry(2 + (i % 5)), f"{i}d")

        previous_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            threads = [threading.Thread(target=churn, args=(w,)) for w in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
        finally:
            sys.setswitchinterval(previous_interval)
        assert not any(t.is_alive() for t in threads)

        expected = sum(loaded_value_count(entry) for entry in session.cache.values())
        assert session.cache_values == expected
        # and every entry still has its tag: no half-install survived
        assert set(session.cache) == set(session.cache_lookback)

    def test_a_scan_snapshot_survives_a_concurrent_install(self):
        """``cached_entries`` exists because iterating the dict while ``/reload``
        installs into it raises "dictionary changed size during iteration".

        The installer must keep GROWING the dict and the scanned metric must be
        big enough to be preempted mid-comprehension: review round 1 found the
        first version installed the same 27 keys over and over, so after one
        pass the dict never resized again and the test could not fail with the
        lock removed (20/20 green). It now fails 3/3.
        """
        session = _session()
        for i in range(2000):
            session.install_cutoff("arpu", CUTOFF + timedelta(seconds=i), _entry(2), "7d")

        stop = threading.Event()
        failures: list[BaseException] = []

        def installer() -> None:
            i = 0
            while not stop.is_set():
                i += 1
                session.install_cutoff("other", CUTOFF + timedelta(seconds=i), _entry(2), "7d")

        def scanner() -> None:
            try:
                for _ in range(400):
                    entries = session.cached_entries("arpu")
                    assert [ts for ts, _ in entries] == sorted(ts for ts, _ in entries)
                    assert len(entries) == 2000
                    assert session.cached_cutoffs("arpu") == [ts for ts, _ in entries]
            except BaseException as exc:  # noqa: BLE001 — re-raised below
                failures.append(exc)

        writer = threading.Thread(target=installer, daemon=True)
        reader = threading.Thread(target=scanner)
        writer.start()
        reader.start()
        reader.join(timeout=30)
        stop.set()
        writer.join(timeout=10)
        assert not reader.is_alive()
        if failures:
            raise failures[0]

    def test_disable_cache_clears_all_three_fields_together(self):
        session = _session()
        session.install_cutoff("arpu", CUTOFF, _entry(5), "7d")
        session.disable_cache("over budget")
        assert session.cache == {} and session.cache_lookback == {}
        assert session.cache_values == 0
        assert session.cache_disabled_reason == "over budget"


# -- the AST gate --------------------------------------------------------------

PACKAGE = Path(__file__).resolve().parents[2] / "abkit"
DEFINITION = PACKAGE / "tuning" / "session.py"
GUARDED = {"cache", "cache_lookback", "cache_values"}


def _guarded_accesses(tree: ast.Module) -> list[tuple[int, str]]:
    """Attribute reads/writes of a guarded field, plus getattr reach-arounds."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in GUARDED:
            found.append((node.lineno, node.attr))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "setattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in GUARDED
        ):
            found.append((node.lineno, str(node.args[1].value)))
    return found


def test_the_tier_s_cache_is_touched_only_inside_session_py():
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path == DEFINITION:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for lineno, attr in _guarded_accesses(tree):
            offenders.append(f"{path.relative_to(PACKAGE.parent)}:{lineno} ({attr})")
    assert not offenders, (
        "the Tier-S cache is shared mutable state since m10 WP4 — reach it only "
        "through ExploreSession's locked accessors (loaded/cached_entry/"
        "cached_cutoffs/cached_entries/install_cutoff/disable_cache):\n  " + "\n  ".join(offenders)
    )


EVASIONS = {
    "direct_read": "def f(session):\n    return session.cache.get(('m', 1))\n",
    "nested_read": "def f(srv):\n    return srv.session.cache_lookback.get(('m', 1))\n",
    "counter_update": "def f(session):\n    session.cache_values += 7\n",
    "getattr_reach_around": "def f(session):\n    return getattr(session, 'cache')\n",
    "setattr_reach_around": "def f(session):\n    setattr(session, 'cache_values', 0)\n",
}


def test_the_gate_catches_every_evasion_shape():
    """A gate that only matches one spelling teaches people the others."""
    for label, source in EVASIONS.items():
        assert _guarded_accesses(ast.parse(source)), label


def test_the_gate_does_not_flag_the_accessors():
    """``session.loaded(...)``/``install_cutoff(...)`` are the sanctioned shape,
    and an unrelated ``lru_cache`` attribute must not trip the walk."""
    allowed = (
        "from functools import lru_cache\n"
        "@lru_cache(maxsize=4)\n"
        "def g(x):\n    return x\n"
        "def f(session, srv):\n"
        "    a = session.loaded('m', 1)\n"
        "    b, tag = session.cached_entry('m', 1)\n"
        "    session.install_cutoff('m', 1, b, tag)\n"
        "    return a, srv.session.cached_cutoffs('m')\n"
    )
    assert _guarded_accesses(ast.parse(allowed)) == []
