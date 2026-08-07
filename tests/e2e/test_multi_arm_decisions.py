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

The five legs are the plan's DEC-6, in order, with the corrections the build
made to it:

1. **Byte-compatibility against a real `v0.8.0` checkout** — the STAT-6
   discipline, via ``_m14_baseline.py`` (which documents how to regenerate).
   The claim is the DEC-3 **amended** form, not the plan's literal one: for a
   rendering surface "byte-identical" is almost always false — the payload
   legitimately gains keys and the report's stylesheet grows — so what is
   asserted is that **every `0.8.0` field reproduces and the only difference is
   an enumerated set of ADDED keys, each with a DECLARED value**. Two persisted
   tables and seven read surfaces: the readout, the report payload, the
   dashboard row, the notification contexts, the explore payload, the real CLI
   ``Report →`` line and the ``_ab_aa_runs`` row.
2. **Control-anchored verdicts unchanged at four arms**, field for field with
   ``rationale`` and ``caveats`` included, while the three treatment pairs
   appear beside them — under the default `bonferroni` AND under the read-time
   ``benjamini_hochberg``, which is the scheme §0.2 point 1 is actually about.
3. **Rollup correctness on constructed data**: a clean leader, two arms that
   cannot be separated, no winner at all, and a leader that LOSES on the second
   main metric.
4. **Every surface names the same leader and the same separation** over
   identical rows — the report payload, the dashboard row, the notification
   context, the explore payload and the CLI's verdict note.
5. **``contrasts: vs_control`` ⇒ ``separation: untested``**, with the knob named
   as the reason.

**Two structural rules this file learned the hard way**, both from mutations that
survived its first draft:

* **An ADDED key is exempt from value comparison** — that is what makes the
  comparison possible at all — so every added path needs a declared expected
  value (:data:`ADDED_VALUES`). Without it, `_ab_experiments.control` could name
  the wrong arm to BI, `report.srm.culprit` could invent an arm on a green gate,
  and a two-arm message could grow prose, all invisibly.
* **A per-item assertion cannot see a doubled or filtered list.** Every list the
  decision layer produces therefore has an explicit expected LENGTH, and the
  four-arm verdict lists are compared as a PREFIX — DEC-2 requires `0.8.0`'s
  verdicts to come first, in `0.8.0`'s order, which nothing else pins.

What this file does NOT reach, stated rather than implied: the rendered **DOM**
(``web/test/smoke*.mjs`` owns it — the report gates its affordances on
``isMultiArm()``, the dashboard row on its distinct-treatment count, explore on
``payload.arms.length > 2``), the notification TRANSPORTS (nine channels, pinned
in ``tests/notify/test_channels.py`` — this gate compares ``build_context()``,
which is what a message *says*), a FAILING SRM gate (its per-arm culprit
decomposition is pinned in ``tests/stats/test_srm.py`` and
``tests/reporting/test_builder.py``; here the gate is green, and the value pin
below is what keeps a culprit from being invented on a green one), ``abk plan``
(``tests/cli/test_plan_command.py``), and DEC-1's re-orientation of a declared
non-first control, which has no `0.8.0` counterpart to compare against at all —
``assignment.control`` is an unknown key there — and is pinned by
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


def _under(prefix: str, paths=ADDED_TWO_ARM) -> frozenset:
    """The same added-key set, rooted under a capture's name.

    :data:`ADDED_TWO_ARM` is the readable documentation of what M14 adds and is
    kept prefix-free; the four-arm legs walk the same surfaces under a
    ``four_arm.`` root, and DERIVING their set is what stops the two from
    drifting into separate lists of the same fact.
    """
    return frozenset(f"{prefix}.{path}" for path in paths)


#: The four-arm surfaces gain exactly the same keys and nothing else. The verdict
#: LISTS grow, which is :data:`PREFIX_EXTENSIBLE`'s job, not this set's.
ADDED_FOUR_ARM = _under("four_arm")

#: Lists whose `0.8.0` content must be a literal PREFIX of HEAD's. DEC-2 requires
#: every control-anchored verdict first, in `0.8.0`'s exact order, then the
#: treatment pairs — which is what keeps `verdicts[0]` on the same pair and makes
#: `ship_decisions` a prefix. Nothing else in the suite pins it. An entry here is
#: only safe beside an explicit LENGTH assertion: a re-added `ship_decisions`
#: filter produces exactly `0.8.0`'s list, which is a valid prefix of itself.
PREFIX_EXTENSIBLE = frozenset(
    {
        "four_arm.readout.verdicts",
        "four_arm.report.verdicts",
        "four_arm.explore.verdicts",
        "four_arm_bh.readout.verdicts",
    }
)

#: **Every added path's expected value at two arms.** An added key is exempt from
#: the golden comparison by construction, so without this table each one is a
#: hole: mutations proved that `_ab_experiments.control` could name the LAST arm
#: to BI, `report.control` could make the report header name a treatment as the
#: baseline, and `report.srm.culprit` could invent an arm on a green gate — all
#: with the whole gate green. A value here is the two-arm claim: the decision
#: layer is present, correct, and says nothing a two-arm reader has to interpret.
ADDED_VALUES: dict[str, object] = {
    "_ab_experiments[].control": "control",
    "readout.verdicts[].role": "vs_control",
    "readout.verdicts[].judged": True,
    # both rollups name the single treatment, so they agree — which is exactly
    # why the report GATES its leaders chip on the arm count (DEC-3): at two
    # arms agreement is a tautology, not news
    "readout.leaders_agree": True,
    "report.control": "control",
    "report.verdicts[].role": "vs_control",
    "report.leaders_agree": True,
    # a green gate has no culprit; a dict here would be an invented arm
    "report.srm.culprit": None,
    "dashboard_row.leader": "treatment",
    "dashboard_row.separation": "separated",
    "dashboard_row.leaders_agree": True,
    "dashboard_row.verdicts[].role": "vs_control",
    # DEC-4 gates the message line on the ARM COUNT: at two arms "leader:
    # treatment" only restates the verdict word beside it, so the line is EMPTY
    # and a `0.8.0` message is unchanged to the character
    "notify[].rollup_display": "",
    "notify[].rollup_line": "",
}

#: The two added paths whose value is a structure rather than a scalar, checked
#: by their own assertions below (a literal here would be unreadable).
ADDED_STRUCTURED = frozenset({"readout.rollups", "report.rollups", "dashboard_row.rollups"})


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


def assert_reproduces(
    actual, expected, where: str, *, added=frozenset(), diffs=None, values=None
) -> None:
    """*expected* (the `0.8.0` capture) reproduces inside *actual*, key by key.

    Recursive over dicts and lists. A key present in *actual* and absent from
    *expected* is allowed only when its normalised path is in *added* — and then
    its value is checked against *values* when that path declares one, because an
    added key is otherwise never looked at. A key that disappeared is always a
    failure.

    Numbers compare at rel-1e-9 rather than by identity, because byte
    reproducibility of a float aggregate holds only under a fixed BLAS
    configuration (M7 D13) and CI is not the machine the golden was captured on.
    **Integers compare exactly**: every int on these surfaces is a count, a
    ms-epoch instant or a flag, and a tolerance on a count is a tolerance on a
    row that went missing.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: {type(actual).__name__} is not a dict"
        for key in sorted(set(actual) - set(expected)):
            path = _normalise(f"{where}.{key}")
            assert path in added, f"{where}: unexpected new key {key!r} (path {path})"
            if diffs is not None:
                diffs.add(path)
            if values is not None and path in values:
                assert actual[key] == values[path], (
                    f"{where}.{key}: added key carries {actual[key]!r}, "
                    f"the declared two-arm value is {values[path]!r}"
                )
        for key in sorted(set(expected) - set(actual)):
            raise AssertionError(f"{where}: key {key!r} disappeared")
        for key in expected:
            assert_reproduces(
                actual[key],
                expected[key],
                f"{where}.{key}",
                added=added,
                diffs=diffs,
                values=values,
            )
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{where}: {type(actual).__name__} is not a list"
        if _normalise(where) in PREFIX_EXTENSIBLE:
            assert len(actual) >= len(expected), f"{where}: {len(actual)} items vs {len(expected)}"
        else:
            assert len(actual) == len(expected), f"{where}: {len(actual)} items vs {len(expected)}"
        for index, item in enumerate(expected):
            assert_reproduces(
                actual[index], item, f"{where}[{index}]", added=added, diffs=diffs, values=values
            )
    elif isinstance(expected, bool) or isinstance(actual, bool):
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"
    elif isinstance(expected, int) and isinstance(actual, int):
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

    #: The surfaces compared field-for-field against the golden. ``explore`` is
    #: not one of them — it is a RELATION to the report payload rather than an
    #: independent document, and its two halves get their own tests below.
    SURFACES = (
        "_ab_results",
        "_ab_experiments",
        "readout",
        "report",
        "dashboard_row",
        "notify",
    )

    @pytest.mark.parametrize("surface", SURFACES)
    def test_every_two_arm_surface_reproduces_0_8_0(self, two_arm, golden, surface):
        assert_reproduces(
            two_arm[surface],
            golden["two_arm"][surface],
            surface,
            added=ADDED_TWO_ARM,
            values=ADDED_VALUES,
        )

    def test_the_capture_and_the_comparison_cover_the_same_surfaces(self, two_arm):
        """A captured-but-unread surface is dead weight that reads as coverage —
        four of the seven four-arm surfaces sat unread in a 1 MB fixture until the
        review counted them."""
        assert set(two_arm) == set(self.SURFACES) | {"explore"}

    def test_the_cockpit_knob_block_reproduces_0_8_0(self, two_arm, golden):
        """The cockpit's own block gains nothing: M14 put its keys at the payload
        TOP level, so an empty ``added`` set here is the statement."""
        assert_reproduces(
            two_arm["explore"]["block"], golden["two_arm"]["explore"]["block"], "explore.block"
        )

    def test_the_cockpit_carries_the_report_payload_through_verbatim(self, two_arm):
        """DEC-4 released DEC-3's hold here, and the architecture rules forbid
        re-adding it in italics — so it needs a test that can SEE a filter.

        ``build_explore_payload`` returns ``dict(report_payload)`` plus one
        ``explore`` key, so ``passthrough`` names every report key that arrived
        with its value intact. A ``ship_decisions`` filter re-added in
        ``tuning/payload.py`` drops ``verdicts`` from that list — the first draft
        of this gate captured only the ``explore`` sub-block and the mutation
        passed while every arm-vs-arm card vanished from Review mode.
        """
        passthrough = set(two_arm["explore"]["passthrough"])
        assert {"verdicts", "rollups", "leaders_agree", "control", "srm", "metrics"} <= passthrough
        assert passthrough == set(two_arm["report"]) - {"explore"}
        assert two_arm["explore"]["verdicts"] == two_arm["report"]["verdicts"]

    def test_every_declared_addition_is_actually_present(self, two_arm, golden):
        """The other direction, and the one a permissive comparator forgets: an
        ``added`` path nothing produces would make :data:`ADDED_TWO_ARM` a wish
        list. DEC-3's own review found a "hold" that had stopped being called at
        all and left fifteen tests green — an allowance nobody exercises is the
        same defect one level up.
        """
        seen: set[str] = set()
        for surface in self.SURFACES:
            assert_reproduces(
                two_arm[surface],
                golden["two_arm"][surface],
                surface,
                added=ADDED_TWO_ARM,
                diffs=seen,
            )
        assert seen == ADDED_TWO_ARM, (
            f"declared but never produced: {sorted(ADDED_TWO_ARM - seen)}; "
            f"produced but undeclared: {sorted(seen - ADDED_TWO_ARM)}"
        )

    def test_every_added_key_declares_its_two_arm_value(self):
        """The structural rule, enforced on the table itself.

        :func:`assert_reproduces` cannot look at an added key's value, so a path
        with no entry in :data:`ADDED_VALUES` and no structured assertion of its
        own is a hole — proved three times over by mutation: the catalog's
        ``control`` naming the wrong arm, the report header naming a treatment as
        the baseline, and an invented SRM culprit on a green gate.
        """
        undeclared = ADDED_TWO_ARM - set(ADDED_VALUES) - ADDED_STRUCTURED
        assert not undeclared, undeclared

    def test_the_two_arm_rollup_is_present_and_says_the_obvious(self, two_arm):
        """The structured half of the table above. With one treatment the rollup
        has exactly one candidate, so it must name it and report `separated` —
        `untested` would invent a gap and `no_leader` would contradict the WIN
        beside it."""
        for surface in ("readout", "report", "dashboard_row"):
            rollups = two_arm[surface]["rollups"]
            assert len(rollups) == 2, (surface, rollups)  # two main metrics
            for rollup in rollups:
                assert rollup["leader"] == "treatment", (surface, rollup)
                assert rollup["separation"] == "separated", (surface, rollup)
                assert rollup["indistinguishable"] == [], (surface, rollup)

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


#: Four arms × ``all_pairs`` = C(4,2) = 6 declared pairs; 3 of them are
#: control-anchored. Two MAIN metrics carry verdicts, so 12 verdicts of which 6
#: are ship decisions, and 6 messages (D7: control-anchored only).
FOUR_ARM_VERDICTS = 12
FOUR_ARM_SHIP = 6
#: 6 pairs × 3 comparisons × FOUR_ARM_DAYS looks.
FOUR_ARM_ROWS = 126


class TestControlAnchoredVerdictsAreUnchangedAtFourArms:
    """§0.2's structural claim, measured: verdicting a row that is already in the
    read-time family cannot move a threshold."""

    #: Compared as wholes. ``dashboard_row`` is excluded on purpose — its headline
    #: legitimately MOVED (DEC-4) — and its unmoved fields are asserted below.
    SURFACES = ("_ab_results", "_ab_experiments", "readout", "report", "notify")

    @pytest.mark.parametrize("surface", SURFACES)
    def test_every_four_arm_surface_reproduces_0_8_0(self, four_arm, golden, surface):
        """The four-arm half used to capture seven surfaces and read three, so a
        `0.8.0` number could move inside a `0.8.0` message unseen (mutation: every
        four-arm notification reported α = 0.5 instead of the corrected 0.00833,
        with the whole gate green).

        The verdict LISTS grow here, which :data:`PREFIX_EXTENSIBLE` allows and
        the count assertions below bound.
        """
        assert_reproduces(
            four_arm[surface],
            golden["four_arm"][surface],
            f"four_arm.{surface}",
            added=ADDED_FOUR_ARM,
        )

    def test_the_persisted_rows_are_identical(self, four_arm, golden):
        """No number, no alpha and no ``reject`` flag moved."""
        assert len(four_arm["_ab_results"]) == FOUR_ARM_ROWS
        assert_reproduces(
            four_arm["_ab_results"], golden["four_arm"]["_ab_results"], "four_arm._ab_results"
        )

    def test_the_0_8_0_verdicts_are_a_literal_PREFIX_of_the_new_list(self, four_arm, golden):
        """DEC-2's ordering rule, which nothing else pins: every control-anchored
        verdict comes first, in `0.8.0`'s exact sequence, then the treatment
        pairs. That is what keeps ``verdicts[0]`` on the same pair and makes
        ``ship_decisions`` a prefix rather than a filter of a shuffled list.

        The whole-surface comparison above already walks the prefix field for
        field (``rationale`` and ``caveats`` included); this asserts the LENGTHS,
        without which a re-added ``ship_decisions`` filter — producing exactly
        `0.8.0`'s six — is a valid prefix of itself, and a DOUBLED list is
        invisible to every per-item check.
        """
        for surface in ("readout", "report"):
            verdicts = four_arm[surface]["verdicts"]
            assert len(verdicts) == FOUR_ARM_VERDICTS, surface
            assert len(golden["four_arm"][surface]["verdicts"]) == FOUR_ARM_SHIP, surface
            roles = [v["role"] for v in verdicts]
            assert roles == ["vs_control"] * FOUR_ARM_SHIP + ["treatment_pair"] * FOUR_ARM_SHIP

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
        assert set(now) - was == {
            (metric, name_1, name_2)
            for metric in ("arpu", "conversion")
            for name_1, name_2 in (("b", "c"), ("b", "d"), ("c", "d"))
        }
        assert all(now[key] == "vs_control" for key in was)
        assert all(now[key] == "treatment_pair" for key in set(now) - was)

    def test_the_alphas_did_not_move_at_any_look(self, four_arm, golden):
        """The divisor is derived from the arm count and the declared family, not
        from an enumeration (§0.2 point 2). Keyed by (pair, LOOK): the first draft
        keyed by pair alone, so a dict comprehension over 252 rows kept only the
        last look and an alpha that moved on looks 1–13 passed."""

        def alphas(rows):
            return {(r["metric"], r["name_1"], r["name_2"], r["end_ts"]): r["alpha"] for r in rows}

        was, now = alphas(golden["four_arm"]["_ab_results"]), alphas(four_arm["_ab_results"])
        assert len(was) == FOUR_ARM_ROWS
        assert {k: now[k] for k in was} == was

    def test_the_dashboard_rows_unmoved_fields_are_unmoved(self, four_arm, golden):
        """Everything on the row EXCEPT the headline cells the leader legitimately
        moved. ``guardrail_regressed`` is here because DEC-4 scoped it to the SHIP
        decisions — a regression between two treatments says nothing about harm
        relative to control — and reverting that scope (mutation: OR over every
        declared pair) flipped it False → True on a fixture with no regression
        against the control, invisibly."""
        was, now = golden["four_arm"]["dashboard_row"], four_arm["dashboard_row"]
        for field in (
            "guardrail_regressed",
            "srm_flag",
            "srm_pvalue",
            "main_metric",
            "insufficient",
        ):
            assert now[field] == was[field], field

    def test_a_read_time_correction_scheme_moves_no_verdict_either(self, golden):
        """§0.2 point 1 is stated about ``benjamini_hochberg``/``holm``, and this
        is the only leg that runs one.

        Under the default ``bonferroni`` the family is resolved at COMPUTE time
        and no read-time family exists at all — so the legs above measure "adding
        a verdict cannot move a threshold" exactly where it is trivially true.
        Under BH the family IS built at read time from every informative row at a
        cutoff, treatment-pair rows included, which is the configuration the claim
        is about.
        """
        from _m14_baseline import capture_read_time_family

        surface = capture_read_time_family(FOUR_ARMS)
        assert_reproduces(
            surface["readout"],
            golden["four_arm_bh"]["readout"],
            "four_arm_bh.readout",
            added=_under("four_arm_bh"),
        )
        assert len(surface["readout"]["verdicts"]) == FOUR_ARM_VERDICTS
        assert len(golden["four_arm_bh"]["readout"]["verdicts"]) == FOUR_ARM_SHIP
        assert surface["alphas"] == golden["four_arm_bh"]["alphas"]


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

    def test_the_leader_on_one_metric_can_be_a_LOSER_on_another(self):
        """The contract's fourth shape, and the one DEC-4's ``N lost`` chip exists
        for: ``b`` leads ``revenue`` and is decisively WORSE than the control on
        ``orders``, so it appears in one rollup as the leader and in the other's
        ``losers``. Merely giving the two metrics *different* leaders is weaker —
        it leaves "the leader is also a loser somewhere" untested, which is the
        state an operator most needs told.

        D2's other half rides along: no cross-metric pick, and ``leaders_agree``
        REPORTS the split rather than resolving it.
        """
        experiment = _rollup_experiment(metrics=("revenue", "orders"))
        rows = [
            *_series(experiment, "control", "b", effect=0.30, decisive=True),
            *_series(experiment, "control", "c", effect=0.10, decisive=True),
            *_series(experiment, "control", "d", effect=0.00, decisive=False),
            *_series(experiment, "b", "c", effect=-0.15, decisive=True),
            *_series(experiment, "b", "d", effect=-0.28, decisive=True),
            *_series(experiment, "c", "d", effect=-0.09, decisive=True),
            # `orders`: b LOSES to the control, c wins it
            *_series(experiment, "control", "b", effect=-0.20, decisive=True, metric="orders"),
            *_series(experiment, "control", "c", effect=0.40, decisive=True, metric="orders"),
            *_series(experiment, "control", "d", effect=0.00, decisive=False, metric="orders"),
            *_series(experiment, "b", "c", effect=0.60, decisive=True, metric="orders"),
            *_series(experiment, "b", "d", effect=0.18, decisive=True, metric="orders"),
            *_series(experiment, "c", "d", effect=-0.38, decisive=True, metric="orders"),
        ]
        readout = evaluate(experiment, rows)
        by_metric = {r.metric: r for r in readout.rollups}
        assert [(r.metric, r.leader) for r in readout.rollups] == [
            ("revenue", "b"),
            ("orders", "c"),
        ]
        assert readout.leaders_agree is False
        # the same arm: `revenue`'s leader, `orders`' loser
        assert by_metric["orders"].losers == ("b",)
        assert by_metric["revenue"].losers == ()
        # …and a loser is never also an unresolved co-leader (disjoint by
        # construction — an arm the baseline BEAT is not a leadership candidate)
        assert "b" not in by_metric["orders"].indistinguishable

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

    The plan says four surfaces; there are five, because DEC-4 put the decision
    layer in ``abk explore`` too, and the CLI's line is a sixth reader of the same
    payload. ``_verdict_note`` is called here rather than the whole CLI because
    the LINE's emission is pinned by leg 1's real ``abk run --report`` and by
    ``tests/cli/test_run_report.py``; what leg 4 owns is agreement.

    **The fixture gives the two main metrics DIFFERENT leaders on purpose**
    (``c`` on ``arpu``, ``b`` on ``conversion``). With one leader everywhere,
    a message carrying the experiment's FIRST rollup instead of its own metric's
    is indistinguishable from a correct one — and the mutation that did exactly
    that passed the gate's first draft.
    """

    #: What every surface must say, per metric. Written out so an assertion can
    #: compare a whole rendered STRING rather than test that a one-character arm
    #: name appears somewhere in it — `"c" in "conversion"` and `"c" in "control"`
    #: are both true, which is how a message reading "Leader on arpu: control"
    #: passed a substring check.
    EXPECTED = {
        "arpu": ("c", "co_leaders", "Leader on arpu: c — not separated from every other arm"),
        "conversion": (
            "b",
            "co_leaders",
            "Leader on conversion: b — not separated from every other arm",
        ),
    }

    def test_the_report_and_the_dashboard_row_agree(self, four_arm):
        report, row = four_arm["report"], four_arm["dashboard_row"]
        rollups = {r["metric"]: r for r in report["rollups"]}
        assert {m: r["leader"] for m, r in rollups.items()} == {
            m: expected[0] for m, expected in self.EXPECTED.items()
        }
        assert {m: r["separation"] for m, r in rollups.items()} == {
            m: expected[1] for m, expected in self.EXPECTED.items()
        }
        # the row's own cells describe the HEADLINE metric — the first declared
        # main one — while `rollups` carries every metric's, identical to the
        # report's
        assert (row["leader"], row["separation"]) == self.EXPECTED["arpu"][:2]
        assert row["rollups"] == report["rollups"]
        assert row["leaders_agree"] is False and report["leaders_agree"] is False

    def test_the_cockpit_sees_the_same_verdicts_and_rollups(self, four_arm):
        """Not ``default_metric``, which says nothing about a leader: the cockpit
        renders the report payload's own lists, so THEY are the assertion."""
        assert four_arm["explore"]["verdicts"] == four_arm["report"]["verdicts"]
        assert four_arm["explore"]["rollups"] == four_arm["report"]["rollups"]
        assert len(four_arm["explore"]["verdicts"]) == FOUR_ARM_VERDICTS

    def test_every_message_renders_its_own_metrics_rollup_verbatim(self, four_arm):
        """The whole line, compared as a string.

        Two mutations survived the substring version: taking the experiment's
        first rollup instead of the verdict's own metric, and printing
        ``name_1`` — the CONTROL — where the leader belongs, which reads
        "Leader on arpu: control" and still contains a ``c``.
        """
        rendered = {
            (ctx["metric"], ctx["name_2"]): (ctx["rollup_display"], ctx["rollup_line"])
            for ctx in four_arm["notify"]
        }
        assert len(rendered) == FOUR_ARM_SHIP
        for (metric, _treatment), (display, line) in rendered.items():
            expected = self.EXPECTED[metric][2]
            assert display == expected, (metric, display)
            assert line == expected + "\n", (metric, line)

    def test_the_cli_line_names_the_leader_per_metric(self, four_arm):
        """And it must not print bare verdict words: at two main metrics the same
        arm would appear twice with contradictory verdicts (`ship_decisions` is
        metric-blind), which is why DEC-4 replaced the join with the rollup."""
        assert _verdict_note(four_arm["report"]) == (
            "leader — arpu: c, conversion: b (metrics disagree)"
        )

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

    def test_messages_stay_control_anchored_and_do_not_multiply(self, four_arm):
        """D7, as a COUNT. Two main metrics × three treatments = six messages;
        one per declared pair would be twelve, and every rollup assertion above
        would still pass on the doubled list — a treatment-pair message carries a
        metric and a leader like any other. This is the DEC-2 hold that must never
        be released: a treatment pair is evidence, and a four-arm experiment must
        not double its message volume."""
        assert len(four_arm["notify"]) == FOUR_ARM_SHIP
        assert {(c["metric"], c["name_1"], c["name_2"]) for c in four_arm["notify"]} == {
            (metric, "control", treatment)
            for metric in ("arpu", "conversion")
            for treatment in ("b", "c", "d")
        }


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

        * the FPR column has no SYSTEMATIC move — a null is a null at any n,
          which is exactly why this went unnoticed. Its sampled value does move
          (300 iterations resolve it to ±2 hits), so what is asserted is that
          both readings sit inside the same budget and read "well-calibrated";
        * achieved-MDE GROWS by about ``√(2(G−1)/G)`` = √1.5 at four arms,
          i.e. `0.8.0` was optimistic, and power falls with it;
        * the verdict now DISCLOSES which contrast it calibrated.

        The band on the ratio is deliberately narrow at the top: a HALF-revert
        (restoring the pooled denominator in ``_share_a`` while keeping the arm
        filter) measures 1.383, only 2.4% above the upper bound. Widen it and that
        revert starts passing.
        """
        was = golden["aa"]["four_arm"][0]
        now = capture_aa_surface(FOUR_ARMS)[0]

        assert now["alpha"] == was["alpha"] == 0.05
        assert now["iterations"] == was["iterations"]
        # (a) the blind column: no SYSTEMATIC move, asserted as "within sampling
        # noise of each other" rather than as a budget word. The two readings come
        # from DIFFERENT placebo pools, so the difference carries both variances:
        # σ_diff = √2·√(α(1−α)/N) ≈ 0.0097 at N=1000, and 3σ_diff ≈ 0.029. A
        # change in the rejection rule clears that; a different draw of the same
        # exact null does not. A budget-band assertion looked equivalent and was
        # not — one unlucky draw crossed it and reported "inflated" for a correct
        # engine, which is the failure this column's blindness is ABOUT.
        sigma_diff = math.sqrt(2 * 0.05 * 0.95 / now["iterations"])
        assert abs(now["fpr"] - was["fpr"]) < 3 * sigma_diff, (
            was["fpr"],
            now["fpr"],
            sigma_diff,
        )
        # (b) the column that was wrong: √1.5 ≈ 1.22, measured with a band wide
        # enough for 300 iterations and no wider (see the docstring)
        ratio = now["achieved_mde"] / was["achieved_mde"]
        assert 1.10 < ratio < 1.35, ratio
        assert now["power"] < was["power"]
        # (c) and it says so, naming the pair it drew the placebo from
        assert "calibrated on control vs b" in now["verdict"]
        assert "calibrated on" not in was["verdict"]

    def test_the_placebo_split_still_reads_the_declared_shares(self, golden):
        """DEC-5's other half, which an even split cannot test at all.

        ``_share_a``'s denominator is the calibrated PAIR rather than the whole
        declared split. On an even split the pair's share is 0.5 at every arm
        count, so ``_share_a`` reduced to a hardcoded ``0.5`` — ignoring
        ``expected_split`` entirely — moved no number and passed the gate. The A/A
        fixture therefore declares 40/30/20/10 at four arms (pair share
        0.4/0.7 ≈ 0.571) and 60/40 at two, where the pair's share IS the whole
        split's — the coincidence the two-arm byte-identity claim rests on.
        """
        from _m14_baseline import experiment_payload

        from abkit.config import ExperimentConfig
        from abkit.validate.runner import _share_a

        four = experiment_payload(FOUR_ARMS)
        four["assignment"]["expected_split"] = {"control": 0.4, "b": 0.3, "c": 0.2, "d": 0.1}
        assert _share_a(ExperimentConfig.model_validate(four)) == pytest.approx(0.4 / 0.7)

        two = experiment_payload(TWO_ARMS)
        two["assignment"]["expected_split"] = {"control": 0.6, "treatment": 0.4}
        assert _share_a(ExperimentConfig.model_validate(two)) == pytest.approx(0.6)


# ────────────────────────────────────────── leg 5: the narrowed contrast family


class TestVsControlLeavesSeparationUntested:
    """Leg 5. Under ``contrasts: vs_control`` the treatment-vs-treatment rows are
    never computed, so no separation claim is available — and the reason must be
    the KNOB, not an invented measurement."""

    #: The narrowed family changes WHICH pairs exist, never which arm leads —
    #: same per-metric leaders as leg 4, because the control-anchored rows are
    #: identical.
    LEADERS = {"arpu": "c", "conversion": "b"}

    def test_the_rollup_says_untested_and_names_the_knob(self, vs_control):
        for rollup in vs_control["report"]["rollups"]:
            assert rollup["separation"] == "untested", rollup
            assert rollup["leader"] == self.LEADERS[rollup["metric"]], rollup
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
        # the whole message line, per metric — not a substring, which would pass
        # for any string containing the letter of an arm name
        rendered = {ctx["metric"]: ctx["rollup_display"] for ctx in vs_control["notify"]}
        assert rendered == {
            metric: f"Leader on {metric}: {arm} — separation untested"
            for metric, arm in self.LEADERS.items()
        }
        assert _verdict_note(vs_control["report"]) == (
            "leader — arpu: c, conversion: b (metrics disagree)"
        )
