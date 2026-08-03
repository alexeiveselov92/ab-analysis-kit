# Notification channels

abkit is **not** a monitoring system — it has no alerting subsystem, no
severities, no recovery/no-data events. What it *does* have is a way to push
what a run just decided to a chat or inbox:

```bash
abk run --notify           # readouts, SRM breaches, pipeline errors, schedule slips
abk validate --notify      # methods whose false-positive rate broke its budget
```

Both flags are opt-in, both are best-effort (a channel that is down never fails
your run), and both send **six** kinds of signal you can route independently:

| Kind | Sent by | Means |
|---|---|---|
| `readout` | `abk run --notify` | a comparison's verdict — WIN / LOSE / FLAT / INCONCLUSIVE |
| `verdict_change` | `abk run --notify` | that verdict *flipped* since the last message |
| `srm` | `abk run --notify` | the sample-ratio gate failed — the same readout, re-classified |
| `error` | `abk run --notify` | the pipeline failed; there is no result to report |
| `stale` | `abk run --notify` | the computed series was behind the looks already due |
| `calibration_red` | `abk validate --notify` | an A/A cell exceeded its false-positive budget |

Nothing in a message is recomputed for it: a verdict is `readout.evaluate()`'s
over the persisted rows — the same decision `abk run --report` bakes into its
HTML — so a notification can never disagree with the report or the dashboard
about the same experiment.

## Check the plumbing first

```bash
abk test-report example_signup_test
```

`abk test-report` sends a **mock readout** through every channel you have
configured and prints a per-channel ✓/✗. It is a connectivity and formatting
check — it does not read your warehouse, take a lock, or run any statistics; the
payload is synthetic. Use it after wiring up a channel (or rotating a secret) to
confirm messages arrive and look right.

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

Nine of them: `webhook`, `slack`, `mattermost`, `telegram`, `email`, `discord`,
`teams`, `googlechat`, `ntfy`. Every non-`type` key (except the `on:` routing
filter) is passed straight to the channel, so the field names below are the full
surface.

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

### `discord`

One embed per readout, via an "Execute Webhook" URL.

| Field | Required | Notes |
|---|---|---|
| `webhook_url` | yes | `https://discord.com/api/webhooks/<id>/<token>`. |
| `username` | no | Bot name override (default `abkit`). |
| `avatar_url` | no | Bot avatar override. |
| `timeout` | no | Request timeout, seconds (default 10). |

Mentions are delivered in the message's top-level content, not inside the embed
— Discord never pings from inside an embed.

### `teams`

An Adaptive Card posted to a **Power Automate "Workflows"** webhook. This is
*not* the retired Office 365 connector: create a flow with the "When a Teams
webhook request is received" trigger and use its URL. Two consequences of that
path, both Microsoft's: the message posts as the flow's identity (no per-message
bot name or avatar), and the card's status colour is a named Adaptive Card token
rather than a hex.

| Field | Required | Notes |
|---|---|---|
| `webhook_url` | yes | The Workflows trigger URL. |
| `timeout` | no | Request timeout, seconds (default 10). |

### `googlechat`

A Cards v2 message posted to a space's incoming webhook.

| Field | Required | Notes |
|---|---|---|
| `webhook_url` | yes | The space webhook (carries `key` and `token` query params — treat it as a secret). |
| `timeout` | no | Request timeout, seconds (default 10). |

Mentions accept `everyone` / `all` / `here` (rendered as the space-wide ping) or
a numeric user id; anything else renders as plain text.

### `ntfy`

Push notifications to an [ntfy](https://ntfy.sh) topic — the public server or
your own.

| Field | Required | Notes |
|---|---|---|
| `topic` | yes | The topic to publish to. |
| `server` | no | Base URL (default `https://ntfy.sh`). |
| `token` | no | Bearer token for a protected topic. |
| `user` / `password` | no | Basic-auth alternative to `token`. |
| `priority` | no | 1–5 override. Applied **only** to the urgent verdicts (LOSE, a failed SRM gate, errors) — a WIN or a FLAT stays calm however you set it, so this cannot be configured into buzzing a phone over routine news. |
| `timeout` | no | Request timeout, seconds (default 10). |

ntfy renders no colour; the status cue is the tag emoji and the priority.

```yaml
notification_channels:
  team_discord:
    type: discord
    webhook_url: "${DISCORD_WEBHOOK_URL}"
  eng_teams:
    type: teams
    webhook_url: "${TEAMS_WORKFLOW_URL}"
  space_chat:
    type: googlechat
    webhook_url: "${GOOGLE_CHAT_WEBHOOK_URL}"
  phone_push:
    type: ntfy
    topic: "${NTFY_TOPIC}"
    priority: 5
```

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

Five things follow from that, and they are worth knowing before you wire it
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
- **A run whose schedule slipped also says so** — see
  [Calibration and schedule signals](#calibration-and-schedule-signals).

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
  cooldown_seconds: 86400       # repeat an unchanged stale/calibration_red (default: never)
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

The kinds are the six in the table at the top of this page. Two of them are
narrower views of a message that also answers to `readout`, so scoping a channel
to one of those gets you a subset, never a duplicate:

- `on: [srm]` — only readouts whose sample-ratio gate failed.
- `on: [verdict_change]` — only readouts whose verdict *flipped*. Not the first
  message about a comparison (news, but nothing changed), and not one re-sent
  because its SRM gate moved while the word stayed put.

And `calibration_red` comes from `abk validate --notify`, not from `abk run` —
see [Calibration and schedule signals](#calibration-and-schedule-signals).

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

## Calibration and schedule signals

Two signals describe the *machinery* rather than an experiment's result. Neither
carries an effect or a p-value — nothing was measured — so both render as a
notice with a sentence in it.

### `calibration_red` — a method you should not decide on

```bash
abk validate --select my_experiment --notify
```

[`abk validate`](validate.md) measures each method's false-positive rate on
placebo A/A splits. A cell whose measured FPR exceeds its budget is the matrix's
"do not use" verdict, and `--notify` is how it reaches you when nobody is
watching the terminal. The message names each red cell, its FPR and the budget
it broke.

Like `--report`, the flag is best-effort: a notification failure never turns a
successful validation into a failed one, and a validation that genuinely fails
still exits non-zero.

### `stale` — the schedule fell behind

`abk run --notify` sends this when a metric's computed series was more than
three cadence steps behind the looks that were already due when the run planned
them.

It is deliberately **retrospective**, and the message says so: the run that
detects a backlog is the run that computes the missing looks, so what is behind
is your *schedule* — a run that never fired, was locked out, or failed — not
your warehouse. An experiment that has passed its horizon with every look
computed is never "behind", however long ago it finished.

### How often they repeat

Both conditions survive the run that reports them, so unlike a verdict they
would otherwise re-send forever. abkit remembers *which* metrics are behind and
*which* cells are red (never how far behind or by how much — those numbers drift
on every run) and sends again only when that set changes:

| Situation | Sends? |
|---|---|
| First time | **yes** |
| Same metrics behind / same cells red | no |
| Another metric falls behind, another cell goes red | **yes** |
| The condition cleared, then came back | **yes** — recovery is remembered, so a second outage is news |

To be reminded about an unchanged condition, set a cooldown on the experiment:

```yaml
notify:
  cooldown_seconds: 86400       # re-send an unchanged stale/calibration_red once a day
```

It never applies to verdicts: a WIN→LOSE flip always sends immediately, whatever
the cooldown says.

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

Only the rows marked as a *flip* also count as `verdict_change`: the first
message about a comparison and an SRM-triggered re-send are delivered as
`readout` without the verdict having moved.

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

## Wiring it into a scheduler

```bash
abk run --notify                       # every experiment, every signal it has
abk validate --select my_exp --notify  # out-of-band, on its own cadence
```

Three properties make this safe to run unattended, and all three are pinned by
an end-to-end test:

- **A channel cannot change an exit code.** One that is down, misconfigured,
  raising, or lying about success is one yellow line in the log; the exit code
  belongs to the pipeline alone. A run that genuinely failed still exits
  non-zero — notifying about a failure never converts it into a success.
- **A run that says nothing new sends nothing.** Two identical runs deliver one
  message, because the memory lives in your warehouse rather than in the
  process.
- **One broken channel does not block the others.** Each is attempted
  separately.

Two experiments cannot notify about each other's state, and two runs of the same
experiment cannot race: `abk run` already holds one pipeline lock per
experiment, so a second invocation is a no-op that notifies nothing.
`abk validate` takes a different lock and writes a different row, so the two
commands can safely run at once.
