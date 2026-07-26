"""The M10 exit gate: sub-day anchors + the decoupled, memoizing cockpit
(m10-implementation-plan.md §3).

Four legs, in the order the gate states them:

1. **The regression gate.** Each of the ``WINDOW_CASES`` shapes below
   reproduces its grid, its cutoffs and every derived number
   (``window_seconds``, ``elapsed_days``, ``weekly_cycle_pct``,
   ``look_days``/``horizon_days``, the CUPED pre-period) **byte-identically**
   against a golden captured from the pre-m10 code itself — plus the two
   numbers that legitimately moved, each pinned in both directions. Not a claim
   of exhaustiveness over everything the planner supports: ``limit``/max_looks
   and the per-branch enumeration edges stay pinned by
   ``tests/core/test_period_planner.py``, whose assertions are themselves
   byte-unchanged since ``f85371d``.
2. **A timestamped start.** A new fixture starting at ``09:00`` local anchors
   at that instant with no midnight snap, drives cutoffs INSIDE the opening
   local day (the shape that hid two silent-wrong-number defects from WP1's
   own tests — §6), and is accepted by config-lint, the driver, the STATE
   stage + the additive read path, ``abk plan`` and ``abk explore``.
3. **The schema break**, as far as an in-memory backend can prove it:
   ``_ab_results`` is created without ``start_date``/``end_date``,
   ``_ab_experiments`` with ``start_ts``/``horizon_ts``/``interval_anchor``, a
   pre-m10 catalog table fails with the drop-and-recreate remedy rather than a
   bare type error, and no shipped hint or BI recipe still names the dropped
   columns. The real-DDL half (``DateTime64(3)`` on a live server) is the
   Docker-gated ``test_first_run_clickhouse.py`` leg.
4. **The cockpit.** Over the real HTTP server: a slow ``/validate`` does not
   block a cheap alpha-only ``/recompute`` (WP4), and five alphas over one
   bootstrap knob state draw the replicates exactly ONCE (WP5) while
   reproducing the unmemoized numbers.

**Regenerating the leg-1 golden** (only ever from a pre-m10 checkout — never
from HEAD, which would make the gate compare HEAD with itself): copy
``capture_window_surface()`` and ``WINDOW_CASES`` into a checkout of
``f85371d`` (the last commit before M10 WP1) and run it there; the function
sniffs the field names, so it runs unmodified in both vocabularies.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from click.testing import CliRunner
from test_first_run import EXPOSURE_TS, SeedMirrorWarehouse

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from abkit.config import ExperimentConfig

runner = CliRunner()

DAY_SECONDS = 86400
WEEKLY_CYCLE_DAYS = 7.0

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "window_golden_pre_m10.json"

#: (case, start, INCLUSIVE ``end_date`` in the pre-m10 vocabulary, cadence, tz)
#:
#: The shapes the pre-m10 suite pinned: whole days, a non-UTC zone, both DST
#: directions, sub-day steps, a cadence that does not divide the window, a
#: window under one weekly cycle, and dense-early schedules.
WINDOW_CASES = [
    ("scaffold_daily_utc", "2024-07-01", "2024-07-14", "1d", "UTC"),
    ("daily_moscow", "2024-07-01", "2024-07-14", "1d", "Europe/Moscow"),
    ("daily_dst_fall_back", "2024-10-25", "2024-11-05", "1d", "America/New_York"),
    ("daily_dst_spring_forward", "2024-03-08", "2024-03-15", "1d", "America/New_York"),
    ("sub_day_6h_utc", "2024-07-01", "2024-07-03", "6h", "UTC"),
    ("sub_day_90m_moscow", "2024-07-01", "2024-07-02", "90m", "Europe/Moscow"),
    ("weekly_utc", "2024-06-01", "2024-07-31", "7d", "UTC"),
    ("non_dividing_5d", "2024-07-01", "2024-07-14", "5d", "UTC"),
    ("short_window_under_a_week", "2024-07-01", "2024-07-04", "1d", "UTC"),
    (
        "dense_early_schedule_utc",
        "2024-07-01",
        "2024-07-14",
        [{"every": "1h", "until": "48h"}, {"every": "1d"}],
        "UTC",
    ),
    (
        "dense_early_schedule_moscow",
        "2024-07-01",
        "2024-07-14",
        [{"every": "6h", "until": "2d"}, {"every": "1d"}],
        "Europe/Moscow",
    ),
    # …plus the shapes review round 1 found missing, each chosen because it can
    # move a number the first eleven cannot:
    ("single_day_window", "2024-07-01", "2024-07-01", "1d", "UTC"),
    # a NON-day-multiple `until`, which takes the day-lattice's elapsed-seconds
    # branch rather than its day-space one
    (
        "non_day_multiple_until",
        "2024-07-01",
        "2024-07-14",
        [{"every": "6h", "until": "36h"}, {"every": "1d"}],
        "UTC",
    ),
    ("half_hour_dst_lord_howe", "2024-09-29", "2024-10-09", "1d", "Australia/Lord_Howe"),
    ("two_hour_dst_troll", "2024-03-27", "2024-04-08", "1d", "Antarctica/Troll"),
    # a whole local calendar day that never existed (Apia crossed the date line)
    ("apia_line_jump_2011", "2011-12-27", "2012-01-03", "1d", "Pacific/Apia"),
    ("apia_start_on_the_skipped_day", "2011-12-30", "2012-01-05", "1d", "Pacific/Apia"),
    # an offset change with NO DST on either side (a permanent zone shift)
    ("moscow_permanent_shift_2014", "2014-10-20", "2014-11-02", "1d", "Europe/Moscow"),
    # a zone whose offset is not a whole hour, with no change inside the window
    ("kathmandu_45min_offset", "2024-07-01", "2024-07-08", "1d", "Asia/Kathmandu"),
    # a WHOLE-DAY `until` across a DST fall-back: the day-lattice's day-space
    # segment-bound comparison exists solely for this shape, and no other case
    # here reaches it (a `raise` probe inside that branch left the whole module
    # green — round 1's finding 13)
    (
        "whole_day_until_across_dst",
        "2024-10-30",
        "2024-11-08",
        [{"every": "1d", "until": "5d"}, {"every": "2d"}],
        "America/New_York",
    ),
    # three segments, so a MIDDLE segment has both bounds
    (
        "three_segment_schedule",
        "2024-07-01",
        "2024-07-20",
        [{"every": "1h", "until": "12h"}, {"every": "6h", "until": "3d"}, {"every": "1d"}],
        "UTC",
    ),
    # sub-day segments spanning a transition (absolute-duration stepping, where
    # the day lattice would have snapped)
    (
        "sub_day_across_dst",
        "2024-11-02",
        "2024-11-05",
        [{"every": "6h", "until": "2d"}, {"every": "12h"}],
        "America/New_York",
    ),
]

#: the CUPED lookbacks whose whole-day pre-period window is captured
LOOKBACKS = ["7d", "14d"]

#: The ONE case whose GRID moved, and the only one: a ``start_ts`` on a local
#: calendar day that never existed. Pre-m10 both the start and the first daily
#: lattice point snapped to the same instant, so the series opened with a
#: ZERO-LENGTH look; the m10 planner keeps cutoffs strictly after the start and
#: drops it. Disclosed rather than waived — an empty first window computed
#: nothing and could not be read.
SKIPPED_LOCAL_DAY_CASE = "apia_start_on_the_skipped_day"

_HAS_TS_FIELDS = "start_ts" in ExperimentConfig.model_fields


def _needs_data_lag(cadence) -> bool:
    """A sub-day step must declare an ingestion SLA (declarative-config §8)."""
    segments = cadence if isinstance(cadence, list) else [{"every": cadence}]
    return any(not str(s["every"]).endswith(("d", "w")) for s in segments)


def window_document(name, start, end_inclusive, cadence, tz) -> dict:
    """One experiment document in the vocabulary THIS checkout speaks."""
    document = {
        "name": name,
        "unit_key": "user_id",
        "timezone": tz,
        "cadence": cadence,
        "assignment": {
            "query": "SELECT user_id, variant, exposure_ts FROM assignments",
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
        },
        "comparisons": [
            {
                "metric": "m",
                "is_main_metric": True,
                "method": {"name": "t-test", "params": {"test_type": "relative"}},
            }
        ],
    }
    if _needs_data_lag(cadence):
        document["data_lag"] = 0
    if _HAS_TS_FIELDS:
        # m10 D6: a bare date is local midnight of THAT day for BOTH edges, so
        # the pre-m10 INCLUSIVE `end_date` ports to the EXCLUSIVE next midnight
        # — the same window, spelled in one vocabulary.
        document["start_ts"] = start
        document["horizon_ts"] = (date.fromisoformat(end_inclusive) + timedelta(days=1)).isoformat()
    else:
        document["start_date"] = start
        document["end_date"] = end_inclusive
    return document


def _grid_of(config):
    if _HAS_TS_FIELDS:
        return config.grid()
    from abkit.core.period_planner import generate_grid

    return generate_grid(
        config.start_date,
        config.end_date,
        config.cadence_segments(),
        tz=config.timezone,
    )


def _preperiod(config, grid, lookback) -> list[str]:
    from abkit.compute.recompute_backend import RecomputeBackend

    window = RecomputeBackend(None, config)._preperiod_window(lookback, grid)
    return [window.start_ts.isoformat(), window.end_ts.isoformat()]


def capture_window_surface() -> dict:
    """The whole window surface, per case — the leg-1 comparison payload.

    Runs unmodified at pre-m10 and at HEAD (see the module docstring).
    """
    captured = {}
    for name, start, end_inclusive, cadence, tz in WINDOW_CASES:
        config = ExperimentConfig.model_validate(
            window_document(name, start, end_inclusive, cadence, tz)
        )
        grid = _grid_of(config)
        looks = []
        for cutoff in grid.cutoffs:
            # pipeline/enrich.py
            window_seconds = int((cutoff.end_ts - grid.start_ts).total_seconds())
            elapsed_days = window_seconds / DAY_SECONDS
            looks.append(
                {
                    "end_ts": cutoff.end_ts.isoformat(),
                    "is_horizon": cutoff.is_horizon,
                    "window_seconds": window_seconds,
                    "elapsed_days": elapsed_days,
                    # pipeline/readout.py, below one weekly cycle only
                    "weekly_cycle_pct": (
                        elapsed_days / WEEKLY_CYCLE_DAYS
                        if elapsed_days < WEEKLY_CYCLE_DAYS
                        else None
                    ),
                    # cli/commands/plan.py
                    "look_days": (cutoff.end_ts - grid.start_ts).total_seconds() / 86400.0,
                }
            )
        captured[name] = {
            "start_ts": grid.start_ts.isoformat(),
            "horizon_ts": grid.horizon_ts.isoformat(),
            "look_count": len(grid.cutoffs),
            "horizon_seconds": config.horizon_seconds(),
            "horizon_days": (grid.horizon_ts - grid.start_ts).total_seconds() / 86400.0,
            "looks": looks,
            "cuped_preperiod": {lb: _preperiod(config, grid, lb) for lb in LOOKBACKS},
        }
    return captured


class TestWindowGoldenAgainstPreM10:
    """§3(1): the regression gate — not a new-behavior test."""

    def test_the_golden_covers_every_case_in_this_file(self):
        # a case silently dropped from the golden would pass every assertion
        # below by never being compared
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        assert set(golden) == {case[0] for case in WINDOW_CASES}
        assert len(golden) == len(WINDOW_CASES) == 22

    def test_every_window_reproduces_its_pre_m10_grid_and_derived_numbers(self):
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        captured = capture_window_surface()

        for name in sorted(golden):
            expected = dict(golden[name])
            actual = dict(captured[name])
            # the two documented divergences are asserted separately, below
            expected.pop("horizon_seconds")
            actual.pop("horizon_seconds")
            if name == SKIPPED_LOCAL_DAY_CASE:
                continue  # its grid moved too — pinned exactly below
            assert actual == expected, f"{name}: window surface moved"

    def test_horizon_seconds_moves_by_exactly_the_windows_utc_offset_change(self):
        """The general law, not an allowlist.

        ``horizon_seconds()`` was a nominal day count and is now the elapsed
        length between the two resolved instants, so it differs from the pre-m10
        value by exactly the UTC-offset change between the window's edges — and
        by nothing anywhere else. Stating it as "±1h across DST" was wrong three
        ways: the delta is ±30 min in Australia/Lord_Howe, ±2h in
        Antarctica/Troll and −24h across Pacific/Apia's line jump, and it fires
        with no DST at all (Moscow's 2014 permanent +4→+3 shift, ``dst() == 0``
        on both sides). A waiver list would have had to grow for each; this
        cannot.
        """
        # imported HERE, not at module scope: `capture_window_surface()` must
        # stay runnable at `f85371d`, where this m10 helper does not exist
        from abkit.core.period_planner import as_local_datetime

        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        captured = capture_window_surface()
        moved = []

        for name, start, end_inclusive, cadence, tz in WINDOW_CASES:
            config = ExperimentConfig.model_validate(
                window_document(name, start, end_inclusive, cadence, tz)
            )
            zone = ZoneInfo(tz)

            def utc_offset(local_edge, zone=zone):
                """The zone's offset at a LOCAL wall-clock edge.

                Taken at the local time, never at the resolved instant: on
                Pacific/Apia's skipped day the resolved instant lands on the
                far side of the jump, so reading the offset there loses the
                24h it is the whole point of.
                """
                return local_edge.replace(tzinfo=zone).utcoffset().total_seconds()

            expected_delta = utc_offset(as_local_datetime(config.start_ts)) - utc_offset(
                as_local_datetime(config.horizon_ts)
            )
            actual_delta = captured[name]["horizon_seconds"] - golden[name]["horizon_seconds"]
            assert actual_delta == expected_delta, name
            # …and the new value agrees with the grid it describes, which the
            # old one did not
            assert captured[name]["horizon_seconds"] == pytest.approx(
                golden[name]["horizon_days"] * DAY_SECONDS
            ), f"{name}: horizon_seconds must agree with the grid"
            if actual_delta:
                moved.append(name)

        # the law is only worth asserting if cases actually exercise it, in
        # every magnitude the wrong "±1h across DST" story missed
        assert len(moved) == 9, moved
        assert {
            "daily_dst_fall_back",  # +1h, DST
            "half_hour_dst_lord_howe",  # -30min, DST
            "two_hour_dst_troll",  # -2h, DST
            "apia_line_jump_2011",  # -24h, a date-line jump
            "moscow_permanent_shift_2014",  # +1h, NO DST on either side
        } <= set(moved)

    def test_the_only_grid_that_moved_is_a_start_on_a_skipped_local_day(self):
        """The second disclosed divergence, pinned exactly.

        Pacific/Apia's 2011-12-30 never existed locally, so pre-m10 the start
        and the first daily lattice point resolved to the SAME instant and the
        series opened with a zero-length look. It is gone, and nothing else
        about the case moved.
        """
        golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))[SKIPPED_LOCAL_DAY_CASE]
        actual = capture_window_surface()[SKIPPED_LOCAL_DAY_CASE]

        assert golden["start_ts"] == actual["start_ts"]  # the window is unchanged
        assert golden["horizon_ts"] == actual["horizon_ts"]
        assert actual["look_count"] == golden["look_count"] - 1

        dropped = golden["looks"][0]
        assert dropped["end_ts"] == golden["start_ts"], "the dropped look was zero-length"
        assert dropped["window_seconds"] == 0
        # every surviving look is byte-identical to its pre-m10 twin
        assert actual["looks"] == golden["looks"][1:]


# --------------------------------------------------------------------------
# Leg 2 — a timestamped start, end to end
# --------------------------------------------------------------------------

#: 09:00 local, so the seed's daily 12:00 events fall INSIDE the opening day
#: (a 14:30 start would leave every opening-day look empty) while the 08:00
#: exposure still precedes the start.
SUB_DAY_START = "2024-07-01 09:00:00"
SUB_DAY_HORIZON = "2024-07-04"
SUB_DAY_EXP = "example_signup_test"
STATE_METRIC = "example_arpu"

_INCREMENTAL_BLOCK = "\ncompute:\n  incremental_reads: true\n"


def _sub_day_project(
    tmp_path, monkeypatch, *, incremental=False, anchor="midnight", name="demo_subday"
):
    """The scaffolded project, re-windowed to a timestamped sub-day start."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", name]).exit_code == 0
    monkeypatch.chdir(tmp_path / name)

    path = Path("experiments") / f"{SUB_DAY_EXP}.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["start_ts"] = SUB_DAY_START
    document["horizon_ts"] = SUB_DAY_HORIZON
    document["cadence"] = "6h"
    document["data_lag"] = 0  # a sub-day cadence must declare its SLA
    document["interval_anchor"] = anchor
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    if incremental:
        project_yml = Path("abkit_project.yml")
        project_yml.write_text(project_yml.read_text() + _INCREMENTAL_BLOCK, encoding="utf-8")

    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse, path


def _loaded_experiment(path: Path) -> ExperimentConfig:
    return ExperimentConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class TestSubDayStart:
    """§3(2): the new sub-day fixture, accepted everywhere and never snapped."""

    def test_the_grid_anchors_at_the_instant_with_cutoffs_inside_the_opening_day(
        self, tmp_path, monkeypatch
    ):
        _, path = _sub_day_project(tmp_path, monkeypatch)
        experiment = _loaded_experiment(path)
        grid = experiment.grid()

        # anchored at the instant — NOT floored to midnight
        assert grid.start_ts == datetime(2024, 7, 1, 9, 0)
        assert grid.horizon_ts == datetime(2024, 7, 4, 0, 0)
        assert grid.cutoffs[-1].is_horizon

        # the shape §6 says hid two silent-wrong-number defects: looks that
        # close INSIDE the opening local day
        opening_day = [c for c in grid.cutoffs if c.end_ts < datetime(2024, 7, 2)]
        assert [c.end_ts for c in opening_day] == [
            datetime(2024, 7, 1, 12, 0),
            datetime(2024, 7, 1, 18, 0),
        ]
        # the first window is a genuine 3h partial day, not 24h
        assert int((grid.cutoffs[0].end_ts - grid.start_ts).total_seconds()) == 3 * 3600
        assert len(grid.cutoffs) == 11

    def test_an_off_phase_anchor_moves_the_lattice_without_moving_the_edges(
        self, tmp_path, monkeypatch
    ):
        _, path = _sub_day_project(tmp_path, monkeypatch, anchor="start")
        grid = _loaded_experiment(path).grid()
        assert grid.start_ts == datetime(2024, 7, 1, 9, 0)
        assert grid.horizon_ts == datetime(2024, 7, 4, 0, 0)
        # anchor=start ⇒ the lattice counts from 09:00, so every look lands at
        # :00 offsets of the START, and the opening-day look is still inside it
        assert grid.cutoffs[0].end_ts == datetime(2024, 7, 1, 15, 0)
        assert grid.cutoffs[1].end_ts == datetime(2024, 7, 1, 21, 0)

    def test_config_lint_accepts_it(self, tmp_path, monkeypatch):
        _sub_day_project(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["run", "--select", SUB_DAY_EXP, "--steps", "validate"])
        assert result.exit_code == 0, result.output

    def test_the_driver_computes_the_sub_day_series(self, tmp_path, monkeypatch):
        warehouse, path = _sub_day_project(tmp_path, monkeypatch)
        result = runner.invoke(cli, ["run", "--select", SUB_DAY_EXP])
        assert result.exit_code == 0, result.output

        grid = _loaded_experiment(path).grid()
        rows = warehouse._rows["_ab_results"]
        assert rows, "the sub-day series persisted nothing"
        # every look is a real persisted cutoff, at the sub-day instants
        persisted_cutoffs = {str(row["end_ts"]) for row in rows}
        assert persisted_cutoffs == {str(c.end_ts) for c in grid.cutoffs}

        # window_seconds is measured from the INSTANT: the opening look is 3h
        by_cutoff = {str(row["end_ts"]): row for row in rows}
        first = by_cutoff[str(datetime(2024, 7, 1, 12, 0))]
        assert int(first["window_seconds"]) == 3 * 3600
        assert float(first["elapsed_days"]) == pytest.approx(3 / 24)

        # the opening-day window starts at 09:00, so the 08:00 exposure-time
        # facts of the seed are outside it — a sub-day start does not silently
        # sum pre-start rows
        assert EXPOSURE_TS < grid.start_ts

    def test_the_catalog_row_stores_the_resolved_instant_and_the_anchor(
        self, tmp_path, monkeypatch
    ):
        warehouse, _ = _sub_day_project(tmp_path, monkeypatch)
        assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0
        catalog = warehouse._rows["_ab_experiments"]
        assert len(catalog) == 1
        row = catalog[0]
        assert str(row["start_ts"]) == "2024-07-01 09:00:00"
        assert str(row["horizon_ts"]) == "2024-07-04 00:00:00"
        assert row["interval_anchor"] == "midnight"

    def test_the_cuped_preperiod_stays_whole_day(self, tmp_path, monkeypatch):
        from abkit.compute.recompute_backend import RecomputeBackend

        _, path = _sub_day_project(tmp_path, monkeypatch)
        experiment = _loaded_experiment(path)
        grid = experiment.grid()
        window = RecomputeBackend(None, experiment)._preperiod_window("14d", grid)
        # whole-day aligned even though the start carries a time-of-day
        assert window.start_ts == datetime(2024, 6, 17, 0, 0)
        assert window.end_ts == datetime(2024, 7, 1, 0, 0)
        assert window.end_ts < grid.start_ts

    def test_plan_accepts_a_timestamped_start(self, tmp_path, monkeypatch):
        _sub_day_project(tmp_path, monkeypatch)
        # `abk plan` needs a persisted baseline, or every comparison prints
        # "SKIPPED: no baseline" and the sizing math the gate names
        # (look_days/horizon_days, achievable MDE, required N) never runs
        assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0
        result = runner.invoke(cli, ["plan", "--select", SUB_DAY_EXP])
        assert result.exit_code == 0, result.output
        assert "SKIPPED: no baseline" not in result.output, result.output
        assert "looks:" in result.output, result.output

    def test_validate_accepts_a_timestamped_start(self, tmp_path, monkeypatch):
        """The A/A matrix enumerates the SAME grid, a surface no other suite
        drives off a timestamped start.

        Exit 0 alone would be a green light over a half-failed matrix, so the
        per-cell outcomes are pinned. The fraction cell DOES fail here — and
        measurably NOT because of the timestamped start: it fails identically at
        `f85371d` with a midnight start and the same 6h cadence (`fraction
        input_kind requires an nobs (trials) array`), i.e. a pre-existing
        sub-day-cadence defect in the A/A panel, out of M10's scope. Pinned as
        the current truth so the day it changes, this notices.
        """
        warehouse, _ = _sub_day_project(tmp_path, monkeypatch)
        assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0
        result = runner.invoke(cli, ["validate", "--select", SUB_DAY_EXP, "--iterations", "50"])
        assert result.exit_code == 0, result.output

        statuses = {row["metric"]: row["status"] for row in warehouse._rows["_ab_aa_runs"]}
        assert statuses == {
            STATE_METRIC: "success",  # the sample metric scores normally
            "example_signup_cr": "failed",  # the pre-existing fraction defect
        }, statuses
        assert "well-calibrated" in result.output
        # the REASON too, not just the status: a different failure with the same
        # status would otherwise let this test pass while its docstring lied
        assert "fraction input_kind requires an nobs (trials) array" in result.output

    def test_day_state_materializes_every_closed_day_of_a_sub_day_series(
        self, tmp_path, monkeypatch
    ):
        """The 09:00 half: the clamp must not COST a day.

        The seed's opening-day facts (12:00) are inside `[09:00, Jul 2)`, so all
        three closed days carry state. This says the clamp does not over-clamp;
        the test below is the one that can fail if it does not clamp at all
        (with a 14:30 start the same facts fall before the window).
        """
        warehouse, _ = _sub_day_project(tmp_path, monkeypatch)
        assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0
        inside = {str(row["day"]) for row in warehouse._rows.get("_ab_unit_state", [])}
        assert inside == {"2024-07-01", "2024-07-02", "2024-07-03"}

    def test_a_start_after_the_days_facts_leaves_the_opening_day_unmaterialized(
        self, tmp_path, monkeypatch
    ):
        warehouse, path = _sub_day_project(tmp_path, monkeypatch)
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        document["start_ts"] = "2024-07-01 14:30:00"  # after the seed's 12:00 events
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0

        state = warehouse._rows.get("_ab_unit_state", [])
        days = {str(row["day"]) for row in state}
        assert days == {"2024-07-02", "2024-07-03"}, "the opening day summed pre-start facts"
        assert state, "day state vanished entirely — the leg proves nothing"

    def test_copy_mode_still_copies_the_whole_opening_day(self, tmp_path, monkeypatch):
        """m8 × m10: the persisted-cohort engine must not lose the units exposed
        before a sub-day start.

        The incremental copy anchors its first scan bucket at the opening LOCAL
        DAY, not at `grid.start_ts`. Anchoring on the instant — which was
        invisible until m10, because a start was always a bare date — dropped
        every unit exposed earlier that day: the scaffold's 08:00 cohort
        vanishes under a 09:00 start, 0 of 600 units persisted, while the SRM
        line still reads 600 off the LIVE source. A real warehouse's metric join
        then returns nothing and every look degrades to "insufficient", unwarned.
        """
        warehouse, path = _sub_day_project(tmp_path, monkeypatch)
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        document["assignment"]["cohort_copy"] = {"enabled": True}
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        # copy mode requires the bounds hook to be a LIVE render reference
        query = Path(document["assignment"]["query_file"])
        sql = query.read_text(encoding="utf-8")
        query.write_text(
            sql.rstrip().rstrip(";") + "\n  WHERE 1 = 1 {{ ab_added_filters }}\n",
            encoding="utf-8",
        )

        result = runner.invoke(cli, ["run", "--select", SUB_DAY_EXP])
        assert result.exit_code == 0, result.output
        # the ENGINE ran (direct mode never writes this table at all, and the
        # round-trip line names the origin the fix moved)
        assert "cohort copy from 2024-07-01 00:00:00" in result.output, result.output
        assert "+600 units" in result.output
        persisted = warehouse._rows.get("_ab_exposures", [])
        assert len(persisted) == 600, f"the copy dropped {600 - len(persisted)} unit(s)"
        assert len({row["unit_id"] for row in persisted}) == 600, "duplicated units"
        # the exposures it copied really do precede the start instant
        assert EXPOSURE_TS < datetime(2024, 7, 1, 9, 0)

    def test_the_additive_read_path_reproduces_recompute_under_a_timestamped_start(
        self, tmp_path, monkeypatch
    ):
        """§3(a) over the three M9 surfaces WP1 found broken by a timestamped
        start: the incremental reader compared a ``date`` against the config
        field (a ``TypeError`` on *every* cutoff), passed it as a day key, and
        the STATE stage seeded its day loop from it. Both backends must now
        agree over the whole sub-day series, with nothing falling back.
        """
        incremental, _ = _sub_day_project(tmp_path, monkeypatch, incremental=True)

        # Count real additive reads. Without this the leg is SELF-parity: if the
        # driver stopped honouring `compute.incremental_reads` the comparison
        # below would put recompute against recompute and stay green — the exact
        # trap WP5's own review found in the memo gate.
        import abkit.compute.incremental_backend as incremental_mod

        reads: list[tuple] = []
        original_load = incremental_mod.IncrementalBackend.load_cutoff

        def counted(self, *args, **kwargs):
            reads.append((args, tuple(sorted(kwargs))))
            return original_load(self, *args, **kwargs)

        monkeypatch.setattr(incremental_mod.IncrementalBackend, "load_cutoff", counted)

        result = runner.invoke(cli, ["run", "--select", SUB_DAY_EXP])
        assert result.exit_code == 0, result.output
        # one additive read per cutoff of the state-eligible metric
        assert len(reads) == 11, len(reads)
        # …and none of them degraded to the recompute fallback
        assert "falling back" not in result.output.lower()
        assert "fell back" not in result.output.lower()

        verify = runner.invoke(cli, ["verify-incremental", "--select", SUB_DAY_EXP])
        assert verify.exit_code == 0, verify.output
        assert "11 matched" in verify.output  # every cutoff of the sub-day series
        # a green report that verified nothing is the failure mode this guards
        assert "unverified" not in verify.output.lower()

        # …and the flag moves no number: recompute the same window with it off
        recompute, _ = _sub_day_project(
            tmp_path, monkeypatch, incremental=False, name="demo_recompute"
        )
        assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0

        additive, full_rescan = _numbers(incremental), _numbers(recompute)
        assert set(additive) == set(full_rescan)  # the same identities, both ways
        for identity, values in additive.items():
            other = full_rescan[identity]
            assert values["sizes"] == other["sizes"], identity  # discrete: exact
            for column, value in values["continuous"].items():
                expected = other["continuous"][column]
                if value is None or expected is None:
                    assert value == expected, (identity, column)
                    continue
                # partial-day sums associate differently than one full-window
                # scan, so rel-1e-9 is the honest tolerance (the M7 lesson)
                assert math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-12), (
                    identity,
                    column,
                )


def _numbers(warehouse) -> dict[tuple, dict]:
    """Persisted numbers per row identity: sizes exact, the rest continuous."""
    out: dict[tuple, dict] = {}
    for row in warehouse._rows["_ab_results"]:
        identity = (row["metric"], str(row["end_ts"]), row["name_1"], row["name_2"])
        assert identity not in out, f"duplicate persisted row {identity}"
        out[identity] = {
            "sizes": (int(row["size_1"]), int(row["size_2"])),
            "continuous": {
                column: (None if row[column] is None else float(row[column]))
                for column in ("effect", "pvalue", "left_bound", "right_bound")
            },
        }
    assert out, "no rows to compare"
    return out


# --------------------------------------------------------------------------
# Leg 3 — the schema break, as far as an in-memory backend can prove it
# --------------------------------------------------------------------------


def _stale_experiments_model():
    """A pre-m10 ``_ab_experiments``: ``start_date``/``end_date`` ``Date``, no
    ``interval_anchor`` — the shape a 0.4.0 install has on disk."""
    from abkit.core.models import ColumnDefinition, TableModel
    from abkit.database.tables import get_experiments_table_model

    current = get_experiments_table_model()
    renamed = {"start_ts", "horizon_ts", "interval_anchor"}
    columns = []
    for column in current.columns:
        if column.name in renamed:
            continue
        columns.append(column)
        if column.name == "is_actual":
            columns += [
                ColumnDefinition("start_date", "Date"),
                ColumnDefinition("end_date", "Date"),
            ]
    return TableModel(
        columns=columns,
        primary_key=current.primary_key,
        engine=current.engine,
        order_by=current.order_by,
        indexes=current.indexes,
        version_column=current.version_column,
    )


class TestSchemaBreak:
    """§3(3): the two breaks of the whole track, and the upgrade path they need.

    The live-DDL half (``DateTime64(3)`` as the server stores it) is the
    Docker-gated leg in ``test_first_run_clickhouse.py``; that a shipped hint or
    BI recipe never names the dropped columns is the standing text gate
    ``tests/docs/test_no_stale_window_keys.py``.
    """

    def test_results_is_created_without_the_dropped_date_columns(self, tmp_path, monkeypatch):
        warehouse, _ = _sub_day_project(tmp_path, monkeypatch)
        assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0
        live = warehouse.list_columns("abkit_internal._ab_results")
        assert "start_date" not in live
        assert "end_date" not in live
        assert {"start_ts", "end_ts", "window_seconds", "elapsed_days"} <= set(live)

    def test_the_catalog_is_created_with_the_renamed_window_and_the_anchor(
        self, tmp_path, monkeypatch
    ):
        warehouse, _ = _sub_day_project(tmp_path, monkeypatch)
        assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0
        live = warehouse.list_columns("abkit_internal._ab_experiments")
        assert {"start_ts", "horizon_ts", "interval_anchor"} <= set(live)
        assert "start_date" not in live
        assert "end_date" not in live

    def test_a_pre_m10_catalog_fails_with_the_recreate_remedy_on_the_terminal(
        self, tmp_path, monkeypatch
    ):
        """A type change is not auto-migratable, so the guard must NAME the
        remedy — and name it where an operator reads it. It used to raise
        outside the driver's handler, so Click printed a traceback and
        ``result.output`` carried nothing.
        """
        warehouse, _ = _sub_day_project(tmp_path, monkeypatch)
        warehouse.create_table("abkit_internal._ab_experiments", _stale_experiments_model())
        assert "start_date" in warehouse.list_columns("abkit_internal._ab_experiments")

        result = runner.invoke(cli, ["run", "--select", SUB_DAY_EXP])
        assert result.exit_code == 1
        assert "drop and recreate" in result.output.lower()
        assert "DROP TABLE abkit_internal._ab_experiments" in result.output
        assert "CHANGELOG" in result.output
        # a clean error line, not a stack trace escaping the command
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_the_worker_pool_path_names_the_remedy_too(self, tmp_path, monkeypatch):
        """`run_experiments` has a SECOND `ensure_tables()` — the pool-bootstrap
        DDL serializer — reached only with `--workers N>1` over 2+ experiments.
        Guarding just the per-experiment call left that path tracebacking, which
        is how review round 1 found it: no test drove it.
        """
        warehouse, path = _sub_day_project(tmp_path, monkeypatch)
        second = yaml.safe_load(path.read_text(encoding="utf-8"))
        second["name"] = "second_experiment"
        (path.parent / "second_experiment.yml").write_text(
            yaml.safe_dump(second, sort_keys=False), encoding="utf-8"
        )
        warehouse.create_table("abkit_internal._ab_experiments", _stale_experiments_model())

        result = runner.invoke(cli, ["run", "--workers", "2"])
        assert result.exit_code == 1
        assert "drop and recreate" in result.output.lower()
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_unlock_also_names_the_remedy_rather_than_tracebacking(self, tmp_path, monkeypatch):
        warehouse, _ = _sub_day_project(tmp_path, monkeypatch)
        warehouse.create_table("abkit_internal._ab_experiments", _stale_experiments_model())
        result = runner.invoke(cli, ["unlock", "--select", SUB_DAY_EXP])
        assert result.exit_code == 1
        assert "drop and recreate" in result.output.lower()


# --------------------------------------------------------------------------
# Leg 4 — the cockpit: the decoupled lock (WP4) and the bootstrap memo (WP5)
# --------------------------------------------------------------------------

#: a Tier-S knob on the scaffolded sample metric: bootstrap has no closed form,
#: so every look genuinely redraws unless the memo stops it. 200 replicates keep
#: the leg fast — the memo's behaviour is replicate-count-independent.
BOOTSTRAP_KNOB = {"name": "bootstrap", "params": {"test_type": "relative", "n_samples": 200}}
ALPHA_DRAG = (0.05, 0.04, 0.03, 0.02, 0.01)


def _knob_body(alpha: float, request_id: int, metric: str = STATE_METRIC) -> dict:
    return {"metric": metric, "method": BOOTSTRAP_KNOB, "alpha": alpha, "request_id": request_id}


def _resample_counter(monkeypatch) -> list:
    """Count real ``_resample`` entries on the live server's handler threads."""
    from abkit.stats.bootstrap.bootstrap import BootstrapTest

    calls: list = []
    original = BootstrapTest._resample

    def counted(self, sample_1, sample_2):
        calls.append((sample_1.array.size, sample_2.array.size))
        return original(self, sample_1, sample_2)

    monkeypatch.setattr(BootstrapTest, "_resample", counted)
    return calls


@pytest.fixture
def served_sub_day(tmp_path, monkeypatch):
    """The real explore server over the timestamped-start project."""
    from test_explore_session import Served

    warehouse, _ = _sub_day_project(tmp_path, monkeypatch)
    assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0
    served = Served(warehouse)
    try:
        yield served
    finally:
        served.stop()


class TestCockpitUnderLoad:
    """§3(4): both live explore defects M10 fixed, over real HTTP."""

    def test_a_knob_turn_answers_while_a_real_auto_validate_holds_the_lock(
        self, served_sub_day, monkeypatch
    ):
        """WP4's lock split. The proof is the ORDER, not a duration: the cheap
        reply lands while the heavy request is still inside its handler, which a
        queued request could not have done at all. The validate is the real
        reduced-N Auto run, merely held at its door.
        """
        import threading

        from test_explore_session import http

        from abkit.tuning import server as server_mod

        entered, release = threading.Event(), threading.Event()
        real_validate = server_mod._run_validate

        def frozen(*args, **kwargs):
            entered.set()
            assert release.wait(timeout=30), "the validate was never released"
            return real_validate(*args, **kwargs)

        monkeypatch.setattr(server_mod, "_run_validate", frozen)

        validate_replies: list = []
        validate_thread = threading.Thread(
            target=lambda: validate_replies.append(
                http(served_sub_day.endpoint("validate"), {"request_id": 1}, timeout=60)
            ),
            daemon=True,
        )
        validate_thread.start()
        try:
            assert entered.wait(timeout=30)
            assert served_sub_day.server.heavy_lock.locked()  # /validate owns it

            status, reply = http(served_sub_day.endpoint("recompute"), _knob_body(0.03, 2))
            assert status == 200, reply
            assert reply["pairs"][0]["points"], "a real answer, not a stub"
            # …and it answered BEFORE the heavy request even finished
            assert validate_replies == []
            assert served_sub_day.server.heavy_lock.locked()
        finally:
            release.set()
            validate_thread.join(timeout=60)

        # the heavy path still works, and really did the A/A work
        assert validate_replies and validate_replies[0][0] == 200, validate_replies
        assert validate_replies[0][1]["recommended"], "the Auto run scored nothing"

    def test_five_alphas_draw_the_replicates_once_per_cutoff(self, served_sub_day, monkeypatch):
        """WP5's engagement proof: an alpha-only drag redraws nothing."""
        from test_explore_session import http

        calls = _resample_counter(monkeypatch)
        session = served_sub_day.session

        first_status, first_reply = http(served_sub_day.endpoint("recompute"), _knob_body(0.05, 1))
        assert first_status == 200, first_reply
        drawn = len(calls)
        points = first_reply["pairs"][0]["points"]
        # Every look that computed resampled exactly ONCE. The count is derived
        # from the reply, not hard-coded, so a changed fixture cannot quietly
        # turn this into a weaker claim — and it is ALSO pinned exactly, so a
        # fixture whose series stopped exercising the memo fails loudly rather
        # than passing with `0 == 0`.
        computed = [p for p in points if p.get("tier") != "baseline"]
        assert drawn == len(computed) == 10, (drawn, len(computed))
        assert len(points) == 11  # …of which the empty opening 3h look is one
        assert session.memoized_count() == drawn
        assert first_reply.get("warnings") == [], "the memo refused an entry"

        for request_id, alpha in enumerate(ALPHA_DRAG[1:], start=2):
            status, reply = http(
                served_sub_day.endpoint("recompute"), _knob_body(alpha, request_id)
            )
            assert status == 200, reply
            assert len(calls) == drawn, f"alpha={alpha} redrew the replicates"
            # the alpha really moved: a percentile CI at 0.01 is wider than 0.05
            assert reply.get("warnings") == []
            assert len(reply["pairs"][0]["points"]) == len(points)
        assert session.memoized_count() == drawn
        # …and the whole drag stayed on the memoized path: 5 alphas, 10 draws
        assert len(calls) == 10

    def test_the_memoized_alpha_drag_reproduces_the_unmemoized_numbers(self, tmp_path, monkeypatch):
        """The parity oracle takes the OTHER path — the capability flag off, so
        the engine runs the verbatim ``compare_pair`` per alpha. A cache emptied
        by a zero budget would have compared the memo path with itself.
        """
        from test_explore_session import Served, http

        from abkit.stats.bootstrap.bootstrap import BootstrapTest

        warehouse, _ = _sub_day_project(tmp_path, monkeypatch)
        assert runner.invoke(cli, ["run", "--select", SUB_DAY_EXP]).exit_code == 0

        def drag(memo_enabled: bool) -> tuple[int, list]:
            calls = _resample_counter(monkeypatch)
            monkeypatch.setattr(BootstrapTest, "supports_resample_memo", memo_enabled)
            served = Served(warehouse)
            points = []
            try:
                for request_id, alpha in enumerate(ALPHA_DRAG, start=1):
                    status, reply = http(
                        served.endpoint("recompute"), _knob_body(alpha, request_id)
                    )
                    assert status == 200, reply
                    points.append(reply["pairs"][0]["points"])
            finally:
                served.stop()
            return len(calls), points

        memoized_calls, memoized = drag(True)
        unmemoized_calls, unmemoized = drag(False)

        # a floor first: `0 == 0 * 5` would satisfy the ratio below while
        # proving nothing (a degraded Tier-S cache resamples nothing at all)
        assert memoized_calls == 10, memoized_calls
        assert all(len(points) == 11 for points in memoized), "the series went empty"
        # the oracle really is the other path: five drags, five draws per look
        assert unmemoized_calls == memoized_calls * len(ALPHA_DRAG)
        # …and every number over the wire is identical, alpha by alpha
        assert memoized == unmemoized

    def test_explore_serves_the_timestamped_start_series(self, served_sub_day):
        """Gate item 2's last surface: explore accepts a sub-day start."""
        from test_explore_session import http

        status, reply = http(served_sub_day.endpoint("recompute"), _knob_body(0.05, 1))
        assert status == 200, reply
        points = reply["pairs"][0]["points"]
        assert len(points) == 11
        # ms-epoch ints on the wire; the opening look closes at 12:00, not 00:00
        opening = min(points, key=lambda p: p["end_ts"])
        opened = datetime.fromtimestamp(opening["end_ts"] / 1000, tz=timezone.utc).replace(
            tzinfo=None
        )
        assert opened == datetime(2024, 7, 1, 12, 0)
