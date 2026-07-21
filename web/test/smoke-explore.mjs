// @ts-nocheck — assertion-style test code; the payload fixtures live in
// fixtures-explore.mjs, which IS checked against the lockstep contract.
// jsdom smoke test for the committed explore bundle.
//
// Loads abkit/tuning/assets/explore.js (the COMMITTED artifact — run
// `npm run build` first; CI rebuilds and diffs before running this) into a
// jsdom window and renders fixture payloads. The static-preview payload
// exercises the render skeleton, the §4 marker classes, escaping, and the
// degradations; a fake window.fetch exercises the live half — the initial
// recompute, reply adoption into the chips, request_id monotonicity, and the
// Apply calibration-confirm flow. jsdom has no canvas 2D context, so the
// chart exercises the abk-chart-fallback self-defense path.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

import {
  makeCalibration,
  makeExplorePayload,
  makeReply,
  makeSurface,
  makeThreeArmExplorePayload,
  makeValidateReply,
} from './fixtures-explore.mjs';
import { makePoint } from './fixtures.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const BUNDLE = readFileSync(
  path.join(here, '..', '..', 'abkit', 'tuning', 'assets', 'explore.js'),
  'utf8',
);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function makeDom() {
  return new JSDOM(
    '<!doctype html><html><head></head><body><div id="abk-explore"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true },
  );
}

function renderInJsdom(payload, { fetchImpl } = {}) {
  const dom = makeDom();
  if (fetchImpl) dom.window.fetch = fetchImpl;
  dom.window.eval(BUNDLE);
  const mount = dom.window.document.getElementById('abk-explore');
  dom.window.__ABK_EXPLORE__.render(payload, mount);
  return { dom, mount };
}

/** A canned-reply fetch fake that records every request. */
function fakeFetch(handler) {
  const calls = [];
  const impl = (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, body });
    const reply = handler(url, body);
    return Promise.resolve({
      ok: reply.status === 200,
      status: reply.status,
      json: () => Promise.resolve(reply.json),
      text: () => Promise.resolve(reply.text ?? ''),
    });
  };
  return { impl, calls };
}

// ---------------------------------------------------------------------------
// static preview (all endpoint slots null)
// ---------------------------------------------------------------------------

test('bundle exposes the window global', () => {
  const dom = makeDom();
  dom.window.eval(BUNDLE);
  assert.equal(typeof dom.window.__ABK_EXPLORE__, 'object');
  assert.equal(typeof dom.window.__ABK_EXPLORE__.render, 'function');
});

test('static preview renders the cockpit skeleton, badge, and degradations', () => {
  const { dom, mount } = renderInJsdom(makeExplorePayload());
  const q = (sel) => mount.querySelector(sel);
  assert.ok(mount.classList.contains('abk-explore'));
  assert.match(q('.abk-title').textContent, /acme · explore_exp/);
  assert.match(q('.abk-badge-page').textContent, /preview/);
  assert.ok(q('.abk-badge-preview'), 'preview badge (save_url null)');
  // windshield chips
  assert.equal(q('.abk-chip.abk-srm').getAttribute('data-abk-srm'), 'ok');
  assert.equal(q('.abk-calibration').getAttribute('data-abk-calibration'), 'uncalibrated');
  assert.match(q('.abk-calibration').textContent, /uncalibrated — run `abk validate` \(M4\)/);
  // chart: jsdom has no canvas 2D → the fallback self-defense path
  assert.ok(q('.abk-chart-main'));
  assert.ok(q('.abk-chart-fallback'));
  // rail auto-derived from param_specs: method seg + test_type + alpha knob
  const segLabels = [...mount.querySelectorAll('.abk-seg-btn')].map((b) => b.textContent);
  assert.ok(segLabels.includes('t-test'), 'method picker lists t-test');
  assert.ok(segLabels.some((l) => l.startsWith('cuped-t-test')), 'method picker lists cuped variant');
  assert.ok(segLabels.includes('relative'), 'test_type seg in Basic');
  assert.ok(q('.abk-advanced'), 'Advanced disclosure present');
  assert.ok(q('input.abk-num'), 'alpha number knob present');
  // identity-excluded specs never become knobs
  const labels = [...mount.querySelectorAll('.abk-ctl-label')].map((n) => n.textContent);
  assert.ok(!labels.some((l) => l.includes('seed')), 'seed is not a knob');
  // static degradations: knobs read-only + no Apply button
  assert.ok(q('.abk-rail-off'), 'rail disabled in static preview');
  assert.ok(!q('.abk-btn-apply'), 'no Apply button without save_url');
  assert.ok(q('.abk-preview-note'));
  // modes: Tune/Review live, Auto + Segment present but disabled
  const modes = [...mount.querySelectorAll('.abk-mode-btn')].map((b) => b.textContent);
  assert.deepEqual(modes, ['Tune', 'Review', 'Auto', 'Segment']);
  assert.equal(mount.querySelectorAll('.abk-mode-disabled').length, 2);
  // style injected once, scoped
  assert.ok(dom.window.document.head.querySelector('style[data-abk-explore]'));
});

test('daily cadence hides the look counter; sub-day shows it', () => {
  const { mount: daily } = renderInJsdom(makeExplorePayload());
  assert.ok(!daily.querySelector('.abk-look'));
  const { mount: subday } = renderInJsdom(makeExplorePayload({ cadence_seconds: 3600 }));
  assert.match(subday.querySelector('.abk-look').textContent, /look 14 \/ ~14 planned/);
});

test('SRM failure renders the red gate chip with the abk-srm-fail marker', () => {
  const payload = makeExplorePayload({
    srm: {
      flag: true,
      pvalue: 0.0001,
      observed: { control: 6200, treatment: 3800 },
      expected: { control: 0.5, treatment: 0.5 },
    },
  });
  const { mount } = renderInJsdom(payload);
  const chip = mount.querySelector('.abk-srm-fail');
  assert.ok(chip, 'abk-srm-fail marker present');
  assert.match(chip.textContent, /SRM FAILED .* — effects untrustworthy/);
  assert.equal(chip.getAttribute('data-abk-srm'), 'fail');
});

test('pre-horizon and insufficient cutoffs carry the §4 marker classes', () => {
  const payload = makeExplorePayload();
  const series = Array.from({ length: 5 }, (_, i) => makePoint(i + 1));
  series[2] = makePoint(3, { ins: 1, e: null, lo: null, hi: null, p: null, rj: null, mde: null });
  payload.metrics[0].pairs[0].series = series;
  const { mount } = renderInJsdom(payload);
  const pre = mount.querySelector('.abk-note.abk-prehorizon');
  assert.ok(pre, 'abk-prehorizon marker present');
  assert.match(pre.textContent, /not peeking-valid/);
  assert.equal(mount.querySelector('[data-abk-prehorizon]').getAttribute('data-abk-prehorizon'), '1');
  const ins = mount.querySelector('.abk-note.abk-insufficient');
  assert.ok(ins, 'abk-insufficient marker present');
  assert.match(ins.textContent, /1 insufficient-data cutoff greyed/);
});

test('hostile payload strings stay text — nothing executes or injects', () => {
  const hostile = '<img src=x onerror="window.__pwned=1">';
  const payload = makeExplorePayload({
    description: hostile,
    warnings: [hostile],
  });
  payload.metrics[0].name = hostile; // rides into the metric picker + review rows
  payload.explore.metrics[hostile] = makeSurface({ metric: hostile });
  delete payload.explore.metrics.revenue;
  payload.explore.default_metric = hostile;
  const { dom, mount } = renderInJsdom(payload);
  assert.equal(dom.window.__pwned, undefined);
  assert.ok(!mount.querySelector('img'), 'no element injected');
  assert.ok(mount.textContent.includes(hostile), 'hostile string survives as text');
});

test('empty experiment payload renders the empty state, never throws', () => {
  const payload = makeExplorePayload({ metrics: [], verdicts: [] });
  payload.explore.metrics = {};
  payload.explore.default_metric = null;
  const { mount } = renderInJsdom(payload);
  assert.match(mount.querySelector('.abk-empty').textContent, /run `abk run` first/);
});

// ---------------------------------------------------------------------------
// Review mode (WP0): one verdict line per (metric, pair), never just the first
// ---------------------------------------------------------------------------

test('Review mode renders exactly one verdict line for a 2-arm metric', () => {
  const { mount } = renderInJsdom(makeExplorePayload());
  const reviewBtn = [...mount.querySelectorAll('.abk-mode-btn')].find((b) => b.textContent === 'Review');
  reviewBtn.click();
  const rows = [...mount.querySelectorAll('.abk-review-row')];
  assert.equal(rows.length, 1, 'one review row for the one metric');
  const verdictLines = rows[0].querySelectorAll('.abk-review-verdict');
  assert.equal(verdictLines.length, 1, '2-arm: exactly one verdict line, unchanged from today');
  assert.match(verdictLines[0].textContent, /WIN \(control vs treatment\)/);
  assert.ok(verdictLines[0].classList.contains('abk-verdict-win'), 'abk-verdict-<word> marker present');
});

test('Review mode renders one verdict line PER PAIR for a 3-arm metric (WP0 regression)', () => {
  // the "revenue" metric carries TWO VerdictBlocks here (control-vs-treatment,
  // control-vs-treatment_b) — before the WP0 fix, `payload.verdicts.find(...)`
  // rendered only the first and silently dropped the second.
  const { mount } = renderInJsdom(makeThreeArmExplorePayload());
  const reviewBtn = [...mount.querySelectorAll('.abk-mode-btn')].find((b) => b.textContent === 'Review');
  reviewBtn.click();
  const rows = [...mount.querySelectorAll('.abk-review-row')];
  assert.equal(rows.length, 1, 'still one review row per metric, not per pair');
  const verdictLines = [...rows[0].querySelectorAll('.abk-review-verdict')];
  assert.equal(verdictLines.length, 2, 'both declared pairs render their own verdict line');
  const texts = verdictLines.map((n) => n.textContent);
  assert.ok(texts.some((t) => /WIN \(control vs treatment\)/.test(t)), 'first pair renders');
  assert.ok(texts.some((t) => /LOSE \(control vs treatment_b\)/.test(t)), 'second pair renders (was dropped pre-fix)');
  assert.ok(
    verdictLines.some((n) => n.classList.contains('abk-verdict-win')) &&
      verdictLines.some((n) => n.classList.contains('abk-verdict-lose')),
    'each line keeps its own abk-verdict-<word> marker class',
  );
});

// ---------------------------------------------------------------------------
// live server (fake fetch): initial recompute, adoption, id monotonicity
// ---------------------------------------------------------------------------

function liveUrls() {
  return {
    save_url: 'http://127.0.0.1:9/apply?token=t',
    recompute_url: 'http://127.0.0.1:9/recompute?token=t',
    reload_url: 'http://127.0.0.1:9/reload?token=t',
    validate_url: 'http://127.0.0.1:9/validate?token=t',
  };
}

test('live page fires the initial recompute and adopts the reply into the chips', async () => {
  const { impl, calls } = fakeFetch((url, body) => ({
    status: 200,
    json: makeReply(body.request_id, {
      calibration: {
        state: 'calibrated',
        alpha: 0.05,
        fpr: 0.048,
        peeking_fpr: null,
        calibrated_alpha: 0.05,
        budget: 0.075,
        over_budget: false,
        runs: 3,
        headline: 'calibrated — FPR 4.8% vs nominal α=0.05',
      },
    }),
  }));
  const { mount } = renderInJsdom(makeExplorePayload(liveUrls()), { fetchImpl: impl });
  await sleep(30); // adoption is a microtask chain off the fake fetch
  assert.equal(calls.length, 1, 'exactly one initial recompute');
  const body = calls[0].body;
  assert.equal(body.metric, 'revenue');
  assert.equal(body.method.name, 't-test');
  // minimal params: the configured value equals the spec default → omitted
  assert.deepEqual(body.method.params, {});
  // bonferroni, 2 arms, 0 non-main → effective α = raw 0.05 / C(2,2)=1
  assert.equal(body.alpha, 0.05);
  assert.equal(typeof body.request_id, 'number');
  // chips adopted from the reply (full names)
  const chipTexts = [...mount.querySelectorAll('.abk-live-chip')].map((c) => c.textContent);
  assert.ok(chipTexts.some((t) => t.includes('+0.120')), 'lift chip adopted');
  assert.ok(chipTexts.some((t) => t.includes('91%')), 'power chip adopted');
  assert.equal(
    mount.querySelector('.abk-calibration').getAttribute('data-abk-calibration'),
    'calibrated',
  );
  assert.match(mount.querySelector('.abk-stat').textContent, /14\/14 cutoffs .*14 exact/);
});

test('knob changes send monotonically increasing request ids (Date.now-seeded)', async () => {
  const { impl, calls } = fakeFetch((url, body) => ({
    status: 200,
    json: makeReply(body.request_id),
  }));
  const { mount } = renderInJsdom(makeExplorePayload(liveUrls()), { fetchImpl: impl });
  await sleep(30);
  // click test_type: relative → absolute (fires the debounced recompute)
  const absBtn = [...mount.querySelectorAll('.abk-seg-btn')].find((b) => b.textContent === 'absolute');
  absBtn.click();
  await sleep(200); // > the 130 ms debounce
  assert.equal(calls.length, 2, 'debounced recompute fired');
  assert.deepEqual(calls[1].body.method.params, { test_type: 'absolute' });
  assert.ok(calls[1].body.request_id > calls[0].body.request_id, 'ids are monotonic');
  assert.ok(calls[0].body.request_id > 1e12, 'seeded from Date.now(), not 1');
});

test('identity-changed reply shows the ⚠ different-series chip', async () => {
  const { impl } = fakeFetch((url, body) => ({
    status: 200,
    json: makeReply(body.request_id, { identity_changed: true, method_config_id: 'b'.repeat(16) }),
  }));
  const { mount } = renderInJsdom(makeExplorePayload(liveUrls()), { fetchImpl: impl });
  await sleep(30);
  const chip = mount.querySelector('.abk-identity');
  assert.notEqual(chip.style.display, 'none', 'identity chip visible');
});

// ---------------------------------------------------------------------------
// Apply: the D3 uncalibrated-cost confirm + the success epilogue
// ---------------------------------------------------------------------------

test('Apply on an uncalibrated knob state confirms first, then sends confirm_uncalibrated', async () => {
  const applyReply = {
    saved: '/proj/experiments/explore_exp.yml',
    archived: '/proj/experiments/.history/explore_exp/explore_exp-20260705.yml',
    updated: ['revenue'],
    preserved: [],
    experiment_fields: [],
    orphaned: [{ metric: 'revenue', old_id: 'a'.repeat(16), new_id: 'b'.repeat(16), rows: 14 }],
    orphan_warning: 'orphaned method_config_id series in _ab_results — run `abk clean`',
  };
  const { impl, calls } = fakeFetch((url, body) => {
    if (url.includes('/apply')) return { status: 200, json: applyReply };
    return { status: 200, json: makeReply(body.request_id) };
  });
  const { mount } = renderInJsdom(makeExplorePayload(liveUrls()), { fetchImpl: impl });
  await sleep(30);
  // dirty the comparison
  const absBtn = [...mount.querySelectorAll('.abk-seg-btn')].find((b) => b.textContent === 'absolute');
  absBtn.click();
  await sleep(200);

  const applyBtn = mount.querySelector('.abk-btn-apply');
  applyBtn.click();
  const confirm = mount.querySelector('.abk-confirm');
  assert.notEqual(confirm.style.display, 'none', 'confirm box shown for uncalibrated params');
  assert.match(confirm.textContent, /never passed `abk validate`/);
  assert.ok(!calls.some((c) => c.url.includes('/apply')), 'nothing sent before the confirm');

  [...confirm.querySelectorAll('button')].find((b) => b.textContent === 'Apply anyway').click();
  await sleep(30);
  const applyCall = calls.find((c) => c.url.includes('/apply'));
  assert.ok(applyCall, 'apply sent after the confirm');
  assert.equal(applyCall.body.confirm_uncalibrated, true);
  assert.equal(applyCall.body.comparisons.length, 1);
  assert.equal(applyCall.body.comparisons[0].metric, 'revenue');
  assert.deepEqual(applyCall.body.comparisons[0].method.params, { test_type: 'absolute' });

  const msg = mount.querySelector('.abk-apply-msg');
  assert.match(msg.textContent, /Applied → \/proj\/experiments\/explore_exp\.yml/);
  assert.match(msg.textContent, /updated: revenue/);
  assert.match(msg.textContent, /run `abk clean`/, 'orphan warning + clean hint surfaced');
  assert.match(msg.textContent, /abk run --select explore_exp/);
  assert.ok(applyBtn.disabled, 'one apply per page life');
});

test('Apply with nothing dirty is a friendly noop', async () => {
  const { impl, calls } = fakeFetch((url, body) => ({ status: 200, json: makeReply(body.request_id) }));
  const { mount } = renderInJsdom(makeExplorePayload(liveUrls()), { fetchImpl: impl });
  await sleep(30);
  mount.querySelector('.abk-btn-apply').click();
  assert.match(mount.querySelector('.abk-apply-msg').textContent, /nothing to apply/);
  assert.ok(!calls.some((c) => c.url.includes('/apply')));
});

// ---------------------------------------------------------------------------
// Tier-R interception: covariate-needing method with no cached covariate
// ---------------------------------------------------------------------------

test('switching to a covariate-needing method shows the reload bar instead of recomputing', async () => {
  const { impl, calls } = fakeFetch((url, body) => ({
    status: 200,
    json: makeReply(body.request_id, { method: String(body.method.name) }),
  }));
  const { mount } = renderInJsdom(makeExplorePayload(liveUrls()), { fetchImpl: impl });
  await sleep(30);
  assert.equal(calls.length, 1);
  const cupedBtn = [...mount.querySelectorAll('.abk-seg-btn')].find((b) =>
    b.textContent.startsWith('cuped-t-test'),
  );
  cupedBtn.click();
  await sleep(200);
  assert.equal(calls.length, 1, 'no recompute while the Tier-R change is pending');
  const bar = mount.querySelector('.abk-reloadbar');
  assert.notEqual(bar.style.display, 'none', 'reload bar shown');
  assert.match(bar.textContent, /covariate/);
  // Reload → POSTs the knob state to /reload through the same discipline
  [...bar.querySelectorAll('button')].find((b) => b.textContent.includes('Reload')).click();
  await sleep(30);
  const reloadCall = calls.find((c) => c.url.includes('/reload'));
  assert.ok(reloadCall, '/reload dispatched');
  assert.equal(reloadCall.body.method.name, 'cuped-t-test');
});

// ---------------------------------------------------------------------------
// M9 WP2: switching BACK to the configured CUPED method with reconstructable
// rows recomputes (Tier E) — no reload demand from either needsReload gate
// ---------------------------------------------------------------------------

function cupedConfiguredPayload(momentRows) {
  const payload = makeExplorePayload(liveUrls());
  const surface = payload.explore.metrics.revenue;
  surface.configured = {
    method: 'cuped-t-test',
    params: { test_type: 'relative', covariate_lookback: '14d' },
    method_config_id: 'b'.repeat(16),
    alpha: 0.05,
  };
  surface.cache.covariate_cutoffs = [];
  surface.cache.covariate_moment_rows = momentRows;
  return payload;
}

test('switch-away-then-back to the configured CUPED method recomputes without a reload demand', async () => {
  const { impl, calls } = fakeFetch((url, body) => ({
    status: 200,
    json: makeReply(body.request_id, { method: String(body.method.name) }),
  }));
  const { mount } = renderInJsdom(cupedConfiguredPayload(true), { fetchImpl: impl });
  await sleep(30);
  assert.equal(calls.length, 1, 'initial recompute on the configured cuped method');
  const buttons = () => [...mount.querySelectorAll('.abk-seg-btn')];
  buttons()
    .find((b) => b.textContent.startsWith('t-test'))
    .click();
  await sleep(200);
  assert.equal(calls.length, 2, 'plain method recomputes');
  const cupedBtn = buttons().find((b) => b.textContent.startsWith('cuped-t-test'));
  assert.equal(cupedBtn.textContent, 'cuped-t-test', 'no ↻ badge — rows reconstruct');
  cupedBtn.click();
  await sleep(200);
  // pre-fix the R-tier scan re-demanded the reload the first gate exempted
  assert.equal(calls.length, 3, 'switch-back recomputes via Tier E, no reload gate');
  const bar = mount.querySelector('.abk-reloadbar');
  assert.equal(bar.style.display, 'none', 'reload bar stays hidden');
});

test('switch-back still demands the reload when the rows carry no covariate moments', async () => {
  const { impl, calls } = fakeFetch((url, body) => ({
    status: 200,
    json: makeReply(body.request_id, { method: String(body.method.name) }),
  }));
  const { mount } = renderInJsdom(cupedConfiguredPayload(false), { fetchImpl: impl });
  await sleep(30);
  const buttons = () => [...mount.querySelectorAll('.abk-seg-btn')];
  buttons()
    .find((b) => b.textContent.startsWith('t-test'))
    .click();
  await sleep(200);
  assert.equal(calls.length, 2);
  buttons()
    .find((b) => b.textContent.startsWith('cuped-t-test'))
    .click();
  await sleep(200);
  assert.equal(calls.length, 2, 'no recompute — pre-migration rows cannot reconstruct');
  const bar = mount.querySelector('.abk-reloadbar');
  assert.notEqual(bar.style.display, 'none', 'reload bar shown');
});

// ---------------------------------------------------------------------------
// Auto mode (WP6): server-side /validate, in-session chip flip, knob re-seed
// ---------------------------------------------------------------------------

test('Auto is greyed in the static preview and reports it needs a served session', () => {
  const { mount } = renderInJsdom(makeExplorePayload()); // validate_url null
  const autoBtn = [...mount.querySelectorAll('.abk-mode-btn')].find((b) => b.textContent === 'Auto');
  assert.ok(autoBtn.classList.contains('abk-mode-disabled'), 'Auto greyed without a server route');
  autoBtn.click();
  assert.match(mount.querySelector('.abk-stat').textContent, /needs a served session/);
});

test('Auto mode validates server-side, re-seeds the rail, and greens the live chip', async () => {
  let validated = false;
  const { impl, calls } = fakeFetch((url, body) => {
    if (url.includes('/validate')) {
      validated = true; // subsequent /recompute now sees the greened rows
      return { status: 200, json: makeValidateReply(body.request_id) };
    }
    return {
      status: 200,
      json: makeReply(body.request_id, {
        method: String(body.method.name),
        calibration: validated
          ? makeCalibration({
              state: 'calibrated',
              fpr: 0.049,
              calibrated_alpha: 0.05,
              over_budget: false,
              runs: 1,
              headline: 'calibrated — FPR 4.9% vs nominal α=0.05',
            })
          : makeCalibration(),
      }),
    };
  });
  const { mount } = renderInJsdom(makeExplorePayload(liveUrls()), { fetchImpl: impl });
  await sleep(30);
  assert.equal(
    mount.querySelector('.abk-calibration').getAttribute('data-abk-calibration'),
    'uncalibrated',
  );

  // the user tunes away from the recommendation: test_type relative → absolute
  [...mount.querySelectorAll('.abk-seg-btn')].find((b) => b.textContent === 'absolute').click();
  await sleep(200);
  assert.deepEqual(calls[calls.length - 1].body.method.params, { test_type: 'absolute' });

  // Auto: /validate, re-seed the rail back to the recommendation, follow-up
  // recompute, chip greens in place — no explore restart
  const autoBtn = [...mount.querySelectorAll('.abk-mode-btn')].find((b) => b.textContent === 'Auto');
  assert.ok(!autoBtn.classList.contains('abk-mode-disabled'), 'Auto live with a server route');
  autoBtn.click();
  await sleep(60);

  const validateCall = calls.find((c) => c.url.includes('/validate'));
  assert.ok(validateCall, '/validate dispatched');
  assert.equal(typeof validateCall.body.request_id, 'number');
  assert.ok(validateCall.body.request_id > 1e12, 'request_id shares the Date.now() stale-drop stream');

  // the follow-up recompute carries the RE-SEEDED (recommended) params, not absolute
  const lastRecompute = [...calls].reverse().find((c) => c.url.includes('/recompute'));
  assert.deepEqual(lastRecompute.body.method.params, {}, 're-seeded to relative (default → minimal {})');
  const relSeg = [...mount.querySelectorAll('.abk-seg-btn')].find((b) => b.textContent === 'relative');
  assert.ok(relSeg.classList.contains('on'), 'the rail re-seeded to the recommended relative test_type');

  // the D3 chip is green in-session
  assert.equal(
    mount.querySelector('.abk-calibration').getAttribute('data-abk-calibration'),
    'calibrated',
  );
  assert.match(mount.querySelector('.abk-calibration').textContent, /calibrated — FPR 4.9%/);
});

test('Auto after a just-armed knob edit is not aborted by the stale debounce timer', async () => {
  // F2 regression: a knob edit arms the 130ms debounce; clicking Auto within
  // that window must flush the pending recompute so the stale timer cannot fire
  // mid-validate and abort the in-flight /validate (which would drop the re-seed
  // + chip-green). The fake /validate resolves AFTER the debounce and honors
  // abort, so a regressed pokeValidate leaves the chip uncalibrated.
  const calls = [];
  let validated = false;
  const impl = (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, body });
    if (url.includes('/validate')) {
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          validated = true; // only a COMPLETED validate greens the rows
          resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(makeValidateReply(body.request_id)),
            text: () => Promise.resolve(''),
          });
        }, 250);
        if (init.signal) {
          init.signal.addEventListener('abort', () => {
            clearTimeout(timer);
            const err = new Error('aborted');
            err.name = 'AbortError';
            reject(err);
          });
        }
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve(
          makeReply(body.request_id, {
            method: String(body.method.name),
            calibration: validated
              ? makeCalibration({
                  state: 'calibrated',
                  fpr: 0.049,
                  calibrated_alpha: 0.05,
                  over_budget: false,
                  runs: 1,
                  headline: 'calibrated — FPR 4.9% vs nominal α=0.05',
                })
              : makeCalibration(),
          }),
        ),
      text: () => Promise.resolve(''),
    });
  };
  const { mount } = renderInJsdom(makeExplorePayload(liveUrls()), { fetchImpl: impl });
  await sleep(30);
  // edit a knob (arms the 130ms debounce), then click Auto within that window
  [...mount.querySelectorAll('.abk-seg-btn')].find((b) => b.textContent === 'absolute').click();
  [...mount.querySelectorAll('.abk-mode-btn')].find((b) => b.textContent === 'Auto').click();
  await sleep(500); // past both the debounce (130ms) and the validate delay (250ms)
  assert.ok(calls.some((c) => c.url.includes('/validate')), '/validate dispatched');
  assert.equal(
    mount.querySelector('.abk-calibration').getAttribute('data-abk-calibration'),
    'calibrated',
    'the validate reply was adopted (not aborted) → chip greened in place',
  );
  assert.match(mount.querySelector('.abk-calibration').textContent, /calibrated — FPR 4.9%/);
});

test('a stale (409) Auto validate yields quietly without greening the chip', async () => {
  const { impl, calls } = fakeFetch((url, body) => {
    if (url.includes('/validate')) return { status: 409, json: { stale: true, request_id: body.request_id } };
    return { status: 200, json: makeReply(body.request_id) };
  });
  const { mount } = renderInJsdom(makeExplorePayload(liveUrls()), { fetchImpl: impl });
  await sleep(30);
  [...mount.querySelectorAll('.abk-mode-btn')].find((b) => b.textContent === 'Auto').click();
  await sleep(40);
  assert.ok(calls.some((c) => c.url.includes('/validate')), '/validate attempted');
  assert.match(mount.querySelector('.abk-stat').textContent, /another explore tab is ahead/);
  assert.equal(
    mount.querySelector('.abk-calibration').getAttribute('data-abk-calibration'),
    'uncalibrated',
    'a dropped validate never greens the chip',
  );
});
