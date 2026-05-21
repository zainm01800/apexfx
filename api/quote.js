// /api/quote — Vercel Edge Function
// Returns stock fundamentals and quote data from Finnhub.
// GET /api/quote?sym=AAPL&type=Stock

export const config = { runtime: 'edge' };

const FINNHUB_KEY = process.env.FINNHUB_API_KEY;

function isAllowedOrigin(origin, host) {
  if (!origin) return true;
  try {
    const url = new URL(origin);
    if (url.host === host) return true;
    if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') return true;
  } catch {}
  return false;
}

async function finnhubFetch(path, signal) {
  const res = await fetch(`https://finnhub.io/api/v1${path}&token=${FINNHUB_KEY}`, {
    headers: { 'User-Agent': 'Mozilla/5.0' },
    signal,
  });
  if (!res.ok) return null;
  return res.json();
}

export default async function handler(req) {
  const url = new URL(req.url);
  const origin = req.headers.get('origin');
  const sym = url.searchParams.get('sym');
  const type = url.searchParams.get('type') || 'Stock';
  const allowedOrigin = isAllowedOrigin(origin, url.host) ? (origin || url.origin) : url.origin;

  const corsHeaders = {
    'Access-Control-Allow-Origin': allowedOrigin,
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Content-Type': 'application/json',
    'Cache-Control': 's-maxage=60, stale-while-revalidate=120',
  };

  if (origin && !isAllowedOrigin(origin, url.host)) {
    return new Response(JSON.stringify({ error: 'Origin not allowed' }), { status: 403, headers: corsHeaders });
  }
  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }
  if (!sym) {
    return new Response(JSON.stringify({ error: 'sym parameter required' }), { status: 400, headers: corsHeaders });
  }

  // Only Finnhub stock endpoints are supported; for Forex/Crypto/Futures return partial data
  const ticker = sym.toUpperCase();

  try {
    const abort = AbortSignal.timeout(12000);

    // For non-stock asset types, Finnhub fundamentals don't apply — return empty
    if (type !== 'Stock' && type !== 'ETF') {
      return new Response(JSON.stringify({ name: ticker }), { headers: corsHeaders });
    }

    const [quoteData, profileData, metricsData] = await Promise.all([
      finnhubFetch(`/quote?symbol=${encodeURIComponent(ticker)}`, abort),
      finnhubFetch(`/stock/profile2?symbol=${encodeURIComponent(ticker)}`, abort),
      finnhubFetch(`/stock/metric?symbol=${encodeURIComponent(ticker)}&metric=all`, abort),
    ]);

    const m = metricsData?.metric || {};

    const quote = {
      name:              profileData?.name || ticker,
      currentPrice:      quoteData?.c ?? null,
      change:            quoteData?.d ?? null,
      changePercent:     quoteData?.dp ? quoteData.dp / 100 : null,
      marketCap:         profileData?.marketCapitalization
                           ? profileData.marketCapitalization * 1e6
                           : null,
      pe:                m['peNormalizedAnnual'] ?? m['peTTM'] ?? null,
      forwardPE:         m['peForward'] ?? null,
      eps:               m['epsNormalizedAnnual'] ?? m['epsTTM'] ?? null,
      dividendYield:     m['dividendYieldIndicatedAnnual']
                           ? m['dividendYieldIndicatedAnnual'] / 100
                           : null,
      week52High:        m['52WeekHigh'] ?? null,
      week52Low:         m['52WeekLow'] ?? null,
      avgVolume:         m['10DayAverageTradingVolume']
                           ? m['10DayAverageTradingVolume'] * 1e6
                           : null,
      beta:              m['beta'] ?? null,
      revenueGrowth:     m['revenueGrowthTTMYoy']
                           ? m['revenueGrowthTTMYoy'] / 100
                           : null,
      earningsGrowth:    m['epsGrowthTTMYoy']
                           ? m['epsGrowthTTMYoy'] / 100
                           : null,
      recommendationKey: null,
      targetMeanPrice:   m['targetPrice'] ?? null,
    };

    return new Response(JSON.stringify(quote), { headers: corsHeaders });
  } catch (err) {
    return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: corsHeaders });
  }
}
