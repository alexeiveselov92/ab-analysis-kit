"""Experiment configuration — THE primary entity (declarative-config.md §2).

An experiment declares its READ-ONLY assignment source, variants and expected
split (the SRM gate input), the pinned cumulative window edges, the cadence
(scalar duration or a dense-early coarsening schedule —
cumulative-intervals.md §6), and the list of comparisons binding library
metrics to statistical methods.

Validation split: everything checkable from THIS file alone lives here
(fail-fast at parse); cross-file reference integrity, look-count gates that
need the project config + the planner grid, and SQL render checks live in
``config/validator`` level 2 (WP6).
"""

from __future__ import annotations

import zoneinfo
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from abkit.config.method_config import MethodConfig
from abkit.core.interval import Interval
from abkit.database.tables import MAX_EXPERIMENT_NAME_LENGTH, MAX_VARIANT_NAME_LENGTH
from abkit.utils.json_utils import json_dumps_sorted

DAY_SECONDS = 86400

ExperimentStatus = Literal["design", "running", "concluded", "archived"]
CorrectionKind = Literal["none", "bonferroni", "benjamini_hochberg"]
SequentialScheme = Literal["always_valid", "alpha_spending"]


class CadenceSegment(BaseModel):
    """One segment of a coarsening cadence schedule: ``{every: 1h, until: 48h}``.

    ``until`` is measured from ``start_ts``; the LAST segment may omit it
    (runs to the horizon).
    """

    every: int | str = Field(..., description="Cutoff step within this segment")
    until: int | str | None = Field(
        default=None, description="Segment end, measured from start_ts (last segment: omit)"
    )

    @field_validator("every", "until")
    @classmethod
    def _parses_as_interval(cls, v: int | str | None) -> int | str | None:
        if v is not None:
            Interval(v)  # raises ValueError on bad grammar / non-positive
        return v

    def every_seconds(self) -> int:
        return Interval(self.every).seconds

    def until_seconds(self) -> int | None:
        return None if self.until is None else Interval(self.until).seconds


class AssignmentConfig(BaseModel):
    """The READ-ONLY exposure source — abkit does not randomize."""

    query: str | None = Field(default=None, description="Inline assignment SQL")
    query_file: Path | None = Field(default=None, description="Path to assignment SQL file")
    added_filters: str = Field(
        default="", description="Optional extra SQL fragment (must start with AND)"
    )
    variants: list[str] = Field(..., description="Variant names; FIRST is control (name_1)")
    expected_split: dict[str, float] = Field(
        ..., description="Expected assignment shares; drives the SRM chi-square gate"
    )

    @field_validator("variants")
    @classmethod
    def validate_variants(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("assignment.variants needs at least two variants")
        if len(v) != len(set(v)):
            raise ValueError("assignment.variants must be unique")
        for name in v:
            if not name:
                raise ValueError("variant names cannot be empty")
            if len(name) > MAX_VARIANT_NAME_LENGTH:
                raise ValueError(
                    f"variant name '{name}' is longer than {MAX_VARIANT_NAME_LENGTH} "
                    "characters (the storage key budget)"
                )
        return v

    @field_validator("added_filters")
    @classmethod
    def validate_added_filters(cls, v: str) -> str:
        v = v.strip()
        if v and not v.upper().startswith("AND"):
            raise ValueError(
                "assignment.added_filters must start with 'AND' (it is appended "
                "to the packaged cohort WHERE clause)"
            )
        return v

    @model_validator(mode="after")
    def validate_query_source(self) -> AssignmentConfig:
        if self.query is None and self.query_file is None:
            raise ValueError("assignment needs either 'query' or 'query_file'")
        if self.query is not None and self.query_file is not None:
            raise ValueError("assignment: only one of 'query' or 'query_file', not both")
        return self

    @model_validator(mode="after")
    def validate_expected_split(self) -> AssignmentConfig:
        unknown = set(self.expected_split) - set(self.variants)
        if unknown:
            raise ValueError(
                f"expected_split names unknown variants {sorted(unknown)} "
                f"(assignment.variants: {self.variants})"
            )
        missing = set(self.variants) - set(self.expected_split)
        if missing:
            raise ValueError(f"expected_split is missing variants {sorted(missing)}")
        for name, share in self.expected_split.items():
            if not 0.0 < share < 1.0:
                raise ValueError(
                    f"expected_split['{name}'] must be a fraction in (0, 1), got {share}"
                )
        total = sum(self.expected_split.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"expected_split must sum to 1.0, got {total}")
        return self

    def get_query_text(self, project_root: Path | None = None) -> str:
        """Get the assignment SQL text (inline or from file)."""
        if self.query is not None:
            return self.query
        query_path = project_root / self.query_file if project_root is not None else self.query_file
        if not query_path.exists():
            raise FileNotFoundError(f"Assignment query file not found: {query_path}")
        with open(query_path) as f:
            return f.read()


class SequentialConfig(BaseModel):
    """Opt-in peeking-correct CIs (default off = legacy behaviour, decision Q2)."""

    enabled: bool = Field(default=False)
    scheme: SequentialScheme = Field(default="always_valid")

    @model_validator(mode="after")
    def validate_scheme(self) -> SequentialConfig:
        # M5 ships always_valid (mSPRT/asymptotic-CS) only; group-sequential is deferred
        # (m5-implementation-plan.md D6). The Literal keeps forward-compat; the message
        # is friendlier than a bare enum error.
        if self.scheme == "alpha_spending":
            raise ValueError(
                "scheme: alpha_spending (group-sequential) is not implemented — "
                "a future item, no version promise; use scheme: always_valid "
                "(the mSPRT/asymptotic always-valid mode)"
            )
        return self


class ComparisonConfig(BaseModel):
    """One (metric × method) binding within an experiment.

    ``min_effect``/``desired_direction`` are READ-TIME verdict inputs
    (m3-implementation-plan.md D5; data-contract-and-reporting.md §1) — they
    are not method params and never enter ``method_config_id``.
    """

    metric: str = Field(..., description="References metrics/<name>.yml by name")
    is_main_metric: bool = Field(default=False, description="Primary winner criterion")
    is_guardrail: bool = Field(default=False, description="Checked for regression only")
    method: MethodConfig = Field(..., description="The statistical method to run")
    min_effect: float | None = Field(
        default=None,
        gt=0,
        description=(
            "The business-meaningful effect in the units of this comparison's "
            "persisted effect (test_type-dependent). Enables the FLAT verdict: "
            "without it, flat cannot be distinguished from underpowered (D5(b))"
        ),
    )
    desired_direction: Literal["increase", "decrease"] = Field(
        default="increase",
        description=(
            "Which effect sign is good for this metric — orients WIN vs LOSE "
            "for main metrics and the regression check for guardrails (D5(c))"
        ),
    )

    @model_validator(mode="after")
    def validate_roles(self) -> ComparisonConfig:
        if self.is_main_metric and self.is_guardrail:
            raise ValueError(
                f"comparison '{self.metric}': is_main_metric and is_guardrail "
                "cannot both be true"
            )
        return self


class ReadoutConfig(BaseModel):
    """Read-time verdict knobs (m3-implementation-plan.md D5 — never identity)."""

    stabilization_days: float = Field(
        default=7.0,
        gt=0,
        description=(
            "The trailing elapsed-days window over which significance must be "
            "persistent (judged over elapsed time, never look count — "
            "data-contract-and-reporting.md §4); default 7 covers one weekly cycle"
        ),
    )
    guardrail_policy: Literal["block", "warn"] = Field(
        default="block",
        description=(
            "What a regressed guardrail does to a WIN: 'block' caps it at "
            "INCONCLUSIVE (default); 'warn' keeps WIN with a mandatory loud "
            "caveat (owner-ratified D5(c))"
        ),
    )


class ExperimentConfig(BaseModel):
    """The experiment — see the module docstring and declarative-config.md §2."""

    name: str = Field(..., description="Globally unique experiment name (DB key)")
    description: str | None = Field(default=None)
    status: ExperimentStatus = Field(default="running")
    is_actual: bool = Field(default=True, description="Scheduled runs pick it up")
    tags: list[str] | None = Field(default=None)

    start_date: date = Field(..., description="PINNED left edge of every cumulative window")
    end_date: date = Field(..., description="Planner horizon (drives the power plan)")
    unit_key: str = Field(..., description="Randomization + default analysis unit")

    cadence: int | str | list[CadenceSegment] = Field(
        default="1d",
        description="Cumulative cutoff step: duration scalar or coarsening schedule",
    )
    data_lag: int | str | None = Field(
        default=None,
        description=(
            "Completeness watermark: data assumed complete through now() - "
            "data_lag. REQUIRED when cadence < 1d; default 0 reproduces "
            "*_wo_curr_day at daily cadence"
        ),
    )
    timezone: str = Field(
        default="UTC",
        description="Interprets date-typed fields & daily midnight snapping; storage is UTC",
    )

    assignment: AssignmentConfig = Field(...)
    alpha: float | None = Field(
        default=None, description="Experiment-level significance (None -> project default)"
    )
    correction: CorrectionKind | None = Field(default=None, description="None -> project default")
    sequential: SequentialConfig = Field(default_factory=SequentialConfig)
    readout: ReadoutConfig = Field(default_factory=ReadoutConfig)
    comparisons: list[ComparisonConfig] = Field(...)

    # ── field validators ─────────────────────────────────────────────────────

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v:
            raise ValueError("Experiment name cannot be empty")
        if not all(c.isalnum() or c in ("_", "-") for c in v):
            raise ValueError(
                "Experiment name can only contain alphanumeric characters, "
                "underscores, and dashes"
            )
        if len(v) > MAX_EXPERIMENT_NAME_LENGTH:
            raise ValueError(
                f"Experiment name is longer than {MAX_EXPERIMENT_NAME_LENGTH} "
                "characters (the storage key budget)"
            )
        return v

    @field_validator("alpha")
    @classmethod
    def validate_alpha(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 < v < 1.0:
            raise ValueError(f"alpha must be in (0, 1), got {v}")
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            zoneinfo.ZoneInfo(v)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {v!r}") from exc
        return v

    @field_validator("data_lag")
    @classmethod
    def validate_data_lag(cls, v: int | str | None) -> int | str | None:
        if v is None or v == 0:
            return v
        Interval(v)  # raises on bad grammar / negative
        return v

    @field_validator("cadence")
    @classmethod
    def validate_cadence_scalar(cls, v):
        if not isinstance(v, list):
            Interval(v)  # whole seconds >= 1s (the §6.1 grammar)
        return v

    # ── model validators ─────────────────────────────────────────────────────

    @model_validator(mode="after")
    def validate_dates(self) -> ExperimentConfig:
        if self.end_date < self.start_date:
            raise ValueError(f"end_date ({self.end_date}) is before start_date ({self.start_date})")
        return self

    @model_validator(mode="after")
    def validate_cadence_schedule(self) -> ExperimentConfig:
        """Schedule segments: non-overlapping, strictly coarsening, increasing until."""
        if not isinstance(self.cadence, list):
            if self.cadence_seconds_min() > self.horizon_seconds():
                raise ValueError(
                    f"cadence ({self.cadence}) is longer than the experiment horizon "
                    f"({self.horizon_seconds()}s) — no cutoff would ever be produced"
                )
            return self

        segments = self.cadence
        if not segments:
            raise ValueError("cadence schedule cannot be empty")
        previous_every = 0
        previous_until = 0
        for i, seg in enumerate(segments):
            is_last = i == len(segments) - 1
            every = seg.every_seconds()
            until = seg.until_seconds()
            if every <= previous_every:
                raise ValueError(
                    "cadence schedule must be strictly coarsening: segment "
                    f"{i} 'every' ({seg.every}) must be longer than the previous"
                )
            if until is None and not is_last:
                raise ValueError(
                    f"cadence schedule segment {i} needs 'until' (only the last "
                    "segment may run to the horizon)"
                )
            if until is not None:
                if until <= previous_until:
                    raise ValueError(
                        "cadence schedule 'until' bounds must be strictly increasing: "
                        f"segment {i} ({seg.until})"
                    )
                if until <= every:
                    raise ValueError(
                        f"cadence schedule segment {i}: 'until' ({seg.until}) must "
                        f"exceed 'every' ({seg.every})"
                    )
                previous_until = until
            previous_every = every
        if self.cadence_seconds_min() > self.horizon_seconds():
            raise ValueError("cadence schedule's densest segment is longer than the horizon")
        return self

    @model_validator(mode="after")
    def validate_sub_day_gates(self) -> ExperimentConfig:
        """cumulative-intervals §6: sub-day cadence gates that need no project config."""
        if not self.is_sub_day():
            return self
        if self.data_lag is None:
            raise ValueError(
                "cadence < 1d requires 'data_lag' (declare your ingestion SLA — "
                "cumulative-intervals.md §6.2). Use data_lag: 0 only if data is "
                "truly complete in real time."
            )
        # (scheme: alpha_spending is rejected globally in SequentialConfig — M6 deferral.)
        return self

    @model_validator(mode="after")
    def validate_comparisons(self) -> ExperimentConfig:
        if not self.comparisons:
            raise ValueError("an experiment needs at least one comparison")
        metrics = [c.metric for c in self.comparisons]
        duplicates = sorted({m for m in metrics if metrics.count(m) > 1})
        if duplicates:
            raise ValueError(
                f"duplicate metric references in comparisons: {duplicates} "
                "(bind each metric at most once per experiment)"
            )
        if not any(c.is_main_metric for c in self.comparisons):
            raise ValueError(
                "at least one comparison must set is_main_metric: true "
                "(it drives the verdict and the two-tier Bonferroni)"
            )
        return self

    # ── derived accessors ────────────────────────────────────────────────────

    def cadence_segments(self) -> list[tuple[int, int | None]]:
        """Normalised ``[(every_seconds, until_seconds|None), ...]``.

        A scalar cadence is one segment running to the horizon — a property
        the planner tests pin: grids for ``1d`` and ``[{every: 1d}]`` must be
        identical (plan R1).
        """
        if isinstance(self.cadence, list):
            return [(seg.every_seconds(), seg.until_seconds()) for seg in self.cadence]
        return [(Interval(self.cadence).seconds, None)]

    def cadence_seconds_min(self) -> int:
        """The densest step (drives the sub-day gates)."""
        return min(every for every, _ in self.cadence_segments())

    def is_sub_day(self) -> bool:
        return self.cadence_seconds_min() < DAY_SECONDS

    def data_lag_seconds(self) -> int:
        """The declared ingestion SLA in seconds (0 when unset — daily default)."""
        if self.data_lag is None or self.data_lag == 0:
            return 0
        return Interval(self.data_lag).seconds

    def horizon_seconds(self) -> int:
        """Length of the full experiment window: [start_date .. end_date] inclusive."""
        return ((self.end_date - self.start_date).days + 1) * DAY_SECONDS

    def cadence_canonical_json(self) -> str:
        """Canonical JSON for the ``_ab_experiments`` catalog (always a segment list)."""
        return json_dumps_sorted(
            [{"every": every, "until": until} for every, until in self.cadence_segments()]
        )

    def main_metrics(self) -> list[str]:
        return [c.metric for c in self.comparisons if c.is_main_metric]

    def get_comparison(self, metric: str) -> ComparisonConfig:
        for comparison in self.comparisons:
            if comparison.metric == metric:
                return comparison
        raise KeyError(f"no comparison for metric {metric!r} in experiment {self.name!r}")

    def catalog_record(
        self,
        path: str = "",
        effective_alpha: float | None = None,
        effective_correction: str | None = None,
    ) -> dict[str, Any]:
        """The flat ``_ab_experiments`` row (JSON fields canonical).

        ``effective_alpha``/``effective_correction`` are the project-resolved
        values the pipeline actually runs with (an unset experiment field
        falls back to the project default — the caller resolves).
        """
        return {
            "experiment": self.name,
            "description": self.description,
            "status": self.status,
            "is_actual": self.is_actual,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "unit_key": self.unit_key,
            "cadence": self.cadence_canonical_json(),
            "data_lag_seconds": self.data_lag_seconds(),
            "timezone": self.timezone,
            "variants": json_dumps_sorted(self.assignment.variants),
            "expected_split": json_dumps_sorted(self.assignment.expected_split),
            "alpha": effective_alpha if effective_alpha is not None else self.alpha,
            "correction": (
                effective_correction if effective_correction is not None else self.correction
            ),
            "sequential_enabled": self.sequential.enabled,
            "sequential_scheme": self.sequential.scheme,
            "comparisons": json_dumps_sorted(
                [
                    {
                        "metric": c.metric,
                        "is_main_metric": c.is_main_metric,
                        "is_guardrail": c.is_guardrail,
                        "method": c.method.name,
                        "method_config_id": c.method.method_config_id,
                    }
                    for c in self.comparisons
                ]
            ),
            "path": path,
            "tags": json_dumps_sorted(self.tags or []),
        }

    @classmethod
    def from_yaml_file(cls, path: Path) -> ExperimentConfig:
        """Load experiment configuration from a YAML file.

        Supports both flat and nested (``experiment: {...}``) structures.
        """
        import yaml

        if not path.exists():
            raise FileNotFoundError(f"Experiment config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError(f"Empty experiment config file: {path}")

        if "experiment" in data and isinstance(data["experiment"], dict):
            data = data["experiment"]

        return cls.model_validate(data)
