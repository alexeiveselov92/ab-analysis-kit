"""Implementation of ``abk unlock``.

Force-releases stale pipeline locks left behind by a run that died without
releasing them (e.g. the database restarted mid-run). Normally the lock
auto-expires after its timeout; this command clears it immediately.
"""

from __future__ import annotations

import click

from abkit.cli._output import echo_done, echo_error, echo_noop, echo_tree
from abkit.cli.commands._context import load_project_context
from abkit.config import select_experiments
from abkit.database.internal_tables import InternalTablesManager


def run_unlock(select: tuple[str, ...], profile: str | None) -> None:
    context = load_project_context()
    click.echo(f"Project root: {context.root}")

    selected, warnings = select_experiments(context.root, select)
    for warning in warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not selected:
        echo_done("Nothing selected.")
        return

    manager = context.manager_factory(profile)()
    try:
        tables = InternalTablesManager(manager)
        tables.ensure_tables()

        cleared = 0
        errors = 0
        for _, experiment in selected:
            try:
                was_locked = tables.clear_lock(experiment.name)
            except Exception as exc:
                errors += 1
                echo_error(experiment.name, f"error clearing lock: {exc}")
                continue
            if was_locked:
                cleared += 1
                echo_tree(experiment.name, ["lock cleared"])
            else:
                echo_noop(experiment.name, "no active lock")
    finally:
        manager.close()

    echo_done(f"Cleared {cleared} lock(s) of {len(selected)} experiment(s).")
    if errors:
        raise SystemExit(1)
