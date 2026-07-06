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
from abkit.config.method_config import MethodConfig
from abkit.core.period_planner import generate_grid
from abkit.pipeline.analyze import comparison_alpha, effective_alphas
from abkit.validate.result import CellResult
from abkit.validate.runner import (
    ValidateSettings,
    _mark_recommended,
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


def test_mark_recommended_carries_the_actual_rationale_not_a_hardcode():
    """WP5 review: the over-budget fallback rationale must reach the report — a
    Recommended over-budget cell may NOT claim it was selected 'within budget'."""

    def cell(mid, fpr):
        return CellResult(
            metric="arpu",
            method_name=mid,
            method_params="{}",
            method_config_id=mid,
            mode="fpr",
            alpha=0.05,
            iterations=100,
            injected_effect=None,
            fpr=fpr,
            peeking_fpr=None,
            power=0.9,
            achieved_mde=None,
            coverage=None,
            effect_exaggeration=None,
            verdict="",
            budget=0.075,
            recommended=False,
            details={},
        )

    # every method over the 0.075 budget -> the fallback branch of _select_recommended
    cells = [cell("A", 0.11), cell("B", 0.30)]
    rec_id, rationale = _select_recommended(cells)
    marked = [
        _mark_recommended(c, rationale if c.method_config_id == rec_id else None) for c in cells
    ]
    rec = next(c for c in marked if c.recommended)
    assert rec.method_config_id == rec_id
    # the stored rationale is the real fallback warning, never the in-budget hardcode
    assert rec.details["recommended_rationale"] == rationale
    assert "fallback" in rec.details["recommended_rationale"]
    assert "within budget" not in rec.details["recommended_rationale"]
    # the non-recommended cell stays unflagged with no injected rationale
    other = next(c for c in marked if not c.recommended)
    assert "recommended_rationale" not in other.details


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


def _seeded_warehouse():
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=160)
    seed_null_events(warehouse)
    return warehouse


def test_run_validation_scores_the_composed_family(monkeypatch):
    """D9/WP8: a multi-metric run also produces the composed FWER/FDR family sweep."""
    warehouse = _seeded_warehouse()
    experiment = _two_tier_experiment()  # arpu + conversion + ctr
    backend = RecomputeBackend(warehouse, experiment)
    result = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        _grid(experiment),
        ValidateSettings(iterations=300),
        now_iso=NOW_ISO,
    )
    assert result.family is not None
    fam = result.family
    assert fam.correction == "bonferroni"
    assert fam.n_metrics >= 2 and fam.n_null_metrics == fam.n_metrics  # a null sweep
    assert fam.fwer is not None and 0.0 <= fam.fwer <= 1.0
    assert fam.fdr == fam.fwer  # complete-null identity
    assert "composed" in fam.verdict


def test_family_budget_is_anchored_to_the_nominal_rate_not_a_single_cell():
    """M5 exit-gate round-1 fix: the family FWER budget scales with the composed rule's
    nominal rate (≈Σα over the members), so it exceeds a single cell's α×1.5 — otherwise
    the default two-tier Bonferroni multi-metric family false-reads over budget."""
    from abkit.tuning.recompute import resolve_fpr_budget

    warehouse = _seeded_warehouse()
    experiment = _two_tier_experiment()  # 3 comparisons
    backend = RecomputeBackend(warehouse, experiment)
    result = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        _grid(experiment),
        ValidateSettings(iterations=300),
        now_iso=NOW_ISO,
    )
    single_cell = resolve_fpr_budget(PROJECT, 0.05, None)  # the old (wrong) family budget
    assert result.family is not None
    assert result.family.budget > single_cell  # family-scaled, not one cell's α×1.5


def test_bh_family_budget_anchors_to_member_level_not_the_composition():
    """M5 exit-gate round-2 fix: BH controls the complete-null family FWER at ≈α, so its
    budget must NOT scale with the Bonferroni composition (≈Σα) — otherwise a miscalibrated
    BH method is under-flagged. The BH budget stays ≈ max-member-α × headroom."""
    warehouse = _seeded_warehouse()
    experiment = ExperimentConfig.model_validate(
        {
            "name": "bh_family",
            "start_date": "2024-07-01",
            "end_date": "2024-07-04",
            "unit_key": "user_id",
            "alpha": 0.05,
            "correction": "benjamini_hochberg",
            "assignment": {
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            },
            "comparisons": [
                {"metric": "arpu", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "conversion", "method": {"name": "z-test"}},
                {"metric": "ctr", "method": {"name": "ratio-delta"}},
            ],
        }
    )
    backend = RecomputeBackend(warehouse, experiment)
    result = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        _grid(experiment),
        ValidateSettings(iterations=300),
        now_iso=NOW_ISO,
    )
    assert result.family is not None
    # member-level: max α (0.05) × 1.5 = 0.075, NOT the 3-metric composition ≈0.21
    assert result.family.budget < 0.10


def test_metric_filter_skips_the_family_sweep():
    warehouse = _seeded_warehouse()
    experiment = _two_tier_experiment()
    backend = RecomputeBackend(warehouse, experiment)
    result = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        _grid(experiment),
        ValidateSettings(iterations=300),
        now_iso=NOW_ISO,
        metric_filter="arpu",  # a single-metric view has no family to compose
    )
    assert result.family is None


def test_single_comparison_has_no_family():
    warehouse = _seeded_warehouse()
    experiment = make_experiment("aa_solo", "arpu", {"name": "t-test"})
    backend = RecomputeBackend(warehouse, experiment)
    result = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        _grid(experiment),
        ValidateSettings(iterations=300),
        now_iso=NOW_ISO,
    )
    assert result.family is None  # one declared comparison ⇒ no family


def test_enumerate_filters_incompatible_extra_methods_and_dedups():
    """m4 exit-gate round-2 (D6): a --method must match the metric's input_kind, not be
    paired, not be quarantined, and never duplicate a declared cell — else it enqueues a
    doomed cell that persists as a confusing 'failed' row."""
    experiment = make_experiment("aa_filter", "arpu", {"name": "t-test"})  # arpu is 'sample'
    log = []
    specs = enumerate_cells(
        experiment,
        PROJECT,
        METRICS,
        [
            MethodConfig(name="z-test"),  # needs a fraction metric -> skipped
            MethodConfig(name="paired-t-test"),  # paired can't run A/A -> skipped
            MethodConfig(name="t-test"),  # duplicate of the declared method -> deduped
        ],
        log,
    )
    assert [(s.metric, s.method.name) for s in specs] == [("arpu", "t-test")]
    messages = " ".join(d.message for d in log)
    assert "z-test" in messages and "paired-t-test" in messages  # each skip is logged

    # a compatible, distinct method IS enqueued
    specs2 = enumerate_cells(experiment, PROJECT, METRICS, [MethodConfig(name="cuped-t-test")])
    assert ("arpu", "cuped-t-test") in [(s.metric, s.method.name) for s in specs2]


def test_bootstrap_cell_fails_gracefully_without_aborting_siblings():
    """m4 exit-gate F1: a declared bootstrap method has no from_suffstats path and raises
    SampleValidationError (a StatsError). It must fail only ITS OWN cell (status='failed',
    reason recorded — R37), never escape per-cell isolation and abort the whole
    experiment's matrix, discarding the sibling closed-form cell."""
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=140)
    seed_null_events(warehouse)
    experiment = make_experiment("aa_boot", "arpu", {"name": "t-test"})
    backend = RecomputeBackend(warehouse, experiment)

    result = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        _grid(experiment),
        ValidateSettings(iterations=200),
        now_iso=NOW_ISO,
        extra_methods=[MethodConfig(name="bootstrap")],
    )
    by_method = {c.method_name: c for c in result.cells}
    # the closed-form sibling still scores and would persist
    assert by_method["t-test"].status == "success" and by_method["t-test"].fpr is not None
    # the bootstrap cell fails in isolation, carrying its reason for the audit row
    assert by_method["bootstrap"].status == "failed"
    assert by_method["bootstrap"].fpr is None
    assert by_method["bootstrap"].error_message


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
