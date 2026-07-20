"""Query template + packaged macro render tests (declarative-config §4/§5)."""

from __future__ import annotations

from datetime import datetime

import pytest

from abkit.loaders.query_template import (
    QueryTemplate,
    RenderWindow,
    TemplateRenderError,
    build_builtins,
)


def make_builtins(**overrides):
    kwargs = {
        "experiment_id": "signup_test",
        "unit_key": "user_id",
        "variants": ["control", "treatment"],
        "added_filters": "",
        "window": RenderWindow(start_ts=datetime(2024, 7, 1), end_ts=datetime(2024, 7, 8)),
        "data_database": "analytics",
        "internal_database": "abkit_internal",
        "exposures_table": "_ab_exposures",
        "dialect": "clickhouse",
    }
    kwargs.update(overrides)
    return build_builtins(**kwargs)


MACRO_SQL = """
{% import 'abkit_assignment.jinja' as ab %}
SELECT
    {{ ab.variant_col() }} AS variant,
    user_id,
    sum(gross_usd) AS gross_usd
FROM {{ data_database }}.user_revenue
{{ ab.exposed_units() }}
GROUP BY variant, user_id
"""


class TestBuiltins:
    def test_authoritative_table(self):
        builtins = make_builtins()
        assert builtins["ab_experiment_id"] == "signup_test"
        assert builtins["ab_start_date"] == "2024-07-01"
        # end_ts is EXCLUSIVE: the last covered date is July 7
        assert builtins["ab_end_date"] == "2024-07-07"
        assert builtins["ab_start_ts"] == "2024-07-01 00:00:00"
        assert builtins["ab_end_ts"] == "2024-07-08 00:00:00"
        assert builtins["ab_unit_key"] == "user_id"
        assert builtins["ab_variants"] == ["control", "treatment"]
        assert builtins["data_database"] == "analytics"
        assert builtins["internal_database"] == "abkit_internal"
        assert builtins["ab_exposures_table"] == "abkit_internal._ab_exposures"

    def test_cov_window_bounds(self):
        builtins = make_builtins(
            cov_window=RenderWindow(start_ts=datetime(2024, 6, 17), end_ts=datetime(2024, 7, 1))
        )
        assert builtins["ab_cov_start"] == "2024-06-17"
        assert builtins["ab_cov_end"] == "2024-06-30"

    def test_sub_day_window_dates(self):
        """A 12:00-ending window's end_date is the same day (partition bound)."""
        builtins = make_builtins(
            window=RenderWindow(start_ts=datetime(2024, 7, 1), end_ts=datetime(2024, 7, 3, 12))
        )
        assert builtins["ab_end_date"] == "2024-07-03"
        assert builtins["ab_end_ts"] == "2024-07-03 12:00:00"


DIRECT_SQL = "SELECT user_id, variant, exposure_ts, stratum FROM analytics.assignments"


class TestCohortSource:
    """The ab_cohort_source builtin — m8-implementation-plan.md WP3."""

    def test_copy_mode_clickhouse_reads_final(self):
        assert make_builtins()["ab_cohort_source"] == "abkit_internal._ab_exposures FINAL"

    def test_copy_mode_sql_backends_have_no_final(self):
        for dialect in ("postgres", "mysql"):
            builtins = make_builtins(dialect=dialect)
            assert builtins["ab_cohort_source"] == "abkit_internal._ab_exposures"

    def test_direct_mode_wraps_the_rendered_sql_verbatim(self):
        builtins = make_builtins(direct_source_sql=DIRECT_SQL)
        source = builtins["ab_cohort_source"]
        assert DIRECT_SQL in source  # the rendered assignment SQL, byte-verbatim
        assert source.startswith(
            "(SELECT user_id AS unit_id, variant, MIN(exposure_ts) AS exposure_ts, "
            "MIN(stratum) AS stratum, 'signup_test' AS experiment FROM ("
        )
        # the outer alias is mandatory — PG/MySQL reject an unaliased derived table
        assert source.endswith("GROUP BY user_id, variant) _abk_cohort")
        # dedup lives in the GROUP BY, never in a table engine
        assert "FINAL" not in source
        # ab_exposures_table stays available for external consumers in BOTH modes
        assert builtins["ab_exposures_table"] == "abkit_internal._ab_exposures"

    def test_direct_mode_stratum_less_source_projects_null(self):
        source = make_builtins(direct_source_sql=DIRECT_SQL, has_stratum=False)["ab_cohort_source"]
        assert "NULL AS stratum" in source
        assert "MIN(stratum)" not in source

    def test_direct_mode_survives_trailing_terminator(self):
        """The WP2 review finding, shared: a trailing ';' or line comment in the
        rendered assignment SQL must not break the FROM (...) wrap."""
        stripped = make_builtins(direct_source_sql=DIRECT_SQL + " ;\n")["ab_cohort_source"]
        assert ";" not in stripped
        commented = make_builtins(direct_source_sql=DIRECT_SQL + "\n-- provenance note")[
            "ab_cohort_source"
        ]
        assert "\n) _abk_raw" in commented  # the closing paren escapes the comment line

    def test_direct_mode_dedup_is_in_lockstep_with_the_validation_pushdown(self):
        """build_builtins' direct-mode aggregation and the WP2 validation
        pushdown (exposure_source._pushdown_sql) must dedupe IDENTICALLY —
        same MIN() aggregates, same derived-table wrap, same GROUP BY — or the
        cohort the metrics join could diverge from the cohort the SRM gate
        counted (the §0.5(e) semantic-parity contract both docstrings cite)."""
        from abkit.loaders.exposure_source import _pushdown_sql
        from abkit.loaders.query_template import as_derived_table

        shared_core = f"FROM {as_derived_table(DIRECT_SQL, '_abk_raw')} GROUP BY user_id, variant"
        pushdown = _pushdown_sql("user_id", DIRECT_SQL, has_stratum=True)
        builtin = make_builtins(direct_source_sql=DIRECT_SQL)["ab_cohort_source"]
        for fragment in (pushdown, builtin):
            assert shared_core in fragment
            assert "MIN(exposure_ts) AS exposure_ts" in fragment
            assert "MIN(stratum) AS stratum" in fragment


class TestRender:
    def test_strict_undefined_hard_fails(self):
        with pytest.raises(TemplateRenderError, match="undefined"):
            QueryTemplate().render("SELECT {{ not_declared }}", make_builtins())

    def test_builtin_shadowing_raises(self):
        """Deliberate deviation from detectkit: shadowing ab_end_ts must not be silent."""
        with pytest.raises(TemplateRenderError, match="must not shadow"):
            QueryTemplate().render("SELECT 1", make_builtins(), context={"ab_end_ts": "2030-01-01"})

    def test_context_extends(self):
        sql = QueryTemplate().render(
            "SELECT * FROM {{ my_table }}", make_builtins(), context={"my_table": "t"}
        )
        assert sql == "SELECT * FROM t"

    def test_syntax_error_is_typed(self):
        with pytest.raises(TemplateRenderError, match="Invalid template syntax"):
            QueryTemplate().render("SELECT {% if %}", make_builtins())


class TestPackagedMacro:
    def test_exposed_units_join_and_windows(self):
        sql = QueryTemplate().render(MACRO_SQL, make_builtins())
        assert "INNER JOIN" in sql
        assert "abkit_internal._ab_exposures FINAL" in sql  # CH dedup
        assert "WHERE experiment = 'signup_test'" in sql
        # collision-proof aliases + a dialect cast of the fact-side key
        assert "unit_id     AS _abk_unit_id" in sql
        assert "_abk_exposures._abk_unit_id = toString(user_id)" in sql
        # BOTH the coarse date predicate and the precise half-open ts filter
        assert "event_date >= '2024-07-01'" in sql
        assert "event_date <= '2024-07-07'" in sql
        assert "event_time >= '2024-07-01 00:00:00'" in sql
        assert "event_time < '2024-07-08 00:00:00'" in sql
        assert "event_time >= _abk_exposures._abk_exposure_ts" in sql
        assert "_abk_exposures._abk_variant" in sql

    def test_no_final_on_sql_backends(self):
        sql = QueryTemplate().render(MACRO_SQL, make_builtins(dialect="postgres"))
        assert "FINAL" not in sql
        assert "INNER JOIN" in sql
        assert "CAST(user_id AS TEXT)" in sql  # PG cast of the fact-side key

    def test_mysql_cast(self):
        sql = QueryTemplate().render(MACRO_SQL, make_builtins(dialect="mysql"))
        assert "CAST(user_id AS CHAR)" in sql

    def test_added_filters_never_leaks_into_metric_scans(self):
        """added_filters scopes the ASSIGNMENT query only — auto-injecting it
        into every metric's fact WHERE would silently change numbers."""
        sql = QueryTemplate().render(MACRO_SQL, make_builtins(added_filters="AND country = 'US'"))
        assert "country = 'US'" not in sql
        # ...but the builtin stays available for assignment SQL
        assignment = QueryTemplate().render(
            "SELECT 1 FROM a WHERE 1=1 {{ ab_added_filters }}",
            make_builtins(added_filters="AND country = 'US'"),
        )
        assert "AND country = 'US'" in assignment

    def test_covariate_render_drops_exposure_filter(self):
        """ab_apply_exposure_filter=False — the pre-period precedes exposure."""
        sql = QueryTemplate().render(MACRO_SQL, make_builtins(apply_exposure_filter=False))
        assert "exposure_ts" not in sql.split("WHERE experiment")[1]

    def test_custom_event_columns(self):
        sql = QueryTemplate().render(
            """{% import 'abkit_assignment.jinja' as ab %}
SELECT {{ ab.variant_col() }} AS v, user_id FROM t {{ ab.exposed_units('dt', 'ts') }}""",
            make_builtins(),
        )
        assert "dt >= '2024-07-01'" in sql
        assert "ts < '2024-07-08 00:00:00'" in sql

    def test_stratum_col(self):
        sql = QueryTemplate().render(
            """{% import 'abkit_assignment.jinja' as ab %}
SELECT {{ ab.stratum_col() }} AS s FROM t {{ ab.exposed_units() }}""",
            make_builtins(),
        )
        assert "_abk_exposures._abk_stratum" in sql

    def test_copy_mode_render_is_byte_identical_to_pre_wp3(self):
        """§0.4.3 applied to the render itself: the pre-WP3 FROM line ended in
        a '{% endif %}' BLOCK tag, so trim_blocks ate the newline and collapsed
        FROM + WHERE onto one physical line; the comment tag on the new line
        must reproduce that byte-exactly (a WP3 review finding — a bare
        '{{ ab_cohort_source }}' line keeps its newline and moves every
        provenance copy of the executed SQL)."""
        sql = QueryTemplate().render(MACRO_SQL, make_builtins())
        assert "FROM abkit_internal._ab_exposures FINAL    WHERE experiment = 'signup_test'" in sql
        pg = QueryTemplate().render(MACRO_SQL, make_builtins(dialect="postgres"))
        assert "FROM abkit_internal._ab_exposures    WHERE experiment = 'signup_test'" in pg

    def test_direct_mode_join(self):
        sql = QueryTemplate().render(MACRO_SQL, make_builtins(direct_source_sql=DIRECT_SQL))
        assert "FROM (SELECT user_id AS unit_id" in sql
        assert DIRECT_SQL in sql
        # the retained WHERE experiment predicate matches the projected literal
        assert "'signup_test' AS experiment" in sql
        assert "WHERE experiment = 'signup_test'" in sql
        assert "abkit_internal._ab_exposures" not in sql  # no persisted-table read

    def test_two_modes_differ_only_in_the_cohort_source(self):
        """The WP3 rendered-SQL snapshot gate: outside the ab_cohort_source
        substitution the two modes render byte-identically."""
        copy_builtins = make_builtins()
        direct_builtins = make_builtins(direct_source_sql=DIRECT_SQL)
        copy_sql = QueryTemplate().render(MACRO_SQL, copy_builtins)
        direct_sql = QueryTemplate().render(MACRO_SQL, direct_builtins)
        direct_fragment = direct_builtins["ab_cohort_source"]
        assert direct_sql.count(direct_fragment) == 1
        assert direct_sql.replace(direct_fragment, copy_builtins["ab_cohort_source"]) == copy_sql

    def test_stratum_col_renders_against_a_stratum_less_direct_source(self):
        """ab.stratum_col() must stay renderable when the direct source has no
        stratum column — the builtin projects NULL AS stratum, so StrictUndefined
        (and the DB's unknown-column error) never trips."""
        sql = QueryTemplate().render(
            """{% import 'abkit_assignment.jinja' as ab %}
SELECT {{ ab.stratum_col() }} AS s FROM t {{ ab.exposed_units() }}""",
            make_builtins(direct_source_sql=DIRECT_SQL, has_stratum=False),
        )
        assert "_abk_exposures._abk_stratum" in sql
        assert "NULL AS stratum" in sql

    def test_ab_cov_builtins_absent_without_cov_window(self):
        """Referencing ab_cov_* without a covariate hard-fails (StrictUndefined)
        instead of rendering the literal string 'None' into SQL."""
        with pytest.raises(TemplateRenderError, match="ab_cov_start"):
            QueryTemplate().render("SELECT '{{ ab_cov_start }}'", make_builtins())
        builtins = make_builtins(
            cov_window=RenderWindow(start_ts=datetime(2024, 6, 17), end_ts=datetime(2024, 7, 1))
        )
        sql = QueryTemplate().render("SELECT '{{ ab_cov_start }}', '{{ ab_cov_end }}'", builtins)
        assert "2024-06-17" in sql and "2024-06-30" in sql
