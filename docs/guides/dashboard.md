# The dashboard (abk dashboard)

`abk dashboard` serves a **project-level cockpit**: one row per experiment, with
its verdict, effect, p-value and a sparkline of the stabilization history — and
buttons that launch the same `abk` commands you would type yourself.

```bash
abk dashboard                       # every experiment in the project
abk dashboard --select tag:actual   # just the ones you are watching
abk ui                              # an alias for the same command
```

It prints a `http://127.0.0.1:<port>/?token=…` URL and opens your browser.
Ctrl-C stops it.

> **It is a launcher, not a monitor.** The dashboard computes no statistic and
> takes no pipeline lock. Every number on the page comes from rows `abk run`
> already persisted, read through the same `readout.evaluate()` decision code as
> `abk run --report`; every button is a real `abk` subprocess. There is no
> alerting, no scheduling and no polling of your warehouse — a row is read when
> the page asks for it. It *does* write experiment YAML when you edit one, and
> that is not an exception to the rule: a config is your own declaration, not a
> result — no number on the page derives from it, and a save can never block a
> pipeline.

## What a row shows

The page renders instantly: the boot payload is **metadata only** (name, file,
tags, status, timezone, window, configured comparisons), so a project with a
hundred experiments does not wait on a hundred queries. The client then fills
the rows, **three at a time**, one `GET /api/stats/<experiment>` each.

Each filled row carries the **headline** comparison's state. At two arms that is
the one ship decision. At **three or more** it is the first declared main
metric's **leader** — the arm that beat the control with the best effect in the
desired direction (m14 DEC-4) — falling back to the first declared treatment
when no arm beat the control. Through `0.8.0` the row showed the first declared
treatment unconditionally, which at 3+ arms presented an arbitrary arm as the
experiment's result.

| Cell | Meaning |
|---|---|
| verdict | `WIN` / `LOSE` / `FLAT` / `INCONCLUSIVE`, or `no data — press Run` when nothing is persisted yet |
| effect | the headline effect, signed |
| p | the headline p-value |
| last look | the newest computed cutoff (UTC) |
| sparkline | the effect across looks, over the selected window |

Plus the chips that keep a verdict honest — the same three states the HTML
readout marks:

- **pre-horizon** — the experiment has not reached its horizon and the method is
  fixed-horizon, so no WIN/LOSE is called (see
  [reading a readout](reading-a-readout.md) and [sequential](sequential.md)).
- **insufficient** — the headline look was **demoted**: it did not meet the
  minimum counts, so it reports counts only. This reads the persisted
  `insufficient_data` flag of that look, not a re-derived guess.
- **SRM** — the red gate: the observed arm split does not match the expected
  one. Fix the assignment before trusting any effect on that row.
- **→ arm** — the **leader chip** at 3+ arms: the arm this metric says to ship.
  Hover for the readout's own sentence. Absent when no arm beat the control.
- **leaders split** — two main metrics name different arms. abkit reports the
  disagreement and deliberately does not break the tie: there is no declared
  metric priority for it to use. Hover for each metric's leader.
- **locked** — an `abk run` currently holds this experiment's pipeline lock.
- **error** — that row failed to build (a warehouse hiccup, an orphaned method
  config). It is *that row's* error: the other rows still fill.

The **guardrail** chip is deliberately control-anchored: it fires when a
guardrail regressed against the *control*, never when one treatment regressed
against another — which says nothing about whether the experiment harms users.

Expand a row (the `▸` toggle) for every DECLARED pair — the ship decisions plus,
under `contrasts: all_pairs`, the arm-vs-arm comparisons, each tagged **`arm vs
arm`** because a `WIN` there means "this arm came out ahead of that one", not
"ship it" — the facts line
(SRM p, last look, timezone, lock state), the per-metric Run buttons, the
maintenance buttons, and the YAML editor.

### The window bounds the sparkline only

`--window` (and the segmented control on the page) picks `24h`, `7d`, `30d`
(default), `90d` or `all`. It changes **only how much of the sparkline is
drawn** — every verdict, effect and p-value is always the full series'. That is
deliberate: `_ab_results` rows are *cumulative looks*, so dropping the oldest
would not shorten the experiment, it would truncate its stabilization history
and could change what the readout decides. A row therefore never disagrees with
what `abk run --report` shows for the same experiment.

## The buttons

Every one of them spawns a real `abk` subprocess in your project directory, with
the same `--profile` the dashboard was started with, and streams its output into
the job drawer:

| Button | Spawns | Notes |
|---|---|---|
| **Run** | `abk run --select <exp>` | the whole experiment |
| **Run `<metric>`** | `abk run --select <exp> --metric <m>` | one comparison (per row detail); secondary metrics are listed too |
| **Explore** | `abk explore --select <exp>` | opens the tuning cockpit in a new tab; the request is held until the child prints its URL (up to 90 s) |
| **Unlock** | `abk unlock --select <exp>` | clears a stale lock |
| **Clean…** | `abk clean --select <exp> --execute` | behind a confirm box — it **deletes** orphaned rows, and there is no undo |
| **Open** | — | the full HTML readout for that experiment, rendered on demand (not a stale file from `reports/`) |
| **Edit YAML** | — | the experiment config as it is on disk, editable in place — see [editing a config](#editing-a-config) |

**Job discipline.** `run`, `unlock` and `clean` are **one at a time** across the
whole project — press Run while one is going and the request is refused, because
they contend for the pipeline lock. `explore` is exempt (it takes no lock), and
is deduped per experiment. The job chip shows what is running; the drawer keeps
each job's log, and **Stop** terminates one. Ctrl-C on the dashboard terminates
every job it spawned — a run must not outlive the cockpit that started it.

## Editing a config

`Edit YAML` opens the experiment file in a textarea with **Save**, **Revert** and
**Delete…**. The text round-trips **verbatim**: your comments, key order and
blank lines survive a save, normalized only to end with a newline. That is why
the editor is not built on `abk explore`'s Apply — Apply re-emits a *parsed*
document, so every comment in the file is lost. Apply exists to write back a
knob you turned; this editor exists to let you write the config yourself.

A save is **validate → archive → write**, in that order:

1. **Validate**, both levels: the pydantic `ExperimentConfig` shape *and* the
   level-2 matrix that `abk run --steps validate` runs (declarative-config §8) —
   reference integrity, the CUPED rules, the cadence/looks gates over the real
   grid, the no-DB SQL render smoke. Errors refuse the write; warnings ride back
   into the editor.
2. **Archive** the previous file byte-verbatim under `.history/<name>/` *beside
   the file* — a nested `experiments/growth/x.yml` archives to
   `experiments/growth/.history/x/`, the same archive tree `abk explore`'s Apply
   writes into.
3. **Write** atomically (temp file + rename), so nothing can leave a
   half-written config on disk.

**Revert** discards the buffer and re-reads the file. **Delete…**, behind a
confirm box that names what is *not* deleted, removes the **YAML only**: the
experiment's rows in `_ab_results` / `_ab_unit_state` stay until `abk clean
--orphaned-experiments` prunes them. The deleted file is archived as
`<name>-<stamp>-deleted.yml`, so the delete is reversible by hand.

Renaming is allowed — change the config's `name:` and save. The file keeps its
path, the archive is keyed by the *old* name, and you are warned that already
persisted rows keep the old name too (`abk clean --orphaned-experiments` is how
you prune them).

### What a save refuses

Every refusal is a `400` with the reason in the message:

- **A stale digest.** Opening the file hands the editor a sha256 of its text; a
  save or delete echoes it back and is refused if the file on disk has changed
  meanwhile. That covers a second browser tab *and* an `abk explore` Apply —
  which this dashboard can itself have spawned on the experiment you are
  editing. The digest is checked before your text is even parsed: a stale buffer
  has to be reopened either way.
- **A running job.** A save or delete is refused while the dashboard has a
  `run` / `unlock` / `clean` / `explore` job going on that experiment. A job you
  started in your own terminal is invisible to this check; the digest still
  catches the `explore` half of it, after the fact.
- **Level-2 findings** — unless you press **Save anyway**, which appears on
  exactly those refusals. It downgrades the §8 errors to loud warnings (*SAVED
  WITH AN ERROR — `abk run` will refuse this: …*) so the editor is usable on a
  project that does not lint yet. **Level 1 is never forceable**, and grows no
  such button.
- **A file too large to show.** Over 512 kB the source comes back truncated and
  not editable: writing the prefix back would drop the tail.
- **A duplicate name**, checked across the whole project and across the one
  namespace experiments and metrics share.

### New experiment

**New experiment**, in the header controls, opens a panel with a seeded template
and an optional subfolder field. There is no file-name box — the file is named
after the config's own `name:`, and an existing file is refused. The folder must
be a subdirectory of `paths.experiments`; an absolute path, a `..` or a hidden
component is refused. If the new experiment falls outside the `--select` this
cockpit was started with it is reported as such and stays off the page — never
silently missing.

### The selection refreshes itself

After every successful save, create or delete the server re-resolves the
selection it was started with, re-bakes the boot page, and returns the refreshed
experiment list in the same reply — so a rename or a new experiment appears
without a restart. **Reload configs** in the header is the manual form: it
re-reads every config from disk, and it is what you press after editing a YAML
in your own editor. If that re-read fails (a broken sibling YAML, a name
collision) the previous selection is kept and the failure comes back as a
warning — your write has already landed, so it never turns into a 500.

`Refresh` and `Reload configs` are different buttons on purpose: `Refresh`
re-reads the **warehouse** for rows, `Reload configs` re-reads the **YAML on
disk**.

## Options

```bash
abk dashboard [--select <sel>]... [--exclude <sel>]... [--window 24h|7d|30d|90d|all] \
              [--no-open] [--profile NAME]
```

`abk ui` is an alias for the same command — literally the same callback, so the
options and the help can never drift apart. The canonical name stays
`dashboard`, because `dashboard` and `explore` say *which* surface you want and
`ui` does not.

- `--select` / `-s` — experiment selector: name, path glob, `tag:<tag>` or `*`
  (repeatable; every experiment when omitted). `--exclude` removes matches. Both
  behave exactly as they do on `abk run` — see the
  [CLI reference](../reference/cli.md#the-two-level-selector-model).
- `--window` — the **initial** sparkline window (default `30d`); the page can
  switch it without a restart. An unknown value is refused at startup, where you
  typed it.
- `--no-open` — do not launch a browser (the URL still prints).
- `--profile` — the `profiles.yml` connection, and the profile every spawned
  command inherits. A staging dashboard cannot launch a production run.

Configs are read at boot and re-read on demand: **Reload configs** picks up an
edit you made in your own editor, and every save through the page refreshes the
selection itself (see [editing a config](#editing-a-config)). A project that has
never run is fine — that is what the Run buttons are for.

## Security: the URL is a credential

The server binds `127.0.0.1` only, and a one-shot token is generated per start.
**Every request is authorized, `GET` included** — the row reads hit your
warehouse, the boot page enumerates your experiments, and the editor routes
**write files in your project**, so an unauthenticated request would be a real
leak in one direction and a real write in the other. The token lives in the URL,
not in the page, so the served HTML is not a credential at rest.

Consequence: anyone who has that URL can spawn `abk run` / `abk clean` on your
project, and can overwrite or delete an experiment YAML on your disk. Do not
paste it into a shared channel, and do not port-forward the port to a network
you do not trust. Every overwrite is archived under `.history/`, which is a
recovery path, not a defense.

## Gotchas

- **A row says `no data — press Run`.** Nothing is persisted for that
  experiment yet (or `_ab_results` does not exist at all). The dashboard never
  creates schema; `abk run` does.
- **`pending` rows that never fill.** Check the banner: a page opened without
  its `?token=` gets a 403 on every request. Reopen the URL the command printed.
- **The verdict looks older than the run you just launched.** Rows are read on
  demand, not pushed — press **Refresh** (or reopen the row) after a job
  finishes.
- **Run is refused with a 400.** Another pipeline job is running; the job chip
  names it. The chip is advisory, the refusal is authoritative.
- **A save is refused with a 400.** Read the message: a stale digest means the
  file changed under you (reopen it and redo the edit), a running job means the
  experiment is busy, a §8 finding means the config would not run — and only
  that last one offers **Save anyway**.
- **The cockpit will not stop.** The dashboard has no terminal action by design
  — Ctrl-C is the exit, and it takes the spawned jobs down with it.

## See also

- [The explore cockpit](explore.md) — the per-experiment tuning surface the
  Explore button launches, and the other surface that writes a config (its Apply
  re-emits a parsed document; this editor keeps your text verbatim).
- [Reading a readout](reading-a-readout.md) — what the verdicts and the
  pre-horizon / insufficient / SRM states mean.
- [CLI reference](../reference/cli.md) — every command and flag.
- [Visualizing results](visualizing-results.md) — BI over `_ab_results` when
  you want dashboards that outlive a localhost session.
