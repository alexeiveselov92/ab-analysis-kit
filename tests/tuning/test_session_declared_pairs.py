"""m13 STAT-1b: the cockpit reads the same declared family every surface does.

``abk explore`` is the FOURTH reader of persisted rows (report, dashboard and
notifications are the other three) and it was the one without a filter. A pair
outside the declared contrast set — a renamed arm, or a family narrowed to
``contrasts: vs_control`` — is not on the page, so recomputing its series spends
the Tier-S budget on rows nobody reads and can raise the engine's
sequential-reload warning for a contrast the experiment does not claim.
"""

from __future__ import annotations

import numpy as np
from synthetic_ab import (
    METRICS,
    SyntheticWarehouse,
    build_session,
    make_experiment,
    run_pipeline,
    seed_all_events,
    seed_cohort,
)

from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._results import RESULT_COLUMNS

T_TEST = {"name": "t-test", "params": {"test_type": "relative"}}


def _prepared() -> tuple[SyntheticWarehouse, InternalTablesManager, object]:
    warehouse = SyntheticWarehouse()
    seed_cohort(warehouse)
    seed_all_events(warehouse)
    tables = InternalTablesManager(warehouse)
    tables.ensure_tables()
    experiment = make_experiment("explore_pairs", "arpu", T_TEST)
    run_pipeline(warehouse, tables, experiment)
    return warehouse, tables, experiment


def _clone_rows_under_a_new_pair(tables, experiment, name_2: str) -> int:
    """Copy the persisted series onto an arm pair the config does not declare."""
    rows = tables.load_results(experiment.name, metric="arpu")
    cloned = [{**row, "name_2": name_2} for row in rows]
    batch = {
        col: np.array([row.get(col) for row in cloned], dtype=object) for col in RESULT_COLUMNS
    }
    tables.save_results(batch)
    return len(cloned)


def test_undeclared_pair_rows_never_reach_the_session():
    warehouse, tables, experiment = _prepared()
    declared = len(tables.load_results(experiment.name, metric="arpu"))
    added = _clone_rows_under_a_new_pair(tables, experiment, "renamed_away")
    assert added > 0

    session = build_session(warehouse, tables, experiment, metrics=METRICS)

    series = session.series_by_metric["arpu"]
    assert len(series.rows) == declared
    assert {(r["name_1"], r["name_2"]) for r in series.rows} == set(experiment.contrast_pairs())
    assert any("declared contrast set" in w for w in session.warnings)


def test_a_clean_series_is_untouched_and_silent():
    """The filter must not invent a warning for the ordinary case — the
    cockpit's warning list is the operator's signal that something is off."""
    warehouse, tables, experiment = _prepared()

    session = build_session(warehouse, tables, experiment, metrics=METRICS)

    assert session.series_by_metric["arpu"].rows
    assert not any("contrast set" in w for w in session.warnings)
