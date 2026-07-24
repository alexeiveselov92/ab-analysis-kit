"""Reconciliation-engine tests (m9-implementation-plan.md WP5).

The gates: a clean series reconciles green across metric kinds; a REAL drift
(an event backfilled into an already-materialized day — the exact limitation
WP4 documents and this command exists to detect) is reported as a divergence,
not swallowed; a cutoff the incremental read fell back on is reported
UNVERIFIED rather than counted as a pass; comparisons with no incremental
path at all are skipped with a reason.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from synthetic_ab import (
    CONVERSION,
    CTR,
    METRICS,
    NOW,
    PROJECT,
    START,
    SyntheticWarehouse,
    experiment_payload,
    make_experiment,
    seed_all_events,
    seed_cohort,
)

from abkit.compute.reconcile import compare_results, reconcile_experiment
from abkit.config import ExperimentConfig, ProjectConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.pipeline import PipelineStep, run_experiment

T_TEST = {"name": "t-test", "params": {"test_type": "relative"}}

PROJECT_INCREMENTAL = ProjectConfig.model_validate(
    {"name": "p", "default_profile": "dev", "compute": {"incremental_reads": True}}
)


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_all_events(wh)
    return wh


@pytest.fixture
def tables(warehouse):
    return InternalTablesManager(warehouse)


def _run(warehouse, tables, experiment, project=PROJECT_INCREMENTAL, **kwargs):
    outcome = run_experiment(
        experiment, METRICS, project, warehouse, tables, now_utc=NOW, **kwargs
    )
    assert outcome.status == "completed", outcome.error
    return outcome


def _reconcile(warehouse, tables, experiment, **kwargs):
    return reconcile_experiment(
        experiment, METRICS, PROJECT_INCREMENTAL, warehouse, tables, **kwargs
    )


class TestCleanSeries:
    @pytest.mark.parametrize(
        "metric_name,method",
        [
            ("arpu", T_TEST),
            ("conversion", {"name": "z-test", "params": {"test_type": "relative"}}),
            ("ctr", {"name": "ratio-delta", "params": {"test_type": "relative"}}),
        ],
    )
    def test_whole_series_reconciles(self, warehouse, tables, metric_name, method):
        experiment = make_experiment("exp_clean", metric_name, method)
        _run(warehouse, tables, experiment)

        outcome = _reconcile(warehouse, tables, experiment)
        assert outcome.ok
        assert outcome.mismatches == []
        assert outcome.unverified == []
        assert outcome.cutoffs_checked == 4  # the 4-day daily grid
        assert len(outcome.matched) == 4  # one pair per cutoff

    def test_subday_series_reconciles(self, warehouse, tables):
        payload = experiment_payload("exp_subday", "arpu", T_TEST)
        payload["cadence"] = "18h"
        payload["data_lag"] = "1h"
        experiment = ExperimentConfig.model_validate(payload)
        _run(warehouse, tables, experiment)

        outcome = _reconcile(warehouse, tables, experiment)
        assert outcome.ok
        assert outcome.unverified == []
        assert outcome.cutoffs_checked >= 4


class TestDriftDetection:
    def test_backfill_into_a_materialized_day_is_reported(self, warehouse, tables):
        """The documented WP4 limitation, made visible: an event arriving into
        an already-closed day is frozen in state, so recompute and the
        incremental read legitimately disagree — exactly the drift this
        command exists to surface (and `--full-refresh` to heal)."""
        experiment = make_experiment("exp_drift", "arpu", T_TEST)
        _run(warehouse, tables, experiment)
        assert _reconcile(warehouse, tables, experiment).ok  # green before the backfill

        warehouse.events["user_revenue"].append(
            ("c000", "control", START + timedelta(days=1, hours=9), {"gross_usd": 500.0})
        )

        outcome = _reconcile(warehouse, tables, experiment)
        assert not outcome.ok
        assert outcome.mismatches
        # every cutoff spanning the backfilled day diverges (days 1..3)
        assert len(outcome.mismatches) == 3
        diverged_fields = {d.field for v in outcome.mismatches for d in v.diffs}
        assert "value_1" in diverged_fields  # the control arm's mean moved

    def test_full_refresh_heals_the_drift(self, warehouse, tables):
        """The documented recovery path actually restores agreement."""
        experiment = make_experiment("exp_heal", "arpu", T_TEST)
        _run(warehouse, tables, experiment)
        warehouse.events["user_revenue"].append(
            ("c000", "control", START + timedelta(days=1, hours=9), {"gross_usd": 500.0})
        )
        assert not _reconcile(warehouse, tables, experiment).ok

        _run(
            warehouse,
            tables,
            experiment,
            full_refresh_window=(START, START + timedelta(days=4)),
        )
        assert _reconcile(warehouse, tables, experiment).ok


class TestUnverifiedIsNotAPass:
    def test_missing_state_reports_unverified(self, warehouse, tables):
        """Without the STATE step every cutoff falls back to recompute — both
        sides then run the SAME code, so agreement proves nothing and must be
        reported as a coverage gap, not as a pass."""
        experiment = make_experiment("exp_nostate", "arpu", T_TEST)
        _run(
            warehouse,
            tables,
            experiment,
            steps=[PipelineStep.LOAD, PipelineStep.COMPUTE],
        )

        outcome = _reconcile(warehouse, tables, experiment)
        assert outcome.matched == []
        assert len(outcome.unverified) == 4
        assert outcome.ok  # a coverage gap is not a divergence
        assert all("fell back" in (v.note or "") for v in outcome.unverified)


class TestSkips:
    def test_bootstrap_comparison_is_skipped_with_a_reason(self, warehouse, tables):
        experiment = make_experiment(
            "exp_boot", "arpu", {"name": "bootstrap", "params": {"n_samples": 50}}
        )
        _run(warehouse, tables, experiment)

        outcome = _reconcile(warehouse, tables, experiment)
        assert outcome.verdicts == []
        assert [s.metric for s in outcome.skipped] == ["arpu"]
        assert "not state-eligible" in outcome.skipped[0].reason
        assert outcome.ok

    def test_uncomputed_series_is_skipped_with_a_reason(self, warehouse, tables):
        experiment = make_experiment("exp_norun", "arpu", T_TEST)
        tables.ensure_tables()

        outcome = _reconcile(warehouse, tables, experiment)
        assert outcome.verdicts == []
        assert "no computed cutoffs" in outcome.skipped[0].reason

    def test_metric_filter_narrows_the_scope(self, warehouse, tables):
        payload = experiment_payload("exp_two", "arpu", T_TEST)
        payload["comparisons"].append(
            {
                "metric": "ctr",
                "is_main_metric": False,
                "method": {"name": "ratio-delta", "params": {"test_type": "relative"}},
            }
        )
        experiment = ExperimentConfig.model_validate(payload)
        _run(warehouse, tables, experiment)

        outcome = _reconcile(warehouse, tables, experiment, metric_filter="ctr")
        assert {v.metric for v in outcome.verdicts} == {"ctr"}


class TestComparisonSemantics:
    def test_tolerance_is_relative_not_exact(self):
        from abkit.stats import TestResult

        base = TestResult(
            name_1="c",
            name_2="t",
            value_1=1.0,
            value_2=2.0,
            std_1=0.1,
            std_2=0.2,
            size_1=10,
            size_2=10,
            method_name="t-test",
            method_params={},
            alpha=0.05,
            pvalue=0.03,
            effect=1.0,
            ci_length=0.4,
            left_bound=0.8,
            right_bound=1.2,
            reject=True,
        )
        import dataclasses

        assert compare_results(base, dataclasses.replace(base, value_2=2.0 * (1 + 1e-12))) == []
        assert compare_results(base, dataclasses.replace(base, value_2=2.0 * (1 + 1e-6)))

    def test_size_and_reject_are_exact(self):
        from abkit.stats import TestResult

        import dataclasses

        base = TestResult(
            name_1="c",
            name_2="t",
            value_1=1.0,
            value_2=2.0,
            std_1=0.1,
            std_2=0.2,
            size_1=10,
            size_2=10,
            method_name="t-test",
            method_params={},
            alpha=0.05,
            pvalue=0.03,
            effect=1.0,
            ci_length=0.4,
            left_bound=0.8,
            right_bound=1.2,
            reject=True,
        )
        assert [d.field for d in compare_results(base, dataclasses.replace(base, size_1=11))] == [
            "size_1"
        ]
        assert [
            d.field for d in compare_results(base, dataclasses.replace(base, reject=False))
        ] == ["reject"]

    def test_one_sided_demotion_diverges(self):
        from abkit.stats import TestResult

        base = TestResult(
            name_1="c",
            name_2="t",
            value_1=1.0,
            value_2=2.0,
            std_1=0.1,
            std_2=0.2,
            size_1=10,
            size_2=10,
            method_name="t-test",
            method_params={},
            alpha=0.05,
            pvalue=0.03,
            effect=1.0,
            ci_length=0.4,
            left_bound=0.8,
            right_bound=1.2,
            reject=True,
        )
        assert [d.field for d in compare_results(base, None)] == ["insufficient_data"]
        assert compare_results(None, None) == []
