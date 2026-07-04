// @ts-nocheck — assertion-style test code; the payload fixtures live in
// fixtures.mjs, which IS checked against the lockstep contract.
// jsdom smoke test for the committed report bundle.
//
// Loads abkit/reporting/assets/report.js (the COMMITTED artifact — run
// `npm run build` first; CI rebuilds and diffs before running this) into a
// jsdom window and renders fixture payloads, asserting the section skeleton,
// the §4 peeking-honesty marker classes, payload-string escaping, and the
// empty-state / SRM-fail branches. jsdom has no canvas 2D context, so charts
// exercise the abk-chart-fallback self-defense path — canvas drawing itself
// is covered by eye + the WP10 e2e, per the donor stance that the bundle is
// an opaque committed asset.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

import { makePayload, makePoint } from './fixtures.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const BUNDLE = readFileSync(
  path.join(here, '..', '..', 'abkit', 'reporting', 'assets', 'report.js'),
  'utf8',
);

function renderInJsdom(payload) {
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="abk-report"></div></body></html>', {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  });
  dom.window.eval(BUNDLE);
  const mount = dom.window.document.getElementById('abk-report');
  dom.window.__ABK_REPORT__.render(payload, mount);
  return { dom, mount };
}

test('bundle exposes the window global', () => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
  dom.window.eval(BUNDLE);
  assert.equal(typeof dom.window.__ABK_REPORT__, 'object');
  assert.equal(typeof dom.window.__ABK_REPORT__.render, 'function');
});

test('renders the full section skeleton for a healthy WIN payload', () => {
  const { dom, mount } = renderInJsdom(makePayload());
  const q = (sel) => mount.querySelector(sel);
  assert.ok(mount.classList.contains('abk-report'));
  assert.ok(q('.abk-header'), 'header');
  assert.match(q('.abk-title').textContent, /acme · report_exp/);
  assert.ok(q('.abk-chip.abk-srm'), 'SRM chip present');
  assert.equal(q('.abk-chip.abk-srm').getAttribute('data-abk-srm'), 'ok');
  assert.ok(!q('.abk-srm-fail'), 'no SRM-fail marker on a healthy cohort');
  assert.match(q('.abk-calibration').textContent, /uncalibrated — run `abk validate` \(M4\)/);
  assert.equal(q('.abk-verdict').getAttribute('data-abk-verdict'), 'WIN');
  assert.match(q('.abk-verdict-word').textContent, /WIN/);
  assert.ok(q('.abk-rationale li'), 'rationale rendered');
  assert.ok(q('.abk-metric'), 'metric section');
  assert.ok(q('.abk-chart-main'), 'stabilization chart wrapper');
  assert.ok(q('.abk-chart-fallback'), 'jsdom (no canvas 2D) exercises the fallback');
  assert.equal(mount.querySelectorAll('.abk-mini').length, 4, 'four small multiples');
  assert.ok(q('.abk-audit table'), 'audit table');
  assert.equal(mount.querySelectorAll('.abk-audit tbody tr').length, 14);
  // at-horizon payload: no pre-horizon note
  assert.ok(!q('.abk-prehorizon'));
  // style injected once, scoped
  assert.ok(dom.window.document.head.querySelector('style[data-abk-report]'));
});

test('daily cadence hides the look counter; sub-day shows it', () => {
  const { mount: daily } = renderInJsdom(makePayload());
  assert.ok(!daily.querySelector('.abk-look'));
  const { mount: subday } = renderInJsdom(makePayload({ cadence_seconds: 3600 }));
  assert.match(subday.querySelector('.abk-look').textContent, /look 14 \/ ~14 planned/);
});

test('SRM failure renders the red gate chip with the abk-srm-fail marker', () => {
  const payload = makePayload({
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
  assert.match(chip.textContent, /SRM FAILED \(observed 0\.62\/0\.38 vs expected 0\.50\/0\.50, χ² p<0\.001\) — effects untrustworthy/);
});

test('pre-horizon latest cutoff renders the abk-prehorizon note', () => {
  const series = Array.from({ length: 5 }, (_, i) => makePoint(i + 1));
  const payload = makePayload();
  payload.metrics[0].pairs[0].series = series;
  payload.verdicts[0].is_horizon = false;
  payload.verdicts[0].verdict = 'INCONCLUSIVE';
  const { mount } = renderInJsdom(payload);
  const note = mount.querySelector('.abk-note.abk-prehorizon');
  assert.ok(note, 'abk-prehorizon marker present');
  assert.match(note.textContent, /not peeking-valid/);
  assert.equal(mount.querySelector('[data-abk-prehorizon]').getAttribute('data-abk-prehorizon'), '1');
});

test('insufficient_data cutoffs render the abk-insufficient note and grey audit rows', () => {
  const payload = makePayload();
  const series = payload.metrics[0].pairs[0].series;
  series[2] = makePoint(3, { ins: 1, e: null, lo: null, hi: null, p: null, rj: null, mde: null, s1: 40, s2: 38 });
  series[3] = makePoint(4, { ins: 1, e: null, lo: null, hi: null, p: null, rj: null, mde: null, s1: 80, s2: 79 });
  const { mount } = renderInJsdom(payload);
  const note = mount.querySelector('.abk-note.abk-insufficient');
  assert.ok(note, 'abk-insufficient marker present');
  assert.match(note.textContent, /2 insufficient-data cutoffs greyed — counts \+ SRM only/);
  assert.equal(mount.querySelectorAll('.abk-audit tr.abk-insufficient').length, 2);
});

test('empty experiment payload renders empty states, never throws', () => {
  const payload = makePayload({
    verdicts: [],
    period: { start: Date.UTC(2026, 0, 1), end: 0, horizon: Date.UTC(2026, 0, 15) },
    srm: { flag: false, pvalue: null, observed: { control: 0, treatment: 0 }, expected: { control: 0.5, treatment: 0.5 } },
    look: { n: 0, planned: 14 },
  });
  payload.metrics[0].pairs[0].series = [];
  const { mount } = renderInJsdom(payload);
  assert.match(mount.querySelector('.abk-verdicts .abk-empty').textContent, /No verdict yet/);
  assert.match(mount.querySelector('.abk-pair .abk-empty').textContent, /No persisted cutoffs/);
  assert.equal(mount.querySelector('.abk-chip.abk-srm').getAttribute('data-abk-srm'), 'na');
  assert.match(mount.querySelector('.abk-meta').textContent, /no cutoffs yet/);
});

test('payload strings are escaped — markup in descriptions/warnings stays text', () => {
  const hostile = '<img src=x onerror="window.__pwned=1"> & </script>';
  const payload = makePayload({ description: hostile, warnings: [hostile] });
  payload.metrics[0].description = hostile;
  payload.verdicts[0].rationale = [hostile];
  const { dom, mount } = renderInJsdom(payload);
  assert.equal(mount.querySelectorAll('img').length, 0, 'no injected elements');
  assert.equal(dom.window.__pwned, undefined);
  assert.match(mount.querySelector('.abk-desc').textContent, /<img src=x/);
  assert.match(mount.querySelector('.abk-warning').textContent, /<img src=x/);
});

test('guardrail regression and caveats render loud', () => {
  const payload = makePayload();
  payload.verdicts[0].verdict = 'INCONCLUSIVE';
  payload.verdicts[0].caveats = ['covers 43% of a weekly cycle'];
  payload.verdicts[0].guardrails = [
    {
      metric: 'latency',
      pair: { c: 'control', t: 'treatment' },
      regressed: true,
      effect: -0.12,
      desired_direction: 'increase',
    },
  ];
  const { mount } = renderInJsdom(payload);
  assert.match(mount.querySelector('.abk-caveat').textContent, /weekly cycle/);
  const guardrail = mount.querySelector('.abk-guardrail-regressed');
  assert.ok(guardrail);
  assert.match(guardrail.textContent, /REGRESSED/);
});

test('calibration block tolerates the M4 shape', () => {
  const payload = makePayload({ calibration: { fpr: 0.062, headline: 'FPR 6.2% vs nominal 5%' } });
  const { mount } = renderInJsdom(payload);
  const chip = mount.querySelector('.abk-calibration');
  assert.equal(chip.getAttribute('data-abk-calibration'), 'present');
  assert.match(chip.textContent, /FPR 6\.2%/);
});
