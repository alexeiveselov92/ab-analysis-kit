# M12 Implementation Plan — notifications

> **IMPLEMENTATION RECORD for M12** (polish track, approved 2026-07-18), in the
> shape of [m4-implementation-plan.md](m4-implementation-plan.md) /
> [m6-implementation-plan.md](m6-implementation-plan.md). **Released as
> `0.7.0`.** NTF-1..NTF-6 are all merged (PRs #89–#94); the as-designed contract
> below is kept verbatim, with an **"As built"** block under each work package
> recording where the build diverged from it, the §4 exit-gate result, and the
> §7 record (signal table, state-row semantics, both review rounds). Where a
> step and its "As built" block disagree, the block is what shipped.
>
> Governing specs: [cli-and-dx.md](cli-and-dx.md) (the `abk test-report` /
> `abkit/notify/` layer as shipped in M6),
> [data-contract-and-reporting.md](data-contract-and-reporting.md) (the readout
> contract §5.3 that `ReadoutData` mirrors), [ROADMAP.md](../../ROADMAP.md) M12.
> Sibling milestone docs: [m11-implementation-plan.md](m11-implementation-plan.md)
> (the `abk dashboard` track — DASH-1..DASH-7 — shares the milestone's source
> design/verify pass but ships one release earlier, `0.6.0`; **not restated
> here**), [m9-implementation-plan.md](m9-implementation-plan.md) (the additive
> compute engine — the one schema-touch collision with this milestone, see §6).
> Donor: `/home/aleksei/wsl_analytics/detektkit` (import pkg `detectkit`,
> `detectkit/alerting/`) — reused for wire-format/platform facts only, never for
> alerting semantics (§0.4).

## 0. Scope, posture & decisions

**Goal (NTF track only):** wire the already-branded, already-shipped
`abkit/notify/` channel layer (5 channels, M6) to the six real pipeline
signals it currently never sees — readout-ready, verdict-change (with
dedup/cooldown), SRM-breach, calibration-red, stale/backlog, pipeline-error —
behind an opt-in `--notify` flag; add the four channels detectkit has and
abkit doesn't (discord/teams/googlechat/ntfy); and add per-experiment `notify:`
routing (channels/mentions/on-filters). Everything is **fail-soft** (a
notification can never fail a run) and **zero statistics changes**. This
milestone covers **only the NTF-1..NTF-6 work packages** from the shared
design pass; the DASH-1..DASH-7 dashboard work packages from the same design
pass belong to [m11-implementation-plan.md](m11-implementation-plan.md)
(`0.6.0`) and are cross-referenced, not restated, here.

### 0.1 What already exists (do not rebuild)

- **`abkit/notify/` ships 5 channels** via `ChannelFactory.CHANNEL_TYPES`
  (`abkit/notify/factory.py:27-33`): `webhook`, `mattermost`, `slack`,
  `telegram`, `email`. `BaseChannel` (`abkit/notify/base.py`) is an ABC whose
  only abstract method is `send(readout, template=None) -> bool`;
  `build_context` is the single source of every display string; the five
  brand verdict tokens (`_VERDICT_COLORS`/`_VERDICT_WORDS`/`_VERDICT_EMOJI`,
  `base.py:85-107`) already carry WIN/LOSE/FLAT/INCONCLUSIVE/**SRM** (SRM
  overrides any verdict when `readout.srm_flag` is set — `verdict_kind`,
  `base.py:118-124`).
- **`ReadoutData`** (`abkit/notify/base.py`) is the flat, display-oriented
  payload every channel sends — it already mirrors the readout contract
  (`data-contract-and-reporting.md §5.3`): verdict, effect, CI, p-value, the
  **effective post-correction per-comparison alpha**, `srm_flag`,
  `weekly_cycle_pct`, plus display fields (timezone, project name, mentions,
  links). NTF-1 populates it from real rows; it adds no new fields.
- **The only caller today is `abk test-report`** (`abkit/cli/commands/test_report.py:51-93`
  — `ChannelFactory.create_from_config` + `create_mock_readout`): a
  connectivity/formatting smoke test over a **synthetic** mock readout. It
  reads nothing from the warehouse, takes no lock, runs no statistics — and
  **stays exactly as-is** through this whole milestone (the NTF track adds new
  callers; it never touches this one).
- **The config surface today is profile-level only.**
  `NotificationChannelConfig` (`abkit/config/profile.py:203-218`, `extra="allow"`,
  discriminated by `type`) lives under `ProfilesConfig.notification_channels:
  dict[str, NotificationChannelConfig]` (`profile.py:235`). `ExperimentConfig`
  (`abkit/config/experiment_config.py:224+`) has **no** `notify`/`mentions`/`on`
  field anywhere — every experiment currently shares the same profile-wide
  channel roster with no per-experiment routing.
- **The three real hook points already exist, unwired.** `run_run()`'s
  `for outcome in outcomes:` loop (`abkit/cli/commands/run.py:223-271`) is a
  single seam per experiment: the `completed` branch already splits
  `srm_warnings = [w for w in outcome.warnings if "SRM" in w]` from
  `other_warnings` (`run.py:233-234`, feeding `echo_srm`/`echo_tree`); the
  `else` branch already calls `echo_error(outcome.experiment, outcome.error or
  "failed")` (`run.py:240-241`). Both sit right beside the existing
  `--report` bake, itself already wrapped in a `try/except Exception` that
  echoes `"Report skipped: {report_error}"` and **never fails the run**
  (`run.py:270-271`) — the exact "never fail on a side channel" precedent NTF-1
  reuses verbatim for `--notify`. `abkit/validate/runner.py:_verdict`
  (`runner.py:141-152`) already renders the "FPR inflated … do not use"
  plain-language string for an over-budget cell — the calibration-red
  condition (`score.fpr > budget`) it encodes is the **existing** detection
  NTF-5 reuses, never re-derives. `abkit/pipeline/driver.py:342-347` already
  emits a `"… backlog"`-suffixed warning string when `backlog_seconds(...)`
  exceeds three cadence steps — the **existing** staleness signal NTF-5 filters
  on.
- **The generic upsert primitive NTF-3 needs already exists.**
  `BaseDatabaseManager.upsert_record(table_name, key_columns, data, sync=False)`
  (`abkit/database/manager.py:251-276`) is the database-agnostic
  delete-then-insert pattern (CH `ALTER … DELETE` + insert; PG/MySQL
  transactional delete+insert) already used by the `_ab_tasks` lock row and
  reusable verbatim for `_ab_notify_states` — no new manager method needed.
  `_AaRunsMixin` (`abkit/database/internal_tables/_aa_runs.py`) is the direct
  shape precedent for the new `_NotifyStatesMixin` (a `TABLE_*` constant in
  `abkit/database/tables.py`, an entry in `INTERNAL_TABLES`
  (`tables.py:319-325`), a mixin composed into `InternalTablesManager`'s MRO,
  `manager.py:14-23`).

### 0.2 What does not exist yet (the M12 build)

`abkit/notify/dispatch.py`, `abkit/notify/cooldown.py`, the `ExperimentConfig.notify`
block (`NotifyConfig`), `NotificationChannelConfig.on`, `_ab_notify_states` +
its mixin, `abkit/notify/{discord,teams,googlechat,ntfy}.py`, any `--notify`
CLI flag (on `run`/`validate`/possibly `explore`), `BaseChannel.send_notice`,
and the calibration-red/stale wiring into `validate.py`/`run.py`/(optionally)
`tuning/server.py`. None of this exists in the shipped 0.1.x/0.6.0 tree; every
WP below is additive.

### 0.3 The no-numbers-move posture (M7–M12 hard rule)

Notifications read **already-persisted** rows (`_ab_results`, `_ab_aa_runs`,
the pipeline `outcome` object) — nothing in this milestone touches
`abkit/stats/`, changes a persisted numeric field, or re-derives a number
`evaluate()`/`readout.py`/`runner.py` already computed. The exit gate greps for
an `ALGORITHM_VERSION` bump and expects **zero** hits; the parity/golden
suites are untouched by construction (no method-math file is in any WP's file
list below).

### 0.4 Milestone-specific corrections (plan-review record)

The shared pre-implementation design pass (the NTF-\* work-package breakdown)
and its code-verification pass (file:line facts against this repo and the
donor) left five points that this contract resolves explicitly before implementation
starts (mirrors m6 §0.5's pre-implementation review record):

1. **Fail-soft is a hard invariant, not a per-WP nicety.** A notification
   failure must **never** fail a run — the CLI exit code is unaffected whether
   zero, one, or every configured channel raises. Every WP that adds a send
   call wraps it in its own `try/except` **in addition to** `dispatch.py`'s
   internal per-channel catch (deliberate defense-in-depth, NTF-1) — a later
   "simplify" pass must not collapse the two. This is pinned by the exit-gate
   e2e (NTF-6): a channel that always raises, injected into the fixture
   project, must not change the run's exit code.
2. **The dedup state key is the FULL comparison identity** — `(experiment,
   metric, name_1, name_2, method_config_id)`, not a display name — so a
   re-tuned comparison (a changed `method_config_id`) starts a fresh dedup
   track rather than silently inheriting stale state. **A verdict FLIP is
   ALWAYS sent even inside the cooldown window; an UNCHANGED verdict is NEVER
   re-sent.** `cooldown_seconds` is reserved for a future recurring signal
   kind (e.g. a repeating `stale`) that legitimately re-fires with the *same*
   value — it is never consulted for verdict-change dedup (NTF-3). The
   exit-gate e2e proves a repeat `abk run --notify` over unchanged data sends
   **zero** notifications.
3. **The 4 new channels are thin adapters in the abkit idiom, not a donor
   port.** `abkit/notify/slack.py` (49 lines, wrapping `WebhookChannel`) is
   the size/shape reference for `discord.py`/`teams.py`/`googlechat.py`/
   `ntfy.py` — each takes the donor's **wire format** (Discord embed shape,
   Teams Adaptive Card over the Power Automate Workflows webhook, Google Chat
   Cards v2, ntfy's JSON-publish endpoint) and **platform caps** (field-length
   limits, mention syntax, line-break handling) verbatim as platform facts,
   but never the donor's alerting semantics (severity/anomaly/recovery/no-data
   kinds abkit has no equivalent of). Content always comes from
   `BaseChannel.build_context()`, exactly like every existing abkit channel
   (NTF-4).
4. **Calibration-red and stale/backlog add zero new detection logic.**
   Calibration-red reuses the existing `score.fpr > budget` condition
   `_verdict` (`runner.py:143-147`) already renders as text; stale/backlog
   reuses the existing `"… backlog"` substring `driver.py:342-347` already
   emits into `outcome.warnings`. NTF-5 is a routing/formatting WP over
   signals that already exist, not a new detector (NTF-5).
5. **The explore-Apply notification hook is validate-only in this first cut.**
   Firing calibration-red from `abk validate`'s per-cell scan is in scope;
   firing it from explore's `confirm_uncalibrated` Apply path
   (`abkit/tuning/server.py:325-399`) is **deferred** — it is the single most
   invasive addition relative to its one-line origin in the design pass
   (it requires adding notify-config plumbing, including a new `--notify` flag
   on `abk explore`, to a server that today carries none of it). NTF-5 ships
   `dispatch_calibration_red` wired only from `abk validate`; the explore-Apply
   half is named explicitly in §5 (open questions) as a deferred follow-up,
   not silently dropped.

---

## 1. Work packages in strict dependency order

### NTF-1 — Send seam: opt-in "readout ready" dispatch wired into `abk run`

**Goal:** stage 1 of the rollout — a new `abkit/notify/dispatch.py` turns a
just-completed experiment's persisted rows into `ReadoutData` (via the
**already-shipped** `abkit.pipeline.readout.evaluate` — the same function
`build_report_payload` uses, never a re-derivation) and sends through every
configured channel, called from `run.py`'s existing per-outcome loop, gated
behind an opt-in flag so this WP changes no default-path behavior. Adds the
`ExperimentConfig.notify` block (`channels`/`mentions`/`on`) and
`NotificationChannelConfig.on` so the whole routing surface exists from the
start, even though only the `readout` kind fires yet.

**Files touched:**
- `abkit/notify/dispatch.py` (new)
- `abkit/config/experiment_config.py` (add `NotifyConfig` + `ExperimentConfig.notify`)
- `abkit/config/profile.py` (add `NotificationChannelConfig.on`)
- `abkit/cli/commands/run.py` (wire dispatch into the outcome loop; add
  `--notify`/`--no-notify`)
- `abkit/cli/main.py` (surface the opt-in flag on `abk run`)
- `tests/notify/test_dispatch.py` (new)
- `tests/cli/test_run_command.py` (extend)

**Steps:**
1. `experiment_config.py`: `class NotifyConfig(BaseModel): channels:
   list[str] = []; mentions: list[str] = []; on: list[Literal['readout',
   'verdict_change', 'srm', 'calibration_red', 'stale', 'error']] | None =
   None` (`None` = all signal kinds — a sane default-inclusive filter). Add
   `ExperimentConfig.notify: NotifyConfig | None = None`.
2. `profile.py`: `NotificationChannelConfig.on: list[str] | None = None`
   (default `None` = receives every kind) — the per-channel urgency filter
   NTF-2 needs; landing the field now makes NTF-2 a pure behavior change, not
   a schema change.
3. `dispatch.py`: `readout_data_from_verdict(experiment, verdict: PairVerdict,
   *, project_name, timezone, mentions, dashboard_url=None) -> ReadoutData` —
   maps `PairVerdict` fields (effect/pvalue/left_bound/right_bound/alpha/
   elapsed_days/weekly_cycle_pct) 1:1 onto `ReadoutData`; `srm_flag`/
   `srm_pvalue` come from the sibling `ExperimentReadout`, never re-derived —
   mirrors `create_mock_readout`'s field set with real numbers. **Where the
   dedup key's `method_config_id` comes from:** `PairVerdict`/`ReadoutData`
   carry **no** `method_config_id` field — dispatch looks it up from the
   config, `{c.metric: c.method.method_config_id for c in
   experiment.comparisons}[verdict.metric]`, mirroring `evaluate()`'s own
   internal pattern (`readout.py:469`); safe because `ExperimentConfig`
   validation already rejects duplicate metric references in `comparisons`.
4. `dispatch_experiment_signals(*, experiment, readout, channels_cfg, project_name,
   echo) -> None`: resolves the channel list as `experiment.notify.channels`
   if set, else **all configured channels** (see D1, §3, for the default
   resolution); builds one `ReadoutData` per `PairVerdict` whose kind
   (`'readout'`) passes both the channel's `on` filter and the experiment's
   `on` filter; calls `ChannelFactory.create_from_config(cfg.model_dump()).send(readout)`
   inside a per-channel `try/except` — a channel exception is caught, echoed
   as a yellow line, and never propagates (mirrors `run.py:270-271`'s "never
   fail the run on a report" precedent, sharing the same comment convention).
5. `run.py`: add `--notify/--no-notify` (default `False` — opt-in) threaded
   into `run_run()`; inside the existing `for outcome in outcomes:` loop's
   `completed` branch (`run.py:223-234`), after `echo_tree`/`echo_srm`, call
   `dispatch_experiment_signals(...)` wrapped in its **own** `try/except`
   (belt-and-suspenders on top of `dispatch`'s internal per-channel catch) so
   a notify failure can never fail the run.
6. Reuse (don't rebuild) `report_tables`/`report_manager`'s lazy construction
   already present at `run.py:249-256` for the notify path too when
   `--report` was not also passed — one `InternalTablesManager` per `run.py`
   invocation, not one per experiment.

**Tests:**
- `tests/notify/test_dispatch.py`: `readout_data_from_verdict` field-for-field
  mapping against a synthetic `PairVerdict`/`ExperimentReadout` fixture;
  `dispatch_experiment_signals` sends to every configured channel when
  `experiment.notify` is unset; narrows to `experiment.notify.channels` when
  set; a channel whose `.send()` raises is caught and the others still get
  called (fail-soft, one bad channel doesn't block the rest); `mentions` from
  `experiment.notify.mentions` land in `ReadoutData.mentions`.
- `tests/cli/test_run_command.py`: `abk run` **without** `--notify` sends
  nothing (a spy on `ChannelFactory.create_from_config` asserts zero calls) —
  proves the opt-in default is truly off; **with** `--notify` and a fake
  channel captured via monkeypatch, one send per completed experiment's main
  verdict.
- No `abkit.stats` touched; `ALGORITHM_VERSION` unchanged (reads persisted
  numbers only).

**Risks / hotspots:**
- The default-channel-selection-when-`experiment.notify`-is-unset choice (all
  configured channels vs none) is a real behavior decision with no
  single-line spec text pinning it down — resolved in §3 D1, flagged for
  maintainer confirmation before merging.
- The double `try/except` (dispatch's internal + `run.py`'s wrapper) is
  deliberately redundant defense-in-depth (§0.4 point 1), not dead code —
  removing either during a later simplify pass reintroduces the fail-soft
  risk this WP exists to close.

**Session estimate:** 1 session.

**As built (shipped 2026-08-02; the design's shape held, two additions):**

- **`ChannelFactory` had to learn that some keys are abkit's, not the
  channel's.** `notification_channels` is `extra="allow"`, so every sibling key
  reaches the constructor — and a *declared* field is worse than an undeclared
  one, because `model_dump()` emits `on: None` even for a config that never
  writes it. Landing step 2 alone would therefore have broken **every** channel
  in `abk test-report`, which never asked about routing (18 tests red under a
  mutation probe). `ROUTING_KEYS` lives beside the factory — the single funnel
  every caller goes through — and the test asserts the classification is
  exhaustive over the model's declared fields, not that the constant equals
  itself.
- **`SignalKind` lives in `abkit/config/signals.py`, a leaf module.** Both
  config models declare an `on:` filter, and neither `profile.py` nor
  `experiment_config.py` may import the other's dependency tree to share a
  literal.
- Smaller, deliberate: `run.py`'s lazy `report_*` manager is now `readback_*`
  and shared by both flags (step 6, honestly named); the notify block sits
  **before** the report block because the report block's `continue` would skip
  it; `--notify` on a validate-only run is refused the way `--report` is;
  `ReadoutData.timestamp` carries the verdict's own `end_ts` (the look the
  numbers are as of) rather than wall-clock "now"; and per-arm `n` is read off
  **that pair's own look**, since another metric's later row would otherwise
  lend it sizes nothing computed.

---

### NTF-2 — SRM-breach + pipeline-error urgency, per-channel `on:` filter enforcement

**Goal:** stage 2 — route the two blocking/urgent signals (SRM gate failure,
pipeline error) through the same NTF-1 seam, and make the per-channel/
per-experiment `on:` filters (schema landed in NTF-1) actually gate delivery,
so an "urgent-only" channel can be configured to receive solely `srm`+`error`
while a "readout" channel gets the routine one.

**Files touched:**
- `abkit/notify/dispatch.py` (extend: signal-kind gating, srm/error builders)
- `abkit/cli/commands/run.py` (wire the error + srm branches of the outcome loop)
- `tests/notify/test_dispatch.py` (extend)
- `tests/cli/test_run_command.py` (extend)

**Steps:**
1. `_passes_filter(kind, channel_on, experiment_on) -> bool` — `None` means
   "all kinds" at both levels; a kind must pass **both** filters
   (intersection, not union) to fire on a channel: the per-experiment `on:`
   narrows what the experiment ever sends; the per-channel `on:` narrows what
   that channel ever receives, independently.
2. **SRM:** `readout.srm_flag` is already the override
   `BaseChannel.verdict_kind()` checks (`base.py:118-124`) — dispatch's signal
   kind here is `'srm'`, fired from the **same** per-comparison `ReadoutData`
   already built for `'readout'` (NTF-2 re-classifies the already-built
   payload's kind for filter purposes, it never re-evaluates); when
   `readout.srm_flag`, dispatch both the `'readout'` kind (if configured) and
   treat it as also passing the `'srm'` filter on channels that only accept
   `'srm'`.
3. **Pipeline error:** in `run.py`'s existing `else: failed += 1;
   echo_error(...)` branch (`run.py:240-241`), call a new
   `dispatch_pipeline_error(experiment_config, error_message, channels_cfg,
   echo)`. No verdict/effect fields exist for a failed run, so a minimal
   `send_notice(notice, kind)` default is added to `BaseChannel` (a one-line
   body per kind reusing `build_context`'s brand/footer machinery) rather than
   inventing an `ErrorNotice`-plus-`ReadoutData` dual shape — this keeps the
   "only `send()` is abstract" contract intact and generalizes cleanly for
   NTF-5's `calibration_red`/`stale` kinds.
4. Design `send_notice`'s `kind` parameter as `Literal['error',
   'calibration_red', 'stale']` from the start (not just `'error'`), even
   though only `'error'` is wired here — NTF-5 needs the other two kinds and a
   later signature rework would touch every channel again.
5. Wire the error dispatch under the existing failed-experiment branch, still
   opt-in via the same `--notify` flag, still wrapped in its own never-fail
   `try/except`.

**Tests:**
- `tests/notify/test_dispatch.py`: a channel configured `on: ['srm', 'error']`
  receives an SRM-failed readout **and** a pipeline error notice but **not** a
  routine WIN readout; a channel configured `on: ['readout']` (or unset)
  receives the routine readout but not a bare pipeline error; an
  experiment-level `on: ['error']` suppresses its own readout/srm sends even
  on channels that would otherwise accept them (intersection semantics, not
  union).
- `tests/cli/test_run_command.py`: a forced-failure experiment (monkeypatched
  `outcome.status='failed'`) with `--notify` triggers exactly one
  `send_notice(kind='error')` call, never a `send()` call (no readout exists
  for a failed run).

**Risks / hotspots:**
- Adding `send_notice` as a new `BaseChannel` method (even with a default
  implementation) touches every existing channel's inheritance chain — a
  channel that overrides `build_context` in an incompatible way (check
  `email.py`'s HTML-card path specifically) could break silently. Run the
  **full** existing `tests/notify/test_channels.py` suite (439 lines) after
  this change, not just the new tests.

**Session estimate:** 1 session.

**As built (shipped 2026-08-03; the routing shape held, the payload shape did
not):**

- **A notice is a `ReadoutData` carrying `kind` + `notice`, not a new type.**
  That is what lets all five channels deliver one through the transport they
  already have, and it keeps "only `send` is abstract" true. The cost the file
  list did not name: every rich renderer needs ONE branch — `webhook._rich_body`,
  `telegram._build_html_message`, `email._build_html_body` and `email._subject`
  all assume a verdict, so an unbranched notice renders "Effect: N/A · p = N/A"
  and (via the unknown-verdict fallback) the word **Flat** for a crashed run.
  `base.py` gained `NOTICE_KINDS`, `is_notice`, `get_notice_template`,
  `default_template_for` (the format fallback is chosen by KIND, so a broken
  custom template degrades to the notice body, not to a readout of blanks) and
  a `timestamp_line` that collapses.
- **`send_notice(notice)` — the `kind` parameter step 4 asked for is not
  taken.** The kind has to live on the payload regardless, because
  `verdict_color()` is called deep inside each channel's payload builder where
  no argument of `send_notice` reaches; a second parameter would be a second
  source of truth free to disagree with it. The signature stays stable for
  NTF-5's kinds either way — they are values of the same field. `send_notice`
  raises on a verdict payload rather than rendering one blank.
- **No sixth brand token.** `docs/design/brand-tokens.md`'s five are VERDICT
  tokens and a notice is not a verdict, so `error`/`calibration_red`/`stale`
  all reuse `--srm` `#B23A6B` — already "no trustworthy result, look at this" —
  with the word and emoji carrying the distinction
  (`BaseChannel._NOTICE_PRESENTATION`). A designer's error token later changes
  that one map and nothing else.
- **SRM re-classification is `signal_kinds_for()`**, and delivery asks "does ANY
  kind pass both filters", so a channel accepting `readout` and `srm` still gets
  exactly one message. The experiment-level filter still wins (intersection).
- The step-3 claim that this "re-classifies the already-built payload" held
  exactly; `run.py`'s `failed` branch was the only new wiring, and the NTF-1
  claim that a failed run sends nothing was corrected across all four doc
  bodies in this WP.

---

### NTF-3 — `_ab_notify_states` table + verdict-change dedup/cooldown

**Goal:** stage 3, the design pass's "largest missing primitive" — a new
`_ab_notify_states` table + `abkit/database/internal_tables/_notify_states.py`
mixin + `abkit/notify/cooldown.py`, wired to suppress a repeat `readout` send
when the verdict hasn't changed since the last notify, with a configurable
cooldown floor reserved for future recurring kinds.

**Files touched:**
- `abkit/database/tables.py` (`get_notify_states_table_model`,
  `TABLE_NOTIFY_STATES`, register in `INTERNAL_TABLES`)
- `abkit/database/internal_tables/_notify_states.py` (new)
- `abkit/database/internal_tables/manager.py` (compose `_NotifyStatesMixin`)
- `abkit/notify/cooldown.py` (new)
- `abkit/notify/dispatch.py` (wire verdict-change detection + the cooldown
  gate before every `readout` send)
- `abkit/config/experiment_config.py` (`NotifyConfig.cooldown_seconds:
  int | str | None`)
- `tests/database/test_notify_states.py` (new)
- `tests/notify/test_cooldown.py` (new)

**Steps:**
1. `tables.py`: `get_notify_states_table_model()` — schema keyed by
   `(experiment, metric, name_1, name_2, method_config_id)` (the **full**
   comparison identity — §0.4 point 2 — so a re-tuned comparison starts a
   fresh dedup track); columns `last_verdict` (nullable String),
   `last_notified_at` (`DateTime64(3, 'UTC')`), `notify_count` (`UInt32`),
   `updated_at`. Deliberately **omit** the donor's `last_recovery_sent` (no
   recovery concept in abkit's experiment-primary model). Engine
   `ReplacingMergeTree(updated_at)`, the `_ab_aa_runs`-adjacent shape minus
   the alerting-only fields. `TABLE_NOTIFY_STATES = "_ab_notify_states"`;
   register in `INTERNAL_TABLES` (`tables.py:319-325` pattern).
2. `_notify_states.py`: `class _NotifyStatesMixin(_InternalTablesBase)` with
   `get_notify_state(experiment, metric, name_1, name_2, method_config_id) ->
   dict` (defaults `last_verdict=None`/`last_notified_at=None`/
   `notify_count=0`) and `upsert_notify_state(..., last_verdict=None,
   last_notified_at=None, increment_count=False)` using
   `self._manager.upsert_record` (the **already-shipped generic upsert**,
   `manager.py:251-276` — the same one `_AaRunsMixin`'s neighbors use, not a
   raw-array insert).
3. `manager.py`: add `_NotifyStatesMixin` to `InternalTablesManager`'s MRO
   (`manager.py:14-23` pattern).
4. `cooldown.py`: `is_in_cooldown(state, cooldown_seconds, now) -> bool` —
   (1) no `cooldown_seconds` configured → `False`; (2) `state['last_notified_at']
   is None` → `False` (never notified, no cooldown); (3) otherwise `(now -
   last_notified_at).total_seconds() < cooldown_seconds` → `True`. Minus the
   donor's recovery-reset step (no recovery concept here).
5. `should_notify_verdict_change(state, current_verdict, now) -> bool` — the
   resolved design (§0.4 point 2, §3 D2): `current_verdict !=
   state['last_verdict']` **always** fires regardless of `cooldown_seconds` (a
   WIN→LOSE flip must never be silenced by a stale cooldown timer); an
   unchanged verdict **never** re-fires. Implemented as the single boolean
   `current_verdict != state['last_verdict']` — `is_in_cooldown` is exposed as
   a separate, reusable helper for a **future** recurring kind (e.g. `stale`),
   not consulted here.
6. `dispatch.py`: before sending a `'readout'` `ReadoutData`, call
   `tables.get_notify_state(...)`, then `should_notify_verdict_change(...)`;
   if `False`, skip the send (echo a muted "verdict unchanged (WIN), notify
   skipped" line); else send, then `tables.upsert_notify_state(...,
   last_verdict=verdict.verdict, last_notified_at=now_utc_naive(),
   increment_count=True)`.

**Tests:**
- `tests/database/test_notify_states.py`: `get_notify_state` defaults on a
  never-seen key; `upsert_notify_state` round-trips `last_verdict`/
  `last_notified_at`/`notify_count` against the fixture backend already used
  by the internal-tables suite.
- `tests/notify/test_cooldown.py`: `is_in_cooldown`'s truth table
  (no-config/never-notified/inside-window/outside-window);
  `should_notify_verdict_change` fires on WIN→LOSE, WIN→WIN suppresses,
  first-ever notify (`state.last_verdict=None`) always fires.
- `tests/notify/test_dispatch.py` (extend): two consecutive
  `dispatch_experiment_signals` calls with an **unchanged** verdict send
  exactly once; a verdict flip between calls sends twice; the
  `_ab_notify_states` row's `notify_count` increments correctly.

**Risks / hotspots:**
- The cooldown-vs-dedup interaction (a verdict change always overrides
  cooldown) is a genuine design call with no one-line spec text pinning it —
  flagged for maintainer sign-off (§5); getting it backwards (cooldown
  suppressing a real WIN→LOSE flip) would be a **correctness-affecting
  silence bug** in a notification system — worse than a missed notify.
- Dropping the donor's `last_recovery_sent`/recovery-reset behavior is
  deliberate (§0.4 point 2) but worth re-checking against NTF-5's stale
  design in case a recovery-analog (e.g. "backlog cleared") turns out to be
  needed there too.

**Session estimate:** 1–2 sessions.

**As built (shipped 2026-08-03; the rule held, the KEY did not):**

- **The dedup signature is `(last_verdict, last_srm_flag)`, not the verdict
  alone**, so the table carries an extra `Bool` column the plan did not have.
  Step 1's word-only key reads fine until you notice that a pair sits at
  INCONCLUSIVE for its whole pre-horizon life: a sample-ratio gate breaking
  keeps the word identical, so the word-only dedup would have swallowed the
  `srm` signal NTF-2 had just built, on precisely the experiments most likely to
  need it. `announcement_signature()` names the pair; `should_announce()` is the
  single comparison over it.
- **State is written only after a channel ACCEPTED the message.** `_deliver`
  returns per-payload success counts for this reason. The plan's step 6 says
  "else send, then upsert"; recording an announcement that reached nobody (every
  channel down, or all filtered out) makes the next run treat the flip as old
  news and loses it permanently, because nothing re-derives what was never sent.
- **`_ab_notify_states` is experiment-keyed and joins the `abk clean` sweep.**
  Not tidiness: an experiment name deleted and later REUSED would inherit the
  old one's announcement history and have its first real verdict deduped away.
  Adding it turned two hand-listed roster tests ("all six tables") into ones
  derived from `INTERNAL_TABLES` — the plan did not name them, and they are the
  gate that made the addition visible.
- Smaller: `NotifyConfig.cooldown_seconds` is NOT added (step 6's config field).
  Nothing consults a cooldown for verdict dedup by D2, and shipping a knob that
  changes nothing is the "decorative knob" failure this track has recorded
  twice. `is_in_cooldown()` ships as the tested primitive; the config field
  lands with the first recurring kind that needs it (NTF-5's `stale`).
- `states` is a REQUIRED keyword on `dispatch_experiment_signals`, explicitly
  `None` where dedup does not apply — a default would let a caller silently
  disable the quiet by forgetting it.

---

### NTF-4 — Port the 4 missing channels: discord, teams, googlechat, ntfy

**Goal:** add `discord`/`teams`/`googlechat`/`ntfy` as thin `BaseChannel`
subclasses following abkit's own thin-adapter idiom (`slack.py`, 49 lines,
wrapping `WebhookChannel`; `webhook.py`'s `build_payload`/`send` split) — **not**
a verbatim port of the donor's heavier alerting-shaped files
(`discord.py` 426 lines / `teams.py` 329 / `googlechat.py` 392 / `ntfy.py` 305,
all built around multi-severity anomaly/recovery/no-data kinds abkit has no
equivalent of). Only the **wire format** is reused from the donor; the
**content** always comes from `BaseChannel.build_context`. **Independent of
NTF-1/2/3/5 — can be built/merged in parallel with the rest of the track.**

**Files touched:**
- `abkit/notify/discord.py` (new)
- `abkit/notify/teams.py` (new)
- `abkit/notify/googlechat.py` (new)
- `abkit/notify/ntfy.py` (new)
- `abkit/notify/factory.py` (register all 4 in `CHANNEL_TYPES`)
- `abkit/notify/__init__.py` (export the 4 new classes)
- `docs/guides/notification-channels.md` (document the 4 new channel types)
- `tests/notify/test_channels.py` (extend)

**Steps:**
1. `discord.py`: `class DiscordChannel(BaseChannel)` (webhook-url
   constructor); `send()` builds one embed (`{title, description, color,
   fields:[Effect,CI,p-value], footer, timestamp}`), `color` as a **decimal**
   int (Discord wants decimal, not hex — `int(self.verdict_color(readout)
   .lstrip('#'), 16)`) via `requests.post(webhook_url, json={'embeds':
   [embed]})`; reuse the donor's field-cap constants (`_TITLE_CAP=256`,
   `_DESCRIPTION_CAP=4096`, `_FIELD_VALUE_CAP=1024`, `_CONTENT_CAP=2000`,
   `_EMBED_TOTAL_CAP=6000`) as defensive truncation, dropping every donor
   field with no abkit equivalent (quorum/severity/anomalous-span/detector-params).
2. `teams.py`: `class TeamsChannel(BaseChannel)` targeting the Power Automate
   "Workflows" webhook (**not** the retired O365 connector — the donor's
   docstring explanation is a Microsoft-platform fact, reusable verbatim);
   `send()` posts an Adaptive Card (`contentType:
   'application/vnd.microsoft.card.adaptive'`) with a status-colored
   TextBlock + FactSet (effect/CI/p-value/alpha) — no per-message avatar
   override (the documented Workflows-path limitation applies identically).
3. `googlechat.py`: `class GoogleChatChannel(BaseChannel)` posting a Cards v2
   payload (`cardsV2:[{card:{header,sections:[{widgets:[{textParagraph}]}]}}]`);
   Cards v2 does **not** honor `\n` — body text needs explicit `<br>` line
   breaks (the donor's finding carries over verbatim as a platform fact);
   reuse the donor's `_ALL_MENTION='<users/all>'` + `_ALL_KEYWORDS` mapping
   for a `format_mentions` override.
4. `ntfy.py`: `class NtfyChannel(BaseChannel)` using ntfy's JSON-publish
   endpoint (`POST` to server root with `{topic, title, message, priority,
   tags}`, per the donor's documented UTF-8 rationale — reused verbatim as
   design rationale); map `verdict_kind()` (WIN/LOSE/FLAT/INCONCLUSIVE/SRM) to
   ntfy tags/priority via a **fresh** mapping table (the kind vocabulary
   itself does not carry over from the donor's anomaly/recovery/no_data/error
   kinds).
5. `factory.py`: add all 4 to `CHANNEL_TYPES` (`factory.py:27-33`) — 9 total,
   matching the donor's channel count.
6. `__init__.py`: export the 4 new classes + update the module docstring's
   "five channels" claim to "nine channels".
7. `docs/guides/notification-channels.md`: add 4 new config examples
   (`webhook_url` for discord/teams/googlechat, `topic`+`server` for ntfy)
   mirroring the existing slack/telegram examples' style.

**Tests:**
- `tests/notify/test_channels.py` (439 lines today, extend with 4 new test
  classes mirroring the existing webhook/slack/telegram shape): each new
  channel's `build_payload()`-equivalent produces the expected JSON shape for
  a WIN readout, an SRM-failed readout (color/tag maps to the SRM token), and
  truncation at the field caps for an oversized description; `requests.post`
  is mocked (no real network) — the mock is asserted called with the exact
  URL/headers/json.
- `abk test-report` exercises all 9 channels end-to-end (extend the existing
  command test to include the 4 new types in its channel loop).

**Risks / hotspots:**
- Reusing the donor's platform-specific caps/rationale verbatim is correct
  (the API limits don't depend on detectkit vs abkit); the risk is copying
  the donor's anomaly/severity **content** logic instead of just the
  wire-format shell — each `send()` must build its payload from
  `build_context()`'s dict, never from a donor-shaped `AlertData` object that
  doesn't exist in abkit.
- Teams' Workflows webhook shape can silently change (an active Microsoft
  migration as of the donor's own docstring) — worth one live-webhook smoke
  test against a real (test) Teams workflow before considering this WP done,
  not just JSON-shape unit tests.

**Session estimate:** 2 sessions.

**As built (shipped 2026-08-03; the shape held, the cost did not):**

- **Under a session, not two** — because NTF-2 had already split notice from
  verdict. Each channel needed ONE branch (statistics block or the sentence),
  not a second renderer, and `format_message` picks the template by kind.
- **Three platform rules that bend the house pattern**, each pinned by a test
  rather than a comment: Discord's colour is a DECIMAL int and a mention inside
  an embed never pings (top-level `content` + `allowed_mentions`, and stripped
  from the body so a handle is not printed twice); Teams takes a NAMED Adaptive
  Card colour, so it is the only channel where the brand token layer cannot be
  the literal source; Cards v2 ignores `\n` (escape, THEN convert to `<br>`);
  ntfy caps in BYTES, so the ellipsis has to fit inside the budget.
- **The ntfy priority override is partial by design** (`_OVERRIDABLE_KINDS`):
  a WIN or a FLAT stays calm however the channel is configured. The donor has
  the same idea for a different kind vocabulary; the mapping itself is fresh.
- `abk test-report` grew a nine-channel leg. Step 5's "9 total" is only true if
  the smoke test proves it, and the roster assertion moved to one place.
- **Not done, and named:** the §Risks live-webhook smoke against a real Teams
  Workflows endpoint. It needs credentials this environment does not have. The
  JSON shape is unit-pinned; the Workflows contract is an actively migrating
  Microsoft surface, so `abk test-report` is the first operator's job.

---

### NTF-5 — Calibration-red + stale/backlog signals (validate-only first cut)

**Goal:** stage 5 — two new signal kinds fired from existing seams:
calibration-red from `abk validate`'s per-cell FPR-budget scan, and
stale/backlog from `abk run`'s already-existing backlog warning string. New
`CALIBRATION`/`STALE` brand tokens are added to `abkit/notify/base.py`'s
verdict-color dicts, patterned after the existing SRM entry — **zero new
detection logic** (§0.4 point 4): both conditions already exist and are only
being routed/formatted here. **The explore-Apply calibration-red hook is
explicitly out of scope for this first cut** (§0.4 point 5) — deferred and
named in §5.

**Files touched:**
- `abkit/notify/base.py` (add `CALIBRATION`/`STALE` kinds to
  `_VERDICT_COLORS`/`_VERDICT_WORDS`/`_VERDICT_EMOJI`)
- `abkit/notify/dispatch.py` (`dispatch_calibration_red`, `dispatch_stale`,
  via NTF-2's generalized `send_notice(kind=...)`)
- `abkit/cli/commands/validate.py` (wire `_emit_matrix`'s red-cell scan)
- `abkit/cli/commands/run.py` (wire the existing `"backlog"` warning substring)
- `tests/notify/test_dispatch.py` (extend)
- `tests/cli/test_validate_command.py` (extend)

**Steps:**
1. `base.py`: add `'CALIBRATION': (hex, word, emoji)` and `'STALE': (hex,
   word, emoji)` entries — patterned after SRM's `#B23A6B` / "SRM gate
   failed" / purple-circle triple (`base.py:90-107`): two **new** hexes
   distinct from the existing 5 tokens' meanings, added to the same dict
   shape (never a parallel structure).
2. `validate.py`: in `_emit_matrix` (`validate.py:224-233`), after building
   `by_metric`, scan `result.cells` for the **existing** condition
   `cell.fpr is not None and cell.budget is not None and cell.fpr >
   cell.budget` (the exact "do not use" condition `_verdict` already renders
   as text, `runner.py:143-147`); collect the red cells; if any and
   `--notify` is set, call `dispatch_calibration_red(experiment, red_cells,
   channels_cfg, echo)` wrapped in try/except (never fail validate on a
   notify — the same "`_emit_report` never fails validate" precedent already
   at `validate.py:214-217`).
3. `run.py`: alongside the existing `srm_warnings` split
   (`run.py:233`), add `backlog_warnings = [w for w in outcome.warnings if
   "backlog" in w]` — the **exact** substring `driver.py:342-347` already
   emits into `outcome.warnings`, zero new detection — and call
   `dispatch_stale(experiment, backlog_warnings, ...)` under the same
   `--notify` gate.
4. `dispatch_calibration_red`/`dispatch_stale` build a lightweight notice and
   route through NTF-2's generalized `send_notice(notice, kind='calibration_red'
   |'stale')` path (**not** `ReadoutData` — no verdict/effect exist for a
   calibration or backlog signal).
5. **Explore-Apply is explicitly out of scope in this cut.** No
   `abkit/tuning/server.py` or `abkit/cli/commands/explore.py` changes ship
   in NTF-5; `_ExploreServer`'s `confirm_uncalibrated` Apply path
   (`server.py:325-399`, `_uncalibrated_keys` at `server.py:442`) is left
   untouched. This is named as a deferred follow-up in §5, not silently
   dropped from the design pass's original mention.

**Tests:**
- `tests/notify/test_dispatch.py`: `dispatch_calibration_red` fires once per
  validate run with ≥1 red cell, never fires when every cell is within
  budget; `dispatch_stale` fires exactly when a `"backlog"` warning string is
  present in `outcome.warnings`, never on an unrelated warning.
- `tests/cli/test_validate_command.py`: a fixture forcing one cell's
  `fpr > budget`, with `--notify`, triggers exactly one `calibration_red`
  send; a fully-calibrated fixture sends nothing.

**Risks / hotspots:**
- Depends on NTF-2's `send_notice` having a generic `kind` parameter from the
  start — if NTF-2 shipped a hardcoded `'error'`-only method instead, this WP
  requires a signature rework across every channel, not just an addition.
- Scoping out explore-Apply here (§0.4 point 5) is a deliberate milestone
  boundary, not an oversight — a future WP (named in §5) adds the
  `--notify` flag to `abk explore` and the notify-config plumbing
  `_ExploreServer` currently has none of, if the maintainer confirms it
  belongs in scope at all.

**Session estimate:** 1–2 sessions.

**As built (shipped 2026-08-03; the routing was the easy half):**

- **Step 1 is void, and deliberately so.** It asked for two NEW brand hexes for
  `CALIBRATION`/`STALE`; NTF-2 had already shipped all three notice kinds under
  the rule that the five brand tokens are VERDICT tokens and a notice is not a
  verdict, so every notice reuses `--srm`. What did change in
  `_NOTICE_PRESENTATION` is the WORD: `stale` said "Data is stale", which the
  signal does not mean (below). Adding a real error token stays a designer's
  call, one map away.
- **The detector NTF-5 was told to route was broken, and only routing it showed
  that.** `backlog_seconds(computed, watermark_ts)` measures against the
  watermark — wall-clock minus `data_lag`, which keeps advancing — while an
  experiment's cutoffs stop at its horizon. A finished, fully computed series
  therefore reported a backlog of "now − horizon" growing by a day every day: as
  a printed warning, noise nobody had complained about; as a notification, an
  alarm about every experiment a team has ever finished. It now measures against
  `last_due_cutoff(grid, watermark_ts)` — "the newest look this run could
  already have computed". This is the §0.4-point-4 "zero new detection" clause
  meeting its limit: routing a signal is a test OF that signal.
- **`stale` is retrospective, and the message had to say so.** Detection sits in
  PLAN, inside a run that then computes every pending look, so by delivery the
  gap is closed. A "still behind" flag would have been dead code (the compute
  loop drains `pending` by construction — a `cleared` field was drafted and
  removed for exactly that reason), and the honest news is that the SCHEDULE
  slipped: a run that never fired, was locked out, or failed.
- **Step 3's `"backlog" in w` substring became a structured field.**
  `RunOutcome.backlog: list[BacklogEntry]`, filled at the same condition, same
  place. The warning string carries the lag in hours, so a signature built from
  it would differ on every run and dedup nothing — and a reworded warning would
  silently kill the signal.
- **The recurring kinds needed a dedup rule of their own** (the plan has none;
  NTF-3 reserved `is_in_cooldown` for exactly this). `should_announce_recurring`
  = D2's shape for a condition that persists: a CHANGED signature always
  announces, an unchanged one waits for `notify.cooldown_seconds` (the field
  NTF-3 withheld until something consulted it), and `None` ≠ `0` — both are
  "no window" to `is_in_cooldown`, so deferring to it alone would have made the
  DEFAULT re-announce on every run. The signature is WHICH things are wrong
  (metrics behind, `metric·method_config_id` red cells), never the drifting lag
  or FPR. A CLEARED condition is written back as an empty signature — an
  observation, not an announcement, so NTF-3's "only what a channel accepted
  becomes history" is untouched — because otherwise a second outage months later
  dedups against the first and is never announced.
- **The notice state rows live in `_ab_notify_states` under a sentinel key**
  (`notice_state_key(kind)` → `metric='__stale__'`, empty arm pair). The
  `_ab_aa_runs` `__family__` precedent, but the sentinel NAME is not what makes
  it safe — `MetricConfig` accepts underscores — the EMPTY arm pair is, since a
  variant name cannot be empty.
- **`abk validate` gained `--notify`** (the plan's step 2 assumed it existed).
  It is best-effort on `--report`'s terms, inside the lock's try, and the
  dispatch runs even when nothing is red — that is how a previously announced
  failure is cleared.
- **Step 5 held: explore-Apply is untouched.** `tuning/server.py` and
  `cli/commands/explore.py` have no diff.
- **Named, not fixed:** `verdict_change` is the one declared kind nothing emits,
  so `on: [verdict_change]` matches NOTHING and is silent for a reason no
  operator could guess. NTF-3 made every delivered readout a change by
  construction, so wiring it is a two-line change — but which way to go (emit it
  beside `readout`, or drop it from `SignalKind`) is a vocabulary decision NTF-6
  owns.

---

### NTF-6 — Pipeline-error hardening, docs rewrite, and the NTF exit gate

**Goal:** stage 6 (already largely wired in NTF-2, hardened here) plus the
milestone's final documentation pass and end-to-end proof: a full
`docs/guides/notification-channels.md` rewrite covering the opt-in flag, the
per-experiment `notify:` block, all 6 signal kinds, dedup/cooldown semantics,
and the 9-channel roster; `CHANGELOG.md` entries; an e2e test exercising
every signal kind against fake channels in one scratch project; and 2
adversarial review rounds.

**Files touched:**
- `docs/guides/notification-channels.md` (rewrite)
- `docs/specs/notify-implementation-plan.md` — **note:** this WP produces the
  M12 *implementation record* appendix; per this milestone's own house
  convention it is folded into this same `m12-implementation-plan.md` file
  (§4/§6 below) at the exit gate rather than a second doc, mirroring how
  m4/m6 append their review record as a numbered section of their own plan
  doc, not a separate file.
- `CHANGELOG.md`
- `.claude/rules/` + `abkit/cli/assets/claude/` (three-way docs sync,
  `CLAUDE.md` invariant 7)
- `tests/e2e/test_notify_pipeline.py` (new)

**Steps:**
1. `docs/guides/notification-channels.md`: replace the current "`test-report`
   is the smoke test only … not yet built" framing with the real picture:
   `abk run --notify` (and `abk validate --notify`) sends real signals;
   document the 6 kinds (readout / verdict_change [same seam] / srm /
   calibration_red / stale / error), the per-experiment `notify:` block
   (channels/mentions/on), per-channel `on:` filters, dedup/cooldown
   semantics (a verdict-unchanged readout is silently skipped; a fresh
   verdict flip always fires; other kinds are not deduped in this
   milestone), and the 4 new channel config examples (cross-reference
   NTF-4's doc addition, don't duplicate).
2. `CHANGELOG.md` `[Unreleased]`: one entry per stage or one consolidated
   "Notifications are now wired end-to-end" entry with a bullet per
   capability — match the granularity the `0.1.2` entry uses as house style;
   explicitly state "No statistical numbers changed" since notify reads
   persisted rows only.
3. This document's §4/§6 (the "implementation record" appendix, produced at
   the exit gate) summarizes NTF-1..6's shipped design: the signal-kind
   table, the `_ab_notify_states` schema, the cooldown-vs-dedup resolution
   from NTF-3, and the `send_notice` kind-parameter generalization from
   NTF-2/NTF-5 — for the same audience the m4–m6 review-record sections serve.
4. `tests/e2e/test_notify_pipeline.py`: a scratch project with 2–3
   experiments (one healthy, one forced-SRM-fail, one forced-error) and a
   fake in-memory channel (implements `BaseChannel.send` by appending to a
   list), configured with per-experiment `notify:` blocks exercising
   channel/mentions/`on` narrowing; run `abk run --notify` end-to-end and
   assert: the healthy experiment's readout arrives once; running the
   **same** `abk run --notify` again with unchanged data sends **nothing**
   (verdict-change dedup working end-to-end through the real
   `_ab_notify_states` table); the SRM experiment's send lands on the urgent
   channel only; the failing experiment's error notice lands; and **no run
   ever exits non-zero solely because a channel raised** (inject one channel
   that always raises and confirm the run's exit code is unaffected — the
   single most important fail-soft proof in the whole track, §0.4 point 1).
5. **Round 1 review:** verify every notify call site (`run.py` ×3 signal
   kinds, `validate.py`) is wrapped in a `try/except` that cannot propagate,
   by grep + manual read, not by trusting the WP descriptions.
6. **Round 2 review:** adversarial focus on the cooldown/dedup state machine
   (NTF-3) for the ordering hazard the donor's cooldown code warns about — a
   race between two near-simultaneous runs of the same experiment
   double-notifying before either upserts state — and whether abkit's
   single-pipeline-lock-per-experiment invariant
   (`tables.acquire_lock`/`_ab_tasks`) already prevents this structurally,
   since two `abk run` invocations for the **same** experiment cannot run
   concurrently in the first place.

**Tests:**
- `tests/e2e/test_notify_pipeline.py` green in CI's e2e job.
- Both adversarial review rounds produce written findings attached to the PR
  (even if "none found").
- Full existing `tests/notify/test_channels.py` (439 lines) +
  `tests/cli/test_run_command.py` + `tests/cli/test_validate_command.py`
  suites stay green — the `abk test-report` connectivity-smoke path is
  explicitly **not** touched by NTF-1..6's real-send wiring, only reads the
  same 9-channel factory.

**Risks / hotspots:**
- The double-run dedup proof is the track's single highest-value assertion —
  if it's flaky or skipped under time pressure, the milestone's core value
  claim goes unverified.
- Three-way docs sync (`docs/` + `.claude/rules/` + `abkit/cli/assets/claude/`)
  is easy to do for 2 of 3 and forget the 3rd — use the existing 0.1.2-era
  sync as a template for exactly which files move together.

**Session estimate:** 1 session.

---

## 2. Dependency graph / parallelism

```
NTF-1 (send seam + schema) ──▶ NTF-2 (srm/error + on: filters) ──▶ NTF-5 (calib-red + stale)
        │                              │                                   │
        └────────────▶ NTF-3 (dedup/cooldown state, can run parallel) ─────┤
                                                                            │
NTF-4 (4 channels — fully independent) ─────────────────────────────────────┼──▶ NTF-6 (e2e + docs + exit gate)
                                                                            │
```

- **NTF-1 must land before NTF-2 and NTF-5** — `dispatch.py` + the
  `NotifyConfig`/`on:` schema is the shared foundation both extend.
- **NTF-3's `_ab_notify_states` table is independent of NTF-2/NTF-4** and can
  be built in parallel with them, but **NTF-6's e2e test needs NTF-1..5 all
  merged.**
- **NTF-4 (4 new channels) has NO dependency on NTF-1/2/3/5** — it only
  extends `factory.py`/`base.py` and could be built and merged **first** or in
  full parallel with the rest of the NTF track; it is sequenced fourth in the
  WP list above only to match the design pass's stated rollout order, not
  because of a real code dependency.
- **DASH (M11) and NTF (M12) tracks are fully independent** — no shared
  files — and could in principle run in parallel across two
  contributors/sessions, though the milestone map sequences M11 before M12
  (`0.6.0` before `0.7.0`).

---

## 3. Decisions — the open points, settled here

Mirrors the m4 D-item convention: every open point the design/verify pass
surfaced is resolved below; a different maintainer call reshapes the
corresponding WP.

**D1 — Default channel selection when `experiment.notify` is absent: all
profile-configured channels.** Once `--notify` is passed at the `run`/
`validate` level (the opt-in gate), an experiment with no `notify:` block
sends to **every** channel configured in `profiles.yml`'s
`notification_channels:` — narrowing only happens when the experiment
explicitly sets `notify.channels`. This reading follows the "opt-in flag"
language most literally (the flag is the opt-in; the channel list is a
narrowing filter, not a second opt-in) but is **not** an evidenced maintainer
decision — flagged for explicit sign-off in §5 before NTF-1 merges. Getting it
backwards (default = no channels until the experiment explicitly lists them)
is a smaller, safer blast radius and is the fallback if sign-off goes the
other way; NTF-1's tests are written against the "all configured channels"
default and would need inverting.

**D2 — Verdict-change dedup always overrides cooldown; an unchanged verdict
never re-sends.** (§0.4 point 2.) `should_notify_verdict_change` is the single
authority for the `readout`/`verdict_change` kind: `current_verdict !=
state.last_verdict` fires unconditionally; equality suppresses
unconditionally. `cooldown_seconds` exists in the schema from NTF-3 onward
but is **not consulted** by this function — it is reserved for a future
recurring signal kind that legitimately re-fires with the same value. This is
this plan's resolution of a one-line design-pass gloss and needs explicit
maintainer sign-off (§5) since the opposite reading (cooldown suppressing a
real flip) is a silent-correctness bug, not a crash.

**D3 — Dedup state key is the full comparison identity, not a display name.**
`(experiment, metric, name_1, name_2, method_config_id)` — including
`method_config_id` so a re-tuned comparison (identity-changing param edit)
starts a fresh dedup track rather than silently inheriting a stale
`last_verdict` from a differently-configured method. No schema migration path
is offered for a `method_config_id` change (mirrors the general "editing an
identity param orphans the prior series" convention already governing
`_ab_results`).

**D4 — `send_notice(kind)` is generalized from NTF-2 onward, never
hardcoded to `'error'`.** `Literal['error', 'calibration_red', 'stale']` is the
full kind vocabulary from NTF-2's first landing, even though only `'error'`
fires until NTF-5. This is a design commitment made explicitly to avoid a
signature rework touching every channel a second time; NTF-5 depends on it
holding.

**D5 — Explore-Apply calibration-red is deferred, not implemented, in M12.**
(§0.4 point 5.) `abk validate`'s per-cell scan is the only calibration-red
source this milestone ships. Wiring `_ExploreServer`'s `confirm_uncalibrated`
Apply path requires adding notify-config plumbing (channels, project name,
possibly a new `--notify` flag on `abk explore`) to a server that today
carries none of it — the single most invasive addition relative to its
one-line origin in the design pass. Recorded here as **out of scope**, named
again in §5 as a decision the maintainer may pull into a future milestone or
a M12 follow-up WP, not silently dropped.

---

## 4. Exit gate

**EXECUTED 2026-08-03 (NTF-6).** `tests/e2e/test_notify_pipeline.py` is green
(9 tests, CI e2e job): one `abk run --notify` over three experiments — healthy,
SRM-broken (a 90/10 `expected_split` against 50/50 data, so the gate fails on
real counts), and doomed (its own `added_filters` marker makes the warehouse
raise for that experiment only) — proves the routing, the cross-invocation
dedup through the real table, the fail-soft claim with a channel that raises on
every send, and one channel per signal kind. Two mutations confirm the gate
bites: removing `_deliver`'s per-channel catch, and adding a seventh
`SignalKind`. Requirement (d) is served by the untouched
`tests/cli/test_test_report_command.py`, which constructs and sends through all
nine types. Both review rounds are recorded in §7.

**NTF track exit gate** (from the shared design pass's `exit_gate`, NTF half,
plus the track's common discipline):

- `tests/e2e/test_notify_pipeline.py` green, proving:
  (a) a repeat `abk run --notify` with an **unchanged** verdict sends
  **nothing** (dedup working end-to-end through `_ab_notify_states`);
  (b) a channel that **always raises never fails the run's exit code**
  (fail-soft proven, not just claimed — §0.4 point 1);
  (c) per-channel/per-experiment `on:` filters correctly route SRM/error to
  an urgent subset;
  (d) all **9** channels are registered and connectivity-tested via the
  untouched `abk test-report` path.
- **2 adversarial review rounds**, focused on (per NTF-6): every notify call
  site's exception-swallowing (grep + manual read, not trust), and the
  cooldown/dedup ordering hazard against abkit's single-pipeline-lock
  invariant.
- **Zero `abkit/stats/` changes; zero `ALGORITHM_VERSION` bump** — the grep
  for the version constant stays empty across every NTF-1..6 diff.
- `CHANGELOG.md` entries landed for each stage (or one consolidated entry per
  house style, NTF-6).
- **Docs three-way sync verified in the same PR**: `docs/guides/notification-channels.md`
  (rewritten, 5→9 channels), `.claude/rules/`, and
  `abkit/cli/assets/claude/` (`CLAUDE.md` invariant 7 — keep `init-claude`
  assets in sync on release).
- Web: **no** `web/` change is expected in this milestone (notifications have
  no client-rendered surface) — if a WP unexpectedly touches `web/src/**`,
  the standard `cd web && npm run build` + committed-bundle discipline still
  applies (track-wide convention), but no WP above plans one.

---

## 5. Open questions / "before start" decisions

From the design pass's `open_questions` (NTF-relevant subset) plus the
track plan's "Перед стартом" line for M12, translated and carried forward:

1. **Default channel selection when `experiment.notify` is absent** (D1):
   **SIGNED OFF 2026-08-02 (maintainer, in conversation) — as recommended:**
   once `--notify` is passed at the run level, an experiment with no `notify:`
   block sends to **all profile-configured channels**. The block is *routing*
   (which channels, which mentions, which kinds), never the switch — the flag
   is the switch. The rejected reading ("no channels unless the experiment
   opts in") makes `--notify` on a freshly-configured project silent, and
   silence is indistinguishable from a broken flag. NTF-1 implements this in
   `dispatch.resolve_channels`; the CLI additionally prints a loud line when
   `--notify` is passed with **no** `notification_channels` at all.
2. **NTF-3's cooldown-vs-verdict-change-dedup interaction** (D2):
   **SIGNED OFF 2026-08-02 (maintainer, in conversation) — as recommended:** a
   verdict flip **always** sends, even inside the cooldown window; an unchanged
   verdict is **never** re-sent regardless of `cooldown_seconds`, which stays
   reserved for a future recurring kind (a repeating `stale`) that legitimately
   re-fires with the same value. The rejected reading lets a cooldown swallow
   the single WIN→LOSE message the whole feature exists to deliver. Binding on
   NTF-3.
3. **Explore-Apply calibration-red is deferred out of this milestone's first
   cut** (D5, §0.4 point 5): `abk explore` gains no `--notify` flag and
   `_ExploreServer` gains no notify-config plumbing in M12. Confirm this
   belongs deferred entirely, or whether "calibration-red from `abk validate`
   only" is acceptable as a *permanent* scope boundary (not just a "first
   cut") — i.e., should a future milestone even revisit the explore-Apply
   half, or is it dropped for good? (Track plan's explicit "перед стартом"
   item — recommendation: defer explicitly, decide the permanence question
   at, or after, the M12 exit gate rather than blocking NTF-5 on it now.)
4. **Should `abk explore` ever gain its own `--notify` flag** — needed only
   for the deferred Apply-uncalibrated hook — or is calibration-red-on-Apply
   out of scope until `explore` has some other, unrelated reason to carry
   notify config? Downstream of question 3; no NTF WP depends on the answer.

---

## 6. Dependencies (incl. inter-milestone collisions)

- **Sequencing within the track:** NTF-1 → {NTF-2, NTF-3 in parallel} →
  NTF-5 (needs NTF-2's `send_notice` generalization, D4) → NTF-6 (needs
  NTF-1..5 all merged). NTF-4 has no code dependency on any other NTF WP and
  may be built/merged at any point in the sequence (§2).
- **M12 is independent of M9** (the additive compute engine / CUPED Tier-E)
  in terms of code paths — neither touches `abkit/stats/` or changes a
  persisted numeric field — **but both milestones edit the same schema module
  `abkit/database/tables.py`**: M12's NTF-3 adds a new `_ab_notify_states`
  entry to the `INTERNAL_TABLES` dict; M9's **WP1** adds the 4 nullable
  covariate columns (`cov_std_1/2`, `corr_coef_1/2`) to `_ab_results` via
  `get_results_table_model` in that same file (the M9 STATE-stage wiring
  itself — WP3 — touches `_unit_state.py`/`driver.py`/`metric_loader.py`,
  not `tables.py`; see the WP1/WP3 file lists in
  [m9-implementation-plan.md](m9-implementation-plan.md)).
  If both land concurrently (overlapping PRs across sessions), **coordinate
  migration/PR ordering** on `tables.py`/`INTERNAL_TABLES` to avoid a merge
  collision on the same file — this is a scheduling note, not a design
  coupling.
- **M12 is independent of M11** (`abk dashboard`, DASH-1..7) — no shared
  files; the two tracks could run in parallel across contributors even
  though the milestone map sequences M11 (`0.6.0`) before M12 (`0.7.0`). A
  future milestone surfacing notify history/state in the dashboard (e.g. as
  part of M14's cross-arm overview, which itself builds on M11) is not in
  scope for either M11 or M12 as specified.
- **Release discipline** (track-wide, ROADMAP "Сквозная дисциплина"): one
  minor version per milestone (M12 → `0.7.0`); WP = PR (tests + CHANGELOG +
  conventional commit); the three-way docs sync + wheel-namelist + pip-smoke
  gates apply at the tag; `tag → publish.yml` per the established M1–M6
  release flow.

---

## 7. Implementation record (M12, written at the exit gate)

The as-built summary NTF-6 owes the next reader; the per-WP "As built" notes
under §1 stay the detailed source. Written for the audience the m4–m6 review
records serve.

### 7.1 The six signals, and where each is decided

| Kind | Emitted by | Decided from | Deduped |
|---|---|---|---|
| `readout` | `abk run --notify` | `readout.evaluate()` over persisted rows | signature `(verdict, srm_flag)` per comparison |
| `verdict_change` | the same payload | the verdict WORD vs the one last announced | rides the readout's dedup |
| `srm` | the same payload | `ExperimentReadout.srm_flag` | rides the readout's dedup |
| `error` | `abk run --notify` | `RunOutcome.status == "failed"` | never — a run that failed twice failed twice |
| `stale` | `abk run --notify` | `RunOutcome.backlog` (PLAN stage) | signature = which metrics, + cooldown |
| `calibration_red` | `abk validate --notify` | `CellResult.fpr > CellResult.budget` | signature = which cells, + cooldown |

Three of them ride ONE payload. `signal_kinds_for()` answers with every kind a
readout legitimately belongs to, and delivery asks "does ANY of them pass both
`on:` filters" — so a channel accepting two of them still receives exactly one
message. This is a re-CLASSIFICATION, never a re-evaluation: there is one
verdict per comparison per run and every surface reads it from the same place.

Nothing in `abkit/notify/` computes a statistic. The verdict is
`readout.evaluate()`'s — the function `build_report_payload` and the dashboard
already call — so a message cannot disagree with the report about the same
experiment. This is the m11 launcher discipline applied to a second surface.

### 7.2 `_ab_notify_states`, and what a row means

PK `(experiment, metric, name_1, name_2, method_config_id)`; `last_verdict`,
`last_srm_flag`, `last_notified_at`, `notify_count`, `updated_at`.

- **Comparison rows** hold the last ANNOUNCED verdict + SRM flag. The pair is
  the signature, not the word alone: a pre-horizon pair says INCONCLUSIVE
  whether or not its split is broken, so a word-only key would swallow the SRM
  alarm on the experiments most likely to need it (NTF-3).
- **Notice rows** — one per experiment per recurring kind — use the sentinel
  key `notice_state_key(kind)`: `metric='__stale__'`/`'__calibration_red__'`
  with an EMPTY arm pair. The empty pair is the guarantee (a variant name
  cannot be empty); the sentinel NAME is only an idiom, since `MetricConfig`
  accepts underscores. `last_verdict` there holds the condition's signature,
  and `""` means "the condition cleared" (NTF-5).
- **A row is written only after a channel ACCEPTED the message.** Recording an
  announcement that reached nobody loses the flip permanently, because nothing
  re-derives what was never sent. The one deliberate exception is a cleared
  recurring signature, which records an OBSERVATION nobody had to receive.
- The table is in `EXPERIMENT_KEYED_TABLES`, so `abk clean
  --orphaned-experiments` purges it — a deleted-and-reused experiment name
  would otherwise inherit the old history and have its first verdict deduped
  away.

### 7.3 Cooldown vs dedup, as resolved

D2 (signed off before implementation) governs verdicts: a change always sends,
an unchanged verdict never re-sends, and `cooldown_seconds` is not consulted.
NTF-5 gave the field its only consumer — the two RECURRING kinds, whose
condition is still true on the next run. There the rule is the same shape with
one addition: a changed signature always announces, an unchanged one waits for
the cooldown. `None` (the default) means "never repeat" and is deliberately
distinct from `0`; `is_in_cooldown` mutes neither, so deferring to it alone
would have made the default re-announce every run.

### 7.4 Review rounds (NTF-6)

**Round 1 — every notify call site's exception handling**, by grep and read
rather than by trusting the WP descriptions. Four dispatch calls live in
`run.py` under ONE `try/except Exception`, and one in `validate.py` under its
own; `_deliver` additionally catches per channel (construction and send
separately). The structural argument was not accepted on its own: the two
dispatches that had no behavioural proof — `dispatch_stale` (last in run.py's
block, after a readout has already gone out) and `dispatch_calibration_red` —
now have one each, and the deliberate double-wrapping stands (§0.4 point 1: the
inner catch keeps one bad channel from blocking the rest, the outer keeps a
pre-channel failure from failing the run; a simplify pass must not collapse
them).

**Round 2 — the double-notify race.** The donor's cooldown code warns about two
near-simultaneous runs both announcing before either writes state. In abkit it
is structurally impossible for the `run` path: `_ab_tasks` holds one lock per
experiment at `(experiment, "pipeline", "run")`, so a second concurrent
invocation gets `status="locked"` — and `locked` notifies nothing at all, by
the NTF-1 rule that only `completed`/`failed` reach the notify block. Within one
invocation the notify block runs in the MAIN thread over `outcomes` after the
worker pool has joined, so `--workers N` does not reintroduce it. The one pair
that CAN run concurrently is `abk run --notify` and `abk validate --notify` on
the same experiment (different locks, by m4's D5) — they write disjoint rows
(comparison + `__stale__` vs `__calibration_red__`), so the race is real but
harmless. No code change.

**Also found and fixed in this WP** (writing the gate, not reading the code —
the recurring lesson): one of the gate's own assertions was a tautology
(`... or True`), and its roster test pinned a hand-written set of covered kinds
instead of deriving coverage from the file's own channel configs. Both were
rewritten before the gate was believed.

### 7.5 What M12 deliberately did not do

- **No `abk explore --notify`** and no notify plumbing in `_ExploreServer`
  (§0.4 point 5). The Apply-uncalibrated hook stays out; `abk validate
  --notify` is the calibration path. §5 questions 3–4 are the maintainer's to
  close.
- **No monitoring semantics**: no severities, no recovery events, no
  anomaly/no-data kinds. A "backlog cleared" message was considered for NTF-5
  and rejected — the clearing is recorded, not announced.
- **No statistical change**: zero `abkit/stats/` diffs and no
  `ALGORITHM_VERSION` bump across NTF-1..6; every number in a message is copied
  off a `PairVerdict` or a `CellResult`.
