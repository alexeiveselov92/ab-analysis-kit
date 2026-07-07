"""``abk test-report`` — the WP5 CLI surface (m6-implementation-plan.md WP5).

Runs over an `abk init` scaffold with a `notification_channels:` block added to
profiles.yml. The mock readout is synthetic (no lock, no warehouse read), so
these tests never touch a DB — a webhook channel is intercepted with
``requests_mock``. Asserts the ✓/✗ report, the non-zero exit on any failure, the
--channel filter, and the missing-config / unknown-experiment error paths.
"""

from __future__ import annotations

import textwrap

import pytest
import requests_mock
from click.testing import CliRunner

from abkit.cli.main import cli

runner = CliRunner()
EXP = "example_signup_test"
WH = "https://webhook.test/team"
WH2 = "https://webhook.test/ops"

PROFILES = textwrap.dedent(
    f"""\
    default_profile: dev
    profiles:
      dev:
        type: clickhouse
        host: localhost
        port: 9000
    notification_channels:
      team:
        type: webhook
        webhook_url: "{WH}"
      ops:
        type: webhook
        webhook_url: "{WH2}"
    """
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A scaffolded demo whose profiles.yml carries two webhook channels."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    proj = tmp_path / "demo"
    (proj / "profiles.yml").write_text(PROFILES)
    monkeypatch.chdir(proj)
    return proj


def test_all_channels_succeed(project):
    with requests_mock.Mocker() as m:
        m.post(WH, status_code=200)
        m.post(WH2, status_code=200)
        result = runner.invoke(cli, ["test-report", EXP])
    assert result.exit_code == 0, result.output
    assert "2/2 channel(s)" in result.output
    assert result.output.count("✓") == 2
    assert "✗" not in result.output


def test_channel_failure_exits_nonzero(project):
    with requests_mock.Mocker() as m:
        m.post(WH, status_code=200)
        m.post(WH2, status_code=500)  # one channel down
        result = runner.invoke(cli, ["test-report", EXP])
    assert result.exit_code == 1
    assert "✓ team" in result.output
    assert "✗ ops" in result.output
    assert "1/2" in result.output


def test_channel_filter(project):
    with requests_mock.Mocker() as m:
        m.post(WH, status_code=200)
        result = runner.invoke(cli, ["test-report", EXP, "--channel", "team"])
    assert result.exit_code == 0, result.output
    assert "1/1 channel(s)" in result.output
    assert "team" in result.output and "ops" not in result.output


def test_unknown_channel_is_bad_parameter(project):
    result = runner.invoke(cli, ["test-report", EXP, "--channel", "nope"])
    assert result.exit_code == 2  # click.BadParameter
    assert "unknown channel" in result.output.lower()


def test_unknown_experiment_exits_nonzero(project):
    result = runner.invoke(cli, ["test-report", "does_not_exist"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_no_channels_configured_errors(tmp_path, monkeypatch):
    # the pristine scaffold has notification_channels COMMENTED out
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    monkeypatch.chdir(tmp_path / "demo")
    result = runner.invoke(cli, ["test-report", EXP])
    assert result.exit_code == 1
    assert "No notification_channels" in result.output


def test_mock_readout_is_synthetic_no_db(project):
    # No warehouse is reachable (localhost:9000 is not mocked); the command must
    # still succeed because the mock is synthetic — proof it never connects.
    with requests_mock.Mocker() as m:
        m.post(WH, status_code=200)
        m.post(WH2, status_code=200)
        result = runner.invoke(cli, ["test-report", EXP])
    assert result.exit_code == 0, result.output
    assert "mock WIN readout" in result.output
