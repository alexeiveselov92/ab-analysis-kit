"""Config validation.

Level 1: discovery + name uniqueness — parameterized by (directory, config
class) so experiments and metrics share one engine.

Level 2 (:func:`validate_level2`): the full declarative-config.md §8 matrix —
reference integrity, method-params-by-instantiation, CUPED covariate rules,
the cadence/looks gates (through the SAME grid enumeration the planner uses —
plan R1), and the no-DB SQL render smoke incl. the macro-usage lint. Runs
under ``abk run --steps validate`` without touching any database.

Namespace rule (cli-and-dx.md §1): experiment and metric names share ONE
namespace, so every uniqueness error names both files and the namespace —
detectkit users will assume the one-level model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from abkit.config.experiment_config import DAY_SECONDS, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.interval import Interval
from abkit.core.period_planner import GridLimitExceeded, generate_grid


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


# ── level 2: the declarative-config.md §8 matrix ─────────────────────────────


@dataclass
class ValidationReport:
    """Collected level-2 findings; errors block, warnings are surfaced."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: ValidationReport) -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def validate_experiment_level2(
    experiment: ExperimentConfig,
    metrics_by_name: dict[str, MetricConfig],
    project: ProjectConfig,
    project_root: Path | None = None,
    experiment_path: Path | None = None,
) -> ValidationReport:
    """The §8 battery for one experiment (no DB, no compute)."""
    report = ValidationReport()
    where = f"experiment '{experiment.name}'" + (f" ({experiment_path})" if experiment_path else "")

    # ── reference integrity + per-comparison method/metric rules ────────────
    for comparison in experiment.comparisons:
        label = f"{where}, comparison '{comparison.metric}'"
        metric = metrics_by_name.get(comparison.metric)
        if metric is None:
            report.errors.append(
                f"{label}: no metric named '{comparison.metric}' in the metrics/ "
                f"library (known: {sorted(metrics_by_name)})"
            )
            continue

        # unit-key consistency (omitted metric unit_key inherits)
        if metric.unit_key is not None and metric.unit_key != experiment.unit_key:
            report.errors.append(
                f"{label}: metric.unit_key '{metric.unit_key}' != experiment."
                f"unit_key '{experiment.unit_key}' (must match or be omitted)"
            )

        # method params validated BY INSTANTIATION (one path; quarantine and
        # bad params fail here, at validate time, not at run time)
        try:
            comparison.method.bind(alpha=project.statistics.alpha)
        except Exception as exc:
            report.errors.append(f"{label}: method '{comparison.method.name}': {exc}")
            continue

        # capability lint (plan R8): the same declarative attributes analyze.py
        # dispatches on must gate at VALIDATE time, not at run time
        from abkit.stats import get_method_class

        method_cls = get_method_class(comparison.method.name)
        if method_cls.is_paired:
            report.errors.append(
                f"{label}: '{comparison.method.name}' is a paired design — "
                "the v1 pipeline serves independent-arm experiments "
                "(use the notebook API for paired data)"
            )
            continue
        if method_cls.input_kind != metric.type:
            report.errors.append(
                f"{label}: method '{comparison.method.name}' expects a "
                f"'{method_cls.input_kind}' metric, got '{metric.type}'"
            )
            continue

        # CUPED covariate rules (statistics-changes §5; cumulative-intervals §6.5)
        lookback = comparison.method.covariate_lookback
        is_cuped = "cuped" in comparison.method.name
        if is_cuped:
            if metric.type != "sample":
                report.errors.append(
                    f"{label}: CUPED needs a 'sample' metric (the covariate is the "
                    f"same metric over the pre-period), got type '{metric.type}'"
                )
            if lookback is None and metric.columns.covariate is None:
                report.errors.append(
                    f"{label}: CUPED needs a covariate — set method params."
                    "covariate_lookback (pre-period render) or a metric "
                    "columns.covariate"
                )
        # (a non-CUPED method with covariate_lookback already failed bind()
        # above — only CUPED methods declare the param in their schema)
        if lookback is not None:
            try:
                lookback_seconds = Interval(lookback).seconds
            except (ValueError, TypeError) as exc:
                report.errors.append(f"{label}: bad covariate_lookback: {exc}")
            else:
                if lookback_seconds < DAY_SECONDS:
                    report.errors.append(
                        f"{label}: covariate_lookback < 1d is an error (the fixed "
                        "lookback is whole days — statistics-changes §5)"
                    )
                elif lookback_seconds % DAY_SECONDS != 0:
                    report.errors.append(
                        f"{label}: covariate_lookback must be WHOLE days "
                        "(statistics-changes §5), got a fractional-day duration"
                    )
                elif lookback_seconds < 7 * DAY_SECONDS:
                    report.warnings.append(
                        f"{label}: covariate_lookback < 7d — the covariate won't "
                        "cover a weekly cycle (diurnal/weekday confounding)"
                    )

    # ── cadence & looks gates (through the planner's own enumeration) ───────
    try:
        grid = generate_grid(
            experiment.start_date,
            experiment.end_date,
            experiment.cadence_segments(),
            tz=experiment.timezone,
            limit=project.limits.max_looks,
        )
    except GridLimitExceeded as exc:
        report.errors.append(f"{where}: {exc}")
        grid = None
    if grid is not None and len(grid) > project.limits.warn_looks:
        if not experiment.sequential.enabled:
            report.warnings.append(
                f"{where}: {len(grid)} planned looks with fixed-horizon CIs — "
                f"peeking risk (warn_looks={project.limits.warn_looks}). "
                "sequential: {enabled: true, scheme: always_valid} lets you "
                "decide earlier at any look; `abk validate` measures this "
                "grid's real A/A FPR."
            )
    for every, _ in experiment.cadence_segments():
        if every < DAY_SECONDS and DAY_SECONDS % every != 0:
            report.warnings.append(
                f"{where}: 24h is not a multiple of the {every}s cadence — the "
                "grid drifts across midnight (diurnal composition oscillates "
                "across looks; daily BI rollups misalign)"
            )

    # ── SQL render smoke (StrictUndefined, no DB) + macro-usage lint ────────
    report.extend(_render_smoke(experiment, metrics_by_name, project_root))

    return report


def _render_smoke(
    experiment: ExperimentConfig,
    metrics_by_name: dict[str, MetricConfig],
    project_root: Path | None,
) -> ValidationReport:
    """Render every SQL under fixture built-ins; assert the macro join."""
    from abkit.loaders.query_template import QueryTemplate, RenderWindow, build_builtins

    report = ValidationReport()
    template = QueryTemplate()
    from datetime import datetime, timedelta

    start = datetime.combine(experiment.start_date, datetime.min.time())
    builtins = build_builtins(
        experiment_id=experiment.name,
        unit_key=experiment.unit_key,
        variants=experiment.assignment.variants,
        added_filters=experiment.assignment.added_filters,
        window=RenderWindow(start_ts=start, end_ts=start + timedelta(days=1)),
        data_database="__data__",
        internal_database="__internal__",
        exposures_table="_ab_exposures",
        dialect="clickhouse",
    )

    try:
        assignment_sql = experiment.assignment.get_query_text(project_root)
        rendered = template.render(assignment_sql, builtins)
    except Exception as exc:
        report.errors.append(f"experiment '{experiment.name}': assignment SQL: {exc}")
    else:
        for token in (experiment.unit_key, "variant", "exposure_ts"):
            if token not in rendered:
                report.errors.append(
                    f"experiment '{experiment.name}': assignment SQL must SELECT "
                    f"'{token}' (unit_key, variant, exposure_ts are the exposure "
                    "contract)"
                )

    for comparison in experiment.comparisons:
        metric = metrics_by_name.get(comparison.metric)
        if metric is None:
            continue  # already reported
        try:
            metric_sql = metric.get_query_text(project_root)
            rendered = template.render(metric_sql, builtins)
        except Exception as exc:
            report.errors.append(f"metric '{comparison.metric}': {exc}")
            continue
        if "_abk_exposures" not in rendered:
            report.errors.append(
                f"metric '{comparison.metric}': rendered SQL does not join the "
                "persisted cohort — import the packaged macro "
                "({% import 'abkit_assignment.jinja' as ab %} … "
                "{{ ab.exposed_units() }}); hand-rolled cohort logic fails "
                "config-lint"
            )

    return report


def validate_level2(
    project_root: Path,
    project: ProjectConfig,
    experiments: list[tuple[Path, ExperimentConfig]],
    metrics: list[tuple[Path, MetricConfig]],
) -> ValidationReport:
    """Run the §8 battery over every experiment (``abk run --steps validate``)."""
    metrics_by_name = {config.name: config for _, config in metrics}
    report = ValidationReport()
    for path, experiment in experiments:
        report.extend(
            validate_experiment_level2(experiment, metrics_by_name, project, project_root, path)
        )
    return report
