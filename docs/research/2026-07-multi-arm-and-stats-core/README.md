# Research: multi-arm support + stats-core optimization (2026-07-07)

Two maintainer questions, audited against the shipped code (`main @ 700e749`) by
independent multi-agent sweeps, each finding adversarially cross-checked. Every
claim carries a `file:line` anchor.

| Question | Answer up front | Detail |
|---|---|---|
| Do **>2-arm (multi-group)** experiments work — stats *and* report/explore UX? | **Yes end-to-end at the statistical layer** (no crash, correct all-pairwise math, joint K-way SRM, correct Bonferroni). The gaps are **UX/decision-layer**, not correctness — one is a near-decision bug. | [multi-arm-support.md](multi-arm-support.md) |
| Is the **stats core** optimal (deps, efficiency, maintainability)? | **Already strong.** Minimal deps (numpy+scipy+statsmodels), genuinely vectorized bootstrap, distribution math delegated to scipy. Real upside = one big **byte-identical latency win** + a cluster of **honest, versioned statistical upgrades**. | [stats-core-review.md](stats-core-review.md) |

## The framing that shaped this (maintainer)

- The legacy engine was a **risk-reduction anchor** — "it definitely worked, but is
  not necessarily perfectly correct everywhere." It locks **logic + numeric results**
  (golden rel-1e-9), **not the implementation** and **not correctness forever**.
- **Efficiency / optimization is desirable**, wherever it keeps numbers byte-identical.
  Numeric changes are legitimate too — they just require an `ALGORITHM_VERSION` bump +
  `statistics-changes.md` entry + A/A revalidation (never a silent change).
- "More options → better; my personal numpy experience is not the gold standard" — so
  the review deliberately questions legacy choices and looks *beyond* the current impl.

## Roadmap split (see [ROADMAP.md](../../../ROADMAP.md) → "Post-baseline hardening")

Bias per the maintainer's steer — **ship the MVP fast, improve in 1.x**:

- **Now / 0.1.0 (MVP):** document the known multi-arm limitations honestly; fix the one
  near-decision UX bug (explore Review shows only the first arm's verdict). No stats
  numbers move.
- **0.1.x safe wins (byte-identical, no version bump):** the `ndtri/ndtr` hot-path
  speedup (~60×), lazy `statsmodels` import + lazy `effect_distribution` (~250× on the
  validate/explore hot path), plus polish (test parametrization, parametric `_finalize`
  helper, dedup). Multi-arm: B-vs-C verdict card, CLI per-pair labels.
- **1.x (versioned statistical improvements — ALGORITHM_VERSION + A/A):** Holm over
  Bonferroni, unpooled/Wilson proportion CIs, the relative-z covariance term, uniform
  ddof, Welch-t small-n, BCa bootstrap, cross-fitted CUPED, the main-tier
  `metrics_count=1` FWER fix; multi-arm experiment-level winner rollup +
  treatment-vs-treatment verdicts + a `control:` field.
- **v2 / bets:** incremental Chan-merge recompute (the real warehouse-cost lever),
  drop-`statsmodels` reimplementation, SFC64 bit generator.
