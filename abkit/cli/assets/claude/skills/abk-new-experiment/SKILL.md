---
name: abk-new-experiment
description: >-
  Scaffold a new abkit experiment as a validated YAML file under experiments/.
  Use when the user wants to add or create an A/B experiment, analyze a test or
  rollout, set up an experiment analysis, or compare variants on one or more
  metrics. Produces an experiments/<name>.yml that references existing metrics,
  passes config validation, and is ready to run with `abk run`.
---

# Create a new abkit experiment

Scaffold one experiment YAML that **validates and is ready to run**. An
experiment is the PRIMARY entity: it names a read-only assignment source, its
variants + expected split, the cumulative window + cadence, and a list of
comparisons binding reusable **metrics** to statistical **methods**. Work the
steps in order. Do not invent metric names — reference existing metrics or
scaffold them first. When you need field detail read the matching file under
`.claude/rules/ab-analysis-kit/` (`experiments.md`, `methods.md`, `metrics.md`,
`project.md`); this skill is the procedure, those are the reference.

## Step 0 — Confirm you're in a configured abkit project

A project root contains `abkit_project.yml`. Verify it (or find the nearest
ancestor). If there is none, stop and tell the user to run `abk init <name>`
first. Experiments go in `experiments/<name>.yml`.

If `profiles.yml` is still the `abk init` placeholder (or a run fails with
`internal_database must be set` / `Connection refused`), the database connection
comes first — use the **`abk-setup-project`** skill, then come back here. The
config-lint in Step 8 needs no database, but running the experiment does.

## Step 1 — Name and uniqueness

- Confirm the experiment `name`: lowercase snake_case, descriptive
  (`checkout_button_color`, not `test1`). Alphanumerics, `_`, `-` only.
- The file is `experiments/<name>.yml`; keep filename == `name`.
- **Uniqueness is mandatory and spans ONE namespace**: the name is the database
  key and must not collide with any experiment **or metric** in the project.
  Grep `experiments/` and `metrics/` for `name: <name>`; abort on a clash and
  suggest a more specific name.

## Step 2 — The assignment source (variants + split)

abkit **never randomizes** — it reads a read-only exposure table you point it at.
Gather from the user:

- The assignment SQL: one row per unit with its variant and first-exposure
  timestamp. Put it in `sql/<name>_assignment.sql` and reference it with
  `assignment.query_file:` (or inline `assignment.query:`). Mirror the scaffold's
  `sql/example_assignment.sql`: select the `unit_key`, `variant`, `exposure_ts`,
  and end the WHERE with `{{ ab_added_filters }}`.
- `variants:` — the arm names. **The FIRST is the control** (name_1); effects are
  measured against it.
- `expected_split:` — the intended share per variant; must name every variant
  and sum to 1.0. **This drives the SRM gate** — set it to the real intended
  allocation, not a guess.

## Step 3 — Choose or scaffold the metrics

Every comparison references a metric **by name** — never inline a query here.
List the metrics the user wants to judge the experiment on. For each:

- **Exists already** (`metrics/<metric>.yml`) → reference its name.
- **Does not exist** → stop and scaffold it with the **`abk-new-metric`** skill
  (it produces a one-row-per-unit SQL joined to the cohort macro), then return.

Do not fabricate a metric name that has no file — config-lint will reject the
dangling reference.

## Step 4 — Main vs secondary + comparisons

Build the `comparisons:` list — one entry per (metric × method):

- Exactly **at least one** comparison must set `is_main_metric: true`. It drives
  the WIN/LOSE/FLAT verdict and the **two-tier Bonferroni** (the main metric gets
  a laxer post-correction alpha than the secondary family).
- Mark regression-only metrics `is_guardrail: true` (a regressed guardrail caps a
  WIN — `readout.guardrail_policy: block` by default). A comparison cannot be both
  main and guardrail.
- Optional read-time verdict knobs (NOT method params, never in identity):
  `min_effect:` (the business-meaningful effect that enables the FLAT verdict —
  without it, flat is indistinguishable from underpowered) and
  `desired_direction:` (`increase` / `decrease`).

## Step 5 — Choose the method + params

Per comparison, pick a registered method (see `methods.md` for the decision
table). Common choices:

| Metric shape | Method | Notes |
|---|---|---|
| `fraction` (conversion) | `z-test` | proportions |
| `sample` (revenue, time) | `t-test` | means |
| `sample` + pre-period covariate | `cuped-t-test` | variance reduction; set `covariate_lookback` (e.g. `14d`) |
| `ratio` (per-session rate) | `ratio-delta` | delta-method CI |
| non-normal / custom statistic | `bootstrap` (+ paired/poisson/post-normed variants) | set `n_samples`; heavier |

Params live under `method.params`. Frequent ones: `test_type: relative|absolute`,
`calculate_mde: true`, `covariate_lookback: <dur>` (CUPED), `n_samples` +
`seed` (bootstrap). **Editing an identity param orphans the prior result series**
(`method_config_id` = hash of method + non-default identity params) — recompute
and `abk clean` prune the strays. `seed` and `alpha` are identity-EXCLUDED.

## Step 6 — Window, cadence, data_lag, alpha

- `start_date` (PINNED left edge of every cumulative window) and `end_date` (the
  planned **horizon** the power plan targets). Use `unit_key` for the analysis
  unit.
- `cadence:` — the cutoff step (`1d` default; or a dense-early schedule like
  `[{every: 1h, until: 48h}, {every: 1d}]`). **Cadence below `1d` REQUIRES
  `data_lag:`** (declare the ingestion SLA; use `data_lag: 0` only if data is
  truly complete in real time).
- `alpha:` / `correction:` — omit to inherit project defaults; `correction:
  bonferroni` gives the two-tier split, read-time BH is applied across a family.

## Step 7 — Optional: enable sequential (peeking-safe)

By default sequential is **OFF**: fixed-horizon CIs are not peeking-valid, so the
readout **withholds WIN/LOSE and FLAT before the horizon** (FLAT is equally a stop
decision; a daily series read early inflates false positives). If the user intends
to look early and possibly stop:

```yaml
sequential:
  enabled: true          # always-valid (anytime-valid) CIs; WIN/LOSE allowed pre-horizon
  # scheme: always_valid # default; scheme: alpha_spending (group-sequential) is NOT yet
                         # implemented — it refuses cleanly, with no version promise
```

Sequential is a peeking-safe MODE over the same effect/SE; toggling it re-plans
the series (a bare `abk run` re-plans). It does not change the method's numbers.

## Step 8 — Write the file, then validate

Write `experiments/<name>.yml` mirroring the scaffold's schema exactly. A typical
result:

```yaml
name: checkout_button_color
description: "Green vs blue primary CTA on checkout"
status: running
start_date: 2024-08-01      # PINNED window start
end_date: 2024-08-21        # horizon
unit_key: user_id
cadence: 1d

assignment:
  query_file: sql/checkout_button_color_assignment.sql
  variants: [control, treatment]                    # FIRST is control
  expected_split: {control: 0.5, treatment: 0.5}    # drives SRM

alpha: 0.05
correction: bonferroni

comparisons:
  - metric: checkout_cr
    is_main_metric: true
    method: {name: z-test, params: {test_type: relative, calculate_mde: true}}
  - metric: revenue_per_user
    method: {name: cuped-t-test, params: {test_type: relative, covariate_lookback: 14d}}
```

Then config-lint (round-trips the YAML through the real validator — macro import,
one-row-per-unit shape, method instantiation, cadence gates; **no database
needed**):

```bash
abk run --steps validate      # config lint — NOT `abk validate` (that is the A/A matrix)
```

Fix every reported error before declaring done. Re-check:

- [ ] `name` unique across experiments AND metrics; matches the filename.
- [ ] `assignment` has one of `query`/`query_file`, ≥2 unique variants, and an
      `expected_split` naming every variant that sums to 1.0.
- [ ] Every `comparisons[].metric` references a metric file that exists.
- [ ] Exactly ≥1 comparison sets `is_main_metric: true`; no main+guardrail clash.
- [ ] Each method name is registered and its params instantiate (no quarantined
      branch, no unknown param).
- [ ] `end_date >= start_date`; sub-day cadence has `data_lag`.

## Step 9 — Run it, and what's next

Report the created path and the commands to run it for real:

```bash
abk plan --select <name>                 # OPTIONAL pre-launch: required-N / MDE / power
abk run --select <name>                  # compute + write _ab_results (the BI table)
abk run --select <name> --report         # + a self-contained HTML readout
```

The CLI exits **non-zero** on failure. After a run, offer the follow-ups:

- **Is the method calibrated on this data?** → the **`abk-validate`** skill (the
  A/A false-positive + power matrix; lights the explore calibration chip).
- **Tune the method live** → the **`abk-explore`** skill (interactive cockpit,
  Apply back into the YAML).
- **Check SRM first, always** — a red `SRM FAILED` line means the assignment is
  broken and no effect is trustworthy until it's fixed.
