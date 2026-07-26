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
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import yaml
from synthetic_ab import (
    METRICS,
    REL,
    START,
    SyntheticWarehouse,
    build_session,
    experiment_payload,
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

    def __init__(
        self, tmp_path, method=T_TEST, metric="arpu", echo=None, run=True, cohort_copy=False
    ):
        from abkit.config import ExperimentConfig

        self.warehouse = SyntheticWarehouse()
        seed_cohort(self.warehouse)
        seed_all_events(self.warehouse)
        self.tables = InternalTablesManager(self.warehouse)
        document = experiment_payload("exp_srv", metric, method)
        if cohort_copy:
            # m8 WP4: the opt-in persisted-copy mode (the default is direct);
            # WP5's incremental engine requires the ab_added_filters hook
            document["assignment"]["cohort_copy"] = {"enabled": True}
            document["assignment"]["query"] += " WHERE 1 = 1 {{ ab_added_filters }}"
        self.experiment = ExperimentConfig.model_validate(document)
        if run:
            run_pipeline(self.warehouse, self.tables, self.experiment)
        self.session = build_session(self.warehouse, self.tables, self.experiment)
        self.engine = RecomputeEngine(self.session)
        self.echo_lines: list[str] = []

        experiments = tmp_path / "experiments"
        experiments.mkdir(parents=True, exist_ok=True)
        self.path = experiments / "exp_srv.yml"
        self.path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

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

    def test_reload_series_is_cohort_mode_invariant(self, tmp_path):
        """m8 WP4 cross-command parity: ``POST /reload`` answers identical
        series whether the factory-built backend reads the live assignment
        source (the direct default) or the persisted ``_ab_exposures`` copy
        (``cohort_copy.enabled``, populated by the run pipeline)."""
        knobs = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "14d"},
        }
        replies: dict[str, dict] = {}
        for mode, copy_enabled in (("direct", False), ("copy", True)):
            explore = Explore(tmp_path / mode, method=CUPED, cohort_copy=copy_enabled)
            try:
                status, reply = http(explore.endpoint("reload"), recompute_request(method=knobs))
                assert status == 200, reply
                reply.pop("request_id", None)
                replies[mode] = reply
            finally:
                explore.stop()
        assert replies["direct"] == replies["copy"]


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
                {
                    "metric": "arpu",
                    "method": {"name": "t-test", "params": {"test_type": "absolute"}},
                }
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


class TestLockDecoupling:
    """m10 WP4: the coarse ``request_lock`` became ``heavy_lock`` around
    ``/reload``, ``/validate`` and ``/apply`` ONLY, and ``/recompute`` runs
    lock-free with a post-compute staleness re-check.

    Every scenario forces the overlap with events and asserts the cheap reply
    lands **while the heavy request is still frozen** — a proof that cannot
    flake on timing, because a queued request could not have answered at all.
    """

    @staticmethod
    def _freeze(monkeypatch, name: str, result=None):
        """Freeze ``server.<name>`` mid-handler; returns ``(entered, release)``."""
        from abkit.tuning import server as server_mod

        entered, release = threading.Event(), threading.Event()
        real = getattr(server_mod, name)

        def frozen(*args, **kwargs):
            entered.set()
            assert release.wait(timeout=30), f"{name} was never released"
            return result if result is not None else real(*args, **kwargs)

        monkeypatch.setattr(server_mod, name, frozen)
        return entered, release

    @staticmethod
    def _fire(url: str, payload: dict) -> tuple[threading.Thread, list]:
        out: list = []
        thread = threading.Thread(target=lambda: out.append(http(url, payload)), daemon=True)
        thread.start()
        return thread, out

    def test_a_knob_turn_answers_while_a_reload_holds_the_heavy_lock(self, explore, monkeypatch):
        entered, release = self._freeze(monkeypatch, "_run_reload")
        thread, reload_out = self._fire(explore.endpoint("reload"), recompute_request())
        try:
            assert entered.wait(timeout=30)
            assert explore.server.heavy_lock.locked()  # the reload owns it
            status, reply = http(explore.endpoint("recompute"), recompute_request())
            assert status == 200, reply
            assert reply["pairs"][0]["points"]  # a real answer, not a stub
        finally:
            release.set()
            thread.join(timeout=60)
        assert reload_out and reload_out[0][0] == 200  # …and the reload still works

    def test_a_knob_turn_answers_while_auto_validate_holds_the_heavy_lock(
        self, explore, monkeypatch
    ):
        """The scenario that motivated the split: Auto mode runs hundreds of
        placebo splits inside the handler; a knob turn used to wait it out."""
        entered, release = self._freeze(
            monkeypatch, "_run_validate", result={"recommended": {}, "log": []}
        )
        thread, validate_out = self._fire(explore.endpoint("validate"), {"request_id": 1})
        try:
            assert entered.wait(timeout=30)
            assert explore.server.heavy_lock.locked()
            status, reply = http(explore.endpoint("recompute"), recompute_request(request_id=2))
            assert status == 200, reply
            assert reply["pairs"][0]["points"]
        finally:
            release.set()
            thread.join(timeout=60)
        assert validate_out and validate_out[0][0] == 200

    def test_the_heavy_paths_still_exclude_each_other(self, explore, monkeypatch):
        """``heavy_lock``'s job is unchanged: a ``/validate`` must NOT start
        while a ``/reload`` holds it (own DB managers, the ``_ab_tasks`` lock,
        the YAML seam)."""
        from abkit.tuning import server as server_mod

        reload_entered, reload_release = self._freeze(monkeypatch, "_run_reload")
        validate_entered = threading.Event()
        real_validate = server_mod._run_validate

        def watched_validate(*args, **kwargs):
            validate_entered.set()
            return real_validate(*args, **kwargs)

        monkeypatch.setattr(server_mod, "_run_validate", watched_validate)

        reload_thread, _ = self._fire(explore.endpoint("reload"), recompute_request())
        assert reload_entered.wait(timeout=30)
        validate_thread, validate_out = self._fire(explore.endpoint("validate"), {})
        try:
            # give the validate handler every chance to slip in
            validate_thread.join(timeout=1.0)
            assert not validate_entered.is_set(), "validate entered while reload held heavy_lock"
            assert validate_thread.is_alive()
        finally:
            reload_release.set()
            reload_thread.join(timeout=60)
            validate_thread.join(timeout=120)
        assert validate_entered.is_set()  # …and it ran once the lock was free
        assert validate_out and validate_out[0][0] == 200

    def test_apply_also_waits_for_the_heavy_lock(self, explore, monkeypatch):
        """The third pairing (review round 1 found it untested): the one-shot
        ``srv.applied is not None`` check AND the YAML archive/rewrite seam live
        inside ``heavy_lock``. Without it two Applies can both pass the check
        and both archive."""
        from abkit.tuning import server as server_mod

        reload_entered, reload_release = self._freeze(monkeypatch, "_run_reload")
        applied = threading.Event()
        real_apply = server_mod.apply_tuned_config

        def watched(*args, **kwargs):
            applied.set()
            return real_apply(*args, **kwargs)

        monkeypatch.setattr(server_mod, "apply_tuned_config", watched)

        reload_thread, _ = self._fire(explore.endpoint("reload"), recompute_request())
        assert reload_entered.wait(timeout=30)
        apply_thread, apply_out = self._fire(
            explore.endpoint("apply"), {**TestApply.APPLY, "confirm_uncalibrated": True}
        )
        try:
            apply_thread.join(timeout=1.0)
            assert not applied.is_set(), "Apply ran the write seam while /reload held heavy_lock"
            assert apply_thread.is_alive()
        finally:
            reload_release.set()
            reload_thread.join(timeout=60)
            apply_thread.join(timeout=60)
        assert applied.is_set()  # …and it ran once the lock was free
        assert apply_out and apply_out[0][0] == 200
        assert len(list((explore.path.parent / ".history").rglob("*.yml"))) == 1

    def test_a_recompute_superseded_mid_compute_409s_instead_of_replying(self, explore):
        """The post-compute re-check. Without it, the slow request would reply
        200 AFTER the newer one — overwriting the fresher answer in the rail."""

        class _SlowOnMarkerAlpha:
            """Engine proxy that freezes the marked request AFTER computing.

            Deliberately drops ``should_stop`` when delegating and blocks on the
            way OUT: the mid-compute cancellation poll therefore cannot fire and
            the 409 can only come from the handler's post-compute re-check —
            the guarantee this test exists for.
            """

            MARKER = 0.011

            def __init__(self, inner):
                self._inner = inner
                self.computing = threading.Event()
                self.release = threading.Event()

            def recompute(self, metric, knobs, should_stop=None):
                result = self._inner.recompute(metric, knobs)
                if knobs.alpha == self.MARKER:
                    self.computing.set()
                    assert self.release.wait(timeout=30)
                return result

        proxy = _SlowOnMarkerAlpha(explore.engine)
        explore.server.engine = proxy
        try:
            slow_thread, slow_out = self._fire(
                explore.endpoint("recompute"),
                recompute_request(alpha=_SlowOnMarkerAlpha.MARKER, request_id=100),
            )
            assert proxy.computing.wait(timeout=30)
            # a newer knob turn lands (and answers) while 100 is still computing
            status, fresh = http(explore.endpoint("recompute"), recompute_request(request_id=101))
            assert status == 200 and fresh["request_id"] == 101
            proxy.release.set()
            slow_thread.join(timeout=60)
            assert not slow_thread.is_alive()
        finally:
            explore.server.engine = explore.engine
        status, body = slow_out[0]
        assert status == 409, body
        assert body["stale"] is True and body["request_id"] == 100

    def test_a_superseded_recompute_stops_computing_instead_of_finishing(
        self, explore, monkeypatch
    ):
        """The lock did double duty: it queued computes AND cancelled the ones a
        newer knob turn outranked while they waited. Dropping it dropped the
        cancellation too — review round 1 measured a 6-turn drag going from
        0.80 s / 0.80 CPU-s to 3.40 s / 6.94 CPU-s because all six ran in full.
        The engine now polls the same staleness predicate between points.
        """
        from abkit.tuning.recompute import RecomputeEngine

        marker = 0.011
        real_point = RecomputeEngine._compute_point
        points: dict[float, int] = {}
        inside_first, keep_going = threading.Event(), threading.Event()

        def counting(self, *args, **kwargs):
            knobs = args[4]
            points[knobs.alpha] = points.get(knobs.alpha, 0) + 1
            if knobs.alpha == marker and points[knobs.alpha] == 1:
                inside_first.set()
                assert keep_going.wait(timeout=30)
            return real_point(self, *args, **kwargs)

        monkeypatch.setattr(RecomputeEngine, "_compute_point", counting)

        slow_thread, slow_out = self._fire(
            explore.endpoint("recompute"), recompute_request(alpha=marker, request_id=200)
        )
        assert inside_first.wait(timeout=30)
        status, fresh = http(explore.endpoint("recompute"), recompute_request(request_id=201))
        assert status == 200 and fresh["request_id"] == 201
        keep_going.set()
        slow_thread.join(timeout=60)
        assert not slow_thread.is_alive()

        assert slow_out[0][0] == 409
        # a full answer for this fixture is 4 cutoffs; the superseded request
        # stopped after the one point it was already inside
        assert points[marker] == 1
        assert points[0.05] == 4

    def test_a_request_superseded_while_waiting_for_a_slot_computes_nothing(self, explore):
        """Admission control (review round 2). Cancellation polls BETWEEN
        points, so it cannot help a one-cutoff series — a young experiment, or
        any coarse cadence — where six knob turns still cost six full computes.
        The bounded semaphore is where that request dies: superseded at the
        door, zero points computed, and simultaneous resample blocks bounded.
        """
        import threading as _threading

        from abkit.tuning.recompute import RecomputeEngine

        explore.server.compute_slots = _threading.BoundedSemaphore(1)  # pin the bound
        real_point = RecomputeEngine._compute_point
        points: dict[float, int] = {}
        holding, release = threading.Event(), threading.Event()

        def counting(self, *args, **kwargs):
            knobs = args[4]
            points[knobs.alpha] = points.get(knobs.alpha, 0) + 1
            if knobs.alpha == 0.011 and points[knobs.alpha] == 1:
                holding.set()
                assert release.wait(timeout=30)
            return real_point(self, *args, **kwargs)

        try:
            RecomputeEngine._compute_point = counting  # type: ignore[method-assign]
            holder, holder_out = self._fire(
                explore.endpoint("recompute"), recompute_request(alpha=0.011, request_id=300)
            )
            assert holding.wait(timeout=30)  # request 300 owns the only slot

            waiting, waiting_out = self._fire(
                explore.endpoint("recompute"), recompute_request(alpha=0.022, request_id=301)
            )
            # …301 is now queued at the door; 302 supersedes it before it starts
            newest, newest_out = self._fire(
                explore.endpoint("recompute"), recompute_request(alpha=0.033, request_id=302)
            )
            deadline = time.monotonic() + 30
            while explore.server.latest_request_id < 302 and time.monotonic() < deadline:
                time.sleep(0.01)  # the server must have SEEN 302 before anyone runs
            assert explore.server.latest_request_id == 302
            release.set()
            for thread in (holder, waiting, newest):
                thread.join(timeout=60)
                assert not thread.is_alive()
        finally:
            RecomputeEngine._compute_point = real_point  # type: ignore[method-assign]

        assert waiting_out[0][0] == 409 and waiting_out[0][1]["stale"] is True
        assert 0.022 not in points, "a request superseded at the door must compute nothing"
        assert newest_out[0][0] == 200 and points[0.033] == 4
        assert holder_out[0][0] == 409  # superseded mid-compute, as before

    def test_lock_free_recomputes_keep_the_cache_consistent_under_a_reload(self, explore):
        """A REAL ``/reload`` racing 20 Tier-S bootstrap knob turns over the same
        cutoffs: every reply is a result or a clean stale-409 (never a 400/500),
        and the cache's three fields still agree afterwards."""
        from abkit.tuning.session import loaded_value_count

        boot = {"name": "bootstrap", "params": {"test_type": "relative", "n_samples": 40}}
        replies: list[tuple[int, object]] = []
        reload_out: list[tuple[int, object]] = []

        threads = [
            threading.Thread(
                target=lambda: reload_out.append(
                    http(explore.endpoint("reload"), recompute_request())
                )
            )
        ]
        threads += [
            threading.Thread(
                target=lambda: replies.append(
                    http(explore.endpoint("recompute"), recompute_request(method=boot))
                )
            )
            for _ in range(20)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert not any(t.is_alive() for t in threads)

        assert reload_out and reload_out[0][0] == 200
        assert len(replies) == 20
        for status, body in replies:
            assert status in (200, 409), body
            if status == 200:
                assert body["pairs"][0]["points"]
            else:
                assert body["stale"] is True

        session = explore.session
        assert set(session.cache) == set(session.cache_lookback)
        assert session.cached_value_count() == sum(
            loaded_value_count(entry) for entry in session.cache.values()
        )


def test_every_cancellable_recompute_catches_the_cancellation():
    """m10 WP4 review round 2: ``should_stop`` is a two-part contract.

    Pass the predicate without catching ``RecomputeSuperseded`` and a
    cancellation becomes a user-facing error — ``/reload``'s call site sits
    under ``except Exception -> 400``, so the next caller that copies the
    pattern renders "reload failed: …" in the client's status bar. One call
    site is a convention; an AST walk is a contract (the m10 WP1 lesson).
    """
    import ast
    from pathlib import Path

    package = Path(__file__).resolve().parents[2] / "abkit"
    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not any(kw.arg == "should_stop" for kw in node.keywords):
                continue
            guarded, walker = False, parents.get(node)
            while walker is not None:
                if isinstance(walker, ast.Try) and any(
                    isinstance(handler.type, ast.Name)
                    and handler.type.id == "RecomputeSuperseded"
                    or isinstance(handler.type, ast.Attribute)
                    and handler.type.attr == "RecomputeSuperseded"
                    for handler in walker.handlers
                ):
                    guarded = True
                    break
                walker = parents.get(walker)
            if not guarded:
                offenders.append(f"{path.relative_to(package.parent)}:{node.lineno}")
    assert not offenders, (
        "a recompute given should_stop can raise RecomputeSuperseded — catch it "
        "and reply 409, never let it fall into a generic error handler:\n  "
        + "\n  ".join(offenders)
    )


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


class TestBootstrapMemoThroughTheServer:
    """m10 WP5 end to end: the memo must survive the real handler, and a
    ``/reload`` that changes the data must be visible in the very next answer.

    The engine-level gates live in ``tests/tuning/test_recompute.py``; these two
    run through HTTP because that is where ``/recompute`` and ``/reload`` are
    genuinely concurrent (m10 WP4) and where a stale hit would reach a user.
    """

    METHOD = {"name": "bootstrap", "params": {"test_type": "relative", "n_samples": 200}}

    @staticmethod
    def _numbers(reply):
        return [
            (p["end_ts"], p["effect"], p["left_bound"], p["right_bound"], p["tier"])
            for p in reply["pairs"][0]["points"]
        ]

    def _grow_the_warehouse(self, explore):
        """Make a re-render legitimately differ: more revenue for the treatment."""
        for unit, variant, _ in explore.warehouse.cohort:
            if variant != "treatment":
                continue
            for day in range(4):
                explore.warehouse.events["user_revenue"].append(
                    (unit, variant, START + timedelta(days=day, hours=13), {"gross_usd": 5.0})
                )

    def test_a_reload_that_changes_the_data_is_visible_in_the_next_recompute(self, tmp_path):
        explore = Explore(tmp_path, method=self.METHOD)
        try:
            request = recompute_request(method=self.METHOD, alpha=0.05)
            status, first = http(explore.endpoint("recompute"), request)
            assert status == 200
            assert all(p["tier"] == "exact" for p in first["pairs"][0]["points"])

            self._grow_the_warehouse(explore)
            status, reloaded = http(explore.endpoint("reload"), request)
            assert status == 200
            assert self._numbers(reloaded) != self._numbers(
                first
            ), "the fixture must actually move the numbers or the gate is vacuous"

            status, after = http(explore.endpoint("recompute"), request)
            assert status == 200
            assert self._numbers(after) == self._numbers(reloaded)
        finally:
            explore.stop()

    def test_knob_turns_racing_a_reload_never_hang_and_never_answer_stale(self, tmp_path):
        """Eight concurrent knob turns over a reload that moves every number.

        Anything that answers AFTER the reload installed its cutoffs must carry
        the new numbers; the memo is keyed to the cache generation, so a request
        that read the pre-reload entry can only insert an unreachable entry.
        """
        explore = Explore(tmp_path, method=self.METHOD)
        try:
            alphas = [0.01, 0.02, 0.03, 0.04, 0.06, 0.07, 0.08, 0.09]
            replies: list = []
            errors: list = []

            def turn(alpha: float, request_id: int) -> None:
                try:
                    for _ in range(3):
                        replies.append(
                            http(
                                explore.endpoint("recompute"),
                                recompute_request(
                                    method=self.METHOD, alpha=alpha, request_id=request_id
                                ),
                            )
                        )
                except BaseException as exc:  # pragma: no cover - the failure path
                    errors.append(exc)

            threads = [
                threading.Thread(target=turn, args=(alpha, 1000 + i), daemon=True)
                for i, alpha in enumerate(alphas)
            ]
            for thread in threads:
                thread.start()
            self._grow_the_warehouse(explore)
            status, reloaded = http(
                explore.endpoint("reload"),
                recompute_request(method=self.METHOD, alpha=0.05, request_id=2000),
            )
            for thread in threads:
                thread.join(timeout=120)
            assert not any(thread.is_alive() for thread in threads), "a knob turn hung"
            assert not errors, errors
            assert status == 200, reloaded
            assert {code for code, _ in replies} <= {200, 409}

            # after the dust settles every alpha answers off the RELOADED data
            for alpha in alphas:
                status, reply = http(
                    explore.endpoint("recompute"),
                    recompute_request(method=self.METHOD, alpha=alpha, request_id=3000),
                )
                assert status == 200
                fresh = {
                    p["end_ts"]: (p["effect"], p["value_2"]) for p in reply["pairs"][0]["points"]
                }
                for point in reloaded["pairs"][0]["points"]:
                    assert fresh[point["end_ts"]] == (
                        point["effect"],
                        point["value_2"],
                    ), f"alpha={alpha} answered pre-reload numbers"
        finally:
            explore.stop()
