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

// Style-aware outcome resolution. Each trade style is graded on a matching-
// granularity timeframe (so intrabar TP/SL is detected accurately) with an expiry
// scaled to its holding horizon; the style is read from the row's persisted
// setup_features (rows without it default to swing). Direction comes from verdictDir
// so SHORT verdicts resolve correctly (they say "SHORT", not "sell").
const STYLE_RES = {
  scalp:    { tf: '15m', expiryDays: 3,   bufferDays: 1 },
  intraday: { tf: '1h',  expiryDays: 7,   bufferDays: 2 },
  swing:    { tf: '1d',  expiryDays: 30,  bufferDays: 5 },
  position: { tf: '1d',  expiryDays: 120, bufferDays: 7 },
};
function resolutionFor(row) {
  let f = row && row.setup_features;
  if (typeof f === 'string') { try { f = JSON.parse(f); } catch { f = null; } }
  const s = (f && f.style ? String(f.style) : 'swing').toLowerCase();
  return STYLE_RES[s] || STYLE_RES.swing;
}

async function resolveIfPending(rows) {
  const pending = rows.filter(r => r.outcome === 'pending' && r.target_price && r.stop_loss && r.price);
  if (!pending.length) return;

  // Group by symbol + resolution timeframe (style-derived), fetching the right-
  // granularity candles once per group.
  const groups = {};
  for (const r of pending) {
    const res = resolutionFor(r);
    const key = r.symbol + '|' + res.tf;
    (groups[key] ||= { sym: r.symbol, type: r.asset_type || 'Stock', tf: res.tf, rows: [] }).rows.push({ row: r, res });
  }

  await Promise.allSettled(
    Object.values(groups).map(async (g) => {
      try {
        const oldest = Math.min(...g.rows.map(x => rowTs(x.row) / 1000));
        const buffer = Math.max(...g.rows.map(x => x.res.bufferDays)) * 86400;
        const from   = Math.floor(oldest - buffer);
        const to     = Math.floor(Date.now() / 1000);
        const candleRes = await fetch(
          `${API_CANDLES}?sym=${encodeURIComponent(g.sym)}&type=${encodeURIComponent(g.type)}&tf=${g.tf}&from=${from}&to=${to}`
        );
        if (!candleRes.ok) return;
        const candles = await candleRes.json();
        if (!Array.isArray(candles) || candles.length < 2) return;

        for (const { row, res } of g.rows) {
          const entryTs = rowTs(row) / 1000;
          const tp = parseFloat(row.target_price);
          const sl = parseFloat(row.stop_loss);
          if (isNaN(tp) || isNaN(sl)) continue;
          const dir = verdictDir(row.verdict);

          const afterEntry = candles.filter(c => c.time > entryTs);
          let resolved = null;
          if (dir !== 'neutral') {
            // Entry-fill gate: only grade TP/SL once price trades INTO the entry zone.
            // An entry at/around the scan price fills at market; a pullback/breakout
            // entry must be reached first — otherwise a TP "hit" without a fill is a
            // phantom win that never actually happened.
            const eb = entryBounds(row.entry_zone);
            const scanPx = parseFloat(row.price);
            const atMarket = eb && !isNaN(scanPx) && scanPx >= eb.lo - Math.abs(eb.lo) * 0.003 && scanPx <= eb.hi + Math.abs(eb.hi) * 0.003;
            let filled = !eb || atMarket;
            for (const bar of afterEntry) {
              if (!filled) {
                if (bar.low <= eb.hi && bar.high >= eb.lo) filled = true;
                else continue;
              }
              if (dir === 'short') {
                if (bar.low  <= tp) { resolved = 'tp_hit'; break; }
                if (bar.high >= sl) { resolved = 'sl_hit'; break; }
              } else {
                if (bar.high >= tp) { resolved = 'tp_hit'; break; }
                if (bar.low  <= sl) { resolved = 'sl_hit'; break; }
              }
            }
          }

          // No TP/SL hit but past the style's expiry window → expired.
          const ageDays = (Date.now() / 1000 - entryTs) / 86400;
          if (!resolved && ageDays > res.expiryDays) resolved = 'expired';

          if (resolved) {
            row.outcome = resolved;
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

// ── Accuracy scoreboard ─────────────────────────────────────────────────────
// Aggregates realised outcomes across ALL scans into headline accuracy metrics.
function computeAccuracy(rows) {
  const total    = rows.length;
  const resolved = rows.filter(r => r.outcome === 'tp_hit' || r.outcome === 'sl_hit');
  const wins     = resolved.filter(r => r.outcome === 'tp_hit');
  const losses   = resolved.filter(r => r.outcome === 'sl_hit');
  const winRate  = resolved.length ? Math.round(wins.length / resolved.length * 100) : null;
  const pctResolved = total ? Math.round(resolved.length / total * 100) : 0;

  const avgConf = arr => arr.length ? Math.round(arr.reduce((s, r) => s + (Number(r.confidence) || 0), 0) / arr.length) : null;

  const dirOf = r => {
    const u = (r.verdict || '').toUpperCase();
    if (/BUY/.test(u)) return 'buy';
    if (/SELL|SHORT/.test(u)) return 'sell';
    return 'other';
  };
  const buyRes  = resolved.filter(r => dirOf(r) === 'buy');
  const sellRes = resolved.filter(r => dirOf(r) === 'sell');
  const acc = set => set.length ? Math.round(set.filter(r => r.outcome === 'tp_hit').length / set.length * 100) : null;

  const hiConf = resolved.filter(r => (Number(r.confidence) || 0) >= 80);

  // Brier score — mean squared error between the stated probability and the
  // realised binary outcome (1 = TP hit, 0 = SL hit). Lower is better; 0.25 is the
  // no-skill baseline (always saying 50%). This is the PROPER way to score
  // confidence: a win-rate / ranking can look fine while the probabilities
  // themselves are badly miscalibrated (overconfident).
  let brier = null;
  if (resolved.length) {
    const sum = resolved.reduce((s, r) => {
      const p = Math.min(1, Math.max(0, (Number(r.confidence) || 50) / 100));
      const o = r.outcome === 'tp_hit' ? 1 : 0;
      return s + (p - o) * (p - o);
    }, 0);
    brier = +(sum / resolved.length).toFixed(3);
  }

  // Reliability curve — realised hit-rate per stated-confidence band. A well-
  // calibrated model sits near the diagonal (80% band actually wins ~80%).
  const _bandMid = { '50–59': 55, '60–69': 65, '70–79': 75, '80–89': 85, '90+': 95 };
  const reliability = [
    { band: '50–59', lo: 0,  hi: 59 }, { band: '60–69', lo: 60, hi: 69 },
    { band: '70–79', lo: 70, hi: 79 }, { band: '80–89', lo: 80, hi: 89 },
    { band: '90+',   lo: 90, hi: 100 },
  ].map(b => {
    const set = resolved.filter(r => { const c = Number(r.confidence) || 0; return c >= b.lo && c <= b.hi; });
    const a   = set.length ? Math.round(set.filter(r => r.outcome === 'tp_hit').length / set.length * 100) : null;
    return { band: b.band, n: set.length, acc: a, gap: a == null ? null : a - _bandMid[b.band] };
  }).filter(b => b.n > 0);

  return {
    total, resolvedN: resolved.length, pctResolved, winRate,
    wins: wins.length, losses: losses.length,
    avgWinConf: avgConf(wins), avgLossConf: avgConf(losses),
    buyAcc: acc(buyRes),   buyN: buyRes.length,
    sellAcc: acc(sellRes), sellN: sellRes.length,
    hiConfAcc: acc(hiConf), hiConfN: hiConf.length,
    brier, reliability,
  };
}

function accStat(label, value, sub, cls) {
  return `<div class="acc-stat">
    <span class="acc-val ${cls || ''}">${value}</span>
    <span class="acc-label">${label}</span>
    ${sub ? `<span class="acc-sub">${sub}</span>` : ''}
  </div>`;
}

function renderScoreboard() {
  const el = document.getElementById('accBoard');
  if (!el) return;
  const a = computeAccuracy(_allRows);
  if (!a.total) { el.innerHTML = ''; return; }

  const wrCls = a.winRate == null ? '' : a.winRate >= 50 ? 'pos' : 'neg';
  const cmp = (a.avgWinConf != null && a.avgLossConf != null)
    ? `${a.avgWinConf}% on wins vs ${a.avgLossConf}% on losses` : '—';
  const cmpCls = (a.avgWinConf != null && a.avgLossConf != null)
    ? (a.avgWinConf >= a.avgLossConf ? 'pos' : 'neg') : '';

  const relRows = (a.reliability || []).map(r => {
    const cls = r.gap == null ? '' : Math.abs(r.gap) <= 10 ? 'pos' : 'neg';
    const w   = r.acc == null ? 0 : Math.max(3, Math.min(100, r.acc));
    return `<div class="acc-rel-row">
      <span class="acc-rel-band">${r.band}%</span>
      <span class="acc-rel-bar"><span class="acc-rel-fill ${cls}" style="width:${w}%"></span></span>
      <span class="acc-rel-val ${cls}">${r.acc}% actual</span>
      <span class="acc-rel-n">n=${r.n}</span>
    </div>`;
  }).join('');

  el.innerHTML = `
    <div class="acc-title">🎯 Accuracy Scoreboard</div>
    <div class="acc-grid">
      ${accStat('Total Scans', a.total, `${a.resolvedN} resolved`, '')}
      ${accStat('% Resolved', a.pctResolved + '%', `${a.total - a.resolvedN} still open`, '')}
      ${accStat('Win Rate', a.winRate != null ? a.winRate + '%' : '—', a.resolvedN ? `${a.wins}W / ${a.losses}L` : 'no resolved calls', wrCls)}
      ${accStat('Conf · Win vs Loss', cmp, 'avg confidence by outcome', cmpCls)}
      ${accStat('BUY Accuracy', a.buyAcc != null ? a.buyAcc + '%' : '—', a.buyN ? `${a.buyN} resolved BUYs` : 'none resolved', a.buyAcc == null ? '' : a.buyAcc >= 50 ? 'pos' : 'neg')}
      ${accStat('SELL Accuracy', a.sellAcc != null ? a.sellAcc + '%' : '—', a.sellN ? `${a.sellN} resolved SELLs` : 'none resolved', a.sellAcc == null ? '' : a.sellAcc >= 50 ? 'pos' : 'neg')}
      ${accStat('Brier Score', a.brier != null ? a.brier.toFixed(3) : '—',
        a.brier == null ? 'need resolved calls' : a.brier <= 0.25 ? 'beats 50/50 guessing' : 'worse than a coin-flip',
        a.brier == null ? '' : a.brier <= 0.25 ? 'pos' : 'neg')}
    </div>
    ${relRows ? `<div class="acc-rel">
      <div class="acc-rel-title">Calibration curve — stated confidence vs. what actually happened</div>
      <div class="acc-rel-rows">${relRows}</div>
      <div class="acc-rel-foot">Bands within ±10% of the diagonal are well-calibrated. Large gaps = over/under-confidence the live verdict now auto-corrects.</div>
    </div>` : ''}
    <div class="acc-calib ${a.hiConfAcc == null ? '' : a.hiConfAcc >= 50 ? 'pos' : 'neg'}">
      When APEX says <strong>80%+ confidence</strong> →
      ${a.hiConfN ? `<strong>${a.hiConfAcc}% accuracy</strong> across ${a.hiConfN} resolved high-conviction call${a.hiConfN === 1 ? '' : 's'}` : 'no resolved 80%+ calls yet'}
    </div>`;
}

// ── View toggle (Scans ⟷ Watchlist) ─────────────────────────────────────────
let _currentView = 'scans';

function setView(view) {
  _currentView = view;
  document.querySelectorAll('.vt-btn').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  const isScans = view === 'scans';
  document.querySelector('.hist-filters').style.display = isScans ? '' : 'none';
  document.getElementById('scanGrid').style.display     = isScans ? '' : 'none';
  document.getElementById('accBoard').style.display     = isScans ? '' : 'none';
  const empty = document.getElementById('histEmpty');
  if (empty && !isScans) empty.style.display = 'none';
  document.getElementById('watchlistView').style.display = isScans ? 'none' : '';
  if (isScans) renderGrid(); else renderWatchlist();
}

function initViewToggle() {
  document.querySelectorAll('.vt-btn').forEach(btn => {
    btn.addEventListener('click', () => setView(btn.dataset.view));
  });
}

// ── Watchlist (localStorage) ─────────────────────────────────────────────────
const WL_KEY    = 'apex_watchlist';
const ALERT_KEY = 'apex_alerts';

function getWatchlist() { try { return JSON.parse(localStorage.getItem(WL_KEY) || '[]'); } catch { return []; } }
function setWatchlistStore(l) { try { localStorage.setItem(WL_KEY, JSON.stringify(l)); } catch {} }
function getAlerts() { try { return JSON.parse(localStorage.getItem(ALERT_KEY) || '{}'); } catch { return {}; } }
function setAlertsStore(a) { try { localStorage.setItem(ALERT_KEY, JSON.stringify(a)); } catch {} }

const _wlPrices = {};   // sym → latest close, cached across renders

async function fetchLivePrice(sym, type) {
  try {
    const to = Math.floor(Date.now() / 1000), from = to - 7 * 86400;
    const r = await fetch(`${API_CANDLES}?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type || 'Stock')}&tf=1d&from=${from}&to=${to}`);
    if (!r.ok) return null;
    const c = await r.json();
    if (!Array.isArray(c) || !c.length) return null;
    return c[c.length - 1].close;
  } catch { return null; }
}

// Parse the low/high bounds of an entry zone like "182.5 - 185" or "182.5".
function entryBounds(entryZone) {
  const nums = String(entryZone == null ? '' : entryZone).match(/-?\d+(?:\.\d+)?/g);
  if (!nums) return null;
  const v = nums.map(Number).filter(n => !isNaN(n));
  if (!v.length) return null;
  return { lo: Math.min(...v), hi: Math.max(...v) };
}

// Classify a watchlist row given its live price.
function rowStatus(item, price) {
  if (price == null) return { cls: 'pending', label: '⏳ —' };
  const dir = verdictDir(item.verdict);
  const sl  = parseFloat(item.stop_loss);
  const tp  = parseFloat(item.target_price);
  const eb  = entryBounds(item.entry_zone);

  // Stop hit?
  if (!isNaN(sl)) {
    if (dir === 'short' && price >= sl) return { cls: 'stopped', label: '❌ Stop hit' };
    if (dir !== 'short' && price <= sl) return { cls: 'stopped', label: '❌ Stop hit' };
  }
  // Target hit?
  if (!isNaN(tp)) {
    if (dir === 'short' && price <= tp) return { cls: 'target', label: '✅ Target hit' };
    if (dir !== 'short' && price >= tp) return { cls: 'target', label: '✅ Target hit' };
  }
  // In the entry zone?
  if (eb) {
    const pad = (eb.hi - eb.lo) * 0.001 + Math.abs(eb.hi) * 0.0015;   // small tolerance
    if (price >= eb.lo - pad && price <= eb.hi + pad) return { cls: 'inzone', label: '🟢 In entry zone' };
  }
  return { cls: 'pending', label: '⏳ Pending' };
}

function renderWatchlist() {
  const list = getWatchlist();
  const wrap  = document.getElementById('wlTableWrap');
  const empty = document.getElementById('wlEmpty');
  const body  = document.getElementById('wlBody');
  if (!list.length) {
    wrap.style.display = 'none';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  wrap.style.display = '';

  const alerts = getAlerts();
  body.innerHTML = list.map((item, i) => {
    const price = _wlPrices[item.sym];
    const st = rowStatus(item, price);
    const vc = verdictClass(item.verdict);
    const hasAlert = alerts[item.sym] != null;
    return `
      <tr class="wl-row ${st.cls}">
        <td class="wl-sym">${escHtml(item.sym)}<span class="wl-type">${escHtml(item.type || '')}</span></td>
        <td><span class="wl-verdict ${vc}">${escHtml((item.verdict || '—').replace(/_/g, ' '))}</span></td>
        <td class="wl-mono">${item.confidence != null ? item.confidence + '%' : '—'}</td>
        <td class="wl-mono">${item.entry_zone ? escHtml(String(item.entry_zone)) : '—'}</td>
        <td class="wl-mono stop">${item.stop_loss ? fmtPrice(item.stop_loss) : '—'}</td>
        <td class="wl-mono target">${item.target_price ? fmtPrice(item.target_price) : '—'}</td>
        <td class="wl-mono now">${price != null ? fmtPrice(price) : '…'}</td>
        <td><span class="wl-status ${st.cls}">${st.label}</span></td>
        <td class="wl-actions">
          <button class="wl-btn ${hasAlert ? 'on' : ''}" onclick="setAlert(${i})" title="${hasAlert ? 'Alert set at ' + alerts[item.sym] : 'Set a price alert'}">🔔${hasAlert ? '✓' : ''}</button>
          <button class="wl-btn del" onclick="removeFromWatchlist(${i})" title="Remove">✕</button>
        </td>
      </tr>`;
  }).join('');

  // Fetch any missing live prices, then re-render once they land
  const missing = list.filter(item => _wlPrices[item.sym] == null);
  if (missing.length) {
    Promise.allSettled(missing.map(async item => {
      const p = await fetchLivePrice(item.sym, item.type);
      if (p != null) _wlPrices[item.sym] = p;
    })).then(() => { if (_currentView === 'watchlist') renderWatchlist(); checkAlerts(); });
  }

  // Portfolio heat / correlation (needs ≥2 holdings)
  loadPortfolioHeat(list);
}

// ── Portfolio heat / correlation ─────────────────────────────────────────────
// Highly-correlated holdings are effectively ONE position at multiplied size — the
// classic risk that "5 different longs" are really 5× the same beta. We fetch each
// holding's recent daily returns and surface pairs that move together.
const _wlReturns = {};   // sym → array of daily returns (cached)

async function fetchReturnSeries(sym, type) {
  if (_wlReturns[sym]) return _wlReturns[sym];
  try {
    const to = Math.floor(Date.now() / 1000), from = to - 130 * 86400;
    const r = await fetch(`${API_CANDLES}?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type || 'Stock')}&tf=1d&from=${from}&to=${to}`);
    if (!r.ok) return null;
    const c = await r.json();
    if (!Array.isArray(c) || c.length < 20) return null;
    const closes = c.map(b => b.close).filter(x => x != null);
    const rets = closes.slice(1).map((v, i) => (v - closes[i]) / closes[i]);
    _wlReturns[sym] = rets;
    return rets;
  } catch { return null; }
}

function pearson(a, b) {
  const n = Math.min(a.length, b.length);
  if (n < 15) return null;
  const x = a.slice(-n), y = b.slice(-n);
  const mx = x.reduce((s, v) => s + v, 0) / n, my = y.reduce((s, v) => s + v, 0) / n;
  let num = 0, dx = 0, dy = 0;
  for (let i = 0; i < n; i++) { const a1 = x[i] - mx, b1 = y[i] - my; num += a1 * b1; dx += a1 * a1; dy += b1 * b1; }
  const den = Math.sqrt(dx * dy);
  return den > 0 ? num / den : null;
}

async function loadPortfolioHeat(list) {
  const el = document.getElementById('wlHeat');
  if (!el) return;
  if (!list || list.length < 2) { el.innerHTML = ''; return; }

  await Promise.allSettled(list.map(item => fetchReturnSeries(item.sym, item.type)));
  if (_currentView !== 'watchlist') return;

  // Pairwise correlations
  const pairs = [];
  const involved = new Set();
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      const a = _wlReturns[list[i].sym], b = _wlReturns[list[j].sym];
      if (!a || !b) continue;
      const c = pearson(a, b);
      if (c == null) continue;
      pairs.push({ a: list[i].sym, b: list[j].sym, c });
      if (Math.abs(c) >= 0.7) { involved.add(list[i].sym); involved.add(list[j].sym); }
    }
  }
  if (!pairs.length) { el.innerHTML = ''; return; }

  const high = pairs.filter(p => Math.abs(p.c) >= 0.7).sort((a, b) => Math.abs(b.c) - Math.abs(a.c));
  const corrClass = c => Math.abs(c) >= 0.7 ? 'hot' : Math.abs(c) >= 0.4 ? 'warm' : 'cool';
  const top = pairs.slice().sort((a, b) => Math.abs(b.c) - Math.abs(a.c)).slice(0, 6);

  let warn = '';
  if (involved.size >= 2) {
    warn = `<div class="wl-heat-warn">⚠️ ${involved.size} of your ${list.length} holdings are highly correlated (≥0.7) — they move as effectively one position, so your real risk is more concentrated than the count suggests. Consider sizing them as a group.</div>`;
  }

  el.innerHTML = `
    <div class="wl-heat-title">🔥 Portfolio Heat — correlation of recent daily moves</div>
    ${warn}
    <div class="wl-heat-pairs">
      ${top.map(p => `<span class="wl-corr ${corrClass(p.c)}">${escHtml(p.a)} · ${escHtml(p.b)} <strong>${p.c >= 0 ? '+' : ''}${p.c.toFixed(2)}</strong></span>`).join('')}
    </div>`;
}

function removeFromWatchlist(i) {
  const list = getWatchlist();
  if (i < 0 || i >= list.length) return;
  const removed = list[i];
  list.splice(i, 1);
  setWatchlistStore(list);
  // Drop any alert tied to a symbol no longer on the list
  if (removed && !list.some(x => x.sym === removed.sym)) {
    const alerts = getAlerts(); delete alerts[removed.sym]; setAlertsStore(alerts);
  }
  renderWatchlist();
}

// Set / clear a price-alert threshold for a watchlist row.
function setAlert(i) {
  const list = getWatchlist();
  const item = list[i];
  if (!item) return;
  const alerts = getAlerts();
  if (alerts[item.sym] != null) {            // toggle off if already set
    delete alerts[item.sym];
    setAlertsStore(alerts);
    renderWatchlist();
    return;
  }
  const eb = entryBounds(item.entry_zone);
  const suggested = eb ? ((eb.lo + eb.hi) / 2) : (_wlPrices[item.sym] ?? item.currentPrice ?? '');
  const input = window.prompt(`Set a price alert for ${item.sym}.\nYou'll be notified when the price reaches this level:`, suggested ? String(+(+suggested).toFixed(5)) : '');
  if (input == null) return;
  const threshold = parseFloat(input);
  if (isNaN(threshold)) return;
  const ref = _wlPrices[item.sym] ?? parseFloat(item.currentPrice) ?? threshold;
  alerts[item.sym] = threshold;
  // Remember which side of the threshold we started on, so we can detect a crossing
  alerts[`${item.sym}__from`] = ref >= threshold ? 'above' : 'below';
  setAlertsStore(alerts);
  renderWatchlist();
  checkAlerts();
}

// On load / focus, fire any alert whose threshold has been reached.
function checkAlerts() {
  const alerts = getAlerts();
  const list = getWatchlist();
  const triggered = [];
  for (const item of list) {
    const th = alerts[item.sym];
    if (th == null) continue;
    const price = _wlPrices[item.sym];
    if (price == null) continue;
    const from = alerts[`${item.sym}__from`];
    const reached = from === 'above' ? price <= th : from === 'below' ? price >= th
      : Math.abs(price - th) / (Math.abs(th) || 1) < 0.002;
    if (reached) triggered.push({ sym: item.sym, th, price });
  }
  renderAlertBanner(triggered);
}

function renderAlertBanner(triggered) {
  const el = document.getElementById('alertBanner');
  if (!el) return;
  if (!triggered.length) { el.innerHTML = ''; return; }
  el.innerHTML = triggered.map(t =>
    `<div class="alert-banner">⚠️ <strong>${escHtml(t.sym)}</strong> has reached your alert level (${fmtPrice(t.th)}) — now ${fmtPrice(t.price)}.
      <button class="alert-dismiss" onclick="dismissAlert('${escHtml(t.sym)}')">Dismiss</button></div>`
  ).join('');
}

function dismissAlert(sym) {
  const alerts = getAlerts();
  delete alerts[sym];
  delete alerts[`${sym}__from`];
  setAlertsStore(alerts);
  checkAlerts();
  if (_currentView === 'watchlist') renderWatchlist();
}

// Pre-fetch watchlist prices so the alert banner can fire on load (any view).
async function primeWatchlistPrices() {
  const list = getWatchlist();
  if (!list.length) return;
  await Promise.allSettled(list.map(async item => {
    const p = await fetchLivePrice(item.sym, item.type);
    if (p != null) _wlPrices[item.sym] = p;
  }));
  checkAlerts();
}

// Refresh live prices when the tab regains focus (lightweight polling).
function refreshOnFocus() {
  window.addEventListener('focus', () => {
    const list = getWatchlist();
    if (!list.length) return;
    Promise.allSettled(list.map(async item => {
      const p = await fetchLivePrice(item.sym, item.type);
      if (p != null) _wlPrices[item.sym] = p;
    })).then(() => { checkAlerts(); if (_currentView === 'watchlist') renderWatchlist(); });
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function init() {
  const loadingEl = document.getElementById('histLoading');

  try {
    _allRows = await fetchAllScans();

    // Resolve outcomes in background (updates _allRows in-place then re-renders)
    resolveIfPending(_allRows).then(() => {
      updateSummary();
      renderScoreboard();
      if (_currentView === 'scans') renderGrid();
    }).catch(() => {});

    loadingEl.style.display = 'none';
    updateSummary();
    renderScoreboard();
    renderGrid();
    initFilters();
    initViewToggle();
    refreshOnFocus();
    primeWatchlistPrices();   // so an alert can fire on load even from the Scans view
  } catch (err) {
    loadingEl.innerHTML = `<p style="color:#f87171">Failed to load history: ${err.message}</p>`;
  }
}

document.addEventListener('DOMContentLoaded', init);
