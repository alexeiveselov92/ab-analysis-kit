"""Implementation of ``abk plan`` — the read-only pre-launch power / sizing planner
(m5-implementation-plan.md WP6; cli-and-dx.md §1).

For each selected experiment it echoes the projected look count + cost shape from the
SAME ``generate_grid`` enumeration the run/validator use, then — per comparison — reads
the latest persisted per-arm moments from ``_ab_results`` (a ``--baseline`` override
supplies them for a greenfield experiment) and reports required-N / achievable-MDE /
achieved-power at the effective two-tier alpha.

Strictly read-only (D11): no lock, no ``_ab_*`` writes; its own manager is closed in a
``finally``. Honest refusals (D10): ratio metrics and resampling (bootstrap) methods
have no versioned power formula → SKIPPED with a reason, never sized with invented math;
CUPED is sized on the variance its own persisted ``corr_coef_1`` deflates (PLAN-1), and
the plan line always names which variance that was — raw or deflated, and why. A genuine
harness failure (bad selection / ``--baseline`` / warehouse error) exits non-zero; a
by-design refusal does not.

Runtime / ASN (WP-A, m6-implementation-plan.md): each sizable comparison also reports
days-to-required-N from a unit-arrival rate (derived read-only from the cohort source —
the persisted ``_ab_exposures`` copy under ``assignment.cohort_copy.enabled``, otherwise
a fresh snapshot of the live assignment source re-executed at invocation time — or
supplied via ``--arrival-rate``) and, for a ``sequential.enabled`` design, the
always-valid average sample number. No arrival data ⇒ runtime SKIPPED, never invented.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Any

import click

from abkit.cli._output import echo_done, echo_error, echo_tree
from abkit.cli.commands._context import load_project_context
from abkit.config import select_experiments
from abkit.config.experiment_config import ExperimentConfig
from abkit.config.project_config import ProjectConfig
from abkit.core.period_planner import GridLimitExceeded, as_local_datetime
from abkit.pipeline import comparison_alpha, effective_alphas
from abkit.planning.sizing import (
    FRACTION,
    SAMPLE,
    SUSPICIOUS_RESIDUAL_VARIANCE_SHARE,
    AsnResult,
    BaselineMoments,
    ComparisonPlan,
    RuntimePlan,
    asn_for,
    is_powered,
    moments_from_override,
    parse_baseline_overrides,
    runtime_for,
    size_comparison,
)
from abkit.stats import get_method_class
from abkit.stats.correction import READ_TIME_CORRECTIONS


def _resolved_correction(experiment: ExperimentConfig, project: ProjectConfig) -> str:
    """The scheme actually in force — the experiment's, else the project's."""
    return (
        experiment.correction
        if experiment.correction is not None
        else project.statistics.correction
    )


def run_plan(
    select: tuple[str, ...],
    metric: str | None,
    mde: float | None,
    power: float | None,
    alpha: float | None,
    baseline: tuple[str, ...],
    from_history: str | None,
    arrival_rate: float | None,
    profile: str | None,
) -> None:
    context = load_project_context(require_profiles=True)
    click.echo(f"Project root: {context.root}")

    if alpha is not None and not 0.0 < alpha < 1.0:
        raise click.BadParameter(f"--alpha must be in (0, 1), got {alpha}", param_hint="--alpha")
    if power is not None and not 0.0 < power < 1.0:
        raise click.BadParameter(f"--power must be in (0, 1), got {power}", param_hint="--power")
    if mde is not None and mde <= 0:
        raise click.BadParameter(f"--mde must be > 0, got {mde}", param_hint="--mde")
    if arrival_rate is not None and arrival_rate <= 0:
        raise click.BadParameter(
            f"--arrival-rate must be > 0, got {arrival_rate}", param_hint="--arrival-rate"
        )
    try:
        overrides = parse_baseline_overrides(baseline)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--baseline") from exc
    if from_history is not None:
        _validate_history_interval(from_history)

    selected, selection_warnings = select_experiments(context.root, select)
    for warning in selection_warnings:
        click.echo(click.style(f"  ⚠ {warning}", fg="yellow"))
    if not selected:
        echo_done("Nothing selected.")
        return

    failed = 0
    for _, experiment in selected:
        try:
            _plan_one(
                experiment,
                context,
                profile,
                metric,
                mde,
                power,
                alpha,
                overrides,
                from_history,
                arrival_rate,
            )
        except click.ClickException:
            raise
        except Exception as exc:  # a warehouse/read failure on one experiment
            echo_error(experiment.name, f"plan failed: {exc}")
            failed += 1

    echo_done(f"{len(selected)} experiment(s) planned" + (f", {failed} FAILED" if failed else ""))
    if failed:
        raise SystemExit(1)


def _plan_one(
    experiment,
    context,
    profile,
    metric_filter,
    mde,
    power_opt,
    alpha_opt,
    overrides,
    from_history,
    arrival_rate,
) -> None:
    from abkit.database.internal_tables import InternalTablesManager

    project = context.project
    power = power_opt if power_opt is not None else project.statistics.power
    # honour --alpha without mutating the config: re-resolve the two-tier scheme off a
    # copy with the overridden experiment-level alpha, so the correction still divides it.
    exp_for_alpha = (
        experiment.model_copy(update={"alpha": alpha_opt}) if alpha_opt is not None else experiment
    )
    alphas = effective_alphas(exp_for_alpha, project)

    comparisons = experiment.comparisons
    if metric_filter is not None:
        comparisons = [c for c in comparisons if c.metric == metric_filter]
        if not comparisons:
            configured = ", ".join(c.metric for c in experiment.comparisons)
            raise click.ClickException(
                f"--metric '{metric_filter}' is not a comparison of '{experiment.name}' "
                f"(have: {configured})"
            )

    # look-count + cost shape from the one shared enumeration (§6.4). Bound it by
    # max_looks like the run planner so a pathological sub-day grid fails fast instead of
    # OOM-enumerating in this read-only command (plan skips the config-lint that would
    # otherwise catch it — M5 exit-gate round-1 finding).
    try:
        grid = experiment.grid(limit=project.limits.max_looks)
    except GridLimitExceeded as exc:
        raise click.ClickException(
            f"{experiment.name}: planned looks exceed max_looks="
            f"{project.limits.max_looks} — coarsen the cadence or raise the limit ({exc})"
        ) from exc
    looks = len(grid)
    # the DECLARED contrast set, not C(g,2): under `contrasts: vs_control` the
    # pipeline writes g−1 pair rows per look, and this estimate is the number an
    # operator sizes storage on (m13 STAT-1b)
    pairs = len(experiment.contrast_pairs())
    rows_per_refresh = looks * pairs * len(experiment.comparisons)

    # WP-A: the shared look schedule (cumulative days since the pinned left edge) and
    # planned horizon length — the ASN process and days-to-N both live on this axis.
    look_days = [(c.end_ts - grid.start_ts).total_seconds() / 86400.0 for c in grid.cutoffs]
    horizon_days = (grid.horizon_ts - grid.start_ts).total_seconds() / 86400.0

    plans: list[ComparisonPlan] = []
    manager = context.profiles.create_manager(profile)
    try:
        tables = InternalTablesManager(manager)
        has_results = tables.results_table_exists()
        rate_control, rate_source = _resolve_arrival_rate(
            experiment, arrival_rate, tables, manager, context.root, grid
        )
        history = (
            _HistoryBaselines(experiment, context, manager, grid, from_history)
            if from_history is not None
            else None
        )
        for comparison in comparisons:
            plans.append(
                _plan_comparison(
                    experiment,
                    comparison,
                    alphas,
                    power,
                    mde,
                    overrides.get(comparison.metric),
                    tables if has_results else None,
                    rate_control,
                    rate_source,
                    look_days,
                    horizon_days,
                    history,
                )
            )
    finally:
        manager.close()

    _emit_plan(experiment, project, alphas, power, looks, grid, rows_per_refresh, plans)


def _resolve_arrival_rate(
    experiment, override: float | None, tables, manager, project_root, grid
) -> tuple[float | None, str]:
    """Control-arm units/day for the runtime/ASN axis (WP-A), or ``(None, reason)``.

    ``--arrival-rate`` is total traffic across all arms → split to the control arm by the
    normalized ``expected_split`` (so it lines up with required-N, which is per control
    arm). Absent an override the rate is derived read-only from the cohort source: the
    persisted ``_ab_exposures`` copy under ``assignment.cohort_copy.enabled``, otherwise
    (the m8 WP4 no-copy default) a fresh in-memory snapshot of the live assignment
    source — re-executed at invocation time, the documented cost/freshness tradeoff.
    An empty cohort or a backfilled cohort spanning ~one instant yields no rate —
    runtime is then SKIPPED with a reason, never guessed.
    """
    variants = experiment.assignment.variants
    control = variants[0]
    split = experiment.assignment.expected_split
    total_weight = sum(split.values()) or float(len(variants))
    control_share = (split.get(control, 0.0) or 0.0) / total_weight
    if control_share <= 0.0:
        control_share = 1.0 / len(variants)

    if override is not None:
        return (
            override * control_share,
            f"--arrival-rate {override:g}/day → {control_share:.0%} control",
        )

    if experiment.assignment.cohort_copy.enabled:
        # copy mode: the persisted-table derivation, unchanged
        if not tables.exposures_table_exists():
            return None, "no _ab_exposures yet — pass --arrival-rate <units/day>"
        arrivals = tables.get_arrival_rate(experiment.name, list(variants))
        if arrivals is None:
            # get_arrival_rate returns None for BOTH an empty cohort and a one-instant
            # window; disambiguate so the skip reason is truthful (a never-run experiment
            # in a project where OTHER experiments have run reaches here with zero rows).
            if tables.count_exposures(experiment.name) == 0:
                return (
                    None,
                    "no exposures for this experiment yet — pass --arrival-rate <units/day>",
                )
            return (
                None,
                "arrival rate underivable (exposures span ~one instant) — pass --arrival-rate",
            )
        rates, window_days = arrivals
        source = f"_ab_exposures over {window_days:.1f} observed days"
    else:
        # direct mode (m8 WP4): snapshot the live source; the SAME rate arithmetic
        # through core.exposure_counting — the two modes can never drift. A genuine
        # contract violation (missing columns, cross-variant) still fails the plan
        # loudly; only the not-yet-launched empty source politely skips.
        from abkit.core.exposure_counting import arrival_rate as shared_arrival_rate
        from abkit.core.exposure_counting import bucket_timestamps
        from abkit.loaders.exposure_source import (
            EmptyCohortError,
            render_assignment_sql,
            validate_and_snapshot,
        )

        try:
            rendered = render_assignment_sql(manager, experiment, project_root, grid)
            snapshot = validate_and_snapshot(manager, experiment, rendered)
        except EmptyCohortError:
            return (
                None,
                "assignment source returned no rows yet — pass --arrival-rate <units/day>",
            )
        per_variant = bucket_timestamps(
            ((variant, ts) for variant, ts, _ in snapshot.by_unit.values()), list(variants)
        )
        arrivals = shared_arrival_rate(per_variant, list(variants))
        if arrivals is None:
            return (
                None,
                "arrival rate underivable (exposures span ~one instant) — pass --arrival-rate",
            )
        rates, window_days = arrivals
        source = f"assignment source over {window_days:.1f} observed days"

    rate_control = rates.get(control, 0.0)
    if rate_control <= 0.0:
        return None, "no control-arm exposures — pass --arrival-rate <units/day>"
    return rate_control, source


def _plan_comparison(
    experiment,
    comparison,
    alphas,
    power: float,
    mde: float | None,
    override: dict[str, float] | None,
    tables,
    rate_control: float | None = None,
    rate_source: str = "",
    look_days: list[float] | None = None,
    horizon_days: float = 0.0,
    history: _HistoryBaselines | None = None,
) -> ComparisonPlan:
    role = (
        "main"
        if comparison.is_main_metric
        else "guardrail" if comparison.is_guardrail else "secondary"
    )
    method_name = comparison.method.name
    method_cls = get_method_class(method_name)

    # honest-refusal dispatch on DECLARATIVE capability (invariant 3), never a name check.
    needs_seed = any(spec.name == "seed" for spec in method_cls.param_specs)
    if method_cls.is_paired:
        return ComparisonPlan(comparison.metric, method_name, role, refused="paired design")
    if needs_seed:
        return ComparisonPlan(
            comparison.metric,
            method_name,
            role,
            refused="resampling method — no closed-form power (measure it with "
            "`abk validate --inject-effect`)",
        )
    if method_cls.input_kind == "ratio":
        return ComparisonPlan(
            comparison.metric,
            method_name,
            role,
            refused="ratio metric — no versioned power formula",
        )

    kind = FRACTION if method_cls.input_kind == "fraction" else SAMPLE
    defaults = {spec.name: spec.default for spec in method_cls.param_specs}
    test_type = str(
        comparison.method.params.get("test_type", defaults.get("test_type", "relative"))
    )
    alpha = comparison_alpha(comparison, alphas)
    target_mde = mde if mde is not None else comparison.min_effect

    # The interval shape is a PARAM (m13 STAT-3), so only the bound instance can
    # answer whether this comparison's CI is symmetric — the class default would
    # answer for a configuration nobody wrote. Bad params are config-lint's to
    # explain; the planner refuses rather than sizing a design that will not run.
    try:
        asymmetric_ci = bool(comparison.method.bind(alpha=alpha).asymmetric_ci)
    except Exception as exc:
        return ComparisonPlan(
            comparison.metric,
            method_name,
            role,
            refused=f"method params rejected ({exc}) — run `abk run --steps validate`",
            kind=kind,
            test_type=test_type,
            alpha=alpha,
        )

    notes: list[str] = []
    if asymmetric_ci:
        notes.append(
            "sizing uses the normal (Wald) power formula while the analysis will "
            "invert the score statistic — the two rules differ by O(z²/N), so read "
            "the MDE as the planning figure, not as the boundary the readout applies"
        )
    moments = _resolve_moments(experiment, comparison, kind, override, tables, notes, history)
    if moments is None:
        refusal = _no_baseline_refusal(comparison, history)
        return ComparisonPlan(
            comparison.metric,
            method_name,
            role,
            refused=refusal,
            kind=kind,
            test_type=test_type,
            alpha=alpha,
            power=power,
            target_mde=target_mde,
        )
    if method_cls.requires_covariate:
        notes.append(_covariate_note(moments))

    plan_ratio = _plan_ratio(experiment)
    result = size_comparison(
        moments,
        test_type=test_type,
        alpha=alpha,
        power=power,
        target_mde=target_mde,
        plan_ratio=plan_ratio,
    )
    runtime = _build_runtime(
        experiment,
        method_cls,
        result,
        moments,
        test_type=test_type,
        alpha=alpha,
        target_mde=target_mde,
        plan_ratio=plan_ratio,
        rate_control=rate_control,
        rate_source=rate_source,
        look_days=look_days,
        horizon_days=horizon_days,
        asymmetric_ci=asymmetric_ci,
    )
    return ComparisonPlan(
        metric=comparison.metric,
        method_name=method_name,
        role=role,
        kind=kind,
        test_type=test_type,
        alpha=alpha,
        power=power,
        baseline=moments,
        target_mde=target_mde,
        plan_ratio=plan_ratio,
        result=result,
        runtime=runtime,
        notes=notes,
    )


def _build_runtime(
    experiment,
    method_cls,
    result,
    moments,
    *,
    test_type: str,
    alpha: float,
    target_mde: float | None,
    plan_ratio: float,
    rate_control: float | None,
    rate_source: str,
    look_days: list[float] | None,
    horizon_days: float,
    asymmetric_ci: bool = False,
) -> RuntimePlan:
    """Days-to-required-N + the always-valid ASN for one sized comparison (WP-A).

    Without an arrival rate, runtime is SKIPPED (``rate_control_per_day=None``). ASN is
    emitted only for a sequential-eligible method under ``sequential.enabled`` with a
    target MDE; otherwise ``asn_note`` records why it is n/a — never a fixed-horizon N
    dressed up as sequential.
    """
    if rate_control is None:
        return RuntimePlan(None, rate_source, None, horizon_days, None, None)

    days_to_required_n = runtime_for(result.required_n, rate_control)
    asn: AsnResult | None = None
    asn_note: str | None = None
    if asymmetric_ci:
        # m13 STAT-3: `abk run` refuses the always-valid mode for an asymmetric
        # interval (config-lint errors on the pair), so printing an ASN here would
        # size a design the pipeline will not run — the "a surface that prints a
        # level must know the scheme" rule, applied to a sequential number.
        asn_note = "fixed-horizon (asymmetric interval — the always-valid mode is refused)"
    elif not method_cls.supports_sequential:
        asn_note = "fixed-horizon (resampling method — not sequential-eligible)"
    elif not experiment.sequential.enabled:
        asn_note = "fixed-horizon design (set sequential.enabled for anytime ASN)"
    elif target_mde is None:
        asn_note = "no target MDE (pass --mde for a sequential ASN)"
    else:
        asn = asn_for(
            moments,
            test_type=test_type,
            target_mde=target_mde,
            alpha=alpha,
            plan_ratio=plan_ratio,
            look_days=look_days or [],
            rate_control_per_day=rate_control,
        )
        if asn is None:
            asn_note = "ASN n/a (degenerate baseline or <2 usable looks)"
    asn_below_required = bool(
        asn is not None
        and result.required_n is not None
        and math.isfinite(result.required_n)
        and asn.asn_n_h1 < result.required_n
    )
    return RuntimePlan(
        rate_control,
        rate_source,
        days_to_required_n,
        horizon_days,
        asn,
        asn_note,
        asn_below_required,
    )


def _plan_ratio(experiment) -> float:
    """Forward-looking treatment:control allocation from ``expected_split`` (defaults 1.0)."""
    variants = experiment.assignment.variants
    split = experiment.assignment.expected_split
    control = split.get(variants[0])
    treatment = split.get(variants[1])
    if control and treatment and control > 0:
        return treatment / control
    return 1.0


class _HistoryBaselines:
    """`--from-history`: population moments from the N whole days before the start.

    Sizing needs per-unit moments, and before PLAN-2 they came only from a previous
    ``abk run`` of this same experiment or a hand-typed ``--baseline`` — so the
    pre-launch case, the one planning is actually for, read ``SKIPPED: no baseline``.

    The read is deliberately POPULATION-wide: an experiment that has not launched has
    no cohort to join, so the render measures everything the metric SQL yields over
    ``[tz-midnight(start − interval), tz-midnight(start))``. That is a strictly better
    input than a guess and strictly worse than a pilot run — the plan line says so,
    especially when ``assignment.added_filters`` narrows the real cohort.

    Lazy and cached per metric: a plan touches each metric once, and a comparison that
    refuses (ratio/bootstrap) must not pay for a render it will never use.
    """

    def __init__(
        self, experiment: Any, context: Any, manager: Any, grid: Any, interval: str
    ) -> None:
        self._experiment = experiment
        self._context = context
        self._manager = manager
        self._grid = grid
        self.interval = interval
        self._backend: Any = None
        self._cache: dict[str, BaselineMoments | None] = {}
        self.failures: dict[str, str] = {}

    @property
    def label(self) -> str:
        return f"history {self.interval}"

    def moments(self, comparison: Any, kind: str) -> BaselineMoments | None:
        """Population moments for one comparison, or ``None`` with a recorded reason.

        A render failure refuses THAT comparison (the caller turns it into a SKIPPED
        line naming the reason) rather than failing the command: one unreadable metric
        must not cost the whole plan (the same discipline the row shaper uses).
        """
        metric_name = comparison.metric
        if metric_name in self._cache:
            return self._cache[metric_name]
        try:
            moments = self._load(metric_name, kind)
        except Exception as exc:  # a render/SQL/shape failure on ONE metric
            self.failures[metric_name] = str(exc)
            moments = None
        self._cache[metric_name] = moments
        return moments

    def _load(self, metric_name: str, kind: str) -> BaselineMoments | None:
        from abkit.loaders.exposure_source import build_population_backend
        from abkit.loaders.query_template import POPULATION_VARIANT
        from abkit.pipeline.analyze import build_container

        metric = self._context.metrics_by_name.get(metric_name)
        if metric is None:
            raise ValueError(f"metric '{metric_name}' is not in the library")
        if self._backend is None:
            self._backend = build_population_backend(self._manager, self._experiment)
        loaded = self._backend.load_population_window(
            metric, metric.get_query_text(self._context.root), self.interval, self._grid
        )
        if POPULATION_VARIANT not in loaded.variants():
            raise ValueError("the history window returned no rows")
        # The pipeline's OWN container → suffstats path, never a hand-rolled np.std:
        # `_ab_results.std_1` carries the legacy mixed-ddof convention, and two baseline
        # sources that disagreed by a ddof would be quietly incomparable.
        container = build_container(kind, POPULATION_VARIANT, loaded)
        source = f"{self.label} @ {self._experiment.name}"
        if kind == FRACTION:
            return BaselineMoments(
                FRACTION, container.prop, container.nobs, container.nobs, None, source
            )
        from abkit.stats import SufficientStats

        stats = SufficientStats.from_sample(container)
        if stats.sample_size < 2 or not stats.std > 0:
            raise ValueError(
                f"the history window yielded {stats.sample_size} unit(s) with no spread"
            )
        return BaselineMoments(
            SAMPLE,
            stats.mean,
            float(stats.sample_size),
            float(stats.sample_size),
            stats.std,
            source,
        )


def _history_notes(experiment: Any, history: _HistoryBaselines) -> list[str]:
    """What a population baseline is NOT, said on the line that uses it."""
    notes = [
        f"baseline from the {history.interval} before the start — POPULATION-wide "
        "(the experiment has no cohort to read yet), so `n` counts everyone the metric "
        "SQL yields, not the units this experiment will enroll"
    ]
    if experiment.assignment.added_filters:
        notes.append(
            "assignment.added_filters narrows the real cohort, and a population read "
            "cannot apply it — treat this variance as indicative, not as this "
            "experiment's own"
        )
    return notes


def _no_baseline_refusal(comparison: Any, history: _HistoryBaselines | None) -> str:
    """The SKIPPED reason, naming the history render's own failure when there was one."""
    if history is not None and comparison.metric in history.failures:
        return (
            f"no baseline — the {history.label} render failed: "
            f"{history.failures[comparison.metric]}"
        )
    hint = "" if history is not None else ", or pass --from-history <N d>"
    return "no baseline — run `abk run` first, or pass " f"--baseline {comparison.metric}:...{hint}"


def _validate_history_interval(raw: str) -> None:
    """`--from-history` is whole days — the grain `covariate_lookback` already uses."""
    from abkit.core.interval import Interval

    try:
        seconds = Interval(raw).seconds
    except Exception as exc:
        raise click.BadParameter(
            f"--from-history '{raw}' is not an interval (e.g. 14d, 2w)",
            param_hint="--from-history",
        ) from exc
    if seconds <= 0 or seconds % 86400 != 0:
        raise click.BadParameter(
            f"--from-history '{raw}' must be a positive WHOLE number of days "
            "(the pre-period window is day-aligned in the experiment timezone)",
            param_hint="--from-history",
        )


def _resolve_moments(
    experiment,
    comparison,
    kind: str,
    override,
    tables,
    notes: list[str],
    history: _HistoryBaselines | None = None,
) -> BaselineMoments | None:
    if override is not None:
        try:
            moments = moments_from_override(kind, override)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--baseline") from exc
        if (
            moments.corr_coef is not None
            and not get_method_class(comparison.method.name).requires_covariate
        ):
            # Deflating a method that will not run CUPED promises a variance reduction
            # the analysis cannot deliver — refuse where it was typed rather than print
            # an under-stated required-N.
            raise click.BadParameter(
                f"--baseline {comparison.metric}: 'corr' sizes a covariate method, but "
                f"{comparison.metric} is planned with '{comparison.method.name}' — drop "
                "'corr' or plan a cuped-t-test comparison",
                param_hint="--baseline",
            )
        return moments
    # Precedence (contract step 3): an explicit --baseline wins, then --from-history,
    # then the persisted rows. A hand-typed number is the operator's deliberate
    # statement; a history render is a measurement of the wrong population; a
    # persisted row is this experiment's own cohort and the most specific of all —
    # but it only exists once the experiment has run, which is the case
    # --from-history is for. Whichever wins names itself in the plan line.
    if history is not None:
        from_history = history.moments(comparison, kind)
        if from_history is not None:
            notes.extend(_history_notes(experiment, history))
            return from_history
        if comparison.metric in history.failures:
            return None  # the SKIPPED line names the render failure
    if tables is None:
        return None
    return _moments_from_results(experiment, comparison, kind, tables)


def _covariate_note(moments: BaselineMoments) -> str:
    """The one line a CUPED plan states about the variance it was sized on (PLAN-1).

    Every branch names what it used, because "required-N" without its variance basis
    is the number an operator would then plan a launch around. The pre-PLAN-1 note
    said the correlation "is not persisted", which M9 WP1 made false for every row
    written since ``0.4.0``.
    """
    rho = moments.usable_corr
    if rho is None:
        if moments.corr_coef is None:
            # Two different causes, two different fixes: a hand-typed baseline simply
            # omitted `corr=`, while a persisted one predates the 0.4.0 column. Naming
            # the wrong one sends the operator to re-run an experiment that would not
            # have helped (or to look for a flag that would not have applied).
            if moments.source.startswith("--baseline"):
                return (
                    "sized on RAW variance — the --baseline override carries no 'corr', "
                    "so required-N is an upper bound (add corr=<ρ> to size on CUPED)"
                )
            return (
                "sized on RAW variance — no covariate correlation on the baseline row "
                "(rows written before 0.4.0 carry none), so required-N is an upper bound"
            )
        return (
            f"sized on RAW variance — ρ = {moments.corr_coef:.6g} leaves no usable "
            "residual variance (the covariate reproduces the metric), so a deflated "
            "required-N would be rounding noise"
        )
    if rho == 0.0:
        return (
            "sized on RAW variance — the baseline's covariate correlation is ρ = 0, "
            "so CUPED reduces no variance here (this is a measurement, not a missing value)"
        )
    share = 1.0 - rho * rho
    note = (
        f"sized on CUPED-deflated variance (ρ = {rho:.4g}) — required-N is "
        f"{share:.3g}× the raw-variance bound"
    )
    if share < SUSPICIOUS_RESIDUAL_VARIANCE_SHARE:
        # Computable, so we compute it — but a >100× reduction is far more often a
        # covariate derived from the metric than a real one, and this is the number
        # someone sizes a launch on. Say so on the line, do not silently under-size.
        note += (
            f" — that is a {1.0 / share:,.0f}× reduction; check that the covariate is "
            "not derived from the metric"
        )
    return note


def _moments_from_results(experiment, comparison, kind: str, tables) -> BaselineMoments | None:
    """Latest usable persisted control/treatment moments for the first-pair series."""
    variants = experiment.assignment.variants
    name_1, name_2 = variants[0], variants[1]
    rows = tables.load_results(
        experiment.name, comparison.metric, method_config_id=comparison.method.method_config_id
    )
    latest = None
    for row in rows:  # ascending by end_ts ⇒ the last match is the most data-rich look
        if str(row.get("name_1")) != name_1 or str(row.get("name_2")) != name_2:
            continue
        if row.get("insufficient_data"):
            continue
        if row.get("value_1") is None:
            continue
        latest = row
    if latest is None:
        return None

    value_1 = _num(latest.get("value_1"))
    size_1 = latest.get("size_1")
    size_2 = latest.get("size_2")
    ts = latest.get("end_ts")
    source = f"persisted @ {ts.isoformat() if ts is not None else '?'}"
    if value_1 is None or not size_1 or not size_2:
        return None

    if kind == FRACTION:
        std_1 = _num(latest.get("std_1"))
        value_2 = _num(latest.get("value_2"))
        std_2 = _num(latest.get("std_2"))
        if std_1 is None or std_2 is None or value_2 is None:
            return None
        if not (0.0 < value_1 < 1.0 and 0.0 < value_2 < 1.0 and std_1 > 0 and std_2 > 0):
            return None
        nobs_1 = value_1 * (1.0 - value_1) / (std_1**2)
        nobs_2 = value_2 * (1.0 - value_2) / (std_2**2)
        return BaselineMoments(FRACTION, value_1, nobs_1, nobs_2, None, source)

    std_1 = _num(latest.get("std_1"))
    if std_1 is None or std_1 <= 0:
        return None
    # M9 WP1 persists the control arm's value↔covariate correlation beside its std, and
    # the rows are already filtered to THIS comparison's method_config_id — so a non-NULL
    # corr_coef_1 is this method's own CUPED correlation, never another method's.
    # `_num` maps NULL/NaN/inf to None; `usable_corr` makes the final call.
    corr_1 = _num(latest.get("corr_coef_1"))
    return BaselineMoments(SAMPLE, value_1, float(size_1), float(size_2), std_1, source, corr_1)


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ── rendering ──────────────────────────────────────────────────────────────────


def _fmt_effect(value: float | None, test_type: str) -> str:
    if value is None:
        return "—"
    if not math.isfinite(value):
        return "∞"
    if test_type == "relative":
        return f"{value * 100:.2f}%"
    return f"{value:.4g}"


def _fmt_n(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not math.isfinite(value):
        return "∞ (underpowered)"
    return f"{int(round(value)):,}"


def _correction_note(experiment, alphas, correction: str) -> str:
    """The header's alpha statement — the ONLY one `abk plan` prints.

    The per-comparison rows below it report sizing, never their own level, so a
    tier missing from this string is a tier the operator cannot see. m13 D8 added
    a third: an untiered guardrail is sized at the RAW alpha, and without naming
    it here the header actively contradicts the guardrail row beneath it. m13
    STAT-1b added the family the levels were divided by, for the same reason: at
    three arms `vs_control` and `all_pairs` print DIFFERENT main alphas off the
    same arm count, and only this string says why.

    It is APPENDED rather than folded into the main/secondary branch because it
    can equal ``main`` numerically (3 arms with one screening metric) while still
    being a different tier — a value-equality test would drop it exactly when the
    coincidence makes it most confusing.

    ``correction`` is REQUIRED rather than defaulted (m13 STAT-1): under a
    read-time scheme every level printed here is the raw alpha, and a caller that
    forgot to pass the scheme would print the most misleading header of the three
    — a per-comparison level that no comparison is actually judged at.
    """
    note = (
        f"main {alphas.main:.4g} / secondary {alphas.secondary:.4g}"
        if alphas.secondary is not None and alphas.secondary != alphas.main
        else f"per-comparison {alphas.main:.4g}"
    )
    if alphas.contrasts == "vs_control" and alphas.groups_count > 2 and alphas.main != alphas.alpha:
        # The divisor is g−1, not C(g,2) — say which family bought the level, or
        # the number cannot be reconciled with the arm count beside it. Gated on
        # the level having actually MOVED, the way the guardrail clause below is
        # gated on its tier existing: under `correction: none` nothing was
        # divided by anything, and naming a family there would explain a
        # division that did not happen.
        note += " (vs_control family)"

    if alphas.guardrail is not None and any(c.is_guardrail for c in experiment.comparisons):
        note += f" / guardrail {alphas.guardrail:.4g} (uncorrected)"

    if correction in READ_TIME_CORRECTIONS:
        # A read-time scheme has no compute-time level to print: every row carries
        # the RAW alpha and the decision is recomputed over the family at read time.
        # Silence would leave the header claiming the sizing level IS the decision
        # level, which is the optimistic direction — Holm's own first step is α/m.
        note += (
            f" — {correction} is applied read-time, so the level above is the raw "
            "alpha this plan sizes at; the decision threshold depends on the "
            "family's p-values"
        )
    return note


def _emit_plan(experiment, project, alphas, power, looks, grid, rows_per_refresh, plans) -> None:
    correction_note = _correction_note(
        experiment, alphas, _resolved_correction(experiment, project)
    )
    header = f"{experiment.name}: plan · α raw={alphas.alpha:.4g} → {correction_note} · power {power:.2f}"

    children: list[str] = []
    for plan in plans:
        children.extend(_plan_lines(plan))

    cadence = _fmt_cadence(experiment)
    children.append(
        f"looks: {looks} planned · cadence {cadence} · horizon "
        f"{_fmt_instant(experiment.horizon_ts)} · ~{rows_per_refresh:,} "
        f"_ab_results rows/full-refresh"
    )
    if experiment.is_sub_day():
        children.append("  sub-day: each look ≤ one day of fact rows (day-grained state, §6.4)")

    warnings: list[str] = []
    variants = experiment.assignment.variants
    if len(variants) > 2:
        others = len(experiment.contrast_pairs()) - 1
        family = (
            f"the other {others} vs-control contrast{'s' if others != 1 else ''} "
            "shares the same α"
            if experiment.contrasts == "vs_control"
            else "the other pairs share the same α"
        )
        warnings.append(
            f"{len(variants)}-arm experiment — sizing is shown for the "
            f"{variants[0]} vs {variants[1]} contrast only ({family})"
        )
    warn_looks = project.limits.warn_looks
    if looks > warn_looks and not experiment.sequential.enabled:
        warnings.append(
            f"{looks} looks > warn_looks={warn_looks} without sequential.enabled — "
            "peeking inflates the false-positive rate (enable sequential or coarsen cadence)"
        )
    # (a grid over max_looks never reaches here — generate_grid(limit=…) fails fast above)

    echo_tree(header, children, warnings=warnings or None)


def _plan_lines(plan: ComparisonPlan) -> list[str]:
    tag = f"[{plan.role} · {plan.method_name}" + (
        f" · {plan.test_type}]" if plan.test_type else "]"
    )
    if plan.refused is not None:
        return [f"{plan.metric} {tag} — SKIPPED: {plan.refused}"]

    assert plan.baseline is not None and plan.result is not None
    b = plan.baseline
    if plan.kind == FRACTION:
        base = f"baseline prop={b.baseline:.4g} · n={_fmt_n(b.n)}/{_fmt_n(b.n_other)} trials"
    else:
        base = (
            f"baseline mean={b.baseline:.4g} std={b.std:.4g} · n={_fmt_n(b.n)}/{_fmt_n(b.n_other)}"
        )
    lines = [f"{plan.metric} {tag} — {base} ({b.source})"]

    r = plan.result
    parts: list[str] = []
    if plan.target_mde is not None:
        powered = is_powered(plan)
        flag = " ✓ powered" if powered else " ✗ underpowered" if powered is False else ""
        parts.append(
            f"target MDE {_fmt_effect(plan.target_mde, plan.test_type or 'relative')} → "
            f"required {_fmt_n(r.required_n)}/arm{flag}"
        )
        if r.achieved_power is not None:
            parts.append(f"power@MDE {r.achieved_power:.2f}")
    else:
        parts.append("no target MDE (pass --mde or set comparison.min_effect for required-N)")
    parts.append(f"achievable MDE {_fmt_effect(r.achievable_mde, plan.test_type or 'relative')}")
    lines.append("  " + " · ".join(parts))
    if plan.runtime is not None:
        lines.extend(_runtime_lines(plan.runtime))
    for note in plan.notes:
        lines.append(f"  ⚠ {note}")
    return lines


def _fmt_days(days: float | None) -> str:
    if days is None:
        return "—"
    if not math.isfinite(days):
        return "∞"
    if days < 1.0:
        return f"{days * 24:.1f}h"
    return f"{days:,.1f}d"


def _fmt_rate(rate: float) -> str:
    # a low-traffic derived rate can be < 1/day; ",.0f" would round it to a
    # self-contradictory "0 units/day" beside a finite runtime, so keep 2 dp under 1.
    return f"{rate:,.2f}" if rate < 1.0 else f"{rate:,.0f}"


def _runtime_lines(rt) -> list[str]:
    """The WP-A timing lines: days-to-N and (for a sequential design) the ASN."""
    if rt.rate_control_per_day is None:
        return [f"  runtime: n/a — {rt.rate_source}"]
    lines = [
        f"  runtime ≈ {_fmt_days(rt.days_to_required_n)} to required-N "
        f"@ {_fmt_rate(rt.rate_control_per_day)} units/day/arm ({rt.rate_source}) · "
        f"horizon {_fmt_days(rt.horizon_days)}"
    ]
    if rt.asn is not None:
        a = rt.asn
        # ASN is the expected STOPPING N (horizon-capped), not a smaller sample
        # requirement than the fixed required-N. When it lands below required-N (the
        # underpowered/horizon-capped regime) say so, so the juxtaposition can't read as
        # "sequential concludes in fewer samples than a fixed test" (cli-and-dx §1).
        tail = (
            " · horizon-capped expected-stop, not a lower requirement"
            if rt.asn_below_required
            else ""
        )
        lines.append(
            f"  sequential ASN ≈ {_fmt_n(a.asn_n_h1)}/arm (≈ {_fmt_days(a.asn_days_h1)}) "
            f"at target effect · P(win by horizon) {a.prob_win_by_horizon * 100:.0f}% · "
            f"null ASN ≈ {_fmt_n(a.asn_n_h0)}/arm{tail}"
        )
    elif rt.asn_note is not None:
        lines.append(f"  sequential ASN: n/a — {rt.asn_note}")
    return lines


def _fmt_instant(value: date | datetime) -> str:
    """A window edge for the terminal: date alone at midnight, else date + time.

    Fed the LOCAL config value, not ``grid.horizon_ts`` — the grid edge is
    naive UTC, so a Moscow experiment configured ``horizon_ts: 2024-07-15``
    would otherwise print an unlabelled ``2024-07-14 21:00:00``, which is
    neither what was written nor a local time. Echoing the config keeps the
    line readable and, at UTC, byte-identical to the pre-m10 output.
    """
    local = as_local_datetime(value)
    return local.date().isoformat() if local.time() == time.min else local.isoformat(sep=" ")


def _fmt_cadence(experiment) -> str:
    segments = experiment.cadence_segments()
    if len(segments) == 1 and segments[0][1] is None:
        return _fmt_seconds(segments[0][0])
    return " → ".join(
        _fmt_seconds(every) + (f"/until {_fmt_seconds(until)}" if until else "")
        for every, until in segments
    )


def _fmt_seconds(seconds: int) -> str:
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"
