"""DASH-2 tests: the dashboard's one-row-per-experiment shaper.

``docs/specs/m11-implementation-plan.md`` DASH-2. The suite pins the three
things the milestone's posture rests on: ``readout.evaluate()`` is the ONLY
verdict source (no numeric recomputation anywhere in ``overview.py``), one
bad experiment degrades to a full-shape row with an ``error`` string instead
of sinking the list, and every bound the payload claims (≤160 spark buckets,
the display-only point cap, the window presets) actually holds.

House pattern: ``tests/reporting/test_builder.py``'s direct-seeded
``_ab_results`` through the real ``save_results`` contract, run over both
fake-manager flavours (the clickhouse-like leg keeps duplicate PK rows until
a FINAL read).
"""

from __future__ import annotations

import ast
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from abkit.config import ProjectConfig
from abkit.config.experiment_config import ExperimentConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._results import RESULT_COLUMNS
from abkit.pipeline.readout import MIN_STABLE_CUTOFFS, ExperimentReadout, evaluate
from abkit.tuning import overview
from abkit.tuning.overview import (
    ALL_WINDOW_PRESETS,
    MAX_STAT_POINTS,
    WINDOW_PRESETS,
    build_experiment_row,
    build_experiment_row_safe,
    build_overview_boot_entries,
    resolve_experiment_location,
)
from tests._helpers.fake_db import FakeDatabaseManager

#: the fixture experiment's pinned left edge (local midnight == UTC midnight)
START = datetime(2026, 1, 1)
#: "now" for every windowed assertion — one hour past the last seeded look
NOW = datetime(2026, 1, 15, 1, 0, 0)
PROJECT = ProjectConfig.model_validate({"name": "p", "default_profile": "dev"})
ROOT = Path("/proj")
EXP_PATH = ROOT / "experiments" / "dash_exp.yml"


@pytest.fixture(params=[False, True], ids=["sql-like", "clickhouse-like"])
def tables(request) -> InternalTablesManager:
    manager = InternalTablesManager(FakeDatabaseManager(clickhouse_like=request.param))
    manager.ensure_tables()
    return manager


def make_experiment(**overrides) -> ExperimentConfig:
    config = {
        "name": "dash_exp",
        "start_ts": "2026-01-01",
        "horizon_ts": "2026-01-15",  # EXCLUSIVE right edge (m10 D6) — day 14 covered
        "unit_key": "user_id",
        "tags": ["growth", "checkout"],
        "assignment": {
            "query": "SELECT 1",
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
        },
        "alpha": 0.05,
        "correction": "none",
        "comparisons": [
            {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
        ],
    }
    config.update(overrides)
    return ExperimentConfig.model_validate(config)


def make_row(
    experiment: ExperimentConfig,
    metric: str = "revenue",
    name_2: str = "treatment",
    **overrides,
) -> dict:
    """One full-contract ``_ab_results`` row (the reporting fixture shape)."""
    comparison = experiment.get_comparison(metric)
    day = overrides.pop("day", 14)
    end_ts = START + timedelta(days=day)
    row = {
        "experiment": experiment.name,
        "metric": metric,
        "is_main_metric": comparison.is_main_metric,
        "is_guardrail": comparison.is_guardrail,
        "method_name": comparison.method.name,
        "method_params": comparison.method.canonical_params_json,
        "method_config_id": comparison.method.method_config_id,
        "name_1": "control",
        "name_2": name_2,
        "start_ts": START,
        "end_ts": end_ts,
        "window_seconds": day * 86400,
        "elapsed_days": float(day),
        "value_1": 10.0,
        "value_2": 11.0,
        "std_1": 2.0,
        "std_2": 2.0,
        "cov_value_1": None,
        "cov_value_2": None,
        "cov_std_1": None,
        "cov_std_2": None,
        "corr_coef_1": None,
        "corr_coef_2": None,
        "size_1": 1000,
        "size_2": 1000,
        "alpha": 0.05,
        "pvalue": 0.001,
        "effect": 0.1,
        "left_bound": 0.05,
        "right_bound": 0.15,
        "ci_length": 0.10,
        "reject": True,
        "mde_1": 0.04,
        "mde_2": 0.04,
        "srm_flag": False,
        "srm_pvalue": 0.8,
        "decision_blocked": False,
        "insufficient_data": False,
        "ci_kind": "fixed",
        "is_horizon": day >= 14,
        "warnings": None,
        "diagnostics": None,
        "metric_query": "SELECT template",
        "metric_rendered_query": "SELECT rendered",
        "watermark_ts": end_ts,
    }
    row.update(overrides)
    return row


def save_rows(tables: InternalTablesManager, rows: list[dict]) -> None:
    batch = {col: np.array([row[col] for row in rows], dtype=object) for col in RESULT_COLUMNS}
    tables.save_results(batch)


def seed_series(
    tables: InternalTablesManager,
    experiment: ExperimentConfig,
    metric: str = "revenue",
    name_2: str = "treatment",
    days: int = 14,
    first_day: int = 1,
    **overrides,
) -> list[dict]:
    rows = [
        make_row(experiment, metric=metric, name_2=name_2, day=day, **overrides)
        for day in range(first_day, days + 1)
    ]
    save_rows(tables, rows)
    return rows


def row_for(tables, experiment, window_preset="all", now=NOW, project=PROJECT, **overrides):
    kwargs = {
        "project_root": ROOT,
        "experiment_path": EXP_PATH,
        "experiment": experiment,
        "project": project,
        "tables": tables,
        "window_preset": window_preset,
        "now": now,
    }
    kwargs.update(overrides)
    return build_experiment_row(**kwargs)


def row_safe_for(tables, experiment, window_preset="all", now=NOW, project=PROJECT, **overrides):
    kwargs = {
        "project_root": ROOT,
        "experiment_path": EXP_PATH,
        "experiment": experiment,
        "project": project,
        "tables": tables,
        "window_preset": window_preset,
        "now": now,
    }
    kwargs.update(overrides)
    return build_experiment_row_safe(**kwargs)


def ms(value: datetime) -> int:
    return int(np.datetime64(value, "ms").astype("int64"))


class TestGoldenRow:
    """The whole row, field by field, for a 14-look single-pair experiment."""

    def test_the_golden_row(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        headline = evaluate(
            experiment, tables.load_results(experiment.name), project=PROJECT
        ).verdicts[0]

        row = row_for(tables, experiment)

        assert row["rationale"] and all(isinstance(line, str) for line in row["rationale"])
        assert row == {
            "name": "dash_exp",
            "dir": "",
            "file": "experiments/dash_exp.yml",
            "tags": ["growth", "checkout"],
            "status": "running",
            "timezone": "UTC",
            "start_ts": ms(datetime(2026, 1, 1)),
            "horizon_ts": ms(datetime(2026, 1, 15)),
            "main_metric": "revenue",
            "locked": False,
            "verdict": "WIN",
            "srm_flag": False,
            "srm_pvalue": 0.8,
            "effect": 0.1,
            "ci": [0.05, 0.15],
            "pvalue": 0.001,
            "alpha": 0.05,
            "elapsed_days": 14.0,
            "is_horizon": True,
            "weekly_cycle_pct": None,
            "insufficient": False,
            "rationale": list(headline.rationale),
            "caveats": [],
            "guardrail_regressed": False,
            "last_end_ts": ms(datetime(2026, 1, 15)),
            "spark": [[ms(START + timedelta(days=d)), 0.1] for d in range(1, 15)],
            "verdicts": [
                {
                    "metric": "revenue",
                    "pair": {"c": "control", "t": "treatment"},
                    "verdict": "WIN",
                    "effect": 0.1,
                    "caveats": [],
                    "guardrail_regressed": False,
                }
            ],
            "warnings": [],
            "error": None,
        }

    def test_every_stat_cell_is_evaluates_own_verdict_never_a_re_derivation(self, tables):
        """The milestone's #1 invariant, asserted against an independent call."""
        experiment = make_experiment()
        rows = seed_series(tables, experiment)
        expected = evaluate(experiment, tables.load_results(experiment.name), project=PROJECT)
        headline = expected.verdicts[0]

        row = row_for(tables, experiment)

        assert row["verdict"] == headline.verdict
        assert row["effect"] == headline.effect
        assert row["ci"] == [headline.left_bound, headline.right_bound]
        assert row["pvalue"] == headline.pvalue
        assert row["alpha"] == headline.alpha
        assert row["elapsed_days"] == headline.elapsed_days
        assert row["is_horizon"] == headline.is_horizon
        assert row["weekly_cycle_pct"] == headline.weekly_cycle_pct
        assert row["last_end_ts"] == ms(headline.end_ts)
        assert len(rows) == 14

    def test_a_really_degraded_row_has_exactly_the_keys_a_filled_row_has(self, tables, monkeypatch):
        """Built through the failing path, not compared to its own constructor."""
        experiment = make_experiment()
        seed_series(tables, experiment)
        filled = row_for(tables, experiment)

        monkeypatch.setattr(
            overview, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        )
        degraded = row_safe_for(tables, experiment)

        assert set(degraded) == set(filled), "one client renders both"
        assert degraded["verdicts"] == []
        assert degraded["caveats"] == []
        assert degraded["tags"] == ["growth", "checkout"]
        assert degraded["locked"] is False
        assert degraded["guardrail_regressed"] is False

    def test_the_empty_row_defaults_are_the_documented_ones(self):
        """Values, not just keys — a client following the contract indexes them."""
        assert overview._empty_row("x") == {
            "name": "x",
            "dir": "",
            "file": "",
            "tags": [],
            "status": None,
            "timezone": None,
            "start_ts": None,
            "horizon_ts": None,
            "main_metric": None,
            "locked": False,
            "verdict": None,
            "srm_flag": False,
            "srm_pvalue": None,
            "effect": None,
            "ci": [None, None],
            "pvalue": None,
            "alpha": None,
            "elapsed_days": None,
            "is_horizon": False,
            "weekly_cycle_pct": None,
            "insufficient": False,
            "rationale": [],
            "caveats": [],
            "guardrail_regressed": False,
            "last_end_ts": None,
            "spark": [],
            "verdicts": [],
            "warnings": [],
            "error": None,
        }

    def test_the_payload_is_json_serializable_with_the_stdlib_encoder(self, tables):
        import json

        experiment = make_experiment()
        seed_series(tables, experiment)

        row = row_for(tables, experiment)

        json.dumps(row)  # no custom default= — no numpy scalar, no datetime, no NaN
        assert isinstance(row["start_ts"], int) and not isinstance(row["start_ts"], bool)
        assert isinstance(row["last_end_ts"], int)
        assert all(isinstance(point[0], int) for point in row["spark"])
        assert isinstance(row["locked"], bool)

    def test_one_load_results_call_serves_every_comparison(self, tables):
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "signups", "method": {"name": "t-test"}},
                {
                    "metric": "latency",
                    "is_guardrail": True,
                    "desired_direction": "decrease",
                    "method": {"name": "t-test"},
                },
            ]
        )
        for metric in ("revenue", "signups", "latency"):
            seed_series(tables, experiment, metric=metric)
        manager = tables._manager
        manager.queries.clear()

        row_for(tables, experiment)

        reads = [
            q for q, _ in manager.queries if "_ab_results" in q and q.strip().startswith("SEL")
        ]
        assert len(reads) == 1, "one unfiltered read, not one per comparison"
        assert "metric = " not in reads[0]

    def test_a_never_run_project_is_no_data_not_an_error(self):
        """No ``_ab_results`` table: config fields fill, stats stay empty."""
        manager = InternalTablesManager(FakeDatabaseManager())  # no ensure_tables()
        experiment = make_experiment()

        row = row_safe_for(manager, experiment)

        assert row["error"] is None
        assert row["name"] == "dash_exp"
        assert row["main_metric"] == "revenue"
        assert (row["verdict"], row["last_end_ts"], row["spark"]) == (None, None, [])

    def test_a_tz_aware_now_is_normalized_not_compared_raw(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        aware = NOW.replace(tzinfo=timezone.utc)

        assert row_for(tables, experiment, "24h", now=aware) == row_for(
            tables, experiment, "24h", now=NOW
        )

    def test_an_aware_now_in_another_zone_is_converted_not_relabelled(self, tables):
        """+03:00 noon is 09:00 UTC — re-labelling would shift every window 3h."""
        experiment = make_experiment()
        seed_series(tables, experiment)
        moscow = NOW.replace(tzinfo=timezone(timedelta(hours=3)))

        assert row_for(tables, experiment, "24h", now=moscow) == row_for(
            tables, experiment, "24h", now=NOW - timedelta(hours=3)
        )

    def test_an_omitted_now_falls_back_to_the_wall_clock(self, tables):
        """``all`` ignores the anchor, so the default branch is assertable."""
        experiment = make_experiment()
        seed_series(tables, experiment)

        assert row_for(tables, experiment, "all", now=None) == row_for(
            tables, experiment, "all", now=NOW
        )


class TestSpark:
    def test_five_hundred_looks_bucket_under_the_cap(self, tables):
        experiment = make_experiment(horizon_ts="2027-06-01")
        seed_series(tables, experiment, days=500)

        row = row_for(tables, experiment)

        spark = row["spark"]
        assert len(spark) <= 160
        # ceil(500/ceil(500/160)) = ceil(500/4) = 125 — a ceiling, not a target
        assert len(spark) == 125
        assert [point[0] for point in spark] == sorted(point[0] for point in spark)
        assert spark[-1][0] == ms(START + timedelta(days=500)), "last bucket keeps its last look"

    def test_a_bucket_carries_the_mean_of_its_finite_effects(self, tables):
        experiment = make_experiment(horizon_ts="2027-06-01")
        rows = [
            make_row(experiment, day=day, effect=float(day)) for day in range(1, 321)
        ]  # 320 looks -> step 2
        save_rows(tables, rows)

        spark = row_for(tables, experiment)["spark"]

        assert len(spark) == 160
        assert spark[0] == [ms(START + timedelta(days=2)), 1.5]
        assert spark[1] == [ms(START + timedelta(days=4)), 3.5]

    def test_a_non_finite_effect_never_reaches_the_payload(self, tables):
        experiment = make_experiment()
        rows = [
            make_row(experiment, day=1, effect=float("nan")),
            make_row(experiment, day=2, effect=float("inf")),
            make_row(experiment, day=3, effect=0.2),
        ]
        save_rows(tables, rows)

        spark = row_for(tables, experiment)["spark"]

        assert spark == [
            [ms(START + timedelta(days=1)), None],
            [ms(START + timedelta(days=2)), None],
            [ms(START + timedelta(days=3)), 0.2],
        ]
        assert all(value is None or math.isfinite(value) for _, value in spark)

    def test_an_all_non_finite_bucket_keeps_its_gap_visible(self, tables):
        experiment = make_experiment(horizon_ts="2027-06-01")
        rows = [make_row(experiment, day=day, effect=float("nan")) for day in range(1, 321)]
        save_rows(tables, rows)

        spark = row_for(tables, experiment)["spark"]

        assert len(spark) == 160
        assert all(value is None for _, value in spark), "a [t, None] point, never a dropped point"

    def test_orphaned_method_config_rows_never_interleave_into_the_curve(self, tables):
        """An edited identity param leaves old rows behind — one curve, not two."""
        experiment = make_experiment()
        seed_series(tables, experiment, days=3)
        stale = [
            make_row(experiment, day=day, method_config_id="dead" + "0" * 12, effect=9.9)
            for day in range(1, 4)
        ]
        save_rows(tables, stale)

        row = row_for(tables, experiment)

        assert len(row["spark"]) == 3
        assert all(value == 0.1 for _, value in row["spark"])

    def test_the_point_cap_is_display_only_and_never_moves_the_verdict(self, tables, monkeypatch):
        """The cap is set BELOW the stabilization floor on purpose.

        At exactly ``MIN_STABLE_CUTOFFS`` a truncated series still stabilizes,
        so the assertion would hold even if the cap reached ``evaluate()`` —
        a gate that cannot fail. One look below it, the verdict WOULD move.
        """
        cap = MIN_STABLE_CUTOFFS - 1
        experiment = make_experiment()
        seed_series(tables, experiment)
        full = row_for(tables, experiment)

        monkeypatch.setattr(overview, "MAX_STAT_POINTS", cap)
        capped = row_for(tables, experiment)

        assert len(capped["spark"]) == cap, "the sparkline truncates to the most recent looks"
        assert capped["spark"] == full["spark"][-cap:]
        for cell in ("verdict", "effect", "ci", "pvalue", "alpha", "elapsed_days", "last_end_ts"):
            assert capped[cell] == full[cell], f"{cell} moved with a DISPLAY cap"

    def test_the_default_cap_is_the_documented_twenty_thousand(self):
        assert MAX_STAT_POINTS == 20_000


class TestWindowPresets:
    @pytest.mark.parametrize(
        ("preset", "expected_looks"),
        [("24h", 1), ("7d", 7), ("30d", 14), ("90d", 14), ("all", 14)],
    )
    def test_each_preset_filters_end_ts_against_now(self, tables, preset, expected_looks):
        experiment = make_experiment()
        seed_series(tables, experiment)

        row = row_for(tables, experiment, preset)

        assert len(row["spark"]) == expected_looks

    def test_the_preset_vocabulary_is_exactly_five(self):
        assert ALL_WINDOW_PRESETS == {"24h", "7d", "30d", "90d", "all"}
        assert WINDOW_PRESETS == {"24h": 1, "7d": 7, "30d": 30, "90d": 90}

    def test_the_preset_never_moves_the_verdict(self, tables):
        """The blocker this WP's review found: these rows are CUMULATIVE looks.

        Truncating the left edge deletes stabilization history while every
        surviving row still measures from ``start_ts`` — a 14-look daily
        experiment would read INCONCLUSIVE at ``24h`` (one look, below
        ``MIN_STABLE_CUTOFFS``) purely because someone changed a display knob.
        """
        experiment = make_experiment()
        seed_series(tables, experiment)
        full = evaluate(experiment, tables.load_results(experiment.name), project=PROJECT)

        rows = {preset: row_for(tables, experiment, preset) for preset in ALL_WINDOW_PRESETS}

        assert full.verdicts[0].verdict == "WIN"
        for preset, row in rows.items():
            for cell in ("verdict", "effect", "ci", "pvalue", "alpha", "last_end_ts"):
                assert row[cell] == rows["all"][cell], f"{preset} moved {cell}"
            assert row["verdict"] == "WIN"

    def test_a_window_with_no_looks_keeps_the_verdict_and_empties_the_sparkline(self, tables):
        """ "Decided; nothing new in this window" — never a downgraded verdict."""
        experiment = make_experiment()
        seed_series(tables, experiment)

        row = row_for(tables, experiment, "24h", now=NOW + timedelta(days=90))

        assert row["error"] is None
        assert row["spark"] == [], "the client's 'no looks in this window' signal"
        assert row["verdict"] == "WIN"
        assert row["last_end_ts"] == ms(datetime(2026, 1, 15))

    def test_the_inclusive_left_edge_keeps_a_look_landing_exactly_on_it(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        # the day-14 look sits exactly on the 7d window's left edge
        boundary_now = START + timedelta(days=14) + timedelta(days=7)

        row = row_for(tables, experiment, "7d", now=boundary_now)

        assert len(row["spark"]) == 1
        assert row["spark"][0][0] == ms(START + timedelta(days=14))

    @pytest.mark.parametrize("builder", [build_experiment_row, build_experiment_row_safe])
    def test_an_unknown_preset_raises_out_of_both_entries(self, tables, builder):
        experiment = make_experiment()
        with pytest.raises(ValueError, match="Unknown window preset 'yesterday'"):
            builder(
                project_root=ROOT,
                experiment_path=EXP_PATH,
                experiment=experiment,
                project=PROJECT,
                tables=tables,
                window_preset="yesterday",
                now=NOW,
            )

    def test_the_preset_error_names_every_allowed_value(self, tables):
        experiment = make_experiment()
        with pytest.raises(ValueError) as excinfo:
            row_for(tables, experiment, "1y")
        for preset in ALL_WINDOW_PRESETS:
            assert preset in str(excinfo.value)


class TestComparisonsSubList:
    def test_one_entry_per_main_metric_times_treatment_arm(self, tables):
        experiment = make_experiment(
            assignment={
                "query": "SELECT 1",
                "variants": ["control", "treat_a", "treat_b"],
                "expected_split": {"control": 0.34, "treat_a": 0.33, "treat_b": 0.33},
            }
        )
        seed_series(tables, experiment, name_2="treat_a")
        seed_series(tables, experiment, name_2="treat_b", effect=0.2)

        row = row_for(tables, experiment)

        assert [entry["pair"] for entry in row["verdicts"]] == [
            {"c": "control", "t": "treat_a"},
            {"c": "control", "t": "treat_b"},
        ]
        assert [entry["effect"] for entry in row["verdicts"]] == [0.1, 0.2]

    def test_the_headline_is_verdicts_zero(self, tables):
        experiment = make_experiment(
            assignment={
                "query": "SELECT 1",
                "variants": ["control", "treat_a", "treat_b"],
                "expected_split": {"control": 0.34, "treat_a": 0.33, "treat_b": 0.33},
            }
        )
        seed_series(tables, experiment, name_2="treat_a")
        seed_series(tables, experiment, name_2="treat_b", effect=0.2)

        row = row_for(tables, experiment)
        readout = evaluate(experiment, tables.load_results(experiment.name), project=PROJECT)

        assert row["effect"] == readout.verdicts[0].effect
        assert row["verdicts"][0]["effect"] == row["effect"]

    def test_a_secondary_metric_never_appears_in_the_sub_list(self, tables):
        """The ``evaluate()`` contract, not a bug — see the module docstring."""
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "signups", "method": {"name": "t-test"}},
                {
                    "metric": "latency",
                    "is_guardrail": True,
                    "desired_direction": "decrease",
                    "method": {"name": "t-test"},
                },
            ]
        )
        for metric in ("revenue", "signups", "latency"):
            seed_series(tables, experiment, metric=metric)

        row = row_for(tables, experiment)

        assert [entry["metric"] for entry in row["verdicts"]] == ["revenue"]

    def test_but_the_boot_entry_carries_it_so_it_still_gets_a_run_button(self):
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "signups", "method": {"name": "t-test"}},
            ]
        )

        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)], project=PROJECT)[0]

        assert entry["comparisons"] == [
            {"metric": "revenue", "is_main_metric": True},
            {"metric": "signups", "is_main_metric": False},
        ]


class TestDegrade:
    def test_a_readout_failure_degrades_the_row_instead_of_sinking_the_list(
        self, tables, monkeypatch
    ):
        experiment = make_experiment()
        seed_series(tables, experiment)

        def boom(*args, **kwargs):
            raise RuntimeError("bad config edge")

        monkeypatch.setattr(overview, "evaluate", boom)
        row = row_safe_for(tables, experiment)

        assert row["error"] == "RuntimeError: bad config edge"
        assert row["verdict"] is None
        assert row["effect"] is None
        assert row["ci"] == [None, None]
        assert row["spark"] == []

    def test_the_fields_filled_before_the_failure_survive_it(self, tables, monkeypatch):
        """A FLAGGED SRM on purpose: ``False`` is also the empty-row default,
        so seeding a healthy gate would assert nothing about the ordering."""
        experiment = make_experiment()
        seed_series(tables, experiment, srm_flag=True, srm_pvalue=1e-9)
        monkeypatch.setattr(
            overview, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        )

        row = row_safe_for(tables, experiment)

        assert row["name"] == "dash_exp"
        assert row["file"] == "experiments/dash_exp.yml"
        assert row["tags"] == ["growth", "checkout"]
        assert row["main_metric"] == "revenue"
        assert (row["srm_flag"], row["srm_pvalue"]) == (True, 1e-9), (
            "the SRM read precedes the readout, so a broken assignment stays "
            "loud on a row whose verdict failed"
        )

    def test_a_db_failure_degrades_too(self, tables, monkeypatch):
        experiment = make_experiment()
        seed_series(tables, experiment)
        monkeypatch.setattr(
            type(tables),
            "load_results",
            lambda *a, **k: (_ for _ in ()).throw(ConnectionError("gone")),
        )

        row = row_safe_for(tables, experiment)

        assert row["error"] == "ConnectionError: gone"
        assert row["name"] == "dash_exp"

    def test_the_unsafe_entry_propagates_on_purpose(self, tables, monkeypatch):
        experiment = make_experiment()
        seed_series(tables, experiment)
        monkeypatch.setattr(
            overview, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        )

        with pytest.raises(RuntimeError):
            row_for(tables, experiment)

    def test_an_empty_verdict_tuple_degrades_rather_than_raising_index_error(
        self, tables, monkeypatch
    ):
        """Unreachable through a validated config — guarded anyway, not indexed blind."""
        experiment = make_experiment()
        seed_series(tables, experiment)
        monkeypatch.setattr(
            overview,
            "evaluate",
            lambda *a, **k: ExperimentReadout(
                experiment="dash_exp", srm_flag=False, srm_pvalue=None, verdicts=(), warnings=()
            ),
        )

        row = row_safe_for(tables, experiment)

        assert row["error"].startswith("ValueError:")
        assert "no verdicts" in row["error"]
        assert row["verdict"] is None


class TestSrmIsWindowIndependent:
    def test_a_red_srm_chip_survives_a_window_that_contains_no_looks(self, tables):
        """Assignment health is whole-experiment; a preset must not silence it."""
        experiment = make_experiment()
        seed_series(tables, experiment, srm_flag=True, srm_pvalue=1e-9)

        row = row_for(tables, experiment, "24h", now=NOW + timedelta(days=90))

        assert row["spark"] == [], "the window is genuinely empty"
        assert row["srm_flag"] is True
        assert row["srm_pvalue"] == 1e-9

    def test_srm_matches_the_reports_own_summary(self, tables):
        from abkit.pipeline.readout import srm_summary

        experiment = make_experiment()
        seed_series(tables, experiment, srm_flag=True, srm_pvalue=2e-8)
        expected = srm_summary(experiment, tables.load_results(experiment.name))

        row = row_for(tables, experiment, "7d")

        assert (row["srm_flag"], row["srm_pvalue"]) == expected


class TestInsufficientFlag:
    """DASH-5's §4 ``abk-insufficient`` substrate: the HEADLINE look's own
    persisted demotion cell, read (never re-derived) so the chip cannot
    disagree with the rationale printed beside it."""

    def test_a_clean_series_is_not_insufficient(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)

        assert row_for(tables, experiment)["insufficient"] is False

    def test_a_demoted_headline_look_sets_the_flag_and_the_readout_agrees(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment, days=13)
        save_rows(
            tables,
            [
                make_row(
                    experiment,
                    day=14,
                    insufficient_data=True,
                    effect=None,
                    left_bound=None,
                    right_bound=None,
                    pvalue=None,
                    reject=None,
                )
            ],
        )

        row = row_for(tables, experiment)

        assert row["insufficient"] is True
        assert row["verdict"] == "INCONCLUSIVE", "the readout withheld inference"
        assert any("insufficient data" in line for line in row["rationale"])

    def test_an_earlier_demoted_look_does_not_flag_the_row(self, tables):
        """The flag is the headline look's, not "any look in the series"."""
        experiment = make_experiment()
        save_rows(
            tables,
            [make_row(experiment, day=1, insufficient_data=True, effect=None)]
            + [make_row(experiment, day=day) for day in range(2, 15)],
        )

        row = row_for(tables, experiment)

        assert row["insufficient"] is False
        assert row["verdict"] == "WIN"

    @pytest.mark.parametrize("preset", sorted(ALL_WINDOW_PRESETS))
    def test_no_window_preset_can_move_the_flag(self, tables, preset):
        """The demotion is read off the headline's own look, and the headline is
        always the FULL series' — so a preset showing no looks at all still
        reports it (the ``24h``-preset hazard DASH-2 as-built (1) describes)."""
        experiment = make_experiment()
        seed_series(tables, experiment, days=13)
        save_rows(tables, [make_row(experiment, day=14, insufficient_data=True, effect=None)])

        row = row_for(tables, experiment, preset, now=NOW + timedelta(days=120))

        if preset != "all":
            assert row["spark"] == [], "every fixed preset is genuinely empty here"
        assert row["insufficient"] is True

    def test_a_never_run_experiment_is_not_insufficient(self, tables):
        """No looks at all is the "no data yet" state, not a demotion."""
        experiment = make_experiment()

        row = row_for(tables, experiment)

        assert row["verdict"] == "INCONCLUSIVE"
        assert row["insufficient"] is False
        assert row["last_end_ts"] is None

    def test_a_degraded_row_reports_the_documented_default(self, tables, monkeypatch):
        experiment = make_experiment()
        seed_series(tables, experiment)
        monkeypatch.setattr(
            overview, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        )

        assert row_safe_for(tables, experiment)["insufficient"] is False

    def test_the_flag_reads_the_cell_the_readout_reads(self, tables):
        """A ``"0"`` string cell is falsy to ``bool(int(...))`` and TRUTHY to a
        plain ``bool()`` — the row must side with the readout (whose demotion
        branch decides the verdict), not with the report's looser flag."""
        assert overview._flag("0") is False
        assert overview._flag(1) is True
        assert overview._flag(None) is False


class TestLock:
    def test_a_held_run_lock_greys_the_row(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        assert tables.acquire_lock(experiment.name, "pipeline", "run") is True

        assert row_for(tables, experiment)["locked"] is True

    def test_a_validate_lock_does_not_grey_it(self, tables):
        """It never blocks ``abk run``, which is the button this flag guards."""
        experiment = make_experiment()
        seed_series(tables, experiment)
        assert tables.acquire_lock(experiment.name, "pipeline", "validate") is True

        assert row_for(tables, experiment)["locked"] is False

    def test_the_probed_triple_is_the_one_the_driver_takes(self):
        from abkit.pipeline import driver

        assert (overview._LOCK_SCOPE, overview._LOCK_PROCESS_TYPE) == (
            driver.LOCK_SCOPE,
            driver.LOCK_PROCESS,
        )

    def test_an_unreadable_tasks_table_costs_the_flag_and_nothing_else(self, tables, monkeypatch):
        """A partial ``ensure_tables`` or a narrow read-only grant must not be
        able to blank a verdict — least of all the SRM chip."""
        experiment = make_experiment()
        seed_series(tables, experiment, srm_flag=True, srm_pvalue=1e-9)
        monkeypatch.setattr(
            type(tables),
            "check_lock",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("_ab_tasks does not exist")),
        )

        row = row_safe_for(tables, experiment)

        assert row["error"] is None
        assert row["locked"] is False, "fails closed on the flag, open on the button"
        # SRM gates the decision, so INCONCLUSIVE here is the readout's own
        # answer — the point is that it is an ANSWER, not a blanked row.
        assert row["verdict"] == "INCONCLUSIVE"
        assert row["effect"] == 0.1
        assert (row["srm_flag"], row["srm_pvalue"]) == (True, 1e-9)
        assert len(row["spark"]) == 14


class TestBootEntries:
    def test_the_boot_entry_shape(self):
        experiment = make_experiment()

        entries = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)], project=PROJECT)

        assert entries == [
            {
                "name": "dash_exp",
                "dir": "",
                "file": "experiments/dash_exp.yml",
                "tags": ["growth", "checkout"],
                "status": "running",
                "timezone": "UTC",
                "start_ts": ms(datetime(2026, 1, 1)),
                "horizon_ts": ms(datetime(2026, 1, 15)),
                "main_metric": "revenue",
                "comparisons": [{"metric": "revenue", "is_main_metric": True}],
            }
        ]

    def test_it_carries_no_stats_at_all(self):
        experiment = make_experiment()

        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)], project=PROJECT)[0]

        stat_keys = {"verdict", "effect", "ci", "pvalue", "spark", "srm_flag", "locked"}
        assert stat_keys.isdisjoint(entry), "GET / must not need a database"

    def test_absent_tags_read_as_an_empty_list_never_null(self):
        experiment = make_experiment(tags=None)

        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)], project=PROJECT)[0]

        assert entry["tags"] == []

    def test_a_nested_experiment_reports_its_group_dir(self):
        experiment = make_experiment()
        path = ROOT / "experiments" / "growth" / "q1" / "dash_exp.yml"

        entry = build_overview_boot_entries(ROOT, [(path, experiment)], project=PROJECT)[0]

        assert entry["dir"] == "growth/q1"
        assert entry["file"] == "experiments/growth/q1/dash_exp.yml"

    def test_a_renamed_experiments_dir_is_honored_when_the_project_is_passed(self):
        project = ProjectConfig.model_validate(
            {"name": "p", "default_profile": "dev", "paths": {"experiments": "tests_ab"}}
        )
        experiment = make_experiment()
        path = ROOT / "tests_ab" / "growth" / "dash_exp.yml"

        entry = build_overview_boot_entries(ROOT, [(path, experiment)], project=project)[0]

        assert entry["dir"] == "growth"

    def test_the_stats_row_and_the_boot_entry_agree_on_identity(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        path = ROOT / "experiments" / "growth" / "dash_exp.yml"

        entry = build_overview_boot_entries(ROOT, [(path, experiment)], project=PROJECT)[0]
        row = row_for(tables, experiment, experiment_path=path)

        for key in ("name", "dir", "file", "tags", "status", "start_ts", "horizon_ts"):
            assert entry[key] == row[key], f"{key} disagrees between boot list and stats row"


class TestLocation:
    def test_a_top_level_experiment_has_an_empty_dir(self):
        assert resolve_experiment_location(EXP_PATH, ROOT, ROOT / "experiments") == (
            "",
            "experiments/dash_exp.yml",
        )

    def test_a_path_outside_the_roots_falls_back_instead_of_raising(self):
        outside = Path("/elsewhere/x.yml")

        assert resolve_experiment_location(outside, ROOT, ROOT / "experiments") == (
            "",
            "/elsewhere/x.yml",
        )


class TestAgreesWithTheReport:
    """The milestone's #1 gate: the dashboard cannot say what the report won't."""

    def test_the_headline_equals_what_abk_run_report_would_bake(self, tables):
        from abkit.reporting.builder import build_report_payload

        experiment = make_experiment()
        seed_series(tables, experiment)

        row = row_for(tables, experiment)
        payload = build_report_payload(experiment, tables, project=PROJECT, generated_at="now")
        baked = payload["verdicts"][0]

        assert row["verdict"] == baked["verdict"]
        assert row["effect"] == baked["effect"]
        assert row["ci"] == [baked["lo"], baked["hi"]]
        assert row["pvalue"] == baked["pvalue"]
        assert row["alpha"] == baked["alpha"]
        assert row["last_end_ts"] == baked["end_ts"]

    def test_the_project_reaches_evaluate_so_read_time_bh_is_applied(self, tables):
        """Without ``project=`` the readout falls back to stored-alpha CI and a
        project-level Benjamini-Hochberg is mis-scored — a silent false WIN."""
        project = ProjectConfig.model_validate(
            {
                "name": "p",
                "default_profile": "dev",
                "statistics": {"correction": "benjamini_hochberg"},
            }
        )
        experiment = make_experiment(
            correction=None,
            comparisons=[
                {"metric": m, "is_main_metric": True, "method": {"name": "t-test"}}
                for m in ("revenue", "signups", "clicks", "visits")
            ],
        )
        seed_series(tables, experiment, metric="revenue", pvalue=0.06)
        for metric in ("signups", "clicks", "visits"):
            seed_series(tables, experiment, metric=metric, pvalue=0.06)

        row = row_for(tables, experiment, project=project)
        rows = tables.load_results(experiment.name)

        assert row["verdict"] == evaluate(experiment, rows, project=project).verdicts[0].verdict
        assert row["verdict"] != evaluate(experiment, rows).verdicts[0].verdict, (
            "the fixture must actually distinguish the two resolutions, "
            "or this gate proves nothing"
        )

    def test_rows_from_a_renamed_away_arm_never_join_the_correction_family(self, tables):
        """``_filter_rows`` screens by metric and method id only; the BH family
        is built from every informative row at the cutoff."""
        from abkit.reporting.builder import build_report_payload

        experiment = make_experiment(correction="benjamini_hochberg")
        seed_series(tables, experiment, pvalue=0.03, left_bound=0.01, right_bound=0.19)
        for index in range(9):
            rows = [
                make_row(experiment, day=day, pvalue=0.9, name_2=f"treatment_v{index}")
                for day in range(1, 15)
            ]
            save_rows(tables, rows)

        row = row_for(tables, experiment)
        baked = build_report_payload(experiment, tables, project=PROJECT, generated_at="now")

        assert row["verdict"] == baked["verdicts"][0]["verdict"]
        assert row["verdict"] == "WIN"
        assert len(row["spark"]) == 14, "the stale pairs stay out of the curve too"


class TestSparkIsTheHeadlinesOwnSeries:
    def test_another_metrics_rows_never_join_the_curve(self, tables):
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "signups", "method": {"name": "t-test"}},
            ]
        )
        seed_series(tables, experiment, metric="revenue", days=3)
        seed_series(tables, experiment, metric="signups", days=3, effect=99.0)

        row = row_for(tables, experiment)

        assert [value for _, value in row["spark"]] == [0.1, 0.1, 0.1]

    def test_another_arms_rows_never_join_the_curve(self, tables):
        experiment = make_experiment(
            assignment={
                "query": "SELECT 1",
                "variants": ["control", "treat_a", "treat_b"],
                "expected_split": {"control": 0.34, "treat_a": 0.33, "treat_b": 0.33},
            }
        )
        seed_series(tables, experiment, name_2="treat_a", days=3)
        seed_series(tables, experiment, name_2="treat_b", days=3, effect=99.0)

        row = row_for(tables, experiment)

        assert [value for _, value in row["spark"]] == [0.1, 0.1, 0.1]

    def test_the_method_id_is_the_headlines_own_not_the_first_comparisons(self, tables):
        """A guardrail declared FIRST under a different method must not blank
        the chart while every headline number renders fine."""
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "latency",
                    "is_guardrail": True,
                    "desired_direction": "decrease",
                    "method": {"name": "z-test"},
                },
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
            ]
        )
        seed_series(tables, experiment, metric="latency", days=5)
        seed_series(tables, experiment, metric="revenue", days=5)

        row = row_for(tables, experiment)

        assert len(row["spark"]) == 5


class TestSeveralMainMetrics:
    def _experiment(self):
        return make_experiment(
            assignment={
                "query": "SELECT 1",
                "variants": ["control", "treat_a", "treat_b"],
                "expected_split": {"control": 0.34, "treat_a": 0.33, "treat_b": 0.33},
            },
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "signups", "is_main_metric": True, "method": {"name": "t-test"}},
            ],
        )

    def test_the_expand_list_carries_each_pairs_own_metric_and_verdict(self, tables):
        experiment = self._experiment()
        for metric in ("revenue", "signups"):
            seed_series(tables, experiment, metric=metric, name_2="treat_a")
            seed_series(tables, experiment, metric=metric, name_2="treat_b")
        # one losing arm — a sub-list that copied the headline would hide it
        save_rows(
            tables,
            [
                make_row(
                    experiment,
                    metric="revenue",
                    name_2="treat_b",
                    day=day,
                    effect=-0.1,
                    left_bound=-0.15,
                    right_bound=-0.05,
                )
                for day in range(1, 15)
            ],
        )

        row = row_for(tables, experiment)

        assert [
            (entry["metric"], entry["pair"], entry["verdict"], entry["effect"])
            for entry in row["verdicts"]
        ] == [
            ("revenue", {"c": "control", "t": "treat_a"}, "WIN", 0.1),
            ("revenue", {"c": "control", "t": "treat_b"}, "LOSE", -0.1),
            ("signups", {"c": "control", "t": "treat_a"}, "WIN", 0.1),
            ("signups", {"c": "control", "t": "treat_b"}, "WIN", 0.1),
        ]
        assert all(entry["caveats"] == [] for entry in row["verdicts"])
        assert all(entry["guardrail_regressed"] is False for entry in row["verdicts"])

    def test_the_headline_metric_is_the_first_main_one_on_both_surfaces(self, tables):
        experiment = self._experiment()
        for metric in ("revenue", "signups"):
            seed_series(tables, experiment, metric=metric, name_2="treat_a")
            seed_series(tables, experiment, metric=metric, name_2="treat_b")

        row = row_for(tables, experiment)
        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)], project=PROJECT)[0]

        assert row["main_metric"] == "revenue"
        assert entry["main_metric"] == "revenue"
        assert row["verdicts"][0]["metric"] == "revenue"


class TestQualifiedVerdicts:
    def test_a_regressed_guardrail_is_disclosed_even_when_the_win_is_kept(self, tables):
        """``guardrail_policy: warn`` keeps WIN *with* a mandatory loud caveat —
        a row carrying only the word WIN would be the green light it withheld."""
        experiment = make_experiment(
            readout={"guardrail_policy": "warn"},
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {
                    "metric": "latency",
                    "is_guardrail": True,
                    "desired_direction": "decrease",
                    "method": {"name": "t-test"},
                },
            ],
        )
        seed_series(tables, experiment, metric="revenue")
        seed_series(
            tables,
            experiment,
            metric="latency",
            effect=0.3,
            left_bound=0.2,
            right_bound=0.4,
        )

        row = row_for(tables, experiment)

        assert row["verdict"] == "WIN"
        assert row["guardrail_regressed"] is True
        assert any("latency" in caveat for caveat in row["caveats"])

    def test_a_clean_win_carries_neither(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)

        row = row_for(tables, experiment)

        assert (row["verdict"], row["guardrail_regressed"], row["caveats"]) == ("WIN", False, [])

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(None, None), (float("nan"), None), (float("inf"), None), ("", None), (b"x", None)],
    )
    def test_the_scrubber_answers_none_for_anything_json_cannot_hold(self, value, expected):
        """Width matters: a driver can hand back a string or bytes cell, and a
        raising scrubber would cost the whole row over one unreadable number."""
        assert overview._num(value) is expected

    def test_a_non_finite_headline_cell_is_nulled_not_passed_through(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment, days=13)
        save_rows(
            tables,
            [make_row(experiment, day=14, effect=float("inf"), pvalue=float("nan"))],
        )

        row = row_for(tables, experiment)

        assert row["effect"] is None
        assert row["pvalue"] is None


class TestConfigCellsAreReadNotAssumed:
    @pytest.mark.parametrize("status", ["design", "running", "concluded", "archived"])
    def test_status_is_the_configs_own_on_both_surfaces(self, tables, status):
        experiment = make_experiment(status=status)
        seed_series(tables, experiment)

        row = row_for(tables, experiment)
        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)], project=PROJECT)[0]

        assert row["status"] == status
        assert entry["status"] == status

    def test_a_renamed_experiments_dir_is_honored_by_the_stats_row_too(self, tables):
        project = ProjectConfig.model_validate(
            {"name": "p", "default_profile": "dev", "paths": {"experiments": "tests_ab"}}
        )
        experiment = make_experiment()
        seed_series(tables, experiment)
        path = ROOT / "tests_ab" / "growth" / "dash_exp.yml"

        row = row_for(tables, experiment, project=project, experiment_path=path)
        entry = build_overview_boot_entries(ROOT, [(path, experiment)], project=project)[0]

        assert row["dir"] == "growth"
        assert entry["dir"] == row["dir"], "the shell and the fill must group alike"

    def test_tags_are_copied_so_a_consumer_cannot_mutate_the_config(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)

        row = row_for(tables, experiment)
        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)], project=PROJECT)[0]

        assert row["tags"] is not experiment.tags
        assert entry["tags"] is not experiment.tags


class TestRoundTwoRegressions:
    """One gate per defect the second adversarial round reproduced."""

    def test_a_reversed_arm_pair_row_never_joins_the_correction_family(self, tables):
        """Swapping the declared variant order leaves rows whose ``name_1`` is
        the treatment. ``combinations`` excludes them; ``permutations`` would
        not, and they would tighten every BH threshold."""
        experiment = make_experiment(correction="benjamini_hochberg")
        seed_series(tables, experiment, pvalue=0.03, left_bound=0.01, right_bound=0.19)
        reversed_rows = [
            make_row(experiment, day=day, pvalue=0.9) | {"name_1": "treatment", "name_2": "control"}
            for day in range(1, 15)
        ]
        save_rows(tables, reversed_rows)

        row = row_for(tables, experiment)

        assert row["verdict"] == "WIN"
        assert len(row["spark"]) == 14

    def test_a_regression_on_the_second_arm_still_flags_the_row(self, tables):
        """The row-level flag is ORed across every listed pair: a green flag
        must not coexist with a qualified verdict on the same row."""
        experiment = make_experiment(
            readout={"guardrail_policy": "warn"},
            assignment={
                "query": "SELECT 1",
                "variants": ["control", "treat_a", "treat_b"],
                "expected_split": {"control": 0.34, "treat_a": 0.33, "treat_b": 0.33},
            },
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {
                    "metric": "latency",
                    "is_guardrail": True,
                    "desired_direction": "decrease",
                    "method": {"name": "t-test"},
                },
            ],
        )
        for arm in ("treat_a", "treat_b"):
            seed_series(tables, experiment, metric="revenue", name_2=arm)
        seed_series(
            tables,
            experiment,
            metric="latency",
            name_2="treat_a",
            effect=-0.3,
            left_bound=-0.4,
            right_bound=-0.2,
        )
        seed_series(
            tables,
            experiment,
            metric="latency",
            name_2="treat_b",
            effect=0.3,
            left_bound=0.2,
            right_bound=0.4,
        )

        row = row_for(tables, experiment)

        assert row["verdict"] == "WIN"
        assert row["guardrail_regressed"] is True, "the headline arm is clean; treat_b is not"
        assert row["verdicts"][0]["guardrail_regressed"] is False
        assert row["verdicts"][1]["guardrail_regressed"] is True
        assert any("latency" in caveat for caveat in row["verdicts"][1]["caveats"])

    def test_a_bucket_mean_that_overflows_is_nulled(self, monkeypatch):
        """Two finite effects near the float ceiling sum to ``inf``, which
        JSON cannot express — the scrub is on the MEAN, not only the cells."""
        monkeypatch.setattr(overview, "_MAX_SPARK_BUCKETS", 1)
        experiment = make_experiment()
        rows = [make_row(experiment, day=day, effect=1.7e308) for day in (1, 2)]

        with np.errstate(over="ignore"):  # the overflow IS the case under test
            spark = overview._spark_series(rows)

        assert spark == [[ms(START + timedelta(days=2)), None]]

    def test_last_end_ts_is_the_headlines_cutoff_not_the_latest_row(self, tables):
        """Another metric can be ahead; the row's stat cells are as of the
        headline pair's own last look."""
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "signups", "method": {"name": "t-test"}},
            ]
        )
        seed_series(tables, experiment, metric="revenue", days=10)
        seed_series(tables, experiment, metric="signups", days=14)

        row = row_for(tables, experiment)

        assert row["last_end_ts"] == ms(START + timedelta(days=10))

    def test_the_pair_series_is_sorted_even_when_the_read_is_not(self):
        experiment = make_experiment()
        rows = [make_row(experiment, day=day, effect=float(day)) for day in (3, 1, 2)]
        headline = overview.PairVerdict(
            metric="revenue",
            name_1="control",
            name_2="treatment",
            verdict="WIN",
            rationale=(),
            caveats=(),
            end_ts=None,
            elapsed_days=None,
            is_horizon=False,
            effect=None,
            pvalue=None,
            left_bound=None,
            right_bound=None,
            alpha=None,
            significant=False,
            mde=None,
            min_effect=None,
            weekly_cycle_pct=None,
            guardrails=(),
        )

        picked = overview._pair_rows(experiment, rows, headline)

        assert [row["effect"] for row in picked] == [1.0, 2.0, 3.0]

    def test_the_lock_flag_survives_a_failing_read(self, tables, monkeypatch):
        """The degraded row is the one an operator is most likely to press Run
        on — reporting it unlocked while a run holds the lock is the worst
        moment to be wrong."""
        experiment = make_experiment()
        seed_series(tables, experiment)
        assert tables.acquire_lock(experiment.name, "pipeline", "run") is True
        monkeypatch.setattr(
            type(tables),
            "load_results",
            lambda *a, **k: (_ for _ in ()).throw(ConnectionError("gone")),
        )

        row = row_safe_for(tables, experiment)

        assert row["error"] == "ConnectionError: gone"
        assert row["locked"] is True

    def test_renamed_away_arms_leave_a_warning_not_just_an_empty_row(self, tables):
        """Otherwise a renamed arm looks exactly like a never-run experiment."""
        experiment = make_experiment()
        seed_series(tables, experiment, name_2="treat_c")

        row = row_for(tables, experiment)

        assert row["spark"] == []
        assert any("renamed arms" in warning for warning in row["warnings"])
        assert any("abk clean" in warning for warning in row["warnings"])

    def test_an_orphaned_series_leaves_the_readouts_own_warning(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        save_rows(
            tables,
            [
                make_row(experiment, day=day, method_config_id="dead" + "0" * 12)
                for day in range(1, 15)
            ],
        )

        row = row_for(tables, experiment)

        assert row["verdict"] == "WIN"
        assert any("orphaned" in warning for warning in row["warnings"])

    def test_a_healthy_row_warns_about_nothing(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)

        assert row_for(tables, experiment)["warnings"] == []

    def test_the_experiment_timezone_rides_along_on_both_surfaces(self, tables):
        """Instants are naive UTC; without the zone a client is off by a day."""
        experiment = make_experiment(timezone="Europe/Moscow")
        seed_series(tables, experiment)

        row = row_for(tables, experiment)
        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)], project=PROJECT)[0]

        assert row["timezone"] == "Europe/Moscow"
        assert entry["timezone"] == "Europe/Moscow"

    def test_the_rationale_is_the_readouts_own_words(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        headline = evaluate(
            experiment, tables.load_results(experiment.name), project=PROJECT
        ).verdicts[0]

        assert row_for(tables, experiment)["rationale"] == list(headline.rationale)

    def test_the_preset_error_is_its_own_type_so_a_route_can_answer_400(self, tables):
        experiment = make_experiment()

        with pytest.raises(overview.UnknownWindowPreset):
            row_safe_for(tables, experiment, "1y")
        assert issubclass(overview.UnknownWindowPreset, ValueError)

    def test_the_verdict_list_and_the_boot_list_do_not_share_a_key(self, tables):
        """DASH-5 merges the two payloads by name; one key, two shapes is a trap."""
        experiment = make_experiment()
        seed_series(tables, experiment)

        row = row_for(tables, experiment)
        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)], project=PROJECT)[0]

        assert "comparisons" not in row
        assert "verdicts" not in entry


class TestModuleContract:
    def test_the_shaper_never_imports_the_statistics_core(self):
        source = Path(overview.__file__).read_text()
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not [name for name in imported if name.startswith("abkit.stats")]

    def test_the_builders_are_exported_from_the_package(self):
        import abkit.tuning as package

        for name in (
            "build_experiment_row",
            "build_experiment_row_safe",
            "build_overview_boot_entries",
            "WINDOW_PRESETS",
            "ALL_WINDOW_PRESETS",
        ):
            assert name in package.__all__
            assert hasattr(package, name)
