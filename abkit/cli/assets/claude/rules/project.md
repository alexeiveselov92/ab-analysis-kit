# abkit — Project & Profiles config

Two project-level files sit at the root of an abkit project:
`abkit_project.yml` (project settings + statistical defaults) and `profiles.yml`
(database connections). Both support environment-variable interpolation —
`{{ env_var('VAR') }}` and `${VAR}` — so **secrets stay out of YAML**. An
unresolved placeholder is kept **verbatim** (never silently emptied) so a missing
variable is detectable rather than a blank connection; notification-channel secrets
are the exception (an unresolved one is actively rejected). `abk init
--db-type {clickhouse,postgres,mysql}` scaffolds both files (ClickHouse default).

A directory is an abkit project when it contains `abkit_project.yml`.

## `abkit_project.yml`

```yaml
name: my_ab_project              # required — project identifier (alnum/_/-/space)
version: "1.0"                   # optional (default "1.0")
default_profile: dev             # required — profile name from profiles.yml

paths:                           # optional — directory names
  experiments: experiments       # experiment YAMLs (default: experiments)
  metrics: metrics               # metric YAMLs (default: metrics)
  sql: sql                       # query_file: SQL (default: sql)

statistics:                      # project-wide defaults; an experiment overrides any
  alpha: 0.05                    # default significance level (0,1) (default 0.05)
  test_type: relative            # relative | absolute (default relative)
  correction: bonferroni         # none | bonferroni | benjamini_hochberg (default bonferroni)
  power: 0.8                     # target power for MDE / plan (0,1) (default 0.8)
  aa_fpr_budget: null            # optional A/A false-positive budget the validate matrix colours against

limits:                          # look-count & small-sample gates
  max_looks: 5000                # planned looks above this = a config error (the one hard cadence gate)
  warn_looks: 100                # looks above this WITHOUT sequential = peeking warning
  min_units_per_arm: 100         # below this a row is demoted to insufficient_data (written, inference withheld)

timeouts:                        # per-step, seconds
  load: 3600                     # load step (default 3600)
  compute: 7200                  # compute step; also the run-lock staleness threshold (default 7200)

compute:
  mode: recompute                # v1: full-window recompute (the golden reference). Only value today.
  incremental_reads: false       # opt-in (M9): eligible closed-form comparisons read the `state`
                                 # stage's `_ab_unit_state` day moments instead of re-scanning the
                                 # fact window; any gap falls back to recompute. An experiment can
                                 # override it with its own `incremental_reads:`. Changes HOW a
                                 # number is computed, never the number.
```

Everything except `name` and `default_profile` has a default — a minimal project
file is just those two lines (that is what `abk init` writes; the rest is
commented). `tables:` (the six `_ab_*` names) exists for forward-compat but
**rejects overrides** today — the names are canonical.

### The statistics block

| Field | Meaning |
|---|---|
| `alpha` | Experiment-level significance, **pre-correction**. The per-comparison post-correction alpha is derived (see two-tier below), never set here. |
| `test_type` | `relative` (percent lift) or `absolute` (raw difference) — the units the persisted `effect` and any `min_effect` live in. |
| `correction` | Multiple-testing correction across a comparison family. `bonferroni` = the config-time two-tier scheme; `benjamini_hochberg` = read-time FDR across the experiment's metrics; `none` disables. |
| `power` | Target power for MDE reporting and `abk plan` sizing. |
| `aa_fpr_budget` | Tuning-only band for the `abk validate` matrix: a fraction in `(0,1]`; a cell whose measured FPR exceeds it colours red. A `metric.aa_fpr_budget` overrides it. Never touches the pipeline math. |

Nothing in this block enters `method_config_id` — alpha/correction/power are
experiment-level and do not orphan a results series when changed.

### The limits block (cadence & small-sample gates)

`cadence` on the experiment enumerates cumulative cutoffs (looks). `max_looks` is
the single hard gate — a plan that would produce more looks than this is a config
error at validate time (there is deliberately **no** minimum-interval floor).
`warn_looks` is softer: past it, an experiment **without** `sequential.enabled`
gets a peeking warning (fixed-horizon CIs are not valid under repeated looks).
`min_units_per_arm` is the small-sample floor — a cutoff below it is written to
`_ab_results` but demoted to `insufficient_data` with inference withheld.

## `profiles.yml`

Connections live here, keyed by name; `default_profile` at the top selects one.
`abk run` (without `--profile`) uses **this** file's `default_profile` — the
`default_profile` in `abkit_project.yml` is not read at runtime; keep them in sync
to avoid confusion.

> ClickHouse, PostgreSQL and MySQL are all supported. ClickHouse/MySQL use two
> *databases* (`internal_database` / `data_database`). PostgreSQL connects to one
> `database` and uses two *schemas* (`internal_schema` / `data_schema`).

**ClickHouse** (no `database:` field):
```yaml
default_profile: dev

profiles:
  dev:
    type: clickhouse
    host: localhost
    port: 9000                    # native protocol
    user: default                 # optional (default "default")
    password: ""                  # optional
    internal_database: abkit_internal   # required — where the _ab_* tables live
    data_database: analytics            # required — your fact tables (queries read here)
    settings:                     # optional ClickHouse settings
      max_execution_time: 600

  prod:
    type: clickhouse
    host: "{{ env_var('ABKIT_CH_HOST') }}"
    port: 9000
    user: "{{ env_var('ABKIT_CH_USER') }}"
    password: "{{ env_var('ABKIT_CH_PASSWORD') }}"
    internal_database: abkit_internal
    data_database: analytics
```

**PostgreSQL** (connect to `database`, tables in schemas):
```yaml
profiles:
  prod:
    type: postgres
    host: localhost
    port: 5432
    database: analytics           # required — the database to connect to
    user: postgres
    password: "{{ env_var('ABKIT_PG_PASSWORD') }}"
    internal_schema: abkit        # required — _ab_* tables
    data_schema: public           # required — data queries
    settings: {}                  # optional — extra psycopg2.connect kwargs
```

**MySQL** (8.0+; two databases):
```yaml
profiles:
  prod:
    type: mysql
    host: localhost
    port: 3306
    user: root
    password: "{{ env_var('ABKIT_MYSQL_PASSWORD') }}"
    internal_database: abkit      # required — _ab_* tables
    data_database: analytics      # required — data queries
    database: analytics           # optional — default db for the connection
    settings: {}                  # optional — extra pymysql.connect kwargs
```

### Connection field reference

| Field | Type / applies | Meaning |
|---|---|---|
| `type` | required, all | `clickhouse` \| `postgres` \| `mysql` |
| `host` | all | default `localhost` |
| `port` | required, all | 1–65535 |
| `user` / `password` | all | credentials; put secrets behind `env_var` |
| `database` | **required PG**, optional MySQL, unused CH | the database to connect to |
| `internal_database` | CH / MySQL | where the `_ab_*` tables live |
| `internal_schema` | PostgreSQL | schema for the `_ab_*` tables |
| `data_database` | CH / MySQL | where metric/assignment SQL reads from |
| `data_schema` | PostgreSQL | schema for data queries |
| `settings` | all | extra backend driver kwargs |

Keep the internal location **separate** from your analytics location so the
`_ab_*` tables don't clutter shared schemas. Override per run with
`abk run --profile prod`.

### `notification_channels` (optional — for `abk test-report`)

A top-level `notification_channels:` block in `profiles.yml`, keyed by channel
name, declares where readouts can be sent. Each entry has a `type`
(`slack` \| `mattermost` \| `webhook` \| `telegram` \| `email`) plus that
transport's fields (put secrets behind `env_var`/`${VAR}` — an unresolved
channel secret **is** rejected):

```yaml
notification_channels:
  team_slack:
    type: slack
    webhook_url: "${SLACK_WEBHOOK_URL}"
  ops_telegram:
    type: telegram
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "-1001234567890"
```

`abk test-report <exp> [--channel <name>]... [--profile <p>]` sends a **mock**
WIN readout through them (no lock, no warehouse read, no stats) and prints a
per-channel ✓/✗, exiting non-zero if any channel fails — a connectivity/format
smoke test before wiring channels into an orchestrator.

## The two-tier alpha (why the effective alpha isn't the alpha you set)

`bonferroni` correction applies the legacy **two-tier** scheme keyed off each
comparison's `is_main_metric` flag. With `C = C(groups, 2)` pairwise comparisons:

- **Main metric** (`is_main_metric: true`): effective per-comparison alpha
  `= alpha / C` — the full budget, only the pairwise divisor.
- **Secondary metrics** (everything else): effective alpha
  `= alpha / (C × n_secondary)`, where `n_secondary` is the count of non-main
  metrics sharing that tier. An experiment with only its main metric has **no**
  secondary tier.

So the main verdict is protected at the looser (more powerful) alpha, and the
secondary metrics split a stricter budget. `abk run`, `abk validate`, and the HTML
report **echo** the effective per-comparison alpha and the `C × metrics` divisor —
inspect them there; do not compute alpha by hand. `abk validate` persists at and
`abk plan` sizes at this **same effective** alpha (the same resolver), so an A/A
cell calibrated for a metric matches what the pipeline actually applied.

`benjamini_hochberg` instead controls FDR **read-time** across the family;
`none` disables correction. See `.claude/rules/ab-analysis-kit/experiments.md`
for `is_main_metric` / `is_guardrail` on the comparison, and `validate.md` for how
the calibration chip reads the effective alpha.

## First-run setup

The `profiles.yml` that `abk init` writes is a **placeholder**: its `dev` profile
points at example locations on `localhost` and a `prod` profile reads secrets from
`ABKIT_*` env vars. Edit the host, credentials, and location names to match your
environment before running — the **`abk-setup-project`** skill walks this. Then:

```bash
abk run --steps validate         # config lint only, NO database needed
abk run --select <experiment>    # the real pipeline
```

`abk run --steps validate` is a pure config lint (macros, method params, looks
gates) and is **not** the A/A `abk validate` matrix — see `validate.md`. Every
scaffolded example round-trips through the real pydantic models + the level-2
validator at `init` time, so the shapes above are exactly what the code accepts.
The CLI exits **non-zero** on any failure (safe for cron / Prefect).
