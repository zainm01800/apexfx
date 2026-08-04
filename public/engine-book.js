// engine-book.js — Engine proof book (£100k paper experiment): hero equity,
// equity curve, book stats, open positions, closed trades.
// Data comes from /api/paper (Supabase mirror of the engine's nightly step):
//   ?table=daily&limit=500  — ascending daily equity snapshots (apex_paper_daily)
//   ?table=positions        — open engine paper positions (apex_paper_positions)
// Closed trades are read from the latest daily row's state_extra.trades log —
// there is no separate closed-trades endpoint.

const BOOK_START_EQUITY = 100000; // seeded £100,000 on 2026-07-16
const BOOK_START_LABEL = '16 Jul 2026';
const BOOK_CCY = '£'; // the experiment is a £100k virtual book

let _dailyRows = [];    // ascending daily snapshots
let _positions = [];    // open engine positions
let _eqChart = null;    // equity curve chart instance (destroyed before re-render)
let _eqResize = null;   // resize handler for the equity chart (replaced, not stacked)

// ── Formatting helpers (same conventions as ibkr-trades.js) ──────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function num(v) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

function fmtMoney(v) {
  const n = num(v);
  if (n === null) return '—';
  const sign = n < 0 ? '-' : '';
  return sign + BOOK_CCY + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtSignedMoney(v) {
  const n = num(v);
  if (n === null) return '—';
  return (n >= 0 ? '+' : '-') + BOOK_CCY + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtQty(v) {
  const n = num(v);
  if (n === null) return '—';
  return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

// Paper rows carry no asset_class — classify loosely: slash pairs are forex or
// crypto (by base), anything else is a stock. Matches the engine's book shape.
function paperClassFor(inst) {
  if (inst.includes('/')) {
    const base = inst.split('/')[0].toUpperCase();
    return ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'DOGE', 'LINK', 'AVAX'].includes(base) ? 'crypto' : 'forex';
  }
  return 'stocks';
}

function fmtPrice(v, assetClass) {
  const n = num(v);
  if (n === null) return '—';
  if (assetClass === 'forex') return n.toFixed(5);
  if (assetClass === 'crypto') return n >= 100 ? n.toFixed(2) : n.toFixed(4);
  return n.toFixed(2);
}

function pnlClass(v) {
  const n = num(v);
  if (n === null || n === 0) return '';
  return n > 0 ? 'green' : 'red';
}

// Every timestamp on this page is shown in UK time (Europe/London), explicitly
// labeled "UK". Supabase stores UTC ISO strings — this is the single conversion
// point. The book steps nightly off DAILY bars, so entry dates are dates.
const UK_TZ = 'Europe/London';
function fmtUK(ts, withSeconds) {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '—';
  const opts = { timeZone: UK_TZ, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false };
  if (withSeconds) opts.second = '2-digit';
  return d.toLocaleString('en-GB', opts) + ' UK';
}

function fmtDay(ts) {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-GB', { timeZone: UK_TZ, day: '2-digit', month: 'short', year: 'numeric' });
}

function setText(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  if (cls !== undefined) el.className = 'hs-val' + (cls ? ' ' + cls : '');
}

function dirBadge(isLong) {
  return isLong
    ? '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">LONG</span>'
    : '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">SHORT</span>';
}

function pnlBadge(pnl, pct) {
  const pnlTxt = fmtSignedMoney(pnl) + (pct === null ? '' : ` (${pct >= 0 ? '+' : ''}${(pct * 100).toFixed(2)}%)`);
  if (pnl > 0) {
    return `<span style="display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:6px; background:rgba(0,200,100,0.18); color:var(--green); font-family:var(--mono); border:1px solid rgba(0,200,100,0.35); box-shadow: 0 0 10px rgba(0,200,100,0.15);">${escHtml(pnlTxt)}</span>`;
  }
  if (pnl < 0) {
    return `<span style="display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:6px; background:rgba(255,70,70,0.18); color:var(--red); font-family:var(--mono); border:1px solid rgba(255,70,70,0.35); box-shadow: 0 0 10px rgba(255,70,70,0.15);">${escHtml(pnlTxt)}</span>`;
  }
  return `<span style="display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:6px; background:rgba(255,255,255,0.06); color:var(--text2); font-family:var(--mono); border:1px solid var(--border);">${escHtml(pnlTxt)}</span>`;
}

// ── Data loading ─────────────────────────────────────────────────────────────
async function loadEngineBook() {
  const SUPA_URL = 'https://cuvchjhaojhmxfgczndy.supabase.co';
  const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';
  const supaHeaders = { 'apikey': SUPA_ANON, 'Authorization': `Bearer ${SUPA_ANON}` };

  let daily = null;
  let positions = null;

  try {
    const [dRes, pRes] = await Promise.all([
      fetch('/api/paper?table=daily&limit=500').catch(() => null),
      fetch('/api/paper?table=positions&limit=100').catch(() => null),
    ]);
    if (dRes && dRes.ok) daily = await dRes.json().catch(() => null);
    if (pRes && pRes.ok) positions = await pRes.json().catch(() => null);
  } catch (e) { /* fall through to direct Supabase */ }

  // Direct Supabase REST fallback if the Vercel serverless proxy route returned
  // an error or is unreachable (e.g. static local dev server without /api).
  if (!Array.isArray(daily) || !Array.isArray(positions)) {
    const [sdRes, spRes] = await Promise.all([
      fetch(`${SUPA_URL}/rest/v1/apex_paper_daily?order=date.asc&limit=500`, { headers: supaHeaders }).catch(() => null),
      fetch(`${SUPA_URL}/rest/v1/apex_paper_positions?order=instrument.asc&limit=100`, { headers: supaHeaders }).catch(() => null),
    ]);
    if (sdRes && sdRes.ok) daily = await sdRes.json().catch(() => daily);
    if (spRes && spRes.ok) positions = await spRes.json().catch(() => positions);
  }

  if (Array.isArray(daily)) _dailyRows = daily;
  if (Array.isArray(positions)) _positions = positions;

  renderAll();
}

// ── Derived book state ───────────────────────────────────────────────────────
function latestDaily() {
  return _dailyRows.length ? _dailyRows[_dailyRows.length - 1] : null;
}

function closedTrades() {
  const latest = latestDaily();
  const trades = latest && latest.state_extra && Array.isArray(latest.state_extra.trades)
    ? latest.state_extra.trades
    : [];
  return [...trades].sort((a, b) => String(b.exit_time || '').localeCompare(String(a.exit_time || '')));
}

function positionUpnl(p) {
  const entry = num(p.entry_price);
  const lastPx = num(p.last_px);
  const units = num(p.units);
  if (entry === null || lastPx === null || units === null) return null;
  const isLong = String(p.direction || '').toLowerCase() !== 'short';
  return (isLong ? (lastPx - entry) : (entry - lastPx)) * units;
}

// ── Hero + book stats ────────────────────────────────────────────────────────
function renderHero() {
  const latest = latestDaily();

  const equity = latest ? num(latest.equity) : null;
  const dayPnl = latest ? num(latest.day_pnl) : null;
  const cumPnl = latest && num(latest.cum_pnl) !== null
    ? num(latest.cum_pnl)
    : (equity !== null ? equity - BOOK_START_EQUITY : null);
  const cash = latest ? num(latest.cash) : null;

  let maxDD = null;
  for (const r of _dailyRows) {
    const dd = num(r.drawdown_from_peak);
    if (dd !== null && (maxDD === null || dd > maxDD)) maxDD = dd;
  }
  const curDD = latest ? num(latest.drawdown_from_peak) : null;

  setText('engEquity', fmtMoney(equity));
  setText('engCash', fmtMoney(cash));
  setText('engDayPnl', fmtSignedMoney(dayPnl), pnlClass(dayPnl));
  setText('engCumPnl', fmtSignedMoney(cumPnl), pnlClass(cumPnl));
  setText('engCurDD', curDD === null ? '—' : '-' + (curDD * 100).toFixed(2) + '%', curDD > 0 ? 'red' : '');
  setText('engMaxDD', maxDD === null ? '—' : '-' + (maxDD * 100).toFixed(2) + '%', maxDD > 0 ? 'red' : '');

  const dayChip = document.getElementById('engDayChip');
  if (dayChip) {
    dayChip.textContent = 'Today: ' + fmtSignedMoney(dayPnl);
    dayChip.style.color = dayPnl === null ? 'var(--text2)' : (dayPnl >= 0 ? 'var(--green)' : 'var(--red)');
  }
  const sinceChip = document.getElementById('engSinceChip');
  if (sinceChip) {
    if (cumPnl === null) {
      sinceChip.textContent = 'Net Return: —';
      sinceChip.style.color = 'var(--text2)';
    } else {
      const pct = (cumPnl / BOOK_START_EQUITY) * 100;
      sinceChip.textContent = `Net Return: ${fmtSignedMoney(cumPnl)} (${cumPnl >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
      sinceChip.style.color = cumPnl >= 0 ? 'var(--green)' : 'var(--red)';
    }
  }

  const heroLine = document.getElementById('engHeroLine');
  if (heroLine) {
    const se = (latest && latest.state_extra) || {};
    const bits = [];
    if (latest && latest.date) bits.push('snapshot ' + latest.date);
    if (num(se.peak) !== null) bits.push('peak ' + fmtMoney(se.peak));
    bits.push(_dailyRows.length + ' daily snapshots since ' + BOOK_START_LABEL);
    if (se.halted) bits.push('HALTED — drawdown rule hit');
    heroLine.textContent = bits.join(' · ');
    heroLine.style.color = se.halted ? 'var(--red)' : 'var(--text3)';
  }

  const label = document.getElementById('lastUpdatedLabel');
  if (label) {
    const ts = (_positions[0] && _positions[0].updated_at) || (latest && latest.inserted_at) || null;
    label.textContent = 'Last Sync: ' + (ts ? fmtUK(ts, true) : '—');
  }
}

function renderBookStats() {
  const latest = latestDaily();

  const openCount = _positions.length || (latest ? num(latest.n_open) : null);

  let gross = 0;
  let unreal = 0;
  let unrealKnown = false;
  for (const p of _positions) {
    const lastPx = num(p.last_px);
    const units = num(p.units);
    if (lastPx !== null && units !== null) gross += Math.abs(lastPx * units);
    const u = positionUpnl(p);
    if (u !== null) { unreal += u; unrealKnown = true; }
  }
  if (!_positions.length && latest && num(latest.gross_exposure_x) !== null && num(latest.equity) !== null) {
    gross = num(latest.gross_exposure_x) * num(latest.equity);
  }
  const equity = latest ? num(latest.equity) : null;
  const grossX = (equity !== null && equity > 0) ? gross / equity : null;

  setText('engOpenCount', openCount === null ? '—' : String(openCount));
  setText('engGross', gross > 0 ? fmtMoney(gross) : '—');
  const gxEl = document.getElementById('engGrossX');
  if (gxEl) gxEl.textContent = grossX === null ? 'of book equity' : grossX.toFixed(2) + 'x of book equity';
  setText('engUnreal', unrealKnown ? fmtSignedMoney(unreal) : '—', unrealKnown ? pnlClass(unreal) : '');

  const closed = closedTrades();
  const wins = closed.filter(t => num(t.pnl) > 0).length;
  const winRate = closed.length ? (wins / closed.length) * 100 : null;
  const realized = closed.reduce((s, t) => s + (num(t.pnl) || 0), 0);

  setText('engWinRate', winRate === null ? '—' : winRate.toFixed(1) + '%',
    winRate === null ? '' : (winRate >= 50 ? 'green' : 'red'));
  const ccEl = document.getElementById('engClosedCount');
  if (ccEl) ccEl.textContent = `${closed.length} closed`;
  setText('engRealized', closed.length ? fmtSignedMoney(realized) : '—', closed.length ? pnlClass(realized) : '');
}

// ── Equity curve ─────────────────────────────────────────────────────────────
function renderEquityChart() {
  const chartEl = document.getElementById('equityChart');
  const emptyEl = document.getElementById('equityChartEmpty');
  if (!chartEl) return;

  if (_eqChart) { try { _eqChart.remove(); } catch (e) {} _eqChart = null; }

  const pts = [];
  const seen = new Set();
  for (const r of _dailyRows) {
    const eq = num(r.equity);
    if (!r.date || eq === null || seen.has(r.date)) continue;
    seen.add(r.date);
    pts.push({ time: r.date, value: eq });
  }

  if (pts.length < 2 || typeof LightweightCharts === 'undefined') {
    chartEl.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'flex';
    return;
  }
  chartEl.style.display = '';
  if (emptyEl) emptyEl.style.display = 'none';

  const up = pts[pts.length - 1].value >= pts[0].value;
  const line = up ? '#34D399' : '#F87171';
  const chart = LightweightCharts.createChart(chartEl, {
    width: chartEl.clientWidth,
    height: chartEl.clientHeight || 300,
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
    localization: { priceFormatter: v => fmtMoney(v) },
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
  area.createPriceLine({ price: BOOK_START_EQUITY, color: 'rgba(56, 189, 248, 0.45)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'seed' });
  chart.timeScale().fitContent();
  if (_eqResize) window.removeEventListener('resize', _eqResize);
  _eqResize = () => {
    if (chartEl.isConnected) chart.applyOptions({ width: chartEl.clientWidth, height: chartEl.clientHeight || 300 });
  };
  window.addEventListener('resize', _eqResize);
  _eqChart = chart;
}

// ── Open positions grid ──────────────────────────────────────────────────────
// +1R partial/breakeven trigger: entry ± |entry − initial stop|, direction-aware.
function partialsInfo(entry, lastPx, isLong, cls, tmsP1, initStop, rawStop) {
  if (entry === null) return { targetTxt: '—', distTxt: '', color: 'var(--text3)' };

  let riskDist = (initStop !== null && Math.abs(entry - initStop) > 0.05)
    ? Math.abs(entry - initStop)
    : (rawStop !== null ? Math.abs(entry - rawStop) : 0);
  if (riskDist <= 0) return { targetTxt: '—', distTxt: '', color: 'var(--text3)' };

  const partialTarget = isLong ? entry + riskDist : entry - riskDist;
  const targetTxt = fmtPrice(partialTarget, cls);
  const isHit = (tmsP1 === true) || (lastPx !== null && (isLong ? lastPx >= partialTarget : lastPx <= partialTarget));
  if (isHit) return { targetTxt, distTxt: '(Hit ✅)', color: 'var(--green)' };

  if (lastPx !== null) {
    const dist = isLong ? partialTarget - lastPx : lastPx - partialTarget;
    if (dist <= 0) return { targetTxt, distTxt: '(Hit ✅)', color: 'var(--green)' };
    return { targetTxt, distTxt: `(${fmtPrice(dist, cls)} away)`, color: '#F5B04C' };
  }
  return { targetTxt, distTxt: '', color: '#F5B04C' };
}

function renderPositionCard(p) {
  const inst = String(p.instrument || '');
  const cls = paperClassFor(inst);
  const isLong = String(p.direction || '').toLowerCase() !== 'short';

  const units = num(p.units);
  const entry = num(p.entry_price);
  const stop = num(p.stop);
  const target = num(p.target);
  const lastPx = num(p.last_px);
  const upnl = positionUpnl(p);
  const upnlCls = upnl === null ? '' : (upnl > 0 ? 'pos' : (upnl < 0 ? 'neg' : ''));
  const pInfo = partialsInfo(entry, lastPx, isLong, cls, p.tms_p1 === true, num(p.initial_stop), stop);

  const enteredTxt = p.entry_time ? fmtDay(p.entry_time) : '—';
  const updated = p.updated_at ? fmtUK(p.updated_at) : '—';
  const bars = num(p.bars_open);
  const bankedPartials = num(p.realized_pnl_total);

  return `
    <div class="stat-item ibkr-pos-card eng-pos-card" data-instrument="${escHtml(inst)}" style="padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); display: flex; flex-direction: column; gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s;">
      <div class="card-face-stats">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
          <strong style="font-family: var(--mono); font-size: 17px; color: var(--text);">${escHtml(inst)}</strong>
          ${dirBadge(isLong)}
        </div>
        <span style="font-size: 11px; font-weight: 700; color: var(--text3); font-family: var(--mono);">${escHtml(fmtQty(units))} units</span>
      </div>

      <div style="font-size: 10.5px; color: var(--text3); font-family: var(--mono); margin-top: 2px;">Entered: ${escHtml(enteredTxt)}${bars !== null ? ` · ${bars} bar${bars === 1 ? '' : 's'} open` : ''}</div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; margin-top: 4px;">
        <span style="color: var(--text3)">Avg Entry</span>
        <span style="font-family: var(--mono); color: var(--text2);">${escHtml(fmtPrice(entry, cls))}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
        <span style="color: var(--text3)">Last Price</span>
        <span style="font-family: var(--mono); color: var(--text2); font-weight: 600;">${escHtml(fmtPrice(lastPx, cls))}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
        <span style="color: var(--text3)">Stop Loss</span>
        <span style="font-family: var(--mono); color: var(--red);">${escHtml(stop !== null ? fmtPrice(stop, cls) : '—')}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; white-space: nowrap;">
        <span style="color: var(--text3)">Partials (+1.0R)</span>
        <span style="font-family: var(--mono); color: ${pInfo.color}; font-weight: 600;">${escHtml(pInfo.targetTxt)} <span style="font-size:11px;font-weight:500;">${escHtml(pInfo.distTxt)}</span></span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
        <span style="color: var(--text3)">Take Profit</span>
        <span style="font-family: var(--mono); color: var(--green);">${escHtml(target !== null ? fmtPrice(target, cls) : '—')}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 15px; font-weight: 700; padding-top: 4px;">
        <span style="color: var(--text)">Unrealized P&amp;L</span>
        <span class="${upnlCls}" style="font-family: var(--mono); font-size: 16px;">${escHtml(upnl === null ? '—' : fmtSignedMoney(upnl))}</span>
      </div>

      ${bankedPartials !== null && bankedPartials !== 0 ? `
      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
        <span style="color: var(--text3)">Banked partials</span>
        <span class="${bankedPartials > 0 ? 'pos' : 'neg'}" style="font-family: var(--mono); font-size: 12px;">${escHtml(fmtSignedMoney(bankedPartials))}</span>
      </div>` : ''}

      <div style="font-size: 10.5px; color: var(--text3); margin-top: 6px; text-align: right; font-style: italic;">
        Engine book · ${escHtml(updated)}
      </div>
      </div>
    </div>
  `;
}

function renderPositions() {
  const wrap = document.getElementById('engPositionsWrap');
  if (!wrap) return;

  const latest = latestDaily();
  const noteEl = document.getElementById('engPosNote');
  if (noteEl) {
    noteEl.textContent = latest && latest.notes ? `last step: ${latest.notes}` : '';
  }

  const open = _positions.filter(p => p && p.instrument && num(p.units) > 0 && String(p.status || '').toLowerCase() !== 'closed');

  if (!open.length) {
    wrap.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">No open engine positions.</div>`;
    return;
  }

  wrap.innerHTML = open.map(renderPositionCard).join('');
}

// ── Closed trades table ──────────────────────────────────────────────────────
function renderClosedTrades() {
  const wrap = document.getElementById('engClosedWrap');
  if (!wrap) return;

  const closed = closedTrades();
  const noteEl = document.getElementById('engClosedNote');
  if (noteEl) noteEl.textContent = closed.length ? `${closed.length} round-trips` : '';

  if (!closed.length) {
    wrap.innerHTML = `<div style="text-align: center; padding: 30px; color: var(--text3); font-size: 14px; font-style: italic;">No closed engine trades yet.</div>`;
    return;
  }

  const rows = closed.map(t => {
    const inst = String(t.instrument || '');
    const cls = paperClassFor(inst);
    const isLong = String(t.direction || '').toLowerCase() !== 'short';
    const pnl = num(t.pnl);
    const retPct = num(t.return_pct); // fraction, e.g. -0.09538
    const reason = t.exit_reason ? String(t.exit_reason).toUpperCase() : '—';
    return `<tr class="wl-row">
      <td style="color: var(--text3); font-size: 12px; white-space: nowrap;">${escHtml(t.exit_time ? fmtDay(t.exit_time) : '—')}</td>
      <td><strong class="wl-sym">${escHtml(inst)}</strong></td>
      <td>${dirBadge(isLong)}</td>
      <td style="font-family: var(--mono);">${escHtml(fmtQty(t.units))}</td>
      <td style="font-family: var(--mono); color: var(--text2);">${escHtml(fmtPrice(t.entry_price, cls))}</td>
      <td style="font-family: var(--mono); color: var(--text); font-weight: 600;">${escHtml(fmtPrice(t.exit_price, cls))}</td>
      <td>${pnlBadge(pnl, retPct)}</td>
      <td><span style="font-size:10.5px;font-weight:700;padding:3px 8px;border-radius:4px;background:rgba(255,255,255,0.06);color:var(--text2);font-family:var(--mono);border:1px solid var(--border);">${escHtml(reason)}</span></td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `<div class="wl-table-wrap"><table class="wl-table">
    <thead><tr>
      <th>Exit Date</th><th>Instrument</th><th>Direction</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Realized P&amp;L</th><th>Exit Reason</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

// ── Render all ───────────────────────────────────────────────────────────────
function renderAll() {
  renderHero();
  renderBookStats();
  renderEquityChart();
  renderPositions();
  renderClosedTrades();
}

// ── Refresh / polling / realtime ─────────────────────────────────────────────
function initRefreshButton() {
  const btnRefresh = document.getElementById('btnRefresh');
  if (!btnRefresh) return;
  let rotation = 0;
  btnRefresh.addEventListener('click', async () => {
    const icon = document.getElementById('refreshIcon');
    const text = document.getElementById('refreshText');
    rotation += 360;
    if (icon) icon.style.transform = `rotate(${rotation}deg)`;
    if (text) text.textContent = 'Syncing...';

    btnRefresh.disabled = true;
    btnRefresh.style.opacity = '0.7';

    try {
      await loadEngineBook();
    } catch (e) {
      console.error('Refresh fetch error:', e);
    } finally {
      setTimeout(() => {
        btnRefresh.disabled = false;
        btnRefresh.style.opacity = '1';
        if (text) text.textContent = 'Refresh Book';
      }, 600);
    }
  });
}

let _pollIntervalId = null;
function startPolling(ms) {
  if (_pollIntervalId) clearInterval(_pollIntervalId);
  _pollIntervalId = setInterval(() => {
    try { loadEngineBook(); } catch (e) { console.error('Poll refresh error:', e); }
  }, ms);
}

// ── Supabase Realtime: push updates, no refresh ──────────────────────────────
// Subscribes to the engine paper tables; the nightly step's writes trigger a
// reload. 15-min polling stays as fallback. 5s debounce collapses the nightly
// write burst into one refresh (egress-conscious — same pattern as terminal).
const SUPA_RT_URL  = 'https://cuvchjhaojhmxfgczndy.supabase.co';
const SUPA_RT_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';
const RT_TABLES = ['apex_paper_positions', 'apex_paper_daily'];
let _rtDebounce = null;

function setLivePill(connected) {
  const pill = document.querySelector('.live-pill');
  if (!pill) return;
  pill.style.opacity = connected ? '1' : '0.45';
  pill.title = connected
    ? 'Realtime connected — changes push to this page instantly'
    : 'Realtime disconnected — 15-minute fallback polling active';
}

function initRealtime() {
  if (!window.supabase || !window.supabase.createClient) {
    setLivePill(false);
    return;
  }
  const client = window.supabase.createClient(SUPA_RT_URL, SUPA_RT_ANON);
  const trigger = () => {
    if (_rtDebounce) clearTimeout(_rtDebounce);
    _rtDebounce = setTimeout(() => {
      if (document.hidden) return;   // background tabs don't need to re-pull
      try { loadEngineBook(); } catch (e) { console.error('Realtime reload err:', e); }
    }, 5000);
  };
  const channel = client.channel('engine-book-live');
  for (const t of RT_TABLES) {
    channel.on('postgres_changes', { event: '*', schema: 'public', table: t }, trigger);
  }
  channel.subscribe((status) => {
    setLivePill(status === 'SUBSCRIBED');
    if (status === 'SUBSCRIBED') console.log('Realtime live — push updates active');
  });
}

function bootEngineBook() {
  try { initRefreshButton(); } catch (e) { console.error('Refresh btn err:', e); }
  try { initRealtime(); } catch (e) { console.error('Realtime err:', e); }

  // Initial load + slow 15-minute background fallback (Realtime is primary)
  try { loadEngineBook(); } catch (e) { console.error('Initial load err:', e); }
  startPolling(900000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootEngineBook);
} else {
  bootEngineBook();
}
