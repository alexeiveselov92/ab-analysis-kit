"""Helpers for tests that drive a real ``abk init`` scaffold."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_FLAG_RE = re.compile(r"^(?P<indent>[ \t]*)incremental_reads:[ \t]*(?:true|false)[ \t]*$", re.M)


def set_incremental_reads(project_yml: Path, enabled: bool) -> None:
    """Set ``compute.incremental_reads`` in a SCAFFOLDED project config.

    PERF-1 flipped the scaffold to ``true``, so a test that wants the recompute
    path must now say so explicitly. These tests used to APPEND a second
    ``compute:`` block, which is a duplicate YAML key whose winner is a PyYAML
    implementation detail — and, once the scaffold declared the flag itself,
    a "flag off" leg that silently agreed with the "flag on" leg. A parity
    gate comparing a path against itself cannot fail.

    Rewrites the scaffold's own line and asserts BOTH that the substitution
    landed and that the parsed result says what was asked, so a scaffold that
    stops emitting the flag fails here instead of quietly reverting some other
    test's leg.
    """
    text = project_yml.read_text(encoding="utf-8")
    replacement = f"\\g<indent>incremental_reads: {'true' if enabled else 'false'}"
    rewritten, count = _FLAG_RE.subn(replacement, text, count=1)
    assert count == 1, (
        "the scaffold no longer writes a `compute.incremental_reads` line — "
        "update tests/_helpers/scaffold.py together with abk init"
    )
    project_yml.write_text(rewritten, encoding="utf-8")
    parsed = yaml.safe_load(rewritten)
    assert parsed["compute"]["incremental_reads"] is enabled, parsed.get("compute")


def unset_incremental_reads(project_yml: Path) -> None:
    """Drop the ``compute:`` block entirely — the UNDECIDED project.

    This is what every project scaffolded before PERF-1 looks like, and the
    state the `abk run` hint exists to talk about. Distinct from an explicit
    ``false``, which is a recorded decision and stays quiet.
    """
    document = yaml.safe_load(project_yml.read_text(encoding="utf-8"))
    assert document.pop("compute", None) is not None, "the scaffold wrote no compute block"
    project_yml.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
