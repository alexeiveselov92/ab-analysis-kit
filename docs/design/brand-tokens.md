# abkit brand tokens — the finalized design source-of-truth

> Distilled from the Claude Design deliverables in [`mockups/`](mockups/) (delivered
> 2026-07-07). **This file is the canonical token source** for `website/src/styles/brand.css`,
> `web/src/shared/chart.ts` `TOKEN_FALLBACKS`, the self-contained `report.js`/`explore.js`
> surfaces, and the `abk test-report` channel branding. When those consumers are built (WP7 +
> the report/explore surface pass), their `--abk-*` values must match the hex here; the CI
> name+value gate (WP7) pins the two token layers in lockstep.

## Positioning (do not drift)

abkit is **detectkit's sibling**: *same* warm paper, ink, and type — **only the accent hue
moves**, so the two libraries read as a family. The abkit accent is **Iris Violet
`#6A45C4`** — a statistics/experiment hue, distinct from detectkit's clay and safely apart
from the green/red verdict colors. Principles: **Honest · Calm · Legible · Convergent.**
Tagline: *"Call it once it stabilizes."*

## Type

| Role | Family | Weights | Source |
|---|---|---|---|
| UI · headings · body | **Schibsted Grotesk** (`system-ui, sans-serif` fallback) | 400 / 500 / 600 / 700 / 800 | Google Fonts |
| Stats · code · CLI · numeric | **JetBrains Mono** (`monospace` fallback) | 400 / 500 / 600 / 700 | Google Fonts |

Google Fonts URL (site chrome only — the self-contained bundles must **self-host or fall
back**, never fetch an external host):
`https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap`

## Palette — light (default)

Brand & neutrals (the "warm bones", carried over from detectkit):

| Token | Hex | Role |
|---|---|---|
| `--iris` | `#6A45C4` | primary accent · cumulative-effect line · CI band (@ 14%) · links |
| `--iris-700` | `#4F2F9E` | iris deep · hover/pressed · link:hover |
| `--iris-tint` | `#6A45C4` @ 14% | CI-band fill / selected-tint |
| `--ink` | `#1B1916` | primary text · dark tiles |
| `--muted` | `#6E675B` | secondary text |
| `--subtle` | `#9A9384` | tertiary / captions / axis labels |
| `--paper` | `#F5F1E8` | page background |
| `--surface` | `#FBF9F3` | card / elevated surface |
| `--border` | `#E6E0D4` | hairline borders / grid major |
| `--grid` | `#EEE9DE` | grid minor |
| `--zero-line` | `#B7AF9E` | chart "zero / no effect" dashed hairline |

Verdict semantics (**every readout, chart and cockpit reads from exactly these five**):

| Token | Hex | Verdict | Meaning |
|---|---|---|---|
| `--win` | `#1E9E6A` | **WIN** | significant lift (also the decision-horizon marker) |
| `--lose` | `#D6453D` | **LOSE** | significant harm |
| `--flat` | `#7A8595` | **FLAT** | no detectable effect |
| `--inconclusive` | `#E0A23B` | **INCONCLUSIVE** | underpowered / still converging |
| `--srm` | `#B23A6B` | **SRM** | sample-ratio gate failed, results withheld |

## Palette — dark (inverse chrome, CLI, chat cards)

Derived from the dark surfaces used throughout the mockups (brand §03/§05, report/explore).
Refine + WCAG-AA-verify at build time (WP7 acceptance criterion).

| Token | Hex | Role |
|---|---|---|
| `--ink` (bg) | `#1B1916` | dark background |
| surface-raised | `#211E1A` | raised dark card |
| border-dark | `#332F29` | hairline on dark |
| fg | `#FBF9F3` | text on dark |
| muted-dark | `#7C766A` | secondary text on dark |
| iris-on-dark | `#C9A6F0` | iris, lightened for AA on ink (accent text/values) |
| iris-alt-dark | `#8E76E0` | secondary iris on dark (e.g. bar B) |
| win-on-dark | `#6FCB8E` | win, lightened for AA on ink |
| info-on-dark | `#9FB8C4` | method/label accent on dark |

## The signature chart (the stabilization view)

The product's signature: cumulative effect + CI, one point/day, watched converging past the
decision horizon. Token mapping (see `mockups/brand.html` §06):

- **effect line** = `--iris`, 3px, round caps/joins; end-point dot `--iris` r≈5 + halo @ 18%.
- **CI band** = `--iris` fill @ **14% opacity** (the band tightening *is the point* — always draw it).
- **zero line** = `--zero-line` `#B7AF9E`, 1.5px, dashed `4 4`.
- **decision horizon** = `--win` `#1E9E6A`, 1.5px, dashed `3 4`, labelled "horizon".
- **grid** = major `--border`, minor `--grid`.
- verdict-colored headline number + badge from `--win`/`--lose`/`--flat`/`--inconclusive`.

## Logo

"**Diverge**" (chosen of 5 concepts): rounded-square tile (`rx = 26/100`), Iris fill, one
node → two arms; treatment climbs to the accented effect point (dot), control holds baseline.
Same tile geometry as detectkit's spike; different story inside. Legible to **24px** (the
`abk test-report` chat-avatar size). Assets:

- [`logo/logomark.svg`](logo/logomark.svg) — primary (Iris tile, paper strokes).
- [`logo/logomark-mono.svg`](logo/logomark-mono.svg) — ink tile (single-colour / dark chrome).
- [`logo/favicon.svg`](logo/favicon.svg) — favicon (heavier strokes for small sizes).
- Wordmark: `abkit` (Schibsted Grotesk 700) or `ab`(ink)+`kit`(`--subtle`); CLI short mark `abk` (JetBrains Mono 700).

## DB badge colors (carried from detectkit, verbatim)

PostgreSQL `#336791` · MySQL `#00758F` · ClickHouse (yellow/black per legacy `DbBadges`).
