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
import itertools
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from abkit.loaders.metric_loader import MetricLoadResult
from abkit.stats.bootstrap import ResampleOutcome
from abkit.tuning.session import (
    BOOT_MEMO_ENTRY_OVERHEAD,
    BootMemoEntry,
    BootMemoKey,
    ExploreSession,
    loaded_value_count,
    memo_slot_charge,
)

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

        entry, tag, generation = pair[0]
        assert loaded_value_count(entry) == 9 and tag == "14d"  # type: ignore[arg-type]
        assert generation == 2  # m10 WP5: the generation travels with the pair

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

        entry, tag, generation = pair[0]
        # m10 WP5 widened the atomic read to a TRIPLE: a memo keyed to a
        # generation that does not belong to the entry it was resampled from is
        # the stale-hit hazard the generation exists to prevent.
        observed = (loaded_value_count(entry), tag, generation)  # type: ignore[arg-type]
        assert observed in {(4, "7d", 1), (9, "14d", 2)}, f"torn triple: {observed}"

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
                    # ``cached_cutoffs`` iterates the same dict and needs its own
                    # exposure: sampled once per pass it only caught an unlocked
                    # accessor 1 run in 5 (review round 2).
                    for _ in range(3):
                        assert len(session.cached_cutoffs("arpu")) == 2000
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

    def test_cached_cutoffs_holds_the_lock_across_its_whole_iteration(self):
        """The sibling scan needs its own gate, deterministically.

        The hammer above catches an unlocked ``cached_entries`` every run but an
        unlocked ``cached_cutoffs`` only ~2 in 5 (review round 2). Here the dict
        itself starts a writer at the first key: with the lock the writer blocks
        and the iteration completes, without it the install lands mid-iteration
        and CPython raises "dictionary changed size during iteration".
        """
        session = _session()
        for i in range(50):
            session.install_cutoff("arpu", CUTOFF + timedelta(seconds=i), _entry(2), "7d")

        installed = threading.Event()

        def write() -> None:
            session.install_cutoff("arpu", CUTOFF + timedelta(days=99), _entry(2), "7d")
            installed.set()

        class _WriterAtFirstKey(dict):
            def __iter__(self):  # type: ignore[no-untyped-def]
                keys = super().__iter__()
                started = False
                while True:
                    try:
                        key = next(keys)
                    except StopIteration:
                        return
                    if not started:
                        started = True
                        threading.Thread(target=write, daemon=True).start()
                        # locked ⇒ the writer is stuck on cache_lock and this
                        # times out; unlocked ⇒ it installs right here
                        installed.wait(timeout=0.5)
                    yield key

        session.cache = _WriterAtFirstKey(session.cache)  # type: ignore[assignment]
        cutoffs = session.cached_cutoffs("arpu")  # must not raise
        assert len(cutoffs) == 50
        assert installed.wait(timeout=10)  # …and the writer lands afterwards
        assert len(session.cached_cutoffs("arpu")) == 51

    def test_disable_cache_clears_all_three_fields_together(self):
        session = _session()
        session.install_cutoff("arpu", CUTOFF, _entry(5), "7d")
        session.disable_cache("over budget")
        assert session.cache == {} and session.cache_lookback == {}
        assert session.cache_values == 0
        assert session.cache_disabled_reason == "over budget"


# -- the m10 WP5 resample memo -------------------------------------------------


def _memo(values: int, marker: float = 1.0) -> BootMemoEntry:
    return BootMemoEntry(
        outcome=ResampleOutcome(np.full(values, marker), marker, ()),
        caught=(),
        values=values,
    )


def _key(end_ts: datetime, generation: int = 1, metric: str = "arpu") -> BootMemoKey:
    return BootMemoKey(
        metric=metric,
        name_1="control",
        name_2="treatment",
        end_ts=end_ts,
        generation=generation,
        method="bootstrap",
        params="{}",
    )


class TestBootMemo:
    def test_a_reinstall_bumps_the_generation_and_drops_the_entry(self):
        session = _session()
        session.install_cutoff("arpu", CUTOFF, _entry(4), "7d")
        assert session.cached_entry("arpu", CUTOFF)[2] == 1

        session.memoize_resample(_key(CUTOFF, generation=1), _memo(10))
        assert session.memoized_count() == 1

        session.install_cutoff("arpu", CUTOFF, _entry(4), "7d")
        assert session.cached_entry("arpu", CUTOFF)[2] == 2
        assert session.memoized_count() == 0, "the reload must drop the stale entry"
        assert session.memoized_value_count() == 0

    def test_a_reinstall_leaves_other_cutoffs_and_metrics_alone(self):
        session = _session()
        other = CUTOFF + timedelta(days=1)
        for metric in ("arpu", "clicks"):
            for ts in (CUTOFF, other):
                session.install_cutoff(metric, ts, _entry(2), None)
                session.memoize_resample(_key(ts, 1, metric), _memo(5))
        assert session.memoized_count() == 4

        session.install_cutoff("arpu", CUTOFF, _entry(2), None)
        assert session.memoized_count() == 3
        assert session.memoized_resample(_key(CUTOFF, 1, "clicks")) is not None
        assert session.memoized_resample(_key(other, 1, "arpu")) is not None

    def test_the_budget_evicts_oldest_first_and_stays_exact(self):
        slot = 10 + BOOT_MEMO_ENTRY_OVERHEAD
        session = _session()
        session.boot_memo_budget = 2 * slot + 5
        for day in range(10):
            session.memoize_resample(_key(CUTOFF + timedelta(days=day)), _memo(10))
            assert session.memoized_value_count() <= session.boot_memo_budget
        assert session.memoized_count() == 2  # two slots fit, the third evicts the first
        assert session.memoized_value_count() == 2 * slot
        assert session.memoized_resample(_key(CUTOFF)) is None  # the oldest is gone
        assert session.memoized_resample(_key(CUTOFF + timedelta(days=9))) is not None
        assert session.memo_eviction_count() == 8  # …and the evictions are counted

    def test_an_entry_larger_than_the_whole_budget_is_refused_not_stored(self):
        """Storing it would evict every neighbour AND still bust the cap."""
        session = _session()
        session.boot_memo_budget = 10 + BOOT_MEMO_ENTRY_OVERHEAD
        session.memoize_resample(_key(CUTOFF), _memo(10))
        assert session.memoize_resample(_key(CUTOFF + timedelta(days=1)), _memo(11)) is False
        assert session.memoized_count() == 1
        assert session.memoized_value_count() == 10 + BOOT_MEMO_ENTRY_OVERHEAD

    def test_the_slot_overhead_bounds_a_flood_of_tiny_entries(self):
        """A value-only budget bounds the payload and nothing else: one-replicate
        entries would let a client mint millions of slots "inside" the budget
        (review round 1 measured ~773 B of fixed cost each). The slot charge is
        what makes the cap a real memory bound."""
        session = _session()
        session.boot_memo_budget = 10 * (1 + BOOT_MEMO_ENTRY_OVERHEAD)
        for day in range(500):
            session.memoize_resample(_key(CUTOFF + timedelta(days=day)), _memo(1))
        assert session.memoized_count() == 10  # not 500
        assert session.memoized_value_count() <= session.boot_memo_budget

    def test_reinserting_the_same_key_does_not_double_count_the_budget(self):
        session = _session()
        for _ in range(5):
            session.memoize_resample(_key(CUTOFF), _memo(10))
        assert session.memoized_count() == 1
        assert session.memoized_value_count() == 10 + BOOT_MEMO_ENTRY_OVERHEAD

    def test_disable_cache_clears_the_memo_too(self):
        session = _session()
        session.install_cutoff("arpu", CUTOFF, _entry(4), "7d")
        session.memoize_resample(_key(CUTOFF), _memo(10))
        session.disable_cache("over budget")
        assert session.memoized_count() == 0
        assert session.memoized_value_count() == 0
        # generations are monotonic ACROSS a disable: a re-populated cache must
        # never hand a resurrected entry a matching key
        session.install_cutoff("arpu", CUTOFF, _entry(4), "7d")
        assert session.cached_entry("arpu", CUTOFF)[2] == 2

    def test_concurrent_memoizes_keep_the_budget_counter_exact(self):
        """``boot_memo_values`` is a read-modify-write pair like ``cache_values``
        — unlocked, two threads lose one another's update.

        The keys are SHARED across the threads on purpose (review round 1): with
        a key per thread ``previous`` is always None and the whole update is
        ``+= entry.values`` — bytecode with no CALL and no backward jump, which
        CPython never preempts, so the first version of this test passed 0/60
        with the lock removed. Sharing 16 keys puts the ``pop(...)`` call inside
        the window (the shape the WP4 original has), and the mutation is then
        caught 12/12.
        """
        session = _session()
        session.boot_memo_budget = 10_000_000
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:

            def churn(worker: int) -> None:
                for i in range(4000):
                    session.memoize_resample(
                        _key(CUTOFF + timedelta(seconds=(worker + i) % 16)), _memo(3)
                    )

            threads = [threading.Thread(target=churn, args=(w,)) for w in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            assert not any(thread.is_alive() for thread in threads)
        finally:
            sys.setswitchinterval(previous)
        assert session.memoized_count() == 16
        assert session.memoized_value_count() == sum(
            memo_slot_charge(entry) for entry in session.boot_memo.values()
        )

    def test_a_purge_racing_the_readers_never_hangs_or_miscounts(self):
        """``install_cutoff`` purges the memo AFTER releasing ``cache_lock``, so
        the two locks are never nested — a reader hammering both accessors while
        a writer installs must neither deadlock, crash, nor corrupt the counter.

        Two things make the mutation (a purge without ``boot_memo_lock``) reliably
        detectable, both learned the hard way in review round 1: the purge has to
        SCAN a long dict — hence 4 000 pre-filled entries under a cutoff it never
        touches — and the interpreter has to preempt inside that scan, which at
        the default 5 ms switch interval it never does. Unlocked, the scan then
        raises "OrderedDict mutated during iteration" out of the /reload handler.
        """
        session = _session()
        session.boot_memo_budget = 10_000_000
        # a long tail the purge must walk past on every install (different cutoff
        # ⇒ never dropped, so the scan stays long)
        other = CUTOFF + timedelta(days=99)
        for i in range(4000):
            session.memoize_resample(_key(other, generation=i), _memo(1))

        stop = threading.Event()
        failures: list[BaseException] = []
        # Each insert uses a FRESH generation so the purged cutoff's slice GROWS
        # between purges (with one recycled key the memo never held more than one
        # entry for it and the mutation was caught 3/20 instead of every run).
        generations = itertools.count(1)

        def reader() -> None:
            try:
                while not stop.is_set():
                    session.cached_entry("arpu", CUTOFF)
                    generation = next(generations)
                    session.memoize_resample(_key(CUTOFF, generation=generation), _memo(4))
                    session.memoized_resample(_key(CUTOFF, generation=generation))
            except BaseException as exc:  # pragma: no cover - the failure path
                failures.append(exc)

        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
        try:
            for thread in readers:
                thread.start()
            for _ in range(200):
                session.install_cutoff("arpu", CUTOFF, _entry(3), None)
            stop.set()
            for thread in readers:
                thread.join(timeout=20)
        finally:
            sys.setswitchinterval(previous)
        assert not any(thread.is_alive() for thread in readers), "deadlock"
        assert not failures, failures
        assert session.memoized_value_count() == sum(
            memo_slot_charge(entry) for entry in session.boot_memo.values()
        )


# -- the AST gate --------------------------------------------------------------

PACKAGE = Path(__file__).resolve().parents[2] / "abkit"
DEFINITION = PACKAGE / "tuning" / "session.py"
#: m10 WP5 added ``cache_generation`` (read out with the entry) and the
#: ``boot_memo`` pair to the guarded set. ``boot_memo_budget`` stays out:
#: it is construction-time config, never mutated by a serving thread.
GUARDED = {
    "cache",
    "cache_lookback",
    "cache_values",
    "cache_generation",
    "boot_memo",
    "boot_memo_values",
}


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
        "the Tier-S cache and the m10 WP5 resample memo are shared mutable state — "
        "reach them only through ExploreSession's locked accessors (loaded/"
        "cached_entry/cached_cutoffs/cached_entries/install_cutoff/disable_cache/"
        "memoized_resample/memoize_resample/drop_memoized_cutoff/memoized_count/"
        "memoized_value_count):\n  " + "\n  ".join(offenders)
    )


EVASIONS = {
    "direct_read": "def f(session):\n    return session.cache.get(('m', 1))\n",
    "nested_read": "def f(srv):\n    return srv.session.cache_lookback.get(('m', 1))\n",
    "counter_update": "def f(session):\n    session.cache_values += 7\n",
    "getattr_reach_around": "def f(session):\n    return getattr(session, 'cache')\n",
    "setattr_reach_around": "def f(session):\n    setattr(session, 'cache_values', 0)\n",
    "memo_read": "def f(session):\n    return session.boot_memo.get(('m', 1))\n",
    "memo_write": "def f(session):\n    session.boot_memo[('m', 1)] = None\n",
    "memo_counter": "def f(session):\n    session.boot_memo_values = 0\n",
    "generation_read": "def f(srv):\n    return srv.session.cache_generation.get(('m', 1))\n",
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
        "    b, tag, generation = session.cached_entry('m', 1)\n"
        "    memo = session.memoized_resample(key)\n"
        "    session.drop_memoized_cutoff('m', 1)\n"
        "    session.install_cutoff('m', 1, b, tag)\n"
        "    return a, srv.session.cached_cutoffs('m')\n"
    )
    assert _guarded_accesses(ast.parse(allowed)) == []


# -- the m10 WP5 structural gates ---------------------------------------------


def _boot_memo_key_names(tree: ast.Module) -> set[str]:
    """Every local name bound to ``BootMemoKey``, alias forms included.

    The m10 WP4 round-2 lesson: a gate that matches one spelling teaches the
    others. ``from abkit.tuning.session import BootMemoKey as K`` and
    ``session_module.BootMemoKey`` are the same construction.
    """
    names = {"BootMemoKey"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "BootMemoKey":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            # K = BootMemoKey  (a rebinding hands the constructor out)
            value = node.value
            resolved = None
            if isinstance(value, ast.Name) and value.id in names:
                resolved = value.id
            elif isinstance(value, ast.Attribute) and value.attr == "BootMemoKey":
                resolved = value.attr
            if resolved is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
    return names


def test_boot_memo_keys_are_composed_only_by_the_session_factory():
    """``BootMemoKey`` has seven fields and every missing one is a silent wrong
    number (another metric's, another arm pair's, another seed's replicates), so
    it is built in ONE place — ``ExploreSession.boot_memo_key``. The m9
    ``state_series_key()`` precedent, and the m10 WP1 grid-factory one: a
    composition copied to a second call site is a composition that will be
    copied with a field dropped."""
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path == DEFINITION:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        names = _boot_memo_key_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in names:
                    offenders.append(f"{path.relative_to(PACKAGE.parent)}:{node.lineno}")
                elif isinstance(func, ast.Attribute) and func.attr == "BootMemoKey":
                    offenders.append(f"{path.relative_to(PACKAGE.parent)}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr == "_make":
                # BootMemoKey._make([...]) — the NamedTuple back door
                value = node.value
                if (isinstance(value, ast.Name) and value.id in names) or (
                    isinstance(value, ast.Attribute) and value.attr == "BootMemoKey"
                ):
                    offenders.append(f"{path.relative_to(PACKAGE.parent)}:{node.lineno} (_make)")
    assert (
        not offenders
    ), "compose a memo key ONLY through ExploreSession.boot_memo_key():\n  " + "\n  ".join(
        offenders
    )


KEY_EVASIONS = {
    "direct": "from abkit.tuning.session import BootMemoKey\nk = BootMemoKey(1, 2, 3, 4, 5, 6, 7)\n",
    "aliased_import": (
        "from abkit.tuning.session import BootMemoKey as K\nk = K(1, 2, 3, 4, 5, 6, 7)\n"
    ),
    "module_attribute": "import abkit.tuning.session as s\nk = s.BootMemoKey(1, 2)\n",
    "rebound": "from abkit.tuning.session import BootMemoKey\nK = BootMemoKey\nk = K(1, 2)\n",
    "namedtuple_make": (
        "from abkit.tuning.session import BootMemoKey\nk = BootMemoKey._make(parts)\n"
    ),
}


def test_the_key_gate_catches_every_construction_shape():
    """A gate that only matches one spelling teaches people the others."""
    for label, source in KEY_EVASIONS.items():
        tree = ast.parse(source)
        names = _boot_memo_key_names(tree)
        hit = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Name) and func.id in names) or (
                    isinstance(func, ast.Attribute) and func.attr == "BootMemoKey"
                ):
                    hit = True
            elif isinstance(node, ast.Attribute) and node.attr == "_make":
                value = node.value
                if (isinstance(value, ast.Name) and value.id in names) or (
                    isinstance(value, ast.Attribute) and value.attr == "BootMemoKey"
                ):
                    hit = True
        assert hit, label


def test_the_memo_lock_is_never_taken_inside_the_cache_lock():
    """The two locks are independent, not ordered — the purge runs AFTER
    ``install_cutoff``'s ``cache_lock`` section returns. That is a stronger
    property than a documented acquisition order (there is nothing to get
    wrong), and it is only true as long as nothing reaches the memo from inside
    a ``cache_lock`` block; this walks the AST and says so."""
    tree = ast.parse(DEFINITION.read_text(), filename=str(DEFINITION))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        holds_cache_lock = any(
            isinstance(item.context_expr, ast.Attribute) and item.context_expr.attr == "cache_lock"
            for item in node.items
        )
        if not holds_cache_lock:
            continue
        for inner in ast.walk(node):
            name = None
            if isinstance(inner, ast.Attribute):
                name = inner.attr
            elif isinstance(inner, ast.Name):
                name = inner.id
            if name and (name.startswith("boot_memo") or name.startswith("drop_memoized")):
                offenders.append(f"session.py:{inner.lineno} ({name}) inside a cache_lock block")
    assert not offenders, (
        "cache_lock and boot_memo_lock must never nest — move the memo work "
        "after the cache_lock section:\n  " + "\n  ".join(offenders)
    )
