// /api/memory — Supabase-backed AI analysis memory
// GET  /api/memory?sym=AAPL        — last 15 analyses + outcomes for this symbol
// POST /api/memory  { ...fields }  — save a new analysis
// PATCH /api/memory { id, outcome, outcome_price, outcome_date } — update outcome

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://ksxznauzvlsgfghvpeew.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtzeHpuYXV6dmxzZ2ZnaHZwZWV3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM0ODg4MjIsImV4cCI6MjA4OTA2NDgyMn0.B5a2zl8Vr_Q51fB9_Pv1Q8SXnh41xELgJkrRu0BEkEk';
const TABLE     = `${SUPA_URL}/rest/v1/apex_research_memory`;

function supaHeaders(extra = {}) {
  return {
    'apikey': SUPA_ANON,
    'Authorization': `Bearer ${SUPA_ANON}`,
    'Content-Type': 'application/json',
    ...extra,
  };
}

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, POST, PATCH, OPTIONS',
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

  // ── GET: fetch prior analyses for a symbol ──────────────────────────────────
  if (req.method === 'GET') {
    const sym = (url.searchParams.get('sym') || '').trim().toUpperCase();
    if (!sym) return new Response(JSON.stringify([]), { headers: cors });

    try {
      const res = await fetch(
        `${TABLE}?symbol=eq.${encodeURIComponent(sym)}&order=created_at.desc&limit=15`,
        { headers: supaHeaders() }
      );
      const data = res.ok ? await res.json() : [];
      return new Response(JSON.stringify(Array.isArray(data) ? data : []), { headers: cors });
    } catch {
      return new Response(JSON.stringify([]), { headers: cors });
    }
  }

  // ── POST: save a new analysis ───────────────────────────────────────────────
  if (req.method === 'POST') {
    let body;
    try { body = await req.json(); } catch {
      return new Response(JSON.stringify({ error: 'Invalid JSON' }), { status: 400, headers: cors });
    }
    if (!body?.symbol) return new Response(JSON.stringify({ error: 'symbol required' }), { status: 400, headers: cors });

    const row = {
      id:            `${body.symbol.toUpperCase()}_${Date.now()}`,
      symbol:        body.symbol.toUpperCase(),
      asset_type:    body.asset_type    || null,
      analysis_date: new Date().toISOString().slice(0, 10),
      price:         body.price         ?? null,
      verdict:       body.verdict       || null,
      confidence:    body.confidence    ?? null,
      target_price:  body.target_price  || null,
      entry_zone:    body.entry_zone    || null,
      stop_loss:     body.stop_loss     || null,
      risk_reward:   body.risk_reward   || null,
      summary:       (body.summary || '').slice(0, 500),
      outcome:       'pending',
    };

    try {
      const res = await fetch(TABLE, {
        method:  'POST',
        headers: supaHeaders({ 'Prefer': 'return=minimal' }),
        body:    JSON.stringify(row),
      });
      return new Response(
        res.ok ? JSON.stringify({ ok: true, id: row.id }) : JSON.stringify({ error: `Supabase ${res.status}` }),
        { status: res.ok ? 200 : 500, headers: cors }
      );
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: cors });
    }
  }

  // ── PATCH: update outcome for a resolved analysis ───────────────────────────
  if (req.method === 'PATCH') {
    let body;
    try { body = await req.json(); } catch {
      return new Response(JSON.stringify({ error: 'Invalid JSON' }), { status: 400, headers: cors });
    }
    if (!body?.id) return new Response(JSON.stringify({ error: 'id required' }), { status: 400, headers: cors });

    const patch = {
      outcome:       body.outcome       || 'expired',
      outcome_price: body.outcome_price ?? null,
      outcome_date:  body.outcome_date  || new Date().toISOString().slice(0, 10),
    };

    try {
      const res = await fetch(
        `${TABLE}?id=eq.${encodeURIComponent(body.id)}`,
        {
          method:  'PATCH',
          headers: supaHeaders({ 'Prefer': 'return=minimal' }),
          body:    JSON.stringify(patch),
        }
      );
      return new Response(
        res.ok ? JSON.stringify({ ok: true }) : JSON.stringify({ error: 'update failed' }),
        { status: res.ok ? 200 : 500, headers: cors }
      );
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: cors });
    }
  }

  return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers: cors });
}
