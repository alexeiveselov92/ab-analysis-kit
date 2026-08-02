---
name: abk-web-bundle
description: >-
  Change one of ab-analysis-kit's three browser renderers (report, explore,
  dashboard) under the committed-bundle discipline: edit `web/src/**`, rebuild,
  commit the regenerated `abkit/*/assets/*.js` in the SAME PR, and satisfy the
  build-time and CI gates (globals, §4 marker classes, the script-tokenizer
  hazard scan, brand-token lockstep, jsdom smoke). Use whenever the task touches
  the HTML report, the explore cockpit's page, the dashboard page, the shared
  chart/logo code, or the page shells in `html_report.py` / `tuning/html.py`.
---

# Change a renderer, ship its bundle

`web/` is a **dev-only** TypeScript toolchain that never ships in the wheel.
What ships is its output: three committed, minified IIFE bundles inlined
verbatim into the page shells by Python.

| Source | Committed artifact | Global it assigns |
|---|---|---|
| `web/src/report/` | `abkit/reporting/assets/report.js` | `__ABK_REPORT__` |
| `web/src/explore/` | `abkit/tuning/assets/explore.js` | `__ABK_EXPLORE__` |
| `web/src/dashboard/` | `abkit/tuning/assets/dashboard.js` | `__ABK_DASHBOARD__` |

`web/src/shared/` (`chart.ts`, `logo.ts`, `payload.ts`) is shared — **an edit
there rebuilds all three**, and can also red the `website` CI job, because the
landing page's demo imports `chart.ts`.

## The loop

```bash
cd web && npm run check && npm run build && npm test
```

- `npm run check` — `tsc --noEmit`, strict, with `noUnusedLocals` /
  `noUnusedParameters`, and it **includes `test/**/*.mjs`** (`checkJs: true`),
  so a stale test fixture is a type error.
- `npm run build` — `node build.mjs`.
- `npm test` — `pretest` rebuilds, then jsdom over the **committed** bundle.

Then commit the artifact **in the same PR**:

```bash
git status --porcelain -- ':(glob)abkit/*/assets/**'
```

The `:(glob)` pathspec magic is mandatory — CI's freshness gate uses it, and a
plain `abkit/*/assets/` matches nothing and silently passes.

## Gates that fail the build before it writes

`build.mjs` refuses to emit when:

1. the expected **global** is missing from the minified code;
2. any **§4 marker class** is missing — `abk-prehorizon`, `abk-insufficient`,
   `abk-srm-fail`. They are the machine-checkable peeking-honesty markers; a
   minifier that ate one would silently drop a disclosure;
3. the bundle contains **`</script` or `<!--`**. The Python bake inlines the
   bundle verbatim into `<script>…</script>` (only the payload slot is
   `<`-escaped), so either sequence terminates the inline script early and kills
   the whole page. This bites help text, placeholders and YAML examples — the
   dashboard's editor template is exactly the shape that could reintroduce it.

## CI gates on top

| Job step | What it enforces |
|---|---|
| Committed assets are fresh | rebuilding must produce no diff |
| Marker classes present in committed bundles | greps every `abkit/*/assets/*.js` |
| Python-side placeholder hexes stay inside the brand-token layer | every `#rrggbb` in `html_report.py` / `tuning/html.py` must appear in `web/src/shared/chart.ts` — **no new hex in a page shell** |
| brand.css and TOKEN_FALLBACKS stay in token lockstep | name parity both ways plus value parity between `TOKEN_FALLBACKS` and `website/src/styles/brand.css`; a new token means editing **both** |
| jsdom smoke suite over the committed bundle | `npm test --workspace web` |

## Styling rules

Every colour goes through `TOKEN_FALLBACKS` — the one brand-token layer. Never
write a hex in a renderer or a page shell. The dashboard bundle historically had
**no form-control CSS** (it was buttons and text only); the input/textarea rules
live in `explore.ts` and are **copied**, not imported — nothing is shared
between renderers except `chart.ts` and `logo.ts`.

## Writing the smoke test

`web/test/smoke-*.mjs` runs jsdom over the committed bundle. The house pattern:

- fixtures live in `web/test/fixtures-*.mjs` and are **type-checked** against
  `src/*/payload.ts`, so a new wire shape gets its interface first;
- register any new route in the test's `defaultHandler` so existing tests keep
  passing;
- drive real DOM events, and find buttons by **exact `textContent`** — button
  labels are part of the test contract;
- assert on the recorded calls (`path`, `method`, `body`, `query.get('token')`),
  never on internals;
- gate every wait behind the `until(...)` poller — **never a bare timing
  assertion**;
- close every window in `afterEach`: the clients poll forever and `node --test`
  would never exit.

**jsdom has no canvas 2D context and no layout.** Anything visual — a sparkline,
the stabilization chart, a modal's focus trap, `ResizeObserver` — is *not*
covered by this suite. Say so rather than implying coverage; a real-browser
check is a manual step (or a Playwright MCP, if one is connected).

## Wiring a NEW bundle

Two hand-maintained namelists, neither derived from the build config:

1. `.github/workflows/ci.yml` — the wheel gate's bundle tuple;
2. `tests/e2e/test_release_readiness.py` — the self-contained-bundles check.

Plus a `build.mjs` spec entry. Packaging itself is glob-based
(`pyproject.toml` `"abkit.tuning" = ["assets/*.js"]`, `MANIFEST.in`) and needs
no edit.

A missing bundle at bake time must **raise**, never degrade to a "run npm build"
note — a `pip install` user cannot act on that note.
