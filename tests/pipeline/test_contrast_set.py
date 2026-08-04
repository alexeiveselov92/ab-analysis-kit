"""m13 STAT-1b / decision D15: an experiment declares the contrasts it claims.

``contrasts: vs_control`` says the family is the ``g−1`` many-to-one contrasts
against the first declared variant. Two things follow, and they must move
together — this file exists to make a half-implementation fail:

1. the alpha divisor falls from ``C(g,2)`` to ``g−1`` (that is the power win);
2. the treatment-vs-treatment pairs are no longer COMPUTED, and every surface
   that filters persisted rows agrees they are not declared.

Resolving only (1) hands out levels bought for ``g−1`` contrasts while writing
``C(g,2)`` rows — a silently broken FWER claim, and a re-run of every existing
fixture would not notice. Resolving only (2) writes fewer rows at the old,
needlessly tight level: the experiment is more conservative than it declared,
which no assertion about alphas alone can see either.

**Three arms everywhere.** At two arms ``C(2,2) = 1 = g−1``, so the two families
are numerically identical and every assertion here would pass against a
no-op implementation.
"""

from __future__ import annotations

import numpy as np
import pytest

from abkit.config import ExperimentConfig, ProjectConfig
from abkit.pipeline.analyze import analyze_cutoff, comparison_alpha, effective_alphas
from abkit.stats import n_comparisons

METHOD = {"name": "t-test", "params": {"test_type": "relative"}}
THREE_ARMS = {
    "query": "SELECT user_id, variant, exposure_ts FROM assignments",
    "variants": ["control", "t1", "t2"],
    "expected_split": {"control": 1 / 3, "t1": 1 / 3, "t2": 1 / 3},
}
FOUR_ARMS = {
    "query": "SELECT user_id, variant, exposure_ts FROM assignments",
    "variants": ["control", "t1", "t2", "t3"],
    "expected_split": {"control": 0.25, "t1": 0.25, "t2": 0.25, "t3": 0.25},
}

MIXED = [
    {"metric": "arpu", "is_main_metric": True, "method": METHOD},
    {"metric": "clicks", "method": METHOD},
]


def _experiment(**overrides) -> ExperimentConfig:
    payload: dict = {
        "name": "contrast_test",
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-06",
        "unit_key": "user_id",
        "assignment": THREE_ARMS,
        "comparisons": MIXED,
    }
    payload.update(overrides)
    return ExperimentConfig.model_validate(payload)


def _project(**statistics) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {"name": "p", "default_profile": "dev", "statistics": {"alpha": 0.05, **statistics}}
    )


class TestTheDeclaredFamily:
    def test_all_pairs_is_the_default_and_reproduces_combinations(self) -> None:
        """m13 D1: a config that says nothing must behave exactly like 0.7.0."""
        exp = _experiment()
        assert exp.contrasts == "all_pairs"
        assert exp.contrast_pairs() == (
            ("control", "t1"),
            ("control", "t2"),
            ("t1", "t2"),
        )

    def test_vs_control_keeps_the_control_contrasts_in_the_same_order(self) -> None:
        """A prefix-ordered SUBSET: the shared rows keep their identity and their
        place, so flipping the knob cannot reshuffle an existing report."""
        exp = _experiment(contrasts="vs_control")
        assert exp.contrast_pairs() == (("control", "t1"), ("control", "t2"))

    def test_the_control_is_the_first_declared_variant(self) -> None:
        """D15: STAT-1b does not wait for M14's explicit ``control:`` field — the
        positional convention (baseline §5, first variant = ``name_1``) already
        answers WHICH arm is the control, and it is the same convention the
        readout's verdicts and the SRM rollup already read."""
        exp = _experiment(
            contrasts="vs_control",
            assignment={**THREE_ARMS, "variants": ["t1", "control", "t2"]},
        )
        assert exp.contrast_pairs() == (("t1", "control"), ("t1", "t2"))

    def test_two_arms_make_the_knob_a_no_op(self) -> None:
        exp = _experiment(
            contrasts="vs_control",
            assignment={
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            },
        )
        assert exp.contrast_pairs() == (("control", "treatment"),)

    def test_an_unknown_family_is_rejected_at_parse(self) -> None:
        with pytest.raises(ValueError):
            _experiment(contrasts="vs_everything")


class TestTheDivisor:
    def test_the_pair_count_is_the_declared_family(self) -> None:
        assert n_comparisons(4, 1, "all_pairs") == 6
        assert n_comparisons(4, 1, "vs_control") == 3
        assert n_comparisons(4, 2, "vs_control") == 6  # × the secondary tier

    def test_the_legacy_default_is_unchanged_at_every_call_site(self) -> None:
        """Nothing that omits the argument may move (D1)."""
        for groups in range(2, 8):
            assert n_comparisons(groups) == groups * (groups - 1) / 2

    def test_vs_control_loosens_both_tiers_by_g_over_2(self) -> None:
        """The whole point of the WP: at four arms the level triples."""
        exp = _experiment(assignment=FOUR_ARMS)
        wide = effective_alphas(exp, _project())
        narrow = effective_alphas(
            _experiment(assignment=FOUR_ARMS, contrasts="vs_control"), _project()
        )

        assert wide.main == pytest.approx(0.05 / 6)
        assert narrow.main == pytest.approx(0.05 / 3)
        assert wide.secondary == pytest.approx(0.05 / 6)  # 1 non-main metric
        assert narrow.secondary == pytest.approx(0.05 / 3)
        assert narrow.main / wide.main == pytest.approx(4 / 2)

    def test_the_resolved_object_carries_the_family_it_was_divided_by(self) -> None:
        """``groups_count`` alone no longer determines the divisor, so every
        surface that re-derives it (the CLI header lines, the explore client
        mirror) must read the family off the result rather than assume."""
        alphas = effective_alphas(_experiment(contrasts="vs_control"), _project())
        assert alphas.contrasts == "vs_control"
        assert alphas.pairs_count == 2
        assert effective_alphas(_experiment(), _project()).pairs_count == 3

    def test_the_family_travels_through_the_uncorrected_schemes_too(self) -> None:
        """``none``/BH hand out the raw alpha, but the object still reports the
        declared family — the CLI prints it either way, and a stale
        ``all_pairs`` there would misdescribe the rows that get written."""
        for scheme in ("none", "benjamini_hochberg"):
            alphas = effective_alphas(
                _experiment(contrasts="vs_control"), _project(correction=scheme)
            )
            assert alphas.main == pytest.approx(0.05)
            assert alphas.contrasts == "vs_control"
            assert alphas.pairs_count == 2

    def test_the_guardrail_tier_is_not_divided_by_the_family(self) -> None:
        """D8 × STAT-1b: an untiered guardrail is the RAW alpha under both
        families — a level it does not pay for cannot tighten it."""
        exp = _experiment(
            contrasts="vs_control",
            comparisons=[*MIXED, {"metric": "crashes", "is_guardrail": True, "method": METHOD}],
        )
        alphas = effective_alphas(exp, _project(guardrail_correction="none"))
        per_metric = {c.metric: comparison_alpha(c, alphas) for c in exp.comparisons}
        assert per_metric["crashes"] == pytest.approx(0.05)
        assert per_metric["arpu"] == pytest.approx(0.025)  # 0.05 / (g−1 = 2)


class _Loaded:
    """A minimal ``MetricLoadResult`` stand-in for the analyze stage."""

    def __init__(self, variants: list[str], n: int = 400) -> None:
        rng = np.random.default_rng(11)
        self.roles_by_variant = {v: {"value": rng.normal(10.0, 2.0, n)} for v in variants}
        self.strata_by_variant: dict[str, np.ndarray] = {}

    def size(self, variant: str) -> int:
        return int(self.roles_by_variant[variant]["value"].size)


class TestTheAnalyzeStageComputesTheDeclaredPairsOnly:
    def _outcomes(self, exp: ExperimentConfig) -> list[tuple[str, str]]:
        from datetime import datetime

        from abkit.config.metric_config import MetricConfig

        project = _project()
        metric = MetricConfig.model_validate(
            {
                "name": "arpu",
                "type": "sample",
                "columns": {"variant": "variant", "value": "value"},
                "query": "SELECT variant, value FROM facts",
            }
        )
        outcomes = analyze_cutoff(
            exp,
            exp.comparisons[0],
            metric,
            _Loaded(list(exp.assignment.variants)),
            datetime(2024, 7, 3),
            effective_alphas(exp, project),
            project,
        )
        return [(o.name_1, o.name_2) for o in outcomes]

    def test_all_pairs_computes_every_pair(self) -> None:
        assert self._outcomes(_experiment()) == [
            ("control", "t1"),
            ("control", "t2"),
            ("t1", "t2"),
        ]

    def test_vs_control_never_computes_a_treatment_vs_treatment_row(self) -> None:
        """The row that is not written is the row nobody has to explain: leaving
        it in at the loosened level is what breaks the FWER claim the loosened
        level was bought with."""
        assert self._outcomes(_experiment(contrasts="vs_control")) == [
            ("control", "t1"),
            ("control", "t2"),
        ]

    def test_the_computed_pairs_are_tested_at_the_declared_family_alpha(self) -> None:
        """Divisor and enumeration are one declaration — a fixture where they
        disagree is exactly the half-implementation this file guards against."""
        exp = _experiment(contrasts="vs_control")
        project = _project()
        alphas = effective_alphas(exp, project)
        assert len(exp.contrast_pairs()) == alphas.pairs_count
        assert alphas.main == pytest.approx(0.05 / len(exp.contrast_pairs()))


class TestTheReadSurfacesAgreeOnTheFamily:
    """The three persisted-row filters (report / dashboard / notify) and the
    producer must answer the same question. They are separate two-line
    comprehensions on purpose (m11 DASH-2, m12 NTF-1), but the SET now comes
    from one factory — before STAT-1b each carried its own ``combinations``
    call, and a knob resolved in one place would have reached none of them."""

    ROWS = [
        {"name_1": "control", "name_2": "t1"},
        {"name_1": "control", "name_2": "t2"},
        {"name_1": "t1", "name_2": "t2"},
        {"name_1": "control", "name_2": "renamed_away"},
    ]

    def _filters(self):
        from abkit.notify.dispatch import declared_pairs_only
        from abkit.tuning.overview import _declared_pairs_only

        return (declared_pairs_only, _declared_pairs_only)

    def test_all_pairs_keeps_the_treatment_contrast_and_drops_only_the_stale_arm(self) -> None:
        exp = _experiment()
        for filt in self._filters():
            kept = [(r["name_1"], r["name_2"]) for r in filt(exp, self.ROWS)]
            assert kept == [("control", "t1"), ("control", "t2"), ("t1", "t2")]

    def test_vs_control_drops_the_treatment_vs_treatment_rows_too(self) -> None:
        """Not cosmetic: those rows would otherwise enter the read-time BH
        family and tighten every member's threshold — an experiment that
        narrowed its family would be scored against the family it left."""
        exp = _experiment(contrasts="vs_control")
        for filt in self._filters():
            kept = [(r["name_1"], r["name_2"]) for r in filt(exp, self.ROWS)]
            assert kept == [("control", "t1"), ("control", "t2")]


class TestTheSequentialRePlanPredicate:
    """A pair nothing recomputes must not force a re-plan forever.

    ``_sequential_mode_changed`` compares the persisted ``ci_kind`` against the
    mode this run would stamp, and re-plans the whole series on a mismatch. A
    row left behind by a narrowed family (or a renamed arm) can never be
    superseded — no cutoff writes that pair again — so judging it would make the
    predicate permanently true: a full-series re-plan on every scheduled run,
    silently, for rows no surface reads.
    """

    def test_an_undeclared_pairs_stale_mode_does_not_force_a_replan(self) -> None:
        from abkit.pipeline.driver import _sequential_mode_changed

        exp = _experiment(contrasts="vs_control")
        declared = frozenset(exp.contrast_pairs())
        persisted = {
            ("control", "t1"): {"always_valid"},
            ("t1", "t2"): {"always_valid"},  # written before the family narrowed
        }
        tau2 = {("control", "t1"): 0.01, ("control", "t2"): 0.01}

        assert not _sequential_mode_changed(persisted, True, tau2, declared)
        # ...and without the declared set it fires forever (the pre-fix shape)
        assert _sequential_mode_changed(persisted, True, tau2)

    def test_a_declared_pair_still_self_invalidates(self) -> None:
        """The M5 WP3 toggle must keep working — the filter narrows WHICH pairs
        are judged, never whether the judgment happens."""
        from abkit.pipeline.driver import _sequential_mode_changed

        exp = _experiment(contrasts="vs_control")
        declared = frozenset(exp.contrast_pairs())
        persisted = {("control", "t1"): {"fixed"}}
        tau2 = {("control", "t1"): 0.01}

        assert _sequential_mode_changed(persisted, True, tau2, declared)


class TestTheOperatorCanReconcileTheNumber:
    """Every CLI surface that prints the divisor must name the family.

    ``abk run`` / ``abk validate`` / ``abk plan`` each printed a literal
    ``C(g,2)``, which is true of exactly one of the two families. An operator
    who cannot reconcile the divisor with the arm count beside it reads the
    alpha as a bug — the STAT-1c lesson, where the plan header knew two tiers
    of three and contradicted the row beneath it.
    """

    def test_the_pairs_phrase_names_the_family_it_counted(self) -> None:
        from abkit.cli._output import pairs_phrase

        wide = effective_alphas(_experiment(assignment=FOUR_ARMS), _project())
        narrow = effective_alphas(
            _experiment(assignment=FOUR_ARMS, contrasts="vs_control"), _project()
        )

        assert pairs_phrase(wide) == "C(4,2)=6 pairs"
        assert "vs_control" in pairs_phrase(narrow)
        assert "3" in pairs_phrase(narrow)
        assert "C(4,2)" not in pairs_phrase(narrow)

    def test_the_plan_header_says_which_family_bought_the_level(self) -> None:
        """At three arms the two families print DIFFERENT main alphas off the
        same arm count; only this string explains the difference."""
        from abkit.cli.commands.plan import _correction_note

        exp = _experiment(contrasts="vs_control")
        note = _correction_note(exp, effective_alphas(exp, _project()))
        assert "vs_control" in note
        assert "0.025" in note

        default = _experiment()
        assert "vs_control" not in _correction_note(
            default, effective_alphas(default, _project())
        )

    def test_two_arms_do_not_advertise_a_family_that_changes_nothing(self) -> None:
        from abkit.cli.commands.plan import _correction_note

        exp = _experiment(
            contrasts="vs_control",
            assignment={
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "treatment"],
                "expected_split": {"control": 0.5, "treatment": 0.5},
            },
        )
        assert "vs_control" not in _correction_note(exp, effective_alphas(exp, _project()))
