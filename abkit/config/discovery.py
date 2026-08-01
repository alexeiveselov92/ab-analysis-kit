"""Project-root discovery + the two-level selector.

Hoisted out of the CLI (the donor kept it inside ``run.py``) so ``abk run``,
``abk explore``, ``abk clean`` and the validator share ONE selection seam.

Two-level semantics (cli-and-dx.md §1 — detectkit users will assume the
one-level model): ``--select`` resolves EXPERIMENTS only; metrics are chosen
with the distinct ``--metric`` flag. Selector forms: an experiment name, a
path glob (``experiments/growth/*.yml``), ``tag:<tag>``, or ``*``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml

from abkit.config.experiment_config import ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.validator import (
    discover_config_files,
    is_discoverable_config_file,
    validate_config_uniqueness,
)

PROJECT_FILE = "abkit_project.yml"

C = TypeVar("C", ExperimentConfig, MetricConfig)


def find_project_root(start: Path | None = None) -> Path | None:
    """Find the abkit project root by walking up from *start* (default cwd).

    Searches up to 10 levels for ``abkit_project.yml``.
    """
    current = (start or Path.cwd()).resolve()
    for _ in range(10):
        if (current / PROJECT_FILE).exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def _find_by_name(base_dir: Path, name: str) -> Path | None:
    """Recursive search by the ``name:`` field (cheap YAML peek)."""
    for path in discover_config_files(base_dir):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("name") == name:
            return path
    return None


def find_by_tag(base_dir: Path, tag: str) -> tuple[list[Path], list[str]]:
    """All live configs carrying *tag*; returns (paths, skip-warnings)."""
    matching: list[Path] = []
    skipped: list[str] = []
    for path in discover_config_files(base_dir):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except Exception as e:
            skipped.append(f"Skipping {path}: {e}")
            continue
        if isinstance(data, dict) and tag in (data.get("tags") or []):
            matching.append(path)
    return matching, skipped


def select_configs(
    selector: str,
    project_root: Path,
    base_dir_name: str,
    config_cls: type[C],
    namespace: str,
) -> tuple[list[tuple[Path, C]], list[str]]:
    """Resolve one selector against a config directory.

    Selector types (donor semantics, ``.history`` always excluded):
    - name: ``signup_test`` (file name first, then recursive ``name:`` search)
    - path pattern: ``experiments/growth/*.yml`` (or relative to the dir)
    - tag: ``tag:actual``
    - ``*``: everything

    Returns (selected configs uniqueness-checked, warnings).
    """
    base_dir = project_root / base_dir_name
    if not base_dir.exists():
        return [], []

    warnings: list[str] = []
    paths: list[Path] = []

    if selector.startswith("tag:"):
        paths, warnings = find_by_tag(base_dir, selector[4:])
    elif "*" in selector or "/" in selector:
        if selector == "*":
            paths = discover_config_files(base_dir)
        else:
            pattern = (
                selector
                if selector.startswith(f"{base_dir_name}/")
                else f"{base_dir_name}/{selector}"
            )
            paths = [
                p for p in project_root.glob(pattern) if is_discoverable_config_file(p, base_dir)
            ]
    else:
        for suffix in (".yml", ".yaml"):
            candidate = base_dir / f"{selector}{suffix}"
            if candidate.exists():
                paths = [candidate]
                break
        else:
            found = _find_by_name(base_dir, selector)
            if found:
                paths = [found]

    if not paths:
        return [], warnings
    return validate_config_uniqueness(sorted(set(paths)), config_cls, namespace), warnings


def resolve_experiments_dir(project_root: Path) -> str:
    """``project.paths.experiments`` for *project_root*, or the default.

    Resolved HERE rather than passed in by each caller: ten commands call
    :func:`select_experiments`, all of them with the default, which is exactly
    how a configurable directory ends up reaching none of its call sites (the
    m10 ``interval_anchor`` lesson). A project whose ``paths.experiments`` is
    renamed used to resolve NO selector at all — every command answered
    "Nothing selected." — while ``abk dashboard``'s own derivation honored the
    setting, so the two disagreed about which files exist.

    Deliberately tolerant: discovery runs before (and independently of) the
    CLI's config load, so an unreadable/absent ``abkit_project.yml`` falls back
    to the default instead of turning selection into a parse error — the
    caller that needs a validated project reports that failure itself.
    """
    from abkit.config.project_config import ProjectConfig

    try:
        project = ProjectConfig.from_yaml_file(project_root / "abkit_project.yml")
    except Exception:
        return "experiments"
    return project.paths.experiments


def select_experiments(
    project_root: Path,
    select: tuple[str, ...],
    exclude: tuple[str, ...] = (),
    experiments_dir: str | None = None,
) -> tuple[list[tuple[Path, ExperimentConfig]], list[str]]:
    """Resolve ``--select``/``--exclude`` to a unique experiment list.

    No selectors means ``*``. Every selection error names the namespace: an
    unmatched selector reminds that ``--select`` resolves experiments and
    metrics need ``--metric``.

    ``experiments_dir`` defaults to the project's own ``paths.experiments``
    (see :func:`resolve_experiments_dir`); pass it explicitly only to search a
    directory the project config does not name.
    """
    if experiments_dir is None:
        experiments_dir = resolve_experiments_dir(project_root)
    selectors = select or ("*",)
    chosen: dict[str, tuple[Path, ExperimentConfig]] = {}
    warnings: list[str] = []

    for selector in selectors:
        found, warns = select_configs(
            selector, project_root, experiments_dir, ExperimentConfig, "experiment"
        )
        warnings.extend(warns)
        if not found:
            warnings.append(
                f"selector '{selector}' matched no experiments "
                f"(--select resolves the experiment namespace; use --metric for metrics)"
            )
        for path, config in found:
            chosen[config.name] = (path, config)

    for selector in exclude:
        found, warns = select_configs(
            selector, project_root, experiments_dir, ExperimentConfig, "experiment"
        )
        warnings.extend(warns)
        for _, config in found:
            chosen.pop(config.name, None)

    ordered = sorted(chosen.values(), key=lambda pair: pair[1].name)
    return ordered, warnings
