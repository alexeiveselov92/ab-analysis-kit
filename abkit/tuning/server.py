"""The explore localhost server (WP6) — the donor's interaction contract.

A pure-stdlib server bound to ``127.0.0.1`` with a one-shot token: GET serves
ONE pre-rendered page (unauthenticated, any path — the token gates only the
POSTs); ``POST /recompute`` answers knob changes from the in-memory session
(repeatable, advisory, lock-serialized, **stale-dropping** — a ``threading``
lock alone cannot cancel an in-flight bootstrap, so the handler drops
outdated ``request_id``s BEFORE and AFTER acquiring the compute lock);
``POST /reload`` executes the confirmed Tier-R actions (its OWN manager
inside the serialized handler — DB-API connections are not thread-safe) and
streams a run-log through ``server.echo`` (``/recompute`` stays silent — per-
knob terminal streaming is spam); ``POST /validate`` is the reserved M4 slot
(501); ``POST /apply`` is the only terminal action — it enforces the
calibration gate server-side (``confirm_uncalibrated`` required while the
knob state's D3 lookup is not green), writes through the WP5 seam, echoes the
``orphaned`` block for the CLI epilogue, and shuts the server down from a
spawned daemon thread. An invalid config returns 400 and KEEPS serving.

No pipeline lock is taken: explore writes only YAML, and every read is the
FINAL-deduped ``load_results`` path (seam map §6).
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
import webbrowser
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from abkit.config.method_config import MethodConfig
from abkit.core.period_planner import Cutoff
from abkit.tuning.config_writer import AppliedConfig, TunedComparison, apply_tuned_config
from abkit.tuning.html import render_explore_html
from abkit.tuning.payload import ENDPOINT_SLOTS, _ms
from abkit.tuning.recompute import (
    KnobState,
    RecomputeEngine,
    RecomputeResult,
    find_calibration,
    resolve_fpr_budget,
)
from abkit.tuning.session import ExploreSession

if TYPE_CHECKING:
    from abkit.config.metric_config import MetricConfig
    from abkit.database.internal_tables import InternalTablesManager
    from abkit.database.manager import BaseDatabaseManager

_MAX_BODY = 5_000_000  # generous cap on the posted knob/config payload
_MAX_DRAIN = 32_000_000  # how much of an oversized body to drain before the 413


@contextlib.contextmanager
def _quiet_stderr() -> Iterator[None]:
    """Silence OS-level stderr for the duration of the block.

    ``webbrowser.open`` shells out to ``xdg-open``, which prints a wall of
    "browser not found" lines on a headless / WSL box. The launch is
    best-effort (the URL is already printed), so swallow that noise.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        yield
        return
    saved = os.dup(2)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(devnull)


class _ExploreServer(ThreadingHTTPServer):
    """Localhost server holding the per-serve state the handler reads."""

    # Don't block interpreter exit on in-flight request threads (we stop after
    # a single successful apply anyway).
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(address, handler)
        self.token: str = ""
        self.html: str = ""
        self.original_path: Path = Path(".")
        self.project_root: Path = Path(".")
        self.applied: AppliedConfig | None = None
        # /reload's run-log sink; serve_explore swaps in the command's echo.
        self.echo: Callable[[str], None] = print
        # The in-memory state every answer comes from (None = a bare server).
        self.session: ExploreSession | None = None
        self.engine: RecomputeEngine | None = None
        # The Apply seam's orphan scan + validation context.
        self.tables: InternalTablesManager | None = None
        self.metrics_by_name: dict[str, MetricConfig] | None = None
        # Tier-R support: a manager per /reload call (DB-API connections are
        # not thread-safe; the session-load manager belongs to the CLI thread).
        self.manager_factory: Callable[[], BaseDatabaseManager] | None = None
        self.metric_sql_by_name: dict[str, str] | None = None
        # One compute at a time (two tabs / queued knob drags stay safe)…
        self.request_lock = threading.Lock()
        # …and the server-side stale-drop: outdated request ids never compute.
        self._id_lock = threading.Lock()
        self.latest_request_id: int = 0

    def check_stale(self, request_id: int | None) -> bool:
        """Record ``request_id`` and report whether it is already outdated."""
        if request_id is None:
            return False  # id-less requests carry no ordering semantics
        with self._id_lock:
            if request_id < self.latest_request_id:
                return True
            self.latest_request_id = request_id
            return False

    def is_stale(self, request_id: int | None) -> bool:
        """Re-check after acquiring the compute lock (a newer id may have
        arrived while this request waited on it)."""
        if request_id is None:
            return False
        with self._id_lock:
            return request_id < self.latest_request_id


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence default stderr logging
        return

    def _srv(self) -> _ExploreServer:
        return cast(_ExploreServer, self.server)

    def do_GET(self) -> None:
        body = self._srv().html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        from urllib.parse import parse_qs, urlparse

        srv = self._srv()
        parsed = urlparse(self.path)
        if parse_qs(parsed.query).get("token", [""])[0] != srv.token:
            self._reply_error(403, "bad token")
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            # a malformed header must be a clean 400, never a dead thread
            self._reply_error(400, "bad Content-Length header")
            return
        if length <= 0 or length > _MAX_BODY:
            # Drain a bounded amount before replying so the client can read
            # the 413 instead of hitting a broken pipe mid-upload.
            if 0 < length <= _MAX_DRAIN:
                with contextlib.suppress(OSError):
                    self.rfile.read(length)
            self._reply_error(413, "empty or too large")
            return
        body = self.rfile.read(length)
        if parsed.path == "/recompute":
            self._handle_recompute(srv, body)
        elif parsed.path == "/reload":
            self._handle_reload(srv, body)
        elif parsed.path == "/validate":
            self._reply_error(501, "Auto mode requires `abk validate` (M4)")
        elif parsed.path == "/apply":
            self._handle_apply(srv, body)
        else:
            self._reply_error(404, f"unknown endpoint: {parsed.path}")

    # -- transport helpers (donor-verbatim) -----------------------------------

    def _reply_json(self, payload: dict[str, Any], code: int = 200) -> None:
        resp = json.dumps(payload, default=_json_default).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def _reply_error(self, code: int, detail: str) -> None:
        """Error detail in the UTF-8 body, never the latin-1 status line.

        ``send_error`` writes the message into the HTTP status line, which is
        latin-1 only — a pydantic/stats exception carrying a unicode dash or
        ``α`` would crash the response with ``UnicodeEncodeError`` instead of
        returning a clean error. The page reads the body via ``r.text()``.
        """
        body = detail.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- endpoints -------------------------------------------------------------

    def _handle_recompute(self, srv: _ExploreServer, body: bytes) -> None:
        """Answer one knob state — silent (structured JSON only), repeatable."""
        if srv.engine is None:
            self._reply_error(400, "recompute is unavailable for this session")
            return
        try:
            request = json.loads(body.decode("utf-8"))
            metric, knobs, request_id = _parse_knob_request(request)
        except Exception as exc:
            self._reply_error(400, f"invalid recompute request: {exc}")
            return
        if srv.check_stale(request_id):
            self._reply_json({"stale": True, "request_id": request_id}, code=409)
            return
        with srv.request_lock:
            if srv.is_stale(request_id):
                self._reply_json({"stale": True, "request_id": request_id}, code=409)
                return
            try:
                result = srv.engine.recompute(metric, knobs)
            except Exception as exc:
                self._reply_error(400, f"recompute failed: {exc}")
                return
        self._reply_json(_result_json(result, request_id))

    def _handle_reload(self, srv: _ExploreServer, body: bytes) -> None:
        """The confirmed Tier-R action: re-render the cached cutoffs under the
        requested method (its own manager), then answer like /recompute —
        streaming the run-log through ``srv.echo`` (never per-knob spam)."""
        if (
            srv.session is None
            or srv.engine is None
            or srv.manager_factory is None
            or not srv.metric_sql_by_name
        ):
            self._reply_error(400, "reload is unavailable for this session")
            return
        try:
            request = json.loads(body.decode("utf-8"))
            metric, knobs, request_id = _parse_knob_request(request)
        except Exception as exc:
            self._reply_error(400, f"invalid reload request: {exc}")
            return
        if srv.check_stale(request_id):
            self._reply_json({"stale": True, "request_id": request_id}, code=409)
            return
        with srv.request_lock:
            if srv.is_stale(request_id):
                self._reply_json({"stale": True, "request_id": request_id}, code=409)
                return
            try:
                result = _run_reload(srv, metric, knobs)
            except Exception as exc:
                srv.echo(f"RELOAD {metric}: failed — {exc}")
                self._reply_error(400, f"reload failed: {exc}")
                return
        self._reply_json(_result_json(result, request_id))

    def _handle_apply(self, srv: _ExploreServer, body: bytes) -> None:
        """The only terminal action: gate → WP5 seam → reply → self-shutdown.

        Serialized under the request lock: two tabs' Applies must not race the
        archive/rewrite seam or the shared CLI-thread DB manager, and a second
        Apply after a successful one is refused (the server is already going
        down). Every request-shaped failure is a clean 400/409 — a handler
        thread must never die replyless on malformed input.
        """
        try:
            request = json.loads(body.decode("utf-8"))
            comparisons = _parse_tuned_comparisons(request)
            alpha = request.get("alpha")
            alpha = None if alpha is None else float(alpha)
            correction = request.get("correction")
            correction = None if correction is None else str(correction)
        except Exception as exc:
            self._reply_error(400, f"invalid apply request: {exc}")
            return

        with srv.request_lock:
            if srv.applied is not None:
                self._reply_error(409, "already applied — the server is shutting down")
                return

            # The calibration gate (D3, server-side half): while the applied
            # knob state has no green D3 lookup, Apply needs the explicit
            # confirm_uncalibrated — a visible cost, never a hard block.
            try:
                uncalibrated = _uncalibrated_keys(srv, comparisons, alpha, correction)
            except Exception as exc:
                self._reply_error(400, f"invalid apply request: {exc}")
                return
            if uncalibrated and not bool(request.get("confirm_uncalibrated")):
                self._reply_error(
                    409,
                    "these params have never passed `abk validate` — the real FPR is "
                    "unknown and the nominal α may understate it. Re-send with "
                    f"confirm_uncalibrated: true to apply anyway. ({'; '.join(uncalibrated)})",
                )
                return

            try:
                applied = apply_tuned_config(
                    original_path=srv.original_path,
                    project_root=srv.project_root,
                    comparisons=comparisons,
                    alpha=alpha,
                    correction=correction,
                    tables=srv.tables,
                    metrics_by_name=srv.metrics_by_name,
                )
            except Exception as exc:
                # Keep serving so the user can fix the knobs and retry.
                self._reply_error(400, f"invalid config: {exc}")
                return
            srv.applied = applied

        try:
            self._reply_json(
                {
                    "saved": str(applied.saved),
                    "archived": str(applied.archived),
                    "updated": list(applied.updated),
                    "preserved": list(applied.preserved),
                    "experiment_fields": list(applied.experiment_fields),
                    "orphaned": [asdict(o) for o in applied.orphaned],
                    "orphan_warning": applied.orphan_warning,
                }
            )
        finally:
            # The YAML is written: shut down EVEN IF the reply write failed
            # (a vanished client must not leave the server alive — Ctrl-C
            # would then report "unchanged" after a successful write).
            threading.Thread(target=srv.shutdown, daemon=True).start()


def _parse_knob_request(request: dict[str, Any]) -> tuple[str, KnobState, int | None]:
    metric = str(request["metric"])
    method = request.get("method") or {}
    knobs = KnobState(
        method_name=str(method.get("name", "")),
        params=dict(method.get("params") or {}),
        alpha=float(request.get("alpha", 0.05)),
    )
    raw_id = request.get("request_id")
    request_id = int(raw_id) if isinstance(raw_id, int) and not isinstance(raw_id, bool) else None
    return metric, knobs, request_id


def _parse_tuned_comparisons(request: dict[str, Any]) -> list[TunedComparison]:
    """``comparisons: [{metric, method: {name, params}, is_main_metric, …}]`` —
    one entry per DIRTY comparison (the donor's dirty-slot discipline)."""
    out: list[TunedComparison] = []
    for entry in request.get("comparisons") or []:
        if not isinstance(entry, dict):
            continue
        method = entry.get("method")
        # an ABSENT params key stays None — the writer's "a method switch must
        # carry the full param set" guard must see the absence, not a fake {}
        params = None
        if isinstance(method, dict) and "params" in method:
            params = dict(method.get("params") or {})
        out.append(
            TunedComparison(
                metric=str(entry.get("metric", "")),
                method_name=(
                    str(method["name"]) if isinstance(method, dict) and method.get("name") else None
                ),
                params=params,
                is_main_metric=entry.get("is_main_metric"),
                is_guardrail=entry.get("is_guardrail"),
            )
        )
    return out


def _uncalibrated_keys(
    srv: _ExploreServer,
    comparisons: list[TunedComparison],
    alpha: float | None,
    correction: str | None,
) -> list[str]:
    """The (metric, id, alpha) keys this Apply would run at that are NOT green.

    D3 keys calibration by the EFFECTIVE post-correction per-comparison alpha
    — an alpha or correction edit re-keys every comparison, and role flips
    move comparisons between the two Bonferroni tiers, so ALL of those gate
    conservatively (with ``_ab_aa_runs`` empty until M4, every Apply takes
    the confirm path — the mechanically testable M3 DoD). Without a session
    (a bare server) everything is uncalibrated.
    """
    session = srv.session
    if session is None:
        return ["no session — calibration unknown"]

    from abkit.pipeline.analyze import comparison_alpha, effective_alphas

    # The PROSPECTIVE experiment folds in alpha/correction edits AND the
    # posted role flips before resolving effective alphas: a role flip moves
    # comparisons across the two Bonferroni tiers and shifts the non-main
    # count, so gating at the OLD roles' alphas would under-gate exactly the
    # edit class D3 calls out (milestone-review finding — latent while
    # ``_ab_aa_runs`` is empty, load-bearing from M4 on).
    role_updates = {
        tuned.metric: tuned
        for tuned in comparisons
        if tuned.is_main_metric is not None or tuned.is_guardrail is not None
    }
    prospective_comparisons = []
    flipped_by_metric: dict[str, Any] = {}
    for comp in session.experiment.comparisons:
        tuned = role_updates.get(comp.metric)
        if tuned is not None:
            role_fields: dict[str, Any] = {}
            if tuned.is_main_metric is not None:
                role_fields["is_main_metric"] = bool(tuned.is_main_metric)
            if tuned.is_guardrail is not None:
                role_fields["is_guardrail"] = bool(tuned.is_guardrail)
            comp = comp.model_copy(update=role_fields)
        prospective_comparisons.append(comp)
        flipped_by_metric.setdefault(comp.metric, comp)  # explore serves the first
    updates: dict[str, Any] = {}
    if alpha is not None:
        updates["alpha"] = alpha
    if correction is not None:
        updates["correction"] = correction
    if role_updates:
        updates["comparisons"] = prospective_comparisons
    prospective = session.experiment.model_copy(update=updates) if updates else session.experiment
    try:
        alphas = effective_alphas(prospective, session.project)
    except Exception:
        alphas = None  # apply_tuned_config will produce the real error

    def _effective(metric: str, series: Any) -> float:
        if alphas is None:
            return series.configured_alpha
        return comparison_alpha(flipped_by_metric.get(metric, series.comparison), alphas)

    findings: list[str] = []

    def _check(metric: str, method_config_id: str, effective_alpha: float) -> None:
        budget = resolve_fpr_budget(session.project, effective_alpha)
        status = find_calibration(
            session.aa_rows, metric, method_config_id, effective_alpha, budget=budget
        )
        if status.state != "calibrated":
            findings.append(f"{metric}: {status.headline}")

    checked: set[str] = set()
    for tuned in comparisons:
        if tuned.params is None or tuned.metric not in session.series_by_metric:
            continue
        series = session.series_by_metric[tuned.metric]
        name = tuned.method_name or series.comparison.method.name
        # the writer strips a riding "name" key — the gate must key identically
        params = {k: v for k, v in dict(tuned.params).items() if k != "name"}
        try:
            new_id = MethodConfig(name=name, params=params).method_config_id
        except Exception:
            # unbindable params gate conservatively; the writer then rejects
            # them with the real error message
            findings.append(f"{tuned.metric}: method params not bindable — treated as uncalibrated")
            checked.add(tuned.metric)
            continue
        _check(tuned.metric, new_id, _effective(tuned.metric, series))
        checked.add(tuned.metric)

    rekeys_everything = (
        alpha is not None
        or correction is not None
        or any(
            tuned.is_main_metric is not None or tuned.is_guardrail is not None
            for tuned in comparisons
        )
    )
    if rekeys_everything:
        # experiment-level alpha/correction edits re-key EVERY comparison at
        # its prospective effective alpha; role flips move comparisons across
        # the two Bonferroni tiers — both gate conservatively (D3)
        for metric, series in session.series_by_metric.items():
            if metric not in checked:
                _check(
                    metric, series.comparison.method.method_config_id, _effective(metric, series)
                )
    return findings


def _run_reload(srv: _ExploreServer, metric: str, knobs: KnobState) -> RecomputeResult:
    """Re-render the metric's cached cutoffs under the requested method.

    Runs inside the request lock with its OWN manager (created and closed
    here). Cache entries — and the lookback they were rendered with — are
    replaced in place; the engine then answers from the refreshed session.
    """
    from abkit.compute.recompute_backend import RecomputeBackend

    session = cast(ExploreSession, srv.session)
    engine = cast(RecomputeEngine, srv.engine)
    factory = cast(Callable[[], Any], srv.manager_factory)
    sql_by_name = cast("dict[str, str]", srv.metric_sql_by_name)

    series = session.series(metric)
    if metric not in sql_by_name:
        raise ValueError(f"no metric SQL for '{metric}'")
    method_config = MethodConfig(name=knobs.method_name, params=dict(knobs.params))
    method_config.bind(alpha=knobs.alpha)  # validate BEFORE any warehouse work
    comparison = series.comparison.model_copy(update={"method": method_config})

    if session.cache_disabled_reason is not None:
        # a budget-degraded session must not grow a shadow cache the replies
        # would keep contradicting with the "suffstats-only" warning
        raise ValueError(f"reload disabled: {session.cache_disabled_reason}")

    cutoffs = session.cached_cutoffs(metric) or series.cutoffs[-1:]
    if not cutoffs:
        raise ValueError(f"metric '{metric}' has no computed cutoffs to reload")

    from abkit.tuning.session import loaded_value_count

    experiment = session.experiment.name
    srv.echo(
        f"RELOAD {experiment}/{metric}: {len(cutoffs)} cutoff(s) under "
        f"method '{knobs.method_name}' "
        f"(covariate_lookback={method_config.covariate_lookback!r})"
    )
    manager = factory()
    try:
        backend = RecomputeBackend(manager, session.experiment)
        for end_ts in cutoffs:
            loaded = backend.load_cutoff(
                comparison, series.metric, sql_by_name[metric], session.grid, Cutoff(end_ts=end_ts)
            )
            previous = session.cache.get((metric, end_ts))
            if previous is not None:
                session.cache_values -= loaded_value_count(previous)
            session.cache[(metric, end_ts)] = loaded
            session.cache_lookback[(metric, end_ts)] = method_config.covariate_lookback
            session.cache_values += loaded_value_count(loaded)
            srv.echo(f"LOAD  {experiment}/{metric}: cutoff {end_ts} reloaded")
    finally:
        manager.close()
    srv.echo(f"RELOAD {experiment}/{metric}: done — recomputing")
    return engine.recompute(metric, knobs)


def _json_default(o: Any) -> Any:
    """JSON fallback so a reply carrying numpy scalars/arrays serializes.

    Invoked by ``json.dumps`` only for otherwise-unserializable values, so the
    hot plain-dict replies never import numpy.
    """
    import numpy as np

    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, datetime):
        return _ms(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _result_json(result: RecomputeResult, request_id: int | None) -> dict[str, Any]:
    """One RecomputeResult as the wire reply (datetimes → ms-epoch ints)."""
    return {
        "request_id": request_id,
        "metric": result.metric,
        "method": result.method_name,
        "method_config_id": result.method_config_id,
        "alpha": result.alpha,
        "identity_changed": result.identity_changed,
        "warnings": list(result.warnings),
        "calibration": asdict(result.calibration),
        "pairs": [
            {
                "name_1": pair.name_1,
                "name_2": pair.name_2,
                "chips": {
                    **pair.chips,
                    "latest_end_ts": (
                        None
                        if pair.chips.get("latest_end_ts") is None
                        else _ms(pair.chips["latest_end_ts"])
                    ),
                },
                "points": [
                    {
                        "end_ts": _ms(point.end_ts),
                        "elapsed_days": point.elapsed_days,
                        "tier": point.tier,
                        "effect": point.effect,
                        "left_bound": point.left_bound,
                        "right_bound": point.right_bound,
                        "pvalue": point.pvalue,
                        "reject": point.reject,
                        "mde_1": point.mde_1,
                        "mde_2": point.mde_2,
                        "value_1": point.value_1,
                        "value_2": point.value_2,
                        "std_1": point.std_1,
                        "std_2": point.std_2,
                        "size_1": point.size_1,
                        "size_2": point.size_2,
                        "insufficient": point.insufficient,
                        "warnings": list(point.warnings),
                    }
                    for point in pair.points
                ],
            }
            for pair in result.pairs
        ],
    }


def build_explore_server(
    *,
    payload: dict[str, Any],
    original_path: Path,
    project_root: Path,
    session: ExploreSession | None = None,
    engine: RecomputeEngine | None = None,
    tables: InternalTablesManager | None = None,
    metrics_by_name: dict[str, MetricConfig] | None = None,
    manager_factory: Callable[[], BaseDatabaseManager] | None = None,
    metric_sql_by_name: dict[str, str] | None = None,
) -> tuple[_ExploreServer, str]:
    """Construct (without running) the explore server; return ``(server, url)``.

    The bound port is known only after construction, so the tokened endpoint
    URLs are injected into the payload here and the HTML is rendered ONCE,
    post-bind. Omitting ``manager_factory``/``metric_sql_by_name`` disables
    ``/reload`` (400); omitting ``engine`` disables ``/recompute`` too — the
    static-preview degradations, never crashes.
    """
    server = _ExploreServer(("127.0.0.1", 0), _Handler)
    token = secrets.token_urlsafe(16)
    port = int(server.server_address[1])
    server.token = token
    server.original_path = original_path
    server.project_root = project_root
    server.session = session
    server.engine = engine
    server.tables = tables
    server.metrics_by_name = metrics_by_name
    server.manager_factory = manager_factory
    server.metric_sql_by_name = metric_sql_by_name
    endpoint_urls = {
        "save_url": f"http://127.0.0.1:{port}/apply?token={token}",
        "recompute_url": f"http://127.0.0.1:{port}/recompute?token={token}",
        "reload_url": f"http://127.0.0.1:{port}/reload?token={token}",
        "validate_url": f"http://127.0.0.1:{port}/validate?token={token}",
    }
    assert set(endpoint_urls) == set(ENDPOINT_SLOTS)
    server.html = render_explore_html({**payload, **endpoint_urls})
    return server, f"http://127.0.0.1:{port}/?token={token}"


def serve_explore(
    *,
    payload: dict[str, Any],
    original_path: Path,
    project_root: Path,
    session: ExploreSession | None = None,
    engine: RecomputeEngine | None = None,
    tables: InternalTablesManager | None = None,
    metrics_by_name: dict[str, MetricConfig] | None = None,
    manager_factory: Callable[[], BaseDatabaseManager] | None = None,
    metric_sql_by_name: dict[str, str] | None = None,
    open_browser: bool = True,
    echo: Callable[[str], None] = print,
    on_ready: Callable[[str], None] | None = None,
) -> AppliedConfig | None:
    """Serve explore until the user applies (returns the result) or cancels (None)."""
    server, url = build_explore_server(
        payload=payload,
        original_path=original_path,
        project_root=project_root,
        session=session,
        engine=engine,
        tables=tables,
        metrics_by_name=metrics_by_name,
        manager_factory=manager_factory,
        metric_sql_by_name=metric_sql_by_name,
    )
    server.echo = echo
    if on_ready is not None:
        on_ready(url)
    echo(f"  Explore: {url}")
    echo(
        "  Open the URL above if no browser opens. Turn the knobs, then click "
        "Apply (Ctrl-C to cancel)."
    )
    if open_browser:
        try:
            with _quiet_stderr():
                webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        # Fall through to the applied-aware return: a Ctrl-C racing the
        # post-Apply self-shutdown window (~poll_interval) must not discard a
        # SUCCESSFUL Apply — the YAML is already rewritten and a series
        # possibly orphaned; reporting it as "cancelled — unchanged" would
        # suppress the orphan/re-run epilogue (milestone-review finding).
        pass
    finally:
        server.server_close()
    return server.applied
