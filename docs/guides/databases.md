# Databases

abkit runs your experiment analysis **inside your warehouse**: it reads your
fact tables where they already live and writes its own state to a small set of
internal `_ab_*` tables. Three backends are supported — **ClickHouse** (the
first-class target), **PostgreSQL**, and **MySQL** — and every one of them is
driven through the same generic, dialect-neutral manager. You pick a backend by
writing a **profile** in `profiles.yml`; nothing in your experiment or metric
YAML changes when you switch.

This page is the per-backend reference: how to connect, what each profile needs,
and the dialect specifics worth knowing. For the shared `profiles.yml` shape and
the full field table, see [Configuration](configuration.md); for how metric SQL
references a database, see [Metrics](metrics.md).

## Install the driver

The database drivers are optional extras — install only the one you use:

```bash
pip install "ab-analysis-kit[clickhouse]"   # clickhouse-driver
pip install "ab-analysis-kit[postgres]"     # psycopg2-binary
pip install "ab-analysis-kit[mysql]"        # pymysql
pip install "ab-analysis-kit[all-db]"       # all three drivers
```

Configs parse without a driver installed — `abk run --steps validate` is a pure
config lint and touches no database — but the first command that opens a
connection raises a clear `ImportError` naming the extra to install if the
driver is missing.

## The internal / data split

Every backend keeps two locations apart:

- **the data location** — where *your* fact tables live and where metric and
  assignment SQL reads from (referenced in SQL as `{{ data_database }}`);
- **the internal location** — where abkit's own `_ab_*` state tables live
  (`{{ internal_database }}`).

Keep them separate so the `_ab_*` tables never clutter a shared analytics
schema. On ClickHouse and MySQL each location is a **database**; on PostgreSQL
each is a **schema** inside one connected database. That single difference is the
reason the profile keys differ per backend (below).

## ClickHouse (first-class)

ClickHouse is the primary target. Connect over the **native protocol** (default
port `9000`, not the HTTP `8123`):

```yaml
default_profile: dev

profiles:
  dev:
    type: clickhouse
    host: localhost
    port: 9000                          # native protocol
    user: default                       # optional (default "default")
    password: ""                        # optional (default "")
    internal_database: abkit_internal   # required — the _ab_* tables live here
    data_database: analytics            # required — your fact tables (queries read here)
    settings:                           # optional — ClickHouse server settings
      max_execution_time: 600
      max_memory_usage: 10000000000

  prod:
    type: clickhouse
    host: "{{ env_var('ABKIT_CH_HOST') }}"
    port: 9000
    user: "{{ env_var('ABKIT_CH_USER') }}"
    password: "{{ env_var('ABKIT_CH_PASSWORD') }}"
    internal_database: abkit_internal
    data_database: analytics
```

**Required:** `type: clickhouse`, `port`, `internal_database`, `data_database`.
You do **not** set a connection-level `database` field for ClickHouse — it is
ignored for this backend; the two locations are named by `internal_database` and
`data_database`. `settings` is a dict of ClickHouse settings applied to the
client connection (e.g. execution/memory limits).

Dialect notes:

- abkit **auto-creates** both the internal and data databases if they don't
  exist (`CREATE DATABASE IF NOT EXISTS`) on first connect.
- The versioned `_ab_*` tables use `ReplacingMergeTree`, which can hold transient
  duplicate primary keys until a background merge runs. abkit's
  correctness-sensitive reads append `FINAL` so you always see the collapsed
  latest version — you never need to add it yourself.
- ClickHouse has no atomic upsert, so the run lock is **advisory**: abkit uses a
  synchronous delete-then-insert with a deterministic read-back tie-break. Prefer
  one scheduler per project and keep clocks NTP-synced. (PostgreSQL and MySQL
  locks are single-statement atomic — see [Concurrency](#concurrency-and-locking).)

## PostgreSQL

PostgreSQL connects to one `database` and stores tables in two **schemas** inside
it. The database must **already exist**; abkit creates the schemas.

```yaml
default_profile: dev

profiles:
  prod:
    type: postgres
    host: localhost
    port: 5432
    user: postgres
    password: "{{ env_var('ABKIT_PG_PASSWORD') }}"
    database: analytics          # required — the database to connect to (must exist)
    internal_schema: abkit       # required — the _ab_* tables
    data_schema: public          # required — data queries read here
    settings: {}                 # optional — extra psycopg2.connect kwargs
```

**Required:** `type: postgres`, `port`, `database`, `internal_schema`,
`data_schema`. If `database` is omitted the profile fails fast with a clear
error. `settings` is passed through as extra keyword arguments to
`psycopg2.connect` (e.g. `sslmode`, `connect_timeout`).

Dialect notes:

- abkit runs `CREATE SCHEMA IF NOT EXISTS` for both schemas, but it does **not**
  create the database — provision that yourself first.
- `ReplacingMergeTree` dedup is reproduced with an enforced primary key plus a
  version-aware `INSERT ... ON CONFLICT (pk) DO UPDATE` (last-writer-wins by the
  version column), so there are never duplicate rows and no `FINAL` equivalent is
  needed.
- Timestamp columns are `TIMESTAMP(3)` (millisecond precision).

## MySQL

MySQL (8.0.19+ is the supported floor) has no schema-vs-database distinction, so
both locations are real **databases**:

```yaml
default_profile: dev

profiles:
  prod:
    type: mysql
    host: localhost
    port: 3306
    user: root
    password: "{{ env_var('ABKIT_MYSQL_PASSWORD') }}"
    internal_database: abkit     # required — the _ab_* tables
    data_database: analytics     # required — data queries read here
    database: analytics          # optional — default database for the connection
    settings: {}                 # optional — extra pymysql.connect kwargs
```

**Required:** `type: mysql`, `port`, `internal_database`, `data_database`. The
`database` key is optional (a default database for the raw connection).
`settings` is passed through as extra keyword arguments to `pymysql.connect`.

Dialect notes:

- abkit runs `CREATE DATABASE IF NOT EXISTS` for both databases.
- Dedup uses an enforced primary key plus a version-aware row-alias
  `INSERT ... ON DUPLICATE KEY UPDATE` (MySQL 8.0.19+).
- MySQL cannot index a `TEXT` column in a primary key without a prefix length, so
  string columns that are part of a primary key are rendered `VARCHAR(255)` while
  the rest stay `TEXT`. Composite `_ab_*` keys are sized to fit InnoDB's
  3072-byte index cap. Timestamp columns are `DATETIME(3)`.

## The generic manager

abkit talks to every backend through one database-agnostic manager interface.
Its operations are **`table_name`-keyed and never special-case a table**:
`execute_query`, `create_table` (from an abstract table model), `insert_batch`,
`upsert_record`, `delete_rows`, `get_max_timestamp`, and the atomic
`try_acquire_lock`. Each backend subclass renders those into its own SQL dialect
(the `_TYPE_MAP`, the conflict clause, the lock statement). The `_ab_*` table
shapes and semantics live one layer up and are identical across backends, which
is why the same experiment produces the same results contract on ClickHouse,
PostgreSQL, or MySQL.

The internal state, in the internal location (architecture §6):

| Table | Role |
|---|---|
| `_ab_experiments` | experiment catalog (name, dates, status) |
| `_ab_exposures` | per-unit assignment (unit, variant, exposure_ts, stratum); the SRM source — **read-only**, loaded from your assignment SQL |
| `_ab_unit_state` | cumulative per-unit moments (the scalability substrate) |
| `_ab_results` | the clean BI contract — one cumulative row per (experiment, metric, pair, method, cutoff) |
| `_ab_aa_runs` | `abk validate` A/A audit (FPR, power, peeking-FPR, verdict) |
| `_ab_tasks` | run/validate locks + idempotency |

You never create or migrate these by hand — abkit creates them on first run and
prunes stale rows with `abk clean`.

## Referencing your data location in SQL

In metric and assignment SQL, qualify tables with the location built-ins rather
than hard-coding a database name, so the same SQL works across profiles
(declarative-config §5):

```sql
{% import 'abkit_assignment.jinja' as ab %}
SELECT {{ ab.variant_col() }}  AS variant
     , user_id
     , amount
FROM {{ data_database }}.orders
{{ ab.exposed_units() }}
```

`{{ data_database }}` resolves to the active profile's **data location** and
`{{ internal_database }}` to its internal location. These are the built-in names
on **every** backend — on PostgreSQL `{{ data_database }}` resolves to the
`data_schema` you configured (the profile *key* is `data_schema`, but the SQL
built-in is still `{{ data_database }}`). Both are rendered under
`StrictUndefined`, so a typo fails the render loudly instead of producing broken
SQL. See [Metrics](metrics.md) for the `ab.exposed_units(...)` assignment macro
and the full built-ins list.

## Selecting a profile at runtime

Every command that hits the database (`run`, `explore`, `validate`, `plan`,
`unlock`, `clean`) accepts `--profile`:

```bash
abk run --select example_signup_test --profile prod
```

Without `--profile`, abkit uses the `default_profile` declared at the top of
`profiles.yml`. (Note: `abkit_project.yml` also carries a `default_profile`, but
the one that selects your connection at runtime is the **`profiles.yml`** one —
keep them in sync.)

Scaffold a project pre-wired for a backend with `abk init`:

```bash
abk init my_project --db-type postgres    # clickhouse | postgres | mysql (default clickhouse)
```

This writes a `profiles.yml` (with a `dev` and an env-var-driven `prod` profile)
and a seed dataset for the chosen backend, so `abk run --select
example_signup_test` produces real results on a fresh machine.

## Keeping secrets out of YAML

Any string in `profiles.yml` may contain an environment-variable placeholder, in
either of two syntaxes:

```yaml
password: "{{ env_var('ABKIT_PG_PASSWORD') }}"   # dbt-style
host: "${ABKIT_PG_HOST}"                          # shell-style
```

Placeholders are resolved when the file loads. An **unset** variable is left
unresolved rather than silently blanked, so a missing secret surfaces as a
connection failure you can diagnose — it never quietly connects with an empty
password. Keep real credentials in the environment (or your orchestrator's
secret store), not in the committed YAML.

## Concurrency and locking

abkit serializes pipeline work per experiment with a lock row in `_ab_tasks`:

- **PostgreSQL / MySQL** — the claim is a single atomic statement
  (`ON CONFLICT` / `ON DUPLICATE KEY UPDATE`), so two concurrent runs can never
  both win.
- **ClickHouse** — the claim is **advisory** (ClickHouse has no atomic upsert),
  using a synchronous delete-insert-then-read-back with a deterministic
  tie-break. This is correct for a normal single-scheduler setup; avoid running
  the same experiment from two schedulers at once, and keep host clocks synced.

A stale lock left by a crashed process is reclaimed automatically after the
compute timeout. To clear one manually:

```bash
abk unlock --select <experiment>
```

## See also

- [Configuration](configuration.md) — the full `profiles.yml` and
  `abkit_project.yml` field reference.
- [Metrics](metrics.md) — the `ab.exposed_units(...)` macro and SQL built-ins.
- [Experiments](experiments.md) — the experiment YAML that references your metrics.
