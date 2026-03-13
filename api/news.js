// /api/news — Vercel serverless function
// Proxies Finnhub company-news so the API key never appears in browser source.
// Called by frontend as: GET /api/news?symbol=AAPL&from=2024-01-01&to=2024-01-31

export const config = { runtime: 'edge' };

const FINNHUB_KEY = process.env.FINNHUB_API_KEY;

export default async function handler(req) {
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
    'Cache-Control': 's-maxage=1800, stale-while-revalidate=3600',
  };

  if (!FINNHUB_KEY) {
    return new Response(JSON.stringify([]), { status: 200, headers: corsHeaders });
  }

  const { searchParams } = new URL(req.url);
  const symbol = searchParams.get('symbol') || '';
  const from   = searchParams.get('from')   || '';
  const to     = searchParams.get('to')     || '';

  if (!symbol) {
    return new Response(JSON.stringify({ error: 'Missing symbol' }), { status: 400, headers: corsHeaders });
  }

  try {
    const url = `https://finnhub.io/api/v1/company-news?symbol=${encodeURIComponent(symbol)}&from=${from}&to=${to}&token=${FINNHUB_KEY}`;
    const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) return new Response(JSON.stringify([]), { status: 200, headers: corsHeaders });
    const data = await res.json();
    return new Response(JSON.stringify(data), { status: 200, headers: corsHeaders });
  } catch (e) {
    return new Response(JSON.stringify([]), { status: 200, headers: corsHeaders });
  }
}
