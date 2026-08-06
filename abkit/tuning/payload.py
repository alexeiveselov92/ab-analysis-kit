"""The explore payload: the WP2 experiment payload + explore extras (WP6, D6).

Thin by design (the donor's series/window logic is superseded by the WP2
builder + the WP4 engine): the report payload rides verbatim — the report
renderer ignores unknown keys, the explore client reads the ``explore`` block
(m14 DEC-3 filtered ``verdicts`` here for one WP; DEC-4 released it once
Review mode could label a role). Extended with the knob surfaces from ``param_specs`` (D12),
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
            "peeking_fpr_sequential": calibration.peeking_fpr_sequential,
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
    _guardrail_correction = (
        experiment.guardrail_correction
        if experiment.guardrail_correction is not None
        else project.statistics.guardrail_correction
    )
    # m14 DEC-4 released the DEC-3 hold: the report payload rides VERBATIM
    # again, treatment-vs-treatment verdicts included, because Review mode now
    # labels the role and renders the rollup line beside them. Do not re-add a
    # `ship_decisions` filter here — it would silently undo that.
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
            # m13 STAT-1b: `groups_count` alone no longer fixes the divisor —
            # the client must know WHICH family was declared, or a vs_control
            # experiment's live alpha would be C(g,2)-corrected on the page and
            # (g−1)-corrected on every server that answers it.
            "contrasts": experiment.contrasts,
            # m13 D8 (STAT-1c): the client's effectiveAlpha mirrors
            # analyze.effective_alphas, so it needs the RULE, not a resolved
            # number — the operator drags alpha/correction live. Both halves ride
            # along: the mode, and a non_main_count that ALREADY drops guardrails
            # under 'none' (the per-metric guardrail flag is already in each
            # metric block, from reporting/builder.py).
            "guardrail_correction": _guardrail_correction,
            "non_main_count": sum(
                1
                for c in experiment.comparisons
                if not c.is_main_metric and not (_guardrail_correction == "none" and c.is_guardrail)
            ),
        },
        "cache": {
            "values": session.cached_value_count(),
            "disabled_reason": session.cache_disabled_reason,
        },
        "warnings": list(session.warnings),
    }
    for slot in ENDPOINT_SLOTS:
        payload.setdefault(slot, None)
    return payload
