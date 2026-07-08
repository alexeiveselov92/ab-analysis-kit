---
name: abk-setup-project
description: >-
  Configure an abkit project's database connection in profiles.yml so `abk run`
  works end to end. Use for first-time setup, right after `abk init`, when the
  user asks to connect abkit to their database or set up profiles.yml, or when a
  run fails with "internal_database must be set", "data_database must be set",
  "Profiles file not found", "default_profile '<x>' not found", or "Connection
  refused". Interactive and database-type-aware.
---

# Set up an abkit project (database connection)

Turn the placeholder `profiles.yml` that `abk init` writes into a **runnable**
connection, branching on the database backend, verify it, then prove the whole
pipeline by running the scaffolded example. Do not invent hosts, ports or
databases — gather them from the user. For field detail read
`.claude/rules/ab-analysis-kit/project.md`; this skill is the procedure, that
file is the reference.

## What this skill does (read first)

`abk init` ships more than a stub: a `profiles.yml` whose `dev` profile is a
**placeholder** (`host: localhost`, empty password, `internal_database:
abkit_internal`, `data_database: analytics`), **plus** a fully runnable example
(`experiments/example_signup_test.yml`, two metrics, an assignment SQL, and a
synthetic seed dataset under `seed/`). The placeholder validates but almost
never matches a real warehouse. Your job: point the profile at the user's real
database, verify connectivity, then run the example to prove the setup — end to
end, on real infrastructure.

## Step 0 — Locate or create the project

A project root contains `abkit_project.yml` and `profiles.yml`. Find it (or the
nearest ancestor that has it). If there is none, ask for a project name and run
`abk init <name>` (add `--db-type {clickhouse,postgres,mysql}`), then work
inside that folder. If several projects sit side by side, ask which one.

> **Recommended (optional): give _yourself_ read access to the database.** abkit
> connects to the warehouse directly via its drivers and never needs an MCP to
> run — the `profiles.yml` connection below is all the *tool* needs. But you
> assist far better with read access to the same DB (e.g. a database MCP): you
> can confirm a metric SQL returns one row per unit, inspect arm sizes before
> worrying about SRM, and read `_ab_results` / `_ab_aa_runs` to explain a
> verdict. Suggest it; if the user can't, fall back to `abk explore` and to
> asking them to run queries.

## Step 1 — Pick the database backend

**ClickHouse, PostgreSQL and MySQL are all fully supported.** Ask which one the
project uses (default to ClickHouse if unsure). `abk init --db-type
{clickhouse,postgres,mysql}` scaffolds `profiles.yml` for the chosen backend.
The location fields differ:

- **ClickHouse** / **MySQL** — two *databases*: `internal_database` /
  `data_database`.
- **PostgreSQL** — connect to a `database` (must already exist), then two
  *schemas*: `internal_schema` / `data_schema`.

The metric-query SQL dialect also differs (`toStartOfInterval` on ClickHouse
vs `date_trunc` on Postgres vs `FROM_UNIXTIME` on MySQL). Everything else — the
methods, the CLI, the readout — is identical.

## Step 2 — Connection details (gather, don't guess)

Ask for, and confirm:

- `host` — e.g. `localhost`, `clickhouse.internal`.
- `port` — **native protocol** port. ClickHouse `9000` (TLS `9440`) — **not**
  the HTTP port `8123`. Postgres `5432`. MySQL `3306`.
- `user` — CH `default`, PG `postgres`, MySQL `root` by default.
- `password` — default empty.

**Keep secrets out of YAML.** For passwords (and remote hosts) use environment
interpolation: `password: "{{ env_var('ABKIT_CH_PASSWORD') }}"` (`${VAR}` also
works). `profiles.yml` resolves these on load; an *unresolved* placeholder is
kept as the literal string, so a missing variable fails at connect time, not at
load — remind the user to export it before running. The scaffolded `prod`
profile already models this pattern.

## Step 3 — Internal vs data location (both required)

Point **both** at the user's real databases (the placeholder ships examples):

- `internal_database` — a dedicated database for abkit's own `_ab_*` tables
  (`_ab_results`, `_ab_exposures`, `_ab_tasks`, `_ab_aa_runs`), created
  automatically on first run. Keep it **separate** from analytics data, e.g.
  `abkit_internal`.
- `data_database` — where the source/fact tables your metric queries read from
  live (and where the seed dataset loads).

For Postgres these are `internal_schema` / `data_schema` (inside the connected
`database`); for MySQL they are `internal_database` / `data_database`.

## Step 4 — Profile name & `default_profile`

`abk run` (with no `--profile`) uses **`profiles.yml`'s `default_profile`**, so
it must name a profile that exists in the same file (validated —
`default_profile '<x>' not found` otherwise). Replace or keep the scaffolded
`dev`/`prod` profiles, point `default_profile` at the one you configured, and set
`default_profile` in `abkit_project.yml` to the same value (it isn't read at
runtime, but matching avoids confusion).

A clean result:

```yaml
# profiles.yml
default_profile: prod

profiles:
  prod:
    type: clickhouse
    host: "{{ env_var('ABKIT_CH_HOST') }}"
    port: 9000
    user: "{{ env_var('ABKIT_CH_USER') }}"
    password: "{{ env_var('ABKIT_CH_PASSWORD') }}"
    internal_database: abkit_internal   # the _ab_* tables live here
    data_database: analytics            # where your source tables live
```

## Step 5 — Validate the config (no database needed)

Config-lint first — it round-trips every experiment + metric through the real
validator (macro import, one-row-per-unit shape, method instantiation, cadence
gates) and needs no connection:

```bash
abk run --steps validate      # 'validate' alone = config lint, no DB
```

Fix any reported error before touching the warehouse. (This is the config lint —
it is **not** `abk validate`, which is the A/A false-positive matrix and needs
data.)

## Step 6 — Verify connectivity, then prove the pipeline

The `abk init` example is designed to run end to end. First load the synthetic
seed into `data_database` (the exact command is in the project `README.md`), e.g.
for ClickHouse:

```bash
clickhouse-client --multiquery < seed/seed_dataset.clickhouse.sql
```

Then check the connection with a non-destructive load-only run (connects,
creates the `_ab_*` internal tables, loads the cohort — no compute):

```bash
abk run --select example_signup_test --steps load
```

Finally run the full example to prove compute + readout write real numbers:

```bash
abk run --select example_signup_test
```

This computes the 14-point cumulative series for both metrics into
`abkit_internal._ab_results` (the stable BI contract table). Re-running is
idempotent — already-computed cutoffs are skipped. Add `--report` for a
self-contained HTML readout. The CLI exits **non-zero** on any failure.

## Step 7 — Interpret failures

- `internal_database must be set` / `data_database must be set` (CH/MySQL) or
  `internal_schema` / `data_schema` (PG) → a Step 3 location field is blank or
  missing. ClickHouse has no connect-target `database:` key (it uses
  `internal_database`/`data_database`); for MySQL `database:` is an optional
  connect-target and for Postgres it is required, but the location fields above
  are still needed.
- `default_profile '<x>' not found` → Step 4: it names a profile that isn't
  defined in `profiles.yml`.
- `Profiles file not found` → run from inside the project root (or pass the
  right directory).
- `Connection refused` / timeout → host/port wrong or DB unreachable; check the
  **native** port (not `8123`).
- unresolved `{{ env_var('…') }}` → the variable isn't exported in the shell.
- example query errors on real tables → expected once you retarget
  `data_database`; the seed loads into it, or the user replaces the example.

## Step 8 — Final checklist

- [ ] The active profile has `type`, `host`, `port`, and its location fields —
      CH/MySQL: `internal_database` + `data_database`; PG: `database` +
      `internal_schema` + `data_schema`.
- [ ] Secrets use `env_var`/`${VAR}`, and the user knows which vars to export.
- [ ] `profiles.yml`'s `default_profile` names an existing profile (and
      `abkit_project.yml` matches).
- [ ] `abk run --steps validate` passes (config is clean).
- [ ] `abk run --select example_signup_test` writes rows to
      `abkit_internal._ab_results`.

Then point the user at the **`abk-new-metric`** skill to scaffold their first
real metric, the **`abk-new-experiment`** skill to define an experiment over it,
and **`abk plan`** to size it before launch.
