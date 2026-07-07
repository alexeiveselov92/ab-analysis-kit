"""abkit notification channels — the ``abk test-report`` delivery layer.

A deliberately minimal, experiment-primary notification surface (NOT an alerting
subsystem): a base channel contract, five channels (webhook / slack / mattermost
/ telegram / email), a factory, and a synthetic mock readout. Ported and
reshaped from detectkit's alerting channels (m6-implementation-plan.md WP5) — the
transport/envelope kept, every anomaly/detector/severity/recovery semantic
dropped. Secrets come only from env interpolation.

``branding`` is imported by its full dotted path (not re-exported here).
"""

from __future__ import annotations

from abkit.notify.base import VERDICT_KINDS, BaseChannel, ReadoutData
from abkit.notify.email import EmailChannel
from abkit.notify.factory import ChannelFactory
from abkit.notify.mattermost import MattermostChannel
from abkit.notify.mock import create_mock_readout
from abkit.notify.slack import SlackChannel
from abkit.notify.telegram import TelegramChannel
from abkit.notify.webhook import WebhookChannel

__all__ = [
    "BaseChannel",
    "ReadoutData",
    "VERDICT_KINDS",
    "WebhookChannel",
    "SlackChannel",
    "MattermostChannel",
    "TelegramChannel",
    "EmailChannel",
    "ChannelFactory",
    "create_mock_readout",
]
