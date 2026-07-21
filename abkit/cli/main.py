"""``abk`` — the ab-analysis-kit command-line interface.

dbt-like commands over declarative experiment/metric YAML (cli-and-dx.md §1).
Command bodies import lazily so ``abk --version`` stays instant and no DB
driver is required until a command actually needs one. Failures exit NON-ZERO
— the CLI is the Prefect unit of automation (a deliberate, recorded deviation
from the detectkit donor's swallow-and-return-0 behaviour).

Surface: ``init``, ``run``, ``unlock``, ``clean`` (M2) + ``explore`` (M3) +
``validate`` (M4) + ``plan`` (M5) + ``init-claude`` / ``test-report`` (M6).
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


@cli.command(name="init-claude")
@click.option(
    "--target-dir",
    "-d",
    default=".",
    help="Directory to install the Claude context into (default: current directory)",
)
def init_claude(target_dir: str) -> None:
    """Install AI-assistant context for operating this abkit project.

    Writes (idempotently) a managed block into CLAUDE.md, the reference rules
    under .claude/rules/ab-analysis-kit/, and the abk-* skills under
    .claude/skills/. Re-run after upgrading abkit to refresh the context.
    """
    from abkit.cli.commands.init_claude import run_init_claude

    run_init_claude(target_dir)


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
@click.option(
    "--resync-cohort",
    is_flag=True,
    help=(
        "Copy mode only: full cohort resync (delete + reinsert) instead of the "
        "incremental append — recovers a persisted copy the watermark cannot "
        "heal (late-arriving/corrected assignment rows). No effect in the "
        "direct (no-copy) default."
    ),
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
    resync_cohort: bool,
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
        resync_cohort=resync_cohort,
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
    help="Experiment selector — name, path glob, tag:<tag>, or * (repeatable; default all)",
)
@click.option(
    "--method",
    "-m",
    multiple=True,
    help="Extra registered method(s) to score beyond the declared comparison (repeatable)",
)
@click.option("--metric", help="Validate only this metric (default: every declared comparison)")
@click.option(
    "--iterations",
    "-n",
    type=int,
    default=None,
    help=(
        "Placebo A/A splits per cell (default: tied to each cell's effective alpha, "
        "max(2000, ceil(200/alpha)) — e.g. 4000 at the 5% main tier)"
    ),
)
@click.option(
    "--family-sweep/--no-family-sweep",
    default=False,
    help=(
        "Also run the composed multi-metric FWER/FDR sweep (D9) — roughly doubles the "
        "cost; before 0.2.0 it always ran when --metric was omitted"
    ),
)
@click.option(
    "--inject-effect",
    type=float,
    default=None,
    help="Inject this relative effect (e.g. 0.05) to measure power / achieved MDE / coverage",
)
@click.option(
    "--scoring",
    type=click.Choice(["fpr", "power", "mde"]),
    default="fpr",
    show_default=True,
    help="Selection objective for the 'Recommended' row (all columns are always computed)",
)
@click.option(
    "--report",
    "report_path",
    is_flag=False,
    flag_value="",
    default=None,
    help=(
        "Emit a self-contained HTML matrix report (best-effort). Optional value: an "
        "output file or directory; defaults to reports/<experiment>__validate.html."
    ),
)
@click.option("--force", is_flag=True, help="Take over a held validate lock (use with care)")
@click.option("--profile", help="Profile name (default: profiles.yml default_profile)")
def validate(
    select: tuple[str, ...],
    method: tuple[str, ...],
    metric: str | None,
    iterations: int | None,
    family_sweep: bool,
    inject_effect: float | None,
    scoring: str,
    report_path: str | None,
    force: bool,
    profile: str | None,
) -> None:
    """Score each method's empirical false-positive rate on placebo A/A splits.

    Out-of-band from `abk run`: measures whether each method is actually calibrated
    on this data (FPR ≈ α?), the honest cumulative-peeking FPR, power, achieved MDE,
    and CI coverage — persisted to `_ab_aa_runs` so the explore calibration chip
    lights. `--report` writes the matrix as a self-contained HTML page.
    """
    from abkit.cli.commands.validate import run_validate

    run_validate(
        select,
        method,
        metric,
        iterations,
        inject_effect,
        scoring,
        report_path,
        force,
        profile,
        family_sweep=family_sweep,
    )


@cli.command()
@click.option(
    "--select",
    "-s",
    multiple=True,
    help="Experiment selector: name, path glob, tag:<tag>, or * (repeatable; default all)",
)
@click.option("--metric", help="Plan only this comparison (default: every declared comparison)")
@click.option(
    "--mde",
    type=float,
    default=None,
    help="Target minimum detectable effect (units of the comparison's effect; "
    "default: the comparison's min_effect)",
)
@click.option("--power", type=float, default=None, help="Target power (default: project default)")
@click.option(
    "--alpha",
    type=float,
    default=None,
    help="Experiment-level significance before correction (default: experiment/project alpha)",
)
@click.option(
    "--baseline",
    multiple=True,
    help="Baseline moments override for a metric with no persisted data: "
    "'<metric>:mean=..,std=..,n=..' (sample) or '<metric>:prop=..,n=..' (fraction); repeatable",
)
@click.option(
    "--arrival-rate",
    type=float,
    default=None,
    help="Total units/day across all arms for runtime + ASN (default: derived read-only "
    "from the cohort source — the persisted copy or a live re-render of the assignment "
    "SQL; without either, runtime is skipped)",
)
@click.option("--profile", help="Profile name (default: profiles.yml default_profile)")
def plan(
    select: tuple[str, ...],
    metric: str | None,
    mde: float | None,
    power: float | None,
    alpha: float | None,
    baseline: tuple[str, ...],
    arrival_rate: float | None,
    profile: str | None,
) -> None:
    """Pre-launch power / sizing planner (read-only — no lock, no writes).

    Reports required sample size, achievable MDE, and achieved power per comparison
    from the latest persisted baseline moments (or a `--baseline` override), plus the
    projected look count and cost shape. Given an arrival rate (derived from
    the cohort source or `--arrival-rate`) it adds days-to-required-N and, for a
    `sequential.enabled` design, the always-valid average sample number (ASN). Refuses
    what it cannot size honestly (ratio / bootstrap methods).
    """
    from abkit.cli.commands.plan import run_plan

    run_plan(select, metric, mde, power, alpha, baseline, arrival_rate, profile)


@cli.command(name="test-report")
@click.argument("experiment_name", metavar="EXPERIMENT")
@click.option(
    "--channel",
    multiple=True,
    help="Test only these configured channel(s) by name (repeatable; default all)",
)
@click.option("--profile", help="Profile name (default: profiles.yml default_profile)")
def test_report(experiment_name: str, channel: tuple[str, ...], profile: str | None) -> None:
    """Send a mock readout through the configured notification channels.

    A connectivity / format check for the profiles.yml `notification_channels:`
    block: builds a synthetic WIN readout for EXPERIMENT (no lock, no warehouse
    read) and delivers it to each channel, printing a per-channel ✓/✗. Exits
    non-zero if any channel fails or is misconfigured.
    """
    from abkit.cli.commands.test_report import run_test_report

    run_test_report(experiment_name, channel, profile)


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
