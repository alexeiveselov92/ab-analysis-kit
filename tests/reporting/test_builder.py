"""Unit tests for abkit.reporting.builder (the §5.3 experiment payload).

Rewrites the donor ``test_report.py`` payload cases experiment-primary
(m3-implementation-plan.md WP2): fake_db-seeded ``_ab_results`` for the
integration shape, plus the donor's narrow duck-typed stub where a test
needs NaN injection the fake manager would scrub.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from abkit.config.experiment_config import ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.core.period_planner import generate_grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._results import RESULT_COLUMNS
from abkit.pipeline.readout import evaluate
from abkit.reporting.builder import (
    PAYLOAD_VERSION,
    _ms,
    _num_or_none,
    build_report_payload,
)
from abkit.stats.power import _ttest_effect_size_at_power
from abkit.utils.json_utils import json_dumps_sorted
from tests._helpers.fake_db import FakeDatabaseManager

START = datetime(2026, 1, 1)

TOP_LEVEL_KEYS = {
    "v",
    "experiment",
    "project",
    "generated_at",
    "description",
    "period",
    "cadence_seconds",
    "tz",
    "arms",
    "srm",
    "calibration",
    "verdicts",
    "metrics",
    "look",
    "endpoints",
    "warnings",
}


def make_experiment(**overrides) -> ExperimentConfig:
    config = {
        "name": "report_exp",
        "description": "the readout fixture",
        "start_date": "2026-01-01",
        "end_date": "2026-01-14",  # horizon = 2026-01-15 00:00 (day 14)
        "unit_key": "user_id",
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


def make_row(experiment: ExperimentConfig, metric: str = "revenue", **overrides) -> dict:
    """One full-contract ``_ab_results`` row (the WP1 fixture shape)."""
    try:
        comparison = experiment.get_comparison(metric)
        method_name = comparison.method.name
        method_params = comparison.method.canonical_params_json
        method_config_id = comparison.method.method_config_id
        is_main = comparison.is_main_metric
        is_guardrail = comparison.is_guardrail
    except KeyError:
        method_name, method_params = "t-test", '{"test_type":"relative"}'
        method_config_id, is_main, is_guardrail = "x" * 16, False, False
    day = overrides.pop("day", 14)
    end_ts = START + timedelta(days=day)
    row = {
        "experiment": experiment.name,
        "metric": metric,
        "is_main_metric": is_main,
        "is_guardrail": is_guardrail,
        "method_name": method_name,
        "method_params": method_params,
        "method_config_id": method_config_id,
        "name_1": "control",
        "name_2": "treatment",
        "start_ts": START,
        "end_ts": end_ts,
        "start_date": date(2026, 1, 1),
        "end_date": (end_ts - timedelta(microseconds=1)).date(),
        "window_seconds": day * 86400,
        "elapsed_days": float(day),
        "value_1": 10.0,
        "value_2": 11.0,
        "std_1": 2.0,
        "std_2": 2.0,
        "cov_value_1": None,
        "cov_value_2": None,
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
        "metric_rendered_query": "SELECT rendered_2026_01_01",
        "watermark_ts": end_ts,
    }
    row.update(overrides)
    return row


def save_rows(tables: InternalTablesManager, rows: list[dict]) -> None:
    batch = {col: np.array([row[col] for row in rows], dtype=object) for col in RESULT_COLUMNS}
    tables.save_results(batch)


# the `tables` fixture lives in tests/reporting/conftest.py (shared with the
# WP3 html-report suite)


def seed_series(
    tables: InternalTablesManager,
    experiment: ExperimentConfig,
    metric: str = "revenue",
    days: int = 14,
    **overrides,
) -> list[dict]:
    rows = [make_row(experiment, metric=metric, day=d, **overrides) for d in range(1, days + 1)]
    save_rows(tables, rows)
    return rows


class StubTables:
    """The donor's duck-typed fake: only what the builder calls, no DB.

    Lets a test inject NaN — the fake manager's coercion layer scrubs it.
    """

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def results_table_exists(self) -> bool:
        return True

    def load_results(self, experiment, metric=None, method_config_id=None):
        rows = [
            dict(row)
            for row in self._rows
            if row["experiment"] == experiment
            and (metric is None or row["metric"] == metric)
            and (method_config_id is None or row["method_config_id"] == method_config_id)
        ]
        rows.sort(key=lambda r: (r["metric"], r["name_1"], r["name_2"], r["end_ts"]))
        return rows

    def exposures_table_exists(self) -> bool:
        return False

    def get_exposure_counts(self, experiment, until=None):
        return {}

    def aa_runs_table_exists(self) -> bool:
        return False

    def get_aa_runs(self, experiment):
        return []

    def list_method_config_ids(self, experiment, metric=None):
        return {}


class TestHelpers:
    def test_num_or_none_scrubs_nan_and_inf(self):
        assert _num_or_none(None) is None
        assert _num_or_none(float("nan")) is None
        assert _num_or_none(float("inf")) is None
        assert _num_or_none(float("-inf")) is None
        assert _num_or_none("not a number") is None
        assert _num_or_none(np.float64(1.5)) == 1.5
        assert _num_or_none(0.0) == 0.0

    def test_ms_epoch(self):
        assert _ms(datetime(1970, 1, 1)) == 0
        assert _ms(datetime(1970, 1, 2)) == 86_400_000


class TestPayloadShape:
    def test_top_level_contract(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        payload = build_report_payload(experiment, tables, generated_at="2026-07-03 00:00")

        assert set(payload) == TOP_LEVEL_KEYS
        assert payload["v"] == PAYLOAD_VERSION
        assert payload["experiment"] == "report_exp"
        assert payload["project"] is None
        assert payload["generated_at"] == "2026-07-03 00:00"
        assert payload["description"] == "the readout fixture"
        assert payload["tz"] == "UTC"
        assert payload["arms"] == ["control", "treatment"]
        assert payload["cadence_seconds"] == 86400
        assert payload["calibration"] is None
        assert payload["endpoints"] == {
            "save_url": None,
            "recompute_url": None,
            "reload_url": None,
            "validate_url": None,
        }
        assert payload["period"]["start"] == _ms(START)
        assert payload["period"]["end"] == _ms(START + timedelta(days=14))
        assert payload["period"]["horizon"] == _ms(datetime(2026, 1, 15))

    def test_point_keys_and_values(self, tables):
        experiment = make_experiment()
        # asymmetric arm MDEs: the D5(b) pair MDE is the LARGER magnitude —
        # a degenerate symmetric fixture could not tell max() from either arm
        seed_series(tables, experiment, days=1, mde_1=0.03, mde_2=0.08)
        payload = build_report_payload(experiment, tables)

        (metric,) = payload["metrics"]
        (pair,) = metric["pairs"]
        (point,) = pair["series"]
        assert point == {
            "t": _ms(START + timedelta(days=1)),
            "ed": 1.0,
            "e": 0.1,
            "lo": 0.05,
            "hi": 0.15,
            "p": 0.001,
            "rj": 1,
            "s1": 1000,
            "s2": 1000,
            "v1": 10.0,
            "v2": 11.0,
            "sd1": 2.0,
            "sd2": 2.0,
            "cv1": None,  # non-CUPED row — covariate means stay null
            "cv2": None,
            "mde": 0.08,  # max(|mde_1|, |mde_2|) — the underpowered arm wins
            "hz": 0,
            "blk": 0,
            "ins": 0,
        }

    def test_point_mde_null_when_not_computed(self, tables):
        """NULL mde columns: the point shows null — no per-point read-time solve.

        The read-time D5(b) fallback is verdict-level, not per historical point
        (cost discipline). A row that never computed MDE honestly reports null.
        """
        experiment = make_experiment()
        seed_series(tables, experiment, days=1, mde_1=None, mde_2=None)
        payload = build_report_payload(experiment, tables)

        (point,) = payload["metrics"][0]["pairs"][0]["series"]
        assert point["mde"] is None
        # but the verdict still carries the read-time fallback (0.0251 known
        # answer for value 10/11, std 2, n 1000, relative, alpha 0.05, power .8)
        (verdict,) = payload["verdicts"]
        assert verdict["mde"] == pytest.approx(0.0251, abs=5e-4)

    def test_point_mde_half_present_is_null_not_finite_arm(self, tables):
        """A half-present pair (one arm's MDE solved to inf, enrich NULLed it):
        the point must show null, never the finite arm — taking it alone fakes
        adequate power on a pair the verdict declares undetectable (D5(b)
        both-present guard; review finding)."""
        experiment = make_experiment()
        seed_series(tables, experiment, days=1, mde_1=0.05, mde_2=None)
        payload = build_report_payload(experiment, tables)

        (point,) = payload["metrics"][0]["pairs"][0]["series"]
        assert point["mde"] is None

    def test_point_mde_read_time_solve_not_called_per_point(self, tables):
        """Guard the cost fix: building a series triggers zero t-test solves."""
        experiment = make_experiment()
        seed_series(tables, experiment, days=8, mde_1=None, mde_2=None)
        _ttest_effect_size_at_power.cache_clear()
        before = _ttest_effect_size_at_power.cache_info()
        build_report_payload(experiment, tables)
        after = _ttest_effect_size_at_power.cache_info()
        # the only solves are the verdict's (one per pair on the latest cutoff),
        # never one per point — 8 points would be 8+ misses if regressed
        assert after.misses - before.misses <= 2

    def test_method_block_reflects_what_ran(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment, alpha=0.01)
        payload = build_report_payload(experiment, tables)

        method = payload["metrics"][0]["method"]
        comparison = experiment.comparisons[0]
        assert method["name"] == "t-test"
        assert method["id"] == comparison.method.method_config_id
        assert method["params"] == json.loads(comparison.method.canonical_params_json)
        assert method["alpha"] == 0.01  # the stored row alpha, not config

    def test_metric_description_from_metric_config(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        metric_config = MetricConfig.model_validate(
            {
                "name": "revenue",
                "description": "revenue per user",
                "type": "sample",
                "columns": {"variant": "variant", "value": "revenue"},
                "sql": "SELECT 1",
            }
        )
        payload = build_report_payload(
            experiment, tables, metric_configs={"revenue": metric_config}
        )
        assert payload["metrics"][0]["description"] == "revenue per user"

        bare = build_report_payload(experiment, tables)
        assert bare["metrics"][0]["description"] is None


class TestCalibrationBlock:
    """The M4 `calibration` payload fill from `_ab_aa_runs` (WP5)."""

    def _save_aa_run(self, tables, experiment, **overrides):
        mcid = experiment.comparisons[0].method.method_config_id
        record = {
            "experiment": experiment.name,
            "run_id": "stamp0:cellA",
            "metric": "revenue",
            "method_name": "t-test",
            "method_params": "{}",
            "method_config_id": mcid,
            "mode": "fpr",
            "iterations": 2000,
            "alpha": 0.05,
            "injected_effect": None,
            "fpr": 0.052,
            "peeking_fpr": 0.13,
            "power": None,
            "achieved_mde": None,
            "coverage": 0.95,
            "effect_exaggeration": None,
            "verdict": "t-test on revenue: well-calibrated, FPR 5.2%",
            "details": '{"single_look_fpr": 0.052, "peeking_fpr": 0.13, '
            '"peeking_curve": [[1.0, 0.05], [14.0, 0.13]], "budget": 0.075, '
            '"recommended": true, "kept_grid_points": 14, "total_grid_points": 14}',
            "status": "success",
            "error_message": "",
        }
        record.update(overrides)
        tables.save_aa_run(record)

    def test_null_until_validate_runs(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        # no _ab_aa_runs table yet -> the chip's empty state
        assert build_report_payload(experiment, tables)["calibration"] is None

    def test_fills_from_persisted_aa_runs(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        tables.ensure_tables()
        self._save_aa_run(tables, experiment)

        cal = build_report_payload(experiment, tables)["calibration"]
        assert cal is not None
        assert cal["fpr"] == 0.052
        assert "nominal α 5.0%" in cal["headline"]
        (row,) = cal["matrix_rows"]
        assert row["method"] == "t-test"
        assert row["over_budget"] is False
        assert row["recommended"] is True
        assert row["peeking_curve"] == [[1.0, 0.05], [14.0, 0.13]]

    def test_empty_table_stays_null(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        tables.ensure_tables()  # table exists but holds no rows
        assert build_report_payload(experiment, tables)["calibration"] is None


class TestSeriesSelection:
    def test_series_grouped_by_configured_method_config_id(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment, days=3)
        # an orphaned series under a different id must not leak into the payload
        orphan_rows = [
            make_row(
                experiment,
                day=d,
                method_config_id="b" * 16,
                method_params='{"test_type":"absolute"}',
            )
            for d in range(1, 6)
        ]
        save_rows(tables, orphan_rows)

        payload = build_report_payload(experiment, tables)
        (pair,) = payload["metrics"][0]["pairs"]
        assert len(pair["series"]) == 3
        # the driver's orphan scan surfaces on the read path: the old series
        # would otherwise be a silently truncated history
        assert any(
            "orphaned method_config_id" in w and "abk clean" in w for w in payload["warnings"]
        )

    def test_no_orphan_warning_on_single_series(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment, days=3)
        payload = build_report_payload(experiment, tables)
        assert not any("orphaned" in w for w in payload["warnings"])

    def test_stored_method_params_win_over_config(self, tables):
        """The method block reflects what actually ran, not the current YAML."""
        experiment = make_experiment()
        seed_series(tables, experiment, days=2, method_params='{"test_type":"absolute"}')
        payload = build_report_payload(experiment, tables)
        assert payload["metrics"][0]["method"]["params"] == {"test_type": "absolute"}

    def test_lww_rewrite_wins_in_payload(self, tables):
        """A re-saved cutoff (same PK, later created_at) shows the new numbers."""
        experiment = make_experiment()
        seed_series(tables, experiment, days=2)
        save_rows(tables, [make_row(experiment, day=2, effect=0.42)])
        payload = build_report_payload(experiment, tables)

        (pair,) = payload["metrics"][0]["pairs"]
        assert len(pair["series"]) == 2  # deduped, not three rows
        assert pair["series"][-1]["e"] == 0.42

    def test_demoted_row_null_passthrough(self, tables):
        experiment = make_experiment()
        rows = [make_row(experiment, day=1)]
        rows.append(
            make_row(
                experiment,
                day=2,
                insufficient_data=True,
                pvalue=None,
                effect=None,
                left_bound=None,
                right_bound=None,
                ci_length=None,
                reject=None,
                mde_1=None,
                mde_2=None,
                size_1=40,
                size_2=38,
            )
        )
        save_rows(tables, rows)
        payload = build_report_payload(experiment, tables)

        demoted = payload["metrics"][0]["pairs"][0]["series"][1]
        assert demoted["ins"] == 1
        assert demoted["e"] is None
        assert demoted["lo"] is None
        assert demoted["hi"] is None
        assert demoted["p"] is None
        assert demoted["rj"] is None
        assert demoted["s1"] == 40
        assert demoted["s2"] == 38

    def test_window_pinning_replays_history(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        payload = build_report_payload(experiment, tables, end=START + timedelta(days=7))

        (pair,) = payload["metrics"][0]["pairs"]
        assert len(pair["series"]) == 7
        assert pair["series"][-1]["t"] == _ms(START + timedelta(days=7))
        assert payload["period"]["end"] == _ms(START + timedelta(days=7))
        # day 7 is pre-horizon: the replayed verdict must withhold WIN
        (verdict,) = payload["verdicts"]
        assert verdict["verdict"] == "INCONCLUSIVE"
        assert verdict["is_horizon"] is False
        assert payload["look"]["n"] == 7

    def test_window_start_bound(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        payload = build_report_payload(experiment, tables, start=START + timedelta(days=3))

        (pair,) = payload["metrics"][0]["pairs"]
        assert len(pair["series"]) == 12  # days 3..14
        assert pair["series"][0]["t"] == _ms(START + timedelta(days=3))
        assert payload["look"]["n"] == 12
        assert payload["period"]["end"] == _ms(START + timedelta(days=14))

    def test_stale_pair_rows_dropped_loudly(self, tables):
        """Rows for pairs outside the declared variants: warned, not charted."""
        experiment = make_experiment()
        seed_series(tables, experiment, days=3)
        stale = [
            make_row(experiment, day=d, name_2="old_arm", end_ts=START + timedelta(days=10 + d))
            for d in range(1, 3)
        ]
        save_rows(tables, stale)
        payload = build_report_payload(experiment, tables)

        (pair,) = payload["metrics"][0]["pairs"]
        assert len(pair["series"]) == 3
        # the stale rows feed NO payload surface: not the chart, not look/period
        assert payload["look"]["n"] == 3
        assert payload["period"]["end"] == _ms(START + timedelta(days=3))
        assert any("outside the declared variants" in w for w in payload["warnings"])

    def test_nan_scrubbed_via_stub(self):
        experiment = make_experiment()
        rows = [
            make_row(
                experiment,
                day=d,
                effect=float("nan"),
                left_bound=float("nan"),
                right_bound=float("nan"),
                pvalue=float("nan"),
            )
            for d in range(1, 3)
        ]
        payload = build_report_payload(experiment, StubTables(rows))

        for point in payload["metrics"][0]["pairs"][0]["series"]:
            assert point["e"] is None
            assert point["lo"] is None
            assert point["hi"] is None
            assert point["p"] is None
        json.dumps(payload, allow_nan=False)  # must not raise


class TestVerdictsAndSrm:
    def test_verdict_block_equals_readout(self, tables):
        experiment = make_experiment()
        rows = seed_series(tables, experiment)
        payload = build_report_payload(experiment, tables)
        readout = evaluate(experiment, rows)

        (expected,) = readout.verdicts
        (verdict,) = payload["verdicts"]
        assert verdict["verdict"] == expected.verdict == "WIN"
        assert verdict["metric"] == "revenue"
        assert verdict["pair"] == {"c": "control", "t": "treatment"}
        assert verdict["rationale"] == list(expected.rationale)
        assert verdict["caveats"] == list(expected.caveats)
        assert verdict["significant"] is True
        assert verdict["effect"] == expected.effect == 0.1
        assert verdict["pvalue"] == expected.pvalue == 0.001
        assert verdict["lo"] == expected.left_bound == 0.05
        assert verdict["hi"] == expected.right_bound == 0.15
        assert verdict["alpha"] == expected.alpha == 0.05
        assert verdict["mde"] == expected.mde == 0.04
        assert verdict["min_effect"] is None and expected.min_effect is None
        assert verdict["elapsed_days"] == expected.elapsed_days == 14.0
        assert verdict["end_ts"] == _ms(expected.end_ts)
        assert verdict["is_horizon"] is True
        assert verdict["guardrails"] == []
        assert payload["warnings"] == list(readout.warnings)

    def test_flat_verdict_carries_min_effect(self, tables):
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "method": {"name": "t-test"},
                    "min_effect": 0.5,
                }
            ]
        )
        # tiny CI around zero, MDE well under min_effect -> FLAT
        seed_series(
            tables,
            experiment,
            effect=0.001,
            left_bound=-0.01,
            right_bound=0.012,
            pvalue=0.8,
            reject=False,
            mde_1=0.05,
            mde_2=0.05,
        )
        payload = build_report_payload(experiment, tables)

        (verdict,) = payload["verdicts"]
        assert verdict["verdict"] == "FLAT"
        assert verdict["min_effect"] == 0.5
        assert verdict["mde"] == 0.05
        assert verdict["significant"] is False

    def test_srm_block(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment, srm_flag=True, srm_pvalue=0.0001, decision_blocked=True)
        payload = build_report_payload(experiment, tables)

        assert payload["srm"]["flag"] is True
        assert payload["srm"]["pvalue"] == 0.0001
        assert payload["srm"]["expected"] == {"control": 0.5, "treatment": 0.5}
        # no exposures seeded: declared variants are zero-filled like the driver
        assert payload["srm"]["observed"] == {"control": 0, "treatment": 0}
        assert payload["metrics"][0]["pairs"][0]["series"][-1]["blk"] == 1

    def seed_exposures(self, tables, experiment):
        units = [
            ("u1", "control", START + timedelta(days=1)),
            ("u2", "control", START + timedelta(days=2)),
            ("u3", "treatment", START + timedelta(days=1)),
            ("u4", "treatment", START + timedelta(days=10)),
        ]
        tables.replace_exposures(
            experiment.name,
            {
                "unit_id": np.array([u[0] for u in units], dtype=object),
                "variant": np.array([u[1] for u in units], dtype=object),
                "exposure_ts": np.array([u[2] for u in units], dtype=object),
            },
        )

    def test_srm_observed_from_exposures(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        self.seed_exposures(tables, experiment)
        payload = build_report_payload(experiment, tables)
        assert payload["srm"]["observed"] == {"control": 2, "treatment": 2}

    def test_srm_observed_whole_cohort_under_replay(self, tables):
        """A pinned end does NOT subset observed: it must stay coherent with
        the whole-run srm flag/pvalue the driver broadcast onto every row."""
        experiment = make_experiment()
        seed_series(tables, experiment)
        self.seed_exposures(tables, experiment)
        payload = build_report_payload(experiment, tables, end=START + timedelta(days=7))
        # u4 (exposed day 10) is outside the day-7 chart window but still in
        # the whole-cohort SRM counts — same cohort the flag/pvalue describe
        assert payload["srm"]["observed"] == {"control": 2, "treatment": 2}

    def test_srm_block_window_independent(self, tables):
        """The srm flag/pvalue come from the latest row OVERALL, not the latest
        charted row — a pinned end that predates a failing cutoff must still
        show the failing gate (§6 SRM loud; window-independent)."""
        experiment = make_experiment()
        # healthy early, fails at the horizon
        rows = [make_row(experiment, day=d) for d in range(1, 14)]
        rows.append(make_row(experiment, day=14, srm_flag=True, srm_pvalue=1e-6))
        save_rows(tables, rows)

        pinned = build_report_payload(experiment, tables, end=START + timedelta(days=7))
        assert pinned["srm"]["flag"] is True
        assert pinned["srm"]["pvalue"] == 1e-6
        assert pinned["period"]["end"] == _ms(START + timedelta(days=7))  # chart is as-of

    def test_srm_loud_on_empty_window(self, tables):
        """An empty pin (no charted rows) must not silence a failing SRM gate:
        observed stays whole-cohort and flag/pvalue come from the latest row
        overall (the critic's silent-SRM hole — §6 must-fix)."""
        experiment = make_experiment()
        rows = [make_row(experiment, day=d, srm_flag=True, srm_pvalue=1e-9) for d in range(10, 15)]
        save_rows(tables, rows)
        self.seed_exposures(tables, experiment)  # 2:2 here, but flag is set
        payload = build_report_payload(experiment, tables, end=START + timedelta(days=5))

        assert payload["period"]["end"] == 0  # no charted rows in the window
        assert payload["metrics"][0]["pairs"][0]["series"] == []
        assert payload["srm"]["flag"] is True  # NOT silenced
        assert payload["srm"]["pvalue"] == 1e-9
        assert payload["srm"]["observed"] == {"control": 2, "treatment": 2}

    def test_missing_exposures_table_zero_fills(self, tables):
        """_ab_results present but _ab_exposures dropped: no crash, zeros."""
        experiment = make_experiment()
        seed_series(tables, experiment)
        tables._manager._rows.pop("_ab_exposures")
        payload = build_report_payload(experiment, tables)
        assert payload["srm"]["observed"] == {"control": 0, "treatment": 0}

    def test_guardrail_entry_in_verdict(self, tables):
        experiment = make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {
                    "metric": "latency",
                    "is_guardrail": True,
                    "method": {"name": "t-test"},
                    "desired_direction": "decrease",
                },
            ]
        )
        seed_series(tables, experiment, metric="revenue")
        seed_series(tables, experiment, metric="latency")
        payload = build_report_payload(experiment, tables)

        (verdict,) = payload["verdicts"]
        (guardrail,) = verdict["guardrails"]
        assert guardrail["metric"] == "latency"
        assert guardrail["pair"] == {"c": "control", "t": "treatment"}
        assert guardrail["regressed"] is True  # significant increase, wanted decrease
        assert guardrail["effect"] == 0.1
        assert guardrail["desired_direction"] == "decrease"
        latency_entry = payload["metrics"][1]
        assert latency_entry["guardrail"] is True
        assert latency_entry["main"] is False


class TestEmptyContract:
    def assert_empty_shape(self, payload, experiment):
        assert set(payload) == TOP_LEVEL_KEYS
        assert payload["period"]["end"] == 0
        assert payload["period"]["start"] == _ms(START)
        assert payload["period"]["horizon"] == _ms(datetime(2026, 1, 15))
        assert payload["look"] == {"n": 0, "planned": 14}
        assert payload["srm"] == {
            "flag": False,
            "pvalue": None,
            "observed": {"control": 0, "treatment": 0},
            "expected": {"control": 0.5, "treatment": 0.5},
        }
        (metric,) = payload["metrics"]
        assert metric["name"] == "revenue"
        assert metric["method"]["id"] == experiment.comparisons[0].method.method_config_id
        assert metric["method"]["alpha"] is None
        assert metric["query"] is None
        (pair,) = metric["pairs"]
        assert pair == {"c": "control", "t": "treatment", "series": [], "diag": None}
        (verdict,) = payload["verdicts"]
        assert verdict["verdict"] == "INCONCLUSIVE"

    def test_never_run_project_missing_tables(self):
        experiment = make_experiment()
        tables = InternalTablesManager(FakeDatabaseManager())  # no ensure_tables()
        payload = build_report_payload(experiment, tables)
        self.assert_empty_shape(payload, experiment)

    def test_tables_exist_but_no_rows(self, tables):
        experiment = make_experiment()
        payload = build_report_payload(experiment, tables)
        self.assert_empty_shape(payload, experiment)


class TestProvenanceAndWarnings:
    def test_rendered_sql_projected_out(self, tables):
        experiment = make_experiment()
        seed_series(tables, experiment)
        payload = build_report_payload(experiment, tables)

        baked = json_dumps_sorted(payload)
        assert "rendered_2026_01_01" not in baked
        assert "created_at" not in baked
        assert payload["metrics"][0]["query"] == "SELECT template"
        assert baked.count("SELECT template") == 1  # deduped to one entry per metric

    def test_row_warnings_and_diagnostics_parsed(self, tables):
        experiment = make_experiment()
        rows = [
            make_row(experiment, day=1, warnings='["theta drifted"]'),
            make_row(experiment, day=2, warnings='["theta drifted"]'),
            make_row(experiment, day=3, diagnostics='{"theta": 0.42}'),
        ]
        save_rows(tables, rows)
        payload = build_report_payload(experiment, tables)

        metric = payload["metrics"][0]
        assert metric["warnings"] == ["theta drifted"]  # deduped, parsed
        assert metric["pairs"][0]["diag"] == {"theta": 0.42}  # latest row, parsed


class TestLookCounter:
    def test_look_at_subday_cadence(self, tables):
        experiment = make_experiment(end_date="2026-01-03", cadence="6h", data_lag=0)
        grid = generate_grid(
            experiment.start_date,
            experiment.end_date,
            experiment.cadence_segments(),
            experiment.timezone,
        )
        rows = [
            make_row(experiment, end_ts=START + timedelta(hours=6 * k), day=0, is_horizon=False)
            for k in range(1, 5)
        ]
        rows.append(
            make_row(
                experiment,
                end_ts=START + timedelta(hours=30),
                day=0,
                is_horizon=False,
                insufficient_data=True,
                pvalue=None,
                effect=None,
                left_bound=None,
                right_bound=None,
                ci_length=None,
                reject=None,
                mde_1=None,
                mde_2=None,
            )
        )
        save_rows(tables, rows)
        payload = build_report_payload(experiment, tables)

        assert payload["look"]["planned"] == len(grid) == 12  # 3 days x 4 looks/day
        assert payload["look"]["n"] == 4  # the demoted 5th cutoff is not a look


class TestOrderingAndBudget:
    def make_multiarm(self):
        return make_experiment(
            assignment={
                "query": "SELECT 1",
                "variants": ["control", "t1", "t2"],
                "expected_split": {"control": 0.34, "t1": 0.33, "t2": 0.33},
            },
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "orders", "method": {"name": "z-test"}},
            ],
        )

    def seed_multiarm(self, tables, experiment, days=5):
        pairs = [("control", "t1"), ("control", "t2"), ("t1", "t2")]
        for metric in ("revenue", "orders"):
            rows = [
                make_row(experiment, metric=metric, day=d, name_1=c, name_2=t)
                for d in range(1, days + 1)
                for c, t in pairs
            ]
            save_rows(tables, rows)

    def test_metrics_and_pairs_ordering_stable(self, tables):
        experiment = self.make_multiarm()
        self.seed_multiarm(tables, experiment)
        payload = build_report_payload(experiment, tables)

        assert [m["name"] for m in payload["metrics"]] == ["revenue", "orders"]
        for metric in payload["metrics"]:
            assert [(p["c"], p["t"]) for p in metric["pairs"]] == [
                ("control", "t1"),
                ("control", "t2"),
                ("t1", "t2"),
            ]
        assert [(v["pair"]["c"], v["pair"]["t"]) for v in payload["verdicts"]] == [
            ("control", "t1"),
            ("control", "t2"),
        ]

    def test_byte_stable_across_builds(self, tables):
        experiment = self.make_multiarm()
        self.seed_multiarm(tables, experiment)
        first = build_report_payload(experiment, tables, generated_at="fixed")
        second = build_report_payload(experiment, tables, generated_at="fixed")
        assert json_dumps_sorted(first) == json_dumps_sorted(second)

    def test_byte_stable_across_managers_and_insert_order(self):
        """Two managers seeded in different insertion order: identical bytes.

        Catches storage-order leaking into payload list ordering — the
        same-manager double-build alone is structurally blind to it.
        """
        experiment = self.make_multiarm()
        pairs = [("control", "t1"), ("control", "t2"), ("t1", "t2")]

        def build_from(metric_order, day_order):
            manager = InternalTablesManager(FakeDatabaseManager())
            manager.ensure_tables()
            for metric in metric_order:
                rows = [
                    make_row(experiment, metric=metric, day=d, name_1=c, name_2=t)
                    for d in day_order
                    for c, t in pairs
                ]
                save_rows(manager, rows)
            return build_report_payload(experiment, manager, generated_at="fixed")

        forward = build_from(["revenue", "orders"], [1, 2, 3])
        shuffled = build_from(["orders", "revenue"], [3, 1, 2])
        assert json_dumps_sorted(forward) == json_dumps_sorted(shuffled)

    def test_point_budget_clips_tail_window_with_warning(self, tables):
        experiment = self.make_multiarm()
        self.seed_multiarm(tables, experiment, days=5)  # 6 series x 5 points = 30
        payload = build_report_payload(experiment, tables, max_points=12)

        for metric in payload["metrics"]:
            for pair in metric["pairs"]:
                assert len(pair["series"]) == 2  # 12 // 6
                # trailing window: the latest cutoffs survive
                assert pair["series"][-1]["t"] == _ms(START + timedelta(days=5))
        assert any("clipped" in w for w in payload["warnings"])
        # the verdict evaluated the FULL series before clipping
        assert payload["verdicts"][0]["end_ts"] == _ms(START + timedelta(days=5))

    def test_budget_not_hit_no_warning(self, tables):
        experiment = self.make_multiarm()
        self.seed_multiarm(tables, experiment)
        payload = build_report_payload(experiment, tables)
        assert not any("clipped" in w for w in payload["warnings"])

    def test_budget_floor_one_point_per_series(self, tables):
        """max_points below the series count: allowed floors at 1, never 0."""
        experiment = self.make_multiarm()
        self.seed_multiarm(tables, experiment, days=5)  # 6 series
        payload = build_report_payload(experiment, tables, max_points=3)

        for metric in payload["metrics"]:
            for pair in metric["pairs"]:
                assert len(pair["series"]) == 1  # max(1, 3 // 6)
                assert pair["series"][0]["t"] == _ms(START + timedelta(days=5))
        assert any("clipped" in w for w in payload["warnings"])


class TestJsonSafety:
    def test_payload_json_serializable_strict(self, tables):
        experiment = make_experiment()
        rows = [make_row(experiment, day=1)]
        rows.append(
            make_row(
                experiment,
                day=2,
                insufficient_data=True,
                pvalue=None,
                effect=None,
                left_bound=None,
                right_bound=None,
                ci_length=None,
                reject=None,
                mde_1=None,
                mde_2=None,
            )
        )
        save_rows(tables, rows)
        payload = build_report_payload(experiment, tables, generated_at="2026-07-03")
        # strict: rejects NaN/inf; TypeError on datetime/numpy leftovers
        round_tripped = json.loads(json.dumps(payload, allow_nan=False))
        assert round_tripped == payload
