"""Turn a just-completed experiment's PERSISTED rows into channel messages.

The seam ``abk run --notify`` calls (m12-implementation-plan.md NTF-1). Three
properties hold it together, and each exists because its opposite is a defect
this milestone was written to avoid:

* **Nothing here computes a statistic.** The verdict is
  :func:`abkit.pipeline.readout.evaluate`'s — the same function
  ``build_report_payload`` calls — over the same persisted ``_ab_results``
  rows, so a message can never disagree with the report or the dashboard about
  the same experiment. Every number is copied off a :class:`PairVerdict`.
* **Fail-soft is the contract, not a courtesy.** A channel that raises, a
  channel misconfigured, a warehouse that answers nothing — none of it may
  change the run's exit code. Each channel is sent inside its own
  ``try/except`` here, and ``run.py`` wraps the whole call again
  (deliberate defense-in-depth, §0.4 point 1 — a later simplify pass must not
  collapse the two into one).
* **Opt-in twice over, in the two places that mean different things.** The
  ``--notify`` flag is the operator saying "send"; ``experiment.notify`` is the
  experiment saying "to whom, and about what". With the flag and NO experiment
  block, every configured channel receives every kind (D1, maintainer-signed
  2026-08-02) — an operator who wired channels up in ``profiles.yml`` never has
  to touch experiment YAML to hear from them.

Five of the six kinds fire from here: ``readout`` and its ``srm``
re-classification (NTF-1/NTF-2) off the persisted rows, ``error`` off a failed
run (NTF-2), and the two RECURRING ones — ``stale`` and ``calibration_red``
(NTF-5) — whose condition survives the run that reports it and is therefore
deduped by signature plus an optional cooldown.

``verdict_change`` (NTF-6) is the sixth, and it is narrower than "was
delivered": a readout whose verdict WORD differs from the one last announced.
A first-ever readout and one re-sent because its SRM gate moved are both
delivered without a flip, so the filter means what an operator writing it
would expect.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from itertools import combinations
from typing import Any

from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.project_config import ProjectConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._notify_states import notice_state_key
from abkit.notify.base import ReadoutData, describe_error
from abkit.notify.branding import READOUT_GUIDE_URL
from abkit.notify.cooldown import (
    recurring_signature,
    should_announce,
    should_announce_recurring,
)
from abkit.pipeline._types import BacklogEntry
from abkit.pipeline.readout import ExperimentReadout, PairVerdict, evaluate
from abkit.utils.datetime_utils import now_utc_naive, to_naive_utc

#: What a caller passes to receive the yellow one-liners this module produces.
#: ``run.py`` hands it ``click.echo``-with-style; tests hand it a list append.
Echo = Callable[[str], None]


def declared_pairs_only(experiment: ExperimentConfig, rows: Sequence[dict]) -> list[dict]:
    """Drop rows whose arm pair is not among the CURRENTLY declared variants.

    The third copy of a filter ``reporting/builder.py`` and ``tuning/overview.py``
    each carry, and it is load-bearing on all three: ``readout._filter_rows``
    drops rows by metric and ``method_config_id`` only, while the read-time
    Benjamini-Hochberg family is built from EVERY informative row at a cutoff —
    so rows left behind by a mid-flight arm rename would tighten every member's
    threshold and this surface would contradict ``abk run --report`` on
    identical rows (the m11 DASH-2 finding). A fourth copy should force the
    extraction; three still read better mirrored than routed through a new
    shared module.
    """
    declared = set(combinations(experiment.assignment.variants, 2))
    return [row for row in rows if (str(row["name_1"]), str(row["name_2"])) in declared]


def load_experiment_readout(
    experiment: ExperimentConfig,
    tables: InternalTablesManager,
    *,
    project: ProjectConfig | None = None,
) -> tuple[ExperimentReadout, list[dict]] | None:
    """The readout for *experiment* over its persisted rows, or ``None``.

    ``None`` means "there is nothing to say": no ``_ab_results`` table (a
    project that has never run) or no rows for this experiment. Both must stay
    silent rather than notify — ``evaluate()`` over zero rows answers
    INCONCLUSIVE, which is a verdict about DATA, and sending it for an
    experiment nobody computed would report a finding where there is not even
    an observation (the m11 DASH-7 finding, in message form).

    The rows ride back with the readout because the verdict does not carry
    per-arm sample sizes and the message renders them.
    """
    if not tables.results_table_exists():
        return None
    loaded = tables.load_results(experiment.name)
    if not loaded:
        return None
    rows = declared_pairs_only(experiment, loaded)
    if not rows:
        return None
    return evaluate(experiment, rows, project=project), rows


def passes_filter(
    kind: str,
    channel_on: Sequence[str] | None,
    experiment_on: Sequence[str] | None,
) -> bool:
    """Does *kind* survive both ``on:`` filters? ``None`` means "every kind".

    An INTERSECTION, never a union: the experiment's filter narrows what it
    ever sends, the channel's narrows what it ever accepts, and the two answer
    different questions ("do I care about this experiment's SRM?" vs "is this
    channel the on-call one?"). A union would let either side re-open what the
    other closed.
    """
    if experiment_on is not None and kind not in experiment_on:
        return False
    if channel_on is not None and kind not in channel_on:
        return False
    return True


def resolve_channels(
    experiment: ExperimentConfig,
    channels_cfg: dict[str, Any],
) -> tuple[list[tuple[str, Any]], list[str]]:
    """The (name, config) channels this experiment sends to, plus warnings.

    D1: no ``notify.channels`` means EVERY configured channel. A named channel
    that does not exist in ``profiles.yml`` is a warning, never an error — this
    path may not fail a run, and a typo that silently sent to nothing would be
    worse than a loud line naming what is configured.
    """
    warnings: list[str] = []
    names = list(experiment.notify.channels) if experiment.notify is not None else []
    if not names:
        return list(channels_cfg.items()), warnings

    resolved: list[tuple[str, Any]] = []
    for name in dict.fromkeys(names):  # honour the declared order, drop repeats
        cfg = channels_cfg.get(name)
        if cfg is None:
            configured = ", ".join(sorted(channels_cfg)) or "(none)"
            warnings.append(
                f"{experiment.name}: notify.channels names '{name}', which is not "
                f"in profiles.yml notification_channels (configured: {configured})"
            )
            continue
        resolved.append((name, cfg))
    return resolved, warnings


def _method_config_id(experiment: ExperimentConfig, metric: str) -> str:
    """The comparison's identity hash — part of the dedup key (§0.4 point 2).

    Read from the CONFIG, mirroring ``evaluate()``'s own lookup
    (``readout.py``'s ``_filter_rows``): the rows that produced this verdict
    were already filtered to exactly this id, and ``ExperimentConfig``
    validation rejects duplicate metric references, so the mapping is total and
    unambiguous. A metric with no comparison cannot reach here (the verdict came
    from one), but answer "" rather than raise if it ever does — a dedup key is
    not worth failing a run over.
    """
    for comparison in experiment.comparisons:
        if comparison.metric == metric:
            return comparison.method.method_config_id
    return ""


def _is_relative(comparison: ComparisonConfig | None) -> bool:
    """Is this comparison's persisted effect a relative lift or an absolute one?

    Read off the CONFIG rather than the row: ``evaluate`` has already dropped
    every row whose ``method_config_id`` is not the configured one, so the two
    agree by construction — and the config is where the dedup key's
    ``method_config_id`` comes from too (one lookup, one source). The default
    comes from the method's own ``ParamSpec``, mirroring ``readout.py``'s MDE
    path, never a hardcoded "relative".
    """
    if comparison is None:
        return True
    params = comparison.method.params
    if "test_type" in params:
        return str(params["test_type"]) == "relative"
    from abkit.stats import UnknownMethodError, get_method_class

    try:
        method_cls = get_method_class(comparison.method.name)
    except UnknownMethodError:  # unreachable through a validated config
        return True
    default = next(
        (spec.default for spec in method_cls.param_specs if spec.name == "test_type"),
        "relative",
    )
    return str(default) == "relative"


def _pair_sizes(verdict: PairVerdict, rows: Sequence[dict]) -> tuple[int | None, int | None]:
    """Per-arm sample sizes off the verdict's OWN look, or ``(None, None)``.

    The look, not the latest row: another metric — or another arm pair — can be
    ahead, and a message pairing this pair's effect with that pair's n would be
    a number nothing computed. ``PairVerdict.end_ts`` is ``None`` only when the
    pair had no rows at all.
    """
    anchor = to_naive_utc(verdict.end_ts)
    if anchor is None:
        return None, None
    for row in rows:
        if (
            str(row["metric"]) == verdict.metric
            and str(row["name_1"]) == verdict.name_1
            and str(row["name_2"]) == verdict.name_2
            and to_naive_utc(row["end_ts"]) == anchor
        ):
            size_1, size_2 = row.get("size_1"), row.get("size_2")
            return (
                int(size_1) if size_1 is not None else None,
                int(size_2) if size_2 is not None else None,
            )
    return None, None


def readout_data_from_verdict(
    experiment: ExperimentConfig,
    verdict: PairVerdict,
    readout: ExperimentReadout,
    *,
    project_name: str | None = None,
    rows: Sequence[dict] = (),
    dashboard_url: str | None = None,
    verdict_changed: bool = False,
) -> ReadoutData:
    """One channel-facing payload, copied field-for-field off the verdict.

    ``srm_flag``/``srm_pvalue`` come from the sibling :class:`ExperimentReadout`
    (the gate is a whole-experiment property, not a per-pair one) and are never
    re-derived. ``timestamp`` is the look the numbers are AS OF — the cutoff's
    ``end_ts``, not "now": a message that arrives an hour after the run must
    still say which cutoff it describes.
    """
    comparison = next((c for c in experiment.comparisons if c.metric == verdict.metric), None)
    n_1, n_2 = _pair_sizes(verdict, rows)
    mentions = list(experiment.notify.mentions) if experiment.notify is not None else []
    return ReadoutData(
        experiment=experiment.name,
        metric=verdict.metric,
        verdict=verdict.verdict,
        name_1=verdict.name_1,
        name_2=verdict.name_2,
        effect=verdict.effect,
        left_bound=verdict.left_bound,
        right_bound=verdict.right_bound,
        pvalue=verdict.pvalue,
        alpha=verdict.alpha,
        relative=_is_relative(comparison),
        srm_flag=readout.srm_flag,
        srm_pvalue=readout.srm_pvalue,
        weekly_cycle_pct=verdict.weekly_cycle_pct,
        n_1=n_1,
        n_2=n_2,
        timestamp=to_naive_utc(verdict.end_ts),
        timezone=experiment.timezone,
        elapsed_days=verdict.elapsed_days,
        project_name=project_name,
        description=experiment.description,
        mentions=mentions,
        dashboard_url=dashboard_url,
        help_url=READOUT_GUIDE_URL,
        verdict_changed=verdict_changed,
    )


def signal_kinds_for(payload: ReadoutData) -> tuple[str, ...]:
    """Every kind this ONE payload legitimately answers to (m12 NTF-2).

    A readout whose SRM gate failed is both the routine `readout` and the
    urgent `srm` — the same message, re-CLASSIFIED, never re-evaluated. So an
    on-call channel scoped to `on: [srm, error]` hears about a broken split
    while a routine channel keeps getting its readouts, and a channel that
    accepts both still gets exactly ONE message (delivery is decided by "does
    ANY of these kinds pass", not per kind).

    `verdict_change` is the same re-classification one step narrower (m12
    NTF-6): a readout whose verdict WORD differs from the one last announced
    for this comparison. It is deliberately not a synonym for "was delivered" —
    NTF-3 also delivers a first-ever readout (news, but nothing changed) and a
    readout delivered because its SRM gate moved (the word can be identical) —
    so `on: [verdict_change]` is the filter for a channel that wants decision
    FLIPS and nothing else.
    """
    if payload.kind != "readout":
        return (payload.kind,)
    kinds = ["readout"]
    if payload.verdict_changed:
        kinds.append("verdict_change")
    if payload.srm_flag:
        kinds.append("srm")
    return tuple(kinds)


def _deliver(
    *,
    experiment: ExperimentConfig,
    payloads: Sequence[ReadoutData],
    channels: Sequence[tuple[str, Any]],
    echo: Echo,
) -> list[int]:
    """Push every payload through every channel that accepts its kind.

    The one place a channel is constructed and a send is attempted, shared by
    the verdict and notice paths so the fail-soft discipline cannot drift
    between them.

    Returns the successful-send count PER PAYLOAD, parallel to *payloads* —
    the caller sums it for the terminal line, and NTF-3's dedup needs the
    per-payload half: an announcement may only be recorded once somebody has
    actually received it.
    """
    from abkit.notify.factory import ChannelFactory

    experiment_on = experiment.notify.on if experiment.notify is not None else None
    sent = [0] * len(payloads)
    for name, cfg in channels:
        channel_on = getattr(cfg, "on", None)
        accepted = [
            index
            for index, payload in enumerate(payloads)
            if any(
                passes_filter(kind, channel_on, experiment_on) for kind in signal_kinds_for(payload)
            )
        ]
        if not accepted:
            continue
        try:
            channel = ChannelFactory.create_from_config(cfg.model_dump())
        except Exception as exc:
            echo(f"{experiment.name}: notify channel '{name}' skipped — {exc}")
            continue
        for index in accepted:
            payload = payloads[index]
            try:
                delivered = (
                    channel.send_notice(payload)
                    if channel.is_notice(payload)
                    else channel.send(payload)
                )
                if delivered:
                    sent[index] += 1
                else:
                    echo(f"{experiment.name}: notify channel '{name}' reported a failed send")
            except Exception as exc:  # a channel that raises despite the bool contract
                # `describe_error`, never the raw exception: `requests` embeds
                # the full URL in its exception strings, and a webhook/bot URL
                # carries the credential in its PATH — this line goes to stdout
                # and into CI logs (the discipline `webhook.py`/`telegram.py`
                # already follow for their own handled failures).
                echo(f"{experiment.name}: notify channel '{name}' failed — {describe_error(exc)}")
    return sent


def dispatch_experiment_signals(
    *,
    experiment: ExperimentConfig,
    readout: ExperimentReadout,
    rows: Sequence[dict] = (),
    channels_cfg: dict[str, Any],
    project_name: str | None = None,
    states: InternalTablesManager | None,
    echo: Echo,
) -> int:
    """Send one message per CHANGED verdict through every channel that accepts it.

    Returns how many sends reported success — the caller prints it. Nothing
    raises: a channel that cannot even be CONSTRUCTED (a rotated secret, an
    unknown type) is one yellow line, and the remaining channels still go.

    ``states`` is the ``_ab_notify_states`` store (m12 NTF-3): a comparison
    whose announcement signature is unchanged since last time is skipped, so a
    run every hour is not a message every hour. It is a REQUIRED argument with
    an explicit ``None`` for "no dedup" — a default would let a caller disable
    the quiet by forgetting it.
    """
    channels, warnings = resolve_channels(experiment, channels_cfg)
    for warning in warnings:
        echo(warning)
    if not channels:
        return 0

    verdicts = list(readout.verdicts)
    # NTF-6: which of them are a verdict FLIP, decided here because this is
    # where the previously announced verdict is read. Without the dedup store
    # there is no "previously announced" to compare against, so nothing claims
    # the kind rather than guessing it from the current word.
    changed: set[int] = set()
    if states is not None:
        keep: list[PairVerdict] = []
        for verdict in verdicts:
            state = states.get_notify_state(
                experiment.name,
                verdict.metric,
                verdict.name_1,
                verdict.name_2,
                _method_config_id(experiment, verdict.metric),
            )
            if should_announce(state, verdict.verdict, readout.srm_flag):
                previous = state.get("last_verdict") if state.get("notify_count") else None
                if previous is not None and previous != verdict.verdict:
                    changed.add(len(keep))
                keep.append(verdict)
            else:
                echo(
                    f"{experiment.name}: {verdict.metric} {verdict.name_1} vs "
                    f"{verdict.name_2} unchanged ({verdict.verdict}) — not re-sent"
                )
        verdicts = keep

    payloads = [
        readout_data_from_verdict(
            experiment,
            verdict,
            readout,
            project_name=project_name,
            rows=rows,
            verdict_changed=index in changed,
        )
        for index, verdict in enumerate(verdicts)
    ]
    if not payloads:
        return 0

    per_payload = _deliver(experiment=experiment, payloads=payloads, channels=channels, echo=echo)

    if states is not None:
        for verdict, delivered in zip(verdicts, per_payload, strict=True):
            # Only a message somebody RECEIVED becomes history. Recording an
            # announcement that reached no channel (all down, all filtered out)
            # would make the next run treat the flip as old news and lose it —
            # permanently, since nothing re-derives what was never sent.
            if delivered:
                states.record_notification(
                    experiment.name,
                    verdict.metric,
                    verdict.name_1,
                    verdict.name_2,
                    _method_config_id(experiment, verdict.metric),
                    verdict=verdict.verdict,
                    srm_flag=readout.srm_flag,
                )
    return sum(per_payload)


def pipeline_error_notice(
    experiment: ExperimentConfig,
    error: str,
    *,
    project_name: str | None = None,
    timestamp: datetime | None = None,
) -> ReadoutData:
    """The payload for a run that FAILED (m12 NTF-2).

    Every statistical field stays ``None`` — there is no verdict, no effect, no
    arm pair, because the pipeline never got far enough to produce one. The
    channels render this shape as a notice, not as a readout with blanks.
    ``timestamp`` is wall-clock here (unlike a readout's, which is the look's
    own cutoff): the news IS that the run failed just now.
    """
    return ReadoutData(
        experiment=experiment.name,
        metric="",
        verdict="",
        name_1="",
        name_2="",
        kind="error",
        notice=error,
        timestamp=timestamp if timestamp is not None else now_utc_naive(),
        timezone=experiment.timezone,
        project_name=project_name,
        description=experiment.description,
        mentions=list(experiment.notify.mentions) if experiment.notify is not None else [],
    )


def dispatch_pipeline_error(
    *,
    experiment: ExperimentConfig,
    error: str,
    channels_cfg: dict[str, Any],
    project_name: str | None = None,
    echo: Echo,
) -> int:
    """Tell the channels a run failed — the one signal with no readout behind it.

    Deliberately NOT gated on persisted rows the way the readout path is: the
    absence of a result is exactly what this reports.
    """
    channels, warnings = resolve_channels(experiment, channels_cfg)
    for warning in warnings:
        echo(warning)
    if not channels:
        return 0
    payload = pipeline_error_notice(experiment, error, project_name=project_name)
    # No dedup: an error is not a verdict, and a run that fails twice failed
    # twice. NTF-3's state store is deliberately not consulted here.
    return sum(_deliver(experiment=experiment, payloads=[payload], channels=channels, echo=echo))


def _recurring_notice(
    experiment: ExperimentConfig,
    kind: str,
    sentence: str,
    *,
    project_name: str | None = None,
) -> ReadoutData:
    """The payload shape both recurring signals share (m12 NTF-5).

    Statistics stay ``None`` for the reason NTF-2's error notice leaves them
    empty: neither a backlog nor a red calibration cell is a measurement OF the
    experiment, and a channel rendering "Effect: N/A" beside it would imply
    somebody looked. ``timestamp`` is wall-clock — the news is the condition
    observed just now, not any particular look.
    """
    return ReadoutData(
        experiment=experiment.name,
        metric="",
        verdict="",
        name_1="",
        name_2="",
        kind=kind,
        notice=sentence,
        timestamp=now_utc_naive(),
        timezone=experiment.timezone,
        project_name=project_name,
        description=experiment.description,
        mentions=list(experiment.notify.mentions) if experiment.notify is not None else [],
    )


def _dispatch_recurring(
    *,
    experiment: ExperimentConfig,
    kind: str,
    items: Sequence[str],
    sentence: str,
    channels_cfg: dict[str, Any],
    project_name: str | None = None,
    states: InternalTablesManager | None,
    echo: Echo,
) -> int:
    """Announce a recurring condition — once per distinct condition (m12 NTF-5).

    *items* is what the condition IS (the backlogged metrics, the red cells);
    *sentence* is how it reads. Two arguments, because the dedup signature is
    built from the first while the second carries numbers that drift on every
    run — a lag that grows, an FPR that moves with the resample.

    An EMPTY *items* is the condition going away, and it is handled here rather
    than by the caller: nothing is sent (a "backlog cleared" message is not in
    this milestone), but the stored signature is RESET, so the same condition
    recurring next month announces again instead of deduping against a stale
    row. That write is not the NTF-3 hazard it resembles — recording an
    OBSERVATION nobody had to receive loses nothing, whereas recording an
    unreceived ANNOUNCEMENT would lose the flip permanently.
    """
    signature = recurring_signature(items)
    state_key = notice_state_key(kind)
    state = None if states is None else states.get_notify_state(experiment.name, *state_key)

    if not signature:
        if states is not None and state is not None and state.get("last_verdict"):
            states.record_notification(experiment.name, *state_key, verdict="", srm_flag=False)
        return 0

    if state is not None:
        cooldown = experiment.notify.cooldown_seconds if experiment.notify is not None else None
        if not should_announce_recurring(state, signature, cooldown, now_utc_naive()):
            echo(f"{experiment.name}: {kind} unchanged since the last message — not re-sent")
            return 0

    channels, warnings = resolve_channels(experiment, channels_cfg)
    for warning in warnings:
        echo(warning)
    if not channels:
        return 0

    payload = _recurring_notice(experiment, kind, sentence, project_name=project_name)
    sent = sum(_deliver(experiment=experiment, payloads=[payload], channels=channels, echo=echo))
    if sent and states is not None:
        # Only a received message becomes history (the NTF-3 rule): a signature
        # recorded after every channel failed would mute the condition until it
        # changed shape.
        states.record_notification(experiment.name, *state_key, verdict=signature, srm_flag=False)
    return sent


def backlog_sentence(entries: Sequence[BacklogEntry]) -> str:
    """The `stale` message body — retrospective, because the condition is.

    The run that reports a backlog is the run that computes the missing looks
    (the PLAN stage detects it, the COMPUTE stage drains it), so the news is
    that the SCHEDULE slipped — a run that never fired, was locked out, or
    failed — not that the warehouse is behind at this moment. Claiming
    otherwise would alarm an operator about a gap that is already closed.
    """
    listed = ", ".join(f"{entry.metric} by {entry.lag_seconds / 3600.0:.1f}h" for entry in entries)
    plural = "series was" if len(entries) == 1 else "series were"
    return (
        f"{len(entries)} metric {plural} more than 3 cadence steps behind the watermark "
        f"when this run planned it: {listed}. This run computed the missing looks — "
        "what is behind is the SCHEDULE (a run that never fired, was locked out, or "
        "failed), not the warehouse."
    )


def dispatch_stale(
    *,
    experiment: ExperimentConfig,
    entries: Sequence[BacklogEntry],
    channels_cfg: dict[str, Any],
    project_name: str | None = None,
    states: InternalTablesManager | None,
    echo: Echo,
) -> int:
    """Route ``abk run``'s existing backlog detection as the `stale` signal.

    Zero new detection: ``driver.py``'s PLAN stage has decided what a backlog
    is since m2 (`backlog_seconds` > 3 tail-cadence steps) — this formats and
    delivers what it found.
    """
    return _dispatch_recurring(
        experiment=experiment,
        kind="stale",
        items=[entry.metric for entry in entries],
        sentence=backlog_sentence(entries),
        channels_cfg=channels_cfg,
        project_name=project_name,
        states=states,
        echo=echo,
    )


def red_cells(cells: Sequence[Any]) -> list[Any]:
    """The A/A cells whose measured FPR exceeded their budget — "do not use".

    The EXACT condition ``validate/runner.py``'s ``_verdict`` renders as text
    (``fpr > budget``), read off the same ``CellResult`` objects, so a message
    and the matrix printed beside it cannot disagree about which cells are red.
    """
    return [
        cell
        for cell in cells
        if cell.fpr is not None and cell.budget is not None and cell.fpr > cell.budget
    ]


def calibration_sentence(cells: Sequence[Any]) -> str:
    """The `calibration_red` message body: which cells are red, and by how much."""
    red = red_cells(cells)
    listed = "; ".join(
        f"{cell.method_name} on {cell.metric}: FPR {cell.fpr:.1%} (budget {cell.budget:.1%})"
        for cell in red
    )
    plural = "cell" if len(red) == 1 else "cells"
    return (
        f"{len(red)} of {len(cells)} A/A {plural} exceeded the false-positive budget: "
        f"{listed}. Those comparisons reject the null more often than their alpha "
        "allows — do not decide on them until the method or the metric changes."
    )


def dispatch_calibration_red(
    *,
    experiment: ExperimentConfig,
    cells: Sequence[Any],
    channels_cfg: dict[str, Any],
    project_name: str | None = None,
    states: InternalTablesManager | None,
    echo: Echo,
) -> int:
    """Route ``abk validate``'s over-budget cells as the `calibration_red` signal.

    Zero new detection: the cells arrive already scored, carrying the same
    ``fpr``/``budget`` pair the matrix prints and ``_ab_aa_runs`` persists.
    The dedup identity is ``metric·method_config_id`` rather than the method
    NAME — one metric can carry two cells of the same method with different
    params, and collapsing them would let a newly-red second cell dedup
    against the first.

    The signature describes what THIS validation measured, so alternating a
    ``--metric``-narrowed run with a full one legitimately re-announces: each
    message is true about the cells it scored, and the alternative — merging
    with a previous run's cells — would report redness nobody just measured.
    """
    return _dispatch_recurring(
        experiment=experiment,
        kind="calibration_red",
        items=[f"{cell.metric}·{cell.method_config_id}" for cell in red_cells(cells)],
        sentence=calibration_sentence(cells),
        channels_cfg=channels_cfg,
        project_name=project_name,
        states=states,
        echo=echo,
    )
