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
        # the pipeline lock (run) and the out-of-band validate lock (m4 D5) share the
        # (experiment, scope, process_type) key shape — clear both process types.
        lock_kinds = (("pipeline", "run"), ("pipeline", "validate"))
        for _, experiment in selected:
            released = []
            try:
                for scope, process_type in lock_kinds:
                    if tables.clear_lock(experiment.name, scope, process_type):
                        released.append(process_type)
            except Exception as exc:
                errors += 1
                echo_error(experiment.name, f"error clearing lock: {exc}")
                continue
            if released:
                cleared += 1
                echo_tree(experiment.name, [f"{kind} lock cleared" for kind in released])
            else:
                echo_noop(experiment.name, "no active lock")
    finally:
        manager.close()

    echo_done(f"Cleared {cleared} lock(s) of {len(selected)} experiment(s).")
    if errors:
        raise SystemExit(1)
