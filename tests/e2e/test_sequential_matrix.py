"""The M5 exit-gate e2e (m5-implementation-plan.md WP9): the sequential milestone tied
together through the ``abk`` CLI on the scaffolded seed experiment.

Proves end-to-end, over the in-memory seed mirror (no Docker):
  - a ``sequential: {enabled: true}`` experiment emits ``ci_kind='always_valid'`` rows on
    a plain ``abk run`` (WP3);
  - the toggle self-invalidates — flipping it on re-plans the series on a *bare* re-run,
    no ``--full-refresh`` (WP3b/B4);
  - ``abk validate`` renders the D8 always-valid peeking column beside the fixed one and
    writes the D9 composed-family sentinel (WP2/WP8);
  - ``abk plan`` sizes the experiment read-only (WP6).

The exact FPR/recovery numbers are pinned by the unit fixtures (test_scoring.py D8
headline, test_family_sweep.py); this gate proves the CLI wiring, not the statistics.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from abkit.database.internal_tables import InternalTablesManager
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()
EXP = "example_signup_test"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


def _enable_sequential():
    """Flip the scaffolded experiment to the always-valid sequential mode."""
    path = Path("experiments") / f"{EXP}.yml"
    text = path.read_text(encoding="utf-8")
    assert "sequential:" not in text
    path.write_text(text + "\nsequential:\n  enabled: true\n", encoding="utf-8")


def _rows(warehouse):
    return InternalTablesManager(warehouse).load_results(EXP)


def test_sequential_run_emits_always_valid_rows(project):
    _enable_sequential()
    assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
    rows = _rows(project)
    assert rows, "the run persisted no rows"
    # both declared comparisons (z-test + cuped-t-test) are sequential-eligible ⇒ every
    # informative row carries the always-valid CI vocabulary
    informative = [r for r in rows if not r.get("insufficient_data")]
    assert informative
    assert all(r["ci_kind"] == "always_valid" for r in informative)


def test_toggle_self_invalidates_on_a_bare_rerun(project):
    # 1) fixed run → fixed CIs
    assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
    assert all(r["ci_kind"] == "fixed" for r in _rows(project) if not r.get("insufficient_data"))

    # 2) enable sequential + a BARE re-run (no --full-refresh) must re-plan the series
    _enable_sequential()
    assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
    informative = [r for r in _rows(project) if not r.get("insufficient_data")]
    assert informative
    assert all(
        r["ci_kind"] == "always_valid" for r in informative
    ), "the sequential toggle silently no-op'd — WP3b self-invalidation regressed"


def test_validate_renders_the_sequential_column_and_family_sweep(project):
    _enable_sequential()
    assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
    # --family-sweep is the m7 WP6 opt-in (the D9 sweep no longer auto-runs) — this
    # invocation doubles as the CLI-level proof the flag reaches the runner
    result = runner.invoke(
        cli, ["validate", "--select", EXP, "--iterations", "300", "--family-sweep"]
    )
    assert result.exit_code == 0, result.output

    rows = InternalTablesManager(project).get_aa_runs(EXP)
    cells = [r for r in rows if r["metric"] != "__family__"]
    family = [r for r in rows if r["metric"] == "__family__"]

    # D8: at least one sequential-eligible cell carries the always-valid peeking column
    assert any(r.get("peeking_fpr_sequential") is not None for r in cells), "no D8 column"
    assert any(r.get("tau2") is not None for r in cells)

    # D9: the composed-family sentinel is written with FWER/FDR (identity under the null)
    assert len(family) == 1
    fam = json.loads(family[0]["details"])["family"]
    assert fam["n_metrics"] >= 2 and fam["fwer"] is not None
    assert fam["fwer"] == fam["fdr"]  # complete-null identity
    # WP-B (D8×D9): the composed peeking pair is persisted too (≥1 eligible cell above ⇒
    # the family lights the pair); the complete-null identity holds for both new families.
    assert fam["fwer_peeking"] is not None and fam["fwer_sequential"] is not None
    assert fam["fwer_peeking"] == fam["fdr_peeking"]
    assert fam["fwer_sequential"] == fam["fdr_sequential"]


def test_plan_sizes_the_sequential_experiment(project):
    _enable_sequential()
    assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert result.exit_code == 0, result.output
    assert "required" in result.output and "achievable MDE" in result.output
    assert "looks: 14 planned" in result.output
