// Layer 1 test gate: prove the shared lib matches the live analysis layer.
//
//  (1) PARITY — every indicator in public/lib/ta.js and the confluence score in
//      public/lib/confluence.js produce output IDENTICAL to the live functions
//      extracted from public/dashboard.js, across a deterministic fixture.
//  (2) REGIME — the JS port (public/lib/regime.js) matches hand-computed
//      expectations and is self-consistent (classifyRegime == regimeSeries tail).
//
// Run: node test/backtest/layer1.test.cjs
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..', '..');
// Load for side effects — each lib attaches to globalThis.APEX (the same
// mechanism used in the browser <script> and the Web Worker importScripts).
require(path.join(ROOT, 'public/lib/ta.js'));
require(path.join(ROOT, 'public/lib/regime.js'));
require(path.join(ROOT, 'public/lib/confluence.js'));
const ta = globalThis.APEX.ta;
const regime = globalThis.APEX.regime;
const confluence = globalThis.APEX.confluence;

let pass = 0, fail = 0;
const ok = (name, cond) => { if (cond) { pass++; } else { fail++; console.error('  ✗ ' + name); } };
const eq = (name, a, b) => ok(name, JSON.stringify(a) === JSON.stringify(b) || a === b ||
  (typeof a === 'number' && typeof b === 'number' && Math.abs(a - b) < 1e-9));

// ── Deterministic OHLCV fixture (no RNG; reproducible) ────────────────────────
function makeFixture(n) {
  const bars = [];
  const t0 = 1577836800; // 2020-01-01
  for (let i = 0; i < n; i++) {
    const base = 100 + 25 * Math.sin(i / 17) + i * 0.08 + 6 * Math.sin(i / 3.3);
    const close = +base.toFixed(4);
    const open = +(base - Math.sin(i / 5) * 1.5).toFixed(4);
    const high = +(Math.max(open, close) + 1.2 + Math.abs(Math.cos(i / 4))).toFixed(4);
    const low = +(Math.min(open, close) - 1.2 - Math.abs(Math.sin(i / 6))).toFixed(4);
    const volume = Math.round(1000 + 600 * Math.sin(i / 7) + (i % 13) * 25);
    bars.push({ time: t0 + i * 86400, open, high, low, close, volume });
  }
  return bars;
}
const bars = makeFixture(400);
const closes = bars.map(b => b.close);

// ── Extract the LIVE functions from dashboard.js (anchored slices, DOM-free) ──
const dash = fs.readFileSync(path.join(ROOT, 'public/dashboard.js'), 'utf8');
function slice(from, to) {
  const a = dash.indexOf(from); const b = dash.indexOf(to, a);
  assert(a >= 0 && b > a, `anchors not found: ${from} .. ${to}`);
  return dash.slice(a, b);
}
const indicatorSrc = slice('function calcSMA', '// ── Historical setup scanner');
const confluenceSrc = slice('function calcConfluenceScore', 'async function fetchFearGreed');

const refTa = new Function(indicatorSrc + `
  return { calcSMA, calcEMA, calcRSI, calcMACD, calcATR, calcBollingerBands, calcStochRSI,
           calcOBVTrend, findPivotSR, calcFibLevels, calcVolTrend, calcADX, calcBBWidthPct, getTrend };`)();
const refCalcConfluence = new Function(confluenceSrc + '\n return calcConfluenceScore;')();

// ── (1) PARITY: indicators across full series + several sub-slices ────────────
const closeFns = ['calcSMA', 'calcEMA', 'calcRSI', 'calcMACD', 'calcStochRSI', 'calcBBWidthPct'];
const barFns = ['calcATR', 'calcOBVTrend', 'calcVolTrend', 'calcADX', 'calcFibLevels', 'findPivotSR', 'calcBollingerBands'];
for (const cut of [60, 120, 220, 300, 400]) {
  const c = closes.slice(0, cut), b = bars.slice(0, cut);
  eq(`calcSMA(20)@${cut}`, ta.calcSMA(c, 20), refTa.calcSMA(c, 20));
  eq(`calcSMA(50)@${cut}`, ta.calcSMA(c, 50), refTa.calcSMA(c, 50));
  eq(`calcSMA(200)@${cut}`, ta.calcSMA(c, 200), refTa.calcSMA(c, 200));
  eq(`calcEMA(12)@${cut}`, ta.calcEMA(c, 12), refTa.calcEMA(c, 12));
  eq(`calcRSI@${cut}`, ta.calcRSI(c), refTa.calcRSI(c));
  eq(`calcMACD@${cut}`, ta.calcMACD(c), refTa.calcMACD(c));
  eq(`calcStochRSI@${cut}`, ta.calcStochRSI(c), refTa.calcStochRSI(c));
  eq(`calcBBWidthPct@${cut}`, ta.calcBBWidthPct(c), refTa.calcBBWidthPct(c));
  eq(`calcBollingerBands@${cut}`, ta.calcBollingerBands(c), refTa.calcBollingerBands(c));
  eq(`calcATR@${cut}`, ta.calcATR(b), refTa.calcATR(b));
  eq(`calcOBVTrend@${cut}`, ta.calcOBVTrend(b), refTa.calcOBVTrend(b));
  eq(`calcVolTrend@${cut}`, ta.calcVolTrend(b), refTa.calcVolTrend(b));
  eq(`calcADX@${cut}`, ta.calcADX(b), refTa.calcADX(b));
  eq(`calcFibLevels@${cut}`, ta.calcFibLevels(b), refTa.calcFibLevels(b));
  eq(`findPivotSR@${cut}`, ta.findPivotSR(b), refTa.findPivotSR(b));
  const sma20 = ta.calcSMA(c, 20), sma50 = ta.calcSMA(c, 50);
  eq(`getTrend@${cut}`, ta.getTrend(c, sma20, sma50), refTa.getTrend(c, sma20, sma50));
}

// ── (1b) PARITY: confluence score matches the live function exactly ───────────
for (let i = 220; i < 400; i += 9) {
  const inp = confluence.confluenceInputsAtBar(bars, null, i);
  eq(`confluence@${i}`, confluence.calcConfluenceScore(inp), refCalcConfluence(inp));
}
// also with a synthetic weekly series (every 5th daily bar acts as a weekly bar)
const weekly = bars.filter((_, i) => i % 5 === 0);
for (let i = 250; i < 400; i += 11) {
  const inp = confluence.confluenceInputsAtBar(bars, weekly, i);
  eq(`confluence+weekly@${i}`, confluence.calcConfluenceScore(inp), refCalcConfluence(inp));
  const atBar = confluence.confluenceAtBar(bars, weekly, i);
  ok(`confluenceAtBar signalMap@${i}`, atBar && typeof atBar.signalMap === 'object' && atBar.signalCount > 0);
}

// ── (2) REGIME: hand-computed + self-consistency ──────────────────────────────
// Steady uptrend (monotone rising closes) -> trend 'up'.
const upBars = Array.from({ length: 300 }, (_, i) => ({ time: i * 86400, open: 100 + i, high: 100 + i + 0.5, low: 100 + i - 0.5, close: 100 + i, volume: 1000 }));
ok('regime up-trend', regime.classifyRegime(upBars.map(b => b.close)).trend === 'up');
// Steady downtrend.
const dnBars = Array.from({ length: 300 }, (_, i) => ({ time: i * 86400, close: 400 - i }));
ok('regime down-trend', regime.classifyRegime(dnBars.map(b => b.close)).trend === 'down');
// Flat -> ranging (slope ~ 0).
const flatBars = Array.from({ length: 300 }, (_, i) => ({ time: i * 86400, close: 100 + (i % 2) * 0.01 }));
ok('regime flat-ranging', regime.classifyRegime(flatBars.map(b => b.close)).trend === 'ranging');
// trendSlope sign matches direction.
ok('slope up>0', regime.trendSlope(upBars.map(b => b.close), 200, 21) > 0);
ok('slope dn<0', regime.trendSlope(dnBars.map(b => b.close), 200, 21) < 0);
// regimeSeries tail equals classifyRegime on the fixture (live region, post-warmup).
const series = regime.regimeSeries(bars);
for (const i of [250, 320, 399]) {
  const point = regime.classifyRegime(closes.slice(0, i + 1));
  eq(`regimeSeries trend@${i}`, series[i].trend, point.trend);
  eq(`regimeSeries vol@${i}`, series[i].vol, point.vol);
  eq(`regimeSeries conf@${i}`, +series[i].confidence.toFixed(9), +point.confidence.toFixed(9));
}
ok('regimeSeries length', series.length === bars.length);

console.log(`\nLayer 1 gate: ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
