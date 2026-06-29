# CLI & developer experience

> The goal is detectkit-grade DX: a dbt-like CLI, AI-native onboarding
> (`abkit init-claude`), single-source developer docs, a chart-first local cockpit
> (the **priority** interface), and orchestration via **Prefect**. The CLI is the
> unit of automation; the cockpit is the unit of exploration.

## 1. CLI commands (`abkit`)

Ported from detectkit's lazy-import Click group (shared flag vocabulary:
`--select/--exclude`, `--from/--to`, `--profile`, `--report`, `--force`).

| Command | Purpose |
|---|---|
| `abkit init <name> [--db-type clickhouse\|postgres\|mysql]` | Scaffold `ab_kit_project.yml`, `profiles.yml` (env-var secrets), `experiments/`, `metrics/`, `sql/`, a **runnable example** + a **Prefect flow/deployment** example, README |
| `abkit init-claude [--target-dir DIR]` | Install AI-assistant context: managed `CLAUDE.md` block + `.claude/rules/ab-analysis-kit/` + `.claude/skills/`; idempotent, version-stamped, re-runnable after upgrade |
| `abkit run --select <exp> [--steps validate,plan,load,compute,readout] [--from/--to] [--full-refresh] [--profile] [--report]` | The pipeline: validate → plan → maintain unit-state → load → SRM → compute → persist → optional HTML readout. Streams `VALIDATE → PLAN → STATE → LOAD → SRM → COMPUTE → RESULT` |
| `abkit explore --select <exp> [--metric <m>] [--no-serve] [--no-open]` | **PRIORITY:** the localhost cockpit — live `method_params` tuning + the stabilization chart + always-visible A/A calibration + write-back |
| `abkit validate --select <exp> [--method <m>] [--metric <m>] [--iterations N] [--inject-effect <pct>] [--scoring fpr\|power\|mde] [--report]` | The A/A false-positive + power matrix (incl. honest peeking FPR) → `_ab_aa_runs` + recommendation |
| `abkit plan --select <exp> [--metric <m>] [--mde <pct>] [--power 0.8] [--alpha 0.05]` | Pre-launch power / sample-size / runtime planner (no detectkit analog) |
| `abkit clean --select <exp> \| --orphaned-experiments [--execute] [--yes]` | Config-hash drift GC (prune `_ab_results` rows whose `method_config_id` the YAML no longer produces; purge removed experiments). Dry-run by default |
| `abkit unlock --select <exp> [--profile]` | Clear stale run locks (verbatim from detectkit) |
| `abkit test-report <exp> [--profile]` | Send a mock readout through configured channels (connectivity/format check) |
| `abkit verify-incremental --select <exp>` | (v2) reconcile the incremental backend vs recompute to tolerance across the whole series; gates `compute.mode=incremental` |

### Selector & two-level naming (must-fix: disambiguate, don't overload)

`--select` resolves **experiments**; `--metric` selects a library metric;
`validate`'s method grid uses a distinct **`--method`** (not polysemous
`--select`). Every uniqueness/selection error **names the namespace** and offers the
fix ("experiment name X collides with experiments/Y.yml" vs "metric name X collides
with metrics/Z.yml — metric and experiment names share one namespace"). The
two-level selector semantics are documented in `cli.md` because detectkit users will
assume the one-level model.

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

- `abkit init` scaffolds a **runnable Prefect flow + deployment** (`runners/` /
  `deployments/`) that calls `abkit run --select tag:actual` on a cadence — so an
  analyst schedules experiments to recompute daily with no human in the loop.
- The **CLI is the unit of automation**: a Prefect task = an `abkit` invocation.
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
- **Skills** (routed by experiment lifecycle phase): `abkit-setup-project`,
  `abkit-new-experiment`, `abkit-new-metric`, `abkit-explore` (the hands-on
  umbrella), `abkit-validate`, `abkit-plan`, `abkit-feedback`.
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

The A/B empty path is longer than detectkit's, so `abkit init` ships a **fully
working example** — `example_signup_test.yml` + a real `assignment.sql` + a real
metric SQL against a documented **synthetic/seed dataset** — so `abkit init && abkit
run --select example_signup_test` produces a real result (and an HTML report) on a
fresh machine, not a placeholder-table error. The scaffolded metric carries an
annotated comment block explaining pinned-start / moving-end Jinja semantics and the
one-row-per-unit contract.

## 7. Contributor docs

`CLAUDE.md` (repo root) is the contributor/AI guide (mirrors detectkit's): it points
at `.claude/rules/architecture.md` and `.claude/rules/contributing.md` as the
single source for dev context, lists the test/lint/release commands, and states the
invariants (keep `abkit.stats` pure; methods are plugins; the DB manager stays
generic; keep the renderer framework-free; keep `init-claude` assets in sync on
release). See [ROADMAP.md](../../ROADMAP.md) for milestones.
