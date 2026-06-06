// /api/backtest-runs — store + read the client-side strategy backtest results.
//
//   POST /api/backtest-runs   body: [ row, ... ]  (or { rows:[...] })  -> append
//   GET  /api/backtest-runs?instrument=&timeframe=&strategy=&run_id=&family=&limit=
//        GET ...&runs=true                 -> distinct recent run_ids (for the UI)
//
// Append-only: each row id embeds the run timestamp, so re-running adds new rows
// rather than overwriting. Mirrors api/memory.js (Supabase REST, anon key, RLS).
// Writes go to apex_strategy_backtests — a NEW table, separate from the Python
// engine's apex_backtests, which is left untouched.

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://dtiuwllodzqpbwohzrgj.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k';
const TABLE     = `${SUPA_URL}/rest/v1/apex_strategy_backtests`;

function supaHeaders(extra = {}) {
  return { apikey: SUPA_ANON, Authorization: `Bearer ${SUPA_ANON}`, 'Content-Type': 'application/json', ...extra };
}
function cors(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
  };
}

// Whitelist of columns we accept on insert (ignore anything else a client sends).
const COLS = new Set([
  'id', 'run_id', 'instrument', 'asset_class', 'timeframe', 'strategy', 'strategy_family',
  'regime_filtered', 'data_from', 'data_to', 'n_bars', 'n_trades', 'total_return', 'sharpe',
  'max_drawdown', 'win_rate', 'avg_win_pct', 'avg_loss_pct', 'avg_win_pips', 'avg_loss_pips',
  'expectancy', 'profit_factor', 'low_sample', 'shallow_sharpe', 'regime_breakdown',
  'signal_lift', 'threshold_sweep', 'params', 'app_version',
  // Walk-forward / out-of-sample (added 2026-06-06; stripped on retry if unmigrated)
  'is_return', 'is_sharpe', 'oos_return', 'oos_sharpe', 'oos_win_rate', 'oos_n_trades', 'oos_holds',
]);
// Walk-forward columns — dropped on the graceful retry if the table hasn't been migrated.
const WF_COLS = ['is_return', 'is_sharpe', 'oos_return', 'oos_sharpe', 'oos_win_rate', 'oos_n_trades', 'oos_holds'];
function clean(row) {
  const out = {};
  for (const k of Object.keys(row)) if (COLS.has(k)) out[k] = row[k];
  return out;
}

export default async function handler(req) {
  const url = new URL(req.url);
  const headers = cors(req.headers.get('origin'));
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers });

  // ── GET: query results ──────────────────────────────────────────────────────
  if (req.method === 'GET') {
    const p = url.searchParams;
    const limit = Math.min(2000, parseInt(p.get('limit') || '500', 10));
    try {
      if (p.get('runs') === 'true') {
        const q = `${TABLE}?select=run_id,inserted_at&order=inserted_at.desc&limit=2000`;
        const res = await fetch(q, { headers: supaHeaders() });
        const rows = res.ok ? await res.json() : [];
        const seen = new Map();
        for (const r of rows) if (!seen.has(r.run_id)) seen.set(r.run_id, r.inserted_at);
        return new Response(JSON.stringify([...seen].map(([run_id, inserted_at]) => ({ run_id, inserted_at }))), { headers });
      }
      const filt = [];
      for (const [k, col] of [['instrument', 'instrument'], ['timeframe', 'timeframe'], ['strategy', 'strategy'], ['run_id', 'run_id'], ['family', 'strategy_family']]) {
        const v = p.get(k); if (v) filt.push(`${col}=eq.${encodeURIComponent(v)}`);
      }
      const q = `${TABLE}?${filt.length ? filt.join('&') + '&' : ''}order=inserted_at.desc&limit=${limit}`;
      const res = await fetch(q, { headers: supaHeaders() });
      const rows = res.ok ? await res.json() : [];
      return new Response(JSON.stringify(Array.isArray(rows) ? rows : []), { headers });
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message, rows: [] }), { status: 200, headers });
    }
  }

  // ── POST: append a batch of result rows ─────────────────────────────────────
  if (req.method === 'POST') {
    let body;
    try { body = await req.json(); } catch { return new Response(JSON.stringify({ error: 'Invalid JSON' }), { status: 400, headers }); }
    const rows = (Array.isArray(body) ? body : body && body.rows) || [];
    if (!rows.length) return new Response(JSON.stringify({ error: 'no rows' }), { status: 400, headers });
    const cleaned = rows.filter(r => r && r.id && r.instrument && r.strategy).map(clean);
    if (!cleaned.length) return new Response(JSON.stringify({ error: 'no valid rows' }), { status: 400, headers });
    try {
      const post = (payload) => fetch(TABLE, {
        method: 'POST',
        // merge-duplicates makes a retried job idempotent without overwriting other runs
        headers: supaHeaders({ Prefer: 'resolution=merge-duplicates,return=minimal' }),
        body: JSON.stringify(payload),
      });
      let res = await post(cleaned);
      // Graceful fallback: if the walk-forward columns aren't migrated yet, Supabase
      // 400s on the unknown column — strip them and retry so saves never break.
      if (!res.ok && cleaned.some(r => WF_COLS.some(k => k in r))) {
        const stripped = cleaned.map(r => { const o = { ...r }; for (const k of WF_COLS) delete o[k]; return o; });
        res = await post(stripped);
      }
      if (res.ok) return new Response(JSON.stringify({ ok: true, n: cleaned.length }), { status: 200, headers });
      const detail = await res.text();
      return new Response(JSON.stringify({ error: `Supabase ${res.status}`, detail }), { status: 500, headers });
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), { status: 500, headers });
    }
  }

  // ── DELETE: remove an entire run by run_id (run-management) ──────────────────
  if (req.method === 'DELETE') {
    const runId = url.searchParams.get('run_id');
    if (!runId) return new Response(JSON.stringify({ error: 'run_id required' }), { status: 400, headers });
    try {
      const res = await fetch(`${TABLE}?run_id=eq.${encodeURIComponent(runId)}`, {
        method: 'DELETE',
        headers: supaHeaders({ Prefer: 'return=minimal' }),
      });
      if (res.ok) return new Response(JSON.stringify({ ok: true }), { status: 200, headers });
      const detail = await res.text();
      return new Response(JSON.stringify({ error: `Supabase ${res.status}`, detail }), { status: res.status, headers });
    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), { status: 500, headers });
    }
  }

  return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers });
}
