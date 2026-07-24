"""WP2 pushdown snapshot tests (m8-implementation-plan.md WP2).

Covers the ``exposure_source`` pushdown module directly: byte-identical
error/warning wording vs. the historical Python loop (§0.5(d)), the
``has_stratum`` branches, empty/missing-column guards, and — the §0.5(b)
review fixture — duplicate rows differing in BOTH timestamp AND stratum, where
``MIN(exposure_ts)`` / ``MIN(stratum)`` resolve independently. A local
``_legacy_seen`` re-implements the exact pre-WP2 dedup loop so parity (counts +
earliest exposure_ts) and the accepted stratum divergence are proven against
the real old behavior, not just asserted from memory.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fake_db import FakeDatabaseManager, serve_assignment_pushdown

from abkit.config import ExperimentConfig
from abkit.loaders.exposure_source import (
    ExposureLoadError,
    ExposureSnapshot,
    _pushdown_sql,
    build_cohort_backend,
    load_variant_map,
    probe_has_stratum,
    render_assignment_sql,
    validate_and_snapshot,
)
from abkit.utils.datetime_utils import to_naive_utc


class ScriptedAssignmentManager(FakeDatabaseManager):
    """Serves scripted assignment rows through the WP2 pushdown shape.

    The ``LIMIT 1`` probe returns the first scripted row verbatim (so a
    missing column is detected on the ACTUAL source columns); the ``GROUP BY``
    aggregation is delegated to the base manager's ``_project`` so MIN/COUNT
    are evaluated by the one shared fake-DB implementation.
    """

    def __init__(self):
        super().__init__()
        self.scripted_rows: list[dict] = []

    def execute_query(self, query, params=None):
        normalized = " ".join(query.split())
        if "assignments" not in normalized:
            return super().execute_query(query, params)
        return serve_assignment_pushdown(self._project, normalized, self.scripted_rows)


def _legacy_seen(
    rows: list[dict], unit_key: str, declared: set[str]
) -> tuple[dict[str, int], dict[Any, tuple[str, Any, Any]]]:
    """The exact pre-WP2 Python dedup loop (counts + seen), for parity proofs."""
    has_stratum = bool(rows) and "stratum" in rows[0]
    seen: dict[Any, tuple[str, Any, Any]] = {}
    for row in rows:
        unit = row[unit_key]
        variant = row["variant"]
        exposure_ts = to_naive_utc(row["exposure_ts"])
        stratum = row.get("stratum") if has_stratum else None
        if unit in seen:
            prev_variant, prev_ts, _prev_stratum = seen[unit]
            if prev_variant != variant:
                raise AssertionError("cross-variant in legacy fixture")
            if exposure_ts is not None and (prev_ts is None or exposure_ts < prev_ts):
                seen[unit] = (variant, exposure_ts, stratum)
        else:
            seen[unit] = (variant, exposure_ts, stratum)
    counts: dict[str, int] = {}
    for variant, _, _ in seen.values():
        counts[variant] = counts.get(variant, 0) + 1
    return counts, seen


def make_experiment(**overrides):
    base = {
        "name": "signup_test",
        "start_date": "2024-07-01",
        "end_date": "2024-07-28",
        "unit_key": "user_id",
        "assignment": {
            "query": "SELECT user_id, variant, exposure_ts FROM assignments",
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
        },
        "comparisons": [
            {
                "metric": "arpu",
                "is_main_metric": True,
                "method": {"name": "t-test", "params": {"test_type": "relative"}},
            }
        ],
    }
    base.update(overrides)
    return ExperimentConfig.model_validate(base)


RENDERED = "SELECT user_id, variant, exposure_ts FROM assignments"
RENDERED_STRATUM = "SELECT user_id, variant, exposure_ts, stratum FROM assignments"


def row(unit, variant, ts=None, **extra):
    return {
        "user_id": unit,
        "variant": variant,
        "exposure_ts": ts or datetime(2024, 7, 1, 10, 0, 0),
        **extra,
    }


@pytest.fixture
def manager():
    return ScriptedAssignmentManager()


@pytest.fixture
def experiment():
    return make_experiment()


class TestSnapshotHappyPath:
    def test_clean_cohort_counts_and_by_unit(self, manager, experiment):
        manager.scripted_rows = [
            row("u1", "control", datetime(2024, 7, 1, 9)),
            row("u2", "treatment", datetime(2024, 7, 2, 9)),
            row("u3", "control", datetime(2024, 7, 3, 9)),
        ]
        snap = validate_and_snapshot(manager, experiment, RENDERED)
        assert isinstance(snap, ExposureSnapshot)
        assert snap.counts == {"control": 2, "treatment": 1}
        assert snap.has_stratum is False
        assert snap.by_unit["u1"] == ("control", datetime(2024, 7, 1, 9), None)
        # Parity: same counts the legacy loop produced on the same input.
        legacy_counts, _ = _legacy_seen(manager.scripted_rows, "user_id", set())
        assert snap.counts == legacy_counts

    def test_has_stratum_true_branch(self, manager, experiment):
        manager.scripted_rows = [
            row("u1", "control", stratum="US"),
            row("u2", "treatment", stratum="CA"),
        ]
        assert probe_has_stratum(manager, RENDERED_STRATUM) is True
        snap = validate_and_snapshot(manager, experiment, RENDERED_STRATUM)
        assert snap.has_stratum is True
        assert snap.by_unit["u1"][2] == "US"
        assert snap.by_unit["u2"][2] == "CA"

    def test_has_stratum_can_be_forced_off(self, manager, experiment):
        manager.scripted_rows = [row("u1", "control", stratum="US")]
        snap = validate_and_snapshot(manager, experiment, RENDERED_STRATUM, has_stratum=False)
        assert snap.has_stratum is False
        assert snap.by_unit["u1"][2] is None


class TestValidationErrors:
    def test_cross_variant_is_a_hard_error(self, manager, experiment):
        manager.scripted_rows = [row("u1", "control"), row("u1", "treatment")]
        with pytest.raises(ExposureLoadError, match="BOTH 'control' and 'treatment'"):
            validate_and_snapshot(manager, experiment, RENDERED)

    def test_undeclared_variant(self, manager, experiment):
        manager.scripted_rows = [row("u1", "ghost")]
        with pytest.raises(ExposureLoadError, match="variant 'ghost' not declared"):
            validate_and_snapshot(manager, experiment, RENDERED)

    def test_empty_cohort(self, manager, experiment):
        manager.scripted_rows = []
        with pytest.raises(ExposureLoadError, match="returned no rows"):
            validate_and_snapshot(manager, experiment, RENDERED)

    def test_missing_required_column(self, manager, experiment):
        # No exposure_ts column at all — must be caught on the raw probe with the
        # friendly "must SELECT" message, BEFORE the MIN(exposure_ts) aggregation.
        manager.scripted_rows = [{"user_id": "u1", "variant": "control"}]
        with pytest.raises(ExposureLoadError, match="must SELECT"):
            validate_and_snapshot(manager, experiment, RENDERED)


class TestDuplicateRows:
    def test_same_variant_dupes_warn_and_keep_earliest_ts(self, manager, experiment):
        manager.scripted_rows = [
            row("u1", "control", datetime(2024, 7, 2, 9)),
            row("u1", "control", datetime(2024, 7, 1, 9)),
        ]
        with pytest.warns(UserWarning, match="duplicate unit rows"):
            snap = validate_and_snapshot(manager, experiment, RENDERED)
        assert snap.counts == {"control": 1}
        # MIN(exposure_ts) picks the same winner the legacy `< prev_ts` loop did.
        assert snap.by_unit["u1"][1] == datetime(2024, 7, 1, 9)
        legacy_counts, legacy_seen = _legacy_seen(manager.scripted_rows, "user_id", set())
        assert snap.counts == legacy_counts
        assert snap.by_unit["u1"][1] == legacy_seen["u1"][1]

    def test_duplicate_count_is_total_collapsed_rows(self, manager, experiment):
        # 3 rows for u1 + 2 for u2 → 3 collapsed duplicates (2 + 1).
        manager.scripted_rows = [
            row("u1", "control", datetime(2024, 7, 1, 9)),
            row("u1", "control", datetime(2024, 7, 2, 9)),
            row("u1", "control", datetime(2024, 7, 3, 9)),
            row("u2", "treatment", datetime(2024, 7, 1, 9)),
            row("u2", "treatment", datetime(2024, 7, 2, 9)),
        ]
        with pytest.warns(UserWarning, match="returned 3 duplicate"):
            snap = validate_and_snapshot(manager, experiment, RENDERED)
        assert snap.counts == {"control": 1, "treatment": 1}


class TestStratumTieBreakDivergence:
    """The §0.5(b) review fixture: dup rows differing in BOTH ts AND stratum.

    ``MIN(exposure_ts)`` and ``MIN(stratum)`` resolve independently, so the
    pushdown keeps the earliest exposure_ts (parity) but the
    lexicographically-smallest stratum — which is a DIFFERENT row's stratum than
    the earliest-ts row the legacy loop kept. This is the accepted, disclosed
    divergence: it only surfaces on already-malformed (duplicate) input that
    also trips the loud duplicate warning.
    """

    def test_exposure_ts_parity_but_stratum_diverges(self, manager, experiment):
        # Earliest-ts row (7-01) carries stratum "US"; the later row (7-02) "CA".
        manager.scripted_rows = [
            row("u1", "control", datetime(2024, 7, 1, 9), stratum="US"),
            row("u1", "control", datetime(2024, 7, 2, 9), stratum="CA"),
        ]
        with pytest.warns(UserWarning, match="duplicate unit rows"):
            snap = validate_and_snapshot(manager, experiment, RENDERED_STRATUM)

        legacy_counts, legacy_seen = _legacy_seen(manager.scripted_rows, "user_id", set())

        # exposure_ts: exact parity — both keep the earliest.
        assert snap.by_unit["u1"][1] == datetime(2024, 7, 1, 9)
        assert snap.by_unit["u1"][1] == legacy_seen["u1"][1]
        assert snap.counts == legacy_counts

        # stratum: DIVERGES by design. Pushdown = MIN("US","CA") = "CA";
        # the legacy loop kept the earliest-ts row's "US".
        assert snap.by_unit["u1"][2] == "CA"
        assert legacy_seen["u1"][2] == "US"
        assert snap.by_unit["u1"][2] != legacy_seen["u1"][2]

    def test_no_divergence_on_wellformed_cohort(self, manager, experiment):
        # One row per unit → MIN over a singleton == that row's stratum. The
        # milestone's numeric-parity gate holds exactly here.
        manager.scripted_rows = [
            row("u1", "control", datetime(2024, 7, 1, 9), stratum="US"),
            row("u2", "treatment", datetime(2024, 7, 2, 9), stratum="CA"),
        ]
        snap = validate_and_snapshot(manager, experiment, RENDERED_STRATUM)
        _, legacy_seen = _legacy_seen(manager.scripted_rows, "user_id", set())
        assert snap.by_unit["u1"] == legacy_seen["u1"]
        assert snap.by_unit["u2"] == legacy_seen["u2"]


class TestCountsParityMatrix:
    """counts == the legacy loop's counts across the WP2-listed fixtures."""

    @pytest.mark.parametrize(
        "rows",
        [
            pytest.param(
                [row("u1", "control"), row("u2", "treatment"), row("u3", "control")],
                id="clean-cohort",
            ),
            pytest.param(
                [
                    row("u1", "control", datetime(2024, 7, 2, 9)),
                    row("u1", "control", datetime(2024, 7, 1, 9)),
                    row("u2", "treatment"),
                ],
                id="same-variant-duplicate",
            ),
            pytest.param(
                [
                    row("u1", "control", datetime(2024, 7, 1, 9), stratum="US"),
                    row("u1", "control", datetime(2024, 7, 2, 9), stratum="CA"),
                    row("u2", "treatment", stratum="US"),
                ],
                id="dup-differing-ts-and-stratum",
            ),
        ],
    )
    def test_counts_match_legacy(self, manager, experiment, rows):
        import warnings as _w

        manager.scripted_rows = rows
        rendered = RENDERED_STRATUM if "stratum" in rows[0] else RENDERED
        with _w.catch_warnings():  # duplicate warnings are exercised elsewhere
            _w.simplefilter("ignore")
            snap = validate_and_snapshot(manager, experiment, rendered)
        legacy_counts, _ = _legacy_seen(rows, "user_id", set())
        assert snap.counts == legacy_counts


class TestDerivedTableWrapIsSyntaxSafe:
    """The wrap must not break on a trailing ``;`` or ``-- line comment`` in the
    user's assignment SQL — both ran fine pre-WP2 (direct execute). Fake backends
    don't parse SQL, so this is asserted on the generated query string shape."""

    def test_trailing_semicolon_is_stripped(self):
        sql = _pushdown_sql("user_id", "SELECT user_id, variant, exposure_ts FROM t;", False)
        # No terminator survives inside the derived table.
        assert ";" not in sql
        assert "FROM t\n)" in sql

    def test_trailing_line_comment_cannot_swallow_the_closing_paren(self):
        sql = _pushdown_sql(
            "user_id",
            "SELECT user_id, variant, exposure_ts FROM t -- picked cohort",
            False,
        )
        # The closing paren + alias sit on their own line, past the comment.
        assert "-- picked cohort\n) _abk_raw" in sql
        assert "GROUP BY user_id, variant" in sql

    def test_wrap_survives_the_fake_backend_round_trip(self, manager, experiment):
        # A trailing semicolon in the rendered SQL still loads through the fake
        # manager (regression guard for the normalize+split parsing).
        manager.scripted_rows = [row("u1", "control"), row("u2", "treatment")]
        snap = validate_and_snapshot(
            manager, experiment, "SELECT user_id, variant, exposure_ts FROM assignments;"
        )
        assert snap.counts == {"control": 1, "treatment": 1}


class TestNoWarningPath:
    def test_clean_cohort_no_warning(self, manager, experiment):
        manager.scripted_rows = [row("u1", "control"), row("u2", "treatment")]
        import warnings as _w

        with _w.catch_warnings():
            _w.simplefilter("error")
            snap = validate_and_snapshot(manager, experiment, RENDERED)
        legacy_counts, _ = _legacy_seen(manager.scripted_rows, "user_id", set())
        assert snap.counts == legacy_counts


# ── m8 WP4: render_assignment_sql + build_cohort_backend ────────────────────────

METRIC_TEMPLATE = (
    "{% import 'abkit_assignment.jinja' as ab %}"
    "SELECT {{ ab.variant_col() }} AS variant, user_id, sum(v) AS v "
    "FROM {{ data_database }}.facts {{ ab.exposed_units() }} GROUP BY variant, user_id"
)


class _UntouchableManager(FakeDatabaseManager):
    """Proves a code path never reaches the warehouse."""

    def execute_query(self, query, params=None):
        raise AssertionError(f"this path must be warehouse-free, executed: {query!r}")


def _grid(experiment):
    from abkit.core.period_planner import generate_grid

    return generate_grid(
        experiment.start_date,
        experiment.end_date,
        experiment.cadence_segments(),
        tz=experiment.timezone,
    )


def _copy_experiment():
    return make_experiment(
        assignment={
            "query": "SELECT user_id, variant, exposure_ts FROM assignments",
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
            "cohort_copy": {"enabled": True},
        }
    )


class TestRenderAssignmentSql:
    def test_renders_the_driver_identical_full_window(self, manager):
        # the window built-ins must span [grid.start_ts, grid.horizon_ts) — the
        # same tz-snapped edges the driver's historical LOAD render used
        experiment = make_experiment(
            assignment={
                "query": (
                    "SELECT user_id, variant, exposure_ts FROM assignments "
                    "WHERE exposure_ts >= '{{ ab_start_ts }}' "
                    "AND exposure_ts < '{{ ab_end_ts }}' {{ ab_added_filters }}"
                ),
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            }
        )
        grid = _grid(experiment)
        rendered = render_assignment_sql(manager, experiment, None, grid)
        assert grid.start_ts.strftime("%Y-%m-%d %H:%M:%S") in rendered
        assert grid.horizon_ts.strftime("%Y-%m-%d %H:%M:%S") in rendered
        assert "{{" not in rendered  # fully rendered, ab_added_filters included


class TestLoadVariantMap:
    """The m9 WP4 mid-run refresh reader (an R3 review gap: it had no direct test)."""

    def test_maps_units_to_variants(self, manager, experiment):
        manager.scripted_rows = [
            row("u1", "control"),
            row("u2", "treatment"),
            row("u3", "control"),
        ]
        assert load_variant_map(manager, experiment, None, _grid(experiment)) == {
            "u1": "control",
            "u2": "treatment",
            "u3": "control",
        }

    def test_duplicate_rows_do_not_warn_again(self, manager, experiment):
        """LOAD already warned on this source once this run — warning twice for
        one source was the R2 finding this reader exists to avoid."""
        import warnings as warnings_module

        manager.scripted_rows = [
            row("u1", "control", datetime(2024, 7, 2, 9)),
            row("u1", "control", datetime(2024, 7, 1, 9)),
        ]
        with warnings_module.catch_warnings(record=True) as caught:
            warnings_module.simplefilter("always")
            mapped = load_variant_map(manager, experiment, None, _grid(experiment))
        assert mapped == {"u1": "control"}
        assert [w for w in caught if "duplicate unit rows" in str(w.message)] == []

    def test_cross_variant_still_fails_loudly_with_mid_run_context(self, manager, experiment):
        manager.scripted_rows = [row("u1", "control"), row("u1", "treatment")]
        with pytest.raises(ExposureLoadError, match="BOTH 'control' and 'treatment'") as excinfo:
            load_variant_map(manager, experiment, None, _grid(experiment))
        assert "mid-run" in str(excinfo.value)

    def test_undeclared_variant_still_fails_loudly(self, manager, experiment):
        """The m8 doctrine holds on this surface too (an R3 review fix)."""
        manager.scripted_rows = [row("u1", "control"), row("u2", "ghost")]
        with pytest.raises(ExposureLoadError, match="variant 'ghost' not declared") as excinfo:
            load_variant_map(manager, experiment, None, _grid(experiment))
        assert "mid-run" in str(excinfo.value)


class TestBuildCohortBackend:
    def test_direct_default_threads_source_and_probed_stratum(self, manager, experiment):
        from abkit.loaders.query_template import RenderWindow

        manager.scripted_rows = [row("u1", "control"), row("u2", "treatment")]
        grid = _grid(experiment)
        backend, snapshot = build_cohort_backend(manager, experiment, None, grid)

        # the snapshot comes back validated even without with_snapshot (direct
        # mode renders anyway); has_stratum probed off the source's own columns
        assert snapshot is not None
        assert snapshot.counts == {"control": 1, "treatment": 1}
        assert snapshot.has_stratum is False

        rendered = backend.render(
            METRIC_TEMPLATE, RenderWindow(grid.start_ts, grid.cutoffs[0].end_ts)
        )
        # the metric render joins the deduping direct fragment, not the copy
        assert "_abk_raw" in rendered
        assert "NULL AS stratum" in rendered
        assert "_ab_exposures" not in rendered
        # ... and embeds the driver-identical rendered assignment SQL verbatim
        assert render_assignment_sql(manager, experiment, None, grid) in rendered

    def test_direct_mode_stratum_source_projects_min_stratum(self, manager):
        from abkit.loaders.query_template import RenderWindow

        experiment = make_experiment()
        manager.scripted_rows = [
            row("u1", "control", stratum="ru"),
            row("u2", "treatment", stratum="de"),
        ]
        grid = _grid(experiment)
        backend, snapshot = build_cohort_backend(manager, experiment, None, grid)
        assert snapshot is not None and snapshot.has_stratum is True
        rendered = backend.render(
            METRIC_TEMPLATE, RenderWindow(grid.start_ts, grid.cutoffs[0].end_ts)
        )
        assert "MIN(stratum) AS stratum" in rendered

    def test_copy_mode_is_warehouse_free_without_snapshot(self):
        from abkit.loaders.query_template import RenderWindow

        experiment = _copy_experiment()
        manager = _UntouchableManager()  # raises on ANY query
        grid = _grid(experiment)
        backend, snapshot = build_cohort_backend(manager, experiment, None, grid)
        assert snapshot is None  # read-only callers stay cheap in copy mode
        rendered = backend.render(
            METRIC_TEMPLATE, RenderWindow(grid.start_ts, grid.cutoffs[0].end_ts)
        )
        assert "._ab_exposures" in rendered  # the persisted join, unchanged
        assert "_abk_raw" not in rendered

    def test_copy_mode_with_snapshot_validates_but_keeps_the_persisted_join(self, manager):
        from abkit.loaders.query_template import RenderWindow

        experiment = _copy_experiment()
        manager.scripted_rows = [row("u1", "control"), row("u2", "treatment")]
        grid = _grid(experiment)
        backend, snapshot = build_cohort_backend(
            manager, experiment, None, grid, with_snapshot=True
        )
        assert snapshot is not None
        assert snapshot.counts == {"control": 1, "treatment": 1}
        rendered = backend.render(
            METRIC_TEMPLATE, RenderWindow(grid.start_ts, grid.cutoffs[0].end_ts)
        )
        assert "._ab_exposures" in rendered
        assert "_abk_raw" not in rendered

    def test_factory_never_writes(self, manager, experiment):
        # persistence belongs to the caller that owns writes (the driver's
        # copy-mode persist_snapshot) — the factory itself is read-only
        manager.scripted_rows = [row("u1", "control"), row("u2", "treatment")]
        build_cohort_backend(manager, experiment, None, _grid(experiment), with_snapshot=True)
        assert manager._rows.get("_ab_exposures", []) == []

    def test_direct_mode_surfaces_the_cross_variant_hard_error(self, manager, experiment):
        # a corrupted LIVE source must fail the factory before any metric
        # joins it — the same wording the historical loader used
        manager.scripted_rows = [row("u1", "control"), row("u1", "treatment")]
        with pytest.raises(ExposureLoadError, match="assigned to BOTH"):
            build_cohort_backend(manager, experiment, None, _grid(experiment))
