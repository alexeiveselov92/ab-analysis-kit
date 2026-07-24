"""Implementation of ``abk run`` — the pipeline driver command.

VALIDATE (config-lint, no DB) always runs first; the effective per-comparison
alphas are echoed (the inspectable two-tier scheme, declarative-config.md §6);
SRM failures print the loud red gate line (data-contract §6). Any failed
experiment or validation error exits NON-ZERO (the CLI is the Prefect unit of
automation).

``--report`` (tri-state: absent / bare / path — the donor's flag shape) emits
one self-contained HTML readout per experiment after its pipeline, inside
try/except: a report failure yellow-skips and NEVER fails the run — the one
recorded exception to the exit-non-zero contract (m3-implementation-plan.md
WP3/D8). Emission happens even when zero cutoffs were pending — re-running an
up-to-date experiment is the "just give me the report" path (D8).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

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
from abkit.config.experiment_config import ExperimentConfig
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


def _resolve_report_path(report_path: str, project_root: Path, experiment: str) -> Path:
    """The donor's path convention: "" → reports/<experiment>.html under the
    project root; a ``.html`` value → that exact file; anything else → a
    directory getting ``/<experiment>.html`` appended."""
    if report_path == "":
        return project_root / "reports" / f"{experiment}.html"
    candidate = Path(report_path)
    if candidate.suffix.lower() == ".html":
        return candidate
    return candidate / f"{experiment}.html"


def _emit_experiment_report(
    experiment: ExperimentConfig,
    tables,
    context,
    report_path: str,
    generated_at: str,
    manager=None,
    cohort_counts: dict[str, int] | None = None,
) -> None:
    """Build + write one experiment readout; prints the house report line.

    Raises on failure — the caller owns the yellow-skip (never fail the run
    on a report)."""
    from abkit.reporting import build_report_payload, render_report_html

    payload = build_report_payload(
        experiment,
        tables,
        project=context.project,
        metric_configs=context.metrics_by_name,
        generated_at=generated_at,
        # the SRM chip's counts (m8 WP4): reuse the run's own validated
        # snapshot when the LOAD stage produced one; otherwise the builder
        # derives them (live-source snapshot in direct mode)
        manager=manager,
        project_root=context.root,
        cohort_counts=cohort_counts,
    )
    if payload["period"]["end"] == 0:
        click.echo("  │ Report: no persisted results, skipped")
        return

    out = _resolve_report_path(report_path, context.root, experiment.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    # atomic replace: a mid-write failure (disk full, kill) must never leave
    # a truncated file where a previous good report lived (review finding)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(render_report_html(payload), encoding="utf-8")
    os.replace(tmp, out)

    try:
        shown: Path | str = out.relative_to(context.root)
    except ValueError:
        shown = out
    verdicts = [str(v["verdict"]) for v in payload["verdicts"]]
    note = " · ".join(verdicts) if verdicts else "no verdicts yet"
    if payload["srm"]["flag"]:
        note += " · SRM FAILED"
    click.echo(click.style(f"  │ Report → {shown}  ({note})", fg="cyan"))


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
    report_path: str | None = None,
    resync_cohort: bool = False,
) -> None:
    try:
        parsed_steps = PipelineStep.parse(steps)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--steps") from exc

    validate_only = parsed_steps == [PipelineStep.VALIDATE]
    if validate_only and report_path is not None:
        raise click.BadParameter(
            "--report needs pipeline steps (validate-only runs never touch the DB)",
            param_hint="--report",
        )
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
        resync_cohort=resync_cohort,
        log=log,
    )

    click.echo()
    failed = 0
    experiments_by_name = {experiment.name: experiment for _, experiment in selected}
    report_manager = None
    report_tables = None
    generated_at = None
    try:
        for outcome in outcomes:
            if outcome.status == "completed":
                children = [
                    f"exposures: {outcome.exposures_loaded}",
                    f"cutoffs planned: {outcome.cutoffs_planned}",
                    f"results written: {outcome.results_written}",
                ]
                if PipelineStep.STATE in parsed_steps:
                    children.insert(1, f"state days: {outcome.state_days_materialized}")
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

            # ── the readout (D8): per experiment, after its pipeline, inside
            # try/except — never fail the run on a report ─────────────────────
            if report_path is None:
                continue
            if outcome.status not in ("completed", "skipped"):
                # a locked/failed pipeline withholds the report but must say
                # so — automation polling for the artifact should not have to
                # guess (review finding); the lock/error line already printed
                click.echo(
                    click.style(f"  │ Report skipped: experiment {outcome.status}", fg="yellow")
                )
                continue
            try:
                if report_tables is None:
                    from abkit.database.internal_tables import InternalTablesManager
                    from abkit.utils.datetime_utils import now_utc_naive

                    report_manager = context.manager_factory(profile)()
                    report_tables = InternalTablesManager(report_manager)
                    generated_at = now_utc_naive().strftime("%Y-%m-%d %H:%M UTC")
                _emit_experiment_report(
                    experiments_by_name[outcome.experiment],
                    report_tables,
                    context,
                    report_path,
                    generated_at or "",
                    manager=report_manager,
                    cohort_counts=outcome.exposure_counts or None,
                )
            except Exception as report_error:  # never fail the run on a report
                click.echo(click.style(f"  │ Report skipped: {report_error}", fg="yellow"))
    finally:
        if report_manager is not None:
            report_manager.close()

    total_rows = sum(o.results_written for o in outcomes)
    echo_done(
        f"{len(outcomes)} experiment(s), {total_rows} result row(s)"
        + (f", {failed} FAILED" if failed else "")
    )
    if failed:
        raise SystemExit(1)
