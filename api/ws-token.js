// /api/ws-token — Vercel serverless function
// Returns the Finnhub WebSocket token for live tick subscriptions.
// Keeping it server-side means the key never appears in browser source.

export const config = { runtime: 'edge' };

const FINNHUB_KEY = process.env.FINNHUB_API_KEY; // Set in Vercel dashboard → Environment Variables

export default async function handler(req) {
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store', // never cache a token
  };

  if (!FINNHUB_KEY) {
    return new Response(JSON.stringify({ error: 'Not configured' }), { status: 503, headers: corsHeaders });
  }

  // Return the token — browser uses it to open the WebSocket directly
  // (WebSocket connections can't be proxied through edge functions)
  return new Response(JSON.stringify({ token: FINNHUB_KEY }), { status: 200, headers: corsHeaders });
}
