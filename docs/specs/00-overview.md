# ab-analysis-kit — specs index

`ab-analysis-kit` (pip `ab-analysis-kit`, CLI `abkit`, docs `abkit.pipelab.dev`) is
an open-source, **declarative** (dbt / detectkit-style: YAML + SQL), **db-agnostic**
(ClickHouse-first; PostgreSQL / MySQL supported), **numpy-first** Python library
for **A/B experiment analysis**. It is the sibling of
[detectkit](https://dtk.pipelab.dev) — the same design DNA (CLI-first, AI-native
onboarding, self-contained reports, a chart-first tuning cockpit) with the
`detect` stage replaced by a **statistical `compute` stage** and the primary
entity flipped from *metric* to *experiment*.

This folder is the **development contract**: the project is "ready to build" when
these specs are agreed.

| Spec | What it pins down |
|---|---|
| [architecture.md](architecture.md) | Module map, pipeline, the 3 evaluated approaches + the chosen synthesis, key decisions, quorum verdict |
| [statistics-baseline.md](statistics-baseline.md) | The legacy math captured verbatim as the reference baseline (frozen) |
| [statistics-changes.md](statistics-changes.md) | Deliberate deviations & additions vs the baseline; the blind-rederivation plan |
| [cumulative-intervals.md](cumulative-intervals.md) | The cumulative expanding-window contract; the incremental-vs-recompute investigation & decision |
| [declarative-config.md](declarative-config.md) | The YAML+SQL model (experiment / metric / method), Jinja built-ins, validation, `method_config_id` |
| [data-contract-and-reporting.md](data-contract-and-reporting.md) | The clean **greenfield** results contract, the decision logic, reporting & explore |
| [aa-false-positive-matrix.md](aa-false-positive-matrix.md) | `abkit validate` — empirical FPR/power (incl. honest peeking FPR) and its UX |
| [cli-and-dx.md](cli-and-dx.md) | The `abkit` CLI, the explore cockpit, `init-claude`, developer docs |
| [quorum-review.md](quorum-review.md) | The adversarial-review must-fix gate (blocking before/during development) |
| [../reference/legacy-method-catalogue.md](../reference/legacy-method-catalogue.md) | Full per-method extraction of the legacy engine (reference) |

The master, plain-language synthesis (in Russian) lives at
[../ru/project-initiation-spec.md](../ru/project-initiation-spec.md) — start there.

## Founding decisions (locked this session)

1. **Greenfield storage.** The legacy `marts.exp_comparison_results` / витрины /
   storage internals are **not** carried over (they are explicitly bad). The
   legacy Grafana dashboard is a **reference only** — to understand what users
   saw and how they decide. We design a clean native data contract from scratch.
2. **The math is preserved as a baseline, then improved.** The legacy statistical
   algorithms are valuable ("выстраданное"); we capture them verbatim
   ([statistics-baseline.md](statistics-baseline.md)), golden-test the new engine
   against the legacy *engine* (not its table), then **deliberately** improve them
   with a documented, A/A-validated process — never a silent number change.
3. **Fixed-horizon by default, honest about peeking.** The daily cumulative chart
   inherently peeks; the default keeps the legacy fixed-horizon CI, the readout
   refuses a pre-horizon WIN/LOSE, `abkit validate` measures the **real**
   cumulative-peeking FPR, and always-valid (sequential) CIs are one toggle away.
4. **ClickHouse-first**, PostgreSQL/MySQL correct & supported (same contract,
   recompute path).
5. **Read-only exposures.** abkit reads an assignment/exposure source (analysis
   only); owned randomization is a possible later addition.

These are expanded in [architecture.md](architecture.md) and the RU initiation spec.
