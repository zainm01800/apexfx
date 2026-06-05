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

// ── Walk-forward / out-of-sample split ────────────────────────────────────────
// Measures the SAME fixed strategy on the older (in-sample) portion vs the held-out
// recent (out-of-sample) portion of the data. A rule that only worked in-sample and
// fell apart out-of-sample is overfit to a past regime — the OOS result is the honest
// one for "would this work going forward?". Single 70/30 hold-out (older→test on newer).
// Trades are bucketed by entry bar; bar-returns are sliced at the same index, so OOS
// metrics reuse the exact same formulas as the full-period ones. Null if too little data.
function walkForward(sim, ctx, frac) {
  frac = frac || 0.7;
  const barReturns = sim.barReturns || [];
  const trades = sim.trades || [];
  if (barReturns.length < 60) return null;       // too short to split meaningfully
  const splitIdx = Math.floor(barReturns.length * frac);
  const isM  = computeMetrics({ trades: trades.filter(t => t.entryIdx <  splitIdx), barReturns: barReturns.slice(0, splitIdx) }, ctx);
  const oosM = computeMetrics({ trades: trades.filter(t => t.entryIdx >= splitIdx), barReturns: barReturns.slice(splitIdx) }, ctx);
  const enough = oosM.nTrades >= 10;             // need a usable OOS sample to judge
  const holds  = enough && oosM.totalReturn > 0 && oosM.sharpe > 0;
  return {
    is_return: isM.totalReturn, is_sharpe: isM.sharpe,
    oos_return: oosM.totalReturn, oos_sharpe: oosM.sharpe,
    oos_win_rate: oosM.winRate, oos_n_trades: oosM.nTrades,
    oos_holds: holds, oos_enough: enough,
  };
}

// ── Monte Carlo drawdown / return bands ───────────────────────────────────────
// A single backtest is ONE path history happened to take. Bootstrap-resample the
// per-trade returns (with replacement) to turn it into a distribution: how bad could
// the drawdown plausibly get, and how often is the edge even profitable? Pure; works
// off a trade list, no re-simulation. Returns null below a usable sample.
function monteCarloDrawdown(trades, opts) {
  opts = opts || {};
  const runs = opts.runs || 2000;
  const rnd = opts.rng || Math.random;
  const rets = (trades || []).filter(t => t && t.pnlPct != null).map(t => t.pnlPct / 100);
  const n = rets.length;
  if (n < 5) return null;
  // Observed trade-sequence drawdown (trades in their ACTUAL order) — the like-for-like
  // reference for the resampled bands below (both are close-to-close, trade-level).
  let oeq = 1, opeak = 1, oDD = 0;
  for (const x of rets) { oeq *= (1 + x); if (oeq > opeak) opeak = oeq; const d = opeak > 0 ? (opeak - oeq) / opeak : 0; if (d > oDD) oDD = d; }
  const ddObserved = +(oDD * 100).toFixed(1);
  const maxDDs = new Array(runs), totals = new Array(runs);
  for (let r = 0; r < runs; r++) {
    let eq = 1, peak = 1, maxDD = 0;
    for (let k = 0; k < n; k++) {
      eq *= (1 + rets[(rnd() * n) | 0]);          // sample a trade with replacement
      if (eq > peak) peak = eq;
      const dd = peak > 0 ? (peak - eq) / peak : 0;
      if (dd > maxDD) maxDD = dd;
    }
    maxDDs[r] = maxDD * 100;
    totals[r] = (eq - 1) * 100;
  }
  const pctile = (arr, p) => { const s = arr.slice().sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.max(0, Math.floor(p / 100 * s.length)))]; };
  const posRate = totals.reduce((c, x) => c + (x > 0 ? 1 : 0), 0) / runs * 100;
  return {
    runs, n, ddObserved,
    ddMedian:  +pctile(maxDDs, 50).toFixed(1),
    ddP95:     +pctile(maxDDs, 95).toFixed(1),
    ddWorst:   +Math.max.apply(null, maxDDs).toFixed(1),
    retP5:     +pctile(totals, 5).toFixed(1),
    retMedian: +pctile(totals, 50).toFixed(1),
    retP95:    +pctile(totals, 95).toFixed(1),
    posRate:   +posRate.toFixed(0),
  };
}

const _metrics = { computeMetrics, walkForward, monteCarloDrawdown, barsPerYear, BARS_PER_YEAR, MIN_SAMPLE };
(function (g) { g.APEX = g.APEX || {}; g.APEX.metrics = _metrics; })(
  typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this)
);
if (typeof module !== 'undefined' && module.exports) module.exports = _metrics;
