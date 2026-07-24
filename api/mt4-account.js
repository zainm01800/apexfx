// /api/mt4-account — Fetch live MT4 account stats (balance, equity, floating P&L, starting balance)
// GET /api/mt4-account — returns single object representing current status

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://cuvchjhaojhmxfgczndy.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';
const TABLE     = `${SUPA_URL}/rest/v1/apex_mt4_account`;

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
    // Select the row with id=1 representing active account info
    const queryUrl = `${TABLE}?id=eq.1`;

    const response = await fetch(queryUrl, {
      method: 'GET',
      headers: supaHeaders(),
    });

    if (!response.ok) {
      const txt = await response.text();
      return new Response(JSON.stringify({ error: `Supabase query failed: ${txt}` }), { status: response.status, headers: cors });
    }

    const data = await response.json();
    // Return first element if array, or empty object
    const record = (Array.isArray(data) && data.length > 0) ? data[0] : {};
    return new Response(JSON.stringify(record), { status: 200, headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: cors });
  }
}
