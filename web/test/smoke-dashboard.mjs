// @ts-nocheck — assertion-style test code; the payload fixtures live in
// fixtures-dashboard.mjs, which IS checked against the lockstep contract.
// jsdom smoke test for the committed dashboard bundle.
//
// Loads abkit/tuning/assets/dashboard.js (the COMMITTED artifact — `npm test`
// rebuilds first, and CI diffs the rebuild before running this) into a jsdom
// window whose URL carries a ?token=, then drives the client against a fake
// window.fetch that records every call and can hold replies open. What it pins:
// the metadata-only boot render (zero requests), the fixed-concurrency-3 stats
// pool and its per-row error isolation, the §4 marker classes on the verdict
// chip, the job routes each button posts, the log drawer's absolute-offset
// polling, and that hostile payload strings stay text.
//
// jsdom has no canvas 2D context, so every row exercises drawSpark's
// null-context self-defense path.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

import {
  makeBootEntry,
  makeDashboardPayload,
  makeJobSnapshot,
  makeJobSummary,
  makeJobsReply,
  makeManyEntries,
  makeRow,
} from './fixtures-dashboard.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const BUNDLE = readFileSync(
  path.join(here, '..', '..', 'abkit', 'tuning', 'assets', 'dashboard.js'),
  'utf8',
);

const ORIGIN = 'http://127.0.0.1:9';
const TOKEN = 't0k3n';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Poll until `predicate()` is true (never a bare timing assertion). */
async function until(predicate, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await sleep(5);
  }
  throw new Error('condition never became true');
}

/** Every window this file opens, closed after each test.
 *
 * Not hygiene: the dashboard polls `/api/jobs` FOREVER (an idle re-arm every
 * 8 s — it is a live cockpit), so a window left open keeps a jsdom timer
 * pending and `node --test` never exits. Closing the window is also what
 * proves the client's teardown is reachable at all.
 */
const openWindows = [];

afterEach(() => {
  for (const dom of openWindows.splice(0)) dom.window.close();
});

function makeDom(url = `${ORIGIN}/?token=${TOKEN}`) {
  const dom = new JSDOM(
    '<!doctype html><html><head></head><body><div id="abk-dashboard"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url },
  );
  openWindows.push(dom);
  return dom;
}

/**
 * A recording fake `fetch`.
 *
 * `handler(url, body, parsed)` returns `{status, json}` (or `{status, text}`);
 * every reply resolves on a macrotask, so the in-flight counter observes real
 * overlap — the concurrency bound is asserted by COUNTING, never by timing.
 */
function fakeFetch(handler, { delay = 8 } = {}) {
  // `statsInflight`/`statsPeak` count the STATS pool only: the job-chip poll is
  // its own single request and is not what `POOL_SIZE` bounds, so folding it in
  // would make the bound read as 4 and the assertion meaningless.
  const state = { calls: [], statsInflight: 0, statsPeak: 0, opened: [] };
  const impl = (url, init) => {
    const body = init && init.body ? JSON.parse(init.body) : null;
    const parsed = new URL(url, ORIGIN);
    const isStats = parsed.pathname.startsWith('/api/stats/');
    state.calls.push({
      url,
      path: parsed.pathname,
      query: parsed.searchParams,
      method: (init && init.method) || 'GET',
      body,
    });
    if (isStats) {
      state.statsInflight += 1;
      state.statsPeak = Math.max(state.statsPeak, state.statsInflight);
    }
    const reply = handler(parsed.pathname, body, parsed) || { status: 200, json: {} };
    return new Promise((resolve) => {
      setTimeout(() => {
        if (isStats) state.statsInflight -= 1;
        if (reply.reject) {
          resolve(Promise.reject(new Error('network down')));
          return;
        }
        resolve({
          ok: reply.status >= 200 && reply.status < 300,
          status: reply.status,
          json: () => Promise.resolve(reply.json),
          text: () => Promise.resolve(reply.text ?? ''),
        });
      }, delay);
    });
  };
  return { impl, state };
}

/** A handler that answers every route with a sane default. */
function defaultHandler(overrides = {}) {
  return (pathname, body, parsed) => {
    if (overrides[pathname]) return overrides[pathname](pathname, body, parsed);
    if (pathname.startsWith('/api/stats/')) {
      const name = decodeURIComponent(pathname.slice('/api/stats/'.length));
      return { status: 200, json: makeRow(name) };
    }
    if (pathname === '/api/jobs') return { status: 200, json: makeJobsReply() };
    if (pathname.startsWith('/api/job/')) return { status: 200, json: makeJobSnapshot() };
    if (pathname === '/api/explore') {
      return { status: 200, json: { job_id: 'ex01', url: `${ORIGIN}/explore?token=x` } };
    }
    if (pathname.startsWith('/api/')) return { status: 200, json: { job_id: 'ab12cd34' } };
    return { status: 404, text: `not found: ${pathname}` };
  };
}

function renderInJsdom(payload, { fetchImpl } = {}) {
  const dom = makeDom();
  const opened = [];
  dom.window.open = (href) => {
    opened.push(href);
    return {};
  };
  if (fetchImpl) dom.window.fetch = fetchImpl;
  dom.window.eval(BUNDLE);
  const mount = dom.window.document.getElementById('abk-dashboard');
  dom.window.__ABK_DASHBOARD__.render(payload, mount);
  return { dom, mount, opened };
}

const chips = (mount) => [...mount.querySelectorAll('.abk-cell-verdict .abk-chip')];
const rowFor = (mount, name) => mount.querySelector(`[data-abk-experiment="${name}"]`);
const chipFor = (mount, name) => rowFor(mount, name).querySelector('.abk-cell-verdict .abk-chip');
const buttonNamed = (scope, text) =>
  [...scope.querySelectorAll('button')].find((b) => b.textContent === text);

// ---------------------------------------------------------------------------
// the boot render: metadata only, zero requests
// ---------------------------------------------------------------------------

test('bundle exposes the window global', () => {
  const dom = makeDom();
  dom.window.eval(BUNDLE);
  assert.equal(typeof dom.window.__ABK_DASHBOARD__, 'object');
  assert.equal(typeof dom.window.__ABK_DASHBOARD__.render, 'function');
});

test('the boot render paints every row pending and fetches NOTHING', () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const payload = makeDashboardPayload({ experiments: makeManyEntries(5) });
  const { mount, dom } = renderInJsdom(payload, { fetchImpl: impl });

  assert.ok(mount.classList.contains('abk-dashboard'));
  assert.match(mount.querySelector('.abk-title').textContent, /dashboard · acme/);
  const painted = chips(mount);
  assert.equal(painted.length, 5, 'one verdict cell per boot entry');
  assert.deepEqual(
    painted.map((c) => c.textContent),
    Array(5).fill('pending'),
  );
  // The whole point of the two-phase payload: the list exists before the
  // network is touched at all.
  assert.equal(state.calls.length, 0, 'no request fired by the render itself');
  assert.ok(dom.window.document.head.querySelector('style[data-abk-dashboard]'));
});

test('metadata comes off the boot entry, not off a fetch', () => {
  const payload = makeDashboardPayload({
    experiments: [makeBootEntry('dash_exp', { tags: ['growth', 'checkout'] })],
  });
  const { mount } = renderInJsdom(payload);
  const row = rowFor(mount, 'dash_exp');
  assert.match(row.querySelector('.abk-name').textContent, /dash_exp/);
  assert.match(row.querySelector('.abk-sub').textContent, /experiments\/dash_exp\.yml/);
  assert.deepEqual(
    [...row.querySelectorAll('.abk-tag')].map((t) => t.textContent),
    ['growth', 'checkout'],
  );
});

test('an empty selection renders the empty state, never throws', () => {
  const { mount } = renderInJsdom(makeDashboardPayload({ experiments: [] }));
  assert.match(mount.querySelector('.abk-empty').textContent, /No experiments/);
});

test('a page opened without its token says so once instead of N row errors', () => {
  const dom = makeDom(`${ORIGIN}/`);
  dom.window.eval(BUNDLE);
  const mount = dom.window.document.getElementById('abk-dashboard');
  dom.window.__ABK_DASHBOARD__.render(makeDashboardPayload(), mount);
  assert.match(mount.querySelector('.abk-warning').textContent, /without its \?token=/);
});

// ---------------------------------------------------------------------------
// the fixed-concurrency-3 stats pool
// ---------------------------------------------------------------------------

test('the stats pool never runs more than three requests at once', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const payload = makeDashboardPayload({ experiments: makeManyEntries(9) });
  const { mount } = renderInJsdom(payload, { fetchImpl: impl });

  await until(() => chips(mount).every((c) => c.textContent !== 'pending'));
  const statsCalls = state.calls.filter((c) => c.path.startsWith('/api/stats/'));
  assert.equal(statsCalls.length, 9, 'every row fetched exactly once');
  assert.ok(state.statsPeak <= 3, `peak in-flight was ${state.statsPeak}, must stay ≤ 3`);
  assert.equal(state.statsPeak, 3, 'and the pool does saturate — otherwise it is serial');
  assert.equal(
    new Set(statsCalls.map((c) => c.path)).size,
    9,
    'one request per experiment, no duplicates',
  );
});

test('every request carries the token from location.search', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  await until(() => chipFor(mount, 'dash_exp').textContent === 'WIN');
  assert.ok(state.calls.length > 0);
  for (const call of state.calls) assert.equal(call.query.get('token'), TOKEN);
});

test('the stats request carries the boot window preset', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const { mount } = renderInJsdom(makeDashboardPayload({ initial_window: '7d' }), {
    fetchImpl: impl,
  });
  await until(() => chipFor(mount, 'dash_exp').textContent === 'WIN');
  const stats = state.calls.find((c) => c.path.startsWith('/api/stats/'));
  assert.equal(stats.query.get('window'), '7d');
});

test('a window change re-fills every row with the new preset', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const payload = makeDashboardPayload({ experiments: makeManyEntries(3) });
  const { mount } = renderInJsdom(payload, { fetchImpl: impl });
  await until(() => chips(mount).every((c) => c.textContent === 'WIN'));

  buttonNamed(mount.querySelector('.abk-seg'), '24h').click();
  // back to the skeleton immediately — the rows are stale by definition
  assert.deepEqual(
    chips(mount).map((c) => c.textContent),
    ['pending', 'pending', 'pending'],
  );
  await until(() => chips(mount).every((c) => c.textContent === 'WIN'));
  const windows = state.calls.filter((c) => c.path.startsWith('/api/stats/')).map((c) => c.query.get('window'));
  assert.deepEqual(windows.slice(0, 3), ['30d', '30d', '30d']);
  assert.deepEqual(windows.slice(3), ['24h', '24h', '24h']);
});

test('one row failing is one row failing — the pool keeps going', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/stats/exp_1': () => ({ status: 500, text: 'RuntimeError: warehouse gone' }),
      '/api/stats/exp_2': () => ({ reject: true }),
    }),
  );
  const payload = makeDashboardPayload({ experiments: makeManyEntries(4) });
  const { mount } = renderInJsdom(payload, { fetchImpl: impl });

  await until(() => chips(mount).every((c) => c.textContent !== 'pending'));
  assert.equal(chipFor(mount, 'exp_0').textContent, 'WIN');
  assert.equal(chipFor(mount, 'exp_3').textContent, 'WIN', 'the row queued behind the failures');
  assert.equal(chipFor(mount, 'exp_1').textContent, 'error');
  assert.match(rowFor(mount, 'exp_1').querySelector('.abk-row-note').textContent, /warehouse gone/);
  assert.equal(chipFor(mount, 'exp_2').textContent, 'error', 'a rejected fetch too');
  assert.equal(state.calls.filter((c) => c.path.startsWith('/api/stats/')).length, 4);
});

test("a row's own error field degrades that row, not the list", async () => {
  const { impl } = fakeFetch(
    defaultHandler({
      '/api/stats/exp_0': () => ({
        status: 200,
        json: makeRow('exp_0', { verdict: null, error: 'ValueError: boom', effect: null }),
      }),
    }),
  );
  const payload = makeDashboardPayload({ experiments: makeManyEntries(2) });
  const { mount } = renderInJsdom(payload, { fetchImpl: impl });

  await until(() => chips(mount).every((c) => c.textContent !== 'pending'));
  assert.equal(chipFor(mount, 'exp_0').textContent, 'error');
  assert.match(rowFor(mount, 'exp_0').querySelector('.abk-row-note').textContent, /boom/);
  assert.equal(chipFor(mount, 'exp_1').textContent, 'WIN');
});

test('verdict null WITHOUT an error reads as "no data", not as a failure', async () => {
  const { impl } = fakeFetch(
    defaultHandler({
      '/api/stats/dash_exp': () => ({
        status: 200,
        json: makeRow('dash_exp', { verdict: null, error: null, effect: null, spark: [] }),
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  await until(() => chipFor(mount, 'dash_exp').textContent !== 'pending');
  assert.equal(chipFor(mount, 'dash_exp').textContent, 'no data');
  assert.match(rowFor(mount, 'dash_exp').querySelector('.abk-row-note').textContent, /press Run/);
});

// ---------------------------------------------------------------------------
// §4 marker classes on the verdict chip
// ---------------------------------------------------------------------------

async function chipWith(rowOverrides) {
  const { impl } = fakeFetch(
    defaultHandler({
      '/api/stats/dash_exp': () => ({
        status: 200,
        json: makeRow('dash_exp', rowOverrides),
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  await until(() => chipFor(mount, 'dash_exp').textContent !== 'pending');
  return { mount, chip: chipFor(mount, 'dash_exp') };
}

test('a failing SRM gate renders the abk-srm-fail marker', async () => {
  const { mount, chip } = await chipWith({
    srm_flag: true,
    srm_pvalue: 1e-9,
    verdict: 'INCONCLUSIVE',
  });
  assert.ok(chip.classList.contains('abk-srm-fail'), 'marker present');
  assert.match(
    rowFor(mount, 'dash_exp').querySelector('.abk-row-note').textContent,
    /SRM FAILED .* effects untrustworthy/,
  );
});

test('a demoted headline look renders the abk-insufficient marker', async () => {
  const { mount, chip } = await chipWith({
    insufficient: true,
    verdict: 'INCONCLUSIVE',
    effect: null,
    ci: [null, null],
    pvalue: null,
  });
  assert.ok(chip.classList.contains('abk-insufficient'), 'marker present');
  assert.match(
    rowFor(mount, 'dash_exp').querySelector('.abk-row-note').textContent,
    /insufficient data .* inference withheld/,
  );
  assert.equal(
    rowFor(mount, 'dash_exp').querySelector('.abk-cell-effect').textContent,
    '— [—, —]',
    'no inference is shown for a demoted look',
  );
});

test('a pre-horizon withheld verdict renders the abk-prehorizon marker', async () => {
  const { mount, chip } = await chipWith({ is_horizon: false, verdict: 'INCONCLUSIVE' });
  assert.ok(chip.classList.contains('abk-prehorizon'), 'marker present');
  assert.match(
    rowFor(mount, 'dash_exp').querySelector('.abk-row-note').textContent,
    /not peeking-valid/,
  );
});

test('a decided pre-horizon verdict is NOT marked as withheld', async () => {
  // Under an always-valid confidence sequence the readout legitimately calls
  // WIN before the horizon — the marker belongs to the refusal, not to the date.
  const { chip } = await chipWith({ is_horizon: false, verdict: 'WIN' });
  assert.ok(chip.classList.contains('abk-v-win'));
  assert.ok(!chip.classList.contains('abk-prehorizon'));
});

test('the verdict word drives the chip class for each verdict kind', async () => {
  for (const verdict of ['WIN', 'LOSE', 'FLAT', 'INCONCLUSIVE']) {
    const { chip } = await chipWith({ verdict, is_horizon: true });
    assert.equal(chip.textContent, verdict);
    assert.ok(chip.classList.contains(`abk-v-${verdict.toLowerCase()}`), verdict);
  }
});

test('a qualified verdict never renders as an unqualified one', async () => {
  const { mount, chip } = await chipWith({
    verdict: 'WIN',
    guardrail_regressed: true,
    caveats: ['a guardrail regressed: refunds -3%'],
  });
  assert.equal(chip.textContent, 'WIN');
  const badges = rowFor(mount, 'dash_exp').querySelector('.abk-badges');
  assert.match(badges.textContent, /guardrail/);
  assert.match(badges.textContent, /⚠ 1/);
});

test('a held pipeline lock is disclosed on the row', async () => {
  const { mount } = await chipWith({ locked: true });
  assert.match(rowFor(mount, 'dash_exp').querySelector('.abk-badges').textContent, /locked/);
});

// ---------------------------------------------------------------------------
// buttons → the DASH-4 job routes
// ---------------------------------------------------------------------------

test('Open opens the report route for that experiment, tokened', async () => {
  const { impl } = fakeFetch(defaultHandler());
  const { mount, opened } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  buttonNamed(rowFor(mount, 'dash_exp').querySelector('.abk-cell-actions'), 'Open').click();
  assert.equal(opened.length, 1);
  const url = new URL(opened[0], ORIGIN);
  assert.equal(url.pathname, '/experiment/dash_exp');
  assert.equal(url.searchParams.get('token'), TOKEN);
});

test('Run posts {select} and follows the spawned job in the drawer', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  buttonNamed(rowFor(mount, 'dash_exp').querySelector('.abk-cell-actions'), 'Run').click();

  await until(() => state.calls.some((c) => c.path === '/api/run'));
  const call = state.calls.find((c) => c.path === '/api/run');
  assert.equal(call.method, 'POST');
  assert.deepEqual(call.body, { select: 'dash_exp' });
  await until(() => state.calls.some((c) => c.path.startsWith('/api/job/')));
  assert.notEqual(mount.querySelector('.abk-drawer').style.display, 'none');
});

test('a busy pipeline 400 surfaces the server message on the row', async () => {
  const { impl } = fakeFetch(
    defaultHandler({
      '/api/run': () => ({ status: 400, text: 'a pipeline job is already running' }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  buttonNamed(rowFor(mount, 'dash_exp').querySelector('.abk-cell-actions'), 'Run').click();
  await until(() =>
    /already running/.test(rowFor(mount, 'dash_exp').querySelector('.abk-row-msg').textContent),
  );
  assert.ok(
    rowFor(mount, 'dash_exp')
      .querySelector('.abk-row-msg')
      .classList.contains('abk-row-msg-err'),
  );
});

test('the expanded row offers one Run per CONFIGURED comparison, secondary included', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const row = rowFor(mount, 'dash_exp');
  row.querySelector('.abk-disclose').click();

  const labels = [...row.querySelectorAll('.abk-detail button')].map((b) => b.textContent);
  assert.ok(labels.includes('Run revenue'), 'the main metric');
  assert.ok(labels.includes('Run refunds (secondary)'), 'a secondary metric never reaches verdicts');

  buttonNamed(row.querySelector('.abk-detail'), 'Run refunds (secondary)').click();
  await until(() => state.calls.some((c) => c.path === '/api/run'));
  assert.deepEqual(state.calls.find((c) => c.path === '/api/run').body, {
    select: 'dash_exp',
    metric: 'refunds',
  });
});

test('Explore posts {select} and opens the URL the server scraped', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const { mount, opened } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const actions = rowFor(mount, 'dash_exp').querySelector('.abk-cell-actions');
  buttonNamed(actions, 'Explore').click();

  // the one LONG route: the button stays busy while the child boots
  assert.equal(buttonNamed(actions, 'Explore').disabled, true);
  assert.match(rowFor(mount, 'dash_exp').querySelector('.abk-row-msg').textContent, /up to 90 s/);

  await until(() => opened.length > 0);
  assert.deepEqual(state.calls.find((c) => c.path === '/api/explore').body, {
    select: 'dash_exp',
  });
  assert.equal(opened[0], `${ORIGIN}/explore?token=x`);
  assert.equal(buttonNamed(actions, 'Explore').disabled, false, 'the button comes back');
});

test('a failed explore says why and re-enables the button', async () => {
  const { impl } = fakeFetch(
    defaultHandler({
      '/api/explore': () => ({
        status: 400,
        text: 'the explore cockpit for dash_exp exited without serving (failed) — output:\nno computed results yet',
      }),
    }),
  );
  const { mount, opened } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const actions = rowFor(mount, 'dash_exp').querySelector('.abk-cell-actions');
  buttonNamed(actions, 'Explore').click();
  await until(() =>
    /exited without serving/.test(rowFor(mount, 'dash_exp').querySelector('.abk-row-msg').textContent),
  );
  assert.equal(opened.length, 0, 'no tab for a cockpit that never served');
  assert.equal(buttonNamed(actions, 'Explore').disabled, false);
});

test('Clean is confirmed before it is posted (it spawns the --execute form)', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const row = rowFor(mount, 'dash_exp');
  row.querySelector('.abk-disclose').click();
  const detail = row.querySelector('.abk-detail');

  buttonNamed(detail, 'Clean…').click();
  const confirm = detail.querySelector('.abk-confirm');
  assert.notEqual(confirm.style.display, 'none');
  assert.match(confirm.textContent, /DELETES/);
  assert.ok(!state.calls.some((c) => c.path === '/api/clean'), 'nothing posted before the confirm');

  buttonNamed(confirm, 'Clean anyway').click();
  await until(() => state.calls.some((c) => c.path === '/api/clean'));
  assert.deepEqual(state.calls.find((c) => c.path === '/api/clean').body, { select: 'dash_exp' });
});

test('Cancel on the clean confirm posts nothing', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const row = rowFor(mount, 'dash_exp');
  row.querySelector('.abk-disclose').click();
  const detail = row.querySelector('.abk-detail');
  buttonNamed(detail, 'Clean…').click();
  buttonNamed(detail.querySelector('.abk-confirm'), 'Cancel').click();
  await sleep(20);
  assert.ok(!state.calls.some((c) => c.path === '/api/clean'));
  assert.equal(detail.querySelector('.abk-confirm').style.display, 'none');
});

test('Unlock posts {select}', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const row = rowFor(mount, 'dash_exp');
  row.querySelector('.abk-disclose').click();
  buttonNamed(row.querySelector('.abk-detail'), 'Unlock').click();
  await until(() => state.calls.some((c) => c.path === '/api/unlock'));
  assert.deepEqual(state.calls.find((c) => c.path === '/api/unlock').body, { select: 'dash_exp' });
});

test('Show YAML reads the read-only source route — there is no save button', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/experiment-source/dash_exp': () => ({
        status: 200,
        json: {
          name: 'dash_exp',
          path: 'experiments/dash_exp.yml',
          yaml_text: 'name: dash_exp\n',
          truncated: false,
        },
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const row = rowFor(mount, 'dash_exp');
  row.querySelector('.abk-disclose').click();
  const detail = row.querySelector('.abk-detail');
  buttonNamed(detail, 'Show YAML').click();

  await until(() => /name: dash_exp/.test(detail.querySelector('.abk-source').textContent));
  assert.ok(state.calls.some((c) => c.path === '/api/experiment-source/dash_exp'));
  const labels = [...detail.querySelectorAll('button')].map((b) => b.textContent);
  assert.ok(!labels.some((l) => /save/i.test(l)), 'read-only: no save affordance exists');
});

// ---------------------------------------------------------------------------
// the job chip + the log drawer (absolute offsets)
// ---------------------------------------------------------------------------

test('the job chip reads the SERVER pipeline_active flag and names running jobs', async () => {
  const { impl } = fakeFetch(
    defaultHandler({
      '/api/jobs': () => ({
        status: 200,
        json: makeJobsReply([makeJobSummary({ kind: 'run', experiment: 'dash_exp' })], true),
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  await until(() => /run dash_exp/.test(mount.querySelector('.abk-chip-job').textContent));
  assert.match(mount.querySelector('.abk-chip-job').textContent, /jobs: run dash_exp/);
  assert.ok(mount.querySelector('.abk-chip-job').classList.contains('abk-chip-busy'));
});

test('the drawer polls with monotonically advancing offsets and never repeats a line', async () => {
  const offsets = [];
  const { impl } = fakeFetch(
    defaultHandler({
      '/api/jobs': () => ({ status: 200, json: makeJobsReply() }),
      '/api/job/ab12cd34': (_pathname, _body, parsed) => {
        const offset = Number(parsed.searchParams.get('offset'));
        offsets.push(offset);
        if (offset === 0) {
          return {
            status: 200,
            json: makeJobSnapshot({ lines: ['LOAD 1', 'LOAD 2'], next_offset: 2 }),
          };
        }
        return {
          status: 200,
          json: makeJobSnapshot({
            lines: ['COMPUTE 3'],
            next_offset: 3,
            status: 'done',
            returncode: 0,
          }),
        };
      },
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  buttonNamed(rowFor(mount, 'dash_exp').querySelector('.abk-cell-actions'), 'Run').click();

  await until(() => /exit 0/.test(mount.querySelector('.abk-drawer-status').textContent), 4000);
  const lines = [...mount.querySelectorAll('.abk-drawer-log .abk-log-line')].map(
    (n) => n.textContent,
  );
  assert.deepEqual(lines, ['LOAD 1', 'LOAD 2', 'COMPUTE 3'], 'each line rendered exactly once');
  assert.deepEqual(offsets.slice(0, 2), [0, 2], 'the client tracks next_offset, not a bare append');
  assert.ok(
    offsets.every((offset, i) => i === 0 || offset >= offsets[i - 1]),
    'offsets never rewind',
  );
  // a finished job stops the poll loop
  const seen = offsets.length;
  await sleep(DRAWER_QUIET_MS);
  assert.equal(offsets.length, seen, 'no polling after a terminal status');
});

const DRAWER_QUIET_MS = 900;

test('discarded output is disclosed rather than left as a hole', async () => {
  const { impl } = fakeFetch(
    defaultHandler({
      '/api/job/ab12cd34': () => ({
        status: 200,
        json: makeJobSnapshot({
          lines: ['tail'],
          next_offset: 5001,
          dropped: 5000,
          truncated: true,
          status: 'done',
          returncode: 0,
        }),
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  buttonNamed(rowFor(mount, 'dash_exp').querySelector('.abk-cell-actions'), 'Run').click();
  await until(() => /discarded/.test(mount.querySelector('.abk-drawer-status').textContent));
  assert.match(mount.querySelector('.abk-drawer-status').textContent, /5000 earlier line/);
});

test('Stop posts to the stop route while the job runs', async () => {
  const { impl, state } = fakeFetch(defaultHandler());
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  buttonNamed(rowFor(mount, 'dash_exp').querySelector('.abk-cell-actions'), 'Run').click();
  await until(() => state.calls.some((c) => c.path.startsWith('/api/job/ab12cd34')));
  buttonNamed(mount.querySelector('.abk-drawer-head'), 'Stop').click();
  await until(() => state.calls.some((c) => c.path === '/api/job/ab12cd34/stop'));
  assert.equal(state.calls.find((c) => c.path === '/api/job/ab12cd34/stop').method, 'POST');
});

test('a job that just finished refreshes ITS row only', async () => {
  let finished = false;
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/jobs': () => ({
        status: 200,
        json: makeJobsReply(
          [
            makeJobSummary({
              experiment: 'exp_1',
              status: finished ? 'done' : 'running',
              returncode: finished ? 0 : null,
            }),
          ],
          !finished,
        ),
      }),
    }),
  );
  const payload = makeDashboardPayload({ experiments: makeManyEntries(3) });
  const { mount } = renderInJsdom(payload, { fetchImpl: impl });
  await until(() => chips(mount).every((c) => c.textContent === 'WIN'));
  await until(() => state.calls.some((c) => c.path === '/api/jobs'));
  const before = state.calls.filter((c) => c.path.startsWith('/api/stats/')).length;

  finished = true; // the next poll sees the running→done edge
  await until(
    () => state.calls.filter((c) => c.path.startsWith('/api/stats/')).length > before,
    4000,
  );
  const extra = state.calls
    .filter((c) => c.path.startsWith('/api/stats/'))
    .slice(before)
    .map((c) => c.path);
  assert.deepEqual(extra, ['/api/stats/exp_1'], 'one row re-read, not the list');
});

// ---------------------------------------------------------------------------
// escaping + the canvas fallback
// ---------------------------------------------------------------------------

test('hostile payload strings stay text — nothing executes or injects', async () => {
  const hostile = '<img src=x onerror="window.__pwned=1">';
  const payload = makeDashboardPayload({
    project: hostile,
    experiments: [makeBootEntry(hostile, { tags: [hostile], file: hostile })],
  });
  const { impl } = fakeFetch(
    defaultHandler({
      [`/api/stats/${hostile}`]: () => ({
        status: 200,
        json: makeRow(hostile, {
          error: hostile,
          verdict: null,
          warnings: [hostile],
          rationale: [hostile],
        }),
      }),
    }),
  );
  const { dom, mount } = renderInJsdom(payload, { fetchImpl: impl });
  await until(() => chips(mount).every((c) => c.textContent !== 'pending'));
  assert.equal(dom.window.__pwned, undefined);
  assert.ok(!mount.querySelector('img'), 'no element injected');
  assert.ok(mount.textContent.includes(hostile), 'hostile string survives as text');
});

test('a canvas-less environment loses the sparkline and nothing else', async () => {
  const { impl } = fakeFetch(defaultHandler());
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  await until(() => chipFor(mount, 'dash_exp').textContent === 'WIN');
  // jsdom has no 2D context: the row still carries every number.
  assert.ok(mount.querySelector('canvas.abk-spark'));
  assert.match(
    rowFor(mount, 'dash_exp').querySelector('.abk-cell-effect').textContent,
    /\+0\.120 \[0\.060, 0\.180\]/,
  );
  assert.match(rowFor(mount, 'dash_exp').querySelector('.abk-cell-p').textContent, /0\.002 \/ 0\.050/);
});

test('a re-render tears the previous page down (no double pollers)', async () => {
  // Deterministic by construction, because a count-over-time threshold is not:
  // the job poll re-arms itself from its OWN completion, so the leak is a poll
  // that was in flight at teardown scheduling the next one. The fake therefore
  // holds every /api/jobs reply open until the test releases it, and the two
  // pages are told apart by the cadence their reply selects — the abandoned one
  // gets `pipeline_active: true` (re-arm in 1.2 s), the live one gets `false`
  // (8 s). Any poll inside the wait below is the abandoned page's.
  const calls = [];
  const gates = [];
  const ok = (json) => ({
    ok: true,
    status: 200,
    json: () => Promise.resolve(json),
    text: () => Promise.resolve(''),
  });
  const impl = (url) => {
    const parsed = new URL(url, ORIGIN);
    calls.push(parsed.pathname);
    if (parsed.pathname === '/api/jobs') {
      return new Promise((resolve) => {
        gates.push((active) => resolve(ok(makeJobsReply([makeJobSummary()], active))));
      });
    }
    if (parsed.pathname.startsWith('/api/stats/')) {
      return Promise.resolve(ok(makeRow(decodeURIComponent(parsed.pathname.slice(11)))));
    }
    return Promise.resolve(ok({}));
  };

  const dom = makeDom();
  dom.window.open = () => ({});
  dom.window.fetch = impl;
  dom.window.eval(BUNDLE);
  const mount = dom.window.document.getElementById('abk-dashboard');

  dom.window.__ABK_DASHBOARD__.render(makeDashboardPayload(), mount);
  await until(() => gates.length === 1, 1000);
  // Re-render while page 1's poll is still in flight — the leak's exact window.
  dom.window.__ABK_DASHBOARD__.render(makeDashboardPayload(), mount);
  await until(() => gates.length === 2, 1000);
  assert.equal(chips(mount).length, 1, 'the mount is rebuilt, not appended to');

  gates[0](true); // the abandoned page: busy ⇒ it would re-arm in 1.2 s
  gates[1](false); // the live page: idle ⇒ its next poll is 8 s away
  await until(() => chipFor(mount, 'dash_exp').textContent === 'WIN', 1000);

  const before = calls.filter((p) => p === '/api/jobs').length;
  await sleep(1600); // past the busy cadence, far short of the idle one
  const added = calls.filter((p) => p === '/api/jobs').length - before;
  assert.equal(added, 0, 'the abandoned page must not still be polling');
});
