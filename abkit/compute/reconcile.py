"""Cross-backend reconciliation — the `abk verify-incremental` engine (m9 WP5).

Answers the one question that gates flipping ``compute.incremental_reads``
to true for a project: *does the additive read path reproduce the
full-window recompute across the WHOLE series, not just the latest cutoff?*
(cumulative-intervals.md §4 asks for exactly that framing — a single-cutoff
check would miss a state-accumulation drift that only appears after many
days.)

For every already-computed cutoff of every STATE-eligible comparison the
engine loads the data BOTH ways and diffs the resulting ``TestResult``
dicts field by field at the project's standard tolerance (rel-1e-9 /
abs-1e-12; §0.1 — never ``==`` on a float, since the two read paths sum in
different orders by design).

Three honesty properties this engine is built around:

1. **A fallback is NOT a pass.** When the incremental backend falls back to
   recompute for a cutoff (a state gap, a non-finite tail), both sides run
   the same code and agree trivially. Counting that as verified would be a
   lie, so such cutoffs are reported separately as UNVERIFIED and the
   summary says how many.
2. **It verifies the backend the pipeline actually runs** — both this
   engine and the driver construct the reader through the one
   ``build_incremental_backend`` factory.
3. **It writes nothing and takes no lock.** Reconciliation is a read-only
   maintainer command: it never persists, so it can run against a live
   project without racing `abk run` for the `_ab_tasks` lock (unlike `abk
   validate`, which writes `_ab_aa_runs` and therefore does take one). It
   must never run as part of a normal `abk run` — it costs strictly more
   than the run it checks.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from abkit.compute.incremental_backend import build_incremental_backend
from abkit.config.experiment_config import ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.database.internal_tables import InternalTablesManager
from abkit.database.manager import BaseDatabaseManager
from abkit.loaders.exposure_source import build_cohort_backend
from abkit.pipeline.analyze import analyze_cutoff, effective_alphas
from abkit.pipeline.state import comparison_state_eligible
from abkit.stats import TestResult

#: the project's standard parity tolerances (m9 §0.1)
DEFAULT_REL_TOL = 1e-9
DEFAULT_ABS_TOL = 1e-12

#: ``TestResult`` fields that carry no cross-backend signal: identity that is
#: equal by construction (both sides analyze the same comparison config).
_IDENTITY_FIELDS = frozenset({"method_name", "method_params", "alpha", "name_1", "name_2"})

Logger = Callable[[str], None]


def _noop_log(_: str) -> None:  # pragma: no cover - trivial
    return None


@dataclass(frozen=True)
class FieldDiff:
    """One diverging field of one pair at one cutoff."""

    field: str
    recompute: Any
    incremental: Any

    def describe(self) -> str:
        if isinstance(self.recompute, float) and isinstance(self.incremental, float):
            scale = max(abs(self.recompute), abs(self.incremental))
            rel = abs(self.recompute - self.incremental) / scale if scale else float("inf")
            return f"{self.field}: {self.recompute!r} vs {self.incremental!r} (rel {rel:.3g})"
        return f"{self.field}: {self.recompute!r} vs {self.incremental!r}"


@dataclass(frozen=True)
class PairVerdict:
    """One (comparison, cutoff, variant pair) reconciliation outcome."""

    metric: str
    method_config_id: str
    name_1: str
    name_2: str
    end_ts: datetime
    status: str  # "match" | "mismatch" | "unverified"
    diffs: tuple[FieldDiff, ...] = ()
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.status != "mismatch"


@dataclass
class MetricSkip:
    """A comparison the engine could not (or need not) reconcile."""

    metric: str
    reason: str


@dataclass
class ReconcileOutcome:
    """One experiment's whole-series reconciliation summary."""

    experiment: str
    verdicts: list[PairVerdict] = field(default_factory=list)
    skipped: list[MetricSkip] = field(default_factory=list)
    error: str | None = None

    @property
    def matched(self) -> list[PairVerdict]:
        return [v for v in self.verdicts if v.status == "match"]

    @property
    def mismatches(self) -> list[PairVerdict]:
        return [v for v in self.verdicts if v.status == "mismatch"]

    @property
    def unverified(self) -> list[PairVerdict]:
        return [v for v in self.verdicts if v.status == "unverified"]

    @property
    def cutoffs_checked(self) -> int:
        return len({(v.metric, v.method_config_id, v.end_ts) for v in self.verdicts})

    @property
    def ok(self) -> bool:
        """Pass iff nothing diverged. An unverified cutoff is not a failure —
        it is a coverage gap the report states out loud."""
        return self.error is None and not self.mismatches


def compare_results(
    recompute: TestResult | None,
    incremental: TestResult | None,
    rel_tol: float = DEFAULT_REL_TOL,
    abs_tol: float = DEFAULT_ABS_TOL,
) -> list[FieldDiff]:
    """Diff two ``TestResult``s field by field; empty list == agreement.

    Compares ``to_dict()`` (derived from the dataclass fields, so a future
    persisted column is covered without touching this function): floats
    within tolerance, everything else — ints, bools, strings, the warnings
    list, the diagnostics sub-dict — exactly. A demoted pair (``None``,
    insufficient data) must be demoted on both sides.
    """
    if recompute is None or incremental is None:
        if recompute is None and incremental is None:
            return []
        return [FieldDiff("insufficient_data", recompute is None, incremental is None)]
    # A both-demoted pair carries no TestResult to compare; the caller in
    # reconcile_experiment additionally diffs the demoted unit counts.

    left = recompute.to_dict()
    right = incremental.to_dict()
    diffs: list[FieldDiff] = []
    for key in sorted(set(left) | set(right)):
        if key in _IDENTITY_FIELDS:
            continue
        diffs.extend(_diff_value(key, left.get(key), right.get(key), rel_tol, abs_tol))
    return diffs


def _diff_value(
    key: str, left: Any, right: Any, rel_tol: float, abs_tol: float
) -> Iterable[FieldDiff]:
    if isinstance(left, dict) and isinstance(right, dict):
        for sub in sorted(set(left) | set(right)):
            yield from _diff_value(f"{key}.{sub}", left.get(sub), right.get(sub), rel_tol, abs_tol)
        return
    # bool is an int subclass — compare it exactly, never as a float
    if (
        isinstance(left, float)
        and isinstance(right, float)
        and not isinstance(left, bool)
        and not isinstance(right, bool)
    ):
        if math.isclose(left, right, rel_tol=rel_tol, abs_tol=abs_tol):
            return
        yield FieldDiff(key, left, right)
        return
    if left != right:
        yield FieldDiff(key, left, right)


def reconcile_experiment(
    experiment: ExperimentConfig,
    metrics_by_name: dict[str, MetricConfig],
    project: ProjectConfig,
    manager: BaseDatabaseManager,
    tables: InternalTablesManager,
    project_root: Path | None = None,
    metric_filter: str | None = None,
    rel_tol: float = DEFAULT_REL_TOL,
    abs_tol: float = DEFAULT_ABS_TOL,
    log: Logger = _noop_log,
) -> ReconcileOutcome:
    """Reconcile every computed cutoff of one experiment across both backends.

    Read-only (no lock, no writes — see the module docstring). Comparisons
    that have no incremental path at all (bootstrap, stratified, explicit
    covariate) are reported as skipped with the reason, never silently
    counted as passing.
    """
    outcome = ReconcileOutcome(experiment=experiment.name)
    alphas = effective_alphas(experiment, project)
    grid = experiment.grid(limit=project.limits.max_looks)

    backend, snapshot = build_cohort_backend(
        manager, experiment, project_root, grid, with_snapshot=True
    )
    fallbacks: set[tuple[str, datetime]] = set()
    incremental = build_incremental_backend(
        manager,
        tables,
        experiment,
        backend,
        snapshot,
        grid,
        project_root=project_root,
        on_fallback=lambda metric_name, _kind, end_ts: fallbacks.add((metric_name, end_ts)),
    )

    for comparison in experiment.comparisons:
        metric = metrics_by_name[comparison.metric]
        if metric_filter is not None and metric.name != metric_filter:
            continue
        metric_sql = metric.get_query_text(project_root)
        if not comparison_state_eligible(comparison, metric, metric_sql):
            outcome.skipped.append(
                MetricSkip(
                    metric=metric.name,
                    reason=(
                        "not state-eligible — bootstrap, stratified and "
                        "explicit-covariate comparisons always use full recompute, "
                        "so there is no incremental path to verify"
                    ),
                )
            )
            continue

        method_config_id = comparison.method.method_config_id
        computed = tables.list_computed_cutoffs(experiment.name, metric.name, method_config_id)
        cutoffs = [c for c in grid.cutoffs if c.end_ts in computed]
        # A persisted cutoff the CURRENT grid no longer produces (the schedule
        # was edited: horizon_ts moved, the cadence or interval_anchor changed,
        # max_looks lowered)
        # cannot be reconciled — the incremental read needs a grid cutoff to
        # load. Silently intersecting them away would let a clean exit-0
        # report hide a whole unexamined chunk of the series, which is exactly
        # the "whole series, not just the latest cutoff" promise this command
        # exists to keep (an R1 review finding).
        off_grid = sorted(computed - {c.end_ts for c in grid.cutoffs})
        if off_grid:
            outcome.skipped.append(
                MetricSkip(
                    metric=metric.name,
                    reason=(
                        f"{len(off_grid)} computed cutoff(s) are NOT on the current grid "
                        f"(earliest {off_grid[0]:%Y-%m-%d %H:%M:%S}, latest "
                        f"{off_grid[-1]:%Y-%m-%d %H:%M:%S}) — the schedule changed since "
                        "they were computed, so they cannot be reconciled; `abk clean` "
                        "prunes a series the config no longer produces"
                    ),
                )
            )
        if not cutoffs:
            outcome.skipped.append(
                MetricSkip(
                    metric=metric.name,
                    reason="no computed cutoffs in _ab_results yet — run `abk run` first",
                )
            )
            continue

        log(f"VERIFY {experiment.name}/{metric.name}: {len(cutoffs)} computed cutoffs")
        for cutoff in cutoffs:
            before = len(fallbacks)
            loaded_incremental = incremental.load_cutoff(
                comparison, metric, metric_sql, grid, cutoff
            )
            fell_back = len(fallbacks) > before
            loaded_recompute = backend.load_cutoff(comparison, metric, metric_sql, grid, cutoff)

            # The sequential widening is a pure post-transform of (effect, SE)
            # applied identically to both sides, so it cannot mask a load
            # difference; it is deliberately NOT applied here — this command
            # certifies the READ path, and the raw fixed CI shows a divergence
            # most directly.
            outcomes_recompute = analyze_cutoff(
                experiment, comparison, metric, loaded_recompute, cutoff.end_ts, alphas, project
            )
            outcomes_incremental = analyze_cutoff(
                experiment, comparison, metric, loaded_incremental, cutoff.end_ts, alphas, project
            )
            by_pair = {(o.name_1, o.name_2): o for o in outcomes_incremental}
            for pair_outcome in outcomes_recompute:
                key = (pair_outcome.name_1, pair_outcome.name_2)
                other = by_pair.get(key)
                if fell_back:
                    outcome.verdicts.append(
                        PairVerdict(
                            metric=metric.name,
                            method_config_id=method_config_id,
                            name_1=pair_outcome.name_1,
                            name_2=pair_outcome.name_2,
                            end_ts=cutoff.end_ts,
                            status="unverified",
                            note=(
                                "the incremental read fell back to full recompute "
                                "for this cutoff — nothing to compare"
                            ),
                        )
                    )
                    continue
                if other is None:  # pragma: no cover - variant order is shared
                    outcome.verdicts.append(
                        PairVerdict(
                            metric=metric.name,
                            method_config_id=method_config_id,
                            name_1=pair_outcome.name_1,
                            name_2=pair_outcome.name_2,
                            end_ts=cutoff.end_ts,
                            status="mismatch",
                            diffs=(FieldDiff("pair", "present", "missing"),),
                        )
                    )
                    continue
                diffs = compare_results(
                    pair_outcome.result, other.result, rel_tol=rel_tol, abs_tol=abs_tol
                )
                # A pair demoted on BOTH sides carries no TestResult, so the
                # field diff is vacuously empty — but the demoted UNIT COUNTS
                # are still observable and must agree, otherwise a divergence
                # hiding under the small-sample floor reads as a match (an R1
                # review finding).
                if pair_outcome.result is None and other.result is None:
                    for label, left, right in (
                        ("size_1", pair_outcome.size_1, other.size_1),
                        ("size_2", pair_outcome.size_2, other.size_2),
                    ):
                        if left != right:
                            diffs.append(FieldDiff(label, left, right))
                outcome.verdicts.append(
                    PairVerdict(
                        metric=metric.name,
                        method_config_id=method_config_id,
                        name_1=pair_outcome.name_1,
                        name_2=pair_outcome.name_2,
                        end_ts=cutoff.end_ts,
                        status="mismatch" if diffs else "match",
                        diffs=tuple(diffs),
                    )
                )

    return outcome
