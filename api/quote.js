// /api/quote — Vercel Edge Function
// Returns stock fundamentals and quote data from Yahoo Finance.
// GET /api/quote?sym=AAPL&type=Stock

export const config = { runtime: 'edge' };

function toYahooTicker(sym, type) {
  if (type === 'Forex') return sym.replace('/', '') + '=X';
  if (type === 'Crypto') return sym.replace('/', '-');
  if (type === 'Futures') {
    const m = { 'ES1!': 'ES=F', 'CL1!': 'CL=F', 'GC1!': 'GC=F', 'NQ1!': 'NQ=F' };
    return m[sym] || sym;
  }
  return sym;
}

function isAllowedOrigin(origin, host) {
  if (!origin) return true;
  try {
    const url = new URL(origin);
    if (url.host === host) return true;
    if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') return true;
  } catch {}
  return false;
}

export default async function handler(req) {
  const url = new URL(req.url);
  const origin = req.headers.get('origin');
  const { searchParams } = url;
  const sym = searchParams.get('sym');
  const type = searchParams.get('type') || 'Stock';
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

  const ticker = toYahooTicker(sym.toUpperCase(), type);

  try {
    const fetchUrl =
      `https://query1.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(ticker)}` +
      `?modules=price%2CsummaryDetail%2CfinancialData%2CdefaultKeyStatistics`;

    const res = await fetch(fetchUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
      },
      signal: AbortSignal.timeout(10000),
    });

    if (!res.ok) {
      return new Response(
        JSON.stringify({ error: `Yahoo Finance error ${res.status}` }),
        { status: res.status, headers: corsHeaders }
      );
    }

    const data = await res.json();
    const result = data?.quoteSummary?.result?.[0];

    if (!result) {
      return new Response(
        JSON.stringify({ error: 'No data found for this symbol' }),
        { status: 404, headers: corsHeaders }
      );
    }

    const price    = result.price || {};
    const summary  = result.summaryDetail || {};
    const financial = result.financialData || {};
    const keyStats = result.defaultKeyStatistics || {};

    const quote = {
      name:              price.longName?.raw || price.shortName?.raw || sym,
      currentPrice:      price.regularMarketPrice?.raw ?? null,
      change:            price.regularMarketChange?.raw ?? null,
      changePercent:     price.regularMarketChangePercent?.raw ?? null,
      marketCap:         price.marketCap?.raw ?? null,
      pe:                summary.trailingPE?.raw ?? null,
      forwardPE:         summary.forwardPE?.raw ?? null,
      eps:               keyStats.trailingEps?.raw ?? null,
      dividendYield:     summary.dividendYield?.raw ?? null,
      week52High:        summary.fiftyTwoWeekHigh?.raw ?? null,
      week52Low:         summary.fiftyTwoWeekLow?.raw ?? null,
      avgVolume:         summary.averageVolume?.raw ?? null,
      beta:              summary.beta?.raw ?? null,
      revenueGrowth:     financial.revenueGrowth?.raw ?? null,
      earningsGrowth:    financial.earningsGrowth?.raw ?? null,
      recommendationKey: financial.recommendationKey?.raw ?? null,
      targetMeanPrice:   financial.targetMeanPrice?.raw ?? null,
    };

    return new Response(JSON.stringify(quote), { headers: corsHeaders });
  } catch (err) {
    return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: corsHeaders });
  }
}
