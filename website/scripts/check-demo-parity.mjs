/*
 * check-demo-parity.mjs — prove the TS stabilization-demo math matches abkit.stats.
 *
 * The landing demo re-implements abkit's cumulative-effect computation in
 * TypeScript (src/scripts/demo/stats.ts): it folds per-day sufficient statistics
 * with abkit's Chan/Welford merge and reduces them to an absolute two-sample
 * effect + Normal confidence interval per day. This script is the golden-parity
 * gate: it reads the frozen Python output (src/scripts/demo/golden.json, produced
 * by website/scripts/gen-demo-golden.py), bundles stats.ts with esbuild, runs its
 * `runCumulative` over each case's frozen daily suffstats, and asserts every
 * per-day point reproduces the real abkit.stats output within a 1e-6 relative
 * tolerance.
 *
 *   node scripts/check-demo-parity.mjs        (or: npm run check:demo-parity)
 *
 * Per point we assert: `scored` matches; `n1`/`n2` match exactly (integers);
 * when scored, `effect`/`lo`/`hi`/`p` are within (1e-9 + 1e-6·|expected|) and
 * `reject` matches. `reject` is derived on both sides from the CI excluding zero
 * (algebraically identical to abkit's `pvalue < alpha` at the same critical z),
 * so a mismatch only within 1e-6 of alpha at the true boundary is tolerated.
 * Prints a per-case PASS/FAIL summary and exits non-zero on any mismatch, so it
 * fits a CI gate.
 *
 * No new dependencies: esbuild ships transitively with astro/vite, and the
 * bundle is loaded from a temp .mjs file (portable across Node versions).
 */
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = path.dirname(fileURLToPath(import.meta.url));
const WEBSITE = path.resolve(here, '..');
const STATS_TS = path.join(WEBSITE, 'src', 'scripts', 'demo', 'stats.ts');
const GOLDEN_JSON = path.join(WEBSITE, 'src', 'scripts', 'demo', 'golden.json');

const RTOL = 1e-6;
const ATOL = 1e-9;

/** Bundle stats.ts to ESM and dynamically import its `runCumulative`. */
async function loadRunCumulative() {
  const result = await build({
    entryPoints: [STATS_TS],
    bundle: true,
    write: false,
    format: 'esm',
    platform: 'node',
    target: 'node18',
    logLevel: 'silent',
  });
  const code = result.outputFiles[0].text;

  const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'abk-demo-parity-'));
  const tmpFile = path.join(tmpDir, 'stats.bundle.mjs');
  await fs.writeFile(tmpFile, code, 'utf8');
  try {
    const mod = await import(pathToFileURL(tmpFile).href);
    if (typeof mod.runCumulative !== 'function') {
      throw new Error('stats.ts does not export a `runCumulative` function');
    }
    return mod.runCumulative;
  } finally {
    await fs.rm(tmpDir, { recursive: true, force: true });
  }
}

/** True when got ≈ exp within (ATOL + RTOL·|exp|). */
function close(got, exp) {
  return Math.abs(got - exp) <= ATOL + RTOL * Math.abs(exp);
}

/** Compare a TS CumulativePoint[] against the golden expected[] for one case. */
function checkCase(config, got, expected) {
  const mismatches = [];

  if (got.length !== expected.length) {
    mismatches.push(`point count mismatch: got ${got.length}, expected ${expected.length}`);
    return mismatches;
  }

  for (let i = 0; i < expected.length; i++) {
    const e = expected[i];
    const g = got[i];

    if (g.ed !== e.ed) {
      mismatches.push(`idx ${i}: ed mismatch (got ${g.ed}, expected ${e.ed})`);
    }
    if (g.n1 !== e.n1 || g.n2 !== e.n2) {
      mismatches.push(
        `idx ${i}: N mismatch (got ${g.n1}/${g.n2}, expected ${e.n1}/${e.n2})`,
      );
    }
    if (Boolean(g.scored) !== Boolean(e.scored)) {
      mismatches.push(`idx ${i}: scored mismatch (got ${g.scored}, expected ${e.scored})`);
      continue; // band fields are meaningless if scored disagrees
    }
    if (!e.scored) continue; // degenerate point: no interval to compare

    for (const key of ['effect', 'lo', 'hi', 'p']) {
      const gv = g[key];
      const ev = e[key];
      if (gv == null || ev == null) {
        if (gv !== ev) {
          mismatches.push(`idx ${i}: ${key} null mismatch (got ${gv}, expected ${ev})`);
        }
        continue;
      }
      if (!close(gv, ev)) {
        const d = Math.abs(gv - ev);
        mismatches.push(
          `idx ${i}: ${key} off by ${d.toExponential(3)} (got ${gv}, expected ${ev})`,
        );
      }
    }

    if (Boolean(g.reject) !== Boolean(e.reject)) {
      // `reject` == "CI excludes zero" on both sides; a legitimate float-boundary
      // flip can only happen when p sits within round-off of alpha. Tolerate that
      // knife-edge; hard-fail otherwise.
      const nearBoundary = e.p != null && Math.abs(e.p - config.alpha) < 1e-6;
      if (!nearBoundary) {
        mismatches.push(
          `idx ${i}: reject mismatch (got ${g.reject}, expected ${e.reject}; ` +
            `p=${e.p}, alpha=${config.alpha})`,
        );
      }
    }
  }

  return mismatches;
}

async function main() {
  let golden;
  try {
    golden = JSON.parse(await fs.readFile(GOLDEN_JSON, 'utf8'));
  } catch (err) {
    console.error(
      `check-demo-parity: cannot read ${path.relative(WEBSITE, GOLDEN_JSON)} ` +
        `— run \`.venv/bin/python website/scripts/gen-demo-golden.py\` first.\n${err.message}`,
    );
    process.exit(1);
  }

  const cases = golden.cases || [];
  if (cases.length === 0) {
    console.error('check-demo-parity: golden.json has no cases');
    process.exit(1);
  }

  const runCumulative = await loadRunCumulative();

  let failed = 0;
  const MAX_SHOWN = 8; // cap noise per failing case

  for (const c of cases) {
    let mismatches;
    try {
      const got = runCumulative(c.days, c.config);
      mismatches = checkCase(c.config, got, c.expected);
    } catch (err) {
      mismatches = [`runCumulative threw: ${err.stack || err.message}`];
    }

    if (mismatches.length === 0) {
      console.log(`PASS  ${c.name}  (${c.expected.length} points)`);
    } else {
      failed++;
      console.log(`FAIL  ${c.name}  (${mismatches.length} mismatch(es))`);
      for (const m of mismatches.slice(0, MAX_SHOWN)) console.log(`        ${m}`);
      if (mismatches.length > MAX_SHOWN) {
        console.log(`        … and ${mismatches.length - MAX_SHOWN} more`);
      }
    }
  }

  console.log('');
  if (failed > 0) {
    console.log(`check-demo-parity: ${failed}/${cases.length} case(s) FAILED`);
    process.exit(1);
  }
  console.log(`check-demo-parity: all ${cases.length} case(s) PASSED`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
