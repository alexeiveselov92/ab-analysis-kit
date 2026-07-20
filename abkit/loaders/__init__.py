"""Loaders: Jinja templating + the packaged macro + exposure/metric loading."""

from abkit.loaders.exposure_loader import load_exposures, persist_snapshot
from abkit.loaders.exposure_source import (
    EmptyCohortError,
    ExposureLoadError,
    ExposureSnapshot,
    build_cohort_backend,
    probe_has_stratum,
    render_assignment_sql,
    validate_and_snapshot,
)
from abkit.loaders.metric_loader import (
    MetricLoadError,
    MetricLoadResult,
    load_covariate_from_preperiod,
    load_metric,
)
from abkit.loaders.query_template import (
    QueryTemplate,
    RenderWindow,
    TemplateRenderError,
    build_builtins,
)

__all__ = [
    "EmptyCohortError",
    "ExposureLoadError",
    "ExposureSnapshot",
    "MetricLoadError",
    "MetricLoadResult",
    "QueryTemplate",
    "RenderWindow",
    "TemplateRenderError",
    "build_builtins",
    "build_cohort_backend",
    "load_covariate_from_preperiod",
    "load_exposures",
    "load_metric",
    "persist_snapshot",
    "probe_has_stratum",
    "render_assignment_sql",
    "validate_and_snapshot",
]
