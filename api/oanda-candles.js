// /api/oanda-candles — Vercel serverless function (Edge Runtime)
// Proxies OANDA's v20 API (trying Live first, then falling back to Practice)
// and returns clean OHLCV bars as JSON.
// Called by the frontend as: GET /api/oanda-candles?sym=EUR/USD&tf=15m&from=...&to=...

export const config = { runtime: 'edge' };

// Map standard TF to OANDA granularity
const GRANULARITY_MAP = {
  '1m':  'M1',
  '5m':  'M5',
  '15m': 'M15',
  '30m': 'M30',
  '1h':  'H1',
  '4h':  'H4',
  '1d':  'D',
  '1w':  'W'
};

function isAllowedOrigin(origin, host) {
  if (!origin) return true;
  try {
    const url = new URL(origin);
    if (url.host === host) return true;
    if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') return true;
  } catch {}
  return false;
}

async function fetchOanda(baseUrl, oandaSymbol, granularity, fromISO, toISO, apiKey) {
  let oandaUrl = `${baseUrl}/v3/instruments/${oandaSymbol}/candles?granularity=${granularity}&price=M`;
  if (fromISO) oandaUrl += `&from=${encodeURIComponent(fromISO)}`;
  if (toISO) oandaUrl += `&to=${encodeURIComponent(toISO)}`;

  const res = await fetch(oandaUrl, {
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json'
    },
    signal: AbortSignal.timeout(12000)
  });
  return res;
}

export default async function handler(req) {
  const { searchParams } = new URL(req.url);
  const url = new URL(req.url);
  const origin = req.headers.get('origin');
  
  const sym  = searchParams.get('sym')  || 'EUR/USD';
  const tf   = searchParams.get('tf')   || '1d';
  const from = searchParams.get('from');
  const to   = searchParams.get('to');
  
  const allowedOrigin = isAllowedOrigin(origin, url.host) ? (origin || url.origin) : url.origin;
  const corsHeaders = {
    'Access-Control-Allow-Origin': allowedOrigin,
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
    'Cache-Control': 'public, max-age=60, s-maxage=300'
  };

  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  // Authenticate using the environment key
  const apiKey = process.env.APEX_OANDA_API_KEY || '';
  if (!apiKey) {
    return new Response(JSON.stringify({ error: 'APEX_OANDA_API_KEY environment variable is not configured' }), {
      status: 500,
      headers: corsHeaders
    });
  }

  // Convert symbol: EUR/USD -> EUR_USD
  const oandaSymbol = sym.replace('/', '_').toUpperCase();

  // Convert granularity
  const granularity = GRANULARITY_MAP[tf];
  if (!granularity) {
    return new Response(JSON.stringify({ error: `Unsupported timeframe: ${tf}` }), {
      status: 400,
      headers: corsHeaders
    });
  }

  const fromISO = from ? new Date(parseInt(from, 10) * 1000).toISOString() : null;
  const toISO = to ? new Date(parseInt(to, 10) * 1000).toISOString() : null;

  try {
    // 1. Try OANDA Live server first
    let res = await fetchOanda('https://api-fxtrade.oanda.com', oandaSymbol, granularity, fromISO, toISO, apiKey);
    
    // 2. Fallback to Practice/Demo server on auth error (or if live failed to connect)
    if (!res.ok && (res.status === 401 || res.status === 403)) {
      try {
        res = await fetchOanda('https://api-fxpractice.oanda.com', oandaSymbol, granularity, fromISO, toISO, apiKey);
      } catch (err) {
        // Fallback failed, keep original error response
      }
    }

    if (!res.ok) {
      const errText = await res.text();
      return new Response(JSON.stringify({ error: `OANDA API returned HTTP ${res.status}`, details: errText }), {
        status: res.status,
        headers: corsHeaders
      });
    }

    const data = await res.json();
    const candles = data?.candles || [];

    // Map OANDA candles to APEX standard format
    const mappedBars = candles.map(c => {
      const timeSec = Math.floor(new Date(c.time).getTime() / 1000);
      return {
        time: timeSec,
        open: parseFloat(c.mid.o),
        high: parseFloat(c.mid.h),
        low: parseFloat(c.mid.l),
        close: parseFloat(c.mid.c),
        volume: parseInt(c.volume, 10) || 0
      };
    }).filter(b => b.open && b.high && b.low && b.close);

    return new Response(JSON.stringify(mappedBars), {
      status: 200,
      headers: corsHeaders
    });

  } catch (error) {
    return new Response(JSON.stringify({ error: 'Failed to fetch OANDA historical candles', details: String(error) }), {
      status: 500,
      headers: corsHeaders
    });
  }
}
