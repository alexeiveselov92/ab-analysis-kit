"""WP6 tests: the explore server + html + payload (m3-implementation-plan.md WP6).

The ported ``test_tune_server.py`` shape: real HTTP against the threaded
server (never handler unit-fakes), a stub-free warehouse via the shared
synthetic harness. Pins the donor's interaction contract — token-gated POSTs,
GET-serves-the-page-on-any-path, terminal Apply with self-shutdown proven by
``thread.join``, 400-keeps-serving, the server-side stale-drop, the
calibration gate, the ``/reload`` run-log vs the silent ``/recompute``, the
501 ``/validate`` stub, body limits, and the numpy JSON fallback.
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

    def test_validate_is_the_reserved_501_stub(self, explore):
        status, detail = http(explore.endpoint("validate"), {})
        assert status == 501
        assert "abk validate" in detail and "M4" in detail

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


class TestServeExplore:
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
        assert "http://" not in page.replace("http://www.w3.org", "")  # no network
