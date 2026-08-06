"""Implementation of ``abk run`` — the pipeline driver command.

VALIDATE (config-lint, no DB) always runs first; the effective per-comparison
alphas are echoed (the inspectable two-tier scheme, declarative-config.md §6);
SRM failures print the loud red gate line (data-contract §6). Any failed
experiment or validation error exits NON-ZERO (the CLI is the Prefect unit of
automation).

``--metric`` (m11 DASH-4a) narrows the run to one metric's comparison(s): the
selection drops experiments that do not declare it (a printed skip line), and a
value matching NOWHERE is a loud error, never a silent no-op. The alphas below
are echoed — and persisted — unfiltered: the two-tier scheme is a property of
the config, not of what one invocation recomputes.

``--notify`` (m12 NTF-1) pushes each completed experiment's readout through the
configured ``notification_channels``. Opt-in, and fail-soft on the same terms as
``--report``: a channel that raises is a yellow line, never a non-zero exit. It
reads the rows the run just persisted — it computes nothing.

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
    alpha_lines,
    echo_done,
    echo_error,
    echo_noop,
    echo_srm,
    echo_tree,
)
from abkit.cli.commands._context import load_project_context
from abkit.config import select_experiments, validate_level2
from abkit.config.experiment_config import ExperimentConfig
from abkit.pipeline import PipelineStep, RunOutcome, effective_alphas, run_experiments


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


def _additive_cost_lines(outcome: RunOutcome) -> list[str]:
    """The PERF-1 counterfactual under ``--cost-report``'s ``compute:`` line.

    Only the SLICE is a measurement (the same query deltas, attributed to the
    day-additive comparisons alone). The sentence after it deliberately claims
    less than the m9 perf gate does: that gate's "COMPUTE scans no fact rows"
    holds at DAILY cadence, while a sub-day grid still renders the current
    day's tail and every look inside the opening local day reads no day state
    at all. Nothing here estimates a number that was not observed.
    """
    status = outcome.additive
    slice_cost = outcome.stage_costs.get("compute.additive")
    if slice_cost is None or status.eligible_comparisons == 0:
        return []
    lines = [f"    of which day-additive: {slice_cost.describe()}"]
    if status.enabled:
        took = status.looks_computed - status.fallbacks
        lines.append(
            f"    → {took} of {status.looks_computed} looks took the additive "
            "path; recompute would re-scan the full window at each of them"
        )
    elif status.configured:
        # the driver already warned WHY it disabled the path for this run
        lines.append("    → the additive path is configured but was disabled for this run")
    else:
        lines.append(
            f"    → compute.incremental_reads: true would read day moments for "
            f"those {status.looks_computed} looks instead of re-scanning the "
            "full window"
        )
    return lines


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


def _verdict_note(payload: dict) -> str:
    """The verdict summary on the ``Report →`` line (m14 DEC-4, audit gap 7).

    Through `0.8.0` this was ``" · ".join(words)`` — bare words, and at three
    arms the reader could not tell which arm each belonged to. DEC-3 then put a
    verdict on every DECLARED pair, so joining them unlabeled would have added
    "WIN" for one treatment against another to a line read as ship decisions.

    So: the SHIP decisions carry their arm (``WIN treatment_b``) and the
    arm-vs-arm verdicts stay OFF this line entirely — they are evidence, and a
    one-line summary is the wrong place to explain the distinction. At 3+ arms
    the leader replaces the list, because "which arm do I ship" is the question
    the line is being read for; the per-pair words are on the page it points at.

    A two-arm line is `0.8.0`'s to the character: one ship decision whose
    treatment is the only one, so the arm suffix is dropped.
    """
    from abkit.reporting.builder import ship_decisions

    ship = ship_decisions(payload["verdicts"])
    if not ship:
        return "no verdicts yet"
    if len(payload.get("arms", [])) <= 2:
        return " · ".join(str(v["verdict"]) for v in ship)

    words = " · ".join(f"{v['verdict']} {v['pair']['t']}" for v in ship)
    leaders = [r for r in payload.get("rollups", []) if r.get("leader")]
    if not leaders:
        return words
    named = ", ".join(f"{r['leader']} ({r['metric']})" for r in leaders)
    return f"{words} · leader: {named}"


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
    note = _verdict_note(payload)
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
    cost_report: bool = False,
    metric: str | None = None,
    notify: bool = False,
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
    if validate_only and notify:
        # Same shape as --report: a notification carries a readout, and a
        # validate-only run never reads a result row to build one from.
        raise click.BadParameter(
            "--notify needs pipeline steps (validate-only runs never touch the DB)",
            param_hint="--notify",
        )
    if validate_only and metric is not None:
        # The config lint is whole-project by construction (every metric SQL,
        # every method): accepting a narrowing flag it cannot honour would read
        # as "only this metric was linted".
        raise click.BadParameter(
            "--metric needs pipeline steps (the config lint is project-wide)",
            param_hint="--metric",
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

    # ── --metric: narrow the selection to experiments declaring it ───────────
    # Repo idiom (the `abk plan`/`verify-incremental` precedent): a filter that
    # matches nothing is an error, not a quiet exit-0 that reads as success.
    if metric is not None:
        # ONE predicate, shared with DASH-4's per-metric Run route
        matching = [item for item in selected if item[1].declares_metric(metric)]
        for _, experiment in selected:
            if not experiment.declares_metric(metric):
                echo_noop(experiment.name, f"no '{metric}' comparison — skipped by --metric")
        if not matching:
            configured = sorted({c.metric for _, e in selected for c in e.comparisons})
            raise click.ClickException(
                f"--metric '{metric}' is not a comparison of any selected experiment "
                f"(have: {', '.join(configured)})"
            )
        selected = matching
        # What the run withholds, named — a generic "the other comparisons" line
        # describes comparisons that may not exist (a single-comparison
        # experiment withholds nothing), and the day-state half of the sentence
        # is only true in one of three modes (review finding). So: print the
        # names, and let the mode decide what happens to their day state.
        withheld = sorted(
            {
                comparison.metric
                for _, experiment in matching
                for comparison in experiment.comparisons
                if comparison.metric != metric
            }
        )
        if withheld:
            click.echo(
                click.style(
                    f"  ⚠ --metric {metric}: not recomputed this run: " + ", ".join(withheld),
                    fg="yellow",
                )
            )
            # What happens to the WITHHELD metrics' day state is a
            # per-experiment property of three inputs — is the `state` step even
            # selected, is this experiment in copy mode with `--resync-cohort`
            # (then the driver keeps the rebuild experiment-wide), is there a
            # refresh window (then the withheld series are truncated). Round 2 of
            # review caught a run-level `any()` here printing the copy-mode
            # sentence on behalf of direct-mode experiments — and suppressing the
            # truncation line that was the true one for them. So: classify each
            # experiment, then print one line per distinct outcome, naming
            # experiments only when the selection is heterogeneous.
            state_selected = PipelineStep.STATE in parsed_steps
            day_state_groups: dict[str, list[str]] = {}
            for _, experiment in matching:
                if not state_selected:
                    kind = "no_state_step"
                elif resync_cohort and experiment.assignment.cohort_copy.enabled:
                    kind = "rebuilt"
                elif full_refresh:
                    kind = "truncated"
                else:
                    kind = "untouched"
                day_state_groups.setdefault(kind, []).append(experiment.name)
            explanations = {
                "rebuilt": (
                    "--resync-cohort rebuilds the whole cohort — it is not "
                    "per-metric — so day state IS re-materialized for every "
                    "eligible metric (each series was derived from that copy); "
                    "only compute is narrowed"
                ),
                "truncated": (
                    "results stay as they are; any day state the withheld metrics "
                    "hold is truncated from the first day the refresh window "
                    "touches through the end of the series (not re-rendered) — a "
                    "later run that includes them re-derives it from current facts"
                ),
                "untouched": "their results and day state stay exactly as they are",
                "no_state_step": (
                    "the 'state' step is not selected, so day state is not touched "
                    "at all — a refresh window does NOT truncate it here"
                ),
            }
            for kind, names in day_state_groups.items():
                prefix = f"{', '.join(sorted(names))}: " if len(day_state_groups) > 1 else ""
                click.echo(click.style(f"    ⚠ {prefix}{explanations[kind]}", fg="yellow"))

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
        correction = (
            experiment.correction
            if experiment.correction is not None
            else context.project.statistics.correction
        )
        echo_tree(f"{experiment.name}: effective alphas", alpha_lines(alphas, correction))

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
        metric_filter=metric,
        log=log,
    )

    click.echo()
    failed = 0
    experiments_by_name = {experiment.name: experiment for _, experiment in selected}

    # m12 NTF-1: resolved once. An operator who passed --notify with nothing
    # configured must hear about it — silence there reads as a broken flag.
    notify_channels: dict = {}
    if notify:
        notify_channels = dict(context.profiles.notification_channels)
        if not notify_channels:
            click.echo(
                click.style(
                    "  ⚠ --notify: no notification_channels in profiles.yml — "
                    "nothing to send to (see docs/guides/notification-channels.md)",
                    fg="yellow",
                )
            )

    # ONE manager per invocation, shared by --report and --notify (both read
    # back the rows this run just persisted); built on first use so a run with
    # neither flag opens no extra connection.
    readback_manager = None
    readback_tables = None
    generated_at = None

    def readback():
        nonlocal readback_manager, readback_tables, generated_at
        if readback_tables is None:
            from abkit.database.internal_tables import InternalTablesManager
            from abkit.utils.datetime_utils import now_utc_naive

            readback_manager = context.manager_factory(profile)()
            readback_tables = InternalTablesManager(readback_manager)
            generated_at = now_utc_naive().strftime("%Y-%m-%d %H:%M UTC")
        return readback_tables

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
                if cost_report:
                    # m9 WP5: the evidence for the incremental-reads
                    # default-flip decision. Counters are always collected;
                    # this flag only prints them.
                    children.append("cost:")
                    for stage in ("load", "state", "compute"):
                        cost = outcome.stage_costs.get(stage)
                        if cost is not None:
                            children.append(f"  {stage}: {cost.describe()}")
                        if stage == "compute":
                            children.extend(_additive_cost_lines(outcome))
                srm_warnings = [w for w in outcome.warnings if "SRM" in w]
                other_warnings = [w for w in outcome.warnings if "SRM" not in w]
                # PERF-1: the m9 fast path shipped silent — nothing here ever
                # said it existed. It rides the warning channel because that is
                # the one that reaches the terminal (the M7 `decision_log`
                # lesson: a list nothing echoes is a list nobody reads).
                hint = outcome.additive.hint()
                if hint is not None:
                    other_warnings.append(hint)
                echo_tree(outcome.experiment, children, warnings=other_warnings)
                for warning in srm_warnings:
                    echo_srm(warning)
            elif outcome.status == "locked":
                echo_noop(outcome.experiment, outcome.error or "locked")
            elif outcome.status == "skipped":
                # the driver may name the reason (an unmatched --metric filter
                # reaching it from a non-CLI caller); otherwise it is the steps
                echo_noop(
                    outcome.experiment, outcome.error or "nothing to do for the selected steps"
                )
            else:
                failed += 1
                echo_error(outcome.experiment, outcome.error or "failed")

            # ── notify (m12 NTF-1): the just-persisted readout, pushed to the
            # configured channels. Sits BEFORE the report block on purpose —
            # that block's `continue` would skip everything after it. The whole
            # call is wrapped again here on top of dispatch's own per-channel
            # catch (§0.4 point 1): the inner one keeps one bad channel from
            # blocking the rest, this one keeps ANY notify failure — a
            # warehouse read, a config surprise — from failing the run.
            #
            # Two signals, two conditions. `completed` sends the readout — "a
            # new look landed", which a locked/skipped experiment never
            # produced. `failed` sends the error notice instead (m12 NTF-2):
            # the absence of a result is precisely what it reports, so unlike
            # the readout path it is NOT gated on persisted rows.
            if notify and notify_channels and outcome.status in ("completed", "failed"):
                try:
                    from abkit.notify.dispatch import (
                        dispatch_experiment_signals,
                        dispatch_pipeline_error,
                        dispatch_stale,
                        load_experiment_readout,
                    )

                    experiment = experiments_by_name[outcome.experiment]

                    def notify_echo(line: str) -> None:
                        click.echo(click.style(f"  │ Notify: {line}", fg="yellow"))

                    sent = 0
                    if outcome.status == "failed":
                        sent = dispatch_pipeline_error(
                            experiment=experiment,
                            error=outcome.error or "failed",
                            channels_cfg=notify_channels,
                            project_name=context.project.name,
                            echo=notify_echo,
                        )
                    else:
                        loaded = load_experiment_readout(
                            experiment, readback(), project=context.project
                        )
                        if loaded is None:
                            click.echo(
                                click.style(
                                    "  │ Notify skipped: no persisted results yet", fg="yellow"
                                )
                            )
                        else:
                            experiment_readout, result_rows = loaded
                            sent = dispatch_experiment_signals(
                                experiment=experiment,
                                readout=experiment_readout,
                                rows=result_rows,
                                channels_cfg=notify_channels,
                                project_name=context.project.name,
                                # m12 NTF-3: the same tables handle carries the
                                # dedup state, so an unchanged verdict is not
                                # re-announced on every scheduled run
                                states=readback(),
                                echo=notify_echo,
                            )
                        # m12 NTF-5: the `stale` signal, routed off the SAME
                        # backlog the PLAN stage already warned about above.
                        # Completed runs only — a failed run's news is the
                        # failure, and it has just been sent. The call is made
                        # even with an empty backlog: that is how the stored
                        # signature is cleared, so a gap that reappears next
                        # month is news again instead of deduping against a
                        # months-old row.
                        sent += dispatch_stale(
                            experiment=experiment,
                            entries=outcome.backlog,
                            channels_cfg=notify_channels,
                            project_name=context.project.name,
                            states=readback(),
                            echo=notify_echo,
                        )
                    if sent:
                        click.echo(click.style(f"  │ Notify → {sent} message(s) sent", fg="cyan"))
                except Exception as notify_error:  # never fail the run on a notification
                    click.echo(click.style(f"  │ Notify skipped: {notify_error}", fg="yellow"))

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
                _emit_experiment_report(
                    experiments_by_name[outcome.experiment],
                    readback(),
                    context,
                    report_path,
                    generated_at or "",
                    manager=readback_manager,
                    cohort_counts=outcome.exposure_counts or None,
                )
            except Exception as report_error:  # never fail the run on a report
                click.echo(click.style(f"  │ Report skipped: {report_error}", fg="yellow"))
    finally:
        if readback_manager is not None:
            readback_manager.close()

    total_rows = sum(o.results_written for o in outcomes)
    echo_done(
        f"{len(outcomes)} experiment(s), {total_rows} result row(s)"
        + (f", {failed} FAILED" if failed else "")
    )
    if failed:
        raise SystemExit(1)
