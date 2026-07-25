// ibkr-trades.js — IBKR paper terminal: overall account stats + per-asset-class tabs.
// Data comes from /api/ibkr (Supabase mirror pushed by engine/scripts/run_ibkr_mirror.py).
// Asset class is derived server-side; this file stays dumb.

let _ibkrClassFilter = 'stocks'; // 'forex' | 'stocks' | 'crypto'
let _ibkrAccountCache = {};
let _ibkrPositionsCache = [];
let _ibkrTradesCache = [];
let _ibkrPaperMap = {}; // instrument -> apex_paper_positions row (stop/target join source)

const CLASS_LABELS = { forex: 'forex', stocks: 'stock', crypto: 'crypto' };

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

function curSymbol() {
  const map = { 'GBP': '£', 'USD': '$', 'EUR': '€', 'CHF': 'CHF' };
  return map[_ibkrAccountCache.currency] || '$';
}

function fmtMoney(v, sym) {
  const n = num(v);
  if (n === null) return '—';
  const sign = n < 0 ? '-' : '';
  return sign + sym + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtSignedMoney(v, sym) {
  const n = num(v);
  if (n === null) return '—';
  return (n >= 0 ? '+' : '-') + sym + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPrice(v, assetClass) {
  const n = num(v);
  if (n === null) return '—';
  if (assetClass === 'forex') return n.toFixed(5);
  if (assetClass === 'crypto') return n >= 100 ? n.toFixed(2) : n.toFixed(4);
  return n.toFixed(2);
}

function fmtQty(v) {
  const n = num(v);
  if (n === null) return '—';
  return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

// Every timestamp on this page is shown in UK time (Europe/London), explicitly
// labeled "UK". Supabase stores UTC ISO strings — this is the single conversion
// point. Crypto is 24/7 ("BTC time"), so no session conversion is ever needed.
const UK_TZ = 'Europe/London';
function fmtUK(ts, withSeconds) {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '—';
  const opts = { timeZone: UK_TZ, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false };
  if (withSeconds) opts.second = '2-digit';
  return d.toLocaleString('en-GB', opts) + ' UK';
}

function pnlClass(v) {
  const n = num(v);
  if (n === null || n === 0) return '';
  return n > 0 ? 'green' : 'red';
}

function setText(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  if (cls !== undefined) el.className = 'hs-val' + (cls ? ' ' + cls : '');
}

// ── Data loading ─────────────────────────────────────────────────────────────
async function loadIbkr() {
  try {
    const [accountRes, positionsRes, tradesRes, paperRes] = await Promise.all([
      fetch('/api/ibkr?view=account'),
      fetch('/api/ibkr?view=positions'),
      fetch('/api/ibkr?view=trades&limit=50'),
      fetch('/api/paper?table=positions&limit=100').catch(() => null),
    ]);

    if (!accountRes.ok || !positionsRes.ok || !tradesRes.ok) {
      // Name the ACTUAL cause. A 402 is Supabase refusing to serve the project
      // (free-tier egress/quota restriction) — nothing to do with IB Gateway, and
      // saying "IBKR bridge" sent us hunting the wrong system for an hour.
      const status = [accountRes, positionsRes, tradesRes].find(r => !r.ok).status;
      if (status === 402) {
        const err = new Error('Database quota exceeded — Supabase has paused this project, '
          + 'so no data can be read. IBKR itself is fine. Restore service in the Supabase '
          + 'dashboard (upgrade plan or raise the spend cap), or wait for the quota to reset.');
        err.kind = 'quota';
        throw err;
      }
      throw new Error(`Failed to load terminal data (HTTP ${status})`);
    }

    _ibkrAccountCache = await accountRes.json();
    _ibkrPositionsCache = await positionsRes.json();
    _ibkrTradesCache = await tradesRes.json();

    // Stop/target join source: the engine paper book (apex_paper_positions)
    // carries live stops/targets per instrument; the IBKR mirror does not.
    // Tolerate failure — cards simply show '—' for stop/target.
    _ibkrPaperMap = {};
    if (paperRes && paperRes.ok) {
      try {
        const rows = await paperRes.json();
        if (Array.isArray(rows)) {
          for (const r of rows) {
            if (r && r.instrument) _ibkrPaperMap[String(r.instrument)] = r;
          }
        }
      } catch (e) {
        console.warn('Paper positions parse failed:', e);
      }
    }

    updateScoreboard();
    renderClassTab();
  } catch (e) {
    console.error('Error fetching IBKR data:', e);
    const isQuota = e && e.kind === 'quota';
    const title = isQuota ? '⏸ Data paused — database quota' : 'Error syncing with IBKR bridge';
    const colour = isQuota ? 'var(--amber, #E4B23F)' : 'var(--red)';
    const msg = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: ${colour}; font-size: 14px; line-height: 1.6;">`
      + `<strong>${escHtml(title)}</strong><br>${escHtml(e.message || e)}</div>`;
    const pw = document.getElementById('ibkrPositionsWrap');
    const tw = document.getElementById('ibkrTradesWrap');
    if (pw) pw.innerHTML = msg;
    if (tw) tw.innerHTML = msg;
  }
}

// ── Overall account scoreboard ───────────────────────────────────────────────
function updateScoreboard() {
  const a = _ibkrAccountCache || {};
  const sym = curSymbol();

  setText('statNetLiq', fmtMoney(a.net_liquidation, sym));
  setText('statCash', fmtMoney(a.cash, sym));
  setText('statBuyingPower', fmtMoney(a.buying_power, sym));
  setText('statDailyPnl', fmtSignedMoney(a.daily_pnl, sym), pnlClass(a.daily_pnl));
  setText('statUnrealizedPnl', fmtSignedMoney(a.unrealized_pnl, sym), pnlClass(a.unrealized_pnl));
  setText('statRealizedPnl', fmtSignedMoney(a.realized_pnl, sym), pnlClass(a.realized_pnl));
  setText('statOpenCount', String(_ibkrPositionsCache.length));

  // Hero chips: today + total since the paper test started (account began at £1m on 17 Jul)
  const dayV = num(a.daily_pnl);
  const netV = num(a.net_liquidation);
  const dayChip = document.getElementById('heroDayChip');
  if (dayChip) {
    dayChip.textContent = 'Today: ' + (dayV === null ? '—' : fmtSignedMoney(a.daily_pnl, sym));
    dayChip.style.color = dayV === null ? 'var(--text3)' : (dayV >= 0 ? 'var(--green)' : 'var(--red)');
  }
  const sinceChip = document.getElementById('heroSinceChip');
  if (sinceChip) {
    if (netV === null) {
      sinceChip.textContent = 'Since 17 Jul: —';
      sinceChip.style.color = 'var(--text3)';
    } else {
      const since = netV - 1000000;
      const pct = (since / 1000000) * 100;
      const abs = Math.abs(since).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      sinceChip.textContent = `Since 17 Jul: ${since >= 0 ? '+' : '-'}${sym}${abs} (${since >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
      sinceChip.style.color = since >= 0 ? 'var(--green)' : 'var(--red)';
    }
  }

  const label = document.getElementById('lastUpdatedLabel');
  if (label) {
    if (a.updated_at) {
      const lastUpdate = new Date(a.updated_at);
      if (!isNaN(lastUpdate.getTime())) {
        const timeStr = lastUpdate.toLocaleTimeString('en-GB', { timeZone: UK_TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        label.textContent = 'Last Sync: ' + timeStr + ' UK';
      }
    } else {
      label.textContent = 'Last Sync: —';
    }
  }
}

// ── Closed-trade stats: FIFO round-trip matching per instrument ──────────────
// The trades table stores raw fills (no per-fill P&L), so closed win rate is
// computed by matching each fill against the instrument's open lots (FIFO).
// A fill that closes one or more lots counts as one closed trade.
function computeClosedStats(trades) {
  const sorted = [...trades].sort((a, b) => new Date(a.exec_time) - new Date(b.exec_time));
  const openLots = {}; // instrument -> [{ qty (signed), price }]
  let closedCount = 0;
  let wins = 0;

  for (const t of sorted) {
    const inst = t.instrument;
    const price = num(t.price);
    const qty = num(t.qty);
    if (!inst || price === null || qty === null || qty <= 0) continue;

    let remaining = (String(t.side).toUpperCase() === 'SELL' ? -1 : 1) * qty;
    const lots = openLots[inst] || (openLots[inst] = []);
    let realized = 0;
    let matched = false;

    while (remaining !== 0 && lots.length > 0 && Math.sign(lots[0].qty) !== Math.sign(remaining)) {
      const lot = lots[0];
      const closeQty = Math.min(Math.abs(remaining), Math.abs(lot.qty));
      realized += (price - lot.price) * closeQty * (lot.qty > 0 ? 1 : -1);
      matched = true;
      lot.qty -= Math.sign(lot.qty) * closeQty;
      remaining -= Math.sign(remaining) * closeQty;
      if (Math.abs(lot.qty) < 1e-12) lots.shift();
    }
    if (remaining !== 0) lots.push({ qty: remaining, price });

    if (matched) {
      closedCount++;
      if (realized > 0) wins++;
    }
  }

  return { closedCount, wins, winRate: closedCount > 0 ? (wins / closedCount) * 100 : null };
}

// ── Per-class tab rendering ──────────────────────────────────────────────────
function classPositions() {
  return _ibkrPositionsCache.filter(p => p.asset_class === _ibkrClassFilter);
}

function classTrades() {
  return _ibkrTradesCache.filter(t => t.asset_class === _ibkrClassFilter);
}

function renderClassTab() {
  const sym = curSymbol();
  const cls = _ibkrClassFilter;
  const positions = classPositions();
  const trades = classTrades();

  // Stats
  const gross = positions.reduce((s, p) => s + Math.abs(num(p.market_value) || 0), 0);
  const unreal = positions.reduce((s, p) => s + (num(p.unrealized_pnl) || 0), 0);
  const hasUnreal = positions.some(p => num(p.unrealized_pnl) !== null);
  const closed = computeClosedStats(trades);

  setText('clsOpenCount', String(positions.length));
  setText('clsGrossExposure', positions.length ? fmtMoney(gross, sym) : '—');
  setText('clsUnrealizedPnl', hasUnreal ? fmtSignedMoney(unreal, sym) : '—', hasUnreal ? pnlClass(unreal) : '');
  setText('clsDailyPnl', '—'); // per-class day P&L is not reported by the sync
  setText('clsWinRate', closed.winRate !== null ? closed.winRate.toFixed(1) + '%' : '—',
    closed.winRate !== null ? (closed.winRate >= 50 ? 'green' : 'red') : '');
  const ccEl = document.getElementById('clsClosedCount');
  if (ccEl) ccEl.textContent = `${closed.closedCount} closed`;

  renderPositionsCards(positions, cls);
  renderTradesTable(trades, cls);
}

// ── Open positions: MT4-style per-position cards ─────────────────────────────
function renderPositionsCards(positions, cls) {
  const wrap = document.getElementById('ibkrPositionsWrap');
  if (!wrap) return;
  destroyChart(); // chart instance must die before innerHTML wipes its canvas

  if (!positions.length) {
    _openChartInst = null; // no cards to restore onto
    wrap.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">No ${escHtml(CLASS_LABELS[cls] || cls)} positions yet.</div>`;
    return;
  }

  const sym = curSymbol();
  // Gross book = sum of every position's absolute notional; each card shows its
  // own slice of it so "how much is open right now" reads off the card directly.
  const gross = positions.reduce((s, p) => s + Math.abs(num(p.market_value) || 0), 0);
  wrap.innerHTML = positions.map(p => renderPositionCard(p, cls, sym, gross)).join('');
  restoreOpenChart(); // re-expand the open chart (if any) after background refreshes
}

function renderPositionCard(p, cls, sym, gross) {
  const dir = String(p.direction || '').toLowerCase();
  const isLong = dir !== 'short';
  const dirBadge = isLong
    ? '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">LONG</span>'
    : '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">SHORT</span>';

  const units = num(p.units);
  const mv = num(p.market_value);
  // Shorts carry a NEGATIVE market_value at IBKR — always display the absolute
  // notional, and derive the current mark as |market_value| / |units|.
  const absMv = mv === null ? null : Math.abs(mv);
  const curPx = (mv !== null && units) ? Math.abs(mv) / Math.abs(units) : null;

  // Share of the class book this position accounts for, so the card answers
  // "how much of my open exposure is this one trade?" at a glance.
  const sharePct = (absMv !== null && gross > 0)
    ? ` <span style="font-size:11px;color:var(--text3);font-weight:400;">(${((absMv / gross) * 100).toFixed(0)}% of book)</span>`
    : '';

  // Two distinct readouts on the card:
  //   • Profit / Loss Now — the actual money you're up/down right now. The data's
  //     sign is already direction-correct for longs and shorts, so show as-is.
  //   • Price Move — how far the PRICE itself has travelled from your avg entry,
  //     coloured by whether that move helps (green) or hurts (red) the position,
  //     so a short that profits on a falling price still reads green.
  const upnl = num(p.unrealized_pnl);
  const upnlCls = upnl === null ? '' : (upnl > 0 ? 'pos' : (upnl < 0 ? 'neg' : ''));

  const entryPx = num(p.avg_price);
  const priceDelta = (curPx !== null && entryPx !== null) ? curPx - entryPx : null;
  const priceDeltaPct = (priceDelta !== null && entryPx) ? (priceDelta / entryPx) * 100 : null;
  const moveAgainst = priceDelta === null ? 0 : (isLong ? priceDelta : -priceDelta); // >0 helps, <0 hurts
  const moveColor = moveAgainst > 0 ? 'var(--green)' : (moveAgainst < 0 ? 'var(--red)' : 'var(--text2)');
  const priceMoveTxt = priceDelta === null ? '—'
    : (priceDelta >= 0 ? '+' : '-') + fmtPrice(Math.abs(priceDelta), cls)
      + (priceDeltaPct === null ? '' : ` (${priceDeltaPct >= 0 ? '+' : '-'}${Math.abs(priceDeltaPct).toFixed(2)}%)`);

  // Stop/target join: apex_paper_positions keyed by instrument, when present.
  const pp = (p.instrument && _ibkrPaperMap[String(p.instrument)]) || null;
  const stopTxt = pp && num(pp.stop) !== null ? fmtPrice(pp.stop, cls) : '—';
  const targetTxt = pp && num(pp.target) !== null ? fmtPrice(pp.target, cls) : '—';

  const updated = p.updated_at ? fmtUK(p.updated_at) : '—';

  return `
    <div class="stat-item ibkr-pos-card" data-instrument="${escHtml(p.instrument || '')}" style="padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); display: flex; flex-direction: column; gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s, height 0.3s cubic-bezier(0.16, 1, 0.3, 1);">
      <div class="card-face-stats">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
          <strong style="font-family: var(--mono); font-size: 17px; color: var(--text);">${escHtml(p.instrument)}</strong>
          ${dirBadge}
        </div>
        <div style="display: flex; align-items: center; gap: 10px;">
          <button class="ibkr-chart-btn" data-chart-inst="${escHtml(p.instrument || '')}" title="Daily chart with trade levels">CHART</button>
          <span style="font-size: 11px; font-weight: 700; color: var(--text3); font-family: var(--mono);">${escHtml(fmtQty(units))} units</span>
        </div>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; margin-top: 4px;">
        <span style="color: var(--text3)">Avg Entry</span>
        <span style="font-family: var(--mono); color: var(--text2);">${escHtml(fmtPrice(p.avg_price, cls))}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
        <span style="color: var(--text3)">Current Price</span>
        <span style="font-family: var(--mono); color: var(--text2); font-weight: 600;">${curPx === null ? '—' : escHtml(fmtPrice(curPx, cls))}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
        <span style="color: var(--text3)">Price Move</span>
        <span style="font-family: var(--mono); color: ${moveColor}; font-weight: 600;">${escHtml(priceMoveTxt)}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
        <span style="color: var(--text3)">Open Now</span>
        <span style="font-family: var(--mono); color: var(--text); font-weight: 600;">${escHtml(fmtMoney(absMv, sym))}${sharePct}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px;">
        <span style="color: var(--text3)">Stop Loss</span>
        <span style="font-family: var(--mono); color: var(--red);">${escHtml(stopTxt)}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
        <span style="color: var(--text3)">Take Profit</span>
        <span style="font-family: var(--mono); color: var(--green);">${escHtml(targetTxt)}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 15px; font-weight: 700; padding-top: 4px;">
        <span style="color: var(--text)">Profit / Loss Now</span>
        <span class="${upnlCls}" style="font-family: var(--mono); font-size: 16px;">${escHtml(upnl === null ? '—' : fmtSignedMoney(upnl, sym))}</span>
      </div>

      <div style="font-size: 10.5px; color: var(--text3); margin-top: 6px; text-align: right; font-style: italic;">
        Updated: ${escHtml(updated)}
      </div>
      </div>
    </div>
  `;
}

// ── Per-position chart face (two-face card) ──────────────────────────────────
// CHART on a position card swaps the card's content IN PLACE: the stats face
// (.card-face-stats) hides and a chart face (.card-face-chart) takes the same
// footprint — compact header (instrument + direction + live P&L), level chips,
// and a daily-candle chart filling the rest of the card. The button flips to
// CARD to swap back. One chart-mode card at a time. Level lines: entry, stop,
// target, and the +1R partial/breakeven trigger (direction-aware:
// entry ± |entry − stop|). Chart view survives background refreshes:
// renderPositionsCards destroys the canvas, then restoreOpenChart rebuilds the
// face from cached bars with fresh levels — no refetch.
const CLASS_TO_CANDLE_TYPE = { stocks: 'Stock', forex: 'Forex', crypto: 'Crypto' };
const LEVEL_COLORS = { entry: '#8EA3C8', stop: '#F87171', target: '#34D399', oneR: '#F5B04C' };
let _openChartInst = null;  // instrument currently in chart view
let _chartObj = null;       // live LightweightCharts instance
let _chartRO = null;        // ResizeObserver tracking the chart box
const _chartBarsCache = {}; // `${cls}|${inst}` -> daily bars

function destroyChart() {
  if (_chartRO) { try { _chartRO.disconnect(); } catch (e) {} _chartRO = null; }
  if (_chartObj) { try { _chartObj.remove(); } catch (e) {} _chartObj = null; }
  window._ibkrChart = null;
  window._ibkrSeries = null;
}

function closePositionChart() {
  const wrap = document.getElementById('ibkrPositionsWrap');
  destroyChart();
  _openChartInst = null;
  if (!wrap) return;
  // Flip every chart-mode card (normally just one) back to its stats face.
  for (const card of wrap.querySelectorAll('.ibkr-pos-card.chart-mode')) {
    card.classList.remove('chart-mode');
    const face = card.querySelector('.card-face-chart');
    if (face) face.remove();
    const stats = card.querySelector('.card-face-stats');
    if (stats) {
      stats.classList.add('face-enter'); // quick fade back in
      setTimeout(() => { if (stats.isConnected) stats.classList.remove('face-enter'); }, 220);
    }
  }
}

function restoreOpenChart() {
  if (!_openChartInst) return;
  const wrap = document.getElementById('ibkrPositionsWrap');
  const btn = wrap && wrap.querySelector(`.ibkr-chart-btn[data-chart-inst="${CSS.escape(_openChartInst)}"]`);
  if (!btn) { _openChartInst = null; return; } // instrument closed or tab switched
  togglePositionChart(_openChartInst, btn);
}

// Engine store-slug form (GBP_JPY, SOL_USD) -> slash form the candles API maps.
function normalizeChartSymbol(inst, cls) {
  if ((cls === 'forex' || cls === 'crypto') && /^[A-Za-z]{2,6}_[A-Za-z]{2,6}$/.test(inst)) {
    return inst.replace('_', '/');
  }
  return inst;
}

async function fetchDailyBars(inst, cls) {
  const key = cls + '|' + inst;
  if (_chartBarsCache[key]) return _chartBarsCache[key];
  const to = Math.floor(Date.now() / 1000);
  const from = to - 200 * 86400; // 90 trading days need ~130+ calendar days; 200 is safe
  const type = CLASS_TO_CANDLE_TYPE[cls] || 'Stock';
  const res = await fetch(`/api/candles?sym=${encodeURIComponent(normalizeChartSymbol(inst, cls))}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const bars = await res.json();
  if (!Array.isArray(bars) || bars.length < 2) return [];
  const out = bars.slice(-90).map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }));
  _chartBarsCache[key] = out;
  return out;
}

function togglePositionChart(inst, btn) {
  const wrap = document.getElementById('ibkrPositionsWrap');
  if (!wrap) return;
  const card = btn.closest('.ibkr-pos-card');
  if (!card) return;
  const wasChartMode = card.classList.contains('chart-mode');
  closePositionChart(); // accordion: flip any chart-mode card back to stats
  if (wasChartMode) return; // CARD button on the chart face = swap back to stats

  const p = _ibkrPositionsCache.find(x => String(x.instrument) === inst && x.asset_class === _ibkrClassFilter);
  if (!p) return;

  _openChartInst = inst;

  const cls = _ibkrClassFilter;
  const isLong = String(p.direction || '').toLowerCase() !== 'short';
  const entry = num(p.avg_price);
  const pp = (p.instrument && _ibkrPaperMap[String(p.instrument)]) || null;
  const stop = pp ? num(pp.stop) : null;
  const target = pp ? num(pp.target) : null;
  // +1R partial/breakeven trigger: one initial-risk unit past entry, direction-aware
  const oneR = (entry !== null && stop !== null)
    ? (isLong ? entry + Math.abs(entry - stop) : entry - Math.abs(entry - stop))
    : null;

  const sym = curSymbol();
  const upnl = num(p.unrealized_pnl);
  const upnlCls = upnl === null ? '' : (upnl > 0 ? 'pos' : (upnl < 0 ? 'neg' : ''));
  const dirBadge = isLong
    ? '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">LONG</span>'
    : '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">SHORT</span>';

  const chips = [];
  if (entry !== null) chips.push({ c: LEVEL_COLORS.entry, label: `Entry ${fmtPrice(entry, cls)}` });
  if (stop !== null) chips.push({ c: LEVEL_COLORS.stop, label: `Stop ${fmtPrice(stop, cls)}` });
  if (target !== null) chips.push({ c: LEVEL_COLORS.target, label: `Target ${fmtPrice(target, cls)}` });
  if (oneR !== null) chips.push({ c: LEVEL_COLORS.oneR, label: `+1R ${fmtPrice(oneR, cls)}`, dashed: true });

  const face = document.createElement('div');
  face.className = 'card-face-chart';
  face.innerHTML = `
    <div class="cf-head">
      <div class="cf-id">
        <strong class="cf-sym">${escHtml(inst)}</strong>
        ${dirBadge}
      </div>
      <span class="cf-pnl ${upnlCls}" title="Unrealized P&amp;L">${escHtml(upnl === null ? '—' : fmtSignedMoney(upnl, sym))}</span>
      <button class="ibkr-chart-btn" data-chart-inst="${escHtml(inst)}" title="Back to position card">CARD</button>
    </div>
    <div class="ibkr-chart-legend">${chips.map(ch => `<span class="ibkr-chart-chip${ch.dashed ? ' dashed' : ''}" style="--chip-color: ${ch.c}">${escHtml(ch.label)}</span>`).join('')}</div>
    <div class="ibkr-chart-box"></div>
    <div class="ibkr-chart-msg">Loading chart…</div>`;
  card.classList.add('chart-mode'); // hides .card-face-stats via CSS
  card.appendChild(face);

  const box = face.querySelector('.ibkr-chart-box');
  const msg = face.querySelector('.ibkr-chart-msg');
  const fail = text => { msg.textContent = text; msg.classList.add('err'); box.remove(); };

  if (typeof LightweightCharts === 'undefined') { fail('Chart library failed to load (CDN unreachable).'); return; }

  (async () => {
    let bars;
    try {
      bars = await fetchDailyBars(inst, cls);
    } catch (e) {
      if (face.isConnected) fail(`Chart data failed for ${inst} (${e.message || e}).`);
      return;
    }
    if (!face.isConnected) return; // swapped back or tab switched mid-fetch
    if (!bars.length) { fail(`No daily chart data available for ${inst}.`); return; }
    msg.remove();

    const refPx = entry !== null ? entry : bars[bars.length - 1].close;
    const precision = cls === 'forex' ? 5 : (refPx >= 100 ? 2 : 4);
    const minMove = cls === 'forex' ? 0.00001 : (refPx >= 100 ? 0.01 : 0.0001);

    const chart = LightweightCharts.createChart(box, {
      width: box.clientWidth,
      height: Math.max(box.clientHeight, 160), // box flex-fills the locked card
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#64748B',
        fontSize: 10,
        fontFamily: "'Space Mono', monospace",
      },
      grid: { horzLines: { color: 'rgba(51, 65, 85, 0.28)' }, vertLines: { visible: false } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, rightOffset: 2 },
      crosshair: {
        vertLine: { color: 'rgba(56, 189, 248, 0.3)' },
        horzLine: { color: 'rgba(56, 189, 248, 0.3)' },
      },
      localization: { priceFormatter: v => fmtPrice(v, cls) },
    });
    _chartObj = chart;

    const series = chart.addCandlestickSeries({
      upColor: '#34D399', downColor: '#F87171',
      wickUpColor: 'rgba(52, 211, 153, 0.7)', wickDownColor: 'rgba(248, 113, 113, 0.7)',
      borderVisible: false,
      priceFormat: { type: 'price', precision, minMove },
    });
    series.setData(bars);

    // Level lines carry NO axis titles — the chips row above the chart already
    // names + prices each level; the axis keeps only its price pills.
    const LS = LightweightCharts.LineStyle;
    const mkLine = (price, color, style) =>
      series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title: '' });
    if (entry !== null) mkLine(entry, LEVEL_COLORS.entry, LS.Solid);
    if (stop !== null) mkLine(stop, LEVEL_COLORS.stop, LS.Solid);
    if (target !== null) mkLine(target, LEVEL_COLORS.target, LS.Solid);
    if (oneR !== null) mkLine(oneR, LEVEL_COLORS.oneR, LS.Dashed);

    // Autoscale fits candle data only — a stop/target beyond the bars' range
    // would be drawn off-screen. Anchor the price scale to the level extremes
    // with two FLAT transparent lines (one at the padded low, one at the
    // padded high) so the extremes hold in ANY visible time window — a single
    // diagonal two-point anchor only touches the extremes at its endpoints
    // and lets far-away levels clip once the view is narrowed.
    const lvls = [entry, stop, target, oneR].filter(v => v !== null);
    if (lvls.length) {
      const span = Math.max(...lvls) - Math.min(...lvls);
      const pad = span > 0 ? span * 0.02 : Math.abs(lvls[0]) * 0.005 || 1; // hug the levels — the 330px card needs the vertical room
      const t0 = bars[0].time, tN = bars[bars.length - 1].time;
      for (const v of [Math.min(...lvls) - pad, Math.max(...lvls) + pad]) {
        const anchor = chart.addLineSeries({
          color: 'rgba(0, 0, 0, 0)', lineWidth: 1,
          crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
        });
        anchor.setData([{ time: t0, value: v }, { time: tN, value: v }]);
      }
    }

    // Price scale margins kept small so the content vertically fills the
    // compact card — just enough air that no line glues to the frame edge.
    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.05, bottom: 0.05 } });

    // Open on the recent ~58 bars so candles read at a comfortable size and
    // the four levels sit clearly separated; the full 90-bar series stays
    // scrollable — drag/scroll left for older bars.
    const ts = chart.timeScale();
    if (bars.length > 60) {
      ts.setVisibleLogicalRange({ from: bars.length - 58, to: bars.length + 1 });
    } else {
      ts.fitContent();
    }

    window._ibkrChart = chart; // debug/verification hook (nulled on close)
    window._ibkrSeries = series;

    // ── Left-edge level labels ───────────────────────────────────────────────
    // lightweight-charts has no left-side price-line labels, so overlay one
    // tiny pill per level inside the box: y from series.priceToCoordinate,
    // hidden when the line leaves the visible price range (no clamping into a
    // pile at the edge). Repositioned on scroll/zoom (visible-range
    // subscription) and on the box ResizeObserver below.
    const lvlDefs = [
      { price: entry, color: LEVEL_COLORS.entry, tag: 'ENTRY' },
      { price: stop, color: LEVEL_COLORS.stop, tag: 'SL' },
      { price: target, color: LEVEL_COLORS.target, tag: 'TP' },
      { price: oneR, color: LEVEL_COLORS.oneR, tag: '+1R' },
    ].filter(d => d.price !== null);
    const lvlLabels = lvlDefs.map(d => {
      const el = document.createElement('div');
      el.className = 'ibkr-lvl-label';
      el.textContent = `${d.tag} ${fmtPrice(d.price, cls)}`;
      el.style.color = d.color;
      box.appendChild(el);
      return { el, price: d.price };
    });
    const positionLevelLabels = () => {
      if (!box.isConnected) return;
      const bottomBound = box.clientHeight - 28; // pane bottom ≈ box minus the time-axis strip
      for (const { el, price } of lvlLabels) {
        const y = series.priceToCoordinate(price);
        if (y === null || y < 2 || y > bottomBound) { el.style.display = 'none'; continue; }
        el.style.display = '';
        el.style.top = `${Math.round(y)}px`;
      }
    };
    positionLevelLabels();
    requestAnimationFrame(positionLevelLabels);
    // The visible-range event fires BEFORE the chart recomputes autoscale, so
    // reading priceToCoordinate synchronously yields stale coordinates — defer
    // the reposition two frames (the chart repaints on its own frame first).
    ts.subscribeVisibleLogicalRangeChange(() =>
      requestAnimationFrame(() => requestAnimationFrame(positionLevelLabels)));

    _chartRO = new ResizeObserver(() => {
      if (_chartObj && box.isConnected) {
        _chartObj.applyOptions({ width: box.clientWidth, height: Math.max(box.clientHeight, 160) });
        positionLevelLabels();
      }
    });
    _chartRO.observe(box);
  })();
}

function initChartAccordion() {
  const wrap = document.getElementById('ibkrPositionsWrap');
  if (!wrap) return;
  wrap.addEventListener('click', (e) => {
    const btn = e.target.closest('.ibkr-chart-btn');
    if (btn && btn.dataset.chartInst) togglePositionChart(btn.dataset.chartInst, btn);
  });
}

function renderTradesTable(trades, cls) {
  const wrap = document.getElementById('ibkrTradesWrap');
  if (!wrap) return;

  if (!trades.length) {
    wrap.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">No ${escHtml(CLASS_LABELS[cls] || cls)} trades synced yet.</div>`;
    return;
  }

  const sym = curSymbol();
  const sorted = [...trades].sort((a, b) => new Date(b.exec_time) - new Date(a.exec_time));
  const rows = sorted.map(t => {
    const side = String(t.side || '').toUpperCase();
    const sideBadge = side === 'BUY'
      ? '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">BUY</span>'
      : '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">SELL</span>';
    const comm = num(t.commission);
    const when = t.exec_time ? fmtUK(t.exec_time) : '—';
    return `<tr class="wl-row">
      <td style="color: var(--text3); font-size: 12px; white-space: nowrap;">${escHtml(when)}</td>
      <td><span class="wl-sym">${escHtml(t.instrument)}</span></td>
      <td>${sideBadge}</td>
      <td style="font-family: var(--mono);">${escHtml(fmtQty(t.qty))}</td>
      <td style="font-family: var(--mono);">${escHtml(fmtPrice(t.price, cls))}</td>
      <td style="font-family: var(--mono);">${comm === null ? '—' : escHtml(fmtMoney(comm, sym))}</td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `<div class="wl-table-wrap"><table class="wl-table">
    <thead><tr>
      <th>Time</th><th>Instrument</th><th>Side</th><th>Qty</th><th>Price</th><th>Commission</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

// ── Tabs / refresh / polling ─────────────────────────────────────────────────
function initIbkrTabs() {
  const btns = [
    document.getElementById('btnForex'),
    document.getElementById('btnStocks'),
    document.getElementById('btnCrypto'),
  ].filter(Boolean);
  if (!btns.length) return;

  for (const btn of btns) {
    btn.addEventListener('click', () => {
      for (const b of btns) b.classList.remove('active');
      btn.classList.add('active');
      _ibkrClassFilter = btn.dataset.class;
      renderClassTab();
    });
  }
}

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
      await loadIbkr();
    } catch (e) {
      console.error('Refresh fetch error:', e);
    } finally {
      setTimeout(() => {
        btnRefresh.disabled = false;
        btnRefresh.style.opacity = '1';
        if (text) text.textContent = 'Refresh Terminal';
      }, 600);
    }
  });
}

let _pollIntervalId = null;

function startPolling(ms) {
  if (_pollIntervalId) clearInterval(_pollIntervalId);
  _pollIntervalId = setInterval(() => {
    try { loadIbkr(); } catch (e) { console.error('Poll refresh error:', e); }
  }, ms);
}

// ── Supabase Realtime: push updates, no refresh ──────────────────────────────
// Subscribes to the live-trading tables; any sync/fill/step that writes a row
// triggers an instant reload of the terminal. 15-min polling stays as fallback.
const SUPA_RT_URL  = 'https://cuvchjhaojhmxfgczndy.supabase.co';
const SUPA_RT_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';
const RT_TABLES = ['apex_ibkr_account', 'apex_ibkr_positions', 'apex_ibkr_trades',
                   'apex_paper_positions', 'apex_paper_daily'];
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
  // Debounce 400ms -> 5s (2026-07-22): every realtime row-change triggered a FULL
  // four-endpoint reload, so one nightly run — which writes hundreds of rows across
  // five subscribed tables — fanned out into a reload storm. That was the main
  // driver of the egress overrun that got the project restricted. A 5s window
  // collapses a whole write burst into a single refresh; the page still feels live.
  const trigger = () => {
    if (_rtDebounce) clearTimeout(_rtDebounce);
    _rtDebounce = setTimeout(() => {
      if (document.hidden) return;   // background tabs don't need to re-pull
      try { loadIbkr(); } catch (e) { console.error('Realtime reload err:', e); }
    }, 5000);
  };
  const channel = client.channel('ibkr-live');
  for (const t of RT_TABLES) {
    channel.on('postgres_changes', { event: '*', schema: 'public', table: t }, trigger);
  }
  channel.subscribe((status) => {
    setLivePill(status === 'SUBSCRIBED');
    if (status === 'SUBSCRIBED') console.log('Realtime live — push updates active');
  });
}

document.addEventListener('DOMContentLoaded', () => {
  try { initPulse(); } catch (e) { console.error('Pulse err:', e); }
  try { initIbkrTabs(); } catch (e) { console.error('Tabs err:', e); }
  try { initChartAccordion(); } catch (e) { console.error('Chart accordion err:', e); }
  try { initRefreshButton(); } catch (e) { console.error('Refresh btn err:', e); }
  try { initRealtime(); } catch (e) { console.error('Realtime err:', e); }

  // Initial load + slow 15-minute background fallback (Realtime is primary)
  try { loadIbkr(); } catch (e) { console.error('Initial load err:', e); }
  startPolling(900000);
});

// ── Market Pulse Header ──────────────────────────────────────────────────────
async function loadPulse(sym, type, elId) {
  const elements = document.getElementsByClassName(elId);
  if (!elements.length) return;

  const cached = localStorage.getItem('pulse_cache_' + sym);
  if (cached) {
    try {
      const data = JSON.parse(cached);
      for (let el of elements) {
        el.querySelector('.pulse-price').textContent = data.price;
        const ce = el.querySelector('.pulse-change');
        ce.textContent = data.change;
        ce.className = `pulse-change ${data.isUp ? 'up' : 'down'}`;
      }
    } catch {}
  }

  try {
    const to = Math.floor(Date.now() / 1000);
    const from = to - 7 * 86400;
    const r = await fetch(`/api/candles?sym=${encodeURIComponent(sym)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
    if (!r.ok) return;
    const bars = await r.json();
    if (!Array.isArray(bars) || bars.length < 2) return;

    const curr = bars[bars.length - 1].close, prev = bars[bars.length - 2].close;
    const pct = (curr - prev) / prev * 100;

    const formattedPrice = type === 'Forex' ? curr.toFixed(5) : curr >= 100 ? curr.toFixed(2) : curr.toFixed(4);
    const formattedChange = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
    const isUp = pct >= 0;

    localStorage.setItem('pulse_cache_' + sym, JSON.stringify({
      price: formattedPrice,
      change: formattedChange,
      isUp: isUp
    }));

    for (let el of elements) {
      el.querySelector('.pulse-price').textContent = formattedPrice;
      const ce = el.querySelector('.pulse-change');
      ce.textContent = formattedChange;
      ce.className = `pulse-change ${isUp ? 'up' : 'down'}`;
    }
  } catch {}
}

function initPulse() {
  loadPulse('SPY',     'ETF',     'pulse-SPY');
  loadPulse('QQQ',     'ETF',     'pulse-QQQ');
  loadPulse('BTC/USD', 'Crypto',  'pulse-BTC');
  loadPulse('EUR/USD', 'Forex',   'pulse-EUR');
  loadPulse('GC1!',    'Futures', 'pulse-GOLD');
}
