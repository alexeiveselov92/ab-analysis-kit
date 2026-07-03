"""Metric configuration — the reusable library item (declarative-config.md §3).

A metric is referenced by experiments by name; its SQL must return ONE ROW PER
UNIT with additive aggregate columns over the cumulative window (the loader
guards this). The ``type`` + ``columns`` role mapping tells the loader how to
build stats-core containers (``Sample``/``Fraction``/``RatioSample`` or
sufficient statistics).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from abkit.database.tables import MAX_METRIC_NAME_LENGTH

MetricType = Literal["fraction", "sample", "ratio"]


class MetricColumnsConfig(BaseModel):
    """Column-role mapping from the metric SQL's result set.

    Role requirements by metric type (validated on the parent):
      - ``sample``:   requires ``value``; optional ``covariate`` (CUPED)
      - ``fraction``: requires ``count`` and ``nobs``
      - ``ratio``:    requires ``numerator`` and ``denominator``
      - ``variant`` is always required; ``stratum`` is always optional
    """

    variant: str = Field(..., description="Arm label column (from the cohort macro)")
    value: str | None = Field(default=None, description="Per-unit value (type=sample)")
    covariate: str | None = Field(default=None, description="CUPED covariate (type=sample)")
    count: str | None = Field(default=None, description="Successes (type=fraction)")
    nobs: str | None = Field(default=None, description="Trials (type=fraction)")
    numerator: str | None = Field(default=None, description="Numerator (type=ratio)")
    denominator: str | None = Field(default=None, description="Denominator (type=ratio)")
    stratum: str | None = Field(default=None, description="Stratification key (optional)")

    def role_map(self) -> dict[str, str]:
        """The set roles as ``{role: column}`` (drives ``column_set_id``)."""
        return {role: col for role, col in self.model_dump().items() if col is not None}


#: which roles each metric type requires / permits (beyond variant/stratum)
_TYPE_ROLES: dict[str, tuple[set[str], set[str]]] = {
    # type: (required, optional)
    "sample": ({"value"}, {"covariate"}),
    "fraction": ({"count", "nobs"}, set()),
    "ratio": ({"numerator", "denominator"}, set()),
}


class MetricConfig(BaseModel):
    """
    Configuration for a single reusable metric.

    Loaded from YAML files in the ``metrics/`` directory.

    Example YAML:
        ```yaml
        name: arpu
        description: "Average revenue per user"
        type: sample
        unit_key: user_id
        tags: [revenue, guardrail]
        columns:
          variant: group
          value: gross_usd
          covariate: prev_gross_usd
          stratum: country
        query_file: sql/arpu.sql
        ```
    """

    name: str = Field(..., description="Metric name (globally unique; DB key)")
    description: str | None = Field(default=None, description="Optional description")
    type: MetricType = Field(..., description="fraction | sample | ratio")
    unit_key: str | None = Field(
        default=None,
        description="Analysis unit key; must match (or be inherited from) the experiment",
    )
    tags: list[str] | None = Field(
        default=None, description="Optional tags for selection (tag: selectors)"
    )
    columns: MetricColumnsConfig = Field(..., description="Column-role mapping")
    query: str | None = Field(default=None, description="Inline SQL query")
    query_file: Path | None = Field(default=None, description="Path to SQL file")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Metric name: non-empty, storage-safe charset, fits the PK budget."""
        if not v:
            raise ValueError("Metric name cannot be empty")
        if not all(c.isalnum() or c in ("_", "-") for c in v):
            raise ValueError(
                "Metric name can only contain alphanumeric characters, underscores, and dashes"
            )
        if len(v) > MAX_METRIC_NAME_LENGTH:
            raise ValueError(
                f"Metric name is longer than {MAX_METRIC_NAME_LENGTH} characters "
                "(the storage key budget)"
            )
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        """Validate tags field."""
        if v is None:
            return v
        if not v:
            raise ValueError("tags list cannot be empty (use null instead)")
        if len(v) != len(set(v)):
            raise ValueError("Duplicate tags not allowed")
        for tag in v:
            if not tag:
                raise ValueError("Empty tag not allowed")
            if not all(c.isalnum() or c in ("_", "-") for c in tag):
                raise ValueError(
                    f"Invalid tag '{tag}': only alphanumeric characters, "
                    f"underscores, and dashes allowed"
                )
        return v

    @model_validator(mode="after")
    def validate_query_source(self) -> MetricConfig:
        """Exactly one of query / query_file."""
        if self.query is None and self.query_file is None:
            raise ValueError("Either 'query' or 'query_file' must be specified")
        if self.query is not None and self.query_file is not None:
            raise ValueError("Only one of 'query' or 'query_file' can be specified, not both")
        return self

    @model_validator(mode="after")
    def validate_columns_match_type(self) -> MetricConfig:
        """Enforce the role-set ↔ type matrix (declarative-config §3)."""
        required, optional = _TYPE_ROLES[self.type]
        roles = self.columns.role_map()
        present = set(roles) - {"variant", "stratum"}

        missing = required - present
        if missing:
            raise ValueError(f"metric type '{self.type}' requires column roles {sorted(missing)}")
        allowed = required | optional
        forbidden = present - allowed
        if forbidden:
            raise ValueError(
                f"metric type '{self.type}' does not accept column roles "
                f"{sorted(forbidden)} (allowed: {sorted(allowed)})"
            )
        return self

    def get_query_text(self, project_root: Path | None = None) -> str:
        """Get SQL query text (from inline query or file)."""
        if self.query is not None:
            return self.query

        if project_root is not None:
            query_path = project_root / self.query_file
        else:
            query_path = self.query_file

        if not query_path.exists():
            raise FileNotFoundError(f"Query file not found: {query_path}")

        with open(query_path) as f:
            return f.read()

    @classmethod
    def from_yaml_file(cls, path: Path) -> MetricConfig:
        """Load metric configuration from a YAML file.

        Supports both flat and nested (``metric: {...}``) structures.
        """
        import yaml

        if not path.exists():
            raise FileNotFoundError(f"Metric config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError(f"Empty metric config file: {path}")

        if "metric" in data and isinstance(data["metric"], dict):
            data = data["metric"]

        return cls.model_validate(data)
