"""ProjectConfig tests: statistical defaults, limits, compute mode gating."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from abkit.config import ProjectConfig


def make(**overrides) -> ProjectConfig:
    payload = {"name": "proj", "default_profile": "dev"}
    payload.update(overrides)
    return ProjectConfig.model_validate(payload)


class TestDefaults:
    def test_paths_and_tables_defaults(self):
        config = make()
        assert config.paths.experiments == "experiments"
        assert config.paths.metrics == "metrics"
        assert config.tables.results == "_ab_results"
        assert config.tables.unit_state == "_ab_unit_state"

    def test_statistical_defaults(self):
        config = make()
        assert config.statistics.alpha == 0.05
        assert config.statistics.test_type == "relative"
        assert config.statistics.correction == "bonferroni"
        assert config.statistics.power == 0.8

    def test_limit_defaults(self):
        config = make()
        assert config.limits.max_looks == 5000
        assert config.limits.warn_looks == 100
        assert config.limits.min_units_per_arm == 100

    def test_compute_default_is_recompute(self):
        assert make().compute.mode == "recompute"


class TestValidation:
    def test_alpha_range(self):
        with pytest.raises(ValidationError, match="fraction in"):
            make(statistics={"alpha": 1.2})

    def test_power_range(self):
        with pytest.raises(ValidationError, match="fraction in"):
            make(statistics={"power": 0.0})

    def test_incremental_mode_rejected_in_v1(self):
        with pytest.raises(ValidationError):
            make(compute={"mode": "incremental"})

    def test_limits_positive(self):
        with pytest.raises(ValidationError, match="at least 1"):
            make(limits={"max_looks": 0})

    def test_timeout_bounds(self):
        with pytest.raises(ValidationError, match="at least 1 second"):
            make(timeouts={"load": 0})
        with pytest.raises(ValidationError, match="cannot exceed 24 hours"):
            make(timeouts={"compute": 90000})

    def test_name_charset(self):
        with pytest.raises(ValidationError, match="alphanumeric"):
            make(name="bad/name")


class TestFromYaml:
    def test_round_trip(self, tmp_path):
        (tmp_path / "abkit_project.yml").write_text(
            """
name: "demo"
default_profile: "dev"
statistics:
  alpha: 0.01
limits:
  warn_looks: 50
"""
        )
        config = ProjectConfig.from_yaml_file(tmp_path / "abkit_project.yml")
        assert config.statistics.alpha == 0.01
        assert config.limits.warn_looks == 50
        assert config.limits.max_looks == 5000  # untouched default

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ProjectConfig.from_yaml_file(tmp_path / "absent.yml")

    def test_empty_file(self, tmp_path):
        (tmp_path / "abkit_project.yml").write_text("")
        with pytest.raises(ValueError, match="Empty project config"):
            ProjectConfig.from_yaml_file(tmp_path / "abkit_project.yml")
