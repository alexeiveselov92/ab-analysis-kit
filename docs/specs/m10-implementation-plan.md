# M10 Implementation Plan — timestamps + schema cleanup + explore polish

> **Implementation record — M10 shipped in full (WP1–WP5 + the exit gate),
> 2026-07-25/26; released as `0.5.0` — tagged `v0.5.0`
> and published to PyPI.** Written as the as-designed
> contract for M10 (track approved 2026-07-18) in the shape of
> [m4-implementation-plan.md](m4-implementation-plan.md) /
> [m6-implementation-plan.md](m6-implementation-plan.md), and amended in place
> at the exit gate into this record (the m4–m9 pattern). The WP bodies below
> keep their original contract wording ("WP2 adds…") as the designed
> baseline; the **"done" table** below, the **per-WP as-built notes** (the
> block quotes opening each WP), the **exit-gate record** appended to §3 and
> the **adversarial-review record** in §6 are the authoritative as-built
> account. Where a WP body and its as-built note disagree, the note wins.
>
> **Read [§4](#4-decisions--settled-by-the-maintainer-2026-07-25) BEFORE any WP.**
> All five "before start" questions were settled by the maintainer on
> 2026-07-25 and two of them **overturn recommendations still written into the
> WP bodies**: `start_date`/`end_date` are **renamed** to
> `start_ts`/`horizon_ts` with no compatibility aliases (D1), and grid
> anchoring becomes the configurable `interval_anchor` knob rather than a
> fixed rule (D2). Where a WP body and §4 disagree, §4 wins.
>
> Governing specs: [cumulative-intervals.md](cumulative-intervals.md) (§6 the
> window-column contract, the CUPED whole-day pre-period rule),
> [declarative-config.md](declarative-config.md) (§3 the `start_date`/`end_date`
> config fields, the `ab_start_date`/`ab_end_date` SQL builtins),
> [data-contract-and-reporting.md](data-contract-and-reporting.md) (§5 the
> `_ab_results` window columns), [ROADMAP.md](../../ROADMAP.md) M10. Sibling
> milestone docs: [m9-implementation-plan.md](m9-implementation-plan.md) (M10
> was expected to depend on nothing from M9 code-wise — **it did**: three M9
> surfaces broke on a timestamped start, see WP1's as-built notes),
> [m11-implementation-plan.md](m11-implementation-plan.md) (clones
> `tuning/server.py` **after** this milestone's WP4, which has landed — it
> inherits the decoupled lock model).
>
> Source: `~/.claude/plans/report-md-replicated-truffle.md` (the approved
> polish-track plan, M10 section) + the canonical detailed WP breakdown
> `~/.claude/plans/abkit-v2-details/design_time_explore.json`, cross-checked
> against `~/.claude/plans/abkit-v2-details/verify_time_explore.json` (code-verified
> file:line facts, treated as ground truth for citations in this document).

## Status — all work packages shipped (the "done" table)

| WP | Landed as | Load-bearing as-built delta (details in the per-WP notes) |
|---|---|---|
| WP1 — config + planner core: sub-day timestamps | PR #61 (`a634f90`) | the fields are **renamed** `start_ts`/`horizon_ts` (D1) and a bare date is local midnight for BOTH edges, so the horizon is EXCLUSIVE and a ported `end_date` gains a day (D6); §0.2's call-site register was ~60% accurate and its central claim false — six further sites, three of them in M9 code; the knob reached nothing until `ExperimentConfig.grid()` became the one factory (AST-gated) |
| WP2 — propagate the anchors (CUPED, render-smoke, catalog, docs) | PR #61 (`a634f90`) — **inseparable from WP1** | a field rename cannot land half-way, so all four steps shipped in WP1's commit; the catalog change grew from "widen" into rename + widen + a new `interval_anchor` column storing the RESOLVED window in naive UTC (a BI join now lines up) |
| WP3 — drop the `_ab_results` date columns | PR #62 (`52f92da`) | the audit found a **live** `SELECT metric, end_date, …` in the quickstart and four specs naming `end_date` as the results grain — none of them in the WP's file list; the operator hazard is backend-asymmetric (PG/MySQL fail loudly, ClickHouse silently stamps `1970-01-01`), which is why the CHANGELOG's recreate note is combined and explicit |
| WP4 — decouple the explore request lock | PR #63 (`8f8d232`) | the removed lock did **two** jobs: it also CANCELLED superseded work (a 6-turn drag went 0.80 s → 3.40 s at 8.7× CPU without it) and BOUNDED concurrent computes — restored as `should_stop=` polling + a 2-slot admission semaphore, not a queue; `warnings.catch_warnings` is process-global and had to be replaced thread-scoped (`utils/warn_scope.py`) |
| WP5 — memoize bootstrap resampling across alphas | PR #64 (`eaa1476`) | the contract's key `(method_config_id, end_ts)` collides three ways — across metrics, across arm pairs, and across the identity-EXCLUDED `seed`, which IS the draw; as shipped `BootMemoKey` carries all of it and the cache **generation** rides IN the key, so a resample that lost a race to `/reload` is unreachable rather than stale (and the two locks are never nested) |
| exit gate — the §3 e2e + docs sync + the `0.5.0` cut | this PR | leg 1 found the one derived number that DID move (`horizon_seconds()` across DST — pre-m10 the config disagreed with its own grid) and leg 3 found the breaking-change remedy escaping as an uncaught traceback, so the message the release most needs never reached the terminal |

**Zero statistical numbers moved anywhere in the milestone** — no
`ALGORITHM_VERSION` bump in any PR, no `statistics-changes.md` deviation
entry, `abkit.stats` purity intact, `tests/golden` untouched. WP5's
bootstrap split is a pure refactor pinned per class (`from_samples` ==
`_resample` + `_finalize`, bit for bit); the window rename's numeric gate is
that an unchanged window persists unchanged `_ab_results` numbers, pinned by
the exit gate's pre-m10 golden (§3 leg 1) with **two disclosed exceptions**,
neither of them a persisted number: `horizon_seconds()` is now the true elapsed
length rather than a nominal day count, so it differs from the pre-m10 value by
exactly the UTC-offset change between the window's local edges (−30 min to
−24h by zone, and also for a permanent zone shift with no DST at all) — it
reaches config-lint's cadence gate and the readout's pre-horizon rationale
line; and for a `start_ts` on a local calendar day that never existed, the
series loses its pre-m10 ZERO-LENGTH opening look. Every WP PR carried its own adversarial review
(1–2 rounds each, findings fixed in-PR before merge); §6 is the record and
the milestone-level exit gate is recorded at the end of §3.

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
2. **Rename + widen** the `_ab_experiments` window columns (shipped in WP1,
   `aef6c66`) — a genuine discovery made while implementing WP1's type widen
   (`_ab_experiments` mirrors the config field directly: `catalog_record()` →
   `pipeline/driver.py` → `upsert_experiment`), not itself named in
   REPORT.md §8's explicit scope, but a real silent-truncation bug the type
   widen would otherwise introduce into an informational catalog table.
   **As built** (wider than this line originally said): `start_date`/
   `end_date` `Date` → `start_ts`/`horizon_ts` `DateTime64(3, 'UTC')`
   holding the RESOLVED window in naive UTC — the same frame as
   `_ab_results.start_ts`, so a BI join lines up instead of differing by the
   timezone offset — plus a new `interval_anchor` `String` column. Note this
   is a TYPE change, which `ensure_columns` (ADD-only) cannot migrate: the
   raised error names the drop-and-recreate remedy since the round-1 review.

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
  > **[Amended by WP5's as-built — this paragraph's key is WRONG; do not copy
  > it.]** As shipped the key is
  > `BootMemoKey(metric, name_1, name_2, end_ts, generation, method, canonical
  > resolved params)`, composed ONLY through `ExploreSession.boot_memo_key()`.
  > `method_config_id` is a hash of the method name plus its non-default
  > IDENTITY params, so `(method_config_id, end_ts)` collides across metrics,
  > across the arm pairs of a multi-arm experiment, and across the
  > identity-EXCLUDED `seed` (which IS the draw) — each collision a wrong
  > number, each pinned by a test that goes red under this paragraph's key.
  > And the lock order is not a rule but an absence: the memo purge runs
  > AFTER `install_cutoff` releases `cache_lock`, so the two locks are never
  > nested (an AST gate keeps it that way). See §WP5's as-built notes.

---

## 1. Work packages

### WP1 — Config + planner core: sub-day start/horizon timestamps ✅ SHIPPED (PR #61, squashed as `a634f90`)

> **Amended by the §4 decisions (2026-07-25) — read them first.** Three things
> in the body below are superseded: (a) the fields are **renamed** to
> `start_ts`/`horizon_ts` with no aliases (D1), so "existing configs stay
> byte-identical" no longer holds — the numeric gate replaces it: an
> unchanged window must persist unchanged `_ab_results` numbers; (b) grid
> anchoring is not a fixed rule to be chosen but the configurable
> `interval_anchor` knob — `midnight` (the absent-key behavior, written
> explicitly by the scaffold) | `start` | an explicit timestamp — with the
> engine rule "cutoffs = anchor + k·interval, kept strictly after start"
> (D2); (c) **step 3/step 5's type-branching horizon is dead** — a bare date
> is local midnight of that day for BOTH edges, so `horizon_ts` is the
> exclusive right edge and there is no `+1 day` bump anywhere (D6).
>
> **As-built notes (what the session actually found, beyond the contract):**
>
> - **§0.2's register was ~60% accurate and its central claim was false.**
>   "Only sites 2 and 8 reimplement date arithmetic; sites 3–7 and 9 need no
>   code change" missed six further sites, three of them in M9 code that
>   post-dates the register: `IncrementalBackend` compared a `date` against
>   the config field (`TypeError` on *every* cutoff under
>   `compute.incremental_reads`) and passed it as an `_ab_unit_state` day
>   key; the STATE stage seeded its day loop from it, carrying a `datetime`
>   into a `Date` column and into comparisons against `get_last_state_day()`;
>   and `horizon_seconds()`'s own `(end − start).days + 1`. §0.2's list of
>   nine *consumers* also omits two real `generate_grid` callers
>   (`explore.py`, `reconcile.py`) — there are **eight** call sites in all.
>   Line numbers throughout §0.2/§0.3 had drifted 20–70 lines.
> - **The knob reached nothing.** Adding `interval_anchor` to the planner
>   signature left all eight hand-copied call sites passing their old
>   argument lists — a decorative knob. Fixed by `ExperimentConfig.grid()`,
>   the one factory composing window + cadence + anchor (m8's
>   `build_cohort_backend` contract applied to the planner), pinned by an AST
>   gate (`tests/core/test_grid_factory_is_the_only_entry.py`). One of those
>   sites passed `timezone` **positionally as the 4th argument**, so any
>   future parameter inserted before `tz` would have silently re-bound it.
> - **Day-space comparison is gated on anchor phase.** A whole-day segment
>   `until` bound is compared in day space — the DST compensation that keeps
>   a 25h fall-back day from dropping a boundary look — **only while the
>   anchor shares `start_ts`'s wall clock**. Off-phase it reads as elapsed
>   seconds, which is its literal meaning. The boolean is pinned in BOTH
>   directions (mutating it either way fails a test).
> - **The STATE stage clamps the opening day** to `grid.start_ts`, so a
>   sub-day start cannot sum pre-experiment facts into day state; the CUPED
>   pre-period stays whole-day (`[midnight(D − lookback), midnight(D))`) and
>   is byte-identical at a midnight start.
> - **`_ab_experiments` stores the RESOLVED window in naive UTC**, not the
>   local config value — the same frame as `_ab_results.start_ts`, so a BI
>   join lines up instead of differing by the timezone offset.
>   `interval_anchor` is persisted alongside; it is deliberately **not**
>   folded into the m9 state identity (it moves cutoffs, never day
>   boundaries).
> - Gates: the full suite (2 214 tests) green with only config keys and
>   ported horizon values edited; zero `ALGORITHM_VERSION` changes; zero new
>   mypy errors (111 → 111).

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

### WP2 — Propagate sub-day anchors: CUPED lookback, SQL render-smoke, catalog table, docs ✅ SHIPPED INSIDE WP1 (PR #61, squashed as `a634f90`)

> **This WP has no separate session: the rename made it inseparable from WP1.**
> A field rename cannot land half-way — every consumer must move in the same
> commit or the package does not import — so all four steps below shipped
> with WP1. Read the body for the *intent*; the shapes it names are
> pre-decision and in places now dead. What actually shipped:
>
> - **Step 1 (CUPED pre-period)** — done, and stronger than described: the
>   window is derived from `grid.start_ts` at BOTH edges
>   (`[midnight(D − lookback), midnight(D))`), so a sub-day start keeps a
>   whole-day lookback instead of gaining a partial tail. Byte-identical at a
>   midnight start. Pinned by
>   `test_the_cuped_preperiod_stays_whole_day_under_a_sub_day_start`.
> - **Step 2 (render smoke)** — done, but not by the guard this step
>   proposes: the fixture window now comes from `experiment.start_instant()`,
>   the same resolver the grid uses, so `datetime.combine` is gone rather
>   than special-cased. Pinned by `test_a_sub_day_start_still_lints`.
> - **Step 3 (catalog table)** — done, wider than described: a rename **and**
>   a widen **and** a new `interval_anchor` column, storing resolved UTC. See
>   §0.3 item 2. Pinned by `TestExperimentsCatalogSchema` (the coverage gap
>   this step suspected was real — there was no `_ab_experiments` contract
>   test at all).
> - **Step 4 (docs)** — done across `cumulative-intervals.md`,
>   `declarative-config.md`, the guides, the packaged operator assets and the
>   landing page; the `ab_start_date`/`ab_end_date` builtins were left
>   untouched exactly as this step demands.
>
> Two consumers this WP's body claims need **no** code change did:
> `IncrementalBackend` and the STATE stage. See WP1's as-built notes.

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

### WP3 — Drop `start_date`/`end_date` from `_ab_results`; fix stale hints and comments ✅ SHIPPED (PR #62, squashed as `52f92da`)

> **As-built notes (what the session found beyond the contract):**
>
> - **The WP's own "re-run the grep, don't trust the prior pass" instruction
>   paid for itself.** A repo-wide audit found a **live** `SELECT metric,
>   end_date, … FROM abkit_internal._ab_results` in
>   `docs/getting-started/quickstart.md` — the twin of the `abk init` hint this
>   WP's step 4 names, which the step did not mention and a scoped grep over
>   `abkit/` could never see. The governing spec §6.3 of
>   [cumulative-intervals.md](cumulative-intervals.md) still declared `end_date`
>   a stored derived column, and four more docs (`architecture.md` ×3,
>   `PRINCIPLES.md`, `declarative-config.md`, `statistics-changes.md`) named
>   `end_date` as the results grain / anti-join key / seed-identity part where
>   the code has always used `end_ts`. All corrected here.
> - **A new gate closes the shape that hid it.** `tests/docs/`'s existing
>   window-key check is anchored to `^\s*name:` — the YAML **key** form — so a
>   `SELECT … end_date` sailed past it. The companion
>   `test_no_dropped_result_columns_in_pasteable_sql` bans the bare identifier
>   on every paste surface in any syntax, and asserts a **file count** so a
>   renamed directory cannot turn it into a silent no-op.
> - **The step-6 test rewrite went further than "delete or rewrite".**
>   `TestTimezoneDates` encoded a real past review finding (a Moscow
>   experiment's date must be the Moscow date), so instead of deleting it the
>   suite now proves the **replacement recipe** reproduces the dropped values
>   exactly — and gates each of its two corrections **separately**. That split
>   was forced by review: the original single test passed with the timezone leg
>   of the recipe deleted, because at UTC+3 `end_ts − 1µs` lands on the right
>   day by coincidence. The leg is only observable west of Greenwich (the
>   America/New_York case now pinning it). Mutating either leg fails a test.
> - **The operator hazard is backend-asymmetric, and the CHANGELOG says so.**
>   `ensure_columns` is ADD-only and nothing drops columns, so a pre-0.5.0
>   `_ab_results` keeps both. PostgreSQL/MySQL declare them `DATE NOT NULL`, so
>   the omitting INSERT fails loudly; **ClickHouse fills an omitted column with
>   its type default and silently stamps `1970-01-01`** — the one silent path,
>   called out explicitly in the combined recreate note.
> - Scope note: `statistics-changes.md`'s H2 row was edited to say `end_ts`.
>   That is a **factual correction to a stale field name** (the code has always
>   passed `end_ts` — the same correction step 5 makes in `rng.py`), **not** a
>   new deviation entry. No `ALGORITHM_VERSION` moved; `git diff -- abkit/stats
>   tests/golden` shows only the `rng.py` docstring.

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

### WP4 — Explore: decouple the global request lock (cheap tiers vs Reload/Auto-validate) ✅ SHIPPED (PR #63, squashed as `8f8d232`)

> **As-built notes (what the session found beyond the contract):**
>
> - **The lock was not the only thing the lock was doing.** Removing it from
>   `/recompute` exposed `warnings.catch_warnings`, which saves and restores
>   PROCESS-global state (the filter list, the recorder) and is documented as
>   not thread-safe. Two overlapping scopes cross-attribute warnings, an
>   "ignore" filter set by one silences the other (exactly Auto-validate's A/A
>   scoring × a concurrent `/recompute`), and exits in the wrong order leave a
>   finished thread's recorder installed — after which **every warning in the
>   process disappears silently**. The last shape was already reachable on
>   `main`: `abk run` fans experiments out over a `ThreadPoolExecutor`
>   (`driver.py:673`), so a guard could be persisted against the wrong
>   experiment's rows. All three abkit scopes now route through the new
>   `abkit/utils/warn_scope.py` (one process-global recorder installed by the
>   outermost scope, per-thread frame stacks); the stdlib's failure is pinned
>   as a test, so if `catch_warnings` ever becomes thread-safe the module can
>   be reconsidered.
> - **The lock is an invariant of the data structure, not a call-site
>   convention.** The contract's steps 4–6 place `with cache_lock:` at each of
>   the five sites; as built, `ExploreSession` owns the lock and exposes
>   `loaded`/`cached_entry`/`cached_cutoffs`/`cached_entries`/`install_cutoff`/
>   `cached_value_count`/`disable_cache`, and an AST gate
>   (`tests/tuning/test_session_cache_lock.py`) refuses any `session.cache*`
>   access outside `session.py`. This is the WP1 lesson applied — a discipline
>   spread over N call sites is forgotten at the N+1st — and the gate proved it
>   immediately: it found a **sixth** site the contract's enumeration missed
>   (`tuning/payload.py`'s baked `cache.values`).
> - **The torn pair is demonstrated, not just prevented.** A dict subclass
>   freezes `/reload` between installing the entry and installing its lookback
>   tag; the test then shows the OLD two-read shape observing the tear
>   (fresh 9-value entry + stale `"7d"` tag) and `cached_entry` blocking
>   instead. Mattering, not cosmetic: at the Tier-S gate a fresh entry paired
>   with the previous tag is a 14d-rendered cutoff scored as a 7d one, labelled
>   `exact`.
> - **`heavy_lock`'s remaining job is pinned in both directions.** One test
>   proves a knob turn answers *while* a frozen `/reload` (and a frozen
>   `/validate`) holds it; another proves a `/validate` still cannot start
>   while `/reload` holds it. Both force the overlap with events, so neither
>   can pass by timing luck.
> - The post-compute re-check needed an engine proxy that freezes only the
>   marked request's compute to be testable at all: the superseded request must
>   reply 409 *after* the newer one has already answered 200.
> - **The lock was also the CANCELLATION point — the review's biggest find.**
>   Its post-lock `is_stale` re-check killed every queued request a newer knob
>   turn had outranked, so a knob-drag burst cost ONE compute. Deleting the
>   queue deleted that: measured on a 6-turn drag, the answer the user waits
>   for went 0.80 s → 3.40 s at 8.7× the CPU (and up to ~3× peak RSS — every
>   superseded bootstrap holds its own resample block). The queue is NOT back:
>   `recompute()` takes a `should_stop` predicate, polls it between points, and
>   raises `RecomputeSuperseded` → the same 409. Restores 1.04 s / 1.14 CPU-s
>   while keeping WP4's win (never waiting on `/reload`/`/validate`). §0.4(e)'s
>   "post-compute re-check" is necessary but was **not sufficient** — that is
>   the correction this WP carries back into the contract.

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

### WP5 — Explore: memoize bootstrap resampling across alpha-only changes ✅ SHIPPED (PR #64, squashed as `eaa1476`)

> **As-built notes (what the session found beyond the contract):**
>
> - **The prescribed key `(method_config_id, end_ts)` is not enough — it
>   collides three ways, each of them a wrong number.** `method_config_id` is
>   a hash of the method name plus its non-default IDENTITY params, so it
>   carries neither *which data* was resampled nor two params that reach the
>   draw:
>   1. **across metrics** — one session serves every comparison, so two
>      sample-typed metrics under the same method and cutoff share a key while
>      resampling completely different arrays;
>   2. **across arm pairs** — a multi-arm experiment computes
>      `(control, treatment)` and `(control, treatment2)` at the same cutoff
>      under one identity;
>   3. **across `seed`** — identity-EXCLUDED by design (baseline fact #3), yet
>      the per-row derived seed IS the draw. (`max_block_bytes` is also
>      identity-excluded and also carried, but as belt-and-braces only: it is
>      block-invariant by the engine's contract — measured byte-identical across
>      five block sizes on both engines — so carrying it costs at most a missed
>      hit, and a missed hit costs a resample where a wrong hit would cost a
>      wrong number. Same for `pvalue_kind`, which `_finalize` reads. Narrowing
>      the key to the draw-affecting params behind a declarative `ParamSpec`
>      flag is a **named follow-up**, not a fix.)
>   As built the key is a `BootMemoKey` NamedTuple — `(metric, name_1, name_2,
>   end_ts, generation, method, canonical resolved params)` — i.e. everything
>   the draw is a function of, minus alpha. All three collisions are pinned by
>   tests that go red under the contract's key
>   (`tests/tuning/test_recompute.py::TestBootstrapMemo`).
> - **The step-6 amendment's `cache_epoch` is per CUTOFF, and it is in the
>   KEY, not a check at insert time.** `install_cutoff` bumps
>   `cache_generation[(metric, end_ts)]` and `cached_entry()` returns it inside
>   the SAME critical section as the entry and the lookback tag (the WP4 triple,
>   now widened). A resample memoized against generation *n* is therefore
>   unreachable after a reload rather than merely purged — the interleaving WP4's
>   review demonstrated costs a discarded resample, never a stale hit. One
>   consequence is better than the contract asked for: since correctness no
>   longer needs the purge, the purge is housekeeping and runs AFTER
>   `install_cutoff` releases `cache_lock`, so `cache_lock` and `boot_memo_lock`
>   are **never nested** and §0.4(f)'s lock-ordering rule has nothing to order.
> - **The split returns a `ResampleOutcome` NamedTuple, not the prescribed
>   3-tuple.** `_finalize` already accepted the caller's `value_1`/`value_2`
>   (the M7 WP1 A4 hoist), and four of the six classes pass them; a 3-tuple
>   would have silently dropped that optimization and made the memo recompute
>   `stat_point` per alpha. `warnings` is a TUPLE for a reason the contract
>   could not have known: `_finalize` APPENDS its H5 warning to the list it is
>   handed and stores that same list on the `TestResult`, so a shared mutable
>   list would have grown one duplicate warning per alpha (pinned).
> - **Warnings have two channels and both had to be replayed.** Besides the
>   result's own `warnings` list, `_compare` captures `AbkitStatsWarning`s
>   raised DURING the call. A hit never re-runs the resample, so its captured
>   messages are stored in the memo entry and re-attached ahead of the finalize
>   step's — the same order one capture around the whole `from_samples` produced.
> - **The capability is declared, not sniffed** (`supports_resample_memo`,
>   mirroring M7's `supports_vectorized`): the engine dispatches on the flag and
>   falls back to the verbatim `_compare` for anything else — including a pair
>   shape `compare_pair` would have routed to `from_suffstats`. A registry
>   roster gate keeps the flag, the `_resample` override and the inherited
>   template `from_samples` in step, so a future plugin cannot half-adopt it.
> - **Measured** (4 000 units × 10 000 replicates × 4 cutoffs, six alpha turns):
>   6.01 s → 1.01 s total; per turn 1.00 s → **0.002 s** after the first. At a
>   modest 1 000 units × 2 000 replicates the warm drag is 20× faster. The
>   budget is counted in replicate VALUES (≈16 MB), not entries — `n_samples`
>   is a live knob, so an entry cap bounds nothing; an entry bigger than the
>   whole budget is refused rather than admitted-then-thrashing.
> - **File-list deviations:** the bootstrap method tests live in
>   `tests/stats/test_bootstrap_methods.py` (there is no `tests/stats/bootstrap/`
>   package), and the session-level memo discipline is pinned in
>   `tests/tuning/test_session_cache_lock.py` beside the cache it shares a
>   lifecycle with.

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
6. **[Amended by WP4's review — read this before implementing step 6.]** The
   purge extends `ExploreSession.install_cutoff` (WP4 made the session the one
   owner of the cache lock; `server.py` never touches `cache_lock`), and the
   §0.4(f) lock order is honoured there. But a purge alone is **provably
   insufficient now that `/recompute` is unserialized**, and WP4's review
   demonstrated the losing interleaving: a Tier-S reader reads the PRE-reload
   entry through `cached_entry()`, the reload then installs + purges, and the
   reader finally inserts its resample — keyed to data that no longer exists.
   Give the guarded set a monotonic `cache_epoch` bumped inside
   `install_cutoff`, return it from `cached_entry()`, and insert into
   `boot_memo` only if the epoch still matches (re-read under `cache_lock`,
   insert under `boot_memo_lock`): the race then costs a discarded resample
   instead of a stale hit. The original step-6 text follows.
   Invalidate `boot_memo` whenever the underlying raw cache changes:
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
2. Adds one **new** fixture with an explicit sub-day `start_ts`/`horizon_ts`
   (e.g. `start_ts: '2024-07-01 14:30:00'`) and asserts the grid anchors
   at that instant with no midnight snap, validator/plan/driver/explore all
   accept it, and CUPED lookback still lands on a whole-day boundary. It must
   also drive a cutoff INSIDE the opening local day (sub-day cadence, or an
   off-phase `interval_anchor`) — the shape that hid two silent-wrong-number
   defects from WP1's own tests, see §6.
3. Runs `abk run` against a fresh ClickHouse (testcontainers, matching the
   project's existing `e2e-clickhouse` CI job) and confirms `_ab_results` is
   created **without** `start_date`/`end_date` and `_ab_experiments` **with**
   `start_ts`/`horizon_ts` `DateTime64(3)` + `interval_anchor`, that a
   pre-m10 `_ab_experiments` fails with the drop-and-recreate message rather
   than a bare type error, and that `abk init`'s printed hint / BI docs no
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
document's **§6** adversarial-review record (mirroring
[m4](m4-implementation-plan.md) §5 / [m6](m6-implementation-plan.md) §0.5 —
in *this* document the review record is §6, since §5 is Dependencies), and
cross-check the coverage map in [ROADMAP.md](../../ROADMAP.md) (REPORT
#9–#12 → M10).

### Exit-gate record — 2026-07-26 (`tests/e2e/test_sub_day_anchors_and_explore.py`)

> **All four legs land in one new e2e module** (23 tests) plus two tests added
> to the Docker-gated `tests/e2e/test_first_run_clickhouse.py` for the single
> claim an in-memory backend cannot express. Suite after round 1's additions:
> **2 369 passed, 6 skipped** (2 343 on `main` + 26 new; the skips are the 4
> Docker-gated ClickHouse tests — 2 of them this gate's — plus the MySQL-mock
> and `ABK_BENCH` cases that were already skipping). Zero
> `ALGORITHM_VERSION` changes; `tests/golden` untouched; mypy 111 = `main`'s
> baseline.
>
> **Three corrections this gate forced on the gate's own wording:**
>
> 1. **Item 1's "real existing fixture YAML configs from `tests/fixtures`"
>    describes a directory that does not exist** — there is not one `.yml`
>    under `tests/`; experiment documents are built in code and the only real
>    on-disk config is the `abk init` scaffold. And post-D1 a fixture carrying
>    bare `start_date`/`end_date` would not validate at all. Re-expressed
>    honestly: `capture_window_surface()` enumerates 19 window shapes (the
>    ones `tests/core/test_period_planner.py` pins: whole days, Moscow, both
>    DST directions, sub-day steps, a non-dividing cadence, a sub-week window,
>    two dense-early schedules), the golden was **captured by running that
>    same function at `f85371d`** — the last commit before WP1 — and the
>    module docstring says regeneration may only ever happen from a pre-m10
>    checkout. A golden re-captured at HEAD would compare HEAD with itself.
> 2. **TWO numbers moved, and round 1 corrected this entry's first attempt at
>    saying so.** Grids, cutoff sets, `window_seconds`, `elapsed_days`,
>    `weekly_cycle_pct`, `look_days`/`horizon_days` and the CUPED pre-period are
>    byte-identical across all 19 shapes but one. What moved:
>
>    - **`horizon_seconds()`**, which was the nominal day count
>      `((end − start).days + 1) × 86 400` and is now the elapsed length between
>      the two resolved instants. This entry first said "±1h, only across DST",
>      and a lens measured that wrong in **both** halves: over 8 added shapes the
>      delta is −30 min (Australia/Lord_Howe), −2h (Antarctica/Troll), −24h
>      (Pacific/Apia's 2011 line jump) and **+1h with `dst() == 0` on both
>      sides** (Moscow's 2014 permanent +4→+3 shift). So the gate carries no
>      waiver list at all: it asserts the LAW — the delta equals the UTC-offset
>      change between the window's **local** edges, for every case, 7 of the 19
>      exercising it. The offsets must be read at the local wall clock, not at
>      the resolved instant: on Apia's skipped day the resolved instant sits past
>      the jump and loses the 24h that is the whole point. Pre-m10 the config
>      disagreed with its OWN grid (the fall-back case's `horizon_days` was
>      already 12.0416…), and the two now agree — the honest framing of the
>      change. Consumers: config-lint's cadence gate — where a sub-day cadence
>      between the two lengths can flip accept↔reject — and the readout's
>      pre-horizon rationale line. No persisted `_ab_results` column derives
>      from it.
>    - **the grid of one shape**: a `start_ts` on a local calendar day that never
>      existed (Pacific/Apia, 2011-12-30). Pre-m10 the start and the first daily
>      lattice point resolved to the same instant, so the series opened with a
>      ZERO-LENGTH look; m10 keeps cutoffs strictly after the start and drops it.
>      Pinned exactly — window unchanged, one fewer look, every survivor
>      byte-identical — rather than waived, which is what WP1's own round asked
>      for.
> 3. **Item 3's "fails with the drop-and-recreate message rather than a bare
>    type error" was half-true, in the worse half.** `ensure_columns` raised
>    the right message, but `tables.ensure_tables()` sat OUTSIDE the driver's
>    `except BaseException` handler, so it escaped as an uncaught `ValueError`:
>    Click's standalone mode printed a stack trace and `abk run`'s own error
>    line never appeared — `result.output` carried nothing. The one failure a
>    real operator hits on this release's breaking change was the one whose
>    remedy was buried (the M7 WP6 lesson repeating: a message the user must
>    read has to be echoed as a CLI line). Fixed in the driver and in
>    `abk unlock`, which had the same hole. All six surfaces were then audited
>    against a fabricated pre-m10 catalog: `run`, `unlock`, `validate` and
>    `clean` each exit 1 with the remedy on the terminal and no traceback
>    (`validate`/`clean` already did); `verify-incremental` and `abk explore`
>    are read-only, never call `ensure_tables()`, and correctly do not fail on
>    it. Auto mode's `/validate` already returns the message as a 400 body.
>
> **What the four legs assert** (§3's items, in order):
>
> - **Leg 1** — the pre-m10 golden above, plus a coverage test asserting the
>   golden holds a case for every entry in `WINDOW_CASES` (a case dropped from
>   the golden would otherwise pass by never being compared).
> - **Leg 2** — a 09:00 start anchors at the instant with two cutoffs INSIDE
>   the opening local day (the shape §6's WP1 round 1 says hid two
>   silent-wrong-number defects); the opening look persists a real 3h
>   `window_seconds`; the catalog stores the resolved instant + the anchor;
>   `interval_anchor: start` moves the lattice onto the start's own phase without
>   moving the edges (in the planner's vocabulary that is IN phase — round 1
>   caught this line calling it "off-phase", which is what the `midnight` default
>   is here); the
>   CUPED pre-period stays whole-day; and the timestamped start is accepted by
>   config-lint, the driver, `abk plan`, `abk validate` and `abk explore` — the
>   last three being surfaces no other suite drives off a sub-day start. Day
>   state's opening-day clamp is made **falsifiable** by a second fixture
>   starting at 14:30, after the seed's 12:00 facts: the day is left
>   unmaterialized, where an unclamped render would have summed
>   pre-experiment events into it. The additive read path reconciles the whole
>   sub-day series (`11 matched`, zero `unverified`) and reproduces the
>   recompute numbers — §3(a)'s re-check of the three M9 call sites, executed
>   rather than reasoned.
> - **Leg 3** — `_ab_results` created without the dropped columns, the catalog
>   created with `start_ts`/`horizon_ts`/`interval_anchor`, and a fabricated
>   pre-m10 catalog table refusing to migrate with the remedy **on the
>   terminal**. On real ClickHouse: the live types (`DateTime64(3, 'UTC')`,
>   `String`), the resolved window round-tripping, and the refusal leaving the
>   stale table untouched rather than half-migrated. That no shipped hint or BI
>   recipe names the dropped columns is the standing text gate
>   `tests/docs/test_no_stale_window_keys.py` (+ WP3's
>   `test_no_dropped_result_columns_in_pasteable_sql`) — not duplicated here.
> - **Leg 4** — over real HTTP, a knob turn answers 200 while a REAL reduced-N
>   Auto `/validate` still holds `heavy_lock` and has not replied. The proof is
>   the ORDER, not a duration: a queued request could not have answered at all
>   (§3(b)). Five alphas over one bootstrap knob draw the replicates exactly
>   once per look (10 draws for 11 points — the empty opening look has nothing
>   to draw), with an empty `warnings` list proving the budget refused nothing
>   (§3(c)). The parity oracle takes the OTHER path — `supports_resample_memo`
>   off, so the engine runs verbatim `compare_pair` per alpha, 50 draws vs 10 —
>   and every number over the wire is identical. WP5's own round 1 found the
>   engine-level parity gate comparing the memo path with itself; this one
>   cannot.
>
> **Every new gate was mutation-verified** (revert the fix → a named test
> fails): the driver echo (leg 3 goes red), the STATE opening-day clamp (leg
> 2), `supports_resample_memo` (leg 4's memo), `/recompute` back under
> `heavy_lock` (leg 4's lock split), a 1h shift in the resolved horizon and a
> changed CUPED pre-period edge (leg 1 and 2), and a tampered golden fixture.
> Tooling lesson: the first attempt at the horizon mutation was a silent
> string-replace no-op that still printed "applied" — a mutation script must
> assert its own edit landed, or it certifies nothing.
>
> The e2e `http()` helper now returns transport failures as
> `(0, "transport: …")` like its tuning-suite sibling (the WP5 round-2
> lesson): raised inside a thread it vanishes into a stack trace and leaves
> the caller asserting on a reply COUNT.

---

## 4. Decisions — SETTLED by the maintainer, 2026-07-25

> **All five questions below were answered before implementation started; the
> answers are binding and are NOT to be re-litigated at WP time.** The
> original question text is kept underneath each decision so the reasoning
> stays legible. Two answers overturn what this document originally
> recommended — read the decisions, not the recommendations.
>
> **The standing principle behind three of them:** abkit has no live external
> users yet, so "this breaks existing configs/schemas" is **not** an argument.
> The maintainer's instruction is to choose the correct, scalable design and
> document the break — never to carry a legacy shape for compatibility. That
> does not weaken the statistical invariant: numbers still never move
> silently (`ALGORITHM_VERSION` + change control).

### D1 — Rename `start_date`/`end_date` → `start_ts`/`horizon_ts` (clean break)

**Decision: RENAME. The plan's original "no rename" recommendation is
overturned.** The fields become `start_ts` / `horizon_ts`, matching the
vocabulary the engine already uses internally (`grid.start_ts`,
`grid.horizon_ts`). **No deprecated aliases**: a config carrying
`start_date`/`end_date` fails validation with an explicit "renamed to
`start_ts`/`horizon_ts`" error, so the break is loud and one-line-fixable.

Why: abkit is a *flexible-interval* system — sub-day cadences are a
first-class feature (cumulative-intervals.md §6) — and `*_date` names are a
leftover from the legacy code the project was asked to **rewrite**, not to
treat as truth. A field that can hold a timestamp must not be called a date.
The maintainer has raised this across several sessions; it is settled.

**Scope delta this creates for WP1** (the WP body below still describes the
pre-decision design): WP1 is no longer "byte-identical YAML, existing tests
unmodified are the gate". It now also carries a mechanical rename across
`ExperimentConfig`, every test fixture, the `abk init` scaffold, the packaged
`init-claude` assets and the docs. The *numeric* gate is unchanged and still
the real one: for an experiment whose window is unchanged, every persisted
`_ab_results` number stays identical — only the config key changed.

### D2 — Grid anchoring becomes an explicit, configurable knob

**Decision: add `interval_anchor` with three forms —
`midnight` | `start` | an explicit timestamp.** The engine rule generalizes to
one sentence: **cutoffs are `anchor + k·interval`, snapped forward to the
first point at or after `start_ts`.**

- `midnight` — local midnight of the experiment timezone (whole calendar days,
  what BI dashboards read). **This is the behavior when the key is absent**,
  and the `abk init` scaffold writes it out explicitly with the alternatives
  in a comment, so the choice is visible in the config rather than implicit.
- `start` — count from the experiment start (`14:00` start ⇒ cutoffs at
  `14:00` every day; 3-day segments run from the start instant). This is
  today's engine mechanics unchanged — today's grid is already
  `start_ts + k·interval` and merely *looks* midnight-anchored because
  `start_date` forces a midnight start.
- an explicit timestamp — align the grid to an external cycle. The concrete
  case that decided it: **3-day windows at 00:00 MSK on a UTC warehouse**,
  with the experiment starting a little before or after such a boundary. The
  anchor may therefore precede `start_ts`; the forward snap is what makes
  that well-defined, and the first window is legitimately partial.

This replaces the original question's "wall-clock generalization vs
disallow" fork: neither — it is a first-class option, because a system that
sells flexible intervals cannot hard-code where an interval begins.

### D3 — Widen `_ab_experiments.start_date`/`end_date` to `DateTime64(3)` in the same pass

**Decision: yes, in WP1/WP2.** Leaving a `Date`-typed catalog column while
the config can carry a sub-day timestamp is silent truncation, i.e. a bug —
not a design choice. (Column names in that table follow D1's rename too.)

### D4 — Dropping the `_ab_results` date columns: CHANGELOG note + ready-made SQL

**Decision: option 1 — a breaking-change note plus copy-pasteable
`ALTER TABLE … DROP COLUMN` / recreate SQL per backend. No `abk migrate`
tooling.** This matches `ensure_tables()`'s existing create-if-not-exists-only
posture; an irreversible DROP behind a convenience command is a bigger risk
surface than a documented one-time manual step, and there are no installed
users to shield.

### D6 — A bare date means local midnight of THAT day, for BOTH edges (settled at WP1 time, 2026-07-25)

**Decision: `horizon_ts` is the EXCLUSIVE right edge.** D1 renamed the field
but left one thing open: what a bare `date` means in a field now called
`horizon_ts`. The WP1 body (written pre-D1) branched on the type — `date` kept
the legacy "+1 day, inclusive of that day" convention, `datetime` was exact.
That is overturned: **one resolver for both edges**, a bare date is local
midnight of that day, and therefore

```
config.horizon_ts resolved  ==  grid.horizon_ts     (always, exactly)
```

Why: under type-branching a field named `horizon_ts` whose bare-date form
means "the day *after* me" is still an inclusive end **date** — precisely the
legacy shape D1 exists to delete — and `2024-07-14` vs `2024-07-14T00:00:00`
would denote instants a day apart in the same field. The rename's whole point
is that the config speaks the engine's vocabulary; this is the only reading
that delivers it.

**Cost, accepted:** porting is no longer a pure key rename — the horizon VALUE
moves one day (`end_date: 2024-07-14` → `horizon_ts: 2024-07-15`). Every
fixture, scaffold and doc example was ported accordingly, and the rename error
text spells the shift out. The numeric gate is unchanged and still the real
one: **an unchanged window persists unchanged numbers.**

### D5 — Explore: decouple the global request lock

**Decision: decouple.** Cheap tiers (alpha/method knobs) run concurrently;
the heavy paths (warehouse reload, Auto-validate) keep the lock. The accepted
trade is explicit: under a race two identical recomputes may both run —
wasted CPU, never a wrong number.

> **Amended at the exit gate (review round 1, reproduced 3/3):** "never a wrong
> number" is too strong as stated. Every individual point is still computed from
> immutable or lock-read inputs — but a `/reload` installing cutoffs UNDER a
> running `/recompute` pass makes one 200 reply mix two warehouse renders of one
> series, every point labelled `tier: "exact"`, which can show a cumulative arm
> mean moving backwards across consecutive looks (structurally impossible for a
> single render, and the stabilization chart is read for exactly that shape).
> The reply, not the point, is the unit of consistency the client needs. The
> symptom CLASS predates M10 — a `/reload` that fails mid-loop leaves an
> installed prefix and every later `/recompute` serves a mixed series
> permanently, on code WP4 did not touch, and WP4's own round 1 recorded the
> `post-normed-bootstrap` variant of it and deferred a per-series Tier-S
> consistency check to the hardening backlog. WP4 adds a new, silent, transient
> route to that recorded inconsistency; the accurate statement of the trade is
> **"wasted CPU, and a reply can mix two renders of one series — never a wrong
> number for the inputs it used."** One fix closes both (make the REPLY the unit:
> compare per-row generations, or a per-metric series epoch, at entry and exit,
> and 409 on a mismatch) — a named follow-up next to the deferred per-series
> check, not an exit-gate blocker.

---

<details>
<summary>The original questions, as posed before the decisions above</summary>

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

</details>

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

---

## 6. Adversarial review record

The m4/m6 pattern: per-round, per-finding, with the executed evidence. A
finding nobody ran is not recorded here.

### WP1 round 1 (`eaf5e47` + follow-ups) — 2 silent-wrong-number defects

Both at the window edges, both introduced by WP1, both found by a lens that
built the scenario and ran it rather than reasoning about the code.

| # | Defect | Severity | Fix |
|---|---|---|---|
| 1 | `IncrementalBackend`'s tail render opened at local midnight, unclamped. WP1 clamped the STATE *writer*'s opening day to `grid.start_ts` and left the *reader*'s mirror alone, so a cutoff landing inside the opening local day summed pre-experiment facts. No gap, no fallback, no warning. | silent-wrong-number | `RenderWindow(max(last_midnight, grid.start_ts), end_ts)` |
| 2 | A sub-day `horizon_ts` materialized a TRUNCATED trailing state day. Pre-m10 the `min(midnight(d+1), horizon_ts)` clamp was provably a no-op; widening the field made it live. A materialized day is never re-rendered and `horizon_ts` does not generally re-key the series, so extending the experiment kept summing the truncated day (~8× wrong effect, across runs). | silent-wrong-number | do not clamp — a trailing day the horizon would cut is not materialized at all; nothing reads it |

**The test lesson.** WP1's own `test_sub_day_start_parity` was written to cover
defect 1 and could not fail: daily cadence with a `midnight` anchor produces no
cutoff inside the opening day. Both defects now have tests that fail without
the fix (verified by reverting each), and defect 1's needs a fact seeded in
the pre-start hours to be observable at all.

**Also from round 1:** test-side `_grid()` helpers called `generate_grid`
directly and dropped `interval_anchor`, so a test grid could differ from the
production one (19 sites moved to `experiment.grid()`); the `ensure_columns`
error on a pre-m10 `_ab_experiments` never named the documented
drop-and-recreate remedy (and blocked `abk clean` too); the new "first look is
short" config-lint note measured elapsed seconds and therefore accused the
anchor at every spring-forward experiment (now gated on the anchor's PHASE);
the AST factory gate was evadable by an aliased import or `getattr` and
false-positived on a nested helper inside the factory; `abk plan` printed the
horizon as an unlabelled naive-UTC instant for non-UTC experiments (now echoes
the config value); plus docstring drift left by the rename.

**Verified clean in round 1, with the evidence:**

- pre-m10 vs m10 `generate_grid` over **40 000** randomized configs (10
  timezones incl. 30-min DST, 45-min offsets, Apia, Chatham; DST-straddling
  windows; 1–3 coarsening segments) — **0 mismatches** on `start_ts`,
  `horizon_ts` and every `(end_ts, is_horizon)`. Independently re-run by two
  more lenses over 5 760 and 300 000 combinations: 0 mismatches — **with one
  documented exception**, found only by an exhaustive sweep of every real
  tzdata transition 2009–2027 (146 412 grids): **38 grids in Pacific/Apia
  across the 2011-12-30 date-line skip**, where local 2011-12-30 does not
  exist. There the pre-m10 engine emitted a first cutoff EQUAL to `start_ts`
  — a look over an empty window — because it compared calendar-day offsets,
  which run one ahead of elapsed days after a line skip. The m10 forward snap
  works in instant space and cannot. The new grid is the correct one in all
  38; pinned by
  `test_a_date_line_skip_does_not_emit_a_zero_length_window`. State the
  byte-identity claim as "identical except where the old engine emitted a
  degenerate point".
- **60 000** anchored grids — 0 violations of: edges equal `resolve_instant`
  of the config values, non-empty, strictly ascending and deduped, first
  cutoff strictly after `start_ts`, last cutoff exactly the horizon with
  nothing past it, exactly one `is_horizon` flag last, `_snap_forward` cap
  never firing, no grid slower than 0.5 s.
- `local_date(tz_midnight_utc(d, z), z) == d` over **all 599 tzdata zones ×
  732 days** — 0 violations (the invariant the STATE/CUPED rewrites rest on).
- Every complete experiment YAML snippet in the docs, the packaged operator
  assets and the landing page validates (7 by one sweep, 15 by another).
- Zero `ALGORITHM_VERSION` changes; `git show <wp1> --stat -- abkit/stats
  tests/golden` empty.

**Round 1, second wave** (three more lenses, each running its own harness):

| # | Defect | Severity | Fix |
|---|---|---|---|
| 3 | `horizon_seconds()` became honest elapsed time, and the HARD cadence gate read a 23h spring-forward window as "a day does not fit in a day" — rejecting ordinary configs (a one-day daily experiment starting on the transition day; the very common "one week, look once at the end" weekly shape) whose grids had not moved at all. Nothing in the suite covered it. | crash-on-parse | `cadence_fits_horizon()` — a whole-day step compares in CALENDAR days, the space the planner steps in; sub-day steps keep seconds |
| 4 | Sub-second precision validated but is unrepresentable end to end: the rendered SQL window formats to whole seconds and `_ab_results.end_ts` is `DateTime64(3)`, so a microsecond cutoff would persist rounded, never match the planned instant, and re-plan every run — forever. | silent-replan-loop | reject `microsecond != 0` alongside the existing offset/number rejections |
| 5 | A calendar-edge window raised a raw `OverflowError` out of `model_validate` (pydantic wraps only `ValueError`), naming neither field nor cause. | UX | resolve the edges inside `validate_window` and re-raise as a `ValueError` naming both fields and the timezone |

**The adjudicator's own pass** (re-running every lens finding, then probing
what the lenses covered least — the explore/tuning + persistence surface)
found one more that no lens saw, and that the round-1 gate fix did not close:

| # | Defect | Severity | Fix |
|---|---|---|---|
| 6 | The cadence gate still did ARITHMETIC about whether a cutoff exists. With `interval_anchor` that stopped being a property of the step length: a lattice hung off midnight can put a real cutoff inside a window SHORTER than its own step. A 6 000-case property vet found 6 such wrongly-rejected configs; the same vet finds 454 with the arithmetic-only gate and **0** once the gate enumerates. | crash-on-parse | keep the two cheap accepts, then ask the planner — `any(not c.is_horizon for c in self.grid().cutoffs)`. Only reached when the arithmetic says "too long", so the enumerated grid is always tiny. |

It also verified, and cleared, two things worth recording: the explore **Apply
seam** round-trips all four window shapes with an identical grid and preserved
`date`-vs-`datetime` typing (the type reaches the state identity hash); and
editing `interval_anchor` on a live experiment interleaves new rows with old
— which is **pre-existing behavior**, not new, since editing `cadence` does
exactly the same at `f85371d`.

**Self-review, same round:** the planner raised an uncaught `OverflowError`
for a window butting against the representable calendar edge (year 1 / 9999).
A lattice step off the end now saturates, which every comparison in the
enumeration already reads correctly as "beyond that edge". (Pre-m10 raised
identically, so it is a hardening, not a regression.)

### WP2 — no separate round (its review is WP1's)

WP2 shipped inside WP1's commit (a field rename cannot land half-way), so the
rounds above cover it: the CUPED whole-day pre-period, the render-smoke's move
onto `start_instant()`, and the `_ab_experiments` rename/widen/`interval_anchor`
column were all in the diff those lenses read. The catalog change is where a
reviewed-in-flight discovery landed — there was no `_ab_experiments` contract
test at all before it (`TestExperimentsCatalogSchema` is new), and the
ADD-only guard's refusal message gained its drop-and-recreate remedy in that
round.

### WP3 (PR #62) — review folded into the WP, 2 findings that changed the diff

WP3's round is recorded in its as-built notes above and in the PR body rather
than as a separate round table; both findings are load-bearing enough to name
here:

| # | Defect | Severity | Fix |
|---|---|---|---|
| 1 | **The replacement recipe's test passed with half the recipe deleted.** `TestTimezoneDates` checked `end_ts − 1µs` read in the experiment timezone against the dropped `end_date` — but at UTC+3 (Moscow) the timezone leg is unobservable: dropping it lands on the right day by coincidence. A BI user west of Greenwich would have followed a recipe the suite called proven. | silent-wrong-value in shipped docs | split the assertions so each of the two corrections (exclusivity, UTC) is gated separately, and add an `America/New_York` case where the timezone leg is observable. Mutating either leg now fails a test. |
| 2 | **The existing docs gate could not see the shape that mattered.** `tests/docs`' window-key check is anchored to the YAML **key** form (`^\s*name:`), so a live `SELECT metric, end_date, … FROM _ab_results` in `docs/getting-started/quickstart.md` sailed past it — and a scoped grep over `abkit/` could never have found it either. | stale pasteable SQL shipped to users | `test_no_dropped_result_columns_in_pasteable_sql` bans the bare identifier on every paste surface in any syntax and asserts a scanned-file COUNT, so a renamed directory cannot turn it into a silent no-op. It immediately caught one of that same commit's own doc edits. |

### WP4 round 1 (`708b8a5` + `fdb103c`) — 5 lenses, 10 refutations

Five lenses (concurrency, the warning primitive, behavior-preservation + the
client contract, test mutation, contract/docs compliance), each required to
BUILD and RUN its scenario, then one skeptic per finding tasked with refuting
it. Every defect below reproduced deterministically; every fix below is
mutation-checked (revert it and a named test goes red).

| # | Defect | Severity | Fix |
|---|---|---|---|
| 1 | **The lock was also the CANCELLATION point.** Its post-lock `is_stale` re-check dropped every queued request a newer knob turn outranked; unserialized, all of them ran to completion instead. Measured on a 6-turn drag: the answer the user waits for went 0.80 s → **3.40 s**, at **8.7×** the CPU and up to ~3× peak RSS (each superseded bootstrap holds its own resample block). | perf/resource regression | `recompute(..., should_stop=)`, polled between points → `RecomputeSuperseded` → the same 409. Restores 1.04 s / 1.14 CPU-s with no queue reintroduced. §0.4(e)'s post-compute re-check is necessary but was **not sufficient**. |
| 2 | **`_route` could delegate to itself.** A foreign `catch_warnings()` that opens after the router is installed and closes after the last scope leaves restores `_route` behind the module's back; the next scope adopted it as its own delegate — every warning silently dropped, then `RecursionError` **raised out of `warnings.warn`**, i.e. out of a live compute. Not theoretical: instrumenting a real `abk run` + `abk validate` caught **pandas entering `catch_warnings()` 15 times inside abkit capture scopes**. | crash + silent loss | never adopt the router as its own delegate; resolve the handling live instead of dropping; evict an unowned router (`_depth == 0`) back to the last handling it displaced. |
| 3 | The install was not atomic: an exception between `guard.__enter__()` and `_depth += 1` (e.g. `capture_warnings("not a class")`) orphaned the router permanently — which then became defect 2. | medium | validate the category before touching any global, and roll the install back on any `BaseException`. |
| 4 | Teardown cleared `_delegate` **before** un-installing, so a frameless thread's warning inside that window reached a router with nothing to hand it to. | low | restore first, clear after. |
| 5 | **Four of the primitive's own tests — and BOTH "production call site" tests — passed with the primitive reverted to the stdlib.** They drove LIFO-nested interleavings, which `catch_warnings` handles correctly; `validate/scoring`'s suppression had no failing coverage at all and `pipeline/analyze` had no test. | high (a gate that cannot fail) | the interleavings now overlap (capture open first, the peer warns inside it); hazard 3 asserts DELIVERY through an ambient sink installed **before** the interleave; and an AST gate bans `catch_warnings`/`simplefilter`/`filterwarnings`/`showwarning =` anywhere in `abkit/` outside `warn_scope.py` — covering all three call sites and every future one. 15 of 23 now fail against the stdlib. |
| 6 | Three of the four cache-lock tests passed with `cache_lock` removed: the scan installer recycled 27 keys so the dict stopped resizing, and 4×60 installs at the default 5 ms switch interval never interleaved the read-modify-write. | medium (same) | growing keys + 2 000 pre-filled entries; `sys.setswitchinterval(1e-6)` + 3 000 installs; plus a new test that instruments the lock itself to prove `cached_entry` reads the pair in ONE critical section (a two-locked-reads implementation now returns a torn pair and fails). |
| 7 | `/apply`'s `heavy_lock` was untested — dropping it left the whole tuning+CLI suite green, though the one-shot `srv.applied` check and the YAML archive/rewrite seam both live inside it. | low | the third pairing added to `TestLockDecoupling`. |
| 8 | Three docstrings still said "the request lock" after the rename — the exact conflation §0.4(e)'s risk note warns about, in the file M11 is scheduled to clone. | low | renamed to `heavy_lock`. |

**Verified clean, with the evidence:**

- **Reply parity against `main`** over a scripted session (the payload block,
  `knob_surface`, `/recompute` in nine shapes incl. every 400 and the
  stale-409, Auto `/validate`, `/apply` incl. the rewritten YAML, a CUPED
  `/reload` 7d→14d round trip, and a budget-degraded session):
  **byte-identical, 78 963 bytes both sides** — the only diffs the apply
  timestamp and the tmpdir path.
- No reentrancy hazard: no accessor is ever called with `cache_lock` held, and
  an AST walk found no in-place mutation of anything `recompute()` reads
  (`session.aa_rows` is only ever rebound — the lock-free claim holds).
- The warning primitive under stress: 24 threads × 40 randomized
  capture/suppress/nested scopes with injected exceptions — 0 hangs, 0
  cross-thread mixups, every global restored. `warnings.filters` does **not**
  grow with scope count (500 sequential / 200 deep / 2 000 concurrent scopes:
  5 → 6 entries). Note what that stress did NOT cover, and round 2 did: every
  thread was inside a scope, so it never exercised a **frameless** warner
  racing an install — the shape of round 2's defect 1.
- The routing cost is real but small: +0.68 µs per suppressed warning; on a
  realistic A/A cell firing the CUPED guard on every arm (1 500 units × 10
  looks × 400 iterations = 8 002 warnings) `main` 0.69 s vs 0.72 s, identical
  FPR. `abk run` / `abk validate` stdout is byte-identical and stderr empty on
  both trees.
- Zero `ALGORITHM_VERSION` changes; `git diff main..HEAD -- abkit/stats
  tests/golden` empty; `tests/stats/test_purity.py` and `tests/golden` green.

**Found, verified, and deliberately NOT fixed here** (recorded so nobody
re-derives it): `_cache_serves` compares the entry's `covariate_lookback` tag
only for methods that DECLARE a `covariate_lookback` param, so
`post-normed-bootstrap` (`requires_covariate = True`, no such param) will
serve a mixed cache — two covariate windows in one series, every point
labelled `exact`. The skeptic established this is **pre-existing, not
WP4-caused**: `_handle_reload` already leaves an installed prefix in place
when a render fails mid-loop, so `main` reaches the same mixed cache with no
concurrency at all. The one-line fix the lens proposed is also wrong — a
post-normed method has no lookback to compare, so it would lose Tier S
entirely. The correct fix is a per-SERIES lookback-consistency check: a
change to the Tier-S gate, which belongs in the hardening backlog rather than
inside a lock-scoping WP.

**The client was cleared, twice.** A lens filed the mid-reload knob turn as a
lost-Reload regression; the skeptic showed both reproduction paths are
unreachable from the real UI — `knobChanged`/`onMethodSwitch` already clear
the debounce timer on the Tier-R edit that raises the Reload bar, and while
that bar is up every knob turn still reads `needsReload` and dispatches
nothing. The delta is real for a raw HTTP client only. `web/src/**` is
therefore untouched, exactly as the WP predicted.

### WP4 round 2 — the fixes attacked in turn

Three lenses over the round-1 fix delta only (the paired skeptics were lost to
a session limit, so every finding below was instead reproduced and
mutation-checked by hand before it was fixed). Round 1 hardened the warning
primitive; round 2 showed the hardening had opened two new holes of its own —
the standing lesson that a concurrency fix needs its own adversarial pass.

| # | Defect | Severity | Fix |
|---|---|---|---|
| 1 | **Round 1's zombie eviction evicted LIVE routers.** `_scope` published `showwarning = _route` before `_depth += 1`, so a frameless warning on any other thread reached `_route`, read depth 0 as "unowned", and un-installed the router the installing thread was still setting up — that whole nest then captured **nothing**. 0.4–0.5 % of scope entries under a hammer; 217/30 000 through the real production call sites; **0 on the pre-round-1 module**, so it was a regression introduced by the fix. | silent loss | claim the nest (`_depth += 1`) BEFORE the router is visible, roll it back on failure, and make the lock-free evictor take `_install_lock` **non-blockingly** and skip when someone holds it (`_install` uses the `_locked` variant it already owns). |
| 2 | **Every scope entry rewrote the process-global filter list.** `filterwarnings` is a remove-then-insert; a peer's warning landing in that gap is matched by the DEFAULT rules and lost from a live capture. 223–1 074 losses per 200 000 with peers merely entering scopes; 21 610–27 420 with peers calling `filterwarnings` directly. Falsified the module's own "no per-call global writes". | silent loss | install "always" ONCE per nest per category (`_filtered`, guarded by `_install_lock`, cleared when the nest's guard restores the list). |
| 3 | **The nest guard resurrected a dead recorder.** `catch_warnings` also snapshots the private `_showwarnmsg_impl` — a hook this module never writes — so a foreign `catch_warnings(record=True)` that opened before our nest and died inside it was reinstalled by our exit: the dead-recorder leak, one hook below the one round 1 fixed. | silent loss | keep the LIVE impl across the guard's exit instead of the snapshot. |
| 4 | **Cancellation was only half of what the old lock did.** It restored the CPU/latency half but not the bound on simultaneous work (5 concurrent resample blocks vs main's 1, 3.2× peak RSS), and it is a **no-op on a one-cutoff series** — a young experiment or a weekly cadence — where six knob turns still cost six full computes (2.0 s vs main's 0.48 s). | perf/resource | a `BoundedSemaphore(RECOMPUTE_SLOTS=2)` admission door with the staleness re-checked on acquire: a request superseded while waiting computes nothing at all, and simultaneous blocks are bounded — without ever waiting on `/reload`/`/validate`. |
| 5 | Two round-1 fixes had no test that could fail, and the AST gate missed seven working spellings of what it bans (`import warnings as w`, `from warnings import catch_warnings as cw`, `filters[:] = …`, `filters.insert`, `filters += …`, a rebound member, an annotated assignment). | high (gates that cannot fail) | the gate now resolves import aliases and flags bare references, `filters` mutations and `_showwarnmsg_impl`/`_filters_mutated`; its evasion suite grew from 6 to 14 shapes. Each round-2 fix has a mutation-checked test (nest-claim, non-blocking evictor, one-filter-per-nest, live-impl, the admission door). |
| 6 | `cached_cutoffs`'s lock — the contract's fifth call site — was caught only 1–2 runs in 5 when removed. | medium (same) | a deterministic gate: the cache dict starts a writer at the first key, so an unlocked accessor raises "dictionary changed size during iteration" **5/5**. |
| 7 | `RecomputeSuperseded` was unexported, carried the metric name as its whole message, and nothing forced the next `should_stop` caller to catch it — `/reload`'s call site would render it as "reload failed: arpu" in the client's status bar. | low | exported, self-describing message, and an AST gate asserting every call passing `should_stop=` is inside a `try` that names the exception. |

**Verified clean in round 2, with the evidence:** the scripted-session dump is
byte-identical across `main`, `708b8a5` and `b6da191` (22 603 bytes, one
sha256); the only two `.recompute(` call sites in `abkit/` are the handler and
`_run_reload`, and no id-less request can ever be cancelled (`check_stale`/
`is_stale` short-circuit on `None`); cancelling mid-compute strands nothing —
`series.rows`, the cached cutoffs/entries, `aa_rows`, the value count and the
next full reply are all identical to the pre-cancellation run; a 25 s
randomized storm (8 client threads × 4 metrics × 6 methods, periodic `/reload`,
one Auto `/validate`) produced 0 5xx, 0 hung threads and a post-storm reply
differing only in the `calibration` block the Auto run populated; the poll
costs 0.744 µs per call (+32 µs on an 18-row recompute); superseded requests
compute exactly one point (`1,1,1,1,1` against a winner's 8).

### WP5 round 1 (`35323f4`…`dc0ba98`) — 6 lenses, 19 findings, 8 survived a skeptic

Six lenses (numbers-moved, concurrency, key completeness, warnings/side
effects/memory, can-the-new-gates-fail, contract & blast radius), each in its
own worktree and required to RUN its scenario, then one skeptic per finding
tasked with refuting it (default REFUTED; "does it also happen on `main`?" as an
explicit question). 19 raised, 8 confirmed. **No finding touched a number** —
the numbers lens independently re-derived the milestone's headline claim (4 131
main-vs-WP5 results across all 6 classes and 8 data shapes, byte-identical at
`float.hex()`, including warnings, diagnostics and every refusal's exception
text), and the key lens failed to construct a single collision.

| # | Defect | Severity | Fix |
|---|---|---|---|
| 1 | **The counter-exactness gate could not fail.** With `boot_memo_lock` stripped from `memoize_resample` the test passed **0 of 60** runs: its threads used a key each, so `previous` was always `None` and the whole update was `+= entry.values` — bytecode with no CALL and no backward jump, which CPython never preempts, at any switch interval. The WP4 original it was copied from shares 4 keys, which is exactly why its window contains a call and it catches its mutation 20/20. | high (a gate that cannot fail) | 16 SHARED keys, so the `pop(...)` call sits inside the window: the mutation is now caught **6/6** (the skeptic's independent variant: 12/12). |
| 2 | **The purge hammer could not fail either** (3/20): its readers recycled ONE key, so the memo never held more than one entry and an unlocked `drop_memoized_cutoff` never scanned a mutating dict. | high (same) | 4 000 pre-filled entries under an untouched cutoff (so every purge walks a long dict), fresh generations per insert, and a 1 µs switch interval: **6/6**, raising the real `RuntimeError: OrderedDict mutated during iteration` that would be a 500 on `/reload`. |
| 3 | **The resample's own result warnings were pinned by nothing.** `BootMemoEntry.caught` (the `AbkitStatsWarning` channel) was pinned; `ResampleOutcome.warnings` → `_finalize`'s list → `TestResult.warnings` was not — the test compared three memoized answers only to each other, so dropping the whole channel stayed green across all 657 tuning+stats tests. | silent-loss (gate) | the fixture now checks against an INDEPENDENT `compare_pair` over the same containers and the same derived seed. Mutation (`list(outcome.warnings)` → `[]`): red. |
| 4 | **The headline parity gate compared the memo path against itself.** `boot_memo_budget = 0` disables the CACHE, not the code path — both sides still ran `_memoized_compare` → `_resample_captured`/`_finalize_captured`. | contract (gate) | the oracle now clears `supports_resample_memo`, which routes the baseline down the verbatim `_compare` → `compare_pair` → `from_samples` path the pipeline itself uses. Both parity gates (5-alpha and the knob matrix) use it; the budget-0 refusal path keeps its own test. |
| 5 | **A value-only budget bounds the payload, not the memory.** Each slot costs ~773 B beyond its replicates (the key's canonical params JSON dominates), so a client sweeping distinct `n_samples` values could hold millions of 1-replicate entries "inside" a 16 MB budget and retain more than a gigabyte. | perf/resource | every add and subtract goes through one `memo_slot_charge()` = replicates + `BOOT_MEMO_ENTRY_OVERHEAD` (128 values ≈ 1 KiB). Writing it in two places first made the counter drift — the session's own concurrency gates caught that within the minute. |
| 6 | **A lying capability flag died with a bare `AttributeError`** deep inside the engine. `supports_resample_memo` is advertised on `BaseMethod`, but the protocol it promises only existed on the bootstrap base — a downstream plugin had no signature to implement and no message telling it what was missing (the M7 lying-`supports_vectorized` lesson, unlearned). | contract | `BaseMethod._resample` now mirrors `from_suffstats_array`: a documented default raising `NotImplementedError` that names the flag; `BaseBootstrapMethod`'s abstract override delegates to it, so a runtime-patched-away implementation fails the same way. Pinned through both the stats core and the engine. |
| 7 | **The as-built justified keying on `max_block_bytes` with a false claim** ("the block size is free to change the draw"). It is block-invariant by the engine's contract — measured byte-identical across five block sizes on both engines. | contract (docs) | the bullet now separates `seed` (genuinely the draw) from the belt-and-braces params, and records the `ParamSpec`-flag narrowing as a named follow-up. |
| 8 | Three test files were not black-clean, so `pre-commit run --all-files` rewrote them — the "black churn in `tests/`" pain the project has already recorded twice. | style | formatted with the pinned black. |

**Fixed beyond the confirmed set** (both filed as nits by their skeptics, both
cheap and both matching an established precedent the project has already paid
for twice):

- `BootMemoKey` is composed ONLY through `ExploreSession.boot_memo_key()`, with
  an AST gate — the m9 `state_series_key()` and m10 WP1 grid-factory discipline
  (a composition copied to a second call site is one that will be copied with a
  field dropped, and every dropped field here is a wrong number).
- The "the two locks are never nested" claim is now a **test**: an AST walk over
  `session.py` refuses any `boot_memo*`/`drop_memoized*` reference inside a
  `with self.cache_lock:` body. Both gates go red on their mutation.
- The FIFO budget's silent cliff (once the working set exceeds the budget, an
  oldest-first policy yields NO reuse at all) is now **disclosed**: `recompute()`
  compares the session's eviction counter around the pass and appends a warning
  naming the budget — the same honesty the Tier-S cache owes when it degrades.
  (The skeptic downgraded the CPU half to PRE_EXISTING: thrashing costs what
  `main` already cost, never more.)

**Verified clean, with the evidence:**

- **No number moves, established independently of the repo's own tests**: 4 131
  main-vs-WP5 comparisons across the 6 classes × 8 data shapes byte-identical;
  my own 64-cell knob-matrix fuzz (3 families × stat × pvalue_kind × test_type ×
  4 alphas, 256 points) memo-on vs memo-off identical; `git diff main..HEAD --
  tests/golden` empty; zero `ALGORITHM_VERSION` changes.
- **No collision exists in the shipped key.** Three independent search
  strategies failed to construct one; the data axis is closed by construction
  (one writer, `cached_entry()` returns the entry and its generation in one
  critical section, and `MetricLoadResult` is never mutated after installation).
- **The generation scheme is sound under concurrency**: no interleaving serves a
  stale resample — the counter is written by one function, never reset (a
  `disable_cache` deliberately leaves it monotone), and a production-shaped
  hammer (4 recompute threads + 400 installs) shows 0 exceptions and 0 drift on
  the shipped code against 30 `RuntimeError`s + 258 drift with the lock removed.
- **Warnings cannot cross-attribute**: `warn_scope` frames are per-thread and a
  replayed warning always belongs to the same logical comparison the key names
  (8-thread × 2-metric tagged run).
- Deliberately NOT changed: the memo key still carries `pvalue_kind` and
  `max_block_bytes` (correct, merely wider than necessary — the narrowing is a
  named follow-up, and a missed hit costs a resample while a wrong hit would
  cost a wrong number); `contributing.md` step 4b keeps its general wording,
  scoped by the sentence naming the bootstrap family as today's only adopter.

### WP5 round 2 — the round-1 fixes attacked in turn

Three lenses over the round-1 fix delta (`dc0ba98..37872fd`) plus two
independent re-establishments of the whole WP, each in its own worktree, each
required to run what it claims. 9 findings, 5 reproduced. The standing lesson
held again: **round 1's own fix opened the round's worst hole.**

| # | Defect | Severity | Fix |
|---|---|---|---|
| 1 | **Round 1's eviction warning fired when the memo was working perfectly, and blamed this reply for another request's evictions.** It compared a SESSION-WIDE monotone counter around the pass, so ordinary turnover between two knob states ("make room for the new state") reported a degradation the very next request contradicted — measured: the warning fires, then the same knobs at a new alpha reuse everything and resample **0** times. And since m10 WP4 made `/recompute` concurrent over one session, a bootstrap handler's evictions surfaced in an unrelated reply, quoting that reply's own roomy budget. | contract (a warning users learn to ignore) | measure the PASS: `_memoized_compare` records the keys this pass stored, and `recompute()` asks `session.memoized_all(...)` — did anything I just stored already go? Healthy turnover and a noisy neighbour are both silent; real thrash still speaks. |
| 2 | **The loudest case was the silent one.** An entry bigger than the WHOLE budget is refused, evicting nothing — so the eviction-keyed warning never fired for the one case where reuse is zero *forever*. Reachable at the shipped default: `n_samples` has no maximum, so anything above ~2 M replicates refuses every entry. | perf/resource | the refusal is recorded as a sentinel in the same per-pass list, so it warns through the same branch. |
| 3 | **The capability refusal covered only half the contract.** Round 1 gave `BaseMethod._resample` a named `NotImplementedError`; a method with `_resample` but no `_finalize` still died with a bare `AttributeError` inside the engine. | contract | `_memoized_compare` checks both halves up front and names the missing one. Both shapes pinned. |
| 4 | **The WP's central atomicity claim was pinned by nothing.** Nothing failed when the memo key's `generation` stopped coming from the same locked read as the entry — the torn read that keys THIS render's replicates to the NEXT render's generation, i.e. a stale hit that survives every purge. | contract (a gate that cannot fail) | the WP4 hooked-lock instrument, extended: a complete `/reload` lands at the reader's first release, and the FOLLOWING request must answer off the installed render. A two-read implementation serves the pre-reload numbers and the test goes red. |
| 5 | **The round-1 docs correction reached two of three bodies.** `.claude/rules/architecture.md` still asserted the `max_block_bytes` collision the fix had retracted — and it is the body a future contributor reads before picking up the named narrowing follow-up. | contract (three-way sync) | corrected; the rules bullet now separates the collision-critical fields from the belt-and-braces ones. |
| 6 | The named refusal claimed the method "declares `supports_resample_memo`" even for a method that never declared it. | style | the message branches on the flag. |
| 7 | The two new AST gates matched only the direct spelling (`BootMemoKey(...)`), missing the alias, module-attribute, rebinding and `_make` forms — the exact evasion class WP4's round 2 had to fix in its own gate. | style (gate coverage) | the key gate resolves import aliases and rebindings and covers `_make`; its evasion suite has 5 shapes. |

**Recorded, deliberately NOT fixed** (unreachable in-tree, and the guard would
cost more than it buys): a plugin that declares `supports_resample_memo`, keeps
a working `_resample`/`_finalize` pair AND overrides `from_samples` would have
explore skip the override while the pipeline still runs it. In-repo the roster
gate refuses exactly that shape (`from_samples is BaseBootstrapMethod.from_samples`);
downstream it is out of contract, and detecting it generically means marking the
template method itself. Named, not built.

**Verified clean in round 2, with the evidence** (independent of the repo's own
tests — the numbers lens diffed against the `main` tree, which has no memo code
at all):

- **16 128 stats-layer cases** (6 families × 8 data flavours incl. all-zero
  control, constant, 1e-9, 1e12, Pareto; stratified and not; every stat ×
  pvalue_kind × test_type × alpha × max_block_bytes) byte-identical to `main` on
  every `TestResult` field, diagnostic and warning — the split moves no number.
- **21 328 engine-layer points** across two seed sets, **14 503 of them served by
  a memo HIT**, byte-identical to `main` across all 18 `ExplorePoint` fields, the
  raw `TestResult`, the chips and the engine warnings — with 3-arm pairs, two
  sample metrics, degenerate H5 arms (350+ undefined-effect and 800+ non-finite
  warnings actually exercised), 298 interleaved `/reload`s (scale, zero, 1e18,
  NaN, lookback change) and stratified entries in the space.
- **46 696 points across two HTTP storms** checked against per-generation
  memo-free oracles: **zero stale hits and zero mixtures** (the signature a stale
  hit would leave, since `_finalize` takes `value_i` from the memoized outcome
  but `std`/`size` from the live containers).
- `boot_memo_values == sum(memo_slot_charge(...))` held exactly after every
  scenario including a mid-storm `disable_cache` and an `n_samples` sweep with
  472 evictions; no lock cycle exists; `boot_data` is a fresh allocation in both
  engines (never a view into a block buffer), so the budget's `values` is an
  honest memory measure.

**A CI-only failure, and what it was allowed to say.** The first run of the
round-2 tree lost exactly one reply out of twenty in WP4's
`test_lock_free_recomputes_keep_the_cache_consistent_under_a_reload`
(`assert 19 == 20`). The count was all the test could say: its `http()` helper
converted HTTP errors into `(status, body)` but let a TRANSPORT error escape,
so inside a thread it vanished into a stack trace and the reply simply never
arrived. The helper now returns `(0, "transport: …")` and the existing
per-reply assertion names the cause the next time. Sized with it: the server's
accept queue (socketserver's default backlog of 5 — right while one lock
serialized every POST, tight now that a knob drag arrives as a burst of
concurrent connections) is now 64. Disclosed as a hardening, not a proven fix:
a local probe could not reproduce a drop at either backlog size, the failure
has not recurred, and the comment in `server.py` says exactly that.

### Exit gate round 1 — 6 lenses in isolated worktrees, a skeptic per finding

Every lens ran in its own git worktree with a mandate to break things; every
finding it raised went to a skeptic whose default was REFUTED and who had to
**reproduce** the defect by running something. 29 raised, **12 survived**, and
the split is the useful part: 8 were refuted outright, 7 were real but
immaterial or pre-existing to this PR, and of the 12 confirmed, 5 were defects
in the gate's own claims rather than in the code it gates.

**The blocker.** `SELECT … FROM _ab_experiments FINAL` in the new ClickHouse
leg. The catalog is an Ordinary `MergeTree` (no version column), so FINAL is
illegal on it — the Docker-gated CI job would have **errored**, not failed, and
the leg would have certified nothing. The skeptic reproduced it against an
embedded ClickHouse on three versions bracketing the CI image. abkit's own
reader does not use FINAL either: `upsert_experiment` replaces the row with a
synchronous delete + insert, so exactly one row is the honest assertion.

**Three code defects, each reproduced:**

| # | Defect | Fix |
|---|---|---|
| 1 | **The `+1 day` horizon instruction was unreachable.** The rename guard raised on the FIRST stale key and `start_date` sorts first — whose note says the value carries over unchanged. Every real 0.4.0 config carries both keys, so the operator never saw the `end_date` → `horizon_ts` instruction, renamed both mechanically, and got a window ONE DAY SHORT that validates in silence. | report every renamed key present in one error (`test_a_real_0_4_0_config_is_told_about_BOTH_renames_at_once`) |
| 2 | **Copy mode dropped the whole opening day.** The incremental cohort copy anchored its first scan bucket at `grid.start_ts`, which until m10 was always local midnight. Measured on the scaffold: a 09:00 start persists **0 of 600** units (they were exposed at 08:00) while the SRM line still reads 600 off the LIVE source — so a real warehouse's metric join returns nothing and every look degrades to "insufficient", unwarned. | anchor the origin at the opening LOCAL DAY's midnight — byte-identical for every midnight start, and closer to direct mode, which applies no lower bound at all |
| 3 | **`--workers N>1` buried the upgrade remedy.** `run_experiments` has a SECOND `ensure_tables()` (the pool-bootstrap DDL serializer) that the gate's own driver fix did not cover, so with 2+ experiments the breaking-release message went back to being a traceback. Pre-existing at `main` (both sites were unguarded there) — what this PR newly introduced was the *claim* that both paths were fixed. | guard it too, and pin it (`test_the_worker_pool_path_names_the_remedy_too`) |

**Five defects in the gate's own claims — the recurring lesson.** Every one was
a sentence this PR added, and every one was checked by running something:

1. **"±1h across DST" was wrong in both halves.** Measured against pre-m10 over
   8 added shapes: −30 min (Australia/Lord_Howe), −2h (Antarctica/Troll), −24h
   (Pacific/Apia's 2011 line jump), and +1h with `dst() == 0` on both sides
   (Moscow's 2014 permanent +4→+3 shift). The allowlist test that was supposed
   to stop the waiver growing **checked no DST property at all and touched no
   production code**. Both are gone: the gate now asserts the LAW — the delta
   equals the UTC-offset change between the window's LOCAL edges, for every
   case, 9 of 22 exercising it — which needs no waiver list and cannot be
   satisfied by test data alone.
2. **A second divergence was undisclosed.** For a `start_ts` on a local calendar
   day that never existed, pre-m10 opened the series with a ZERO-LENGTH look and
   m10 drops it. WP1's own round had ordered this disclosed and it had reached
   neither the CHANGELOG nor the rules. Now pinned exactly and scoped in both.
   (Enumerated rather than taken on trust: tzdata puts a skipped local day on
   exactly 3 dates between 1970 and 2036 — 1993-08-21 Kwajalein, 1994-12-31
   Enderbury/Kanton/Kiritimati, 2011-12-30 Apia/Fakaofo — i.e. 7 zone entries
   counting aliases, every one historical.)
3. **`type(x) is date` appears nowhere in `abkit/`.** The rules described a
   construct from the design document, not the code (which tests
   `isinstance(value, datetime)` FIRST, because `datetime` subclasses `date`).
4. **"the recipe is test-pinned per dialect" was false.** The skeptic kept the
   whole suite green with the PostgreSQL `AT TIME ZONE` operands
   swapped, its sign flipped, and the MySQL `CONVERT_TZ` arguments reversed:
   `TestTimezoneDates` pins the recipe transcribed to Python, and no test
   executes or even greps the three dialect expressions.
5. **"both are AST-gated" was false** — only M10's planner contract has a gate;
   the M8 cohort-factory contract is honor-system prose, which is precisely the
   shape that let a decorative knob reach none of eight call sites.

**Four gates that passed for the wrong reason:**

- leg 2's flag-parity was **self-parity**: nothing proved the first run took the
  additive path, so a driver ignoring `compute.incremental_reads` would have
  compared recompute with recompute. It now counts
  `IncrementalBackend.load_cutoff` (11 reads, zero fallbacks).
- `abk validate` was green-lit by `exit_code == 0` while HALF its matrix errored
  — the runner catches per-cell failures and still exits 0. Per-cell statuses
  are now pinned, including the fraction cell that FAILS, which the skeptic
  proved pre-existing (identical at `f85371d` with a midnight start and the same
  6h cadence): a sub-day-cadence defect in the A/A panel, disclosed rather than
  adopted.
- `abk plan` ran only its SKIPPED-no-baseline branch (the sizing math the gate
  names never executed).
- the memo parity oracle had no non-vacuity floor: `0 == 0 * 5` satisfied it.

**A coverage hole proven, then closed.** A `raise` probe inside the day-lattice's
day-space `until` comparison — a branch that exists *solely* for the pre-m10
"whole-day `until` across a DST fall-back" shape — left all 23 leg-1 tests
green. Three shapes were added (a whole-day `until` across a fall-back, a
three-segment schedule, sub-day segments across a transition), the golden was
re-captured at `f85371d`, and the same probe now fails 3 of the 4 golden tests.
11 → 22 cases.

**Recorded, not fixed (all pre-existing to this PR, all in `main`):** the
`/recompute` reply that can mix two warehouse renders of one series with every
point labelled `exact` — reproduced 3/3, and the reason D5's "never a wrong
number" is now amended above; the memo's per-pass shortfall warning firing on a
concurrent `/reload`'s housekeeping purge (a false thrash diagnosis with false
advice); the WP5 AST gate scoping `GUARDED` to two of the three `boot_memo*`
fields; `ensure_columns` not reporting live columns the model no longer declares
(the ClickHouse `1970-01-01` hazard the CHANGELOG spells out); and the
`interval_anchor` parse error discarding the inner diagnosis. Each belongs next
to the hardening backlog's existing per-series Tier-S consistency item.

**Two things the lenses cleared, worth not re-checking:** a lens independently
re-captured the golden from a `git worktree` at `f85371d` and deep-compared it
to the committed fixture (identical, every case, every field) — the gate really
does compare against a different code path; and the D6 port is the correct
translation of "the same window", proven from the pre-m10 planner's own
`horizon_ts = tz_midnight_utc(end_date + 1d)` rather than from prose.

### Exit gate round 2 — the round-1 fixes attacked in turn

**Disclosure first: round 2's four-lens fleet died before returning anything.**
All four agents failed on the org's monthly spend limit (`agents_error: 4`,
zero results) after ~180 tool calls of work. An empty workflow result is not a
clean review — this project has recorded that lesson once already — so round 2
was executed directly instead, against the same four attack lists the lenses
carried. That makes it a self-review rather than an independent one, which is
weaker on exactly the axis rounds 1 and 2 exist to cover, and is recorded here
as a deviation rather than papered over. Every claim below is a command that ran.

**The new LAW survived its sharpest attack.** Round 1 replaced the "±1h across
DST" waiver list with "the delta equals the UTC-offset change between the
window's LOCAL edges". The obvious way to break that is PEP 495: an edge landing
in a local hour that is AMBIGUOUS (a fall-back repeat) or in a GAP (a
spring-forward skip), where "the offset at that local time" is not a function.
Four such windows were measured (start in the gap, horizon in the gap, start in
the ambiguous hour, horizon in it) and the law held for all four — because it
reads the offset through the same `replace(tzinfo=zone)` mechanism
`resolve_instant()` uses, so the two cannot disagree by construction. A wrong
story about DST was replaced by one that is true for the same reason the code is.

**Six gates re-verified by mutation or probe**, each a claim round 1 made:

| Probe | Result |
|---|---|
| `raise` in `abk plan`'s post-baseline sizing path | `test_plan_accepts_a_timestamped_start` FAILS — the sizing math really executes now (before round 1 it only ran the SKIPPED-no-baseline branch) |
| `raise` in the worker-pool bootstrap branch | `test_the_worker_pool_path_names_the_remedy_too` FAILS — the test really takes the pool path, not the serial one |
| drop a SECOND look from the Apia case | `test_the_only_grid_that_moved_is_a_start_on_a_skipped_local_day` FAILS — the `continue` that excuses that case from the byte-identity loop hides nothing else about it |
| force a PARTIAL fallback (delete one state day, re-plan) | the driver prints `incremental read fell back to full recompute — state through 2024-07-02, cutoffs need closed days through 2024-07-03`, so leg 2's output grep is a real guard. This mattered: `_fallback` is INTERNAL to `load_cutoff`, so the call counter alone would NOT have caught a partial fallback |
| rename guard against 5 payload shapes | both-old-keys, old+new mixed, old keys set to `None`, a valid config and a non-dict payload all behave correctly; every stale key present is named |
| copy origin across 6 knob combinations | `batch_interval: 1d` and `7d`, `maturity_delay: 2h`, `Europe/Moscow` (day floor 14h before the start), a 23:30 start, and a re-run: 600 units every time, and the re-run stays 600 — append-only, no duplicates |

**Two gaps found and closed in round 2 itself:**

1. `test_validate_accepts_a_timestamped_start` pinned the fraction cell's
   *status* but not its *reason*, so a different failure with the same status
   would have let the test pass while its own docstring lied about why. The
   reason string is now asserted.
2. `test_copy_mode_still_copies_the_whole_opening_day` asserted the row count
   without proving the copy ENGINE produced it. It now asserts the round-trip log
   line (which names the origin the fix moved) and that the 600 units are
   distinct — a duplicated copy would otherwise have satisfied the count.

**Cleared, with the check that cleared it:** the `http()` helper's transport
sentinel cannot mask a failure (all 8 call sites in `test_explore_session.py`
assert `status == 200` or `== 409`, never a negation); the three window shapes
round 1 added are non-degenerate and land where intended (39/13/8 looks, and
`whole_day_until_across_dst` visibly holds local 00:00 across the fall-back:
`…04:00` before, `…05:00` UTC after); the `_ab_experiments` catalog really is an
Ordinary `MergeTree` with no version column and abkit's own reader uses no FINAL;
and the tzdata claim in the round-1 record was re-derived by enumerating every
zone over 1970–2036 rather than trusted (3 dates, 7 zone entries with aliases).

**Round 3 was CI.** The first push failed `Test (Python 3.10)` in 48 s while
every other job — including the Docker-gated ClickHouse leg, the one thing no
local run could check — passed. The cause was `from datetime import UTC` in the
new e2e module: `datetime.UTC` landed in **3.11**, and the project supports 3.10
(`requires-python`). A local interpreter at 3.12 cannot catch that class of
defect at all, and neither round of review did: both read the file on a 3.12
machine. Replaced with `timezone.utc`, and the new files were swept for the rest
of the 3.11+ surface (`ExceptionGroup`, `tomllib`, `itertools.batched`,
`typing.Self`, `StrEnum`) — none present. The general lesson for a repo whose
floor is 3.10: a new import of a stdlib name is a version claim, and only the
matrix can check it.

**Final state:** 2 369 passed, 6 skipped; mypy 111 = `main`'s baseline;
`ALGORITHM_VERSION` grep over the diff empty; `tests/golden` untouched;
`__version__ = 0.5.0` matching the CHANGELOG heading, with `0.4.0` the latest on
PyPI and `v0.5.0` deliberately untagged (the maintainer's step).
