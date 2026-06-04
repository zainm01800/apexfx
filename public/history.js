// ── APEX History Page ────────────────────────────────────────────────────────
// Fetches all saved scans from /api/memory and presents them GROUPED BY SYMBOL:
// one card per instrument showing the CURRENT call + an expandable thesis-evolution
// trail of prior scans, the symbol's realised win/loss record, and an anti-anchoring
// flag when the same direction has been re-scanned repeatedly without resolving.
// Also resolves pending outcomes against fresh candle data.

const API_MEMORY = '/api/memory';
const API_CANDLES = '/api/candles';   // same endpoint dashboard uses

// ── State ────────────────────────────────────────────────────────────────────
let _allRows      = [];   // all rows from Supabase (flat)
let _filterOutcome = 'all';
let _filterType    = 'all';
let _filterSym     = '';

// ── Fetch helpers ─────────────────────────────────────────────────────────────

async function fetchAllScans() {
  const res = await fetch(`${API_MEMORY}?all=true&limit=200`);
  if (!res.ok) throw new Error('Failed to load scan history');
  return res.json();
}

// Resolve pending outcomes against fresh candle data (same logic as dashboard.js).
// Fetches a BOUNDED daily window per symbol (from the oldest open scan → now) using
// the params /api/candles actually understands (tf/from/to — it ignores resolution/bars).
async function resolveIfPending(rows) {
  const pending = rows.filter(r => r.outcome === 'pending' && r.target_price && r.stop_loss && r.price);
  if (!pending.length) return;

  // Group by symbol so we only fetch candles once per symbol
  const bySymbol = {};
  for (const r of pending) {
    if (!bySymbol[r.symbol]) bySymbol[r.symbol] = [];
    bySymbol[r.symbol].push(r);
  }

  await Promise.allSettled(
    Object.entries(bySymbol).map(async ([sym, symRows]) => {
      try {
        const type = symRows[0].asset_type || 'Stock';
        // Bounded window: from 5 days before the oldest open scan to now.
        const oldest = Math.min(...symRows.map(r => new Date(r.analysis_date).getTime() / 1000));
        const from   = Math.floor(oldest - 5 * 86400);
        const to     = Math.floor(Date.now() / 1000);
        const candleRes = await fetch(
          `${API_CANDLES}?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`
        );
        if (!candleRes.ok) return;
        const candles = await candleRes.json();
        if (!Array.isArray(candles) || candles.length < 2) return;

        for (const row of symRows) {
          const entryDate = new Date(row.analysis_date).getTime() / 1000;
          const tp = parseFloat(row.target_price);
          const sl = parseFloat(row.stop_loss);
          if (!tp || !sl) continue;

          const afterEntry = candles.filter(c => c.time > entryDate);
          let resolved = null;

          for (const bar of afterEntry) {
            const isBull = row.verdict?.toLowerCase().includes('buy');
            const isBear = row.verdict?.toLowerCase().includes('sell');
            if (isBull) {
              if (bar.high >= tp)  { resolved = 'tp_hit';  break; }
              if (bar.low  <= sl)  { resolved = 'sl_hit';  break; }
            } else if (isBear) {
              if (bar.low  <= tp)  { resolved = 'tp_hit';  break; }
              if (bar.high >= sl)  { resolved = 'sl_hit';  break; }
            }
          }

          // If no resolution but analysis is >30 days old → expired
          const ageMs = Date.now() - new Date(row.analysis_date).getTime();
          if (!resolved && ageMs > 30 * 86400 * 1000) resolved = 'expired';

          if (resolved) {
            row.outcome = resolved;
            // Patch Supabase (fire-and-forget)
            fetch(API_MEMORY, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id: row.id, outcome: resolved, outcome_date: new Date().toISOString().slice(0, 10) }),
            }).catch(() => {});
          }
        }
      } catch {}
    })
  );
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function verdictClass(v) {
  if (!v) return '';
  const lv = v.toLowerCase().replace(/_/g, '-');
  if (lv.includes('strong-buy') || lv.includes('strong buy')) return 'strong-buy';
  if (lv.includes('buy'))  return 'buy';
  if (lv.includes('sell')) return lv.includes('strong') ? 'strong-sell' : 'sell';
  return 'hold';
}

// Bucket a verdict into a trade direction (for anchoring detection).
function verdictDir(v) {
  const u = (v || '').toUpperCase();
  if (/BUY/.test(u)) return 'long';
  if (/SELL|SHORT/.test(u)) return 'short';
  return 'neutral';
}

function outcomeLabel(o) {
  switch (o) {
    case 'tp_hit':  return '✅ TP Hit';
    case 'sl_hit':  return '❌ SL Hit';
    case 'expired': return '⏱ Expired';
    default:        return '⏳ Pending';
  }
}

function fmtPrice(p) {
  if (p == null || p === '') return '—';
  const n = parseFloat(p);
  if (isNaN(n)) return p;
  return n < 10 ? n.toFixed(5) : n < 1000 ? n.toFixed(2) : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// Best-effort timestamp for a row: created_at → epoch baked into the id → analysis_date.
function rowTs(row) {
  if (row.created_at) { const t = Date.parse(row.created_at); if (!isNaN(t)) return t; }
  const m = String(row.id || '').match(/_(\d{10,})$/);
  if (m) return parseInt(m[1], 10);
  if (row.analysis_date) { const t = Date.parse(row.analysis_date); if (!isNaN(t)) return t; }
  return 0;
}

function rescanUrl(row) {
  return `dashboard.html?sym=${encodeURIComponent(row.symbol)}&compare=${encodeURIComponent(row.id)}`;
}

function chartUrl(row) {
  return `index.html?sym=${encodeURIComponent(row.symbol)}`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Grouping ───────────────────────────────────────────────────────────────────
// Collapse the flat scan list into one entry per symbol: the latest scan is the
// "current" call; the rest form the evolution trail. Also computes the realised
// win/loss record and an anti-anchoring flag for the symbol.
function buildGroups(rows) {
  const bySym = {};
  for (const r of rows) {
    (bySym[r.symbol] ||= []).push(r);
  }

  const groups = Object.entries(bySym).map(([symbol, scans]) => {
    scans.sort((a, b) => rowTs(b) - rowTs(a));   // newest first
    const current = scans[0];
    const trail   = scans.slice(1);

    const resolved = scans.filter(s => s.outcome === 'tp_hit' || s.outcome === 'sl_hit');
    const wins     = resolved.filter(s => s.outcome === 'tp_hit').length;
    const losses   = resolved.length - wins;
    const winRate  = resolved.length ? Math.round((wins / resolved.length) * 100) : null;

    // Anti-anchoring: ≥3 open (pending) same-direction scans with non-falling
    // confidence and nothing resolved — i.e. the same idea re-asserted with growing
    // conviction but no evidence it's working yet.
    const openSame = scans.filter(s => s.outcome === 'pending' && verdictDir(s.verdict) === verdictDir(current.verdict));
    let anchorFlag = null;
    if (verdictDir(current.verdict) !== 'neutral' && openSame.length >= 3 && !resolved.length) {
      anchorFlag = `Re-scanned ${openSame.length}× ${verdictDir(current.verdict).toUpperCase()}, none resolved yet`;
    }

    return { symbol, current, trail, scans, resolved: resolved.length, wins, losses, winRate, anchorFlag, ts: rowTs(current) };
  });

  groups.sort((a, b) => b.ts - a.ts);   // most recently active symbol first
  return groups;
}

// ── Rendering ───────────────────────────────────────────────────────────────────

function renderTrailRow(scan, prevOlder) {
  const vc = verdictClass(scan.verdict);
  const vDisplay = (scan.verdict || '—').replace(/_/g, ' ').toUpperCase();
  // confidence delta vs the next-older scan (chronologically before this one)
  let delta = '';
  if (prevOlder && scan.confidence != null && prevOlder.confidence != null) {
    const d = scan.confidence - prevOlder.confidence;
    if (d > 0)      delta = `<span class="tr-delta up">▲${d}</span>`;
    else if (d < 0) delta = `<span class="tr-delta dn">▼${Math.abs(d)}</span>`;
    else            delta = `<span class="tr-delta flat">·</span>`;
  }
  return `
    <div class="trail-row">
      <span class="tr-date">${escHtml(scan.analysis_date || '')}</span>
      <span class="tr-verdict ${vc}">${escHtml(vDisplay)}</span>
      <span class="tr-conf">${scan.confidence != null ? scan.confidence + '%' : '—'}${delta}</span>
      <span class="tr-px">@ ${fmtPrice(scan.price)}</span>
      <span class="tr-tgt">T ${scan.target_price ? fmtPrice(scan.target_price) : '—'} / S ${scan.stop_loss ? fmtPrice(scan.stop_loss) : '—'}</span>
      <span class="tr-outcome ${scan.outcome || 'pending'}">${outcomeLabel(scan.outcome)}</span>
    </div>`;
}

function renderCard(g) {
  const row = g.current;
  const vc  = verdictClass(row.verdict);
  const vDisplay = (row.verdict || '—').replace(/_/g, ' ').toUpperCase();

  // Realised-record badge for this symbol
  let recordBadge = '';
  if (g.resolved > 0) {
    const cls = g.winRate >= 50 ? 'rec-good' : 'rec-bad';
    recordBadge = `<span class="sc-record ${cls}" title="Realised outcomes of resolved calls on ${escHtml(g.symbol)}">📊 ${g.wins}W / ${g.losses}L · ${g.winRate}%</span>`;
  }

  const anchor = g.anchorFlag
    ? `<div class="sc-anchor" title="Repeated same-direction calls with no resolved outcome — beware anchoring">⚠ ${escHtml(g.anchorFlag)}</div>`
    : '';

  // Evolution trail (older scans), newest of the older-set first; delta vs the one before it
  let trail = '';
  if (g.trail.length) {
    const rowsHtml = g.trail.map((s, i) => renderTrailRow(s, g.trail[i + 1])).join('');
    trail = `
      <details class="sc-trail">
        <summary>📜 ${g.trail.length} earlier ${g.trail.length === 1 ? 'scan' : 'scans'} — thesis evolution</summary>
        <div class="trail-list">${rowsHtml}</div>
      </details>`;
  }

  const scanCount = g.scans.length;

  return `
    <div class="scan-card ${vc}">
      <div class="sc-head">
        <div>
          <div class="sc-sym">${escHtml(g.symbol)}</div>
          <div class="sc-date">${scanCount} ${scanCount === 1 ? 'scan' : 'scans'} · latest ${escHtml(row.analysis_date || '')}</div>
        </div>
        <span class="sc-type">${escHtml(row.asset_type || 'Stock')}</span>
      </div>

      <div class="sc-verdict-row">
        <span class="sc-verdict ${vc}">${vDisplay}</span>
        <span class="sc-conf">${row.confidence != null ? row.confidence + '%' : '—'} confidence</span>
      </div>

      ${recordBadge ? `<div class="sc-record-row">${recordBadge}</div>` : ''}
      ${anchor}

      <div class="sc-price-row">
        <span class="sc-price">@ $${fmtPrice(row.price)}</span>
        <span class="sc-outcome ${row.outcome || 'pending'}">${outcomeLabel(row.outcome)}</span>
      </div>

      ${row.summary ? `<p class="sc-summary">${escHtml(row.summary)}</p>` : ''}

      <div class="sc-targets">
        ${row.entry_zone  ? `<div class="sc-target-item"><span class="sc-tl">Entry</span><span class="sc-tv entry">${escHtml(row.entry_zone)}</span></div>`  : ''}
        ${row.target_price? `<div class="sc-target-item"><span class="sc-tl">Target</span><span class="sc-tv target">$${fmtPrice(row.target_price)}</span></div>` : ''}
        ${row.stop_loss   ? `<div class="sc-target-item"><span class="sc-tl">Stop</span><span class="sc-tv stop">$${fmtPrice(row.stop_loss)}</span></div>`    : ''}
        ${row.risk_reward ? `<div class="sc-target-item"><span class="sc-tl">R:R</span><span class="sc-tv">${escHtml(row.risk_reward)}</span></div>`          : ''}
      </div>

      ${trail}

      <div class="sc-actions">
        <a class="sc-btn sc-btn-rescan" href="${rescanUrl(row)}" title="Re-run the analysis and compare to this scan">
          🔄 Rescan &amp; Compare
        </a>
        <a class="sc-btn sc-btn-chart" href="${chartUrl(row)}" title="Open chart for this symbol">
          📈 Chart
        </a>
      </div>
    </div>
  `;
}

// ── Filter + render ───────────────────────────────────────────────────────────

// A symbol group passes when: its symbol matches the search, its current type matches
// the type filter, and (for outcome) ANY scan in the group has that outcome — so
// filtering "TP Hit" surfaces every instrument that has ever hit a target.
function applyFilters(groups) {
  return groups.filter(g => {
    if (_filterSym && !g.symbol.toUpperCase().includes(_filterSym.toUpperCase())) return false;
    if (_filterType !== 'all' && g.current.asset_type !== _filterType) return false;
    if (_filterOutcome !== 'all' && !g.scans.some(s => (s.outcome || 'pending') === _filterOutcome)) return false;
    return true;
  });
}

function renderGrid() {
  const grid = document.getElementById('scanGrid');
  const empty = document.getElementById('histEmpty');
  const groups = applyFilters(buildGroups(_allRows));

  if (!groups.length) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  grid.innerHTML = groups.map(renderCard).join('');
}

function updateSummary() {
  const total    = _allRows.length;
  const symbols  = new Set(_allRows.map(r => r.symbol)).size;
  const tp       = _allRows.filter(r => r.outcome === 'tp_hit').length;
  const sl       = _allRows.filter(r => r.outcome === 'sl_hit').length;
  const resolved = tp + sl;
  const accuracy = resolved > 0 ? Math.round(tp / resolved * 100) : null;

  setText('hsStat0', `${symbols}`,                              `Symbols · ${total} scans`);
  setText('hsStat1', tp,                                        'TP Hit',  'green');
  setText('hsStat2', sl,                                        'SL Hit',  'red');
  setText('hsStat3', accuracy != null ? accuracy + '%' : '—%',  'Accuracy', 'accent');
}

function setText(id, val, label, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.querySelector('.hs-val').textContent = val;
  el.querySelector('.hs-label').textContent = label;
  if (cls) el.querySelector('.hs-val').className = `hs-val ${cls}`;
}

// ── Filters wiring ────────────────────────────────────────────────────────────

function initFilters() {
  document.querySelectorAll('[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-filter]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _filterOutcome = btn.dataset.filter;
      renderGrid();
    });
  });

  document.querySelectorAll('[data-type]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-type]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _filterType = btn.dataset.type;
      renderGrid();
    });
  });

  const searchEl = document.getElementById('hfSearch');
  if (searchEl) {
    searchEl.addEventListener('input', () => {
      _filterSym = searchEl.value.trim();
      renderGrid();
    });
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function init() {
  const loadingEl = document.getElementById('histLoading');

  try {
    _allRows = await fetchAllScans();

    // Resolve outcomes in background (updates _allRows in-place then re-renders)
    resolveIfPending(_allRows).then(() => {
      updateSummary();
      renderGrid();
    }).catch(() => {});

    loadingEl.style.display = 'none';
    updateSummary();
    renderGrid();
    initFilters();
  } catch (err) {
    loadingEl.innerHTML = `<p style="color:#f87171">Failed to load history: ${err.message}</p>`;
  }
}

document.addEventListener('DOMContentLoaded', init);
