"""m13 STAT-1: `correction: holm` at the config and CLI surfaces.

The rule itself is pinned in ``test_correction_rule.py`` and its verdict
behaviour in ``test_readout.py::TestHolm``. What is left is everything a
read-time scheme touches OUTSIDE the rule: the compute-time alpha it must NOT
resolve (a step procedure has no per-comparison level), and the surfaces that
print a level and would otherwise claim the sizing level is the decision level.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from abkit.config import ExperimentConfig, ProjectConfig
from abkit.pipeline.analyze import comparison_alpha, effective_alphas

METHOD = {"name": "t-test", "params": {"test_type": "relative"}}

COMPARISONS = [
    {"metric": "arpu", "is_main_metric": True, "method": METHOD},
    {"metric": "clicks", "method": METHOD},
    {"metric": "crashes", "is_guardrail": True, "method": METHOD},
]


def _experiment(**overrides) -> ExperimentConfig:
    payload: dict = {
        "name": "holm_test",
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-06",
        "unit_key": "user_id",
        "assignment": {
            "query": "SELECT user_id, variant, exposure_ts FROM assignments",
            # THREE arms: at two arms C(g,2) = 1 and every tier collapses to the
            # raw alpha, so a Bonferroni-vs-raw assertion could not fail
            "variants": ["control", "t1", "t2"],
            "expected_split": {"control": 0.34, "t1": 0.33, "t2": 0.33},
        },
        "comparisons": COMPARISONS,
    }
    payload.update(overrides)
    return ExperimentConfig.model_validate(payload)


def _project(**statistics) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {"name": "p", "default_profile": "dev", "statistics": {"alpha": 0.05, **statistics}}
    )


class TestConfigSurface:
    def test_holm_is_accepted_at_both_levels(self) -> None:
        assert _project(correction="holm").statistics.correction == "holm"
        assert _experiment(correction="holm").correction == "holm"

    def test_an_unknown_scheme_is_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _project(correction="hochberg")
        with pytest.raises(ValidationError):
            _experiment(correction="hochberg")

    def test_the_experiment_overrides_the_project(self) -> None:
        alphas = effective_alphas(_experiment(correction="holm"), _project(correction="bonferroni"))
        assert alphas.main == 0.05


class TestCompuTimeAlphaStaysRaw:
    """A step procedure has no per-comparison level, so the rows carry the raw
    alpha — exactly as under BH. Resolving a two-tier level here would silently
    apply Bonferroni AND Holm, i.e. correct twice."""

    def test_every_tier_is_the_raw_alpha(self) -> None:
        experiment = _experiment(correction="holm")
        alphas = effective_alphas(experiment, _project())
        assert (alphas.main, alphas.secondary) == (0.05, 0.05)
        by_metric = {c.metric: comparison_alpha(c, alphas) for c in experiment.comparisons}
        assert by_metric == {"arpu": 0.05, "clicks": 0.05, "crashes": 0.05}

    def test_bonferroni_on_the_same_experiment_does_divide(self) -> None:
        """The premise: at three arms with two non-main metrics the tiers differ,
        so the assertion above is about the scheme and not about the fixture."""
        alphas = effective_alphas(_experiment(correction="bonferroni"), _project())
        assert alphas.main == pytest.approx(0.05 / 3)
        assert alphas.secondary == pytest.approx(0.05 / 6)

    def test_holm_and_bh_resolve_identically(self) -> None:
        holm = effective_alphas(_experiment(correction="holm"), _project())
        bh = effective_alphas(_experiment(correction="benjamini_hochberg"), _project())
        assert (holm.main, holm.secondary) == (bh.main, bh.secondary)

    def test_guardrail_correction_none_is_inert_under_a_read_time_scheme(self) -> None:
        """m13 STAT-1c's flip only bites under Bonferroni — under Holm the
        guardrail already had the raw alpha, and the divisor is unused."""
        experiment = _experiment(correction="holm")
        inherit = effective_alphas(experiment, _project())
        untiered = effective_alphas(experiment, _project(guardrail_correction="none"))
        assert (inherit.main, inherit.secondary) == (untiered.main, untiered.secondary)


class TestPlanHeader:
    """`abk plan` prints ONE alpha statement. Under a read-time scheme that number
    is the raw alpha it sized at, not the level anything is judged at."""

    def _note(self, correction: str) -> str:
        from abkit.cli.commands.plan import _correction_note

        experiment = _experiment(correction=correction)
        return _correction_note(experiment, effective_alphas(experiment, _project()), correction)

    def test_a_read_time_scheme_is_named_and_the_level_is_qualified(self) -> None:
        for scheme in ("holm", "benjamini_hochberg"):
            note = self._note(scheme)
            assert scheme in note
            assert "read-time" in note
            assert "raw" in note

    def test_a_compute_time_scheme_says_nothing_extra(self) -> None:
        note = self._note("bonferroni")
        assert "read-time" not in note
        assert "main 0.01667" in note

    def test_emit_plan_passes_the_resolved_scheme(self, capsys) -> None:
        """The wiring, not the helper: a caller that forgot the argument would
        print the most misleading header of the three."""
        from abkit.cli.commands.plan import _emit_plan

        experiment = _experiment(correction=None)  # inherits the project's holm
        project = _project(correction="holm")
        alphas = effective_alphas(experiment, project)
        grid = experiment.grid()
        _emit_plan(experiment, project, alphas, 0.8, len(grid), grid, 42, [])
        assert "holm is applied read-time" in capsys.readouterr().out
