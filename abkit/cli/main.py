"""``abk`` — the ab-analysis-kit command-line interface.

dbt-like commands over declarative experiment/metric YAML (cli-and-dx.md §1).
Command bodies import lazily so ``abk --version`` stays instant and no DB
driver is required until a command actually needs one. Failures exit NON-ZERO
— the CLI is the Prefect unit of automation (a deliberate, recorded deviation
from the detectkit donor's swallow-and-return-0 behaviour).

Surface: ``init``, ``run``, ``unlock``, ``clean`` (M2) + ``explore`` (M3).
The remaining commands (``validate``, ``plan``, ``init-claude``,
``test-report``) land per ROADMAP.md M4–M6.
"""

from __future__ import annotations

import click

from abkit import __version__


@click.group()
@click.version_option(__version__, prog_name="abk")
def cli() -> None:
    """ab-analysis-kit — declarative A/B experiment analysis.

    Examples:
        abk init my_ab_project
        abk run --select signup_test
        abk run --steps validate
        abk run --select tag:actual

    Docs: https://abkit.pipelab.dev
    """


@cli.command()
@click.argument("project_name")
@click.option(
    "--target-dir",
    "-d",
    default=".",
    help="Directory to create the project in (default: current directory)",
)
@click.option(
    "--db-type",
    type=click.Choice(["clickhouse", "postgres", "mysql"]),
    default="clickhouse",
    show_default=True,
    help="Database backend to scaffold the profiles and example SQL for.",
)
def init(project_name: str, target_dir: str, db_type: str) -> None:
    """Initialize a new abkit project (with a runnable example experiment).

    Creates abkit_project.yml, profiles.yml, experiments/, metrics/, sql/
    and a seed-dataset example so `abk run --select example_signup_test`
    produces real results on a fresh machine.
    """
    from abkit.cli.commands.init import run_init

    run_init(project_name, target_dir, db_type=db_type)


@cli.command()
@click.option(
    "--select",
    "-s",
    multiple=True,
    help="Experiment selector: name, path glob, tag:<tag>, or * (repeatable; default all)",
)
@click.option("--exclude", multiple=True, help="Selectors to exclude (same forms)")
@click.option(
    "--steps",
    default="validate,plan,load,compute",
    show_default=True,
    help="Comma-separated pipeline steps. 'validate' alone = config-lint, no DB.",
)
@click.option("--profile", help="Profile name (default: profiles.yml default_profile)")
@click.option("--from", "from_ts", help="Full-refresh window start (with --full-refresh)")
@click.option("--to", "to_ts", help="Full-refresh window end, exclusive (with --full-refresh)")
@click.option(
    "--full-refresh",
    is_flag=True,
    help="Re-open already-computed cutoffs in [--from, --to) and recompute them",
)
@click.option("--force", is_flag=True, help="Take over a held lock (use with care)")
@click.option(
    "--workers",
    default=1,
    show_default=True,
    help="Worker threads across experiments (each gets its own DB connection)",
)
@click.option(
    "--report",
    "report_path",
    is_flag=False,
    flag_value="",
    default=None,
    help=(
        "After the run, emit a self-contained HTML readout per experiment "
        "(best-effort — never fails the run). Optional value: an output file "
        "or directory; defaults to reports/<experiment>.html."
    ),
)
def run(
    select: tuple[str, ...],
    exclude: tuple[str, ...],
    steps: str,
    profile: str | None,
    from_ts: str | None,
    to_ts: str | None,
    full_refresh: bool,
    force: bool,
    workers: int,
    report_path: str | None,
) -> None:
    """Run the pipeline: validate → plan → load → SRM → compute → persist."""
    from abkit.cli.commands.run import run_run

    run_run(
        select,
        exclude,
        steps,
        profile,
        from_ts,
        to_ts,
        full_refresh,
        force,
        workers,
        report_path,
    )


@cli.command()
@click.option(
    "--select",
    "-s",
    multiple=True,
    help="Experiment selector — must match exactly ONE experiment",
)
@click.option("--metric", help="Open the cockpit on this comparison (default: the main metric)")
@click.option("--profile", help="Profile name (default: profiles.yml default_profile)")
@click.option(
    "--no-serve",
    is_flag=True,
    help="Write a static snapshot to reports/<experiment>__explore.html instead of serving",
)
@click.option("--no-open", is_flag=True, help="Do not launch a browser (the URL still prints)")
def explore(
    select: tuple[str, ...],
    metric: str | None,
    profile: str | None,
    no_serve: bool,
    no_open: bool,
) -> None:
    """Serve the interactive explore cockpit for one experiment.

    Reads the persisted results (run `abk run` first), lets you tune method
    knobs live against a localhost page, and — only on an explicit Apply —
    writes the tuned config back to the experiment YAML (the previous file is
    archived under experiments/.history/).
    """
    from abkit.cli.commands.explore import run_explore

    run_explore(select, metric, profile, no_serve, no_open)


@cli.command()
@click.option(
    "--select",
    "-s",
    multiple=True,
    help="Experiment selector (name, glob, tag:<tag>, *; default all)",
)
@click.option("--profile", help="Profile name (default: profiles.yml default_profile)")
def unlock(select: tuple[str, ...], profile: str | None) -> None:
    """Clear stale pipeline locks left by a run that died."""
    from abkit.cli.commands.unlock import run_unlock

    run_unlock(select, profile)


@cli.command()
@click.option(
    "--select",
    "-s",
    multiple=True,
    help="Experiment selector (name, glob, tag:<tag>, *; default all)",
)
@click.option(
    "--orphaned-experiments",
    is_flag=True,
    help="Purge experiments that have DB rows but no YAML in the project",
)
@click.option("--execute", is_flag=True, help="Apply the changes (default: dry run)")
@click.option("--yes", is_flag=True, help="Skip the per-experiment purge confirmation")
@click.option("--profile", help="Profile name (default: profiles.yml default_profile)")
def clean(
    select: tuple[str, ...],
    orphaned_experiments: bool,
    execute: bool,
    yes: bool,
    profile: str | None,
) -> None:
    """Prune orphaned result series (method_config_id drift) and removed experiments."""
    from abkit.cli.commands.clean import run_clean

    run_clean(select, orphaned_experiments, execute, yes, profile)


if __name__ == "__main__":
    cli()
