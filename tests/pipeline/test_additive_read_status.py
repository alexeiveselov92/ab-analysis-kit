"""PERF-1: the hint that breaks the m9 fast path's silence.

`AdditiveReadStatus.hint()` is pure — no warehouse, no config — so the rules
about WHEN abkit nags are pinned here, and the driver tests below pin that the
facts it reasons over are collected truthfully.
"""

from __future__ import annotations

import pytest
from synthetic_ab import (
    CONVERSION,
    METRICS,
    NOW,
    PROJECT,
    SyntheticWarehouse,
    experiment_payload,
    seed_all_events,
    seed_cohort,
)

from abkit.config import ExperimentConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.pipeline import run_experiment
from abkit.pipeline._types import AdditiveReadStatus


def _undecided(**overrides) -> AdditiveReadStatus:
    """A project that never mentioned the flag and has an additive metric."""
    status = AdditiveReadStatus(
        enabled=False,
        declared=False,
        eligible_comparisons=1,
        total_comparisons=2,
        series_looks=14,
        looks_computed=14,
    )
    for key, value in overrides.items():
        setattr(status, key, value)
    return status


class TestTheUndecidedProjectIsNagged:
    """The defect PERF-1 exists for: the scaffold paid the STATE write and
    never took the read, and nothing ever said so."""

    def test_hint_names_the_flag_the_counts_and_both_ways_out(self):
        hint = _undecided().hint()
        assert hint is not None
        assert "compute.incremental_reads" in hint
        assert "1 of 2 comparisons" in hint
        assert "14 looks" in hint
        # both exits are offered: taking the path, and recording the refusal
        assert "true" in hint and "false" in hint
        # the empirical check is named, not just asserted
        assert "verify-incremental" in hint

    def test_no_additive_comparison_no_hint(self):
        """Nothing to offer: the STATE stage writes nothing for this project,
        so there is no wasted write to complain about."""
        assert _undecided(eligible_comparisons=0).hint() is None

    @pytest.mark.parametrize("looks", [0, 1, 5])
    def test_short_series_stays_quiet(self, looks):
        """cumulative-intervals §4.1: below the threshold the two paths are
        within noise, so a nag would be noise too."""
        assert _undecided(series_looks=looks).hint() is None

    def test_the_threshold_is_the_documented_one(self):
        assert AdditiveReadStatus.MIN_LOOKS_TO_MATTER == 6
        assert _undecided(series_looks=6).hint() is not None


class TestADeclaredChoiceIsRespected:
    """The nag has to terminate, or it is just a different kind of noise —
    an explicit `false` is a decision, not an omission."""

    def test_explicit_false_silences_it(self):
        assert _undecided(declared=True).hint() is None

    def test_flag_on_and_working_says_nothing(self):
        status = _undecided(enabled=True, declared=True)
        assert status.hint() is None


class TestTheOtherTwoSilences:
    def test_flag_on_but_nothing_is_additive(self):
        """The mirror-image incoherence: the operator asked for the fast path
        and silently gets none of it, because no metric declares itself."""
        hint = _undecided(enabled=True, declared=True, eligible_comparisons=0).hint()
        assert hint is not None
        assert "state_additive" in hint

    def test_no_comparisons_at_all_is_not_a_complaint(self):
        """A run that computed nothing (an unmatched --metric filter) must not
        claim the flag is doing nothing."""
        status = AdditiveReadStatus(enabled=True, declared=True, total_comparisons=0)
        assert status.hint() is None

    def test_fallbacks_report_their_extent(self):
        """The reader's own warnings name the REASON but are deduped per
        (metric, reason), so only this can say how many looks paid for it."""
        hint = _undecided(enabled=True, declared=True, fallbacks=3, looks_computed=14).hint()
        assert hint is not None
        assert "3 of 14 looks" in hint


T_TEST = {"name": "t-test", "params": {"test_type": "relative"}}
Z_TEST = {"name": "z-test", "params": {"test_type": "relative"}}
#: each synthetic metric's kind decides its method (a fraction metric
#: refuses a t-test at analyze time)
_METHOD = {"arpu": T_TEST, "conversion": Z_TEST}
#: the same metric, with its day-additivity promise withdrawn — the ONLY
#: difference from `METRICS["conversion"]`, so any cost attributed to it is
#: attributed by eligibility and nothing else
NOT_ADDITIVE = CONVERSION.model_copy(update={"state_additive": False})


def _run(metrics: list[str], metrics_by_name: dict) -> tuple[int, int]:
    """Run one experiment over `metrics`; return (COMPUTE scan, additive scan)."""
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=20)
    seed_all_events(warehouse, days=4)
    payload = experiment_payload("attribution", metrics[0], T_TEST)
    payload["comparisons"] = [
        {"metric": name, "is_main_metric": i == 0, "method": _METHOD[name]}
        for i, name in enumerate(metrics)
    ]
    outcome = run_experiment(
        ExperimentConfig.model_validate(payload),
        metrics_by_name,
        PROJECT,
        warehouse,
        InternalTablesManager(warehouse),
        now_utc=NOW,
    )
    assert outcome.status == "completed", outcome.error
    slice_cost = outcome.stage_costs.get("compute.additive")
    return (
        outcome.stage_costs["compute"].queries.scanned_rows,
        slice_cost.queries.scanned_rows if slice_cost else 0,
    )


class TestTheSliceIsAttributedNotJustPresent:
    """`compute.additive` must hold the eligible comparisons' cost and ONLY
    theirs — it is what `--cost-report` calls the counterfactual, so an
    implementation that counts every comparison overstates what the fast path
    would move off the fact table.

    Asserted on scanned ROWS (the synthetic warehouse reports them) rather than
    on rendered wall-clock: a timing comparison passes against exactly that
    implementation, because whether the slice reads lower than the stage total
    is then an accident of which metric happens to be slower.
    """

    def _mixed(self) -> dict:
        return {**METRICS, "conversion": NOT_ADDITIVE}

    def test_the_slice_holds_the_additive_metric_alone(self):
        additive_only, _ = _run(["arpu"], METRICS)
        total, sliced = _run(["arpu", "conversion"], self._mixed())

        assert sliced == additive_only, "the slice must cost exactly what `arpu` alone costs"
        assert total > sliced, "the non-additive comparison must be outside the slice"

    def test_a_run_with_nothing_additive_has_no_slice(self):
        total, sliced = _run(["conversion"], self._mixed())
        assert total > 0
        assert sliced == 0

    def test_the_slice_is_a_subset_so_the_stage_total_survives(self):
        """The sibling defect: recording an eligible look into the slice
        INSTEAD of the stage would leave `compute` empty on an all-additive
        run."""
        total, sliced = _run(["arpu"], METRICS)
        assert total == sliced > 0
