// APEX strategy library + execution harness — pure, no DOM, no network.
//
// A strategy is { entry(i,ctx)->{dir,stop?,target?}|null, exit?(i,ctx,pos)->bool }.
// simulate(bars,strat,ctx) runs it bar-by-bar with one position at a time:
//   - decision at bar i uses data <= i; entry/signal-exit FILL at the next bar's OPEN
//   - stops/targets are checked intrabar via high/low (stop assumed first if a bar
//     spans both — pessimistic); force-close at end of data
//   - a modelled round-trip spread (ctx.costPct) is deducted from every trade and
//     the equity curve.
// It returns { trades[], barReturns[] } (barReturns feeds the annualised Sharpe).
// Regime-filtered twins wrap a base strategy and drop trades whose entry-bar
// regime is misaligned. The confluence strategy reuses APEX.confluence so the
// backtest evaluates the live analysis. → APEX.strategies
'use strict';

function _conf() {
  const g = (typeof globalThis !== 'undefined') ? globalThis : self;
  return g.APEX.confluence;
}
function _regimeLib() {
  const g = (typeof globalThis !== 'undefined') ? globalThis : self;
  return g.APEX.regime;
}

// ── Indicator series (efficient; correctness-tested, not tied to ta.js) ───────
function smaSeries(c, p) {
  const out = new Array(c.length).fill(null);
  let s = 0;
  for (let i = 0; i < c.length; i++) { s += c[i]; if (i >= p) s -= c[i - p]; if (i >= p - 1) out[i] = s / p; }
  return out;
}
function emaSeries(c, p) {
  const out = new Array(c.length).fill(null);
  if (c.length < p) return out;
  const k = 2 / (p + 1);
  let e = 0; for (let i = 0; i < p; i++) e += c[i]; e /= p; out[p - 1] = e;
  for (let i = p; i < c.length; i++) { e = c[i] * k + e * (1 - k); out[i] = e; }
  return out;
}
function rsiSeries(c, p = 14) {
  const out = new Array(c.length).fill(null);
  if (c.length < p + 1) return out;
  let ag = 0, al = 0;
  for (let i = 1; i <= p; i++) { const x = c[i] - c[i - 1]; if (x > 0) ag += x; else al -= x; }
  ag /= p; al /= p;
  out[p] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
  for (let i = p + 1; i < c.length; i++) {
    const x = c[i] - c[i - 1];
    ag = (ag * (p - 1) + Math.max(0, x)) / p;
    al = (al * (p - 1) + Math.max(0, -x)) / p;
    out[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
  }
  return out;
}
function macdSeries(c) {
  const e12 = emaSeries(c, 12), e26 = emaSeries(c, 26);
  const line = c.map((_, i) => (e12[i] != null && e26[i] != null) ? e12[i] - e26[i] : null);
  const first = line.findIndex(v => v != null);
  const signal = new Array(c.length).fill(null), hist = new Array(c.length).fill(null);
  if (first >= 0) {
    const tail = line.slice(first);
    const sig = emaSeries(tail, 9);
    for (let j = 0; j < sig.length; j++) if (sig[j] != null) { signal[first + j] = sig[j]; hist[first + j] = line[first + j] - sig[j]; }
  }
  return { line, signal, hist };
}
function atrSeries(bars, p = 14) {
  const out = new Array(bars.length).fill(null);
  if (bars.length < p + 1) return out;
  const tr = [];
  for (let i = 1; i < bars.length; i++) { const b = bars[i], pc = bars[i - 1].close; tr.push(Math.max(b.high - b.low, Math.abs(b.high - pc), Math.abs(b.low - pc))); }
  let atr = 0; for (let i = 0; i < p; i++) atr += tr[i]; atr /= p; out[p] = atr;       // tr[i] -> bar i+1
  for (let i = p; i < tr.length; i++) { atr = (atr * (p - 1) + tr[i]) / p; out[i + 1] = atr; }
  return out;
}
function bbSeries(c, p = 20, mult = 2) {
  const mid = smaSeries(c, p);
  const upper = new Array(c.length).fill(null), lower = new Array(c.length).fill(null);
  for (let i = p - 1; i < c.length; i++) {
    let v = 0; const m = mid[i];
    for (let k = i - p + 1; k <= i; k++) { const d = c[k] - m; v += d * d; }
    const sd = Math.sqrt(v / p);
    upper[i] = m + mult * sd; lower[i] = m - mult * sd;
  }
  return { upper, mid, lower };
}
function stochSeries(bars, p = 14, d = 3) {
  const k = new Array(bars.length).fill(null);
  for (let i = p - 1; i < bars.length; i++) {
    let hi = -Infinity, lo = Infinity;
    for (let j = i - p + 1; j <= i; j++) { if (bars[j].high > hi) hi = bars[j].high; if (bars[j].low < lo) lo = bars[j].low; }
    k[i] = hi > lo ? (bars[i].close - lo) / (hi - lo) * 100 : 50;
  }
  const dd = smaSeries(k.map(v => v == null ? 0 : v), d).map((v, i) => k[i] == null ? null : v);
  return { k, d: dd };
}
function priorExtremeSeries(bars, lb, kind) { // 'high' or 'low' over the lb bars BEFORE i
  const out = new Array(bars.length).fill(null);
  for (let i = lb; i < bars.length; i++) {
    let ext = kind === 'high' ? -Infinity : Infinity;
    for (let j = i - lb; j < i; j++) { const v = kind === 'high' ? bars[j].high : bars[j].low; if (kind === 'high' ? v > ext : v < ext) ext = v; }
    out[i] = ext;
  }
  return out;
}

// ── Context: precomputed once per (pair, timeframe); memoised indicator series ─
const COSTS = { // round-trip = spread once + slippage both sides (bps of price)
  Forex:   { spreadBps: 1.0, slippageBps: 0.5 },
  Stock:   { spreadBps: 2.0, slippageBps: 1.0 },
  ETF:     { spreadBps: 2.0, slippageBps: 1.0 },
  Crypto:  { spreadBps: 5.0, slippageBps: 2.0 },
  Futures: { spreadBps: 2.0, slippageBps: 1.0 },
};
function costPctFor(assetClass) { const c = COSTS[assetClass] || COSTS.Stock; return (c.spreadBps + 2 * c.slippageBps) / 100; }
function pipSizeFor(sym, assetClass) {
  if (assetClass !== 'Forex') return null;
  return /JPY/.test(String(sym).toUpperCase()) ? 0.01 : 0.0001;
}

function buildContext(bars, { sym, assetClass, timeframe, weekly = null }) {
  const closes = bars.map(b => b.close);
  const cache = {};
  const memo = (key, fn) => (cache[key] || (cache[key] = fn()));
  const ctx = {
    bars, closes, weekly, sym, assetClass, timeframe,
    costPct: costPctFor(assetClass),
    pipSize: pipSizeFor(sym, assetClass),
    regimeLabels: _regimeLib().regimeSeries(bars),
    sma: (p) => memo('sma' + p, () => smaSeries(closes, p)),
    ema: (p) => memo('ema' + p, () => emaSeries(closes, p)),
    rsi: (p = 14) => memo('rsi' + p, () => rsiSeries(closes, p)),
    macd: () => memo('macd', () => macdSeries(closes)),
    atr: (p = 14) => memo('atr' + p, () => atrSeries(bars, p)),
    bb: (p = 20, m = 2) => memo(`bb${p}_${m}`, () => bbSeries(closes, p, m)),
    stoch: (p = 14, d = 3) => memo(`st${p}_${d}`, () => stochSeries(bars, p, d)),
    priorHigh: (lb) => memo('ph' + lb, () => priorExtremeSeries(bars, lb, 'high')),
    priorLow: (lb) => memo('pl' + lb, () => priorExtremeSeries(bars, lb, 'low')),
  };
  return ctx;
}

// ATR-based protective stop off the signal bar's close.
function atrStop(ctx, i, dir, mult = 2) {
  const atr = ctx.atr()[i];
  if (atr == null) return null;
  return ctx.closes[i] - dir * mult * atr;
}

// ── Execution harness ─────────────────────────────────────────────────────────
function simulate(bars, strat, ctx) {
  const n = bars.length;
  const trades = [];
  const barReturns = new Array(n).fill(0);
  const costFrac = ctx.costPct / 100;
  const pip = ctx.pipSize;
  let pos = null, pendingEntry = null, pendingExit = false;

  const record = (p, exitIdx, exitPrice, reason) => {
    const grossPct = p.dir * (exitPrice - p.entryPrice) / p.entryPrice * 100;
    const t = {
      entryIdx: p.entryIdx, exitIdx, dir: p.dir,
      entryPrice: +p.entryPrice.toFixed(6), exitPrice: +exitPrice.toFixed(6),
      pnlPct: +(grossPct - ctx.costPct).toFixed(4),
      pnlPips: pip ? +((p.dir * (exitPrice - p.entryPrice) / pip) - (ctx.costPct / 100 * p.entryPrice / pip)).toFixed(1) : null,
      exitReason: reason, barsHeld: exitIdx - p.entryIdx,
      regimeAtEntry: ctx.regimeLabels[p.entryIdx] || null,
      signals: p.signals || null,
    };
    trades.push(t);
  };

  for (let i = 0; i < n; i++) {
    const bar = bars[i];
    const prevClose = i > 0 ? bars[i - 1].close : bar.open;

    // (1) pending signal-exit fills at this open
    if (pos && pendingExit) {
      barReturns[i] += pos.dir * (bar.open - prevClose) / prevClose - costFrac;
      record(pos, i, bar.open, 'signal'); pos = null; pendingExit = false;
    }
    // (2) pending entry fills at this open
    if (!pos && pendingEntry) {
      pos = { dir: pendingEntry.dir, entryIdx: i, entryPrice: bar.open, stop: pendingEntry.stop, target: pendingEntry.target, signals: pendingEntry.signals || null };
      pendingEntry = null;
    }
    // (3) manage live position intrabar on bar i
    if (pos) {
      const ref = pos.entryIdx === i ? pos.entryPrice : prevClose;
      const hitStop = pos.stop != null && (pos.dir > 0 ? bar.low <= pos.stop : bar.high >= pos.stop);
      const hitTarget = pos.target != null && (pos.dir > 0 ? bar.high >= pos.target : bar.low <= pos.target);
      if (hitStop) {            // pessimistic: stop checked before target
        barReturns[i] += pos.dir * (pos.stop - ref) / ref - costFrac;
        record(pos, i, pos.stop, 'stop'); pos = null;
      } else if (hitTarget) {
        barReturns[i] += pos.dir * (pos.target - ref) / ref - costFrac;
        record(pos, i, pos.target, 'target'); pos = null;
      } else {
        barReturns[i] += pos.dir * (bar.close - ref) / ref;   // mark to close
      }
    }
    // (4) generate signals on data <= i
    if (pos) {
      if (strat.exit && strat.exit(i, ctx, pos)) pendingExit = true;
    } else if (!pendingEntry) {
      const e = strat.entry(i, ctx);
      if (e && (e.dir === 1 || e.dir === -1)) pendingEntry = e;
    }
  }
  // force-close at end of data (already marked-to-close each bar; deduct exit cost)
  if (pos) { barReturns[n - 1] -= costFrac; record(pos, n - 1, bars[n - 1].close, 'end-of-data'); }

  return { trades, barReturns };
}

// ── Regime filter twin ────────────────────────────────────────────────────────
const ALLOW = {
  trend: (lab) => lab && (lab.trend === 'up' || lab.trend === 'down'),
  rangingNormal: (lab) => lab && lab.trend === 'ranging' && lab.vol === 'normal',
};
function withRegimeFilter(base, allowFn) {
  return {
    entry(i, ctx) {
      const e = base.entry(i, ctx);
      if (!e) return null;
      return allowFn(ctx.regimeLabels[i]) ? e : null;
    },
    exit: base.exit ? (i, ctx, pos) => base.exit(i, ctx, pos) : undefined,
  };
}

// ── Base strategies (factories: (ctx)->strat). Stops are ATR-based; trend
//    strategies exit on the opposite signal, mean-reversion on a target. ───────
function maCross(fast, slow, kind) { // kind: 'sma'|'ema'
  return (ctx) => {
    const f = kind === 'ema' ? ctx.ema(fast) : ctx.sma(fast);
    const s = kind === 'ema' ? ctx.ema(slow) : ctx.sma(slow);
    const crossed = (i, up) => f[i] != null && s[i] != null && f[i - 1] != null && s[i - 1] != null &&
      (up ? (f[i - 1] <= s[i - 1] && f[i] > s[i]) : (f[i - 1] >= s[i - 1] && f[i] < s[i]));
    return {
      entry(i) { if (i < 1) return null; if (crossed(i, true)) return { dir: 1, stop: atrStop(ctx, i, 1) }; if (crossed(i, false)) return { dir: -1, stop: atrStop(ctx, i, -1) }; return null; },
      exit(i, c, pos) { return f[i] != null && s[i] != null && (pos.dir > 0 ? f[i] < s[i] : f[i] > s[i]); },
    };
  };
}
function tripleEma(a, b, d) { // enter on a crossing b in direction of trend filter d
  return (ctx) => {
    const ea = ctx.ema(a), eb = ctx.ema(b), ed = ctx.ema(d);
    return {
      entry(i) {
        if (i < 1 || ea[i] == null || eb[i] == null || ed[i] == null) return null;
        const upCross = ea[i - 1] <= eb[i - 1] && ea[i] > eb[i];
        const dnCross = ea[i - 1] >= eb[i - 1] && ea[i] < eb[i];
        if (upCross && ctx.closes[i] > ed[i]) return { dir: 1, stop: atrStop(ctx, i, 1) };
        if (dnCross && ctx.closes[i] < ed[i]) return { dir: -1, stop: atrStop(ctx, i, -1) };
        return null;
      },
      exit(i, c, pos) { return ea[i] != null && eb[i] != null && (pos.dir > 0 ? ea[i] < eb[i] : ea[i] > eb[i]); },
    };
  };
}
function priceVsMA(period, kind) {
  return (ctx) => {
    const m = kind === 'ema' ? ctx.ema(period) : ctx.sma(period);
    const c = ctx.closes;
    return {
      entry(i) { if (i < 1 || m[i] == null || m[i - 1] == null) return null; if (c[i - 1] <= m[i - 1] && c[i] > m[i]) return { dir: 1, stop: atrStop(ctx, i, 1) }; if (c[i - 1] >= m[i - 1] && c[i] < m[i]) return { dir: -1, stop: atrStop(ctx, i, -1) }; return null; },
      exit(i, _c, pos) { return m[i] != null && (pos.dir > 0 ? c[i] < m[i] : c[i] > m[i]); },
    };
  };
}
function macdCross() {
  return (ctx) => {
    const { line, signal } = ctx.macd();
    return {
      entry(i) { if (i < 1 || line[i] == null || signal[i] == null || signal[i - 1] == null) return null; if (line[i - 1] <= signal[i - 1] && line[i] > signal[i]) return { dir: 1, stop: atrStop(ctx, i, 1) }; if (line[i - 1] >= signal[i - 1] && line[i] < signal[i]) return { dir: -1, stop: atrStop(ctx, i, -1) }; return null; },
      exit(i, _c, pos) { return line[i] != null && signal[i] != null && (pos.dir > 0 ? line[i] < signal[i] : line[i] > signal[i]); },
    };
  };
}
function macdHistReversal() {
  return (ctx) => {
    const { hist } = ctx.macd();
    return {
      entry(i) { if (i < 1 || hist[i] == null || hist[i - 1] == null) return null; if (hist[i - 1] <= 0 && hist[i] > 0) return { dir: 1, stop: atrStop(ctx, i, 1) }; if (hist[i - 1] >= 0 && hist[i] < 0) return { dir: -1, stop: atrStop(ctx, i, -1) }; return null; },
      exit(i, _c, pos) { return hist[i] != null && (pos.dir > 0 ? hist[i] < 0 : hist[i] > 0); },
    };
  };
}
function rsiReversal(lo = 30, hi = 70) {
  return (ctx) => {
    const r = ctx.rsi();
    return {
      entry(i) { if (i < 1 || r[i] == null || r[i - 1] == null) return null; if (r[i - 1] <= lo && r[i] > lo) return { dir: 1, stop: atrStop(ctx, i, 1), target: ctx.closes[i] + 2 * (ctx.atr()[i] || 0) }; if (r[i - 1] >= hi && r[i] < hi) return { dir: -1, stop: atrStop(ctx, i, -1), target: ctx.closes[i] - 2 * (ctx.atr()[i] || 0) }; return null; },
      exit(i, _c, pos) { return r[i] != null && (pos.dir > 0 ? r[i] >= hi : r[i] <= lo); },
    };
  };
}
function rsiCenterline() {
  return (ctx) => {
    const r = ctx.rsi();
    return {
      entry(i) { if (i < 1 || r[i] == null || r[i - 1] == null) return null; if (r[i - 1] <= 50 && r[i] > 50) return { dir: 1, stop: atrStop(ctx, i, 1) }; if (r[i - 1] >= 50 && r[i] < 50) return { dir: -1, stop: atrStop(ctx, i, -1) }; return null; },
      exit(i, _c, pos) { return r[i] != null && (pos.dir > 0 ? r[i] < 50 : r[i] > 50); },
    };
  };
}
function stochCross() {
  return (ctx) => {
    const { k, d } = ctx.stoch();
    return {
      entry(i) { if (i < 1 || k[i] == null || d[i] == null || k[i - 1] == null || d[i - 1] == null) return null; if (k[i - 1] <= d[i - 1] && k[i] > d[i] && k[i] < 20) return { dir: 1, stop: atrStop(ctx, i, 1) }; if (k[i - 1] >= d[i - 1] && k[i] < d[i] && k[i] > 80) return { dir: -1, stop: atrStop(ctx, i, -1) }; return null; },
      exit(i, _c, pos) { return k[i] != null && (pos.dir > 0 ? k[i] >= 80 : k[i] <= 20); },
    };
  };
}
function rsiMacdCombo() {
  return (ctx) => {
    const r = ctx.rsi(); const { line, signal } = ctx.macd();
    return {
      entry(i) {
        if (i < 1 || r[i] == null || line[i] == null || signal[i] == null) return null;
        const bull = r[i] > 50 && line[i] > signal[i] && (r[i - 1] <= 50 || line[i - 1] <= signal[i - 1]);
        const bear = r[i] < 50 && line[i] < signal[i] && (r[i - 1] >= 50 || line[i - 1] >= signal[i - 1]);
        if (bull) return { dir: 1, stop: atrStop(ctx, i, 1) };
        if (bear) return { dir: -1, stop: atrStop(ctx, i, -1) };
        return null;
      },
      exit(i, _c, pos) { return r[i] != null && line[i] != null && signal[i] != null && (pos.dir > 0 ? (r[i] < 50 && line[i] < signal[i]) : (r[i] > 50 && line[i] > signal[i])); },
    };
  };
}
function bollingerBreakout() {
  return (ctx) => {
    const { upper, lower } = ctx.bb(20, 2); const c = ctx.closes; const vol = ctx.bars.map(b => b.volume);
    const vsma = smaSeries(vol, 20);
    return {
      entry(i) {
        if (upper[i] == null || vsma[i] == null) return null;
        const volOK = vol[i] > vsma[i] * 1.2;
        if (c[i] > upper[i] && volOK) return { dir: 1, stop: atrStop(ctx, i, 1) };
        if (c[i] < lower[i] && volOK) return { dir: -1, stop: atrStop(ctx, i, -1) };
        return null;
      },
      exit(i, _c, pos) { const { mid } = ctx.bb(20, 2); return mid[i] != null && (pos.dir > 0 ? ctx.closes[i] < mid[i] : ctx.closes[i] > mid[i]); },
    };
  };
}
function bollingerMeanReversion() {
  return (ctx) => {
    const { upper, lower, mid } = ctx.bb(20, 2); const c = ctx.closes;
    return {
      entry(i) {
        if (upper[i] == null) return null;
        if (c[i] < lower[i]) return { dir: 1, stop: atrStop(ctx, i, 1.5), target: mid[i] };
        if (c[i] > upper[i]) return { dir: -1, stop: atrStop(ctx, i, -1.5), target: mid[i] };
        return null;
      },
    };
  };
}
function atrChannelBreakout(lb = 20, mult = 1) {
  return (ctx) => {
    const ph = ctx.priorHigh(lb), pl = ctx.priorLow(lb), atr = ctx.atr();
    return {
      entry(i) {
        if (ph[i] == null || atr[i] == null) return null;
        if (ctx.closes[i] > ph[i] + mult * atr[i]) return { dir: 1, stop: atrStop(ctx, i, 2) };
        if (ctx.closes[i] < pl[i] - mult * atr[i]) return { dir: -1, stop: atrStop(ctx, i, -2) };
        return null;
      },
      exit(i, _c, pos) { const m = ctx.ema(20); return m[i] != null && (pos.dir > 0 ? ctx.closes[i] < m[i] : ctx.closes[i] > m[i]); },
    };
  };
}
function priorBreakout(lb) {
  return (ctx) => {
    const ph = ctx.priorHigh(lb), pl = ctx.priorLow(lb);
    return {
      entry(i) { if (ph[i] == null) return null; if (ctx.closes[i] > ph[i]) return { dir: 1, stop: atrStop(ctx, i, 2) }; if (ctx.closes[i] < pl[i]) return { dir: -1, stop: atrStop(ctx, i, -2) }; return null; },
      exit(i, _c, pos) { const m = ctx.ema(20); return m[i] != null && (pos.dir > 0 ? ctx.closes[i] < m[i] : ctx.closes[i] > m[i]); },
    };
  };
}
function rangeBreakout(lb = 20) { return priorBreakout(lb); } // 20-bar range high/low breakout
function swingBreakRetest(lb = 10) {
  return (ctx) => {
    const ph = ctx.priorHigh(lb), pl = ctx.priorLow(lb); const c = ctx.closes;
    return {
      entry(i) {
        if (i < 2 || ph[i] == null) return null;
        // break above prior swing high then a retest close back near it
        const brokeUp = c[i - 1] > ph[i - 1];
        const brokeDn = c[i - 1] < pl[i - 1];
        if (brokeUp && c[i] <= ph[i - 1] * 1.002 && c[i] >= ph[i - 1] * 0.995) return { dir: 1, stop: atrStop(ctx, i, 2) };
        if (brokeDn && c[i] >= pl[i - 1] * 0.998 && c[i] <= pl[i - 1] * 1.005) return { dir: -1, stop: atrStop(ctx, i, -2) };
        return null;
      },
      exit(i, _c, pos) { const m = ctx.ema(20); return m[i] != null && (pos.dir > 0 ? ctx.closes[i] < m[i] : ctx.closes[i] > m[i]); },
    };
  };
}
// Live confluence as a strategy (1d/1w only — weekly confirmation needs 1w bars).
function confluenceStrategy(threshold = 65) {
  return (ctx) => {
    const C = _conf();
    const cache = new Map();
    const at = (i) => { if (!cache.has(i)) cache.set(i, C.confluenceAtBar(ctx.bars, ctx.weekly, i)); return cache.get(i); };
    return {
      entry(i) {
        if (i < 1) return null;
        const s = at(i); if (!s) return null;
        if (s.bullPct >= threshold) return { dir: 1, stop: atrStop(ctx, i, 2), signals: s.signalMap };
        if (s.bullPct <= 100 - threshold) return { dir: -1, stop: atrStop(ctx, i, -2), signals: s.signalMap };
        return null;
      },
      exit(i, _c, pos) { const s = at(i); if (!s) return false; return pos.dir > 0 ? s.direction !== 'BULLISH' : s.direction !== 'BEARISH'; },
    };
  };
}

// ── Registry: base strategies + auto-generated regime-filtered twins ──────────
const BASE = [
  { id: 'sma_10_50',   name: 'SMA 10/50 cross',   family: 'MA Trend',  make: maCross(10, 50, 'sma'),  twin: 'trend' },
  { id: 'sma_20_100',  name: 'SMA 20/100 cross',  family: 'MA Trend',  make: maCross(20, 100, 'sma'), twin: 'trend' },
  { id: 'sma_50_200',  name: 'SMA 50/200 cross',  family: 'MA Trend',  make: maCross(50, 200, 'sma'), twin: 'trend' },
  { id: 'ema_10_50',   name: 'EMA 10/50 cross',   family: 'MA Trend',  make: maCross(10, 50, 'ema'),  twin: 'trend' },
  { id: 'ema_20_100',  name: 'EMA 20/100 cross',  family: 'MA Trend',  make: maCross(20, 100, 'ema'), twin: 'trend' },
  { id: 'ema_50_200',  name: 'EMA 50/200 cross',  family: 'MA Trend',  make: maCross(50, 200, 'ema'), twin: 'trend' },
  { id: 'triple_ema',  name: 'Triple EMA 10/20/50', family: 'MA Trend', make: tripleEma(10, 20, 50),  twin: 'trend' },
  { id: 'px_ema50',    name: 'Price vs EMA50',    family: 'MA Trend',  make: priceVsMA(50, 'ema'),    twin: 'trend' },
  { id: 'px_ema200',   name: 'Price vs EMA200',   family: 'MA Trend',  make: priceVsMA(200, 'ema'),   twin: 'trend' },
  { id: 'macd_cross',  name: 'MACD signal cross', family: 'Momentum',  make: macdCross(),             twin: 'trend' },
  { id: 'macd_hist',   name: 'MACD histogram flip', family: 'Momentum', make: macdHistReversal(),     twin: 'trend' },
  { id: 'rsi_revert',  name: 'RSI 30/70 reversal', family: 'Momentum', make: rsiReversal(30, 70),     twin: 'rangingNormal' },
  { id: 'rsi_center',  name: 'RSI 50 centerline', family: 'Momentum',  make: rsiCenterline(),         twin: 'trend' },
  { id: 'stoch_cross', name: 'Stochastic cross',  family: 'Momentum',  make: stochCross(),            twin: 'rangingNormal' },
  { id: 'rsi_macd',    name: 'RSI + MACD combo',  family: 'Momentum',  make: rsiMacdCombo(),          twin: 'trend' },
  { id: 'bb_breakout', name: 'Bollinger breakout', family: 'Volatility', make: bollingerBreakout(),   twin: 'trend' },
  { id: 'bb_revert',   name: 'Bollinger mean-revert', family: 'Volatility', make: bollingerMeanReversion(), twin: 'rangingNormal' },
  { id: 'atr_channel', name: 'ATR channel breakout', family: 'Volatility', make: atrChannelBreakout(20, 1), twin: 'trend' },
  { id: 'breakout_20', name: '20-bar high/low breakout', family: 'Structure', make: priorBreakout(20), twin: 'trend' },
  { id: 'breakout_50', name: '50-bar high/low breakout', family: 'Structure', make: priorBreakout(50), twin: 'trend' },
  { id: 'range_20',    name: '20-bar range breakout', family: 'Structure', make: rangeBreakout(20),    twin: 'trend' },
  { id: 'swing_retest', name: 'Swing break + retest', family: 'Structure', make: swingBreakRetest(10), twin: 'trend' },
];
const CONFLUENCE = { id: 'confluence', name: 'Live confluence (4+ signals)', family: 'Confluence', make: confluenceStrategy(65), confluenceOnly: true };

// Build the strategy list for a given context. `tf` gates the confluence strategy
// to 1d/1w. Returns [{ id, name, family, regimeFiltered, strat }].
function buildStrategies(ctx) {
  const out = [];
  for (const b of BASE) {
    const base = b.make(ctx);
    out.push({ id: b.id, name: b.name, family: b.family, regimeFiltered: false, strat: base });
    if (b.twin && ALLOW[b.twin]) {
      out.push({ id: b.id + '_rf', name: b.name + ' (regime-filtered)', family: b.family, regimeFiltered: true, strat: withRegimeFilter(base, ALLOW[b.twin]) });
    }
  }
  if (ctx.timeframe === '1d' || ctx.timeframe === '1w') {
    out.push({ id: CONFLUENCE.id, name: CONFLUENCE.name, family: CONFLUENCE.family, regimeFiltered: false, strat: CONFLUENCE.make(ctx) });
  }
  return out;
}

const _strategies = {
  buildContext, simulate, buildStrategies, withRegimeFilter, ALLOW, COSTS, costPctFor, pipSizeFor,
  BASE, CONFLUENCE, confluenceStrategy,
  // exported series helpers (for tests)
  smaSeries, emaSeries, rsiSeries, macdSeries, atrSeries, bbSeries, stochSeries, priorExtremeSeries,
};
(function (g) { g.APEX = g.APEX || {}; g.APEX.strategies = _strategies; })(
  typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this)
);
if (typeof module !== 'undefined' && module.exports) module.exports = _strategies;
