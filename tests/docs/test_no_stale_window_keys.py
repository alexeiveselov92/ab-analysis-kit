"""The renamed window keys must not survive anywhere a user copies from.

m10 WP1 renamed `start_date`/`end_date` to `start_ts`/`horizon_ts` with no
aliases, so a stale key in a doc, a packaged operator asset, or the `abk init`
scaffold is not cosmetic drift — it is a snippet that **fails validation** the
moment someone pastes it. The three-way sync gate next door checks *coverage*
(every operator rule has a docs home), never *content*; this one checks the
one piece of content that can silently rot.

Deliberately NOT covered (they are different objects that share a spelling):

- ``ab_start_date`` / ``ab_end_date`` — the day-partition SQL built-ins, an
  orthogonal and already-solved mechanism;
- ``_ab_results.start_date``/``end_date`` — derived columns dropped by WP3,
  not renamed (their successor is ``end_ts``, never ``horizon_ts``);
- historical records: past-milestone plans, ``docs/research/**``, released
  CHANGELOG sections. Rewriting those would falsify the record.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Surfaces a user reads, pastes from, or gets written into their own project.
_SURFACES = (
    "docs/guides",
    "docs/getting-started",
    "abkit/cli/assets/claude",
)
_SCAFFOLD = "abkit/cli/commands/init.py"

#: The YAML key form only — `start_date:` at the head of a config line. Prose
#: mentioning the old name while explaining the rename stays legal.
_STALE_KEY = re.compile(r"^\s*(start_date|end_date)\s*:", re.MULTILINE)


def _offenders(text: str, path: Path) -> list[str]:
    return [
        f"{path.relative_to(_REPO_ROOT)}:{text[: m.start()].count(chr(10)) + 1}: {m.group(0).strip()}"
        for m in _STALE_KEY.finditer(text)
    ]


def test_no_stale_window_keys_in_user_facing_surfaces():
    found: list[str] = []
    for surface in _SURFACES:
        for path in sorted((_REPO_ROOT / surface).rglob("*.md")):
            found += _offenders(path.read_text(), path)
    assert not found, (
        "`start_date:`/`end_date:` were renamed to `start_ts:`/`horizon_ts:` "
        "and no longer validate — these snippets would fail if pasted:\n  " + "\n  ".join(found)
    )


def test_the_init_scaffold_uses_the_current_keys():
    """`abk init` round-trips its own scaffold through validation, so a stale
    key here fails loudly — but only for whoever runs it. Catch it in CI."""
    scaffold = (_REPO_ROOT / _SCAFFOLD).read_text()
    assert not _offenders(scaffold, _REPO_ROOT / _SCAFFOLD)
    assert "start_ts: 2024-07-01" in scaffold
    assert "horizon_ts: 2024-07-15" in scaffold
    # D2: the anchor is written out explicitly, not left implicit
    assert "interval_anchor: midnight" in scaffold
