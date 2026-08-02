"""``abk dashboard`` — the project-level cockpit shell (m11 plan DASH-6).

Orchestration only, and deliberately thinner than ``abk explore``'s: resolve the
selection (MANY experiments, not one), open ONE manager, and hand
``tuning.serve_dashboard`` the pieces DASH-3/DASH-5 declared. Every number on the
page is computed later, per row, by the server — this module reads nothing from
the warehouse and takes no lock.

Three deliberate differences from ``run_explore``, each a consequence of the
dashboard being a **launcher, not a reader**:

* **No single-experiment restriction.** ``--select``/``--exclude`` resolve the
  whole selection, exactly as ``abk run`` does; the dashboard is the surface that
  exists to show them side by side. They are also handed to the server, which
  re-resolves them after every YAML edit (UI-1's reload).
* **No never-run noop.** ``abk explore`` refuses a project with no persisted rows
  (there is nothing to tune). Here that project is the normal first case: rows
  render "no data — press Run", and the Run button is the answer. So a missing
  ``_ab_results`` is not an error, and nothing here creates it — the dashboard
  never writes schema.
* **No startup orphan scan.** Explore's is one ``list_method_config_ids`` query
  for its one experiment. Doing it per row here would put N warehouse queries in
  front of a page whose whole design is a metadata-only boot (DASH-3), so the
  orphan warning stays where a per-experiment command already prints it
  (``abk run``, ``abk explore``, ``abk clean``).

It also owns the one startup warning DASH-4 deferred here: a job is a spawned
``abk`` process with the CWD dropped from ``sys.path``, so an abkit that is not
installed makes EVERY button fail identically — said once, before the page
opens, instead of N times in the drawer
(:func:`_spawned_jobs_can_import_abkit`).

Failures raise ``click.ClickException`` → non-zero exit (the house rule).
"""

from __future__ import annotations

import importlib.machinery
import os
import sys
from pathlib import Path

import click

from abkit.cli._output import echo_done
from abkit.cli.commands._context import load_project_context
from abkit.config import select_experiments


def _spawned_jobs_can_import_abkit(project_root: Path) -> bool:
    """Would a job spawned by a button be able to import abkit at all?

    Every job runs ``python -c`` with ``''`` and its own CWD (the project root)
    dropped from ``sys.path`` before the first import — deliberately, so a
    ``click.py`` sitting in the operator's project cannot shadow the real one
    (``dashboard_server._CLI_BOOTSTRAP``). The consequence DASH-4 recorded and
    left to this command: from a bare source checkout that was never installed,
    the dropped directory is the ONLY place abkit lives, so every job dies with
    ``ModuleNotFoundError`` in its own drawer — N identical failures nobody can
    read a cause out of. Warn ONCE, here, before the cockpit opens.

    It asks the child's own question — *does ``abkit`` resolve without the
    CWD?* — through both import mechanisms, because either alone answers wrong:
    a ``sys.path`` search misses a **strict editable** install (setuptools puts
    a finder in ``sys.meta_path``, so nothing on ``sys.path`` resolves
    ``abkit``), and the meta-path finders alone miss an ordinary site-packages
    or ``PYTHONPATH`` install. Distribution *metadata* is deliberately NOT a
    signal: it is present and stale in a checkout whose install was removed, and
    trusting it there suppresses the warning in exactly the environment that
    needs it (found by the DASH-7 exit gate, whose own spawning legs skip in
    such a checkout). Probed in-process rather than by spawning
    ``abk --version``: same answer, no process per startup.
    """
    dropped = {"", os.getcwd(), str(project_root)}
    paths = [entry for entry in sys.path if entry not in dropped]
    try:
        if importlib.machinery.PathFinder.find_spec("abkit", paths) is not None:
            return True
    except (ImportError, ValueError, AttributeError):
        pass
    for finder in sys.meta_path:
        if finder is importlib.machinery.PathFinder:
            continue  # already asked, over the REDUCED path
        find_spec = getattr(finder, "find_spec", None)
        if find_spec is None:
            continue
        try:
            if find_spec("abkit", None) is not None:
                return True
        except Exception:  # noqa: BLE001 — a third-party finder must not break a cockpit
            continue
    return False


def run_dashboard(
    select: tuple[str, ...],
    exclude: tuple[str, ...],
    profile: str | None,
    window: str,
    no_open: bool,
) -> None:
    from abkit.database.internal_tables import InternalTablesManager
    from abkit.tuning import UnknownWindowPreset, serve_dashboard

    context = load_project_context(require_profiles=True)
    selected, selection_warnings = select_experiments(context.root, select, exclude)
    for warning in selection_warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not selected:
        # The house idiom for an empty selection (`abk run`/`abk validate`): the
        # unmatched-selector warning above already said what happened, and
        # serving a cockpit with zero rows would be a page about nothing.
        echo_done("Nothing selected.")
        return

    click.echo(click.style(f"Dashboard: {context.project.name}", fg="cyan", bold=True))
    click.echo(f"  │ {len(selected)} experiment(s) selected — verdicts load per row, on demand.")
    if not _spawned_jobs_can_import_abkit(context.root):
        click.echo(
            click.style(
                "  ⚠ abkit is not installed in this interpreter — the page will load, "
                "but every button spawns a job that cannot import abkit "
                "(ModuleNotFoundError). Install it first: `pip install -e .` "
                "(or `pip install ab-analysis-kit`).",
                fg="yellow",
                bold=True,
            )
        )

    manager = context.profiles.create_manager(profile)
    try:
        serve_dashboard(
            project=context.project,
            project_root=context.root,
            experiments=selected,
            tables=InternalTablesManager(manager),
            initial_window=window,
            profile=profile,
            # DASH-5's Open button renders the same report `abk run --report`
            # writes: the metric configs become its metric descriptions, and the
            # raw manager (the one `tables` wraps, used under the server's
            # db_lock) lets the no-copy default snapshot the live cohort for the
            # SRM chip's observed counts. Both degrade in the open without us.
            metrics=context.metrics_by_name,
            manager=manager,
            # UI-1: the editor's reload re-resolves THIS selection, so the
            # selectors have to travel with it — re-deriving them server-side
            # would silently widen an edited page to the whole project.
            selectors=select,
            excludes=exclude,
            open_browser=not no_open,
            echo=click.echo,
        )
    except UnknownWindowPreset as exc:
        # Raised at boot, before a socket exists — where the operator typed it.
        # The server's OTHER startup refusal (a duplicated experiment name) gets
        # no handler on purpose: `select_experiments` already enforces uniqueness
        # over the one global namespace, so reaching it would be an abkit bug,
        # and a bug deserves its traceback rather than a tidy `Error:` line.
        raise click.ClickException(f"--window: {exc}") from exc
    finally:
        manager.close()

    echo_done("Dashboard stopped.")
