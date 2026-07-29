"""DASH-3 tests: the dashboard localhost server + the page bake.

``docs/specs/m11-implementation-plan.md`` DASH-3. Real HTTP against the
threaded server (the ``tests/tuning/test_server.py`` house pattern — never
handler unit-fakes), over the DASH-2 fixtures in ``test_overview.py`` so the
row this suite reads through HTTP is the same row that suite pins field by
field.

The two deltas from ``abkit/tuning/server.py`` — the dtk-tune pattern, NOT
dtk-ui (§0.5(b)) — are what most of this file exists for, because both would
be reintroduced by a copy-paste that looks entirely reasonable:

* **the token gates EVERY request, GET included** — one parametrized test over
  every route, plus the non-ASCII token that ``secrets.compare_digest``
  refuses to compare as ``str``;
* **the server never shuts itself down** — a source-level gate (the window is
  too small to time) that is itself proven to bite, plus the behavioural half:
  after every route including its error paths, the server is still serving.

Also pinned: the launcher invariant (§0.5(d) — no route acquires the pipeline
lock), that ``db_lock`` really serializes the one DB connection, row-error
isolation surfacing as a 200, and that the window query bounds the sparkline
only (DASH-2's as-built (1), through the HTTP layer).
"""

from __future__ import annotations

import ast
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_overview import (  # the DASH-2 fixtures — one row shape, one source
    EXP_PATH,
    PROJECT,
    ROOT,
    START,
    make_experiment,
    ms,
    seed_series,
)

from abkit import __version__
from abkit.database.internal_tables import InternalTablesManager
from abkit.tuning import dashboard_server, html
from abkit.tuning.dashboard_server import (
    DEFAULT_WINDOW_PRESET,
    build_dashboard_server,
    serve_dashboard,
    window_preset_order,
)
from abkit.tuning.html import _FAVICON, render_dashboard_html
from abkit.tuning.jobs import JobManager, JobManagerClosed
from abkit.tuning.overview import UnknownWindowPreset, build_experiment_row_safe
from tests._helpers.fake_db import FakeDatabaseManager

EXP_PATH_TWO = ROOT / "experiments" / "dash_two.yml"

#: The baked payload literal, so a test can read the boot shell back off the page.
_PAYLOAD_RE = re.compile(r"window\.__ABK_DASHBOARD_PAYLOAD__ = (.*?);</script>")


def baked_payload(page: str) -> dict:
    match = _PAYLOAD_RE.search(page)
    assert match is not None, "the page carries no __ABK_DASHBOARD_PAYLOAD__ literal"
    return json.loads(match.group(1).replace("\\u003c", "<"))


def sleeper_argv(seconds: float = 30.0) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def talker_argv(lines: tuple[str, ...] = ("hello", "world")) -> list[str]:
    body = "; ".join(f"print({line!r}, flush=True)" for line in lines)
    return [sys.executable, "-c", body]


class Dashboard:
    """One served dashboard over the DASH-2 fake-warehouse fixtures.

    Nothing here writes YAML: DASH-3 reads only the database and the already
    validated configs, and the paths it does touch (``dir``/``file`` on a row)
    are pure path arithmetic — so the fixture keeps ``test_overview``'s
    hermetic ``/proj`` root instead of a scratch tree. DASH-4's source route is
    the first one that will need real files.
    """

    def __init__(
        self,
        *,
        experiments: list | None = None,
        seed: bool = True,
        initial_window: str = DEFAULT_WINDOW_PRESET,
        jobs: JobManager | None = None,
        profile: str | None = None,
    ) -> None:
        self.tables = InternalTablesManager(FakeDatabaseManager())
        self.tables.ensure_tables()
        self.experiment = make_experiment()
        if seed:
            seed_series(self.tables, self.experiment)
        self.echo_lines: list[str] = []
        self.server, self.url = build_dashboard_server(
            project=PROJECT,
            project_root=ROOT,
            experiments=(experiments if experiments is not None else [(EXP_PATH, self.experiment)]),
            tables=self.tables,
            initial_window=initial_window,
            profile=profile,
            jobs=jobs,
            echo=self.echo_lines.append,
        )
        self.base = self.url.split("/?")[0]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    # -- request helpers ------------------------------------------------------

    def tokened(self, path: str, **query: str) -> str:
        params = {"token": self.server.token, **query}
        return f"{self.base}{path}?{urllib.parse.urlencode(params)}"

    def get(self, path: str, **query: str):
        return raw_get(self.tokened(path, **query))

    def post(self, path: str, payload: dict | None = None, raw: bytes | None = None):
        data = raw if raw is not None else json.dumps(payload or {}).encode()
        return request(self.tokened(path), data=data, method="POST")

    def stop(self) -> None:
        # The TEST may stop the server; the module may not (TestNoSelfShutdown).
        self.server.shutdown()
        self.server.server_close()
        self.server.jobs.shutdown()


def request(url: str, data: bytes | None = None, method: str = "GET"):
    """One request; ``(status, parsed-or-text)``, never raising on 4xx/5xx."""
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            return resp.status, _maybe_json(body)
    except urllib.error.HTTPError as exc:
        return exc.code, _maybe_json(exc.read().decode())
    except (urllib.error.URLError, OSError) as exc:  # refused/reset/timed out
        return 0, f"transport: {exc!r}"


def raw_get(url: str):
    return request(url)


def poll_until_done(dash: Dashboard, job, timeout: float = 30.0) -> dict:
    """Poll ``/api/job/<id>`` until the pump reports a terminal status.

    Never ``proc.wait()``: the child exiting does not mean the pump has drained
    its stdout or written the status, so waiting on the process would race the
    very fields under assertion.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, snapshot = dash.get(f"/api/job/{job.id}", offset="0")
        assert status == 200
        if snapshot["status"] != "running":
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"job {job.id} never finished")


def _maybe_json(body: str):
    try:
        return json.loads(body)
    except ValueError:
        return body


@pytest.fixture
def dash():
    served = Dashboard()
    yield served
    served.stop()


ROUTES = ["/", "/api/stats/dash_exp", "/api/jobs", "/api/job/abcd1234", "/nope"]


class TestTokenGate:
    """Delta 1: the token gates EVERY request — the explore server's GET does not."""

    @pytest.mark.parametrize("path", ROUTES)
    def test_every_get_route_refuses_an_untokened_request(self, dash, path):
        status, detail = raw_get(f"{dash.base}{path}")
        assert status == 403
        assert detail == "bad token"

    @pytest.mark.parametrize("path", ROUTES)
    def test_every_get_route_refuses_a_wrong_token(self, dash, path):
        status, detail = raw_get(f"{dash.base}{path}?token=wrong")
        assert status == 403
        assert detail == "bad token"

    def test_the_refusal_does_not_leak_which_paths_exist(self, dash):
        """Authorization precedes routing, so a 403 is not a path oracle."""
        known = raw_get(f"{dash.base}/api/stats/dash_exp")
        unknown = raw_get(f"{dash.base}/api/stats/no_such_experiment")
        missing = raw_get(f"{dash.base}/nope")
        assert known == unknown == missing == (403, "bad token")

    def test_a_non_ascii_token_is_refused_rather_than_unanswered(self, dash):
        """``compare_digest`` raises on non-ASCII ``str`` — hence the bytes form.

        The check runs BEFORE ``do_GET``'s exception wrapping, so a raise here
        would answer nothing at all and the client would see a closed socket.
        """
        status, detail = raw_get(f"{dash.base}/?token=%CE%B1")
        assert (status, detail) == (403, "bad token")
        assert dash.thread.is_alive()

    def test_post_is_gated_too_then_404s_for_an_unrouted_path(self, dash):
        status, detail = request(f"{dash.base}/api/run", data=b"{}", method="POST")
        assert (status, detail) == (403, "bad token")
        # DASH-4 owns the POST routing table; the transport + gate ship here.
        status, detail = dash.post("/api/run", {"select": "dash_exp"})
        assert status == 404
        assert "/api/run" in detail


class TestBootPage:
    def test_the_page_is_the_dashboard_shell(self, dash):
        status, page = dash.get("/")
        assert status == 200
        assert 'id="abk-dashboard"' in page
        assert "window.__ABK_DASHBOARD_PAYLOAD__" in page
        assert "window.__ABK_DASHBOARD__.render" in page
        assert _FAVICON in page  # explore's mark, so no new hex enters the CI scan
        assert "abkit dashboard — p" in page  # the project name in the title

    def test_the_token_is_not_baked_into_the_page(self, dash):
        """The client reads it from ``location.search`` (the donor's contract).

        A page that carried the token would be a credential at rest the moment
        anything writes it to disk.
        """
        _, page = dash.get("/")
        assert dash.server.token not in page

    def test_the_boot_payload_is_metadata_only(self, dash):
        _, page = dash.get("/")
        payload = baked_payload(page)
        assert payload["project"] == "p"
        assert payload["version"] == __version__
        assert payload["initial_window"] == DEFAULT_WINDOW_PRESET
        assert payload["window_presets"] == ["24h", "7d", "30d", "90d", "all"]
        assert isinstance(payload["generated_at"], int)
        entry = payload["experiments"][0]
        assert entry["name"] == "dash_exp"
        assert entry["main_metric"] == "revenue"
        # the CONFIGURED comparisons (DASH-2 as-built (7)): the per-metric Run
        # button must exist for a secondary metric, which has no verdict ever
        assert entry["comparisons"] == [{"metric": "revenue", "is_main_metric": True}]
        # …and not one statistic anywhere: the rows arrive over /api/stats
        stat_keys = {"verdict", "verdicts", "effect", "spark", "srm_flag", "pvalue", "ci"}
        assert stat_keys.isdisjoint(_all_keys(payload))

    def test_a_payload_value_cannot_break_out_of_the_script_tag(self):
        page = render_dashboard_html({"project": "</script><script>alert(1)//"})
        assert "</script><script>alert(1)" not in page
        assert "\\u003c/script" in page
        # …and the title is HTML-escaped independently of the JSON bake
        assert "&lt;/script&gt;" in page

    def test_unknown_get_path_is_a_404(self, dash):
        status, detail = dash.get("/nope")
        assert status == 404
        assert "/nope" in detail


class TestStatsRoute:
    def test_the_row_is_the_dash2_builders_row_verbatim(self, dash):
        status, row = dash.get("/api/stats/dash_exp", window="all")
        assert status == 200
        expected = build_experiment_row_safe(
            project_root=ROOT,
            experiment_path=EXP_PATH,
            experiment=dash.experiment,
            project=PROJECT,
            tables=dash.tables,
            window_preset="all",
        )
        assert row == json.loads(json.dumps(expected))
        assert row["verdict"] == "WIN"
        assert row["error"] is None

    def test_the_window_query_bounds_the_sparkline_only(self, dash):
        """DASH-2 as-built (1) surfacing through HTTP: the verdict is the full series'."""
        _, wide = dash.get("/api/stats/dash_exp", window="all")
        _, narrow = dash.get("/api/stats/dash_exp", window="24h")
        assert wide["spark"] == [[ms(START + timedelta(days=d)), 0.1] for d in range(1, 15)]
        assert narrow["spark"] == []  # the fixture's looks are long past
        for key in ("verdict", "effect", "ci", "pvalue", "alpha", "elapsed_days", "last_end_ts"):
            assert narrow[key] == wide[key]

    def test_no_window_query_uses_the_servers_boot_window(self):
        served = Dashboard(initial_window="all")
        try:
            _, row = served.get("/api/stats/dash_exp")
            assert len(row["spark"]) == 14
        finally:
            served.stop()

    def test_unknown_experiment_is_a_404(self, dash):
        status, detail = dash.get("/api/stats/nope")
        assert status == 404
        assert "unknown experiment: nope" in detail

    def test_the_path_is_unquoted_before_the_lookup(self, dash):
        """A config's ``name`` is restricted to ``[A-Za-z0-9_-]``, so no served
        experiment ever needs percent-decoding — but a mistyped one does, and
        without ``unquote`` the 404 would name the encoded string instead of
        what the operator actually asked for."""
        status, detail = dash.get("/api/stats/no%20such")
        assert status == 404
        assert "unknown experiment: no such" in detail

    @pytest.mark.parametrize("window", ["7days", "", "24H"])
    def test_an_unknown_window_preset_is_a_400_naming_the_choices(self, dash, window):
        """The blank case is the ``keep_blank_values`` half: ``?window=`` must
        not silently read as the boot window."""
        status, detail = dash.get("/api/stats/dash_exp", window=window)
        assert status == 400
        assert f"Unknown window preset {window!r}" in detail
        assert "24h" in detail and "all" in detail
        assert dash.thread.is_alive()

    def test_a_bad_window_is_rejected_before_the_db_is_touched(self, dash):
        """A request-level mistake must not queue behind a slow read."""
        calls: list[str] = []
        dash.tables.load_results = lambda *a, **k: calls.append("read") or []  # type: ignore[method-assign]
        status, _ = dash.get("/api/stats/dash_exp", window="7days")
        assert status == 400
        assert calls == []

    def test_a_failing_read_is_a_200_carrying_the_row_error(self, dash):
        def boom(*_args, **_kwargs):
            raise RuntimeError("warehouse is down")

        dash.tables.load_results = boom  # type: ignore[method-assign]
        status, row = dash.get("/api/stats/dash_exp", window="all")
        assert status == 200
        assert row["error"] == "RuntimeError: warehouse is down"
        # the config half of the row survives; every stat stays at its default
        assert row["name"] == "dash_exp"
        assert row["start_ts"] == ms(datetime(2026, 1, 1))
        assert row["verdict"] is None and row["spark"] == [] and row["verdicts"] == []

    def test_one_broken_experiment_does_not_cost_the_other(self):
        broken = make_experiment(name="dash_two")
        served = Dashboard(experiments=[(EXP_PATH, make_experiment()), (EXP_PATH_TWO, broken)])
        original = served.tables.load_results
        try:

            def selective(experiment, *args, **kwargs):
                if experiment == "dash_two":
                    raise RuntimeError("nope")
                return original(experiment, *args, **kwargs)

            served.tables.load_results = selective  # type: ignore[method-assign]
            status_a, row_a = served.get("/api/stats/dash_exp", window="all")
            status_b, row_b = served.get("/api/stats/dash_two", window="all")
            assert (status_a, status_b) == (200, 200)
            assert row_a["verdict"] == "WIN" and row_a["error"] is None
            assert row_b["verdict"] is None and row_b["error"] == "RuntimeError: nope"
        finally:
            served.tables.load_results = original  # type: ignore[method-assign]
            served.stop()


class TestDbLock:
    def test_concurrent_stats_calls_both_succeed_and_never_overlap(self, dash):
        """``db_lock`` serializes the one connection without deadlocking.

        The overlap counter is what makes this more than a smoke test: with the
        lock dropped, both reads run inside one another and ``peak`` reaches 2.
        """
        original = dash.tables.load_results
        state = {"inflight": 0, "peak": 0}
        guard = threading.Lock()

        def slow(*args, **kwargs):
            with guard:
                state["inflight"] += 1
                state["peak"] = max(state["peak"], state["inflight"])
            try:
                time.sleep(0.2)
                return original(*args, **kwargs)
            finally:
                with guard:
                    state["inflight"] -= 1

        dash.tables.load_results = slow  # type: ignore[method-assign]
        replies: list = []

        def call():
            replies.append(dash.get("/api/stats/dash_exp", window="all"))

        threads = [threading.Thread(target=call) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert [status for status, _ in replies] == [200, 200]
        assert state["peak"] == 1


class TestJobRoutes:
    def test_jobs_route_is_empty_on_a_fresh_server(self, dash):
        status, reply = dash.get("/api/jobs")
        assert status == 200
        assert reply == {"jobs": [], "pipeline_active": False}

    def test_a_spawned_job_is_listed_and_gates_the_pipeline(self, dash):
        job = dash.server.jobs.spawn(
            "run",
            "run --select dash_exp",
            sleeper_argv(),
            cwd=Path.cwd(),
            env=dict(os.environ),
            experiment="dash_exp",
        )
        try:
            status, reply = dash.get("/api/jobs")
            assert status == 200
            assert reply["pipeline_active"] is True
            assert [entry["id"] for entry in reply["jobs"]] == [job.id]
            entry = reply["jobs"][0]
            assert entry["kind"] == "run"
            assert entry["experiment"] == "dash_exp"
            assert entry["status"] == "running"
        finally:
            dash.server.jobs.stop(job.id)

    def test_job_polling_streams_from_an_absolute_offset(self, dash):
        job = dash.server.jobs.spawn(
            "run", "run", talker_argv(), cwd=Path.cwd(), env=dict(os.environ)
        )
        snapshot = poll_until_done(dash, job)
        assert snapshot["status"] == "done"
        assert snapshot["returncode"] == 0
        assert snapshot["lines"] == ["hello", "world"]
        assert snapshot["next_offset"] == 2
        assert snapshot["dropped"] == 0 and snapshot["truncated"] is False
        # polling from the reported offset never re-delivers a line
        _, tail = dash.get(f"/api/job/{job.id}", offset=str(snapshot["next_offset"]))
        assert tail["lines"] == []
        assert tail["next_offset"] == 2

    def test_unknown_job_is_a_404(self, dash):
        status, detail = dash.get("/api/job/deadbeef", offset="0")
        assert status == 404
        assert "unknown job: deadbeef" in detail

    @pytest.mark.parametrize("offset", ["abc", "-1", "1.5", "", "undefined"])
    def test_a_non_integer_offset_is_a_400(self, dash, offset):
        """Loud, unlike the donor's silent fallback to 0 (which rewinds a drawer).

        The empty case is why the query is parsed with ``keep_blank_values``:
        ``?offset=`` would otherwise be dropped and read as "no offset given",
        so a client bug would quietly resend the whole buffer. ``undefined`` is
        the same bug's other spelling.
        """
        status, detail = dash.get("/api/job/deadbeef", offset=offset)
        assert status == 400
        assert "offset must be a non-negative integer" in detail

    def test_an_absent_offset_still_defaults_to_the_start(self, dash):
        job = dash.server.jobs.spawn(
            "run", "run", talker_argv(), cwd=Path.cwd(), env=dict(os.environ)
        )
        poll_until_done(dash, job)
        status, snapshot = dash.get(f"/api/job/{job.id}")
        assert status == 200
        assert snapshot["lines"] == ["hello", "world"]


class TestTransport:
    def test_oversized_body_is_a_413(self, dash):
        status, detail = dash.post("/api/run", raw=b"x" * 5_000_001)
        assert status == 413
        assert "too large" in detail
        assert dash.thread.is_alive()

    def test_a_malformed_content_length_is_a_400(self, dash):
        parsed = urllib.parse.urlparse(dash.tokened("/api/run"))
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
        try:
            conn.putrequest("POST", f"{parsed.path}?{parsed.query}")
            conn.putheader("Content-Length", "abc")
            conn.endheaders()
            response = conn.getresponse()
            assert response.status == 400
            assert b"Content-Length" in response.read()
        finally:
            conn.close()
        assert dash.thread.is_alive()

    def test_a_bodyless_post_still_reaches_routing(self, dash):
        status, detail = dash.post("/api/run", raw=b"")
        assert status == 404
        assert "/api/run" in detail

    @pytest.mark.parametrize(
        ("raised", "code"), [(ValueError("bad input"), 400), (RuntimeError("boom"), 500)]
    )
    def test_a_raising_route_replies_instead_of_killing_the_thread(
        self, dash, monkeypatch, raised, code
    ):
        """The insurance DASH-4's routes inherit: a request-level ``ValueError``
        is a 400, anything else a 500, and the server keeps serving either way.
        """

        def raiser(self, *_args, **_kwargs):
            raise raised

        monkeypatch.setattr(dashboard_server._Handler, "_route_get", raiser)
        monkeypatch.setattr(dashboard_server._Handler, "_route_post", raiser)
        assert dash.get("/")[0] == code
        assert dash.post("/api/run", {})[0] == code
        monkeypatch.undo()
        assert dash.get("/")[0] == 200

    def test_a_client_disconnect_is_silent_and_a_real_error_is_one_line(self, dash):
        """``handle_error``: the stdlib default would dump a traceback per
        aborted page load, which on Ctrl-C is a wall of BrokenPipe noise."""
        try:
            raise BrokenPipeError("client went away")
        except BrokenPipeError:
            dash.server.handle_error(None, None)
        assert dash.echo_lines == []
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            dash.server.handle_error(None, None)
        assert dash.echo_lines == ["  [dashboard] request error: RuntimeError: boom"]

    def test_json_default_covers_numpy_and_refuses_the_rest(self):
        import numpy as np

        assert dashboard_server._json_default(np.float64(1.5)) == 1.5
        assert dashboard_server._json_default(np.array([1, 2])) == [1, 2]
        with pytest.raises(TypeError, match="not JSON serializable"):
            dashboard_server._json_default(object())


def _all_keys(value, out: set | None = None) -> set:
    """Every dict key anywhere in a nested payload."""
    keys: set = out if out is not None else set()
    if isinstance(value, dict):
        keys.update(value)
        for item in value.values():
            _all_keys(item, keys)
    elif isinstance(value, list):
        for item in value:
            _all_keys(item, keys)
    return keys


def _shutdown_offenders(source: str) -> list[str]:
    """Calls that would stop the HTTP server, and thread targets that would.

    ``<something>.jobs.shutdown()`` is the JOB REGISTRY's teardown and is the
    one allowed form (``serve_dashboard``'s ``finally``).
    """
    offenders: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "shutdown":
                owner = func.value
                if not (isinstance(owner, ast.Attribute) and owner.attr == "jobs"):
                    offenders.append(ast.unparse(node))
        if isinstance(node, ast.keyword) and node.arg == "target":
            if "shutdown" in ast.unparse(node.value):
                offenders.append(ast.unparse(node))
    return offenders


def _pipeline_lock_offenders(source: str) -> list[str]:
    """Any reference to the write-side lock API (the §0.5(d) launcher invariant)."""
    return [
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and node.attr in {"acquire_lock", "release_lock"}
    ]


MODULE_SOURCE = Path(dashboard_server.__file__).read_text(encoding="utf-8")


class TestNoSelfShutdown:
    """Delta 2: the dashboard has no terminal action, so it never stops itself."""

    def test_no_code_path_stops_the_http_server(self):
        assert _shutdown_offenders(MODULE_SOURCE) == []

    def test_the_gate_bites_on_the_explore_servers_shape(self):
        """The tune-server pattern a copy-paste would bring back, verbatim."""
        hostile = (
            "import threading\n"
            "def _handle_apply(self, srv, body):\n"
            "    threading.Thread(target=srv.shutdown, daemon=True).start()\n"
            "    srv.shutdown()\n"
        )
        assert len(_shutdown_offenders(hostile)) == 2

    def test_the_gate_allows_the_job_registry_teardown(self):
        assert _shutdown_offenders("def f(server):\n    server.jobs.shutdown()\n") == []
        assert "server.jobs.shutdown()" in MODULE_SOURCE

    def test_the_server_keeps_serving_after_every_route_including_its_errors(self, dash):
        for status, path, query in [
            (200, "/", {}),
            (200, "/api/stats/dash_exp", {"window": "all"}),
            (404, "/api/stats/nope", {}),
            (400, "/api/stats/dash_exp", {"window": "7days"}),
            (200, "/api/jobs", {}),
            (404, "/api/job/nope", {"offset": "0"}),
            (400, "/api/job/nope", {"offset": "x"}),
            (404, "/nope", {}),
        ]:
            assert dash.get(path, **query)[0] == status, path
        assert dash.post("/api/run", {})[0] == 404
        assert raw_get(f"{dash.base}/")[0] == 403
        # still serving, and still the same server
        assert dash.thread.is_alive()
        assert dash.get("/")[0] == 200


class TestLauncherOnly:
    """§0.5(d): the dashboard spawns `abk`; it never takes the pipeline lock."""

    def test_the_module_never_mentions_the_lock_api(self):
        assert _pipeline_lock_offenders(MODULE_SOURCE) == []

    def test_the_lock_gate_bites(self):
        hostile = "def f(tables):\n    tables.acquire_lock('e', 's', 'p')\n"
        assert _pipeline_lock_offenders(hostile) == ["acquire_lock"]

    def test_no_route_acquires_the_lock_while_the_row_still_reads_it(self, dash, monkeypatch):
        taken: list[tuple] = []
        monkeypatch.setattr(
            InternalTablesManager,
            "acquire_lock",
            lambda self, *a, **k: taken.append(a) or True,
        )
        monkeypatch.setattr(
            InternalTablesManager, "release_lock", lambda self, *a, **k: taken.append(a)
        )
        probed: list[tuple] = []
        original = InternalTablesManager.check_lock
        monkeypatch.setattr(
            InternalTablesManager,
            "check_lock",
            lambda self, *a, **k: probed.append(a) or original(self, *a, **k),
        )
        status, row = dash.get("/api/stats/dash_exp", window="all")
        assert status == 200
        assert taken == []  # never acquired…
        assert probed  # …but the read-only probe behind `locked` did run
        assert row["locked"] is False


class TestBuildValidation:
    def test_an_unknown_boot_window_is_refused_at_construction(self):
        with pytest.raises(UnknownWindowPreset, match="7days"):
            build_dashboard_server(
                project=PROJECT,
                project_root=ROOT,
                experiments=[],
                tables=InternalTablesManager(FakeDatabaseManager()),
                initial_window="7days",
            )

    def test_a_duplicated_experiment_name_is_refused(self):
        experiment = make_experiment()
        with pytest.raises(ValueError, match="duplicate experiment name 'dash_exp'"):
            build_dashboard_server(
                project=PROJECT,
                project_root=ROOT,
                experiments=[(EXP_PATH, experiment), (EXP_PATH_TWO, experiment)],
                tables=InternalTablesManager(FakeDatabaseManager()),
            )

    def test_an_empty_selection_serves_an_empty_list(self):
        served = Dashboard(experiments=[], seed=False)
        try:
            _, page = served.get("/")
            assert baked_payload(page)["experiments"] == []
            assert served.get("/api/stats/dash_exp")[0] == 404
        finally:
            served.stop()

    def test_the_profile_rides_along_for_dash4s_argv(self):
        served = Dashboard(profile="prod")
        try:
            assert served.server.profile == "prod"
            _, page = served.get("/")
            assert baked_payload(page)["profile"] == "prod"
        finally:
            served.stop()

    def test_the_preset_order_is_derived_not_written_out(self):
        assert window_preset_order() == ["24h", "7d", "30d", "90d", "all"]
        assert DEFAULT_WINDOW_PRESET in window_preset_order()


class TestServeDashboard:
    def test_ctrl_c_tears_the_job_registry_down(self, monkeypatch):
        """The cockpit's exit must not leave an `abk run` holding the lock."""
        monkeypatch.setattr(
            dashboard_server._DashboardServer,
            "serve_forever",
            lambda self, poll_interval=0.5: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        jobs = JobManager()
        job = jobs.spawn("run", "run", sleeper_argv(), cwd=Path.cwd(), env=dict(os.environ))
        echoed: list[str] = []
        urls: list[str] = []

        serve_dashboard(
            project=PROJECT,
            project_root=ROOT,
            experiments=[(EXP_PATH, make_experiment())],
            tables=InternalTablesManager(FakeDatabaseManager()),
            jobs=jobs,
            open_browser=False,
            echo=echoed.append,
            on_ready=urls.append,
        )

        assert urls and urls[0].startswith("http://127.0.0.1:")
        assert any(line.startswith("  Dashboard: http://127.0.0.1:") for line in echoed)
        assert echoed[-1] == "  Stopped."
        assert any("Stopping" in line for line in echoed)
        # the child is gone, and the registry refuses to start another
        try:
            job.proc.wait(timeout=10)
        except subprocess.TimeoutExpired as exc:  # pragma: no cover — a leaked child
            job.proc.kill()
            raise AssertionError("the spawned job outlived the dashboard") from exc
        with pytest.raises(JobManagerClosed):
            jobs.spawn("run", "run", talker_argv(), cwd=Path.cwd(), env=dict(os.environ))

    def test_a_browser_launch_is_best_effort(self, monkeypatch):
        """The URL is already printed, so a headless box must not sink the serve."""
        monkeypatch.setattr(
            dashboard_server._DashboardServer,
            "serve_forever",
            lambda self, poll_interval=0.5: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        opened: list[str] = []

        def explode(url: str) -> None:
            opened.append(url)
            raise RuntimeError("no browser here")

        monkeypatch.setattr(dashboard_server.webbrowser, "open", explode)
        echoed: list[str] = []
        serve_dashboard(
            project=PROJECT,
            project_root=ROOT,
            experiments=[],
            tables=InternalTablesManager(FakeDatabaseManager()),
            open_browser=True,
            echo=echoed.append,
        )
        assert opened and opened[0].startswith("http://127.0.0.1:")
        assert echoed[-1] == "  Stopped."

    def test_no_browser_is_launched_when_open_browser_is_false(self, monkeypatch):
        monkeypatch.setattr(
            dashboard_server._DashboardServer,
            "serve_forever",
            lambda self, poll_interval=0.5: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        opened: list[str] = []
        monkeypatch.setattr(dashboard_server.webbrowser, "open", opened.append)
        serve_dashboard(
            project=PROJECT,
            project_root=ROOT,
            experiments=[],
            tables=InternalTablesManager(FakeDatabaseManager()),
            open_browser=False,
            echo=lambda _line: None,
        )
        assert opened == []


class TestBundleBake:
    def test_the_committed_bundle_is_inlined_verbatim_when_present(self, monkeypatch):
        monkeypatch.setattr(
            html, "_read_bundle", lambda name: "/*BUNDLE*/" if name == "dashboard.js" else None
        )
        page = render_dashboard_html({"project": "p", "experiments": []})
        assert "/*BUNDLE*/" in page
        assert "npm run build" not in page

    def test_a_missing_bundle_degrades_to_the_pending_note(self, monkeypatch):
        monkeypatch.setattr(html, "_read_bundle", lambda name: None)
        page = render_dashboard_html({"project": "p", "experiments": []})
        assert "window.__ABK_DASHBOARD__ = {" in page
        assert "npm run build" in page

    def test_the_pending_note_satisfies_the_window_global_contract(self):
        """What ``build.mjs`` asserts of the real bundle in DASH-6, and what the
        page's bootstrap calls — the placeholder must not be a broken page."""
        assert "window.__ABK_DASHBOARD__" in html._PENDING_DASHBOARD_JS
        assert "render:" in html._PENDING_DASHBOARD_JS

    def test_a_missing_bundle_reads_as_none_rather_than_raising(self):
        assert html._read_bundle("no_such_bundle.js") is None

    def test_the_explore_bundle_read_stays_undegraded(self, monkeypatch):
        """A missing explore.js is a packaging bug to surface, not to paper over.

        The degradation is the dashboard's alone: neutering ``_read_bundle``
        must leave ``render_explore_html`` reading the committed bundle.
        """
        monkeypatch.setattr(html, "_read_bundle", lambda name: None)
        assert html._explore_js().strip()
