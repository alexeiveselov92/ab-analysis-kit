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

DASH-4 adds the job routes to the same suite, over a REAL project root (its
routes spawn with ``cwd=project_root`` and read YAML off disk, which
``test_overview``'s hermetic ``/proj`` cannot serve) and a stub standing in for
the ``abk`` entry point. The stub is installed by pointing ``_CLI_PREFIX`` at
it, NOT by monkeypatching the argv builders: the builders then still compose the
verb and the flags, and the stub echoes the argv it actually received — so the
assertions cover the command the cockpit really issues.
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
from abkit.config import select_experiments
from abkit.config.experiment_config import ExperimentConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.tuning import dashboard_server, html
from abkit.tuning import overview as overview_module
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


#: A stand-in for the ``abk`` entry point (DASH-4). Reports the argv it was
#: handed, then behaves as ``$ABK_STUB`` says — so one script covers a job that
#: succeeds, one that hangs, one that fails, and a cockpit that prints a URL.
_ABK_STUB = """\
import os
import sys
import time

print("ARGV " + " ".join(sys.argv[1:]), flush=True)
mode = os.environ.get("ABK_STUB", "done")
if mode == "explore":
    # `abk explore` prints THIS first, before it serves anything — the line a
    # bare "Explore:" predicate would scrape as if it were a URL.
    print("Explore: dash_exp", flush=True)
    print("  Explore: http://127.0.0.1:9/?token=stub", flush=True)
    time.sleep(120)
elif mode == "hang":
    time.sleep(120)
elif mode == "briefexit":
    # long enough to be running when a second caller dedups onto it, short
    # enough to die while that caller is still waiting for a URL
    time.sleep(1.0)
elif mode == "fail":
    print("boom: no computed results yet", flush=True)
    sys.exit(3)
print("DONE", flush=True)
"""

#: The fixture experiment as YAML — the same shape ``make_experiment()`` builds,
#: so the served config can be parsed FROM the file the source route reads.
_EXPERIMENT_YAML = """\
name: {name}
start_ts: 2026-01-01
horizon_ts: 2026-01-15
unit_key: user_id
tags: [growth, checkout]
assignment:
  query: SELECT 1
  variants: [control, treatment]
  expected_split: {{control: 0.5, treatment: 0.5}}
alpha: 0.05
correction: none
comparisons:
  - metric: revenue
    is_main_metric: true
    method: {{name: t-test}}
"""

#: UI-1's fixture project: the same experiment, but written so the §8 matrix
#: PASSES — the assignment SQL selects the exposure contract and the metric SQL
#: joins the packaged macro. `_EXPERIMENT_YAML` above deliberately does not
#: (`query: SELECT 1`), which is what the editor's `force` path is tested
#: against: DASH-4's routes never validate, so M11 never needed a lint-clean
#: fixture and this is the first WP that does.
_VALID_EXPERIMENT_YAML = """\
# a comment the round-trip must preserve
name: {name}
start_ts: 2026-01-01
horizon_ts: 2026-01-15
unit_key: user_id
tags: [growth, checkout]
assignment:
  query: SELECT user_id, variant, exposure_ts FROM assignments
  variants: [control, treatment]
  expected_split: {{control: 0.5, treatment: 0.5}}
alpha: {alpha}
correction: none
comparisons:
  - metric: revenue
    is_main_metric: true
    method: {{name: t-test}}
"""

_METRIC_YAML = """\
name: revenue
type: sample
columns:
  variant: variant
  value: value
query: |
  {% import 'abkit_assignment.jinja' as ab %}
  SELECT _abk_exposures._abk_variant AS variant, t.user_id AS user_id, t.amount AS value
  FROM events t
  {{ ab.exposed_units() }}
"""


def write_project(root: Path, name: str = "dash_exp", *, alpha: str = "0.05"):
    """A minimal project that passes `abk run --steps validate`.

    Returns ``(experiment_path, parsed config)`` like :func:`write_experiment`,
    and additionally writes ``metrics/revenue.yml`` — without it every level-2
    save would fail on reference integrity, which is a real refusal but not the
    one a happy-path test means to exercise.
    """
    path = root / "experiments" / f"{name}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VALID_EXPERIMENT_YAML.format(name=name, alpha=alpha), encoding="utf-8")
    metrics = root / "metrics"
    metrics.mkdir(parents=True, exist_ok=True)
    (metrics / "revenue.yml").write_text(_METRIC_YAML, encoding="utf-8")
    return path, ExperimentConfig.from_yaml_file(path)


def write_experiment(root: Path, name: str = "dash_exp", *, file_stem: str | None = None):
    """Write an experiment YAML under *root* and return ``(path, parsed config)``.

    The config is parsed from the file it was just written to, so a served entry
    and the bytes on disk cannot disagree — which is the whole point for the
    source route and for the selector's shadow case (*file_stem* names the file
    something other than the experiment).
    """
    path = root / "experiments" / f"{file_stem or name}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_EXPERIMENT_YAML.format(name=name), encoding="utf-8")
    return path, ExperimentConfig.from_yaml_file(path)


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
        project_root: Path = ROOT,
        metrics: dict | None = None,
        with_manager: bool = False,
    ) -> None:
        self.manager = FakeDatabaseManager()
        self.tables = InternalTablesManager(self.manager)
        self.tables.ensure_tables()
        self.experiment = make_experiment()
        if seed:
            seed_series(self.tables, self.experiment)
        self.echo_lines: list[str] = []
        self.server, self.url = build_dashboard_server(
            project=PROJECT,
            project_root=project_root,
            experiments=(experiments if experiments is not None else [(EXP_PATH, self.experiment)]),
            tables=self.tables,
            initial_window=initial_window,
            profile=profile,
            jobs=jobs,
            metrics=metrics,
            # The report route's live-cohort seam (DASH-5): the SAME manager
            # ``tables`` wraps, exactly as `abk dashboard` will pass it.
            manager=self.manager if with_manager else None,
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


def poll_id_until_done(dash: Dashboard, job_id: str, timeout: float = 30.0) -> dict:
    """Poll ``/api/job/<id>`` until the pump reports a terminal status.

    Never ``proc.wait()``: the child exiting does not mean the pump has drained
    its stdout or written the status, so waiting on the process would race the
    very fields under assertion.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, snapshot = dash.get(f"/api/job/{job_id}", offset="0")
        assert status == 200
        if snapshot["status"] != "running":
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never finished")


def poll_until_done(dash: Dashboard, job, timeout: float = 30.0) -> dict:
    return poll_id_until_done(dash, job.id, timeout)


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


@pytest.fixture
def stub_cli(tmp_path, monkeypatch) -> Path:
    """Point the argv builders' interpreter prefix at the stub ``abk``.

    Patching the PREFIX rather than the builders keeps the real verb/flag
    composition — and ``_label_for``'s slice — under test.
    """
    script = tmp_path / "abk_stub.py"
    script.write_text(_ABK_STUB, encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "_CLI_PREFIX", (sys.executable, "-u", str(script)))
    return script


@pytest.fixture
def jobs_dash(tmp_path, stub_cli):
    """A dashboard over a real project root, with the stub CLI installed."""
    path, experiment = write_experiment(tmp_path)
    served = Dashboard(project_root=tmp_path, experiments=[(path, experiment)])
    yield served
    served.stop()


ROUTES = [
    "/",
    "/api/stats/dash_exp",
    "/api/jobs",
    "/api/job/abcd1234",
    # DASH-4's read route — the only one that returns file contents, so leaving
    # it out of this list would let an ungated version of it ship green.
    "/api/experiment-source/dash_exp",
    # DASH-5's report page — same reasoning: it renders an experiment's whole
    # readout, and it is the one route that answers HTML.
    "/experiment/dash_exp",
    # UI-1's refreshable selection: it enumerates every experiment in the
    # project, exactly like `GET /`.
    "/api/experiments",
    "/nope",
]

#: Every POST route, for the same gate. Kept beside :data:`ROUTES` and checked
#: against the AST the same way (UI-1): M11 checked only the GET list, so this
#: one could rot silently — and it is the list covering the routes that MUTATE.
POST_ROUTES = [
    "/api/run",
    "/api/unlock",
    "/api/clean",
    "/api/explore",
    "/api/job/abcd1234/stop",
    "/api/experiment/save",
    "/api/experiment/create",
    "/api/experiment/delete",
    "/api/reload",
]


def _routed_get_paths() -> set[str]:
    """Every literal path (or path prefix) ``_route_get`` dispatches on.

    Read off the AST rather than listed, so :data:`ROUTES` cannot silently rot
    the moment a WP adds a route: a `path == "/x"` comparison contributes
    ``"/x"``, and a ``path.startswith(_X_PREFIX)`` contributes that module
    constant's value. The token gate's coverage assertion below is only as
    honest as this extraction, which is why it resolves the constants instead of
    matching their names.
    """
    return _routed_paths("_route_get")


def _routed_post_paths() -> set[str]:
    """The same extraction over ``_route_post`` (UI-1).

    M11 wrote it for the GET dispatcher only, so :data:`POST_ROUTES` — the list
    covering the routes that MUTATE — was the hand-maintained one nothing
    checked. Editor routes are POST, so the honesty check has to cover both.
    """
    return _routed_paths("_route_post")


def _routed_paths(dispatcher: str) -> set[str]:
    """Every literal path (or prefix) *dispatcher* dispatches on.

    Handles both spellings the module uses: `path == "/x"` (a literal) and
    `path == _X_PATH` / `path.startswith(_X_PREFIX)` (a module constant, which
    is RESOLVED rather than matched by name — a name-matching gate would pass
    while pointing at the wrong string).
    """
    source = Path(dashboard_server.__file__).read_text(encoding="utf-8")
    body = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == dispatcher
    )
    paths: set[str] = set()
    for node in ast.walk(body):
        if isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Eq):
            right = node.comparators[0]
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                paths.add(right.value)
            elif isinstance(right, ast.Name):
                paths.add(getattr(dashboard_server, right.id))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            paths.add(getattr(dashboard_server, node.args[0].id))
    return paths


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

    def test_the_parametrized_route_list_covers_every_routed_get(self):
        """The list above is the gate; an un-listed route is an ungated route.

        DASH-4's review found exactly this rot once already (its new file-content
        route was missing from the list), so the list is now checked against what
        ``_route_get`` actually dispatches on.
        """
        uncovered = [
            routed
            for routed in _routed_get_paths()
            if not any(path == routed or path.startswith(routed) for path in ROUTES)
        ]
        assert uncovered == [], f"add these routes to ROUTES: {uncovered}"

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

    @pytest.mark.parametrize("path", POST_ROUTES)
    def test_every_job_route_refuses_an_untokened_post(self, dash, path):
        """The gate precedes routing, so a new POST route inherits it."""
        status, detail = request(f"{dash.base}{path}", data=b"{}", method="POST")
        assert (status, detail) == (403, "bad token")

    def test_the_parametrized_route_list_covers_every_routed_post(self):
        """The POST half of the same honesty check (UI-1).

        M11 AST-checked the GET list only, which left the routes that MUTATE
        covered by a hand-maintained list nothing verified — the exact rot
        DASH-4's review had already found once on the GET side.
        """
        uncovered = [
            routed
            for routed in _routed_post_paths()
            if not any(path == routed or path.startswith(routed) for path in POST_ROUTES)
        ]
        assert uncovered == [], f"add these routes to POST_ROUTES: {uncovered}"

    def test_a_tokened_post_to_an_unrouted_path_is_a_404(self, dash):
        status, detail = dash.post("/api/nope", {"select": "dash_exp"})
        assert status == 404
        assert "/api/nope" in detail


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

    def test_a_bad_window_is_rejected_without_waiting_for_the_db_lock(self, dash):
        """A request-level mistake must not queue behind a slow read.

        Holding ``db_lock`` is what makes this falsifiable: the safe row builder
        validates the preset before touching the DB either way, so "the read
        never happened" cannot tell a route-level pre-check from the builder's
        own. Waiting for the lock can — with the pre-check gone this request
        blocks until the ``with`` block ends and the client has timed out.
        """
        calls: list[str] = []
        dash.tables.load_results = lambda *a, **k: calls.append("read") or []  # type: ignore[method-assign]
        with dash.server.db_lock:
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

    @pytest.mark.parametrize("offset", ["abc", "-1", "1.5", "", "undefined", "9" * 16, "9" * 4400])
    def test_a_non_integer_offset_is_a_400(self, dash, offset):
        """Loud, unlike the donor's silent fallback to 0 (which rewinds a drawer).

        The empty case is why the query is parsed with ``keep_blank_values``:
        ``?offset=`` would otherwise be dropped and read as "no offset given",
        so a client bug would quietly resend the whole buffer. ``undefined`` is
        the same bug's other spelling. The two long cases are why the regex is
        length-bounded rather than ``\\d+``: past 4300 digits ``int()`` itself
        raises, and that would surface as a 500 on a bad request.
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


class TestSpawnedCommands:
    """What the cockpit hands to the OS (DASH-4 step 1) — argv, label, env."""

    def test_the_child_is_this_interpreters_abkit_not_a_path_lookup(self):
        """A bare ``abk`` would be whatever is on PATH — possibly another install
        entirely when the dashboard runs from an unactivated venv."""
        prefix = dashboard_server._CLI_PREFIX
        assert prefix[0] == sys.executable
        assert prefix[1] == "-c"
        assert "from abkit.cli.main import cli" in prefix[2]
        assert "os.getcwd()" in prefix[2]  # …and the CWD is dropped first
        assert "sys.argv[0] = 'abk'" in prefix[2]  # …and the child names itself

    def test_the_bootstrap_runs_the_cli_and_ignores_the_project_directory(self, tmp_path):
        """The claim "exactly as if typed", tested where it is easiest to break.

        A job spawns with ``cwd=<project root>`` — the OPERATOR's directory — and
        both ``-m`` and ``-c`` put that on ``sys.path[0]``. A file there named
        after anything abkit imports would then break every button; a console
        script (what typing ``abk`` runs) never does that. The second half runs
        the naive form to prove the hazard is real rather than theoretical.

        Needs an INSTALLED abkit, since the bootstrap drops the very directory a
        bare checkout would be importable from. CI installs it (``pip install
        -e .``); elsewhere this says so instead of asserting something weaker.
        """
        (tmp_path / "click.py").write_text("raise RuntimeError('shadowed')\n", encoding="utf-8")
        ours = subprocess.run(
            [*dashboard_server._CLI_PREFIX, "--version"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if ours.returncode != 0 and "No module named 'abkit'" in ours.stderr:
            pytest.skip("abkit is not installed here; every spawned job needs an install")
        assert ours.returncode == 0, ours.stderr
        assert __version__ in ours.stdout
        assert ours.stdout.startswith("abk,")  # click's prog_name, as if typed

        naive = subprocess.run(
            [sys.executable, "-m", "abkit.cli.main", "--version"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert naive.returncode != 0
        assert "shadowed" in naive.stderr

    @pytest.mark.parametrize(
        ("verb", "argv"),
        [
            (
                "run",
                dashboard_server._run_argv(select="experiments/a.yml", metric=None, profile=None),
            ),
            ("run", dashboard_server._run_argv(select="a", metric="revenue", profile="prod")),
            ("unlock", dashboard_server._unlock_argv(select="a", profile="prod")),
            ("clean", dashboard_server._clean_argv(select="a", profile=None)),
            ("clean", dashboard_server._clean_argv(select="a", profile="prod")),
            ("explore", dashboard_server._explore_argv(select="a", profile="prod")),
        ],
    )
    def test_every_flag_a_builder_passes_is_declared_by_that_cli_command(self, verb, argv):
        """The builders cannot drift from the CLI they name.

        A renamed or dropped option (``--metric``, ``--no-open``, ``--execute``)
        fails here instead of surfacing as a job that exits 2 in the drawer.
        """
        from abkit.cli.main import cli

        command = cli.commands[verb]
        declared = {opt for param in command.params for opt in param.opts}
        assert argv[3] == verb
        passed = {token for token in argv[4:] if token.startswith("--")}
        assert passed <= declared, f"{verb}: {sorted(passed - declared)}"

    def test_run_argv_is_the_select_plus_the_optional_metric_and_profile(self):
        prefix = list(dashboard_server._CLI_PREFIX)
        assert dashboard_server._run_argv(
            select="experiments/a.yml", metric=None, profile=None
        ) == [*prefix, "run", "--select", "experiments/a.yml"]
        assert dashboard_server._run_argv(
            select="experiments/a.yml", metric="revenue", profile="prod"
        ) == [
            *prefix,
            "run",
            "--select",
            "experiments/a.yml",
            "--metric",
            "revenue",
            "--profile",
            "prod",
        ]

    def test_clean_is_the_apply_form_and_never_the_prompting_one(self):
        argv = dashboard_server._clean_argv(select="a", profile=None)
        assert "--execute" in argv  # else the button is a no-op dry run
        assert "--orphaned-experiments" not in argv  # the one clean path that prompts

    def test_explore_does_not_open_a_second_browser(self):
        assert "--no-open" in dashboard_server._explore_argv(select="a", profile=None)

    def test_the_label_is_the_command_an_operator_would_have_typed(self):
        argv = dashboard_server._run_argv(
            select="experiments/a.yml", metric="revenue", profile="prod"
        )
        assert (
            dashboard_server._label_for(argv)
            == "abk run --select experiments/a.yml --metric revenue --profile prod"
        )
        # …including the flags the caller never chose, which is the point:
        assert "--execute" in dashboard_server._label_for(
            dashboard_server._clean_argv(select="a", profile=None)
        )

    def test_the_spawn_env_is_the_dashboards_plus_unbuffered_output(self, monkeypatch):
        monkeypatch.setenv("ABK_MARKER", "inherited")
        # Seeded so the "a copy, not os.environ" assertion below can actually
        # fail: with `env = os.environ` the write lands in the SERVING process.
        monkeypatch.setenv("PYTHONUNBUFFERED", "sentinel")
        env = dashboard_server._subprocess_env()
        assert env["PYTHONUNBUFFERED"] == "1"
        assert env["ABK_MARKER"] == "inherited"
        assert os.environ["PYTHONUNBUFFERED"] == "sentinel"


class TestSelectorIsThePath:
    """Why ``--select`` gets a path: a name can resolve to another experiment."""

    def test_the_selector_is_the_root_relative_path(self, tmp_path):
        path, experiment = write_experiment(tmp_path)
        assert (
            dashboard_server._selector_for(path, experiment, tmp_path) == "experiments/dash_exp.yml"
        )

    def test_a_name_selector_would_launch_the_wrong_experiment(self, tmp_path):
        """The hazard, reproduced with the CHILD's own resolver.

        ``select_configs`` tries ``experiments/<name>.yml`` before searching the
        ``name:`` fields, so a file named after another experiment shadows it —
        and the dashboard would run something other than the clicked row, with
        nothing saying so.
        """
        write_experiment(tmp_path, name="beta", file_stem="alpha")
        alpha_path, alpha = write_experiment(tmp_path, name="alpha", file_stem="one")

        by_name, _ = select_experiments(tmp_path, ("alpha",))
        assert [config.name for _p, config in by_name] == ["beta"]  # the shadow bites

        selector = dashboard_server._selector_for(alpha_path, alpha, tmp_path)
        by_path, _ = select_experiments(tmp_path, (selector,))
        assert [config.name for _p, config in by_path] == ["alpha"]  # the path does not

    def test_it_falls_back_to_the_name_when_the_path_has_no_directory_part(self, tmp_path):
        """Not something discovery produces — but a bare ``dash_exp.yml`` has no
        ``/``, so ``select_configs`` would read it as a NAME with a ``.yml`` glued
        on; the name is the honest form, and the verifier still proves it."""
        loose = tmp_path / "dash_exp.yml"
        loose.write_text(_EXPERIMENT_YAML.format(name="dash_exp"), encoding="utf-8")
        experiment = ExperimentConfig.from_yaml_file(loose)
        assert dashboard_server._selector_for(loose, experiment, tmp_path) == "dash_exp"

    def test_it_falls_back_to_the_name_outside_the_project_root(self, tmp_path):
        path, experiment = write_experiment(tmp_path)
        assert dashboard_server._selector_for(path, experiment, tmp_path / "elsewhere") == (
            "dash_exp"
        )

    def test_a_bracket_in_the_file_name_is_escaped_not_traded_for_the_name(self, tmp_path):
        """``checkout[v2].yml`` is a legal file name, and ``[1]`` raw is a
        character class — so the path is escaped rather than abandoned."""
        path, experiment = write_experiment(tmp_path, file_stem="dash[1]")
        selector = dashboard_server._selector_for(path, experiment, tmp_path)
        assert selector == "experiments/dash[[]1].yml"
        resolved, _ = select_experiments(tmp_path, (selector,))
        assert [config.name for _p, config in resolved] == ["dash_exp"]
        # …and the unescaped path really would have resolved nothing:
        raw, _ = select_experiments(tmp_path, ("experiments/dash[1].yml",))
        assert raw == []

    def test_a_star_in_the_file_name_cannot_pull_in_a_sibling(self, tmp_path):
        """The worse half of an unescaped metacharacter: not "no match" but the
        WRONG match — ``abk run`` would then run both experiments."""
        path, experiment = write_experiment(tmp_path, name="dash_exp", file_stem="star*")
        write_experiment(tmp_path, name="sibling", file_stem="starX")
        selector = dashboard_server._selector_for(path, experiment, tmp_path)
        assert selector == "experiments/star[*].yml"
        resolved, _ = select_experiments(tmp_path, (selector,))
        assert [config.name for _p, config in resolved] == ["dash_exp"]
        raw, _ = select_experiments(tmp_path, ("experiments/star*.yml",))
        assert sorted(config.name for _p, config in raw) == ["dash_exp", "sibling"]

    def test_a_question_mark_is_escaped_too(self, tmp_path):
        path, experiment = write_experiment(tmp_path, file_stem="q?")
        assert dashboard_server._selector_for(path, experiment, tmp_path) == "experiments/q[?].yml"

    def test_escaping_brackets_first_keeps_the_others_intact(self):
        assert dashboard_server._escape_glob("a[1]*?.yml") == "a[[]1][*][?].yml"


class TestRunRoute:
    def test_a_run_spawns_the_cli_with_the_path_selector(self, jobs_dash):
        status, reply = jobs_dash.post("/api/run", {"select": "dash_exp"})
        assert status == 200
        snapshot = poll_id_until_done(jobs_dash, reply["job_id"])
        assert (snapshot["status"], snapshot["returncode"]) == ("done", 0)
        assert snapshot["lines"][0] == "ARGV run --select experiments/dash_exp.yml"
        assert snapshot["kind"] == "run"
        assert snapshot["experiment"] == "dash_exp"
        assert snapshot["label"] == "abk run --select experiments/dash_exp.yml"

    def test_the_metric_rides_through_to_the_cli(self, jobs_dash):
        status, reply = jobs_dash.post("/api/run", {"select": "dash_exp", "metric": "revenue"})
        assert status == 200
        snapshot = poll_id_until_done(jobs_dash, reply["job_id"])
        assert snapshot["lines"][0] == (
            "ARGV run --select experiments/dash_exp.yml --metric revenue"
        )
        assert snapshot["label"].endswith("--metric revenue")

    def test_a_metric_the_experiment_does_not_declare_is_a_400(self, jobs_dash):
        status, detail = jobs_dash.post("/api/run", {"select": "dash_exp", "metric": "nope"})
        assert status == 400
        assert "not a configured comparison" in detail
        assert "revenue" in detail  # names what IS declared
        assert jobs_dash.get("/api/jobs")[1]["jobs"] == []  # nothing spawned

    def test_a_null_metric_reads_as_the_whole_experiment(self, jobs_dash):
        status, reply = jobs_dash.post("/api/run", {"select": "dash_exp", "metric": None})
        assert status == 200
        snapshot = poll_id_until_done(jobs_dash, reply["job_id"])
        assert "--metric" not in snapshot["lines"][0]

    @pytest.mark.parametrize("metric", ["", "   "])
    def test_a_BLANK_metric_is_refused_rather_than_read_as_absent(self, jobs_dash, metric):
        status, detail = jobs_dash.post("/api/run", {"select": "dash_exp", "metric": metric})
        assert status == 400
        assert "'metric' must be a non-empty string" in detail

    def test_an_unknown_experiment_is_a_400_because_the_name_is_in_the_BODY(self, jobs_dash):
        status, detail = jobs_dash.post("/api/run", {"select": "no_such_experiment"})
        assert status == 400
        assert "unknown experiment" in detail
        # …while the same name in a PATH keeps DASH-3's 404
        assert jobs_dash.get("/api/stats/no_such_experiment")[0] == 404

    @pytest.mark.parametrize(
        ("select", "says"),
        [
            (None, "'select' is required"),
            ("", "must be a non-empty string"),
            ("  ", "must be a non-empty string"),
            (7, "must be a non-empty string"),
            (["dash_exp"], "must be a non-empty string"),
            # The length cap, asserted by its OWN message: "unknown experiment"
            # would also be a 400 here, so a status-only assertion would pass
            # with the cap removed.
            ("x" * 201, "longer than 200 characters"),
        ],
    )
    def test_a_malformed_select_is_a_400(self, jobs_dash, select, says):
        status, detail = jobs_dash.post("/api/run", {"select": select})
        assert status == 400, detail
        assert says in detail

    @pytest.mark.parametrize("field", ["select", "metric"])
    def test_a_huge_field_is_refused_without_echoing_it_back(self, jobs_dash, field):
        """What the cap is FOR: an unbounded name would be quoted back inside the
        "unknown …" 400, turning a 4 MB body into a 4 MB error."""
        payload = {"select": "dash_exp", field: "x" * 4_000_000}
        status, detail = jobs_dash.post("/api/run", payload)
        assert status == 400
        assert len(detail) < 500
        assert "longer than 200 characters" in detail

    def test_an_unknown_field_is_refused_rather_than_silently_dropped(self, jobs_dash):
        """A client asking for ``full_refresh`` must not get a plain run."""
        status, detail = jobs_dash.post("/api/run", {"select": "dash_exp", "full_refresh": True})
        assert status == 400
        assert "full_refresh" in detail
        assert "this route accepts" in detail

    @pytest.mark.parametrize("route", ["/api/unlock", "/api/clean", "/api/explore"])
    def test_a_null_field_this_route_does_not_take_asks_for_nothing(
        self, jobs_dash, monkeypatch, route
    ):
        """``metric: null`` is how the documented convention spells "no metric",
        so a client helper that always emits it must not fail the other three
        buttons — while a NON-null unknown field is still refused."""
        monkeypatch.setenv("ABK_STUB", "explore")
        status, reply = jobs_dash.post(route, {"select": "dash_exp", "metric": None})
        assert status == 200, reply
        status, detail = jobs_dash.post(route, {"select": "dash_exp", "metric": "revenue"})
        assert status == 400
        assert "unknown field(s) 'metric'" in detail

    def test_the_profile_reaches_the_argv(self, tmp_path, stub_cli):
        path, experiment = write_experiment(tmp_path)
        served = Dashboard(project_root=tmp_path, experiments=[(path, experiment)], profile="prod")
        try:
            status, reply = served.post("/api/run", {"select": "dash_exp"})
            assert status == 200
            snapshot = poll_id_until_done(served, reply["job_id"])
            assert snapshot["lines"][0].endswith("--profile prod")
        finally:
            served.stop()

    def test_a_second_run_while_one_is_running_is_the_busy_400(self, jobs_dash, monkeypatch):
        monkeypatch.setenv("ABK_STUB", "hang")
        status, reply = jobs_dash.post("/api/run", {"select": "dash_exp"})
        assert status == 200
        status, detail = jobs_dash.post("/api/run", {"select": "dash_exp"})
        assert status == 400
        assert detail == "a pipeline job is already running"
        assert jobs_dash.get("/api/jobs")[1]["pipeline_active"] is True
        assert jobs_dash.post(f"/api/job/{reply['job_id']}/stop", {})[0] == 200

    def test_a_route_racing_the_teardown_is_a_503_not_the_busy_400(self, jobs_dash):
        """``None`` means "try later"; a shut-down registry is not that."""
        jobs_dash.server.jobs.shutdown()
        status, detail = jobs_dash.post("/api/run", {"select": "dash_exp"})
        assert status == 503
        assert "shut down" in detail

    def test_a_spawn_that_cannot_start_at_all_is_a_500_naming_the_root(
        self, jobs_dash, monkeypatch
    ):
        """An ``OSError`` out of ``Popen`` is the environment, not the request —
        but a bare errno on a POST reads like a routing bug."""
        monkeypatch.setattr(
            dashboard_server, "_CLI_PREFIX", ("/nonexistent/python", "-m", "abkit.cli.main")
        )
        status, detail = jobs_dash.post("/api/run", {"select": "dash_exp"})
        assert status == 500
        assert "could not start a subprocess in" in detail
        assert str(jobs_dash.server.project_root) in detail
        assert jobs_dash.thread.is_alive()


class TestTheSelectorIsVerifiedBeforeSpawning:
    """A stale boot snapshot must not launch a green job that did nothing."""

    @pytest.mark.parametrize("route", ["/api/run", "/api/unlock", "/api/clean", "/api/explore"])
    def test_a_yaml_that_moved_since_boot_is_refused_not_spawned(self, jobs_dash, tmp_path, route):
        """`abk run --select <nomatch>` warns, prints "Nothing selected." and
        exits **0** — so without this check the drawer would show a successful
        Run that computed nothing (`unlock`/`clean` likewise)."""
        (tmp_path / "experiments" / "dash_exp.yml").rename(tmp_path / "experiments" / "moved.yml")
        status, detail = jobs_dash.post(route, {"select": "dash_exp"})
        assert status == 400
        assert "no longer resolves to dash_exp" in detail
        # UI-1: the remedy is a reload, not a restart — the cockpit can re-read
        # its own selection now.
        assert "press Reload configs" in detail
        assert jobs_dash.get("/api/jobs")[1]["jobs"] == []

    def test_the_name_fallback_cannot_launch_the_wrong_experiment(self, tmp_path, stub_cli):
        """The remaining fallback still lands on a NAME, which resolves
        file-first: a file named after another experiment shadows it, and running
        `bar` for the `foo` row would delete the wrong rows (`clean --execute`)
        or rewrite the wrong YAML (an explore Apply)."""
        write_experiment(tmp_path, name="bar", file_stem="foo")  # the decoy
        outside = tmp_path.parent / f"{tmp_path.name}_outside"  # no path form exists
        foo_path, foo = write_experiment(outside, name="foo")
        assert dashboard_server._selector_for(foo_path, foo, tmp_path) == "foo"
        by_name, _ = select_experiments(tmp_path, ("foo",))
        assert [config.name for _p, config in by_name] == ["bar"]  # the shadow is live

        served = Dashboard(project_root=tmp_path, experiments=[(foo_path, foo)], seed=False)
        try:
            status, detail = served.post("/api/run", {"select": "foo"})
            assert status == 400
            assert "no longer resolves to foo (it now matches bar)" in detail
            assert served.get("/api/jobs")[1]["jobs"] == []
        finally:
            served.stop()

    def test_an_escaped_metacharacter_path_still_spawns(self, tmp_path, stub_cli):
        path, experiment = write_experiment(tmp_path, file_stem="dash[1]")
        served = Dashboard(project_root=tmp_path, experiments=[(path, experiment)], seed=False)
        try:
            status, reply = served.post("/api/run", {"select": "dash_exp"})
            assert status == 200, reply
            snapshot = poll_id_until_done(served, reply["job_id"])
            assert snapshot["lines"][0] == "ARGV run --select experiments/dash[[]1].yml"
        finally:
            served.stop()

    def test_a_project_root_that_vanished_is_refused_before_the_spawn(self, tmp_path, stub_cli):
        """Also the honest answer for a non-default ``paths.experiments``, which
        the CLI's selector cannot reach at all."""
        path, experiment = write_experiment(tmp_path)
        served = Dashboard(
            project_root=tmp_path / "gone", experiments=[(path, experiment)], seed=False
        )
        try:
            status, detail = served.post("/api/run", {"select": "dash_exp"})
            assert status == 400
            assert "it now matches nothing" in detail
        finally:
            served.stop()

    def test_a_broken_sibling_config_is_reported_rather_than_a_500(self, tmp_path, stub_cli):
        """`select_experiments` parses; a file it must parse to answer can be
        mid-edit, and that is a 400 naming the file (POST ValueError → 400)."""
        path, experiment = write_experiment(tmp_path)
        served = Dashboard(project_root=tmp_path, experiments=[(path, experiment)], seed=False)
        try:
            path.write_text("name: dash_exp\nstart_ts: not-a-date\n", encoding="utf-8")
            status, detail = served.post("/api/run", {"select": "dash_exp"})
            assert status == 400
            assert "Failed to parse experiment config" in detail
        finally:
            served.stop()


class TestUnlockAndCleanRoutes:
    @pytest.mark.parametrize(
        ("route", "expected"),
        [
            ("/api/unlock", "ARGV unlock --select experiments/dash_exp.yml"),
            ("/api/clean", "ARGV clean --select experiments/dash_exp.yml --execute"),
        ],
    )
    def test_the_verb_and_its_flags_reach_the_child(self, jobs_dash, route, expected):
        status, reply = jobs_dash.post(route, {"select": "dash_exp"})
        assert status == 200
        snapshot = poll_id_until_done(jobs_dash, reply["job_id"])
        assert snapshot["lines"][0] == expected
        assert snapshot["status"] == "done"

    def test_they_share_the_one_at_a_time_gate_with_run(self, jobs_dash, monkeypatch):
        monkeypatch.setenv("ABK_STUB", "hang")
        status, reply = jobs_dash.post("/api/unlock", {"select": "dash_exp"})
        assert status == 200
        for route in ("/api/run", "/api/clean", "/api/unlock"):
            assert jobs_dash.post(route, {"select": "dash_exp"})[0] == 400, route
        assert jobs_dash.post(f"/api/job/{reply['job_id']}/stop", {})[0] == 200

    def test_neither_takes_a_metric(self, jobs_dash):
        status, detail = jobs_dash.post("/api/clean", {"select": "dash_exp", "metric": "revenue"})
        assert status == 400
        assert "metric" in detail


class TestExploreRoute:
    def test_the_url_is_scraped_past_the_clis_own_header_line(self, jobs_dash, monkeypatch):
        """``abk explore`` prints ``Explore: <experiment>`` BEFORE the URL.

        The donor's ported predicate (``"Tuner:" in line``) would have matched
        that header and handed the client an experiment name as a URL.
        """
        monkeypatch.setenv("ABK_STUB", "explore")
        status, reply = jobs_dash.post("/api/explore", {"select": "dash_exp"})
        assert status == 200, reply
        assert reply["url"] == "http://127.0.0.1:9/?token=stub"
        snapshot = jobs_dash.get(f"/api/job/{reply['job_id']}")[1]
        assert snapshot["kind"] == "explore"
        assert snapshot["url"] == reply["url"]
        assert snapshot["lines"][0] == "ARGV explore --select experiments/dash_exp.yml --no-open"

    def test_a_second_click_reopens_the_same_cockpit(self, jobs_dash, monkeypatch):
        monkeypatch.setenv("ABK_STUB", "explore")
        first = jobs_dash.post("/api/explore", {"select": "dash_exp"})[1]
        second = jobs_dash.post("/api/explore", {"select": "dash_exp"})[1]
        assert second == first
        assert len(jobs_dash.get("/api/jobs")[1]["jobs"]) == 1

    def test_a_concurrent_double_click_starts_exactly_one_cockpit(self, jobs_dash, monkeypatch):
        """The dedup is atomic (``spawn_deduped``), not check-then-spawn.

        ``spawn`` is slowed so the race window is guaranteed open: two sessions
        on one experiment both write the YAML from their own snapshot on Apply.
        """
        monkeypatch.setenv("ABK_STUB", "explore")
        original = JobManager.spawn

        def slow_spawn(self, *args, **kwargs):
            time.sleep(0.3)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(JobManager, "spawn", slow_spawn)
        barrier = threading.Barrier(2)
        replies: list = []
        guard = threading.Lock()

        def click() -> None:
            barrier.wait()
            reply = jobs_dash.post("/api/explore", {"select": "dash_exp"})
            with guard:
                replies.append(reply)

        threads = [threading.Thread(target=click) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert [status for status, _ in replies] == [200, 200]
        assert len({reply["job_id"] for _s, reply in replies}) == 1
        assert len(jobs_dash.get("/api/jobs")[1]["jobs"]) == 1

    def test_explore_is_not_gated_by_a_pipeline_job_in_either_direction(
        self, jobs_dash, monkeypatch
    ):
        monkeypatch.setenv("ABK_STUB", "hang")
        run = jobs_dash.post("/api/run", {"select": "dash_exp"})[1]
        monkeypatch.setenv("ABK_STUB", "explore")
        status, reply = jobs_dash.post("/api/explore", {"select": "dash_exp"})
        assert status == 200
        assert jobs_dash.get("/api/jobs")[1]["pipeline_active"] is True
        for job_id in (run["job_id"], reply["job_id"]):
            assert jobs_dash.post(f"/api/job/{job_id}/stop", {})[0] == 200

    def test_a_cockpit_that_never_prints_a_url_is_stopped_and_reported(
        self, jobs_dash, monkeypatch
    ):
        monkeypatch.setenv("ABK_STUB", "hang")
        jobs_dash.server.explore_url_timeout = 0.5
        status, detail = jobs_dash.post("/api/explore", {"select": "dash_exp"})
        assert status == 400
        assert "did not start" in detail
        assert "ARGV explore" in detail  # the child's own output rides along
        # The terminate is in flight when the 400 is written, so the STATUS
        # assertion has to be the settled one — "running or stopped" would pass
        # for a route that never stopped anything.
        listed = jobs_dash.get("/api/jobs")[1]["jobs"][0]
        assert poll_id_until_done(jobs_dash, listed["id"])["status"] == "stopped"

    def test_a_timeout_never_kills_a_cockpit_this_request_did_not_spawn(
        self, jobs_dash, monkeypatch
    ):
        """Only what we started. Someone else's session may just be slower than
        our deadline, and killing it turns one slow tab into two failures."""
        monkeypatch.setenv("ABK_STUB", "hang")
        theirs, created = jobs_dash.server.jobs.spawn_deduped(
            "explore",
            "explore --select dash_exp",
            dashboard_server._explore_argv(select="experiments/dash_exp.yml", profile=None),
            cwd=jobs_dash.server.project_root,
            env=dashboard_server._subprocess_env(),
            experiment="dash_exp",
        )
        assert created is True
        jobs_dash.server.explore_url_timeout = 0.5
        status, detail = jobs_dash.post("/api/explore", {"select": "dash_exp"})
        assert status == 400
        # …and the message must not claim it failed: it is still running
        assert "is still starting" in detail
        assert "did not start" not in detail
        assert jobs_dash.get(f"/api/job/{theirs.id}")[1]["status"] == "running"
        assert len(jobs_dash.get("/api/jobs")[1]["jobs"]) == 1  # no second spawn either

    def test_a_repeat_click_does_not_wait_out_the_spawning_callers_timeout(
        self, jobs_dash, monkeypatch
    ):
        """Every waiter holds a request thread, so only the caller that started
        the cockpit pays the full 90 s; repeat clicks are answered in seconds."""
        monkeypatch.setenv("ABK_STUB", "hang")
        _theirs, created = jobs_dash.server.jobs.spawn_deduped(
            "explore",
            "explore --select dash_exp",
            dashboard_server._explore_argv(select="experiments/dash_exp.yml", profile=None),
            cwd=jobs_dash.server.project_root,
            env=dashboard_server._subprocess_env(),
            experiment="dash_exp",
        )
        assert created is True
        jobs_dash.server.explore_url_timeout = 3600.0  # the spawning caller's budget
        monkeypatch.setattr(dashboard_server, "_EXPLORE_DEDUP_WAIT", 0.5)
        started = time.monotonic()
        status, detail = jobs_dash.post("/api/explore", {"select": "dash_exp"})
        elapsed = time.monotonic() - started
        assert status == 400
        assert "is still starting" in detail
        assert elapsed < 30, elapsed  # not the 3600 s the first caller waits

    def test_a_cockpit_that_dies_under_a_second_caller_is_not_called_starting(
        self, jobs_dash, monkeypatch
    ):
        """The wait also ends when the child EXITS, and the message has to say
        which — inferring it from "did I spawn this?" tells the second caller a
        dead cockpit "is still starting" (the D2 noop exits in about a second)."""
        monkeypatch.setenv("ABK_STUB", "briefexit")
        _theirs, created = jobs_dash.server.jobs.spawn_deduped(
            "explore",
            "explore --select dash_exp",
            dashboard_server._explore_argv(select="experiments/dash_exp.yml", profile=None),
            cwd=jobs_dash.server.project_root,
            env=dashboard_server._subprocess_env(),
            experiment="dash_exp",
        )
        assert created is True
        jobs_dash.server.explore_url_timeout = 30.0
        status, detail = jobs_dash.post("/api/explore", {"select": "dash_exp"})
        assert status == 400
        assert "exited without serving (done)" in detail
        assert "is still starting" not in detail

    def test_a_cockpit_that_exits_is_reported_with_its_own_reason(self, jobs_dash, monkeypatch):
        """The D2 noop: `abk explore` on a never-run project exits 0 with a
        message, so the wait ends early rather than on the 90 s timeout."""
        monkeypatch.setenv("ABK_STUB", "fail")
        status, detail = jobs_dash.post("/api/explore", {"select": "dash_exp"})
        assert status == 400
        assert "no computed results yet" in detail

    def test_an_unknown_experiment_never_spawns(self, jobs_dash):
        status, detail = jobs_dash.post("/api/explore", {"select": "nope"})
        assert status == 400
        assert jobs_dash.get("/api/jobs")[1]["jobs"] == []


class TestStopRoute:
    def test_stopping_a_running_job_terminates_it(self, jobs_dash, monkeypatch):
        monkeypatch.setenv("ABK_STUB", "hang")
        reply = jobs_dash.post("/api/run", {"select": "dash_exp"})[1]
        status, body = jobs_dash.post(f"/api/job/{reply['job_id']}/stop", {})
        assert (status, body) == (200, {"ok": True})
        snapshot = poll_id_until_done(jobs_dash, reply["job_id"])
        assert snapshot["status"] == "stopped"

    def test_an_unknown_job_is_a_404(self, jobs_dash):
        status, detail = jobs_dash.post("/api/job/deadbeef/stop", {})
        assert status == 404
        assert "unknown job: deadbeef" in detail

    def test_a_finished_job_is_a_400_not_a_404(self, jobs_dash):
        """The donor conflates the two, which reads as "your id is wrong" for a
        job that simply finished a moment earlier."""
        reply = jobs_dash.post("/api/run", {"select": "dash_exp"})[1]
        poll_id_until_done(jobs_dash, reply["job_id"])
        status, detail = jobs_dash.post(f"/api/job/{reply['job_id']}/stop", {})
        assert status == 400
        assert "is not running" in detail

    def test_the_suffix_is_required(self, jobs_dash):
        reply = jobs_dash.post("/api/run", {"select": "dash_exp"})[1]
        poll_id_until_done(jobs_dash, reply["job_id"])
        status, detail = jobs_dash.post(f"/api/job/{reply['job_id']}", {})
        assert status == 404
        assert "not found" in detail

    def test_a_stop_takes_no_body(self, jobs_dash, monkeypatch):
        """It addresses the job in the PATH, so a body is not even read."""
        monkeypatch.setenv("ABK_STUB", "hang")
        reply = jobs_dash.post("/api/run", {"select": "dash_exp"})[1]
        assert jobs_dash.post(f"/api/job/{reply['job_id']}/stop", raw=b"")[0] == 200


class TestExperimentSourceRoute:
    def test_it_returns_the_yaml_text_and_the_rows_file_path(self, jobs_dash, tmp_path):
        status, reply = jobs_dash.get("/api/experiment-source/dash_exp")
        assert status == 200
        assert reply["name"] == "dash_exp"
        assert reply["path"] == "experiments/dash_exp.yml"
        assert reply["truncated"] is False
        assert reply["yaml_text"] == (tmp_path / "experiments" / "dash_exp.yml").read_text()
        # the same string the row carries as `file`, so the client needs no
        # second derivation
        assert jobs_dash.get("/api/stats/dash_exp")[1]["file"] == reply["path"]

    def test_an_unknown_experiment_is_a_404(self, jobs_dash):
        status, detail = jobs_dash.get("/api/experiment-source/nope")
        assert status == 404
        assert "unknown experiment: nope" in detail

    def test_a_yaml_that_vanished_since_boot_is_a_404_that_says_reload(self, jobs_dash, tmp_path):
        (tmp_path / "experiments" / "dash_exp.yml").unlink()
        status, detail = jobs_dash.get("/api/experiment-source/dash_exp")
        assert status == 404
        assert "press Reload configs" in detail

    def test_an_oversized_file_is_truncated_and_says_so(self, jobs_dash, tmp_path, monkeypatch):
        monkeypatch.setattr(dashboard_server, "_MAX_SOURCE_BYTES", 32)
        status, reply = jobs_dash.get("/api/experiment-source/dash_exp")
        assert status == 200
        assert reply["truncated"] is True
        assert len(reply["yaml_text"]) <= 32

    def test_undecodable_bytes_are_replaced_rather_than_a_500(self, jobs_dash, tmp_path):
        (tmp_path / "experiments" / "dash_exp.yml").write_bytes(b"name: \xff\xfe\n")
        status, reply = jobs_dash.get("/api/experiment-source/dash_exp")
        assert status == 200
        assert "�" in reply["yaml_text"]

    def test_the_source_route_is_not_itself_a_save_endpoint(self, jobs_dash):
        """The read route stays a read route — the editor's verbs are elsewhere.

        UI-1 added save/create/delete under ``/api/experiment/``, deliberately
        NOT as a POST to this path: a POST here would be routed by the same
        prefix that reads a NAME out of the URL, and ``/api/experiment-source/
        save`` would then be ambiguous with an experiment called ``save``.
        """
        status, _detail = jobs_dash.post(
            "/api/experiment-source/dash_exp", {"yaml_text": "name: hacked"}
        )
        assert status == 404
        assert (jobs_dash.server.project_root / "experiments" / "dash_exp.yml").read_text() != (
            "name: hacked"
        )

    def test_it_carries_the_digest_the_editor_saves_against(self, jobs_dash, tmp_path):
        """UI-1: the read hands out the concurrency token with the text."""
        from abkit.tuning.config_files import text_digest

        status, reply = jobs_dash.get("/api/experiment-source/dash_exp")
        assert status == 200
        assert reply["editable"] is True
        assert reply["digest"] == text_digest(
            (tmp_path / "experiments" / "dash_exp.yml").read_text(encoding="utf-8")
        )

    def test_a_truncated_file_is_handed_out_with_no_digest_at_all(
        self, jobs_dash, tmp_path, monkeypatch
    ):
        """A digest over a PREFIX would let a save silently drop the tail."""
        monkeypatch.setattr(dashboard_server, "_MAX_SOURCE_BYTES", 40)
        status, reply = jobs_dash.get("/api/experiment-source/dash_exp")
        assert status == 200
        assert (reply["truncated"], reply["digest"], reply["editable"]) == (True, None, False)


@pytest.fixture
def editor_dash(tmp_path, stub_cli):
    """A dashboard over a project that PASSES `abk run --steps validate`.

    UI-1's routes validate what they write, so unlike `jobs_dash` this fixture
    needs a coherent project: a metric library, and an assignment SQL that
    selects the exposure contract.
    """
    path, experiment = write_project(tmp_path)
    served = Dashboard(project_root=tmp_path, experiments=[(path, experiment)])
    yield served
    served.stop()


def source_of(dash: Dashboard, name: str = "dash_exp") -> dict:
    status, reply = dash.get(f"/api/experiment-source/{name}")
    assert status == 200, reply
    return reply


class TestEditorRoutes:
    """UI-1: save / create / delete / reload — the dashboard's CRUD seam.

    The invariant M11 wrote as "never writes a config" was restated by this WP
    as *computes no statistic and takes no pipeline lock* — which is what its
    two gates always checked, and which these routes keep (`TestLauncherOnly`
    exercises them). What they add is a write, and everything below is about
    the write being safe: validated on both levels, archived byte-verbatim,
    atomic, refused when something else is using the file, and followed by a
    re-resolution so the page never shows a config that is no longer on disk.
    """

    def test_a_save_round_trips_the_text_verbatim_and_archives_the_previous(self, editor_dash):
        root = editor_dash.server.project_root
        path = root / "experiments" / "dash_exp.yml"
        before = path.read_text(encoding="utf-8")
        edited = before.replace("alpha: 0.05", "alpha: 0.01")

        status, reply = editor_dash.post(
            "/api/experiment/save",
            {"select": "dash_exp", "text": edited, "digest": source_of(editor_dash)["digest"]},
        )

        assert status == 200, reply
        # byte-for-byte what was posted — comments and layout included, which
        # `abk explore`'s re-emitting Apply cannot promise.
        assert path.read_text(encoding="utf-8") == edited
        assert "# a comment the round-trip must preserve" in path.read_text(encoding="utf-8")
        archived = root / reply["archived"]
        assert archived.read_text(encoding="utf-8") == before
        assert ".history" in reply["archived"]
        assert reply["name"] == "dash_exp"
        assert reply["renamed_from"] is None
        assert reply["digest"] == source_of(editor_dash)["digest"]

    def test_the_save_is_visible_to_every_other_route_without_a_restart(self, editor_dash):
        """The reload is the point: M11's boot snapshot would still say 0.05."""
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        edited = path.read_text(encoding="utf-8").replace("alpha: 0.05", "alpha: 0.01")
        status, reply = editor_dash.post(
            "/api/experiment/save", {"select": "dash_exp", "text": edited}
        )
        assert status == 200, reply

        _path, served = editor_dash.server.experiment_entry("dash_exp")
        assert served.alpha == 0.01
        # …and the re-baked page carries the new list, so a browser refresh
        # cannot paint the pre-save one.
        assert editor_dash.get("/")[0] == 200
        names = [entry["name"] for entry in baked_payload(editor_dash.get("/")[1])["experiments"]]
        assert names == ["dash_exp"]

    def test_invalid_yaml_writes_nothing(self, editor_dash):
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        before = path.read_bytes()
        status, detail = editor_dash.post(
            "/api/experiment/save", {"select": "dash_exp", "text": "name: [unclosed"}
        )
        assert status == 400
        assert "invalid YAML" in detail
        assert path.read_bytes() == before
        assert not list((path.parent / ".history").rglob("*.yml"))  # not even archived

    def test_a_config_that_would_not_run_is_refused_and_the_message_says_why(self, editor_dash):
        """Level 2 — the §8 matrix — decides, not just pydantic."""
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        before = path.read_bytes()
        broken = path.read_text(encoding="utf-8").replace("metric: revenue", "metric: nope")
        status, detail = editor_dash.post(
            "/api/experiment/save", {"select": "dash_exp", "text": broken}
        )
        assert status == 400
        assert "no metric named 'nope'" in detail
        assert path.read_bytes() == before

    def test_force_saves_it_anyway_and_says_the_run_will_refuse_it(self, editor_dash):
        """The editor has to be usable ON a project that does not lint yet."""
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        broken = path.read_text(encoding="utf-8").replace("metric: revenue", "metric: nope")
        status, reply = editor_dash.post(
            "/api/experiment/save", {"select": "dash_exp", "text": broken, "force": True}
        )
        assert status == 200, reply
        assert path.read_text(encoding="utf-8") == broken
        assert any("SAVED WITH AN ERROR" in w for w in reply["warnings"])
        assert any("no metric named 'nope'" in w for w in reply["warnings"])

    def test_level_1_is_never_forceable(self, editor_dash):
        """A file that is not an ExperimentConfig cannot be served as a row."""
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        before = path.read_bytes()
        status, detail = editor_dash.post(
            "/api/experiment/save",
            {"select": "dash_exp", "text": "name: dash_exp\nunit_key: user_id\n", "force": True},
        )
        assert status == 400
        assert "invalid experiment config" in detail
        assert path.read_bytes() == before

    @pytest.mark.parametrize("force", ["true", 1, "yes"])
    def test_force_must_be_a_real_boolean(self, editor_dash, force):
        """A truthy STRING must not arm an override the client did not ask for."""
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        status, detail = editor_dash.post(
            "/api/experiment/save",
            {"select": "dash_exp", "text": path.read_text(encoding="utf-8"), "force": force},
        )
        assert status == 400
        assert "'force' must be true or false" in detail

    def test_a_stale_digest_refuses_rather_than_clobbering(self, editor_dash):
        """The `abk explore` Apply case: someone else wrote between open and save."""
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        opened = source_of(editor_dash)
        path.write_text(
            path.read_text(encoding="utf-8").replace("alpha: 0.05", "alpha: 0.02"),
            encoding="utf-8",
        )
        theirs = path.read_bytes()

        status, detail = editor_dash.post(
            "/api/experiment/save",
            {
                "select": "dash_exp",
                "text": opened["yaml_text"].replace("alpha: 0.05", "alpha: 0.03"),
                "digest": opened["digest"],
            },
        )

        assert status == 400
        assert "changed on disk" in detail
        assert path.read_bytes() == theirs  # their write survives

    def test_a_save_without_a_digest_is_allowed(self, editor_dash):
        """Optimistic concurrency is opt-in per request, like the donor's."""
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        edited = path.read_text(encoding="utf-8").replace("alpha: 0.05", "alpha: 0.04")
        status, reply = editor_dash.post(
            "/api/experiment/save", {"select": "dash_exp", "text": edited}
        )
        assert status == 200, reply

    @pytest.mark.parametrize("route", ["/api/experiment/save", "/api/experiment/delete"])
    def test_editing_an_experiment_with_a_running_job_is_refused(
        self, editor_dash, monkeypatch, route
    ):
        """A live `abk run` already read the config; a live cockpit will overwrite it."""
        monkeypatch.setenv("ABK_STUB", "hang")
        status, reply = editor_dash.post("/api/run", {"select": "dash_exp"})
        assert status == 200, reply
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        before = path.read_bytes()

        body: dict = {"select": "dash_exp"}
        if route.endswith("/save"):
            body["text"] = path.read_text(encoding="utf-8")
        status, detail = editor_dash.post(route, body)

        assert status == 400
        assert "running 'run' job" in detail
        assert path.read_bytes() == before
        editor_dash.post(f"/api/job/{reply['job_id']}/stop", {})

    def test_a_rename_is_allowed_and_says_the_history_does_not_follow(self, editor_dash):
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        renamed = path.read_text(encoding="utf-8").replace("name: dash_exp", "name: dash_exp_v2")
        status, reply = editor_dash.post(
            "/api/experiment/save", {"select": "dash_exp", "text": renamed}
        )
        assert status == 200, reply
        assert reply["renamed_from"] == "dash_exp"
        assert reply["name"] == "dash_exp_v2"
        assert any("persisted rows are still keyed by the OLD name" in w for w in reply["warnings"])
        # the archive is keyed by the OLD name — it is the old config preserved
        assert "dash_exp/" in reply["archived"].replace("\\", "/")
        assert editor_dash.server.experiment_entry("dash_exp") is None
        assert editor_dash.server.experiment_entry("dash_exp_v2") is not None

    def test_an_unknown_experiment_is_a_400_because_the_name_is_in_the_body(self, editor_dash):
        status, detail = editor_dash.post(
            "/api/experiment/save", {"select": "nope", "text": "name: nope\n"}
        )
        assert status == 400
        assert "unknown experiment" in detail

    # -- create ---------------------------------------------------------------

    def test_a_create_writes_a_new_file_named_after_the_config(self, editor_dash):
        root = editor_dash.server.project_root
        text = _VALID_EXPERIMENT_YAML.format(name="dash_new", alpha="0.05")
        status, reply = editor_dash.post("/api/experiment/create", {"text": text})

        assert status == 200, reply
        assert reply["path"] == "experiments/dash_new.yml"
        assert (root / "experiments" / "dash_new.yml").read_text(encoding="utf-8") == text
        assert reply["archived"] is None  # nothing existed to preserve
        assert reply["in_selection"] is True
        assert {entry["name"] for entry in reply["experiments"]} == {"dash_exp", "dash_new"}
        # …and it is immediately addressable, without a restart
        assert editor_dash.get("/api/stats/dash_new")[0] == 200

    def test_a_create_into_a_subfolder_lands_where_discovery_looks(self, editor_dash):
        text = _VALID_EXPERIMENT_YAML.format(name="dash_nested", alpha="0.05")
        status, reply = editor_dash.post(
            "/api/experiment/create", {"text": text, "folder": "growth"}
        )
        assert status == 200, reply
        assert reply["path"] == "experiments/growth/dash_nested.yml"
        assert editor_dash.server.experiment_entry("dash_nested") is not None

    @pytest.mark.parametrize("folder", ["../../etc", "/etc", ".hidden", "a/../../b"])
    def test_a_folder_cannot_escape_the_experiments_directory(self, editor_dash, folder):
        text = _VALID_EXPERIMENT_YAML.format(name="dash_escape", alpha="0.05")
        status, detail = editor_dash.post(
            "/api/experiment/create", {"text": text, "folder": folder}
        )
        assert status == 400, detail
        assert not list(editor_dash.server.project_root.rglob("dash_escape.yml"))

    def test_a_duplicate_name_is_refused_against_the_WHOLE_project(self, editor_dash):
        """Not against the served selection: a duplicate corrupts the shared tables."""
        text = _VALID_EXPERIMENT_YAML.format(name="dash_exp", alpha="0.05")
        status, detail = editor_dash.post(
            "/api/experiment/create", {"text": text, "folder": "growth"}
        )
        assert status == 400
        assert "already used by experiments/dash_exp.yml" in detail

    def test_a_name_the_metric_namespace_owns_is_refused(self, editor_dash):
        """Experiments and metrics share ONE namespace (cli-and-dx §1)."""
        text = _VALID_EXPERIMENT_YAML.format(name="revenue", alpha="0.05").replace(
            "name: revenue", "name: revenue", 1
        )
        status, detail = editor_dash.post("/api/experiment/create", {"text": text})
        assert status == 400
        assert "share ONE namespace" in detail

    def test_creating_over_an_existing_file_is_refused(self, editor_dash):
        """Even when the name is free — the file is what would be lost."""
        root = editor_dash.server.project_root
        (root / "experiments" / "dash_other.yml").write_text("# hand-written\n", encoding="utf-8")
        text = _VALID_EXPERIMENT_YAML.format(name="dash_other", alpha="0.05")
        status, detail = editor_dash.post("/api/experiment/create", {"text": text})
        assert status == 400
        assert "already exists" in detail
        assert (root / "experiments" / "dash_other.yml").read_text() == "# hand-written\n"

    def test_a_create_outside_the_selection_says_so_instead_of_vanishing(self, tmp_path, stub_cli):
        path, experiment = write_project(tmp_path)
        served = Dashboard(project_root=tmp_path, experiments=[(path, experiment)])
        served.server.selectors = ("dash_exp",)
        try:
            text = _VALID_EXPERIMENT_YAML.format(name="dash_hidden", alpha="0.05")
            status, reply = served.post("/api/experiment/create", {"text": text})
            assert status == 200, reply
            assert reply["in_selection"] is False
            assert any("outside this dashboard's selection" in w for w in reply["warnings"])
            assert (tmp_path / "experiments" / "dash_hidden.yml").exists()
        finally:
            served.stop()

    # -- delete ---------------------------------------------------------------

    def test_a_delete_archives_the_file_then_removes_it(self, editor_dash):
        root = editor_dash.server.project_root
        path = root / "experiments" / "dash_exp.yml"
        before = path.read_bytes()

        status, reply = editor_dash.post("/api/experiment/delete", {"select": "dash_exp"})

        assert status == 200, reply
        assert not path.exists()
        assert (root / reply["archived"]).read_bytes() == before
        assert reply["archived"].endswith("-deleted.yml")
        assert reply["experiments"] == []
        assert editor_dash.server.experiment_entry("dash_exp") is None

    def test_a_delete_says_the_persisted_rows_stay(self, editor_dash):
        """The silent half of a destructive button, said out loud."""
        _status, reply = editor_dash.post("/api/experiment/delete", {"select": "dash_exp"})
        assert any("--orphaned-experiments" in w for w in reply["warnings"])

    def test_a_stale_digest_refuses_the_delete_too(self, editor_dash):
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        status, detail = editor_dash.post(
            "/api/experiment/delete", {"select": "dash_exp", "digest": "0" * 64}
        )
        assert status == 400
        assert "changed on disk" in detail
        assert path.exists()

    # -- reload ---------------------------------------------------------------

    def test_reload_picks_up_an_edit_made_outside_the_cockpit(self, editor_dash):
        """M11's named follow-up: an editor, a `git pull`, an explore Apply."""
        root = editor_dash.server.project_root
        (root / "experiments" / "dash_side.yml").write_text(
            _VALID_EXPERIMENT_YAML.format(name="dash_side", alpha="0.05"), encoding="utf-8"
        )
        assert editor_dash.server.experiment_entry("dash_side") is None

        status, reply = editor_dash.post("/api/reload", {})

        assert status == 200, reply
        assert {entry["name"] for entry in reply["experiments"]} == {"dash_exp", "dash_side"}
        assert editor_dash.server.experiment_entry("dash_side") is not None

    def test_a_broken_sibling_keeps_the_previous_selection_and_warns(self, editor_dash):
        """A reload that fails must not turn a landed write into a 500."""
        root = editor_dash.server.project_root
        path = root / "experiments" / "dash_exp.yml"
        (root / "experiments" / "dash_broken.yml").write_text("name: [", encoding="utf-8")
        edited = path.read_text(encoding="utf-8").replace("alpha: 0.05", "alpha: 0.01")

        status, reply = editor_dash.post(
            "/api/experiment/save", {"select": "dash_exp", "text": edited}
        )

        assert status == 200, reply
        assert path.read_text(encoding="utf-8") == edited  # the write landed
        assert any("still shows the previous selection" in w for w in reply["warnings"])
        assert editor_dash.server.experiment_entry("dash_exp") is not None

    def test_the_selection_route_answers_the_boot_payloads_own_shape(self, editor_dash):
        status, reply = editor_dash.get("/api/experiments")
        assert status == 200
        baked = baked_payload(editor_dash.get("/")[1])["experiments"]
        assert reply["experiments"] == baked

    def test_an_unknown_field_is_refused_on_every_editor_route(self, editor_dash):
        for route, body in (
            ("/api/experiment/save", {"select": "dash_exp", "text": "x", "metric": "revenue"}),
            ("/api/experiment/create", {"text": "x", "select": "dash_exp"}),
            ("/api/experiment/delete", {"select": "dash_exp", "text": "x"}),
            ("/api/reload", {"select": "dash_exp"}),
        ):
            status, detail = editor_dash.post(route, body)
            assert status == 400, (route, detail)
            assert "unknown field" in detail, (route, detail)

    def test_two_concurrent_saves_cannot_both_win(self, editor_dash):
        """The digest check and the write are one critical section (UI-1 review).

        Without the editor lock the whole two-level validation runs BETWEEN the
        check and the `os.replace` (25–180 ms measured), so two tabs holding the
        same digest both answered 200 and one edit existed nowhere — not on
        disk, and not in the archive. `abk explore`'s Apply is serialized for
        exactly this reason; this is the same discipline on the second seam.
        """
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        digest = source_of(editor_dash)["digest"]
        base = path.read_text(encoding="utf-8")
        replies: list = []

        def save(alpha: str) -> None:
            replies.append(
                editor_dash.post(
                    "/api/experiment/save",
                    {
                        "select": "dash_exp",
                        "text": base.replace("alpha: 0.05", f"alpha: {alpha}"),
                        "digest": digest,
                    },
                )
            )

        threads = [threading.Thread(target=save, args=(a,)) for a in ("0.01", "0.02")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

        statuses = sorted(status for status, _ in replies)
        assert statuses == [200, 400], replies
        loser = next(detail for status, detail in replies if status == 400)
        assert "changed on disk" in loser
        # …and the winner's text is what is on disk, with the ORIGINAL archived
        assert path.read_text(encoding="utf-8") != base
        archives = list((path.parent / ".history" / "dash_exp").glob("*.yml"))
        assert [a.read_text(encoding="utf-8") for a in archives] == [base]

    def test_a_file_too_large_to_show_cannot_be_saved_back(self, editor_dash, monkeypatch):
        """The client hides Save; the SERVER has to refuse it too.

        `digest` is optional, so a request that skips the client would replace
        the whole file with the prefix the page was shown.
        """
        monkeypatch.setattr(dashboard_server, "_MAX_SOURCE_BYTES", 200)
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        before = path.read_bytes()
        assert editor_dash.get("/api/experiment-source/dash_exp")[1]["editable"] is False

        status, detail = editor_dash.post(
            "/api/experiment/save", {"select": "dash_exp", "text": "name: dash_exp\n"}
        )

        assert status == 400
        assert "larger than the 200-byte editing cap" in detail
        assert path.read_bytes() == before

    def test_a_filesystem_failure_is_not_reported_as_a_failed_spawn(self, editor_dash, monkeypatch):
        """`do_POST`'s OSError branch says "could not start a subprocess" — true
        of the job routes, a lie on a route that never spawns."""

        def boom(*_a, **_k):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(dashboard_server.config_files, "atomic_write_bytes", boom)
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        status, detail = editor_dash.post(
            "/api/experiment/save",
            {"select": "dash_exp", "text": path.read_text(encoding="utf-8")},
        )
        assert status == 500
        assert "subprocess" not in detail
        assert "could not write dash_exp's config" in detail

    def test_an_oversized_document_is_refused_before_anything_is_touched(
        self, editor_dash, monkeypatch
    ):
        monkeypatch.setattr(dashboard_server, "_MAX_SOURCE_BYTES", 64)
        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        before = path.read_bytes()
        status, detail = editor_dash.post(
            "/api/experiment/save",
            {"select": "dash_exp", "text": path.read_text(encoding="utf-8")},
        )
        assert status == 400
        assert "larger than 64 bytes" in detail
        assert path.read_bytes() == before


def baked_report_payload(page: str) -> dict:
    """The payload out of a rendered report page (the `__ABK_PAYLOAD__` bake).

    Sliced off the assignment line and JSON-parsed, so an assertion can compare
    the SERVED payload against an independently built one instead of grepping
    the document for substrings.
    """
    marker = "window.__ABK_PAYLOAD__ = "
    start = page.index(marker) + len(marker)
    end = page.index(";</script>", start)
    return json.loads(page[start:end])


class TestReportPage:
    """DASH-5's Open button: ``GET /experiment/<name>`` renders the SAME report
    ``abk run --report`` writes, on demand, for one experiment."""

    def test_it_serves_the_report_document_the_report_bundle_drives(self, dash):
        status, page = dash.get("/experiment/dash_exp")
        assert status == 200
        assert page.startswith("<!doctype html>")
        assert "<title>abkit report — dash_exp</title>" in page
        # the committed report bundle + its window global, inlined verbatim
        assert "window.__ABK_REPORT__" in page
        assert "window.__ABK_PAYLOAD__ = " in page

    def test_the_served_payload_is_the_builders_own(self, dash):
        """Not "looks like a report" — the same dict, field for field.

        ``generated_at`` is the one cell that legitimately differs (the route
        stamps its own clock), so it is compared for shape and then dropped.
        """
        from abkit.reporting import build_report_payload

        expected = build_report_payload(
            dash.experiment, dash.tables, project=PROJECT, generated_at="pinned"
        )
        served = baked_report_payload(dash.get("/experiment/dash_exp")[1])

        assert served["generated_at"].endswith(" UTC")
        served.pop("generated_at")
        expected.pop("generated_at")
        # the route's no-manager disclosure rides in `warnings` (asserted below)
        assert served.pop("warnings")[:-1] == expected.pop("warnings")
        assert served == expected

    def test_the_verdict_on_the_page_is_the_rows_verdict(self, dash):
        """One readout, two surfaces — the row and the report cannot disagree."""
        row = dash.get("/api/stats/dash_exp")[1]
        payload = baked_report_payload(dash.get("/experiment/dash_exp")[1])

        assert [v["verdict"] for v in payload["verdicts"]] == [row["verdict"]]
        assert payload["srm"]["flag"] is row["srm_flag"]

    def test_an_unknown_experiment_is_a_404(self, dash):
        status, detail = dash.get("/experiment/nope")
        assert status == 404
        assert "unknown experiment: nope" in detail

    def test_a_trailing_slash_still_resolves(self, dash):
        """``/experiment/dash_exp/`` is the same resource, not a 404."""
        assert dash.get("/experiment/dash_exp/")[0] == 200

    def test_a_never_run_experiment_renders_the_reports_empty_state(self):
        """Not an error: the report's own "no persisted cutoffs" page."""
        served = Dashboard(seed=False)
        try:
            status, page = served.get("/experiment/dash_exp")
            payload = baked_report_payload(page)
        finally:
            served.stop()
        assert status == 200
        assert payload["period"]["end"] == 0
        assert payload["period"]["start"] > 0, "start/horizon are config facts"

    def test_the_metric_configs_seam_reaches_the_report(self):
        """The description D6 puts on a metric card comes from ``metrics=``."""
        from abkit.config.metric_config import MetricConfig

        metric = MetricConfig.model_validate(
            {
                "name": "revenue",
                "description": "revenue per user",
                "type": "sample",
                "columns": {"variant": "variant", "value": "revenue"},
                "sql": "SELECT 1",
            }
        )
        served = Dashboard(metrics={"revenue": metric})
        try:
            payload = baked_report_payload(served.get("/experiment/dash_exp")[1])
        finally:
            served.stop()
        assert payload["metrics"][0]["description"] == "revenue per user"

    def test_without_a_manager_the_no_copy_srm_counts_are_zero_and_it_says_so(self, dash):
        """A silent "0 / 0" beside a green chip would read as a broken cohort."""
        payload = baked_report_payload(dash.get("/experiment/dash_exp")[1])

        assert payload["srm"]["observed"] == {"control": 0, "treatment": 0}
        assert any("ZERO observed units" in w for w in payload["warnings"])
        assert any("without a database manager" in w for w in payload["warnings"])

    def test_a_cohort_source_that_cannot_be_read_costs_the_counts_not_the_page(self):
        """The manager IS passed here, and the fake cannot run assignment SQL —
        the same shape as a source that emptied or corrupted since the last run.
        ``abk explore`` turns that into a CLI error; a click must still get its
        report, with the failure named."""
        served = Dashboard(with_manager=True)
        try:
            status, page = served.get("/experiment/dash_exp")
            payload = baked_report_payload(page)
        finally:
            served.stop()
        assert status == 200
        assert payload["srm"]["observed"] == {"control": 0, "treatment": 0}
        assert any("cohort source could not be read" in w for w in payload["warnings"])
        assert any("ValueError" in w for w in payload["warnings"]), "names the failure"

    def test_a_read_that_fails_on_the_retry_too_is_a_500(self, dash, monkeypatch):
        """The fallback is for the cohort counts, not a blanket "never fail"."""
        monkeypatch.setattr(
            dash.tables,
            "load_results",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("warehouse gone")),
        )
        status, detail = dash.get("/experiment/dash_exp")
        assert status == 500
        assert "warehouse gone" in detail

    def test_the_render_holds_the_db_lock(self, dash):
        """One connection, one reader — the stats route's discipline.

        With the lock dropped the report's reads interleave with a row fill and
        ``peak`` reaches 2.
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
        threads = [
            threading.Thread(target=lambda: replies.append(dash.get("/experiment/dash_exp")[0])),
            threading.Thread(
                target=lambda: replies.append(dash.get("/api/stats/dash_exp", window="all")[0])
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert replies == [200, 200]
        assert state["peak"] == 1

    @pytest.mark.parametrize("path", ["/experiment/dash_exp", "/"])
    def test_html_replies_are_not_cacheable(self, dash, path):
        """The JSON routes' reason applies to both HTML routes too: a report
        reopened after a Run must not be the pre-run render, and a page reloaded
        after a restart must not be the previous selection's shell."""
        req = urllib.request.Request(dash.tokened(path))
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.headers["Content-Type"] == "text/html; charset=utf-8"
            assert resp.headers["Cache-Control"] == "no-store"


class TestTransport:
    def test_oversized_body_is_a_413(self, dash):
        status, detail = dash.post("/api/run", raw=b"x" * 5_000_001)
        assert status == 413
        assert "too large" in detail
        assert dash.thread.is_alive()

    @pytest.mark.parametrize("length", ["abc", "-5"])
    def test_a_malformed_content_length_is_a_400_not_a_413(self, dash, length):
        """A negative length is a broken header, not an oversized body — calling
        it 413 would send the client looking for a body it shrank."""
        parsed = urllib.parse.urlparse(dash.tokened("/api/run"))
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
        try:
            conn.putrequest("POST", f"{parsed.path}?{parsed.query}")
            conn.putheader("Content-Length", length)
            conn.endheaders()
            response = conn.getresponse()
            assert response.status == 400
            assert b"Content-Length" in response.read()
        finally:
            conn.close()
        assert dash.thread.is_alive()

    def test_a_bodyless_post_still_reaches_routing(self, dash):
        status, detail = dash.post("/api/nope", raw=b"")
        assert status == 404
        assert "/api/nope" in detail

    @pytest.mark.parametrize(
        ("body", "says"),
        [
            (b"", "a JSON body is required"),
            (b"   ", "a JSON body is required"),
            (b"not json", "Expecting value"),
            (b"[1, 2]", "must be a JSON object, got list"),
            (b'"select"', "must be a JSON object, got str"),
            (b"\xff\xfe", "codec can't decode"),
        ],
    )
    def test_a_job_route_needs_a_json_OBJECT_body(self, dash, body, says):
        """Each malformed body gets the message that names ITS fault.

        Asserting only the 400 would pass for a server that read a missing body
        as ``{}`` and then complained about a ``select`` the client did send.
        """
        status, detail = dash.post("/api/run", raw=body)
        assert status == 400, detail
        assert says in detail

    def test_a_deeply_nested_body_is_the_400_it_is_not_a_500(self, dash):
        """``RecursionError`` is a ``RuntimeError``, so without its own branch the
        decoder blows past ``except ValueError`` and a malformed BODY is reported
        as a server defect naming an internal exception class."""
        body = b'{"select": ' + b"[" * 20_000 + b"]" * 20_000 + b"}"
        status, detail = dash.post("/api/run", raw=body)
        assert status == 400, detail
        assert "nested too deeply" in detail
        assert "RecursionError" not in detail
        assert dash.get("/")[0] == 200  # …and the server is still serving

    @pytest.mark.parametrize(
        ("raised", "get_code", "post_code"),
        [
            (UnknownWindowPreset("Unknown window preset '9d'"), 400, 400),
            (ValueError("bad input"), 500, 400),
            (RuntimeError("boom"), 500, 500),
        ],
    )
    def test_a_raising_route_replies_instead_of_killing_the_thread(
        self, dash, monkeypatch, raised, get_code, post_code
    ):
        """The insurance DASH-4's routes inherit — with the GET/POST asymmetry.

        A GET's arguments are looked up or regex-checked, so only the NAMED
        window-preset error is a 400 there and a stray ``ValueError`` is the
        defect it actually is (DASH-2 named the exception for exactly this).
        A POST body is arbitrary JSON — and ``json.JSONDecodeError`` is a
        ``ValueError`` — so there it stays a 400.
        """

        def raiser(self, *_args, **_kwargs):
            raise raised

        monkeypatch.setattr(dashboard_server._Handler, "_route_get", raiser)
        monkeypatch.setattr(dashboard_server._Handler, "_route_post", raiser)
        assert dash.get("/")[0] == get_code
        assert dash.post("/api/run", {})[0] == post_code
        monkeypatch.undo()
        assert dash.get("/")[0] == 200

    def test_json_replies_are_never_cached(self, dash):
        """Every dynamic answer is a GET at a URL that repeats between polls; a
        heuristically cached row would show a pre-run verdict."""
        with urllib.request.urlopen(
            dash.tokened("/api/stats/dash_exp", window="all"), timeout=10
        ) as resp:
            assert resp.headers["Cache-Control"] == "no-store"
            assert resp.headers["Content-Type"] == "application/json"

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

    def test_a_broken_echo_cannot_take_the_cockpit_down(self, dash):
        """``handle_error`` runs inside socketserver's own ``except``, so raising
        here escapes ``_handle_request_noblock`` and ends ``serve_forever`` —
        which is what `abk dashboard | head` would do through a closed stdout."""

        def broken(_line: str) -> None:
            raise BrokenPipeError("stdout is gone")

        dash.server.echo = broken
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            dash.server.handle_error(None, None)  # must not raise
        assert dash.get("/")[0] == 200

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
            (404, "/api/experiment-source/nope", {}),
        ]:
            assert dash.get(path, **query)[0] == status, path
        for status, path, payload in [
            (400, "/api/run", {}),  # 'select' is required
            (400, "/api/run", {"select": "nope"}),
            (400, "/api/explore", {"select": "nope"}),
            (404, "/api/job/nope/stop", {}),
            (404, "/api/nope", {}),
        ]:
            assert dash.post(path, payload)[0] == status, path
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

    def test_the_editor_routes_take_no_lock_and_compute_nothing(self, editor_dash, monkeypatch):
        """UI-1's restatement, executable: a config write is not a pipeline action.

        The invariant M11 wrote as "computes a statistic, turns a knob, writes a
        config or takes the pipeline lock" folded in a clause no gate ever
        checked — and it is the clause this WP contradicts. What the gates
        actually enforce is the other two, so they run over the routes that
        write: no lock is acquired, and no verdict is computed (``evaluate`` is
        the readout entry every row and report goes through; an editor route
        that reached for it would be doing the pipeline's job).
        """
        taken: list[tuple] = []
        monkeypatch.setattr(
            InternalTablesManager, "acquire_lock", lambda self, *a, **k: taken.append(a) or True
        )
        monkeypatch.setattr(
            InternalTablesManager, "release_lock", lambda self, *a, **k: taken.append(a)
        )
        evaluated: list[tuple] = []
        original = overview_module.evaluate
        monkeypatch.setattr(
            overview_module, "evaluate", lambda *a, **k: evaluated.append(a) or original(*a, **k)
        )

        path = editor_dash.server.project_root / "experiments" / "dash_exp.yml"
        text = path.read_text(encoding="utf-8")
        assert (
            editor_dash.post("/api/experiment/save", {"select": "dash_exp", "text": text})[0] == 200
        )
        assert (
            editor_dash.post(
                "/api/experiment/create",
                {"text": _VALID_EXPERIMENT_YAML.format(name="dash_lockspy", alpha="0.05")},
            )[0]
            == 200
        )
        assert editor_dash.post("/api/reload", {})[0] == 200
        assert editor_dash.post("/api/experiment/delete", {"select": "dash_lockspy"})[0] == 200

        assert taken == []
        assert evaluated == []
        # …and the row route still computes its verdict, so the spy is live.
        assert editor_dash.get("/api/stats/dash_exp", window="all")[0] == 200
        assert evaluated

    def test_no_job_route_acquires_the_lock_either(self, jobs_dash, monkeypatch):
        """§0.5(d) through the job routes: only the spawned child locks anything.

        The AST gate above cannot see a lock taken through a helper, and these
        are the routes that mutate — so the spy runs over every one of them.
        """
        taken: list[tuple] = []
        monkeypatch.setattr(
            InternalTablesManager,
            "acquire_lock",
            lambda self, *a, **k: taken.append(a) or True,
        )
        monkeypatch.setattr(
            InternalTablesManager, "release_lock", lambda self, *a, **k: taken.append(a)
        )
        for route in ("/api/run", "/api/unlock", "/api/clean"):
            status, reply = jobs_dash.post(route, {"select": "dash_exp"})
            assert status == 200, reply
            poll_id_until_done(jobs_dash, reply["job_id"])
        monkeypatch.setenv("ABK_STUB", "explore")
        status, reply = jobs_dash.post("/api/explore", {"select": "dash_exp"})
        assert status == 200, reply
        assert jobs_dash.post(f"/api/job/{reply['job_id']}/stop", {})[0] == 200
        assert jobs_dash.get("/api/experiment-source/dash_exp")[0] == 200
        assert taken == []


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

    def test_a_failed_construction_does_not_leak_the_bound_socket(self, monkeypatch):
        """The port is bound before the selection is indexed or the page baked,
        so every post-bind failure has to close it — nobody else has a handle."""
        closed: list[int] = []
        original = dashboard_server._DashboardServer.server_close
        monkeypatch.setattr(
            dashboard_server._DashboardServer,
            "server_close",
            lambda self: closed.append(id(self)) or original(self),
        )
        experiment = make_experiment()
        with pytest.raises(ValueError):
            build_dashboard_server(
                project=PROJECT,
                project_root=ROOT,
                experiments=[(EXP_PATH, experiment), (EXP_PATH_TWO, experiment)],
                tables=InternalTablesManager(FakeDatabaseManager()),
            )
        assert len(closed) == 1
        # …and a bad boot window never binds a socket at all
        closed.clear()
        with pytest.raises(UnknownWindowPreset):
            build_dashboard_server(
                project=PROJECT,
                project_root=ROOT,
                experiments=[],
                tables=InternalTablesManager(FakeDatabaseManager()),
                initial_window="7days",
            )
        assert closed == []

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
    def test_the_committed_bundle_is_inlined_verbatim(self):
        """The real committed artifact, not a stub, is what the page ships.

        DASH-6 removed the pending-note degradation, so this is now a statement
        about the file on disk: the whole bundle text appears in the page, and
        the page carries the window global its bootstrap calls.
        """
        committed = (Path(html.__file__).parent / "assets" / "dashboard.js").read_text(
            encoding="utf-8"
        )
        page = render_dashboard_html({"project": "p", "experiments": []})
        assert committed in page
        assert "window.__ABK_DASHBOARD__" in page
        assert "npm run build" not in page

    @pytest.mark.parametrize("reader", ["_explore_js", "_dashboard_js"])
    def test_a_missing_bundle_raises_rather_than_degrading(self, reader, monkeypatch, tmp_path):
        """Both bundles are committed AND wheel-namelist-asserted (DASH-6 added
        ``dashboard.js`` to that tuple), so an absent one is a packaging bug that
        must be loud — a page telling a `pip install` user to run `npm run build`
        blames them for something they cannot fix.

        Asserting "raises" needs the file to actually be gone, so the resource
        root is pointed at an empty directory. Testing it via a hijacked reader
        would prove nothing: the law IS that there is no fallback path. The
        exception TYPE is deliberately loose — a filesystem read raises
        ``FileNotFoundError``, a zip-imported wheel raises ``KeyError``, and the
        contract is "loud", not a particular class.
        """
        monkeypatch.setattr(html, "files", lambda _package: tmp_path)
        with pytest.raises((OSError, KeyError)):
            getattr(html, reader)()

    def test_both_readers_return_their_committed_file(self):
        """No degradation constant survives for either page (the DASH-6 decision)."""
        assets = Path(html.__file__).parent / "assets"
        assert html._explore_js() == (assets / "explore.js").read_text(encoding="utf-8")
        assert html._dashboard_js() == (assets / "dashboard.js").read_text(encoding="utf-8")
        assert not hasattr(html, "_PENDING_DASHBOARD_JS")
