// /api/ws-token — Vercel serverless function
// Returns the Finnhub WebSocket token for live tick subscriptions.
// Keeping it server-side means the key never appears in browser source.

export const config = { runtime: 'edge' };

const FINNHUB_KEY = process.env.FINNHUB_API_KEY; // Set in Vercel dashboard → Environment Variables

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
  const corsHeaders = {
    'Access-Control-Allow-Origin': allowedOrigin,
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store', // never cache a token
  };

  if (origin && !isAllowedOrigin(origin, url.host)) {
    return new Response(JSON.stringify({ error: 'Origin not allowed' }), { status: 403, headers: corsHeaders });
  }

  if (!FINNHUB_KEY) {
    return new Response(JSON.stringify({ error: 'Not configured' }), { status: 503, headers: corsHeaders });
  }

  // Return the token — browser uses it to open the WebSocket directly
  // (WebSocket connections can't be proxied through edge functions)
  return new Response(JSON.stringify({ token: FINNHUB_KEY }), { status: 200, headers: corsHeaders });
}
