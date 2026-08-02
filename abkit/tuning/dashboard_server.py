"""The dashboard localhost server (``docs/specs/m11-implementation-plan.md``
DASH-3 + DASH-4).

The project-level cockpit's transport: a pure-stdlib ``ThreadingHTTPServer``
bound to ``127.0.0.1`` with a one-shot token, serving the metadata-only boot
page and, per row, one lazily-fetched statistics reply built by
``tuning/overview.py`` (DASH-2). Every job route — Run / Explore / Unlock /
Clean (DASH-4) — sits on top of the :class:`~abkit.tuning.jobs.JobManager` this
server holds (DASH-1).

**The dashboard computes no statistic and takes no pipeline lock** (§0.5(d) as
restated by UI-1). That is the whole of the launcher invariant, and it is what
the two gates in ``tests/tuning/test_dashboard_server.py`` have always actually
enforced — an AST scan proving this module never names the lock API, plus a spy
proving no route reaches it through a helper. Every verdict comes from
``readout.evaluate()`` — through ``overview.py`` for a row, through
``reporting.build_report_payload`` for the Open button's report page — and every
*pipeline* action is a real ``abk`` subprocess spawned by DASH-4's routes,
exactly as if typed at a terminal. The two reads that do touch the warehouse are
the row fill and (in the no-copy default) that report page's cohort snapshot;
both go through the single ``InternalTablesManager``/manager pair serialized by
``db_lock`` — a DB-API connection is not thread-safe.

**The editor (UI-1) writes YAML, and that is not a violation of the above.**
M11 wrote the invariant as "computes a statistic, turns a knob, writes a config
or takes the pipeline lock", which folded a fourth clause into it that no gate
ever checked; what the invariant protects is that no number on the page was
produced here and that nothing here can block a pipeline. A config write does
neither. ``POST /api/experiment/{save,create,delete}`` go through
``tuning/config_files.py`` — validate (both levels) → archive verbatim →
atomic write — and the file they touch is the operator's own declaration, not
a result. What a write DOES do is make the boot snapshot stale, which is why
this module now owns a re-resolution seam (``reload_selection``) instead of the
"restart the dashboard" note M11 left behind.

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
plus ``GET /api/experiment-source/<name>`` (DASH-4 — the experiment's raw YAML,
carrying since UI-1 the digest the editor saves against), ``GET
/api/experiments`` (the served selection, in the boot payload's own shape) and
``GET /experiment/<name>`` (DASH-5 — the full report page behind the Open
button, the one route that answers HTML rather than JSON). Job routes (DASH-4):
``POST /api/run`` (optionally one ``metric``), ``POST /api/unlock``, ``POST
/api/clean``, ``POST /api/explore`` and ``POST /api/job/<id>/stop``. Every one
of them spawns — or stops — a real ``abk`` subprocess; none of them computes,
reads the warehouse, or writes a file. Run / Unlock / Clean answer as soon as the
child exists (a selector resolve, then ``Popen``), but **``/api/explore`` is a
long request by design**: it holds the response until the spawned cockpit prints
its URL, up to ``explore_url_timeout`` (90 s), so a client must give that one
route a long fetch timeout and a spinner.

Editor routes (UI-1): ``POST /api/experiment/save``, ``POST
/api/experiment/create``, ``POST /api/experiment/delete``, plus ``POST
/api/reload``. They mutate YAML through ``tuning/config_files.py`` and then
re-resolve the selection; none of them spawns a process, reads the warehouse or
takes the pipeline lock. Two refusals they own that a job route does not: a
stale ``digest`` (the file changed on disk since the editor opened it — an
``abk explore`` Apply, or a second tab), and a live job on the same experiment
(a running ``abk run`` has already read the config it is running, and a live
cockpit's Apply would overwrite whatever is saved here).

The one thing a job route deliberately does NOT do: take the pipeline lock, not
even briefly. The one process that does is the spawned child, in its own OS
process, exactly as if the command had been typed.

There is deliberately **no caching layer**: every ``/api/stats`` call re-reads
the DB, matching the donor. DASH-5's fixed-concurrency-3 client pool is what
bounds the load, not a server-side cache — and since ``db_lock`` serializes
those reads, a project of very long experiments loads its list in about the
sum of its reads, not the slowest one. Acceptable per the donor's own
one-connection-per-manager precedent (§DASH-3 "Risks / hotspots"): a comment,
not a fix, in this WP.

The served **selection** and the baked page were, in M11, snapshots taken at
boot and never refreshed — a restart was the only way to pick up an edited
YAML. UI-1 makes the editor the surface that edits them, so both are now
re-derived by :meth:`_DashboardServer.reload_selection`: every mutation route
calls it, ``POST /api/reload`` is the manual form, and the pair
(``experiments``, its by-name index, ``metrics``, ``html``) is written and read
under ``selection_lock``, never touched directly — the explore session's
cache-lock discipline. Two consequences worth knowing: a **failed** reload
(a broken sibling YAML, a name collision) keeps the previous selection and says
so in the reply rather than 500-ing after a write that already landed, and a
newly created experiment outside this cockpit's ``--select`` is reported as
created-but-not-shown instead of silently missing. The YAML *text* the source
route returns is still read live off disk, so between two reloads it can
legitimately disagree with the parsed config every other route uses.
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
from abkit.tuning import config_files
from abkit.tuning.html import render_dashboard_html
from abkit.tuning.jobs import JOB_KINDS, JobManager, JobManagerClosed
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

#: UI-1's editor verbs. Under ``/api/experiment/`` (singular) rather than the
#: report page's ``/experiment/`` prefix or the source route's, so no GET route
#: can ever read one of these as an experiment NAME.
_SAVE_PATH = "/api/experiment/save"
_CREATE_PATH = "/api/experiment/create"
_DELETE_PATH = "/api/experiment/delete"
_RELOAD_PATH = "/api/reload"

#: Cap on a posted string field (an experiment or metric name). Both are looked
#: up rather than parsed, so the cap is not about parsing: an unbounded value
#: would be echoed back inside the "unknown …" 400, turning a 5 MB body into a
#: 5 MB error.
_MAX_FIELD = 200

#: Cap on the YAML text ``GET /api/experiment-source`` returns — and, since
#: UI-1, on the text a save may POST back. A config is a few kB; the bound is
#: what keeps a pathological file out of a JSON reply (``truncated`` says so on
#: the wire, the house discipline of ``_MAX_LINES`` / ``MAX_STAT_POINTS``). It
#: is the SAME constant on both sides on purpose: a file the editor could only
#: read truncated must not be savable, or the save would write back the prefix
#: it was shown and silently drop the rest.
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
            f"{landed}) — this page's selection is older than the files; press "
            "Reload configs (or POST /api/reload) to re-read them"
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


def _text_field(payload: dict[str, Any], field: str) -> str:
    """A posted YAML document, validated as transport (never as a config).

    Separate from :func:`_string_field` because the cap is four orders of
    magnitude apart: a name is looked up (200 chars), a document is written
    (:data:`_MAX_SOURCE_BYTES`, the same bound the read route truncates at, so
    a file the editor could only show truncated cannot be saved back).
    Emptiness is refused here rather than by the YAML parser, whose message for
    ``""`` would be about a mapping.
    """
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field}' must be a non-empty YAML document")
    if len(value.encode("utf-8")) > _MAX_SOURCE_BYTES:
        raise ValueError(
            f"'{field}' is larger than {_MAX_SOURCE_BYTES} bytes — edit a file that "
            "size in your editor, not here"
        )
    return value


def _flag_field(payload: dict[str, Any], field: str) -> bool:
    """A posted boolean, as a boolean and nothing else.

    ``force`` overrides a refusal, so a truthy STRING must not silently arm it:
    a client sending ``"false"`` would get exactly the behaviour it asked not
    to have. Absent (or ``null``) is ``False``; anything but a real JSON boolean
    is a 400.
    """
    value = payload.get(field)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"'{field}' must be true or false, got {type(value).__name__}")
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
        # The selectors this cockpit was started with — what `reload_selection`
        # re-resolves. Empty means `abk dashboard` with no `--select`, i.e. the
        # whole project, which is `select_experiments`' own default.
        self.selectors: tuple[str, ...] = ()
        self.excludes: tuple[str, ...] = ()
        # Guards the four things a reload rewrites together: `experiments`,
        # `_by_name`, `metrics` and the baked `html`. A reader taking three of
        # them across a reload would render a row whose config it cannot look
        # up (the explore session's cache_lock discipline, m10 WP4).
        self.selection_lock = threading.RLock()
        # Serializes the EDITOR's writes end to end. The digest check and the
        # write are otherwise a check-then-act with the whole two-level
        # validation between them (measured at 25–180 ms), so two tabs saving
        # the same file both answered 200 and one edit existed nowhere — not on
        # disk, not in the archive. `abk explore`'s Apply is serialized for
        # exactly this reason (`server.py`'s heavy_lock, "two tabs' Applies
        # must not race the archive/rewrite seam"); this is that discipline for
        # the second write seam. It is NOT the pipeline lock — nothing outside
        # this process can see it, and no statistic waits on it.
        self.editor_lock = threading.Lock()
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

        The index is built BEFORE either field is replaced, so a duplicate
        leaves the previously served selection intact rather than half of it —
        which matters now that UI-1's reload calls this mid-serve, not only
        :func:`build_dashboard_server` at boot.
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
        with self.selection_lock:
            self.experiments = list(experiments)
            self._by_name = by_name

    def experiment_entry(self, name: str) -> tuple[Path, ExperimentConfig] | None:
        """The ``(path, config)`` for *name*, or ``None`` if it is not served."""
        with self.selection_lock:
            return self._by_name.get(name)

    def served_experiments(self) -> list[tuple[Path, ExperimentConfig]]:
        """The served selection, snapshotted under the lock."""
        with self.selection_lock:
            return list(self.experiments)

    def served_html(self) -> str:
        """The baked page, snapshotted under the lock (a reload re-bakes it)."""
        with self.selection_lock:
            return self.html

    def rebake(self) -> None:
        """Re-render the boot page from the current selection.

        Cheap and worth doing on every reload: without it a browser refresh
        after a create would paint the pre-create list, and the client's own
        refresh would then have to correct a page the operator already read.
        """
        with self.selection_lock:
            self.html = render_dashboard_html(
                _boot_payload(
                    project=self.project,
                    project_root=self.project_root,
                    experiments=self.experiments,
                    initial_window=self.initial_window,
                    profile=self.profile,
                )
            )

    def reload_selection(self) -> list[str]:
        """Re-resolve the selection from disk; return the warnings, never raise.

        THE re-derivation seam (UI-1). Every editor route calls it after a
        successful write, and ``POST /api/reload`` is its manual form.

        It never raises, and that is the whole design: the write has already
        landed by the time it runs, so a failure here — a sibling YAML that no
        longer parses, a name collision the editor could not have known about —
        must not turn a successful save into a 500 with no reply. The previous
        selection stays served and the reason rides back in the reply's
        ``warnings``, where the client shows it beside a "restart the
        dashboard" hint.
        """
        from abkit.config import select_experiments

        # The whole read-modify-write is one critical section, not three. Two
        # reloads in flight (four independently-clickable buttons produce them)
        # would otherwise interleave resolve-A → resolve-B → install-B →
        # install-A, and the STALE resolver wins — the page would silently drop
        # the change that finished second. `selection_lock` is an RLock so the
        # accessors below can re-enter it.
        with self.selection_lock:
            try:
                resolved, warnings = select_experiments(
                    self.project_root, self.selectors, self.excludes
                )
            except Exception as exc:  # noqa: BLE001 — a broken project must not kill a save
                return [
                    f"the project could not be re-read after the change "
                    f"({type(exc).__name__}: {exc}) — this page still shows the previous "
                    "selection; fix the config and press Reload configs"
                ]
            try:
                self.set_experiments(resolved)
            except ValueError as exc:
                return [
                    f"{exc} — this page still shows the previous selection; "
                    "fix the config and press Reload configs"
                ]
            self.metrics = _load_metric_configs(self.project_root, self.project)
            self.rebake()
            return list(warnings)

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
            self._reply_html(srv.served_html())
            return
        if path == "/api/experiments":
            self._reply_json(_selection_reply(srv))
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
        if path == _SAVE_PATH:
            self._handle_save(srv, body)
            return
        if path == _CREATE_PATH:
            self._handle_create(srv, body)
            return
        if path == _DELETE_PATH:
            self._handle_delete(srv, body)
            return
        if path == _RELOAD_PATH:
            self._handle_reload(srv, body)
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
        """One experiment's raw YAML — what UI-1's editor opens with.

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
                f"{relative} is gone — this page's selection is older than the "
                "files; press Reload configs (or POST /api/reload) to re-read them",
            )
            return
        truncated = len(raw) > _MAX_SOURCE_BYTES
        # errors="replace" also covers a cut mid-codepoint: the slice is by
        # bytes, and a mojibake tail beats a 500 on a legal file.
        text = raw[:_MAX_SOURCE_BYTES].decode("utf-8", errors="replace")
        self._reply_json(
            {
                "name": experiment.name,
                "path": relative,
                "yaml_text": text,
                "truncated": truncated,
                # The concurrency token UI-1's editor echoes back on save — of
                # the WHOLE file, not the text above, and `null` when the two
                # differ: a digest over a truncated read would let a save write
                # the prefix back and drop the rest, which is exactly the
                # silent data loss the digest exists to prevent. `editable`
                # says so in one field so the client never has to infer it.
                "digest": None if truncated else config_files.text_digest(text),
                "editable": not truncated,
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

    # -- POST handlers: edit a YAML, then re-resolve (UI-1) --------------------

    def _handle_save(self, srv: _DashboardServer, body: bytes) -> None:
        """``POST /api/experiment/save`` — overwrite one experiment's YAML.

        The text is the operator's, verbatim: ``config_files`` validates it
        (level 1 + the §8 matrix), archives the previous bytes and writes
        atomically. Two refusals precede the write and neither is the file
        system's: a job running on this experiment (:meth:`_refuse_if_busy`),
        and a ``digest`` that no longer matches disk.

        The experiment is addressed by NAME through the boot index, exactly like
        the read route — no client-supplied path ever reaches the filesystem.
        """
        payload = _load_json(body)
        _reject_unknown_fields(payload, {"select", "text", "digest", "force"})
        experiment_path, experiment = _resolve_target(srv, payload)
        text = _text_field(payload, "text")
        digest = _string_field(payload, "digest", required=False)
        force = _flag_field(payload, "force")
        self._refuse_if_busy(srv, experiment.name)
        self._refuse_editing_a_truncated_file(experiment_path)
        written = self._edit(
            srv,
            f"write {experiment.name}'s config",
            lambda: config_files.update_experiment_file(
                project_root=srv.project_root,
                project=srv.project,
                path=experiment_path,
                text=text,
                expected_digest=digest,
                force=force,
            ),
        )
        reply = _write_reply(srv, written)
        if written.renamed_from is not None:
            reply["warnings"] = [
                *reply["warnings"],
                f"the experiment was renamed {written.renamed_from} → "
                f"{written.config.name}: its persisted rows are still keyed by the OLD "
                "name, so the new one starts with no history (`abk clean "
                "--orphaned-experiments` prunes the old rows)",
            ]
        self._reply_json(reply)

    def _handle_create(self, srv: _DashboardServer, body: bytes) -> None:
        """``POST /api/experiment/create`` — a new experiment YAML.

        The file name comes from the config's own ``name:`` (one identity, the
        convention ``abk init`` scaffolds); ``folder`` optionally puts it in a
        subdirectory of ``paths.experiments``. Nothing is looked up in the boot
        index — that is the point of a create — so this is the one editor route
        whose target does not exist yet, and the uniqueness check that stands in
        for it covers the WHOLE project, not the served selection.
        """
        payload = _load_json(body)
        _reject_unknown_fields(payload, {"text", "folder", "force"})
        text = _text_field(payload, "text")
        folder = _string_field(payload, "folder", required=False) or ""
        force = _flag_field(payload, "force")
        written = self._edit(
            srv,
            "create a config",
            lambda: config_files.create_experiment_file(
                project_root=srv.project_root,
                project=srv.project,
                text=text,
                folder=folder,
                force=force,
            ),
        )
        reply = _write_reply(srv, written)
        if srv.experiment_entry(written.config.name) is None:
            # Created, but outside this cockpit's --select: it will not appear
            # in the list, and a silent absence reads as a failed create.
            reply["warnings"] = [
                *reply["warnings"],
                f"{written.config.name} was created but is outside this dashboard's "
                "selection, so it is not in the list — restart `abk dashboard` without "
                "the --select/--exclude that filters it out",
            ]
            reply["in_selection"] = False
        self._reply_json(reply)

    def _handle_delete(self, srv: _DashboardServer, body: bytes) -> None:
        """``POST /api/experiment/delete`` — archive the YAML, then remove it.

        Only the file goes. The experiment's rows stay in ``_ab_results`` /
        ``_ab_unit_state`` until ``abk clean --orphaned-experiments`` prunes
        them, and the reply says so: a delete button that quietly stranded a
        warehouse series would be the silent half of a destructive action. The
        archived copy makes it reversible by hand.
        """
        payload = _load_json(body)
        _reject_unknown_fields(payload, {"select", "digest"})
        experiment_path, experiment = _resolve_target(srv, payload)
        digest = _string_field(payload, "digest", required=False)
        self._refuse_if_busy(srv, experiment.name)
        archived = self._edit(
            srv,
            f"delete {experiment.name}'s config",
            lambda: config_files.delete_experiment_file(
                project_root=srv.project_root,
                project=srv.project,
                path=experiment_path,
                expected_digest=digest,
            ),
        )
        warnings = srv.reload_selection()
        self._reply_json(
            {
                "name": experiment.name,
                "path": _relative(experiment_path, srv.project_root),
                "archived": _relative(archived, srv.project_root),
                "warnings": [
                    *warnings,
                    f"the YAML is gone, but {experiment.name}'s persisted rows are not — "
                    "run `abk clean --orphaned-experiments` to prune them",
                ],
                "experiments": _boot_entries(srv),
            }
        )

    def _handle_reload(self, srv: _DashboardServer, body: bytes) -> None:
        """``POST /api/reload`` — re-read the project's configs from disk.

        The manual form of what every editor route does implicitly, and the
        M11 "reload configs affordance" follow-up: an experiment added, edited
        or removed OUTSIDE this cockpit (an editor, a `git pull`, an `abk
        explore` Apply) reaches the page without a restart.
        """
        _reject_unknown_fields(_load_json(body) if body.strip() else {}, set())
        warnings = srv.reload_selection()
        reply = _selection_reply(srv)
        reply["warnings"] = warnings
        self._reply_json(reply)

    def _edit(self, srv: _DashboardServer, what: str, action: Callable[[], Any]) -> Any:
        """Run one editor mutation: serialized, and with honest failures.

        Two things every write route needs and none of them should spell twice.

        **The lock.** ``update_experiment_file`` checks the digest, then spends
        the whole two-level validation (25–180 ms measured) before its
        ``os.replace`` — a check-then-act wide enough that two tabs saving the
        same file both answered 200 with one edit surviving. Serializing the
        seam closes it; it is a process-local mutex, not the pipeline lock.

        **The message.** ``do_POST``'s ``OSError`` branch says "could not start
        a subprocess", which is true of DASH-4's routes and a lie here — a full
        disk or a read-only ``experiments/`` would be reported as a spawn
        failure on a route that never spawns.
        """
        with srv.editor_lock:
            try:
                return action()
            except OSError as exc:
                raise RuntimeError(
                    f"could not {what} under {srv.project_root}: " f"{type(exc).__name__}: {exc}"
                ) from exc

    def _refuse_editing_a_truncated_file(self, path: Path) -> None:
        """Refuse to write over a file the read route could only show in part.

        ``GET /api/experiment-source`` reports ``editable: false`` past
        :data:`_MAX_SOURCE_BYTES`, and the client hides Save — but the SERVER
        has to enforce it too, or a request that skips the client (or simply
        omits ``digest``, which is optional) replaces an 800 kB file with the
        512 kB prefix it was shown. The cap is read here rather than passed to
        ``config_files`` because it is a property of this transport, not of the
        file format.
        """
        try:
            size = path.stat().st_size
        except OSError:
            return  # the write itself will report it, with the right message
        if size > _MAX_SOURCE_BYTES:
            raise ValueError(
                f"{path.name} is {size} bytes, larger than the {_MAX_SOURCE_BYTES}-byte "
                "editing cap — this page could only show a prefix of it, and saving "
                "that back would drop the rest; edit it in your editor"
            )

    def _refuse_if_busy(self, srv: _DashboardServer, experiment: str) -> None:
        """Refuse to edit an experiment this cockpit is currently running.

        Not a lock — a lock is exactly what this server may not take. It is the
        narrow, honest check the launcher CAN make: it knows the jobs it spawned
        itself. Both directions matter and neither is theoretical, because both
        buttons are on the same row: a running ``abk run`` has already read the
        config it is executing (so a save would land in a file whose results
        were computed from the previous text, with nothing recording the
        difference), and a live ``abk explore`` will write its OWN merged
        document on Apply, discarding whatever was saved here.

        A job started from a terminal is invisible to this check — said plainly
        rather than papered over; the digest catches the explore half of that
        case after the fact, and refuses the save instead of losing it.
        """
        for kind in sorted(JOB_KINDS):
            job = srv.jobs.running_job_for(kind, experiment)
            if job is not None:
                raise ValueError(
                    f"{experiment} has a running '{kind}' job ({job.label}) — stop it "
                    "before editing the config it is using, or the two will disagree "
                    "about what ran"
                )

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


def _relative(path: Path, project_root: Path) -> str:
    """*path* as the root-relative posix string every reply speaks."""
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def _boot_entries(srv: _DashboardServer) -> list[dict[str, Any]]:
    """The served selection in the boot payload's own shape.

    The SAME builder ``_boot_payload`` bakes into the page, so a list refreshed
    after an edit and a list rendered at boot cannot describe an experiment
    differently — the m11 discipline that one row shape has one source.
    """
    return build_overview_boot_entries(
        srv.project_root, srv.served_experiments(), project=srv.project
    )


def _selection_reply(srv: _DashboardServer) -> dict[str, Any]:
    """``{experiments, generated_at, warnings}`` — the refreshable half of boot."""
    return {
        "experiments": _boot_entries(srv),
        "generated_at": _ms(now_utc_naive()),
        "warnings": [],
    }


def _write_reply(srv: _DashboardServer, written: config_files.ConfigWrite) -> dict[str, Any]:
    """The common save/create reply: what landed, plus the refreshed selection.

    The reload runs HERE, between the write and the reply, so a client never
    has to poll to find out whether the row it just edited still exists — and
    its warnings ride in the same envelope as the validator's, because to an
    operator "saved, but the page is stale" and "saved, with a warning" are the
    same kind of news.
    """
    reload_warnings = srv.reload_selection()
    return {
        "name": written.config.name,
        "path": _relative(written.path, srv.project_root),
        "archived": (
            None if written.archived is None else _relative(written.archived, srv.project_root)
        ),
        # The bytes THIS write produced — never a re-read (`ConfigWrite.digest`
        # says why): a re-read would hand this editor a token certifying some
        # other writer's text, and its next save would then pass the digest
        # check while clobbering that writer.
        "digest": written.digest,
        "renamed_from": written.renamed_from,
        "in_selection": srv.experiment_entry(written.config.name) is not None,
        "warnings": [*written.warnings, *reload_warnings],
        "experiments": _boot_entries(srv),
    }


def _load_metric_configs(project_root: Path, project: ProjectConfig) -> dict[str, MetricConfig]:
    """The project's metric configs, re-read after an edit (UI-1's reload).

    A thin indirection so the reload does not have to reach into
    ``config_files`` from inside the server class, and so a test can point it
    at a stub.
    """
    return config_files.load_metric_configs(project_root, project)


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
    selectors: Sequence[str] = (),
    excludes: Sequence[str] = (),
    echo: Callable[[str], None] = print,
) -> tuple[_DashboardServer, str]:
    """Construct (without running) the dashboard server; return ``(server, url)``.

    The bound port is known only after construction, so the page is rendered
    ONCE post-bind, exactly like ``build_explore_server`` — but no URLs are
    baked into the payload (see :func:`_boot_payload`).

    *metrics* and *manager* feed ``GET /experiment/<name>`` (DASH-5's Open
    button): the metric configs become the report's metric descriptions, and the
    manager — the SAME one *tables* wraps, used only under ``db_lock`` — lets the
    no-copy default snapshot the live cohort for the SRM chip's observed counts.
    Both are optional and both degrade in the open (:func:`_report_payload`).

    *selectors* / *excludes* are the ``--select``/``--exclude`` this cockpit was
    started with, and they exist for exactly one reason: UI-1's reload has to
    re-resolve the SAME selection the caller resolved, or an edit would silently
    widen the page to the whole project. Left empty (the tests' default, and
    ``abk dashboard`` with no selector) they mean "everything", which is
    ``select_experiments``' own default — so a reload of an unfiltered cockpit
    is faithful without the CLI having to spell it.

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
        server.selectors = tuple(selectors)
        server.excludes = tuple(excludes)
        if metrics is not None:
            server.metrics = metrics
        server.manager = manager
        if jobs is not None:
            server.jobs = jobs
        server.set_experiments(experiments)
        server.rebake()
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
    selectors: Sequence[str] = (),
    excludes: Sequence[str] = (),
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
        selectors=selectors,
        excludes=excludes,
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
