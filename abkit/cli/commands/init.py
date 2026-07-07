"""Implementation of ``abk init`` — project scaffolding with a RUNNABLE example.

The A/B empty path is longer than detectkit's, so init ships a fully working
example (cli-and-dx.md §6): ``example_signup_test.yml`` + a real assignment
SQL + two example metrics (a z-test fraction and a CUPED sample) against a
documented synthetic seed dataset — so ``abk init && <seed> && abk run
--select example_signup_test`` produces real results on a fresh machine, not
a placeholder-table error.

Donor discipline kept: every scaffolded artifact ROUND-TRIPS through the real
pydantic config classes + the level-2 validator before init reports success —
docs and examples cannot drift from the code (declarative-config.md §5).
"""

from __future__ import annotations

from pathlib import Path

import click

from abkit.cli._output import echo_done, echo_tree

# ── scaffold payloads ─────────────────────────────────────────────────────────

PROJECT_YML = """\
# abkit project configuration (see docs: https://abkit.pipelab.dev)
name: "{project_name}"
version: "1.0"

default_profile: dev

# statistics:            # project-wide defaults (experiments override)
#   alpha: 0.05
#   correction: bonferroni
#   power: 0.8
# limits:
#   max_looks: 5000      # the one hard cadence gate
#   warn_looks: 100      # peeking warning threshold without sequential
#   min_units_per_arm: 100
"""

PROFILES_CLICKHOUSE = """\
default_profile: dev

profiles:
  dev:
    type: clickhouse
    host: localhost
    port: 9000
    user: default
    password: ""
    internal_database: abkit_internal   # the _ab_* tables live here
    data_database: analytics            # your fact tables (and the seed dataset)

  prod:
    type: clickhouse
    host: "{{ env_var('ABKIT_CH_HOST') }}"
    port: 9000
    user: "{{ env_var('ABKIT_CH_USER') }}"
    password: "{{ env_var('ABKIT_CH_PASSWORD') }}"
    internal_database: abkit_internal
    data_database: analytics

# Optional: channels for `abk test-report` (a connectivity / format check).
# Secrets are read from env vars at load time — never stored here. Uncomment to use.
# notification_channels:
#   team_slack:
#     type: slack
#     webhook_url: "${SLACK_WEBHOOK_URL}"
#   ops_telegram:
#     type: telegram
#     bot_token: "${TELEGRAM_BOT_TOKEN}"
#     chat_id: "${TELEGRAM_CHAT_ID}"
"""

PROFILES_POSTGRES = """\
default_profile: dev

profiles:
  dev:
    type: postgres
    host: localhost
    port: 5432
    user: postgres
    password: ""
    database: postgres
    internal_schema: abkit
    data_schema: public

  prod:
    type: postgres
    host: "{{ env_var('ABKIT_PG_HOST') }}"
    port: 5432
    user: "{{ env_var('ABKIT_PG_USER') }}"
    password: "{{ env_var('ABKIT_PG_PASSWORD') }}"
    database: analytics
    internal_schema: abkit
    data_schema: public

# Optional: channels for `abk test-report` (a connectivity / format check).
# Secrets are read from env vars at load time — never stored here. Uncomment to use.
# notification_channels:
#   team_slack:
#     type: slack
#     webhook_url: "${SLACK_WEBHOOK_URL}"
#   ops_telegram:
#     type: telegram
#     bot_token: "${TELEGRAM_BOT_TOKEN}"
#     chat_id: "${TELEGRAM_CHAT_ID}"
"""

PROFILES_MYSQL = """\
default_profile: dev

profiles:
  dev:
    type: mysql
    host: localhost
    port: 3306
    user: root
    password: ""
    internal_database: abkit
    data_database: analytics

  prod:
    type: mysql
    host: "{{ env_var('ABKIT_MYSQL_HOST') }}"
    port: 3306
    user: "{{ env_var('ABKIT_MYSQL_USER') }}"
    password: "{{ env_var('ABKIT_MYSQL_PASSWORD') }}"
    internal_database: abkit
    data_database: analytics

# Optional: channels for `abk test-report` (a connectivity / format check).
# Secrets are read from env vars at load time — never stored here. Uncomment to use.
# notification_channels:
#   team_slack:
#     type: slack
#     webhook_url: "${SLACK_WEBHOOK_URL}"
#   ops_telegram:
#     type: telegram
#     bot_token: "${TELEGRAM_BOT_TOKEN}"
#     chat_id: "${TELEGRAM_CHAT_ID}"
"""

EXPERIMENT_YML = """\
# The runnable example experiment — works against seed/seed_dataset.*.sql.
# Fixed past dates keep every daily cutoff complete, so a first run computes
# the full 14-point stabilization series immediately.
name: example_signup_test
description: "Example: onboarding experiment against the synthetic seed dataset"
status: running
is_actual: true
tags: [example]

start_date: 2024-07-01      # PINNED left edge of every cumulative window
end_date: 2024-07-14        # planner horizon (the last cutoff covers this day)
unit_key: user_id
cadence: 1d                 # or dense-early: [{every: 1h, until: 48h}, {every: 1d}]
timezone: UTC

assignment:                 # READ-ONLY exposure source — abkit never randomizes
  query_file: sql/example_assignment.sql
  variants: [control, treatment]        # FIRST is control (name_1)
  expected_split: {control: 0.5, treatment: 0.5}   # drives the SRM gate

alpha: 0.05
correction: bonferroni      # two-tier: main metric vs the rest (inspectable at run)

comparisons:
  - metric: example_signup_cr
    is_main_metric: true    # drives the verdict + the two-tier Bonferroni
    method: {name: z-test, params: {test_type: relative, calculate_mde: true}}
  - metric: example_arpu
    method:
      name: cuped-t-test
      params: {test_type: relative, covariate_lookback: 14d}
"""

SIGNUP_CR_YML = """\
name: example_signup_cr
description: "Example: signup conversion (fraction metric, z-test)"
type: fraction
unit_key: user_id
tags: [example]
columns:                  # column roles in the query's result set
  variant: variant
  count: signed_up
  nobs: visits
sql: |
  {% import 'abkit_assignment.jinja' as ab %}
  -- ONE ROW PER UNIT with additive aggregates over the cumulative window.
  -- The window is PINNED-START / MOVING-END: ab_start_date never moves,
  -- ab_end_ts advances per cutoff — so every run aggregates from experiment
  -- start through the cutoff (the stabilization chart's points).
  -- ab.exposed_units() joins the persisted cohort (_ab_exposures), applies
  -- the window + exposure filters and dedup — never re-implement those.
  SELECT
      {{ ab.variant_col() }}  AS variant,
      user_id,
      max(signed_up)          AS signed_up,   -- converted within the window?
      1                       AS visits       -- one trial per exposed unit
  FROM {{ data_database }}.example_signup_events
  {{ ab.exposed_units() }}
  GROUP BY variant, user_id
"""

ARPU_YML = """\
name: example_arpu
description: "Example: revenue per user (sample metric, CUPED)"
type: sample
unit_key: user_id
tags: [example]
columns:
  variant: variant
  value: gross_usd
sql: |
  {% import 'abkit_assignment.jinja' as ab %}
  -- CUPED needs no extra SQL: with covariate_lookback set on the method,
  -- abkit renders THIS SAME query a second time over the pre-period window
  -- (exposure filter dropped) and uses the pre-period value as the covariate.
  SELECT
      {{ ab.variant_col() }}  AS variant,
      user_id,
      sum(gross_usd)          AS gross_usd    -- ADDITIVE: one row per unit
  FROM {{ data_database }}.example_signup_events
  {{ ab.exposed_units() }}
  GROUP BY variant, user_id
"""

ASSIGNMENT_SQL = """\
-- The assignment (exposure) source: one row per unit with its variant and
-- first-exposure timestamp. abkit persists this ONCE per run into
-- _ab_exposures; every metric query joins that cohort via the packaged macro.
SELECT
    user_id,
    variant,
    exposure_ts
FROM {{ data_database }}.example_ab_assignments
WHERE 1 = 1
  {{ ab_added_filters }}
"""

SEED_CLICKHOUSE = """\
-- Synthetic seed dataset for the example experiment (ClickHouse).
-- Load:  clickhouse-client --multiquery < seed/seed_dataset.clickhouse.sql
-- 600 users, 50/50 split, 14 experiment days + a 14-day pre-period
-- (the CUPED covariate window). Deterministic: reloading reproduces
-- identical numbers.

CREATE DATABASE IF NOT EXISTS analytics;

DROP TABLE IF EXISTS analytics.example_ab_assignments;
CREATE TABLE analytics.example_ab_assignments
(
    user_id String,
    variant String,
    exposure_ts DateTime('UTC')
)
ENGINE = MergeTree ORDER BY user_id;

INSERT INTO analytics.example_ab_assignments
SELECT
    concat('user_', toString(number))                    AS user_id,
    if(number % 2 = 0, 'control', 'treatment')           AS variant,
    toDateTime('2024-07-01 08:00:00', 'UTC')             AS exposure_ts
FROM numbers(600);

DROP TABLE IF EXISTS analytics.example_signup_events;
CREATE TABLE analytics.example_signup_events
(
    user_id String,
    event_date Date,
    event_time DateTime('UTC'),
    signed_up UInt8,
    gross_usd Float64
)
ENGINE = MergeTree ORDER BY (event_date, user_id);

-- Experiment period (2024-07-01 .. 2024-07-14), one row per user per day.
-- Conversion is per USER (20% of control vs 25% of treatment convert, each on
-- their k % 14-th day); treatment also spends ~15% more.
INSERT INTO analytics.example_signup_events
SELECT
    concat('user_', toString(number % 600))                          AS user_id,
    toDate('2024-07-01') + toIntervalDay(intDiv(number, 600))        AS event_date,
    toDateTime(toDate('2024-07-01') + toIntervalDay(intDiv(number, 600)), 'UTC')
        + toIntervalHour(12)                                         AS event_time,
    multiIf(
        (number % 600) % 2 = 0
            AND intDiv(number % 600, 2) % 5 = 0
            AND intDiv(number, 600) = intDiv(number % 600, 2) % 14, 1,
        (number % 600) % 2 = 1
            AND intDiv(number % 600, 2) % 4 = 0
            AND intDiv(number, 600) = intDiv(number % 600, 2) % 14, 1,
        0
    )                                                                AS signed_up,
    ((number % 600) % 7) * 1.5
        * if((number % 600) % 2 = 0, 1.0, 1.15)                      AS gross_usd
FROM numbers(600 * 14);

-- Pre-period (2024-06-17 .. 2024-06-30): the CUPED covariate source.
-- No treatment effect before the experiment, by construction.
INSERT INTO analytics.example_signup_events
SELECT
    concat('user_', toString(number % 600))                          AS user_id,
    toDate('2024-06-17') + toIntervalDay(intDiv(number, 600))        AS event_date,
    toDateTime(toDate('2024-06-17') + toIntervalDay(intDiv(number, 600)), 'UTC')
        + toIntervalHour(12)                                         AS event_time,
    0                                                                AS signed_up,
    ((number % 600) % 7) * 1.4                                       AS gross_usd
FROM numbers(600 * 14);
"""

GITIGNORE = """\
.env
*.pyc
__pycache__/
"""

README = """\
# {project_name}

An [ab-analysis-kit](https://abkit.pipelab.dev) project: declarative A/B
experiment analysis (YAML + SQL) over your warehouse.

## Quickstart (the runnable example)

1. Load the synthetic seed dataset (ClickHouse):

       clickhouse-client --multiquery < seed/seed_dataset.clickhouse.sql

2. Lint the configs (no database needed):

       abk run --steps validate

3. Run the example experiment:

       abk run --select example_signup_test

   This computes the full 14-point cumulative series for both metrics and
   writes it to `abkit_internal._ab_results` — the BI-friendly contract
   table. Re-running is idempotent (already-computed cutoffs are skipped).

4. Look at the numbers:

       SELECT metric, end_date, effect, pvalue, left_bound, right_bound
       FROM abkit_internal._ab_results
       WHERE experiment = 'example_signup_test'
       ORDER BY metric, end_ts

## Layout

- `abkit_project.yml` — project config (statistical defaults, limits)
- `profiles.yml` — database connections (secrets via `${{ENV_VAR}}`)
- `experiments/` — experiment definitions (the PRIMARY entity)
- `metrics/` — the reusable metric library (YAML + SQL)
- `sql/` — shared SQL files (assignment sources, metric queries)
- `seed/` — the synthetic example dataset
- `runners/` — orchestration examples: a Prefect flow (`prefect_flow.py`) + a
  Prefect 3 deployment (`prefect.yaml`, `prefect deploy --all`). Tag live
  experiments `actual` so the daily job recomputes them.

## Domain rules worth knowing

- Every metric query must be ONE ROW PER UNIT and join the cohort via the
  packaged macro (`{{{{ ab.exposed_units() }}}}`) — config-lint enforces it.
- Check SRM before trusting any effect: a red `SRM FAILED` line means the
  assignment is broken and effects are untrustworthy.
- The daily cumulative series is peeking-prone: watch it, but decide at the
  horizon (sequential methods arrive as an opt-in).
- Editing method params orphans the old result series (`abk clean` prunes).
"""

PREFECT_FLOW = '''\
"""Prefect orchestration example: recompute all live experiments daily.

The CLI is the unit of automation — a Prefect task simply shells out to
``abk run``; locks are self-healing for unattended runs and failures exit
non-zero. abkit itself never imports prefect; install it separately with
``pip install "ab-analysis-kit[orchestration]"`` (Prefect 3).

This flow selects ``tag:actual`` — experiments whose YAML ``tags:`` list contains
``actual``. TAG YOUR LIVE EXPERIMENTS ``actual`` so this picks them up (the
scaffolded example is tagged ``example`` on purpose, so the daily job does not
run the demo). Deploy it with the committed ``runners/prefect.yaml``:

    prefect deploy --all           # reads runners/prefect.yaml
    # or, ad hoc:
    prefect deploy runners/prefect_flow.py:abkit_daily --cron "0 6 * * *"
"""

import subprocess

from prefect import flow, task


@task(retries=1)
def abk_run() -> None:
    subprocess.run(["abk", "run", "--select", "tag:actual"], check=True)


@flow(name="abkit-daily")
def abkit_daily() -> None:
    abk_run()


if __name__ == "__main__":
    abkit_daily()
'''

# A committed Prefect 3 project-deployment config so a scheduled recompute is one
# `prefect deploy` away (cli-and-dx.md §3). Prefect is not imported by abkit and the
# scaffold cannot be CI-round-tripped like the YAML configs are, so the file pins the
# Prefect major it targets. The flow stays a thin `abk run` shell (version-robust).
PREFECT_YAML = """\
# prefect.yaml — Prefect 3 project deployment for {project_name}.
# Targets Prefect 3 (`pip install "ab-analysis-kit[orchestration]"`). The
# `abk` CLI is the unit of automation; this only schedules `abk run`.
#   prefect deploy --all
name: {project_name}
prefect-version: 3.0.0

deployments:
  - name: abkit-daily
    entrypoint: runners/prefect_flow.py:abkit_daily
    description: Recompute all experiments tagged `actual`, daily at 06:00 UTC.
    # Tag your LIVE experiments `actual` (the scaffolded example is `example`, so
    # this job skips the demo). Adjust the cron / work pool to your infra.
    schedules:
      - cron: "0 6 * * *"
        timezone: "UTC"
    work_pool:
      name: default-process-pool
"""

PROFILES_BY_DB = {
    "clickhouse": PROFILES_CLICKHOUSE,
    "postgres": PROFILES_POSTGRES,
    "mysql": PROFILES_MYSQL,
}


def run_init(project_name: str, target_dir: str, db_type: str = "clickhouse") -> None:
    root = Path(target_dir) / project_name
    if root.exists():
        raise click.ClickException(f"directory already exists: {root} (init refuses to overwrite)")

    files: dict[str, str] = {
        "abkit_project.yml": PROJECT_YML.format(project_name=project_name),
        "profiles.yml": PROFILES_BY_DB[db_type],
        ".gitignore": GITIGNORE,
        "README.md": README.format(project_name=project_name),
        "experiments/example_signup_test.yml": EXPERIMENT_YML,
        "metrics/example_signup_cr.yml": SIGNUP_CR_YML,
        "metrics/example_arpu.yml": ARPU_YML,
        "sql/example_assignment.sql": ASSIGNMENT_SQL,
        "seed/seed_dataset.clickhouse.sql": SEED_CLICKHOUSE,
        "runners/prefect_flow.py": PREFECT_FLOW,
        "runners/prefect.yaml": PREFECT_YAML.format(project_name=project_name),
    }

    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    # Round-trip: every scaffolded artifact through the REAL config classes +
    # the level-2 validator (macro lint, method instantiation, looks gates) —
    # the scaffold cannot drift from the code.
    from abkit.config import (
        ProfilesConfig,
        ProjectConfig,
        validate_level2,
        validate_project_configs,
    )

    try:
        project = ProjectConfig.from_yaml_file(root / "abkit_project.yml")
        ProfilesConfig.from_yaml(root / "profiles.yml")
        experiments, metrics = validate_project_configs(root, project)
        report = validate_level2(root, project, experiments, metrics)
        if not report.ok:
            raise ValueError("; ".join(report.errors))
    except Exception as exc:  # pragma: no cover - a scaffold bug, not user error
        raise click.ClickException(
            f"scaffold failed its own validation (this is an abkit bug): {exc}"
        ) from exc

    echo_tree(f"{project_name}/", list(files))
    echo_done(
        f"Project '{project_name}' created ({db_type}). Next:\n"
        f"  1. cd {project_name}\n"
        "  2. load the seed dataset (see README.md)\n"
        "  3. abk run --select example_signup_test"
    )
