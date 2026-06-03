// Layer 3 test gate (compute side): runJob() on REAL EUR/USD 1d+1w candles
// produces well-formed storage rows deterministically (the worker just calls
// runJob, so this is the worker's output). Storage round-trip (POST->GET) is
// verified separately once the Supabase table exists.
// Run: node test/backtest/layer3.test.cjs
'use strict';
const path = require('path');
const ROOT = path.resolve(__dirname, '..', '..');
['ta', 'regime', 'confluence', 'strategies', 'metrics', 'runjob'].forEach(m => require(path.join(ROOT, `public/lib/${m}.js`)));
const { runJob } = globalThis.APEX.runjob;

const BASE = process.env.APEX_BASE || 'https://apexfx.vercel.app';
let pass = 0, fail = 0;
const ok = (n, c) => { if (c) pass++; else { fail++; console.error('  ✗ ' + n); } };

async function candles(sym, type, tf, days) {
  const to = Math.floor(Date.now() / 1000), from = to - days * 86400;
  const r = await fetch(`${BASE}/api/candles?sym=${encodeURIComponent(sym)}&type=${type}&tf=${tf}&from=${from}&to=${to}`);
  if (!r.ok) throw new Error(`candles ${tf} HTTP ${r.status}`);
  return r.json();
}

(async () => {
  const sym = 'EUR/USD', type = 'Forex';
  const bars = await candles(sym, type, '1d', 3649);
  const weekly = await candles(sym, type, '1w', 3649);
  ok('fetched EUR/USD 1d (>500 bars)', Array.isArray(bars) && bars.length > 500);
  ok('fetched EUR/USD 1w', Array.isArray(weekly) && weekly.length > 50);

  const runTs = 1780000000000, runId = 'test_' + runTs;
  const rows = runJob({ bars, weekly, sym, assetClass: type, timeframe: '1d', runId, runTs, appVersion: 'test' });

  ok('rows >= 40 (strategies + twins + confluence)', rows.length >= 40);
  ok('confluence row present @1d', rows.some(r => r.strategy === 'confluence'));
  const ids = new Set(rows.map(r => r.id));
  ok('ids unique', ids.size === rows.length);
  ok('ids embed runTs', rows.every(r => r.id.endsWith('_' + runTs)));

  const required = ['id', 'run_id', 'instrument', 'asset_class', 'timeframe', 'strategy', 'strategy_family',
    'regime_filtered', 'data_from', 'data_to', 'n_bars', 'n_trades', 'total_return', 'sharpe', 'max_drawdown',
    'win_rate', 'expectancy', 'profit_factor', 'low_sample', 'shallow_sharpe', 'regime_breakdown'];
  ok('every row has required fields', rows.every(r => required.every(k => k in r)));
  ok('asset_class Forex', rows.every(r => r.asset_class === 'Forex'));
  ok('timeframe 1d', rows.every(r => r.timeframe === '1d'));
  ok('n_bars matches', rows.every(r => r.n_bars === bars.length));
  ok('data_from < data_to (ISO)', rows.every(r => new Date(r.data_from) < new Date(r.data_to)));
  ok('shallow_sharpe false @1d', rows.every(r => r.shallow_sharpe === false));
  ok('regime_breakdown is object', rows.every(r => r.regime_breakdown && typeof r.regime_breakdown === 'object'));
  ok('has regime-filtered twins', rows.some(r => r.regime_filtered === true));
  ok('numbers are finite or null', rows.every(r => [r.sharpe, r.total_return, r.max_drawdown, r.win_rate, r.expectancy].every(v => v === null || Number.isFinite(v))));
  // forex pips populated for at least one strategy that traded
  const traded = rows.filter(r => r.n_trades > 0);
  ok('some strategies traded', traded.length > 0);
  ok('forex pip stats present where trades exist', traded.some(r => r.avg_win_pips !== null || r.avg_loss_pips !== null));

  // Determinism: same inputs + runTs -> identical rows (=> worker output matches main thread)
  const rows2 = runJob({ bars, weekly, sym, assetClass: type, timeframe: '1d', runId, runTs, appVersion: 'test' });
  ok('deterministic (worker == main-thread)', JSON.stringify(rows) === JSON.stringify(rows2));

  // sample-size gate behaves
  ok('low_sample flagged when <30 trades', rows.filter(r => r.n_trades < 30).every(r => r.low_sample === true));

  console.log(`\nLayer 3 compute gate: ${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(e => { console.error('ERROR', e); process.exit(1); });
