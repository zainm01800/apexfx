'use strict';

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtPrice(n, type) {
  if (n == null || isNaN(n)) return '—';
  if (type === 'Forex') return Number(n).toFixed(5);
  if (n >= 10000) return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 100)   return Number(n).toFixed(2);
  return Number(n).toFixed(4);
}
function fmtPct(n) {
  if (n == null || isNaN(n)) return '—';
  return `${n >= 0 ? '+' : ''}${Number(n).toFixed(2)}%`;
}
function fmtMCap(n) {
  if (!n) return '—';
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9)  return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6)  return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toLocaleString()}`;
}
function fmtNum(n, dp = 2) {
  return (n == null || isNaN(n)) ? '—' : Number(n).toFixed(dp);
}
function fmtVol(n) {
  if (!n && n !== 0) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}
function fmtDate(ts) {
  return new Date(ts * 1000).toISOString().slice(0, 10);
}

// ── Asset type detection ──────────────────────────────────────────────────────

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

// ── Technical indicators ──────────────────────────────────────────────────────

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

function calcVolumeProfile(bars) {
  const lookback = bars.slice(-60);
  if (lookback.length < 10) return null;
  const buckets = 30;
  const hi = Math.max(...lookback.map(b => b.high));
  const lo = Math.min(...lookback.map(b => b.low));
  const range = hi - lo;
  if (range <= 0) return null;
  const size = range / buckets;
  const vol = new Array(buckets).fill(0);
  lookback.forEach(bar => {
    const barRange = bar.high - bar.low || 0.0001;
    for (let i = 0; i < buckets; i++) {
      const bLo = lo + i * size, bHi = bLo + size;
      const overlap = Math.max(0, Math.min(bar.high, bHi) - Math.max(bar.low, bLo));
      vol[i] += bar.volume * (overlap / barRange);
    }
  });
  const pocIdx = vol.indexOf(Math.max(...vol));
  const poc = lo + (pocIdx + 0.5) * size;
  const totalVol = vol.reduce((a, b) => a + b, 0);
  let cumVol = 0, vaLo = pocIdx, vaHi = pocIdx;
  const target = totalVol * 0.70;
  let lo2 = pocIdx, hi2 = pocIdx;
  cumVol = vol[pocIdx];
  while (cumVol < target && (lo2 > 0 || hi2 < buckets - 1)) {
    const nextLo = lo2 > 0 ? vol[lo2 - 1] : -Infinity;
    const nextHi = hi2 < buckets - 1 ? vol[hi2 + 1] : -Infinity;
    if (nextHi >= nextLo && hi2 < buckets - 1) { hi2++; cumVol += vol[hi2]; }
    else if (lo2 > 0) { lo2--; cumVol += vol[lo2]; }
    else break;
  }
  return {
    poc:  +poc.toFixed(4),
    vah:  +(lo + (hi2 + 1) * size).toFixed(4),
    val:  +(lo + lo2 * size).toFixed(4),
  };
}

function calcRelStrength(assetCloses, benchCloses) {
  if (!assetCloses?.length || !benchCloses?.length) return null;
  const n = Math.min(assetCloses.length, benchCloses.length);
  const ac = assetCloses.slice(-n), bc = benchCloses.slice(-n);
  const ret = (arr, days) => arr.length > days
    ? ((arr[arr.length-1] - arr[arr.length-1-days]) / arr[arr.length-1-days] * 100)
    : null;
  const rs1w  = ret(ac,5)  != null && ret(bc,5)  != null ? +(ret(ac,5)  - ret(bc,5) ).toFixed(2) : null;
  const rs1m  = ret(ac,21) != null && ret(bc,21) != null ? +(ret(ac,21) - ret(bc,21)).toFixed(2) : null;
  const rs3m  = ret(ac,63) != null && ret(bc,63) != null ? +(ret(ac,63) - ret(bc,63)).toFixed(2) : null;
  return { rs1w, rs1m, rs3m };
}

function getTrend(c, sma20, sma50) {
  if (!sma20 || !sma50) return 'sideways';
  const p = c[c.length - 1];
  if (p > sma20 && sma20 > sma50) return 'bullish';
  if (p < sma20 && sma20 < sma50) return 'bearish';
  return p > sma50 ? 'mildly bullish' : 'mildly bearish';
}

// ── Historical setup scanner ──────────────────────────────────────────────────
// Finds every past bar with similar RSI + trend + MACD state, computes forward returns
function runHistoricalScan(bars) {
  if (bars.length < 80) return null;
  const closes = bars.map(b => b.close);
  const curRSI   = calcRSI(closes);
  const curSMA20 = calcSMA(closes, 20);
  const curSMA50 = calcSMA(closes, 50);
  const curTrend = getTrend(closes, curSMA20, curSMA50);
  const curMACD  = calcMACD(closes);
  const curMacdPos = curMACD != null ? curMACD > 0 : null;
  if (!curRSI) return null;

  const instances = [];
  for (let i = 60; i < bars.length - 21; i++) {
    const hCloses = closes.slice(0, i + 1);
    const hRSI    = calcRSI(hCloses);
    const hSMA20  = calcSMA(hCloses, 20);
    const hSMA50  = calcSMA(hCloses, 50);
    const hTrend  = getTrend(hCloses, hSMA20, hSMA50);
    const hMACD   = calcMACD(hCloses);
    if (!hRSI) continue;
    const rsiMatch  = Math.abs(hRSI - curRSI) <= 12;
    const trendMatch = hTrend === curTrend;
    const macdMatch  = hMACD != null && curMacdPos != null ? (hMACD > 0) === curMacdPos : true;
    if (!rsiMatch || !trendMatch || !macdMatch) continue;
    const entry = closes[i];
    const f5  = closes[i + 5]  != null ? (closes[i + 5]  - entry) / entry * 100 : null;
    const f10 = closes[i + 10] != null ? (closes[i + 10] - entry) / entry * 100 : null;
    const f20 = closes[i + 20] != null ? (closes[i + 20] - entry) / entry * 100 : null;
    if (f5 != null && f20 != null) instances.push({ f5, f10: f10 ?? f5, f20 });
  }
  if (instances.length < 3) return null;
  const avg = arr => arr.reduce((s, x) => s + x, 0) / arr.length;
  const wr  = arr => (arr.filter(x => x > 0).length / arr.length * 100).toFixed(0);
  return {
    count:   instances.length,
    avg5d:   avg(instances.map(x => x.f5)).toFixed(1),
    avg10d:  avg(instances.map(x => x.f10)).toFixed(1),
    avg20d:  avg(instances.map(x => x.f20)).toFixed(1),
    win5d:   wr(instances.map(x => x.f5)),
    win20d:  wr(instances.map(x => x.f20)),
    best20d:  Math.max(...instances.map(x => x.f20)).toFixed(1),
    worst20d: Math.min(...instances.map(x => x.f20)).toFixed(1),
  };
}

// ── News → Price impact ───────────────────────────────────────────────────────
// For each news item, find the nearest candle and compute next-1d / next-5d move
function analyzeNewsImpact(news, bars) {
  if (!news.length || bars.length < 6) return [];
  return news.slice(0, 20).map(n => {
    try {
      const newsTs = new Date(n.date).getTime() / 1000;
      let closest = -1, minDiff = Infinity;
      bars.forEach((b, i) => { const d = Math.abs(b.time - newsTs); if (d < minDiff) { minDiff = d; closest = i; } });
      if (closest < 0 || minDiff > 4 * 86400 || closest + 5 >= bars.length) return null;
      const entry = bars[closest].close;
      const next1d = bars[closest + 1]?.close;
      const next5d = bars[closest + 5]?.close;
      return {
        title:    n.title.slice(0, 80),
        date:     n.date?.slice(0, 10) || '',
        impact1d: next1d != null ? +((next1d - entry) / entry * 100).toFixed(2) : null,
        impact5d: next5d != null ? +((next5d - entry) / entry * 100).toFixed(2) : null,
      };
    } catch { return null; }
  }).filter(Boolean);
}

// ── Fibonacci extensions (price targets beyond structure) ─────────────────────
function calcFibExtensions(bars) {
  const recent = bars.slice(-60);
  if (recent.length < 20) return null;
  const high   = Math.max(...recent.map(b => b.high));
  const low    = Math.min(...recent.map(b => b.low));
  const range  = high - low;
  if (range <= 0) return null;
  const highTs = recent.find(b => b.high === high)?.time || 0;
  const lowTs  = recent.find(b => b.low  === low)?.time  || 0;
  const dp = 4;
  if (lowTs < highTs) {
    // High came more recently → downside extension targets
    return { direction: 'downside', swing: low,
      e1272: +(low - range * 0.272).toFixed(dp),
      e1618: +(low - range * 0.618).toFixed(dp),
      e2000: +(low - range).toFixed(dp),
      e2618: +(low - range * 1.618).toFixed(dp),
    };
  } else {
    // Low came more recently → upside extension targets
    return { direction: 'upside', swing: high,
      e1272: +(high + range * 0.272).toFixed(dp),
      e1618: +(high + range * 0.618).toFixed(dp),
      e2000: +(high + range).toFixed(dp),
      e2618: +(high + range * 1.618).toFixed(dp),
    };
  }
}

// ── Confluence Score ──────────────────────────────────────────────────────────
// Weights signals across daily + weekly timeframes into a single directional score.
// bullPct = % of weighted signals pointing bullish (0–100)
// Returns { bullPct, bearPct, direction, strength, signalCount }
// Multi-timeframe confluence — DE-CORRELATED. The research is blunt that stacking
// correlated indicators manufactures false confidence ("RSI + Stochastic both
// oversold is one signal shown twice"; "Price > SMA20/50/200" is the SAME uptrend
// fact counted three times). So each signal is tagged with a correlation FAMILY,
// and within a family we keep the strongest signal at full weight and DECAY each
// additional same-family signal (0.55^rank) — turning a raw vote count into an
// effective-information score. A reading driven by one family is not real
// confluence, so we surface `independentSignals` + `concentrated` and damp the
// reported strength accordingly.
function calcConfluenceScore({ curr, sma20, sma50, sma200, wCurr, wSMA20, wSMA50, rsi, wRSI, macd, wMACD, volTrnd, adx, stochRsi }) {
  const signals = []; // { bull, weight, label, family }

  // ── Daily trend / moving-average structure (one underlying fact) ──
  if (curr != null && sma20 != null)  signals.push({ bull: curr > sma20,   weight: 2, label: 'Price vs SMA20',  family: 'dtrend' });
  if (curr != null && sma50 != null)  signals.push({ bull: curr > sma50,   weight: 2, label: 'Price vs SMA50',  family: 'dtrend' });
  if (curr != null && sma200 != null) signals.push({ bull: curr > sma200,  weight: 3, label: 'Price vs SMA200', family: 'dtrend' });
  if (sma20 != null && sma50 != null) signals.push({ bull: sma20 > sma50,  weight: 2, label: 'SMA20 vs SMA50',  family: 'dtrend' });
  if (sma50 != null && sma200 != null) signals.push({ bull: sma50 > sma200, weight: 2, label: 'SMA50 vs SMA200', family: 'dtrend' });
  if (adx != null && adx > 25)        signals.push({ bull: curr > (sma20 || curr), weight: 1, label: 'ADX trend strength', family: 'dtrend' });

  // ── Daily momentum oscillators (correlated with each other) ──
  if (rsi != null)      signals.push({ bull: rsi > 50,      weight: 2, label: 'RSI momentum', family: 'dmom' });
  if (macd != null)     signals.push({ bull: macd > 0,      weight: 2, label: 'MACD',         family: 'dmom' });
  if (stochRsi != null) signals.push({ bull: stochRsi > 50, weight: 1, label: 'StochRSI',     family: 'dmom' });

  // ── Weekly trend structure ──
  if (wCurr != null && wSMA20 != null) signals.push({ bull: wCurr > wSMA20, weight: 3, label: 'Weekly vs WMA20', family: 'wtrend' });
  if (wCurr != null && wSMA50 != null) signals.push({ bull: wCurr > wSMA50, weight: 2, label: 'Weekly vs WMA50', family: 'wtrend' });
  if (wMACD != null)                   signals.push({ bull: wMACD > 0,      weight: 3, label: 'Weekly MACD',     family: 'wtrend' });

  // ── Weekly momentum (separate read from weekly trend) ──
  if (wRSI != null) signals.push({ bull: wRSI > 50, weight: 2, label: 'Weekly RSI', family: 'wmom' });

  // ── Volume (genuinely independent of price) ──
  if (volTrnd === 'rising')    signals.push({ bull: true,  weight: 2, label: 'Volume rising',    family: 'vol' });
  if (volTrnd === 'declining') signals.push({ bull: false, weight: 2, label: 'Volume declining', family: 'vol' });

  if (!signals.length) return null;

  // De-correlate within each family: strongest signal full weight, each further
  // same-family signal decayed. bullPct is computed from these EFFECTIVE weights.
  const DECAY = 0.55;
  const byFamily = {};
  for (const s of signals) (byFamily[s.family] ||= []).push(s);
  let totalW = 0, bullW = 0;
  const famW = {};
  for (const fam in byFamily) {
    const fs = byFamily[fam].slice().sort((a, b) => b.weight - a.weight);
    let w = 0;
    fs.forEach((s, i) => { const eff = s.weight * Math.pow(DECAY, i); totalW += eff; w += eff; if (s.bull) bullW += eff; });
    famW[fam] = w;
  }
  const bullPct = Math.round(bullW / totalW * 100);
  const independentSignals = Object.keys(byFamily).length;          // distinct info sources
  const maxShare   = Math.max(...Object.values(famW)) / totalW;     // weight in the biggest family
  const concentrated = maxShare >= 0.5;                             // dominated by one correlated cluster

  let strength = bullPct >= 80 || bullPct <= 20 ? 'HIGH' : bullPct >= 65 || bullPct <= 35 ? 'MODERATE' : 'LOW';
  if (concentrated) strength = strength === 'HIGH' ? 'MODERATE' : strength === 'MODERATE' ? 'LOW' : strength;

  return {
    bullPct,
    bearPct:     100 - bullPct,
    direction:   bullPct >= 65 ? 'BULLISH' : bullPct <= 35 ? 'BEARISH' : 'MIXED',
    strength,
    signalCount: signals.length,
    independentSignals,
    concentrated,
  };
}

async function fetchFearGreed() {
  try {
    const res = await fetch('https://api.alternative.me/fng/?limit=1');
    if (!res.ok) return null;
    const d = await res.json();
    const item = d?.data?.[0];
    if (!item) return null;
    return { value: Number(item.value), label: item.value_classification };
  } catch { return null; }
}

// ── Supabase-backed analysis memory ──────────────────────────────────────────
// Fetches prior analyses for a symbol from Supabase (falls back to [] on error)
async function fetchTickerMemory(sym) {
  try {
    const r = await fetch(`/api/memory?sym=${encodeURIComponent(sym)}`);
    if (!r.ok) return [];
    const data = await r.json();
    return Array.isArray(data) ? data : [];
  } catch { return []; }
}

// Saves a completed analysis to Supabase (fire-and-forget — don't await in UI flow).
// If `updateId` is given, the SAME open setup is being re-scanned, so we refresh
// that existing history row in place instead of creating a duplicate.
function saveToMemory(sym, type, analysis, price, updateId = null, setupFeatures = null) {
  // Attach the verdict's direction + stated confidence to the structural feature
  // vector so future meta-label retrieval can also condition on side/conviction.
  const features = setupFeatures ? {
    ...setupFeatures,
    dir:  /BUY|LONG/i.test(analysis.verdict || '') ? 1 : /SELL|SHORT/i.test(analysis.verdict || '') ? -1 : 0,
    conf: analysis.confidence_score != null ? +(analysis.confidence_score / 100).toFixed(3) : null,
    // Mark bot-generated scans (auto-scan workflow) so the History scoreboard can
    // separate them from the user's own calls. setupDistance ignores this key, so it
    // never affects structural matching / meta-label / lessons retrieval.
    ...(_autoScan ? { auto: 1 } : {}),
    // Control-arm rows are tagged so the A/B (directive-fed vs directive-blind
    // resolved outcomes) can be evaluated later. Ignored by setupDistance.
    ...(_controlArm ? { control: 1 } : {}),
  } : null;
  const fields = {
    symbol:               sym,
    asset_type:           type,
    price:                +price.toFixed(4),
    verdict:              analysis.verdict,
    confidence:           analysis.confidence_score,
    target_price:         analysis.target_price          || null,
    entry_zone:           analysis.entry_zone            || null,
    stop_loss:            analysis.stop_loss             || null,
    risk_reward:          analysis.risk_reward           || null,
    summary:              (analysis.executive_summary    || '').slice(0, 500),
    // Richer fields for history comparison and AI learning
    technical_analysis:   (analysis.technical_analysis  || '').slice(0, 800),
    fundamental_analysis: (analysis.fundamental_analysis|| '').slice(0, 800),
    macro_environment:    (analysis.macro_environment   || '').slice(0, 800),
    risk_analysis:        (analysis.risk_analysis       || '').slice(0, 800),
    key_reasons:          analysis.key_reasons          || null,
    short_term_outlook:   (analysis.short_term_outlook  || '').slice(0, 300),
    timeframe:            analysis.timeframe             || null,
    setup_features:       features,
  };
  const req = updateId
    ? { method: 'PATCH', body: JSON.stringify({ id: updateId, refresh: true, ...fields }) }
    : { method: 'POST',  body: JSON.stringify(fields) };
  fetch('/api/memory', { headers: { 'Content-Type': 'application/json' }, ...req })
    .catch(() => {}); // silent fail — memory is best-effort
}

// ── Style-aware outcome resolution ────────────────────────────────────────────
// Each trade style is graded on a matching-granularity timeframe (so intrabar TP/SL
// is detected accurately) and an expiry scaled to its holding horizon. The style is
// read from the row's persisted setup_features; rows without it default to swing.
const STYLE_RES = {
  scalp:    { tf: '15m', expiryDays: 3,   bufferDays: 1 },
  intraday: { tf: '1h',  expiryDays: 7,   bufferDays: 2 },
  swing:    { tf: '1d',  expiryDays: 30,  bufferDays: 5 },
  position: { tf: '1d',  expiryDays: 120, bufferDays: 7 },
};
// Bar length per timeframe — used to require a FULL bar-period of clearance after the
// entry before a bar may grade TP/SL, so the entry-day bar (whose high/low may pre-date
// the entry) can't fabricate an impossible hit (look-ahead). See resolveOutcomes.
const TF_SECONDS = { '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800 };
// UTC calendar day ("2026-06-05") for the daily no-look-ahead gate.
function utcDay(ts) { return new Date(ts * 1000).toISOString().slice(0, 10); }
function resolutionFor(row) {
  let f = row && row.setup_features;
  if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
  const s = (f && f.style ? String(f.style) : 'swing').toLowerCase();
  return STYLE_RES[s] || STYLE_RES.swing;
}
// Precise entry timestamp (seconds): created_at → epoch in the id → analysis_date.
function entryTsOf(row) {
  if (row.created_at) { const t = Date.parse(row.created_at); if (!isNaN(t)) return t / 1000; }
  const m = String(row.id || '').match(/_(\d{10,})$/);
  if (m) return parseInt(m[1], 10) / 1000;
  return new Date(row.analysis_date).getTime() / 1000;
}

// Resolve outcomes of pending analyses using actual candle data.
// `candleTf` is the timeframe of the passed candles; TP/SL detection only runs for
// rows whose style resolves on that same timeframe — others are left for the History
// page resolver (which fetches the right-granularity candles). Expiry is style-scaled.
function resolveOutcomes(pendingRows, candles, candleTf) {
  pendingRows.forEach(row => {
    if (row.outcome !== 'pending' || !row.analysis_date) return;
    const res = resolutionFor(row);
    const entryTs = entryTsOf(row);
    // No-look-ahead. DAILY/WEEKLY: only grade bars on a strictly LATER calendar day
    // (excludes the entry-day bar's possibly-pre-entry extremes, but keeps the first
    // genuine next-day bar — a "+24h" gate wrongly dropped it for 00:00-stamped bars).
    // INTRADAY: one full bar-period of clearance past entry.
    const tfSec = TF_SECONDS[candleTf] || 86400;
    let barsAfter = (candleTf === '1d' || candleTf === '1w')
      ? candles.filter(b => utcDay(b.time) > utcDay(entryTs))
      : candles.filter(b => b.time >= entryTs + tfSec);

    // Sanitize opening-print spikes for Stocks/ETFs (Yahoo Finance data anomaly).
    // The literal first bar of a session can be a garbled opening-auction cross
    // whose distortion can corrupt the open/close, not just the wick — verified
    // live: NFLX 2026-06-26 13:30 UTC printed O:71.60 L:71.54 (a phantom "TP hit"
    // on a 72.20 target) while the rest of the session traded 73.2-75.2 and the real
    // exchange tape never went near 72. A wick-clip doesn't help when the body
    // itself is the bad print, so EXCLUDE the bar entirely (confirmed against the
    // live NFLX data to correctly recover sl_hit). Mirrors history.js's gradeRow —
    // keep both in sync if this logic changes.
    const _resType = row.asset_type || 'Stock';
    if (_resType === 'Stock' || _resType === 'ETF') {
      barsAfter = barsAfter.filter((c, i) => {
        const isFirstOfDay = (i === 0) || (new Date(c.time * 1000).getUTCDate() !== new Date(barsAfter[i - 1].time * 1000).getUTCDate());
        return !isFirstOfDay;
      });
    }

    const tp  = parseFloat(row.target_price);
    const sl  = parseFloat(row.stop_loss);
    const ageDays = (Date.now() / 1000 - entryTs) / 86400;

    let outcome = null, outcomePrice = null, outcomeTime = null;

    // Only grade TP/SL when the candles we have match this row's resolution TF.
    if (res.tf === candleTf && barsAfter.length && !isNaN(tp) && !isNaN(sl)) {
      const dir = verdictDir(row.verdict);   // long: TP above / SL below; short: reversed
      if (dir !== 'neutral') {
        // Entry-fill gate: a setup only becomes a real trade once price trades INTO
        // its entry zone. An entry at/around the scan price is a market fill; an entry
        // away from price (pullback/breakout) must be reached first — otherwise a TP
        // hit without ever filling is a phantom win that never existed.
        const eb = entryBounds(row.entry_zone);
        const scanPx = parseFloat(row.price);
        const atMarket = eb && !isNaN(scanPx) && scanPx >= eb.lo - Math.abs(eb.lo) * 0.0005 && scanPx <= eb.hi + Math.abs(eb.hi) * 0.0005;
        let filled = !eb || atMarket;
        for (const b of barsAfter) {
          if (!filled) {
            if (b.low <= eb.hi && b.high >= eb.lo) filled = true;   // traded into the entry zone
            else continue;
          }
          if (dir === 'short') {
            if (b.low  <= tp) { outcome = 'tp_hit'; outcomePrice = tp; outcomeTime = b.time * 1000; break; }
            if (b.high >= sl) { outcome = 'sl_hit'; outcomePrice = sl; outcomeTime = b.time * 1000; break; }
          } else {
            if (b.high >= tp) { outcome = 'tp_hit'; outcomePrice = tp; outcomeTime = b.time * 1000; break; }
            if (b.low  <= sl) { outcome = 'sl_hit'; outcomePrice = sl; outcomeTime = b.time * 1000; break; }
          }
        }
      }
    }
    if (!outcome && ageDays > res.expiryDays) outcome = 'expired';
    if (!outcome) return; // still genuinely pending

    const outcomeDate = outcomeTime ? new Date(outcomeTime).toISOString() : new Date().toISOString();

    // PATCH outcome back to Supabase (fire-and-forget)
    fetch('/api/memory', {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id:            row.id,
        outcome,
        outcome_price: outcomePrice,
        outcome_date:  outcomeDate,
      }),
    }).catch(() => {});

    // Update row locally so the UI reflects it immediately
    row.outcome = outcome;
    row.outcome_price = outcomePrice;
    row.outcome_date = outcomeDate;
  });
}

// ── API calls ─────────────────────────────────────────────────────────────────

// ── Trade style (timeframe) ───────────────────────────────────────────────────
// The selected style drives which candle timeframe the WHOLE analysis runs on, and
// tells the committee what horizon to plan entries / stops / take-profits for.
const TRADE_STYLES = {
  scalp:    { label: 'Scalp',    primaryTf: '15m', contextTf: '1h', entryTf: '5m',  entryDays: 10,  primaryDays: 30,   contextDays: 60,   horizon: 'minutes to a few hours' },
  intraday: { label: 'Intraday', primaryTf: '1h',  contextTf: '4h', entryTf: '15m', entryDays: 25,  primaryDays: 120,  contextDays: 360,  horizon: 'a few hours up to one trading day' },
  swing:    { label: 'Swing',    primaryTf: '1d',  contextTf: '1w', entryTf: '4h',  entryDays: 90,  primaryDays: 210,  contextDays: 730,  horizon: 'several days to a few weeks' },
  position: { label: 'Position', primaryTf: '1w',  contextTf: '1M', entryTf: '1d',  entryDays: 400, primaryDays: 1825, contextDays: 3650, horizon: 'several weeks to months' },
};
let _tradeStyle = 'swing';
function tradeStyle() { return TRADE_STYLES[_tradeStyle] || TRADE_STYLES.swing; }
// Honest disclosure: the free price feed lags intraday, so flag scalp/intraday styles.
function updateStyleLagNotice() {
  const el = document.getElementById('styleLagNotice');
  if (el) el.style.display = (_tradeStyle === 'scalp' || _tradeStyle === 'intraday') ? '' : 'none';
}

// ── Re-scan cooldown (paced by trade style) ───────────────────────────────────
// Stops the SAME symbol from being re-analysed instantly (burns rate-limited AI
// calls for no new information). A DIFFERENT symbol can always be run right away.
// Windows roughly match how long each style's setup takes to actually play out.
const COOLDOWN_MS = {
  scalp:    5  * 60 * 1000,
  intraday: 30 * 60 * 1000,
  swing:    4  * 60 * 60 * 1000,
  position: 24 * 60 * 60 * 1000,
};
const _cdKey = (sym) => `apex_lastscan_${String(sym).toUpperCase()}`;
function cooldownRemainingMs(sym) {
  const last = parseInt(localStorage.getItem(_cdKey(sym)) || '0', 10);
  if (!last) return 0;
  const window = COOLDOWN_MS[_tradeStyle] || COOLDOWN_MS.swing;
  return Math.max(0, last + window - Date.now());
}
function markScanned(sym) { try { localStorage.setItem(_cdKey(sym), String(Date.now())); } catch {} }
function fmtDuration(ms) {
  const s = Math.ceil(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.ceil(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}

// ── Trade-setup math: deterministic R:R + "same setup" detection ───────────────
// Professional minimum reward:risk by trade style (research-backed): scalpers run
// tighter R:R but compensate with high win rate; day/swing/position traders demand
// more because their win rates are lower and holds longer. 1.5:1 is the absolute
// floor anyone serious uses; 3:1+ is an "A+" setup. Sub-minimum setups are NOT trades
// a professional would take — the scan downgrades them to WAIT.
const MIN_RR_BY_STYLE = { scalp: 1.5, intraday: 2.0, swing: 2.0, position: 2.5 };
const MIN_RR = 1.5;   // fallback floor
function minRRForStyle(style) { return MIN_RR_BY_STYLE[style] || MIN_RR; }
// Parse a single representative entry price from an entry_zone (number or range).
function parseEntryPrice(entryZone) {
  if (entryZone == null) return NaN;
  const nums = String(entryZone).match(/-?\d+(?:\.\d+)?/g);
  if (!nums) return NaN;
  const vals = nums.map(Number).filter(n => !isNaN(n));
  if (!vals.length) return NaN;
  return vals.reduce((a, b) => a + b, 0) / vals.length;   // midpoint of a range
}
// Low/high bounds of an entry zone ("182 - 185" → {lo:182, hi:185}); null if unparseable.
function entryBounds(entryZone) {
  const nums = String(entryZone == null ? '' : entryZone).match(/-?\d+(?:\.\d+)?/g);
  if (!nums) return null;
  const v = nums.map(Number).filter(n => !isNaN(n));
  if (!v.length) return null;
  return { lo: Math.min(...v), hi: Math.max(...v) };
}
// Compute risk:reward deterministically from entry / stop / target.
// Returns { ratio, text, weak, aPlus, minRR } or null if it can't be computed.
function computeRR(entryZone, stop, target, minRR = MIN_RR) {
  const e = parseEntryPrice(entryZone), s = parseFloat(stop), t = parseFloat(target);
  if ([e, s, t].some(v => isNaN(v))) return null;
  const risk = Math.abs(e - s), reward = Math.abs(t - e);
  if (risk <= 0) return null;
  const ratio = reward / risk;
  return { ratio, text: `${ratio.toFixed(1)}:1`, weak: ratio < minRR, aPlus: ratio >= 3, minRR };
}
// Bucket a verdict into a trade direction for setup comparison.
function verdictDir(v) {
  const u = (v || '').toUpperCase();
  if (/BUY/.test(u)) return 'long';
  if (/SELL|SHORT/.test(u)) return 'short';
  return 'neutral';
}
// Are entry/stop/target each within ~2% of the prior scan's?
function levelsClose(prior, a) {
  const pairs = [
    [parseEntryPrice(prior.entry_zone), parseEntryPrice(a.entry_zone)],
    [parseFloat(prior.stop_loss),       parseFloat(a.stop_loss)],
    [parseFloat(prior.target_price),    parseFloat(a.target_price)],
  ];
  for (const [x, y] of pairs) {
    if (isNaN(x) || isNaN(y)) continue;            // not comparable → don't fail the match on it
    const denom = Math.abs(x) || 1;
    if (Math.abs(x - y) / denom > 0.02) return false;
  }
  return true;
}
// Did the prior scan happen on the same UTC calendar day as now?
function sameUTCDay(priorRow) {
  const today = new Date().toISOString().slice(0, 10);
  const pd = (priorRow.analysis_date || '').slice(0, 10);
  return pd && pd === today;
}
// Two scans are "the same evolving thesis" (→ refresh the open row, don't pile up a
// duplicate) when the DIRECTION matches and EITHER: they're from the same day (so all
// the rapid same-session re-scans collapse into one living card), OR the entry/stop/
// target are still materially identical across days. A direction flip, or a genuinely
// different setup on a later day, becomes a NEW history row — preserving the day-by-day
// evolution trail the History page renders.
function sameSetup(prior, a) {
  if (!prior) return false;
  if (verdictDir(prior.verdict) !== verdictDir(a.verdict)) return false;
  return sameUTCDay(prior) || levelsClose(prior, a);
}

// Force a re-scan that bypasses the cooldown (from the cooldown notice button).
function forceRescan(sym) {
  document.getElementById('symInput').value = sym;
  startResearch._force = String(sym).toUpperCase();
  startResearch();
}

// Cooldown notice (shown instead of running a too-soon re-scan). Counts down live.
let _cdTimer = null;
function showCooldownNotice(sym, ms) {
  hideAll();
  document.getElementById('analyseBtn').disabled = false;
  let el = document.getElementById('cooldownSection');
  if (!el) {
    el = document.createElement('section');
    el.id = 'cooldownSection';
    el.className = 'error-section';
    const anchor = document.getElementById('resultsSection');
    anchor.parentNode.insertBefore(el, anchor);
  }
  el.style.display = '';
  const safeSym = escHtmlSafe(String(sym).toUpperCase());
  const render = (remain) => {
    el.innerHTML = `
      <div class="error-card">
        <div class="error-icon">⏳</div>
        <p>You just analysed <b>${safeSym}</b>. Re-scans of the same symbol are paced by trade style
        (<b>${tradeStyle().label}</b>) so we don't waste rate-limited AI calls re-running an unchanged setup.</p>
        <p style="color:var(--text2)">Re-scan available in <b>${fmtDuration(remain)}</b>.</p>
        <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
          <button onclick="forceRescan('${safeSym}')">Re-scan anyway</button>
          <button class="btn-secondary" onclick="resetState()">Pick another symbol</button>
        </div>
      </div>`;
  };
  render(ms);
  if (_cdTimer) clearInterval(_cdTimer);
  const end = Date.now() + ms;
  _cdTimer = setInterval(() => {
    const remain = end - Date.now();
    if (remain <= 0) { clearInterval(_cdTimer); _cdTimer = null; resetState(); return; }
    render(remain);
  }, 1000);
}

function alignedTimes(tf, days) {
  const tfSec = {
    '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800, '1M': 2592000
  }[tf] || 86400;
  const to = Math.floor(Date.now() / 1000 / tfSec) * tfSec;
  const from = to - days * 86400;
  return { from, to };
}

async function fetchCandles(sym, type, tf = '1d', days = 210) {
  const { from, to } = alignedTimes(tf, days);
  const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=${tf}&from=${from}&to=${to}`);
  if (!r.ok) throw new Error(`Price data unavailable (HTTP ${r.status})`);
  const d = await r.json();
  if (d.error) throw new Error(d.error);
  return d;
}
async function fetchWeeklyCandles(sym, type, tf = '1w', days = 730) {
  try {
    const { from, to } = alignedTimes(tf, days);
    const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=${tf}&from=${from}&to=${to}`);
    if (!r.ok) return null;
    const d = await r.json();
    return Array.isArray(d) && d.length > 4 ? d : null;
  } catch { return null; }
}
async function fetchNews(sym, type) {
  try {
    const r = await fetch(`/api/news?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}`);
    if (!r.ok) return [];
    const d = await r.json();
    return Array.isArray(d) ? d : [];
  } catch { return []; }
}
async function fetchQuote(sym, type) {
  try {
    const r = await fetch(`/api/quote?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}`);
    if (!r.ok) return null;
    return r.json();
  } catch { return null; }
}
async function fetchMacroContext(sym) {
  // Skip when the symbol is one of the macro instruments itself
  if (['SPY', 'QQQ', 'TLT'].includes(sym.toUpperCase())) return null;
  try {
    const { from, to } = alignedTimes('1d', 40);
    const [spyBars, qqqBars, tltBars] = await Promise.all([
      fetch(`/api/candles?sym=SPY&type=ETF&tf=1d&from=${from}&to=${to}`).then(r => r.ok ? r.json() : []).catch(() => []),
      fetch(`/api/candles?sym=QQQ&type=ETF&tf=1d&from=${from}&to=${to}`).then(r => r.ok ? r.json() : []).catch(() => []),
      fetch(`/api/candles?sym=TLT&type=ETF&tf=1d&from=${from}&to=${to}`).then(r => r.ok ? r.json() : []).catch(() => []),
    ]);
    const summarize = (bars, name) => {
      if (!Array.isArray(bars) || bars.length < 5) return `${name}: unavailable`;
      const c   = bars.map(b => b.close);
      const cur = c[c.length - 1];
      const chg = ((cur - (c[c.length - 6] || c[0])) / (c[c.length - 6] || c[0]) * 100).toFixed(2);
      const rsi = calcRSI(c) ?? 'N/A';
      const s20 = calcSMA(c, 20);
      const pos = s20 ? (cur > s20 ? 'above SMA20' : 'below SMA20') : '';
      return `${name}: ${cur.toFixed(2)} | 5d: ${chg > 0 ? '+' : ''}${chg}% | RSI: ${rsi} | ${pos}`;
    };
    return [
      summarize(spyBars, 'SPY (S&P 500)'),
      summarize(qqqBars, 'QQQ (Nasdaq 100)'),
      summarize(tltBars, 'TLT (20Y Bonds)'),
    ].join('\n');
  } catch { return null; }
}

// Fetches quantified macro intermarket data (yield curve, HY OAS, VIX, DXY)
async function fetchMacroIntermarket() {
  try {
    const r = await fetch('/api/macro-intermarket');
    if (!r.ok) return null;
    return r.json();
  } catch { return null; }
}

// Fetches Piotroski F-Score, Beneish M-Score, Accrual Ratio, Altman Z-Score
// Only called for Stock type; requires FMP_API_KEY on the server side.
async function fetchQualityScores(sym) {
  try {
    const r = await fetch(`/api/quality-scores?sym=${encodeURIComponent(sym)}`);
    if (!r.ok) return null;
    const d = await r.json();
    return d?.available ? d : null;
  } catch { return null; }
}
// Backtest knowledge base (Supabase) — what has actually survived testing for this symbol.
async function fetchBacktestKB(sym) {
  try {
    const r = await fetch(`/api/backtests?sym=${encodeURIComponent(sym)}`);
    if (!r.ok) return null;
    const d = await r.json();
    return d?.summary ? d.summary : null;
  } catch { return null; }
}

// Summarise the in-browser Backtest Lab results for this symbol (apex_strategy_backtests
// via /api/backtest-runs). Uses the most recent BROAD run (≥6 qualifying, ≥30-trade
// results) so an ad-hoc user run can't skew the committee; null if none broad enough.
// Inverse standard-normal CDF (Acklam's rational approximation; ~1e-9 accuracy).
function _invNormCDF(p) {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  const a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00];
  const b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01];
  const c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00];
  const d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00];
  const pl = 0.02425, ph = 1 - pl;
  let q, r;
  if (p < pl) { q = Math.sqrt(-2 * Math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
  if (p <= ph) { q = p - 0.5; r = q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1); }
  q = Math.sqrt(-2 * Math.log(1 - p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1);
}

// Expected MAXIMUM Sharpe across N skill-less trials (Bailey & Lopez de Prado,
// False Strategy Theorem / EVT form). sigmaSR = dispersion of the trials' Sharpes.
// A "best" Sharpe below this noise floor is what pure trial-and-error produces.
function _expectedMaxSharpe(N, sigmaSR) {
  if (!(N > 1) || !(sigmaSR > 0)) return 0;
  const g = 0.5772156649;                       // Euler-Mascheroni
  return sigmaSR * ((1 - g) * _invNormCDF(1 - 1 / N) + g * _invNormCDF(1 - 1 / (N * Math.E)));
}

async function fetchStrategyBacktests(sym) {
  try {
    const r = await fetch(`/api/backtest-runs?instrument=${encodeURIComponent(sym)}&limit=300`);
    if (!r.ok) return null;
    const rows = await r.json();
    if (!Array.isArray(rows) || !rows.length) return null;
    // ROBUSTNESS: do NOT just grab the newest run — a user's ad-hoc 1–2 combo backtest
    // would then become the committee's reference and skew it. Group recent runs
    // (API returns newest first) and use the most recent run that is BROAD enough to be
    // representative (≥ MIN_COVERAGE qualifying results). Thin exploratory runs are
    // ignored; if none are broad enough we return null (no backtest context is safer
    // than misleading context). The deflated-Sharpe + OOS checks below handle the rest.
    const MIN_COVERAGE = 6;
    const runOrder = [], byRun = {};
    for (const x of rows) { (byRun[x.run_id] ||= []).push(x); if (byRun[x.run_id].length === 1) runOrder.push(x.run_id); }
    let cur = null;
    for (const rid of runOrder) {
      const q = byRun[rid].filter(x => x.n_trades >= 30 && x.sharpe != null);
      if (q.length >= MIN_COVERAGE) { cur = q; break; }
    }
    if (!cur) return null;
    // Headline edge prefers RELIABLE (≥100-trade) results when the run has them.
    const reliable = cur.filter(x => x.n_trades >= 100);
    const best = (reliable.length ? reliable : cur).slice().sort((a, b) => (b.sharpe ?? -9) - (a.sharpe ?? -9))[0];

    // Multiple-testing correction (C1): the "best" Sharpe was selected across
    // N strategy trials, so it is inflated. Compare it to the expected best-by-
    // chance Sharpe and deflate. If it does not clear the noise floor, there is
    // no demonstrated edge no matter how good the headline Sharpe looks.
    const sharpes = cur.map(x => x.sharpe).filter(v => v != null);
    const meanSR  = sharpes.reduce((s, v) => s + v, 0) / sharpes.length;
    const sigmaSR = sharpes.length > 1
      ? Math.sqrt(sharpes.reduce((s, v) => s + (v - meanSR) ** 2, 0) / (sharpes.length - 1)) : 0;
    const expMax   = _expectedMaxSharpe(cur.length, sigmaSR);
    const deflated = +(best.sharpe - expMax).toFixed(2);

    // Walk-forward / out-of-sample survival (the honest "did it keep working on
    // unseen recent data?" read). Only counts strategies with a usable OOS sample.
    const oosEligible = cur.filter(x => x.oos_n_trades != null && x.oos_n_trades >= 10);
    const oosHeld     = oosEligible.filter(x => x.oos_holds === true);
    const hasOOS      = oosEligible.length > 0;

    return {
      n: cur.length,
      n_pos: cur.filter(x => x.total_return > 0).length,
      best,
      conf: cur.find(x => x.strategy === 'confluence') || null,
      tfs: [...new Set(cur.map(x => x.timeframe))],
      dataTo: cur[0].data_to,
      n_trials: cur.length,
      exp_max_sharpe: +expMax.toFixed(2),
      deflated_best: deflated,
      edge_survives: deflated > 0,
      has_oos: hasOOS,
      n_oos_eligible: oosEligible.length,
      n_oos_held: oosHeld.length,
      best_oos_holds: best.oos_holds === true,
      best_oos_return: best.oos_return ?? null,
    };
  } catch { return null; }
}

// ── Quant Engine cross-check ──────────────────────────────────────────────────
// Independent regime + risk-sizing + CPCV/DSR/PBO validation from the local Python
// engine. Fully graceful: if the engine isn't reachable (e.g. on the live site,
// where it isn't hosted), this is skipped and the rest of Analyse is unaffected.
// API base: ?engine= query param > localStorage('apexEngineApi') > host-based default
// (local engine during local dev; the hosted engine on the deployed site).
const _engQp = new URLSearchParams(location.search).get('engine');
const _engDefault = /^(localhost|127\.0\.0\.1)$/.test(location.hostname)
  ? 'http://127.0.0.1:8000'
  : '/api/quant';   // same-origin proxy → Render (avoids the browser-side 503/CORS on the free tier)
const ENGINE_API = (_engQp || localStorage.getItem('apexEngineApi') || _engDefault).replace(/\/$/, '');
if (_engQp) localStorage.setItem('apexEngineApi', _engQp);

// When pointed at the same-origin proxy, the engine sub-path goes in ?p= (so
// /api/quant stays a single flat route); locally we hit the engine directly.
const _engUseProxy = ENGINE_API === '/api/quant';
const engUrl = (path) => (_engUseProxy ? `/api/quant?p=${encodeURIComponent(path)}` : `${ENGINE_API}${path}`);

function _engFmt(p, d = 5) {
  const n = parseFloat(p);
  return (p == null || isNaN(n)) ? '—' : (Math.abs(n) >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toFixed(d));
}

// Fetch the engine's regime + risk + validation once (graceful, cold-start aware).
// Returns { online, supported, regime, risk, validation }. Reused by BOTH the AI
// committee prompt (so the verdict accounts for it) and the engine card render.
async function fetchEngineData(sym) {
  const isRemote = !/^https?:\/\/(localhost|127\.0\.0\.1)/i.test(ENGINE_API);
  const probeMs = isRemote ? 75000 : 4000;
  let online = false;
  try {
    const h = await fetch(engUrl('/health'), { signal: AbortSignal.timeout(probeMs) });
    online = h.ok;
  } catch { online = false; }
  if (!online) return { online: false, supported: false, regime: null, risk: null, validation: null };

  const enc = encodeURIComponent(sym);
  const [regime, risk, validation] = await Promise.all([
    fetch(engUrl(`/regime/${enc}`)).then(r => r.ok ? r.json() : null).catch(() => null),
    fetch(engUrl(`/risk/${enc}?equity=100000`)).then(r => r.ok ? r.json() : null).catch(() => null),
    fetch(engUrl(`/validation/regime_gated_momentum?instrument=${enc}`)).then(r => r.ok ? r.json() : null).catch(() => null),
  ]);
  return { online: true, supported: !!(regime || risk), regime, risk, validation };
}

// Thin wrapper kept for any direct callers: fetch + render the card.
async function loadEngineInsights(sym, type) {
  const card = document.getElementById('engineCard');
  const body = document.getElementById('engineInsights');
  if (card) card.style.display = '';
  if (body) body.innerHTML = '<div class="eng-loading">Querying the quant engine (regime · risk · validation)…</div>';
  renderEngineCard(sym, await fetchEngineData(sym));
}

function renderEngineCard(sym, data) {
  const card = document.getElementById('engineCard');
  const body = document.getElementById('engineInsights');
  const chip = document.getElementById('engineStatusChip');
  if (!card || !body) return;
  card.style.display = '';

  if (!data || !data.online) {
    chip.textContent = 'OFFLINE'; chip.className = 'engine-status-chip off';
    body.innerHTML = `<div class="eng-offline">The quant engine isn't reachable, so it wasn't factored into the verdict above — <strong>the AI analysis is still complete.</strong>
      <span class="eng-dim">To enable it, run the engine locally (<code>uvicorn apex_quant.api.app:app --port 8000</code>) or host it. Trying: ${escHtmlSafe(ENGINE_API)}.</span></div>`;
    return;
  }
  chip.textContent = 'ONLINE'; chip.className = 'engine-status-chip on';

  const { regime, risk, validation } = data;
  if (!data.supported) {
    body.innerHTML = `<div class="eng-offline">The engine is online but couldn't analyse <b>${escHtmlSafe(sym)}</b> (it currently covers FX majors and equities). The AI analysis above is unaffected.</div>`;
    return;
  }

  // ── Regime ──
  let regimeHtml = '';
  if (regime) {
    regimeHtml = `<div class="eng-stat"><span class="eng-k">Market regime</span>
      <span class="eng-v">${escHtmlSafe((regime.name || '').toUpperCase())} · ${(regime.confidence * 100).toFixed(0)}% conf</span></div>`;
  }

  // ── Risk layer (the disciplined verdict) ──
  let riskHtml = '';
  if (risk) {
    if (risk.permitted) {
      const chips = (risk.constraints_applied || []).map(c => `<span class="eng-con">${escHtmlSafe(c)}</span>`).join('');
      riskHtml = `<div class="eng-stat"><span class="eng-k">Risk layer</span>
          <span class="eng-v pos">${escHtmlSafe((risk.direction || '').toUpperCase())} · ${(risk.risk_fraction * 100).toFixed(2)}% of equity · notional $${Number(risk.notional || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></div>
        <div class="eng-stat"><span class="eng-k">Entry / Stop / Target</span>
          <span class="eng-v">${_engFmt(risk.price)} / ${_engFmt(risk.stop_price)} / ${_engFmt(risk.target_price)}</span></div>
        ${chips ? `<div class="eng-cons">${chips}</div>` : ''}`;
    } else {
      const chips = (risk.constraints_applied || []).map(c => `<span class="eng-con">${escHtmlSafe(c)}</span>`).join('');
      riskHtml = `<div class="eng-stat"><span class="eng-k">Risk layer</span>
          <span class="eng-v neg">NO POSITION</span></div>
        <div class="eng-veto">${escHtmlSafe(risk.rationale || 'Vetoed by the risk layer.')}</div>
        ${chips ? `<div class="eng-cons">${chips}</div>` : ''}`;
    }
  }

  // ── Validation verdict ──
  let valHtml = '';
  if (validation && validation.verdict) {
    const pass = validation.verdict.passed;
    const dsr = validation.dsr || {}, pbo = validation.pbo || {}, cpcv = validation.cpcv || {};
    valHtml = `<div class="eng-stat"><span class="eng-k">Systematic validation</span>
        <span class="eng-v ${pass ? 'pos' : 'neg'}">${pass ? 'PASSED' : 'REJECTED'}</span></div>
      <div class="eng-valdetail">Momentum strategy on ${escHtmlSafe(sym)}: DSR ${dsr.dsr != null ? dsr.dsr.toFixed(2) : '—'} (need &gt;0.95) · PBO ${pbo.pbo != null ? pbo.pbo : '—'} (need &lt;0.5) · ${cpcv.frac_positive != null ? Math.round(cpcv.frac_positive * 100) + '%' : '—'} of ${cpcv.n_paths || '—'} OOS paths positive</div>`;
  } else {
    valHtml = `<div class="eng-stat"><span class="eng-k">Systematic validation</span>
        <span class="eng-v neu">not yet run for ${escHtmlSafe(sym)}</span></div>
      <div class="eng-valdetail eng-dim">Generate with <code>scripts/run_validation.py ${escHtmlSafe(sym)}</code></div>`;
  }

  body.innerHTML = `<div class="eng-grid">${regimeHtml}${riskHtml}${valHtml}</div>`;
}

function escHtmlSafe(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// ── TradingView deep-link ─────────────────────────────────────────────────────
// Maps a symbol + asset type to a fully-qualified TradingView ticker so the chart
// opens on the right exchange. Stocks default to NASDAQ unless known to be NYSE.
const TV_NYSE = new Set([
  'JPM','BAC','WFC','GS','MS','C','AXP','V','MA','BRK.B','XOM','CVX','COP','SLB',
  'JNJ','PG','KO','PEP','DIS','WMT','HD','LOW','NKE','MCD','UNH','PFE','MRK','LLY','ABBV','TMO','ABT',
  'BA','CAT','GE','HON','UPS','MMM','IBM','ORCL','CRM','ACN','T','VZ','NOW',
  // NYSE-Arca ETFs
  'SPY','IWM','GLD','SLV','USO','HYG','LQD','XLF','XLE','XLK','XLV','XLI','XLC','XLY','XLP','GDX','GDXJ','DIA','VTI','VOO','EEM','EFA','VNQ',
]);
const TV_NASDAQ_ETF = new Set(['QQQ','SMH','SOXX','ARKK','XBI','IBB','TLT']);

function tvSymbol(sym, type) {
  const s = String(sym).toUpperCase().trim();
  if (type === 'Forex')  return 'FOREXCOM:' + s.replace(/[^A-Z]/g, '');
  if (type === 'Crypto') {
    const base = s.replace(/[/\-](USDT?|USD)$/, '').replace(/USDT?$/, '').replace(/[/\-]/g, '');
    const ex = (base === 'BTC' || base === 'ETH') ? 'BITSTAMP:' : 'BINANCE:';
    return ex + base + 'USD';
  }
  if (type === 'Futures') return s.replace('1!', '');
  if (TV_NASDAQ_ETF.has(s)) return 'NASDAQ:' + s;
  if (TV_NYSE.has(s))       return 'NYSE:' + s;
  return 'NASDAQ:' + s;   // sensible default for most listed equities
}
function tvUrl(sym, type) {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol(sym, type))}`;
}

// ── Last rendered result (for the Share / Watchlist buttons) ──────────────────
let _lastResult = null;

function buildShareText(r) {
  const a = r.analysis || {};
  const verdict = (a.verdict || 'HOLD').replace(/_/g, ' ');
  const conf = a.confidence_score != null ? ` (${a.confidence_score}% confidence)` : '';
  const lines = [`$${r.sym} — ${verdict}${conf}`];
  const levels = [];
  if (a.entry_zone)   levels.push(`Entry ${a.entry_zone}`);
  if (a.target_price) levels.push(`Target ${a.target_price}`);
  if (a.stop_loss)    levels.push(`Stop ${a.stop_loss}`);
  if (a.risk_reward)  levels.push(`R:R ${a.risk_reward}`);
  if (levels.length) lines.push(levels.join('  ·  '));
  if (a.executive_summary) lines.push(a.executive_summary.trim().slice(0, 200));
  lines.push('via APEX AI Research · apexfx.vercel.app');
  return lines.join('\n');
}
function twitterIntent(r) {
  return `https://twitter.com/intent/tweet?text=${encodeURIComponent(buildShareText(r))}`;
}
async function shareAnalysis(btn) {
  if (!_lastResult) return;
  const text = buildShareText(_lastResult);
  const original = btn.innerHTML;
  try {
    await navigator.clipboard.writeText(text);
    btn.innerHTML = '✅ Copied!';
  } catch {
    btn.innerHTML = '⚠ Copy failed';
  }
  setTimeout(() => { btn.innerHTML = original; }, 1900);
}
function addToWatchlist(btn) {
  if (!_lastResult) return;
  const r = _lastResult, a = r.analysis || {};
  const item = {
    sym: r.sym, type: r.type,
    verdict: a.verdict || null,
    entry_zone: a.entry_zone || null,
    stop_loss: a.stop_loss || null,
    target_price: a.target_price || null,
    confidence: a.confidence_score ?? null,
    addedAt: Date.now(),
    currentPrice: r.price ?? null,
  };
  try {
    const list = JSON.parse(localStorage.getItem('apex_watchlist') || '[]');
    const i = list.findIndex(x => x.sym === item.sym && x.type === item.type);
    if (i >= 0) list[i] = item; else list.push(item);
    localStorage.setItem('apex_watchlist', JSON.stringify(list));
    const original = btn.innerHTML;
    btn.innerHTML = '✅ On Watchlist';
    setTimeout(() => { btn.innerHTML = original; }, 1900);
  } catch {}
}
function renderResultsActions(r) {
  const el = document.getElementById('resultsActions');
  if (!el) return;
  el.innerHTML = `
    <a class="ra-btn ra-tv" href="${tvUrl(r.sym, r.type)}" target="_blank" rel="noopener noreferrer">📊 View chart on TradingView →</a>
    <button class="ra-btn ra-share" onclick="shareAnalysis(this)">🔗 Share</button>
    <a class="ra-btn ra-x" href="${twitterIntent(r)}" target="_blank" rel="noopener noreferrer">𝕏 Post</a>
    <button class="ra-btn ra-watch" onclick="addToWatchlist(this)">⭐ Add to Watchlist</button>`;
}

// ── Pre-analysis flags (events + sector RS + TradingView), shown on selection ──
let _preData = { sym: null, events: null, sector: null };

async function loadPreAnalysis(sym, type) {
  const box = document.getElementById('preAnalysis');
  if (!box) return;
  const S = String(sym).toUpperCase();
  const tv = `<a class="pa-tv" href="${tvUrl(S, type)}" target="_blank" rel="noopener noreferrer">📊 View on TradingView →</a>`;
  box.style.display = '';
  box.innerHTML = `<div class="pa-row">${tv}</div>`;   // show TradingView immediately, flags fill in

  const [events, sector] = await Promise.all([
    fetch(`/api/events?sym=${encodeURIComponent(S)}&type=${encodeURIComponent(type)}`).then(r => r.ok ? r.json() : null).catch(() => null),
    fetch(`/api/sector?sym=${encodeURIComponent(S)}&type=${encodeURIComponent(type)}`).then(r => r.ok ? r.json() : null).catch(() => null),
  ]);
  // Ignore a stale response if the user has since picked a different symbol
  if (document.getElementById('symInput').value.trim().toUpperCase() !== S) return;
  _preData = { sym: S, events, sector };
  renderPreAnalysis(S, type, events, sector, tv);
}

function renderPreAnalysis(sym, type, events, sector, tv) {
  const box = document.getElementById('preAnalysis');
  if (!box) return;
  let banners = '';
  const earn = events?.earnings;
  if (earn && earn.daysAway != null && earn.daysAway <= 7) {
    banners += `<div class="pa-banner danger">⚠️ EARNINGS IN ${earn.daysAway} DAY${earn.daysAway === 1 ? '' : 'S'} — expect elevated volatility. Analysis risk is higher.</div>`;
  }
  const macro = Array.isArray(events?.macro) ? events.macro : [];
  const nearMacro = macro.filter(m => m.daysAway != null && m.daysAway <= 3).sort((a, b) => a.daysAway - b.daysAway)[0];
  if (nearMacro) {
    banners += `<div class="pa-banner warn">📅 ${escHtmlSafe(nearMacro.type)} in ${nearMacro.daysAway} day${nearMacro.daysAway === 1 ? '' : 's'} — macro volatility risk elevated.</div>`;
  }
  // Market session / hours — warn loudly when the market is closed or data is stale.
  const ms = marketSession(type);
  if (ms.closed) {
    banners += `<div class="pa-banner danger">🔒 ${escHtmlSafe(ms.session)} — the market is CLOSED, so the latest price is a stale close and intraday signals aren't reliable. Treat any setup as a PLAN for the next open (beware a gap), not a live entry.</div>`;
  } else if (ms.stale) {
    banners += `<div class="pa-banner warn">🌙 ${escHtmlSafe(ms.session)} — outside regular hours; prices are thin/unreliable. Treat extended-hours levels with caution.</div>`;
  }
  const sessPill = `<span class="pa-pill ${ms.closed ? 'neg' : (ms.liquidity === 'peak' || ms.liquidity === 'high') ? 'pos' : ''}" title="${escHtmlSafe(ms.guidance)}">🕐 ${escHtmlSafe(ms.session)} · ${escHtmlSafe(ms.liquidity)} liquidity</span>`;

  let pill = '';
  if (sector && sector.stock_return_30d != null && sector.sector_return_30d != null) {
    pill = `<span class="pa-pill ${sector.outperforming ? 'pos' : 'neg'}">${sector.outperforming ? 'Outperforming' : 'Underperforming'} ${escHtmlSafe(sector.sector)} by ${escHtmlSafe(sector.vs_sector)} (30d)</span>`;
  }
  box.innerHTML = `${banners}<div class="pa-row">${tv}${sessPill}${pill}</div>`;
}

// ── Nav win-rate badge (overall realised hit-rate) ────────────────────────────
async function loadNavWinRate() {
  const el = document.getElementById('navWinRate');
  if (!el) return;
  try {
    const r = await fetch('/api/memory?all=true&resolved=true&lean=true&limit=1000');
    if (!r.ok) return;
    const rows = await r.json();
    if (!Array.isArray(rows)) return;
    const tp = rows.filter(x => x.outcome === 'tp_hit').length;
    const sl = rows.filter(x => x.outcome === 'sl_hit').length;
    const resolved = tp + sl;
    if (!resolved) return;
    const wr = Math.round(tp / resolved * 100);
    el.textContent = `Win rate: ${wr}%`;
    el.classList.add(wr >= 50 ? 'good' : 'bad');
    el.title = `${tp}W / ${sl}L across ${resolved} resolved calls`;
    el.style.display = '';
  } catch {}
}

// ── COT speculative positioning (CFTC) ────────────────────────────────────────
async function fetchPositioning(sym, type) {
  try {
    const r = await fetch(`/api/positioning?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}`);
    if (!r.ok) return null;
    const d = await r.json();
    return (d && d.net != null) ? d : null;
  } catch { return null; }
}

// ── Crypto-native positioning + structure (funding, OI, L/S, dominance) ────────
// Real-time perp derivatives + market structure — the data a crypto desk lives on
// and which the generic technicals miss (crowding / squeeze risk, dry powder).
async function fetchCryptoDerivs(sym, type) {
  if (type !== 'Crypto') return null;
  try {
    const r = await fetch(`/api/crypto-derivs?sym=${encodeURIComponent(sym)}`);
    if (!r.ok) return null;
    const d = await r.json();
    return (d && d.signal) ? d : null;
  } catch { return null; }
}

// ── Seasonality — current calendar month's historical bias ────────────────────
// Pulls ~5y of daily candles and computes the average month-over-month return and
// hit-rate for the CURRENT calendar month across prior years. Cheap, client-side.
async function fetchSeasonality(sym, type) {
  try {
    const bars = await fetchCandles(sym, type, '1d', 1825);
    if (!Array.isArray(bars) || bars.length < 260) return null;
    const byMonthYear = {};
    for (const b of bars) {
      const d = new Date(b.time * 1000);
      (byMonthYear[`${d.getUTCFullYear()}-${d.getUTCMonth()}`] ||= []).push(b.close);
    }
    const monthReturns = {};
    for (const [key, closes] of Object.entries(byMonthYear)) {
      if (closes.length < 2) continue;
      const m = parseInt(key.split('-')[1], 10);
      (monthReturns[m] ||= []).push((closes[closes.length - 1] - closes[0]) / closes[0] * 100);
    }
    const curMonth = new Date().getUTCMonth();
    const arr = monthReturns[curMonth];
    if (!arr || arr.length < 3) return null;
    const avg = arr.reduce((s, x) => s + x, 0) / arr.length;
    const monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    return {
      month: monthNames[curMonth],
      years: arr.length,
      avgReturn: +avg.toFixed(2),
      winRate: Math.round(arr.filter(x => x > 0).length / arr.length * 100),
      best: +Math.max(...arr).toFixed(1),
      worst: +Math.min(...arr).toFixed(1),
    };
  } catch { return null; }
}

// Helper to identify weekend scans
function isWeekendRow(row) {
  let t = 0;
  if (row.created_at) { const parsed = Date.parse(row.created_at); if (!isNaN(parsed)) t = parsed; }
  if (!t) {
    const m = String(row.id || '').match(/_(\d{10,})$/);
    if (m) t = parseInt(m[1], 10);
  }
  if (!t && row.analysis_date) { const parsed = Date.parse(row.analysis_date); if (!isNaN(parsed)) t = parsed; }
  if (!t) return false;
  const d = new Date(t);
  const day = d.getUTCDay();
  return day === 0 || day === 6;
}

function isWeekendNow() {
  const d = new Date();
  const day = d.getUTCDay();
  return day === 0 || day === 6;
}

// ── Calibration feedback — realised accuracy by stated-confidence bucket ───────
// The model's own historical hit-rate, so the committee can self-correct for over/
// under-confidence. Needs a minimum of resolved calls to be meaningful.
async function fetchCalibration(currentType) {
  try {
    // resolved=true → the FULL graded history (old resolved swing/position rows
    // must never fall off a recent-rows window as scan volume grows).
    const r = await fetch('/api/memory?all=true&resolved=true&lean=true&limit=1000');
    if (!r.ok) return null;
    const rows = await r.json();
    if (!Array.isArray(rows)) return null;

    let resolved = rows.filter(x => x.outcome === 'tp_hit' || x.outcome === 'sl_hit');
    
    // Separate weekend crypto data from weekday crypto data (due to significant volume differences)
    if (currentType === 'Crypto') {
      const wantWeekend = isWeekendNow();
      resolved = resolved.filter(x => {
        if (x.asset_type !== 'Crypto') return true;
        return isWeekendRow(x) === wantWeekend;
      });
    }

    if (resolved.length < 8) return null;
    const buckets = [
      { label: '50–59%', lo: 0,  hi: 59 },
      { label: '60–69%', lo: 60, hi: 69 },
      { label: '70–79%', lo: 70, hi: 79 },
      { label: '80–89%', lo: 80, hi: 89 },
      { label: '90%+',   lo: 90, hi: 100 },
    ];
    const lines = [];
    for (const b of buckets) {
      const set = resolved.filter(x => { const c = Number(x.confidence) || 0; return c >= b.lo && c <= b.hi; });
      if (set.length < 3) continue;
      const acc = Math.round(set.filter(x => x.outcome === 'tp_hit').length / set.length * 100);
      lines.push({ band: b.label, acc, n: set.length });
    }
    if (!lines.length) return null;
    const tp = resolved.filter(x => x.outcome === 'tp_hit').length;
    return { lines, overallAcc: Math.round(tp / resolved.length * 100), n: resolved.length };
  } catch { return null; }
}

// ── Hard post-hoc confidence calibration (A1/A2) ──────────────────────────────
// The committee is ALSO told its realised accuracy (the soft prompt loop above),
// but LLM/model confidence stays systematically OVER-confident — a true ~55% win
// rate gets stated as 70%+. So we additionally re-map the DISPLAYED number through
// the realised hit-rate curve. Each band's raw->actual correction is shrunk toward
// the raw value by sample size (Bayesian-style: a thin band barely moves the number,
// a deep band moves it fully). The shrinkage also resists the feedback-loop / self-
// learning overfitting that an unguarded "learn from your own outcomes" loop hits.
function calibrateConfidence(rawConf, calibration) {
  if (!calibration || !Array.isArray(calibration.lines) || !calibration.lines.length) return null;
  const want = rawConf < 60 ? '50–59%'
             : rawConf < 70 ? '60–69%'
             : rawConf < 80 ? '70–79%'
             : rawConf < 90 ? '80–89%'
             : '90%+';
  const line = calibration.lines.find(l => l.band === want);
  if (!line) return null;                       // not enough resolved calls in this band yet
  const SHRINK_K = 12;                           // pull toward raw until ~12 samples accrue
  const w = line.n / (line.n + SHRINK_K);
  const mapped = Math.round(w * line.acc + (1 - w) * rawConf);
  return {
    raw: rawConf,
    mapped: Math.min(99, Math.max(1, mapped)),
    bandN: line.n, bandAcc: line.acc, totalN: calibration.n,
  };
}

// ── Setup feature vector + meta-labeling (B1/B2/B3) ───────────────────────────
// A compact, normalised vector describing the SETUP's market structure (known
// BEFORE the verdict). Persisted with every scan so that, as outcomes resolve via
// the triple-barrier rule (TP=+1 / SL=-1 / time-expiry=0), we can retrieve
// STRUCTURALLY-similar past setups and measure how often the committee was right
// on that KIND of setup — i.e. meta-labeling, the substrate for a future ML model.
// Retrieval is on structure (regime/trend/momentum/vol/confluence), NOT the ticker
// — the research showed surface (same-symbol) retrieval is the dominant RAG failure.
function buildSetupFeatures({ type, style, closes, rsi, adx, bbWidth, confluence, regimeName }) {
  const c01 = x => Math.max(0, Math.min(1, x));
  const sma50 = calcSMA(closes, 50), sma200 = calcSMA(closes, 200);
  const last = closes[closes.length - 1];
  const trendAlign = (sma50 && sma200) ? (sma50 > sma200 ? 1 : -1) : 0;
  const pxVsSma50 = sma50 ? c01((last / sma50 - 1) * 5 + 0.5) : 0.5;
  return {
    v: 1,
    asset: type || null,
    style: style || null,
    regime: regimeName || null,
    rsi:        rsi        != null ? +c01(rsi / 100).toFixed(3)      : null,
    adx:        adx        != null ? +c01(adx / 60).toFixed(3)       : null,
    vol:        bbWidth    != null ? +c01(bbWidth / 0.15).toFixed(3) : null,
    confluence: confluence != null ? +c01(confluence / 100).toFixed(3) : null,
    trendAlign,
    pxVsSma50:  +pxVsSma50.toFixed(3),
  };
}

// Structural distance between two setup vectors (0 = identical, higher = less alike).
function setupDistance(a, b) {
  const keys = ['rsi', 'adx', 'vol', 'confluence', 'pxVsSma50'];
  let sum = 0, n = 0;
  for (const k of keys) {
    if (a[k] == null || b[k] == null) continue;
    sum += (a[k] - b[k]) ** 2; n++;
  }
  if (!n) return Infinity;
  let d = Math.sqrt(sum / n);
  if (a.asset && b.asset && a.asset !== b.asset) d += 0.15;   // penalise cross-asset class matching
  if (a.trendAlign !== b.trendAlign) d += 0.15;
  if (a.regime && b.regime && a.regime !== b.regime) d += 0.15;
  if (a.style  && b.style  && a.style  !== b.style)  d += 0.10;
  return d;
}

// Meta-label: among RESOLVED past setups structurally similar to this one, how
// often was the committee's verdict correct (TP hit)? Shrunk toward the overall
// base rate by sample size (resists feedback-overfitting + thin-sample noise).
// Returns null until enough genuinely-similar resolved setups exist.
async function fetchMetaLabel(features) {
  try {
    if (!features) return null;
    const r = await fetch('/api/memory?all=true&resolved=true&lean=true&limit=1000');
    if (!r.ok) return null;
    const rows = await r.json();
    if (!Array.isArray(rows)) return null;
    let resolved = rows.filter(x => (x.outcome === 'tp_hit' || x.outcome === 'sl_hit') && x.setup_features);

    // Separate weekend crypto data from weekday crypto data (due to significant volume differences)
    if (features.asset === 'Crypto') {
      const wantWeekend = isWeekendNow();
      resolved = resolved.filter(x => {
        if (x.asset_type !== 'Crypto') return true;
        return isWeekendRow(x) === wantWeekend;
      });
    }

    if (resolved.length < 6) return null;
    const scored = resolved.map(x => {
      let f = x.setup_features;
      if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
      return f ? { d: setupDistance(features, f), win: x.outcome === 'tp_hit' } : null;
    }).filter(Boolean).sort((a, b) => a.d - b.d);
    // Evidence gate (learning-loop research): with few, loosely-similar neighbours a
    // k-NN directive is noise wearing the costume of evidence — injecting it measurably
    // HURTS (Halawi 2024: Brier 0.240 vs 0.175 below ~5 relevant items; the skeptic's
    // dimensionality math says <10 neighbours ≈ the global base rate + variance).
    // Below 10 genuinely-similar resolved setups: silence.
    const near = scored.slice(0, 15).filter(s => s.d <= 0.45);   // only genuinely similar
    if (near.length < 10) return null;
    const wins   = near.filter(s => s.win).length;
    const rawAcc = wins / near.length;
    const base   = resolved.filter(x => x.outcome === 'tp_hit').length / resolved.length;
    const w      = near.length / (near.length + 8);             // shrink toward base rate
    return {
      pCorrect: Math.round((w * rawAcc + (1 - w) * base) * 100),
      n: near.length, wins, losses: near.length - wins, base: Math.round(base * 100),
    };
  } catch { return null; }
}

// Retrieve post-mortem lessons from STRUCTURALLY-similar resolved trades (matched on
// regime/trend/momentum/volatility, NOT the same ticker — surface-ticker retrieval is
// the #1 RAG failure). These qualitative "what went wrong" notes are fed into the
// committee so the engine stops repeating its own mistakes. Returns up to 3 nearest.
async function fetchLessons(features) {
  try {
    if (!features) return [];
    const r = await fetch('/api/memory?all=true&resolved=true&lean=true&limit=1000');
    if (!r.ok) return [];
    const rows = await r.json();
    if (!Array.isArray(rows)) return [];
    let withLesson = rows.filter(x =>
      x.lesson && String(x.lesson).trim() &&
      (x.outcome === 'tp_hit' || x.outcome === 'sl_hit' || x.outcome === 'expired') &&
      x.setup_features);

    // Separate weekend crypto data from weekday crypto data (due to significant volume differences)
    if (features.asset === 'Crypto') {
      const wantWeekend = isWeekendNow();
      withLesson = withLesson.filter(x => {
        if (x.asset_type !== 'Crypto') return true;
        return isWeekendRow(x) === wantWeekend;
      });
    }

    if (!withLesson.length) return [];
    const scored = withLesson.map(x => {
      let f = x.setup_features;
      if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
      return f ? { d: setupDistance(features, f), row: x } : null;
    }).filter(Boolean).sort((a, b) => a.d - b.d);
    return scored.slice(0, 3).filter(s => s.d <= 0.55).map(s => ({
      sym: s.row.symbol, verdict: s.row.verdict, outcome: s.row.outcome, lesson: s.row.lesson,
    }));
  } catch { return []; }
}

// ── Position-size calculator (results panel) ──────────────────────────────────
// Pure risk math: shares/units = (account × risk%) ÷ per-unit risk (|entry−stop|).
// Account size + risk% persist in localStorage so the trader sets them once.
function _psGet(key, dflt) {
  const v = parseFloat(localStorage.getItem(key));
  return isNaN(v) ? dflt : v;
}
function renderPositionSizer(r) {
  const el = document.getElementById('positionSizer');
  if (!el) return;
  const a = r.analysis || {};
  const entry = parseEntryPrice(a.entry_zone);
  const stop  = parseFloat(a.stop_loss);
  const target = parseFloat(a.target_price);
  if (isNaN(entry) || isNaN(stop) || Math.abs(entry - stop) <= 0) { el.style.display = 'none'; el.innerHTML = ''; return; }

  const acct = _psGet('apex_ps_account', 10000);
  const riskPct = _psGet('apex_ps_riskpct', 1);
  el.style.display = '';
  el.innerHTML = `
    <div class="a-card-header"><span class="a-icon">🧮</span><h2>Position-Size Calculator</h2></div>
    <div class="ps-inputs">
      <label class="ps-field"><span>Account size ($)</span><input type="number" id="psAccount" min="1" step="100" value="${acct}"></label>
      <label class="ps-field"><span>Risk per trade (%)</span><input type="number" id="psRisk" min="0.1" max="100" step="0.1" value="${riskPct}"></label>
    </div>
    <div class="ps-out" id="psOut"></div>
    <p class="ps-note">Sized off this setup's entry (${fmtPrice(entry, r.type)}) and stop (${fmtPrice(stop, r.type)}). Risk-first: you never lose more than your chosen % if the stop is honoured.</p>`;

  const recompute = () => {
    const account = Math.max(0, parseFloat(document.getElementById('psAccount').value) || 0);
    const rp = Math.max(0, parseFloat(document.getElementById('psRisk').value) || 0);
    localStorage.setItem('apex_ps_account', String(account));
    localStorage.setItem('apex_ps_riskpct', String(rp));
    const riskAmount = account * rp / 100;
    const perUnit = Math.abs(entry - stop);
    const units = perUnit > 0 ? riskAmount / perUnit : 0;
    const notional = units * entry;
    const rewardPerUnit = !isNaN(target) ? Math.abs(target - entry) : null;
    const potReward = rewardPerUnit != null ? units * rewardPerUnit : null;
    const unitLabel = r.type === 'Forex' ? 'units' : r.type === 'Crypto' ? 'coins' : 'shares';
    document.getElementById('psOut').innerHTML = `
      <div class="ps-stat"><span class="ps-lbl">Size</span><span class="ps-val accent">${units >= 1 ? Math.floor(units).toLocaleString() : units.toFixed(4)} ${unitLabel}</span></div>
      <div class="ps-stat"><span class="ps-lbl">Position value</span><span class="ps-val">$${notional.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></div>
      <div class="ps-stat"><span class="ps-lbl">Risk ($)</span><span class="ps-val neg">$${riskAmount.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></div>
      ${potReward != null ? `<div class="ps-stat"><span class="ps-lbl">Reward at TP ($)</span><span class="ps-val pos">$${potReward.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></div>` : ''}`;
  };
  document.getElementById('psAccount').addEventListener('input', recompute);
  document.getElementById('psRisk').addEventListener('input', recompute);
  recompute();
}

// ── Multi-agent AI call ───────────────────────────────────────────────────────
// Calls /api/ai with a focused prompt. Returns the text or throws.
// Robust: reads the body as text and parses safely, so a transient gateway
// timeout / 5xx (which returns an HTML/text error page, not JSON) yields a
// clear retryable message instead of a cryptic "Unexpected token" crash.
// Retries once on a transient failure before giving up.
async function callAgent(system, prompt, maxTokens = 2500, opts = {}) {
  if (localStorage.getItem('apex_local_llm_enabled') === 'true' && window.callLocalLLM) {
    try {
      return await window.callLocalLLM(system, prompt, maxTokens);
    } catch (err) {
      console.error('[APEX] Local LLM connection failed. Falling back to cloud.', err);
    }
  }
  const attempt = async () => {
    const res = await fetch('/api/ai', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt,
        system,
        max_tokens:  maxTokens,
        temperature: 0.3,
        timeoutMs:   55000,
        // Optional explicit provider/model — used by the committee ensemble for
        // genuine model diversity. /api/ai falls back to its chain if it's down.
        ...(opts.provider ? { provider: opts.provider } : {}),
        ...(opts.model ? { model: opts.model } : {}),
      }),
    });

    const raw = await res.text();
    let data = null;
    try { data = raw ? JSON.parse(raw) : {}; } catch { data = null; }

    // Non-JSON body = platform error page (gateway timeout, 5xx, cold start).
    if (data === null) {
      const e = new Error(
        res.status >= 500 || res.status === 0
          ? `AI service hiccup (HTTP ${res.status || '—'}). Usually a brief timeout — retrying…`
          : `AI returned an unexpected response (HTTP ${res.status}).`
      );
      e._transient = res.status >= 500 || res.status === 0 || res.status === 408;
      throw e;
    }

    if (!res.ok || data.error) {
      // Surface rate-limit errors properly (not retryable here)
      if (res.status === 429 || data.retryAfterMs) {
        const mins = data.retryAfterMs ? Math.ceil(data.retryAfterMs / 60000) : null;
        throw new Error(mins
          ? `AI rate limit reached. Resets in ~${mins} min. Get a free GEMINI_API_KEY at aistudio.google.com to avoid this.`
          : (data.error || 'AI rate limit reached.'));
      }
      const e = new Error(data.error || `Agent error HTTP ${res.status}`);
      e._transient = res.status >= 500;
      throw e;
    }
    return data.text || '';
  };

  try {
    return await attempt();
  } catch (e) {
    if (e._transient) {
      await new Promise(r => setTimeout(r, 1500));
      return await attempt();   // one retry on transient gateway/5xx failures
    }
    throw e;
  }
}

// ── Committee ensemble — genuine model diversity ──────────────────────────────
// The competitive edge of running SEVERAL genuinely-different frontier models is that
// they don't share blind spots, so disagreement becomes signal. We run the FINAL
// verdict on multiple models in parallel and combine them. Member {} = the default
// provider chain (Gemini); the others force a specific provider/model. The "desk"
// evidence-gathering call stays single-model to respect the free-tier budget.
const COMMITTEE_MODELS = [
  { label: 'Gemini' },                                                          // default chain
  { label: 'Llama-3.3-70B', provider: 'groq', model: 'llama-3.3-70b-versatile' },
  // A 3rd genuinely-different model can be added here when budget allows.
];
// How many members to actually use. Manual scans get the full ensemble; the bulk
// auto-scan stays single-model so 16 scans/day don't blow the free Groq daily cap.
const ENSEMBLE_SIZE = { manual: 2, auto: 1 };

function _parseVerdictJSON(text) {
  const cleaned = String(text || '').replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
  const m = cleaned.match(/\{[\s\S]*\}/);
  if (!m) throw new Error('no JSON');
  return JSON.parse(m[0]);
}

// Run the committee prompt across N genuinely-different models in parallel; each gets
// 2 attempts to land clean JSON. Returns the members that succeeded (graceful: a dead
// provider just drops out, the scan never breaks).
async function runCommitteeEnsemble(system, prompt, n) {
  const members = COMMITTEE_MODELS.slice(0, Math.max(1, n || 1));
  const runMember = async (mem) => {
    for (let a = 0; a < 2; a++) {
      try { return { label: mem.label, analysis: _parseVerdictJSON(await callAgent(system, prompt, 6000, mem)) }; }
      catch { /* retry once, then drop */ }
    }
    return null;
  };
  return (await Promise.all(members.map(runMember))).filter(Boolean);
}

const _median = (arr) => {
  const s = [...arr].sort((a, b) => a - b);
  if (!s.length) return null;
  return s.length % 2 ? s[(s.length - 1) / 2] : Math.round((s[s.length / 2 - 1] + s[s.length / 2]) / 2);
};

// Combine ensemble members → one verdict. Majority/most-decisive direction, MEDIAN
// confidence, and an agreement score. Disagreement is signal: a split lowers confidence
// and (when there's no real majority) leans the verdict to WAIT.
function combineEnsemble(members) {
  if (members.length === 1) return members[0].analysis;
  const dirOf = a => verdictDir(a.verdict);
  const n = members.length;
  const dirs = members.map(m => dirOf(m.analysis));
  const counts = dirs.reduce((acc, d) => (acc[d] = (acc[d] || 0) + 1, acc), {});
  const majorityDir = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0];
  const agree = counts[majorityDir];
  const medianConf = _median(members.map(m => Number(m.analysis.confidence_score) || 50));
  // Base verdict = most decisive (highest confidence) among the majority direction.
  const base = members.filter(m => dirOf(m.analysis) === majorityDir)
    .sort((a, b) => (Number(b.analysis.confidence_score) || 0) - (Number(a.analysis.confidence_score) || 0))[0];
  const analysis = { ...base.analysis };
  let conf = medianConf;
  if (agree < n) conf = Math.round(medianConf * (0.6 + 0.4 * (agree / n)));   // any disagreement → shade down
  // No real majority (tie) or the majority itself is neutral → lean WAIT.
  if (agree <= n / 2 || majorityDir === 'neutral') {
    if (verdictDir(analysis.verdict) !== 'neutral') { analysis._ensemble_downgrade = analysis.verdict; analysis.verdict = 'WAIT'; }
    conf = Math.min(conf, 50);
  }
  analysis.confidence_score = conf;
  analysis._ensemble = {
    n, agree, score: `${agree}/${n}`, unanimous: agree === n,
    models: members.map(m => m.label),
    breakdown: members.map((m, i) => ({ model: m.label, dir: dirs[i], verdict: m.analysis.verdict, conf: Number(m.analysis.confidence_score) || null })),
  };
  return analysis;
}

// ── Market pulse ──────────────────────────────────────────────────────────────

async function loadPulse(sym, type, elId) {
  try {
    const { from, to } = alignedTimes('1d', 5);
    const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
    if (!r.ok) return;
    const bars = await r.json();
    if (!Array.isArray(bars) || bars.length < 2) return;
    const el = document.getElementById(elId); if (!el) return;
    const curr = bars[bars.length - 1].close, prev = bars[bars.length - 2].close;
    const pct = (curr - prev) / prev * 100;
    el.classList.remove('loading');
    el.querySelector('.pulse-price').textContent = type === 'Forex' ? curr.toFixed(5) : curr >= 100 ? curr.toFixed(2) : curr.toFixed(4);
    const ce = el.querySelector('.pulse-change');
    ce.textContent = fmtPct(pct); ce.className = `pulse-change ${pct >= 0 ? 'up' : 'down'}`;
    el.onclick = () => quickPick(sym);
  } catch {}
}
function initPulse() {
  loadPulse('SPY',     'ETF',     'pulse-SPY');
  loadPulse('QQQ',     'ETF',     'pulse-QQQ');
  loadPulse('BTC/USD', 'Crypto',  'pulse-BTC');
  loadPulse('EUR/USD', 'Forex',   'pulse-EUR');
  loadPulse('GC1!',    'Futures', 'pulse-GOLD');
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function showSection(id) {
  ['loadingSection', 'errorSection', 'resultsSection', 'cooldownSection'].forEach(s => {
    const el = document.getElementById(s); if (el) el.style.display = s === id ? '' : 'none';
  });
}
function hideAll() {
  ['loadingSection', 'errorSection', 'resultsSection', 'cooldownSection'].forEach(s => {
    const el = document.getElementById(s); if (el) el.style.display = 'none';
  });
}
function setStep(n) {
  for (let i = 1; i <= 5; i++) {
    const el = document.getElementById(`ls${i}`); if (!el) continue;
    el.className = i < n ? 'loader-step done' : i === n ? 'loader-step active' : 'loader-step';
  }
}
function showError(msg) {
  document.getElementById('errorMsg').textContent = msg;
  showSection('errorSection');
  document.getElementById('analyseBtn').disabled = false;
}
function resetState() {
  hideAll();
  document.getElementById('symInput').value = '';
  updateTypePill('');
  const pa = document.getElementById('preAnalysis');
  if (pa) { pa.style.display = 'none'; pa.innerHTML = ''; }
  _preData = { sym: null, events: null, sector: null };
  document.getElementById('analyseBtn').disabled = false;
  document.getElementById('heroSection').scrollIntoView({ behavior: 'smooth' });
}
function updateTypePill(sym) {
  const pill = document.getElementById('typePill');
  if (!sym.trim()) { pill.className = 'type-pill'; pill.textContent = ''; return; }
  const t = detectType(sym);
  pill.className = `type-pill ${t.toLowerCase()}`; pill.textContent = t;
}
function quickPick(sym) {
  document.getElementById('symInput').value = sym;
  updateTypePill(sym);
  closeDropdown();
  document.getElementById('symInput').focus();
  loadPreAnalysis(sym, detectType(sym));   // surface earnings/macro flags, sector RS + TradingView link
}
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ── Render ────────────────────────────────────────────────────────────────────

// ── Decision-quality helpers (D1 EV framing + A7 method-evidence honesty) ─────
// Parse a "1:2.5" / "2.5:1" / "2.5" risk:reward string into reward-per-1-unit-risk.
function parseRewardRisk(rr) {
  if (rr == null) return null;
  const s = String(rr).trim();
  const m = s.match(/(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)/);
  if (m) {
    const a = parseFloat(m[1]), b = parseFloat(m[2]);
    if (!(a > 0) || !(b > 0)) return null;
    return a === 1 ? b : (b === 1 ? a : b / a);   // normalise to reward per 1R risk
  }
  const n = parseFloat(s);
  return isFinite(n) && n > 0 ? n : null;
}

// Named discretionary methods with no independently verified edge (research-flagged).
const _UNPROVEN_METHODS = [
  { re: /\binner circle trader\b|\bICT\b/,                 name: 'ICT' },
  { re: /\bsmart money concepts?\b|\bSMC\b/i,              name: 'Smart Money Concepts' },
  { re: /\border block/i,                                  name: 'Order Blocks' },
  { re: /\bfair value gaps?\b|\bFVG\b/,                    name: 'Fair Value Gaps' },
  { re: /\bliquidity (?:grab|sweep|raid|pool)/i,           name: 'Liquidity sweeps' },
  { re: /\bjudas swing\b/i,                                name: 'Judas Swing' },
  { re: /\boptimal trade entry\b|\bOTE\b/,                 name: 'OTE' },
  { re: /\belliott waves?\b/i,                             name: 'Elliott Wave' },
  { re: /\bharmonic (?:pattern|trading)\b|\bgartley\b|\bbat pattern\b/i, name: 'Harmonic patterns' },
  { re: /\bgann\b/i,                                       name: 'Gann' },
];
// Scan the committee's prose for unproven-method references; returns unique names.
function methodEvidenceFlags(...texts) {
  const blob = texts.filter(Boolean).join(' \n ');
  const found = [];
  for (const m of _UNPROVEN_METHODS) if (m.re.test(blob) && !found.includes(m.name)) found.push(m.name);
  return found;
}

function renderResults({ sym, type, candles, weeklyCandles, quote, news, analysis: a, historicalScan, newsImpact, fibExt, tickerMemory, fearGreed, relStr, benchName, volProfile, adx, bbWidth, confluenceScore, macroIntermarket, qualityScores, engineData, positioning, seasonality, calibration, metaLabel, cryptoDerivs }) {
  const closes = candles.map(c => c.close);
  const curr   = closes[closes.length - 1];
  const prev   = closes[closes.length - 2];
  const chgPct = (curr - prev) / prev * 100;

  // ── Verdict card ──
  setText('vcSymbol', sym.toUpperCase());
  setText('vcName',   quote?.name || type);
  const tb = document.getElementById('vcTypeBadge');
  tb.textContent = type; tb.className = `vc-type-badge ${type.toLowerCase()}`;

  setText('vcPrice', fmtPrice(curr, type));
  const ce = document.getElementById('vcChg');
  ce.textContent = fmtPct(chgPct); ce.className = `vc-chg ${chgPct >= 0 ? 'up' : 'down'}`;

  const rawVerdict = (a.verdict || 'HOLD').toUpperCase().replace(/ /g, '_');
  const vbadge = document.getElementById('verdictBadge');
  // Accessibility: carry a shape icon alongside the word so the verdict never
  // relies on red/green colour alone (~1 in 12 men are red-green colourblind).
  const _vIcon = /BUY|LONG/.test(rawVerdict) ? '▲ ' : /SELL|SHORT/.test(rawVerdict) ? '▼ ' : '⏸ ';
  vbadge.textContent = _vIcon + rawVerdict.replace(/_/g, ' ');
  vbadge.className   = `verdict-badge ${rawVerdict.toLowerCase()}`;

  const rawConf = Math.min(100, Math.max(0, Number(a.confidence_score) || 50));
  // Hard post-hoc calibration: re-map the displayed number through the realised
  // hit-rate curve so "78%" actually means 78%. Falls back to raw until enough
  // resolved calls exist in that confidence band.
  const cal  = calibrateConfidence(rawConf, calibration);
  const conf = cal ? cal.mapped : rawConf;
  setText('confPct', `${conf}%`);
  const calNote = document.getElementById('confCalibNote');
  if (calNote) {
    if (cal && Math.abs(cal.mapped - cal.raw) >= 2) {
      calNote.innerHTML = `📊 Calibrated <b>${cal.raw}% → ${cal.mapped}%</b> from your ${cal.totalN} resolved calls — this confidence band has actually hit target ${cal.bandAcc}% of the time (n=${cal.bandN}).`;
      calNote.style.display = '';
    } else if (cal) {
      calNote.innerHTML = `📊 Confidence calibrated against ${cal.totalN} resolved calls.`;
      calNote.style.display = '';
    } else {
      calNote.style.display = 'none';
    }
  }
  const fill = document.getElementById('confFill');
  fill.style.width = '0%';
  fill.className = `conf-fill ${rawVerdict.toLowerCase()}`;
  requestAnimationFrame(() => setTimeout(() => { fill.style.width = `${conf}%`; }, 80));

  setText('vcThesis',      a.executive_summary || '');
  setText('confNotHigher', a.why_confidence_not_higher ? `Why not higher: ${a.why_confidence_not_higher}` : '');

  // ── Action bar (TradingView · Share · Watchlist) + position-size calculator ──
  _lastResult = { sym: sym.toUpperCase(), type, analysis: a, price: curr };
  renderResultsActions(_lastResult);
  renderPositionSizer(_lastResult);

  // ── Stats grid ──
  const rsi    = calcRSI(closes);
  const sma20  = calcSMA(closes, 20);
  const sma50  = calcSMA(closes, 50);
  const sma200 = calcSMA(closes, 200);
  const { supports, resistances } = findPivotSR(candles);
  const atr    = calcATR(candles);
  const chg7d  = closes.length > 7  ? (curr - closes[closes.length - 8])  / closes[closes.length - 8]  * 100 : null;
  const chg30d = closes.length > 30 ? (curr - closes[closes.length - 31]) / closes[closes.length - 31] * 100 : null;

  const statsGrid = document.getElementById('statsGrid');
  statsGrid.innerHTML = [
    { l: 'RSI (14)',    v: rsi  != null ? String(rsi)     : '—', c: rsi  ? (rsi > 70  ? 'down' : rsi < 30  ? 'up' : 'neutral') : '' },
    { l: '7-Day',      v: chg7d  != null ? fmtPct(chg7d)  : '—', c: chg7d  != null ? (chg7d  >= 0 ? 'up' : 'down') : '' },
    { l: '30-Day',     v: chg30d != null ? fmtPct(chg30d) : '—', c: chg30d != null ? (chg30d >= 0 ? 'up' : 'down') : '' },
    { l: 'Support',    v: supports[0]    != null ? fmtPrice(supports[0],    type) : '—', c: '' },
    { l: 'Resistance', v: resistances[0] != null ? fmtPrice(resistances[0], type) : '—', c: '' },
    { l: 'ATR (14)',   v: atr != null ? fmtPrice(atr, type) : '—', c: '' },
  ].map(s => `<div class="stat-item"><div class="stat-label">${s.l}</div><div class="stat-value ${s.c}">${s.v}</div></div>`).join('');

  // Fear & Greed
  if (fearGreed) {
    const fgClass = fearGreed.value <= 30 ? 'up' : fearGreed.value >= 70 ? 'down' : '';
    statsGrid.innerHTML += `<div class="stat-item"><div class="stat-label">Fear &amp; Greed</div><div class="stat-value ${fgClass}">${fearGreed.value} — ${fearGreed.label}</div></div>`;
  }
  // Relative Strength
  if (relStr?.rs1m != null) {
    const rsClass = relStr.rs1m > 2 ? 'up' : relStr.rs1m < -2 ? 'down' : '';
    statsGrid.innerHTML += `<div class="stat-item"><div class="stat-label">vs ${benchName} (1M)</div><div class="stat-value ${rsClass}">${relStr.rs1m > 0 ? '+' : ''}${relStr.rs1m}%</div></div>`;
  }
  // Volume Profile POC
  if (volProfile) {
    const pocAbove = candles[candles.length-1].close > volProfile.poc;
    statsGrid.innerHTML += `<div class="stat-item"><div class="stat-label">Vol Profile POC</div><div class="stat-value ${pocAbove ? 'up' : 'down'}">${volProfile.poc} (${pocAbove ? 'above' : 'below'})</div></div>`;
  }
  // Confluence Score
  if (confluenceScore) {
    const csClass = confluenceScore.bullPct >= 65 ? 'up' : confluenceScore.bullPct <= 35 ? 'down' : 'neutral';
    const csTitle = `${confluenceScore.independentSignals} independent signal families (correlated indicators down-weighted)${confluenceScore.concentrated ? ' · ⚠ concentrated in one family — not genuine confluence' : ''}`;
    statsGrid.innerHTML += `<div class="stat-item" title="${csTitle}"><div class="stat-label">Confluence${confluenceScore.concentrated ? ' ⚠' : ''}</div><div class="stat-value ${csClass}">${confluenceScore.bullPct}% Bull (${confluenceScore.direction})</div></div>`;
  }
  // Yield Curve (from intermarket)
  if (macroIntermarket?.yield_curve?.value != null) {
    const yc = macroIntermarket.yield_curve;
    const ycClass = yc.value < 0 ? 'down' : yc.value > 0.5 ? 'up' : 'neutral';
    statsGrid.innerHTML += `<div class="stat-item"><div class="stat-label">Yield Curve (2s10s)</div><div class="stat-value ${ycClass}">${yc.value > 0 ? '+' : ''}${yc.value.toFixed(2)}% (${yc.label})</div></div>`;
  }
  // Model-ensemble agreement — how many independent models agreed on the direction.
  // Full agreement = higher conviction; a split lowers confidence / leans the call to WAIT.
  if (a._ensemble && a._ensemble.n > 1) {
    const e = a._ensemble;
    const cls = e.unanimous ? 'up' : e.agree <= e.n / 2 ? 'down' : 'neutral';
    const tip = 'Independent models — ' + e.breakdown.map(b => `${b.model}: ${String(b.verdict || '').replace(/_/g, ' ')}${b.conf != null ? ' ' + b.conf + '%' : ''}`).join(' · ') + (e.unanimous ? '. Full agreement → conviction.' : '. Disagreement → confidence shaded down / leans WAIT.');
    statsGrid.innerHTML += `<div class="stat-item" title="${tip.replace(/"/g, '&quot;')}"><div class="stat-label">Model agreement</div><div class="stat-value ${cls}">${e.score} agree${e.unanimous ? ' ✓' : ''}</div></div>`;
  }
  // Data-feed freshness — shows the measured age of the latest bar so the delay is
  // visible, and warns (⚠) when the market is open but the feed appears delayed.
  if (candles && candles.length > 2) {
    const ageMin = Math.round((Date.now() / 1000 - candles[candles.length - 1].time) / 60);
    const barMin = Math.max(1, Math.round((candles[candles.length - 1].time - candles[candles.length - 2].time) / 60));
    const ms = marketSession(type);
    const delayed = ms.open && ageMin > barMin * 1.5 + 12;
    const cls = !ms.open ? 'neutral' : delayed ? 'down' : 'up';
    const label = ageMin < 90 ? `${ageMin}m old` : ageMin < 1440 ? `${Math.round(ageMin / 60)}h old` : `${Math.round(ageMin / 1440)}d old`;
    const tip = !ms.open ? 'Market closed — this is the last close, not live.' : delayed ? `Market open but the free feed looks ~${ageMin} min behind — intraday precision is reduced; the committee shades confidence down.` : 'Feed is fresh / near real-time.';
    statsGrid.innerHTML += `<div class="stat-item" title="${tip}"><div class="stat-label">Data feed${delayed ? ' ⚠' : ''}</div><div class="stat-value ${cls}">${label}</div></div>`;
  }
  // VIX (from intermarket)
  if (macroIntermarket?.vix?.value != null) {
    const vx = macroIntermarket.vix;
    const vxClass = vx.value > 30 ? 'down' : vx.value < 18 ? 'up' : 'neutral';
    statsGrid.innerHTML += `<div class="stat-item"><div class="stat-label">VIX</div><div class="stat-value ${vxClass}">${vx.value.toFixed(1)} — ${vx.label}</div></div>`;
  }
  // Piotroski F-Score
  if (qualityScores?.piotroski?.score != null) {
    const fs = qualityScores.piotroski.score;
    const fsClass = fs >= 7 ? 'up' : fs <= 3 ? 'down' : 'neutral';
    statsGrid.innerHTML += `<div class="stat-item"><div class="stat-label">Piotroski F-Score</div><div class="stat-value ${fsClass}">${fs}/9 — ${qualityScores.piotroski.quality}</div></div>`;
  }
  // COT speculative positioning
  if (positioning && positioning.net != null) {
    const netLong = positioning.net >= 0;
    const crowded = positioning.pct_long >= 75 || positioning.pct_long <= 25;
    const cls = crowded ? 'neutral' : netLong ? 'up' : 'down';
    statsGrid.innerHTML += `<div class="stat-item" title="Weak evidence / slow context only — COT is lagged (~3d), weekly, and a poor timing signal. Not a primary reason."><div class="stat-label">COT Specs (${escHtmlSafe(positioning.asset)}) · weak</div><div class="stat-value ${cls}">${positioning.pct_long}% long${crowded ? ' ⚠' : ''}</div></div>`;
  }
  // Seasonality (current month)
  if (seasonality) {
    const sClass = seasonality.avgReturn > 0.5 ? 'up' : seasonality.avgReturn < -0.5 ? 'down' : 'neutral';
    statsGrid.innerHTML += `<div class="stat-item" title="Weak evidence — calendar seasonality is contested/often data-mined and marginal after costs (n=${seasonality.years} yrs). A soft tie-breaker only."><div class="stat-label">${escHtmlSafe(seasonality.month)} Seasonality · weak</div><div class="stat-value ${sClass}">${seasonality.avgReturn > 0 ? '+' : ''}${seasonality.avgReturn}% · ${seasonality.winRate}% pos</div></div>`;
  }
  if (metaLabel) {
    const mClass = metaLabel.pCorrect >= 60 ? 'bull' : metaLabel.pCorrect < 45 ? 'bear' : 'neutral';
    statsGrid.innerHTML += `<div class="stat-item" title="Committee's realised accuracy on the ${metaLabel.n} most structurally-similar resolved setups (not the same ticker)"><div class="stat-label">Setup Reliability</div><div class="stat-value ${mClass}">${metaLabel.pCorrect}% · ${metaLabel.wins}W/${metaLabel.losses}L</div></div>`;
  }
  // Crypto derivatives & structure (funding, long/short crowding, BTC dominance)
  if (cryptoDerivs) {
    const fd = cryptoDerivs.funding;
    if (fd) {
      const fCls = fd.label === 'neutral' ? 'neutral' : fd.rate_8h_pct > 0 ? 'down' : 'up';   // crowded longs = downside risk
      statsGrid.innerHTML += `<div class="stat-item" title="Perp funding ${fd.rate_8h_pct}%/8h (~${fd.annualized_pct}%/yr) — ${fd.label}. Real-time crowding/squeeze read."><div class="stat-label">Perp Funding</div><div class="stat-value ${fCls}">${fd.rate_8h_pct > 0 ? '+' : ''}${fd.rate_8h_pct}% · ${fd.label.split(' ')[0]}</div></div>`;
    }
    if (cryptoDerivs.long_short) {
      const ls = cryptoDerivs.long_short;
      const lCls = (ls.pct_long >= 65 || ls.pct_long <= 35) ? 'neutral' : ls.pct_long >= 50 ? 'up' : 'down';
      statsGrid.innerHTML += `<div class="stat-item" title="Retail account long/short ratio ${ls.account_ratio} — ${ls.label}. Contrarian at extremes."><div class="stat-label">Retail L/S</div><div class="stat-value ${lCls}">${ls.pct_long}% long${(ls.pct_long >= 65 || ls.pct_long <= 35) ? ' ⚠' : ''}</div></div>`;
    }
    if (cryptoDerivs.open_interest && cryptoDerivs.open_interest.change_7d_pct != null) {
      const oi = cryptoDerivs.open_interest;
      statsGrid.innerHTML += `<div class="stat-item" title="Open interest ${oi.btc.toLocaleString()} ${cryptoDerivs.base}, ${oi.change_7d_pct}% over 7d — ${oi.label}. Rising OI = fresh leverage."><div class="stat-label">Open Interest (7d)</div><div class="stat-value ${oi.change_7d_pct > 5 ? 'up' : oi.change_7d_pct < -5 ? 'down' : 'neutral'}">${oi.change_7d_pct > 0 ? '+' : ''}${oi.change_7d_pct}%</div></div>`;
    }
    if (cryptoDerivs.dominance_pct != null) {
      statsGrid.innerHTML += `<div class="stat-item" title="Bitcoin dominance — BTC's share of total crypto market cap. Rising = risk-off within crypto."><div class="stat-label">BTC Dominance</div><div class="stat-value neutral">${cryptoDerivs.dominance_pct}%</div></div>`;
    }
    // Spot-ETF 5-day net flow (real institutional demand)
    if (cryptoDerivs.etf && cryptoDerivs.etf.net_5d_usd != null) {
      const e = cryptoDerivs.etf;
      const eCls = e.net_5d_usd > 0 ? 'up' : e.net_5d_usd < 0 ? 'down' : 'neutral';
      const eFmt = (n) => { const s = n < 0 ? '-$' : '+$', a = Math.abs(n); return a >= 1e9 ? s + (a / 1e9).toFixed(1) + 'B' : s + (a / 1e6).toFixed(0) + 'M'; };
      statsGrid.innerHTML += `<div class="stat-item" title="Spot Bitcoin/Ether ETF net flow over 5 days (SoSoValue) — real institutional demand. ${e.streak_days}-day ${e.streak_dir} streak."><div class="stat-label">ETF Flow (5d)</div><div class="stat-value ${eCls}">${eFmt(e.net_5d_usd)}</div></div>`;
    }
    // On-chain MVRV (valuation vs aggregate cost basis)
    if (cryptoDerivs.onchain && cryptoDerivs.onchain.mvrv != null) {
      const mv = cryptoDerivs.onchain.mvrv;
      const mCls = mv < 1 ? 'up' : mv > 3.5 ? 'down' : 'neutral';
      statsGrid.innerHTML += `<div class="stat-item" title="MVRV — market value vs realized (cost-basis) value. <1 = below aggregate cost (value zone); >3.5 = historically near tops. Realized price: ${cryptoDerivs.onchain.realized_price ? '$' + cryptoDerivs.onchain.realized_price.toLocaleString() : 'n/a'}."><div class="stat-label">MVRV</div><div class="stat-value ${mCls}">${mv}${cryptoDerivs.onchain.sopr != null ? ` · SOPR ${cryptoDerivs.onchain.sopr}` : ''}</div></div>`;
    }
    // Implied volatility (Deribit DVOL)
    if (cryptoDerivs.vol && cryptoDerivs.vol.implied_dvol_pct != null) {
      const iv = cryptoDerivs.vol.implied_dvol_pct;
      statsGrid.innerHTML += `<div class="stat-item" title="Deribit DVOL — option-implied annualized volatility. Higher = market pricing bigger forward moves."><div class="stat-label">Implied Vol (DVOL)</div><div class="stat-value ${iv > 60 ? 'down' : iv < 40 ? 'up' : 'neutral'}">${iv}%</div></div>`;
    }
  }

  // ── Macro ──
  const reg = (a.macro_regime || '').toLowerCase().replace(/[_ ]/g, '-');
  const rb = document.getElementById('regimeBadge');
  rb.textContent = a.macro_regime ? a.macro_regime.replace(/-/g, ' ').toUpperCase() : '';
  rb.className = `regime-badge ${reg}`;
  setText('macroText', a.macro_environment || '');

  // Remove any previously rendered intermarket strip to avoid duplication on rescan
  document.querySelector('.intermarket-strip')?.remove();

  // Append live intermarket data below the AI macro text
  const macroTextEl = document.getElementById('macroText');
  if (macroTextEl && macroIntermarket) {
    const im = macroIntermarket;
    const lines = [];
    if (im.yield_curve?.signal)           lines.push(`📊 Yield Curve: ${im.yield_curve.signal}`);
    if (im.hy_oas?.signal)                lines.push(`💳 HY Credit: ${im.hy_oas.signal}`);
    if (im.vix?.signal)                   lines.push(`📉 VIX: ${im.vix.signal}`);
    if (im.dxy?.signal)                   lines.push(`💵 DXY: ${im.dxy.signal}`);
    if (im.bond_equity_correlation)       lines.push(`🔗 Bond/Equity: ${im.bond_equity_correlation}`);
    if (lines.length) {
      const div = document.createElement('div');
      div.className = 'intermarket-strip';
      div.innerHTML = lines.map(l => `<div class="im-line">${l}</div>`).join('');
      macroTextEl.after(div);
    }
  }

  // ── Technical ──
  const macd    = calcMACD(closes);
  const bb      = calcBollingerBands(closes);
  const stochRsi= calcStochRSI(closes);
  const obv     = calcOBVTrend(candles);
  const volTrnd = calcVolTrend(candles);
  const trend   = getTrend(closes, sma20, sma50);
  const wCloses = weeklyCandles?.map(c => c.close);
  const wRSI    = wCloses ? calcRSI(wCloses)    : null;
  const wSMA20  = wCloses ? calcSMA(wCloses, 20) : null;
  const wCurr   = wCloses?.[wCloses.length - 1];
  const wTrend  = wCloses && wCurr && wSMA20 ? (wCurr > wSMA20 ? 'bullish' : 'bearish') : null;

  const chips = [
    { l: 'Trend',     v: trend,                                  c: trend.includes('bull') ? 'bull' : trend.includes('bear') ? 'bear' : 'neutral' },
    { l: 'MACD',      v: macd != null ? (macd > 0 ? 'Bullish' : 'Bearish') : '—', c: macd != null ? (macd > 0 ? 'bull' : 'bear') : 'neutral' },
    { l: 'SMA20',     v: sma20 ? (curr > sma20 ? 'Above' : 'Below') : '—',        c: sma20 ? (curr > sma20 ? 'bull' : 'bear') : 'neutral' },
    { l: 'SMA50',     v: sma50 ? (curr > sma50 ? 'Above' : 'Below') : '—',        c: sma50 ? (curr > sma50 ? 'bull' : 'bear') : 'neutral' },
    { l: 'BB %B',     v: bb    != null ? `${bb.pctB}%`    : '—',                  c: bb != null ? (bb.pctB > 80 ? 'bear' : bb.pctB < 20 ? 'bull' : 'neutral') : 'neutral' },
    { l: 'StochRSI',  v: stochRsi != null ? String(stochRsi) : '—',               c: stochRsi != null ? (stochRsi > 80 ? 'bear' : stochRsi < 20 ? 'bull' : 'neutral') : 'neutral' },
    { l: 'OBV',       v: obv,                                    c: obv === 'accumulation' ? 'bull' : obv === 'distribution' ? 'bear' : 'neutral' },
    { l: 'Volume',    v: volTrnd,                                 c: volTrnd === 'rising' ? 'bull' : volTrnd === 'falling' ? 'bear' : 'neutral' },
    ...(wTrend ? [{ l: 'Wkly',     v: wTrend, c: wTrend.includes('bull') ? 'bull' : 'bear' }] : []),
    ...(wRSI != null ? [{ l: 'Wkly RSI', v: String(wRSI), c: wRSI > 70 ? 'bear' : wRSI < 30 ? 'bull' : 'neutral' }] : []),
  ];
  const strip = document.getElementById('indicatorsStrip');
  strip.innerHTML = chips.map(c =>
    `<div class="ind-chip"><span class="ic-lbl">${c.l}</span><span class="ic-val ${c.c}">${c.v}</span></div>`
  ).join('');

  // ADX badge
  if (adx != null) {
    const adxClass = adx > 25 ? 'bull' : adx < 15 ? 'bear' : 'neutral';
    strip.innerHTML += `<div class="ind-badge ${adxClass}"><span class="ind-label">ADX</span><span class="ind-val">${adx}</span><span class="ind-sub">${adx > 25 ? 'Strong' : adx > 15 ? 'Moderate' : 'Weak'}</span></div>`;
  }
  // BB Squeeze badge
  if (bbWidth != null && bbWidth < 3) {
    strip.innerHTML += `<div class="ind-badge neutral"><span class="ind-label">BB</span><span class="ind-val">SQUEEZE</span><span class="ind-sub">Breakout near</span></div>`;
  }

  setText('techText', a.technical_analysis || '');

  // ── Fundamental ──
  const fundTitle = type === 'Forex' ? 'Macro & FX Context' : type === 'Crypto' ? 'On-Chain & Market Context' : 'Fundamental Analysis';
  setText('fundTitle', fundTitle);
  const val = (a.valuation || '').toLowerCase().replace(/ /g, '-');
  const vb  = document.getElementById('valuationBadge');
  vb.textContent = a.valuation ? a.valuation.replace(/-/g, ' ').toUpperCase() : '';
  vb.className   = `valuation-badge ${val}`;

  if (quote && type === 'Stock') {
    const fundGrid = document.getElementById('fundGrid');
    fundGrid.innerHTML = [
      { k: 'Market Cap',     v: fmtMCap(quote.marketCap) },
      { k: 'P/E (TTM)',      v: quote.pe        ? fmtNum(quote.pe, 1) + 'x'        : '—' },
      { k: 'Forward P/E',   v: quote.forwardPE  ? fmtNum(quote.forwardPE, 1) + 'x' : '—' },
      { k: 'EPS (TTM)',      v: quote.eps        ? '$' + fmtNum(quote.eps)           : '—' },
      { k: '52W High',       v: quote.week52High ? '$' + fmtNum(quote.week52High)    : '—' },
      { k: '52W Low',        v: quote.week52Low  ? '$' + fmtNum(quote.week52Low)     : '—' },
      { k: 'Beta',           v: fmtNum(quote.beta) },
      { k: 'Rev Growth',     v: quote.revenueGrowth  ? fmtPct(quote.revenueGrowth  * 100) : '—' },
      { k: 'Earn Growth',    v: quote.earningsGrowth ? fmtPct(quote.earningsGrowth * 100) : '—' },
      { k: 'Analyst Target', v: quote.targetMeanPrice ? '$' + fmtNum(quote.targetMeanPrice) : '—' },
    ].map(i => `<div class="fund-item"><span class="fund-key">${i.k}</span><span class="fund-val">${i.v}</span></div>`).join('');
    if (quote?.metrics) {
      const m = quote.metrics;
      if (m.debtEquityAnnual  != null) fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">D/E Ratio</span><span class="fund-val">${fmtNum(m.debtEquityAnnual,2)}x</span></div>`;
      if (m.evEbitdaAnnual    != null) fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">EV/EBITDA</span><span class="fund-val">${fmtNum(m.evEbitdaAnnual,1)}x</span></div>`;
      if (m.psAnnual          != null) fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">P/S Ratio</span><span class="fund-val">${fmtNum(m.psAnnual,1)}x</span></div>`;
      if (m.fcfPerShareAnnual != null) fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">FCF/Share</span><span class="fund-val">$${fmtNum(m.fcfPerShareAnnual)}</span></div>`;
      // Finnhub already reports these as percentages (e.g. 74.15 = 74.15%), so do NOT ×100.
      if (m.grossMarginTTM    != null) fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">Gross Margin</span><span class="fund-val">${fmtPct(m.grossMarginTTM)}</span></div>`;
      if (m.roeTTM            != null) fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">ROE (TTM)</span><span class="fund-val">${fmtPct(m.roeTTM)}</span></div>`;
    }
    // Quality Scores (Piotroski, Beneish, Accrual, Altman Z)
    if (qualityScores) {
      const qs = qualityScores;
      if (qs.piotroski?.score != null) {
        const fc = qs.piotroski.score >= 7 ? 'green' : qs.piotroski.score <= 3 ? 'red' : '';
        fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">Piotroski F-Score</span><span class="fund-val" style="color:${fc==='green'?'var(--green)':fc==='red'?'var(--red)':''}">${qs.piotroski.score}/9 (${qs.piotroski.quality})</span></div>`;
      }
      if (qs.beneish?.score != null) {
        const bc = qs.beneish.flag === 'MANIPULATION_RISK' ? 'red' : 'green';
        fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">Beneish M-Score</span><span class="fund-val" style="color:${bc==='red'?'var(--red)':'var(--green)'}">${qs.beneish.score} (${qs.beneish.flag === 'MANIPULATION_RISK' ? '⚠ risk' : '✓ clean'})</span></div>`;
      }
      if (qs.accrual_ratio?.pct != null) {
        const ac = qs.accrual_ratio.flag === 'HIGH_ACCRUALS' ? 'red' : qs.accrual_ratio.flag === 'CONSERVATIVE' ? 'green' : '';
        fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">Accrual Ratio</span><span class="fund-val" style="color:${ac==='red'?'var(--red)':ac==='green'?'var(--green)':''}">${qs.accrual_ratio.pct}%</span></div>`;
      }
      if (qs.altman_z?.score != null) {
        const zc = qs.altman_z.zone === 'safe' ? 'green' : qs.altman_z.zone === 'distress' ? 'red' : '';
        fundGrid.innerHTML += `<div class="fund-item"><span class="fund-key">Altman Z-Score</span><span class="fund-val" style="color:${zc==='green'?'var(--green)':zc==='red'?'var(--red)':''}">${qs.altman_z.score} (${qs.altman_z.zone})</span></div>`;
      }
    }
  } else {
    document.getElementById('fundGrid').innerHTML = '';
  }
  setText('fundText', a.fundamental_analysis || '');

  // ── Sentiment ──
  const sc = (a.sentiment_condition || '').toLowerCase().replace(/ /g, '-');
  const sb = document.getElementById('sentimentBadge');
  sb.textContent = a.sentiment_condition ? a.sentiment_condition.replace(/-/g, ' ').toUpperCase() : '';
  sb.className   = `sentiment-badge ${sc}`;
  setText('sentText', a.sentiment_analysis || '');

  // ── Catalysts + news ──
  setText('catalystText', a.catalyst_analysis || '');
  document.getElementById('newsGrid').innerHTML = news.slice(0, 4).map(n => `
    <a class="news-item" href="${n.link || '#'}" target="_blank" rel="noopener noreferrer">
      <div class="news-title">${n.title || ''}</div>
      <div class="news-meta">${n.source || ''} · ${n.date ? new Date(n.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : ''}</div>
    </a>`).join('');

  // ── Scenarios ──
  const ev  = (a.expected_value || 'neutral').toLowerCase().replace(/ /g, '-');
  const evb = document.getElementById('evBadge');
  evb.textContent = `EV: ${(a.expected_value || 'Neutral').replace(/-/g, ' ').toUpperCase()}`;
  evb.className   = `ev-badge ${ev}`;

  const sc_data = a.scenarios || {};
  document.getElementById('scenarioGrid').innerHTML = [
    { key: 'bull',    label: 'Bull',    data: sc_data.bull    },
    { key: 'base',    label: 'Base',    data: sc_data.base    },
    { key: 'bear',    label: 'Bear',    data: sc_data.bear    },
    { key: 'extreme', label: 'Extreme', data: sc_data.extreme },
  ].map(s => {
    const d = s.data || {};
    const chg = d.upside || d.change || d.downside || '';
    return `<div class="scenario-card ${s.key}">
      <div class="sc-header"><span class="sc-label">${s.label}</span><span class="sc-prob">${d.probability != null ? d.probability + '%' : '—'}</span></div>
      <div class="sc-target">${d.target || '—'}</div>
      <div class="sc-change">${chg}</div>
      <div class="sc-desc">${d.description || ''}</div>
    </div>`;
  }).join('');

  // ── Risk ──
  setText('riskText', a.risk_analysis || '');

  // ── Time horizons ──
  document.getElementById('horizonGrid').innerHTML = [
    { label: 'Short-Term (days–4 wks)',  text: a.short_term_outlook  },
    { label: 'Medium-Term (1–3 months)', text: a.medium_term_outlook },
    { label: 'Long-Term (3–12 months)',  text: a.long_term_outlook   },
  ].map(h => `<div class="horizon-card"><div class="horizon-label">${h.label}</div><div class="horizon-text">${h.text || '—'}</div></div>`).join('');

  // ── Trade plan guide (prominent, actionable: direction + WHEN to enter) ──
  const _v = (a.verdict || '').toUpperCase();
  const _isLong = /BUY/.test(_v), _isShort = /SELL|SHORT/.test(_v);
  const _dirLabel = _isLong ? 'LONG · BUY' : _isShort ? 'SHORT · SELL' : 'NO ACTIVE TRADE — conditional plan';
  const _dirCls = _isLong ? 'pos' : _isShort ? 'neg' : 'neu';
  const _tp2 = (a.take_profit_2 && String(a.take_profit_2) !== String(a.target_price)) ? a.take_profit_2 : null;
  // Expected-value framing (D1): judge the BET, not the single outcome. A 45%
  // win-rate trade at 1:3 is +EV; an 80% trade at 1:0.2 is −EV. Uses the
  // CALIBRATED confidence so the edge number is honest.
  const _rr = parseRewardRisk(a.risk_reward);
  let _evBlock = '';
  if ((_isLong || _isShort) && _rr) {
    const p   = conf / 100;                          // calibrated win probability
    const evR = +(p * _rr - (1 - p)).toFixed(2);     // expected value in R-multiples (risk = 1R)
    _evBlock = `<div class="tpg-ev ${evR > 0 ? 'pos' : 'neg'}"><strong>Expected value:</strong> ${evR > 0 ? '+' : ''}${evR}R per trade <span class="tpg-ev-sub">${conf}% win prob × ${_rr.toFixed(1)}:1 reward — ${evR > 0 ? 'positive edge over many trades' : 'NEGATIVE edge: even a good-looking setup loses money long-run'}</span></div>`;
  }
  const _premortem = a.premortem
    ? `<div class="tpg-premortem"><strong>⚠ Pre-mortem — if this loses:</strong> ${escHtmlSafe(a.premortem)}</div>` : '';
  const _methods = methodEvidenceFlags(a.technical_analysis, a.executive_summary,
    Array.isArray(a.key_reasons) ? a.key_reasons.join(' ') : a.key_reasons);
  const _methodFlag = _methods.length
    ? `<div class="tpg-method">⚠ References <strong>${_methods.map(escHtmlSafe).join(', ')}</strong> — popular but no independently verified edge (largely repackaged support/resistance & supply/demand). Treated as soft context only, not the basis for the verdict.</div>` : '';
  const _lagNote = (_tradeStyle === 'scalp' || _tradeStyle === 'intraday')
    ? `<div class="tpg-lag">⚠ Free price feed can lag ~15&nbsp;min — treat these ${tradeStyle().label.toLowerCase()} entry/stop levels as <strong>approximate</strong>, not exact live fills. Swing/position styles are less affected.</div>` : '';
  const _guide = document.getElementById('tradePlanGuide');
  if (_guide) _guide.innerHTML = `
    <div class="tpg-head"><span class="tpg-dir ${_dirCls}">${_dirLabel}</span><span class="tpg-style">${tradeStyle().label}${a.timeframe ? ` · ${escHtmlSafe(a.timeframe)}` : ''}</span></div>
    ${_lagNote}
    ${a.entry_trigger ? `<div class="tpg-trigger"><strong>When to enter:</strong> ${escHtmlSafe(a.entry_trigger)}</div>` : ''}
    ${_evBlock}
    ${_premortem}
    ${_methodFlag}
    ${a._rr_downgrade ? `<div class="tpg-warn">🛡️ <strong>Professional R:R gate:</strong> ${escHtmlSafe(a._rr_downgrade)}</div>`
      : a._rr_weak ? `<div class="tpg-warn">⚠ Weak setup — reward-to-risk is only ${escHtmlSafe(a.risk_reward)}, below the ${a._rr_min || MIN_RR}:1 a professional ${tradeStyle().label} trade requires. Consider waiting for a better entry.</div>`
      : a._rr_aplus ? `<div class="tpg-aplus">✅ A+ reward-to-risk (${escHtmlSafe(a.risk_reward)}) — pays at least 3× the risk.</div>` : ''}
    <div class="tpg-legal">Information &amp; education only — <strong>not financial advice</strong> and not a personal recommendation. You decide and act for yourself.</div>`;

  // ── Trade levels ──
  document.getElementById('tradeLevels').innerHTML = `
    <div class="trade-level"><div class="tl-label">Entry</div><div class="tl-value entry">${a.entry_zone  || '—'}</div></div>
    <div class="trade-level"><div class="tl-label">Stop Loss</div><div class="tl-value stop">${a.stop_loss   || '—'}</div></div>
    <div class="trade-level"><div class="tl-label">Take Profit${_tp2 ? ' 1' : ''}</div><div class="tl-value target">${a.target_price || '—'}</div></div>
    ${_tp2 ? `<div class="trade-level"><div class="tl-label">Take Profit 2</div><div class="tl-value target">${_tp2}</div></div>` : ''}
    <div class="trade-level"><div class="tl-label">Risk : Reward</div><div class="tl-value rr${a._rr_weak ? ' weak' : ''}">${a.risk_reward  || '—'}${a._rr_weak ? ' ⚠' : ''}</div></div>`;

  // ── Strategy grid ──
  document.getElementById('strategyGrid').innerHTML = [
    { l: 'Entry Strategy',   t: a.entry_strategy          },
    { l: 'Position Sizing',  t: a.position_sizing         },
    { l: 'Stop Loss Logic',  t: a.stop_loss_logic         },
    { l: 'Profit Taking',    t: a.profit_taking_logic     },
    { l: 'Hedging',          t: a.hedging_considerations  },
  ].filter(x => x.t).map(x => `
    <div class="strategy-item">
      <div class="strategy-label">${x.l}</div>
      <div class="strategy-text">${x.t}</div>
    </div>`).join('');

  setText('tradeTf', `${tradeStyle().label} trade${a.timeframe ? ` · suggested holding period: ${a.timeframe}` : ''}`);

  // ── Key reasons ──
  document.getElementById('keyReasonsList').innerHTML =
    (a.key_reasons || []).map(r => `<li>${r}</li>`).join('');

  // ── Invalidation ──
  document.getElementById('invalidationList').innerHTML =
    (a.invalidation_conditions || []).map(c => `<li>${c}</li>`).join('');
  setText('changeViewText', a.what_would_change_view || '');

  // ── Specialist agent breakdown card ──
  const specCard = document.getElementById('specialistCard');
  if (specCard && a._specialists) {
    const s = a._specialists;
    specCard.style.display = '';
    specCard.querySelector('#specGrid').innerHTML = [
      { icon: '📈', label: 'Technical Analyst',    text: s.technical   },
      { icon: '📊', label: 'Fundamental Analyst',  text: s.fundamental },
      { icon: '🌐', label: 'Macro Strategist',     text: s.macro       },
      { icon: '⚠️', label: 'Risk Manager',         text: s.risk        },
    ].map(ag => `
      <div class="spec-panel">
        <div class="spec-label">${ag.icon} ${ag.label}</div>
        <div class="spec-text">${ag.text || '—'}</div>
      </div>`).join('');
    const disagreement = a.specialist_disagreements;
    const discEl = specCard.querySelector('#specDisagreement');
    if (discEl) discEl.textContent = disagreement ? `Committee note: ${disagreement}` : '';
  } else if (specCard) {
    specCard.style.display = 'none';
  }

  // ── Historical Edge card ──
  const heCard = document.getElementById('historicalEdgeCard');
  if (heCard) {
    if (historicalScan && historicalScan.count >= 3) {
      const win5  = Number(historicalScan.win5d);
      const win20 = Number(historicalScan.win20d);
      const edgeClass = win20 >= 65 ? 'edge-bullish' : win20 <= 35 ? 'edge-bearish' : 'edge-neutral';
      heCard.style.display = '';
      heCard.querySelector('#heScanCount').textContent = `${historicalScan.count} similar setups found in 210-day history`;
      heCard.querySelector('#heStats').innerHTML = `
        <div class="he-stat"><span class="he-label">Avg 5d return</span><span class="he-val ${Number(historicalScan.avg5d) >= 0 ? 'pos' : 'neg'}">${historicalScan.avg5d > 0 ? '+' : ''}${historicalScan.avg5d}%</span></div>
        <div class="he-stat"><span class="he-label">Avg 10d return</span><span class="he-val ${Number(historicalScan.avg10d) >= 0 ? 'pos' : 'neg'}">${historicalScan.avg10d > 0 ? '+' : ''}${historicalScan.avg10d}%</span></div>
        <div class="he-stat"><span class="he-label">Avg 20d return</span><span class="he-val ${Number(historicalScan.avg20d) >= 0 ? 'pos' : 'neg'}">${historicalScan.avg20d > 0 ? '+' : ''}${historicalScan.avg20d}%</span></div>
        <div class="he-stat"><span class="he-label">Win rate (5d)</span><span class="he-val ${edgeClass}">${historicalScan.win5d}%</span></div>
        <div class="he-stat"><span class="he-label">Win rate (20d)</span><span class="he-val ${edgeClass}">${historicalScan.win20d}%</span></div>
        <div class="he-stat"><span class="he-label">20d range</span><span class="he-val">${historicalScan.worst20d}% to +${historicalScan.best20d}%</span></div>`;
    } else {
      heCard.style.display = 'none';
    }
  }

  // ── Price Targets card ──
  const ptCard = document.getElementById('priceTargetsCard');
  if (ptCard) {
    const dp2 = type === 'Forex' ? 5 : 2;
    const curr2 = candles[candles.length - 1]?.close;
    const pct = (p, base) => base ? `(${((p - base) / base * 100).toFixed(1)}%)` : '';
    let rows = '';
    if (a.target_price) rows += `<div class="pt-row"><span class="pt-label">AI Primary Target</span><span class="pt-val target">${a.target_price} <small>${pct(parseFloat(a.target_price), curr2)}</small></span></div>`;
    if (a.stop_loss)    rows += `<div class="pt-row"><span class="pt-label">Stop Loss</span><span class="pt-val stop">${a.stop_loss} <small>${pct(parseFloat(a.stop_loss), curr2)}</small></span></div>`;
    const sc2 = a.scenarios || {};
    if (sc2.bull?.target)    rows += `<div class="pt-row"><span class="pt-label">Bull scenario</span><span class="pt-val pos">${sc2.bull.target} <small>${sc2.bull.upside || ''}</small></span></div>`;
    if (sc2.base?.target)    rows += `<div class="pt-row"><span class="pt-label">Base scenario</span><span class="pt-val">${sc2.base.target} <small>${sc2.base.change || ''}</small></span></div>`;
    if (sc2.bear?.target)    rows += `<div class="pt-row"><span class="pt-label">Bear scenario</span><span class="pt-val neg">${sc2.bear.target} <small>${sc2.bear.downside || ''}</small></span></div>`;
    if (quote?.targetMeanPrice) rows += `<div class="pt-row"><span class="pt-label">Analyst consensus</span><span class="pt-val">${'$' + fmtNum(quote.targetMeanPrice)} <small>${pct(quote.targetMeanPrice, curr2)}</small></span></div>`;
    if (fibExt) {
      const dir = fibExt.direction === 'upside' ? '↑' : '↓';
      rows += `<div class="pt-row pt-fib-header"><span class="pt-label">Fib Extensions (${dir})</span><span class="pt-val"></span></div>`;
      rows += `<div class="pt-row"><span class="pt-label">  127.2%</span><span class="pt-val">${fibExt.e1272}</span></div>`;
      rows += `<div class="pt-row"><span class="pt-label">  161.8%</span><span class="pt-val">${fibExt.e1618}</span></div>`;
      rows += `<div class="pt-row"><span class="pt-label">  200%</span><span class="pt-val">${fibExt.e2000}</span></div>`;
      rows += `<div class="pt-row"><span class="pt-label">  261.8%</span><span class="pt-val">${fibExt.e2618}</span></div>`;
    }
    ptCard.querySelector('#ptRows').innerHTML = rows || '<p style="color:var(--text3)">No price target data available.</p>';
    ptCard.style.display = '';
  }

  // ── News impact enrichment ──
  const newsGrid = document.getElementById('newsGrid');
  if (newsGrid) {
    const impactMap = {};
    (newsImpact || []).forEach(n => { if (n.title) impactMap[n.title.slice(0, 50)] = n; });
    newsGrid.innerHTML = news.slice(0, 6).map(n => {
      const key = (n.title || '').slice(0, 50);
      const imp = impactMap[key];
      const impHtml = imp ? `<div class="news-impact">${
        imp.impact1d != null ? `<span class="${imp.impact1d >= 0 ? 'imp-pos' : 'imp-neg'}">1d: ${imp.impact1d >= 0 ? '+' : ''}${imp.impact1d}%</span>` : ''
      }${
        imp.impact5d != null ? `<span class="${imp.impact5d >= 0 ? 'imp-pos' : 'imp-neg'}">5d: ${imp.impact5d >= 0 ? '+' : ''}${imp.impact5d}%</span>` : ''
      }</div>` : '';
      return `<a class="news-item" href="${n.link || '#'}" target="_blank" rel="noopener noreferrer">
        <div class="news-title">${n.title || ''}</div>
        <div class="news-meta">${n.source || ''} · ${n.date ? new Date(n.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''}</div>
        ${impHtml}
      </a>`;
    }).join('');
  }

  // ── Prior memory badge ──
  const memBadge = document.getElementById('memoryBadge');
  if (memBadge) {
    const prev = Array.isArray(tickerMemory) && tickerMemory.length ? tickerMemory[0] : null;
    if (prev) {
      const priceNow  = candles[candles.length - 1]?.close;
      const priceThen = prev.price;
      const chgSince  = priceThen ? ((priceNow - priceThen) / priceThen * 100).toFixed(1) : null;
      const outcomeIcon = prev.outcome === 'tp_hit' ? '✅' : prev.outcome === 'sl_hit' ? '❌' : prev.outcome === 'expired' ? '⏱' : '⏳';
      memBadge.style.display = '';
      memBadge.innerHTML = `<span class="mem-icon">🧠</span> Last analysed <strong>${prev.analysis_date}</strong> at $${prev.price} — <strong>${prev.verdict}</strong> (${prev.confidence}% conf) ${outcomeIcon}${chgSince != null ? ` · Price ${Number(chgSince) >= 0 ? '+' : ''}${chgSince}% since` : ''} · ${tickerMemory.length} prior ${tickerMemory.length === 1 ? 'analysis' : 'analyses'} in memory`;
    } else {
      memBadge.style.display = 'none';
    }
  }

  showSection('resultsSection');
  // Render the engine card from the data already fetched for the verdict (no re-fetch).
  // Fallback to a fresh fetch only if it wasn't passed (e.g. a direct renderResults call).
  if (engineData !== undefined) renderEngineCard(sym, engineData);
  else loadEngineInsights(sym, type);
  setTimeout(() => document.getElementById('resultsSection').scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
}

// ── Market session & hours awareness ──────────────────────────────────────────
// Which trading session is active for this instrument, whether the market is open,
// and research-grounded best-practices per session — fed to the committee so it
// tailors the read to liquidity/volatility, and FLAGS stale data when the market is
// closed (weekend/holiday) so it won't treat a stale price as actionable.
// All times UTC. Sessions: Sydney/Tokyo (Asian), London (07–16), New York (12–21),
// London–NY overlap (12–16) = peak. US equities regular 14:30–21:00.
const US_MARKET_HOLIDAYS = new Set([   // NYSE 2026 (also covers the FX "holiday" cases)
  '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25',
  '2026-06-19', '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25',
]);
function marketSession(type) {
  const now = new Date();
  const day = now.getUTCDay();                       // 0=Sun … 6=Sat
  const h = now.getUTCHours() + now.getUTCMinutes() / 60;
  const dateStr = now.toISOString().slice(0, 10);
  const weekend = (day === 6 || day === 0);
  const t = (type || '').toLowerCase();

  if (t.includes('crypto')) {
    const peak = (h >= 13 && h < 18);                // ~16:00 UTC global peak
    const sess = (h >= 7 && h < 12) ? 'London (EU) hours' : (h >= 12 && h < 21) ? 'US hours' : (h < 9) ? 'Asian hours' : 'off-peak hours';
    return {
      open: true, closed: false, stale: false,
      session: sess + (peak ? ' · peak ~16:00 UTC' : ''),
      liquidity: weekend ? 'thin (weekend)' : peak ? 'high' : 'normal',
      guidance: `Crypto trades 24/7. ${peak ? 'You are in the peak-volatility window (London PM / NY AM, ~16:00 UTC): cleanest moves, breakouts/trend trades favoured.' : 'Off the peak window — moves are often choppier.'}${weekend ? ' WEEKEND: spot volume drops ~20–40% and the order book is thin, so expect wider spreads and sharp, low-conviction moves from small orders (worse for alts). Treat breakouts sceptically, size down, and prefer waiting for the weekday open to confirm.' : ''}`,
    };
  }

  if (t.includes('forex')) {
    const closed = (day === 6) || (day === 0 && h < 22) || (day === 5 && h >= 22);   // Fri 22:00 → Sun 22:00 UTC
    if (closed) return { open: false, closed: true, stale: true, session: 'CLOSED — weekend', liquidity: 'closed',
      guidance: 'The FX market is CLOSED for the weekend. The latest price is Friday\'s close and any intraday signal is STALE — do not treat live levels as actionable. Beware a weekend GAP on the Sunday/Monday re-open (weekend news can gap straight through stops). Use this only to PLAN; do not enter.' };
    const overlap = (h >= 12 && h < 16), london = (h >= 7 && h < 16), ny = (h >= 12 && h < 21);
    if (overlap) return { open: true, closed: false, stale: false, session: 'London–New York OVERLAP', liquidity: 'peak',
      guidance: 'Peak liquidity & volatility of the day — cleanest directional moves and tightest spreads. Best window for breakout / trend trades; big moves and reversals happen fast, so size stops for the volatility.' };
    if (london) return { open: true, closed: false, stale: false, session: 'London', liquidity: 'high',
      guidance: 'The most active session (~38% of FX turnover): decisive directional moves, tight spreads. The London open (~07:00–10:00 UTC) typically breaks the Asian range and sets the daily bias — favour breakout / trend-continuation over fading.' };
    if (ny) return { open: true, closed: false, stale: false, session: 'New York (post-overlap)', liquidity: 'moderate',
      guidance: 'After the London close (~16:00 UTC) liquidity thins and trends can stall or reverse — be wary of chasing late-day breakouts; lean toward taking profit over initiating fresh trend trades.' };
    return { open: true, closed: false, stale: false, session: 'Asian (Tokyo/Sydney)', liquidity: 'low',
      guidance: 'Lowest-volatility session — EUR/USD typically ranges only ~30–40% of its daily range. It tends to build a RANGE, not trend: favour range / mean-reversion off the extremes and AVOID breakout trades on EUR/GBP majors (false breaks are common). JPY / AUD / NZD pairs are the most active now.' };
  }

  // Stock / ETF — US regular hours 14:30–21:00 UTC, Mon–Fri, ex-holidays.
  const holiday = US_MARKET_HOLIDAYS.has(dateStr);
  if (weekend || holiday) return { open: false, closed: true, stale: true, session: holiday ? 'CLOSED — US holiday' : 'CLOSED — weekend', liquidity: 'closed',
    guidance: `The US market is CLOSED${holiday ? ' for a holiday' : ' for the weekend'}. The latest price is the last session's close and intraday reads are STALE — don't treat live levels as actionable. Beware a GAP on the re-open from weekend/overnight news. Use this to PLAN, not to enter.` };
  if (h < 14.5 || h >= 21) return { open: false, closed: false, stale: true, session: h < 14.5 ? 'Pre-market' : 'After-hours', liquidity: 'thin',
    guidance: 'Outside US regular hours (14:30–21:00 UTC). Pre/after-hours is THIN and volatile — wide spreads, unreliable prints, and moves that often don\'t hold into the regular session. Treat the last regular close as the reference and extended-hours levels with caution.' };
  if (h < 15.5) return { open: true, closed: false, stale: false, session: 'US open (first hour)', liquidity: 'high',
    guidance: 'The opening hour is the most volatile of the day — biggest, fastest moves and the cleanest directional setups, but also the most whipsaw. Let the first few minutes settle and size stops for the volatility.' };
  if (h >= 16.5 && h < 19) return { open: true, closed: false, stale: false, session: 'Midday lull', liquidity: 'low',
    guidance: 'The midday lull (~11:30–14:00 ET) is the quietest, choppiest part of the day — the worst window for new directional trades and breakouts often fail here. Favour patience / range behaviour.' };
  if (h >= 20) return { open: true, closed: false, stale: false, session: 'Power hour (close)', liquidity: 'high',
    guidance: 'The final hour is the second most active — institutions execute remaining orders so volume rises, but reversals are fast. Good for momentum; manage risk into the close.' };
  return { open: true, closed: false, stale: false, session: 'US mid-session', liquidity: 'moderate',
    guidance: 'Regular hours, moderate liquidity between the open and the afternoon — normal conditions.' };
}

// ── Main research flow ────────────────────────────────────────────────────────

async function startResearch() {
  const raw = document.getElementById('symInput').value.trim();
  if (!raw) { document.getElementById('symInput').focus(); return; }

  const sym = raw.toUpperCase(), type = detectType(sym);

  // Re-scan cooldown: block a too-soon re-run of the SAME symbol unless forced. A
  // DELIBERATE re-check / update (from History) always bypasses it — the cooldown is
  // only there to stop accidental rapid re-scans, not an intentional re-validation.
  const _forced = startResearch._force === sym || _validateMode || _updateMode;
  startResearch._force = null;
  if (!_forced) {
    const cdMs = cooldownRemainingMs(sym);
    if (cdMs > 0) { showCooldownNotice(sym, cdMs); return; }
  }

  document.getElementById('analyseBtn').disabled = true;
  document.getElementById('loaderSym').textContent = sym;
  showSection('loadingSection');
  for (let i = 1; i <= 5; i++) document.getElementById(`ls${i}`).className = 'loader-step';

  // Update loader labels
  [
    'Fetching multi-timeframe price history',
    'Computing 15+ technical indicators',
    'Pulling fundamentals, news, macro & memory',
    'Running 4 specialist agents + debate round…',
    'Investment committee synthesising final verdict',
  ].forEach((t, i) => { const el = document.getElementById(`ls${i + 1}`); if (el) el.textContent = t; });

  setStep(1);

  // A validity re-check MUST run in the SAME style as the original trade — an intraday
  // trade is re-checked as intraday, never swing. Force it from the trade's own
  // setup_features regardless of the UI pill, so an accidental style change (or a stale
  // default) can't mismatch the re-check to a trade of a different timeframe.
  if (_validateMode && _validateTarget && _validateTarget.symbol === sym) {
    let _f = _validateTarget.setup_features;
    if (typeof _f === 'string') { try { _f = JSON.parse(_f); } catch { _f = null; } }
    const _k = _f && _f.style ? String(_f.style).toLowerCase() : null;
    if (_k && TRADE_STYLES[_k]) {
      _tradeStyle = _k;
      const _pill = document.querySelector(`#tradeStylePills .ts-pill[data-style="${_k}"]`);
      if (_pill) { document.querySelectorAll('#tradeStylePills .ts-pill').forEach(b => b.classList.remove('active')); _pill.classList.add('active'); }
    }
  }

  const ts = tradeStyle();   // selected timeframe drives the whole analysis
  const styleMinRR = minRRForStyle(_tradeStyle);   // professional reward:risk floor for this style
  try {
    // ── Step 1: trade-timeframe + higher-timeframe context candles in parallel ──
    const [candles, weeklyCandles, benchCandles, entryCandles] = await Promise.all([
      fetchCandles(sym, type, ts.primaryTf, ts.primaryDays),
      fetchWeeklyCandles(sym, type, ts.contextTf, ts.contextDays),
      type !== 'Forex' ? fetchCandles(type === 'Crypto' ? 'BTC/USD' : 'SPY', type === 'Crypto' ? 'Crypto' : 'ETF', ts.primaryTf, ts.primaryDays).catch(() => null) : Promise.resolve(null),
      // The LOWER entry-trigger timeframe (e.g. 4h for a swing) so the committee can
      // actually CONFIRM the entry on it, not just infer it. Graceful: null if shallow.
      fetchCandles(sym, type, ts.entryTf, ts.entryDays).catch(() => null),
    ]);
    if (!candles || candles.length < 30) throw new Error(`No ${ts.primaryTf} price data found for "${sym}". Try a longer trade style or a different symbol.`);
    setStep(2);

    // ── Step 2: Compute all indicators ──
    const closes  = candles.map(c => c.close);
    const curr    = closes[closes.length - 1];
    const dp      = type === 'Forex' ? 5 : 4;
    const rsi     = calcRSI(closes);
    const macd    = calcMACD(closes);
    const sma20   = calcSMA(closes, 20);
    const sma50   = calcSMA(closes, 50);
    const sma200  = calcSMA(closes, 200);
    const bb      = calcBollingerBands(closes);
    const stochRsi= calcStochRSI(closes);
    const obv     = calcOBVTrend(candles);
    const fib     = calcFibLevels(candles);
    const { supports, resistances } = findPivotSR(candles);
    const atr     = calcATR(candles);
    const volTrnd = calcVolTrend(candles);
    const trend   = getTrend(closes, sma20, sma50);
    const chg1d   = ((curr - closes[closes.length - 2]) / closes[closes.length - 2] * 100).toFixed(2);
    const chg7d   = closes.length > 7  ? ((curr - closes[closes.length - 8])  / closes[closes.length - 8]  * 100).toFixed(2) : null;
    const chg30d  = closes.length > 30 ? ((curr - closes[closes.length - 31]) / closes[closes.length - 31] * 100).toFixed(2) : null;

    // Weekly indicators
    const wCloses = weeklyCandles?.map(c => c.close);
    const wRSI    = wCloses ? calcRSI(wCloses)     : null;
    const wSMA20  = wCloses ? calcSMA(wCloses, 20)  : null;
    const wSMA50  = wCloses ? calcSMA(wCloses, 50)  : null;
    const wMACD   = wCloses ? calcMACD(wCloses)      : null;
    const wCurr   = wCloses?.[wCloses.length - 1];
    const wTrend  = wCloses && wCurr && wSMA20 ? (wCurr > wSMA20 ? 'bullish' : 'bearish') : null;

    // ── Entry-timeframe read — the LOWER TF (e.g. 4h for a swing), so the committee
    // can CONFIRM the precise entry on it instead of inferring. Graceful: null if the
    // lower-TF data is too shallow (intraday history is short on the data source).
    let entryTfRead = null;
    if (Array.isArray(entryCandles) && entryCandles.length >= 30) {
      const eC = entryCandles.map(c => c.close);
      const eClose = eC[eC.length - 1];
      const eLast = entryCandles[entryCandles.length - 1];
      const eSMA20 = calcSMA(eC, 20), eSMA50 = calcSMA(eC, 50);
      const recent = entryCandles.slice(-40);
      entryTfRead = {
        tf: ts.entryTf,
        close: eClose,
        rsi: calcRSI(eC),
        macdUp: (calcMACD(eC) ?? 0) > 0,
        trend: (eSMA20 && eSMA50) ? (eClose > eSMA20 && eSMA20 > eSMA50 ? 'up' : eClose < eSMA20 && eSMA20 < eSMA50 ? 'down' : 'mixed') : 'mixed',
        high: Math.max(...recent.map(c => c.high)),
        low: Math.min(...recent.map(c => c.low)),
        vol: calcVolTrend(entryCandles),
        candle: eLast.close > eLast.open ? 'bullish' : eLast.close < eLast.open ? 'bearish' : 'doji',
      };
    }

    const adx       = calcADX(candles);
    const bbWidth   = calcBBWidthPct(closes);
    const volProfile= calcVolumeProfile(candles);
    const relStr    = sym !== 'SPY' && sym !== 'BTC/USD' ? calcRelStrength(closes, benchCandles?.map(c => c.close)) : null;
    const benchName = type === 'Crypto' ? 'BTC' : 'SPY';

    // ── Confluence Score — weighted multi-timeframe signal alignment ──────────
    const confluenceScore = calcConfluenceScore({
      curr, sma20, sma50, sma200,
      wCurr, wSMA20, wSMA50,
      rsi, wRSI, macd, wMACD,
      volTrnd, adx, stochRsi,
    });

    setStep(3);

    // Reuse the pre-analysis events/sector if they were already fetched for this
    // symbol on selection; otherwise fetch fresh (edge-cached, so cheap).
    const _evPromise  = (_preData.sym === sym && _preData.events !== null)
      ? Promise.resolve(_preData.events)
      : fetch(`/api/events?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}`).then(r => r.ok ? r.json() : null).catch(() => null);
    const _secPromise = (_preData.sym === sym && _preData.sector !== null)
      ? Promise.resolve(_preData.sector)
      : fetch(`/api/sector?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}`).then(r => r.ok ? r.json() : null).catch(() => null);

    // ── Step 3: News, fundamentals, macro context + all new signals in parallel ──
    const [news, quote, macroCtx, supaMemory, fearGreed, macroIntermarket, qualityScores, backtestKB, strategyBT, eventsData, sectorData, positioning, seasonality, calibration, cryptoDerivs] = await Promise.all([
      fetchNews(sym, type),
      ['Stock', 'ETF'].includes(type) ? fetchQuote(sym, type) : Promise.resolve(null),
      fetchMacroContext(sym),
      fetchTickerMemory(sym),
      fetchFearGreed(),
      fetchMacroIntermarket(),
      type === 'Stock' ? fetchQualityScores(sym) : Promise.resolve(null),
      fetchBacktestKB(sym),
      fetchStrategyBacktests(sym),
      _evPromise,
      _secPromise,
      fetchPositioning(sym, type),
      fetchSeasonality(sym, type),
      fetchCalibration(type),
      fetchCryptoDerivs(sym, type),
    ]);

    // Resolve outcomes of pending analyses now that we have fresh candles
    const pendingRows = (supaMemory || []).filter(r => r.outcome === 'pending');
    if (pendingRows.length) resolveOutcomes(pendingRows, candles, ts.primaryTf);
    const tickerMemory = supaMemory; // alias for readability below

    // Fire the quant-engine fetch now so it overlaps the (slow) LLM agent calls.
    // We await it just before committee synthesis so the final verdict + confidence
    // account for the engine's regime / risk-veto / validation. Graceful if offline.
    const enginePromise = fetchEngineData(sym);

    setStep(4);

    // ── Build prompt ──────────────────────────────────────────────────────────

    // Run historical scan + news impact + extensions (all client-side, no extra API calls)
    const historicalScan = runHistoricalScan(candles);
    const newsImpact     = analyzeNewsImpact(news, candles);
    const fibExt         = calcFibExtensions(candles);

    // Raw daily OHLCV (last 20 bars — enough for pattern recognition, keeps tokens low)
    const ohlcvTable = candles.slice(-20).map(b =>
      `${fmtDate(b.time)} O:${b.open.toFixed(dp)} H:${b.high.toFixed(dp)} L:${b.low.toFixed(dp)} C:${b.close.toFixed(dp)} V:${fmtVol(b.volume)}`
    ).join('\n');

    // Weekly OHLCV (last 8 weeks)
    const weeklyTable = weeklyCandles
      ? weeklyCandles.slice(-8).map(b =>
          `${fmtDate(b.time)} O:${b.open.toFixed(dp)} H:${b.high.toFixed(dp)} L:${b.low.toFixed(dp)} C:${b.close.toFixed(dp)} V:${fmtVol(b.volume)}`
        ).join('\n')
      : 'Weekly data unavailable.';

    // Pivot S/R
    const srText = [
      `Resistance: ${resistances.length ? resistances.map(r => r.toFixed(dp)).join(' | ') : 'none identified'}`,
      `Support:    ${supports.length    ? supports.map(s => s.toFixed(dp)).join(' | ')    : 'none identified'}`,
    ].join('\n');

    // Fibonacci retracements + extensions
    const fibText = `Retracements — Range ${fib.low.toFixed(dp)}–${fib.high.toFixed(dp)} | 23.6%:${fib.f236} | 38.2%:${fib.f382} | 50%:${fib.f500} | 61.8%:${fib.f618} | 78.6%:${fib.f786}`;
    const fibExtText = fibExt
      ? `Extensions (${fibExt.direction}) — 127.2%: ${fibExt.e1272} | 161.8%: ${fibExt.e1618} | 200%: ${fibExt.e2000} | 261.8%: ${fibExt.e2618}`
      : '';

    // Historical scan block
    const scanBlock = historicalScan
      ? `\n━━━ HISTORICAL SETUP SCAN (${historicalScan.count} similar RSI+trend+MACD setups in last 210d) ━━━
Avg forward returns: 5d: ${historicalScan.avg5d}% | 10d: ${historicalScan.avg10d}% | 20d: ${historicalScan.avg20d}%
Win rate: 5d: ${historicalScan.win5d}% | 20d: ${historicalScan.win20d}% | Best 20d: +${historicalScan.best20d}% | Worst: ${historicalScan.worst20d}%`
      : '';

    // ── Position-management block (validate mode only) ─────────────────────────
    // When re-checking an OPEN trade the AI must give position management verdicts
    // (HOLD_TRADE, CLOSE_TRADE, etc.) NOT fresh entry verdicts (WAIT, SHORT, BUY).
    let positionMgmtBlock = '';
    const _isRealTrade = _validateTarget && (() => {
      const u = (_validateTarget.verdict || '').toUpperCase();
      return /BUY|SELL|SHORT|LONG/.test(u);
    })();
    
    // Helper functions to check if trade was filled (since entryBounds and rowTs don't exist in dashboard.js yet)
    const getEntryBounds = (ez) => {
      const m = String(ez || '').match(/-?\d+(?:\.\d+)?/g);
      if (!m) return null;
      const v = m.map(Number).filter(x => !isNaN(x));
      return v.length ? { lo: Math.min(...v), hi: Math.max(...v) } : null;
    };
    const getRowTs = (row) => {
      if (row.created_at) { const t = Date.parse(row.created_at); if (!isNaN(t)) return t; }
      const m = String(row.id || '').match(/_(\d{10,})$/);
      if (m) return parseInt(m[1], 10);
      if (row.analysis_date) { const t = Date.parse(row.analysis_date); if (!isNaN(t)) return t; }
      return 0;
    };

    const _tradeFilled = _isRealTrade && (() => {
      const eb = getEntryBounds(_validateTarget.entry_zone);
      if (!eb) return true; // market entry (no zone) -> always filled
      const scanPx = parseFloat(_validateTarget.price);
      const atMarket = !isNaN(scanPx) && scanPx >= eb.lo - Math.abs(eb.lo) * 0.0005 && scanPx <= eb.hi + Math.abs(eb.hi) * 0.0005;
      if (atMarket) return true; // filled at market instantly
      
      let _f = _validateTarget.setup_features;
      if (typeof _f === 'string') { try { _f = JSON.parse(_f); } catch { _f = null; } }
      const _style = (_f && _f.style) ? String(_f.style).toLowerCase() : 'swing';
      const res = STYLE_RES[_style] || STYLE_RES.swing;
      const tfSec = TF_SECONDS[res.tf] || 86400;

      const entryTs = getRowTs(_validateTarget) / 1000;
      // Filter candles that occurred during or after the scan time
      const afterEntry = (res.tf === '1d' || res.tf === '1w')
        ? candles.filter(c => c.time >= entryTs)
        : candles.filter(c => c.time + tfSec > entryTs);
      for (const bar of afterEntry) {
        if (bar.low <= eb.hi && bar.high >= eb.lo) {
          return true; // Price traded into the entry zone!
        }
      }
      return false;
    })();

    let waitConstraint = '';
    let pmPrompt = '';
    let pmVerdictJson = 'STRONG_BUY|BUY|SPECULATIVE_BUY|WAIT|HOLD|REDUCE_EXPOSURE|AVOID|SHORT|SPECULATIVE_SHORT|HEDGE|NO_EDGE';

    const _pmTarget = (_validateMode && _validateTarget && _validateTarget.symbol === sym && _isRealTrade) ? _validateTarget : null;
    if (_pmTarget) {
      const origDir   = verdictDir(_pmTarget.verdict);
      const origVerd  = (_pmTarget.verdict || '').replace(/_/g, ' ');
      const entryZone = _pmTarget.entry_zone || 'N/A';
      const stopLoss  = _pmTarget.stop_loss  || 'N/A';
      const target    = _pmTarget.target_price || 'N/A';
      const origConf  = _pmTarget.confidence != null ? _pmTarget.confidence + '%' : 'N/A';
      const origDate  = (_pmTarget.analysis_date || '').slice(0, 10);
      const priceThen = _pmTarget.price != null ? (+_pmTarget.price).toFixed(dp) : 'N/A';
      // Progress toward TP/SL
      const prog = validationProgress(_pmTarget, curr);
      const progLine = prog ? `Price is now ${prog.pct}% of the way toward the ${prog.toward}.` : '';

      const ageDays = (Date.now() - getRowTs(_pmTarget)) / 86400000;
      const elapsedDays = Math.floor(ageDays);
      let _f = _pmTarget.setup_features;
      if (typeof _f === 'string') { try { _f = JSON.parse(_f); } catch { _f = null; } }
      const styleName = (_f && _f.style) ? String(_f.style).toUpperCase() : 'SWING';
      const expiryThreshold = (_f && _f.expiryDays) ? Number(_f.expiryDays) : 30;

      const durationBlock = `
━━━ DURATION & STAGNANCIES ANALYSIS ━━━
This trade setup has been open/pending for ${elapsedDays} days.
Trade Style: ${styleName} (max expiry is ${expiryThreshold} days).
Guidelines on stagnant setups:
- Trend-following momentum setup edges decay significantly over time if the market goes sideways.
- Stagnant capital represents opportunity cost.
- If it has been unfilled for more than 5 days (for SCALP/INTRADAY) or 15 days (for SWING/POSITION), strongly consider recommending CLOSE_TRADE to cancel the watch.
- If it has been filled/active but sideways for too long, strongly consider CLOSE_TRADE or TIGHTEN_STOP.
Consider if time-decay has invalidated the setup's original edge, and recommend closing early if there is no momentum.`;

      waitConstraint = _tradeFilled
        ? ' (but in position re-check mode, WAIT is invalid — use HOLD_TRADE, MOVE_TO_BREAKEVEN, or CLOSE_TRADE instead)'
        : ' (but in pending setup re-check mode, WAIT is invalid — use HOLD_TRADE or CLOSE_TRADE instead)';

      pmPrompt = `\n⚠ RE-CHECK MODE: You are managing an existing ${_tradeFilled ? 'ACTIVE' : 'UNFILLED'} ${verdictDir(_pmTarget.verdict).toUpperCase()} trade.\nThe ONLY valid verdicts here are: ${_tradeFilled ? 'HOLD_TRADE | MOVE_TO_BREAKEVEN | TIGHTEN_STOP | SCALE_OUT | CLOSE_TRADE' : 'HOLD_TRADE | CLOSE_TRADE'}.\nAny other verdict is INVALID for this response.\nThe executive_summary and key_reasons must explain the position management decision.`;

      pmVerdictJson = _tradeFilled
        ? 'HOLD_TRADE|MOVE_TO_BREAKEVEN|TIGHTEN_STOP|SCALE_OUT|CLOSE_TRADE'
        : 'HOLD_TRADE|CLOSE_TRADE';
      
      if (_tradeFilled) {
        positionMgmtBlock = `
━━━ OPEN POSITION RE-CHECK — YOU ARE MANAGING AN ACTIVE ${origDir.toUpperCase()} POSITION ━━━
This is NOT a new entry decision. The position has already filled and is live:
  Direction : ${origDir.toUpperCase()} (original verdict: ${origVerd})
  Opened    : ${origDate} @ ${priceThen}
  Entry zone: ${entryZone}
  Take-profit: ${target}
  Stop-loss  : ${stopLoss}
  Confidence then: ${origConf}
  Current price  : ${curr.toFixed(dp)} ${progLine}

YOUR SOLE TASK is to assess what the HOLDER of this active position should do RIGHT NOW.
The valid verdicts for an active position re-check are ONLY:
  HOLD_TRADE        — thesis intact, stay in position
  MOVE_TO_BREAKEVEN — risk is rising; move stop to entry to protect capital
  TIGHTEN_STOP      — significant profit banked; trail the stop to lock in gains
  SCALE_OUT         — partial take-profit now; let a runner go for the rest
  CLOSE_TRADE       — thesis is broken or risk clearly outweighs reward; exit entirely

Decide based on: (a) how much the original thesis still holds on fresh evidence,
(b) how far price is toward TP vs SL, (c) any new signals that change the picture.
Be direct and specific. The holder needs a clear action, not another WAIT.
${durationBlock}`;
      } else {
        positionMgmtBlock = `
━━━ PENDING ENTRY RE-CHECK — YOU ARE EVALUATING AN UNFILLED ${origDir.toUpperCase()} SETUP ━━━
This trade has NOT filled yet. Price is sitting outside the entry zone:
  Direction : ${origDir.toUpperCase()} (original verdict: ${origVerd})
  Scanned   : ${origDate} @ ${priceThen}
  Entry zone: ${entryZone}
  Take-profit: ${target}
  Stop-loss  : ${stopLoss}
  Confidence then: ${origConf}
  Current price  : ${curr.toFixed(dp)}

YOUR SOLE TASK is to assess whether we should continue waiting for the entry zone to trigger, or cancel the setup.
The valid verdicts for an unfilled setup re-check are ONLY:
  HOLD_TRADE  — thesis still valid, continue waiting for the entry zone to trigger
  CLOSE_TRADE — setup is invalidated or trend has shifted; cancel the watch setup

Do NOT issue fresh entry level changes. Do NOT recommend active management actions (MOVE_TO_BREAKEVEN, TIGHTEN_STOP, SCALE_OUT) because the trade is not yet filled.
Decide based on whether the original setup pattern still holds, or if the market has broken the thesis before entry.
${durationBlock}`;
      }
    }

    // News impact block
    const impactBlock = newsImpact.length
      ? `\n━━━ NEWS → PRICE IMPACT (historical) ━━━\n` + newsImpact.slice(0, 8).map(n =>
          `• [${n.date}] "${n.title}" → 1d: ${n.impact1d != null ? (n.impact1d > 0 ? '+' : '') + n.impact1d + '%' : 'N/A'} | 5d: ${n.impact5d != null ? (n.impact5d > 0 ? '+' : '') + n.impact5d + '%' : 'N/A'}`
        ).join('\n')
      : '';

    // Memory block (prior analyses for this ticker — from Supabase)
    const memoryBlock = Array.isArray(tickerMemory) && tickerMemory.length
      ? `\n━━━ PRIOR ANALYSIS MEMORY (${sym}) ━━━\n` + tickerMemory.slice(0, 5).map(h => {
          const outcomeStr = h.outcome === 'tp_hit' ? '✅ TP HIT'
            : h.outcome === 'sl_hit'  ? '❌ SL HIT'
            : h.outcome === 'expired' ? '⏱ EXPIRED (neither TP nor SL hit)'
            : h.outcome === 'invalidated' ? '❌ CLOSED EARLY (AI re-check)'
            : '⏳ PENDING';
          return `• ${h.analysis_date}: $${h.price} → ${h.verdict} (${h.confidence}% conf) | Target: ${h.target_price || 'N/A'} | Stop: ${h.stop_loss || 'N/A'} | Outcome: ${outcomeStr}`;
        }).join('\n')
      : '';

    // Track-record block — the REALISED win/loss record of prior calls on this
    // symbol, so the committee calibrates its confidence against what actually
    // happened (learn from what was won or lost), not just what was predicted.
    let trackRecordBlock = '';
    if (Array.isArray(tickerMemory) && tickerMemory.length) {
      const resolved = tickerMemory.filter(h => h.outcome === 'tp_hit' || h.outcome === 'sl_hit');
      const expired  = tickerMemory.filter(h => h.outcome === 'expired').length;
      const closedEarly = tickerMemory.filter(h => h.outcome === 'invalidated').length;
      if (resolved.length || expired > 0 || closedEarly > 0) {
        const wins   = resolved.filter(h => h.outcome === 'tp_hit').length;
        const losses = resolved.length - wins;
        const winRate = resolved.length ? Math.round((wins / resolved.length) * 100) : 0;
        const byDir = ['long', 'short'].map(dir => {
          const set = resolved.filter(h => verdictDir(h.verdict) === dir);
          if (!set.length) return null;
          const w = set.filter(h => h.outcome === 'tp_hit').length;
          return `${dir.toUpperCase()} ${w}W/${set.length - w}L`;
        }).filter(Boolean).join(' · ');
        const last = resolved.length ? resolved[0] : null;
        trackRecordBlock = `\n━━━ REAL TRACK RECORD (${sym}) ━━━\n`
          + `Resolved prior calls: ${resolved.length} → ${wins} TP-hit / ${losses} SL-hit (${winRate}% win rate) · ${expired} expired · ${closedEarly} closed early by AI.\n`
          + (byDir ? `By direction: ${byDir}.\n` : '')
          + (last ? `Most recent resolved: ${last.analysis_date} ${last.verdict} → ${last.outcome === 'tp_hit' ? '✅ WON' : '❌ LOST'}.\n` : '')
          + `CALIBRATION: This is your ACTUAL realised hit-rate on ${sym}. If recent same-direction calls LOST, be more skeptical and trim confidence; if they WON, a genuine edge may exist. If many calls are EXPIRED or CLOSED EARLY, it indicates this asset tends to consolidate sideways for long periods or is highly volatile with decaying setups — adjust targets closer or avoid entry.`;
      }
    }

    // Anti-anchoring block — guards against re-asserting the SAME unconfirmed thesis
    // with growing conviction. Fires when the most recent runs of calls on this symbol
    // are all the same direction and NONE have resolved (no TP/SL hit) — the textbook
    // confirmation-bias setup (e.g. repeated BUYs that the market hasn't validated).
    let anchorBlock = '';
    if (Array.isArray(tickerMemory) && tickerMemory.length >= 3) {
      const dirs = tickerMemory.map(h => verdictDir(h.verdict));   // tickerMemory is newest-first
      const d0 = dirs[0];
      if (d0 !== 'neutral') {
        let run = 0;
        for (const d of dirs) { if (d === d0) run++; else break; }   // leading same-direction streak
        const runRows = tickerMemory.slice(0, run);
        const anyResolved = runRows.some(h => h.outcome === 'tp_hit' || h.outcome === 'sl_hit');
        if (run >= 3 && !anyResolved) {
          const confs = runRows.map(h => h.confidence).filter(c => c != null);   // newest → oldest
          const rising = confs.length >= 2 && confs[0] >= confs[confs.length - 1];
          anchorBlock = `\n━━━ ⚠ ANCHORING CHECK (${sym}) ━━━\n`
            + `You have issued ${run} consecutive ${d0.toUpperCase()} calls on ${sym}, ALL still unresolved (neither TP nor SL hit)`
            + (rising && confs.length >= 2 ? `, with confidence holding or RISING (${confs[confs.length - 1]}% → ${confs[0]}%)` : '')
            + `.\nThis is a classic anchoring / confirmation-bias pattern: repeating a thesis with growing conviction while the market has produced NO confirming outcome.\n`
            + `DIRECTIVE: Do NOT simply re-assert the prior ${d0.toUpperCase()} call. Either (a) cite genuinely NEW evidence that strengthens it, or (b) trim confidence / consider WAIT. Re-stating an unconfirmed thesis at equal-or-higher confidence demands explicit justification.`;
        }
      }
    }

    // Fundamentals block (stocks only)
    let fundBlock = '';
    if (quote && (type === 'Stock' || type === 'ETF')) {
      const w52pct = (quote.week52High && quote.week52Low)
        ? ((curr - quote.week52Low) / (quote.week52High - quote.week52Low) * 100).toFixed(0) + '% of 52W range'
        : '';
      fundBlock = `
━━━ FUNDAMENTALS ━━━
Market Cap: ${fmtMCap(quote.marketCap)} | Beta: ${fmtNum(quote.beta)}
P/E (TTM): ${quote.pe ? fmtNum(quote.pe, 1) + 'x' : 'N/A'} | Forward P/E: ${quote.forwardPE ? fmtNum(quote.forwardPE, 1) + 'x' : 'N/A'}
EPS: ${quote.eps ? '$' + fmtNum(quote.eps) : 'N/A'} | Div Yield: ${quote.dividendYield ? fmtPct(quote.dividendYield * 100) : 'N/A'}
52W Range: $${fmtNum(quote.week52Low)} – $${fmtNum(quote.week52High)} | Position: ${w52pct}
Revenue Growth (YoY): ${quote.revenueGrowth ? fmtPct(quote.revenueGrowth * 100) : 'N/A'} | Earnings Growth: ${quote.earningsGrowth ? fmtPct(quote.earningsGrowth * 100) : 'N/A'}
Analyst Mean Target: ${quote.targetMeanPrice ? '$' + fmtNum(quote.targetMeanPrice) + ' (' + ((quote.targetMeanPrice - curr) / curr * 100).toFixed(1) + '% from current)' : 'N/A'}
Next Earnings: ${quote.nextEarningsDate || 'N/A'} | Insider Sentiment (3M MSPR): ${quote.insiderSentiment?.mspr != null ? quote.insiderSentiment.mspr.toFixed(3) + (quote.insiderSentiment.mspr > 0 ? ' (net buying)' : quote.insiderSentiment.mspr < 0 ? ' (net selling)' : ' (neutral)') : 'N/A'}`;

      if (quote.analystRecs) {
        const r = quote.analystRecs;
        const tot = (r.strongBuy || 0) + (r.buy || 0) + (r.hold || 0) + (r.sell || 0) + (r.strongSell || 0);
        fundBlock += `\nAnalyst Ratings (${r.period}): ${r.strongBuy} StrongBuy | ${r.buy} Buy | ${r.hold} Hold | ${r.sell} Sell | ${r.strongSell} StrongSell (n=${tot})`;
      }
      if (quote.earningsHistory?.length) {
        fundBlock += `\nEarnings Surprises (last 4Q): ${quote.earningsHistory.map(e =>
          `${e.period}: ${e.surprisePct != null ? (e.surprisePct > 0 ? '+' : '') + e.surprisePct + '%' : 'N/A'}`
        ).join(' | ')}`;
      }
      if (quote?.metrics) {
        const m = quote.metrics;
        const parts = [];
        if (m.fcfPerShareAnnual != null) parts.push(`FCF/Share: $${fmtNum(m.fcfPerShareAnnual)}`);
        if (m.debtEquityAnnual  != null) parts.push(`D/E: ${fmtNum(m.debtEquityAnnual, 2)}x`);
        if (m.evEbitdaAnnual    != null) parts.push(`EV/EBITDA: ${fmtNum(m.evEbitdaAnnual, 1)}x`);
        if (m.psAnnual          != null) parts.push(`P/S: ${fmtNum(m.psAnnual, 1)}x`);
        if (m.roeTTM            != null) parts.push(`ROE: ${fmtPct(m.roeTTM * 100)}`);
        if (m.grossMarginTTM    != null) parts.push(`Gross Margin: ${fmtPct(m.grossMarginTTM * 100)}`);
        if (m.netProfitMarginTTM!= null) parts.push(`Net Margin: ${fmtPct(m.netProfitMarginTTM * 100)}`);
        if (parts.length) fundBlock += `\n${parts.join(' | ')}`;
      }
    }

    const newsText = news.slice(0, 6).map(n =>
      `• ${n.title} (${n.source || 'news'}, ${n.date ? new Date(n.date).toLocaleDateString() : 'recent'})`
    ).join('\n');

    // ── Upcoming events block (earnings + FOMC/CPI hard-flags) ─────────────────
    let eventBlock = '';
    if (eventsData) {
      const e = eventsData.earnings;
      const macroEvts = Array.isArray(eventsData.macro) ? eventsData.macro : [];
      const parts = [];
      if (e && e.daysAway != null) parts.push(`Earnings in ${e.daysAway} day${e.daysAway === 1 ? '' : 's'} (${e.date}).`);
      macroEvts.forEach(m => { if (m.daysAway != null) parts.push(`${m.type} in ${m.daysAway} days (${m.date}).`); });
      if (parts.length) {
        eventBlock = `\n━━━ UPCOMING EVENTS ━━━\n${parts.join(' ')}`;
        if (e && e.daysAway != null && e.daysAway <= 7) {
          eventBlock += `\nEarnings in ${e.daysAway} days. Weight risk analysis heavily — event volatility is elevated; favour wider stops, reduced size, or WAIT if the setup must survive the print.`;
        }
        const nearMacro = macroEvts.filter(m => m.daysAway != null && m.daysAway <= 3).sort((a, b) => a.daysAway - b.daysAway)[0];
        if (nearMacro) {
          eventBlock += `\n${nearMacro.type} is ${nearMacro.daysAway} days away — macro volatility risk is elevated; factor this into stop placement and conviction.`;
        }
      }
    }

    // ── Market session & hours block ───────────────────────────────────────────
    const session = marketSession(type);
    const sessionBlock = `\n━━━ MARKET SESSION & HOURS ━━━\nRight now: ${session.session} · liquidity ${session.liquidity}${session.open ? ' · market OPEN' : session.closed ? ' · market CLOSED' : ' · outside regular hours'}.\n${session.guidance}\nDIRECTIVE: factor the session into the read — match the strategy to the liquidity/volatility regime above, and lower confidence for moves made in thin conditions.${(session.closed || session.stale) ? ' CRITICAL: the market is closed / data is STALE, so the latest price and intraday signals are NOT reliable for a live decision — do NOT issue an actionable BUY/SELL to enter NOW; frame any setup as a plan for the next open and explicitly warn about gap risk. Lean toward WAIT.' : ''}`;

    // ── Data-feed freshness / delay check ──────────────────────────────────────
    // Empirically measure how old the latest bar is. When the market is OPEN but the
    // latest bar lags real time, the free Yahoo feed is delayed — flag it so the
    // committee doesn't over-trust an intraday price (matters most for scalp/intraday).
    const _barMin = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440, '1w': 10080, '1M': 43200 }[ts.primaryTf] || 1440;
    const dataAgeMin = candles?.length ? Math.round((Date.now() / 1000 - candles[candles.length - 1].time) / 60) : null;
    const feedDelayed = !!(session.open && dataAgeMin != null && dataAgeMin > _barMin * 1.5 + 12);   // beyond ~1.5 bars + buffer
    const delayMaterial = feedDelayed && ['1m', '5m', '15m', '1h'].includes(ts.primaryTf);            // short-TF → delay matters
    const freshnessBlock = feedDelayed
      ? `\n━━━ DATA-FEED FRESHNESS ━━━\nThe market is OPEN but the latest ${ts.primaryTf} bar is ~${dataAgeMin} min old — the free price feed appears DELAYED.${delayMaterial ? ` For this short-timeframe (${ts.label}) trade that is MATERIAL: the live price may be ~${dataAgeMin} min behind, so do NOT over-trust precise intraday levels — widen entry tolerance and shade confidence down.` : ' For this longer-timeframe trade the delay is immaterial.'}`
      : '';

    // ── Sector relative strength block ─────────────────────────────────────────
    let sectorBlock = '';
    if (sectorData && sectorData.stock_return_30d != null && sectorData.sector_return_30d != null) {
      const sr = sectorData;
      sectorBlock = `\n━━━ SECTOR RELATIVE STRENGTH ━━━\n${sym} is ${sr.outperforming ? 'OUTPERFORMING' : 'UNDERPERFORMING'} its sector (${sr.sector}) by ${sr.vs_sector} over the last 30 days (${sym} ${sr.stock_return_30d > 0 ? '+' : ''}${sr.stock_return_30d}% vs sector ${sr.sector_return_30d > 0 ? '+' : ''}${sr.sector_return_30d}%). ${sr.outperforming ? 'Relative strength supports the long thesis; leaders tend to keep leading.' : 'Relative weakness is a caution flag — the name is lagging its peers.'}`;
    }

    // ── COT speculative-positioning block ──────────────────────────────────────
    let positioningBlock = '';
    if (positioning && positioning.signal) {
      positioningBlock = `\n━━━ COT SPECULATIVE POSITIONING (${positioning.source}, as of ${positioning.as_of}) — ⚠ WEAK EVIDENCE / SLOW CONTEXT ONLY ━━━\n${positioning.signal}\nEVIDENCE TIER: LOW. COT data is published with a ~3-day lag, weekly, and the academic record for it as a timing signal is weak and drawdown-heavy (forecasting ability only at extremes; mostly promoted by those selling COT tools). Use ONLY as slow crowding context — positioning at an extreme (≥75% or ≤25% one-sided) can precede mean reversion — and NEVER as a primary entry/timing reason or a confidence booster.`;
    }

    // ── Crypto derivatives + flows + on-chain + vol block (real-time, MED-HIGH) ──
    let cryptoBlock = '';
    if (cryptoDerivs && cryptoDerivs.signal) {
      const ls = cryptoDerivs.long_short, fd = cryptoDerivs.funding;
      const crowded = (ls && (ls.pct_long >= 65 || ls.pct_long <= 35)) || (fd && fd.label !== 'neutral');
      // Realized vol (annualized) from the analysed candles, to compare with implied DVOL.
      const _ppy = { '15m': 35040, '1h': 8760, '4h': 2190, '1d': 365, '1w': 52 }[ts.primaryTf] || 365;
      let _rvol = null;
      if (closes && closes.length > 31) {
        const _sl = closes.slice(-31), _r = [];
        for (let i = 1; i < _sl.length; i++) _r.push(Math.log(_sl[i] / _sl[i - 1]));
        const _m = _r.reduce((a, b) => a + b, 0) / _r.length;
        const _v = _r.reduce((a, b) => a + (b - _m) * (b - _m), 0) / (_r.length - 1);
        _rvol = +(Math.sqrt(_v) * Math.sqrt(_ppy) * 100).toFixed(0);
      }
      let _volLine = '';
      if (_rvol != null) {
        const iv = cryptoDerivs.vol?.implied_dvol_pct;
        _volLine = `\nRealized vol (30-bar, annualized) ~${_rvol}%.${iv ? ` Implied DVOL ${iv}% — ${iv > _rvol * 1.15 ? 'options pricing ELEVATED forward risk (fear premium)' : iv < _rvol * 0.85 ? 'implied < realized (complacency)' : 'in line with realized'}. Size stops/targets to this vol regime, not a fixed %.` : ''}`;
      }
      cryptoBlock = `\n━━━ CRYPTO DERIVATIVES, FLOWS, ON-CHAIN & VOL (${cryptoDerivs.source}, live) ━━━\n${cryptoDerivs.signal}${_volLine}
HOW TO USE — the crypto-native board the price chart can't show (MEDIUM-HIGH tier, real-time/real data — weigh properly, but no single one is a standalone trigger):
• POSITIONING (funding + retail L/S + OI): one-sided funding / crowd at an extreme = SQUEEZE risk against the crowd — do NOT stack high conviction in the direction the crowd is already maxed on. Rising OI = fresh leverage (reversal fuel).
• SPOT-ETF FLOWS: the REAL institutional-demand signal — use this instead of guessing/quoting flow numbers. Sustained net outflows = genuine distribution headwind; inflows = real accumulation.
• ON-CHAIN VALUATION: realized price is the aggregate cost basis and a heavily-watched support/resistance — price BELOW it = most holders underwater (capitulation risk), ABOVE it = unrealized-profit cushion. MVRV<1 = value zone, >3.5 = historically rich. SOPR<1 = coins selling at a loss.
• High stablecoin dry powder = latent sideline buying capacity.${crowded ? '\n⚠ Positioning is at/near an extreme — explicitly weigh squeeze risk before assigning high confidence.' : ''}`;
    }

    // ── Seasonality block ──────────────────────────────────────────────────────
    let seasonalityBlock = '';
    if (seasonality) {
      const sn = seasonality;
      seasonalityBlock = `\n━━━ SEASONALITY (${sn.month}, last ${sn.years} years) — ⚠ WEAK EVIDENCE ━━━\nHistorically ${sym} has averaged ${sn.avgReturn > 0 ? '+' : ''}${sn.avgReturn}% in ${sn.month}, positive ${sn.winRate}% of the time (best +${sn.best}%, worst ${sn.worst}%, n=${sn.years} years).\nEVIDENCE TIER: LOW. Calendar seasonality is contested in the literature — often attributed to data-mining / a few outlier years, is marginal after costs, and ${sn.years} years is a tiny sample. Treat as a very soft tie-breaker only, NEVER a standalone reason, and do not let it raise confidence.`;
    }

    // ── Calibration block — the model's OWN realised accuracy by confidence band ─
    // This is the self-correction loop: feed back how often calls at each stated
    // confidence actually hit TP, so the committee can adjust for over/underconfidence.
    let calibrationBlock = '';
    if (!_controlArm && calibration && calibration.lines?.length) {
      // Evidence gate (learning-loop research): a per-band line needs ~25+ resolved
      // calls before its hit-rate means anything (±10pp CI even at n=100). Thin
      // bands injected as "feedback" are noise the committee will anchor on.
      // Below-threshold bands are omitted; if none qualify, an overall-only line
      // (which aggregates across bands) is sent once there are ≥10 resolved calls.
      // The HARD post-hoc remap (calibrateConfidence) is unaffected — its Bayesian
      // shrinkage handles thin bands mathematically and sits outside the LLM.
      const solid = calibration.lines.filter(l => l.n >= 25);
      if (solid.length) {
        const rows = solid.map(l => `  • Stated ${l.band} → ${l.acc}% actually hit TP (n=${l.n})`).join('\n');
        calibrationBlock = `\n━━━ ⚖ CONFIDENCE CALIBRATION (your realised hit-rate by stated confidence, ${calibration.n} resolved calls; overall ${calibration.overallAcc}%) ━━━\n${rows}\nCALIBRATION DIRECTIVE: This is YOUR historical accuracy at each confidence level across ALL symbols. If a band's realised hit-rate is well BELOW the stated confidence, you have been systematically OVERCONFIDENT there — pick a confidence_score whose band has historically matched the real outcome. Do not state 85% if your 80–89% calls have only hit ~55%.`;
      } else if (calibration.n >= 10) {
        calibrationBlock = `\n━━━ ⚖ CONFIDENCE CALIBRATION ━━━\nAcross ${calibration.n} resolved calls (all confidence levels pooled), ${calibration.overallAcc}% hit TP. Per-band data is still too thin to act on — calibrate your confidence against this overall base rate and avoid extreme confidence in either direction.`;
      }
    }

    // ── Confluence Score block ─────────────────────────────────────────────────
    const confluenceBlock = confluenceScore ? `
━━━ MULTI-TIMEFRAME CONFLUENCE SCORE (de-correlated) ━━━
Signal Alignment: ${confluenceScore.bullPct}% bullish / ${confluenceScore.bearPct}% bearish — from ${confluenceScore.independentSignals} INDEPENDENT signal families (${confluenceScore.signalCount} raw indicators, but correlated ones within a family are down-weighted so the same trend/momentum read is not counted multiple times).
Direction: ${confluenceScore.direction} | Strength: ${confluenceScore.strength}
${confluenceScore.concentrated ? '⚠️ CONCENTRATED — most of this score comes from ONE correlated family (e.g. just the moving-average stack). That is NOT genuine confluence; do not treat it as strong multi-source agreement, and keep confidence moderate.' : ''}
${confluenceScore.bullPct >= 80 && !confluenceScore.concentrated ? '⚡ STRONG BULLISH ALIGNMENT across independent families — high-conviction long setups supported' : ''}${confluenceScore.bullPct <= 20 && !confluenceScore.concentrated ? '⚡ STRONG BEARISH ALIGNMENT across independent families — high-conviction short setups supported' : ''}${confluenceScore.direction === 'MIXED' ? '⚠️ MIXED SIGNALS — requires extra caution; NO_EDGE or WAIT may be most honest verdict' : ''}
CONFIDENCE CALIBRATION RULE: Confidence score should generally not exceed (Confluence% + 20) unless exceptional circumstances justify it${confluenceScore.concentrated ? ', and because this score is concentrated in one correlated family, stay well BELOW that ceiling' : ''}. A confluence of ${confluenceScore.bullPct}% → confidence ceiling ~${Math.min(100, confluenceScore.bullPct + 20)}%.` : '';

    // ── Entry-confluence framework (top-down, style-specific) ──────────────────
    // Research-grounded: a high-probability entry needs several INDEPENDENT factors
    // agreeing across the right timeframe stack — and the ENTRY itself (not just a
    // price level) must be confluent. The 3-TF stack here is the style's own.
    const confluenceFrameworkBlock = `
━━━ ENTRY-CONFLUENCE FRAMEWORK (${ts.label} — top-down) ━━━
A high-probability ${ts.label} entry needs CONFLUENCE — several INDEPENDENT factors agreeing, read top-down: ${ts.contextTf} for the higher-timeframe TREND/bias → ${ts.primaryTf} for the SETUP (all the technical evidence above is on this TF) → ${ts.entryTf} for the precise ENTRY TRIGGER. Require alignment across at least THREE of these INDEPENDENT categories (not three flavours of the same one) before an actionable BUY/SELL:
1. STATIC structure — price reacting at a real level: prior support/resistance, a supply/demand zone, range edge, round number, or a Fibonacci confluence.
2. DYNAMIC structure — agreement with the moving-average stack / trendline / VWAP, and trading WITH the ${ts.contextTf} higher-timeframe trend (not against it) unless it is an explicit mean-reversion play at a stretched extreme.
3. MOMENTUM — RSI / MACD / StochRSI confirming the direction (a momentum divergence at the level is a strong plus), and NOT already exhausted/overbought INTO the move.
4. VOLUME / participation — genuine volume behind the move, so the level isn't a thin false break.
RULES: (a) the de-correlated Confluence Score above already collapses redundant same-family indicators — do NOT re-inflate confidence by counting the MA stack three times. (b) The ENTRY must be confluent, not merely a price number — the entry_trigger you output should be "price AT a static level, confirmed by a ${ts.entryTf} reversal/break candle + momentum/volume", e.g. "buy the pullback to X on a ${ts.entryTf} bullish reversal"; "price reached X" alone is NOT an entry. (c) If fewer than three independent categories align, or the trade fights the ${ts.contextTf} trend with no extreme to mean-revert from, output WAIT / NO_EDGE and state exactly which confluence is missing.`;

    // ── Entry-timeframe read block (actual lower-TF data, fixes "inferred" entry) ──
    const entryTfBlock = entryTfRead ? `
━━━ ENTRY-TIMEFRAME READ (${entryTfRead.tf} — ACTUAL lower-TF data for the entry trigger) ━━━
On the ${entryTfRead.tf} chart right now: price ${entryTfRead.close.toFixed(dp)}, short-term trend ${entryTfRead.trend.toUpperCase()} (vs its 20/50 MAs), RSI ${entryTfRead.rsi != null ? Math.round(entryTfRead.rsi) : 'n/a'}, momentum ${entryTfRead.macdUp ? 'UP (MACD>0)' : 'DOWN (MACD<0)'}, last ${entryTfRead.tf} candle ${entryTfRead.candle}, volume ${entryTfRead.vol}. Nearest ${entryTfRead.tf} structure: resistance ~${entryTfRead.high.toFixed(dp)} / support ~${entryTfRead.low.toFixed(dp)}.
DIRECTIVE: this is the REAL lower-timeframe data — use it to confirm the precise entry. The entry is only "live/confirmed" when this ${entryTfRead.tf} read AGREES with the trade direction (long → ${entryTfRead.tf} trend turning up / reclaiming its 20MA with momentum up at support; short → the mirror). If the ${entryTfRead.tf} currently DISAGREES with the ${ts.primaryTf} setup, the entry is NOT confirmed yet — say so and keep it WAIT / pending entry until the lower timeframe confirms.` : '';

    // ── Research priors (peer-reviewed) — weak Bayesian tie-breakers, not overrides ──
    const researchPriorsBlock = `
━━━ RESEARCH PRIORS (peer-reviewed evidence — weak tie-breakers, NEVER overrides) ━━━
Anchor conviction in what has actually REPLICATED out-of-sample, and discount what hasn't:
• Durable, multi-asset edges — lean WITH the setup when it aligns: TREND / time-series momentum, CARRY (esp. FX), cross-sectional MOMENTUM & VALUE, and post-earnings drift (PEAD) for stocks. Trend/technical edge is REGIME-DEPENDENT — real in clearly trending or stressed markets, weak in chop.
• Treat with skepticism: lone oscillator / chart-pattern signals (little robust standalone edge), and ANY famous retail setup — documented anomalies fade ~26% out-of-sample and ~58% post-publication (McLean & Pontiff 2016), so "well-known" usually means "already arbitraged".
• Most apparent edges are partly data-mined (only a small fraction survive multiple-testing correction) — a clean-looking signal is not automatically real.
RULE: use these to BREAK TIES and TEMPER confidence only. The live evidence above and the realised track record dominate. Do NOT inflate confidence just because a setup matches a famous factor's name.`;

    // ── Macro Intermarket block (FRED + Yahoo Finance) ─────────────────────────
    const intermarketBlock = macroIntermarket ? `
━━━ MACRO INTERMARKET SIGNALS (Live, Quantified) ━━━
Yield Curve (10Y−2Y): ${macroIntermarket.yield_curve?.signal || 'N/A'} → Regime: ${macroIntermarket.yield_curve?.regime || 'N/A'}
HY Credit Spreads (OAS): ${macroIntermarket.hy_oas?.signal || 'N/A'} → Regime: ${macroIntermarket.hy_oas?.regime || 'N/A'}
VIX: ${macroIntermarket.vix?.signal || 'N/A'} → Regime: ${macroIntermarket.vix?.regime || 'N/A'}
DXY (Dollar): ${macroIntermarket.dxy?.signal || 'N/A'}
Bond-Equity Correlation: ${macroIntermarket.bond_equity_correlation || 'N/A'}` : '';

    // ── Quality Scores block (Piotroski, Beneish, Accrual, Altman Z) ──────────
    const qualityBlock = qualityScores ? `
━━━ ACCOUNTING QUALITY SCORES (${qualityScores.period || 'Annual'}) ━━━
Piotroski F-Score: ${qualityScores.piotroski?.score ?? 'N/A'}/9 (${qualityScores.piotroski?.quality || 'N/A'}) — ${qualityScores.piotroski?.interpretation || ''}
Beneish M-Score: ${qualityScores.beneish?.score ?? 'N/A'} — ${qualityScores.beneish?.interpretation || ''}
Accrual Ratio: ${qualityScores.accrual_ratio?.pct ?? 'N/A'}% — ${qualityScores.accrual_ratio?.interpretation || ''}
Altman Z-Score: ${qualityScores.altman_z?.score ?? 'N/A'} — ${qualityScores.altman_z?.interpretation || ''}
${qualityScores.quality_flags?.length ? '⚑ FLAGS:\n' + qualityScores.quality_flags.map(f => `  ${f}`).join('\n') : ''}` : '';

    const systemPrompt = `You are a ruthlessly honest quantitative trading analyst. The user has already been shown a clear disclaimer stating this is an AI estimate and not financial advice. They understand this. Therefore you have full permission to be completely direct, unhedged, and honest — there is no need to soften conclusions or add caveats.

ABSOLUTE RULES — violating any of these is a failure:
1. HOLD is NOT a default. HOLD means the market is genuinely consolidating with no directional edge. If you're uncertain, use WAIT or NO_EDGE — not HOLD.
2. confidence_score of 60-65 is BANNED as a default. Every asset has a real confidence level. Assign it based on signal alignment, not safety. Most analyses should score outside the 58-67 range.
3. NEVER soften a bearish view with vague language. If the asset is broken, say it clearly with AVOID, SHORT, or REDUCE_EXPOSURE.
4. NEVER soften a bullish view either. If the setup is strong, say STRONG_BUY or BUY — not SPECULATIVE_BUY or WAIT.
5. NO_EDGE is always available. It is more honest than a forced verdict. Use it freely when evidence is genuinely mixed.
6. AVOID BIAS: Do not treat extended oscillators (RSI > 70, StochRSI > 80) as automatic short signals. In strong uptrends, prices can remain extended for long periods. Similarly, oversold oscillators in a strong downtrend are not automatic buy signals. Respect the macro trend.
7. You must respond with ONLY valid JSON. No markdown. No preamble. No "note:". No disclaimers.

CALIBRATION EXAMPLES — use these as anchors:
- confidence_score 90: Strong daily/weekly uptrend, price consolidating then breaking out on high volume, all key SMAs aligned bullishly, MACD positive. Extremely high-conviction BUY setup.
- confidence_score 82: RSI 72 overbought, price at BB upper, StochRSI 92, weekly MACD crossing down, approaching major resistance. Extremely high-conviction SHORT setup.
- confidence_score 77: Strong uptrend, price above all SMAs, MACD positive, OBV accumulation, but RSI 64 — not overbought, room to run. Solid BUY.
- confidence_score 63: Mixed signals — daily bullish but weekly bearish MACD, volume declining, near 61.8% Fibonacci. Genuine uncertainty. WAIT for confirmation.
- confidence_score 45: Three indicators bullish, three bearish, flat OBV, macro unclear. NO_EDGE is the correct answer.
- confidence_score 31: Asset in structural downtrend, breaking support, negative OBV, sector deteriorating. HIGH-CONVICTION AVOID or SHORT.

VERDICT GUIDE — pick the one that actually fits, not the safe one:
- STRONG_BUY: Multiple timeframe alignment, all major indicators bullish, strong momentum, low risk
- BUY: Most indicators bullish, clear upside, acceptable risk
- SPECULATIVE_BUY: Bullish thesis but high risk or weak evidence; only for risk-tolerant traders
- WAIT: Setup is forming but the trigger hasn't fired — specific catalyst or level needed
- HOLD: Asset is in genuine consolidation; no new entry but existing longs reasonable
- REDUCE_EXPOSURE: Thesis deteriorating; existing holders should trim
- AVOID: Poor risk/reward; neither long nor short is attractive
- SHORT: Active bearish thesis with clear downside target and defined risk
- SPECULATIVE_SHORT: Bearish thesis but high risk; for risk-tolerant traders only
- HEDGE: Long but protect with puts or inverse ETF due to elevated tail risk
- NO_EDGE: Genuinely conflicting signals; no trade edge exists right now`;

    const prompt = `ASSET: ${sym}  |  TYPE: ${type}
══════════════════════════════════════════════════
Current Price: ${curr.toFixed(dp)}
1D Change: ${chg1d}%  |  7D: ${chg7d != null ? chg7d + '%' : 'N/A'}  |  30D: ${chg30d != null ? chg30d + '%' : 'N/A'}

━━━ TECHNICAL INDICATORS (Daily) ━━━
Trend (SMA crossover): ${trend}
RSI(14): ${rsi ?? 'N/A'}${rsi ? (rsi > 70 ? ' ⚠ OVERBOUGHT' : rsi < 30 ? ' ⚠ OVERSOLD' : rsi > 60 ? ' (bullish momentum)' : rsi < 40 ? ' (bearish momentum)' : '') : ''}
MACD: ${macd != null ? (macd > 0 ? '+' + macd.toFixed(dp) + ' (bullish)' : macd.toFixed(dp) + ' (bearish)') : 'N/A'}
SMA20:  ${sma20?.toFixed(dp) || 'N/A'} → price ${sma20 ? (curr > sma20 ? 'ABOVE ↑' : 'BELOW ↓') : 'N/A'}
SMA50:  ${sma50?.toFixed(dp) || 'N/A'} → price ${sma50 ? (curr > sma50 ? 'ABOVE ↑' : 'BELOW ↓') : 'N/A'}
SMA200: ${sma200?.toFixed(dp) || 'N/A'} → price ${sma200 ? (curr > sma200 ? 'ABOVE ↑ (long-term uptrend)' : 'BELOW ↓ (long-term downtrend)') : 'N/A'}
Bollinger Bands (20,2σ): Upper ${bb?.upper ?? 'N/A'} | Mid ${bb?.middle ?? 'N/A'} | Lower ${bb?.lower ?? 'N/A'} | %B: ${bb?.pctB ?? 'N/A'}%${bb != null ? (bb.pctB > 80 ? ' (near upper — overbought)' : bb.pctB < 20 ? ' (near lower — oversold)' : '') : ''}
StochRSI(14): ${stochRsi ?? 'N/A'}${stochRsi != null ? (stochRsi > 80 ? ' ⚠ OVERBOUGHT' : stochRsi < 20 ? ' ⚠ OVERSOLD' : '') : ''}
ATR(14): ${atr?.toFixed(dp) || 'N/A'} | ADX(14): ${adx != null ? adx + (adx > 25 ? ' (strong trend)' : adx > 15 ? ' (developing trend)' : ' (weak/no trend)') : 'N/A'}
BB Width: ${bbWidth != null ? bbWidth + '%' + (bbWidth < 3 ? ' ⚡ SQUEEZE — breakout imminent' : bbWidth > 8 ? ' (expanded — high vol)' : ' (normal)') : 'N/A'} | Volume: ${volTrnd} | OBV: ${obv}
${srText}
${fibText}
${fibExtText ? fibExtText : ''}

━━━ WEEKLY TIMEFRAME ━━━
Weekly trend: ${wTrend ?? 'N/A'} | Weekly RSI: ${wRSI ?? 'N/A'}${wRSI != null ? (wRSI > 70 ? ' ⚠ OVERBOUGHT' : wRSI < 30 ? ' ⚠ OVERSOLD' : '') : ''}
Weekly MACD: ${wMACD != null ? (wMACD > 0 ? 'POSITIVE (bullish)' : 'NEGATIVE (bearish)') : 'N/A'}
Weekly SMA20: ${wSMA20?.toFixed(dp) || 'N/A'} | Weekly SMA50: ${wSMA50?.toFixed(dp) || 'N/A'}

WEEKLY OHLCV (last 8 weeks, oldest→newest):
${weeklyTable}

${relStr ? `\n━━━ RELATIVE STRENGTH vs ${benchName} ━━━
1W RS: ${relStr.rs1w != null ? (relStr.rs1w > 0 ? '+' : '') + relStr.rs1w + '%' : 'N/A'} | 1M RS: ${relStr.rs1m != null ? (relStr.rs1m > 0 ? '+' : '') + relStr.rs1m + '%' : 'N/A'} | 3M RS: ${relStr.rs3m != null ? (relStr.rs3m > 0 ? '+' : '') + relStr.rs3m + '%' : 'N/A'}
${relStr.rs1m != null ? (relStr.rs1m > 5 ? 'Significantly OUTPERFORMING benchmark' : relStr.rs1m < -5 ? 'Significantly UNDERPERFORMING benchmark' : 'In line with benchmark') : ''}` : ''}
${volProfile ? `\n━━━ VOLUME PROFILE (60-day) ━━━
POC (Point of Control): ${volProfile.poc} | VAH: ${volProfile.vah} | VAL: ${volProfile.val}
${curr > volProfile.poc ? 'Price ABOVE POC — buyers in control of value area' : 'Price BELOW POC — sellers in control of value area'}` : ''}
${fearGreed ? `\n━━━ MARKET SENTIMENT ━━━
Fear & Greed Index: ${fearGreed.value}/100 (${fearGreed.label})${fearGreed.value <= 25 ? ' — EXTREME FEAR: historically excellent buy zone' : fearGreed.value >= 75 ? ' — EXTREME GREED: historically risky entry, elevated correction risk' : fearGreed.value <= 40 ? ' — Fear: cautious market, potential opportunity' : fearGreed.value >= 60 ? ' — Greed: elevated sentiment, watch for reversals' : ' — Neutral: balanced market sentiment'}` : ''}

━━━ DAILY PRICE ACTION (last 20 bars, oldest→newest) ━━━
${ohlcvTable}
${fundBlock}
${eventBlock}
${sessionBlock}
${freshnessBlock}
${sectorBlock}
${positioningBlock}
${cryptoBlock}
${seasonalityBlock}
${macroCtx ? `\n━━━ LIVE MACRO CONTEXT ━━━\n${macroCtx}` : ''}
${intermarketBlock}
${qualityBlock}
${confluenceBlock}
${confluenceFrameworkBlock}
${entryTfBlock}
${researchPriorsBlock}
${scanBlock}
${impactBlock}
${memoryBlock}

━━━ RECENT NEWS (last 60 days) ━━━
${newsText || 'No recent news available.'}

${positionMgmtBlock}
━━━ TASK ━━━
Analyze all data above. Be direct. Do not hedge. Do not default.
Read the price action carefully — what is actually happening? Are bulls or bears in control? What does the volume say? Where is this asset headed?

Respond ONLY with valid JSON. No text before or after.

{
  "verdict": "${_pmTarget ? 'HOLD_TRADE|MOVE_TO_BREAKEVEN|TIGHTEN_STOP|SCALE_OUT|CLOSE_TRADE' : 'STRONG_BUY|BUY|SPECULATIVE_BUY|WAIT|HOLD|REDUCE_EXPOSURE|AVOID|SHORT|SPECULATIVE_SHORT|HEDGE|NO_EDGE'}",
  "confidence_level": "Low|Moderate|High|Very High",
  "confidence_score": <integer 0-100. Base this on how many indicators and frameworks align. If 7 out of 9 signals point the same direction, score 75+. If signals are split 5-4, score 45-55. If genuinely uncertain, score below 50. 60-65 is a valid score only if evidence is genuinely in that middle band — not as a default.>,
  "executive_summary": "3-4 sentence institutional overview of the opportunity/risk",
  "macro_environment": "3-4 sentences on macro regime and specific impact on this asset",
  "macro_regime": "risk-on|risk-off|late-cycle|recessionary|expansionary|euphoric|fearful|liquidity-driven|fundamentally-driven",
  "fundamental_analysis": "3-4 sentences on fundamentals, valuation, competitive position",
  "valuation": "undervalued|fairly-valued|overvalued|irrationally-priced",
  "technical_analysis": "3-4 sentences on full multi-timeframe technical picture",
  "sentiment_analysis": "2-3 sentences on positioning, crowding, contrarian signals",
  "sentiment_condition": "excessively-bullish|excessively-bearish|complacent|euphoric|fearful|neutral",
  "catalyst_analysis": "2-3 sentences on material catalysts and underpriced risks",
  "risk_analysis": "3-4 sentences on key risks and how the thesis fails",
  "scenarios": {
    "bull":    { "probability": <int>, "target": "<price>", "upside": "<pct>",   "description": "<1 sentence>" },
    "base":    { "probability": <int>, "target": "<price>", "change": "<pct>",   "description": "<1 sentence>" },
    "bear":    { "probability": <int>, "target": "<price>", "downside": "<pct>", "description": "<1 sentence>" },
    "extreme": { "probability": <int>, "target": "<price>", "downside": "<pct>", "description": "<1 sentence>" }
  },
  "expected_value": "positive|slightly-positive|neutral|slightly-negative|negative",
  "risk_reward": "<e.g. 2.5:1>",
  "short_term_outlook":  "1-2 sentences on days-to-4-week outlook",
  "medium_term_outlook": "1-2 sentences on 1-3 month outlook",
  "long_term_outlook":   "1-2 sentences on 3-12 month outlook",
  "key_reasons": ["<reason 1>", "<reason 2>", "<reason 3>", "<reason 4>"],
  "invalidation_conditions": ["<condition 1>", "<condition 2>", "<condition 3>"],
  "entry_zone": "<the BEST entry LEVEL for this setup — a pullback / rally / breakout-retest zone, NOT just the current price (use current price only when price is already at a prime entry). A tight concrete zone, never blank>",
  "entry_trigger": "WHEN/HOW to enter: e.g. 'enter at market now', 'buy the pullback to X', or 'wait for a break/close above Y then enter'. For WAIT/NO_EDGE give the conditional level that WOULD make it a valid entry.",
  "stop_loss": "<concrete stop-loss price — never blank>",
  "target_price": "<first take-profit price, TP1 — never blank>",
  "take_profit_2": "<second take-profit / runner price, TP2 (use the same as TP1 if you only have one target)>",
  "entry_strategy": "How and when to build the position",
  "position_sizing": "Recommended sizing relative to portfolio and why",
  "stop_loss_logic": "Why this stop level and what it protects against",
  "profit_taking_logic": "When and how to take profits, scaling strategy",
  "hedging_considerations": "Any hedges worth considering",
  "timeframe": "<recommended holding period>",
  "what_would_change_view": "What specific development would flip this thesis",
  "why_confidence_not_higher": "What uncertainty prevents higher confidence"
}`;

    // ── Step 5: Multi-agent parallel analysis — TWO-PASS ARCHITECTURE ─────────
    // PASS 1: Each specialist enumerates evidence only (no verdict, no conclusion).
    //         This prevents anchoring — agents surface facts, not spin.
    // PASS 2: Cross-critique debate — agents challenge each other's weakest assumptions.
    // FINAL:  Committee synthesises all factor lists + debate → single JSON verdict.

    // Shared data block sent to each specialist
    const sharedData = prompt;

    // Stagger helper — spreads concurrent Gemini calls to avoid burst rate-limiting
    const stagger = ms => new Promise(r => setTimeout(r, ms));

    // ── PASS 1: Structured Factor Enumeration ─────────────────────────────────
    function parseFactors(raw, fallback) {
      try {
        const cleaned = raw.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
        const m = cleaned.match(/\{[\s\S]*\}/);
        if (m) return JSON.parse(m[0]);
      } catch {}
      return { bullish_factors: [fallback], bearish_factors: [], key_signal: fallback, key_level: 'N/A' };
    }

    // ── PASS 1+2 CONSOLIDATED ──────────────────────────────────────────────────
    // One call reasons through ALL FOUR specialist lenses (technical / fundamental
    // / macro / risk) AND runs the cross-critique debate — the same analytical
    // framework as the old 4 specialist + 3 debate calls, but the data bundle is
    // sent ONCE instead of 5×. ~7 fewer AI calls + far fewer tokens per scan. The
    // committee synthesis (next call) is unchanged. Produces the same variables.
    const _deskSystem = `You are a four-person hedge-fund analysis desk working a single name: a pure TECHNICAL analyst, a FUNDAMENTAL analyst, a MACRO strategist, and a RISK manager / devil's advocate. Each enumerates SPECIFIC evidence — exact indicator values, price levels, metrics, data points — and NEVER vague statements or conclusions. Reason through every lens with full rigour, independently, then run a brief cross-critique where members challenge each other's weakest assumptions (this reduces groupthink). Output ONLY the requested JSON.`;

    const _fundInstruction = (type === 'Stock' || type === 'ETF')
      ? 'For "fundamental", reference exact financial metrics.'
      : `This is a ${type} asset, so set "fundamental" to {"bullish_factors":["${type} assets: fundamental analysis not applicable"],"bearish_factors":[],"key_signal":"N/A","valuation_view":"N/A"}.`;

    const _deskPrompt = `For ${sym} (${type}, price: ${curr.toFixed(dp)}), produce all four specialists' evidence AND their cross-critique for a ${ts.label} trade. Enumerate specific, data-driven evidence (3 items per list, each with a concrete data point) — do NOT reach a verdict here. ${_fundInstruction}

Respond ONLY with this JSON (no other text, no markdown):
{
  "technical":   { "bullish_factors": ["specific factor with data point","specific factor","specific factor"], "bearish_factors": ["specific factor with data point","specific factor","specific factor"], "key_signal": "the single most significant technical signal right now (1 sentence)", "key_level": "the most critical price level and why it matters" },
  "fundamental": { "bullish_factors": ["specific factor with exact metric","specific factor","specific factor"], "bearish_factors": ["specific factor with exact metric","specific factor","specific factor"], "key_signal": "the most important fundamental signal right now (1 sentence)", "valuation_view": "undervalued|fairly-valued|overvalued — one sentence explaining why" },
  "macro":       { "tailwinds": ["specific macro tailwind with data","specific tailwind","specific tailwind"], "headwinds": ["specific macro headwind with data","specific headwind","specific headwind"], "key_signal": "the most important macro signal for THIS specific asset (1 sentence)", "regime": "risk-on|risk-off|late-cycle|recessionary|expansionary" },
  "risk":        { "critical_risks": ["specific risk with supporting data","specific risk","specific risk"], "underappreciated_risks": ["risk the bulls are ignoring","risk","risk"], "bear_trigger": "the single most likely catalyst that causes a 20%+ drawdown (1 sentence)", "max_downside": "realistic worst-case price level and the scenario that gets there" },
  "debate": {
    "technical_rebuts_risk": "TECHNICAL analyst, blunt, 2-3 sentences: which of the risk manager's concerns does the current technical picture specifically CONTRADICT? cite exact levels/indicators",
    "risk_challenges_technical": "RISK manager, harsh, 2-3 sentences: what critical risk does the technical optimism IGNORE? what fundamental/macro factor makes the charts less reliable here? cite specific data",
    "macro_vs_fundamental": "MACRO strategist, direct, 2-3 sentences: how does the current regime specifically challenge or support the fundamental thesis? what is the fundamental analyst underweighting?"
  }
}

DATA:
${sharedData}`;

    let _desk = {};
    try {
      const _raw = await callAgent(_deskSystem, _deskPrompt, 4000);
      const _cleaned = _raw.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
      const _m = _cleaned.match(/\{[\s\S]*\}/);
      if (_m) _desk = JSON.parse(_m[0]);
    } catch { /* fall through to per-lens fallbacks below */ }

    const techRaw = _desk.technical || { bullish_factors: ['Technical data unavailable'], bearish_factors: [], key_signal: 'N/A', key_level: 'N/A' };
    const fundRaw = (type === 'Stock' || type === 'ETF')
      ? (_desk.fundamental || { bullish_factors: ['Fundamental data unavailable'], bearish_factors: [], key_signal: 'N/A', valuation_view: 'N/A' })
      : { bullish_factors: [`${type} assets: fundamental analysis not applicable`], bearish_factors: [], key_signal: 'N/A', valuation_view: 'N/A' };
    const macroRaw = _desk.macro || { tailwinds: ['Macro data unavailable'], headwinds: [], key_signal: 'N/A', regime: 'N/A' };
    const riskRaw = _desk.risk || { critical_risks: ['Risk data unavailable'], underappreciated_risks: [], bear_trigger: 'N/A', max_downside: 'N/A' };
    const _dbt = _desk.debate || {};
    const techVsRisk  = _dbt.technical_rebuts_risk    || null;
    const riskVsTech  = _dbt.risk_challenges_technical || null;
    const macroVsFund = _dbt.macro_vs_fundamental      || null;

    // Format factor lists for committee briefing
    const fmtList = (arr, label) => arr?.length ? `  ${label}: ${arr.slice(0, 4).join(' | ')}` : '';
    const techFactors = [
      fmtList(techRaw.bullish_factors, '▲ Bullish'),
      fmtList(techRaw.bearish_factors, '▼ Bearish'),
      techRaw.key_signal ? `  Key signal: ${techRaw.key_signal}` : '',
      techRaw.key_level  ? `  Key level:  ${techRaw.key_level}` : '',
    ].filter(Boolean).join('\n');

    const fundFactors = [
      fmtList(fundRaw.bullish_factors, '▲ Bullish'),
      fmtList(fundRaw.bearish_factors, '▼ Bearish'),
      fundRaw.key_signal    ? `  Key signal:     ${fundRaw.key_signal}` : '',
      fundRaw.valuation_view ? `  Valuation view: ${fundRaw.valuation_view}` : '',
    ].filter(Boolean).join('\n');

    const macroFactors = [
      fmtList(macroRaw.tailwinds     || macroRaw.bullish_factors, '↑ Tailwinds'),
      fmtList(macroRaw.headwinds     || macroRaw.bearish_factors, '↓ Headwinds'),
      macroRaw.key_signal ? `  Key signal: ${macroRaw.key_signal}` : '',
      macroRaw.regime     ? `  Regime:     ${macroRaw.regime}` : '',
    ].filter(Boolean).join('\n');

    const riskFactors = [
      fmtList(riskRaw.critical_risks,        '🚨 Critical'),
      fmtList(riskRaw.underappreciated_risks, '⚠️ Hidden'),
      riskRaw.bear_trigger ? `  Bear trigger: ${riskRaw.bear_trigger}` : '',
      riskRaw.max_downside ? `  Worst case:   ${riskRaw.max_downside}` : '',
    ].filter(Boolean).join('\n');

    const debateBlock = [
      techVsRisk  ? `Technical analyst rebutting risks: ${techVsRisk}` : null,
      riskVsTech  ? `Risk manager challenging technicals: ${riskVsTech}` : null,
      macroVsFund ? `Macro vs fundamental: ${macroVsFund}` : null,
    ].filter(Boolean).join('\n');

    // Legacy aliases for display in renderResults specialist panel
    const techRawText  = [techFactors,  techVsRisk  || ''].join('\n');
    const fundRawText  = [fundFactors].join('\n');
    const macroRawText = [macroFactors, macroVsFund || ''].join('\n');
    const sentRawText  = [riskFactors,  riskVsTech  || ''].join('\n');

    setStep(5);

    // ── Quant engine: await the early-fired fetch so the committee can weigh it ──
    const engineData = await enginePromise;

    // ── Setup feature vector + meta-label (structural retrieval of similar setups) ─
    const setupFeatures = buildSetupFeatures({
      type, style: ts.label, closes,
      rsi: calcRSI(closes), adx, bbWidth,
      confluence: confluenceScore?.bullPct,
      regimeName: engineData?.regime?.name,
    });
    const metaLabel = _controlArm ? null : await fetchMetaLabel(setupFeatures);
    let metaLabelBlock = '';
    if (metaLabel) {
      const directive = metaLabel.pCorrect < 45
        ? 'This setup TYPE has a POOR track record — demand strong confluence or favour WAIT, and do NOT assign high confidence.'
        : metaLabel.pCorrect >= 60
          ? 'This setup type has historically WORKED — a genuine edge may exist, but still avoid overconfidence.'
          : 'MIXED historical reliability for this setup type — weigh the live evidence carefully and keep confidence moderate.';
      metaLabelBlock = `\n━━━ 🧠 SETUP RELIABILITY (meta-label) ━━━\nAcross the ${metaLabel.n} most STRUCTURALLY-SIMILAR resolved setups (matched on regime, trend, momentum, volatility & confluence — NOT the same ticker), the committee's verdict was correct ${metaLabel.pCorrect}% of the time (${metaLabel.wins}W / ${metaLabel.losses}L; overall base rate ${metaLabel.base}%).\nDIRECTIVE: ${directive}`;
    }

    // Qualitative post-mortems from structurally-similar resolved trades — the "what
    // went wrong / right" the engine learns from each closed call (see History tab).
    const lessons = _controlArm ? [] : await fetchLessons(setupFeatures);
    let lessonsBlock = '';
    if (lessons.length) {
      const items = lessons.map(l => {
        const res = l.outcome === 'tp_hit' ? 'WON' : l.outcome === 'sl_hit' ? 'LOST' : 'EXPIRED';
        return `• [${l.sym} ${(l.verdict || '').replace(/_/g, ' ')} → ${res}] ${l.lesson}`;
      }).join('\n');
      lessonsBlock = `\n━━━ 📓 LESSONS FROM SIMILAR PAST TRADES ━━━\nPost-mortems from structurally-similar resolved trades (matched on market structure, NOT the same ticker):\n${items}\nDIRECTIVE: Treat these as hard-won feedback. If THIS setup is about to repeat a mistake flagged above, lower confidence or favour WAIT. If it echoes a past win, that's mild corroboration — not a guarantee. Do not blindly anchor to them.`;
    }
    let engineBlock = '\nINDEPENDENT QUANT ENGINE: unavailable this run (not factored into the verdict).';
    if (engineData && engineData.online && engineData.supported) {
      const eR = engineData.regime, eRisk = engineData.risk, eVal = engineData.validation;
      const riskLine = eRisk
        ? (eRisk.permitted
            ? `would size a ${String(eRisk.direction || '').toUpperCase()} position (~${((eRisk.risk_fraction || 0) * 100).toFixed(2)}% of equity at risk)`
            : `NO POSITION — ${eRisk.rationale || 'vetoed by the risk layer'}`)
        : 'n/a';
      const valLine = (eVal && eVal.verdict)
        ? `${eVal.verdict.passed ? 'PASSED' : 'REJECTED'} (DSR ${eVal.dsr?.dsr != null ? Number(eVal.dsr.dsr).toFixed(2) : 'n/a'} vs >0.95 bar, PBO ${eVal.pbo?.pbo ?? 'n/a'} vs <0.5 bar)`
        : 'not available for this symbol';
      engineBlock = `
INDEPENDENT QUANT ENGINE (point-in-time, leakage-free, validation-gated — weigh heavily):
- Regime: ${eR ? `${eR.name} (${Math.round((eR.confidence || 0) * 100)}% confidence)` : 'n/a'}
- Risk layer (the supreme sizing authority): ${riskLine}
- Systematic strategy validation: ${valLine}
ENGINE DIRECTIVE: This is a disciplined, independent quant check. If the risk layer returns NO POSITION, or validation is REJECTED, treat that as strong DISCONFIRMING evidence — materially lower your confidence (do NOT output High/Very High confidence), and consider WAIT / NO_EDGE over an aggressive BUY/SELL. Explain any conflict. If it AGREES with your thesis, it modestly reinforces confidence.`;
    } else if (engineData && engineData.online) {
      engineBlock = `\nINDEPENDENT QUANT ENGINE: online but no quantitative coverage for ${sym}; not factored in.`;
    }

    // ── Backtest knowledge base (Supabase): what has actually survived testing ──
    let backtestBlock = '';
    if (backtestKB && backtestKB.n) {
      backtestBlock = `
BACKTEST KNOWLEDGE BASE (${sym}, ${backtestKB.n} stored backtests):
- ${backtestKB.n_passed}/${backtestKB.n} strategy configs passed full CPCV/DSR/PBO validation (pass rate ${Math.round((backtestKB.pass_rate || 0) * 100)}%)
- Best Deflated Sharpe on record: ${backtestKB.best_dsr != null ? Number(backtestKB.best_dsr).toFixed(2) : 'n/a'} (config "${backtestKB.best_config || '?'}"${backtestKB.best_passed ? ', which PASSED' : ', not passing'})
- Track record: ${backtestKB.edge}
PRIOR-EVIDENCE DIRECTIVE: this is the historical record of systematic strategies on this instrument. If NO config has ever passed validation, be sceptical of high-confidence systematic claims and lean conservative; if several passed, that supports a real edge here.`;
    }

    // ── Strategy Backtest Lab (in-browser, apex_strategy_backtests): exploratory ──
    let strategyBtBlock = '';
    if (strategyBT && strategyBT.n) {
      const b = strategyBT.best, c = strategyBT.conf;
      strategyBtBlock = `
STRATEGY BACKTEST LAB (${sym}, ${strategyBT.n} strategies on ${strategyBT.tfs.join('/')}, as of ${(strategyBT.dataTo || '').slice(0, 10)}, net of modelled spread):
- ${strategyBT.n_pos}/${strategyBT.n} simple/regime-filtered strategies were net-positive IN-SAMPLE.
- Best by Sharpe: ${b.strategy} on ${b.timeframe} (Sharpe ${b.sharpe}, win ${b.win_rate}%, ${b.n_trades} trades).
- MULTIPLE-TESTING CHECK (False Strategy Theorem): ${strategyBT.n_trials} strategies were tried, so the BEST Sharpe is selection-biased upward. Pure chance across ${strategyBT.n_trials} trials would yield a best Sharpe of ~${strategyBT.exp_max_sharpe}. Deflated best = ${strategyBT.deflated_best} → ${strategyBT.edge_survives ? 'the best strategy still clears the noise floor (weak positive signal)' : 'the best strategy DOES NOT exceed what random trial-and-error produces — treat as NO demonstrated edge'}.${strategyBT.has_oos ? `\n- WALK-FORWARD (out-of-sample, held-out recent data): only ${strategyBT.n_oos_held}/${strategyBT.n_oos_eligible} strategies with a usable OOS sample stayed profitable out-of-sample, and the best in-sample strategy ${strategyBT.best_oos_holds ? `DID hold up out-of-sample (OOS return ${strategyBT.best_oos_return}%) — the strongest evidence available here` : 'did NOT survive out-of-sample (its in-sample edge did not persist on unseen data)'}.` : ''}${c ? `\n- The live CONFLUENCE strategy backtests at Sharpe ${c.sharpe}, win ${c.win_rate}%, return ${c.total_return}% (${c.n_trades} trades, ${c.timeframe}).` : ''}
STRATEGY-BACKTEST DIRECTIVE: weigh OUT-OF-SAMPLE survival above in-sample headline numbers. ${strategyBT.has_oos ? (strategyBT.best_oos_holds ? 'The best strategy survived the out-of-sample hold-out — that is a genuine (if modest) supporting signal for this kind of setup. ' : 'The in-sample winner did NOT survive out-of-sample, so do NOT let the headline backtest raise confidence — treat naive trend/indicator signals here with scepticism. ') : 'No out-of-sample data yet — treat the in-sample numbers as exploratory only and weigh LIGHTLY. '}${strategyBT.edge_survives ? '' : 'The best result also fails the multiple-testing noise floor. '}Never treat this as a validated edge.`;
    }

    // ── Trade style: tailor the whole plan to the requested horizon ──
    const tradeStyleBlock = `
━━━ TRADE STYLE: ${ts.label.toUpperCase()} (hold ${ts.horizon}) ━━━
The trader wants a ${ts.label} trade. ALL technical evidence above is computed on the ${ts.primaryTf} timeframe (higher-timeframe context: ${ts.contextTf}). Tailor the ENTIRE plan to this horizon:
- WHEN TO ENTER (prefer patience — do NOT chase): default to the BEST achievable entry, NOT the current price — a pullback to support/value for a long, a rally to resistance for a short, or the retest of a confirmed breakout. Put THAT level in entry_zone and only say "enter now at market" when price is ALREADY sitting at such a level. A real edge that needs price to come to a better level is still an actionable BUY/SELL with a PENDING entry (e.g. "buy the pullback to X", "sell the rally to X", "wait for a break+retest of Y") — NOT a WAIT. Say which case it is in entry_trigger, and measure reward:risk from that entry.
- WHEN TO SELL / TAKE PROFIT: a specific take-profit target (target_price) sized for a ${ts.label} move on ${ts.primaryTf} (scalps = tight, near price; positions = wider, structure-based).
- WHERE TO STOP: a stop_loss appropriate to ${ts.primaryTf} volatility (use the ATR/levels in the evidence) with a sensible risk:reward.
- The "timeframe" field MUST be a concrete ${ts.label} holding estimate (e.g. scalp "~30 min–4 h", swing "1–3 weeks", position "1–4 months").
- Do NOT give daily-swing levels for a scalp, or scalp levels for a position trade. Match the horizon.`;

    // ── Committee Agent: synthesise all specialist findings → final JSON ──────
    const committeePrompt = `You are the head of an investment committee. Four specialist analysts have submitted structured evidence — NOT conclusions — on ${sym} (current price: ${curr.toFixed(dp)}). The analysts have also debated each other. Your job is to weigh the EVIDENCE, resolve disagreements, and deliver the final verdict for a ${ts.label.toUpperCase()} trade.
${tradeStyleBlock}
${confluenceFrameworkBlock}
${entryTfBlock}
${researchPriorsBlock}

━━━ TECHNICAL ANALYST — Evidence ━━━
${techFactors}

━━━ FUNDAMENTAL ANALYST — Evidence ━━━
${fundFactors}

━━━ MACRO STRATEGIST — Evidence ━━━
${macroFactors}

━━━ RISK MANAGER — Evidence ━━━
${riskFactors}

━━━ CROSS-CRITIQUE DEBATE ━━━
${debateBlock || 'Debate data unavailable.'}

━━━ QUANTITATIVE CONTEXT ━━━
${confluenceScore ? `Multi-Timeframe Confluence (de-correlated): ${confluenceScore.bullPct}% bullish / ${confluenceScore.bearPct}% bearish (${confluenceScore.direction}, ${confluenceScore.strength} strength, ${confluenceScore.independentSignals} independent signal families${confluenceScore.concentrated ? ' — ⚠️ CONCENTRATED in one correlated family, not genuine confluence' : ''})
CRITICAL: Confidence score should generally not exceed (Confluence% + 20) unless overwhelming evidence justifies it. Confluence of ${confluenceScore.bullPct}% → implied confidence ceiling ~${Math.min(100, confluenceScore.bullPct + 20)}%.` : ''}
${fearGreed ? `Fear & Greed: ${fearGreed.value}/100 (${fearGreed.label})` : ''}
${macroIntermarket?.yield_curve?.signal ? `Yield Curve: ${macroIntermarket.yield_curve.signal}` : ''}
${macroIntermarket?.hy_oas?.signal ? `HY Credit: ${macroIntermarket.hy_oas.signal}` : ''}
${macroIntermarket?.vix?.signal ? `VIX: ${macroIntermarket.vix.signal}` : ''}
${relStr?.rs1m != null ? `${sym} 1M RS vs ${benchName}: ${relStr.rs1m > 0 ? '+' : ''}${relStr.rs1m}%` : ''}
${qualityScores?.quality_flags?.length ? `Quality flags:\n${qualityScores.quality_flags.join('\n')}` : ''}
${eventBlock || ''}
${sessionBlock || ''}
${freshnessBlock || ''}
${sectorBlock || ''}
${positioningBlock || ''}
${cryptoBlock || ''}
${seasonalityBlock || ''}
${calibrationBlock || ''}
${metaLabelBlock || ''}
${lessonsBlock || ''}
${engineBlock}
${backtestBlock}
${strategyBtBlock}
${scanBlock || ''}
${memoryBlock || ''}
${trackRecordBlock || ''}
${anchorBlock || ''}
${positionMgmtBlock || ''}

Your task:
1. Read the EVIDENCE lists — count how many bullish vs bearish factors exist across all four analysts
2. Identify where analysts AGREE (high conviction areas) and where they CONFLICT (uncertainty areas)
3. The Risk Manager's evidence deserves EQUAL weight to bullish factors — do not dismiss risks
4. Apply the Confluence Score as a hard calibration constraint on your confidence score
5. If bullish and bearish factors are roughly balanced (4:4 or 5:5 or similar), lean toward NO_EDGE or WAIT — not HOLD${waitConstraint}
6. If quality flags are present (Beneish manipulation risk, weak F-Score), reduce confidence by 10–15 points
7. Weigh the INDEPENDENT QUANT ENGINE per its directive: a risk-layer NO POSITION or a REJECTED validation must pull confidence down materially and may turn an aggressive BUY/SELL into WAIT/NO_EDGE; agreement modestly supports the call
8. Honour the TRADE STYLE: entry_zone, stop_loss, target_price, risk_reward and timeframe must all be sized for a ${ts.label} (${ts.primaryTf}) trade, consistent with the verdict — a BUY needs a concrete entry trigger, take-profit and stop for THIS horizon
8b. ENTRY DISCIPLINE — prefer a PATIENT entry over chasing the current price. Place entry_zone at the best achievable level (pullback to support/value for longs, rally to resistance for shorts, breakout-retest otherwise); only "enter now at market" when price is ALREADY there. A directional edge that needs price to come to a better level is still an actionable BUY/SELL with a PENDING entry trigger (state the wait-for level in entry_trigger) — do NOT collapse it to WAIT just because the entry isn't live yet. Reward:risk is measured from THAT entry, which should improve the setup quality.
9. RISK:REWARD GATE (professional standard) — a professional will NOT take a ${ts.label} trade below ${styleMinRR}:1 reward:risk, where reward = |target_price − entry| and risk = |entry − stop_loss|. This is the minimum a disciplined ${ts.label} trader requires; 3:1+ is an A+ setup. If the best honest setup at this horizon cannot reach ${styleMinRR}:1, you MUST NOT output an actionable BUY/SELL/SHORT — return WAIT or NO_EDGE and state the specific entry/level that WOULD make the reward justify the risk. Do not force a sub-standard trade just to have a directional call.
10. Be brutally honest — no performance, no softening, no default verdicts
11. PRE-MORTEM (decision-quality discipline): before finalising, assume this trade has ALREADY hit its stop. Identify the single most likely reason it failed and put it in the premortem field — this surfaces the dominant risk and counters confirmation bias.
12. METHOD HONESTY: do NOT treat named discretionary chart methods (ICT, Smart Money Concepts, order blocks, fair value gaps, liquidity sweeps, Elliott Wave, harmonics, Gann) as established edge — they have no independently verified track record and are largely repackaged support/resistance & supply/demand. You may reference them, but weight price action, volume, regime and confluence ABOVE any named pattern, and never raise confidence on the strength of such a method alone.
${pmPrompt}

Respond ONLY with this exact JSON structure:

{
  "verdict": "${pmVerdictJson}",
  "confidence_level": "Low|Moderate|High|Very High",
  "confidence_score": <integer 0-100. High when specialists agree, low when they conflict. Most scores land 45-80. Only 85+ when near-unanimous bullish/bearish signals across all four agents.>,
  "executive_summary": "3-4 sentences synthesising the committee view — what is the overall picture and why",
  "macro_environment": "3-4 sentences on macro regime and specific impact on this asset",
  "macro_regime": "risk-on|risk-off|late-cycle|recessionary|expansionary|euphoric|fearful|liquidity-driven|fundamentally-driven",
  "fundamental_analysis": "3-4 sentences on fundamentals, valuation, business quality",
  "valuation": "undervalued|fairly-valued|overvalued|irrationally-priced",
  "technical_analysis": "3-4 sentences on technical picture and price structure",
  "sentiment_analysis": "2-3 sentences on positioning, crowding, contrarian signals",
  "sentiment_condition": "excessively-bullish|excessively-bearish|complacent|euphoric|fearful|neutral",
  "catalyst_analysis": "2-3 sentences on near-term catalysts and underpriced risks",
  "risk_analysis": "3-4 sentences — the bear case synthesised from risk manager findings",
  "specialist_disagreements": "1-2 sentences on where the analysts disagreed and how you resolved it",
  "scenarios": {
    "bull":    { "probability": <int>, "target": "<price>", "upside": "<pct>",   "description": "<1 sentence>" },
    "base":    { "probability": <int>, "target": "<price>", "change": "<pct>",   "description": "<1 sentence>" },
    "bear":    { "probability": <int>, "target": "<price>", "downside": "<pct>", "description": "<1 sentence>" },
    "extreme": { "probability": <int>, "target": "<price>", "downside": "<pct>", "description": "<1 sentence>" }
  },
  "expected_value": "positive|slightly-positive|neutral|slightly-negative|negative",
  "risk_reward": "<e.g. 2.5:1>",
  "short_term_outlook":  "1-2 sentences on days-to-4-week outlook",
  "medium_term_outlook": "1-2 sentences on 1-3 month outlook",
  "long_term_outlook":   "1-2 sentences on 3-12 month outlook",
  "key_reasons": ["<reason 1>", "<reason 2>", "<reason 3>", "<reason 4>"],
  "invalidation_conditions": ["<condition 1>", "<condition 2>", "<condition 3>"],
  "entry_zone": "<the BEST entry LEVEL for this setup — a pullback / rally / breakout-retest zone, NOT just the current price (use current price only when price is already at a prime entry). A tight concrete zone, never blank>",
  "entry_trigger": "WHEN/HOW to enter: e.g. 'enter at market now', 'buy the pullback to X', or 'wait for a break/close above Y then enter'. For WAIT/NO_EDGE give the conditional level that WOULD make it a valid entry.",
  "stop_loss": "<concrete stop-loss price — never blank>",
  "target_price": "<first take-profit price, TP1 — never blank>",
  "take_profit_2": "<second take-profit / runner price, TP2 (use the same as TP1 if you only have one target)>",
  "entry_strategy": "How and when to build the position",
  "position_sizing": "Recommended sizing relative to portfolio and why",
  "stop_loss_logic": "Why this stop level and what it protects against",
  "profit_taking_logic": "When and how to take profits, scaling strategy",
  "hedging_considerations": "Any hedges worth considering",
  "timeframe": "<recommended holding period>",
  "what_would_change_view": "What specific development would flip this thesis",
  "why_confidence_not_higher": "What uncertainty or disagreement prevents higher confidence",
  "premortem": "PRE-MORTEM — assume the trade already hit its stop: in ONE sentence, the single most likely reason it failed (the dominant risk to this thesis)."
}`;

    // 6000 tokens: the committee JSON is large (full multi-section report + 4
    // scenarios + strategy), so a smaller budget TRUNCATES it → JSON parse failure.
    // The 504s that 6000 used to cause came from the Edge wall-clock limit, which is
    // now fixed by running /api/ai on the Node runtime with a raised maxDuration.
    // Parse can still occasionally fail if a model returns malformed/truncated JSON,
    // so we retry the call once before surfacing an error (a fresh call usually lands
    // clean JSON — different model in the chain / less verbose generation).
    // ENSEMBLE the final verdict across genuinely-different models (manual scans) for
    // diversity; the bulk auto-scan stays single-model for budget. Graceful: if a
    // provider is down its member drops out and we use whoever answered.
    const _ensembleN = _autoScan ? ENSEMBLE_SIZE.auto : ENSEMBLE_SIZE.manual;
    const _members = await runCommitteeEnsemble(systemPrompt, committeePrompt, _ensembleN);
    if (!_members.length) throw new Error('AI returned an unexpected response format. Please try again.');
    let analysis = combineEnsemble(_members);
    // Store specialist notes on the analysis object for display
    analysis._specialists = { technical: techRawText, fundamental: fundRawText, macro: macroRawText, risk: sentRawText };
    // Persist the model-agreement summary onto the saved row (setup_features JSONB).
    if (analysis._ensemble && setupFeatures) setupFeatures.ensemble = { score: analysis._ensemble.score, agree: analysis._ensemble.agree, n: analysis._ensemble.n, unanimous: analysis._ensemble.unanimous, models: analysis._ensemble.models };

    // Deterministic risk:reward from the ACTUAL levels (the LLM's own string is
    // frequently inconsistent with its entry/stop/target). Overrides it everywhere
    // — render + saved memory — and grades against the style's professional floor.
    const _rr = computeRR(analysis.entry_zone, analysis.stop_loss, analysis.target_price, styleMinRR);
    if (_rr) {
      analysis.risk_reward = _rr.text; analysis._rr_ratio = _rr.ratio;
      analysis._rr_weak = _rr.weak; analysis._rr_min = styleMinRR; analysis._rr_aplus = _rr.aPlus;
      // PROFESSIONAL GATE: an actionable directional call below the style's minimum
      // reward:risk is not a trade a pro would take — downgrade it to WAIT (the
      // directional read may be right, but this entry doesn't pay enough for the risk).
      if (_rr.weak && verdictDir(analysis.verdict) !== 'neutral') {
        analysis._downgraded_from = analysis.verdict;
        analysis.verdict = 'WAIT';
        analysis.confidence_score = Math.min(Number(analysis.confidence_score) || 50, 48);
        analysis._rr_downgrade = `Auto-downgraded to WAIT — reward-to-risk is only ${_rr.text}, below the ${styleMinRR}:1 a professional requires for a ${ts.label} trade. The directional bias (${(analysis._downgraded_from || '').replace(/_/g, ' ')}) may be correct, but the entry here doesn't pay enough for the risk. Wait for a better price or a wider, structurally-sound target.`;
      }
    }

    // VALIDATION re-check (History "Update"): re-run the scan but DON'T create or
    // refresh a trade — attach a validity record to the original trade instead.
    const _isValidate = _validateMode && _validateTarget && _validateTarget.symbol === sym;
    if (_isValidate) {
      saveValidation(_validateTarget, analysis, curr);
    } else {
      // Same OPEN setup → refresh the existing history row; otherwise a new idea.
      // EXCEPTION: a legacy "update" is non-destructive — writes a NEW dated row.
      const _priorOpen = (Array.isArray(tickerMemory) ? tickerMemory : []).find(r => r.outcome === 'pending') || null;
      const _isUpdateScan = _updateMode && _compareOriginal && _compareOriginal.symbol === sym;
      const _updateId  = (!_isUpdateScan && _priorOpen && sameSetup(_priorOpen, analysis)) ? _priorOpen.id : null;
      saveToMemory(sym, type, analysis, curr, _updateId, setupFeatures);
    }
    markScanned(sym);

    renderResults({ sym, type, candles, weeklyCandles, quote, news, analysis, historicalScan, newsImpact, fibExt, tickerMemory, fearGreed, relStr, benchName, volProfile, adx, bbWidth, confluenceScore, macroIntermarket, qualityScores, engineData, positioning, seasonality, calibration, metaLabel, cryptoDerivs });

    // Banner: validity re-check, or the legacy compare/update banner.
    if (_isValidate) {
      showValidationBanner(_validateTarget, { ...analysis, price: curr });
      _validateTarget = null; _validateMode = false;
    } else if (_compareOriginal && _compareOriginal.symbol === sym) {
      showCompareBanner(_compareOriginal, { ...analysis, price: curr }, _updateMode);
      _compareOriginal = null;
      _updateMode = false;
    }

    document.getElementById('analyseBtn').disabled = false;

  } catch (err) {
    showError(err.message || 'An unexpected error occurred. Please try again.');
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

// ── Quick picks data ──────────────────────────────────────────────────────────
const QUICK_PICKS = {
  trending: [
    { s: 'NVDA',  n: 'NVIDIA' },     { s: 'AAPL',  n: 'Apple' },
    { s: 'MSFT',  n: 'Microsoft' },   { s: 'META',  n: 'Meta' },
    { s: 'AMZN',  n: 'Amazon' },      { s: 'GOOGL', n: 'Alphabet' },
    { s: 'TSLA',  n: 'Tesla' },       { s: 'AMD',   n: 'AMD' },
    { s: 'PLTR',  n: 'Palantir' },    { s: 'TSM',   n: 'TSMC' },
    { s: 'NFLX',  n: 'Netflix' },     { s: 'UBER',  n: 'Uber' },
  ],
  crypto: [
    { s: 'BTC/USD',  n: 'Bitcoin' },   { s: 'ETH/USD',  n: 'Ethereum' },
    { s: 'SOL/USD',  n: 'Solana' },    { s: 'BNB/USD',  n: 'BNB' },
    { s: 'XRP/USD',  n: 'Ripple' },    { s: 'ADA/USD',  n: 'Cardano' },
    { s: 'AVAX/USD', n: 'Avalanche' }, { s: 'DOGE/USD', n: 'Dogecoin' },
    { s: 'MATIC/USD',n: 'Polygon' },   { s: 'LINK/USD', n: 'Chainlink' },
    { s: 'ARB/USD',  n: 'Arbitrum' },  { s: 'SUI/USD',  n: 'Sui' },
  ],
  forex: [
    { s: 'EUR/USD', n: 'Euro / Dollar' },  { s: 'GBP/USD', n: 'Cable' },
    { s: 'USD/JPY', n: 'Dollar / Yen' },   { s: 'USD/CHF', n: 'Swissy' },
    { s: 'AUD/USD', n: 'Aussie' },         { s: 'USD/CAD', n: 'Loonie' },
    { s: 'NZD/USD', n: 'Kiwi' },           { s: 'GBP/JPY', n: 'Guppy' },
    { s: 'EUR/GBP', n: 'Euro / Pound' },   { s: 'EUR/JPY', n: 'Euro / Yen' },
  ],
  etfs: [
    { s: 'SPY',  n: 'S&P 500' },      { s: 'QQQ',  n: 'NASDAQ 100' },
    { s: 'IWM',  n: 'Russell 2000' }, { s: 'GLD',  n: 'Gold' },
    { s: 'TLT',  n: '20yr Treasury' },{ s: 'XLK',  n: 'Tech Sector' },
    { s: 'XLE',  n: 'Energy Sector' },{ s: 'XLF',  n: 'Financials' },
    { s: 'ARKK', n: 'ARK Innovation'},{ s: 'SMH',  n: 'Semiconductors'},
    { s: 'SOXX', n: 'Semis (SOXX)' }, { s: 'XBI',  n: 'Biotech' },
  ],
};

function renderQuickPicks(cat) {
  const grid = document.getElementById('qpGrid');
  if (!grid) return;
  const items = QUICK_PICKS[cat] || [];
  grid.innerHTML = items.map(({ s, n }) =>
    `<button class="qp-btn" onclick="quickPick('${s}')">
      <span class="qp-sym">${s}</span>
      <span class="qp-name">${n}</span>
    </button>`
  ).join('');
}

function initQuickPicks() {
  renderQuickPicks('trending');
  document.querySelectorAll('.qp-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.qp-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      renderQuickPicks(tab.dataset.cat);
    });
  });
}

// ── Symbol autocomplete ───────────────────────────────────────────────────────
let _acTimer = null;
let _acActive = -1; // keyboard nav index

function closeDropdown() {
  const dd = document.getElementById('symDropdown');
  if (dd) { dd.innerHTML = ''; dd.classList.remove('open'); }
  _acActive = -1;
}

function renderDropdown(results) {
  const dd = document.getElementById('symDropdown');
  if (!dd) return;
  if (!results.length) { closeDropdown(); return; }

  const typeColor = { Stock:'stock', ETF:'etf', Crypto:'crypto', Forex:'forex', REIT:'stock', ADR:'stock' };

  dd.innerHTML = results.map((r, i) =>
    `<div class="dd-item" data-idx="${i}" data-sym="${r.symbol}" onmousedown="event.preventDefault();quickPick('${r.symbol}')">
      <span class="dd-sym">${r.symbol}</span>
      <span class="dd-name">${r.name}</span>
      <span class="dd-type ${typeColor[r.type] || 'stock'}">${r.type}</span>
    </div>`
  ).join('');
  dd.classList.add('open');
  _acActive = -1;
}

function highlightItem(idx) {
  const items = document.querySelectorAll('#symDropdown .dd-item');
  items.forEach((el, i) => el.classList.toggle('focused', i === idx));
}

async function doSearch(q) {
  if (!q || q.length < 1) { closeDropdown(); return; }
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    if (!res.ok) return;
    const data = await res.json();
    renderDropdown(data);
  } catch {}
}

function initAutocomplete() {
  const inp = document.getElementById('symInput');
  if (!inp) return;

  inp.addEventListener('input', () => {
    updateTypePill(inp.value);
    clearTimeout(_acTimer);
    const q = inp.value.trim();
    if (!q) { closeDropdown(); return; }
    _acTimer = setTimeout(() => doSearch(q), 200);
  });

  inp.addEventListener('keydown', e => {
    const items = document.querySelectorAll('#symDropdown .dd-item');
    const open  = items.length > 0;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) return;
      _acActive = Math.min(_acActive + 1, items.length - 1);
      highlightItem(_acActive);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _acActive = Math.max(_acActive - 1, -1);
      highlightItem(_acActive);
    } else if (e.key === 'Enter') {
      if (open && _acActive >= 0) {
        const sym = items[_acActive].dataset.sym;
        quickPick(sym);
      } else {
        closeDropdown();
        startResearch();
      }
    } else if (e.key === 'Escape') {
      closeDropdown();
    }
  });

  // Close dropdown when clicking outside
  document.addEventListener('click', e => {
    if (!e.target.closest('#searchField')) closeDropdown();
  });
}

// ── Comparison banner (shown when arriving from History with ?compare=ID) ──────
let _compareOriginal = null; // holds the historical row for comparison
let _updateMode = false;     // arrived via ?update=ID — preserve the original trade (legacy)
let _autoScan = false;       // arrived via ?auto=1 — bot scan (auto-scan workflow)
let _controlArm = false;     // arrived via ?control=1 — DIRECTIVE-BLIND scan: the
                             // calibration/meta-label/lessons feedback blocks are NOT
                             // injected into the committee prompt. ~15% of auto-scans
                             // run blind as a permanent control arm: it (a) measures
                             // whether the learning loops actually help (A/B on
                             // resolved outcomes) and (b) stops the k-NN feedback loop
                             // from entrenching its own early noise (cold-start
                             // self-fulfillment). The HARD post-hoc confidence remap
                             // still applies — it lives outside the LLM.
let _validateMode = false;   // arrived via ?validate=ID — re-check an existing trade
let _validateTarget = null;  // the original trade row being re-validated

function showCompareBanner(original, fresh, isUpdate = false) {
  let el = document.getElementById('compareBanner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'compareBanner';
    el.className = 'compare-banner';
    document.getElementById('resultsSection').prepend(el);
  }

  const priceThen = parseFloat(original.price);
  const priceNow  = parseFloat(fresh.price || 0);
  const priceDiff = priceThen && priceNow ? ((priceNow - priceThen) / priceThen * 100).toFixed(2) : null;
  const confDiff  = (fresh.confidence_score || 0) - (original.confidence || 0);
  const verdictChanged = original.verdict !== fresh.verdict;
  const outcomeIcon   = original.outcome === 'tp_hit' ? '✅' : original.outcome === 'sl_hit' ? '❌' : original.outcome === 'expired' ? '⏱️' : '⏳';
  const title = isUpdate
    ? '🔄 Confidence Update — your saved trade vs a fresh scan'
    : '📊 Comparison — Previous scan vs Today';
  const note = isUpdate
    ? '<div class="cb-note">Your original trade is kept intact in History — this fresh read was added alongside it.</div>'
    : '';

  el.innerHTML = `
    <div class="cb-title">${title}</div>
    ${note}
    <div class="cb-grid">
      <div class="cb-col">
        <div class="cb-label">${isUpdate ? 'Saved trade' : 'Previous scan'}</div>
        <div class="cb-date">${original.analysis_date}</div>
        <div class="cb-verdict ${original.verdict?.toLowerCase().replace(/_/g,'-')}">${original.verdict}</div>
        <div class="cb-conf">${original.confidence}% confidence</div>
        <div class="cb-price">@ $${original.price}</div>
        <div class="cb-outcome">${outcomeIcon} ${original.outcome?.replace(/_/g,' ') || 'pending'}</div>
      </div>
      <div class="cb-arrow">→</div>
      <div class="cb-col">
        <div class="cb-label">${isUpdate ? 'Fresh scan (just now)' : "Today's scan"}</div>
        <div class="cb-date">${new Date().toISOString().slice(0,10)}</div>
        <div class="cb-verdict ${fresh.verdict?.toLowerCase().replace(/_/g,'-')}">${fresh.verdict}</div>
        <div class="cb-conf">${fresh.confidence_score}% confidence ${confDiff !== 0 ? `<span class="${confDiff>0?'pos':'neg'}">(${confDiff>0?'+':''}${confDiff}%)</span>` : ''}</div>
        <div class="cb-price">@ $${priceNow > 0 ? priceNow.toFixed(2) : '—'}</div>
        ${priceDiff != null ? `<div class="cb-pricediff ${Number(priceDiff)>=0?'pos':'neg'}">Price ${Number(priceDiff)>=0?'+':''}${priceDiff}% since original scan</div>` : ''}
      </div>
    </div>
    ${verdictChanged ? `<div class="cb-change-alert">⚡ Verdict changed: ${original.verdict} → ${fresh.verdict}</div>` : '<div class="cb-same">Verdict unchanged</div>'}
    <button class="cb-close" onclick="this.closest('.compare-banner').remove()">Dismiss</button>
  `;
}

// ── Validity re-check (History "Update") ──────────────────────────────────────
// Re-runs the scan against an existing trade and judges whether the original call
// still holds — WITHOUT creating a new trade. Combines the fresh committee read
// (same side? confidence up/down?) with objective price progress (how far toward the
// target vs the stop since entry). Each record is appended to the trade's
// `validations` array as research the learning loop can later mine.

// How far price has travelled from entry toward the target (positive) vs the stop.
function validationProgress(target, curr) {
  const eb = entryBounds(target.entry_zone);
  const entry = eb ? (eb.lo + eb.hi) / 2 : parseFloat(target.price);
  const tp = parseFloat(target.target_price), sl = parseFloat(target.stop_loss);
  const dir = verdictDir(target.verdict);
  if (isNaN(entry) || isNaN(tp) || isNaN(sl) || dir === 'neutral') return null;
  let toTarget, toStop;
  if (dir === 'short') { toTarget = (entry - curr) / (entry - tp); toStop = (curr - entry) / (sl - entry); }
  else                 { toTarget = (curr - entry) / (tp - entry); toStop = (entry - curr) / (entry - sl); }
  const clamp = x => Math.round(Math.max(0, Math.min(1, x)) * 100);
  if (toTarget >= 0 && toTarget >= toStop) return { pct: clamp(toTarget), toward: 'target' };
  if (toStop > 0) return { pct: clamp(toStop), toward: 'stop' };
  return { pct: 0, toward: 'target' };
}

// Position-management verdicts map to standard assessments so learning loops still work.
const PM_VERDICT_ASSESSMENT = {
  HOLD_TRADE:        'confirmed',
  MOVE_TO_BREAKEVEN: 'weakening',
  TIGHTEN_STOP:      'confirmed',    // still in trade, but protecting gains
  SCALE_OUT:         'weakening',    // partial exit — thesis losing steam
  CLOSE_TRADE:       'invalidated',  // exit entirely
};
function buildValidation(target, analysis, curr) {
  const origDir = verdictDir(target.verdict);
  const freshDir = verdictDir(analysis.verdict);
  const v = (analysis.verdict || '').toUpperCase();
  let assessment;
  // If a position-management verdict was returned, use its direct mapping.
  if (PM_VERDICT_ASSESSMENT[v]) {
    assessment = PM_VERDICT_ASSESSMENT[v];
  } else if (origDir === 'neutral') {
    assessment = freshDir !== 'neutral' ? 'activated' : 'still-waiting';
  } else if (freshDir === origDir)    assessment = 'confirmed';
  else if (freshDir === 'neutral')    assessment = 'weakening';
  else                                assessment = 'invalidated';
  const prog = validationProgress(target, curr);
  return {
    ts: new Date().toISOString(),
    verdict: analysis.verdict,
    confidence: analysis.confidence_score ?? null,
    confidenceThen: target.confidence ?? null,
    price: +(+curr).toFixed(5),
    assessment,
    progressPct: prog ? prog.pct : null,
    progressToward: prog ? prog.toward : null,
  };
}

// Read-modify-write the trade's validations array (single-user; races negligible).
async function saveValidation(target, analysis, curr) {
  const rec = buildValidation(target, analysis, curr);
  try {
    // By-id lookup: at 200 scans/week an open trade can be far older than any
    // recent-rows window, so never search for it in a limited list.
    const rows = await fetch(`/api/memory?id=${encodeURIComponent(target.id)}`).then(r => r.json()).catch(() => null);
    let vals = [];
    const row = Array.isArray(rows) ? rows.find(r => r.id === target.id) : null;
    let v = row ? row.validations : null;
    if (typeof v === 'string') { try { v = JSON.parse(v); } catch { v = null; } }
    if (Array.isArray(v)) vals = v;
    vals.push(rec);
    const patchBody = { id: target.id, validations: vals };
    if (analysis.verdict === 'CLOSE_TRADE') {
      patchBody.outcome = 'invalidated';
      patchBody.outcome_date = new Date().toISOString();
    }
    fetch('/api/memory', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patchBody),
    }).catch(() => {});
  } catch {}
}

// Learned track record of each re-check assessment: across resolved trades that were
// re-checked, how the LAST re-check's assessment lined up with the actual outcome.
function computeValidationReliability(rows) {
  const b = { confirmed: { tp: 0, sl: 0 }, weakening: { tp: 0, sl: 0 }, invalidated: { tp: 0, sl: 0 } };
  for (const r of (rows || [])) {
    if (r.outcome !== 'tp_hit' && r.outcome !== 'sl_hit') continue;
    let vs = r.validations;
    if (typeof vs === 'string') { try { vs = JSON.parse(vs); } catch { vs = null; } }
    if (!Array.isArray(vs) || !vs.length) continue;
    const a = vs[vs.length - 1].assessment;
    if (!b[a]) continue;
    if (r.outcome === 'tp_hit') b[a].tp++; else b[a].sl++;
  }
  const out = {};
  for (const k of ['confirmed', 'weakening', 'invalidated']) {
    const n = b[k].tp + b[k].sl;
    out[k] = { n, slRate: n ? Math.round(b[k].sl / n * 100) : null, tpRate: n ? Math.round(b[k].tp / n * 100) : null };
  }
  return out;
}

async function showValidationBanner(target, fresh) {
  let el = document.getElementById('compareBanner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'compareBanner';
    el.className = 'compare-banner';
    document.getElementById('resultsSection').prepend(el);
  }
  const rec = buildValidation(target, fresh, parseFloat(fresh.price));
  // Learned track record for this kind of re-check (dormant until enough resolved).
  let trackNote = '';
  try {
    const rows = await fetch('/api/memory?all=true&resolved=true&lean=true&limit=1000').then(r => r.json());
    const vr = computeValidationReliability(rows);
    const s = vr[rec.assessment];
    if (s && s.n >= 4) {
      const good = rec.assessment === 'confirmed';
      trackNote = `<div class="vb-prog ${good ? 'pos' : 'neg'}">📊 Track record: past <strong>${rec.assessment}</strong> re-checks went on to hit the ${good ? `target ${s.tpRate}%` : `stop ${s.slRate}%`} of the time (n=${s.n})</div>`;
    }
  } catch {}
  // Use the raw position-management verdict as the label when it's a PM re-check;
  // fall back to the generic assessment labels for classic re-checks.
  const PM_LABELS = {
    HOLD_TRADE:        '✅ HOLD TRADE — stay in',
    MOVE_TO_BREAKEVEN: '🛡 MOVE TO BREAKEVEN — protect capital',
    TIGHTEN_STOP:      '📐 TIGHTEN STOP — lock in gains',
    SCALE_OUT:         '📤 SCALE OUT — take partial profits',
    CLOSE_TRADE:       '🚪 CLOSE TRADE — exit now',
  };
  const rawVerdict = (fresh.verdict || '').toUpperCase();
  const pmLabel = PM_LABELS[rawVerdict];
  const LABEL = { confirmed: '✅ STILL VALID', weakening: '⚠️ WEAKENING', invalidated: '❌ INVALIDATED', activated: '🟢 NOW ACTIONABLE', 'still-waiting': '⏳ STILL WAITING', 'n/a': '🔁 RE-CHECKED' };
  const CLS   = { confirmed: 'pos', weakening: 'warn', invalidated: 'neg', activated: 'pos', 'still-waiting': '', 'n/a': '' };
  const confThen = target.confidence != null ? target.confidence + '%' : '—';
  const confNow  = fresh.confidence_score != null ? fresh.confidence_score + '%' : '—';
  const progLine = rec.progressPct != null
    ? `<div class="vb-prog ${rec.progressToward === 'target' ? 'pos' : 'neg'}">📈 Price has moved <strong>${rec.progressPct}%</strong> of the way toward the ${rec.progressToward} since entry</div>`
    : '';
  el.innerHTML = `
    <div class="cb-title">🔁 Validity Re-check — is this trade still good?</div>
    <div class="cb-note">Your original trade is unchanged. This re-check was saved as research; it does not create a new trade.</div>
    <div class="vb-verdict ${CLS[rec.assessment] || ''}">${LABEL[rec.assessment] || '🔁 RE-CHECKED'}</div>
    <div class="vb-row">Original call: <strong>${(target.verdict || '').replace(/_/g, ' ')}</strong> @ ${confThen} (${escapeAttr(target.analysis_date || '')}) → fresh read: <strong>${(fresh.verdict || '').replace(/_/g, ' ')}</strong> @ ${confNow}</div>
    ${progLine}
    ${trackNote}
    <button class="cb-close" onclick="this.closest('.compare-banner').remove()">Dismiss</button>
  `;
}
function escapeAttr(s) { return String(s).replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c])); }

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initPulse();
  initQuickPicks();
  initAutocomplete();
  loadNavWinRate();   // overall realised win-rate badge in the nav
  document.getElementById('analyseBtn').addEventListener('click', () => {
    closeDropdown();
    startResearch();
  });

  // Trade-style selector
  const tsPills = document.getElementById('tradeStylePills');
  if (tsPills) {
    tsPills.querySelectorAll('.ts-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        tsPills.querySelectorAll('.ts-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        _tradeStyle = btn.dataset.style;
        updateStyleLagNotice();
      });
    });
  }

  // Handle ?sym=NVDA URL param (launched from History page)
  const params = new URLSearchParams(window.location.search);
  const symParam = params.get('sym');
  if (symParam) {
    const inp = document.getElementById('symInput');
    inp.value = symParam.toUpperCase();
    updateTypePill(symParam);
    loadPreAnalysis(symParam, detectType(symParam));   // show flags/TradingView for the launched symbol
  }

  // ?style= (from the History "Update" link) → preselect the trade's OWN style so the
  // re-check runs on the right timeframe (e.g. intraday, not the default swing).
  const styleParam = (params.get('style') || '').toLowerCase();
  if (styleParam && TRADE_STYLES[styleParam] && tsPills) {
    _tradeStyle = styleParam;
    tsPills.querySelectorAll('.ts-pill').forEach(b => b.classList.toggle('active', b.dataset.style === styleParam));
  }
  updateStyleLagNotice();   // reflect the (possibly preselected) style

  // Handle ?validate=ID / ?compare=ID / ?update=ID (re-scan launched from History).
  //  • validate → re-check the EXISTING trade: run a fresh scan but DON'T create a new
  //    trade; attach a validation record (still valid / weakening / invalidated) to the
  //    original and save it as research. This is the current History "Update" button.
  //  • update/compare → legacy paths (new dated row / before-after banner).
  if (params.get('auto') === '1') _autoScan = true;   // bot scan from the auto-scan workflow
  if (params.get('control') === '1') _controlArm = true;   // directive-blind control-arm scan
  const validateId = params.get('validate');
  const compareId  = params.get('compare');
  const updateId   = params.get('update');
  const loadId     = validateId || updateId || compareId;
  if (validateId) _validateMode = true;
  if (updateId)   _updateMode = true;
  if (loadId) {
    fetch(`/api/memory?id=${encodeURIComponent(loadId)}`)
      .then(r => r.json())
      .then(rows => {
        const row = rows.find(r => r.id === loadId);
        // A FINISHED trade (TP/SL hit) is frozen — never re-validate/update it. If a
        // resolved id is passed (e.g. a stale History page), drop validate/update mode
        // so this becomes a normal scan that opens a NEW, separate trade for the pair.
        if (row && (row.outcome === 'tp_hit' || row.outcome === 'sl_hit')) {
          _validateMode = false; _updateMode = false;
        } else if (row) {
          _compareOriginal = row; _validateTarget = row;
        }
      })
      .catch(() => {})
      // Signal the headless auto-scan that the validate target is loaded, so it never
      // clicks Analyse before _validateTarget is set (which would create a new trade).
      .finally(() => { try { window.__apexValidateReady = true; } catch {} });
  } else {
    try { window.__apexValidateReady = true; } catch {}
  }
});
