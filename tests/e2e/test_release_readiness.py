"""The M6 exit-gate release-readiness e2e (m6 plan §WP10).

Proves the whole first-release user journey end-to-end, offline and
byte-reproducibly — the deterministic complement to the CI ``install-smoke``
job (which installs the *built wheel* into a fresh venv across the Python
matrix; WP9). Together they are the ``pip install ab-analysis-kit`` DoD:

* ``install-smoke`` proves the wheel resolves the ``abk`` console script and
  reads its packaged assets via ``importlib.resources`` once installed clean;
* this e2e proves the journey the seed dataset makes possible — which needs a
  warehouse backend (the in-memory ``SeedMirrorWarehouse``, not shippable) and
  so runs against the checkout via ``CliRunner`` like the other e2e gates.

Journey: ``abk --version`` (the real, non-placeholder release version) → ``abk
init`` → ``abk run --select <example>`` yields a real ``_ab_results`` row →
``abk run --report`` bakes a self-contained, zero-network readout → ``abk
init-claude -d <tmp>`` materializes the managed ``CLAUDE.md`` block + the 9
operator rules + the 7 skills → the committed report/explore bundles are
self-contained. Fully offline (no wheel build, no network); no Docker. The
*wheel packaging* DoD — that a built wheel ships every bundle + ``init-claude``
asset and resolves in a clean venv — is owned authoritatively by CI's dedicated
``lint`` wheel-namelist gate and the ``install-smoke`` job (across the Python
matrix); this e2e is the deterministic behavioral complement.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from test_first_report import PLACEHOLDERS, _baked_payload
from test_first_run import SeedMirrorWarehouse

import abkit
import abkit.config.profile as profile_mod
from abkit.cli.main import cli

runner = CliRunner()

# The shipped operator-rule and skill sets (kept in lockstep with
# tests/cli/test_init_claude.py and the CI install-smoke job).
RULE_COUNT = 9
SKILL_COUNT = 7


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


class TestReleaseVersion:
    def test_cli_reports_the_release_version(self):
        """``abk --version`` mirrors ``__version__`` and is a real release, not
        the reserved ``0.0.1.dev0`` placeholder (PyPI rejects a dev re-upload)."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0, result.output
        assert abkit.__version__ in result.output
        assert abkit.__version__ != "0.0.1.dev0"
        # a real, non-dev release version (setuptools/PEP 440: no dev segment)
        assert ".dev" not in abkit.__version__


class TestReleaseJourney:
    def test_scaffold_run_produces_real_results(self, scaffolded):
        """init → run --select lands persisted, verdict-bearing results."""
        result = runner.invoke(cli, ["run", "--select", "example_signup_test"])
        assert result.exit_code == 0, result.output

        rows = scaffolded._rows["_ab_results"]
        assert rows, "the run must persist real _ab_results rows"
        signup = [r for r in rows if r["metric"] == "example_signup_cr"]
        assert signup, "the main metric must have persisted rows"
        horizon = max(signup, key=lambda r: r["end_ts"])
        assert horizon["is_main_metric"] is True
        assert horizon["is_horizon"] is True
        assert horizon["srm_flag"] is False
        assert horizon["effect"] > 0  # treatment converts more by construction

    def test_report_is_self_contained_and_verdict_bearing(self, scaffolded):
        """run --report bakes an offline, zero-network readout with a verdict."""
        result = runner.invoke(cli, ["run", "--select", "example_signup_test", "--report"])
        assert result.exit_code == 0, result.output
        html = (Path("reports") / "example_signup_test.html").read_text(encoding="utf-8")

        for placeholder in PLACEHOLDERS:
            assert placeholder not in html, f"unconsumed placeholder {placeholder}"
        # zero network: the only tolerated absolute URI is the favicon SVG namespace
        stripped = html.replace("http://www.w3.org", "")
        assert "http://" not in stripped
        assert "https://" not in stripped

        payload = _baked_payload(html)
        assert payload["verdicts"], "a main-metric verdict must be present"
        assert payload["verdicts"][0]["verdict"] in ("WIN", "LOSE", "FLAT", "INCONCLUSIVE")

    def test_report_is_byte_reproducible(self, scaffolded):
        """Two identical runs bake byte-identical payloads modulo the stamp."""

        def bake() -> dict:
            assert (
                runner.invoke(cli, ["run", "--select", "example_signup_test", "--report"]).exit_code
                == 0
            )
            html = (Path("reports") / "example_signup_test.html").read_text(encoding="utf-8")
            return _baked_payload(html)

        first, second = bake(), bake()
        first.pop("generated_at")
        second.pop("generated_at")
        assert first == second


class TestInitClaudeMaterializes:
    def test_managed_block_rules_and_skills(self, tmp_path):
        """abk init-claude writes the managed block + 9 rules + 7 skills."""
        result = runner.invoke(cli, ["init-claude", "--target-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output

        claude_md = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "<!-- BEGIN ab-analysis-kit" in claude_md
        assert "<!-- END ab-analysis-kit -->" in claude_md

        rules = list((tmp_path / ".claude" / "rules" / "ab-analysis-kit").glob("*.md"))
        assert len(rules) == RULE_COUNT, f"expected {RULE_COUNT} rules, found {len(rules)}"
        skills = list((tmp_path / ".claude" / "skills").glob("*/SKILL.md"))
        assert len(skills) == SKILL_COUNT, f"expected {SKILL_COUNT} skills, found {len(skills)}"

    def test_init_claude_is_idempotent(self, tmp_path):
        """A re-run changes nothing (safe to re-scaffold)."""
        assert runner.invoke(cli, ["init-claude", "-d", str(tmp_path)]).exit_code == 0
        before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        assert runner.invoke(cli, ["init-claude", "-d", str(tmp_path)]).exit_code == 0
        after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
        assert before == after


class TestBundlesAreSelfContained:
    """The committed renderer bundles that the wheel ships must embed offline —
    no external host. (That the wheel *ships* them is the CI wheel-namelist
    gate's job; here we assert the shipped artifacts are self-contained.)"""

    def test_committed_bundles_reference_no_external_host(self):
        pkg_root = Path(abkit.__file__).resolve().parent
        for rel in ("reporting/assets/report.js", "tuning/assets/explore.js"):
            src = (pkg_root / rel).read_text(encoding="utf-8")
            # the only tolerated absolute URI is the SVG namespace in inline markup
            stripped = src.replace("http://www.w3.org", "")
            assert "http://" not in stripped, f"{rel} references an external host"
            assert "https://" not in stripped, f"{rel} references an external host"
