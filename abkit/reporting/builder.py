"""Assemble the experiment report payload from persisted ``_ab_results`` rows.

One JSON-serializable payload per **experiment** — the shared contract the
offline HTML readout (WP3) and the explore cockpit (WP6/WP7) both consume
(data-contract-and-reporting.md §5.3; m3-implementation-plan.md D6). Kept in
documented lockstep with the renderer-side ``web/src/shared/payload.ts``
(WP3) — same keys, same units.

The payload derives from **stored** rows (including their persisted
``method_params``/``alpha``/``srm_flag``), not from re-evaluating what the
current YAML would produce — the report shows *what actually ran* (the donor
doctrine). Header metadata comes from the experiment config (the truth);
``_ab_experiments`` is informational only and never read here.

Units and null discipline (§5.3): timestamps are integer ms-epoch (UTC);
every nullable numeric passes through :func:`_num_or_none`, so NaN **and
±inf** become JSON ``null`` (H5 zero-denominator NaNs; the verdict's
``pair_mde`` ``math.inf`` = "configured but unavailable" — the rationale
strings carry the explanation). No NaN, numpy scalar, or datetime object ever
reaches the returned dict.

Cost discipline: per-point MDE reads the **stored** columns only — the
read-time D5(b) solve runs once per pair on the latest cutoff (the verdict),
never per historical point (a statsmodels root-solve × O(cutoffs) would cost
seconds-to-minutes at sub-day cadence; review finding).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from itertools import combinations
from typing import Any

import numpy as np

from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.period_planner import generate_grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.pipeline.readout import ExperimentReadout, PairVerdict, evaluate, srm_summary
from abkit.utils.datetime_utils import to_naive_utc
from abkit.utils.json_utils import json_loads

#: the payload schema version (§5.3) — bump on breaking key/unit changes
PAYLOAD_VERSION = 1

#: total point budget across metrics × pairs × cutoffs; exceeding it clips
#: every series to its trailing window and adds a top-level payload warning
#: (the donor capped at 1500 points for one series; an experiment payload
#: carries many series, so the budget is global)
REPORT_POINT_BUDGET = 20_000


def _ms(value: Any) -> int:
    """Coerce a datetime / datetime64 to integer ms-epoch (UTC)."""
    if isinstance(value, datetime):
        value = to_naive_utc(value)
    return int(np.datetime64(value, "ms").astype("int64"))


def _num_or_none(value: Any) -> float | None:
    """Pass a stored Nullable number through; NaN/±inf/None become ``None``.

    The inf clause extends the donor helper: ``pair_mde`` uses ``math.inf``
    for "``calculate_mde: true`` but columns are NULL", and JSON ``Infinity``
    is invalid in browsers — serialization only, no statistical number
    changes.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _flag01(value: Any) -> int:
    """A stored Bool cell (bool / 0-1 int / numpy) as a terse 0/1 point flag."""
    return 1 if value else 0


def _reject_flag(value: Any) -> int | None:
    """``reject`` is Nullable(Bool): None = inference withheld (demoted)."""
    if value is None:
        return None
    return 1 if value else 0


def _parse_json_cell(value: Any) -> Any:
    """Parse a stored canonical-JSON cell (str | None); never raises."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json_loads(value)
    except (TypeError, ValueError):
        return None


def _stored_pair_mde(row: dict) -> float | None:
    """The per-point pair MDE from the **stored** columns only (D5(b) combine).

    Deliberately no read-time ``pair_mde`` solve here: that runs a statsmodels
    root-solve per t/z row, and cumulative windows give every cutoff a unique
    ``(size, ratio)`` — so the whole series would cost O(cutoffs) solves
    (seconds-to-minutes at sub-day cadence; review finding). The point series
    honestly shows *what actually ran*: MDE where the row computed it
    (``calculate_mde: true``), null otherwise. The read-time D5(b) fallback
    stays where the FLAT decision needs it — the **verdict**, one solve per
    pair on the latest cutoff (readout ``pair_mde``).

    The **both-present guard** mirrors readout ``pair_mde``: enrich NULLs a
    non-finite solve, so a half-present pair means one arm is undetectable —
    taking the finite arm alone would fake adequate power (the exact trap the
    verdict path guards; review finding). Half-present ⇒ null, never the
    finite arm."""
    mde_1, mde_2 = _num_or_none(row.get("mde_1")), _num_or_none(row.get("mde_2"))
    if mde_1 is None or mde_2 is None:
        return None
    return max(abs(mde_1), abs(mde_2))


def _point(row: dict) -> dict:
    """One ``_ab_results`` row → the terse §5.3 series point.

    ``mde`` is the per-point pair MDE from the stored columns (:func:
    `_stored_pair_mde`). Demoted rows pass their NULL test columns through as
    nulls; sizes stay real.
    """
    return {
        "t": _ms(row["end_ts"]),
        "ed": _num_or_none(row.get("elapsed_days")),
        "e": _num_or_none(row.get("effect")),
        "lo": _num_or_none(row.get("left_bound")),
        "hi": _num_or_none(row.get("right_bound")),
        "p": _num_or_none(row.get("pvalue")),
        "rj": _reject_flag(row.get("reject")),
        "s1": int(row.get("size_1") or 0),
        "s2": int(row.get("size_2") or 0),
        "mde": _stored_pair_mde(row),
        "hz": _flag01(row.get("is_horizon")),
        "blk": _flag01(row.get("decision_blocked")),
        "ins": _flag01(row.get("insufficient_data")),
    }


def _verdict_to_payload(verdict: PairVerdict) -> dict:
    """A WP1 :class:`PairVerdict` → the JSON-safe §5.3 verdict entry."""
    return {
        "metric": verdict.metric,
        "pair": {"c": verdict.name_1, "t": verdict.name_2},
        "verdict": verdict.verdict,
        "rationale": list(verdict.rationale),
        "caveats": list(verdict.caveats),
        "significant": bool(verdict.significant),
        "effect": _num_or_none(verdict.effect),
        "pvalue": _num_or_none(verdict.pvalue),
        "lo": _num_or_none(verdict.left_bound),
        "hi": _num_or_none(verdict.right_bound),
        "alpha": _num_or_none(verdict.alpha),
        "mde": _num_or_none(verdict.mde),
        "min_effect": _num_or_none(verdict.min_effect),
        "end_ts": _ms(verdict.end_ts) if verdict.end_ts is not None else None,
        "elapsed_days": _num_or_none(verdict.elapsed_days),
        "is_horizon": bool(verdict.is_horizon),
        "guardrails": [
            {
                "metric": g.metric,
                "pair": {"c": g.name_1, "t": g.name_2},
                "regressed": bool(g.regressed),
                "effect": _num_or_none(g.effect),
                "desired_direction": g.desired_direction,
            }
            for g in verdict.guardrails
        ],
    }


def _window_filter(rows: list[dict], start: datetime | None, end: datetime | None) -> list[dict]:
    """Keep rows with ``start <= end_ts <= end`` (both bounds inclusive).

    Pinning ``end`` before the latest cutoff replays the report as-of that
    moment — the readout then verdicts on the historical state (the donor's
    replay stance).
    """
    if start is None and end is None:
        return rows
    kept = []
    for row in rows:
        end_ts = row["end_ts"]
        if start is not None and end_ts < start:
            continue
        if end is not None and end_ts > end:
            continue
        kept.append(row)
    return kept


def _metric_entry(
    comparison: ComparisonConfig,
    rows: list[dict],
    variants: list[str],
    metric_configs: Mapping[str, MetricConfig] | None,
) -> dict:
    """One configured comparison → the §5.3 ``metrics[]`` entry.

    ``description`` comes from the metric YAML config (D6 — ``_ab_experiments``
    stores only the experiment description). ``method``/``query``/``alpha``
    prefer the latest stored row (what actually ran); config values are the
    fallback for a never-run comparison. Provenance is deduped to one
    ``query`` entry per metric; the rendered SQL never enters the payload.
    """
    metric_config = (metric_configs or {}).get(comparison.metric)

    by_pair: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        by_pair.setdefault((str(row["name_1"]), str(row["name_2"])), []).append(row)

    latest: dict | None = None
    for group in by_pair.values():
        candidate = group[-1]
        if latest is None or candidate["end_ts"] > latest["end_ts"]:
            latest = candidate

    method_params = None
    if latest is not None:
        method_params = _parse_json_cell(latest.get("method_params"))
    if method_params is None:
        method_params = _parse_json_cell(comparison.method.canonical_params_json) or {}

    warnings: list[str] = []
    seen_warnings: set[str] = set()
    for row in rows:
        for warning in _parse_json_cell(row.get("warnings")) or []:
            text = str(warning)
            if text not in seen_warnings:
                seen_warnings.add(text)
                warnings.append(text)

    pairs = []
    for name_1, name_2 in combinations(variants, 2):
        group = by_pair.get((name_1, name_2), [])
        pairs.append(
            {
                "c": name_1,
                "t": name_2,
                "series": [_point(row) for row in group],
                "diag": _parse_json_cell(group[-1].get("diagnostics")) if group else None,
            }
        )

    return {
        "name": comparison.metric,
        "description": metric_config.description if metric_config is not None else None,
        "main": bool(comparison.is_main_metric),
        "guardrail": bool(comparison.is_guardrail),
        "method": {
            "name": comparison.method.name,
            "params": method_params,
            "id": comparison.method.method_config_id,
            "alpha": _num_or_none(latest.get("alpha")) if latest is not None else None,
        },
        "query": str(latest.get("metric_query")) if latest is not None else None,
        "pairs": pairs,
        "warnings": warnings,
    }


def _clip_to_budget(metrics: list[dict], max_points: int) -> str | None:
    """Enforce the global point budget by trailing-window truncation.

    Runs AFTER the readout evaluated the full series — clipping affects only
    what gets baked, never the verdict. Returns the payload warning when
    anything was dropped (no silent caps)."""
    series_list = [pair["series"] for metric in metrics for pair in metric["pairs"]]
    non_empty = [s for s in series_list if s]
    total = sum(len(s) for s in non_empty)
    if total <= max_points or not non_empty:
        return None
    allowed = max(1, max_points // len(non_empty))
    dropped = 0
    for metric in metrics:
        for pair in metric["pairs"]:
            series = pair["series"]
            if len(series) > allowed:
                dropped += len(series) - allowed
                pair["series"] = series[-allowed:]
    return (
        f"payload clipped to the trailing {allowed} cutoffs per series "
        f"({total} points exceeded the {max_points}-point budget; "
        f"{dropped} older points dropped — pin start/end to window explicitly)"
    )


def build_report_payload(
    experiment: ExperimentConfig,
    tables: InternalTablesManager,
    *,
    project: ProjectConfig | None = None,
    metric_configs: Mapping[str, MetricConfig] | None = None,
    generated_at: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    max_points: int = REPORT_POINT_BUDGET,
) -> dict:
    """Read ``_ab_results`` for one experiment and assemble the §5.3 payload.

    ``start``/``end`` bound the report window on ``end_ts`` (both inclusive);
    pinning ``end`` replays the readout as-of a historical cutoff.
    ``generated_at`` is a caller-supplied preformatted string — the builder
    never reads the wall clock (determinism; the CLI owns formatting).
    ``project`` resolves the effective correction for the readout (BH is
    read-time) and names the payload; ``metric_configs`` supplies the metric
    descriptions (D6). On a never-run project (no ``_ab_results`` table) every
    key is still present with empty series — reporting never creates schema.
    """
    if start is not None:
        start = to_naive_utc(start)
    if end is not None:
        end = to_naive_utc(end)

    grid = generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        experiment.timezone,
    )
    variants = list(experiment.assignment.variants)

    declared_pairs = set(combinations(variants, 2))
    ready = tables.results_table_exists()
    declared_by_comparison: dict[str, list[dict]] = {}
    rows_by_comparison: dict[str, list[dict]] = {}
    stale_by_metric: dict[str, int] = {}
    for comparison in experiment.comparisons:
        loaded = (
            tables.load_results(
                experiment.name, comparison.metric, comparison.method.method_config_id
            )
            if ready
            else []
        )
        # pairs outside the declared variants (a mid-flight arm rename) are
        # not chartable; drop them from EVERY payload surface — series, look,
        # period, latest-row method block — loudly, never silently
        declared = [
            row for row in loaded if (str(row["name_1"]), str(row["name_2"])) in declared_pairs
        ]
        if len(declared) != len(loaded):
            stale_by_metric[comparison.metric] = len(loaded) - len(declared)
        declared_by_comparison[comparison.metric] = declared
        # the chart + verdict are as-of the window; the SRM block is not
        rows_by_comparison[comparison.metric] = _window_filter(declared, start, end)

    charted_rows = [row for rows in rows_by_comparison.values() for row in rows]
    declared_rows = [row for rows in declared_by_comparison.values() for row in rows]
    readout: ExperimentReadout = evaluate(experiment, charted_rows, project=project)

    # SRM block — CURRENT experiment health, window-independent (§6 "SRM loud"):
    # flag/pvalue come from the latest persisted row overall (not the latest
    # *charted* row), so a pinned/empty replay never silences a failing gate,
    # and they stay coherent with the whole-cohort observed counts below. M2
    # SRM is one whole-experiment check (per-cutoff SRM = M5 sequential).
    srm_flag, srm_pvalue = srm_summary(experiment, declared_rows)
    observed_counts: dict[str, int] = {}
    if ready and tables.exposures_table_exists():
        observed_counts = tables.get_exposure_counts(experiment.name)
    # zero-fill declared variants, mirroring the driver's SRM gate input
    observed = {variant: int(observed_counts.get(variant, 0)) for variant in variants}

    metrics = [
        _metric_entry(comparison, rows_by_comparison[comparison.metric], variants, metric_configs)
        for comparison in experiment.comparisons
    ]

    warnings = list(readout.warnings)
    for metric_name, dropped in stale_by_metric.items():
        warnings.append(
            f"{experiment.name}/{metric_name}: {dropped} persisted rows are for "
            "variant pairs outside the declared variants (renamed arms?) — not "
            "charted"
        )
    if ready:
        # the driver's orphan scan, surfaced on the read path too: an edited
        # method leaves the old series in _ab_results and the report would
        # otherwise show a silently truncated history
        stored_ids = tables.list_method_config_ids(experiment.name)
        for comparison in experiment.comparisons:
            orphaned = {
                mc_id for (metric_name, mc_id) in stored_ids if metric_name == comparison.metric
            } - {comparison.method.method_config_id}
            if orphaned:
                warnings.append(
                    f"{experiment.name}/{comparison.metric}: {len(orphaned)} orphaned "
                    "method_config_id series in _ab_results (the BI chart will "
                    "show duplicate stabilization lines) — run `abk clean`"
                )
    clip_warning = _clip_to_budget(metrics, max_points)
    if clip_warning is not None:
        warnings.append(clip_warning)

    # look counter (§4): n = charted cutoffs where inference actually happened
    # (at least one non-demoted row); planned = the one-enumeration grid length
    informative_cutoffs = {
        row["end_ts"] for row in charted_rows if not row.get("insufficient_data")
    }
    latest_end_ts = max((row["end_ts"] for row in charted_rows), default=None)

    return {
        "v": PAYLOAD_VERSION,
        "experiment": experiment.name,
        "project": project.name if project is not None else None,
        "generated_at": generated_at,
        "description": experiment.description,
        "period": {
            "start": _ms(grid.start_ts),
            # 0 = no persisted cutoffs (the donor's empty sentinel); start
            # and horizon stay real — they are config facts, not data facts
            "end": _ms(latest_end_ts) if latest_end_ts is not None else 0,
            "horizon": _ms(grid.horizon_ts),
        },
        "cadence_seconds": experiment.cadence_seconds_min(),
        "tz": experiment.timezone,
        "arms": variants,
        "srm": {
            "flag": bool(srm_flag),
            "pvalue": _num_or_none(srm_pvalue),
            "observed": observed,
            "expected": {
                variant: float(split)
                for variant, split in experiment.assignment.expected_split.items()
            },
        },
        # null until M4; the M4 shape (fpr, peeking_fpr, headline, matrix_rows,
        # report_link) is documented in §5.3 so M4 fills it without a v-bump
        "calibration": None,
        "verdicts": [_verdict_to_payload(v) for v in readout.verdicts],
        "metrics": metrics,
        "look": {"n": len(informative_cutoffs), "planned": len(grid)},
        # injected by the explore server at serve time; null in a baked report
        "endpoints": {
            "save_url": None,
            "recompute_url": None,
            "reload_url": None,
            "validate_url": None,
        },
        "warnings": warnings,
    }
