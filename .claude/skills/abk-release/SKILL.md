---
name: abk-release
description: >-
  Cut and publish an ab-analysis-kit release: bump `__version__`, cut
  `[Unreleased]` into a dated section, SWEEP the previous release's now-false
  status lines, run the full gate, PR → CI → squash-merge, then tag `vX.Y.Z` to
  trigger the OIDC trusted-publisher workflow, and verify the wheel from PyPI in
  a clean venv. Use when the maintainer asks to cut, tag, release or publish a
  version, or to "ship what is in [Unreleased]". Do NOT use to implement a work
  package (that is `abk-wp`).
---

# Cut a release

A release here is a **PR that changes only version metadata and prose**, merged
green, then a tag. `publish.yml` is triggered by `push: tags: ["v*"]` and
publishes to PyPI over an OIDC trusted publisher — so the tag is the release,
and a tag pushed before the version bump uploads a duplicate version that PyPI
rejects.

## 0. Preconditions

- `main`, clean tree, all WPs for this version merged.
- `CHANGELOG.md` `[Unreleased]` holds everything that is shipping. If it is
  empty there is nothing to release.
- Ask the maintainer before tagging unless they have already authorized it in
  this conversation.

## 1. The bump

`abkit/__init__.py` is the **single source** of `__version__`. Bump it there and
nowhere else. Check `pyproject.toml`'s Development Status classifier is still
current for the tier you are shipping.

## 2. Cut `[Unreleased]`

Rename it to `## [X.Y.Z] - YYYY-MM-DD`, keeping the Keep-a-Changelog sections
(`### Added` / `### Changed` / `### Fixed`). Do not summarize the entries — the
CHANGELOG is **authoritative for behaviour changes** and is what a user reads to
understand a number that moved.

**Editing near a CHANGELOG heading is delicate**: an Edit-tool insertion has
eaten the neighbouring heading before. Re-read the file around the seam.

## 3. Sweep the previous release's status lines — this is part of the cut

**This has bitten twice in three releases.** Status prose written during a
release cycle becomes false the moment the *next* one publishes, and it is the
quietest kind of wrong. Sweep every one of these:

| File | What goes stale |
|---|---|
| `CLAUDE.md` | the `## Status:` line, the "Next —" paragraph, per-milestone "Released as" sentences |
| `.claude/rules/architecture.md` | the header quote block (what is shipped / released / open), the "Next" paragraph |
| `ROADMAP.md` | the milestone/interstitial headers (📋 → 🚧 → ✅), the "pending" bullets |
| `README.md`, `docs/README.md` | any "latest on PyPI" claim |

The test is simple: **grep every version number in the repo and ask whether it
is still true.** `grep -rn "0\.6\.[0-9]" --include=*.md .`

## 4. The gate

```bash
python3 -m pytest tests/ -q
```

```bash
cd web && npm run build && npm test
```

The release-readiness e2e (`tests/e2e/test_release_readiness.py`) is the one
that matters most here: it asserts the committed bundles are self-contained and
that the packaging DoD holds.

**A new JS bundle must be added to two hand-maintained namelists**, neither of
which is derived from the build config — `.github/workflows/ci.yml` (the wheel
gate) and `tests/e2e/test_release_readiness.py`. Adding a bundle without
touching both ships a wheel that is missing it.

## 5. PR → CI → merge

```bash
gh pr create --title "chore(release): the X.Y.Z cut — <what it carries>" --body "…"
```

All 10 CI jobs must be green, including the three `pip install` smoke jobs
(clean-venv install across Python 3.10/3.11/3.12) — those are what prove the
wheel actually resolves.

```bash
gh pr merge <n> --squash --delete-branch
git checkout main && git pull
```

## 6. Tag — this is the publish

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

The tag must sit on the **squash-merge commit on `main`**, never on the branch
commit. Then watch the run:

```bash
gh run list --workflow=publish.yml --limit 3
```

## 7. Verify from PyPI, in a clean venv

Not from the working tree — from the index:

```bash
python3 -m venv /tmp/verify && /tmp/verify/bin/pip install -q ab-analysis-kit==X.Y.Z && /tmp/verify/bin/abk --version
```

Check the release's headline capability actually resolves (e.g. `abk ui --help`
for UI-2, `abk init-claude` for a packaged-asset change).

**The JSON API answers ~1 minute before the `/simple/` index pip reads** — a
failed install right after a successful publish is propagation lag, not a
failure. Retry once before diagnosing.

## 8. Record it

Update the memory: version, PR number, squash SHA, tag, "verified in a clean
venv", and what the release carried. Then the standing handoff block (next WP,
explicit effort, open questions).

## Known trap

A **pending** trusted publisher is not a project-level publisher. The first
publish of a project can 403 for this reason even though the config looks right —
it caused `0.1.0`'s failure. Once the project exists on PyPI, convert the pending
publisher to a project-scoped one.
