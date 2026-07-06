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

import warnings as _warnings
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
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

    Guardrails count as tests: every non-main comparison shares the secondary
    budget. ``correction: none`` collapses both tiers to the raw alpha.
    """
    alpha = experiment.alpha if experiment.alpha is not None else project.statistics.alpha
    correction = (
        experiment.correction
        if experiment.correction is not None
        else project.statistics.correction
    )
    groups = len(experiment.assignment.variants)
    non_main = sum(1 for c in experiment.comparisons if not c.is_main_metric)
    if correction == "bonferroni":
        return two_tier_alphas(alpha, groups_count=groups, metrics_count=non_main)
    # none / benjamini_hochberg (BH is read-time): raw alpha at compute time
    return TwoTierAlphas(
        alpha=alpha, groups_count=groups, metrics_count=non_main, main=alpha, secondary=alpha
    )


def comparison_alpha(comparison: ComparisonConfig, alphas: TwoTierAlphas) -> float:
    if comparison.is_main_metric or alphas.secondary is None:
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
    """All pairwise variant outcomes for one (comparison, cutoff).

    Pairs follow the declared variant order (first = control = ``name_1``,
    baseline §5 ``combinations`` semantics). Stats-core warnings are captured
    per pair and routed into the row (plan R7), never to stderr.

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
    variant_order = experiment.assignment.variants
    for name_1, name_2 in combinations(variant_order, 2):
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

        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always", AbkitStatsWarning)
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
