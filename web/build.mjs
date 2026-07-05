/*
 * build.mjs — bundle abkit's committed browser assets.
 *
 * Bundles each entry into a single minified IIFE (es2019, browser, no
 * externals) and writes it to the committed, wheel-packaged asset path.
 * The Python side inlines these assets into self-contained HTML documents
 * (abkit/reporting/html_report.py); at load the report bundle assigns
 * `window.__ABK_REPORT__ = { render }`.
 *
 *   node build.mjs          (or: npm run build)
 *
 * Every bundle is gated before it is written (m3-implementation-plan.md D7 /
 * WP3): the window-global assignment must be present (the donor's
 * gen-report-bundle.mjs:48-51 assertion) and the peeking-honesty marker
 * classes must survive minification so WP10 and the CI freshness job can
 * assert them in the committed artifact.
 */
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const here = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(here, '..');

const BUNDLES = [
  {
    entry: path.join(here, 'src', 'report', 'report.ts'),
    outFile: path.join(REPO, 'abkit', 'reporting', 'assets', 'report.js'),
    global: '__ABK_REPORT__',
    // stable machine-checkable peeking-honesty markers (data-contract §4)
    markers: ['abk-prehorizon', 'abk-insufficient', 'abk-srm-fail'],
  },
  {
    entry: path.join(here, 'src', 'explore', 'explore.ts'),
    outFile: path.join(REPO, 'abkit', 'tuning', 'assets', 'explore.js'),
    global: '__ABK_EXPLORE__',
    // the same §4 peeking-honesty markers as the report bundle — the CI
    // marker step greps every abkit/*/assets/*.js for all three
    markers: ['abk-prehorizon', 'abk-insufficient', 'abk-srm-fail'],
  },
];

/**
 * @param {{entry: string, outFile: string, global: string, markers: string[]}} spec
 */
async function bundle({ entry, outFile, global: globalName, markers }) {
  const result = await build({
    entryPoints: [entry],
    bundle: true,
    write: false,
    format: 'iife',
    platform: 'browser',
    target: 'es2019',
    minify: true,
    legalComments: 'none',
    logLevel: 'info',
  });

  const code = result.outputFiles[0].text;
  const missing = [globalName, ...markers].filter((m) => !code.includes(m));
  if (missing.length > 0) {
    console.error(`build: ${path.relative(REPO, outFile)} is missing required markers: ${missing.join(', ')}`);
    process.exit(1);
  }
  // The bundle is inlined VERBATIM into <script>…</script> by the Python
  // bake (only the payload slot is <-escaped): a "</script" or "<!--" inside
  // any bundle string would terminate the inline script early (or enter the
  // tokenizer's double-escaped state) and kill the whole page — gate it here,
  // where the offending source line is one grep away.
  const hazard = /<\/script|<!--/i.exec(code);
  if (hazard) {
    console.error(
      `build: ${path.relative(REPO, outFile)} contains the script-tokenizer hazard ` +
        `sequence ${JSON.stringify(hazard[0])} — inline <script> baking would break`,
    );
    process.exit(1);
  }

  await fs.mkdir(path.dirname(outFile), { recursive: true });
  await fs.writeFile(outFile, code, 'utf8');
  const kb = (Buffer.byteLength(code, 'utf8') / 1024).toFixed(1);
  console.log(`build: wrote ${path.relative(REPO, outFile)} (${kb} KB)`);
}

for (const spec of BUNDLES) {
  await bundle(spec);
}
