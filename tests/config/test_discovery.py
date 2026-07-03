"""Discovery + two-level selector tests (cli-and-dx §1)."""

from __future__ import annotations

from pathlib import Path

from abkit.config.discovery import find_project_root, select_experiments

EXPERIMENT_YML = """
name: {name}
tags: [{tags}]
start_date: 2024-07-01
end_date: 2024-07-28
unit_key: user_id
assignment:
  query: "SELECT user_id, variant, exposure_ts FROM a"
  variants: [control, treatment]
  expected_split: {{control: 0.5, treatment: 0.5}}
comparisons:
  - metric: m1
    is_main_metric: true
    method: {{name: z-test, params: {{test_type: relative}}}}
"""


def scaffold(tmp_path: Path, experiments: dict[str, str]) -> Path:
    (tmp_path / "abkit_project.yml").write_text("name: p\ndefault_profile: dev\n")
    exp_dir = tmp_path / "experiments"
    exp_dir.mkdir()
    for name, tags in experiments.items():
        (exp_dir / f"{name}.yml").write_text(EXPERIMENT_YML.format(name=name, tags=tags))
    return tmp_path


class TestFindProjectRoot:
    def test_walks_up_from_nested_dir(self, tmp_path):
        root = scaffold(tmp_path, {"exp1": "growth"})
        nested = root / "experiments" / "sub"
        nested.mkdir()
        assert find_project_root(nested) == root

    def test_none_outside_a_project(self, tmp_path):
        assert find_project_root(tmp_path) is None


class TestSelectExperiments:
    def test_default_selects_all(self, tmp_path):
        root = scaffold(tmp_path, {"exp1": "growth", "exp2": "ops"})
        selected, warnings = select_experiments(root, select=())
        assert [c.name for _, c in selected] == ["exp1", "exp2"]
        assert warnings == []

    def test_select_by_name(self, tmp_path):
        root = scaffold(tmp_path, {"exp1": "growth", "exp2": "ops"})
        selected, _ = select_experiments(root, select=("exp2",))
        assert [c.name for _, c in selected] == ["exp2"]

    def test_select_by_name_field_in_subdir(self, tmp_path):
        root = scaffold(tmp_path, {"exp1": "growth"})
        sub = root / "experiments" / "team"
        sub.mkdir()
        (sub / "renamed_file.yml").write_text(EXPERIMENT_YML.format(name="exp_nested", tags="ops"))
        selected, _ = select_experiments(root, select=("exp_nested",))
        assert [c.name for _, c in selected] == ["exp_nested"]

    def test_select_by_tag(self, tmp_path):
        root = scaffold(tmp_path, {"exp1": "growth", "exp2": "ops", "exp3": "growth"})
        selected, _ = select_experiments(root, select=("tag:growth",))
        assert [c.name for _, c in selected] == ["exp1", "exp3"]

    def test_select_by_glob(self, tmp_path):
        root = scaffold(tmp_path, {"exp1": "growth", "other": "ops"})
        selected, _ = select_experiments(root, select=("exp*.yml",))
        assert [c.name for _, c in selected] == ["exp1"]

    def test_exclude(self, tmp_path):
        root = scaffold(tmp_path, {"exp1": "growth", "exp2": "ops"})
        selected, _ = select_experiments(root, select=("*",), exclude=("exp1",))
        assert [c.name for _, c in selected] == ["exp2"]

    def test_unmatched_selector_names_the_namespace(self, tmp_path):
        root = scaffold(tmp_path, {"exp1": "growth"})
        selected, warnings = select_experiments(root, select=("ghost",))
        assert selected == []
        assert any("--metric" in w for w in warnings)

    def test_history_archive_never_selected(self, tmp_path):
        root = scaffold(tmp_path, {"exp1": "growth"})
        history = root / "experiments" / ".history" / "exp1"
        history.mkdir(parents=True)
        (history / "v1.yml").write_text(EXPERIMENT_YML.format(name="exp1", tags="growth"))
        selected, _ = select_experiments(root, select=("*",))
        assert len(selected) == 1
