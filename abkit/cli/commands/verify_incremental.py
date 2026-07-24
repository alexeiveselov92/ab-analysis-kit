"""Implementation of ``abk verify-incremental`` — the reconciliation gate (m9 WP5).

The command that makes flipping ``compute.incremental_reads`` a data-driven
decision instead of a leap of faith: for every already-computed cutoff of
every STATE-eligible comparison it loads the data through BOTH backends and
diffs the results at the project's rel-1e-9 tolerance
(``abkit/compute/reconcile.py`` is the engine; cumulative-intervals.md §4
asks for the WHOLE series, not just the latest cutoff).

Posture (deliberately unlike ``abk validate``): **read-only, lock-free.** It
persists nothing, so it never races a running pipeline for the ``_ab_tasks``
lock; it also costs strictly more than the run it checks, so it is an
explicit on-demand maintainer command and never part of ``abk run``. Any
divergence — or any experiment that fails outright — exits NON-ZERO.
"""

from __future__ import annotations

import click

from abkit.cli._output import echo_done, echo_error, echo_noop, echo_tree
from abkit.cli.commands._context import load_project_context
from abkit.compute.reconcile import DEFAULT_REL_TOL, ReconcileOutcome, reconcile_experiment
from abkit.config import select_experiments

#: how many diverging cutoffs to print in full before summarising the rest
_MAX_REPORTED_MISMATCHES = 10


def run_verify_incremental(
    select: tuple[str, ...],
    metric: str | None,
    rel_tol: float,
    profile: str | None,
) -> None:
    context = load_project_context(require_profiles=True)
    click.echo(f"Project root: {context.root}")

    selected, selection_warnings = select_experiments(context.root, select)
    for warning in selection_warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not selected:
        echo_done("Nothing selected.")
        return

    failed = 0
    diverged = 0
    for _, experiment in selected:
        outcome = _verify_one(experiment, context, profile, metric, rel_tol)
        if outcome.error is not None:
            failed += 1
        elif not outcome.ok:
            diverged += 1

    summary = f"{len(selected)} experiment(s) verified"
    if diverged:
        summary += f", {diverged} DIVERGED"
    if failed:
        summary += f", {failed} FAILED"
    echo_done(summary)
    if diverged or failed:
        raise SystemExit(1)


def _verify_one(experiment, context, profile, metric, rel_tol) -> ReconcileOutcome:
    manager = context.manager_factory(profile)()
    try:
        from abkit.database.internal_tables import InternalTablesManager

        tables = InternalTablesManager(manager)
        if not tables.results_table_exists():
            echo_noop(experiment.name, "no _ab_results yet — run `abk run` first")
            return ReconcileOutcome(experiment=experiment.name)
        outcome = reconcile_experiment(
            experiment,
            context.metrics_by_name,
            context.project,
            manager,
            tables,
            project_root=context.root,
            metric_filter=metric,
            rel_tol=rel_tol,
        )
    except Exception as exc:  # noqa: BLE001 - one bad experiment must not kill the sweep
        echo_error(experiment.name, f"{type(exc).__name__}: {exc}")
        return ReconcileOutcome(experiment=experiment.name, error=str(exc))
    finally:
        manager.close()

    _render(outcome, rel_tol)
    return outcome


def _render(outcome: ReconcileOutcome, rel_tol: float) -> None:
    matched = outcome.matched
    unverified = outcome.unverified
    mismatches = outcome.mismatches

    children = [
        f"cutoffs checked: {outcome.cutoffs_checked}",
        f"pair comparisons: {len(matched)} matched at rel_tol={rel_tol:g}",
    ]
    if unverified:
        # An unverified cutoff is NOT a pass — the incremental read fell back,
        # so both sides ran the same code and agreeing proves nothing.
        children.append(
            f"unverified: {len(unverified)} (the incremental read fell back to "
            "recompute — run the `state` step so the series covers them)"
        )
    if mismatches:
        children.append(click.style(f"DIVERGED: {len(mismatches)} pair comparison(s)", fg="red"))
        for verdict in mismatches[:_MAX_REPORTED_MISMATCHES]:
            head = (
                f"  {verdict.metric} {verdict.name_1}/{verdict.name_2} "
                f"@ {verdict.end_ts:%Y-%m-%d %H:%M:%S}"
            )
            children.append(click.style(head, fg="red"))
            for diff in verdict.diffs:
                children.append(click.style(f"    {diff.describe()}", fg="red"))
        if len(mismatches) > _MAX_REPORTED_MISMATCHES:
            children.append(
                click.style(
                    f"  … and {len(mismatches) - _MAX_REPORTED_MISMATCHES} more", fg="red"
                )
            )
    for skip in outcome.skipped:
        children.append(f"skipped {skip.metric}: {skip.reason}")

    if not outcome.verdicts and not outcome.skipped:
        children.append("nothing to reconcile")

    echo_tree(outcome.experiment, children)


__all__ = ["DEFAULT_REL_TOL", "run_verify_incremental"]
