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

// Saves a completed analysis to Supabase (fire-and-forget — don't await in UI flow)
function saveToMemory(sym, type, analysis, price) {
  fetch('/api/memory', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbol:       sym,
      asset_type:   type,
      price:        +price.toFixed(4),
      verdict:      analysis.verdict,
      confidence:   analysis.confidence_score,
      target_price: analysis.target_price  || null,
      entry_zone:   analysis.entry_zone    || null,
      stop_loss:    analysis.stop_loss     || null,
      risk_reward:  analysis.risk_reward   || null,
      summary:      (analysis.executive_summary || '').slice(0, 500),
    }),
  }).catch(() => {}); // silent fail — memory is best-effort
}

// Resolve outcomes of pending analyses using actual candle data.
// Called after candles are fetched — checks if TP or SL was hit since analysis date.
function resolveOutcomes(pendingRows, candles) {
  pendingRows.forEach(row => {
    if (row.outcome !== 'pending' || !row.analysis_date) return;
    const analysisTs = new Date(row.analysis_date).getTime() / 1000;
    // Only look at bars after the analysis date
    const barsAfter = candles.filter(b => b.time > analysisTs);
    if (!barsAfter.length) return;

    const tp  = parseFloat(row.target_price);
    const sl  = parseFloat(row.stop_loss);
    const ageMs = Date.now() - new Date(row.analysis_date).getTime();
    const ageDays = ageMs / 86400000;

    let outcome = null, outcomePrice = null;

    if (!isNaN(tp) && !isNaN(sl)) {
      for (const b of barsAfter) {
        if (b.high >= tp)  { outcome = 'tp_hit'; outcomePrice = tp;  break; }
        if (b.low  <= sl)  { outcome = 'sl_hit'; outcomePrice = sl;  break; }
      }
    }
    if (!outcome && ageDays > 30) outcome = 'expired';
    if (!outcome) return; // still genuinely pending

    // PATCH outcome back to Supabase (fire-and-forget)
    fetch('/api/memory', {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id:            row.id,
        outcome,
        outcome_price: outcomePrice,
        outcome_date:  new Date().toISOString().slice(0, 10),
      }),
    }).catch(() => {});

    // Update row locally so the UI reflects it immediately
    row.outcome = outcome;
    row.outcome_price = outcomePrice;
  });
}

// ── API calls ─────────────────────────────────────────────────────────────────

async function fetchCandles(sym, type) {
  const to = Math.floor(Date.now() / 1000), from = to - 210 * 86400;
  const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
  if (!r.ok) throw new Error(`Price data unavailable (HTTP ${r.status})`);
  const d = await r.json();
  if (d.error) throw new Error(d.error);
  return d;
}
async function fetchWeeklyCandles(sym, type) {
  try {
    const to = Math.floor(Date.now() / 1000), from = to - 2 * 365 * 86400;
    const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1w&from=${from}&to=${to}`);
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
    const to = Math.floor(Date.now() / 1000), from = to - 40 * 86400;
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

// ── Multi-agent AI call ───────────────────────────────────────────────────────
// Calls /api/ai with a focused prompt. Returns the text or throws.
async function callAgent(system, prompt, maxTokens = 2500) {
  const res = await fetch('/api/ai', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt,
      system,
      max_tokens:  maxTokens,
      temperature: 0.3,
      timeoutMs:   55000,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) {
    // Surface rate-limit errors properly
    if (res.status === 429 || (data.retryAfterMs)) {
      const mins = data.retryAfterMs ? Math.ceil(data.retryAfterMs / 60000) : null;
      throw new Error(mins
        ? `AI rate limit reached. Resets in ~${mins} min. Get a free GEMINI_API_KEY at aistudio.google.com to avoid this.`
        : (data.error || 'AI rate limit reached.'));
    }
    throw new Error(data.error || `Agent error HTTP ${res.status}`);
  }
  return data.text || '';
}

// ── Market pulse ──────────────────────────────────────────────────────────────

async function loadPulse(sym, type, elId) {
  try {
    const to = Math.floor(Date.now() / 1000), from = to - 5 * 86400;
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
  ['loadingSection', 'errorSection', 'resultsSection'].forEach(s => {
    document.getElementById(s).style.display = s === id ? '' : 'none';
  });
}
function hideAll() {
  ['loadingSection', 'errorSection', 'resultsSection'].forEach(s => {
    document.getElementById(s).style.display = 'none';
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
  startResearch();
}
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ── Render ────────────────────────────────────────────────────────────────────

function renderResults({ sym, type, candles, weeklyCandles, quote, news, analysis: a, historicalScan, newsImpact, fibExt, tickerMemory }) {
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
  vbadge.textContent = rawVerdict.replace(/_/g, ' ');
  vbadge.className   = `verdict-badge ${rawVerdict.toLowerCase()}`;

  const conf = Math.min(100, Math.max(0, Number(a.confidence_score) || 50));
  setText('confPct', `${conf}%`);
  const fill = document.getElementById('confFill');
  fill.style.width = '0%';
  fill.className = `conf-fill ${rawVerdict.toLowerCase()}`;
  requestAnimationFrame(() => setTimeout(() => { fill.style.width = `${conf}%`; }, 80));

  setText('vcThesis',      a.executive_summary || '');
  setText('confNotHigher', a.why_confidence_not_higher ? `Why not higher: ${a.why_confidence_not_higher}` : '');

  // ── Stats grid ──
  const rsi    = calcRSI(closes);
  const sma20  = calcSMA(closes, 20);
  const sma50  = calcSMA(closes, 50);
  const sma200 = calcSMA(closes, 200);
  const { supports, resistances } = findPivotSR(candles);
  const atr    = calcATR(candles);
  const chg7d  = closes.length > 7  ? (curr - closes[closes.length - 8])  / closes[closes.length - 8]  * 100 : null;
  const chg30d = closes.length > 30 ? (curr - closes[closes.length - 31]) / closes[closes.length - 31] * 100 : null;

  document.getElementById('statsGrid').innerHTML = [
    { l: 'RSI (14)',    v: rsi  != null ? String(rsi)     : '—', c: rsi  ? (rsi > 70  ? 'down' : rsi < 30  ? 'up' : 'neutral') : '' },
    { l: '7-Day',      v: chg7d  != null ? fmtPct(chg7d)  : '—', c: chg7d  != null ? (chg7d  >= 0 ? 'up' : 'down') : '' },
    { l: '30-Day',     v: chg30d != null ? fmtPct(chg30d) : '—', c: chg30d != null ? (chg30d >= 0 ? 'up' : 'down') : '' },
    { l: 'Support',    v: supports[0]    != null ? fmtPrice(supports[0],    type) : '—', c: '' },
    { l: 'Resistance', v: resistances[0] != null ? fmtPrice(resistances[0], type) : '—', c: '' },
    { l: 'ATR (14)',   v: atr != null ? fmtPrice(atr, type) : '—', c: '' },
  ].map(s => `<div class="stat-item"><div class="stat-label">${s.l}</div><div class="stat-value ${s.c}">${s.v}</div></div>`).join('');

  // ── Macro ──
  const reg = (a.macro_regime || '').toLowerCase().replace(/[_ ]/g, '-');
  const rb = document.getElementById('regimeBadge');
  rb.textContent = a.macro_regime ? a.macro_regime.replace(/-/g, ' ').toUpperCase() : '';
  rb.className = `regime-badge ${reg}`;
  setText('macroText', a.macro_environment || '');

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
  document.getElementById('indicatorsStrip').innerHTML = chips.map(c =>
    `<div class="ind-chip"><span class="ic-lbl">${c.l}</span><span class="ic-val ${c.c}">${c.v}</span></div>`
  ).join('');

  setText('techText', a.technical_analysis || '');

  // ── Fundamental ──
  const fundTitle = type === 'Forex' ? 'Macro & FX Context' : type === 'Crypto' ? 'On-Chain & Market Context' : 'Fundamental Analysis';
  setText('fundTitle', fundTitle);
  const val = (a.valuation || '').toLowerCase().replace(/ /g, '-');
  const vb  = document.getElementById('valuationBadge');
  vb.textContent = a.valuation ? a.valuation.replace(/-/g, ' ').toUpperCase() : '';
  vb.className   = `valuation-badge ${val}`;

  if (quote && type === 'Stock') {
    document.getElementById('fundGrid').innerHTML = [
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

  // ── Trade levels ──
  document.getElementById('tradeLevels').innerHTML = `
    <div class="trade-level"><div class="tl-label">Entry Zone</div><div class="tl-value entry">${a.entry_zone  || '—'}</div></div>
    <div class="trade-level"><div class="tl-label">Stop Loss</div><div class="tl-value stop">${a.stop_loss   || '—'}</div></div>
    <div class="trade-level"><div class="tl-label">Target Price</div><div class="tl-value target">${a.target_price || '—'}</div></div>
    <div class="trade-level"><div class="tl-label">Risk : Reward</div><div class="tl-value rr">${a.risk_reward  || '—'}</div></div>`;

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

  setText('tradeTf', a.timeframe ? `Suggested holding period: ${a.timeframe}` : '');

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
  setTimeout(() => document.getElementById('resultsSection').scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
}

// ── Main research flow ────────────────────────────────────────────────────────

async function startResearch() {
  const raw = document.getElementById('symInput').value.trim();
  if (!raw) { document.getElementById('symInput').focus(); return; }

  const sym = raw.toUpperCase(), type = detectType(sym);

  document.getElementById('analyseBtn').disabled = true;
  document.getElementById('loaderSym').textContent = sym;
  showSection('loadingSection');
  for (let i = 1; i <= 5; i++) document.getElementById(`ls${i}`).className = 'loader-step';

  // Update loader labels
  [
    'Fetching multi-timeframe price history',
    'Computing 15+ technical indicators',
    'Pulling fundamentals, news, macro & memory',
    'Running 4 specialist AI agents in parallel…',
    'Investment committee synthesising final verdict',
  ].forEach((t, i) => { const el = document.getElementById(`ls${i + 1}`); if (el) el.textContent = t; });

  setStep(1);

  try {
    // ── Step 1: Daily + weekly candles in parallel ──
    const [candles, weeklyCandles] = await Promise.all([
      fetchCandles(sym, type),
      fetchWeeklyCandles(sym, type),
    ]);
    if (!candles || candles.length < 30) throw new Error(`No price data found for "${sym}". Check the symbol and try again.`);
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

    setStep(3);

    // ── Step 3: News, fundamentals, macro context + memory — all in parallel ──
    const [news, quote, macroCtx, supaMemory] = await Promise.all([
      fetchNews(sym, type),
      ['Stock', 'ETF'].includes(type) ? fetchQuote(sym, type) : Promise.resolve(null),
      fetchMacroContext(sym),
      fetchTickerMemory(sym),
    ]);

    // Resolve outcomes of pending analyses now that we have fresh candles
    const pendingRows = (supaMemory || []).filter(r => r.outcome === 'pending');
    if (pendingRows.length) resolveOutcomes(pendingRows, candles);
    const tickerMemory = supaMemory; // alias for readability below

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
            : h.outcome === 'expired' ? '⏱ EXPIRED (neither TP nor SL hit in 30d)'
            : '⏳ PENDING';
          return `• ${h.analysis_date}: $${h.price} → ${h.verdict} (${h.confidence}% conf) | Target: ${h.target_price || 'N/A'} | Stop: ${h.stop_loss || 'N/A'} | Outcome: ${outcomeStr}`;
        }).join('\n')
      : '';

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
    }

    const newsText = news.slice(0, 6).map(n =>
      `• ${n.title} (${n.source || 'news'}, ${n.date ? new Date(n.date).toLocaleDateString() : 'recent'})`
    ).join('\n');

    const systemPrompt = `You are a ruthlessly honest quantitative trading analyst. The user has already been shown a clear disclaimer stating this is an AI estimate and not financial advice. They understand this. Therefore you have full permission to be completely direct, unhedged, and honest — there is no need to soften conclusions or add caveats.

ABSOLUTE RULES — violating any of these is a failure:
1. HOLD is NOT a default. HOLD means the market is genuinely consolidating with no directional edge. If you're uncertain, use WAIT or NO_EDGE — not HOLD.
2. confidence_score of 60-65 is BANNED as a default. Every asset has a real confidence level. Assign it based on signal alignment, not safety. Most analyses should score outside the 58-67 range.
3. NEVER soften a bearish view with vague language. If the asset is broken, say it clearly with AVOID, SHORT, or REDUCE_EXPOSURE.
4. NEVER soften a bullish view either. If the setup is strong, say STRONG_BUY or BUY — not SPECULATIVE_BUY or WAIT.
5. NO_EDGE is always available. It is more honest than a forced verdict. Use it freely when evidence is genuinely mixed.
6. You must respond with ONLY valid JSON. No markdown. No preamble. No "note:". No disclaimers.

CALIBRATION EXAMPLES — use these as anchors:
- confidence_score 88: RSI 72 overbought, price at BB upper, StochRSI 92, weekly MACD crossing down, approaching major resistance. Extremely high-conviction SHORT setup.
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
ATR(14): ${atr?.toFixed(dp) || 'N/A'} | Volume: ${volTrnd} | OBV: ${obv}
${srText}
${fibText}
${fibExtText ? fibExtText : ''}

━━━ WEEKLY TIMEFRAME ━━━
Weekly trend: ${wTrend ?? 'N/A'} | Weekly RSI: ${wRSI ?? 'N/A'}${wRSI != null ? (wRSI > 70 ? ' ⚠ OVERBOUGHT' : wRSI < 30 ? ' ⚠ OVERSOLD' : '') : ''}
Weekly MACD: ${wMACD != null ? (wMACD > 0 ? 'POSITIVE (bullish)' : 'NEGATIVE (bearish)') : 'N/A'}
Weekly SMA20: ${wSMA20?.toFixed(dp) || 'N/A'} | Weekly SMA50: ${wSMA50?.toFixed(dp) || 'N/A'}

WEEKLY OHLCV (last 8 weeks, oldest→newest):
${weeklyTable}

━━━ DAILY PRICE ACTION (last 20 bars, oldest→newest) ━━━
${ohlcvTable}
${fundBlock}
${macroCtx ? `\n━━━ LIVE MACRO CONTEXT ━━━\n${macroCtx}` : ''}
${scanBlock}
${impactBlock}
${memoryBlock}

━━━ RECENT NEWS (last 60 days) ━━━
${newsText || 'No recent news available.'}

━━━ TASK ━━━
Analyze all data above. Be direct. Do not hedge. Do not default.
Read the price action carefully — what is actually happening? Are bulls or bears in control? What does the volume say? Where is this asset headed?

Respond ONLY with valid JSON. No text before or after.

{
  "verdict": "STRONG_BUY|BUY|SPECULATIVE_BUY|WAIT|HOLD|REDUCE_EXPOSURE|AVOID|SHORT|SPECULATIVE_SHORT|HEDGE|NO_EDGE",
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
  "entry_zone": "<price or range>",
  "stop_loss": "<price>",
  "target_price": "<price>",
  "entry_strategy": "How and when to build the position",
  "position_sizing": "Recommended sizing relative to portfolio and why",
  "stop_loss_logic": "Why this stop level and what it protects against",
  "profit_taking_logic": "When and how to take profits, scaling strategy",
  "hedging_considerations": "Any hedges worth considering",
  "timeframe": "<recommended holding period>",
  "what_would_change_view": "What specific development would flip this thesis",
  "why_confidence_not_higher": "What uncertainty prevents higher confidence"
}`;

    // ── Step 5: Multi-agent parallel analysis ────────────────────────────────
    // 4 specialist agents run in parallel (same wall-clock time as 1 call),
    // then a committee agent synthesises their findings into the final JSON.

    // Shared data block sent to each specialist
    const sharedData = prompt; // already built above — contains all indicators, OHLCV, macro etc.

    const [techRaw, fundRaw, macroRaw, sentRaw] = await Promise.all([

      // Agent 1 — Technical Trader
      callAgent(
        'You are a pure technical analyst. You ONLY analyse price structure, momentum, volume, and indicators. No fundamental opinions. Be direct and specific. Respond in plain text, 4-6 sentences.',
        `Analyse ${sym} (${type}) technically. Focus on: trend quality, momentum state, key support/resistance, overbought/oversold conditions, volume confirmation, and what the multi-timeframe structure says about short-term direction.\n\n${sharedData}`,
        2500
      ).catch(e => `Technical analysis unavailable: ${e.message}`),

      // Agent 2 — Fundamental Analyst (stocks/ETFs only, else skip)
      (type === 'Stock' || type === 'ETF')
        ? callAgent(
            'You are a fundamental analyst focused on business quality and valuation. No technical opinions. Be direct. Respond in plain text, 4-6 sentences.',
            `Analyse ${sym} fundamentally. Cover: valuation vs peers and history, earnings quality and trajectory, revenue growth sustainability, balance sheet strength, competitive moat, and whether the current price reflects fair value.\n\n${sharedData}`,
            2500
          ).catch(e => `Fundamental analysis unavailable: ${e.message}`)
        : Promise.resolve(`Not applicable for ${type} assets.`),

      // Agent 3 — Macro Strategist
      callAgent(
        'You are a macro strategist. You ONLY analyse the macro environment and its specific impact on the given asset. No technical or fundamental opinions. Be direct. Respond in plain text, 4-6 sentences.',
        `Analyse the macro environment and its specific impact on ${sym} (${type}). Cover: current rate cycle, inflation trajectory, Fed/central bank stance, USD strength, risk-on vs risk-off conditions, sector rotation, and how macro tailwinds or headwinds affect this specific asset right now.\n\n${sharedData}`,
        2500
      ).catch(e => `Macro analysis unavailable: ${e.message}`),

      // Agent 4 — Risk Manager (argues AGAINST any bullish thesis)
      callAgent(
        'You are a risk manager and devil\'s advocate. Your ONLY job is to identify what could go wrong — what destroys the bull thesis, what is overpriced, what risks are underappreciated. Be harsh, specific, and direct. Respond in plain text, 4-6 sentences.',
        `For ${sym} (${type}): identify the key risks. What breaks the bullish thesis? What structural risks are underappreciated? What could cause a 20-40% drawdown from here? What do the bears know that bulls are ignoring?\n\n${sharedData}`,
        2500
      ).catch(e => `Risk analysis unavailable: ${e.message}`),

    ]);

    setStep(5);

    // ── Committee Agent: synthesise all specialist findings → final JSON ──────
    const committeePrompt = `You are the head of an investment committee. Four specialist analysts have submitted their findings on ${sym} (current price: ${curr.toFixed(dp)}). Your job is to weigh their inputs, resolve disagreements, and deliver the final verdict.

━━━ TECHNICAL ANALYST ━━━
${techRaw}

━━━ FUNDAMENTAL ANALYST ━━━
${fundRaw}

━━━ MACRO STRATEGIST ━━━
${macroRaw}

━━━ RISK MANAGER (bear case / devil's advocate) ━━━
${sentRaw}

━━━ ADDITIONAL CONTEXT ━━━
${scanBlock || 'No historical scan data.'}
${memoryBlock || 'No prior analyses in memory.'}

Your task:
1. Weigh the four specialist views against each other
2. Identify where they agree (high conviction) and where they conflict (uncertainty)
3. Give the risk manager's concerns serious weight — do not dismiss them
4. Deliver a final verdict. If the specialists disagree materially, lean toward NO_EDGE or WAIT
5. Be brutally honest — the user has been informed this is an AI estimate, not advice

Respond ONLY with this exact JSON structure:

{
  "verdict": "STRONG_BUY|BUY|SPECULATIVE_BUY|WAIT|HOLD|REDUCE_EXPOSURE|AVOID|SHORT|SPECULATIVE_SHORT|HEDGE|NO_EDGE",
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
  "entry_zone": "<price or range>",
  "stop_loss": "<price>",
  "target_price": "<price>",
  "entry_strategy": "How and when to build the position",
  "position_sizing": "Recommended sizing relative to portfolio and why",
  "stop_loss_logic": "Why this stop level and what it protects against",
  "profit_taking_logic": "When and how to take profits, scaling strategy",
  "hedging_considerations": "Any hedges worth considering",
  "timeframe": "<recommended holding period>",
  "what_would_change_view": "What specific development would flip this thesis",
  "why_confidence_not_higher": "What uncertainty or disagreement prevents higher confidence"
}`;

    const committeeText = await callAgent(systemPrompt, committeePrompt, 6000);

    let analysis;
    try {
      const cleaned = committeeText.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
      const m = cleaned.match(/\{[\s\S]*\}/);
      if (!m) throw new Error('no JSON');
      analysis = JSON.parse(m[0]);
      // Store specialist notes on the analysis object for display
      analysis._specialists = { technical: techRaw, fundamental: fundRaw, macro: macroRaw, risk: sentRaw };
    } catch {
      throw new Error('AI returned an unexpected response format. Please try again.');
    }

    // Save this analysis to Supabase memory (fire-and-forget)
    saveToMemory(sym, type, analysis, curr);

    renderResults({ sym, type, candles, weeklyCandles, quote, news, analysis, historicalScan, newsImpact, fibExt, tickerMemory });
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

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initPulse();
  initQuickPicks();
  initAutocomplete();
  document.getElementById('analyseBtn').addEventListener('click', () => {
    closeDropdown();
    startResearch();
  });
});
