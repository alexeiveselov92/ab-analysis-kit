"""DASH-1 tests: the dashboard's subprocess registry (m11-implementation-plan.md).

Real subprocesses throughout (``sys.executable -c ...``), never a fake Popen —
the module's whole job is process lifecycle, and a stubbed process proves
nothing about SIGTERM/grace/SIGKILL or about the pump thread. What is pinned:
the absolute-offset poll math past the line cap, the one-at-a-time pipeline
gate under a real thread race, the abkit kind vocabulary (``explore`` outside
the gate, deduped per experiment, an unknown kind refused rather than
silently ungated), stop/shutdown reaching a process that ignores SIGTERM, and
``wait_for_line`` staying sighted after the line buffer saturates.
"""

from __future__ import annotations

import ast
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from abkit.tuning import jobs as jobs_module
from abkit.tuning.jobs import JobManager, JobManagerClosed

# A child that outlives any assertion in this file; the fixture reaps it.
SLEEPER = "import time; time.sleep(300)"
# Same, but deaf to SIGTERM — exercises the grace→SIGKILL escalation.
STUBBORN_SLEEPER = (
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)"
)


@pytest.fixture
def manager() -> object:
    mgr = JobManager()
    try:
        yield mgr
    finally:
        mgr.shutdown()


def _spawn(
    mgr: JobManager,
    code: str,
    *,
    kind: str = "run",
    label: str | None = None,
    experiment: str | None = None,
    tmp_path: Path | None = None,
) -> object:
    return mgr.spawn(
        kind,
        label or f"{kind} (test)",
        [sys.executable, "-u", "-c", code],
        cwd=tmp_path or Path.cwd(),
        env=dict(os.environ),
        experiment=experiment,
    )


def _await_status(
    mgr: JobManager, job: object, *, expected: set[str], timeout: float = 20.0
) -> str:
    """Poll until *job*'s status leaves ``running``; returns the final status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = mgr.snapshot(job)["status"]
        if status in expected:
            return status
        time.sleep(0.02)
    pytest.fail(f"job stayed {mgr.snapshot(job)['status']!r}, expected one of {sorted(expected)}")


def _live_sleeper_pids(cwd: Path) -> list[int]:
    """PIDs of test children still alive, matched by their working directory.

    Each test gets its own ``tmp_path``, so this sees only its own spawns —
    and a reaped/zombie child has no readable ``cwd`` link, which is exactly
    the distinction the leak assertions need.
    """
    target = str(cwd.resolve())
    alive: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(entry / "cwd") != target:
                continue
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"time.sleep(300)" in cmdline:
            alive.append(int(entry.name))
    return alive


requires_procfs = pytest.mark.skipif(
    not Path("/proc").is_dir(), reason="leaked-child detection reads Linux /proc"
)


def _await(condition, *, timeout: float = 20.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    pytest.fail(f"timed out waiting for {what}")


class TestSpawnAndPump:
    def test_stdout_lines_are_pumped_into_the_buffer(self, manager, tmp_path):
        job = _spawn(
            manager,
            "print('first', flush=True); print('second', flush=True)",
            tmp_path=tmp_path,
        )
        assert _await_status(manager, job, expected={"done"}) == "done"
        snap = manager.snapshot(job)
        assert snap["lines"] == ["first", "second"]
        assert snap["returncode"] == 0
        assert snap["next_offset"] == 2
        assert job.finished_at is not None and job.finished_at >= job.started_at

    def test_nonzero_exit_is_failed_not_done(self, manager, tmp_path):
        job = _spawn(manager, "import sys; sys.exit(3)", tmp_path=tmp_path)
        assert _await_status(manager, job, expected={"failed"}) == "failed"
        assert manager.snapshot(job)["returncode"] == 3

    def test_stderr_is_merged_into_the_same_stream(self, manager, tmp_path):
        job = _spawn(
            manager,
            "import sys; print('out', flush=True); print('err', file=sys.stderr, flush=True)",
            tmp_path=tmp_path,
        )
        _await_status(manager, job, expected={"done"})
        assert set(manager.snapshot(job)["lines"]) == {"out", "err"}

    def test_argv_is_copied_so_a_caller_cannot_mutate_it_afterwards(self, manager, tmp_path):
        argv = [sys.executable, "-c", "pass"]
        job = manager.spawn("run", "run (test)", argv, cwd=tmp_path, env=dict(os.environ))
        argv.append("--sneaky")
        assert job.argv == [sys.executable, "-c", "pass"]

    def test_unknown_kind_is_refused_and_nothing_is_spawned(self, manager, tmp_path):
        with pytest.raises(ValueError, match="unknown job kind"):
            _spawn(manager, "pass", kind="validate", tmp_path=tmp_path)
        assert manager.list_snapshots() == []

    def test_cwd_is_the_spawn_directory(self, manager, tmp_path):
        job = _spawn(manager, "import os; print(os.getcwd(), flush=True)", tmp_path=tmp_path)
        _await_status(manager, job, expected={"done"})
        assert Path(manager.snapshot(job)["lines"][0]).resolve() == tmp_path.resolve()


class TestSnapshotOffsetMath:
    """Absolute offsets: a job chattier than ``_MAX_LINES`` keeps streaming."""

    @pytest.fixture
    def chatty(self, manager, tmp_path):
        total = jobs_module._MAX_LINES + 1000
        job = _spawn(
            manager,
            f"[print(f'line {{i}}', flush=True) for i in range({total})]",
            tmp_path=tmp_path,
        )
        _await_status(manager, job, expected={"done"})
        return job, total

    def test_buffer_is_capped_and_marked_truncated(self, manager, chatty):
        job, total = chatty
        snap = manager.snapshot(job)
        assert len(snap["lines"]) == jobs_module._MAX_LINES
        assert job.dropped == total - jobs_module._MAX_LINES
        assert job.truncated is True

    def test_next_offset_counts_the_whole_lifetime_not_the_buffer(self, manager, chatty):
        job, total = chatty
        assert manager.snapshot(job)["next_offset"] == total

    def test_offset_below_the_dropped_floor_clamps_to_the_oldest_retained_line(
        self, manager, chatty
    ):
        job, total = chatty
        snap = manager.snapshot(job, offset=0)
        assert snap["lines"][0] == f"line {job.dropped}"
        assert len(snap["lines"]) == jobs_module._MAX_LINES

    def test_a_mid_stream_offset_returns_only_the_lines_after_it(self, manager, chatty):
        job, total = chatty
        snap = manager.snapshot(job, offset=total - 10)
        assert snap["lines"] == [f"line {i}" for i in range(total - 10, total)]

    def test_an_offset_past_the_end_returns_nothing_and_does_not_rewind(self, manager, chatty):
        job, total = chatty
        snap = manager.snapshot(job, offset=total + 10**6)
        assert snap["lines"] == []
        assert snap["next_offset"] == total

    def test_polling_from_next_offset_never_repeats_a_line(self, manager, tmp_path):
        job = _spawn(
            manager,
            "import time\n"
            "for i in range(3):\n"
            "    print(f'tick {i}', flush=True)\n"
            "    time.sleep(0.1)\n",
            tmp_path=tmp_path,
        )
        seen: list[str] = []
        offset = 0
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            snap = manager.snapshot(job, offset=offset)
            seen.extend(snap["lines"])
            offset = snap["next_offset"]
            if snap["status"] != "running":
                # One final drain after exit — the pump may still be flushing.
                snap = manager.snapshot(job, offset=offset)
                seen.extend(snap["lines"])
                break
            time.sleep(0.02)
        assert seen == ["tick 0", "tick 1", "tick 2"]


class TestPipelineGate:
    def test_two_concurrent_spawn_pipeline_calls_start_exactly_one_job(self, manager, tmp_path):
        barrier = threading.Barrier(2)
        results: list[object] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            barrier.wait()
            job = manager.spawn_pipeline(
                "run",
                "run (test)",
                [sys.executable, "-c", SLEEPER],
                cwd=tmp_path,
                env=dict(os.environ),
            )
            with results_lock:
                results.append(job)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        assert sorted(r is None for r in results) == [False, True]
        assert len(manager.list_snapshots()) == 1

    def test_a_second_pipeline_kind_is_refused_while_the_first_runs(self, manager, tmp_path):
        first = manager.spawn_pipeline(
            "run", "run", [sys.executable, "-c", SLEEPER], cwd=tmp_path, env=dict(os.environ)
        )
        assert first is not None
        for kind in ("run", "unlock", "clean"):
            assert (
                manager.spawn_pipeline(
                    kind, kind, [sys.executable, "-c", SLEEPER], cwd=tmp_path, env=dict(os.environ)
                )
                is None
            )
        assert len(manager.list_snapshots()) == 1

    def test_the_gate_reopens_once_the_job_finishes(self, manager, tmp_path):
        first = manager.spawn_pipeline(
            "run", "run", [sys.executable, "-c", "pass"], cwd=tmp_path, env=dict(os.environ)
        )
        assert first is not None
        _await_status(manager, first, expected={"done"})
        second = manager.spawn_pipeline(
            "clean", "clean", [sys.executable, "-c", "pass"], cwd=tmp_path, env=dict(os.environ)
        )
        assert second is not None
        _await_status(manager, second, expected={"done"})

    def test_explore_is_outside_the_gate_in_both_directions(self, manager, tmp_path):
        explore = _spawn(manager, SLEEPER, kind="explore", experiment="exp_a", tmp_path=tmp_path)
        assert manager.pipeline_active() is False
        run = manager.spawn_pipeline(
            "run", "run", [sys.executable, "-c", SLEEPER], cwd=tmp_path, env=dict(os.environ)
        )
        assert run is not None
        assert manager.pipeline_active() is True
        # And a run in flight does not block a second cockpit.
        second_explore = _spawn(
            manager, SLEEPER, kind="explore", experiment="exp_b", tmp_path=tmp_path
        )
        assert second_explore.id != explore.id

    def test_spawn_pipeline_refuses_a_non_pipeline_kind(self, manager, tmp_path):
        with pytest.raises(ValueError, match="not a pipeline job kind"):
            manager.spawn_pipeline(
                "explore",
                "explore",
                [sys.executable, "-c", SLEEPER],
                cwd=tmp_path,
                env=dict(os.environ),
            )
        assert manager.list_snapshots() == []

    def test_pipeline_active_ignores_finished_jobs(self, manager, tmp_path):
        job = manager.spawn_pipeline(
            "run", "run", [sys.executable, "-c", "pass"], cwd=tmp_path, env=dict(os.environ)
        )
        _await_status(manager, job, expected={"done"})
        assert manager.pipeline_active() is False


class TestExploreDedup:
    def test_running_job_for_matches_kind_and_experiment(self, manager, tmp_path):
        job = _spawn(manager, SLEEPER, kind="explore", experiment="exp_a", tmp_path=tmp_path)
        assert manager.running_job_for("explore", "exp_a") is job
        assert manager.running_job_for("explore", "exp_b") is None
        assert manager.running_job_for("run", "exp_a") is None

    def test_a_finished_cockpit_is_no_longer_deduped(self, manager, tmp_path):
        job = _spawn(manager, "pass", kind="explore", experiment="exp_a", tmp_path=tmp_path)
        _await_status(manager, job, expected={"done"})
        assert manager.running_job_for("explore", "exp_a") is None

    def test_jobs_without_an_experiment_never_match_a_dedup_probe(self, manager, tmp_path):
        _spawn(manager, SLEEPER, kind="explore", tmp_path=tmp_path)
        assert manager.running_job_for("explore", "exp_a") is None

    def test_the_experiment_rides_along_in_both_snapshot_shapes(self, manager, tmp_path):
        job = _spawn(manager, SLEEPER, kind="explore", experiment="exp_a", tmp_path=tmp_path)
        assert manager.snapshot(job)["experiment"] == "exp_a"
        assert manager.list_snapshots()[0]["experiment"] == "exp_a"

    def test_a_pipeline_job_can_carry_its_experiment_too(self, manager, tmp_path):
        job = manager.spawn_pipeline(
            "run",
            "run --select exp_a",
            [sys.executable, "-c", SLEEPER],
            cwd=tmp_path,
            env=dict(os.environ),
            experiment="exp_a",
        )
        assert job is not None
        assert manager.snapshot(job)["experiment"] == "exp_a"
        assert manager.running_job_for("run", "exp_a") is job

    def test_set_url_is_visible_to_pollers(self, manager, tmp_path):
        job = _spawn(manager, SLEEPER, kind="explore", experiment="exp_a", tmp_path=tmp_path)
        manager.set_url(job, "http://127.0.0.1:9/?token=x")
        assert manager.snapshot(job)["url"] == "http://127.0.0.1:9/?token=x"
        assert manager.list_snapshots()[0]["url"] == "http://127.0.0.1:9/?token=x"


class TestStop:
    def test_sigterm_stops_a_cooperative_process(self, manager, tmp_path):
        job = _spawn(manager, SLEEPER, tmp_path=tmp_path)
        assert manager.stop(job.id) is True
        assert _await_status(manager, job, expected={"stopped"}) == "stopped"
        assert job.proc.poll() is not None

    def test_a_process_deaf_to_sigterm_is_killed_after_the_grace_period(
        self, manager, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(jobs_module, "_STOP_GRACE_SECONDS", 0.3)
        job = _spawn(manager, STUBBORN_SLEEPER, tmp_path=tmp_path)
        # Let the child install its SIGTERM handler before we signal it.
        time.sleep(0.5)
        assert manager.stop(job.id) is True
        assert _await_status(manager, job, expected={"stopped"}) == "stopped"
        assert job.proc.poll() is not None

    def test_stopping_a_finished_job_is_false(self, manager, tmp_path):
        job = _spawn(manager, "pass", tmp_path=tmp_path)
        _await_status(manager, job, expected={"done"})
        assert manager.stop(job.id) is False

    def test_stopping_an_unknown_id_is_false(self, manager):
        assert manager.stop("deadbeef") is False

    def test_a_stopped_job_reports_stopped_not_failed_despite_the_signal_returncode(
        self, manager, tmp_path
    ):
        job = _spawn(manager, SLEEPER, tmp_path=tmp_path)
        manager.stop(job.id)
        _await_status(manager, job, expected={"stopped"})
        snap = manager.snapshot(job)
        assert snap["status"] == "stopped"
        assert snap["returncode"] not in (0, None)

    def test_a_job_that_survives_the_stop_and_succeeds_reports_done(self, manager, tmp_path):
        """A clean exit outranks stop_requested — the donor's flag alone lies.

        The pump reaps the process BEFORE it takes ``job.lock``, so a stop
        landing in that window marks a run that had already finished on its
        own. Here the same interleaving is made deterministic from the other
        side: the child ignores SIGTERM and exits 0, so ``terminate()``
        provably changed nothing and "stopped" would be a false report.
        """
        job = _spawn(
            manager,
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(1.0)\n",
            tmp_path=tmp_path,
        )
        time.sleep(0.5)  # let the handler install
        assert manager.stop(job.id) is True
        assert _await_status(manager, job, expected={"done", "failed", "stopped"}) == "done"
        snap = manager.snapshot(job)
        assert snap["returncode"] == 0
        assert job.stop_requested is True  # the flag WAS set; the status still says done


class TestWaitForLine:
    def test_a_matching_line_is_returned(self, manager, tmp_path):
        job = _spawn(
            manager,
            "import time; time.sleep(0.2); print('  Explore: http://127.0.0.1:8/?token=t', flush=True); time.sleep(30)",
            kind="explore",
            experiment="exp_a",
            tmp_path=tmp_path,
        )
        line = manager.wait_for_line(job, lambda ln: "Explore:" in ln, timeout=20.0)
        assert line is not None and "http://127.0.0.1:8/?token=t" in line

    def test_a_job_that_exits_without_the_line_stops_the_wait_early(self, manager, tmp_path):
        job = _spawn(manager, "print('nothing here', flush=True)", tmp_path=tmp_path)
        started = time.monotonic()
        assert manager.wait_for_line(job, lambda ln: "Explore:" in ln, timeout=30.0) is None
        assert time.monotonic() - started < 20.0  # returned on exit, not on timeout

    def test_the_timeout_bounds_a_silent_running_job(self, manager, tmp_path):
        job = _spawn(manager, SLEEPER, tmp_path=tmp_path)
        started = time.monotonic()
        assert manager.wait_for_line(job, lambda ln: "Explore:" in ln, timeout=0.5) is None
        elapsed = time.monotonic() - started
        assert 0.5 <= elapsed < 10.0

    def test_the_watcher_stays_sighted_after_the_line_buffer_saturates(
        self, manager, tmp_path, monkeypatch
    ):
        """The donor tracked a buffer-relative index, which pins at the cap.

        Noise saturates the buffer first and the awaited line arrives only
        after the watcher has already scanned the full (capped) buffer — the
        exact shape where a buffer-relative scan position stops advancing and
        the watcher never sees another line.
        """
        monkeypatch.setattr(jobs_module, "_MAX_LINES", 5)
        job = _spawn(
            manager,
            "import time\n"
            "for i in range(40):\n"
            "    print(f'noise {i}', flush=True)\n"
            "time.sleep(0.8)\n"
            "print('  Explore: http://127.0.0.1:8/?token=t', flush=True)\n"
            "time.sleep(30)\n",
            kind="explore",
            experiment="exp_a",
            tmp_path=tmp_path,
        )
        line = manager.wait_for_line(job, lambda ln: "Explore:" in ln, timeout=20.0)
        assert line is not None and "token=t" in line
        assert job.dropped > 0  # the buffer really did saturate

    def test_a_line_appended_after_the_watcher_scanned_a_full_buffer_is_still_seen(
        self, manager, tmp_path, monkeypatch
    ):
        """The same law as above, with the interleaving forced rather than timed.

        The child is silent; the buffer is put in the saturated state by hand
        and the awaited line is appended only AFTER the watcher has provably
        scanned it once — so this cannot pass by accident on a loaded runner,
        the way a child-timing version could.
        """
        monkeypatch.setattr(jobs_module, "_MAX_LINES", 5)
        job = _spawn(manager, SLEEPER, kind="explore", experiment="exp_a", tmp_path=tmp_path)
        with job.lock:  # a pump that already dropped 20 lines off the front
            job.lines = [f"noise {i}" for i in range(20, 25)]
            job.dropped = 20
            job.truncated = True

        scanned = threading.Event()
        examined: list[str] = []

        def predicate(line: str) -> bool:
            examined.append(line)
            scanned.set()
            return "Explore:" in line

        result: list[str | None] = []
        watcher = threading.Thread(
            target=lambda: result.append(manager.wait_for_line(job, predicate, timeout=15.0))
        )
        watcher.start()
        try:
            assert scanned.wait(10.0), "the watcher never scanned the saturated buffer"
            with job.lock:  # one more pumped line, evicting the oldest
                job.lines.pop(0)
                job.dropped += 1
                job.lines.append("  Explore: http://127.0.0.1:8/?token=t")
            watcher.join(timeout=20.0)
        finally:
            watcher.join(timeout=20.0)
        assert result == ["  Explore: http://127.0.0.1:8/?token=t"]
        assert examined[:5] == [f"noise {i}" for i in range(20, 25)]  # scanned, then advanced


class TestRegistryCap:
    def test_finished_jobs_are_evicted_oldest_first(self, manager, tmp_path, monkeypatch):
        monkeypatch.setattr(jobs_module, "_MAX_JOBS", 3)
        for i in range(5):
            job = _spawn(manager, "pass", label=f"job {i}", tmp_path=tmp_path)
            _await_status(manager, job, expected={"done"})
        labels = [s["label"] for s in manager.list_snapshots()]
        assert labels == ["job 4", "job 3", "job 2"]  # newest first, oldest evicted

    def test_a_running_job_is_never_evicted_even_over_the_cap(self, manager, tmp_path, monkeypatch):
        monkeypatch.setattr(jobs_module, "_MAX_JOBS", 2)
        running = [
            _spawn(manager, SLEEPER, kind="explore", label=f"live {i}", tmp_path=tmp_path)
            for i in range(3)
        ]
        ids = {s["id"] for s in manager.list_snapshots()}
        assert ids == {j.id for j in running}  # over cap rather than orphaning a process

    def test_eviction_walks_past_a_running_head_to_the_finished_job_behind_it(
        self, manager, tmp_path, monkeypatch
    ):
        """The scan is forward, not head-only — a head-only check grows unbounded.

        A long cockpit started first with quick jobs finishing behind it is the
        dashboard's ordinary shape, and it is the ONLY shape in which the
        difference shows: every other eviction test leaves the finished job at
        index 0.
        """
        monkeypatch.setattr(jobs_module, "_MAX_JOBS", 2)
        first = _spawn(manager, SLEEPER, kind="explore", label="live cockpit", tmp_path=tmp_path)
        middle = _spawn(manager, "pass", label="quick run", tmp_path=tmp_path)
        _await_status(manager, middle, expected={"done"})
        last = _spawn(manager, SLEEPER, kind="explore", label="second cockpit", tmp_path=tmp_path)
        ids = {s["id"] for s in manager.list_snapshots()}
        assert ids == {first.id, last.id}
        assert middle.id not in ids  # the finished job behind the running head

    def test_get_finds_a_registered_job_and_misses_an_unknown_id(self, manager, tmp_path):
        job = _spawn(manager, SLEEPER, tmp_path=tmp_path)
        assert manager.get(job.id) is job
        assert manager.get("nope") is None

    def test_list_snapshots_carries_no_line_payload(self, manager, tmp_path):
        job = _spawn(manager, "print('x', flush=True)", tmp_path=tmp_path)
        _await_status(manager, job, expected={"done"})
        assert "lines" not in manager.list_snapshots()[0]


class TestShutdown:
    def test_every_running_job_is_reaped(self, tmp_path):
        mgr = JobManager()
        jobs = [_spawn(mgr, SLEEPER, label=f"live {i}", tmp_path=tmp_path) for i in range(2)]
        mgr.shutdown()
        for job in jobs:
            assert job.proc.poll() is not None

    def test_a_process_deaf_to_sigterm_is_killed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jobs_module, "_STOP_GRACE_SECONDS", 0.3)
        mgr = JobManager()
        job = _spawn(mgr, STUBBORN_SLEEPER, tmp_path=tmp_path)
        time.sleep(0.5)  # let the handler install
        mgr.shutdown()
        _await(lambda: job.proc.poll() is not None, what="the stubborn child to die")

    def test_shutdown_is_idempotent_and_safe_with_no_jobs(self, tmp_path):
        mgr = JobManager()
        mgr.shutdown()
        mgr.shutdown()

    @requires_procfs
    def test_spawning_after_shutdown_is_refused_and_the_child_is_not_left_running(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(jobs_module, "_STOP_GRACE_SECONDS", 0.3)
        mgr = JobManager()
        mgr.shutdown()
        with pytest.raises(JobManagerClosed):
            _spawn(mgr, SLEEPER, tmp_path=tmp_path)
        assert mgr.list_snapshots() == []  # nothing registered either
        _await(
            lambda: not _live_sleeper_pids(tmp_path),
            what="the refused child to be terminated",
        )

    @requires_procfs
    def test_a_spawn_racing_shutdown_never_leaves_a_live_child(self, tmp_path, monkeypatch):
        """The donor's snapshot-then-reap loses this race 300/300 (review R1).

        Both orders are legitimate — the job lands in the snapshot and is
        reaped, or the registry is already closed and spawn refuses — but a
        surviving subprocess is not: for abkit that is an ``abk run`` holding
        the pipeline lock with nobody left to watch it.
        """
        monkeypatch.setattr(jobs_module, "_STOP_GRACE_SECONDS", 0.3)
        for _ in range(25):
            mgr = JobManager()
            barrier = threading.Barrier(2)
            spawned: list[object] = []

            def spawner(mgr=mgr, barrier=barrier, spawned=spawned) -> None:
                barrier.wait()
                try:
                    spawned.append(_spawn(mgr, SLEEPER, tmp_path=tmp_path))
                except JobManagerClosed:
                    spawned.append(None)

            thread = threading.Thread(target=spawner)
            thread.start()
            barrier.wait()
            mgr.shutdown()
            thread.join(timeout=20)
            assert not thread.is_alive()
            assert len(spawned) == 1
        _await(
            lambda: not _live_sleeper_pids(tmp_path),
            timeout=30.0,
            what="every raced child to be reaped",
        )

    def test_the_grace_budget_is_shared_across_jobs_rather_than_multiplied(
        self, tmp_path, monkeypatch
    ):
        """Bounded total teardown, unlike stop()'s independent per-job window.

        Pinned because it is a real asymmetry inside one module, not because
        it is obviously right: three SIGTERM-deaf jobs must still cost about
        ONE grace period in total, and all three must die.
        """
        monkeypatch.setattr(jobs_module, "_STOP_GRACE_SECONDS", 0.6)
        mgr = JobManager()
        jobs = [
            _spawn(mgr, STUBBORN_SLEEPER, label=f"deaf {i}", tmp_path=tmp_path) for i in range(3)
        ]
        time.sleep(0.6)  # let every handler install
        started = time.monotonic()
        mgr.shutdown()
        elapsed = time.monotonic() - started
        # Shared budget ⇒ ~0.6s total; a per-job window would cost ~1.8s.
        assert (
            elapsed < 2 * 0.6
        ), f"shutdown took {elapsed:.2f}s — the budget is per-job, not shared"
        for job in jobs:
            _await(lambda job=job: job.proc.poll() is not None, what=f"{job.label} to die")


class TestModuleContract:
    def test_the_kind_vocabulary_is_declared_once_and_pipeline_kinds_are_a_subset(self):
        assert jobs_module.JOB_KINDS == {"run", "unlock", "clean", "explore"}
        assert jobs_module.PIPELINE_KINDS < jobs_module.JOB_KINDS
        assert jobs_module.JOB_KINDS - jobs_module.PIPELINE_KINDS == {"explore"}

    def test_the_gate_reads_the_vocabulary_rather_than_naming_explore(self):
        """A literal ``!= "explore"`` gate silently ungates any future kind."""
        source = Path(jobs_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        gate = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "pipeline_active"
        )
        literals = {
            node.value
            for node in ast.walk(gate)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "explore" not in literals
        assert any(
            isinstance(node, ast.Name) and node.id == "PIPELINE_KINDS" for node in ast.walk(gate)
        )

    def test_shutdown_latches_closed_inside_the_snapshot_critical_section(self):
        """The atomicity claim itself — too narrow a window to time, so read it.

        If ``self._closed = True`` moves out of the ``with self._lock`` block
        that snapshots the registry, a spawn slipping between the two once
        again produces a child nobody reaps. No timing test can reliably hit
        that interleaving, so the structure is what gets pinned.
        """
        tree = ast.parse(Path(jobs_module.__file__).read_text(encoding="utf-8"))
        shutdown = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "shutdown"
        )
        guarded = [node for node in shutdown.body if isinstance(node, ast.With)]
        assert len(guarded) == 1, "shutdown should snapshot under exactly one lock block"
        body_src = [ast.dump(stmt) for stmt in guarded[0].body]
        assert any("_closed" in dumped for dumped in body_src), "the latch escaped the lock"
        assert any("_jobs" in dumped for dumped in body_src), "the snapshot escaped the lock"

    def test_the_registry_never_imports_the_statistics_core(self):
        tree = ast.parse(Path(jobs_module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not [name for name in imported if name.startswith("abkit.")]

    def test_the_job_manager_is_exported_from_the_package(self):
        import abkit.tuning as tuning

        assert tuning.JobManager is JobManager
        assert "JobManager" in tuning.__all__
        assert tuning.Job is jobs_module.Job
