"""The renamed window keys must not survive anywhere a user copies from.

m10 WP1 renamed `start_date`/`end_date` to `start_ts`/`horizon_ts` with no
aliases, so a stale key in a doc, a packaged operator asset, or the `abk init`
scaffold is not cosmetic drift — it is a snippet that **fails validation** the
moment someone pastes it. The three-way sync gate next door checks *coverage*
(every operator rule has a docs home), never *content*; this one checks the
one piece of content that can silently rot.

m10 WP3 then dropped ``_ab_results.start_date``/``end_date``, so on these same
surfaces the *column* spelling is dead too — and it hides in a different shape.
The key check below is anchored to ``^\\s*name:``, which a
``SELECT metric, end_date, …`` sails straight past. That gap was not
hypothetical: a query in ``docs/getting-started/quickstart.md`` — valid until
WP3 dropped the column out from under it — was invisible to every gate in the
repo and had to be found by an audit. The second gate closes the shape by
banning the bare identifier outright on the paste surfaces, in any syntax.

Deliberately NOT covered (they are different objects that share a spelling):

- ``ab_start_date`` / ``ab_end_date`` — the day-partition SQL built-ins, an
  orthogonal and already-solved mechanism;
- ``docs/reference/`` and ``docs/specs/`` — they must be free to *name* the
  removed columns while documenting the removal and its replacement;
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

#: Surfaces above, plus the shipped BI recipes — every one of them a place a
#: user copies SQL from. Checked by the identifier gate only.
_SQL_SURFACES = _SURFACES + ("docs/examples",)

#: The YAML key form only — `start_date:` at the head of a config line. Prose
#: mentioning the old name while explaining the rename stays legal.
_STALE_KEY = re.compile(r"^\s*(start_date|end_date)\s*:", re.MULTILINE)

#: The bare identifier in ANY syntax — a SELECT list, a GROUP BY, a qualified
#: `r.start_date`, prose. `\b` is doing the load-bearing work: `_` is a word
#: character, so there is NO word boundary inside `ab_start_date` and the live
#: day-partition built-ins are excluded for free. (An earlier draft added a
#: `(?<!ab_)` lookbehind for this; it could never change an outcome.)
_STALE_IDENTIFIER = re.compile(r"\b(start_date|end_date)\b")


def _offenders(text: str, path: Path, pattern: re.Pattern[str] = _STALE_KEY) -> list[str]:
    return [
        f"{path.relative_to(_REPO_ROOT)}:{text[: m.start()].count(chr(10)) + 1}: {m.group(0).strip()}"
        for m in pattern.finditer(text)
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


def test_no_dropped_result_columns_in_pasteable_sql():
    """m10 WP3: `_ab_results.start_date`/`end_date` no longer exist.

    A `SELECT metric, end_date, …` in a guide is not drift — on ClickHouse it
    is an error the reader hits on their first copy-paste, and on a
    not-yet-recreated table it is worse: the column is still there, silently
    holding `1970-01-01`. The key gate above cannot see this shape, which is
    how one survived a whole milestone.
    """
    found: list[str] = []
    scanned = 0
    for surface in _SQL_SURFACES:
        root = _REPO_ROOT / surface
        assert root.is_dir(), (
            f"surface {surface!r} does not exist — a renamed or moved directory "
            "would otherwise turn this gate into a silent no-op"
        )
        for path in sorted(p for p in root.rglob("*") if p.suffix in {".md", ".sql", ".json"}):
            found += _offenders(path.read_text(), path, _STALE_IDENTIFIER)
            scanned += 1
    scaffold = _REPO_ROOT / _SCAFFOLD
    found += _offenders(scaffold.read_text(), scaffold, _STALE_IDENTIFIER)
    scanned += 1
    # the gate is only as good as its reach: a green run over 0 files is a
    # broken gate, not a clean tree
    assert scanned > 15, f"only {scanned} files scanned — the surface list has rotted"
    assert not found, (
        "`_ab_results.start_date`/`end_date` were REMOVED in 0.5.0 — the cutoff "
        "key is `end_ts`, and the calendar day it covers is `end_ts - 1µs` read "
        "in the experiment timezone (docs/reference/internal-tables.md). These "
        "would break if pasted:\n  " + "\n  ".join(found)
    )


def test_the_identifier_gate_can_actually_fail():
    """The gate above passes today; prove that is a property of the tree and
    not of a regex that matches nothing — and that it still ignores the live
    SQL built-ins, which share the spelling and must never be flagged."""
    # the shapes a dropped column actually takes in a doc
    assert _STALE_IDENTIFIER.search("SELECT metric, end_date, effect FROM _ab_results")
    assert _STALE_IDENTIFIER.search("GROUP BY start_date")
    assert _STALE_IDENTIFIER.search("ORDER BY r.end_date DESC")  # table-qualified
    assert _STALE_IDENTIFIER.search('SELECT "start_date"')  # quoted identifier
    # ...and the built-ins it must stay blind to, in the forms they appear in
    assert not _STALE_IDENTIFIER.search("WHERE event_date >= '{{ ab_start_date }}'")
    assert not _STALE_IDENTIFIER.search("{{ ab_end_date }}")
    # NOTE the deliberate asymmetry: an attribute access like the live
    # `RenderWindow.start_date` IS matched, because a regex cannot tell it from
    # the table-qualified `r.start_date` above. That is the right trade for
    # these surfaces — no paste surface names that property — but it is why
    # `docs/reference/` and `docs/specs/` stay out of scope.


def test_the_init_scaffold_uses_the_current_keys():
    """`abk init` round-trips its own scaffold through validation, so a stale
    key here fails loudly — but only for whoever runs it. Catch it in CI."""
    scaffold = (_REPO_ROOT / _SCAFFOLD).read_text()
    assert not _offenders(scaffold, _REPO_ROOT / _SCAFFOLD)
    assert "start_ts: 2024-07-01" in scaffold
    assert "horizon_ts: 2024-07-15" in scaffold
    # D2: the anchor is written out explicitly, not left implicit
    assert "interval_anchor: midnight" in scaffold
