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
from abkit.pipeline.readout import ExperimentReadout, evaluate
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

        row = row_for(tables, experiment)

        assert row == {
            "name": "dash_exp",
            "dir": "",
            "file": "experiments/dash_exp.yml",
            "tags": ["growth", "checkout"],
            "status": "running",
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
            "last_end_ts": ms(datetime(2026, 1, 15)),
            "spark": [[ms(START + timedelta(days=d)), 0.1] for d in range(1, 15)],
            "comparisons": [
                {
                    "metric": "revenue",
                    "pair": {"c": "control", "t": "treatment"},
                    "verdict": "WIN",
                    "effect": 0.1,
                }
            ],
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

    def test_the_degraded_row_has_exactly_the_keys_a_filled_row_has(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)

        filled = row_for(tables, experiment)
        empty = overview._empty_row("dash_exp")

        assert set(filled) == set(empty), "a degraded row must be renderable by the same client"

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
        experiment = make_experiment()
        seed_series(tables, experiment)
        full = row_for(tables, experiment)

        monkeypatch.setattr(overview, "MAX_STAT_POINTS", 3)
        capped = row_for(tables, experiment)

        assert len(capped["spark"]) == 3, "the sparkline truncates to the most recent looks"
        assert capped["spark"] == full["spark"][-3:]
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

    def test_a_window_with_no_looks_reads_as_no_looks_not_as_a_verdict(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)

        row = row_for(tables, experiment, "24h", now=NOW + timedelta(days=90))

        assert row["error"] is None
        assert row["last_end_ts"] is None, "the client's 'no looks in this window' signal"
        assert row["spark"] == []
        assert row["verdict"] == "INCONCLUSIVE"

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

        assert [entry["pair"] for entry in row["comparisons"]] == [
            {"c": "control", "t": "treat_a"},
            {"c": "control", "t": "treat_b"},
        ]
        assert [entry["effect"] for entry in row["comparisons"]] == [0.1, 0.2]

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
        assert row["comparisons"][0]["effect"] == row["effect"]

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

        assert [entry["metric"] for entry in row["comparisons"]] == ["revenue"]

    def test_but_the_boot_entry_carries_it_so_it_still_gets_a_run_button(self):
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "signups", "method": {"name": "t-test"}},
            ]
        )

        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)])[0]

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
        experiment = make_experiment()
        seed_series(tables, experiment)
        monkeypatch.setattr(
            overview, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        )

        row = row_safe_for(tables, experiment)

        assert row["name"] == "dash_exp"
        assert row["file"] == "experiments/dash_exp.yml"
        assert row["tags"] == ["growth", "checkout"]
        assert row["main_metric"] == "revenue"
        assert row["srm_flag"] is False, "SRM ran before the readout and is kept"

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


class TestBootEntries:
    def test_the_boot_entry_shape(self):
        experiment = make_experiment()

        entries = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)])

        assert entries == [
            {
                "name": "dash_exp",
                "dir": "",
                "file": "experiments/dash_exp.yml",
                "tags": ["growth", "checkout"],
                "status": "running",
                "start_ts": ms(datetime(2026, 1, 1)),
                "horizon_ts": ms(datetime(2026, 1, 15)),
                "main_metric": "revenue",
                "comparisons": [{"metric": "revenue", "is_main_metric": True}],
            }
        ]

    def test_it_carries_no_stats_at_all(self):
        experiment = make_experiment()

        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)])[0]

        stat_keys = {"verdict", "effect", "ci", "pvalue", "spark", "srm_flag", "locked"}
        assert stat_keys.isdisjoint(entry), "GET / must not need a database"

    def test_absent_tags_read_as_an_empty_list_never_null(self):
        experiment = make_experiment(tags=None)

        entry = build_overview_boot_entries(ROOT, [(EXP_PATH, experiment)])[0]

        assert entry["tags"] == []

    def test_a_nested_experiment_reports_its_group_dir(self):
        experiment = make_experiment()
        path = ROOT / "experiments" / "growth" / "q1" / "dash_exp.yml"

        entry = build_overview_boot_entries(ROOT, [(path, experiment)])[0]

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

        entry = build_overview_boot_entries(ROOT, [(path, experiment)])[0]
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
