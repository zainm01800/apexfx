// Grader test gate: prove the LIVE outcome graders (public/history.js +
// scripts/proximity-watch.mjs) grade pessimistically — stop-first, matching both
// backtesters (layer2 'span: stop wins over target'):
//   - one bar spanning BOTH TP and SL => 'ambiguous' (NOT the old flattering tp_hit)
//   - single-barrier bars still grade tp_hit / sl_hit
//   - the first resolving bar wins (no look-ahead)
//   - the entry-fill gate still applies (no fill, no grade)
// 'ambiguous' must be excluded from win-rate numerators AND denominators downstream.
// Run: node test/backtest/grader.test.cjs
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');

let pass = 0, fail = 0;
const ok = (name, cond) => { if (cond) { pass++; } else { fail++; console.error('  ✗ ' + name); } };

// ── Extract the LIVE gradeRow from each file (anchored slices, DOM-free) ──────
function sliceOf(file, from, to) {
  const src = fs.readFileSync(path.join(ROOT, file), 'utf8');
  const a = src.indexOf(from); const b = src.indexOf(to, a);
  assert(a >= 0 && b > a, `anchors not found in ${file}: ${from} .. ${to}`);
  return src.slice(a, b);
}

// history.js: gradeRow depends on verdictDir, rowTs, utcDay, TF_SECONDS/STYLE_RES
// (resolutionFor) and entryBounds.
const historySrc =
  sliceOf('public/history.js', 'function verdictDir', 'function outcomeLabel') +
  sliceOf('public/history.js', 'function rowTs', 'function localTimezone') +
  sliceOf('public/history.js', 'const STYLE_RES', 'async function resolveIfPending') +
  sliceOf('public/history.js', 'function entryBounds', '// Classify a watchlist row');
const historyGrader = new Function(historySrc + '\nreturn { gradeRow, resolutionFor };')();

// proximity-watch.mjs: everything gradeRow needs sits in one contiguous block.
const proxSrc = sliceOf('scripts/proximity-watch.mjs', 'const entryBounds', 'async function resolveAndCheckTrade');
const proxGrader = new Function(proxSrc + '\nreturn { gradeRow, resolutionFor };')();

// ── Fixtures ──────────────────────────────────────────────────────────────────
// Daily bars AFTER the entry day (both graders gate daily bars on a strictly later
// UTC calendar day). asset_type 'Forex' skips the Stock/ETF opening-bar exclusion.
const DAY = 86400;
const T0 = Date.UTC(2026, 6, 15) / 1000;               // entry: 2026-07-15 00:00 UTC
const B = (o, h, l, c, dayOffset) => ({ time: T0 + dayOffset * DAY, open: o, high: h, low: l, close: c, volume: 1000 });
const mkRow = (over) => ({
  id: 'TEST_1752643200000', symbol: 'EUR/USD', asset_type: 'Forex',
  verdict: 'BUY', price: 100, entry_zone: '100',
  target_price: 110, stop_loss: 90,
  created_at: '2026-07-15T00:00:00Z',
  ...over,
});

for (const [name, g] of [['history.js', historyGrader], ['proximity-watch.mjs', proxGrader]]) {
  const grade = (row, candles) => g.gradeRow(row, g.resolutionFor(row), candles);

  // 1. One bar spans BOTH barriers (long) → ambiguous, never tp_hit.
  ok(`${name}: both barriers in one bar (long) → ambiguous`,
    grade(mkRow(), [B(100, 111, 89, 100, 1)]) === 'ambiguous');

  // 2. One bar spans BOTH barriers (short) → ambiguous.
  ok(`${name}: both barriers in one bar (short) → ambiguous`,
    grade(mkRow({ verdict: 'SELL', target_price: 90, stop_loss: 110 }), [B(100, 111, 89, 100, 1)]) === 'ambiguous');

  // 3. TP-only / SL-only bars still grade normally.
  ok(`${name}: TP-only bar → tp_hit`, grade(mkRow(), [B(100, 111, 95, 105, 1)]) === 'tp_hit');
  ok(`${name}: SL-only bar → sl_hit`, grade(mkRow(), [B(100, 105, 89, 95, 1)]) === 'sl_hit');
  ok(`${name}: short TP-only bar → tp_hit`,
    grade(mkRow({ verdict: 'SELL', target_price: 90, stop_loss: 110 }), [B(100, 105, 89, 95, 1)]) === 'tp_hit');
  ok(`${name}: short SL-only bar → sl_hit`,
    grade(mkRow({ verdict: 'SELL', target_price: 90, stop_loss: 110 }), [B(100, 111, 95, 105, 1)]) === 'sl_hit');

  // 4. First resolving bar wins — SL on day 1, TP on day 2 → sl_hit (no look-ahead).
  ok(`${name}: SL one day before TP → sl_hit`,
    grade(mkRow(), [B(100, 105, 89, 95, 1), B(95, 111, 95, 110, 2)]) === 'sl_hit');

  // 5. An ambiguous bar resolves immediately — a later clean TP bar does NOT upgrade it.
  ok(`${name}: ambiguous bar not upgraded by later TP bar`,
    grade(mkRow(), [B(100, 111, 89, 100, 1), B(100, 112, 95, 111, 2)]) === 'ambiguous');

  // 6. Neither barrier touched → null (still pending).
  ok(`${name}: no barrier touched → null`, grade(mkRow(), [B(100, 104, 96, 101, 1)]) === null);

  // 7. Entry-fill gate: price must trade INTO the zone before a barrier counts.
  ok(`${name}: fill on the grading bar, then TP-only → tp_hit`,
    grade(mkRow({ entry_zone: '95 - 96' }), [B(100, 99, 97, 98, 1), B(98, 111, 95.5, 110, 2)]) === 'tp_hit');
  ok(`${name}: never filled → null despite TP bar`,
    grade(mkRow({ entry_zone: '95 - 96' }), [B(100, 111, 97, 110, 1)]) === null);

  // 8. Entry-day bar is gated out (daily no-look-ahead): a same-day span must not grade.
  ok(`${name}: entry-day bar spanning both → null (gated)`,
    grade(mkRow(), [B(100, 111, 89, 100, 0)]) === null);
}

// 9. 'ambiguous' is a distinct outcome value — the win-rate filters downstream key on
//    'tp_hit'/'sl_hit' only, so it drops out of numerator AND denominator by construction.
ok('ambiguous ≠ tp_hit/sl_hit', !['tp_hit', 'sl_hit'].includes('ambiguous'));

console.log(`\nGrader gate: ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
