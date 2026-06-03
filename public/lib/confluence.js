// APEX confluence — the live multi-timeframe confluence score, reusable in the
// backtest. `calcConfluenceScore(inputs)` is OUTPUT-IDENTICAL to dashboard.js
// (parity-tested); the signal construction is factored into `confluenceSignals`
// so the hypotheses engine can read each signal's boolean. `confluenceAtBar`
// reproduces the live call-site wiring point-in-time (data <= bar i only) so the
// backtest's confluence strategy sees exactly what the live pill would have shown
// on that date. DOM-free → page, worker, Node. → APEX.confluence
'use strict';

function _ta() {
  const g = (typeof globalThis !== 'undefined') ? globalThis : self;
  if (!g.APEX || !g.APEX.ta) throw new Error('APEX.ta must load before confluence.js');
  return g.APEX.ta;
}

// Build the weighted signal list — verbatim logic from dashboard.js
// calcConfluenceScore (the only refactor: extracted so signals are inspectable).
function confluenceSignals({ curr, sma20, sma50, sma200, wCurr, wSMA20, wSMA50, rsi, wRSI, macd, wMACD, volTrnd, adx, stochRsi }) {
  const signals = []; // { bull, weight, label }

  // ── Daily trend (highest weight) ──
  if (curr != null && sma20 != null) signals.push({ bull: curr > sma20,  weight: 2, label: 'Price vs SMA20' });
  if (curr != null && sma50 != null) signals.push({ bull: curr > sma50,  weight: 2, label: 'Price vs SMA50' });
  if (curr != null && sma200 != null) signals.push({ bull: curr > sma200, weight: 3, label: 'Price vs SMA200' });
  if (sma20 != null && sma50 != null) signals.push({ bull: sma20 > sma50, weight: 2, label: 'SMA20 vs SMA50' });
  if (sma50 != null && sma200 != null) signals.push({ bull: sma50 > sma200, weight: 2, label: 'SMA50 vs SMA200' });

  // ── Daily momentum ──
  if (rsi != null) signals.push({ bull: rsi > 50, weight: 2, label: 'RSI momentum' });
  if (macd != null) signals.push({ bull: macd > 0, weight: 2, label: 'MACD' });
  if (stochRsi != null) signals.push({ bull: stochRsi > 50, weight: 1, label: 'StochRSI' });

  // ── Weekly trend (strong confirmatory weight) ──
  if (wCurr != null && wSMA20 != null) signals.push({ bull: wCurr > wSMA20,  weight: 3, label: 'Weekly vs WMA20' });
  if (wCurr != null && wSMA50 != null) signals.push({ bull: wCurr > wSMA50,  weight: 2, label: 'Weekly vs WMA50' });
  if (wRSI  != null) signals.push({ bull: wRSI  > 50, weight: 2, label: 'Weekly RSI' });
  if (wMACD != null) signals.push({ bull: wMACD > 0,  weight: 3, label: 'Weekly MACD' });

  // ── Volume / trend strength ──
  if (volTrnd === 'rising')   signals.push({ bull: true,  weight: 2, label: 'Volume rising' });
  if (volTrnd === 'declining') signals.push({ bull: false, weight: 2, label: 'Volume declining' });
  if (adx != null && adx > 25) signals.push({ bull: curr > (sma20 || curr), weight: 1, label: 'ADX trend strength' });

  return signals;
}

// Output-identical to dashboard.js calcConfluenceScore.
function calcConfluenceScore(inputs) {
  const signals = confluenceSignals(inputs);
  if (!signals.length) return null;
  const totalWeight = signals.reduce((s, x) => s + x.weight, 0);
  const bullWeight  = signals.filter(x => x.bull).reduce((s, x) => s + x.weight, 0);
  const bullPct     = Math.round(bullWeight / totalWeight * 100);
  return {
    bullPct,
    bearPct:     100 - bullPct,
    direction:   bullPct >= 65 ? 'BULLISH' : bullPct <= 35 ? 'BEARISH' : 'MIXED',
    strength:    bullPct >= 80 || bullPct <= 20 ? 'HIGH' : bullPct >= 65 || bullPct <= 35 ? 'MODERATE' : 'LOW',
    signalCount: signals.length,
  };
}

// Compute the confluence inputs at daily bar `i` using only data <= i, mirroring
// the live startResearch() wiring. `weekly` is the higher-timeframe (1w) bar array;
// weekly scalars use only weekly bars whose timestamp <= daily[i].time.
function confluenceInputsAtBar(daily, weekly, i) {
  const ta = _ta();
  const d = daily.slice(0, i + 1);
  const closes = d.map(b => b.close);
  const curr = closes[closes.length - 1];
  const inputs = {
    curr,
    sma20: ta.calcSMA(closes, 20),
    sma50: ta.calcSMA(closes, 50),
    sma200: ta.calcSMA(closes, 200),
    rsi: ta.calcRSI(closes),
    macd: ta.calcMACD(closes),
    stochRsi: ta.calcStochRSI(closes),
    adx: ta.calcADX(d),
    volTrnd: ta.calcVolTrend(d),
    wCurr: null, wSMA20: null, wSMA50: null, wRSI: null, wMACD: null,
  };
  if (weekly && weekly.length) {
    const t = daily[i].time;
    const wk = weekly.filter(w => w.time <= t);
    if (wk.length) {
      const wc = wk.map(b => b.close);
      inputs.wCurr = wc[wc.length - 1];
      inputs.wSMA20 = ta.calcSMA(wc, 20);
      inputs.wSMA50 = ta.calcSMA(wc, 50);
      inputs.wRSI = ta.calcRSI(wc);
      inputs.wMACD = ta.calcMACD(wc);
    }
  }
  return inputs;
}

// Full point-in-time confluence read at daily bar i: score + per-signal booleans.
function confluenceAtBar(daily, weekly, i) {
  const inputs = confluenceInputsAtBar(daily, weekly, i);
  const signals = confluenceSignals(inputs);
  const score = calcConfluenceScore(inputs);
  const signalMap = {};
  for (const s of signals) signalMap[s.label] = s.bull;
  return score ? { ...score, signalMap, signalCount: signals.length } : null;
}

const _confluence = { calcConfluenceScore, confluenceSignals, confluenceInputsAtBar, confluenceAtBar };
(function (g) { g.APEX = g.APEX || {}; g.APEX.confluence = _confluence; })(
  typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this)
);
if (typeof module !== 'undefined' && module.exports) module.exports = _confluence;
