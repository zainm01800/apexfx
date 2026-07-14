// /api/economic-calendar — Vercel edge function
// Fetches economic calendar events via Finnhub.
// GET /api/economic-calendar?from=YYYY-MM-DD&to=YYYY-MM-DD

export const config = { runtime: 'edge' };

const FINNHUB_KEY = process.env.FINNHUB_API_KEY;
const FBASE = 'https://finnhub.io/api/v1';

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
  const allowedOrigin = isAllowedOrigin(origin, url.host) ? (origin || url.origin) : url.origin;
  const cors = {
    'Access-Control-Allow-Origin': allowedOrigin,
    'Content-Type': 'application/json',
    'Cache-Control': 's-maxage=600, stale-while-revalidate=1200',
  };

  if (origin && !isAllowedOrigin(origin, url.host)) {
    return new Response(JSON.stringify([]), { status: 403, headers: cors });
  }

  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: cors });
  }

  if (!FINNHUB_KEY) {
    return new Response(JSON.stringify({ error: 'No Finnhub API Key configured' }), { status: 500, headers: cors });
  }

  const { searchParams } = new URL(req.url);
  const from = searchParams.get('from') || new Date(Date.now() - 3 * 24 * 3600 * 1000).toISOString().slice(0, 10);
  const to = searchParams.get('to') || new Date(Date.now() + 7 * 24 * 3600 * 1000).toISOString().slice(0, 10);

  try {
    const queryUrl = `${FBASE}/calendar/economic?from=${from}&to=${to}&token=${FINNHUB_KEY}`;
    const res = await fetch(queryUrl, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) {
      const errText = await res.text();
      return new Response(JSON.stringify({ error: `Finnhub returned ${res.status}: ${errText}` }), { status: res.status, headers: cors });
    }
    const data = await res.json();
    return new Response(JSON.stringify(data.economicCalendar || []), { status: 200, headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: cors });
  }
}
