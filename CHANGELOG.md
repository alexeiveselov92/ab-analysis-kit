# Changelog

All notable changes to ab-analysis-kit will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Once implementation begins, `CHANGELOG.md` is **authoritative for behavior changes**
— in particular every statistical deviation from the captured legacy baseline is
recorded here alongside an `ALGORITHM_VERSION` bump and a
[`statistics-changes.md`](docs/specs/statistics-changes.md) entry (never a silent
number change).

## [Unreleased]

## [0.1.2] - 2026-07-09

Explore-cockpit / CLI DX + reporting polish. **No statistical numbers changed**
(no `ALGORITHM_VERSION` bump, goldens intact, `abkit.stats` purity held) — every
change below is transport, logging, or presentation.

### Added
- **Brand logo in every generated surface** — the "Diverge" mark + `abkit` wordmark
  now render in the `abk run --report` and `abk explore` headers (inline SVG, shared
  `web/src/shared/logo.ts`), not just the browser-tab favicon.
- **Progress heartbeats on long-running compute** so a multi-minute run is no longer a
  silent freeze: `abk run` prints a throttled `LOOK i/N` per computed look; `abk
  validate` streams `scoring cell i/N` per cell; Auto mode echoes the same to the
  explore terminal.

### Changed
- **Explore Auto button is honest when unavailable** — on a `--no-serve` / saved-report
  page it now carries an actionable tooltip ("Auto needs a live server — open the
  printed localhost (127.0.0.1) URL, not a saved report") + `aria-disabled`, and it
  shows a `busy` state while a validation is in flight.
- **Removed the redundant explore "CUPED on/off" checkbox** — it was a pure UI alias of
  the method picker (it only strip/prepended `cuped-` and switched the method). CUPED is
  now chosen directly in the method picker as the `cuped-t-test` variant; no functional
  loss, one fewer duplicate control.

### Fixed
- **No more `BrokenPipeError` tracebacks from `abk explore`** — the stale-drop discipline
  (a knob turn aborting a superseded request) left the server writing to a closed socket;
  the transport helpers now suppress `BrokenPipeError`/`ConnectionResetError` (the latest
  request still computes and replies).
- **No more per-split `AbkitStatsWarning` flood during `abk validate` / Auto mode** — the
  A/A sweep re-invokes the same method over hundreds of placebo splits × looks, so the
  CUPED low-correlation / ratio-zero legacy guards spammed stderr thousands of times.
  They are now suppressed inside the scoring loop only (the single real `abk run` still
  surfaces them; also carried in `TestResult.warnings`). Non-numeric.

## [0.1.1] - 2026-07-08

Documentation + AI-assistant-context accuracy patch (no code, no statistical
numbers — a post-`0.1.0` fact-check of every published doc page and every packaged
`abk init-claude` asset against the shipped CLI/config/method surface). 15 verified
findings fixed; each was independently re-verified against the code.

### Fixed
- **Packaged `abk init-claude` assets now match the shipped API** (these ship in the
  wheel, so the fix ships in `0.1.1`):
  - Metric-SQL docs no longer reference a non-existent `{{ data_schema }}` template
    built-in (`rules/metrics.md`, `skills/abk-new-metric`) — `{{ data_database }}` is
    the single data-location built-in on every dialect (on Postgres it resolves to the
    profile's `data_schema` value); under `StrictUndefined` the old note would have made
    Postgres metric SQL fail to render.
  - `skills/abk-explore` no longer lists **the multiple-comparison correction** among the
    identity params that orphan an `_ab_results` series — the correction (like `alpha`)
    is experiment-level and never enters `method_config_id`; changing it re-arms the
    calibration chip but does not orphan results.
  - `skills/abk-validate` quotes the **actual** Recommended-row rationale ("highest power
    among methods with FPR within budget", tiebreak: tightest achieved MDE), not a
    CI-width criterion the selector never uses.
  - `rules/explore.md` marks **Segment mode** as a deferred placeholder (not an available
    0.1.0 cockpit mode).
  - `rules/project.md` describes env-var interpolation correctly (an unresolved
    placeholder is kept verbatim, not raised as an error; only channel secrets are
    actively rejected); `skills/abk-setup-project` scopes the "no `database:` key" note to
    ClickHouse (it is optional for MySQL, required for Postgres).
- **`abk test-report` + `notification_channels` are now covered** for the AI assistant and
  in the docs (they shipped in `0.1.0` but were undocumented): a `test-report` command
  entry + `## abk test-report` section (`rules/cli.md`, `docs/reference/cli.md`), a
  `notification_channels` block in `rules/project.md`, a routing entry in
  `CLAUDE.section.md`, and the command added to the enumerated command surface in
  `docs/README.md` / `docs/getting-started/installation.md`.
- **`abk plan` runtime/ASN documented as shipped**: `docs/reference/cli.md` no longer says
  runtime/ASN "is not part of this command", and the `--arrival-rate` flag is added to the
  `abk plan` option/flag tables in `docs/reference/cli.md`, `rules/plan.md`, and
  `skills/abk-plan`.
- The CI `install-smoke` version gate now compares `abk --version` against
  `abkit.__version__` dynamically (no longer hard-pinned to a literal), and the
  `abk --version` sample in `installation.md` tracks the release.

No `abkit.stats` change; no `ALGORITHM_VERSION` moved; goldens untouched at rel-1e-9.

## [0.1.0] - 2026-07-08

The first tagged public release — milestones **M1–M6**. The pure numpy statistical
core, the declarative YAML+SQL config / DB layer / recompute pipeline, the explore
cockpit + self-contained reports, `abk validate` (the A/A false-positive matrix),
opt-in sequential analysis + `abk plan`, and the M6 DX layer (`abk init-claude`,
`abk test-report`, the docs site, Prefect scaffolding). No statistical numbers
changed across M2–M6 (goldens intact at rel-1e-9; no `ALGORITHM_VERSION` moved).

### Added
- **M6 WP10 — the M6 exit gate: release-readiness e2e, ≥2 adversarial review rounds,
  and the coordinated milestone-header sync.** New `tests/e2e/test_release_readiness.py`
  proves the whole first-release journey offline and byte-reproducibly — `abk --version`
  reports the real (non-placeholder) release, `abk init` → `abk run --select` lands a real
  verdict-bearing `_ab_results` row, `abk run --report` bakes a self-contained zero-network
  readout, `abk init-claude` materializes the managed `CLAUDE.md` block + the 9 rules + 7
  skills (idempotently), and the committed renderer bundles are self-contained (offline, no
  external host). The *wheel-packaging* DoD — a built wheel shipping both bundles + every
  `abkit/cli/assets/claude/**` asset and resolving in a clean venv — is owned authoritatively
  by the CI `lint` wheel-namelist gate + the `install-smoke` job (across the Python matrix),
  which this fully-offline e2e complements deterministically. The as-built docs are flipped to
  one story now that M6 is shipped:
  the status headers in `CLAUDE.md`, `.claude/rules/architecture.md` (including
  `__version__` `0.0.1.dev0` → `0.1.0` in the banner), `.claude/rules/contributing.md`
  (the release checklist names the three single-source bodies + the packaging DoD), and
  `ROADMAP.md` (M6 ✅ SHIPPED; the sole `alpha_spending`/group-sequential deferral pointed
  at the future with no version promise). The exit-gate review (≥2 full rounds,
  refute-by-default, a second independent verifier per finding) is recorded in
  `docs/specs/m6-implementation-plan.md §5`. No `abkit.stats` change; no `ALGORITHM_VERSION`
  moved; goldens untouched at rel-1e-9; `abkit.stats` purity intact.
- **M6 WP9 — release engineering (prep only; the tagged publish is a separate,
  maintainer-gated step).** Bumped `__version__` `0.0.1.dev0` → `0.1.0` (the first
  real version must exceed the reserved placeholder or PyPI rejects the upload) and
  the packaging classifier to `Development Status :: 3 - Alpha`. Cut this
  `[Unreleased]` history into the dated `[0.1.0]` section. Hardened the release DoD
  with three new gates: (1) the CI **wheel-namelist gate** now also asserts the wheel
  ships every `abkit/cli/assets/claude/**` file (the 17 `abk init-claude` assets —
  the highest-risk packaging miss, since a bad wheel can't be re-uploaded under the
  same version), alongside the existing `report.js`/`explore.js` bundle check; (2) a
  new **`pip install` DoD smoke** job installs the *built wheel* (not `-e .`) into a
  clean venv on Python 3.10/3.11/3.12 and proves `abk --version` reports `0.1.0` and
  `abk init-claude -d <tmp>` materializes the managed `CLAUDE.md` block + the 9 rules
  + the 7 skills from `importlib.resources` at install time; (3) the WP8-promised
  cross-body **docs single-source drift gate** (`tests/docs/test_docs_single_source.py`)
  asserts every packaged operator rule in `abkit/cli/assets/claude/rules/` has a
  corresponding published `docs/` page — so a new rule cannot ship without a user-doc
  home. The `mypy abkit` strict gate stays `continue-on-error` (aspirational) for
  0.1.0: the ~124 tracked strict-mode errors live in numeric hot paths
  (`recompute.py`/`readout.py`) and clearing them is a post-0.1.0 quality pass, not a
  release blocker (§7 Q9 decision). No `abkit.stats` change; no `ALGORITHM_VERSION`
  moved; goldens untouched.

### Changed
- **M6 WP8 — named-deferrals hygiene: the shipped code, packaged assistant assets, docs, and
  specs now tell one true story about what is and isn't implemented (no behavior change).**
  Every "planned for M6 / deferred to M6 / M6 follow-up" string that pointed at a feature which
  actually **shipped** in M6 is flipped to shipped — `abk plan` **runtime/ASN** (WP-A) and the
  A/A **sequential × composed** sweep (WP-B) across `abkit/planning/__init__.py`, the packaged
  `abk init-claude` assets (`rules/plan.md`, `rules/validate.md`, `skills/abk-plan`),
  `docs/guides/plan.md`, `docs/specs/cli-and-dx.md`, `docs/specs/aa-false-positive-matrix.md`,
  `ROADMAP.md`, `.claude/rules/architecture.md`, and this repo's `CLAUDE.md`. The **one**
  genuinely unshipped item — `alpha_spending` / group-sequential — is re-pointed everywhere from
  "M6" to a **future item with no version promise** (the user-facing config error already refuses
  it cleanly). Three spec-reconciliations bring the as-built into line with the prose: the
  single-source docs model is documented as **three separately-authored bodies kept consistent by
  human review** (not machine cross-generation; a CI drift gate lands in WP9), the BI deliverable as
  **tool-agnostic reference SQL + one Grafana
  dashboard** (not a per-tool importable dashboard for each of the four), and project-level error
  *notification* as a **post-M6 item** (with `abk test-report` the shipped connectivity smoke). No
  `abkit.stats` change; no `ALGORITHM_VERSION` moved; goldens untouched.
- **M6 WP7b — the self-contained `abk run --report` + `abk explore` surfaces now render in the
  finalized Iris brand.** The one brand-token layer (`web/src/shared/chart.ts` `TOKEN_FALLBACKS`)
  was frozen from placeholder values to the real Iris palette (`docs/design/brand-tokens.md`):
  a warm-paper light page (`#f5f1e8`/`#1b1916`), a dark chart panel, the iris-family series
  slots (`#c9a6f0`/`#8e76e0`), and the five verdict/status tokens (WIN `#1e9e6a` … SRM `#b23a6b`).
  Both page shells (`abkit/reporting/html_report.py`, `abkit/tuning/html.py`) now carry the abkit
  **"Diverge"** brand mark as their favicon (iris tile + paper strokes) and open on warm paper —
  still fully self-contained (no network, no webfonts; system-font fallback). `report.js` +
  `explore.js` rebuilt. The CI token-sync gate is **promoted to a hard value check** for the
  theme-independent tokens now that the palette is frozen (per-theme surface tokens stay
  value-skipped). WCAG-AA contrast recorded for the reskinned text surfaces (body/muted/accent on
  paper and the dark chart panel all pass AA). No `abkit.stats` change; no `ALGORITHM_VERSION` moved.

### Added
- **M6 WP7b — an interactive stabilization demo on the landing page.** The marketing hero now
  mounts a live `#abk-demo` widget (dial true effect / noise / traffic, watch the cumulative
  effect + CI converge past the decision horizon and the WIN/LOSE/FLAT/INCONCLUSIVE verdict get
  called). Its compute core (`website/src/scripts/demo/stats.ts`) is a TypeScript re-derivation of
  `abkit.stats`, golden-parity-gated in CI (`check-demo-parity.mjs`, rel-1e-6), painted through the
  shared framework-free renderer core (`web/src/shared/chart.ts`). Everything is client-side and
  dependency-free. The `Notification channels` guide (`docs/guides/notification-channels.md`, WP5)
  is now wired into the docs site (sidebar + `sync-docs` PAGES).

### Fixed
- **`abk init` prod-profile env placeholders were double-wrapped (latent scaffold bug).**
  The `prod:` profiles in the three `abk init` templates used quadruple-brace
  `{{{{ env_var('ABKIT_*') }}}}` placeholders, but `profiles.yml` is written **raw** (never
  `.format()`-ed, unlike `abkit_project.yml`), so a set env var resolved to `{{value}}`
  (wrapped in stray braces) instead of `value`. Corrected to standard double-brace
  `{{ env_var('ABKIT_*') }}`. Latent because `abk init`'s scaffold self-check runs with the
  vars unset (a preserved placeholder validates fine either way). Surfaced while adding the
  WP5 `notification_channels:` seed block.
- **M6 WP1 — tooling debt root-caused + partly cleared (no behavior change).** The
  long-standing "`mypy` fails on clean HEAD" was **not** a numpy issue: a stray comment
  `# type: (required, optional)` in `abkit/config/metric_config.py` was parsed by mypy as a
  PEP-484 type comment (`Invalid syntax`), making it bail before type-checking anything.
  Reworded the comment; raised `[tool.mypy] python_version` to `3.12` (clears the secondary
  numpy 2.5 PEP-695 stub error); added `yaml.*` to `ignore_missing_imports`. `mypy abkit` now
  runs to completion (it reports ~124 real strict-mode errors, still `continue-on-error` —
  tracked debt, they live in numeric hot paths). Pinned `[dev]` `black==24.4.2` and
  `mypy==1.10.0` to the pre-commit revs so CI and local pre-commit cannot diverge (zero
  reformat churn). No runtime code changed; goldens untouched; no `ALGORITHM_VERSION` moved.

### Added
- **M6 WP5 — `abk test-report` + a minimal notification-channel layer (`abkit/notify/`).**
  A new command sends a **synthetic mock readout** through every channel in a new
  `profiles.yml` `notification_channels:` block and prints a per-channel ✓/✗ — a
  connectivity + formatting smoke test (no lock, no warehouse read, no statistics). Five
  channels ported and **reshaped** from detectkit's alerting channels — Slack, Mattermost,
  a generic webhook, Telegram, email — keeping the transport/envelope but dropping every
  alerting semantic (no severity / recovery / no-data / detector / quorum / consecutive
  machinery; abkit has no alerting). The message is experiment-primary: a verdict
  (WIN/LOSE/FLAT/INCONCLUSIVE, SRM-gate overriding), effect + CI, p-value, the effective
  post-correction alpha, and the weekly-cycle representativeness, colored by the five brand
  verdict tokens. Secrets come **only** from env interpolation (`${VAR}` / `{{ env_var(…) }}`)
  and an unresolved placeholder is refused with a clear error. `notification_channels:` is a
  new typed field on `ProfilesConfig` (`NotificationChannelConfig`, additive — existing
  `profiles.yml` files are unaffected); a commented example ships in the `abk init` seed.
  The command exits **non-zero** on any send failure / misconfiguration (the CLI-is-the-
  automation-unit convention). Pure Python, no new dependency (`requests` was already a
  dependency); `abkit.stats` untouched, no `ALGORITHM_VERSION` moved. Covered by
  `tests/notify/test_channels.py` + `tests/cli/test_test_report_command.py`.
- **M6 WP-A — `abk plan` gains runtime + ASN (read-only, no stats-core change).** Given a
  unit-arrival rate — derived read-only from `_ab_exposures` (new `get_arrival_rate`:
  distinct units per observed day, whole-cohort window, split to the control arm) or supplied
  via the new `--arrival-rate <units/day>` flag — each sizable comparison now also reports
  **runtime** (`days-to-required-N = required_n / rate` + the planned horizon) and, for a
  `sequential.enabled` sequential-eligible design, the always-valid **ASN** (average sample
  number): the expected control-arm N at which the confidence sequence first excludes zero
  under the true effect (H1) and the null (H0). ASN is a deterministic fixed-seed Monte-Carlo
  estimate over the canonical information-time process, crossing the **exact shipped CS
  boundary** (`abkit.stats.sequential`) — it adds no estimator and moves no
  `ALGORITHM_VERSION`; `abkit.stats` stays pure and byte-identical. No arrival data ⇒ runtime
  is SKIPPED with a reason (never guessed); a fixed-horizon/resampling design ⇒ `ASN n/a`.
  **Honest framing:** the always-valid design's *sample requirement* (N to reach a given
  power) is *larger* than the fixed required-N (the Robbins mixture CI is wider by design —
  the price of unlimited peeking), so the CS never lets you design for fewer units at the
  same power. The reported **ASN is a different quantity** — the expected *stopping* N,
  horizon-capped — guaranteed only against the horizon (ASN_H1 ≪ horizon-N; ASN_H0 ≈
  horizon-N; monotone in effect); vs required-N it is regime-dependent (can dip below in the
  underpowered/horizon-capped case, which the CLI line flags). The Monte-Carlo estimate is
  cross-validated against an independent scalar first-passage simulation in the tests.
- **M6 WP-B — the A/A composed sweep gains its always-valid (peeking) twin (no
  behavior change to the shipped single-look family).** `abk validate`'s composed
  multi-metric family sweep now mirrors the per-cell D8 trio at the family level: alongside
  the unchanged single-look `fwer`/`fdr`, it composes a matched **peeking pair** over the
  same shared placebo assignments — `fwer_peeking`/`fdr_peeking` (each member's fixed CI
  peeked across every look: the composed optional-stopping hazard, inflated) and
  `fwer_sequential`/`fdr_sequential` (the always-valid twin via the identical D8 estimator:
  controlled, ≈ the single-look rate). Gated on a sequential-eligible family (≥1 member has
  a frozen τ²); an ineligible member (bootstrap — unscorable from suffstats) is a full gap
  in every family, disclosed by the existing "scored in 0 iterations" warning. The numbers
  persist additively in the `_ab_aa_runs` sentinel row's `details.family` (no new schema
  column); the report's composed band renders a "peeking → always-valid" recovery stat.
  This is a validate-layer MODE transform reusing the M5 D8 estimator verbatim — **no
  `ALGORITHM_VERSION` bump, no stats-core number changed, the single-look family byte-stable
  (`sequential` defaults off)**. Closes the last non-`alpha_spending` A/A deferral
  (aa-false-positive-matrix.md §8.1). Pinned by the D8×D9 headline tests in
  `tests/validate/test_family_sweep.py` + the sequential-matrix e2e.
- **M6 WP7a — the abkit docs + marketing website (`website/`, Astro + Starlight).** A
  single-source site built from the `docs/` body via `sync-docs.mjs`, on the real Iris
  brand (`brand.css`, light+dark, name-locked to the bundles' `--abk-*` token layer), with
  the "Diverge" logo/favicon, a landing page, and an interactive stabilization-chart demo
  whose JS compute path is golden-pinned to `abkit.stats` (hard demo-parity CI gate). `web/`
  and `website/` are now an npm workspace (single root lockfile); a Docker-free `website` CI
  job runs sync + `astro check` + build + demo-parity. The live deploy (Dockerfile → GHCR →
  `abkit.pipelab.dev`) is a separate gated step. Renderer bundles unchanged.
- **M6 — user-facing docs body + brand source-of-truth.** The `docs/` guide/reference tree
  (WP3) and the finalized Claude Design brand deliverables under `docs/design/`
  (`brand-tokens.md`, logo SVGs, mockups) that the site and surfaces build on.
- **M6 WP2 — `abk init-claude` + packaged Claude Code context.** New command that
  installs AI-assistant context into a user's abkit project (idempotent,
  version-stamped, re-runnable after upgrade): a marker-delimited managed block in
  `CLAUDE.md` (existing content preserved; a stale versioned marker is refreshed in
  place), the 9 reference rules under `.claude/rules/ab-analysis-kit/`
  (overview, cli, project, experiments, metrics, methods, explore, validate, plan),
  and the 7 `abk-*` skills under `.claude/skills/` (setup-project, new-experiment,
  new-metric, explore, validate, plan, feedback). The source tree ships in the wheel
  (`abkit/cli/assets/claude/**`) and is read via `importlib.resources`. Ported from
  the detectkit donor (cli-and-dx.md §5); mechanism domain-agnostic, content authored
  for A/B analysis and fact-checked against the M5 as-built engine.
- **M6 WP4 — BI reference queries + dashboards (`docs/examples/bi/`).** Connect
  Grafana / Lightdash / Metabase / Superset to the `_ab_results` contract table:
  `queries.sql` (8 tool-agnostic recipes — headline scoreboard, the effect+CI
  stabilization chart, raw/CUPED arm values, significance-vs-effective-alpha,
  MDE/power, cross-experiment board, freshness, config-drift detector),
  `srm_panel.sql` (the SRM validity guard), one importable `grafana_dashboard.json`
  (ClickHouse), and a README documenting the five hard invariants (read `FINAL`;
  group by `method_config_id`; compare to the row's two-tier `alpha` not 0.05;
  respect the pre-horizon peeking guard via `is_horizon`/`ci_kind`; handle NULLs).
  Guarded by `tests/reporting/test_bi_examples.py`, which fails if a recipe drifts
  from the real `_ab_results` schema. Docs/SQL only — no runtime code.
- **M6 WP6 — Prefect deployment scaffold.** `abk init` now also scaffolds
  `runners/prefect.yaml` (a Prefect 3 project-deploy config — `prefect deploy --all`
  schedules the daily `abk run`) beside the existing `runners/prefect_flow.py`.
  Documents the `tag:actual` convention the daily job relies on (tag live experiments
  `actual`; the demo is tagged `example` so the schedule skips it) and pins the
  targeted Prefect major. The `[orchestration]`/`[all]` extras now require
  `prefect>=3.0` to match the scaffolded syntax (abkit still never imports prefect).
  Scaffold test asserts the deployment is valid YAML and the flow parses.
- **M5 — sequential analysis, the always-valid CI, `abk plan`, composed corrections.** Opt-in
  (`sequential: {enabled: true}`, **default off** — the fixed-horizon series is
  byte-identical, no `ALGORITHM_VERSION` bump, goldens untouched). Landed so far
  (implementation record: [`m5-implementation-plan.md`](docs/specs/m5-implementation-plan.md);
  math: [`statistics-changes.md §4.1`](docs/specs/statistics-changes.md)):
  - **The always-valid confidence sequence** (`abkit/stats/sequential/`) — an
    asymptotic Gaussian confidence sequence (Waudby-Smith & Ramdas normal mixture)
    computed as a pure experiment-level MODE transform over the fixed `(effect, SE)`,
    never a method plugin. SE recovered by CI-inversion (preserving the delta-method
    covariance); the mixing variance `τ²` is anchored to the first usable look
    (stable across runs, computable live). Rows carry `ci_kind='always_valid'`.
  - **The A/A matrix's sequential side-by-side column (D8)** — `abk validate` now
    measures the always-valid peeking FPR, power, and CI-width beside the fixed ones:
    where the fixed peeking FPR breaks budget, the always-valid twin returns to ≈α (the
    honest completion of the peeking story). Surfaced in the matrix report (a "peeking
    (AV)" column + a second curve) and the live explore calibration chip.
  - **Pipeline activation** — a plain `abk run` on a sequential-enabled experiment emits
    always-valid rows. `scheme: alpha_spending` (group-sequential) is a clear
    "planned M6" config error.
  - **The toggle self-invalidates (B4)** — flipping `sequential.enabled` on an *existing*
    experiment now re-plans the affected series in place on a bare `abk run` (no
    `--full-refresh` needed): `sequential.enabled` is deliberately not in
    `method_config_id`, so the planner compares the persisted per-pair `ci_kind` against
    the mode this run stamps and forces a full recompute on a mismatch — idempotent
    (a steady sequential experiment still plans zero) and robust to the first-usable-look
    τ² anchor legitimately leaving a later-usable pair fixed.
  - **Explore threading (B5)** — the live explore recompute now mirrors the baked
    per-pair CI vocabulary so the cockpit never mixes fixed & always-valid on one chart.
    A pair is widened live iff its **persisted** rows are already always-valid (a
    read-view of what `abk run` stored — so the multi-pair case where the anchor left a
    late-usable pair fixed, and a not-yet-applied config toggle, both stay consistent);
    each widened point uses the same first-usable-look τ² (the configured knob state
    reproduces the baked always-valid bounds — exactly for the closed-form families).
    α-inversion cannot honestly widen an already-widened persisted CI, so under the mode
    those cutoffs are dropped with a Reload hint rather than shown as a silent fixed CI;
    a switch to a sequential-ineligible method (bootstrap) turns the mode off. Server-only
    — no bundle change (the client draws whatever bounds the reply carries).
  - **The readout reads always-valid rows early (WP4)** — the pre-horizon withholding
    that refuses WIN/LOSE/FLAT before the planned horizon now lifts for a row whose
    persisted `ci_kind` is `always_valid` (a fixed row is still withheld). An early
    decisive verdict names its own justification ("called before the planned horizon
    under an always-valid confidence sequence — peeking-safe by construction"). The
    "covers X% of a weekly cycle" representativeness caveat on a sub-week verdict is
    promoted from a caveat bullet to a structured `weekly_cycle_pct` rendered as a chip
    on the HTML report's verdict card. The daily-SRM posture under sequential is settled
    (plan D9): daily & coarser keep the χ² gate (bounded looks on a ~3.3σ hard gate ⇒
    negligible peeking inflation); only sub-day (a follow-up) swaps to the anytime-valid
    multinomial test.
  - **Sub-day anytime-valid SRM (WP5)** — below 1d cadence the SRM gate swaps from χ²
    to an anytime-valid Dirichlet-multinomial e-process (Lindon & Malek 2022;
    [`statistics-changes.md §4.2`](docs/specs/statistics-changes.md)): a dense sub-day
    cadence would peek the χ² hard gate dozens of times a day → false alarms, whereas the
    e-process is valid at every look by construction. Dispatched on
    `experiment.is_sub_day()` (daily & coarser are unchanged). One verdict **per look**,
    stamped from the cumulative as-of exposure counts (`get_exposure_count_stream`) — the
    truthful as-of series the M2 whole-cohort broadcast deferred — and it runs even on
    demoted rows. Default prior is the paper's uniform `Dir(1,…,1)`; the anytime
    false-alarm rate holds ≤ α for any fixed prior. It is an additive gate, not a
    registered method: **no `ALGORITHM_VERSION` bump, goldens untouched**, no schema change
    (reuses `srm_flag`/`srm_pvalue`).
  - **`abk plan` — the read-only pre-launch power/sizing planner (WP6)** —
    `abk plan --select <exp> [--metric <m>] [--mde <pct>] [--power] [--alpha] [--baseline]`
    reports, per comparison, the **required sample size** to detect a target MDE, the
    **achievable MDE** at the current size, and the **achieved power** — at the effective
    two-tier alpha — plus the projected **look count** and cost shape from the same
    `generate_grid` enumeration `run`/config-lint use. Baseline moments come from the
    latest persisted `_ab_results` per-arm stats (a `--baseline metric:mean=..,std=..,n=..`
    override sizes a greenfield experiment); the target MDE defaults to the comparison's
    `min_effect`. **Strictly read-only** — no lock, no `_ab_*` writes. Refuses what it
    cannot size honestly: **ratio** and **bootstrap/resampling** methods have no versioned
    power formula (SKIPPED, never invented math), and CUPED is sized on the raw persisted
    variance (ρ is not persisted per row) as a flagged conservative upper bound. **Runtime
    / ASN** (days-to-N from an arrival rate + the sequential design's average sample
    number) are a named **M6** deferral.
  - **The composed multi-metric FWER/FDR family sweep (D9, WP7+WP8)** — M4 validated only
    the per-cell peeking FPR at the correct two-tier alphas; D9 closes the family-level
    loop. The read-time composed rule (two-tier Bonferroni ∘ Benjamini-Hochberg) is
    extracted from the readout's inline `_build_sig_map` into one shared pure helper
    (`stats.correction.composed_significance`, WP7) that the readout and the sweep both
    apply — a behavior-preserving refactor (goldens untouched, verdict-snapshot pinned).
    `abk validate` then runs the sweep: each iteration draws **one** unit→arm assignment
    over the **union** of the metrics' cohorts (the real single-assignment semantics; no
    imputation — a unit absent from a metric doesn't contribute), scores every metric at
    its horizon, and tallies the empirical **family-wise error rate** (any false rejection)
    and **false-discovery rate** (mean false fraction among rejections). On the placebo
    (complete) null FWER and FDR coincide by construction, at the composed rule's nominal
    rate (≈α per tier, so ≈2α whole-family under the default two-tier Bonferroni); the
    budget is anchored to that nominal rate so "over budget" flags a miscalibrated method
    (clustering), not a loose correction. A planted true effect in one metric leaves the
    null metrics' family error controlled. Persisted as one
    sentinel `_ab_aa_runs` row (`metric='__family__'`, numbers in `details`) — no schema
    change, never lights the per-cell calibration chip — and surfaced as a composed-family
    band above the report's A/A matrix (`report.js` rebuilt). Fixed-horizon only;
    sequential × composed is a named **M6** follow-up.
- **M4 — `abk validate`, the A/A false-positive matrix.** The trust artifact that
  answers "is this method actually calibrated on this data, or does it lie about its
  α?" (docs/specs/aa-false-positive-matrix.md; the implementation record is
  [`m4-implementation-plan.md`](docs/specs/m4-implementation-plan.md)):
  - **`abk validate --select <exp> [--method <m>] [--metric <m>] [--iterations N]
    [--inject-effect <pct>] [--scoring fpr|power|mde] [--report] [--force]`** — draws N
    deterministic placebo A/A splits over the experiment's own pooled cohort
    (label-permutation, an exact null by construction), scores each declared method's
    empirical **single-look FPR**, **cumulative-peeking FPR**, **power @ MDE**,
    **achieved MDE**, **CI coverage**, and **effect-exaggeration-at-stop**, and persists
    one `_ab_aa_runs` audit row per cell at the effective per-comparison alpha. Its own
    out-of-band lock (`process_type='validate'`, `abk unlock`-clearable); non-zero exit
    on failure; stages `LOAD → RESAMPLE → SCORE → PERSIST` (distinct copy from `abk run`'s
    config-lint `VALIDATE`).
  - **Honest peeking FPR** — the naive optional-stopping hazard (CI-excludes-zero at
    *any* look, pre-horizon refusal off), reported *beside* the single-look FPR so the
    jump is visible, with the per-look cumulative curve. Deliberately not the readout's
    stabilized verdict (that is the *defense*); `pipeline/readout.py` is untouched.
  - **The matrix UX** — budget-band-colored FPR cells, an explicit **Recommended** row
    (FPR-closest-to-nominal, max-power) with a truthful one-line rationale, plain-language
    per-method verdicts, and the "nominal α 5%, real peeking FPR X%" headline. Rendered by
    `abk validate --report` reusing the committed report bundle (no third JS bundle) and
    surfaced live by the explore calibration chip.
  - **Auto mode** — a real server-side `POST /validate` (was a 501 stub) runs a reduced
    validate, refreshes `session.aa_rows` in place so the D3 chip greens without an
    explore restart, and re-seeds the knobs to the recommended config. The Apply gate is
    unchanged (an uncalibrated Apply still confirms).
  - **`metric.aa_fpr_budget`** (a fraction in `(0,1]`) completes the budget resolver
    (metric → project → α×1.5); added to the §8 validation matrix.
  - **No statistical numbers changed** — validate reads the existing `from_suffstats`
    methods; the goldens are untouched and no `ALGORITHM_VERSION` was bumped.

### Fixed
- **M3 milestone review closure** (the WP10 exit gate: 7 lenses / 17 raw
  findings, verified + inline-triaged — 13 real, all fixed; the full record
  is [`m3-implementation-plan.md §5`](docs/specs/m3-implementation-plan.md)):
  - **Apply writes are atomic**: the final YAML overwrite goes through
    temp + `os.replace` (+fsync) — an ENOSPC/kill mid-write can no longer
    leave the live config torn while the reply claims nothing was written.
  - **Guardrail regression is correction-independent**: judged from the
    STORED CI bounds per D5(c) — BH adjustment can no longer un-flag a
    stored-significant harm and un-block a WIN (known-answer test added).
  - **SRM stays loud over an empty main series**: the summary scans ALL
    comparisons' series, so the state an explore Apply produces (main series
    empty under its new id, flagged rows elsewhere) no longer renders a
    green "SRM ok" chip.
  - **The D3 Apply gate keys role flips at the PROSPECTIVE alphas**: posted
    is_main/is_guardrail flips overlay the prospective experiment before
    `effective_alphas`, closing the under-gating latent behind the empty
    `_ab_aa_runs` (server + regression test).
  - **Ctrl-C cannot swallow a successful Apply**: `serve_explore` returns
    the applied config even when SIGINT races the post-Apply self-shutdown
    window — the orphan/re-run epilogue always prints.
  - **Stale mid-series horizons render honestly**: both charts corroborate
    a stored `hz=1` row against the CURRENT config horizon, so an
    `end_date` extension no longer paints later cutoffs as decision-grade
    solid CIs (§4).
  - **Cockpit dirty-state fidelity**: `edited` keeps FULL params (an edit
    back to a spec default no longer silently reverts to the configured
    value on a rail rebuild; wire bodies are minimalized at send time), and
    the confirm box's "Apply anyway" runs the same preflight as the Apply
    button (a pending Tier-R edit can no longer ride into the YAML).
  - **Orphan warnings survive unbindable legacy method blocks**; the client
    remembers a completed covariate `/reload` (no redundant re-renders);
    the explore bake test asserts `https://` too; `build.mjs` fails on
    `</script`/`<!--` tokenizer hazards inside a bundle; header period
    timestamps are labeled UTC next to the experiment-tz name.

- **M3 WP5/WP6/WP8 review-closure** (adversarial review, 4 lenses / 25 raw
  findings; the verify fleet was limit-truncated, findings triaged inline —
  7 real after dedup):
  - The D3 calibration gate lost its side doors: **correction-only** and
    **role-flip-only** Applies now gate too (a correction edit re-keys every
    comparison; a role flip moves comparisons across the two Bonferroni
    tiers), and the gate keys by the **prospective EFFECTIVE per-comparison
    alpha** (`effective_alphas` over the applied alpha/correction), not the
    raw body alpha — restoring the mechanically testable "every Apply takes
    the confirm path" DoD. Params carrying a riding `"name"` key are keyed
    exactly as the writer strips them; unbindable params gate conservatively
    instead of silently skipping the check.
  - Handler-thread hardening: a malformed `Content-Length` header and a
    non-numeric `alpha` in the `/apply` body are clean 400s (previously a
    dead thread with no HTTP reply); `/apply` is **serialized** under the
    request lock (two tabs cannot race the archive/rewrite seam or the shared
    CLI-thread DB manager) and a second Apply after a successful one is a 409;
    the self-shutdown thread now spawns in a `finally`, so a client that
    vanishes mid-reply can no longer leave the server alive with the YAML
    already rewritten (Ctrl-C would then have lied "experiment unchanged").
  - `/reload` refuses on a budget-degraded (suffstats-only) session instead
    of silently growing a shadow cache the replies keep contradicting, and
    keeps `session.cache_values` accounting exact when replacing entries.
  - The HTTP `comparisons` parser preserves an ABSENT `params` key as `None`
    (the writer's "a method switch must carry the full param set" guard was
    bypassable with a fake `{}`); the provenance header sanitizes newlines
    (no comment-escape injection into the emitted YAML); the WP5 role-flip
    test now proves the promised per-comparison alpha shift on a
    three-comparison fixture (`0.05 → 0.025`), not a structural equality.

### Added
- **M4 WP5 — the A/A calibration matrix report + payload block + metric budget**
  (per [`docs/specs/m4-implementation-plan.md`](docs/specs/m4-implementation-plan.md)
  WP5/D10/D12): `abk validate --report` now bakes a self-contained matrix page
  by **reusing the committed report bundle** (no third JS bundle) — the report/
  explore payload's reserved `calibration` block is filled from the latest
  `_ab_aa_runs` invocation (`abkit/reporting/calibration.py`), so the offline
  readout and the live explore chip both surface the *"nominal α X%, real peeking
  FPR Y%"* headline, the per-method matrix (FPR coloured against the
  `aa_fpr_budget` band, the **Recommended** row + rationale, plain-language
  verdicts), and the recommended cell's cumulative peeking-FPR-vs-looks curve.
  The scorer now emits that monotone `peeking_curve` (one point per grid look,
  ending at the reported peeking FPR — the "peeking is the product" visual).
  Adds `MetricConfig.aa_fpr_budget` (a fraction in `(0, 1]`) completing the
  `resolve_fpr_budget` chain (metric → project → `α × 1.5`). No payload version
  bump; no statistical-number change (goldens untouched). The standalone WP4
  matrix template is retired in favour of the shared bundle.
- **M3 WP7 — the explore cockpit client** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md)
  WP7; data-contract §5.1 as amended by D9/D12): the browser half of
  `abk explore`, ported from the detectkit `tune.ts` skeleton to
  `web/src/explore/` and committed as the wheel-shipped
  `abkit/tuning/assets/explore.js` (replacing the WP6 placeholder). The
  windshield: the stabilization chart with D1-tier-styled live segments
  (solid exact, hatched "approx (α-only)", the persisted baseline always
  visible), §4 dashed pre-horizon CIs, greyed insufficient spans, run breaks
  at server-refused cutoffs, an off-scale indicator, and pinned chips (lift,
  ±CI, p, power, the D3 calibration chip incl. the alpha-mismatch downgrade,
  the red SRM gate, the sub-day look counter) re-keyed from every
  `/recompute` reply. The side rail is auto-derived from `param_specs`
  (Basic = method/CUPED/test_type/alpha; an Advanced disclosure for the
  rest + correction; identity ⚠ and Tier-R ↻ badges; the donor's slider
  identity hazard ported). Tier-R edits route through a per-metric confirm →
  `POST /reload`; Apply follows the dirty-slot discipline (role-only entries
  carry no method key; minimal params) behind the uncalibrated-cost confirm
  mirroring the server gate, with the archive/orphan/`abk clean` epilogue.
  The donor's stale-drop discipline is re-expressed over HTTP: a monotonic
  `request_id` seeded from `Date.now()` (re-seeded after a two-tab 409),
  `AbortController` kill-not-queue, stale replies never clear the spinner,
  the 130 ms debounce with the flush-before-switch trap. The client resolves
  raw alpha + correction to the effective per-comparison alpha by mirroring
  `analyze.effective_alphas` over the new
  `payload["explore"]["experiment"]` block (raw alpha, correction + choices,
  `groups_count`, `non_main_count`). Toolchain: a second `build.mjs` bundle
  entry (marker-gated), `--abk-explore-accent` joins the brand-token layer,
  the CI hex loop covers `tuning/html.py`, the wheel gate asserts
  `explore.js`, a jsdom smoke suite drives the live half through a fake
  `fetch`, and `tests/tuning/test_explore_bundle.py` pins the bundle
  packaging + the alpha-mirror substrate. Reviewed: 11 findings fixed
  pre-merge (stale cached-reply adoption on metric switch, surfaced-subset
  `non_main_count`, two-tab 409 lockout, reload-pending Apply bypass, chart
  listener leak, and six more).

- **M3 WP10 — the e2e exit gate** (per the plan WP10):
  `tests/e2e/test_first_report.py` (scaffold → `abk run --report` → a
  verdict-bearing, self-contained readout with the baked payload asserted
  structurally; re-run byte-stable modulo `generated_at`; a builder crash
  yellow-skips) and `tests/e2e/test_explore_session.py` (the real explore
  server over live HTTP: persisted numbers reproduced at rel-1e-9, Tier-E
  alpha recompute + α-inversion on a suffstats-only CUPED series, the stale
  409, the Apply gate → `.history` archive → orphan block → self-shutdown).

- **M3 WP8 — `abk explore`** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md)
  WP8; cli-and-dx §1): the cockpit shell —
  `abk explore --select <exp> [--metric <m>] [--no-serve] [--no-open]
  [--profile]`. Registered per the house pattern (eager stanza, lazy command
  body — `abk --version` stays instant). Resolves exactly ONE experiment
  (selection errors name the namespace), guards a never-run project with the
  friendly "run `abk run` first" noop (D2), prints the startup orphan warning
  (the same `list_method_config_ids` scan the driver and `abk clean` use),
  streams the session load through the house `StageLogRenderer`, then serves
  the WP6 cockpit — or, with `--no-serve`, atomically writes the static
  `reports/<experiment>__explore.html` snapshot (null endpoints — the
  preview badge, Apply disabled). `--metric` narrows the opened comparison
  (default: the main metric). The Apply epilogue echoes the archive path,
  updated/preserved comparisons, the orphan warning + `abk clean` hint, and
  the "re-run `abk run --select <exp>`" reminder; Ctrl-C cancels with the
  experiment unchanged. All failures exit non-zero (the house rule).

- **M3 WP6 — the explore localhost server + page + payload** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md)
  WP6/D1/D3):
  - `abkit.tuning.server`: `build_explore_server` / `serve_explore` — the
    donor's exact interaction contract on `127.0.0.1:0` with a one-shot
    token: GET serves ONE pre-rendered page on any path (the token gates only
    POSTs); `POST /recompute` answers knob states from the in-memory session —
    repeatable, advisory, lock-serialized, **stale-dropping** (outdated
    `request_id`s get `409 {stale}` before AND after the compute lock —
    debounced knob drags never queue behind an in-flight bootstrap) and
    silent; `POST /reload` executes the confirmed Tier-R actions with its OWN
    manager inside the serialized handler (re-rendering cached cutoffs under
    the requested lookback — the session tracks per-entry render lookbacks so
    the refreshed cache serves subsequent `/recompute`s) and streams a
    run-log through `server.echo`; `POST /validate` is the reserved M4 slot
    (501); `POST /apply` is the only terminal action — the **server-side
    calibration gate** (D3: `confirm_uncalibrated` required while the applied
    `(metric, method_config_id, alpha)` keys are not green — with
    `_ab_aa_runs` empty until M4 every Apply takes the confirm path), the WP5
    seam, the `orphaned` block + warning echoed in the reply, then
    self-shutdown from a daemon thread. Invalid configs return 400 and KEEP
    serving; error detail travels in the UTF-8 body (never the latin-1 status
    line); oversized bodies drain-then-413; no pipeline lock is ever taken.
  - `abkit.tuning.html`: `render_explore_html` — the WP3-hardened template
    mechanics verbatim (one-pass regex substitution, every `<` in the baked
    JSON escaped, no webfonts, `abk-explore` mount, `__ABK_EXPLORE__`
    global). Ships with a committed placeholder `assets/explore.js` (honest
    pending note) until the WP7 cockpit bundle replaces it — the wheel
    packaging contract was pre-wired in WP3.
  - `abkit.tuning.payload`: `build_explore_payload` — the WP2 report payload
    riding verbatim + the `explore` block (knob surfaces from `param_specs`,
    per-metric initial calibration chip state keyed by the configured
    `(method_config_id, alpha)`, session-cache facts, ms-epoch cutoffs) and
    the four endpoint slots (`None` = the static `--no-serve` preview badge).

- **M3 WP5 — Apply, `.history`, orphan detection** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md)
  WP5/D4/D9):
  - `abkit.tuning.config_writer`: `apply_tuned_config` — the ONLY mutation
    seam of `abk explore`, donor-disciplined **validate → archive → re-emit**:
    per-comparison `method` blocks (matched by metric; a merely-viewed
    comparison is never written — the dirty-slot discipline), Review-mode
    `is_main_metric`/`is_guardrail` flips (marking only, D9), and
    experiment-level `alpha`/`correction`, merged into the parsed document and
    validated as a whole (`create_method` per touched method +
    `ExperimentConfig.model_validate`) before ANY filesystem write. Tunability
    is registry-derived (paired designs and cross-kind methods refused — never
    a hardcoded name set); identity-excluded params (`seed`,
    `max_block_bytes`) carry over from the slot being retuned via the specs.
  - The previous YAML is archived **byte-verbatim** (comments included) to
    `<dir>/.history/<experiment>/<experiment>-<stamp>.yml` before overwrite —
    repeated Applies each archive, same-second Applies de-collide, and
    discovery never picks archives up as live configs. Comments die on
    re-emit (owner-ratified D4); re-emission is isolated behind the ONE
    `_reemit_yaml` strategy function so a comment-preserving ruamel backend
    can swap in later without contract changes.
  - **Orphan detection** (NEW vs the donor): old-vs-new `method_config_id`
    per touched comparison through the single hashing path; an identity edit
    over a series with persisted rows yields the `orphaned` block + the
    driver-identical warning (`abk clean` + `abk run --select` hints) in the
    result, and the provenance header. Apply **never** auto-cleans or
    auto-runs; alpha-only edits and role flips are orphan-free by
    construction.

- **M3 WP4 — the explore recompute engine** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md)
  WP4/D1/D3/D11/D12):
  - `abkit.tuning.session`: `load_session` — the one warehouse load pass at
    explore start (D2): the persisted per-comparison series plus the bounded
    Tier-S per-unit cache (latest cutoffs first, older newest-first under a
    ~2×10⁷-value budget; over-budget degrades honestly to a suffstats-only
    session with a reason string, never a silent partial cache).
  - `abkit.tuning.recompute`: `RecomputeEngine` — one knob state answered
    entirely in memory (D1, "no *warehouse* round-trip per knob change"):
    **Tier E** exact suffstats reconstruction across the whole grid for the
    closed-form families (t-test `m2 = std²·n`; z-test `nobs` inverted from
    the persisted SE — never from the one-row-per-unit `size_i`; ratio-delta
    via the exact denominator≡1 surrogate; CUPED→t-test "CUPED off" rides the
    persisted ORIGINAL per-arm mean/std), **Tier α** alpha-inversion for
    closed-form rows (symmetric normal CIs only — resampling families are
    declaratively excluded), **Tier S** `from_samples` over the session cache
    (bootstrap knobs, the stratify toggle, CUPED param edits) with the
    per-row seed re-derived by the persisted convention so unchanged knobs
    reproduce stored rows byte-exactly, and **Tier R** classification for
    CUPED off→on / `covariate_lookback` edits (the serialized `/reload`
    executes them, WP6). Per-pair points carry an exact/approx/baseline tier;
    windshield chips (lift, CI half-width, p-value, achieved power at
    `min_effect` with honest capability notes); the live `method_config_id`
    hashed only through the bound-probe path; knob metadata auto-derived from
    `param_specs` (nothing special-cases a method name; a supplied `seed` is
    ignored with a warning); `QuarantinedMethodError` surfaces verbatim.
  - `find_calibration` + `resolve_fpr_budget` (D3): the calibration chip
    lookup keyed by `(metric, method_config_id, **alpha**)` against the
    as-built `_ab_aa_runs` (`status='failed'`/FPR-less rows never count;
    alpha edits downgrade to `alpha_mismatch`; identity edits flip to
    uncalibrated — that IS the staleness semantics); budget resolves
    metric-seam → project `aa_fpr_budget` → `α × 1.5`.
  - `pipeline.analyze.build_container` is now public (shared by the engine's
    Tier-S path — byte-identical containers to the pipeline);
    `InternalTablesManager.aa_runs_table_exists()` guards chip reads on a
    never-validated project. Sidedness + winsorization stay OFF the knob
    surface (D12) — deferred to M4 under change control (ROADMAP note).

### Fixed
- **M3 WP4 review-closure** (adversarial review, 4 lenses / 15 findings, the
  blocker empirically reproduced by an independent verifier):
  - `RecomputeEngine.recompute` gained the `analyze_cutoff`-parity gate: a
    paired or cross-kind knob state (e.g. `t-test` on a fraction series, whose
    persisted `std_i` is the SE, not a sample std) now raises
    `MethodParamError` instead of returning a silently ~nobs-fold-collapsed CI
    labeled `tier="exact"` (the confirmed major).
  - Tier E now refuses rows whose per-arm columns don't carry mean/std
    semantics: a resampling series with a non-mean `stat` (e.g. median
    bootstrap) persists the bootstrapped statistic in `value_i` — such rows
    recompute only through the Tier-S cache (correct) or stay gaps, never
    "exact" numbers off the median. Unknown/quarantined legacy row methods are
    likewise never reconstructed.
  - New declarative `BaseMethod.requires_covariate` capability flag (CUPED +
    post-normed families): the Tier-S cache gate reads it instead of guessing
    from param names, so `post-normed-bootstrap` — which needs `cov_array` but
    has no `covariate_lookback` param — yields an honest gap on a
    covariate-less cache instead of an unhandled `SampleValidationError`.
  - Demoted (`insufficient_data`) and NULLed (H5) rows now pass through the
    reply untouched as flagged `baseline` points (NULL test columns, real
    sizes) instead of vanishing; the windshield chips read the latest point
    *with inference*, so a demoted latest cutoff no longer blanks or shifts
    them silently.
  - Point `size_i` keeps the persisted unit-count semantics across every tier
    (a fraction result's `round(nobs)` no longer makes sizes jump between
    tiers of one series; the method sizes stay on the raw `result`); the
    fraction power chip solves on trial counts (`nobs`) from the
    reconstruction, falling back to SE-inversion.
  - The session load clamps the cache during the latest-cutoffs pass, bounding
    the transient peak near the budget in the exact scenario the clamp exists
    for; `knob_surface` additionally exposes `needs_covariate` per method, the
    `correction_tier` (correction resolves to the effective alpha upstream —
    the WP4 DoD's experiment-level-knob classification), and the cache's
    `covariate_cutoffs` (the WP7 ↻-badge substrate).

### Changed
- **D11 — canonical unit order in `load_metric`** (M3 WP4; recorded in
  [`statistics-changes.md §8`](docs/specs/statistics-changes.md); a
  pipeline-level input-assembly fix, NO `ALGORITHM_VERSION` bump): every
  variant's per-unit arrays are sorted by unit key after fetch, making
  order-dependent bootstrap replicates reproducible across physical warehouse
  read orders (ClickHouse guarantees none). Bootstrap rows persisted before
  the sort may differ from re-computed ones on backends that happened to
  return a different order; closed-form results are order-invariant.

### Added
- **M3 WP3 — the self-contained HTML readout + `abk run --report`** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md)
  WP3/D7/D8):
  - `abkit.reporting.html_report`: `render_report_html(payload)` — one
    offline HTML per experiment (baked payload + the inlined committed
    `assets/report.js` bundle; framework-free, zero network requests, no
    webfonts — the donor's Google-Fonts links are deliberately dropped).
    Template mechanics per the donor (escaped title; data-URI favicon; never
    `.format`), hardened past it after the WP3 adversarial review: the baked
    JSON escapes **every `<` as `\u003c`** (escaping only `</` leaves the
    HTML tokenizer's `<!--`+`<script` double-escaped state able to swallow
    the real terminator), placeholders substitute in **one regex pass** (a
    payload string containing `__REPORT_JS__` can no longer be clobbered),
    and the CLI writes the file **atomically** (temp + `os.replace`) so a
    mid-write failure never truncates a previous good report.
  - `web/` — the dev-only bundle toolchain (D7): `web/src/shared/payload.ts`
    (the §5.3 contract in documented lockstep with `builder.py`),
    `web/src/shared/chart.ts` (canvas primitives + the one placeholder
    brand-token layer per branding-and-site.md §3), `web/src/report/report.ts`
    (the experiment-primary renderer: verdict banners with rationale/caveats/
    guardrails, the stabilization chart — effect + CI vs `elapsed_days`, zero
    line, horizon marker, wheel-zoom/drag-pan/hover — four one-axis small
    multiples (variant means incl. CUPED covariate, pair MDE vs `min_effect`,
    p-value vs α, client-derived avg group size), a results/audit table, the
    red SRM gate chip, the calibration empty state "uncalibrated — run
    `abk validate` (M4)", and the sub-day look counter). Built by
    `web/build.mjs` (esbuild, IIFE, es2019) into the committed, wheel-packaged
    `abkit/reporting/assets/report.js`.
  - Peeking honesty rendered per data-contract §4 with **stable
    machine-checkable markers**: pre-horizon fixed CIs dashed/de-emphasized
    (`abk-prehorizon`), `insufficient_data` cutoffs greyed with counts+SRM
    only (`abk-insufficient`), the SRM chip (`abk-srm-fail`); asserted by the
    build script, the Python suite, the jsdom smoke suite, and a new CI
    `bundle` job that rebuilds `web/` and diffs the committed assets
    (freshness gate).
  - `abk run --report` (D8, the donor's tri-state flag): bare →
    `reports/<experiment>.html`, a directory → `<dir>/<experiment>.html`, a
    `.html` value → that exact file. Emitted per experiment after its
    pipeline **best-effort** — a report failure yellow-skips and never fails
    the run (the one recorded exception to the CLI exit-non-zero contract) —
    and even with zero pending cutoffs (the re-run-to-report path).
    `--report` with `--steps validate` is rejected; one `.html` file with
    multiple selected experiments is rejected. cli-and-dx §1's never-wired
    `readout` `--steps` token is amended away (D8).
  - Payload series points gain per-arm keys `v1/v2/sd1/sd2/cv1/cv2` (stored
    value/std/CUPED covariate means) — **additive, no schema v-bump** —
    feeding the §5.2 variant-means/lift view; §5.3 amended, `payload.ts`
    lockstep.

### Fixed
- **MDE solve crash + report cost** (M3 WP2 review-closure, adversarial
  re-verification): `abkit.stats.power` — statsmodels' `solve_power` returns a
  shape-`(1,)` ndarray from its `fsolve` fallback for a data-dependent
  few-percent of ordinary `(nobs, ratio)` inputs (e.g. n=139, ratio=1.0);
  under numpy ≥ 2.0 `float(ndarray)` raised, crashing the readout verdict and
  report MDE paths. `_as_scalar` now extracts the value (value-preserving —
  golden tests unchanged, **zero statistical numbers changed**). And the report
  payload's per-point `mde` reads the **stored** `mde_1/2` columns only (null
  when the row did not compute MDE) instead of a read-time statsmodels solve
  per point — the read-time D5(b) fallback stays verdict-level (one solve per
  pair on the latest cutoff). A worst-case sub-day payload dropped from
  ~40–100 s (and a hard crash) to milliseconds; data-contract §5.3 amended.
- **Payload consistency** (M3 WP2 sweep-closure, second review pass):
  - per-point `mde` now honours the D5(b) **both-present guard** — a
    half-present stored pair (one arm's MDE solved to inf and was NULLed by
    enrich) shows null, never the finite arm alone (which would fake adequate
    power and contradict the verdict on the same cutoff; review finding).
  - `srm.observed` is the **whole-cohort** count even under a pinned-`end`
    replay, so it stays coherent with the whole-run `srm.flag`/`pvalue` the
    driver computes once and broadcasts (the `until=` pin is dropped;
    per-cutoff SRM lands with M5 sequential). §5.3 amended.
- **SRM chip loudness under replay** (M3 WP2 final-gate, third review pass):
  the payload `srm` block is now **window-independent** (current experiment
  health) — `flag`/`pvalue` come from the latest persisted row *overall* via a
  new `readout.srm_summary`, not the latest *charted* row. A pinned or empty
  replay window therefore never silences a failing SRM gate (§6 must-fix) and
  the flag/pvalue stay coherent with the whole-cohort `observed`; the chart and
  verdict remain as-of the window. `readout`'s experiment-level SRM aggregation
  is extracted into `srm_summary` (no behavior change to `evaluate`). §5.3
  amended.

### Added
- **M3 WP2 — the experiment-primary report payload** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md) D6):
  - `abkit.reporting.builder`: `build_report_payload(experiment, tables, ...)`
    — one versioned JSON-serializable payload per experiment from persisted
    `_ab_results` rows, the shared contract for the WP3 readout renderer and
    the WP6/WP7 explore shell: WP1 verdict block, experiment-level SRM block
    (driver-mirrored zero-filled exposure counts), M4-shaped
    `calibration: null`, `look: {n, planned}` from the one-enumeration
    planner grid, terse ms-epoch series points, NaN **and ±inf** → null,
    provenance projection (rendered SQL never enters the payload; one
    `metric_query` per metric), metric descriptions from the metric YAML,
    caller-supplied `generated_at`, inclusive `start`/`end` window pinning
    (historical readout replay), a global point budget with trailing-window
    clipping + a loud payload warning, and the full-key empty-experiment
    contract. Zero statistical numbers changed.
  - `InternalTablesManager`: `results_table_exists()` /
    `exposures_table_exists()` — the never-run-project guards for read-only
    surfaces (reporting never creates schema). *(A short-lived `until=` bound
    on `get_exposure_counts` was added here and then removed in the review
    passes below — the SRM block is whole-cohort/window-independent; see the
    Fixed entries.)*
  - Review-driven consistency rules (adversarial review, 4 lenses): rows for
    variant pairs outside the declared arms are excluded from every payload
    surface with a loud warning (never silently mixed into look/period/BH);
    the driver's orphaned-`method_config_id` scan is surfaced as a payload
    warning on the read path too.
  - Specs amended (data-contract-and-reporting.md §5: subsections numbered
    5.1/5.2, the D2 explore data-source rewording, new §5.3 payload contract;
    §2 metric-description sourcing note).
- **M3 WP1 — the readout decision core** (per
  [`docs/specs/m3-implementation-plan.md`](docs/specs/m3-implementation-plan.md) D5):
  - `abkit.pipeline.readout`: pure read-time WIN/LOSE/FLAT/INCONCLUSIVE
    verdicts over persisted `_ab_results` rows — SRM hard gate; pre-horizon
    withholding (extends to FLAT); elapsed-time stabilization over the
    trailing `readout.stabilization_days` (default 7, floored at 3
    informative cutoffs); FLAT gated on `min_effect` vs the pair MDE with a
    read-time MDE fallback for t-test/z-test rows (the z-test `nobs` inverted
    from the persisted SE, never the unit count); guardrail regression under
    the owner-ratified `guardrail_policy: block | warn`; read-time
    Benjamini-Hochberg rescoring (pulled forward from the M5 roadmap line —
    compute-time BH rows carry the raw alpha); orphaned/unconfigured row
    filtering with warnings. Verdicts are read-time only, never persisted.
    Zero statistical numbers changed.
  - Experiment config: `readout: {stabilization_days, guardrail_policy}` and
    per-comparison `min_effect` / `desired_direction` (read-time only — never
    part of `method_config_id`); specs amended
    (data-contract-and-reporting.md §1, declarative-config.md §2).
- **M2 — declarative config + DB layer + the recompute pipeline** (per
  [`docs/specs/m2-implementation-plan.md`](docs/specs/m2-implementation-plan.md)):
  - `abkit.core`: duration parser (`N{s,m,h,d,w}`), `TableModel`/`ColumnDefinition`
    (+`max_length` for MySQL key budgets), and `period_planner` — ONE pure grid
    enumeration (scalar + dense-early schedule cadence, experiment-tz midnight
    snapping, DST-safe, horizon always flagged) consumed by BOTH the validator's
    look gates and the planner anti-join; `data_lag: 0` + half-open windows
    reproduce `*_wo_curr_day` exactly.
  - `abkit.database`: generic CH/PG/MySQL managers with the quorum **atomic
    lock** primitive (PG single-statement `INSERT…ON CONFLICT…DO UPDATE…WHERE`;
    MySQL row-alias upsert with the claim verdict latched into a session
    variable; ClickHouse advisory claim with a deterministic read-back
    tie-break) and the greenfield `_ab_*` schema: `_ab_experiments`,
    `_ab_exposures` (persisted cohort), `_ab_unit_state` (replace-not-sum,
    keyed per source-table+column-set+unit+day; twice-run invariant tested),
    `_ab_results` (the BI contract incl. new `warnings`/`diagnostics` JSON
    columns — spec §2 amended), `_ab_aa_runs`, `_ab_tasks`; strictly-monotonic
    distinct `created_at` via `next_version_ts()`.
  - `abkit.config`: pydantic Experiment (primary entity; cadence
    duration-or-schedule union; sub-day gates) / Metric (type + column roles) /
    Method (delegates validation AND `method_config_id` to the stats factory —
    one hashing path; quarantined branches fail at validate time) / Project
    (statistical defaults + `max_looks`/`warn_looks`/`min_units_per_arm`) /
    Profiles (env-interpolated, lazy driver imports); the full
    declarative-config §8 level-2 validation matrix incl. the macro-usage lint
    and the peeking warnings; project-root discovery + the two-level selector.
  - `abkit.loaders`: StrictUndefined Jinja with the authoritative `ab_*`
    built-ins and the **packaged assignment macro** (`ab.exposed_units()` —
    dialect-aware cohort dedup, both window predicates, exposure filter);
    exposure loader (idempotent per experiment; unit-in-two-variants is a hard
    error) and metric loader (one-row-per-unit REJECTED on violation with the
    GROUP BY hint).
  - `abkit.pipeline` + `abkit.compute`: the v1 full-window recompute pipeline —
    lock → catalog → exposures once → SRM gate (blocking-but-non-dropping,
    broadcast to every row) → per-comparison anti-join plan (Python-computed
    watermark) → analyze (declarative `input_kind`/`is_paired` dispatch;
    two-tier Bonferroni; deterministic per-row bootstrap seeds;
    `insufficient_data` demotion) → enrich (the full contract row) → LWW
    persist; worker pool across experiments; backlog + orphaned-series
    warnings.
  - `abk` CLI: `run` (validate/plan/load/compute steps, `--full-refresh
    --from/--to`, the inspectable effective-alphas echo, the red `SRM FAILED`
    gate line), `unlock`, `clean` (method_config_id drift GC + orphaned
    experiments; dry-run default), and `init` — a **runnable example**
    (z-test fraction + CUPED sample metrics, assignment SQL, a deterministic
    ClickHouse seed dataset, Prefect flow example) that round-trips through
    the real config classes and the L2 validator at scaffold time.
  - Tests: 905 (incl. an in-memory SQL-semantics fake backend, a synthetic
    warehouse that aggregates a real event log per rendered window, the
    machine-independent first-run e2e mirroring the seed generation rule, and
    a testcontainers ClickHouse e2e gate that runs where Docker is available).
- **M2 stats-core additions (zero number changes; goldens untouched):**
  `COVARIATE_LOOKBACK_PARAM` on the two CUPED methods (the lookback is
  identity-bearing — a different pre-period is a different covariate series);
  declarative `BaseMethod.input_kind`/`is_paired` capability attributes.

### Changed (M2 recorded deviations — no statistical numbers changed)
- **Jinja precedence flip vs the detectkit donor:** `ab_*` built-ins WIN over
  caller context; a colliding context key raises instead of silently moving
  the analysis window.
- **CLI exit codes:** every `abk` command exits non-zero on failure (the donor
  echoed and returned 0) — the CLI is the Prefect unit of automation.
- **CUPED covariate mechanics (declarative-config §3/§4 amended):** the
  covariate comes from a SECOND render of the same metric SQL over the fixed
  pre-period window with the exposure filter dropped (legacy semantics — the
  covariate is the same metric pre-period); the original `ab.covariate_window()`
  conditional-aggregate sketch is superseded (its own spec example would have
  double-counted the pre-period under plain `sum()`).
- `_ab_results` gains nullable `warnings`/`diagnostics` canonical-JSON columns
  (plan R7) — the stats core's human-readable failure signal is persisted, not
  lost to stderr; data-contract-and-reporting.md §2 amended in the same change.

- **M1 — the pure statistical core `abkit.stats`** (importable standalone;
  numpy/scipy/statsmodels only). Data model: `Sample` / `Fraction` /
  `RatioSample`, sufficient statistics with the exact legacy **mixed-ddof**
  convention (`np.var`→ddof=0, `np.cov`→ddof=1), `JointMoments`,
  `PairedSufficientStats`, Welford/Chan-stable merges (`accumulate`). Plugin
  method registry + factory + canonical `method_config_id`
  (sha256 over registry name + sorted non-default identity params, version
  appended only when >1; byte-exact-tested; `seed` identity-excluded).
  Closed-form methods (`t-test`, `paired-t-test`, `z-test`, `cuped-t-test`,
  `paired-cuped-t-test`, the new `ratio-delta`) with dual entry
  (`from_samples` ≡ `from_suffstats`); bootstrap family (`bootstrap`,
  `paired-bootstrap`, `poisson-bootstrap`, `paired-poisson-bootstrap`,
  `post-normed-bootstrap`, `paired-post-normed-bootstrap`) on a vectorised
  block-streaming engine with deterministic per-seed draws. Power/MDE
  (t-test, CUPED-deflated, proportions), Bonferroni (incl. the legacy
  two-tier scheme) + read-time Benjamini-Hochberg, SRM chi-square gate,
  deterministic seed derivation (`rng.derive_seed`).
- **Tests (760+):** golden tests vs an independent transcription of the legacy
  engine at rel-1e-9 (incl. the CUPED θ golden and a heavy-tailed sparse-revenue
  fixture), byte-exact identity-hash tests, bootstrap byte-stability /
  block-invariance tests, quarantine and known-answer tests
  (`ratio-delta` ≡ `t-test` at denominator ≡ 1), A/A calibration smoke.

### Changed
- Engine-hygiene fixes H1–H10 applied per
  [`statistics-changes.md` §7](docs/specs/statistics-changes.md) (M1
  implementation record): Generator-based RNG + deterministic per-row seeds,
  baseline-faithful sign p-value default with the H4 plug-in as opt-in
  `pvalue_kind: plugin`, Hamilton stratum apportionment (quorum-mandated),
  Poisson mean-only guard, H5 zero-denominator NaN+warning policy, H9
  point-estimate effect convention, named-stat registry (`register_stat`)
  replacing raw `stat_func` callables; broken legacy ratio methods quarantined
  (never silently substituted).
- Adversarial post-M1 review (8 finder angles → 30 verified findings) applied:
  registry alias-shadowing guard + reload-safe re-registration; param range /
  finiteness validation at construction (`power`, `n_samples`,
  `max_block_bytes`); `weight_method` removed from Poisson schemas and rejected
  without `stratify` (a no-op value could fork `method_config_id`);
  two-tier Bonferroni supports main-metric-only experiments; paired methods
  drive through the uniform `compare()` (a sequence of `PairedSufficientStats`
  is a list of ready comparisons); bootstrap memory cap accounts for index
  matrices + fancy-indexing temporaries; Poisson engine reuses one float64
  weight buffer; stratified planning is a single `np.unique` pass; power/MDE
  effect-size solves are LRU-cached; `TestResult.to_dict` derives from dataclass
  fields; purity of `abkit.stats` enforced by test.
- **Project initiation contract.** Architecture synthesized from the legacy
  `ab_testing` engine (statistical baseline) and detectkit (architecture / DX),
  validated by a 5-lens adversarial subagent quorum (all approve-with-changes).
  See the master plan [`docs/ru/project-initiation-spec.md`](docs/ru/project-initiation-spec.md)
  and the [specs index](docs/specs/00-overview.md): architecture, statistics
  baseline + changes + legacy method catalogue, cumulative-intervals/compute
  strategy, declarative config, data contract & reporting, A/A false-positive
  matrix, CLI & DX, branding & site, and the quorum must-fix gate.
- **Development scaffolding** (this session): packaging (`pyproject.toml`,
  `setup.py`, `MANIFEST.in`, `requirements.txt`), `pre-commit`, GitHub workflows
  (CI, publish-to-PyPI on tags, website), a minimal importable `abkit` package with
  a working `abk` CLI entry point (`abk --version`), and smoke tests.

### Decisions
- **Sub-day cumulative intervals (abk-intervals, 2026-07).** `cadence` is a true
  duration with schedule support (dense-early grids first-class); NO hard time
  floor — the hard gate is `max_looks` (look count is the dangerous variable,
  not the time unit); `data_lag` completeness watermark required below `1d`;
  window contract keyed on exclusive UTC `end_ts` with derived `end_date`
  (daily parity byte-clean); fixed-horizon sub-day = monitoring mode (readout
  still refuses pre-horizon WIN/LOSE), `sequential: always_valid` is the
  sanctioned early-decision path; early rows demoted via `insufficient_data`,
  never hidden; anytime-valid sequential SRM below `1d`; A/A peeking-FPR runs
  the actual cadence grid + gains an exaggeration-at-stop column; unit-state
  stays day-grained (sub-day reads = closed-day state + current-day tail).
  Full record: `docs/specs/cumulative-intervals.md` §6.
- **CUPED covariate window resolved to fixed lookback** (whole days, cadence-
  independent) — the legacy growing window is incoherent at sub-day grain.
  Record: `docs/specs/statistics-changes.md` §5.

### Locked decisions
- Greenfield storage (legacy dashboard is reference only); statistical math
  preserved as a baseline then improved deliberately.
- Fixed-horizon CI by default with honest cumulative-peeking FPR in `abk validate`;
  sequential (always-valid) CIs opt-in.
- ClickHouse-first; PostgreSQL/MySQL supported. Read-only exposures.

_This section was authored pre-release and is cut into the `[0.1.0]` heading above
— the first tagged PyPI release (M1–M6). Roadmap: [`ROADMAP.md`](ROADMAP.md)._
