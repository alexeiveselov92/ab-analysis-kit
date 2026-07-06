# CLI & developer experience

> The goal is detectkit-grade DX: a dbt-like CLI, AI-native onboarding
> (`abk init-claude`), single-source developer docs, a chart-first local cockpit
> (the **priority** interface), and orchestration via **Prefect**. The CLI is the
> unit of automation; the cockpit is the unit of exploration.

## 1. CLI commands (`abk`)

> Naming: pip package `ab-analysis-kit`; Python import package `abkit`; **terminal
> command `abk`** (short, like detectkit's `dtk`); assistant skills `abk-*`.

Ported from detectkit's lazy-import Click group (shared flag vocabulary:
`--select/--exclude`, `--from/--to`, `--profile`, `--report`, `--force`).

| Command | Purpose |
|---|---|
| `abk init <name> [--db-type clickhouse\|postgres\|mysql]` | Scaffold `abkit_project.yml`, `profiles.yml` (env-var secrets), `experiments/`, `metrics/`, `sql/`, a **runnable example** + a **Prefect flow/deployment** example, README |
| `abk init-claude [--target-dir DIR]` | Install AI-assistant context: managed `CLAUDE.md` block + `.claude/rules/ab-analysis-kit/` + `.claude/skills/`; idempotent, version-stamped, re-runnable after upgrade |
| `abk run --select <exp> [--steps validate,plan,load,compute] [--from/--to] [--full-refresh] [--profile] [--report]` | The pipeline: validate → plan → maintain unit-state → load → SRM → compute → persist → optional HTML readout. Streams `VALIDATE → PLAN → STATE → LOAD → SRM → COMPUTE → RESULT`. *(The readout surface is `--report` — tri-state: bare → `reports/<exp>.html`, a directory → `<dir>/<exp>.html`, a `.html` path → that file; emitted best-effort per experiment after its pipeline, even with zero pending cutoffs. A former `readout` `--steps` token was never wired and is superseded by `--report` — m3-implementation-plan.md D8.)* |
| `abk explore --select <exp> [--metric <m>] [--no-serve] [--no-open]` | **PRIORITY:** the localhost cockpit — live `method_params` tuning + the stabilization chart + always-visible A/A calibration + write-back |
| `abk validate --select <exp> [--method <m>] [--metric <m>] [--iterations N] [--inject-effect <pct>] [--scoring fpr\|power\|mde] [--report] [--force]` | The A/A false-positive + power matrix (incl. honest peeking FPR) → `_ab_aa_runs` + recommendation. Streams `LOAD → RESAMPLE → SCORE → PERSIST` — a **distinct** stage vocabulary from `abk run`'s config-lint `VALIDATE` step (`--steps validate`): the two never share copy (the word "validate" is deliberately not reused between the config gate and the A/A matrix). Its own out-of-band lock (`process_type='validate'`, D5), cleared by `abk unlock`; exits non-zero on any cell/harness failure; `--report` is best-effort. `--method` (not `--select`) is the method-grid axis (§below). |
| `abk plan --select <exp> [--metric <m>] [--mde <pct>] [--power 0.8] [--alpha 0.05] [--baseline <metric>:mean=..,std=..,n=..]` | Pre-launch power / sample-size planner (no detectkit analog). **Read-only** (no lock, no `_ab_*` writes). Reports required-N / achievable-MDE / achieved-power at the effective two-tier alpha + the projected look count & cost shape; refuses ratio/bootstrap methods it cannot size honestly. **runtime/ASN → M6** (see amendment below). |
| `abk clean --select <exp> \| --orphaned-experiments [--execute] [--yes]` | Config-hash drift GC (prune `_ab_results` rows whose `method_config_id` the YAML no longer produces; purge removed experiments). Dry-run by default |
| `abk unlock --select <exp> [--profile]` | Clear stale run locks (verbatim from detectkit) |
| `abk test-report <exp> [--profile]` | Send a mock readout through configured channels (connectivity/format check) |
| `abk verify-incremental --select <exp>` | (v2) reconcile the incremental backend vs recompute to tolerance across the whole series; gates `compute.mode=incremental` |

### Selector & two-level naming (must-fix: disambiguate, don't overload)

`--select` resolves **experiments**; `--metric` selects a library metric;
`validate`'s method grid uses a distinct **`--method`** (not polysemous
`--select`). Every uniqueness/selection error **names the namespace** and offers the
fix ("experiment name X collides with experiments/Y.yml" vs "metric name X collides
with metrics/Z.yml — metric and experiment names share one namespace"). The
two-level selector semantics are documented in `cli.md` because detectkit users will
assume the one-level model.

### `abk plan` scope amendment (M5 WP6; m5-implementation-plan.md D10)

The `abk plan` row's word **"runtime"** — days-to-N from a unit-arrival rate, and the
sequential design's expected/average sample number (ASN) — is **deferred to M6**: it
needs an arrival-rate source the pipeline does not yet capture. M5 ships the **sizing**
planner only: required-N / achievable-MDE / achieved-power (at the effective two-tier
alpha), the projected look count, and the compute cost shape — all read-only. Baseline
per-arm moments come from the latest persisted `_ab_results` row for the control/first-
treatment pair (a `--baseline <metric>:mean=..,std=..,n=..` / `:prop=..,n=..` override
sizes a greenfield experiment; without either, that comparison is reported as
un-sizable, not guessed). The target MDE defaults to the comparison's `min_effect`. Only
the closed-form power families are sized: **ratio** metrics and **bootstrap/resampling**
methods have no versioned power formula and are refused (SKIPPED, never invented math);
CUPED is sized on the raw persisted variance (the covariate correlation is not persisted
per row) as a flagged conservative upper bound. A by-design refusal exits zero; a genuine
harness failure (bad selection / `--baseline` / warehouse error) exits non-zero.

A transcript over the scaffolded example (after one `abk run` has persisted the moments):

```
$ abk plan --select example_signup_test --mde 0.05
  ┌─ example_signup_test: plan · α raw=0.05 → per-comparison 0.05 · power 0.80
  │   example_signup_cr [main · z-test · relative] — baseline prop=0.2 · n=300/300 trials (persisted @ …)
  │     target MDE 5.00% → required 25,580/arm ✗ underpowered · power@MDE 0.06 · achievable MDE 49.26%
  │   example_arpu [secondary · cuped-t-test · relative] — baseline mean=62.86 std=42 · n=300/300 (persisted @ …)
  │     target MDE 5.00% → required 2,804/arm ✗ underpowered · power@MDE 0.15 · achievable MDE 15.31%
  │     ⚠ sized on RAW variance — CUPED (ρ not persisted) lowers required-N further
  └─ looks: 14 planned · cadence 1d · horizon 2024-07-15 · ~28 _ab_results rows/full-refresh
Done. 1 experiment(s) planned
```

The effective **per-comparison alpha** (two-tier resolved) heads the tree; each line
reports required-N (vs the current N → powered/underpowered), the achievable MDE at the
current size, and achieved power; a ratio/bootstrap comparison reads `SKIPPED: …` instead.

## 2. The explore cockpit (priority interface)

The detectkit-`tune` port — see [data-contract-and-reporting.md §5](data-contract-and-reporting.md).
Key DX commitments (from the quorum):
- **Calibration always visible**, Apply gated when uncalibrated (no silent
  mis-calibration footgun).
- **Basic / Advanced knob disclosure** — default view shows `test_type`, alpha,
  CUPED on/off; the full ~9-knob surface is opt-in (the median user is an analyst,
  not a statistician).
- **Live recompute via the Python `from_suffstats` path** (one source of truth for
  the math; no JS stats fork; no DB round-trip).
- **Orphan detection** — warn at run/explore when an experiment has >1
  `method_config_id` for a metric (the BI chart will show duplicate stabilization
  lines) and offer `clean`.
- **Web-first, framework-free** renderer (baked payload + self-contained JS) so it
  can later embed in a full app.

## 3. Orchestration via Prefect (the automation path)

abkit is orchestration-friendly by design (the legacy system ran on Prefect):

- `abk init` scaffolds a **runnable Prefect flow + deployment** (`runners/` /
  `deployments/`) that calls `abk run --select tag:actual` on a cadence — so an
  analyst schedules experiments to recompute daily with no human in the loop.
- The **CLI is the unit of automation**: a Prefect task = an `abk` invocation.
  Nothing about the pipeline assumes interactivity; locks are self-healing for
  unattended runs; failures surface via `test-report` channels and project-level
  error notification.
- The same flow works under cron or any orchestrator; Prefect is the documented
  first-class path.

## 4. BI integration (connect your own)

`_ab_results` is a stable, BI-friendly warehouse table; teams connect **Grafana /
Lightdash / Metabase / Superset** to it. We ship reference queries + example
dashboards per tool in `docs/examples/bi/`, plus the optional SRM panel. abkit owns
the numbers, not the dashboard. ([data-contract-and-reporting.md §3](data-contract-and-reporting.md))

## 5. `init-claude` & single-source developer docs

Near-verbatim port of detectkit's `init-claude` (the AI-native onboarding crown
jewel; domain-agnostic mechanism):

- Source of truth is a **packaged asset tree** `abkit/cli/assets/claude/`
  (`CLAUDE.section.md`, `rules/ab-analysis-kit/*.md`, `skills/<skill>/SKILL.md`),
  shipped in the wheel, read via `importlib.resources`.
- Three idempotent, version-stamped writes into the target project: (1) a **managed
  `CLAUDE.md` block** (marker-delimited, DOTALL-regex sub so the user's own content
  is preserved); (2) `.claude/rules/ab-analysis-kit/` reference docs; (3)
  `.claude/skills/`.
- **Skills** (routed by experiment lifecycle phase): `abk-setup-project`,
  `abk-new-experiment`, `abk-new-metric`, `abk-explore` (the hands-on
  umbrella), `abk-validate`, `abk-plan`, `abk-feedback`.
- **`CLAUDE.section.md`** is the thin always-loaded index: what abkit is, a "read
  the matching rule before answering" routing table, the Skills section, and the
  **domain gotchas** — SRM before trusting any effect; peeking on the daily series
  (sequential is opt-in); globally-unique experiment AND metric names; editing
  `method_params` orphans rows (recompute + `clean`); every loader query must be
  one-row-per-unit and join the cohort macro; sum/count metrics are additive
  (incremental) while medians/quantiles are not; `_ab_results` is the BI contract.

**Single-source, two renders.** The `.claude/rules/*.md` (assistant-facing,
installed by `init-claude`) and the published `docs/` tree (the site at
`abkit.pipelab.dev`) are **one** Markdown body of domain truth, authored once,
version-stamped so re-running `init-claude` after an upgrade refreshes the in-repo
copy. `docs/specs/` additionally holds the migration/quality contract
(baseline, changes, A/A matrix, data contract, this spec) so the whole project is
auditable. The website mirrors detectkit's Astro `website/` + `sync-docs.mjs`.

## 6. First-run experience (must-fix)

The A/B empty path is longer than detectkit's, so `abk init` ships a **fully
working example** — `example_signup_test.yml` + a real `assignment.sql` + a real
metric SQL against a documented **synthetic/seed dataset** — so
`abk init && abk run --select example_signup_test` produces a real result (and an
HTML report) on a fresh machine, not a placeholder-table error. The scaffolded metric carries an
annotated comment block explaining pinned-start / moving-end Jinja semantics and the
one-row-per-unit contract.

## 7. Contributor docs

`CLAUDE.md` (repo root) is the contributor/AI guide (mirrors detectkit's): it points
at `.claude/rules/architecture.md` and `.claude/rules/contributing.md` as the
single source for dev context, lists the test/lint/release commands, and states the
invariants (keep `abkit.stats` pure; methods are plugins; the DB manager stays
generic; keep the renderer framework-free; keep `init-claude` assets in sync on
release). See [ROADMAP.md](../../ROADMAP.md) for milestones.
