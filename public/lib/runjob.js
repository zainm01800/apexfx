// APEX backtest job runner — pure compute for ONE (pair, timeframe) job.
// Builds the context, runs every strategy, computes metrics + a regime breakdown,
// and returns one storage row per strategy. Used by backtest.worker.js (off the
// main thread) and directly in Node tests. No DOM, no network. → APEX.runjob
'use strict';

function _apex() { const g = (typeof globalThis !== 'undefined') ? globalThis : self; return g.APEX; }

// Win-rate / avg-PnL grouped by the entry-bar regime ("trend/vol"). Feeds the
// regime x strategy matrix and the (Layer 5) hypotheses engine.
function regimeBreakdown(trades) {
  const g = {};
  for (const t of trades) {
    const lab = t.regimeAtEntry; if (!lab) continue;
    const key = `${lab.trend}/${lab.vol}`;
    (g[key] || (g[key] = [])).push(t.pnlPct);
  }
  const out = {};
  for (const k in g) {
    const a = g[k]; const wins = a.filter(x => x > 0).length;
    out[k] = { n: a.length, winRate: +(wins / a.length * 100).toFixed(1), avgPnl: +(a.reduce((s, x) => s + x, 0) / a.length).toFixed(3) };
  }
  return out;
}

// payload: { bars, weekly?, sym, assetClass, timeframe, runId, runTs, appVersion? }
// onProgress(idx, total, strategyId) optional.
function runJob(payload, onProgress) {
  const { bars, weekly = null, sym, assetClass, timeframe, runId, runTs, appVersion = null } = payload;
  const { strategies: S, metrics: M } = _apex();
  if (!Array.isArray(bars) || bars.length < 2) return [];

  const H = _apex().hypotheses;
  const ctx = S.buildContext(bars, { sym, assetClass, timeframe, weekly });
  const strats = S.buildStrategies(ctx);
  const dataFrom = new Date(bars[0].time * 1000).toISOString();
  const dataTo = new Date(bars[bars.length - 1].time * 1000).toISOString();

  const rows = [];
  for (let k = 0; k < strats.length; k++) {
    const st = strats[k];
    const sim = S.simulate(bars, st.strat, ctx);
    const m = M.computeMetrics(sim, ctx);
    // Tier-1 hypotheses aggregates for the confluence strategy (stored as JSONB).
    let signalLift = null, thresholdSweep = null;
    if (st.id === 'confluence' && H) {
      try { signalLift = H.signalLift(sim.trades); thresholdSweep = H.thresholdSweep(ctx); } catch (_) { /* best-effort */ }
    }
    rows.push({
      id: `${sym}_${timeframe}_${st.id}_${runTs}`,
      run_id: runId,
      instrument: sym,
      asset_class: assetClass,
      timeframe,
      strategy: st.id,
      strategy_family: st.family,
      regime_filtered: !!st.regimeFiltered,
      data_from: dataFrom,
      data_to: dataTo,
      n_bars: bars.length,
      n_trades: m.nTrades,
      total_return: m.totalReturn,
      sharpe: m.sharpe,
      max_drawdown: m.maxDrawdown,
      win_rate: m.winRate,
      avg_win_pct: m.avgWinPct,
      avg_loss_pct: m.avgLossPct,
      avg_win_pips: m.avgWinPips,
      avg_loss_pips: m.avgLossPips,
      expectancy: m.expectancy,
      profit_factor: m.profitFactor,
      low_sample: m.lowSample,
      shallow_sharpe: m.shallowSharpe,
      regime_breakdown: regimeBreakdown(sim.trades),
      signal_lift: signalLift,        // confluence only (else null)
      threshold_sweep: thresholdSweep, // confluence only (else null)
      params: st.params || {},
      app_version: appVersion,
    });
    if (onProgress) onProgress(k + 1, strats.length, st.id);
  }
  return rows;
}

const _runjob = { runJob, regimeBreakdown };
(function (g) { g.APEX = g.APEX || {}; g.APEX.runjob = _runjob; })(
  typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this)
);
if (typeof module !== 'undefined' && module.exports) module.exports = _runjob;
