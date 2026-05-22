// /api/macro-intermarket
// GET /api/macro-intermarket
// Returns quantified macro intermarket signals: yield curve, HY credit spreads,
// VIX, DXY, bond-equity correlation — all with interpretations pre-baked.
// Data sources: FRED (St. Louis Fed) CSV + Yahoo Finance quotes
// No API key required. Cached 1 hour server-side.

export const config = { runtime: 'edge' };

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Content-Type': 'application/json',
  'Cache-Control': 'public, s-maxage=3600, stale-while-revalidate=7200',
};

// ── FRED CSV (no key required for public series) ─────────────────────────────
async function fetchFredLatest(seriesId) {
  try {
    const res = await fetch(
      `https://fred.stlouisfed.org/graph/fredgraph.csv?id=${seriesId}`,
      {
        headers: { 'User-Agent': 'APEX-Research/1.0 (market analysis tool)' },
        signal: AbortSignal.timeout(9000),
      }
    );
    if (!res.ok) return null;
    const text = await res.text();
    const lines = text.trim().split('\n');
    // Walk backwards to find latest non-missing value
    for (let i = lines.length - 1; i >= 1; i--) {
      const parts = lines[i].split(',');
      if (parts.length < 2) continue;
      const val = parts[1].trim();
      if (val === '.' || val === '') continue; // FRED uses '.' for missing
      const num = parseFloat(val);
      if (!isNaN(num)) return { date: parts[0].trim(), value: num };
    }
    return null;
  } catch { return null; }
}

// ── Yahoo Finance multi-quote ─────────────────────────────────────────────────
async function fetchYahooQuotes(symbols) {
  try {
    const url = `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${encodeURIComponent(symbols)}&fields=regularMarketPrice,regularMarketChangePercent`;
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
      },
      signal: AbortSignal.timeout(9000),
    });
    if (!res.ok) return {};
    const data = await res.json();
    const results = data?.quoteResponse?.result || [];
    return Object.fromEntries(results.map(q => [q.symbol, q]));
  } catch { return {}; }
}

// ── Interpreters ──────────────────────────────────────────────────────────────

function interpretYieldCurve(spread) {
  if (spread == null) return {};
  if (spread < -0.75) return {
    label: 'deeply inverted',
    regime: 'recession-warning',
    signal: `${spread.toFixed(2)}% — deep inversion historically precedes recession by 12–18 months; demand destruction risk elevated`,
  };
  if (spread < 0) return {
    label: 'inverted',
    regime: 'late-cycle',
    signal: `${spread.toFixed(2)}% — inverted curve signals tightening financial conditions and late-cycle dynamics`,
  };
  if (spread < 0.30) return {
    label: 'flat',
    regime: 'late-cycle',
    signal: `+${spread.toFixed(2)}% — very flat curve, near-zero term premium, economic uncertainty elevated`,
  };
  if (spread < 1.00) return {
    label: 'normal',
    regime: 'expansion',
    signal: `+${spread.toFixed(2)}% — healthy steepness supportive of bank lending, growth, and risk assets`,
  };
  return {
    label: 'steep',
    regime: 'early-cycle',
    signal: `+${spread.toFixed(2)}% — steep curve signals strong growth expectations; typically bullish for cyclicals and banks`,
  };
}

function interpretHyOas(oasPct) {
  if (oasPct == null) return {};
  const bps = Math.round(oasPct * 100);
  if (bps < 250) return { bps, label: 'benign', regime: 'risk-on',  signal: `${bps}bps — credit markets calm; low systemic stress; supportive of equity risk-taking` };
  if (bps < 350) return { bps, label: 'slightly elevated', regime: 'cautious', signal: `${bps}bps — mild credit stress beginning; monitor for further widening` };
  if (bps < 500) return { bps, label: 'elevated', regime: 'risk-off', signal: `${bps}bps — significant credit stress; equity market typically struggles here` };
  return           { bps, label: 'distressed', regime: 'crisis',  signal: `${bps}bps — crisis-level credit spreads; maximum defensive positioning warranted` };
}

function interpretVix(vix) {
  if (vix == null) return {};
  if (vix < 12) return { label: 'extreme complacency', regime: 'low-vol',  signal: `${vix.toFixed(1)} — dangerously low; typically precedes sharp volatility spikes; crowded long-equity positioning` };
  if (vix < 18) return { label: 'calm',                regime: 'low-vol',  signal: `${vix.toFixed(1)} — benign environment; risk-on conditions supportive` };
  if (vix < 25) return { label: 'normal',              regime: 'normal',   signal: `${vix.toFixed(1)} — typical volatility; no extreme fear or complacency` };
  if (vix < 35) return { label: 'elevated fear',       regime: 'elevated', signal: `${vix.toFixed(1)} — elevated fear; potential for snap-back rallies; watch for capitulation signals` };
  return               { label: 'extreme fear',        regime: 'panic',    signal: `${vix.toFixed(1)} — panic/capitulation zone; historically excellent long-term entry for patient buyers` };
}

function interpretDxy(dxy) {
  if (dxy == null) return {};
  if (dxy > 108) return { label: 'very strong', signal: `${dxy.toFixed(2)} — strong headwind for EM equities, gold, oil, and US multinational earnings` };
  if (dxy > 103) return { label: 'strong',      signal: `${dxy.toFixed(2)} — moderate headwind for commodities and international risk assets` };
  if (dxy > 97)  return { label: 'neutral',     signal: `${dxy.toFixed(2)} — dollar roughly neutral; limited currency-driven tailwind or headwind` };
  if (dxy > 91)  return { label: 'weak',        signal: `${dxy.toFixed(2)} — weak dollar tailwind for EM, gold, commodities, US multinationals` };
  return               { label: 'very weak',   signal: `${dxy.toFixed(2)} — very weak dollar; strong boost for gold, EM, oil; potential USD confidence concern` };
}

// ── Handler ───────────────────────────────────────────────────────────────────

export default async function handler(req) {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });

  // Fetch FRED (yield curve + HY OAS) and Yahoo Finance quotes in parallel
  const [fredData, yahooQuotes] = await Promise.all([
    Promise.all([
      fetchFredLatest('T10Y2Y'),          // 10Y − 2Y Treasury spread
      fetchFredLatest('BAMLH0A0HYM2OAS'), // ICE BofA HY OAS (in %, e.g. 2.63 = 263 bps)
    ]),
    fetchYahooQuotes('^VIX,DX-Y.NYB,TLT,SPY'),
  ]);

  const [yieldCurveRaw, hyOasRaw] = fredData;
  const vixQ = yahooQuotes['^VIX'];
  const dxyQ = yahooQuotes['DX-Y.NYB'];
  const tltQ = yahooQuotes['TLT'];
  const spyQ = yahooQuotes['SPY'];

  const yieldSpread  = yieldCurveRaw?.value ?? null;
  const hyOasVal     = hyOasRaw?.value ?? null;
  const vix          = vixQ?.regularMarketPrice ?? null;
  const dxy          = dxyQ?.regularMarketPrice ?? null;
  const tltChg       = tltQ?.regularMarketChangePercent ?? null;
  const spyChg       = spyQ?.regularMarketChangePercent ?? null;

  // Bond-equity correlation inferred from today's price moves
  let bondEquityCorr = null;
  if (tltChg != null && spyChg != null) {
    const sameDir = (tltChg > 0) === (spyChg > 0);
    const tltStr  = `TLT ${tltChg >= 0 ? '+' : ''}${tltChg.toFixed(2)}%`;
    const spyStr  = `SPY ${spyChg >= 0 ? '+' : ''}${spyChg.toFixed(2)}%`;
    bondEquityCorr = sameDir
      ? `positive correlation (${tltStr}, ${spyStr}) — bonds NOT hedging equities today; rate/inflation shock regime`
      : `negative correlation (${tltStr}, ${spyStr}) — bonds hedging equities; traditional flight-to-quality regime active`;
  }

  // Overall regime summary for AI context
  const ycData  = { value: yieldSpread, date: yieldCurveRaw?.date, ...interpretYieldCurve(yieldSpread) };
  const hyData  = { value: hyOasVal,    date: hyOasRaw?.date,      ...interpretHyOas(hyOasVal) };
  const vixData = { value: vix,         ...interpretVix(vix) };
  const dxyData = { value: dxy,         ...interpretDxy(dxy) };

  return new Response(JSON.stringify({
    yield_curve:              ycData,
    hy_oas:                   hyData,
    vix:                      vixData,
    dxy:                      dxyData,
    bond_equity_correlation:  bondEquityCorr,
    tlt_1d_pct:               tltChg != null ? +tltChg.toFixed(2) : null,
    spy_1d_pct:               spyChg != null ? +spyChg.toFixed(2) : null,
  }), { status: 200, headers: CORS });
}
