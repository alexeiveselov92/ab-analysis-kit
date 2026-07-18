# M10 Implementation Plan — timestamps + schema cleanup + explore polish

> **Status: as-designed contract for M10 (track approved 2026-07-18), targets
> release `0.5.0`. NOT yet implemented** — this document is the contract the
> implementation sessions execute, in the shape of
> [m4-implementation-plan.md](m4-implementation-plan.md) /
> [m6-implementation-plan.md](m6-implementation-plan.md). It becomes the
> implementation record at the exit gate (the m4–m6 pattern): as each WP lands,
> its section is annotated done and a §5 adversarial-review record is appended,
> mirroring [m4](m4-implementation-plan.md)/[m6](m6-implementation-plan.md).
> This document must never claim unbuilt code exists — every WP is written in
> contract/future tense ("WP2 adds…", "the gate asserts…").
>
> Governing specs: [cumulative-intervals.md](cumulative-intervals.md) (§6 the
> window-column contract, the CUPED whole-day pre-period rule),
> [declarative-config.md](declarative-config.md) (§3 the `start_date`/`end_date`
> config fields, the `ab_start_date`/`ab_end_date` SQL builtins),
> [data-contract-and-reporting.md](data-contract-and-reporting.md) (§5 the
> `_ab_results` window columns), [ROADMAP.md](../../ROADMAP.md) M10. Sibling
> milestone docs: [m9-implementation-plan.md](m9-implementation-plan.md) (M10
> depends on nothing from M9 code-wise, but M9's `build_cohort_backend`
> discipline is the pattern M10 does *not* touch),
> [m11-implementation-plan.md](m11-implementation-plan.md) (clones
> `tuning/server.py` **after** this milestone's WP4 lands, inheriting the
> decoupled lock model).
>
> Source: `~/.claude/plans/report-md-replicated-truffle.md` (the approved
> polish-track plan, M10 section) + the canonical detailed WP breakdown
> `~/.claude/plans/abkit-v2-details/design_time_explore.json`, cross-checked
> against `~/.claude/plans/abkit-v2-details/verify_time_explore.json` (code-verified
> file:line facts, treated as ground truth for citations in this document).

## 0. Scope, posture & decisions

**M10 covers REPORT.md #9–#12**: sub-day experiment start/horizon timestamps,
both real schema breaks of the whole polish track collected into one release,
and the two live `abk explore` performance/concurrency defects (the single
lock that queues a cheap knob turn behind a slow Reload/Auto-validate, and the
missing memoization of bootstrap resampling across alpha-only changes).

**Goal (from the canonical WP breakdown, lightly compressed):** let an experiment's
start/horizon be a real point in time instead of a calendar day — fixing the
"gridsteps are sub-day but anchors are not" contradiction flagged in
REPORT.md §8 — while keeping every existing date-anchored config
byte-identical; delete the two derived, unread `start_date`/`end_date`
columns from `_ab_results` and point every doc/hint reference at `end_ts`
instead; and fix the single-lock serialization and missing bootstrap-memo
defects in `abk explore` found in REPORT.md §7.

### 0.1 Posture: statistical numbers do not move anywhere

M10 is one of the M7–M12 core milestones under the track's hard rule:
**no statistical number moves in this milestone.** Concretely:

- No `ALGORITHM_VERSION` bump anywhere — a repo-wide grep for
  `ALGORITHM_VERSION` changes stays empty at the exit gate.
- No `docs/specs/statistics-changes.md` entry is needed for any of the five
  WPs — call this out explicitly in each WP's own exit checklist so a
  reviewer doesn't reflexively demand one (this is itself one of the design
  JSON's stated dependencies).
- WP5's bootstrap-class split (`from_samples` → `_resample` + `_finalize`) is
  a **pure refactor** of `abkit.stats` — same inputs, same outputs, parity
  gate at the class level — not a numeric change, and not exempt from the
  purity invariant (`abkit.stats` stays numpy/scipy/statsmodels + stdlib
  only; `tests/stats/test_purity.py` is untouched by this milestone).
- Parity/golden gates for M10 specifically: **byte-identical** grids/numbers
  for every existing bare-date fixture (an exact-equality gate, not
  rel-1e-9 — there is no floating-point path being changed in WP1/WP2, so
  "byte-identical" is the honest, stronger claim here), plus the standard
  rel-1e-9 continuous-value tolerance wherever a new numeric surface is
  exercised (e.g. the bootstrap memo parity test).

### 0.2 The one choke point: `generate_grid`

Every one of the roughly nine consumers of `ExperimentConfig.start_date`/
`end_date` goes through the single shared enumeration function
`generate_grid(start_date, end_date, cadence_segments, tz, limit=None)`
(`abkit/core/period_planner.py:71-165`) — this is **the** place a
`date`→`date | datetime` signature change must land, and it is why WP1 is
scoped alone before any downstream consumer is touched. The verified call
sites (file:line, from the code-verified facts register) are:

1. Grid generation itself — `abkit/core/period_planner.py:72-99`.
2. Config-lint L2 gates — `abkit/config/validator.py:284-300` (max_looks/
   warn_looks) and the SQL render-smoke at `abkit/config/validator.py:331`
   (`datetime.combine(experiment.start_date, datetime.min.time())`).
3. `abk validate` — `abkit/cli/commands/validate.py:167-171`.
4. The pipeline driver (run/compute) — `abkit/pipeline/driver.py:188-193`.
5. `abk plan` sizing + runtime/ASN — `abkit/cli/commands/plan.py:141-146,
   158-159`, consumed by `_build_runtime`/`runtime_for`/`asn_for` at
   `plan.py:335-393`.
6. Reporting readout series — `abkit/reporting/builder.py:341-346`.
7. Explore session load — `abkit/tuning/session.py:134-139`.
8. CUPED pre-period window — `abkit/compute/recompute_backend.py:82-94`
   (`self._experiment.start_date - timedelta(days=lookback_days)`,
   tz-midnight-snapped, ending at `grid.start_ts`).
9. The sequential/weekly-cycle chip — indirectly, via `window_seconds =
   cutoff.end_ts - grid.start_ts` (`abkit/pipeline/enrich.py:56`), propagated
   through `weekly_cycle_pct = elapsed_days / WEEKLY_CYCLE_DAYS`
   (`abkit/pipeline/readout.py:519-523`) and surfaced in
   `abkit/reporting/builder.py:179,184` and `abkit/notify/base.py:172-174`.

Only sites 2 (the render-smoke `datetime.combine` call) and 8 (the CUPED
lookback) reimplement date arithmetic directly against
`start_date`/`end_date` rather than only forwarding the fields into
`generate_grid`; those two get code changes in WP2. Sites 3–7 and 9 need
**no code change at all** — they only pass `experiment.start_date`/
`experiment.end_date` straight through, so WP1 generalizing `generate_grid`
generalizes them for free. Their existing test suites are therefore the
regression gate for "nothing else needed to change," not a set of tests to
rewrite.

### 0.3 Both track-wide schema breaks land in this one release

Per the track's decided schema policy: additive columns (M9) auto-`ALTER ADD
COLUMN` in `ensure_tables`, no migration tooling anywhere, and **both real
breaking changes of the whole 0.2.0→0.12.0 track are collected into this one
M10 release** so operators get one recreate instruction, once, instead of
scattered breaking notes across milestones:

1. **Drop** the two derived, unread `start_date`/`end_date` `Date` columns
   from `_ab_results` (WP3).
2. **Widen** `_ab_experiments.start_date`/`end_date` from `Date` to
   `DateTime64(3)` (WP2) — a genuine discovery made while implementing WP1's
   type widen (`_ab_experiments` mirrors the config field directly:
   `experiment_config.py:462` → `pipeline/driver.py:177` → `upsert_experiment`),
   not itself named in REPORT.md §8's explicit scope, but a real
   silent-truncation bug the type widen would otherwise introduce into an
   informational catalog table.

Both breaks ship as **CHANGELOG breaking-change notes + drop/recreate
guidance** (matching `ensure_tables()`'s existing create-if-not-exists-only
posture) — no `ALTER TABLE … DROP COLUMN` helper command, no migration
tooling. WP2 and WP3 sequence their CHANGELOG entries into one combined
"drop and recreate `_ab_experiments`/`_ab_results` after upgrading"
instruction rather than two separate scattered notes.

### 0.4 Plan-review record — corrections carried into this contract

Before this WP breakdown was finalized, five specific correction points were
raised and are binding requirements on the implementation, layered onto the
canonical design JSON's own step-by-step content (not replacing it):

- **(a) The `date | datetime` field is a UNION, not a coercion, and the
  before-validator must reject raw non-`str`/`date`/`datetime` scalars.**
  Discriminate the resolved runtime type with `type(x) is date` (never
  `isinstance`, since `datetime` subclasses `date` and `isinstance` would
  always read `True`). Beyond the sniff the design JSON already specifies
  (string length/format sniff to choose `date.fromisoformat` vs
  `datetime.fromisoformat`), the `mode="before"` validator must explicitly
  **raise** on a raw scalar that is not a `str`, `date`, or `datetime` —
  because Pydantic v2's `datetime` union member, left to its own coercion
  rules, interprets a bare `int`/`float` as a Unix timestamp. An unquoted
  YAML scalar like `start_date: 20240101` (no dashes, no quotes) parses via
  PyYAML as the Python `int` `20240101`, and without an explicit guard the
  union would silently accept it as a `datetime` **≈1970-08-24** (20240101
  seconds after the epoch) rather than erroring — a silent, wildly-wrong
  experiment start. WP1 adds a dedicated regression test pinning this exact
  failure mode: `start_date: 20240101` (raw int) must raise
  `ValidationError`, never resolve to an epoch-adjacent datetime.
- **(b) A bare `date` keeps byte-identical behavior; existing tests passing
  UNMODIFIED is the compatibility gate**, not a new byte-equality assertion
  written after the fact. Every currently-green test in
  `tests/core/test_period_planner.py` and `tests/config/test_experiment_config.py`
  must still pass **without being edited** once WP1 lands.
- **(c) Both schema breaks land here, in one release, with one recreate
  guide** — see §0.3 above; WP2's `_ab_experiments` widen and WP3's
  `_ab_results` column drop are sequenced into a single CHANGELOG
  breaking-change narrative, not two independent notes.
- **(d) Day-cadence with an explicit time anchors wall-clock (DST-safe).**
  When an experiment's `start_date` carries an explicit time-of-day, daily/
  weekly cadence segments anchor at that same **local wall-clock time** every
  day (mirroring the existing DST-safe midnight-snap machinery,
  `tz_midnight_utc`/the Moscow-midnight and EST/EDT tests) rather than
  silently sliding back to midnight. At `time.min` (every existing bare-date
  config) this degrades to today's exact behavior — strictly additive. This
  is WP1's resolution of the day-cadence design fork (§4 below still records
  it as needing explicit maintainer sign-off before merge, per the design
  JSON's own risk note, since it is a genuine extrapolation beyond
  REPORT.md §8's literal scope).
- **(e) Lock decoupling is scoped precisely.** `heavy_lock` (the renamed
  `request_lock`) guards **only** `/reload`, `/validate`, and `/apply`
  (unchanged mutual exclusion among those three); `/recompute` drops the lock
  entirely and gains a **post-compute** stale re-check (the pre-check alone
  is not enough once compute is unserialized — a request can go stale
  *during* its own now-concurrent compute); the new fine-grained `cache_lock`
  wraps only the `session.cache`/`cache_lookback`/`cache_values` **pairs**,
  never the warehouse I/O that produces the data being installed;
  `session.aa_rows` stays deliberately lock-free (a whole-object reference
  reassignment, GIL-atomic by construction — documented so nobody "fixes" it
  into an in-place mutation without adding a lock); `_id_lock`/the two-tab 409
  staleness machinery is untouched. **The fifth `cache_lock` call site is
  pinned, not left as a "verify and maybe skip" note**: `knob_surface()`'s
  `covariate_cutoffs` scan (`abkit/tuning/recompute.py:526-532`) reads
  **only `session.cache`** — via `session.cached_cutoffs()`'s dict
  comprehension and `session.loaded()` (`session.py:112-116`); it never
  touches `cache_lookback` (verified against the code — the design
  breakdown's own risk note asked exactly this question). It is brought
  under `cache_lock` anyway, for the *real* reason: `cached_cutoffs()`
  iterates `session.cache` concurrently with `_run_reload`'s writes to that
  same dict — a "dictionary changed size during iteration" hazard —
  independent of any torn-pair concern.
- **(f) Bootstrap memoization is a template-method refactor across 6
  classes**, keyed `(method_config_id, end_ts)` (identity already excludes
  `alpha`/`seed`, so no separate exclusion bookkeeping is needed), with a
  bounded FIFO eviction policy, Reload-triggered invalidation under a
  documented fixed lock-acquisition order (`cache_lock` before
  `boot_memo_lock`, never the reverse), and the "5 alphas → 1 resample"
  instrumentation test as the engagement proof (not just a numbers-match
  parity test, which alone wouldn't prove memoization actually fired).

---

## 1. Work packages

### WP1 — Config + planner core: sub-day start/horizon timestamps (byte-identical for existing configs)

**Goal.** Widen `ExperimentConfig.start_date`/`end_date` from `date` to a
type-preserving `date | datetime` union (not a coercing `datetime` field —
Python keeps `date` and `datetime` as distinct runtime types even though
`datetime` subclasses `date`, which sidesteps the "was it a bare date or an
explicit midnight?" ambiguity entirely), and generalize
`abkit/core/period_planner.py`'s anchor/horizon/day-snap logic to branch on
the literal input type: a plain `date` reproduces today's exact
midnight-snap / +1-day-inclusive-horizon behavior byte-identically; a
`datetime` localizes directly with no snap and no +1-day bump. This is the
single highest-risk WP in the milestone — it touches the one shared
`generate_grid` choke point — and lands alone, proven byte-identical, before
any downstream consumer is touched.

**Files touched:**
- `abkit/config/experiment_config.py` (fields at lines 233-234, the
  `validate_dates` model validator at 315-319, `horizon_seconds()` at
  426-428)
- `abkit/core/period_planner.py` (`tz_midnight_utc` at 65-68,
  `generate_grid` signature/body at 71-99, the day-cadence snap loop at
  117-144, the module docstring at 7-22)
- `tests/core/test_period_planner.py`
- `tests/config/test_experiment_config.py`

**Steps:**
1. Change `start_date: date` / `end_date: date` to `start_date: date |
   datetime` / `end_date: date | datetime`; import `datetime` alongside the
   existing `date` import (line 18). Add a `field_validator(mode="before")`
   per field (or one shared `_parse_date_or_datetime(v)` helper) that: passes
   an already-typed `datetime`/`date` object through unchanged (PyYAML
   already returns these natively for `YYYY-MM-DD` vs full-ISO strings);
   for a **string**, uses a strict length/format sniff (`len(v) == 10 and
   v.count('-') == 2` and no `'T'`/space) to pick `date.fromisoformat(v)` vs
   `datetime.fromisoformat(v)` — never relies on Pydantic's smart-union
   ordering to disambiguate `date | datetime`; for anything else (raw `int`/
   `float`/other), **raises** rather than falling through to the union's
   default coercion (the §0.4(a) correction — the `start_date: 20240101`
   epoch-1970 trap). Write an explicit test pinning the string sniff both
   ways and the raw-scalar rejection.
2. Update the `end_date < start_date` ordering check (`validate_dates`) via a
   new `_as_naive_datetime(v: date | datetime) -> datetime` helper (returns
   `v` unchanged if already `datetime`, else `datetime.combine(v, time.min)`)
   so a `date` vs `datetime` mix compares correctly (verify and guard against
   the `TypeError` Python raises comparing `date` to `datetime` directly in
   some mixed cases).
3. Rewrite `horizon_seconds()` (today: `((end_date - start_date).days + 1) *
   DAY_SECONDS`) to: normalize both fields via `_as_naive_datetime`; if
   `type(self.end_date) is date` (checked with `type(...) is date`, **never**
   `isinstance`), add one day to the normalized end before subtracting
   (reproducing today's inclusive-day convention exactly); else use the
   normalized end as-is (an explicit horizon instant, no bump). Return
   `int(round((end_target - start_dt).total_seconds()))`. Pin with unit
   tests: two bare dates reproduce the exact current integer; a bare-date
   vs explicit-time end produces the true elapsed seconds; both-explicit
   gives the raw diff with no bump.
4. In `period_planner.py`, replace `tz_midnight_utc(day, zone)` with two
   explicit helpers reused by both `experiment_config.py` and this module
   (re-exported from `abkit/core/__init__.py` alongside the existing
   `tz_midnight_utc` re-export so every existing importer, e.g.
   `recompute_backend.py:93`, is untouched): (1) `tz_localize_utc(local_dt:
   datetime, zone) -> datetime` — the generalized primitive; (2) keep
   `tz_midnight_utc(day, zone) -> datetime` as a one-line wrapper around it
   so every existing caller is byte-identical.
5. Rewrite `generate_grid`'s signature to accept `start_date: date |
   datetime, end_date: date | datetime`. Add `_resolve_start(start_date,
   zone)`: `tz_midnight_utc(start_date, zone)` when `type(start_date) is
   date`, else `tz_localize_utc(start_date, zone)`. Add
   `_resolve_horizon(end_date, zone)`: `tz_midnight_utc(end_date +
   timedelta(days=1), zone)` when `type(end_date) is date` (unchanged), else
   `tz_localize_utc(end_date, zone)` (no +1-day bump — the explicit instant
   *is* the horizon). Replace the current two anchor-computation lines with
   calls to these resolvers.
6. Generalize the day-or-coarser cadence snap loop (today: always anchors on
   midnight via `tz_midnight_utc(start_date + timedelta(days=day_offset),
   zone)`). Compute once, before the loop: `anchor_local = start_ts.replace(
   tzinfo=timezone.utc).astimezone(zone)`, `anchor_time =
   anchor_local.time()`; replace the midnight call with a new
   `tz_local_anchor_utc(day: date, anchor_time: time, zone) -> datetime`
   helper (`datetime.combine(day, anchor_time).replace(tzinfo=zone)
   .astimezone(timezone.utc).replace(tzinfo=None)`), using `start_date`'s
   date part for the day-counting arithmetic (unaffected by time-of-day).
   For every existing bare-date config `anchor_time == time.min`, so this is
   byte-identical today; for an explicit-time start, daily/weekly cadence
   segments now land on the same local wall-clock time every day
   (DST-safe — the §0.4(d) correction). This step is confirmed against the
   maintainer's sign-off before landing (design-fork, not a re-derivation of
   an already-decided detail — see §4).
7. Update the module docstring to describe the type-branching precisely
   (`date` → legacy midnight/day semantics; `datetime` → exact-instant
   anchor/horizon, no snap) instead of the current unconditional "midnight
   of start_date" wording.
8. Regression tests (`tests/core/test_period_planner.py`): keep **every**
   existing test green **unmodified** (the `date`-typed path, byte-identity
   pin); add a new `TestExplicitTimeAnchors` class: sub-day explicit start
   (`datetime(2024,7,1,14,30)`) produces `grid.start_ts` equal to that
   instant localized with no midnight snap; explicit-time end horizon has no
   +1-day bump; daily cadence with an explicit-time start lands each day at
   the same local wall-clock time (hand-computed DST-crossing case mirroring
   the existing `test_moscow_midnights`/EST-EDT tests); mixed bare-start/
   explicit-end and explicit-start/bare-end combinations.
9. Regression tests (`tests/config/test_experiment_config.py`): keep the
   existing string-date payload, ordering-check, and date-only YAML fixture
   tests passing **unmodified** (the byte-identity pin); add: an explicit
   ISO-datetime string parses to a `datetime` instance (not silently
   truncated); the sniff round-trips both a Python `date` object and a
   `datetime` object passed directly (programmatic construction, e.g.
   `model_copy(update=...)` call sites already used by
   `cli/commands/plan.py`'s `exp_for_alpha`); `horizon_seconds()` parity
   across all three date/datetime combinations.

**Tests and gates:**
- `tests/core/test_period_planner.py` — full existing suite green,
  unmodified (byte-identity pin).
- `tests/config/test_experiment_config.py` — full existing suite green,
  unmodified (byte-identity pin).
- New: `TestExplicitTimeAnchors` (sub-day anchors, no-snap horizon,
  DST-safe daily-cadence wall-clock anchoring).
- New: datetime-string parsing, raw-scalar rejection (`start_date: 20240101`
  → `ValidationError`), `horizon_seconds()` parity across all 3 combinations.
- CHANGELOG entry under `[Unreleased]` noting the additive type widen,
  explicitly stating "no `ALGORITHM_VERSION` bump, no
  `statistics-changes.md` entry — pure config/planner change, no numeric
  output altered for any existing config."

**Risks / hotspots:**
- The `date | datetime` union with a custom before-validator must be tested
  against every existing construction path, not just fresh YAML parses:
  `experiment.model_copy(update={...})` (`plan.py`'s `exp_for_alpha`), any
  test-fixture builder passing Python `date(...)` objects directly, and
  `to_dict()`-style serialization (`experiment_config.py:462`) feeding
  `upsert_experiment` — a missed path could silently coerce or reject a
  previously-valid value.
- `type(x) is date` checks are deliberately **not** `isinstance` — if a
  future contributor "cleans up" these to `isinstance(x, date)` (always
  `True` for a `datetime` too), the byte-identity/no-snap branching silently
  collapses to one branch. Comment this loudly at every such site.
- The day-cadence wall-clock-anchor generalization (step 6) is the one piece
  not explicitly spelled out in REPORT.md §8 — a genuine design
  extrapolation that must be confirmed with the maintainer before merging,
  or descoped to "reject/warn on day-cadence + explicit-time start" if
  preferred (see §4 open questions).

**Session estimate:** 2 sessions (the core planner logic is small, but the
byte-identity proof obligation across every existing test, plus the
day-cadence wall-clock generalization, both need careful, unhurried
verification).

---

### WP2 — Propagate sub-day anchors: CUPED lookback, SQL render-smoke, catalog table, docs

**Goal.** Fix the two remaining call sites that reimplement date arithmetic
against `start_date`/`end_date` directly (the CUPED pre-period window and the
config validator's SQL render-smoke) rather than going through
`generate_grid` (which WP1 already made safe), and widen the
`_ab_experiments` catalog mirror table's column typing — the second of the
two track-wide schema breaks. Update the docs that describe the old
date-only anchor behavior. Every other consumer enumerated in §0.2 needs
**no code change**.

**Files touched:**
- `abkit/compute/recompute_backend.py` (`_preperiod_window`, lines 83-96)
- `abkit/config/validator.py` (`_render_smoke`, line 331)
- `abkit/database/tables.py` (`get_experiments_table_model`, lines 45-46)
- `abkit/pipeline/driver.py` (no code change expected — verification only)
- `tests/database/test_tables_contract.py` (step 3: the `_ab_experiments`
  column-type assertions)
- `docs/specs/cumulative-intervals.md`, `docs/specs/declarative-config.md`
- `tests/compute/test_recompute_backend.py`
- `tests/config/test_validator_l2.py`

**Steps:**
1. `recompute_backend.py::_preperiod_window`: the CUPED lookback is
   contractually **whole-day** (statistics-changes.md §5) regardless of
   whether the experiment start carries a time-of-day. Extract the date part
   before the day-arithmetic: `start_date_only = experiment.start_date if
   type(experiment.start_date) is date else experiment.start_date.date()`;
   keep `pre_start = tz_midnight_utc(start_date_only - timedelta(days=
   lookback_days), zone)` otherwise unchanged. This keeps the CUPED window
   midnight-aligned even for a sub-day-anchored experiment (correct per
   spec — the pre-period is a coarse daily aggregate, never a sub-day one).
   Add a test with an explicit-time `start_date` asserting the pre-period
   window is still midnight-to-midnight, and does **not** accidentally land
   at `14:30` daily.
2. `validator.py::_render_smoke`: `datetime.combine(experiment.start_date,
   datetime.min.time())` breaks if `experiment.start_date` is already a
   `datetime` — verify `datetime.combine`'s exact behavior with a `datetime`
   first argument in this Python version (do not assume) and guard:
   `experiment.start_date if isinstance(experiment.start_date, datetime)
   else datetime.combine(experiment.start_date, datetime.min.time())`. This
   is a render-smoke fixture window only (not the real grid), so exact-
   instant fidelity doesn't matter — just don't crash the lint on a
   sub-day-anchored experiment. Add a validator_l2 test with an
   explicit-time `start_date` asserting the `abk run --steps validate`-
   equivalent lint still passes.
3. `tables.py::get_experiments_table_model`: widen
   `ColumnDefinition("start_date", "Date")` /
   `ColumnDefinition("end_date", "Date")` to
   `ColumnDefinition("start_date", "DateTime64(3)")` /
   `ColumnDefinition("end_date", "DateTime64(3)")` — the informational
   `_ab_experiments` catalog mirror (`upsert_experiment` ←
   `pipeline/driver.py:177` ← `experiment_config.py:462`'s
   `self.start_date`/`self.end_date`). This is out of REPORT.md §8's
   explicit scope but a genuine silent-truncation bug the type widen would
   otherwise introduce; confirmed with the maintainer per §4 before landing.
   Update `tests/database/test_tables_contract.py`'s `_ab_experiments`
   column-type assertions accordingly (or add one if none exists).
4. Docs: update `cumulative-intervals.md`'s framing ("`start_date` is pinned
   to experiment start") to note start/horizon are now full timestamps (a
   bare date still means midnight, unchanged default);
   `declarative-config.md`'s `start_date`/`end_date` sample comments get a
   one-line addendum documenting the now-legal explicit-time-of-day form.
   **Do not** touch the `ab_start_date`/`ab_end_date` SQL-builtin
   documentation (`declarative-config.md:151-152`,
   `query_template.py`'s `RenderWindow.start_date/end_date`) — those are
   pre-existing, already-date-truncated builtins for day-partitioned SQL
   filters, an orthogonal and already-solved sub-day mechanism; call this
   out explicitly in the PR description so a reviewer doesn't conflate the
   two.

**Tests and gates:**
- `tests/compute/test_recompute_backend.py` — new test: explicit-time
  `start_date` still produces a midnight-aligned CUPED pre-period window.
- `tests/config/test_validator_l2.py` — new test: explicit-time `start_date`
  passes the SQL render-smoke without crashing.
- `tests/database/test_tables_contract.py` — `_ab_experiments`
  `start_date`/`end_date` column-type assertion updated to `DateTime64(3)`.
- Full existing suite green — validator/driver/plan/reporting/session paths
  need zero code changes per this WP's design, so their existing tests are
  the regression gate; any failure there is a previously-hidden reader that
  needs its own fix, not a test update.
- CHANGELOG entry (can be folded into WP1's entry as one combined "sub-day
  anchors" note) covering the CUPED/render-smoke/catalog-table fixes,
  explicitly flagging the `_ab_experiments` widen as half of the milestone's
  combined breaking-schema note (§0.3/§0.4(c)).

**Risks / hotspots:**
- `datetime.combine`'s exact behavior with a `datetime` (not `date`) first
  argument must be verified directly in this repo's Python version before
  writing the guard — do not assume.
- The `_ab_experiments` widen is itself a schema change alongside WP3's
  larger one — sequence both into the **same** release note / CHANGELOG
  breaking-change section (§0.3) rather than two scattered notes.

**Session estimate:** 1 session.

---

### WP3 — Drop `start_date`/`end_date` from `_ab_results`; fix stale hints and comments

**Goal.** Remove the two derived, effectively-unread `start_date`/`end_date`
`Date` columns from the `_ab_results` schema (`tables.py`, `enrich.py`, the
`RESULT_COLUMNS` contract) — nothing in the pipeline/report/explore/BI-example
surface reads them, and they are fully reconstructable from `end_ts` in the
experiment timezone (with the documented −1µs trap). Update the one real
reference (`abk init`'s printed hint) and fix the stale `rng.py` docstring
comment. This is a breaking schema change under the project's pre-1.0 alpha
policy: CHANGELOG + drop/recreate guidance, no migration tooling.

**Files touched:**
- `abkit/database/tables.py` (`get_results_table_model`, lines 147-161,
  177-178)
- `abkit/pipeline/enrich.py` (`rows_for_cutoff`, lines 41-86)
- `abkit/database/internal_tables/_results.py` (`RESULT_COLUMNS`, no code
  change — auto-derived; contract test re-run only)
- `abkit/cli/commands/init.py` (line 347)
- `abkit/stats/rng.py` (lines 9, 28 — docstring only)
- `tests/database/test_tables_contract.py`, `tests/database/test_internal_tables.py`
- `docs/specs/data-contract-and-reporting.md`, `docs/reference/internal-tables.md`
- `CHANGELOG.md`

**Steps:**
1. `tables.py::get_results_table_model`: delete the two
   `ColumnDefinition("start_date", "Date")` /
   `ColumnDefinition("end_date", "Date")` lines (177-178). Update the
   function's docstring (the "`end_date`/`start_date` are derived Dates,
   legacy-identical at daily cadence" sentence) to instead say these were
   removed — derive the calendar date from `end_ts` in the experiment
   timezone minus 1µs if needed for BI. Leave `primary_key`/`order_by`/
   `version_column` untouched (they never referenced these columns).
2. `enrich.py::rows_for_cutoff` (lines 41-86): delete the
   `start_date_local`/`end_date_local` computation (lines 59-65) and the two
   `"start_date": start_date_local` / `"end_date": end_date_local` row-dict
   entries (lines 84-85). Remove now-unused `ZoneInfo`/`_ONE_US`/`zone`/`utc`
   locals if nothing else in the function still needs them — double-check
   `_ONE_US`, which may be used elsewhere in the file; keep the import if so.
3. `_results.py`: `RESULT_COLUMNS` (line 27-29) is auto-derived from
   `get_results_table_model()`, so it updates for free once the columns are
   dropped — no code change here, but re-run its contract test to confirm
   the missing/extra-column guard in `save_results` (lines 49-54) still
   validates cleanly against the enrich stage's now-shorter row dict.
4. `init.py` line 347: change `SELECT metric, end_date, effect, pvalue,
   left_bound, right_bound FROM …` to `SELECT metric, end_ts, effect,
   pvalue, left_bound, right_bound FROM …`. Add a one-line comment above the
   generated SQL sample (or the surrounding markdown) noting the
   timezone/−1µs trap: a naive `toDate(end_ts)` groups by the UTC date and
   misdates an around-midnight cutoff for a non-UTC-timezone experiment —
   the correct BI-side calendar date is
   `toDate(end_ts - toIntervalMicrosecond(1), '<experiment timezone>')` (or
   the equivalent). Mirror this same note into
   `docs/reference/internal-tables.md` (currently line ~129) and
   `docs/specs/data-contract-and-reporting.md` (currently line ~74, the
   window-columns table row listing `start_date`/`end_date`) — both edited
   in the same PR, per invariant 6/7's docs-sync discipline, not left stale.
5. `rng.py` lines 9 and 28: change both docstring occurrences of
   `end_date, n_samples` to `end_ts, n_samples` (the actual `derive_seed`
   call sites — `pipeline/analyze.py:187-192`, `tuning/recompute.py:708` —
   already pass `end_ts`; a pure comment fix, zero behavior change, zero
   risk to the pinned known-answer seed test).
6. Update `tests/database/test_tables_contract.py`: delete
   `test_end_ts_is_datetime_end_date_is_date` (lines 109-113) or rewrite it
   to `test_end_ts_is_datetime_no_start_date_end_date_columns` asserting
   `model.get_column("start_date")`/`get_column("end_date")` now raise/
   return `None` and `end_ts` is still `DateTime64`; update
   `RESULTS_CONTRACT_COLUMNS` (line 26) to drop the two entries so
   `test_exact_column_list_and_order` (lines 83-85) passes.
7. Update `tests/database/test_internal_tables.py` lines 326-327 (row
   construction with `date(2024,1,1)` literals for `start_date`/`end_date`)
   — remove those two keys from every result-row test fixture in the file
   (grep for other occurrences beyond the cited lines) and confirm
   `save_results`'s exact-contract-column guard doesn't reject the
   now-shorter fixtures.
8. `CHANGELOG.md`: add a `### Removed`/`### Changed` entry under
   `[Unreleased]` explicitly flagged as **BREAKING**: "`_ab_results.
   start_date`/`end_date` columns removed (unread, fully derivable from
   `end_ts`) — existing deployments must drop and recreate `_ab_results`
   (`DROP TABLE abkit_internal._ab_results` then re-run `abk run`, or
   `ALTER TABLE … DROP COLUMN start_date, DROP COLUMN end_date` manually)
   before upgrading; BI queries/dashboards referencing these columns must
   switch to `end_ts` (see `docs/reference/internal-tables.md` for the
   timezone/−1µs calendar-date recipe)." Explicitly state "no
   `ALGORITHM_VERSION` bump — schema-only, zero numeric change."

**Tests and gates:**
- `tests/database/test_tables_contract.py` — updated
  `RESULTS_CONTRACT_COLUMNS`, `test_exact_column_list_and_order`, replaced
  `test_end_ts_is_datetime_end_date_is_date`.
- `tests/database/test_internal_tables.py` — result-row fixtures no longer
  construct `start_date`/`end_date`.
- Full pipeline/enrich/reporting/explore test suites green with the shorter
  row-dict contract (all currently ignore these columns per the audit, so a
  clean pass is expected — treat any failure as a previously-hidden reader
  needing its own fix, not a test update).
- `grep -rn 'start_date\|end_date' abkit/` (excluding tests) returns
  **only**: the config-field occurrences from WP1/WP2, the
  `RenderWindow.start_date/end_date` SQL-builtin properties (out of scope,
  confirmed orthogonal), and nothing else — the WP's own done-verification
  step.

**Risks / hotspots:**
- Any hidden reader of the results-table `start_date`/`end_date` columns not
  caught by the audit's repo-wide grep (a dynamically-built `SELECT *`
  consumer, or a notebook/example script outside `abkit/`/`docs/`) would
  silently start erroring post-drop — re-run the audit's grep scope
  (including `docs/examples/`, any `notebooks/`, and `website/` if it embeds
  SQL) as this WP's own first step, not just trusting the prior pass.
- Dropping columns from a live `ReplacingMergeTree` with no migration
  tooling is an operationally disruptive change for any real deployment
  already running `abk run` — the CHANGELOG guidance is necessary, but the
  PR description (not code) should flag this is best shipped in a version
  bump the maintainer is comfortable calling breaking, not silently inside
  a patch release.

**Session estimate:** 1 session.

---

### WP4 — Explore: decouple the global request lock (cheap tiers vs Reload/Auto-validate)

**Goal.** Split `_ExploreServer.request_lock` (`server.py:116`) — which today
serializes `/recompute`, `/reload`, `/validate`, and `/apply` against each
other with one coarse lock — so a cheap Tier α/E (and even Tier S,
cache-hit) `/recompute` never queues behind a slow `/reload` or a
400-iteration Auto-`/validate`. Design: rename the existing lock to
`heavy_lock` and keep it around `/reload`/`/validate`/`/apply` **only**
(unchanged mutual exclusion there — these three already share DB-manager/
YAML-write concerns unrelated to recompute cost); drop it entirely from
`/recompute`; add **one** new fine-grained `cache_lock` guarding exactly the
two dict-pairs that `/reload` mutates and Tier-S `/recompute` reads
(`session.cache`/`session.cache_lookback`/`session.cache_values`) so a
concurrent Tier-S read never sees a torn (loaded-entry, lookback-tag) pair
mid-Reload; leave `session.aa_rows` deliberately lock-free (documented
explicitly per §0.4(e)). Extend the existing stale-request-drop discipline
with a post-compute re-check so removing the lock-as-a-queue doesn't let a
request that goes stale **during** its own (now-unserialized) compute reply
with a superseded answer.

**Files touched:**
- `abkit/tuning/server.py`
- `abkit/tuning/session.py`
- `abkit/tuning/recompute.py` (steps 5–6: the `_compute_point` Tier-S read
  pair + the `knob_surface()` scan)
- `tests/tuning/test_server.py`

**Steps:**
1. `_ExploreServer.__init__` (`server.py:96-119`): rename
   `self.request_lock` to `self.heavy_lock` (plain `threading.Lock`); add
   `self.cache_lock = threading.Lock()`. Update the docstring comment ("One
   compute at a time…") to describe the split explicitly.
2. `_handle_recompute` (lines 227-250): remove the `with srv.request_lock:`
   block — call `srv.engine.recompute(metric, knobs)` directly after the
   existing pre-check `srv.check_stale(request_id)` (still fast-rejects an
   already-superseded request before spending CPU). Immediately after
   `result = srv.engine.recompute(...)` succeeds and before
   `self._reply_json(...)`, add the new **post-compute** check:
   `if srv.is_stale(request_id): self._reply_json({"stale": True,
   "request_id": request_id}, code=409); return` — this closes the race the
   removed lock's pre-reply re-check used to cover (a newer `request_id`
   arriving while this thread was mid-compute). Confirm `is_stale`'s exact
   current signature/behavior (guarded by `_id_lock`) before wiring the new
   call site.
3. `_handle_reload` (lines 252-283) and `_handle_validate` (lines 285-323):
   keep exactly as-is except rename `srv.request_lock` → `srv.heavy_lock` in
   both `with` statements. `_handle_apply` (lines 325+): same rename only.
4. `_run_reload` (around `server.py:554-604`, the per-cutoff cache-mutation
   loop): wrap the read-modify-write of `session.cache`/
   `session.cache_lookback`/`session.cache_values` for **each cutoff** (the
   block at lines 597-604) in `with srv.cache_lock:` — hold the lock only
   across this small dict-mutation block, **not** across the potentially
   slow warehouse `loader(...)` call that produces `loaded` beforehand (no
   shared-state dependency there; running it outside the lock keeps a slow
   warehouse read from blocking a concurrent Tier-S cache read any longer
   than necessary).
5. Thread the same lock object into `abkit/tuning/recompute.py::_compute_point`'s
   Tier-S branch (lines 690-716): move the `cache_lock` field from
   `_ExploreServer.__init__` onto `ExploreSession` (`session.py:84-101`,
   alongside the existing `cache`/`cache_lookback`/`cache_values` fields:
   `cache_lock: threading.Lock = field(default_factory=threading.Lock)`) so
   it's constructed once with the session and naturally shared by both
   `server.py`'s `_run_reload` and `recompute.py`'s `_compute_point` — update
   `server.py.__init__` to reference `srv.session.cache_lock` instead of
   constructing its own (guarding for `srv.session is None` the same way
   other session-dependent code already does). Wrap the two reads `loaded =
   self._session.loaded(...)` and `entry_lookback =
   self._session.cache_lookback.get(...)` in `with
   self._session.cache_lock:`, copying the small values out before
   releasing the lock (a reference copy is enough — the underlying numpy
   arrays are never mutated in place, only the dict entries are ever
   replaced wholesale); the actual resample/compare math proceeds lock-free
   afterward.
6. Bring `knob_surface()`'s `covariate_cutoffs` scan
   (`recompute.py:526-532`) under `cache_lock` too — the **fifth** call
   site, pinned per §0.4(e) rather than left as a "verify whether it needs
   one" decision. Note the accurate rationale: the scan reads **only**
   `session.cache` (through `cached_cutoffs()`/`loaded()`,
   `session.py:112-116`), not `cache_lookback` — the lock is needed because
   `cached_cutoffs()`'s comprehension iterates the dict `_run_reload`
   concurrently mutates, not because of a torn pair.
7. Tests (`tests/tuning/test_server.py`): extend `TestReload`/
   `TestAutoValidate` (existing classes) with a new `TestLockDecoupling`
   class: (a) monkeypatch `_run_reload` (or inject an artificial
   `time.sleep(0.5)` into the loader) to make one `/reload` call slow, fire
   it on a background thread, then fire a `/recompute` for a different
   (non-bootstrap, Tier E) knob state on the main thread and assert it
   replies well under the reload's sleep duration (proving it isn't queued
   behind `heavy_lock` anymore); (b) the same shape for a slow `/validate`;
   (c) a race test concurrently running `/reload` (mutating `session.cache`
   for cutoff X) and 20 rapid `/recompute` calls touching the **same**
   cached cutoff X for a bootstrap knob state, asserting no exception and
   every reply is either a valid result or a clean stale-409 — never a
   corrupted/mismatched result (the `cache_lock` correctness gate); (d)
   extend the existing `test_concurrent_recomputes_all_answer` (line 547)
   pattern with a mix of stale/fresh `request_id`s to prove the new
   post-compute staleness re-check actually fires (a slow recompute whose
   `request_id` is superseded mid-flight by a second, faster request must
   409, not reply).

**Tests and gates:**
- `tests/tuning/test_server.py::TestLockDecoupling` (new) — all 4
  sub-scenarios above.
- Existing `tests/tuning/test_server.py` suite green (`TestReload`,
  `TestAutoValidate`, `TestApply`, `TestApplyGateClosure` — `heavy_lock`'s
  mutual exclusion among Reload/validate/apply must be provably unchanged).
- Existing `test_concurrent_recomputes_all_answer` (line 547) still green,
  extended per step 7d.
- No new thread-safety warnings under `python -m pytest -W
  error::RuntimeWarning` or equivalent.

**Risks / hotspots:**
- The two-tab 409 semantics are really about `_id_lock`/`check_stale`/
  `is_stale`, which this WP does **not** touch (a separate lock, unchanged)
  — the risk is a careless implementer conflating `request_lock`/
  `heavy_lock` with `_id_lock` and accidentally removing the id-based
  staleness check instead of just the coarse compute lock. Call this out
  explicitly in code review.
- If WP5's memo-cache dict is called with no lock at all from
  `/recompute`, the memo cache itself becomes the only remaining
  shared-mutable-state hazard in the hot path — WP5 must add its **own**
  dedicated lock around that dict; `cache_lock` does not cover it (a
  different object, different data).
- Holding `cache_lock` only across the small dict-mutation block in
  `_run_reload` (not across the warehouse `loader()` call) means the
  loaded-but-not-yet-installed data sits in a local variable during the slow
  I/O — verify no other thread can observe a half-reloaded state through
  some other path (the `knob_surface()` fifth call site is exactly this
  concern, resolved by step 6).

**Session estimate:** 1 session.

---

### WP5 — Explore: memoize bootstrap resampling across alpha-only changes

**Goal.** Split every `BaseBootstrapMethod` subclass's `from_samples`
(`bootstrap.py`, `paired_bootstrap.py`, `post_normed_bootstrap.py`,
`poisson_bootstrap.py`, `paired_post_normed_bootstrap.py` — 5 files, all
following the identical `boot_data = …; …; return self._finalize(sample_1,
sample_2, boot_data, effect, result_warnings)` shape) into a template
method: a new base-class `from_samples` that calls an abstract `_resample(
sample_1, sample_2) -> tuple[FloatArray, float, list[str]]` (the boot_data/
effect/warnings a subclass currently computes inline) then `self._finalize
(...)`. This is a pure structural refactor of `abkit.stats` (still
numpy/scipy-only, satisfying the purity invariant) with zero numeric change
(parity-tested). Then, in `RecomputeEngine`, add a small memo cache keyed by
`(method.method_config_id, row["end_ts"])` storing the `(boot_data, effect,
result_warnings)` tuple, so that when only `alpha` changes across repeated
Tier-S recomputes of the same bootstrap knob state + cutoff, the engine calls
`_resample` at most **once** and reuses the cached tuple for every
subsequent `_finalize` call at a different alpha.

**Files touched:**
- `abkit/stats/bootstrap/bootstrap.py`
- `abkit/stats/bootstrap/paired_bootstrap.py`
- `abkit/stats/bootstrap/post_normed_bootstrap.py`
- `abkit/stats/bootstrap/poisson_bootstrap.py`
- `abkit/stats/bootstrap/paired_post_normed_bootstrap.py`
- `abkit/tuning/recompute.py`
- `abkit/tuning/session.py`
- `tests/stats/bootstrap/test_bootstrap.py`
- `tests/tuning/test_recompute.py`

**Steps:**
1. `BaseBootstrapMethod` (`bootstrap.py`, class starting line 56): add an
   abstract `def _resample(self, sample_1, sample_2) -> tuple[FloatArray,
   float, list[str]]: raise NotImplementedError` (or `@abstractmethod` if
   the class already uses ABC machinery — check `BaseMethod`'s metaclass)
   with a docstring explaining it returns exactly what each subclass
   currently inlines before calling `_finalize`. Add a concrete
   `def from_samples(self, sample_1, sample_2) -> TestResult: boot_data,
   effect, result_warnings = self._resample(sample_1, sample_2); return
   self._finalize(sample_1, sample_2, boot_data, effect, result_warnings)`
   on the base class.
2. In each of the 6 classes (`BootstrapTest.from_samples`
   `bootstrap.py:294-311`; `PairedBootstrapTest.from_samples`
   `paired_bootstrap.py:37-57`; `PostNormedBootstrapTest.from_samples`
   `post_normed_bootstrap.py:55-83`; `PoissonBootstrapTest.from_samples`
   `poisson_bootstrap.py:52-75`; `PairedPostNormedBootstrapTest.from_samples`
   `paired_post_normed_bootstrap.py:55-81`; and
   `PairedPoissonBootstrapTest.from_samples` `poisson_bootstrap.py:90-112`,
   which subclasses `PoissonBootstrapTest` and **overrides `from_samples`
   again** — verify explicitly whether it calls `super().from_samples(...)`
   (which would break once the base becomes a template method — the
   override chain must become `_resample` overriding `_resample`, not
   `from_samples` overriding `from_samples`) or reimplements independently;
   handle explicitly rather than assuming the same one-line pattern as the
   other 5): rename `from_samples` to `_resample`, delete the trailing
   `return self._finalize(...)` line (replaced by the new base-class
   `from_samples`), change the final `return` to `return boot_data, effect,
   result_warnings` (the exact three locals each already computes). Do
   **not** change a single line of the resample math itself — a pure
   rename/split, byte-identical by construction.
3. Parity test (`tests/stats/bootstrap/test_bootstrap.py` and the
   equivalent modules for the other classes, per the actual
   `tests/stats/bootstrap/` layout): for each of the 6 classes, call
   `method.from_samples(s1, s2)` (the new template-method composition) and
   separately `boot_data, effect, warnings = method._resample(s1, s2);
   result = method._finalize(s1, s2, boot_data, effect, warnings)` with the
   same rng seed, assert the two `TestResult`s are field-for-field
   identical (the parity gate proving the refactor changed nothing). Run
   the full existing bootstrap test suite (goldens, known-answer seed
   tests) unmodified — they call `from_samples`/`compare_pair` at the
   public API level and must stay green untouched.
4. `ExploreSession` (`session.py`, lines 84-101): add `boot_memo:
   dict[tuple[str, datetime], tuple[Any, float, list[str]]] = field(
   default_factory=dict)` (key: `(method_config_id, end_ts)` — sufficient
   per the identity analysis: `method_config_id` already excludes `alpha`
   and `seed` while including every other identity-bearing param, so it is
   exactly "identity params excluding alpha" already) and `boot_memo_lock:
   threading.Lock = field(default_factory=threading.Lock)`. Add a small
   budget constant `EXPLORE_BOOT_MEMO_BUDGET` (mirroring
   `EXPLORE_CACHE_BUDGET`'s pattern, but a cap on entry **count** rather
   than byte size, since boot_data arrays are a predictable fixed size per
   `n_samples`) and a bounded-FIFO eviction policy
   (`collections.OrderedDict`, evict oldest past the cap) — the resample
   memo grows one entry per (identity, cutoff) combination explored in a
   session, naturally small, but still needs some cap against a scripted/
   automated client hammering many distinct `n_samples` values.
5. In `recompute.py`'s Tier-S branch (`_compute_point`, lines 689-718): when
   `_needs_seed(method_cls)` is true (the bootstrap-family branch, `reusable
   is None`), before constructing `method` and calling `_compare(method,
   group_1, group_2)`, check `self._session.boot_memo.get((
   live_method_config_id, row["end_ts"]))` under `self._session.
   boot_memo_lock` (acquire, check, release immediately — never hold the
   lock across the resample compute). On a cache **miss**: release the
   lock, construct `method` and call `method._resample(group_1, group_2)`
   directly (not `_compare`/`compare_pair`, which would call the old
   monolithic `from_samples` and always redo `_finalize` too) to get
   `(boot_data, effect, result_warnings)`, then re-acquire `boot_memo_lock`
   briefly to insert (last-writer-wins under a race is fine — deterministic
   inputs mean a duplicate compute is wasted CPU, never wrong numbers, per
   WP4's design note). On a cache **hit**: skip `_resample` entirely, call
   `method._finalize(group_1, group_2, boot_data, effect, result_warnings)`
   directly (`method` still needs constructing for its `.alpha`/`.params`).
   Wrap the `_finalize` call the same way `_compare` wraps `compare_pair`
   today (the `_warnings.catch_warnings` capture at `recompute.py:1033-1039`)
   so `AbkitStatsWarning` capture behavior is unchanged on both paths —
   factor a small `_finalize_captured(method, s1, s2, boot_data, effect,
   warnings) -> tuple[TestResult, list[str]]` helper mirroring `_compare`'s
   warning-capture pattern.
6. Invalidate `boot_memo` whenever the underlying raw cache changes:
   `_run_reload` (WP4's territory) mutates `session.cache[(metric, end_ts)]`
   for specific cutoffs — any memo entries keyed by an `end_ts` whose raw
   cache entry was just reloaded are now stale (resampled against old
   per-user data) and must be dropped. Add a targeted purge
   (`session.boot_memo = {k: v for k, v in session.boot_memo.items() if
   k[1] != end_ts}` or a per-`end_ts` delete) at the point `_run_reload`
   installs a new `loaded` entry — the same block WP4 wraps in
   `cache_lock`, extended to also purge matching memo entries guarded by
   `boot_memo_lock` nested **after** `cache_lock` (the fixed lock-ordering
   convention from §0.4(f): always acquire `cache_lock` before
   `boot_memo_lock`, never the reverse — documented to avoid deadlock).
7. Parity test (`tests/tuning/test_recompute.py::TestBootstrap`, existing
   class at line 330): add a test asserting that recomputing the **same**
   bootstrap knob state across 5 different `alpha` values for the same
   cutoff produces identical `left_bound`/`right_bound`/`pvalue`/`effect`
   numbers to today's un-memoized baseline (capture a golden before the
   change, or compute both via a temporarily-disabled-memo code path in the
   test) — the byte-parity gate. Add an instrumentation test:
   monkeypatch/spy on the class's `_resample` method (`unittest.mock.
   patch.object` with `wraps=`) and assert it is called **exactly once**
   across 5 alpha-only `/recompute`-equivalent calls (call
   `RecomputeEngine.recompute` directly 5 times with the same method/
   params, different alpha) — proving the memoization actually engages, not
   just that numbers happen to match. Add a Reload-invalidation test:
   populate the memo, simulate a reload swapping the raw cache for that
   cutoff, then recompute again and assert `_resample` is called a
   **second** time (proving stale memo entries are correctly purged, not
   silently reused post-reload).

**Tests and gates:**
- `tests/stats/bootstrap/` — full existing suite green, unmodified
  (goldens/known-answer seed tests are the byte-identity pin for the pure
  refactor).
- New parity tests per subclass: `from_samples()` composition ==
  manual `_resample()` + `_finalize()` composition, same seed.
- `tests/tuning/test_recompute.py::TestBootstrap` — new: 5-alpha-values-
  same-numbers parity test; `_resample`-called-once instrumentation test;
  reload-invalidates-memo test.
- No change to any golden CSV / regression fixture anywhere in the repo
  (grep for golden fixture directories touched by `git status` before
  committing — an unexpected golden diff means the refactor leaked a
  numeric change and must be reverted, not re-tolerated).
- CHANGELOG entry noting: internal-only refactor + explore-only performance
  fix, "no `ALGORITHM_VERSION` bump, no `statistics-changes.md` entry —
  numbers are provably unchanged (see the new parity test suite)."

**Risks / hotspots:**
- The base-class `from_samples`/`_resample` split must be checked against
  every other place in the codebase that calls `.from_samples()`
  polymorphically expecting the old per-subclass override (`compare_pair`'s
  public dispatch, or any registry/factory introspecting `from_samples`
  specifically) — a `grep -rn 'from_samples'` sweep across `abkit/` (not
  just the bootstrap directory) before landing.
- `PairedPoissonBootstrapTest` (`poisson_bootstrap.py:90-112`) subclasses
  `PoissonBootstrapTest` and **also** overrides `from_samples` again — its
  override chain must be handled explicitly (see step 2), not assumed to
  follow the same one-line pattern as the other 5 classes.
- Lock-ordering between `cache_lock` (WP4) and `boot_memo_lock` (this WP)
  at the Reload-invalidation point is a genuine deadlock hazard if not
  disciplined — document and enforce a single fixed order (§0.4(f)), and
  add a Reload-during-concurrent-recompute stress test (extending WP4's
  `TestLockDecoupling`) under `pytest-timeout` or similar so a deadlock
  fails the test suite instead of hanging CI.

**Session estimate:** 1 session (the `abkit.stats` refactor is mechanical
across 6 classes; the memo cache + invalidation + concurrency tests are the
part needing care, kept to one session by leaning on WP4's already-built
lock primitives).

---

## 2. Dependency graph / parallelism

```
WP1 (config+planner core, byte-identical) ──▶ WP2 (propagation: CUPED,
     render-smoke, _ab_experiments widen, docs)

WP3 (drop _ab_results date columns) ── independent, no shared code path
     with WP1/WP2 (results-table columns vs config fields) — can run in
     parallel with WP1/WP2 or in either order

WP4 (lock decoupling) ──▶ WP5 (bootstrap memoization)
```

- **WP2 depends on WP1** — the config field type must widen before
  downstream call sites (the CUPED lookback, the render-smoke, the catalog
  table) and docs are updated against it.
- **WP3 is independent of WP1/WP2** — no shared code path (results-table
  column drop vs. config field type widen); it may land in parallel with, or
  in either order relative to, WP1/WP2.
- **WP5 should land after WP4.** WP5's new per-call memo cache is only
  strictly required to carry its own lock once `/recompute` stops being
  serialized by the old global `request_lock`; sequencing WP4 first avoids a
  window where the memo cache is "accidentally" safe only because of the
  soon-to-be-removed coarse lock.
- None of the 5 WPs touch `abkit.stats`' external numeric surface
  (`bootstrap.py`'s split is a pure refactor) — no `ALGORITHM_VERSION` bump
  anywhere in this milestone; no `docs/specs/statistics-changes.md` entry
  needed (called out in each WP's own exit checklist, §0.1).

---

## 3. Exit gate

One end-to-end regression script/test — `tests/e2e/test_sub_day_anchors_and_explore.py`
(or folded into the existing e2e harness under `tests/e2e/` if that fits
better) — that:

1. Loads a handful of **real existing fixture** YAML configs from
   `tests/fixtures` (bare `start_date`/`end_date`) through `ExperimentConfig`
   → `generate_grid` → pipeline `enrich`, and asserts the resulting
   `Grid.start_ts`/`horizon_ts`/`cutoffs` and every derived number
   (`window_seconds`, `elapsed_days`, the CUPED pre-period window,
   `look_days`/`horizon_days` in `abk plan`, `weekly_cycle_pct`) are
   **byte-identical** to a captured pre-change golden — the regression gate
   this milestone demands, not a new-behavior test.
2. Adds one **new** fixture with an explicit sub-day `start_date`/`end_date`
   (e.g. `start_date: '2024-07-01T14:30:00'`) and asserts the grid anchors
   at that instant with no midnight snap, validator/plan/driver/explore all
   accept it, and CUPED lookback still lands on a whole-day boundary.
3. Runs `abk run` against a fresh ClickHouse (testcontainers, matching the
   project's existing `e2e-clickhouse` CI job) and confirms `_ab_results`/
   `_ab_experiments` are created **without** `start_date`/`end_date` on
   results (**with** widened `DateTime` `start_date`/`end_date` on the
   experiments catalog), and that `abk init`'s printed hint / BI docs no
   longer mention the dropped columns.
4. Spins up `abk explore --no-serve=false` (the real HTTP server) against
   that fixture and drives: a slow `/validate` (monkeypatched or reduced-N
   but artificially delayed) concurrently with a fast alpha-only
   `/recompute` on a bootstrap comparison, asserting the `/recompute` reply
   lands well before the `/validate` reply completes (proving the lock
   split), **and** that changing only alpha across 5 requests for the same
   bootstrap knob state hits the memo cache (an instrumentation counter/
   monkeypatch on the resample entry point showing exactly 1 resample call
   for 5 alpha values) with byte-identical numbers to the unmemoized
   baseline.

Exit requires **at least 2 adversarial review rounds** (per-file
line-anchored findings, the [m4](m4-implementation-plan.md)-style critique
round format), covering:

- (a) every one of the ~9 `generate_grid`/date-arithmetic call sites
  enumerated in §0.2, re-checked one by one for the byte-identical-on-
  bare-date claim;
- (b) the lock-decoupling design specifically probed for the two-tab 409 /
  stale-request-drop races described in WP4;
- (c) the memo-cache eviction/budget and thread-safety under real
  concurrent alpha-drag load.

`CHANGELOG.md` gets one entry per WP (the breaking-change flag explicit for
WP2/WP3); `docs/specs/cumulative-intervals.md`, `docs/specs/declarative-config.md`,
`docs/specs/data-contract-and-reporting.md`, `docs/reference/internal-tables.md`,
and the `abk init` generated project sample (`abkit/cli/commands/init.py`)
are updated in the same PRs that change the behavior they describe — no
separate doc-catch-up WP. At milestone close: flip `CLAUDE.md` +
`.claude/rules/architecture.md` status to "M10 shipped", append this
document's §5 adversarial-review record (mirroring
[m4](m4-implementation-plan.md) §5 / [m6](m6-implementation-plan.md) §0.5),
and cross-check the coverage map in [ROADMAP.md](../../ROADMAP.md) (REPORT
#9–#12 → M10).

---

## 4. Open questions / decisions needed before start

Per the track plan's "перед стартом" ("before start") discipline, these need
an explicit maintainer answer before the corresponding WP lands (recommended
answers noted where the canonical breakdown offers one):

1. **Field naming.** Should `ExperimentConfig.start_date`/`end_date` keep
   their current names once they can hold a full timestamp (potentially
   confusing since "date" no longer describes the type), or should this
   milestone also introduce `start_ts`/`horizon` aliases with
   `start_date`/`end_date` deprecated-but-accepted? **Recommendation: no
   rename** — the milestone's decision text implies keeping the existing
   names for byte-identical YAML; a rename would be a bigger,
   config-migration-flavored change and is out of scope here. WP1 assumes
   no rename; flag to the maintainer before implementation in case a rename
   is actually wanted alongside the type widen.
2. **Day-or-coarser cadence + an explicit time.** Is the wall-clock-anchor
   generalization (§0.4(d), WP1 step 6) the desired behavior, or should
   day-or-coarser cadence simply be **disallowed/warned** when start carries
   a non-midnight time (forcing users who want daily cadence to also use a
   midnight start)? REPORT.md §8 only worked out the start/horizon anchor
   fix, not this interaction — a genuine design fork.
   **Recommendation: the wall-clock-anchor generalization** (it's strictly
   additive and degrades to today's exact behavior at midnight), but this is
   not literally spelled out in the track's decision and needs explicit
   sign-off before WP1 lands.
3. **`_ab_experiments` widen.** Confirm the intended fix for the catalog
   table's `Date`-typed `start_date`/`end_date` (which would silently
   truncate a sub-day start/end once WP1 lands) is widening to
   `DateTime64(3)` in the **same** WP1/WP2 pass (recommended), rather than
   leaving this as silent truncation in an informational-only table.
4. **`_ab_results` column-drop mechanics (WP3).** Confirm "CHANGELOG
   breaking-change note + recreate guidance" is sufficient (matching
   `ensure_tables()`'s existing create-if-not-exists-only posture, no
   ALTER/migration tooling in this codebase) rather than shipping an `ALTER
   TABLE … DROP COLUMN` helper command.
5. **Explore lock split scope (WP4).** Should `/recompute` keep **any**
   coarse serialization, or is fully-concurrent `/recompute` (bounded only
   by the new fine-grained `cache_lock` + the bootstrap memo lock)
   acceptable? This changes behavior under fast double-clicking / slider-
   dragging in ways the current single-lock design implicitly prevented
   (e.g. two Tier-S resamples for the **same** cutoff running truly in
   parallel). **Recommendation: confirm** "wasted duplicate CPU under a
   race, never wrong numbers" is an acceptable trade — WP4's own design note
   makes this case.

Additional "before start" checks from the track plan (per-milestone
discipline, not new questions): pin the field names as decided in Q1 before
WP1 begins; settle Q2 (wall-clock vs. disallow) before WP1 step 6 is
implemented, not after; confirm the `_ab_experiments` widen (Q3) explicitly
before WP2 touches `tables.py`.

---

## 5. Dependencies (incl. inter-milestone collisions)

- **Intra-milestone:** WP2 depends on WP1 (§2); WP3 is independent and may
  run in parallel with WP1/WP2 or in either order; WP5 should land after WP4
  (§2).
- **M8 → M10 (no direct code dependency, but a shared discipline):** M8's
  `build_cohort_backend`/`ab_cohort_source` factory is the only sanctioned
  way to build cohort SQL from M9 onward — M10's WPs do not touch cohort SQL
  at all (WP1/WP2/WP3 are timestamps/schema, WP4/WP5 are explore-server
  concurrency), so this milestone has no interaction with that factory, but
  it must not be reintroduced accidentally by any new call site this
  milestone adds.
- **M10 → M11 (blocking, forward dependency):** [m11-implementation-plan.md](m11-implementation-plan.md)
  (the `abk dashboard` flagship) clones `tuning/server.py`'s shape **after**
  this milestone's WP4 lands, so the dashboard server inherits the decoupled
  `heavy_lock`/`cache_lock` model from day one instead of cloning the old
  single-lock design and having to redo the split later.
- **Track-wide schema-break collection (§0.3):** M10 is where **both**
  breaking schema changes of the whole 0.2.0→0.12.0 track land — the
  `_ab_results` date-column drop (WP3) and the `_ab_experiments` widen
  (WP2) — specifically so operators get one recreate guide, once, instead
  of a breaking note in two different milestone releases.
- **Release discipline (unchanged from M1–M9):** one WP = one session = one
  PR (tests + CHANGELOG + conventional commit); the milestone exit gate is
  e2e + ≥2 adversarial review rounds with written findings; the three-way
  docs sync (`docs/` + `.claude/rules/` + the packaged `init-claude` assets)
  + wheel-namelist + pip-smoke gates run before the `0.5.0` tag; `web/`
  changes in this milestone are limited to none expected (WP4/WP5 are
  server/stats-core only, no `web/src/**` edits) — if a later review finds
  an explore-client-visible behavior change is needed, `cd web && npm run
  build` and commit the bundle in the same PR per the standing rule.
