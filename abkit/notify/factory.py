"""Construct a notification channel from a declarative config block.

The profiles.yml ``notification_channels:`` block is a mapping of name → a flat
dict with a required ``type`` plus channel-specific params as sibling keys
(mirroring each channel's constructor). ``ProfilesConfig.from_yaml`` already
env-interpolates the whole file, so secrets resolve before they reach here; the
factory re-runs interpolation defensively (a no-op on resolved values) and then
refuses a config whose required secret is still an unresolved ``${VAR}`` /
``{{ env_var('VAR') }}`` placeholder — the interpolation helper leaves those
verbatim rather than raising, so we surface the missing env var loudly instead of
POSTing a literal placeholder.
"""

from __future__ import annotations

import re
from typing import Any

from abkit.notify.base import BaseChannel
from abkit.notify.email import EmailChannel
from abkit.notify.mattermost import MattermostChannel
from abkit.notify.slack import SlackChannel
from abkit.notify.telegram import TelegramChannel
from abkit.notify.webhook import WebhookChannel
from abkit.utils import interpolate_env_vars


class ChannelFactory:
    CHANNEL_TYPES: dict[str, type[BaseChannel]] = {
        "webhook": WebhookChannel,
        "mattermost": MattermostChannel,
        "slack": SlackChannel,
        "telegram": TelegramChannel,
        "email": EmailChannel,
    }

    @classmethod
    def create(cls, channel_type: str, params: dict[str, Any]) -> BaseChannel:
        key = (channel_type or "").lower()
        if key not in cls.CHANNEL_TYPES:
            available = ", ".join(sorted(cls.CHANNEL_TYPES))
            raise ValueError(f"Unknown channel type '{channel_type}'. Available: {available}")
        resolved = interpolate_env_vars(dict(params))
        _reject_unresolved(key, resolved)
        try:
            return cls.CHANNEL_TYPES[key](**resolved)
        except TypeError as exc:
            raise ValueError(f"Invalid parameters for {key} channel: {exc}") from exc

    @classmethod
    def create_from_config(cls, channel_config: dict[str, Any]) -> BaseChannel:
        config = dict(channel_config)
        channel_type = config.pop("type", None)
        if not channel_type:
            raise ValueError("Channel config must have a 'type' field")
        return cls.create(channel_type, config)

    @classmethod
    def create_multiple(cls, channel_configs: list[dict[str, Any]]) -> list[BaseChannel]:
        return [cls.create_from_config(c) for c in channel_configs]

    @classmethod
    def list_available_types(cls) -> list[str]:
        return sorted(cls.CHANNEL_TYPES)


# A genuine leftover placeholder — the SAME grammar env_interpolation resolves
# (shell ``${VAR}`` with a closing brace, or dbt ``{{ env_var('VAR') }}``). Using
# the real grammar (not a loose ``"${" in value`` substring) avoids false-rejecting
# a resolved secret that merely contains those characters.
_UNRESOLVED = re.compile(r"\$\{[^}]+\}|\{\{\s*env_var\(['\"][^'\"]+['\"]\)\s*\}\}")


def _reject_unresolved(channel_type: str, params: dict[str, Any]) -> None:
    """Raise if a param still carries an unresolved env placeholder.

    Names the param only — never echoes the value, which for a resolved secret
    would leak it to stdout via the CLI's per-channel error line.
    """
    for name, value in params.items():
        if isinstance(value, str) and _UNRESOLVED.search(value):
            raise ValueError(
                f"{channel_type} channel: '{name}' references an unset environment "
                "variable — set it, or remove the placeholder"
            )
