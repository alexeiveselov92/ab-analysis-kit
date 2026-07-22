"""WP4 tests: the explore recompute engine (m3-implementation-plan.md WP4).

The golden shape: pipeline a synthetic fixture through the REAL
``run_experiment`` (persisting via fake_db), build an explore session over the
same warehouse, and prove the engine's answers against the persisted rows —
Tier E at rel-1e-9 with no cache at all, bootstrap byte-equal through the
Tier-S cache and re-derived seeds, alpha-inversion against a second pipeline
run at the other alpha (a cross-experiment golden, never the engine testing
itself), the D11 order-permutation invariance, tier classification, the
cache-budget clamp, the canonical live ``method_config_id``, and the D3
calibration lookup states. The warehouse harness lives in
``tests/_helpers/synthetic_ab.py`` (shared with the WP6 server suite).
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from synthetic_ab import (
    METRICS,
    PROJECT,
    SyntheticWarehouse,
    assert_close,
    build_engine,
    build_session,
    make_experiment,
    persisted,
    run_pipeline,
    seed_all_events,
    seed_cohort,
)

from abkit.config import ProjectConfig
from abkit.config.method_config import MethodConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.stats import (
    MethodParamError,
    QuarantinedMethodError,
    UnknownMethodError,
    get_method_class,
)
from abkit.tuning import (
    KnobState,
    RecomputeEngine,
    find_calibration,
    load_session,
    resolve_fpr_budget,
)
from abkit.tuning.recompute import alpha_knob_tier, classify_knob


@pytest.fixture
def warehouse():
    wh = SyntheticWarehouse()
    seed_cohort(wh)
    seed_all_events(wh)
    return wh


@pytest.fixture
def tables(warehouse):
    return InternalTablesManager(warehouse)


# ── Tier E golden round-trips (NO cache: suffstats-only sessions) ────────────


class TestTierERoundTrip:
    def test_ttest_reproduces_persisted_rows_without_cache(self, warehouse, tables):
        experiment = make_experiment(
            "exp_t",
            "arpu",
            {"name": "t-test", "params": {"test_type": "relative", "calculate_mde": True}},
        )
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        result = engine.recompute("arpu", engine.default_knobs("arpu"))

        baseline = persisted(tables, experiment, "arpu")
        assert len(result.pairs) == 1
        points = result.pairs[0].points
        assert len(points) == 4
        for point in points:
            assert point.tier == "exact"
            row = baseline[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue", "mde_1", "mde_2"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")
            assert point.reject == row["reject"]
        assert not result.identity_changed

    def test_ztest_inverts_nobs_from_the_persisted_se(self, warehouse, tables):
        experiment = make_experiment(
            "exp_z",
            "conversion",
            {"name": "z-test", "params": {"test_type": "relative", "calculate_mde": True}},
        )
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        result = engine.recompute("conversion", engine.default_knobs("conversion"))

        baseline = persisted(tables, experiment, "conversion")
        points = result.pairs[0].points
        assert len(points) == 4
        for point in points:
            assert point.tier == "exact"
            row = baseline[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue", "mde_1", "mde_2"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")
            # THE blocker regression: per-unit trials > 1, so the z-test ran on
            # summed nobs — recoverable ONLY from the SE, never from size_i
            # (the one-row-per-unit count persisted on the row). The POINT
            # keeps unit-count sizes (tier-consistent); the reconstructed
            # method sizes live on the raw result.
            assert point.size_1 == row["size_1"]
            assert point.result.size_1 > row["size_1"]
            assert point.result.size_2 > row["size_2"]

    def test_ratio_delta_surrogate_reproduces_persisted_rows(self, warehouse, tables):
        experiment = make_experiment(
            "exp_r", "ctr", {"name": "ratio-delta", "params": {"test_type": "relative"}}
        )
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        result = engine.recompute("ctr", engine.default_knobs("ctr"))

        baseline = persisted(tables, experiment, "ctr")
        points = result.pairs[0].points
        assert len(points) == 4
        for point in points:
            assert point.tier == "exact"
            row = baseline[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")

    def test_cuped_reproduces_persisted_rows_without_cache(self, warehouse, tables):
        """The M9 WP2 golden round-trip (the decision-(d) gate): a CUPED row's
        persisted covariate moments reconstruct the full ``SufficientStats``
        pair and ``from_suffstats`` reproduces the pipeline's ``from_samples``
        numbers — incl. θ — at rel-1e-9 (NEVER ``==``: ``std → std²·n`` is a
        round-off-exact reconstruction, not a bit-identical one). This is the
        concrete evidence for "schema change, not ALGORITHM_VERSION bump"."""
        experiment = make_experiment(
            "exp_cuped_rt",
            "arpu",
            {
                "name": "cuped-t-test",
                "params": {
                    "test_type": "relative",
                    "covariate_lookback": "7d",
                    "calculate_mde": True,
                },
            },
        )
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        result = engine.recompute("arpu", engine.default_knobs("arpu"))
        assert not result.identity_changed

        baseline = persisted(tables, experiment, "arpu")
        points = result.pairs[0].points
        assert len(points) == 4
        for point in points:
            assert point.tier == "exact"
            row = baseline[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue", "mde_1", "mde_2"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")
            assert point.reject == row["reject"]
            # θ diagnostics: the reconstruction reproduces the pooled
            # mixed-ddof θ the pipeline persisted for this cutoff
            stored_theta = json.loads(row["diagnostics"])["theta"]
            assert_close(point.result.diagnostics["theta"], stored_theta, f"theta@{point.end_ts}")

    def test_test_type_switch_recomputes_the_whole_grid(self, warehouse, tables):
        """An identity edit inside the Tier-E family stays exact everywhere:
        the absolute-effect answer equals a pipeline actually run absolute."""
        relative = make_experiment(
            "exp_rel", "arpu", {"name": "t-test", "params": {"test_type": "relative"}}
        )
        absolute = make_experiment(
            "exp_abs", "arpu", {"name": "t-test", "params": {"test_type": "absolute"}}
        )
        run_pipeline(warehouse, tables, relative)
        run_pipeline(warehouse, tables, absolute)

        engine = build_engine(warehouse, tables, relative, with_cache=False)
        result = engine.recompute(
            "arpu", KnobState("t-test", {"test_type": "absolute"}, alpha=0.05)
        )
        assert result.identity_changed
        expected = persisted(tables, absolute, "arpu")
        points = result.pairs[0].points
        assert len(points) == 4
        for point in points:
            assert point.tier == "exact"
            row = expected[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")


# ── Alpha knob: Tier-E recompute & the CUPED α-inversion (cross-golden) ─────


class TestAlphaChange:
    @pytest.mark.parametrize(
        ("metric", "method"),
        [
            ("arpu", {"name": "t-test", "params": {"test_type": "relative"}}),
            ("conversion", {"name": "z-test", "params": {"test_type": "relative"}}),
            ("ctr", {"name": "ratio-delta", "params": {"test_type": "relative"}}),
        ],
    )
    def test_closed_form_alpha_change_matches_a_real_run(self, warehouse, tables, metric, method):
        exp_a = make_experiment(f"exp_a_{metric}", metric, method, alpha=0.05)
        exp_b = make_experiment(f"exp_b_{metric}", metric, method, alpha=0.01)
        run_pipeline(warehouse, tables, exp_a)
        run_pipeline(warehouse, tables, exp_b)

        engine = build_engine(warehouse, tables, exp_a, with_cache=False)
        knobs = KnobState(method["name"], method["params"], alpha=0.01)
        result = engine.recompute(metric, knobs)
        assert not result.identity_changed  # alpha never enters the id

        expected = persisted(tables, exp_b, metric)
        points = result.pairs[0].points
        assert len(points) == 4
        for point in points:
            assert point.tier == "exact"
            row = expected[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")
            assert point.reject == row["reject"]

    def test_cuped_alpha_change_matches_a_real_run_exactly(self, warehouse, tables):
        """M9 WP2: the persisted covariate moments make the CUPED alpha knob
        Tier E — the whole grid recomputes exactly with NO cache at all
        (pre-WP2 this test pinned exact-if-cached / α-invert-otherwise)."""
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        exp_a = make_experiment("exp_cuped_a", "arpu", method, alpha=0.05)
        exp_b = make_experiment("exp_cuped_b", "arpu", method, alpha=0.01)
        run_pipeline(warehouse, tables, exp_a)
        run_pipeline(warehouse, tables, exp_b)

        engine = build_engine(warehouse, tables, exp_a, with_cache=False)
        knobs = KnobState("cuped-t-test", method["params"], alpha=0.01)
        result = engine.recompute("arpu", knobs)
        assert not result.identity_changed  # alpha never enters the id

        expected = persisted(tables, exp_b, "arpu")
        points = result.pairs[0].points
        assert len(points) == 4
        for point in points:
            assert point.tier == "exact"
            row = expected[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")
            assert point.reject == row["reject"]

    def test_cuped_alpha_inversion_matches_a_real_run_on_pre_migration_rows(
        self, warehouse, tables
    ):
        """The honest α-inversion fallback survives for 0.3.x rows: with the
        M9 WP1 covariate-moment columns NULLed, uncached cutoffs α-invert
        (tier "approx") against the same cross-run golden as before."""
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        exp_a = make_experiment("exp_cuped_legacy_a", "arpu", method, alpha=0.05)
        exp_b = make_experiment("exp_cuped_legacy_b", "arpu", method, alpha=0.01)
        run_pipeline(warehouse, tables, exp_a)
        run_pipeline(warehouse, tables, exp_b)
        for row in warehouse._rows["_ab_results"]:
            for column in ("cov_std_1", "cov_std_2", "corr_coef_1", "corr_coef_2"):
                row[column] = None

        # cache only the LATEST cutoff (one cuped cutoff = 120 units × 2 arms
        # × 2 roles = 480 values) so older cutoffs must α-invert
        engine = build_engine(warehouse, tables, exp_a, budget=500)
        knobs = KnobState("cuped-t-test", method["params"], alpha=0.01)
        result = engine.recompute("arpu", knobs)

        expected = persisted(tables, exp_b, "arpu")
        points = result.pairs[0].points
        assert len(points) == 4
        cached = engine._session.cached_cutoffs("arpu")
        for point in points:
            # cached cutoffs recompute exactly (Tier S); the rest α-invert
            assert point.tier == ("exact" if point.end_ts in cached else "approx")
            row = expected[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")
        assert any(point.tier == "approx" for point in points)
        assert any(point.tier == "exact" for point in points)


# ── CUPED tier routing (on→off exact; off→on is Tier R) ─────────────────────


class TestCupedRouting:
    def test_cuped_off_is_exact_over_the_whole_grid(self, warehouse, tables):
        cuped = make_experiment(
            "exp_cuped_off",
            "arpu",
            {
                "name": "cuped-t-test",
                "params": {"test_type": "relative", "covariate_lookback": "7d"},
            },
        )
        plain = make_experiment(
            "exp_plain", "arpu", {"name": "t-test", "params": {"test_type": "relative"}}
        )
        run_pipeline(warehouse, tables, cuped)
        run_pipeline(warehouse, tables, plain)

        # CUPED rows persist the ORIGINAL per-arm mean/std, so switching the
        # method to t-test is Tier-E reconstructable — no cache needed.
        engine = build_engine(warehouse, tables, cuped, with_cache=False)
        result = engine.recompute("arpu", KnobState("t-test", {"test_type": "relative"}))
        assert result.identity_changed

        expected = persisted(tables, plain, "arpu")
        points = result.pairs[0].points
        assert len(points) == 4
        for point in points:
            assert point.tier == "exact"
            row = expected[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")

    def test_cuped_on_from_a_plain_series_is_a_reload(self, warehouse, tables):
        plain = make_experiment(
            "exp_plain_on", "arpu", {"name": "t-test", "params": {"test_type": "relative"}}
        )
        run_pipeline(warehouse, tables, plain)
        engine = build_engine(warehouse, tables, plain)  # cache has NO covariate

        knobs = KnobState("cuped-t-test", {"test_type": "relative", "covariate_lookback": "7d"})
        result = engine.recompute("arpu", knobs)
        assert result.identity_changed
        assert result.pairs[0].points == []  # nothing servable — /reload's job
        assert classify_knob(get_method_class("cuped-t-test"), "covariate_lookback") == "R"
        surface = engine.knob_surface("arpu")
        cuped_entry = next(m for m in surface["methods"] if m["name"] == "cuped-t-test")
        assert cuped_entry["needs_covariate"] is True  # WP7's ↻ badge substrate
        assert surface["cache"]["covariate_cutoffs"] == []
        # plain t-test rows carry no covariate moments — the client's reload
        # exemption (M9 WP2) must not fire for this series
        assert surface["cache"]["covariate_moment_rows"] is False

    def test_post_normed_bootstrap_without_covariate_is_a_gap_not_a_crash(self, warehouse, tables):
        """post-normed-bootstrap requires cov_array yet has no lookback param —
        the cache gate must use the declared capability, never param names."""
        plain = make_experiment(
            "exp_pn", "arpu", {"name": "t-test", "params": {"test_type": "relative"}}
        )
        run_pipeline(warehouse, tables, plain)
        engine = build_engine(warehouse, tables, plain)
        result = engine.recompute("arpu", KnobState("post-normed-bootstrap", {"n_samples": 100}))
        assert result.pairs[0].points == []  # no covariate in the cache — a gap

    def test_cuped_param_edit_is_exact_over_the_whole_grid(self, warehouse, tables):
        """M9 WP2 (pre-WP2 this pinned "cached cutoffs only"): a non-lookback
        CUPED knob edit reconstructs the WHOLE grid from the persisted
        covariate moments — no cache needed — and matches a fresh pipeline
        run of the edited config at rel-1e-9."""
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        experiment = make_experiment("exp_cuped_edit", "arpu", method)
        run_pipeline(warehouse, tables, experiment)
        edited = make_experiment(
            "exp_cuped_edit_golden",
            "arpu",
            {
                "name": "cuped-t-test",
                "params": {
                    "test_type": "relative",
                    "covariate_lookback": "7d",
                    "calculate_mde": True,
                },
            },
        )
        run_pipeline(warehouse, tables, edited)

        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        knobs = KnobState(
            "cuped-t-test",
            {"test_type": "relative", "covariate_lookback": "7d", "calculate_mde": True},
        )
        result = engine.recompute("arpu", knobs)
        assert result.identity_changed
        points = result.pairs[0].points
        assert len(points) == 4  # the whole grid, not just cached cutoffs
        expected = persisted(tables, edited, "arpu")
        for point in points:
            assert point.tier == "exact"
            row = expected[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue", "mde_1", "mde_2"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")

    def test_cuped_pre_migration_rows_fall_back_to_cached_cutoffs_only(self, warehouse, tables):
        """Backward compat: rows written before the M9 WP1 columns exist
        (NULL covariate moments) keep the pre-WP2 behavior — the session
        cache serves cached cutoffs (Tier S), everything else is a gap;
        nothing raises."""
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        experiment = make_experiment("exp_cuped_premig", "arpu", method)
        run_pipeline(warehouse, tables, experiment)
        for row in warehouse._rows["_ab_results"]:
            for column in ("cov_std_1", "cov_std_2", "corr_coef_1", "corr_coef_2"):
                row[column] = None

        knobs = KnobState(
            "cuped-t-test",
            {"test_type": "relative", "covariate_lookback": "7d", "calculate_mde": True},
        )
        engine = build_engine(warehouse, tables, experiment)
        result = engine.recompute("arpu", knobs)
        assert result.identity_changed
        points = result.pairs[0].points
        cached = engine._session.cached_cutoffs("arpu")
        assert [p.end_ts for p in points] == cached  # gaps everywhere else
        assert all(p.tier == "exact" for p in points)
        assert all(p.mde_1 is not None for p in points)

        # and with no cache at all: same-identity knobs pass persisted rows
        # through (tier "baseline"), never a crash on the NULL columns
        bare = build_engine(warehouse, tables, experiment, with_cache=False)
        passthrough = bare.recompute("arpu", bare.default_knobs("arpu"))
        assert [p.tier for p in passthrough.pairs[0].points] == ["baseline"] * 4

    def test_lookback_change_is_a_reload_not_a_cache_hit(self, warehouse, tables):
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        experiment = make_experiment("exp_cuped_lb", "arpu", method)
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment)

        knobs = KnobState("cuped-t-test", {"test_type": "relative", "covariate_lookback": "14d"})
        result = engine.recompute("arpu", knobs)
        assert result.identity_changed  # a different lookback = a different series
        assert result.pairs[0].points == []  # the cached covariate is 7d — Tier R

    def test_lookback_change_never_reconstructs_from_stale_moments(self, warehouse, tables):
        """The load-bearing M9 WP2 safety check: with NO cache in the way,
        a changed lookback must NOT reconstruct from the persisted covariate
        moments (they belong to the OLD pre-period) — Tier R, not Tier E."""
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        experiment = make_experiment("exp_cuped_lb_guard", "arpu", method)
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)

        knobs = KnobState("cuped-t-test", {"test_type": "relative", "covariate_lookback": "14d"})
        result = engine.recompute("arpu", knobs)
        assert result.identity_changed
        assert result.pairs[0].points == []  # stale moments never serve a new pre-period

    def test_equal_lookback_spellings_reconstruct(self, warehouse, tables):
        """The guard compares SECONDS (the ``_cache_serves`` discipline):
        "1w" and "7d" are the same pre-period, so reconstruction serves —
        even though the STRING param makes it a different method_config_id
        (identity hashes the spelling, not the resolved duration)."""
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        experiment = make_experiment("exp_cuped_lb_alias", "arpu", method)
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)

        knobs = KnobState("cuped-t-test", {"test_type": "relative", "covariate_lookback": "1w"})
        result = engine.recompute("arpu", knobs)
        assert result.identity_changed  # "1w" hashes differently from "7d"
        points = result.pairs[0].points
        assert len(points) == 4
        assert all(p.tier == "exact" for p in points)

    def test_lookback_guard_is_unconditional_at_the_function_level(self, warehouse, tables):
        """The R1 review fix: the lookback comparison in ``_exact_suffstats``
        has NO declared-covariate bypass — a persisted row is frozen, so a
        mismatched live lookback refuses reconstruction regardless of what
        covariate source the LIVE metric config declares."""
        from abkit.tuning.recompute import _exact_suffstats

        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        experiment = make_experiment("exp_cuped_fn_guard", "arpu", method)
        run_pipeline(warehouse, tables, experiment)
        row = next(iter(persisted(tables, experiment, "arpu").values()))
        cuped_cls = get_method_class("cuped-t-test")

        served = _exact_suffstats(cuped_cls, row, {"covariate_lookback": "7d"})
        assert served is not None  # matching lookback reconstructs
        assert _exact_suffstats(cuped_cls, row, {"covariate_lookback": "14d"}) is None
        assert _exact_suffstats(cuped_cls, row, {"covariate_lookback": None}) is None
        assert _exact_suffstats(cuped_cls, row, None) is None  # no params — no claim


# ── Bootstrap: byte-stability through the cache + derived seeds ─────────────


BOOTSTRAP = {"name": "bootstrap", "params": {"test_type": "relative", "n_samples": 200}}


class TestBootstrap:
    def test_unchanged_knobs_reproduce_persisted_rows_byte_exactly(self, warehouse, tables):
        experiment = make_experiment("exp_boot", "arpu", BOOTSTRAP)
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment)
        result = engine.recompute("arpu", engine.default_knobs("arpu"))

        baseline = persisted(tables, experiment, "arpu")
        points = result.pairs[0].points
        assert len(points) == 4
        assert all(point.tier == "exact" for point in points)
        for point in points:
            row = baseline[("control", "treatment", point.end_ts)]
            # BYTE equality — same canonical unit order (D11), same derived seed
            assert point.effect == row["effect"]
            assert point.left_bound == row["left_bound"]
            assert point.right_bound == row["right_bound"]
            assert point.pvalue == row["pvalue"]

    def test_supplied_seed_is_ignored_with_a_warning(self, warehouse, tables):
        experiment = make_experiment("exp_boot_seed", "arpu", BOOTSTRAP)
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment)
        knobs = KnobState("bootstrap", {**BOOTSTRAP["params"], "seed": 12345}, alpha=0.05)
        result = engine.recompute("arpu", knobs)

        assert any("seed is derived per row" in w for w in result.warnings)
        baseline = persisted(tables, experiment, "arpu")
        for point in result.pairs[0].points:
            row = baseline[("control", "treatment", point.end_ts)]
            assert point.effect == row["effect"]
            assert point.left_bound == row["left_bound"]

    def test_alpha_change_widens_the_percentile_ci_but_keeps_the_pvalue(self, warehouse, tables):
        experiment = make_experiment("exp_boot_alpha", "arpu", BOOTSTRAP)
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment)
        result = engine.recompute("arpu", KnobState("bootstrap", BOOTSTRAP["params"], alpha=0.01))

        baseline = persisted(tables, experiment, "arpu")
        points = result.pairs[0].points
        assert len(points) == 4  # every cutoff is cached under the default budget
        for point in points:
            assert point.tier == "exact"  # re-resampled, not normal-inverted
            row = baseline[("control", "treatment", point.end_ts)]
            assert point.pvalue == row["pvalue"]  # the sign p-value is α-free
            assert point.left_bound < row["left_bound"]  # 99% CI is wider
            assert point.right_bound > row["right_bound"]

    def test_recompute_is_deterministic(self, warehouse, tables):
        experiment = make_experiment("exp_boot_det", "arpu", BOOTSTRAP)
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment)
        knobs = KnobState("bootstrap", {"test_type": "relative", "n_samples": 300})
        first = engine.recompute("arpu", knobs)
        second = engine.recompute("arpu", knobs)
        for p1, p2 in zip(first.pairs[0].points, second.pairs[0].points, strict=True):
            assert (p1.effect, p1.left_bound, p1.right_bound, p1.pvalue) == (
                p2.effect,
                p2.left_bound,
                p2.right_bound,
                p2.pvalue,
            )


# ── D11: order permutation ───────────────────────────────────────────────────


class TestOrderPermutation:
    def test_shuffled_warehouse_reproduces_the_same_bootstrap_rows(self):
        """Two pipelines over physically different read orders persist
        byte-identical bootstrap rows — the D11 canonical sort at work."""

        def run_on(shuffled: bool):
            wh = SyntheticWarehouse(shuffled=shuffled)
            seed_cohort(wh)
            seed_all_events(wh)
            tables = InternalTablesManager(wh)
            experiment = make_experiment("exp_perm", "arpu", BOOTSTRAP)
            run_pipeline(wh, tables, experiment)
            return persisted(tables, experiment, "arpu")

        sorted_rows = run_on(shuffled=False)
        shuffled_rows = run_on(shuffled=True)
        assert sorted_rows.keys() == shuffled_rows.keys()
        for key, row in sorted_rows.items():
            other = shuffled_rows[key]
            for column in ("effect", "left_bound", "right_bound", "pvalue", "value_1", "std_1"):
                assert row[column] == other[column], f"{column}@{key}"

    def test_session_cache_units_are_canonically_sorted(self, warehouse, tables):
        experiment = make_experiment("exp_sorted", "arpu", BOOTSTRAP)
        run_pipeline(warehouse, tables, experiment)
        warehouse.shuffled = True  # the session load now reads scrambled rows
        session = build_session(warehouse, tables, experiment)
        for (_, _), loaded in session.cache.items():
            for units in loaded.units_by_variant.values():
                assert list(units) == sorted(units)


# ── Cache budget: clamping + the degraded suffstats-only mode ────────────────


class TestCacheBudget:
    def test_older_cutoffs_fall_out_first_and_pass_through_as_baseline(self, warehouse, tables):
        experiment = make_experiment("exp_budget", "arpu", BOOTSTRAP)
        run_pipeline(warehouse, tables, experiment)
        # one arpu cutoff = 240 values (120 units × 2 arms × 1 role)
        session = build_session(warehouse, tables, experiment, budget=250)
        assert session.cache_disabled_reason is None
        assert len(session.cache) == 1  # the latest cutoff only
        assert any("budget reached" in w for w in session.warnings)

        engine = RecomputeEngine(session)
        result = engine.recompute("arpu", engine.default_knobs("arpu"))
        points = result.pairs[0].points
        assert [p.tier for p in points] == ["baseline", "baseline", "baseline", "exact"]
        baseline = persisted(tables, experiment, "arpu")
        for point in points:  # pass-through must equal the persisted numbers
            row = baseline[("control", "treatment", point.end_ts)]
            assert point.effect == row["effect"]
            assert point.left_bound == row["left_bound"]

        # an identity edit can only be served where the cache reaches
        edited = engine.recompute(
            "arpu", KnobState("bootstrap", {"test_type": "relative", "n_samples": 300})
        )
        assert [p.end_ts for p in edited.pairs[0].points] == session.cached_cutoffs("arpu")

    def test_over_budget_latest_degrades_to_suffstats_only(self, warehouse, tables):
        experiment = make_experiment("exp_degraded", "arpu", BOOTSTRAP)
        run_pipeline(warehouse, tables, experiment)
        session = build_session(warehouse, tables, experiment, budget=100)
        assert session.cache == {}
        assert session.cache_disabled_reason is not None
        assert "suffstats-only" in session.cache_disabled_reason

        engine = RecomputeEngine(session)
        result = engine.recompute(
            "arpu", KnobState("bootstrap", {"test_type": "relative", "n_samples": 300})
        )
        assert result.pairs[0].points == []  # bootstrap disabled, with the reason
        assert any("suffstats-only" in w for w in result.warnings)


# ── Knob surface / tier classification ───────────────────────────────────────


class TestKnobSurface:
    def test_methods_filtered_by_metric_type_and_paired_excluded(self, warehouse, tables):
        experiment = make_experiment("exp_surface", "arpu", {"name": "t-test", "params": {}})
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        surface = engine.knob_surface("arpu")
        names = {method["name"] for method in surface["methods"]}
        assert names == {
            "t-test",
            "cuped-t-test",
            "bootstrap",
            "poisson-bootstrap",
            "post-normed-bootstrap",
        }
        assert surface["configured"]["method"] == "t-test"
        assert surface["configured"]["method_config_id"] == (
            experiment.comparisons[0].method.method_config_id
        )

    def test_tier_classification_table(self):
        ttest = get_method_class("t-test")
        cuped = get_method_class("cuped-t-test")
        boot = get_method_class("bootstrap")
        ztest = get_method_class("z-test")
        ratio = get_method_class("ratio-delta")

        assert {classify_knob(ttest, s.name) for s in ttest.param_specs} == {"E"}
        assert {classify_knob(ztest, s.name) for s in ztest.param_specs} == {"E"}
        assert {classify_knob(ratio, s.name) for s in ratio.param_specs} == {"E"}
        # M9 WP2: every CUPED knob is Tier E except the pre-period itself
        assert classify_knob(cuped, "covariate_lookback") == "R"
        assert classify_knob(cuped, "test_type") == "E"
        assert classify_knob(cuped, "calculate_mde") == "E"
        assert {classify_knob(boot, s.name) for s in boot.param_specs} == {"S"}

        assert alpha_knob_tier(ttest) == "E"
        assert alpha_knob_tier(ztest) == "E"
        assert alpha_knob_tier(ratio) == "E"
        assert alpha_knob_tier(cuped) == "E"  # M9 WP2 — was the α-inversion tier
        assert alpha_knob_tier(boot) == "S"

    def test_param_specs_ride_verbatim_with_identity_flags(self, warehouse, tables):
        experiment = make_experiment("exp_specs", "arpu", BOOTSTRAP)
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        surface = engine.knob_surface("arpu")
        boot = next(m for m in surface["methods"] if m["name"] == "bootstrap")
        by_name = {p["name"]: p for p in boot["params"]}
        assert by_name["seed"]["identity"] is False
        assert by_name["n_samples"]["identity"] is True
        assert by_name["n_samples"]["default"] == 1000
        assert by_name["weight_method"]["choices"] == ["min", "mean"]


# ── Identity: one canonical hashing path ─────────────────────────────────────


class TestLiveIdentity:
    @pytest.mark.parametrize(
        ("name", "params"),
        [
            ("t-test", {}),
            ("t-test", {"test_type": "absolute"}),
            ("t-test", {"calculate_mde": True, "power": 0.9}),
            ("bootstrap", {"n_samples": 500, "stat": "median"}),
            ("cuped-t-test", {"covariate_lookback": "14d"}),
        ],
    )
    def test_live_hash_equals_the_config_model_hash(self, warehouse, tables, name, params):
        experiment = make_experiment("exp_id", "arpu", {"name": "t-test", "params": {}})
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        result = engine.recompute("arpu", KnobState(name, params))
        assert result.method_config_id == MethodConfig(name=name, params=params).method_config_id


# ── Calibration (D3) ─────────────────────────────────────────────────────────


def aa_row(**overrides) -> dict:
    row = {
        "metric": "arpu",
        "method_config_id": "abc",
        "alpha": 0.05,
        "status": "success",
        "fpr": 0.048,
        "peeking_fpr": 0.21,
        "created_at": datetime(2026, 7, 1),
    }
    row.update(overrides)
    return row


class TestCalibration:
    def test_empty_is_uncalibrated(self):
        status = find_calibration([], "arpu", "abc", 0.05, budget=0.075)
        assert status.state == "uncalibrated"
        assert "abk validate" in status.headline

    def test_other_identity_does_not_count(self):
        status = find_calibration([aa_row(method_config_id="other")], "arpu", "abc", 0.05)
        assert status.state == "uncalibrated"

    def test_failed_and_fprless_rows_do_not_count(self):
        rows = [aa_row(status="failed"), aa_row(fpr=None)]
        assert find_calibration(rows, "arpu", "abc", 0.05).state == "uncalibrated"

    def test_calibrated_within_budget(self):
        status = find_calibration([aa_row()], "arpu", "abc", 0.05, budget=0.075)
        assert status.state == "calibrated"
        assert status.fpr == 0.048
        assert status.peeking_fpr == 0.21
        assert status.over_budget is False
        assert "FPR 4.8%" in status.headline

    def test_calibrated_over_budget_is_loud(self):
        status = find_calibration([aa_row(fpr=0.2)], "arpu", "abc", 0.05, budget=0.075)
        assert status.over_budget is True
        assert "over the" in status.headline

    def test_alpha_mismatch_downgrades(self):
        status = find_calibration([aa_row()], "arpu", "abc", 0.01, budget=0.015)
        assert status.state == "alpha_mismatch"
        assert status.calibrated_alpha == 0.05
        assert "current α=0.01" in status.headline

    def test_newest_run_wins(self):
        rows = [
            aa_row(fpr=0.03, created_at=datetime(2026, 7, 2)),
            aa_row(fpr=0.09, created_at=datetime(2026, 7, 1)),
        ]
        assert find_calibration(rows, "arpu", "abc", 0.05).fpr == 0.03

    def test_budget_resolver_project_then_alpha_rule(self):
        assert resolve_fpr_budget(PROJECT, 0.05) == pytest.approx(0.075)
        project = ProjectConfig.model_validate(
            {"name": "p", "default_profile": "dev", "statistics": {"aa_fpr_budget": 0.06}}
        )
        assert resolve_fpr_budget(project, 0.05) == 0.06

    def test_budget_resolver_metric_override_wins(self):
        """The metric arm (D12): metric.aa_fpr_budget beats project + the α rule."""
        from abkit.config import MetricConfig

        metric = MetricConfig(
            name="arpu",
            type="sample",
            columns={"variant": "g", "value": "v"},
            query="SELECT 1",
            aa_fpr_budget=0.09,
        )
        # metric override beats the project default and the α×1.5 fallback
        project = ProjectConfig.model_validate(
            {"name": "p", "default_profile": "dev", "statistics": {"aa_fpr_budget": 0.06}}
        )
        assert resolve_fpr_budget(project, 0.05, metric) == 0.09
        assert resolve_fpr_budget(PROJECT, 0.05, metric) == 0.09
        # a metric with no override falls through to the project/α rule
        plain = MetricConfig(
            name="ctr", type="sample", columns={"variant": "g", "value": "v"}, query="SELECT 1"
        )
        assert resolve_fpr_budget(PROJECT, 0.05, plain) == pytest.approx(0.075)

    def test_engine_keys_the_chip_by_the_live_knob_state(self, warehouse, tables):
        experiment = make_experiment("exp_chip", "arpu", {"name": "t-test", "params": {}})
        run_pipeline(warehouse, tables, experiment)
        method_config_id = experiment.comparisons[0].method.method_config_id
        tables.save_aa_run(
            {
                "experiment": "exp_chip",
                "run_id": "r1",
                "metric": "arpu",
                "method_name": "t-test",
                "method_params": "{}",
                "method_config_id": method_config_id,
                "mode": "fpr",
                "iterations": 1000,
                "alpha": 0.05,
                "injected_effect": None,
                "fpr": 0.049,
                "peeking_fpr": None,
                "power": None,
                "achieved_mde": None,
                "coverage": None,
                "effect_exaggeration": None,
                "tau2": None,
                "fpr_sequential": None,
                "peeking_fpr_sequential": None,
                "power_sequential": None,
                "coverage_sequential": None,
                "effect_exaggeration_sequential": None,
                "ci_width": None,
                "ci_width_sequential": None,
                "verdict": "ok",
                "details": "{}",
                "status": "success",
                "error_message": None,
            }
        )
        engine = build_engine(warehouse, tables, experiment, with_cache=False)

        calibrated = engine.recompute("arpu", engine.default_knobs("arpu")).calibration
        assert calibrated.state == "calibrated"

        # an alpha edit downgrades the chip (gates like uncalibrated, D3)
        mismatched = engine.recompute("arpu", KnobState("t-test", {}, alpha=0.01)).calibration
        assert mismatched.state == "alpha_mismatch"

        # an identity edit flips it to uncalibrated — that IS the staleness
        edited = engine.recompute(
            "arpu", KnobState("t-test", {"test_type": "absolute"})
        ).calibration
        assert edited.state == "uncalibrated"


# ── Chips ────────────────────────────────────────────────────────────────────


class TestChips:
    def test_power_chip_with_min_effect(self, warehouse, tables):
        experiment = make_experiment(
            "exp_power",
            "arpu",
            {"name": "t-test", "params": {"test_type": "relative"}},
            min_effect=0.05,
        )
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        result = engine.recompute("arpu", engine.default_knobs("arpu"))
        chips = result.pairs[0].chips
        latest = result.pairs[0].points[-1]
        assert chips["lift"] == latest.effect
        assert chips["pvalue"] == latest.pvalue
        assert chips["ci_half"] == pytest.approx((latest.right_bound - latest.left_bound) / 2.0)
        assert chips["power_note"] is None
        assert 0.0 < chips["power"] <= 1.0

    def test_power_chip_honest_without_min_effect(self, warehouse, tables):
        experiment = make_experiment("exp_nomin", "arpu", {"name": "t-test", "params": {}})
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        chips = engine.recompute("arpu", engine.default_knobs("arpu")).pairs[0].chips
        assert chips["power"] is None
        assert "min_effect" in chips["power_note"]

    def test_power_chip_honest_for_capability_less_families(self, warehouse, tables):
        experiment = make_experiment(
            "exp_nocap",
            "ctr",
            {"name": "ratio-delta", "params": {}},
            min_effect=0.05,
        )
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        chips = engine.recompute("ctr", engine.default_knobs("ctr")).pairs[0].chips
        assert chips["power"] is None
        assert "no power/MDE capability" in chips["power_note"]

    def test_cuped_power_chip_serves_cache_free_from_the_reconstruction(self, warehouse, tables):
        """The R1 review fix: a Tier-E CUPED point's power chip reads the
        control-arm correlation off the reconstructed result — no session
        cache required — so the chip agrees with the exact point beside it.
        The value matches the cached path's ``_control_corr`` answer."""
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        experiment = make_experiment("exp_cuped_power", "arpu", method, min_effect=0.05)
        run_pipeline(warehouse, tables, experiment)

        bare = build_engine(warehouse, tables, experiment, with_cache=False)
        chips = bare.recompute("arpu", bare.default_knobs("arpu")).pairs[0].chips
        assert chips["tier"] == "exact"
        assert chips["power_note"] is None
        assert 0.0 < chips["power"] <= 1.0

        cached = build_engine(warehouse, tables, experiment)
        cached_chips = cached.recompute("arpu", cached.default_knobs("arpu")).pairs[0].chips
        assert chips["power"] == pytest.approx(cached_chips["power"], rel=1e-9)

    def test_cuped_power_chip_still_honest_on_pre_migration_rows(self, warehouse, tables):
        """Pre-migration rows (no moments) with no cache keep the honest None
        chip — with the note naming both missing sources."""
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        experiment = make_experiment("exp_cuped_power_legacy", "arpu", method, min_effect=0.05)
        run_pipeline(warehouse, tables, experiment)
        for row in warehouse._rows["_ab_results"]:
            for column in ("cov_std_1", "cov_std_2", "corr_coef_1", "corr_coef_2"):
                row[column] = None
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        chips = engine.recompute("arpu", engine.default_knobs("arpu")).pairs[0].chips
        assert chips["power"] is None
        assert "covariate correlation" in chips["power_note"]

    def test_cuped_surface_reports_covariate_moment_rows(self, warehouse, tables):
        """The knob-surface flag behind the client's reload exemption: True
        for a post-migration CUPED series, False once the moments are gone."""
        method = {
            "name": "cuped-t-test",
            "params": {"test_type": "relative", "covariate_lookback": "7d"},
        }
        experiment = make_experiment("exp_cuped_surface", "arpu", method)
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        assert engine.knob_surface("arpu")["cache"]["covariate_moment_rows"] is True

        for row in warehouse._rows["_ab_results"]:
            for column in ("cov_std_1", "cov_std_2", "corr_coef_1", "corr_coef_2"):
                row[column] = None
        legacy = build_engine(warehouse, tables, experiment, with_cache=False)
        assert legacy.knob_surface("arpu")["cache"]["covariate_moment_rows"] is False


# ── Validation & quarantine surfacing ────────────────────────────────────────


class TestValidationSurface:
    @pytest.fixture
    def engine(self, warehouse, tables):
        experiment = make_experiment("exp_val", "arpu", {"name": "t-test", "params": {}})
        run_pipeline(warehouse, tables, experiment)
        return build_engine(warehouse, tables, experiment, with_cache=False)

    def test_quarantined_method_surfaces_verbatim(self, engine):
        with pytest.raises(QuarantinedMethodError, match="post-normalisation"):
            engine.recompute("arpu", KnobState("poisson-post-normed-bootstrap", {}))

    def test_unknown_method(self, engine):
        with pytest.raises(UnknownMethodError):
            engine.recompute("arpu", KnobState("no-such-method", {}))

    def test_bad_param(self, engine):
        with pytest.raises(MethodParamError):
            engine.recompute("arpu", KnobState("t-test", {"test_type": "sideways"}))

    def test_bad_alpha(self, engine):
        with pytest.raises(MethodParamError):
            engine.recompute("arpu", KnobState("t-test", {}, alpha=1.5))

    def test_unknown_metric_names_the_namespace(self, engine):
        with pytest.raises(KeyError, match="not a configured comparison"):
            engine.recompute("nope", KnobState("t-test", {}))

    def test_cross_kind_method_is_gated_not_silently_wrong(self, warehouse, tables):
        """The analyze-parity gate (review finding, empirically reproduced):
        t-test on a fraction series would misread the persisted SE as a sample
        std and collapse the CI ~nobs-fold under a tier='exact' label."""
        experiment = make_experiment("exp_gate", "conversion", {"name": "z-test", "params": {}})
        run_pipeline(warehouse, tables, experiment)
        engine = build_engine(warehouse, tables, experiment, with_cache=False)
        with pytest.raises(MethodParamError, match="expects a 'sample' metric"):
            engine.recompute("conversion", KnobState("t-test", {}))

    def test_paired_method_is_gated(self, engine):
        with pytest.raises(MethodParamError, match="paired design"):
            engine.recompute("arpu", KnobState("paired-t-test", {}))

    def test_non_mean_stat_series_never_reconstructs_tier_e(self, warehouse, tables):
        """A median-bootstrap series persists the MEDIAN in value_i — mean-based
        suffstats reconstruction from those rows would be silently wrong. With
        a cache the t-test knob recomputes from real arrays (correct); without
        one it must yield gaps, never fake 'exact' numbers off the median."""
        median_boot = make_experiment(
            "exp_median",
            "arpu",
            {
                "name": "bootstrap",
                "params": {"test_type": "relative", "n_samples": 100, "stat": "median"},
            },
        )
        plain = make_experiment(
            "exp_median_ref", "arpu", {"name": "t-test", "params": {"test_type": "relative"}}
        )
        run_pipeline(warehouse, tables, median_boot)
        run_pipeline(warehouse, tables, plain)

        blind = build_engine(warehouse, tables, median_boot, with_cache=False)
        assert blind.recompute("arpu", KnobState("t-test", {})).pairs[0].points == []

        engine = build_engine(warehouse, tables, median_boot)  # full cache
        result = engine.recompute("arpu", KnobState("t-test", {"test_type": "relative"}))
        expected = persisted(tables, plain, "arpu")
        points = result.pairs[0].points
        assert len(points) == 4  # Tier S over real arrays serves every cutoff
        for point in points:
            row = expected[("control", "treatment", point.end_ts)]
            for key in ("effect", "left_bound", "right_bound", "pvalue"):
                assert_close(getattr(point, key), row[key], f"{key}@{point.end_ts}")


# ── Demoted rows stay gaps ───────────────────────────────────────────────────


class TestDemotedRows:
    def test_demoted_rows_pass_through_untouched(self, warehouse, tables):
        """Demoted rows ride along flagged (NULL test columns, real sizes) —
        never dropped (the chart would lose the greyed segment), never faked."""
        strict = ProjectConfig.model_validate(
            {"name": "p", "default_profile": "dev", "limits": {"min_units_per_arm": 1000}}
        )
        experiment = make_experiment("exp_demoted", "arpu", {"name": "t-test", "params": {}})
        run_pipeline(warehouse, tables, experiment, project=strict)
        rows = tables.load_results("exp_demoted", metric="arpu")
        assert rows and all(row["insufficient_data"] for row in rows)

        session = load_session(experiment, METRICS, strict, tables, loader=None)
        engine = RecomputeEngine(session)
        result = engine.recompute("arpu", engine.default_knobs("arpu"))
        points = result.pairs[0].points
        assert len(points) == len(rows)
        assert all(point.tier == "baseline" for point in points)
        assert all(point.insufficient for point in points)
        assert all(point.effect is None and point.pvalue is None for point in points)
        assert all(point.size_1 and point.size_1 > 0 for point in points)
        # chips must not pretend an all-demoted series carries inference
        assert result.pairs[0].chips["lift"] is None
        assert "no recomputable" in result.pairs[0].chips["power_note"]
