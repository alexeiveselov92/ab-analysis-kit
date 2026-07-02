# Branding & site

> The docs/landing site is **structurally analogous to detectkit's** (same tech,
> same build/deploy shape, same single-source docs pipeline) but with **our own
> palette, our own logo, and our own landing page content**. The visual design
> (palette + logo) is finalized separately in Claude design; this spec pins what
> stays the same, what differs, and how to keep interface styling swappable so the
> brand can drop in without a rewrite.

## 1. Same as detectkit (reuse the machinery)

- **Astro static site** under `website/` (mirrors detectkit's `website/`):
  `astro.config.mjs`, `package.json`, `src/pages/`, `src/components/`,
  `src/styles/`, `src/scripts/`, `public/`.
- **Single-source docs.** One Markdown body of domain truth renders three ways:
  the published site (`abkit.pipelab.dev`), the `.claude/rules` shipped by
  `abk init-claude`, and `docs/`. A `website/scripts/sync-docs.mjs` step keeps them
  in lockstep (detectkit pattern). ([cli-and-dx.md §5](cli-and-dx.md))
- **Framework-free renderer core shared with the product.** The landing's
  interactive demo, the `abk run --report` HTML, and the `abk explore` cockpit all
  render through the **same** dependency-free TS/JS core
  (`website/src/scripts/core/` → committed `assets/*.js` bundles inlined into
  self-contained HTML). This is what makes the explore cockpit "app-seed-shaped".
- **Deploy shape.** `website/Dockerfile` + `nginx.conf` → image on GHCR via
  `.github/workflows/website.yml` → a dispatch tells the pipelab infra repo to
  redeploy `abkit.pipelab.dev` (identical to detectkit's flow, renamed).
- **Docs domain:** `abkit.pipelab.dev` (sibling of `dtk.pipelab.dev`).

## 2. Different from detectkit (our brand)

- **Palette.** A distinct color scheme (detectkit's accent is a warm rust
  `#d15b36`/`--st-*` token family; ours will differ). Finalized in Claude design.
- **Logo / lockup.** Our own logo + light/dark lockups (`assets/`, `public/favicon`,
  the site header). Finalized in Claude design.
- **Landing page.** Our **own landing page content** — the hero, the section
  narrative ("slides"), the interactive demo framing, and the copy are written for
  A/B analysis (experiment → stabilization chart → decision), not monitoring. Not a
  reskin of detectkit's landing; a new page that happens to share the components.
- **Interactive demo.** The landing demo is our own: instead of an anomaly band on a
  time series, it shows the **cumulative effect + CI stabilization** converging as
  synthetic sample accrues (the product's signature chart).

## 3. Themeable interfaces (make the palette a drop-in)

So the brand (once designed) applies everywhere without touching logic:

- **One brand-token layer.** All colors/spacing/typography live in CSS custom
  properties in a single `src/styles/brand.css` (detectkit's `--st-*` token
  pattern). The site, the `run --report` HTML, and the `explore` cockpit read the
  **same** tokens, so swapping the palette is editing one file.
- **Semantic status tokens.** Keep the status-color contract semantic, not
  hard-coded: WIN/significant, LOSE/harmful, FLAT/neutral, INCONCLUSIVE, SRM-failed
  — each a named token so the design can recolor them and every surface follows.
- **Light + dark** parity from day one (both the site and the self-contained HTML
  reports), driven by the token layer.
- **Accessibility.** Palette choices must pass contrast (WCAG AA) in both themes;
  the chart's effect/CI/zero-line/SRM colors must be distinguishable for common
  color-vision deficiencies (the A/A matrix in particular color-codes FPR vs
  budget — see [aa-false-positive-matrix.md §4](aa-false-positive-matrix.md)).

## 4. Where the brand shows up (surfaces to keep on the token layer)

1. The docs/landing site (`abkit.pipelab.dev`).
2. `abk run --report` self-contained HTML readouts.
3. The `abk explore` cockpit.
4. The logo/avatar used by `abk test-report` notification channels (branding
   parity with detectkit's channel branding).

## 5. Timing

The site + branding land in **M6** ([ROADMAP.md](../../ROADMAP.md)). Until then:
keep every interface on the semantic token layer with **placeholder** values, so
the finalized palette + logo from Claude design drop straight in. Do not hard-code
brand colors anywhere in product code.
