"""Telegram Bot API channel.

Sends one readout to a chat via ``sendMessage``. The default (no-template) body
is a rich HTML card with ``parse_mode='HTML'`` and every interpolated value
``html.escape``d (Markdown mode 400s on characters like ``_`` in free-form
values). A caller-supplied ``template`` is sent as **plain text** (no
``parse_mode``) so unescaped ``<``/``>``/``&`` in a value can never trigger a
"can't parse entities" 400. A 4096-char guard truncates on a line boundary so a
cut never splits an open ``<b>``/``<a>`` tag.
"""

from __future__ import annotations

import html
from typing import Any

from abkit.notify.base import BaseChannel, ReadoutData, describe_error, redact_url

_MAX_LEN = 4096
_DESC_CAP = 500


class TelegramChannel(BaseChannel):
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        parse_mode: str | None = "HTML",
        disable_notification: bool = False,
        template: str | None = None,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        if not chat_id:
            raise ValueError("chat_id is required")
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.parse_mode = parse_mode
        self.disable_notification = disable_notification
        self.template = template

    def format_mentions(self, mentions: list[str]) -> str:
        """Plain-text mentions (``@name``) for the base plain-text template path.

        The rich HTML default body uses :meth:`_html_mentions` instead, which
        adds ``tg://`` deep links and HTML-escapes handles.
        """
        if not mentions:
            return ""
        return " ".join(f"@{m}" for m in mentions)

    def _html_mentions(self, mentions: list[str]) -> str:
        """HTML-safe mentions for the rich body: a numeric id → a ``tg://user``
        deep link; any other handle → ``@name`` with the handle HTML-escaped."""
        if not mentions:
            return ""
        out: list[str] = []
        for m in mentions:
            if m.isdigit():
                out.append(f'<a href="tg://user?id={m}">user</a>')
            else:
                out.append(f"@{html.escape(m)}")
        return " ".join(out)

    def _build_html_message(self, readout: ReadoutData) -> str:
        ctx = self.build_context(readout)

        def esc(value: Any) -> str:
            return html.escape(str(value))

        parts = [
            f"{ctx['verdict_emoji']} <b>{esc(ctx['project_name_prefix'])}"
            f"{esc(readout.experiment)} · {esc(readout.metric)}: {esc(ctx['verdict_word'])}</b>"
        ]
        if readout.description:
            parts.append(f"<i>{esc(readout.description[:_DESC_CAP])}</i>")
        parts.append(
            f"Effect: {esc(ctx['effect_display'])} · {esc(ctx['ci_label'])} "
            f"{esc(ctx['ci_display'])}"
        )
        parts.append(
            f"p = {esc(ctx['pvalue_display'])} · α = {esc(ctx['alpha_display'])} · "
            f"{esc(readout.name_1)} vs {esc(readout.name_2)}"
        )
        if ctx["samples_display"]:
            parts.append(esc(ctx["samples_display"]))
        if ctx["srm_display"]:
            parts.append(f"<b>{esc(ctx['srm_display'])}</b>")
        if ctx["weekly_cycle_display"]:
            parts.append(esc(ctx["weekly_cycle_display"]))
        links = []
        if readout.dashboard_url:
            links.append(
                f'<a href="{html.escape(readout.dashboard_url, quote=True)}">Open report</a>'
            )
        for label, url in readout.links.items():
            links.append(f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>')
        if readout.help_url:
            links.append(
                f'<a href="{html.escape(readout.help_url, quote=True)}">{esc(ctx["help_label"])}</a>'
            )
        if links:
            parts.append(" · ".join(links))
        mentions = self._html_mentions(readout.mentions)  # HTML-escaped here
        if mentions:
            parts.append(mentions)

        message = "\n".join(parts)
        if len(message) > _MAX_LEN:
            cut = message[: _MAX_LEN - 1]
            nl = cut.rfind("\n")
            if nl > 0:
                cut = cut[:nl]
            message = cut + "…"
        return message

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        import requests

        # Only the rich default body is HTML (and fully escaped); a caller
        # template renders via str.format (unescaped) so it is sent as plain text
        # — sending unescaped text under parse_mode=HTML would 400 on </>/& in a
        # readout value.
        active = template or self.template
        html_mode = False
        if active is not None:
            text = self.format_message(readout, active)
        elif self.parse_mode == "HTML":
            text = self._build_html_message(readout)
            html_mode = True
        else:
            text = self.format_message(readout)

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_notification": self.disable_notification,
            "disable_web_page_preview": True,
        }
        if html_mode:
            payload["parse_mode"] = "HTML"
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as exc:
            # Redact: the bot_token is in the URL path; requests embeds the full
            # URL in the exception string — never print the raw exc.
            print(
                f"Failed to send Telegram notification to "
                f"{redact_url(url)}: {describe_error(exc)}"
            )
            return False
        return True

    def __repr__(self) -> str:
        return f"TelegramChannel(chat_id={self.chat_id})"
