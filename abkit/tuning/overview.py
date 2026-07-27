"""The dashboard's row shaper: persisted ``_ab_results`` → one row per experiment.

``docs/specs/m11-implementation-plan.md`` DASH-2. The unit the dashboard's
per-experiment stats route serves: latest verdict, effect/CI, and a capped
pre-aggregated sparkline, read from
:meth:`InternalTablesManager.load_results` — **not** through
``build_report_payload`` and **not** through ``tuning.session.load_session``.
Nothing here computes a statistic: every verdict is
:func:`abkit.pipeline.readout.evaluate`'s, the same function ``abk run
--report`` calls, so the dashboard cannot drift from the report.

Row grain is **one experiment** (§3, decided by the maintainer 2026-07-27),
matching the experiment-scoped open/explore/run buttons. What a row can carry
is bounded by ``evaluate()``'s contract: ``ExperimentReadout.verdicts`` is
``[c for c in experiment.comparisons if c.is_main_metric]`` crossed with each
treatment arm (``abkit/pipeline/readout.py`` ``evaluate``), so a
secondary/guardrail comparison NEVER produces a ``PairVerdict`` and never
appears in a row's ``comparisons`` sub-list. Surfacing secondary verdicts
would mean re-implementing the decision logic — M14 work, not this milestone.
The per-metric **Run** affordance a secondary metric still needs is fed by
:func:`build_overview_boot_entries`, which lists the configured comparisons
straight off the config.

Deliberate deviations from the donor (``detectkit/ui/overview.py``) and from a
literal reading of the plan, each with the hazard it avoids:

* **SRM is read window-independently** through
  :func:`abkit.pipeline.readout.srm_summary` over ALL persisted rows, not off
  the windowed ``ExperimentReadout``. "Is assignment broken?" is
  whole-experiment health; sourcing it from the window would let a red chip go
  silent because an operator switched to ``24h``. This is exactly what the
  report already does (``reporting/builder.py`` pairs ``evaluate`` over the
  windowed rows with ``srm_summary`` over the unwindowed ones), so the two
  surfaces agree.
* **The sparkline filters by the comparison's CURRENT** ``method_config_id``.
  ``evaluate`` drops orphaned series internally; a sparkline built off raw
  rows would silently interleave one point per historical config generation —
  the donor's dead-detector-id hazard, with ``_ab_results``' orphan series
  (an edited identity param leaves old rows behind) as abkit's version of it.
* **:data:`MAX_STAT_POINTS` is display-only.** It truncates the sparkline
  input, never ``evaluate``'s input: a rendering cap that could move a verdict
  would be a statistical change in a UI milestone.
* **``locked`` probes the ``run`` lock only**, not the out-of-band ``validate``
  one. The flag exists to grey the dashboard's Run button, and a held
  ``validate`` claim does not block ``abk run`` (different ``process_type``,
  so ``acquire_lock`` never sees it).
* ``±inf`` is scrubbed to ``null`` alongside ``NaN`` (the donor filters only
  NaN). JSON has no ``Infinity``, and abkit legitimately stores it —
  ``pair_mde`` uses ``math.inf`` for "configured but unavailable".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from abkit.config.experiment_config import ExperimentConfig
from abkit.config.project_config import ProjectConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._tasks import DEFAULT_PROCESS_TYPE, DEFAULT_SCOPE
from abkit.pipeline.readout import PairVerdict, evaluate, srm_summary
from abkit.tuning.payload import _ms
from abkit.tuning.recompute import _clean, _row_float
from abkit.utils.datetime_utils import now_utc_naive, to_naive_utc

#: Day counts for the fixed window presets; ``"all"`` has no fixed lookback
#: and is resolved separately (see :func:`_window_start`).
WINDOW_PRESETS: dict[str, int] = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}

#: Every accepted ``window_preset`` value.
ALL_WINDOW_PRESETS = frozenset({*WINDOW_PRESETS, "all"})

#: Defensive cap on how many looks of ONE (metric, arm pair) series reach the
#: sparkline bucketer. DISPLAY ONLY — the same rows reach ``evaluate()``
#: untruncated, because a rendering cap must never be able to move a verdict.
MAX_STAT_POINTS = 20_000

# Ceiling on emitted sparkline points; the donor's number and its bucketing
# idiom (a ceiling, not a target — 161 looks bucket to 81 points, not 160).
_MAX_SPARK_BUCKETS = 160

# The lock triple `abk run` acquires (abkit/pipeline/driver.py LOCK_SCOPE /
# LOCK_PROCESS, which are these DB-layer defaults) — mirrored read-only so the
# dashboard can grey a Run button without any of the write side. Pinned
# against the driver's own constants by tests/tuning/test_overview.py.
_LOCK_SCOPE = DEFAULT_SCOPE
_LOCK_PROCESS_TYPE = DEFAULT_PROCESS_TYPE


def experiments_base_dir(project_root: Path, project: ProjectConfig | None = None) -> Path:
    """The directory experiment YAMLs live in — ``project.paths.experiments``.

    One derivation shared by the stats row and the boot list, so the two
    payloads can never disagree on an experiment's ``dir``. Without a
    *project* it falls back to the same literal ``discovery.select_experiments``
    defaults to.
    """
    return project_root / (project.paths.experiments if project is not None else "experiments")


def resolve_experiment_location(
    experiment_path: Path, project_root: Path, experiments_dir: Path
) -> tuple[str, str]:
    """``(dir, file)`` for an experiment YAML.

    ``dir`` is the parent directory relative to the experiments root (``""``
    for a top-level experiment — the grouping key); ``file`` is the path
    relative to the project root (the "open in your editor" target). Both are
    posix-separated regardless of platform, and both fall back rather than
    raise when the path is not actually under the expected root: this runs on
    every boot payload and every row, where a defensive string beats an
    exception.
    """
    try:
        rel_dir = experiment_path.parent.relative_to(experiments_dir)
        dir_str = "" if rel_dir == Path() else rel_dir.as_posix()
    except ValueError:
        dir_str = ""
    try:
        file_str = experiment_path.relative_to(project_root).as_posix()
    except ValueError:
        file_str = experiment_path.as_posix()
    return dir_str, file_str


def _validate_window_preset(window_preset: str) -> None:
    """Reject an unknown preset loudly, before any per-row work.

    A request-level mistake must raise, never masquerade as N broken rows
    (the donor validates identically in both of its public entries).
    """
    if window_preset not in ALL_WINDOW_PRESETS:
        allowed = ", ".join(sorted(ALL_WINDOW_PRESETS))
        raise ValueError(f"Unknown window preset {window_preset!r}. Choose one of: {allowed}.")


def _window_start(window_preset: str, now: datetime) -> datetime | None:
    """Left edge of the preset's ``end_ts`` filter; ``None`` means unbounded.

    Anchored on *now*, not on the experiment's last look, so a finished
    experiment simply shows nothing under a short preset instead of
    back-dating its window — the row's ``last_end_ts`` stays ``None`` and the
    client can say "no looks in this window" rather than reporting a verdict
    the operator's window never covered.
    """
    if window_preset == "all":
        return None
    return now - timedelta(days=WINDOW_PRESETS[window_preset])


def _resolve_now(now: datetime | None) -> datetime:
    """The anchor every fixed preset counts back from, as naive UTC.

    ``to_naive_utc`` returns ``None`` only for a ``None`` input, so its
    fallback doubles as the "caller passed no *now*" default — and a tz-aware
    value is CONVERTED, never re-labelled, before it meets a naive ``end_ts``.
    """
    resolved = to_naive_utc(now)
    return now_utc_naive() if resolved is None else resolved


def _spark_series(rows: Sequence[dict]) -> list[list[Any]]:
    """Bucket one pair's looks into ≤ :data:`_MAX_SPARK_BUCKETS` ``[ms, effect]``.

    *rows* must be one (metric, arm pair, method_config_id) series ascending
    by ``end_ts``. Bucketing is by STRIDE, not by time — equal point counts,
    so a gapped series yields time-irregular buckets and the renderer must
    plot against the emitted timestamp, never against the index. The
    timestamp is the bucket's LAST look; the value is the mean of its finite
    effects (``None`` when a bucket has none, which keeps the gap visible on
    the x axis instead of silently closing it).
    """
    n = len(rows)
    if n == 0:
        return []
    step = max(1, -(-n // _MAX_SPARK_BUCKETS))  # ceil(n / _MAX_SPARK_BUCKETS)
    out: list[list[Any]] = []
    for i in range(0, n, step):
        chunk = rows[i : i + step]
        values = [
            value for value in (_row_float(row, "effect") for row in chunk) if value is not None
        ]
        mean_value = float(np.mean(values)) if values else None
        out.append([_ms(chunk[-1]["end_ts"]), mean_value])
    return out


def _empty_row(name: str) -> dict[str, Any]:
    """The full-shape row with every stat field at its "no data" default.

    Allocated before anything can fail, so a mid-flight exception still
    yields a complete row rather than a fragment, and the fields already
    filled survive it. Instants and rates default to ``None`` (*unknown*),
    collections to empty, flags to a concrete ``False`` — the client tests
    values, never key existence.
    """
    return {
        "name": name,
        "dir": "",
        "file": "",
        "tags": [],
        "status": None,
        "start_ts": None,
        "horizon_ts": None,
        "main_metric": None,
        "locked": False,
        "verdict": None,
        "srm_flag": False,
        "srm_pvalue": None,
        "effect": None,
        "ci": [None, None],
        "pvalue": None,
        "alpha": None,
        "elapsed_days": None,
        "is_horizon": False,
        "weekly_cycle_pct": None,
        "last_end_ts": None,
        "spark": [],
        "comparisons": [],
        "error": None,
    }


def _fill_config_fields(
    row: dict[str, Any],
    *,
    project_root: Path,
    experiments_dir: Path,
    experiment_path: Path,
    experiment: ExperimentConfig,
) -> None:
    """Populate the fields derived purely from the (already-validated) config.

    ``start_ts``/``horizon_ts`` are the RESOLVED instants
    (``ExperimentConfig.start_instant``/``horizon_instant``) as ms-epoch, i.e.
    the same naive-UTC frame ``_ab_results.start_ts`` is stored in — the raw
    config fields are local wall clock and are a ``date | datetime`` union
    that cannot even be compared to each other (m10 D1/D6).
    """
    dir_str, file_str = resolve_experiment_location(experiment_path, project_root, experiments_dir)
    row["dir"] = dir_str
    row["file"] = file_str
    row["tags"] = list(experiment.tags) if experiment.tags else []
    row["status"] = experiment.status
    row["start_ts"] = _ms(experiment.start_instant())
    row["horizon_ts"] = _ms(experiment.horizon_instant())
    main_metrics = experiment.main_metrics()
    row["main_metric"] = main_metrics[0] if main_metrics else None


def _pair_rows(
    experiment: ExperimentConfig, rows: Sequence[dict], verdict: PairVerdict
) -> list[dict]:
    """The windowed looks behind one ``PairVerdict``, ascending, capped.

    Filtered to the comparison's CURRENT ``method_config_id`` — the same rule
    ``readout._filter_rows`` applies before the verdict is computed, so the
    sparkline and the headline describe one series. Skipping it would
    interleave every superseded config generation's rows (an edited identity
    param orphans but never deletes) into what reads as one curve.
    """
    expected = experiment.get_comparison(verdict.metric).method.method_config_id
    picked = [
        row
        for row in rows
        if str(row["metric"]) == verdict.metric
        and str(row["name_1"]) == verdict.name_1
        and str(row["name_2"]) == verdict.name_2
        and str(row["method_config_id"]) == expected
    ]
    picked.sort(key=lambda row: row["end_ts"])
    return picked[-MAX_STAT_POINTS:]


def _fill_stats(
    row: dict[str, Any],
    *,
    experiment: ExperimentConfig,
    project: ProjectConfig | None,
    tables: InternalTablesManager,
    window_preset: str,
    now: datetime,
) -> None:
    """Populate everything that needs the database: lock, SRM, verdict, spark.

    A project that has never run has no ``_ab_results`` table; that is the
    honest "no data yet" state, not an error, so the stats stay at their
    ``_empty_row`` defaults (the reporting builder's ``results_table_exists``
    precedent — a read-only surface never creates schema).
    """
    if not tables.results_table_exists():
        return

    row["locked"] = tables.check_lock(experiment.name, _LOCK_SCOPE, _LOCK_PROCESS_TYPE) is not None

    rows = tables.load_results(experiment.name)

    # Whole-experiment health, deliberately NOT windowed (module docstring).
    srm_flag, srm_pvalue = srm_summary(experiment, rows)
    row["srm_flag"] = bool(srm_flag)
    row["srm_pvalue"] = _clean(srm_pvalue)

    start = _window_start(window_preset, now)
    windowed = rows if start is None else [r for r in rows if r["end_ts"] >= start]

    readout = evaluate(experiment, windowed, project=project)
    if not readout.verdicts:
        # Unreachable through a validated config (≥1 main comparison and ≥2
        # variants are both enforced), so degrade rather than index blind.
        raise ValueError(
            f"experiment {experiment.name!r} produced no verdicts — "
            "a main comparison and a treatment arm are both required"
        )

    headline = readout.verdicts[0]
    row["verdict"] = headline.verdict
    row["effect"] = _clean(headline.effect)
    row["ci"] = [_clean(headline.left_bound), _clean(headline.right_bound)]
    row["pvalue"] = _clean(headline.pvalue)
    row["alpha"] = _clean(headline.alpha)
    row["elapsed_days"] = _clean(headline.elapsed_days)
    row["is_horizon"] = bool(headline.is_horizon)
    row["weekly_cycle_pct"] = _clean(headline.weekly_cycle_pct)
    row["last_end_ts"] = None if headline.end_ts is None else _ms(headline.end_ts)
    row["comparisons"] = [
        {
            "metric": verdict.metric,
            # The report payload's arm vocabulary (`_verdict_to_payload`), so
            # the two surfaces name a pair the same way.
            "pair": {"c": verdict.name_1, "t": verdict.name_2},
            "verdict": verdict.verdict,
            "effect": _clean(verdict.effect),
        }
        for verdict in readout.verdicts
    ]
    row["spark"] = _spark_series(_pair_rows(experiment, windowed, headline))


def _fill_row(
    row: dict[str, Any],
    *,
    project_root: Path,
    experiments_dir: Path,
    experiment_path: Path,
    experiment: ExperimentConfig,
    project: ProjectConfig | None,
    tables: InternalTablesManager,
    window_preset: str,
    now: datetime,
) -> None:
    """Config fields first, then stats — the order the degrade path relies on."""
    _fill_config_fields(
        row,
        project_root=project_root,
        experiments_dir=experiments_dir,
        experiment_path=experiment_path,
        experiment=experiment,
    )
    _fill_stats(
        row,
        experiment=experiment,
        project=project,
        tables=tables,
        window_preset=window_preset,
        now=now,
    )


def build_experiment_row(
    *,
    project_root: Path,
    experiment_path: Path,
    experiment: ExperimentConfig,
    project: ProjectConfig | None,
    tables: InternalTablesManager,
    window_preset: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One experiment's dashboard row — the unit the stats route serves.

    Raises ``ValueError`` for an unknown *window_preset*, and propagates
    anything the read or the readout raises; use
    :func:`build_experiment_row_safe` on any path that renders a list.

    Always pass *project*: with it ``None`` and the experiment leaving
    ``correction`` unset, ``evaluate`` degrades to stored-alpha CI
    significance, which mis-scores a project-level ``benjamini_hochberg``.
    *now* defaults to the wall clock and is the anchor every fixed preset
    counts back from; it must be naive UTC (a tz-aware value is normalized).
    """
    _validate_window_preset(window_preset)
    row = _empty_row(experiment.name)
    _fill_row(
        row,
        project_root=project_root,
        experiments_dir=experiments_base_dir(project_root, project),
        experiment_path=experiment_path,
        experiment=experiment,
        project=project,
        tables=tables,
        window_preset=window_preset,
        now=_resolve_now(now),
    )
    return row


def build_experiment_row_safe(
    *,
    project_root: Path,
    experiment_path: Path,
    experiment: ExperimentConfig,
    project: ProjectConfig | None,
    tables: InternalTablesManager,
    window_preset: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """:func:`build_experiment_row`, degrading one bad experiment into a row.

    A DB hiccup, an orphaned method config, a config edge the readout refuses
    — any of them must cost that experiment's numbers, never the whole list.
    The failure is a renderable string in ``error``, the fields filled before
    it survive, and every remaining field keeps its ``_empty_row`` default.

    An unknown *window_preset* still raises: that is a request-level mistake,
    and reporting it as N broken experiments would point the operator at the
    wrong thing.
    """
    _validate_window_preset(window_preset)
    row = _empty_row(experiment.name)
    try:
        _fill_row(
            row,
            project_root=project_root,
            experiments_dir=experiments_base_dir(project_root, project),
            experiment_path=experiment_path,
            experiment=experiment,
            project=project,
            tables=tables,
            window_preset=window_preset,
            now=_resolve_now(now),
        )
    except Exception as exc:  # noqa: BLE001 — one bad experiment must not sink the list
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def build_overview_boot_entries(
    project_root: Path,
    experiments: Sequence[tuple[Path, ExperimentConfig]],
    *,
    project: ProjectConfig | None = None,
) -> list[dict[str, Any]]:
    """The metadata-only experiment list the dashboard's ``GET /`` bakes.

    Zero DB access and zero statistics — just enough to render the list shell
    before the per-experiment stats arrive. ``comparisons`` carries the
    experiment's CONFIGURED comparisons (not the readout's verdicts) because
    the per-metric Run affordance must exist for a secondary metric too, and
    a secondary metric never appears in ``readout.verdicts``.
    """
    base = experiments_base_dir(project_root, project)
    entries: list[dict[str, Any]] = []
    for path, experiment in experiments:
        dir_str, file_str = resolve_experiment_location(path, project_root, base)
        main_metrics = experiment.main_metrics()
        entries.append(
            {
                "name": experiment.name,
                "dir": dir_str,
                "file": file_str,
                "tags": list(experiment.tags) if experiment.tags else [],
                "status": experiment.status,
                "start_ts": _ms(experiment.start_instant()),
                "horizon_ts": _ms(experiment.horizon_instant()),
                "main_metric": main_metrics[0] if main_metrics else None,
                "comparisons": [
                    {"metric": comparison.metric, "is_main_metric": bool(comparison.is_main_metric)}
                    for comparison in experiment.comparisons
                ],
            }
        )
    return entries
