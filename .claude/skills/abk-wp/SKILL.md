---
name: abk-wp
description: >-
  Run one ab-analysis-kit work package end to end under the track's discipline:
  one WP = one session = one PR. Branch, implement, adversarially review, sync
  all three documentation bodies, update CHANGELOG/ROADMAP, run the full gate
  (pytest + the web bundle), open the PR, wait for all 10 CI jobs, squash-merge,
  then write the memory and the session handoff. Use whenever the maintainer
  says "continue with the plan", names a WP (UI-1, PERF-1, DASH-4, NTF-2, …),
  or asks to implement the next milestone item. Do NOT use for a release cut
  (that is `abk-release`) or for a bundle-only edit (that is `abk-web-bundle`).
---

# One work package, start to merged

The track runs on a hard rule: **one WP = one session = one PR**. Nothing carries
between sessions except the repository and the memory, so a WP that ends
half-done costs the next session an archaeology pass. This skill is that
session's shape.

## 0. Orient before writing anything

Read, in this order — they disagree only when something is stale, and finding
that is part of the job:

| Source | What it is |
|---|---|
| `ROADMAP.md` (the WP's own bullet) | the contract: scope, the trap the planner already saw, the size estimate |
| `.claude/rules/architecture.md` | the system **as built** — the "facts an assistant must know" blocks per milestone |
| `docs/specs/*.md` | the canonical design contract for the area |
| `CLAUDE.md` status section | what is released vs merged vs open |

Then `git log --oneline -5`, `git status`, `git branch -a`. The tree should be
clean and on `main`; if it is not, find out why before branching.

**Read what a gate DOES, not what is written about it.** UI-1's ROADMAP entry
predicted the WP would fail its own CI because the M11 invariant said "writes a
config" — but both gates had only ever checked the lock API. The prose was
wrong, not the gates. Every "this WP must first change X" note in the ROADMAP is
a hypothesis to verify against the code.

## 1. Branch

```bash
git checkout -b <type>/<wp-slug>
```

`feat/`, `fix/`, `perf/`, `chore/` — conventional commits scoped by package
(`feat(dashboard):`, `fix(stats):`).

## 2. Implement

House constraints that are not negotiable (`.claude/rules/architecture.md`
"Invariants"):

- `abkit.stats` stays pure — numpy/scipy/statsmodels/stdlib only.
- **Never change a statistical number silently**: any deviation from the
  baseline is an `ALGORITHM_VERSION` bump + a `docs/specs/statistics-changes.md`
  entry + a CHANGELOG entry + A/A validation. M7–M12 are explicitly *no-number*
  milestones — the parity gates are the proof, and `grep -rn ALGORITHM_VERSION`
  over the diff should come back empty.
- Methods are plugins; nothing special-cases a method name.
- Every cohort read goes through `build_cohort_backend`; the planner is reached
  only through `ExperimentConfig.grid()` (AST-gated).
- Comments explain constraints the code cannot show. Match the surrounding
  file's density — this repo writes dense *why*, never restated *what*.

Write the tests **as you go**, not after. Five of five defects in one exit-gate
WP were found while writing the gate, none while reading the code.

## 3. Test discipline

- Real HTTP against a threaded server for server code (never handler unit-fakes).
- **A test that cannot fail is worse than no test.** Two recorded instances: an
  allowlist assertion built from the same test data it checked, and a
  "schema is not created" assertion counting rows when `ensure_tables()` creates
  empty tables. If a test would pass against a hostile implementation, it is not
  a test — mutate the source and watch it go red.
- A gate that forbids something must be **proven to bite**: give it a hostile
  source fixture and assert it fires.
- A hand-maintained list (route names, method rosters, bundle namelists) rots
  silently. Derive it from the code (AST) or assert it against the code.

## 4. Adversarially review before you believe it

Fan out several independent lenses over `git diff main...HEAD` — concurrency,
filesystem/data-loss, the HTTP surface, the client, the domain contract,
documentation-vs-code — then **refute each finding** with a second agent that
defaults to "not real". Only what survives refutation gets fixed.

The maintainer may instead run `/code-review ultra` (billed, user-triggered);
you cannot launch it yourself.

Two recorded rules:
- **A lens with zero findings is a reason to re-run it deeper**, not a clean
  bill. One such lens returned a MAJOR on the second pass.
- **Do not edit code while a review is running** — the lenses' evidence goes
  stale and their line numbers stop matching.
- An empty workflow result is **not** a clean review: session limits and 529s
  kill lenses silently. Read `journal.jsonl` before concluding anything.

## 5. Sync the three documentation bodies

Drift is a defect here. Every behaviour change touches:

1. **`docs/**`** — the user body, rendered to the site.
2. **`.claude/rules/{architecture,contributing}.md`** — the contributor body.
3. **`abkit/cli/assets/claude/**`** — the packaged operator body, shipped in the
   wheel and written into a *user's* project by `abk init-claude`.

Plus `docs/specs/` (canonical design contract — an interstitial WP's contract
goes in `cli-and-dx.md`, following the `abk plan` sizing-gaps precedent, not in
a new `m*-implementation-plan.md`), `CHANGELOG.md` (authoritative for behaviour)
and `ROADMAP.md` (flip 📋 → ✅, record the PR and the as-built deltas).

`website/src/content/docs/` is a **generated, git-ignored mirror** — never edit it.

Grep for claims the WP just falsified (`grep -rn "read-only\|phase 2\|once at
boot" --include=*.md --include=*.py`). Code docstrings count as documentation.

## 6. The gate, before the PR

```bash
python3 -m pytest tests/ -q
```

```bash
cd web && npm run check && npm run build && npm test
```

Then confirm the committed bundles are fresh:

```bash
git status --porcelain -- ':(glob)abkit/*/assets/**'
```

Formatting: run `black`/`ruff` **only over the files you touched**.
`pre-commit run --all-files` drags in unrelated churn — the local ruff/isort is
stricter than CI's, and it silently reformatted seven foreign files in one
session. Always check `git diff --stat` afterwards and revert what is not yours.

## 7. PR, CI, merge

```bash
gh pr create --title "…" --body "…"
```

The body states: what shipped, the load-bearing as-built deltas (what the design
did not have and the build needed), the review outcome, and the gates that ran.

Wait for **all 10 CI jobs** — Test × Python 3.10/3.11/3.12, E2E (ClickHouse,
testcontainers), Bundle, Website, Lint & type-check, pip-install smoke × 3.
The ClickHouse leg cannot run locally (no Docker), so CI is the only place it
executes.

```bash
gh pr merge <n> --squash --delete-branch
```

Then `git checkout main && git pull && git remote prune origin`.

## 8. Memory and handoff — the session is not over without them

Update the project memory (`abkit-v2-track`, the current backlog memory): what
merged, the PR number and squash SHA, and **the lessons that transfer** — not a
diff summary. Then close with the standing ritual: a "next session" block, a
one-line explicit **effort** recommendation with its reason, and any question
for the maintainer stated plainly.

## Tool hazards recorded from real damage

- **`git checkout --` erases uncommitted work.** Done twice on this repo, losing
  three files once. To revert an experiment, back up to the scratchpad and
  restore from there.
- **`s.replace(s[i1:i2], new)` with the anchors in the wrong order** gives an
  empty `old`, so `new` is inserted between *every character* — one file went
  73 kB → 61 MB. Prefer the Edit tool; if you must slice, `assert i1 < i2`.
  (It is reversible with `s.replace(new, '')`, which is the only reason that
  session survived.)
- A mutation probe must **assert the edit landed**. A `print("applied")` after a
  no-op `replace` lied once and invalidated a whole verification pass.
