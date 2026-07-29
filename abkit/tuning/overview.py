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
appears in a row's ``verdicts`` sub-list. Surfacing secondary verdicts
would mean re-implementing the decision logic — M14 work, not this milestone.
The per-metric **Run** affordance a secondary metric still needs is fed by
:func:`build_overview_boot_entries`, which lists the configured comparisons
straight off the config.

Deliberate deviations from the donor (``detectkit/ui/overview.py``) and from a
literal reading of the plan, each with the hazard it avoids:

* **The window preset is the sparkline's x-range and NOTHING else** — the
  plan's DASH-2 step 3 says to filter rows by ``end_ts`` and hand the result
  to ``evaluate``; doing that is wrong here, and the review reproduced both
  halves of the damage. The donor's datapoints are a plain time series, where
  a left-bounded window is a shorter series. ``_ab_results`` rows are
  **cumulative looks from a pinned start**: dropping the oldest does not
  produce a shorter experiment, it produces a truncated *stabilization
  history*, while each surviving row still measures the whole window from
  ``start_ts``. Measured on the fixture: a 14-look daily experiment that
  ``evaluate`` calls WIN reads INCONCLUSIVE at the ``24h`` preset (one look
  left, below ``MIN_STABLE_CUTOFFS``) — i.e. every daily experiment — and a
  6h-cadence series inverts the other way, reporting a WIN the full readout
  refuses because the look that crossed zero fell outside the window while
  the rationale still says "trailing 7-day window". ``abk run --report``
  passes no ``start``/``end`` at all, so the report's verdict IS the
  full-series verdict; anything else here would be a silent disagreement.
* **Undeclared arm pairs are dropped before the readout** (the
  ``reporting/builder.py`` filter, mirrored). ``readout._filter_rows`` screens
  by metric and ``method_config_id`` only, but the read-time
  Benjamini-Hochberg family is built from every informative row at a cutoff —
  so rows left by a mid-flight arm rename inflate the family and tighten every
  threshold. Reproduced: nine renamed-away pairs turn the report's WIN into
  the dashboard's INCONCLUSIVE on identical rows.
* **SRM is read window-independently** through
  :func:`abkit.pipeline.readout.srm_summary` over every declared-pair row, not
  the windowed subset. "Is assignment broken?" is whole-experiment health, and
  the report reads it the same way (``reporting/builder.py`` deliberately
  keeps the SRM block out of the window so a pinned replay never silences a
  failing gate). It is also read BEFORE the readout, so a row whose verdict
  failed still carries a red gate.
* **The sparkline filters by the comparison's CURRENT** ``method_config_id``.
  ``evaluate`` drops orphaned series internally; a sparkline built off raw
  rows would silently interleave one point per historical config generation —
  the donor's dead-detector-id hazard, with ``_ab_results``' orphan series
  (an edited identity param leaves old rows behind) as abkit's version of it.
* **:data:`MAX_STAT_POINTS` is display-only**, like the window: it truncates
  the sparkline input, never ``evaluate``'s.
* **A qualified verdict never renders as an unqualified one.** The plan's row
  shape carries the verdict word alone, but under ``guardrail_policy: warn``
  the readout KEEPS a WIN and attaches a mandatory loud caveat — the one
  policy whose safety IS the caveat. The row therefore also carries
  ``rationale``, ``caveats`` and ``guardrail_regressed``, each listed pair
  carries its own two, and the row-level flag is ORed across all of them: a
  regression on the second arm must not leave a green flag on the row that
  lists it. ``readout.warnings`` rides along in ``warnings`` for the same
  reason — a renamed arm otherwise looks exactly like a never-run experiment.
* **The per-pair list is named ``verdicts``, not ``comparisons``.** The boot
  entry's ``comparisons`` is the CONFIGURED list; DASH-5 merges the two
  payloads by experiment name, and one key holding two incompatible shapes is
  a trap worth renaming out of existence.
* **``project`` is required, not optional.** With it absent and the experiment
  leaving ``correction`` unset, ``evaluate`` falls back to stored-alpha CI
  significance and mis-scores a project-level ``benjamini_hochberg`` — it says
  so in its own warnings, which a glanceable row has nowhere to put. A
  docstring sentence is not a defense; the signature is.
* **``locked`` probes the ``run`` lock only**, not the out-of-band ``validate``
  one (which does not block ``abk run``, the button this flag greys), and the
  probe runs in a ``finally`` inside its own ``try``: neither direction may
  cost the other. An unreadable ``_ab_tasks`` (a partially-completed
  ``ensure_tables``, a narrow read-only grant) must not blank the statistics,
  and a failed read must not report "unlocked" for the degraded row an
  operator is most likely to press Run on.
* ``±inf`` is scrubbed to ``null`` alongside ``NaN`` (the donor filters only
  NaN, and JSON has no ``Infinity``). Mostly defence in depth —
  ``pipeline/enrich`` NULLs non-finite values on the WRITE path, though only
  for real ``float`` cells, so a numpy or ``Decimal`` NaN from an external
  writer still arrives — and the bucket mean is scrubbed too, since summing
  large finite effects can overflow where no single one did.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from abkit.config.experiment_config import ExperimentConfig
from abkit.config.project_config import ProjectConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.internal_tables._tasks import DEFAULT_PROCESS_TYPE, DEFAULT_SCOPE
from abkit.pipeline.readout import PairVerdict, evaluate, srm_summary
from abkit.tuning.payload import _ms
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


def _num(value: Any) -> float | None:
    """A stored number, JSON-safe: ``None``/NaN/±inf all become ``None``.

    A local copy, matching the three the codebase already keeps
    (``pipeline/enrich``, ``stats/result``, ``tuning/recompute``) rather than
    one shared import — and the widest of the four: a driver can hand back a
    string, ``bytes`` or ``Decimal`` cell, where ``recompute._clean`` raises.
    Keeping it local also stops the dashboard's read side from importing the
    explore recompute engine directly. That independence is only partial
    today (``abkit/tuning/__init__`` imports the engine eagerly, so any
    ``abkit.tuning`` import pays for it) — it becomes real if DASH-3 imports
    this module by path.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def experiments_base_dir(project_root: Path, project: ProjectConfig) -> Path:
    """The directory experiment YAMLs live in — ``project.paths.experiments``.

    One derivation shared by the stats row and the boot list, so the two
    payloads can never disagree on an experiment's ``dir``. *project* is
    required at both call sites for exactly that reason: an optional fallback
    to the literal ``"experiments"`` would let the boot shell group a renamed
    project's cards under one key while the stats fill patches them in under
    another, with no error anywhere.
    """
    return project_root / project.paths.experiments


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


class UnknownWindowPreset(ValueError):
    """Raised for a ``window_preset`` outside :data:`ALL_WINDOW_PRESETS`.

    A ``ValueError`` subclass so existing handlers keep working, but named so
    a server can answer 400 for it and 500 for anything else — the safe row
    builder swallows every other failure into ``row["error"]``, so this is the
    one exception that reaches a route.
    """


def validate_window_preset(window_preset: str) -> None:
    """Reject an unknown preset loudly, before any per-row work.

    A request-level mistake must raise, never masquerade as N broken rows
    (the donor validates identically in both of its public entries). Public so
    the dashboard server (DASH-3) can reject its ``--window`` boot value and a
    bad ``?window=`` query with the SAME message the row builders raise —
    a second copy of the wording would drift.
    """
    if window_preset not in ALL_WINDOW_PRESETS:
        allowed = ", ".join(sorted(ALL_WINDOW_PRESETS))
        raise UnknownWindowPreset(
            f"Unknown window preset {window_preset!r}. Choose one of: {allowed}."
        )


def _window_start(window_preset: str, now: datetime) -> datetime | None:
    """Left edge of the SPARKLINE's ``end_ts`` filter; ``None`` is unbounded.

    Anchored on *now*, not on the experiment's last look, so a finished
    experiment draws an empty sparkline under a short preset instead of
    back-dating its window. The verdict cells are unaffected — they are always
    the full series — so an empty ``spark`` beside a real verdict reads
    exactly as it should: "decided; nothing new in this window".
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
    the x axis instead of silently closing it). The mean itself goes back
    through the finite scrub: summing finite effects near the float ceiling
    overflows to ``inf``, which JSON cannot express.
    """
    n = len(rows)
    if n == 0:
        return []
    step = max(1, -(-n // _MAX_SPARK_BUCKETS))  # ceil(n / _MAX_SPARK_BUCKETS)
    out: list[list[Any]] = []
    for i in range(0, n, step):
        chunk = rows[i : i + step]
        values = [
            value for value in (_num(row.get("effect")) for row in chunk) if value is not None
        ]
        mean_value = _num(float(np.mean(values))) if values else None
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
        "timezone": None,
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
        "rationale": [],
        "caveats": [],
        "guardrail_regressed": False,
        "last_end_ts": None,
        "spark": [],
        "verdicts": [],
        "warnings": [],
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
    # Every instant on the row is naive UTC; without the experiment's own zone
    # a client renders it in the browser's, and m10 made "the calendar day a
    # look covers" a timezone-sensitive contract.
    row["timezone"] = experiment.timezone
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


def _declared_pairs_only(experiment: ExperimentConfig, rows: Sequence[dict]) -> list[dict]:
    """Drop rows whose arm pair is not among the CURRENTLY declared variants.

    Mirrors ``reporting/builder.py``, which filters the same way before it
    calls ``evaluate`` — and it is not cosmetic: ``readout._filter_rows`` drops
    rows only by metric and ``method_config_id``, while the read-time
    Benjamini-Hochberg family is built from EVERY informative row at a cutoff.
    Rows left behind by a mid-flight arm rename would otherwise inflate the
    family and tighten every member's threshold, so the dashboard would
    contradict ``abk run --report`` on identical rows.
    """
    declared = set(combinations(experiment.assignment.variants, 2))
    return [row for row in rows if (str(row["name_1"]), str(row["name_2"])) in declared]


def _declared_pair_warning(experiment: ExperimentConfig, dropped: int) -> tuple[str, ...]:
    """The report's own wording for rows dropped by :func:`_declared_pairs_only`.

    Mirroring the filter without mirroring its loudness would make a renamed
    arm look exactly like a never-run experiment on the one surface an
    operator watches.
    """
    if dropped <= 0:
        return ()
    return (
        f"{experiment.name}: ignored {dropped} persisted rows for variant pairs "
        "outside the declared variants (renamed arms?) — run `abk clean`",
    )


def _fill_lock(
    row: dict[str, Any], *, experiment: ExperimentConfig, tables: InternalTablesManager
) -> None:
    """Probe the pipeline lock, isolated in both directions.

    Called from a ``finally``, so it runs on the degrade path too, and
    swallowing its own failure so the statistics survive an unreadable
    ``_ab_tasks`` (a partially-completed ``ensure_tables()`` leaves
    ``_ab_results`` created and ``_ab_tasks`` absent; a read-only dashboard
    credential can be granted one and not the other). Failing to ``False``
    leaves Run enabled, which is safe: the spawned ``abk run`` takes the real
    lock itself and refuses.
    """
    try:
        row["locked"] = (
            tables.check_lock(experiment.name, _LOCK_SCOPE, _LOCK_PROCESS_TYPE) is not None
        )
    except Exception:  # noqa: BLE001 — a cosmetic flag must not cost the row
        row["locked"] = False


def _fill_stats(
    row: dict[str, Any],
    *,
    experiment: ExperimentConfig,
    project: ProjectConfig,
    tables: InternalTablesManager,
    window_preset: str,
    now: datetime,
) -> None:
    """Populate everything that needs the database: verdict, SRM, spark, lock.

    A project that has never run has no ``_ab_results`` table; that is the
    honest "no data yet" state, not an error, so the stats stay at their
    ``_empty_row`` defaults (the reporting builder's ``results_table_exists``
    precedent — a read-only surface never creates schema).
    """
    if not tables.results_table_exists():
        return

    loaded = tables.load_results(experiment.name)
    rows = _declared_pairs_only(experiment, loaded)
    warnings = list(_declared_pair_warning(experiment, len(loaded) - len(rows)))

    # Whole-experiment health, deliberately NOT windowed (module docstring).
    srm_flag, srm_pvalue = srm_summary(experiment, rows)
    row["srm_flag"] = bool(srm_flag)
    row["srm_pvalue"] = _num(srm_pvalue)

    # The FULL series, never the window — these rows are CUMULATIVE looks, so
    # dropping the oldest is not a shorter experiment, it is a truncated
    # stabilization history (module docstring).
    readout = evaluate(experiment, rows, project=project)
    if not readout.verdicts:
        # Unreachable through a validated config (≥1 main comparison and ≥2
        # variants are both enforced), so degrade rather than index blind.
        raise ValueError(
            f"experiment {experiment.name!r} produced no verdicts — "
            "a main comparison and a treatment arm are both required"
        )

    headline = readout.verdicts[0]
    row["verdict"] = headline.verdict
    row["effect"] = _num(headline.effect)
    row["ci"] = [_num(headline.left_bound), _num(headline.right_bound)]
    row["pvalue"] = _num(headline.pvalue)
    row["alpha"] = _num(headline.alpha)
    row["elapsed_days"] = _num(headline.elapsed_days)
    row["is_horizon"] = bool(headline.is_horizon)
    row["weekly_cycle_pct"] = _num(headline.weekly_cycle_pct)
    # The cutoff every stat cell above is as of — the headline pair's latest
    # look, NOT the experiment's latest row (another metric can be ahead).
    row["last_end_ts"] = None if headline.end_ts is None else _ms(headline.end_ts)
    # A verdict the readout QUALIFIED must never render as an unqualified one:
    # under ``guardrail_policy: warn`` a WIN is kept with a mandatory loud
    # caveat, and a row carrying only the word "WIN" would hand the operator
    # exactly the green light the policy withheld. ``rationale``/``caveats``
    # describe the HEADLINE (they explain the cells beside them), while
    # ``guardrail_regressed`` is ORed across every listed pair — a safety flag
    # must not go green because the regression happened on another arm.
    row["rationale"] = list(headline.rationale)
    row["caveats"] = list(headline.caveats)
    row["guardrail_regressed"] = any(
        guardrail.regressed for verdict in readout.verdicts for guardrail in verdict.guardrails
    )
    # Named `verdicts`, matching ``ExperimentReadout.verdicts`` — deliberately
    # NOT `comparisons`, which is the boot entry's CONFIGURED list. DASH-5
    # merges the two payloads by experiment name, and one key holding two
    # incompatible shapes is a trap.
    row["verdicts"] = [
        {
            "metric": verdict.metric,
            # The report payload's arm vocabulary (`_verdict_to_payload`), so
            # the two surfaces name a pair the same way.
            "pair": {"c": verdict.name_1, "t": verdict.name_2},
            "verdict": verdict.verdict,
            "effect": _num(verdict.effect),
            "caveats": list(verdict.caveats),
            "guardrail_regressed": any(guardrail.regressed for guardrail in verdict.guardrails),
        }
        for verdict in readout.verdicts
    ]
    # The two states `abk clean` exists for are invisible unless the row says
    # so: the report warns about them and this surface is the one an operator
    # actually watches.
    row["warnings"] = warnings + list(readout.warnings)

    # The preset is the sparkline's x-range and nothing else — see above.
    start = _window_start(window_preset, now)
    windowed = rows if start is None else [r for r in rows if r["end_ts"] >= start]
    row["spark"] = _spark_series(_pair_rows(experiment, windowed, headline))


def _fill_row(
    row: dict[str, Any],
    *,
    project_root: Path,
    experiments_dir: Path,
    experiment_path: Path,
    experiment: ExperimentConfig,
    project: ProjectConfig,
    tables: InternalTablesManager,
    window_preset: str,
    now: datetime,
) -> None:
    """Config fields, then stats, then the lock probe — which runs on BOTH
    paths, in a ``finally``. Probed only after a successful read it would
    report "unlocked" for every degraded row, and a degraded row is exactly
    the one an operator is most likely to press Run on."""
    _fill_config_fields(
        row,
        project_root=project_root,
        experiments_dir=experiments_dir,
        experiment_path=experiment_path,
        experiment=experiment,
    )
    try:
        _fill_stats(
            row,
            experiment=experiment,
            project=project,
            tables=tables,
            window_preset=window_preset,
            now=now,
        )
    finally:
        _fill_lock(row, experiment=experiment, tables=tables)


def build_experiment_row(
    *,
    project_root: Path,
    experiment_path: Path,
    experiment: ExperimentConfig,
    project: ProjectConfig,
    tables: InternalTablesManager,
    window_preset: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One experiment's dashboard row — the unit the stats route serves.

    Raises ``ValueError`` for an unknown *window_preset*, and propagates
    anything the read or the readout raises; use
    :func:`build_experiment_row_safe` on any path that renders a list.

    Every verdict cell is the FULL series' — *window_preset* bounds the
    sparkline only (module docstring), so the row never disagrees with what
    ``abk run --report`` shows. *now* is the anchor that sparkline window
    counts back from; it defaults to the wall clock and a tz-aware value is
    converted to naive UTC rather than re-labelled.
    """
    validate_window_preset(window_preset)
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
    project: ProjectConfig,
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
    validate_window_preset(window_preset)
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
    project: ProjectConfig,
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
                "timezone": experiment.timezone,
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
