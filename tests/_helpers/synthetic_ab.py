"""The shared explore-test harness (WP4/WP6): a synthetic warehouse + fixtures.

The same shape as the pipeline suite's SyntheticWarehouse, extended to three
metric kinds (sample / fraction-with-nobs>1 / ratio) and a deterministic
shuffle mode (the D11 no-ORDER-guarantee fixture). Everything the recompute
and server suites both need lives here.
"""

from __future__ import annotations

import math
import random
import re
from datetime import datetime, timedelta
from typing import Any

from fake_db import FakeDatabaseManager, serve_assignment_pushdown

from abkit.compute.recompute_backend import RecomputeBackend
from abkit.config import ExperimentConfig, MetricConfig, ProjectConfig
from abkit.pipeline import run_experiment
from abkit.tuning import RecomputeEngine, backend_cutoff_loader, load_session

START = datetime(2024, 7, 1)
NOW = datetime(2024, 7, 20)  # past the horizon: every cutoff is complete
REL = 1e-9

_WINDOW_RE = re.compile(r"event_time >= '([^']+)' AND event_time < '([^']+)'")


def _metric_sql(table: str, selects: str) -> str:
    return (
        "{% import 'abkit_assignment.jinja' as ab %}\n"
        "SELECT {{ ab.variant_col() }} AS variant, user_id, " + selects + " "
        "FROM {{ data_database }}." + table + " {{ ab.exposed_units() }} "
        "GROUP BY variant, user_id"
    )


REVENUE = MetricConfig.model_validate(
    {
        "name": "arpu",
        "type": "sample",
        "columns": {"variant": "variant", "value": "gross_usd"},
        "state_additive": True,
        "query": _metric_sql("user_revenue", "sum(gross_usd) AS gross_usd"),
    }
)
CONVERSION = MetricConfig.model_validate(
    {
        "name": "conversion",
        "type": "fraction",
        "columns": {"variant": "variant", "count": "conversions", "nobs": "trials"},
        "state_additive": True,
        "query": _metric_sql(
            "user_conversions", "sum(conversions) AS conversions, sum(trials) AS trials"
        ),
    }
)
CTR = MetricConfig.model_validate(
    {
        "name": "ctr",
        "type": "ratio",
        "columns": {"variant": "variant", "numerator": "clicks", "denominator": "views"},
        "state_additive": True,
        "query": _metric_sql("user_engagement", "sum(clicks) AS clicks, sum(views) AS views"),
    }
)
METRICS = {"arpu": REVENUE, "conversion": CONVERSION, "ctr": CTR}

PROJECT = ProjectConfig.model_validate({"name": "p", "default_profile": "dev"})


class SyntheticWarehouse(FakeDatabaseManager):
    """Aggregates synthetic per-unit event logs for the three metric SQLs.

    ``shuffled=True`` returns metric result sets in a scrambled-but-
    deterministic order — the D11 fixture: a warehouse that honors no ORDER.
    """

    def __init__(self, shuffled: bool = False):
        super().__init__()
        self.shuffled = shuffled
        #: fact rows scanned per table — the m9 WP5 perf gate's measurement
        self.scanned_by_table: dict[str, int] = {}
        self._last_scanned = 0
        # (unit, variant, exposure_ts)
        self.cohort: list[tuple[str, str, datetime]] = []
        # table -> [(unit, variant, event_ts, {column: value})]
        self.events: dict[str, list[tuple[str, str, datetime, dict[str, float]]]] = {
            "user_revenue": [],
            "user_conversions": [],
            "user_engagement": [],
        }

    def execute_query(self, query: str, params: dict[str, Any] | None = None):
        flat = " ".join(query.split())
        # The short-circuit paths bypass the base manager, so they must record
        # their own cost — otherwise the m9 WP5 counters (and the perf gate
        # built on them) would see every fact scan as free.
        for table, events in self.events.items():
            if table in flat:
                rows = self._aggregate(flat, events)
                # SCANNED = the fact rows inside the rendered window (what a
                # partition-pruned warehouse actually reads), not the
                # aggregated rows handed back. This is the quantity the
                # milestone's O(D²)→O(D) claim is about, so the fake reports
                # it the way ClickHouse does.
                self._record_query(len(rows), 0.0, scanned_rows=self._last_scanned)
                self.scanned_by_table[table] = (
                    self.scanned_by_table.get(table, 0) + self._last_scanned
                )
                return rows
        if "FROM assignments" in flat:
            raw = [{"user_id": u, "variant": v, "exposure_ts": ts} for u, v, ts in self.cohort]
            served = serve_assignment_pushdown(self._project, flat, raw)
            self._record_query(len(served), 0.0)
            return served
        return super().execute_query(query, params)

    def _cohort_join_map(self, flat: str) -> dict[str, datetime]:
        """The unit → exposure_ts map the metric query's cohort fragment names.

        Copy mode (no ``_abk_raw`` wrap) keeps the historical shortcut: the
        scripted cohort verbatim. A direct-mode fragment (m8 WP3) is held to
        real-backend semantics instead: every column its SELECT list references
        must exist in the assignment source rows — ``MIN(stratum)`` against the
        stratum-less scripted source raises the fake's unknown-column error
        exactly where ClickHouse/PG/MySQL would (the has_stratum contract) —
        and exposure_ts is the deduped ``MIN()`` per unit.
        """
        if "_abk_raw" not in flat:
            return {u: ts for u, _, ts in self.cohort}
        source_columns = {"user_id", "variant", "exposure_ts"}  # the scripted rows
        unit_projection = re.search(r"\(SELECT (\w+) AS unit_id", flat)
        assert unit_projection, f"direct cohort fragment lost its unit projection: {flat}"
        referenced = [unit_projection.group(1)]
        if "MIN(stratum)" in flat:
            referenced.append("stratum")
        for column in referenced:
            if column not in source_columns:
                raise ValueError(
                    f"unknown column '{column}' in the direct cohort source "
                    "(the fake mirrors a real backend's unknown-column error)"
                )
        earliest: dict[str, datetime] = {}
        for unit, _, ts in self.cohort:
            if unit not in earliest or ts < earliest[unit]:
                earliest[unit] = ts
        return earliest

    def _aggregate(self, flat: str, events: list) -> list[dict[str, Any]]:
        match = _WINDOW_RE.search(flat)
        assert match, f"metric SQL lost its window filter: {flat}"
        w_start = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        w_end = datetime.strptime(match.group(2), "%Y-%m-%d %H:%M:%S")
        exposure_filter = "exposure_ts" in flat.split("WHERE experiment")[-1]
        # PLAN-2 population render: the macro swapped the cohort for a one-row
        # synthetic relation, so EVERY unit the fact table yields is in scope and
        # its arm is the sentinel — a fake that kept serving scripted variants
        # here would make the cohort-free contract untestable (the m8 WP3 lesson:
        # the fake must model the real backend, or the parity test is vacuous).
        population = re.search(r"CROSS JOIN \(SELECT '([^']+)' AS _abk_variant", flat)
        if population is not None:
            return self._aggregate_population(flat, events, w_start, w_end, population.group(1))
        exposure_by_unit = self._cohort_join_map(flat)
        sums: dict[tuple[str, str], dict[str, float]] = {}
        scanned = 0
        for unit, variant, ts, values in events:
            if not (w_start <= ts < w_end):
                continue
            scanned += 1  # inside the rendered window ⇒ a real scanned row
            if unit not in exposure_by_unit:
                continue  # the cohort join
            if exposure_filter and ts < exposure_by_unit[unit]:
                continue
            acc = sums.setdefault((unit, variant), dict.fromkeys(values, 0.0))
            for column, value in values.items():
                acc[column] += value
        self._last_scanned = scanned
        rows = [
            {"variant": variant, "user_id": unit, **acc}
            for (unit, variant), acc in sorted(sums.items())
        ]
        if self.shuffled:
            random.Random(20240701).shuffle(rows)
        return rows

    def _aggregate_population(self, flat, events, w_start, w_end, arm: str):
        """The cohort-free counterpart of :meth:`_aggregate` (PLAN-2)."""
        sums: dict[str, dict[str, float]] = {}
        scanned = 0
        for unit, _variant, ts, values in events:
            if not (w_start <= ts < w_end):
                continue
            scanned += 1
            acc = sums.setdefault(unit, dict.fromkeys(values, 0.0))
            for column, value in values.items():
                acc[column] += value
        self._last_scanned = scanned
        rows = [{"variant": arm, "user_id": unit, **acc} for unit, acc in sorted(sums.items())]
        if self.shuffled:
            random.Random(20240701).shuffle(rows)
        return rows


def seed_cohort(warehouse: SyntheticWarehouse, n_per_arm: int = 120) -> None:
    for i in range(n_per_arm):
        warehouse.cohort.append((f"c{i:03d}", "control", START + timedelta(hours=1)))
        warehouse.cohort.append((f"t{i:03d}", "treatment", START + timedelta(hours=1)))


def seed_null_events(warehouse: SyntheticWarehouse, days: int = 4) -> None:
    """A/A twin of :func:`seed_all_events`: identical shape, NO treatment lift.

    With no true effect, a placebo re-split has an analytically exact FPR ≈ α — the
    ground-truth fixture for the ``abk validate`` scorer (m4 WP2/WP7).
    """
    seed_all_events(warehouse, days=days, treatment_lift=1.0)


def seed_all_events(
    warehouse: SyntheticWarehouse, days: int = 4, treatment_lift: float = 1.25
) -> None:
    """Deterministic per-unit daily values with a treatment lift everywhere."""
    for unit, variant, _ in warehouse.cohort:
        idx = int(unit[1:])
        lift = treatment_lift if variant == "treatment" else 1.0
        base = 1.0 + (idx % 7) * 0.5
        for day in range(days):
            ts = START + timedelta(days=day, hours=12)
            wiggle = ((idx * 7 + day) % 5) * 0.3
            warehouse.events["user_revenue"].append(
                (unit, variant, ts, {"gross_usd": (base + wiggle) * lift})
            )
            trials = 2.0 + (idx + day) % 3  # per-unit nobs > 1 (the blocker fixture)
            converted = float((idx + day) % 2) + (1.0 if variant == "treatment" else 0.0)
            warehouse.events["user_conversions"].append(
                (unit, variant, ts, {"conversions": min(trials, converted), "trials": trials})
            )
            views = 5.0 + (idx + day) % 4
            clicks = (1.0 + (idx * 3 + day) % 4) * lift
            warehouse.events["user_engagement"].append(
                (unit, variant, ts, {"clicks": clicks, "views": views})
            )
        # pre-period signal correlated with the in-period base (CUPED)
        for day in range(1, 8):
            warehouse.events["user_revenue"].append(
                (
                    unit,
                    variant,
                    START - timedelta(days=day, hours=6),
                    {"gross_usd": base + (idx % 3) * 0.2},
                )
            )


def make_experiment(
    name: str,
    metric: str,
    method: dict[str, Any],
    alpha: float | None = None,
    min_effect: float | None = None,
    sequential: dict[str, Any] | None = None,
) -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        experiment_payload(name, metric, method, alpha, min_effect, sequential)
    )


def experiment_payload(
    name: str,
    metric: str,
    method: dict[str, Any],
    alpha: float | None = None,
    min_effect: float | None = None,
    sequential: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The raw experiment document — also YAML-dumpable for Apply tests."""
    comparison: dict[str, Any] = {"metric": metric, "is_main_metric": True, "method": method}
    if min_effect is not None:
        comparison["min_effect"] = min_effect
    payload: dict[str, Any] = {
        "name": name,
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-05",
        "unit_key": "user_id",
        "assignment": {
            "query": "SELECT user_id, variant, exposure_ts FROM assignments",
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
        },
        "comparisons": [comparison],
    }
    if alpha is not None:
        payload["alpha"] = alpha
    if sequential is not None:
        payload["sequential"] = sequential
    return payload


def run_pipeline(warehouse, tables, experiment, metrics=METRICS, project=PROJECT):
    outcome = run_experiment(experiment, metrics, project, warehouse, tables, now_utc=NOW)
    assert outcome.status == "completed", outcome.error
    return outcome


def build_session(warehouse, tables, experiment, metrics=METRICS, project=PROJECT, **kwargs):
    backend = RecomputeBackend(warehouse, experiment)
    loader = backend_cutoff_loader(
        backend, {name: cfg.get_query_text(None) for name, cfg in metrics.items()}
    )
    return load_session(experiment, metrics, project, tables, loader=loader, **kwargs)


def build_engine(warehouse, tables, experiment, with_cache=True, **kwargs) -> RecomputeEngine:
    if with_cache:
        session = build_session(warehouse, tables, experiment, **kwargs)
    else:
        session = load_session(experiment, METRICS, PROJECT, tables, loader=None, **kwargs)
    return RecomputeEngine(session)


def persisted(tables, experiment, metric) -> dict[tuple[str, str, datetime], dict]:
    rows = tables.load_results(experiment.name, metric=metric)
    return {(r["name_1"], r["name_2"], r["end_ts"]): r for r in rows}


def assert_close(actual, expected, what=""):
    if expected is None or actual is None:
        assert actual is None and expected is None, f"{what}: {actual!r} != {expected!r}"
        return
    assert math.isclose(
        actual, expected, rel_tol=REL, abs_tol=1e-12
    ), f"{what}: {actual!r} != {expected!r}"
