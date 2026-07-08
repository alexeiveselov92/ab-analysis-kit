"""Cross-body single-source drift gate (M6 WP9).

The "single source" contract is **three separately-authored Markdown bodies kept
in lockstep** (docs/specs/cli-and-dx.md §5, docs/specs/branding-and-site.md §1;
m6-implementation-plan.md D2 / WP3 hotspots / WP9), NOT one file rendered three
ways:

  (a) contributor rules  — ``.claude/rules/{architecture,contributing}.md``
  (b) operator rules     — ``abkit/cli/assets/claude/rules/*.md`` (shipped in the
      wheel, written into a *user's* project by ``abk init-claude``)
  (c) user docs body     — ``docs/**`` (rendered on the site by ``sync-docs``)

No content is machine-generated across the bodies; human review enforces
*agreement*. This gate — promised in the WP8 CHANGELOG/spec prose as "landing in
WP9" — enforces *coverage*: **every operator rule topic in (b) has a
corresponding published guide/reference page in (c)**, so a new operator rule
cannot ship without a user-facing documentation home (and a docs page cannot be
deleted out from under the rule that points readers at the deeper treatment).

The reverse direction is deliberately NOT required: (c) legitimately carries
pages with no operator rule of their own (e.g. sequential analysis, reading a
readout, notification channels, visualizing results, installation/quickstart).
"""

from pathlib import Path

from abkit.cli.commands.init_claude import _assets_root

# tests/docs/test_docs_single_source.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Each packaged operator rule (b) -> the docs page(s) (c) that give its topic a
# published home. Keyed by rule filename; values are repo-relative doc paths.
# Keep in lockstep with the shipped rule set (the coverage assertion below fails
# loudly if a rule is added/removed without updating this map).
RULE_TO_DOCS: dict[str, tuple[str, ...]] = {
    "overview.md": ("docs/README.md",),
    "cli.md": ("docs/reference/cli.md",),
    # the operator "project" rule covers both project config and the profiles /
    # database connection surface
    "project.md": ("docs/guides/configuration.md", "docs/guides/databases.md"),
    "experiments.md": ("docs/guides/experiments.md",),
    "metrics.md": ("docs/guides/metrics.md",),
    "methods.md": ("docs/guides/compute-methods.md",),
    "explore.md": ("docs/guides/explore.md",),
    "validate.md": ("docs/guides/validate.md",),
    "plan.md": ("docs/guides/plan.md",),
}


def _shipped_rule_files() -> set[str]:
    rules_dir = _assets_root().joinpath("rules")
    return {p.name for p in rules_dir.iterdir() if p.suffix == ".md"}


class TestDocsSingleSource:
    """The (b) operator rules ↔ (c) user docs coverage gate."""

    def test_mapping_covers_exactly_the_shipped_operator_rules(self):
        """A rule added or removed without updating RULE_TO_DOCS fails here.

        This is the forcing function: you cannot ship a new operator rule
        without consciously choosing (and asserting) its docs home.
        """
        shipped = _shipped_rule_files()
        mapped = set(RULE_TO_DOCS)
        missing = shipped - mapped
        extra = mapped - shipped
        assert (
            not missing
        ), f"operator rules with no docs mapping (add them to RULE_TO_DOCS): {sorted(missing)}"
        assert not extra, f"RULE_TO_DOCS names rules that are no longer shipped: {sorted(extra)}"

    def test_every_operator_rule_has_an_existing_docs_page(self):
        """Every mapped docs page exists and is non-trivial (real coverage)."""
        for rule, doc_paths in sorted(RULE_TO_DOCS.items()):
            assert doc_paths, f"{rule} maps to no docs page"
            for rel in doc_paths:
                doc = _REPO_ROOT / rel
                assert doc.is_file(), f"{rule}: missing docs page {rel}"
                assert doc.read_text(encoding="utf-8").strip(), f"{rule}: empty docs page {rel}"
