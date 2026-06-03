// APEX backtest metrics — pure functions over a simulate() result.
//
// Input: { trades[], barReturns[] } from strategies.simulate(), plus context
// (barsPerYear for annualisation, pipSize for forex pip stats, dataRange).
// All return %s are net of the modelled spread already applied inside simulate().
// Sharpe is annualised from the per-bar return series (well-defined regardless of
// timeframe); 1m/5m are flagged shallow. DOM-free → page, worker, Node. → APEX.metrics
'use strict';

// Bars per year per (assetClass, timeframe) — used to annualise Sharpe. Crypto
// trades 24/7, forex ~24x5, equities ~6.5h x 252 sessions. Daily/weekly are the
// session counts. These are the standard conventions; exact values matter less
// than being consistent and flagged.
const BARS_PER_YEAR = {
  Crypto: { '1m': 525600, '5m': 105120, '15m': 35040, '30m': 17520, '1h': 8760, '4h': 2190, '1d': 365, '1w': 52 },
  Forex:  { '1m': 372000, '5m': 74400,  '15m': 24800, '30m': 12400, '1h': 6200, '4h': 1550, '1d': 252, '1w': 52 },
  _stock: { '1m': 98280,  '5m': 19656,  '15m': 6552,  '30m': 3276,  '1h': 1638, '4h': 410,  '1d': 252, '1w': 52 },
};
function barsPerYear(assetClass, tf) {
  const table = BARS_PER_YEAR[assetClass] || BARS_PER_YEAR._stock;
  return table[tf] || table['1d'];
}

const SHALLOW_TFS = new Set(['1m', '5m']);
const MIN_SAMPLE = 30;

function _mean(a) { return a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0; }
function _std(a) { // population-ish sample std (ddof=1)
  if (a.length < 2) return 0;
  const m = _mean(a);
  return Math.sqrt(a.reduce((s, x) => s + (x - m) * (x - m), 0) / (a.length - 1));
}

// Equity curve (multiplicative) from per-bar fractional returns; returns
// { totalReturnPct, maxDrawdownPct }.
function _equityStats(barReturns) {
  let eq = 1, peak = 1, maxDD = 0;
  for (const r of barReturns) {
    eq *= (1 + r);
    if (eq > peak) peak = eq;
    const dd = peak > 0 ? (peak - eq) / peak : 0;
    if (dd > maxDD) maxDD = dd;
  }
  return { totalReturnPct: (eq - 1) * 100, maxDrawdownPct: maxDD * 100 };
}

function computeMetrics(sim, ctx) {
  const trades = sim.trades || [];
  const barReturns = sim.barReturns || [];
  const n = trades.length;
  const bpy = barsPerYear(ctx.assetClass, ctx.timeframe);

  const { totalReturnPct, maxDrawdownPct } = _equityStats(barReturns);
  const rMean = _mean(barReturns), rStd = _std(barReturns);
  const sharpe = rStd > 0 ? (rMean / rStd) * Math.sqrt(bpy) : 0;

  const winsPct = trades.filter(t => t.pnlPct > 0).map(t => t.pnlPct);
  const lossPct = trades.filter(t => t.pnlPct <= 0).map(t => t.pnlPct);
  const winRate = n ? (winsPct.length / n) * 100 : 0;
  const avgWinPct = winsPct.length ? _mean(winsPct) : 0;
  const avgLossPct = lossPct.length ? _mean(lossPct) : 0;           // <= 0
  const grossWin = winsPct.reduce((s, x) => s + x, 0);
  const grossLoss = Math.abs(lossPct.reduce((s, x) => s + x, 0));
  const profitFactor = grossLoss > 0 ? grossWin / grossLoss : (grossWin > 0 ? null : 0); // null = no losses (∞)
  // Expectancy = winRate·avgWin − lossRate·|avgLoss|  (per-trade %, net of spread)
  const lossRate = n ? lossPct.length / n : 0;
  const expectancy = (winRate / 100) * avgWinPct - lossRate * Math.abs(avgLossPct);

  // Forex-only pip stats (pnlPips is null for other asset classes).
  let avgWinPips = null, avgLossPips = null;
  const hasPips = trades.some(t => t.pnlPips != null);
  if (hasPips) {
    const wp = trades.filter(t => t.pnlPct > 0 && t.pnlPips != null).map(t => t.pnlPips);
    const lp = trades.filter(t => t.pnlPct <= 0 && t.pnlPips != null).map(t => t.pnlPips);
    avgWinPips = wp.length ? +(_mean(wp)).toFixed(1) : null;
    avgLossPips = lp.length ? +(_mean(lp)).toFixed(1) : null;
  }

  return {
    nTrades: n,
    totalReturn: +totalReturnPct.toFixed(2),
    sharpe: +sharpe.toFixed(3),
    maxDrawdown: +maxDrawdownPct.toFixed(2),
    winRate: +winRate.toFixed(1),
    avgWinPct: +avgWinPct.toFixed(3),
    avgLossPct: +avgLossPct.toFixed(3),
    avgWinPips, avgLossPips,
    expectancy: +expectancy.toFixed(4),
    profitFactor: profitFactor == null ? null : +profitFactor.toFixed(3),
    lowSample: n < MIN_SAMPLE,
    shallowSharpe: SHALLOW_TFS.has(ctx.timeframe),
    barsPerYear: bpy,
  };
}

const _metrics = { computeMetrics, barsPerYear, BARS_PER_YEAR, MIN_SAMPLE };
(function (g) { g.APEX = g.APEX || {}; g.APEX.metrics = _metrics; })(
  typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this)
);
if (typeof module !== 'undefined' && module.exports) module.exports = _metrics;
