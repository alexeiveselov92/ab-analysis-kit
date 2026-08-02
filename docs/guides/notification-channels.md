# Notification channels

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

Once the plumbing works, `abk run --notify` sends the **real** readout — see
[Sending real readouts](#sending-real-readouts) below.

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

## Sending real readouts

```bash
abk run --select my_experiment --notify
```

After each experiment finishes, abkit reads the rows it just persisted, runs the
**same** readout `abk run --report` bakes into its HTML, and sends one message
per verdict. Nothing is recomputed for the message — a notification cannot
disagree with the report or the dashboard about the same experiment.

Three things follow from that, and they are worth knowing before you wire it
into a scheduler:

- **It is opt-in and it never fails your run.** Without `--notify` no channel is
  even constructed. With it, a channel that is down, misconfigured, or throwing
  is one yellow line — the run's exit code is decided by the pipeline alone.
- **An experiment nobody computed sends nothing.** No results yet (or only rows
  for arm names you have since renamed) is silence, not an "INCONCLUSIVE"
  message. A run that was **locked** or had nothing to do is silent too.
- **A run that FAILED sends an error notice** instead of a readout — see
  [Urgent signals](#urgent-signals-srm-and-pipeline-errors).
- **Only a CHANGE is announced.** The first message about a comparison always
  goes; after that abkit stays quiet until something moves, so a run every hour
  is not a message every hour. See [What counts as a change](#what-counts-as-a-change).

### Routing per experiment

An experiment YAML may add a `notify:` block. It is *routing*, never the switch:
without `--notify` it does nothing, and without a block a notified run goes to
**every** channel in `profiles.yml`.

```yaml
name: my_experiment
# ...
notify:
  channels: [team_slack]        # subset of notification_channels (default: all)
  mentions: [growth-team]       # rendered in each channel's own @-syntax
  on: [readout]                 # signal kinds this experiment sends (default: all)
```

### Sending only what a channel is for

A channel can declare which kinds it accepts:

```yaml
notification_channels:
  team_slack:
    type: slack
    webhook_url: "${SLACK_WEBHOOK_URL}"
  oncall_telegram:
    type: telegram
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
    on: [srm, error]            # urgent only — no routine readouts
```

The two filters **intersect**: a kind must pass the experiment's `on:` *and* the
channel's to be delivered. The experiment narrows what it sends; the channel
narrows what it accepts; neither re-opens what the other closed.

The kinds are `readout`, `verdict_change`, `srm`, `calibration_red`, `stale` and
`error`. **`readout`, `srm` and `error` fire today**; `verdict_change`,
`calibration_red` and `stale` are accepted now so that a filter you write today
keeps its meaning (rather than silently widening) as those signals ship.

## Urgent signals: SRM and pipeline errors

The example above — `on: [srm, error]` — is the on-call channel, and it is only
useful if those two things actually reach it:

- **`srm`** is not a separate message. When an experiment's sample-ratio gate
  fails, the readout abkit already built *is* the urgent signal, so the same
  message answers to both `readout` and `srm`. A channel that accepts either
  gets it (exactly once); a routine channel keeps receiving its readouts; and a
  channel scoped to `on: [srm]` stays quiet until a split actually breaks.
- **`error`** is a run that failed. There is no verdict, effect, CI or p-value
  behind it — the pipeline never got that far — so the message carries the
  reason instead of a statistics block. Nothing renders as `N/A`, and a crashed
  run is never shown as "Flat".

A failing run still exits non-zero: notifying about a failure never converts it
into a success.

Both signals obey the same intersection rule as everything else — an experiment
whose `notify.on` omits `error` will not report its own failures, on any channel.

## What counts as a change

Point `--notify` at a scheduled run and you want it quiet until something
happens. abkit remembers, per comparison, what it last told you (in
`_ab_notify_states`) and sends only when that changes:

| Situation | Sends? |
|---|---|
| First message about a comparison | **yes** |
| Same verdict as last time | no |
| WIN → LOSE, INCONCLUSIVE → WIN, any flip | **yes** |
| Same verdict, but the SRM gate just broke | **yes** |
| Same verdict, SRM still broken | no |
| A run that failed | **yes, every time** — an error is not a verdict, and a run that fails twice failed twice |

The SRM row is the subtle one and it is deliberate: before its horizon an
experiment sits at INCONCLUSIVE for days, so a broken split would keep the
verdict word identical. Remembering the gate alongside the verdict is what keeps
that alarm from being deduped away.

Two consequences worth knowing:

- **A message nobody received is not remembered.** If every channel was down,
  the next run tries again rather than treating the flip as old news.
- **Re-tuning a comparison starts a fresh history.** The memory is keyed by the
  method identity, so changing a method param means the next run announces the
  new comparison's verdict even if the word is the same.

To make abkit repeat itself — after a channel migration, say — purge the
experiment's rows with `abk clean --orphaned-experiments` (it resets the dedup
along with everything else) or delete from `_ab_notify_states` directly.
