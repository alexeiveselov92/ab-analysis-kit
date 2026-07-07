/*
 * sync-docs.mjs — import the repo's Markdown docs into the Starlight content
 * collection at build time, so `docs/` (and CHANGELOG.md) stay the single
 * source of truth. Run automatically by `npm run dev` / `npm run build`.
 *
 * For each page it:
 *   - injects Starlight frontmatter (title from the leading `# H1`, or an
 *     explicit override), stripping that H1 so the title is not duplicated;
 *   - rewrites internal `*.md` links (and a few directory links) to the
 *     clean routes Starlight serves.
 *
 * The output directory (src/content/docs) is fully regenerated each run and
 * is git-ignored — never edit it by hand.
 */
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const WEBSITE = path.resolve(here, '..');
const ROOT = path.resolve(WEBSITE, '..'); // repo root
const OUT = path.join(WEBSITE, 'src', 'content', 'docs');

// repo-relative source -> { dest (route-relative .md), title? }
const PAGES = [
  { src: 'docs/README.md', dest: 'overview.md', title: 'Overview' },
  { src: 'docs/getting-started/installation.md', dest: 'getting-started/installation.md' },
  { src: 'docs/getting-started/quickstart.md', dest: 'getting-started/quickstart.md' },
  { src: 'docs/guides/configuration.md', dest: 'guides/configuration.md' },
  { src: 'docs/guides/experiments.md', dest: 'guides/experiments.md' },
  { src: 'docs/guides/metrics.md', dest: 'guides/metrics.md' },
  { src: 'docs/guides/compute-methods.md', dest: 'guides/compute-methods.md' },
  { src: 'docs/guides/databases.md', dest: 'guides/databases.md' },
  { src: 'docs/guides/reading-a-readout.md', dest: 'guides/reading-a-readout.md' },
  { src: 'docs/guides/explore.md', dest: 'guides/explore.md' },
  { src: 'docs/guides/validate.md', dest: 'guides/validate.md' },
  { src: 'docs/guides/sequential.md', dest: 'guides/sequential.md' },
  { src: 'docs/guides/plan.md', dest: 'guides/plan.md' },
  { src: 'docs/guides/visualizing-results.md', dest: 'guides/visualizing-results.md' },
  { src: 'docs/reference/cli.md', dest: 'reference/cli.md' },
  { src: 'docs/reference/internal-tables.md', dest: 'reference/internal-tables.md' },
  { src: 'docs/reference/legacy-method-catalogue.md', dest: 'reference/legacy-method-catalogue.md' },
  { src: 'docs/examples/README.md', dest: 'examples.md', title: 'Examples' },
  // "Development" — sourced from the repo's own .claude/rules so the dev
  // context is single-source (in Claude Code) and rendered on the site.
  { src: '.claude/rules/architecture.md', dest: 'development/architecture.md', title: 'Architecture' },
  { src: '.claude/rules/contributing.md', dest: 'development/contributing.md', title: 'Contributing' },
  { src: 'CHANGELOG.md', dest: 'changelog.md', title: 'Changelog' },
];

const routeOf = (dest) => '/' + dest.replace(/\.md$/, '') + '/';

// repo-relative source file -> served route
const SRC_TO_ROUTE = new Map(PAGES.map((p) => [p.src, routeOf(p.dest)]));

// repo-relative directory -> served route (for bare directory links)
const DIR_TO_ROUTE = new Map([
  ['docs', '/overview/'],
  ['docs/examples', routeOf('examples.md')],
  ['docs/getting-started', '/getting-started/installation/'],
  ['docs/guides', '/guides/configuration/'],
  ['docs/reference', '/reference/cli/'],
]);

let warnings = 0;

/** Pull the first `# H1` out of the content; return { title, body }. */
function extractTitle(content, override) {
  const lines = content.split('\n');
  let title = override;
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^#\s+(.+?)\s*$/);
    if (m) {
      if (!title) title = m[1].trim();
      lines.splice(i, 1);
      if (lines[i] !== undefined && lines[i].trim() === '') lines.splice(i, 1);
      break;
    }
  }
  return { title: title || 'Untitled', body: lines.join('\n') };
}

/** Resolve one markdown link target (path[#anchor]) to a served route, or null. */
function resolveLink(target, srcRel) {
  if (/^(https?:|mailto:|tel:|#|\/)/i.test(target)) return null; // external / absolute / pure anchor
  const hashIdx = target.indexOf('#');
  const rawPath = hashIdx === -1 ? target : target.slice(0, hashIdx);
  const anchor = hashIdx === -1 ? '' : target.slice(hashIdx);
  if (rawPath === '') return null;

  const srcDir = path.posix.dirname(srcRel);
  const abs = path.posix.normalize(path.posix.join(srcDir, rawPath));

  if (/\.md$/i.test(abs)) {
    const route = SRC_TO_ROUTE.get(abs);
    return route ? route + anchor : undefined; // undefined => known-shaped but unmapped
  }
  // raw config examples (+ any self-contained demo html), shipped under /examples/
  if (/^docs\/examples\/.+\.(ya?ml|sql|json|toml|html)$/i.test(abs)) {
    return '/examples/' + path.posix.basename(abs) + anchor;
  }
  // directory link (with or without trailing slash)
  const dir = abs.replace(/\/$/, '');
  if (DIR_TO_ROUTE.has(dir)) return DIR_TO_ROUTE.get(dir) + anchor;
  if (SRC_TO_ROUTE.has(dir + '/README.md')) return SRC_TO_ROUTE.get(dir + '/README.md') + anchor;
  return undefined;
}

/** Rewrite all `](target)` links in a body. */
function rewriteLinks(body, srcRel) {
  return body.replace(/\]\(([^)]+)\)/g, (whole, inner) => {
    // split off an optional `"title"` part
    const m = inner.match(/^(\S+)(\s+.*)?$/s);
    if (!m) return whole;
    const url = m[1];
    const rest = m[2] || '';
    const resolved = resolveLink(url, srcRel);
    if (resolved === null) return whole; // leave external/absolute/anchor as-is
    if (resolved === undefined) {
      warnings++;
      console.warn(`  ! ${srcRel}: unresolved link target "${url}" (left as-is)`);
      return whole;
    }
    return `](${resolved}${rest})`;
  });
}

async function main() {
  await fs.rm(OUT, { recursive: true, force: true });
  await fs.mkdir(OUT, { recursive: true });

  for (const page of PAGES) {
    const srcAbs = path.join(ROOT, page.src);
    let raw;
    try {
      raw = await fs.readFile(srcAbs, 'utf8');
    } catch {
      throw new Error(`sync-docs: missing source file ${page.src}`);
    }
    const { title, body } = extractTitle(raw, page.title);
    const rewritten = rewriteLinks(body, page.src);
    const frontmatter = `---\ntitle: ${JSON.stringify(title)}\n---\n\n`;
    const destAbs = path.join(OUT, page.dest);
    await fs.mkdir(path.dirname(destAbs), { recursive: true });
    await fs.writeFile(destAbs, frontmatter + rewritten.replace(/^\n+/, ''), 'utf8');
  }

  // ship referenced config examples as static downloads under /examples/, FLATTENED by
  // basename to match resolveLink's `/examples/<basename>` mapping. Walk recursively so the
  // BI recipes under docs/examples/bi/ (queries.sql, srm_panel.sql, grafana_dashboard.json)
  // ship too — a non-recursive readdir dropped them and every doc link to them 404'd.
  const exDir = path.join(ROOT, 'docs', 'examples');
  const pubEx = path.join(WEBSITE, 'public', 'examples');
  await fs.rm(pubEx, { recursive: true, force: true });
  const EXAMPLE_EXT = /\.(ya?ml|sql|json|toml|html)$/i;
  async function collectExamples(dir) {
    const found = [];
    for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) found.push(...(await collectExamples(full)));
      else if (EXAMPLE_EXT.test(entry.name)) found.push(full);
    }
    return found;
  }
  const exFiles = await collectExamples(exDir);
  const seenBasenames = new Map();
  if (exFiles.length) {
    await fs.mkdir(pubEx, { recursive: true });
    for (const f of exFiles) {
      const base = path.basename(f);
      if (seenBasenames.has(base)) {
        warnings++;
        console.warn(
          `  ! example basename collision: ${path.relative(ROOT, f)} and ` +
            `${path.relative(ROOT, seenBasenames.get(base))} both map to /examples/${base}`,
        );
      }
      seenBasenames.set(base, f);
      await fs.copyFile(f, path.join(pubEx, base));
    }
  }

  console.log(
    `sync-docs: wrote ${PAGES.length} pages to src/content/docs` +
      `, copied ${exFiles.length} example asset(s) to public/examples` +
      (warnings ? ` (${warnings} link warning(s))` : ''),
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
