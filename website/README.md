# abkit website

Marketing landing + documentation site for **abkit**, served at
[abkit.pipelab.dev](https://abkit.pipelab.dev).

Built with [Astro](https://astro.build) + [Starlight](https://starlight.astro.build).
The brand system is defined in [`docs/design/brand-tokens.md`](../docs/design/brand-tokens.md);
the theme tokens are mirrored in [`src/styles/brand.css`](./src/styles/brand.css) using the
same `--abk-*` names as `web/src/shared/chart.ts` `TOKEN_FALLBACKS`.

## Workspace note

This site is a member of the repo-root npm workspace (`ab-analysis-kit-monorepo`,
`workspaces: ["web", "website"]`). The **lockfile lives at the repo root** — run
`npm install` from the root, not from here. The sibling workspace `web/` (package
`abkit-web`) owns the framework-free renderer core (`web/src/shared/chart.ts`); the
landing demo consumes it through the workspace rather than re-porting the canvas.

## How docs get here

`docs/*.md`, `.claude/rules/{architecture,contributing}.md`, and `CHANGELOG.md` stay the
**single source of truth**. At dev/build time, [`scripts/sync-docs.mjs`](./scripts/sync-docs.mjs)
imports them into `src/content/docs/` (git-ignored), injecting Starlight frontmatter (title
from the leading `# H1`) and rewriting cross-`.md` links to clean routes. Referenced config
examples (`docs/examples/*.{yml,sql,json,toml,html}`) are copied to `public/examples/` as
downloads.

**Edit the docs in `docs/`, never in `website/src/content/docs/`.** To add or move a page,
update the `PAGES` map in `scripts/sync-docs.mjs` and the `sidebar` in
[`astro.config.mjs`](./astro.config.mjs).

## Develop

```bash
# from the repo root (workspace install):
npm install

cd website
npm run dev      # runs sync-docs, then astro dev  → http://localhost:4321
```

## Build

```bash
npm run build    # runs sync-docs, then astro build → ./dist (static)
npm run preview  # serve ./dist locally
npm run check    # astro type-check
```

The build is fully static — copy `website/dist/` to any static host / nginx root
(`nginx.conf` is the reference server config). The container image + live deploy are held
for the M6 G2 release gate.
