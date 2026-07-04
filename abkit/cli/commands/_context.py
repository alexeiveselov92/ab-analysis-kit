"""Shared command bootstrap: project root, configs, profiles, manager.

Every failure raises :class:`click.ClickException` so commands exit NON-ZERO —
a documented deviation from the detectkit donor (which echoed and returned 0):
the CLI is the Prefect unit of automation (cli-and-dx.md §3), and a scheduler
must see failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

from abkit.config import (
    ExperimentConfig,
    MetricConfig,
    ProfilesConfig,
    ProjectConfig,
    find_project_root,
    validate_project_configs,
)


@dataclass
class ProjectContext:
    root: Path
    project: ProjectConfig
    profiles: ProfilesConfig
    experiments: list[tuple[Path, ExperimentConfig]]
    metrics: list[tuple[Path, MetricConfig]]

    @property
    def metrics_by_name(self) -> dict[str, MetricConfig]:
        return {config.name: config for _, config in self.metrics}

    def manager_factory(self, profile: str | None):
        """One manager per call (worker threads need their own connections)."""
        profiles = self.profiles

        def factory():
            return profiles.create_manager(profile)

        return factory


def load_project_context(require_profiles: bool = True) -> ProjectContext:
    """Resolve the project root and parse every config (level-1 fail-fast)."""
    root = find_project_root()
    if root is None:
        raise click.ClickException(
            "not inside an abkit project (no abkit_project.yml found up the tree). "
            "Run `abk init <name>` to create one."
        )

    try:
        project = ProjectConfig.from_yaml_file(root / "abkit_project.yml")
    except Exception as exc:
        raise click.ClickException(f"abkit_project.yml: {exc}") from exc

    profiles = None
    profiles_path = root / "profiles.yml"
    if profiles_path.exists():
        try:
            profiles = ProfilesConfig.from_yaml(profiles_path)
        except Exception as exc:
            raise click.ClickException(f"profiles.yml: {exc}") from exc
    elif require_profiles:
        raise click.ClickException(f"profiles.yml not found (expected at {profiles_path})")

    try:
        experiments, metrics = validate_project_configs(root, project)
    except (ValueError, FileNotFoundError) as exc:
        raise click.ClickException(str(exc)) from exc

    return ProjectContext(
        root=root,
        project=project,
        profiles=profiles,
        experiments=experiments,
        metrics=metrics,
    )
