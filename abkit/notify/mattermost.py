"""Mattermost incoming-webhook channel — a thin :class:`WebhookChannel` wrapper.

Mattermost incoming webhooks are Slack-API-compatible, so this class only names
the channel for config/docs and forwards its constructor. ``WebhookChannel``
auto-detects the platform by host (``hooks.slack.com`` → Slack markdown, else the
CommonMark ``**bold**`` / ``[label](url)`` Mattermost accepts), so no rendering
override is needed. Auth headers are deliberately not surfaced (mirroring Slack);
a self-hosted instance behind auth can use :class:`WebhookChannel` directly with
``extra_headers``.
"""

from __future__ import annotations

from abkit.notify.branding import BRAND_USERNAME
from abkit.notify.webhook import WebhookChannel


class MattermostChannel(WebhookChannel):
    def __init__(
        self,
        webhook_url: str,
        username: str = BRAND_USERNAME,
        icon_url: str | None = None,
        icon_emoji: str | None = None,
        channel: str | None = None,
        timeout: int = 10,
    ) -> None:
        super().__init__(
            webhook_url,
            username=username,
            icon_url=icon_url,
            icon_emoji=icon_emoji,
            channel=channel,
            timeout=timeout,
        )
