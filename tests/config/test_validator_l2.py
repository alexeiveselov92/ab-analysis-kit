"""Validator level-2 tests: the declarative-config §8 matrix."""

from __future__ import annotations

from datetime import datetime

import pytest

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
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-29",
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

    def test_an_off_phase_anchor_warns_that_the_first_look_is_short(self):
        """The lattice does not start at start_ts, so the first window is
        shorter than the cadence — expected, but it reads like a dropped look."""
        exp = make_experiment(start_ts="2024-07-01 14:30:00")  # midnight anchor
        report = run_l2(exp, [make_metric()])
        assert not report.errors, report.errors
        assert any("the first look covers" in w for w in report.warnings), report.warnings

    def test_an_on_phase_anchor_stays_quiet(self):
        report = run_l2(make_experiment(), [make_metric()])
        assert not any("the first look covers" in w for w in report.warnings)

    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            ("daily US spring-forward", {"start_ts": "2024-03-10", "horizon_ts": "2024-03-20"}),
            (
                "weekly across spring-forward",
                {"start_ts": "2024-03-08", "horizon_ts": "2024-04-20", "cadence": "7d"},
            ),
            (
                "2d across spring-forward",
                {"start_ts": "2024-03-09", "horizon_ts": "2024-03-25", "cadence": "2d"},
            ),
        ],
    )
    def test_a_dst_shortened_first_day_is_not_blamed_on_the_anchor(self, label, overrides):
        """A 23h local day makes an ordinary midnight-anchored first look
        'short' in SECONDS while being a perfectly whole local day. Measuring
        the note in seconds printed an anchor accusation at every
        spring-forward experiment; it is gated on the anchor's PHASE instead."""
        exp = make_experiment(timezone="America/New_York", **overrides)
        report = run_l2(exp, [make_metric()])
        assert not any("the first look covers" in w for w in report.warnings), label

    def test_the_anchor_reaches_the_grid_through_the_factory(self):
        """End-to-end proof the knob is wired: the same window with a `start`
        anchor and a sub-day start_ts must produce a DIFFERENT lattice."""
        midnight = make_experiment(start_ts="2024-07-01 14:30:00")
        anchored = make_experiment(start_ts="2024-07-01 14:30:00", interval_anchor="start")
        assert midnight.grid().cutoffs[0].end_ts == datetime(2024, 7, 2, 0, 0)
        assert anchored.grid().cutoffs[0].end_ts == datetime(2024, 7, 2, 14, 30)
        assert anchored.grid().anchor_ts == datetime(2024, 7, 1, 14, 30)

    def test_a_sub_day_start_still_lints(self):
        """m10 WP1: the fixture window came from
        `datetime.combine(experiment.start_date, time.min)`, which raises no
        error on a datetime — it silently drops the time. It now resolves
        through the same helper the grid uses."""
        exp = make_experiment(start_ts="2024-07-01 14:30:00")
        report = run_l2(exp, [make_metric()])
        assert not report.errors, report.errors

    def test_metric_without_macro_fails_lint(self):
        metric = make_metric(query="SELECT variant, user_id, v FROM {{ data_database }}.t")
        report = run_l2(make_experiment(), [metric])
        assert any("packaged macro" in e for e in report.errors)

    def test_undeclared_jinja_variable_fails(self):
        metric = make_metric(query=MACRO_QUERY + " {{ mystery }}")
        report = run_l2(make_experiment(), [metric])
        assert any("mystery" in e for e in report.errors)

    def test_cohort_copy_requires_the_added_filters_hook(self):
        """m8 WP5: the incremental copy's batch bounds land in
        {{ ab_added_filters }} — copy mode without the reference fails lint."""
        exp = make_experiment(
            assignment={
                "query": ASSIGNMENT_QUERY,  # no ab_added_filters reference
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
                "cohort_copy": {"enabled": True},
            }
        )
        report = run_l2(exp, [make_metric()])
        assert any("ab_added_filters" in e for e in report.errors)

    def test_cohort_copy_hook_in_a_comment_fails_lint(self):
        """The lint proves a LIVE render (sentinel survives), not a substring —
        a token parked in a SQL comment must not pass (review finding)."""
        exp = make_experiment(
            assignment={
                "query": ASSIGNMENT_QUERY + " -- ab_added_filters",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
                "cohort_copy": {"enabled": True},
            }
        )
        report = run_l2(exp, [make_metric()])
        assert any("ab_added_filters" in e for e in report.errors)

    def test_cohort_copy_with_the_hook_passes(self):
        exp = make_experiment(
            assignment={
                "query": ASSIGNMENT_QUERY + " WHERE 1 = 1 {{ ab_added_filters }}",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
                "cohort_copy": {"enabled": True},
            }
        )
        report = run_l2(exp, [make_metric()])
        assert not any("ab_added_filters" in e for e in report.errors)

    def test_direct_mode_never_requires_the_hook(self):
        report = run_l2(make_experiment(), [make_metric()])  # default: no copy
        assert not any("ab_added_filters" in e for e in report.errors)


class TestAsymmetricIntervalVersusTheSequentialMode:
    """m13 STAT-3: the one configuration pair that is a static contradiction.

    The always-valid transform never receives a standard error — it recovers one
    by inverting the CI width, which is only an SE for ``effect ± z·SE``.
    STAT-3a made that refuse loudly at the inversion; refusing HERE moves the
    failure off the warehouse read, where it would have killed one experiment
    mid-run with the cohort already loaded.
    """

    def fraction_metric(self):
        return make_metric(
            name="cr",
            type="fraction",
            columns={"variant": "variant", "count": "c", "nobs": "n"},
        )

    def experiment_with(self, interval, sequential):
        return make_experiment(
            sequential={"enabled": sequential},
            comparisons=[
                {
                    "metric": "cr",
                    "is_main_metric": True,
                    "method": {"name": "z-test", "params": {"interval": interval}},
                }
            ],
        )

    def test_a_score_interval_under_the_sequential_mode_is_an_error(self):
        report = run_l2(self.experiment_with("score", True), [self.fraction_metric()])
        assert not report.ok
        message = "\n".join(report.errors)
        assert "asymmetric" in message and "sequential.enabled" in message

    def test_each_half_alone_is_fine(self):
        """Neither declaration is a defect on its own — only the pair is. A gate
        that refused the score interval outright would make the whole WP
        unreachable for anyone who had ever turned the sequential mode on."""
        for interval, sequential in (("score", False), ("pooled", True), ("pooled", False)):
            report = run_l2(self.experiment_with(interval, sequential), [self.fraction_metric()])
            assert report.ok, (interval, sequential, report.errors)

    def test_the_gate_reads_the_BOUND_method_not_the_class(self):
        """The mutation this gate exists to survive: ``asymmetric_ci`` is a plain
        attribute resolved per instance precisely because a PARAM selects the
        interval. A class-level read answers for the default params and would pass
        the configuration above — a guard that cannot fire for its own case."""
        from abkit.stats import get_method_class

        assert get_method_class("z-test").asymmetric_ci is False
        report = run_l2(self.experiment_with("score", True), [self.fraction_metric()])
        assert not report.ok

    def experiment_with_fieller(self, interval, sequential):
        """m13 STAT-4: the same contradiction, on a VALUE metric.

        The gate reads ``method.asymmetric_ci`` and never a method name, so a
        second asymmetric estimator must be covered without touching the
        validator. This leg is what proves that — it would fail if the rule had
        been written against ``interval == "score"``.
        """
        return make_experiment(
            sequential={"enabled": sequential},
            comparisons=[
                {
                    "metric": "arpu",
                    "is_main_metric": True,
                    "method": {
                        "name": "t-test",
                        "params": {"test_type": "relative", "interval": interval},
                    },
                }
            ],
        )

    def test_a_fieller_interval_under_the_sequential_mode_is_the_same_error(self):
        report = run_l2(self.experiment_with_fieller("fieller", True), [make_metric()])
        assert not report.ok
        message = "\n".join(report.errors)
        assert "asymmetric" in message and "sequential.enabled" in message

    def test_each_fieller_half_alone_is_fine(self):
        for interval, sequential in (("fieller", False), ("delta", True), ("delta", False)):
            report = run_l2(self.experiment_with_fieller(interval, sequential), [make_metric()])
            assert report.ok, (interval, sequential, report.errors)
