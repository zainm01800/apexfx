// Layer 2 test gate: prove the simulate() harness, metrics, regime twins, and the
// confluence strategy are correct on hand-known synthetic series.
// Run: node test/backtest/layer2.test.cjs
'use strict';
const path = require('path');
const ROOT = path.resolve(__dirname, '..', '..');
require(path.join(ROOT, 'public/lib/ta.js'));
require(path.join(ROOT, 'public/lib/regime.js'));
require(path.join(ROOT, 'public/lib/confluence.js'));
require(path.join(ROOT, 'public/lib/strategies.js'));
require(path.join(ROOT, 'public/lib/metrics.js'));
const S = globalThis.APEX.strategies;
const M = globalThis.APEX.metrics;

let pass = 0, fail = 0;
const ok = (name, cond) => { if (cond) pass++; else { fail++; console.error('  ✗ ' + name); } };
const near = (name, a, b, tol = 1e-6) => ok(name + ` (${a} vs ${b})`, Math.abs(a - b) < tol);

const B = (o, h, l, c, t) => ({ time: t, open: o, high: h, low: l, close: c, volume: 1000 });
// Minimal ctx for harness-semantics tests (bypasses buildContext).
const ctxOf = (bars, costPct = 0.04, pipSize = null, regime = 'up') =>
  ({ bars, closes: bars.map(b => b.close), costPct, pipSize, regimeLabels: bars.map(() => ({ trend: regime, vol: 'normal' })) });
// A strategy that enters once at a chosen bar, with optional stop/target + exit rule.
const oneShot = (atBar, dir, stop, target, exitFn) => ({
  entry(i) { return i === atBar ? { dir, stop, target } : null; },
  exit: exitFn,
});

// ── 1. next-bar-open fill + TARGET hit ────────────────────────────────────────
{
  const bars = [B(100,101,99,100,0), B(100,101,99,100,1), B(100,100,100,100,2), B(100,106,100,104,3), B(104,104,104,104,4)];
  const { trades } = S.simulate(bars, oneShot(1, 1, 95, 105), ctxOf(bars));
  ok('target: one trade', trades.length === 1);
  const t = trades[0];
  ok('target: entryIdx=2 (next-bar open)', t.entryIdx === 2);
  near('target: entryPrice=100', t.entryPrice, 100);
  ok('target: exitIdx=3', t.exitIdx === 3);
  ok('target: reason target', t.exitReason === 'target');
  near('target: exitPrice=105', t.exitPrice, 105);
  near('target: pnlPct = 5 - 0.04 cost', t.pnlPct, 4.96, 1e-3);
}
// ── 2. STOP hit ───────────────────────────────────────────────────────────────
{
  const bars = [B(100,101,99,100,0), B(100,101,99,100,1), B(100,100,100,100,2), B(100,100,94,96,3), B(96,96,96,96,4)];
  const { trades } = S.simulate(bars, oneShot(1, 1, 95, 200), ctxOf(bars));
  ok('stop: reason stop', trades[0].exitReason === 'stop');
  near('stop: exitPrice=95', trades[0].exitPrice, 95);
  near('stop: pnlPct = -5 - 0.04', trades[0].pnlPct, -5.04, 1e-3);
}
// ── 3. STOP BEFORE TARGET when one bar spans both (pessimistic) ───────────────
{
  const bars = [B(100,101,99,100,0), B(100,101,99,100,1), B(100,100,100,100,2), B(100,106,94,100,3), B(100,100,100,100,4)];
  const { trades } = S.simulate(bars, oneShot(1, 1, 95, 105), ctxOf(bars));
  ok('span: stop wins over target', trades[0].exitReason === 'stop' && Math.abs(trades[0].exitPrice - 95) < 1e-9);
}
// ── 4. SIGNAL exit fills at NEXT open ─────────────────────────────────────────
{
  const bars = [B(100,101,99,100,0), B(100,101,99,100,1), B(100,101,99,100,2), B(100,101,99,100,3), B(110,111,109,110,4), B(110,110,110,110,5)];
  // enter long at bar1 (fill bar2), exit signal true from bar3 -> fill bar4 open=110
  const { trades } = S.simulate(bars, oneShot(1, 1, 50, null, (i) => i >= 3), ctxOf(bars));
  ok('signal: reason signal', trades[0].exitReason === 'signal');
  ok('signal: exitIdx=4', trades[0].exitIdx === 4);
  near('signal: exitPrice=110 (next open)', trades[0].exitPrice, 110);
}
// ── 5. FORCE-CLOSE at end of data ─────────────────────────────────────────────
{
  const bars = [B(100,101,99,100,0), B(100,101,99,100,1), B(100,101,99,100,2), B(100,101,99,103,3)];
  const { trades } = S.simulate(bars, oneShot(1, 1, 50, null, () => false), ctxOf(bars));
  ok('force: reason end-of-data', trades[0].exitReason === 'end-of-data');
  near('force: exitPrice=last close 103', trades[0].exitPrice, 103);
}
// ── 6. SHORT trade target ─────────────────────────────────────────────────────
{
  const bars = [B(100,101,99,100,0), B(100,101,99,100,1), B(100,100,100,100,2), B(100,100,94,96,3)];
  const { trades } = S.simulate(bars, oneShot(1, -1, 105, 95), ctxOf(bars));
  ok('short: reason target', trades[0].exitReason === 'target');
  near('short: pnlPct = +5 - cost', trades[0].pnlPct, 4.96, 1e-3);
}
// ── 7. SPREAD reduces pnl ─────────────────────────────────────────────────────
{
  const bars = [B(100,101,99,100,0), B(100,101,99,100,1), B(100,100,100,100,2), B(100,106,100,105,3)];
  const noCost = S.simulate(bars, oneShot(1, 1, 95, 105), ctxOf(bars, 0)).trades[0];
  const withCost = S.simulate(bars, oneShot(1, 1, 95, 105), ctxOf(bars, 0.04)).trades[0];
  ok('spread: cost lowers pnl', withCost.pnlPct < noCost.pnlPct);
  near('spread: exactly cost lower', noCost.pnlPct - withCost.pnlPct, 0.04, 1e-6);
}
// ── 8. FOREX pips populated, equities null ────────────────────────────────────
{
  const bars = [B(1.1000,1.1010,1.0990,1.1000,0), B(1.1000,1.1010,1.0990,1.1000,1), B(1.1000,1.1000,1.1000,1.1000,2), B(1.1000,1.1100,1.1000,1.1050,3)];
  const fx = S.simulate(bars, oneShot(1, 1, 1.0900, 1.1050), ctxOf(bars, 0.02, 0.0001)).trades[0];
  ok('pips: forex pnlPips not null', fx.pnlPips != null && fx.pnlPips > 0);
  const eq = S.simulate([B(100,101,99,100,0),B(100,101,99,100,1),B(100,100,100,100,2),B(100,106,100,105,3)], oneShot(1,1,95,105), ctxOf([], 0.04, null)).trades;
  ok('pips: equity pnlPips null', eq.length && eq[0].pnlPips === null);
}
// ── 9. REGIME twin drops misaligned entries ───────────────────────────────────
{
  const base = oneShot(1, 1, 50, null, () => false);
  const twin = S.withRegimeFilter(base, S.ALLOW.trend);
  const upBars = [B(100,101,99,100,0), B(100,101,99,100,1), B(100,101,99,103,2)];
  const rgBars = upBars.slice();
  ok('twin: enters in up-trend', S.simulate(upBars, twin, ctxOf(upBars, 0.04, null, 'up')).trades.length === 1);
  ok('twin: blocked in ranging', S.simulate(rgBars, twin, ctxOf(rgBars, 0.04, null, 'ranging')).trades.length === 0);
  ok('twin: base still enters in ranging', S.simulate(rgBars, base, ctxOf(rgBars, 0.04, null, 'ranging')).trades.length === 1);
}
// ── 10. METRICS on a hand-computed trade set ──────────────────────────────────
{
  // 3 trades: +2%, -1%, +3% (already net). win rate 2/3, PF = 5/1 = 5, expectancy = (2/3)*2.5 - (1/3)*1
  const sim = { trades: [{ pnlPct: 2, pnlPips: null }, { pnlPct: -1, pnlPips: null }, { pnlPct: 3, pnlPips: null }],
                barReturns: [0.02, -0.01, 0.03] };
  const m = M.computeMetrics(sim, { assetClass: 'Stock', timeframe: '1d' });
  ok('metrics: nTrades=3', m.nTrades === 3);
  near('metrics: winRate 66.7', m.winRate, 66.7, 0.05);
  near('metrics: profitFactor 5', m.profitFactor, 5, 1e-6);
  near('metrics: expectancy', m.expectancy, (2 / 3) * 2.5 - (1 / 3) * 1, 1e-3);
  ok('metrics: lowSample true (<30)', m.lowSample === true);
  ok('metrics: sharpe finite', Number.isFinite(m.sharpe));
  ok('metrics: shallowSharpe false @1d', m.shallowSharpe === false);
  ok('metrics: shallow @1m', M.computeMetrics(sim, { assetClass: 'Crypto', timeframe: '1m' }).shallowSharpe === true);
}
// ── 11. Full registry runs on the fixture; confluence carries signals ─────────
{
  function makeFixture(n) {
    const bars = []; const t0 = 1577836800;
    for (let i = 0; i < n; i++) {
      const base = 100 + 25 * Math.sin(i / 17) + i * 0.08 + 6 * Math.sin(i / 3.3);
      const close = +base.toFixed(4), open = +(base - Math.sin(i / 5) * 1.5).toFixed(4);
      const high = +(Math.max(open, close) + 1.2 + Math.abs(Math.cos(i / 4))).toFixed(4);
      const low = +(Math.min(open, close) - 1.2 - Math.abs(Math.sin(i / 6))).toFixed(4);
      bars.push(B(open, high, low, close, t0 + i * 86400)); bars[bars.length - 1].volume = 1000 + (i % 13) * 50;
    }
    return bars;
  }
  const bars = makeFixture(400);
  const weekly = bars.filter((_, i) => i % 5 === 0);
  const ctx = S.buildContext(bars, { sym: 'TESTUSD', assetClass: 'Stock', timeframe: '1d', weekly });
  const strats = S.buildStrategies(ctx);
  ok('registry: ~40+ strategies incl twins + confluence', strats.length >= 40);
  ok('registry: confluence present @1d', strats.some(s => s.id === 'confluence'));
  let anyTrades = 0, confSignals = false;
  for (const st of strats) {
    const sim = S.simulate(bars, st.strat, ctx);
    const m = M.computeMetrics(sim, ctx);
    ok(`run ${st.id}: metrics object`, m && typeof m.sharpe === 'number' && typeof m.nTrades === 'number');
    anyTrades += sim.trades.length;
    if (st.id === 'confluence' && sim.trades.some(t => t.signals && Object.keys(t.signals).length)) confSignals = true;
  }
  ok('registry: produced trades across strategies', anyTrades > 0);
  ok('registry: confluence trades carry per-signal map', confSignals);
  // confluence gated off intraday
  const ctxIntraday = S.buildContext(bars, { sym: 'TESTUSD', assetClass: 'Stock', timeframe: '1h', weekly: null });
  ok('registry: confluence gated out @1h', !S.buildStrategies(ctxIntraday).some(s => s.id === 'confluence'));
}

console.log(`\nLayer 2 gate: ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
