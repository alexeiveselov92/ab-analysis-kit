"""WP3 runner: cell enumeration, effective alpha, selection, verdicts, determinism."""

from __future__ import annotations

from synthetic_ab import (
    METRICS,
    PROJECT,
    SyntheticWarehouse,
    make_experiment,
    seed_cohort,
    seed_null_events,
)

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ExperimentConfig
from abkit.core.period_planner import generate_grid
from abkit.pipeline.analyze import comparison_alpha, effective_alphas
from abkit.validate.result import CellResult
from abkit.validate.runner import (
    ValidateSettings,
    _select_recommended,
    enumerate_cells,
    run_validation,
)

NOW_ISO = "2026-07-05T00:00:00"


def _grid(experiment):
    return generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        tz=experiment.timezone,
    )


def _two_tier_experiment() -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "name": "twotier",
            "start_date": "2024-07-01",
            "end_date": "2024-07-04",
            "unit_key": "user_id",
            "alpha": 0.05,
            "correction": "bonferroni",
            "assignment": {
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            },
            "comparisons": [
                {"metric": "arpu", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "conversion", "is_main_metric": False, "method": {"name": "z-test"}},
                {"metric": "ctr", "is_main_metric": False, "method": {"name": "ratio-delta"}},
            ],
        }
    )


def test_enumerate_uses_effective_two_tier_alphas():
    experiment = _two_tier_experiment()
    cells = enumerate_cells(experiment, PROJECT)
    alphas = effective_alphas(experiment, PROJECT)
    by_metric = {c.metric: c for c in cells}
    # main and secondary metrics land at DIFFERENT effective alphas (Bonferroni tiers:
    # with 2 non-main metrics the secondary budget is split, so 0.05 vs 0.025)
    assert by_metric["arpu"].alpha == comparison_alpha(experiment.comparisons[0], alphas)
    assert by_metric["conversion"].alpha == comparison_alpha(experiment.comparisons[1], alphas)
    assert by_metric["arpu"].alpha != by_metric["conversion"].alpha


def test_select_recommended_prefers_in_budget_max_power():
    def cell(mid, fpr, power, budget=0.075):
        return CellResult(
            metric="arpu",
            method_name="m",
            method_params="{}",
            method_config_id=mid,
            mode="fpr",
            alpha=0.05,
            iterations=100,
            injected_effect=0.1,
            fpr=fpr,
            peeking_fpr=None,
            power=power,
            achieved_mde=None,
            coverage=None,
            effect_exaggeration=None,
            verdict="",
            budget=budget,
            recommended=False,
            details={},
        )

    # A is in budget with lower power; B is in budget with higher power -> B wins
    cells = [cell("A", 0.05, 0.6), cell("B", 0.05, 0.9), cell("C", 0.2, 0.99)]
    rec_id, rationale = _select_recommended(cells)
    assert rec_id == "B"
    assert "within budget" in rationale


def test_select_recommended_falls_back_when_none_in_budget():
    def cell(mid, fpr):
        return CellResult(
            metric="arpu",
            method_name="m",
            method_params="{}",
            method_config_id=mid,
            mode="fpr",
            alpha=0.05,
            iterations=100,
            injected_effect=None,
            fpr=fpr,
            peeking_fpr=None,
            power=None,
            achieved_mde=None,
            coverage=None,
            effect_exaggeration=None,
            verdict="",
            budget=0.075,
            recommended=False,
            details={},
        )

    rec_id, rationale = _select_recommended([cell("A", 0.11), cell("B", 0.30)])
    assert rec_id == "A"  # closest-to-nominal fallback (lowest fpr)
    assert "fallback" in rationale


def test_run_validation_scores_cells_and_marks_one_recommended():
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=160)
    seed_null_events(warehouse)
    experiment = make_experiment("aa_run", "arpu", {"name": "t-test"})
    backend = RecomputeBackend(warehouse, experiment)

    result = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        _grid(experiment),
        ValidateSettings(iterations=400),
        now_iso=NOW_ISO,
    )
    assert len(result.cells) == 1
    cell = result.cells[0]
    assert cell.metric == "arpu" and cell.method_name == "t-test"
    assert cell.status == "success" and cell.fpr is not None
    assert cell.recommended is True  # the only cell for the metric
    assert "well-calibrated" in cell.verdict or "FPR" in cell.verdict
    assert cell.alpha == comparison_alpha(
        experiment.comparisons[0], effective_alphas(experiment, PROJECT)
    )


def test_run_validation_is_reproducible():
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=140)
    seed_null_events(warehouse)
    experiment = make_experiment("aa_repro", "arpu", {"name": "t-test"})
    backend = RecomputeBackend(warehouse, experiment)
    sqls = {name: cfg.get_query_text(None) for name, cfg in METRICS.items()}

    a = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        sqls,
        _grid(experiment),
        ValidateSettings(iterations=300),
        now_iso=NOW_ISO,
    )
    b = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        sqls,
        _grid(experiment),
        ValidateSettings(iterations=300),
        now_iso=NOW_ISO,
    )
    assert a.cells[0].fpr == b.cells[0].fpr
    assert a.run_stamp == b.run_stamp  # deterministic, wall-clock-free
