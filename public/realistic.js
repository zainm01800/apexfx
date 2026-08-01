/* Realistic Results JS — Clean MT4 Execution Model */

(function () {
  'use strict';

  let _positions = [];
  let _trades = [];
  let _roundTrips = [];

  function escHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function fmtPrice(val) {
    if (val == null || isNaN(val)) return '—';
    const num = Number(val);
    if (Math.abs(num) >= 1000) return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (Math.abs(num) >= 1) return num.toFixed(2);
    return num.toFixed(4);
  }

  function computeRoundTrips(trades) {
    const sorted = [...trades].sort((a, b) => new Date(a.exec_time) - new Date(b.exec_time));
    const openLots = {};
    const roundTrips = [];

    for (const t of sorted) {
      const inst = t.instrument;
      const price = parseFloat(t.price);
      const qty = parseFloat(t.qty);
      if (!inst || isNaN(price) || isNaN(qty) || qty <= 0) continue;

      const side = String(t.side).toUpperCase();
      let remaining = (side === 'SELL' ? -1 : 1) * qty;
      const lots = openLots[inst] || (openLots[inst] = []);

      while (remaining !== 0 && lots.length > 0 && Math.sign(lots[0].qty) !== Math.sign(remaining)) {
        const lot = lots[0];
        const closeQty = Math.min(Math.abs(remaining), Math.abs(lot.qty));
        const entryPrice = lot.price;
        const isLong = lot.qty > 0;
        const pnl = (price - entryPrice) * closeQty * (isLong ? 1 : -1);
        const pnlPct = entryPrice ? ((price - entryPrice) / entryPrice * (isLong ? 1 : -1) * 100) : 0;

        roundTrips.push({
          instrument: inst,
          assetClass: t.asset_class || 'stocks',
          direction: isLong ? 'LONG' : 'SHORT',
          qty: closeQty,
          entryPrice: entryPrice,
          exitPrice: price,
          realizedPnl: pnl,
          pnlPct: pnlPct,
          openTime: lot.exec_time,
          closeTime: t.exec_time
        });

        lot.qty -= Math.sign(lot.qty) * closeQty;
        remaining -= Math.sign(remaining) * closeQty;
        if (Math.abs(lot.qty) < 1e-12) lots.shift();
      }
      if (remaining !== 0) {
        lots.push({ qty: remaining, price, exec_time: t.exec_time, side });
      }
    }

    return roundTrips.sort((a, b) => new Date(b.closeTime) - new Date(a.closeTime));
  }

  function loadData() {
    Promise.all([
      fetch('/api/ibkr?view=positions').then(r => r.ok ? r.json() : []).catch(() => []),
      fetch('/api/ibkr?view=trades').then(r => r.ok ? r.json() : []).catch(() => [])
    ]).then(([pos, tr]) => {
      _positions = Array.isArray(pos) ? pos : [];
      _trades = Array.isArray(tr) ? tr : [];
      _roundTrips = computeRoundTrips(_trades);
      renderUI();
    }).catch(err => {
      console.warn('[Realistic Results] Data load error:', err);
      renderUI();
    });
  }

  function renderUI() {
    const wins = _roundTrips.filter(rt => rt.realizedPnl > 0);
    const losses = _roundTrips.filter(rt => rt.realizedPnl < 0);
    const winsSum = wins.reduce((sum, rt) => sum + rt.realizedPnl, 0);
    const lossSum = losses.reduce((sum, rt) => sum + rt.realizedPnl, 0);
    const realizedPnl = winsSum + lossSum;

    const unrealizedPnl = _positions.reduce((sum, p) => sum + (parseFloat(p.unrealized_pnl) || 0), 0);
    const cleanEquity = 1000000 + realizedPnl + unrealizedPnl;
    const cleanReturn = realizedPnl + unrealizedPnl;
    const cleanReturnPct = (cleanReturn / 1000000) * 100;
    const winRate = _roundTrips.length > 0 ? Math.round((wins.length / _roundTrips.length) * 100) : 0;

    // Update headline cards
    const eqEl = document.getElementById('realEquity');
    if (eqEl) {
      eqEl.textContent = '$' + cleanEquity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      eqEl.className = 'hs-val ' + (cleanReturn >= 0 ? 'green' : 'red');
    }
    const eqSub = document.getElementById('realEquitySub');
    if (eqSub) {
      eqSub.textContent = (cleanReturn >= 0 ? '+' : '') + cleanReturnPct.toFixed(2) + '% Net Profit ($' + (cleanReturn >= 0 ? '+' : '-') + Math.abs(cleanReturn).toFixed(2) + ')';
    }

    const rPnlEl = document.getElementById('realizedPnl');
    if (rPnlEl) {
      rPnlEl.textContent = (realizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(realizedPnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      rPnlEl.className = 'hs-val ' + (realizedPnl >= 0 ? 'green' : 'red');
    }
    const rSub = document.getElementById('realizedSub');
    if (rSub) {
      rSub.textContent = `${wins.length} Wins · ${losses.length} Loss (${winRate}% Win Rate)`;
    }

    const uPnlEl = document.getElementById('unrealizedPnl');
    if (uPnlEl) {
      uPnlEl.textContent = (unrealizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(unrealizedPnl).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      uPnlEl.className = 'hs-val ' + (unrealizedPnl >= 0 ? 'green' : 'red');
    }

    // Render Tables
    const container = document.getElementById('realisticTrades');
    if (!container) return;

    const head = `<div class="acc-header"><div class="acc-title">⚙️ MT5 Funded Account Trades <span class="pp-book">MetaTrader 5 Swap-Free · Zero Commission &amp; Interest</span></div></div>`;

    // Open Positions section
    const openRows = _positions.map(p => {
      const dir = String(p.direction || '').toLowerCase() === 'short' ? 'short' : 'long';
      const dirBadge = dir === 'short' ? '<span class="eng-dir short">▼ SHORT</span>' : '<span class="eng-dir long">▲ LONG</span>';
      const mv = parseFloat(p.market_value);
      const units = parseFloat(p.units);
      const entryPx = parseFloat(p.avg_price);
      const curPx = (mv && units) ? Math.abs(mv) / Math.abs(units) : entryPx;
      const upnl = parseFloat(p.unrealized_pnl);
      const pnlCls = !isNaN(upnl) ? (upnl > 0 ? 'pos' : (upnl < 0 ? 'neg' : '')) : '';
      const pnlTxt = !isNaN(upnl) ? (upnl >= 0 ? '+' : '-') + '$' + Math.abs(upnl).toFixed(2) : '—';
      const mt5Ticker = p.instrument + '.US';

      return `<tr class="wl-row eng-row open">
        <td><span class="wl-sym">${escHtml(mt5Ticker)}</span><span class="wl-type">US CFD</span></td>
        <td>${dirBadge}</td>
        <td class="wl-mono">${escHtml(p.units)} lots</td>
        <td class="wl-mono">@ $${fmtPrice(entryPx)}</td>
        <td class="wl-mono">$${fmtPrice(curPx)}</td>
        <td><span class="eng-pnl ${pnlCls}">${escHtml(pnlTxt)}</span></td>
        <td class="eng-days">Live Open</td>
      </tr>`;
    }).join('');

    const openSec = `<div class="eng-sec">Open Positions · ${_positions.length}</div>` + (_positions.length
      ? `<div class="eng-wrap"><table class="wl-table eng-table">
          <thead><tr><th>Instrument (MT5)</th><th>Direction</th><th>Lots / Units</th><th>Entry Price</th><th>Current Price</th><th>Unrealized P&amp;L</th><th>Status</th></tr></thead>
          <tbody>${openRows}</tbody></table></div>`
      : `<div class="acc-empty">No open positions right now.</div>`);

    // Closed Trades section
    const closedRows = _roundTrips.map(rt => {
      const isLong = rt.direction === 'LONG';
      const dirBadge = isLong ? '<span class="eng-dir long">▲ LONG</span>' : '<span class="eng-dir short">▼ SHORT</span>';
      const isWin = rt.realizedPnl > 0;
      const ocCls = isWin ? 'win' : 'loss';
      const ocTxt = isWin ? 'WIN' : 'LOSS';
      const pnlCls = isWin ? 'pos' : 'neg';
      const pnlTxt = (rt.realizedPnl >= 0 ? '+' : '-') + '$' + Math.abs(rt.realizedPnl).toFixed(2) + ` (${rt.pnlPct >= 0 ? '+' : ''}${rt.pnlPct.toFixed(2)}%)`;
      const closeTimeStr = rt.closeTime ? new Date(rt.closeTime).toISOString().slice(0, 16).replace('T', ' ') : '—';
      const mt5Ticker = rt.instrument + '.US';

      return `<tr class="wl-row eng-row ${ocCls}">
        <td><span class="wl-sym">${escHtml(mt5Ticker)}</span><span class="wl-type">US CFD</span></td>
        <td>${dirBadge}</td>
        <td class="wl-mono">@ $${fmtPrice(rt.entryPrice)}</td>
        <td class="wl-mono">@ $${fmtPrice(rt.exitPrice)}</td>
        <td class="wl-mono">${escHtml(closeTimeStr)}</td>
        <td><span class="eng-badge ${ocCls}">${ocTxt}</span></td>
        <td><span class="eng-pnl ${pnlCls}">${escHtml(pnlTxt)}</span></td>
        <td class="eng-days">Closed</td>
      </tr>`;
    }).join('');

    const closedSec = `<div class="eng-sec">Closed Trades &amp; Realized Round-Trips · ${_roundTrips.length}</div>` + (_roundTrips.length
      ? `<div class="eng-wrap"><table class="wl-table eng-table">
          <thead><tr><th>Instrument (MT5)</th><th>Direction</th><th>Entry Price</th><th>Exit Price</th><th>Closed Time</th><th>Outcome</th><th>Realized P&amp;L</th><th>Status</th></tr></thead>
          <tbody>${closedRows}</tbody></table></div>`
      : `<div class="acc-empty">No closed trades recorded yet.</div>`);

    container.innerHTML = head + openSec + closedSec;
  }

  document.addEventListener('DOMContentLoaded', loadData);
})();
