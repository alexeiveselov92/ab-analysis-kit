"""MetricConfig tests: the type ↔ column-role matrix and query sourcing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from abkit.config import MetricConfig


def make(type_: str, columns: dict, **kwargs) -> MetricConfig:
    return MetricConfig(
        name=kwargs.pop("name", "m1"),
        type=type_,
        columns=columns,
        query=kwargs.pop("query", "SELECT 1"),
        **kwargs,
    )


class TestTypeColumnMatrix:
    def test_sample_requires_value(self):
        make("sample", {"variant": "group", "value": "gross_usd"})
        with pytest.raises(ValidationError, match="requires column roles \\['value'\\]"):
            make("sample", {"variant": "group"})

    def test_sample_accepts_covariate_and_stratum(self):
        config = make(
            "sample",
            {
                "variant": "group",
                "value": "gross_usd",
                "covariate": "prev_gross_usd",
                "stratum": "country",
            },
        )
        assert config.columns.role_map()["covariate"] == "prev_gross_usd"

    def test_sample_rejects_fraction_roles(self):
        with pytest.raises(ValidationError, match="does not accept column roles"):
            make("sample", {"variant": "g", "value": "v", "count": "c"})

    def test_fraction_requires_count_and_nobs(self):
        make("fraction", {"variant": "g", "count": "conversions", "nobs": "visits"})
        with pytest.raises(ValidationError, match="requires column roles"):
            make("fraction", {"variant": "g", "count": "conversions"})

    def test_fraction_rejects_covariate(self):
        with pytest.raises(ValidationError, match="does not accept"):
            make("fraction", {"variant": "g", "count": "c", "nobs": "n", "covariate": "x"})

    def test_ratio_requires_numerator_denominator(self):
        make("ratio", {"variant": "g", "numerator": "clicks", "denominator": "views"})
        with pytest.raises(ValidationError, match="requires column roles"):
            make("ratio", {"variant": "g", "numerator": "clicks"})

    def test_variant_always_required(self):
        with pytest.raises(ValidationError):
            make("sample", {"value": "v"})

    def test_unknown_type_rejected(self):
        with pytest.raises(ValidationError):
            make("median", {"variant": "g", "value": "v"})


class TestQuerySource:
    def test_query_xor_query_file(self):
        with pytest.raises(ValidationError, match="Only one of"):
            MetricConfig(
                name="m1",
                type="sample",
                columns={"variant": "g", "value": "v"},
                query="SELECT 1",
                query_file="sql/m1.sql",
            )
        with pytest.raises(ValidationError, match="Either 'query' or 'query_file'"):
            MetricConfig(name="m1", type="sample", columns={"variant": "g", "value": "v"})

    def test_get_query_text_inline(self):
        assert make("sample", {"variant": "g", "value": "v"}).get_query_text() == "SELECT 1"

    def test_get_query_text_from_file(self, tmp_path):
        (tmp_path / "sql").mkdir()
        (tmp_path / "sql" / "m1.sql").write_text("SELECT 2")
        config = MetricConfig(
            name="m1",
            type="sample",
            columns={"variant": "g", "value": "v"},
            query_file="sql/m1.sql",
        )
        assert config.get_query_text(project_root=tmp_path) == "SELECT 2"

    def test_missing_query_file_raises(self, tmp_path):
        config = MetricConfig(
            name="m1",
            type="sample",
            columns={"variant": "g", "value": "v"},
            query_file="sql/absent.sql",
        )
        with pytest.raises(FileNotFoundError):
            config.get_query_text(project_root=tmp_path)


class TestNameAndTags:
    def test_name_charset(self):
        with pytest.raises(ValidationError, match="alphanumeric"):
            make("sample", {"variant": "g", "value": "v"}, name="bad name!")

    def test_name_length_budget(self):
        with pytest.raises(ValidationError, match="longer than 128"):
            make("sample", {"variant": "g", "value": "v"}, name="x" * 129)

    def test_duplicate_tags_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate tags"):
            make("sample", {"variant": "g", "value": "v"}, tags=["a", "a"])


class TestAaFprBudget:
    """The per-metric A/A budget override (M4/D12; declarative-config §8)."""

    def test_default_is_none(self):
        assert make("sample", {"variant": "g", "value": "v"}).aa_fpr_budget is None

    def test_accepts_a_fraction(self):
        assert make("sample", {"variant": "g", "value": "v"}, aa_fpr_budget=0.08).aa_fpr_budget == 0.08

    def test_accepts_upper_bound_one(self):
        assert make("sample", {"variant": "g", "value": "v"}, aa_fpr_budget=1.0).aa_fpr_budget == 1.0

    @pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
    def test_rejects_out_of_range(self, bad):
        with pytest.raises(ValidationError, match="aa_fpr_budget must be a fraction"):
            make("sample", {"variant": "g", "value": "v"}, aa_fpr_budget=bad)


class TestFromYaml:
    def test_round_trip(self, tmp_path):
        (tmp_path / "arpu.yml").write_text(
            """
name: arpu
description: "Average revenue per user"
type: sample
unit_key: user_id
tags: [revenue]
columns:
  variant: group
  value: gross_usd
query: "SELECT 1"
"""
        )
        config = MetricConfig.from_yaml_file(tmp_path / "arpu.yml")
        assert config.name == "arpu"
        assert config.type == "sample"
        assert config.unit_key == "user_id"

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            MetricConfig.from_yaml_file(tmp_path / "absent.yml")
