"""The M2 definition-of-done gate: ``abk init && abk run --select example``.

Machine-independent variant: the warehouse is an in-memory mirror of the
scaffolded seed dataset's GENERATION RULE (numbers()-based, deterministic), so
the exact SQL the scaffold ships is rendered, window-filtered and aggregated
like a real backend — no Docker required. The real-ClickHouse variant lives in
``test_first_run_clickhouse.py`` (skipped without Docker).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from fake_db import FakeDatabaseManager

import abkit.config.profile as profile_mod
from abkit.cli.main import cli

USERS = 600
EXP_START = date(2024, 7, 1)
PRE_START = date(2024, 6, 17)
DAYS = 14
EXPOSURE_TS = datetime(2024, 7, 1, 8, 0, 0)

_WINDOW_RE = re.compile(r"event_time >= '([^']+)' AND event_time < '([^']+)'")


def _variant(user_idx: int) -> str:
    return "control" if user_idx % 2 == 0 else "treatment"


def _experiment_rows():
    """Mirror of the seed SQL's experiment-period INSERT (numbers(600*14))."""
    for number in range(USERS * DAYS):
        user_idx = number % USERS
        day_idx = number // USERS
        event_time = datetime.combine(
            EXP_START + timedelta(days=day_idx), datetime.min.time()
        ) + timedelta(hours=12)
        k = user_idx // 2
        converts = (k % 5 == 0) if user_idx % 2 == 0 else (k % 4 == 0)
        signed_up = 1 if converts and day_idx == k % 14 else 0
        gross = (user_idx % 7) * 1.5 * (1.0 if user_idx % 2 == 0 else 1.15)
        yield user_idx, event_time, signed_up, gross


def _preperiod_rows():
    """Mirror of the pre-period INSERT (no lift, no signups, by construction)."""
    for number in range(USERS * DAYS):
        user_idx = number % USERS
        day_idx = number // USERS
        event_time = datetime.combine(
            PRE_START + timedelta(days=day_idx), datetime.min.time()
        ) + timedelta(hours=12)
        yield user_idx, event_time, 0, (user_idx % 7) * 1.4


class SeedMirrorWarehouse(FakeDatabaseManager):
    """Serves the scaffolded assignment/metric SQL from the seed generation rule."""

    def execute_query(self, query, params=None):
        flat = " ".join(query.split())
        if "example_ab_assignments" in flat:
            return [
                {
                    "user_id": f"user_{i}",
                    "variant": _variant(i),
                    "exposure_ts": EXPOSURE_TS,
                }
                for i in range(USERS)
            ]
        if "example_signup_events" in flat:
            match = _WINDOW_RE.search(flat)
            assert match, f"scaffolded metric SQL lost its window filter: {flat}"
            w_start = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            w_end = datetime.strptime(match.group(2), "%Y-%m-%d %H:%M:%S")
            exposure_filter = "exposure_ts" in flat.split("WHERE experiment")[-1]
            wants_signups = "signed_up" in flat

            per_unit: dict[int, dict[str, float]] = {}
            for user_idx, event_time, signed_up, gross in (
                *_experiment_rows(),
                *_preperiod_rows(),
            ):
                if not (w_start <= event_time < w_end):
                    continue
                if exposure_filter and event_time < EXPOSURE_TS:
                    continue
                acc = per_unit.setdefault(user_idx, {"signed_up": 0, "gross_usd": 0.0})
                acc["signed_up"] = max(acc["signed_up"], signed_up)
                acc["gross_usd"] += gross

            rows = []
            for user_idx in sorted(per_unit):
                row = {"variant": _variant(user_idx), "user_id": f"user_{user_idx}"}
                if wants_signups:
                    row["signed_up"] = per_unit[user_idx]["signed_up"]
                    row["visits"] = 1
                else:
                    row["gross_usd"] = per_unit[user_idx]["gross_usd"]
                rows.append(row)
            return rows
        return super().execute_query(query, params)


runner = CliRunner()


@pytest.fixture
def scaffolded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["init", "demo"])
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(tmp_path / "demo")
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


class TestInitScaffold:
    def test_files_created_and_self_validated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["init", "demo"])
        assert result.exit_code == 0, result.output
        for rel in (
            "abkit_project.yml",
            "profiles.yml",
            "README.md",
            "experiments/example_signup_test.yml",
            "metrics/example_signup_cr.yml",
            "metrics/example_arpu.yml",
            "sql/example_assignment.sql",
            "seed/seed_dataset.clickhouse.sql",
            "runners/prefect_flow.py",
        ):
            assert (tmp_path / "demo" / rel).exists(), rel

    def test_refuses_existing_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "demo").mkdir()
        result = runner.invoke(cli, ["init", "demo"])
        assert result.exit_code != 0
        assert "refuses to overwrite" in result.output

    def test_db_type_variants_validate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        for db_type in ("postgres", "mysql"):
            result = runner.invoke(cli, ["init", f"demo_{db_type}", "--db-type", db_type])
            assert result.exit_code == 0, result.output

    def test_scaffold_passes_its_own_lint(self, scaffolded):
        result = runner.invoke(cli, ["run", "--steps", "validate"])
        assert result.exit_code == 0, result.output
        assert "config valid" in result.output


class TestFirstRun:
    def test_init_and_run_produce_real_results(self, scaffolded):
        result = runner.invoke(cli, ["run", "--select", "example_signup_test"])
        assert result.exit_code == 0, result.output

        rows = scaffolded._rows["_ab_results"]
        # 14 daily cutoffs × 2 metrics × 1 pair
        assert len(rows) == 28
        by_metric = {}
        for row in rows:
            by_metric.setdefault(row["metric"], []).append(row)

        signup = sorted(by_metric["example_signup_cr"], key=lambda r: r["end_ts"])
        assert signup[-1]["method_name"] == "z-test"
        assert signup[-1]["is_main_metric"] is True
        assert signup[-1]["mde_1"] is not None  # calculate_mde: true
        assert signup[-1]["effect"] > 0  # treatment converts more by construction
        assert signup[-1]["is_horizon"] is True
        assert signup[-1]["srm_flag"] is False

        arpu = sorted(by_metric["example_arpu"], key=lambda r: r["end_ts"])
        assert arpu[-1]["method_name"] == "cuped-t-test"
        assert arpu[-1]["cov_value_1"] is not None  # the pre-period covariate
        assert 0.10 < arpu[-1]["effect"] < 0.20  # the seeded ~15% lift
        assert arpu[-1]["pvalue"] < 0.05

    def test_rerun_is_idempotent(self, scaffolded):
        assert runner.invoke(cli, ["run", "--select", "example_signup_test"]).exit_code == 0
        result = runner.invoke(cli, ["run", "--select", "example_signup_test"])
        assert result.exit_code == 0
        assert "cutoffs planned: 0" in result.output
        assert len(scaffolded._rows["_ab_results"]) == 28

    def test_unlock_and_clean_smoke(self, scaffolded):
        assert runner.invoke(cli, ["run"]).exit_code == 0
        assert runner.invoke(cli, ["unlock"]).exit_code == 0
        clean = runner.invoke(cli, ["clean"])
        assert clean.exit_code == 0
        assert "no orphaned series" in clean.output


def test_seed_mirror_matches_seed_sql_shape():
    """The mirror must stay in lockstep with the shipped seed SQL constants."""
    seed_sql = (Path(__file__).parents[2] / "abkit" / "cli" / "commands" / "init.py").read_text()
    assert "numbers(600)" in seed_sql
    assert "numbers(600 * 14)" in seed_sql
    assert "toDate('2024-07-01')" in seed_sql
    assert "toDate('2024-06-17')" in seed_sql
    assert "% 5 = 0" in seed_sql and "% 4 = 0" in seed_sql
    assert "1.15" in seed_sql and "* 1.4" in seed_sql
