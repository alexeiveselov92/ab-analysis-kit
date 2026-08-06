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
  makeMultiArmRow,
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

const SOURCE = {
  name: 'dash_exp',
  path: 'experiments/dash_exp.yml',
  yaml_text: 'name: dash_exp\n',
  truncated: false,
  digest: 'd1',
  editable: true,
};

const writeReply = (over = {}) => ({
  name: 'dash_exp',
  path: 'experiments/dash_exp.yml',
  archived: 'experiments/.history/dash_exp/dash_exp-20260802T101530Z.yml',
  digest: 'd2',
  renamed_from: null,
  in_selection: true,
  warnings: [],
  experiments: makeDashboardPayload().experiments,
  ...over,
});

/** Open a row's editor and wait for the source load to land. */
async function openEditor(mount, state, name = 'dash_exp') {
  const row = rowFor(mount, name);
  row.querySelector('.abk-disclose').click();
  const detail = row.querySelector('.abk-detail');
  buttonNamed(detail, 'Edit YAML').click();
  await until(() => state.calls.some((c) => c.path === `/api/experiment-source/${name}`));
  await until(() => detail.querySelector('.abk-yaml').value !== '');
  return { row, detail, area: detail.querySelector('.abk-yaml') };
}

test('Edit YAML loads the source into a textarea and Save posts the text back', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/experiment-source/dash_exp': () => ({ status: 200, json: SOURCE }),
      '/api/experiment/save': () => ({ status: 200, json: writeReply() }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const { detail, area } = await openEditor(mount, state);

  assert.equal(area.value, 'name: dash_exp\n');
  area.value = 'name: dash_exp\nalpha: 0.01\n';
  buttonNamed(detail, 'Save').click();

  await until(() => state.calls.some((c) => c.path === '/api/experiment/save'));
  const call = state.calls.find((c) => c.path === '/api/experiment/save');
  assert.equal(call.method, 'POST');
  assert.equal(call.query.get('token'), TOKEN);
  // the digest the read handed out rides back — the concurrency token
  assert.deepEqual(call.body, {
    select: 'dash_exp',
    text: 'name: dash_exp\nalpha: 0.01\n',
    digest: 'd1',
    force: false,
  });
  await until(() => /saved experiments\/dash_exp\.yml/.test(
    detail.querySelector('.abk-editor-msg').textContent,
  ));
});

test('a refused save shows the reason and offers no override for bad YAML', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/experiment-source/dash_exp': () => ({ status: 200, json: SOURCE }),
      '/api/experiment/save': () => ({ status: 400, text: 'invalid YAML: while parsing' }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const { detail } = await openEditor(mount, state);
  buttonNamed(detail, 'Save').click();

  await until(() => /invalid YAML/.test(detail.querySelector('.abk-editor-msg').textContent));
  // level 1 is never forceable, so no "Save anyway" appears
  assert.equal(buttonNamed(detail, 'Save anyway'), undefined);
});

test('a level-2 refusal offers Save anyway, which re-posts with force', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/experiment-source/dash_exp': () => ({ status: 200, json: SOURCE }),
      '/api/experiment/save': (_p, body) =>
        body.force
          ? { status: 200, json: writeReply({ warnings: ['SAVED WITH AN ERROR — …'] }) }
          : {
              status: 400,
              text: "the config is valid YAML but not valid for this project:\n  - no metric named 'nope'",
            },
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const { detail } = await openEditor(mount, state);
  buttonNamed(detail, 'Save').click();

  await until(() => buttonNamed(detail, 'Save anyway') !== undefined);
  buttonNamed(detail, 'Save anyway').click();

  await until(() => state.calls.filter((c) => c.path === '/api/experiment/save').length === 2);
  assert.equal(state.calls.filter((c) => c.path === '/api/experiment/save')[1].body.force, true);
  await until(() => /SAVED WITH AN ERROR/.test(
    detail.querySelector('.abk-editor-msg').textContent,
  ));
});

test('a file too large to show is loaded read-only, with Save disabled', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/experiment-source/dash_exp': () => ({
        status: 200,
        json: { ...SOURCE, truncated: true, digest: null, editable: false },
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const { detail } = await openEditor(mount, state);

  await until(() => /too large to edit here/.test(
    detail.querySelector('.abk-editor-msg').textContent,
  ));
  assert.equal(buttonNamed(detail, 'Save').disabled, true);
});

test('Delete needs the confirm, then posts and drops the row', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/experiment-source/dash_exp': () => ({ status: 200, json: SOURCE }),
      '/api/experiment/delete': () => ({
        status: 200,
        json: {
          name: 'dash_exp',
          path: 'experiments/dash_exp.yml',
          archived: 'experiments/.history/dash_exp/dash_exp-x-deleted.yml',
          warnings: ['… `abk clean --orphaned-experiments` prunes them'],
          experiments: makeDashboardPayload().experiments.filter((e) => e.name !== 'dash_exp'),
        },
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const { detail } = await openEditor(mount, state);

  buttonNamed(detail, 'Delete…').click();
  assert.ok(!state.calls.some((c) => c.path === '/api/experiment/delete'));
  buttonNamed(detail, 'Delete anyway').click();

  await until(() => state.calls.some((c) => c.path === '/api/experiment/delete'));
  await until(() => rowFor(mount, 'dash_exp') === null);
});

test('New experiment posts the template and adopts the returned selection', async () => {
  const created = [
    ...makeDashboardPayload().experiments,
    { ...makeDashboardPayload().experiments[0], name: 'dash_new', file: 'experiments/dash_new.yml' },
  ];
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/experiment/create': () => ({
        status: 200,
        json: writeReply({ name: 'dash_new', path: 'experiments/dash_new.yml', archived: null, experiments: created }),
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  buttonNamed(mount, 'New experiment').click();
  const panel = mount.querySelector('.abk-create');
  assert.match(panel.querySelector('.abk-yaml').value, /name: my_experiment/);
  panel.querySelector('.abk-text').value = 'growth';
  buttonNamed(panel, 'Create').click();

  await until(() => state.calls.some((c) => c.path === '/api/experiment/create'));
  const call = state.calls.find((c) => c.path === '/api/experiment/create');
  assert.equal(call.body.folder, 'growth');
  assert.match(call.body.text, /horizon_ts/);
  await until(() => rowFor(mount, 'dash_new') !== null);
});

test('Reload configs repaints a metadata-only change (same names, new rows)', async () => {
  // The regression this pins: comparing NAME LISTS made a reload that changed
  // an experiment's comparisons/tags repaint nothing at all — and that is the
  // button documented as what you press after editing a YAML in your own
  // editor, where the name almost never moves.
  const base = makeDashboardPayload();
  const edited = base.experiments.map((e) =>
    e.name === 'dash_exp' ? { ...e, tags: ['freshly-edited'] } : e,
  );
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/reload': () => ({ status: 200, json: { experiments: edited, generated_at: 0, warnings: [] } }),
    }),
  );
  const { mount } = renderInJsdom(base, { fetchImpl: impl });
  await until(() => state.calls.filter((c) => c.path.startsWith('/api/stats/')).length >= 1);
  buttonNamed(mount, 'Reload configs').click();

  await until(() => state.calls.some((c) => c.path === '/api/reload'));
  await until(() => /freshly-edited/.test(rowFor(mount, 'dash_exp').textContent));
});

test('a delete shows the archive path where the removed row cannot swallow it', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/experiment-source/dash_exp': () => ({ status: 200, json: SOURCE }),
      '/api/experiment/delete': () => ({
        status: 200,
        json: {
          name: 'dash_exp',
          path: 'experiments/dash_exp.yml',
          archived: 'experiments/.history/dash_exp/dash_exp-x-deleted.yml',
          warnings: ['… `abk clean --orphaned-experiments` prunes them'],
          experiments: makeDashboardPayload().experiments.filter((e) => e.name !== 'dash_exp'),
        },
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const { detail } = await openEditor(mount, state);
  buttonNamed(detail, 'Delete…').click();
  buttonNamed(detail, 'Delete anyway').click();

  await until(() => rowFor(mount, 'dash_exp') === null);
  const banner = mount.querySelector('.abk-banner').textContent;
  assert.match(banner, /dash_exp-x-deleted\.yml/);
  assert.match(banner, /orphaned-experiments/);
});

test('Reload configs re-reads the selection and surfaces a failed reload once', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/reload': () => ({
        status: 200,
        json: {
          experiments: makeDashboardPayload().experiments,
          generated_at: 0,
          warnings: ['the project could not be re-read after the change'],
        },
      }),
    }),
  );
  const { mount } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  buttonNamed(mount, 'Reload configs').click();

  await until(() => state.calls.some((c) => c.path === '/api/reload'));
  await until(() => /could not be re-read/.test(mount.querySelector('.abk-banner').textContent));
  // one banner, not one message per row
  assert.equal(mount.querySelectorAll('.abk-banner').length, 1);
});

test('a hostile server message in the editor pane stays text', async () => {
  const { impl, state } = fakeFetch(
    defaultHandler({
      '/api/experiment-source/dash_exp': () => ({ status: 200, json: SOURCE }),
      '/api/experiment/save': () => ({
        status: 400,
        text: '<img src=x onerror="window.__pwned=1"> invalid YAML',
      }),
    }),
  );
  const { mount, dom } = renderInJsdom(makeDashboardPayload(), { fetchImpl: impl });
  const { detail } = await openEditor(mount, state);
  buttonNamed(detail, 'Save').click();

  await until(() => /invalid YAML/.test(detail.querySelector('.abk-editor-msg').textContent));
  assert.equal(dom.window.__pwned, undefined);
  assert.equal(detail.querySelectorAll('img').length, 0);
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

// ── m14 DEC-4: the decision layer on the row ──────────────────────────────────

test('a three-arm row names the leader and labels the arm-vs-arm evidence', async () => {
  const { mount, dom } = renderInJsdom(makeDashboardPayload(), {
    fetchImpl: fakeFetch(
      defaultHandler({
        '/api/stats/dash_exp': () => ({ status: 200, json: makeMultiArmRow('dash_exp') }),
      }),
    ).impl,
  });
  await until(() => chipFor(mount, 'dash_exp').textContent === 'WIN', 1000);

  // the row's own cells are the LEADER's, not the first declared treatment's
  const leader = mount.querySelector('.abk-badge-leader');
  assert.ok(leader, 'leader chip present at three arms');
  assert.match(leader.textContent, /t2/);

  // expand: every declared pair is listed, and only the arm-vs-arm one is tagged
  mount.querySelector('.abk-disclose').click();
  const pairs = [...mount.querySelectorAll('.abk-pair')];
  assert.equal(pairs.length, 3);
  const tagged = pairs.filter((p) => p.querySelector('.abk-pair-role'));
  assert.equal(tagged.length, 1, 'exactly the treatment pair carries the role tag');
  assert.match(tagged[0].textContent, /t1 vs t2/);
  assert.match(tagged[0].querySelector('.abk-pair-role').textContent, /arm vs arm/);
  dom.window.close();
});

test('a two-arm row grows no leader chip', async () => {
  // With one treatment "→ treatment" only restates the WIN beside it, so the
  // row is the one `0.8.0` rendered — even though `leader` IS in the payload.
  const { mount, dom } = renderInJsdom(makeDashboardPayload(), {
    fetchImpl: fakeFetch(defaultHandler()).impl,
  });
  await until(() => chipFor(mount, 'dash_exp').textContent === 'WIN', 1000);

  assert.equal(mount.querySelector('.abk-badge-leader'), null);
  mount.querySelector('.abk-disclose').click();
  assert.equal(mount.querySelector('.abk-pair-role'), null, 'nothing to label at two arms');
  dom.window.close();
});

test('main metrics naming different leaders raise a split chip', async () => {
  const split = makeMultiArmRow('dash_exp', {
    leaders_agree: false,
    rollups: [
      { metric: 'revenue', leader: 't2', indistinguishable: [], separation: 'separated',
        losers: [], guardrail_regressed: [], rationale: ['t2 beat control'], caveats: [] },
      { metric: 'orders', leader: 't1', indistinguishable: [], separation: 'separated',
        losers: [], guardrail_regressed: [], rationale: ['t1 beat control'], caveats: [] },
    ],
  });
  const { mount, dom } = renderInJsdom(makeDashboardPayload(), {
    fetchImpl: fakeFetch(
      defaultHandler({ '/api/stats/dash_exp': () => ({ status: 200, json: split }) }),
    ).impl,
  });
  await until(() => chipFor(mount, 'dash_exp').textContent === 'WIN', 1000);

  const chip = [...mount.querySelectorAll('.abk-badge-caveat')].find((b) =>
    /leaders split/.test(b.textContent),
  );
  assert.ok(chip, 'the disagreement is REPORTED — the dashboard never breaks the tie');
  assert.match(chip.title, /revenue: t2/);
  assert.match(chip.title, /orders: t1/);
  dom.window.close();
});
