"""The M11 exit gate: a functioning dashboard session over the scaffolded project
(m11-implementation-plan.md DASH-7).

The in-memory seed-mirror variant (no Docker), mirroring
``test_explore_session.py``: ``abk init`` → ``abk run`` → build the real
dashboard server from the scaffolded configs (the same plumbing ``abk
dashboard`` runs) and prove over live HTTP that:

* every route — ``GET /`` included — is token-gated, and the boot page is
  metadata ONLY (not one verdict, effect or sparkline in the baked payload);
* one row per experiment fills independently: a computed one carries a verdict
  and a sparkline, one whose warehouse read RAISES comes back 200 with
  ``error`` set and null numbers, and a never-computed one is the third,
  distinct "no data — press Run" state;
* ``GET /experiment/<name>`` renders the same self-contained readout ``abk run
  --report`` bakes;
* a job is a **real ``abk`` subprocess**: spawned through the route, pumped into
  the line buffer, polled to a terminal status over ``GET /api/job/<id>?offset=``
  on absolute offsets, and listed by ``GET /api/jobs``; a second pipeline job is
  refused with 400 while one is alive; ``/api/job/<id>/stop`` terminates one;
* the dashboard takes **no pipeline lock** anywhere in that flow (§0.5(d)), and
  never calls ``server.shutdown()`` (§0.5(b) delta 2);
* tearing the registry down leaves **no dangling subprocess**.

**Every job needs an INSTALLED abkit** (DASH-4 note 7: the spawn bootstrap drops
the CWD from ``sys.path``, which is the only place a bare checkout lives), so the
spawning tests say so and skip instead of asserting something weaker.

One honest boundary, disclosed rather than papered over: the warehouse here is an
in-process fake, so a spawned child — its own OS process — CANNOT reach it and a
real ``abk run`` ends in a connection failure. What the ``/api/run`` leg proves
end-to-end is therefore the launcher contract (spawn → pump → poll → terminal
status → registry), not a green pipeline; the green half is a real ``abk run
--steps validate`` (the config lint: no DB, no lock) driven through the same
routes to ``done``.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_first_run import SeedMirrorWarehouse

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from abkit.tuning import dashboard_server
from abkit.tuning.jobs import JobManager

runner = CliRunner()

EXP = "example_signup_test"
BOOM = "boom_test"  # its warehouse read raises — the row-isolation subject
FRESH = "fresh_test"  # never computed — the "no data — press Run" state

_PAYLOAD_RE = re.compile(r"window\.__ABK_DASHBOARD_PAYLOAD__ = (.*?);</script>", re.DOTALL)
_JOB_DEADLINE = 120.0


class HiccupWarehouse(SeedMirrorWarehouse):
    """The seed mirror, plus one experiment whose ``_ab_results`` read raises.

    A DB hiccup is the realistic shape of DASH-2's row-error contract, and it
    enters through the ONE door the row builder uses, so the isolation being
    proven is the shipped code path rather than a patched-out function.
    """

    def execute_query(self, query, params=None):
        if params and params.get("experiment") == BOOM and "_ab_results" in query:
            raise RuntimeError("simulated warehouse hiccup")
        return super().execute_query(query, params)


def _clone_experiment(name: str) -> None:
    source = Path("experiments") / f"{EXP}.yml"
    (Path("experiments") / f"{name}.yml").write_text(
        source.read_text(encoding="utf-8").replace(f"name: {EXP}", f"name: {name}"),
        encoding="utf-8",
    )


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    """`abk init demo` → one real run of the example → two extra experiments."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = HiccupWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    result = runner.invoke(cli, ["run", "--select", EXP])
    assert result.exit_code == 0, result.output
    # cloned AFTER the run, so neither has a persisted row of its own
    _clone_experiment(BOOM)
    _clone_experiment(FRESH)
    return warehouse


def request(url: str, payload: dict | None = None, timeout: float = 30):
    """One request; ``(status, parsed-or-text)``, never raising (the m10 lesson).

    A transport failure raised inside a poll loop would vanish into a stack
    trace and leave the caller asserting on a missing reply with no clue why.
    """
    if payload is None:
        req = urllib.request.Request(url, method="GET")
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body.startswith("{") else body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body
    except Exception as exc:  # noqa: BLE001 — see the docstring
        return 0, f"transport: {exc}"


class Served:
    """The dashboard server built exactly the way ``abk dashboard`` builds it."""

    def __init__(self, warehouse, monkeypatch):
        from abkit.cli.commands._context import load_project_context
        from abkit.config import select_experiments
        from abkit.database.internal_tables import InternalTablesManager
        from abkit.tuning import build_dashboard_server

        # §0.5(d): the launcher takes no pipeline lock. Spied for the WHOLE
        # session rather than asserted per route, since the invariant is "never".
        self.locks: list[str] = []
        monkeypatch.setattr(
            InternalTablesManager,
            "acquire_lock",
            lambda *a, **k: self.locks.append("acquire"),
        )
        monkeypatch.setattr(
            InternalTablesManager,
            "release_lock",
            lambda *a, **k: self.locks.append("release"),
        )

        context = load_project_context(require_profiles=True)
        selected, _ = select_experiments(context.root, ("*",))
        assert sorted(exp.name for _, exp in selected) == sorted([BOOM, EXP, FRESH])
        self.root = context.root
        self.jobs = JobManager()
        self.server, self.url = build_dashboard_server(
            project=context.project,
            project_root=context.root,
            experiments=selected,
            tables=InternalTablesManager(warehouse),
            profile=None,
            jobs=self.jobs,
            metrics=context.metrics_by_name,
            manager=warehouse,
            echo=lambda _line: None,
        )
        self.base = self.url.split("/?")[0]
        self.token = self.server.token
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    def get(self, path: str, *, token: bool = True, query: str = ""):
        auth = f"token={self.token}" if token else ""
        joiner = "&" if query and auth else ""
        tail = f"?{auth}{joiner}{query}" if (auth or query) else ""
        return request(f"{self.base}{path}{tail}")

    def post(self, path: str, payload: dict):
        return request(f"{self.base}{path}?token={self.token}", payload)

    def poll_to_terminal(self, job_id: str) -> tuple[dict, int]:
        """Poll ``/api/job/<id>?offset=`` to a terminal status; return the last
        snapshot and the number of lines seen. Offsets are ABSOLUTE, so the
        cursor may only advance."""
        offset, seen, deadline = 0, 0, time.monotonic() + _JOB_DEADLINE
        snapshot: dict = {}
        while time.monotonic() < deadline:
            status, body = self.get(f"/api/job/{job_id}", query=f"offset={offset}")
            assert status == 200, body
            assert isinstance(body, dict)
            snapshot = body
            assert body["next_offset"] >= offset, "job offsets went backwards"
            offset = body["next_offset"]
            seen += len(body["lines"])
            if body["status"] != "running":
                return snapshot, seen
            time.sleep(0.2)
        raise AssertionError(f"job {job_id} never reached a terminal status: {snapshot}")

    def stop(self):
        self.jobs.shutdown()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def served(scaffolded, monkeypatch):
    s = Served(scaffolded, monkeypatch)
    try:
        yield s
    finally:
        s.stop()


def _abkit_is_installed() -> bool:
    """Whether a spawned job could import abkit (the bootstrap drops the CWD)."""
    probe = subprocess.run(
        [*dashboard_server._CLI_PREFIX, "--version"],
        cwd=os.path.dirname(sys.executable),
        capture_output=True,
        text=True,
        timeout=180,
    )
    return probe.returncode == 0


class TestDashboardSession:
    def test_every_route_is_token_gated_and_the_boot_page_is_metadata_only(self, served):
        for path in ("/", f"/api/stats/{EXP}", "/api/jobs", f"/experiment/{EXP}"):
            status, _ = served.get(path, token=False)
            assert status == 403, f"{path} served an unauthenticated GET"

        status, html = served.get("/")
        assert status == 200
        assert "window.__ABK_DASHBOARD__" in html and 'id="abk-dashboard"' in html
        for placeholder in ("__PAYLOAD__", "__DASHBOARD_JS__", "__PROJECT__"):
            assert placeholder not in html

        match = _PAYLOAD_RE.search(html)
        assert match, "the baked payload is not in the page"
        payload = json.loads(match.group(1))
        assert "token" not in payload  # the page is not a credential at rest
        assert sorted(e["name"] for e in payload["experiments"]) == sorted([BOOM, EXP, FRESH])
        # metadata ONLY — every statistic arrives later over /api/stats
        for entry in payload["experiments"]:
            for statistic in ("verdict", "effect", "pvalue", "spark", "srm_flag", "insufficient"):
                assert statistic not in entry, f"boot entry leaked {statistic}"

    def test_rows_fill_in_three_distinct_states_and_one_error_is_isolated(self, served):
        # `window=all`, because the seed dataset is from 2024 and the presets
        # count back from the WALL CLOCK — the default 30d legitimately empties
        # the sparkline of a fixture this old (the verdict, being the full
        # series', does not move).
        status, row = served.get(f"/api/stats/{EXP}", query="window=all")
        assert status == 200, row
        assert row["error"] is None
        assert row["verdict"] is not None
        assert row["spark"], "a computed experiment has no sparkline"
        assert row["insufficient"] in (True, False)

        status, boom = served.get(f"/api/stats/{BOOM}", query="window=all")
        assert status == 200, boom  # a bad row is a row, never a 500
        assert "simulated warehouse hiccup" in str(boom["error"])
        assert boom["verdict"] is None and boom["effect"] is None

        status, fresh = served.get(f"/api/stats/{FRESH}", query="window=all")
        assert status == 200, fresh
        assert fresh["error"] is None and fresh["verdict"] is None  # "no data — press Run"

        # the failing row did not poison the others: re-read after it
        status, again = served.get(f"/api/stats/{EXP}", query="window=all")
        assert status == 200 and again["verdict"] == row["verdict"]

        assert served.get("/api/stats/nope")[0] == 404
        assert served.get(f"/api/stats/{EXP}", query="window=3d")[0] == 400
        assert served.get(f"/api/stats/{EXP}", query="window=")[0] == 400

    def test_the_open_button_renders_the_full_readout_on_demand(self, served):
        status, html = served.get(f"/experiment/{EXP}")
        assert status == 200, html
        assert "window.__ABK_REPORT__" in html
        for placeholder in ("__PAYLOAD__", "__REPORT_JS__"):
            assert placeholder not in html
        assert served.get("/experiment/nope")[0] == 404

    def test_a_real_abk_job_runs_through_the_routes_to_done(self, served):
        """The green half: `abk run --steps validate` is the config lint — no DB,
        no lock — so a REAL child can finish 0 against an in-process warehouse.
        Spawned through the registry the routes hold, then driven over HTTP."""
        if not _abkit_is_installed():
            pytest.skip("abkit is not installed here; every spawned job needs an install")

        job = served.jobs.spawn_pipeline(
            "run",
            "abk run --steps validate",
            [*dashboard_server._CLI_PREFIX, "run", "--steps", "validate"],
            cwd=served.root,
            env=dashboard_server._subprocess_env(),
        )
        assert job is not None
        snapshot, seen = served.poll_to_terminal(job.id)
        assert snapshot["status"] == "done", snapshot
        assert snapshot["returncode"] == 0, snapshot
        assert seen > 0, "the log pump delivered nothing from a real child"

        status, jobs = served.get("/api/jobs")
        assert status == 200
        assert [j for j in jobs["jobs"] if j["id"] == job.id][0]["status"] == "done"
        assert jobs["pipeline_active"] is False

    def test_the_run_route_spawns_one_real_child_and_is_refused_while_one_lives(self, served):
        """The `/api/run` leg. The child cannot REACH the in-process warehouse
        (its own OS process), so a real `abk run` ends in a connection failure —
        what this proves end-to-end is the launcher contract, not a green
        pipeline.

        The one-at-a-time gate is asserted against a long-lived REAL occupant
        rather than against the run itself: a child that dies on the connection
        in under a second makes "post twice quickly" a race, and a gate test that
        passes because it lost a race is not a gate test.
        """
        if not _abkit_is_installed():
            pytest.skip("abkit is not installed here; every spawned job needs an install")

        occupant = served.jobs.spawn_pipeline(
            "run",
            "occupant",
            [sys.executable, "-c", "import time; time.sleep(120)"],
            cwd=served.root,
            env=dict(os.environ),
        )
        assert occupant is not None
        status, refused = served.post("/api/run", {"select": EXP})
        assert status == 400, refused
        assert "running" in str(refused).lower() or "one at a time" in str(refused).lower()

        status, stopped = served.post(f"/api/job/{occupant.id}/stop", {})
        assert status == 200, stopped
        served.poll_to_terminal(occupant.id)

        status, spawn = served.post("/api/run", {"select": EXP})
        assert status == 200, spawn
        snapshot, seen = served.poll_to_terminal(spawn["job_id"])
        assert snapshot["status"] in ("done", "failed"), snapshot
        assert seen > 0, "a real abk child produced no output"

        status, jobs = served.get("/api/jobs")
        assert status == 200
        assert any(j["id"] == spawn["job_id"] for j in jobs["jobs"])
        assert jobs["pipeline_active"] is False

    def test_stop_terminates_a_job_and_teardown_leaves_nothing_dangling(self, served):
        """A spawned job must not outlive the cockpit that started it."""
        sleeper = served.jobs.spawn(
            "explore",  # the non-pipeline kind: no one-at-a-time gate to fight
            "sleeper",
            [sys.executable, "-c", "import time; time.sleep(120)"],
            cwd=served.root,
            env=dict(os.environ),
        )
        assert sleeper is not None
        pid = sleeper.proc.pid

        status, stopped = served.post(f"/api/job/{sleeper.id}/stop", {})
        assert status == 200, stopped
        snapshot, _ = served.poll_to_terminal(sleeper.id)
        assert snapshot["status"] == "stopped", snapshot

        second = served.jobs.spawn(
            "explore",
            "sleeper2",
            [sys.executable, "-c", "import time; time.sleep(120)"],
            cwd=served.root,
            env=dict(os.environ),
        )
        assert second is not None
        served.jobs.shutdown()
        for _ in range(100):
            if second.proc.poll() is not None:
                break
            time.sleep(0.1)
        assert second.proc.poll() is not None, "the spawned job outlived the dashboard"
        assert not _pid_alive(pid), "the stopped child is still running"

    def test_the_editor_edits_the_scaffolded_project_over_real_http(self, served):
        """UI-1 end to end, on the project `abk init` actually writes.

        The unit suites build a hand-written project; this one edits the
        SCAFFOLD, which is the only place the §8 matrix runs against real
        packaged SQL, the packaged assignment macro and a real metric library.
        A save that passed the fakes and failed here would be a save no operator
        could ever make.
        """
        status, source = served.get(f"/api/experiment-source/{EXP}")
        assert status == 200, source
        assert source["editable"] is True
        assert source["digest"]

        edited = source["yaml_text"].replace("alpha: 0.05", "alpha: 0.01")
        assert edited != source["yaml_text"], "the scaffold no longer declares alpha: 0.05"
        status, reply = served.post(
            "/api/experiment/save",
            {"select": EXP, "text": edited, "digest": source["digest"]},
        )
        assert status == 200, reply

        on_disk = (Path("experiments") / f"{EXP}.yml").read_text(encoding="utf-8")
        assert on_disk == edited  # verbatim, comments and all
        assert Path(reply["archived"]).read_text(encoding="utf-8") == source["yaml_text"]
        # the reload made it visible to every other route, with no restart
        assert served.server.experiment_entry(EXP)[1].alpha == 0.01
        assert served.get(f"/api/stats/{EXP}")[0] == 200

        # a second save with the FIRST digest is the two-tabs case: refused,
        # and the file that is there stays there
        status, detail = served.post(
            "/api/experiment/save",
            {"select": EXP, "text": "name: x\n", "digest": source["digest"]},
        )
        assert status == 400
        assert "changed on disk" in detail
        assert (Path("experiments") / f"{EXP}.yml").read_text(encoding="utf-8") == edited

    def test_create_and_delete_move_the_served_selection(self, served):
        text = (Path("experiments") / f"{EXP}.yml").read_text(encoding="utf-8")
        status, reply = served.post(
            "/api/experiment/create", {"text": text.replace(f"name: {EXP}", "name: made_here")}
        )
        assert status == 200, reply
        assert reply["path"] == "experiments/made_here.yml"
        assert "made_here" in {entry["name"] for entry in reply["experiments"]}
        assert served.get("/api/stats/made_here")[0] == 200

        status, reply = served.post("/api/experiment/delete", {"select": "made_here"})
        assert status == 200, reply
        assert not (Path("experiments") / "made_here.yml").exists()
        assert Path(reply["archived"]).exists()
        assert any("--orphaned-experiments" in w for w in reply["warnings"])
        assert served.get("/api/stats/made_here")[0] == 404

    def test_the_whole_session_took_no_pipeline_lock_and_no_self_shutdown(self, served):
        """§0.5(d) + §0.5(b) delta 2, over a session that exercised every read
        route AND every write route: a lock here would block the very `abk run`
        the buttons launch, and a self-shutdown would make the cockpit
        disappear under the operator.

        The write routes are in this list since UI-1 — the invariant's
        restatement ("computes no statistic and takes no pipeline lock") is
        exactly what makes editing compatible with it, so the routes that edit
        have to be the ones proving it.
        """
        assert served.get("/")[0] == 200
        assert served.get(f"/api/stats/{EXP}")[0] == 200
        assert served.get(f"/api/experiment-source/{EXP}")[0] == 200
        assert served.get("/api/jobs")[0] == 200
        assert served.get("/api/experiments")[0] == 200
        text = (Path("experiments") / f"{EXP}.yml").read_text(encoding="utf-8")
        assert served.post("/api/experiment/save", {"select": EXP, "text": text})[0] == 200
        assert served.post("/api/reload", {})[0] == 200
        assert served.locks == [], f"the dashboard took a pipeline lock: {served.locks}"
        # still serving: nothing asked the HTTP server to stop
        assert served.get("/api/jobs")[0] == 200
        assert served.thread.is_alive()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie reaped by the pump thread reports alive to kill(0) on Linux only
    # until waited on; the JobManager waits, so treat /proc as the authority
    return Path(f"/proc/{pid}").exists() and signal.SIGTERM is not None
