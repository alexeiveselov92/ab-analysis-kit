# Configuration

An abkit project is a directory of declarative files — no Python required to run
an experiment. Two files live at the project root and configure the whole
project; the rest are the reusable library of experiments and metrics you author.

| File | Role |
|---|---|
| `abkit_project.yml` | Project settings + statistical defaults |
| `profiles.yml` | Database connections (secrets via env vars) |
| `experiments/<name>.yml` | The **primary entity** — one experiment per file |
| `metrics/<name>.yml` (+ `sql/`) | The reusable metric library, referenced by experiments |

A directory becomes an abkit project the moment it contains `abkit_project.yml`.
`abk init <name> --db-type {clickhouse,postgres,mysql}` scaffolds all of the
above with a runnable seed example (ClickHouse is the default backend). This page
covers the two project-level files and how experiments and metrics are
discovered; for the experiment and metric YAML themselves see the
[experiments](experiments.md) and [metrics](metrics.md) guides.

## Project layout

`abk init` writes a tree like this (paths are configurable — see
`paths` below):

```
my_ab_project/
  abkit_project.yml     # project config
  profiles.yml          # database connections
  experiments/          # one experiment YAML per file (the primary entity)
  metrics/              # the reusable metric library (YAML + SQL)
  sql/                  # SQL referenced by `query_file:`
```

Every command finds the project by walking **up** from the current directory
looking for `abkit_project.yml` (up to 10 levels), so you can run `abk` from any
subdirectory of the project.

## `abkit_project.yml`

The only two required keys are `name` and `default_profile`; everything else has
a default, so the minimal file `abk init` writes is essentially those two lines
with the rest commented. The fields below are the authoritative pydantic model
(`abkit/config/project_config.py`).

```yaml
name: my_ab_project              # required — identifier (alphanumeric / _ / - / space)
version: "1.0"                   # optional (default "1.0")
default_profile: dev             # required — a profile name from profiles.yml

paths:                           # optional — directory names, relative to the project root
  experiments: experiments       # experiment YAMLs (default: experiments)
  metrics: metrics               # metric YAMLs (default: metrics)
  sql: sql                       # query_file SQL (default: sql)

statistics:                      # project-wide defaults; an experiment overrides any of these
  alpha: 0.05                    # significance level, a fraction in (0,1) (default 0.05)
  test_type: relative            # relative | absolute (default relative)
  correction: bonferroni         # none | bonferroni | benjamini_hochberg (default bonferroni)
  power: 0.8                     # target power for MDE / `abk plan` (0,1) (default 0.8)
  aa_fpr_budget: null            # optional — fraction in (0,1] the `abk validate` matrix colours against

limits:                          # look-count & small-sample gates
  max_looks: 5000                # planned cutoffs above this = a config error (the one hard cadence gate)
  warn_looks: 100                # looks above this WITHOUT sequential = peeking warning
  min_units_per_arm: 100         # below this a row is demoted to insufficient_data (written, inference withheld)

timeouts:                        # per-step, in seconds (each 1..86400)
  load: 3600                     # load step (default 3600)
  compute: 7200                  # compute step; also the run-lock staleness threshold (default 7200)

compute:
  mode: recompute                # v1 ships full-window recompute only — the only accepted value today
```

### The `statistics` block

These are project-wide defaults; the corresponding fields on an experiment
override them. Nothing in this block enters a method's `method_config_id`, so
changing `alpha`, `correction`, or `power` never orphans a results series
(declarative-config §7).

| Field | Meaning |
|---|---|
| `alpha` | Experiment-level significance, **pre-correction**. The per-comparison post-correction alpha is *derived* (see below), never set here. Must be in `(0,1)`. |
| `test_type` | `relative` (percent lift) or `absolute` (raw difference) — the units the persisted `effect` and any `min_effect` live in. |
| `correction` | Multiple-testing correction across a comparison family: `bonferroni` (the config-time two-tier scheme), `benjamini_hochberg` (read-time FDR across the experiment's metrics), or `none`. |
| `power` | Target power for MDE reporting and `abk plan` sizing. Must be in `(0,1)`. |
| `aa_fpr_budget` | Tuning-only band for the `abk validate` matrix: a fraction in `(0,1]`; a cell whose measured false-positive rate exceeds it colours red. A per-metric `aa_fpr_budget` overrides it (declarative-config §8). Never touches the pipeline math. |

**Why the effective alpha isn't the alpha you set.** With `correction:
bonferroni`, abkit applies the legacy two-tier scheme keyed off each comparison's
`is_main_metric` flag: the main metric gets `alpha / C` (where `C` is the number
of pairwise comparisons), and secondary metrics split a stricter
`alpha / (C × n_secondary)` budget (declarative-config §6). Do not compute this
by hand — `abk run`, `abk validate`, `abk plan`, and the HTML report all **echo**
the effective per-comparison alpha and the `C × metrics` divisor, and all four use
the same resolver so an A/A cell calibrated for a metric matches what the pipeline
actually applied.

### The `limits` block (cadence & small-sample gates)

An experiment's `cadence` enumerates the cumulative cutoffs ("looks") over the
window. These gates bound that (declarative-config §8, cumulative-intervals §6):

- `max_looks` — the single **hard** gate. A plan that would produce more looks
  than this is a config error at validate time. (There is deliberately no
  minimum-interval floor.)
- `warn_looks` — softer: past it, an experiment **without** `sequential.enabled`
  gets a peeking warning, because fixed-horizon CIs are not valid under repeated
  looks. Turn on sequential CIs to silence it legitimately.
- `min_units_per_arm` — the small-sample floor. A cutoff with fewer units per arm
  is still written to `_ab_results`, but demoted to `insufficient_data` with
  inference withheld.

### The `tables` block

The six internal tables (`_ab_experiments`, `_ab_exposures`, `_ab_unit_state`,
`_ab_results`, `_ab_aa_runs`, `_ab_tasks`) have a `tables:` block for forward
compatibility, but it **rejects any override today** — the `_ab_*` names are
canonical. You do not need to set it.

## `profiles.yml`

Connections live in `profiles.yml`, keyed by name, with a top-level
`default_profile` selecting one. ClickHouse, PostgreSQL, and MySQL are all
supported. The connection fields are the `ProfileConfig` model
(`abkit/config/profile.py`).

> **Two locations, always separate.** abkit reads your fact tables from a **data**
> location and writes its own `_ab_*` state to an **internal** location. Keep them
> apart so the internal tables don't clutter shared analytics schemas. ClickHouse
> and MySQL name these as two *databases* (`internal_database` / `data_database`);
> PostgreSQL connects to one `database` and names two *schemas*
> (`internal_schema` / `data_schema`).

**ClickHouse** (native protocol; no `database:` field):

```yaml
default_profile: dev

profiles:
  dev:
    type: clickhouse
    host: localhost
    port: 9000
    user: default                       # optional (default "default")
    password: ""                        # optional (default "")
    internal_database: abkit_internal   # required — where the _ab_* tables live
    data_database: analytics            # required — your fact tables (queries read here)
    settings:                           # optional — extra ClickHouse settings
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

**PostgreSQL** (connect to one `database`; internal/data are schemas inside it):

```yaml
profiles:
  prod:
    type: postgres
    host: localhost
    port: 5432
    database: analytics                 # required — the database to connect to
    user: postgres
    password: "{{ env_var('ABKIT_PG_PASSWORD') }}"
    internal_schema: abkit              # required — the _ab_* tables
    data_schema: public                 # required — data queries read here
    settings: {}                        # optional — extra psycopg2.connect kwargs
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
    internal_database: abkit            # required — the _ab_* tables
    data_database: analytics            # required — data queries read here
    database: analytics                 # optional — default db for the connection
    settings: {}                        # optional — extra pymysql.connect kwargs
```

### Connection field reference

| Field | Applies to | Meaning |
|---|---|---|
| `type` | required, all | `clickhouse` \| `postgres` \| `mysql` |
| `host` | all | default `localhost` |
| `port` | required, all | integer 1–65535 |
| `user` | all | default `default` |
| `password` | all | default empty; put secrets behind an env var |
| `database` | **required PostgreSQL**, optional MySQL, unused ClickHouse | the database to connect to |
| `internal_database` | ClickHouse / MySQL | where the `_ab_*` tables live |
| `internal_schema` | PostgreSQL | schema for the `_ab_*` tables |
| `data_database` | ClickHouse / MySQL | where metric/assignment SQL reads from |
| `data_schema` | PostgreSQL | schema for data queries |
| `settings` | all | extra backend driver kwargs (a mapping) |

Select a profile per command with `abk run --profile prod` (also on `explore`,
`validate`, `plan`, `unlock`, `clean`); without it, `abk` uses `profiles.yml`'s
own `default_profile`.

> **Two `default_profile` keys.** Both `abkit_project.yml` and `profiles.yml`
> carry a `default_profile`. At runtime the **`profiles.yml`** one selects the
> connection when you omit `--profile`; the project-file one is not read for
> connection selection. Keep them in sync to avoid confusion.

## Keeping secrets out of YAML

Any string value in `abkit_project.yml` or `profiles.yml` may contain an
environment-variable placeholder, resolved before the config is validated
(`abkit/utils/env_interpolation.py`). Two syntaxes are accepted:

- `{{ env_var('VAR_NAME') }}` — dbt-style.
- `${VAR_NAME}` — shell-style.

Interpolation walks nested mappings and lists, so it works anywhere in the file.
An **unresolved** placeholder (the variable is not set) is left intact rather than
replaced with an empty string — so a missing secret fails loudly (an invalid port
or a refused connection) instead of silently connecting with blank credentials.
Export the variables before running:

```bash
export ABKIT_CH_PASSWORD='…'
abk run --profile prod --select my_experiment
```

## How experiments and metrics are discovered

Experiments and metrics are selected with two **separate flags** — `--select`
for experiments, `--metric` for a comparison — but their names live in **one
shared namespace**. Files are discovered recursively under the
`paths.experiments` and `paths.metrics` directories; a hidden `.history/`
subdirectory (where `abk explore` archives pre-tune versions) is always excluded.
Every name must be **globally unique across the whole project** — names are
database keys, and the validator rejects a duplicate, including an experiment and
a metric that share a name (declarative-config §8).

`abk run` (and `explore`, `validate`, `plan`, `clean`) selects **experiments**
with `--select` (`abk run` additionally supports `--exclude`), which accept four
selector forms:

| Selector | Matches |
|---|---|
| `signup_test` | an experiment by file name, then by its `name:` field |
| `experiments/growth/*.yml` | a path glob (relative to the project root) |
| `tag:actual` | every experiment whose YAML `tags:` list contains `actual` |
| `*` | everything (also the default when no `--select` is given) |

`--select` resolves the **experiment** namespace only. Commands that operate on a
single comparison (`explore`, `validate`, `plan`) narrow to one metric with a
separate `--metric` flag. A selector that matches nothing produces a warning that
reminds you of this split.

Tags are the idiom for orchestration: tag your live experiments (e.g. `actual`)
and schedule `abk run --select tag:actual`. The scaffolded example is tagged
`example` on purpose so a daily job doesn't pick it up.

## Validate the config without a database

Every config change should round-trip through the validator before you touch the
warehouse. `abk run --steps validate` runs the full parse, cross-reference
resolution, method-param checks, cadence/looks gates, and a StrictUndefined SQL
render smoke-test **without connecting to any database** — safe to run in CI
(declarative-config §8):

```bash
abk run --steps validate                # config-lint only, no DB
abk run --select my_experiment          # the real pipeline (validate → plan → load → compute)
```

Note that `abk run --steps validate` is a *config lint* — it is unrelated to the
`abk validate` command, which runs the A/A false-positive matrix against live
data. The CLI exits non-zero on any failure, so both are cron/Prefect-safe.

## See also

- [Experiments](experiments.md) — the experiment YAML: variants, cadence,
  comparisons, sequential, readout knobs.
- [Metrics](metrics.md) — metric YAML, the `ab.exposed_units(...)` assignment
  macro, and the Jinja built-ins (`ab_start_date`, `ab_start_ts`, `ab_end_ts`,
  `data_database`, …).
- The pydantic models are the last word on field names:
  `abkit/config/project_config.py` and `abkit/config/profile.py`.
