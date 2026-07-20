"""SQL query templating with Jinja2 — the ``ab_*`` render surface.

Renders metric/assignment SQL with the authoritative built-ins table
(declarative-config.md §5) under ``StrictUndefined`` (an undeclared variable
hard-fails), and loads the PACKAGED assignment macro
(``{% import 'abkit_assignment.jinja' as ab %}``) from ``abkit.loaders.templates``
via ``PackageLoader`` so correctness-critical cohort/window/dedup SQL is never
hand-repeated.

Deliberate deviation from the detectkit donor (recorded in CHANGELOG):
built-ins WIN over caller context — a context key shadowing ``ab_end_ts``
would silently change the analysis window, so a collision raises
:class:`TemplateRenderError` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from jinja2 import (
    Environment,
    PackageLoader,
    StrictUndefined,
    TemplateSyntaxError,
)


class TemplateRenderError(Exception):
    """Raised for template syntax errors, undefined variables, or built-in shadowing."""


#: SQL-safe formats shared by ClickHouse / PostgreSQL / MySQL
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_DATE_FORMAT = "%Y-%m-%d"


@dataclass(frozen=True)
class RenderWindow:
    """One cumulative window's render-time bounds (naive UTC; end EXCLUSIVE)."""

    start_ts: datetime
    end_ts: datetime

    @property
    def start_date(self) -> date:
        return self.start_ts.date()

    @property
    def end_date(self) -> date:
        """The last date the half-open window covers (partition-pruning bound)."""
        return (self.end_ts - timedelta(microseconds=1)).date()


def as_derived_table(rendered_sql: str, alias: str) -> str:
    """Wrap rendered SQL as a derived table, syntax-safely.

    Executed DIRECTLY, a trailing ``;`` or a trailing ``-- line comment``
    (both common copy-paste artifacts) run fine; nested inside
    ``FROM (<rendered>) <alias>`` a trailing ``;`` terminates the statement
    early and a trailing line comment swallows the closing paren/alias — a
    syntax error on ClickHouse/PostgreSQL/MySQL. Strip the trailing
    terminator and put the closing paren on its OWN line so neither artifact
    can break the wrap (a WP2 review finding; shared by the exposure
    validation pushdown and the ``ab_cohort_source`` builtin).
    """
    inner = rendered_sql.rstrip(" \t\r\n;")
    return f"(\n{inner}\n) {alias}"


def build_builtins(
    *,
    experiment_id: str,
    unit_key: str,
    variants: list[str],
    added_filters: str,
    window: RenderWindow,
    data_database: str,
    internal_database: str,
    exposures_table: str,
    dialect: str,
    apply_exposure_filter: bool = True,
    cov_window: RenderWindow | None = None,
    direct_source_sql: str | None = None,
    has_stratum: bool = True,
) -> dict[str, Any]:
    """The authoritative ``ab_*`` built-ins dict (declarative-config.md §5).

    ``apply_exposure_filter=False`` is set by the loader's COVARIATE render:
    the pre-period window precedes exposure by construction, so the
    ``event_time >= exposure_ts`` predicate must be dropped there
    (statistics-changes.md §5 fixed-lookback mechanics).

    ``ab_cohort_source`` (m8-implementation-plan.md WP3) is the ONE cohort
    fragment the packaged macro reads, built here in one of two modes:

    - **copy mode** (``direct_source_sql is None``): the persisted
      ``_ab_exposures`` table (``FINAL`` on ClickHouse — a mid-merge
      ``ReplacingMergeTree`` must never yield a unit twice; PG/MySQL enforce
      the PK).
    - **direct mode**: a deduping subquery wrapping the rendered assignment
      SQL — ``MIN(exposure_ts)`` per ``(unit, variant)``, the SAME
      aggregation the validation pushdown runs
      (``exposure_source._pushdown_sql`` — keep the two in lockstep; the
      §0.5(e) semantic-parity contract). The projection is normalized to the
      persisted-table column names plus an ``experiment`` literal so the
      macro's cohort projection and its ``WHERE experiment = ...`` predicate
      work identically in both modes.
    """
    if direct_source_sql is None:
        cohort_source = f"{internal_database}.{exposures_table}"
        if dialect == "clickhouse":
            cohort_source += " FINAL"
    else:
        # NULL AS stratum keeps ab.stratum_col() renderable (StrictUndefined
        # never trips) against a stratum-less assignment source. The outer
        # alias is mandatory: PG/MySQL reject an unaliased derived table.
        stratum_sel = "MIN(stratum) AS stratum" if has_stratum else "NULL AS stratum"
        cohort_source = (
            f"(SELECT {unit_key} AS unit_id, variant, "
            f"MIN(exposure_ts) AS exposure_ts, {stratum_sel}, "
            f"'{experiment_id}' AS experiment "
            f"FROM {as_derived_table(direct_source_sql, '_abk_raw')} "
            f"GROUP BY {unit_key}, variant) _abk_cohort"
        )
    builtins: dict[str, Any] = {
        "ab_experiment_id": experiment_id,
        "ab_unit_key": unit_key,
        "ab_variants": list(variants),
        "ab_added_filters": added_filters,
        "ab_start_date": window.start_date.strftime(_DATE_FORMAT),
        "ab_end_date": window.end_date.strftime(_DATE_FORMAT),
        "ab_start_ts": window.start_ts.strftime(_TS_FORMAT),
        "ab_end_ts": window.end_ts.strftime(_TS_FORMAT),
        "data_database": data_database,
        "internal_database": internal_database,
        # kept for external consumers/back-compat only — the packaged macro
        # reads ab_cohort_source (canonical); never diverge the two silently
        "ab_exposures_table": f"{internal_database}.{exposures_table}",
        "ab_cohort_source": cohort_source,
        "ab_dialect": dialect,
        "ab_apply_exposure_filter": apply_exposure_filter,
    }
    # Only present when a covariate window exists — otherwise a template
    # referencing ab_cov_* hard-fails under StrictUndefined instead of
    # silently rendering the string 'None' into SQL.
    if cov_window is not None:
        builtins["ab_cov_start"] = cov_window.start_date.strftime(_DATE_FORMAT)
        builtins["ab_cov_end"] = cov_window.end_date.strftime(_DATE_FORMAT)
    return builtins


class QueryTemplate:
    """SQL query template renderer using Jinja2 (StrictUndefined always).

    Example:
        >>> template = QueryTemplate()
        >>> sql = template.render(
        ...     "SELECT 1 FROM t WHERE ts < '{{ ab_end_ts }}'",
        ...     builtins,
        ... )
    """

    def __init__(self) -> None:
        # The loader is shared across renders; the Environment is rebuilt per
        # render so the ab_* built-ins can live in env.globals — plain
        # {% import 'abkit_assignment.jinja' as ab %} (no `with context`)
        # resolves names against globals, never the caller's render context.
        self._loader = PackageLoader("abkit.loaders", "templates")

    def _make_env(self, template_globals: dict[str, Any]) -> Environment:
        env = Environment(
            autoescape=False,  # SQL, not HTML
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
            loader=self._loader,
        )
        env.globals.update(template_globals)
        return env

    def render(
        self,
        query: str,
        builtins: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> str:
        """Render a SQL template.

        Args:
            query: the SQL template text
            builtins: the ``ab_*`` built-ins (:func:`build_builtins`)
            context: optional extra variables; a key colliding with a
                built-in raises (shadowing ``ab_end_ts`` must never be silent)

        Raises:
            TemplateRenderError: on syntax errors, undefined variables, or
                built-in shadowing
        """
        template_context = dict(builtins)
        if context:
            collisions = sorted(set(context) & set(builtins))
            if collisions:
                raise TemplateRenderError(
                    f"context must not shadow built-ins: {collisions} "
                    "(the ab_* window bounds are authoritative)"
                )
            template_context.update(context)

        try:
            env = self._make_env(template_context)
            template = env.from_string(query)
            return template.render()
        except TemplateSyntaxError as e:
            raise TemplateRenderError(f"Invalid template syntax: {e.message}") from e
        except TemplateRenderError:
            raise
        except Exception as e:
            raise TemplateRenderError(f"Template rendering failed: {e}") from e
