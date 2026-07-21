# abkit — the explore cockpit (`abk explore`)

The interactive, chart-first **cockpit** for tuning an experiment's compute
method on its **real** persisted results. The manual sibling of `abk validate`
(A/A calibration) and `abk plan` (pre-launch sizing). Spec:
`data-contract-and-reporting.md §5`, `cli-and-dx.md §2`.

```bash
abk explore --select <experiment>            # serve the localhost cockpit
abk explore -s <experiment> --metric <name>  # open on one comparison, not the main metric
abk explore -s <experiment> --no-serve       # static read-only HTML (Apply disabled)
abk explore -s <experiment> --no-open        # serve but don't launch a browser (URL still prints)
abk explore -s <experiment> --profile staging
```

`--select` must resolve to **exactly one experiment** (a name, path glob,
`tag:<tag>`, or `*`) — a multi-match is an error, not a menu. `--metric` must
name a configured **comparison** of that experiment (default: the main metric).

## What it is (and is not)

- It **reads persisted `_ab_results` rows** and recomputes method params **live**
  — it never runs the pipeline, takes **no pipeline lock**, and never writes to
  the warehouse. Run `abk run --select <exp>` **first**: no computed rows ⇒ a
  friendly noop telling you to run first.
- **A broken live assignment source blocks the cockpit too** (M8): at
  session-load explore resolves the cohort through the same
  `build_cohort_backend` factory as `abk run` (the SRM chip's count source) —
  in the default no-copy mode a source that empties or corrupts fails with a
  clean, actionable error naming the fix, never a raw traceback.
- Freshness is whatever the last `abk run` produced; the header shows the latest
  `end_ts` / watermark so staleness is visible. It is a **retuning** surface, not
  a live monitor.
- It is **not** A/A calibration (`abk validate`) and **not** a config lint
  (`abk run --steps validate`). The calibration chip is *shown* here; the numbers
  behind it come from `abk validate` (or the in-cockpit **Auto** mode).

## The cockpit

- **Windshield** — one big chart: the **cumulative effect + CI stabilization**
  series (the point estimate with its CI shrinking as sample accrues over
  `elapsed_days`). Pinned live chips ride over it: estimated lift, CI half-width,
  p-value, current power, the **A/A calibration (real α)** chip, and the **SRM
  flag**. Pre-horizon fixed CIs render de-emphasized (peeking is not free).
- **Side rail (mode-aware, Basic / Advanced disclosure)** — the knobs, auto-derived
  from the live method's `param_specs`. **Basic** shows the median-analyst surface
  (the method picker — pick `cuped-t-test` for CUPED — plus `test_type` and `alpha`);
  **Advanced** opens the full ~9-knob set:
  covariate + `covariate_lookback`, stratification keys, bootstrap iterations,
  `correction`, analysis unit (preview-only). Sidedness and winsorization are
  **not** here — the stats core has no such params (two-sided p-values, no winsor).
- **Modes** — Tune (knobs lead) / Auto (run `validate` server-side, re-seed the
  knobs, green the chip in place) / Review (mark guardrail vs primary, confirm the
  decision). *(Segment / heterogeneous-effects mode is a deferred placeholder — not
  available in 0.1.0.)*

## Live recompute — one source of math truth

Every knob change recomputes through the **Python `from_suffstats` / `from_samples`
path** — the same stats core the pipeline uses. There is **no JS stats fork and no
DB round-trip** for a normal tune. Cost depends on which knob you turned:

| Tier | Knob → | How it recomputes |
|---|---|---|
| **E** exact | `test_type`, most closed-form params | suffstats reconstructed from persisted rows, whole grid |
| **α** | experiment-level `alpha` | alpha-inversion on closed-form rows (approx), whole grid |
| **S** session cache | needs raw samples (e.g. bootstrap) | `from_samples` over the bounded session cache (cached cutoffs) |
| **R** reload | CUPED off→on with no cached covariate | flagged `R`; a serialized `/reload` re-reads the warehouse on demand |

The one session-load pass at startup (lock-free, read-only) fills a **bounded**
Tier-S cache. Over budget ⇒ honest suffstats-only degradation (a smaller live
surface), never a silently partial cache.

## Calibration is always visible; Apply is gated

- The **A/A real-α chip** lives in the cockpit, never a separate screen. It keys
  by `(metric, method_config_id, effective alpha)` and reads `_ab_aa_runs`
  (populated by `abk validate` or Auto mode). States: `calibrated`,
  `uncalibrated` (no matching A/A run), `alpha_mismatch` (a run exists at a
  different effective alpha).
- **Apply is gated when uncalibrated.** If the active params have not passed
  `validate`, Apply takes the `confirm_uncalibrated` path — you must explicitly
  confirm you are shipping a method whose false-positive cost is unmeasured. This
  is the anti-footgun: you cannot silently ship a mis-calibrated method.
- **Auto mode** runs `validate` server-side (reduced N), mutates the session's A/A
  rows in place, and flips the live chip to `calibrated` without an explore
  restart. To make it stick for the whole team, run `abk validate` for real.

## Apply — the only write-back

Apply is **explicit** (nothing is written while you tune) and is the sole mutation
seam. Order is **validate → archive → re-emit**:

1. The edited config is validated as a whole (`ExperimentConfig`) before anything
   is written.
2. The previous YAML is archived verbatim to `experiments/.history/<experiment>/`
   (a timestamped copy; `.history/` is excluded from discovery, so an archive
   never collides as a "duplicate name").
3. The experiment YAML is re-emitted with the tuned `method_params` **merged** —
   only the comparison(s) you tuned change; other comparisons are preserved. The
   re-emitted header names what was updated vs preserved.

Caveat: re-emit uses `safe_dump` today, so **YAML comments are lost** on Apply —
the verbatim `.history/` archive is the recovery path.

**Apply does not run the pipeline.** After Apply, run
`abk run --select <experiment>` to compute the new series under the new params.

## Orphan detection (editing method params starts a new series)

Method identity (`method_config_id`) is a hash of the method + its non-default
**identity** params (`alpha` and `seed` are excluded). Changing an identity param
starts a **new** results series and strands the old rows — the BI stabilization
chart would then show duplicate lines. explore warns about this in two places:

- **At startup** — if `_ab_results` already holds more than one `method_config_id`
  for a metric, a yellow warning suggests `abk clean`.
- **After Apply** — if the tuned params orphan the prior series, the epilogue says
  so and points at `abk clean`.

After retuning: `abk run --select <exp>` to compute the new series, then
`abk clean --select <exp>` to prune the orphaned old series.

## `--no-serve`

Writes a static read-only snapshot to `reports/<experiment>__explore.html`
(self-contained, offline — nothing leaves the page) and exits. **Apply is
disabled** in the static page (there is no server to tune against or write back);
serve normally to tune.

## Gotchas

- **No rows ⇒ noop.** explore reads persisted results; run `abk run` first.
- **The chip needs `abk validate`.** An uncalibrated chip is not a bug — it means
  no A/A run matches these params at this effective alpha. Run `validate` (or Auto).
- **Apply loses comments; the archive keeps them.** Recover from
  `experiments/.history/<experiment>/` if you need the original file.
- **Retuning strands old rows.** Recompute (`abk run`) then `abk clean` after any
  identity-param change, or BI shows duplicate stabilization lines.
- **SRM still gates.** A tuned significant effect on an SRM-failed experiment is
  not trustworthy — the flag rides on the windshield; fix the assignment first.

> Installed by `abk init-claude`; tracks the installed abkit version.
