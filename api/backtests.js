// /api/backtests — read the Supabase backtest knowledge base for a symbol.
// GET /api/backtests?sym=EUR/USD  -> { rows:[...], summary:{...} }
// The Deep Analyse uses the summary to inform the committee's verdict.

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://cuvchjhaojhmxfgczndy.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';
const TABLE     = `${SUPA_URL}/rest/v1/apex_backtests`;

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Content-Type': 'application/json',
  'Cache-Control': 'public, s-maxage=300, stale-while-revalidate=600',
};

export default async function handler(req) {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });
  const sym = (new URL(req.url).searchParams.get('sym') || '').trim();
  if (!sym) return new Response(JSON.stringify({ error: 'sym required' }), { status: 400, headers: CORS });

  try {
    const q = `${TABLE}?instrument=eq.${encodeURIComponent(sym)}&order=inserted_at.desc&limit=50`;
    const res = await fetch(q, {
      headers: { apikey: SUPA_ANON, Authorization: `Bearer ${SUPA_ANON}` },
      signal: AbortSignal.timeout(8000),
    });
    const rows = res.ok ? await res.json() : [];
    const list = Array.isArray(rows) ? rows : [];

    let summary = null;
    if (list.length) {
      const passed = list.filter(r => r.passed).length;
      const dsrs = list.map(r => r.dsr).filter(v => v != null);
      const best = list.reduce((a, b) => ((b.dsr ?? -1) > (a.dsr ?? -1) ? b : a), list[0]);
      summary = {
        n: list.length,
        n_passed: passed,
        pass_rate: +(passed / list.length).toFixed(2),
        best_dsr: dsrs.length ? Math.max(...dsrs) : null,
        best_config: best ? best.config_label : null,
        best_passed: best ? !!best.passed : false,
        last_updated: list[0]?.inserted_at || null,
        edge: passed > 0 ? 'some configs passed validation' : 'no config has passed validation',
      };
    }
    return new Response(JSON.stringify({ instrument: sym, summary, rows: list }), { status: 200, headers: CORS });
  } catch (e) {
    return new Response(JSON.stringify({ instrument: sym, summary: null, rows: [], error: e.message }), { status: 200, headers: CORS });
  }
}
