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

import {
  makeCalibration,
  makeMultiArmDecisionPayload,
  makePayload,
  makePoint,
  makeThreeArmPayload,
} from './fixtures.mjs';

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

test('sub-day SRM failure names the anytime-valid gate, not χ² (WP5)', () => {
  const payload = makePayload({
    srm: {
      flag: true,
      pvalue: 0.0004,
      observed: { control: 5000, treatment: 5000 }, // re-balanced, yet FAILED (past imbalance)
      expected: { control: 0.5, treatment: 0.5 },
      kind: 'sequential_multinomial',
    },
  });
  const { mount } = renderInJsdom(payload);
  const chip = mount.querySelector('.abk-srm-fail');
  assert.ok(chip, 'abk-srm-fail marker present');
  assert.match(chip.textContent, /anytime-valid p<0\.001/);
  assert.ok(!chip.textContent.includes('χ²'), 'the e-process gate is not labelled χ²');
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

test('early decisive verdict promotes the weekly-cycle caveat to a chip (WP4 §6.5)', () => {
  const payload = makePayload();
  const v = payload.verdicts[0];
  v.is_horizon = false;
  v.weekly_cycle_pct = 5 / 7;
  // Both caveats are present in the data; only the weekly one is promoted.
  v.caveats = [
    'covers 71% of a weekly cycle — day-of-week effects may not be represented',
    'guardrail regressed — verdict kept under guardrail_policy: warn',
  ];
  const { mount } = renderInJsdom(payload);
  const chip = mount.querySelector('.abk-verdict-head .abk-weekly-chip');
  assert.ok(chip, 'weekly-cycle chip present in the verdict head');
  assert.equal(chip.getAttribute('data-abk-weekly'), '1');
  assert.match(chip.textContent, /covers 71% of a weekly cycle/);
  // The promoted caveat is filtered out of the caveat list…
  const caveatText = Array.from(mount.querySelectorAll('.abk-caveat')).map((li) => li.textContent);
  assert.ok(!caveatText.some((t) => /of a weekly cycle/.test(t)), 'weekly caveat dropped from the list');
  // …every other caveat still renders.
  assert.ok(caveatText.some((t) => /guardrail regressed/.test(t)), 'non-weekly caveat kept');
});

test('a verdict without weekly_cycle_pct renders no weekly chip', () => {
  // The default WIN fixture is at-horizon with no weekly_cycle_pct.
  const { mount } = renderInJsdom(makePayload());
  assert.ok(!mount.querySelector('.abk-weekly-chip'), 'no weekly chip when the field is absent');
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
  // no matrix_rows -> the chip lights but the full matrix section stays absent
  assert.ok(!mount.querySelector('.abk-calibration-matrix'), 'no matrix without rows');
});

test('an all-failed A/A matrix does not read as green "calibrated"', () => {
  // every cell failed -> build_calibration_block still returns a non-null block with
  // fpr=null; the chip must NOT claim the green success state (m4 exit-gate review).
  const calibration = makeCalibration({
    fpr: null,
    peeking_fpr: null,
    headline: 'nominal α 5.0% · single-look FPR —',
    matrix_rows: [
      {
        metric: 'revenue',
        method: 'quarantined-method',
        method_config_id: 'e'.repeat(16),
        fpr: null,
        single_look_fpr: null,
        peeking_fpr: null,
        power: null,
        achieved_mde: null,
        coverage: null,
        effect_exaggeration: null,
        alpha: 0.05,
        budget: 0.075,
        over_budget: false,
        recommended: false,
        rationale: null,
        verdict: 'quarantined-method on revenue: failed (…)',
        status: 'failed',
        iterations: 2000,
        injected_effect: null,
        peeking_curve: null,
        note: null,
      },
    ],
  });
  const { mount } = renderInJsdom(makePayload({ calibration }));
  const chip = mount.querySelector('.abk-calibration');
  assert.equal(chip.getAttribute('data-abk-calibration'), 'failed');
  assert.ok(!chip.classList.contains('abk-calibrated'), 'no green success class when nothing measured');
  // the matrix section still renders so the analyst sees WHICH cells failed
  assert.ok(mount.querySelector('.abk-calibration-matrix'), 'failed rows stay visible');
});

test('the A/A calibration matrix renders rows, budget colouring, and the recommended cell', () => {
  const payload = makePayload({ calibration: makeCalibration() });
  const { mount } = renderInJsdom(payload);

  const section = mount.querySelector('.abk-calibration-matrix');
  assert.ok(section, 'the matrix section is present');
  assert.match(section.querySelector('.abk-cal-title').textContent, /A\/A false-positive matrix/);
  assert.match(section.querySelector('.abk-cal-headline').textContent, /peeking FPR 14\.0%/);

  const rows = section.querySelectorAll('[data-abk-calibration-row]');
  assert.equal(rows.length, 2, 'one row per scored cell');

  // the recommended cell is marked + badged + sorts first
  const rec = section.querySelector('[data-abk-calibration-row="recommended"]');
  assert.ok(rec, 'the recommended row is tagged');
  assert.ok(rec.classList.contains('abk-cal-rec'));
  assert.match(rec.querySelector('.abk-cal-badge').textContent, /Recommended/);
  assert.match(rec.textContent, /highest power among methods with FPR within budget/);
  assert.match(rec.querySelector('.abk-cal-fpr-ok').textContent, /5\.2%/); // in budget -> green

  // the over-budget cell is coloured critical and shows its subsample note
  const over = section.querySelector('.abk-cal-fpr-over');
  assert.ok(over, 'the over-budget FPR cell is flagged');
  assert.match(over.textContent, /11\.0%/);
  assert.match(section.textContent, /5\/40 looks scored/);
  assert.match(section.textContent, /do not use/);

  // the chip mirrors the headline (calibrated, not the empty state)
  const chip = mount.querySelector('.abk-calibration');
  assert.equal(chip.getAttribute('data-abk-calibration'), 'present');
  assert.match(chip.textContent, /peeking FPR 14\.0%/);
});

test('the composed FWER/FDR family band renders above the matrix (D9/WP8)', () => {
  const calibration = makeCalibration({
    family: {
      correction: 'bonferroni',
      fwer: 0.048,
      fdr: 0.048,
      budget: 0.075,
      over_budget: false,
      n_metrics: 3,
      n_null_metrics: 3,
      metrics: ['revenue', 'signups', 'retention'],
      iterations: 2000,
      valid_iterations: 2000,
      verdict: 'composed bonferroni over 3 metrics: family-wise error 4.8% (budget 7.5%) within budget; FDR 4.8%',
    },
  });
  const { mount } = renderInJsdom(makePayload({ calibration }));
  const band = mount.querySelector('.abk-cal-family');
  assert.ok(band, 'the composed-family band is present');
  assert.equal(band.getAttribute('data-abk-family'), 'ok');
  assert.match(band.querySelector('.abk-cal-family-title').textContent, /Composed multiple testing · bonferroni · 3 metrics/);
  assert.match(band.textContent, /family-wise error/);
  assert.match(band.textContent, /4\.8%/);
  assert.match(band.textContent, /false-discovery rate/);
  assert.match(band.querySelector('.abk-cal-family-verdict').textContent, /within budget/);
});

test('an over-budget composed family is flagged critical', () => {
  const calibration = makeCalibration({
    family: {
      correction: 'bonferroni',
      fwer: 0.12,
      fdr: 0.12,
      budget: 0.075,
      over_budget: true,
      n_metrics: 3,
      metrics: ['a', 'b', 'c'],
      verdict: 'composed bonferroni over 3 metrics: family-wise error 12.0% OVER budget',
    },
  });
  const { mount } = renderInJsdom(makePayload({ calibration }));
  const band = mount.querySelector('.abk-cal-family');
  assert.equal(band.getAttribute('data-abk-family'), 'over');
  assert.ok(band.querySelector('.abk-cal-fpr-over'), 'the FWER stat is coloured critical');
  assert.match(band.textContent, /12\.0%/);
});

test('the arms line names the baseline arm when a control is declared (m14 DEC-1)', () => {
  // absent `control` — every payload baked before 0.9.0 — keeps the old
  // sentence, so an existing report renders byte-identically
  const { mount: legacy } = renderInJsdom(makePayload());
  assert.match(legacy.querySelector('.abk-arms').textContent, /first = control/);

  // a control that IS the first arm is the positional default written out:
  // same sentence, because it is still true
  const positional = makePayload();
  positional.arms = ['a', 'b', 'c'];
  positional.control = 'a';
  const { mount: def } = renderInJsdom(positional);
  assert.match(def.querySelector('.abk-arms').textContent, /first = control/);

  // a declared non-first control: the old sentence would name `a` as the
  // baseline directly above pair blocks reading "c vs a"
  const declared = makePayload();
  declared.arms = ['a', 'b', 'c'];
  declared.control = 'c';
  const { mount } = renderInJsdom(declared);
  const line = mount.querySelector('.abk-arms').textContent;
  assert.match(line, /control: c/);
  assert.ok(!/first = control/.test(line), line);
});

// ----------------------------------------------------------------------------
// m14 DEC-3 — the decision layer on the page
// ----------------------------------------------------------------------------

test('a treatment-pair verdict renders labeled, and never among the ship decisions', () => {
  const { mount } = renderInJsdom(makeMultiArmDecisionPayload());

  // the two ship decisions stay in the headline area; the arm-vs-arm evidence
  // is one collapsed group below them
  const headline = [...mount.querySelectorAll('.abk-verdicts > .abk-verdict')];
  assert.equal(headline.length, 2, 'only the control-anchored cards are headline cards');
  assert.ok(!headline.some((c) => c.querySelector('[data-abk-role]')), 'no role chip on a ship card');

  const evidence = mount.querySelector('.abk-evidence');
  assert.ok(evidence, 'the arm-vs-arm group exists');
  assert.equal(evidence.tagName, 'DETAILS');
  assert.ok(!evidence.open, 'collapsed by default — C(N,2) cards are not the headline');
  assert.match(evidence.querySelector('summary').textContent, /arm-vs-arm evidence — 1 comparison/);

  const card = evidence.querySelector('.abk-verdict');
  assert.equal(card.getAttribute('data-abk-verdict'), 'INCONCLUSIVE');
  assert.match(card.querySelector('.abk-verdict-target').textContent, /treatment vs treatment_b/);
  const chip = card.querySelector('[data-abk-role]');
  assert.ok(chip, 'the role chip is what stops "WIN (B vs C)" reading as a ship recommendation');
  assert.match(chip.textContent, /not a ship decision/);
});

test('the cross-arm overview names the leader the ROLLUP chose, not the first winner', () => {
  const { mount } = renderInJsdom(makeMultiArmDecisionPayload());
  const panel = mount.querySelector('.abk-rollup');
  assert.ok(panel, 'the overview renders at 3+ arms');
  assert.equal(panel.getAttribute('data-abk-separation'), 'co_leaders');
  // `treatment` is declared first and also beat control — the leader is the
  // arm the readout picked, which is the LAST declared one
  assert.match(panel.querySelector('.abk-rollup-leader').textContent, /leader: treatment_b/);
  assert.equal(panel.querySelector('.abk-rollup-state').textContent, 'co-leaders');
  assert.match(panel.querySelector('.abk-rollup-line').textContent, /co-leaders on this metric/);

  const rows = [...panel.querySelectorAll('tbody tr')].map((tr) =>
    [...tr.querySelectorAll('td')].map((td) => td.textContent),
  );
  assert.equal(rows.length, 3, 'the baseline plus one row per treatment');
  assert.match(rows[0][0], /^control/);
  assert.equal(rows[0][3], 'baseline');
  assert.equal(rows[0][4], '1100', "the control's n is s1, not the treatment's s2");
  assert.match(rows[1][0], /^treatmentnot separated/);
  assert.match(rows[2][0], /^treatment_bleader/);
  assert.equal(rows[2][3], 'WIN');
  assert.equal(rows[2][4], '900', "each arm's n is the latest cutoff of its control pair");
  assert.ok(panel.querySelector('tr.abk-arm-leader'), 'the leader row is marked');
});

test('the pair selector defaults to the control-anchored pairs and reveals on demand', () => {
  const { mount } = renderInJsdom(makeMultiArmDecisionPayload());
  const picker = mount.querySelector('.abk-pair-picker');
  assert.ok(picker, 'the selector renders at 3+ arms');
  const toggles = [...picker.querySelectorAll('input[type=checkbox]')];
  assert.deepEqual(
    toggles.map((t) => t.checked),
    [true, true, false],
    'the treatment pair starts collapsed — block count grows as C(N,2)',
  );

  const blocks = [...mount.querySelectorAll('.abk-metric .abk-pair')];
  assert.equal(blocks.length, 3, 'every declared pair is BUILT, only the display is deferred');
  assert.equal(blocks[2].hidden, true);

  toggles[2].checked = true;
  toggles[2].dispatchEvent(new mount.ownerDocument.defaultView.Event('change'));
  assert.equal(blocks[2].hidden, false, 'toggling reveals the block');

  toggles[0].checked = false;
  toggles[0].dispatchEvent(new mount.ownerDocument.defaultView.Event('change'));
  assert.equal(blocks[0].hidden, true, 'and hides one that was shown');
});

test('the leaders chip reports disagreement, and never appears at two arms', () => {
  const split = makeMultiArmDecisionPayload({
    leaders_agree: false,
    rollups: [
      { metric: 'revenue', leader: 'treatment', indistinguishable: [], separation: 'separated',
        losers: [], guardrail_regressed: [], rationale: ['treatment beat control'], caveats: [] },
      { metric: 'orders', leader: 'treatment_b', indistinguishable: [], separation: 'separated',
        losers: [], guardrail_regressed: [], rationale: ['treatment_b beat control'], caveats: [] },
    ],
  });
  const { mount } = renderInJsdom(split);
  const chip = mount.querySelector('[data-abk-leaders]');
  assert.equal(chip.getAttribute('data-abk-leaders'), 'split');
  assert.match(chip.textContent, /DIFFERENT leaders/);

  // §0.2 point 3: at two arms both main metrics can only name the same single
  // treatment, so a chip here would appear on a page 0.8.0 rendered without one
  const twoArm = makePayload({ leaders_agree: true });
  const { mount: plain } = renderInJsdom(twoArm);
  assert.equal(plain.querySelector('[data-abk-leaders]'), null);
});

test('a two-arm report grows no cross-arm affordance (§0.2 point 3)', () => {
  const twoArm = makePayload({
    rollups: [
      { metric: 'revenue', leader: 'treatment', indistinguishable: [], separation: 'separated',
        losers: [], guardrail_regressed: [], rationale: ['treatment beat control'], caveats: [] },
    ],
    leaders_agree: null,
  });
  const { mount } = renderInJsdom(twoArm);
  assert.equal(mount.querySelector('.abk-rollup'), null, 'no overview');
  assert.equal(mount.querySelector('.abk-pair-picker'), null, 'no pair selector');
  assert.equal(mount.querySelector('.abk-evidence'), null, 'no evidence group');
  assert.equal(mount.querySelector('[data-abk-role]'), null, 'no role chip');
  assert.equal(mount.querySelector('.abk-pair').hidden, false, 'the one pair block is visible');
});

test('a pre-0.9.0 payload without rollups or roles renders as it always did', () => {
  const { mount } = renderInJsdom(makeThreeArmPayload());
  assert.equal(mount.querySelector('.abk-rollup'), null, 'no overview without a rollup');
  assert.equal(mount.querySelector('.abk-evidence'), null, 'a roleless verdict is a ship decision');
  assert.equal(mount.querySelectorAll('.abk-verdicts > .abk-verdict').length, 2);
  // the selector is a presentation affordance and does not need a rollup
  assert.ok(mount.querySelector('.abk-pair-picker'), 'the selector still applies');
});

test('the overview never states a finding the readout refused to state', () => {
  // `no_leader` is ONE state with three readouts — nobody won, the SRM gate
  // failed, or nothing could be judged yet — and the readout words each
  // differently because a rollup must not speak over a gate. A static "no arm
  // beat the control" chip beside a failed SRM gate is that failure at the
  // renderer, so the chip is suppressed and the readout's own sentence carries.
  const srmFailed = makeMultiArmDecisionPayload({
    rollups: [
      { metric: 'revenue', leader: null, indistinguishable: [], separation: 'no_leader',
        losers: [], guardrail_regressed: [],
        rationale: ['SRM failed — no arm can be judged against control on revenue'],
        caveats: [] },
    ],
  });
  const { mount } = renderInJsdom(srmFailed);
  const panel = mount.querySelector('.abk-rollup');
  assert.equal(panel.getAttribute('data-abk-separation'), 'no_leader');
  assert.match(panel.querySelector('.abk-rollup-leader').textContent, /^no leader$/);
  assert.equal(panel.querySelector('.abk-rollup-state'), null, 'no separation chip without a leader');
  assert.ok(!/no arm beat the control/.test(panel.textContent), panel.textContent);
  assert.match(panel.textContent, /SRM failed/);
});

test('an arm nobody could compare is not tagged a co-leader', () => {
  // `indistinguishable` merges "compared and undecided" with "could not be
  // compared" (demoted rows, pre-horizon); asserting co-leadership over the
  // second is a positive claim of measured parity about a pair nobody measured
  const untested = makeMultiArmDecisionPayload({
    rollups: [
      { metric: 'revenue', leader: 'treatment_b', indistinguishable: ['treatment'],
        separation: 'untested', losers: [], guardrail_regressed: [],
        rationale: ['treatment_b beat control; treatment could not be compared against it'],
        caveats: [] },
    ],
  });
  const { mount } = renderInJsdom(untested);
  const panel = mount.querySelector('.abk-rollup');
  assert.equal(panel.querySelector('.abk-rollup-state').textContent, 'separation untested');
  assert.ok(!/co-leader/.test(panel.textContent), panel.textContent);
  assert.match(panel.textContent, /not separated/);
});

/**
 * jsdom has no 2D context, so every chart in the suite above takes the
 * `abk-chart-fallback` path and `charts` is EMPTY — which makes the pair
 * selector's re-fit-on-reveal loop unreachable, i.e. a mechanism no assertion
 * could see (deleting it left the suite green). This harness stubs the context
 * and a layout in which a canvas inside a `hidden` ancestor measures zero,
 * which is the browser behaviour the loop exists for.
 */
function renderWithCanvas(payload) {
  const dom = new JSDOM('<!doctype html><html><head></head><body><div id="abk-report"></div></body></html>', {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  });
  const win = dom.window;
  const ctx = new Proxy(
    {},
    {
      get(target, prop) {
        if (prop === 'measureText') return () => ({ width: 10 });
        if (prop === 'createPattern') return () => null;
        if (prop === 'createLinearGradient') return () => ({ addColorStop() {} });
        if (prop in target) return target[prop];
        return () => {};
      },
      set(target, prop, value) {
        target[prop] = value;
        return true;
      },
    },
  );
  win.HTMLCanvasElement.prototype.getContext = () => ctx;
  const laidOut = (node) => {
    for (let el = node; el; el = el.parentElement) if (el.hidden) return false;
    return true;
  };
  for (const [prop, size] of [['clientWidth', 600], ['clientHeight', 340]]) {
    Object.defineProperty(win.HTMLElement.prototype, prop, {
      configurable: true,
      get() {
        return laidOut(this) ? size : 0;
      },
    });
  }
  win.eval(BUNDLE);
  const mount = win.document.getElementById('abk-report');
  win.__ABK_REPORT__.render(payload, mount);
  return { dom, mount };
}

test('revealing a collapsed pair re-fits its charts (a hidden canvas measures zero)', () => {
  const { mount } = renderWithCanvas(makeMultiArmDecisionPayload());
  const blocks = [...mount.querySelectorAll('.abk-metric .abk-pair')];
  const widths = (block) => [...block.querySelectorAll('canvas')].map((c) => c.width);

  assert.ok(widths(blocks[0]).length > 0, 'the stub really produced charts');
  assert.ok(
    widths(blocks[0]).every((w) => w > 0),
    'a shown block is fitted by the initial resize pass',
  );
  assert.ok(
    widths(blocks[2]).every((w) => w === 0),
    'a hidden block fits to zero — this is what makes the reveal re-fit necessary',
  );

  const toggles = [...mount.querySelectorAll('.abk-pair-picker input[type=checkbox]')];
  toggles[2].checked = true;
  toggles[2].dispatchEvent(new mount.ownerDocument.defaultView.Event('change'));

  assert.ok(
    widths(blocks[2]).every((w) => w > 0),
    'without the re-fit the revealed charts stay blank until the window resizes',
  );
  assert.ok(widths(blocks[1]).every((w) => w > 0), 'the other blocks are untouched');
});

test('the selector opens the pairs that HAVE data when the declared ones are empty', () => {
  // DEC-1's documented window: a control declared on a running experiment
  // re-orients the pairs, so until the next `abk run` the control-anchored
  // blocks are present-but-empty while the old treatment pairs hold the series.
  // Defaulting on orientation alone opens three "no cutoffs yet" boxes and
  // hides every chart with data — worse than the page 0.8.0 rendered.
  const reoriented = makeMultiArmDecisionPayload();
  const [, withData] = reoriented.metrics[0].pairs;
  reoriented.metrics[0].pairs = [
    { c: 'control', t: 'treatment', series: [], diag: null },
    { c: 'control', t: 'treatment_b', series: [], diag: null },
    { c: 'treatment', t: 'treatment_b', series: withData.series, diag: null },
  ];
  const { mount } = renderInJsdom(reoriented);
  const blocks = [...mount.querySelectorAll('.abk-metric .abk-pair')];
  assert.deepEqual(
    blocks.map((b) => b.hidden),
    [true, true, false],
    'the block with a series is the one on screen',
  );
});

test('the leaders chip also reports agreement', () => {
  const agreed = makeMultiArmDecisionPayload({
    leaders_agree: true,
    rollups: [
      { metric: 'revenue', leader: 'treatment_b', indistinguishable: [], separation: 'separated',
        losers: [], guardrail_regressed: [], rationale: ['treatment_b beat control'], caveats: [] },
      { metric: 'orders', leader: 'treatment_b', indistinguishable: [], separation: 'separated',
        losers: [], guardrail_regressed: [], rationale: ['treatment_b beat control'], caveats: [] },
    ],
  });
  const { mount } = renderInJsdom(agreed);
  const chip = mount.querySelector('[data-abk-leaders]');
  assert.equal(chip.getAttribute('data-abk-leaders'), 'agree');
  assert.match(chip.textContent, /agree on the leader/);
  assert.ok(chip.classList.contains('abk-leaders-agree'));
});
