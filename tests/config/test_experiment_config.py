"""ExperimentConfig tests: the primary entity's intra-file validation matrix."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from abkit.config import ExperimentConfig


def base_payload(**overrides) -> dict:
    payload = {
        "name": "signup_test",
        "status": "running",
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
                "metric": "signup_cr",
                "is_main_metric": True,
                "method": {"name": "z-test", "params": {"test_type": "relative"}},
            }
        ],
    }
    payload.update(overrides)
    return payload


class TestHappyPath:
    def test_spec_example_shape(self):
        config = ExperimentConfig.model_validate(base_payload())
        assert config.cadence == "1d"  # friction-free default
        assert config.data_lag_seconds() == 0
        assert config.timezone == "UTC"
        assert not config.is_sub_day()
        assert config.sequential.enabled is False
        assert config.main_metrics() == ["signup_cr"]

    def test_cadence_segments_scalar_normalisation(self):
        config = ExperimentConfig.model_validate(base_payload())
        assert config.cadence_segments() == [(86400, None)]
        # plan R1 comparability promise: scalar 1d ≡ [{every: 1d}]
        schedule = ExperimentConfig.model_validate(
            base_payload(
                cadence=[{"every": "1d"}],
            )
        )
        assert schedule.cadence_segments() == [(86400, None)]

    def test_dense_early_schedule(self):
        config = ExperimentConfig.model_validate(
            base_payload(
                cadence=[{"every": "1h", "until": "48h"}, {"every": "1d"}],
                data_lag="2h",
            )
        )
        assert config.cadence_segments() == [(3600, 172800), (86400, None)]
        assert config.is_sub_day()
        assert config.data_lag_seconds() == 7200

    def test_catalog_record_round_trips_canonical_json(self):
        config = ExperimentConfig.model_validate(base_payload())
        record = config.catalog_record(
            path="experiments/signup_test.yml",
            effective_alpha=0.05,
            effective_correction="bonferroni",
        )
        assert record["cadence"] == '[{"every":86400,"until":null}]'
        assert record["alpha"] == 0.05
        assert record["variants"] == '["control","treatment"]'


class TestCadenceValidation:
    def test_bad_scalar_grammar(self):
        with pytest.raises(ValidationError, match="Invalid interval format"):
            ExperimentConfig.model_validate(base_payload(cadence="daily"))

    def test_schedule_must_coarsen(self):
        with pytest.raises(ValidationError, match="strictly coarsening"):
            ExperimentConfig.model_validate(
                base_payload(
                    cadence=[{"every": "1d", "until": "2d"}, {"every": "1h"}],
                    data_lag="1h",
                )
            )

    def test_middle_segment_needs_until(self):
        with pytest.raises(ValidationError, match="needs 'until'"):
            ExperimentConfig.model_validate(
                base_payload(
                    cadence=[{"every": "1h"}, {"every": "1d"}],
                    data_lag="1h",
                )
            )

    def test_until_strictly_increasing(self):
        with pytest.raises(ValidationError, match="strictly increasing"):
            ExperimentConfig.model_validate(
                base_payload(
                    cadence=[
                        {"every": "1h", "until": "48h"},
                        {"every": "6h", "until": "48h"},
                        {"every": "1d"},
                    ],
                    data_lag="1h",
                )
            )

    def test_until_must_exceed_every(self):
        with pytest.raises(ValidationError, match="must exceed 'every'"):
            ExperimentConfig.model_validate(
                base_payload(
                    cadence=[{"every": "6h", "until": "3h"}, {"every": "1d"}],
                    data_lag="1h",
                )
            )

    def test_cadence_longer_than_horizon(self):
        with pytest.raises(ValidationError, match="longer than the experiment horizon"):
            ExperimentConfig.model_validate(base_payload(cadence="60d"))  # horizon is 28 days


class TestSubDayGates:
    def test_sub_day_requires_data_lag(self):
        with pytest.raises(ValidationError, match="requires 'data_lag'"):
            ExperimentConfig.model_validate(base_payload(cadence="1h"))

    def test_sub_day_with_explicit_zero_lag_ok(self):
        config = ExperimentConfig.model_validate(base_payload(cadence="1h", data_lag=0))
        assert config.data_lag_seconds() == 0

    def test_alpha_spending_forbidden_sub_day(self):
        with pytest.raises(ValidationError, match="alpha_spending"):
            ExperimentConfig.model_validate(
                base_payload(
                    cadence="30m",
                    data_lag="1h",
                    sequential={"enabled": True, "scheme": "alpha_spending"},
                )
            )

    def test_alpha_spending_fine_at_daily(self):
        ExperimentConfig.model_validate(
            base_payload(sequential={"enabled": True, "scheme": "alpha_spending"})
        )

    def test_always_valid_fine_sub_day(self):
        ExperimentConfig.model_validate(
            base_payload(
                cadence="1h",
                data_lag="2h",
                sequential={"enabled": True, "scheme": "always_valid"},
            )
        )


class TestAssignment:
    def test_needs_two_variants(self):
        with pytest.raises(ValidationError, match="at least two"):
            ExperimentConfig.model_validate(
                base_payload(
                    assignment={
                        "query": "SELECT 1",
                        "variants": ["control"],
                        "expected_split": {"control": 1.0},
                    }
                )
            )

    def test_expected_split_must_cover_variants(self):
        with pytest.raises(ValidationError, match="missing variants"):
            ExperimentConfig.model_validate(
                base_payload(
                    assignment={
                        "query": "SELECT 1",
                        "variants": ["control", "treatment"],
                        "expected_split": {"control": 1.0},
                    }
                )
            )

    def test_expected_split_unknown_variant(self):
        with pytest.raises(ValidationError, match="unknown variants"):
            ExperimentConfig.model_validate(
                base_payload(
                    assignment={
                        "query": "SELECT 1",
                        "variants": ["control", "treatment"],
                        "expected_split": {"control": 0.5, "treatment": 0.3, "ghost": 0.2},
                    }
                )
            )

    def test_expected_split_must_sum_to_one(self):
        with pytest.raises(ValidationError, match="sum to 1.0"):
            ExperimentConfig.model_validate(
                base_payload(
                    assignment={
                        "query": "SELECT 1",
                        "variants": ["control", "treatment"],
                        "expected_split": {"control": 0.5, "treatment": 0.4},
                    }
                )
            )

    def test_added_filters_must_start_with_and(self):
        with pytest.raises(ValidationError, match="must start with 'AND'"):
            ExperimentConfig.model_validate(
                base_payload(
                    assignment={
                        "query": "SELECT 1",
                        "added_filters": "WHERE country = 'US'",
                        "variants": ["control", "treatment"],
                        "expected_split": {"control": 0.5, "treatment": 0.5},
                    }
                )
            )

    def test_variant_name_length_budget(self):
        with pytest.raises(ValidationError, match="longer than 64"):
            ExperimentConfig.model_validate(
                base_payload(
                    assignment={
                        "query": "SELECT 1",
                        "variants": ["control", "x" * 65],
                        "expected_split": {"control": 0.5, "x" * 65: 0.5},
                    }
                )
            )


class TestComparisons:
    def test_duplicate_metric_refs(self):
        payload = base_payload()
        payload["comparisons"].append(
            {
                "metric": "signup_cr",
                "method": {"name": "t-test", "params": {}},
            }
        )
        with pytest.raises(ValidationError, match="duplicate metric references"):
            ExperimentConfig.model_validate(payload)

    def test_main_and_guardrail_exclusive(self):
        payload = base_payload()
        payload["comparisons"][0]["is_guardrail"] = True
        with pytest.raises(ValidationError, match="cannot both be true"):
            ExperimentConfig.model_validate(payload)

    def test_at_least_one_main_metric(self):
        payload = base_payload()
        payload["comparisons"][0]["is_main_metric"] = False
        with pytest.raises(ValidationError, match="is_main_metric"):
            ExperimentConfig.model_validate(payload)

    def test_empty_comparisons(self):
        with pytest.raises(ValidationError):
            ExperimentConfig.model_validate(base_payload(comparisons=[]))


class TestDatesAndMisc:
    def test_end_before_start(self):
        with pytest.raises(ValidationError, match="before start_date"):
            ExperimentConfig.model_validate(base_payload(end_date="2024-06-30"))

    def test_bad_timezone(self):
        with pytest.raises(ValidationError, match="unknown timezone"):
            ExperimentConfig.model_validate(base_payload(timezone="Mars/Olympus"))

    def test_name_length_budget(self):
        with pytest.raises(ValidationError, match="longer than 128"):
            ExperimentConfig.model_validate(base_payload(name="x" * 129))

    def test_alpha_range(self):
        with pytest.raises(ValidationError, match="alpha must be in"):
            ExperimentConfig.model_validate(base_payload(alpha=1.5))

    def test_from_yaml_file(self, tmp_path):
        (tmp_path / "exp.yml").write_text(
            """
name: signup_test
start_date: 2024-07-01
end_date: 2024-07-28
unit_key: user_id
assignment:
  query: "SELECT user_id, variant, exposure_ts FROM a"
  variants: [control, treatment]
  expected_split: {control: 0.5, treatment: 0.5}
comparisons:
  - metric: signup_cr
    is_main_metric: true
    method: {name: z-test, params: {test_type: relative}}
"""
        )
        config = ExperimentConfig.from_yaml_file(tmp_path / "exp.yml")
        assert config.name == "signup_test"
        assert config.comparisons[0].method.name == "z-test"
