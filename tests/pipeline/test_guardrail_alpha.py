"""m13 STAT-1c / decision D8: guardrails stop sharing the secondary budget.

Correcting a guardrail costs sensitivity to the harm it exists to catch, so the
error points the dangerous way. Under ``guardrail_correction: none`` a guardrail
is tested at the RAW alpha **and** stops counting towards the secondary divisor.

The second half is what these tests exist for. Resolving only the first — giving
guardrails the raw alpha while still counting them — produces a scheme that helps
guardrails, taxes nothing else, and leaves every persisted secondary alpha
unchanged, so no pre-existing assertion in the suite would notice.
"""

from __future__ import annotations

import pytest

from abkit.config import ExperimentConfig, ProjectConfig
from abkit.pipeline.analyze import comparison_alpha, effective_alphas

METHOD = {"name": "t-test", "params": {"test_type": "relative"}}


def _experiment(comparisons: list[dict], **overrides) -> ExperimentConfig:
    payload: dict = {
        "name": "guardrail_test",
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-06",
        "unit_key": "user_id",
        "assignment": {
            "query": "SELECT user_id, variant, exposure_ts FROM assignments",
            "variants": ["control", "treatment"],
            "expected_split": {"control": 0.5, "treatment": 0.5},
        },
        "comparisons": comparisons,
    }
    payload.update(overrides)
    return ExperimentConfig.model_validate(payload)


def _project(**statistics) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {"name": "p", "default_profile": "dev", "statistics": {"alpha": 0.05, **statistics}}
    )


#: 1 main + 1 screening + 1 guardrail over 2 variants, so C(g,2) = 1 and the
#: arithmetic is readable: inherit ⇒ secondary divisor 2, none ⇒ divisor 1.
MIXED = [
    {"metric": "arpu", "is_main_metric": True, "method": METHOD},
    {"metric": "clicks", "method": METHOD},
    {"metric": "crashes", "is_guardrail": True, "method": METHOD},
]


class TestGuardrailTiering:
    def test_inherit_is_the_pre_0_8_0_scheme(self) -> None:
        """The default must not move a single number (m13 D1)."""
        project = _project()
        assert project.statistics.guardrail_correction == "inherit"
        alphas = effective_alphas(_experiment(MIXED), project)

        assert alphas.metrics_count == 2  # the guardrail still counts
        assert alphas.main == pytest.approx(0.05)
        assert alphas.secondary == pytest.approx(0.025)
        assert alphas.guardrail is None

        exp = _experiment(MIXED)
        per_metric = {c.metric: comparison_alpha(c, alphas) for c in exp.comparisons}
        assert per_metric == pytest.approx({"arpu": 0.05, "clicks": 0.025, "crashes": 0.025})

    def test_none_gives_the_guardrail_raw_alpha(self) -> None:
        exp = _experiment(MIXED)
        alphas = effective_alphas(exp, _project(guardrail_correction="none"))

        assert alphas.guardrail == pytest.approx(0.05)
        per_metric = {c.metric: comparison_alpha(c, alphas) for c in exp.comparisons}
        assert per_metric["crashes"] == pytest.approx(0.05)

    def test_none_ALSO_loosens_the_screening_metrics_that_remain(self) -> None:
        """The second half of D8, and the one a partial implementation drops.

        With the guardrail out of the tier the divisor falls 2 → 1, so the
        screening metric's alpha doubles. An implementation that only re-routed
        the guardrail would leave ``clicks`` at 0.025 and still pass every other
        assertion in this file.
        """
        exp = _experiment(MIXED)
        alphas = effective_alphas(exp, _project(guardrail_correction="none"))

        assert alphas.metrics_count == 1
        assert alphas.secondary == pytest.approx(0.05)
        per_metric = {c.metric: comparison_alpha(c, alphas) for c in exp.comparisons}
        assert per_metric["clicks"] == pytest.approx(0.05)
        assert per_metric["arpu"] == pytest.approx(0.05)  # main tier is untouched

    def test_guardrail_only_experiment_does_not_fall_back_to_the_MAIN_alpha(self) -> None:
        """The k=0 edge, and the reason ``comparison_alpha`` tests the guardrail
        tier BEFORE the ``secondary is None`` fallback.

        When every non-main comparison is a guardrail, ``metrics_count`` becomes
        0 and ``secondary`` is ``None``. The pre-D8 fallback then returned
        ``main`` — the TIGHTEST level in the scheme — which is the exact opposite
        of what D8 asks for, and it would be invisible in any fixture that also
        had a screening metric.

        **THREE arms, deliberately.** With two arms ``C(g,2) = 1``, so the main
        alpha and the raw alpha are the same number and a wrong check order is
        undetectable — a mutation probe that reordered the two branches left this
        test green until the third variant was added. At three arms main is
        0.05/3 and the guardrail must still be 0.05.
        """
        exp = _experiment(
            [
                {"metric": "arpu", "is_main_metric": True, "method": METHOD},
                {"metric": "crashes", "is_guardrail": True, "method": METHOD},
            ],
            assignment={
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "t1", "t2"],
                "expected_split": {"control": 1 / 3, "t1": 1 / 3, "t2": 1 / 3},
            },
        )
        alphas = effective_alphas(exp, _project(guardrail_correction="none"))

        assert alphas.metrics_count == 0
        assert alphas.secondary is None
        assert alphas.main == pytest.approx(0.05 / 3)
        per_metric = {c.metric: comparison_alpha(c, alphas) for c in exp.comparisons}
        assert per_metric["crashes"] == pytest.approx(0.05)

    def test_multi_arm_divides_by_pairs_in_both_modes(self) -> None:
        """C(g,2) applies to every tier; only the metric divisor moves."""
        exp = _experiment(
            MIXED,
            assignment={
                "query": "SELECT user_id, variant, exposure_ts FROM assignments",
                "variants": ["control", "t1", "t2"],
                "expected_split": {"control": 1 / 3, "t1": 1 / 3, "t2": 1 / 3},
            },
        )
        inherit = effective_alphas(exp, _project())
        untiered = effective_alphas(exp, _project(guardrail_correction="none"))

        assert inherit.main == pytest.approx(0.05 / 3)
        assert inherit.secondary == pytest.approx(0.05 / 6)
        assert untiered.secondary == pytest.approx(0.05 / 3)
        # the guardrail's own level is the RAW alpha — never divided by pairs,
        # because it has left the corrected family altogether
        assert untiered.guardrail == pytest.approx(0.05)

    @pytest.mark.parametrize("correction", ["none", "benjamini_hochberg"])
    def test_flip_moves_no_ALPHA_when_the_correction_is_not_bonferroni(
        self, correction: str
    ) -> None:
        """Every other scheme already hands out the raw alpha at compute time.

        The assertion is deliberately about the ALPHAS and not about the whole
        dataclass: ``metrics_count`` still reports the DECLARED tiering (2 vs 1),
        because it describes which comparisons would share a secondary budget,
        not which ones did. It is inspectable-only — nothing computes from it —
        so the D1 promise ("a project that changes nothing reproduces 0.7.0") is
        about the levels, and those are identical here.
        """
        exp = _experiment(MIXED)
        before = effective_alphas(exp, _project(correction=correction))
        after = effective_alphas(exp, _project(correction=correction, guardrail_correction="none"))

        for comparison in exp.comparisons:
            assert comparison_alpha(comparison, before) == pytest.approx(
                comparison_alpha(comparison, after)
            )
        assert after.main == pytest.approx(0.05)
        assert after.secondary == pytest.approx(0.05)
        assert after.guardrail is None

    def test_experiment_override_beats_the_project_default(self) -> None:
        exp = _experiment(MIXED, guardrail_correction="none")
        alphas = effective_alphas(exp, _project())  # project says 'inherit'

        assert alphas.guardrail == pytest.approx(0.05)
        assert alphas.metrics_count == 1

    def test_experiment_none_means_inherit_the_project(self) -> None:
        exp = _experiment(MIXED)
        assert exp.guardrail_correction is None
        alphas = effective_alphas(exp, _project(guardrail_correction="none"))
        assert alphas.guardrail == pytest.approx(0.05)
