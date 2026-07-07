"""Implementation of ``abk test-report`` — the notification-channel smoke test
(cli-and-dx.md §1; m6-implementation-plan.md WP5).

Builds a purely synthetic readout for the named experiment (no lock, no warehouse
read — the mock exercises connectivity + message formatting only) and sends it
through every ``notification_channels:`` entry in profiles.yml, printing a
per-channel ✓/✗. Exits non-zero if any channel fails or is misconfigured (abkit's
CLI-is-the-automation-unit convention — the detectkit donor swallowed failures
and returned 0; abkit does not).
"""

from __future__ import annotations

import click

from abkit.cli._output import echo_done, echo_tree
from abkit.cli.commands._context import load_project_context
from abkit.config import select_experiments


def run_test_report(
    experiment_name: str,
    channel: tuple[str, ...],
    profile: str | None,
) -> None:
    context = load_project_context(require_profiles=True)
    click.echo(f"Project root: {context.root}")

    channels_cfg = context.profiles.notification_channels
    if not channels_cfg:
        raise click.ClickException(
            "No notification_channels configured in profiles.yml — add a "
            "notification_channels: block (see docs/guides/notification-channels.md)."
        )

    if channel:
        unknown = [c for c in channel if c not in channels_cfg]
        if unknown:
            raise click.BadParameter(
                f"unknown channel(s): {', '.join(unknown)} "
                f"(configured: {', '.join(channels_cfg)})",
                param_hint="--channel",
            )
        # honour the order the channels were given on the command line (dedup)
        selected_names = list(dict.fromkeys(channel))
    else:
        selected_names = list(channels_cfg)

    readout = _build_mock(experiment_name, context)

    from abkit.notify import ChannelFactory

    children: list[str] = []
    ok_count = 0
    failed = 0
    for name in selected_names:
        cfg = channels_cfg[name]
        try:
            ch = ChannelFactory.create_from_config(cfg.model_dump())
        except Exception as exc:
            children.append(f"✗ {name} [{cfg.type}]: config error — {exc}")
            failed += 1
            continue
        try:
            sent = ch.send(readout)
        except Exception as exc:  # a channel that raises despite the bool contract
            children.append(f"✗ {name} [{cfg.type}]: {exc}")
            failed += 1
            continue
        if sent:
            children.append(f"✓ {name} [{cfg.type}]: sent")
            ok_count += 1
        else:
            children.append(f"✗ {name} [{cfg.type}]: send failed")
            failed += 1

    echo_tree(
        f"test-report {experiment_name}: mock {readout.verdict} readout → "
        f"{ok_count}/{len(selected_names)} channel(s)",
        children,
    )
    echo_done(
        f"Sent mock readout to {ok_count}/{len(selected_names)} channel(s)"
        + (f" — {failed} FAILED" if failed else ".")
    )
    if failed:
        raise SystemExit(1)


def _build_mock(experiment_name: str, context):
    """A synthetic readout labelled from the experiment config (no DB read)."""
    from abkit.notify import create_mock_readout
    from abkit.notify.branding import READOUT_GUIDE_URL

    selected, _warnings = select_experiments(context.root, (experiment_name,))
    if not selected:
        available = ", ".join(sorted(e.name for _, e in context.experiments)) or "(none)"
        raise click.ClickException(
            f"Experiment '{experiment_name}' not found. Available: {available}"
        )
    _, experiment = selected[0]

    variants = experiment.assignment.variants
    name_1 = variants[0] if variants else "control"
    name_2 = variants[1] if len(variants) > 1 else "treatment"

    comparisons = experiment.comparisons
    main = next(
        (c for c in comparisons if c.is_main_metric), comparisons[0] if comparisons else None
    )
    metric = main.metric if main is not None else "example_metric"

    alpha = _effective_alpha(experiment, context.project, main)

    return create_mock_readout(
        experiment=experiment.name,
        metric=metric,
        name_1=name_1,
        name_2=name_2,
        alpha=alpha,
        project_name=context.project.name,
        description=experiment.description,
        help_url=READOUT_GUIDE_URL,
    )


def _effective_alpha(experiment, project, comparison) -> float:
    """The effective post-correction per-comparison alpha (never re-derived here).

    Best-effort — a mock label; falls back to 0.05 if the resolver is unhappy.
    """
    if comparison is None:
        return 0.05
    try:
        from abkit.pipeline import comparison_alpha, effective_alphas

        return comparison_alpha(comparison, effective_alphas(experiment, project))
    except Exception:
        return 0.05
