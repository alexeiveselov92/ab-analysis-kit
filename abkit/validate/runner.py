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
from abkit.stats.registry import get_method_class
from abkit.validate._types import DecisionEntry, ValidateError
from abkit.validate.family import FamilyMember, sweep_family
from abkit.validate.load import DEFAULT_GRID_CAP, load_placebo_panel
from abkit.validate.panel import PlaceboPanel
from abkit.validate.result import AaValidateResult, CellResult, FamilyResult
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


def _method_fits(
    method: MethodConfig, metric: MetricConfig | None, log: list[DecisionEntry] | None
) -> bool:
    """D6: an extra ``--method`` must match the metric's ``input_kind``, not be paired,
    and not be quarantined/unknown — else it can never score in A/A (placebo arms are
    unpaired). Skip-and-log rather than enqueue a doomed cell that persists as a
    confusing ``status='failed'`` row (m4 exit-gate review). Mirrors the knob-surface
    filter (recompute.py ``knob_surface``)."""
    if metric is None:
        return True  # a missing metric is reported downstream by the runner
    try:
        method_cls = get_method_class(method.name)
    except StatsError as exc:  # quarantined or unknown
        if log is not None:
            log.append(DecisionEntry("enumerate", f"--method {method.name!r} skipped: {exc}"))
        return False
    if method_cls.input_kind != metric.type or method_cls.is_paired:
        if log is not None:
            reason = "paired" if method_cls.is_paired else f"needs {method_cls.input_kind}"
            log.append(
                DecisionEntry(
                    "enumerate",
                    f"--method {method.name!r} skipped for {metric.name!r} "
                    f"({metric.type} metric): {reason}",
                )
            )
        return False
    return True


def enumerate_cells(
    experiment: ExperimentConfig,
    project: ProjectConfig,
    metrics: dict[str, MetricConfig] | None = None,
    extra_methods: list[MethodConfig] | None = None,
    log: list[DecisionEntry] | None = None,
) -> list[_CellSpec]:
    """One cell per declared comparison + compatible extra methods on each metric (D6).

    Extra ``--method`` methods are filtered per metric (``_method_fits``) and de-duped
    against cells already enqueued for that metric (same ``method_config_id``), so a
    ``--method`` that repeats a declared method — or can't run on the metric — never
    produces a duplicate or a doomed cell."""
    alphas = effective_alphas(experiment, project)
    cells: list[_CellSpec] = []
    seen: set[tuple[str, str]] = set()

    def _add(metric_name: str, comparison: ComparisonConfig, method: MethodConfig, alpha: float):
        key = (metric_name, method.method_config_id)
        if key in seen:
            return
        seen.add(key)
        cells.append(_CellSpec(metric_name, comparison, method, alpha))

    for comparison in experiment.comparisons:
        alpha = comparison_alpha(comparison, alphas)
        _add(comparison.metric, comparison, comparison.method, alpha)
        metric = metrics.get(comparison.metric) if metrics else None
        for method in extra_methods or []:
            if _method_fits(method, metric, log):
                _add(comparison.metric, comparison, method, alpha)
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
    specs = enumerate_cells(experiment, project, metrics, extra_methods, log)
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

    # The composed multi-metric FWER/FDR sweep (D9) — only when the whole declared family
    # was scored (a --metric filter or a single comparison has no family to compose).
    family = None
    if metric_filter is None:
        family = _run_family_sweep(experiment, project, panel_cache, share_a, settings, log)

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
        family=family,
    )


def _family_verdict(score, budget: float | None) -> str:
    """A plain-language composed-family verdict (aa-fpr §4.3, generalized to the family)."""
    if score.fwer is None:
        return "composed family: could not measure family-wise error (degenerate splits)"
    band = f" (budget {budget:.1%})" if budget is not None else ""
    state = "within budget" if not score.over_budget else "OVER budget — do not trust the family"
    fdr_txt = "—" if score.fdr is None else f"{score.fdr:.1%}"
    return (
        f"composed {score.correction} over {score.n_metrics} metrics: family-wise error "
        f"{score.fwer:.1%}{band} {state}; FDR {fdr_txt}"
    )


def _run_family_sweep(
    experiment: ExperimentConfig,
    project: ProjectConfig,
    panel_cache: dict[tuple[str, object], PlaceboPanel],
    share_a: float,
    settings: ValidateSettings,
    log: list[DecisionEntry],
) -> FamilyResult | None:
    """Score the composed FWER/FDR over the declared comparison family (D9/WP8).

    Reuses the panels the per-cell pass already loaded (``panel_cache``); a comparison
    whose panel failed to load is dropped with a log entry. Runs the complete-null sweep
    (no injection) at each comparison's effective two-tier alpha under the experiment's
    correction, so FWER (any false rejection) and FDR (mean FDP) coincide by construction.
    """
    alphas = effective_alphas(experiment, project)
    correction = experiment.correction or project.statistics.correction
    members: list[FamilyMember] = []
    for comparison in experiment.comparisons:
        panel = panel_cache.get((comparison.metric, comparison.method.covariate_lookback))
        if panel is None:
            log.append(
                DecisionEntry(
                    "family", f"{comparison.metric}: no panel loaded — excluded from family"
                )
            )
            continue
        alpha = comparison_alpha(comparison, alphas)
        members.append(
            FamilyMember(
                metric=comparison.metric,
                panel=panel,
                method=comparison.method.bind(alpha=alpha),
                alpha=alpha,
                planted=False,
            )
        )
    if len(members) < 2:
        log.append(DecisionEntry("family", "fewer than two scorable metrics — no family sweep"))
        return None

    # Judge the empirical family FWER against the composed rule's OWN nominal rate, not a
    # single-cell α×1.5. Two-tier Bonferroni protects the main tier at α and the secondary
    # tier at α (collectively), so a correctly-configured multi-metric family sits at its
    # nominal composed rate 1−∏(1−αᵢ) ≈ Σαᵢ (≈2α under the default) — comparing that to α
    # would falsely flag the default setup as over budget (M5 exit-gate round-1 finding).
    # Anchoring the budget to the nominal rate makes "over budget" mean the METHODS are
    # miscalibrated (clustering / variance underestimation — the D9 purpose), independent
    # of how tight the correction is; a broken method still trips it, a loose-but-honest
    # `correction: none` does not.
    per_cell_budget = _budget(project, alphas.alpha, None)
    headroom = per_cell_budget / alphas.alpha if alphas.alpha else 1.5
    if correction == "benjamini_hochberg":
        # BH controls the complete-null family error at ≈ the members' level (FWER==FDR≈α
        # under the complete null — test_null_bh_controls_fdr_near_nominal), NOT the
        # Bonferroni composition ≈Σα. Anchoring to the composition rate would leave the
        # BH budget ~n× too lenient and under-flag a miscalibrated method (M5 exit-gate
        # round-2 finding); anchor to the members' level instead.
        nominal_family = max(member.alpha for member in members)
    else:
        # Bonferroni / none: the complete-null family FWER is the composed nominal rate.
        nominal_family = 1.0
        for member in members:
            nominal_family *= 1.0 - member.alpha
        nominal_family = 1.0 - nominal_family
    budget = min(1.0, nominal_family * headroom)
    try:
        score = sweep_family(
            members,
            correction=correction,
            iterations=settings.iterations,
            share_a=share_a,
            seed_parts=("aa-family", experiment.name),
            inject_effect=None,
            budget=budget,
        )
    except (ValidateError, StatsError) as exc:
        log.append(DecisionEntry("family", f"family sweep failed — {exc}"))
        return None

    verdict = _family_verdict(score, budget)
    log.append(DecisionEntry("family", verdict))
    return FamilyResult(
        correction=score.correction,
        n_metrics=score.n_metrics,
        n_null_metrics=score.n_null_metrics,
        metrics=tuple(m.metric for m in members),
        iterations=score.iterations,
        valid_iterations=score.valid_iterations,
        fwer=score.fwer,
        fdr=score.fdr,
        any_rejection_rate=score.any_rejection_rate,
        budget=budget,
        over_budget=score.over_budget,
        alpha=alphas.alpha,
        verdict=verdict,
        warnings=score.warnings,
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
        # M5 D8: the always-valid twin curve + scalar (None-safe; empty when no column)
        "single_look_fpr_sequential": score.fpr_sequential,
        "peeking_fpr_sequential": score.peeking_fpr_sequential,
        "peeking_curve_sequential": [list(point) for point in score.peeking_curve_sequential],
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
        tau2=score.tau2,
        fpr_sequential=score.fpr_sequential,
        peeking_fpr_sequential=score.peeking_fpr_sequential,
        power_sequential=score.power_sequential,
        coverage_sequential=score.coverage_sequential,
        effect_exaggeration_sequential=score.effect_exaggeration_sequential,
        ci_width=score.ci_width,
        ci_width_sequential=score.ci_width_sequential,
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
