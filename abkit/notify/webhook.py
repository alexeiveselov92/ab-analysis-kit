"""Generic outbound-webhook channel (also the Slack/Mattermost transport).

POSTs a single status-colored attachment as Slack/Mattermost-compatible JSON.
The payload builder (:meth:`build_payload`) is split from the network call so it
is unit-testable and previewable without POSTing. Slack vs Mattermost markdown is
chosen at runtime by the webhook host (``hooks.slack.com``), not by subclass — a
raw ``WebhookChannel`` pointed at a Mattermost URL renders identically to
:class:`~abkit.notify.mattermost.MattermostChannel`.

``requests`` is imported lazily inside :meth:`send` so importing the package (and
the CLI) stays cheap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from abkit.notify.base import BaseChannel, ReadoutData, describe_error, redact_url
from abkit.notify.branding import BRAND_ICON_URL, BRAND_USERNAME


class WebhookChannel(BaseChannel):
    def __init__(
        self,
        webhook_url: str,
        username: str = BRAND_USERNAME,
        icon_url: str | None = None,
        icon_emoji: str | None = None,
        channel: str | None = None,
        timeout: int = 10,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self.webhook_url = webhook_url
        self.username = username
        # Exactly one icon is sent; icon_url wins. Default to the brand avatar only
        # when the caller set neither.
        if icon_url is None and icon_emoji is None:
            icon_url = BRAND_ICON_URL
        self.icon_url = icon_url
        self.icon_emoji = icon_emoji
        self.channel = channel
        self.timeout = timeout
        self.extra_headers = extra_headers or {}

    # ---- platform-aware markdown -------------------------------------------
    def _is_slack(self) -> bool:
        return "hooks.slack.com" in self.webhook_url

    def _bold(self, text: str) -> str:
        return f"*{text}*" if self._is_slack() else f"**{text}**"

    def _link(self, url: str, label: str) -> str:
        return f"<{url}|{label}>" if self._is_slack() else f"[{label}]({url})"

    def format_mentions(self, mentions: list[str]) -> str:
        # Base default (@name); SlackChannel overrides with native syntax.
        return super().format_mentions(mentions)

    # ---- payload ------------------------------------------------------------
    def _rich_body(self, readout: ReadoutData) -> str:
        """The default markdown body, most-important-first."""
        ctx = self.build_context(readout)
        lines = [
            f"{self._bold('Effect')}: {ctx['effect_display']}  ·  "
            f"{ctx['ci_label']} {ctx['ci_display']}",
            f"p = {ctx['pvalue_display']}  ·  α = {ctx['alpha_display']}  ·  "
            f"{readout.name_1} vs {readout.name_2}",
        ]
        if ctx["samples_display"]:
            lines.append(ctx["samples_display"])
        if ctx["srm_display"]:
            lines.append(self._bold(ctx["srm_display"]))
        if ctx["weekly_cycle_display"]:
            lines.append(ctx["weekly_cycle_display"])
        link_parts = []
        if readout.dashboard_url:
            link_parts.append(self._link(readout.dashboard_url, "Open report"))
        for label, url in readout.links.items():
            link_parts.append(self._link(url, label))
        if readout.help_url:
            link_parts.append(self._link(readout.help_url, ctx["help_label"]))
        if link_parts:
            lines.append(" · ".join(link_parts))
        return "\n".join(lines)

    def build_payload(self, readout: ReadoutData, template: str | None = None) -> dict[str, Any]:
        ctx = self.build_context(readout)
        title = self.format_title(readout)
        attachment: dict[str, Any] = {
            "color": self.verdict_color(readout),
            "title": title,
            "mrkdwn_in": ["text"],
        }
        if template is not None:
            attachment["text"] = self.format_message(readout, template)
        else:
            attachment["text"] = self._rich_body(readout)
            # Plain one-liner preview for clients that fold the attachment.
            attachment["fallback"] = self.format_message(readout)
        if readout.dashboard_url:
            attachment["title_link"] = readout.dashboard_url
        footer = self.username or BRAND_USERNAME
        if readout.project_name:
            footer = f"{footer} · {readout.project_name}"
        attachment["footer"] = footer
        if self.icon_url:
            attachment["footer_icon"] = self.icon_url
        ts = _unix_ts(readout.timestamp)
        if ts is not None:
            attachment["ts"] = ts

        payload: dict[str, Any] = {"username": self.username, "attachments": [attachment]}
        # Mentions ride in the TOP-LEVEL text — in-attachment mentions render but
        # do not reliably notify on Slack.
        mentions = ctx["mentions"]
        if mentions:
            payload["text"] = mentions
        if self.icon_url:
            payload["icon_url"] = self.icon_url
        elif self.icon_emoji:
            payload["icon_emoji"] = self.icon_emoji
        if self.channel:
            payload["channel"] = self.channel
        return payload

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        import requests

        payload = self.build_payload(readout, template)
        headers = {"Content-Type": "application/json", **self.extra_headers}
        try:
            resp = requests.post(
                self.webhook_url, json=payload, headers=headers, timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            # Redact: the webhook URL path is the secret and requests embeds the
            # full URL in the exception string — never print the raw exc.
            print(
                f"Failed to send webhook notification to "
                f"{redact_url(self.webhook_url)}: {describe_error(exc)}"
            )
            return False
        return True

    def __repr__(self) -> str:
        shown = self.webhook_url[:30] + ("..." if len(self.webhook_url) > 30 else "")
        tail = f", channel={self.channel}" if self.channel else ""
        return f"{self.__class__.__name__}(webhook_url={shown}{tail})"


def _unix_ts(value: datetime | None) -> int | None:
    """Epoch seconds from a naive-UTC datetime (Slack-only ``ts`` field)."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())
