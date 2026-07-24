"""
Project configuration models.

Defines configuration structure for abkit_project.yml.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectPathsConfig(BaseModel):
    """
    Project directory paths configuration.

    Attributes:
        experiments: Directory containing experiment YAML files
        metrics: Directory containing metric YAML files
        sql: Directory containing SQL query files
    """

    experiments: str = Field(default="experiments", description="Experiments directory")
    metrics: str = Field(default="metrics", description="Metrics directory")
    sql: str = Field(default="sql", description="SQL files directory")


class ProjectTablesConfig(BaseModel):
    """Internal table names (the six ``_ab_*`` tables).

    Overrides are NOT supported yet: the internal-tables mixins are keyed by
    the canonical constants, so a renamed table would split the read and
    write paths. The block exists (and validates) so the config surface is
    stable when renaming lands. Per-experiment source reference does not need
    this — see ``AssignmentConfig.cohort_copy``; unblocking table-name
    overrides generally remains future work (m8-implementation-plan.md §0.4.2).
    """

    experiments: str = Field(default="_ab_experiments")
    exposures: str = Field(default="_ab_exposures")
    unit_state: str = Field(default="_ab_unit_state")
    results: str = Field(default="_ab_results")
    aa_runs: str = Field(default="_ab_aa_runs")
    tasks: str = Field(default="_ab_tasks")

    @model_validator(mode="after")
    def reject_overrides(self) -> "ProjectTablesConfig":
        for name, field in type(self).model_fields.items():
            if getattr(self, name) != field.default:
                raise ValueError(
                    f"tables.{name}: internal table name overrides are not "
                    "supported yet (the _ab_* names are canonical)"
                )
        return self


class ProjectTimeoutsConfig(BaseModel):
    """
    Default timeout values for operations (in seconds).

    Attributes:
        load: Timeout for exposure/metric loading operations
        compute: Timeout for the statistical compute stage (also the run-lock
            staleness threshold)
    """

    load: int = Field(default=3600, description="Load timeout (seconds)")
    compute: int = Field(default=7200, description="Compute timeout (seconds)")

    @field_validator("load", "compute")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        """Validate timeout value."""
        if v < 1:
            raise ValueError("Timeout must be at least 1 second")
        if v > 86400:  # 24 hours
            raise ValueError("Timeout cannot exceed 24 hours (86400 seconds)")
        return v


class ProjectStatisticsConfig(BaseModel):
    """Project-wide statistical defaults (experiment fields override).

    ``alpha``/``correction`` feed the inspectable two-tier scheme
    (declarative-config.md §6); ``aa_fpr_budget`` colours the A/A matrix
    (M4); nothing here enters ``method_config_id``.
    """

    alpha: float = Field(default=0.05, description="Default significance level")
    test_type: Literal["relative", "absolute"] = Field(default="relative")
    correction: Literal["none", "bonferroni", "benjamini_hochberg"] = Field(
        default="bonferroni", description="Default multiple-testing correction"
    )
    power: float = Field(default=0.8, description="Default target power for MDE")
    aa_fpr_budget: float | None = Field(
        default=None,
        description="A/A false-positive budget the validate matrix colours against",
    )

    @field_validator("alpha", "power")
    @classmethod
    def validate_fraction(cls, v: float) -> float:
        if not 0.0 < v < 1.0:
            raise ValueError(f"must be a fraction in (0, 1), got {v}")
        return v

    @field_validator("aa_fpr_budget")
    @classmethod
    def validate_budget(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 < v <= 1.0:
            raise ValueError(f"aa_fpr_budget must be a fraction in (0, 1], got {v}")
        return v


class ProjectLimitsConfig(BaseModel):
    """Look-count and small-sample gates (cumulative-intervals.md §6.1).

    ``max_looks`` is the ONE hard cadence gate (there is deliberately no time
    floor); ``warn_looks`` triggers the peeking warning without
    ``sequential.enabled``; ``min_units_per_arm`` drives the
    ``insufficient_data`` demotion (row written, inference withheld).
    """

    max_looks: int = Field(default=5000, description="Planned looks above this = config error")
    warn_looks: int = Field(
        default=100, description="Looks above this without sequential = peeking warning"
    )
    min_units_per_arm: int = Field(
        default=100, description="Below this the row is demoted to insufficient_data"
    )

    @field_validator("max_looks", "warn_looks", "min_units_per_arm")
    @classmethod
    def validate_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"must be at least 1, got {v}")
        return v


class ProjectComputeConfig(BaseModel):
    """Compute backend selection. v1 ships recompute only; ``incremental`` is
    the v2 path gated behind ``abk verify-incremental`` (cumulative-intervals §4)."""

    mode: Literal["recompute"] = Field(
        default="recompute",
        description="v1: full-window recompute (the golden reference). "
        "'incremental' arrives in v2 behind verify-incremental.",
    )
    incremental_reads: bool = Field(
        default=False,
        description="m9 WP4 opt-in: closed-form, non-stratified comparisons "
        "read per-unit cumulative moments from _ab_unit_state instead of "
        "re-scanning the fact table (any state gap falls back to recompute). "
        "Distinct from `mode` (the reserved v2 backend selector): this flag "
        "changes HOW a number is computed, never the number. Experiments "
        "override via their own `incremental_reads`. Default false until "
        "verify-incremental (m9 WP5) bakes.",
    )


class ProjectConfig(BaseModel):
    """
    Project configuration loaded from abkit_project.yml.

    Example YAML:
        ```yaml
        name: "my_ab_project"
        version: "1.0"

        paths:
          experiments: "experiments"
          metrics: "metrics"
          sql: "sql"

        statistics:
          alpha: 0.05
          test_type: relative
          correction: bonferroni
          power: 0.8

        limits:
          max_looks: 5000
          warn_looks: 100
          min_units_per_arm: 100

        timeouts:
          load: 3600
          compute: 7200

        default_profile: "clickhouse_prod"
        ```
    """

    name: str = Field(..., description="Project name")
    version: str = Field(default="1.0", description="Project version")
    paths: ProjectPathsConfig = Field(
        default_factory=ProjectPathsConfig, description="Directory paths"
    )
    tables: ProjectTablesConfig = Field(
        default_factory=ProjectTablesConfig, description="Default table names"
    )
    timeouts: ProjectTimeoutsConfig = Field(
        default_factory=ProjectTimeoutsConfig, description="Operation timeouts"
    )
    statistics: ProjectStatisticsConfig = Field(
        default_factory=ProjectStatisticsConfig, description="Statistical defaults"
    )
    limits: ProjectLimitsConfig = Field(
        default_factory=ProjectLimitsConfig, description="Look-count / small-n gates"
    )
    compute: ProjectComputeConfig = Field(
        default_factory=ProjectComputeConfig, description="Compute backend"
    )
    default_profile: str = Field(..., description="Default database profile")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate project name."""
        if not v:
            raise ValueError("Project name cannot be empty")
        # Allow alphanumeric, underscore, dash, space
        if not all(c.isalnum() or c in ("_", "-", " ") for c in v):
            raise ValueError(
                "Project name can only contain alphanumeric characters, "
                "underscores, dashes, and spaces"
            )
        return v

    @classmethod
    def from_yaml_file(cls, path: Path) -> "ProjectConfig":
        """
        Load project configuration from YAML file.

        Args:
            path: Path to abkit_project.yml

        Returns:
            ProjectConfig instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If YAML is invalid
        """
        import yaml

        if not path.exists():
            raise FileNotFoundError(f"Project config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError(f"Empty project config file: {path}")

        return cls.model_validate(data)
