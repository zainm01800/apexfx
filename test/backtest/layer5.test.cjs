// Layer 5 test gate: the improvement-hypotheses engine.
//   - signalLift: hand-verified per-signal predictive power.
//   - thresholdSweep + runJob: confluence row carries signal_lift + threshold_sweep.
//   - buildHypotheses: assembles the right cards and excludes <30-trade results.
// Run: node test/backtest/layer5.test.cjs
'use strict';
const path = require('path');
const ROOT = path.resolve(__dirname, '..', '..');
['ta', 'regime', 'confluence', 'strategies', 'metrics', 'hypotheses', 'runjob'].forEach(m => require(path.join(ROOT, `public/lib/${m}.js`)));
const H = globalThis.APEX.hypotheses;
const { runJob } = globalThis.APEX.runjob;
const BASE = process.env.APEX_BASE || 'https://apexfx.vercel.app';

let pass = 0, fail = 0;
const ok = (n, c) => { if (c) pass++; else { fail++; console.error('  ✗ ' + n); } };
const near = (n, a, b, t = 0.05) => ok(n + ` (${a} vs ${b})`, a != null && Math.abs(a - b) < t);

// ── 1. signalLift hand-verified ───────────────────────────────────────────────
{
  // 3 wins (good:true), 3 losses (good:false); noise:true always; half split.
  const T = (dir, good, noise, half, pnl) => ({ dir, pnlPct: pnl, signals: { good, noise, half } });
  const trades = [
    T(1, true, true, true, 1), T(1, true, true, true, 1), T(1, true, true, false, 1),
    T(1, false, true, true, -1), T(1, false, true, false, -1), T(1, false, true, false, -1),
  ];
  const lift = H.signalLift(trades);
  near('signalLift good = +100', lift.good.lift, 100);
  ok('signalLift noise lift null (no misaligned)', lift.noise.lift === null && lift.noise.nMis === 0);
  near('signalLift half ≈ +33.4', lift.half.lift, 33.4, 0.2);
  ok('signalLift good winAligned 100', lift.good.winAligned === 100 && lift.good.winMis === 0);
}

// ── 2. buildHypotheses assembles cards + excludes <30 ─────────────────────────
{
  const reg = { 'up/normal': { n: 20, winRate: 60, avgPnl: 0.4 }, 'ranging/normal': { n: 12, winRate: 40, avgPnl: -0.2 } };
  const rows = [
    { instrument: 'EUR/USD', timeframe: '1d', strategy: 'confluence', strategy_family: 'Confluence', n_trades: 50, sharpe: 1.2, data_from: '2016-01-01T00:00:00Z', data_to: '2026-01-01T00:00:00Z', regime_breakdown: reg,
      signal_lift: { 'Price vs SMA200': { lift: 12, nAligned: 30, nMis: 20 }, 'StochRSI': { lift: 0.5, nAligned: 25, nMis: 25 } },
      threshold_sweep: [{ threshold: 55, nTrades: 80, winRate: 48, expectancy: 0.05, sharpe: 0.6 }, { threshold: 65, nTrades: 50, winRate: 55, expectancy: 0.2, sharpe: 1.1 }, { threshold: 75, nTrades: 20, winRate: 60, expectancy: 0.3, sharpe: 0.9 }] },
    { instrument: 'EUR/USD', timeframe: '1d', strategy: 'ema_50_200', strategy_family: 'MA Trend', n_trades: 40, sharpe: 0.8, data_from: '2016-01-01T00:00:00Z', data_to: '2026-01-01T00:00:00Z', regime_breakdown: reg },
    { instrument: 'EUR/USD', timeframe: '4h', strategy: 'ema_50_200', strategy_family: 'MA Trend', n_trades: 35, sharpe: 0.3, data_from: '2024-01-01T00:00:00Z', data_to: '2026-01-01T00:00:00Z', regime_breakdown: reg },
    { instrument: 'EUR/USD', timeframe: '1d', strategy: 'rsi_revert', strategy_family: 'Momentum', n_trades: 12, sharpe: 9.9, data_from: '2016-01-01T00:00:00Z', data_to: '2026-01-01T00:00:00Z', regime_breakdown: reg }, // <30 -> excluded
  ];
  const { meta, cards } = H.buildHypotheses(rows);
  const ids = cards.map(c => c.id);
  ok('cards: signal_power', ids.includes('signal_power'));
  ok('cards: threshold', ids.includes('threshold'));
  ok('cards: top_combos', ids.includes('top_combos'));
  ok('cards: tf_consistency', ids.includes('tf_consistency'));
  ok('meta: excluded 1 (the <30 row)', meta.excluded === 1);
  ok('meta: framing present', /hypothesis for review/i.test(meta.framing));
  // top combo for EUR/USD: best sharpe is confluence (1.2), excludes the 9.9 thin row
  const top = cards.find(c => c.id === 'top_combos').perPair['EUR/USD'];
  ok('top_combos: best is confluence (thin 9.9 excluded)', top[0].strategy === 'confluence' && Math.abs(top[0].sharpe - 1.2) < 1e-9);
  // threshold distribution shows ALL tested thresholds (not just winner)
  const sweep = cards.find(c => c.id === 'threshold').sweep;
  ok('threshold: full distribution shown', sweep.length === 3);
  ok('threshold: best by expectancy = 75', /threshold 75/.test(cards.find(c => c.id === 'threshold').hypothesis));
  // signal power flags the near-zero-lift signal as a drop candidate
  ok('signal_power: flags weak StochRSI', /StochRSI/.test(cards.find(c => c.id === 'signal_power').hypothesis));
}

// ── 3. runJob populates confluence signal_lift + threshold_sweep (real data) ──
(async () => {
  try {
    const to = Math.floor(Date.now() / 1000), from = to - 3649 * 86400;
    const get = async (tf) => (await fetch(`${BASE}/api/candles?sym=EUR/USD&type=Forex&tf=${tf}&from=${from}&to=${to}`)).json();
    const bars = await get('1d'), weekly = await get('1w');
    const rows = runJob({ bars, weekly, sym: 'EUR/USD', assetClass: 'Forex', timeframe: '1d', runId: 'r', runTs: 1780000000000 });
    const conf = rows.find(r => r.strategy === 'confluence');
    ok('runJob: confluence row exists', !!conf);
    ok('runJob: signal_lift is object', conf && conf.signal_lift && typeof conf.signal_lift === 'object');
    ok('runJob: threshold_sweep is array of 6', conf && Array.isArray(conf.threshold_sweep) && conf.threshold_sweep.length === 6);
    ok('runJob: sweep entries have metrics', conf && conf.threshold_sweep.every(s => 'threshold' in s && 'nTrades' in s && 'expectancy' in s));
    ok('runJob: non-confluence rows have null signal_lift', rows.filter(r => r.strategy !== 'confluence').every(r => r.signal_lift === null));
  } catch (e) { ok('runJob real-data section (network) — ' + e.message, false); }

  console.log(`\nLayer 5 gate: ${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})();
