// ── APEX History Page ────────────────────────────────────────────────────────
// Fetches all saved scans from /api/memory, renders them with filtering,
// live outcome resolution (candle-based), and rescan comparison navigation.

const API_MEMORY = '/api/memory';
const API_CANDLES = '/api/candles';   // same endpoint dashboard uses

// ── State ────────────────────────────────────────────────────────────────────
let _allRows      = [];   // all rows from Supabase
let _filterOutcome = 'all';
let _filterType    = 'all';
let _filterSym     = '';

// ── Fetch helpers ─────────────────────────────────────────────────────────────

async function fetchAllScans() {
  const res = await fetch(`${API_MEMORY}?all=true&limit=200`);
  if (!res.ok) throw new Error('Failed to load scan history');
  return res.json();
}

// Resolve pending outcomes against fresh candle data (same logic as dashboard.js)
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
        const candleRes = await fetch(`${API_CANDLES}?sym=${encodeURIComponent(sym)}&type=${type}&resolution=D&bars=90`);
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

          // If no resolution but analysis is >14 days old → expired
          const ageMs = Date.now() - new Date(row.analysis_date).getTime();
          if (!resolved && ageMs > 14 * 86400 * 1000) resolved = 'expired';

          if (resolved) {
            // Update in memory
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

// ── Rendering helpers ─────────────────────────────────────────────────────────

function verdictClass(v) {
  if (!v) return '';
  const lv = v.toLowerCase().replace(/_/g, '-');
  if (lv.includes('strong-buy') || lv.includes('strong buy')) return 'strong-buy';
  if (lv.includes('buy'))  return 'buy';
  if (lv.includes('sell')) return lv.includes('strong') ? 'strong-sell' : 'sell';
  return 'hold';
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

function rescanUrl(row) {
  return `dashboard.html?sym=${encodeURIComponent(row.symbol)}&compare=${encodeURIComponent(row.id)}`;
}

function chartUrl(row) {
  return `index.html?sym=${encodeURIComponent(row.symbol)}`;
}

function renderCard(row) {
  const vc  = verdictClass(row.verdict);
  const vDisplay = (row.verdict || '—').replace(/_/g, ' ').toUpperCase();

  return `
    <div class="scan-card ${vc}">
      <div class="sc-head">
        <div>
          <div class="sc-sym">${row.symbol}</div>
          <div class="sc-date">${row.analysis_date}</div>
        </div>
        <span class="sc-type">${row.asset_type || 'Stock'}</span>
      </div>

      <div class="sc-verdict-row">
        <span class="sc-verdict ${vc}">${vDisplay}</span>
        <span class="sc-conf">${row.confidence != null ? row.confidence + '%' : '—'} confidence</span>
      </div>

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

      <div class="sc-actions">
        <a class="sc-btn sc-btn-rescan" href="${rescanUrl(row)}" title="Re-run the analysis and compare to this original scan">
          🔄 Rescan &amp; Compare
        </a>
        <a class="sc-btn sc-btn-chart" href="${chartUrl(row)}" title="Open chart for this symbol">
          📈 Chart
        </a>
      </div>
    </div>
  `;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Filter + render ───────────────────────────────────────────────────────────

function applyFilters() {
  return _allRows.filter(row => {
    if (_filterSym && !row.symbol.toUpperCase().includes(_filterSym.toUpperCase())) return false;
    if (_filterOutcome !== 'all' && row.outcome !== _filterOutcome) return false;
    if (_filterType    !== 'all' && row.asset_type !== _filterType)  return false;
    return true;
  });
}

function renderGrid() {
  const grid = document.getElementById('scanGrid');
  const empty = document.getElementById('histEmpty');
  const filtered = applyFilters();

  if (!filtered.length) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  grid.innerHTML = filtered.map(renderCard).join('');
}

function updateSummary() {
  const total = _allRows.length;
  const tp    = _allRows.filter(r => r.outcome === 'tp_hit').length;
  const sl    = _allRows.filter(r => r.outcome === 'sl_hit').length;
  const resolved = tp + sl;
  const accuracy = resolved > 0 ? Math.round(tp / resolved * 100) : null;

  setText('hsStat0', total,    'Total Scans');
  setText('hsStat1', tp,       'TP Hit',  'green');
  setText('hsStat2', sl,       'SL Hit',  'red');
  setText('hsStat3', accuracy != null ? accuracy + '%' : '—%', 'Accuracy', 'accent');
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
  // Outcome pills
  document.querySelectorAll('[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-filter]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _filterOutcome = btn.dataset.filter;
      renderGrid();
    });
  });

  // Type pills
  document.querySelectorAll('[data-type]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('[data-type]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _filterType = btn.dataset.type;
      renderGrid();
    });
  });

  // Symbol search
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
  const emptyEl   = document.getElementById('histEmpty');

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
