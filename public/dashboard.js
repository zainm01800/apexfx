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
  startResearch();
}
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ── Render ────────────────────────────────────────────────────────────────────

function renderResults({ sym, type, candles, weeklyCandles, quote, news, analysis: a }) {
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
    'Pulling fundamentals, news & macro regime',
    'Running DeepSeek R1 deep reasoning engine',
    'Building probabilistic institutional report',
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

    // ── Step 3: News, fundamentals, macro context in parallel ──
    const [news, quote, macroCtx] = await Promise.all([
      fetchNews(sym, type),
      ['Stock', 'ETF'].includes(type) ? fetchQuote(sym, type) : Promise.resolve(null),
      fetchMacroContext(sym),
    ]);

    setStep(4);

    // ── Build prompt ──────────────────────────────────────────────────────────

    // Raw daily OHLCV (last 30 bars)
    const ohlcvTable = candles.slice(-30).map(b =>
      `${fmtDate(b.time)} O:${b.open.toFixed(dp)} H:${b.high.toFixed(dp)} L:${b.low.toFixed(dp)} C:${b.close.toFixed(dp)} V:${fmtVol(b.volume)}`
    ).join('\n');

    // Weekly OHLCV (last 12 weeks)
    const weeklyTable = weeklyCandles
      ? weeklyCandles.slice(-12).map(b =>
          `${fmtDate(b.time)} O:${b.open.toFixed(dp)} H:${b.high.toFixed(dp)} L:${b.low.toFixed(dp)} C:${b.close.toFixed(dp)} V:${fmtVol(b.volume)}`
        ).join('\n')
      : 'Weekly data unavailable.';

    // Pivot S/R
    const srText = [
      `Resistance: ${resistances.length ? resistances.map(r => r.toFixed(dp)).join(' | ') : 'none identified'}`,
      `Support:    ${supports.length    ? supports.map(s => s.toFixed(dp)).join(' | ')    : 'none identified'}`,
    ].join('\n');

    // Fibonacci
    const fibText = `Range ${fib.low.toFixed(dp)}–${fib.high.toFixed(dp)} | 23.6%:${fib.f236} | 38.2%:${fib.f382} | 50%:${fib.f500} | 61.8%:${fib.f618} | 78.6%:${fib.f786}`;

    // Fundamentals block (stocks only)
    let fundBlock = '';
    if (quote && type === 'Stock') {
      const w52pct = (quote.week52High && quote.week52Low)
        ? ((curr - quote.week52Low) / (quote.week52High - quote.week52Low) * 100).toFixed(0) + '% of 52W range'
        : '';
      fundBlock = `
━━━ FUNDAMENTALS ━━━
Market Cap: ${fmtMCap(quote.marketCap)} | Beta: ${fmtNum(quote.beta)}
P/E (TTM): ${quote.pe ? fmtNum(quote.pe, 1) + 'x' : 'N/A'} | Forward P/E: ${quote.forwardPE ? fmtNum(quote.forwardPE, 1) + 'x' : 'N/A'}
EPS: ${quote.eps ? '$' + fmtNum(quote.eps) : 'N/A'} | Div Yield: ${quote.dividendYield ? fmtPct(quote.dividendYield * 100) : 'N/A'}
52W Range: $${fmtNum(quote.week52Low)} – $${fmtNum(quote.week52High)} | Current position: ${w52pct}
Revenue Growth (YoY): ${quote.revenueGrowth ? fmtPct(quote.revenueGrowth * 100) : 'N/A'} | Earnings Growth: ${quote.earningsGrowth ? fmtPct(quote.earningsGrowth * 100) : 'N/A'}
Analyst Mean Target: ${quote.targetMeanPrice ? '$' + fmtNum(quote.targetMeanPrice) + ' (' + ((quote.targetMeanPrice - curr) / curr * 100).toFixed(1) + '% from current)' : 'N/A'}`;

      if (quote.analystRecs) {
        const r = quote.analystRecs;
        const tot = (r.strongBuy || 0) + (r.buy || 0) + (r.hold || 0) + (r.sell || 0) + (r.strongSell || 0);
        fundBlock += `\nAnalyst Ratings (${r.period}): ${r.strongBuy} StrongBuy | ${r.buy} Buy | ${r.hold} Hold | ${r.sell} Sell | ${r.strongSell} StrongSell (n=${tot})`;
      }
      if (quote.earningsHistory?.length) {
        fundBlock += `\nEarnings Surprises: ${quote.earningsHistory.map(e =>
          `${e.period}: ${e.surprisePct != null ? (e.surprisePct > 0 ? '+' : '') + e.surprisePct + '%' : 'N/A'}`
        ).join(' | ')}`;
      }
    }

    const newsText = news.slice(0, 8).map(n =>
      `• ${n.title} (${n.source || 'news'}, ${n.date ? new Date(n.date).toLocaleDateString() : 'recent'})`
    ).join('\n');

    const systemPrompt = `You are a ruthlessly honest quantitative trading analyst. Your only job is to give accurate, unhedged market analysis. You do not give financial advice — you give market analysis. There is no legal risk in being direct.

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
Fibonacci retracements: ${fibText}

━━━ WEEKLY TIMEFRAME ━━━
Weekly trend: ${wTrend ?? 'N/A'} | Weekly RSI: ${wRSI ?? 'N/A'}${wRSI != null ? (wRSI > 70 ? ' ⚠ OVERBOUGHT' : wRSI < 30 ? ' ⚠ OVERSOLD' : '') : ''}
Weekly MACD: ${wMACD != null ? (wMACD > 0 ? 'POSITIVE (bullish)' : 'NEGATIVE (bearish)') : 'N/A'}
Weekly SMA20: ${wSMA20?.toFixed(dp) || 'N/A'} | Weekly SMA50: ${wSMA50?.toFixed(dp) || 'N/A'}

WEEKLY OHLCV (last 12 weeks, oldest→newest):
${weeklyTable}

━━━ DAILY PRICE ACTION (last 30 bars, oldest→newest) ━━━
${ohlcvTable}
${fundBlock}
${macroCtx ? `\n━━━ LIVE MACRO CONTEXT ━━━\n${macroCtx}` : ''}

━━━ NEWS & CATALYSTS (last 14 days) ━━━
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

    const aiRes = await fetch('/api/ai', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt,
        system:      systemPrompt,
        model:       'llama-3.3-70b-versatile',
        max_tokens:  6000,
        temperature: 0.35,
        timeoutMs:   58000,
      }),
    });
    if (!aiRes.ok) {
      const e = await aiRes.json().catch(() => ({}));
      throw new Error(e.error || `AI service error (HTTP ${aiRes.status})`);
    }
    const aiData = await aiRes.json();
    if (aiData.error) throw new Error(aiData.error);

    setStep(5);

    let analysis;
    try {
      // Strip DeepSeek R1 chain-of-thought thinking blocks before parsing JSON
      const cleaned = aiData.text.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
      const m = cleaned.match(/\{[\s\S]*\}/);
      if (!m) throw new Error('no JSON');
      analysis = JSON.parse(m[0]);
    } catch {
      throw new Error('AI returned an unexpected response format. Please try again.');
    }

    renderResults({ sym, type, candles, weeklyCandles, quote, news, analysis });
    document.getElementById('analyseBtn').disabled = false;

  } catch (err) {
    showError(err.message || 'An unexpected error occurred. Please try again.');
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  initPulse();
  const inp = document.getElementById('symInput');
  inp.addEventListener('input',   () => updateTypePill(inp.value));
  inp.addEventListener('keydown', e  => { if (e.key === 'Enter') startResearch(); });
  document.getElementById('analyseBtn').addEventListener('click', startResearch);
});
