"""Subprocess registry + output pumping for the ``abk dashboard`` cockpit.

Ported near-verbatim from the donor's ``detectkit/ui/jobs.py``
(``docs/specs/m11-implementation-plan.md`` DASH-1). The dashboard server never
runs the pipeline in-process — every Run / Unlock / Clean / Explore click
spawns the real ``abk`` CLI as a subprocess, exactly as if typed at a
terminal. This module only tracks those subprocesses: pumping their merged
stdout/stderr into an in-memory line buffer the page polls, and reporting
status/return code. Nothing here touches the DB, the pipeline lock, or
``abkit.stats``.

Three deliberate deviations from the donor (DASH-1 steps 1/3/4/5):

* **The kind vocabulary is abkit's** — ``run``/``unlock``/``clean``/``explore``
  (the donor's ``autotune``/``tune`` have no abkit equivalent). ``explore``
  plays the donor's ``tune`` role: it is excluded from the one-at-a-time
  pipeline gate (concurrent cockpits on *different* experiments are safe)
  while being deduped per experiment, because two cockpits on the *same*
  experiment race the same Apply-rewrites-YAML hazard. The vocabulary lives in
  :data:`JOB_KINDS` / :data:`PIPELINE_KINDS` and both spawn entry points
  **validate against it**: the donor took any string, so a typo'd kind would
  silently become "not a pipeline job" and skip the gate it was meant to
  respect.
* **``Job.experiment`` replaces the donor's ``Job.metric``** — abkit's whole
  dashboard grain is the experiment (every button is experiment-scoped), so
  the dedup key is a purpose-built field rather than an overloaded one, and it
  rides along in both snapshot shapes so the client's job chip can render
  ``<kind> <experiment>`` without parsing ``label``.
* **:meth:`JobManager.wait_for_line` is drop-aware.** The donor tracked its
  scan position as an index into the *current* buffer, which stops advancing
  once the buffer hits :data:`_MAX_LINES` (each append pops the front, so the
  length is pinned) — the watcher then goes permanently blind. Both this
  method and :meth:`JobManager.snapshot` now count ABSOLUTE line indices, so a
  job that is chattier than the cap before it prints the line being waited for
  (``Explore: <url>``) is still matched instead of failing at the timeout.
"""

from __future__ import annotations

import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Cap on retained stdout/stderr lines per job — a runaway/verbose process must
# not grow memory unboundedly; the oldest lines are dropped once the cap is hit.
_MAX_LINES = 5000
# Cap on retained jobs — the drawer only ever needs recent history.
_MAX_JOBS = 20
# Grace period between SIGTERM and SIGKILL when stopping a job.
_STOP_GRACE_SECONDS = 5.0

#: Job kinds the dashboard spawns (abkit CLI verbs; DASH-1 step 1).
JOB_KINDS = frozenset({"run", "unlock", "clean", "explore"})
#: The kinds that serialize against each other: they all mutate the same
#: DB-level state, so the cockpit only ever lets one of them run at a time.
#: ``explore`` is deliberately absent (DASH-1 step 3) — it only reads results
#: and writes YAML on an explicit Apply.
PIPELINE_KINDS = frozenset({"run", "unlock", "clean"})


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


@dataclass
class Job:
    """One spawned subprocess and its captured output.

    All mutable fields (``lines``, ``status``, ``returncode``, ``url``,
    ``finished_at``, ``stop_requested``) are guarded by ``lock`` — the pump
    thread writes them, request-handler threads read/poll them.
    """

    id: str
    kind: str  # "run" | "unlock" | "clean" | "explore"
    label: str
    argv: list[str]
    proc: subprocess.Popen[str]
    started_at: int
    # explore jobs: the experiment the cockpit was opened on (dedup key).
    experiment: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    lines: list[str] = field(default_factory=list)
    # Count of lines dropped off the front of ``lines`` once the buffer cap is
    # hit. Poll offsets are ABSOLUTE line indices (dropped + buffered), so a
    # verbose job keeps streaming past the cap instead of the poller's offset
    # pinning at the buffer length and never advancing again.
    dropped: int = 0
    truncated: bool = False
    status: str = "running"  # running | done | failed | stopped
    returncode: int | None = None
    url: str | None = None
    finished_at: int | None = None
    stop_requested: bool = False


def _pump(job: Job) -> None:
    """Read *job*'s merged stdout line by line into its buffer until it exits."""
    assert job.proc.stdout is not None
    try:
        for raw_line in job.proc.stdout:
            line = raw_line.rstrip("\n")
            with job.lock:
                job.lines.append(line)
                if len(job.lines) > _MAX_LINES:
                    job.lines.pop(0)
                    job.dropped += 1
                    job.truncated = True
    finally:
        returncode = job.proc.wait()
        with job.lock:
            job.returncode = returncode
            if job.stop_requested:
                job.status = "stopped"
            else:
                job.status = "done" if returncode == 0 else "failed"
            job.finished_at = _now_ms()


class JobManager:
    """In-memory registry of spawned CLI subprocesses (last :data:`_MAX_JOBS`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Serializes the check-and-spawn of pipeline jobs (run/unlock/clean) so
        # two near-simultaneous POSTs can't both pass the single-job gate
        # (spawn_pipeline). Separate from _lock: spawn() acquires _lock
        # internally, so holding _gate around it must not self-deadlock.
        self._gate = threading.Lock()
        self._jobs: list[Job] = []

    def spawn(
        self,
        kind: str,
        label: str,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        experiment: str | None = None,
    ) -> Job:
        """Start *argv* as a subprocess and begin pumping its output.

        Never called while holding a DB lock — spawning is fire-and-forget;
        the caller returns the job id immediately and polls for progress.

        Raises ``ValueError`` for a *kind* outside :data:`JOB_KINDS`: an
        unknown kind would sail past :meth:`pipeline_active`'s gate and
        :meth:`running_job_for`'s dedup alike, which is precisely the silent
        divergence the vocabulary constant exists to prevent.
        """
        if kind not in JOB_KINDS:
            raise ValueError(f"unknown job kind {kind!r} (expected one of {sorted(JOB_KINDS)})")
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
        job = Job(
            id=secrets.token_hex(4),
            kind=kind,
            label=label,
            argv=list(argv),
            proc=proc,
            started_at=_now_ms(),
            experiment=experiment,
        )
        with self._lock:
            self._jobs.append(job)
            if len(self._jobs) > _MAX_JOBS:
                # Evict the oldest *finished* job — never a running one, which
                # would orphan its process (untracked by stop()/shutdown()).
                # If everything is still running, the registry briefly exceeds
                # the cap rather than losing track of a live subprocess.
                for i, old in enumerate(self._jobs):
                    with old.lock:
                        still_running = old.status == "running"
                    if not still_running:
                        self._jobs.pop(i)
                        break
        threading.Thread(target=_pump, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            for job in self._jobs:
                if job.id == job_id:
                    return job
        return None

    def set_url(self, job: Job, url: str) -> None:
        """Record the cockpit URL an ``explore`` job reported on its stdout."""
        with job.lock:
            job.url = url

    def snapshot(self, job: Job, offset: int = 0) -> dict[str, Any]:
        """``{id, kind, label, experiment, status, returncode, url, next_offset, lines}``.

        ``offset`` / ``next_offset`` are ABSOLUTE line indices over the job's
        whole lifetime (``dropped + buffered``), not indices into the current
        buffer — otherwise a job more verbose than :data:`_MAX_LINES` would pin
        the poller's offset at the buffer length and the stream would go silent
        forever. Lines that already fell off the front are simply gone
        (``truncated=True``), matching a live terminal's scrollback.
        """
        with job.lock:
            lines = list(job.lines)
            dropped = job.dropped
            status = job.status
            returncode = job.returncode
            url = job.url
        total = dropped + len(lines)
        start = max(dropped, min(offset, total))
        return {
            "id": job.id,
            "kind": job.kind,
            "label": job.label,
            "experiment": job.experiment,
            "status": status,
            "returncode": returncode,
            "url": url,
            "next_offset": total,
            "lines": lines[start - dropped :],
        }

    def list_snapshots(self) -> list[dict[str, Any]]:
        """Every job's summary (no ``lines``), newest first."""
        with self._lock:
            jobs = list(self._jobs)
        out = []
        for job in reversed(jobs):
            with job.lock:
                out.append(
                    {
                        "id": job.id,
                        "kind": job.kind,
                        "label": job.label,
                        "experiment": job.experiment,
                        "status": job.status,
                        "returncode": job.returncode,
                        "url": job.url,
                        "started_at": job.started_at,
                        "finished_at": job.finished_at,
                    }
                )
        return out

    def spawn_pipeline(
        self,
        kind: str,
        label: str,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> Job | None:
        """Atomically spawn a pipeline job, or return ``None`` if one is running.

        The busy check and the spawn happen under one gate, so two
        near-simultaneous requests can't both observe "idle" and each start a
        subprocess (the plain check-then-``spawn()`` sequence is a TOCTOU race
        across the server's request threads).

        Raises ``ValueError`` for a *kind* outside :data:`PIPELINE_KINDS` —
        ``explore`` is not gated one-at-a-time and must go through
        :meth:`spawn`, so accepting it here would return a job the gate never
        actually protected.
        """
        if kind not in PIPELINE_KINDS:
            raise ValueError(
                f"{kind!r} is not a pipeline job kind "
                f"(expected one of {sorted(PIPELINE_KINDS)}); use spawn() instead"
            )
        with self._gate:
            if self.pipeline_active():
                return None
            return self.spawn(kind, label, argv, cwd=cwd, env=env)

    def running_job_for(self, kind: str, experiment: str) -> Job | None:
        """The still-running *kind* job for *experiment*, if any.

        The abkit analog of the donor's ``running_tune_for(metric)``: dedup for
        ``POST /api/explore``, keyed on ``(kind='explore', experiment=name)``
        so a second click reopens the live cockpit instead of racing it.
        """
        with self._lock:
            jobs = list(self._jobs)
        for job in jobs:
            with job.lock:
                if job.kind == kind and job.experiment == experiment and job.status == "running":
                    return job
        return None

    def pipeline_active(self) -> bool:
        """True when a :data:`PIPELINE_KINDS` job (run/unlock/clean) is still running.

        ``explore`` jobs are excluded: multiple concurrent cockpits are fine
        (each explores a different experiment, and explore takes no pipeline
        lock), but Run/Unlock/Clean all mutate the same DB-level state, so the
        dashboard only ever lets one of those run at a time.
        """
        with self._lock:
            jobs = list(self._jobs)
        for job in jobs:
            with job.lock:
                if job.kind in PIPELINE_KINDS and job.status == "running":
                    return True
        return False

    def stop(self, job_id: str) -> bool:
        """Terminate a running job (grace period, then kill). False if not running."""
        job = self.get(job_id)
        if job is None:
            return False
        with job.lock:
            if job.status != "running":
                return False
            job.stop_requested = True
        try:
            job.proc.terminate()
        except Exception:
            pass

        def _grace() -> None:
            try:
                job.proc.wait(timeout=_STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    job.proc.kill()
                except Exception:
                    pass

        threading.Thread(target=_grace, daemon=True).start()
        return True

    def wait_for_line(
        self, job: Job, predicate: Callable[[str], bool], timeout: float
    ) -> str | None:
        """Block (polling) until a line matching *predicate* appears, or *timeout*.

        Returns ``None`` on timeout or if the job stops running before a
        matching line appears. Used by ``POST /api/explore`` to wait for the
        ``Explore: <url>`` line ``serve_explore`` echoes.

        The scan position is an ABSOLUTE line index, like
        :meth:`snapshot`'s offsets: tracking an index into the live buffer
        (the donor's shape) stops advancing the moment the buffer saturates at
        :data:`_MAX_LINES`, and the watcher never sees another line. Lines
        dropped before this method got to them are gone, not re-scanned.
        """
        deadline = time.monotonic() + timeout
        checked = 0
        while True:
            with job.lock:
                dropped = job.dropped
                buffered = list(job.lines)
                status = job.status
            total = dropped + len(buffered)
            start = max(checked, dropped)
            new_lines = buffered[start - dropped :] if start < total else []
            checked = total
            for line in new_lines:
                if predicate(line):
                    return line
            if status != "running":
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)

    def shutdown(self) -> None:
        """Terminate every still-running job (grace period, then kill)."""
        with self._lock:
            jobs = list(self._jobs)
        running = []
        for job in jobs:
            with job.lock:
                if job.status != "running":
                    continue
            try:
                job.proc.terminate()
            except Exception:
                pass
            running.append(job)
        deadline = time.monotonic() + _STOP_GRACE_SECONDS
        for job in running:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                job.proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    job.proc.kill()
                except Exception:
                    pass
