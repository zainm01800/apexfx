// APEX improvement-hypotheses engine.
//
// Tier 1 (computed in the worker per confluence run, stored as JSONB):
//   - signalLift(trades): each confluence signal's win rate when ALIGNED with the
//     trade direction vs not -> its marginal predictive power.
//   - thresholdSweep(ctx): re-runs the confluence strategy across entry thresholds
//     and records metrics for EACH (the honest full distribution, not a winner).
// Tier 2 (computed client-side over stored rows, for the UI):
//   - buildHypotheses(rows): signal power, optimal threshold, top strategy×tf×
//     regime per pair, timeframe consistency — every card carrying the mandatory
//     "hypothesis, not a validated edge" framing + sample/shallow caveats.
// No DOM, no network. → APEX.hypotheses
'use strict';

function _A() { const g = (typeof globalThis !== 'undefined') ? globalThis : self; return g.APEX; }
const MIN = 30; // sample-size gate — below this, exclude from suggestion logic

// ── Tier 1: per-signal predictive power on one confluence run's trades ─────────
function signalLift(trades) {
  const t = (trades || []).filter(x => x.signals);
  const labels = new Set();
  t.forEach(x => Object.keys(x.signals).forEach(l => labels.add(l)));
  const out = {};
  const wr = (arr) => arr.length ? arr.filter(x => x.pnlPct > 0).length / arr.length * 100 : null;
  for (const l of labels) {
    const aligned = t.filter(x => x.signals[l] === (x.dir > 0));
    const mis = t.filter(x => x.signals[l] === (x.dir < 0));
    const wa = wr(aligned), wm = wr(mis);
    out[l] = {
      nAligned: aligned.length, winAligned: wa == null ? null : +wa.toFixed(1),
      nMis: mis.length, winMis: wm == null ? null : +wm.toFixed(1),
      lift: (wa != null && wm != null) ? +(wa - wm).toFixed(1) : null,
    };
  }
  return out;
}

// ── Tier 1: entry-threshold sweep (full distribution) ──────────────────────────
function thresholdSweep(ctx, thresholds = [55, 60, 65, 70, 75, 80]) {
  const { strategies: S, metrics: M } = _A();
  return thresholds.map(thr => {
    const strat = S.confluenceStrategy(thr)(ctx);
    const sim = S.simulate(ctx.bars, strat, ctx);
    const m = M.computeMetrics(sim, ctx);
    return { threshold: thr, nTrades: m.nTrades, winRate: m.winRate, expectancy: m.expectancy, sharpe: m.sharpe };
  });
}

// ── Tier 2 helpers over stored rows ────────────────────────────────────────────
function _dataRange(rows) {
  const froms = rows.map(r => r.data_from).filter(Boolean).sort();
  const tos = rows.map(r => r.data_to).filter(Boolean).sort();
  return { from: froms[0] || null, to: tos[tos.length - 1] || null };
}
const FRAMING = 'This is a hypothesis for review, not a validated edge. In-sample, net of a modelled spread only. Out-of-sample performance may differ significantly. Results under 30 trades are excluded from this logic.';

// Aggregate confluence signal power across all eligible confluence rows.
function aggregateSignalPower(rows) {
  const conf = rows.filter(r => r.strategy === 'confluence' && r.signal_lift && r.n_trades >= MIN);
  const acc = {};
  for (const r of conf) {
    for (const [label, s] of Object.entries(r.signal_lift)) {
      if (s.lift == null) continue;
      const a = acc[label] || (acc[label] = { liftSum: 0, wAlignedSum: 0, n: 0, samples: 0 });
      a.liftSum += s.lift; a.n += 1; a.samples += (s.nAligned + s.nMis);
    }
  }
  const list = Object.entries(acc).map(([label, a]) => ({ label, avgLift: +(a.liftSum / a.n).toFixed(1), runs: a.n, samples: a.samples }))
    .sort((x, y) => y.avgLift - x.avgLift);
  return { conf, list };
}

function aggregateThreshold(rows) {
  const conf = rows.filter(r => r.strategy === 'confluence' && Array.isArray(r.threshold_sweep) && r.n_trades >= MIN);
  const byThr = {};
  for (const r of conf) for (const s of r.threshold_sweep) {
    const b = byThr[s.threshold] || (byThr[s.threshold] = { threshold: s.threshold, expSum: 0, wrSum: 0, shSum: 0, tradeSum: 0, n: 0 });
    b.expSum += s.expectancy || 0; b.wrSum += s.winRate || 0; b.shSum += s.sharpe || 0; b.tradeSum += s.nTrades || 0; b.n += 1;
  }
  return Object.values(byThr).map(b => ({ threshold: b.threshold, avgExpectancy: +(b.expSum / b.n).toFixed(3), avgWinRate: +(b.wrSum / b.n).toFixed(1), avgSharpe: +(b.shSum / b.n).toFixed(2), avgTrades: Math.round(b.tradeSum / b.n), runs: b.n }))
    .sort((a, b) => a.threshold - b.threshold);
}

function topCombosPerPair(rows) {
  const eligible = rows.filter(r => r.n_trades >= MIN);
  const byPair = {};
  for (const r of eligible) (byPair[r.instrument] || (byPair[r.instrument] = [])).push(r);
  const out = {};
  for (const [pair, rs] of Object.entries(byPair)) {
    out[pair] = rs.slice().sort((a, b) => (b.sharpe ?? -1e9) - (a.sharpe ?? -1e9)).slice(0, 3).map(r => {
      let bestRegime = null, bestWr = -1;
      for (const [rg, c] of Object.entries(r.regime_breakdown || {})) if (c.n >= 8 && c.winRate > bestWr) { bestWr = c.winRate; bestRegime = rg; }
      return { strategy: r.strategy, timeframe: r.timeframe, sharpe: r.sharpe, nTrades: r.n_trades, bestRegime, bestRegimeWin: bestRegime ? bestWr : null };
    });
  }
  return out;
}

function timeframeConsistency(rows) {
  const eligible = rows.filter(r => r.n_trades >= MIN && r.sharpe != null);
  const byPairTf = {};
  for (const r of eligible) {
    const k = r.instrument + '|' + r.timeframe;
    (byPairTf[k] || (byPairTf[k] = [])).push(r.sharpe);
  }
  const byPair = {};
  for (const [k, arr] of Object.entries(byPairTf)) {
    const [pair, tf] = k.split('|');
    const med = arr.slice().sort((a, b) => a - b)[Math.floor(arr.length / 2)];
    (byPair[pair] || (byPair[pair] = [])).push({ timeframe: tf, medianSharpe: +med.toFixed(2), n: arr.length });
  }
  for (const p in byPair) byPair[p].sort((a, b) => b.medianSharpe - a.medianSharpe);
  return byPair;
}

// Assemble the hypothesis cards for the UI.
function buildHypotheses(rows) {
  const range = _dataRange(rows);
  const eligible = rows.filter(r => r.n_trades >= MIN);
  const totalTrades = eligible.reduce((s, r) => s + (r.n_trades || 0), 0);
  const meta = { framing: FRAMING, dataFrom: range.from, dataTo: range.to, nEligible: eligible.length, totalTrades, excluded: rows.length - eligible.length };
  const cards = [];

  // 1. Confluence signal predictive power
  const sp = aggregateSignalPower(rows);
  if (sp.list.length) {
    const weak = sp.list.filter(s => Math.abs(s.avgLift) < 3);
    const strong = sp.list.filter(s => s.avgLift >= 5);
    cards.push({
      id: 'signal_power', title: 'Which confluence signals actually add predictive power',
      note: `Across ${sp.conf.length} eligible confluence run(s). "Lift" = win rate when a signal agreed with the trade vs when it didn't.`,
      signals: sp.list,
      hypothesis: weak.length ? `Candidates to drop/replace (near-zero lift): ${weak.map(s => s.label).join(', ')}.` : 'No clearly useless signals found.',
      strongest: strong.length ? `Most predictive: ${strong.slice(0, 3).map(s => `${s.label} (+${s.avgLift})`).join(', ')}.` : null,
    });
  }

  // 2. Optimal entry threshold (full distribution)
  const thr = aggregateThreshold(rows);
  if (thr.length) {
    const best = thr.slice().sort((a, b) => b.avgExpectancy - a.avgExpectancy)[0];
    cards.push({
      id: 'threshold', title: 'Is 4+/65% the right confluence entry threshold?',
      note: 'Full distribution across all tested thresholds (not a cherry-picked winner).',
      sweep: thr,
      hypothesis: `Highest in-sample expectancy at threshold ${best.threshold} (${best.avgExpectancy}% / trade, ${best.avgWinRate}% win, ~${best.avgTrades} trades). Treat as a candidate, not a setting — it was selected from ${thr.length} trials.`,
    });
  }

  // 3. Top strategy × timeframe × regime per pair
  const top = topCombosPerPair(rows);
  if (Object.keys(top).length) cards.push({ id: 'top_combos', title: 'Top strategy × timeframe × regime per pair', note: 'Ranked by in-sample Sharpe; only ≥30-trade results.', perPair: top });

  // 4. Timeframe consistency
  const tfc = timeframeConsistency(rows);
  if (Object.keys(tfc).length) cards.push({ id: 'tf_consistency', title: 'Which timeframes carry the most consistent signal per pair', note: 'Median Sharpe across strategy families (≥30 trades).', perPair: tfc });

  return { meta, cards };
}

const _hyp = { signalLift, thresholdSweep, buildHypotheses, aggregateSignalPower, aggregateThreshold, topCombosPerPair, timeframeConsistency, MIN };
(function (g) { g.APEX = g.APEX || {}; g.APEX.hypotheses = _hyp; })(
  typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this)
);
if (typeof module !== 'undefined' && module.exports) module.exports = _hyp;
