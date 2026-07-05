"""WP3 persistence: records satisfy save_aa_run and flip the D3 calibration chip."""

from __future__ import annotations

import pytest
from synthetic_ab import (
    METRICS,
    PROJECT,
    SyntheticWarehouse,
    make_experiment,
    seed_cohort,
    seed_null_events,
)

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.core.period_planner import generate_grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._aa_runs import AA_RUN_COLUMNS
from abkit.pipeline.analyze import comparison_alpha, effective_alphas
from abkit.tuning.recompute import find_calibration, resolve_fpr_budget
from abkit.validate.persistence import aa_run_records
from abkit.validate.runner import ValidateSettings, run_validation

NOW_ISO = "2026-07-05T00:00:00"


def _run(experiment, warehouse, **settings):
    backend = RecomputeBackend(warehouse, experiment)
    grid = generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        tz=experiment.timezone,
    )
    return run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        grid,
        ValidateSettings(iterations=400, **settings),
        now_iso=NOW_ISO,
    )


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh, n_per_arm=160)
    seed_null_events(wh)
    return wh


def test_records_have_every_column_and_unique_run_ids(warehouse):
    experiment = make_experiment("aa_persist", "arpu", {"name": "t-test"})
    result = _run(experiment, warehouse)
    records = aa_run_records(result)
    assert len(records) == 1
    for record in records:
        assert set(AA_RUN_COLUMNS).issubset(record)  # save_aa_run requires every key
    # run_ids encode the cell -> unique per (invocation, cell)
    assert len({r["run_id"] for r in records}) == len(records)
    assert result.run_stamp in records[0]["run_id"]


def test_persisted_alpha_byte_matches_effective_alpha(warehouse):
    experiment = make_experiment("aa_alpha", "arpu", {"name": "t-test"}, alpha=0.05)
    result = _run(experiment, warehouse)
    record = aa_run_records(result)[0]
    expected = comparison_alpha(experiment.comparisons[0], effective_alphas(experiment, PROJECT))
    assert record["alpha"] == expected  # exact -> find_calibration's isclose(rel 1e-9) matches


def test_saved_rows_flip_the_calibration_chip(warehouse):
    experiment = make_experiment("aa_chip", "arpu", {"name": "t-test"})
    tables = InternalTablesManager(warehouse)
    tables.ensure_tables()

    # before: uncalibrated
    method_config_id = experiment.comparisons[0].method.method_config_id
    alpha = comparison_alpha(experiment.comparisons[0], effective_alphas(experiment, PROJECT))
    budget = resolve_fpr_budget(PROJECT, alpha)
    before = find_calibration(
        tables.get_aa_runs("aa_chip"), "arpu", method_config_id, alpha, budget
    )
    assert before.state == "uncalibrated"

    # write the validate rows
    for record in aa_run_records(_run(experiment, warehouse)):
        tables.save_aa_run(record)

    # after: calibrated (a real FPR ≈ α on the null fixture, within budget)
    after = find_calibration(tables.get_aa_runs("aa_chip"), "arpu", method_config_id, alpha, budget)
    assert after.state == "calibrated"
    assert after.fpr is not None

    # an alpha edit downgrades to alpha_mismatch (gates like uncalibrated, D3)
    mism = find_calibration(tables.get_aa_runs("aa_chip"), "arpu", method_config_id, 0.01, 0.015)
    assert mism.state == "alpha_mismatch"


def test_failed_cell_is_persisted_but_never_calibrates(warehouse):
    # the CTR fixture is degenerate over a 4-day window -> a status='success' row with
    # fpr=None (never counts) ; ratio-delta has no analytic MDE
    experiment = make_experiment("aa_ratio", "ctr", {"name": "ratio-delta"})
    tables = InternalTablesManager(warehouse)
    tables.ensure_tables()
    result = _run(experiment, warehouse)
    for record in aa_run_records(result):
        tables.save_aa_run(record)
    method_config_id = experiment.comparisons[0].method.method_config_id
    status = find_calibration(tables.get_aa_runs("aa_ratio"), "ctr", method_config_id, 0.05, 0.075)
    assert status.state == "uncalibrated"  # fpr is None -> never counts
