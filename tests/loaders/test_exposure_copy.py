"""WP5 incremental cohort copy tests (m8-implementation-plan.md WP5).

Covers the ``exposure_copy`` engine directly: the first-run backfill from the
experiment start, watermark resume, the closed-interval discipline (the still-
open bucket and the ``maturity_delay`` window are withheld), round-trip-count
invariance, the ``{{ ab_added_filters }}`` fail-fast guard, the custom
``update_column`` path, and — pinned, not fixed — the two DISCLOSED donor
limitations: a late-arriving row below the watermark is permanently missed,
and a malformed cross-batch duplicate resolves to the LATER batch's
``exposure_ts`` (LWW) instead of the full reload's global earliest.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fake_db import FakeDatabaseManager, serve_assignment_pushdown

from abkit.config import ExperimentConfig
from abkit.core.period_planner import generate_grid
from abkit.database.internal_tables import InternalTablesManager
from abkit.loaders.exposure_copy import _batch_added_filters, copy_exposures_incremental
from abkit.loaders.exposure_source import ExposureLoadError

START = datetime(2024, 7, 1)
#: default "now": 10 full days into the experiment, mid-bucket (12:00)
NOW = datetime(2024, 7, 11, 12, 0, 0)

COPY_QUERY = (
    "SELECT user_id, variant, exposure_ts FROM assignments WHERE 1 = 1 {{ ab_added_filters }}"
)


class ScriptedAssignmentManager(FakeDatabaseManager):
    """Scripted assignment rows through the shared pushdown evaluator.

    ``serve_assignment_pushdown`` applies the WP5 batch bounds (injected via
    ``{{ ab_added_filters }}``) with real-backend semantics before the
    ``GROUP BY`` aggregation, so windowing is exercised for real here.
    """

    def __init__(self):
        super().__init__()
        self.scripted_rows: list[dict] = []
        self.assignment_queries: list[str] = []

    def execute_query(self, query, params=None):
        normalized = " ".join(query.split())
        if "assignments" not in normalized:
            return super().execute_query(query, params)
        self.assignment_queries.append(normalized)
        return serve_assignment_pushdown(self._project, normalized, self.scripted_rows)


def make_experiment(**overrides) -> ExperimentConfig:
    assignment = {
        "query": COPY_QUERY,
        "variants": ["control", "treatment"],
        "expected_split": {"control": 0.5, "treatment": 0.5},
        "cohort_copy": {"enabled": True},
    }
    assignment.update(overrides.pop("assignment", {}))
    payload = {
        "name": "copy_test",
        "start_date": "2024-07-01",
        "end_date": "2024-07-28",
        "unit_key": "user_id",
        "assignment": assignment,
        "comparisons": [
            {
                "metric": "arpu",
                "is_main_metric": True,
                "method": {"name": "t-test", "params": {"test_type": "relative"}},
            }
        ],
    }
    payload.update(overrides)
    return ExperimentConfig.model_validate(payload)


def make_grid(experiment: ExperimentConfig):
    return generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        tz=experiment.timezone,
    )


def row(unit, variant, ts, **extra):
    return {"user_id": unit, "variant": variant, "exposure_ts": ts, **extra}


def persisted(tables_manager: InternalTablesManager, manager) -> dict[str, tuple]:
    """unit -> (variant, exposure_ts) from the persisted copy."""
    rows = manager._rows.get("_ab_exposures", [])
    return {r["unit_id"]: (r["variant"], r["exposure_ts"]) for r in rows}


@pytest.fixture
def manager():
    return ScriptedAssignmentManager()


@pytest.fixture
def tables(manager):
    t = InternalTablesManager(manager)
    t.ensure_tables()
    return t


def copy_once(manager, tables, experiment, *, now=NOW, has_stratum=False, log=None):
    return copy_exposures_incremental(
        manager,
        tables,
        experiment,
        None,
        make_grid(experiment),
        now=now,
        has_stratum=has_stratum,
        log=log,
    )


class TestGuards:
    def test_missing_added_filters_reference_raises(self, manager, tables):
        experiment = make_experiment(
            assignment={"query": "SELECT user_id, variant, exposure_ts FROM assignments"}
        )
        with pytest.raises(ExposureLoadError, match="ab_added_filters"):
            copy_once(manager, tables, experiment)
        assert manager.assignment_queries == []  # refused before any DB work

    def test_update_column_absent_from_source_raises(self, manager, tables):
        manager.scripted_rows = [row("u1", "control", START + timedelta(hours=2))]
        experiment = make_experiment(
            assignment={"cohort_copy": {"enabled": True, "update_column": "updated_at"}}
        )
        with pytest.raises(ValueError, match="unknown column"):
            copy_once(manager, tables, experiment)

    def test_token_hidden_in_a_sql_comment_still_raises(self, manager, tables):
        """The guard proves a LIVE render, not a substring — a token parked in
        a comment must not pass (review-confirmed silent-bounds hazard)."""
        experiment = make_experiment(
            assignment={
                "query": (
                    "-- remember ab_added_filters when editing\n"
                    "SELECT user_id, variant, exposure_ts FROM assignments"
                )
            }
        )
        with pytest.raises(ExposureLoadError, match="must render"):
            copy_once(manager, tables, experiment)

    def test_token_in_a_jinja_comment_still_raises(self, manager, tables):
        experiment = make_experiment(
            assignment={
                "query": (
                    "SELECT user_id, variant, exposure_ts FROM assignments "
                    "{# ab_added_filters #}"
                )
            }
        )
        with pytest.raises(ExposureLoadError, match="must render"):
            copy_once(manager, tables, experiment)


class TestFirstRunBackfill:
    def test_backfills_everything_matured_from_experiment_start(self, manager, tables):
        manager.scripted_rows = [
            row("u1", "control", START + timedelta(hours=2)),
            row("u2", "treatment", START + timedelta(days=3)),
            row("u3", "control", START + timedelta(days=9, hours=23)),
        ]
        outcome = copy_once(manager, tables, make_experiment())
        assert outcome.resumed is False
        assert outcome.covered_from == START  # grid.start_ts = tz-snapped start
        # snap: [START, NOW-0) // 1d = 10 whole days
        assert outcome.covered_through == START + timedelta(days=10)
        assert outcome.rows_written == 3
        assert set(persisted(tables, manager)) == {"u1", "u2", "u3"}

    def test_in_batch_duplicate_dedupes_to_earliest(self, manager, tables):
        early, late = START + timedelta(hours=2), START + timedelta(hours=20)
        manager.scripted_rows = [
            row("u1", "control", late),
            row("u1", "control", early),
        ]
        copy_once(manager, tables, make_experiment())
        assert persisted(tables, manager)["u1"] == ("control", early)

    def test_stratum_carried_when_probed(self, manager, tables):
        manager.scripted_rows = [row("u1", "control", START + timedelta(hours=2), stratum="ru")]
        copy_once(manager, tables, make_experiment(), has_stratum=True)
        rows = manager._rows["_ab_exposures"]
        assert rows[0]["stratum"] == "ru"


class TestClosedIntervalDiscipline:
    def test_open_interval_withheld_until_it_closes(self, manager, tables):
        in_open_bucket = NOW - timedelta(hours=2)  # inside [Jul 11 00:00, Jul 12 00:00)
        manager.scripted_rows = [
            row("u1", "control", START + timedelta(hours=2)),
            row("u2", "treatment", in_open_bucket),
        ]
        copy_once(manager, tables, make_experiment())
        assert set(persisted(tables, manager)) == {"u1"}

        # one day later the bucket has closed — the same row now loads
        copy_once(manager, tables, make_experiment(), now=NOW + timedelta(days=1))
        assert set(persisted(tables, manager)) == {"u1", "u2"}

    def test_maturity_delay_withholds_young_rows(self, manager, tables):
        experiment = make_experiment(
            assignment={"cohort_copy": {"enabled": True, "maturity_delay": "2d"}}
        )
        manager.scripted_rows = [
            row("u1", "control", START + timedelta(hours=2)),
            row("u2", "treatment", NOW - timedelta(days=1)),  # younger than the delay
        ]
        outcome = copy_once(manager, tables, experiment)
        assert set(persisted(tables, manager)) == {"u1"}
        # snap: [START, NOW-2d) // 1d = 8 whole days
        assert outcome.covered_through == START + timedelta(days=8)

    def test_nothing_matured_yet_bails_early(self, manager, tables):
        manager.scripted_rows = [row("u1", "control", START + timedelta(hours=2))]
        outcome = copy_once(manager, tables, make_experiment(), now=START + timedelta(hours=12))
        assert outcome.rows_written == 0
        assert outcome.covered_through is None  # nothing copied, ever
        assert manager.assignment_queries == []  # bailed before any batch query
        assert manager._rows.get("_ab_exposures", []) == []


class TestWatermarkResume:
    def test_second_pass_appends_only_new_rows(self, manager, tables):
        manager.scripted_rows = [row("u1", "control", START + timedelta(hours=2))]
        copy_once(manager, tables, make_experiment())
        first_pass_queries = len(manager.assignment_queries)

        manager.scripted_rows.append(row("u2", "treatment", START + timedelta(days=11)))
        outcome = copy_once(
            manager, tables, make_experiment(), now=NOW + timedelta(days=3)
        )
        assert outcome.resumed is True
        # resume re-scans from the WATERMARK'S BUCKET FLOOR (grid-anchored) —
        # the partially-persisted bucket is re-read and its units are
        # idempotently LWW-upserted, so "rows touched" is 2 while the SET
        # grows by 1
        assert outcome.covered_from == START  # floor of the Jul 1 02:00 watermark
        assert outcome.rows_written == 2
        assert set(persisted(tables, manager)) == {"u1", "u2"}
        assert len(manager._rows["_ab_exposures"]) == 2  # no duplicate u1 row
        assert len(manager.assignment_queries) > first_pass_queries

    def test_second_pass_in_the_same_bucket_holds_coverage(self, manager, tables):
        """Coverage is the deterministic grid bound, never the (lower) data
        maximum — a closed-enrollment cohort must not read as trailing
        forever (a review-confirmed false-warning source)."""
        manager.scripted_rows = [row("u1", "control", START + timedelta(hours=2))]
        first = copy_once(manager, tables, make_experiment())

        outcome = copy_once(
            manager, tables, make_experiment(), now=NOW + timedelta(hours=1)
        )
        assert outcome.rows_written in (0, 1)  # the re-scan only LWW-upserts u1
        assert outcome.covered_through == first.covered_through == START + timedelta(days=10)
        assert set(persisted(tables, manager)) == {"u1"}
        assert len(manager._rows["_ab_exposures"]) == 1

    def test_late_arriving_row_below_watermark_is_missed_forever(self, manager, tables):
        """The DISCLOSED donor limitation (m8 §4 Q3, doc-only): a row earlier
        than the watermark that only APPEARS later never reaches the copy."""
        manager.scripted_rows = [row("u1", "control", START + timedelta(days=5))]
        copy_once(manager, tables, make_experiment())

        # a backfilled assignment row, EARLIER than the watermark
        manager.scripted_rows.append(row("u9", "treatment", START + timedelta(days=1)))
        copy_once(manager, tables, make_experiment(), now=NOW + timedelta(days=5))
        assert "u9" not in persisted(tables, manager)

    def test_cross_batch_duplicate_resolves_to_later_batch_lww(self, manager, tables):
        """Pinned DISCLOSED divergence: a malformed duplicate spanning batches
        keeps the LATER batch's exposure_ts (the LWW upsert), unlike the full
        reload's global earliest-wins. Such input already fired the run-level
        duplicate warning."""
        early, late = START + timedelta(hours=2), START + timedelta(days=3, hours=2)
        manager.scripted_rows = [
            row("u1", "control", early),
            row("u1", "control", late),
        ]
        experiment = make_experiment(
            assignment={
                "cohort_copy": {"enabled": True, "batch_intervals_per_round_trip": 1}
            }
        )
        copy_once(manager, tables, experiment)
        assert persisted(tables, manager)["u1"] == ("control", late)

    def test_resume_rescan_takes_the_window_minimum_on_duplicate_input(
        self, manager, tables
    ):
        """Pinned DISCLOSED divergence (round 2, same LWW class): a resume
        re-scan window that no longer sees a duplicate unit's earliest row
        LWW-overwrites the persisted earliest with the window's own minimum.
        Only reachable on malformed multi-row-per-unit input — the run-level
        duplicate warning has already fired on it every run."""
        manager.scripted_rows = [
            row("dup", "control", datetime(2024, 7, 7, 5)),
            row("dup", "control", datetime(2024, 7, 9, 12)),
            row("z", "treatment", datetime(2024, 7, 9, 18)),  # sets the watermark
        ]
        copy_once(manager, tables, make_experiment(), now=datetime(2024, 7, 10))
        assert persisted(tables, manager)["dup"] == ("control", datetime(2024, 7, 7, 5))

        outcome = copy_once(manager, tables, make_experiment(), now=datetime(2024, 7, 12))
        assert outcome.resumed is True
        # the re-scan window [Jul 9, Jul 12) sees only the later duplicate
        assert persisted(tables, manager)["dup"] == ("control", datetime(2024, 7, 9, 12))


class TestBatchingInvariance:
    def test_round_trip_size_never_changes_the_persisted_rows(self, manager, tables):
        manager.scripted_rows = [
            row(f"u{i}", "control" if i % 2 else "treatment", START + timedelta(days=i % 9, hours=3))
            for i in range(20)
        ]
        results = {}
        for per_trip in (1, 3, 30):
            mgr = ScriptedAssignmentManager()
            mgr.scripted_rows = list(manager.scripted_rows)
            tbl = InternalTablesManager(mgr)
            tbl.ensure_tables()
            experiment = make_experiment(
                assignment={
                    "cohort_copy": {
                        "enabled": True,
                        "batch_intervals_per_round_trip": per_trip,
                    }
                }
            )
            outcome = copy_once(mgr, tbl, experiment)
            results[per_trip] = (persisted(tbl, mgr), outcome.rows_written)
        assert results[1] == results[3] == results[30]

    def test_round_trip_count_matches_the_chunk_arithmetic(self, manager, tables):
        manager.scripted_rows = [row("u1", "control", START + timedelta(hours=2))]
        experiment = make_experiment(
            assignment={
                "cohort_copy": {"enabled": True, "batch_intervals_per_round_trip": 4}
            }
        )
        outcome = copy_once(manager, tables, experiment)
        # 10 matured days / (4 × 1d) per round trip = ceil(2.5) = 3
        assert outcome.round_trips == 3


class TestCustomUpdateColumn:
    def test_bounds_filter_on_update_column_not_exposure_ts(self, manager, tables):
        experiment = make_experiment(
            assignment={"cohort_copy": {"enabled": True, "update_column": "updated_at"}}
        )
        manager.scripted_rows = [
            # old exposure, recently (re-)written by the ETL: inside the open
            # bucket on updated_at → withheld even though exposure_ts is old
            row("u1", "control", START + timedelta(hours=2), updated_at=NOW - timedelta(hours=1)),
            # matured on updated_at → loads, keeping its own exposure_ts
            row(
                "u2",
                "treatment",
                START + timedelta(hours=3),
                updated_at=START + timedelta(days=2),
            ),
        ]
        copy_once(manager, tables, experiment)
        snapshot = persisted(tables, manager)
        assert set(snapshot) == {"u2"}
        assert snapshot["u2"] == ("treatment", START + timedelta(hours=3))

    def test_resume_never_bounds_update_column_by_the_exposure_watermark(
        self, manager, tables
    ):
        """Review-confirmed MAJOR: MAX(exposure_ts) says nothing about another
        column's frontier — bounding updated_at by it silently drops
        legitimate new rows forever. A custom update_column therefore takes
        no watermark fast-path and re-scans from the experiment start."""
        experiment = make_experiment(
            assignment={"cohort_copy": {"enabled": True, "update_column": "updated_at"}}
        )
        # a unit whose exposure_ts is FAR AHEAD of its own update stamp
        manager.scripted_rows = [
            row(
                "u_future",
                "control",
                START + timedelta(days=20),
                updated_at=START + timedelta(hours=2),
            )
        ]
        first = copy_once(manager, tables, experiment)
        assert first.resumed is False
        assert set(persisted(tables, manager)) == {"u_future"}

        # a brand-new ordinary unit, matured on updated_at but with an
        # exposure_ts far BELOW the persisted MAX(exposure_ts)
        manager.scripted_rows.append(
            row(
                "u_new",
                "treatment",
                START + timedelta(days=2),
                updated_at=START + timedelta(days=11),
            )
        )
        second = copy_once(manager, tables, experiment, now=NOW + timedelta(days=3))
        assert second.resumed is False  # no persisted cursor for a custom column
        assert "u_new" in persisted(tables, manager)


class TestFilterComposition:
    def test_bounds_start_with_and_when_base_is_empty(self):
        frag = _batch_added_filters("", "exposure_ts", START, START + timedelta(days=1))
        assert frag.startswith("AND exposure_ts >= '2024-07-01 00:00:00'")
        assert "AND exposure_ts < '2024-07-02 00:00:00'" in frag

    def test_base_filters_are_preserved_in_front(self):
        frag = _batch_added_filters(
            "AND country = 'DE'", "exposure_ts", START, START + timedelta(days=1)
        )
        assert frag.startswith("AND country = 'DE' AND exposure_ts >= ")

    def test_engine_injects_bounds_through_the_template(self, manager, tables):
        manager.scripted_rows = [row("u1", "control", START + timedelta(hours=2))]
        copy_once(manager, tables, make_experiment())
        batch_queries = [q for q in manager.assignment_queries if "GROUP BY" in q]
        assert batch_queries, "no batch query executed"
        assert all("exposure_ts >= '" in q and "exposure_ts < '" in q for q in batch_queries)
