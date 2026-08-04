"""The readout stage: persisted ``_ab_results`` rows → the experiment verdict.

WIN / LOSE / FLAT / INCONCLUSIVE per (main-metric comparison ×
control-vs-treatment pair), evaluated at the series' latest computed cutoff
(data-contract-and-reporting.md §1; the numeric rules are
m3-implementation-plan.md D5). Pure decision logic over the row dicts
``load_results()`` returns — no DB, no rendering. Verdicts are read-time
only: recomputed at render, never persisted (D5(f)).

Decision order per pair (each gate may short-circuit to INCONCLUSIVE):

1. **SRM hard gate** (§1) — a flagged cohort makes every effect untrustworthy.
2. **Pre-horizon withholding** (D5(d); quorum "peeking is the product") —
   with fixed-horizon CIs (every M3 row), WIN/LOSE **and FLAT** are withheld
   until ``is_horizon``; FLAT is equally a stop decision.
3. **Latest-cutoff usability** — ``insufficient_data`` demotion or degenerate
   (NULL-bound) inference at the latest cutoff.
4. **Elapsed-time stabilization** (§4: never look count) — the trailing
   ``readout.stabilization_days`` window, widened to the last
   ``MIN_STABLE_CUTOFFS`` informative cutoffs when the cadence is coarser
   than the window.
5. **Significance + sign consistency** → WIN/LOSE against the comparison's
   ``desired_direction``; all-quiet + adequately powered (``min_effect`` vs
   the pair MDE, D5(b)) → FLAT.
6. **Guardrail regression** (D5(c), owner-ratified policy) —
   ``guardrail_policy: block`` caps WIN at INCONCLUSIVE; ``warn`` keeps WIN
   with a mandatory loud caveat. LOSE is never upgraded or blocked.

The read-time schemes — Benjamini-Hochberg (FDR) and, since m13 STAT-1, Holm
(FWER) — are applied HERE, per cutoff across the experiment's comparisons;
compute-time rows deliberately carry the raw alpha
(``analyze.effective_alphas``), because a step procedure has no per-comparison
level at all. A readout ignoring them would verdict at the wrong alpha.

**Fork B (m13 D7): under a read-time scheme the verdict and the stored interval
may legitimately disagree.** The interval is one comparison at its own alpha;
the decision is the family's. The divergence is one-directional (the family
rule is never looser than the member's raw alpha), so what an operator can see
is an interval excluding zero under a verdict that refuses to call it — which
is why every non-rejecting pair whose stored CI excludes zero carries an
explicit caveat rather than leaving the contradiction to be discovered.

MDE fallback (D5(b)): rows persisted with ``calculate_mde: false`` carry NULL
``mde_1/2``. For t-test and z-test rows the pair MDE is recomputed read-time
from the on-row per-arm stats via ``stats/power.py`` — byte-equivalent to the
method's own solve (the z-test ``nobs`` is inverted from the persisted SE,
never taken from ``size_i``, which counts unit rows). Methods without an MDE
capability (ratio-delta, the bootstrap family) leave FLAT honestly
unreachable, with the rationale saying so.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from abkit.config.experiment_config import ComparisonConfig, ExperimentConfig
from abkit.config.project_config import ProjectConfig
from abkit.stats.correction import (
    READ_TIME_CORRECTIONS,
    SignificanceInput,
    composed_significance,
)
from abkit.stats.exceptions import UnknownMethodError
from abkit.stats.power import get_fraction_mde, get_ttest_mde
from abkit.stats.registry import get_method_class

VerdictKind = Literal["WIN", "LOSE", "FLAT", "INCONCLUSIVE"]

#: The representativeness horizon (§4): verdicts covering less than one weekly
#: cycle carry the "covers X% of a weekly cycle" caveat.
WEEKLY_CYCLE_DAYS = 7.0
#: The stabilization-window floor (D5(a)): fewer informative cutoffs than this
#: cannot demonstrate persistence.
MIN_STABLE_CUTOFFS = 3

_DAY_SECONDS = 86400.0


# ── row coercion (DB backends return numpy scalars / 0-1 ints for booleans) ──


def _num(value: Any) -> float | None:
    """None-safe finite float (NaN/inf → None, matching the nullable contract)."""
    if value is None:
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _flag(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return bool(int(value))


# ── results ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GuardrailStatus:
    """One guardrail comparison's regression check for one variant pair."""

    metric: str
    name_1: str
    name_2: str
    regressed: bool
    effect: float | None
    desired_direction: str


@dataclass(frozen=True)
class PairVerdict:
    """The verdict for one (main-metric comparison × control-vs-treatment pair)."""

    metric: str
    name_1: str
    name_2: str
    verdict: VerdictKind
    rationale: tuple[str, ...]
    caveats: tuple[str, ...]
    end_ts: datetime | None
    elapsed_days: float | None
    is_horizon: bool
    effect: float | None
    pvalue: float | None
    left_bound: float | None
    right_bound: float | None
    alpha: float | None
    significant: bool
    mde: float | None
    min_effect: float | None
    #: Weekly-cycle coverage fraction (elapsed / 7d) when a decisive verdict is
    #: called before one full weekly cycle, else ``None`` — the report promotes
    #: it to a representativeness chip (§6.5). ``None`` on INCONCLUSIVE or ≥7d.
    weekly_cycle_pct: float | None
    guardrails: tuple[GuardrailStatus, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["rationale"] = list(self.rationale)
        payload["caveats"] = list(self.caveats)
        payload["guardrails"] = [asdict(g) for g in self.guardrails]
        return payload


@dataclass(frozen=True)
class ExperimentReadout:
    """The experiment-level readout: per-pair verdicts + the SRM summary."""

    experiment: str
    srm_flag: bool
    srm_pvalue: float | None
    verdicts: tuple[PairVerdict, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment": self.experiment,
            "srm_flag": self.srm_flag,
            "srm_pvalue": self.srm_pvalue,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "warnings": list(self.warnings),
        }


# ── significance (read-time-scheme aware) ────────────────────────────────────


@dataclass(frozen=True)
class _Sig:
    significant: bool
    sign: int  # +1 effect up, -1 effect down, 0 undecidable


_RowKey = tuple[str, str, str, Any]  # (metric, name_1, name_2, end_ts)


def _row_key(row: dict) -> _RowKey:
    return (str(row["metric"]), str(row["name_1"]), str(row["name_2"]), row["end_ts"])


def _informative(row: dict) -> bool:
    """A row the stabilization scan may judge: inference present, not demoted.

    Demoted (``insufficient_data``) and degenerate (NULL-bound) rows are gaps —
    skipped, never treated as zeros.
    """
    if _flag(row.get("insufficient_data")):
        return False
    return _num(row.get("left_bound")) is not None and _num(row.get("right_bound")) is not None


def _sig_input(row: dict) -> SignificanceInput:
    """Adapt a persisted ``_ab_results`` row to the shared composed-rule primitive."""
    return SignificanceInput(
        left_bound=_num(row.get("left_bound")),
        right_bound=_num(row.get("right_bound")),
        pvalue=_num(row.get("pvalue")),
        effect=_num(row.get("effect")),
        alpha=_num(row.get("alpha")),
    )


def _build_sig_map(rows: Sequence[dict], correction: str) -> dict[_RowKey, _Sig]:
    """Per-row significance under the experiment's correction scheme.

    Delegates the composed multiple-testing rule to the shared
    ``stats.correction.composed_significance`` (WP7) so the readout and the A/A
    composed FWER/FDR sweep apply ONE rule. Bonferroni/none: the CI already
    reflects the stored effective alpha ⇒ significance is "the CI excludes zero"
    (per-row, no cross-row interaction). A read-time scheme (Benjamini-Hochberg,
    or Holm since m13 STAT-1): the family is one cadence cutoff's informative rows
    (metrics × pairs — the compute-time ``n_comparisons`` convention), adjusted
    then compared against the stored raw alpha.

    The branch asks ``READ_TIME_CORRECTIONS``, never a scheme NAME: a per-cutoff
    family is what every read-time scheme needs, so a name test here would silently
    hand the next one the per-row CI rule — a scheme that appears to work while
    controlling nothing.
    """
    sig: dict[_RowKey, _Sig] = {}
    informative = [row for row in rows if _informative(row)]

    if correction not in READ_TIME_CORRECTIONS:
        outcomes = composed_significance([_sig_input(row) for row in informative], correction)
        for row, outcome in zip(informative, outcomes, strict=True):
            sig[_row_key(row)] = _Sig(outcome.significant, outcome.sign)
        return sig

    by_cutoff: dict[Any, list[dict]] = {}
    for row in informative:
        by_cutoff.setdefault(row["end_ts"], []).append(row)
    for cutoff_rows in by_cutoff.values():
        outcomes = composed_significance([_sig_input(row) for row in cutoff_rows], correction)
        for row, outcome in zip(cutoff_rows, outcomes, strict=True):
            sig[_row_key(row)] = _Sig(outcome.significant, outcome.sign)
    return sig


#: How each read-time scheme is NAMED to the operator. A verdict that diverges
#: from the interval printed beside it has to say which rule made the call.
_SCHEME_LABELS = {"benjamini_hochberg": "Benjamini-Hochberg", "holm": "Holm"}


def _ci_excludes_zero(row: dict) -> bool:
    """The per-comparison, PRE-family reading of one row's stored interval.

    Identical to the persisted ``reject`` flag's meaning (data-contract §1) and to
    what every surface draws beside the verdict. Under a read-time scheme it is no
    longer the decision — which is exactly why the readout must be able to detect
    the two disagreeing.
    """
    left, right = _num(row.get("left_bound")), _num(row.get("right_bound"))
    return (left is not None and left > 0) or (right is not None and right < 0)


def _sig_phrase(correction: str) -> str:
    """How to describe what made a cutoff significant, in the scheme's own terms.

    Saying "CI excludes zero" under a read-time family rule is not a wording
    preference: it names a per-comparison fact as the reason for a family-level
    decision, and the two can disagree in both directions once the family moves.
    """
    label = _SCHEME_LABELS.get(correction)
    return f"the {label}-adjusted p is below alpha" if label else "CI excludes zero"


def _quiet_phrase(correction: str) -> str:
    """The negation of :func:`_sig_phrase`, in a sentence-leading form."""
    label = _SCHEME_LABELS.get(correction)
    return f"not significant under the {label} family rule" if label else "CI includes zero"


# ── MDE (D5(b)) ──────────────────────────────────────────────────────────────


def _combine_mde(mde_1: float | None, mde_2: float | None) -> float | None:
    """The pair MDE: the larger per-arm magnitude (compared against min_effect)."""
    magnitudes = [abs(m) for m in (mde_1, mde_2) if m is not None]
    return max(magnitudes) if magnitudes else None


def pair_mde(row: dict) -> tuple[float | None, str | None]:
    """``(pair_mde, unavailable_reason)`` for one row — stored or read-time.

    The stored pair is trusted only when BOTH ``mde_1/2`` are present: enrich
    NULLs a non-finite solve (an arm with zero variance or ``n<=1`` has
    infinite MDE), so a half-present pair means one arm is undetectable —
    taking the finite arm alone would fake adequate power (review finding).
    Otherwise the fallback recomputes the method's own solve from on-row
    stats — exact for t-test (mean/std/units persist directly) and z-test
    (``nobs`` inverted from the persisted SE ``sqrt(p(1−p)/nobs)``; ``size_i``
    is the unit-row count and must never be used as ``nobs``). Where the
    fallback cannot recompute but ``calculate_mde: true`` is on the row's
    params, a NULL column provably WAS a non-finite solve — the pair MDE is
    ``inf`` (reads as underpowered), never None.
    """
    stored_1, stored_2 = _num(row.get("mde_1")), _num(row.get("mde_2"))
    if stored_1 is not None and stored_2 is not None:
        return _combine_mde(stored_1, stored_2), None

    method_name = str(row.get("method_name"))
    try:
        method_cls = get_method_class(method_name)
    except UnknownMethodError:
        return None, f"unknown method {method_name!r}"
    params: dict[str, Any] = json.loads(row["method_params"]) if row.get("method_params") else {}
    defaults = {spec.name: spec.default for spec in method_cls.param_specs}
    test_type = str(params.get("test_type", defaults.get("test_type", "relative")))
    power = float(params.get("power", defaults.get("power", 0.8)))
    calculate_mde = bool(params.get("calculate_mde", defaults.get("calculate_mde", False)))
    alpha = _num(row.get("alpha"))
    value_1, value_2 = _num(row.get("value_1")), _num(row.get("value_2"))
    std_1, std_2 = _num(row.get("std_1")), _num(row.get("std_2"))

    def unavailable(reason: str) -> tuple[float | None, str | None]:
        # calculate_mde: true wrote the mde columns; a NULL one provably was a
        # non-finite solve (enrich NULLs inf) — underpowered, not unknown.
        if calculate_mde:
            return math.inf, None
        return None, reason

    if method_cls.name == "t-test":
        size_1 = row.get("size_1")
        size_2 = row.get("size_2")
        if None in (value_1, value_2, std_1, std_2, alpha, size_1, size_2):
            return unavailable("per-arm stats unavailable on the row")
        n1, n2 = int(size_1), int(size_2)
        if n1 <= 0 or n2 <= 0:
            return unavailable("per-arm sizes unavailable on the row")
        mde_1 = get_ttest_mde(
            value_1, std_1, n1, test_type=test_type, alpha=alpha, power=power, ratio=n2 / n1
        )
        mde_2 = get_ttest_mde(
            value_2, std_2, n2, test_type=test_type, alpha=alpha, power=power, ratio=n1 / n2
        )
        return _combine_mde(mde_1, mde_2), None

    if method_cls.name == "z-test":
        if None in (value_1, value_2, std_1, std_2, alpha):
            return unavailable("per-arm stats unavailable on the row")
        if not (0.0 < value_1 < 1.0 and 0.0 < value_2 < 1.0 and std_1 > 0 and std_2 > 0):
            return unavailable("degenerate proportion (p in {0, 1}) — nobs is not invertible")
        # std_i is the SE of the proportion: sqrt(p(1-p)/nobs) -> invert nobs.
        nobs_1 = value_1 * (1.0 - value_1) / (std_1**2)
        nobs_2 = value_2 * (1.0 - value_2) / (std_2**2)
        mde_1 = get_fraction_mde(
            value_1,
            int(round(nobs_1)),
            test_type=test_type,
            alpha=alpha,
            power=power,
            ratio=nobs_2 / nobs_1,
        )
        mde_2 = get_fraction_mde(
            value_2,
            int(round(nobs_2)),
            test_type=test_type,
            alpha=alpha,
            power=power,
            ratio=nobs_1 / nobs_2,
        )
        return _combine_mde(mde_1, mde_2), None

    if any(spec.name == "calculate_mde" for spec in method_cls.param_specs):
        return unavailable(
            f"MDE not computed — set calculate_mde: true on {method_cls.name!r} "
            "(its solve needs moments that are not persisted per row)"
        )
    return None, f"method {method_cls.name!r} has no MDE capability"


# ── evaluate ─────────────────────────────────────────────────────────────────


def _group_series(rows: Sequence[dict]) -> dict[tuple[str, str, str], list[dict]]:
    """Group rows into ``(metric, name_1, name_2)`` series, each end_ts-ascending."""
    series: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        key = (str(row["metric"]), str(row["name_1"]), str(row["name_2"]))
        series.setdefault(key, []).append(row)
    for group in series.values():
        group.sort(key=lambda row: row["end_ts"])
    return series


def _srm_from_series(
    experiment: ExperimentConfig, series: dict[tuple[str, str, str], list[dict]]
) -> tuple[bool, float | None]:
    """The experiment-level SRM ``(flag, pvalue)`` over pre-grouped series.

    When any latest row is flagged, the reported p-value must come from a
    FLAGGED row (a lagging non-flagged series would otherwise pair
    ``srm_flag=True`` with a healthy p — review finding); ``min()`` picks the
    loudest evidence. ALL comparisons' series are scanned, not just main —
    SRM is a whole-experiment fact stamped on every row, and a main-only scan
    goes silent exactly when the main series is empty under its CURRENT
    ``method_config_id`` (e.g. right after an explore Apply edited the main
    method) while flagged rows still exist on other series (§6 must stay
    loud — milestone-review finding).
    """
    control = experiment.assignment.variants[0]
    treatments = experiment.assignment.variants[1:]
    srm_flag = False
    flagged_pvalues: list[float] = []
    healthy_pvalues: list[float] = []
    for comparison in experiment.comparisons:
        for treatment in treatments:
            group = series.get((comparison.metric, control, treatment))
            if not group:
                continue
            latest = group[-1]
            flagged = _flag(latest.get("srm_flag"))
            srm_flag = srm_flag or flagged
            pvalue = _num(latest.get("srm_pvalue"))
            if pvalue is not None:
                (flagged_pvalues if flagged else healthy_pvalues).append(pvalue)
    if flagged_pvalues:
        return srm_flag, min(flagged_pvalues)
    if healthy_pvalues:
        return srm_flag, healthy_pvalues[0]
    return srm_flag, None


def srm_summary(experiment: ExperimentConfig, rows: Sequence[dict]) -> tuple[bool, float | None]:
    """The experiment-level SRM ``(flag, pvalue)`` over raw (ungrouped) rows.

    The report's loud §6 SRM chip calls this over the FULL persisted rows
    (current experiment health, window-independent) so the chip never goes
    silent under a pinned/empty replay — while ``evaluate`` uses the same core
    over the windowed series for the as-of verdict. SRM is a whole-experiment
    gate ("is assignment broken?"), not a per-cutoff series in M2 (per-cutoff
    lands with M5 sequential)."""
    filtered, _warnings = _filter_rows(experiment, rows)
    return _srm_from_series(experiment, _group_series(filtered))


def evaluate(
    experiment: ExperimentConfig,
    rows: Sequence[dict],
    *,
    project: ProjectConfig | None = None,
) -> ExperimentReadout:
    """The pure verdict over ``load_results()`` rows for one experiment.

    ``project`` resolves the effective correction when the experiment leaves
    it unset — the same resolution the pipeline runs with
    (``analyze.effective_alphas`` requires the project; here it is optional
    for notebook convenience). When BOTH are absent the readout falls back to
    stored-alpha CI significance — correct for ``none``/``bonferroni`` (the
    persisted bounds already carry the effective alpha) but WRONG for a
    project-level READ-time default (``benjamini_hochberg``/``holm``), so the
    degradation is surfaced as a loud ``ExperimentReadout.warnings`` entry,
    never silent (review finding; D5(g): a read-time scheme is required, not
    optional).
    """
    correction = experiment.correction
    unresolved_correction = correction is None and project is None
    if correction is None:
        correction = project.statistics.correction if project is not None else "none"

    filtered, warnings = _filter_rows(experiment, rows)
    if unresolved_correction:
        warnings.append(
            "correction is unset on the experiment and no project config was "
            "passed — significance falls back to the stored-alpha CI; a "
            "project-level read-time default ('benjamini_hochberg' / 'holm') "
            "would be mis-scored (pass project= to resolve the effective "
            "correction)"
        )
    sig_map = _build_sig_map(filtered, correction)

    series = _group_series(filtered)

    control = experiment.assignment.variants[0]
    treatments = experiment.assignment.variants[1:]
    main_comparisons = [c for c in experiment.comparisons if c.is_main_metric]
    guardrail_comparisons = [c for c in experiment.comparisons if c.is_guardrail]

    verdicts: list[PairVerdict] = []
    for comparison in main_comparisons:
        for treatment in treatments:
            verdicts.append(
                _pair_verdict(
                    experiment,
                    comparison,
                    control,
                    treatment,
                    series,
                    sig_map,
                    guardrail_comparisons,
                    correction,
                )
            )

    srm_flag, srm_pvalue = _srm_from_series(experiment, series)

    return ExperimentReadout(
        experiment=experiment.name,
        srm_flag=srm_flag,
        srm_pvalue=srm_pvalue,
        verdicts=tuple(verdicts),
        warnings=tuple(warnings),
    )


def _filter_rows(
    experiment: ExperimentConfig, rows: Sequence[dict]
) -> tuple[list[dict], list[str]]:
    """Keep only rows this experiment currently declares.

    Three ways a persisted row stops being declared: an edited identity param
    leaves an orphaned ``method_config_id`` series behind, the config stops
    binding the metric, or the row's arm pair leaves the **contrast set** — a
    renamed arm, or a family narrowed to ``contrasts: vs_control`` (m13
    STAT-1b). All three are ignored with a warning; the driver prints the same
    story at run time (duplicate stabilization lines; ``abk clean``).

    The pair filter lives HERE, not only in the three surfaces that mirror it
    (report / dashboard / notify), because the read-time Benjamini-Hochberg
    family is built from every informative row at a cutoff: a caller that
    reaches ``evaluate`` directly — a notebook, a future surface — would
    otherwise score a family of ``C(g,2)`` for an experiment that declared
    ``g−1``, and contradict ``abk run --report`` on identical rows. The
    surfaces keep their own copies because each owes the operator its own
    loud line about what it dropped.
    """
    configured = {c.metric: c.method.method_config_id for c in experiment.comparisons}
    declared_pairs = set(experiment.contrast_pairs())
    filtered: list[dict] = []
    orphaned: dict[str, set[str]] = {}
    unconfigured: set[str] = set()
    undeclared_pairs = 0
    for row in rows:
        metric = str(row["metric"])
        expected = configured.get(metric)
        if expected is None:
            unconfigured.add(metric)
            continue
        if str(row["method_config_id"]) != expected:
            orphaned.setdefault(metric, set()).add(str(row["method_config_id"]))
            continue
        if (str(row["name_1"]), str(row["name_2"])) not in declared_pairs:
            undeclared_pairs += 1
            continue
        filtered.append(row)

    warnings: list[str] = []
    if undeclared_pairs:
        warnings.append(
            f"ignored {undeclared_pairs} rows for variant pairs outside the declared "
            "contrast set (renamed arms, or `contrasts: vs_control`?)"
        )
    for metric in sorted(orphaned):
        ids = orphaned[metric]
        warnings.append(
            f"metric {metric!r}: ignored {len(ids)} orphaned method_config_id "
            "series (edited method params leave old rows behind — run `abk clean`)"
        )
    for metric in sorted(unconfigured):
        warnings.append(f"ignored rows for metric {metric!r} — not bound by this experiment")
    return filtered, warnings


def _pair_verdict(
    experiment: ExperimentConfig,
    comparison: ComparisonConfig,
    control: str,
    treatment: str,
    series: dict[tuple[str, str, str], list[dict]],
    sig_map: dict[_RowKey, _Sig],
    guardrail_comparisons: list[ComparisonConfig],
    correction: str,
) -> PairVerdict:
    metric = comparison.metric
    group = series.get((metric, control, treatment), [])
    rationale: list[str] = []
    caveats: list[str] = []

    def build(verdict: VerdictKind, latest: dict | None) -> PairVerdict:
        guardrails = _guardrail_statuses(control, treatment, series, guardrail_comparisons)
        verdict, extra_rationale, extra_caveats = _apply_guardrail_policy(
            experiment, verdict, guardrails
        )
        rationale.extend(extra_rationale)
        caveats.extend(extra_caveats)
        elapsed = _num(latest.get("elapsed_days")) if latest else None
        decisive = verdict != "INCONCLUSIVE"
        weekly_cycle_pct: float | None = None
        if decisive and elapsed is not None and elapsed < WEEKLY_CYCLE_DAYS:
            weekly_cycle_pct = elapsed / WEEKLY_CYCLE_DAYS
            caveats.append(
                f"covers {weekly_cycle_pct:.0%} of a weekly cycle — "
                "day-of-week effects may not be represented"
            )
        # WP4 (§6.5): a decisive verdict reached *before* the planned horizon is
        # only legitimate under an always-valid CI (fixed CIs are withheld above);
        # name the reason so a reader isn't surprised by an early WIN/LOSE/FLAT.
        latest_ci_kind = str(latest.get("ci_kind") or "fixed") if latest else "fixed"
        latest_is_horizon = _flag(latest.get("is_horizon")) if latest else False
        if decisive and not latest_is_horizon and latest_ci_kind == "always_valid":
            rationale.append(
                "called before the planned horizon under an always-valid "
                "confidence sequence — peeking-safe by construction (its "
                "cumulative-peeking FPR is measured by `abk validate`)"
            )
        key = _row_key(latest) if latest else None
        latest_sig = sig_map.get(key, _Sig(False, 0)) if key else _Sig(False, 0)
        # Fork B (m13 D7), made visible: under a step procedure the family decision
        # and the per-comparison interval printed beside it may legitimately point
        # opposite ways. It happens in ONE direction — the family rule is never
        # looser than the member's own raw alpha — so the operator sees an interval
        # excluding zero under a verdict that refuses to call it. Unexplained, the
        # first occurrence reads as a bug in whichever number they trust less; the
        # three renderers all show this caveat because they all show `caveats`.
        if (
            correction in READ_TIME_CORRECTIONS
            and latest is not None
            and _informative(latest)  # a demoted row's reason is the demotion, said above
            and not latest_sig.significant
            and _ci_excludes_zero(latest)
        ):
            stored_alpha = _num(latest.get("alpha"))
            level = f" (α={stored_alpha:.4g})" if stored_alpha is not None else ""
            caveats.append(
                f"the stored per-comparison interval{level} excludes zero, but the "
                f"read-time {_SCHEME_LABELS[correction]} rule over this cutoff's "
                "family does not reject — the interval is per-comparison, the "
                "decision is family-wide, and under a step procedure they may "
                "legitimately disagree"
            )
        mde_value, _ = pair_mde(latest) if latest else (None, None)
        return PairVerdict(
            metric=metric,
            name_1=control,
            name_2=treatment,
            verdict=verdict,
            rationale=tuple(rationale),
            caveats=tuple(caveats),
            end_ts=latest["end_ts"] if latest else None,
            elapsed_days=elapsed,
            is_horizon=_flag(latest.get("is_horizon")) if latest else False,
            effect=_num(latest.get("effect")) if latest else None,
            pvalue=_num(latest.get("pvalue")) if latest else None,
            left_bound=_num(latest.get("left_bound")) if latest else None,
            right_bound=_num(latest.get("right_bound")) if latest else None,
            alpha=_num(latest.get("alpha")) if latest else None,
            significant=latest_sig.significant,
            mde=mde_value,
            min_effect=comparison.min_effect,
            weekly_cycle_pct=weekly_cycle_pct,
            guardrails=guardrails,
        )

    if not group:
        rationale.append(
            f"no computed results for this pair — run `abk run --select {experiment.name}`"
        )
        return build("INCONCLUSIVE", None)

    latest = group[-1]

    # 1. SRM hard gate (§1: effects untrustworthy under a broken cohort).
    if _flag(latest.get("srm_flag")) or _flag(latest.get("decision_blocked")):
        srm_p = _num(latest.get("srm_pvalue"))
        # Name the gate that actually ran: χ² at daily+, the anytime-valid
        # sequential multinomial e-process below 1d (WP5). ``kind`` is not
        # persisted, so it is derived from the current cadence (the gate that
        # wrote these rows) — statistics-changes.md §4.2.
        gate = "anytime-valid" if experiment.is_sub_day() else "chi-square"
        rationale.append(
            "SRM failed"
            + (f" ({gate} p={srm_p:.3g})" if srm_p is not None else "")
            + " — observed group sizes are inconsistent with expected_split; "
            "effects untrustworthy (hard gate)"
        )
        return build("INCONCLUSIVE", latest)

    # 2. Pre-horizon withholding (D5(d)): fixed CIs are not peeking-valid.
    ci_kind = str(latest.get("ci_kind") or "fixed")
    if not _flag(latest.get("is_horizon")) and ci_kind == "fixed":
        elapsed = _num(latest.get("elapsed_days")) or 0.0
        horizon_days = experiment.horizon_seconds() / _DAY_SECONDS
        rationale.append(
            f"pre-horizon: latest cutoff covers {elapsed:.1f} of {horizon_days:.1f} "
            "planned days and fixed-horizon CIs are not peeking-valid — "
            "WIN/LOSE/FLAT withheld until the horizon (enable `sequential: "
            "{enabled: true}` on a sequential-eligible method for peeking-valid "
            "early readouts)"
        )
        if experiment.sequential.enabled:
            caveats.append(
                "sequential.enabled is set, but these rows carry fixed CIs — the "
                "method is not sequential-eligible (e.g. bootstrap) or this pair had "
                "no usable look at the τ² anchor; the pre-horizon refusal still applies"
            )
        return build("INCONCLUSIVE", latest)

    # 3. Latest-cutoff usability.
    if _flag(latest.get("insufficient_data")):
        rationale.append(
            f"insufficient data at the latest cutoff ({latest.get('size_1')}/"
            f"{latest.get('size_2')} units) — inference withheld"
        )
        return build("INCONCLUSIVE", latest)
    if not _informative(latest):
        rationale.append(
            "no confidence interval at the latest cutoff (degenerate variance) — "
            "cannot judge significance"
        )
        return build("INCONCLUSIVE", latest)

    # 4. The elapsed-time stabilization window (D5(a)).
    stabilization_days = experiment.readout.stabilization_days
    informative = [row for row in group if _informative(row)]
    latest_elapsed = _num(latest.get("elapsed_days")) or 0.0
    window = [
        row
        for row in informative
        if (_num(row.get("elapsed_days")) or 0.0) >= latest_elapsed - stabilization_days
    ]
    if len(window) < MIN_STABLE_CUTOFFS:
        # Coarse cadence: widen to the last MIN_STABLE_CUTOFFS informative cutoffs.
        window = informative[-MIN_STABLE_CUTOFFS:]
    if len(window) < MIN_STABLE_CUTOFFS:
        rationale.append(
            f"only {len(window)} informative cutoff(s) available — at least "
            f"{MIN_STABLE_CUTOFFS} are needed to judge stabilization over the "
            f"trailing {stabilization_days:g} days"
        )
        return build("INCONCLUSIVE", latest)

    # 5. Significance + sign consistency over the window.
    sigs = [sig_map.get(_row_key(row), _Sig(False, 0)) for row in window]
    desired_sign = 1 if comparison.desired_direction == "increase" else -1

    if all(s.significant for s in sigs):
        signs = {s.sign for s in sigs}
        if len(signs) == 1:
            sign = signs.pop()
            direction = "desired" if sign == desired_sign else "adverse"
            rationale.append(
                f"{_sig_phrase(correction)} in the {direction} direction "
                f"({'up' if sign > 0 else 'down'}) at every informative cutoff in the "
                f"trailing {stabilization_days:g}-day window ({len(window)} cutoffs)"
            )
            return build("WIN" if sign == desired_sign else "LOSE", latest)
        rationale.append(
            "not stabilized: the effect sign flipped within the trailing "
            f"{stabilization_days:g}-day window"
        )
        return build("INCONCLUSIVE", latest)

    if any(s.significant for s in sigs):
        crossed = (
            "the CI crossed zero"
            if correction not in READ_TIME_CORRECTIONS
            else f"the {_SCHEME_LABELS[correction]} family rule stopped rejecting"
        )
        rationale.append(
            f"not stabilized: {crossed} within the trailing "
            f"{stabilization_days:g}-day window (significant at some cutoffs, "
            "not at others)"
        )
        return build("INCONCLUSIVE", latest)

    # All quiet: FLAT needs the power story (D5(b)).
    min_effect = comparison.min_effect
    if min_effect is None:
        rationale.append(
            f"{_quiet_phrase(correction)} across the window but no min_effect is "
            "configured — "
            "cannot distinguish flat from underpowered (set comparisons[].min_effect)"
        )
        return build("INCONCLUSIVE", latest)
    # Under the always-valid mode the reported interval IS the (wider) confidence
    # sequence, so FLAT's "adequately powered" claim must be judged against the interval
    # we actually show — not the fixed-horizon MDE, which `pair_mde` leaves untouched on
    # an always-valid row and which understates the anytime width (~1.55× at horizon).
    # Judging FLAT on the fixed MDE would leak fixed-horizon power into a wider-CI
    # inference (M5 exit-gate round-1 finding). The half-width is directly on the row.
    latest_ci_kind = str(latest.get("ci_kind") or "fixed") if latest else "fixed"
    if latest_ci_kind == "always_valid":
        lo, hi = _num(latest.get("left_bound")), _num(latest.get("right_bound"))
        av_half = (hi - lo) / 2.0 if lo is not None and hi is not None else None
        if av_half is None:
            rationale.append(
                f"{_quiet_phrase(correction)} across the window but the always-valid "
                "interval is unavailable — FLAT is not callable"
            )
            return build("INCONCLUSIVE", latest)
        if av_half <= min_effect:
            rationale.append(
                f"{_quiet_phrase(correction)} across the trailing "
                f"{stabilization_days:g}-day window and the always-valid interval "
                f"rules out a meaningful effect "
                f"(half-width {av_half:.4g} <= min_effect {min_effect:g})"
            )
            return build("FLAT", latest)
        rationale.append(
            f"underpowered under the always-valid CI: half-width {av_half:.4g} > "
            f"min_effect {min_effect:g} — keep running (INCONCLUSIVE, not FLAT)"
        )
        return build("INCONCLUSIVE", latest)

    mde_value, mde_reason = pair_mde(latest)
    if mde_value is None:
        rationale.append(
            f"{_quiet_phrase(correction)} across the window but the MDE is "
            "unavailable: "
            f"{mde_reason} — FLAT is not callable"
        )
        return build("INCONCLUSIVE", latest)
    if mde_value <= min_effect:
        rationale.append(
            f"{_quiet_phrase(correction)} across the trailing "
            f"{stabilization_days:g}-day window and the test is adequately powered "
            f"(MDE {mde_value:.4g} <= "
            f"min_effect {min_effect:g})"
        )
        return build("FLAT", latest)
    rationale.append(
        f"underpowered: MDE {mde_value:.4g} > min_effect {min_effect:g} — "
        "keep running (INCONCLUSIVE, not FLAT)"
    )
    return build("INCONCLUSIVE", latest)


def _guardrail_statuses(
    control: str,
    treatment: str,
    series: dict[tuple[str, str, str], list[dict]],
    guardrail_comparisons: list[ComparisonConfig],
) -> tuple[GuardrailStatus, ...]:
    statuses: list[GuardrailStatus] = []
    for guardrail in guardrail_comparisons:
        group = series.get((guardrail.metric, control, treatment), [])
        informative = [row for row in group if _informative(row)]
        desired_sign = 1 if guardrail.desired_direction == "increase" else -1
        if not informative:
            statuses.append(
                GuardrailStatus(
                    metric=guardrail.metric,
                    name_1=control,
                    name_2=treatment,
                    regressed=False,
                    effect=None,
                    desired_direction=guardrail.desired_direction,
                )
            )
            continue
        latest = informative[-1]
        # D5(c): regression = the STORED CI excludes zero against the desired
        # direction at the stored per-row alpha — conservative ("any
        # significant harm flags"), no stabilization requirement, and
        # deliberately CORRECTION-INDEPENDENT: BH adjustment (which can only
        # inflate p-values) must never un-flag a stored-significant harm
        # (milestone-review finding).
        left = _num(latest.get("left_bound"))
        right = _num(latest.get("right_bound"))
        sign = (
            1 if (left is not None and left > 0) else -1 if (right is not None and right < 0) else 0
        )
        regressed = sign != 0 and sign == -desired_sign
        statuses.append(
            GuardrailStatus(
                metric=guardrail.metric,
                name_1=control,
                name_2=treatment,
                regressed=regressed,
                effect=_num(latest.get("effect")),
                desired_direction=guardrail.desired_direction,
            )
        )
    return tuple(statuses)


def _apply_guardrail_policy(
    experiment: ExperimentConfig,
    verdict: VerdictKind,
    guardrails: tuple[GuardrailStatus, ...],
) -> tuple[VerdictKind, list[str], list[str]]:
    """D5(c), owner-ratified: block caps WIN; warn keeps WIN with a loud caveat.

    The regression is always spelled out; LOSE is never upgraded or blocked.
    """
    rationale: list[str] = []
    caveats: list[str] = []
    regressed = [g for g in guardrails if g.regressed]
    if not regressed:
        return verdict, rationale, caveats
    policy = experiment.readout.guardrail_policy
    for g in regressed:
        effect_str = f"effect {g.effect:+.4g}" if g.effect is not None else "effect unavailable"
        message = (
            f"guardrail {g.metric!r} regressed ({effect_str} against desired "
            f"direction {g.desired_direction!r})"
        )
        if verdict == "WIN" and policy == "block":
            rationale.append(f"{message} — WIN withheld (guardrail_policy: block)")
        elif verdict == "WIN":
            caveats.append(f"{message} — verdict kept under guardrail_policy: warn")
        else:
            caveats.append(message)
    if verdict == "WIN" and policy == "block":
        return "INCONCLUSIVE", rationale, caveats
    return verdict, rationale, caveats
