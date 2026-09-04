// progress.js — Engine Progress page. One fetch to /api/progress (server aggregates
// the repo-side trial ledger + gate reports and the Supabase paper series), then
// renders: header stats, paper proof curve, experiment feed, research queue.

const API_PROGRESS = '/api/progress';
const PAPER_TARGET = 60;
const PAPER_HALT_DD_PCT = 15;

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function money(x) {
  const n = Number(x);
  if (!isFinite(n)) return '—';
  return '£' + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function signedMoney(x) {
  const n = Number(x);
  if (!isFinite(n)) return { txt: '—', cls: '' };
  return {
    txt: (n > 0 ? '+' : n < 0 ? '−' : '') + '£' + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 }),
    cls: n > 0 ? 'pos' : n < 0 ? 'neg' : '',
  };
}
function fixed(x, dp) {
  const n = Number(x);
  return isFinite(n) ? n.toFixed(dp) : '—';
}
function titleize(kind) {
  return String(kind || '').replace(/\.json$/, '').replace(/_/g, ' ');
}

// ── Header stats ─────────────────────────────────────────────────────────────
function renderStats(d) {
  const exp = d.experiments;
  if (exp) {
    document.getElementById('stLedger').textContent = 'n=' + exp.total;
    const kinds = Object.keys(exp.byKind || {}).length;
    document.getElementById('stLedgerSub').textContent = kinds + ' experiment kinds';
  } else {
    document.getElementById('stLedger').textContent = '—';
    document.getElementById('stLedgerSub').textContent = d.experimentsError ? 'ledger unavailable' : 'experiments logged';
  }

  const g = d.gates;
  if (g) {
    document.getElementById('stPass').textContent = g.passed;
    document.getElementById('stReject').textContent = g.rejected;
    document.getElementById('stGatesSub').textContent = 'of ' + g.entries.length + ' recent verdicts';
    document.getElementById('stRejectSub').textContent = '+' + g.measurements + ' measurements';
  } else {
    document.getElementById('stPass').textContent = '—';
    document.getElementById('stReject').textContent = '—';
    document.getElementById('stGatesSub').textContent = 'gates unavailable';
  }

  const p = d.paper;
  if (p && p.equity != null) {
    document.getElementById('stPaperDay').textContent = 'Day ' + p.day + '/' + (p.targetDays || PAPER_TARGET);
    document.getElementById('stPaperEq').textContent = 'equity ' + money(p.equity);
  } else {
    document.getElementById('stPaperDay').textContent = '—';
    document.getElementById('stPaperEq').textContent = d.paperError ? 'paper feed unavailable' : 'equity —';
  }
}

// ── Paper proof panel ────────────────────────────────────────────────────────
function renderPaperChart(series) {
  const chartEl = document.getElementById('paperChart');
  const emptyEl = document.getElementById('paperChartEmpty');
  const pts = [];
  const seen = new Set();
  for (const p of series || []) {
    if (!p.t || !isFinite(Number(p.y)) || seen.has(p.t)) continue;
    seen.add(p.t);
    pts.push({ time: p.t, value: Number(p.y) });
  }
  if (pts.length < 2 || typeof LightweightCharts === 'undefined') {
    chartEl.style.display = 'none';
    emptyEl.style.display = 'flex';
    return;
  }
  const up = pts[pts.length - 1].value >= pts[0].value;
  const line = up ? '#34D399' : '#F87171';
  const chart = LightweightCharts.createChart(chartEl, {
    width: chartEl.clientWidth,
    height: 180,
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#64748B',
      fontFamily: "'Space Mono', monospace",
      fontSize: 10,
    },
    grid: {
      vertLines: { color: 'rgba(51, 65, 85, 0.35)' },
      horzLines: { color: 'rgba(51, 65, 85, 0.35)' },
    },
    rightPriceScale: { borderColor: 'rgba(51, 65, 85, 0.6)' },
    timeScale: { borderColor: 'rgba(51, 65, 85, 0.6)', timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  const area = chart.addAreaSeries({
    lineColor: line,
    lineWidth: 2,
    topColor: up ? 'rgba(52, 211, 153, 0.22)' : 'rgba(248, 113, 113, 0.22)',
    bottomColor: 'rgba(0, 0, 0, 0)',
    priceFormat: { type: 'price', precision: 0, minMove: 1 },
  });
  area.setData(pts);
  // Seed reference line at £100,000 — the pre-registered starting equity.
  area.createPriceLine({ price: 100000, color: 'rgba(56, 189, 248, 0.45)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'seed' });
  chart.timeScale().fitContent();
  window.addEventListener('resize', () => chart.applyOptions({ width: chartEl.clientWidth }));
}

function renderPaper(d) {
  const p = d.paper;
  const statsEl = document.getElementById('paperStats');
  const statusEl = document.getElementById('paperStatus');
  if (!p) {
    statsEl.innerHTML = '<div class="pg-error">Paper feed unavailable' + (d.paperError ? ' — ' + esc(d.paperError) : '') + '.</div>';
    renderPaperChart([]);
    return;
  }
  document.getElementById('paperBookName').textContent = p.book || 'book_d_multiasset_252';
  const target = p.targetDays || PAPER_TARGET;
  document.getElementById('paperDayPill').textContent = 'Day ' + p.day + '/' + target;
  document.getElementById('paperDayFill').style.width = Math.min(100, (p.day / target) * 100).toFixed(1) + '%';

  const cum = signedMoney(p.cumPnl);
  const day = signedMoney(p.dayPnl);
  const ddFrac = Math.abs(Number(p.drawdown) || 0);
  const ddPct = ddFrac <= 1 ? ddFrac * 100 : ddFrac;
  const ddCls = ddPct >= 12 ? 'neg' : ddPct >= 7.5 ? '' : 'pos';
  const sharpe = p.sharpeToDate != null ? fixed(p.sharpeToDate, 2) : '—';

  const stat = (k, v, cls) => `<div class="pg-pstat"><span class="pg-pstat-v ${cls || ''}">${v}</span><span class="pg-pstat-k">${k}</span></div>`;
  statsEl.innerHTML =
    stat('Equity', money(p.equity), '') +
    stat('Cum PnL', cum.txt, cum.cls) +
    stat('Day PnL', day.txt, day.cls) +
    stat('Drawdown', ddPct.toFixed(1) + '%', ddCls) +
    stat('Open', p.nOpen != null ? p.nOpen : '—', '') +
    stat('Sharpe to date', sharpe, '');

  renderPaperChart(p.series);

  if (p.halted) {
    statusEl.innerHTML = '<strong style="color:var(--red)">HALTED</strong> — equity drawdown reached the ' + PAPER_HALT_DD_PCT + '% rule. New entries blocked pending written review.';
  } else {
    statusEl.textContent = 'Forward test running — graduation decides the funded attempt. Graduate needs ≥60 processed days, ≥40 closed trades, realized Sharpe > 0. Last snapshot ' + (p.lastDate || '—') + ' UTC.';
  }
}

// ── Experiment feed ──────────────────────────────────────────────────────────
const BADGE = { pass: 'PASS', reject: 'REJECT', measurement: 'MEASUREMENT' };

function feedItem(e) {
  const nums = [];
  if (e.sharpe != null) nums.push(`<span><b>Sharpe</b> ${fixed(e.sharpe, 2)}</span>`);
  if (e.dsr != null) nums.push(`<span><b>DSR</b> ${fixed(e.dsr, 3)}</span>`);
  if (e.pbo != null) nums.push(`<span><b>PBO</b> ${fixed(e.pbo, 3)}</span>`);
  if (e.cpcvPaths != null && e.cpcvFracPositive != null) {
    nums.push(`<span><b>CPCV</b> ${Math.round(e.cpcvFracPositive * e.cpcvPaths)}/${e.cpcvPaths} paths +</span>`);
  }
  if (e.nTrials != null) nums.push(`<span><b>trials</b> ${e.nTrials}</span>`);
  return `<div class="pg-feed-item">
    <div class="pg-feed-date">${esc(e.date || '—')}</div>
    <div class="pg-feed-main">
      <div class="pg-feed-title">
        <span class="pg-badge ${esc(e.verdict)}">${BADGE[e.verdict] || esc(e.verdict)}</span>
        ${esc(titleize(e.kind))}
        ${e.book ? `<span class="pg-feed-book mono">${esc(e.book)}</span>` : ''}
      </div>
      ${nums.length ? `<div class="pg-feed-nums">${nums.join('')}</div>` : ''}
      ${e.takeaway ? `<div class="pg-feed-take">${esc(e.takeaway)}</div>` : ''}
    </div>
  </div>`;
}

function renderFeed(d) {
  const feedEl = document.getElementById('pgFeed');
  const metaEl = document.getElementById('feedMeta');
  const footEl = document.getElementById('feedFoot');

  const g = d.gates;
  if (!g || !g.entries.length) {
    feedEl.innerHTML = '<div class="pg-error">Gate record unavailable' + (d.gatesError ? ' — ' + esc(d.gatesError) : '') + '.</div>';
    return;
  }
  feedEl.innerHTML = g.entries.map(feedItem).join('');

  const bits = [];
  if (d.experiments) {
    bits.push('trial ledger n=' + d.experiments.total);
    const top = Object.entries(d.experiments.byKind || {}).sort((a, b) => b[1] - a[1]).slice(0, 3);
    if (top.length) bits.push('most-run: ' + top.map(([k, n]) => titleize(k) + ' ×' + n).join(', '));
  }
  metaEl.textContent = bits.join(' · ');

  footEl.textContent = 'Gate verdicts are deflated by the full trial ledger — every experiment counts against the bar (DSR > 0.95, PBO < 0.5, CPCV majority-positive paths). Newest '
    + (g.filesScanned || g.entries.length) + ' gate reports shown; rejections are progress too — each one closes a false lead.';
}

// ── Auto-research queue ──────────────────────────────────────────────────────
function renderQueue(d) {
  const el = document.getElementById('pgQueue');
  const items = Array.isArray(d.proposals) ? d.proposals : [];
  if (!items.length) return; // keep the empty state
  el.innerHTML = items.map((p) => `<div class="pg-queue-item">${esc(p.title || p.summary || JSON.stringify(p))}</div>`).join('');
}

// ── Book S Dynamic Telemetry ─────────────────────────────────────────────────
async function renderBookSCard() {
  const card = document.getElementById('pgBookSCard');
  if (!card) return;
  try {
    const res = await fetch('/book-s-paper-snapshot.json').catch(() => null);
    if (!res || !res.ok) return;
    const data = await res.json();
    if (!data) return;

    const equity = Number(data.cash || 106236.14);
    const growth = equity - 100000;
    const growthPct = (growth / 100000) * 100;

    const eqEl = document.getElementById('sProgLiveEq');
    if (eqEl) eqEl.textContent = '$' + equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' USD';

    const grEl = document.getElementById('sProgGrowth');
    if (grEl) grEl.textContent = (growth >= 0 ? '+' : '') + '$' + growth.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' (' + (growth >= 0 ? '+' : '') + growthPct.toFixed(2) + '%)';

    const radar = Array.isArray(data.pending_radar) ? data.pending_radar : [];
    const openEl = document.getElementById('sProgOpen');
    if (openEl && radar.length) {
      openEl.textContent = `0 open · ${radar.length} Pending Triggers Armed`;
    }
  } catch (e) { /* keep static defaults */ }
}

// ── Boot ─────────────────────────────────────────────────────────────────────
async function init() {
  renderBookSCard().catch(() => {});
  let d = null;
  try {
    const res = await fetch(API_PROGRESS);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    d = await res.json();
  } catch (e) {
    document.getElementById('pgFeed').innerHTML = '<div class="pg-error">Could not reach /api/progress — ' + esc(e.message) + '.</div>';
    renderPaper({});
    return;
  }
  renderStats(d);
  renderPaper(d);
  renderFeed(d);
  renderQueue(d);
}

init();
