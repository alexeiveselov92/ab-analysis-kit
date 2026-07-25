"""ExperimentConfig tests: the primary entity's intra-file validation matrix."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from abkit.config import ExperimentConfig


def base_payload(**overrides) -> dict:
    payload = {
        "name": "signup_test",
        "status": "running",
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-29",
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
        assert config.incremental_reads is None  # m9 WP4: None -> project default
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

    def test_alpha_spending_not_implemented(self):
        # always_valid is the only supported scheme; alpha_spending is a config error at ANY cadence.
        with pytest.raises(ValidationError, match="not implemented"):
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


class TestCohortCopy:
    """M8 WP1: the opt-in ``assignment.cohort_copy`` block (additive-only)."""

    def _payload(self, **copy_block) -> dict:
        payload = base_payload()
        payload["assignment"]["cohort_copy"] = copy_block
        return payload

    def test_default_disabled_with_donor_knob_defaults(self):
        config = ExperimentConfig.model_validate(base_payload())
        cohort_copy = config.assignment.cohort_copy
        assert cohort_copy.enabled is False
        assert cohort_copy.update_column == "exposure_ts"
        assert cohort_copy.batch_interval == "1d"
        assert cohort_copy.batch_intervals_per_round_trip == 30
        assert cohort_copy.maturity_delay == 0
        assert cohort_copy.batch_interval_seconds() == 86400
        assert cohort_copy.maturity_delay_seconds() == 0

    def test_accepts_int_seconds_and_interval_strings(self):
        config = ExperimentConfig.model_validate(
            self._payload(enabled=True, batch_interval=3600, maturity_delay="1d")
        )
        cohort_copy = config.assignment.cohort_copy
        assert cohort_copy.enabled is True
        assert cohort_copy.batch_interval_seconds() == 3600
        assert cohort_copy.maturity_delay_seconds() == 86400

    def test_bad_batch_interval_grammar_fails_at_parse(self):
        with pytest.raises(ValidationError, match="Unknown time unit"):
            ExperimentConfig.model_validate(self._payload(batch_interval="1fortnight"))

    def test_non_positive_batch_interval_rejected(self):
        with pytest.raises(ValidationError, match="positive"):
            ExperimentConfig.model_validate(self._payload(batch_interval=0))

    def test_maturity_delay_zero_ok_negative_rejected(self):
        config = ExperimentConfig.model_validate(self._payload(maturity_delay=0))
        assert config.assignment.cohort_copy.maturity_delay_seconds() == 0
        with pytest.raises(ValidationError, match="positive"):
            ExperimentConfig.model_validate(self._payload(maturity_delay=-60))

    def test_round_trip_count_must_be_positive(self):
        with pytest.raises(ValidationError):
            ExperimentConfig.model_validate(self._payload(batch_intervals_per_round_trip=0))

    def test_update_column_gate_fires_only_when_enabled(self):
        with pytest.raises(ValidationError, match="plain column identifier"):
            ExperimentConfig.model_validate(
                self._payload(enabled=True, update_column="exposure ts")
            )
        with pytest.raises(ValidationError, match="plain column identifier"):
            ExperimentConfig.model_validate(self._payload(enabled=True, update_column=""))
        # Disabled: the cheap gate deliberately does not fire (WP1 step 4) —
        # the run-time column probe (WP2) is the real check.
        config = ExperimentConfig.model_validate(
            self._payload(enabled=False, update_column="not an identifier")
        )
        assert config.assignment.cohort_copy.enabled is False


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


class TestWindowFields:
    """m10 WP1/D1: the window edges are timestamps, and the old keys are gone."""

    @pytest.mark.parametrize(
        ("old", "new"),
        [("start_date", "start_ts"), ("end_date", "horizon_ts")],
    )
    def test_the_renamed_keys_fail_by_name(self, old, new):
        """pydantic's default extra="ignore" would drop the stale key and then
        report a bare 'Field required' — which does not tell the reader that
        the horizon VALUE moves too."""
        payload = base_payload()
        payload[old] = payload.pop({"start_date": "start_ts", "end_date": "horizon_ts"}[old])
        with pytest.raises(ValidationError, match=f"`{old}` was renamed to `{new}`"):
            ExperimentConfig.model_validate(payload)

    def test_the_end_date_hint_spells_out_the_off_by_one(self):
        payload = base_payload()
        payload["end_date"] = payload.pop("horizon_ts")
        with pytest.raises(ValidationError, match="EXCLUSIVE right edge"):
            ExperimentConfig.model_validate(payload)

    def test_a_bare_date_stays_a_date_and_a_timestamp_stays_a_datetime(self):
        """Type-preserving on purpose: str() of the field reaches the m9 state
        identity hash, so a re-parse that flipped the type would orphan every
        materialized series."""
        config = ExperimentConfig.model_validate(
            base_payload(start_ts="2024-07-01", horizon_ts="2024-07-14 18:30:00")
        )
        assert type(config.start_ts) is date
        assert type(config.horizon_ts) is datetime
        assert config.horizon_ts == datetime(2024, 7, 14, 18, 30)

    def test_python_objects_pass_through_unchanged(self):
        config = ExperimentConfig.model_validate(
            base_payload(start_ts=date(2024, 7, 1), horizon_ts=datetime(2024, 7, 14, 6, 0))
        )
        assert type(config.start_ts) is date
        assert type(config.horizon_ts) is datetime

    def test_an_unquoted_number_is_refused_not_read_as_a_unix_timestamp(self):
        """`start_ts: 20240101` (no quotes, no dashes) is an int to YAML, and
        pydantic's datetime member would happily read it as 1970-08-23."""
        with pytest.raises(ValidationError, match="expected an ISO date or timestamp"):
            ExperimentConfig.model_validate(base_payload(start_ts=20240101))

    def test_a_utc_offset_is_refused(self):
        with pytest.raises(ValidationError, match="drop the UTC offset"):
            ExperimentConfig.model_validate(base_payload(start_ts="2024-07-01T10:00:00+03:00"))

    def test_sub_second_precision_is_refused(self):
        """Accepting it would validate a window nothing downstream can carry:
        the rendered SQL window formats to whole seconds and `_ab_results.end_ts`
        is DateTime64(3), so a microsecond cutoff would persist rounded, never
        match the planned instant, and re-plan on every run — forever."""
        with pytest.raises(ValidationError, match="drop the sub-second part"):
            ExperimentConfig.model_validate(base_payload(start_ts="2024-07-01T14:30:00.123456"))
        # whole seconds stay legal
        assert ExperimentConfig.model_validate(
            base_payload(start_ts="2024-07-01T14:30:00")
        ).start_ts == datetime(2024, 7, 1, 14, 30)

    def test_a_calendar_edge_window_fails_as_a_validation_error(self):
        """`astimezone` off the end of the representable calendar raises
        OverflowError, which pydantic does NOT wrap — the raw exception used to
        escape `model_validate` naming neither field nor cause."""
        with pytest.raises(ValidationError, match="outside the representable calendar"):
            ExperimentConfig.model_validate(
                base_payload(
                    start_ts="9999-12-31 20:00:00",
                    horizon_ts="9999-12-31 23:00:00",
                    timezone="America/New_York",
                )
            )

    def test_garbage_is_refused(self):
        with pytest.raises(ValidationError, match="is not an ISO date"):
            ExperimentConfig.model_validate(base_payload(start_ts="last tuesday"))

    def test_instants_resolve_through_the_experiment_timezone(self):
        config = ExperimentConfig.model_validate(
            base_payload(start_ts="2024-07-01", horizon_ts="2024-07-15", timezone="Europe/Moscow")
        )
        assert config.start_instant() == datetime(2024, 6, 30, 21, 0)
        assert config.horizon_instant() == datetime(2024, 7, 14, 21, 0)

    def test_horizon_seconds_is_the_true_elapsed_window(self):
        whole_days = ExperimentConfig.model_validate(base_payload(horizon_ts="2024-07-15"))
        assert whole_days.horizon_seconds() == 14 * 86400

        sub_day = ExperimentConfig.model_validate(
            base_payload(start_ts="2024-07-01 06:00:00", horizon_ts="2024-07-02 12:00:00")
        )
        assert sub_day.horizon_seconds() == 30 * 3600

    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            (
                "daily starting ON the US spring-forward day",
                {
                    "start_ts": "2024-03-10",
                    "horizon_ts": "2024-03-11",
                    "timezone": "America/New_York",
                },
            ),
            (
                "daily starting ON the EU spring-forward day",
                {"start_ts": "2024-03-31", "horizon_ts": "2024-04-01", "timezone": "Europe/Berlin"},
            ),
            (
                "weekly spanning a spring-forward",
                {
                    "start_ts": "2024-03-08",
                    "horizon_ts": "2024-03-15",
                    "cadence": "7d",
                    "timezone": "America/New_York",
                },
            ),
        ],
    )
    def test_a_dst_shortened_window_still_admits_its_whole_day_cadence(self, label, overrides):
        """`horizon_seconds()` is honest elapsed time, so a spring-forward
        window is 23h — but the cadence gate must not read that as 'a day does
        not fit in a day'. A whole-day step is measured in CALENDAR days, the
        space the planner steps in. These configs parsed before m10 and their
        grids did not move."""
        ExperimentConfig.model_validate(base_payload(**overrides))

    def test_a_step_longer_than_the_window_can_still_fire_off_an_anchor(self):
        """With `interval_anchor` the gate stopped being a property of the step
        LENGTH. A daily lattice hung at 06:00 puts a real cutoff inside a
        12h window opening at midnight — arithmetic cannot see that, so the
        gate enumerates instead of guessing."""
        config = ExperimentConfig.model_validate(
            base_payload(
                start_ts="2024-07-01",
                horizon_ts="2024-07-01 12:00:00",
                interval_anchor="2024-07-01 06:00:00",
            )
        )
        cutoffs = config.grid().cutoffs
        assert [c.end_ts for c in cutoffs if not c.is_horizon] == [datetime(2024, 7, 1, 6, 0)]

    def test_a_cadence_that_genuinely_cannot_fire_is_still_refused(self):
        """Same window, default anchor: the only point is the horizon itself."""
        with pytest.raises(ValidationError, match="longer than the experiment horizon"):
            ExperimentConfig.model_validate(
                base_payload(start_ts="2024-07-01", horizon_ts="2024-07-01 12:00:00")
            )

    def test_horizon_seconds_absorbs_a_dst_transition(self):
        """A 5-day window over the spring-forward weekend is 5 days MINUS an
        hour — a day count would report 432000 and be wrong by 3600."""
        config = ExperimentConfig.model_validate(
            base_payload(
                start_ts="2024-03-08", horizon_ts="2024-03-13", timezone="America/New_York"
            )
        )
        assert config.horizon_seconds() == 5 * 86400 - 3600

    def test_the_grid_horizon_is_exactly_the_configured_horizon(self):
        """The rename's point: one vocabulary. `config.horizon_ts` resolved IS
        `grid.horizon_ts` — no +1-day translation anywhere."""
        config = ExperimentConfig.model_validate(base_payload())
        grid = config.grid()
        assert grid.start_ts == config.start_instant()
        assert grid.horizon_ts == config.horizon_instant()


class TestIntervalAnchorConfig:
    def test_defaults_to_midnight(self):
        assert ExperimentConfig.model_validate(base_payload()).interval_anchor == "midnight"

    @pytest.mark.parametrize("keyword", ["midnight", "start"])
    def test_keywords(self, keyword):
        config = ExperimentConfig.model_validate(base_payload(interval_anchor=keyword))
        assert config.interval_anchor == keyword

    def test_an_explicit_timestamp_is_type_preserved(self):
        config = ExperimentConfig.model_validate(
            base_payload(interval_anchor="2024-06-30 21:00:00")
        )
        assert config.interval_anchor == datetime(2024, 6, 30, 21, 0)

    @pytest.mark.parametrize("bad", ["noon", 20240101, "2024-13-01"])
    def test_every_rejection_names_all_three_forms(self, bad):
        """`interval_anchor: noon` must not read as 'not a timestamp' — the
        message has to mention that two keywords exist."""
        with pytest.raises(ValidationError, match="is not a valid anchor") as exc:
            ExperimentConfig.model_validate(base_payload(interval_anchor=bad))
        message = str(exc.value)
        assert "'midnight'" in message and "'start'" in message and "timestamp" in message


class TestDatesAndMisc:
    def test_horizon_equal_to_start_is_an_empty_window(self):
        """The horizon is EXCLUSIVE, so equality is a zero-length experiment."""
        with pytest.raises(ValidationError, match="is not after start_ts"):
            ExperimentConfig.model_validate(base_payload(horizon_ts="2024-07-01"))

    def test_horizon_before_start(self):
        with pytest.raises(ValidationError, match="is not after start_ts"):
            ExperimentConfig.model_validate(base_payload(horizon_ts="2024-06-30"))

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
start_ts: 2024-07-01
horizon_ts: 2024-07-29
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
