"""Implementation of ``abk plan`` — the read-only pre-launch power / sizing planner
(m5-implementation-plan.md WP6; cli-and-dx.md §1).

For each selected experiment it echoes the projected look count + cost shape from the
SAME ``generate_grid`` enumeration the run/validator use, then — per comparison — reads
the latest persisted per-arm moments from ``_ab_results`` (a ``--baseline`` override
supplies them for a greenfield experiment) and reports required-N / achievable-MDE /
achieved-power at the effective two-tier alpha.

Strictly read-only (D11): no lock, no ``_ab_*`` writes; its own manager is closed in a
``finally``. Honest refusals (D10): ratio metrics and resampling (bootstrap) methods
have no versioned power formula → SKIPPED with a reason, never sized with invented math;
CUPED is sized on the raw persisted variance (ρ is not persisted) and flagged as a
conservative upper bound. runtime / ASN are deferred to M6. A genuine harness failure
(bad selection / ``--baseline`` / warehouse error) exits non-zero; a by-design refusal
does not.
"""

from __future__ import annotations

import math

import click

from abkit.cli._output import echo_done, echo_error, echo_tree
from abkit.cli.commands._context import load_project_context
from abkit.config import select_experiments
from abkit.core.period_planner import GridLimitExceeded, generate_grid
from abkit.pipeline import comparison_alpha, effective_alphas
from abkit.planning.sizing import (
    FRACTION,
    SAMPLE,
    BaselineMoments,
    ComparisonPlan,
    is_powered,
    moments_from_override,
    parse_baseline_overrides,
    size_comparison,
)
from abkit.stats import get_method_class, n_comparisons


def run_plan(
    select: tuple[str, ...],
    metric: str | None,
    mde: float | None,
    power: float | None,
    alpha: float | None,
    baseline: tuple[str, ...],
    profile: str | None,
) -> None:
    context = load_project_context(require_profiles=True)
    click.echo(f"Project root: {context.root}")

    if alpha is not None and not 0.0 < alpha < 1.0:
        raise click.BadParameter(f"--alpha must be in (0, 1), got {alpha}", param_hint="--alpha")
    if power is not None and not 0.0 < power < 1.0:
        raise click.BadParameter(f"--power must be in (0, 1), got {power}", param_hint="--power")
    if mde is not None and mde <= 0:
        raise click.BadParameter(f"--mde must be > 0, got {mde}", param_hint="--mde")
    try:
        overrides = parse_baseline_overrides(baseline)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--baseline") from exc

    selected, selection_warnings = select_experiments(context.root, select)
    for warning in selection_warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not selected:
        echo_done("Nothing selected.")
        return

    failed = 0
    for _, experiment in selected:
        try:
            _plan_one(experiment, context, profile, metric, mde, power, alpha, overrides)
        except click.ClickException:
            raise
        except Exception as exc:  # a warehouse/read failure on one experiment
            echo_error(experiment.name, f"plan failed: {exc}")
            failed += 1

    echo_done(f"{len(selected)} experiment(s) planned" + (f", {failed} FAILED" if failed else ""))
    if failed:
        raise SystemExit(1)


def _plan_one(
    experiment,
    context,
    profile,
    metric_filter,
    mde,
    power_opt,
    alpha_opt,
    overrides,
) -> None:
    from abkit.database.internal_tables import InternalTablesManager

    project = context.project
    power = power_opt if power_opt is not None else project.statistics.power
    # honour --alpha without mutating the config: re-resolve the two-tier scheme off a
    # copy with the overridden experiment-level alpha, so the correction still divides it.
    exp_for_alpha = (
        experiment.model_copy(update={"alpha": alpha_opt}) if alpha_opt is not None else experiment
    )
    alphas = effective_alphas(exp_for_alpha, project)

    comparisons = experiment.comparisons
    if metric_filter is not None:
        comparisons = [c for c in comparisons if c.metric == metric_filter]
        if not comparisons:
            configured = ", ".join(c.metric for c in experiment.comparisons)
            raise click.ClickException(
                f"--metric '{metric_filter}' is not a comparison of '{experiment.name}' "
                f"(have: {configured})"
            )

    # look-count + cost shape from the one shared enumeration (§6.4). Bound it by
    # max_looks like the run planner so a pathological sub-day grid fails fast instead of
    # OOM-enumerating in this read-only command (plan skips the config-lint that would
    # otherwise catch it — M5 exit-gate round-1 finding).
    try:
        grid = generate_grid(
            experiment.start_date,
            experiment.end_date,
            experiment.cadence_segments(),
            tz=experiment.timezone,
            limit=project.limits.max_looks,
        )
    except GridLimitExceeded as exc:
        raise click.ClickException(
            f"{experiment.name}: planned looks exceed max_looks="
            f"{project.limits.max_looks} — coarsen the cadence or raise the limit ({exc})"
        ) from exc
    looks = len(grid)
    pairs = int(n_comparisons(len(experiment.assignment.variants), 1))
    rows_per_refresh = looks * pairs * len(experiment.comparisons)

    plans: list[ComparisonPlan] = []
    manager = context.profiles.create_manager(profile)
    try:
        tables = InternalTablesManager(manager)
        has_results = tables.results_table_exists()
        for comparison in comparisons:
            plans.append(
                _plan_comparison(
                    experiment,
                    comparison,
                    alphas,
                    power,
                    mde,
                    overrides.get(comparison.metric),
                    tables if has_results else None,
                )
            )
    finally:
        manager.close()

    _emit_plan(experiment, project, alphas, power, looks, grid, rows_per_refresh, plans)


def _plan_comparison(
    experiment,
    comparison,
    alphas,
    power: float,
    mde: float | None,
    override: dict[str, float] | None,
    tables,
) -> ComparisonPlan:
    role = (
        "main"
        if comparison.is_main_metric
        else "guardrail" if comparison.is_guardrail else "secondary"
    )
    method_name = comparison.method.name
    method_cls = get_method_class(method_name)

    # honest-refusal dispatch on DECLARATIVE capability (invariant 3), never a name check.
    needs_seed = any(spec.name == "seed" for spec in method_cls.param_specs)
    if method_cls.is_paired:
        return ComparisonPlan(comparison.metric, method_name, role, refused="paired design")
    if needs_seed:
        return ComparisonPlan(
            comparison.metric,
            method_name,
            role,
            refused="resampling method — no closed-form power (measure it with "
            "`abk validate --inject-effect`)",
        )
    if method_cls.input_kind == "ratio":
        return ComparisonPlan(
            comparison.metric,
            method_name,
            role,
            refused="ratio metric — no versioned power formula (M6)",
        )

    kind = FRACTION if method_cls.input_kind == "fraction" else SAMPLE
    defaults = {spec.name: spec.default for spec in method_cls.param_specs}
    test_type = str(
        comparison.method.params.get("test_type", defaults.get("test_type", "relative"))
    )
    alpha = comparison_alpha(comparison, alphas)
    target_mde = mde if mde is not None else comparison.min_effect

    notes: list[str] = []
    moments = _resolve_moments(experiment, comparison, kind, override, tables, notes)
    if moments is None:
        return ComparisonPlan(
            comparison.metric,
            method_name,
            role,
            refused="no baseline — run `abk run` first, or pass "
            f"--baseline {comparison.metric}:...",
            kind=kind,
            test_type=test_type,
            alpha=alpha,
            power=power,
            target_mde=target_mde,
        )
    if method_cls.requires_covariate:
        notes.append("sized on RAW variance — CUPED (ρ not persisted) lowers required-N further")

    plan_ratio = _plan_ratio(experiment)
    result = size_comparison(
        moments,
        test_type=test_type,
        alpha=alpha,
        power=power,
        target_mde=target_mde,
        plan_ratio=plan_ratio,
    )
    return ComparisonPlan(
        metric=comparison.metric,
        method_name=method_name,
        role=role,
        kind=kind,
        test_type=test_type,
        alpha=alpha,
        power=power,
        baseline=moments,
        target_mde=target_mde,
        plan_ratio=plan_ratio,
        result=result,
        notes=notes,
    )


def _plan_ratio(experiment) -> float:
    """Forward-looking treatment:control allocation from ``expected_split`` (defaults 1.0)."""
    variants = experiment.assignment.variants
    split = experiment.assignment.expected_split
    control = split.get(variants[0])
    treatment = split.get(variants[1])
    if control and treatment and control > 0:
        return treatment / control
    return 1.0


def _resolve_moments(
    experiment, comparison, kind: str, override, tables, notes: list[str]
) -> BaselineMoments | None:
    if override is not None:
        try:
            return moments_from_override(kind, override)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--baseline") from exc
    if tables is None:
        return None
    return _moments_from_results(experiment, comparison, kind, tables)


def _moments_from_results(experiment, comparison, kind: str, tables) -> BaselineMoments | None:
    """Latest usable persisted control/treatment moments for the first-pair series."""
    variants = experiment.assignment.variants
    name_1, name_2 = variants[0], variants[1]
    rows = tables.load_results(
        experiment.name, comparison.metric, method_config_id=comparison.method.method_config_id
    )
    latest = None
    for row in rows:  # ascending by end_ts ⇒ the last match is the most data-rich look
        if str(row.get("name_1")) != name_1 or str(row.get("name_2")) != name_2:
            continue
        if row.get("insufficient_data"):
            continue
        if row.get("value_1") is None:
            continue
        latest = row
    if latest is None:
        return None

    value_1 = _num(latest.get("value_1"))
    size_1 = latest.get("size_1")
    size_2 = latest.get("size_2")
    ts = latest.get("end_ts")
    source = f"persisted @ {ts.isoformat() if ts is not None else '?'}"
    if value_1 is None or not size_1 or not size_2:
        return None

    if kind == FRACTION:
        std_1 = _num(latest.get("std_1"))
        value_2 = _num(latest.get("value_2"))
        std_2 = _num(latest.get("std_2"))
        if std_1 is None or std_2 is None or value_2 is None:
            return None
        if not (0.0 < value_1 < 1.0 and 0.0 < value_2 < 1.0 and std_1 > 0 and std_2 > 0):
            return None
        nobs_1 = value_1 * (1.0 - value_1) / (std_1**2)
        nobs_2 = value_2 * (1.0 - value_2) / (std_2**2)
        return BaselineMoments(FRACTION, value_1, nobs_1, nobs_2, None, source)

    std_1 = _num(latest.get("std_1"))
    if std_1 is None or std_1 <= 0:
        return None
    return BaselineMoments(SAMPLE, value_1, float(size_1), float(size_2), std_1, source)


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ── rendering ──────────────────────────────────────────────────────────────────


def _fmt_effect(value: float | None, test_type: str) -> str:
    if value is None:
        return "—"
    if not math.isfinite(value):
        return "∞"
    if test_type == "relative":
        return f"{value * 100:.2f}%"
    return f"{value:.4g}"


def _fmt_n(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not math.isfinite(value):
        return "∞ (underpowered)"
    return f"{int(round(value)):,}"


def _emit_plan(experiment, project, alphas, power, looks, grid, rows_per_refresh, plans) -> None:
    correction_note = (
        f"main {alphas.main:.4g} / secondary {alphas.secondary:.4g}"
        if alphas.secondary is not None and alphas.secondary != alphas.main
        else f"per-comparison {alphas.main:.4g}"
    )
    header = f"{experiment.name}: plan · α raw={alphas.alpha:.4g} → {correction_note} · power {power:.2f}"

    children: list[str] = []
    for plan in plans:
        children.extend(_plan_lines(plan))

    cadence = _fmt_cadence(experiment)
    children.append(
        f"looks: {looks} planned · cadence {cadence} · horizon "
        f"{grid.horizon_ts.date().isoformat()} · ~{rows_per_refresh:,} _ab_results rows/full-refresh"
    )
    if experiment.is_sub_day():
        children.append("  sub-day: each look ≤ one day of fact rows (day-grained state, §6.4)")

    warnings: list[str] = []
    variants = experiment.assignment.variants
    if len(variants) > 2:
        warnings.append(
            f"{len(variants)}-arm experiment — sizing is shown for the "
            f"{variants[0]} vs {variants[1]} contrast only (the other pairs share the same α)"
        )
    warn_looks = project.limits.warn_looks
    if looks > warn_looks and not experiment.sequential.enabled:
        warnings.append(
            f"{looks} looks > warn_looks={warn_looks} without sequential.enabled — "
            "peeking inflates the false-positive rate (enable sequential or coarsen cadence)"
        )
    # (a grid over max_looks never reaches here — generate_grid(limit=…) fails fast above)

    echo_tree(header, children, warnings=warnings or None)


def _plan_lines(plan: ComparisonPlan) -> list[str]:
    tag = f"[{plan.role} · {plan.method_name}" + (
        f" · {plan.test_type}]" if plan.test_type else "]"
    )
    if plan.refused is not None:
        return [f"{plan.metric} {tag} — SKIPPED: {plan.refused}"]

    assert plan.baseline is not None and plan.result is not None
    b = plan.baseline
    if plan.kind == FRACTION:
        base = f"baseline prop={b.baseline:.4g} · n={_fmt_n(b.n)}/{_fmt_n(b.n_other)} trials"
    else:
        base = (
            f"baseline mean={b.baseline:.4g} std={b.std:.4g} · n={_fmt_n(b.n)}/{_fmt_n(b.n_other)}"
        )
    lines = [f"{plan.metric} {tag} — {base} ({b.source})"]

    r = plan.result
    parts: list[str] = []
    if plan.target_mde is not None:
        powered = is_powered(plan)
        flag = " ✓ powered" if powered else " ✗ underpowered" if powered is False else ""
        parts.append(
            f"target MDE {_fmt_effect(plan.target_mde, plan.test_type or 'relative')} → "
            f"required {_fmt_n(r.required_n)}/arm{flag}"
        )
        if r.achieved_power is not None:
            parts.append(f"power@MDE {r.achieved_power:.2f}")
    else:
        parts.append("no target MDE (pass --mde or set comparison.min_effect for required-N)")
    parts.append(f"achievable MDE {_fmt_effect(r.achievable_mde, plan.test_type or 'relative')}")
    lines.append("  " + " · ".join(parts))
    for note in plan.notes:
        lines.append(f"  ⚠ {note}")
    return lines


def _fmt_cadence(experiment) -> str:
    segments = experiment.cadence_segments()
    if len(segments) == 1 and segments[0][1] is None:
        return _fmt_seconds(segments[0][0])
    return " → ".join(
        _fmt_seconds(every) + (f"/until {_fmt_seconds(until)}" if until else "")
        for every, until in segments
    )


def _fmt_seconds(seconds: int) -> str:
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"
