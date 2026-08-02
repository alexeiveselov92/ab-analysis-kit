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

PROFILES = textwrap.dedent(f"""\
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
    """)


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


# ── NTF-4: the smoke test must cover all nine channel types ───────────────────
NINE_URLS = {
    "discord": "https://discord.com/api/webhooks/1/tok",
    "teams": "https://prod-1.westus.logic.azure.com/workflows/a/triggers/x/paths/invoke",
    "googlechat": "https://chat.googleapis.com/v1/spaces/A/messages?key=k&token=t",
    "ntfy": "https://ntfy.sh",
}

PROFILES_NINE = textwrap.dedent(f"""\
    default_profile: dev
    profiles:
      dev:
        type: clickhouse
        host: localhost
        port: 9000
    notification_channels:
      c_webhook:
        type: webhook
        webhook_url: "{WH}"
      c_slack:
        type: slack
        webhook_url: "{WH}"
      c_mattermost:
        type: mattermost
        webhook_url: "{WH}"
      c_telegram:
        type: telegram
        bot_token: "1:abc"
        chat_id: "-100"
      c_email:
        type: email
        smtp_host: smtp.example.com
        smtp_port: 587
        from_email: bot@x
        to_emails: [a@x]
      c_discord:
        type: discord
        webhook_url: "{NINE_URLS['discord']}"
      c_teams:
        type: teams
        webhook_url: "{NINE_URLS['teams']}"
      c_googlechat:
        type: googlechat
        webhook_url: "{NINE_URLS['googlechat']}"
      c_ntfy:
        type: ntfy
        topic: abkit-smoke
    """)


def test_every_channel_type_sends_the_mock_readout(tmp_path, monkeypatch):
    """`abk test-report` is the connectivity check operators run after wiring a
    channel — a type it cannot construct or send through is a type that does not
    really ship."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", "demo"]).exit_code == 0
    proj = tmp_path / "demo"
    (proj / "profiles.yml").write_text(PROFILES_NINE)
    monkeypatch.chdir(proj)

    import abkit.notify.email as email_mod

    class _FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def sendmail(self, *a):
            pass

        def quit(self):
            pass

    monkeypatch.setattr(email_mod.smtplib, "SMTP", _FakeSMTP)

    with requests_mock.Mocker() as m:
        m.post(WH, status_code=200)
        m.post("https://api.telegram.org/bot1:abc/sendMessage", json={"ok": True})
        for url in NINE_URLS.values():
            m.post(url, status_code=200)
        result = runner.invoke(cli, ["test-report", EXP])

    assert result.exit_code == 0, result.output
    assert "9/9 channel(s)" in result.output
    assert "✗" not in result.output
