"""m13 STAT-3 — ``interval: score`` through the real pipeline, the readout and A/A.

The unit tests prove the estimator; this proves the ROW. A confidence interval only
matters after it has been persisted, read back and turned into a verdict, and the
three consequences worth pinning are all downstream of the math:

* the bounds survive the round trip (a non-finite bound would be cleaned to NULL and
  the row would silently stop being informative);
* the verdict is the one the interval implies, so a decision cannot disagree with
  what the report draws beside it;
* ``abk validate`` — the instrument the change-control process names for exactly this
  kind of deviation — can still score the cell.
"""

from __future__ import annotations

import math

import pytest
from synthetic_ab import (
    METRICS,
    PROJECT,
    SyntheticWarehouse,
    make_experiment,
    persisted,
    run_pipeline,
    seed_all_events,
    seed_cohort,
)

from abkit.database.internal_tables import InternalTablesManager
from abkit.pipeline.readout import evaluate

POOLED = {"name": "z-test", "params": {"test_type": "absolute"}}
SCORE = {"name": "z-test", "params": {"test_type": "absolute", "interval": "score"}}


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_all_events(wh)
    return wh


def _run(warehouse, method, name):
    tables = InternalTablesManager(warehouse)
    experiment = make_experiment(name, "conversion", method, min_effect=0.001)
    run_pipeline(warehouse, tables, experiment)
    return experiment, tables, persisted(tables, experiment, "conversion")


def test_a_score_series_persists_finite_asymmetric_bounds(warehouse):
    """Every look, both bounds finite, and asymmetric about the estimate on at least
    one of them — the property that would be lost if the pooled branch were taken by
    accident (identical numbers would pass every other assertion in this file)."""
    _, _, rows = _run(warehouse, SCORE, "score_exp")
    assert rows
    asymmetric_looks = 0
    for row in rows.values():
        left, right = row["left_bound"], row["right_bound"]
        assert left is not None and right is not None
        assert math.isfinite(left) and math.isfinite(right)
        assert -1.0 <= left <= row["effect"] <= right <= 1.0
        below, above = row["effect"] - left, right - row["effect"]
        if abs(above - below) > 1e-12 * max(above, below):
            asymmetric_looks += 1
    assert asymmetric_looks == len(rows), "a score interval is never centred"


def test_the_p_values_of_the_two_series_are_identical(warehouse):
    """The milestone's headline claim, at the level an operator can check: two runs of
    the same data differing only in `interval` produce the same p-value at every
    look. The bounds differ — asserted, so this cannot pass by the runs being the
    same run."""
    _, _, pooled_rows = _run(warehouse, POOLED, "pooled_exp")

    second = SyntheticWarehouse()
    seed_cohort(second)
    seed_all_events(second)
    _, _, score_rows = _run(second, SCORE, "score_exp")

    assert set(pooled_rows) == set(score_rows)
    moved = 0
    for key, pooled in pooled_rows.items():
        score = score_rows[key]
        assert score["pvalue"] == pooled["pvalue"], key
        assert score["effect"] == pooled["effect"], key
        assert score["reject"] == pooled["reject"], key
        if score["right_bound"] != pooled["right_bound"]:
            moved += 1
    assert moved == len(pooled_rows), "every interval must move — else nothing was tested"


def test_the_readout_reads_the_score_series_and_agrees_with_its_own_interval(warehouse):
    """The verdict is `readout.evaluate()`'s over the persisted rows, and under a
    compute-time correction its significance rule IS "the CI excludes zero". A row
    whose stored bounds and stored `reject` disagreed would make the verdict
    contradict the interval drawn beside it — which is exactly the coherence the score
    construction was chosen to preserve."""
    experiment, tables, rows = _run(warehouse, SCORE, "score_readout")
    readout = evaluate(experiment, tables.load_results(experiment.name), project=PROJECT)

    assert readout.verdicts
    for row in rows.values():
        excludes_zero = row["left_bound"] > 0 or row["right_bound"] < 0
        assert excludes_zero == bool(row["reject"]), row["end_ts"]
    # and nothing was dropped as uninformative on the way in: a verdict that saw no
    # informative row answers INCONCLUSIVE with a 'no computed results' rationale
    assert all(v.effect is not None for v in readout.verdicts)


def test_abk_validate_can_still_score_a_score_interval_cell(warehouse):
    """The change-control instrument, on the shipped configuration.

    ``_cell_tau2`` runs unconditionally at the top of both scoring engines, so an
    unguarded CI-inversion there fails EVERY cell — and a failed cell carries no FPR,
    which means explore's D3 calibration chip could never leave `uncalibrated` and the
    command it names could never clear it. The fixed columns must survive; only the
    always-valid column is legitimately absent.
    """
    from abkit.compute.recompute_backend import RecomputeBackend
    from abkit.validate.runner import ValidateSettings, run_validation

    experiment = make_experiment("aa_score", "conversion", SCORE)
    tables = InternalTablesManager(warehouse)
    run_pipeline(warehouse, tables, experiment)

    result = run_validation(
        RecomputeBackend(warehouse, experiment),
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        experiment.grid(),
        ValidateSettings(iterations=60),
        now_iso="2026-08-05T00:00:00",
    )

    cell = result.cells[0]
    assert cell.status == "success", cell.error_message
    assert cell.fpr is not None
    assert cell.fpr_sequential is None  # the one column an asymmetric interval loses
    assert "asymmetric" in str(cell.details.get("warnings", ""))
