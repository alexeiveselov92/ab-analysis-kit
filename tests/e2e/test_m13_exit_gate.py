"""The M13 exit gate: versioned statistics that move no default
(m13-implementation-plan.md §4).

M7–M12 all shipped under "no statistical number moves". M13 inverts that
posture — it is the first milestone of the track whose numbers DO move — so the
safety property is narrower and has to be proved rather than grepped: **numbers
move, but no default does.** A project that writes nothing new reproduces
``0.7.0``; a project that opts in gets a new series it asked for.

The six legs are the plan's §4, in order:

1. **The byte-compatibility gate — the milestone's №1 assertion.** Two surfaces
   captured from the *released* ``v0.7.0`` code itself (``_m13_baseline.py``,
   which documents how to regenerate) reproduce here: the scaffolded project as
   shipped, and a three-arm five-comparison experiment that reaches every
   default M13 touched. Discrete columns exactly, continuous at rel-1e-9, JSON
   payload columns PARSED before comparison — the M9 lesson, because a CUPED θ
   differs in its last ULP and comparing serialized strings demands a property
   IEEE-754 does not offer. (On one machine the two surfaces are in fact
   byte-identical; the gate does not assert that, because byte reproducibility
   holds only under a fixed BLAS configuration — M7 D13 — and CI is not this
   machine.)
2. **Opting in forks the series; changing the correction scheme does not.**
   That asymmetry is what makes D4 — no ``ALGORITHM_VERSION`` bump anywhere in
   M13 — safe, and it is asserted through the pipeline rather than on the hash:
   ``interval: fieller`` writes a *new* ``method_config_id`` beside the old
   rows, while ``correction: holm`` re-decides the rows already stored.
3. **The family rule lands at α**, measured over the composed sweep the A/A
   instrument runs, not argued from the derivation.
4. **The sign instrument sees the estimator the FPR column cannot** (STAT-2 ×
   STAT-4): on one panel, ``delta`` and ``fieller`` report the same
   false-positive RATE and a different false-positive SIGN split — which is why
   the two-sided column had called a one-sidedly broken interval calibrated. Its
   STAT-3 twin is stronger still and asserted as an EQUALITY: under one set of
   placebo draws ``pooled`` and ``score`` agree to the last float on the FPR,
   the sign split and the power, and differ only in coverage.
5. **No ``ALGORITHM_VERSION`` was bumped** (D4), derived from the registry.
6. **Every knob M13 added still defaults to the legacy branch**, likewise
   derived — a hand-written list of defaults passes by being edited.

What leg 1 does NOT reach, stated rather than implied: the paired family
(``paired-t-test``, ``paired-cuped-t-test``) — two of STAT-4's five adopters —
is notebook-only and cannot appear in a pipeline fixture at all, and the
sequential layer is off by default. Those are pinned where they belong: the
paired methods by the legacy goldens in ``tests/golden/`` (rel-1e-9 against an
independent transcription, a stronger anchor than a persisted-row diff), and the
sequential transform by ``tests/stats/sequential/``. This file's claim is about
what a *project* gets, not about every method the registry holds.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from _m13_baseline import (
    MULTI_ARM_PAYLOAD,
    ROW_ORDER,
    build_multi_arm_context,
    capture_multi_arm_surface,
    capture_scaffold_surface,
)

from abkit.stats import create_method, get_method_class
from abkit.stats.registry import available_methods

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "results_golden_0_7_0.json"

#: Columns whose payload is JSON: compared PARSED, so a last-ULP difference in
#: an embedded float is judged by the same rel-1e-9 rule as a top-level one
#: (and key order cannot make an equal payload unequal).
JSON_COLUMNS = frozenset(
    {
        "method_params",
        "warnings",
        "diagnostics",
        "cadence",
        "variants",
        "expected_split",
        "comparisons",
        "tags",
    }
)

REL = 1e-9


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text())


def _assert_value_matches(actual, expected, where: str) -> None:
    if isinstance(expected, bool) or isinstance(actual, bool):
        assert actual == expected, where
    elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if isinstance(expected, float) and math.isnan(expected):
            assert isinstance(actual, float) and math.isnan(actual), where
        else:
            assert math.isclose(
                actual, expected, rel_tol=REL, abs_tol=1e-12
            ), f"{where}: {actual!r} != {expected!r} (rel={REL})"
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and set(actual) == set(expected), where
        for key in expected:
            _assert_value_matches(actual[key], expected[key], f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), where
        for index, item in enumerate(expected):
            _assert_value_matches(actual[index], item, f"{where}[{index}]")
    else:
        assert actual == expected, where


def assert_rows_match(actual: list[dict], expected: list[dict], what: str, *, added=frozenset()):
    """Row-by-row, column-by-column — ``added`` names columns this release grew."""
    assert len(actual) == len(expected), f"{what}: {len(actual)} rows vs {len(expected)}"
    for row_index, (got, want) in enumerate(zip(actual, expected, strict=True)):
        identity = "/".join(str(want.get(c, "")) for c in ROW_ORDER)
        assert set(got) - set(want) == set(added), f"{what}[{identity}]: unexpected new columns"
        assert not set(want) - set(got), f"{what}[{identity}]: columns disappeared"
        for column, expected_value in want.items():
            where = f"{what}[{row_index} {identity}].{column}"
            if (
                column in JSON_COLUMNS
                and isinstance(expected_value, str)
                and isinstance(got[column], str)
            ):
                _assert_value_matches(json.loads(got[column]), json.loads(expected_value), where)
            else:
                _assert_value_matches(got[column], expected_value, where)


class TestByteCompatibility:
    """Leg 1 — the milestone's №1 assertion, against the released code."""

    def test_the_scaffolded_project_reproduces_0_7_0(self, golden):
        surface = capture_scaffold_surface()
        assert_rows_match(
            surface["_ab_results"], golden["scaffold"]["_ab_results"], "scaffold/_ab_results"
        )

    def test_the_multi_arm_defaults_reproduce_0_7_0(self, golden):
        """Three arms, five comparisons: the C(3,2) family (STAT-1b's default),
        a guardrail (STAT-1c's), ``z-test`` pooled (STAT-3's), three mean methods
        on the relative scale (STAT-4's) and one bootstrap — which M13 never
        touched, so its seeds pin that the new param folding in
        ``BaseMethod.__init__`` moved nothing either."""
        surface = capture_multi_arm_surface()
        assert_rows_match(
            surface["_ab_results"], golden["multi_arm"]["_ab_results"], "multi_arm/_ab_results"
        )

    def test_the_only_catalog_delta_is_the_declared_family(self, golden):
        """``_ab_experiments`` grew exactly one column, and it is POPULATED.

        STAT-1b declared ``contrasts`` in the model and emitted it from
        ``catalog_record``, but ``_EXPERIMENT_FIELDS`` — the writer's whitelist —
        never learned it, so the column shipped empty. Comparing against the
        released catalog row is what surfaced that: the whole point of the
        column is that BI can tell a narrowed family from an incomplete run.
        """
        surface = capture_scaffold_surface()
        assert_rows_match(
            surface["_ab_experiments"],
            golden["scaffold"]["_ab_experiments"],
            "scaffold/_ab_experiments",
            added={"contrasts"},
        )
        assert surface["_ab_experiments"][0]["contrasts"] == "all_pairs"


class TestOptingInForksTheSeriesButTheSchemeDoesNot:
    """Leg 2 — the asymmetry that makes "no ``ALGORITHM_VERSION`` bump" safe."""

    def _run(self, payload) -> list[dict]:
        return capture_multi_arm_surface(payload)["_ab_results"]

    @staticmethod
    def _with(**overrides) -> dict:
        payload = json.loads(json.dumps(MULTI_ARM_PAYLOAD))
        payload.update(overrides)
        return payload

    def test_a_new_interval_param_writes_a_new_series_beside_the_old(self):
        """``interval: fieller`` on the main comparison, run against the SAME
        storage as the default: the legacy rows are still there afterwards, and
        the opted-in ones land beside them under a different
        ``method_config_id``. That is what "the operator orphans their own
        series" means, and why no version field had to move. Two separate
        warehouses would only have shown two different answers — never that they
        coexist.
        """
        context = build_multi_arm_context()
        legacy = capture_multi_arm_surface(context=context)["_ab_results"]
        opted_payload = json.loads(json.dumps(MULTI_ARM_PAYLOAD))
        opted_payload["comparisons"][0]["method"]["params"] = {
            "test_type": "relative",
            "interval": "fieller",
        }
        after = capture_multi_arm_surface(opted_payload, context=context)["_ab_results"]

        def ids(rows, metric):
            return {row["method_config_id"] for row in rows if row["metric"] == metric}

        # the old series is intact, the new one is additional
        assert ids(legacy, "arpu") < ids(after, "arpu")
        assert len(ids(after, "arpu")) == 2
        assert [r for r in after if r in legacy] == legacy  # every legacy row survived verbatim
        # every OTHER comparison keeps its single identity: the fork is scoped
        # to the method whose params changed, not to the experiment
        for metric in ("conversion", "ctr", "arpu_cuped", "arpu_boot"):
            assert ids(after, metric) == ids(legacy, metric), metric

    def test_switching_to_holm_re_decides_the_series_it_finds(self):
        """The other side of the asymmetry, and the reason §6.3 keeps
        ``correction`` out of ``method_config_id``: a read-time scheme has to be
        able to re-read rows that already exist, so Holm must NOT fork them.
        Asserted through the pipeline (the ids are the same rows) and on the
        schema (no method can ever hash the scheme or the level)."""
        legacy = self._run(MULTI_ARM_PAYLOAD)
        holm = self._run(self._with(correction="holm"))

        def by_identity(rows):
            return {(r["metric"], r["method_config_id"], r["name_1"], r["name_2"]) for r in rows}

        assert by_identity(holm) == by_identity(legacy)
        for name in available_methods():
            spec_names = {spec.name for spec in get_method_class(name).param_specs}
            assert "correction" not in spec_names, name
            assert "alpha" not in spec_names, name

    def test_narrowing_the_family_moves_the_level_but_not_the_identity(self):
        """STAT-1b's two halves in one assertion: ``vs_control`` drops the
        treatment-vs-treatment pairs AND loosens the persisted alpha — while the
        surviving rows keep their series (the divisor is not part of a method's
        identity, so the operator's history stays readable)."""
        legacy = self._run(MULTI_ARM_PAYLOAD)
        narrowed = self._run(self._with(contrasts="vs_control"))

        def pairs(rows):
            return {(r["name_1"], r["name_2"]) for r in rows}

        assert pairs(legacy) == {
            ("control", "treatment"),
            ("control", "challenger"),
            ("treatment", "challenger"),
        }
        assert pairs(narrowed) == {("control", "treatment"), ("control", "challenger")}
        assert {r["method_config_id"] for r in narrowed} <= {r["method_config_id"] for r in legacy}

        # C(3,2)=3 contrasts vs g−1=2: every surviving row is tested LOOSER
        def main(rows):
            return next(r for r in rows if r["metric"] == "arpu" and r["name_2"] == "treatment")

        assert main(narrowed)["alpha"] > main(legacy)["alpha"]


class TestTheFamilyRuleLandsAtAlpha:
    """Leg 3 — Holm's claim, measured over the instrument's own composed sweep."""

    def test_holm_controls_the_family_at_alpha(self):
        from abkit.config.method_config import MethodConfig
        from abkit.validate.family import FamilyMember, sweep_family
        from tests.validate._panels import normal_panel

        alpha = 0.05
        members = [
            FamilyMember(
                metric=f"m{i}",
                panel=normal_panel(n_units=2000, n_cutoffs=1, seed=500 + i, mu=50.0, sigma=10.0),
                method=MethodConfig(name="t-test", params={"test_type": "absolute"}).bind(
                    alpha=alpha
                ),
                alpha=alpha,
                planted=False,
            )
            for i in range(4)
        ]
        holm = sweep_family(
            members, correction="holm", iterations=4000, share_a=0.5, seed_parts=("gate",)
        )
        # Binomial(4000, 0.05): σ ≈ 0.0034, so a ±3σ band is [0.040, 0.060].
        assert 0.038 < holm.fwer < 0.062, holm.fwer
        # …and the members' OWN per-comparison rule is never looser than the
        # family's, which is the one-directional divergence STAT-1 disclosed.
        uncorrected = sweep_family(
            members, correction="none", iterations=4000, share_a=0.5, seed_parts=("gate",)
        )
        assert holm.any_rejection_rate <= uncorrected.any_rejection_rate


class TestTheSignInstrumentSeesWhatTheRateCannot:
    """Leg 4 — STAT-2 × STAT-4, on one panel, in one assertion block."""

    def test_delta_leans_low_and_fieller_does_not_while_both_read_calibrated(self):
        """The whole reason STAT-2 shipped before STAT-4.

        The panel's control mean has a coefficient of variation of ~5%
        (σ/μ = 1 over 400 units per arm), which is the regime the derivation
        predicts a share of ``0.5 + φ(z)z²·CV₁·√w₁/α`` ≈ 0.66 for the delta
        interval — while its two-sided false-positive RATE stays at α. Every
        abkit verdict is a one-sided claim, so that lean IS the error; the FPR
        column is structurally blind to it, and the sign column is not.
        """
        from abkit.config.method_config import MethodConfig
        from abkit.validate.scoring import score_cell
        from tests.validate._panels import normal_panel

        alpha = 0.05
        panel = normal_panel(n_units=800, n_cutoffs=1, seed=4242, mu=50.0, sigma=50.0)

        def score(interval: str):
            method = MethodConfig(
                name="t-test", params={"test_type": "relative", "interval": interval}
            ).bind(alpha=alpha)
            return score_cell(panel, method, iterations=6000, seed_parts=("m13", interval))

        delta = score("delta")
        fieller = score("fieller")

        # (a) the two-sided column cannot tell them apart — both in budget
        assert 0.035 < delta.fpr < 0.070, delta.fpr
        assert 0.035 < fieller.fpr < 0.070, fieller.fpr

        # (b) the sign column can. σ(share) = √(0.25/hits); with ≥250 hits that
        # is ≈0.032, so the two bands below are ~4σ apart and cannot overlap by
        # noise. Asserted as bands, never as the derivation's point value.
        assert delta.valid_iterations == fieller.valid_iterations == 6000
        assert delta.fpr_negative_share is not None and fieller.fpr_negative_share is not None
        assert delta.fpr_negative_share > 0.58, delta.fpr_negative_share
        assert 0.40 < fieller.fpr_negative_share < 0.60, fieller.fpr_negative_share

    def test_the_proportion_interval_is_invisible_to_every_column_but_coverage(self):
        """STAT-3's half of the same story, and it is EXACT rather than banded.

        `Z(0)` is bit-identical to the pooled z (D11), so the two intervals
        exclude zero on exactly the same placebos: over ONE set of draws the
        FPR, the sign split and the power column agree to the last float, and
        only the relative-scale coverage moves. Pinning it as equality is the
        point — a "both are calibrated" band would have been satisfied by an
        interval that changed nothing at all, and by one that changed the
        rejection set too.
        """
        from abkit.config.method_config import MethodConfig
        from abkit.validate.scoring import score_cell
        from tests.validate._panels import fraction_panel

        panel = fraction_panel(n_units=4000, seed=90210, base_rate=0.08)

        def score(interval: str):
            method = MethodConfig(
                name="z-test", params={"test_type": "relative", "interval": interval}
            ).bind(alpha=0.05)
            # the SAME seed parts for both: the comparison is paired on purpose
            return score_cell(
                panel, method, iterations=3000, seed_parts=("m13", "prop"), inject_effect=0.25
            )

        pooled, score_interval = score("pooled"), score("score")
        assert pooled.fpr == score_interval.fpr
        assert pooled.fpr_negative_share == score_interval.fpr_negative_share
        assert pooled.power == score_interval.power
        # …and the one column that is not blind: the pooled relative interval
        # undercovers the injected lift, the score interval much less so
        assert score_interval.coverage > pooled.coverage + 0.015, (
            pooled.coverage,
            score_interval.coverage,
        )


class TestNoNumberMovedSilently:
    """Legs 5 and 6 — both derived from the code, never from a written list."""

    def test_no_method_bumped_its_algorithm_version(self):
        """D4: under opt-in an identity-flagged param already orphans the
        opting-in operator's series, so the version field — which exists for
        changing a DEFAULT — stays at 1 everywhere."""
        versions = {name: get_method_class(name).ALGORITHM_VERSION for name in available_methods()}
        assert set(versions.values()) == {1}, versions

    def test_every_interval_knob_defaults_to_the_legacy_branch(self):
        """Derived from the registry: a method that adopts one of M13's interval
        specs and defaults it to the NEW branch would move a number for a
        project that wrote nothing, which is the one thing `0.8.0` promises not
        to do."""
        legacy_default = {"interval": {"pooled", "delta"}}
        seen = 0
        for name in available_methods():
            for spec in get_method_class(name).param_specs:
                if spec.name in legacy_default:
                    assert spec.default in legacy_default[spec.name], (name, spec.default)
                    seen += 1
        assert seen == 6, f"expected z-test + the five mean methods, saw {seen}"

    def test_no_method_declares_an_asymmetric_ci_at_class_level(self):
        """The class default stays symmetric; asymmetry is a bound-instance
        property of the chosen param (STAT-3a, as amended by STAT-4). A class
        that flipped it would make every symmetric configuration of that method
        refuse the sequential transform."""
        for name in available_methods():
            assert get_method_class(name).asymmetric_ci is False, name

    def test_the_experiment_level_knobs_default_to_the_legacy_family(self):
        from abkit.config import ExperimentConfig, ProjectConfig

        project = ProjectConfig.model_validate({"name": "p", "default_profile": "dev"})
        assert project.statistics.correction == "bonferroni"
        assert project.statistics.guardrail_correction == "inherit"
        experiment = ExperimentConfig.model_validate(MULTI_ARM_PAYLOAD)
        assert experiment.contrasts == "all_pairs"

    def test_the_default_z_test_still_agrees_with_its_own_p_value(self):
        """The coherence STAT-3 preserved by construction, spot-checked on the
        boundary table where a Wald interval would have parted from the score
        p-value: CI-excludes-zero and p<α are one event under the default."""
        from abkit.stats.samples import Fraction

        method = create_method("z-test", alpha=0.05)
        result = method.from_samples(Fraction(count=500, nobs=10_000), Fraction(560, 10_000))
        excludes_zero = result.left_bound > 0 or result.right_bound < 0
        assert excludes_zero == (result.pvalue < 0.05)
