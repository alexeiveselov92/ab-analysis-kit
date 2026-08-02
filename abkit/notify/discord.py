"""Discord incoming-webhook channel (m12 NTF-4).

One **embed** per readout, posted to an "Execute Webhook" URL
(``https://discord.com/api/webhooks/<id>/<token>``). The wire format and its
limits are Discord platform facts, taken from the detectkit donor verbatim; the
*content* is abkit's own — one readout, no severity, no anomaly/recovery kinds.

Two Discord quirks the payload is shaped around:

* **The embed colour is a DECIMAL int**, not the ``#RRGGBB`` string every other
  channel takes — ``int(hex, 16)``.
* **A mention inside an embed never pings.** It has to ride in the top-level
  ``content`` field, with an ``allowed_mentions`` object, or Discord renders the
  text and notifies nobody.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from abkit.notify.base import BaseChannel, ReadoutData, describe_error, redact_url
from abkit.notify.branding import BRAND_ICON_URL, BRAND_USERNAME

# https://discord.com/developers/docs/resources/message#embed-object — enforced
# defensively so a long metric name or description can never trip a 400.
_TITLE_CAP = 256
_DESCRIPTION_CAP = 4096
_FOOTER_CAP = 2048
_CONTENT_CAP = 2000
#: Discord also caps the SUM of title + description + footer across the embed.
_EMBED_TOTAL_CAP = 6000


class DiscordChannel(BaseChannel):
    """Discord channel using an incoming webhook.

    Args:
        webhook_url: ``https://discord.com/api/webhooks/<id>/<token>``.
        username: bot name override (default: the abkit brand name).
        avatar_url: bot avatar override.
        timeout: request timeout in seconds.
    """

    def __init__(
        self,
        webhook_url: str,
        username: str = BRAND_USERNAME,
        avatar_url: str | None = None,
        timeout: int = 10,
    ) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required for DiscordChannel")
        self.webhook_url = webhook_url
        self.username = username
        self.avatar_url = avatar_url or BRAND_ICON_URL
        self.timeout = timeout

    # ---- payload -------------------------------------------------------------
    def build_payload(self, readout: ReadoutData, template: str | None = None) -> dict[str, Any]:
        ctx = self.build_context(readout)
        title = _cap(self.format_title(readout), _TITLE_CAP)
        footer_text = _cap(_footer_text(ctx), _FOOTER_CAP)
        # The body is the shared message — so a notice (m12 NTF-2) renders as a
        # notice here too, with no statistics block, without this file knowing
        # anything about kinds. Mentions are stripped from it: they ride in the
        # top-level `content` (below), and leaving them in the body would print
        # every handle twice.
        description = self.format_message(replace(readout, mentions=[]), template)
        budget = min(_DESCRIPTION_CAP, _EMBED_TOTAL_CAP - len(title) - len(footer_text))
        description = _cap(description, max(budget, 64))

        embed: dict[str, Any] = {
            "color": int(self.verdict_color(readout).lstrip("#"), 16),
            "title": title,
            "description": description,
            "footer": {"text": footer_text},
        }
        if readout.dashboard_url:
            embed["url"] = readout.dashboard_url
        stamp = _iso_utc(readout.timestamp)
        if stamp is not None:
            embed["timestamp"] = stamp

        payload: dict[str, Any] = {
            "username": self.username,
            "avatar_url": self.avatar_url,
            "embeds": [embed],
        }
        mentions = ctx["mentions"]
        if mentions:
            payload["content"] = _cap(mentions, _CONTENT_CAP)
            payload["allowed_mentions"] = {"parse": ["everyone", "users", "roles"]}
        return payload

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        import requests

        try:
            resp = requests.post(
                self.webhook_url, json=self.build_payload(readout, template), timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            # the webhook token is in the URL PATH and requests embeds the URL
            # in its exception string — redact both halves
            print(
                f"Failed to send Discord notification to "
                f"{redact_url(self.webhook_url)}: {describe_error(exc)}"
            )
            return False
        return True

    def __repr__(self) -> str:
        return f"DiscordChannel(webhook_url={redact_url(self.webhook_url)})"


def _footer_text(ctx: dict[str, Any]) -> str:
    project = ctx["project_name"]
    return f"{BRAND_USERNAME} · {project}" if project else BRAND_USERNAME


def _cap(value: str, limit: int) -> str:
    """Truncate with an ellipsis; the caps are Discord's, not a style choice."""
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)] + "…"


def _iso_utc(value: datetime | None) -> str | None:
    """ISO-8601 UTC, the only timestamp form Discord accepts on an embed."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
