# ab-analysis-kit

**A/B experiment analysis as declarative YAML + SQL — with a chart-first cockpit.**

`ab-analysis-kit` (CLI `abk`) is an open-source, declarative
(dbt / [detectkit](https://dtk.pipelab.dev)-style), database-agnostic, numpy-first
Python library for analyzing A/B experiments. You define an **experiment** and its
**metrics** in YAML + SQL; abkit computes per-method effect + confidence interval +
p-value + MDE/power **cumulatively over the experiment's lifetime** (the
stabilization chart), writes them to a clean warehouse table any BI can read, and
gives you a local cockpit to tune the analysis and a harness to prove your method is
actually calibrated.

> **Status: `0.8.0` (Alpha) — the latest on PyPI**
> (milestones **M1–M13** shipped — M13 is the **versioned statistics**
> milestone: five opt-in options that each buy power or fix an interval, with
> **no default moved** — a project that changes nothing reproduces `0.7.0` row
> for row. `correction: holm` is uniformly more powerful than one-step
> Bonferroni at the same family error rate; `contrasts: vs_control` declares the
> family you actually decide on, so the divisor is `g−1` instead of `C(g,2)`
> (≈ +10 points of power at four arms) and the treatment-vs-treatment pairs stop
> being computed; `guardrail_correction: none` takes a guardrail out of the
> screening budget entirely; `interval: score` gives proportions an interval
> that is the inversion of the z-test printed beside it (no more "significant"
> next to a CI covering zero, and a real answer on empty cells); and
> `interval: fieller` does the same for the relative lift of the five
> mean-based methods — the delta interval it replaces has nominal *two-sided*
> coverage with lopsided tails (1.7% / 3.3% against 2.5% each), and every abkit
> verdict is a one-sided claim. `abk validate` gained the one column that can
> tell those two apart: the share of false positives falling **below** zero.
> M12 wired **notifications**: `abk run
> --notify` and `abk validate --notify` push what a run just decided to nine
> channel types (Slack, Telegram, email, webhook, Mattermost, Discord, Teams,
> Google Chat, ntfy), as six routable signals — the readout verdict, a verdict
> flip, a failed sample-ratio gate, a pipeline error, a slipped schedule, and an
> A/A cell that broke its false-positive budget. Nothing is recomputed for a
> message, so it cannot disagree with the report; a repeat run over unchanged
> data is silent; and no channel failure can change an exit code. M11 added
> **`abk dashboard`**, the
> project-level cockpit: one row per experiment with its headline verdict,
> effect + CI, p/α and a sparkline of the cumulative series, plus buttons that
> spawn real `abk` subprocesses (Run — for the whole experiment or one metric —
> Unlock, Clean, Explore, Open report) and stream their logs. It is a
> **launcher**: it computes no statistic and never takes the pipeline lock, so
> every verdict on the page is the readout's own. The `0.6.x` interstitial then
> closed both `abk plan` sizing gaps: a CUPED comparison is sized on the covariate
> correlation its own results row already persists — required-N is `(1 − ρ²)×` the
> old raw-variance bound (`0.6.1`) — and `--from-history <N d>` gives an experiment
> that has **never run** a baseline from the days before its start, instead of
> `SKIPPED: no baseline` (`0.6.2`). A second `0.6.x` interstitial then gave the
> dashboard **CRUD YAML editing** — edit, create, delete an experiment from the
> cockpit, validated at both levels and archived byte-verbatim before every write
> — added `abk ui` as its alias, and made M9's additive read path **discoverable**:
> `abk run` no longer stays quiet about an undecided `compute.incremental_reads`,
> `--cost-report` prints the counterfactual, and `abk init` scaffolds it on
> (`0.6.4`)). The statistical core, the
> declarative config / DB / pipeline layer, the explore cockpit +
> self-contained reports, `abk validate` (numpy-vectorized — minutes → sub-seconds),
> opt-in sequential analysis + `abk plan`, and the DX layer (`abk init-claude`, docs site,
> Prefect scaffolding) are all shipped. Docs: [abkit.pipelab.dev](https://abkit.pipelab.dev).

## Install

```bash
pip install ab-analysis-kit          # Python 3.10+; add a DB extra for real data:
pip install "ab-analysis-kit[clickhouse]"   # or [postgres] / [mysql] / [all-db]
```

(`pip install ab-analysis-kit` gets `0.8.0` — the opt-in M13 statistics (Holm,
the declared contrast set, the score and Fieller intervals, the A/A sign
column), opt-in notifications across nine channels, `abk dashboard` with its
YAML editor, `abk ui`, CUPED-aware `abk plan` sizing, `abk plan --from-history`
and the discoverable additive read path all included.)

`abk --version` and `abk --help` work with no database driver; you can even lint a
config (`abk run --steps validate`) with no database at all. See the
[getting-started guide](https://abkit.pipelab.dev) for the full first run.

## What it does

- **Declarative experiments** — `experiments/*.yml` (assignment + variants +
  comparisons) referencing a reusable `metrics/*.yml` library (YAML + SQL).
- **A rigorous statistical engine** — t-test, two-proportion z-test, CUPED, ratio
  (delta-method), and a vectorised bootstrap family (plain/paired/Poisson/
  post-normed), with relative & absolute effects, MDE/power, and multiple-testing
  correction. Ported from a battle-tested legacy engine and improved deliberately.
- **The cumulative stabilization chart** — effect + CI per day from experiment
  start, so you see the estimate converge and call a winner only once it stabilizes.
- **`abk dashboard`** — the project-level cockpit: every experiment as one row
  (verdict, effect + CI, p/α, sparkline), with Run / Unlock / Clean / Explore /
  Open-report buttons that spawn real `abk` subprocesses and stream their logs.
  It never computes a statistic itself, so what you read is the readout's own
  verdict.
- **`abk explore`** — a local, chart-first cockpit to turn method knobs (CUPED,
  stratification, alpha…) and watch the result recompute live, with A/A calibration
  always in view. *The priority interface.*
- **`abk validate`** — an A/A false-positive + power matrix that measures your
  method's **real** α (including the honest cumulative-peeking FPR), not the nominal.
- **BI-agnostic** — results land in one clean table; connect Grafana, Lightdash,
  Metabase, or Superset. Orchestrate with **Prefect**.
- **AI-native** — `abk init-claude` sets up assistant context + skills so an
  assistant can scaffold and tune experiments with (or for) you.

## Design at a glance

```
experiment (YAML)  ──▶ load exposures ──▶ SRM gate ──▶ compute (t/z/CUPED/bootstrap) ──▶ readout
  └ references reusable metrics (YAML + SQL)                                          └ _ab_results → your BI
```

abkit is the sibling of detectkit: same DNA (CLI-first, db-agnostic, numpy-first,
self-contained reports, a chart-first cockpit, `init-claude`), with the anomaly
`detect` stage replaced by a statistical `compute` stage and the primary entity
flipped from *metric* to *experiment*.

## Documentation

- **Docs site:** [abkit.pipelab.dev](https://abkit.pipelab.dev) — getting started, guides, reference
- **Roadmap:** [ROADMAP.md](ROADMAP.md) · **Principles:** [PRINCIPLES.md](PRINCIPLES.md)
- **Contributor guide:** [CLAUDE.md](CLAUDE.md) · design contracts in [docs/specs/](docs/specs/)
- **Master plan (RU):** [docs/ru/project-initiation-spec.md](docs/ru/project-initiation-spec.md)

## License

[MIT](LICENSE).
