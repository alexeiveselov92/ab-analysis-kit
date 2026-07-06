"""WP4 — the readout under sequential (always-valid) CIs.

The pre-horizon withholding branch (``readout.py``) refuses WIN/LOSE/FLAT before
the planned horizon only when ``ci_kind == 'fixed'`` — an always-valid confidence
sequence is peeking-safe by construction, so the readout may call it early. These
known-answer tables pin that the relaxation is keyed off the **persisted row's**
``ci_kind`` (not the live config), that an early decisive verdict names the reason
and carries the weekly-cycle representativeness signal, and that a fixed row is
still withheld (with the M5-shipped wording, no "lands in M5" placeholder).

Governing contract: data-contract-and-reporting.md §6.5; m5-implementation-plan.md
WP4.
"""

from __future__ import annotations

from tests.pipeline.test_readout import (
    joined,
    make_experiment,
    make_row,
    make_series,
    single_verdict,
)


def _av_rows(experiment, *, effect, left, right, days=range(1, 6)):
    """A daily always-valid series (pre-horizon: none is the horizon)."""
    return [
        make_row(
            experiment,
            day=d,
            ci_kind="always_valid",
            is_horizon=False,
            effect=effect,
            left_bound=left,
            right_bound=right,
        )
        for d in days
    ]


class TestAlwaysValidLiftsPreHorizonWithholding:
    def test_always_valid_win_allowed_before_horizon(self):
        experiment = make_experiment(sequential={"enabled": True})  # 14-day horizon
        rows = _av_rows(experiment, effect=0.1, left=0.05, right=0.15)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"
        assert not verdict.is_horizon
        # The early call names its own justification (WP4 wording).
        assert "always-valid" in joined(verdict.rationale)
        assert "peeking-safe" in joined(verdict.rationale)
        # …and never claims sequential is still unshipped.
        assert "land" not in joined(verdict.rationale)

    def test_always_valid_lose_allowed_before_horizon(self):
        experiment = make_experiment(sequential={"enabled": True})
        rows = _av_rows(experiment, effect=-0.1, left=-0.15, right=-0.05)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "LOSE"
        assert not verdict.is_horizon
        assert "always-valid" in joined(verdict.rationale)

    def test_relaxation_is_driven_by_the_row_not_the_config(self):
        """A not-yet-applied config (sequential OFF) but always-valid rows on
        disk must still read early — the readout is a view of persisted rows."""
        experiment = make_experiment()  # sequential disabled in config
        rows = _av_rows(experiment, effect=0.1, left=0.05, right=0.15)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"

    def test_at_horizon_always_valid_win_has_no_early_note(self):
        experiment = make_experiment(sequential={"enabled": True})
        rows = [
            make_row(experiment, day=d, ci_kind="always_valid", is_horizon=(d == 14))
            for d in range(1, 15)
        ]
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"
        assert verdict.is_horizon
        # The "called before the planned horizon" note only fires pre-horizon.
        assert "before the planned horizon" not in joined(verdict.rationale)


class TestFixedStillWithheld:
    def test_fixed_pre_horizon_is_withheld_with_shipped_wording(self):
        experiment = make_experiment()
        rows = make_series(experiment, days=10)  # horizon is day 14, all fixed
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        joined_rationale = joined(verdict.rationale)
        assert "pre-horizon" in joined_rationale
        # WP4: the M3 "sequential CIs land in M5" placeholder is gone; the message
        # now points at the shipped toggle.
        assert "land in M5" not in joined_rationale
        assert "sequential" in joined_rationale

    def test_sequential_enabled_but_fixed_rows_carries_the_updated_caveat(self):
        experiment = make_experiment(sequential={"enabled": True})
        rows = make_series(experiment, days=10)  # fixed rows (e.g. bootstrap / pre-anchor)
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        caveat = joined(verdict.caveats)
        assert "sequential.enabled is set" in caveat
        assert "not sequential-eligible" in caveat
        assert "lands in M5" not in caveat


class TestWeeklyCyclePct:
    def test_early_always_valid_verdict_sets_weekly_cycle_pct(self):
        experiment = make_experiment(sequential={"enabled": True})
        rows = _av_rows(experiment, effect=0.1, left=0.05, right=0.15)  # latest day 5
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"
        assert verdict.weekly_cycle_pct is not None
        assert abs(verdict.weekly_cycle_pct - 5.0 / 7.0) < 1e-9
        # The structured field and the human caveat agree (both still present).
        assert "weekly cycle" in joined(verdict.caveats)

    def test_full_week_always_valid_has_no_weekly_cycle_pct(self):
        experiment = make_experiment(sequential={"enabled": True})
        rows = [
            make_row(experiment, day=d, ci_kind="always_valid", is_horizon=(d == 14))
            for d in range(1, 15)
        ]
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "WIN"
        assert verdict.weekly_cycle_pct is None

    def test_inconclusive_verdict_has_no_weekly_cycle_pct(self):
        experiment = make_experiment()
        rows = make_series(experiment, days=10)  # fixed → withheld INCONCLUSIVE
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert verdict.weekly_cycle_pct is None


def _seq_flat_experiment():
    return make_experiment(
        sequential={"enabled": True},
        comparisons=[
            {
                "metric": "revenue",
                "is_main_metric": True,
                "min_effect": 0.05,
                "method": {"name": "t-test"},
            }
        ],
    )


class TestFlatUnderSequentialUsesTheAlwaysValidInterval:
    """M5 exit-gate round-1 fix: FLAT's 'adequately powered' claim must be judged against
    the wider always-valid interval actually reported, not the fixed-horizon MDE (which
    ``pair_mde`` leaves untouched on an always-valid row)."""

    def test_wide_always_valid_ci_is_inconclusive_despite_a_small_fixed_mde(self):
        experiment = _seq_flat_experiment()
        # all-quiet; the persisted (fixed) MDE 0.02 <= min_effect 0.05 would have called
        # FLAT under the old rule, but the always-valid half-width is 0.06 > 0.05.
        rows = make_series(
            experiment,
            ci_kind="always_valid",
            effect=0.001,
            left_bound=-0.06,
            right_bound=0.06,
            pvalue=0.8,
            reject=False,
            mde_1=0.02,
            mde_2=0.02,
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "INCONCLUSIVE"
        assert "always-valid" in joined(verdict.rationale)
        assert "half-width" in joined(verdict.rationale)

    def test_tight_always_valid_ci_is_flat(self):
        experiment = _seq_flat_experiment()
        rows = make_series(
            experiment,
            ci_kind="always_valid",
            effect=0.001,
            left_bound=-0.04,
            right_bound=0.04,  # half-width 0.04 <= min_effect 0.05
            pvalue=0.8,
            reject=False,
            mde_1=0.02,
            mde_2=0.02,
        )
        verdict = single_verdict(experiment, rows)
        assert verdict.verdict == "FLAT"
        assert "always-valid interval rules out" in joined(verdict.rationale)
