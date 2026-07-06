"""Tests for ``abk init-claude`` — Claude context scaffolding (M6 WP2).

Covers: fresh creation, idempotent re-runs, marker-based injection into an
existing CLAUDE.md (append + in-place stale-marker refresh) with user content
preserved, that the packaged rules/skills materialize, and that the index
(``CLAUDE.section.md``) stays in lockstep with the shipped rule/skill set.

Ported from the detectkit donor (tests/unit/test_init_claude.py); the mechanism
is domain-agnostic, the rule/skill set is abkit's.
"""

from pathlib import Path

from click.testing import CliRunner

from abkit.cli.commands.init_claude import _BLOCK_RE, _assets_root, run_init_claude
from abkit.cli.main import cli

# The 9 operator rules and 7 skills abkit ships (cli-and-dx.md §5 / m6 plan D1).
RULE_FILES = {
    "overview.md",
    "cli.md",
    "project.md",
    "experiments.md",
    "metrics.md",
    "methods.md",
    "explore.md",
    "validate.md",
    "plan.md",
}
SKILL_NAMES = {
    "abk-setup-project",
    "abk-new-experiment",
    "abk-new-metric",
    "abk-explore",
    "abk-validate",
    "abk-plan",
    "abk-feedback",
}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class TestFreshScaffold:
    def test_creates_all_artifacts(self, tmp_path):
        run_init_claude(str(tmp_path))

        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        text = _read(claude_md)
        # Exactly one managed block. The marker is intentionally version-less so a
        # no-op upgrade doesn't churn the block (see init_claude._BEGIN).
        assert text.count("<!-- BEGIN ab-analysis-kit") == 1
        assert text.count("<!-- END ab-analysis-kit -->") == 1
        assert "<!-- BEGIN ab-analysis-kit (managed by `abk init-claude`" in text
        assert _BLOCK_RE.search(text) is not None

        rules_dir = tmp_path / ".claude" / "rules" / "ab-analysis-kit"
        assert {p.name for p in rules_dir.glob("*.md")} == RULE_FILES

        for name in SKILL_NAMES:
            skill = tmp_path / ".claude" / "skills" / name / "SKILL.md"
            assert skill.exists(), f"missing skill {name}"
            assert f"name: {name}" in _read(skill), f"{name}/SKILL.md lacks its name frontmatter"

    def test_block_points_to_rules_and_skills(self, tmp_path):
        run_init_claude(str(tmp_path))
        text = _read(tmp_path / "CLAUDE.md")
        assert ".claude/rules/ab-analysis-kit/" in text
        for name in SKILL_NAMES:
            assert name in text, f"index block does not mention skill {name}"


class TestIdempotency:
    def test_rerun_changes_nothing(self, tmp_path):
        run_init_claude(str(tmp_path))
        before = {p: _read(p) for p in tmp_path.rglob("*") if p.is_file()}

        run_init_claude(str(tmp_path))
        after = {p: _read(p) for p in tmp_path.rglob("*") if p.is_file()}

        assert before.keys() == after.keys()
        assert before == after


class TestInjectionIntoExistingFile:
    def test_appends_block_and_preserves_user_content(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My rules\n\nAlways write tests.\n", encoding="utf-8")

        run_init_claude(str(tmp_path))
        text = _read(claude_md)

        assert "# My rules" in text
        assert "Always write tests." in text
        assert text.count("<!-- BEGIN ab-analysis-kit") == 1
        assert text.count("<!-- END ab-analysis-kit -->") == 1

    def test_refreshes_stale_block_in_place(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Top\n\nmine above.\n\n"
            "<!-- BEGIN ab-analysis-kit v0.0.1 (managed by `abk init-claude` — do not "
            "edit between these markers) -->\n"
            "OLD STALE CONTENT\n"
            "<!-- END ab-analysis-kit -->\n\n"
            "mine below.\n",
            encoding="utf-8",
        )

        run_init_claude(str(tmp_path))
        text = _read(claude_md)

        # User content on both sides preserved; stale body gone; single block.
        assert "mine above." in text
        assert "mine below." in text
        assert "OLD STALE CONTENT" not in text
        assert text.count("<!-- BEGIN ab-analysis-kit") == 1
        assert text.count("<!-- END ab-analysis-kit -->") == 1
        # The old versioned marker is replaced by the current version-less one.
        assert "v0.0.1" not in text
        assert "<!-- BEGIN ab-analysis-kit (managed by `abk init-claude`" in text


class TestPackagedAssetsInSync:
    """The shipped asset tree matches the declared rule/skill set and the index."""

    def test_shipped_tree_matches_declared_set(self):
        assets = _assets_root()
        rules = {c.name for c in assets.joinpath("rules").iterdir()}
        assert rules == RULE_FILES
        skills = {c.name for c in assets.joinpath("skills").iterdir() if c.is_dir()}
        assert skills == SKILL_NAMES

    def test_index_references_every_rule_and_skill(self):
        section = _assets_root().joinpath("CLAUDE.section.md").read_text(encoding="utf-8")
        for rule in RULE_FILES:
            assert rule in section, f"CLAUDE.section.md does not route to {rule}"
        for name in SKILL_NAMES:
            assert name in section, f"CLAUDE.section.md does not list skill {name}"

    def test_every_skill_has_matching_name_frontmatter(self):
        skills = _assets_root().joinpath("skills")
        for child in skills.iterdir():
            if child.is_dir():
                body = child.joinpath("SKILL.md").read_text(encoding="utf-8")
                assert body.lstrip().startswith("---"), f"{child.name} lacks frontmatter"
                assert f"name: {child.name}" in body, f"{child.name} name mismatch"


class TestCliWiring:
    def test_init_claude_command_runs(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["init-claude", "--target-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "CLAUDE.md").exists()
        assert (tmp_path / ".claude" / "skills" / "abk-new-metric" / "SKILL.md").exists()
