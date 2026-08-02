"""Microsoft Teams channel — Adaptive Card over a Power Automate webhook (m12 NTF-4).

Targets the **Workflows** webhook (Power Automate), NOT the retired O365
connector: Microsoft has been switching those off, and the two take different
payloads. The envelope below — ``{"type": "message", "attachments": [{contentType:
"application/vnd.microsoft.card.adaptive", content: <card>}]}`` — is the
Workflows shape, a platform fact carried over from the detectkit donor.

Two consequences of that path, both Microsoft's:

* **No per-message bot name or avatar.** The Workflows connector posts as the
  flow's own identity, so ``BRAND_USERNAME``/icon overrides other channels honour
  have nowhere to go here.
* **Colour is a named token, not a hex.** Adaptive Cards take
  ``Good``/``Attention``/``Warning``/``Accent``/``Default``, so the brand hex is
  mapped rather than passed through — the one channel where the token layer
  cannot be the literal source.
"""

from __future__ import annotations

from typing import Any

from abkit.notify.base import BaseChannel, ReadoutData, describe_error, redact_url
from abkit.notify.branding import BRAND_USERNAME

#: The verdict → Adaptive Card colour token map. A notice (m12 NTF-2) is
#: ``Attention`` for the same reason it reuses the SRM brand token: there is no
#: result, and the card should look like something needs a human.
_CARD_COLORS = {
    "WIN": "Good",
    "LOSE": "Attention",
    "FLAT": "Default",
    "INCONCLUSIVE": "Warning",
    "SRM": "Attention",
}
_NOTICE_COLOR = "Attention"


class TeamsChannel(BaseChannel):
    """Teams channel posting an Adaptive Card to a Workflows webhook.

    Args:
        webhook_url: the Power Automate "When a Teams webhook request is
            received" trigger URL.
        timeout: request timeout in seconds.
    """

    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required for TeamsChannel")
        self.webhook_url = webhook_url
        self.timeout = timeout

    # ---- payload -------------------------------------------------------------
    def card_color(self, readout: ReadoutData) -> str:
        if self.is_notice(readout):
            return _NOTICE_COLOR
        return _CARD_COLORS.get(self.verdict_kind(readout), "Default")

    def build_card(self, readout: ReadoutData, template: str | None = None) -> dict[str, Any]:
        """The Adaptive Card ``content`` object — split out from :meth:`send` so
        the shape is unit-testable without a network call."""
        ctx = self.build_context(readout)
        body: list[dict[str, Any]] = [
            _text_block(
                self.format_title(readout),
                weight="Bolder",
                size="Medium",
                color=self.card_color(readout),
            )
        ]
        if readout.description:
            body.append(_text_block(readout.description, is_subtle=True))

        if template is not None:
            body.append(_text_block(self.format_message(readout, template)))
        elif self.is_notice(readout):
            # nothing was measured — the sentence, not an empty FactSet
            if ctx["notice"]:
                body.append(_text_block(ctx["notice"]))
        else:
            facts = [
                {"title": "Effect", "value": ctx["effect_display"]},
                {"title": ctx["ci_label"], "value": ctx["ci_display"]},
                {"title": "p-value", "value": ctx["pvalue_display"]},
                {"title": "alpha", "value": ctx["alpha_display"]},
                {"title": "Arms", "value": f"{readout.name_1} vs {readout.name_2}"},
            ]
            if ctx["samples_display"]:
                facts.append({"title": "Samples", "value": ctx["samples_display"]})
            body.append({"type": "FactSet", "facts": facts})
            for extra in (ctx["srm_display"], ctx["weekly_cycle_display"]):
                if extra:
                    body.append(_text_block(extra, wrap=True))

        if ctx["mentions"]:
            body.append(_text_block(ctx["mentions"], is_subtle=True))
        if ctx["timestamp"]:
            body.append(_text_block(f"Observed: {ctx['timestamp']}", is_subtle=True, size="Small"))
        body.append(_text_block(_footer_text(ctx), is_subtle=True, size="Small"))

        card: dict[str, Any] = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "msteams": {"width": "Full"},
            "body": body,
        }
        actions = []
        if readout.dashboard_url:
            actions.append(
                {"type": "Action.OpenUrl", "title": "Open report", "url": readout.dashboard_url}
            )
        for label, url in readout.links.items():
            actions.append({"type": "Action.OpenUrl", "title": label, "url": url})
        if readout.help_url:
            actions.append(
                {"type": "Action.OpenUrl", "title": ctx["help_label"], "url": readout.help_url}
            )
        if actions:
            card["actions"] = actions
        return card

    def build_payload(self, readout: ReadoutData, template: str | None = None) -> dict[str, Any]:
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": self.build_card(readout, template),
                }
            ],
        }

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        import requests

        try:
            resp = requests.post(
                self.webhook_url, json=self.build_payload(readout, template), timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(
                f"Failed to send Teams notification to "
                f"{redact_url(self.webhook_url)}: {describe_error(exc)}"
            )
            return False
        return True

    def __repr__(self) -> str:
        return f"TeamsChannel(webhook_url={redact_url(self.webhook_url)})"


def _footer_text(ctx: dict[str, Any]) -> str:
    project = ctx["project_name"]
    return f"Sent by {BRAND_USERNAME} · {project}" if project else f"Sent by {BRAND_USERNAME}"


def _text_block(
    text: str,
    *,
    weight: str | None = None,
    size: str | None = None,
    color: str | None = None,
    is_subtle: bool = False,
    wrap: bool = True,
) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "TextBlock", "text": text, "wrap": wrap}
    if weight:
        block["weight"] = weight
    if size:
        block["size"] = size
    if color:
        block["color"] = color
    if is_subtle:
        block["isSubtle"] = True
    return block
