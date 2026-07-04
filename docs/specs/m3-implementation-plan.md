# M3 Implementation Plan — the explore cockpit + the self-contained readout + decision logic

> **Working plan, not a design contract.** Synthesized 2026-07-03 from the specs
> plus a 7-extraction survey (data-contract-and-reporting, cli-and-dx + the §4 port
> map, the calibration/quorum/ROADMAP bindings, the detectkit `tuning` and
> `reporting` donor packages, the as-built abkit seams, and the house plan format),
> then adversarially critiqued by two independent verifiers (completeness vs the
> specs; porting fidelity vs the real donor + abkit code) — 1 blocker, 9 major,
> 15 minor findings, all applied below before commit. The specs stay canonical;
> where this plan settles an open point that amends a spec (D1–D12 note which)
> the spec is amended in the same PR. Updated as work packages land; archive at
> M3 close.
>
> **Contradiction audit across the extractions:** no reader-vs-reader conflicts.
> One spec↔code tension: cli-and-dx.md §2 promises knob changes with "no DB
> round-trip", but `_ab_results` persists no covariate second moments, no cross-
> moments, and no bootstrap replicates (tables.py:182–189; result.py drops
> `effect_distribution`), so CUPED-param and bootstrap recompute are provably
> impossible from stored rows — settled in **D1** with a spec amendment. One
> spec↔schema drift: aa-false-positive-matrix.md §7's `_ab_aa_runs` column list
> is a subset-with-renames of the shipped model (tables.py:224–263) — the chip is
> built against the **as-built** schema (D3). Two spec↔stats-core gaps: the §5.1
> side rail names a one/two-sided knob and winsorization, neither of which exists
> as a method param anywhere in `abkit.stats` — deferred under change control
> (**D12**), never faked in the UI.

Sources: data-contract-and-reporting.md §1–§7, cli-and-dx.md §1–§2, architecture.md §4–§5, §8–§9, aa-false-positive-matrix.md §3–§5, §7, quorum-review.md (the peeking + DX must-fixes), declarative-config.md §3, ROADMAP M3 + the M2 deferral list, the two detectkit donor surveys (`tuning/`, `reporting/`), the as-built seam map (tables.py, `_results.py`, enrich.py, analyze.py, `stats/base.py`, `stats/effects.py`, driver.py, `_output.py`), m2-implementation-plan.md as the format donor, and the two critique reports.

Conventions: `⟲` = port near-verbatim, `A` = adapt, `RW` = rewrite on donor skeleton, `NEW` = no donor. All abkit paths relative to repo root; donor paths relative to `/home/aleksei/wsl_analytics/detektkit`. Every WP is one reviewable PR (~300–900 net LOC target; donor-port WPs may run larger, as in M2). One conventional commit per WP.

---

## 1. Work packages in strict dependency order

### WP1 — `pipeline/readout.py` decision core + readout config fields (pure logic, NEW)

**Goal:** the WIN/LOSE/FLAT/INCONCLUSIVE verdict engine as a pure, replayable module over persisted `_ab_results` rows — no DB, no rendering — plus the three config fields the qualitative spec left homeless.

| Source | Target | Verdict |
|---|---|---|
| — (architecture.md §8: "Output is a readout/decision, not a paged alert — no cooldown/recovery/quorum") | `abkit/pipeline/readout.py` | **NEW** |
| — | `abkit/config/experiment_config.py` (extend `ComparisonConfig` + a `readout:` block) | **NEW** |
| `detectkit/reporting/builder.py:344–372` (alert **replay** stance: reconstruct in memory over stored rows, no writes) | replay pattern only | A (pattern, not code) |

**Hotspots (from the data-contract + seams surveys):**
- Inputs are all on-row: `is_main_metric`, `is_guardrail`, `reject`, `effect`, bounds, `srm_flag`/`decision_blocked`, `insufficient_data`, `is_horizon`, `ci_kind`, `alpha`, `mde_1/2` (enrich.py:41–136; tables.py:146–221). The series arrives from `load_results()` already deduped, `end_ts`-ascending (`_results.py:150–182`).
- Verdict algorithm per (main-metric, control-vs-treatment pair), evaluated at the latest cutoff — the full rules and thresholds are **settled in D5**; implement exactly that: SRM hard gate first (data-contract §1), pre-horizon withholding (`is_horizon` + `ci_kind` — unconditional in M3, all rows `"fixed"`, enrich.py:114; ROADMAP M2 deferral), elapsed-time stabilization window (never look count, §4), FLAT per the D5(b) MDE rules, guardrail regression per D5(c).
- **The NULL-MDE reality (critique finding):** `calculate_mde` defaults to `False` (base.py:116–121) so most rows persist NULL `mde_1/2` (enrich.py:105–106), and `ratio-delta` has no MDE capability at all (ratio_delta.py:61 — `param_specs = (TEST_TYPE_PARAM,)`). Per D5(b): the readout computes a **read-time MDE fallback** for t-test/z-test rows from on-row `{value, std, size}` via `stats/power.py` (cross-checked equal to `calculate_mde: true` rows at rel-1e-9); NULL-and-no-fallback (ratio-delta, bootstrap) ⇒ FLAT unreachable, with the honest rationale ("mde not computed — set `calculate_mde: true`" where the method supports it).
- **BH is read-time**: when `correction: benjamini_hochberg`, compute-time rows carry raw alpha (analyze.py:74–77) — `readout.py` must apply `stats.correction.benjamini_hochberg` across the latest cutoff's comparisons itself (seam map §5). This deliberately pulls the ROADMAP M5 "Benjamini-Hochberg read-time" line forward (an M3 readout ignoring an M2-accepted config value would verdict at the wrong alpha); annotate ROADMAP in this PR; the composed-FDR *empirical validation* stays M4/M5 (aa-fpr §3).
- Every verdict carries a machine-readable `rationale: list[str]` + `caveats: list[str]` (the §5.2 "verdict with its rationale"; the sub-day "covers X% of a weekly cycle" caveat, §4).
- Demoted rows (`insufficient_data=1`) have NULL test columns but real sizes (analyze.py:150–166) — the stabilization scan must skip them as gaps, never treat NULL as zero.
- Effective alphas are reproducible offline via `effective_alphas`/`comparison_alpha` (analyze.py:58–83, exported) — the readout compares against the **stored per-row alpha** and never re-corrects Bonferroni-tier rows.
- Config additions (spec amendment, same PR — declarative-config + data-contract §1): `ComparisonConfig.min_effect: float | None = None` (**in the units of the persisted `effect` for that comparison** — test_type-dependent, base.py:109–115), `ComparisonConfig.desired_direction: Literal["increase","decrease"] = "increase"`, experiment-level `readout: {stabilization_days: float = 7.0, guardrail_policy: Literal["block","warn"] = "block"}` (the guardrail policy is owner-ratified: `block` caps WIN at INCONCLUSIVE, `warn` keeps WIN with a mandatory loud caveat — D5(c)). Validate in the L1/L2 matrix; all four are readout-time only — **never** method params, never enter `method_config_id`.

**Tests:** new `tests/pipeline/test_readout.py` — known-answer verdict tables (one fixture row-set per verdict × per refusal reason); pre-horizon refusal on `is_horizon=0`; SRM gate forces INCONCLUSIVE with the SRM rationale; stabilization over `elapsed_days` with a deliberately irregular cutoff grid (proves look-count independence); CI-re-crossing series is not WIN (§1); `min_effect` absent ⇒ FLAT unreachable; NULL-mde branch + read-time-MDE equivalence vs `calculate_mde: true` rows; ratio-delta FLAT-unreachable rationale; guardrail regression under **both** `guardrail_policy` values (`block` caps WIN at INCONCLUSIVE; `warn` keeps WIN and the caveat is asserted present); BH read-time rescoring vs a hand-computed BH set; demoted-row gaps; multi-arm per-pair verdicts; config-field validation additions in `tests/config/`.

**DoD:** `readout.evaluate(experiment_config, rows) -> ExperimentReadout` is pure, deterministic, and covers every D5 branch; spec amendments + the ROADMAP BH annotation merged.

**Must-fixes discharged:** *peeking is the product* (quorum-review) — the refusal half: `readout` refuses pre-horizon WIN/LOSE (extended to FLAT per D5) unless `sequential.enabled`.

---

### WP2 — `reporting/builder.py` — the experiment-primary payload (the "payload swapped" half of the ⟲)

**Goal:** one JSON-serializable payload per **experiment** — the shared contract consumed by both the readout renderer (WP3) and the explore shell (WP6/WP7).

| Source | Target | Verdict |
|---|---|---|
| `detectkit/reporting/builder.py` (391 ln) | `abkit/reporting/builder.py` | **RW** on donor skeleton — architecture.md §4 "renderer verbatim; **payload swapped**"; metric-primary → experiment-primary is the contributing.md conscious-reshape case |
| `website/src/scripts/report/payload.ts:1–10` (lockstep-contract stance) | `web/src/shared/payload.ts` (WP3 creates the tree; WP2 lands the Python side + a schema doc) | A |
| `builder.py:124–156` (`_ms`, `_num_or_none`) | `abkit/reporting/builder.py` helpers | ⟲ (`_parse_seasonality` skipped, no analogue) |
| `builder.py:40–47, 65–121` (detector warm-up defaults, `_effective_start_index`), alert-orchestrator replay internals, `_direction_from_metadata` | — | — skip (no abkit analogue; the *concept* of warm-up dimming maps to pre-horizon) |

**Hotspots (from the reporting survey + seams):**
- Read path: `load_project_context` → `select_experiments` → `InternalTablesManager.load_results(exp, metric, method_config_id)` per configured comparison (comparisons → `comparison.method.method_config_id`, method_config.py:51–60); header metadata from config YAML (the truth) with `get_experiment()` informational only (seam map §5). Guard reads on a never-run project (missing tables) with a friendly skip, not `ensure_tables()`.
- **Payload schema is D6** — implement exactly: version key, terse point keys, `verdicts` block from WP1, top-level `srm` block, a `calibration` block that is `null` in M3 but **shaped for M4** (D6), endpoints (`save_url`, `recompute_url`, …) `None` until a server injects them (donor payload.py:404–426 pattern).
- **The look counter has a producer here (critique finding):** derive `look: {n, planned}` from the one-enumeration `core/period_planner` grid through the horizon — the same single grid function the validator and planner use (the M2 R1 invariant); `n` = count of non-demoted persisted cutoffs, `planned` = grid length.
- **Column projection, not `SELECT *` bake**: `metric_query`/`metric_rendered_query` ride every row (tables.py:212–213) — project them out; dedupe provenance to one entry per series.
- NaN discipline: every numeric through `_num_or_none` (H5 zero-denominator NaNs must become null — donor gotcha); `warnings`/`diagnostics` `json.loads`-ed into the payload.
- Empty-experiment contract: all keys present, zeroed summary (donor builder.py:247–260) — the renderer never sees a missing key.
- `generated_at` caller-supplied preformatted string (builder purity for tests, donor builder.py:235).
- Size control: donor caps at 1500 points (builder.py:33–34); abkit cap = `metrics × pairs × cutoffs` point budget with tail-window truncation + a payload warning when clipped.

**Tests:** rewrite donor `test_report.py` payload cases against `tests/_helpers/fake_db.py` — payload shape/keys vs a seeded fake `_ab_results`; per-series grouping by `method_config_id`; NaN→null; demoted-row null pass-through; empty-experiment contract; provenance dedupe + projection (assert rendered SQL absent from payload); verdict block equals WP1 output; the `look` block at a sub-day-cadence fixture; multi-metric/multi-pair ordering stable (`json_dumps_sorted` determinism).

**DoD:** `build_report_payload(experiment, tables, *, project=None, metric_configs=None, generated_at=None, start=None, end=None, max_points=REPORT_POINT_BUDGET) -> dict` matches the D6 schema doc byte-stably on the fake manager. *(As-built signature — `project` resolves the readout correction + names the payload, `metric_configs` supplies metric descriptions per D6, `max_points` bounds the point budget; all keyword-only with sane defaults.)*

**Must-fixes discharged:** none directly; it is the contract both must-fix surfaces render.

---

### WP3 — `reporting/html_report.py` + the `web/` toolchain + `abk run --report`

**Goal:** the self-contained offline HTML readout — baked payload + framework-free JS — emitted best-effort from `abk run`.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/reporting/html_report.py` (84 ln) | `abkit/reporting/html_report.py` | ⟲ (rename `__DTK_*`→`__ABK_*`, `dtk-report`→`abk-report`; **drop the Google-Fonts `<link>`s** — abkit's invariant is stricter) |
| `website/src/scripts/report/report.ts` + `core/canvas.ts` primitives | `web/src/report/` (+ `web/src/shared/chart.ts`) | **RW** on donor skeleton — canvas chart, decimation, band fill, warm-up overlay port; series semantics reshaped |
| `website/scripts/gen-report-bundle.mjs` (+ `gen-tune-bundle.mjs`) | `web/build.mjs` | A (esbuild IIFE es2019; keeps the `__ABK_REPORT__`-presence assertion, gen-report-bundle.mjs:48–51) |
| `detectkit/reporting/assets/report.js` (committed artifact) | `abkit/reporting/assets/report.js` | regenerated, committed (D7) |
| `detectkit/cli/main.py:139–150` (the `--report` tri-state flag: `is_flag=False, flag_value="", default=None`) + `detectkit/cli/commands/run.py:255–309` (`_resolve_report_path`, `emit_metric_report`) | `abkit/cli/main.py` + `abkit/cli/commands/run.py` additions | A (per-**experiment**, `reports/<experiment>.html`) |

**Hotspots (from the reporting survey):**
- Template mechanics verbatim: `str.replace` never `.format`; `__REPORT_JS__` substituted **last**; `html.escape` on the title; data-URI favicon; `<meta charset="utf-8">`; bundle read via `importlib.resources.files("abkit.reporting")/"assets"/"report.js"` + the 2-file packaging contract (`pyproject.toml` package-data glob + `MANIFEST.in`) — abkit already has the pattern for `loaders/templates/abkit_assignment.jinja`.
- **Harden the donor's `</script>` hole**: escape `</` → `<\/` when baking the payload JSON (verified: donor `json_dumps_sorted` does NOT escape it — the donor gets away with trusted YAML; we don't rely on it).
- Renderer content (data-contract §5.2): verdict banner + rationale; the stabilization chart (effect + CI band vs `elapsed_days`, zero line, `avg_group_size` derived client-side per §2); variant means/lift; MDE/power view; p-value-vs-alpha view; results/audit table (the four §3 views); **red SRM gate chip**; **the calibration/A-A slot rendering its empty state** ("uncalibrated — run `abk validate` (M4)"), consuming the D6 calibration block tolerantly.
- Peeking-honesty rendering (§4): pre-horizon fixed CIs **dashed/de-emphasized**; `insufficient_data` segments **greyed with counts+SRM only**; the horizon marker; the weekly-cycle caveat string from WP1 rendered under the verdict. These renderings carry **stable machine-checkable markers** (`abk-prehorizon`, `abk-insufficient`, `abk-srm-fail` CSS classes/data-attrs) so WP10 and the CI bundle gate can assert them (critique finding — the must-fix rendering half needs automated proof).
- Renderer self-defense verbatim: try/catch mount (donor html_report.py:56–60 — it lives in the Python template), local HTML escaper on every payload string, CSS injected once under the `abk-report` root class with a sentinel attr (report.ts:844) — this *is* the embeddability invariant (CLAUDE.md invariant 6).
- CLI: the tri-state `--report` flag; emitted per experiment after its pipeline inside try/except — "never fail the run on a report" (donor run.py:238–252); skip-with-message on empty payload; cyan `│ Report → <relpath>` line in the house `_output.py` style. Report emission happens even when zero cutoffs were pending (the re-run-to-report path, D8). The cli-and-dx §1 `--steps … readout` token is **superseded by `--report`** — amend the spec's run row in this PR (D8); no READOUT `PipelineStep` is added.
- CI freshness gate for the committed bundle: a node job rebuilds `web/` and diffs against `abkit/*/assets/*.js`, and asserts the peeking-marker classes are present in the built bundles (donor gotcha: "the committed asset can silently drift").

**Tests:** port `test_report.py`'s self-containment gate — no surviving placeholder, `__ABK_REPORT__` + mount present; the `</`-escaping case (payload containing `</script>` produces a parseable document); path-convention cases for `--report`/`--report DIR`/`--report file.html`; best-effort emission (builder raising ⇒ yellow skip, run still exits 0 when the pipeline succeeded); utf-8 round-trip; bundle-packaging test via `importlib.resources` on an installed wheel layout (mirror the jinja-template precedent). JS behavior is covered by the bundle-marker assertions + the WP10 e2e (donor stance: the bundle is an opaque committed asset, not Python-unit-tested).

**DoD:** `abk run --report` on the `abk init` example writes one offline HTML that opens file:// with zero network requests and shows verdict, stabilization chart, SRM chip, and the calibration empty-state.

**Must-fixes discharged:** *SRM loud* — the HTML-report half of "HTML report & explore: a red SRM gate chip" (§6); *peeking is the product* — the "not peeking-valid" rendering half (§4).

---

### WP4 — `tuning/recompute.py` — the explore recompute engine (NEW; can start after WP1, parallel to WP2/WP3)

**Goal:** the Python-side live-recompute service: reconstruct suffstats from persisted rows where exact, hold a bounded per-unit session cache for the rest, hash the live knob state, and answer one knob-change request in milliseconds — the single source of truth the spec demands ("no JS stats fork", cli-and-dx §2).

| Source | Target | Verdict |
|---|---|---|
| — (the donor recomputes **in JS** via a parity-checked TS detector port — the tuning survey flags this as "the biggest divergence"; forbidden here by the spec's "no JS stats fork") | `abkit/tuning/recompute.py` (+ `abkit/tuning/session.py` for the cache) | **NEW** |
| `abkit/compute/recompute_backend.py:102–140` (`load_cutoff`), `abkit/pipeline/analyze.py` (`analyze_cutoff`, seed derivation :171–181) | reused, not copied | — (in-tree dependency) |
| `detectkit/tuning/payload.py:30–51` (`_TUNE_COMPUTE_BUDGET` window-sizing idea) | cache-budget arithmetic | A (idea only) |

**Hotspots (from the seams survey §2 — the exactness table, corrected by the fidelity critique):**
- **Tier E (exact from rows, whole grid, zero load):**
  - t-test — `SufficientStats(n=size_i, mean=value_i, m2=std_i²·size_i)` (ttest.py:81–105; samples.py:261–288).
  - z-test — **invert `nobs` from the persisted SE, never from `size_i`** (the critique **blocker**: `size_i` is the one-row-per-unit count, enrich.py:95–96, while the z-test ran on summed `nobs`, analyze.py:100–104 — equal only when every unit contributes one trial): `nobs = value_i·(1−value_i)/std_i²` (persisted `std_i` is `sqrt(p(1−p)/nobs)`, samples.py:144), `count = value_i·nobs`; degenerate `p∈{0,1}`/`std=0` rows route to Tier α/S.
  - ratio-delta — the exact surrogate `RatioSufficientStats(n=size_i, mean_num=value_i, m2_num=std_i²·size_i, mean_den=1, m2_den=0, c_nd=0)` (ratio_delta.py:49–54, 93–113).
  - All existing knobs of these families recompute across every persisted cutoff at rel-1e-9.
- **Tier α (alpha-inversion, all parametric incl. CUPED):** `se = (right−left)/(2·z_{1−α/2})` → `sps.norm(effect, se)` → new-α CI + the α-independent p-value (effects.py:94–99; ztest.py:84–88). NULLed/demoted rows pass through untouched.
- **Tier S (per-unit session cache):** at session start, run `RecomputeBackend.load_cutoff` for the **latest** cutoff (plus older cutoffs while `units × cutoffs × arms` stays under `_EXPLORE_CACHE_BUDGET ≈ 2×10⁷` values); keep raw `Sample`s (+ covariate when the persisted method configured one, + the stratum column the metric SQL emits). Enables bootstrap knobs (`n_samples`, `stat`, `pvalue_kind`), the stratify **toggle** + `weight_method`, and CUPED **on→off** — via `from_samples`. Bootstrap seed re-derived per the persisted-row convention `derive_seed(exp, metric, name_1, name_2, end_ts, n_samples)` (analyze.py:171–181; rng.py:24–34) so untouched knobs reproduce stored rows byte-exactly under D11's canonical order.
- **Tier R (explicit reload):** CUPED **off→on** from a non-CUPED series (the covariate is only in the cache when the persisted method configured a lookback — recompute_backend.py:112–117; critique finding), `covariate_lookback` change (a new pre-period render — declarative-config §3 as amended), and analysis-unit change (**preview-only**: `unit_key` lives in metric YAML which Apply never touches, and must match the experiment — metric_config.py:82–84; badge it so). These are marked ↻ in the rail and go through a serialized `/reload` server action (WP6), never a silent per-knob warehouse hit.
- **Not in the knob surface (D12):** one/two-sided and winsorization — no such method params exist in the stats core (hardcoded `alternative="two-sided"` in power.py; zero winsor hits in `abkit/`); the rail is auto-derived from `param_specs`, so they cannot appear without a change-controlled stats-core addition. Stratum-**key** changes are a metric-YAML/SQL edit, out of scope for knobs.
- **Canonical unit order (D11, applied here):** `metric_loader` gains a canonical sort of per-unit arrays by unit key (and the session cache preserves it) so bootstrap replicates — order-dependent by construction — are reproducible across warehouse re-reads (ClickHouse guarantees no result order; the critique showed the byte-equality DoD was fake-backend-only without this).
- The engine returns, per knob state: per-cutoff `{e, lo, hi, p, rj}` split into `exact` / `approx` / `baseline` segments (D1), the chip values (lift, CI half-width, p, power via `stats/power.py`), the live `method_config_id` (hashed **only through the bound probe** — `MethodConfig.method_config_id`, method_config.py:51–60; base.py:207–226 — never a second hashing path), and the calibration lookup result (D3) via `find_calibration(rows, metric, method_config_id, alpha)` — one function M4 reuses.
- Knob panel metadata is auto-derived: `get_method_class(name).param_specs` (base.py:35–104; registry.py:92–107) + identity flags — nothing special-cases a method name (invariant 3); `QuarantinedMethodError` is surfaced verbatim, never swallowed (registry.py:96–99).
- Budget-resolution seam for the chip: metric-override (future) → project `aa_fpr_budget` (project_config.py:93–109) → built-in `α × 1.5` (aa-fpr §4.1) — one resolver function, not a hardcode.
- Requests carry a monotonically increasing id so WP6 can drop stale computes before they start (the donor's worker terminate-respawn discipline, re-expressed server-side).
- Thread discipline: session load runs on the main thread with one manager before serving; per-knob recompute touches **no DB** (driver.py:250–268 — DB-API connections are not thread-safe); `/reload` creates its own manager inside the serialized handler.

**Tests:** new `tests/tuning/test_recompute.py` — **golden round-trip**: pipeline a fixture through `analyze_cutoff` → persist via fake_db → reconstruct → recompute with unchanged knobs → equal `TestResult.to_dict()` at rel-1e-9 for t/z/ratio-delta and **byte-equal bootstrap** via re-derived seeds from the cache; the z-test round-trip on a fixture with per-unit `nobs > 1` (the blocker's regression test); an order-permutation test (a shuffled warehouse read still reproduces persisted rows after the canonical sort); alpha-inversion vs direct `create_method(alpha=…)` for all parametric families incl. CUPED; CUPED on→off vs off→on tier routing; tier classification table per knob per family; cache-budget clamping + degraded suffstats-only mode (bootstrap knobs disabled with a reason string); `method_config_id` live hash equals `MethodConfig.method_config_id` for every knob permutation; calibration lookup states incl. alpha-mismatch and `status='failed'` rows not counting; quarantine surfacing.

**DoD:** every knob derivable from the shipped `param_specs` (plus experiment-level alpha/correction) is classified E/α/S/R with tests; unchanged knobs reproduce persisted rows exactly; D11 and D12 spec/CHANGELOG notes merged.

**Must-fixes discharged:** *calibration-in-explore* — the lookup/keying half (chip keyed to the *current* knob combination incl. alpha, aa-fpr §5).

---

### WP5 — `tuning/config_writer.py` — Apply, `.history`, orphan detection (parallel to WP2–WP4 after WP1)

**Goal:** the only mutation seam: validate → archive → re-emit the **experiment YAML**, with the orphaning consequence computed and surfaced before anything is written.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/tuning/config_writer.py` (253 ln) | `abkit/tuning/config_writer.py` | A — order (validate→archive→re-emit), verbatim-bytes archive, provenance header, slot-merge discipline all kept; detector slots → per-comparison `method` blocks |
| `config_writer.py:33` `_TUNABLE_TYPES` hardcoded set | registry-derived tunability | **RW** (invariant 3: derive from the plugin registry) |
| `config_writer.py:39, 150–152` `_EXECUTION_PARAMS` carry-over | identity-**excluded** param carry-over (`seed`, `max_block_bytes` — base.py:175–195) | A |
| — (donor has **no orphan detection** — "must be built new, not ported") | orphan diff + warning in the Apply result | **NEW** |

**Hotspots (from the tuning survey + seams):**
- Apply targets **one file**: the experiment YAML (method lives on `ComparisonConfig`, experiment_config.py:150–165, 204; D4). Writes: per-comparison `method.params` (+ `name` on family switch), experiment-level `alpha`/`correction`, and Review-mode `is_main_metric`/`is_guardrail` flips (**marking only** — D9). `MethodConfig` is frozen (method_config.py:29) — build new instances, never mutate.
- Validation before any filesystem write: construct via `create_method(name, alpha, params)` (the donor's `DetectorFactory.create` analogue) **and** `ExperimentConfig.model_validate` on the merged document (config_writer.py:157–158, 227 pattern).
- Archive: original **bytes verbatim** to `experiments/.history/<exp>/<exp>-<YYYYmmddTHHMMSSZ>.yml` — the seam is pre-wired: discovery already excludes hidden dirs and documents exactly this contract (validator.py:37–56; discovery.py:85). Repeated Applies each archive (timestamped names). YAML comments die on safe_load→safe_dump; the archive is the recovery — accept + document, donor precedent (config_writer.py docstring; D4). Re-emission goes through the single `_reemit_yaml` strategy function (the D4 ruamel seam) — nothing else in the writer may serialize YAML.
- Provenance header on re-emit: timestamp, archive path, changed comparisons, orphaning note, reproduce command (config_writer.py:106–118 pattern).
- **Orphan detection**: before writing, compute old vs new `method_config_id` per touched comparison; if changed AND `list_method_config_ids(exp, metric)` shows persisted rows under the old id (`_results.py:85–112`), the `AppliedConfig` result carries an `orphaned:[{metric, old_id, rows}]` block. The warning text mirrors the driver's existing one — "orphaned method_config_id series … duplicate stabilization lines — run `abk clean`" (driver.py:192–204) — and the epilogue adds "re-run `abk run --select <exp>`". Apply **never** auto-cleans or auto-runs (D4).
- Dirty-slot semantics ported as dirty-**comparison** semantics: the comparison the cockpit opened on is always written; a comparison merely viewed via the picker never is (the phantom-param bug class, tune.ts:480–495).

**Tests:** port `test_tune_config_writer.py`'s shapes (404 ln): fixed `now` for deterministic archive paths; archive holds original bytes incl. comments; invalid params / quarantined method / empty change-set write nothing and archive nothing; untouched comparisons preserved **verbatim, both orderings, no phantom params**; identity-excluded carry-over; frozen-model rebuild; ADD: (a) orphan block present iff id changed AND old rows exist (fake_db), absent for alpha-only edits (alpha is identity-excluded — factory.py:16–26), (b) `is_main_metric` flip is orphan-free but changes `effective_alphas` output (cross-check via analyze.py:58–83), (c) repeated-Apply archive accumulation.

**DoD:** `apply_tuned_config` is the only writer; every identity-bearing edit yields an orphan warning before the write; `.history` archive is byte-verbatim.

**Must-fixes discharged:** the orphan-detection half of cli-and-dx §2 ("warn … and offer `clean`"); Apply-side groundwork for *calibration-in-explore* (the confirm payload, WP6 enforces).

---

### WP6 — `tuning/server.py` + `tuning/html.py` + `tuning/payload.py` — the explore localhost server (after WP2, WP4, WP5)

**Goal:** the stdlib localhost server binding the recompute engine, the Apply seam, and the baked cockpit page into the donor's exact interaction contract: GET serves one pre-rendered page; token-gated POSTs mutate; only a successful Apply is terminal.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/tuning/server.py` (554 ln) | `abkit/tuning/server.py` | A — `_TuneServer`→`_ExploreServer`; state fields `metric/incidents_dir/metric_config/…` → `experiment/experiment_config/session cache` (server.py:91–101); all transport gotchas kept verbatim |
| `POST /apply` (server.py:179–188) | `POST /apply` | ⟲ (parse → `apply_tuned_config` → reply incl. `orphaned` → **shutdown from a spawned daemon thread**; errors 400 + keep serving) |
| `POST /autotune` (server.py:212–231, 288–463) | `POST /recompute` (repeatable, advisory, lock-serialized, **stale-dropping**) + `POST /reload` (Tier-R) + a **stubbed** `POST /validate` route reserved for M4 Auto mode | A — the donor's repeatable-advisory shape is the template; `/validate` is one line: 501 + "requires abk validate (M4)" |
| `POST /labels` (server.py:190–210) | — | — skip (incident labeling has no experiment analogue; not repurposed in M3 — D9) |
| `detectkit/tuning/html.py` (82 ln) | `abkit/tuning/html.py` | ⟲ (`__DTK_TUNE_PAYLOAD__`→`__ABK_EXPLORE_PAYLOAD__`; **drop Google Fonts**; `str.replace`, JS last; `importlib.resources` bundle read) |
| `detectkit/tuning/payload.py` (426 ln) | `abkit/tuning/payload.py` | **RW** — thin: wraps WP2's experiment payload + explore extras (knob specs from `param_specs`, seed values from the row's canonical `method_params`, tier map, calibration block, look counter, endpoint slots); the donor's series/window logic is superseded by WP2 + WP4 |

**Hotspots (from the tuning survey — the gotcha list is the checklist):**
- `("127.0.0.1", 0)` ephemeral port; one-shot `secrets.token_urlsafe(16)`; `daemon_threads=True`; `_MAX_BODY` 5 MB; endpoint URLs injected + HTML rendered **once, post-bind** (server.py:487–504); GET unauthenticated serves the page on any path — token gates only POSTs.
- `_reply_error` puts detail in the UTF-8 body, never the latin-1 status line (server.py:146–162); lazy-numpy `_json_default` (server.py:271–285).
- `/recompute` serialized by a `threading.Lock` (donor `autotune_lock`, server.py:99–101); it touches only the in-memory session cache (no DB). **Server-side stale-drop (critique finding):** the handler compares the request's id against the latest-seen id *before* starting compute and replies `409 {stale: true}` for stale ones — a `threading.Lock` alone cannot cancel an in-flight bootstrap and debounced knob drags would queue behind it (the donor killed stale computes via worker terminate-respawn, tune.ts:1240–1252; this is the server-side re-expression). Client `AbortController` handles the transport half (WP7).
- **`/recompute` replies silently** (structured JSON only); the `StageLogRenderer` run-log streaming via the injectable-echo `"STAGE rest"` line protocol (`_output.py:47, 64–91`; run.py:115–119) is attached to **`/reload`** (and the future `/validate`) only — the donor streams on explicit `/autotune` clicks, not per-knob (critique finding: per-knob terminal streaming is spam).
- **Apply gate enforced server-side too**: `/apply` requires `confirm_uncalibrated: true` in the body when the applied knob state's calibration lookup (keyed incl. **alpha**, D3) is empty, and echoes the WP5 `orphaned` block in the reply for the CLI epilogue.
- **No pipeline lock**: explore writes only YAML (donor tune.py:8–11; seam map §6 — `load_results` reads are FINAL-deduped and lock-free).
- `serve_explore`: print URL always; `webbrowser.open` wrapped in the `os.dup2` stderr silencer (WSL/headless noise, server.py:48–68); `KeyboardInterrupt` → cancel/`None`; `finally: server_close()`; return `applied`.
- `--no-serve` path: payload with `save_url/recompute_url = null` → the client renders the preview badge, Apply hidden, recompute knobs disabled with a note (the donor's exact static-preview mechanism, tune.ts:576, 1789 — reused as the D3 gating substrate).

**Tests:** port `test_tune_server.py`'s shape (613 ln — real HTTP against the threaded server, stub manager honoring half-open bounds): token URLs baked; valid apply → 200 + `th.join` proves self-shutdown + YAML rewritten + archive exists + `orphaned` in reply; bad token 403 file untouched; invalid config 400 no archive **keeps serving**; `/recompute` repeatable + advisory + serialized; stale request id → 409 and the fresh one still answers; `/recompute` with unchanged knobs returns the persisted numbers (WP4 golden reused over HTTP); uncalibrated apply without `confirm_uncalibrated` → 409/400 with the cost message, with it → 200; `/reload` streams the structured run-log through `server.echo` while `/recompute` stays silent; `/validate` → 501 stub; oversized body 413; numpy JSON fallback; empty-results experiment → payload empty-state not a crash.

**DoD:** `build_explore_server(...) -> (server, url)` + `serve_explore(...)` pass the ported suite; the donor's terminal-Apply / repeatable-everything-else contract holds.

**Must-fixes discharged:** *calibration-in-explore* — the enforcement half (Apply gated server-side when uncalibrated, ROADMAP M3 DoD).

---

### WP7 — `web/src/explore/` — the cockpit client (after WP6's endpoint contract; UI parallelizable against a payload fixture)

**Goal:** the browser cockpit: stabilization windshield + pinned chips, Basic/Advanced side rail, Tune/Review live + Auto/Segment stubs, the Apply gate dialog — vanilla DOM + canvas, zero runtime deps.

| Source | Target | Verdict |
|---|---|---|
| `website/src/scripts/report/tune.ts` (2365 ln) | `web/src/explore/explore.ts` | **RW** on donor skeleton — chart/rail/mode scaffolding, debounce + stale-drop discipline kept; detector semantics replaced |
| `website/src/scripts/report/tune.worker.ts` | — | — skip (recompute is server-side per D1 — the worker's terminate-respawn discipline maps to `fetch` + `AbortController` + request ids: abort the in-flight `/recompute` on a new knob change, drop stale responses by request id, stale must not clear the in-flight flag — tune.ts:1185–1252 semantics preserved over HTTP, paired with WP6's server-side stale-drop) |
| donor band chart (`createChart`) | the stabilization chart (shared `web/src/shared/chart.ts` from WP3) | A — effect ± CI band vs `elapsed_days`, zero line, horizon marker; warm-up shading + "detection at full power →" divider → the **pre-horizon zone** + dashed pre-horizon CIs |
| Review-alerts / Label-incidents / lasso / threshold-capture modes | — | — skip (anomaly-workflow-specific); **Review** here = guardrail/primary **marking** (D9) |
| donor quality HUD / `false_alert_budget` | the **calibration chip** + verdict chips | RW |

**Hotspots (from the tuning survey + spec):**
- **Windshield** (§5.1): the stabilization chart + pinned chips — estimated lift, CI half-width, p-value, current power, **A/A calibration (real α)**, **SRM flag** — chips update from every `/recompute` reply. Chart segments styled by D1 tier: solid (exact recompute), hatched "approx (α-only)" for CUPED-under-α-change, and the persisted baseline line always visible for comparison; greyed `insufficient_data` gaps; dashed pre-horizon CIs (§4). All these renderings carry the WP3 stable marker classes (`abk-prehorizon`, `abk-insufficient`, …) so the CI bundle gate + WP10 can assert them.
- **Calibration chip** (D3): states — grey "uncalibrated — never passed `abk validate`", green "calibrated / FPR=X.X% vs nominal α", red when over the resolved budget, and **downgraded-to-uncalibrated on alpha mismatch** ("calibrated at α=0.05, current α=0.01") (aa-fpr §3 headline format, §5). The **look counter** ("look 37 / ~336 planned", from the WP2 `look` block) sits next to it at sub-day cadence (§4).
- **Side rail** (§5.1 as amended by D12; cli-and-dx §2): auto-generated from the payload's `param_specs` block; **Basic** = `test_type`, alpha, CUPED on/off (the donor has no Basic/Advanced split — this is NEW; sidedness struck per D12); **Advanced** = the full existing-param surface (stratify toggle + `weight_method`, bootstrap `n_samples`/`stat`/`pvalue_kind`, correction, `calculate_mde`/`power`; **no winsorization** — D12; analysis unit badged **preview-only**). Identity-bearing knobs badged ("⚠ changes the results series"); Tier-R knobs badged ↻ and routed through a confirm → `/reload`. Slider identity hazard ported: a knob's range must include the seeded value exactly and step must address it (tune.ts:1411–1416) — a snapped param would silently mint a new `method_config_id`.
- **Apply flow**: collect dirty comparisons → if reply-side calibration says uncalibrated, show the confirm dialog quoting the cost ("these params have never passed `abk validate` — real FPR unknown; nominal α may understate it") and set `confirm_uncalibrated`; on 200 show archive path + updated/preserved + **the orphan warning + `abk clean` hint** when present; on error re-enable (donor flow). `save_url === null` → preview badge, no Apply (the `--no-serve` static page).
- **Modes**: Tune + Review (marking-only) shipped; **Auto** button present-but-disabled with "requires `abk validate` (M4)" wired to the reserved `/validate` route; **Segment** present-but-disabled (D9 — deferral recorded in ROADMAP). Debounce 130 ms; expensive path on `change`, echo on `input`.
- All strings escaped before `innerHTML`; styles injected once under `abk-explore`; bundle built by `web/build.mjs` into `abkit/tuning/assets/explore.js`, wheel-packaged (2-file contract), CI freshness-checked (shared with WP3).

**Tests:** Python-side (donor stance — the bundle is an opaque committed asset): html.py bake test (no leftover placeholders, `__ABK_EXPLORE__` global + mount present, `</`-escaped payload); payload knob-spec block round-trips `param_specs`; static-preview payload has null endpoints. Plus the CI bundle-marker assertions (WP3's node job covers both bundles). Interactive behavior is covered by WP6's HTTP suite + the WP10 e2e.

**DoD:** every §5.1 element (as amended by D9/D12) exists; the M3 DoD sentence — "Apply gated when uncalibrated; calibration chip wired" — is demonstrable in the browser against an empty `_ab_aa_runs`.

**Must-fixes discharged:** *calibration-in-explore* — the always-visible-chip half (quorum-review); *SRM loud* — the explore red-chip half (§6); *peeking* — the cockpit "not peeking-valid" rendering (§4).

---

### WP8 — `cli/commands/explore.py` + registration (after WP6/WP7)

**Goal:** `abk explore --select <exp> [--metric <m>] [--no-serve] [--no-open] [--profile]` — the orchestration shell, house-styled.

| Source | Target | Verdict |
|---|---|---|
| `detectkit/cli/commands/tune.py` (163 ln) | `abkit/cli/commands/explore.py` | A — single-**experiment** guard (donor tune.py:54–61 was single-metric); `_load_project` → `load_project_context` (`_context.py:48–83`); incidents preload (tune.py:67–92) — skip |
| `detectkit/cli/main.py:310–385` (command registration) | `abkit/cli/main.py` | A — an **eager `@cli.command()` stanza with the lazy command-body import inside the function** (the house pattern, abkit main.py:36–60, 92–106; the "explore" slot is a docstring mention at main.py:9–11 — there is no lazy-group machinery, critique finding) |

**Hotspots:**
- Flow: `load_project_context(require_profiles=True)` → `select_experiments` (must match exactly one; errors name the namespace, cli-and-dx §1) → manager → `InternalTablesManager` → **guard**: no `_ab_results` rows for the experiment ⇒ friendly "run `abk run` first" noop (`echo_noop`, D2) → WP2 payload + WP4 session load (progress streamed via `StageLogRenderer`) → `--no-serve` writes `reports/<experiment>__explore.html` and exits, else `serve_explore`.
- `--metric` narrows the opened comparison (the donor's `_choose_seed_index` analogue: default = the main metric's comparison).
- **Startup orphan warning**: run the `list_method_config_ids` scan and print the driver-identical warning before serving (cli-and-dx §2) — same scan `abk clean` uses (`_results.py:88–93`).
- Epilogue on Apply: archive path, updated/preserved comparisons, the orphan/`clean` hint when the reply carries `orphaned`, and "re-run `abk run --select <exp>`" (donor tune.py:151–163 reshaped). Ctrl-C → "explore cancelled — experiment unchanged". All failures `click.ClickException` → non-zero exit (house rule, `_context.py:1–7`).

**Tests:** port `test_tune_command.py`'s monkeypatch orchestration shape (no DB, no server): `--no-serve` static file; serve receives config path/project root/payload; cancelled → no write; multi-experiment select refused; empty-results noop; no-match non-zero; orphan warning printed when the fake scan returns >1 id; `--no-open` suppresses the browser call.

**DoD:** the cli-and-dx §1 command line works end-to-end against fake_db; registered per the house pattern (`abk --version` stays instant).

**Must-fixes discharged:** orphan-warning surfacing at explore startup (cli-and-dx §2).

---

### WP9 — M2-deferred hardening: PG/MySQL testcontainers + the atomic-claim race (parallel, any time)

**Goal:** discharge the one M2 deferral explicitly routed here (ROADMAP M2): "The PG/MySQL testcontainers integration suite (incl. the two-process atomic-claim race test)".

| Source | Target | Verdict |
|---|---|---|
| `tests/e2e/` testcontainers-ClickHouse precedent (M2 WP10) | `tests/integration/test_pg_container.py`, `tests/integration/test_mysql_container.py`, `tests/integration/test_lock_race.py` | **NEW** (pattern reuse) |

**Hotspots:**
- Per dialect (Postgres, MySQL): manager round-trip (create/insert/read/delete on a `TableModel`), `ensure_tables()` for the full `_ab_*` set, `save_results` LWW/version semantics, `load_results` FINAL-equivalent dedup, `try_acquire_lock` claim + release + failure-record path (driver.py:229–239).
- **Two-process atomic-claim race**: `multiprocessing` (not threads — the claim must be cross-connection), both processes race `try_acquire_lock` at `(experiment, "pipeline", "run")`, assert exactly one winner and a clean loser; repeat N rounds. PG/MySQL claims are single-statement atomic — this is the test that proves the statement, per dialect.
- CI: a separate matrix job (dockerized, like the CH e2e job added in the M2 review — m2-plan §5); marked `integration`, skipped without Docker locally.
- Do **not** pull the other M2 deferrals (STATE-stage wiring → v2, paired methods → notebook-only, sequential → M5, `tables:` overrides → unassigned).

**Tests:** the WP *is* tests. ADD: a small fixture factory unifying the three dialects so M4+ integration tests reuse it.

**DoD:** ROADMAP M2 deferral line struck through with a pointer to this WP; CI matrix green.

**Must-fixes discharged:** none (recorded M2 deferral discharge).

---

### WP10 — the M3 e2e gate + adversarial review (last)

**Goal:** the milestone exit: an end-to-end proof that a fresh project reaches a verdict-bearing offline report and a functioning gated cockpit, then the N-lens adversarial review, applied as one `fix:` commit with the record appended as section 5.

| Source | Target | Verdict |
|---|---|---|
| `tests/e2e/test_first_run.py` (M2 WP10 — in-memory mirror + testcontainers CH variants) | `tests/e2e/test_first_report.py`, `tests/e2e/test_explore_session.py` | A (extend the shipped harness) |

**Hotspots:**
- **Report e2e:** `abk init` → seed → `abk run --select example_signup_test --report` → assert the HTML exists, is self-contained (no `__*__` placeholder, no `http(s)://` substring — stricter than the donor, we dropped webfonts), carries `__ABK_REPORT__`, a verdict string, the SRM chip markup, and the calibration empty-state; **assert the per-point payload flags** (`hz` on the horizon row, `ins` on demoted rows, `blk` under SRM) and the `look` block on a sub-day fixture, plus the marker classes present in the baked page (the critique's automated-proof hole for the peeking-rendering must-fix); re-run → report regenerates, payload byte-stable modulo `generated_at`.
- **Explore e2e:** build the server against the seeded project → GET serves the page → `POST /recompute` with unchanged knobs reproduces the persisted latest-cutoff numbers → `POST /recompute` with a changed alpha returns the α-inverted CI → a stale request id → 409 → `POST /apply` without `confirm_uncalibrated` refused (empty `_ab_aa_runs` ⇒ uncalibrated — the M3 DoD sentence, mechanically tested) → with it, YAML rewritten + `.history` archive + orphan block for an identity edit → server self-shutdown observed. Both e2e's run in the in-memory-mirror variant on every push; the testcontainers-CH variant in the docker job. The CH variant also exercises D11 (real warehouse read order feeding byte-stable bootstrap rows).
- **Adversarial review** (the M1/M2 pattern): finder lenses chosen for the M3 risk surface — suggested six: (1) recompute exactness/statistical binding (Tier-E surrogates incl. the z-test inversion, seeds, α-inversion, canonical order), (2) verdict logic vs the §1/§4 contract (the DoD-audit lens), (3) the HTTP surface (token, body limits, injection, shutdown, concurrency, stale-drop), (4) config-write/orphaning/`.history` integrity, (5) payload/renderer self-containment + escaping, (6) time & grids (elapsed-days stabilization, tz, sub-day rendering, look counter). Findings independently refuted, all verified findings applied in one `fix:` commit that also syncs CLAUDE.md, `.claude/rules/architecture.md` (the "Not yet present (M3+)" list shrinks), ROADMAP, CHANGELOG, and appends plan section 5 — the docs-sync happens in the review commit, not per-WP (house rule).

**Tests:** the two e2e files above; a CI job wiring commit if needed (the M2 precedent added the CH job during review).

**DoD:** the full section-4 table below is green; the exit-gate sentence holds.

**Must-fixes discharged:** the DoD-audit lens re-verifies *calibration-in-explore*, *SRM surfacing*, and the *peeking* readout/rendering halves end-to-end.

---

## 2. Dependency graph / parallelism

```
WP1 (readout core, NEW)
 ├──▶ WP2 (experiment payload) ──▶ WP3 (HTML readout + run --report)   [report track]
 │            │
 ├──▶ WP4 (recompute engine) ─┐
 └──▶ WP5 (config writer) ────┼──▶ WP6 (explore server + shell) ──▶ WP7 (client) ──▶ WP8 (abk explore)
              WP2 ────────────┘                                                        │
WP9 (PG/MySQL hardening — parallel to everything) ────────────────────────────────────┴──▶ WP10 (e2e + review)
```

Three parallel tracks after WP1: **Track A** WP2→WP3 (the readout), **Track B** WP4 (recompute) + WP5 (writer) converging on WP6→WP7→WP8 (the cockpit), **Track C** WP9 (hardening, no coupling). The payload schema (D6) is one contract spanning WP2/WP3/WP6/WP7 — coordinate those reviews even though they're in different WPs; likewise the `/recompute` reply shape (incl. request ids) couples WP4↔WP6↔WP7. The `web/` toolchain lands in WP3 and is reused by WP7 — if the cockpit track outpaces the report track, hoist `web/build.mjs` + `shared/chart.ts` into a small WP3a.

---

## 3. Decisions — the open points, settled here

Every reader- or critique-surfaced open decision is settled below; spec amendments ride the WP PR that implements them (house rule, m2-plan).

**D1 — Explore recompute architecture: server-side Python over a tiered cache; spec amendment to cli-and-dx §2.** The spec's two constraints collide at the edges: "no JS stats fork" rules out the donor's client-side recompute, and "no DB round-trip" collides with the fact that CUPED cross-moments and bootstrap replicates are simply not in `_ab_results` (seam map §2). Resolution: **all recompute is server-side Python** (`POST /recompute` on localhost — the donor's `/autotune` shape, repeatable/advisory/locked/stale-dropping), and "no DB round-trip" is amended to mean "no *warehouse* round-trip per knob change": the server answers from (Tier E) exact suffstats reconstructed from persisted rows for t-test/z-test/ratio-delta across the whole grid — with the z-test's `nobs` inverted from the persisted SE, never taken from `size_i` (the critique blocker), (Tier α) alpha-inversion for all parametric rows incl. CUPED, (Tier S) a bounded per-unit session cache (latest cutoff always; older cutoffs to a ~2×10⁷-value budget) for bootstrap/stratify-toggle/CUPED-off via `from_samples`, and (Tier R) explicit, confirmed `/reload` actions for CUPED-on, `covariate_lookback`, and analysis-unit changes. The chart renders exact/approx/baseline segments honestly. This keeps one source of truth (the M3 DoD's literal "live `from_suffstats` recompute" holds for the closed-form families), makes bootstrap knobs live for the latest cutoff, and never silently degrades. cli-and-dx §2 amended in the WP4/WP6 PR.

**D2 — Explore data source & freshness: persisted rows + one session load; a prior `abk run` is required; spec amendment.** The spec says "a localhost cockpit where the analyst runs the pipeline and plays with method params live" (data-contract §5.1). Re-running the full per-cutoff load loop inside explore would duplicate the pipeline (locks, SRM, persistence) for no benefit. Explore therefore reads the **persisted** `_ab_results` series (the donor's "what actually ran" stance, builder.py:1–9) for the baseline chart, and performs exactly one warehouse load pass at session start (`RecomputeBackend.load_cutoff`, budget-bounded) to fill the Tier-S cache — read-only, lock-free (seam map §6). No rows ⇒ friendly "run `abk run --select <exp>` first" noop. Freshness = whatever the last run produced; the header shows the latest `end_ts`/watermark so staleness is visible. **This interpretation is folded into the data-contract §5 amendment** (same PR as D6) so the spec stops implying an in-cockpit pipeline run (critique finding).

**D3 — Calibration chip pre-M4: keyed by (metric, `method_config_id`, **alpha**), against the as-built `_ab_aa_runs`; "gated" = confirm-with-cost, enforced server-side.** The chip reads `get_aa_runs(experiment)` filtered in Python by `(metric, method_config_id, alpha, status='success')` — one reusable `find_calibration` function — against the **shipped** schema (tables.py:224–263, a superset-with-renames of aa-fpr §7; the chip tolerates extra keys). **Alpha is in the key** (critique finding): alpha is identity-excluded from `method_config_id`, but empirical FPR is measured at a specific nominal α (the as-built row stores `alpha`, tables.py:245) — an alpha edit therefore downgrades the chip to "calibrated at α=X, current α=Y" and gates like uncalibrated. States: uncalibrated / calibrated-in-budget / calibrated-red / alpha-mismatch; budget via the metric→project→`α×1.5` resolver seam (aa-fpr §4.1; project field exists, project_config.py:93–109; metric-level left as a seam). No separate "stale" state is invented: calibration is keyed by `method_config_id`+alpha, so any identity or alpha edit flips the chip automatically — that *is* the staleness semantics, recorded in the spec amendment. "Gated/confirmed" (the spec's own ambiguity) = a confirm dialog quoting the cost client-side **plus** a server-side `confirm_uncalibrated: true` requirement on `/apply` — never a hard block (an analyst may legitimately ship pre-validation), always a visible cost. With `_ab_aa_runs` empty until M4, every Apply takes the confirm path — the mechanically testable M3 DoD. Auto mode ships as a disabled button + a reserved one-line 501 `/validate` route so M4 only supplies the callable. The D6 `calibration` payload block is **shaped for M4 now** (fields for `empirical_fpr`, `peeking_fpr`, the "nominal α X vs real peeking FPR Y" headline, matrix-row list, and a link-out slot to the `abk validate --report` artifact) so M4 fills it without a payload v-bump (critique finding).

**D4 — Apply ↔ orphaning: warn-and-hint, never auto-clean/auto-run; experiment YAML is the sole write target; `.history` archives verbatim per Apply.** Method params live on `ComparisonConfig` in the experiment YAML (experiment_config.py:150–165) — metric YAMLs are never touched (which also makes the analysis-unit knob preview-only, WP4). Apply computes old-vs-new `method_config_id` through the single hashing path (method_config.py:51–60), and when the id changes over an existing series it surfaces the driver-identical orphan warning (driver.py:192–204) in the confirm dialog, the `/apply` reply, and the CLI epilogue, with `abk clean` + `abk run` hints. It never auto-triggers either: clean deletes data and run takes locks/time — both deserve explicit user intent, and the pipeline already re-plans the whole grid for a new id on the next run (empty `list_computed_cutoffs`). Repeated Applies each write a timestamped archive; YAML comments die on re-emit and survive only in the archive — donor-documented behavior, **owner-ratified**, with a designed seam for the future: `config_writer` isolates document re-emission behind one strategy function (`_reemit_yaml(document, original_bytes) -> bytes`) so a comment-preserving ruamel.yaml backend can swap in later without touching the Apply contract, the validate→archive→re-emit order, or the archive semantics. ruamel stays **out** of the M3 dependency set; the option is recorded as a ROADMAP backlog note in the WP5 PR.

**D5 — Readout numbers (spec amendment to data-contract §1 in the WP1 PR).** (a) **Stabilization**: over the trailing `readout.stabilization_days` (default **7** elapsed-days, floored at 3 non-demoted cutoffs), every cutoff's CI must exclude zero with a consistent sign (for WIN/LOSE) or include zero (for FLAT) — judged strictly over `elapsed_days` (§4); 7 days covers one weekly cycle, the same constant the spec's own representativeness caveat uses. (b) **Business-meaningful effect**: a new optional `ComparisonConfig.min_effect`, **in the units of the persisted `effect` for that comparison** (test_type-dependent — critique finding); when absent, FLAT is unreachable and the INCONCLUSIVE rationale says "no min_effect configured — cannot distinguish flat from underpowered" — honest by construction. FLAT additionally requires `max(mde_1, mde_2) ≤ min_effect` at the latest cutoff, where NULL `mde` columns fall back to the read-time `stats/power.py` computation for t/z rows (cross-checked at rel-1e-9 against `calculate_mde: true` rows) and stay honest-unreachable for methods without MDE capability (ratio-delta, bootstrap) (critique finding). (c) **Guardrail regression**: a new `ComparisonConfig.desired_direction` (default `increase`); regression = the guardrail's CI excludes zero against its desired direction at the stored per-row alpha at the latest cutoff — no stabilization requirement (conservative: any significant harm flags). The consequence is a **user-choosable policy** (owner-ratified): `readout.guardrail_policy: "block"` (default) **caps WIN at INCONCLUSIVE**; `"warn"` keeps the WIN verdict but attaches a mandatory loud caveat. Under either policy the regression is always listed in the rationale, LOSE is never upgraded or blocked, and the policy — being readout-time config — never enters `method_config_id`. (d) **Pre-horizon**: with `ci_kind="fixed"` (all M3 rows) and `is_horizon=0` at the latest cutoff, the verdict is INCONCLUSIVE with a progress rationale — this extends the spec's "refuses pre-horizon WIN/LOSE" to FLAT, since FLAT is equally a stop decision. (e) **Multi-arm aggregation**: verdicts are per control-vs-treatment pair; the report shows one verdict per treatment arm and **no invented scalar aggregate** — the spec defines the verdict on a single series (§1), and the two-arm common case naturally yields one headline. (f) **Persistence**: verdicts are read-time only, recomputed at render — the contract has no verdict column (§2) and adding one would freeze read-time-tunable logic into rows. (g) **BH**: applied read-time across the latest cutoff's comparisons when `correction: benjamini_hochberg` — required, not optional, because compute-time rows carry raw alpha (analyze.py:74–77); the ROADMAP M5 line is annotated (pull-forward recorded), the composed-FDR empirical validation stays M4/M5.

**D6 — The baked payload contract (experiment-primary; new short spec section in data-contract §5 with the WP2 PR).** One versioned schema shared by report and explore: `{v, experiment, project, generated_at, period, cadence_seconds, tz, arms, srm:{flag,pvalue,observed,expected}, calibration|null (M4-shaped: fpr, peeking_fpr, headline, matrix_rows, report_link), verdicts:[{metric,pair,verdict,rationale,caveats}], metrics:[{name, description, main, guardrail, method:{name,params,id,alpha}, pairs:[{c,t, series:[{t,ed,e,lo,hi,p,rj,s1,s2,mde,hz,blk,ins}]}], warnings}], look:{n,planned}|null, endpoints…|null}` — terse point keys (donor gotcha), NaN→null, `metric_query`/`metric_rendered_query` projected out, **metric descriptions sourced from the metric YAML configs the builder already loads** (`MetricConfig.description` — `_ab_experiments` stores only the experiment description; critique finding), `</`-escaped at bake. Explore extends it with `param_specs`/tier-map/seed blocks; the report ignores unknown keys. Python `reporting/builder.py` and `web/src/shared/payload.ts` are kept in documented lockstep (the donor's payload.ts:1–10 discipline).

**D7 — JS source home: in-repo `web/` TS + esbuild, committed bundles, CI freshness gate.** abkit has no website repo, and the donor's silent-drift hazard (bundles regenerated manually out-of-band) is real. Sources live at `web/src/{shared,report,explore}/`, built by `web/build.mjs` (esbuild, IIFE, es2019, dev-only node dependency) into the committed, wheel-packaged `abkit/reporting/assets/report.js` and `abkit/tuning/assets/explore.js`; a CI job rebuilds, diffs, and asserts the peeking-marker classes. The renderer stays framework-free (invariant 6); the donor's `__ABK_*`-global assertion is kept in the build script.

**D8 — The readout surface is `abk run --report`, no new command; the `--steps readout` token is amended away.** The spec's CLI table defines `--report` (cli-and-dx §1); the donor precedent emits the report after the pipeline regardless of work done, so re-running an up-to-date experiment (zero pending cutoffs — cheap: plan resolves to the anti-join no-op) is the "just give me the report" path. `abk explore --no-serve` covers the static-snapshot need. The same §1 row also lists `readout` as a `--steps` token — never wired, superseded by `--report`; the row is amended in the WP3 PR rather than adding a READOUT `PipelineStep` (critique finding). A dedicated `abk report` can be added later without breaking anything; not in M3. (`abk test-report` stays M6 with channels.)

**D9 — Mode scope: Tune + Review (marking-only) shipped; Auto + Segment stubbed-disabled; deferrals recorded.** Auto requires the M4 engine by definition (aa-fpr §5). Segment has one spec sentence and no data source (§5.1); shipping a half-designed heterogeneity view would violate never-change-a-number discipline in spirit — **the deferral is recorded in ROADMAP in the same PR** (critique finding: currently it would fall to no milestone). Review mode ships the cheap, shippable half: `is_main_metric`/`is_guardrail` flips through the same Apply seam (orphan-free — they're config, not method params — but they change the two-tier effective alphas, which the confirm dialog states). The spec's "confirm the decision" half of Review (§5.1) is **explicitly deferred with a §5.1 amendment** (critique finding): a decision acknowledgment needs a storage/audit answer (read-time verdicts have no row to acknowledge onto, per D5(f)) — it lands with the app-trajectory work, not as an invented `_ab_decisions` table in M3. Both stub buttons render disabled with a one-line reason so the M4+ seams stay visible in the UI contract.

**D10 — `docs/examples/bi/` reference queries + SRM panel snippet → M6.** The spec assigns no milestone (§3); ROADMAP already places the BI panel + gap documentation in M6. M3's only BI obligation: don't break the `_ab_results` contract (no schema change is made) and state in the report footer/docs that the CLI + HTML report is the canonical SRM gate surface (§6).

**D11 — Canonical unit order in the loaders (bootstrap determinism on real backends).** `metric_loader` builds per-variant arrays in warehouse result-set order with no sort (metric_loader.py:164–186), and bootstrap replicates are order-dependent (resample indices index the array) — so the M2 "byte-stable re-run" and the M3 "unchanged knobs reproduce persisted rows" claims only held on order-deterministic backends (fake_db, the seed dataset); ClickHouse guarantees no order (critique major). Resolution: the loader canonically sorts per-unit arrays by unit key after fetch (the session cache preserves that order), making bootstrap rows reproducible across physical read orders. This is a **pipeline-level input-assembly fix, not a method change** — identical `Sample` inputs still produce identical outputs, no `ALGORITHM_VERSION` bump — but rows persisted before the sort may differ from re-computed ones on backends that happened to return a different order, so it is recorded in `statistics-changes.md` as a determinism note + CHANGELOG entry (never change a number silently, even here). Lands inside WP4 with the order-permutation test; the WP10 CH e2e proves it end-to-end.

**D12 — Sidedness + winsorization: deferred from the knob surface under change control; §5.1 amended.** The spec's side-rail list names "alpha + one/two-sided" and "winsorization", but neither exists in the shipped stats core — p-values are hardcoded two-sided (effects.py:131, ztest.py:63; power.py `alternative="two-sided"`), and no winsor param exists anywhere in `abkit/` (critique majors). The rail is auto-derived from `param_specs` (invariant 3), so faking either would special-case UI against math that isn't there. Both are deferred: adding a sidedness param or a winsorization param is a stats-core change with the full change-control obligations (`ALGORITHM_VERSION`-adjacent identity impact, statistics-changes entry, A/A validation) — queued behind M4 where the A/A harness can arbitrate them, recorded in ROADMAP. cli-and-dx/data-contract §5.1 amended in the WP7 PR to strike both from the M3 rail.

**Owner decisions (ratified 2026-07-03):**
1. D5(c) guardrail regression: **blocking is the default, user-choosable** — `readout.guardrail_policy: block | warn`, default `block` (caps WIN at INCONCLUSIVE); `warn` keeps WIN with the mandatory loud caveat.
2. D4 YAML comments: **variant A ratified** — no ruamel dependency in M3; the verbatim `.history` archive is the recovery, and the `_reemit_yaml` strategy seam is designed in so a comment-preserving ruamel backend can land later without contract changes (ROADMAP backlog note).

---

## 4. M3 definition-of-done → WP map

| DoD item (ROADMAP M3 + quorum) | Proven by | WP |
|---|---|---|
| Port `tune` → `abk explore`: localhost server | ported `test_tune_server.py` suite over the abkit server; e2e explore session | **WP6**, **WP10** |
| Live `from_suffstats` recompute (no JS stats fork) | golden round-trip: reconstructed suffstats reproduce persisted rows (incl. the z-test `nobs` inversion on `nobs>1` fixtures); α-inversion vs direct construction; order-permutation test (D11) | **WP4**, **WP6** |
| Stabilization chart (elapsed-time x-axis, tier-honest segments) | payload schema tests + e2e page assertions | **WP2**, **WP7** |
| Basic/Advanced knobs from `param_specs` (as amended by D12) | knob-spec payload tests; identity-badge/tier classification table | **WP4**, **WP7** |
| `.history` write-back | ported `test_tune_config_writer.py` archive-verbatim suite | **WP5** |
| Orphan detection (warn + offer `clean`) | orphan-block writer tests; explore-startup warning test; e2e identity-edit Apply | **WP5**, **WP8**, **WP10** |
| Port `reporting` → self-contained HTML readout | self-containment gate (no placeholders, no external URLs); `run --report` e2e | **WP3**, **WP10** |
| `readout.py` decision logic (WIN/LOSE/FLAT/INCONCLUSIVE; SRM hard gate; pre-horizon refusal) | verdict known-answer tables; SRM/pre-horizon refusal cases; NULL-mde + read-time-MDE branches; both `guardrail_policy` branches | **WP1** |
| **Apply gated when uncalibrated; calibration chip wired (depends on M4)** | `/apply` refuses without `confirm_uncalibrated` against empty `_ab_aa_runs`; chip state lookup tests incl. alpha-mismatch; e2e | **WP4**, **WP6**, **WP7**, **WP10** |
| Must-fix: calibration-in-explore (quorum) | chip always in the windshield; server-side gate; alpha in the calibration key | **WP4/WP6/WP7** |
| Must-fix: SRM surfacing — red chip in HTML report & explore (quorum; §6) | SRM block in payload + chip markup asserted in both surfaces | **WP2/WP3/WP7** |
| Must-fix: peeking — refusal + "not peeking-valid" rendering (quorum) | WP1 refusal tests; per-point payload-flag (`hz`/`ins`/`blk`) + `look`-block e2e assertions + CI bundle-marker checks | **WP1/WP3/WP7/WP10** |
| M2 deferral: PG/MySQL testcontainers + two-process atomic-claim race | the integration suite itself; CI matrix job | **WP9** |
| Renderer stays framework-free / embeddable (invariant 6) | zero-external-request e2e assertion; scoped-CSS/escaper checks; CI bundle-freshness gate | **WP3/WP7/WP10** |

**Exit gate:** CI green (unit + the PG/MySQL/CH integration matrix), both e2e variants green, CHANGELOG entries for the D1 spec amendment, the D5 config additions, and the D11 loader ordering (zero method-math changes — goldens untouched), specs amended where D-items say so (cli-and-dx §1 `--steps readout` row + §2 recompute wording, data-contract §1 decision numbers + §5 payload/D2/D9/D12 scope, declarative-config comparison fields), ROADMAP annotated (BH pull-forward, Segment + decision-confirm + sidedness/winsorization deferrals, the ruamel comment-preserving-Apply backlog note), the statistics-changes.md D11 determinism note, `.claude/rules/architecture.md` "Not yet present" list updated, and the adversarial-review record appended as section 5 with all verified findings applied in one `fix:` commit.

---

## 5. Adversarial review record (M3 exit gate)

*Appended at M3 close (WP10) — six lenses proposed: recompute exactness/statistical binding, verdict-logic DoD audit, HTTP surface, config-write/orphaning integrity, payload/renderer self-containment, time & grids.*
