// /api/progress — Engine self-improvement record, READ-ONLY aggregation.
// GET /api/progress — one JSON payload with four independently-fault-tolerant sections:
//   experiments — trial ledger (n experiments, count by kind, recent entries), fetched
//                 server-side from the repo (engine/data_store/validation/trial_ledger.json
//                 is git-tracked but not deployed to Vercel).
//   gates       — newest validation/*gate*.json results parsed into feed entries with
//                 verdicts (pass / reject / measurement) + headline metrics.
//   paper       — forward paper test (apex_paper_daily): equity, day X/60, mini curve.
//   proposals   — auto-research queue (apex_research_proposals; tolerated if absent).
// A failing section degrades to { error: "…" } — the response never hard-500s.

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://cuvchjhaojhmxfgczndy.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';

const GH_REPO = 'zainm01800/apexfx';
const GH_RAW  = `https://raw.githubusercontent.com/${GH_REPO}/main/engine/data_store`;
const GH_DIR  = `https://api.github.com/repos/${GH_REPO}/contents/engine/data_store/validation?ref=main`;

const GATE_FILES  = 10;   // newest gate JSONs to parse
const RECENT_N    = 25;   // trial-ledger entries returned as "recent"
const PAPER_START = '2026-07-17';
const PAPER_TARGET_DAYS = 60;
const PAPER_SEED_EQUITY = 100000;

function supaHeaders() {
  return {
    'apikey': SUPA_ANON,
    'Authorization': `Bearer ${SUPA_ANON}`,
    'Content-Type': 'application/json',
  };
}

function ghHeaders() {
  return {
    'User-Agent': 'apexfx-progress',
    'Accept': 'application/vnd.github+json',
  };
}

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
    'Cache-Control': 's-maxage=300, stale-while-revalidate=600',
  };
}

const num = (x) => (typeof x === 'number' && isFinite(x) ? x : null);
const dateFromName = (name) => {
  const m = String(name).match(/(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : null;
};

// ── Experiments (trial ledger) ───────────────────────────────────────────────
// The ledger is a dedup registry: { "<json of {factory, instrument, params,
// timeframe}>": null }. No timestamps — object insertion order is the only
// recency proxy, so "recent" = the last-recorded entries.
async function loadExperiments() {
  const res = await fetch(`${GH_RAW}/validation/trial_ledger.json`, { headers: ghHeaders() });
  if (!res.ok) throw new Error(`trial_ledger fetch failed: HTTP ${res.status}`);
  const ledger = await res.json();
  const keys = Object.keys(ledger || {});
  const byKind = {};
  const parsed = [];
  for (const k of keys) {
    let e = null;
    try { e = JSON.parse(k); } catch { e = null; }
    const kind = (e && e.factory) || 'unknown';
    byKind[kind] = (byKind[kind] || 0) + 1;
    parsed.push({
      kind,
      instrument: (e && e.instrument) || null,
      timeframe: (e && e.timeframe) || null,
      params: (e && e.params) || null,
      date: null, // ledger carries no timestamps — see note above
    });
  }
  return {
    total: keys.length,
    byKind,
    recent: parsed.slice(-RECENT_N).reverse(),
  };
}

// ── Gates ────────────────────────────────────────────────────────────────────
// Gate JSONs come in a few shapes; the common thread is a per-book verdict block:
//   { passed: bool, dsr: {dsr, observed_sharpe_ann}, pbo: {pbo}, cpcv: {…}, reasons: [] }
// found under `verdicts` (preferred), `books[*].gate`, `grid_results[*].gate`, or
// `run1.grid_results[*].gate`. Meta-label gates use `adopt_eligible` instead of
// `passed`. Cost/robustness measurements carry `kind: "cost_model_measurement"`
// and are labelled MEASUREMENT rather than pass/reject.
function gateEntriesFromFile(name, d) {
  if (!d || typeof d !== 'object') return [];
  const kind = typeof d.kind === 'string' ? d.kind : null;
  const isMeasurement = /measurement/i.test(kind || '') || /measurement/i.test(name);
  const date = String(d.generated_at || d.timestamp || '').slice(0, 10) || dateFromName(name);
  const nTrials = num(d.n_trials_used) ?? num(d.n_trials_before);

  const blocks = [];
  if (d.verdicts && typeof d.verdicts === 'object') {
    for (const [book, v] of Object.entries(d.verdicts)) blocks.push([book, v]);
  } else {
    for (const c of [d.books, d.grid_results, d.run1 && d.run1.grid_results]) {
      if (c && typeof c === 'object') {
        for (const [book, v] of Object.entries(c)) {
          if (v && typeof v === 'object' && v.gate) blocks.push([book, v.gate]);
        }
      }
      if (blocks.length) break;
    }
  }

  const entries = [];
  for (const [book, v] of blocks) {
    if (!v || typeof v !== 'object') continue;
    let verdict = null;
    if (typeof v.passed === 'boolean') verdict = v.passed ? 'pass' : 'reject';
    else if (typeof v.adopt_eligible === 'boolean') verdict = v.adopt_eligible ? 'pass' : 'reject';
    if (isMeasurement && verdict) verdict = 'measurement';
    if (!verdict) continue; // not a gate-result block — skip

    const dsr = v.dsr && typeof v.dsr === 'object' ? num(v.dsr.dsr) : null;
    const sharpe = v.dsr && typeof v.dsr === 'object' ? num(v.dsr.observed_sharpe_ann) : null;
    const pbo = (v.pbo && typeof v.pbo === 'object' ? num(v.pbo.pbo) : null) ?? (d.pbo ? num(d.pbo.pbo) : null);
    const cpcvPaths = v.cpcv && typeof v.cpcv === 'object' ? num(v.cpcv.n_paths) : null;
    const cpcvFracPositive = v.cpcv && typeof v.cpcv === 'object' ? num(v.cpcv.frac_positive) : null;

    let takeaway = null;
    if (Array.isArray(v.reasons) && v.reasons.length) takeaway = v.reasons.join(' · ');
    else if (v.paired && num(v.paired.sharpe_delta) !== null) {
      const p = num(v.paired.p_value_one_sided);
      takeaway = `ΔSharpe ${v.paired.sharpe_delta.toFixed(3)} vs baseline${p !== null ? ` (p=${p.toFixed(3)})` : ''}`;
    }

    entries.push({
      id: `${name}::${book}`,
      file: name,
      date,
      kind: kind || name.replace(/_?\d{4}-\d{2}-\d{2}.*$/, '').replace(/\.json$/, ''),
      book,
      verdict,
      sharpe,
      dsr,
      pbo,
      cpcvPaths,
      cpcvFracPositive,
      nTrials,
      takeaway,
    });
  }

  // Measurement files with no gate blocks still earn one feed entry.
  if (!entries.length && isMeasurement) {
    const del = d.deltas_borrow_minus_baseline || d.deltas_challenger_minus_control || null;
    entries.push({
      id: name,
      file: name,
      date,
      kind: kind || 'measurement',
      book: null,
      verdict: 'measurement',
      sharpe: null,
      dsr: null,
      pbo: d.pbo ? num(d.pbo.pbo) : null,
      cpcvPaths: null,
      cpcvFracPositive: null,
      nTrials,
      takeaway: del && num(del.sharpe) !== null
        ? `ΔSharpe ${del.sharpe >= 0 ? '+' : ''}${del.sharpe.toFixed(4)} vs baseline — cost/robustness measurement, no deployment verdict`
        : 'Cost/robustness measurement — no deployment verdict',
    });
  }
  return entries;
}

async function loadGates() {
  const res = await fetch(GH_DIR, { headers: ghHeaders() });
  if (!res.ok) throw new Error(`GitHub contents listing failed: HTTP ${res.status}`);
  const listing = await res.json();
  // "_gate_" / "_gate.json" / "measurement" — NOT "gated" (regime_gated_momentum__*
  // files are per-instrument screens, not gate results). Dated files first (date desc);
  // undated ones trail by name.
  const isGateFile = (n) => n.endsWith('.json') && /(_gate[_.]|measurement)/i.test(n);
  const names = (Array.isArray(listing) ? listing : [])
    .filter((f) => f && f.type === 'file' && isGateFile(f.name))
    .map((f) => f.name)
    .sort((a, b) => {
      const da = dateFromName(a), db = dateFromName(b);
      if (da && db) return db.localeCompare(da);
      if (da) return -1;
      if (db) return 1;
      return b.localeCompare(a);
    })
    .slice(0, GATE_FILES);

  const nested = await Promise.all(names.map(async (n) => {
    try {
      const rr = await fetch(`${GH_RAW}/validation/${encodeURIComponent(n)}`, { headers: ghHeaders() });
      if (!rr.ok) return [];
      return gateEntriesFromFile(n, await rr.json());
    } catch {
      return []; // skip files that don't parse as gate results
    }
  }));

  const all = nested.flat().sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
  // Re-run files (e.g. *_run2, *measurement vs *measurement_certified) repeat the same
  // book — keep the first (newest) entry per kind+book.
  const seen = new Set();
  const entries = all.filter((e) => {
    const key = `${e.kind}::${e.book || e.file}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return {
    filesScanned: names.length,
    passed: entries.filter((e) => e.verdict === 'pass').length,
    rejected: entries.filter((e) => e.verdict === 'reject').length,
    measurements: entries.filter((e) => e.verdict === 'measurement').length,
    entries,
  };
}

// ── Paper test ───────────────────────────────────────────────────────────────
async function loadPaper() {
  const res = await fetch(`${SUPA_URL}/rest/v1/apex_paper_daily?order=date.asc&limit=120`, { headers: supaHeaders() });
  if (!res.ok) throw new Error(`Supabase apex_paper_daily failed: HTTP ${res.status}`);
  const rows = await res.json();
  if (!Array.isArray(rows) || !rows.length) {
    return {
      book: 'book_d_multiasset_252', startDate: PAPER_START, targetDays: PAPER_TARGET_DAYS,
      day: 0, seedEquity: PAPER_SEED_EQUITY, equity: null, dayPnl: null, cumPnl: null,
      drawdown: null, nOpen: null, halted: false, sharpeToDate: null, lastDate: null, series: [],
    };
  }
  const latest = rows[rows.length - 1];
  const extra = latest.state_extra && typeof latest.state_extra === 'object' ? latest.state_extra : {};
  const metrics = latest.metrics && typeof latest.metrics === 'object' ? latest.metrics : {};
  const series = rows.slice(-30)
    .map((r) => ({ t: r.date, y: Number(r.equity) }))
    .filter((p) => p.t && isFinite(p.y));
  return {
    book: extra.book || 'book_d_multiasset_252',
    startDate: PAPER_START,
    targetDays: PAPER_TARGET_DAYS,
    day: Math.max(0, rows.length - 1), // processed days after the seed snapshot
    seedEquity: PAPER_SEED_EQUITY,
    equity: num(latest.equity),
    dayPnl: num(latest.day_pnl),
    cumPnl: num(latest.cum_pnl),
    drawdown: num(latest.drawdown_from_peak),
    nOpen: num(latest.n_open),
    halted: Boolean(extra.halted),
    sharpeToDate: num(metrics.sharpe),
    lastDate: latest.date || null,
    series,
  };
}

// ── Auto-research queue (Part 2 slot) ────────────────────────────────────────
async function loadProposals() {
  try {
    const res = await fetch(`${SUPA_URL}/rest/v1/apex_research_proposals?order=created_at.desc&limit=20`, { headers: supaHeaders() });
    if (!res.ok) return { items: [], note: 'queue table not initialised yet' }; // tolerate absence
    const items = await res.json();
    return { items: Array.isArray(items) ? items : [] };
  } catch {
    return { items: [], note: 'queue unavailable' };
  }
}

export default async function handler(req) {
  const origin = req.headers.get('origin');
  const cors = corsHeaders(origin);

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (req.method !== 'GET') return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers: cors });

  const [experiments, gates, paper, proposals] = await Promise.all([
    loadExperiments().then((d) => ({ data: d }), (e) => ({ error: String(e && e.message || e) })),
    loadGates().then((d) => ({ data: d }), (e) => ({ error: String(e && e.message || e) })),
    loadPaper().then((d) => ({ data: d }), (e) => ({ error: String(e && e.message || e) })),
    loadProposals().then((d) => ({ data: d }), (e) => ({ error: String(e && e.message || e) })),
  ]);

  const body = {
    generated_at: new Date().toISOString(),
    certified: {
      // Hardcoded from the binding prereg (engine/data_store/pre_registration_paper_trend_2026-07-17.md).
      book: 'book_d_multiasset_252',
      label: 'Book D — multi-asset trend',
      maxRiskPerTrade: 0.01,   // amended 0.02 → 0.01 on 2026-07-19 (prop-rules Monte Carlo)
      maxPortfolioRisk: 0.065, // config v5, binding
      haltDrawdown: 0.15,
      universe: 42,
    },
    experiments: experiments.data || null,
    experimentsError: experiments.error || null,
    gates: gates.data || null,
    gatesError: gates.error || null,
    paper: paper.data || null,
    paperError: paper.error || null,
    proposals: (proposals.data && proposals.data.items) || [],
    proposalsNote: (proposals.data && proposals.data.note) || proposals.error || null,
  };

  return new Response(JSON.stringify(body), { status: 200, headers: cors });
}
