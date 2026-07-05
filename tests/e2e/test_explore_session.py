"""The M3 cockpit half of the exit gate: a functioning gated explore session
over the freshly scaffolded project (m3-implementation-plan.md WP10).

The in-memory seed-mirror variant (no Docker): ``abk init`` → ``abk run`` →
build the real explore server from the scaffolded configs (the same plumbing
``abk explore`` runs) and prove over live HTTP that: GET serves the baked
page; unchanged knobs reproduce the persisted latest-cutoff numbers at
rel-1e-9; an alpha edit recomputes exactly on Tier E and α-inverts (tier
"approx") on a suffstats-only CUPED series; a stale request id is 409-dropped;
Apply is refused without ``confirm_uncalibrated`` against the empty
``_ab_aa_runs`` (the M3 DoD sentence, mechanically tested) and, confirmed,
rewrites the YAML + archives to ``.history`` + reports the orphaned series —
then the server shuts itself down.
"""

from __future__ import annotations

import json
import math
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from test_first_run import SeedMirrorWarehouse

import abkit.config.profile as profile_mod
from abkit.cli.main import cli

runner = CliRunner()


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    assert runner.invoke(cli, ["run", "--select", "example_signup_test"]).exit_code == 0
    return warehouse


def http(url: str, payload: dict):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST"
    )
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


class Served:
    """The explore server built exactly the way ``abk explore`` builds it."""

    def __init__(self, warehouse, with_cache: bool = True):
        from abkit.cli.commands._context import load_project_context
        from abkit.compute.recompute_backend import RecomputeBackend
        from abkit.config import select_experiments
        from abkit.database.internal_tables import InternalTablesManager
        from abkit.reporting import build_report_payload
        from abkit.tuning import (
            RecomputeEngine,
            backend_cutoff_loader,
            build_explore_payload,
            load_session,
        )
        from abkit.tuning.server import build_explore_server

        context = load_project_context(require_profiles=True)
        selected, _ = select_experiments(context.root, ("example_signup_test",))
        assert len(selected) == 1
        self.experiment_path, self.experiment = selected[0]
        self.root = context.root
        tables = InternalTablesManager(warehouse)
        configured = [c.metric for c in self.experiment.comparisons]
        metric_sql = {
            name: context.metrics_by_name[name].get_query_text(context.root)
            for name in configured
        }
        backend = RecomputeBackend(warehouse, self.experiment)
        self.session = load_session(
            self.experiment,
            context.metrics_by_name,
            context.project,
            tables,
            loader=backend_cutoff_loader(backend, metric_sql) if with_cache else None,
        )
        engine = RecomputeEngine(self.session)
        report_payload = build_report_payload(
            self.experiment,
            tables,
            project=context.project,
            metric_configs=context.metrics_by_name,
        )
        self.payload = build_explore_payload(self.session, engine, report_payload)
        self.server, self.url = build_explore_server(
            payload=self.payload,
            original_path=self.experiment_path,
            project_root=context.root,
            session=self.session,
            engine=engine,
            tables=tables,
            metrics_by_name=context.metrics_by_name,
            manager_factory=lambda: warehouse,
            metric_sql_by_name=metric_sql,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    def endpoint(self, name: str) -> str:
        return f"{self.url.split('/?')[0]}/{name}?token={self.server.token}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _latest_row(warehouse, metric: str) -> dict:
    rows = [r for r in warehouse._rows["_ab_results"] if r["metric"] == metric]
    return max(rows, key=lambda r: r["end_ts"])


def _knob_request(payload: dict, metric: str, alpha: float | None = None, request_id: int = 1):
    configured = payload["explore"]["metrics"][metric]["configured"]
    return {
        "metric": metric,
        "method": {"name": configured["method"], "params": configured["params"]},
        "alpha": alpha if alpha is not None else configured["alpha"],
        "request_id": request_id,
    }


class TestExploreSession:
    def test_page_recompute_alpha_and_stale_drop(self, scaffolded):
        served = Served(scaffolded)
        try:
            # GET serves the baked page on any path
            with urllib.request.urlopen(f"{served.url.split('/?')[0]}/anything", timeout=10) as r:
                page = r.read().decode()
            assert "window.__ABK_EXPLORE__" in page
            assert 'id="abk-explore"' in page
            assert "recompute?token=" in page  # live endpoints injected post-bind

            # unchanged knobs reproduce the persisted latest-cutoff numbers
            status, reply = http(
                served.endpoint("recompute"),
                _knob_request(served.payload, "example_signup_cr", request_id=1),
            )
            assert status == 200, reply
            persisted = _latest_row(scaffolded, "example_signup_cr")
            latest = max(reply["pairs"][0]["points"], key=lambda p: p["end_ts"])
            for reply_key, row_key in (
                ("effect", "effect"),
                ("pvalue", "pvalue"),
                ("left_bound", "left_bound"),
                ("right_bound", "right_bound"),
            ):
                assert math.isclose(
                    latest[reply_key], float(persisted[row_key]), rel_tol=1e-9
                ), reply_key
            assert reply["identity_changed"] is False
            assert reply["calibration"]["state"] == "uncalibrated"  # empty _ab_aa_runs

            # an alpha edit recomputes Tier E exactly: wider CI at α=0.01
            status, tightened = http(
                served.endpoint("recompute"),
                _knob_request(served.payload, "example_signup_cr", alpha=0.01, request_id=2),
            )
            assert status == 200, tightened
            latest_001 = max(tightened["pairs"][0]["points"], key=lambda p: p["end_ts"])
            assert latest_001["tier"] == "exact"
            width_005 = latest["right_bound"] - latest["left_bound"]
            width_001 = latest_001["right_bound"] - latest_001["left_bound"]
            assert width_001 > width_005

            # the server-side stale-drop: an outdated id never computes
            status, stale = http(
                served.endpoint("recompute"),
                _knob_request(served.payload, "example_signup_cr", request_id=1),
            )
            assert status == 409
            assert stale == {"stale": True, "request_id": 1}
        finally:
            served.stop()

    def test_alpha_inversion_on_a_suffstats_only_cuped_series(self, scaffolded):
        # loader=None: the cache cannot serve the CUPED family, so a changed
        # alpha rides the α-inversion path — tier "approx", MDE withheld
        served = Served(scaffolded, with_cache=False)
        try:
            status, reply = http(
                served.endpoint("recompute"),
                _knob_request(served.payload, "example_arpu", alpha=0.01, request_id=1),
            )
            assert status == 200, reply
            latest = max(reply["pairs"][0]["points"], key=lambda p: p["end_ts"])
            assert latest["tier"] == "approx"
            assert latest["mde_1"] is None  # the stored MDE was solved at the old alpha
            persisted = _latest_row(scaffolded, "example_arpu")
            stored_width = float(persisted["right_bound"]) - float(persisted["left_bound"])
            inverted_width = latest["right_bound"] - latest["left_bound"]
            assert inverted_width > stored_width  # z(0.995)/z(0.975)-scaled
            assert math.isclose(latest["pvalue"], float(persisted["pvalue"]), rel_tol=1e-12)
        finally:
            served.stop()

    def test_apply_gate_archive_orphan_and_shutdown(self, scaffolded):
        served = Served(scaffolded)
        original_yaml = served.experiment_path.read_text(encoding="utf-8")
        apply_body = {
            "comparisons": [
                {
                    "metric": "example_signup_cr",
                    "method": {"name": "z-test", "params": {"test_type": "absolute"}},
                }
            ]
        }

        # the M3 DoD sentence: with _ab_aa_runs empty, Apply without the
        # explicit confirmation is refused — a visible cost, never silent
        status, refusal = http(served.endpoint("apply"), apply_body)
        assert status == 409
        assert "confirm_uncalibrated" in refusal
        assert served.experiment_path.read_text(encoding="utf-8") == original_yaml

        # confirmed: YAML rewritten, prior config archived, the identity edit
        # reported as an orphaned persisted series (with the `abk clean` hint)
        status, applied = http(
            served.endpoint("apply"), {**apply_body, "confirm_uncalibrated": True}
        )
        assert status == 200, applied
        assert applied["updated"] == ["example_signup_cr"]
        assert "example_arpu" in applied["preserved"]

        rewritten = yaml.safe_load(served.experiment_path.read_text(encoding="utf-8"))
        signup = next(
            c for c in rewritten["comparisons"] if c["metric"] == "example_signup_cr"
        )
        assert signup["method"]["params"]["test_type"] == "absolute"

        archived = Path(applied["archived"])
        assert archived.is_file()
        assert ".history" in archived.parts
        assert archived.read_text(encoding="utf-8") == original_yaml

        assert len(applied["orphaned"]) == 1
        orphan = applied["orphaned"][0]
        assert orphan["metric"] == "example_signup_cr"
        assert orphan["rows"] == 14
        assert "abk clean" in applied["orphan_warning"]

        # the only terminal action: the server takes itself down
        served.thread.join(timeout=10)
        assert not served.thread.is_alive()
        served.server.server_close()
