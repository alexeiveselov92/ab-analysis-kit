"""NTF-1 tests: the send seam between a finished run and the channels.

``docs/specs/m12-implementation-plan.md`` NTF-1. What the suite pins is the
posture, not the plumbing: every number in a message is
``readout.evaluate()``'s (no recomputation anywhere in ``dispatch.py``), the
two ``on:`` filters intersect rather than union, an experiment nobody computed
sends nothing at all, and NO channel failure — raising, lying, unconstructable
— can stop the others or reach the caller.

Fixture shape is ``tests/tuning/test_overview.py``'s: real ``save_results``
rows through the fake manager, over both flavours.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from abkit.config import ProjectConfig
from abkit.config.experiment_config import ExperimentConfig
from abkit.config.profile import NotificationChannelConfig
from abkit.config.signals import SIGNAL_KINDS
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._notify_states import notice_state_key
from abkit.database.internal_tables._results import RESULT_COLUMNS
from abkit.notify.base import BaseChannel, ReadoutData
from abkit.notify.dispatch import (
    dispatch_calibration_red,
    dispatch_experiment_signals,
    dispatch_pipeline_error,
    dispatch_stale,
    load_experiment_readout,
    passes_filter,
    pipeline_error_notice,
    readout_data_from_verdict,
    resolve_channels,
    signal_kinds_for,
)
from abkit.notify.factory import ROUTING_KEYS, ChannelFactory
from abkit.pipeline._types import BacklogEntry
from tests._helpers.fake_db import FakeDatabaseManager

START = datetime(2026, 1, 1)
PROJECT = ProjectConfig.model_validate({"name": "shop", "default_profile": "dev"})


@pytest.fixture(params=[False, True], ids=["sql-like", "clickhouse-like"])
def tables(request) -> InternalTablesManager:
    manager = InternalTablesManager(FakeDatabaseManager(clickhouse_like=request.param))
    manager.ensure_tables()
    return manager


def make_experiment(**overrides) -> ExperimentConfig:
    config = {
        "name": "ntf_exp",
        "description": "Checkout redesign",
        "start_ts": "2026-01-01",
        "horizon_ts": "2026-01-15",
        "unit_key": "user_id",
        "timezone": "Europe/Berlin",
        "assignment": {
            "query": "SELECT 1",
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
        },
        "alpha": 0.05,
        "correction": "none",
        "comparisons": [
            {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
        ],
    }
    config.update(overrides)
    return ExperimentConfig.model_validate(config)


def make_row(experiment: ExperimentConfig, metric="revenue", name_2="treatment", **overrides):
    comparison = experiment.get_comparison(metric)
    day = overrides.pop("day", 14)
    end_ts = START + timedelta(days=day)
    row = {
        "experiment": experiment.name,
        "metric": metric,
        "is_main_metric": comparison.is_main_metric,
        "is_guardrail": comparison.is_guardrail,
        "method_name": comparison.method.name,
        "method_params": comparison.method.canonical_params_json,
        "method_config_id": comparison.method.method_config_id,
        "name_1": "control",
        "name_2": name_2,
        "start_ts": START,
        "end_ts": end_ts,
        "window_seconds": day * 86400,
        "elapsed_days": float(day),
        "value_1": 10.0,
        "value_2": 11.0,
        "std_1": 2.0,
        "std_2": 2.0,
        "cov_value_1": None,
        "cov_value_2": None,
        "cov_std_1": None,
        "cov_std_2": None,
        "corr_coef_1": None,
        "corr_coef_2": None,
        "size_1": 1000,
        "size_2": 1000,
        "alpha": 0.05,
        "pvalue": 0.001,
        "effect": 0.1,
        "left_bound": 0.05,
        "right_bound": 0.15,
        "ci_length": 0.10,
        "reject": True,
        "mde_1": 0.04,
        "mde_2": 0.04,
        "srm_flag": False,
        "srm_pvalue": 0.8,
        "decision_blocked": False,
        "insufficient_data": False,
        "ci_kind": "fixed",
        "is_horizon": day >= 14,
        "warnings": None,
        "diagnostics": None,
        "metric_query": "SELECT template",
        "metric_rendered_query": "SELECT rendered",
        "watermark_ts": end_ts,
    }
    row.update(overrides)
    return row


def save_rows(tables: InternalTablesManager, rows: list[dict]) -> None:
    batch = {col: np.array([row[col] for row in rows], dtype=object) for col in RESULT_COLUMNS}
    tables.save_results(batch)


def seed(tables, experiment, metric="revenue", name_2="treatment", days=14, **overrides):
    rows = [
        make_row(experiment, metric=metric, name_2=name_2, day=day, **overrides)
        for day in range(1, days + 1)
    ]
    save_rows(tables, rows)
    return rows


# ---------------------------------------------------------------- fake channel
class RecordingChannel(BaseChannel):
    """A channel that records what it was handed. Registered as a real type, so
    the payload travels the SAME factory path a slack webhook would."""

    #: every (label, readout) this run — class-level, the sends are the assertion
    sent: list[tuple[str, ReadoutData]] = []

    def __init__(self, label: str = "rec", mode: str = "ok"):
        self.label = label
        self.mode = mode

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        if self.mode == "raise":
            raise RuntimeError("boom")
        RecordingChannel.sent.append((self.label, readout))
        return self.mode != "false"


@pytest.fixture(autouse=True)
def recording_channel(monkeypatch):
    RecordingChannel.sent = []
    monkeypatch.setitem(ChannelFactory.CHANNEL_TYPES, "recording", RecordingChannel)
    return RecordingChannel


def channel(label: str, mode: str = "ok", **extra) -> NotificationChannelConfig:
    return NotificationChannelConfig(type="recording", label=label, mode=mode, **extra)


def dispatch(experiment, tables, channels, echo=None, project=PROJECT, states=None):
    """One dispatch pass. ``states=None`` disables NTF-3's dedup, which is what
    the routing/rendering tests want — pass ``states=tables`` to exercise it."""
    loaded = load_experiment_readout(experiment, tables, project=project)
    assert loaded is not None
    readout, rows = loaded
    return dispatch_experiment_signals(
        experiment=experiment,
        readout=readout,
        rows=rows,
        channels_cfg=channels,
        project_name=project.name,
        states=states,
        echo=echo if echo is not None else (lambda line: None),
    )


# ------------------------------------------------------------------ the payload
class TestReadoutDataFromVerdict:
    def test_every_number_is_copied_off_the_verdict(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        readout, rows = load_experiment_readout(experiment, tables, project=PROJECT)
        verdict = readout.verdicts[0]

        data = readout_data_from_verdict(
            experiment, verdict, readout, project_name="shop", rows=rows
        )

        assert (data.effect, data.pvalue) == (verdict.effect, verdict.pvalue)
        assert (data.left_bound, data.right_bound) == (verdict.left_bound, verdict.right_bound)
        assert (data.alpha, data.elapsed_days) == (verdict.alpha, verdict.elapsed_days)
        assert data.verdict == verdict.verdict
        assert data.weekly_cycle_pct == verdict.weekly_cycle_pct
        assert (data.name_1, data.name_2) == (verdict.name_1, verdict.name_2)
        # the SRM gate is a whole-experiment property, taken from the readout
        assert (data.srm_flag, data.srm_pvalue) == (readout.srm_flag, readout.srm_pvalue)
        # display context that only the config knows
        assert data.experiment == "ntf_exp"
        assert data.timezone == "Europe/Berlin"
        assert data.project_name == "shop"
        assert data.description == "Checkout redesign"
        assert data.help_url  # the readout guide always rides along

    def test_timestamp_is_the_look_not_the_wall_clock(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        readout, rows = load_experiment_readout(experiment, tables, project=PROJECT)
        verdict = readout.verdicts[0]

        data = readout_data_from_verdict(experiment, verdict, readout, rows=rows)

        # the cutoff the numbers are AS OF — a message read an hour later must
        # still say which look it describes
        assert data.timestamp == verdict.end_ts

    def test_sizes_come_from_this_pairs_own_look(self, tables):
        """A later look on ANOTHER metric must not lend this pair its n."""
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "clicks", "method": {"name": "t-test"}},
            ]
        )
        seed(tables, experiment, metric="revenue", days=10, size_1=111, size_2=222)
        # the secondary metric runs ahead, with wildly different sizes
        seed(tables, experiment, metric="clicks", days=14, size_1=999, size_2=888)
        readout, rows = load_experiment_readout(experiment, tables, project=PROJECT)
        verdict = readout.verdicts[0]
        assert verdict.metric == "revenue"

        data = readout_data_from_verdict(experiment, verdict, readout, rows=rows)

        assert (data.n_1, data.n_2) == (111, 222)

    def test_sizes_are_absent_rather_than_wrong_when_the_look_is_missing(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        readout, rows = load_experiment_readout(experiment, tables, project=PROJECT)
        verdict = readout.verdicts[0]

        data = readout_data_from_verdict(experiment, verdict, readout, rows=[])

        assert (data.n_1, data.n_2) == (None, None)

    @pytest.mark.parametrize(
        "params, expected",
        [
            ({}, True),  # the method's own ParamSpec default
            ({"test_type": "relative"}, True),
            ({"test_type": "absolute"}, False),
        ],
    )
    def test_relative_follows_the_configured_estimand(self, tables, params, expected):
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "method": {"name": "t-test", "params": params},
                }
            ]
        )
        seed(tables, experiment)
        readout, rows = load_experiment_readout(experiment, tables, project=PROJECT)

        data = readout_data_from_verdict(experiment, readout.verdicts[0], readout, rows=rows)

        assert data.relative is expected

    def test_every_registered_method_declares_the_estimand(self):
        """A trip-wire, not a survey. ``_is_relative`` falls back to *relative*
        for a method with no ``test_type`` spec, and today every one of the 12
        declares it — so the fallback is unreachable and the message can never
        render an absolute effect as a percentage. A new method (M15) that omits
        the param makes it reachable, and this failure is where its author
        decides what the notification should say."""
        from abkit.stats.registry import available_methods, get_method_class

        missing = [
            name
            for name in available_methods()
            if not any(spec.name == "test_type" for spec in get_method_class(name).param_specs)
        ]

        assert missing == []

    def test_mentions_ride_from_the_experiment_block(self, tables):
        experiment = make_experiment(notify={"mentions": ["growth-team", "ana"]})
        seed(tables, experiment)
        readout, rows = load_experiment_readout(experiment, tables, project=PROJECT)

        data = readout_data_from_verdict(experiment, readout.verdicts[0], readout, rows=rows)

        assert data.mentions == ["growth-team", "ana"]


# -------------------------------------------------------------- what to send at
class TestLoadExperimentReadout:
    def test_a_project_that_never_ran_has_nothing_to_say(self):
        """No ``_ab_results`` at all — silence, not an INCONCLUSIVE message."""
        bare = InternalTablesManager(FakeDatabaseManager())
        assert load_experiment_readout(make_experiment(), bare, project=PROJECT) is None

    def test_an_experiment_with_no_rows_of_its_own_sends_nothing(self, tables):
        """The m11 DASH-7 finding in message form: ``evaluate()`` over zero rows
        answers INCONCLUSIVE, which is a verdict about DATA — sending it for an
        experiment nobody computed reports a finding where there is no
        observation."""
        other = make_experiment(name="another_exp")
        seed(tables, other)

        assert load_experiment_readout(make_experiment(), tables, project=PROJECT) is None

    def test_rows_for_undeclared_arm_pairs_are_dropped(self, tables):
        """A renamed arm leaves rows behind; they must not reach ``evaluate``
        (they would inflate the BH family) and, alone, they are 'no data'."""
        experiment = make_experiment()
        seed(tables, experiment, name_2="variant_b")  # not a declared variant

        assert load_experiment_readout(experiment, tables, project=PROJECT) is None

    def test_a_real_series_yields_verdicts_and_its_rows(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)

        readout, rows = load_experiment_readout(experiment, tables, project=PROJECT)

        assert [v.metric for v in readout.verdicts] == ["revenue"]
        assert len(rows) == 14


# ------------------------------------------------------------------- the filter
class TestPassesFilter:
    @pytest.mark.parametrize(
        "channel_on, experiment_on, expected",
        [
            (None, None, True),  # both open
            (["readout"], None, True),
            (None, ["readout"], True),
            (["readout"], ["readout"], True),
            (["srm"], None, False),  # the channel does not accept it
            (None, ["error"], False),  # the experiment does not send it
            (["readout"], ["error"], False),  # INTERSECTION: neither side re-opens
            (["error"], ["readout"], False),
        ],
    )
    def test_intersection_never_union(self, channel_on, experiment_on, expected):
        assert passes_filter("readout", channel_on, experiment_on) is expected

    def test_every_declared_kind_is_filterable(self):
        """The six kinds are one vocabulary — a config may name any of them
        today even though only ``readout`` fires yet."""
        for kind in SIGNAL_KINDS:
            assert passes_filter(kind, [kind], [kind]) is True
            assert passes_filter(kind, ["readout"], None) is (kind == "readout")


# ----------------------------------------------------------------- the channels
class TestResolveChannels:
    def test_no_notify_block_means_every_configured_channel(self):
        channels = {"a": channel("a"), "b": channel("b")}

        resolved, warnings = resolve_channels(make_experiment(), channels)

        assert [name for name, _ in resolved] == ["a", "b"]
        assert warnings == []

    def test_an_empty_channel_list_is_still_everything(self):
        """``notify:`` written for its ``mentions`` alone must not silence the
        experiment — D1's default is a property of the CHANNEL list being empty."""
        experiment = make_experiment(notify={"mentions": ["ana"]})

        resolved, _ = resolve_channels(experiment, {"a": channel("a")})

        assert [name for name, _ in resolved] == ["a"]

    def test_a_named_list_narrows_and_keeps_its_order(self):
        experiment = make_experiment(notify={"channels": ["b", "a", "b"]})
        channels = {"a": channel("a"), "b": channel("b"), "c": channel("c")}

        resolved, warnings = resolve_channels(experiment, channels)

        assert [name for name, _ in resolved] == ["b", "a"]
        assert warnings == []

    def test_an_unknown_channel_warns_and_the_rest_still_go(self):
        experiment = make_experiment(notify={"channels": ["typo", "a"]})

        resolved, warnings = resolve_channels(experiment, {"a": channel("a")})

        assert [name for name, _ in resolved] == ["a"]
        assert len(warnings) == 1
        assert "typo" in warnings[0] and "a" in warnings[0]


# ------------------------------------------------------------------ the sending
class TestDispatchExperimentSignals:
    def test_one_message_per_verdict_to_every_channel(self, tables):
        experiment = make_experiment(
            assignment={
                "query": "SELECT 1",
                "variants": ["control", "t1", "t2"],
                "expected_split": {"control": 0.34, "t1": 0.33, "t2": 0.33},
            }
        )
        seed(tables, experiment, name_2="t1")
        seed(tables, experiment, name_2="t2")

        sent = dispatch(experiment, tables, {"a": channel("a"), "b": channel("b")})

        # 2 treatment arms × 2 channels
        assert sent == 4
        assert sorted(label for label, _ in RecordingChannel.sent) == ["a", "a", "b", "b"]
        assert {data.name_2 for _, data in RecordingChannel.sent} == {"t1", "t2"}

    def test_a_raising_channel_never_blocks_the_others(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        lines: list[str] = []

        sent = dispatch(
            experiment,
            tables,
            {"bad": channel("bad", mode="raise"), "good": channel("good")},
            echo=lines.append,
        )

        assert sent == 1
        assert [label for label, _ in RecordingChannel.sent] == ["good"]
        assert any("bad" in line for line in lines)

    def test_a_delivery_failure_never_echoes_the_credential(self, tables):
        """``requests`` embeds the full URL in its exception strings, and a
        webhook/bot URL carries its token in the PATH — this line reaches stdout
        and CI logs, so it must carry ``describe_error``, not the raw exception
        (the discipline ``webhook.py``/``telegram.py`` already follow)."""
        experiment = make_experiment()
        seed(tables, experiment)
        secret = "https://hooks.slack.com/services/T000/B000/SUPERSECRETTOKEN"
        lines: list[str] = []

        class LeakyChannel(RecordingChannel):
            def send(self, readout, template=None):
                raise RuntimeError(f"POST to {secret} failed")

        ChannelFactory.CHANNEL_TYPES["leaky"] = LeakyChannel
        try:
            dispatch(
                experiment,
                tables,
                {"leaky": NotificationChannelConfig(type="leaky", label="leaky")},
                echo=lines.append,
            )
        finally:
            del ChannelFactory.CHANNEL_TYPES["leaky"]

        assert lines, "a raising channel must still be reported"
        assert not any("SUPERSECRETTOKEN" in line for line in lines)
        assert any("RuntimeError" in line for line in lines)

    def test_a_channel_that_cannot_be_constructed_is_one_line(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        lines: list[str] = []

        sent = dispatch(
            experiment,
            tables,
            {
                "broken": NotificationChannelConfig(type="recording", nonsense=1),
                "good": channel("good"),
            },
            echo=lines.append,
        )

        assert sent == 1
        assert any("broken" in line for line in lines)

    def test_a_false_return_is_reported_not_counted(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        lines: list[str] = []

        sent = dispatch(experiment, tables, {"a": channel("a", mode="false")}, echo=lines.append)

        assert sent == 0
        assert any("a" in line for line in lines)

    def test_a_channel_scoped_to_other_kinds_gets_no_readout(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)

        sent = dispatch(
            experiment,
            tables,
            {"urgent": channel("urgent", on=["srm", "error"]), "routine": channel("routine")},
        )

        assert sent == 1
        assert [label for label, _ in RecordingChannel.sent] == ["routine"]

    def test_an_experiment_scoped_to_other_kinds_sends_nothing(self, tables):
        experiment = make_experiment(notify={"on": ["error"]})
        seed(tables, experiment)

        sent = dispatch(experiment, tables, {"a": channel("a"), "b": channel("b")})

        assert sent == 0
        assert RecordingChannel.sent == []

    def test_no_configured_channels_is_a_no_op(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)

        assert dispatch(experiment, tables, {}) == 0


class TestSrmIsTheSameMessageReclassified:
    """NTF-2: a readout whose SRM gate failed answers to BOTH kinds."""

    def test_an_srm_failed_readout_reaches_an_urgent_only_channel(self, tables):
        experiment = make_experiment()
        seed(tables, experiment, srm_flag=True, srm_pvalue=1e-9)

        sent = dispatch(experiment, tables, {"oncall": channel("oncall", on=["srm", "error"])})

        assert sent == 1
        assert RecordingChannel.sent[0][1].srm_flag is True

    def test_a_clean_readout_does_not(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)

        assert dispatch(experiment, tables, {"oncall": channel("oncall", on=["srm"])}) == 0

    def test_a_channel_accepting_both_kinds_still_gets_exactly_one_message(self, tables):
        """Delivery asks 'does ANY kind pass', so the same payload cannot be
        sent twice to a channel that happens to accept both of its kinds."""
        experiment = make_experiment()
        seed(tables, experiment, srm_flag=True, srm_pvalue=1e-9)

        sent = dispatch(experiment, tables, {"both": channel("both", on=["readout", "srm"])})

        assert sent == 1
        assert len(RecordingChannel.sent) == 1

    def test_an_experiment_filter_still_wins(self, tables):
        """Intersection, not union: scoping the experiment to `error` silences
        its SRM-failed readout even on a channel that accepts `srm`."""
        experiment = make_experiment(notify={"on": ["error"]})
        seed(tables, experiment, srm_flag=True, srm_pvalue=1e-9)

        assert dispatch(experiment, tables, {"oncall": channel("oncall", on=["srm"])}) == 0

    def test_signal_kinds_are_read_off_the_payload(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        readout, rows = load_experiment_readout(experiment, tables, project=PROJECT)
        clean = readout_data_from_verdict(experiment, readout.verdicts[0], readout, rows=rows)

        assert signal_kinds_for(clean) == ("readout",)
        assert signal_kinds_for(replace(clean, srm_flag=True)) == ("readout", "srm")
        assert signal_kinds_for(replace(clean, kind="error")) == ("error",)


class TestPipelineErrorNotice:
    """NTF-2: the one signal with no readout behind it."""

    def test_the_payload_claims_no_measurement(self):
        experiment = make_experiment()

        notice = pipeline_error_notice(experiment, "warehouse down", project_name="shop")

        assert notice.kind == "error"
        assert notice.notice == "warehouse down"
        # every statistical field stays empty: the run never produced one, and a
        # zero or a "FLAT" here would be a claim about the experiment
        assert (notice.effect, notice.pvalue, notice.alpha) == (None, None, None)
        assert (notice.left_bound, notice.right_bound) == (None, None)
        assert (notice.verdict, notice.metric, notice.name_1, notice.name_2) == ("", "", "", "")
        assert notice.experiment == "ntf_exp"
        assert notice.project_name == "shop"
        assert notice.timestamp is not None  # wall-clock: the news is that it failed NOW

    def test_it_reaches_an_urgent_only_channel_and_not_a_readout_only_one(self, tables):
        experiment = make_experiment()
        lines: list[str] = []

        sent = dispatch_pipeline_error(
            experiment=experiment,
            error="warehouse down",
            channels_cfg={
                "oncall": channel("oncall", on=["srm", "error"]),
                "routine": channel("routine", on=["readout"]),
            },
            project_name="shop",
            echo=lines.append,
        )

        assert sent == 1
        assert [label for label, _ in RecordingChannel.sent] == ["oncall"]

    def test_it_does_not_need_persisted_rows(self, tables):
        """Unlike the readout path — the absence of a result is what it reports."""
        experiment = make_experiment()  # nothing seeded at all

        sent = dispatch_pipeline_error(
            experiment=experiment,
            error="boom",
            channels_cfg={"a": channel("a")},
            echo=lambda line: None,
        )

        assert sent == 1

    def test_a_raising_channel_is_still_fail_soft(self):
        experiment = make_experiment()
        lines: list[str] = []

        sent = dispatch_pipeline_error(
            experiment=experiment,
            error="boom",
            channels_cfg={"bad": channel("bad", mode="raise"), "good": channel("good")},
            echo=lines.append,
        )

        assert sent == 1
        assert any("bad" in line for line in lines)

    def test_mentions_ride_along(self):
        experiment = make_experiment(notify={"mentions": ["oncall-eng"]})

        notice = pipeline_error_notice(experiment, "boom")

        assert notice.mentions == ["oncall-eng"]

    def test_it_goes_through_send_notice_not_send(self, tables):
        """The seam channels may override — `email` does, for its HTML card."""
        experiment = make_experiment()
        calls: list[str] = []

        class PickyChannel(RecordingChannel):
            def send(self, readout, template=None):
                calls.append("send")
                return super().send(readout, template)

            def send_notice(self, notice):
                calls.append("send_notice")
                return super().send_notice(notice)

        ChannelFactory.CHANNEL_TYPES["picky"] = PickyChannel
        try:
            dispatch_pipeline_error(
                experiment=experiment,
                error="boom",
                channels_cfg={"p": NotificationChannelConfig(type="picky", label="p")},
                echo=lambda line: None,
            )
        finally:
            del ChannelFactory.CHANNEL_TYPES["picky"]

        # send_notice FIRST, and it delegates to send by default
        assert calls == ["send_notice", "send"]

    def test_send_notice_refuses_a_verdict_payload(self, tables):
        """The two entry points are not interchangeable: a readout routed
        through the notice seam would render with no effect block at all."""
        experiment = make_experiment()
        seed(tables, experiment)
        readout, rows = load_experiment_readout(experiment, tables, project=PROJECT)
        verdict_payload = readout_data_from_verdict(
            experiment, readout.verdicts[0], readout, rows=rows
        )

        with pytest.raises(ValueError, match="send_notice expects"):
            RecordingChannel().send_notice(verdict_payload)


class TestVerdictDedup:
    """NTF-3 end to end: the state store decides whether a message goes at all."""

    def test_an_unchanged_verdict_is_sent_once_not_twice(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)

        first = dispatch(experiment, tables, {"a": channel("a")}, states=tables)
        second = dispatch(experiment, tables, {"a": channel("a")}, states=tables)

        assert (first, second) == (1, 0)
        assert len(RecordingChannel.sent) == 1

    def test_a_flip_between_runs_sends_again(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        assert dispatch(experiment, tables, {"a": channel("a")}, states=tables) == 1
        first_verdict = RecordingChannel.sent[0][1].verdict

        # a later look that inverts the effect — a genuinely new decision
        save_rows(
            tables,
            [
                make_row(
                    experiment,
                    day=15,
                    effect=-0.2,
                    left_bound=-0.3,
                    right_bound=-0.1,
                    pvalue=0.0001,
                )
            ],
        )
        second = dispatch(experiment, tables, {"a": channel("a")}, states=tables)

        assert second == 1
        assert RecordingChannel.sent[-1][1].verdict != first_verdict

    def test_the_state_row_records_what_was_announced(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)

        dispatch(experiment, tables, {"a": channel("a")}, states=tables)

        comparison = experiment.get_comparison("revenue")
        state = tables.get_notify_state(
            experiment.name,
            "revenue",
            "control",
            "treatment",
            comparison.method.method_config_id,
        )
        assert state["notify_count"] == 1
        assert state["last_verdict"] == RecordingChannel.sent[0][1].verdict
        assert state["last_notified_at"] is not None

    def test_a_message_nobody_received_is_not_recorded(self, tables):
        """The flip must survive a broken channel: recording an announcement
        that reached nobody would make the next run treat it as old news and
        lose it permanently."""
        experiment = make_experiment()
        seed(tables, experiment)

        first = dispatch(experiment, tables, {"bad": channel("bad", mode="raise")}, states=tables)
        assert first == 0

        second = dispatch(experiment, tables, {"good": channel("good")}, states=tables)

        assert second == 1

    def test_a_filtered_out_verdict_is_not_recorded_either(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)

        dispatch(experiment, tables, {"urgent": channel("urgent", on=["srm"])}, states=tables)
        # nothing was announced, so the next run with a routine channel still speaks
        assert dispatch(experiment, tables, {"a": channel("a")}, states=tables) == 1

    def test_a_new_srm_breach_reannounces_the_same_word(self, tables):
        """The hazard in dispatch form (the unit rule is pinned in
        test_cooldown): a pre-horizon pair keeps saying INCONCLUSIVE, so the SRM
        alarm must not be deduped away."""
        experiment = make_experiment()
        seed(tables, experiment, days=3)  # pre-horizon ⇒ INCONCLUSIVE
        assert dispatch(experiment, tables, {"a": channel("a")}, states=tables) == 1
        first = RecordingChannel.sent[0][1]
        assert first.verdict == "INCONCLUSIVE" and first.srm_flag is False

        save_rows(tables, [make_row(experiment, day=4, srm_flag=True, srm_pvalue=1e-9)])
        second = dispatch(experiment, tables, {"a": channel("a")}, states=tables)

        assert second == 1
        assert RecordingChannel.sent[-1][1].verdict == "INCONCLUSIVE"
        assert RecordingChannel.sent[-1][1].srm_flag is True

    def test_each_comparison_dedups_independently(self, tables):
        experiment = make_experiment(
            assignment={
                "query": "SELECT 1",
                "variants": ["control", "t1", "t2"],
                "expected_split": {"control": 0.34, "t1": 0.33, "t2": 0.33},
            }
        )
        seed(tables, experiment, name_2="t1")
        seed(tables, experiment, name_2="t2")

        assert dispatch(experiment, tables, {"a": channel("a")}, states=tables) == 2
        assert dispatch(experiment, tables, {"a": channel("a")}, states=tables) == 0

    def test_without_a_state_store_nothing_is_deduped(self, tables):
        """`states=None` is explicit, not a default — but it must still work
        (the pipeline-error path never consults the store)."""
        experiment = make_experiment()
        seed(tables, experiment)

        assert dispatch(experiment, tables, {"a": channel("a")}, states=None) == 1
        assert dispatch(experiment, tables, {"a": channel("a")}, states=None) == 1

    def test_a_pipeline_error_is_never_deduped(self, tables):
        """A run that failed twice failed twice."""
        experiment = make_experiment()

        for _ in range(2):
            sent = dispatch_pipeline_error(
                experiment=experiment,
                error="warehouse down",
                channels_cfg={"a": channel("a")},
                echo=lambda line: None,
            )
            assert sent == 1

    def test_the_skip_is_announced_to_the_operator(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        lines: list[str] = []

        dispatch(experiment, tables, {"a": channel("a")}, states=tables)
        dispatch(experiment, tables, {"a": channel("a")}, echo=lines.append, states=tables)

        assert any("unchanged" in line for line in lines)


class TestRoutingKeysNeverReachAChannel:
    def test_on_is_stripped_by_the_factory(self):
        """``notification_channels`` is ``extra='allow'``: every sibling key is
        forwarded to the constructor, so an unstripped ``on:`` would break the
        channel — in ``abk test-report`` too, which never asked about routing."""
        cfg = NotificationChannelConfig(type="recording", label="x", on=["readout"])

        built = ChannelFactory.create_from_config(cfg.model_dump())

        assert isinstance(built, RecordingChannel)
        assert built.label == "x"

    def test_every_declared_field_is_classified(self):
        """The anti-rot law, not a restatement of the constant: a field DECLARED
        on the config model is abkit's own vocabulary, so it must be either
        ``type`` (popped by name) or routing (popped by this list). Adding one
        and forgetting it here forwards it to the constructor, which breaks
        every channel the moment an operator writes it."""
        declared = set(NotificationChannelConfig.model_fields) - {"type"}

        assert declared == set(ROUTING_KEYS)


# ------------------------------------------------------- NTF-5: the two recurring kinds
def make_cell(metric="revenue", fpr=0.05, budget=0.075, method="t-test", config_id="mc1"):
    """The `fpr`/`budget`/identity slice of a `CellResult` the dispatcher reads.

    Deliberately a stand-in rather than a full `CellResult`: what NTF-5 routes
    is exactly these five attributes, and pinning the shape here is what keeps
    a future field addition from silently changing which cells are red.
    """
    return SimpleNamespace(
        metric=metric,
        method_name=method,
        method_config_id=config_id,
        fpr=fpr,
        budget=budget,
    )


class TestStaleSignal:
    def test_it_names_the_metrics_and_reads_as_a_notice(self, tables):
        experiment = make_experiment()

        sent = dispatch_stale(
            experiment=experiment,
            entries=[BacklogEntry("revenue", 14 * 86400.0)],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )

        assert sent == 1
        payload = RecordingChannel.sent[0][1]
        assert payload.kind == "stale"
        assert "revenue by 336.0h" in payload.notice
        # a notice claims no measurement: NTF-2's rule, unchanged here
        assert (payload.effect, payload.pvalue, payload.verdict) == (None, None, "")

    def test_the_message_does_not_claim_the_data_is_stale_now(self, tables):
        """The run that reports a backlog is the run that drains it — the PLAN
        stage detects, the COMPUTE stage computes. What is behind is the
        SCHEDULE, and a message saying otherwise would send an operator looking
        at a warehouse that is fine."""
        experiment = make_experiment()

        dispatch_stale(
            experiment=experiment,
            entries=[BacklogEntry("revenue", 14 * 86400.0)],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )

        notice = RecordingChannel.sent[0][1].notice
        assert "SCHEDULE" in notice and "computed the missing looks" in notice
        assert RecordingChannel().verdict_word(RecordingChannel.sent[0][1]) == (
            "Schedule fell behind"
        )

    def test_no_backlog_sends_nothing(self, tables):
        experiment = make_experiment()

        sent = dispatch_stale(
            experiment=experiment,
            entries=[],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )

        assert (sent, RecordingChannel.sent) == (0, [])

    def test_the_same_backlog_is_announced_once(self, tables):
        experiment = make_experiment()
        entries = [BacklogEntry("revenue", 14 * 86400.0)]

        def send(entries):
            return dispatch_stale(
                experiment=experiment,
                entries=entries,
                channels_cfg={"a": channel("a")},
                states=tables,
                echo=lambda line: None,
            )

        first = send(entries)
        # the lag grows with every run: a signature built from the SENTENCE
        # would re-announce forever
        second = send([BacklogEntry("revenue", 15 * 86400.0)])

        assert (first, second) == (1, 0)

    def test_a_second_metric_falling_behind_announces(self, tables):
        experiment = make_experiment()

        def send(entries):
            return dispatch_stale(
                experiment=experiment,
                entries=entries,
                channels_cfg={"a": channel("a")},
                states=tables,
                echo=lambda line: None,
            )

        send([BacklogEntry("revenue", 14 * 86400.0)])
        widened = send([BacklogEntry("revenue", 14 * 86400.0), BacklogEntry("clicks", 86400.0)])

        assert widened == 1

    def test_a_gap_that_returns_after_clearing_is_news_again(self, tables):
        """The recovery reset. Without it the stored signature outlives the
        condition, and the SAME metric falling behind again next month — the
        second outage — dedups against the first and is never announced."""
        experiment = make_experiment()

        def send(entries):
            return dispatch_stale(
                experiment=experiment,
                entries=entries,
                channels_cfg={"a": channel("a")},
                states=tables,
                echo=lambda line: None,
            )

        assert send([BacklogEntry("revenue", 14 * 86400.0)]) == 1
        assert send([]) == 0  # caught up: nothing sent, the signature is cleared
        assert send([BacklogEntry("revenue", 20 * 86400.0)]) == 1

    def test_a_cooldown_lets_an_unchanged_backlog_repeat(self, tables):
        experiment = make_experiment(notify={"cooldown_seconds": 0})

        def send():
            return dispatch_stale(
                experiment=experiment,
                entries=[BacklogEntry("revenue", 14 * 86400.0)],
                channels_cfg={"a": channel("a")},
                states=tables,
                echo=lambda line: None,
            )

        assert (send(), send()) == (1, 1)

    def test_a_message_nobody_received_is_not_recorded(self, tables):
        """The NTF-3 rule, on this path too: every channel failing must not
        make the next run treat the condition as old news."""
        experiment = make_experiment()

        def send(mode):
            return dispatch_stale(
                experiment=experiment,
                entries=[BacklogEntry("revenue", 14 * 86400.0)],
                channels_cfg={"a": channel("a", mode=mode)},
                states=tables,
                echo=lambda line: None,
            )

        assert send("raise") == 0
        assert send("ok") == 1

    def test_the_experiment_filter_still_applies(self, tables):
        experiment = make_experiment(notify={"on": ["readout"]})

        sent = dispatch_stale(
            experiment=experiment,
            entries=[BacklogEntry("revenue", 14 * 86400.0)],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )

        assert sent == 0


class TestCalibrationRedSignal:
    def test_only_over_budget_cells_fire(self, tables):
        experiment = make_experiment()

        sent = dispatch_calibration_red(
            experiment=experiment,
            cells=[make_cell(fpr=0.05, budget=0.075), make_cell(metric="clicks", fpr=0.07)],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )

        assert (sent, RecordingChannel.sent) == (0, [])

    def test_a_red_cell_names_itself_and_its_budget(self, tables):
        experiment = make_experiment()

        sent = dispatch_calibration_red(
            experiment=experiment,
            cells=[make_cell(fpr=0.12, budget=0.075), make_cell(metric="clicks", fpr=0.04)],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )

        assert sent == 1
        payload = RecordingChannel.sent[0][1]
        assert payload.kind == "calibration_red"
        assert "t-test on revenue" in payload.notice
        assert "12.0%" in payload.notice and "7.5%" in payload.notice
        assert "1 of 2" in payload.notice

    def test_an_unmeasurable_cell_is_not_red(self, tables):
        """A degenerate cell scores `fpr=None` — "could not measure", which is
        not "exceeds its budget"; claiming otherwise would alarm on missing
        data (the same `is not None` guard `_verdict` uses)."""
        experiment = make_experiment()

        sent = dispatch_calibration_red(
            experiment=experiment,
            cells=[make_cell(fpr=None), make_cell(metric="clicks", budget=None, fpr=0.9)],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )

        assert sent == 0

    def test_the_same_red_cell_is_announced_once(self, tables):
        experiment = make_experiment()

        def send(fpr):
            return dispatch_calibration_red(
                experiment=experiment,
                cells=[make_cell(fpr=fpr)],
                channels_cfg={"a": channel("a")},
                states=tables,
                echo=lambda line: None,
            )

        # the second validation measures a different FPR on the same red cell:
        # a signature carrying the NUMBER would re-announce every run
        assert (send(0.12), send(0.13)) == (1, 0)

    def test_two_cells_of_one_method_on_one_metric_are_distinct(self, tables):
        """Identity is `metric·method_config_id`, not the method name: one
        metric can carry two cells of the same method with different params,
        and collapsing them would dedup the second one away."""
        experiment = make_experiment()

        def send(cells):
            return dispatch_calibration_red(
                experiment=experiment,
                cells=cells,
                channels_cfg={"a": channel("a")},
                states=tables,
                echo=lambda line: None,
            )

        first = send([make_cell(fpr=0.12, config_id="mc1")])
        second = send([make_cell(fpr=0.12, config_id="mc1"), make_cell(fpr=0.12, config_id="mc2")])

        assert (first, second) == (1, 1)

    def test_a_fixed_calibration_is_news_when_it_breaks_again(self, tables):
        experiment = make_experiment()

        def send(fpr):
            return dispatch_calibration_red(
                experiment=experiment,
                cells=[make_cell(fpr=fpr)],
                channels_cfg={"a": channel("a")},
                states=tables,
                echo=lambda line: None,
            )

        assert send(0.12) == 1
        assert send(0.05) == 0  # back inside budget: cleared, nothing sent
        assert send(0.12) == 1


class TestRecurringStateIsolation:
    def test_the_two_kinds_do_not_share_a_row(self, tables):
        """One experiment can be behind AND miscalibrated; a shared key would
        let one condition dedup the other away."""
        experiment = make_experiment()

        stale = dispatch_stale(
            experiment=experiment,
            entries=[BacklogEntry("revenue", 14 * 86400.0)],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )
        red = dispatch_calibration_red(
            experiment=experiment,
            cells=[make_cell(fpr=0.12)],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )

        assert (stale, red) == (1, 1)

    def test_a_notice_row_cannot_collide_with_a_comparison(self, tables):
        """The sentinel key's real guarantee is the EMPTY arm pair — a variant
        name cannot be empty — not the `__stale__` name, which a metric may
        legitimately carry."""
        assert notice_state_key("stale") == ("__stale__", "", "", "")

        experiment = make_experiment()
        seed(tables, experiment)
        dispatch(experiment, tables, {"a": channel("a")}, states=tables)
        dispatch_stale(
            experiment=experiment,
            entries=[BacklogEntry("revenue", 86400.0)],
            channels_cfg={"a": channel("a")},
            states=tables,
            echo=lambda line: None,
        )

        # the verdict's own state row is untouched by the notice
        verdict_state = tables.get_notify_state(
            experiment.name,
            "revenue",
            "control",
            "treatment",
            experiment.get_comparison("revenue").method.method_config_id,
        )
        assert verdict_state["last_verdict"] not in (None, "")
        assert (
            tables.get_notify_state(experiment.name, *notice_state_key("stale"))["last_verdict"]
            == "revenue"
        )


class TestVerdictChangeKind:
    """NTF-6: the sixth kind, narrower than "was delivered"."""

    def test_a_first_readout_is_not_a_change(self, tables):
        """News, but nothing flipped — a channel asking for flips must not get
        the announcement that merely opened the history."""
        experiment = make_experiment()
        seed(tables, experiment)

        sent = dispatch(
            experiment, tables, {"flips": channel("flips", on=["verdict_change"])}, states=tables
        )

        assert (sent, RecordingChannel.sent) == (0, [])

    def test_a_flip_reaches_a_flips_only_channel(self, tables):
        experiment = make_experiment()
        seed(tables, experiment)
        channels = {
            "flips": channel("flips", on=["verdict_change"]),
            "team": channel("team"),
        }
        first = dispatch(experiment, tables, channels, states=tables)
        assert [label for label, _ in RecordingChannel.sent] == ["team"]

        save_rows(
            tables,
            [
                make_row(
                    experiment, day=15, effect=-0.2, left_bound=-0.3, right_bound=-0.1, pvalue=1e-4
                )
            ],
        )
        second = dispatch(experiment, tables, channels, states=tables)

        assert (first, second) == (1, 2)
        assert sorted(label for label, _ in RecordingChannel.sent[1:]) == ["flips", "team"]
        assert RecordingChannel.sent[-1][1].verdict_changed is True

    def test_a_new_srm_breach_with_the_same_word_is_not_a_flip(self, tables):
        """NTF-3 re-sends it (the gate moved), but the DECISION did not — the
        two kinds must not collapse into each other. The pre-horizon fixture is
        the one where this is observable: a pair sits at INCONCLUSIVE either
        way, which is exactly why NTF-3 deduped on the pair and not the word."""
        experiment = make_experiment()
        seed(tables, experiment, days=3)  # pre-horizon ⇒ INCONCLUSIVE
        channels = {
            "flips": channel("flips", on=["verdict_change"]),
            "oncall": channel("oncall", on=["srm"]),
        }
        dispatch(experiment, tables, {"team": channel("team")}, states=tables)
        announced = RecordingChannel.sent[0][1].verdict
        assert announced == "INCONCLUSIVE"

        save_rows(tables, [make_row(experiment, day=4, srm_flag=True, srm_pvalue=1e-9)])
        dispatch(experiment, tables, channels, states=tables)

        delivered = [label for label, _ in RecordingChannel.sent[1:]]
        assert delivered == ["oncall"]
        assert RecordingChannel.sent[-1][1].verdict == announced  # the word never moved
        assert RecordingChannel.sent[-1][1].verdict_changed is False

    def test_without_a_state_store_it_is_never_claimed(self, tables):
        """There is no "last announced" to compare against, so guessing from
        the current word would invent a flip out of the first message."""
        experiment = make_experiment()
        seed(tables, experiment)

        sent = dispatch(
            experiment, tables, {"flips": channel("flips", on=["verdict_change"])}, states=None
        )

        assert sent == 0

    def test_the_kinds_a_readout_answers_to(self):
        payload = ReadoutData(experiment="e", metric="m", verdict="WIN", name_1="a", name_2="b")
        assert signal_kinds_for(payload) == ("readout",)
        assert signal_kinds_for(replace(payload, verdict_changed=True)) == (
            "readout",
            "verdict_change",
        )
        assert signal_kinds_for(replace(payload, verdict_changed=True, srm_flag=True)) == (
            "readout",
            "verdict_change",
            "srm",
        )
