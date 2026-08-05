"""m13 STAT-4 — ``interval: fieller`` through the real pipeline and the readout.

The unit tests prove the estimator; this proves the ROW. Its shape deliberately
mirrors `test_score_interval_end_to_end.py`, because the two WPs make opposite
claims about the same column and the pair is only convincing side by side:
STAT-3's headline is "no p-value moves", STAT-4's is "the relative p-value
becomes the absolute comparison's" — asserted here against a real persisted
series rather than against a hand-built moment tuple.

The last test pins a DISCLOSED limitation rather than a guarantee. It is here so
the limitation is executable: prose in a spec cannot fail a build.
"""

from __future__ import annotations

import math

import pytest
from synthetic_ab import (
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

DELTA = {"name": "t-test", "params": {"test_type": "relative"}}
FIELLER = {"name": "t-test", "params": {"test_type": "relative", "interval": "fieller"}}
ABSOLUTE = {"name": "t-test", "params": {"test_type": "absolute"}}


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_all_events(wh)
    return wh


def _run(warehouse, method, name):
    tables = InternalTablesManager(warehouse)
    experiment = make_experiment(name, "arpu", method, min_effect=0.001)
    run_pipeline(warehouse, tables, experiment)
    return experiment, tables, persisted(tables, experiment, "arpu")


def _fresh():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_all_events(wh)
    return wh


def test_a_fieller_series_persists_finite_asymmetric_bounds(warehouse):
    """Every look bounded, and off-centre on every one of them.

    The asymmetry count is asserted as `== len(rows)` rather than `> 0`: a run
    that had silently taken the delta branch would produce perfectly centred
    intervals and pass any weaker form of this.
    """
    _, _, rows = _run(warehouse, FIELLER, "fieller_exp")
    assert rows
    off_centre = 0
    for row in rows.values():
        left, right = row["left_bound"], row["right_bound"]
        assert left is not None and right is not None
        assert math.isfinite(left) and math.isfinite(right)
        assert left <= row["effect"] <= right
        below, above = row["effect"] - left, right - row["effect"]
        if abs(above - below) > 1e-12 * max(above, below):
            off_centre += 1
    assert off_centre == len(rows), "a Fieller interval is never centred"


def test_the_relative_p_value_becomes_the_absolute_series_p_value():
    """Three runs of the same data, one assertion each way.

    Against the ABSOLUTE series the Fieller relative p-values must match exactly
    — that is the coherence claim at the level an operator can check. Against the
    DELTA series they must differ at every look, which is what stops this from
    passing on a build where the param did nothing.
    """
    _, _, delta_rows = _run(_fresh(), DELTA, "delta_exp")
    _, _, fieller_rows = _run(_fresh(), FIELLER, "fieller_exp")
    _, _, absolute_rows = _run(_fresh(), ABSOLUTE, "absolute_exp")

    assert set(delta_rows) == set(fieller_rows) == set(absolute_rows)
    moved = 0
    for key, fieller in fieller_rows.items():
        assert fieller["pvalue"] == absolute_rows[key]["pvalue"], key
        assert fieller["reject"] == absolute_rows[key]["reject"], key
        # the reported lift is the delta series' own number, untouched
        assert fieller["effect"] == delta_rows[key]["effect"], key
        if fieller["pvalue"] != delta_rows[key]["pvalue"]:
            moved += 1
    assert moved == len(delta_rows), "every relative p-value must move — else nothing was tested"


def test_the_readout_agrees_with_the_interval_it_prints(warehouse):
    """Under a compute-time correction the readout's significance rule IS "the CI
    excludes zero", and `reject` comes from the p-value. Under `fieller` those are
    one event by construction, so a row where they disagreed would mean the
    coherence had been lost somewhere between the estimator and the column."""
    experiment, tables, rows = _run(warehouse, FIELLER, "fieller_readout")
    readout = evaluate(experiment, tables.load_results(experiment.name), project=PROJECT)

    assert readout.verdicts
    for row in rows.values():
        excludes_zero = row["left_bound"] > 0 or row["right_bound"] < 0
        assert excludes_zero == bool(row["reject"]), row["end_ts"]
    assert all(verdict.effect is not None for verdict in readout.verdicts)


def test_an_unbounded_row_is_a_gap_to_the_readout_and_LEAVES_a_read_time_family():
    """The disclosed limitation, made executable.

    A row whose control mean is not distinguishable from zero carries a real
    p-value and NULL bounds. `readout._informative` keys on the bounds, so such a
    row is skipped — correct under a compute-time correction (it cannot exclude
    zero) but, under BH/Holm, it also leaves the family, which SHRINKS `m` for its
    siblings. That direction is anti-conservative, and it is the first time a row
    with a valid p-value has been excludable: before STAT-4, NULL bounds always
    came with a NULL p-value.

    Pinned rather than fixed, because relaxing `_informative` is a readout-wide
    semantics change (the stabilization scan reads the same predicate) and belongs
    to STAT-6's exit gate, not to the estimator that made it reachable.
    """
    from abkit.pipeline.readout import _informative

    unbounded = {
        "metric": "arpu",
        "left_bound": None,
        "right_bound": None,
        "pvalue": 0.001,
        "effect": 4.2,
        "alpha": 0.05,
        "insufficient_data": 0,
    }
    bounded = {**unbounded, "left_bound": 0.01, "right_bound": 0.09}
    assert _informative(bounded) is True
    assert _informative(unbounded) is False
