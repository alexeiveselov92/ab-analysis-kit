"""Exposure + metric loader tests on the scripted in-memory backend."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest
from fake_db import FakeDatabaseManager, serve_assignment_pushdown

from abkit.config import ExperimentConfig, MetricConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.loaders import (
    ExposureLoadError,
    MetricLoadError,
    RenderWindow,
    build_builtins,
    load_covariate_from_preperiod,
    load_exposures,
    load_metric,
)


class ScriptedQueryManager(FakeDatabaseManager):
    """Delegates ``_ab_*`` queries to the in-memory store; serves scripted rows
    for user-facing (fact/assignment) SQL.

    The assignment path is now the WP2 pushdown: the ``LIMIT 1`` column probe
    returns the first scripted row verbatim, while the ``GROUP BY`` aggregation
    is delegated to the base manager's own ``_project`` so there is exactly one
    MIN/COUNT implementation shared with the real fake-DB SQL evaluator.
    """

    def __init__(self):
        super().__init__()
        self.scripted_rows: list[dict] = []
        self.executed_user_sql: list[str] = []

    def execute_query(self, query, params=None):
        normalized = " ".join(query.split())
        if "user_revenue" in normalized:
            self.executed_user_sql.append(query)
            return [dict(r) for r in self.scripted_rows]
        if "assignments" in normalized:
            self.executed_user_sql.append(query)
            return self._serve_assignment(normalized)
        return super().execute_query(query, params)

    def _serve_assignment(self, normalized):
        return serve_assignment_pushdown(self._project, normalized, self.scripted_rows)


@pytest.fixture
def backend():
    return ScriptedQueryManager()


@pytest.fixture
def tables(backend):
    manager = InternalTablesManager(backend)
    manager.ensure_tables()
    return manager


@pytest.fixture
def experiment():
    return ExperimentConfig.model_validate(
        {
            "name": "signup_test",
            "start_date": "2024-07-01",
            "end_date": "2024-07-28",
            "unit_key": "user_id",
            "assignment": {
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            },
            "comparisons": [
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {"name": "t-test", "params": {"test_type": "relative"}},
                }
            ],
        }
    )


def make_builtins(**overrides):
    kwargs = {
        "experiment_id": "signup_test",
        "unit_key": "user_id",
        "variants": ["control", "treatment"],
        "added_filters": "",
        "window": RenderWindow(start_ts=datetime(2024, 7, 1), end_ts=datetime(2024, 7, 8)),
        "data_database": "analytics",
        "internal_database": "abkit_internal",
        "exposures_table": "_ab_exposures",
        "dialect": "clickhouse",
    }
    kwargs.update(overrides)
    return build_builtins(**kwargs)


def exposure_row(unit, variant, ts=None, **extra):
    return {
        "user_id": unit,
        "variant": variant,
        "exposure_ts": ts or datetime(2024, 7, 1, 10, 0, 0),
        **extra,
    }


class TestExposureLoader:
    def test_happy_path_persists_and_counts(self, backend, tables, experiment):
        backend.scripted_rows = [
            exposure_row("u1", "control"),
            exposure_row("u2", "treatment"),
            exposure_row("u3", "control"),
        ]
        counts = load_exposures(
            backend, tables, experiment, experiment.assignment.query, make_builtins()
        )
        assert counts == {"control": 2, "treatment": 1}
        assert tables.count_exposures("signup_test") == 3
        assert tables.get_exposure_counts("signup_test") == counts

    def test_unit_in_two_variants_is_a_hard_error(self, backend, tables, experiment):
        backend.scripted_rows = [
            exposure_row("u1", "control"),
            exposure_row("u1", "treatment"),
        ]
        with pytest.raises(ExposureLoadError, match="BOTH 'control' and 'treatment'"):
            load_exposures(
                backend, tables, experiment, experiment.assignment.query, make_builtins()
            )

    def test_same_variant_duplicates_dedupe_to_earliest_with_warning(
        self, backend, tables, experiment
    ):
        backend.scripted_rows = [
            exposure_row("u1", "control", ts=datetime(2024, 7, 2, 9)),
            exposure_row("u1", "control", ts=datetime(2024, 7, 1, 9)),
        ]
        with pytest.warns(UserWarning, match="duplicate unit rows"):
            counts = load_exposures(
                backend, tables, experiment, experiment.assignment.query, make_builtins()
            )
        assert counts == {"control": 1}
        assert tables.get_first_exposure_ts("signup_test") == datetime(2024, 7, 1, 9)

    def test_missing_required_column(self, backend, tables, experiment):
        backend.scripted_rows = [{"user_id": "u1", "variant": "control"}]
        with pytest.raises(ExposureLoadError, match="must SELECT"):
            load_exposures(
                backend, tables, experiment, experiment.assignment.query, make_builtins()
            )

    def test_undeclared_variant(self, backend, tables, experiment):
        backend.scripted_rows = [exposure_row("u1", "ghost")]
        with pytest.raises(ExposureLoadError, match="variant 'ghost' not declared"):
            load_exposures(
                backend, tables, experiment, experiment.assignment.query, make_builtins()
            )

    def test_empty_cohort_is_an_error(self, backend, tables, experiment):
        backend.scripted_rows = []
        with pytest.raises(ExposureLoadError, match="returned no rows"):
            load_exposures(
                backend, tables, experiment, experiment.assignment.query, make_builtins()
            )

    def test_rerun_is_idempotent(self, backend, tables, experiment):
        backend.scripted_rows = [exposure_row("u1", "control"), exposure_row("u2", "treatment")]
        load_exposures(backend, tables, experiment, experiment.assignment.query, make_builtins())
        load_exposures(backend, tables, experiment, experiment.assignment.query, make_builtins())
        assert tables.count_exposures("signup_test") == 2


ARPU_METRIC = MetricConfig.model_validate(
    {
        "name": "arpu",
        "type": "sample",
        "columns": {"variant": "variant", "value": "gross_usd"},
        "query": (
            "{% import 'abkit_assignment.jinja' as ab %}\n"
            "SELECT {{ ab.variant_col() }} AS variant, user_id, "
            "sum(gross_usd) AS gross_usd "
            "FROM {{ data_database }}.user_revenue {{ ab.exposed_units() }} "
            "GROUP BY variant, user_id"
        ),
    }
)

VARIANTS = ["control", "treatment"]


def metric_row(unit, variant, value):
    return {"variant": variant, "user_id": unit, "gross_usd": value}


class TestMetricLoader:
    def test_happy_path_arrays_by_variant(self, backend):
        backend.scripted_rows = [
            metric_row("u1", "control", 10.0),
            metric_row("u2", "control", 20.0),
            metric_row("u3", "treatment", 30.0),
        ]
        result = load_metric(backend, ARPU_METRIC, ARPU_METRIC.query, make_builtins(), VARIANTS)
        assert result.variants() == ["control", "treatment"]
        assert result.size("control") == 2
        np.testing.assert_array_equal(result.roles_by_variant["control"]["value"], [10.0, 20.0])

    def test_rendered_sql_must_join_the_cohort(self, backend):
        bare = MetricConfig.model_validate(
            {
                "name": "arpu",
                "type": "sample",
                "columns": {"variant": "variant", "value": "gross_usd"},
                "query": "SELECT variant, user_id, gross_usd FROM analytics.user_revenue",
            }
        )
        with pytest.raises(MetricLoadError, match="packaged macro"):
            load_metric(backend, bare, bare.query, make_builtins(), VARIANTS)

    def test_duplicate_units_rejected_with_group_by_hint(self, backend):
        backend.scripted_rows = [
            metric_row("u1", "control", 10.0),
            metric_row("u1", "control", 5.0),
        ]
        with pytest.raises(MetricLoadError, match="GROUP BY user_id"):
            load_metric(backend, ARPU_METRIC, ARPU_METRIC.query, make_builtins(), VARIANTS)

    def test_undeclared_variant(self, backend):
        backend.scripted_rows = [metric_row("u1", "ghost", 1.0)]
        with pytest.raises(MetricLoadError, match="not declared"):
            load_metric(backend, ARPU_METRIC, ARPU_METRIC.query, make_builtins(), VARIANTS)

    def test_missing_role_column(self, backend):
        backend.scripted_rows = [{"variant": "control", "user_id": "u1"}]
        with pytest.raises(MetricLoadError, match="missing columns"):
            load_metric(backend, ARPU_METRIC, ARPU_METRIC.query, make_builtins(), VARIANTS)

    def test_missing_declared_stratum_is_a_typed_error(self, backend):
        """Review finding: a declared stratum column absent from the result
        set must raise MetricLoadError, not a raw KeyError."""
        stratified = MetricConfig.model_validate(
            {
                "name": "arpu",
                "type": "sample",
                "columns": {
                    "variant": "variant",
                    "value": "gross_usd",
                    "stratum": "country",
                },
                "query": ARPU_METRIC.query,
            }
        )
        backend.scripted_rows = [metric_row("u1", "control", 1.0)]  # no country col
        with pytest.raises(MetricLoadError, match="country"):
            load_metric(backend, stratified, stratified.query, make_builtins(), VARIANTS)

    def test_null_values_become_nan_with_warning(self, backend):
        backend.scripted_rows = [
            metric_row("u1", "control", None),
            metric_row("u2", "control", 5.0),
        ]
        with pytest.warns(UserWarning, match="NULL values"):
            result = load_metric(backend, ARPU_METRIC, ARPU_METRIC.query, make_builtins(), VARIANTS)
        values = result.roles_by_variant["control"]["value"]
        assert np.isnan(values[0]) and values[1] == 5.0

    def test_empty_result_is_valid(self, backend):
        backend.scripted_rows = []
        result = load_metric(backend, ARPU_METRIC, ARPU_METRIC.query, make_builtins(), VARIANTS)
        assert result.variants() == []


class TestCovariate:
    def test_preperiod_render_requires_dropped_exposure_filter(self, backend):
        with pytest.raises(ValueError, match="ab_apply_exposure_filter=False"):
            load_covariate_from_preperiod(
                backend, ARPU_METRIC, ARPU_METRIC.query, make_builtins(), VARIANTS
            )

    def test_covariate_map_and_attach(self, backend):
        backend.scripted_rows = [
            metric_row("u1", "control", 7.0),
            metric_row("u3", "treatment", 3.0),
        ]
        preperiod = make_builtins(
            window=RenderWindow(start_ts=datetime(2024, 6, 17), end_ts=datetime(2024, 7, 1)),
            apply_exposure_filter=False,
        )
        covariate = load_covariate_from_preperiod(
            backend, ARPU_METRIC, ARPU_METRIC.query, preperiod, VARIANTS
        )
        assert covariate == {"u1": 7.0, "u3": 3.0}

        backend.scripted_rows = [
            metric_row("u1", "control", 10.0),
            metric_row("u2", "control", 20.0),
            metric_row("u3", "treatment", 30.0),
        ]
        result = load_metric(backend, ARPU_METRIC, ARPU_METRIC.query, make_builtins(), VARIANTS)
        result.attach_covariate(covariate)
        np.testing.assert_array_equal(
            result.roles_by_variant["control"]["covariate"], [7.0, 0.0]  # u2 absent -> 0
        )
        np.testing.assert_array_equal(result.roles_by_variant["treatment"]["covariate"], [3.0])
