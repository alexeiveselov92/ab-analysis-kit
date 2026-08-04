"""The analyze stage: loaded role arrays → stats-core containers → TestResults.

Pure orchestration over ``abkit.stats`` — no DB, no SQL. Dispatch is
declarative (plan R8): ``method.input_kind`` selects the container family,
``is_paired`` gates unsupported designs, and the presence of a ``seed`` param
spec marks resampling methods that need the deterministic per-row seed
(``seed`` is identity-excluded, so injecting it never changes
``method_config_id``; re-runs are byte-stable).

Small-sample demotion (cumulative-intervals §6.1.4): below
``min_units_per_arm`` the pair is still recorded — counts and SRM stay
visible — but inference is withheld (``result=None`` → the enrich stage
writes NULLed test columns with ``insufficient_data=1``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.metric_config import MetricConfig
from abkit.config.project_config import ProjectConfig
from abkit.loaders.metric_loader import MetricLoadResult
from abkit.stats import (
    AbkitStatsWarning,
    Fraction,
    RatioSample,
    Sample,
    TestResult,
    TwoTierAlphas,
    create_method,
    derive_seed,
    get_method_class,
    two_tier_alphas,
)
from abkit.stats.sequential import to_always_valid
from abkit.utils.warn_scope import capture_warnings


class AnalyzeError(Exception):
    """Raised when a comparison cannot be computed from the loaded data."""


@dataclass
class PairOutcome:
    """One variant pair at one cutoff: a TestResult or a demoted placeholder."""

    name_1: str
    name_2: str
    size_1: int
    size_2: int
    result: TestResult | None  # None => insufficient_data demotion
    warnings: list[str]


def effective_alphas(experiment: ExperimentConfig, project: ProjectConfig) -> TwoTierAlphas:
    """The inspectable two-tier scheme (declarative-config §6).

    Under ``guardrail_correction: inherit`` (the default) guardrails count as
    tests: every non-main comparison shares the secondary budget.
    ``correction: none`` collapses both tiers to the raw alpha.

    Under ``guardrail_correction: none`` (m13 D8) a guardrail leaves that budget
    **entirely**, and that is two changes, not one: it is tested at the raw alpha,
    AND it stops counting towards the secondary divisor — which loosens alpha for
    the screening metrics that remain. Resolving only the first half would help
    guardrails and tax nothing else, i.e. half the intended scheme, with the
    persisted secondary alphas unchanged and no gate the wiser.

    The flip is inert unless the correction is Bonferroni: every other scheme
    already hands out the raw alpha, which is what a guardrail would get anyway.

    ``experiment.contrasts`` (m13 STAT-1b) decides the pair count both tiers
    divide by, and it must be the SAME declaration
    ``ExperimentConfig.contrast_pairs()`` hands the analyze stage: alphas paid
    for ``C(g,2)`` pairs while only ``g−1`` are computed is a silently
    conservative experiment, and the reverse silently breaks the FWER claim.
    """
    alpha = experiment.alpha if experiment.alpha is not None else project.statistics.alpha
    correction = (
        experiment.correction
        if experiment.correction is not None
        else project.statistics.correction
    )
    guardrail_correction = (
        experiment.guardrail_correction
        if experiment.guardrail_correction is not None
        else project.statistics.guardrail_correction
    )
    groups = len(experiment.assignment.variants)
    guardrails_untiered = guardrail_correction == "none"
    non_main = sum(
        1
        for c in experiment.comparisons
        if not c.is_main_metric and not (guardrails_untiered and c.is_guardrail)
    )
    if correction == "bonferroni":
        return two_tier_alphas(
            alpha,
            groups_count=groups,
            metrics_count=non_main,
            guardrail_alpha=alpha if guardrails_untiered else None,
            contrasts=experiment.contrasts,
        )
    # none / benjamini_hochberg (BH is read-time): raw alpha at compute time
    return TwoTierAlphas(
        alpha=alpha,
        groups_count=groups,
        metrics_count=non_main,
        main=alpha,
        secondary=alpha,
        contrasts=experiment.contrasts,
    )


def comparison_alpha(comparison: ComparisonConfig, alphas: TwoTierAlphas) -> float:
    """The per-comparison effective alpha for one comparison's role.

    The guardrail tier is tested BEFORE the ``secondary is None`` fallback on
    purpose: under m13 D8 an experiment whose only non-main comparisons ARE
    guardrails has ``metrics_count == 0``, so ``secondary`` is ``None`` — and the
    old fallback would then hand the guardrail the MAIN alpha, the tightest level
    in the scheme, which is the exact opposite of what D8 asks for.
    """
    if comparison.is_main_metric:
        return alphas.main
    if comparison.is_guardrail and alphas.guardrail is not None:
        return alphas.guardrail
    if alphas.secondary is None:
        return alphas.main
    return alphas.secondary


def build_container(
    kind: str,
    variant: str,
    loaded: MetricLoadResult,
) -> Any:
    """One variant's loaded role arrays → the stats-core container for ``kind``.

    Shared with the explore recompute engine (m3-implementation-plan.md WP4) —
    the Tier-S cache path must build byte-identical containers to the pipeline.
    """
    roles = loaded.roles_by_variant.get(variant, {})
    if kind == "sample":
        return Sample(
            roles["value"],
            cov_array=roles.get("covariate"),
            categories_array=loaded.strata_by_variant.get(variant),
            name=variant,
        )
    if kind == "fraction":
        return Fraction(
            count=float(roles["count"].sum()),
            nobs=float(roles["nobs"].sum()),
            name=variant,
        )
    if kind == "ratio":
        return RatioSample(roles["numerator"], roles["denominator"], name=variant)
    raise AnalyzeError(f"unknown method input_kind: {kind!r}")


def analyze_cutoff(
    experiment: ExperimentConfig,
    comparison: ComparisonConfig,
    metric: MetricConfig,
    loaded: MetricLoadResult,
    end_ts: datetime,
    alphas: TwoTierAlphas,
    project: ProjectConfig,
    sequential_tau2: dict[tuple[str, str], float] | None = None,
) -> list[PairOutcome]:
    """One outcome per DECLARED variant pair for one (comparison, cutoff).

    Pairs come from ``ExperimentConfig.contrast_pairs()`` and follow the
    declared variant order (first = control = ``name_1``, baseline §5
    ``combinations`` semantics); under ``contrasts: vs_control`` (m13 STAT-1b)
    the treatment-vs-treatment pairs are simply never computed — the same
    declaration that removed them from the alpha divisor. Stats-core warnings
    are captured per pair and routed into the row (plan R7), never to stderr.

    ``sequential_tau2`` (M5 WP3): when the experiment's sequential mode is on,
    ``{(name_1, name_2): tau2}`` (the frozen per-pair mixture variance, anchored to the
    first usable look) widens each supported pair's fixed CI into the always-valid one
    (``ci_kind='always_valid'``). ``None`` / a missing pair / a sequential-ineligible
    method ⇒ the fixed CI is kept unchanged.
    """
    method_cls = get_method_class(comparison.method.name)
    if method_cls.is_paired:
        raise AnalyzeError(
            f"method '{comparison.method.name}' is a paired design — the v1 "
            "pipeline serves independent-arm experiments (use the notebook API "
            "for paired data)"
        )
    if method_cls.input_kind != metric.type:
        raise AnalyzeError(
            f"method '{comparison.method.name}' expects a '{method_cls.input_kind}' "
            f"metric, got '{metric.type}' — declared in metrics/{metric.name}.yml"
        )
    needs_seed = any(spec.name == "seed" for spec in method_cls.param_specs)
    alpha = comparison_alpha(comparison, alphas)
    min_units = project.limits.min_units_per_arm

    reusable = None
    if not needs_seed:
        reusable = create_method(
            comparison.method.name, alpha=alpha, params=dict(comparison.method.params)
        )

    outcomes: list[PairOutcome] = []
    for name_1, name_2 in experiment.contrast_pairs():
        size_1 = loaded.size(name_1)
        size_2 = loaded.size(name_2)
        if size_1 < min_units or size_2 < min_units:
            outcomes.append(
                PairOutcome(
                    name_1=name_1,
                    name_2=name_2,
                    size_1=size_1,
                    size_2=size_2,
                    result=None,
                    warnings=[
                        f"insufficient data: {size_1}/{size_2} units vs "
                        f"min_units_per_arm={min_units} — inference withheld"
                    ],
                )
            )
            continue

        group_1 = build_container(method_cls.input_kind, name_1, loaded)
        group_2 = build_container(method_cls.input_kind, name_2, loaded)

        if needs_seed:
            # Deterministic per-row seed: byte-stable re-runs; identity-excluded.
            params = dict(comparison.method.params)
            params["seed"] = derive_seed(
                experiment.name,
                metric.name,
                name_1,
                name_2,
                end_ts,
                params.get("n_samples", 1000),
            )
            method = create_method(comparison.method.name, alpha=alpha, params=params)
        else:
            method = reusable

        # THREAD-scoped capture (m10 WP4): `abk run` fans experiments out over
        # a ThreadPoolExecutor, and the stdlib's catch_warnings saves/restores
        # PROCESS-global state — overlapping scopes cross-attribute warnings
        # between experiments and can leave a dead recorder installed.
        with capture_warnings(AbkitStatsWarning) as caught:
            result = method.compare_pair(group_1, group_2)
        pair_warnings = [
            str(w.message) for w in caught if issubclass(w.category, AbkitStatsWarning)
        ]

        # M5 WP3: widen into the always-valid CI when the sequential mode is on and the
        # method is eligible (a symmetric-normal fixed CI). Never re-derives a variance.
        if sequential_tau2 is not None and method_cls.supports_sequential:
            tau2 = sequential_tau2.get((name_1, name_2))
            if tau2 is not None:
                result = to_always_valid(result, tau2, alpha)

        outcomes.append(
            PairOutcome(
                name_1=name_1,
                name_2=name_2,
                size_1=size_1,
                size_2=size_2,
                result=result,
                warnings=pair_warnings,
            )
        )
    return outcomes
