"""m14 DEC-6 — the byte-compatibility capture, runnable at ``0.8.0`` AND at HEAD.

The milestone's №1 assertion (m14-implementation-plan.md §0.2 point 3, §4) is
that **M14 moves no persisted number, no alpha and no verdict `0.8.0` already
issues** — so a two-arm experiment must reproduce `0.8.0` on every surface, and
a multi-arm one must reproduce every CONTROL-ANCHORED verdict it already had.
Proving that needs a surface captured from the *released* code: comparing HEAD
with HEAD proves nothing (the M10 window-golden lesson, restated by STAT-6).

This module is that capture, written against only what ``v0.8.0`` already
shipped, so the same file runs unmodified in both checkouts:

* ``tests/_helpers/`` is byte-identical between ``v0.8.0`` and HEAD
  (``git diff v0.8.0 HEAD -- tests/_helpers/`` is empty), so ``synthetic_ab``
  and ``fake_db`` mean the same thing in both trees;
* the only entry points used — ``run_experiment``, ``build_report_payload``,
  ``build_experiment_row``, ``dispatch_experiment_signals``,
  ``run_validation``, ``evaluate`` — have identical signatures at both ends
  (``git diff v0.8.0 HEAD`` over their modules touches no ``def`` line of
  theirs);
* nothing here reads a field M14 added. The capture dumps whatever
  ``to_dict()`` / ``build_context()`` gives, and the *comparison* — which knows
  about ``role``, ``rollups`` and the rest — lives in the gate.

**One correction to the pre-session brief**: the scaffold is NOT
``abkit/cli/assets/project/`` (no such path), it is generated inline by
``abkit/cli/commands/init.py``, which DOES differ from ``v0.8.0`` — by one
comment line in the example experiment's ``variants:`` (DEC-1 documenting
``control:``). A YAML comment reaches no hash and no number, which the scaffold
leg of the gate then proves rather than assumes.

**Regenerating the golden** (only ever from a released checkout — never from
HEAD)::

    git worktree add /tmp/abk-080 v0.8.0
    cp tests/e2e/_m14_baseline.py /tmp/abk-080/tests/e2e/
    .venv/bin/python /tmp/abk-080/tests/e2e/_m14_baseline.py \\
        tests/e2e/fixtures/decisions_golden_0_8_0.json

The ``__main__`` block pins ``abkit`` to the checkout it lives in — it drops the
editable install's ``sys.meta_path`` finder, prepends the checkout, and then
**asserts** where ``abkit`` actually resolved, so a capture cannot silently be
taken from the working tree it is meant to be independent of.

**The payloads below are part of the golden's identity.** Editing one makes the
committed surface unreproducible, and the failure reads as a moved number rather
than an edited fixture — regenerate from the released checkout, or add a new
payload beside it.

Four surfaces, because "every surface" is the claim:

* ``two_arm`` — two arms, two main metrics and a guardrail: the persisted rows,
  the report payload, the dashboard row, the notification contexts and the
  explore payload. This is the leg that must match field for field.
* ``four_arm`` — the SAME comparisons over four arms with per-arm lifts, so
  ``0.8.0``'s three control-anchored verdicts are non-trivial (two winners of
  different sizes and one quiet arm) and HEAD must reproduce them exactly while
  adding the three treatment pairs.
* ``scaffold`` — ``abk init`` + ``abk run --report`` through the real CLI, which
  is the only way to capture the ``Report →`` line's verdict note.
* ``aa`` — ``run_validation`` at two arms (must match) and at four (DEC-5's
  ONE deliberate exception: the placebo is now the calibrated contrast, so
  power / achieved-MDE / coverage legitimately move — and the gate asserts the
  DIRECTION of the move rather than merely tolerating it).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from click.testing import CliRunner

#: The scaffolded window closes on 2024-07-15; any later instant makes every
#: cutoff due. Same value the M2 first-run gate and the M13 baseline use.
SCAFFOLD_NOW = datetime(2024, 8, 1)

#: The synthetic window: 14 daily cutoffs, all complete at ``NOW``.
START = datetime(2024, 7, 1)
NOW = datetime(2024, 7, 20)
DAYS = 14
UNITS_PER_ARM = 120

TWO_ARMS = ("control", "treatment")
#: Four arms, and the control is FIRST — this fixture is about the arm count,
#: not about DEC-1's re-orientation (which cannot be captured at ``0.8.0`` at
#: all: there is no ``control:`` field there, so a declared non-first control
#: would be an unknown key). The declared-control path is pinned by
#: ``tests/config/test_declared_control.py`` and the AST gate instead.
FOUR_ARMS = ("control", "b", "c", "d")

#: Per-arm multiplicative lift on every metric. ``b`` and ``c`` both beat the
#: control decisively and by DIFFERENT margins (so a leader exists and is not a
#: tie), ``d`` is a null arm (so "not every treatment wins" is exercised).
#:
#: **The leader is deliberately NOT the first declared treatment.** ``c`` has
#: the bigger lift while ``b`` is declared first, which is precisely the
#: configuration where the M11 dashboard presented an arbitrary arm as the
#: experiment's result — so the golden captures ``0.8.0`` reading ``b``'s cells
#: and the gate asserts HEAD reads ``c``'s. Reverse the two lifts and the one
#: four-arm surface M14 changes on purpose becomes invisible.
LIFTS = {"control": 1.0, "treatment": 1.25, "b": 1.22, "c": 1.30, "d": 1.0}

#: ``method_config_id`` is identity-bearing, so the methods here are pinned:
#: two MAIN metrics (the ``leaders_agree`` machinery needs two rollups) and a
#: GUARDRAIL, which is what the contract asks for.
COMPARISONS = [
    {
        "metric": "arpu",
        "is_main_metric": True,
        "method": {"name": "t-test", "params": {"test_type": "relative"}},
    },
    {
        "metric": "conversion",
        "is_main_metric": True,
        "method": {"name": "z-test", "params": {"test_type": "relative"}},
    },
    {"metric": "ctr", "is_guardrail": True, "method": {"name": "ratio-delta"}},
]


def experiment_payload(arms: tuple[str, ...], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": f"m14_{len(arms)}arm",
        "description": "M14 exit gate",
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-15",
        "unit_key": "user_id",
        "timezone": "UTC",
        "alpha": 0.05,
        "assignment": {
            "query": "SELECT user_id, variant, exposure_ts FROM assignments",
            "variants": list(arms),
            "expected_split": {arm: 1 / len(arms) for arm in arms},
        },
        "comparisons": [json.loads(json.dumps(c)) for c in COMPARISONS],
    }
    payload.update(overrides)
    return payload


#: Dropped before comparison: wall-clock stamps and the temp dir a capture ran
#: in. Nothing statistical, and every one of them differs between two runs of
#: the SAME code — keeping them would fail the gate for the one reason it must
#: not.
VOLATILE_COLUMNS = frozenset({"created_at", "updated_at", "path", "run_id"})

#: The identity columns rows are ordered by. DISCRETE on purpose: sorting by
#: whole-row content would let a last-ULP float difference REORDER the list, and
#: a comparison that tolerates rel-1e-9 on continuous columns (byte
#: reproducibility holds only under a fixed BLAS configuration — M7 D13) would
#: then compare mismatched pairs.
ROW_ORDER = (
    "experiment",
    "metric",
    "method_config_id",
    "mode",
    "name_1",
    "name_2",
    "end_ts",
    "alpha",
)


def canonical(rows) -> list[dict]:
    """Strip the volatile columns and put the rows in a stable, discrete order."""
    stripped = [{k: v for k, v in dict(row).items() if k not in VOLATILE_COLUMNS} for row in rows]
    return sorted(
        (json.loads(json.dumps(row, sort_keys=True, default=str)) for row in stripped),
        key=lambda row: tuple(str(row.get(column, "")) for column in ROW_ORDER),
    )


def jsonable(value: Any) -> Any:
    """Round-trip through JSON with ``str`` as the last resort, so a payload
    holding ``datetime``/tuples compares the way the golden stores it."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


# ─────────────────────────────────────────────────────── the synthetic fixture


def build_context(arms: tuple[str, ...]):
    """A seeded warehouse + its tables manager, with a per-arm lift.

    ``synthetic_ab.seed_all_events`` lifts the arm literally named
    ``"treatment"`` and nothing else, so a four-arm fixture needs its own
    seeding — written here rather than added to the helper, because this file
    has to keep running unchanged in the ``v0.8.0`` checkout.
    """
    from synthetic_ab import SyntheticWarehouse

    from abkit.database.internal_tables import InternalTablesManager

    warehouse = SyntheticWarehouse()
    for arm_index, arm in enumerate(arms):
        for index in range(UNITS_PER_ARM):
            unit = f"{arm_index}{index:04d}"
            warehouse.cohort.append((unit, arm, START + timedelta(hours=1)))
    _seed_events(warehouse)
    tables = InternalTablesManager(warehouse)
    tables.ensure_tables()
    return warehouse, tables


def _seed_events(warehouse) -> None:
    """Deterministic per-unit daily values, scaled by the arm's lift.

    Shaped like ``synthetic_ab.seed_all_events`` (same three tables, same
    per-unit ``nobs > 1`` on the fraction metric, same CUPED pre-period) so the
    metric SQL in ``synthetic_ab.METRICS`` reads it unchanged.
    """
    for unit, arm, _ in warehouse.cohort:
        index = int(unit)
        lift = LIFTS[arm]
        base = 1.0 + (index % 7) * 0.5
        for day in range(DAYS):
            ts = START + timedelta(days=day, hours=12)
            wiggle = ((index * 7 + day) % 5) * 0.3
            warehouse.events["user_revenue"].append(
                (unit, arm, ts, {"gross_usd": (base + wiggle) * lift})
            )
            trials = 2.0 + (index + day) % 3
            converted = float((index + day) % 2) * lift
            warehouse.events["user_conversions"].append(
                (unit, arm, ts, {"conversions": min(trials, converted), "trials": trials})
            )
            views = 5.0 + (index + day) % 4
            clicks = (1.0 + (index * 3 + day) % 4) * lift
            warehouse.events["user_engagement"].append(
                (unit, arm, ts, {"clicks": clicks, "views": views})
            )
        for day in range(1, 8):
            warehouse.events["user_revenue"].append(
                (
                    unit,
                    arm,
                    START - timedelta(days=day, hours=6),
                    {"gross_usd": base + (index % 3) * 0.2},
                )
            )


# ───────────────────────────────────────────────────────── the four surfaces


def _recording_channel_class():
    """A real ``BaseChannel`` subclass that records the CONTEXT it would render.

    ``build_context()`` is *what a message says* — every display string, before
    a channel escapes it for its own transport — so it is the notification
    surface this gate can compare across two checkouts without a live URL. Built
    inside a function because the base class must be imported from whichever
    tree is running.
    """
    from abkit.notify.base import BaseChannel

    class RecordingChannel(BaseChannel):
        sent: list[dict[str, Any]] = []

        def __init__(self, label: str = "rec"):
            self.label = label

        def send(self, readout, template=None) -> bool:  # noqa: ANN001 — cross-tree signature
            RecordingChannel.sent.append(self.build_context(readout))
            return True

    return RecordingChannel


def capture_pipeline_surface(arms: tuple[str, ...], **overrides: Any) -> dict[str, Any]:
    """One experiment through the real driver, then read by every surface."""
    from synthetic_ab import METRICS, PROJECT

    from abkit.config import ExperimentConfig
    from abkit.config.profile import NotificationChannelConfig
    from abkit.notify.dispatch import dispatch_experiment_signals
    from abkit.notify.factory import ChannelFactory
    from abkit.pipeline import run_experiment
    from abkit.pipeline.readout import evaluate
    from abkit.reporting import build_report_payload
    from abkit.tuning import build_explore_payload
    from abkit.tuning.overview import build_experiment_row

    warehouse, tables = build_context(arms)
    experiment = ExperimentConfig.model_validate(experiment_payload(arms, **overrides))
    outcome = run_experiment(experiment, METRICS, PROJECT, warehouse, tables, now_utc=NOW)
    assert outcome.status == "completed", outcome.error

    rows = tables.load_results(experiment.name)
    readout = evaluate(experiment, rows, project=PROJECT)
    report = build_report_payload(
        experiment,
        tables,
        project=PROJECT,
        metric_configs=METRICS,
        generated_at="2026-08-07 00:00:00",
    )
    row = build_experiment_row(
        project_root=Path("/proj"),
        experiment_path=Path("/proj/experiments/exp.yml"),
        experiment=experiment,
        project=PROJECT,
        tables=tables,
        window_preset="all",
        now=NOW,
    )

    channel_class = _recording_channel_class()
    complaints: list[str] = []
    original = dict(ChannelFactory.CHANNEL_TYPES)
    ChannelFactory.CHANNEL_TYPES["m14recording"] = channel_class
    try:
        dispatch_experiment_signals(
            experiment=experiment,
            readout=readout,
            rows=rows,
            channels_cfg={
                "rec": NotificationChannelConfig(type="m14recording", label="rec"),
            },
            project_name=PROJECT.name,
            states=None,
            echo=complaints.append,
        )
    finally:
        ChannelFactory.CHANNEL_TYPES.clear()
        ChannelFactory.CHANNEL_TYPES.update(original)
    # The dispatcher is fail-soft BY DESIGN (m12 §0.4): a channel it cannot
    # construct is one yellow line and zero messages. That is right in
    # production and fatal here — the first draft of this capture handed it a
    # plain dict, every send was skipped, and the "captured" notification
    # surface was an empty list nothing would have compared.
    assert not complaints, complaints
    notify = list(channel_class.sent)
    assert notify, "the recording channel received nothing"

    explore = build_explore_payload(*_session_and_engine(warehouse, tables, experiment), report)

    return {
        "_ab_results": canonical(rows),
        "_ab_experiments": canonical(warehouse._rows["_ab_experiments"]),
        "readout": jsonable(readout.to_dict()),
        "report": jsonable(report),
        "dashboard_row": jsonable(row),
        "notify": jsonable(notify),
        "explore": jsonable(explore["explore"]),
    }


def _session_and_engine(warehouse, tables, experiment):
    from synthetic_ab import METRICS, PROJECT

    from abkit.compute.recompute_backend import RecomputeBackend
    from abkit.tuning import RecomputeEngine, backend_cutoff_loader, load_session

    backend = RecomputeBackend(warehouse, experiment)
    loader = backend_cutoff_loader(
        backend, {name: cfg.get_query_text(None) for name, cfg in METRICS.items()}
    )
    session = load_session(experiment, METRICS, PROJECT, tables, loader=loader)
    return session, RecomputeEngine(session)


def capture_scaffold_surface() -> dict[str, Any]:
    """``abk init demo && abk run --report`` against the seed-mirror warehouse.

    The CLI is the only surface that can produce the ``Report →`` line, and that
    line's verdict note is what DEC-4 rewrote at 3+ arms — so a two-arm capture
    of it is the byte-compatibility claim for the CLI.
    """
    from test_first_run import SeedMirrorWarehouse

    import abkit.config.profile as profile_mod
    import abkit.pipeline.driver as driver_mod
    from abkit.cli.main import cli

    runner = CliRunner()
    warehouse = SeedMirrorWarehouse()
    with runner.isolated_filesystem():
        created = runner.invoke(cli, ["init", "demo"])
        assert created.exit_code == 0, created.output
        outer = os.getcwd()
        os.chdir("demo")
        try:
            with (
                mock.patch.object(
                    profile_mod.ProfileConfig, "create_manager", lambda self: warehouse
                ),
                mock.patch.object(driver_mod, "now_utc_naive", lambda: SCAFFOLD_NOW),
            ):
                result = runner.invoke(cli, ["run", "--select", "example_signup_test", "--report"])
                assert result.exit_code == 0, result.output
        finally:
            os.chdir(outer)
    report_lines = [line.strip() for line in result.output.splitlines() if "Report →" in line]
    return {
        "_ab_results": canonical(warehouse._rows["_ab_results"]),
        "_ab_experiments": canonical(warehouse._rows["_ab_experiments"]),
        "report_lines": report_lines,
    }


#: Small on purpose: this leg is about WHICH cohort the placebo is drawn from,
#: not about resolving an FPR to three digits (the A/A matrix gate owns that).
AA_ITERATIONS = 300
AA_INJECT = 0.15


def capture_aa_surface(arms: tuple[str, ...]) -> list[dict]:
    """``run_validation`` over one metric — the DEC-5 boundary.

    At two arms the placebo pool IS the calibrated contrast, so every column
    must reproduce ``0.8.0``. At four arms it is not, and DEC-5's ONE deliberate
    exception applies: the power columns move because they finally describe the
    design the engine runs.
    """
    from synthetic_ab import METRICS, PROJECT, SyntheticWarehouse

    from abkit.compute.recompute_backend import RecomputeBackend
    from abkit.config import ExperimentConfig
    from abkit.validate.persistence import aa_run_records
    from abkit.validate.runner import ValidateSettings, run_validation

    warehouse, _tables = build_context(arms)
    assert isinstance(warehouse, SyntheticWarehouse)
    # ONE comparison and `correction: none`, so both arm counts are scored at
    # the SAME α=0.05 and the power columns are directly comparable. Under the
    # default divisor the four-arm cell lands at α/6, where 300 iterations
    # resolve an FPR to ±2 hits and the verdict STRING becomes a coin flip —
    # noise in the one column DEC-5 did not move.
    payload = experiment_payload(arms, correction="none")
    payload["comparisons"] = [json.loads(json.dumps(COMPARISONS[0]))]
    experiment = ExperimentConfig.model_validate(payload)
    backend = RecomputeBackend(warehouse, experiment)
    result = run_validation(
        backend,
        experiment,
        PROJECT,
        METRICS,
        {name: cfg.get_query_text(None) for name, cfg in METRICS.items()},
        experiment.grid(),
        ValidateSettings(iterations=AA_ITERATIONS, inject_effect=AA_INJECT),
        now_iso="2026-08-07T00:00:00",
    )
    return canonical(aa_run_records(result))


def capture_all() -> dict:
    import abkit

    return {
        "abkit_version": abkit.__version__,
        "two_arm": capture_pipeline_surface(TWO_ARMS),
        "four_arm": capture_pipeline_surface(FOUR_ARMS),
        "scaffold": capture_scaffold_surface(),
        "aa": {
            "two_arm": capture_aa_surface(TWO_ARMS),
            "four_arm": capture_aa_surface(FOUR_ARMS),
        },
    }


def _pin_abkit_to_this_checkout() -> None:
    """Make ``import abkit`` resolve to the tree this file lives in.

    A PEP-660 editable install answers from a ``sys.meta_path`` finder, which
    precedes every ``sys.path`` entry — so prepending the checkout is not
    enough, and a capture would silently be taken from the working tree it was
    meant to be independent of.
    """
    root = Path(__file__).resolve().parents[2]
    for finder in list(sys.meta_path):
        # The finder is registered as a CLASS, so `type(finder).__module__` is
        # "builtins" — read the object's own module and name, case-folded.
        origin = f"{getattr(finder, '__module__', '')}{getattr(finder, '__name__', '')}"
        if "editable" in origin.lower():
            sys.meta_path.remove(finder)
    for path in (str(root), str(root / "tests" / "_helpers"), str(root / "tests" / "e2e")):
        sys.path.insert(0, path)
    import abkit

    assert (
        Path(abkit.__file__).resolve().is_relative_to(root)
    ), f"captured from the wrong tree: {abkit.__file__} is not under {root}"


if __name__ == "__main__":
    _pin_abkit_to_this_checkout()
    destination = Path(sys.argv[1]).resolve()
    surface = capture_all()
    destination.write_text(json.dumps(surface, indent=1, sort_keys=True) + "\n")
    print(
        f"captured abkit {surface['abkit_version']} → {destination}: "
        f"{len(surface['two_arm']['_ab_results'])} two-arm rows, "
        f"{len(surface['four_arm']['_ab_results'])} four-arm rows, "
        f"{len(surface['four_arm']['readout']['verdicts'])} four-arm verdicts, "
        f"{len(surface['aa']['four_arm'])} aa rows"
    )
