# The dashboard (abk dashboard)

`abk dashboard` serves a **project-level cockpit**: one row per experiment, with
its verdict, effect, p-value and a sparkline of the stabilization history — and
buttons that launch the same `abk` commands you would type yourself.

```bash
abk dashboard                       # every experiment in the project
abk dashboard --select tag:actual   # just the ones you are watching
```

It prints a `http://127.0.0.1:<port>/?token=…` URL and opens your browser.
Ctrl-C stops it.

> **It is a launcher, not a monitor.** The dashboard runs no pipeline, computes
> no statistic, takes no lock, and writes no config. Every number on the page
> comes from rows `abk run` already persisted, read through the same
> `readout.evaluate()` decision code as `abk run --report`; every button is a
> real `abk` subprocess. There is no alerting, no scheduling and no polling of
> your warehouse — a row is read when the page asks for it.

## What a row shows

The page renders instantly: the boot payload is **metadata only** (name, file,
tags, status, timezone, window, configured comparisons), so a project with a
hundred experiments does not wait on a hundred queries. The client then fills
the rows, **three at a time**, one `GET /api/stats/<experiment>` each.

Each filled row carries the **headline** comparison's state — the same headline
the readout picks:

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
- **locked** — an `abk run` currently holds this experiment's pipeline lock.
- **error** — that row failed to build (a warehouse hiccup, an orphaned method
  config). It is *that row's* error: the other rows still fill.

Expand a row (the `▸` toggle) for the full per-pair readout, the facts line
(SRM p, last look, timezone, lock state), the per-metric Run buttons, the
maintenance buttons, and a read-only view of the experiment YAML.

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
| **Show YAML** | — | the experiment config as it is on disk, read-only |

**Job discipline.** `run`, `unlock` and `clean` are **one at a time** across the
whole project — press Run while one is going and the request is refused, because
they contend for the pipeline lock. `explore` is exempt (it takes no lock), and
is deduped per experiment. The job chip shows what is running; the drawer keeps
each job's log, and **Stop** terminates one. Ctrl-C on the dashboard terminates
every job it spawned — a run must not outlive the cockpit that started it.

**Editing configs is not in this version.** `Show YAML` is a read of the file
plus its path, for you to open in your editor. There is no save endpoint; the
one surface that writes a config is `abk explore`'s Apply.

## Options

```bash
abk dashboard [--select <sel>]... [--exclude <sel>]... [--window 24h|7d|30d|90d|all] \
              [--no-open] [--profile NAME]
```

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

Configs are read **once, at boot**: edit an experiment YAML and restart the
dashboard to see it. A project that has never run is fine — that is what the Run
buttons are for.

## Security: the URL is a credential

The server binds `127.0.0.1` only, and a one-shot token is generated per start.
**Every request is authorized, `GET` included** — the row reads hit your
warehouse and the boot page enumerates your experiments, so an unauthenticated
`GET` would be a real leak. The token lives in the URL, not in the page, so the
served HTML is not a credential at rest.

Consequence: anyone who has that URL can spawn `abk run` / `abk clean` on your
project. Do not paste it into a shared channel, and do not port-forward the port
to a network you do not trust.

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
- **The cockpit will not stop.** The dashboard has no terminal action by design
  — Ctrl-C is the exit, and it takes the spawned jobs down with it.

## See also

- [The explore cockpit](explore.md) — the per-experiment tuning surface the
  Explore button launches.
- [Reading a readout](reading-a-readout.md) — what the verdicts and the
  pre-horizon / insufficient / SRM states mean.
- [CLI reference](../reference/cli.md) — every command and flag.
- [Visualizing results](visualizing-results.md) — BI over `_ab_results` when
  you want dashboards that outlive a localhost session.
