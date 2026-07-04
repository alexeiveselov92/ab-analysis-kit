"""Validator level-1 tests: discovery, uniqueness, the shared namespace."""

from __future__ import annotations

from pathlib import Path

import pytest

from abkit.config.validator import (
    discover_config_files,
    is_discoverable_config_file,
    validate_project_configs,
)

EXPERIMENT_YML = """
name: {name}
start_date: 2024-07-01
end_date: 2024-07-28
unit_key: user_id
assignment:
  query: "SELECT user_id, variant, exposure_ts FROM a"
  variants: [control, treatment]
  expected_split: {{control: 0.5, treatment: 0.5}}
comparisons:
  - metric: signup_cr
    is_main_metric: true
    method: {{name: z-test, params: {{test_type: relative}}}}
"""

METRIC_YML = """
name: {name}
type: fraction
columns:
  variant: variant
  count: conversions
  nobs: visits
query: "SELECT 1"
"""


def scaffold(tmp_path: Path, experiments: list[str], metrics: list[str]) -> Path:
    (tmp_path / "experiments").mkdir(exist_ok=True)
    (tmp_path / "metrics").mkdir(exist_ok=True)
    for name in experiments:
        (tmp_path / "experiments" / f"{name}.yml").write_text(EXPERIMENT_YML.format(name=name))
    for name in metrics:
        (tmp_path / "metrics" / f"{name}.yml").write_text(METRIC_YML.format(name=name))
    return tmp_path


class TestDiscovery:
    def test_history_archive_excluded(self, tmp_path):
        root = scaffold(tmp_path, ["exp1"], ["signup_cr"])
        history = root / "experiments" / ".history" / "exp1"
        history.mkdir(parents=True)
        (history / "exp1_v1.yml").write_text(EXPERIMENT_YML.format(name="exp1"))

        files = discover_config_files(root / "experiments")
        assert [f.name for f in files] == ["exp1.yml"]

    def test_hidden_dir_under_base_excluded_but_dotted_root_ok(self, tmp_path):
        root = (tmp_path / ".dotted-project").resolve()
        root.mkdir()
        scaffold(root, ["exp1"], ["signup_cr"])
        files = discover_config_files(root / "experiments")
        assert len(files) == 1  # the dotted project root itself doesn't exclude

    def test_non_yaml_ignored(self, tmp_path):
        root = scaffold(tmp_path, ["exp1"], ["signup_cr"])
        (root / "experiments" / "notes.md").write_text("hi")
        assert len(discover_config_files(root / "experiments")) == 1

    def test_is_discoverable_direct(self, tmp_path):
        base = tmp_path / "experiments"
        base.mkdir()
        good = base / "a.yml"
        good.write_text("x: 1")
        hidden = base / ".history" / "a.yml"
        hidden.parent.mkdir()
        hidden.write_text("x: 1")
        assert is_discoverable_config_file(good, base)
        assert not is_discoverable_config_file(hidden, base)


class TestUniqueness:
    def test_valid_project_parses(self, tmp_path):
        root = scaffold(tmp_path, ["exp1", "exp2"], ["signup_cr", "arpu"])
        experiments, metrics = validate_project_configs(root)
        assert [c.name for _, c in experiments] == ["exp1", "exp2"]
        assert [c.name for _, c in metrics] == ["arpu", "signup_cr"]

    def test_duplicate_experiment_names(self, tmp_path):
        root = scaffold(tmp_path, ["exp1"], ["signup_cr"])
        (root / "experiments" / "copy.yml").write_text(EXPERIMENT_YML.format(name="exp1"))
        with pytest.raises(ValueError, match="Duplicate experiment name 'exp1'"):
            validate_project_configs(root)

    def test_cross_namespace_collision(self, tmp_path):
        root = scaffold(tmp_path, ["signup_cr"], ["signup_cr"])
        with pytest.raises(ValueError, match="BOTH an experiment and a metric"):
            validate_project_configs(root)

    def test_parse_error_names_the_file(self, tmp_path):
        root = scaffold(tmp_path, ["exp1"], ["signup_cr"])
        (root / "metrics" / "broken.yml").write_text("name: [unclosed")
        with pytest.raises(ValueError, match="broken.yml"):
            validate_project_configs(root)

    def test_missing_directories(self, tmp_path):
        (tmp_path / "experiments").mkdir()
        (tmp_path / "experiments" / "e.yml").write_text(EXPERIMENT_YML.format(name="e"))
        with pytest.raises(FileNotFoundError, match="Metrics directory not found"):
            validate_project_configs(tmp_path)

    def test_empty_experiments_dir(self, tmp_path):
        (tmp_path / "experiments").mkdir()
        (tmp_path / "metrics").mkdir()
        (tmp_path / "metrics" / "m.yml").write_text(METRIC_YML.format(name="m"))
        with pytest.raises(ValueError, match="No experiment files found"):
            validate_project_configs(tmp_path)
