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

    def test_a_missing_treatment_pair_reads_untested_not_co_leaders(self):
        """No rows for (a, b): the leader is not shown to be better — but the
        reason is that NOBODY LOOKED, and `co_leaders` asserts the opposite
        ("we compared them and could not separate them").

        This test asserted `co_leaders` when DEC-2 was first written, which is
        how the defect shipped past its own suite. It is reachable without any
        config edit: in a multi-arm experiment the treatment pair holds the two
        smallest arms, so `insufficient_data` fires there while the control
        pairs are fine.
        """
        experiment = make_experiment()
        rows = (
            self._control_wins(experiment)
            + series(experiment, "a", "c", effect=-0.20, significant=True)
            + series(experiment, "b", "c", effect=-0.10, significant=True)
        )
        result = rollup(experiment, rows)
        assert result.leader == "a"
        assert result.indistinguishable == ("b",)
        assert result.separation == "untested"
        assert "could not be compared" in " | ".join(result.rationale)

    def test_a_demoted_treatment_pair_reads_untested_too(self):
        """Rows exist but the latest look is demoted — still nobody looked."""
        experiment = make_experiment()
        rows = self._control_wins(experiment)
        for name_1, name_2 in (("a", "b"), ("a", "c"), ("b", "c")):
            pair_rows = series(experiment, name_1, name_2, effect=-0.1, significant=True)
            for row in pair_rows:
                row["insufficient_data"] = True
            rows += pair_rows
        result = rollup(experiment, rows)
        assert result.leader == "a"
        assert result.separation == "untested"
        assert set(result.indistinguishable) == {"b", "c"}

    def test_a_pre_horizon_look_is_untested_not_co_leaders(self):
        """Under fixed CIs every verdict is withheld before the horizon, so an
        experiment would spend its whole life claiming measured non-separation."""
        experiment = make_experiment()
        rows = []
        for name_1, name_2 in (("control", "a"), ("control", "b"), ("control", "c")):
            rows += series(experiment, name_1, name_2, effect=0.3, significant=True)
        for name_1, name_2 in (("a", "b"), ("a", "c"), ("b", "c")):
            pair_rows = series(experiment, name_1, name_2, effect=-0.1, significant=True)
            for row in pair_rows:
                row["is_horizon"] = False
            rows += pair_rows
        result = rollup(experiment, rows)
        assert result.separation == "untested"

    def test_a_pair_decisive_AGAINST_the_leader_is_not_a_win(self):
        """THE orientation fixture. Every other decisive pair in this file
        favours the leader, so `== "WIN"`, `== "LOSE"` and
        `in ("WIN", "LOSE")` all agree — an orientation-BLIND implementation
        passed all 21 tests under the review's mutation probe.

        Here `a` leads on the control contrast but `b` is ahead of it
        head-to-head, so the pair `(a, b)` is a WIN *for b*. Reading the word
        without the orientation would call that "a beat b" and report
        `separated`.
        """
        experiment = make_experiment()
        rows = (
            self._control_wins(experiment)
            # (a, b) stored leader-FIRST: `b` ahead ⇒ WIN, which must NOT count
            # as the leader winning
            + series(experiment, "a", "b", effect=0.15, significant=True)
            + series(experiment, "a", "c", effect=-0.20, significant=True)
            + series(experiment, "b", "c", effect=-0.30, significant=True)
        )
        result = rollup(experiment, rows)
        assert result.leader == "a"
        assert result.indistinguishable == ("b",)
        assert result.separation == "co_leaders"

    def test_a_guardrail_between_two_treatments_does_not_move_separation(self):
        """The review's HIGH finding, pinned. `guardrail_policy: block` caps
        WIN and never LOSE, so leaving the cap on a treatment pair made the
        separation claim depend on the ARBITRARY declaration order of the arms:
        the same fact is a WIN stored one way and a LOSE stored the other.

        Both orderings below carry the identical guardrail regression on the
        identical pair, and must answer the same.
        """
        results = []
        for arms in (("control", "a", "b", "c"), ("control", "b", "c", "a")):
            experiment = make_experiment(
                arms=arms,
                comparisons=[
                    {"metric": "revenue", "is_main_metric": True, "method": {"name": "t-test"}},
                    {
                        "metric": "latency",
                        "is_guardrail": True,
                        "desired_direction": "decrease",
                        "method": {"name": "t-test"},
                    },
                ],
            )
            rows = (
                series(experiment, "control", "a", effect=0.30, significant=True)
                + series(experiment, "control", "b", effect=0.20, significant=True)
                + series(experiment, "control", "c", effect=0.10, significant=True)
            )
            for name_1, name_2 in experiment.contrast_pairs():
                if "control" in (name_1, name_2):
                    continue
                # `a` ahead of everyone, whichever way the pair is stored
                ahead = -0.1 if name_1 == "a" else 0.1
                rows += series(experiment, name_1, name_2, effect=ahead, significant=True)
            # a latency regression on the a/b treatment pair only
            g1, g2 = ("a", "b") if arms[1] == "a" else ("b", "a")
            rows += series(experiment, g1, g2, effect=0.5, significant=True, metric="latency")
            results.append(rollup(experiment, rows))

        assert {r.leader for r in results} == {"a"}
        assert {r.separation for r in results} == {"separated"}, [
            (r.separation, r.indistinguishable) for r in results
        ]

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


class TestTheRollupNeverSpeaksOverAGate:
    """m14 DEC-2 review: a summary must not state a finding where the truth is
    that nothing was measured. This is the DEC-1 `_srm_from_series` failure
    mode one level up — a broken assignment reading as an ordinary null."""

    def test_a_failed_srm_gate_is_named_instead_of_no_arm_beat_control(self):
        experiment = make_experiment()
        rows = []
        for arm in ("a", "b", "c"):
            arm_rows = series(experiment, "control", arm, effect=0.3, significant=True)
            for row in arm_rows:
                row["srm_flag"] = True
                row["decision_blocked"] = True
                row["srm_pvalue"] = 1e-9
            rows += arm_rows
        result = rollup(experiment, rows)
        assert result.leader is None
        text = " | ".join(result.rationale)
        assert "SRM failed" in text
        # the sentence that would contradict the per-pair cards above it
        assert "no arm beat control" not in text

    def test_a_pre_horizon_experiment_says_nothing_could_be_judged_yet(self):
        experiment = make_experiment()
        rows = []
        for arm in ("a", "b", "c"):
            arm_rows = series(experiment, "control", arm, effect=0.3, significant=True)
            for row in arm_rows:
                row["is_horizon"] = False
            rows += arm_rows
        result = rollup(experiment, rows)
        assert result.leader is None
        text = " | ".join(result.rationale)
        assert "could be judged" in text
        assert "no arm beat control" not in text

    def test_a_genuine_null_still_says_no_arm_beat_control(self):
        """The gate-aware wording must not swallow the ordinary case."""
        experiment = make_experiment()
        rows = []
        for arm in ("a", "b", "c"):
            rows += series(experiment, "control", arm, effect=0.01, significant=False)
        assert "no arm beat control" in " | ".join(rollup(experiment, rows).rationale)


class TestNoSelfContradiction:
    def test_an_arm_is_never_both_a_loser_and_a_co_leader(self):
        """Two fields of ONE payload disagreeing is the shape a renderer cannot
        paper over: the (control, b) card says LOSE while the rollup calls `b`
        an unresolved co-leader of the arm that beat control."""
        experiment = make_experiment()
        rows = (
            series(experiment, "control", "a", effect=0.30, significant=True)
            + series(experiment, "control", "b", effect=-0.40, significant=True)
            + series(experiment, "control", "c", effect=0.10, significant=True)
            + series(experiment, "a", "c", effect=-0.20, significant=True)
        )
        result = rollup(experiment, rows)
        assert result.leader == "a"
        assert result.losers == ("b",)
        assert "b" not in result.indistinguishable
        assert not set(result.losers) & set(result.indistinguishable)


class TestJudgedFlag:
    """The field that lets the rollup tell "we looked" from "nobody looked"
    without sniffing rationale strings (the STAT-1 prose-is-not-API rule)."""

    def test_a_decided_pair_is_judged(self):
        experiment = make_experiment()
        rows = series(experiment, "control", "a", effect=0.1, significant=True)
        verdict = [
            v
            for v in evaluate(experiment, rows).verdicts
            if (v.name_1, v.name_2) == ("control", "a")
        ][0]
        assert verdict.judged is True

    @pytest.mark.parametrize(
        "mutate,label",
        [
            (lambda row: row.update(is_horizon=False), "pre-horizon"),
            (lambda row: row.update(srm_flag=True, decision_blocked=True), "srm"),
            (lambda row: row.update(insufficient_data=True), "demoted"),
        ],
    )
    def test_a_gated_pair_is_not_judged(self, mutate, label):
        experiment = make_experiment()
        rows = series(experiment, "control", "a", effect=0.1, significant=True)
        for row in rows:
            mutate(row)
        verdict = [
            v
            for v in evaluate(experiment, rows).verdicts
            if (v.name_1, v.name_2) == ("control", "a")
        ][0]
        assert verdict.judged is False, label

    def test_a_pair_with_no_rows_is_not_judged(self):
        experiment = make_experiment()
        rows = series(experiment, "control", "a", effect=0.1, significant=True)
        verdict = [
            v for v in evaluate(experiment, rows).verdicts if (v.name_1, v.name_2) == ("a", "b")
        ][0]
        assert verdict.judged is False

    def test_quiet_but_underpowered_IS_judged(self):
        """The distinction is "did the rules run", not "was a call made"."""
        experiment = make_experiment()
        rows = series(experiment, "control", "a", effect=0.001, significant=False)
        verdict = [
            v
            for v in evaluate(experiment, rows).verdicts
            if (v.name_1, v.name_2) == ("control", "a")
        ][0]
        assert verdict.verdict == "INCONCLUSIVE"
        assert verdict.judged is True
