"""``abk run --notify`` — the NTF-1 CLI surface (m12-implementation-plan.md NTF-1).

The opt-in half of the send seam, over the same ``abk init`` + seed-mirror
harness ``test_run_report.py`` uses. What is pinned here is what the flag
PROMISES: nothing is constructed without it, a channel that explodes cannot
change the exit code, a validate-only run refuses it rather than pretending,
and an experiment's ``notify:`` block routes what the flag turned on.

The tests count real ``ChannelFactory`` constructions and real ``send`` calls,
not log lines — an assertion on output would pass against a flag that echoes
and dispatches nothing.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import yaml
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from abkit.notify.base import BaseChannel, ReadoutData
from abkit.notify.factory import ChannelFactory
from tests.e2e.test_first_run import SeedMirrorWarehouse

runner = CliRunner()

EXP = "example_signup_test"


class SpyChannel(BaseChannel):
    """Records construction and delivery; ``mode`` picks the failure to inject."""

    built: list[str] = []
    sent: list[ReadoutData] = []

    def __init__(self, label: str = "spy", mode: str = "ok"):
        self.label = label
        self.mode = mode
        SpyChannel.built.append(label)

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        if self.mode == "raise":
            raise RuntimeError("channel exploded")
        SpyChannel.sent.append(readout)
        return True


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    """`abk init demo` + the seed-mirror warehouse + a spy channel type."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init", "demo"])
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    SpyChannel.built = []
    SpyChannel.sent = []
    monkeypatch.setitem(ChannelFactory.CHANNEL_TYPES, "spy", SpyChannel)
    return warehouse


def configure_channels(**channels) -> None:
    """Write a ``notification_channels:`` block into the scaffold's profiles.yml."""
    from pathlib import Path

    path = Path("profiles.yml")
    profiles = yaml.safe_load(path.read_text(encoding="utf-8"))
    profiles["notification_channels"] = channels
    path.write_text(yaml.safe_dump(profiles, sort_keys=False), encoding="utf-8")


def set_notify_block(block: dict | None) -> None:
    """Add (or drop) the experiment's ``notify:`` routing block."""
    from pathlib import Path

    path = Path("experiments") / f"{EXP}.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if block is None:
        config.pop("notify", None)
    else:
        config["notify"] = block
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    # the edit must actually land — a silently ignored key would make every
    # routing assertion below pass for the wrong reason (the PERF-1 lesson)
    from abkit.config.experiment_config import ExperimentConfig

    reparsed = ExperimentConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if block is None:
        assert reparsed.notify is None
    else:
        assert reparsed.notify is not None
        assert reparsed.notify.model_dump(exclude_defaults=True) == block


class TestOptIn:
    def test_without_the_flag_no_channel_is_ever_constructed(self, scaffolded):
        """The default is truly off — not 'configured but quiet'."""
        configure_channels(team={"type": "spy", "label": "team"})

        result = runner.invoke(cli, ["run", "--select", EXP])

        assert result.exit_code == 0, result.output
        assert SpyChannel.built == []
        assert SpyChannel.sent == []

    def test_with_the_flag_the_readout_is_delivered(self, scaffolded):
        configure_channels(team={"type": "spy", "label": "team"})

        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code == 0, result.output
        assert SpyChannel.built == ["team"]
        assert len(SpyChannel.sent) == 1
        delivered = SpyChannel.sent[0]
        assert delivered.experiment == EXP
        assert delivered.verdict in ("WIN", "LOSE", "FLAT", "INCONCLUSIVE")
        # the numbers are the persisted readout's, so the message carries the
        # same alpha the run echoed
        assert delivered.alpha is not None
        assert "Notify → 1 message(s) sent" in result.output

    def test_explicit_no_notify_is_the_same_as_absent(self, scaffolded):
        configure_channels(team={"type": "spy", "label": "team"})

        result = runner.invoke(cli, ["run", "--select", EXP, "--no-notify"])

        assert result.exit_code == 0, result.output
        assert SpyChannel.built == []

    def test_the_flag_without_configured_channels_says_so(self, scaffolded):
        """Silence would read as a broken flag."""
        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code == 0, result.output
        assert "no notification_channels" in result.output

    def test_validate_only_refuses_the_flag(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", EXP, "--steps", "validate", "--notify"])

        assert result.exit_code != 0
        assert "--notify needs pipeline steps" in result.output


class TestFailSoft:
    def test_a_raising_channel_never_fails_the_run(self, scaffolded):
        configure_channels(bad={"type": "spy", "label": "bad", "mode": "raise"})

        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code == 0, result.output
        # named, and named SAFELY: the failure line carries `describe_error`
        # (the exception class), never the raw message a requests error would
        # fill with the credential-bearing URL
        assert "notify channel 'bad' failed — RuntimeError" in result.output
        assert "channel exploded" not in result.output
        assert "results written" in result.output  # the pipeline itself reported

    def test_a_dispatcher_failure_never_fails_the_run(self, scaffolded, monkeypatch):
        """The OUTER try/except (§0.4 point 1): dispatch's own per-channel catch
        cannot cover a failure raised before any channel is reached."""
        import abkit.notify.dispatch as dispatch_mod

        def boom(*args, **kwargs):
            raise RuntimeError("readout exploded")

        monkeypatch.setattr(dispatch_mod, "load_experiment_readout", boom)
        configure_channels(team={"type": "spy", "label": "team"})

        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code == 0, result.output
        assert "Notify skipped: readout exploded" in result.output

    def test_a_failed_pipeline_sends_an_error_notice_and_still_exits_nonzero(
        self, scaffolded, monkeypatch
    ):
        """NTF-2: the failure IS the signal. No readout exists, so the payload
        carries `kind='error'` and the reason — and notifying about it must not
        rescue the exit code."""
        configure_channels(team={"type": "spy", "label": "team"})
        orig = scaffolded.execute_query

        def explode(query, params=None):
            if "example_ab_assignments" in query:
                raise RuntimeError("warehouse down")
            return orig(query, params)

        monkeypatch.setattr(scaffolded, "execute_query", explode)

        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code != 0
        assert len(SpyChannel.sent) == 1
        notice = SpyChannel.sent[0]
        assert notice.kind == "error"
        assert "warehouse down" in (notice.notice or "")
        # a notice is not a verdict: nothing here may claim a measurement
        assert notice.verdict == ""
        assert (notice.effect, notice.pvalue, notice.alpha) == (None, None, None)


class TestVerdictDedup:
    """NTF-3 through the real CLI: a scheduler is the point of this feature."""

    def test_a_second_run_over_unchanged_data_says_nothing(self, scaffolded):
        configure_channels(team={"type": "spy", "label": "team"})

        first = runner.invoke(cli, ["run", "--select", EXP, "--notify"])
        assert first.exit_code == 0, first.output
        assert len(SpyChannel.sent) == 1

        second = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert second.exit_code == 0, second.output
        assert len(SpyChannel.sent) == 1  # still one — nothing was re-announced
        assert "unchanged" in second.output

    def test_the_state_survives_in_the_warehouse_not_the_process(self, scaffolded):
        """The dedup is only useful if it outlives the CLI invocation — the
        second run is a fresh process reading `_ab_notify_states`."""
        configure_channels(team={"type": "spy", "label": "team"})
        runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        rows = scaffolded.execute_query("SELECT * FROM _ab_notify_states")

        assert rows, "the announcement must be persisted"
        assert rows[0]["notify_count"] == 1
        assert rows[0]["last_verdict"]

    def test_a_failed_run_is_reported_every_time(self, scaffolded, monkeypatch):
        """An error is not a verdict: a run that fails twice failed twice."""
        configure_channels(team={"type": "spy", "label": "team"})
        orig = scaffolded.execute_query

        def explode(query, params=None):
            if "example_ab_assignments" in query:
                raise RuntimeError("warehouse down")
            return orig(query, params)

        monkeypatch.setattr(scaffolded, "execute_query", explode)

        runner.invoke(cli, ["run", "--select", EXP, "--notify"])
        runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert [r.kind for r in SpyChannel.sent] == ["error", "error"]


class TestRouting:
    def test_no_notify_block_reaches_every_configured_channel(self, scaffolded):
        configure_channels(
            a={"type": "spy", "label": "a"},
            b={"type": "spy", "label": "b"},
        )

        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code == 0, result.output
        assert sorted(SpyChannel.built) == ["a", "b"]

    def test_the_experiments_channel_list_narrows_delivery(self, scaffolded):
        configure_channels(
            a={"type": "spy", "label": "a"},
            b={"type": "spy", "label": "b"},
        )
        set_notify_block({"channels": ["b"]})

        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code == 0, result.output
        assert SpyChannel.built == ["b"]

    def test_a_channel_scoped_to_urgent_kinds_skips_a_routine_readout(self, scaffolded):
        configure_channels(oncall={"type": "spy", "label": "oncall", "on": ["srm", "error"]})

        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code == 0, result.output
        assert SpyChannel.sent == []

    def test_the_same_urgent_channel_does_receive_a_failure(self, scaffolded, monkeypatch):
        """The other half of the promise: `on: [srm, error]` is an on-call
        channel, not a muted one."""
        configure_channels(oncall={"type": "spy", "label": "oncall", "on": ["srm", "error"]})
        orig = scaffolded.execute_query

        def explode(query, params=None):
            if "example_ab_assignments" in query:
                raise RuntimeError("warehouse down")
            return orig(query, params)

        monkeypatch.setattr(scaffolded, "execute_query", explode)

        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code != 0
        assert [r.kind for r in SpyChannel.sent] == ["error"]

    def test_mentions_reach_the_payload(self, scaffolded):
        configure_channels(team={"type": "spy", "label": "team"})
        set_notify_block({"mentions": ["growth"]})

        result = runner.invoke(cli, ["run", "--select", EXP, "--notify"])

        assert result.exit_code == 0, result.output
        assert SpyChannel.sent[0].mentions == ["growth"]
