# abkit design — source of truth

Finalized brand + product designs, authored in **Claude Design** against the shipped abkit
library (delivered 2026-07-07). These are the canonical reference for the M6 brand-token
layer (WP7), the report/explore surface pass, and `abk test-report` channel branding — they
supersede the plan's earlier "ship on neutral placeholders indefinitely" posture
([m6-implementation-plan.md §8.1](../specs/m6-implementation-plan.md)).

## What's here

| Path | What |
|---|---|
| [`brand-tokens.md`](brand-tokens.md) | **The distilled, machine-usable token spec** — palette (light+dark), type, chart + verdict tokens, logo. The canonical source for `brand.css` / `TOKEN_FALLBACKS`. |
| [`logo/`](logo/) | Extracted logo assets — `logomark.svg`, `logomark-mono.svg`, `favicon.svg`. |
| [`mockups/brand.html`](mockups/brand.html) | Full brand system: logo concepts, primary mark, color directions, tokens, typography, the signature stabilization chart, verdict states. |
| [`mockups/landing.html`](mockups/landing.html) | The marketing landing design → informs `website/src/pages/index.astro` (WP7). |
| [`mockups/report.html`](mockups/report.html) | The `abk run --report` readout design → informs `web/src/report/**`. |
| [`mockups/explore-cockpit.html`](mockups/explore-cockpit.html) | The `abk explore` cockpit design → informs `web/src/explore/**`. |

## Notes

- The `mockups/*.html` are **Claude Design exports**: they use the Claude Design runtime
  (`<x-dc>`, `<sc-for>`, `{{ … }}` bindings, `./support.js`), so the interactive/templated
  parts (e.g. the verdict-state switcher) only render inside Claude Design; the static
  sections render standalone in any browser. They are kept as faithful reference, not as
  buildable site source.
- **Consumers must reproduce, not import, these files.** The self-contained `report.js` /
  `explore.js` bundles and the landing demo stay framework-free IIFEs with **no external
  host** (invariant 6 + the CSP rule) — pull the *tokens* from `brand-tokens.md`, not the
  mockup HTML. All colors go through the `--abk-*` token layer; the WP7 CI name+value gate
  keeps `brand.css` ↔ `TOKEN_FALLBACKS` in lockstep.
- **Accessibility is a drop-in acceptance criterion** (branding-and-site §3): the palette
  must pass WCAG-AA contrast in both themes, and chart/A-A-matrix colors must be CVD-
  distinguishable. Verify against the real hex here at WP7, not deferred.
