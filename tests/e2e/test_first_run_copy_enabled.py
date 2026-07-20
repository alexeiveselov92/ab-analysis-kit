"""m8 WP6 — the copy-enabled e2e legs the cross-mode parity gate cannot cover.

The WP4 gate (``test_cohort_mode_parity.py``) proves single-shot numeric
parity across the two source modes on the scaffolded example — spec step 2(a)
lives THERE, unduplicated. What only a multi-run e2e can prove is the WP5
write discipline over the full CLI path, and that is this file:

1. ``TestScaffoldCopyRerun`` — the literal ``abk init`` example with
   ``cohort_copy.enabled``: the first ``abk run`` persists the cohort through
   the incremental engine, the second is an idempotent watermark resume —
   zero cutoffs planned, zero ``_ab_exposures`` deletes ever (the legacy
   delete+reinsert is unreachable from the driver), byte-stable results.

2. ``TestGrowingSourceIncrement`` — the true increment the single-instant
   scaffold seed cannot express (every scaffold unit is exposed at one
   ``EXPOSURE_TS``, so a rerun's watermark-bucket re-scan legitimately
   re-reads everything): staggered enrollment across three daily buckets,
   run 1 mid-flight persists only the CLOSED buckets, the source then grows,
   run 2 appends exactly the delta — the watermark bucket re-sent as
   idempotent LWW upserts, earlier buckets never re-read — and the
   incrementally-built, two-run history lands ``_ab_results`` identical to a
   fresh direct-mode project computed in one shot at the final ``now``.
   Enrollment is arm-balanced per bucket so the per-run whole-cohort SRM
   stamp is χ²=0 → p=1.0 in BOTH modes regardless of when it ran (the
   documented "SRM counts read the LIVE source" divergence never shows a
   different number here by construction).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from click.testing import CliRunner

import abkit.config.profile as profile_mod
import abkit.pipeline.driver as driver_mod
from abkit.cli.main import cli
from tests.e2e.test_cohort_mode_parity import EXP, _scaffold
from tests.pipeline.test_pipeline import SyntheticWarehouse

runner = CliRunner()

START = datetime(2024, 7, 1)
DAY = timedelta(days=1)
#: run 1 freezes here: buckets [07-01,07-02) and [07-02,07-03) have closed
MID_FLIGHT = START + 2 * DAY
#: run 2 / the direct one-shot: past the horizon, everything computable
FINAL_NOW = datetime(2024, 7, 20)
PER_ARM = 40

#: columns legitimately allowed to differ between modes/runs: the WP4 parity
#: set, plus ``watermark_ts`` — the as-of-run compute-watermark provenance
#: stamp, which NECESSARILY differs between a two-run history (early cutoffs
#: stamped mid-flight) and a one-shot (all stamped at the final now); it is
#: wall-clock provenance like ``created_at``, not a statistical number
VOLATILE = {"created_at", "loaded_at", "run_id", "metric_rendered_query", "watermark_ts"}

PROJECT_YML = """
name: demo
default_profile: dev
"""

PROFILES_YML = """
default_profile: dev
profiles:
  dev:
    type: clickhouse
    port: 9000
    internal_database: abkit_internal
    data_database: analytics
"""

#: cohort_copy needs a live {{ ab_added_filters }} — the engine's batch bounds
#: land there (the WP5 sentinel probe refuses the template without it)
EXPERIMENT_YML = """
name: signup_test
start_date: 2024-07-01
end_date: 2024-07-05
unit_key: user_id
assignment:
  query: "SELECT user_id, variant, exposure_ts FROM assignments WHERE 1 = 1 {{ ab_added_filters }}"
  variants: [control, treatment]
  expected_split: {control: 0.5, treatment: 0.5}
#COHORT_COPY#comparisons:
  - metric: arpu
    is_main_metric: true
    method: {name: t-test, params: {test_type: relative}}
"""

METRIC_YML = """
name: arpu
type: sample
columns:
  variant: variant
  value: gross_usd
query: |
  {% import 'abkit_assignment.jinja' as ab %}
  SELECT {{ ab.variant_col() }} AS variant, user_id, sum(gross_usd) AS gross_usd
  FROM {{ data_database }}.user_revenue {{ ab.exposed_units() }}
  GROUP BY variant, user_id
"""


def _spy_writes(warehouse) -> tuple[list[tuple], list[tuple[str, set[str]]]]:
    """Record every delete and every insert's (table, unit_ids) on *warehouse*."""
    deletes: list[tuple] = []
    inserts: list[tuple[str, set[str]]] = []
    original_delete, original_insert = warehouse.delete_rows, warehouse.insert_batch

    def spy_delete(*args, **kwargs):
        deletes.append(args)
        return original_delete(*args, **kwargs)

    def spy_insert(table_name, data, conflict_strategy="ignore"):
        units = {str(u) for u in data["unit_id"]} if "unit_id" in data else set()
        inserts.append((table_name, units))
        return original_insert(table_name, data, conflict_strategy)

    warehouse.delete_rows = spy_delete
    warehouse.insert_batch = spy_insert
    return deletes, inserts


def _exposure_deletes(deletes: list[tuple]) -> list[tuple]:
    return [args for args in deletes if "_ab_exposures" in str(args[0])]


def _units_sent(inserts: list[tuple[str, set[str]]]) -> set[str]:
    return set().union(*(units for table, units in inserts if "_ab_exposures" in table), set())


class TestScaffoldCopyRerun:
    """The scaffolded example, cohort_copy on: engine-only writes, resumable."""

    def test_rerun_is_an_append_only_watermark_resume(self, tmp_path, monkeypatch):
        warehouse = _scaffold(tmp_path, monkeypatch, "demo_copy", copy_enabled=True)
        deletes, inserts = _spy_writes(warehouse)

        first = runner.invoke(cli, ["run", "--select", EXP])
        assert first.exit_code == 0, first.output
        assert "cohort copy trails" not in first.output
        # the engine's write path ran; the persisted cohort is the deduped seed
        assert _units_sent(inserts) == {f"user_{i}" for i in range(600)}
        assert len(warehouse._rows["_ab_exposures"]) == 600
        results_after_first = [dict(r) for r in warehouse._rows["_ab_results"]]
        assert len(results_after_first) == 28  # 14 cutoffs × 2 metrics

        second = runner.invoke(cli, ["run", "--select", EXP])
        assert second.exit_code == 0, second.output
        assert "cutoffs planned: 0" in second.output
        assert "cohort copy trails" not in second.output
        # append-only, ever: the rerun resumed from the exposure_ts watermark —
        # the legacy delete+reinsert is unreachable from the driver (m8 WP5)
        assert _exposure_deletes(deletes) == []
        # the watermark bucket's re-sent units collapse as LWW upserts
        assert len(warehouse._rows["_ab_exposures"]) == 600
        assert warehouse._rows["_ab_results"] == results_after_first


def _enroll(warehouse: SyntheticWarehouse, day: int) -> set[str]:
    """One arm-balanced daily enrollment bucket + one post-exposure event each."""
    exposure_ts = START + day * DAY + timedelta(hours=8)
    units = set()
    for i in range(PER_ARM):
        for arm, base in (("control", 10.0), ("treatment", 10.4)):
            unit = f"d{day}_{arm[0]}{i:02d}"
            warehouse.cohort.append((unit, arm, exposure_ts))
            warehouse.events.append(
                (unit, arm, exposure_ts + timedelta(hours=4), base + (i % 7) * 0.5)
            )
            units.add(unit)
    return units


def _cli_project(tmp_path: Path, name: str, *, copy_enabled: bool) -> Path:
    root = tmp_path / name
    (root / "experiments").mkdir(parents=True)
    (root / "metrics").mkdir()
    (root / "abkit_project.yml").write_text(PROJECT_YML)
    (root / "profiles.yml").write_text(PROFILES_YML)
    cohort_copy_line = "  cohort_copy: {enabled: true}\n" if copy_enabled else ""
    (root / "experiments" / "signup_test.yml").write_text(
        EXPERIMENT_YML.replace("#COHORT_COPY#", cohort_copy_line)
    )
    (root / "metrics" / "arpu.yml").write_text(METRIC_YML)
    return root


def _comparable(rows: list[dict]) -> list[dict]:
    stripped = [{k: v for k, v in r.items() if k not in VOLATILE} for r in rows]
    return sorted(stripped, key=lambda r: repr(sorted(r.items(), key=lambda kv: kv[0])))


class TestGrowingSourceIncrement:
    """Staggered enrollment, growing source: two runs append, never rewrite."""

    def test_two_run_increment_matches_the_direct_one_shot(self, tmp_path, monkeypatch):
        copy_wh = SyntheticWarehouse()
        day0 = _enroll(copy_wh, 0)
        day1 = _enroll(copy_wh, 1)
        monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: copy_wh)
        monkeypatch.chdir(_cli_project(tmp_path, "demo_copy", copy_enabled=True))
        deletes, inserts = _spy_writes(copy_wh)

        # ── run 1, mid-flight: only the two CLOSED daily buckets persist ────
        monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: MID_FLIGHT)
        first = runner.invoke(cli, ["run", "--select", "signup_test"])
        assert first.exit_code == 0, first.output
        assert "cohort copy trails" not in first.output
        assert {r["unit_id"] for r in copy_wh._rows["_ab_exposures"]} == day0 | day1
        # cutoffs computable at MID_FLIGHT: end_ts 07-02 and 07-03
        assert len(copy_wh._rows["_ab_results"]) == 2

        # ── the source grows AFTER run 1, then run 2 appends the delta ─────
        day2 = _enroll(copy_wh, 2)
        inserts.clear()
        monkeypatch.setattr(driver_mod, "now_utc_naive", lambda: FINAL_NOW)
        second = runner.invoke(cli, ["run", "--select", "signup_test"])
        assert second.exit_code == 0, second.output
        assert "cohort copy trails" not in second.output
        # exactly the delta: the watermark bucket (day 1) re-sent as
        # idempotent LWW upserts + the new day-2 bucket; day 0 NEVER re-read
        assert _units_sent(inserts) == day1 | day2
        assert _exposure_deletes(deletes) == []
        # LWW: still exactly one persisted row per unit
        assert len(copy_wh._rows["_ab_exposures"]) == len(day0 | day1 | day2)
        # the remaining cutoffs (07-04, 07-05, 07-06) filled in
        assert len(copy_wh._rows["_ab_results"]) == 5

        # ── the gate: a fresh direct-mode project, one shot at FINAL_NOW ────
        direct_wh = SyntheticWarehouse()
        for day in (0, 1, 2):
            _enroll(direct_wh, day)
        monkeypatch.setattr(profile_mod.ProfileConfig, "create_manager", lambda self: direct_wh)
        monkeypatch.chdir(_cli_project(tmp_path, "demo_direct", copy_enabled=False))
        result = runner.invoke(cli, ["run", "--select", "signup_test"])
        assert result.exit_code == 0, result.output
        assert direct_wh._rows.get("_ab_exposures", []) == []

        assert _comparable(copy_wh._rows["_ab_results"]) == _comparable(
            direct_wh._rows["_ab_results"]
        )

    def test_growing_source_keeps_the_cohort_arm_balanced(self):
        """Fixture self-check: every bucket adds PER_ARM units per arm, so the
        whole-cohort SRM is χ²=0 at every stage — the one construction that
        makes the run-1-stamped SRM p-value mode-invariant (module docstring)."""
        wh = SyntheticWarehouse()
        for day in (0, 1, 2):
            _enroll(wh, day)
        by_arm: dict[str, int] = {}
        for _, arm, _ in wh.cohort:
            by_arm[arm] = by_arm.get(arm, 0) + 1
        assert by_arm == {"control": 3 * PER_ARM, "treatment": 3 * PER_ARM}
