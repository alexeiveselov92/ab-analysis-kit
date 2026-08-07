"""The M14 exit gate: the multi-arm decision layer moves nothing it did not add
(m14-implementation-plan.md §4 / DEC-6).

M13 was the milestone whose numbers moved under opt-in. M14 restores the
M7–M12 posture in the stronger form an interpretation layer allows: **no
persisted number, no alpha and no verdict `0.8.0` already issues moves** — the
milestone only adds verdicts for pairs that had none, a rollup over them, and
the surfaces that render both. There is exactly ONE deliberate exception
(DEC-5: multi-arm `_ab_aa_runs` power / achieved-MDE / coverage, which were
measuring a design nobody runs) and one deliberate multi-arm surface change
(DEC-4: the dashboard headline stops being an arbitrary arm). Both are asserted
here **as moves, with their direction**, because a gate that merely tolerated
them could not tell either from a regression.

The five legs are the plan's DEC-6, in order, with the three corrections the
handoff brief requires:

1. **Two-arm byte-compatibility against a real `v0.8.0` checkout** — the STAT-6
   discipline, via ``_m14_baseline.py`` (which documents how to regenerate).
   The claim is the DEC-3 **amended** form, not the plan's literal one: for a
   rendering surface "byte-identical" is almost always false — the payload
   legitimately gains keys and the report's stylesheet grows — so what is
   asserted is that **every `0.8.0` field reproduces and the only difference is
   an enumerated set of ADDED keys**. Five surfaces, not four: the persisted
   rows, the report payload, the dashboard row, the notification contexts, the
   explore payload, plus the real CLI ``Report →`` line and the two-arm
   ``_ab_aa_runs`` row (the instruments make it seven).
2. **Control-anchored verdicts unchanged at four arms**, field for field with
   ``rationale`` and ``caveats`` included, while the three treatment pairs
   appear beside them.
3. **Rollup correctness on constructed data**: a clean leader, two arms that
   cannot be separated, no winner at all, and per-metric leaders that disagree.
4. **Every surface names the same leader and the same separation** over
   identical rows — the report payload, the dashboard row, the notification
   context, the explore payload and the CLI's verdict note.
5. **``contrasts: vs_control`` ⇒ ``separation: untested``**, with the knob named
   as the reason.

What this file does NOT reach, stated rather than implied: the rendered **DOM**
(``web/test/smoke*.mjs`` owns it — every DEC-3/DEC-4 affordance is gated on
``isMultiArm``/``arm_count`` there, and a two-arm page renders the `0.8.0` DOM
character for character), the notification TRANSPORTS (nine channels, pinned in
``tests/notify/test_channels.py`` — this gate compares ``build_context()``,
which is what a message *says*), and DEC-1's re-orientation of a declared
non-first control, which has no `0.8.0` counterpart to compare against
(``assignment.control`` is an unknown key there) and is pinned by
``tests/config/test_declared_control.py`` plus the AST gate.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from _m14_baseline import (
    FOUR_ARMS,
    TWO_ARMS,
    capture_aa_surface,
    capture_pipeline_surface,
    capture_scaffold_surface,
)

from abkit.cli.commands.run import _verdict_note
from abkit.pipeline.readout import evaluate

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "decisions_golden_0_8_0.json"

REL = 1e-9

#: Columns whose payload is JSON: compared PARSED, so a last-ULP difference in
#: an embedded float is judged by the same rel-1e-9 rule as a top-level one, and
#: key order cannot make an equal payload unequal (the M9 lesson).
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
        "details",
    }
)

#: Every key M14 adds to a `0.8.0` surface, as a normalised path (list indices
#: collapse to ``[]``). This set IS the milestone's claim about what changed on a
#: two-arm experiment — nothing else may differ, and a key missing from it fails
#: the gate rather than being tolerated. Kept flat and literal on purpose: it is
#: read as documentation.
ADDED_TWO_ARM = frozenset(
    {
        # DEC-1: the resolved baseline, in the catalog for BI
        "_ab_experiments[].control",
        # DEC-2: the decision layer on the readout
        "readout.rollups",
        "readout.leaders_agree",
        "readout.verdicts[].role",
        "readout.verdicts[].judged",
        # DEC-3: the report payload
        "report.control",
        "report.rollups",
        "report.leaders_agree",
        "report.verdicts[].role",
        # DEC-5(c): the SRM culprit block
        "report.srm.culprit",
        # DEC-4: the dashboard row and the notification context
        "dashboard_row.leader",
        "dashboard_row.separation",
        "dashboard_row.rollups",
        "dashboard_row.leaders_agree",
        "dashboard_row.verdicts[].role",
        "notify[].rollup_display",
        "notify[].rollup_line",
    }
)


# ─────────────────────────────────────────────────────────────── the comparator


def _normalise(path: str) -> str:
    """``a.verdicts[3].role`` → ``a.verdicts[].role`` — the ADDED-set grain.

    An added key is a property of the SHAPE, not of one list position: keying by
    index would make the set depend on how many verdicts a fixture happens to
    have, and a two-verdict fixture would then silently stop covering the
    second.
    """
    out: list[str] = []
    depth = 0
    for char in path:
        if char == "[":
            depth += 1
            out.append(char)
        elif char == "]":
            depth -= 1
            out.append(char)
        elif depth == 0:
            out.append(char)
    return "".join(out)


def assert_reproduces(actual, expected, where: str, *, added=frozenset(), diffs=None) -> None:
    """*expected* (the `0.8.0` capture) reproduces inside *actual*, key by key.

    Recursive over dicts and lists. A key present in *actual* and absent from
    *expected* is allowed only when its normalised path is in *added*; a key
    that disappeared is always a failure. Numbers compare at rel-1e-9 rather
    than by identity, because byte reproducibility of a float aggregate holds
    only under a fixed BLAS configuration (M7 D13) and CI is not the machine the
    golden was captured on.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: {type(actual).__name__} is not a dict"
        for key in sorted(set(actual) - set(expected)):
            path = _normalise(f"{where}.{key}")
            assert path in added, f"{where}: unexpected new key {key!r} (path {path})"
            if diffs is not None:
                diffs.add(path)
        for key in sorted(set(expected) - set(actual)):
            raise AssertionError(f"{where}: key {key!r} disappeared")
        for key in expected:
            assert_reproduces(
                actual[key], expected[key], f"{where}.{key}", added=added, diffs=diffs
            )
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{where}: {type(actual).__name__} is not a list"
        assert len(actual) == len(expected), f"{where}: {len(actual)} items vs {len(expected)}"
        for index, item in enumerate(expected):
            assert_reproduces(actual[index], item, f"{where}[{index}]", added=added, diffs=diffs)
    elif isinstance(expected, bool) or isinstance(actual, bool):
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"
    elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if isinstance(expected, float) and math.isnan(expected):
            assert isinstance(actual, float) and math.isnan(actual), where
        else:
            assert math.isclose(
                actual, expected, rel_tol=REL, abs_tol=1e-12
            ), f"{where}: {actual!r} != {expected!r} (rel={REL})"
    elif (
        isinstance(expected, str)
        and isinstance(actual, str)
        and where.rsplit(".", 1)[-1] in JSON_COLUMNS
    ):
        assert_reproduces(
            json.loads(actual), json.loads(expected), f"{where}(json)", added=added, diffs=diffs
        )
    else:
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"


# ───────────────────────────────────────────────────────────────────── fixtures


@pytest.fixture(scope="module")
def golden() -> dict:
    surface = json.loads(GOLDEN_PATH.read_text())
    # Provenance, asserted rather than trusted: a golden regenerated from HEAD
    # would make this gate compare HEAD with itself (the M10 window-golden
    # lesson). `0.8.0` is the release M14 must reproduce.
    assert surface["abkit_version"] == "0.8.0", surface["abkit_version"]
    return surface


@pytest.fixture(scope="module")
def two_arm() -> dict:
    return capture_pipeline_surface(TWO_ARMS)


@pytest.fixture(scope="module")
def four_arm() -> dict:
    return capture_pipeline_surface(FOUR_ARMS)


@pytest.fixture(scope="module")
def vs_control() -> dict:
    """The same four arms with the family narrowed — DEC-6 leg 5."""
    return capture_pipeline_surface(FOUR_ARMS, contrasts="vs_control")


# ───────────────────────────────────────────────── leg 1: two-arm compatibility


class TestTwoArmByteCompatibility:
    """The milestone's №1 assertion, against the released code (§0.2 point 3)."""

    @pytest.mark.parametrize(
        "surface",
        ["_ab_results", "_ab_experiments", "readout", "report", "dashboard_row", "notify"],
    )
    def test_every_two_arm_surface_reproduces_0_8_0(self, two_arm, golden, surface):
        assert_reproduces(
            two_arm[surface], golden["two_arm"][surface], surface, added=ADDED_TWO_ARM
        )

    def test_the_explore_payload_reproduces_0_8_0_exactly(self, two_arm, golden):
        """The cockpit block gains NOTHING at two arms — the report payload rides
        into it verbatim (DEC-4 released DEC-3's hold), so every M14 key it
        carries lives under the keys already covered above. Asserted with an
        EMPTY ``added`` set, which is what makes it a statement rather than a
        repetition."""
        assert_reproduces(two_arm["explore"], golden["two_arm"]["explore"], "explore")

    def test_every_declared_addition_is_actually_present(self, two_arm, golden):
        """The other direction, and the one a permissive comparator forgets: an
        ``added`` path nothing produces would make :data:`ADDED_TWO_ARM` a wish
        list. DEC-3's own review found a "hold" that had stopped being called at
        all and left fifteen tests green — an allowance nobody exercises is the
        same defect one level up.
        """
        seen: set[str] = set()
        for surface in ("_ab_experiments", "readout", "report", "dashboard_row", "notify"):
            assert_reproduces(
                two_arm[surface],
                golden["two_arm"][surface],
                surface,
                added=ADDED_TWO_ARM,
                diffs=seen,
            )
        assert seen == ADDED_TWO_ARM, ADDED_TWO_ARM - seen

    def test_a_two_arm_message_gains_no_new_prose(self, two_arm):
        """The one claim the comparator above structurally CANNOT make.

        ``rollup_display``/``rollup_line`` are ADDED keys, so
        :func:`assert_reproduces` never looks at their values — an added key with
        a non-empty string would sail through it while changing the body of every
        message. DEC-4 gates the line on the ARM COUNT precisely because "leader:
        treatment" only restates the verdict word beside it at two arms, and that
        gate needs its own assertion. Its non-empty four-arm twin is leg 4.
        """
        for context in two_arm["notify"]:
            assert context["rollup_display"] == "", context
            assert context["rollup_line"] == "", context

    def test_the_cli_report_line_is_unchanged(self, golden):
        """``abk init && abk run --report`` through the real CLI. DEC-4 rewrote
        this line's verdict note at 3+ arms; at two arms it must still be the
        bare verdict words, and only the CLI can prove that — ``_verdict_note``
        is a function, the LINE is the surface."""
        scaffold = capture_scaffold_surface()
        assert scaffold["report_lines"] == golden["scaffold"]["report_lines"]
        assert_reproduces(
            scaffold["_ab_results"], golden["scaffold"]["_ab_results"], "scaffold/_ab_results"
        )
        assert_reproduces(
            scaffold["_ab_experiments"],
            golden["scaffold"]["_ab_experiments"],
            "scaffold/_ab_experiments",
            added={"scaffold/_ab_experiments[].control"},
        )

    def test_the_two_arm_aa_row_reproduces_0_8_0(self, golden):
        """DEC-5's boundary. The placebo is now the calibrated CONTRAST rather
        than the pooled cohort — and with two arms the contrast IS the cohort, so
        every column, the verdict STRING included, must be `0.8.0`'s. The
        disclosure suffix is deliberately silent here: ``_ab_aa_runs.verdict`` is
        persisted, so leaking it at two arms would move a stored string."""
        assert_reproduces(capture_aa_surface(TWO_ARMS), golden["aa"]["two_arm"], "aa/two_arm")


# ─────────────────────────────────── leg 2: the verdicts that already existed


class TestControlAnchoredVerdictsAreUnchangedAtFourArms:
    """§0.2's structural claim, measured: verdicting a row that is already in the
    read-time family cannot move a threshold."""

    def test_the_persisted_rows_are_identical(self, four_arm, golden):
        """No number, no alpha and no ``reject`` flag moved — 252 rows over six
        arm pairs × three comparisons × fourteen looks."""
        assert_reproduces(
            four_arm["_ab_results"], golden["four_arm"]["_ab_results"], "four_arm/_ab_results"
        )

    def test_every_0_8_0_verdict_reproduces_field_for_field(self, four_arm, golden):
        by_pair = {
            (v["metric"], v["name_1"], v["name_2"]): v for v in four_arm["readout"]["verdicts"]
        }
        for want in golden["four_arm"]["readout"]["verdicts"]:
            key = (want["metric"], want["name_1"], want["name_2"])
            assert key in by_pair, f"{key} lost its verdict"
            assert_reproduces(
                by_pair[key],
                want,
                f"verdict{key}",
                added={f"verdict{key}.role", f"verdict{key}.judged"},
            )

    def test_the_new_verdicts_are_exactly_the_treatment_pairs(self, four_arm, golden):
        """And every one of them is LABELLED as evidence rather than a ship
        decision — the `role` field DEC-2 added because three renderers need the
        distinction and none of them may re-infer it from ``name_1 == control``.
        """
        was = {
            (v["metric"], v["name_1"], v["name_2"])
            for v in golden["four_arm"]["readout"]["verdicts"]
        }
        now = {
            (v["metric"], v["name_1"], v["name_2"]): v["role"]
            for v in four_arm["readout"]["verdicts"]
        }
        assert set(now) - was == {(metric, "b", "c") for metric in ("arpu", "conversion")} | {
            (metric, name_1, name_2)
            for metric in ("arpu", "conversion")
            for name_1, name_2 in (("b", "d"), ("c", "d"))
        }
        assert all(now[key] == "vs_control" for key in was)
        assert all(now[key] == "treatment_pair" for key in set(now) - was)

    def test_the_alphas_did_not_move(self, four_arm, golden):
        """The divisor is derived from the arm count and the declared family, not
        from an enumeration (§0.2 point 2) — so a milestone that adds verdicts
        cannot move a level. Read off the persisted rows, per pair."""

        def alphas(rows):
            return {(r["metric"], r["name_1"], r["name_2"]): r["alpha"] for r in rows}

        assert alphas(four_arm["_ab_results"]) == alphas(golden["four_arm"]["_ab_results"])


# ───────────────────────────────────────── leg 3: the rollup on constructed data


ROLLUP_ARMS = ("control", "b", "c", "d")


def _rollup_experiment(*, arms=ROLLUP_ARMS, contrasts=None, metrics=("revenue",)):
    from abkit.config.experiment_config import ExperimentConfig

    config = {
        "name": "m14_rollup",
        "start_ts": "2026-01-01",
        "horizon_ts": "2026-01-15",
        "unit_key": "user_id",
        "alpha": 0.05,
        "correction": "none",
        "assignment": {
            "query": "SELECT 1",
            "variants": list(arms),
            "expected_split": {arm: 1 / len(arms) for arm in arms},
        },
        "comparisons": [
            {"metric": metric, "is_main_metric": True, "method": {"name": "t-test"}}
            for metric in metrics
        ],
    }
    if contrasts is not None:
        config["contrasts"] = contrasts
    return ExperimentConfig.model_validate(config)


def _series(experiment, name_1, name_2, *, effect, decisive, metric="revenue"):
    """A full 14-look series for one pair — decisive or straddling zero.

    Same shape helper the DEC-2 unit suite uses (``tests.pipeline.test_readout``
    owns the row contract, so a schema change cannot leave this file behind).
    """
    from tests.pipeline.test_readout import make_row

    if decisive:
        bounds = (effect * 0.5, effect * 1.5) if effect > 0 else (effect * 1.5, effect * 0.5)
        pvalue, reject = 0.001, True
    else:
        span = max(abs(effect), 0.01) * 4
        bounds, pvalue, reject = (effect - span, effect + span), 0.8, False
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


def _rollup(experiment, rows, metric="revenue"):
    readout = evaluate(experiment, rows)
    return next(r for r in readout.rollups if r.metric == metric)


class TestTheRollupOnConstructedData:
    """Leg 3 — the four states, each on data where the rejected alternative
    would answer differently. Every fixture has FOUR arms: at two, "the leader
    beat every other treatment" is vacuous and half of these would pass against
    an implementation that never read a treatment pair (the STAT-1b lesson)."""

    def test_a_clean_leader_is_separated(self):
        experiment = _rollup_experiment()
        rows = [
            *_series(experiment, "control", "b", effect=0.30, decisive=True),
            *_series(experiment, "control", "c", effect=0.10, decisive=True),
            *_series(experiment, "control", "d", effect=0.00, decisive=False),
            *_series(experiment, "b", "c", effect=-0.15, decisive=True),
            *_series(experiment, "b", "d", effect=-0.28, decisive=True),
            *_series(experiment, "c", "d", effect=-0.09, decisive=True),
        ]
        rollup = _rollup(experiment, rows)
        assert (rollup.leader, rollup.separation) == ("b", "separated")
        assert rollup.indistinguishable == ()

    def test_an_undecided_middle_arm_makes_co_leaders(self):
        """The one shape that separates "beat the runner-up" from "beat every
        other treatment": ``c`` is undecided against the leader while ``d`` —
        further behind — is decisively beaten."""
        experiment = _rollup_experiment()
        rows = [
            *_series(experiment, "control", "b", effect=0.30, decisive=True),
            *_series(experiment, "control", "c", effect=0.26, decisive=True),
            *_series(experiment, "control", "d", effect=0.05, decisive=True),
            *_series(experiment, "b", "c", effect=-0.03, decisive=False),
            *_series(experiment, "b", "d", effect=-0.24, decisive=True),
            *_series(experiment, "c", "d", effect=-0.20, decisive=True),
        ]
        rollup = _rollup(experiment, rows)
        assert (rollup.leader, rollup.separation) == ("b", "co_leaders")
        assert rollup.indistinguishable == ("c",)
        assert "co-leaders" in " ".join(rollup.rationale)

    def test_nobody_winning_is_no_leader_not_separated(self):
        """The COMMONEST outcome in the world, and the state the design's
        three-value table did not have: with no winner ``indistinguishable`` is
        empty, which would read as ``separated``."""
        experiment = _rollup_experiment()
        rows = [
            row
            for arm in ("b", "c", "d")
            for row in _series(experiment, "control", arm, effect=0.01, decisive=False)
        ]
        rollup = _rollup(experiment, rows)
        assert (rollup.leader, rollup.separation) == (None, "no_leader")
        assert rollup.indistinguishable == ()

    def test_leaders_can_disagree_across_main_metrics(self):
        """D2: no cross-metric pick — the experiment-level statement is whether
        the per-metric leaders coincide, and here they do not."""
        experiment = _rollup_experiment(metrics=("revenue", "orders"))
        rows = [
            *_series(experiment, "control", "b", effect=0.30, decisive=True),
            *_series(experiment, "control", "c", effect=0.10, decisive=True),
            *_series(experiment, "control", "d", effect=0.00, decisive=False),
            *_series(experiment, "b", "c", effect=-0.15, decisive=True),
            *_series(experiment, "b", "d", effect=-0.28, decisive=True),
            *_series(experiment, "c", "d", effect=-0.09, decisive=True),
            *_series(experiment, "control", "b", effect=0.05, decisive=True, metric="orders"),
            *_series(experiment, "control", "c", effect=0.40, decisive=True, metric="orders"),
            *_series(experiment, "control", "d", effect=0.00, decisive=False, metric="orders"),
            *_series(experiment, "b", "c", effect=0.33, decisive=True, metric="orders"),
            *_series(experiment, "b", "d", effect=-0.04, decisive=True, metric="orders"),
            *_series(experiment, "c", "d", effect=-0.38, decisive=True, metric="orders"),
        ]
        readout = evaluate(experiment, rows)
        assert [(r.metric, r.leader) for r in readout.rollups] == [
            ("revenue", "b"),
            ("orders", "c"),
        ]
        assert readout.leaders_agree is False

    def test_a_never_compared_pair_is_untested_not_co_leaders(self):
        """DEC-2 delta 2: ``untested`` covers "we could not look", and merging it
        into ``co_leaders`` would be a positive claim of *measured* non-separation
        about a pair nobody measured. Reachable with no config edit — the
        treatment pair holds the two smallest arms and demotes first."""
        experiment = _rollup_experiment()
        rows = [
            *_series(experiment, "control", "b", effect=0.30, decisive=True),
            *_series(experiment, "control", "c", effect=0.10, decisive=True),
            *_series(experiment, "control", "d", effect=0.00, decisive=False),
            # no b-vs-c, b-vs-d or c-vs-d rows at all
        ]
        rollup = _rollup(experiment, rows)
        assert (rollup.leader, rollup.separation) == ("b", "untested")


# ───────────────────────────────────────────────── leg 4: the surfaces agree


class TestEverySurfaceNamesTheSameLeader:
    """Leg 4, over ONE capture — so "identical rows" is literal, not arranged.

    The plan says four surfaces; there are five. DEC-4 put the decision layer in
    ``abk explore`` too, and the CLI's line is a sixth reader of the same
    payload. ``_verdict_note`` is called here rather than the whole CLI because
    the LINE's emission is pinned by leg 1's real ``abk run --report`` and by
    ``tests/cli/test_run_report.py``; what leg 4 owns is agreement.
    """

    def test_the_leader_is_the_same_arm_everywhere(self, four_arm):
        report, row = four_arm["report"], four_arm["dashboard_row"]
        rollups = {r["metric"]: r for r in report["rollups"]}
        assert {m: r["leader"] for m, r in rollups.items()} == {"arpu": "c", "conversion": "c"}
        assert {m: r["separation"] for m, r in rollups.items()} == {
            "arpu": "co_leaders",
            "conversion": "co_leaders",
        }
        # the dashboard row's headline metric is the first declared MAIN metric
        assert (row["leader"], row["separation"]) == ("c", "co_leaders")
        assert row["rollups"] == report["rollups"]
        # explore takes the report payload verbatim; notify carries the same
        # rollup as FIELDS on the control-anchored payload (D7)
        assert four_arm["explore"]["default_metric"] == "arpu"
        leaders = {
            (ctx["metric"], ctx["name_2"]): ctx["rollup_display"] for ctx in four_arm["notify"]
        }
        assert all("c" in text for text in leaders.values()), leaders
        assert all("not separated" in text for text in leaders.values()), leaders
        # …and the CLI names it per metric rather than printing bare words
        assert _verdict_note(report) == "leader — arpu: c, conversion: c"

    def test_the_row_takes_its_numbers_from_the_leaders_own_pair(self, four_arm):
        """DEC-4's headline fix: every stat cell follows the leader, not just the
        chip. The mutation that left them on ``ship[0]`` survived 769 tests."""
        report = four_arm["report"]
        leader_pair = next(
            v
            for v in report["verdicts"]
            if v["metric"] == "arpu" and v["pair"]["t"] == "c" and v["role"] == "vs_control"
        )
        row = four_arm["dashboard_row"]
        assert row["verdict"] == leader_pair["verdict"]
        assert math.isclose(row["effect"], leader_pair["effect"], rel_tol=REL)
        assert math.isclose(row["pvalue"], leader_pair["pvalue"], rel_tol=REL)
        assert row["rationale"] == leader_pair["rationale"]

    def test_a_notification_carries_the_rollup_of_its_own_metric(self, four_arm):
        """A message about ``conversion`` naming ``arpu``'s leader would name an
        arm the reader cannot find in the numbers beside it."""
        for context in four_arm["notify"]:
            assert context["metric"] in context["rollup_display"], context["rollup_display"]

    def test_messages_stay_control_anchored_and_do_not_multiply(self, four_arm):
        """D7, as a COUNT. Two main metrics × three treatments = six messages;
        one per declared pair would be twelve, and the rollup assertions above
        would all still pass on the doubled list — a treatment-pair message
        carries a metric and a leader like any other. This is the DEC-2 hold that
        must never be released: a treatment pair is evidence, and a three-arm
        experiment must not triple its message volume."""
        assert len(four_arm["notify"]) == 6
        assert {(c["metric"], c["name_1"], c["name_2"]) for c in four_arm["notify"]} == {
            (metric, "control", treatment)
            for metric in ("arpu", "conversion")
            for treatment in ("b", "c", "d")
        }
        assert all(c["rollup_display"] for c in four_arm["notify"])


class TestTheOnlyTwoThingsThatMoved:
    """The handoff's second correction: a gate that cannot tell a deliberate
    multi-arm change from a regression is not a gate. Both moves are asserted
    against the released surface, with their DIRECTION."""

    def test_the_dashboard_headline_moved_to_the_leader(self, four_arm, golden):
        """`0.8.0` read ``verdicts[0]`` — the first declared TREATMENT — as the
        experiment's result on the project-level cockpit. The fixture's leader is
        deliberately not that arm, so the row's numbers legitimately move; every
        one of them is another declared pair's `0.8.0` number, unchanged."""
        was, now = golden["four_arm"]["dashboard_row"], four_arm["dashboard_row"]
        assert not math.isclose(now["effect"], was["effect"], rel_tol=1e-6)
        # `0.8.0`'s headline pair still exists and still carries `0.8.0`'s number
        first_treatment = next(
            v
            for v in four_arm["report"]["verdicts"]
            if v["metric"] == "arpu" and v["pair"]["t"] == "b" and v["role"] == "vs_control"
        )
        assert math.isclose(first_treatment["effect"], was["effect"], rel_tol=REL)

    def test_the_aa_instrument_moved_only_where_dec_5_said(self, golden):
        """DEC-5, M14's ONE exception. The multi-arm placebo used to pool every
        arm — at four even arms a 1/4-vs-3/4 split over four arms' units against
        a live 1/2-vs-1/2 over two — so it measured a design nobody runs.

        Three separate claims, because the instrument is blind along one axis and
        was wrong along another (the STAT-2 lesson in a second place):

        * the FPR column does NOT move meaningfully — a null is a null at any
          n, which is exactly why this went unnoticed;
        * achieved-MDE GROWS by about ``√(2(G−1)/G)`` = √1.5 at four arms,
          i.e. `0.8.0` was optimistic, and power falls with it;
        * the verdict now DISCLOSES which contrast it calibrated.
        """
        was = golden["aa"]["four_arm"][0]
        now = capture_aa_surface(FOUR_ARMS)[0]

        assert now["alpha"] == was["alpha"] == 0.05
        assert now["iterations"] == was["iterations"]
        # (a) both readings stay inside the same budget — the blind column
        budget = json.loads(was["details"])["budget"]
        assert was["fpr"] < budget and now["fpr"] < budget, (was["fpr"], now["fpr"], budget)
        assert "well-calibrated" in was["verdict"] and "well-calibrated" in now["verdict"]
        # (b) the column that was wrong: √1.5 ≈ 1.22, measured with a wide band
        # because 300 iterations resolve an achieved MDE to a few percent
        ratio = now["achieved_mde"] / was["achieved_mde"]
        assert 1.10 < ratio < 1.35, ratio
        assert now["power"] < was["power"]
        # (c) and it says so, naming the pair it drew the placebo from
        assert "calibrated on control vs b" in now["verdict"]
        assert "calibrated on" not in was["verdict"]


# ────────────────────────────────────────── leg 5: the narrowed contrast family


class TestVsControlLeavesSeparationUntested:
    """Leg 5. Under ``contrasts: vs_control`` the treatment-vs-treatment rows are
    never computed, so no separation claim is available — and the reason must be
    the KNOB, not an invented measurement."""

    def test_the_rollup_says_untested_and_names_the_knob(self, vs_control):
        for rollup in vs_control["report"]["rollups"]:
            assert rollup["separation"] == "untested", rollup
            assert rollup["leader"] == "c", rollup
            assert "vs_control" in " ".join(rollup["rationale"]), rollup["rationale"]

    def test_only_the_control_anchored_pairs_exist_at_all(self, vs_control):
        """The half of ``contrasts`` that a separation claim depends on: the
        treatment pairs are not computed, so nothing could have tested them."""
        pairs = {(r["name_1"], r["name_2"]) for r in vs_control["_ab_results"]}
        assert pairs == {("control", "b"), ("control", "c"), ("control", "d")}
        assert all(v["role"] == "vs_control" for v in vs_control["readout"]["verdicts"])

    def test_every_surface_repeats_the_same_state(self, vs_control):
        row = vs_control["dashboard_row"]
        assert (row["leader"], row["separation"]) == ("c", "untested")
        assert all(
            "not tested" in ctx["rollup_display"] or "untested" in ctx["rollup_display"]
            for ctx in vs_control["notify"]
        ), [ctx["rollup_display"] for ctx in vs_control["notify"]]
        assert _verdict_note(vs_control["report"]) == "leader — arpu: c, conversion: c"
