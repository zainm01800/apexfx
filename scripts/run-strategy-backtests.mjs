// APEX Backtest Lab — server-side runner.
// Runs every strategy (the same pure code the browser Web Worker uses) across the
// tracked universe and posts results to /api/backtest-runs (apex_strategy_backtests),
// so the dashboard AND Deep Analyse always have fresh strategy backtests without
// anyone clicking Run. Scheduled by .github/workflows/auto-strategy-backtest.yml.
//
// Local use:
//   node scripts/run-strategy-backtests.mjs
//   APEX_TFS=1d APEX_SYMS="NVDA,BTC/USD,EUR/USD" node scripts/run-strategy-backtests.mjs
import { pathToFileURL, fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const LIB = (f) => pathToFileURL(join(ROOT, 'public/lib', f)).href;
// Load libs for side effects — each attaches to globalThis.APEX (same mechanism as
// the page <script> / worker importScripts). Dependency order matters.
for (const f of ['ta.js', 'regime.js', 'confluence.js', 'strategies.js', 'metrics.js', 'hypotheses.js', 'runjob.js']) {
  await import(LIB(f));
}
const { runJob } = globalThis.APEX.runjob;

const BASE = process.env.APEX_BASE || 'https://apexfx.vercel.app';
// Default to the timeframes with enough Yahoo history to matter; intraday (1m–30m)
// is too shallow for ≥30 trades, so it's opt-in via APEX_TFS.
const TFS = (process.env.APEX_TFS || '1h,4h,1d,1w').split(',').map(s => s.trim()).filter(Boolean);
const ONLY = (process.env.APEX_SYMS || '').split(',').map(s => s.trim()).filter(Boolean);
const MAX_DAYS = { '1m': 7, '5m': 60, '15m': 60, '30m': 60, '1h': 729, '4h': 729, '1d': 3649, '1w': 3649 };

const UNIVERSE = {
  Forex:  ['EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'AUD/USD', 'USD/CAD', 'NZD/USD', 'GBP/JPY', 'EUR/GBP', 'EUR/JPY'],
  Crypto: ['BTC/USD', 'ETH/USD', 'SOL/USD', 'BNB/USD', 'XRP/USD', 'ADA/USD', 'AVAX/USD', 'DOGE/USD', 'MATIC/USD', 'LINK/USD', 'ARB/USD', 'SUI/USD'],
  Stock:  ['NVDA', 'AAPL', 'MSFT', 'META', 'AMZN', 'GOOGL', 'TSLA', 'AMD', 'PLTR', 'TSM', 'NFLX', 'UBER'],
  ETF:    ['SPY', 'QQQ', 'IWM', 'GLD', 'TLT', 'XLK', 'XLE', 'XLF', 'ARKK', 'SMH', 'SOXX', 'XBI'],
};
const JOBS = [];
for (const [type, syms] of Object.entries(UNIVERSE))
  for (const sym of syms)
    if (!ONLY.length || ONLY.includes(sym))
      for (const tf of TFS) JOBS.push({ sym, type, tf });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function getCandles(sym, type, tf) {
  const to = Math.floor(Date.now() / 1000), from = to - (MAX_DAYS[tf] || 3649) * 86400;
  const url = `${BASE}/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=${tf}&from=${from}&to=${to}`;
  const r = await fetch(url, { signal: AbortSignal.timeout(45000) });
  if (!r.ok) throw new Error(`candles HTTP ${r.status}`);
  const d = await r.json();
  if (d && d.error) throw new Error(d.error);
  return Array.isArray(d) ? d : [];
}
async function postRows(rows) {
  const r = await fetch(`${BASE}/api/backtest-runs`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rows), signal: AbortSignal.timeout(30000),
  });
  return r.ok;
}

const runTs = Date.now(), runId = 'auto_' + runTs;
let ok = 0, skip = 0, fail = 0, saved = 0;
console.log(`APEX strategy backtest runner — ${JOBS.length} jobs [${TFS.join(',')}] -> ${BASE}  (run_id ${runId})`);
for (let i = 0; i < JOBS.length; i++) {
  const { sym, type, tf } = JOBS[i];
  const tag = `[${i + 1}/${JOBS.length}] ${sym} ${tf}`;
  try {
    const bars = await getCandles(sym, type, tf);
    if (!bars || bars.length < 30) { skip++; console.log(`${tag}: skip (${bars ? bars.length : 0} bars)`); await sleep(200); continue; }
    const weekly = tf === '1d' ? await getCandles(sym, type, '1w').catch(() => null) : null;
    const rows = runJob({ bars, weekly, sym, assetClass: type, timeframe: tf, runId, runTs, appVersion: 'auto' });
    const posted = await postRows(rows);
    if (posted) { ok++; saved += rows.length; } else fail++;
    console.log(`${tag}: ${rows.length} strategies ${posted ? 'saved' : 'POST FAILED'}`);
  } catch (e) {
    fail++; console.log(`${tag}: ERROR ${e.message}`);
  }
  await sleep(250); // rate-limit-friendly gap between pairs
}
console.log(`\nDONE: ${ok} jobs ok (${saved} rows saved), ${skip} skipped, ${fail} failed. run_id=${runId}`);
process.exit(fail > JOBS.length / 2 ? 1 : 0);
