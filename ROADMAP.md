# Roadmap (technical plan)

Milestones from greenfield to a shippable v1, then v2. Ordered so the **priority
explore cockpit** and a **runnable first-run** arrive early. Each milestone's
definition-of-done includes the relevant
[quorum must-fixes](docs/specs/quorum-review.md).

## M0 — Project scaffolding & contracts ✅ (this session)
- References analyzed; architecture synthesized & quorum-validated.
- Specs written ([docs/specs/](docs/specs/)); statistics baseline + legacy catalogue
  captured; founding decisions locked.
- **Repo infra laid (detectkit-style):** packaging (`pyproject.toml` → `pip install
  ab-analysis-kit`, `abk` entry point; `setup.py`, `MANIFEST.in`, `requirements.txt`),
  `.gitignore`, `.pre-commit-config.yaml`, GitHub workflows (CI, publish-to-PyPI on
  tags, website), `CHANGELOG.md` (Keep a Changelog), a minimal importable `abkit`
  package + `abk --version`, and smoke tests (CI green from day one). Pushed to
  `github.com/alexeiveselov92/ab-analysis-kit` (`main`).
- **Next:** flesh out the package layout from
  [architecture.md §4](docs/specs/architecture.md) starting with M1.

## M1 — Pure statistical core (`abkit.stats`) ✅
- `BaseMethod` ABC + registry + factory; `Sample`/`Fraction`/`SufficientStats`
  (mixed-ddof aware); `effects.py` (delta-method linearisation); `TestResult`.
- Parametric: `ttest`, `paired_ttest`, `ztest`, `cuped_ttest`, `paired_cuped_ttest`,
  `ratio_delta`. Bootstrap: vectorised engine (mean fast-path + Poisson matmul),
  `bootstrap`, `paired_bootstrap`, `poisson_bootstrap`, `post_normed_bootstrap`,
  percentile CI + `(#extreme+1)/(n+1)` p-value (opt-in `pvalue_kind`; the default
  stays the baseline sign p-value per statistics-changes §2). Power/MDE; Bonferroni.
- `rng.py` (`default_rng`, deterministic per-row seeds). Dual entry
  (`from_suffstats` ≡ `from_samples`).
- **DoD (met):** golden tests vs an independent legacy transcription at rel-1e-9
  (incl. θ; see statistics-changes §0 note on transcription provenance);
  known-answer tests; canonical `method_config_id` byte test; quarantine policy
  for broken ratio methods; 8-angle adversarial review applied (30 verified
  findings fixed or recorded). *(Must-fixes: ddof, tolerance, seed policy, hash,
  quarantine — all done.)*
- **Deferred M1 cleanups (tracked, non-blocking):** shared NormalTest→TestResult
  builder for the 5 parametric methods; `_finalize_from_boots` epilogue helper
  for the 4 bootstrap methods (also dedupes the double `stat_point`); route
  `ratio_delta._arm_linearisation` through `effects.relative_delta_effect`;
  `JointMoments.corr(i, j)` accessor replacing `paired_cuped_ttest._corr`;
  declarative introspectable quarantined-branch map (schema-visible, replacing
  imperative `_validate_params` raises); unify warn-vs-record warning channels;
  unify golden-bootstrap tolerance helper with `tests/golden/conftest.py`;
  z-test could route through `effects.normal_test` (kept as a verbatim legacy
  transcription deliberately).

## M2 — Declarative config + DB layer + the pipeline (recompute) ✅
- pydantic Experiment/Metric/Method configs + two-level validator; Jinja templating
  + the **packaged assignment macro**; project/profiles + env interpolation.
- Generic DB manager (CH/PG/MySQL) + internal tables (`_ab_experiments`,
  `_ab_exposures`, `_ab_results`, `_ab_tasks`); `core/period_planner` (expanding
  grid, anti-join, explicit completeness boundary).
- **Sub-day cadence first-class** (decision: cumulative-intervals.md §6):
  duration/schedule-typed `cadence` (dense-early grids), UTC `end_ts` window
  contract with derived `end_date`, `data_lag` watermark planner rule,
  `max_looks`/`warn_looks` gates, `ab_start_ts`/`ab_end_ts` Jinja built-ins,
  `insufficient_data` small-n row flag; CUPED covariate = fixed lookback
  (statistics-changes §5, implemented as the pre-period second render —
  declarative-config §3 amended).
- `pipeline`: discover → plan → load (cohort once) → SRM gate → compute → enrich →
  persist. `abk run`, `abk run --steps validate` (config-lint), `unlock`,
  `clean`, `init` (runnable example + seed dataset). Read-only exposures.
- **DoD (met):** `abk init && abk run --select example_signup_test` produces
  real results against the seed dataset (machine-independent e2e + a
  testcontainers ClickHouse gate); idempotent byte-stable re-run (incl.
  bootstrap via derived seeds); atomic lock (PG/MySQL single-statement, CH
  advisory); strictly-monotonic `created_at`; one-row-per-unit guard.
  *(Must-fixes: macro, alpha inspectability, completeness boundary, lock,
  SRM-in-CLI — all done.)*
- **Deferred (recorded):** the `_ab_unit_state` STATE stage is schema/invariant-
  complete (twice-run test) but not wired into the v1 driver — the v1 read path
  is recompute, so writing day-state would double the warehouse scan for data
  nothing reads; it activates when v2 flips the read path. Paired methods are
  notebook-only (the pipeline serves independent-arm experiments). Sequential
  CIs land in M5 (`ci_kind` is always `fixed` in M2 rows). The PG/MySQL
  testcontainers integration suite (incl. the two-process atomic-claim race
  test) is deferred to the M3 hardening list — CI runs the ClickHouse
  first-run e2e gate; the PG/MySQL claim SQL is unit-tested per dialect.
  Internal table-name overrides (`tables:` block) validate but are rejected
  until the mixins are parameterized.

## M3 — The explore cockpit (PRIORITY) + reporting ✅ SHIPPED
- ✅ `abk explore`: localhost server, live `from_suffstats` recompute (Tiers
  E/α/S/R over a bounded session cache), stabilization chart with tier-styled
  segments, Basic/Advanced knobs auto-derived from `param_specs`,
  `.history` write-back, orphan detection, the D3 calibration gate.
- ✅ Self-contained HTML readout (`abk run --report`); readout decision logic
  (WIN/LOSE/FLAT/INCONCLUSIVE; pre-horizon refusal; SRM gate); the `web/`
  toolchain with committed bundles + CI freshness/marker/token gates.
- ✅ **DoD held:** Apply gated when uncalibrated (`confirm_uncalibrated`
  against the empty `_ab_aa_runs`, e2e-tested); calibration chip wired (all
  D3 states; goes green via M4). *(Must-fixes discharged: calibration-in-
  explore, SRM surfacing, peeking rendering — see
  m3-implementation-plan.md §5.)*
- **Deferred from M3:** WP9 PG/MySQL testcontainers + the two-process lock
  race (needs Docker — run in CI or a Docker-equipped box; the plan-§4 DoD
  row and the exit-gate "integration matrix"/"both e2e variants" words move
  with it); the real-ClickHouse explore e2e leg exercising D11 over a live
  warehouse read order (D11 is unit-proven by the order-permutation test —
  milestone-review record); Segment mode (D9); the `--metric` narrowing
  beyond default-metric selection is as built.

## M4 — A/A false-positive matrix (`abk validate`) ✅ SHIPPED
- ✅ Ported the autotune scaffolding → the pure `abkit/validate/` engine (placebo
  label-permutation splits over the experiment's own pooled cohort, D1; FPR + power +
  achieved-MDE + coverage + effect-exaggeration; **honest cumulative-peeking FPR** —
  the naive optional-stopping hazard, D3 — over the one-enumeration day-grid, denser-
  early ≤100-point cap with disclosure), `_ab_aa_runs` persistence (per-cell `run_id`,
  D4; effective two-tier alphas), the recommendation + plain-language verdicts, and the
  matrix UX (budget-band colors, Recommended row + rationale).
- ✅ `abk validate` CLI (own out-of-band `validate` lock, D5, `abk unlock`-clearable;
  non-zero exit on failure), `--report` reusing the committed report bundle (D10, no
  third JS bundle), the `metric.aa_fpr_budget` override completing the resolver (D12),
  and **Auto mode** — server-side `POST /validate` that greens the live explore chip in
  place and re-seeds the knobs, Apply gate unchanged (R19).
- ✅ **DoD held:** closed-form default (bootstrap A/A left an opt-in follow-up, D7);
  worked example authored (`aa-false-positive-matrix.md §8`); powers the explore
  calibration chip and the blind-rederivation arbitration; the exit-gate e2e proves the
  three classic failures in Binomial bands (`tests/e2e/test_validate_matrix.py`); zero
  method-math changes (goldens untouched, no `ALGORITHM_VERSION` bump). *(Must-fixes
  discharged: matrix UX, peeking FPR, validate cost bound.)*
- **Deferred to M5:** the sequential side-by-side column (D8 — needs `stats/sequential/`,
  all M4 rows are `ci_kind='fixed'`) and the full empirical **composed** FDR/FWER sweep
  over the multi-metric family (D9 — M4 ships each cell's peeking FPR at the correct
  two-tier alphas; read-time BH already shipped in M3).
- **Arbitrated, not implemented (D14, ex-D12, change control):** one/two-sided tests
  and winsorization — neither exists as a stats-core method param (p-values are
  hardcoded two-sided; no winsor code anywhere), and the explore rail is auto-derived
  from `param_specs`, so neither can be faked in the UI. Adding either is a stats-core
  change with the full obligations (identity impact, `statistics-changes.md` entry, A/A
  validation *through this harness*) — a named future change, not a milestone gap.

## M5 — Sequential analysis + planner + corrections ✅ SHIPPED
The implementation record + decisions are in
[m5-implementation-plan.md](docs/specs/m5-implementation-plan.md); the math in
[statistics-changes.md §4](docs/specs/statistics-changes.md).
- **`stats/sequential/`** — an opt-in (`sequential: {enabled: true}`, **default off**,
  byte-identical fixed path) asymptotic Gaussian **confidence sequence** (Waudby-Smith &
  Ramdas), computed as a pure MODE transform over the fixed `(effect, SE)`, never a method
  plugin. Rows carry `ci_kind='always_valid'`; the readout calls WIN/LOSE pre-horizon only
  under it; the toggle self-invalidates (a bare `abk run` re-plans the series). ~~alpha-
  spending / group-sequential~~ → a **future item** (no version promise; a
  `scheme: alpha_spending` config error names it).
- **The A/A matrix's `sequential.enabled` side-by-side column (D8):** `abk validate`
  renders the always-valid peeking FPR + power + CI-width beside the fixed ones — the CI
  brought back to ≈ α, the honest completion of the peeking story.
- **The composed multiple-testing FDR/FWER empirical validation (D9):** the read-time
  composed rule (two-tier Bonferroni ∘ BH) is one shared helper (`stats.correction.
  composed_significance`); `abk validate` sweeps the empirical **FWER + FDR** over the
  multi-metric family (one shared union-cohort assignment per iteration). Fixed-horizon
  in M5; **sequential × composed shipped in M6 (WP-B)** — the composed sweep gained its
  always-valid peeking twin.
- **`abk plan`** — the read-only pre-launch power/sizing planner (required-N / achievable-
  MDE / achieved-power at the effective two-tier alpha + look count & cost); **runtime /
  ASN shipped in M6 (WP-A)**.
- **Sub-day** (cumulative-intervals.md §6): the config lint recommends `always_valid` when
  the planned **look count** exceeds `warn_looks` (the dangerous variable is the look
  count, not the time unit — dense sub-day grids trip it first); `alpha_spending` is a
  config error at sub-day cadence; the anytime-valid sequential multinomial SRM (Lindon &
  Malek) replaces per-cutoff χ² below `1d`.
- Benjamini-Hochberg read-time was *pulled forward to M3 WP1* (`pipeline/readout.py`
  rescoring — an M2-accepted `correction: benjamini_hochberg` would otherwise verdict at
  the wrong alpha).
- **Shipped in M6:** the A/A sequential × composed sweep (WP-B) and `abk plan`
  runtime/ASN (WP-A). **Still deferred** — a future item, no version promise:
  `alpha_spending`/group-sequential.

## M6 — DX, docs, orchestration, release ✅ SHIPPED
- `abk init-claude` + packaged `.claude` assets (the managed `CLAUDE.md` block, 9
  operator rules + 7 skills); single-source docs site (`abkit.pipelab.dev`, Astro +
  sync-docs) — **detectkit-analogous machinery with our own palette, logo, and landing
  page** (the real Iris "Diverge" brand; interfaces stay on a themeable brand-token
  layer) per [branding-and-site.md](docs/specs/branding-and-site.md); Prefect
  flow/deployment scaffolding in `abk init`; BI tool-agnostic reference SQL (paste into
  Grafana / Lightdash / Metabase / Superset) + one importable Grafana dashboard + the
  optional SRM panel; `abk test-report` channels (`abkit/notify/`).
- Two ex-deferrals pulled in as WPs: **`abk plan` runtime/ASN** (WP-A) and the **A/A
  sequential × composed** family sweep (WP-B) — both stats-pure, no `ALGORITHM_VERSION`.
- Release engineering: `__version__ = 0.1.0`, classifier `3 - Alpha`, the CHANGELOG cut,
  the wheel-namelist + `pip install` DoD gates, and the docs single-source drift gate,
  behind the WP10 exit gate (release-readiness e2e + ≥2 adversarial rounds).
- **DoD met:** `pip install ab-analysis-kit` (prep — the tagged publish is the
  maintainer's G1 step); `CHANGELOG.md` authoritative; contributor `CLAUDE.md` +
  `.claude/rules` + packaged init-claude assets in sync. **Zero statistical-number
  changes across M2–M6** (goldens intact, no `ALGORITHM_VERSION` moved).
- **Named future deferral** (no version promise): `alpha_spending`/group-sequential
  (see the hardening tiers below + the v2 list).

## The polish track — M7–M17 → `0.2.0` … `0.12.0` (approved 2026-07-18)

The post-`0.1.x` plan, **approved 2026-07-18**: the code-verified pain audit
([docs/research/2026-07-data-flow-audit/REPORT.md](docs/research/2026-07-data-flow-audit/REPORT.md)
— every claim re-checked against the code by a 10-agent verification pass; ~90%
held verbatim, four corrections recorded in the audit's banner and in the
affected milestone docs) **plus the entire post-baseline hardening backlog
below** (maintainer decision), laid out as milestones M7–M17. **One minor
release per milestone** (M7→`0.2.0` … M17→`0.12.0`), each published to PyPI
(tag → `publish.yml`). Neither 1.0 nor 2.0 is part of this track — 1.0 = the
polished library, 2.0 = the finished product; both come later. The plan itself
passed a 3-critic adversarial review (11 findings, incl. 1 blocker — folded in
below and in the milestone docs). Core (M7–M12): ~42 sessions; extension
(M13–M17): ~22–27; plus the **PLAN-1/PLAN-2 interstitial** below (~2, added
2026-07-29 — it rides a `0.6.x` patch and renumbers nothing).

- **Discipline (unchanged from M1–M6):** one WP = one session = one PR (tests +
  CHANGELOG + conventional commit); milestone exit gate = e2e + ≥2 adversarial
  review rounds with written findings + the implementation-plan doc in
  [docs/specs/](docs/specs/). Session estimates are **not contracts** — a WP
  that doesn't fit a session simply continues into the next one. After M7 and
  M8: retro-calibrate the remaining estimates against actuals (**M7 ✅ done:
  actual 7 PRs/sessions for all 8 WPs — at the track table's coarse ~7-session
  figure, but *under* the detailed m7 plan, whose per-WP lines sum to ~9.5–10.5
  (WP2/WP3/WP4 each budgeted 2 sessions but each landed in one). So the coarse
  M8+ figures stand, if anything conservative; the detailed multi-session WP
  estimates are the ones worth trimming.** **Recalibrated after M11:** the same
  pattern held four milestones running — M8's WP4 and WP5 and M11's DASH-5 each
  carried a 2-session budget and each landed in one, M10 shipped its 5 WPs in 4
  PRs plus the exit-gate PR, and M11 shipped 8 WPs in ~7 sessions plus a
  docs-only decisions PR. **Every
  detailed per-WP "2 sessions" left in the remaining plans should be read as
  1**; the coarse per-milestone figures stay as written. The costs that *did*
  recur are not in the WP bodies at all: an exit gate reliably finds real
  defects in already-merged code — M11's found two — and every milestone since
  M7 has paid ≥2 adversarial review rounds per WP.)
- **M7–M12: statistical numbers do not move anywhere.** Parity/golden gates
  (exact on integer counts + mandatory near-boundary stress fixtures, rel-1e-9
  on continuous values); the grep for `ALGORITHM_VERSION` bumps stays empty.
  **M13/M15 move numbers only through the full change control** —
  `ALGORITHM_VERSION` bump + `statistics-changes.md` entry + A/A revalidation
  through the *already-vectorized* `abk validate` (the M7-first ordering is
  what makes that revalidation cheap) + opt-in first where applicable.
- **Perf milestones (M7, M9) carry an executable perf test as an exit
  criterion** — track lesson: a rule without an executable gate does not hold
  (the 800k-iteration nested `for` loop slipped past numpy-first).
- **Schema policy:** breaks ship as documented recreate instructions, never
  migration code; **both real breaks were collected in M10** (drop the
  `_ab_results` date columns + rename/widen the `_ab_experiments` window
  `Date`→`DateTime64(3)`) — one guide, one release, shipped in `0.5.0`. Column
  *additions* are non-breaking (additive `ensure_columns`, M9).
- **Inter-milestone contracts** (plan-review findings): M8's
  `build_cohort_backend`/`ab_cohort_source` factory is the **only** way M9's
  STATE writer and tail-scan build cohort SQL (the blocker finding — a
  hand-rolled render silently joins a non-existent `_ab_exposures` under the
  no-copy default and yields silent zeros); M11 clones `tuning/server.py`
  **after** M10 WP4 — which has shipped, so the clone inherits the decoupled
  lock model (`heavy_lock` + the admission semaphore + `should_stop=`
  cancellation, not the old coarse `request_lock`); M14's dashboard surface
  builds on M11.
- **Coverage map:** REPORT #5–8→M7, #3–4→M8, #1–2→M9, #9–12→M10, #13→M11,
  #14→M12, #15→parked (revisited in M17). Hardening tiers below: Now-bug→M7
  WP0; "0.1.x safe wins" stats hot path→M7 WP1, its multi-arm UX wins→M14;
  "1.x versioned"→M13+M14; the v2 incremental engine→M9; v2 methods→M15;
  owned randomization→M16; app integration→M17.

### M7 — validate: vectorization + iteration policy → `0.2.0` ✅ SHIPPED
Implementation record: [m7-implementation-plan.md](docs/specs/m7-implementation-plan.md)
(the amended design contract — done table, per-WP as-built notes, exit-gate
record). All eight WPs landed, **including the stretch**: the 800k-iteration
nested Python loop (`scoring.py`) became a numpy block-streaming engine with
**the same numbers** — scipy hot-path swap + lazy imports + bucket A1–A8, up
to ~149× on `normal_test` (WP1, rides WP0's live multi-arm Review-mode fix);
the batch significance kernels (`from_suffstats_array`, bit-exact vs the
scalar path via `_libm_pow`, WP2); the block-streamed permutation engine
(`vector_resample`, masks bit-identical to `placebo_mask` by construction,
WP3); the `score_cell` dispatcher with verbatim scalar fallback, ~10×/cell
(WP4); the exhaustive scalar↔vectorized parity gate + executable perf gate
(WP5); the vectorized family sweep, ~18× (stretch WP7); and the iteration
policy — `--family-sweep` opt-in + per-cell default `max(2000, ceil(200/α))`,
warn-never-cap above 100k (WP6). **Zero statistical numbers moved** (no
`ALGORITHM_VERSION` bump; both e2e matrix gates byte-identical; the
documented engine-parity boundaries — fixed-BLAS byte-repro, the
exactly-solved-boundary flip — are test-pinned properties, not drift).
**Retro-calibration datum:** the track table's coarse estimate was ~7 sessions
(one WP per session, 7 WP + 1 stretch); the detailed per-WP lines in this doc
sum to ~9.5–10.5 (WP2/WP3/WP4 each budgeted 2 sessions). Actual: 7
implementation PRs (#38–#44) for all 8 WPs + this exit-gate/release session —
so delivery hit the coarse figure and beat the detailed one (WP2/WP3/WP4 each
closed in a single session). The coarse M8+ figures stand (if anything
conservative); the detailed multi-session WP estimates are the ones to trim —
revisit after M8.

### M8 — assignments: no-copy default + incremental copy → `0.3.0` ✅
Implementation record:
[m8-implementation-plan.md](docs/specs/m8-implementation-plan.md).
Metrics join *your* assignment source directly; `_ab_exposures` becomes an
**opt-in incremental copy** (detectkit-style watermark batching), never a
2M-row rewrite: the `assignment.cohort_copy` config block (WP1 — renamed from
the working `assignment.copy`: a pydantic field named `copy` shadows
`BaseModel.copy` and warns at import), the pushdown
`ExposureSnapshot` (WP2), the single `ab_cohort_source` builtin (WP3), **all
call-sites through the `build_cohort_backend` factory** (WP4 — the contract M9
depends on), the incremental copier + `abk run --resync-cohort` (WP5),
both-mode e2e incl. the growing-source increment (WP6) — PRs #46–#51 — and
the 3-way docs sync + `0.3.0` cut (WP7). Zero statistical numbers moved.

### M9 — additive compute engine + CUPED Tier-E → `0.4.0` ✅
Implementation record:
[m9-implementation-plan.md](docs/specs/m9-implementation-plan.md).
Kills the O(D²) full-window rescan for closed forms by finally wiring the
STATE stage + `_ab_unit_state` (cumulative-intervals §4–6: warehouse-side
day-bucketed increments, sub-day = state + tail-scan through the M8 factory —
the blocker contract), and makes CUPED instant in explore: +4 covariate
columns via additive `ensure_columns` (WP1 — PR #53), CUPED → Tier-E with
rel-1e-9 reconstruction — the "byte-for-byte" REPORT claim is refuted, the
gate is rel-1e-9 (WP2 — PR #54), the STATE stage (WP3 — PR #55), the opt-in
`IncrementalBackend` with gap→Recompute fallback, never silent undercount
(WP4 — PR #56), `abk verify-incremental` + cost observability + state GC + the
executable perf gate (WP5 — PR #58), and the exit gate — the flag on/off
changes no number (WP6). Bootstrap stays full-window forever.
**The load-bearing as-built delta:** day-additivity became an **explicit
metric declaration** (`state_additive: true`, default off) after three review
rounds each defeated a textual additivity check with a new SQL shape; the text
check survives as a veto-only filter and `abk verify-incremental` is the
empirical oracle — which caught the scaffolded `example_signup_cr`
(`max()` + a literal trial count) inflating `size_1` 11×. `incremental_reads`
shipped **default off** with the flip criteria recorded in
[cumulative-intervals.md §4.1](docs/specs/cumulative-intervals.md); PERF-1
has since run them (§4.2) — `abk init` scaffolds it ON, the library default
stays off.
Measured: recompute scans `N·D(D+1)/2` fact rows across a D-day series,
the additive path `N·D` — and zero inside COMPUTE at daily cadence.
Zero statistical numbers moved (no `ALGORITHM_VERSION` bump; the flag-off/on
parity gate is the milestone's №1 assertion).

### M10 — timestamps + schema cleanup + explore polish → `0.5.0` ✅
Implementation record:
[m10-implementation-plan.md](docs/specs/m10-implementation-plan.md)
— **its §4 records the five decisions settled 2026-07-25, two of which
overturned the WP bodies.** The window fields are **renamed**
`start_date`/`end_date` → `start_ts`/`horizon_ts` (no aliases — a date-shaped
name contradicts a flexible-interval system, and the legacy shape was to be
rewritten, not inherited), and grid anchoring becomes the configurable
`interval_anchor` (`midnight` — the absent-key behavior the scaffold writes
out — | `start` | an explicit timestamp, e.g. 3-day windows at 00:00 MSK on a
UTC warehouse), with one engine rule: cutoffs = anchor + k·interval, snapped
forward to ≥ start.
Experiment start/horizon became full timestamps (`date | datetime` union, no
coercion; the gate is numeric — an unchanged window persists unchanged
`_ab_results` numbers; WP1–2 = PR #61), **both track schema breaks landed here
in one recreate guide** (drop the `_ab_results` date columns = WP3, PR #62;
rename + widen `_ab_experiments` = WP2), the explore lock decoupled
(`heavy_lock` only for reload/validate/apply; `/recompute` free + post-compute
stale re-check; WP4 = PR #63), and bootstrap resampling memoizes
(`_resample`+`_finalize` split, memo key `(metric, arm pair, cutoff, cache
generation, method, resolved params)` — alpha excluded; "5 α → 1 resample";
WP5 = PR #64). Zero statistical numbers moved.

**Load-bearing as-built deltas.** A bare date is local midnight of THAT day for
**both** edges (D6), so `horizon_ts` is the EXCLUSIVE right edge and a ported
`end_date: 2024-07-14` becomes `horizon_ts: 2024-07-15` — one vocabulary, no
`+1 day` translation anywhere. WP1's own review found §0.2's call-site register
~60% accurate and its central claim false: six further sites needed changes,
three of them in M9 code (`IncrementalBackend` compared a `date` against the
config field — a `TypeError` on *every* cutoff under `incremental_reads`), and
the new knob reached none of the eight hand-copied `generate_grid` calls until
`ExperimentConfig.grid()` became the one factory (AST-gated, m8's
`build_cohort_backend` discipline). WP4's removed lock turned out to do **two**
jobs — it also cancelled superseded work (a 6-turn alpha drag cost 3.40 s at
8.7× CPU without that) and bounded concurrent computes — so the fix is
`should_stop=` polling plus a 2-slot admission semaphore, and
`warnings.catch_warnings` had to become thread-scoped because it is
process-global. WP5's prescribed memo key `(method_config_id, end_ts)` collides
three ways (metrics, arm pairs, and the identity-EXCLUDED `seed`, which IS the
draw); the shipped key carries all of it plus the cache generation, so a
resample that lost a race to `/reload` is unreachable rather than stale. The
exit gate (`tests/e2e/test_sub_day_anchors_and_explore.py`) pins a golden
captured from the pre-m10 code across 11 window shapes, and found the one
derived number that DID move — `horizon_seconds()` is now true elapsed time, so
it differs from the old nominal day count by exactly the window's UTC-offset
change (±30 min to ±24h by zone, and for a permanent shift with no DST at all),
which makes it agree with its own grid for the first time — plus a
breaking-change remedy that escaped as an uncaught traceback instead of
`abk run`'s error line, a `--workers N>1` path that still buried it, and an
incremental cohort copy that dropped every unit exposed before a sub-day
start.

### M11 — `abk dashboard` (the flagship overview UI) → `0.6.0` ✅ SHIPPED
Implementation record:
[m11-implementation-plan.md](docs/specs/m11-implementation-plan.md)
(PRs #66, #68, #69, #71–#75 + the docs-only decisions PR #67; **released as
`0.6.0`**, tagged and published to PyPI).
The `dtk ui` architecture ported: metadata-only boot, lazy per-row stats
(client-side pool of 3), sparklines ≤160 points, buttons = CLI subprocesses;
the server **never takes the pipeline lock**; verdicts via
`readout.evaluate()`. `JobManager` port (DASH-1), `overview.py` (DASH-2), the
server skeleton with the two test-pinned deltas from the tune-server pattern —
token on ALL routes, never self-shutdown (DASH-3), the `abk run --metric`
capability a per-metric Run button needs (DASH-4a — a pipeline/CLI WP, not a
dashboard one), job routes (DASH-4),
`dashboard.ts` written from scratch — the donor has no TS sources (DASH-5),
the third build entry + `abk dashboard` CLI (DASH-6), and the exit gate
(DASH-7). CRUD editing is explicitly phase 2, out of the milestone (it shipped
in the `0.6.x` UI-1 interstitial below).
**Zero statistical numbers moved** (no `ALGORITHM_VERSION` bump).

**Load-bearing as-built deltas.** DASH-2's prescribed step 3 — window the rows,
then call `evaluate()` — was **wrong for this data model**: `_ab_results` rows
are cumulative looks from a fixed start, not the donor's plain time series, so
cutting the left edge feeds the readout a truncated stabilization history. A
14-day daily WIN read INCONCLUSIVE at 24h (every daily experiment), and a
6h-cadence series inverted into a WIN the full readout refuses to give;
`--window` now bounds only the sparkline's x-range. Two more row-shape rules
followed: rows for an arm pair the config no longer declares are dropped before
the series lookup (they still enter the BH family and tightened the threshold —
9 renamed pairs turned a report WIN into a dashboard INCONCLUSIVE), and an
experiment with **no rows of its own** must never reach `evaluate()` at all —
zero rows rendered INCONCLUSIVE, a verdict about *data* on a row nobody
computed. The launcher invariant needed **two** gates: the module-level AST
scan cannot see a lock taken through a helper, so a spy runs over every job
route. Spawning was the other surprise: `-m` puts the child's CWD — the
operator's project root — on `sys.path[0]`, so a stray `click.py` there breaks
every button and an `abkit/` directory there runs a *different* abkit than the
one serving the page; the child is now bootstrapped with the CWD dropped, which
makes an **installed abkit** a hard requirement for every job (warned once at
startup, and only after a probe that ignores dist-info metadata — a checkout
whose install was removed keeps it, and that stale metadata silently suppressed
the warning until the exit gate). `--select` is passed as the YAML **path** and
every route re-resolves it through the child's own resolver, because a bare
name resolves file-first (a file named after another experiment shadows it) and
because `abk run/unlock/clean` answer an unmatched selector with "Nothing
selected." and **exit 0** — a green job that computed nothing. DASH-4a found
the one thing a narrowed run must still touch outside its filter: a
stale-but-contiguous `_ab_unit_state` day is invisible to the M9 gap check
(absence only), so a scoped `--full-refresh` truncates the withheld metrics'
day state instead of leaving it (reproduced as a silent 3334.5-vs-3434.5
undercount before the fix).

### Interstitial — `abk plan` sizing gaps ✅ SHIPPED (PLAN-1 `0.6.1` / PLAN-2 `0.6.2`) → `0.6.x`
Design contract: [cli-and-dx.md §1 "`abk plan` sizing gaps"](docs/specs/cli-and-dx.md).
Added 2026-07-29 (maintainer request). Two small, independent WPs that make
pre-launch planning answer the questions the architecture already has the data
for. **Deliberately NOT a milestone**: it renumbers nothing (M12–M17 keep their
numbers and their minor versions), moves no `_ab_results` number, needs no
schema change, and ships as a **patch on top of M11's `0.6.0`**. Sequenced after
M11 so the dashboard release is not held up; either WP can also be pulled
forward — they touch only `abkit/planning/` + `abkit/cli/commands/plan.py`.

- **PLAN-1 ✅ SHIPPED — size CUPED on the persisted covariate correlation.** `abk plan`
  sizes a `cuped-t-test` comparison on the **raw** variance and prints
  "sized on RAW variance — CUPED (ρ not persisted) lowers required-N further".
  That parenthetical stopped being true in M9 WP1, which persists
  `corr_coef_1/2` on every `_ab_results` row, and `stats/power.py` has shipped
  `cuped_adjusted_std` + `get_cuped_ttest_{sample_size,mde,power}` since M1
  (`validate/scoring.py` already uses the first). So the planner leaves a
  tested solve and a persisted input unused, and every CUPED plan line
  over-states required-N. ~1 session — **actual: 1 session**. As built, the
  three solves, the ASN's base variance and the plan line's note all read one
  `usable_corr` gate, and that gate needed a rule the design did not have: a
  covariate that *reproduces* the metric persists ρ a hair **below** 1, passes
  an `|ρ| < 1` check, and deflates the variance to rounding noise — the project
  `abk init` scaffolds is exactly that shape, and the first implementation
  printed "required 10/arm" for it. Deflation is now refused below
  `1 − ρ² = 1e-12` on both the persisted and the `--baseline corr=` path.
- **PLAN-2 ✅ SHIPPED — `abk plan --from-history <interval>`: baseline moments for an
  experiment that has never run.** Today a greenfield experiment is either
  SKIPPED ("no baseline") or needs hand-supplied `--baseline
  <metric>:mean=..,std=..,n=..`. The render it needs already exists: the CUPED
  covariate's pre-period path (`ab_apply_exposure_filter=False`) renders a
  metric's own SQL over a whole-day window that precedes exposure by
  construction. ~1 session.

Two neighbouring gaps stay where they are, on purpose: a **power formula for
ratio metrics / bootstrap methods** is a statistical addition under full change
control (M13 design session, or M15 with the new methods — `abk plan` refuses
them today rather than inventing math, D10), and **multi-arm sizing** (the
planner sizes the first declared pair only) belongs to M14's decision layer.

### Interstitial — the cockpit's next gaps + the incremental default ✅ SHIPPED (`0.6.4`) → `0.6.x`
Added 2026-08-02 (maintainer request, in conversation). Like the PLAN
interstitial above it **renumbers nothing** (M12–M17 keep their numbers and
their minor versions), moves no `_ab_results` number and needs no schema
change. Sequenced ahead of M12 only where it is cheap; UI-1 can also follow it.
**Status: CLOSED — UI-1 + UI-2 + PERF-1 all shipped and released together as
`0.6.4`** (tagged `v0.6.4`, on PyPI).
**Release plan (decided 2026-08-02): ONE `0.6.4` when PERF-1 lands** — honored;
not a cut per WP, because the interstitial is a coherent unit, the library has
no live users to hurry for, and sweeping the previous release's status lines
(the lesson that has bitten twice in three releases) is then done once.

- **`0.6.3` cut ✅ DONE** (PR #82, `8c86a46`, tagged `v0.6.3`, on PyPI). It
  carried the `paths.experiments` selection fix (#81, `16edfce`): a project
  whose experiments live outside `experiments/` answered "Nothing selected." to
  all ten commands.
- **UI-1 ✅ SHIPPED — CRUD YAML editing in `abk dashboard`** (the M11 "phase 2"
  item). `abkit/tuning/config_files.py` is the new seam: validate (BOTH levels —
  `ExperimentConfig`, then the §8 matrix `abk run --steps validate` runs) →
  archive byte-verbatim under `<experiments>/.history/<name>/` → atomic write,
  behind `POST /api/experiment/{save,create,delete}` + `POST /api/reload` +
  `GET /api/experiments`. ~1 session — **actual: 1 session**. Three things the
  design did not have and the build needed:
  - **It could not be built on `config_writer`.** Apply merges a *structured*
    edit and RE-EMITS the document, so comments die; an editor must round-trip
    the operator's own TEXT. The two seams stay different shapes and share only
    the archive + atomic-write primitives.
  - **The boot snapshot had to go.** M11 read the selection once and told the
    operator to restart; with a create button that is a page that cannot show
    what it just wrote. Every mutation now re-resolves the cockpit's own
    `--select`/`--exclude`, re-bakes the page and returns the refreshed list —
    and a reload that FAILS keeps the previous selection and warns, because the
    write has already landed and a 500 would report a successful save as a
    failure.
  - **Level 2 needed an override.** Refusing every save until the whole project
    lints makes the editor useless in the one situation it is opened for; so
    §8 errors are forceable (and come back as `SAVED WITH AN ERROR — abk run
    will refuse this: …`) while level 1, which decides whether the row can be
    served at all, never is.
  The invariant was restated as predicted — *computes no statistic and takes no
  pipeline lock* — and the restatement cost nothing, because both gates were
  already lock-API-specific; what they lacked was **coverage of the writing
  routes**, now added (plus an AST honesty check for the POST route list, which
  was hand-maintained where the GET one was not).
- **UI-2 ✅ SHIPPED — `abk ui` as an alias for `abk dashboard`.** The donor's
  project-level cockpit is `dtk ui` (`dtk tune` is the per-metric sibling we
  ship as `abk explore`); abkit renamed both, and the rename was never
  arbitrated in a decisions table — it fell out of the pair. `dashboard`/
  `explore` say which surface you want where `ui` does not, so the canonical
  name stays; a Click alias costs three lines and keeps the muscle memory of
  anyone running both tools. Folded into UI-1's PR, registered as the SAME
  callback object (`cli.add_command(dashboard, name="ui")`) so the two names
  cannot drift in options or help text.
- **PERF-1 ✅ SHIPPED — the incremental path is discoverable, and the default is
  decided with numbers.** Both halves landed in one session as designed. The
  three as-built deltas the design did not have:
  - **An absent key and an explicit `false` had to become different things.**
    The design said "make it loud"; it did not say how the noise stops. A
    warning that fires forever on a project that has decided *no* is just a
    different defect, so `declared` reads pydantic's `model_fields_set` — the
    field stays a plain `bool`, every reader is untouched, and writing the flag
    either way is the answer that silences it.
  - **The threshold is LOOKS, not days.** §4.1 says "D ≲ 5 days" because it
    assumed a daily grid; the recompute scan is quadratic in *looks*, and an
    hourly cadence re-reads the window 24× a day. The hint fires at ≥ 6 looks
    and the spec now says which variable it is.
  - **The scaffold flip made the milestone's №1 assertion vacuous, exactly as
    predicted — and the full suite went green anyway.** `test_incremental_run`'s
    "flag off" leg turned the flag on by *not* appending a `compute:` block, so
    the parity gate compared the incremental path against itself and still
    passed. Both legs now go through one helper that asserts the edit landed,
    and each leg proves from `--cost-report` output which path it took. Fixing
    it also removed a duplicate-YAML-key hazard four test files shared.
  Evidence, published in [cumulative-intervals.md §4.2](docs/specs/cumulative-intervals.md):
  three consecutive clean `verify-incremental` runs over the scaffold (zero
  divergences, zero `unverified`), and fact-scan savings of 2.5× / 3.5× / 5.0× /
  8.5× / 11.0× at 2 / 4 / 7 / 14 / 19 looks — TOTAL fact rows scanned, each
  reproducing `N·L(L+1)/2 + N·L` vs `N·L` exactly, with **zero** fact rows
  inside COMPUTE at daily cadence. Decisions held: library default stays `false` (criterion 3 is the
  operator's ingestion SLA, not abkit's to assume), scaffold ships `true`. Zero
  statistical numbers moved.

  *The original contract, for the record:* `compute.incremental_reads` is `false` with the reason recorded in
  the field itself — *"Default false until verify-incremental (m9 WP5) bakes"*
  — which was right at M9 (`0.4.0`) and has not been revisited four releases
  later. Two halves, in order:
  1. **The silence is the actual defect.** Nothing in `abk run` or
     `--cost-report` ever tells the operator the fast path exists; it lives
     only in [cumulative-intervals.md §4.1](docs/specs/cumulative-intervals.md).
     Worse, the scaffold declares `state_additive: true` on `example_arpu`, so
     a default project **pays the STATE write and never takes the read** — the
     one configuration that is strictly worse than either endpoint. `abk run`
     should say so when a series is long enough for it to matter, and
     `--cost-report` should print the counterfactual scan it would have paid.
  2. **Only then the default.** The flag does not guard against a wrong number
     (parity is rel-1e-9 pinned; any state gap falls back to recompute on its
     own) — it guards against one thing: a backfill arriving later than
     `data_lag` freezing in day state. That is a property of the operator's
     ingestion, not of abkit, which argues for keeping the opt-in and making it
     loud rather than flipping it blind. Decide with evidence: run §4.1's
     criteria on the scaffolded project and publish the `--cost-report`
     numbers. ~1 session for (1); (2) is a decision, not code.

  **Decided 2026-08-02, ahead of the WP** (so the session executes rather than
  re-asks; any of these is revisable if the measured numbers contradict it):

  - **Both halves land in ONE session, and the numbers go in the PR.** Splitting
    the evidence off into a session of its own would leave the flip undecided
    for another release, which is exactly how the current justification went
    stale — it has been correct-and-unrevisited since `0.4.0`.
  - **The library default stays `false`.** The flag guards one thing: a backfill
    arriving later than `data_lag` freezing in day state. That is a property of
    the operator's ingestion SLA, not of abkit, and a default that depends on
    someone else's SLA is the wrong default to flip. What is actually broken is
    the silence, and that is what (1) fixes.
  - **The SCAFFOLD flips to `incremental_reads: true`, with a comment saying
    why.** The incoherence is the defect, and it can be fixed in either
    direction — but M9 chose `example_arpu` as the additive demo deliberately,
    so a scaffold that pays the STATE write and then *demonstrates the read* is
    strictly more useful than one that demonstrates neither. `abk
    verify-incremental` over the scaffold is the proof that ships with it. Note
    the test impact: the e2e gates run over the scaffold, so this changes which
    path they exercise by default — pin BOTH paths rather than swapping which
    one is covered.
- **`0.6.4` cut ✅ DONE** — the whole interstitial in one release (UI-1 + UI-2
  = #83 `351cf8e`, PERF-1 = #86 `9489b33`), tagged `v0.6.4` and published to
  PyPI, with the `0.6.3` status lines swept in the same PR.
- **UI-3 📋 — metric YAML editing: deliberately NOT in this interstitial**
  (decided 2026-08-02). UI-1 edits experiments only. A metric edit without its
  `sql/` file is half a feature — a metric references SQL by path — so it needs
  a second editor surface whose validation story is different (there is no
  pydantic model for a `.sql` file; only the render smoke and the macro lint),
  plus a metric list the dashboard does not have. That is a full WP of new
  surface, not an addendum to a closing interstitial. Revisit **after M12**,
  with its own design pass.

### M12 — notifications → `0.7.0` ✅ SHIPPED (NTF-1…NTF-6; released, on PyPI)
Design contract: [m12-implementation-plan.md](docs/specs/m12-implementation-plan.md).
`abkit/notify/` (shipped M6, reachable only via `abk test-report`) gets wired
to six real signals behind opt-in `--notify`, with dedup/cooldown state in
`_ab_notify_states` — **a verdict flip always sends over the cooldown, an
unchanged verdict never re-sends**, and a notification failure **never fails
the run** (fail-soft, e2e-pinned): the send seam + readout-ready (NTF-1),
SRM/error urgency with `on:` filters (NTF-2), the dedup state machine (NTF-3),
four new channels — discord/teams/googlechat/ntfy — as thin adapters (NTF-4),
calibration-red + staleness from existing signals (NTF-5), and the exit gate +
5→9 channel docs (NTF-6).

**Both "before start" decisions are signed off** (maintainer, 2026-08-02, in
conversation — recorded in the plan's §5): **D1** — under `--notify` an
experiment with no `notify:` block sends to **every configured channel**
(the block is routing, never the switch); **D2** — a verdict FLIP always sends
even inside a cooldown window, an UNCHANGED verdict is never re-sent, and
`cooldown_seconds` is reserved for future recurring kinds. The two remaining
§5 questions (whether explore-Apply calibration-red is deferred *permanently*,
and whether `abk explore` should ever carry `--notify`) are deliberately left
for the exit gate — no NTF WP depends on them.

- **NTF-1 ✅ SHIPPED — the send seam.** `abkit/notify/dispatch.py` +
  `abk run --notify`: the just-persisted rows go through `readout.evaluate()`
  (the report's own function) and out as one payload per verdict. The
  experiment-level `notify:` block (`channels`/`mentions`/`on`) and the
  per-channel `on:` land with it and are honoured for the `readout` kind, so
  neither is decorative until NTF-2. ~1 session — **actual: 1 session**. Two
  things the design did not have:
  - **The `on:` field breaks every channel unless the factory strips it.**
    `notification_channels` is `extra="allow"`, so each sibling key is a
    constructor kwarg — and because the field is *declared*, `model_dump()`
    emits `on: None` even for a config that never mentions it. Left alone,
    NTF-1 would have broken `abk test-report` for every existing user
    (18 tests red under the mutation probe). `ChannelFactory` now owns a
    `ROUTING_KEYS` strip, pinned by a test that requires every declared field
    to be classified.
  - **`SignalKind` needed a leaf module** (`abkit/config/signals.py`): both
    config models declare an `on:` filter and neither may import the other's
    dependency tree to share the literal.
- **NTF-2 ✅ SHIPPED — the urgent half.** A `failed` run sends an error notice
  (the reason, no statistics block, not gated on rows — the absence of a result
  is the news); an SRM-failed readout is **re-classified**, not re-sent, so
  `on: [srm, error]` is a real on-call channel. ~1 session — **actual: 1
  session**. Three as-built deltas:
  - **A notice is `ReadoutData` + `kind`, not a second payload type** — that is
    what lets all five channels carry it through the transport they already
    have. Every rich renderer (`webhook._rich_body`, `telegram._build_html_message`,
    `email._build_html_body`/`_subject`) gained ONE branch: omit the statistics
    block. The plan's file list named only `dispatch.py`/`run.py`, but a notice
    that renders "Effect: N/A · Flat" is the defect the WP exists to avoid.
  - **`send_notice(notice)`, not `send_notice(notice, kind)`.** The plan asked
    for a kind parameter to avoid a later signature rework; the kind has to ride
    on the payload anyway, because `verdict_color()` is called deep inside each
    channel's payload builder where no argument could reach it. Two sources of
    truth free to disagree is the worse trade.
  - **No sixth brand hex.** The five tokens are verdict tokens; notices reuse
    `--srm` `#B23A6B` and the word/emoji carry the distinction. Adding a real
    error token is a designer's call, and it is one map away.
- **NTF-4 ✅ SHIPPED — nine channels.** `discord`, `teams`, `googlechat`, `ntfy`
  as thin adapters: platform wire format + limits taken as fact, content always
  from `build_context()`, zero donor alerting semantics. ~2 sessions —
  **actual: <1 session** (the notice/verdict split NTF-2 built meant each
  channel needed one branch, not two renderers). Three platform rules that bend
  the house pattern, all pinned by tests: Discord's decimal colour + mentions
  that only ping from top-level `content`; Teams' NAMED Adaptive Card colour
  (the one channel where the brand hex cannot pass through) over the Power
  Automate Workflows path; Cards v2 ignoring `\n`; and ntfy's BYTE-budget body
  cap. The ntfy `priority` override is deliberately partial — urgent kinds only,
  so a WIN can never be configured into buzzing a phone.
  **Not done, and named:** the plan's live-webhook smoke against a real Teams
  workflow. It needs credentials this environment does not have; the JSON shape
  is unit-pinned, but the Workflows contract is an actively migrating Microsoft
  surface, so the first operator to wire one should run `abk test-report`.

- **NTF-3 ✅ SHIPPED — the dedup, and the flag becomes schedulable.** The new
  `_ab_notify_states` table + the pure `notify/cooldown.py` rule: a change always
  announces, an unchanged value never re-announces (D2, signed off before
  implementation). ~1–2 sessions — **actual: 1 session**. Three as-built deltas:
  - **"Unchanged" had to mean the verdict AND its SRM gate**, so the table
    carries `last_srm_flag` beside `last_verdict`. The plan's schema deduped on
    the verdict word, which reads fine until you notice a pre-horizon pair says
    INCONCLUSIVE either way — the word-only key would have swallowed the SRM
    alarm NTF-2 had just built, on the experiments most likely to need it.
  - **State is recorded only after a channel ACCEPTED the message.** `_deliver`
    now returns per-payload success counts for exactly this. Recording an
    announcement that reached nobody loses the flip permanently, because nothing
    re-derives what was never sent.
  - **`_ab_notify_states` joined `EXPERIMENT_KEYED_TABLES`**, so `abk clean
    --orphaned-experiments` purges it. Not tidiness: a deleted-and-reused
    experiment name would otherwise inherit the old history and have its first
    verdict deduped away. Two roster tests that hand-listed six tables are now
    derived from `INTERNAL_TABLES` instead.

- **NTF-5 ✅ SHIPPED — the two signals about the machinery.** `abk validate
  --notify` (new flag) sends `calibration_red` for every cell whose measured FPR
  exceeded its budget; `abk run --notify` also sends `stale` for a series more
  than 3 cadence steps behind the looks already due. Zero new detection, as
  designed. ~1–2 sessions — **actual: 1 session**. Four as-built deltas, one of
  them a real defect the WP existed to expose:
  - **The backlog detector was wrong, and routing it is what proved it.** It
    measured against the WATERMARK, which keeps advancing after an experiment's
    cutoffs stop at its horizon — so a finished, fully computed series reported
    a backlog of "now − horizon" growing by a day every day. As a printed
    warning that was noise; as a notification it would have alarmed about every
    experiment a team has ever finished. Now measured against
    `last_due_cutoff(grid, watermark)`, pinned both ways in
    `tests/pipeline/test_pipeline.py::TestBacklogSignal`.
  - **`stale` is retrospective and the message says so.** The run that detects a
    backlog is the run that drains it (PLAN detects, COMPUTE computes), so the
    news is that the SCHEDULE slipped, never that the warehouse is behind. The
    plan's word for it — NTF-2's placeholder "Data is stale" — is now "Schedule
    fell behind". A "still stale" variant would need a condition nothing in the
    pipeline measures.
  - **Recurring kinds needed a dedup of their own**, so the plan's step 4 grew a
    rule: the signature is WHICH things are wrong (metrics behind, red cells),
    never the lag or the FPR — both drift every run and would re-announce
    forever. A cleared condition is RECORDED as cleared (an observation, not an
    announcement, so the NTF-3 "only what was received becomes history" rule is
    untouched), or a second outage months later would dedup against the first.
    `notify.cooldown_seconds` — the field NTF-3 deliberately withheld — ships
    here as the knob that lets an unchanged condition repeat, and `None` is
    distinct from `0` (`is_in_cooldown` mutes neither, so deferring to it alone
    would have made the DEFAULT re-announce on every run).
  - **No new brand hexes** (the plan asked for two): NTF-4's rule stands —
    the five tokens are VERDICT tokens, all notices reuse `--srm`.
  **Not done, and named:** `verdict_change` is still the one declared kind
  nothing emits, so an `on: [verdict_change]` filter matches nothing. NTF-6
  decides — emit it for a payload that survived the dedup gate, or drop it.

- **NTF-6 ✅ SHIPPED — the exit gate, the docs, and the sixth kind.**
  `verdict_change` had been declared since NTF-1 and emitted by nothing, so
  `on: [verdict_change]` matched NOTHING; it now fires as the narrow view of a
  readout it always read like (the verdict WORD moved — not a first
  announcement, not an SRM-triggered re-send). ~1 session — **actual: 1
  session**. The gate `tests/e2e/test_notify_pipeline.py` runs three
  experiments (healthy / 90-10 split against 50-50 data / a warehouse that
  raises for one experiment's queries only) through ONE `abk run --notify` and
  proves: the cross-invocation dedup over the real table, urgent-vs-routine
  routing, **a channel that raises on every send changes no exit code**, and
  one channel per signal kind. Two mutations confirm it bites. Two findings,
  both in the gate I was writing rather than in the code it tests: an assertion
  that was a tautology (`... or True`), and a roster test pinning a
  hand-written set of covered kinds instead of deriving coverage from the
  file's own channel configs. Round 2's race analysis found nothing to fix and
  is recorded as an argument: two `abk run`s of one experiment cannot both
  notify (the `_ab_tasks` lock makes the second `locked`, and `locked` notifies
  nothing), and the one genuinely concurrent pair — `run` and `validate` —
  writes disjoint rows. The milestone's implementation record is
  [m12-implementation-plan.md](docs/specs/m12-implementation-plan.md) §7.
  **Still the maintainer's to close** (§5 questions 3–4, deliberately parked
  for the exit gate): is the explore-Apply calibration-red hook deferred
  *permanently*, and should `abk explore` ever carry `--notify`? Nothing in the
  milestone depends on the answer.

### M13 — versioned statistical improvements (bucket B, core) → `0.8.0` ✅ SHIPPED (STAT-1c, STAT-2, STAT-1b, STAT-1, STAT-3a, STAT-3, STAT-4, STAT-6; released, on PyPI)
**The design session ran on 2026-08-03** and its output is
[m13-implementation-plan.md](docs/specs/m13-implementation-plan.md) (+ three
blind re-derivations and a code audit in
[docs/research/2026-08-m13-blind-rederive/](docs/research/2026-08-m13-blind-rederive/)).
It **regrouped the contour below**, which is kept for the record:

> *Original contour:* Holm over Bonferroni (strictly more power, same FWER);
> unpooled SE in the z-test CI; restore the relative-z covariance term; uniform
> ddof=1; Agresti-Caffo/Wilson proportion CIs; the main-tier `metrics_count=1`
> FWER fix.

What the session changed, each item load-bearing:

- **The FWER "fix" may move no number at all.** The current levels are exactly
  those of a valid per-arm-pair gatekeeping procedure whose gate the readout does
  not enforce; the worst case is `2α` flat in `g` and `k`, and the *main* tier —
  the one the ship decision reads — is already at Bonferroni-α.
- **Two new WPs the contour missed.** *Guardrails* are currently corrected like
  growth metrics, which makes the engine less able to detect harm — the only
  safety-directed item, and its declaration/storage/UI already exist. *Declaring
  the contrast set* (`vs_control` vs `all_pairs`) buys a factor `g/2` in level —
  more power than Holm, for a config field.
- **"Unpooled SE" and "Agresti-Caffo/Wilson" were one question**, answered by
  Miettinen–Nurminen: one score statistic used three ways, whose value at δ=0 is
  *identically* today's pooled z, so the p-value does not move and CI↔verdict
  coherence is preserved by construction.
- **"The relative-z covariance term" is misnamed** — what is missing is the `R²`
  coefficient, not a between-arm correlation, which is zero.
- **Uniform ddof=1 is recommended DROPPED**: second-order in `1/(n−1)`, below the
  A/A instrument's noise floor, and dominated at small n by the normal-vs-Student-t
  error deferred to M15.
- **No `ALGORITHM_VERSION` bump is needed**: under opt-in, an identity-flagged
  param already orphans the opting-in operator's series.

**All thirteen design decisions are settled** (plan §3). The two that shape the
milestone most: a decision and its stored interval **may diverge** (so Holm is
reachable, and `_ab_results.reject` is redocumented as a pre-family flag), and
**guardrails are uncorrected** — raw α, out of the secondary budget, which
loosens α for the screening metrics remaining in it.

Net effect on numbers: **the FWER item moves none** (the claim is fixed and Holm
is added read-time; neither the gate nor a halved budget ships), the proportion
interval moves **no p-value** (Farrington–Manning form is bit-identical to
today's pooled z at δ=0), and the relative-effect fix moves **no default**.
*Corrected at build time:* D10's "Fieller changes no verdict" was true of
replacing the **shortcut**, which is what the z-test had and what STAT-3 already
answered; the MEAN methods carry the **delta** interval, whose rejection set does
differ — so under `interval: fieller` the relative p-value deliberately becomes
the absolute comparison's, which is what makes the interval and the p-value one
decision. What moves is interval endpoints, the opted-in relative p-value,
guardrail α, and — for whoever opts in — `method_config_id`.

5 WP (uniform ddof dropped), ordered by value rather than dependency —
**STAT-1c (guardrails uncorrected), STAT-2 (the false-positive sign instrument),
STAT-1b (the declared contrast set), STAT-1 (Holm + the claim), STAT-3a (the
`asymmetric_ci` guard), STAT-3 (the score proportion interval) and STAT-4 (the
Fieller relative interval) and **STAT-6 (the exit gate + the batch A/A
revalidation) are ✅ shipped**, and the milestone was **released as `0.8.0`**.
Baseline goldens stay untouched; new numbers get **new** goldens.

**STAT-6 as built:** the byte-compatibility gate compares against the **released
`v0.7.0` code**, not against HEAD — two surfaces captured from a real checkout
(the scaffolded project, and a three-arm five-comparison experiment reaching
every default M13 touched) reproduce **all 89 persisted rows**, with exactly one
delta. That delta was a **defect the gate existed to find**:
`_ab_experiments.contrasts` (STAT-1b) was in the table model and in
`catalog_record` but not in the catalog writer's field whitelist, so it was
dropped in silence — and it was declared NOT NULL with no default, a shape
`ensure_columns` refuses, so every installed project's first `0.8.0` run would
have failed with a spurious "drop and recreate". Both fixed here. The batch
revalidation ([record](docs/research/2026-08-m13-revalidation/REPORT.md)) then
measured every option beside its default at 20 000 iterations **on the same
placebo draws**: all calibrated, with `interval: score` agreeing with `pooled`
to the last float on the FPR, the sign split and the power (it rejects the same
placebos by construction) and differing only in relative-scale coverage — 93.4%
against 90.6%, nominal 95% — while `delta`'s false positives land below zero 64%
of the time with its two-sided FPR *and* coverage reading perfectly calibrated:
the blindness STAT-2 shipped to fix, reproduced end to end. It also
found a second test that could not fail: under the complete null Holm and
one-step Bonferroni are the same event, so the "Holm is more powerful" assertion
was `x >= x`; the claim moved to a planted-effect family where it is measurable.

**STAT-1 as built:** `correction: holm` sits beside `benjamini_hochberg` in the
one read-time seam (`composed_significance`) — the two differ only in the p-value
adjuster — and the two-tier levels are byte-identical, because the FWER item was
a **claim** defect: the scheme's main tier is at α and its whole-experiment bound
is `2α` at the single-main-metric default — flat in the arm count and in the
number of secondary metrics — and that is now written down
(statistics-changes §4.3) instead of an unqualified "FWER ≤ α". Fork B (D7) is
ratified and made visible: a verdict may legitimately disagree with the interval
stored beside it, one-directionally, and the pair carries an explicit caveat when
it does — abkit had been in that position under BH since M3 without saying so.
Two load-bearing deltas the design did not have: a scheme is classified
compute-time vs read-time in ONE place with a roster test against the config
literal (the three scattered `!= "benjamini_hochberg"` tests would each have
silently degraded a new scheme to the per-row CI rule), and the readout's
rationale stops saying "CI excludes zero" when the family rule is what decided.

**STAT-1b as built:** `contrasts: all_pairs | vs_control` on the experiment,
with no project default on purpose — the family a surface reads must not depend
on whether that surface resolved one. It is one declaration with two halves: the
divisor falls to `g−1` **and** the treatment-vs-treatment pairs stop being
computed, because loosening the level while still writing those rows is the
false-FWER half of the change. The build's load-bearing delta is the factory
`ExperimentConfig.contrast_pairs()` + its AST gate: four modules each carried
their own `combinations(variants, 2)` (the analyze stage plus the report,
dashboard and notification filters), which is m10's eight-hand-copied-call-sites
shape one milestone later — `notify/dispatch.py` had even predicted the
extraction in a comment.

**STAT-3a as built:** the symmetry premise behind the always-valid transform is
now enforced rather than assumed — `BaseMethod.asymmetric_ci` (default `False`,
nothing declares it, so **no number moved**) makes every SE-by-CI-inversion entry
raise instead of returning the mean half-width over `z` and calling it a standard
error. Two deltas the design did not have: the flag is resolved per **bound
instance**, because STAT-3 ships its interval as an identity-flagged *param* on
`z-test` and a `ClassVar` would answer for the default params — a guard that
cannot fire for the case it exists for; and there are **twelve** entry points, not
eleven, because explore's Tier α open-codes the inversion instead of calling the
helper. An AST gate fails on any new open-coded inversion.

**STAT-3 as built:** `interval: score` on `z-test` — opt-in, identity-bearing,
and it moves **no p-value at all**, because the Farrington–Manning form of
Miettinen–Nurminen makes the statistic at the null the pooled z the p-value
already used. What moves is the pair of bounds: they stop being `effect ± z·σ̂₀`
(a variance frozen at the null, valid nowhere else) and become the set of
contrasts that same statistic does not reject — valid everywhere, asymmetric,
inside `[−1, 1]`, and coherent with the p-value BY CONSTRUCTION on both the
difference and the ratio scale at once. The case for it is imbalance, not
elegance: at a 900/100 holdout the pooled SE is 24% too small, i.e. the legacy
interval is anti-conservative by 2.7× at α = 0.05 and 30× at α = 1e-4 — and it
degrades exactly as this same milestone's corrections shrink α. Three deltas the
design did not have. The numerics: the published closed-form constrained MLE
loses ~1e-12 to cancellation precisely on the sparse tables the construction is
FOR, so it is a seed polished by Newton on the log-likelihood, and the endpoints
come from a fixed-iteration bisection (fixed work, not a tolerance loop, so the
scalar and batch entries are the same code and bit-identical by construction).
The refusal for `sequential.enabled` moved from a mid-run `AsymmetricCIError` to
a **level-2 config error** naming both knobs — the failure was reachable only
after the cohort had been loaded — and the same sentence now refuses the pair at
the explore knob and at its Apply seam. And `abk plan` had to learn the interval
shape too: it sizes on the normal power formula while the analysis inverts the
score statistic, a gap now MEASURED at `C·z²/n_arm` (C = 4.01 at a 5% baseline)
rather than assumed ignorable.

**STAT-3 also amends STAT-3a.** That WP made `abk validate` REFUSE an asymmetric
method's cell, which was the right answer while no method could declare the flag
and the wrong one the moment a method could: the τ² anchor runs unconditionally
at the top of both scoring engines, so the refusal failed *every* cell — the
change-control instrument unable to measure the estimator it exists to certify,
and explore's calibration chip stuck at `uncalibrated` with no command able to
clear it. Such a cell now degrades exactly as a bootstrap one does: fixed columns
scored, no always-valid column, and a note saying which of the two reasons
applied. The generalisable lesson is the one the milestone keeps re-teaching at
each level: **a capability flag's granularity has to match its switch, and the
enumeration of who reads that flag has to be checked rather than recalled** — the
design's "five class-level readers" was right about the five and silently wrong
about the three instance-level readers inside `abk validate`, which were the ones
that mattered.

**STAT-4 as built:** `interval: delta | fieller` on the five MEAN methods —
opt-in, identity-bearing, no default moved. The scope shifted at build time: the
contour said "the relative-z covariance term", but STAT-3 had already answered
that for the z-test (its ratio-scale score interval is the exact analogue rather
than a normal-theory approximation of Fieller), so what remained was
`t-test`/`cuped-t-test`/`ratio-delta` and the paired pair. The defect it closes
is **one-sided**, and getting that right is the whole value: delta's two-sided
coverage is nominal — so the A/A matrix's own FPR column reads 0.0498 against
Fieller's 0.0499 — while its *tails* are 0.0168/0.0327 at a control-mean CV of 5%
and 0.0083/0.0393 at 10%, **independent of the true effect**. Every abkit verdict
is a one-sided claim, so the directional error rate runs at up to 1.6× the
configured one, and an A/A run measures that faithfully and still reports
"calibrated". STAT-2's sign column is the instrument that separates them (0.664
measured against the derivation's predicted 0.659) — the WP it was shipped a
milestone early for. Three deltas the design did not have. The relative p-value
**deliberately becomes the absolute comparison's, bit-for-bit**, which is what
makes the interval and the p-value one decision; an unbounded answer (`g ≥ 1`) is
reported as **missing bounds** with the effect and p-value still on the row —
Gleser–Hwang says a procedure with guaranteed coverage must sometimes decline,
and the disclosed cost is that such a comparison can reject on the absolute scale
without being called a WIN. And the capability flag moved onto the `ParamSpec`
(`asymmetric_values`, plus its sibling `relative_only`), so the whole STAT-3a
consequence set — the level-2 config error, `abk validate`'s omitted always-valid
column, explore's α-tier gap, the `[low, high]` chip — followed with **no new
surface code**. One design sub-task turned out to be already paid: explore's Tier
α, which STAT-4 was told it must fix, is exactly the twelfth entry point STAT-3a
found and guarded a WP earlier.

**The sequential question is answered (D14):** the confidence sequence *does*
extend to score intervals — the always-valid rule is a standardised test with a
variance-dependent `c(V)` in place of `z`, i.e. a critical-value substitution
inside the root-find the new intervals already perform. The investigation instead
surfaced a live hazard: `to_always_valid` **infers** the SE from the CI width
assuming symmetry, unvalidated — in `se_from_ci_length`, entered from eleven
places, seven of them inside the A/A matrix's own scoring and family sweep. (The
design session measured "six", counting only `to_always_valid`; the guard
therefore belongs to the inference helper, and shipped as its own WP **STAT-3a**
before STAT-3 — D17.) ~5–6 sessions, of which the correction layer (STAT-1c,
STAT-1b, STAT-2, STAT-1), the guard (STAT-3a) and the proportion interval
(STAT-3) are done.

### M14 — multi-arm decision layer (bucket B, decisions) → `0.9.0` 📐 designed
An explicit `control:` field (or a validated positional convention);
experiment-level winner rollup on `ExperimentReadout`; treatment-vs-treatment
verdicts; a cross-arm overview in report/explore/dashboard (the 0.1.x
multi-arm UX safe wins fold in here). Pair statistics do not change — this is
the interpretation layer + UI, built on M11's dashboard surface.

**The design session ran 2026-08-05** and produced
[m14-implementation-plan.md](docs/specs/m14-implementation-plan.md) — six WPs
(DEC-1 `control:` → DEC-2 the decision layer → DEC-3 report / DEC-4 the other
three surfaces; DEC-5 `validate`/`plan`/SRM, independent; DEC-6 exit gate +
`0.9.0`), ten decisions, and the posture that makes it verifiable: **M14 moves
no persisted number, no alpha and no verdict `0.8.0` already issues** — it adds
verdicts for pairs that had none (treatment-vs-treatment, which exist only
under `contrasts: all_pairs`) plus a read-time rollup over them, and a two-arm
experiment stays byte-identical on every surface. The winner claim is "leader +
tested separation" off existing rows (D1); a simultaneous best-arm procedure
(MCB) is new statistics and is re-weighed in M15. ~6 WP, ~6 sessions — one over
the contour, because the audit's supporting items (the `abk validate` N-arm
placebo, `abk plan` sizing one contrast of many, SRM with no culprit arm) were
scoped IN.

### M15 — new methods (bucket C, statistics) → `0.10.0` 📐 contour
Student-t (Welch–Satterthwaite), BCa bootstrap, Mann-Whitney, cross-fitted
CUPED/CUPAC, cluster-robust SE — each through the plugin checklist
(`BaseMethod` + `ParamSpec` + dual entry + identity test + A/A through the
matrix; `supports_vectorized` where applicable) and the full change control.
~6 WP, ~6–8 sessions; design session first.

### M16 — owned randomization (opt-in) → `0.11.0` 📐 contour
abkit today only *reads* assignments. An optional deterministic hash-split
module (`unit_id`+salt → arm) for teams without their own assignment system:
cohort generation written through the existing exposures path, the SRM gate as
a sanity check of our own split. **Never a default**; no-copy semantics per
M8. Design session mandatory (boundary questions). ~3–4 sessions.

### M17 — app integration (agentic) → `0.12.0` 📐 contour
The most open-ended piece, fixed as a milestone contour: its design session
decides the form (a read-only MCP server as in dtk? an agentic layer over the
`abk` CLI?) and cuts the WPs. **Embedding a BI tool is explicitly OUT** (owner
decision, 2026-08-02): abkit stays BI-agnostic — `_ab_results` is the contract
and operators point their own Grafana/Metabase/Superset/Lightdash at it. Parked
items are re-evaluated here (other DBs — REPORT #15). Estimate ~4+ sessions,
conditional until the design session.

> **M15–M17 have no detailed WP breakdowns yet — each opens with its own
> design session** (verification pass → WP breakdown → design doc in
> `docs/specs/`) before any implementation, exactly like the M7–M14 docs were
> produced. (M13's ran on 2026-08-03 and shipped as
> [m13-implementation-plan.md](docs/specs/m13-implementation-plan.md); M14's on
> 2026-08-05 as [m14-implementation-plan.md](docs/specs/m14-implementation-plan.md).)

## Post-baseline hardening (multi-arm UX + stats-core), tiered by version

> **Status (2026-07-18): absorbed into the polish track above** (maintainer
> decision — everything below is scheduled, nothing dropped): the Now-bug →
> M7 WP0; the "0.1.x safe wins" stats hot path → M7 WP1 and the multi-arm UX
> wins → M14; the "1.x versioned" tier → M13 (stats core) + M14 (decision
> layer); the v2-named methods → M15. Kept below as the source inventory.

From the 2026-07-07 audits ([docs/research/2026-07-multi-arm-and-stats-core/](docs/research/2026-07-multi-arm-and-stats-core/)).
Both baselines are **sound**: multi-arm (>2 groups) is correct end-to-end statistically
(all-pairwise compute, joint K-way SRM, `C(N,2)×metrics` Bonferroni, per-pair persistence);
the stats core is minimal-dep, vectorized, and scipy-delegated. What follows is **hardening**,
biased to *ship the MVP fast, improve in 1.x*. The baseline locks **numeric results** (golden
rel-1e-9), not the implementation or correctness-forever — byte-identical wins are free; number
changes are legitimate as a versioned deviation (`ALGORITHM_VERSION` + `statistics-changes.md` +
A/A revalidation).

- **Now / 0.1.0 (MVP, no numbers move):**
  - Fix `abk explore` Review mode showing only the **first** arm's verdict per metric
    (`.find` → map) — the one near-decision multi-arm bug (`web/src/explore/explore.ts:1516`).
  - **Document** the known multi-arm limitations honestly (control-vs-each readout, no
    experiment-level winner, `abk plan` first-pair sizing, `abk validate` two-arm placebo).
- **0.1.x safe wins (byte-identical, no version bump — opportunistic):**
  - Stats hot path: `ndtri/ndtr` swap (~60×) + lazy `statsmodels` import + lazy never-read
    `effect_distribution` (~250× on the `validate`/`explore` path); parametric `_finalize`
    helper + registry-parametrized contract/completeness tests + double-compute dedup.
  - Multi-arm: B-vs-C (non-control) **verdict card** + on-page asymmetry note; per-pair
    labels in `abk run --report` text; explore `activePair` memory.
- **1.x (versioned statistical improvements — ALGORITHM_VERSION + A/A; opt-in first):**
  - **Holm** (step-down) over Bonferroni (strict power gain, same FWER); z-test **unpooled**
    CI SE; restore the **relative-z covariance** term; **uniform ddof=1**; **Agresti-Caffo /
    Wilson** proportion CIs; **main-tier `metrics_count=1` FWER** fix. *(Student-t /
    Welch–Satterthwaite, BCa bootstrap, cross-fitted CUPED/CUPAC, cluster-robust SE are the
    same items already named under v2 below — promote per demand.)*
  - Multi-arm decision layer: **experiment-level winner rollup** on `ExperimentReadout` +
    treatment-vs-treatment verdicts + a cross-arm overview; an explicit **`control:`** field
    (or validate the positional convention).
- **v2 / bets:** incremental Chan-merge cumulative recompute (the real warehouse-cost lever;
  see below); drop-`statsmodels` scipy reimplementation (or `[power]` optional extra);
  bootstrap `PCG64→SFC64`.

## v2 (deferred, profiling-gated)

> **Status (2026-07-18): promoted into the polish track above.** The
> incremental engine + `abk verify-incremental` + cost observability → **M9**
> (the flag will NOT be named `--profile` — that collides with the DB-profile
> selector); the named methods → **M15**; owned randomization → **M16**; app
> integration → **M17**; other-DB support stays parked and is re-evaluated in
> M17. Kept below as the source inventory.

- Python incremental accumulator + array-cache + quantile sketches +
  `incremental_backend`; `abk verify-incremental` gate (whole-series reconciliation);
  `run --profile` observability to trigger it on a concrete cost threshold.
- Cross-fitted CUPED/CUPAC, Student-t (Welch–Satterthwaite), BCa bootstrap,
  Mann-Whitney, cluster-robust SE; full PG/MySQL incremental parity (if needed);
  optional owned randomization; app integration (agentic analysis — no embedded BI).

## Backlog / open items for the user
Tracked in the RU initiation spec ([docs/ru/project-initiation-spec.md](docs/ru/project-initiation-spec.md))
— covariate-window choice, v2 trigger threshold, docs domain confirmation, SRM
`expected_split` source, guardrail multiplicity handling.

### 📋 Website parity with detectkit's landing refresh (maintainer request, 2026-08-03)

The donor's site moved ahead and abkit's has not followed. The reference is
detectkit's **#151** (`67d9c24`, "feat(landing): dark/light theme toggle, honest
quickstart, MCP + comparison + hybrid sections") — landing-only, no library
change, no version bump. Scope here is the parts that apply to abkit; study the
donor diff first rather than re-deriving it.

**1. Theme toggle everywhere, the landing included.** The docs already have
both themes — `website/src/styles/brand.css` defines `:root` (dark, the
Starlight default) and `:root[data-theme='light']`, and Starlight ships the
switch. The **landing does not**: `website/src/pages/index.astro` (693 lines) +
`website/src/styles/landing.css` (1654) carry no `data-theme` at all, so the
marketing page is single-theme while the docs one link away is not. The donor
paid ~1200 lines for this (299 in `index.astro`, 893 in `landing.css`) and
recorded two traps worth stealing: **no-flash init** (read `localStorage`, fall
back to the OS preference, before first paint) and **elements that must NOT
invert** — the donor pinned its CTA band and one status chip to
theme-independent values. abkit's analogue is the report/explore screenshot
surface and the five verdict tokens (`docs/design/brand-tokens.md`): a WIN green
that flips with the page theme stops being the brand colour.

**2. Live social proof in the hero — stars, and something honest beside them.**
The donor fetches at **build time** (`api.github.com/repos/...` →
`stargazers_count`, `pypistats.org/api/packages/<pkg>/recent` → `last_month`),
formats compactly, and **omits a stat entirely when its source is unreachable**
— no fake fallback, so the hero can never show a made-up number. That discipline
carries over verbatim.

**The download figure does not.** The donor's own page shows **~12k downloads/mo
against 3 stars**, and that ratio is the tell: a PyPI download count is not a
count of humans. It includes mirrors, scrapers, dependency resolvers and CI —
and a project whose own CI installs it across three Python versions on every PR
is a meaningful share of its own "audience". Publishing it as social proof is
the kind of number this project refuses to print anywhere else.

So this item is **not** "port the badge". It is:

- **Measure the composition before choosing a metric.** `pypistats`' public API
  exposes no installer breakdown; the real source is PyPI's BigQuery dataset
  (`bigquery-public-data.pypi.file_downloads`, with `details.installer.name`),
  which can separate `pip` from `bandersnatch`/`requests`/unknown. That is a
  one-off investigation, not a build-time dependency — a static site must not
  need GCP credentials to render.
- **Then decide what the page may claim.** Options, in the order they should be
  argued: show **stars only** (unforgeable by CI); show a **verifiable
  project fact** instead (releases, tests, the date the first version shipped);
  or keep a download figure **only if** it can be labelled precisely enough to
  be true — and the label has to survive an operator asking "so how many people
  actually use this?".
- **Whatever ships, the page states what the number is.** The donor already
  links its figure to `pypistats.org` for verification; that part is right and
  stays.

**3. Everything else in the donor diff is judged, not copied.** Its new sections
(a 60-second DuckDB quickstart backed by a real example project, an `dtk mcp`
section, a competitor-comparison matrix, hybrid-mode pricing) map onto abkit
unevenly — abkit has no MCP surface and no hybrid mode, but it does have a
`abk init` scaffold that could back an equally honest quickstart, and the
comparison matrix is a marketing decision the maintainer should make, not an
inherited one.

**Not a milestone**: website-only, no library change, no version bump (the site
auto-deploys on a `website/**` merge to `main`). Sized as one session, or two if
the download investigation turns into a real BigQuery pass.

### 📋 CI + test-suite audit — its own `ultracode` pass (maintainer request, 2026-08-02)

**The suite has grown to 3118 tests and the feedback loop is now the slowest
thing about contributing — that is not acceptable and it gets its own session.**
Measured on the NTF-1 PR (run 30768980129): `Test (Python 3.10)` **6m03s**,
3.11 **5m14s**, 3.12 **5m38s**; the whole workflow ≈ 6 min wall-clock because
the ten jobs run in parallel, and the local full suite is ≈ 6 min too. Every WP
in this track pays that twice (locally, then in CI), and a release cut pays it
again.

**Run it as a dedicated `/code-review ultra` (multi-agent) pass**, because the
question is *not* "which flag makes pytest faster" — it is a codebase-wide
audit that a single reading will not cover honestly:

- **Where does the time actually go?** `--durations` over the whole suite, per
  directory. Suspects worth confirming rather than assuming: the e2e gates each
  scaffold a project and drive real CLI invocations; `tests/validate/` runs
  real resampling engines; coverage instrumentation is on by default
  (`addopts` in `pyproject.toml` carries `--cov=abkit --cov-report=html`) and
  CI already disables it on 3.10/3.11 but not 3.12.
- **Which tests earn their runtime?** Duplicate coverage across the fake-manager
  flavours, oversized parametrize matrices, `sleep`-based waits, fixtures
  rebuilt per test that could be session-scoped. The track's own rule applies
  in reverse here: a test that cannot fail is worse than no test — and a test
  that costs 20 s to re-prove what a 20 ms one already pins is close behind.
- **Parallelism.** `pytest-xdist` is not a dependency today; the blockers are
  the ones this repo actually has (the fake DB is in-memory per test, but the
  e2e legs `chdir`, and warning capture is thread-scoped for a reason —
  `utils/warn_scope.py`). Decide it with evidence, not by adding `-n auto` and
  hoping.
- **CI shape.** Caching (`pip`/`npm` are re-resolved every run), whether the
  3.10/3.11 legs need the FULL suite or only the parts version-sensitive to
  them, and whether the ClickHouse testcontainers leg can start earlier.

**Constraint that must not bend:** speed is bought from *duplication and
waste*, never from coverage. No parity gate, exit gate or golden may be
dropped or sampled to make a number look better — the milestones that moved no
statistical number did so because those gates ran every time.

### Tooling debt (non-blocking; ~~discovered M3 WP2~~ root-caused + partly fixed M6 WP1)
- **~~`mypy` fails on clean HEAD~~ — ROOT-CAUSED + FIXED (M6 WP1).** The real cause
  was **not** numpy: `abkit/config/metric_config.py:48` held a stray comment
  `# type: (required, optional)` that mypy parsed as a **PEP-484 type comment**;
  `(required, optional)` is invalid type syntax → `Invalid syntax [syntax]`, and mypy
  **bailed before checking anything else** (hence "errors prevented further checking"
  and the mis-anchor). The numpy 2.5 PEP-695 stub error was real but *secondary* — it
  only surfaced once the parser got past the comment, and `python_version = "3.12"`
  clears it (mypy 1.10.0 parses the stubs fine at 3.12 — verified). WP1 fixes: reword
  the comment; `python_version` → `3.12`; add `yaml.*` to `ignore_missing_imports`.
  **Now mypy RUNS TO COMPLETION and reports ~124 real strict-mode errors** (41 arg-type,
  38 operator, 28 no-untyped-def, …; ~half in `tuning/recompute.py` + `pipeline/readout.py`,
  mostly `X | None` Optional-handling that the runtime guards but mypy can't prove). CI
  keeps `mypy abkit` `continue-on-error: true` — clearing the 124 is **tracked debt**, held
  separate from WP1 because the fixes live in numeric hot paths (a careless narrowing could
  change a number — the cardinal sin). The pre-commit `mypy` hook stays red until then.
- **`black` version drift pre-commit ↔ CI — FIXED (M6 WP1).** `[dev]` now pins
  `black==24.4.2` and `mypy==1.10.0` to match `.pre-commit-config.yaml` exactly, so CI and
  local pre-commit can no longer diverge on formatting/type results. (`abkit/` verified clean
  under both black 24.4.2 and 26.x, so the pin caused zero reformat churn.) Minor residual:
  `ruff` has the same latent drift shape (pre-commit `v0.4.8` vs unpinned `[dev]`) — left as
  a small follow-up since it was not a reported debt and abkit is clean under both.
