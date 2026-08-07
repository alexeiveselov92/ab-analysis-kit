"""WP3 runner: cell enumeration, effective alpha, selection, verdicts, determinism."""

from __future__ import annotations

from datetime import timedelta

import pytest
from synthetic_ab import (
    METRICS,
    PROJECT,
    START,
    SyntheticWarehouse,
    experiment_payload,
    make_experiment,
    seed_cohort,
    seed_null_events,
)

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ExperimentConfig
from abkit.config.method_config import MethodConfig
from abkit.pipeline.analyze import comparison_alpha, effective_alphas
from abkit.validate.result import CellResult
from abkit.validate.runner import (
    ValidateSettings,
    _default_iterations,
    _mark_recommended,
    _resolve_iterations,
    _select_recommended,
    enumerate_cells,
    run_validation,
)

NOW_ISO = "2026-07-05T00:00:00"


def _grid(experiment):
    return experiment.grid()


def _two_tier_experiment() -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "name": "twotier",
            "start_ts": "2024-07-01",
            "horizon_ts": "2024-07-05",
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
    """D9/WP8: a multi-metric run opted in with ``family_sweep=True`` (the m7 WP6
    default flip) also produces the composed FWER/FDR family sweep."""
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
        ValidateSettings(iterations=300, family_sweep=True),
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
        ValidateSettings(iterations=300, family_sweep=True),
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
            "start_ts": "2024-07-01",
            "horizon_ts": "2024-07-05",
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
        ValidateSettings(iterations=300, family_sweep=True),
        now_iso=NOW_ISO,
    )
    assert result.family is not None
    # member-level: max α (0.05) × 1.5 = 0.075, NOT the 3-metric composition ≈0.21
    assert result.family.budget < 0.10


def test_holm_family_budget_anchors_to_member_level_too():
    """m13 STAT-1: the anchor is chosen by KIND (read-time), not by scheme name —
    a name test here would leave Holm judged against the Bonferroni composition
    (≈Σα) and under-flag a miscalibrated method, exactly the M5 round-2 defect."""
    warehouse = _seeded_warehouse()
    experiment = ExperimentConfig.model_validate(
        {
            "name": "holm_family",
            "start_ts": "2024-07-01",
            "horizon_ts": "2024-07-05",
            "unit_key": "user_id",
            "alpha": 0.05,
            "correction": "holm",
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
        ValidateSettings(iterations=300, family_sweep=True),
        now_iso=NOW_ISO,
    )
    assert result.family is not None
    assert result.family.correction == "holm"
    assert result.family.budget < 0.10  # member level 0.05×1.5, not the ≈0.21 composition


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
        ValidateSettings(iterations=300, family_sweep=True),
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


# ── m7 WP6: the auto-N-per-alpha policy + the family-sweep opt-in ────────────────


def test_default_iterations_follows_the_alpha_table():
    """N = max(2000, ceil(200/α)) — REPORT item 8's table: ~4000 at the 5% main tier,
    ~40000 at a 0.5% secondary tier; loose alphas keep the pre-WP6 2000 floor."""
    assert _default_iterations(0.05) == 4000
    assert _default_iterations(0.025) == 8000
    assert _default_iterations(0.005) == 40000
    assert _default_iterations(0.2) == 2000  # ceil(200/0.2)=1000 < the 2000 floor
    assert _default_iterations(0.0) == 2000  # degenerate alpha: floor, never a crash


def test_resolve_iterations_warns_above_threshold_but_never_caps():
    """§4.1 maintainer call: log-and-continue above 100k, no hard cap — a configured
    (if extreme) alpha tier must never be silently truncated."""
    log = []
    resolved = _resolve_iterations(ValidateSettings(), 0.001, "arpu/t-test", log)
    assert resolved == 200_000  # uncapped
    assert len(log) == 1 and "uncapped" in log[0].message and "arpu/t-test" in log[0].message
    # explicit -n bypasses both the policy and the warning
    log2 = []
    assert _resolve_iterations(ValidateSettings(iterations=50), 0.001, "x", log2) == 50
    assert log2 == []
    # a modest alpha resolves silently
    log3 = []
    assert _resolve_iterations(ValidateSettings(), 0.05, "x", log3) == 4000
    assert log3 == []


def test_auto_iterations_resolve_per_cell_not_once_globally(monkeypatch):
    """iterations=None resolves at EACH cell's effective alpha (main 0.05 → 4000,
    secondary 0.025 → 8000 in the two-tier fixture) — never once for the whole run.
    score_cell is stubbed to raise so the resolved N lands on the (failed) row cheaply."""
    from abkit.validate import runner as runner_module
    from abkit.validate._types import ValidateError

    def _boom(*args, **kwargs):
        raise ValidateError("stub — resolution already happened")

    monkeypatch.setattr(runner_module, "score_cell", _boom)
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
        ValidateSettings(),  # iterations=None → the auto policy
        now_iso=NOW_ISO,
    )
    assert len(result.cells) == 3
    for cell in result.cells:
        assert cell.iterations == _default_iterations(cell.alpha)
    ns = {c.metric: c.iterations for c in result.cells}
    assert ns["arpu"] == 4000 and ns["conversion"] == 8000  # tiers resolve differently


def test_run_validation_auto_n_scores_for_real():
    """The happy path under the auto policy: a single-comparison run at α=0.05 really
    scores 4000 placebo splits (the vectorized engine keeps this cheap) and the
    persisted-row N records what actually ran, never None."""
    warehouse = _seeded_warehouse()
    experiment = make_experiment("aa_auto", "arpu", {"name": "t-test"})
    backend = RecomputeBackend(warehouse, experiment)
    result = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        _grid(experiment),
        ValidateSettings(),
        now_iso=NOW_ISO,
    )
    cell = result.cells[0]
    assert cell.status == "success" and cell.fpr is not None
    assert cell.iterations == 4000


def test_family_sweep_is_opt_in_with_a_migration_notice():
    """m7 WP6 default flip: without family_sweep=True a multi-metric run composes NO
    family — and logs the one-release migration notice naming --family-sweep."""
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
        ValidateSettings(iterations=300),  # family_sweep defaults to False
        now_iso=NOW_ISO,
    )
    assert result.family is None
    notices = [d for d in result.decision_log if "--family-sweep" in d.message]
    assert notices and "skipped" in notices[0].message


def test_family_sweep_flag_with_metric_filter_is_logged_not_run():
    """--family-sweep + --metric: one metric has no family — skip with an explicit log
    entry (never a half-family compose over whatever panels happened to load)."""
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
        ValidateSettings(iterations=300, family_sweep=True),
        now_iso=NOW_ISO,
        metric_filter="arpu",
    )
    assert result.family is None
    assert any("--family-sweep ignored" in d.message for d in result.decision_log)


class TestDeclaredControlReachesThePlaceboSplit:
    """m14 DEC-1, behaviourally: ``_share_a`` mirrors the CONTROL's share.

    The placebo's arm A is the baseline, so its share has to come from the
    declared control — otherwise a 20/40/40 experiment declaring ``control: c``
    is calibrated at a 20% baseline it never runs. No test in this package
    built a config with ``control:`` before, which is how the review's probe
    reverted the site with every validate test still green.
    """

    @staticmethod
    def _experiment(control=None) -> ExperimentConfig:
        assignment = {
            "query": "SELECT 1",
            "variants": ["a", "b", "c"],
            "expected_split": {"a": 0.2, "b": 0.4, "c": 0.4},
        }
        if control is not None:
            assignment["control"] = control
        return ExperimentConfig.model_validate(
            {
                "name": "share_a",
                "start_ts": "2024-07-01",
                "horizon_ts": "2024-07-15",
                "unit_key": "user_id",
                "assignment": assignment,
                "comparisons": [
                    {"metric": "cr", "is_main_metric": True, "method": {"name": "z-test"}}
                ],
            }
        )

    def test_the_share_follows_the_declaration(self):
        """Superseded in its DENOMINATOR by m14 DEC-5, not in its claim.

        The share is still the CONTROL's, but now within the calibrated PAIR
        rather than over every arm: 20/40/40 with the default control gives
        0.2/(0.2+0.4) = 1/3, the split the live control-vs-b comparison runs,
        where `0.8.0` used 0.2 — a baseline share no comparison in this
        experiment has. Declaring `control: c` moves both the numerator and the
        pair (c vs a, the first declared treatment).
        """
        from abkit.validate.runner import _share_a

        assert _share_a(self._experiment()) == pytest.approx(0.2 / 0.6)
        assert _share_a(self._experiment(control="c")) == pytest.approx(0.4 / 0.6)

    def test_the_pair_is_the_control_against_the_first_declared_treatment(self):
        from abkit.validate.runner import calibrated_contrast

        assert calibrated_contrast(self._experiment()) == ("a", "b")
        # with a control declared LAST, the first treatment is `a` — and
        # `contrast_pairs()[0]` would have been the treatment pair (a, b)
        assert calibrated_contrast(self._experiment(control="c")) == ("c", "a")

    def test_a_two_arm_split_is_unchanged(self):
        """The WP's №1 assertion, at the level the change is made: with two arms
        the pair IS the whole split, so the new denominator equals the old."""
        from abkit.validate.runner import _share_a

        two_arm = ExperimentConfig.model_validate(
            {
                "name": "share_two",
                "start_ts": "2024-07-01",
                "horizon_ts": "2024-07-15",
                "unit_key": "user_id",
                "assignment": {
                    "query": "SELECT 1",
                    "variants": ["control", "treatment"],
                    "expected_split": {"control": 0.3, "treatment": 0.7},
                },
                "comparisons": [
                    {"metric": "cr", "is_main_metric": True, "method": {"name": "z-test"}}
                ],
            }
        )

        assert _share_a(two_arm) == pytest.approx(0.3)


class TestTheCalibratedContrastIsDisclosed:
    """m14 DEC-5(a): one pair sizes the placebo, so the verdict names it.

    A decision-log entry would not do — no CLI user ever sees one (the M7 WP6
    lesson, where a warning found by review had never reached a terminal).
    """

    @staticmethod
    def _score(fpr=0.05, share=None):
        import inspect

        from abkit.validate.scoring import CellScore

        # built from the dataclass' own signature: a new CellScore field must
        # not silently default here, it must be classified (the m13 roster rule)
        kwargs = {
            "iterations": 2000,
            "valid_iterations": 2000,
            "fpr": fpr,
            "fpr_negative_share": share,
            "peeking_fpr": fpr,
            "peeking_curve": (),
        }
        params = inspect.signature(CellScore).parameters
        for name, param in params.items():
            if name not in kwargs and param.default is inspect.Parameter.empty:
                kwargs[name] = None
        return CellScore(**kwargs)

    def test_three_arms_name_the_pair(self):
        from abkit.validate.runner import _verdict

        note = _verdict("t-test", "arpu", self._score(), 0.075, 0.05, ("control", "b"))
        assert note.endswith("; calibrated on control vs b")

    def test_two_arms_say_nothing(self):
        """Naming the only pair there is would be noise — and would move a
        `0.8.0` string, which the WP's №1 assertion forbids."""
        from abkit.validate.runner import _verdict

        note = _verdict("t-test", "arpu", self._score(), 0.075, 0.05, None)
        assert "calibrated on" not in note


class TestTheRunnerActuallyCalibratesTheContrast:
    """m14 DEC-5(a) AT THE SURFACE, not at the helper.

    Deleting `arms=arms` from the panel load — i.e. reverting `abk validate` to
    the `0.8.0` pooled placebo — left all 264 tests green, because every test of
    the filter called `load_placebo_panel` directly. That is the DEC-1/DEC-3
    lesson this WP's own commit invokes: a rerouted surface owes a behavioural
    assertion at the surface.
    """

    @staticmethod
    def _three_arm_run(n_per_arm: int = 140):
        warehouse = SyntheticWarehouse()
        for i in range(n_per_arm):
            warehouse.cohort.append((f"c{i:03d}", "control", START + timedelta(hours=1)))
            warehouse.cohort.append((f"t{i:03d}", "treatment", START + timedelta(hours=1)))
            warehouse.cohort.append((f"u{i:03d}", "treatment_b", START + timedelta(hours=1)))
        seed_null_events(warehouse)
        payload = experiment_payload("aa_three_run", "arpu", {"name": "t-test"})
        payload["assignment"]["variants"] = ["control", "treatment", "treatment_b"]
        payload["assignment"]["expected_split"] = {
            "control": 1 / 3,
            "treatment": 1 / 3,
            "treatment_b": 1 / 3,
        }
        experiment = ExperimentConfig.model_validate(payload)
        return warehouse, experiment

    def test_the_panel_the_runner_scores_holds_the_pair_only(self, monkeypatch):
        import abkit.validate.runner as runner_mod

        warehouse, experiment = self._three_arm_run()
        seen: list[object] = []
        real = runner_mod.load_placebo_panel

        def spy(*args, **kwargs):
            panel = real(*args, **kwargs)
            seen.append((kwargs.get("arms"), panel.n_units))
            return panel

        monkeypatch.setattr(runner_mod, "load_placebo_panel", spy)
        run_validation(
            RecomputeBackend(warehouse, experiment),
            experiment,
            PROJECT,
            METRICS,
            {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
            _grid(experiment),
            ValidateSettings(iterations=50),
            now_iso=NOW_ISO,
        )

        assert seen, "the runner loaded no panel at all"
        for arms, n_units in seen:
            assert arms == ("control", "treatment"), "the calibrated contrast, not every arm"
            assert n_units == 280, "the third arm's 140 units must stay out of the placebo"

    def test_the_verdict_names_the_pair_end_to_end(self):
        warehouse, experiment = self._three_arm_run()

        result = run_validation(
            RecomputeBackend(warehouse, experiment),
            experiment,
            PROJECT,
            METRICS,
            {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
            _grid(experiment),
            ValidateSettings(iterations=50),
            now_iso=NOW_ISO,
        )

        assert all("calibrated on control vs treatment" in c.verdict for c in result.cells)

    def test_a_two_arm_verdict_carries_no_disclosure(self):
        """The gate that produces `None`, not the helper that receives it: with
        `> 2` mutated to `> 1` every two-arm cell would gain the suffix — in a
        PERSISTED `_ab_aa_runs.verdict` column and on the printed CLI line."""
        warehouse = SyntheticWarehouse()
        seed_cohort(warehouse, n_per_arm=160)
        seed_null_events(warehouse)
        experiment = make_experiment("aa_two_run", "arpu", {"name": "t-test"})

        result = run_validation(
            RecomputeBackend(warehouse, experiment),
            experiment,
            PROJECT,
            METRICS,
            {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
            _grid(experiment),
            ValidateSettings(iterations=50),
            now_iso=NOW_ISO,
        )

        assert result.cells
        assert all("calibrated on" not in c.verdict for c in result.cells)
