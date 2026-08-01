"""The dashboard localhost server (``docs/specs/m11-implementation-plan.md``
DASH-3 + DASH-4).

The project-level cockpit's transport: a pure-stdlib ``ThreadingHTTPServer``
bound to ``127.0.0.1`` with a one-shot token, serving the metadata-only boot
page and, per row, one lazily-fetched statistics reply built by
``tuning/overview.py`` (DASH-2). Every job route — Run / Explore / Unlock /
Clean (DASH-4) — sits on top of the :class:`~abkit.tuning.jobs.JobManager` this
server holds (DASH-1).

**The dashboard is a launcher, never a worker** (§0.5(d)). Nothing here
acquires the pipeline lock, runs a pipeline step, or computes a statistic: every
verdict comes from ``readout.evaluate()`` — through ``overview.py`` for a row,
through ``reporting.build_report_payload`` for the Open button's report page —
and every mutation is a real ``abk`` subprocess spawned by DASH-4's routes,
exactly as if typed at a terminal. The two reads that do touch the warehouse are
the row fill and (in the no-copy default) that report page's cohort snapshot;
both go through the single ``InternalTablesManager``/manager pair serialized by
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

Read routes (DASH-3): ``GET /`` (the baked page), ``GET
/api/stats/<experiment>``, ``GET /api/jobs``, ``GET /api/job/<id>?offset=``,
plus ``GET /api/experiment-source/<name>`` (DASH-4 — the experiment's raw YAML
for the read-only "open in your editor" affordance) and ``GET
/experiment/<name>`` (DASH-5 — the full report page behind the Open button, the
one route that answers HTML rather than JSON). Job routes (DASH-4): ``POST
/api/run`` (optionally one ``metric``), ``POST /api/unlock``, ``POST
/api/clean``, ``POST /api/explore`` and ``POST /api/job/<id>/stop``. Every one
of them spawns — or stops — a real ``abk`` subprocess; none of them computes,
reads the warehouse, or writes a file. Run / Unlock / Clean answer as soon as the
child exists (a selector resolve, then ``Popen``), but **``/api/explore`` is a
long request by design**: it holds the response until the spawned cockpit prints
its URL, up to ``explore_url_timeout`` (90 s), so a client must give that one
route a long fetch timeout and a spinner.

Two things a job route deliberately does NOT do. It never mutates a config:
"edit" is a read of the YAML text (§0.5(g) — validate-before-write plus the
``.history`` archive, the donor's ``metric_files.py`` shape, is phase 2), so
there is no save endpoint in this milestone. And it never takes the pipeline
lock, not even briefly: the one process that does is the spawned child, in its
own OS process, exactly as if the command had been typed.

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
after the read-only "open in your editor" affordance, and including the
``metric`` gate on ``POST /api/run``) and the baked page. The YAML *text* the
source route returns is read live off disk, so it can legitimately disagree with
the parsed config every other route uses. A "reload configs" affordance is a
named follow-up, not a phase-1 gap.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sys
import threading
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, unquote, urlparse

from abkit import __version__
from abkit.tuning.html import render_dashboard_html
from abkit.tuning.jobs import JobManager, JobManagerClosed
from abkit.tuning.overview import (
    ALL_WINDOW_PRESETS,
    WINDOW_PRESETS,
    UnknownWindowPreset,
    build_experiment_row_safe,
    build_overview_boot_entries,
    experiments_base_dir,
    resolve_experiment_location,
    validate_window_preset,
)
from abkit.tuning.payload import _ms
from abkit.tuning.server import _quiet_stderr
from abkit.utils.datetime_utils import now_utc_naive

if TYPE_CHECKING:
    from abkit.config.experiment_config import ExperimentConfig
    from abkit.config.metric_config import MetricConfig
    from abkit.config.project_config import ProjectConfig
    from abkit.database.internal_tables import InternalTablesManager
    from abkit.database.manager import BaseDatabaseManager

#: The window preset a dashboard boots with (``abk dashboard --window``'s
#: default, DASH-6 — exported so the CLI never spells a second copy).
DEFAULT_WINDOW_PRESET = "30d"

_MAX_BODY = 5_000_000  # generous cap on a posted job request (DASH-4)
_MAX_DRAIN = 32_000_000  # how much of an oversized body to drain before the 413

_STATS_PREFIX = "/api/stats/"
_JOB_PREFIX = "/api/job/"
_SOURCE_PREFIX = "/api/experiment-source/"
_REPORT_PREFIX = "/experiment/"
_STOP_SUFFIX = "/stop"

#: Cap on a posted string field (an experiment or metric name). Both are looked
#: up rather than parsed, so the cap is not about parsing: an unbounded value
#: would be echoed back inside the "unknown …" 400, turning a 5 MB body into a
#: 5 MB error.
_MAX_FIELD = 200

#: Cap on the YAML text ``GET /api/experiment-source`` returns. A config is a
#: few kB; the bound is what keeps a pathological file out of a JSON reply
#: (``truncated`` says so on the wire, the house discipline of ``_MAX_LINES`` /
#: ``MAX_STAT_POINTS``).
_MAX_SOURCE_BYTES = 512_000

#: How long ``POST /api/explore`` waits for the spawned cockpit to print its
#: URL. The donor's 90 s, and its risk too: a legitimately slow session load
#: (large project, cold DB) surfaces as "did not start in time" with the child's
#: own output attached. Per-server so a test can shrink it; a CLI override is a
#: named follow-up, not this WP.
_EXPLORE_URL_TIMEOUT = 90.0

#: How long a caller that did NOT spawn the cockpit waits for its URL. Every
#: waiter holds a request thread, and repeat clicks all land on the one deduped
#: job — so only the caller that started it pays the full timeout, and the rest
#: are told "still starting" in seconds instead of piling up 90 s deep. (A
#: bounded admission semaphore, the shape ``tuning/server.py`` uses for its own
#: long route since m10 WP4, is the follow-up if that ever proves too generous.)
_EXPLORE_DEDUP_WAIT = 10.0

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


# ── the spawned CLI (DASH-4) ────────────────────────────────────────────────
#
# Every mutation the cockpit offers is one of these argv lists in a subprocess.
# They are module-level so a test can point a route at a stub without a fake
# server (the donor's reason too, `detectkit/ui/server.py:142-219`).

#: Three things the spawn form has to get right, and why it is neither a bare
#: ``abk`` nor a plain ``-m``:
#:
#: 1. **This interpreter's abkit, not a ``PATH`` lookup.** A bare ``abk`` is a
#:    different install whenever the dashboard runs from a venv that was never
#:    activated (``python -m abkit …``, an editor's interpreter, a Prefect
#:    worker), so the cockpit would launch a DIFFERENT abkit version than the one
#:    serving the page. Pinning ``sys.executable`` pins the install with it.
#: 2. **The project directory must not be importable.** Both ``-m`` and ``-c``
#:    put the child's CWD on ``sys.path[0]`` — and a job spawns with
#:    ``cwd=<project root>``, the operator's directory, not ours. A file there
#:    named after anything abkit imports (``click.py``, ``yaml.py``,
#:    ``statistics.py``, …) would break every button with a traceback nobody can
#:    connect to the click, and an ``abkit/`` directory there would run a
#:    different abkit than the one (1) just pinned. Typing ``abk run`` in a
#:    terminal does no such thing — a console script puts its own bin directory
#:    on the path, never the CWD — so the bootstrap drops the CWD before
#:    importing anything. That is what makes "exactly as if typed" true.
#: 3. **The child should name itself ``abk``.** ``sys.argv[0]`` is what click
#:    prints in a usage error: under ``-m`` it is ``main.py``, under ``-c`` it is
#:    ``-c``. Neither is a command an operator could retype.
#:
#: What this pins is the interpreter, hence that interpreter's INSTALLED abkit:
#: with the CWD dropped, a bare source checkout that was never ``pip install
#: -e .``'d cannot import abkit at all, and every job fails with a
#: ``ModuleNotFoundError`` in its own output. Warning about that once at startup
#: belongs to the command that starts the cockpit (DASH-6), not to a route.
_CLI_BOOTSTRAP = (
    "import sys, os; "
    "sys.path[:] = [p for p in sys.path if p not in ('', os.getcwd())]; "
    "sys.argv[0] = 'abk'; "
    "from abkit.cli.main import cli; cli()"
)
_CLI_PREFIX = (sys.executable, "-c", _CLI_BOOTSTRAP)

#: The URL line ``serve_explore`` echoes (``"  Explore: http://127.0.0.1:…"``).
#: The scheme is part of the pattern on purpose: ``abk explore`` ALSO prints a
#: ``"Explore: <experiment name>"`` header first (``cli/commands/explore.py``),
#: so the donor's bare ``"Tuner:" in line`` predicate ported literally would
#: match the header and hand the client an experiment name as a URL.
_EXPLORE_URL_RE = re.compile(r"Explore:\s*(https?://\S+)")


def _subprocess_env() -> dict[str, str]:
    """The spawned CLI's environment: the dashboard's own, plus unbuffered I/O.

    Without ``PYTHONUNBUFFERED`` a child whose stdout is a pipe block-buffers it,
    so the job drawer would sit empty for minutes and then print everything at
    once — the pump can only stream what the child flushes.
    """
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _run_argv(*, select: str, metric: str | None, profile: str | None) -> list[str]:
    """``abk run --select … [--metric …] [--profile …]`` (DASH-4a's flag)."""
    argv = [*_CLI_PREFIX, "run", "--select", select]
    if metric:
        argv += ["--metric", metric]
    if profile:
        argv += ["--profile", profile]
    return argv


def _unlock_argv(*, select: str, profile: str | None) -> list[str]:
    """``abk unlock --select … [--profile …]``."""
    argv = [*_CLI_PREFIX, "unlock", "--select", select]
    if profile:
        argv += ["--profile", profile]
    return argv


def _clean_argv(*, select: str, profile: str | None) -> list[str]:
    """``abk clean --select … --execute [--profile …]``.

    ``--execute`` is what makes the button do anything (``abk clean`` is a dry
    run by default), so the route is the destructive form and DASH-5 owns the
    confirmation — which is also why the flag rides in the job LABEL rather than
    only in the argv: the drawer must show what actually ran. ``--orphaned-
    experiments`` is deliberately not offered: purging an experiment's whole
    history is not a one-click action, and it is the only ``clean`` path that
    prompts.
    """
    argv = [*_CLI_PREFIX, "clean", "--select", select, "--execute"]
    if profile:
        argv += ["--profile", profile]
    return argv


def _explore_argv(*, select: str, profile: str | None) -> list[str]:
    """``abk explore --select … --no-open [--profile …]``.

    ``--no-open`` because the DASHBOARD opens the tab, from the URL the child
    prints: without it the operator gets two.
    """
    argv = [*_CLI_PREFIX, "explore", "--select", select, "--no-open"]
    if profile:
        argv += ["--profile", profile]
    return argv


def _label_for(argv: Sequence[str]) -> str:
    """The job label: the command an operator would have typed.

    Derived from the argv that actually ran rather than formatted a second time,
    so the drawer cannot show a command that differs from the process — which
    matters most for the flags a caller does not choose (``--execute``,
    ``--no-open``, the profile).
    """
    return " ".join(["abk", *argv[len(_CLI_PREFIX) :]])


def _escape_glob(pattern: str) -> str:
    """Make ``*``, ``?`` and ``[`` in a real file name match themselves.

    ``pathlib`` has no escape API, but a one-character class is one:
    ``[`` → ``[[]``, ``*`` → ``[*]``, ``?`` → ``[?]``. A bare ``]`` needs
    nothing — outside a class it is already literal. ``[`` is escaped FIRST, or
    the brackets introduced for ``*``/``?`` would be escaped in turn.
    """
    return pattern.replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


def _selector_for(experiment_path: Path, experiment: ExperimentConfig, project_root: Path) -> str:
    """The ``--select`` value naming EXACTLY the clicked experiment.

    The YAML path relative to the project root, not the experiment name — and
    that is not cosmetic. ``config.discovery.select_configs`` resolves a bare
    name by trying ``<experiments>/<name>.yml`` FIRST and only then searching the
    ``name:`` fields, so in a project where one file is named after ANOTHER
    experiment (``experiments/alpha.yml`` declaring ``name: beta``, with ``alpha``
    declared in some other file) a name selector resolves to the wrong
    experiment. The dashboard would then run, unlock, clean or explore something
    other than the row that was clicked, with nothing anywhere saying so. A path
    contains a ``/``, which takes ``select_configs``' glob branch and resolves to
    exactly one file, and it also satisfies ``abk explore``'s "must match exactly
    ONE" by construction.

    A glob metacharacter in the file name is ESCAPED rather than made a reason to
    fall back (see :func:`_escape_glob`): the fallback is a name, and a name is
    the hazard this function exists to avoid — ``checkout[v2].yml`` is a legal,
    unremarkable file name, and ``experiments/star*.yml`` left raw would match a
    SIBLING as well. Only two cases genuinely have no path form: a file outside
    the project root, and one with no directory part (neither is something
    discovery produces). Those do fall back to the name, which resolves through
    ``select_experiments`` — including under a renamed ``paths.experiments``,
    which that function reads from the project config (it hard-coded
    ``experiments/`` until the ``0.6.x`` fix) — which is
    why the routes never use this function directly: :func:`_verified_selector`
    re-resolves whatever comes out of it and refuses to spawn unless it lands on
    the clicked experiment.
    """
    try:
        relative = experiment_path.relative_to(project_root).as_posix()
    except ValueError:
        return experiment.name
    if "/" not in relative:
        return experiment.name
    return _escape_glob(relative)


def _verified_selector(srv: _DashboardServer, entry: tuple[Path, ExperimentConfig]) -> str:
    """The ``--select`` value for *entry*, proven to still name that experiment.

    :func:`_selector_for` derives it; this re-resolves it through
    ``select_experiments`` — **the child's own resolver** — and refuses to spawn
    unless it lands on exactly the clicked experiment. Two failures need that,
    and neither is loud on its own:

    * **A boot snapshot outliving the file.** The served selection is read once
      (module docstring), so after a YAML is renamed, moved or deleted the row is
      still there and the selector still points at the old path — and
      ``abk run``/``unlock``/``clean`` answer an unmatched selector with a
      warning line, "Nothing selected." and **exit 0**. The job would land in the
      drawer green, having computed nothing. A 400 naming the drift is the honest
      answer, and it is the reason the plan's step-4 second resolution exists.
    * **The name fallback re-opening the shadow.** Where a path cannot be a
      selector (a glob metacharacter in the file name, see
      :func:`_selector_for`), the fallback is a name — which resolves file-first
      and can therefore land on a DIFFERENT experiment. Refusing beats running
      the wrong one: for ``clean --execute`` or an explore Apply, "the wrong
      experiment" means the wrong rows deleted or the wrong YAML rewritten.

    The residual window (the file moves between this check and ``Popen``) is
    microseconds, and its outcome is the old exit-0 "Nothing selected." — the
    check narrows the hazard rather than pretending to close it.
    """
    experiment_path, experiment = entry
    selector = _selector_for(experiment_path, experiment, srv.project_root)
    # Imported here, not at module scope: this is the CLI's selection seam, and
    # the module otherwise stays clear of the config package (the `_json_default`
    # lazy-import precedent).
    from abkit.config import select_experiments

    resolved, _warnings = select_experiments(srv.project_root, (selector,))
    names = [config.name for _path, config in resolved]
    if names != [experiment.name]:
        landed = ", ".join(names) if names else "nothing"
        raise ValueError(
            f"'{selector}' no longer resolves to {experiment.name} (it now matches "
            f"{landed}) — the dashboard reads its selection once at boot, so "
            "restart it after moving, renaming or removing an experiment"
        )
    return selector


def _explore_url(line: str) -> str | None:
    """The cockpit URL in *line*, or ``None`` — the predicate AND the extractor.

    One function for both so the wait and the parse cannot drift onto different
    patterns (the donor kept a loose ``in`` predicate beside a strict regex,
    which is what let its header line qualify).
    """
    match = _EXPLORE_URL_RE.search(line)
    return match.group(1) if match else None


# ── posted-body validation (every failure is a ValueError → 400) ─────────────


def _load_json(body: bytes) -> dict[str, Any]:
    """The posted JSON object.

    Raises ``ValueError`` for anything else — including a bodyless POST, which
    read as ``{}`` would land on ``'select' is required`` and send the client
    looking for a field it did send (in a body that never arrived, or arrived
    empty because a fetch dropped its payload). Same status either way; the
    guard exists so the message names the actual fault. ``UnicodeDecodeError``
    and ``JSONDecodeError`` are both ``ValueError`` subclasses, so ``do_POST``'s
    handler answers 400 for all three.
    """
    if not body.strip():
        raise ValueError("a JSON body is required")
    try:
        payload = json.loads(body.decode("utf-8"))
    except RecursionError as exc:
        # 30 kB of `[[[[…` exhausts the decoder's stack, and RecursionError is a
        # RuntimeError — so without this it lands in do_POST's generic handler
        # and a malformed BODY is reported as a 500 naming an internal class.
        raise ValueError("the JSON body is nested too deeply") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"the body must be a JSON object, got {type(payload).__name__}")
    return payload


def _reject_unknown_fields(payload: dict[str, Any], allowed: set[str]) -> None:
    """Refuse a body carrying a field this route does not act on.

    Ignoring it (the donor's shape) is the silent half of a feature that does not
    exist: a client posting ``{"full_refresh": true}`` to ``/api/run`` would get
    a plain run and no hint that the flag went nowhere. Adding a knob stays a
    deliberate act on both sides.

    A ``null`` value is exempt, because it asks for nothing: ``metric`` is
    allowed on ``/api/run`` only, and a client whose request helper always emits
    ``{select, metric}`` spells "the whole experiment" as ``metric: null`` —
    exactly the convention :func:`_string_field` blesses. Refusing that on the
    other three routes would fail Unlock/Clean/Explore for a client that followed
    the documented rule; a non-null unknown field is still refused.
    """
    named = {key for key, value in payload.items() if value is not None}
    unknown = sorted(str(key)[:_MAX_FIELD] for key in named - allowed)
    if unknown:
        raise ValueError(
            f"unknown field(s) {', '.join(repr(key) for key in unknown[:5])} — "
            f"this route accepts {', '.join(sorted(allowed))}"
        )


def _string_field(payload: dict[str, Any], field: str, *, required: bool = True) -> str | None:
    """A posted string field, validated; ``None`` only for an absent optional one.

    A JSON ``null`` reads as absent (clients spell "no metric" that way), but an
    EMPTY string does not: a blank is a client bug, and answering it with the
    whole-experiment run it does not mean is the ``keep_blank_values`` lesson
    from the GET side.
    """
    value = payload.get(field)
    if value is None:
        if required:
            raise ValueError(f"'{field}' is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field}' must be a non-empty string")
    if len(value) > _MAX_FIELD:
        raise ValueError(f"'{field}' is longer than {_MAX_FIELD} characters")
    return value


def _resolve_target(
    srv: _DashboardServer, payload: dict[str, Any]
) -> tuple[Path, ExperimentConfig]:
    """The served ``(path, config)`` a job route's ``select`` names.

    The boot index is the authority — a job route can only act on an experiment
    the page is showing. Unknown is a 400, not the 404 an unknown ``GET
    /api/stats/<name>`` gets: the resource here is the route, which exists, and
    the fault is in the body (DASH-3's status map reads 404 for a name in the
    PATH, and the donor answers 400 for a name in a body).
    """
    name = _string_field(payload, "select")
    assert name is not None  # required=True never returns None
    entry = srv.experiment_entry(name)
    if entry is None:
        raise ValueError(f"unknown experiment: {name} is not in this dashboard's selection")
    return entry


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
        # The report route's two optional collaborators (DASH-5) — defaulted,
        # unlike `project`/`tables`, because every OTHER route works without
        # them and both degradations are honest (see `_report_payload`): the
        # metric configs supply the report's metric descriptions, and the raw
        # manager lets the no-copy default snapshot the live cohort for the SRM
        # chip's observed counts. `abk dashboard` (DASH-6) passes both — it
        # already holds them.
        self.metrics: Mapping[str, MetricConfig] = {}
        self.manager: BaseDatabaseManager | None = None
        # How long POST /api/explore waits for the spawned cockpit's URL line.
        self.explore_url_timeout: float = _EXPLORE_URL_TIMEOUT
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
        """Authorization + transport for the job routes.

        The token check comes first here too, so a new route inherits it by
        extending ``_route_post`` rather than by remembering to gate.

        Here ``ValueError`` DOES map to 400, unlike ``do_GET``: a POST body is
        arbitrary client-supplied JSON, ``json.JSONDecodeError`` *is* a
        ``ValueError``, and "malformed request" is what the explore server
        answers 400 for. The asymmetry is the difference between an argument the
        route parses and one it looks up — and it is why every body validator in
        this module signals by raising ``ValueError``.

        ``JobManagerClosed`` is the one status the routes cannot express as a
        reply: it means the cockpit is tearing down (Ctrl-C), so the spawn was
        refused and its child already killed. 503, not the busy 400 — "try
        later" would be a lie about a server that is going away.
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
        except JobManagerClosed as exc:
            self._reply_error(503, f"{exc}")
        except ValueError as exc:
            self._reply_error(400, f"{exc}")
        except OSError as exc:
            # A spawn that could not even start: the project root moved out from
            # under the running cockpit, or the OS refused a fork. Server-side,
            # so 500 — but say WHERE it tried to run, because a bare errno on a
            # POST reads like a routing bug.
            self._reply_error(
                500,
                f"could not start a subprocess in {srv.project_root}: "
                f"{type(exc).__name__}: {exc}",
            )
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
            # `no-store` for the same reason the JSON replies carry it, and it
            # matters on both HTML routes: a report reopened after a Run must not
            # be the pre-run render, and the shell reloaded after a restart must
            # not be the previous selection's.
            self.send_header("Cache-Control", "no-store")
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
        if path.startswith(_SOURCE_PREFIX):
            self._handle_experiment_source(srv, unquote(path[len(_SOURCE_PREFIX) :]))
            return
        if path.startswith(_REPORT_PREFIX):
            self._handle_report(srv, unquote(path[len(_REPORT_PREFIX) :]).strip("/"))
            return
        if path.startswith(_JOB_PREFIX):
            job_id = unquote(path[len(_JOB_PREFIX) :]).strip("/")
            self._handle_job(srv, job_id, query.get("offset", ["0"])[0])
            return
        self._reply_error(404, f"not found: {path}")

    def _route_post(self, srv: _DashboardServer, body: bytes) -> None:
        """The job routes (DASH-4): spawn an ``abk`` subprocess, or stop one.

        Unrouted paths keep DASH-3's honest 404 — including ``/api/job/<id>``
        without the ``/stop`` suffix, which is a GET route.
        """
        path = urlparse(self.path).path
        if path == "/api/run":
            self._handle_run(srv, body)
            return
        if path == "/api/unlock":
            self._handle_pipeline_job(srv, body, "unlock", _unlock_argv)
            return
        if path == "/api/clean":
            self._handle_pipeline_job(srv, body, "clean", _clean_argv)
            return
        if path == "/api/explore":
            self._handle_explore(srv, body)
            return
        if path.startswith(_JOB_PREFIX) and path.endswith(_STOP_SUFFIX):
            job_id = unquote(path[len(_JOB_PREFIX) : -len(_STOP_SUFFIX)]).strip("/")
            self._handle_stop(srv, job_id)
            return
        self._reply_error(404, f"not found: {path}")

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

    def _handle_experiment_source(self, srv: _DashboardServer, name: str) -> None:
        """One experiment's raw YAML — the read-only "open in your editor" route.

        No DB, no config parse: the text on disk as the operator would see it,
        plus the root-relative path the row already carries as ``file`` (one
        vocabulary — the client can show either without a second derivation).

        The route addresses an experiment by NAME and takes the path from the
        boot index, so no client-supplied string ever reaches the filesystem: a
        ``?path=`` parameter would be a traversal seam on a server whose whole
        job is to hand out file contents.

        A file that disappeared since boot is a 404 (the boot snapshot is not
        refreshed — module docstring), while a permission or I/O failure escapes
        to ``do_GET``'s 500: "not found" would be a wrong diagnosis, and this
        route has nothing to degrade to.
        """
        entry = srv.experiment_entry(name)
        if entry is None:
            self._reply_error(404, f"unknown experiment: {name}")
            return
        experiment_path, experiment = entry
        _dir, relative = resolve_experiment_location(
            experiment_path, srv.project_root, experiments_base_dir(srv.project_root, srv.project)
        )
        try:
            with experiment_path.open("rb") as handle:
                # One byte past the cap distinguishes "exactly at the cap" from
                # "truncated" without a stat() race.
                raw = handle.read(_MAX_SOURCE_BYTES + 1)
        except FileNotFoundError:
            self._reply_error(
                404,
                f"{relative} is gone — the dashboard reads its selection once at "
                "boot, so restart it after moving or deleting an experiment",
            )
            return
        self._reply_json(
            {
                "name": experiment.name,
                "path": relative,
                # errors="replace" also covers a cut mid-codepoint: the slice is
                # by bytes, and a mojibake tail beats a 500 on a legal file.
                "yaml_text": raw[:_MAX_SOURCE_BYTES].decode("utf-8", errors="replace"),
                "truncated": len(raw) > _MAX_SOURCE_BYTES,
            }
        )

    def _handle_report(self, srv: _DashboardServer, name: str) -> None:
        """``GET /experiment/<name>`` — the full report page, behind Open (DASH-5).

        The SAME payload + bundle ``abk run --report`` writes
        (``build_report_payload`` → ``render_report_html``), rendered on demand
        for ONE experiment rather than read off disk: a `reports/` file exists
        only if someone passed ``--report``, and it would be as old as that run.
        Nothing is written; the page is built and streamed.

        This is the one place the dashboard reaches for ``build_report_payload``,
        and it does not weaken ``overview.py``'s rule against it — a ROW must be
        the readout's own cheap verdict, while this route's whole job is to be
        byte-for-byte the report an operator would otherwise generate.

        Two costs, both deliberate and both paid only on a click: the payload
        re-reads every persisted look of every comparison, and in the no-copy
        default it executes the assignment source once for the SRM chip's
        observed counts (``build_report_payload``'s ``manager`` seam). Both run
        under ``db_lock``, so a slow render queues the row fills behind it
        instead of sharing the one DB connection — but the BAKE (reading the
        committed report bundle, one regex pass) is outside it: the lock guards
        the connection, not the CPU.
        """
        entry = srv.experiment_entry(name)
        if entry is None:
            self._reply_error(404, f"unknown experiment: {name}")
            return
        _experiment_path, experiment = entry
        with srv.db_lock:
            payload = _report_payload(srv, experiment)
        self._reply_html(render_report_html(payload))

    # -- POST handlers: spawn `abk`, never do the work ------------------------

    def _handle_run(self, srv: _DashboardServer, body: bytes) -> None:
        """``POST /api/run`` — the whole experiment, or ONE of its metrics.

        The optional ``metric`` is validated with
        :meth:`~abkit.config.experiment_config.ExperimentConfig.declares_metric`,
        the predicate ``abk run --metric``'s own selection narrowing uses
        (DASH-4a as-built (1)), so the route's 400 and the CLI's idea of a valid
        target cannot drift. There is no per-comparison or per-arm-pair
        addressing to expose: a metric binds at most once per experiment.
        """
        payload = _load_json(body)
        _reject_unknown_fields(payload, {"select", "metric"})
        experiment_path, experiment = _resolve_target(srv, payload)
        metric = _string_field(payload, "metric", required=False)
        if metric is not None and not experiment.declares_metric(metric):
            declared = ", ".join(comparison.metric for comparison in experiment.comparisons)
            raise ValueError(
                f"'{metric}' is not a configured comparison of "
                f"'{experiment.name}' (have: {declared})"
            )
        argv = _run_argv(
            select=_verified_selector(srv, (experiment_path, experiment)),
            metric=metric,
            profile=srv.profile,
        )
        self._spawn_pipeline(srv, "run", argv, experiment.name)

    def _handle_pipeline_job(
        self,
        srv: _DashboardServer,
        body: bytes,
        kind: str,
        build_argv: Callable[..., list[str]],
    ) -> None:
        """``POST /api/unlock`` and ``POST /api/clean`` — same shape as run."""
        payload = _load_json(body)
        _reject_unknown_fields(payload, {"select"})
        experiment_path, experiment = _resolve_target(srv, payload)
        argv = build_argv(
            select=_verified_selector(srv, (experiment_path, experiment)),
            profile=srv.profile,
        )
        self._spawn_pipeline(srv, kind, argv, experiment.name)

    def _spawn_pipeline(
        self, srv: _DashboardServer, kind: str, argv: list[str], experiment: str
    ) -> None:
        """Spawn a one-at-a-time job, or answer the donor's busy 400.

        ``spawn_pipeline`` decides that atomically, so the advisory
        ``pipeline_active`` chip a client polls can be stale without letting two
        runs start. ``JobManagerClosed`` deliberately passes through to
        ``do_POST``'s 503: ``None`` means busy, which a teardown is not.
        """
        job = srv.jobs.spawn_pipeline(
            kind,
            _label_for(argv),
            argv,
            cwd=srv.project_root,
            env=_subprocess_env(),
            experiment=experiment,
        )
        if job is None:
            self._reply_error(400, "a pipeline job is already running")
            return
        self._reply_json({"job_id": job.id})

    def _handle_explore(self, srv: _DashboardServer, body: bytes) -> None:
        """``POST /api/explore`` — spawn a cockpit and hand back its URL.

        Not a pipeline job: two cockpits on DIFFERENT experiments are fine (an
        explore takes no pipeline lock), so this route is deduped per experiment
        instead of gated one-at-a-time — atomically, through
        :meth:`~abkit.tuning.jobs.JobManager.spawn_deduped`, because a
        double-clicked button would otherwise start two sessions on one
        experiment and each Apply would write the YAML from its own snapshot.

        A second call for a live cockpit answers with the SAME job and URL (the
        client reopens that tab). If the URL has not been printed yet it waits for
        it too, rather than taking the donor's immediate "already starting"
        refusal — one fewer error a quick double-click can produce — but on a
        SHORTER budget (:data:`_EXPLORE_DEDUP_WAIT`), because every waiter holds a
        request thread and repeat clicks all land on the one deduped job.

        When no URL arrives, only a job THIS request spawned is stopped: the other
        caller's session may simply be slower than our own deadline, and killing
        it would turn one slow tab into two failures. The 400 says which of the
        three things happened — the child exited without serving, our deadline
        lapsed, or someone else's cockpit is still starting — and carries the
        child's last lines either way, which is where "no computed results yet —
        run `abk run` first" shows up: the D2 noop exits 0 without ever serving.
        """
        payload = _load_json(body)
        _reject_unknown_fields(payload, {"select"})
        experiment_path, experiment = _resolve_target(srv, payload)
        argv = _explore_argv(
            select=_verified_selector(srv, (experiment_path, experiment)),
            profile=srv.profile,
        )
        job, created = srv.jobs.spawn_deduped(
            "explore",
            _label_for(argv),
            argv,
            cwd=srv.project_root,
            env=_subprocess_env(),
            experiment=experiment.name,
        )
        url = srv.jobs.url_for(job)
        if url is None:
            # Only the caller that started it pays the full timeout; a repeat
            # click waits seconds, because every waiter holds a request thread.
            budget = (
                srv.explore_url_timeout
                if created
                else min(srv.explore_url_timeout, _EXPLORE_DEDUP_WAIT)
            )
            line = srv.jobs.wait_for_line(job, lambda text: _explore_url(text) is not None, budget)
            url = None if line is None else _explore_url(line)
        if url is None:
            # Snapshot BEFORE the stop, or the stop's own status change races the
            # message: the wait ends for two different reasons and only the job
            # knows which — the deadline lapsed (still running) or the child
            # exited first (`wait_for_line` returns None for that too). Inferring
            # it from `created` would tell a second caller that a dead cockpit
            # "is still starting".
            snapshot = srv.jobs.snapshot(job, 0)
            if created:
                srv.jobs.stop(job.id)
            if snapshot["status"] != "running":
                what = f"exited without serving ({snapshot['status']})"
            elif created:
                what = "did not start in time"
            else:
                what = "is still starting (another tab launched it)"
            tail = "\n".join(snapshot["lines"][-20:])
            self._reply_error(
                400, f"the explore cockpit for {experiment.name} {what} — output:\n{tail}"
            )
            return
        srv.jobs.set_url(job, url)
        self._reply_json({"job_id": job.id, "url": url})

    def _handle_stop(self, srv: _DashboardServer, job_id: str) -> None:
        """``POST /api/job/<id>/stop`` — SIGTERM now, SIGKILL after the grace.

        Unknown id is a 404 (the id is in the path, like ``GET
        /api/job/<id>``); a job that is no longer running is a 400. The donor
        conflates the two into one 400, which reads as "your id is wrong" for a
        job that simply finished a moment earlier — including the honest race
        where it exits between the lookup and the stop.
        """
        if srv.jobs.get(job_id) is None:
            self._reply_error(404, f"unknown job: {job_id}")
            return
        if not srv.jobs.stop(job_id):
            self._reply_error(400, f"job {job_id} is not running")
            return
        self._reply_json({"ok": True})


def render_report_html(payload: dict[str, Any]) -> str:
    """The report bake — the ONE renderer ``abk run --report`` uses.

    A thin re-export so the route (and its tests) never hold a second import
    path, and so the bake stays outside ``db_lock``.
    """
    from abkit.reporting import render_report_html as bake

    return bake(payload)


def _report_payload(srv: _DashboardServer, experiment: ExperimentConfig) -> dict[str, Any]:
    """Build the §5.3 report payload for one experiment (the DB half).

    Imported here rather than at module scope for the reason the selector import
    has: the dashboard's read path is otherwise reporting-free, and this is the
    one seam that deliberately is not.

    ``metrics`` and ``manager`` are optional on the server, so this degrades
    honestly rather than pretending: without the metric configs the report
    simply carries no metric descriptions, and without a manager the no-copy
    default has no live cohort to count — which would otherwise read as a real
    "0 / 0" split beside a green SRM chip, so it is said out loud in the
    payload's own warnings instead.

    **A cohort source that fails to validate costs the counts, not the page.**
    In the no-copy default the builder executes the assignment SQL for those
    counts, and a source that emptied or corrupted since the last run raises
    (``abk explore`` turns the same failure into a clean CLI error). Here the
    page is the whole point of the click, so the render falls back to the
    manager-less build and names the failure in the payload's warnings —
    ``abk run``'s "never fail the run on a report" discipline, applied to a
    route. A failure the retry reproduces is a genuinely broken read and
    propagates to ``do_GET``'s 500.
    """
    from abkit.reporting import build_report_payload

    def build(manager: BaseDatabaseManager | None) -> dict[str, Any]:
        return build_report_payload(
            experiment,
            srv.tables,
            project=srv.project,
            metric_configs=srv.metrics or None,
            # The builder never reads the clock (determinism) — the caller formats.
            generated_at=now_utc_naive().strftime("%Y-%m-%d %H:%M UTC"),
            manager=manager,
            project_root=srv.project_root,
        )

    note: str | None = None
    if srv.manager is None and not experiment.assignment.cohort_copy.enabled:
        note = (
            "the dashboard was started without a database manager, so this "
            "no-copy experiment's SRM chip shows ZERO observed units — the flag "
            "and p-value are still the persisted gate's own"
        )
        payload = build(None)
    else:
        try:
            payload = build(srv.manager)
        except Exception as exc:  # noqa: BLE001 — the counts are not worth the page
            note = (
                f"the cohort source could not be read ({type(exc).__name__}: {exc}), so "
                "the SRM chip shows ZERO observed units — the flag and p-value are "
                "still the persisted gate's own"
            )
            payload = build(None)
    if note is not None:
        payload["warnings"] = [*payload["warnings"], note]
    return payload


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
    metrics: Mapping[str, MetricConfig] | None = None,
    manager: BaseDatabaseManager | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[_DashboardServer, str]:
    """Construct (without running) the dashboard server; return ``(server, url)``.

    The bound port is known only after construction, so the page is rendered
    ONCE post-bind, exactly like ``build_explore_server`` — but no URLs are
    baked into the payload (see :func:`_boot_payload`).

    *metrics* and *manager* feed ``GET /experiment/<name>`` alone (DASH-5's Open
    button): the metric configs become the report's metric descriptions, and the
    manager — the SAME one *tables* wraps, used only under ``db_lock`` — lets the
    no-copy default snapshot the live cohort for the SRM chip's observed counts.
    Both are optional and both degrade in the open (:func:`_report_payload`).

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
        if metrics is not None:
            server.metrics = metrics
        server.manager = manager
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
    metrics: Mapping[str, MetricConfig] | None = None,
    manager: BaseDatabaseManager | None = None,
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
        metrics=metrics,
        manager=manager,
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
