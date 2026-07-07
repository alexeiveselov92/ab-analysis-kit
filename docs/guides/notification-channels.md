# Notification channels (abk test-report)

abkit is **not** a monitoring system — it has no alerting subsystem, no
severities, no recovery/no-data events. What it *does* have is a way to push a
finished **readout** (the WIN / LOSE / FLAT / INCONCLUSIVE decision from
`abk run`) to a chat or inbox, and a command to verify that plumbing works:

```bash
abk test-report example_signup_test
```

`abk test-report` sends a **mock readout** through every channel you have
configured and prints a per-channel ✓/✗. It is a connectivity and formatting
check — it does not read your warehouse, take a lock, or run any statistics; the
payload is synthetic. Use it after wiring up a channel (or rotating a secret) to
confirm messages arrive and look right.

> Delivering *real* readouts on a schedule, and project-level error notification,
> are separate concerns not yet built — `test-report` is the smoke test only.

## Configuring channels

Channels live in `profiles.yml` under a top-level `notification_channels:` block,
a mapping of your own channel name → a config with a `type` plus that channel's
fields:

```yaml
default_profile: dev
profiles:
  dev: { type: clickhouse, host: localhost, port: 9000 }

notification_channels:
  team_slack:
    type: slack
    webhook_url: "${SLACK_WEBHOOK_URL}"
  ops_telegram:
    type: telegram
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
```

### Secrets come from the environment

Never put a token or webhook URL in `profiles.yml` in plaintext. Reference an
environment variable with either syntax abkit already supports for DB secrets:

- shell style — `${SLACK_WEBHOOK_URL}`
- dbt style — `{{ env_var('SLACK_WEBHOOK_URL') }}`

The value is resolved when the file is loaded. If the variable is **not set**,
abkit refuses the channel with a clear error naming the field, rather than
sending a literal `${...}` placeholder.

## The channel types

Every non-`type` key is passed straight to the channel, so the field names below
are the full surface.

### `slack` / `mattermost`

Post to an incoming webhook. Slack and Mattermost share a compatible payload
(one status-colored attachment); the correct markdown is chosen automatically
from the webhook host.

| Field | Required | Notes |
|---|---|---|
| `webhook_url` | yes | The incoming-webhook URL (the secret lives in the path). |
| `channel` | no | Override the target channel (e.g. `#experiments`). |
| `username` | no | Bot display name (default `abkit`). |
| `icon_url` / `icon_emoji` | no | Bot avatar; `icon_url` wins. Defaults to the abkit avatar. |
| `timeout` | no | Request timeout in seconds (default 10). |

For a self-hosted webhook behind auth, use `type: webhook` — it adds an
`extra_headers` field (e.g. `{Authorization: "Bearer ${TOKEN}"}`) that the Slack
and Mattermost types deliberately do not expose.

### `telegram`

Send via the Bot API `sendMessage`.

| Field | Required | Notes |
|---|---|---|
| `bot_token` | yes | From @BotFather. |
| `chat_id` | yes | User / group / `@channel` id. |
| `parse_mode` | no | `HTML` (default) renders a rich card; `Markdown` or empty for plain text. |
| `disable_notification` | no | Send silently. |

### `email`

Send over SMTP as a plain-text + branded-HTML message.

| Field | Required | Notes |
|---|---|---|
| `smtp_host`, `smtp_port` | yes | e.g. `smtp.gmail.com`, `587`. |
| `from_email` | yes | Envelope sender. |
| `to_emails` | yes | A list, or a comma-separated string. |
| `smtp_username` / `smtp_password` | no | Login is attempted only when both are set (open relays need neither). |
| `use_tls` | no | `true` (default) = STARTTLS on 587; `false` = implicit TLS via SMTP_SSL on 465 (never plaintext). |
| `from_name` | no | From display name (default `abkit`). |

## Running the check

```bash
# every configured channel
abk test-report my_experiment

# just one or two, by name
abk test-report my_experiment --channel team_slack --channel ops_telegram
```

The `EXPERIMENT` argument only labels the mock (it borrows the experiment's arm
names, main metric, and effective alpha for a realistic-looking message). The
command exits **non-zero** if any channel fails to send or is misconfigured, so
it is safe to wire into CI or a pre-flight check.
