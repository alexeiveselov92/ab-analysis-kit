"""``abk plan`` — the WP6 CLI surface (m5-implementation-plan.md WP6).

Runs over the ``abk init`` example against the in-memory seed mirror (the M2/M3/M4 e2e
harness): after a run persists baseline moments, ``plan`` sizes each comparison; without
a run it refuses (no baseline) or accepts a ``--baseline`` override; it is strictly
read-only (no lock, no writes); and it refuses ratio/bootstrap methods it cannot size.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.commands.plan import _plan_comparison
from abkit.cli.main import cli
from abkit.config.experiment_config import ExperimentConfig
from abkit.core.period_planner import generate_grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.stats import TwoTierAlphas
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()
EXP = "example_signup_test"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A scaffolded demo whose profile yields the in-memory seed mirror (no run yet)."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


@pytest.fixture
def ran(project):
    """After a run persists the stabilization series `plan` reads baseline moments from."""
    assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
    return project


# ── sizing after a run ───────────────────────────────────────────────────────────


def test_plan_sizes_each_comparison_after_run(ran):
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "plan · α raw=0.05" in out
    # fraction main metric is sized end-to-end
    assert "example_signup_cr" in out
    assert "required" in out and "achievable MDE" in out and "power@MDE" in out
    # CUPED sample metric is sized on raw variance and flagged
    assert "example_arpu" in out
    assert "sized on RAW variance" in out
    assert "SKIPPED" not in out  # both example methods are sizable


def test_plan_look_count_matches_generate_grid(ran):
    # resolve the example experiment to compute the expected grid length
    from pathlib import Path

    from abkit.config import select_experiments

    selected, _ = select_experiments(Path("."), (EXP,))
    _, exp = selected[0]
    looks = len(
        generate_grid(exp.start_date, exp.end_date, exp.cadence_segments(), tz=exp.timezone)
    )
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert result.exit_code == 0, result.output
    assert f"looks: {looks} planned" in result.output


def test_plan_metric_filter(ran):
    result = runner.invoke(cli, ["plan", "--select", EXP, "--metric", "example_signup_cr"])
    assert result.exit_code == 0, result.output
    assert "example_signup_cr" in result.output
    assert "example_arpu" not in result.output


def test_plan_unknown_metric_exits_nonzero(ran):
    result = runner.invoke(cli, ["plan", "--select", EXP, "--metric", "nope"])
    assert result.exit_code != 0
    assert "not a comparison" in result.output


# ── read-only ────────────────────────────────────────────────────────────────────


def test_plan_is_read_only(ran):
    tables = InternalTablesManager(ran)
    before = len(tables.load_results(EXP))
    # hold the pipeline run lock: plan must ignore it (it takes no lock) and still work
    assert tables.acquire_lock(EXP, "pipeline", "run")
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert result.exit_code == 0, result.output
    after = len(tables.load_results(EXP))
    assert after == before  # plan wrote nothing
    assert not tables.get_aa_runs(EXP)  # and no A/A rows


# ── refuse-if-no-baseline + override ─────────────────────────────────────────────


def test_plan_refuses_without_baseline(project):
    # no run ⇒ no persisted moments ⇒ both comparisons cannot be sized
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert result.exit_code == 0, result.output
    assert result.output.count("no baseline") >= 2


def test_plan_baseline_override_sizes_one(project):
    result = runner.invoke(
        cli,
        [
            "plan",
            "--select",
            EXP,
            "--mde",
            "0.05",
            "--baseline",
            "example_signup_cr:prop=0.1,n=10000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "--baseline override" in result.output  # the overridden metric is sized
    assert "required" in result.output
    assert "no baseline" in result.output  # the other (arpu) still cannot be sized


def test_plan_infeasible_target_renders_infinity_not_crash(project):
    # prop*(1+mde) = 0.92*1.10 > 1 is unachievable: the plan must report ∞, exit 0,
    # and never abort the experiment (review finding — the required-N solve used to raise)
    result = runner.invoke(
        cli,
        [
            "plan",
            "--select",
            EXP,
            "--mde",
            "0.10",
            "--baseline",
            "example_signup_cr:prop=0.92,n=50000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "plan failed" not in result.output
    assert "∞ (underpowered)" in result.output


def test_plan_grid_over_max_looks_fails_fast(project):
    # M5 exit-gate round-1 fix: plan bounds generate_grid by max_looks so a pathological
    # grid fails fast (like `abk run`) instead of OOM-enumerating in this read-only command.
    from pathlib import Path

    proj = Path("abkit_project.yml")
    proj.write_text(proj.read_text() + "\nlimits:\n  max_looks: 5\n", encoding="utf-8")
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert result.exit_code != 0
    assert "max_looks" in result.output


def test_plan_malformed_baseline_exits_nonzero(project):
    result = runner.invoke(cli, ["plan", "--select", EXP, "--baseline", "garbage"])
    assert result.exit_code != 0


def test_plan_bad_alpha_exits_nonzero(project):
    result = runner.invoke(cli, ["plan", "--select", EXP, "--alpha", "1.5"])
    assert result.exit_code != 0


# ── honest refusals: ratio / bootstrap (dispatch-level) ──────────────────────────


def _refuse_experiment() -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "name": "refuse_exp",
            "start_date": "2024-07-01",
            "end_date": "2024-07-14",
            "unit_key": "user_id",
            "assignment": {
                "query": "SELECT 1",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            },
            "comparisons": [
                {"metric": "cr", "is_main_metric": True, "method": {"name": "z-test"}},
                {"metric": "rev", "method": {"name": "ratio-delta"}},
                {"metric": "arpu", "method": {"name": "bootstrap"}},
            ],
        }
    )


def test_plan_multi_arm_warns_sizing_is_first_pair_only(capsys):
    from abkit.cli.commands.plan import _emit_plan, _plan_comparison
    from abkit.config.project_config import ProjectConfig
    from abkit.core.period_planner import generate_grid

    exp = ExperimentConfig.model_validate(
        {
            "name": "three_arm",
            "start_date": "2024-07-01",
            "end_date": "2024-07-14",
            "unit_key": "user_id",
            "assignment": {
                "query": "SELECT 1",
                "variants": ["control", "t1", "t2"],
                "expected_split": {"control": 0.34, "t1": 0.33, "t2": 0.33},
            },
            "comparisons": [{"metric": "cr", "is_main_metric": True, "method": {"name": "z-test"}}],
        }
    )
    project = ProjectConfig.model_validate({"name": "p", "default_profile": "dev"})
    alphas = TwoTierAlphas(alpha=0.05, groups_count=3, metrics_count=0, main=0.0167, secondary=None)
    plan = _plan_comparison(
        exp, exp.comparisons[0], alphas, 0.8, 0.05, {"prop": 0.1, "n": 10000}, tables=None
    )
    grid = generate_grid(exp.start_date, exp.end_date, exp.cadence_segments(), tz=exp.timezone)
    _emit_plan(exp, project, alphas, 0.8, len(grid), grid, 42, [plan])
    out = capsys.readouterr().out
    assert "3-arm experiment" in out
    assert "control vs t1 contrast only" in out


def test_plan_comparison_refuses_ratio_and_bootstrap_but_sizes_ztest():
    exp = _refuse_experiment()
    alphas = TwoTierAlphas(alpha=0.05, groups_count=2, metrics_count=2, main=0.05, secondary=0.025)
    by_metric = {c.metric: c for c in exp.comparisons}

    ztest = _plan_comparison(
        exp, by_metric["cr"], alphas, 0.8, 0.05, {"prop": 0.1, "n": 10000}, tables=None
    )
    assert ztest.refused is None
    assert ztest.result is not None and ztest.result.required_n is not None

    ratio = _plan_comparison(exp, by_metric["rev"], alphas, 0.8, 0.05, None, tables=None)
    assert ratio.refused is not None and "ratio" in ratio.refused

    boot = _plan_comparison(exp, by_metric["arpu"], alphas, 0.8, 0.05, None, tables=None)
    assert boot.refused is not None and "resampling" in boot.refused
