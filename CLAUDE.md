# ab-analysis-kit — contributor & AI-assistant guide

**ab-analysis-kit** (CLI `abk`; Python import package `abkit`) is a Python library + CLI for **A/B experiment
analysis**: declarative YAML + SQL run through a `load → compute → readout`
pipeline. It is detectkit's sibling — same DNA (numpy-first, db-agnostic
[ClickHouse-first; PostgreSQL/MySQL], CLI-first, AI-native onboarding, self-contained
reports, a chart-first cockpit), with the `detect` stage replaced by a statistical
`compute` stage and the primary entity flipped from *metric* to *experiment*.

> **Using abkit, not hacking on it?** (Once shipped:) see the README and
> `abk init-claude`, which sets up assistant context inside *your own* project.

## Status: pre-development

The repo currently holds the **project-initiation contract**. The source of truth is
[docs/specs/](docs/specs/). Read the relevant spec before writing code:

| If you're working on… | Read |
|---|---|
| Module map, pipeline, the chosen architecture, key decisions | [docs/specs/architecture.md](docs/specs/architecture.md) |
| The statistical engine (the math to reproduce) | [docs/specs/statistics-baseline.md](docs/specs/statistics-baseline.md) + [../reference/legacy-method-catalogue.md](docs/reference/legacy-method-catalogue.md) |
| Deliberate deviations / new methods / the rederivation process | [docs/specs/statistics-changes.md](docs/specs/statistics-changes.md) |
| Cumulative windows, compute strategy, incremental v2 | [docs/specs/cumulative-intervals.md](docs/specs/cumulative-intervals.md) |
| YAML/SQL config, the assignment macro, `method_config_id`, validation | [docs/specs/declarative-config.md](docs/specs/declarative-config.md) |
| The results contract, decision logic, reporting, explore, BI | [docs/specs/data-contract-and-reporting.md](docs/specs/data-contract-and-reporting.md) |
| The A/A FPR matrix (`abk validate`) | [docs/specs/aa-false-positive-matrix.md](docs/specs/aa-false-positive-matrix.md) |
| CLI, explore cockpit, init-claude, Prefect, docs | [docs/specs/cli-and-dx.md](docs/specs/cli-and-dx.md) |
| **What must be true before/after each milestone** | [docs/specs/quorum-review.md](docs/specs/quorum-review.md) (the must-fix gate) |

The master plan in Russian: [docs/ru/project-initiation-spec.md](docs/ru/project-initiation-spec.md).
Reference material (legacy dashboard JSON, results chart, method catalogue):
[docs/reference/](docs/reference/).

> Once development starts, the per-version assistant-facing condensation moves to
> `.claude/rules/` (the `init-claude` payload, rendered on the docs site) — keep it
> and `docs/` in sync, detectkit-style. Until then, `docs/specs/` is canonical.

## Invariants (do not violate)

- **`abkit.stats` is pure** — numpy/scipy/statsmodels only; never config/DB/Jinja/click.
  (Sole intra-package dependency: the stdlib-only `abkit.utils.json_utils`
  canonical-hash path; enforced by `tests/stats/test_purity.py`.)
- **Never change a number silently** — every deviation from the baseline is an
  `ALGORITHM_VERSION` bump + a `statistics-changes.md` entry + A/A validation.
- **Methods are plugins** — a new estimator is one `BaseMethod` class + registry
  entry; the pipeline/DB/CLI never special-case a method name.
- **The DB manager stays generic** — `table_name`-keyed; `_ab_*` semantics live in
  `internal_tables/`, never in the base manager.
- **Greenfield storage** — we do **not** copy the legacy `marts.*` schema; the legacy
  dashboard is reference only.
- **Renderer stays framework-free** — baked payload + self-contained JS (so it can
  embed in a future app).
- **Keep `init-claude` assets in sync on release** with `docs/` and `__version__`.

## Quick reference (planned)

- **Tests:** `python3 -m pytest tests/` (golden / stats / aa / e2e).
- **Lint/format/types:** `pre-commit run --all-files`.
- `__version__` in `abkit/__init__.py`; `CHANGELOG.md` authoritative for behavior.
- The math reproduces a captured baseline first (golden-tested vs the legacy
  *engine* at rel-1e-9), then improves it via the documented process.

Repo (planned): https://github.com/<org>/ab-analysis-kit · Docs: abkit.pipelab.dev
