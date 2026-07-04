"""Validator level-2 tests: the declarative-config §8 matrix."""

from __future__ import annotations

from abkit.config import (
    ExperimentConfig,
    MetricConfig,
    ProjectConfig,
    validate_experiment_level2,
)

MACRO_QUERY = (
    "{% import 'abkit_assignment.jinja' as ab %}\n"
    "SELECT {{ ab.variant_col() }} AS variant, user_id, sum(v) AS v "
    "FROM {{ data_database }}.t {{ ab.exposed_units() }} GROUP BY variant, user_id"
)

ASSIGNMENT_QUERY = "SELECT user_id, variant, exposure_ts FROM assignments"


def make_metric(name="arpu", **overrides) -> MetricConfig:
    payload = {
        "name": name,
        "type": "sample",
        "columns": {"variant": "variant", "value": "v"},
        "query": MACRO_QUERY,
    }
    payload.update(overrides)
    return MetricConfig.model_validate(payload)


def make_experiment(**overrides) -> ExperimentConfig:
    payload = {
        "name": "exp1",
        "start_date": "2024-07-01",
        "end_date": "2024-07-28",
        "unit_key": "user_id",
        "assignment": {
            "query": ASSIGNMENT_QUERY,
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
    payload.update(overrides)
    return ExperimentConfig.model_validate(payload)


def run_l2(experiment, metrics, project=None):
    project = project or ProjectConfig(name="p", default_profile="dev")
    return validate_experiment_level2(experiment, {m.name: m for m in metrics}, project)


class TestReferenceIntegrity:
    def test_happy_path(self):
        report = run_l2(make_experiment(), [make_metric()])
        assert report.ok, report.errors
        assert report.warnings == []

    def test_dangling_metric_ref(self):
        report = run_l2(make_experiment(), [make_metric(name="other")])
        assert any("no metric named 'arpu'" in e for e in report.errors)

    def test_unit_key_mismatch(self):
        report = run_l2(make_experiment(), [make_metric(unit_key="device_id")])
        assert any("unit_key" in e for e in report.errors)

    def test_omitted_metric_unit_key_inherits(self):
        report = run_l2(make_experiment(), [make_metric(unit_key=None)])
        assert report.ok


class TestMethodValidation:
    def test_unknown_method(self):
        exp = make_experiment(
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {"name": "not-a-method"},
                }
            ]
        )
        report = run_l2(exp, [make_metric()])
        assert any("not-a-method" in e for e in report.errors)

    def test_quarantined_method_blocked_at_validate_time(self):
        exp = make_experiment(
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {"name": "paired-post-normed-bootstrap"},
                }
            ]
        )
        report = run_l2(exp, [make_metric()])
        assert any("quarantine" in e.lower() for e in report.errors)

    def test_bad_param(self):
        exp = make_experiment(
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {"name": "t-test", "params": {"bogus": 1}},
                }
            ]
        )
        report = run_l2(exp, [make_metric()])
        assert any("bogus" in e for e in report.errors)


class TestCupedRules:
    def _cuped_exp(self, params):
        return make_experiment(
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {"name": "cuped-t-test", "params": params},
                }
            ]
        )

    def test_cuped_with_lookback_ok(self):
        report = run_l2(self._cuped_exp({"covariate_lookback": "14d"}), [make_metric()])
        assert report.ok, report.errors

    def test_cuped_needs_a_covariate_source(self):
        report = run_l2(self._cuped_exp({}), [make_metric()])
        assert any("needs a covariate" in e for e in report.errors)

    def test_cuped_with_explicit_covariate_column_ok(self):
        metric = make_metric(columns={"variant": "variant", "value": "v", "covariate": "pre_v"})
        report = run_l2(self._cuped_exp({}), [metric])
        assert report.ok, report.errors

    def test_cuped_on_fraction_metric_rejected(self):
        metric = MetricConfig.model_validate(
            {
                "name": "arpu",
                "type": "fraction",
                "columns": {"variant": "variant", "count": "c", "nobs": "n"},
                "query": MACRO_QUERY,
            }
        )
        report = run_l2(self._cuped_exp({"covariate_lookback": "14d"}), [metric])
        assert any("'sample' metric" in e for e in report.errors)

    def test_lookback_under_one_day_is_an_error(self):
        report = run_l2(self._cuped_exp({"covariate_lookback": "12h"}), [make_metric()])
        assert any("covariate_lookback < 1d" in e for e in report.errors)

    def test_fractional_day_lookback_is_an_error(self):
        report = run_l2(self._cuped_exp({"covariate_lookback": "36h"}), [make_metric()])
        assert any("WHOLE days" in e for e in report.errors)

    def test_lookback_under_week_warns(self):
        report = run_l2(self._cuped_exp({"covariate_lookback": "3d"}), [make_metric()])
        assert report.ok
        assert any("< 7d" in w for w in report.warnings)

    def test_lookback_on_non_cuped_method_is_a_param_error(self):
        """Only CUPED methods declare covariate_lookback — bind() rejects it
        elsewhere (stricter than a warning: the schema is the gate)."""
        exp = make_experiment(
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {
                        "name": "t-test",
                        "params": {"covariate_lookback": "14d"},
                    },
                }
            ]
        )
        report = run_l2(exp, [make_metric()])
        assert any("covariate_lookback" in e for e in report.errors)


class TestCapabilityLint:
    """Plan R8: metric.type × input_kind / is_paired gate at VALIDATE time."""

    def test_input_kind_mismatch(self):
        fraction_metric = MetricConfig.model_validate(
            {
                "name": "arpu",
                "type": "fraction",
                "columns": {"variant": "variant", "count": "c", "nobs": "n"},
                "query": MACRO_QUERY,
            }
        )
        report = run_l2(make_experiment(), [fraction_metric])  # t-test on fraction
        assert any("expects a 'sample' metric" in e for e in report.errors)

    def test_paired_method_rejected(self):
        exp = make_experiment(
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {"name": "paired-t-test", "params": {}},
                }
            ]
        )
        report = run_l2(exp, [make_metric()])
        assert any("paired design" in e for e in report.errors)


class TestLooksGates:
    def test_max_looks_hard_gate(self):
        project = ProjectConfig.model_validate(
            {"name": "p", "default_profile": "dev", "limits": {"max_looks": 20}}
        )
        exp = make_experiment(cadence="1h", data_lag="1h")
        report = run_l2(exp, [make_metric()], project)
        assert any("max_looks" in e for e in report.errors)

    def test_warn_looks_peeking_warning_without_sequential(self):
        exp = make_experiment(cadence="1h", data_lag="1h")  # 672 looks
        report = run_l2(exp, [make_metric()])
        assert report.ok
        assert any("peeking" in w for w in report.warnings)

    def test_sequential_silences_the_peeking_warning(self):
        exp = make_experiment(
            cadence="1h",
            data_lag="1h",
            sequential={"enabled": True, "scheme": "always_valid"},
        )
        report = run_l2(exp, [make_metric()])
        assert not any("peeking" in w for w in report.warnings)

    def test_midnight_drift_warning(self):
        exp = make_experiment(cadence="7h", data_lag="1h")
        report = run_l2(exp, [make_metric()])
        assert any("drifts across midnight" in w for w in report.warnings)


class TestRenderSmoke:
    def test_assignment_missing_contract_token(self):
        exp = make_experiment(
            assignment={
                "query": "SELECT user_id, variant FROM assignments",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            }
        )
        report = run_l2(exp, [make_metric()])
        assert any("exposure_ts" in e for e in report.errors)

    def test_metric_without_macro_fails_lint(self):
        metric = make_metric(query="SELECT variant, user_id, v FROM {{ data_database }}.t")
        report = run_l2(make_experiment(), [metric])
        assert any("packaged macro" in e for e in report.errors)

    def test_undeclared_jinja_variable_fails(self):
        metric = make_metric(query=MACRO_QUERY + " {{ mystery }}")
        report = run_l2(make_experiment(), [metric])
        assert any("mystery" in e for e in report.errors)
