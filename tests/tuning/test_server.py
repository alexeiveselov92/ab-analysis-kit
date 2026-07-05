"""WP6 tests: the explore server + html + payload (m3-implementation-plan.md WP6).

The ported ``test_tune_server.py`` shape: real HTTP against the threaded
server (never handler unit-fakes), a stub-free warehouse via the shared
synthetic harness. Pins the donor's interaction contract — token-gated POSTs,
GET-serves-the-page-on-any-path, terminal Apply with self-shutdown proven by
``thread.join``, 400-keeps-serving, the server-side stale-drop, the
calibration gate, the ``/reload`` run-log vs the silent ``/recompute``, Auto
mode's ``/validate`` in-session chip flip (WP6), body limits, and the numpy
JSON fallback.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pytest
import yaml
from synthetic_ab import (
    METRICS,
    REL,
    SyntheticWarehouse,
    build_session,
    experiment_payload,
    make_experiment,
    persisted,
    run_pipeline,
    seed_all_events,
    seed_cohort,
)

from abkit.config.method_config import MethodConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.tuning import RecomputeEngine, build_explore_payload
from abkit.tuning.html import render_explore_html
from abkit.tuning.server import _json_default, build_explore_server, serve_explore

T_TEST = {"name": "t-test", "params": {"test_type": "relative"}}
CUPED = {"name": "cuped-t-test", "params": {"test_type": "relative", "covariate_lookback": "7d"}}


def http(url: str, payload: dict | None = None, raw: bytes | None = None):
    """One request; returns ``(status, parsed-or-text)`` without raising."""
    data = raw if raw is not None else (json.dumps(payload).encode() if payload else b"{}")
    request = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body.startswith("{") else body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body


def http_get(url: str):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read().decode()


class Explore:
    """One served explore session over the synthetic warehouse."""

    def __init__(self, tmp_path, method=T_TEST, metric="arpu", echo=None, run=True):
        self.warehouse = SyntheticWarehouse()
        seed_cohort(self.warehouse)
        seed_all_events(self.warehouse)
        self.tables = InternalTablesManager(self.warehouse)
        self.experiment = make_experiment("exp_srv", metric, method)
        if run:
            run_pipeline(self.warehouse, self.tables, self.experiment)
        self.session = build_session(self.warehouse, self.tables, self.experiment)
        self.engine = RecomputeEngine(self.session)
        self.echo_lines: list[str] = []

        experiments = tmp_path / "experiments"
        experiments.mkdir(exist_ok=True)
        self.path = experiments / "exp_srv.yml"
        self.path.write_text(
            yaml.safe_dump(experiment_payload("exp_srv", metric, method), sort_keys=False),
            encoding="utf-8",
        )

        payload = build_explore_payload(self.session, self.engine, {"experiment": "exp_srv"})
        self.server, self.url = build_explore_server(
            payload=payload,
            original_path=self.path,
            project_root=tmp_path,
            session=self.session,
            engine=self.engine,
            tables=self.tables,
            metrics_by_name=METRICS,
            manager_factory=lambda: self.warehouse,
            metric_sql_by_name={name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        )
        self.server.echo = echo or self.echo_lines.append
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    def endpoint(self, name: str) -> str:
        base = self.url.split("/?")[0]
        return f"{base}/{name}?token={self.server.token}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def explore(tmp_path):
    session = Explore(tmp_path)
    yield session
    if session.thread.is_alive():
        session.stop()


def recompute_request(method=T_TEST, alpha=0.05, request_id=None, metric="arpu"):
    body = {"metric": metric, "method": method, "alpha": alpha}
    if request_id is not None:
        body["request_id"] = request_id
    return body


class TestTransport:
    def test_get_serves_the_tokened_page_on_any_path(self, explore):
        status, page = http_get(explore.url)
        assert status == 200
        assert explore.server.token in page  # endpoint URLs baked post-bind
        assert "__ABK_EXPLORE__" in page
        assert 'id="abk-explore"' in page
        status, page_two = http_get(explore.url.split("/?")[0] + "/anything")
        assert status == 200 and page_two == page

    def test_bad_token_403_and_file_untouched(self, explore):
        before = explore.path.read_bytes()
        base = explore.url.split("/?")[0]
        status, detail = http(f"{base}/apply?token=wrong", {"comparisons": []})
        assert status == 403
        assert "bad token" in detail
        assert explore.path.read_bytes() == before

    def test_oversized_body_413(self, explore):
        status, detail = http(explore.endpoint("recompute"), raw=b"x" * 5_000_001)
        assert status == 413

    def test_unknown_endpoint_404(self, explore):
        status, _ = http(explore.endpoint("nope"), {})
        assert status == 404

    def test_json_default_numpy_and_datetime(self):
        assert _json_default(np.int64(5)) == 5
        assert _json_default(np.float64(0.5)) == 0.5
        assert _json_default(np.array([1, 2])) == [1, 2]
        assert _json_default(datetime(2024, 7, 2)) == 1719878400000
        with pytest.raises(TypeError):
            _json_default(object())


class TestRecompute:
    def test_unchanged_knobs_reproduce_persisted_numbers_over_http(self, explore):
        status, reply = http(explore.endpoint("recompute"), recompute_request(request_id=1))
        assert status == 200
        baseline = persisted(explore.tables, explore.experiment, "arpu")
        points = reply["pairs"][0]["points"]
        assert len(points) == 4
        for point in points:
            end_ts = datetime.fromtimestamp(point["end_ts"] / 1000.0, tz=timezone.utc).replace(
                tzinfo=None
            )
            row = baseline[("control", "treatment", end_ts)]
            assert point["effect"] == pytest.approx(row["effect"], rel=REL)
            assert point["left_bound"] == pytest.approx(row["left_bound"], rel=REL)
            assert point["pvalue"] == pytest.approx(row["pvalue"], rel=REL)
        assert reply["identity_changed"] is False
        assert reply["calibration"]["state"] == "uncalibrated"

    def test_repeatable_and_silent(self, explore):
        for request_id in (1, 2, 3):
            status, _ = http(
                explore.endpoint("recompute"), recompute_request(request_id=request_id)
            )
            assert status == 200
        assert explore.echo_lines == []  # /recompute never streams to the terminal
        assert explore.thread.is_alive()  # advisory: the server keeps serving

    def test_stale_request_id_409_and_fresh_still_answers(self, explore):
        status, _ = http(explore.endpoint("recompute"), recompute_request(request_id=5))
        assert status == 200
        status, reply = http(explore.endpoint("recompute"), recompute_request(request_id=3))
        assert status == 409
        assert reply["stale"] is True
        status, _ = http(explore.endpoint("recompute"), recompute_request(request_id=6))
        assert status == 200

    def test_bad_knobs_400_keeps_serving(self, explore):
        status, detail = http(
            explore.endpoint("recompute"),
            recompute_request(method={"name": "t-test", "params": {"test_type": "sideways"}}),
        )
        assert status == 400
        assert "recompute failed" in detail
        assert explore.thread.is_alive()
        status, _ = http(explore.endpoint("recompute"), recompute_request())
        assert status == 200


class TestReload:
    def test_reload_streams_run_log_and_enables_the_new_lookback(self, tmp_path):
        explore = Explore(tmp_path, method=CUPED)
        try:
            knobs = {
                "name": "cuped-t-test",
                "params": {"test_type": "relative", "covariate_lookback": "14d"},
            }
            # a 14d lookback over a 7d-rendered cache is Tier R: gaps only
            status, reply = http(explore.endpoint("recompute"), recompute_request(method=knobs))
            assert status == 200
            assert all(p["tier"] == "baseline" for p in reply["pairs"][0]["points"]) or not (
                reply["pairs"][0]["points"]
            )
            assert explore.echo_lines == []

            status, reply = http(explore.endpoint("reload"), recompute_request(method=knobs))
            assert status == 200
            exact = [p for p in reply["pairs"][0]["points"] if p["tier"] == "exact"]
            assert len(exact) == len(explore.session.cached_cutoffs("arpu"))
            assert any("RELOAD exp_srv/arpu" in line for line in explore.echo_lines)
            assert any("reloaded" in line for line in explore.echo_lines)

            # the refreshed cache now serves plain /recompute for those knobs
            explore.echo_lines.clear()
            status, reply = http(explore.endpoint("recompute"), recompute_request(method=knobs))
            assert status == 200
            assert [p["tier"] for p in reply["pairs"][0]["points"]].count("exact") == len(exact)
            assert explore.echo_lines == []
        finally:
            if explore.thread.is_alive():
                explore.stop()

    def test_reload_unavailable_without_a_manager_factory(self, tmp_path):
        explore = Explore(tmp_path)
        try:
            explore.server.manager_factory = None
            status, detail = http(explore.endpoint("reload"), recompute_request())
            assert status == 400
            assert "unavailable" in detail
        finally:
            explore.stop()


class TestAutoValidate:
    """WP6/D11: Auto mode runs a reduced server-side ``abk validate``, greens the
    live D3 chip in place (no restart), and answers with the recommended knob
    state per metric — streaming a run-log, taking the out-of-band lock."""

    def test_validate_runs_auto_and_greens_the_live_chip(self, explore):
        # before: the chip is uncalibrated (no _ab_aa_runs rows exist yet)
        status, reply = http(explore.endpoint("recompute"), recompute_request(request_id=1))
        assert status == 200
        assert reply["calibration"]["state"] == "uncalibrated"

        # Auto mode: the reduced server-side validate
        status, vreply = http(explore.endpoint("validate"), {"request_id": 2})
        assert status == 200
        assert vreply["request_id"] == 2
        rec = vreply["recommended"]["arpu"]
        assert rec["method"]["name"] == "t-test"
        assert rec["calibration"]["state"] == "calibrated"
        assert rec["calibration"]["fpr"] is not None
        assert rec["calibration"]["over_budget"] is False  # a clean placebo A/A
        assert any("VALIDATE exp_srv" in line for line in explore.echo_lines)  # streams a log

        # the LIVE chip is green now, WITHOUT a restart (D11: aa_rows mutated in place)
        status, reply = http(explore.endpoint("recompute"), recompute_request(request_id=3))
        assert status == 200
        assert reply["calibration"]["state"] == "calibrated"

    def test_validate_lock_is_taken_and_released_so_it_reruns(self, explore):
        status, _ = http(explore.endpoint("validate"), {"request_id": 1})
        assert status == 200
        # a leaked '(exp, pipeline, validate)' lock would block the second run
        status, _ = http(explore.endpoint("validate"), {"request_id": 2})
        assert status == 200

    def test_validate_honors_the_stale_drop(self, explore):
        status, _ = http(explore.endpoint("recompute"), recompute_request(request_id=10))
        assert status == 200
        status, reply = http(explore.endpoint("validate"), {"request_id": 4})
        assert status == 409
        assert reply["stale"] is True

    def test_validate_unavailable_without_a_manager_factory(self, tmp_path):
        explore = Explore(tmp_path)
        try:
            explore.server.manager_factory = None
            status, detail = http(explore.endpoint("validate"), {"request_id": 1})
            assert status == 400
            assert "unavailable" in detail
        finally:
            explore.stop()

    def test_validate_closes_the_manager_when_acquire_lock_raises(self, explore, monkeypatch):
        # a raising acquire_lock (transient DB error / `_ab_tasks` absent) must
        # still close the warehouse manager — no leaked connection in the
        # long-lived server (the manager's lifetime is under the outer finally).
        closed = []
        monkeypatch.setattr(explore.warehouse, "close", lambda: closed.append(True))

        def boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(
            "abkit.database.internal_tables.InternalTablesManager.acquire_lock", boom
        )
        status, detail = http(explore.endpoint("validate"), {"request_id": 1})
        assert status == 400
        assert "validate failed" in detail
        assert closed, "the manager was closed even though acquire_lock raised (no leak)"

    def test_validate_does_not_weaken_the_apply_gate(self, explore):
        # Auto populates rows but the Apply gate is unchanged (R19): an edit to a
        # DIFFERENT (uncalibrated) method still needs confirm_uncalibrated.
        status, _ = http(explore.endpoint("validate"), {"request_id": 1})
        assert status == 200
        edit = {
            "comparisons": [
                {"metric": "arpu", "method": {"name": "t-test", "params": {"test_type": "absolute"}}}
            ]
        }
        status, detail = http(explore.endpoint("apply"), edit)
        assert status == 409
        assert "confirm_uncalibrated" in detail


class TestApply:
    APPLY = {
        "comparisons": [
            {"metric": "arpu", "method": {"name": "t-test", "params": {"test_type": "absolute"}}}
        ]
    }

    def test_uncalibrated_apply_requires_confirmation(self, explore):
        before = explore.path.read_bytes()
        status, detail = http(explore.endpoint("apply"), self.APPLY)
        assert status == 409
        assert "abk validate" in detail  # the cost message, not a hard block
        assert explore.path.read_bytes() == before
        assert not (explore.path.parent / ".history").exists()
        assert explore.thread.is_alive()  # refusal keeps serving

    def test_confirmed_apply_writes_replies_and_shuts_down(self, explore):
        status, reply = http(
            explore.endpoint("apply"), {**self.APPLY, "confirm_uncalibrated": True}
        )
        assert status == 200
        assert reply["updated"] == ["arpu"]
        assert (explore.path.parent / ".history").exists()
        # the identity edit orphans the persisted series → the block + warning
        assert reply["orphaned"][0]["metric"] == "arpu"
        assert reply["orphaned"][0]["rows"] > 0
        assert "abk clean" in reply["orphan_warning"]
        # terminal: the serve loop exits so the CLI can print the epilogue
        explore.thread.join(timeout=5)
        assert not explore.thread.is_alive()
        assert explore.server.applied is not None
        assert explore.server.applied.updated == ("arpu",)
        saved = yaml.safe_load(explore.path.read_text(encoding="utf-8"))
        assert saved["comparisons"][0]["method"]["params"] == {"test_type": "absolute"}

    def test_calibrated_apply_skips_the_confirmation(self, explore):
        new_id = MethodConfig(name="t-test", params={"test_type": "absolute"}).method_config_id
        explore.session.aa_rows = [
            {
                "metric": "arpu",
                "method_config_id": new_id,
                "alpha": explore.session.series("arpu").configured_alpha,
                "status": "success",
                "fpr": 0.04,
                "created_at": datetime(2026, 7, 1),
            }
        ]
        status, reply = http(explore.endpoint("apply"), self.APPLY)
        assert status == 200
        explore.thread.join(timeout=5)

    def test_invalid_config_400_no_archive_keeps_serving(self, explore):
        before = explore.path.read_bytes()
        status, detail = http(
            explore.endpoint("apply"),
            {
                "comparisons": [
                    {"metric": "arpu", "method": {"name": "t-test", "params": {"power": 7}}}
                ],
                "confirm_uncalibrated": True,
            },
        )
        assert status == 400
        assert "invalid config" in detail
        assert explore.path.read_bytes() == before
        assert not (explore.path.parent / ".history").exists()
        assert explore.thread.is_alive()
        status, _ = http_get(explore.url)
        assert status == 200


class TestApplyGateClosure:
    """WP5/WP6 review-closure regressions: the D3 gate has no side doors."""

    def test_correction_only_apply_still_gates(self, explore):
        status, detail = http(explore.endpoint("apply"), {"correction": "none"})
        assert status == 409
        assert "abk validate" in detail
        assert explore.thread.is_alive()

    def test_role_flip_only_apply_still_gates(self, explore):
        status, detail = http(
            explore.endpoint("apply"),
            {"comparisons": [{"metric": "arpu", "is_guardrail": False}]},
        )
        assert status == 409
        assert "abk validate" in detail

    def test_role_flip_gates_at_the_prospective_alpha(self):
        """(milestone-review) A main-flip re-tiers the bonferroni budget for
        EVERY comparison — the gate must key its D3 lookups at the PROSPECTIVE
        effective alphas, not the pre-flip ones, or a fully calibrated
        experiment would Apply ungated into never-validated alphas (latent in
        M3 while ``_ab_aa_runs`` is empty; load-bearing from M4)."""
        from types import SimpleNamespace

        from synthetic_ab import METRICS as ALL_METRICS
        from synthetic_ab import PROJECT, experiment_payload

        from abkit.config.experiment_config import ExperimentConfig
        from abkit.tuning import load_session
        from abkit.tuning.config_writer import TunedComparison
        from abkit.tuning.server import _uncalibrated_keys

        # 2 comparisons, 2 arms → 1 pair; bonferroni: pre-flip both effective
        # alphas are 0.05 (main tier α/1; secondary α/(1·1)); flipping arpu to
        # non-main moves BOTH to α/(1·2) = 0.025.
        document = experiment_payload("exp_roles", "arpu", T_TEST, alpha=0.05)
        document["comparisons"].append({"metric": "conversion", "method": {"name": "z-test"}})
        experiment = ExperimentConfig.model_validate(document)
        warehouse = SyntheticWarehouse()
        tables = InternalTablesManager(warehouse)
        session = load_session(experiment, ALL_METRICS, PROJECT, tables, loader=None)
        # every comparison fully calibrated at its PRE-flip alpha
        session.aa_rows = [
            {
                "metric": name,
                "method_config_id": series.comparison.method.method_config_id,
                "status": "success",
                "fpr": 0.05,
                "alpha": 0.05,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
            for name, series in session.series_by_metric.items()
        ]
        srv = SimpleNamespace(session=session)
        flip = TunedComparison(
            metric="arpu", method_name=None, params=None, is_main_metric=False, is_guardrail=None
        )
        findings = _uncalibrated_keys(srv, [flip], None, None)
        # the prospective 0.025 keys have no calibration rows → must gate
        assert findings, "role flip passed ungated at the stale pre-flip alphas"
        assert all("α=0.025" in f or "0.025" in f for f in findings)

    def test_params_with_a_riding_name_key_still_gate(self, explore):
        status, detail = http(
            explore.endpoint("apply"),
            {
                "comparisons": [
                    {
                        "metric": "arpu",
                        "method": {
                            "name": "t-test",
                            "params": {"name": "t-test", "test_type": "absolute"},
                        },
                    }
                ]
            },
        )
        assert status == 409  # gated — never silently skipped past the gate

    def test_method_switch_without_params_is_refused_over_http(self, explore):
        status, detail = http(
            explore.endpoint("apply"),
            {
                "comparisons": [{"metric": "arpu", "method": {"name": "bootstrap"}}],
                "confirm_uncalibrated": True,
            },
        )
        assert status == 400
        assert "full param set" in detail

    def test_non_numeric_alpha_is_a_clean_400(self, explore):
        status, detail = http(
            explore.endpoint("apply"), {"alpha": "bogus", "confirm_uncalibrated": True}
        )
        assert status == 400
        assert "invalid apply request" in detail
        assert explore.thread.is_alive()

    def test_malformed_content_length_is_a_clean_400(self, explore):
        import http.client
        from urllib.parse import urlparse

        parsed = urlparse(explore.endpoint("recompute"))
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
        assert explore.thread.is_alive()

    def test_second_apply_after_success_is_refused(self, explore):
        status, _ = http(
            explore.endpoint("apply"), {**TestApply.APPLY, "confirm_uncalibrated": True}
        )
        assert status == 200
        # the server may already be down; a second Apply must never double-write
        try:
            status_two, detail = http(
                explore.endpoint("apply"), {**TestApply.APPLY, "confirm_uncalibrated": True}
            )
            assert status_two in (409, 400)
        except OSError:
            pass  # connection refused after shutdown — equally safe
        history = list((explore.path.parent / ".history").rglob("*.yml"))
        assert len(history) == 1  # exactly ONE archive: no racing double Apply

    def test_concurrent_recomputes_all_answer(self, explore):
        results: list[int] = []

        def worker(i: int) -> None:
            status, _ = http(explore.endpoint("recompute"), recompute_request())
            results.append(status)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        assert results == [200] * 5

    def test_reload_refused_on_a_degraded_session(self, explore):
        explore.session.cache.clear()
        explore.session.cache_lookback.clear()
        explore.session.cache_values = 0
        explore.session.cache_disabled_reason = "session cache over budget: suffstats-only"
        status, detail = http(explore.endpoint("reload"), recompute_request())
        assert status == 400
        assert "reload disabled" in detail
        assert explore.session.cache == {}  # no shadow cache grew back


class TestServeExplore:
    def test_ctrl_c_racing_the_post_apply_shutdown_keeps_applied(self, tmp_path, monkeypatch):
        """(milestone-review) A KeyboardInterrupt landing in the post-Apply
        self-shutdown window (~poll_interval) must not report a SUCCESSFUL
        Apply as 'cancelled — unchanged': the YAML is already rewritten and a
        series possibly orphaned — the epilogue must run."""
        from abkit.tuning import server as server_mod

        sentinel = object()

        def fake_serve_forever(self, poll_interval=0.5):
            del poll_interval
            self.applied = sentinel  # an Apply landed…
            raise KeyboardInterrupt  # …and Ctrl-C races the self-shutdown

        monkeypatch.setattr(server_mod._ExploreServer, "serve_forever", fake_serve_forever)
        applied = serve_explore(
            payload={"experiment": "exp_srv"},
            original_path=tmp_path / "exp_srv.yml",
            project_root=tmp_path,
            open_browser=False,
            echo=lambda _line: None,
        )
        assert applied is sentinel

    def test_serve_returns_applied_and_prints_url(self, tmp_path):
        explore = Explore(tmp_path)
        explore.stop()  # reuse the built harness; serve_explore runs its own server

        lines: list[str] = []

        def on_ready(url: str) -> None:
            def worker():
                http(
                    url.split("/?")[0] + f"/apply?token={url.split('token=')[1]}",
                    {**TestApply.APPLY, "confirm_uncalibrated": True},
                )

            threading.Thread(target=worker, daemon=True).start()

        payload = build_explore_payload(explore.session, explore.engine, {"experiment": "exp_srv"})
        applied = serve_explore(
            payload=payload,
            original_path=explore.path,
            project_root=tmp_path,
            session=explore.session,
            engine=explore.engine,
            tables=explore.tables,
            metrics_by_name=METRICS,
            open_browser=False,
            echo=lines.append,
            on_ready=on_ready,
        )
        assert applied is not None
        assert applied.updated == ("arpu",)
        assert any("Explore: http://127.0.0.1:" in line for line in lines)


class TestPayloadAndHtml:
    def test_payload_carries_surfaces_calibration_and_null_endpoints(self, explore):
        payload = build_explore_payload(
            explore.session, explore.engine, {"experiment": "exp_srv", "v": 1}
        )
        assert payload["experiment"] == "exp_srv"  # the report payload rides verbatim
        block = payload["explore"]
        assert block["default_metric"] == "arpu"
        surface = block["metrics"]["arpu"]
        assert surface["configured"]["method"] == "t-test"
        assert surface["calibration"]["state"] == "uncalibrated"
        assert all(isinstance(ts, int) for ts in surface["cache"]["cutoffs"])
        for slot in ("save_url", "recompute_url", "reload_url", "validate_url"):
            assert payload[slot] is None  # static preview until a server injects

    def test_empty_results_experiment_is_a_payload_not_a_crash(self, tmp_path):
        explore = Explore(tmp_path, run=False)  # configured, never run
        try:
            payload = build_explore_payload(
                explore.session, explore.engine, {"experiment": "exp_srv"}
            )
            assert payload["explore"]["metrics"]["arpu"]["cache"]["cutoffs"] == []
            status, reply = http(explore.endpoint("recompute"), recompute_request())
            assert status == 200
            assert reply["pairs"] == []  # no rows: an empty state, not a 500
        finally:
            explore.stop()

    def test_html_bake_is_selfcontained_and_escaped(self):
        payload = {
            "experiment": "exp </script><script>alert(1)</script>",
            "explore": {"note": "__EXPLORE_JS__ must not clobber"},
        }
        page = render_explore_html(payload)
        for token in ("__PAYLOAD__", "__FAVICON__", "__EXPERIMENT__"):
            assert token not in page
        assert "</script><script>alert(1)" not in page  # every < escaped
        assert "\\u003c" in page
        assert "window.__ABK_EXPLORE__" in page
        assert 'id="abk-explore"' in page
        # zero network, both schemes — an https:// webfont import would slip
        # past an http://-only scan (milestone-review finding)
        stripped = page.replace("http://www.w3.org", "")
        assert "http://" not in stripped
        assert "https://" not in stripped
