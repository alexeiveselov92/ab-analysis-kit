"""``abk explore`` — the interactive cockpit shell (WP8; cli-and-dx.md §1).

Orchestration only: resolve exactly ONE experiment, guard on a never-run
project (friendly noop, D2), print the startup orphan warning (the same
``list_method_config_ids`` scan the driver and ``abk clean`` use), build the
WP2 report payload + the WP4 session (progress streamed through the house
``StageLogRenderer``), then either write the static snapshot (``--no-serve``)
or serve the WP6 localhost cockpit until the user Applies or cancels. All
failures raise ``click.ClickException`` → non-zero exit (the house rule).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import click

from abkit.cli._output import StageLogRenderer, echo_done, echo_noop
from abkit.cli.commands._context import load_project_context
from abkit.config import select_experiments
from abkit.utils.datetime_utils import now_utc_naive

#: session-load stage titles for the run-log tree
_EXPLORE_STAGE_TITLES = {"SERIES": "SESSION", "CACHE": "CACHE"}


def _atomic_write_text(path: Path, text: str) -> None:
    """temp + ``os.replace`` so a mid-write failure never truncates a
    previous good snapshot (the WP3 report-emission precedent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def run_explore(
    select: tuple[str, ...],
    metric: str | None,
    profile: str | None,
    no_serve: bool,
    no_open: bool,
) -> None:
    from abkit.compute.recompute_backend import RecomputeBackend
    from abkit.database.internal_tables import InternalTablesManager
    from abkit.reporting import build_report_payload
    from abkit.tuning import (
        RecomputeEngine,
        backend_cutoff_loader,
        build_explore_payload,
        load_session,
        render_explore_html,
        serve_explore,
    )

    context = load_project_context(require_profiles=True)
    selected, selection_warnings = select_experiments(context.root, select)
    for warning in selection_warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not selected:
        raise click.ClickException(
            "no experiment matched — `--select` resolves the experiment "
            "namespace (a name, path glob, tag:<tag>, or *)"
        )
    if len(selected) > 1:
        names = ", ".join(experiment.name for _, experiment in selected)
        raise click.ClickException(
            f"explore serves ONE experiment; the selection matched {len(selected)}: "
            f"{names} — narrow --select"
        )
    experiment_path, experiment = selected[0]
    metrics_by_name = context.metrics_by_name

    configured_metrics = [comparison.metric for comparison in experiment.comparisons]
    if metric is not None and metric not in configured_metrics:
        raise click.ClickException(
            f"--metric '{metric}' is not a configured comparison of "
            f"'{experiment.name}' (have: {', '.join(configured_metrics)})"
        )

    click.echo(click.style(f"Explore: {experiment.name}", fg="cyan", bold=True))

    manager = context.profiles.create_manager(profile)
    try:
        tables = InternalTablesManager(manager)

        # D2 guard: explore reads persisted rows — a never-run project noops.
        stored_ids = (
            tables.list_method_config_ids(experiment.name)
            if (tables.results_table_exists())
            else {}
        )
        if not stored_ids:
            echo_noop(
                experiment.name,
                f"no computed results yet — run `abk run --select {experiment.name}` first",
            )
            return

        # Startup orphan warning: the driver-identical scan (cli-and-dx §2).
        configured_ids = {
            comparison.metric: comparison.method.method_config_id
            for comparison in experiment.comparisons
        }
        for metric_name in configured_metrics:
            ids = {mc_id for (m, mc_id) in stored_ids if m == metric_name}
            orphaned = ids - {configured_ids[metric_name]}
            if orphaned:
                click.echo(
                    click.style(
                        f"  ⚠ {experiment.name}/{metric_name}: {len(orphaned)} orphaned "
                        "method_config_id series in _ab_results (the BI chart will "
                        "show duplicate stabilization lines) — run `abk clean`",
                        fg="yellow",
                        bold=True,
                    )
                )

        # The one session load pass (D2), streamed in the house tree style.
        renderer = StageLogRenderer(titles=_EXPLORE_STAGE_TITLES)

        def log(message: str) -> None:
            stage, _, rest = message.partition(" ")
            renderer(stage, rest.strip())

        metric_sql_by_name = {
            name: metrics_by_name[name].get_query_text(context.root) for name in configured_metrics
        }
        backend = RecomputeBackend(manager, experiment)
        session = load_session(
            experiment,
            metrics_by_name,
            context.project,
            tables,
            loader=backend_cutoff_loader(backend, metric_sql_by_name),
            log=log,
        )
        engine = RecomputeEngine(session)

        report_payload = build_report_payload(
            experiment,
            tables,
            project=context.project,
            metric_configs=metrics_by_name,
            generated_at=now_utc_naive().strftime("%Y-%m-%d %H:%M UTC"),
        )
        payload = build_explore_payload(session, engine, report_payload)
        if metric is not None:
            payload["explore"]["default_metric"] = metric

        if no_serve:
            out = context.root / "reports" / f"{experiment.name}__explore.html"
            _atomic_write_text(out, render_explore_html(payload))
            try:
                shown = out.relative_to(Path.cwd())
            except ValueError:
                shown = out
            click.echo(click.style(f"  │ Explore snapshot → {shown}", fg="cyan"))
            echo_done("Static explore page written (Apply disabled — serve to tune).")
            return

        applied = serve_explore(
            payload=payload,
            original_path=experiment_path,
            project_root=context.root,
            session=session,
            engine=engine,
            tables=tables,
            metrics_by_name=metrics_by_name,
            manager_factory=context.manager_factory(profile),
            metric_sql_by_name=metric_sql_by_name,
            open_browser=not no_open,
            echo=click.echo,
        )
    finally:
        manager.close()

    if applied is None:
        echo_done(f"explore cancelled — {experiment.name} unchanged.")
        return

    # The Apply epilogue (donor tune.py reshaped): archive, roles, the orphan
    # consequence, and the re-run hint — Apply never auto-runs (D4).
    try:
        archive_shown = applied.archived.relative_to(context.root)
    except ValueError:
        archive_shown = applied.archived
    lines = [f"Archived previous config: {archive_shown}"]
    if applied.updated:
        lines.append(f"Updated comparison(s): {', '.join(applied.updated)}")
    if applied.preserved:
        lines.append(f"Preserved: {', '.join(applied.preserved)}")
    if applied.experiment_fields:
        lines.append(f"Experiment-level: {', '.join(applied.experiment_fields)}")
    for line in lines:
        click.echo(f"  │ {line}")
    warning = applied.orphan_warning
    if warning:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow", bold=True))
    click.echo(f"  └ Re-run `abk run --select {experiment.name}` to compute the new series.")
    echo_done(f"Applied to {applied.saved}.")
