// /api/memory — Supabase-backed AI analysis memory
// GET  /api/memory?sym=AAPL          — last 15 analyses for this symbol
// GET  /api/memory?all=true&limit=80 — all recent scans across every symbol
// POST /api/memory  { ...fields }    — save a new analysis
// PATCH /api/memory { id, outcome, outcome_price, outcome_date } — update outcome

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://dtiuwllodzqpbwohzrgj.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k';
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

  // ── GET: fetch analyses ─────────────────────────────────────────────────────
  //   ?sym=NVDA                  — last 50 analyses for one symbol
  //   ?all=true&limit=N          — recent scans across every symbol (cap 1000)
  //   ?id=<row id>               — one specific row (returned as a 1-element array)
  //   &lean=true                 — drop the big prose fields (~6× smaller rows; what
  //                                the calibration/meta-label/track-record loops use,
  //                                so 1000-row reads stay cheap at 200 scans/week)
  //   &open=true                 — only unresolved rows (outcome null/pending)
  if (req.method === 'GET') {
    const sym    = (url.searchParams.get('sym') || '').trim().toUpperCase();
    const id     = (url.searchParams.get('id') || '').trim();
    const all    = url.searchParams.get('all') === 'true';
    const lean   = url.searchParams.get('lean') === 'true';
    const open   = url.searchParams.get('open') === 'true';
    const resolved = url.searchParams.get('resolved') === 'true';
    const limit  = Math.min(1000, parseInt(url.searchParams.get('limit') || '80', 10));

    // Everything EXCEPT the large prose fields (summary + the 4 analysis texts etc.).
    const LEAN_COLS = 'id,symbol,asset_type,analysis_date,price,verdict,confidence,target_price,entry_zone,stop_loss,risk_reward,timeframe,outcome,outcome_price,outcome_date,created_at,setup_features,lesson,validations';
    const select  = lean ? `&select=${LEAN_COLS}` : '';
    // open=true → unresolved only; resolved=true → graded TP/SL rows only (the
    // learning loops use this so old resolved swing/position trades never fall
    // off a recent-rows window as scan volume grows).
    const openFlt = open ? '&or=(outcome.is.null,outcome.eq.pending)'
                  : resolved ? '&outcome=in.(tp_hit,sl_hit)' : '';

    try {
      const query = id
        ? `${TABLE}?id=eq.${encodeURIComponent(id)}&limit=1`
        : all
          ? `${TABLE}?order=created_at.desc&limit=${limit}${select}${openFlt}`
          : `${TABLE}?symbol=eq.${encodeURIComponent(sym)}&order=created_at.desc&limit=50${select}${openFlt}`;

      if (!id && !all && !sym) return new Response(JSON.stringify([]), { headers: cors });

      const res  = await fetch(query, { headers: supaHeaders() });
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
      id:              `${body.symbol.toUpperCase()}_${Date.now()}`,
      symbol:          body.symbol.toUpperCase(),
      asset_type:      body.asset_type       || null,
      analysis_date:   new Date().toISOString().slice(0, 10),
      price:           body.price            ?? null,
      verdict:         body.verdict          || null,
      confidence:      body.confidence       ?? null,
      target_price:    body.target_price     || null,
      entry_zone:      body.entry_zone       || null,
      stop_loss:       body.stop_loss        || null,
      risk_reward:     body.risk_reward      || null,
      summary:         (body.summary || '').slice(0, 500),
      // Richer fields for comparison / AI learning
      technical_analysis:   (body.technical_analysis   || '').slice(0, 800),
      fundamental_analysis: (body.fundamental_analysis || '').slice(0, 800),
      macro_environment:    (body.macro_environment    || '').slice(0, 800),
      risk_analysis:        (body.risk_analysis        || '').slice(0, 800),
      key_reasons:          body.key_reasons ? JSON.stringify(body.key_reasons).slice(0, 500) : null,
      short_term_outlook:   (body.short_term_outlook   || '').slice(0, 300),
      timeframe:            body.timeframe             || null,
      setup_features:       body.setup_features        || null,
      outcome:              'pending',
    };

    try {
      let res = await fetch(TABLE, {
        method:  'POST',
        headers: supaHeaders({ 'Prefer': 'return=minimal' }),
        body:    JSON.stringify(row),
      });
      // Graceful fallback: if the setup_features column hasn't been migrated yet,
      // Supabase 400s — strip it and retry so saving keeps working regardless.
      if (!res.ok && row.setup_features != null) {
        const { setup_features, ...rest } = row;
        res = await fetch(TABLE, {
          method:  'POST',
          headers: supaHeaders({ 'Prefer': 'return=minimal' }),
          body:    JSON.stringify(rest),
        });
      }
      return new Response(
        res.ok ? JSON.stringify({ ok: true, id: row.id }) : JSON.stringify({ error: `Supabase ${res.status}` }),
        { status: res.ok ? 200 : 500, headers: cors }
      );
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: cors });
    }
  }

  // ── PATCH: resolve an outcome, OR refresh an existing open setup in place ────
  if (req.method === 'PATCH') {
    let body;
    try { body = await req.json(); } catch {
      return new Response(JSON.stringify({ error: 'Invalid JSON' }), { status: 400, headers: cors });
    }
    if (!body?.id) return new Response(JSON.stringify({ error: 'id required' }), { status: 400, headers: cors });

    let patch;
    if (body.refresh) {
      // Same trade idea re-scanned → overwrite the row's read with the newer one
      // and keep it open (pending). The id (and created_at) stay, so it's the
      // same history card, now updated rather than a duplicate.
      patch = {
        analysis_date: new Date().toISOString().slice(0, 10),
        outcome:       'pending',
        outcome_price: null,
        outcome_date:  null,
        asset_type:    body.asset_type ?? null,
        price:         body.price ?? null,
        verdict:       body.verdict || null,
        confidence:    body.confidence ?? null,
        target_price:  body.target_price || null,
        entry_zone:    body.entry_zone || null,
        stop_loss:     body.stop_loss || null,
        risk_reward:   body.risk_reward || null,
        summary:              (body.summary || '').slice(0, 500),
        technical_analysis:   (body.technical_analysis   || '').slice(0, 800),
        fundamental_analysis: (body.fundamental_analysis || '').slice(0, 800),
        macro_environment:    (body.macro_environment    || '').slice(0, 800),
        risk_analysis:        (body.risk_analysis        || '').slice(0, 800),
        key_reasons:          body.key_reasons ? JSON.stringify(body.key_reasons).slice(0, 500) : null,
        short_term_outlook:   (body.short_term_outlook   || '').slice(0, 300),
        timeframe:            body.timeframe || null,
        ...(body.setup_features ? { setup_features: body.setup_features } : {}),
      };
    } else if (body.lesson != null && body.outcome == null) {
      // Lesson-only patch: attach a post-mortem to an already-resolved row without
      // touching its outcome (so re-running History never re-grades a closed trade).
      patch = { lesson: String(body.lesson).slice(0, 600) };
    } else if (body.validations != null && body.outcome == null) {
      // Validations-only patch: store the trade's re-check history (validity loop).
      // Client does read-modify-write on the array, so we just overwrite it here.
      patch = { validations: Array.isArray(body.validations) ? body.validations.slice(0, 50) : [] };
    } else {
      patch = {
        outcome:       body.outcome       || 'expired',
        outcome_price: body.outcome_price ?? null,
        outcome_date:  body.outcome_date  || new Date().toISOString().slice(0, 10),
      };
      if (body.lesson != null) patch.lesson = String(body.lesson).slice(0, 600);
    }

    try {
      const url = `${TABLE}?id=eq.${encodeURIComponent(body.id)}`;
      let res = await fetch(url, {
        method:  'PATCH',
        headers: supaHeaders({ 'Prefer': 'return=minimal' }),
        body:    JSON.stringify(patch),
      });
      // Same graceful fallback as POST: retry without not-yet-migrated columns
      // (setup_features / lesson / validations) so a write never breaks if the
      // optional column is missing.
      if (!res.ok && (patch.setup_features != null || patch.lesson != null || patch.validations != null)) {
        const { setup_features, lesson, validations, ...rest } = patch;
        // If lesson was the ONLY field (lesson-only patch) there's nothing left to
        // write, so skip the retry rather than send an empty PATCH.
        if (Object.keys(rest).length) {
          res = await fetch(url, {
            method:  'PATCH',
            headers: supaHeaders({ 'Prefer': 'return=minimal' }),
            body:    JSON.stringify(rest),
          });
        }
      }
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
