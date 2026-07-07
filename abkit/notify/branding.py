"""Single source of truth for the abkit notification-bot identity.

The channels (webhook/slack/mattermost/email) import these as *fallback
defaults*; every channel still accepts a per-channel override (``username`` /
``from_name`` for the name, ``icon_url`` / ``icon_emoji`` for the avatar). Keeping
one definition means a domain change is a one-line edit.

abkit has NO alerting subsystem — a notification is a *readout* (a decision
snapshot from ``abk run``), never an anomaly/recovery/no-data event, so the guide
link points at the "reading a readout" doc, not detectkit's "reading alerts".

The bot avatar is an abkit-branded asset (the Iris "Diverge" mark). It MUST be a
raster PNG — Slack/Mattermost do not render an SVG as a webhook bot avatar. The
asset itself is served from the website (WP7); a placeholder is fine until then.
"""

from __future__ import annotations

BRAND_USERNAME = "abkit"
BRAND_SITE_URL = "https://abkit.pipelab.dev"
# Raster PNG (not SVG) — the Slack/Mattermost webhook-avatar constraint.
BRAND_ICON_URL = f"{BRAND_SITE_URL}/bot-icon.png"
# "How to read this readout" — the A/B analogue of detectkit's alert guide.
READOUT_GUIDE_URL = f"{BRAND_SITE_URL}/guides/reading-a-readout/"
READOUT_GUIDE_LABEL = "How to read this readout"
