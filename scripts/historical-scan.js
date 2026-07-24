#!/usr/bin/env node
// ══════════════════════════════════════════════════════════════════════════════
// ApexFX Historical Pattern Scan — builds the AI learning database
//
// USAGE:
//   node scripts/historical-scan.js
//
// SETUP (run once):
//   npm install @supabase/supabase-js
//   Then run this file. It fetches historical data, detects setups using
//   10 trading methods, calculates outcomes from the OHLCV data (no AI
//   needed for history), and stores everything in Supabase.
//
// WHAT IT DOES:
//   • Fetches 2 years of OHLCV data per symbol/timeframe
//   • Runs 10 method detectors on every bar
//   • Calculates if each setup's TP or SL was hit first (ground truth)
//   • Generates 12-dim feature vectors (same format as the live app)
//   • Uploads results to apex_analyses in Supabase
//
// Run once, go make a coffee. Future analyses will match against this database
// instead of calling Groq — especially for common patterns.
// ══════════════════════════════════════════════════════════════════════════════

import { createClient } from '@supabase/supabase-js';

// ── Config ────────────────────────────────────────────────────────────────────
const SUPA_URL  = 'https://cuvchjhaojhmxfgczndy.supabase.co';
const SUPA_KEY  = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';

// Symbols and timeframes to scan — add or remove as needed
const SYMBOLS = [
  { sym: 'BTC-USD',  type: 'Crypto' },
  { sym: 'ETH-USD',  type: 'Crypto' },
  { sym: 'SOL-USD',  type: 'Crypto' },
  { sym: 'BNB-USD',  type: 'Crypto' },
  { sym: 'XRP-USD',  type: 'Crypto' },
  { sym: 'ADA-USD',  type: 'Crypto' },
  { sym: 'DOGE-USD', type: 'Crypto' },
  { sym: 'AVAX-USD', type: 'Crypto' },
];

const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];

// Lookback per timeframe — Yahoo Finance hard limits on intraday history
const LOOKBACK_DAYS_BY_TF = {
  '1m':  7,    // Yahoo only keeps 7 days of 1m data
  '5m':  60,
  '15m': 60,
  '30m': 60,
  '1h':  730,
  '4h':  730,
  '1d':  730,
};

// How many bars forward to check for TP/SL hit after a setup triggers
const OUTCOME_BARS = 50;

// Upload in batches to avoid Supabase rate limits
const BATCH_SIZE = 50;

// ── Supabase client ────────────────────────────────────────────────────────────
const supa = createClient(SUPA_URL, SUPA_KEY);

// ── Yahoo Finance fetch ────────────────────────────────────────────────────────
const YF_INTERVAL  = { '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m', '1h': '60m', '4h': '60m', '1d': '1d' };
const YF_MAX_CHUNK = { '1m': 1 * 86400, '5m': 7 * 86400, '15m': 14 * 86400, '30m': 20 * 86400, '1h': 90 * 86400, '4h': 90 * 86400, '1d': 730 * 86400 };

function toYahooTicker(sym, type) {
  // Crypto tickers (e.g. BTC-USD) work directly with Yahoo Finance
  return sym;
}

async function fetchBars(sym, type, tf) {
  const ticker      = toYahooTicker(sym, type);
  const interval    = YF_INTERVAL[tf] || '1d';
  const lookbackDays = LOOKBACK_DAYS_BY_TF[tf] || 730;
  const now         = Math.floor(Date.now() / 1000);
  const from        = now - lookbackDays * 86400;
  const maxChunk = YF_MAX_CHUNK[tf] || 730 * 86400;
  const chunks   = [];
  let to = now;
  while (to > from) {
    const chunkFrom = Math.max(from, to - maxChunk);
    chunks.push({ from: chunkFrom, to });
    to = chunkFrom - 1;
  }

  const allBars = [];
  for (const chunk of chunks) {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}` +
      `?period1=${chunk.from}&period2=${chunk.to}&interval=${interval}&events=history&includePrePost=false`;
    try {
      const res  = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
      const json = await res.json();
      const r    = json?.chart?.result?.[0];
      if (!r?.timestamp?.length) continue;
      const q = r.indicators.quote[0];
      const bars = r.timestamp.map((t, i) => ({
        time:   t,
        open:   q.open[i]  != null ? +q.open[i].toFixed(5)  : null,
        high:   q.high[i]  != null ? +q.high[i].toFixed(5)  : null,
        low:    q.low[i]   != null ? +q.low[i].toFixed(5)   : null,
        close:  q.close[i] != null ? +q.close[i].toFixed(5) : null,
        volume: q.volume?.[i] || 0,
      })).filter(b => b.open && b.high && b.low && b.close);
      allBars.push(...bars);
    } catch(e) {
      console.warn(`  Fetch error for ${sym} ${tf}: ${e.message}`);
    }
    await sleep(300); // polite delay between requests
  }

  // Aggregate 60m bars → 4h
  if (tf === '4h') {
    const agg = [];
    for (let i = 0; i < allBars.length; i += 4) {
      const c = allBars.slice(i, i + 4);
      if (!c.length) continue;
      agg.push({
        time: c[0].time, open: c[0].open,
        high: Math.max(...c.map(b => b.high)),
        low:  Math.min(...c.map(b => b.low)),
        close: c[c.length - 1].close,
        volume: c.reduce((s, b) => s + b.volume, 0),
      });
    }
    return agg;
  }

  // Deduplicate and sort
  const seen = new Map();
  allBars.forEach(b => seen.set(b.time, b));
  return [...seen.values()].sort((a, b) => a.time - b.time);
}

// ── Indicator helpers ──────────────────────────────────────────────────────────
function calcATR(bars, n = 14) {
  const tr = bars.map((b, i) => {
    if (i === 0) return b.high - b.low;
    const prev = bars[i - 1];
    return Math.max(b.high - b.low, Math.abs(b.high - prev.close), Math.abs(b.low - prev.close));
  });
  const atr = new Array(bars.length).fill(0);
  atr[n - 1] = tr.slice(0, n).reduce((s, v) => s + v, 0) / n;
  for (let i = n; i < bars.length; i++) atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n;
  return atr;
}

function calcSMA(bars, n) {
  return bars.map((_, i) => {
    if (i < n - 1) return null;
    return bars.slice(i - n + 1, i + 1).reduce((s, b) => s + b.close, 0) / n;
  });
}

function calcRSI(bars, n = 14) {
  const rsi = new Array(bars.length).fill(null);
  let gains = 0, losses = 0;
  for (let i = 1; i <= n; i++) {
    const d = bars[i].close - bars[i - 1].close;
    if (d > 0) gains += d; else losses -= d;
  }
  let avgG = gains / n, avgL = losses / n;
  rsi[n] = 100 - 100 / (1 + avgG / (avgL || 0.0001));
  for (let i = n + 1; i < bars.length; i++) {
    const d = bars[i].close - bars[i - 1].close;
    const g = d > 0 ? d : 0, l = d < 0 ? -d : 0;
    avgG = (avgG * (n - 1) + g) / n;
    avgL = (avgL * (n - 1) + l) / n;
    rsi[i] = 100 - 100 / (1 + avgG / (avgL || 0.0001));
  }
  return rsi;
}

function calcMACD(bars, fast = 12, slow = 26, sig = 9) {
  const ema = (arr, n, start) => {
    const k = 2 / (n + 1);
    let val = arr.slice(start, start + n).reduce((s, v) => s + v, 0) / n;
    const out = new Array(arr.length).fill(null);
    out[start + n - 1] = val;
    for (let i = start + n; i < arr.length; i++) {
      val = arr[i] * k + val * (1 - k);
      out[i] = val;
    }
    return out;
  };
  const closes = bars.map(b => b.close);
  const emaFast = ema(closes, fast, 0);
  const emaSlow = ema(closes, slow, 0);
  const macdLine = bars.map((_, i) =>
    emaFast[i] != null && emaSlow[i] != null ? emaFast[i] - emaSlow[i] : null
  );
  const macdVals = macdLine.filter(v => v != null);
  const sigLine  = ema(macdLine.map(v => v ?? 0), sig, slow - 1);
  return bars.map((_, i) => ({
    macd: macdLine[i],
    signal: sigLine[i],
    hist:   macdLine[i] != null && sigLine[i] != null ? macdLine[i] - sigLine[i] : null,
  }));
}

// ── Outcome calculation ────────────────────────────────────────────────────────
// Returns 'tp_hit' | 'sl_hit' | 'pending' based on what happened in OUTCOME_BARS forward
function calcOutcome(bars, setupBar, entry, sl, tp, dir) {
  for (let i = setupBar + 1; i < Math.min(bars.length, setupBar + OUTCOME_BARS + 1); i++) {
    const b = bars[i];
    if (dir === 'long') {
      if (b.low  <= sl) return 'sl_hit';
      if (b.high >= tp) return 'tp_hit';
    } else {
      if (b.high >= sl) return 'sl_hit';
      if (b.low  <= tp) return 'tp_hit';
    }
  }
  return 'pending'; // neither hit in the window
}

// ── Feature vector ─────────────────────────────────────────────────────────────
// 12-dimensional normalised vector — same definition as app.js tapExtractFeatureVector
function buildFeatureVector(bars, i, dir, rr, slAtr, atr, patConf, patAligns, srProx) {
  const rsiVals = calcRSI(bars);
  const sma20   = calcSMA(bars, 20);
  const sma50   = calcSMA(bars, 50);
  const macdVals = calcMACD(bars);
  const rsi  = rsiVals[i] ?? 50;
  const s20  = sma20[i]  ?? 0;
  const s50  = sma50[i]  ?? 0;
  const macdHist = macdVals[i]?.hist ?? 0;
  const recentBars = bars.slice(Math.max(0, i - 19), i + 1);
  const avgVol = recentBars.reduce((s, b) => s + b.volume, 0) / recentBars.length;
  const volRatio = avgVol > 0 ? bars[i].volume / avgVol : 1;
  return [
    rsi / 100,
    (s20 > 0 && s50 > 0) ? (s20 > s50 ? 1 : 0) : 0.5,
    dir === 'long' ? 1 : 0,
    Math.min(rr / 4, 1),
    Math.min(slAtr / 3, 1),
    Math.max(0, Math.min(1, patConf)),
    patAligns,
    Math.max(0, Math.min(1, srProx)),
    Math.min(volRatio / 3, 1),
    macdHist > 0 ? 1 : 0,
    atr > 0 ? Math.min(Math.abs(macdHist) / atr, 1) : 0,
    rsi > 50 ? 1 : 0,
  ];
}

// ── S/R detection helper ───────────────────────────────────────────────────────
function findSRLevels(bars, i, lookback = 30) {
  const slice = bars.slice(Math.max(0, i - lookback), i);
  const support = [], resistance = [];
  for (let j = 1; j < slice.length - 1; j++) {
    if (slice[j].low  < slice[j-1].low  && slice[j].low  < slice[j+1].low)  support.push(slice[j].low);
    if (slice[j].high > slice[j-1].high && slice[j].high > slice[j+1].high) resistance.push(slice[j].high);
  }
  return { support, resistance };
}

// ── 10 Method Detectors ────────────────────────────────────────────────────────
// Each returns an array of { dir, entry, sl, tp, method, subtype, patConf, patAligns, srProx }
// or empty array if no setup found at bar i.

function detectICT(bars, i, atr) {
  if (i < 4) return [];
  const setups = [];
  const b0 = bars[i - 2], b2 = bars[i];
  // Bullish FVG: b0.high < b2.low — gap not filled
  if (b0.high < b2.low && b2.close > b2.open) {
    const entry = (b2.low + b0.high) / 2;  // 50% OTE
    const sl    = b0.high - atr * 0.3;
    const tp    = entry + (entry - sl) * 2.5;
    const sr    = findSRLevels(bars, i);
    const nearSR = [...sr.support, ...sr.resistance].length > 0
      ? Math.min(...[...sr.support, ...sr.resistance].map(v => Math.abs(v - entry))) / (atr * 2)
      : 1;
    setups.push({ dir: 'long', entry, sl, tp, method: 'ICT', subtype: 'Bullish FVG',
      patConf: 0.72, patAligns: 1, srProx: 1 - Math.min(nearSR, 1) });
  }
  // Bearish FVG: b0.low > b2.high
  if (b0.low > b2.high && b2.close < b2.open) {
    const entry = (b2.high + b0.low) / 2;
    const sl    = b0.low + atr * 0.3;
    const tp    = entry - (sl - entry) * 2.5;
    const sr    = findSRLevels(bars, i);
    const nearSR = [...sr.support, ...sr.resistance].length > 0
      ? Math.min(...[...sr.support, ...sr.resistance].map(v => Math.abs(v - entry))) / (atr * 2)
      : 1;
    setups.push({ dir: 'short', entry, sl, tp, method: 'ICT', subtype: 'Bearish FVG',
      patConf: 0.72, patAligns: 0, srProx: 1 - Math.min(nearSR, 1) });
  }
  return setups;
}

function detectSMC(bars, i, atr) {
  // Break of Structure: new high/low beyond recent swing, then retest
  if (i < 20) return [];
  const setups = [];
  const slice  = bars.slice(i - 20, i);
  const swingH = Math.max(...slice.map(b => b.high));
  const swingL = Math.min(...slice.map(b => b.low));
  const cur    = bars[i];
  // Bullish BOS: close above recent swing high after pullback
  if (cur.close > swingH && bars[i - 1].close < swingH) {
    const entry  = cur.close;
    const sl     = swingL > entry - atr * 3 ? swingL : entry - atr * 2;
    const tp     = entry + (entry - sl) * 2;
    setups.push({ dir: 'long', entry, sl, tp, method: 'SMC', subtype: 'Bullish BOS',
      patConf: 0.68, patAligns: 1, srProx: 0.8 });
  }
  // Bearish BOS: close below recent swing low
  if (cur.close < swingL && bars[i - 1].close > swingL) {
    const entry = cur.close;
    const sl    = swingH < entry + atr * 3 ? swingH : entry + atr * 2;
    const tp    = entry - (sl - entry) * 2;
    setups.push({ dir: 'short', entry, sl, tp, method: 'SMC', subtype: 'Bearish BOS',
      patConf: 0.68, patAligns: 0, srProx: 0.8 });
  }
  return setups;
}

function detectSupplyDemand(bars, i, atr) {
  // Supply zone: explosive down move from a base; Demand zone: explosive up from a base
  if (i < 5) return [];
  const setups = [];
  const baseCandle   = bars[i - 3];
  const moveCandle   = bars[i - 2];
  const retestCandle = bars[i];
  const baseSize  = Math.abs(baseCandle.close - baseCandle.open);
  const moveSize  = Math.abs(moveCandle.close - moveCandle.open);
  // Demand: small base + explosive up + retest into base zone
  if (moveSize > atr * 1.5 && moveCandle.close > moveCandle.open &&
      baseSize < atr * 0.6 && retestCandle.low <= baseCandle.high) {
    const entry = baseCandle.high;
    const sl    = baseCandle.low - atr * 0.2;
    const tp    = entry + (entry - sl) * 2.5;
    setups.push({ dir: 'long', entry, sl, tp, method: 'Supply & Demand', subtype: 'Demand Zone',
      patConf: 0.70, patAligns: 1, srProx: 0.9 });
  }
  // Supply: small base + explosive down + retest
  if (moveSize > atr * 1.5 && moveCandle.close < moveCandle.open &&
      baseSize < atr * 0.6 && retestCandle.high >= baseCandle.low) {
    const entry = baseCandle.low;
    const sl    = baseCandle.high + atr * 0.2;
    const tp    = entry - (sl - entry) * 2.5;
    setups.push({ dir: 'short', entry, sl, tp, method: 'Supply & Demand', subtype: 'Supply Zone',
      patConf: 0.70, patAligns: 0, srProx: 0.9 });
  }
  return setups;
}

function detectSupportResistance(bars, i, atr) {
  if (i < 20) return [];
  const setups = [];
  const sr  = findSRLevels(bars, i);
  const cur = bars[i];
  // Bounce off support
  for (const level of sr.support) {
    const dist = Math.abs(cur.low - level);
    if (dist < atr * 0.4 && cur.close > cur.open && cur.close > level) {
      const entry = cur.close;
      const sl    = level - atr * 0.5;
      const tp    = entry + (entry - sl) * 2;
      const nearR = sr.resistance.filter(r => r > entry).sort((a, b) => a - b)[0];
      if (nearR && nearR - entry > entry - sl) {
        setups.push({ dir: 'long', entry, sl, tp: Math.min(tp, nearR),
          method: 'Support & Resistance', subtype: 'Support Bounce',
          patConf: 0.65, patAligns: 1, srProx: 1 - dist / (atr * 2) });
      }
    }
  }
  // Rejection at resistance
  for (const level of sr.resistance) {
    const dist = Math.abs(cur.high - level);
    if (dist < atr * 0.4 && cur.close < cur.open && cur.close < level) {
      const entry = cur.close;
      const sl    = level + atr * 0.5;
      const tp    = entry - (sl - entry) * 2;
      const nearS = sr.support.filter(s => s < entry).sort((a, b) => b - a)[0];
      if (nearS && entry - nearS > sl - entry) {
        setups.push({ dir: 'short', entry, sl, tp: Math.max(tp, nearS),
          method: 'Support & Resistance', subtype: 'Resistance Rejection',
          patConf: 0.65, patAligns: 0, srProx: 1 - dist / (atr * 2) });
      }
    }
  }
  return setups;
}

function detectMATrend(bars, i, atr, sma20, sma50) {
  if (i < 50 || !sma20[i] || !sma50[i]) return [];
  const setups = [];
  const cur    = bars[i];
  const prev   = bars[i - 1];
  // Golden cross pullback: SMA20 > SMA50, price pulls back to SMA20 then bounces
  if (sma20[i] > sma50[i] && sma20[i - 1] > sma50[i - 1]) {
    const prevLow = Math.min(...bars.slice(i - 3, i + 1).map(b => b.low));
    if (prevLow <= sma20[i] * 1.002 && cur.close > sma20[i] && cur.close > prev.close) {
      const entry = cur.close;
      const sl    = sma20[i] - atr * 0.5;
      const tp    = entry + (entry - sl) * 2.5;
      setups.push({ dir: 'long', entry, sl, tp, method: 'MA / Trend Following', subtype: 'SMA20 Pullback',
        patConf: 0.62, patAligns: 1, srProx: 0.7 });
    }
  }
  // Death cross: SMA20 < SMA50, price rallies to SMA20 then rejects
  if (sma20[i] < sma50[i] && sma20[i - 1] < sma50[i - 1]) {
    const prevHigh = Math.max(...bars.slice(i - 3, i + 1).map(b => b.high));
    if (prevHigh >= sma20[i] * 0.998 && cur.close < sma20[i] && cur.close < prev.close) {
      const entry = cur.close;
      const sl    = sma20[i] + atr * 0.5;
      const tp    = entry - (sl - entry) * 2.5;
      setups.push({ dir: 'short', entry, sl, tp, method: 'MA / Trend Following', subtype: 'SMA20 Rejection',
        patConf: 0.62, patAligns: 0, srProx: 0.7 });
    }
  }
  return setups;
}

function detectPriceAction(bars, i, atr) {
  if (i < 5) return [];
  const setups = [];
  const b   = bars[i];
  const sr  = findSRLevels(bars, i);
  const body  = Math.abs(b.close - b.open);
  const upper = b.high - Math.max(b.open, b.close);
  const lower = Math.min(b.open, b.close) - b.low;
  // Bullish pin bar: long lower wick (2× body), small upper wick, near support
  if (lower > body * 2 && upper < body * 0.5 && lower > atr * 0.4) {
    const nearSupport = sr.support.filter(s => Math.abs(s - b.low) < atr).length > 0;
    if (nearSupport) {
      const entry = b.close;
      const sl    = b.low - atr * 0.2;
      const tp    = entry + (entry - sl) * 2.5;
      setups.push({ dir: 'long', entry, sl, tp, method: 'Price Action', subtype: 'Bullish Pin Bar',
        patConf: 0.75, patAligns: 1, srProx: 0.85 });
    }
  }
  // Bearish pin bar: long upper wick
  if (upper > body * 2 && lower < body * 0.5 && upper > atr * 0.4) {
    const nearResistance = sr.resistance.filter(r => Math.abs(r - b.high) < atr).length > 0;
    if (nearResistance) {
      const entry = b.close;
      const sl    = b.high + atr * 0.2;
      const tp    = entry - (sl - entry) * 2.5;
      setups.push({ dir: 'short', entry, sl, tp, method: 'Price Action', subtype: 'Bearish Pin Bar',
        patConf: 0.75, patAligns: 0, srProx: 0.85 });
    }
  }
  // Bullish engulfing
  if (i > 0) {
    const prev = bars[i - 1];
    if (prev.close < prev.open && b.close > b.open &&
        b.open < prev.close && b.close > prev.open && body > atr * 0.5) {
      const entry = b.close;
      const sl    = b.low - atr * 0.3;
      const tp    = entry + (entry - sl) * 2.5;
      setups.push({ dir: 'long', entry, sl, tp, method: 'Price Action', subtype: 'Bullish Engulfing',
        patConf: 0.68, patAligns: 1, srProx: 0.6 });
    }
    // Bearish engulfing
    if (prev.close > prev.open && b.close < b.open &&
        b.open > prev.close && b.close < prev.open && body > atr * 0.5) {
      const entry = b.close;
      const sl    = b.high + atr * 0.3;
      const tp    = entry - (sl - entry) * 2.5;
      setups.push({ dir: 'short', entry, sl, tp, method: 'Price Action', subtype: 'Bearish Engulfing',
        patConf: 0.68, patAligns: 0, srProx: 0.6 });
    }
  }
  return setups;
}

function detectBreakout(bars, i, atr) {
  if (i < 20) return [];
  const setups = [];
  const lookback = 20;
  const slice    = bars.slice(i - lookback, i);
  const rangeH   = Math.max(...slice.map(b => b.high));
  const rangeL   = Math.min(...slice.map(b => b.low));
  const rangeSize = rangeH - rangeL;
  const cur = bars[i];
  // Breakout must be confirmed with volume above average
  const avgVol = slice.reduce((s, b) => s + b.volume, 0) / lookback;
  if (cur.volume < avgVol * 1.2) return setups; // requires volume confirmation
  // Bullish breakout
  if (cur.close > rangeH && bars[i - 1].close <= rangeH) {
    const entry = cur.close;
    const sl    = rangeH - atr * 0.3;    // retest of breakout level
    const tp    = entry + rangeSize;     // equal range projection
    setups.push({ dir: 'long', entry, sl, tp, method: 'Breakout', subtype: 'Range Breakout Long',
      patConf: 0.63, patAligns: 1, srProx: 0.9 });
  }
  // Bearish breakdown
  if (cur.close < rangeL && bars[i - 1].close >= rangeL) {
    const entry = cur.close;
    const sl    = rangeL + atr * 0.3;
    const tp    = entry - rangeSize;
    setups.push({ dir: 'short', entry, sl, tp, method: 'Breakout', subtype: 'Range Breakdown Short',
      patConf: 0.63, patAligns: 0, srProx: 0.9 });
  }
  return setups;
}

function detectFibonacci(bars, i, atr) {
  if (i < 30) return [];
  const setups = [];
  // Find a clear swing: 15-bar lookback for swing high/low
  const slice   = bars.slice(i - 30, i);
  const swingH  = Math.max(...slice.map(b => b.high));
  const swingL  = Math.min(...slice.map(b => b.low));
  const swing   = swingH - swingL;
  if (swing < atr * 2) return []; // too small to be meaningful
  const swingHIdx = slice.findIndex(b => b.high === swingH);
  const swingLIdx = slice.findIndex(b => b.low  === swingL);
  const cur = bars[i];
  // Bullish: swing was up (low before high), price pulled back to 61.8%
  if (swingLIdx < swingHIdx) {
    const fib618 = swingH - swing * 0.618;
    const fib382 = swingH - swing * 0.382;
    if (cur.low <= fib618 * 1.001 && cur.close >= fib618 && cur.close > cur.open) {
      const entry = cur.close;
      const sl    = swingL - atr * 0.3;
      const tp    = swingH + swing * 0.272; // 127.2% extension
      setups.push({ dir: 'long', entry, sl, tp, method: 'Fibonacci', subtype: '61.8% Retracement Long',
        patConf: 0.70, patAligns: 1, srProx: 0.85 });
    }
  }
  // Bearish: swing was down
  if (swingHIdx < swingLIdx) {
    const fib618 = swingL + swing * 0.618;
    if (cur.high >= fib618 * 0.999 && cur.close <= fib618 && cur.close < cur.open) {
      const entry = cur.close;
      const sl    = swingH + atr * 0.3;
      const tp    = swingL - swing * 0.272;
      setups.push({ dir: 'short', entry, sl, tp, method: 'Fibonacci', subtype: '61.8% Retracement Short',
        patConf: 0.70, patAligns: 0, srProx: 0.85 });
    }
  }
  return setups;
}

function detectRSIMomentum(bars, i, atr, rsiVals) {
  if (i < 20 || rsiVals[i] == null) return [];
  const setups = [];
  const rsi = rsiVals[i];
  const sr  = findSRLevels(bars, i);
  const cur = bars[i];
  // RSI oversold at support — look for RSI turning up
  if (rsi < 35 && rsiVals[i - 1] != null && rsiVals[i] > rsiVals[i - 1]) {
    const nearSupport = sr.support.filter(s => Math.abs(s - cur.low) < atr * 1.5).length > 0;
    if (nearSupport && cur.close > cur.open) {
      const entry = cur.close;
      const sl    = cur.low - atr * 0.4;
      const tp    = entry + (entry - sl) * 2.5;
      setups.push({ dir: 'long', entry, sl, tp, method: 'RSI / Momentum', subtype: 'RSI Oversold Bounce',
        patConf: 0.65, patAligns: 1, srProx: 0.8 });
    }
  }
  // RSI overbought at resistance
  if (rsi > 65 && rsiVals[i - 1] != null && rsiVals[i] < rsiVals[i - 1]) {
    const nearResistance = sr.resistance.filter(r => Math.abs(r - cur.high) < atr * 1.5).length > 0;
    if (nearResistance && cur.close < cur.open) {
      const entry = cur.close;
      const sl    = cur.high + atr * 0.4;
      const tp    = entry - (sl - entry) * 2.5;
      setups.push({ dir: 'short', entry, sl, tp, method: 'RSI / Momentum', subtype: 'RSI Overbought Rejection',
        patConf: 0.65, patAligns: 0, srProx: 0.8 });
    }
  }
  return setups;
}

function detectWyckoff(bars, i, atr, sma20, sma50) {
  if (i < 40 || !sma20[i] || !sma50[i]) return [];
  const setups = [];
  const slice  = bars.slice(i - 30, i + 1);
  const phigh  = Math.max(...slice.map(b => b.high));
  const plow   = Math.min(...slice.map(b => b.low));
  const range  = phigh - plow;
  const cur    = bars[i];
  // Wyckoff Spring: in a trading range (small range), price dips below support then closes back inside
  // Indicates accumulation — the "spring" shakes out weak holders
  const rangeIsSmall = range < atr * 8; // trading range, not trending
  if (rangeIsSmall && cur.low < plow && cur.close > plow && cur.close > cur.open) {
    const entry = cur.close;
    const sl    = cur.low - atr * 0.3;
    const tp    = phigh + atr * 1.5; // markup target above range
    setups.push({ dir: 'long', entry, sl, tp, method: 'Wyckoff', subtype: 'Spring (Accumulation)',
      patConf: 0.67, patAligns: 1, srProx: 0.75 });
  }
  // Wyckoff Upthrust: in range, price spikes above resistance then closes back inside (distribution)
  if (rangeIsSmall && cur.high > phigh && cur.close < phigh && cur.close < cur.open) {
    const entry = cur.close;
    const sl    = cur.high + atr * 0.3;
    const tp    = plow - atr * 1.5; // markdown target below range
    setups.push({ dir: 'short', entry, sl, tp, method: 'Wyckoff', subtype: 'Upthrust (Distribution)',
      patConf: 0.67, patAligns: 0, srProx: 0.75 });
  }
  return setups;
}

// ── Main scan ──────────────────────────────────────────────────────────────────
async function scanSymbolTF(sym, type, tf) {
  console.log(`  Fetching ${sym} ${tf}...`);
  const bars = await fetchBars(sym, type, tf);
  if (bars.length < 60) {
    console.log(`  ⚠ Only ${bars.length} bars — skipping`);
    return 0;
  }
  console.log(`  ${bars.length} bars loaded. Running detectors...`);

  const atrVals  = calcATR(bars);
  const rsiVals  = calcRSI(bars);
  const sma20    = calcSMA(bars, 20);
  const sma50    = calcSMA(bars, 50);
  const macdVals = calcMACD(bars);

  const allSetups = [];

  for (let i = 40; i < bars.length - OUTCOME_BARS - 1; i++) {
    const atr = atrVals[i];
    if (!atr || atr === 0) continue;

    const candidates = [
      ...detectICT(bars, i, atr),
      ...detectSMC(bars, i, atr),
      ...detectSupplyDemand(bars, i, atr),
      ...detectSupportResistance(bars, i, atr),
      ...detectMATrend(bars, i, atr, sma20, sma50),
      ...detectPriceAction(bars, i, atr),
      ...detectBreakout(bars, i, atr),
      ...detectFibonacci(bars, i, atr),
      ...detectRSIMomentum(bars, i, atr, rsiVals),
      ...detectWyckoff(bars, i, atr, sma20, sma50),
    ];

    for (const setup of candidates) {
      const { dir, entry, sl, tp, method, subtype, patConf, patAligns, srProx } = setup;
      if (!entry || !sl || !tp || sl === entry || tp === entry) continue;
      const rr     = Math.abs(tp - entry) / Math.abs(sl - entry);
      const slAtr  = Math.abs(entry - sl) / atr;
      if (rr < 1.2 || rr > 6) continue;    // filter unrealistic R:R
      if (slAtr < 0.3 || slAtr > 5) continue; // filter stops too tight or too wide
      const outcome = calcOutcome(bars, i, entry, sl, tp, dir);
      const fv = buildFeatureVector(bars, i, dir, rr, slAtr, atr, patConf, patAligns, srProx);
      allSetups.push({
        id: `hist-${sym.replace(/[^a-z0-9]/gi, '')}-${tf}-${method.replace(/\s/g,'-')}-${i}-${Date.now()}`,
        user_id:         'anonymous',
        symbol:          sym,
        timeframe:       tf,
        direction:       dir,
        feature_vector:  fv,
        analysis_text:   `[Historical] ${method} · ${subtype} · ${sym} ${tf} · R:R ${rr.toFixed(2)}:1 · Outcome: ${outcome}`,
        scorecard:       null,
        verdict:         outcome === 'tp_hit' ? 'Strong Setup' : outcome === 'sl_hit' ? 'Risky Setup' : null,
        combined_score:  null,
        probability:     null,
        method_detected: method,
        entry_price:     entry,
        sl_price:        sl,
        tp_price:        tp,
        outcome,
        verdict_correct: outcome === 'tp_hit',
        outcome_at:      outcome !== 'pending' ? new Date(bars[i].time * 1000).toISOString() : null,
        created_at:      new Date(bars[i].time * 1000).toISOString(),
      });
    }
  }

  console.log(`  Found ${allSetups.length} setups. Uploading to Supabase...`);

  // Upload in batches
  // Re-assign IDs with a global counter to guarantee uniqueness within batches
  allSetups.forEach((s, idx) => { s.id = s.id.split('-').slice(0,-1).join('-') + `-${idx}`; });

  let uploaded = 0;
  for (let i = 0; i < allSetups.length; i += BATCH_SIZE) {
    const batch = allSetups.slice(i, i + BATCH_SIZE);
    const { error } = await supa.from('apex_analyses').upsert(batch, { onConflict: 'id' });
    if (error) {
      console.warn(`  ⚠ Upload error (batch ${i / BATCH_SIZE + 1}): ${error.message}`);
    } else {
      uploaded += batch.length;
    }
    await sleep(200);
  }

  console.log(`  ✓ ${uploaded}/${allSetups.length} setups stored`);
  return uploaded;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Entry point ────────────────────────────────────────────────────────────────
async function main() {
  console.log('╔══════════════════════════════════════════════════╗');
  console.log('║  ApexFX Historical Pattern Scan                  ║');
  console.log('║  Building AI learning database from history...   ║');
  console.log('╚══════════════════════════════════════════════════╝\n');

  // Test Supabase connection
  const { error: pingErr } = await supa.from('apex_analyses').select('id').limit(1);
  if (pingErr) {
    console.error('❌ Cannot connect to Supabase:', pingErr.message);
    console.error('   Make sure you have run supabase/apex_analyses.sql first.');
    process.exit(1);
  }
  console.log('✓ Supabase connected\n');

  let totalUploaded = 0;
  for (const { sym, type } of SYMBOLS) {
    for (const tf of TIMEFRAMES) {
      console.log(`\n[${sym} · ${tf}]`);
      try {
        const n = await scanSymbolTF(sym, type, tf);
        totalUploaded += n;
      } catch(e) {
        console.warn(`  ❌ Error: ${e.message}`);
      }
      await sleep(1000); // polite delay between symbol/tf combos
    }
  }

  console.log(`\n╔══════════════════════════════════════════════════╗`);
  console.log(`║  Scan complete!                                   ║`);
  console.log(`║  Total setups stored: ${String(totalUploaded).padEnd(26)}║`);
  console.log(`║  The AI will now use this database to reduce      ║`);
  console.log(`║  Groq calls for similar future setups.            ║`);
  console.log(`╚══════════════════════════════════════════════════╝`);
}

main().catch(e => { console.error('Fatal error:', e); process.exit(1); });
