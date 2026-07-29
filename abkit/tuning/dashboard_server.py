"""The dashboard localhost server (``docs/specs/m11-implementation-plan.md`` DASH-3).

The project-level cockpit's transport: a pure-stdlib ``ThreadingHTTPServer``
bound to ``127.0.0.1`` with a one-shot token, serving the metadata-only boot
page and, per row, one lazily-fetched statistics reply built by
``tuning/overview.py`` (DASH-2). Every job route — Run / Explore / Unlock /
Clean — lands in DASH-4 on top of the :class:`~abkit.tuning.jobs.JobManager`
this server already holds (DASH-1).

**The dashboard is a launcher, never a worker** (§0.5(d)). Nothing here
acquires the pipeline lock, runs a pipeline step, or computes a statistic: the
verdicts come from ``readout.evaluate()`` through ``overview.py``, and every
mutation is a real ``abk`` subprocess spawned by DASH-4's routes, exactly as if
typed at a terminal. The single ``InternalTablesManager`` is serialized by
``db_lock`` — a DB-API connection is not thread-safe.

Two deltas from ``abkit/tuning/server.py`` — that is, from **the dtk-tune
pattern** ``abkit/tuning/server.py`` mirrors, **not** from dtk-ui, which
already behaves the way this module does (``detektkit/detectkit/ui/server.py``
gates GET too and never self-shuts-down; §0.5(b) records that REPORT's "2
deltas from dtk" phrasing was measured against the wrong donor file):

1. **The token gates EVERY request, GET included.** ``tuning/server.py`` serves
   its one baked page to any unauthenticated GET and gates only the POSTs —
   defensible for a single-experiment page whose whole content is already
   public to whoever can reach the port, but wrong here: ``GET
   /api/stats/<experiment>`` reads the warehouse, and ``GET /`` enumerates
   every experiment in the project. Authorization happens BEFORE routing, so a
   403 also cannot be used to probe which paths exist.
2. **The server never shuts itself down.** ``tuning/server.py``'s ``/apply`` is
   terminal and spawns ``threading.Thread(target=srv.shutdown, …)``; the
   dashboard has no terminal action at all — it serves until Ctrl-C. No code
   path in this module may call ``server.shutdown()``, which
   ``tests/tuning/test_dashboard_server.py`` pins with an AST gate over this
   file (the copy-paste that would reintroduce it is exactly what §0.5(b)
   warns about). ``server.jobs.shutdown()`` in :func:`serve_dashboard`'s
   ``finally`` is the *job registry*'s teardown, not the HTTP server's.

Routes in this WP are read-only: ``GET /`` (the baked page), ``GET
/api/stats/<experiment>``, ``GET /api/jobs``, ``GET /api/job/<id>?offset=``.
``do_POST`` exists with the same authorization discipline and answers 404 —
DASH-4 fills in its routing table, and its transport (bounded body read,
413/400) is already here. ``GET /experiment/<name>`` (the full report render
behind the Open button) belongs to DASH-5, which owns the button.

There is deliberately **no caching layer**: every ``/api/stats`` call re-reads
the DB, matching the donor. DASH-5's fixed-concurrency-3 client pool is what
bounds the load, not a server-side cache — and since ``db_lock`` serializes
those reads, a project of very long experiments loads its list in about the
sum of its reads, not the slowest one. Acceptable per the donor's own
one-connection-per-manager precedent (§DASH-3 "Risks / hotspots"): a comment,
not a fix, in this WP.

Two snapshots taken at boot and never refreshed, both matching the donor: the
served **selection** (its configs are read once, so an experiment added or a
YAML edited while the cockpit runs needs a restart to be reflected — including
after DASH-4's read-only "open in your editor" affordance) and the baked page.
A "reload configs" affordance is a named follow-up, not a phase-1 gap.
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, unquote, urlparse

from abkit import __version__
from abkit.tuning.html import render_dashboard_html
from abkit.tuning.jobs import JobManager
from abkit.tuning.overview import (
    ALL_WINDOW_PRESETS,
    WINDOW_PRESETS,
    UnknownWindowPreset,
    build_experiment_row_safe,
    build_overview_boot_entries,
    validate_window_preset,
)
from abkit.tuning.payload import _ms
from abkit.tuning.server import _quiet_stderr
from abkit.utils.datetime_utils import now_utc_naive

if TYPE_CHECKING:
    from abkit.config.experiment_config import ExperimentConfig
    from abkit.config.project_config import ProjectConfig
    from abkit.database.internal_tables import InternalTablesManager

#: The window preset a dashboard boots with (``abk dashboard --window``'s
#: default, DASH-6 — exported so the CLI never spells a second copy).
DEFAULT_WINDOW_PRESET = "30d"

_MAX_BODY = 5_000_000  # generous cap on a posted job request (DASH-4)
_MAX_DRAIN = 32_000_000  # how much of an oversized body to drain before the 413

_STATS_PREFIX = "/api/stats/"
_JOB_PREFIX = "/api/job/"

#: A poll offset: a line index, so 15 digits is already absurd — and bounding
#: the length keeps ``int()`` from raising on a 5000-digit query value (CPython
#: refuses str→int conversions past 4300 digits), which would surface as a 500.
_OFFSET_RE = re.compile(r"^\d{1,15}$")


def window_preset_order() -> list[str]:
    """The presets shortest-first, with ``"all"`` last — the selector's order.

    Derived from :data:`~abkit.tuning.overview.WINDOW_PRESETS`' day counts
    rather than written out, so adding a preset there reaches the page without
    a second edit here.
    """
    fixed = sorted(WINDOW_PRESETS, key=lambda preset: WINDOW_PRESETS[preset])
    return [*fixed, *sorted(ALL_WINDOW_PRESETS - set(fixed))]


class _DashboardServer(ThreadingHTTPServer):
    """Localhost server holding the per-serve state the handler reads."""

    #: Declared, deliberately NOT defaulted: every route needs both, and
    #: ``build_dashboard_server`` sets them before the socket is ever served.
    #: A stand-in ``None`` would turn "constructed wrongly" into a
    #: ``NoneType`` error deep inside a row build; an unset attribute says so
    #: at the first access instead. (The explore server's ``None``-typed
    #: session/engine exist for its static-preview degradations, which the
    #: dashboard has no equivalent of.)
    project: ProjectConfig
    tables: InternalTablesManager

    # Don't block interpreter exit on in-flight request threads.
    daemon_threads = True
    # The dashboard's boot render fires one /api/stats request per row, so a
    # project-sized burst of simultaneous connections is the NORMAL case, not
    # an edge — socketserver's default backlog of 5 is not (the explore server
    # raised it for a knob-drag burst; m10 WP4).
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(address, handler)
        self.token: str = ""
        self.html: str = ""
        self.project_root: Path = Path(".")
        self.experiments: list[tuple[Path, ExperimentConfig]] = []
        self._by_name: dict[str, tuple[Path, ExperimentConfig]] = {}
        self.initial_window: str = DEFAULT_WINDOW_PRESET
        self.profile: str | None = None
        self.echo: Callable[[str], None] = print
        # Every spawned `abk` subprocess (DASH-4's routes; DASH-1's registry).
        self.jobs: JobManager = JobManager()
        # The one InternalTablesManager holds a single connection — serialize
        # every route that touches it. Never held while a subprocess runs:
        # spawning is fire-and-forget and polling its output touches no DB.
        self.db_lock = threading.Lock()

    def set_experiments(self, experiments: Sequence[tuple[Path, ExperimentConfig]]) -> None:
        """Install the served selection, rejecting a duplicated name.

        ``select_experiments`` already keys its result by name, so a duplicate
        can only arrive from a hand-built caller — and it would be invisible:
        the boot list would show two identical rows while every
        ``/api/stats/<name>`` answered from one of them forever.

        Boot-only: called by :func:`build_dashboard_server` before the socket is
        ever served, which is why the list and its index are written unlocked.
        A mid-serve reload (the named follow-up in the module docstring) would
        have to pair them under a lock, exactly like the explore session's
        cache.
        """
        by_name: dict[str, tuple[Path, ExperimentConfig]] = {}
        for path, experiment in experiments:
            if experiment.name in by_name:
                first = by_name[experiment.name][0]
                raise ValueError(
                    f"duplicate experiment name {experiment.name!r} in the dashboard "
                    f"selection ({first} and {path})"
                )
            by_name[experiment.name] = (path, experiment)
        self.experiments = list(experiments)
        self._by_name = by_name

    def experiment_entry(self, name: str) -> tuple[Path, ExperimentConfig] | None:
        """The ``(path, config)`` for *name*, or ``None`` if it is not served."""
        return self._by_name.get(name)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Keep the terminal clean: a client dropping its socket is routine.

        The stdlib default dumps a full traceback per handler-thread exception —
        on Ctrl-C, or a browser abandoning the boot burst, that is a wall of
        BrokenPipe noise. Client disconnects are swallowed; anything else is
        echoed as one compact line, so a real bug is still visible.

        The echo itself is suppressed, and that is not defensiveness for its own
        sake: ``socketserver`` calls this from inside its own ``except``, so an
        exception raised HERE propagates out of ``_handle_request_noblock`` and
        kills ``serve_forever``. ``abk dashboard | head`` closing stdout would
        otherwise take the whole cockpit down through its logger (the donor
        leaves this unguarded).
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        with contextlib.suppress(Exception):
            self.echo(f"  [dashboard] request error: {type(exc).__name__}: {exc}")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        return

    def _srv(self) -> _DashboardServer:
        return cast(_DashboardServer, self.server)

    def _authorized(self, srv: _DashboardServer) -> bool:
        """The token check EVERY request passes through — delta 1 (docstring).

        Compared as BYTES: ``compare_digest`` refuses ``str`` arguments
        carrying non-ASCII characters, so ``?token=α`` would raise here —
        before ``do_GET``'s handler wrapping — and answer nothing at all.
        """
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        return secrets.compare_digest(
            query.get("token", [""])[0].encode("utf-8"), srv.token.encode("utf-8")
        )

    def do_GET(self) -> None:
        """Every GET: authorize, route, and answer even on a defect.

        Only ``UnknownWindowPreset`` maps to 400 — every other query argument a
        GET route takes is already validated by the route itself (the experiment
        name by a lookup, ``offset`` by a regex), so a stray ``ValueError`` here
        means a server defect and gets a 500 rather than being dressed up as a
        bad request. That is DASH-2's stated reason for naming the exception:
        "so DASH-3 can answer 400 for it and 500 for anything else instead of
        guessing which ``ValueError`` it caught."
        """
        srv = self._srv()
        if not self._authorized(srv):
            self._reply_error(403, "bad token")
            return
        try:
            self._route_get(srv)
        except UnknownWindowPreset as exc:
            self._reply_error(400, f"{exc}")
        except Exception as exc:  # noqa: BLE001 — never kill the serving thread
            self._reply_error(500, f"{type(exc).__name__}: {exc}")

    def do_POST(self) -> None:
        """Authorization + transport for DASH-4's job routes; no routes yet.

        The token check comes first here too, so DASH-4 inherits it by
        extending ``_route_post`` rather than by remembering to gate.

        Here ``ValueError`` DOES map to 400, unlike ``do_GET``: a POST body is
        arbitrary client-supplied JSON, ``json.JSONDecodeError`` *is* a
        ``ValueError``, and "malformed request" is what the explore server
        answers 400 for. The asymmetry is the difference between an argument the
        route parses and one it looks up.
        """
        srv = self._srv()
        if not self._authorized(srv):
            self._reply_error(403, "bad token")
            return
        body = self._read_body()
        if body is None:
            return  # already replied 400/413
        try:
            self._route_post(srv, body)
        except ValueError as exc:
            self._reply_error(400, f"{exc}")
        except Exception as exc:  # noqa: BLE001 — never kill the serving thread
            self._reply_error(500, f"{type(exc).__name__}: {exc}")

    # -- transport helpers (the explore server's, minus the payload parsing) ---

    def _read_body(self) -> bytes | None:
        """The posted body, or ``None`` after replying 400/413.

        An oversized body is drained (bounded) before the 413 so the client
        reads the status instead of a broken pipe mid-upload — the explore
        server's discipline. An empty body is legal here: DASH-4's routes carry
        their arguments in JSON, but a bodyless POST must still reach routing
        and get the honest 404/400 for its path.
        """
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._reply_error(400, "bad Content-Length header")
            return None
        if length < 0:
            # Not "too large": a negative length is a malformed header, and
            # calling it 413 would send the client looking for a body it shrank.
            self._reply_error(400, "bad Content-Length header")
            return None
        if length > _MAX_BODY:
            if length <= _MAX_DRAIN:
                with contextlib.suppress(OSError):
                    self.rfile.read(length)
            self._reply_error(413, "request body too large")
            return None
        return self.rfile.read(length) if length else b""

    def _reply_html(self, html: str) -> None:
        body = html.encode("utf-8")
        # A browser that navigated away mid-load closes the socket: suppress
        # the BrokenPipe rather than logging a traceback per dropped page load.
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _reply_json(self, payload: dict[str, Any], code: int = 200) -> None:
        resp = json.dumps(payload, default=_json_default).encode("utf-8")
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            # Unlike the explore cockpit, whose answers are all POST replies,
            # every dynamic answer here is a GET at a URL that never changes
            # between polls — and a response with no validators is heuristically
            # cacheable. A cached row would show a verdict from before the run
            # the operator just launched.
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    def _reply_error(self, code: int, detail: str) -> None:
        """Error detail in the UTF-8 body, never the latin-1 status line.

        ``send_error`` writes the message into the status line, which is
        latin-1 only — a config/stats exception carrying an ``α`` or a unicode
        dash would then crash the response with ``UnicodeEncodeError`` instead
        of returning a clean error (the explore server's WP6 lesson).
        """
        body = detail.encode("utf-8")
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    # -- routing ---------------------------------------------------------------

    def _route_get(self, srv: _DashboardServer) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        # keep_blank_values: `?window=` / `?offset=` are client BUGS, and
        # parse_qs' default would drop them, making a blank indistinguishable
        # from an absent parameter — i.e. silently falling back to the boot
        # window or to offset 0. A blank value is a value, and gets a 400.
        query = parse_qs(parsed.query, keep_blank_values=True)
        if path == "/":
            self._reply_html(srv.html)
            return
        if path.startswith(_STATS_PREFIX):
            window = query.get("window", [srv.initial_window])[0]
            self._handle_stats(srv, unquote(path[len(_STATS_PREFIX) :]), window)
            return
        if path == "/api/jobs":
            # Two registry reads, so the flag can disagree with the list it
            # ships with by one job that finished in between. Deliberate: the
            # alternative is re-deriving `pipeline_active`'s rule (kind ∈
            # PIPELINE_KINDS ∧ running) over the snapshots, which is the exact
            # divergence DASH-1's validated vocabulary exists to prevent. The
            # flag only drives an advisory chip that is re-polled — the
            # authoritative gate is `spawn_pipeline`'s atomic check, so a client
            # acting on a stale chip still gets DASH-4's 400.
            self._reply_json(
                {"jobs": srv.jobs.list_snapshots(), "pipeline_active": srv.jobs.pipeline_active()}
            )
            return
        if path.startswith(_JOB_PREFIX):
            job_id = unquote(path[len(_JOB_PREFIX) :]).strip("/")
            self._handle_job(srv, job_id, query.get("offset", ["0"])[0])
            return
        self._reply_error(404, f"not found: {path}")

    def _route_post(self, srv: _DashboardServer, body: bytes) -> None:
        """DASH-4's job routes hang here; until then every POST is a 404."""
        self._reply_error(404, f"not found: {urlparse(self.path).path}")

    # -- GET handlers ----------------------------------------------------------

    def _handle_stats(self, srv: _DashboardServer, name: str, window: str) -> None:
        """One experiment's row — the page fills the list in incrementally.

        Small per-experiment requests keep every exchange short (no browser
        abort on a project whose combined statistics take minutes) and let a
        failing experiment degrade to its own ``error`` field instead of
        sinking the list. The window preset is validated BEFORE ``db_lock`` is
        taken: a request-level mistake must not queue behind a slow read.
        """
        validate_window_preset(window)
        entry = srv.experiment_entry(name)
        if entry is None:
            self._reply_error(404, f"unknown experiment: {name}")
            return
        experiment_path, experiment = entry
        with srv.db_lock:
            row = build_experiment_row_safe(
                project_root=srv.project_root,
                experiment_path=experiment_path,
                experiment=experiment,
                project=srv.project,
                tables=srv.tables,
                window_preset=window,
            )
        self._reply_json(row)

    def _handle_job(self, srv: _DashboardServer, job_id: str, raw_offset: str) -> None:
        """One job's poll reply from *raw_offset* (an ABSOLUTE line index).

        A non-numeric offset is a 400 rather than the donor's silent fallback
        to 0: silently rewinding a log drawer to the top of the buffer is the
        kind of "works, wrongly" the repo prefers loud.
        """
        if not _OFFSET_RE.match(raw_offset):
            self._reply_error(
                400, f"offset must be a non-negative integer of ≤15 digits, got {raw_offset!r}"
            )
            return
        job = srv.jobs.get(job_id)
        if job is None:
            self._reply_error(404, f"unknown job: {job_id}")
            return
        self._reply_json(srv.jobs.snapshot(job, int(raw_offset)))


def _json_default(o: Any) -> Any:
    """JSON fallback for a stats row carrying numpy scalars from the warehouse.

    ``overview.py`` scrubs its numbers through ``float()``, but a driver cell
    can still arrive as a numpy type inside a passthrough list (warnings,
    rationale). Imported lazily so the hot plain-dict replies never touch numpy.
    """
    import numpy as np

    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _boot_payload(
    *,
    project: ProjectConfig,
    project_root: Path,
    experiments: Sequence[tuple[Path, ExperimentConfig]],
    initial_window: str,
    profile: str | None,
) -> dict[str, Any]:
    """The ``GET /`` shell payload: metadata only — no statistics, no token.

    Every verdict, effect and sparkline arrives later over ``/api/stats``, so
    the page renders instantly on a project with a hundred experiments and a
    cold warehouse. The token is not baked (the client reads it from
    ``location.search``): the same served HTML then works whatever port was
    bound, and the page is not a credential at rest.
    """
    return {
        "project": project.name,
        "profile": profile,
        "version": __version__,
        "initial_window": initial_window,
        "window_presets": window_preset_order(),
        "generated_at": _ms(now_utc_naive()),
        "experiments": build_overview_boot_entries(project_root, experiments, project=project),
    }


def build_dashboard_server(
    *,
    project: ProjectConfig,
    project_root: Path,
    experiments: Sequence[tuple[Path, ExperimentConfig]],
    tables: InternalTablesManager,
    initial_window: str = DEFAULT_WINDOW_PRESET,
    profile: str | None = None,
    jobs: JobManager | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[_DashboardServer, str]:
    """Construct (without running) the dashboard server; return ``(server, url)``.

    The bound port is known only after construction, so the page is rendered
    ONCE post-bind, exactly like ``build_explore_server`` — but no URLs are
    baked into the payload (see :func:`_boot_payload`).

    Raises :class:`~abkit.tuning.overview.UnknownWindowPreset` for an unknown
    *initial_window* — at boot, where the operator typed it, never as N broken
    rows later — and ``ValueError`` for a selection with a duplicated
    experiment name.
    """
    validate_window_preset(initial_window)  # before a socket exists to leak
    server = _DashboardServer(("127.0.0.1", 0), _Handler)
    try:
        token = secrets.token_urlsafe(16)
        port = int(server.server_address[1])
        server.token = token
        server.project = project
        server.project_root = project_root
        server.tables = tables
        server.initial_window = initial_window
        server.profile = profile
        server.echo = echo
        if jobs is not None:
            server.jobs = jobs
        server.set_experiments(experiments)
        server.html = render_dashboard_html(
            _boot_payload(
                project=project,
                project_root=project_root,
                experiments=server.experiments,
                initial_window=initial_window,
                profile=profile,
            )
        )
    except BaseException:
        # The port is already bound by now, so every post-bind failure — a
        # duplicated name, a config whose window will not resolve, a bundle read
        # — would otherwise leave a listening socket nobody holds a handle to.
        server.server_close()
        raise
    return server, f"http://127.0.0.1:{port}/?token={token}"


def serve_dashboard(
    *,
    project: ProjectConfig,
    project_root: Path,
    experiments: Sequence[tuple[Path, ExperimentConfig]],
    tables: InternalTablesManager,
    initial_window: str = DEFAULT_WINDOW_PRESET,
    profile: str | None = None,
    jobs: JobManager | None = None,
    open_browser: bool = True,
    echo: Callable[[str], None] = print,
    on_ready: Callable[[str], None] | None = None,
) -> None:
    """Serve the dashboard until Ctrl-C; every spawned job is stopped on exit.

    Unlike ``serve_explore`` there is no terminal action and no return value:
    the dashboard has nothing to apply, so it never shuts itself down (delta 2
    in the module docstring). ``KeyboardInterrupt`` is the only exit, and the
    ``finally`` tears the job registry down — a spawned ``abk run`` must not
    outlive the cockpit that started it, untracked and still holding the
    pipeline lock.

    ``daemon_threads`` means ``server_close()`` does not join handler threads,
    so a Ctrl-C landing mid-read returns while that read is still running: the
    caller closing its DB manager can then make the thread fail, which
    :meth:`_DashboardServer.handle_error` reports as one line. Waiting the read
    out instead would trade a rare cosmetic line for a Ctrl-C that hangs for as
    long as the slowest query — the wrong trade for a cockpit.
    """
    server, url = build_dashboard_server(
        project=project,
        project_root=project_root,
        experiments=experiments,
        tables=tables,
        initial_window=initial_window,
        profile=profile,
        jobs=jobs,
        echo=echo,
    )
    if on_ready is not None:
        on_ready(url)
    echo(f"  Dashboard: {url}")
    echo("  Open the URL above if no browser opens. Ctrl-C to stop.")
    if open_browser:
        try:
            with _quiet_stderr():
                webbrowser.open(url)
        except Exception:  # noqa: BLE001 — the URL is printed; a launcher is a bonus
            pass
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        echo("")
        echo("  Stopping — terminating any jobs the dashboard spawned…")
    finally:
        server.jobs.shutdown()
        server.server_close()
        echo("  Stopped.")
