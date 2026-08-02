"""Google Chat channel — Cards v2 over an incoming webhook (m12 NTF-4).

The wire format is a platform fact from the detectkit donor; the content is
abkit's own readout.

The quirk that shapes every string here: **Cards v2 does not honour ``\\n``.**
Text widgets render a limited HTML subset, so a line break has to be an explicit
``<br>`` tag — a body assembled with newlines arrives as one run-on paragraph.
Everything user-supplied is HTML-escaped first, then newlines are converted, so
a metric named ``a<b`` cannot inject markup.
"""

from __future__ import annotations

import html
from typing import Any

from abkit.notify.base import BaseChannel, ReadoutData, describe_error, redact_url
from abkit.notify.branding import BRAND_USERNAME

#: Google Chat's "notify everyone in the space" token, and the words an operator
#: is likely to write for it.
_ALL_MENTION = "<users/all>"
_ALL_KEYWORDS = frozenset({"all", "everyone", "channel", "here"})


class GoogleChatChannel(BaseChannel):
    """Google Chat channel posting a Cards v2 message.

    Args:
        webhook_url: the space's incoming-webhook URL (carries ``key`` and
            ``token`` query params — the credential).
        timeout: request timeout in seconds.
    """

    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required for GoogleChatChannel")
        self.webhook_url = webhook_url
        self.timeout = timeout

    # ---- payload -------------------------------------------------------------
    def build_payload(self, readout: ReadoutData, template: str | None = None) -> dict[str, Any]:
        ctx = self.build_context(readout)
        widgets: list[dict[str, Any]] = []

        if template is not None:
            widgets.append(
                {"textParagraph": {"text": _rich(self.format_message(readout, template))}}
            )
        elif self.is_notice(readout):
            # nothing was measured (m12 NTF-2): the sentence alone
            if ctx["notice"]:
                widgets.append({"textParagraph": {"text": _rich(ctx["notice"])}})
        else:
            lines = [
                f"<b>Effect</b> {html.escape(ctx['effect_display'])} · "
                f"{html.escape(ctx['ci_label'])} {html.escape(ctx['ci_display'])}",
                f"p = {html.escape(ctx['pvalue_display'])} · "
                f"α = {html.escape(ctx['alpha_display'])} · "
                f"{html.escape(readout.name_1)} vs {html.escape(readout.name_2)}",
            ]
            for extra in (
                ctx["samples_display"],
                ctx["srm_display"],
                ctx["weekly_cycle_display"],
            ):
                if extra:
                    lines.append(html.escape(extra))
            widgets.append({"textParagraph": {"text": "<br>".join(lines)}})

        links = _links(readout, ctx)
        if links:
            widgets.append({"textParagraph": {"text": links}})
        if ctx["mentions"]:
            widgets.append({"textParagraph": {"text": ctx["mentions"]}})

        card: dict[str, Any] = {
            "header": {
                "title": self.format_title(readout),
                "subtitle": _subtitle(ctx),
            },
            "sections": [{"widgets": widgets}],
        }
        return {"cardsV2": [{"cardId": "abkit-readout", "card": card}]}

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        import requests

        try:
            resp = requests.post(
                self.webhook_url, json=self.build_payload(readout, template), timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            # the webhook credential rides in the URL's query string
            print(
                f"Failed to send Google Chat notification to "
                f"{redact_url(self.webhook_url)}: {describe_error(exc)}"
            )
            return False
        return True

    def format_mentions(self, mentions: list[str]) -> str:
        """Google Chat mentions: broadcast words → ``<users/all>``, a numeric id
        → ``<users/123>``, anything else → a display-only ``@name``."""
        if not mentions:
            return ""
        out: list[str] = []
        for mention in mentions:
            low = mention.lower()
            if low in _ALL_KEYWORDS:
                out.append(_ALL_MENTION)
            elif mention.isdigit():
                out.append(f"<users/{mention}>")
            else:
                out.append(f"@{html.escape(mention)}")
        return " ".join(out)

    def __repr__(self) -> str:
        return f"GoogleChatChannel(webhook_url={redact_url(self.webhook_url)})"


def _rich(value: str) -> str:
    """Escape, then convert newlines — Cards v2 needs ``<br>``, not ``\\n``."""
    return html.escape(value).replace("\n", "<br>")


def _subtitle(ctx: dict[str, Any]) -> str:
    parts = [part for part in (ctx["project_name"], ctx["timestamp"]) if part]
    return " · ".join(parts) if parts else BRAND_USERNAME


def _links(readout: ReadoutData, ctx: dict[str, Any]) -> str:
    anchors: list[str] = []
    if readout.dashboard_url:
        anchors.append(_anchor(readout.dashboard_url, "Open report"))
    for label, url in readout.links.items():
        anchors.append(_anchor(url, label))
    if readout.help_url:
        anchors.append(_anchor(readout.help_url, str(ctx["help_label"])))
    return " · ".join(anchors)


def _anchor(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
