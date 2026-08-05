"""m14 DEC-1: ``assignment.control`` — the declared baseline.

Every fixture here uses **three arms**. At two arms the declaration can only
name ``variants[0]`` or ``variants[1]``, and in the second case ``treatments``
is a single element either way — so half of these assertions would pass against
a no-op. That is the STAT-1b lesson (its contrast-set tests are 3+ arms for the
identical reason: at two arms ``C(2,2) == 1 == g-1``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from abkit.config import ExperimentConfig


def payload(*, variants=("a", "b", "c"), control=None, contrasts=None, **overrides) -> dict:
    assignment: dict = {
        "query": "SELECT user_id, variant, exposure_ts FROM assignments",
        "variants": list(variants),
        "expected_split": {v: 1 / len(variants) for v in variants},
    }
    if control is not None:
        assignment["control"] = control
    body = {
        "name": "three_arm",
        "start_ts": "2024-07-01",
        "horizon_ts": "2024-07-29",
        "unit_key": "user_id",
        "assignment": assignment,
        "comparisons": [{"metric": "arpu", "is_main_metric": True, "method": {"name": "t-test"}}],
    }
    if contrasts is not None:
        body["contrasts"] = contrasts
    body.update(overrides)
    return body


def build(**kwargs) -> ExperimentConfig:
    return ExperimentConfig.model_validate(payload(**kwargs))


class TestResolution:
    def test_unset_control_is_the_first_declared_variant(self):
        experiment = build()
        assert experiment.control == "a"
        assert experiment.treatments == ("b", "c")

    def test_a_declared_control_wins(self):
        experiment = build(control="b")
        assert experiment.control == "b"

    def test_treatments_keep_declaration_order_around_the_control(self):
        """Not ``variants[1:]``: with the control in the MIDDLE, a slice drops
        the arm that precedes it from every verdict the readout issues."""
        experiment = build(control="b")
        assert experiment.treatments == ("a", "c")

    def test_declaring_the_first_variant_is_the_default_written_out(self):
        assert build(control="a").control == build().control
        assert build(control="a").treatments == build().treatments
        assert not build(control="a").control_reorients_pairs
        assert not build().control_reorients_pairs

    def test_a_non_first_control_reorients(self):
        assert build(control="c").control_reorients_pairs


class TestValidation:
    def test_a_control_outside_variants_is_refused_naming_both_sides(self):
        with pytest.raises(ValidationError) as exc:
            build(control="ghost")
        message = str(exc.value)
        assert "'ghost'" in message
        assert "['a', 'b', 'c']" in message

    def test_the_refusal_survives_a_renamed_arm(self):
        """The realistic mistake: an arm renamed on one line and not the other."""
        with pytest.raises(ValidationError, match="is not one of"):
            ExperimentConfig.model_validate(payload(variants=("a", "b_v2", "c"), control="b"))


class TestContrastPairs:
    def test_the_default_is_byte_identical_to_0_8_0(self):
        """``combinations`` already emits ``variants[0]`` first in every pair
        that contains it, so the re-orientation is a no-op under the default —
        which is what keeps a pre-0.9.0 experiment's rows and alphas untouched."""
        assert build().contrast_pairs() == (("a", "b"), ("a", "c"), ("b", "c"))
        assert build(control="a").contrast_pairs() == (("a", "b"), ("a", "c"), ("b", "c"))

    def test_all_pairs_reorients_but_never_resizes(self):
        pairs = build(control="b").contrast_pairs()
        assert pairs == (("b", "a"), ("a", "c"), ("b", "c"))
        # the family SIZE is the alpha divisor: DEC-1 must not move it
        assert len(pairs) == len(build().contrast_pairs())
        assert {frozenset(p) for p in pairs} == {frozenset(p) for p in build().contrast_pairs()}

    def test_a_last_declared_control_orients_both_of_its_pairs(self):
        pairs = build(control="c").contrast_pairs()
        assert pairs == (("a", "b"), ("c", "a"), ("c", "b"))

    def test_vs_control_follows_the_declaration(self):
        assert build(control="b", contrasts="vs_control").contrast_pairs() == (
            ("b", "a"),
            ("b", "c"),
        )

    def test_vs_control_default_is_unchanged(self):
        assert build(contrasts="vs_control").contrast_pairs() == (("a", "b"), ("a", "c"))

    def test_every_pair_containing_the_control_puts_it_first(self):
        for control in ("a", "b", "c"):
            for family in ("all_pairs", "vs_control"):
                experiment = build(control=control, contrasts=family)
                for name_1, name_2 in experiment.contrast_pairs():
                    assert name_2 != control, (
                        f"{control}/{family}: the baseline must be name_1, got "
                        f"({name_1}, {name_2})"
                    )


class TestCatalog:
    def test_the_resolved_control_is_written_even_when_undeclared(self):
        """The column answers "which arm are these effects measured against",
        and that has an answer for every experiment. Writing NULL for the
        positional default would make BI re-derive the convention from the
        `variants` JSON — the re-derivation `contrasts` was added to stop."""
        assert build().catalog_record()["control"] == "a"
        assert build(control="c").catalog_record()["control"] == "c"

    def test_the_catalog_writer_would_drop_an_unclassified_field(self):
        """`_EXPERIMENT_FIELDS` is a whitelist and `upsert_experiment` refuses a
        record carrying anything outside it — the m13 STAT-6 finding, applied to
        DEC-1's own column."""
        from abkit.database.internal_tables._experiments import _ExperimentsMixin

        assert "control" in _ExperimentsMixin._EXPERIMENT_FIELDS
        record = build().catalog_record()
        assert set(record) <= set(_ExperimentsMixin._EXPERIMENT_FIELDS)
