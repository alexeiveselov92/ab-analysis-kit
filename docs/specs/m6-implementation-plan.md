# M6 Implementation Plan — DX, docs, orchestration, release

> The as-designed contract for M6, in the shape of
> [m5-implementation-plan.md](m5-implementation-plan.md) /
> [m4-implementation-plan.md](m4-implementation-plan.md). Canonical for M6 work.
> Governing specs: [cli-and-dx.md](cli-and-dx.md),
> [branding-and-site.md](branding-and-site.md),
> [data-contract-and-reporting.md §2–§4](data-contract-and-reporting.md),
> [ROADMAP.md](../../ROADMAP.md) M6. Keep `.claude/rules/` + ROADMAP + CHANGELOG +
> `CLAUDE.md` status line in sync at the WP10 exit gate.
> Donor: `/home/aleksei/wsl_analytics/detektkit` (import pkg `detectkit`).

## 0. Scope, posture & decisions

**M6 is DX / docs / orchestration / release. Zero statistical-number changes.**
No `ALGORITHM_VERSION` moves, no golden retolerancing, `abkit.stats` purity intact
(`tests/stats/test_purity.py`). Every deliverable is either an additive command,
an authored Markdown/asset body, or CI/release plumbing. The only *irreversible,
external* actions in M6 — the **PyPI publish** and the **live website deploy** — are
carved out as maintainer-confirmation-required gates (**G1/G2**), never taken
autonomously by an implementation agent.

### 0.1 What already exists (do not rebuild)

- **Packaging is wired.** `pyproject.toml` `[tool.setuptools.package-data]` already
  declares `"abkit.cli" = ["assets/**/*.md"]`, `"abkit.loaders"`, `"abkit.reporting"`,
  `"abkit.tuning"`; `MANIFEST.in` mirrors all four. The `assets/claude/` tree is the
  only missing piece — populate it and it ships with **no config change** (verify the
  recursive glob in a real `python -m build`, WP2).
- **`publish.yml`** is byte-identical to the donor's working tag-triggered OIDC
  trusted-publish job (`on: push: tags: ["v*"]`, environment `pypi`,
  `permissions: id-token: write`, `python -m build` → `pypa/gh-action-pypi-publish`).
  No port needed; only the PyPI-side trusted-publisher registration + the `pypi`
  GitHub environment need confirming (open question).
- **`website.yml`** already fires on `push: branches:[main] paths:['website/**']` (the
  path filter is **already** `website/**` — it is NOT dormant). Its `build-and-push` job
  is guarded on `[ -f website/Dockerfile ]`: **the Dockerfile's presence on `main` is the
  real build/deploy switch.** When it is present the job (a) pushes
  `ghcr.io/alexeiveselov92/ab-analysis-kit-web` using the always-available `GITHUB_TOKEN`
  (no maintainer secret needed — the push *will* succeed) and (b) `notify-pipelab` `curl -f`s
  a `repository_dispatch` (`event_type: ab-analysis-kit-updated`) to `alexeiveselov92/pipelab`
  with `PIPELAB_DISPATCH_TOKEN`; if that secret is unset the `-f` returns 401 and the job
  **reds `main`**. **Consequence (plan-review finding, all 3 critics):** merging
  `website/Dockerfile` to `main` is itself an autonomous ghcr publish + deploy dispatch, and
  reds main when the token is absent. Therefore WP7 lands the whole site **without** the
  Dockerfile, and hardens `website.yml` (environment protection + non-empty-secret guard)
  **before** any `website/**` merge; the Dockerfile + `paths:` widening are **G2** steps
  (see WP7 hotspots + §GATED / G2). The neutral phrasing "widen paths when the site lands"
  in the workflow comment is misleading — the path filter already matches; do not treat it
  as the gate.
- **The framework-free renderer core** already lives in `web/` (M3):
  `web/src/shared/chart.ts` (canvas primitives + `TOKEN_FALLBACKS` under abkit's own
  `--abk-*` token names), built by `web/build.mjs` into `abkit/reporting/assets/report.js`
  (`__ABK_REPORT__`) and `abkit/tuning/assets/explore.js` (`__ABK_EXPLORE__`). This is a
  deliberate M3 divergence from detectkit (which co-locates renderers under `website/`).
  **Do not re-port `canvas.ts` or the `gen-*-bundle.mjs` scripts** — the M6 site *consumes*
  `web/` as the seed (D3).
- **CI (`.github/workflows/ci.yml`)** has `test` (Py 3.10/3.11/3.12), `e2e-clickhouse`
  (testcontainers), `bundle` (web freshness via `git status --porcelain -- ':(glob)abkit/*/assets/**'`
  + marker-class grep `abk-prehorizon`/`abk-insufficient`/`abk-srm-fail` + the
  `TOKEN_FALLBACKS` hex-containment loop + jsdom smoke), and `lint` (`ruff check abkit`,
  `black --check abkit`, `mypy abkit` **continue-on-error**, plus a `pip wheel` gate that
  asserts `report.js`/`explore.js` ship). The wheel gate does **not** yet assert the
  claude assets (WP2 fixes this).
- **`__version__ = "0.0.1.dev0"`** in `abkit/__init__.py` (single source, PEP-562
  numpy-free). Name `ab-analysis-kit` is reserved via that placeholder.

### 0.2 What does not exist yet (the M6 build)

`abkit/cli/commands/init_claude.py`, `abkit/cli/assets/claude/**` (empty), the
`abk test-report` command + any channel infra, `website/`, the entire user-facing
`docs/{README,getting-started,guides,reference,examples}` tree, `docs/examples/bi/`,
a committed Prefect **deployment** artifact (only a bare flow is scaffolded by
`abk init`), and `.claude/rules/design.md`.

### 0.3 Scope answers proposed to the maintainer (assumed pending confirmation — see §7)

> These three are the plan's **assumed** answers, not evidenced maintainer decisions
> (plan-review, critic 0). Implementation proceeds on them, but they are re-surfaced in §7
> (Q1/Q3/Q10) for explicit sign-off; a different call reshapes WP7/WP8/WP9.

1. **Scope decision (maintainer-confirmed 2026-07-06): TWO of the three M5-named "M6"
   items ARE feature work in M6; only `alpha_spending` stays deferred.**
   - **IN M6:** `abk plan` **runtime/ASN** (new **WP-A**) and the A/A **sequential ×
     composed** sweep (new **WP-B**) — both promised "→ M6" by the specs, now honoured.
   - **DEFERRED to v2/future:** `alpha_spending`/group-sequential only (the costliest —
     a new stats-core estimator + `ALGORITHM_VERSION` + full A/A validation + goldens).
   WP8 re-points **only** the `alpha_spending` "planned for M6" strings to a clean v2
   message; WP-A/WP-B strings flip to *shipped*, not deferred. (See §6, thinned to one item.)
2. **The site ships on a placeholder brand-token layer.** Final palette + logo/lockups
   are finalized separately in Claude design (`branding-and-site.md §2/§5`). WP7 builds
   the whole site + demo on the existing neutral `--abk-*` placeholders with a **clean
   single-file swap seam** (`website/src/styles/brand.css`), and the final palette/logo
   drop-in is a follow-up commit that must not touch logic.
3. **First real release version is `0.1.0`** (must exceed the `0.0.1.dev0` placeholder
   or PyPI rejects the upload). Development Status classifier moves `2 - Pre-Alpha` →
   `3 - Alpha` (donor's label). Confirmed in WP9.

### 0.5 Plan-review record (pre-implementation, 3 adversarial critics)

This plan was adversarially reviewed before implementation (the M4/M5 discipline). All three
critics returned **sound-with-fixes**; the fixes are folded in above. The load-bearing ones,
**verified against the live repo**:

- **`website.yml` auto-fires on merge (all 3 critics, CONFIRMED).** The workflow already
  triggers on `push … paths:['website/**']`; its `[ -f website/Dockerfile ]` guard is the
  real deploy switch. Merging the Dockerfile publishes the ghcr image (via `GITHUB_TOKEN`,
  no secret) and dispatches the pipelab deploy — and reds `main` when `PIPELAB_DISPATCH_TOKEN`
  is unset. **Fix:** hold the Dockerfile off `main` until G2; harden `website.yml` first
  (D-CI); corrected §0.1 / WP7 / G2. The plan's original "widen paths when the site lands"
  framing was wrong (paths already match).
- **npm-workspace conversion breaks the existing `bundle` job (critic 2, CONFIRMED).** `bundle`
  runs `npm ci` in `working-directory: web` with `cache-dependency-path: web/package-lock.json`;
  the workspace moves the lockfile to root. **Fix:** WP7/D3 now mandate updating `bundle` in
  the same PR.
- **WP7 astro link-check depends on WP4, not just WP3 (critic 2).** `docs/examples/**` links.
  **Fix:** WP4 is now a WP7 prerequisite in the graph + WP7 goal.
- **WP1 mypy fix is single-lever (critic 2).** mypy 1.10.0 may not parse PEP-695 stubs even at
  py3.12. **Fix:** treat the mypy version as a variable with a bump fallback; defer the
  `continue-on-error` flip to WP9 so intervening WPs aren't retro-blocked.
- **Single-source spec vs as-built (critic 0).** The specs promise machine-lockstep single
  source; the plan ships three coverage-gated bodies. **Fix:** WP8 amends `cli-and-dx §5` +
  `branding-and-site §1`; raised as §7 Q11.
- **Accessibility unassigned (critic 0), BI per-tool reduction (critic 0), error-notification
  deferral (critic 0), demo-parity must be a hard gate (critic 1).** All folded into
  WP7/WP4/WP8/D-Brand + §7.

The M6 **exit-gate** review (§5) is separate and still runs ≥2 full rounds at WP10.

---

## 1. Work packages in strict dependency order

Safe, additive, fully-in-repo work first; external/irreversible work last and gated.

### WP1 — Release-readiness tooling debt: green pre-commit (`mypy`, `black`) — independent, first

**Goal:** the release checklist depends on a clean `pre-commit run --all-files`; close
the two recorded debts (ROADMAP "Tooling debt") so the type-check and formatter agree
locally and in CI before anything else lands.

| Source | Target | Verdict |
|---|---|---|
| `pyproject.toml` `[tool.mypy] python_version = "3.10"` | raise to `"3.12"` (numpy 2.5 PEP-695 `type X = …` stubs are rejected under 3.10) | Edit |
| `.pre-commit-config.yaml` mirrors-mypy v1.10.0 | **mypy version is a variable, not a given (plan-review finding):** verify empirically that 3.12 clears the stub error under 1.10.0. mypy 1.10.0 (mid-2024) predates *stable* PEP-695 `type X = …` support, so it may still fail to parse numpy 2.5's stubs even at `python_version=3.12`. **Fallback:** bump mirrors-mypy (and the `[dev]` mypy pin) to a release with stable PEP-695 support | Verify / Edit |
| `.pre-commit-config.yaml` black rev `24.4.2` **vs** `[dev]` extra `black>=23.0` | pin **one** version in both (`black==24.4.2` in the `[dev]` extra) so pre-commit ↔ CI never diverge. **Pin `mypy` identically** in `.pre-commit-config.yaml` and `[dev]` the same way | Edit |
| `.github/workflows/ci.yml` `lint` job `mypy abkit` `continue-on-error: true` | **flip deferred to WP9 (plan-review finding).** WP1 only makes `mypy abkit` *actually clean*; flipping `continue-on-error` off in WP1 would retro-red-block WP2/WP5/WP6's new Python. The flip decision lands in WP9 (release prep), so all intervening WPs merge on today's tolerated gate | — (see WP9) |

**Hotspots:**
- **Make `mypy abkit` genuinely clean; the `continue-on-error` flip is WP9's, not WP1's.**
  Order: (a) raise `python_version` to `3.12`; (b) run `mypy abkit` locally and in a CI
  dry-run; (c) if the numpy PEP-695 stub error persists, bump mirrors-mypy to a stable-`type`
  release (fallback above) and re-run; (d) confirm truly clean. **Do NOT flip
  `continue-on-error` off in WP1** — doing so before WP2/WP5/WP6 land would retro-red-block
  their new Python; the flip is a WP9 release-prep decision (§7 Q9). All subsequent Python
  WPs must keep `mypy abkit` clean so WP9's flip is a no-op. Re-check that raising to 3.12
  does not *silence* a real 3.10-only typing issue (grep the mypy delta before/after).
- **`black` pin is load-bearing at release.** CI currently installs the latest black
  (26.x) via the unpinned `[dev]` extra; a construct formatted differently by 26.x vs
  the pre-commit 24.4.2 flips CI red at release time. Pin before cutting the CHANGELOG.

**Tests / CI green:** no new tests; the gate is `pre-commit run --all-files` clean and
the `lint` CI job green on Py 3.10/3.11/3.12. Record in CHANGELOG under tooling.

**Adversarial-review note:** verify the mypy pass is *real*, not the numpy stub error
merely relocating; confirm the black pin is identical in `.pre-commit-config.yaml` and
`[dev]` (a common miss is pinning one and not the other).

**✅ As-built (M6 WP1, 2026-07-06) — the diagnosis was WRONG; corrected here.** The
"`mypy` fails on clean HEAD" debt was **not** numpy. The blocker was a stray comment
`# type: (required, optional)` at `metric_config.py:48` that mypy parsed as a **PEP-484
type comment** → `Invalid syntax` → early bail (which is why nothing else was ever
type-checked and why the error mis-anchored). Fixes shipped: (1) reword that comment; (2)
`[tool.mypy] python_version` `3.10 → 3.12` (clears the *secondary*, real numpy 2.5 PEP-695
stub error — mypy 1.10.0 parses the stubs fine at 3.12, verified, so **no `mirrors-mypy`
bump needed**); (3) add `yaml.*` to `ignore_missing_imports`; (4) pin `[dev]`
`black==24.4.2` + `mypy==1.10.0` to the pre-commit revs (zero reformat churn — abkit is
clean under black 24.4.2 **and** 26.x). **Consequence that reshapes the WP1 goal:** with the
parser un-blocked, `mypy abkit` now *runs to completion* and surfaces **~124 real
strict-mode errors** (mostly `X | None` Optional-handling in `tuning/recompute.py` +
`pipeline/readout.py`). Making mypy *strict-green* is therefore NOT a config task — it is a
124-error code cleanup in **numeric hot paths**, where a careless narrowing could silently
change a number (the cardinal invariant). **Decision:** WP1 ships the parse/config/pin fixes
and leaves `mypy abkit` `continue-on-error: true` with the corrected comment; the 124-error
strict cleanup is **tracked debt** (ROADMAP), to be done as a dedicated, test-guarded effort
(the 1566-test suite + goldens are the behavior guardrail) — NOT rushed into WP1, and the
WP9 `continue-on-error` flip stays conditional on it. `black`/`ruff`/other pre-commit hooks
are green; the mypy hook stays red until the cleanup. Reference: ROADMAP "Tooling debt".

---

### WP2 — `abk init-claude` command + packaged `.claude` asset tree — independent, additive

**Goal:** ship the AI-native onboarding crown jewel: the `abk init-claude` command +
the packaged `abkit/cli/assets/claude/` tree (`CLAUDE.section.md`, the operator rule
body, the 7 skills), with a CI gate proving the wheel actually contains them.

| Source (donor) | Target | Verdict |
|---|---|---|
| `detectkit/detectkit/cli/commands/init_claude.py` (185 lines) | `abkit/cli/commands/init_claude.py` — port 1:1: markers, `_BLOCK_RE` DOTALL sub, `_NEW_FILE_HEADER`, `_assets_root` (3.10-safe single-arg `joinpath` chaining), `_write_if_changed`, `_inject_claude_md`, `_copy_tree`, `_summarize`, `run_init_claude` | Port (renamed) |
| `detectkit/detectkit/cli/main.py` L66-95 (`@cli.command(name="init-claude")` + `--target-dir/-d`) | register in `abkit/cli/main.py` lazy Click group; remove the "land per M6" note from the module docstring; honor abkit's **non-zero-exit-on-failure** convention | Port (reshaped) |
| `detectkit/.../assets/claude/CLAUDE.section.md` | `abkit/cli/assets/claude/CLAUDE.section.md` — port STRUCTURE, rewrite content for A/B (§0.4 below) | Port + rewrite |
| `detectkit/.../assets/claude/skills/dtk-feedback/SKILL.md` | `.../skills/abk-feedback/SKILL.md` — near-verbatim (repo → `alexeiveselov92/ab-analysis-kit`, drop detector/alert gotchas) | Port |
| `detectkit/.../assets/claude/skills/{dtk-setup-project,dtk-new-metric,dtk-tune}/SKILL.md` | `.../skills/{abk-setup-project,abk-new-metric,abk-explore}/SKILL.md` — port structure, reshape for A/B | Port + reshape |
| — (net-new for A/B) | `.../skills/{abk-new-experiment,abk-validate,abk-plan}/SKILL.md` | **NEW** |
| `detectkit/.../assets/claude/rules/{overview,cli,project}.md` | `.../rules/ab-analysis-kit/{overview,cli,project}.md` — port + reshape | Port + reshape |
| `detectkit/.../assets/claude/rules/metrics.md` | `.../rules/ab-analysis-kit/metrics.md` — reshape to A/B one-row-per-unit reusable metric | Reshape |
| `detectkit/.../rules/{detectors,alerting,autotune}.md` | **dropped**; replaced by A/B-native `.../rules/ab-analysis-kit/{experiments,methods,explore,validate,plan}.md` | **NEW** |
| `detectkit/tests/unit/test_init_claude.py` | `tests/cli/test_init_claude.py` — port suite; rewrite `RULE_FILES`/`SKILL_FILES` constants to the abkit set | Port |

**Skill provenance (7, per cli-and-dx §5):** `abk-feedback` = near-verbatim port;
`abk-setup-project` + `abk-new-metric` + `abk-explore` = port-then-reshape;
`abk-new-experiment` + `abk-validate` + `abk-plan` = **net-new A/B** (no donor equivalent).

**Rule-file set (D1, proposed — needs maintainer sign-off, doubles as the docs-site IA):**
`overview.md, cli.md, project.md, experiments.md, metrics.md, methods.md, explore.md,
validate.md, plan.md` under `.claude/rules/ab-analysis-kit/`.

**Hotspots:**
- **Renames throughout the port:** `dtk`→`abk`, `detectkit`→`abkit`, `_dtk_*`→`_ab_*`,
  `dtk_project.yml`→`abkit_project.yml`, `dtk_start_time`/`dtk_end_time`→abkit's `ab_*`
  loader built-ins, repo `alexeiveselov92/detectkit`→`alexeiveselov92/ab-analysis-kit`.
  Target install dir is **`.claude/rules/ab-analysis-kit/`** (the *pip* name), not
  `.../detectkit/` and not `.../abkit/` (cli-and-dx §5).
- **The BEGIN marker is intentionally VERSION-LESS.** The donor learned that stamping
  `__version__` into the marker churned the managed block on every no-op upgrade and
  nagged users to re-run. Keep `_BLOCK_RE = <!-- BEGIN abkit.*?-->.*?<!-- END abkit -->`
  **DOTALL** so the whole region is replaced in place — **and so it still matches an old
  versioned marker** (a refresh replaces, never appends a duplicate). The version
  surfaces only in (1) the `CLAUDE.section.md` footer ("Re-run after upgrading …"),
  (2) `echo_done`'s "Claude context ready for ab-analysis-kit v{__version__}". "In sync
  with `__version__`" means the *packaged asset content* changes across versions, not a
  stamped string (D2). Cover the stale-marker refresh with a test.
- **`_write_if_changed` returns created/updated/unchanged** for honest idempotency; the
  three writes are the managed `CLAUDE.md` block, the `.claude/rules/ab-analysis-kit/`
  copy-tree, and the `.claude/skills/` copy-tree. Report via `echo_tree`/`echo_done`
  (both already in `abkit/cli/_output.py`).
- **Keep the `importlib.resources.files('abkit.cli').joinpath('assets','claude')`
  Traversable mechanism verbatim** — it is exactly what the existing package-data slot
  serves. Do not switch to `__file__`/`pkg_resources`.
- **Verify the recursive glob captures nested `skills/<name>/SKILL.md` AND `rules/…/*.md`
  in a real wheel.** `assets/**/*.md` may not capture nested dirs on all setuptools
  versions. Run `python -m build`, unzip, list. If it misses, switch package-data to
  `assets/claude/**/*.md` or explicit patterns. **This is the highest-risk packaging miss
  in M6** — the ported tests run against source and won't catch a wheel drop.

**§0.4 `CLAUDE.section.md` content (A/B index, must reflect M5 as-built precisely):**
intro (abkit = declarative YAML+SQL A/B analysis, `load → compute → readout`, project =
`abkit_project.yml`); the DB-read-access note; the "read the matching rule before
answering" routing table into the 9 rule files; the 7-skill Skills section; the footer.
**Domain gotchas (cli-and-dx §5) — get these exactly right or they mislead the user's
assistant:** SRM before trusting any effect; **peeking on the daily series — sequential
is opt-in `ci_kind='always_valid'`, NOT on by default**; globally-unique experiment AND
metric names (one namespace); editing `method_params` orphans rows (recompute + `abk
clean`); every loader query one-row-per-unit joining the cohort macro
(`{% import 'abkit_assignment.jinja' as ab %}`); sum/count additive vs medians/quantiles
not; `_ab_results` is the BI contract. **`abk validate` is the A/A matrix, NOT a config
lint** — the config-lint verify step is `abk run --steps validate` (rewrite the donor
skill's "there is no dtk validate" line, don't copy it).

**Tests / CI green:** port `tests/cli/test_init_claude.py` — `TestFreshScaffold` (all
artifacts + exactly one managed block), `TestIdempotency` (rerun == unchanged),
`TestInjectionIntoExistingFile` (append preserves user content; stale versioned marker
refreshed in place), `TestCliWiring` (CliRunner invoke, non-zero exit on failure),
`CLAUDE.section.md` names every rule + skill, each `SKILL.md` has a `name:` frontmatter.
**New CI wheel-ships gate** (WP9 wires it into `lint`): extend the existing `pip wheel`
assertion to require `abkit/cli/assets/claude/CLAUDE.section.md` **and** at least one
`rules/ab-analysis-kit/*.md` **and** one `skills/*/SKILL.md` in the wheel namelist. This
is a *wheel-namelist* gate, not the web-freshness gate (the `:(glob)abkit/*/assets/**`
pathspec covers `reporting`/`tuning` JS, not `abkit/cli/assets` hand-authored `.md`).

**Adversarial-review note:** confirm the wheel actually contains the *nested* skill files
(not just top-level `.md`); confirm the marker regex refreshes an old versioned marker
in place; confirm no gotcha overstates a capability (sequential-on-by-default, `abk
validate` as a lint).

---

### WP3 — Single-source user-facing docs body (`docs/` tree) — additive, feeds WP7

**Goal:** author the user-facing `docs/` pages that `sync-docs.mjs` consumes and that the
site renders. This is greenfield *authoring* (larger than the porting), not a port.

| Target | Content |
|---|---|
| `docs/README.md` | Overview → site `/overview/` |
| `docs/getting-started/installation.md`, `quickstart.md` | install + `abk init && abk run` first result |
| `docs/guides/configuration.md`, `databases.md`, `experiments.md`, `metrics.md`, `compute-methods.md`, `explore.md`, `validate.md`, `plan.md`, `sequential.md`, `reading-a-readout.md`, `visualizing-results.md` | the domain body |
| `docs/reference/cli.md`, `internal-tables.md` | command reference + the `_ab_*` schema |
| `docs/examples/README.md` | index → site `/examples/` (BI examples land in WP4) |

**Hotspots:**
- **Single-source contract (D2, stated concretely).** There are **three** authored
  Markdown bodies, kept in lockstep, not one file with three outputs (the "one body,
  three renders" phrasing is a slogan):
  - **(a) contributor body** — repo `.claude/rules/{architecture,contributing,design}.md`
    → site `/development/*` (via sync-docs) AND read by Claude Code in-repo.
    (`design.md` is added in WP7, or the `/development/design/` page is dropped — D4.)
  - **(b) operator body** — packaged `abkit/cli/assets/claude/rules/ab-analysis-kit/*.md`
    → shipped in the wheel, written into a *user's* project by `abk init-claude`;
    **not** rendered on the site, **not** generated from `docs/`.
  - **(c) user docs body** — `docs/{README,getting-started,guides,reference,examples}.md`
    + `CHANGELOG.md` → site guide/reference pages.
  - **Lockstep enforcement (the gate the donor lacked):** the WP2 idempotence test pins
    `CLAUDE.section.md` ↔ shipped rules/skills; **WP9 adds a `test_docs_single_source.py`
    drift gate** asserting every operator-rule topic in (b) has a corresponding `docs/`
    guide in (c), and the release checklist (`contributing.md`) names "(a)/(b)/(c) tell
    the same story + `__version__`". No content is machine-generated across the bodies;
    the gate enforces *coverage*, human review enforces *agreement*.
- **`sync-docs.mjs` throws hard on a missing `PAGES[]` source**, so the site build is red
  until every mapped `docs/` page exists — author the full set in WP3 before wiring WP7.
- Copy is experiment-primary. Reuse the donor's `visualizing-results.md` FORM (tool-
  agnostic copy-paste SQL) but with the A/B BI cautions (WP4).

**Tests / CI green:** no unit tests (authored Markdown); coverage is enforced by the WP9
drift gate + the WP7 `astro build` (which fails on a missing `PAGES[]` source or an
unresolved internal link). Py test matrix unaffected.

**Adversarial-review note:** confirm every `PAGES[]` source authored here matches the map
in WP7's `sync-docs.mjs`; confirm no guide contradicts an operator rule (peeking,
sequential-opt-in, orphaning).

---

### WP4 — BI reference queries + example dashboards + optional SRM panel — additive

**Goal:** the `docs/examples/bi/` deliverable promised by cli-and-dx §4 / data-contract
§2–§4: reference SQL against `_ab_results` (+ the optional SRM panel), so teams connect
Grafana / Lightdash / Metabase / Superset to abkit's numbers.

| Source (donor / reference) | Target | Verdict |
|---|---|---|
| `detectkit/docs/guides/visualizing-results.md` (437 lines, the tool-agnostic copy-paste SQL FORM) | `docs/examples/bi/README.md` + `queries.sql` — the recipe set | Port (FORM) + rewrite |
| `docs/reference/legacy_grafana_dashboard.json` (the legacy A/B panels reference) | the panel list a reference dashboard must reproduce against `_ab_results` | Reference |
| — | `docs/examples/bi/srm_panel.sql` (optional SRM panel) | **NEW** |
| — (decision D5) | per-tool importable dashboard JSON **or** SQL-only recipes | **NEW / decided in D5** |

**Core views (against `_ab_results`):** effect + CI band vs zero (+ `avg_group_size`);
raw values + std + CUPED; MDE + sizes/power; p-value vs alpha; a results/audit table;
a cross-experiment summary; the optional SRM panel.

**Hotspots — BI invariants to bake into every recipe:**
- **ReplacingMergeTree dedup footgun.** `_ab_results` is `ReplacingMergeTree(created_at)`
  (LWW). A naive ClickHouse `SELECT` shows duplicate `created_at` versions — **every**
  recipe uses `FINAL` (ClickHouse) or relies on the PK / `argMax(...)` / `LIMIT 1 BY`;
  PG/MySQL dedupe on the PK. Ship the portability note.
- **Always group/filter by `method_config_id`** — >1 id per (experiment, metric) draws
  duplicate stabilization lines (offer `abk clean`).
- **Two-tier alpha is per-row.** A p-value-vs-alpha panel must compare to the **row's**
  effective (post-correction) `alpha`, never a hardcoded 0.05, or it mis-reports
  secondary/guardrail metrics.
- **Peeking hazard must not leak into BI.** Fixed-horizon CIs on the daily cumulative
  series are **not** peeking-valid; annotate with `is_horizon` + `ci_kind` and give
  pre-horizon `ci_kind='fixed'` a "not peeking-valid" visual treatment, so a dashboard
  can't re-introduce the exact optional-stopping error `abk validate` exists to expose.
- **Nullable columns under `insufficient_data` demotion** — recipes must handle NULL
  effect/CI/test columns, not assume non-null.

**Tests / CI green:** SQL is documentation (no runtime in the wheel). Optionally add a
`tests/e2e` smoke that the core `queries.sql` parse/execute against the ClickHouse e2e
fixture's `_ab_results` (reuses the existing `e2e-clickhouse` job) — recommended for the
dedup/`FINAL` recipes so they can't silently rot. No stats math touched.

**Adversarial-review note:** verify no recipe hardcodes 0.05; verify `FINAL`/dedup on
every ClickHouse query; verify the peeking annotation is present on the effect-band panel.

---

### WP5 — `abk test-report` + notification-channel infra — additive

**Goal:** the `abk test-report <exp>` connectivity/format smoke test (cli-and-dx §1
line 26) + the minimal channel infrastructure it needs. **Scope is a smoke test only** —
NOT an alerting subsystem.

| Source (donor) | Target | Verdict |
|---|---|---|
| `detectkit/detectkit/alerting/channels/{base,factory,slack,telegram,email,webhook,mattermost,branding,__init__}.py` | `abkit/notify/` (or `abkit/reporting/channels/`) — `BaseChannel` + factory + the 5 channels | Port + reshape |
| `detectkit/detectkit/cli/commands/test_alert.py` | `abkit/cli/commands/test_report.py` — load project+profiles, build mock payload, resolve channels, per-channel `send()` with ✓/✗ | Port + reshape |
| `abkit/utils/env_interpolation.py` (already exists) | reused verbatim by the factory (env-var secrets) — **no new util** | — |
| `abkit/reporting/builder.py` (terse readout + WIN/LOSE/FLAT/INCONCLUSIVE) | source of the mock `ReadoutData` payload | — |
| `detectkit/docs/guides/alerting-channels.md` (327 lines) | `docs/guides/notification-channels.md` (config how-to) | Port (FORM) |
| `abkit/config/*.py` + the `init` profiles scaffold | add a `notification_channels:` (or donor's `alert_channels:`) block to `profiles.yml` (env-interpolated) — schema decision D6 | Edit |

**Hotspots:**
- **Do NOT port the alerting subsystem.** Port **only** channels + a mock-payload
  command. Do **not** port `alerting/orchestrator`, `error_dispatch`, `_alert_step`, the
  decision/replay/recovery/cooldown machinery, or any `_dtk_alert_states` table. abkit
  deliberately has no alerting.
- **`AlertData` → `ReadoutData` reshape (experiment-primary, A/B vocabulary).** Drop
  `detector_name`/`detector_params`/`severity`/`consecutive_count`/`is_recovery`/
  `is_no_data`. Add `experiment`, `metric`, `verdict` (WIN/LOSE/FLAT/INCONCLUSIVE),
  `effect`, `left_bound`/`right_bound`, `pvalue`, effective `alpha`, `srm_flag`,
  `weekly_cycle_pct`. Source from `reporting/builder.py`.
- **Mock, not live (spec says "mock readout").** `test-report` builds a synthetic
  `ReadoutData` (donor's `create_mock_alert_data` shape) — no lock, no warehouse read
  required for the smoke test (D7).
- **Branding parity but abkit-branded.** Keep the donor's message-template + branding.py
  pattern; the bot name/avatar is an abkit asset (external — open question; placeholder
  until Claude design delivers, same seam as WP7).
- **Project-level error notification is OUT of M6 scope** (D8) — `test-report` is only a
  smoke test per the spec; wire a named "future" note, not a subsystem.

**Tests / CI green:** `tests/cli/test_test_report_command.py` (CliRunner: builds the mock
payload, resolves channels from a fixture `profiles.yml`, per-channel ✓/✗, non-zero exit
on a send failure) using `requests-mock` (already a `[dev]` dep) so no network; a
`tests/notify/test_channels.py` (factory env-interpolation, payload formatting). Register
in the lazy Click group. Py matrix unaffected.

**Adversarial-review note:** confirm no alerting semantics leaked in (severity/recovery/
cooldown); confirm secrets come only from env interpolation (no plaintext in config);
confirm non-zero exit on a failed channel (abkit convention).

---

### WP6 — Prefect flow/deployment scaffolding — additive

**Goal:** deliver the "runnable Prefect flow **+ deployment**" `abk init` promises
(cli-and-dx §3). A bare flow already scaffolds; add the committed deployment artifact.

| Source | Target | Verdict |
|---|---|---|
| `abkit/cli/commands/init.py:340-367,391` (`PREFECT_FLOW` string → `runners/prefect_flow.py`) | verify it still emits a valid `@flow abkit_daily` shelling `abk run --select tag:actual` | Verify |
| — (net-new; donor ships no Prefect) | add a `prefect.yaml` (or `deployments/`) example to the init scaffold + a cron note | **NEW** |
| `docs/guides/…` | `docs/guides/orchestration.md` (Prefect + generic cron) | **NEW** |

**Hotspots:**
- **Prefect version drift.** `prefect.yaml` deploy syntax differs between Prefect 2 and 3;
  abkit never imports prefect, so this scaffold cannot be CI-round-tripped like the init
  configs are. Pin the targeted Prefect major version in the doc + a header comment in the
  scaffold (D9), and keep the flow itself a thin `abk run` shell (version-robust).
- **The CLI is the unit of automation** — a Prefect task = an `abk` invocation; nothing
  assumes interactivity; locks are self-healing. Keep the scaffold true to that.

**Tests / CI green:** extend the init e2e / `tests/cli/test_init_command.py` to assert the
Prefect deployment artifact is scaffolded and the flow file parses as Python (`ast.parse`);
do **not** import prefect in tests. Py matrix unaffected.

**Adversarial-review note:** confirm the scaffold names its Prefect version; confirm the
flow file is valid Python without prefect installed (parse-only).

---

### WP7 — Astro `website/` + `sync-docs.mjs` + brand-token layer + landing demo — additive, deploy gated (G2)

**Goal:** stand up the single-source Astro/Starlight docs+landing site on a **placeholder**
brand-token layer with a clean palette/logo swap seam. Build + typecheck in CI. **WP7 lands
every `website/**` file EXCEPT `website/Dockerfile`** — the Dockerfile's presence on `main`
is the live-deploy trigger (§0.1), so it is held for **G2**. WP7 also **hardens `website.yml`
before any `website/**` merge** so the deploy cannot fire autonomously. **Prerequisites: WP3
(docs body — `sync-docs` sources) AND WP4 (BI examples — `astro build` link-checks
`docs/examples/**`).**

| Source (donor) | Target | Verdict |
|---|---|---|
| `detectkit/website/astro.config.mjs` | `website/astro.config.mjs` — rewrite title=abkit, `site=https://abkit.pipelab.dev`, sidebar for abkit's nav, github URL, fonts | Port + rewrite |
| `detectkit/website/{package.json,tsconfig.json,src/content.config.ts}` | `website/*` — rename `ab-analysis-kit-website`; deps `@astrojs/starlight`, `astro`, `sharp` | Port |
| `detectkit/website/scripts/sync-docs.mjs` | `website/scripts/sync-docs.mjs` — port machinery verbatim (H1→frontmatter, link rewriting, example-asset copy); rewrite **only** the `PAGES[]` + `DIR_TO_ROUTE`/`SRC_TO_ROUTE` maps | Port + remap |
| `detectkit/website/{Dockerfile,nginx.conf}` | `website/nginx.conf` verbatim now; **`website/Dockerfile` authored on the WP7 branch but NOT merged to `main` until G2** (its presence triggers `website.yml`'s ghcr push + deploy dispatch, §0.1). Dockerfile keeps **build context = repo root** (COPYs `docs/` + `CHANGELOG.md` + `.claude/rules`); rename image | Port (nginx now / Dockerfile at G2) |
| `.github/workflows/website.yml` (pre-harden **before** any `website/**` merge) | add `environment:` (required reviewer, mirroring `publish.yml`'s `environment: pypi`) to `build-and-push` **and** `notify-pipelab`; guard `notify-pipelab` on a non-empty `PIPELAB_DISPATCH_TOKEN` (`if: secrets != ''` via env-indirection) so a missing token can't red `main`; leave the Dockerfile guard as the belt-and-suspenders switch | Edit (safety) |
| `detectkit/website/src/styles/brand.css` (101 lines) | `website/src/styles/brand.css` — rewrite VALUES+NAMES to abkit's **existing `--abk-*`** names (light+dark `:root`, Starlight `--sl-color-*` mapping, fonts) — **placeholder values** | Port (reshape) |
| `detectkit/website/src/styles/landing.css` (2937 lines) | `website/src/styles/landing.css` — reuse the layout skeleton; prune dead anomaly/alerting classes | Port + prune |
| `detectkit/website/src/pages/index.astro` (1140 lines) | `website/src/pages/index.astro` — reuse nav/section/footer machinery; **rewrite ALL copy** for A/B (experiment → stabilization → WIN/LOSE/FLAT) | Port (machinery) + rewrite |
| `detectkit/website/src/scripts/core/canvas.ts` | **DO NOT re-port** — the demo imports abkit's `web/src/shared/chart.ts` (D3) | Reuse `web/` |
| `detectkit/website/src/scripts/demo/*` (anomaly demo) | `website/src/scripts/demo/*` — **NEW** original demo: cumulative effect + CI-band stabilization converging as synthetic N accrues | **NEW** |
| `detectkit/website/scripts/{check-demo-parity.mjs,gen-demo-golden.py}` | `website/scripts/*` — port the harness; `gen-demo-golden.py` freezes `abkit.stats` cumulative-effect output; CI parity gate | Port + regenerate |
| `detectkit/website/src/components/{DbBadges,ChannelIcon,Logo}.astro` | `DbBadges` near-verbatim (same 3 DBs); `ChannelIcon` iff WP5 keeps channel branding; **`Logo` = placeholder** until Claude design | Port / placeholder |
| `detectkit/website/public/{favicon.svg,bot-icon.png,examples/*}` | placeholder favicon/bot-icon; `public/examples/` regenerated from abkit's own configs | Reshape |
| — (D4) | `.claude/rules/design.md` (so `/development/design/` has a source) **or** drop that PAGES entry | Decide |

**`sync-docs.mjs` PAGES map (abkit):** `docs/README.md → /overview/`;
`docs/getting-started/* → /getting-started/*`; `docs/guides/* → /guides/*`;
`docs/reference/* → /reference/*`; `docs/examples/README.md → /examples/`;
`.claude/rules/architecture.md → /development/architecture/`;
`.claude/rules/contributing.md → /development/contributing/`;
(`.claude/rules/design.md → /development/design/` if added, D4);
`CHANGELOG.md → /changelog/`. The link-rewriter + H1-title-strip port unchanged.

**Hotspots:**
- **⚠ `website.yml` deploy safety comes FIRST (plan-review critical, all 3 critics; §0.1).**
  Before the first `website/**` file merges to `main`: (1) **do not commit `website/Dockerfile`
  to `main`** (the guard `[ -f website/Dockerfile ]` is the deploy switch — with no Dockerfile,
  a `website/**` merge fires `website.yml` but the guard is `false`, so build/push + dispatch
  are skipped and the run is a **green no-op**); (2) add `environment:` (required-reviewer)
  protection to `build-and-push` + `notify-pipelab`, matching `publish.yml`'s `environment:
  pypi`; (3) guard `notify-pipelab` on a non-empty token so an unset `PIPELAB_DISPATCH_TOKEN`
  cannot red `main`. The ghcr image push (which uses `GITHUB_TOKEN`, not a maintainer secret)
  is folded into the same G2 gate — it is NOT ungated. CI's astro build/typecheck runs in the
  **`ci.yml` `website` job (Docker-free)**, wholly separate from the `website.yml` deploy
  workflow, so the site is fully tested without ever building the image.
- **Shared renderer core: import `web/`, do NOT re-port (D3) — AND update the `bundle` CI job
  in the same PR (plan-review, critic 2).** detectkit generates its product bundles FROM
  `website/`; abkit inverted this in M3 — `web/` is the seed, the site is a *consumer*. The
  landing demo imports `../web/src/shared/chart.ts`. Wire this as an **npm workspace** (`web/`
  + `website/`). **This relocates the lockfile to a root `package.json`/`package-lock.json`,
  which breaks the existing `bundle` job** (it runs `npm ci` in `working-directory: web` with
  `cache-dependency-path: web/package-lock.json`). WP7 MUST, in the same PR, update `bundle`
  to install from the workspace root (`npm ci` at repo root; `npm run build --workspace web`;
  `npm test --workspace web`) and repoint `cache-dependency-path` to the root lock — else
  every PR reds. (Rejected alt: keep `web/` standalone + copy-with-parity for the site's
  `chart.ts` — leaves `bundle` untouched but adds a parity gate; workspace is preferred but
  the `bundle`-job edit is mandatory either way.) Flag the divergence in the PR.
- **The demo's JS math is a re-derivation of `abkit.stats` → golden-gated (hard gate).**
  "Never change a number silently" applies: `check-demo-parity.mjs` compares the JS
  cumulative effect/CI against `gen-demo-golden.py`'s frozen `abkit.stats` output at a stated
  rel-tolerance (mirror detectkit's 1e-6). **The parity gate must be a hard CI gate (NOT
  `continue-on-error`)** and `gen-demo-golden.py` is regenerated **strictly from `abkit.stats`,
  never hand-edited** (plan-review, critic 1) — so a stats change that desyncs the demo fails
  CI instead of shipping a silently-wrong landing chart. A drifting landing chart is a bug.
- **Token layer has TWO sources kept in lockstep.** `brand.css` = the single source of
  REAL values (light+dark, on the live site AND injectable into the self-contained
  report/explore HTML); `web/src/shared/chart.ts` `TOKEN_FALLBACKS` = the standalone-HTML
  fallback of the **same `--abk-*` names**. WP7 extends the existing CI hex-containment
  loop into a **name+value sync gate**, so a palette swap can't green the site while
  leaving self-contained reports on stale placeholders.
- **Ship on placeholders (scope answer 2).** Build everything on the neutral `--abk-*`
  values. The final palette/logo drop-in (from Claude design, possibly via the
  `DesignSync` tool — open question) is a later commit touching only `brand.css` +
  `Logo.astro` + `public/favicon` + `TOKEN_FALLBACKS`, no logic.
- **Accessibility is an acceptance criterion of the palette drop-in (branding-and-site §3;
  plan-review, critic 0).** The final palette must pass **WCAG AA contrast in BOTH light and
  dark**, and the chart's effect/CI/zero-line/SRM colors + the A/A matrix FPR-vs-budget bands
  must be **distinguishable under common color-vision deficiencies**. The placeholder→brand
  swap commit is not "done" until this is checked (ideally an automated contrast assertion
  added to the brand gate, at minimum a recorded manual check). The current hex-containment +
  name+value sync gates do NOT cover contrast — this is an additional, named criterion.
- **Renderer stays framework-free (invariant 6).** Astro/Starlight is dev-only site
  chrome; the committed `report.js`/`explore.js` and the landing demo bundle stay
  dependency-free IIFEs, CSP-safe, no external hosts. `web/` is never wheel-shipped.
- **Widening `website.yml` `paths:` is a G2 step, NOT a WP7 step (corrected — §0.1).** The
  filter is already `website/**`; adding `docs/**`, `.claude/rules/**`, `CHANGELOG.md` (so the
  site rebuilds on content edits) happens **together with landing the Dockerfile at G2**, once
  the maintainer confirms `PIPELAB_DISPATCH_TOKEN` is set, the pipelab infra is wired for
  `ab-analysis-kit-updated`, and `abkit.pipelab.dev` DNS/cert are live — **external, G2,
  maintainer**. Widening paths in WP7 while the Dockerfile is absent is harmless (guard skips)
  but pointless; do it at G2 with the Dockerfile.

**Tests / CI green:** add a `website` CI job: `npm ci && npm run sync && astro check &&
astro build` (build fails on a missing PAGES source or unresolved link) + the
demo-parity gate. Extend the `bundle` job's hex loop into the brand.css↔TOKEN_FALLBACKS
name+value gate. The existing bundle-freshness gate still requires any `web/src/**` edit
to rebuild+commit `abkit/*/assets/*.js` in the same PR (pathspec `:(glob)abkit/*/assets/**`
— plain `abkit/*/assets/` silently no-ops, a recorded M4 finding). Py matrix + e2e
unaffected.

**Adversarial-review note:** confirm the demo parity gate actually fails on a hand-injected
math drift; confirm the token sync gate catches a placeholder-vs-brand.css mismatch;
confirm `astro build` is red on a deleted `docs/` page; confirm `web/` is absent from the
built wheel namelist; confirm no external-host asset slipped into a self-contained bundle.

---

### WP8 — Named-deferrals hygiene: re-point every "planned for M6" string — additive

**Goal:** two reconciliations so the shipped code + specs tell one true story on release day:
(1) because scope answer 1 keeps three features **out** of M6, no v1 user may read "coming in
M6"; re-point every such string to a clean, honest deferral. (2) where the as-built diverges
from spec prose (the single-source docs contract; the BI per-tool scope; error notification),
amend the spec to match reality (plan-review, critic 0) — else WP10's own "docs tell one
story" DoD fails.

| Source | Target | Verdict |
|---|---|---|
| `abkit/config/experiment_config.py:~156` (`scheme: alpha_spending` ValueError says "planned for M6") | reword to a clean "not implemented — `always_valid` is the supported scheme; group-sequential/`alpha_spending` is a future item" (no version promise) | Edit |
| `abkit/cli/commands/plan.py` (~L197) / `abkit/planning/__init__.py` / `sizing.py` "deferred to M6" docstrings (runtime/ASN) | reword to "runtime/ASN is a future item (needs an arrival-rate source)" | Edit |
| `abkit/cli/main.py` (~L278) + `abkit/stats/sequential/mixture.py` (~L23) "M6" strings | audit each; reword any deferred-feature "M6" to "future/v2" (critic-named hits) | Edit |
| `docs/specs/cli-and-dx.md §1` amendment + `aa-false-positive-matrix.md §8.1` "M6 follow-up" (sequential × composed) | reword to "future / v2 follow-up" | Edit |
| `ROADMAP.md` M5 "Deferred to M6" lines + `docs/specs/statistics-changes.md`, `data-contract-and-reporting.md` cross-refs | audit + reword any "M6" that is now v2 (critic-named) | Edit |
| **Spec-reconciliation (as-built ≠ prose):** `cli-and-dx.md §5` "single-source, two renders" + `branding-and-site.md §1` "one Markdown body … renders three ways … sync-docs keeps them in lockstep" | rewrite to the as-built **three-separately-authored-bodies + coverage-gate + human-review** model (D2); note the lockstep mechanism is a coverage gate, not machine cross-generation | Edit |
| **Spec-reconciliation:** `cli-and-dx.md §3` "failures surface via … project-level error notification" | add a one-line "project-level error notification is a post-M6 item; `abk test-report` is the M6 connectivity smoke" (D8) | Edit |
| **Spec-reconciliation (only if maintainer approves the reduced BI scope, §7#5):** `cli-and-dx.md §4` + `ROADMAP.md` M6 "example dashboards per tool (Grafana/Lightdash/Metabase/Superset)" | if D5's SQL-recipes-plus-one-Grafana-JSON is accepted, amend so the spec no longer promises per-tool importable dashboards; **blocks on §7#5** | Edit (conditional) |

**Hotspots:**
- **User-facing errors first.** `experiment_config.py`'s `alpha_spending` ValueError ships
  to users; a stale "planned for M6" misleads on release day. It must name the feature
  and refuse cleanly **without** a version promise (the maintainer can re-promise in a
  real future milestone).
- **grep discipline.** `grep -rn "M6" abkit/ docs/ ROADMAP.md .claude/` (note: `docs/` and
  `ROADMAP.md`, not just `docs/specs/`) and triage each hit: is it (a) a genuine M6
  deliverable now shipping (leave/flip to "shipped"), or (b) a deferred feature (re-point).
  The critic-named starting hits are `cli/main.py:~278`, `plan.py:~197`,
  `stats/sequential/mixture.py:~23`, and ROADMAP M5's "Deferred to M6" lines — but the grep
  is authoritative, not this list. Do this as part of the exit-gate docs sync too (WP10).

**Tests / CI green:** update any test asserting the old error string (e.g. the WP3-era
`alpha_spending`→"planned M6" config test) to the new message. No math touched.

**Adversarial-review note:** grep for residual "planned for M6"/"deferred to M6"/"M6
follow-up" across `abkit/` + `docs/` after the edits; confirm each remaining "M6" refers
to a *shipped* M6 deliverable.

---

### WP-A — `abk plan` runtime / ASN (pulled into M6 per scope decision) — additive, independent

**Goal:** complete the M5 sizing planner (`abkit/planning/`) with the deferred **runtime**
(days-to-N from a unit-arrival rate) and **ASN** (expected/average sample number for the
always-valid sequential design), fulfilling the `cli-and-dx §1` "runtime/ASN → M6" promise.
**Read-only** (no lock, no `_ab_*` writes) — same posture as the M5 planner.

| Source | Target | Verdict |
|---|---|---|
| `abkit/database/internal_tables/_exposures.py` (M5 WP5 added `get_exposure_count_stream`) | a read-only **arrival-rate** derivation: distinct units per unit-time from `_ab_exposures.exposure_ts` over the observed window → units/day/arm | **NEW** (extend) |
| `abkit/planning/sizing.py` (`size_comparison` — required-N/MDE/power) | add `runtime_for(required_n, arrival_rate)` (days-to-N + days-to-horizon) + `asn_for(...)` (expected looks × cadence to cross the always-valid boundary under H0/H1) | **NEW** (pure, in `planning/`, imports `stats.power` + `stats.sequential`) |
| `abkit/cli/commands/plan.py` | add runtime + ASN lines to the tree; `--arrival-rate <units/day>` override (else derived from `_ab_exposures`; greenfield with `--baseline` and no rate ⇒ runtime SKIPPED, not guessed) | Edit |

**Hotspots:**
- **No stats-core change / no `ALGORITHM_VERSION`.** ASN is new *planning* math (expected
  sample number from the existing CS radius + power curve), computed in `abkit/planning/`,
  never altering any registered method's output. `abkit.stats` stays pure & byte-identical
  (goldens untouched). ASN applies only to sequential-eligible + `sequential.enabled` cells;
  else "ASN n/a (fixed-horizon design)".
- **Arrival rate is read-only and honest.** Derive units/day/arm from the real
  `_ab_exposures` distribution (own manager closed in `finally`, no writes). If exposures are
  absent (greenfield), runtime is SKIPPED with a clear reason — never invented. Ratio/bootstrap
  comparisons stay SKIPPED (M5 declarative dispatch, unchanged).
- **Refusals stay declarative (invariant 3).** No method-name special-casing.

**Tests / CI green:** extend `tests/planning/test_sizing.py` (runtime days-to-N; ASN < fixed-N
under a true effect; ASN n/a for fixed-horizon; greenfield-no-rate SKIPPED) + `tests/cli/
test_plan_command.py` (runtime/ASN lines render; `--arrival-rate` override). Py matrix
unaffected; no goldens touched.

**Adversarial-review note:** confirm no golden moved and `test_purity.py` still passes (ASN
lives in `planning/`, not `stats/`); confirm runtime is SKIPPED (not guessed) with no arrival
data; confirm ASN is only emitted for sequential-eligible cells.

---

### WP-B — A/A sequential × composed sweep (pulled into M6 per scope decision) — additive, independent

**Goal:** the A/A **sequential × composed** cell promised by `aa-fpr §8.1` — `abk validate`
on a `sequential.enabled` multi-metric family reporting composed FWER/FDR computed on the
**always-valid per-look (peeking) significance**, not the fixed-horizon-only significance.
Combines two M5 pieces that already exist separately: the sequential A/A scorer (M5 WP2, D8)
and the composed family sweep (M5 WP8, D9).

| Source (M5 as-built) | Target | Verdict |
|---|---|---|
| `abkit/validate/family.py` (D9 union-cohort composed FWER/FDR over the fixed-horizon significance) | add a parallel always-valid pass: reuse the union-cohort draw + `stats.correction.composed_significance`, but feed the **always-valid per-look** `SignificanceInput`s (peeking across looks), not the horizon-only ones | Edit (extend) |
| `abkit/validate/scoring.py` (`_always_valid_sig`, `_cell_tau2`→`mixture_tau2` from M5 WP2) | reuse verbatim to build the per-look always-valid significance the family pass consumes | Reuse |
| `_ab_aa_runs` sentinel row (`metric="__family__"`, `method_config_id="__composed__"`, D9) | add `fwer_sequential`/`fdr_sequential` into the sentinel `details` JSON — **no schema column change** (mirrors the D9 sentinel-in-details discipline) | Edit |
| `abkit/reporting/calibration.py` + `report.ts` family block | render the sequential composed column beside the fixed one (reuse the D8 "peeking → always-valid" two-curve idiom); **rebuild `report.js`** (`cd web && npm run build`) | Edit + rebuild bundle |
| `docs/specs/aa-false-positive-matrix.md §8.1` | flip from "future/v2 follow-up" to **shipped** (this replaces the WP8 deferral note) | Edit |

**Hotspots:**
- **No new estimator / no `ALGORITHM_VERSION`.** WP-B only *composes* two shipped transforms
  (always-valid MODE transform + composed FWER/FDR rule); no registered method's numbers move,
  goldens untouched, `abkit.stats` pure.
- **Under the COMPLETE null, sequential FWER == FDR** (the D9 identity — every rejection is
  false) must hold on the always-valid pass too; peeking FWER ≥ single-look (the D8 property)
  must hold within the composed sweep. These are the golden-style invariants to pin.
- **Bundle discipline (M3/M4 lesson).** Any `web/src/report/**` edit ⇒ rebuild + commit
  `abkit/reporting/assets/report.js` in the same PR (CI freshness gate, pathspec
  `:(glob)abkit/*/assets/**`).
- **Runs only when the family is `sequential.enabled`** and multi-metric; else the sequential
  composed column is absent (not zero-filled).

**Tests / CI green:** extend `tests/validate/test_family_sweep.py` (null sequential composed
FWER ≈ nominal band; complete-null sequential FDR == FWER; peeking ≥ single-look within the
sweep; planted-effect leaves null metrics' sequential FWER controlled) + the sentinel-details
persistence + the calibration/web-smoke render. Rebuild `report.js`. Py matrix + `bundle`
gate green.

**Adversarial-review note:** confirm the sequential composed pass reuses the SAME τ² anchor +
`_always_valid_sig` as the D8 column (one estimator, not a second); confirm the sentinel-in-
details approach adds no `AA_RUN_COLUMNS` churn; confirm `report.js` rebuilt & `explore.js`
byte-identical if only report TS changed; confirm no golden moved.

---

### WP9 — Release engineering: version, CHANGELOG, wheel-ships gate, `pip install` DoD smoke — additive, prep only (publish is G1)

**Goal:** everything needed for a clean tagged release *except the tag itself* (G1). Make
the DoD `pip install ab-analysis-kit` provable in CI against the built wheel.

| Source | Target | Verdict |
|---|---|---|
| `abkit/__init__.py:13` (`__version__ = "0.0.1.dev0"`) | bump to `"0.1.0"` (must exceed the placeholder or PyPI rejects) | Edit |
| `pyproject.toml` `Development Status :: 2 - Pre-Alpha` | `3 - Alpha` (scope answer 3) | Edit |
| `CHANGELOG.md` `[Unreleased]` | cut into a dated `0.1.0` heading (authoritative for behavior) | Edit |
| `.github/workflows/ci.yml` `lint` `pip wheel` gate | **extend** to assert `abkit/cli/assets/claude/**` ships (WP2), alongside `report.js`/`explore.js` | Edit |
| `.github/workflows/ci.yml` `lint` `mypy abkit` `continue-on-error: true` (deferred from WP1) | **Keep tolerated for the 0.1.0 release** unless the ~124-error strict-mode cleanup (WP1 as-built / tracked debt) lands first — flipping to `false` without it reds every PR. §7 Q9 now = "invest in the strict cleanup pre-release, or ship 0.1.0 with mypy aspirational?" (recommend: ship aspirational; strict cleanup as a post-0.1.0 quality pass) | Edit / decision |
| — | **NEW** `pip install ab-analysis-kit` DoD smoke: install the built wheel in a clean venv → `abk --version` → `abk init-claude -d <tmp>` → assert the managed block + rules + skills materialize (proves console script + packaged assets resolve at install time, not just editable dev) | **NEW** CI step |
| `publish.yml` (byte-identical to donor) | **no code change**; verify PyPI trusted-publisher + `pypi` environment registration (G1, maintainer) | Verify |

**Hotspots:**
- **The wheel is the DoD.** A bad wheel (missing bundles or the init-claude payload)
  cannot be re-uploaded under the same version. The extended wheel-namelist gate (WP2 +
  here) is the last line of defense; the `pip install` smoke proves `importlib.resources`
  reads the assets from an *installed* wheel (the ported unit tests only run against
  source).
- **First real tag must be `> 0.0.1.dev0`.** The normalized name is `ab_analysis_kit-*.whl`
  (CI greps this). `0.1.0` is the target (D-Rel).
- **Publish auth.** `publish.yml` uses OIDC trusted publishing (no long-lived token). The
  previously-pasted placeholder-upload token (MEMORY) must be **rotated/revoked** by the
  maintainer, and the trusted-publisher + `pypi` environment must be registered on PyPI
  for this repo/workflow — **G1, external, §7**.

**Tests / CI green:** the new `pip install` smoke job (fresh venv, wheel install, `abk
--version`, `abk init-claude`) runs on Py 3.10/3.11/3.12; the extended wheel-namelist
assertion; all existing jobs stay green. No stats math touched.

**Adversarial-review note:** confirm the smoke installs the **wheel** (not `-e .`);
confirm the namelist gate fails when a claude asset is removed (hand-delete + expect red);
confirm the version strictly exceeds `0.0.1.dev0`.

---

### WP10 — M6 exit gate: release-readiness e2e, ≥2 adversarial rounds, docs/rules sync flip — last

**Goal:** prove M6 end-to-end, run the milestone review at the M4/M5 bar, and flip all the
as-built docs. Mirrors M5 WP9.

**Scope:**
- **Release-readiness e2e** (`tests/e2e/test_release_readiness.py`): in a fresh venv,
  `pip install` the built wheel → `abk --version` → `abk init <name>` → `abk run --select
  <example>` → assert a real `_ab_results` row + an offline-rendering `--report` HTML →
  `abk init-claude -d <tmp>` materializes the managed block + `.claude/rules/ab-analysis-kit/`
  + `.claude/skills/` → the explore/report bundles render offline (jsdom, existing gates).
  Byte-reproducible; no network.
- **≥2 FULL adversarial review ROUNDS** (the M4/M5 lesson: round-2 is mandatory — it
  caught an incomplete round-1 fix in both). Round-1 (N lenses, refute-by-default, a second
  independent verifier per finding) → land verified fixes → round-2 re-review over the
  patched tree → only then the closing `fix(m6)` commit. Both rounds recorded in §5 below.
  M6-specific lenses: **packaging** (does the wheel really ship every asset? does `pip
  install` resolve them?), **DX correctness** (do the CLAUDE.section gotchas match M5
  as-built — sequential opt-in, `abk validate` ≠ lint?), **single-source drift** (do
  bodies (a)/(b)/(c) agree?), **brand-swap seam** (is every surface on the token layer?),
  **reliability** (idempotent init-claude, non-zero exits), **docs build** (astro red on a
  missing page / drifted demo math).
- **Docs/rules sync flip (one story):** `ROADMAP.md` (M6 shipped; the three named
  deferrals pointed at v2/future); `CHANGELOG.md` (`0.1.0`); `.claude/rules/architecture.md`
  + `contributing.md` (flip "M5 shipped → M6 shipped"; add `cli/assets/claude/`,
  `abkit/notify/`, `website/` to the layout; release checklist names bodies (a)/(b)/(c) +
  `__version__`); `CLAUDE.md` status line.

**DoD:** CI fully green (Py 3.10/3.11/3.12 + `e2e-clickhouse` + `bundle` freshness +
`website` build + demo parity + wheel-namelist + `pip install` smoke); ≥2 review rounds
recorded; goldens untouched, no `ALGORITHM_VERSION` moved, `abkit.stats` purity intact;
docs tell one story. **The tagged PyPI publish (G1) and the live site deploy (G2) are
maintainer actions taken AFTER this gate is green — not part of the autonomous WP.**

---

### GATED external actions (maintainer-confirmation-required — NOT autonomous)

- **G1 — PyPI publish.** After WP9/WP10 are green: the maintainer (a) rotates/revokes the
  pasted placeholder token, (b) confirms the trusted-publisher + `pypi` environment are
  registered on PyPI for `alexeiveselov92/ab-analysis-kit`, (c) pushes the `v0.1.0` tag
  that triggers `publish.yml`. Irreversible (a version can't be re-uploaded). An
  implementation agent prepares and verifies; it does **not** push the release tag.
- **G2 — live website deploy.** After WP7 is green on placeholders (or after the final
  palette/logo drop-in), the maintainer: (a) confirms `PIPELAB_DISPATCH_TOKEN` is set, the
  pipelab infra handles `ab-analysis-kit-updated`, and `abkit.pipelab.dev` DNS/cert are live;
  (b) **merges `website/Dockerfile` to `main` — THIS is the deploy trigger** (it flips
  `website.yml`'s guard to `built=true`, publishing the ghcr image and firing the dispatch),
  approving the hardened workflow's required-reviewer gate; (c) widens `website.yml` `paths:`
  to `docs/**`/`.claude/rules/**`/`CHANGELOG.md` so content edits rebuild. **Correction
  (plan-review):** the trigger is the Dockerfile landing, NOT "widening paths" — the path
  filter already matches `website/**`. External infra; not autonomous; an implementation agent
  never merges the Dockerfile.

---

## 2. Dependency graph / parallelism

```
WP1 (tooling debt) ── independent, first ──────────────────────────────────────┐
WP2 (init-claude cmd + assets) ── independent ─────────────┐ (feeds WP9 wheel gate)
WP3 (user docs body) ──┐                                    │                    │
WP4 (BI examples) ─────┤ (both feed WP7: sync-docs + link)  │                    │
WP5 (test-report + channels) ── independent ───────────────┤                    │
WP6 (Prefect scaffolding) ── independent ──────────────────┤                    │
WP8 (named-deferrals hygiene) ── independent ──────────────┤                    │
WP-A (abk plan runtime/ASN) ── independent, stats-pure ────┤                    │
WP-B (A/A sequential×composed) ── independent, stats-pure ─┤                    │
                                                           ▼                    ▼
WP3 + WP4 ──▶ WP7 (Astro site + sync-docs + brand + demo) ─▶ WP9 (release eng) ─▶ WP10 (exit gate)
              (imports web/ shared core; Dockerfile+deploy    (wheel gate needs      │
               held for G2; hardens website.yml first)         WP2 assets; version;  ▼
                                                               pip-install smoke)  G1 (PyPI publish)
                                                                                  G2 (live deploy)
```

- **Everything up to WP9 is safe, additive, fully-in-repo.** WP1/WP2/WP5/WP6/WP8/WP-A/WP-B are
  independent and parallelizable from day one. **WP-A/WP-B are feature work but stats-pure**
  (no `ALGORITHM_VERSION`, goldens untouched) and must land before the WP10 exit gate. **WP3
  (docs body) AND WP4 (BI examples) both gate WP7** — `sync-docs` needs every `PAGES[]` source
  and `astro build` link-checks `docs/examples/**`, so WP7 landing before WP4 reds on dangling
  links (plan-review, critic 2). WP7 imports `web/` as the shared core (D3) and **must not land
  `website/Dockerfile` on `main`** (that is the deploy trigger → G2; §0.1).
- **WP9 explicitly depends on WP2** (its wheel-namelist + `pip install` smoke must find the
  init-claude payload) and folds in the WP1 `mypy continue-on-error` flip decision (§7 Q9).
  **WP10 is last.** **G1/G2 are gated, external, maintainer.**

## 3. Decisions (Dk) — the arbitration points, settled here

- **D1 — Operator rule-file set (proposed, needs maintainer sign-off).**
  `.claude/rules/ab-analysis-kit/{overview,cli,project,experiments,metrics,methods,explore,
  validate,plan}.md` (9 files) + the 7 skills `abk-{setup-project,new-experiment,new-metric,
  explore,validate,plan,feedback}`. This set doubles as the docs-site IA, so it needs a
  human call (§7).
- **D2 — "Single source" = three authored bodies kept in lockstep, version-stamped, NOT
  one-file-three-outputs.** (a) contributor rules → site `/development/*` + in-repo Claude;
  (b) packaged operator rules → wheel → user project via `init-claude` (not on the site,
  not generated from docs); (c) `docs/` + `CHANGELOG.md` → site guide/reference. The BEGIN
  marker is **version-less** (version lives only in the `CLAUDE.section.md` footer + the
  `echo_done` line); "in sync with `__version__`" means the packaged *content* changes
  across versions. Lockstep is enforced by the WP2 idempotence test + the WP9 coverage
  drift gate + the release checklist, **not** by cross-body generation.
- **D3 — The site consumes `web/` as the shared renderer core (an npm workspace); it does
  NOT re-port `canvas.ts` or the `gen-*-bundle.mjs` scripts.** abkit inverted the donor's
  layout in M3 (`web/` is the seed). Flag the divergence in the PR. **The workspace
  conversion requires updating the existing `bundle` CI job in the same PR** (root `npm ci`,
  `--workspace web` build/test, root `cache-dependency-path`) or it reds every PR
  (plan-review, critic 2). Rejected: moving renderers under `website/` to match detectkit
  (undoes M3); cross-package path-alias import (fragile across esbuild/vite).
- **D-CI — `website.yml` is hardened before any `website/**` merge; the live deploy is
  gate-guarded, not merge-triggered.** The `website/Dockerfile` is held off `main` until G2;
  `build-and-push` + `notify-pipelab` get `environment:` (required-reviewer) protection
  mirroring `publish.yml`; `notify-pipelab` is guarded on a non-empty token so it cannot red
  main. CI tests the site via a Docker-free `website` job in `ci.yml`. This makes the G2 gate
  *real* rather than asserted-in-prose (plan-review, all 3 critics).
- **D4 — `.claude/rules/design.md`.** Add it (so `/development/design/` has a source,
  matching the donor) **or** drop that PAGES entry and fold brand guidance into
  `branding-and-site.md`. Recommendation: add a thin `design.md` (brand-token + swap-seam
  rules) so the site's design page exists. Needs sign-off (§7).
- **D5 — BI deliverable shape.** Ship **tool-agnostic copy-paste SQL recipes** as the
  first-class deliverable (detectkit's choice; low maintenance, dialect-portable), plus
  **one** importable reference dashboard JSON for **Grafana** (the tool with a legacy
  reference already in-repo). Lightdash/Metabase/Superset get SQL recipes, not per-tool
  JSON (JSON is per-tool version-fragile + branding burden). Confirm the four-tool split
  with the maintainer (§7).
- **D6 — `profiles.yml` notification block.** Name it `notification_channels:` (clearer
  than the donor's alerting-flavored `alert_channels:` for a smoke-test-only feature),
  env-var interpolated; parse it in the `ProfilesConfig` pydantic model (typed, not loosely
  parsed like the donor's `test_alert`). Confirm the block name (§7).
- **D7 — `abk test-report` sends a purely synthetic mock** (donor's `create_mock_alert_data`
  shape reshaped to `ReadoutData`) — no lock, no warehouse read. Spec says "mock readout".
- **D8 — Project-level error notification is OUT of M6** (a named future item). `test-report`
  is a connectivity/format smoke test only, per cli-and-dx §1.
- **D9 — Prefect scaffold ships a `prefect.yaml` (Prefect 3 project deploy) with a pinned
  version note**; the flow stays a thin `abk run` shell (version-robust); abkit never
  imports prefect (no CI round-trip; parse-only test). Confirm the Prefect major (§7).
- **D-Rel — First real release is `0.1.0`**, Development Status `3 - Alpha`. Must exceed the
  `0.0.1.dev0` placeholder.
- **D-Brand — Site + all surfaces ship on placeholder `--abk-*` tokens with a single-file
  swap seam** (`brand.css` ↔ `TOKEN_FALLBACKS`, CI name+value gate); the final palette/logo
  drop-in is a logic-free follow-up commit (source: Claude design, possibly via `DesignSync`
  — §7). **Acceptance criterion of that drop-in: WCAG AA contrast in both themes + CVD-
  distinguishable chart/A-A-matrix colors** (branding-and-site §3), checked before it ships
  — the hex + name+value gates do not cover contrast (plan-review, critic 0).

## 4. M6 definition-of-done → WP map

| M6 obligation (ROADMAP / specs) | WP |
|---|---|
| `abk init-claude` + packaged `.claude` assets (rules + 7 skills) | WP2 |
| Single-source docs site (`abkit.pipelab.dev`, Astro + sync-docs) | WP3 (body) + WP7 (site) |
| Own palette/logo/landing on a themeable brand-token layer | WP7 (D-Brand) |
| Prefect flow/deployment scaffolding | WP6 |
| BI reference queries/dashboards (Grafana/Lightdash/Metabase/Superset) + optional SRM panel | WP4 (D5) |
| `abk test-report` channels | WP5 |
| Tooling debt closed (green pre-commit) | WP1 |
| Named-deferrals hygiene (no stale "planned for M6") | WP8 |
| `abk plan` runtime/ASN (was "→ M6") | **WP-A** |
| A/A sequential×composed sweep (was "→ M6") | **WP-B** |
| PyPI release `pip install ab-analysis-kit` (DoD) | WP9 (prep) + **G1** (publish) |
| Contributor `CLAUDE.md` + `.claude/rules` in sync | WP10 |
| Milestone exit gate (e2e + ≥2 review rounds + docs sync) | WP10 |
| `alpha_spending`/group-sequential | **deferred v2** (Named deferrals) |

## 5. Adversarial review record (M6 exit gate)

> To be completed at WP10. Follow the M4/M5 protocol: **≥2 full rounds**, refute-by-default,
> a second independent verifier per finding; per-WP mini-reviews recorded at each WP.
> Round-2 is mandatory (it caught an incomplete round-1 fix in both M4 and M5).
>
> **Round 1 — lenses:** packaging (wheel ships every asset; `pip install` resolves them),
> DX correctness (CLAUDE.section gotchas match M5 as-built — sequential opt-in, `abk
> validate` ≠ lint, orphaning), single-source drift (bodies a/b/c agree), brand-swap seam
> (every surface on the token layer; hex loop + name+value gate), reliability (idempotent
> init-claude, non-zero exits, no leaked alerting semantics in test-report), docs build
> (astro red on missing page; demo parity red on math drift).
>
> **Round 2 — re-review of the patched tree.**
>
> **Exit-gate invariants to reassert:** goldens untouched at rel-1e-9, no `ALGORITHM_VERSION`
> moved, `abkit.stats` purity intact (`tests/stats/test_purity.py`), bundles rebuilt+committed
> for any `web/src/**` edit, `web/` absent from the wheel, no external-host asset in any
> self-contained bundle.

## 6. Named deferrals (explicitly NOT in M6 — pushed to v2/future)

Per scope answer 1, these three M5-named "M6" items are **not** feature work in M6. Each
must be NAMED with a clean, honest error/message where a user reaches for it (WP8) — no
"coming in M6" text ships in v1.

> **Scope update (maintainer-confirmed):** only **one** item remains deferred. `abk plan`
> runtime/ASN and the A/A sequential×composed sweep were pulled INTO M6 as **WP-A** and
> **WP-B** — they are no longer deferrals.

| Deferred feature | Where a user reaches for it | Required M6 behavior (WP8) |
|---|---|---|
| **`alpha_spending` / group-sequential** sequential scheme | `experiment.yml` `sequential.scheme: alpha_spending` → `experiment_config.py` ValueError | Refuse cleanly: "`always_valid` is the supported scheme; group-sequential (`alpha_spending`) is a future item" — **no version promise** |

**No longer deferred (now shipped in M6):**
- **`abk plan` runtime / ASN** → **WP-A** (days-to-N from an `_ab_exposures` arrival rate +
  expected/average sample number for the always-valid design).
- **A/A sequential × composed** sweep → **WP-B** (`abk validate` on a `sequential.enabled`
  multi-metric family: composed FWER/FDR computed on the always-valid per-look significance).

## 7. Open questions for the maintainer (each needs a human decision)

1. **PyPI publish (G1, credential-sensitive, irreversible).** Confirm the OIDC
   trusted-publisher + the `pypi` GitHub environment are registered on PyPI for
   `alexeiveselov92/ab-analysis-kit` (so `publish.yml` needs no token). **Rotate/revoke
   the placeholder-upload token pasted in chat** (MEMORY `abkit-pypi.md`). Confirm the
   first release version is **`0.1.0`** and Development Status → **`3 - Alpha`**. Who
   pushes the `v0.1.0` tag?
2. **Live deploy (G2, external infra).** Is `PIPELAB_DISPATCH_TOKEN` set on this repo? Is
   the pipelab infra wired for `ab-analysis-kit-updated` and `abkit.pipelab.dev`
   DNS/cert? Authorize widening `website.yml` `paths:` and letting the dispatch fire.
3. **Branding assets.** Are the finalized abkit **palette + logo/light-dark lockups +
   favicon** available from Claude design yet? Is the `DesignSync` tool the intended
   mechanism to pull them? If not ready, confirm the placeholder-then-swap strategy (WP7,
   D-Brand) is acceptable for the M6 site launch.
4. **Operator rule-file set + skill count (D1).** Sign off the 9 rule files + 7 skills
   (this set is also the docs-site information architecture). Keep `abk-new-metric` AND
   `abk-new-experiment` separate (spec pins 7)?
5. **BI dashboard scope (D5).** SQL recipes for all four tools + one importable **Grafana**
   JSON — or a different first-class split? Per-tool JSON is version-fragile + branding
   burden.
6. **`profiles.yml` notification block (D6)** name + whether it lands in the typed
   `ProfilesConfig` model; **`abk test-report` bot name/avatar** asset (external) —
   abkit-branded or placeholder for M6?
7. **`.claude/rules/design.md` (D4)** — author it (donor has one; renders `/development/design/`)
   or drop that site page?
8. **Prefect target major (D9)** — `prefect.yaml` for Prefect 3, or a `.deploy()` script,
   or keep only the cron docstring?
9. **Tooling-debt gate (WP1→WP9)** — after the numpy-stub fix verifies clean, flip
   `mypy abkit` `continue-on-error` **off** for the release (in WP9), or keep it tolerated?
10. **~~Confirm the three deferrals are really out of M6~~ — RESOLVED (2026-07-06).** Maintainer
    decision: **`abk plan` runtime/ASN (WP-A) and A/A sequential×composed (WP-B) are IN M6**;
    only `alpha_spending`/group-sequential stays deferred to v2. §0.3-1, §4, §6, WP8, and the
    dependency graph updated accordingly.
11. **Single-source docs contract change (D2) — needs sign-off.** The specs (`cli-and-dx §5`,
    `branding-and-site §1`) promise "one Markdown body … rendered three ways, kept in lockstep
    by `sync-docs.mjs`." The donor actually authors **three separate bodies** and this plan
    adopts that (coverage gate + human review, not machine cross-generation). WP8 amends both
    specs to match. Confirm the weaker lockstep (coverage gate, not machine-sync) is acceptable,
    or require true single-source generation instead.

## 8. Session handoff / next-session inputs (2026-07-06)

**Merged to `main` this session** (maintainer authorized merge; branches integrated locally,
CHANGELOG conflicts reconciled): **WP1** (#19 tooling debt + this plan), **WP2** (#20
`abk init-claude` + 9 rules + 7 skills), **WP4** (#21 BI dashboards), **WP6** (#22 Prefect
deploy scaffold). `main` now carries the packaged `.claude` asset tree, `docs/examples/bi/`,
the `prefect.yaml` scaffold, and the green-pre-commit tooling fixes.

### 8.1 Incorporate the Claude-design deliverables (NEW — supersedes "placeholder-only")

> **✅ DELIVERED + LANDED (2026-07-07).** The maintainer dropped four Claude Design exports
> (Brand, Landing, Report, Explore Cockpit) into `abkit-design/` at the repo root. Decision
> taken (maintainer delegated): they are the **finalized brand source-of-truth**, so they were
> curated into **[`docs/design/`](../design/)** — the distilled buildable spec
> [`brand-tokens.md`](../design/brand-tokens.md), extracted `logo/*.svg` (+ favicon), a
> source-of-truth `README.md`, and the four faithful mockups under `mockups/`. Windows
> `:Zone.Identifier` junk removed; `.gitignore` hardened. **The brand is now real, not
> placeholder** — palette **Iris Violet `#6A45C4`** on detectkit's warm paper/ink, type
> Schibsted Grotesk + JetBrains Mono, logo "Diverge", five verdict tokens (win/lose/flat/
> inconclusive/srm), the signature stabilization-chart tokens. **D-Brand posture flips: WP7 +
> the report/explore surface pass build DIRECTLY on `brand-tokens.md`, not neutral
> placeholders** (the "single-file swap seam" becomes "seed the seam with the real values").
> The mockups are Claude-Design-runtime HTML (need `support.js`) — consumers **reproduce**
> the layout as framework-free, external-host-free bundles (invariant 6), pulling tokens from
> `brand-tokens.md`. Accessibility (WCAG-AA both themes + CVD) is checked against the real hex.

The maintainer has **created brand + product designs in Claude design** (brand design with
**logo**, **report designs**, and more). This changes the D-Brand posture: the site + report/
explore surfaces are no longer "ship on neutral placeholders indefinitely" — there are **real
design assets to pull in**. Next session:

- **Gather the designs first.** Pull the Claude-design pages (via the `DesignSync` tool if it
  resolves them, else the maintainer exports palette hex + logo SVG/PNG + report mockups).
  Enumerate what exists: brand palette (light+dark), logo/lockups + favicon, report layout,
  explore-cockpit layout, any channel/notification branding.
- **Where they feed:**
  - **D-Brand / WP7** — the real palette + logo replace the neutral `--abk-*` placeholders in
    `brand.css` + `TOKEN_FALLBACKS` + `Logo.astro` + `public/favicon` (the logic-free swap
    commit). The **WCAG-AA + CVD** acceptance criterion (WP7 hotspot) is checked against the
    REAL palette now, not deferred.
  - **Report / explore surfaces** — the report designs inform `web/src/report/**` +
    `web/src/explore/**` (rebuild + commit the bundles per the freshness gate). Treat any
    layout change as a `web/src/**` edit: `cd web && npm run build`, commit `abkit/*/assets/*.js`.
  - **`abk test-report` (WP5)** channel branding — the bot name/avatar asset (D-Brand / §7#6).
- **Add a design-source-of-truth note** to `branding-and-site.md §2/§5` (currently "finalized
  separately in Claude design") pointing at where the finalized assets live, so the swap is
  reproducible. Fold this spec-amendment into WP8/WP10.

### 8.2 Release / "update the lib" GitHub workflow (NEXT SESSION — WP9 / G1)

The maintainer wants to wire a GitHub workflow to **publish/update the library**. Current state:
`publish.yml` already exists (tag `v*` → OIDC trusted publish to PyPI, `environment: pypi`) and
`website.yml` exists (guarded on `website/Dockerfile`). Next session, as part of **WP9**:

- Confirm/finish the **release automation**: the version bump (`0.0.1.dev0` → `0.1.0`), the
  CHANGELOG cut, and how a tag is produced (manual `git tag v0.1.0`, a release-drafter, or a
  `workflow_dispatch` version-bump job). Decide whether "update the lib" = the PyPI publish on
  tag (G1), a docs-site redeploy (G2 via website.yml), or both.
- **G1/G2 remain maintainer-gated** — the workflow may be wired, but the actual publish/deploy
  (pushing the tag / merging `website/Dockerfile`) is a human action (§GATED, §7 Q1/Q2). Rotate
  the pasted PyPI token first (§7 Q1).
- Extend the wheel-namelist gate + add the `pip install ab-analysis-kit` DoD smoke (WP9 as
  written) so the published wheel is proven to carry the WP2 assets before any tag.

### 8.3 Remaining WPs (unchanged order)

WP3 (docs body) → WP7 (site, now brand-informed), plus WP5 (test-report), WP8 (deferrals
hygiene + the D2/design spec amendments), **WP-A** (`abk plan` runtime/ASN — stats-adjacent,
do with a design pass), **WP-B** (A/A sequential×composed — stats-adjacent), then WP9 (release
eng) and WP10 (exit gate, ≥2 adversarial rounds). WP-A/WP-B must stay stats-pure (no
`ALGORITHM_VERSION`, goldens untouched).
