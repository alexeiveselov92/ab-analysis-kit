"""m14 DEC-2: the per-metric rollup — leader, separation, losers.

**Every fixture builds THREE or FOUR arms**, and that is not decoration. At two
arms there is exactly one treatment, so "the leader is better than every other
treatment" is vacuously true, `indistinguishable` is always empty and
`separation` is always `separated` — half of the rules below would pass against
an implementation that never looked at a treatment pair at all. It is the
STAT-1b lesson (its contrast-set fixtures are 3+ arms because at two arms
`C(2,2) == 1 == g-1`), applied to the decision layer.

The other discipline here: a rollup rule is only tested by a fixture where the
REJECTED alternative would give a different answer. "The leader beat the
runner-up" and "the leader beat every other treatment" agree at three arms
whenever the third arm is far behind — so the co-leader fixtures put the
undecided arm in the middle, where the two rules part company.
"""

from __future__ import annotations

import pytest

from abkit.config.experiment_config import ExperimentConfig
from abkit.pipeline.readout import evaluate
from tests.pipeline.test_readout import make_row

ARMS = ("control", "a", "b", "c")


def make_experiment(*, arms=ARMS, contrasts=None, desired="increase", **overrides):
    config = {
        "name": "rollup_exp",
        "start_ts": "2026-01-01",
        "horizon_ts": "2026-01-15",
        "unit_key": "user_id",
        "assignment": {
            "query": "SELECT 1",
            "variants": list(arms),
            "expected_split": {arm: 1 / len(arms) for arm in arms},
        },
        "alpha": 0.05,
        "correction": "none",
        "comparisons": [
            {
                "metric": "revenue",
                "is_main_metric": True,
                "desired_direction": desired,
                "method": {"name": "t-test"},
            }
        ],
    }
    if contrasts is not None:
        config["contrasts"] = contrasts
    config.update(overrides)
    return ExperimentConfig.model_validate(config)


def series(experiment, name_1, name_2, *, effect, significant, metric="revenue"):
    """A full 14-day series for one pair, decisive or quiet as asked.

    A decisive series needs a CI excluding zero on the SAME side as `effect`;
    a quiet one straddles zero and is wide enough that FLAT stays unreachable
    (no `min_effect` is declared), so the verdict is INCONCLUSIVE rather than
    FLAT — the state a real "we cannot separate these" pair is in.
    """
    if significant:
        bounds = (effect * 0.5, effect * 1.5) if effect > 0 else (effect * 1.5, effect * 0.5)
        pvalue, reject = 0.001, True
    else:
        span = max(abs(effect), 0.01) * 4
        bounds = (effect - span, effect + span)
        pvalue, reject = 0.8, False
    return [
        make_row(
            experiment,
            metric=metric,
            day=day,
            name_1=name_1,
            name_2=name_2,
            effect=effect,
            left_bound=bounds[0],
            right_bound=bounds[1],
            pvalue=pvalue,
            reject=reject,
        )
        for day in range(1, 15)
    ]


def rollup(experiment, rows, metric="revenue"):
    readout = evaluate(experiment, rows)
    found = [r for r in readout.rollups if r.metric == metric]
    assert len(found) == 1, [r.metric for r in readout.rollups]
    return found[0]


class TestLeaderSelection:
    def test_the_leader_is_the_best_effect_AMONG_WINNERS(self):
        """D6. `b` has the largest effect of all, but it did not beat control —
        so the leader is `a`, the best arm that actually won. Ranking on effect
        alone (the rejected alternative) would name `b`, which is the
        uncontrolled claim D1 refused."""
        experiment = make_experiment()
        rows = (
            series(experiment, "control", "a", effect=0.10, significant=True)
            + series(experiment, "control", "b", effect=0.30, significant=False)
            + series(experiment, "control", "c", effect=0.02, significant=True)
        )
        assert rollup(experiment, rows).leader == "a"

    def test_no_winner_names_nobody(self):
        experiment = make_experiment()
        rows = (
            series(experiment, "control", "a", effect=0.10, significant=False)
            + series(experiment, "control", "b", effect=0.30, significant=False)
            + series(experiment, "control", "c", effect=0.02, significant=False)
        )
        result = rollup(experiment, rows)
        assert result.leader is None
        assert result.separation == "no_leader"
        # the state most experiments are in must not read as "the leader beat
        # everyone" — which is what an empty `indistinguishable` would say
        assert result.indistinguishable == ()
        assert "no arm beat control" in " | ".join(result.rationale)

    def test_desired_direction_decrease_picks_the_most_negative_winner(self):
        """`max(effect)` would name the arm that moved the metric the WRONG way
        the least."""
        experiment = make_experiment(desired="decrease")
        rows = (
            series(experiment, "control", "a", effect=-0.05, significant=True)
            + series(experiment, "control", "b", effect=-0.20, significant=True)
            + series(experiment, "control", "c", effect=-0.01, significant=True)
        )
        assert rollup(experiment, rows).leader == "b"

    def test_a_tie_breaks_on_declaration_order(self):
        experiment = make_experiment()
        rows = (
            series(experiment, "control", "a", effect=0.10, significant=True)
            + series(experiment, "control", "b", effect=0.10, significant=True)
            + series(experiment, "control", "c", effect=0.01, significant=True)
        )
        assert rollup(experiment, rows).leader == "a"


class TestSeparation:
    """D5: separation is tested against EVERY other treatment, not the runner-up."""

    @staticmethod
    def _control_wins(experiment):
        return (
            series(experiment, "control", "a", effect=0.30, significant=True)
            + series(experiment, "control", "b", effect=0.20, significant=True)
            + series(experiment, "control", "c", effect=0.10, significant=True)
        )

    def test_beating_every_other_treatment_is_separated(self):
        experiment = make_experiment()
        rows = (
            self._control_wins(experiment)
            + series(experiment, "a", "b", effect=-0.10, significant=True)
            + series(experiment, "a", "c", effect=-0.20, significant=True)
            + series(experiment, "b", "c", effect=-0.10, significant=True)
        )
        result = rollup(experiment, rows)
        assert result.leader == "a"
        assert result.separation == "separated"
        assert result.indistinguishable == ()

    def test_the_runner_up_rule_and_the_every_arm_rule_part_company(self):
        """THE discriminating fixture. `a` leads and decisively beats the
        runner-up `b`, but NOT the third arm `c`. "Beat the runner-up" would
        report a clean winner; D5's rule reports co-leaders and names `c`.

        This is the shape D5 calls out: the runner-up formulation leaves K-2
        comparisons unexamined while sounding conclusive.
        """
        experiment = make_experiment()
        rows = (
            self._control_wins(experiment)
            + series(experiment, "a", "b", effect=-0.10, significant=True)
            + series(experiment, "a", "c", effect=-0.02, significant=False)
            + series(experiment, "b", "c", effect=-0.10, significant=False)
        )
        result = rollup(experiment, rows)
        assert result.leader == "a"
        assert result.separation == "co_leaders"
        assert result.indistinguishable == ("c",)

    def test_orientation_decides_which_word_means_what(self):
        """Rule 3. The same fact — the leader is ahead — is a WIN when the pair
        is stored ``(other, leader)`` and a LOSE when it is stored
        ``(leader, other)``. Reading the word without the orientation inverts
        the answer on exactly half the pairs.

        `contrast_pairs()` emits treatment pairs in declaration order, so with
        the leader declared LAST every one of its pairs is ``(other, leader)``.
        """
        experiment = make_experiment(arms=("control", "a", "b", "c"))
        rows = (
            series(experiment, "control", "a", effect=0.10, significant=True)
            + series(experiment, "control", "b", effect=0.20, significant=True)
            + series(experiment, "control", "c", effect=0.30, significant=True)
            # `c` leads; both of its pairs are stored with `c` SECOND, so the
            # readout's word for "c is ahead" is WIN
            + series(experiment, "a", "b", effect=0.10, significant=True)
            + series(experiment, "a", "c", effect=0.20, significant=True)
            + series(experiment, "b", "c", effect=0.10, significant=True)
        )
        result = rollup(experiment, rows)
        assert result.leader == "c"
        assert result.separation == "separated"

    def test_a_missing_treatment_pair_is_not_a_win(self):
        """No rows for (a, b) — the leader is not shown to be better, so it is
        undecided, never assumed."""
        experiment = make_experiment()
        rows = (
            self._control_wins(experiment)
            + series(experiment, "a", "c", effect=-0.20, significant=True)
            + series(experiment, "b", "c", effect=-0.10, significant=True)
        )
        result = rollup(experiment, rows)
        assert result.leader == "a"
        assert result.indistinguishable == ("b",)
        assert result.separation == "co_leaders"

    def test_vs_control_reports_untested_not_separated(self):
        """The knob stays load-bearing: under `contrasts: vs_control` the
        treatment pairs were never computed, so nothing can be claimed about
        them — and claiming `separated` off an empty set would be the loudest
        possible lie."""
        experiment = make_experiment(contrasts="vs_control")
        rows = self._control_wins(experiment)
        result = rollup(experiment, rows)
        assert result.leader == "a"
        assert result.separation == "untested"
        assert result.indistinguishable == ()
        assert "vs_control" in " | ".join(result.rationale)
        # and no treatment-pair verdict exists to render
        assert not [v for v in evaluate(experiment, rows).verdicts if v.role == "treatment_pair"]


class TestLosersAndGuardrails:
    def test_losers_are_the_arms_that_lost_to_control(self):
        experiment = make_experiment()
        rows = (
            series(experiment, "control", "a", effect=0.10, significant=True)
            + series(experiment, "control", "b", effect=-0.20, significant=True)
            + series(experiment, "control", "c", effect=-0.10, significant=True)
        )
        result = rollup(experiment, rows)
        assert result.leader == "a"
        assert result.losers == ("b", "c")

    def test_guardrail_regression_is_reported_against_the_control(self):
        experiment = make_experiment(
            comparisons=[
                {
                    "metric": "revenue",
                    "is_main_metric": True,
                    "method": {"name": "t-test"},
                },
                {
                    "metric": "latency",
                    "is_guardrail": True,
                    "desired_direction": "decrease",
                    "method": {"name": "t-test"},
                },
            ],
            readout={"guardrail_policy": "warn"},
        )
        rows = (
            series(experiment, "control", "a", effect=0.10, significant=True)
            + series(experiment, "control", "b", effect=0.05, significant=True)
            + series(experiment, "control", "c", effect=0.01, significant=True)
            # latency UP is harm on a `decrease` guardrail — only for `b`
            + series(experiment, "control", "b", effect=0.5, significant=True, metric="latency")
        )
        result = rollup(experiment, rows)
        assert result.guardrail_regressed == ("b",)
        assert "guardrail regression" in " | ".join(result.caveats)


class TestMultipleMainMetrics:
    """D2: one rollup per main metric; `leaders_agree` reports, never picks."""

    @staticmethod
    def _two_main():
        return make_experiment(
            comparisons=[
                {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                {"metric": "signups", "is_main_metric": True, "method": {"name": "t-test"}},
            ]
        )

    def _rows(self, experiment, revenue_leader, signups_leader):
        rows = []
        for metric, winner in (("revenue", revenue_leader), ("signups", signups_leader)):
            for arm in ("a", "b", "c"):
                rows += series(
                    experiment,
                    "control",
                    arm,
                    effect=0.30 if arm == winner else 0.05,
                    significant=arm == winner,
                    metric=metric,
                )
        return rows

    def test_one_rollup_per_main_metric_in_config_order(self):
        experiment = self._two_main()
        readout = evaluate(experiment, self._rows(experiment, "a", "a"))
        assert [r.metric for r in readout.rollups] == ["revenue", "signups"]

    def test_agreeing_leaders_report_true(self):
        experiment = self._two_main()
        readout = evaluate(experiment, self._rows(experiment, "a", "a"))
        assert readout.leaders_agree is True

    def test_disagreeing_leaders_report_false_and_nothing_is_picked(self):
        experiment = self._two_main()
        readout = evaluate(experiment, self._rows(experiment, "a", "b"))
        assert readout.leaders_agree is False
        assert {r.metric: r.leader for r in readout.rollups} == {"revenue": "a", "signups": "b"}

    def test_fewer_than_two_opinions_is_None(self):
        """`None` means "nothing to agree about", and it must cover the metric
        that simply has no winner — otherwise the most ordinary experiment in
        the world raises a disagreement chip."""
        experiment = self._two_main()
        rows = []
        for arm in ("a", "b", "c"):
            rows += series(experiment, "control", arm, effect=0.3, significant=arm == "a")
            rows += series(
                experiment, "control", arm, effect=0.05, significant=False, metric="signups"
            )
        readout = evaluate(experiment, rows)
        assert {r.metric: r.leader for r in readout.rollups} == {"revenue": "a", "signups": None}
        assert readout.leaders_agree is None


class TestTwoArmShape:
    """§0.2 point 3 + rule 4: the payload shape is uniform, and nothing moves."""

    @staticmethod
    def _two_arm():
        return make_experiment(arms=("control", "treatment"))

    def test_a_two_arm_experiment_still_gets_a_rollup(self):
        experiment = self._two_arm()
        rows = series(experiment, "control", "treatment", effect=0.1, significant=True)
        result = rollup(experiment, rows)
        assert result.leader == "treatment"
        # with one treatment there is no other arm to be undecided about
        assert result.indistinguishable == ()
        assert result.separation == "separated"

    def test_a_two_arm_experiment_has_no_treatment_pair_verdict(self):
        """The byte-identity claim: nothing about a two-arm readout's verdict
        list changed."""
        experiment = self._two_arm()
        rows = series(experiment, "control", "treatment", effect=0.1, significant=True)
        readout = evaluate(experiment, rows)
        assert len(readout.verdicts) == 1
        assert readout.verdicts[0].role == "vs_control"
        assert readout.leaders_agree is None


class TestRollupRecomputesNothing:
    def test_every_rollup_input_is_a_verdict_this_readout_issued(self):
        """The milestone invariant in miniature: one decision, many surfaces.

        The leader, the losers and the separation must all be derivable from
        the verdict list alone — if the rollup ever read a ROW the surfaces
        could disagree with it about the same experiment.
        """
        experiment = make_experiment()
        rows = (
            series(experiment, "control", "a", effect=0.30, significant=True)
            + series(experiment, "control", "b", effect=0.20, significant=True)
            + series(experiment, "control", "c", effect=-0.10, significant=True)
            + series(experiment, "a", "b", effect=-0.10, significant=True)
            + series(experiment, "a", "c", effect=-0.40, significant=True)
            + series(experiment, "b", "c", effect=-0.30, significant=True)
        )
        readout = evaluate(experiment, rows)
        result = readout.rollups[0]
        by_pair = {(v.name_1, v.name_2): v for v in readout.verdicts}

        assert by_pair[("control", result.leader)].verdict == "WIN"
        for loser in result.losers:
            assert by_pair[("control", loser)].verdict == "LOSE"
        # `a` is second in ("a", "b") and ("a", "c"), so "a is ahead" is LOSE
        for other in ("b", "c"):
            assert by_pair[(result.leader, other)].verdict == "LOSE"
        assert result.separation == "separated"


class TestPairRole:
    def test_role_is_a_field_not_an_inference(self):
        """A renderer must not have to test `name_1 == control` — and with a
        DECLARED control that test is the one DEC-1 spent a whole WP removing."""
        experiment = make_experiment(
            arms=("a", "b", "c"),
            assignment={
                "query": "SELECT 1",
                "variants": ["a", "b", "c"],
                "control": "c",
                "expected_split": {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3},
            },
        )
        rows = (
            series(experiment, "c", "a", effect=0.1, significant=True)
            + series(experiment, "c", "b", effect=0.2, significant=True)
            + series(experiment, "a", "b", effect=0.1, significant=True)
        )
        readout = evaluate(experiment, rows)
        roles = {(v.name_1, v.name_2): v.role for v in readout.verdicts}
        assert roles == {
            ("c", "a"): "vs_control",
            ("c", "b"): "vs_control",
            ("a", "b"): "treatment_pair",
        }

    @pytest.mark.parametrize("role", ["vs_control", "treatment_pair"])
    def test_role_survives_to_dict(self, role):
        experiment = make_experiment()
        rows = series(experiment, "control", "a", effect=0.1, significant=True) + series(
            experiment, "a", "b", effect=0.1, significant=True
        )
        readout = evaluate(experiment, rows)
        payload = readout.to_dict()
        assert any(v["role"] == role for v in payload["verdicts"])
        assert payload["rollups"][0]["metric"] == "revenue"
        assert "leaders_agree" in payload
