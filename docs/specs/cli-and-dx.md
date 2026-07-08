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
| `abk plan --select <exp> [--metric <m>] [--mde <pct>] [--power 0.8] [--alpha 0.05] [--baseline <metric>:mean=..,std=..,n=..]` | Pre-launch power / sample-size planner (no detectkit analog). **Read-only** (no lock, no `_ab_*` writes). Reports required-N / achievable-MDE / achieved-power at the effective two-tier alpha + the projected look count & cost shape + **runtime** (days-to-N from an arrival rate) and **ASN** (average sample number for a sequential design); refuses ratio/bootstrap methods it cannot size honestly. (Runtime/ASN shipped in M6 WP-A — see amendment below.) |
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

### `abk plan` scope amendment (M5 WP6; m5-implementation-plan.md D10 — runtime/ASN shipped M6 WP-A)

The `abk plan` row's word **"runtime"** — days-to-N from a unit-arrival rate, and the
sequential design's expected/average sample number (ASN) — **shipped in M6 WP-A** (see
the runtime/ASN sub-section below). M5 shipped the **sizing** planner only: required-N /
achievable-MDE / achieved-power (at the effective two-tier
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

#### Runtime + ASN (M6 WP-A)

Given a **unit-arrival rate** — derived read-only from `_ab_exposures` (distinct units
per observed day, whole-cohort window, split to the control arm) or supplied with
`--arrival-rate <units/day>` (total across arms) — each sizable comparison also reports:

- **runtime** — `days-to-required-N = required_n / rate` plus the planned horizon length,
  a plain division; and
- **ASN** — for a `sequential.enabled`, sequential-eligible comparison, the always-valid
  design's **average sample number**: the expected control-arm N at which the confidence
  sequence first excludes zero, under the true target effect (H1) and the null (H0). It
  is a deterministic (fixed-seed) Monte-Carlo estimate over the canonical information-time
  Gaussian process of the per-look estimate, crossing the **exact shipped CS boundary**
  (`abkit.stats.sequential`), capped at the planned horizon.

Without an arrival rate BOTH are **SKIPPED with a reason** (a backfilled cohort whose
exposures span ~one instant is underivable) — never invented. A fixed-horizon or
resampling design reports `sequential ASN: n/a` with the reason.

> **Honest ASN framing (WP-A).** Keep two quantities distinct.
> **(1) The always-valid design's *sample requirement*** — the N needed to reach a given
> power — is **larger** than the fixed required-N, because the Robbins mixture CI is
> deliberately wider than a fixed CI (≈3.0·SE vs 1.96·SE at the anchor). So the CS never
> lets you *design* for fewer units than a fixed test at the same power; that width is the
> price of unlimited peeking.
> **(2) The reported ASN is a *different* thing** — the *expected stopping* N, capped at
> the planned horizon. Its guarantee is stated **strictly against the horizon**: under a
> true effect the sequence usually stops well before the horizon (ASN_H1 ≪ horizon-N when
> well-powered), while under the null it runs essentially to the horizon (ASN_H0 ≈
> horizon-N); ASN shrinks as the true effect grows, floored by the first look's N (a coarse
> cadence forfeits early-stop savings — itself a planning signal).
> ASN vs the fixed required-N is therefore **regime-dependent**: it *exceeds* required-N
> when the horizon comfortably clears the design's power need, but can dip *below* required-N
> in the underpowered / horizon-capped regime (early crossers pulling the capped mean down,
> non-crossers stopping at a horizon ≈ required-N). It is **never** a "sequential concludes
> in fewer samples than the fixed test" claim — the `abk plan` ASN line flags the
> below-required case as a horizon-capped expected-stop so the juxtaposition can't be
> misread. The shipped tests assert only the horizon-framed invariants (ASN_H1 ≪ horizon-N,
> ASN_H0 ≈ horizon-N, ASN monotone in effect), never an ASN-vs-required-N ordering.

```
  │   example_signup_cr [main · z-test · relative] — baseline prop=0.2 · n=10000/10000 trials
  │     target MDE 5.00% → required 1,568/arm ✓ powered · power@MDE 1.00 · achievable MDE 1.98%
  │     runtime ≈ 0.8d to required-N @ 2,000 units/day/arm (_ab_exposures over 30.0 observed days) · horizon 14.0d
  │     sequential ASN ≈ 2,400/arm (≈ 1.2d) at target effect · P(win by horizon) 100% · null ASN ≈ 27,800/arm
```

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
  unattended runs; failures surface via the CLI's non-zero exit (the orchestrator's
  own alerting then fires). Automatic project-level error *notification* on a failed
  run is a post-M6 item; the shipped connectivity/format smoke is `abk test-report`,
  which sends a mock readout through the configured channels — not an on-failure
  alerter.
- The same flow works under cron or any orchestrator; Prefect is the documented
  first-class path.

## 4. BI integration (connect your own)

`_ab_results` is a stable, BI-friendly warehouse table; teams connect **Grafana /
Lightdash / Metabase / Superset** to it. We ship **tool-agnostic reference SQL** you
paste into any of them (`docs/examples/bi/queries.sql` — headline scoreboard, the
effect+CI stabilization chart, significance-vs-effective-alpha, MDE/power, cross-
experiment board, freshness, config-drift), the optional SRM panel
(`srm_panel.sql`), and **one importable Grafana dashboard** (`grafana_dashboard.json`,
ClickHouse) that wires the core recipes together — the portable SQL recipes are the
first-class deliverable, not a per-tool importable file for each of the four. abkit
owns the numbers, not the dashboard. ([data-contract-and-reporting.md §3](data-contract-and-reporting.md))

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

**One story, three authored bodies (as built).** Domain truth is not machine
cross-generated from a single Markdown source. Three bodies are authored **separately,
each for its audience**, and kept telling **one** story by **human review** — not by
lockstep generation (a CI drift gate to enforce it lands in WP9):

1. the **packaged assistant assets** `abkit/cli/assets/claude/` (`CLAUDE.section.md` +
   `rules/*.md` + `skills/*`), installed into a user's project by `init-claude`,
   version-stamped so an upgrade refreshes the in-repo copy;
2. the **user-facing `docs/` tree** (guides + reference), published to the site at
   `abkit.pipelab.dev`; and
3. the **contributor truth** — `.claude/rules/` (this repo's `architecture.md` +
   `contributing.md`) plus `docs/specs/`, which additionally holds the
   migration/quality contract (baseline, changes, A/A matrix, data contract, this spec)
   so the whole project is auditable.

The **only mechanical sync** is `docs/` → the published site: the website mirrors
detectkit's Astro `website/` + `sync-docs.mjs`, which copies the `docs/` pages into the
build. The assistant assets vs the `docs/` prose are deliberately written for different
readers (terse rule vs. narrative guide), so they are reconciled by **human review**
today — the packaged index is kept honest by `tests/cli/test_init_claude.py` (every
rule/skill is routed from `CLAUDE.section.md`; the shipped tree matches the declared
set). A broader cross-body drift gate (`test_docs_single_source.py`) lands in WP9.

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
