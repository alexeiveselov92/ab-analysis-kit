# Contributing to ab-analysis-kit

> Dev setup, commands, conventions, and the change-control processes that keep
> the statistics trustworthy. Companion to
> [architecture.md](architecture.md) (as-built) and
> [docs/specs/](../../docs/specs/) (design contracts).

## Setup

```bash
pip install -e ".[dev]"          # numpy/scipy/statsmodels/pydantic/click/jinja2 + pytest/black/ruff/mypy
pip install -e ".[dev,all-db]"   # + clickhouse-driver / psycopg2 / pymysql (DB work)
pre-commit install
```

Python ≥ 3.10. `pip` package `ab-analysis-kit`, import package `abkit`,
terminal command `abk`.

## Commands

| What | Command |
|---|---|
| All tests | `python3 -m pytest tests/` |
| Stats unit tests | `python3 -m pytest tests/stats/` |
| Golden (legacy-parity) tests | `python3 -m pytest tests/golden/` |
| Lint/format/types (ruff, black, mypy over `abkit/`) | `pre-commit run --all-files` |
| Version | single source: `__version__` in `abkit/__init__.py` |

CI runs the full matrix on every push; keep it green.

## Conventions

- **numpy-first, no pandas** in core logic. Vectorise; avoid Python loops over
  units.
- Type hints everywhere; `mypy` runs over `abkit/` in pre-commit.
- Docstrings cite the governing spec section (e.g. `docs/specs/declarative-config.md §7`)
  when implementing a contract — the spec is the requirement, the docstring is
  the pointer.
- Commit style: conventional commits scoped by package —
  `feat(stats): …`, `fix(stats): …`, `docs(specs): …`, `chore: …`, `ci: …`.
- `CHANGELOG.md` (Keep a Changelog) is **authoritative for behavior changes**;
  update it in the same PR.
- Repo docs and code comments are English; keep comments to constraints the
  code can't show.

## Adding a statistical method (the plugin checklist)

1. One `BaseMethod` subclass in `abkit/stats/parametric/` or `bootstrap/`;
   decorate with `@register` (canonical `name`, optional `aliases`).
2. Declare params as `ParamSpec`s — typed, defaulted, identity-flagged
   (`seed` must be identity-excluded for bootstrap methods).
3. Implement **both** entries where the math allows: `from_samples` and
   `from_suffstats` (dual-entry equivalence is tested).
4. Tests: known-answer test; dual-entry equivalence; params/identity hash
   addition to `tests/stats/test_identity.py`; golden test if reproducing a
   legacy method.
5. Never touch the pipeline/DB/CLI to make a method work — if you need to,
   the design is wrong (methods are plugins).

## Changing a statistical number (change control — hard rule)

Any deviation from the captured baseline (`docs/specs/statistics-baseline.md`):

1. Bump the method's `ALGORITHM_VERSION`.
2. Record the deviation in `docs/specs/statistics-changes.md` (what, why,
   expected numeric impact).
3. Entry in `CHANGELOG.md`.
4. A/A validation through `abk validate` (shipped in M4): run the matrix on the
   affected method + metric and confirm FPR ≈ α / power holds. The golden tests
   still pin the baseline in parallel.

Golden tests pin the **baseline**, not your improvement: a deliberate deviation
gets a *new* test; the baseline reproduction stays intact behind its original
entry (legacy-parity mode). Tolerance is **relative 1e-9** — never loosen it to
make a test pass.

## Porting from detectkit (M2+)

The donor is `/home/aleksei/wsl_analytics/detektkit`. Components marked ⟲ in
[architecture.md §4](../../docs/specs/architecture.md) port near-verbatim:
rename `dtk`→`abk`, `detectkit`→`abkit`, `_dtk_*`→`_ab_*`; keep the donor's
structure/tests where they hold. Anything metric-primary-shaped (detectkit's
primary entity) must be consciously reshaped to experiment-primary — flag it in
the PR rather than silently diverging.

## Release checklist (from M6 onward)

- `__version__` bumped; `CHANGELOG.md` section cut.
- `docs/`, `.claude/rules/`, and the `init-claude` packaged assets
  (`abkit/cli/assets/claude/`) tell the same story.
- Website sync (`abkit.pipelab.dev`) after docs changes.
- PyPI publish is tag-triggered by CI.
