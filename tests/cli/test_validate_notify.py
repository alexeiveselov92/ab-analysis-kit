"""``abk validate --notify`` — the NTF-5 CLI surface (m12 NTF-5).

The calibration half of the recurring signals: a cell whose measured A/A
false-positive rate exceeds its budget is the one finding in the matrix that
says "do not decide on this", and it is the finding an operator running
validate on a schedule will not be reading the terminal for.

The harness is ``test_validate_command.py``'s (an ``abk init`` scaffold over
the seed mirror), plus ``test_run_notify.py``'s spy channel: the assertions
count real ``send`` calls, never log lines.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

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
    sent: list[ReadoutData] = []

    def __init__(self, label: str = "spy"):
        self.label = label

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        SpyChannel.sent.append(readout)
        return True


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
    SpyChannel.sent = []
    monkeypatch.setitem(ChannelFactory.CHANNEL_TYPES, "spy", SpyChannel)
    path = Path("profiles.yml")
    profiles = yaml.safe_load(path.read_text(encoding="utf-8"))
    profiles["notification_channels"] = {"team": {"type": "spy", "label": "team"}}
    path.write_text(yaml.safe_dump(profiles, sort_keys=False), encoding="utf-8")
    return warehouse


def force_budget(monkeypatch, budget: float) -> None:
    """Pin every cell's FPR budget, so redness is the test's decision.

    The A/A matrix over the scaffold's seed data is well-calibrated by design;
    a budget of 0.0 makes every measurable cell red and 1.0 makes none, which
    is the only way to exercise both branches without asserting on numbers the
    engine is free to improve.
    """
    import abkit.tuning.recompute as recompute_mod

    monkeypatch.setattr(recompute_mod, "resolve_fpr_budget", lambda *a, **k: budget)


def validate(*args: str):
    return runner.invoke(cli, ["validate", "--select", EXP, "--iterations", "60", *args])


def test_a_red_cell_is_announced(scaffolded, monkeypatch):
    force_budget(monkeypatch, 0.0)

    result = validate("--notify")

    assert result.exit_code == 0, result.output
    assert len(SpyChannel.sent) == 1
    payload = SpyChannel.sent[0]
    assert payload.kind == "calibration_red"
    assert "false-positive budget" in payload.notice
    assert payload.experiment == EXP


def test_a_calibrated_matrix_says_nothing(scaffolded, monkeypatch):
    force_budget(monkeypatch, 1.0)

    result = validate("--notify")

    assert result.exit_code == 0, result.output
    assert SpyChannel.sent == []


def test_without_the_flag_nothing_is_sent(scaffolded, monkeypatch):
    force_budget(monkeypatch, 0.0)

    result = validate()

    assert result.exit_code == 0, result.output
    assert SpyChannel.sent == []


def test_the_same_red_cell_is_not_re_announced(scaffolded, monkeypatch):
    """A nightly `abk validate --notify` must not be a nightly message."""
    force_budget(monkeypatch, 0.0)

    assert validate("--notify").exit_code == 0
    second = validate("--notify")

    assert second.exit_code == 0, second.output
    assert len(SpyChannel.sent) == 1


def test_a_notify_failure_never_fails_the_validation(scaffolded, monkeypatch):
    """`--report`'s precedent: a side channel may not turn a successful
    validation into a failed one, and validate exits non-zero on failure."""
    force_budget(monkeypatch, 0.0)

    def explode(**kwargs):
        raise RuntimeError("dispatch is broken")

    import abkit.notify.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "dispatch_calibration_red", explode)

    result = validate("--notify")

    assert result.exit_code == 0, result.output
    assert "Notify skipped" in result.output
    # the validation itself still persisted its rows
    from abkit.database.internal_tables import InternalTablesManager

    assert InternalTablesManager(scaffolded).get_aa_runs(EXP)


def test_the_flag_without_configured_channels_says_so(scaffolded, monkeypatch):
    """Silence here is indistinguishable from a broken flag (the D1 rule)."""
    path = Path("profiles.yml")
    profiles = yaml.safe_load(path.read_text(encoding="utf-8"))
    profiles.pop("notification_channels")
    path.write_text(yaml.safe_dump(profiles, sort_keys=False), encoding="utf-8")
    force_budget(monkeypatch, 0.0)

    result = validate("--notify")

    assert result.exit_code == 0, result.output
    assert "no notification_channels in profiles.yml" in result.output
