"""Implementation of ``abk run`` — the pipeline driver command.

VALIDATE (config-lint, no DB) always runs first; the effective per-comparison
alphas are echoed (the inspectable two-tier scheme, declarative-config.md §6);
SRM failures print the loud red gate line (data-contract §6). Any failed
experiment or validation error exits NON-ZERO (the CLI is the Prefect unit of
automation).
"""

from __future__ import annotations

from datetime import datetime

import click

from abkit.cli._output import (
    StageLogRenderer,
    echo_done,
    echo_error,
    echo_noop,
    echo_srm,
    echo_tree,
)
from abkit.cli.commands._context import load_project_context
from abkit.config import select_experiments, validate_level2
from abkit.pipeline import PipelineStep, effective_alphas, run_experiments
from abkit.stats import n_comparisons


def _parse_date(value: str | None, option: str) -> datetime | None:
    if value is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise click.BadParameter(
        f"invalid {option} value {value!r} (use YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS')"
    )


def run_run(
    select: tuple[str, ...],
    exclude: tuple[str, ...],
    steps: str,
    profile: str | None,
    from_ts: str | None,
    to_ts: str | None,
    full_refresh: bool,
    force: bool,
    workers: int,
) -> None:
    try:
        parsed_steps = PipelineStep.parse(steps)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--steps") from exc

    validate_only = parsed_steps == [PipelineStep.VALIDATE]
    context = load_project_context(require_profiles=not validate_only)
    click.echo(f"Project root: {context.root}")

    # ── VALIDATE: level-2 config lint, no DB (declarative-config §8) ─────────
    report = validate_level2(context.root, context.project, context.experiments, context.metrics)
    for warning in report.warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not report.ok:
        for error in report.errors:
            echo_error("validate", error)
        raise click.ClickException(f"config validation failed ({len(report.errors)} errors)")
    click.echo(
        click.style(
            f"  ✓ config valid: {len(context.experiments)} experiment(s), "
            f"{len(context.metrics)} metric(s)",
            fg="green",
        )
    )
    if validate_only:
        echo_done("Validation passed.")
        return

    # ── selection + the inspectable alphas ───────────────────────────────────
    selected, selection_warnings = select_experiments(context.root, select, exclude)
    for warning in selection_warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not selected:
        echo_done("Nothing selected.")
        return

    for _, experiment in selected:
        alphas = effective_alphas(experiment, context.project)
        pairs = n_comparisons(alphas.groups_count, 1)
        children = [
            f"alpha={alphas.alpha} over {alphas.groups_count} variants "
            f"(C({alphas.groups_count},2)={pairs} pairs)",
            f"main-metric alpha: {alphas.main:.6g}",
        ]
        if alphas.secondary is not None and alphas.metrics_count:
            children.append(
                f"secondary alpha: {alphas.secondary:.6g} "
                f"(÷{pairs}×{alphas.metrics_count} non-main metrics)"
            )
        echo_tree(f"{experiment.name}: effective alphas", children)

    full_refresh_window = None
    if full_refresh:
        window_from = _parse_date(from_ts, "--from")
        window_to = _parse_date(to_ts, "--to")
        if window_from is None or window_to is None:
            raise click.BadParameter("--full-refresh needs both --from and --to")
        full_refresh_window = (window_from, window_to)
    elif from_ts or to_ts:
        raise click.BadParameter("--from/--to only apply with --full-refresh")

    renderer = StageLogRenderer()

    def log(line: str) -> None:
        stage, _, rest = line.partition(" ")
        renderer(stage.strip().lower(), rest.strip())

    outcomes = run_experiments(
        selected,
        context.metrics_by_name,
        context.project,
        manager_factory=context.manager_factory(profile),
        steps=parsed_steps,
        project_root=context.root,
        max_workers=max(1, workers),
        force=force,
        full_refresh_window=full_refresh_window,
        log=log,
    )

    click.echo()
    failed = 0
    for outcome in outcomes:
        if outcome.status == "completed":
            children = [
                f"exposures: {outcome.exposures_loaded}",
                f"cutoffs planned: {outcome.cutoffs_planned}",
                f"results written: {outcome.results_written}",
            ]
            srm_warnings = [w for w in outcome.warnings if "SRM" in w]
            other_warnings = [w for w in outcome.warnings if "SRM" not in w]
            echo_tree(outcome.experiment, children, warnings=other_warnings)
            for warning in srm_warnings:
                echo_srm(warning)
        elif outcome.status == "locked":
            echo_noop(outcome.experiment, outcome.error or "locked")
        elif outcome.status == "skipped":
            echo_noop(outcome.experiment, "nothing to do for the selected steps")
        else:
            failed += 1
            echo_error(outcome.experiment, outcome.error or "failed")

    total_rows = sum(o.results_written for o in outcomes)
    echo_done(
        f"{len(outcomes)} experiment(s), {total_rows} result row(s)"
        + (f", {failed} FAILED" if failed else "")
    )
    if failed:
        raise SystemExit(1)
