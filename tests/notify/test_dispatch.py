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

from datetime import datetime, timedelta

import numpy as np
import pytest

from abkit.config import ProjectConfig
from abkit.config.experiment_config import ExperimentConfig
from abkit.config.profile import NotificationChannelConfig
from abkit.config.signals import SIGNAL_KINDS
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._results import RESULT_COLUMNS
from abkit.notify.base import BaseChannel, ReadoutData
from abkit.notify.dispatch import (
    dispatch_experiment_signals,
    load_experiment_readout,
    passes_filter,
    readout_data_from_verdict,
    resolve_channels,
)
from abkit.notify.factory import ROUTING_KEYS, ChannelFactory
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


def dispatch(experiment, tables, channels, echo=None, project=PROJECT):
    loaded = load_experiment_readout(experiment, tables, project=project)
    assert loaded is not None
    readout, rows = loaded
    return dispatch_experiment_signals(
        experiment=experiment,
        readout=readout,
        rows=rows,
        channels_cfg=channels,
        project_name=project.name,
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
