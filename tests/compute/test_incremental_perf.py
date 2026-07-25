"""The M9 executable perf gate (m9-implementation-plan.md §7).

The milestone exists to kill the O(D²) fact-table rescan, and the track's own
lesson is that *a performance rule with no executable test does not hold*
(M7's 800k-iteration loop slipped past "numpy-first" for exactly that
reason). So the claim is asserted here, on **fact rows scanned** — the
quantity the claim is actually about — not wall-clock, which is noisy in CI
and would measure the fake warehouse's Python overhead rather than the
strategy.

What the numbers mean (N units, D days, daily cadence):

- **recompute** re-reads the whole cumulative window at every cutoff, so its
  COMPUTE stage scans ``N · D(D+1)/2`` fact rows — quadratic in D.
- **incremental** reads pre-aggregated day state at COMPUTE and touches raw
  facts only in the STATE stage (one closed day each), so fact scans are
  ``N · D`` for the whole run — linear in D, and ZERO inside COMPUTE at
  daily cadence (a sub-day grid adds at most the current day's tail, §6.4).
"""

from __future__ import annotations

import pytest
from synthetic_ab import (
    METRICS,
    NOW,
    PROJECT,
    SyntheticWarehouse,
    experiment_payload,
    seed_all_events,
    seed_cohort,
)

from abkit.config import ExperimentConfig, ProjectConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.pipeline import run_experiment

T_TEST = {"name": "t-test", "params": {"test_type": "relative"}}
UNITS_PER_ARM = 20
FACT_TABLE = "user_revenue"

PROJECT_INCREMENTAL = ProjectConfig.model_validate(
    {"name": "p", "default_profile": "dev", "compute": {"incremental_reads": True}}
)


def _run_series(days: int, project: ProjectConfig) -> tuple[int, int]:
    """Run a D-day experiment; return (fact rows scanned overall, in COMPUTE)."""
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse, n_per_arm=UNITS_PER_ARM)
    seed_all_events(warehouse, days=days)
    tables = InternalTablesManager(warehouse)
    payload = experiment_payload(f"perf_{days}", "arpu", T_TEST)
    # horizon_ts is EXCLUSIVE: a D-day window starting July 1 ends at July 1+D
    payload["horizon_ts"] = f"2024-07-{days + 1:02d}"
    experiment = ExperimentConfig.model_validate(payload)

    outcome = run_experiment(experiment, METRICS, project, warehouse, tables, now_utc=NOW)
    assert outcome.status == "completed", outcome.error
    compute_scans = outcome.stage_costs["compute"].queries.scanned_rows
    return warehouse.scanned_by_table.get(FACT_TABLE, 0), compute_scans


class TestFactScanCost:
    def test_incremental_scans_strictly_fewer_facts(self):
        recompute_total, recompute_compute = _run_series(4, PROJECT)
        incremental_total, incremental_compute = _run_series(4, PROJECT_INCREMENTAL)

        assert incremental_total < recompute_total
        # the COMPUTE stage stops touching the fact table entirely at daily
        # cadence — it reads day state instead
        assert recompute_compute > 0
        assert incremental_compute == 0

    def test_recompute_is_quadratic_and_incremental_is_linear_in_days(self):
        """The shape, not just the level: doubling D must roughly quadruple
        recompute's COMPUTE-stage scans while merely doubling the incremental
        path's total fact reads."""
        _, recompute_4 = _run_series(4, PROJECT)
        _, recompute_8 = _run_series(8, PROJECT)
        incremental_4, _ = _run_series(4, PROJECT_INCREMENTAL)
        incremental_8, _ = _run_series(8, PROJECT_INCREMENTAL)

        units = UNITS_PER_ARM * 2
        # N · D(D+1)/2, exactly — one event per unit per day in the fixture
        assert recompute_4 == units * 4 * 5 // 2
        assert recompute_8 == units * 8 * 9 // 2
        assert recompute_8 / recompute_4 == pytest.approx(3.6)

        # N · D, exactly: each closed day is rendered once, ever
        assert incremental_4 == units * 4
        assert incremental_8 == units * 8
        assert incremental_8 / incremental_4 == pytest.approx(2.0)

    def test_the_win_grows_with_the_series(self):
        recompute_total_4, _ = _run_series(4, PROJECT)
        incremental_total_4, _ = _run_series(4, PROJECT_INCREMENTAL)
        recompute_total_8, _ = _run_series(8, PROJECT)
        incremental_total_8, _ = _run_series(8, PROJECT_INCREMENTAL)

        ratio_4 = recompute_total_4 / incremental_total_4
        ratio_8 = recompute_total_8 / incremental_total_8
        assert ratio_8 > ratio_4  # the longer the experiment, the bigger the saving
