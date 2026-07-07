"""Slack incoming-webhook channel — a thin :class:`WebhookChannel` wrapper.

Slack and Mattermost share a compatible webhook payload, so the parent does all
the work. This class only fixes the Slack-relevant constructor (no auth headers)
and overrides mention formatting to Slack's native syntax.
"""

from __future__ import annotations

from abkit.notify.branding import BRAND_USERNAME
from abkit.notify.webhook import WebhookChannel


class SlackChannel(WebhookChannel):
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

    def format_mentions(self, mentions: list[str]) -> str:
        """Slack-native mentions: broadcast keywords → ``<!keyword>``, user ids →
        ``<@U...>``, anything else → a display-only ``@name``."""
        if not mentions:
            return ""
        out: list[str] = []
        for m in mentions:
            low = m.lower()
            if low in ("channel", "here", "everyone"):
                out.append(f"<!{low}>")
            elif low == "all":
                out.append("<!everyone>")
            elif m.startswith("U") and len(m) >= 9 and m[1:].isalnum():
                out.append(f"<@{m}>")
            else:
                out.append(f"@{m}")
        return " ".join(out)
