#!/usr/bin/env node
// ──────────────────────────────────────────────────────────────────────────────
// Measure the REAL data-feed delay (free Yahoo Finance) per asset class.
// Fetches the latest 5m & 15m bar for a sample of instruments and reports how far
// behind real time each is. Run by feed-delay-check.yml on weekdays during US market
// hours so we have an actual, ongoing number for the stock/forex feed delay (crypto
// is 24/7 and already measured ~real-time).
// ──────────────────────────────────────────────────────────────────────────────
import { appendFileSync } from 'node:fs';

const BASE = (process.env.APEX_BASE || 'https://apexfx.vercel.app').replace(/\/$/, '');
const SET = [
  ['AAPL', 'Stock'], ['MSFT', 'Stock'], ['NVDA', 'Stock'], ['SPY', 'ETF'],
  ['EUR/USD', 'Forex'], ['USD/JPY', 'Forex'],
  ['BTC/USD', 'Crypto'], ['ETH/USD', 'Crypto'],
];

async function ageMin(sym, type, tf) {
  const to = Math.floor(Date.now() / 1000), from = to - 3 * 86400;
  try {
    const c = await fetch(`${BASE}/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=${tf}&from=${from}&to=${to}`).then(r => r.json());
    if (Array.isArray(c) && c.length) return Math.round((Date.now() / 1000 - c[c.length - 1].time) / 60);
  } catch {}
  return null;
}

const lines = [];
const log = (s) => { console.log(s); lines.push(s); };

log(`# Yahoo feed-delay check — ${new Date().toISOString()}`);
log('');
log('| Instrument | Type | 5m bar age | 15m bar age |');
log('|---|---|---|---|');
for (const [sym, type] of SET) {
  const [a5, a15] = await Promise.all([ageMin(sym, type, '5m'), ageMin(sym, type, '15m')]);
  log(`| ${sym} | ${type} | ${a5 == null ? 'n/a' : a5 + 'm'} | ${a15 == null ? 'n/a' : a15 + 'm'} |`);
}
log('');
log('_Meaningful for stocks/ETFs only during US market hours (14:30–21:00 UTC). Crypto is 24/7; forex is closed weekends. A bar age within ~1–2 bar-widths = near real-time; much larger = the free feed is delayed._');

if (process.env.GITHUB_STEP_SUMMARY) { try { appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join('\n') + '\n'); } catch {} }
