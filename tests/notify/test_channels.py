"""Unit tests for the ``abk test-report`` notification channels (m6 WP5).

Covers the factory (env-interpolation reuse, unresolved-secret rejection, unknown
type, param validation), the payload/message formatting (verdict presentation,
null discipline, Slack vs Mattermost markdown, Telegram HTML escaping + length
guard, the email multipart), and the per-channel send() bool contract via
``requests_mock`` / a fake SMTP — no network. Also asserts NO alerting semantics
leaked in (no severity/recovery/no-data/detector vocabulary).
"""

from __future__ import annotations

import math

import pytest
import requests
import requests_mock

from abkit.notify import (
    ChannelFactory,
    EmailChannel,
    MattermostChannel,
    ReadoutData,
    SlackChannel,
    TelegramChannel,
    WebhookChannel,
    create_mock_readout,
)

SLACK_URL = "https://hooks.slack.com/services/T/B/xxx"
MM_URL = "https://mm.example.com/hooks/abc"


def _readout(**overrides) -> ReadoutData:
    base = create_mock_readout("signup_test", "signup_cr", "control", "treatment", alpha=0.05)
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ── verdict presentation ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "verdict,color",
    [
        ("WIN", "#1E9E6A"),
        ("LOSE", "#D6453D"),
        ("FLAT", "#7A8595"),
        ("INCONCLUSIVE", "#E0A23B"),
    ],
)
def test_verdict_colors_are_the_brand_tokens(verdict, color):
    ch = WebhookChannel("http://x")
    r = _readout(verdict=verdict)
    assert ch.verdict_color(r) == color
    assert ch.verdict_kind(r) == verdict


def test_srm_overrides_verdict():
    ch = WebhookChannel("http://x")
    r = _readout(verdict="WIN", srm_flag=True, srm_pvalue=1e-6)
    assert ch.verdict_kind(r) == "SRM"
    assert ch.verdict_color(r) == "#B23A6B"
    ctx = ch.build_context(r)
    assert "SRM gate FAILED" in ctx["srm_display"]
    assert "results withheld" in ctx["srm_display"]


def test_unknown_verdict_falls_back_to_flat():
    ch = WebhookChannel("http://x")
    assert ch.verdict_kind(_readout(verdict="banana")) == "FLAT"


# ── null discipline / formatting ────────────────────────────────────────────────
def test_none_and_naninf_render_as_na():
    ch = WebhookChannel("http://x")
    r = _readout(effect=None, left_bound=math.nan, right_bound=math.inf, pvalue=None, alpha=None)
    ctx = ch.build_context(r)
    assert ctx["effect_display"] == "N/A"
    assert ctx["ci_display"] == "N/A"
    assert ctx["pvalue_display"] == "N/A"
    assert ctx["alpha_display"] == "N/A"
    assert ctx["ci_label"] == "CI"  # no confidence level without alpha


def test_relative_effect_has_sign_and_percent():
    ch = WebhookChannel("http://x")
    ctx = ch.build_context(_readout(effect=0.0432, left_bound=0.0118, right_bound=0.0741))
    assert ctx["effect_display"] == "+4.32%"
    assert ctx["ci_display"] == "[1.18%, 7.41%]"
    assert ctx["ci_label"] == "95% CI"


def test_absolute_effect_no_percent():
    ch = WebhookChannel("http://x")
    ctx = ch.build_context(_readout(relative=False, effect=1.5, left_bound=0.3, right_bound=2.7))
    assert ctx["effect_display"] == "+1.5"
    assert ctx["ci_display"] == "[0.3, 2.7]"


def test_weekly_cycle_line_only_when_present():
    ch = WebhookChannel("http://x")
    assert ch.build_context(_readout(weekly_cycle_pct=None))["weekly_cycle_line"] == ""
    line = ch.build_context(_readout(weekly_cycle_pct=42.0))["weekly_cycle_line"]
    assert "42%" in line and "weekly" in line


# ── webhook payload / Slack vs Mattermost ───────────────────────────────────────
def test_slack_payload_shape():
    r = _readout(dashboard_url="https://abkit.pipelab.dev/r.html", mentions=["here"])
    payload = SlackChannel(SLACK_URL, channel="#exp").build_payload(r)
    assert payload["username"] == "abkit"
    assert payload["channel"] == "#exp"
    assert payload["text"] == "<!here>"  # mentions in TOP-LEVEL text
    att = payload["attachments"][0]
    assert att["color"] == "#1E9E6A"
    assert att["title_link"] == "https://abkit.pipelab.dev/r.html"
    assert att["mrkdwn_in"] == ["text"]
    assert att["footer"].startswith("abkit")
    assert "*Effect*" in att["text"]  # slack bold
    assert att["fallback"]  # plain preview present


def test_slack_vs_mattermost_markdown_differs():
    r = _readout(dashboard_url="https://x/r.html")
    slack_text = SlackChannel(SLACK_URL).build_payload(r)["attachments"][0]["text"]
    mm_text = MattermostChannel(MM_URL).build_payload(r)["attachments"][0]["text"]
    assert "*Effect*:" in slack_text and "<https://x/r.html|Open report>" in slack_text
    assert "**Effect**:" in mm_text and "[Open report](https://x/r.html)" in mm_text


def test_slack_mention_syntax():
    ch = SlackChannel(SLACK_URL)
    assert ch.format_mentions(["channel"]) == "<!channel>"
    assert ch.format_mentions(["all"]) == "<!everyone>"
    assert ch.format_mentions(["U012ABCDEF"]) == "<@U012ABCDEF>"
    assert ch.format_mentions(["alice"]) == "@alice"


def test_icon_precedence_default_brand_avatar():
    # neither icon → brand PNG fallback
    assert WebhookChannel("http://x").icon_url.endswith("/bot-icon.png")
    # emoji only → no icon_url
    ch = WebhookChannel("http://x", icon_emoji=":robot:")
    p = ch.build_payload(_readout())
    assert p.get("icon_emoji") == ":robot:" and "icon_url" not in p


# ── webhook send() bool contract ────────────────────────────────────────────────
def test_webhook_send_success_and_failure():
    ch = WebhookChannel("https://webhook.example.com/x")
    with requests_mock.Mocker() as m:
        m.post("https://webhook.example.com/x", status_code=200)
        assert ch.send(_readout()) is True
    with requests_mock.Mocker() as m:
        m.post("https://webhook.example.com/x", status_code=500)
        assert ch.send(_readout()) is False  # never raises
    with requests_mock.Mocker() as m:
        m.post("https://webhook.example.com/x", exc=requests.exceptions.ConnectionError("boom"))
        assert ch.send(_readout()) is False


def test_webhook_extra_headers_sent():
    ch = WebhookChannel("https://w.example.com/x", extra_headers={"Authorization": "Bearer T"})
    with requests_mock.Mocker() as m:
        m.post("https://w.example.com/x", status_code=200)
        assert ch.send(_readout()) is True
        assert m.last_request.headers["Authorization"] == "Bearer T"


# ── telegram ─────────────────────────────────────────────────────────────────────
def test_telegram_html_escapes_untrusted_values():
    ch = TelegramChannel("123:abc", "-100")
    msg = ch._build_html_message(_readout(description="<script>alert(1)</script> & <b>x</b>"))
    assert "<script>" not in msg
    assert "&lt;script&gt;" in msg and "&amp;" in msg


def test_telegram_length_guard():
    ch = TelegramChannel("123:abc", "-100")
    msg = ch._build_html_message(_readout(description="x" * 6000))
    assert len(msg) <= 4096


def test_telegram_send_payload_and_bool():
    ch = TelegramChannel("123:abc", "-100999", disable_notification=True)
    url = "https://api.telegram.org/bot123:abc/sendMessage"
    with requests_mock.Mocker() as m:
        m.post(url, status_code=200, json={"ok": True})
        assert ch.send(_readout()) is True
        body = m.last_request.json()
        assert body["chat_id"] == "-100999"
        assert body["parse_mode"] == "HTML"
        assert body["disable_web_page_preview"] is True
        assert body["disable_notification"] is True
    with requests_mock.Mocker() as m:
        m.post(url, status_code=400, json={"ok": False})
        assert ch.send(_readout()) is False


def test_telegram_repr_hides_token():
    assert "abc" not in repr(TelegramChannel("123:abc", "-100"))


# ── email ────────────────────────────────────────────────────────────────────────
class _FakeSMTP:
    instances: list[_FakeSMTP] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in = None
        self.sent = None
        _FakeSMTP.instances.append(self)

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def sendmail(self, frm, to, msg):
        self.sent = (frm, to, msg)

    def quit(self):
        pass


def test_email_starttls_multipart_and_header_guard(monkeypatch):
    _FakeSMTP.instances.clear()
    import abkit.notify.email as email_mod

    monkeypatch.setattr(email_mod.smtplib, "SMTP", _FakeSMTP)
    ch = EmailChannel(
        "smtp.example.com",
        587,
        "bot@x",
        ["a@x", "b@x"],
        smtp_username="u",
        smtp_password="p",
        use_tls=True,
    )
    # a CR/LF in the experiment must not inject a header
    r = _readout(experiment="exp\r\nBcc: evil@x")
    assert ch.send(r) is True
    smtp = _FakeSMTP.instances[-1]
    assert smtp.started_tls is True
    assert smtp.logged_in == ("u", "p")
    frm, to, raw = smtp.sent
    assert to == ["a@x", "b@x"]
    assert "multipart/alternative" in raw
    assert "text/plain" in raw and "text/html" in raw
    # the Subject header line has no injected CR/LF break
    subject_lines = [ln for ln in raw.splitlines() if ln.startswith("Subject:")]
    assert subject_lines and "Bcc:" not in subject_lines[0]


def test_email_ssl_branch_and_failure(monkeypatch):
    import abkit.notify.email as email_mod

    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", _FakeSMTP)
    ch = EmailChannel("smtp.example.com", 465, "bot@x", "a@x", use_tls=False)
    assert ch.send(_readout()) is True  # SMTP_SSL path

    def _boom(*a, **k):
        raise email_mod.smtplib.SMTPException("nope")

    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", _boom)
    assert ch.send(_readout()) is False


def test_email_requires_recipients():
    with pytest.raises(ValueError):
        EmailChannel("smtp", 587, "a@x", [])


# ── factory ───────────────────────────────────────────────────────────────────────
def test_factory_creates_each_type():
    got = {
        "slack": ChannelFactory.create_from_config({"type": "slack", "webhook_url": SLACK_URL}),
        "mattermost": ChannelFactory.create_from_config(
            {"type": "mattermost", "webhook_url": MM_URL}
        ),
        "webhook": ChannelFactory.create_from_config(
            {"type": "webhook", "webhook_url": "http://x"}
        ),
        "telegram": ChannelFactory.create_from_config(
            {"type": "telegram", "bot_token": "1:2", "chat_id": "3"}
        ),
        "email": ChannelFactory.create_from_config(
            {
                "type": "email",
                "smtp_host": "h",
                "smtp_port": 587,
                "from_email": "a@x",
                "to_emails": ["b@x"],
            }
        ),
    }
    assert isinstance(got["slack"], SlackChannel)
    assert isinstance(got["telegram"], TelegramChannel)
    assert ChannelFactory.list_available_types() == [
        "email",
        "mattermost",
        "slack",
        "telegram",
        "webhook",
    ]


def test_factory_type_is_case_insensitive():
    assert isinstance(
        ChannelFactory.create_from_config({"type": "SLACK", "webhook_url": SLACK_URL}), SlackChannel
    )


def test_factory_unknown_type():
    with pytest.raises(ValueError, match="Unknown channel type"):
        ChannelFactory.create_from_config({"type": "pagerduty"})


def test_factory_missing_type():
    with pytest.raises(ValueError, match="must have a 'type'"):
        ChannelFactory.create_from_config({"webhook_url": "http://x"})


def test_factory_bad_params_reraised_as_valueerror():
    with pytest.raises(ValueError, match="Invalid parameters for telegram"):
        ChannelFactory.create_from_config({"type": "telegram", "bot_token": "1:2"})  # no chat_id


def test_factory_resolves_env_and_rejects_unresolved(monkeypatch):
    monkeypatch.setenv("ABK_TEST_WH", "https://hooks.slack.com/services/REAL")
    ch = ChannelFactory.create_from_config({"type": "slack", "webhook_url": "${ABK_TEST_WH}"})
    assert ch.webhook_url == "https://hooks.slack.com/services/REAL"
    with pytest.raises(ValueError, match="unset environment variable"):
        ChannelFactory.create_from_config({"type": "slack", "webhook_url": "${ABK_MISSING}"})


def test_factory_unresolved_error_does_not_echo_the_value(monkeypatch):
    # the raised error must NOT contain the (would-be secret) param value
    with pytest.raises(ValueError) as ei:
        ChannelFactory.create_from_config(
            {
                "type": "email",
                "smtp_host": "h",
                "smtp_port": 587,
                "from_email": "a@x",
                "to_emails": ["b@x"],
                "smtp_password": "${ABK_SECRET_UNSET}",
            }
        )
    assert "ABK_SECRET_UNSET" not in str(ei.value)
    assert "smtp_password" in str(ei.value)


def test_factory_does_not_false_reject_a_normal_resolved_url():
    # a real resolved value with no full placeholder must pass (regex, not substring)
    ch = ChannelFactory.create_from_config(
        {"type": "webhook", "webhook_url": "https://example.com/hook?x=1"}
    )
    assert isinstance(ch, WebhookChannel)


def test_telegram_and_email_reject_unknown_params():
    # a typo'd optional param must raise loudly (parity with the strict webhook family)
    with pytest.raises(ValueError, match="Invalid parameters for telegram"):
        ChannelFactory.create_from_config(
            {"type": "telegram", "bot_token": "1:2", "chat_id": "3", "disable_notifications": True}
        )
    with pytest.raises(ValueError, match="Invalid parameters for email"):
        ChannelFactory.create_from_config(
            {
                "type": "email",
                "smtp_host": "h",
                "smtp_port": 587,
                "from_email": "a@x",
                "to_emails": ["b@x"],
                "use_tsl": False,
            }
        )


# ── secret redaction on failure ───────────────────────────────────────────────────
def test_webhook_failure_does_not_leak_the_secret_url(capsys):
    secret = "https://hooks.slack.com/services/T01/B02/aBcDeFsecretTOKEN"
    ch = SlackChannel(secret)
    with requests_mock.Mocker() as m:
        m.post(secret, status_code=404)
        assert ch.send(_readout()) is False
    out = capsys.readouterr().out
    assert "aBcDeFsecretTOKEN" not in out  # the secret path token
    assert "/services/" not in out
    assert "hooks.slack.com" in out  # host is fine to show


def test_telegram_failure_does_not_leak_the_bot_token(capsys):
    ch = TelegramChannel("123456:AAsecretBotToken", "-100")
    url = "https://api.telegram.org/bot123456:AAsecretBotToken/sendMessage"
    with requests_mock.Mocker() as m:
        m.post(url, status_code=401)
        assert ch.send(_readout()) is False
    out = capsys.readouterr().out
    assert "AAsecretBotToken" not in out
    assert "api.telegram.org" in out


# ── telegram escaping / template-plain path ───────────────────────────────────────
def test_telegram_html_mentions_are_escaped():
    ch = TelegramChannel("1:2", "-100")
    msg = ch._build_html_message(_readout(mentions=["dev&ops", "a<b"]))
    assert "@dev&amp;ops" in msg and "@a&lt;b" in msg
    assert "&ops" not in msg.replace("&amp;ops", "")  # no raw '&'


def test_telegram_custom_template_sent_as_plain_text_no_parse_mode():
    # a template with '<'/'&' in a value must NOT be sent under parse_mode=HTML
    ch = TelegramChannel("1:2", "-100", template="R: {experiment} / {metric}")
    url = "https://api.telegram.org/bot1:2/sendMessage"
    with requests_mock.Mocker() as m:
        m.post(url, status_code=200, json={"ok": True})
        assert ch.send(_readout(experiment="a<b & c")) is True
        body = m.last_request.json()
        assert "parse_mode" not in body  # plain — no HTML parsing
        assert body["text"] == "R: a<b & c / signup_cr"  # unescaped, but plain is safe


# ── no leaked alerting semantics ──────────────────────────────────────────────────
def test_no_alerting_vocabulary_in_default_message():
    msg = WebhookChannel("http://x").format_message(_readout()).lower()
    for banned in (
        "severity",
        "detector",
        "anomaly",
        "recovery",
        "quorum",
        "consecutive",
        "no data",
    ):
        assert banned not in msg
