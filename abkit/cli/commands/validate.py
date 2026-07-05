"""Implementation of ``abk validate`` — the A/A false-positive matrix (m4 WP4).

Out-of-band from ``abk run``: for each selected experiment it draws N placebo A/A
splits over the experiment's own cohort, scores each method's empirical FPR /
cumulative-peeking FPR / power / achieved-MDE / CI-coverage, and persists one
``_ab_aa_runs`` row per cell (docs/specs/aa-false-positive-matrix.md). Rows carry the
effective per-comparison alpha, so the explore calibration chip lights (D3).

Lock discipline (D5): a distinct ``(experiment, "pipeline", "validate")`` claim —
validate writes only ``_ab_aa_runs`` (never read by the run pipeline), so it need not
serialize behind nightly runs. Failures are recorded on the lock row before
propagating; ``abk unlock`` clears both the run and validate locks. Any failed
experiment exits NON-ZERO. ``--report`` is best-effort (the one exception).
"""

from __future__ import annotations

import os
from pathlib import Path

import click

from abkit.cli._output import (
    StageLogRenderer,
    echo_done,
    echo_error,
    echo_noop,
    echo_tree,
)
from abkit.cli.commands._context import load_project_context
from abkit.config import select_experiments, validate_level2
from abkit.config.method_config import MethodConfig
from abkit.core.period_planner import generate_grid
from abkit.pipeline import effective_alphas
from abkit.stats import n_comparisons

_VALIDATE_STAGE_TITLES = {
    "load": "LOAD",
    "resample": "RESAMPLE",
    "score": "SCORE",
    "persist": "PERSIST",
}


def _resolve_report_path(report_path: str, project_root: Path, experiment: str) -> Path:
    """Same tri-state convention as ``abk run``, defaulting to the ``__validate`` suffix
    so a validate report never clobbers the run readout (the ``__explore`` precedent)."""
    if report_path == "":
        return project_root / "reports" / f"{experiment}__validate.html"
    candidate = Path(report_path)
    if candidate.suffix.lower() == ".html":
        return candidate
    return candidate / f"{experiment}__validate.html"


def run_validate(
    select: tuple[str, ...],
    method: tuple[str, ...],
    metric: str | None,
    iterations: int,
    inject_effect: float | None,
    scoring: str,
    report_path: str | None,
    force: bool,
    profile: str | None,
) -> None:
    context = load_project_context(require_profiles=True)
    click.echo(f"Project root: {context.root}")

    # ── config lint (no DB) ──────────────────────────────────────────────────
    report = validate_level2(context.root, context.project, context.experiments, context.metrics)
    for warning in report.warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not report.ok:
        for error in report.errors:
            echo_error("validate", error)
        raise click.ClickException(f"config validation failed ({len(report.errors)} errors)")

    selected, selection_warnings = select_experiments(context.root, select)
    for warning in selection_warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not selected:
        echo_done("Nothing selected.")
        return
    if (
        report_path is not None
        and report_path != ""
        and Path(report_path).suffix.lower() == ".html"
        and len(selected) > 1
    ):
        raise click.BadParameter(
            f"--report {report_path} names one file but {len(selected)} experiments "
            "are selected — pass a directory instead",
            param_hint="--report",
        )

    failed = 0
    for _, experiment in selected:
        status = _validate_one(
            experiment,
            context,
            profile,
            method,
            metric,
            iterations,
            inject_effect,
            scoring,
            report_path,
            force,
        )
        if status == "failed":
            failed += 1

    echo_done(f"{len(selected)} experiment(s) validated" + (f", {failed} FAILED" if failed else ""))
    if failed:
        raise SystemExit(1)


def _validate_one(
    experiment,
    context,
    profile,
    method_names,
    metric_filter,
    iterations,
    inject_effect,
    scoring,
    report_path,
    force,
) -> str:
    from abkit.compute.recompute_backend import RecomputeBackend
    from abkit.database.internal_tables import InternalTablesManager
    from abkit.utils.datetime_utils import now_utc_naive
    from abkit.validate import ValidateSettings, aa_run_records, run_validation

    # the inspectable effective alphas (R28), echoed like `abk run`
    alphas = effective_alphas(experiment, context.project)
    pairs = n_comparisons(alphas.groups_count, 1)
    echo_tree(
        f"{experiment.name}: effective alphas",
        [
            f"alpha={alphas.alpha} over {alphas.groups_count} variants (C({alphas.groups_count},2)={pairs} pairs)",
            f"main-metric alpha: {alphas.main:.6g}",
        ],
    )

    manager = context.manager_factory(profile)()
    tables = InternalTablesManager(manager)
    if not tables.acquire_lock(
        experiment.name,
        "pipeline",
        "validate",
        timeout_seconds=context.project.timeouts.compute,
        force=force,
    ):
        manager.close()
        echo_noop(experiment.name, "validate lock held (use --force or `abk unlock`)")
        return "locked"

    renderer = StageLogRenderer(titles=_VALIDATE_STAGE_TITLES)
    try:
        tables.ensure_tables()
        backend = RecomputeBackend(manager, experiment)
        grid = generate_grid(
            experiment.start_date,
            experiment.end_date,
            experiment.cadence_segments(),
            tz=experiment.timezone,
        )
        renderer("load", f"cohort over {len(grid.cutoffs)} cutoffs")
        extra_methods = [MethodConfig(name=name) for name in method_names]
        metric_sqls = {cfg.name: cfg.get_query_text(context.root) for _, cfg in context.metrics}
        settings = ValidateSettings(
            iterations=iterations, inject_effect=inject_effect, mode=scoring
        )
        renderer("resample", f"{iterations} placebo splits/cell")

        result = run_validation(
            backend,
            experiment,
            context.project,
            context.metrics_by_name,
            metric_sqls,
            grid,
            settings,
            now_iso=now_utc_naive().isoformat(),
            extra_methods=extra_methods,
            metric_filter=metric_filter,
        )
        renderer("score", f"{len(result.cells)} cell(s)")

        records = aa_run_records(result)
        for record in records:
            tables.save_aa_run(record)
        renderer("persist", f"{len(records)} _ab_aa_runs row(s)")

        _emit_matrix(experiment.name, result)
        tables.release_lock(experiment.name, "pipeline", "validate", status="completed")
    except BaseException as exc:  # incl. KeyboardInterrupt/SystemExit — never strand the lock
        tables.release_lock(
            experiment.name, "pipeline", "validate", status="failed", error_message=str(exc)
        )
        echo_error(experiment.name, f"validate failed: {exc}")
        manager.close()
        if not isinstance(exc, Exception):
            # a signal / interpreter exit still propagates — but with the lock RELEASED,
            # never stranded for the 2h compute timeout (driver.py:229–236 precedent).
            raise
        return "failed"

    if report_path is not None:
        try:
            _emit_report(experiment, context, tables, report_path)
        except Exception as report_error:  # never fail validate on a report
            click.echo(click.style(f"  │ Report skipped: {report_error}", fg="yellow"))
    manager.close()
    return "completed"


def _emit_matrix(experiment: str, result) -> None:
    by_metric: dict[str, list] = {}
    for cell in result.cells:
        by_metric.setdefault(cell.metric, []).append(cell)
    for metric, cells in sorted(by_metric.items()):
        children = []
        for cell in cells:
            mark = "★ " if cell.recommended else ""
            children.append(f"{mark}{cell.verdict}")
        echo_tree(f"{experiment} · {metric}", children)


def _emit_report(experiment, context, tables, report_path: str) -> None:
    """Bake the self-contained readout, now carrying the A/A calibration matrix (WP5).

    Reuses the report bundle (D10): ``build_report_payload`` fills the ``calibration``
    block from the ``_ab_aa_runs`` rows validate just wrote, and ``render_report_html``
    bakes the committed ``report.js`` — one renderer, no third bundle. Best-effort: the
    caller yellow-skips a bake failure so it never masks a successful validation."""
    from abkit.reporting import build_report_payload, render_report_html
    from abkit.utils.datetime_utils import now_utc_naive

    payload = build_report_payload(
        experiment,
        tables,
        project=context.project,
        metric_configs=context.metrics_by_name,
        generated_at=now_utc_naive().strftime("%Y-%m-%d %H:%M UTC"),
    )
    out = _resolve_report_path(report_path, context.root, experiment.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    # atomic replace: a mid-write kill must never leave a truncated file (run.py precedent)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(render_report_html(payload), encoding="utf-8")
    os.replace(tmp, out)
    try:
        shown: Path | str = out.relative_to(context.root)
    except ValueError:
        shown = out
    cal = payload.get("calibration")
    note = cal["headline"] if cal and cal.get("headline") else "no calibration rows"
    click.echo(click.style(f"  │ Report → {shown}  ({note})", fg="cyan"))
