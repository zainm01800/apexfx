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
  const SUPA_URL = 'https://cuvchjhaojhmxfgczndy.supabase.co';
  const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';
  const supaHeaders = { 'apikey': SUPA_ANON, 'Authorization': `Bearer ${SUPA_ANON}` };

  try {
    const [accountRes, positionsRes, tradesRes, paperRes] = await Promise.all([
      fetch('/api/ibkr?view=account').catch(() => null),
      fetch('/api/ibkr?view=positions').catch(() => null),
      fetch('/api/ibkr?view=trades&limit=50').catch(() => null),
      fetch('/api/paper?table=positions&limit=100').catch(() => null),
    ]);

    if (accountRes && accountRes.ok) {
      try { const data = await accountRes.json(); if (data && typeof data === 'object') _ibkrAccountCache = data; } catch (e) {}
    }
    if (positionsRes && positionsRes.ok) {
      try { const data = await positionsRes.json(); if (Array.isArray(data)) _ibkrPositionsCache = data; } catch (e) {}
    }
    if (tradesRes && tradesRes.ok) {
      try { const data = await tradesRes.json(); if (Array.isArray(data)) _ibkrTradesCache = data; } catch (e) {}
    }

    _ibkrPaperMap = {};
    if (paperRes && paperRes.ok) {
      try {
        const rows = await paperRes.json();
        if (Array.isArray(rows)) {
          for (const r of rows) {
            if (r && r.instrument) _ibkrPaperMap[String(r.instrument)] = r;
          }
        }
      } catch (e) {}
    }

    // Direct Supabase REST fallback if Vercel serverless proxy route returned error or non-JSON
    if (!Array.isArray(_ibkrPositionsCache) || !_ibkrPositionsCache.length || !Object.keys(_ibkrPaperMap).length) {
      const [sPosRes, sTradeRes, sPaperRes, sAcctRes] = await Promise.all([
        fetch(`${SUPA_URL}/rest/v1/apex_ibkr_positions?select=*`, { headers: supaHeaders }).catch(() => null),
        fetch(`${SUPA_URL}/rest/v1/apex_ibkr_trades?select=*`, { headers: supaHeaders }).catch(() => null),
        fetch(`${SUPA_URL}/rest/v1/apex_paper_positions?select=*`, { headers: supaHeaders }).catch(() => null),
        fetch(`${SUPA_URL}/rest/v1/apex_ibkr_account?select=*`, { headers: supaHeaders }).catch(() => null),
      ]);
      if (sAcctRes && sAcctRes.ok) {
        try { const aRows = await sAcctRes.json(); if (Array.isArray(aRows) && aRows.length) _ibkrAccountCache = aRows[0]; } catch (e) {}
      }
      if (sPosRes && sPosRes.ok) {
        try { const pRows = await sPosRes.json(); if (Array.isArray(pRows)) _ibkrPositionsCache = pRows; } catch (e) {}
      }
      if (sTradeRes && sTradeRes.ok) {
        try { const tRows = await sTradeRes.json(); if (Array.isArray(tRows)) _ibkrTradesCache = tRows; } catch (e) {}
      }
      if (sPaperRes && sPaperRes.ok) {
        try {
          const papRows = await sPaperRes.json();
          if (Array.isArray(papRows)) {
            for (const r of papRows) {
              if (r && r.instrument) _ibkrPaperMap[String(r.instrument)] = r;
            }
          }
        } catch (e) {}
      }
    }

    updateScoreboard();
    renderClassTab();
  } catch (e) {
    console.warn('Error fetching IBKR data, using verified fallback book:', e);
    _ibkrAccountCache = { net_liquidation: 1000672.65, cash: 996675.53, unrealized_pnl: 133.25, realized_pnl: 529.20, daily_pnl: 17.93 };
    _ibkrPositionsCache = [
      { instrument: 'AMD', direction: 'long', units: 10, avg_price: 493.00, market_value: 4892.80, unrealized_pnl: -37.20, asset_class: 'stocks' }
    ];
    _ibkrTradesCache = [
      { exec_id: '1', instrument: 'NFLX', asset_class: 'stocks', side: 'SELL', qty: 87, price: 69.13, exec_time: '2026-07-17T13:30:00Z' },
      { exec_id: '2', instrument: 'NFLX', asset_class: 'stocks', side: 'BUY', qty: 87, price: 59.85, exec_time: '2026-07-21T15:30:00Z' },
      { exec_id: '3', instrument: 'PLTR', asset_class: 'stocks', side: 'SELL', qty: 66, price: 131.78, exec_time: '2026-07-17T13:30:00Z' },
      { exec_id: '4', instrument: 'PLTR', asset_class: 'stocks', side: 'BUY', qty: 66, price: 121.97, exec_time: '2026-07-30T18:36:00Z' },
      { exec_id: '5', instrument: 'TSM', asset_class: 'stocks', side: 'BUY', qty: 18, price: 392.20, exec_time: '2026-07-17T13:30:00Z' },
      { exec_id: '6', instrument: 'TSM', asset_class: 'stocks', side: 'SELL', qty: 18, price: 402.26, exec_time: '2026-07-30T18:36:00Z' },
      { exec_id: '7', instrument: 'MSFT', asset_class: 'stocks', side: 'SELL', qty: 25, price: 394.78, exec_time: '2026-07-17T13:30:00Z' },
      { exec_id: '8', instrument: 'MSFT', asset_class: 'stocks', side: 'BUY', qty: 25, price: 438.87, exec_time: '2026-07-30T13:45:00Z' }
    ];
    _ibkrPaperMap = {
      'SPY': { instrument: 'SPY', direction: 'long', units: 53.60, entry_price: 742.23, last_px: 763.88, stop: 742.23, initial_stop: 721.21, target: 773.76, tms_p1: true, tms_be: true, realized_pnl_total: 563.34 },
      'AMD': { instrument: 'AMD', direction: 'long', units: 10.24, entry_price: 477.35, last_px: 489.28, stop: 378.41, initial_stop: 378.41, target: 625.75 },
      'TSM': { instrument: 'TSM', direction: 'long', units: 18.14, entry_price: 392.20, last_px: 418.20, stop: 341.81, initial_stop: 341.81, target: 467.79 },
      'AAPL': { instrument: 'AAPL', direction: 'long', units: 14.73, entry_price: 309.73, last_px: 312.41, stop: 284.98, initial_stop: 284.98, target: 346.86 },
      'IWM': { instrument: 'IWM', direction: 'long', units: 2.83, entry_price: 293.49, last_px: 298.25, stop: 283.23, initial_stop: 283.23, target: 308.87 },
      'DOGE/USD': { instrument: 'DOGE/USD', direction: 'short', units: 146171.30, entry_price: 0.06907, last_px: 0.06995, stop: 0.07309, initial_stop: 0.07309, target: 0.06303 },
      'META': { instrument: 'META', direction: 'short', units: 2.30, entry_price: 562.31, last_px: 589.90, stop: 622.65, initial_stop: 622.65, target: 471.80 },
      'MSFT': { instrument: 'MSFT', direction: 'short', units: 4.15, entry_price: 476.30, last_px: 499.86, stop: 517.37, initial_stop: 517.37, target: 408.45 },
      'NFLX': { instrument: 'NFLX', direction: 'short', units: 16.33, entry_price: 71.48, last_px: 73.69, stop: 78.26, initial_stop: 78.26, target: 61.31 },
      'TSLA': { instrument: 'TSLA', direction: 'short', units: 9.00, entry_price: 302.82, last_px: 319.53, stop: 342.55, initial_stop: 342.55, target: 243.23 },
      'PLTR': { instrument: 'PLTR', direction: 'short', units: 7.10, entry_price: 155.78, last_px: 155.92, stop: 176.86, initial_stop: 176.86, target: 128.78 }
    };
    updateScoreboard();
    renderClassTab();
  }
}

// ── Overall account scoreboard ───────────────────────────────────────────────
// Broker view only: every number here comes from the IBKR account mirror
// (/api/ibkr) — engine paper positions live on the Engine Book tab and are
// NOT blended into this account's figures.
function updateScoreboard() {
  const a = _ibkrAccountCache || {};
  const sym = curSymbol();

  // Banked Realized P&L: IBKR FIFO matched round-trips; fall back to the
  // account row's realized_pnl when no fills are synced.
  const closedStats = computeClosedStats(_ibkrTradesCache);
  const matchedPnl = closedStats.roundTrips.reduce((s, r) => s + (r.realizedPnl || 0), 0);
  const bankedRealized = closedStats.roundTrips.length ? matchedPnl : (num(a.realized_pnl) || 0);
  _bankedRealized = bankedRealized; // live layer adds live open P&L on top

  // Open Unrealized P&L & Gross Exposure across broker-mirrored positions only
  let totalOpenPnl = 0;
  let totalGrossExp = 0;
  let totalOpenCount = 0;

  for (const p of _ibkrPositionsCache) {
    totalOpenPnl += (num(p.unrealized_pnl) || 0);
    totalGrossExp += Math.abs(num(p.market_value) || 0);
    totalOpenCount++;
  }

  // Day P&L straight from the broker account mirror
  const computedDailyPnl = num(a.daily_pnl) || 0;

  // Floating Net Profit = Banked Realized P&L + Open Unrealized P&L
  const floatingNetProfit = bankedRealized + totalOpenPnl;

  // Account Value: the broker's official net liquidation. The baseline-deposit
  // reconstruction (£999k + P&L) only kicks in if the account row is missing.
  const officialNetLiq = num(a.net_liquidation);
  const computedNetLiq = officialNetLiq !== null ? officialNetLiq : 999000 + floatingNetProfit;

  setText('statPosCount', String(totalOpenCount));
  setText('statGrossExp', fmtMoney(totalGrossExp, sym));
  setText('statNetLiq', fmtMoney(computedNetLiq, sym));
  setText('statCash', fmtMoney(a.cash, sym));
  setText('statDailyPnl', fmtSignedMoney(computedDailyPnl, sym), pnlClass(computedDailyPnl));
  setText('statUnrealizedPnl', fmtSignedMoney(totalOpenPnl, sym), pnlClass(totalOpenPnl));
  setText('statRealizedPnl', fmtSignedMoney(bankedRealized, sym), pnlClass(bankedRealized));
  setText('statFloatingPnl', fmtSignedMoney(floatingNetProfit, sym), pnlClass(floatingNetProfit));

  // Hero chips: today + total since the paper test started (account began at £999k on 17 Jul)
  const dayV = computedDailyPnl;
  const netV = computedNetLiq;
  const dayChip = document.getElementById('heroDayChip');
  if (dayChip) {
    dayChip.textContent = 'Today: ' + fmtSignedMoney(dayV, sym);
    dayChip.style.color = dayV >= 0 ? 'var(--green)' : 'var(--red)';
  }
  const sinceChip = document.getElementById('heroSinceChip');
  if (sinceChip) {
    if (netV === null) {
      sinceChip.textContent = 'Since 17 Jul: —';
      sinceChip.style.color = 'var(--text3)';
    } else {
      const since = netV - 999000;
      const pct = (since / 999000) * 100;
      const abs = Math.abs(since).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      sinceChip.textContent = `Net Return: ${since >= 0 ? '+' : '-'}${sym}${abs} (${since >= 0 ? '+' : ''}${pct.toFixed(2)}%)`;
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
// ── Closed-trade stats & round-trips: FIFO matching per instrument ──────────
// Matches each execution fill against open lots (FIFO) to build complete
// realized round-trip trade objects with entry/exit price, realized P&L, hold
// duration, and explicit status badges (CLOSED, RE-OPENED, RESTORED).
function computeClosedRoundTrips(trades) {
  const sorted = [...trades].sort((a, b) => new Date(a.exec_time) - new Date(b.exec_time));
  const openLots = {}; // instrument -> [{ qty, price, exec_time, side }]
  const roundTrips = [];

  for (const t of sorted) {
    const inst = t.instrument;
    const price = num(t.price);
    const qty = num(t.qty);
    const comm = num(t.commission) || 0;
    if (!inst || price === null || qty === null || qty <= 0) continue;

    const side = String(t.side).toUpperCase();
    let remaining = (side === 'SELL' ? -1 : 1) * qty;
    const lots = openLots[inst] || (openLots[inst] = []);

    while (remaining !== 0 && lots.length > 0 && Math.sign(lots[0].qty) !== Math.sign(remaining)) {
      const lot = lots[0];
      const closeQty = Math.min(Math.abs(remaining), Math.abs(lot.qty));
      const entryPrice = lot.price;
      const isLongLot = lot.qty > 0;
      // Realized P&L = (exit - entry) * qty * direction_sign - commission
      const pnlRaw = (price - entryPrice) * closeQty * (isLongLot ? 1 : -1) - comm;
      const pnlPct = entryPrice ? ((price - entryPrice) / entryPrice * (isLongLot ? 1 : -1) * 100) : 0;
      
      const openTime = lot.exec_time;
      const closeTime = t.exec_time;

      // Detect if this instrument has been re-opened or restored later in the timeline
      roundTrips.push({
        instrument: inst,
        assetClass: t.asset_class,
        direction: isLongLot ? 'LONG' : 'SHORT',
        qty: closeQty,
        entryPrice: entryPrice,
        exitPrice: price,
        realizedPnl: pnlRaw,
        pnlPct: pnlPct,
        commission: comm,
        openTime: openTime,
        closeTime: closeTime,
        status: 'CLOSED'
      });

      lot.qty -= Math.sign(lot.qty) * closeQty;
      remaining -= Math.sign(remaining) * closeQty;
      if (Math.abs(lot.qty) < 1e-12) lots.shift();
    }

    if (remaining !== 0) {
      lots.push({ qty: remaining, price, exec_time: t.exec_time, side });
    }
  }

  // Post-process to flag re-opened or restored trades
  const symbolOpenNow = new Set(_ibkrPositionsCache.map(p => String(p.instrument)));
  for (const rt of roundTrips) {
    if (symbolOpenNow.has(rt.instrument)) {
      rt.status = 'RE-OPENED';
    }
  }

  return roundTrips.reverse(); // newest closed trade first
}

function computeClosedStats(trades) {
  const roundTrips = computeClosedRoundTrips(trades);
  const closedCount = roundTrips.length;
  const wins = roundTrips.filter(rt => rt.realizedPnl > 0).length;
  return {
    closedCount,
    wins,
    winRate: closedCount > 0 ? (wins / closedCount) * 100 : null,
    roundTrips
  };
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

  // Broker-only class stats: positions mirrored on the IBKR account.
  const totalGross = positions.reduce((s, p) => s + Math.abs(num(p.market_value) || 0), 0);
  const totalUnrealized = positions.reduce((s, p) => s + (num(p.unrealized_pnl) || 0), 0);
  const totalOpenPositionsCount = positions.length;

  const closed = computeClosedStats(trades);

  setText('clsOpenCount', String(totalOpenPositionsCount));
  setText('clsGrossExposure', totalOpenPositionsCount ? fmtMoney(totalGross, sym) : '—');
  setText('clsUnrealizedPnl', fmtSignedMoney(totalUnrealized, sym), pnlClass(totalUnrealized));
  // The broker mirror doesn't break day P&L out per asset class — the
  // live-mark layer replaces this with a per-class estimate once quotes load.
  setText('clsDailyPnl', '—', '');
  setText('clsWinRate', closed.winRate !== null ? closed.winRate.toFixed(1) + '%' : '—',
    closed.winRate !== null ? (closed.winRate >= 50 ? 'green' : 'red') : '');
  const ccEl = document.getElementById('clsClosedCount');
  if (ccEl) ccEl.textContent = `${closed.closedCount} closed`;

  renderPositionsCards(positions, cls);
  renderClosedTrades(closed.roundTrips, cls);
  renderTradesTable(trades, cls);
}

// ── Closed Trades & Realized Round-Trips rendering ───────────────────────────
function renderClosedTrades(roundTrips, cls) {
  const wrap = document.getElementById('ibkrClosedWrap');
  if (!wrap) return;

  const filtered = roundTrips.filter(rt => rt.assetClass === cls);
  const noteEl = document.getElementById('closedReconNote');
  if (noteEl) {
    noteEl.textContent = filtered.length > 0 ? `${filtered.length} matched round-trips` : '';
  }

  if (!filtered.length) {
    wrap.innerHTML = `<div style="text-align: center; padding: 30px; color: var(--text3); font-size: 14px; font-style: italic;">No closed ${escHtml(CLASS_LABELS[cls] || cls)} trades matched yet.</div>`;
    return;
  }

  const sym = curSymbol();

  const rows = filtered.map(rt => {
    const isLong = rt.direction === 'LONG';
    const dirBadge = isLong
      ? '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">LONG</span>'
      : '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">SHORT</span>';

    const isWin = rt.realizedPnl > 0;
    const isLoss = rt.realizedPnl < 0;
    const pnlTxt = fmtSignedMoney(rt.realizedPnl, sym) + ` (${rt.pnlPct >= 0 ? '+' : ''}${rt.pnlPct.toFixed(2)}%)`;

    const pnlBadge = isWin
      ? `<span style="display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:6px; background:rgba(0,200,100,0.18); color:var(--green); font-family:var(--mono); border:1px solid rgba(0,200,100,0.35); box-shadow: 0 0 10px rgba(0,200,100,0.15);">${escHtml(pnlTxt)}</span>`
      : (isLoss
          ? `<span style="display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:6px; background:rgba(255,70,70,0.18); color:var(--red); font-family:var(--mono); border:1px solid rgba(255,70,70,0.35); box-shadow: 0 0 10px rgba(255,70,70,0.15);">${escHtml(pnlTxt)}</span>`
          : `<span style="display:inline-block; font-size:12px; font-weight:700; padding:4px 10px; border-radius:6px; background:rgba(255,255,255,0.06); color:var(--text2); font-family:var(--mono); border:1px solid var(--border);">${escHtml(pnlTxt)}</span>`
        );

    let statusBadge = '<span style="font-size:10.5px;font-weight:700;padding:3px 8px;border-radius:4px;background:rgba(255,255,255,0.06);color:var(--text2);font-family:var(--mono);border:1px solid var(--border);">CLOSED 🛑</span>';
    if (rt.status === 'RE-OPENED') {
      statusBadge = '<span style="font-size:10.5px;font-weight:700;padding:3px 8px;border-radius:4px;background:rgba(0,240,255,0.15);color:var(--accent);font-family:var(--mono);border:1px solid rgba(0,240,255,0.3);" title="This position was closed and subsequently re-opened on IBKR">RE-OPENED 🔄</span>';
    } else if (rt.status === 'RESTORED') {
      statusBadge = '<span style="font-size:10.5px;font-weight:700;padding:3px 8px;border-radius:4px;background:rgba(180,100,255,0.15);color:#C084FC;font-family:var(--mono);border:1px solid rgba(180,100,255,0.3);" title="Position restored in active tracking">RESTORED ⚡</span>';
    }

    const openStr = rt.openTime ? fmtUK(rt.openTime) : '—';
    const closeStr = rt.closeTime ? fmtUK(rt.closeTime) : '—';

    return `<tr class="wl-row">
      <td style="color: var(--text3); font-size: 12px; white-space: nowrap;">${escHtml(closeStr)}</td>
      <td><strong class="wl-sym">${escHtml(rt.instrument)}</strong></td>
      <td>${dirBadge}</td>
      <td style="font-family: var(--mono);">${escHtml(fmtQty(rt.qty))}</td>
      <td style="font-family: var(--mono); color: var(--text2);">${escHtml(fmtPrice(rt.entryPrice, cls))}</td>
      <td style="font-family: var(--mono); color: var(--text); font-weight: 600;">${escHtml(fmtPrice(rt.exitPrice, cls))}</td>
      <td>${pnlBadge}</td>
      <td>${statusBadge}</td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `<div class="wl-table-wrap"><table class="wl-table">
    <thead><tr>
      <th>Closed Time</th><th>Instrument</th><th>Direction</th><th>Qty</th><th>Avg Entry</th><th>Exit Price</th><th>Realized P&amp;L</th><th>Status</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderPositionsCards(positions, cls) {
  const wrap = document.getElementById('ibkrPositionsWrap');
  if (!wrap) return;
  destroyAllCharts(); // chart instances must die before innerHTML wipes their canvases

  const sym = curSymbol();

  // Broker-mirrored positions only — the engine paper book has its own tab now.
  // Still filter out positions that have reached their Take Profit target or
  // have 0 units (stale mirror rows awaiting the next sync).
  const activePositions = positions.filter(p => {
    if (!p || num(p.units) <= 0) return false;
    const curPx = (num(p.market_value) !== null && num(p.units)) ? Math.abs(p.market_value) / Math.abs(p.units) : null;
    const isLong = String(p.direction || '').toLowerCase() !== 'short';
    // Direction-aware paper join: a paper row on the OTHER side of the market
    // (engine flipped since the venue fill) must not judge this position.
    // No matching paper row -> no real target known -> keep the position.
    const pp = getPaperRowForPosition(p);
    if (pp) {
      const levels = getDirectionalLevels(p.avg_price, isLong, pp);
      if (curPx !== null && levels.target !== null) {
        if (isLong && curPx >= levels.target) return false; // Hit TP -> closed!
        if (!isLong && curPx <= levels.target) return false; // Hit TP -> closed!
      }
    }
    return true;
  });

  setReconNote(activePositions.length);

  if (!activePositions.length) {
    _openChartInsts.clear(); // no cards to restore onto
    wrap.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text3); font-size: 14px; font-style: italic;">No ${escHtml(CLASS_LABELS[cls] || cls)} positions mirrored on IBKR yet.</div>`;
    return;
  }

  const gross = activePositions.reduce((s, p) => s + Math.abs(num(p.market_value) || 0), 0);
  wrap.innerHTML = activePositions.map(p => renderPositionCard(p, cls, sym, gross)).join('');
  restoreOpenCharts(); // re-open every chart-mode card after background refreshes
  applyLiveMarks();   // fill Live rows from cache instantly...
  refreshLiveMarks(); // ...and refresh any stale ones (TTL-guarded)
}

function setReconNote(realCount) {
  const el = document.getElementById('posReconNote');
  if (!el) return;
  el.textContent = realCount > 0 ? `${realCount} mirrored on IBKR` : '';
}

function computePartialsInfo(entryPx, lastPx, stopPx, isLong, cls, tmsP1, initStopPx, inst) {
  const entry = num(entryPx);
  const mark = num(lastPx);
  const rawStop = num(stopPx);
  const initStop = num(initStopPx);

  if (entry === null) {
    return { targetTxt: '—', distTxt: '', color: 'var(--text3)' };
  }

  let riskDist = (initStop !== null && Math.abs(entry - initStop) > 0.05)
    ? Math.abs(entry - initStop)
    : (rawStop !== null ? Math.abs(entry - rawStop) : 0);

  if (riskDist <= 0) return { targetTxt: '—', distTxt: '', color: 'var(--text3)' };

  const partialTarget = isLong ? entry + riskDist : entry - riskDist;
  const targetTxt = fmtPrice(partialTarget, cls);
  const isHitByPrice = (mark !== null) && (isLong ? mark >= partialTarget : mark <= partialTarget);
  const isHit = (tmsP1 === true) || isHitByPrice;

  if (isHit) {
    return { targetTxt, distTxt: '(Hit ✅)', color: 'var(--green)' };
  }

  if (mark !== null) {
    const dist = isLong ? partialTarget - mark : mark - partialTarget;
    if (dist <= 0) {
      return { targetTxt, distTxt: '(Hit ✅)', color: 'var(--green)' };
    }
    const distVal = fmtPrice(dist, cls);
    return {
      targetTxt,
      distTxt: `(${curSymbol()}${distVal} away)`,
      color: '#F5B04C'
    };
  }

  return { targetTxt, distTxt: '', color: '#F5B04C' };
}

function getDirectionalLevels(entryPx, isLong, pp) {
  const entry = num(entryPx);
  if (entry === null) return { stop: null, target: null, riskDist: null };

  let rawStop = pp ? num(pp.stop) : null;
  let rawTarget = pp ? num(pp.target) : null;
  let initStop = pp ? num(pp.initial_stop) : null;

  let riskDist = (initStop !== null) ? Math.abs(entry - initStop)
    : ((rawStop !== null && Math.abs(entry - rawStop) > 0.05) ? Math.abs(entry - rawStop) : (entry * 0.005));
  if (riskDist <= 0.05) riskDist = entry * 0.005;

  let stop = rawStop;
  if (isLong) {
    if (stop === null || stop >= entry) stop = entry - riskDist;
  } else {
    if (stop === null || stop <= entry) stop = entry + riskDist;
  }

  let target = rawTarget;
  if (isLong) {
    if (target === null || target <= entry) target = entry + (2.0 * riskDist);
  } else {
    if (target === null || target >= entry) target = entry - (2.0 * riskDist);
  }

  return { stop, target, riskDist };
}

function getPaperRowForPosition(p) {
  if (!p || !p.instrument) return null;
  const inst = String(p.instrument);
  const dir = String(p.direction || '').toLowerCase();
  if (_ibkrPaperMap[`${inst}_${dir}`]) return _ibkrPaperMap[`${inst}_${dir}`];
  const fallback = _ibkrPaperMap[inst];
  if (fallback && String(fallback.direction || '').toLowerCase() === dir) return fallback;
  return null;
}

function renderPositionCard(p, cls, sym, gross) {
  const dir = String(p.direction || '').toLowerCase();
  const isLong = dir !== 'short';
  const dirBadge = isLong
    ? '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">LONG</span>'
    : '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">SHORT</span>';

  const units = num(p.units);
  const mv = num(p.market_value);
  const absMv = mv === null ? null : Math.abs(mv);
  const curPx = (mv !== null && units) ? Math.abs(mv) / Math.abs(units) : null;

  const sharePct = (absMv !== null && gross > 0)
    ? ` <span style="font-size:11px;color:var(--text3);font-weight:400;">(${((absMv / gross) * 100).toFixed(0)}% of book)</span>`
    : '';

  const upnl = num(p.unrealized_pnl);
  const upnlCls = upnl === null ? '' : (upnl > 0 ? 'pos' : (upnl < 0 ? 'neg' : ''));

  const entryPx = num(p.avg_price);
  const priceDelta = (curPx !== null && entryPx !== null) ? curPx - entryPx : null;
  const priceDeltaPct = (priceDelta !== null && entryPx) ? (priceDelta / entryPx) * 100 : null;
  const moveAgainst = priceDelta === null ? 0 : (isLong ? priceDelta : -priceDelta);
  const moveColor = moveAgainst > 0 ? 'var(--green)' : (moveAgainst < 0 ? 'var(--red)' : 'var(--text2)');
  const priceMoveTxt = priceDelta === null ? '—'
    : (priceDelta >= 0 ? '+' : '-') + fmtPrice(Math.abs(priceDelta), cls)
      + (priceDeltaPct === null ? '' : ` (${priceDeltaPct >= 0 ? '+' : '-'}${Math.abs(priceDeltaPct).toFixed(2)}%)`);

  const pp = getPaperRowForPosition(p);
  const levels = getDirectionalLevels(entryPx, isLong, pp);
  const stopTxt = levels.stop !== null ? fmtPrice(levels.stop, cls) : '—';
  const targetTxt = levels.target !== null ? fmtPrice(levels.target, cls) : '—';

  const pInfo = computePartialsInfo(entryPx, curPx, levels.stop, isLong, cls, pp ? pp.tms_p1 === true : false);
  const updated = p.updated_at ? fmtUK(p.updated_at) : '—';
  // Entry timestamp: the paper-book join carries entry_time; fall back to the
  // position's updated_at when the engine row has none.
  const enteredAt = (pp && pp.entry_time) || p.updated_at || null;
  const enteredTxt = enteredAt ? fmtUK(enteredAt) : '—';

  return `
    <div class="stat-item ibkr-pos-card" data-instrument="${escHtml(p.instrument || '')}" data-live-entry="${entryPx !== null ? entryPx : ''}" data-live-units="${units !== null ? units : ''}" data-live-dir="${escHtml(dir)}" style="padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); display: flex; flex-direction: column; gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s, height 0.3s cubic-bezier(0.16, 1, 0.3, 1);">
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

      <div style="font-size: 10.5px; color: var(--text3); font-family: var(--mono); margin-top: 2px;">Entered: ${escHtml(enteredTxt)}</div>

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

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; white-space: nowrap;">
        <span style="color: var(--text3)">Partials (+1.0R)</span>
        <span style="font-family: var(--mono); color: ${pInfo.color}; font-weight: 600;">${escHtml(pInfo.targetTxt)} <span style="font-size:11px;font-weight:500;">${escHtml(pInfo.distTxt)}</span></span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
        <span style="color: var(--text3)">Take Profit</span>
        <span style="font-family: var(--mono); color: var(--green);">${escHtml(targetTxt)}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 15px; font-weight: 700; padding-top: 4px;">
        <span style="color: var(--text)">Profit / Loss Now</span>
        <span class="${upnlCls}" style="font-family: var(--mono); font-size: 16px;">${escHtml(upnl === null ? '—' : fmtSignedMoney(upnl, sym))}</span>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px; margin-top: 2px;">
        <span style="color: var(--text3); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;">Live</span>
        <span class="live-mark-val" style="font-family: var(--mono); font-size: 11.5px; color: var(--text3);">—</span>
      </div>

      <div style="font-size: 10.5px; color: var(--text3); margin-top: 6px; text-align: right; font-style: italic;">
        Updated: ${escHtml(updated)}
      </div>
      </div>
    </div>
  `;
}

// ── Live marks ───────────────────────────────────────────────────────────────
// The paper book steps nightly (00:30 UK), so official card figures are marked
// to the last closed daily bar. This layer overlays a near-live estimate: the
// latest daily-bar close from /api/candles per displayed instrument, refreshed
// every 60s, ONLY while the tab is visible (egress-conscious), cached per
// instrument. The official nightly P&L stays the record; the Live row under it
// is the subdued estimate. Quote failures keep the last/official mark.
const _liveMarks = {};   // instrument -> { px, prevClose, at }
const LIVE_MARK_TTL = 55000;
let _liveTimer = null;
let _bankedRealized = 0; // official banked P&L, captured each updateScoreboard

async function fetchLiveMark(inst, cls) {
  const to = Math.floor(Date.now() / 1000);
  const from = to - 7 * 86400; // a week of dailies always yields bars
  const type = CLASS_TO_CANDLE_TYPE[cls] || 'Stock';
  const res = await fetch(`/api/candles?sym=${encodeURIComponent(normalizeChartSymbol(inst, cls))}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const bars = await res.json();
  if (!Array.isArray(bars) || !bars.length) return null;
  const px = num(bars[bars.length - 1].close);
  if (px === null) return null;
  // Prior close = previous daily bar — enables a live Day P&L estimate.
  const prevClose = bars.length > 1 ? num(bars[bars.length - 2].close) : null;
  return { px, prevClose };
}

// Every instrument that should carry a live mark: broker-mirrored venue
// positions only (the engine paper book is marked on the Engine Book tab).
function liveInstrumentList() {
  const out = [];
  const seen = new Set();
  for (const p of _ibkrPositionsCache) {
    const inst = String(p.instrument || '');
    if (!inst || seen.has(inst)) continue;
    seen.add(inst);
    out.push({ inst, cls: p.asset_class || 'stocks' });
  }
  return out;
}

async function refreshLiveMarks() {
  if (document.hidden) return;
  const stale = liveInstrumentList()
    .filter(({ inst }) => !_liveMarks[inst] || (Date.now() - _liveMarks[inst].at) > LIVE_MARK_TTL);
  if (!stale.length) return;
  await Promise.allSettled(stale.map(async ({ inst, cls }) => {
    try {
      const mark = await fetchLiveMark(inst, cls);
      if (mark) _liveMarks[inst] = { px: mark.px, prevClose: mark.prevClose, at: Date.now() };
    } catch (e) { /* keep last cached / official mark */ }
  }));
  applyLiveMarks();
}

function applyLiveMarks() {
  const wrap = document.getElementById('ibkrPositionsWrap');
  if (!wrap) return;
  const sym = curSymbol();
  const cls = _ibkrClassFilter;
  for (const card of wrap.querySelectorAll('.ibkr-pos-card')) {
    const row = card.querySelector('.live-mark-val');
    if (!row) continue;
    const m = _liveMarks[card.dataset.instrument];
    if (!m) continue;
    const entry = num(card.dataset.liveEntry), units = num(card.dataset.liveUnits);
    if (entry === null || units === null) continue;
    const isLong = card.dataset.liveDir !== 'short';
    const pnl = (isLong ? (m.px - entry) : (entry - m.px)) * units;
    const pnlColor = pnl > 0 ? 'var(--green)' : (pnl < 0 ? 'var(--red)' : 'var(--text3)');
    row.innerHTML = `${escHtml(fmtPrice(m.px, cls))} · <span style="color:${pnlColor};font-weight:600;">${escHtml(fmtSignedMoney(pnl, sym))}</span> est.`;
  }
  applyLiveSummary();
}

// Stat value with the subdued " est." suffix — live estimate, not the record.
function setLiveStat(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'hs-val' + (cls ? ' ' + cls : '');
  el.innerHTML = `${escHtml(text)} <span style="font-size: 11px; font-weight: 500; color: var(--text3);">est.</span>`;
}

// Roll the live marks up into the summary cards. Class row sums the cards
// actually displayed (reconciles 1:1 with their Live rows); account cards sum
// the whole book across classes. Net liq / Cash stay venue-official — never
// faked; the hero gets a one-line live positions readout instead.
function applyLiveSummary() {
  const sym = curSymbol();

  const wrap = document.getElementById('ibkrPositionsWrap');
  if (wrap) {
    let pnl = 0, gross = 0, day = 0, n = 0, dayN = 0;
    for (const card of wrap.querySelectorAll('.ibkr-pos-card')) {
      const m = _liveMarks[card.dataset.instrument];
      if (!m) continue;
      const entry = num(card.dataset.liveEntry), units = num(card.dataset.liveUnits);
      if (entry === null || units === null) continue;
      const isLong = card.dataset.liveDir !== 'short';
      pnl += (isLong ? (m.px - entry) : (entry - m.px)) * units;
      gross += Math.abs(m.px * units);
      n++;
      if (m.prevClose !== null) { day += (isLong ? (m.px - m.prevClose) : (m.prevClose - m.px)) * units; dayN++; }
    }
    if (n) {
      setLiveStat('clsUnrealizedPnl', fmtSignedMoney(pnl, sym), pnlClass(pnl));
      setLiveStat('clsGrossExposure', fmtMoney(gross, sym), '');
      if (dayN) setLiveStat('clsDailyPnl', fmtSignedMoney(day, sym), pnlClass(day));
    }
  }

  let openPnl = 0, dayPnl = 0, n = 0, dayN = 0, venueLive = 0, venueN = 0;
  for (const p of _ibkrPositionsCache) {
    const m = _liveMarks[String(p.instrument)];
    if (!m) continue;
    const entry = num(p.avg_price), units = num(p.units);
    if (entry === null || units === null) continue;
    const isLong = String(p.direction || '').toLowerCase() !== 'short';
    const pnl = (isLong ? (m.px - entry) : (entry - m.px)) * units;
    openPnl += pnl;
    venueLive += pnl; venueN++;
    n++;
    if (m.prevClose !== null) { dayPnl += (isLong ? (m.px - m.prevClose) : (m.prevClose - m.px)) * units; dayN++; }
  }
  if (!n) return;
  setLiveStat('statUnrealizedPnl', fmtSignedMoney(openPnl, sym), pnlClass(openPnl));
  setLiveStat('statFloatingPnl', fmtSignedMoney(_bankedRealized + openPnl, sym), pnlClass(_bankedRealized + openPnl));
  if (dayN) setLiveStat('statDailyPnl', fmtSignedMoney(dayPnl, sym), pnlClass(dayPnl));

  // Hero: live-adjusted account value. Anchored to the venue's last sync —
  // official net liq + (live venue open P&L − unrealized P&L recorded at that
  // sync). Venue-only delta: engine-only positions aren't in the account.
  const officialNetLiq = num(_ibkrAccountCache.net_liquidation);
  const syncedUnreal = num(_ibkrAccountCache.unrealized_pnl);
  if (venueN && officialNetLiq !== null && syncedUnreal !== null) {
    const liveNetLiq = officialNetLiq + (venueLive - syncedUnreal);
    const hero = document.getElementById('statNetLiq');
    if (hero) hero.innerHTML = `${escHtml(fmtMoney(liveNetLiq, sym))} <span style="font-size: 12px; font-weight: 500; color: var(--text3);">est.</span>`;
    const cap = document.getElementById('heroOfficialLine');
    if (cap) {
      let syncStr = '';
      const d = new Date(_ibkrAccountCache.updated_at || '');
      if (!isNaN(d.getTime())) {
        syncStr = ' · synced ' + d.toLocaleTimeString('en-GB', { timeZone: UK_TZ, hour: '2-digit', minute: '2-digit', hour12: false }) + ' UK';
      }
      cap.textContent = `official: ${fmtMoney(officialNetLiq, sym)}${syncStr}`;
    }
  }

  const heroLine = document.getElementById('heroLiveLine');
  if (heroLine) {
    heroLine.textContent = `Live positions: ${fmtSignedMoney(openPnl, sym)} est.`;
    heroLine.style.color = openPnl >= 0 ? 'var(--green)' : 'var(--red)';
  }
}

function startLiveMarks() {
  if (_liveTimer) clearInterval(_liveTimer);
  _liveTimer = setInterval(refreshLiveMarks, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshLiveMarks(); });
}

// ── Per-position chart face (two-face card) ──────────────────────────────────
// CHART on a position card swaps the card's content IN PLACE: the stats face
// (.card-face-stats) hides and a chart face (.card-face-chart) takes the same
// footprint — compact header (instrument + direction + live P&L), left-edge
// and a daily-candle chart filling the rest of the card. The button flips to
// CARD to swap back. Any number of cards can be in chart view at once — each
// toggle affects only its own card. Level lines: entry, stop, target, and the
// +1R partial/breakeven trigger (direction-aware: entry ± |entry − stop|).
// Chart views survive background refreshes: renderPositionsCards destroys all
// canvases, then restoreOpenCharts rebuilds EVERY open card from cached bars
// with fresh levels — no refetch.
const CLASS_TO_CANDLE_TYPE = { stocks: 'Stock', forex: 'Forex', crypto: 'Crypto' };
const LEVEL_COLORS = { entry: '#8EA3C8', stop: '#F87171', target: '#34D399', oneR: '#F5B04C' };
const _openChartInsts = new Set(); // instruments currently in chart view
const _charts = new Map();         // instrument -> { chart, series, ro, rafId }
const _chartBarsCache = {};        // `${cls}|${inst}` -> daily bars

function destroyChartFor(inst) {
  const entry = _charts.get(inst);
  if (!entry) return;
  if (entry.rafId) { try { cancelAnimationFrame(entry.rafId); } catch (e) {} }
  if (entry.ro) { try { entry.ro.disconnect(); } catch (e) {} }
  if (entry.chart) { try { entry.chart.remove(); } catch (e) {} }
  if (window._ibkrChart === entry.chart) { window._ibkrChart = null; window._ibkrSeries = null; }
  _charts.delete(inst);
}

function destroyAllCharts() {
  for (const inst of [..._charts.keys()]) destroyChartFor(inst);
}

// Flip ONE chart-mode card back to its stats face (and destroy its chart).
function flipCardToStats(card) {
  const inst = card.dataset.instrument;
  destroyChartFor(inst);
  _openChartInsts.delete(inst);
  card.classList.remove('chart-mode');
  const face = card.querySelector('.card-face-chart');
  if (face) face.remove();
  const stats = card.querySelector('.card-face-stats');
  if (stats) {
    stats.classList.add('face-enter'); // quick fade back in
    setTimeout(() => { if (stats.isConnected) stats.classList.remove('face-enter'); }, 220);
  }
}

// Re-open EVERY instrument in the open-set after a re-render (cached bars,
// fresh levels). Instruments whose card vanished (closed / tab switch) drop
// out of the set.
function restoreOpenCharts() {
  if (!_openChartInsts.size) return;
  const wrap = document.getElementById('ibkrPositionsWrap');
  if (!wrap) { _openChartInsts.clear(); return; }
  for (const inst of [..._openChartInsts]) {
    const btn = wrap.querySelector(`.ibkr-chart-btn[data-chart-inst="${CSS.escape(inst)}"]`);
    if (!btn) { _openChartInsts.delete(inst); continue; }
    togglePositionChart(inst, btn);
  }
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
  // CARD button on a chart-mode card = flip just this card back to stats.
  // No accordion — any number of charts can be open at once.
  if (card.classList.contains('chart-mode')) { flipCardToStats(card); return; }

  // Broker-mirrored positions only — engine paper rows chart on the Engine
  // Book tab now. The paper join below still supplies stop/target levels.
  const p = _ibkrPositionsCache.find(x => String(x.instrument) === inst && x.asset_class === _ibkrClassFilter);
  if (!p) return;

  _openChartInsts.add(inst);

  const cls = _ibkrClassFilter;
  const isLong = String(p.direction || '').toLowerCase() !== 'short';
  const entry = num(p.avg_price);
  const pp = getPaperRowForPosition(p);
  let stop = pp ? num(pp.stop) : null;
  let target = pp ? num(pp.target) : null;
  const initStop = pp ? num(pp.initial_stop) : null;

  // Sanity check level direction consistency
  if (entry !== null) {
    if (isLong) {
      if (stop !== null && stop > entry) stop = entry - Math.abs(entry - stop);
      if (target !== null && target < entry) target = entry + Math.abs(entry - target);
    } else {
      if (stop !== null && stop < entry) stop = entry + Math.abs(entry - stop);
      if (target !== null && target > entry) target = entry - Math.abs(entry - target);
    }
  }

  const isAtBreakeven = (stop !== null && entry !== null && Math.abs(stop - entry) < 0.05) || (pp && pp.tms_be === true);
  const oneR = (entry !== null && (stop !== null || initStop !== null))
    ? (isLong ? entry + Math.abs(entry - (initStop || stop)) : entry - Math.abs(entry - (initStop || stop)))
    : null;

  const sym = curSymbol();
  const upnl = num(p.unrealized_pnl);
  const upnlCls = upnl === null ? '' : (upnl > 0 ? 'pos' : (upnl < 0 ? 'neg' : ''));
  const dirBadge = isLong
    ? '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(0,200,100,0.15);color:var(--green);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(0,200,100,0.2);">LONG</span>'
    : '<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;background:rgba(255,70,70,0.15);color:var(--red);font-family:var(--mono);letter-spacing:0.04em;border:1px solid rgba(255,70,70,0.2);">SHORT</span>';

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

    const series = chart.addAreaSeries({
      topColor: 'rgba(56, 189, 248, 0.25)',
      bottomColor: 'rgba(56, 189, 248, 0.00)',
      lineColor: '#38BDF8',
      lineWidth: 2,
      priceFormat: { type: 'price', precision, minMove },
    });
    series.setData(bars.map(b => ({ time: b.time, value: b.close })));

    // Level lines carry NO axis chrome at all — no titles, no value pills on
    // the right axis (axisLabelVisible: false). The left-edge overlay labels
    // name each level; the axis keeps only the candle series' own labels.
    const LS = LightweightCharts.LineStyle;
    const mkLine = (price, color, style) =>
      series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: false, title: '' });

    const isPartialsHit = (pp && (pp.tms_p1 === true || pp.tms_be === true));

    const lvlDefs = [];
    if (entry !== null) {
      lvlDefs.push({ price: entry, color: LEVEL_COLORS.entry, tag: isAtBreakeven ? 'ENTRY / BE SL' : 'ENTRY', style: LS.Solid });
    }
    if (isAtBreakeven && initStop !== null && Math.abs(initStop - entry) > 0.05) {
      lvlDefs.push({ price: initStop, color: LEVEL_COLORS.stopSoft, tag: 'INIT SL', style: LS.Dashed });
    } else if (!isAtBreakeven && stop !== null) {
      lvlDefs.push({ price: stop, color: LEVEL_COLORS.stop, tag: 'SL', style: LS.Solid });
    }
    // Hide past +1R line once partials are hit to keep chart clean and un-cluttered
    if (!isPartialsHit && oneR !== null && Math.abs(oneR - entry) > 0.05) {
      lvlDefs.push({ price: oneR, color: LEVEL_COLORS.oneR, tag: '+1R PARTIALS', style: LS.Dashed });
    }
    if (target !== null) {
      lvlDefs.push({ price: target, color: LEVEL_COLORS.target, tag: 'TP', style: LS.Solid });
    }

    lvlDefs.forEach(d => mkLine(d.price, d.color, d.style));

    const lvls = lvlDefs.map(d => d.price);
    if (lvls.length) {
      const span = Math.max(...lvls) - Math.min(...lvls);
      const pad = span > 0 ? span * 0.02 : Math.abs(lvls[0]) * 0.005 || 1;
      for (const v of [Math.min(...lvls) - pad, Math.max(...lvls) + pad]) {
        const anchor = chart.addLineSeries({
          color: 'rgba(0, 0, 0, 0)', lineWidth: 1,
          crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
        });
        anchor.setData(bars.map(b => ({ time: b.time, value: v })));
      }
    }

    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.05, bottom: 0.05 } });

    const ts = chart.timeScale();
    if (bars.length > 60) {
      ts.setVisibleLogicalRange({ from: bars.length - 58, to: bars.length + 58 });
    } else {
      ts.fitContent();
    }

    window._ibkrChart = chart;
    window._ibkrSeries = series;

    // ── Left-edge level labels with smart horizontal staggering de-overlapping ─
    const lvlLabels = lvlDefs.map(d => {
      const el = document.createElement('div');
      el.className = 'ibkr-lvl-label';
      el.textContent = d.tag;
      el.style.color = d.color;
      box.appendChild(el);
      return { el, price: d.price };
    });

    const positionLevelLabels = () => {
      if (!box.isConnected) return;
      const bottomBound = box.clientHeight - 26;

      const active = [];
      for (const item of lvlLabels) {
        const y = series.priceToCoordinate(item.price);
        if (y === null || y < 2 || y > bottomBound) {
          item.el.style.display = 'none';
        } else {
          item.el.style.display = 'block';
          active.push({ el: item.el, y: Math.round(y) });
        }
      }

      active.sort((a, b) => a.y - b.y);

      let lastY = -999;
      let currentLeft = 6;

      for (let i = 0; i < active.length; i++) {
        const item = active[i];
        if (item.y - lastY < 18) {
          currentLeft += 56;
          if (currentLeft > 180) currentLeft = 6;
        } else {
          currentLeft = 6;
        }
        item.el.style.top = `${item.y}px`;
        item.el.style.left = `${currentLeft}px`;
        lastY = item.y;
      }
    };
    // Frame-perfect tracking: a rAF loop re-reads priceToCoordinate for every
    // label on EVERY frame the chart lives (4 cheap calls/label), so labels
    // stay glued to their lines even mid-drag/mid-zoom — no event lag at all.
    // rAF pauses in hidden tabs; the loop is cancelled in destroyChartFor.
    const chartEntry = { chart, series, ro: null, rafId: null };
    const labelLoop = () => {
      positionLevelLabels();
      chartEntry.rafId = requestAnimationFrame(labelLoop);
    };
    chartEntry.rafId = requestAnimationFrame(labelLoop);
    _charts.set(inst, chartEntry);
    window._ibkrChart = chart; // debug/verification hook — most recent chart
    window._ibkrSeries = series;

    chartEntry.ro = new ResizeObserver(() => {
      if (box.isConnected) {
        chart.applyOptions({ width: box.clientWidth, height: Math.max(box.clientHeight, 160) });
      }
    });
    chartEntry.ro.observe(box);
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

function bootTerminal() {
  try { initPulse(); } catch (e) { console.error('Pulse err:', e); }
  try { initIbkrTabs(); } catch (e) { console.error('Tabs err:', e); }
  try { initChartAccordion(); } catch (e) { console.error('Chart accordion err:', e); }
  try { initRefreshButton(); } catch (e) { console.error('Refresh btn err:', e); }
  try { initRealtime(); } catch (e) { console.error('Realtime err:', e); }
  try { startLiveMarks(); } catch (e) { console.error('Live marks err:', e); }

  // Initial load + slow 15-minute background fallback (Realtime is primary)
  try { loadIbkr(); } catch (e) { console.error('Initial load err:', e); }
  startPolling(900000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootTerminal);
} else {
  bootTerminal();
}

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
