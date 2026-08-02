"""The M9 exit gate: the additive read path, end to end over ``abk init``
(m9-implementation-plan.md WP6, §7).

Everything below is driven through the real CLI against the in-memory seed
mirror (no Docker), over the project the scaffold actually ships — which is
exactly the interesting fixture, because it contains **both** shapes: the
declared-additive ``example_arpu`` (``state_additive: true``, CUPED) and the
deliberately non-additive ``example_signup_cr`` (``max()`` + a literal trial
count), which must never materialize day state at all.

The four legs, in the order the plan states them:

1. ``abk run`` twice with ``compute.incremental_reads: true`` — the second
   run plans nothing and changes not one persisted byte; a ``--full-refresh``
   re-run through the same path reproduces every number exactly.
2. ``abk verify-incremental`` reconciles the WHOLE series green at rel-1e-9,
   with the eligible metric genuinely *verified* (not silently "unverified"
   because the read fell back).
3. ``abk explore`` serves ``cuped-t-test`` on Tier E for every knob except
   ``covariate_lookback`` (M9 WP2), on a project whose numbers came from the
   incremental path.
4. **The milestone's single most important assertion** — flipping
   ``incremental_reads`` off reproduces every persisted ``_ab_results``
   number: the flag changes HOW a number is computed, never the number.
   Sizes/flags/identity exactly, continuous columns at the §0.1 rel-1e-9
   tolerance (partial-day sums add in a different ORDER than one full-window
   scan, so byte equality is the wrong assertion — the M7 GEMM lesson).
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

import abkit.config.profile as profile_mod
from abkit.cli.main import cli
from tests._helpers.scaffold import set_incremental_reads
from tests.e2e.test_first_run import _WINDOW_RE, SeedMirrorWarehouse

runner = CliRunner()

EXP = "example_signup_test"
#: the scaffolded metric that DECLARES `state_additive: true`
STATE_METRIC = "example_arpu"
#: the scaffolded metric that must never materialize (max() + `1 AS visits`)
RECOMPUTE_METRIC = "example_signup_cr"

#: columns that legitimately differ between two runs of the same numbers
VOLATILE = {"created_at", "loaded_at", "run_id", "watermark_ts", "metric_rendered_query"}

#: the row identity (the `_ab_results` primary key, tables.py)
_KEY = ("experiment", "metric", "name_1", "name_2", "method_config_id", "end_ts")


def _scaffold(tmp_path, monkeypatch, name: str, incremental: bool) -> SeedMirrorWarehouse:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli, ["init", name]).exit_code == 0
    monkeypatch.chdir(tmp_path / name)
    # ALWAYS written, never only for one leg: PERF-1 made `true` the scaffold's
    # own default, so a leg that stayed silent would agree with the other one
    # and leg 4 below would compare the incremental path against itself.
    set_incremental_reads(Path("abkit_project.yml"), incremental)
    warehouse = SeedMirrorWarehouse()
    monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
    import abkit.pipeline.driver as driver_mod

    monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
    return warehouse


@pytest.fixture
def incremental_project(tmp_path, monkeypatch) -> SeedMirrorWarehouse:
    warehouse = _scaffold(tmp_path, monkeypatch, "demo_incremental", incremental=True)
    result = runner.invoke(cli, ["run", "--select", EXP])
    assert result.exit_code == 0, result.output
    return warehouse


def _count_additive_reads(monkeypatch) -> list[tuple]:
    """Record every `IncrementalBackend.load_cutoff` the run makes.

    The only observation that distinguishes the two read paths from outside:
    the resolved flag, the persisted numbers and `verify-incremental` (which
    builds its own backends) are all identical when the driver stops routing
    to the additive reader at all.
    """
    import abkit.compute.incremental_backend as incremental_mod

    reads: list[tuple] = []
    original = incremental_mod.IncrementalBackend.load_cutoff

    def counted(self, *args, **kwargs):
        reads.append((args, tuple(sorted(kwargs))))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(incremental_mod.IncrementalBackend, "load_cutoff", counted)
    return reads


def _keyed(rows: list[dict]) -> dict[tuple, dict]:
    """Index persisted rows by identity — order must never enter the diff."""
    indexed: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(str(row[column]) for column in _KEY)
        assert key not in indexed, f"duplicate persisted row for {key}"
        indexed[key] = {k: v for k, v in row.items() if k not in VOLATILE}
    return indexed


def _parsed_json(value: str):
    """The payload columns (``diagnostics``, ``method_params``, ``warnings``)
    are JSON STRINGS, so a θ differing in its last ULP shows up as a differing
    string. Parse them so the comparison stays about numbers."""
    if not value.startswith(("{", "[")):
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _same(value, expected, *, exact: bool) -> bool:
    if isinstance(value, float) and isinstance(expected, float):
        if math.isnan(value) and math.isnan(expected):
            return True
        if exact:
            return value == expected
        return math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(value, dict) and isinstance(expected, dict):
        return value.keys() == expected.keys() and all(
            _same(value[k], expected[k], exact=exact) for k in value
        )
    if isinstance(value, list) and isinstance(expected, list):
        return len(value) == len(expected) and all(
            _same(a, b, exact=exact) for a, b in zip(value, expected, strict=True)
        )
    if not exact and isinstance(value, str) and isinstance(expected, str) and value != expected:
        left, right = _parsed_json(value), _parsed_json(expected)
        if left is None or right is None:
            return False
        return _same(left, right, exact=exact)
    return bool(value == expected)


def _assert_numbers_match(left: list[dict], right: list[dict], *, what: str, exact: bool) -> None:
    """Compare two ``_ab_results`` sets field by field.

    ``exact`` is for two passes of the SAME code path (deterministic ⇒ byte
    equality is a legitimate assertion); across the flag it relaxes to the
    §0.1 tolerance on continuous values only — everything discrete (sizes,
    flags, verdict booleans, identity strings, warning texts) stays exact in
    BOTH modes, which is what makes a tolerant comparison honest rather than
    lax.
    """
    a, b = _keyed(left), _keyed(right)
    assert a, f"{what}: nothing was persisted — the comparison would be vacuous"
    assert a.keys() == b.keys(), f"{what}: different row identities persisted"
    for key, row in a.items():
        other = b[key]
        assert row.keys() == other.keys(), f"{what}: column sets differ for {key}"
        for column, value in row.items():
            expected = other[column]
            assert _same(
                value, expected, exact=exact
            ), f"{what}: {column} diverged at {key}: {value!r} vs {expected!r}"


class TestTwiceRun:
    """Leg 1 — running twice with the flag on is a no-op, and re-computing
    the whole series through the incremental path reproduces itself."""

    def test_second_run_plans_nothing_and_persists_identical_rows(self, incremental_project):
        before = [dict(row) for row in incremental_project._rows["_ab_results"]]
        state_before = [dict(row) for row in incremental_project._rows["_ab_unit_state"]]

        result = runner.invoke(cli, ["run", "--select", EXP])
        assert result.exit_code == 0, result.output
        assert "cutoffs planned: 0" in result.output

        _assert_numbers_match(
            incremental_project._rows["_ab_results"], before, what="second run", exact=True
        )
        assert len(incremental_project._rows["_ab_unit_state"]) == len(state_before)

    def test_full_refresh_recomputes_to_the_same_numbers(self, incremental_project):
        before = [dict(row) for row in incremental_project._rows["_ab_results"]]

        # --full-refresh re-opens every computed cutoff; the `state` step is in
        # the default step list, so day state is re-rendered and the read path
        # stays incremental (WP4 only disables it when `state` is NOT run).
        result = runner.invoke(
            cli,
            # the scaffolded horizon: start_ts 2024-07-01, horizon_ts 2024-07-15
            # (the last cutoff covers that day, so the exclusive edge is the 15th)
            [
                "run",
                "--select",
                EXP,
                "--full-refresh",
                "--from",
                "2024-07-01",
                "--to",
                "2024-07-15",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "incremental reads disabled for this run" not in result.output

        _assert_numbers_match(
            incremental_project._rows["_ab_results"], before, what="full refresh", exact=True
        )


class TestStateMaterialization:
    """Only the metric that signed the additivity contract gets day state."""

    def test_declared_metric_materializes_and_the_others_never_do(self, incremental_project):
        state_rows = incremental_project._rows.get("_ab_unit_state", [])
        assert state_rows, "the declared-additive metric must materialize day state"
        sources = {row["source_table"] for row in state_rows}
        assert sources == {f"{EXP}/{STATE_METRIC}"}
        assert all(RECOMPUTE_METRIC not in source for source in sources)
        # one row per (unit, day) — the linear write the perf claim rests on:
        # 600 seeded units × the 14 closed days of the scaffolded horizon
        assert len({row["day"] for row in state_rows}) == 14
        assert len(state_rows) == 600 * 14


class TestWholeSeriesReconciliation:
    """Leg 2 — ``abk verify-incremental`` over the whole series."""

    def test_green_and_actually_verified(self, incremental_project):
        result = runner.invoke(cli, ["verify-incremental", "--select", EXP])
        assert result.exit_code == 0, result.output
        assert "matched at rel_tol=1e-09" in result.output
        assert "DIVERGED" not in result.output
        # a green report that verified nothing is the failure mode this
        # command exists to avoid (WP5): the eligible metric must be checked,
        # and no cutoff may have fallen back
        assert "unverified:" not in result.output
        assert "cutoffs checked: 14" in result.output
        # the non-additive metric is SKIPPED with a reason, never silently
        # counted as verified
        assert f"skipped {RECOMPUTE_METRIC}" in result.output

    def test_metric_filter_verifies_the_state_series(self, incremental_project):
        result = runner.invoke(
            cli, ["verify-incremental", "--select", EXP, "--metric", STATE_METRIC]
        )
        assert result.exit_code == 0, result.output
        assert "cutoffs checked: 14" in result.output


class _BackfillingWarehouse(SeedMirrorWarehouse):
    """The seed mirror plus ONE late event landing in an already-closed day.

    This is the milestone's documented limitation made concrete (m9 WP4, the
    m8 copy-mode precedent): an event that arrives later than `data_lag`
    freezes out of the day state, so the incremental read keeps serving the
    pre-backfill number while recompute picks it up. `verify-incremental` is
    the detector and `--full-refresh` the recovery — both asserted below
    through the CLI's exit code, which is what an operator's CI would use.
    """

    #: switched on AFTER the run that materialized the day
    apply_backfill = False
    backfill_day = datetime(2024, 7, 3)
    backfill_user = "user_0"
    backfill_amount = 500.0

    def execute_query(self, query, params=None):
        rows = super().execute_query(query, params)
        if not self.apply_backfill:
            return rows
        flat = " ".join(query.split())
        if "example_signup_events" not in flat or "gross_usd" not in flat:
            return rows
        match = _WINDOW_RE.search(flat)
        if not match:
            return rows
        w_start = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        w_end = datetime.strptime(match.group(2), "%Y-%m-%d %H:%M:%S")
        if not (w_start <= self.backfill_day < w_end):
            return rows
        for row in rows:
            if row.get("user_id") == self.backfill_user:
                row["gross_usd"] += self.backfill_amount
        return rows


class TestDriftIsCaughtAndHealed:
    """The exit-gate half of WP5's promise: the reconciliation command really
    exits NON-ZERO when the two paths disagree, and the documented recovery
    really restores agreement."""

    def test_backfill_diverges_then_full_refresh_heals(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert runner.invoke(cli, ["init", "demo_drift"]).exit_code == 0
        monkeypatch.chdir(tmp_path / "demo_drift")
        set_incremental_reads(Path("abkit_project.yml"), True)
        warehouse = _BackfillingWarehouse()
        monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: warehouse)
        import abkit.pipeline.driver as driver_mod

        monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: datetime(2024, 8, 1))
        assert runner.invoke(cli, ["run", "--select", EXP]).exit_code == 0
        assert runner.invoke(cli, ["verify-incremental", "--select", EXP]).exit_code == 0

        # the late event arrives into a day that is already materialized
        warehouse.apply_backfill = True

        drifted = runner.invoke(cli, ["verify-incremental", "--select", EXP])
        assert drifted.exit_code == 1, drifted.output
        assert "DIVERGED" in drifted.output

        healed_run = runner.invoke(
            cli,
            [
                "run",
                "--select",
                EXP,
                "--full-refresh",
                "--from",
                "2024-07-01",
                "--to",
                "2024-07-15",
            ],
        )
        assert healed_run.exit_code == 0, healed_run.output
        healed = runner.invoke(cli, ["verify-incremental", "--select", EXP])
        assert healed.exit_code == 0, healed.output
        assert "DIVERGED" not in healed.output


class TestCupedTierE:
    """Leg 3 — the cockpit half (M9 WP2) over an incrementally-computed
    project: every CUPED knob recomputes exactly, except the pre-period."""

    def test_alpha_and_param_knobs_are_exact_the_lookback_is_reload(self, incremental_project):
        from tests.e2e.test_explore_session import Served, http

        served = Served(incremental_project, with_cache=False)
        try:
            surface = served.payload["explore"]["metrics"][STATE_METRIC]
            configured = surface["configured"]
            assert configured["method"] == "cuped-t-test"

            # an alpha edit: Tier E (exact), not the α-inversion approximation
            status, reply = http(
                served.endpoint("recompute"),
                {
                    "metric": STATE_METRIC,
                    "method": {"name": "cuped-t-test", "params": configured["params"]},
                    "alpha": 0.01,
                    "request_id": 1,
                },
            )
            assert status == 200, reply
            assert all(
                point["tier"] == "exact" for point in reply["pairs"][0]["points"]
            ), "a CUPED alpha edit must be Tier E on every cutoff"

            # a method-param edit (test_type) is Tier E too
            params = dict(configured["params"])
            params["test_type"] = "absolute"
            status, absolute = http(
                served.endpoint("recompute"),
                {
                    "metric": STATE_METRIC,
                    "method": {"name": "cuped-t-test", "params": params},
                    "alpha": configured["alpha"],
                    "request_id": 2,
                },
            )
            assert status == 200, absolute
            assert all(point["tier"] == "exact" for point in absolute["pairs"][0]["points"])

            # …and the one exception, declared as such on the knob surface
            method_surface = next(m for m in surface["methods"] if m["name"] == "cuped-t-test")
            assert method_surface["alpha_tier"] == "E"
            assert method_surface["correction_tier"] == "E"
            tiers = method_surface["tiers"]
            assert tiers["covariate_lookback"] == "R"
            assert set(tiers.values()) == {"E", "R"}
            assert [knob for knob, tier in tiers.items() if tier != "E"] == ["covariate_lookback"]
        finally:
            served.stop()


class TestFlagOffChangesNoNumber:
    """Leg 4 — THE milestone assertion (§0.1, §7): the same experiment,
    computed both ways, persists the same numbers."""

    def test_results_identical_across_the_flag(self, tmp_path, monkeypatch):
        persisted: dict[str, list[dict]] = {}
        for mode, incremental in (("incremental", True), ("recompute", False)):
            warehouse = _scaffold(tmp_path, monkeypatch, f"demo_{mode}", incremental)
            # PERF-1: count REAL additive reads, so each leg proves the path it
            # is named for. Asserting on the run's own reporting would only
            # prove the resolved FLAG — with the driver's routing removed, this
            # file stayed 9/9 green. Same idiom (and same trap) as
            # test_sub_day_anchors_and_explore's whole-series leg.
            reads = _count_additive_reads(monkeypatch)
            result = runner.invoke(cli, ["run", "--select", EXP, "--cost-report"])
            assert result.exit_code == 0, result.output
            if incremental:
                # one additive read per cutoff of the one state-eligible metric
                assert len(reads) == 14, len(reads)
                assert "fell back" not in result.output.lower(), result.output
            else:
                assert reads == [], "the recompute leg must never touch the additive reader"
            persisted[mode] = [dict(row) for row in warehouse._rows["_ab_results"]]
            state_rows = warehouse._rows.get("_ab_unit_state", [])
            if incremental:
                assert state_rows, "the incremental leg must have read day state"
            # the STATE stage writes in both modes (it is a write-only stage,
            # gated by the metric's declaration, not by the read flag)
            assert state_rows, "the STATE stage is independent of the read flag"

        assert len(persisted["incremental"]) == 28  # 14 cutoffs × 2 metrics
        _assert_numbers_match(
            persisted["incremental"],
            persisted["recompute"],
            what="incremental vs recompute",
            exact=False,
        )

    def test_the_incremental_leg_really_used_state(self, incremental_project):
        """Guard against a vacuous leg-4: if the read path had quietly fallen
        back to recompute for every cutoff, the numbers would trivially match
        and the assertion above would prove nothing."""
        result = runner.invoke(cli, ["verify-incremental", "--select", EXP])
        assert result.exit_code == 0, result.output
        assert "unverified:" not in result.output
        assert "pair comparisons: 14 matched" in result.output
