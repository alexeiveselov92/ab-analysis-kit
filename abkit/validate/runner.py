"""The validate runner: enumerate cells → load → score → select (m4 WP3, D6/D16).

Pure of persistence — it reads the warehouse (through the backend's loaders) and
returns an :class:`AaValidateResult`; the CLI (WP4) takes the lock and writes the rows.
By default it scores the experiment's *declared* comparisons (D6); ``extra_methods``
adds registered methods to the grid (the ``--method`` surface, WP4). Every row carries
the **effective post-correction per-comparison alpha** (``comparison_alpha ∘
effective_alphas``) so the D3 calibration chip matches (recompute.py:245–315).
"""

from __future__ import annotations

from dataclasses import dataclass

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.method_config import MethodConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.pipeline.analyze import comparison_alpha, effective_alphas
from abkit.stats.exceptions import StatsError
from abkit.validate._types import DecisionEntry, ValidateError
from abkit.validate.load import DEFAULT_GRID_CAP, load_placebo_panel
from abkit.validate.panel import PlaceboPanel
from abkit.validate.result import AaValidateResult, CellResult
from abkit.validate.run_id import run_stamp
from abkit.validate.scoring import CellScore, score_cell

DEFAULT_ITERATIONS = 2000


@dataclass(frozen=True)
class ValidateSettings:
    """Resolved knobs for one validate invocation (the donor ``TuneSettings`` split)."""

    iterations: int = DEFAULT_ITERATIONS
    inject_effect: float | None = None
    mode: str = "fpr"  # fpr | power | mde — the selection objective (D16)
    grid_cap: int = DEFAULT_GRID_CAP
    target_power: float = 0.8


@dataclass(frozen=True)
class _CellSpec:
    metric: str
    comparison: ComparisonConfig
    method: MethodConfig
    alpha: float


def _budget(project: ProjectConfig, alpha: float, metric: MetricConfig | None) -> float:
    """The one aa_fpr_budget resolver (metric → project → α×1.5), reused from M3.

    Imported lazily so ``abkit.validate`` and ``abkit.tuning`` never form an import
    cycle (WP6 has the server call this package).
    """
    from abkit.tuning.recompute import resolve_fpr_budget

    return resolve_fpr_budget(project, alpha, metric)


def _share_a(experiment: ExperimentConfig) -> float:
    """Arm-A split share from the first variant's ``expected_split`` (default 0.5)."""
    variants = experiment.assignment.variants
    split = experiment.assignment.expected_split
    if variants and split:
        first = float(split.get(variants[0], 0.5))
        total = float(sum(split.values())) or 1.0
        share = first / total
        return min(max(share, 0.01), 0.99)
    return 0.5


def enumerate_cells(
    experiment: ExperimentConfig,
    project: ProjectConfig,
    extra_methods: list[MethodConfig] | None = None,
) -> list[_CellSpec]:
    """One cell per (declared comparison) + optional extra methods on each metric (D6)."""
    alphas = effective_alphas(experiment, project)
    cells: list[_CellSpec] = []
    for comparison in experiment.comparisons:
        alpha = comparison_alpha(comparison, alphas)
        cells.append(_CellSpec(comparison.metric, comparison, comparison.method, alpha))
        for method in extra_methods or []:
            cells.append(_CellSpec(comparison.metric, comparison, method, alpha))
    return cells


def _verdict(method_name: str, metric: str, score: CellScore, budget: float, alpha: float) -> str:
    """A plain-language per-method verdict (aa-fpr §4.3 / R15)."""
    if score.fpr is None:
        return f"{method_name} on {metric}: could not measure FPR (degenerate/insufficient data)"
    if score.fpr <= budget:
        verdict = f"{method_name} on {metric}: well-calibrated, FPR {score.fpr:.1%}"
    else:
        verdict = (
            f"{method_name} on {metric}: FPR inflated to {score.fpr:.1%} "
            f"(budget {budget:.1%}), do not use"
        )
    if score.peeking_fpr is not None and score.peeking_fpr > 2 * alpha:
        verdict += f"; peeking FPR {score.peeking_fpr:.1%} vs nominal α={alpha:g}"
    return verdict


def _select_recommended(cells: list[CellResult]) -> tuple[str | None, str]:
    """FPR-closest-to-nominal while maximizing power (aa-fpr §2 / R5, R14).

    Returns ``(recommended method_config_id, rationale)``. Prefers cells whose FPR is
    within budget; among those, the highest power, then the tightest achieved MDE.
    """
    scored = [c for c in cells if c.fpr is not None and c.status == "success"]
    if not scored:
        return None, "no cell produced a usable FPR"
    in_budget = [c for c in scored if c.budget is not None and c.fpr <= c.budget]
    pool = in_budget or scored

    def key(c: CellResult) -> tuple:
        power = c.power if c.power is not None else -1.0
        mde = c.achieved_mde if c.achieved_mde is not None else float("inf")
        return (power, -mde, -(c.fpr or 0.0))

    best = max(pool, key=key)
    if in_budget:
        rationale = "highest power among methods with FPR within budget"
    else:
        rationale = "no method is within the FPR budget; highest-power fallback (use with caution)"
    return best.method_config_id, rationale


def run_validation(
    backend: RecomputeBackend,
    experiment: ExperimentConfig,
    project: ProjectConfig,
    metrics: dict[str, MetricConfig],
    metric_sqls: dict[str, str],
    grid,
    settings: ValidateSettings,
    *,
    now_iso: str,
    extra_methods: list[MethodConfig] | None = None,
    metric_filter: str | None = None,
) -> AaValidateResult:
    """Score every cell and return the per-cell results + the recommendation.

    Reads the warehouse (never writes); the caller persists. Panels are cached by
    ``(metric, covariate_lookback)`` — methods sharing a metric and lookback reuse
    one load. ``metric_filter`` restricts scoring to a single metric (``--metric``).
    """
    log: list[DecisionEntry] = []
    specs = enumerate_cells(experiment, project, extra_methods)
    if metric_filter is not None:
        specs = [s for s in specs if s.metric == metric_filter]
    share_a = _share_a(experiment)
    panel_cache: dict[tuple[str, object], PlaceboPanel] = {}
    cells: list[CellResult] = []

    for spec in specs:
        metric = metrics.get(spec.metric)
        if metric is None:
            log.append(DecisionEntry("enumerate", f"metric '{spec.metric}' not found — skipped"))
            continue
        cell = _score_one(
            backend,
            experiment,
            project,
            metric,
            metric_sqls,
            grid,
            spec,
            settings,
            share_a,
            panel_cache,
            log,
        )
        cells.append(cell)

    # per-metric recommendation
    by_metric: dict[str, list[CellResult]] = {}
    for cell in cells:
        by_metric.setdefault(cell.metric, []).append(cell)
    # (metric, method_config_id) -> the ACTUAL selection rationale (in-budget max-power
    # OR the over-budget fallback warning) so the report never contradicts the verdict.
    recommended: dict[tuple[str, str], str] = {}
    for metric_name, metric_cells in by_metric.items():
        rec_id, rationale = _select_recommended(metric_cells)
        if rec_id is not None:
            recommended[(metric_name, rec_id)] = rationale
            log.append(
                DecisionEntry("select", f"{metric_name}: {rationale}", {"method_config_id": rec_id})
            )

    cells = [
        _mark_recommended(cell, recommended.get((cell.metric, cell.method_config_id)))
        for cell in cells
    ]

    return AaValidateResult(
        experiment=experiment.name,
        run_stamp=run_stamp(
            experiment.name,
            now_iso,
            {
                "iterations": settings.iterations,
                "inject_effect": settings.inject_effect,
                "mode": settings.mode,
            },
        ),
        cells=tuple(cells),
        decision_log=tuple(log),
    )


def _score_one(
    backend,
    experiment,
    project,
    metric,
    metric_sqls,
    grid,
    spec,
    settings,
    share_a,
    panel_cache,
    log,
) -> CellResult:
    method_id = spec.method.method_config_id
    method_params = spec.method.canonical_params_json
    base = {
        "metric": spec.metric,
        "method_name": spec.method.name,
        "method_params": method_params,
        "method_config_id": method_id,
        "mode": settings.mode,
        "alpha": spec.alpha,
        "iterations": settings.iterations,
        "injected_effect": settings.inject_effect,
    }
    budget = _budget(project, spec.alpha, metric)
    try:
        metric_sql = metric_sqls[spec.metric]
        cache_key = (spec.metric, spec.method.covariate_lookback)
        panel = panel_cache.get(cache_key)
        if panel is None:
            panel = load_placebo_panel(
                backend,
                spec.comparison,
                metric,
                metric_sql,
                grid,
                input_kind=metric.type,
                cap=settings.grid_cap,
            )
            panel_cache[cache_key] = panel
        method = spec.method.bind(alpha=spec.alpha)
        score = score_cell(
            panel,
            method,
            iterations=settings.iterations,
            seed_parts=("aa", experiment.name, spec.metric, method_id),
            share_a=share_a,
            inject_effect=settings.inject_effect,
            target_power=settings.target_power,
        )
    except (ValidateError, StatsError, KeyError, ValueError) as exc:
        # StatsError covers QuarantinedMethodError AND the bootstrap-family / degenerate
        # SampleValidationError — a single unscoreable cell fails only ITS row (R37),
        # never aborting the whole experiment's matrix (m4 exit-gate review, F1).
        log.append(DecisionEntry("score", f"{spec.metric}/{spec.method.name}: failed — {exc}"))
        return CellResult(
            **base,
            fpr=None,
            peeking_fpr=None,
            power=None,
            achieved_mde=None,
            coverage=None,
            effect_exaggeration=None,
            verdict=f"{spec.method.name} on {spec.metric}: failed ({exc})",
            budget=budget,
            recommended=False,
            details={},
            status="failed",
            error_message=str(exc),
        )

    details = {
        "kept_grid_points": score.kept_grid_points,
        "total_grid_points": score.total_grid_points,
        "degenerate_horizon": score.degenerate_horizon,
        "valid_iterations": score.valid_iterations,
        "single_look_fpr": score.fpr,
        "peeking_fpr": score.peeking_fpr,
        # (elapsed_days, cumulative_fpr) per look — the peeking-vs-looks curve (R10)
        "peeking_curve": [list(point) for point in score.peeking_curve],
        "warnings": list(score.warnings),
    }
    return CellResult(
        **base,
        fpr=score.fpr,
        peeking_fpr=score.peeking_fpr,
        power=score.power,
        achieved_mde=score.achieved_mde,
        coverage=score.coverage,
        effect_exaggeration=score.effect_exaggeration,
        verdict=_verdict(spec.method.name, spec.metric, score, budget, spec.alpha),
        budget=budget,
        recommended=False,
        details=details,
        status="success",
    )


def _mark_recommended(cell: CellResult, rationale: str | None) -> CellResult:
    """Flag the recommended cell, storing the ACTUAL ``_select_recommended`` rationale.

    ``rationale`` is the real selection reason — the in-budget max-power line OR the
    over-budget "highest-power fallback (use with caution)" warning — never a hardcode,
    so the report's Recommended row can't claim an over-budget fallback was "in budget"
    (WP5 review finding). ``None`` means this cell is not the recommendation.
    """
    from dataclasses import replace

    if rationale is not None:
        details = {**cell.details, "recommended_rationale": rationale}
        return replace(cell, recommended=True, details=details)
    if cell.recommended:
        return replace(cell, recommended=False)
    return cell
