"""``abk plan`` — the WP6 CLI surface (m5-implementation-plan.md WP6).

Runs over the ``abk init`` example against the in-memory seed mirror (the M2/M3/M4 e2e
harness): after a run persists baseline moments, ``plan`` sizes each comparison; without
a run it refuses (no baseline) or accepts a ``--baseline`` override; it is strictly
read-only (no lock, no writes); and it refuses ratio/bootstrap methods it cannot size.
"""

from __future__ import annotations

from datetime import datetime

import click
import pytest
from click.testing import CliRunner
from fake_db import FakeDatabaseManager, serve_assignment_pushdown

import abkit.config.profile as profile_mod
from abkit.cli.commands.plan import _plan_comparison
from abkit.cli.main import cli
from abkit.config.experiment_config import ExperimentConfig
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
    looks = len(exp.grid())
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
            "start_ts": "2024-07-01",
            "horizon_ts": "2024-07-15",
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

    exp = ExperimentConfig.model_validate(
        {
            "name": "three_arm",
            "start_ts": "2024-07-01",
            "horizon_ts": "2024-07-15",
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
    grid = exp.grid()
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


# ── WP-A: runtime + ASN ──────────────────────────────────────────────────────────


def test_plan_arrival_rate_renders_runtime_line(project):
    # --arrival-rate + --baseline sizes and times a comparison with no run/exposures at all
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
            "--arrival-rate",
            "2000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "runtime ≈" in result.output
    assert "units/day/arm" in result.output
    assert "→" in result.output and "control" in result.output  # the split label
    # the example is NOT sequential.enabled ⇒ ASN is honestly declared n/a
    assert "sequential ASN: n/a — fixed-horizon design" in result.output


def test_plan_no_arrival_data_skips_runtime(ran):
    # the seed-mirror exposures all share one timestamp ⇒ the rate is underivable; without
    # --arrival-rate runtime must be SKIPPED with a reason, never guessed.
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert result.exit_code == 0, result.output
    assert "runtime: n/a" in result.output


def test_plan_bad_arrival_rate_exits_nonzero(project):
    result = runner.invoke(cli, ["plan", "--select", EXP, "--arrival-rate", "0"])
    assert result.exit_code != 0


def test_plan_asn_renders_for_a_sequential_experiment(project):
    # flip the scaffolded experiment to sequential.enabled and plan with a rate + baseline:
    # the always-valid ASN line must render (early-stop N/arm + P(win by horizon)).
    from pathlib import Path

    exp_yml = Path("experiments/example_signup_test.yml")
    exp_yml.write_text(
        exp_yml.read_text() + "\nsequential:\n  enabled: true\n  scheme: always_valid\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        cli,
        [
            "plan",
            "--select",
            EXP,
            "--metric",
            "example_signup_cr",
            "--mde",
            "0.05",
            "--baseline",
            "example_signup_cr:prop=0.2,n=10000",
            "--arrival-rate",
            "4000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "sequential ASN ≈" in result.output
    assert "P(win by horizon)" in result.output
    assert "null ASN" in result.output


def test_build_runtime_asn_note_for_non_sequential_and_bootstrap():
    # unit-level: a non-sequential design and a resampling method each get an honest note
    from abkit.cli.commands.plan import _build_runtime
    from abkit.planning.sizing import BaselineMoments, SizingResult
    from abkit.stats import get_method_class

    moments = BaselineMoments("fraction", 0.2, 10000, 10000, None, "x")
    result = SizingResult(required_n=5000, achievable_mde=0.03, achieved_power=0.5)
    look_days = [float(d) for d in range(1, 15)]

    non_seq_exp = ExperimentConfig.model_validate(
        {
            "name": "e",
            "start_ts": "2024-07-01",
            "horizon_ts": "2024-07-15",
            "unit_key": "u",
            "assignment": {
                "query": "SELECT 1",
                "variants": ["control", "t"],
                "expected_split": {"control": 0.5, "t": 0.5},
            },
            "comparisons": [{"metric": "cr", "is_main_metric": True, "method": {"name": "z-test"}}],
        }
    )
    rt = _build_runtime(
        non_seq_exp,
        get_method_class("z-test"),
        result,
        moments,
        test_type="relative",
        alpha=0.05,
        target_mde=0.05,
        plan_ratio=1.0,
        rate_control=1000.0,
        rate_source="test",
        look_days=look_days,
        horizon_days=14.0,
    )
    assert rt.asn is None and rt.asn_note is not None and "fixed-horizon" in rt.asn_note
    assert rt.days_to_required_n == 5.0  # 5000 / 1000


def _seq_experiment(cohort_copy: bool = False) -> ExperimentConfig:
    assignment: dict = {
        "query": "SELECT 1",
        "variants": ["control", "t"],
        "expected_split": {"control": 0.5, "t": 0.5},
    }
    if cohort_copy:
        assignment["cohort_copy"] = {"enabled": True}
    return ExperimentConfig.model_validate(
        {
            "name": "e",
            "start_ts": "2024-07-01",
            "horizon_ts": "2024-07-15",
            "unit_key": "u",
            "assignment": assignment,
            "comparisons": [{"metric": "cr", "is_main_metric": True, "method": {"name": "z-test"}}],
            "sequential": {"enabled": True, "scheme": "always_valid"},
        }
    )


def test_build_runtime_flags_asn_below_required_and_labels_it():
    # underpowered / horizon-capped regime: horizon (28,000/arm) barely clears required-N
    # (25,580) at low sequential power ⇒ the horizon-capped ASN dips BELOW required-N. The
    # line must label it so it can't be misread as "sequential needs fewer samples".
    from abkit.cli.commands.plan import _build_runtime, _runtime_lines
    from abkit.planning.sizing import BaselineMoments, size_comparison
    from abkit.stats import get_method_class

    exp = _seq_experiment()
    m = BaselineMoments("fraction", 0.2, 10000, 10000, None, "x")
    result = size_comparison(
        m, test_type="relative", alpha=0.05, power=0.8, target_mde=0.05, plan_ratio=1.0
    )
    rt = _build_runtime(
        exp,
        get_method_class("z-test"),
        result,
        m,
        test_type="relative",
        alpha=0.05,
        target_mde=0.05,
        plan_ratio=1.0,
        rate_control=2000.0,
        rate_source="test",
        look_days=[float(d) for d in range(1, 15)],
        horizon_days=14.0,
    )
    assert rt.asn is not None and rt.asn.asn_n_h1 < result.required_n
    assert rt.asn_below_required is True
    line = " ".join(_runtime_lines(rt))
    assert "horizon-capped expected-stop, not a lower requirement" in line


def test_fmt_rate_keeps_a_sub_one_rate_visible():
    from abkit.cli.commands.plan import _fmt_rate

    assert _fmt_rate(0.33) == "0.33"  # never rounds a fractional daily rate to "0"
    assert _fmt_rate(2000.0) == "2,000"


def test_resolve_arrival_rate_distinguishes_empty_cohort_from_one_instant():
    """Copy mode (m8 WP4): the persisted-table derivation stays reachable, unchanged."""
    from abkit.cli.commands.plan import _resolve_arrival_rate

    exp = _seq_experiment(cohort_copy=True)

    class _Tables:
        def __init__(self, arrivals, count):
            self._arrivals, self._count = arrivals, count

        def exposures_table_exists(self):
            return True

        def get_arrival_rate(self, name, variants):
            return self._arrivals

        def count_exposures(self, name):
            return self._count

    # copy mode never touches the assignment source: manager/root/grid are unused
    def resolve(tables):
        return _resolve_arrival_rate(exp, None, tables, None, None, None)

    # empty cohort for THIS experiment (table exists, zero rows) ⇒ the empty-case message
    rate, reason = resolve(_Tables(None, 0))
    assert rate is None and "no exposures for this experiment yet" in reason
    # a one-instant window (rows exist, but max == min) ⇒ the ~one-instant message
    rate, reason = resolve(_Tables(None, 5))
    assert rate is None and "one instant" in reason
    # a real derived rate flows through untouched
    rate, reason = resolve(_Tables(({"control": 500.0, "t": 500.0}, 30.0), 30000))
    assert rate == 500.0 and "observed days" in reason


class _ScriptedSource(FakeDatabaseManager):
    """Serves the assignment probe/pushdown from scripted rows (direct mode)."""

    def __init__(self, raw: list[dict]):
        super().__init__()
        self._raw = raw

    def execute_query(self, query, params=None):
        flat = " ".join(query.split())
        if "_abk_probe" in flat or "_abk_raw" in flat:
            return serve_assignment_pushdown(self._project, flat, self._raw)
        return super().execute_query(query, params)


def test_resolve_arrival_rate_direct_mode_snapshots_the_live_source():
    """Direct mode (the m8 WP4 no-copy default): the rate comes from a fresh
    snapshot of the live assignment source — never from ``_ab_exposures`` —
    via the SAME core.exposure_counting arithmetic the copy-mode mixin uses."""
    from abkit.cli.commands.plan import _resolve_arrival_rate

    exp = _seq_experiment()  # cohort_copy defaults to disabled
    grid = exp.grid()

    def resolve(raw):
        # tables=None proves the persisted-table path is never touched
        return _resolve_arrival_rate(exp, None, None, _ScriptedSource(raw), None, grid)

    # two units/arm over exactly 2 observed days ⇒ 1 unit/day/arm, source-labeled
    rows = [
        {"u": f"u{i}", "variant": v, "exposure_ts": datetime(2024, 7, 1 + 2 * (i % 2), 0, 0)}
        for i, v in enumerate(["control", "control", "t", "t"])
    ]
    rate, reason = resolve(rows)
    assert rate == pytest.approx(1.0)
    assert "assignment source over 2.0 observed days" in reason

    # a one-instant cohort (backfill) ⇒ skipped with the ~one-instant message
    instant = [
        {"u": f"u{i}", "variant": v, "exposure_ts": datetime(2024, 7, 1, 8, 0)}
        for i, v in enumerate(["control", "t"])
    ]
    rate, reason = resolve(instant)
    assert rate is None and "one instant" in reason

    # a not-yet-launched source (no rows) politely skips, never fails the plan
    rate, reason = resolve([])
    assert rate is None and "assignment source returned no rows yet" in reason


# ── PLAN-1: CUPED sized on the persisted covariate correlation ───────────────────


class _RowTables:
    """The one `load_results` a plan's baseline lookup makes, with a chosen row."""

    def __init__(self, **overrides):
        self.row = {
            "name_1": "control",
            "name_2": "treatment",
            "insufficient_data": False,
            "value_1": 12.5,
            "std_1": 8.0,
            "size_1": 5000,
            "size_2": 5000,
            "end_ts": datetime(2024, 7, 15),
            "corr_coef_1": None,
        }
        self.row.update(overrides)

    def load_results(self, *a, **kw):
        return [self.row]


def _cuped_experiment() -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "name": "cuped_exp",
            "start_ts": "2024-07-01",
            "horizon_ts": "2024-07-15",
            "unit_key": "user_id",
            "assignment": {
                "query": "SELECT 1",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            },
            "comparisons": [
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {"name": "cuped-t-test", "params": {"covariate_lookback": "14d"}},
                },
                {"metric": "plain", "method": {"name": "t-test"}},
            ],
        }
    )


def _cuped_plan(corr, mde=0.05):
    exp = _cuped_experiment()
    alphas = TwoTierAlphas(alpha=0.05, groups_count=2, metrics_count=2, main=0.05, secondary=0.025)
    comparison = exp.comparisons[0]
    return _plan_comparison(
        exp, comparison, alphas, 0.8, mde, None, tables=_RowTables(corr_coef_1=corr)
    )


def test_plan_sizes_cuped_on_the_persisted_correlation():
    """The gap PLAN-1 closes: `corr_coef_1` has been persisted since 0.4.0."""
    from abkit.stats.power import get_cuped_ttest_sample_size

    with_rho = _cuped_plan(0.6)
    without = _cuped_plan(None)
    assert with_rho.baseline is not None and with_rho.baseline.usable_corr == 0.6
    assert with_rho.result is not None and without.result is not None
    assert with_rho.result.required_n == get_cuped_ttest_sample_size(
        12.5, 8.0, 0.6, 0.05, test_type="relative", alpha=0.05, power=0.8, ratio=1.0
    )
    assert with_rho.result.required_n < without.result.required_n
    assert any("CUPED-deflated" in n and "0.6" in n for n in with_rho.notes)


def test_plan_flags_a_missing_correlation_as_an_upper_bound():
    plan = _cuped_plan(None)
    assert any("sized on RAW variance" in n and "before 0.4.0" in n for n in plan.notes)


def test_plan_refuses_a_degenerate_persisted_correlation():
    """The shape the scaffolded example actually persists: a covariate collinear with
    the metric rounds ρ to a hair under 1, which an ``abs(rho) >= 1`` gate lets
    through and which sizes the experiment to nothing."""
    degenerate = _cuped_plan(1.0 - 1e-16)
    raw = _cuped_plan(None)
    assert degenerate.result is not None and raw.result is not None
    assert degenerate.result.required_n == raw.result.required_n
    assert any("no usable residual variance" in n for n in degenerate.notes)


def test_plan_reports_a_zero_correlation_as_a_measurement():
    plan = _cuped_plan(0.0)
    assert any("ρ = 0" in n and "reduces no variance" in n for n in plan.notes)


def test_plan_baseline_corr_override_deflates(project):
    result = runner.invoke(
        cli,
        [
            "plan",
            "--select",
            EXP,
            "--mde",
            "0.05",
            "--baseline",
            "example_arpu:mean=60,std=40,n=5000,corr=0.7",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "CUPED-deflated" in result.output and "0.7" in result.output


def test_plan_baseline_corr_on_a_non_covariate_method_is_refused():
    """Deflating a method that will not run CUPED under-states required-N for an
    experiment that will never see the reduction — refuse where it was typed."""
    exp = _cuped_experiment()
    alphas = TwoTierAlphas(alpha=0.05, groups_count=2, metrics_count=2, main=0.05, secondary=0.025)
    plain = exp.comparisons[1]
    with pytest.raises(click.BadParameter, match="cuped"):
        _plan_comparison(
            exp,
            plain,
            alphas,
            0.8,
            0.05,
            {"mean": 12.5, "std": 8.0, "n": 5000, "corr": 0.6},
            tables=None,
        )


def test_scaffolded_example_states_why_it_falls_back_to_raw(ran):
    """The example project's synthetic covariate IS its metric (both are linear in the
    same generator), so the honest line is the degenerate one, not a 10-unit plan."""
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert result.exit_code == 0, result.output
    assert "no usable residual variance" in result.output


def test_plan_names_the_right_cause_for_a_missing_corr_on_an_override(project):
    """A hand-typed baseline that omitted `corr=` is not a pre-0.4.0 row: naming the
    wrong cause sends the operator to re-run an experiment that would not have helped."""
    result = runner.invoke(
        cli,
        [
            "plan",
            "--select",
            EXP,
            "--mde",
            "0.05",
            "--baseline",
            "example_arpu:mean=60,std=40,n=5000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "--baseline override carries no 'corr'" in result.output
    assert "before 0.4.0" not in result.output


def test_plan_flags_an_implausibly_large_variance_reduction():
    """A ρ that survives the degeneracy gate can still imply a >100× reduction, which
    is far more often a covariate derived from the metric than a real one — the number
    is computed (it is the measurement) but the line says to check it."""
    plan = _cuped_plan(0.9999)
    assert plan.result is not None
    assert any("check that the covariate is not derived from the metric" in n for n in plan.notes)
    # a healthy ρ says nothing of the sort
    assert not any("derived from the metric" in n for n in _cuped_plan(0.6).notes)


# ── PLAN-2: --from-history population baselines ─────────────────────────────────


def test_from_history_sizes_an_experiment_that_never_ran(project):
    """The gap PLAN-2 closes: before this, a pre-launch experiment read
    `SKIPPED: no baseline` — the one case planning is actually for."""
    without = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert "no baseline" in without.output

    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05", "--from-history", "14d"])
    assert result.exit_code == 0, result.output
    assert "no baseline" not in result.output
    assert "history 14d" in result.output
    assert "required" in result.output
    # the disclosure rides the same line, every time
    assert "POPULATION-wide" in result.output


def test_from_history_loses_to_an_explicit_baseline(project):
    """Precedence: a hand-typed number is the operator's deliberate statement."""
    result = runner.invoke(
        cli,
        [
            "plan",
            "--select",
            EXP,
            "--mde",
            "0.05",
            "--from-history",
            "14d",
            "--baseline",
            "example_arpu:mean=999,std=1,n=42",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "mean=999" in result.output
    # ...only for the metric it names; the other metric still uses history
    assert "history 14d" in result.output


def test_from_history_beats_the_persisted_rows(ran):
    """...and wins over a persisted baseline, which is the same experiment's own
    cohort: the flag is an explicit request to size on the population instead."""
    persisted = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05"])
    assert "persisted @" in persisted.output

    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05", "--from-history", "14d"])
    assert result.exit_code == 0, result.output
    assert "history 14d" in result.output
    assert "persisted @" not in result.output


def test_from_history_reads_the_population_not_the_cohort(project):
    """The whole point of the cohort-free render: an experiment with no enrolled
    units still gets moments, because the read never joins the cohort."""
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05", "--from-history", "14d"])
    assert result.exit_code == 0, result.output
    assert "baseline mean=" in result.output


@pytest.mark.parametrize("bad", ["garbage", "0d", "36h", "-3d", "1w2"])
def test_from_history_rejects_a_non_whole_day_interval(project, bad):
    """Whole days only — the grain `covariate_lookback` uses and the pre-period
    window is aligned to."""
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05", "--from-history", bad])
    assert result.exit_code != 0
    assert "from-history" in result.output


def test_a_history_render_failure_skips_only_that_comparison(project, monkeypatch):
    """One unreadable metric must not cost the whole plan — and the SKIPPED line
    names the render's own failure instead of the generic 'no baseline'."""
    from abkit.compute.recompute_backend import RecomputeBackend

    def explode(self, metric, metric_sql, lookback, grid):
        raise RuntimeError("boom: no such column")

    monkeypatch.setattr(RecomputeBackend, "load_population_window", explode)
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05", "--from-history", "14d"])
    assert result.exit_code == 0, result.output
    assert "boom: no such column" in result.output


def test_added_filters_get_their_own_disclosure(project, monkeypatch):
    """A population read cannot apply `assignment.added_filters`, so the line says
    the variance is indicative rather than this experiment's own."""
    from pathlib import Path

    exp_path = Path("experiments/example_signup_test.yml")
    body = exp_path.read_text(encoding="utf-8")
    assert "added_filters" not in body
    exp_path.write_text(
        body.replace("  variants:", "  added_filters: \"AND country = 'US'\"\n  variants:", 1),
        encoding="utf-8",
    )
    result = runner.invoke(cli, ["plan", "--select", EXP, "--mde", "0.05", "--from-history", "14d"])
    assert result.exit_code == 0, result.output
    assert "added_filters narrows the real cohort" in result.output


def test_an_asymmetric_interval_gets_a_fixed_horizon_asn_note_and_a_sizing_caveat():
    """m13 STAT-3: `abk plan` is the third surface that must know the interval shape.

    Under `interval: score` two things stop being true, and both would be silent:
    `abk run` refuses the always-valid mode for that comparison (config-lint errors
    on the pair), so an ASN here would size a design the pipeline will not run; and
    the power formula is the normal one while the analysis inverts the score
    statistic, so the printed MDE is a planning figure rather than the boundary the
    readout applies. STAT-1's rule — a surface that prints a level must know the
    scheme — with "scheme" read as "estimator".
    """
    from abkit.cli.commands.plan import _build_runtime
    from abkit.planning.sizing import BaselineMoments, SizingResult
    from abkit.stats import get_method_class

    experiment = _seq_experiment()
    moments = BaselineMoments("fraction", 0.2, 10000, 10000, None, "x")
    kwargs = {
        "test_type": "relative",
        "alpha": 0.05,
        "target_mde": 0.05,
        "plan_ratio": 1.0,
        "rate_control": 1000.0,
        "rate_source": "test",
        "look_days": [float(d) for d in range(1, 15)],
        "horizon_days": 14.0,
    }
    result = SizingResult(required_n=5000, achievable_mde=0.03, achieved_power=0.5)

    symmetric = _build_runtime(experiment, get_method_class("z-test"), result, moments, **kwargs)
    assert symmetric.asn is not None, "the sequential baseline must still produce an ASN"

    asymmetric = _build_runtime(
        experiment, get_method_class("z-test"), result, moments, asymmetric_ci=True, **kwargs
    )
    assert asymmetric.asn is None
    assert "asymmetric interval" in (asymmetric.asn_note or "")


def test_plan_refuses_a_comparison_whose_method_params_are_rejected():
    """The bind that resolves the interval shape is also the first place `abk plan`
    validates method params at all. A comparison it cannot construct is refused by
    name rather than silently sized against the defaults it never had."""
    from abkit.cli.commands.plan import _plan_comparison

    experiment = ExperimentConfig.model_validate(
        {
            "name": "e",
            "start_ts": "2024-07-01",
            "horizon_ts": "2024-07-15",
            "unit_key": "u",
            "assignment": {
                "query": "SELECT 1",
                "variants": ["control", "t"],
                "expected_split": {"control": 0.5, "t": 0.5},
            },
            "comparisons": [
                {
                    "metric": "cr",
                    "is_main_metric": True,
                    "method": {"name": "z-test", "params": {"interval": "wilson"}},
                }
            ],
        }
    )
    from abkit.config import ProjectConfig
    from abkit.pipeline.analyze import effective_alphas

    plan = _plan_comparison(
        experiment,
        experiment.comparisons[0],
        effective_alphas(experiment, ProjectConfig(name="p", default_profile="dev")),
        power=0.8,
        mde=None,
        override=None,
        tables=None,
        rate_control=None,
        rate_source="none",
        look_days=[],
        horizon_days=14.0,
        history=None,
    )
    assert plan.refused is not None and "method params rejected" in plan.refused
