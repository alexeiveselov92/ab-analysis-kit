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

> **Status: pre-development.** This repo currently holds the **project-initiation
> specs** (the development contract). Start with the master plan (RU):
> [docs/ru/project-initiation-spec.md](docs/ru/project-initiation-spec.md), then the
> [specs index](docs/specs/00-overview.md).

## What it will do

- **Declarative experiments** — `experiments/*.yml` (assignment + variants +
  comparisons) referencing a reusable `metrics/*.yml` library (YAML + SQL).
- **A rigorous statistical engine** — t-test, two-proportion z-test, CUPED, ratio
  (delta-method), and a vectorised bootstrap family (plain/paired/Poisson/
  post-normed), with relative & absolute effects, MDE/power, and multiple-testing
  correction. Ported from a battle-tested legacy engine and improved deliberately.
- **The cumulative stabilization chart** — effect + CI per day from experiment
  start, so you see the estimate converge and call a winner only once it stabilizes.
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

- **Master plan (RU):** [docs/ru/project-initiation-spec.md](docs/ru/project-initiation-spec.md)
- **Specs index:** [docs/specs/00-overview.md](docs/specs/00-overview.md)
- **Architecture:** [docs/specs/architecture.md](docs/specs/architecture.md)
- **Roadmap:** [ROADMAP.md](ROADMAP.md) · **Principles:** [PRINCIPLES.md](PRINCIPLES.md)
- **Contributor guide:** [CLAUDE.md](CLAUDE.md)

## License

MIT (planned).
