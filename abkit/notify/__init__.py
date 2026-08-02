"""abkit notification channels — the ``abk test-report`` delivery layer.

A deliberately minimal, experiment-primary notification surface (NOT an alerting
subsystem): a base channel contract, nine channels (webhook / slack / mattermost
/ telegram / email / discord / teams / googlechat / ntfy), a factory, and a
synthetic mock readout. Ported and
reshaped from detectkit's alerting channels (m6-implementation-plan.md WP5) — the
transport/envelope kept, every anomaly/detector/severity/recovery semantic
dropped. Secrets come only from env interpolation.

``branding`` and ``dispatch`` are imported by their full dotted paths (not
re-exported here) — ``dispatch`` pulls in the config and pipeline packages, and
``abk test-report`` must keep resolving a channel without any of that.
"""

from __future__ import annotations

from abkit.notify.base import NOTICE_KINDS, VERDICT_KINDS, BaseChannel, ReadoutData
from abkit.notify.discord import DiscordChannel
from abkit.notify.email import EmailChannel
from abkit.notify.factory import ChannelFactory
from abkit.notify.googlechat import GoogleChatChannel
from abkit.notify.mattermost import MattermostChannel
from abkit.notify.mock import create_mock_readout
from abkit.notify.ntfy import NtfyChannel
from abkit.notify.slack import SlackChannel
from abkit.notify.teams import TeamsChannel
from abkit.notify.telegram import TelegramChannel
from abkit.notify.webhook import WebhookChannel

__all__ = [
    "BaseChannel",
    "ReadoutData",
    "VERDICT_KINDS",
    "NOTICE_KINDS",
    "WebhookChannel",
    "SlackChannel",
    "MattermostChannel",
    "TelegramChannel",
    "EmailChannel",
    "DiscordChannel",
    "TeamsChannel",
    "GoogleChatChannel",
    "NtfyChannel",
    "ChannelFactory",
    "create_mock_readout",
]
