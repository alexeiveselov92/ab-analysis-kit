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

import itertools
import zoneinfo
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from abkit.config.method_config import MethodConfig
from abkit.config.signals import SignalKind
from abkit.core.interval import Interval
from abkit.core.period_planner import Grid, as_local_datetime, resolve_instant
from abkit.database.tables import MAX_EXPERIMENT_NAME_LENGTH, MAX_VARIANT_NAME_LENGTH
from abkit.utils.json_utils import json_dumps_sorted

DAY_SECONDS = 86400

ExperimentStatus = Literal["design", "running", "concluded", "archived"]
CorrectionKind = Literal["none", "bonferroni", "benjamini_hochberg"]

#: How a guardrail comparison's alpha is resolved (m13 STAT-1c, decision D8).
#: ``inherit`` is the pre-0.8.0 behaviour — a guardrail shares the secondary
#: tier's budget like any non-main metric. ``none`` leaves that budget entirely:
#: the guardrail is tested at the RAW experiment alpha, and it stops counting
#: towards the secondary divisor (which loosens alpha for the screening metrics
#: that remain). Correcting a guardrail costs sensitivity to the harm it exists
#: to catch, so the error points the dangerous way — but the flip is opt-in
#: because it moves persisted numbers (m13 D1).
GuardrailCorrectionKind = Literal["inherit", "none"]

#: Which contrasts the experiment CLAIMS (m13 STAT-1b, decision D15).
#: ``all_pairs`` is the pre-0.8.0 family — every ``C(g,2)`` variant pair is
#: computed, and the Bonferroni divisor pays for all of them. ``vs_control``
#: declares the ``g−1`` many-to-one contrasts against the first declared
#: variant: treatment-vs-treatment pairs are neither computed nor corrected
#: for, which multiplies every tier's level by ``g/2`` (≈ +10 points of power
#: at four arms). It is a declaration of the DESIGN, not a project-wide
#: statistical policy, which is why it has no project-level default: the
#: family a surface reads must never depend on whether that surface happened
#: to resolve one.
ContrastSet = Literal["all_pairs", "vs_control"]

SequentialScheme = Literal["always_valid", "alpha_spending"]

#: ``interval_anchor``'s two symbolic forms (the third is an explicit instant).
AnchorMode = Literal["midnight", "start"]

_ANCHOR_FORMS = (
    "use 'midnight' (local midnight in the experiment's `timezone` — the default), "
    "'start' (count from start_ts), or an explicit timestamp such as 2024-06-28 or "
    "2024-06-28 21:00:00 (read in the experiment's timezone; it MAY precede start_ts)"
)

#: 0.5.0 renamed the window fields; the old spellings are rejected by name.
_RENAMED_WINDOW_FIELDS = {
    "start_date": (
        "start_ts",
        "a bare date still means local midnight of that day, so the value carries over unchanged",
    ),
    "end_date": (
        "horizon_ts",
        "horizon_ts is the EXCLUSIVE right edge, so port `end_date: 2024-07-14` "
        "as `horizon_ts: 2024-07-15` (or `2024-07-14 18:00:00` for a sub-day horizon)",
    ),
}


def parse_window_scalar(value: Any, field: str) -> date | datetime:
    """Parse one window scalar, PRESERVING ``date`` vs ``datetime``.

    A bare ``date`` and an explicit midnight ``datetime`` denote the same
    instant, so nothing here depends on telling them apart — but the union is
    kept type-preserving anyway, because ``str(value)`` reaches the state
    identity hash and the catalog, and a type that flips under a re-parse
    would silently orphan a materialized series.

    Two coercions pydantic would otherwise perform are rejected outright:

    - a bare number (``start_ts: 20240101``, an unquoted YAML scalar) is read
      by pydantic's ``datetime`` member as a UNIX timestamp — 20240101 seconds
      after the epoch, i.e. 1970-08-23. A wildly wrong window that validates.
    - a UTC offset (``2024-07-01T14:30:00+03:00``) would give an experiment
      two sources of truth for its timezone; the experiment's ``timezone``
      field is the only one.
    """
    if isinstance(value, datetime):
        # only the datetime branches reach the offset check below; both `date`
        # branches return early (a calendar day carries no offset)
        parsed: datetime = value
    elif isinstance(value, date):
        return value  # a calendar day; no offset to check
    elif isinstance(value, str):
        text = value.strip()
        try:
            # Sniff on shape, never on pydantic's union ordering: exactly
            # `YYYY-MM-DD` is a calendar day, anything longer is an instant.
            if len(text) == 10 and text.count("-") == 2:
                return date.fromisoformat(text)
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"{field}: {value!r} is not an ISO date (2024-07-01) or "
                f"timestamp (2024-07-01 14:30:00)"
            ) from exc
    else:
        raise ValueError(
            f"{field}: expected an ISO date or timestamp, got {type(value).__name__} "
            f"({value!r}) — quote it if YAML parsed it as a number"
        )
    if parsed.tzinfo is not None:
        raise ValueError(
            f"{field}: drop the UTC offset ({value!r}) — window timestamps are "
            "local wall-clock times interpreted in the experiment's `timezone`"
        )
    if parsed.microsecond:
        # Nothing downstream can carry sub-second precision: the rendered SQL
        # window formats to whole seconds, and `_ab_results.end_ts` is
        # DateTime64(3) on every backend — so a microsecond cutoff would be
        # stored rounded, never match the planned one, and re-plan forever.
        raise ValueError(
            f"{field}: drop the sub-second part ({value!r}) — window timestamps "
            "are whole seconds (the cadence grammar is too, and cutoffs persist "
            "at millisecond precision)"
        )
    return parsed


class CadenceSegment(BaseModel):
    """One segment of a coarsening cadence schedule: ``{every: 1h, until: 48h}``.

    ``until`` is an elapsed offset from the WINDOW START (``start_ts``) — not
    from ``interval_anchor``, which only decides where the lattice sits; the
    LAST segment may omit it (runs to the horizon).
    """

    every: int | str = Field(..., description="Cutoff step within this segment")
    until: int | str | None = Field(
        default=None,
        description="Segment end as an offset from start_ts (last segment: omit)",
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


class CohortCopyConfig(BaseModel):
    """Opt-in persisted cohort copy (m8-implementation-plan.md WP1; default off).

    When ``enabled``, exposures are persisted into ``_ab_exposures``
    incrementally — watermark on ``update_column``, closed-interval batches
    (the detectkit donor discipline); the knobs here parameterize that copy
    loop (declarative-config.md §2). When disabled (the default), metric SQL
    joins the deduped assignment source directly and nothing is persisted.
    ``batch_intervals_per_round_trip`` is measured in interval-*counts*, not
    row-counts.
    """

    enabled: bool = Field(default=False)
    update_column: str = Field(
        default="exposure_ts",
        description="Watermark column the incremental copy filters on",
    )
    batch_interval: int | str = Field(
        default="1d", description="Closed-interval batch step of the copy loop"
    )
    batch_intervals_per_round_trip: int = Field(
        default=30,
        gt=0,
        description="Batch intervals covered by one load round trip (interval count)",
    )
    maturity_delay: int | str = Field(
        default=0,
        description="Ignore source rows younger than now() - maturity_delay (0 = none)",
    )

    @field_validator("batch_interval")
    @classmethod
    def _batch_interval_parses(cls, v: int | str) -> int | str:
        Interval(v)  # raises ValueError on bad grammar / non-positive
        return v

    @field_validator("maturity_delay")
    @classmethod
    def _maturity_delay_parses(cls, v: int | str) -> int | str:
        # 0 = no delay; Interval rejects non-positive (the data_lag pattern).
        if v == 0:
            return v
        Interval(v)  # raises ValueError on bad grammar / non-positive
        return v

    def batch_interval_seconds(self) -> int:
        return Interval(self.batch_interval).seconds

    def maturity_delay_seconds(self) -> int:
        if self.maturity_delay == 0:
            return 0
        return Interval(self.maturity_delay).seconds


class AssignmentConfig(BaseModel):
    """The READ-ONLY exposure source — abkit does not randomize."""

    query: str | None = Field(default=None, description="Inline assignment SQL")
    query_file: Path | None = Field(default=None, description="Path to assignment SQL file")
    added_filters: str = Field(
        default="", description="Optional extra SQL fragment (must start with AND)"
    )
    # Named cohort_copy (not `copy`) — a field named `copy` shadows the
    # deprecated-but-present BaseModel.copy and pydantic warns at import time
    # (m8-implementation-plan.md §4 Q1, settled at WP1).
    cohort_copy: CohortCopyConfig = Field(
        default_factory=CohortCopyConfig,
        description="Opt-in persisted cohort copy + its incremental-load knobs",
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

    @model_validator(mode="after")
    def validate_cohort_copy_update_column(self) -> AssignmentConfig:
        # Cheap identifier-shaped sanity gate only — the real existence check
        # is the run-time column probe (m8-implementation-plan.md WP1 step 4).
        if self.cohort_copy.enabled and not self.cohort_copy.update_column.isidentifier():
            raise ValueError(
                "assignment.cohort_copy.update_column must be a plain column "
                f"identifier, got {self.cohort_copy.update_column!r}"
            )
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


class NotifyConfig(BaseModel):
    """Per-experiment notification routing (m12 NTF-1).

    Routing only — never a switch: sending at all is the ``abk run --notify``
    opt-in. ``channels`` NARROWS the profile's ``notification_channels`` roster
    (empty = every configured channel, the D1 default); ``on`` narrows the
    signal kinds this experiment ever sends, and a channel's own ``on`` narrows
    what it accepts — a kind must pass BOTH (intersection).
    """

    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(
        default_factory=list,
        description="Channel names from profiles.yml (empty -> every configured channel)",
    )
    mentions: list[str] = Field(
        default_factory=list, description="Handles to @-mention, rendered in the channel's syntax"
    )
    on: list[SignalKind] | None = Field(
        default=None, description="Signal kinds this experiment sends (None -> all kinds)"
    )
    cooldown_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Re-announce an UNCHANGED recurring condition (stale, calibration_red) "
            "after this many seconds; None (the default) announces each distinct "
            "condition once. Never consulted for a verdict — a verdict change "
            "always sends (D2)"
        ),
    )


class ExperimentConfig(BaseModel):
    """The experiment — see the module docstring and declarative-config.md §2."""

    name: str = Field(..., description="Globally unique experiment name (DB key)")
    description: str | None = Field(default=None)
    status: ExperimentStatus = Field(default="running")
    is_actual: bool = Field(default=True, description="Scheduled runs pick it up")
    tags: list[str] | None = Field(default=None)

    start_ts: date | datetime = Field(
        ...,
        description=(
            "PINNED left edge of every cumulative window. A bare date is local "
            "midnight of that day in `timezone`; a timestamp is that exact instant"
        ),
    )
    horizon_ts: date | datetime = Field(
        ...,
        description=(
            "Planner horizon — the EXCLUSIVE right edge of the last window "
            "(drives the power plan). A bare date is local midnight of that day, "
            "so `2024-07-15` means 'through the end of July 14'"
        ),
    )
    unit_key: str = Field(..., description="Randomization + default analysis unit")

    cadence: int | str | list[CadenceSegment] = Field(
        default="1d",
        description="Cumulative cutoff step: duration scalar or coarsening schedule",
    )
    interval_anchor: AnchorMode | date | datetime = Field(
        default="midnight",
        description=(
            "WHERE the cutoff lattice sits (cadence decides how far apart the "
            "points are): 'midnight' (default — local midnight in `timezone`, "
            "i.e. whole calendar days) | 'start' (count from start_ts) | an "
            "explicit timestamp to align to an external cycle. Cutoffs are "
            "anchor + k*cadence, kept strictly after start_ts"
        ),
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
        description="Interprets bare-date window edges, an explicit "
        "interval_anchor, and the DST-safe day lattice; storage is UTC",
    )

    assignment: AssignmentConfig = Field(...)
    alpha: float | None = Field(
        default=None, description="Experiment-level significance (None -> project default)"
    )
    correction: CorrectionKind | None = Field(default=None, description="None -> project default")
    guardrail_correction: GuardrailCorrectionKind | None = Field(
        default=None, description="m13 D8: None -> project default"
    )
    contrasts: ContrastSet = Field(
        default="all_pairs",
        description="m13 STAT-1b: which variant pairs this experiment claims. "
        "'all_pairs' (default, pre-0.8.0) computes and corrects for every "
        "C(g,2) pair; 'vs_control' computes only the g-1 contrasts against the "
        "first declared variant and divides alpha by g-1. Experiment-level "
        "only — it declares the design, not a project policy",
    )
    incremental_reads: bool | None = Field(
        default=None,
        description="m9 WP4: override project.compute.incremental_reads for "
        "this experiment (None -> project default). Never changes a persisted "
        "number — only whether eligible comparisons read _ab_unit_state.",
    )
    sequential: SequentialConfig = Field(default_factory=SequentialConfig)
    readout: ReadoutConfig = Field(default_factory=ReadoutConfig)
    #: m12 NTF-1. ABSENT means "no per-experiment routing" — under `--notify`
    #: every configured channel receives every kind (D1), so an operator who
    #: wired channels up in profiles.yml never has to touch experiment YAML.
    notify: NotifyConfig | None = Field(default=None)
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

    @field_validator("start_ts", "horizon_ts", mode="before")
    @classmethod
    def _parse_window_edge(cls, v: Any, info: Any) -> Any:
        return parse_window_scalar(v, info.field_name)

    @field_validator("interval_anchor", mode="before")
    @classmethod
    def _parse_interval_anchor(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip() in ("midnight", "start"):
            return v.strip()
        try:
            return parse_window_scalar(v, "interval_anchor")
        except ValueError as exc:
            # Every rejection path names all three forms — a bare
            # `interval_anchor: noon` otherwise reads as "not a timestamp",
            # never mentioning that two keywords exist.
            raise ValueError(
                f"interval_anchor: {v!r} is not a valid anchor — {_ANCHOR_FORMS}"
            ) from exc

    # ── model validators ─────────────────────────────────────────────────────

    @model_validator(mode="before")
    @classmethod
    def _reject_renamed_window_fields(cls, data: Any) -> Any:
        """0.5.0 renamed start_date/end_date — fail by name, never by silence.

        ``extra="ignore"`` (pydantic's default) would otherwise drop the old
        key and report a bare "Field required: start_ts", leaving the reader
        to guess that the VALUE also moves (horizon_ts is exclusive).

        **Every** renamed key present is reported in ONE error. Raising on the
        first one hid the message that matters: an 0.4.0 config carries both
        keys, ``start_date`` sorts first, and its note says the value carries
        over unchanged — so the operator never saw the ``end_date`` →
        ``horizon_ts`` ``+1 day`` instruction, renamed both keys mechanically,
        and got a window one day short that validates silently. The one
        genuinely silent-wrong-number path this rename can produce.
        """
        if not isinstance(data, dict):
            return data
        present = [
            (old, *_RENAMED_WINDOW_FIELDS[old]) for old in _RENAMED_WINDOW_FIELDS if old in data
        ]
        if present:
            renames = "; ".join(f"`{old}` → `{new}`: {note}" for old, new, note in present)
            raise ValueError(
                "renamed window field(s) (abkit 0.5.0 — experiment windows are "
                f"timestamps, not dates) — {renames}"
            )
        return data

    @model_validator(mode="after")
    def validate_window(self) -> ExperimentConfig:
        """The window must be non-empty, compared as resolved instants.

        Never compare the raw fields: a ``date`` start against a ``datetime``
        horizon raises ``TypeError`` in Python, and a mixed pair is legal
        config.
        """
        try:
            start, horizon = self.start_instant(), self.horizon_instant()
        except (OverflowError, OSError) as exc:
            # `astimezone` off the end of the representable calendar. pydantic
            # only wraps ValueError/AssertionError, so without this the raw
            # OverflowError escapes model_validate naming neither field.
            raise ValueError(
                f"start_ts ({self.start_ts}) / horizon_ts ({self.horizon_ts}) fall "
                f"outside the representable calendar in timezone {self.timezone!r}"
            ) from exc
        if horizon <= start:
            raise ValueError(
                f"horizon_ts ({self.horizon_ts}) is not after start_ts "
                f"({self.start_ts}) — the horizon is the EXCLUSIVE right edge, "
                "so a one-day experiment starting 2024-07-01 has "
                "horizon_ts: 2024-07-02"
            )
        return self

    @model_validator(mode="after")
    def validate_cadence_schedule(self) -> ExperimentConfig:
        """Schedule segments: non-overlapping, strictly coarsening, increasing until."""
        if not isinstance(self.cadence, list):
            if not self.cadence_fits_horizon():
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
        if not self.cadence_fits_horizon():
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
        # (scheme: alpha_spending is rejected globally in SequentialConfig — a future item.)
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

    def zone(self) -> zoneinfo.ZoneInfo:
        """The experiment timezone (validated at parse)."""
        return zoneinfo.ZoneInfo(self.timezone)

    def start_instant(self) -> datetime:
        """``start_ts`` as naive UTC — the same instant ``Grid.start_ts`` holds.

        The config field is a LOCAL wall-clock value; everything stored or
        compared downstream is naive UTC. Resolving through the planner's
        primitive keeps the two definitions from drifting.
        """
        return resolve_instant(self.start_ts, self.zone())

    def horizon_instant(self) -> datetime:
        """``horizon_ts`` as naive UTC — equals ``Grid.horizon_ts`` exactly."""
        return resolve_instant(self.horizon_ts, self.zone())

    def grid(self, *, limit: int | None = None) -> Grid:
        """THE experiment → grid factory: window + cadence + anchor, composed once.

        Every production consumer goes through this, mirroring m8's
        ``build_cohort_backend`` contract: ``generate_grid`` keeps its
        primitive signature (tests and purity want it), but nothing under
        ``abkit/`` outside this method may call it. A hand-copied argument
        list is exactly how a new planner knob gets silently dropped at eight
        call sites — and one of those sites passed ``timezone`` positionally,
        so inserting a parameter before ``tz`` would have re-bound it in
        silence. Pinned by ``tests/core/test_grid_factory_is_the_only_entry.py``.
        """
        from abkit.core.period_planner import generate_grid

        return generate_grid(
            self.start_ts,
            self.horizon_ts,
            self.cadence_segments(),
            tz=self.timezone,
            limit=limit,
            interval_anchor=self.interval_anchor,
        )

    def contrast_pairs(self) -> tuple[tuple[str, str], ...]:
        """THE experiment → variant-pair factory (m13 STAT-1b): the declared family.

        ``all_pairs`` reproduces ``combinations(variants, 2)`` exactly — same
        pairs, same order, so nothing about a pre-0.8.0 experiment moves.
        ``vs_control`` keeps only the pairs whose first element is the control
        (the first declared variant, baseline §5), which is a prefix-ordered
        SUBSET of the same sequence: the shared rows keep their identity and
        their order.

        Five call sites read this set — the analyze stage that PRODUCES the
        rows, and the four surfaces that filter persisted rows down to what is
        currently declared (report, dashboard, notify, and the alpha divisor).
        They must never disagree about family membership: a surface computing
        a wider family than the one the alphas paid for silently breaks the
        FWER claim, and one computing a narrower one drops rows nobody warned
        about. ``notify/dispatch.py`` predicted the extraction ("a fourth copy
        should force it"); STAT-1b is that fourth reason, and it arrives with a
        knob the copies could have resolved differently.

        Pinned by ``tests/config/test_contrast_pairs_is_the_only_entry.py``.
        """
        variants = self.assignment.variants
        if self.contrasts == "vs_control":
            control = variants[0]
            return tuple((control, treatment) for treatment in variants[1:])
        return tuple(itertools.combinations(variants, 2))

    def cadence_fits_horizon(self) -> bool:
        """Can the densest cadence step produce a cutoff inside the window?

        Two cheap accepts, then the planner as the authority — because with
        `interval_anchor` the answer is no longer a property of the step
        length alone.

        1. A whole-day step is measured in CALENDAR days, the space the
           planner steps in. Across a spring-forward a local day is 23h, and a
           seconds comparison would reject an ordinary one-day daily
           experiment ("cadence 1d is longer than the 82800s horizon") whose
           grid is byte-identical to what it always was.
        2. Any other step compares in seconds.
        3. If neither accepts, ENUMERATE. A step longer than the window can
           still land a cutoff inside it when the lattice is anchored
           elsewhere (`36h` steps off local midnight, a window opening at
           04:00 — the point at midnight+36h falls inside). Arithmetic cannot
           see that; the grid can. Only reached when the cheap checks say
           "too long", so the grid enumerated here is always tiny.
        """
        step = self.cadence_seconds_min()
        if step % DAY_SECONDS == 0:
            span_days = (
                as_local_datetime(self.horizon_ts).date() - as_local_datetime(self.start_ts).date()
            ).days
            if step // DAY_SECONDS <= span_days:
                return True
        elif step <= self.horizon_seconds():
            return True
        return any(not cutoff.is_horizon for cutoff in self.grid().cutoffs)

    def horizon_seconds(self) -> int:
        """Length of the full experiment window: ``[start_ts, horizon_ts)``.

        Measured between resolved instants, so a DST transition inside the
        window makes it legitimately 23h or 25h longer than a day count.
        """
        return int(round((self.horizon_instant() - self.start_instant()).total_seconds()))

    def cadence_canonical_json(self) -> str:
        """Canonical JSON for the ``_ab_experiments`` catalog (always a segment list)."""
        return json_dumps_sorted(
            [{"every": every, "until": until} for every, until in self.cadence_segments()]
        )

    def main_metrics(self) -> list[str]:
        return [c.metric for c in self.comparisons if c.is_main_metric]

    def declares_metric(self, metric: str) -> bool:
        """Does a comparison of this experiment bind ``metric``? (m11 DASH-4a)

        THE predicate behind ``abk run --metric``'s selection narrowing — and
        the one DASH-4's ``POST /api/run`` validates its optional ``metric``
        body field with, so the CLI and the dashboard cannot drift on what a
        valid per-metric target is. A metric binds at most once per experiment
        (:meth:`validate_comparisons`), so a true answer means exactly one
        comparison.
        """
        return any(comparison.metric == metric for comparison in self.comparisons)

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
            # Resolved instants, not the raw local fields: every other
            # timestamp column in the warehouse is naive UTC, and a BI join
            # onto _ab_results.start_ts must line up rather than differ by the
            # timezone offset. `timezone` is right there to convert back.
            "start_ts": self.start_instant(),
            "horizon_ts": self.horizon_instant(),
            "unit_key": self.unit_key,
            "cadence": self.cadence_canonical_json(),
            # ISO, not str(): a `date` and a midnight `datetime` anchor
            # resolve to the same instant but stringify differently. The
            # column is INFORMATIONAL — hash `grid.anchor_ts`, never this
            # string, if something needs "did the lattice change?".
            "interval_anchor": (
                self.interval_anchor
                if isinstance(self.interval_anchor, str)
                else self.interval_anchor.isoformat()
            ),
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
