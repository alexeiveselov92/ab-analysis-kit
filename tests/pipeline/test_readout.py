"""WP1 — the pure readout decision core (m3-implementation-plan.md D5).

Known-answer verdict tables over hand-built ``_ab_results`` row dicts (the
``load_results()`` shape) — no DB, no rendering. Every D5 branch is pinned:
the SRM hard gate, pre-horizon withholding, elapsed-time stabilization (never
look count), FLAT vs underpowered vs no-min_effect, the NULL-MDE fallback
(t-test exact, z-test via nobs inversion, ratio-delta honestly unreachable),
guardrail regression under both policies, read-time BH, demoted-row gaps,
and multi-arm per-pair verdicts.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from abkit.config.experiment_config import ExperimentConfig
from abkit.config.project_config import ProjectConfig
from abkit.pipeline.readout import (
    MIN_STABLE_CUTOFFS,
    evaluate,
    pair_mde,
    srm_summary,
)
from abkit.stats import Fraction, SufficientStats, create_method
from abkit.stats.correction import benjamini_hochberg

START = datetime(2026, 1, 1)
T_TEST_ID = None  # filled lazily from the config fixture


def make_experiment(**overrides) -> ExperimentConfig:
    config = {
        "name": "readout_exp",
        "start_date": "2026-01-01",
        "end_date": "2026-01-14",  # 14-day horizon
        "unit_key": "user_id",
        "assignment": {
            "query": "SELECT 1",
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
        },
        "alpha": 0.05,
        "correction": "none",
        "comparisons": [
            {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
        ],
    }
    config.update(overrides)
    return ExperimentConfig.model_validate(config)


def make_row(experiment: ExperimentConfig, metric: str = "revenue", **overrides) -> dict:
    """One full-contract row with a significant-positive default outcome.

    An unconfigured ``metric`` (the stale-rows scenario) borrows the first
    comparison's method identity — exactly what a leftover row looks like.
    """
    try:
        comparison = experiment.get_comparison(metric)
    except KeyError:
        comparison = experiment.comparisons[0]
    day = overrides.pop("day", 14)
    end_ts = START + timedelta(days=day)
    row = {
        "experiment": experiment.name,
        "metric": metric,
        "is_main_metric": comparison.is_main_metric,
        "is_guardrail": comparison.is_guardrail,
        "method_name": comparison.method.name,
        "method_params": comparison.method.canonical_params_json,
        "method_config_id": comparison.method.method_config_id,
        "name_1": "control",
        "name_2": "treatment",
        "start_ts": START,
        "end_ts": end_ts,
        "start_date": START.date(),
        "end_date": (end_ts - timedelta(microseconds=1)).date(),
        "window_seconds": day * 86400,
        "elapsed_days": float(day),
        "value_1": 10.0,
        "value_2": 11.0,
        "std_1": 2.0,
        "std_2": 2.0,
        "cov_value_1": None,
        "cov_value_2": None,
        "size_1": 1000,
        "size_2": 1000,
        "alpha": 0.05,
        "pvalue": 0.001,
        "effect": 0.1,
        "left_bound": 0.05,
        "right_bound": 0.15,
        "ci_length": 0.10,
        "reject": True,
        "mde_1": None,
        "mde_2": None,
        "srm_flag": False,
        "srm_pvalue": 0.8,
        "decision_blocked": False,
        "insufficient_data": False,
        "ci_kind": "fixed",
        "is_horizon": day >= 14,
        "warnings": None,
        "diagnostics": None,
        "metric_query": "SELECT ...",
        "metric_rendered_query": "SELECT ...",
        "watermark_ts": end_ts,
    }
    row.update(overrides)
    return row


def make_series(experiment: ExperimentConfig, days: int = 14, metric: str = "revenue", **overrides):
    """A daily cumulative series ending at the horizon (day 14)."""
    return [make_row(experiment, metric=metric, day=d, **overrides) for d in range(1, days + 1)]


def single_verdict(experiment: ExperimentConfig, rows):
    readout = evaluate(experiment, rows)
    assert len(readout.verdicts) == 1
    return readout.verdicts[0]


def joined(strings) -> str:
    return " | ".join(strings)


# ── the verdict table ─────────────────────────────────────────────────────────


class TestVerdicts:
    def test_stable_significant_positive_is_win(self):
        experiment = make_experiment()
        verdict = single_verdict(experiment, make_series(experiment))
        assert verdict.verdict == "WIN"
        assert "desired direction" in joined(verdict.rationale)
        assert verdict.significant

    def test_stable_significant_negative_is_lose(self):
        experiment = make_experiment()
        rows = make_series(experiment, effect=-0.1, left_bound=-0.15, right_bound=-0.05)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "LOSE"

    def test_desired_direction_decrease_flips_win_and_lose(self):
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "desired_direction": "decrease",
                    "method": {"name": "t-test"},
                }
            ]
        )
        down = make_series(experiment, effect=-0.1, left_bound=-0.15, right_bound=-0.05)
        assert single_verdict(experiment, down).verdict == "WIN"
        up = make_series(experiment)
        assert single_verdict(experiment, up).verdict == "LOSE"

    def test_quiet_and_powered_is_flat(self):
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "min_effect": 0.05,
                    "method": {"name": "t-test"},
                }
            ]
        )
        rows = make_series(
            experiment,
            effect=0.001,
            left_bound=-0.01,
            right_bound=0.012,
            pvalue=0.8,
            reject=False,
            mde_1=0.02,
            mde_2=0.02,
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "FLAT"
        assert "adequately powered" in joined(verdict.rationale)

    def test_quiet_but_underpowered_is_inconclusive(self):
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "min_effect": 0.05,
                    "method": {"name": "t-test"},
                }
            ]
        )
        rows = make_series(
            experiment,
            effect=0.001,
            left_bound=-0.05,
            right_bound=0.052,
            pvalue=0.8,
            reject=False,
            mde_1=0.20,
            mde_2=0.20,
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "underpowered" in joined(verdict.rationale)

    def test_quiet_without_min_effect_makes_flat_unreachable(self):
        experiment = make_experiment()
        rows = make_series(
            experiment,
            effect=0.001,
            left_bound=-0.01,
            right_bound=0.012,
            pvalue=0.8,
            reject=False,
            mde_1=0.001,
            mde_2=0.001,
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "no min_effect is configured" in joined(verdict.rationale)

    def test_no_rows_is_inconclusive(self):
        experiment = make_experiment()
        verdict = single_verdict(experiment, [])
        assert verdict.verdict == "INCONCLUSIVE"
        assert "no computed results" in joined(verdict.rationale)


# ── the hard gates ────────────────────────────────────────────────────────────


class TestSrmGate:
    def test_srm_forces_inconclusive_before_anything_else(self):
        experiment = make_experiment()
        rows = make_series(experiment, srm_flag=True, decision_blocked=True, srm_pvalue=1e-6)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "SRM failed" in joined(verdict.rationale)

    def test_srm_summary_is_surfaced_experiment_level(self):
        experiment = make_experiment()
        rows = make_series(experiment, srm_flag=True, decision_blocked=True, srm_pvalue=1e-6)
        readout = evaluate(experiment, rows)
        assert readout.srm_flag is True
        assert readout.srm_pvalue == pytest.approx(1e-6)


class TestPreHorizon:
    def test_significant_but_pre_horizon_is_withheld(self):
        experiment = make_experiment()
        rows = make_series(experiment, days=10)  # horizon is day 14
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "pre-horizon" in joined(verdict.rationale)
        assert not verdict.is_horizon

    def test_sequential_config_without_sequential_rows_still_refuses(self):
        experiment = make_experiment(sequential={"enabled": True})
        rows = make_series(experiment, days=10)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "sequential.enabled is set" in joined(verdict.caveats)


class TestLatestCutoffUsability:
    def test_demoted_latest_cutoff_is_inconclusive(self):
        experiment = make_experiment()
        rows = make_series(experiment, days=13)
        rows.append(
            make_row(
                experiment,
                day=14,
                insufficient_data=True,
                pvalue=None,
                effect=None,
                left_bound=None,
                right_bound=None,
                ci_length=None,
                reject=None,
                size_1=5,
                size_2=5,
            )
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "insufficient data at the latest cutoff" in joined(verdict.rationale)

    def test_degenerate_bounds_at_latest_cutoff_is_inconclusive(self):
        experiment = make_experiment()
        rows = make_series(experiment, days=13)
        rows.append(make_row(experiment, day=14, left_bound=None, right_bound=None))
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "degenerate variance" in joined(verdict.rationale)


# ── stabilization (elapsed time, never look count) ────────────────────────────


class TestStabilization:
    def test_ci_recross_within_window_is_not_win(self):
        experiment = make_experiment()
        rows = make_series(experiment)
        # Day 12 (inside the trailing 7-day window) re-crosses zero.
        rows[11] = make_row(
            experiment, day=12, effect=0.01, left_bound=-0.02, right_bound=0.04, pvalue=0.4
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "crossed zero" in joined(verdict.rationale)

    def test_recross_outside_window_does_not_matter(self):
        experiment = make_experiment()
        rows = make_series(experiment)
        # Day 3 volatility is outside the trailing 7-day window ending day 14.
        rows[2] = make_row(
            experiment, day=3, effect=0.01, left_bound=-0.02, right_bound=0.04, pvalue=0.4
        )
        assert single_verdict(experiment, rows).verdict == "WIN"

    def test_sign_flip_within_window_is_not_stabilized(self):
        experiment = make_experiment()
        rows = make_series(experiment)
        rows[11] = make_row(experiment, day=12, effect=-0.1, left_bound=-0.15, right_bound=-0.05)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "sign flipped" in joined(verdict.rationale)

    def test_window_is_elapsed_days_not_look_count(self):
        """An irregular (dense-early) grid: only the last 7 elapsed days count."""
        experiment = make_experiment()
        rows = []
        # Dense early looks (hours 6/12/18 of day 1) — noisy, CI crossing zero.
        for hours in (6, 12, 18):
            rows.append(
                make_row(
                    experiment,
                    day=1,
                    end_ts=START + timedelta(hours=hours),
                    elapsed_days=hours / 24.0,
                    window_seconds=hours * 3600,
                    is_horizon=False,
                    effect=0.01,
                    left_bound=-0.05,
                    right_bound=0.07,
                    pvalue=0.5,
                )
            )
        # Then clean daily significance through the horizon.
        rows.extend(make_series(experiment))
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"  # early noise is outside the window

    def test_dense_late_looks_cannot_outvote_the_elapsed_window(self):
        """The mutant killer (review finding): a look-count window over the
        last N looks would see only the dense significant sub-day cutoffs of
        days 13–14 and call WIN; the elapsed-days window (trailing 7 days)
        still contains the noisy days 7–12 and must refuse."""
        experiment = make_experiment()
        rows = [
            make_row(
                experiment,
                day=d,
                is_horizon=False,
                effect=0.01,
                left_bound=-0.02,
                right_bound=0.04,
                pvalue=0.4,
            )
            for d in range(1, 13)  # noisy days 1..12
        ]
        for k in range(9):  # 9 significant 3-hourly looks spanning days 13..14
            elapsed = 13.0 + k * 0.125
            rows.append(
                make_row(
                    experiment,
                    day=13,
                    end_ts=START + timedelta(days=elapsed),
                    elapsed_days=elapsed,
                    window_seconds=int(elapsed * 86400),
                    is_horizon=(k == 8),
                )
            )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "crossed zero" in joined(verdict.rationale)

    def test_demoted_rows_are_gaps_not_zeros(self):
        experiment = make_experiment()
        rows = make_series(experiment)
        # Demote two mid-window days: they must be skipped, not counted quiet.
        for index in (10, 11):
            rows[index] = make_row(
                experiment,
                day=index + 1,
                insufficient_data=True,
                pvalue=None,
                effect=None,
                left_bound=None,
                right_bound=None,
                reject=None,
            )
        assert single_verdict(experiment, rows).verdict == "WIN"

    def test_too_few_informative_cutoffs_is_inconclusive(self):
        experiment = make_experiment(end_date="2026-01-02")  # 2-day horizon
        rows = [
            make_row(experiment, day=1, is_horizon=False),
            make_row(experiment, day=2, is_horizon=True),
        ]
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert f"at least {MIN_STABLE_CUTOFFS}" in joined(verdict.rationale)

    def test_coarse_cadence_widens_to_last_three_cutoffs(self):
        """Weekly-ish cutoffs: fewer than 3 rows inside 7 trailing days, but the
        window widens to the last 3 informative cutoffs (D5(a) floor)."""
        experiment = make_experiment(end_date="2026-01-21")  # 21-day horizon
        rows = [
            make_row(experiment, day=7, is_horizon=False),
            make_row(experiment, day=14, is_horizon=False),
            make_row(experiment, day=21, is_horizon=True),
        ]
        assert single_verdict(experiment, rows).verdict == "WIN"


# ── weekly-cycle representativeness ───────────────────────────────────────────


class TestWeeklyCycleCaveat:
    def test_short_horizon_win_carries_the_caveat(self):
        experiment = make_experiment(end_date="2026-01-05")  # 5-day horizon
        rows = [make_row(experiment, day=d, is_horizon=(d == 5)) for d in range(1, 6)]
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"
        assert "weekly cycle" in joined(verdict.caveats)

    def test_full_week_has_no_caveat(self):
        experiment = make_experiment()
        verdict = single_verdict(experiment, make_series(experiment))
        assert "weekly cycle" not in joined(verdict.caveats)


# ── guardrails (owner-ratified D5(c)) ─────────────────────────────────────────


def guardrail_experiment(policy: str = "block") -> ExperimentConfig:
    return make_experiment(
        readout={"guardrail_policy": policy},
        comparisons=[
            {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
            {
                "metric": "crashes",
                "is_guardrail": True,
                "desired_direction": "decrease",
                "method": {"name": "t-test"},
            },
        ],
    )


def regressed_guardrail_rows(experiment: ExperimentConfig):
    """Main metric wins; the 'crashes' guardrail significantly increases (harm)."""
    rows = make_series(experiment)
    rows += make_series(experiment, metric="crashes", effect=0.2, left_bound=0.1, right_bound=0.3)
    return rows


class TestGuardrails:
    def test_block_policy_caps_win_at_inconclusive(self):
        experiment = guardrail_experiment("block")
        verdict = single_verdict(experiment, regressed_guardrail_rows(experiment))
        assert verdict.verdict == "INCONCLUSIVE"
        assert "guardrail 'crashes' regressed" in joined(verdict.rationale)
        assert any(g.regressed for g in verdict.guardrails)

    def test_warn_policy_keeps_win_with_loud_caveat(self):
        experiment = guardrail_experiment("warn")
        verdict = single_verdict(experiment, regressed_guardrail_rows(experiment))
        assert verdict.verdict == "WIN"
        assert "guardrail 'crashes' regressed" in joined(verdict.caveats)

    def test_lose_is_never_blocked(self):
        experiment = guardrail_experiment("block")
        rows = make_series(experiment, effect=-0.1, left_bound=-0.15, right_bound=-0.05)
        rows += make_series(
            experiment, metric="crashes", effect=0.2, left_bound=0.1, right_bound=0.3
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "LOSE"
        assert "guardrail 'crashes' regressed" in joined(verdict.caveats)

    def test_guardrail_moving_in_desired_direction_is_not_regression(self):
        experiment = guardrail_experiment("block")
        rows = make_series(experiment)
        rows += make_series(  # crashes DOWN — desired for 'decrease'
            experiment, metric="crashes", effect=-0.2, left_bound=-0.3, right_bound=-0.1
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"
        assert not any(g.regressed for g in verdict.guardrails)

    def test_quiet_guardrail_is_not_regression(self):
        experiment = guardrail_experiment("block")
        rows = make_series(experiment)
        rows += make_series(
            experiment,
            metric="crashes",
            effect=0.001,
            left_bound=-0.05,
            right_bound=0.05,
            pvalue=0.9,
        )
        assert single_verdict(experiment, rows).verdict == "WIN"


# ── read-time Benjamini-Hochberg (D5(g)) ──────────────────────────────────────


class TestBenjaminiHochberg:
    def bh_experiment(self):
        return make_experiment(
            correction="benjamini_hochberg",
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "sessions", "method": {"name": "t-test"}},
                {"metric": "retention", "method": {"name": "t-test"}},
            ],
        )

    def test_bh_rescoring_can_reject_a_raw_significant_row(self):
        """p=0.04 < α=0.05 raw, but BH-adjusted across 3 metrics exceeds α."""
        experiment = self.bh_experiment()
        pvalues = {"revenue": 0.04, "sessions": 0.9, "retention": 0.85}
        adjusted = benjamini_hochberg([0.04, 0.9, 0.85])
        assert float(adjusted[0]) > 0.05  # the hand-computed premise: 0.04*3/1 = 0.12
        rows = []
        for metric, p in pvalues.items():
            rows += make_series(experiment, metric=metric, pvalue=p)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict != "WIN"
        assert not verdict.significant

    def test_bh_keeps_a_strongly_significant_row(self):
        experiment = self.bh_experiment()
        pvalues = {"revenue": 0.0001, "sessions": 0.9, "retention": 0.85}
        adjusted = benjamini_hochberg([0.0001, 0.9, 0.85])
        assert float(adjusted[0]) < 0.05
        rows = []
        for metric, p in pvalues.items():
            rows += make_series(experiment, metric=metric, pvalue=p)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"


# ── the NULL-MDE fallback (D5(b)) ─────────────────────────────────────────────


class TestMdeFallback:
    def test_ttest_fallback_matches_the_method_solve(self):
        """The read-time solve equals the method's own calculate_mde output."""
        stats_1 = SufficientStats(n=1000, mean=10.0, m2=4.0 * 1000, name="control")
        stats_2 = SufficientStats(n=900, mean=10.5, m2=4.4 * 900, name="treatment")
        method = create_method("t-test", alpha=0.05, params={"calculate_mde": True, "power": 0.8})
        result = method.from_suffstats(stats_1, stats_2)

        experiment = make_experiment()
        row = make_row(
            experiment,
            value_1=result.value_1,
            value_2=result.value_2,
            std_1=result.std_1,
            std_2=result.std_2,
            size_1=1000,
            size_2=900,
        )
        mde, reason = pair_mde(row)
        assert reason is None
        expected = max(abs(result.mde_1), abs(result.mde_2))
        assert mde == pytest.approx(expected, rel=1e-9)

    def test_ztest_fallback_inverts_nobs_from_the_persisted_se(self):
        """nobs > size_i (multi-trial units): the SE inversion must recover it."""
        # 500 units contributing 2000 trials per arm — size_i is the unit count.
        f1 = Fraction(count=400.0, nobs=2000.0, name="control")
        f2 = Fraction(count=460.0, nobs=2000.0, name="treatment")
        method = create_method("z-test", alpha=0.05, params={"calculate_mde": True, "power": 0.8})
        result = method.compare_pair(f1, f2)
        assert result.mde_1 is not None

        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "z-test"}}
            ]
        )
        row = make_row(
            experiment,
            value_1=result.value_1,
            value_2=result.value_2,
            std_1=result.std_1,
            std_2=result.std_2,
            size_1=500,  # unit rows — NOT nobs; the fallback must not use it
            size_2=500,
        )
        mde, reason = pair_mde(row)
        assert reason is None
        expected = max(abs(result.mde_1), abs(result.mde_2))
        assert mde == pytest.approx(expected, rel=1e-9)

    def test_stored_mde_wins_over_the_fallback(self):
        experiment = make_experiment()
        row = make_row(experiment, mde_1=0.03, mde_2=0.04)
        mde, reason = pair_mde(row)
        assert mde == pytest.approx(0.04)
        assert reason is None

    def test_ratio_delta_has_no_mde_and_flat_is_unreachable(self):
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "min_effect": 0.05,
                    "method": {"name": "ratio-delta"},
                }
            ]
        )
        rows = make_series(
            experiment,
            effect=0.001,
            left_bound=-0.01,
            right_bound=0.012,
            pvalue=0.8,
            reject=False,
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "no MDE capability" in joined(verdict.rationale)

    def test_cuped_without_calculate_mde_gets_the_hint(self):
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "min_effect": 0.05,
                    "method": {"name": "cuped-t-test", "params": {"covariate_lookback": "14d"}},
                }
            ]
        )
        rows = make_series(
            experiment,
            effect=0.001,
            left_bound=-0.01,
            right_bound=0.012,
            pvalue=0.8,
            reject=False,
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "calculate_mde" in joined(verdict.rationale)

    def test_degenerate_proportion_is_not_invertible(self):
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "z-test"}}
            ]
        )
        row = make_row(experiment, value_1=1.0, value_2=0.5, std_1=0.0, std_2=0.01)
        mde, reason = pair_mde(row)
        assert mde is None
        assert "not invertible" in reason


# ── multi-arm ─────────────────────────────────────────────────────────────────


class TestMultiArm:
    def test_verdicts_are_per_control_treatment_pair_only(self):
        experiment = make_experiment(
            assignment={
                "query": "SELECT 1",
                "variants": ["control", "t1", "t2"],
                "expected_split": {"control": 0.34, "t1": 0.33, "t2": 0.33},
            }
        )
        rows = []
        for treatment, effect in (("t1", 0.1), ("t2", -0.1)):
            bounds = (0.05, 0.15) if effect > 0 else (-0.15, -0.05)
            rows += [
                make_row(
                    experiment,
                    day=d,
                    name_2=treatment,
                    effect=effect,
                    left_bound=bounds[0],
                    right_bound=bounds[1],
                )
                for d in range(1, 15)
            ]
        # A treatment-vs-treatment row must not create a verdict.
        rows.append(make_row(experiment, day=14, name_1="t1", name_2="t2"))
        readout = evaluate(experiment, rows)
        pairs = {(v.name_1, v.name_2): v.verdict for v in readout.verdicts}
        assert pairs == {("control", "t1"): "WIN", ("control", "t2"): "LOSE"}


# ── hygiene: orphans, unconfigured metrics, config validation ────────────────


class TestRowFiltering:
    def test_orphaned_method_config_id_rows_are_ignored_with_warning(self):
        experiment = make_experiment()
        rows = make_series(experiment)
        rows += make_series(experiment, method_config_id="deadbeef" * 8)
        readout = evaluate(experiment, rows)
        assert readout.verdicts[0].verdict == "WIN"
        assert any("orphaned" in w for w in readout.warnings)

    def test_unconfigured_metric_rows_are_ignored_with_warning(self):
        experiment = make_experiment()
        rows = make_series(experiment)
        rows += make_series(experiment, metric="stale_metric")
        readout = evaluate(experiment, rows)
        assert any("stale_metric" in w for w in readout.warnings)


class TestConfigFields:
    def test_defaults(self):
        experiment = make_experiment()
        assert experiment.readout.stabilization_days == 7.0
        assert experiment.readout.guardrail_policy == "block"
        comparison = experiment.comparisons[0]
        assert comparison.min_effect is None
        assert comparison.desired_direction == "increase"

    def test_min_effect_must_be_positive(self):
        with pytest.raises(ValueError):
            make_experiment(
                comparisons=[
                    {
                        "metric": "revenue",
                        "is_main_metric": True,
                        "min_effect": -0.1,
                        "method": {"name": "t-test"},
                    }
                ]
            )

    def test_stabilization_days_must_be_positive(self):
        with pytest.raises(ValueError):
            make_experiment(readout={"stabilization_days": 0})

    def test_guardrail_policy_is_a_closed_enum(self):
        with pytest.raises(ValueError):
            make_experiment(readout={"guardrail_policy": "ignore"})

    def test_readout_fields_never_enter_method_config_id(self):
        plain = make_experiment()
        tuned = make_experiment(
            readout={"stabilization_days": 3, "guardrail_policy": "warn"},
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "min_effect": 0.02,
                    "desired_direction": "decrease",
                    "method": {"name": "t-test"},
                }
            ],
        )
        assert (
            plain.comparisons[0].method.method_config_id
            == tuned.comparisons[0].method.method_config_id
        )


class TestToDict:
    def test_readout_round_trips_to_plain_types(self):
        experiment = guardrail_experiment("block")
        readout = evaluate(experiment, regressed_guardrail_rows(experiment))
        payload = readout.to_dict()
        assert payload["experiment"] == experiment.name
        assert payload["verdicts"][0]["verdict"] == "INCONCLUSIVE"
        assert isinstance(payload["verdicts"][0]["rationale"], list)
        assert isinstance(payload["verdicts"][0]["guardrails"][0], dict)


# ── correction resolution (review finding: BH must never degrade silently) ───


def make_project(correction: str) -> ProjectConfig:
    return ProjectConfig(
        name="readout_proj", default_profile="dev", statistics={"correction": correction}
    )


class TestCorrectionResolution:
    def test_project_level_bh_is_rescored(self):
        """experiment.correction unset + project BH => BH rescoring applies."""
        experiment = make_experiment(
            correction=None,
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "sessions", "method": {"name": "t-test"}},
                {"metric": "retention", "method": {"name": "t-test"}},
            ],
        )
        rows = []
        for metric, p in (("revenue", 0.04), ("sessions", 0.9), ("retention", 0.85)):
            rows += make_series(experiment, metric=metric, pvalue=p)
        readout = evaluate(experiment, rows, project=make_project("benjamini_hochberg"))
        verdict = readout.verdicts[0]
        assert verdict.verdict != "WIN"  # BH-adjusted 0.04*3 = 0.12 > 0.05
        assert not verdict.significant
        assert not any("correction is unset" in w for w in readout.warnings)

    def test_unresolved_correction_warns_loudly(self):
        experiment = make_experiment(correction=None)
        readout = evaluate(experiment, make_series(experiment))
        assert any("correction is unset" in w for w in readout.warnings)

    def test_experiment_level_correction_needs_no_project(self):
        experiment = make_experiment()  # correction: none, set explicitly
        readout = evaluate(experiment, make_series(experiment))
        assert not any("correction is unset" in w for w in readout.warnings)


# ── review-finding branch coverage ────────────────────────────────────────────


class TestBhEdgeCases:
    def bh_experiment(self):
        return make_experiment(correction="benjamini_hochberg")

    def test_null_pvalue_rows_are_never_significant_under_bh(self):
        experiment = self.bh_experiment()
        rows = make_series(experiment, pvalue=None)
        verdict = single_verdict(experiment, rows)
        assert not verdict.significant
        assert verdict.verdict == "INCONCLUSIVE"

    def test_zero_effect_significance_is_demoted_under_bh(self):
        """A tiny p-value with effect exactly 0 has no sign — never significant."""
        experiment = self.bh_experiment()
        rows = make_series(experiment, pvalue=1e-6, effect=0.0)
        verdict = single_verdict(experiment, rows)
        assert not verdict.significant
        assert verdict.verdict != "WIN"


class TestGuardrailEdgeCases:
    def test_all_demoted_guardrail_series_is_not_regression(self):
        experiment = guardrail_experiment("block")
        rows = make_series(experiment)
        rows += make_series(
            experiment,
            metric="crashes",
            insufficient_data=True,
            pvalue=None,
            effect=None,
            left_bound=None,
            right_bound=None,
            reject=None,
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"
        assert not any(g.regressed for g in verdict.guardrails)

    def test_warn_policy_on_lose_still_carries_the_caveat(self):
        experiment = guardrail_experiment("warn")
        rows = make_series(experiment, effect=-0.1, left_bound=-0.15, right_bound=-0.05)
        rows += make_series(
            experiment, metric="crashes", effect=0.2, left_bound=0.1, right_bound=0.3
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "LOSE"
        assert "regressed" in joined(verdict.caveats)

    def test_guardrail_regression_is_correction_independent_under_bh(self):
        """D5(c): regression = the STORED CI excludes zero against the desired
        direction — BH adjustment (which only inflates p-values) must never
        un-flag a stored-significant harm (milestone-review finding). Here the
        crashes harm (raw p=0.04, CI [0.01, 0.19]) BH-adjusts to 0.06 > α
        behind the quiet 'sessions' secondary, but must still cap the WIN."""
        experiment = make_experiment(
            correction="benjamini_hochberg",
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "sessions", "method": {"name": "t-test"}},
                {
                    "metric": "crashes",
                    "is_guardrail": True,
                    "desired_direction": "decrease",
                    "method": {"name": "t-test"},
                },
            ],
        )
        rows = make_series(experiment, pvalue=1e-4)  # the main WIN
        rows += make_series(
            experiment,
            metric="sessions",
            effect=0.001,
            left_bound=-0.05,
            right_bound=0.05,
            pvalue=0.9,
            reject=False,
        )
        rows += make_series(
            experiment,
            metric="crashes",
            effect=0.10,
            left_bound=0.01,
            right_bound=0.19,
            pvalue=0.04,
        )
        verdict = single_verdict(experiment, rows)
        assert any(g.regressed for g in verdict.guardrails)
        assert verdict.verdict == "INCONCLUSIVE"  # block (default) caps the WIN


class TestFlatCaveats:
    def test_flat_on_short_horizon_carries_the_weekly_caveat(self):
        experiment = make_experiment(
            end_date="2026-01-05",
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "min_effect": 0.05,
                    "method": {"name": "t-test"},
                }
            ],
        )
        rows = [
            make_row(
                experiment,
                day=d,
                is_horizon=(d == 5),
                effect=0.001,
                left_bound=-0.01,
                right_bound=0.012,
                pvalue=0.8,
                reject=False,
                mde_1=0.02,
                mde_2=0.02,
            )
            for d in range(1, 6)
        ]
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "FLAT"
        assert "weekly cycle" in joined(verdict.caveats)


class TestMdeHalfStored:
    """Review finding: a half-present stored MDE pair hides an infinite arm."""

    def experiment_with_mde(self, method_name: str = "t-test", **params):
        return make_experiment(
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "min_effect": 0.05,
                    "method": {"name": method_name, "params": {"calculate_mde": True, **params}},
                }
            ]
        )

    def test_half_stored_pair_recovers_the_infinite_arm(self):
        """mde_1=NULL (was inf: std_1=0), mde_2 finite — the pair MDE must be
        inf via the fallback, not the finite arm alone."""
        experiment = self.experiment_with_mde()
        row = make_row(experiment, std_1=0.0, mde_1=None, mde_2=0.03)
        mde, reason = pair_mde(row)
        assert reason is None
        assert math.isinf(mde)

    def test_half_stored_pair_blocks_flat(self):
        experiment = self.experiment_with_mde()
        rows = make_series(
            experiment,
            std_1=0.0,
            mde_1=None,
            mde_2=0.03,
            effect=0.001,
            left_bound=-0.01,
            right_bound=0.012,
            pvalue=0.8,
            reject=False,
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "underpowered" in joined(verdict.rationale)

    def test_cuped_calculate_mde_true_with_null_columns_reads_as_inf(self):
        """No fallback exists for CUPED, but calculate_mde: true proves the
        NULL columns were non-finite solves — inf, and no misleading advice."""
        experiment = self.experiment_with_mde("cuped-t-test", covariate_lookback="14d")
        row = make_row(experiment, mde_1=None, mde_2=None)
        mde, reason = pair_mde(row)
        assert reason is None
        assert math.isinf(mde)

    def test_unknown_method_is_reported(self):
        experiment = make_experiment()
        row = make_row(experiment, method_name="no-such-method")
        mde, reason = pair_mde(row)
        assert mde is None
        assert "unknown method" in reason


class TestSrmSummary:
    def test_flag_survives_an_empty_main_series(self):
        """SRM must stay loud when the main series is empty under its CURRENT
        method_config_id (exactly the state an explore Apply that edits the
        main method produces) while another series carries flagged rows —
        §6 must-fix, milestone-review finding."""
        experiment = guardrail_experiment()
        rows = make_series(
            experiment,
            metric="crashes",
            srm_flag=True,
            decision_blocked=True,
            srm_pvalue=1e-6,
        )  # and NO revenue (main) rows at all
        flag, pvalue = srm_summary(experiment, rows)
        assert flag is True
        assert pvalue == pytest.approx(1e-6)

    def test_pvalue_comes_from_a_flagged_row(self):
        """Two mains: metric A lags (healthy latest row), metric B's latest is
        flagged — the summary must pair srm_flag=True with the FLAGGED p."""
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "signups", "is_main_metric": True, "method": {"name": "t-test"}},
            ]
        )
        rows = make_series(experiment, days=10, srm_pvalue=0.8)  # lagging, healthy
        rows += make_series(
            experiment,
            metric="signups",
            srm_flag=True,
            decision_blocked=True,
            srm_pvalue=1e-6,
        )
        readout = evaluate(experiment, rows)
        assert readout.srm_flag is True
        assert readout.srm_pvalue == pytest.approx(1e-6)

    def test_public_srm_summary_matches_evaluate_and_is_window_independent(self):
        """The extracted `srm_summary` (the report's window-independent SRM
        chip source) agrees with evaluate's experiment-level SRM, and reflects
        exactly the rows it is handed."""
        experiment = make_experiment()
        rows = make_series(experiment, days=13, srm_pvalue=0.8)
        rows.append(make_row(experiment, day=14, srm_flag=True, srm_pvalue=1e-6))

        readout = evaluate(experiment, rows)
        assert srm_summary(experiment, rows) == (readout.srm_flag, readout.srm_pvalue)

        # given only the healthy early rows it returns what those carry; given
        # the full set it sees the failing horizon row
        assert srm_summary(experiment, rows[:5]) == (False, pytest.approx(0.8))
        assert srm_summary(experiment, rows) == (True, pytest.approx(1e-6))
