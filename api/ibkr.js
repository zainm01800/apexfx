// /api/ibkr — Read-only IBKR paper terminal data (synced to Supabase by the engine)
// GET /api/ibkr?view=account                          — singleton account snapshot
// GET /api/ibkr?view=positions[&class=forex|stocks|crypto] — open positions
// GET /api/ibkr?view=trades[&class=forex|stocks|crypto][&limit=100]   — fill records
//
// Asset class is derived SERVER-SIDE from the instrument so the frontend stays
// dumb: a "BASE/QUOTE" pair with both legs in G10 is forex, a pair whose base
// is a known crypto is crypto, everything else is stocks.

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://dtiuwllodzqpbwohzrgj.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k';

const G10 = new Set(['EUR', 'GBP', 'USD', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD']);
const CRYPTO = new Set(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'AVAX', 'DOGE', 'MATIC', 'LINK', 'ARB', 'SUI']);
const CLASSES = new Set(['forex', 'stocks', 'crypto']);

function deriveClass(instrument) {
  const inst = String(instrument || '').toUpperCase();
  if (inst.includes('/')) {
    const [base, quote] = inst.split('/', 2);
    if (G10.has(base) && G10.has(quote)) return 'forex';
    if (CRYPTO.has(base)) return 'crypto';
  }
  return 'stocks';
}

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

async function supaGet(table, query, cors) {
  const response = await fetch(`${SUPA_URL}/rest/v1/${table}?${query}`, {
    method: 'GET',
    headers: supaHeaders(),
  });
  if (!response.ok) {
    const txt = await response.text();
    return { error: `Supabase query failed: ${txt}`, status: response.status };
  }
  return { data: await response.json() };
}

export default async function handler(req) {
  const url    = new URL(req.url);
  const origin = req.headers.get('origin');
  const cors   = corsHeaders(origin);

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (req.method !== 'GET') return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers: cors });

  try {
    const view  = url.searchParams.get('view') || 'account';
    const cls   = url.searchParams.get('class');
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '100', 10) || 100, 500);

    if (view === 'account') {
      const r = await supaGet('apex_ibkr_account', 'id=eq.1', cors);
      if (r.error) return new Response(JSON.stringify({ error: r.error }), { status: r.status, headers: cors });
      const record = (Array.isArray(r.data) && r.data.length > 0) ? r.data[0] : {};
      return new Response(JSON.stringify(record), { status: 200, headers: cors });
    }

    if (view === 'positions') {
      const r = await supaGet('apex_ibkr_positions', 'order=instrument.asc&limit=500', cors);
      if (r.error) return new Response(JSON.stringify({ error: r.error }), { status: r.status, headers: cors });
      let rows = (Array.isArray(r.data) ? r.data : []).map(p => ({ ...p, asset_class: deriveClass(p.instrument) }));
      if (cls && CLASSES.has(cls)) rows = rows.filter(p => p.asset_class === cls);
      return new Response(JSON.stringify(rows), { status: 200, headers: cors });
    }

    if (view === 'trades') {
      const r = await supaGet('apex_ibkr_trades', `order=exec_time.desc&limit=${limit}`, cors);
      if (r.error) return new Response(JSON.stringify({ error: r.error }), { status: r.status, headers: cors });
      let rows = (Array.isArray(r.data) ? r.data : []).map(t => ({ ...t, asset_class: deriveClass(t.instrument) }));
      if (cls && CLASSES.has(cls)) rows = rows.filter(t => t.asset_class === cls);
      return new Response(JSON.stringify(rows), { status: 200, headers: cors });
    }

    return new Response(JSON.stringify({ error: 'Bad view — expected account|positions|trades' }), { status: 400, headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: cors });
  }
}
