// APEX shared TA library — pure indicators + asset-type detection.
//
// These function bodies are copied VERBATIM from public/dashboard.js (the live
// analysis layer) so the backtest computes identical indicators. Do not edit the
// math here in isolation — a Node parity test (test/parity.test.mjs) asserts that
// every function in this file produces output identical to dashboard.js on a
// fixture. The whole file is DOM-free so it loads in the page (<script>), in the
// backtest Web Worker (importScripts), and in Node (require) alike.
//
// Exposed as globalThis.APEX.ta — see the attach block at the bottom.
'use strict';

// ── Asset type detection (verbatim from dashboard.js) ─────────────────────────
const CRYPTO_BASES = new Set(['BTC','ETH','SOL','ADA','XRP','DOGE','AVAX','MATIC','DOT','LINK','LTC','ATOM','UNI','AAVE','BNB','SHIB','ARB','OP','SUI','APT','INJ']);
const FOREX_CCY   = new Set(['EUR','GBP','USD','JPY','CHF','AUD','CAD','NZD','SEK','NOK','DKK','HKD','SGD','MXN','ZAR','TRY']);
const ETFS        = new Set(['SPY','QQQ','IWM','GLD','SLV','USO','TLT','HYG','LQD','XLF','XLE','XLK','XLV','XLI','XLC','ARKK','VTI','VOO','VNQ','EEM','EFA','GDX','GDXJ','XBI','IBB','DIA','SMH','SOXX']);
const FUTURES_RX  = /^(ES|NQ|CL|GC|SI|ZB|ZN|ZF|NG|HG|PL|PA|KC|CT|SB)1!$/;

function detectType(raw) {
  const s = raw.toUpperCase().trim();
  if (CRYPTO_BASES.has(s)) return 'Crypto';
  if (CRYPTO_BASES.has(s.replace(/[/-]USD[T]?$/, ''))) return 'Crypto';
  if ((s.endsWith('-USD') || s.endsWith('/USD') || s.endsWith('USDT')) && !FOREX_CCY.has(s.split(/[-/]/)[0])) return 'Crypto';
  if (/^[A-Z]{3}\/[A-Z]{3}$/.test(s) && FOREX_CCY.has(s.split('/')[0]) && FOREX_CCY.has(s.split('/')[1])) return 'Forex';
  if (FUTURES_RX.test(s)) return 'Futures';
  if (ETFS.has(s)) return 'ETF';
  return 'Stock';
}

// ── Technical indicators (verbatim from dashboard.js) ─────────────────────────

function calcSMA(c, p) {
  return c.length < p ? null : c.slice(-p).reduce((a, b) => a + b, 0) / p;
}
function calcEMA(c, p) {
  if (c.length < p) return null;
  const k = 2 / (p + 1);
  let e = c.slice(0, p).reduce((a, b) => a + b, 0) / p;
  for (let i = p; i < c.length; i++) e = c[i] * k + e * (1 - k);
  return e;
}
function calcRSI(c, p = 14) {
  if (c.length < p + 2) return null;
  const d = c.slice(1).map((v, i) => v - c[i]);
  let ag = 0, al = 0;
  const start = Math.max(0, d.length - p * 3);
  for (let i = start; i < start + p; i++) { const x = d[i] || 0; x > 0 ? ag += x : al -= x; }
  ag /= p; al /= p;
  for (let i = start + p; i < d.length; i++) {
    const x = d[i];
    ag = (ag * (p - 1) + Math.max(0, x)) / p;
    al = (al * (p - 1) + Math.max(0, -x)) / p;
  }
  return al === 0 ? 100 : Math.round(100 - 100 / (1 + ag / al));
}
function calcMACD(c) {
  const e12 = calcEMA(c, 12), e26 = calcEMA(c, 26);
  return (e12 && e26) ? e12 - e26 : null;
}
function calcATR(bars, p = 14) {
  if (bars.length < p + 1) return null;
  const tr = bars.slice(1).map((b, i) => Math.max(b.high - b.low, Math.abs(b.high - bars[i].close), Math.abs(b.low - bars[i].close)));
  return tr.slice(-p).reduce((a, b) => a + b, 0) / p;
}
function calcBollingerBands(closes, period = 20, mult = 2) {
  if (closes.length < period) return null;
  const slice = closes.slice(-period);
  const mean = slice.reduce((a, b) => a + b, 0) / period;
  const variance = slice.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / period;
  const std = Math.sqrt(variance);
  const curr = closes[closes.length - 1];
  const upper = mean + mult * std;
  const lower = mean - mult * std;
  const pctB = std > 0 ? Math.round((curr - lower) / (upper - lower) * 100) : 50;
  return { upper: +upper.toFixed(4), middle: +mean.toFixed(4), lower: +lower.toFixed(4), pctB };
}
function calcStochRSI(closes, rsiPeriod = 14, stochPeriod = 14) {
  if (closes.length < rsiPeriod + stochPeriod + 5) return null;
  const needed = rsiPeriod + stochPeriod + 10;
  const slice = closes.slice(-needed);
  const diffs = slice.slice(1).map((v, i) => v - slice[i]);
  let ag = 0, al = 0;
  for (let i = 0; i < rsiPeriod; i++) { const x = diffs[i] || 0; x > 0 ? ag += x : al -= x; }
  ag /= rsiPeriod; al /= rsiPeriod;
  const rsiArr = [al === 0 ? 100 : 100 - 100 / (1 + ag / al)];
  for (let i = rsiPeriod; i < diffs.length; i++) {
    const x = diffs[i];
    ag = (ag * (rsiPeriod - 1) + Math.max(0, x)) / rsiPeriod;
    al = (al * (rsiPeriod - 1) + Math.max(0, -x)) / rsiPeriod;
    rsiArr.push(al === 0 ? 100 : 100 - 100 / (1 + ag / al));
  }
  if (rsiArr.length < stochPeriod) return null;
  const recent = rsiArr.slice(-stochPeriod);
  const minR = Math.min(...recent), maxR = Math.max(...recent);
  const range = maxR - minR;
  return range === 0 ? 50 : Math.round((rsiArr[rsiArr.length - 1] - minR) / range * 100);
}
function calcOBVTrend(bars) {
  if (bars.length < 20) return 'neutral';
  let obv = 0;
  const obvArr = [];
  for (let i = 1; i < bars.length; i++) {
    if (bars[i].close > bars[i - 1].close)      obv += bars[i].volume;
    else if (bars[i].close < bars[i - 1].close) obv -= bars[i].volume;
    obvArr.push(obv);
  }
  const recent = obvArr.slice(-5).reduce((a, b) => a + b, 0) / 5;
  const older  = obvArr.slice(-20, -15).reduce((a, b) => a + b, 0) / 5;
  const diff   = older !== 0 ? (recent - older) / Math.abs(older) : 0;
  return diff > 0.03 ? 'accumulation' : diff < -0.03 ? 'distribution' : 'neutral';
}
function findPivotSR(bars, lb = 5) {
  const curr = bars[bars.length - 1].close;
  const resistances = [], supports = [];
  for (let i = lb; i < bars.length - lb; i++) {
    const h = bars[i].high, l = bars[i].low;
    let isHigh = true, isLow = true;
    for (let j = i - lb; j <= i + lb; j++) {
      if (j === i) continue;
      if (bars[j].high >= h) isHigh = false;
      if (bars[j].low  <= l) isLow  = false;
    }
    if (isHigh && h > curr) resistances.push(h);
    if (isLow  && l < curr) supports.push(l);
  }
  const cluster = arr => {
    const sorted = [...arr].sort((a, b) => a - b);
    const out = [];
    for (const v of sorted) {
      if (!out.length || Math.abs(v - out[out.length - 1]) / out[out.length - 1] > 0.005)
        out.push(v);
    }
    return out;
  };
  return {
    resistances: cluster(resistances).slice(0, 3),
    supports:    cluster(supports).reverse().slice(0, 3),
  };
}
function calcFibLevels(bars) {
  const recent = bars.slice(-120);
  const high   = Math.max(...recent.map(b => b.high));
  const low    = Math.min(...recent.map(b => b.low));
  const range  = high - low;
  return {
    high, low,
    f236: +(high - range * 0.236).toFixed(4),
    f382: +(high - range * 0.382).toFixed(4),
    f500: +(high - range * 0.500).toFixed(4),
    f618: +(high - range * 0.618).toFixed(4),
    f786: +(high - range * 0.786).toFixed(4),
  };
}
function calcVolTrend(bars) {
  if (bars.length < 20) return 'normal';
  const r5  = bars.slice(-5).reduce((s, b) => s + b.volume, 0) / 5;
  const a20 = bars.slice(-20).reduce((s, b) => s + b.volume, 0) / 20;
  return r5 > a20 * 1.4 ? 'rising' : r5 < a20 * 0.6 ? 'falling' : 'normal';
}
function calcADX(bars, period = 14) {
  if (bars.length < period * 2 + 2) return null;
  const dmP = [], dmM = [], tr = [];
  for (let i = 1; i < bars.length; i++) {
    const h = bars[i].high, l = bars[i].low;
    const ph = bars[i-1].high, pl = bars[i-1].low, pc = bars[i-1].close;
    const up = h - ph, dn = pl - l;
    dmP.push(up > dn && up > 0 ? up : 0);
    dmM.push(dn > up && dn > 0 ? dn : 0);
    tr.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
  }
  const ws = (arr, p) => {
    let s = arr.slice(0, p).reduce((a, b) => a + b, 0);
    const r = [s];
    for (let i = p; i < arr.length; i++) { s = s - s / p + arr[i]; r.push(s); }
    return r;
  };
  const sTR = ws(tr, period), sDMP = ws(dmP, period), sDMM = ws(dmM, period);
  const dx = sTR.map((t, i) => {
    if (t === 0) return 0;
    const diP = 100 * sDMP[i] / t, diM = 100 * sDMM[i] / t;
    const s = diP + diM;
    return s > 0 ? 100 * Math.abs(diP - diM) / s : 0;
  });
  if (dx.length < period) return null;
  let adx = dx.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < dx.length; i++) adx = (adx * (period - 1) + dx[i]) / period;
  return Math.round(adx);
}
function calcBBWidthPct(closes, period = 20) {
  if (closes.length < period) return null;
  const slice = closes.slice(-period);
  const mean = slice.reduce((a, b) => a + b, 0) / period;
  const std = Math.sqrt(slice.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / period);
  return mean > 0 ? +((4 * std / mean) * 100).toFixed(2) : null;
}
function getTrend(c, sma20, sma50) {
  if (!sma20 || !sma50) return 'sideways';
  const p = c[c.length - 1];
  if (p > sma20 && sma20 > sma50) return 'bullish';
  if (p < sma20 && sma20 < sma50) return 'bearish';
  return p > sma50 ? 'mildly bullish' : 'mildly bearish';
}

// ── Attach to a global namespace usable in window, worker, and Node ───────────
const _ta = {
  CRYPTO_BASES, FOREX_CCY, ETFS, FUTURES_RX, detectType,
  calcSMA, calcEMA, calcRSI, calcMACD, calcATR, calcBollingerBands, calcStochRSI,
  calcOBVTrend, findPivotSR, calcFibLevels, calcVolTrend, calcADX, calcBBWidthPct, getTrend,
};
(function (g) { g.APEX = g.APEX || {}; g.APEX.ta = _ta; })(
  typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this)
);
if (typeof module !== 'undefined' && module.exports) module.exports = _ta;
