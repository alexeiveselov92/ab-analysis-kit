# Declarative config (YAML + SQL)

> The dbt/detectkit-style declarative model. Goal: an analyst defines an
> experiment and its metrics **without touching Python**, the way detectkit users
> define metrics. Everything correctness-critical (cohort join, window filter,
> per-unit dedup, alpha) is **packaged**, never hand-repeated.

## 1. Three config objects

| Object | File | Role |
|---|---|---|
| **Experiment** | `experiments/<name>.yml` | **Primary entity.** Assignment source, variants, unit key, the list of comparisons (metric × method) |
| **Metric** | `metrics/<name>.yml` (+ inline or `sql/`) | **Reusable library item** referenced by experiments by name |
| **Method** | inline in a comparison | The tunable statistical object; identified by `method_config_id` |

Globally-unique names per namespace are DB keys (validator-enforced).

## 2. Experiment YAML

```yaml
# experiments/dating_intro_v2.yml — THE PRIMARY ENTITY
name: dating_intro_v2            # globally unique (DB key); legacy exp_id
description: "Onboarding redesign for the dating intro funnel"
status: running                  # design | running | concluded | archived
is_actual: true                  # scheduled (Prefect) runs pick it up
start_ts:   2024-07-31           # PINNED left edge of every cumulative window; a bare
                                 # date is local midnight, a timestamp is the exact instant
horizon_ts: 2024-08-28           # planner horizon — the EXCLUSIVE right edge (this covers
                                 # through Aug 27); also drives the power plan
unit_key: user_id                # randomization + default analysis unit
cadence: 1d                      # cumulative cutoff step — any duration ("1h", "30m", "1d");
                                 # or a coarsening schedule (dense-early, the sanctioned
                                 # impatience path — see cumulative-intervals.md §6):
                                 #   cadence:
                                 #     - {every: 1h, until: 48h}
                                 #     - {every: 1d}
data_lag: 0                      # completeness watermark: data assumed complete through
                                 # now() - data_lag. REQUIRED when cadence < 1d (declare your
                                 # ingestion SLA); default 0 reproduces *_wo_curr_day at 1d
interval_anchor: midnight        # WHERE the cutoff lattice sits: midnight (default — local
                                 # midnight of the opening day) | start (count from start_ts)
                                 # | an explicit timestamp to align to an external cycle
                                 # (it MAY precede start_ts). Cutoffs = anchor + k*cadence,
                                 # kept strictly after start_ts
timezone: UTC                    # interprets bare-date edges, an explicit interval_anchor,
                                 # and the DST-safe day lattice; storage/comparison is UTC

assignment:                      # READ-ONLY exposure source (abkit does not randomize)
  query_file: sql/assignment.sql # must SELECT unit_key, variant, exposure_ts [, stratum]
  added_filters: ""              # optional extra SQL fragment (must start with AND); escape hatch
  cohort_copy:                   # opt-in persisted cohort copy (M8; named at WP1 — a field named
                                 # `copy` shadows pydantic's BaseModel.copy). Default off: metric
                                 # SQL joins the deduped assignment source directly and nothing is
                                 # persisted. Enable for a heavy multi-join source that is
                                 # APPEND-ONLY and monotone on update_column. KNOWN LIMITATION
                                 # (donor watermark model, m8 §4 Q3): a row backfilled/corrected
                                 # BELOW the watermark is silently and permanently missed by the
                                 # copy — a mutating source should stay on the no-copy default, or
                                 # recover with `abk run --resync-cohort` (full delete + reinsert).
                                 # When enabled, the assignment SQL MUST reference
                                 # {{ ab_added_filters }} — the incremental engine injects its
                                 # watermark batch bounds there (config-lint enforces it). Keep
                                 # data_lag >= maturity_delay + batch_interval, or the newest
                                 # cutoffs compute over a copy that does not yet cover them
                                 # (`abk run` warns when that happens).
    enabled: false               # true → persist into _ab_exposures incrementally (watermark +
                                 # closed-interval batches, the detectkit donor discipline; M8 WP5)
    update_column: exposure_ts   # watermark column the incremental copy filters on (must be a
                                 # plain identifier; existence is probed at run time). Only the
                                 # default exposure_ts carries a persisted resume cursor; a custom
                                 # column re-scans from the experiment start every run (still
                                 # batched, closed-intervals-only on that column)
    batch_interval: 1d           # closed-interval batch step of the copy loop
    batch_intervals_per_round_trip: 30   # intervals per load round trip (interval count, not rows)
    maturity_delay: 0            # ignore source rows younger than now() - maturity_delay (0 = none)
  variants: [control, treatment] # name_1 = the control; name_2 = treatment
  control: control               # OPTIONAL (m14 DEC-1): which arm is the baseline
                                 # (default: the first declared variant)
  expected_split: {control: 0.5, treatment: 0.5}   # drives the SRM chi-square gate

alpha: 0.05                      # experiment-level significance (see §6 — inspectable)
correction: bonferroni           # none | bonferroni (compute-time two-tier) | benjamini_hochberg | holm (read-time, §6.3)
sequential: {enabled: false, scheme: always_valid}   # opt-in peeking-correct CIs (default off = legacy)

notify:                          # OPTIONAL routing for `abk run --notify` (M12 NTF-1). Routing
                                 # only — omitting the block does NOT silence the experiment:
                                 # a notified run with no block sends to every configured
                                 # channel (D1). Never enters method_config_id.
  channels: [team_slack]         # subset of profiles.yml notification_channels (default: all)
  mentions: [growth-team]        # handles, rendered in each channel's own @-syntax
  on: [readout]                  # signal kinds this experiment sends (default: all). Composes
                                 # with a channel's own `on:` as an INTERSECTION. Kinds:
                                 # readout | verdict_change | srm | calibration_red | stale |
                                 # error — only `readout` fires as of NTF-1

readout:                         # READ-TIME verdict knobs (M3, plan D5) — never enter method_config_id
  stabilization_days: 7          # trailing elapsed-days window for "persistent significance"
                                 # (judged over elapsed time, never look count; default 7 = one weekly cycle)
  guardrail_policy: block        # block (default): a regressed guardrail caps WIN at INCONCLUSIVE;
                                 # warn: WIN is kept with a mandatory loud caveat (owner-ratified)

comparisons:                     # each binds a library metric to a method
  - metric: social_r1            # references metrics/social_r1.yml by name
    is_main_metric: true         # primary winner criterion (drives the two-tier Bonferroni)
    min_effect: 0.01             # optional: the business-meaningful effect, in the units of this
                                 # comparison's persisted effect (test_type-dependent); enables FLAT —
                                 # without it flat cannot be distinguished from underpowered (D5(b))
    method: {name: z-test, params: {test_type: relative, calculate_mde: true, power: 0.8}}
  - metric: arpu
    method: {name: cuped-t-test, params: {test_type: relative, covariate: prev_gross_usd, covariate_lookback: 14d}}
  - metric: avg_session_time
    method: {name: poisson-bootstrap, params: {test_type: relative, n_samples: 1000, stratify_by: [country]}}
  - metric: bottle_cr
    is_guardrail: true           # checked for regression, not for winning
    desired_direction: increase  # which effect sign is GOOD for this metric (default increase);
                                 # orients WIN/LOSE for mains and the regression check for guardrails
    method: {name: z-test, params: {test_type: relative}}
```

## 3. Metric YAML

```yaml
# metrics/arpu.yml — reusable, referenced by experiments
name: arpu                       # globally unique (DB key)
description: "Average revenue per user"
type: sample                     # fraction | sample | ratio
unit_key: user_id                # must match (or be inherited from) the experiment
tags: [revenue, guardrail]       # selectors apply 1:1 (name / path glob / tag:)
columns:                         # column-role mapping
  variant: group                 # arm label column
  value:   gross_usd             # per-unit value (type=sample)
  covariate: prev_gross_usd      # optional CUPED covariate
  stratum: country               # optional stratification key
# fraction-type → columns: {variant, count, nobs}
# ratio-type    → columns: {variant, numerator, denominator}

sql: |
  {% import 'abkit_assignment.jinja' as ab %}
  SELECT
      {{ ab.variant_col() }}      AS group,        -- arm from the persisted cohort
      user_id,
      sum(gross_usd)              AS gross_usd,     -- ADDITIVE: one row per unit
      any(country)                AS country
  FROM {{ data_database }}.user_revenue
  {{ ab.exposed_units() }}        -- JOIN the cohort (ab_cohort_source, M8) + window filter + dedup
  GROUP BY group, user_id         -- one row per unit; loader builds per-variant arrays / suffstats
```

**Contract:** a metric query returns **one row per unit** with additive
`sum`/`count` columns over `[ab_start_date, ab_end_date]`. The loader **guards**
this: if a query returns more rows than distinct `unit_key`s, it errors loudly
("did you forget `GROUP BY unit_key`? metrics must be one row per unit").

**CUPED covariate mechanics** *(amended in M2 WP5; supersedes the original
`ab.covariate_window()` sketch, which would have required conditional
aggregation — a plain `sum(gross_usd)` over an extended scan double-counts the
pre-period)*: when a comparison's method declares `covariate_lookback`, the
loader renders the **same metric SQL a second time** over the pre-period
window `[start_ts − lookback, start_ts)` with the exposure filter dropped
(`ab_apply_exposure_filter=false` — the pre-period precedes exposure by
construction), and the pre-period **value** becomes the covariate keyed by
unit (absent units → 0.0). This is exactly the legacy CUPED semantics — the
covariate is the same metric over the pre-period — with zero extra authoring.
An explicit `columns.covariate` role (a covariate column the author computes
in their own SQL, e.g. a snapshot) takes precedence and skips the second
render.

## 4. The packaged assignment macro (must-fix: no leaked boilerplate)

The legacy system factored cohort/window/dedup into `exp_users_macros.jinja`. abkit
**ships** an equivalent so a metric SQL describes *only* the metric aggregation:

- `ab.exposed_units(event_date_col='event_date', event_time_col='event_time')` —
  `JOIN`s `{{ ab_cohort_source }}` (the M8 mode switch — see the callout below),
  **deduped per dialect** (`FINAL` on ClickHouse in copy mode — a mid-merge
  ReplacingMergeTree must never yield a unit twice; a
  `MIN(exposure_ts)`-deduped `GROUP BY` in the live-subquery default; PG/MySQL
  enforce the PK in copy mode), and applies BOTH the coarse `event_date`
  predicate (Date partition pruning) and the precise half-open
  `event_time >= ab_start_ts AND event_time < ab_end_ts` filter plus
  `event_time >= exposure_ts` (dropped on the covariate pre-period render).
- `ab.variant_col()` / `ab.stratum_col()` — arm/stratum labels from the cohort.
- *(The `ab.covariate_window()` sketch is superseded by the two-render
  covariate mechanics in §3 — M2 WP5.)*

> **M8: `ab_cohort_source` is the one cohort-mode switch.** Default
> (`assignment.cohort_copy.enabled: false`): a live deduping subquery over the
> rendered assignment SQL, validated once per run, nothing persisted. With
> `cohort_copy.enabled: true`: the persisted `_ab_exposures` table (+ `FINAL`
> on ClickHouse). The packaged macro joins ONLY this builtin — metric authors
> never choose between the modes; `build_cohort_backend`
> (`abkit/loaders/exposure_source.py`) is the single place the branch lives.

Validation asserts the rendered SQL joins the cohort through the macro (present
identically in both modes); a metric authored without the macro fails
config-lint, so correctness-critical join/dedup logic is never silently
re-implemented by hand.

## 5. Jinja built-ins (authoritative, StrictUndefined)

Rendered by `loaders/query_template.py`; an undeclared variable hard-fails. Tested
against the scaffolded example so docs & examples cannot drift.

| Variable | Meaning |
|---|---|
| `ab_experiment_id` | experiment name |
| `ab_start_date` | **pinned** experiment start → cumulative left edge (date part) |
| `ab_end_date` | the **moving** cutoff (date part; partition-pruning predicate) |
| `ab_start_ts` / `ab_end_ts` | the precise UTC window bounds; `ab_end_ts` is **exclusive** (`event_time >= ab_start_ts AND event_time < ab_end_ts`) — the canonical filter at sub-day cadence |
| `ab_cov_start` / `ab_cov_end` | covariate window bounds (per the chosen lookback) |
| `ab_variants` | the variant list |
| `ab_unit_key` | the unit/randomization key |
| `ab_added_filters` | the experiment's optional SQL fragment |
| `data_database` / `internal_database` | profile-resolved schemas |
| `ab_cohort_source` | M8: the one cohort-mode switch — `ab_exposures_table` (+ `FINAL` on ClickHouse) under `assignment.cohort_copy.enabled`, else a live `MIN(exposure_ts)`-deduped subquery over the assignment SQL; the packaged macro's `exposed_units()` joins ONLY this |
| `ab_exposures_table` | the fully-qualified persisted cohort table name; kept for external/back-compat templates — the packaged macro reads the cohort through `ab_cohort_source` (M8) |
| `ab_dialect` | `clickhouse` \| `postgres` \| `mysql` (dialect-aware dedup in the macro) |
| `ab_apply_exposure_filter` | internal: `false` only on the covariate pre-period render |
| `ab.*` (macro) | `exposed_units()`, `variant_col()`, `stratum_col()` |

Built-ins **win** over caller context: a context key shadowing an `ab_*`
variable raises a render error (a silently shadowed `ab_end_ts` would change
the analysis window) — a deliberate, recorded deviation from the detectkit
donor's context-wins behaviour.

## 6. Alpha & multiple-testing (must-fix: inspectable, not hidden)

The legacy applied a **two-tier Bonferroni** at config time: `adjust_alpha(alpha,
groups, 1)` for the main metric, `adjust_alpha(alpha, groups, main_metrics_count)`
for the rest (`alpha / (C(groups,2) × metrics)`). abkit makes this **declared and
inspectable**:

- `alpha` + `correction` are declared at experiment (or project) level.
- `abk run` / `validate` / the HTML report **echo the effective per-comparison
  alpha** and the divisor in the `StageLogRenderer` — `C(groups,2) × metrics`
  for the default family, `(groups−1) × metrics` under `contrasts: vs_control`
  (§6.2), and the line names which family it counted.
- A golden test reproduces the exact two-tier scheme keyed off `is_main_metric`.
- The **read-time** schemes — Benjamini-Hochberg (FDR) and Holm (FWER, §6.3) —
  are applied across an experiment's metrics at every read; their interaction
  with peeking is documented in
  [aa-false-positive-matrix.md](aa-false-positive-matrix.md).

### 6.1 `guardrail_correction` — the guardrail tier (m13 STAT-1c, decision D8)

A **guardrail** exists to catch harm. Correcting its alpha therefore makes the
engine *less* able to do the one job the metric was declared for — the error
points the dangerous way, unlike an over-tight screening metric, which merely
costs power. Through `0.7.0` a guardrail shared the secondary Bonferroni budget
like any other non-main comparison.

`guardrail_correction` is declared at project (or experiment) level:

| Value | Behaviour |
|---|---|
| `inherit` (**default**) | the pre-`0.8.0` scheme — a guardrail shares the secondary tier |
| `none` | the guardrail is tested at the **raw** experiment alpha, and it leaves the secondary divisor |

**It is two changes, not one.** Under `none` a guardrail is not merely re-routed:
it stops counting towards `metrics_count`, so alpha *loosens for the screening
metrics that remain* in the tier. An experiment with one main, one screening and
one guardrail metric over two arms goes from `main 0.05 / secondary 0.025` to
`main 0.05 / secondary 0.05 / guardrail 0.05`.

Notes:

- The flip is **inert unless `correction: bonferroni`** — every other scheme
  already hands out the raw alpha at compute time.
- It is **opt-in** because it moves persisted numbers (`alpha`, the CI bounds and
  `reject` of guardrail and screening rows alike). Since alpha is deliberately
  outside `method_config_id`, changing it writes new-alpha rows into an existing
  series; `abk run --full-refresh` makes a series homogeneous again.
- The A/A calibration chip keys on the **effective** alpha, so existing
  `_ab_aa_runs` rows for guardrail metrics read `alpha_mismatch` until re-run.
- An experiment whose only non-main comparisons are guardrails has
  `metrics_count = 0` and therefore no secondary tier at all; the guardrail still
  gets the raw alpha, never the (tighter) main one.

### 6.2 `contrasts` — the declared family (m13 STAT-1b, decision D15)

Bonferroni divides by the number of comparisons you *claim*. Through `0.7.0`
abkit always claimed `C(g,2)` — every variant pair — and paid for the
treatment-vs-treatment contrasts even when the decision was "each treatment vs
the incumbent". Declaring the narrower family multiplies every tier's level by
`g/2`: ≈ +10 points of power at four arms (an 18% sample-size saving at fixed
MDE), +6 at three — more than Holm gives, for a config field rather than new
math.

`contrasts` is declared on the **experiment**:

| Value | Family | Divisor |
|---|---|---|
| `all_pairs` (**default**) | every variant pair — the pre-`0.8.0` behaviour | `C(g,2) × metrics` |
| `vs_control` | the `g−1` many-to-one contrasts against the control arm | `(g−1) × metrics` |

The **control is `assignment.control` when declared, else the first declared
variant** — resolved in exactly ONE place, `ExperimentConfig.control` (m14
DEC-1, §6.3), which this factory, the readout's verdicts and the SRM rollup all
read. STAT-1b ratified the positional convention and DEC-1 replaced the
resolution; the family declaration deliberately did not wait for it (D15) —
they are different declarations.

**It is one declaration with two halves.** Under `vs_control` the
treatment-vs-treatment pairs are also **not computed**: no `_ab_results` rows,
no verdicts, no read-time BH family members. Loosening the divisor while still
writing those rows would hand levels bought for `g−1` contrasts to a family of
`C(g,2)` — a false FWER claim in the dangerous direction; narrowing only the
enumeration would leave the experiment needlessly conservative. Both halves read
`ExperimentConfig.contrast_pairs()`, the one place under `abkit/` that may
enumerate arm *pairs* — before STAT-1b four modules each carried their own
`combinations(variants, 2)`, and the AST gate
(`tests/config/test_contrast_pairs_is_the_only_entry.py`) now forbids that call
outside the factory and stats-core's experiment-agnostic `compare(groups)`. The
gate models `combinations` calls, not every conceivable enumeration: the
readout's `control × treatments` slice is a deliberate, separately pinned
exemption, because a *verdict* has been control-vs-treatment by design since
m11 — a subset of both families.

Both halves are enforced where the rows are produced AND where they are read:
`readout.evaluate()` filters undeclared pairs itself, so a caller that reaches
it directly (a notebook, a future surface) cannot score a read-time BH family of
`C(g,2)` for an experiment that declared `g−1`; the report, dashboard, cockpit
and notification surfaces each keep their own copy of the filter because each
owes the operator its own loud line about what it dropped.

Notes:

- **Inert at two arms** (`C(2,2) = 1 = g−1`). The alpha half is inert unless
  `correction: bonferroni`; the *enumeration* half applies under every scheme,
  `none` included.
- **Widening the family backfills; narrowing it does not.** The planner's
  anti-join is complete at *(cutoff × declared pair)*: a look missing a declared
  pair is re-planned, so flipping back to `all_pairs` — or adding an arm —
  recomputes the affected looks and re-homogenises their alpha by LWW. The
  narrowing direction leaves the surviving rows at the old, *tighter* level
  (conservative, never anti-conservative); `abk run --full-refresh --from … --to …`
  re-homogenises those.
- **Under a read-time scheme (`benjamini_hochberg`, `holm`) the narrowing is
  retroactive**, so an already-published look is re-scored against the smaller
  family the next time it is read. That is self-consistent — every surface reads
  the same declaration — but it is a verdict that can change without a run, the
  same class as the D7 decision/interval divergence.
- The guardrail tier (§6.1) stays at the raw alpha under both families — a
  family it does not pay for cannot tighten it.
- **Opt-in, so no default moves** (m13 D1) and no `ALGORITHM_VERSION` is bumped
  (D4). Since alpha is outside `method_config_id`, flipping it writes new-alpha
  rows into an existing series; `abk run --full-refresh` re-homogenises it, and
  `_ab_aa_runs` rows keyed on the old effective alpha read `alpha_mismatch`
  until `abk validate` re-runs them.
- Rows already written for a pair the declared family no longer claims are
  **ignored loudly** by every read surface (report, dashboard, notifications).
  There are now **three** causes and they share one sentence
  (`experiment_config.UNDECLARED_PAIR_CAUSES`, since four surfaces each carried
  their own copy of the list): a renamed arm, a family narrowed to
  `vs_control`, and — since m14 DEC-1 — a declared `control:` that re-orients
  `(name_1, name_2)`. `abk clean` does **not** remove them (it prunes series by
  `method_config_id`); `abk run --full-refresh` does, because it deletes the
  window before rewriting the declared pairs — but note its `--to` bound is
  **exclusive** on `end_ts`, and since m10 the horizon cutoff's `end_ts` **is**
  `horizon_ts`, so `--to <horizon>` leaves the horizon look untouched. Pass a
  bound past the horizon (the bounds are parsed naive and compared against
  naive-UTC `end_ts`, while the YAML window is local).
- `contrasts` is deliberately **not** part of the m9 state identity: it changes
  which pairs are compared, never which units/days are materialised — the same
  reasoning that keeps `interval_anchor` out (m10).
- `_ab_experiments.contrasts` records the family for BI (added additively, so an
  existing install picks it up on its next run): without it a join would
  re-derive `C(variants, 2)` and land on a divisor no `_ab_results` row carries.

### 6.3 `assignment.control` — the declared baseline (m14 DEC-1)

`assignment.control` is an **optional** variant name; unset, the control is the
first declared variant, exactly as it was through `0.8.0`. It is validated at
level 1 to be one of `assignment.variants`, with a message naming both the
rejected value and the declared list (the realistic mistake is an arm renamed
on one line and not the other).

There is deliberately **no project-level default** — STAT-1b D16's reason, and
it binds harder here: the baseline a surface measures against must never depend
on whether that surface happened to resolve a `ProjectConfig`.

**One resolver, AST-gated.** `ExperimentConfig.control` (and `.treatments`, the
non-control arms in *declaration order* — not a `variants[1:]` slice, which
would drop the arms preceding a mid-list control from every verdict) is the one
place the convention is resolved, the fourth member of the family that already
holds `grid()` (m10), `contrast_pairs()` (m13) and `build_cohort_backend` (m8).
`tests/config/test_control_is_the_only_entry.py` forbids `variants[0]`,
`variants[1]` and `variants[1:]` anywhere under `abkit/` outside it.

The gate is its own work package because **seven** sites spelled the convention
and they do not fail alike: the family factory would pick the wrong pairs (the
wrong alphas), `readout.evaluate` would verdict against the wrong baseline,
`abk plan` would size against the wrong arm, `abk validate` would calibrate at
the wrong split ratio — and `readout._srm_from_series` would fail **silently**,
because every `(metric, control, treatment)` series lookup misses and a miss is
indistinguishable there from "no rows yet", so a broken assignment reads
healthy. A knob that reaches none of its call sites is the m10
`interval_anchor` failure; a knob whose missed call site turns a safety gate
quiet is worse.

**What a declaration moves, and what it does not.** `contrast_pairs()`
*orients* every pair containing the control as `(control, other)`; it never
adds or removes one, so the family size — and therefore the alpha divisor — is
untouched. Under the default the orientation is a **no-op by construction**:
`itertools.combinations` already emits `variants[0]` first in every pair that
contains it, so a `0.8.0` experiment reproduces row for row.

Declaring a **non-first** control on a running experiment is the one case with
consequences, and config-lint warns about all of them:

- the pairs containing it change `(name_1, name_2)`, so their persisted rows
  leave the declared set (the third cause above);
- the re-oriented pair's effect is measured against the other arm — on the
  absolute scale the **negation** of the old number, but on the **relative
  scale it is not**, because the denominator swaps arms too: `(m₂−m₁)/m₁`
  becomes `(m₁−m₂)/m₂`. `test_type: relative` is the common configuration, so
  an operator comparing an old chart against a new one will not find the sign
  flip they were promised;
- the next **plain `abk run` recomputes the whole series** — no
  `--full-refresh` needed, because no look carries the re-oriented pair and the
  anti-join is complete at *(cutoff × declared pair)*. Until it does, the
  read-time correction families are built from the surviving rows only, so a
  verdict read in between can be looser than the one that settles;
- the previous orientation's rows are **never deleted**. abkit's own surfaces
  drop them with a warning, but raw SQL over `_ab_results` sees BOTH
  orientations of that pair — join `_ab_experiments.control` to tell them apart.

`control` is deliberately **not** part of the m9 state identity (it moves which
pairs are compared, never which units or days are materialised — the
`interval_anchor` and `contrasts` precedent), and `_ab_experiments.control`
records the **resolved** baseline for BI. That column is `Nullable` rather than
defaulted: `ensure_columns` refuses a NOT-NULL/no-default addition (m13 STAT-6)
and no literal default could be right for a per-experiment variant name, so
NULL means exactly one thing — a row written before `0.9.0`.

The scaffold (`abk init`) does **not** write `control:`: an optional field
whose default is right is noise in a starter config (the `contrasts`
precedent).
  (`guardrail_correction` from STAT-1c is still absent from that catalog — a
  known gap, and the per-row `alpha` remains the authority in both cases.)
- Writing `contrasts:` under the project's `statistics:` block is a **loud
  error**, not a silent no-op — every neighbour there does have a project
  default, so the mistake is natural and a knob that accepts a value and changes
  nothing reads as a broken engine.
- **There is no project-level default**, unlike `correction` /
  `guardrail_correction`. The family a surface reads must never depend on
  whether that surface happened to resolve one, and the factory the five call
  sites share therefore needs no project config. It is also a statement about an
  experiment's *design* — which arms it compares — rather than a project-wide
  statistical policy.

### 6.3 `correction: holm` — the read-time FWER rule (m13 STAT-1, decisions D7/D9)

`correction` takes four values, and they split into two kinds:

| Value | Kind | What the persisted row carries |
|---|---|---|
| `none` | compute-time | the raw alpha; significance ≡ the CI excludes zero |
| `bonferroni` (**default**) | compute-time | the two-tier effective alpha (§6) |
| `benjamini_hochberg` | **read-time** (FDR) | the RAW alpha — the decision is recomputed over the family at every read |
| `holm` (new in `0.8.0`) | **read-time** (FWER ≤ α) | the RAW alpha — same |

Holm is the step-down rule: sort the family's p-values, reject `H_(i)` while
every step `j ≤ i` clears `α/(m−j+1)`. It controls the FWER at α under
**arbitrary dependence** — the same assumption-free guarantee Bonferroni gives —
and is uniformly more powerful than a one-step Bonferroni at the same α. It is
*not* uniformly more powerful than abkit's two-tier scheme, whose main tier sits
at `α/P`: that scheme reaches `2α` overall (statistics-changes §4.3(a)), and
Holm's α is the honest one.

**The family is one cutoff's informative rows** (metrics × declared pairs) — the
same membership BH uses, so a cumulative series is never treated as `looks × m`
tests. Peeking stays the sequential toggle's and `abk validate`'s business.

**Fork B (D7): a verdict and the interval stored beside it may legitimately
disagree, and the surfaces say so.** No fixed per-comparison level can reproduce
a step procedure (α=0.05, m=2, p₂=0.03: Holm rejects H₂ when p₁=0.001 and
refuses when p₁=0.9), so the choice was between forgoing every step procedure
and letting the decision leave the interval. abkit has been in Fork B under BH
since M3 without saying so; STAT-1 ratifies and documents it. The divergence is
one-directional (the family rule is never looser than the member's raw alpha),
so the visible case is *an interval excluding zero under a verdict that declines
to call it* — `readout.evaluate()` attaches an explicit caveat to exactly that
pair (and sets `PairVerdict.family_divergence`). The HTML report and `abk
dashboard` render the caveat; a notification renders its own sentence off the
flag, because it shows an interval beside a verdict with no report to click
through to. `abk explore` renders neither: it never calls `evaluate` and shows
uncorrected per-comparison inference by design (below).

Three read-time-only consequences, all conservative:

- **FLAT is withheld when the pair's own interval excludes zero.** "Nothing was
  significant" no longer implies "the interval covers zero", and FLAT is an
  affirmative claim of no meaningful effect — the readout answers INCONCLUSIVE
  and says why.
- **FLAT's power claim is disclosed as optimistic**: the MDE is solved at the
  row's raw alpha while the family threshold is tighter.
- **A `guardrail_correction: none` guardrail leaves this family too** (§6.1), so
  D8's second half — the divisor — applies at read time, where the family *is*
  the divisor. Its own regression check is unchanged and correction-independent.

Notes:

- **A family whose rows carry mixed alphas is warned about**: the rule then
  controls the error rate at the loosest of them (`abk run --full-refresh
  --from … --to …` re-homogenises the series).
- **`_ab_results.reject` is a pre-family flag**, not the composed decision
  (data-contract §1). It stays as-is — a published BI contract — and is
  documented as what it is. The family decision is not persisted at all: under a
  read-time scheme it exists only at read time, and a stored copy would go stale
  the moment a metric was added or the contrast set narrowed.
- `abk plan` sizes at the raw alpha under a read-time scheme and **says so** in
  its header: the decision threshold depends on the family's p-values, so the
  level printed there is the interval's, not the decision's.
- **Opt-in, so no default moves** (D1); no `ALGORITHM_VERSION` bump (nothing
  method-level changed). Flipping to `holm` from `bonferroni` writes raw-alpha
  rows into an existing series, exactly as flipping to `benjamini_hochberg`
  does; `abk run --full-refresh` re-homogenises it, and `_ab_aa_runs` cells keyed
  on the old effective alpha read `alpha_mismatch` until `abk validate` re-runs.
- The explore cockpit shows **uncorrected per-comparison** inference under every
  read-time scheme (it is a knob-turning surface, not a decision surface) and
  labels the α echo accordingly.
- A scheme is classified as compute-time or read-time in exactly one place
  (`stats.correction.READ_TIME_CORRECTIONS` / `COMPUTE_TIME_CORRECTIONS`), and a
  roster test asserts their union equals the config literal — a fifth scheme
  cannot be added on one side only.

## 7. `method_config_id` (must-fix: ONE canonical spec)

```
method_config_id = sha256( method_name              # registry name (NOT class name)
                         + json_dumps_sorted(params) # non-default params only; canonical JSON
                         + ALGORITHM_VERSION )       # appended only when > 1 (match detectkit)
```

- Pinned with a **byte-exact unit test** (the exact bytes hashed for a known
  method+params).
- **Seed policy (uniform):** `seed` is **excluded** from `method_config_id` for
  **all** bootstrap methods (stable per-day series identity); the param schema
  marks `seed` identity-excluded and rejects it for closed-form methods. Re-runs
  stay byte-stable via a deterministic per-row seed derived from
  `(exp, metric, name_1, name_2, end_ts, n_samples)` — see
  [statistics-changes.md](statistics-changes.md).
- Editing any identity-bearing param orphans the prior series (new id);
  `abk clean` GCs it, and `run`/`explore` warn when an experiment has >1 `method_config_id`
  for a metric ("the dashboard will show two stabilization lines — clean to resolve").

## 8. Validation matrix (`config/validator.py`)

Tested, fail-fast, two-level:

- every `comparison.metric` resolves to a `metrics/` file (no dangling refs);
- experiment & metric names unique within their namespace; explicit cross-namespace
  collision rule;
- `metric.unit_key` equals `experiment.unit_key` (or omitted → inherited);
- no duplicate metric refs within one experiment;
- method `name` ∈ registry, params ∈ that method's schema (e.g. CUPED requires a
  covariate; Poisson bootstrap is mean-only; paired requires aligned sizes);
- `is_main_metric` / `is_guardrail` not both true; at least one main metric;
- assignment SQL selects `unit_key`, `variant`, `exposure_ts`;
- `expected_split` variants ⊆ `assignment.variants`;
- **metric `aa_fpr_budget`** (optional; M4/D12) — a fraction in `(0, 1]`; the
  per-metric A/A false-positive budget the validate matrix colours this metric
  against, overriding `project.statistics.aa_fpr_budget` (resolution:
  metric → project → `α × 1.5`, `resolve_fpr_budget`);
- **cadence & looks** (cumulative-intervals.md §6): cadence parses to whole
  seconds ≥ 1s; schedule segments strictly coarsening with increasing `until`;
  planned looks > `max_looks` (project default 5000) → **error** (the only hard
  gate — there is deliberately NO time floor); looks > `warn_looks` (default
  100) without `sequential.enabled` → peeking warning quoting the look count and
  the measured A/A FPR for this grid; `cadence < 1d` requires `data_lag`;
  `cadence < 1d` with `scheme: alpha_spending` → error (mSPRT/always_valid is
  the sub-day path); `24h % cadence != 0` → drift warning; `cadence > horizon`
  → error; `covariate_lookback < 1d` → error, `< 7d` → warning;
- **`assignment.cohort_copy`** (optional; M8): when `enabled`, `update_column`
  must be a valid identifier (parse-time sanity check — real existence is a
  run-time column probe), and the assignment SQL must reference
  `{{ ab_added_filters }}` **live** — the incremental copy engine injects its
  batch bounds through that hook, and config-lint proves the reference by
  rendering a sentinel filter through it (a token parked in a comment cannot
  pass); missing → error with a fix hint.

A `abk run --steps validate` (config-lint) runs the full parse + reference
resolution + SQL render-smoke-test under StrictUndefined **without touching the DB**
— runnable in CI before any compute (the legacy `ExpMetricQueriesCheckingPipeline`).
