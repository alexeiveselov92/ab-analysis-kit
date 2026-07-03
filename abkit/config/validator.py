"""Config validation — level 1: discovery + name uniqueness.

Parameterized by (directory, config class) so experiments and metrics share
one discovery/uniqueness engine. Level 2 — the full declarative-config.md §8
reference-integrity and cadence/looks matrix — lands with the WP6 work
package.

Namespace rule (cli-and-dx.md §1): experiment and metric names share ONE
namespace, so every uniqueness error names both files and the namespace —
detectkit users will assume the one-level model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeVar

from abkit.config.experiment_config import ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig


class _NamedConfig(Protocol):
    name: str


C = TypeVar("C", bound=_NamedConfig)


def is_discoverable_config_file(path: Path, base_dir: Path) -> bool:
    """True for a *live* config YAML — excludes hidden dirs (``.history/``).

    ``abk explore`` (M3) archives the previous config under
    ``<dir>/.history/<name>/`` before writing the tuned version in place.
    Those snapshots keep the original ``name:``, so discovering them as live
    configs would flag every tuned config as a duplicate-name conflict and run
    stale configs. Python's ``pathlib`` glob traverses hidden directories
    (unlike shell globbing), so they must be filtered explicitly. Any hidden
    path component *under* ``base_dir`` is skipped — this covers ``.history``
    and editor/VCS scratch dirs, while a project rooted under a dot-directory
    (checked only below ``base_dir``) is unaffected.
    """
    if not path.is_file() or path.suffix not in (".yml", ".yaml"):
        return False
    try:
        parts = path.relative_to(base_dir).parts
    except ValueError:
        parts = path.parts
    return not any(part.startswith(".") for part in parts)


def discover_config_files(base_dir: Path) -> list[Path]:
    """All live config YAMLs under *base_dir* (recursive), ``.history`` excluded.

    The single discovery seam shared by project validation and CLI selection,
    so the exclusion can't drift between them. Sorted for determinism.
    """
    files = [
        p
        for pattern in ("**/*.yml", "**/*.yaml")
        for p in base_dir.glob(pattern)
        if is_discoverable_config_file(p, base_dir)
    ]
    return sorted(set(files))


def validate_config_uniqueness(
    config_paths: list[Path],
    config_cls: type[C],
    namespace: str,
) -> list[tuple[Path, C]]:
    """Load every config and enforce unique names within *namespace*.

    Raises ValueError naming BOTH conflicting files and the namespace, with
    the parse error chained when a file fails validation.
    """
    configs: list[tuple[Path, C]] = []
    seen_names: dict[str, Path] = {}

    for config_path in config_paths:
        try:
            config = config_cls.from_yaml_file(config_path)  # type: ignore[attr-defined]
        except Exception as e:
            raise ValueError(f"Failed to parse {namespace} config at {config_path}:\n{e}") from e

        if config.name in seen_names:
            conflicting_path = seen_names[config.name]
            raise ValueError(
                f"Duplicate {namespace} name '{config.name}' found:\n"
                f"  - {conflicting_path}\n"
                f"  - {config_path}\n\n"
                f"{namespace.capitalize()} names must be unique across the project."
            )

        seen_names[config.name] = config_path
        configs.append((config_path, config))

    return configs


def validate_project_configs(
    project_root: Path,
    project_config: ProjectConfig | None = None,
) -> tuple[list[tuple[Path, ExperimentConfig]], list[tuple[Path, MetricConfig]]]:
    """Discover + parse + uniqueness-check all experiments and metrics.

    Level-1 validation:
    1. Discover live YAMLs under ``experiments/`` and ``metrics/``.
    2. Parse each (pydantic fail-fast — all intra-file rules run here).
    3. Names unique within each namespace.
    4. Cross-namespace collision rule: experiment and metric names share ONE
       namespace (an experiment named like a metric breaks two-level
       selection) — the error names both files.

    Returns:
        (experiments, metrics) as lists of (path, config) tuples.
    """
    paths = (project_config or ProjectConfig(name="x", default_profile="x")).paths
    experiments_dir = project_root / paths.experiments
    metrics_dir = project_root / paths.metrics

    for directory, kind in ((experiments_dir, "experiments"), (metrics_dir, "metrics")):
        if not directory.exists():
            raise FileNotFoundError(
                f"{kind.capitalize()} directory not found: {directory}\n"
                f"Expected structure:\n"
                f"  {project_root}/\n"
                f"    {paths.experiments}/\n"
                f"    {paths.metrics}/\n"
            )

    experiment_paths = discover_config_files(experiments_dir)
    if not experiment_paths:
        raise ValueError(
            f"No experiment files found in {experiments_dir}\n"
            f"Expected at least one *.yml or *.yaml file."
        )
    metric_paths = discover_config_files(metrics_dir)
    if not metric_paths:
        raise ValueError(
            f"No metric files found in {metrics_dir}\n"
            f"Expected at least one *.yml or *.yaml file."
        )

    experiments = validate_config_uniqueness(experiment_paths, ExperimentConfig, "experiment")
    metrics = validate_config_uniqueness(metric_paths, MetricConfig, "metric")

    # Cross-namespace collision: one shared namespace (cli-and-dx §1).
    metric_names = {config.name: path for path, config in metrics}
    for exp_path, exp_config in experiments:
        if exp_config.name in metric_names:
            raise ValueError(
                f"Name '{exp_config.name}' is used by BOTH an experiment and a metric:\n"
                f"  - experiment: {exp_path}\n"
                f"  - metric:     {metric_names[exp_config.name]}\n\n"
                "Experiment and metric names share one namespace "
                "(two-level selection would be ambiguous) — rename one of them."
            )

    return experiments, metrics
