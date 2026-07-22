# The explore cockpit

`abk explore` is abkit's **priority interface**: a localhost cockpit where you
tune an experiment's compute method **live** — on its *real* persisted results —
watch the effect and its confidence interval stabilize, check whether the method
is A/A-calibrated, and, when you're happy, write the tuned config back to your
experiment YAML with one explicit Apply.

It is the interactive sibling of [`abk validate`](validate.md) (A/A calibration)
and [`abk plan`](plan.md) (pre-launch sizing). Where `abk run` computes results
in batch and `abk validate` measures false-positive cost, **explore is where you
decide what the method should be** — by playing with it, not by editing YAML
blind and re-running.

> Governing contracts: `data-contract-and-reporting.md §5` and `cli-and-dx.md §2`.

## Run it

```bash
abk explore --select signup_test              # serve the cockpit for one experiment
abk explore -s signup_test --metric arpu      # open on a specific comparison
abk explore -s signup_test --no-serve         # write a static read-only HTML and exit
abk explore -s signup_test --no-open          # serve but don't launch a browser
abk explore -s signup_test --profile staging  # use a named profile
```

The full flag set (confirmed against `abkit/cli/main.py`):

| Flag | Meaning |
|---|---|
| `--select` / `-s` | Experiment selector — a name, path glob, `tag:<tag>`, or `*`. **Must resolve to exactly one experiment.** A multi-match is an error that names the candidates, not a menu. |
| `--metric` | Open the cockpit on this comparison instead of the main metric. Must name a configured comparison of the selected experiment. |
| `--profile` | Profile name (default: the `default_profile` from `profiles.yml`). |
| `--no-serve` | Write a static snapshot to `reports/<experiment>__explore.html` instead of serving. Apply is disabled. |
| `--no-open` | Do not launch a browser (the localhost URL still prints so you can open it yourself). |

There is no `--alpha` / `--test-type` / `--iterations` flag here — the knobs live
**in the page**, not on the command line. explore is a UI, and every parameter is
tuned interactively.

## Before you explore: run first

explore **reads persisted `_ab_results` rows** and recomputes method params over
them in memory. It never runs the pipeline, **takes no pipeline lock**, and never
writes to the warehouse. So the one prerequisite is that results exist:

```bash
abk run --select signup_test    # compute + persist the results series
abk explore --select signup_test
```

On a project with no computed rows yet, explore prints a friendly noop telling
you to run `abk run` first, and exits — it does not create schema or invent data
(`data-contract-and-reporting.md §5.1`, D2).

Freshness is whatever your last `abk run` produced. The cockpit header shows the
latest `end_ts` / watermark so staleness is always visible. **explore is a
retuning surface, not a live monitor** — it shows you what actually ran and lets
you ask "what if the method were different?", not "what happened five minutes
ago?".

## The cockpit

### Windshield — the stabilization chart

One big chart dominates the page: the **cumulative effect with its shrinking
confidence interval** as sample accrues over `elapsed_days`. This is the honest
picture of an experiment maturing — a point estimate wandering inside a CI that
tightens toward the horizon.

Pinned live chips ride over the chart and update on every knob change:

- **Estimated lift** and **CI half-width** — the effect and its precision.
- **p-value** and **current power**.
- **A/A calibration (real α)** — the calibration chip (see below).
- **SRM flag** — the sample-ratio-mismatch gate.

Pre-horizon fixed-CI points render **de-emphasized**: with the default
fixed-horizon method, reading significance before the planned end is peeking, and
the chart says so visually. (Always-valid CIs, opt-in via `sequential.enabled:
true` (`scheme: always_valid`), change this — see
[sequential analysis](sequential.md).)

### Side rail — the knobs (Basic / Advanced)

The knobs are **auto-derived from the live method's parameter specs**, so the
rail always matches the method that's actually selected. It has two disclosure
levels (`cli-and-dx.md §2`):

- **Basic** — the median-analyst surface: the method picker (choose `cuped-t-test`
  for CUPED), `test_type`, and `alpha`.
- **Advanced** — the full ~9-knob set: covariate + `covariate_lookback`,
  stratification keys, bootstrap iterations, `correction`, analysis unit
  (preview-only).

What is **not** on the rail: sidedness and winsorization. The shipped stats core
has no such parameters (p-values are two-sided; there is no winsor param), and
the rail refuses to fake UI against math that doesn't exist
(`data-contract-and-reporting.md §5.1`, D12).

### Modes

The cockpit offers **Tune** (knobs lead), **Auto** (run `validate` server-side
and re-seed the knobs), and **Review** (mark guardrail vs primary, confirm the
decision). A fourth entry, **Segment** (heterogeneous effects), appears in the
rail but is **inert** — it is deferred (D9, ROADMAP) and does nothing yet.

## Live recompute — one source of math truth

Every knob change recomputes through the **Python `from_suffstats` /
`from_samples` path** — the exact stats core the pipeline uses. There is **no JS
stats fork and no DB round-trip** for a normal tune, so the numbers you see in the
cockpit are the numbers `abk run` would produce. The cost of a tune depends on
which knob you turned (`recompute.py` tiers E/α/S/R):

| Tier | Turning this knob… | Recomputes by… |
|---|---|---|
| **E** exact | `test_type`, `alpha`, most closed-form params — incl. every CUPED knob except `covariate_lookback` (0.4.0) | Reconstructing suffstats from the persisted rows — the whole grid, instantly. |
| **α** | `alpha` on rows that cannot reconstruct (e.g. CUPED rows written before 0.4.0) | Alpha-inversion on closed-form rows (approximate) — the whole grid. |
| **S** session cache | anything needing raw samples (e.g. bootstrap) | `from_samples` over a bounded in-memory cache of cutoffs. |
| **R** reload | `covariate_lookback`, or turning CUPED on with no cached covariate | Flagged `R`; a confirmed, serialized warehouse re-read on demand. |

At startup, explore does **exactly one read-only, lock-free load pass** to fill
the bounded Tier-S cache. If the experiment is larger than the cache budget, the
cockpit degrades **honestly** to a suffstats-only surface (a smaller live
recompute set) rather than silently caching a partial slice.

## Calibration is always visible; Apply is gated

The single most important safety property of the cockpit: **you cannot silently
ship a mis-calibrated method.**

The **A/A calibration chip** lives on the windshield, never on a separate screen.
It keys by `(metric, method_config_id, effective alpha)` and reads the
`_ab_aa_runs` table that [`abk validate`](validate.md) (or Auto mode) populates.
It has three states (`tuning/recompute.py`):

- **`calibrated`** — an A/A run exists for these exact params at this effective
  alpha, and its measured false-positive rate is shown against nominal α.
- **`uncalibrated`** — no matching A/A run. Not a bug: it just means nobody has
  measured the FPR of *this* method at *this* alpha yet.
- **`alpha_mismatch`** — a run exists, but at a different effective alpha (it gates
  like uncalibrated).

Because method identity is `method_config_id`-keyed, **tuning the method usually
moves the chip to `uncalibrated`** — you've changed the thing whose FPR was
measured. That is expected, and it's exactly what the gate is for.

### The `confirm_uncalibrated` gate

**Apply is gated when the active params are uncalibrated.** If you try to Apply a
method that hasn't passed `validate` at this effective alpha, the server refuses
unless you explicitly take the `confirm_uncalibrated` path — you must acknowledge
that you're shipping a method whose false-positive cost is unmeasured
(`tuning/server.py`). It is a visible, deliberate override, not a hard block: the
anti-footgun is that shipping mis-calibrated is *possible but never silent*.

### Auto mode

To green the chip without leaving the cockpit, switch to **Auto**: it runs
`validate` server-side at a reduced iteration count, mutates the session's A/A
rows in place, and flips the live chip to `calibrated` — no explore restart. Auto
is for the tuning loop; to make the calibration **stick for the whole team**, run
`abk validate` for real so the `_ab_aa_runs` rows persist to the warehouse.

## Apply — the only write-back

Nothing is written while you tune. **Apply is the sole mutation seam**, it is
always explicit, and its order is **validate → archive → re-emit**
(`explore.md` operator rule; `data-contract-and-reporting.md §5.1`):

1. **Validate** — the edited config is validated as a whole (`ExperimentConfig`)
   before anything is written.
2. **Archive** — the previous YAML is copied verbatim to
   `experiments/.history/<experiment>/` (a timestamped copy; `.history/` is
   excluded from discovery, so an archive never collides as a duplicate name).
3. **Re-emit** — the experiment YAML is rewritten with the tuned `method_params`
   **merged in**. Only the comparison(s) you actually tuned change; every other
   comparison is preserved. The re-emit epilogue names what was updated vs
   preserved.

**Caveat: re-emit uses `safe_dump`, so YAML comments are lost on Apply.** The
verbatim `.history/` archive is your recovery path — restore from there if you
need the original file with its comments.

**Apply does not run the pipeline.** After you Apply, compute the new series under
the new params:

```bash
abk run --select signup_test
```

If you `--no-serve`, the page is a static read-only snapshot and **Apply is
disabled** (there is no server to tune against or write back). Serve normally to
tune and Apply.

## Orphan detection — editing method params starts a new series

Method identity (`method_config_id`) is a hash of the method plus its non-default
**identity** parameters. `alpha` and `seed` are **excluded** from that hash, so
changing them re-uses the existing series. Changing any *identity* param — a
different `test_type`, turning CUPED on, changing `covariate_lookback` — mints a
**new** `method_config_id` and **strands the old rows**. If both series linger,
your BI stabilization chart shows two lines for one metric.

explore warns about this in two places so it never surprises you:

- **At startup** — if `_ab_results` already holds more than one
  `method_config_id` for a metric, a yellow warning suggests `abk clean`.
- **After Apply** — if the params you just applied orphan the prior series, the
  epilogue says so and points at `abk clean`.

The clean-up sequence after any identity-param change:

```bash
abk run --select signup_test      # compute the new series under the new params
abk clean --select signup_test    # prune the orphaned old series (dry-run by default)
```

## Gotchas

- **No rows ⇒ noop.** explore reads persisted results; run `abk run` first.
- **An uncalibrated chip is not a bug.** It means no A/A run matches these params
  at this effective alpha — run [`abk validate`](validate.md) (or Auto mode).
- **Apply loses comments; the archive keeps them.** Recover from
  `experiments/.history/<experiment>/` if you need the original file.
- **Retuning strands old rows.** After any identity-param change, recompute
  (`abk run`) then `abk clean`, or BI shows duplicate stabilization lines.
- **SRM still gates.** A tuned significant effect on an SRM-failed experiment is
  not trustworthy — the flag rides on the windshield; fix the assignment first.

## See also

- [`abk validate`](validate.md) — the A/A false-positive matrix that lights the
  calibration chip.
- [`abk plan`](plan.md) — pre-launch power and sample-size planning.
- [The HTML readout](reading-a-readout.md) — `abk run --report`, the shareable
  offline report.
- [Metrics](metrics.md) and [experiments](experiments.md) — the declarative
  config the cockpit writes back to.
