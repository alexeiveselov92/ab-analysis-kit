# Installation

abkit ships as a single Python package. Installing it gives you three things:

| Name | What it is |
|---|---|
| `ab-analysis-kit` | the **pip package** you install |
| `abkit` | the **import package** (`import abkit`, e.g. the pure `abkit.stats` core) |
| `abk` | the **terminal command** (`abk init`, `abk run`, `abk explore`, …) |

## Requirements

- **Python 3.10 or newer** (3.10, 3.11, and 3.12 are tested).
- A SQL warehouse to analyze against — **ClickHouse** (the first-class target),
  **PostgreSQL**, or **MySQL 8.0.19+**. The database driver is an optional extra
  (see [Database drivers](#database-drivers) below) so abkit stays warehouse-agnostic
  and never forces a driver you don't use.

You can install and use the pure statistical core (`abkit.stats`) and the
config-lint path (`abk run --steps validate`) with **no** database at all — a
driver is only needed once you point abkit at a real warehouse.

## Install

Work inside a virtual environment so abkit and its dependencies stay isolated from
your system Python:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install ab-analysis-kit
```

That pulls in the runtime core automatically — numpy, scipy, statsmodels
(the stats engine), pydantic + pyyaml (config), jinja2 (metric SQL templating),
click (the CLI), plus orjson and requests. **No** database driver is installed by
the base package.

> **Prefer a global CLI?** If you only want the `abk` command available everywhere
> (not to `import abkit` inside another project), [`pipx`](https://pipx.pypa.io)
> works too: `pipx install ab-analysis-kit`. Add extras the same way, e.g.
> `pipx install "ab-analysis-kit[all-db]"`.

## Database drivers

Pick the extra that matches your warehouse. The extra names come straight from the
package's `[project.optional-dependencies]`:

| Install command | Extra | Driver it adds |
|---|---|---|
| `pip install "ab-analysis-kit[clickhouse]"` | `clickhouse` | `clickhouse-driver` |
| `pip install "ab-analysis-kit[postgres]"` | `postgres` | `psycopg2-binary` |
| `pip install "ab-analysis-kit[mysql]"` | `mysql` | `pymysql` |
| `pip install "ab-analysis-kit[all-db]"` | `all-db` | all three: `clickhouse-driver`, `psycopg2-binary`, `pymysql` |

The PostgreSQL extra uses `psycopg2-binary`, so you don't need a C compiler or
`libpq` headers to install it.

> Keep the quotes around `"ab-analysis-kit[...]"` — some shells (zsh) treat the
> square brackets as globbing otherwise.

You only need the driver for the backend you actually connect to. If you're not
sure which to pick yet, `[all-db]` installs all three and lets you switch backends
by editing `profiles.yml` later.

## Other extras

| Install command | Extra | When you want it |
|---|---|---|
| `pip install "ab-analysis-kit[orchestration]"` | `orchestration` | run abkit on a schedule with **Prefect 3** (abkit never imports Prefect itself — the CLI is the unit of automation). |
| `pip install "ab-analysis-kit[all]"` | `all` | every runtime extra: all three DB drivers **plus** Prefect. |
| `pip install "ab-analysis-kit[dev]"` | `dev` | contributing to abkit — pytest, coverage/mock plugins, and pinned `black`/`mypy`/`ruff`. |
| `pip install "ab-analysis-kit[integration]"` | `integration` | run the Docker-backed integration tests (`testcontainers` for ClickHouse/PostgreSQL/MySQL). |

Extras combine — separate them with commas, e.g.
`pip install "ab-analysis-kit[clickhouse,orchestration]"`.

## Verify the install

Check that the `abk` command is on your `PATH` and reports a version:

```bash
abk --version
```

```text
abk, version 0.1.0
```

List the available commands to confirm the full surface installed cleanly:

```bash
abk --help
```

You should see the shipped commands: `init`, `init-claude`, `run`, `explore`,
`validate`, `plan`, `unlock`, and `clean`. Every command runs its body lazily, so
`abk --version` and `abk --help` work instantly and require **no** database driver.

If `abk` isn't found, your virtual environment probably isn't active (re-run the
`source .venv/bin/activate` step), or your `PATH` doesn't include the environment's
`bin/`.

## Install from source

To track the latest development build or to hack on abkit itself, install from a
clone in editable mode with the dev extras:

```bash
git clone https://github.com/alexeiveselov92/ab-analysis-kit.git
cd ab-analysis-kit
pip install -e ".[dev]"          # add all-db for database work: ".[dev,all-db]"
```

The editable install exposes the same `abk` command; source edits take effect
without reinstalling. See the [contributor guide](https://github.com/alexeiveselov92/ab-analysis-kit/blob/main/CLAUDE.md)
if you plan to change code.

## Next steps

- **[Quickstart](quickstart.md)** — scaffold a project with `abk init` and get your
  first result from `abk run`.
- **[Databases](../guides/databases.md)** and **[Configuration](../guides/configuration.md)**
  — wire `profiles.yml` up to your warehouse (secrets stay out of YAML via
  `{{ env_var('VAR') }}` / `${VAR}` interpolation).

`abk init` scaffolds a runnable example, so you can go straight from install to a
real readout — no database required for the config lint (`abk run --steps validate`).
