"""Implementation of ``abk clean`` — config-hash drift GC.

Editing identity-bearing method params orphans the prior ``_ab_results``
series (a new ``method_config_id``); the BI chart would show duplicate
stabilization lines. This command diffs what is STORED against what the
current YAML produces — computed through the SAME ``MethodConfig`` path the
pipeline stamps rows with, so the valid set can never drift — and prunes the
orphans. ``--orphaned-experiments`` purges experiments whose YAML no longer
exists. DRY-RUN by default; ``--execute`` (+ ``--yes`` to skip the prompt)
applies.
"""

from __future__ import annotations

import click

from abkit.cli._output import echo_done, echo_error, echo_noop, echo_tree
from abkit.cli.commands._context import ProjectContext, load_project_context
from abkit.config import select_experiments
from abkit.database.internal_tables import InternalTablesManager


def _clean_drift(
    context: ProjectContext,
    tables: InternalTablesManager,
    select: tuple[str, ...],
    execute: bool,
) -> tuple[int, int]:
    """Prune stored series whose method_config_id the YAML no longer produces."""
    selected, warnings = select_experiments(context.root, select)
    for warning in warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))

    pruned = 0
    experiments_touched = 0
    for _, experiment in selected:
        valid_ids: dict[str, set[str]] = {}
        for comparison in experiment.comparisons:
            valid_ids.setdefault(comparison.metric, set()).add(comparison.method.method_config_id)

        stored = tables.list_method_config_ids(experiment.name)
        orphans = [
            (metric, config_id, count)
            for (metric, config_id), count in sorted(stored.items())
            if config_id not in valid_ids.get(metric, set())
        ]
        if not orphans:
            echo_noop(experiment.name, "no orphaned series")
            continue

        experiments_touched += 1
        children = []
        for metric, config_id, count in orphans:
            action = "pruned" if execute else "would prune"
            children.append(f"{action} {metric} / {config_id[:16]}… ({count} rows)")
            if execute:
                tables.delete_results(
                    experiment.name,
                    metric=metric,
                    method_config_id=config_id,
                    mutations_sync=True,
                )
                pruned += count
        echo_tree(experiment.name, children)
    return experiments_touched, pruned


def _clean_state_series(
    context: ProjectContext,
    tables: InternalTablesManager,
    execute: bool,
) -> int:
    """Prune ``_ab_unit_state`` series no live (experiment, metric) claims.

    The STATE-side analogue of the drift sweep (m9 WP5). The per-run sweep in
    ``pipeline/state.py`` already drops superseded ``column_set_id``s under a
    source key it touches — this catches what a run can never revisit: a
    comparison removed, a metric renamed, an experiment deleted, or a
    comparison that stopped being STATE-eligible. Deliberately
    selection-INDEPENDENT: state rows are not experiment-keyed, so the valid
    set is computed from the WHOLE project (pruning by a narrow ``--select``
    would delete another experiment's live series).
    """
    from abkit.pipeline.state import state_eligible_metrics, state_series_key

    if not tables.unit_state_table_exists():
        return 0

    metrics_by_name = context.metrics_by_name
    valid: dict[str, str] = {}
    for _, experiment in context.experiments:
        for metric, metric_sql in state_eligible_metrics(experiment, metrics_by_name, context.root):
            source_id, series_id = state_series_key(experiment, metric, metric_sql, context.root)
            valid[source_id] = series_id

    dropped = 0
    children: list[str] = []
    for source_id in tables.list_state_sources():
        live_series = valid.get(source_id)
        for series_id in tables.list_state_column_sets(source_id):
            if series_id == live_series:
                continue
            reason = "no live metric" if live_series is None else "superseded definition"
            children.append(
                f"{'pruned' if execute else 'would prune'} state {source_id} / "
                f"{series_id[:12]}… ({reason})"
            )
            dropped += 1
            if execute:
                tables.delete_state_series(source_id, series_id)
    if children:
        echo_tree("_ab_unit_state", children)
    else:
        echo_noop("_ab_unit_state", "no orphaned state series")
    return dropped


def _clean_orphaned_experiments(
    context: ProjectContext,
    tables: InternalTablesManager,
    execute: bool,
    yes: bool,
) -> int:
    """Purge experiments that have rows in the DB but no YAML in the project."""
    known_in_db = tables.list_known_experiments()
    known_in_yaml = {config.name for _, config in context.experiments}
    orphaned = sorted(known_in_db - known_in_yaml)
    if not orphaned:
        echo_noop("orphaned-experiments", "none found")
        return 0

    purged = 0
    for name in orphaned:
        counts = tables.count_experiment_rows(name)
        summary = ", ".join(f"{table}: {count}" for table, count in counts.items() if count)
        if not execute:
            echo_tree(name, [f"would purge ({summary or 'no rows'})"])
            continue
        if not yes and not click.confirm(f"Purge ALL rows for '{name}'?", default=False):
            echo_noop(name, "skipped")
            continue
        tables.purge_experiment(name)
        purged += 1
        echo_tree(name, [f"purged ({summary or 'no rows'})"])
    return purged


def run_clean(
    select: tuple[str, ...],
    orphaned_experiments: bool,
    execute: bool,
    yes: bool,
    profile: str | None,
) -> None:
    context = load_project_context()
    click.echo(f"Project root: {context.root}")
    if not execute:
        click.echo(click.style("DRY RUN — pass --execute to apply.", fg="yellow", bold=True))

    manager = context.manager_factory(profile)()
    try:
        tables = InternalTablesManager(manager)
        tables.ensure_tables()

        if orphaned_experiments:
            purged = _clean_orphaned_experiments(context, tables, execute, yes)
            echo_done(
                f"{'Purged' if execute else 'Would purge'} {purged} experiment(s)."
                if execute
                else "Dry run complete."
            )
            return

        touched, pruned = _clean_drift(context, tables, select, execute)
        state_dropped = _clean_state_series(context, tables, execute)
        if execute:
            summary = f"Pruned {pruned} row(s) across {touched} experiment(s)."
            if state_dropped:
                summary += f" Dropped {state_dropped} orphaned state series."
            echo_done(summary)
        else:
            summary = f"Dry run: {touched} experiment(s) have orphaned series."
            if state_dropped:
                summary += f" {state_dropped} orphaned state series."
            echo_done(summary)
    except Exception as exc:
        echo_error("clean", str(exc))
        raise SystemExit(1) from exc
    finally:
        manager.close()
