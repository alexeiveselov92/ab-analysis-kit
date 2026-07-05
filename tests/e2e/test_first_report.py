"""The M3 report half of the exit gate: ``abk init && abk run --report``.

Extends the M2 first-run harness (the in-memory seed mirror — no Docker): a
fresh scaffolded project must reach a verdict-bearing, self-contained offline
readout. Asserts the baked document (placeholders consumed, zero network,
bundle global + §4 marker classes) AND the baked payload structurally —
verdict block, SRM block, calibration empty-state, per-point peeking flags
(``hz``/``ins``/``blk``), the ``look`` block — plus regeneration stability:
a re-run's payload is byte-stable modulo ``generated_at`` (m3 plan WP10).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_first_run import SeedMirrorWarehouse

import abkit.config.profile as profile_mod
from abkit.cli.main import cli

runner = CliRunner()

PLACEHOLDERS = ("__PAYLOAD__", "__REPORT_JS__", "__EXPERIMENT__", "__FAVICON__")
MARKERS = ("abk-prehorizon", "abk-insufficient", "abk-srm-fail")


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    from datetime import datetime

    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


def _baked_payload(html: str) -> dict:
    line = next(ln for ln in html.splitlines() if "window.__ABK_PAYLOAD__" in ln)
    baked = line.split("window.__ABK_PAYLOAD__ = ", 1)[1]
    assert baked.endswith(";</script>")
    return json.loads(baked[: -len(";</script>")])


def _run_report(select: str = "example_signup_test") -> dict:
    result = runner.invoke(cli, ["run", "--select", select, "--report"])
    assert result.exit_code == 0, result.output
    report = Path("reports") / f"{select}.html"
    assert report.is_file(), "the donor path convention: reports/<experiment>.html"
    return _baked_payload(report.read_text(encoding="utf-8"))


class TestFirstReport:
    def test_scaffold_to_verdict_bearing_selfcontained_readout(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", "example_signup_test", "--report"])
        assert result.exit_code == 0, result.output
        html = (Path("reports") / "example_signup_test.html").read_text(encoding="utf-8")

        # self-containment: placeholders consumed, bundle + mount present,
        # zero network (stricter than the donor — webfonts were dropped; the
        # one tolerated substring is the SVG-namespace URI in the favicon)
        for placeholder in PLACEHOLDERS:
            assert placeholder not in html
        assert "__ABK_REPORT__" in html
        assert 'id="abk-report"' in html
        stripped = html.replace("http://www.w3.org", "")
        assert "http://" not in stripped
        assert "https://" not in stripped

        # the §4 machine-checkable markers ship inside the inlined bundle
        for marker in MARKERS:
            assert marker in html

        payload = _baked_payload(html)

        # verdict block (WP1): a real verdict word with its rationale
        assert payload["verdicts"], "a main-metric verdict must be present"
        verdict = payload["verdicts"][0]
        assert verdict["verdict"] in ("WIN", "LOSE", "FLAT", "INCONCLUSIVE")
        assert verdict["rationale"]
        assert verdict["is_horizon"] is True

        # SRM block (§6 must-fix): healthy 50/50 seed, whole-cohort counts
        assert payload["srm"]["flag"] is False
        assert sum(payload["srm"]["observed"].values()) == 600

        # calibration empty-state (M3: always null until M4)
        assert payload["calibration"] is None

        # per-point peeking flags on the main metric's series
        signup = next(m for m in payload["metrics"] if m["name"] == "example_signup_cr")
        series = signup["pairs"][0]["series"]
        assert len(series) == 14
        for point in series:
            assert {"hz", "ins", "blk"} <= set(point)
        assert series[-1]["hz"] == 1, "the horizon row carries hz=1"
        assert all(p["ins"] == 0 for p in series), "healthy seed: nothing demoted"
        assert all(p["blk"] == 0 for p in series), "healthy seed: no SRM block"

        # the look counter substrate (§4): 14 informative daily cutoffs
        assert payload["look"] == {"n": 14, "planned": 14}

        # a baked report is static: every endpoint slot stays null
        assert all(v is None for v in payload["endpoints"].values())

    def test_rerun_regenerates_byte_stable_payload(self, scaffolded):
        first = _run_report()
        second = _run_report()  # plans 0 cutoffs, still re-emits (D8)
        first.pop("generated_at")
        second.pop("generated_at")
        assert first == second

    def test_report_never_fails_the_run(self, scaffolded, monkeypatch):
        """Best-effort emission: a builder crash yellow-skips; run exits 0."""
        import abkit.reporting as reporting_mod

        def boom(*args, **kwargs):
            raise RuntimeError("builder exploded")

        monkeypatch.setattr(reporting_mod, "build_report_payload", boom)
        result = runner.invoke(cli, ["run", "--select", "example_signup_test", "--report"])
        assert result.exit_code == 0, result.output
        assert re.search(r"report skipped", result.output, re.IGNORECASE), result.output
