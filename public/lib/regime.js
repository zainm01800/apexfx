// APEX regime classifier — JS port of the Python engine's rule-based regime
// (engine/apex_quant/regime/rule_based.py + features/trend.py + volatility/realized.py).
//
//   trend: sign of a long-MA slope, normalised per-bar by price.
//          slope = (MA_now - MA_{now-slopeWindow}) / (slopeWindow * price)
//          > eps -> 'up', < -eps -> 'down', else 'ranging'   (eps from config.py)
//   vol:   percentile rank of the latest rolling realised-vol within its
//          trailing window. >= 0.70 -> 'high', <= 0.30 -> 'low', else 'normal'.
//
// classifyRegime(closes) matches rule_based.py exactly for a single point.
// regimeSeries(bars) returns the per-bar label array used by the backtest's
// regime-filtered strategies; it is exact in the post-warmup region (where any
// real trade occurs) and uses prefix sums + a precomputed rolling-std series so
// it is O(n) rather than O(n^2). DOM-free → page, worker, and Node. → APEX.regime
'use strict';

const DEFAULT_REGIME_CFG = {
  maWindow: 200,            // RuleBasedConfig.ma_window
  slopeWindow: 21,          // RuleBasedConfig.slope_window
  rangingSlopeEps: 0.0002,  // LIVE value from engine/config.yaml (overrides the 5e-4 Pydantic default)
  volWindow: 21,            // hardcoded min() cap in rule_based._vol_state
  volPercentileWindow: 252, // RuleBasedConfig.vol_percentile_window
  volHighPct: 0.70,         // RuleBasedConfig.vol_high_pct
  volLowPct: 0.30,          // RuleBasedConfig.vol_low_pct
};

// Shared labelling: given a finite-or-NaN slope and a vol result, produce labels +
// confidence exactly as rule_based.classify(). Single source so the per-point and
// the batched paths can never disagree on thresholds.
function _label(slope, volRes, cfg) {
  const eps = cfg.rangingSlopeEps;
  let trend, trendConf;
  if (!isFinite(slope)) { trend = 'ranging'; trendConf = 0; }
  else if (slope > eps)  { trend = 'up';   trendConf = Math.min(1, (slope - eps) / eps + 0.5); }
  else if (slope < -eps) { trend = 'down'; trendConf = Math.min(1, (-slope - eps) / eps + 0.5); }
  else { trend = 'ranging'; trendConf = eps > 0 ? Math.min(1, 1 - Math.abs(slope) / eps) : 1; }
  const confidence = Math.max(0, Math.min(1, 0.5 * trendConf + 0.5 * volRes.conf));
  return { trend, vol: volRes.vol, confidence, slope, volPct: volRes.pct };
}

function _volFromPct(pct, cfg) {
  if (!isFinite(pct)) return { vol: 'normal', conf: 0, pct: NaN };
  if (pct >= cfg.volHighPct) return { vol: 'high', conf: Math.min(1, (pct - cfg.volHighPct) / Math.max(1e-9, 1 - cfg.volHighPct)), pct };
  if (pct <= cfg.volLowPct)  return { vol: 'low',  conf: Math.min(1, (cfg.volLowPct - pct) / Math.max(1e-9, cfg.volLowPct)), pct };
  const mid = 0.5 * (cfg.volLowPct + cfg.volHighPct);
  const half = 0.5 * (cfg.volHighPct - cfg.volLowPct);
  return { vol: 'normal', conf: Math.max(0, 1 - Math.abs(pct - mid) / Math.max(1e-9, half)), pct };
}

function _logReturns(closes) {
  const c = closes.filter(x => x > 0);
  const r = [];
  for (let i = 1; i < c.length; i++) r.push(Math.log(c[i]) - Math.log(c[i - 1]));
  return r;
}

function _stdSample(arr, from, to) { // ddof=1 over arr[from..to] inclusive
  const n = to - from + 1;
  if (n < 2) return NaN;
  let m = 0; for (let k = from; k <= to; k++) m += arr[k]; m /= n;
  let v = 0; for (let k = from; k <= to; k++) { const d = arr[k] - m; v += d * d; }
  return Math.sqrt(v / (n - 1));
}

// --- single point (exact mirror of rule_based.py) ---
function trendSlope(closes, maWindow, slopeWindow) {
  const n = closes.length;
  if (n < maWindow + slopeWindow) return NaN;
  const iNow = n - 1, iPrev = n - 1 - slopeWindow;
  if (iPrev - maWindow + 1 < 0) return NaN;
  const meanEnding = (end) => { let s = 0; for (let k = end - maWindow + 1; k <= end; k++) s += closes[k]; return s / maWindow; };
  const maNow = meanEnding(iNow), maPrev = meanEnding(iPrev), price = closes[n - 1];
  if (!isFinite(maNow) || !isFinite(maPrev) || price <= 0) return NaN;
  return (maNow - maPrev) / (slopeWindow * price);
}

function _volState(closes, cfg) {
  const r = _logReturns(closes);
  const w = Math.max(2, Math.min(r.length, cfg.volWindow));
  if (r.length < w + 5) return { vol: 'normal', conf: 0, pct: NaN };
  const roll = [];
  for (let i = w - 1; i < r.length; i++) roll.push(_stdSample(r, i - w + 1, i));
  const lookback = roll.slice(-cfg.volPercentileWindow);
  if (lookback.length < 5) return { vol: 'normal', conf: 0, pct: NaN };
  const current = lookback[lookback.length - 1];
  let less = 0; for (const x of lookback) if (x < current) less++;
  return _volFromPct(less / lookback.length, cfg);
}

function classifyRegime(closes, cfg = DEFAULT_REGIME_CFG) {
  return _label(trendSlope(closes, cfg.maWindow, cfg.slopeWindow), _volState(closes, cfg), cfg);
}

// --- batched per-bar labels for the whole series (efficient) ---
// Returns an array `labels[i] = { trend, vol, confidence }` for each bar i, where
// label i is computed from data closes[0..i] only (point-in-time, no lookahead).
function regimeSeries(bars, cfg = DEFAULT_REGIME_CFG) {
  const closes = bars.map(b => b.close);
  const n = closes.length;
  const out = new Array(n);

  // prefix sums for O(1) moving-average means
  const pre = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) pre[i + 1] = pre[i] + closes[i];
  const meanSpan = (a, b) => (pre[b + 1] - pre[a]) / (b - a + 1); // mean closes[a..b]

  // full log-return series + its rolling realised-vol (fixed w in the live region)
  const r = _logReturns(closes);                 // length ~ n-1
  const w = cfg.volWindow;
  const roll = new Array(r.length).fill(NaN);     // roll[j] valid for j >= w-1
  for (let j = w - 1; j < r.length; j++) roll[j] = _stdSample(r, j - w + 1, j);

  for (let i = 0; i < n; i++) {
    // trend slope from closes[0..i]
    let slope = NaN;
    if (i + 1 >= cfg.maWindow + cfg.slopeWindow) {
      const iPrev = i - cfg.slopeWindow;
      const maNow = meanSpan(i - cfg.maWindow + 1, i);
      const maPrev = meanSpan(iPrev - cfg.maWindow + 1, iPrev);
      const price = closes[i];
      if (isFinite(maNow) && isFinite(maPrev) && price > 0) slope = (maNow - maPrev) / (cfg.slopeWindow * price);
    }
    // vol percentile: returns available at bar i are r[0..i-1]; current roll at i-1
    let volRes = { vol: 'normal', conf: 0, pct: NaN };
    const rEnd = i - 1; // last return index known at bar i
    if (rEnd >= w - 1 && rEnd + 1 >= w + 5) {
      const lbStart = Math.max(w - 1, rEnd - cfg.volPercentileWindow + 1);
      const len = rEnd - lbStart + 1;
      if (len >= 5) {
        const current = roll[rEnd];
        let less = 0; for (let j = lbStart; j <= rEnd; j++) if (roll[j] < current) less++;
        volRes = _volFromPct(less / len, cfg);
      }
    }
    const lab = _label(slope, volRes, cfg);
    out[i] = { trend: lab.trend, vol: lab.vol, confidence: lab.confidence };
  }
  return out;
}

const _regime = { DEFAULT_REGIME_CFG, classifyRegime, regimeSeries, trendSlope };
(function (g) { g.APEX = g.APEX || {}; g.APEX.regime = _regime; })(
  typeof globalThis !== 'undefined' ? globalThis : (typeof self !== 'undefined' ? self : this)
);
if (typeof module !== 'undefined' && module.exports) module.exports = _regime;
