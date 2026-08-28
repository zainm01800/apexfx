// /api/paper — Forward paper-trading book, READ-ONLY (mirrors /api/mt4-trades).
// GET /api/paper                    — daily equity snapshots (apex_paper_daily, chronological)
// GET /api/paper?table=daily&limit=N — same, explicit (limit cap 500)
// GET /api/paper?table=positions    — open paper positions (apex_paper_positions)
// GET /api/paper?book=b             — challenger book (252+spill50, apex_paper_b_* tables); default = A
// GET /api/paper?book=c             — champion book (63/126/252, apex_paper_c_* tables)
// GET /api/paper?book=r             — Book R-252 $100k USD ETF forward-paper mirror

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://cuvchjhaojhmxfgczndy.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';

// Book A = frozen proof (Book D); Book B = challenger (Book H gold 252 + spill50); Book C = champion multi-horizon.
// Same schema/RLS on all pairs; only the table names differ.
const TABLES = {
  a: { daily: 'apex_paper_daily', positions: 'apex_paper_positions' },
  b: { daily: 'apex_paper_b_daily', positions: 'apex_paper_b_positions' },
  c: { daily: 'apex_paper_c_daily', positions: 'apex_paper_c_positions' },
  r: { fallbackId: '__apex_book_r_252_forward_paper_runtime__' },
};

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
    'Cache-Control': 'public, s-maxage=10, stale-while-revalidate=59',
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
    const book  = TABLES[url.searchParams.get('book')] ? url.searchParams.get('book') : 'a';

    // Book R is an isolated forward-paper evidence stream stored as one
    // namespaced JSONB document. It has no funded/broker table pair.
    if (book === 'r') {
      const stateUrl = `${SUPA_URL}/rest/v1/apex_analyses?id=eq.${TABLES.r.fallbackId}&select=feature_vector&limit=1`;
      const stateResponse = await fetch(stateUrl, { method: 'GET', headers: supaHeaders() });
      if (!stateResponse.ok) {
        const txt = await stateResponse.text();
        return new Response(JSON.stringify({ error: `Book R mirror query failed: ${txt}` }), { status: stateResponse.status, headers: cors });
      }
      const stateRows = await stateResponse.json();
      const state = stateRows[0] && stateRows[0].feature_vector;
      const key = table === 'positions' ? 'positions' : 'daily';
      const rows = state && Array.isArray(state[key]) ? state[key].slice(-limit) : [];
      return new Response(JSON.stringify(rows), { status: 200, headers: cors });
    }

    let queryUrl;
    if (table === 'positions') {
      queryUrl = `${SUPA_URL}/rest/v1/${TABLES[book].positions}?order=instrument.asc&limit=${limit}`;
    } else {
      // Daily snapshots oldest→newest so the client can draw the curve as-is.
      queryUrl = `${SUPA_URL}/rest/v1/${TABLES[book].daily}?order=date.asc&limit=${limit}`;
    }

    const response = await fetch(queryUrl, {
      method: 'GET',
      headers: supaHeaders(),
    });

    if (!response.ok) {
      // Temporary Book C mirror while its dedicated pair has not yet been
      // provisioned. The engine stores both arrays in one namespaced JSONB row;
      // dedicated tables automatically win as soon as they become reachable.
      if (book === 'c') {
        const stateUrl = `${SUPA_URL}/rest/v1/apex_analyses?id=eq.__apex_book_c_paper_runtime__&select=feature_vector&limit=1`;
        const stateResponse = await fetch(stateUrl, { method: 'GET', headers: supaHeaders() });
        if (stateResponse.ok) {
          const stateRows = await stateResponse.json();
          const state = stateRows[0] && stateRows[0].feature_vector;
          const fallback = state && Array.isArray(state[table]) ? state[table] : [];
          return new Response(JSON.stringify(fallback), { status: 200, headers: cors });
        }
      }
      const txt = await response.text();
      return new Response(JSON.stringify({ error: `Supabase query failed: ${txt}` }), { status: response.status, headers: cors });
    }

    const data = await response.json();
    return new Response(JSON.stringify(data), { status: 200, headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: cors });
  }
}
