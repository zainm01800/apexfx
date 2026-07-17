// /api/paper — Forward paper-trading book, READ-ONLY (mirrors /api/mt4-trades).
// GET /api/paper                    — daily equity snapshots (apex_paper_daily, chronological)
// GET /api/paper?table=daily&limit=N — same, explicit (limit cap 500)
// GET /api/paper?table=positions    — open paper positions (apex_paper_positions)

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://dtiuwllodzqpbwohzrgj.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k';

function supaHeaders() {
  return {
    'apikey': SUPA_ANON,
    'Authorization': `Bearer ${SUPA_ANON}`,
    'Content-Type': 'application/json',
  };
}

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
  };
}

export default async function handler(req) {
  const url    = new URL(req.url);
  const origin = req.headers.get('origin');
  const cors   = corsHeaders(origin);

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (req.method !== 'GET') return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers: cors });

  try {
    const table = url.searchParams.get('table') || 'daily';
    const limit = Math.min(500, parseInt(url.searchParams.get('limit') || '120', 10));

    let queryUrl;
    if (table === 'positions') {
      queryUrl = `${SUPA_URL}/rest/v1/apex_paper_positions?order=instrument.asc&limit=${limit}`;
    } else {
      // Daily snapshots oldest→newest so the client can draw the curve as-is.
      queryUrl = `${SUPA_URL}/rest/v1/apex_paper_daily?order=date.asc&limit=${limit}`;
    }

    const response = await fetch(queryUrl, {
      method: 'GET',
      headers: supaHeaders(),
    });

    if (!response.ok) {
      const txt = await response.text();
      return new Response(JSON.stringify({ error: `Supabase query failed: ${txt}` }), { status: response.status, headers: cors });
    }

    const data = await response.json();
    return new Response(JSON.stringify(data), { status: 200, headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: cors });
  }
}
