---
name: abk-explore
description: >-
  Interactively tune an experiment's compute method in the browser cockpit: run
  `abk run` to persist results, open `abk explore --select <exp>`, and guide the
  user to turn the method's knobs (test type, alpha, CUPED, correction, bootstrap
  iters) on their **real** cumulative series and watch the effect + CI
  stabilization recompute live — with the **A/A calibration chip always visible**
  — then **Apply** the chosen `method_params` back into the experiment YAML in
  place (validated, prior version archived). Use when the user wants to tune or
  dial in a method by hand / interactively / in the browser, explore an
  experiment's results live, compare methods on real data, adjust alpha or turn
  CUPED on, or be in the loop while deciding a method. This is the primary,
  hands-on entry point for choosing a compute method.
---

# Tune an experiment's method interactively (the cockpit)

`abk explore` opens a localhost **browser cockpit** over an experiment's
**persisted** `_ab_results` series. The user turns the compute method's knobs and
the cumulative effect, its shrinking CI, the p-value, power, and the **A/A
calibration** chip **recompute live** — through the Python `from_suffstats` path
(one source of truth for the math; no JS stats fork, no DB round-trip) — then
clicks **Apply** to write the chosen `method_params` **back into the experiment
YAML in place** (validated, with the prior version archived first).

It is the **hands-on umbrella** for choosing a method. **Prefer this skill
whenever the user wants to be in the loop** (see, judge, adjust). Your job is to
**set up the sandbox, open it, and guide the user through it** — they drive the
chart; you prepare it, explain each control in plain language, and handle the
follow-up (`abk run` / `abk clean`). This skill is the procedure; for field
detail read `.claude/rules/ab-analysis-kit/explore.md` (the full `abk explore`
reference), plus `methods.md` (the knobs) and `validate.md` (the calibration chip).

## Step 0 — An experiment with persisted results (the sandbox)

The cockpit charts an experiment's **already-computed** `_ab_results` rows, so you
need an experiment that has run at least once:

- **A project root** contains `abkit_project.yml` and `profiles.yml`. If
  `profiles.yml` is still the `abk init` placeholder, set up the DB first with
  **`abk-setup-project`**.
- **No experiment or metric yet?** Hand off to **`abk-new-experiment`** (and
  **`abk-new-metric`**) to design the config first — never fabricate YAML/SQL here.
- **Compute a series** (more cutoffs ⇒ a richer stabilization curve):

  ```bash
  abk run --select <exp>                    # load → compute → persist the series
  abk run --select <exp> --full-refresh --from 2024-06-01 --to 2024-06-30  # recompute a bounded window
  ```

  `--from`/`--to` **only** apply with `--full-refresh`, and `--full-refresh`
  needs **both** bounds (a bare `--from` is rejected).

  `abk explore` on a never-run experiment prints a friendly noop ("run `abk run
  --select <exp>` first") and exits — it never runs the pipeline itself.

## Step 1 — Open the cockpit

```bash
abk explore --select <exp>              # one experiment; all its comparisons
abk explore --select <exp> --metric <m> # focus a single metric comparison
```

`--select` must resolve to a **single** experiment (it errors and names the
collision otherwise). This starts a `127.0.0.1` server and opens the browser.
Useful flags:

- **Remote / headless machine** — add `--no-open` and share the printed URL (the
  server is localhost-only; the user reaches it via a tunnel/port-forward).
- **No write-back / share a snapshot** — `--no-serve` writes a static read-only
  HTML preview (`reports/<exp>__explore.html`) instead: the knobs still recompute,
  but there is **no Apply** and **no Auto mode** (both need the live server).

On startup the cockpit warns if a metric has **more than one `method_config_id`**
in `_ab_results` (orphaned series from an earlier method edit — the BI chart will
show duplicate stabilization lines); note it and offer `abk clean`. Tell the user
plainly: *"I've opened an interactive chart of your experiment — turn the knobs
and watch the effect and its CI; tell me what you see and I'll help you decide."*

## Step 2 — Orient the user to the cockpit

- **Windshield:** the cumulative **effect + CI stabilization chart** (the estimated
  lift with its shrinking confidence interval as exposure accrues over the
  experiment's cutoffs), with pinned live chips: estimated lift, CI half-width,
  p-value, current power, the **A/A calibration (real α)** chip, and the **SRM
  flag**.
- **Side rail (mode-aware, Basic/Advanced disclosure):** the method's knobs, auto-
  derived from the method's param specs. Every change live-recomputes.
- **The verdict** reads WIN / LOSE / FLAT / INCONCLUSIVE (or blocked on SRM /
  insufficient data) from the current knobs and window.

**Check SRM first.** If the SRM chip is red, the arm split doesn't match the
assigned ratio — the randomization or the cohort query is broken, and abkit blocks
the decision. A significant effect on top of an SRM failure is not trustworthy;
fix the assignment before tuning anything else.

## Step 3 — Turn the knobs

The rail defaults to **Basic** and reveals the rest under **Advanced**:

- **Basic** — the **method picker** (choose the `cuped-t-test` variant to turn on
  CUPED), `test_type` (`relative` vs `absolute` effect), and **alpha**. These cover
  the median analyst.
- **Advanced** — the CUPED **covariate + lookback** (`covariate_lookback`, a whole-
  day window like `14d`), **stratification keys**, **bootstrap iterations**
  (`n_samples` for a bootstrap method), the multiple-comparison **correction**
  (Bonferroni two-tier), and the analysis unit (preview-only).

Explain the trade-offs in plain language as they turn each one: a bigger alpha
widens what counts as significant (and raises the false-positive rate — watch the
calibration chip); CUPED shrinks variance using a pre-period covariate (tighter CI,
often the biggest win on a noisy metric); more bootstrap iterations steady a
resampled estimate at compute cost. The recompute is a faithful **approximation**
of what the next `abk run` will persist.

Two facts to keep straight while tuning:

- **Editing an identity param starts a NEW series.** `method_config_id` is a hash of
  the method + its non-default **identity** params; changing `test_type`, CUPED, the
  lookback, or strata **orphans** the prior `_ab_results` rows.
  `seed` is identity-**excluded**, so changing a bootstrap seed does *not* orphan.
  The **correction** and **alpha** are experiment-level (never in `method_config_id`):
  changing them re-derives the effective alpha and re-arms the calibration chip but
  does **not** orphan the series.
- **Sequential is not a rail knob.** Peeking-safe (always-valid) CIs are the
  experiment-level opt-in `sequential: {enabled: true}` in the YAML, not a method
  param. Without it the readout **withholds** WIN/LOSE and FLAT before the planned horizon
  (fixed-horizon CIs aren't peeking-valid). If the user is reading the daily series
  early and wants an early call, point them at that toggle (and the weekly-cycle
  chip) — not at loosening alpha.

## Step 4 — Keep the calibration chip visible (and green it)

The **A/A calibration** chip is always on the windshield — it is the honest
answer to *"does this method actually hold its false-positive rate on **this**
data?"* It shows the real α (single-look FPR) and the honest **peeking** FPR from
`abk validate`'s placebo A/A splits, versus the metric's budget. Until a matching
A/A run exists it reads **uncalibrated**.

- **Auto mode** (built in) runs a reduced-N `abk validate` **server-side** and
  greens the chip **in place**, without leaving the page — the fastest way to
  calibrate the exact knobs on screen.
- For the full matrix (more iterations, power, achieved-MDE, coverage, the peeking
  curve), hand off to the **`abk-validate`** skill; it persists `_ab_aa_runs` and
  lights this same chip on the next open.

Calibration keys by **(metric, method_config_id, effective alpha)**, so it re-arms
whenever the user changes an identity knob or alpha — that is by design.

## Step 5 — Apply the config back to the YAML

When the user is happy, they click **Apply**. abkit then, in order: **validates**
the prospective config (a bad config is rejected and **nothing is written**),
**archives** the current experiment YAML verbatim under
`experiments/.history/<exp>/`, and **re-emits** the experiment in place, **merging**
the tuned knobs into the affected comparison(s) — every other comparison and
experiment-level field is kept **verbatim**.

**The calibration gate:** if the applied knobs have **not** passed `validate`
(uncalibrated chip), Apply is confirmed, not silently allowed — the user must
acknowledge (`confirm_uncalibrated`) so a mis-calibrated method never ships
unseen. Encourage a `validate` / Auto pass first instead of confirming past a red
chip. Apply ends the session and **never auto-runs** the pipeline.

## Step 6 — Recompute under the new config, prune orphans

The live preview is an approximation — the **next `abk run` is the source of
truth**. Because the identity knobs changed, results recompute under a new
`method_config_id` and the old rows are orphaned:

```bash
abk run --select <exp>              # load → compute → readout under the new config
abk run --select <exp> --report     # optional: a self-contained HTML readout to confirm
abk clean --select <exp>            # dry-run: shows the orphaned old-config rows
abk clean --select <exp> --execute  # prune them (add --yes to skip the prompt)
```

`abk clean` is **dry-run by default**; nothing is deleted until `--execute`.

## When to hand off instead

- **Is it calibrated? / the full A/A matrix** → the **`abk-validate`** skill.
- **Size it before it launches** (required-N / achievable-MDE / power) → **`abk-plan`**.
- **No experiment or metric yet** → **`abk-new-experiment`** / **`abk-new-metric`**.
- **DB not connected** → **`abk-setup-project`**.

## Final checklist — verify before declaring done

- [ ] The experiment resolves to a **single** selector and has persisted
      `_ab_results` rows (ran `abk run`; recomputed a bounded window with
      `--full-refresh --from <start> --to <end>` if the chart was thin).
- [ ] You opened `abk explore` (with `--no-open` + shared URL on a remote machine)
      and told the user it's an interactive chart they drive.
- [ ] You checked the **SRM chip** and explained the Basic/Advanced knobs and which
      ones orphan the series (identity params) vs which don't (`seed`).
- [ ] The **calibration chip** was addressed — Auto mode or `abk validate` run, or
      the `confirm_uncalibrated` cost made explicit before Apply.
- [ ] After **Apply**, you ran `abk run` to recompute and `abk clean --execute` to
      prune the orphaned old-config rows.
