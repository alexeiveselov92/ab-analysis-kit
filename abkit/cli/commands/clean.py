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
        if execute:
            echo_done(f"Pruned {pruned} row(s) across {touched} experiment(s).")
        else:
            echo_done(f"Dry run: {touched} experiment(s) have orphaned series.")
    except Exception as exc:
        echo_error("clean", str(exc))
        raise SystemExit(1) from exc
    finally:
        manager.close()
