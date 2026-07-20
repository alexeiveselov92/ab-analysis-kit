"""RecomputeBackend cohort-source threading (m8-implementation-plan.md WP3).

The constructor's ``direct_source_sql``/``has_stratum`` must reach EVERY render
through the one ``_builtins()`` seam — the metric window render AND the CUPED
covariate pre-period render (the spec demands an explicit test for the latter,
not an assumption) — and a direct-mode load must return arrays identical to a
copy-mode load on a well-formed cohort (§0.4.3 "never change a number
silently" applied to the read-path swap).

Scope note: ``SyntheticWarehouse`` evaluates a direct-mode cohort fragment to
real-backend semantics (unknown-column error + MIN dedup —
``_cohort_join_map``) but serves both modes' events from ONE log, so the
load-parity test pins column-contract validity plus the pipeline-level
invariance (window bounds, covariate mechanics, unit ordering); the byte-level
render difference is pinned by the snapshot test in ``test_query_template.py``,
and executable parity on a real backend is the milestone exit-gate ClickHouse
e2e (WP6).

The scripted assignment source has NO stratum column, so every direct-mode
backend here passes ``has_stratum=False`` — mirroring what WP4's factory will
derive from ``probe_has_stratum``; the mismatch case is its own test below.
"""

from __future__ import annotations

import numpy as np
import pytest
from synthetic_ab import (
    CONVERSION,
    CTR,
    REVENUE,
    SyntheticWarehouse,
    make_experiment,
    seed_all_events,
    seed_cohort,
)

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.core.period_planner import generate_grid

DIRECT_SQL = "SELECT user_id, variant, exposure_ts FROM assignments"
CUPED = {"name": "cuped-t-test", "params": {"covariate_lookback": "7d"}}


class RecordingWarehouse(SyntheticWarehouse):
    """Logs every query — SyntheticWarehouse short-circuits before the base log."""

    def __init__(self) -> None:
        super().__init__()
        self.executed: list[str] = []

    def execute_query(self, query, params=None):
        self.executed.append(" ".join(query.split()))
        return super().execute_query(query, params)


def _seeded() -> RecordingWarehouse:
    warehouse = RecordingWarehouse()
    seed_cohort(warehouse)
    seed_all_events(warehouse)
    return warehouse


def _load_all(backend: RecomputeBackend, experiment, metric):
    grid = generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        tz=experiment.timezone,
    )
    comparison = experiment.comparisons[0]
    sql = metric.get_query_text(None)
    return [backend.load_cutoff(comparison, metric, sql, grid, cutoff) for cutoff in grid.cutoffs]


def test_direct_mode_reaches_the_metric_render():
    warehouse = _seeded()
    experiment = make_experiment("exp_direct", "arpu", {"name": "t-test"})
    backend = RecomputeBackend(
        warehouse, experiment, direct_source_sql=DIRECT_SQL, has_stratum=False
    )
    _load_all(backend, experiment, REVENUE)

    metric_queries = [q for q in warehouse.executed if "user_revenue" in q]
    assert metric_queries
    for q in metric_queries:
        assert "FROM (SELECT user_id AS unit_id" in q
        assert DIRECT_SQL in q
        assert "NULL AS stratum" in q  # the stratum-less source projects the NULL
        assert "_ab_exposures" not in q  # never the persisted table in direct mode


def test_lying_has_stratum_fails_like_a_real_backend():
    """has_stratum=True against a stratum-less source renders MIN(stratum) over
    a column that does not exist — a real backend rejects that query, and so
    does the fake (a WP3 review finding: the old fake silently tolerated it).
    WP4's factory must derive has_stratum from probe_has_stratum, never assume."""
    warehouse = _seeded()
    experiment = make_experiment("exp_lying", "arpu", {"name": "t-test"})
    backend = RecomputeBackend(
        warehouse, experiment, direct_source_sql=DIRECT_SQL, has_stratum=True
    )
    with pytest.raises(ValueError, match="unknown column 'stratum'"):
        _load_all(backend, experiment, REVENUE)


def test_copy_mode_default_is_unchanged():
    warehouse = _seeded()
    experiment = make_experiment("exp_copy", "arpu", {"name": "t-test"})
    backend = RecomputeBackend(warehouse, experiment)
    _load_all(backend, experiment, REVENUE)

    metric_queries = [q for q in warehouse.executed if "user_revenue" in q]
    assert metric_queries
    for q in metric_queries:
        assert "abkit_internal._ab_exposures FINAL" in q
        assert "GROUP BY user_id, variant)" not in q


def test_covariate_render_gets_the_same_cohort_source():
    """The CUPED pre-period render (apply_exposure_filter=False) flows through
    the SAME _builtins() and therefore the same direct-join ab_cohort_source."""
    warehouse = _seeded()
    experiment = make_experiment("exp_cuped", "arpu", CUPED)
    backend = RecomputeBackend(
        warehouse, experiment, direct_source_sql=DIRECT_SQL, has_stratum=False
    )
    loaded = _load_all(backend, experiment, REVENUE)

    # 7d lookback from the 2024-07-01 start: the pre-period render's window
    # opens at 2024-06-24 00:00:00 — that timestamp appears in no other query
    cov_queries = [q for q in warehouse.executed if "'2024-06-24 00:00:00'" in q]
    assert cov_queries, "the covariate pre-period render never executed"
    for q in cov_queries:
        assert "user_revenue" in q
        assert "FROM (SELECT user_id AS unit_id" in q
        # the exposure filter is dropped on the pre-period render
        assert "_abk_exposure_ts" not in q.split("WHERE experiment")[-1]
    # and the covariate actually attached
    assert all("covariate" in r.roles_by_variant[v] for r in loaded for v in r.variants())


def _assert_loads_identical(a, b):
    assert a.units_by_variant.keys() == b.units_by_variant.keys()
    for variant in a.units_by_variant:
        np.testing.assert_array_equal(a.units_by_variant[variant], b.units_by_variant[variant])
        assert a.roles_by_variant[variant].keys() == b.roles_by_variant[variant].keys()
        for role, values in a.roles_by_variant[variant].items():
            np.testing.assert_array_equal(values, b.roles_by_variant[variant][role])
        strata_a = a.strata_by_variant.get(variant)
        strata_b = b.strata_by_variant.get(variant)
        assert (strata_a is None) == (strata_b is None)
        if strata_a is not None:
            np.testing.assert_array_equal(strata_a, strata_b)


def test_direct_vs_copy_load_parity_on_a_well_formed_cohort():
    """§0.4.3: the read-path swap moves no number — every per-unit array a
    direct-mode load returns is identical to the copy-mode load, for all three
    metric kinds plus the CUPED covariate path."""
    cells = [
        (REVENUE, {"name": "t-test"}),
        (REVENUE, CUPED),
        (CONVERSION, {"name": "z-test"}),
        (CTR, {"name": "ratio-delta"}),
    ]
    for metric, method in cells:
        experiment = make_experiment("exp_par", metric.name, method)
        copy_loads = _load_all(RecomputeBackend(_seeded(), experiment), experiment, metric)
        direct_loads = _load_all(
            RecomputeBackend(
                _seeded(), experiment, direct_source_sql=DIRECT_SQL, has_stratum=False
            ),
            experiment,
            metric,
        )
        assert len(copy_loads) == len(direct_loads)
        for copy_load, direct_load in zip(copy_loads, direct_loads, strict=True):
            _assert_loads_identical(copy_load, direct_load)
