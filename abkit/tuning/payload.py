"""The explore payload: the WP2 experiment payload + explore extras (WP6, D6).

Thin by design (the donor's series/window logic is superseded by the WP2
builder + the WP4 engine): the report payload rides verbatim — the report
renderer ignores unknown keys, the explore client reads the ``explore`` block
— extended with the knob surfaces auto-derived from ``param_specs`` (D12),
the per-metric initial calibration state (D3), the session-cache facts, and
the endpoint slots a server injects post-bind (``None`` in the static
``--no-serve`` page — the client's preview-badge substrate, D3 gating).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, get_args

import numpy as np

from abkit.config.experiment_config import CorrectionKind
from abkit.tuning.recompute import RecomputeEngine, find_calibration, resolve_fpr_budget
from abkit.tuning.session import ExploreSession
from abkit.utils.datetime_utils import to_naive_utc

#: endpoint slots the server fills post-bind; null = static preview (D3)
ENDPOINT_SLOTS = ("save_url", "recompute_url", "reload_url", "validate_url")


def _ms(value: datetime) -> int:
    """ms-epoch UTC — the §5.3 point-time unit (builder parity)."""
    return int(np.datetime64(to_naive_utc(value), "ms").astype("int64"))


def _jsonable_surface(surface: dict[str, Any]) -> dict[str, Any]:
    """The knob surface with its datetimes as ms-epoch ints (JSON-safe)."""
    out = dict(surface)
    cache = dict(out.get("cache") or {})
    for key in ("cutoffs", "covariate_cutoffs"):
        cache[key] = [_ms(ts) for ts in cache.get(key, [])]
    out["cache"] = cache
    return out


def build_explore_payload(
    session: ExploreSession,
    engine: RecomputeEngine,
    report_payload: dict[str, Any],
) -> dict[str, Any]:
    """Wrap one experiment's report payload with the explore block.

    Pure over the session (no DB): knob surfaces, the initial calibration chip
    per metric — keyed by the CONFIGURED ``(method_config_id, alpha)``, D3;
    every ``/recompute`` reply re-keys it live — and the cache facts. All keys
    are present even for an empty-results experiment (the WP2 empty-state
    contract extends here: the client renders an empty state, never crashes).
    """
    metrics: dict[str, Any] = {}
    for name, series in session.series_by_metric.items():
        surface = _jsonable_surface(engine.knob_surface(name))
        alpha = series.configured_alpha
        calibration = find_calibration(
            session.aa_rows,
            name,
            series.comparison.method.method_config_id,
            alpha,
            budget=resolve_fpr_budget(session.project, alpha, series.metric),
        )
        surface["calibration"] = {
            "state": calibration.state,
            "fpr": calibration.fpr,
            "peeking_fpr": calibration.peeking_fpr,
            "calibrated_alpha": calibration.calibrated_alpha,
            "alpha": calibration.alpha,
            "budget": calibration.budget,
            "over_budget": calibration.over_budget,
            "runs": calibration.runs,
            "headline": calibration.headline,
        }
        metrics[name] = surface

    default_metric = next(
        (
            name
            for name, series in session.series_by_metric.items()
            if series.comparison.is_main_metric
        ),
        next(iter(session.series_by_metric), None),
    )

    # The experiment-level knob substrate (WP7): the client renders the raw
    # alpha/correction knobs and mirrors analyze.effective_alphas to resolve
    # them into the EFFECTIVE per-comparison alpha every /recompute sends
    # (KnobState.alpha) — that mirror needs the resolved raw values and the
    # two-tier counts, which the report payload does not carry.
    experiment = session.experiment
    project = session.project
    payload = dict(report_payload)
    payload["explore"] = {
        "metrics": metrics,
        "default_metric": default_metric,
        "experiment": {
            "alpha": (
                experiment.alpha if experiment.alpha is not None else project.statistics.alpha
            ),
            "correction": (
                experiment.correction
                if experiment.correction is not None
                else project.statistics.correction
            ),
            "correction_choices": list(get_args(CorrectionKind)),
            "groups_count": len(experiment.assignment.variants),
            "non_main_count": sum(1 for c in experiment.comparisons if not c.is_main_metric),
        },
        "cache": {
            "values": session.cache_values,
            "disabled_reason": session.cache_disabled_reason,
        },
        "warnings": list(session.warnings),
    }
    for slot in ENDPOINT_SLOTS:
        payload.setdefault(slot, None)
    return payload
