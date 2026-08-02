"""ntfy channel — push notifications to a topic (m12 NTF-4).

Publishes through ntfy's **JSON** endpoint (``POST`` to the server root with
``topic`` in the body) rather than the header-based form (topic in the URL,
fields in HTTP headers). The reason is the donor's and it still holds: HTTP
headers cannot carry UTF-8 reliably, and a readout title contains a status
emoji and may contain any metric name.

ntfy renders no colour — the status cue is the ``tags`` emoji and the
``priority``. The mapping below is abkit's own: the donor's kinds
(anomaly/recovery/no-data) do not exist here, and a verdict is not an alert.
"""

from __future__ import annotations

from typing import Any

from abkit.notify.base import BaseChannel, ReadoutData, describe_error

#: ntfy caps a message body at 4096 bytes; stay under it with headroom.
_MESSAGE_CAP_BYTES = 3800

#: Presentation kind → ntfy tag (the client renders it as a leading emoji).
#: ``format_title`` already carries the verdict emoji, so the title is sent
#: WITHOUT it (see ``_strip_leading_emoji``) — otherwise the glyph doubles up.
_TAGS: dict[str, list[str]] = {
    "WIN": ["white_check_mark"],
    "LOSE": ["red_circle"],
    "FLAT": ["white_circle"],
    "INCONCLUSIVE": ["hourglass"],
    "SRM": ["rotating_light"],
    "error": ["rotating_light"],
    "calibration_red": ["test_tube"],
    "stale": ["hourglass"],
}

#: ntfy priority: 1 min … 5 max, 3 default. Only the kinds that mean "act now"
#: sit above default — a WIN is good news, not an interrupt, and a FLAT least
#: of all. An explicit ``priority=`` raises ONLY those urgent kinds, so a
#: routine readout can never be configured into buzzing a phone at 3am.
_DEFAULT_PRIORITY: dict[str, int] = {
    "WIN": 3,
    "LOSE": 4,
    "FLAT": 2,
    "INCONCLUSIVE": 2,
    "SRM": 4,
    "error": 4,
    "calibration_red": 4,
    "stale": 3,
}
_OVERRIDABLE_KINDS = frozenset({"LOSE", "SRM", "error", "calibration_red"})


class NtfyChannel(BaseChannel):
    """ntfy channel publishing to a topic.

    Args:
        topic: the ntfy topic (required).
        server: base URL (default the public ``https://ntfy.sh``;
            self-hosted servers work identically).
        token: bearer token for a protected topic.
        user / password: basic-auth alternative to ``token``.
        priority: 1..5 override, applied to the urgent kinds only.
        timeout: request timeout in seconds.
    """

    def __init__(
        self,
        topic: str,
        server: str = "https://ntfy.sh",
        token: str | None = None,
        user: str | None = None,
        password: str | None = None,
        priority: int | None = None,
        timeout: int = 10,
    ) -> None:
        if not topic:
            raise ValueError("topic is required for NtfyChannel")
        if priority is not None and not 1 <= priority <= 5:
            raise ValueError("priority must be between 1 and 5")
        self.topic = topic
        self.server = server.rstrip("/")
        self.token = token
        self.user = user
        self.password = password
        self.priority = priority
        self.timeout = timeout

    # ---- payload -------------------------------------------------------------
    def build_payload(self, readout: ReadoutData, template: str | None = None) -> dict[str, Any]:
        ctx = self.build_context(readout)
        kind = self.verdict_kind(readout)
        payload: dict[str, Any] = {
            "topic": self.topic,
            "title": _strip_leading_emoji(self.format_title(readout), ctx["verdict_emoji"]),
            "message": _cap_bytes(self.format_message(readout, template), _MESSAGE_CAP_BYTES),
            "priority": self.priority_for(kind),
            "tags": _TAGS.get(kind, ["bell"]),
        }
        if readout.dashboard_url:
            payload["click"] = readout.dashboard_url
        actions = [
            {"action": "view", "label": label, "url": url} for label, url in readout.links.items()
        ]
        if readout.help_url:
            actions.append(
                {"action": "view", "label": str(ctx["help_label"]), "url": readout.help_url}
            )
        if actions:
            payload["actions"] = actions[:3]  # ntfy renders at most three
        return payload

    def priority_for(self, kind: str) -> int:
        """The configured override applies to urgent kinds only — a routine
        readout stays calm however the channel is configured."""
        default = _DEFAULT_PRIORITY.get(kind, 3)
        if self.priority is not None and kind in _OVERRIDABLE_KINDS:
            return self.priority
        return default

    def send(self, readout: ReadoutData, template: str | None = None) -> bool:
        import requests

        headers: dict[str, str] = {}
        auth: tuple[str, str] | None = None
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif self.user and self.password:
            auth = (self.user, self.password)

        try:
            resp = requests.post(
                self.server,
                json=self.build_payload(readout, template),
                headers=headers,
                auth=auth,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            # the topic is not secret, but a bearer token may ride in headers
            # that requests echoes on some errors — describe_error only
            print(f"Failed to send ntfy notification to topic {self.topic}: {describe_error(exc)}")
            return False
        return True

    def __repr__(self) -> str:
        return f"NtfyChannel(server={self.server}, topic={self.topic})"


def _strip_leading_emoji(title: str, emoji: str) -> str:
    """Drop the verdict glyph ntfy will re-add from ``tags``."""
    if emoji and title.startswith(emoji):
        return title[len(emoji) :].lstrip()
    return title


def _cap_bytes(value: str, limit: int) -> str:
    """Truncate on a UTF-8 BYTE budget — ntfy's cap is bytes, and a body of
    multibyte characters would otherwise pass a character-count check and be
    rejected by the server."""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    ellipsis = "…"
    budget = max(limit - len(ellipsis.encode("utf-8")), 0)
    return encoded[:budget].decode("utf-8", errors="ignore") + ellipsis
