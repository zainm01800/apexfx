// ibkr-trades.js — IBKR paper terminal: overall account stats + per-asset-class tabs.
// Data comes from /api/ibkr (Supabase mirror pushed by engine/scripts/run_ibkr_mirror.py).
// Asset class is derived server-side; this file stays dumb.

let _ibkrClassFilter = 'forex'; // 'forex' | 'stocks' | 'crypto'
let _ibkrAccountCache = {};
let _ibkrPositionsCache = [];
let _ibkrTradesCache = [];

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
    const [accountRes, positionsRes, tradesRes] = await Promise.all([
      fetch('/api/ibkr?view=account'),
      fetch('/api/ibkr?view=positions'),
      fetch('/api/ibkr?view=trades&limit=200'),
    ]);

    if (!accountRes.ok || !positionsRes.ok || !tradesRes.ok) {
      throw new Error('Failed to load IBKR terminal data');
    }

    _ibkrAccountCache = await accountRes.json();
    _ibkrPositionsCache = await positionsRes.json();
    _ibkrTradesCache = await tradesRes.json();

    updateScoreboard();
    renderClassTab();
  } catch (e) {
    console.error('Error fetching IBKR data:', e);
    const msg = `<div style="text-align: center; padding: 40px; color: var(--red); font-size: 14px;">Error syncing with IBKR bridge: ${escHtml(e.message || e)}</div>`;
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

  const label = document.getElementById('lastUpdatedLabel');
  if (label) {
    if (a.updated_at) {
      const lastUpdate = new Date(a.updated_at);
      if (!isNaN(lastUpdate.getTime())) {
        const timeStr = lastUpdate.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        label.textContent = 'Last Sync: ' + timeStr;
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

  renderPositionsTable(positions, cls);
  renderTradesTable(trades, cls);
}

function renderPositionsTable(positions, cls) {
  const wrap = document.getElementById('ibkrPositionsWrap');
  if (!wrap) return;

  if (!positions.length) {
    wrap.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">No ${escHtml(CLASS_LABELS[cls] || cls)} positions yet.</div>`;
    return;
  }

  const sym = curSymbol();
  const rows = positions.map(p => {
    const dir = String(p.direction || '').toLowerCase();
    const dirBadge = dir === 'long'
      ? '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">LONG</span>'
      : '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">SHORT</span>';
    const upnl = num(p.unrealized_pnl);
    const upnlTxt = upnl === null ? '—' : fmtSignedMoney(upnl, sym);
    const updated = p.updated_at ? new Date(p.updated_at).toLocaleString() : '—';
    return `<tr class="wl-row">
      <td><span class="wl-sym">${escHtml(p.instrument)}</span></td>
      <td>${dirBadge}</td>
      <td style="font-family: var(--mono);">${escHtml(fmtQty(p.units))}</td>
      <td style="font-family: var(--mono);">${escHtml(fmtPrice(p.avg_price, cls))}</td>
      <td style="font-family: var(--mono);">${escHtml(fmtMoney(p.market_value, sym))}</td>
      <td class="${upnl === null ? '' : (upnl > 0 ? 'pos' : (upnl < 0 ? 'neg' : ''))}" style="font-family: var(--mono); font-weight: 700;">${escHtml(upnlTxt)}</td>
      <td style="color: var(--text3); font-size: 12px;">${escHtml(updated)}</td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `<div class="wl-table-wrap"><table class="wl-table">
    <thead><tr>
      <th>Instrument</th><th>Direction</th><th>Units</th><th>Avg Price</th>
      <th>Market Value</th><th>Unrealized P&amp;L</th><th>Updated</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
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
    const when = t.exec_time ? new Date(t.exec_time).toLocaleString() : '—';
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

document.addEventListener('DOMContentLoaded', () => {
  try { initPulse(); } catch (e) { console.error('Pulse err:', e); }
  try { initIbkrTabs(); } catch (e) { console.error('Tabs err:', e); }
  try { initRefreshButton(); } catch (e) { console.error('Refresh btn err:', e); }

  // Initial load + slow 15-minute background auto-refresh
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
